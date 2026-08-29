#!/usr/bin/env python3
"""Path B: bootstrap a gameserver Galaxy/Region from bang.* service-mode rows.

Canon: OPERATIONS/bang-integration.md § Path B. Reconstructs Universe JSON
from the bang schema, then hands it to the existing translator + validation
gate + apply. Not a second importer.

Fresh install MUST import ``terran_space`` first (new galaxy), then
``central_nexus`` (existing galaxy). Translator ``REGION_ORDER`` puts
Terran first so Sol / Earth Station land at global ``sector_id`` 1
(player spawn). Docs :264 say typically Nexus then Terran — that order
would occupy global 1 with Nexus on a new-galaxy Path B; this CLI
fail-closes instead of following that sentence.

    poetry run python scripts/bootstrap_from_bang.py \\
        --bang-url "$BANG_DATABASE_URL" --universe-name terran \\
        --galaxy-name "SectorWars Galaxy" --region-type terran_space

    poetry run python scripts/bootstrap_from_bang.py \\
        --bang-url "$BANG_DATABASE_URL" --universe-name nexus \\
        --galaxy-name "SectorWars Galaxy" --region-type central_nexus
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
import uuid
from typing import Any, Dict, Optional
from unittest.mock import MagicMock

from sqlalchemy import create_engine, select, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm.attributes import flag_modified

# scripts/ → gameserver root for `import src...`
_GAMESERVER_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _GAMESERVER_ROOT not in sys.path:
    sys.path.insert(0, _GAMESERVER_ROOT)

from src.models.galaxy import Galaxy  # noqa: E402
from src.models.region import Region  # noqa: E402
from src.schemas.bang_config import RegionType  # noqa: E402
from src.services.bang_import_service import (  # noqa: E402
    BangImportService,
    ParsedUniverse,
    RegionAttachment,
)
from src.services.bang_schema import (  # noqa: E402
    config_fingerprint,
    mappings_from_result,
    region_already_imported,
    stamp_region_snapshot,
    universe_from_bang_rows,
)
from src.services.galaxy_validation import (  # noqa: E402
    validate_insert_plan_or_raise,
    validate_region_plan_or_raise,
)

REGION_SECTORS = {
    "central_nexus": 5000,
    "terran_space": 300,
    "player_owned": None,
}

NEW_GALAXY_FIRST_REGION = "terran_space"
SPOKE_REGION_TYPES = ("terran_space", "player_owned")
FORCE_REIMPORT_HINT = "use --force-reimport to override"
SPAWN_AT_GLOBAL_1_HINT = (
    "new galaxy Path B must import terran_space first "
    "(translator REGION_ORDER; spawn at global sector_id 1). "
    "OPERATIONS/bang-integration.md typically lists central_nexus then "
    "terran_space — that order would occupy global 1 with Nexus; refused."
)


logger = logging.getLogger("bootstrap_from_bang")


class _PathBNoDocker:
    """BangImportService requires a client at init; Path B never invoke_bang."""


def translator() -> BangImportService:
    return BangImportService(
        bang_image="path-b:unused",
        docker_client=MagicMock(spec=_PathBNoDocker, name="path_b_noop_docker"),
    )


def require_terran_first_for_new_galaxy(*, galaxy_exists: bool, region_type: str) -> None:
    if not galaxy_exists and region_type != NEW_GALAXY_FIRST_REGION:
        raise SystemExit(SPAWN_AT_GLOBAL_1_HINT)


def persist_region_snapshot(
    galaxy: Galaxy,
    region_type: str,
    region_id: uuid.UUID,
    universe: Dict[str, Any],
) -> None:
    """Write Path B universe blob onto Galaxy.bang_snapshot (idempotent key)."""
    galaxy.bang_snapshot = stamp_region_snapshot(
        galaxy.bang_snapshot,
        region_type,
        region_id=str(region_id),
        universe=universe,
    )
    flag_modified(galaxy, "bang_snapshot")


def should_attach_spokes_after_additional(region_type: str) -> bool:
    """apply_additional_region looks up nexus BEFORE write; adding nexus skips the tunnel."""
    return region_type == "central_nexus"


async def gate_attachment_for_region(session: Any, region_type: str) -> Optional[RegionAttachment]:
    row = (
        await session.execute(
            text(
                "SELECT s.id FROM sectors s "
                "JOIN regions r ON s.region_id = r.id "
                "WHERE r.region_type = :rt "
                "ORDER BY s.sector_id ASC LIMIT 1"
            ),
            {"rt": region_type},
        )
    ).first()
    if row is None:
        return None
    return RegionAttachment(gate_sector_id=row[0])


async def attach_existing_spokes_to_nexus(session: Any) -> None:
    """Reuse apply()'s _add_nexus_warp after Path B adds central_nexus second."""
    nexus = await gate_attachment_for_region(session, "central_nexus")
    if nexus is None:
        return
    for spoke_rt in SPOKE_REGION_TYPES:
        spoke = await gate_attachment_for_region(session, spoke_rt)
        await BangImportService._add_nexus_warp(session, spoke_rt, spoke, nexus)


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Path B bang.* bootstrap into the gameserver translator"
    )
    parser.add_argument("--bang-url", required=True, help="Postgres URL for the bang schema")
    parser.add_argument(
        "--universe-name",
        required=True,
        help="bang.universes.name written by service-mode db-writer",
    )
    parser.add_argument("--galaxy-name", required=True, help="Target gameserver Galaxy.name")
    parser.add_argument(
        "--region-type",
        required=True,
        choices=("central_nexus", "terran_space", "player_owned"),
        help=(
            "Target region (BangConfig literals). New galaxy: terran_space "
            "first (spawn at global sector_id 1), then central_nexus."
        ),
    )
    parser.add_argument(
        "--gameserver-url",
        default=os.environ.get("DATABASE_URL"),
        help="Gameserver Postgres URL (default: DATABASE_URL)",
    )
    parser.add_argument(
        "--force-reimport",
        action="store_true",
        help="Wipe existing region content and re-apply when seed/config mismatch",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Reconstruct + translate + validate only; do not persist",
    )
    return parser.parse_args(argv)


def fetch_universe_json(bang_url: str, universe_name: str) -> Dict[str, Any]:
    engine: Engine = create_engine(bang_url)
    try:
        with engine.connect() as conn:
            universe_row = conn.execute(
                text("SELECT * FROM bang.universes WHERE name = :n"),
                {"n": universe_name},
            ).mappings().first()
            if universe_row is None:
                raise SystemExit(
                    f"bang.universes row {universe_name!r} not found"
                )
            universe_id = universe_row["id"]
            sectors = mappings_from_result(
                conn.execute(
                    text(
                        "SELECT * FROM bang.sectors WHERE universe_id = :id "
                        "ORDER BY sector_number"
                    ),
                    {"id": universe_id},
                )
            )
            warps = mappings_from_result(
                conn.execute(
                    text("SELECT * FROM bang.warps WHERE universe_id = :id"),
                    {"id": universe_id},
                )
            )
            special_locations = mappings_from_result(
                conn.execute(
                    text(
                        "SELECT * FROM bang.special_locations WHERE universe_id = :id"
                    ),
                    {"id": universe_id},
                )
            )
            clusters = mappings_from_result(
                conn.execute(
                    text(
                        "SELECT * FROM bang.clusters WHERE universe_id = :id "
                        "ORDER BY cluster_number"
                    ),
                    {"id": universe_id},
                )
            )
            special_formations = mappings_from_result(
                conn.execute(
                    text(
                        "SELECT * FROM bang.special_formations WHERE universe_id = :id"
                    ),
                    {"id": universe_id},
                )
            )
            npc_rosters = mappings_from_result(
                conn.execute(
                    text("SELECT * FROM bang.npc_rosters WHERE universe_id = :id"),
                    {"id": universe_id},
                )
            )
            ports = mappings_from_result(
                conn.execute(
                    text(
                        "SELECT p.* FROM bang.ports p "
                        "JOIN bang.sectors s ON s.id = p.sector_id "
                        "WHERE s.universe_id = :id"
                    ),
                    {"id": universe_id},
                )
            )
            planets = mappings_from_result(
                conn.execute(
                    text(
                        "SELECT p.* FROM bang.planets p "
                        "JOIN bang.sectors s ON s.id = p.sector_id "
                        "WHERE s.universe_id = :id"
                    ),
                    {"id": universe_id},
                )
            )
            nebulae = mappings_from_result(
                conn.execute(
                    text(
                        "SELECT n.* FROM bang.nebulae n "
                        "JOIN bang.sectors s ON s.id = n.sector_id "
                        "WHERE s.universe_id = :id"
                    ),
                    {"id": universe_id},
                )
            )
            return universe_from_bang_rows(
                universe_row,
                sectors=sectors,
                warps=warps,
                special_locations=special_locations,
                ports=ports,
                planets=planets,
                nebulae=nebulae,
                clusters=clusters,
                special_formations=special_formations,
                npc_rosters=npc_rosters,
            )
    finally:
        engine.dispose()


def _region_defaults(region_type: RegionType, galaxy_name: str, seed: int, total_sectors: int) -> Dict[str, Any]:
    # Match Path A bang_galaxy.py Region() fields (name/display_name/
    # region_type/total_sectors/generation_seed). governance_type and
    # tax_rate stay on the column defaults — do not invent values.
    return {
        "name": f"path-b-{galaxy_name}-{region_type}",
        "display_name": f"{galaxy_name} — {region_type.replace('_', ' ').title()}",
        "region_type": region_type,
        "total_sectors": total_sectors,
        "generation_seed": seed,
    }


async def bootstrap(args: argparse.Namespace) -> int:  # noqa: C901 — new vs additional galaxy branches
    if not args.gameserver_url:
        raise SystemExit("gameserver database URL missing (pass --gameserver-url or DATABASE_URL)")

    raw = fetch_universe_json(args.bang_url, args.universe_name)
    region_type: RegionType = args.region_type
    parsed = ParsedUniverse(region_type=region_type, raw=raw)
    service = translator()

    expected = REGION_SECTORS[region_type]
    if expected is not None and parsed.total_sectors != expected:
        raise SystemExit(
            f"{region_type} expected {expected} sectors; bang universe has {parsed.total_sectors}"
        )

    from sqlalchemy.ext.asyncio import AsyncSession as _AsyncSession

    from src.core.database import async_sessionmaker, create_async_engine

    gs_url = args.gameserver_url.replace("postgresql://", "postgresql+asyncpg://")
    engine = create_async_engine(gs_url, pool_pre_ping=True)
    session_factory = async_sessionmaker(
        bind=engine, class_=_AsyncSession, autocommit=False, autoflush=False
    )

    try:
        async with session_factory() as session:
            galaxy = (
                await session.execute(select(Galaxy).where(Galaxy.name == args.galaxy_name))
            ).scalar_one_or_none()
            require_terran_first_for_new_galaxy(
                galaxy_exists=galaxy is not None, region_type=region_type
            )

            if galaxy is not None and region_already_imported(
                galaxy.bang_snapshot,
                region_type,
                seed=parsed.seed,
                config=raw.get("config") or {},
            ):
                print(
                    f"idempotent skip: galaxy {args.galaxy_name!r} already has "
                    f"{region_type} at seed={parsed.seed} config={config_fingerprint(raw.get('config') or {})[:12]}"
                )
                return 0

            if galaxy is not None:
                snap = galaxy.bang_snapshot or {}
                existing = (snap.get("regions") or {}).get(region_type)
                if existing and not args.force_reimport:
                    raise SystemExit(
                        f"Galaxy {args.galaxy_name!r} already has {region_type} "
                        f"with a different seed/config; {FORCE_REIMPORT_HINT}"
                    )

            region_metadata = {
                "galaxy_name": args.galaxy_name,
                "master_seed": parsed.seed,
            }

            if args.dry_run:
                if galaxy is None:
                    region_id = uuid.uuid4()
                    region_metadata["regions"] = {region_type: {"region_id": str(region_id)}}
                    plan = service.translate({region_type: parsed}, region_metadata)
                    validate_insert_plan_or_raise(plan)
                else:
                    region_plan = service._translate_region(region_type, parsed)
                    validate_region_plan_or_raise(region_plan)
                print(f"dry-run ok: reconstructed {parsed.total_sectors} sectors for {region_type}")
                return 0

            if galaxy is None:
                region_id = uuid.uuid4()
                session.add(
                    Region(
                        id=region_id,
                        **_region_defaults(
                            region_type, args.galaxy_name, parsed.seed, parsed.total_sectors
                        ),
                    )
                )
                await session.flush()
                region_metadata["regions"] = {region_type: {"region_id": str(region_id)}}
                plan = service.translate({region_type: parsed}, region_metadata)
                validate_insert_plan_or_raise(plan)
                try:
                    galaxy = await service.apply(plan, session)
                    persist_region_snapshot(galaxy, region_type, region_id, raw)
                    await session.commit()
                except Exception:
                    await session.rollback()
                    raise
                print(
                    f"imported {region_type} into new galaxy {args.galaxy_name!r} "
                    f"({parsed.total_sectors} sectors)"
                )
                return 0

            # Existing galaxy: splice one region (or force-reimport).
            region_id: Optional[uuid.UUID] = None
            snap = galaxy.bang_snapshot or {}
            existing = (snap.get("regions") or {}).get(region_type) or {}
            if existing.get("region_id"):
                region_id = uuid.UUID(str(existing["region_id"]))
            if region_id is None:
                region_id = uuid.uuid4()
                session.add(
                    Region(
                        id=region_id,
                        **_region_defaults(
                            region_type, args.galaxy_name, parsed.seed, parsed.total_sectors
                        ),
                    )
                )
                await session.flush()
            elif args.force_reimport:
                await service.wipe_region_content(session, region_id)

            current_max = (
                await session.execute(text("SELECT COALESCE(MAX(sector_id), 0) FROM sectors"))
            ).scalar()
            sector_id_offset = int(current_max or 0)
            region_plan = service._translate_region(region_type, parsed)
            if region_type == "terran_space":
                region_plan = service._apply_terran_space_invariants(region_plan, [])
            if sector_id_offset > 0:
                service._offset_region_sector_ids(region_plan, sector_id_offset)
            validate_region_plan_or_raise(region_plan)
            try:
                await service.apply_additional_region(
                    galaxy.id, region_plan, region_id, session
                )
                persist_region_snapshot(galaxy, region_type, region_id, raw)
                if should_attach_spokes_after_additional(region_type):
                    try:
                        await attach_existing_spokes_to_nexus(session)
                    except Exception:
                        # ADR-0050 SK22: tunnel failure must not roll back
                        # already-written region content (same as Path A
                        # apply_additional_region Phase 14).
                        logger.exception(
                            "Path B Nexus spoke attach failed; region content kept"
                        )
                await session.commit()
            except Exception:
                await session.rollback()
                raise
            print(
                f"imported {region_type} into existing galaxy {args.galaxy_name!r} "
                f"({parsed.total_sectors} sectors, offset={sector_id_offset})"
            )
            return 0
    finally:
        await engine.dispose()


def main(argv: Optional[list[str]] = None) -> int:
    args = parse_args(argv)
    try:
        return asyncio.run(bootstrap(args))
    except SystemExit:
        raise
    except Exception as exc:
        raise SystemExit(f"Path B bootstrap failed (no partial persist): {exc}") from exc


if __name__ == "__main__":
    sys.exit(main())

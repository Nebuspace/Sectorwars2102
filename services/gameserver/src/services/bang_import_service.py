"""Bang universe-generator integration service.

This module hosts :class:`BangImportService`, the Phase-1C translator that
invokes the ``sw2102-bang`` Docker sidecar, parses its Universe JSON output,
and persists the canonical gameserver rows in a single atomic transaction.

The translator is the cleavage point of the ADR-0069 contract:
    bang.docker:1.3.0 ──(stdout JSON)──▶ BangImportService.translate
                                           │
                                           ▼ pure InsertPlan
                                       BangImportService.apply
                                           │
                                           ▼ Galaxy / Region / Cluster / Sector …
                                           gameserver canonical schema

Key invariants (per ``DOCS/PLANS/bang-integration.md`` and
``bang-integration-schema-map.md``):

* :meth:`translate` is **pure** — no DB writes, no subprocess calls.
* :meth:`apply` writes inside a single transaction owned by the caller.
* Q1: Station commodities dicts carry all 9 keys including ``precious_metals``.
* Q2: ``Station.is_spacedock`` flips to ``True`` when bang reports ``isSpaceDock``.
* Q3: ``Universe.npcRosters`` is stashed on ``Galaxy.bang_snapshot.npc_rosters``.
* Q4: ``Planet.owner_id`` accepts UUID strings emitted by bang.
* Q6: Bang's ``LOST_SECTOR``/``LOST_CLUSTER``/``ARCHIPELAGO`` enum values are
  passed through (the Postgres enum is extended by the Job Model Author's
  Alembic migration).

The ``terran_space`` region additionally enforces the legacy starter
invariants (Earth Station, New Earth with 8 B population, SpaceDock at
sector 10 with full service flags) so the first-login flow keeps working
after the legacy ``GalaxyGenerator`` is removed in Phase 4.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import subprocess  # noqa: S404 -- we invoke a pinned local CLI/Docker image
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.bang_generation_job import (
    BangGenerationJob,
    BangGenerationJobStatus,
)
from src.models.cluster import Cluster, ClusterType
from src.models.galaxy import Galaxy, GalaxyImportState
from src.models.planet import Planet, PlanetStatus, PlanetType
from src.models.sector import Sector, SectorType, sector_warps
from src.models.special_formation import SpecialFormation, SpecialFormationType
from src.models.station import (
    Station,
    StationClass,
    StationStatus,
    StationType,
)
from src.schemas.bang_config import BangConfig, RegionType

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: Pinned bang image tag used by :meth:`BangImportService.invoke_bang`.
#: Overridable via the ``BANG_VERSION`` env var (set by Phase 2 Dockerfile work).
DEFAULT_BANG_IMAGE = "docker.io/drxelanull/sw2102-bang:1.3.0"

#: PG advisory-lock key used to serialise concurrent generation jobs.
#: Lives here (not in the schema package) so the translator and any future
#: callers share a single source of truth. The integer is arbitrary but
#: stable; pick a value unlikely to collide with other gameserver locks.
GALAXY_GEN_LOCK_KEY = 0x5747_4E47_4C58_4B59  # "SWGNGLXKY" in ASCII

#: Canonical 9-commodity wire (ADR-0062 E-D1). Mirrors the default dict on
#: :class:`src.models.station.Station.commodities`. Order matters for stable
#: comparisons in tests; keep it explicit.
COMMODITY_WIRE_ORDER: Tuple[str, ...] = (
    "ore",
    "organics",
    "equipment",
    "fuel",
    "luxury_goods",
    "gourmet_food",
    "exotic_technology",
    "colonists",
    "precious_metals",
)

#: Per-commodity baseline used when bang's payload omits a key. The numbers
#: mirror :class:`Station.commodities` default so a freshly-imported station
#: has the same shape as one created by direct ORM construction.
_COMMODITY_DEFAULTS: Dict[str, Dict[str, Any]] = {
    "ore": {"base_price": 15, "capacity": 5000, "production_rate": 100, "price_variance": 20},
    "organics": {"base_price": 18, "capacity": 3000, "production_rate": 80, "price_variance": 25},
    "equipment": {"base_price": 35, "capacity": 2000, "production_rate": 50, "price_variance": 30},
    "fuel": {"base_price": 12, "capacity": 4000, "production_rate": 120, "price_variance": 15},
    "luxury_goods": {"base_price": 100, "capacity": 800, "production_rate": 20, "price_variance": 40},
    "gourmet_food": {"base_price": 80, "capacity": 600, "production_rate": 15, "price_variance": 35},
    "exotic_technology": {"base_price": 250, "capacity": 200, "production_rate": 5, "price_variance": 50},
    "colonists": {"base_price": 50, "capacity": 500, "production_rate": 10, "price_variance": 10},
    "precious_metals": {"base_price": 130, "capacity": 400, "production_rate": 8, "price_variance": 30},
}

#: Lossy bang→gameserver planet-type mapping (per schema map §2.6).
_PLANET_TYPE_MAP: Dict[str, PlanetType] = {
    "barren": PlanetType.BARREN,
    "earth": PlanetType.TERRAN,
    "mountainous": PlanetType.MOUNTAINOUS,
    "oceanic": PlanetType.OCEANIC,
    "glacial": PlanetType.ICE,
    "volcanic": PlanetType.VOLCANIC,
}

#: bang Port.class → gameserver StationType (heuristic; matches legacy
#: GalaxyGenerator weighting tables). CLASS_0 stays TRADING since SpaceDocks
#: route via :attr:`Station.is_spacedock` per Q2.
_STATION_TYPE_BY_CLASS: Dict[int, StationType] = {
    0: StationType.TRADING,
    1: StationType.MINING,
    2: StationType.OUTPOST,
    3: StationType.INDUSTRIAL,
    4: StationType.TRADING,
    5: StationType.TRADING,
    6: StationType.TRADING,
    7: StationType.TRADING,
    8: StationType.BLACK_MARKET,
}

#: Security level by cluster type. Mirrors the spirit of the legacy generator
#: without enumerating its exact percentile table.
_SECURITY_BY_CLUSTER_TYPE: Dict[ClusterType, int] = {
    ClusterType.STANDARD: 5,
    ClusterType.RESOURCE_RICH: 5,
    ClusterType.POPULATION_CENTER: 7,
    ClusterType.TRADE_HUB: 7,
    ClusterType.MILITARY_ZONE: 8,
    ClusterType.FRONTIER_OUTPOST: 3,
    ClusterType.CONTESTED: 3,
    ClusterType.SPECIAL_INTEREST: 4,
}

#: Bang region-type → expected sector count (sanity check only).
_EXPECTED_SECTOR_COUNT: Dict[RegionType, Optional[int]] = {
    "player_owned": None,
    "terran_space": 300,
    "central_nexus": 5000,
}

#: BangConfig snake_case field → bang CLI kebab-case flag. Only optional
#: flags with a 1:1 CLI surface live here; the three required fields
#: (seed, sectors, region_type) are emitted directly in
#: :meth:`BangImportService._build_docker_args`. ``validator_strictness``
#: is intentionally absent — bang has no strictness levels today
#: (per Phase 1B handoff).
_CLI_FLAG_MAP: Dict[str, str] = {
    "max_warps": "--max-warps",
    "one_way_warp_percent": "--one-way-warps",
    "port_percent": "--port-percent",
    "planet_percent": "--planet-percent",
    "nebula_percent": "--nebula-percent",
    "stardock_enabled": "--stardock-enabled",
}


# ---------------------------------------------------------------------------
# Lightweight dataclasses (kept here, not in `schemas/`, because they are
# implementation-internal; only the public Pydantic types in
# `schemas/bang_*.py` cross the API boundary.)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ParsedUniverse:
    """Thin wrapper around the raw Universe JSON.

    We do not project bang's TypeScript types into Pydantic — bang already
    validated the payload before emitting it, and rewriting every field
    here would double the maintenance surface. The translator pokes at the
    JSON via dict access and keys validated against a small schema in
    :func:`_validate_universe_shape`.
    """

    region_type: RegionType
    raw: Dict[str, Any]

    @property
    def version(self) -> str:
        return str(self.raw.get("version", ""))

    @property
    def total_sectors(self) -> int:
        return int(self.raw.get("totalSectors", 0))

    @property
    def seed(self) -> int:
        return int(self.raw.get("seed", 0))


@dataclass
class ValidationReport:
    """Outcome of a ``--validate-only`` invocation."""

    stats: Dict[str, Any] = field(default_factory=dict)
    warnings: List[Dict[str, Any]] = field(default_factory=list)
    validation: Dict[str, Any] = field(default_factory=dict)


# Insert spec dataclasses ----------------------------------------------------
#
# Each spec captures *only* what the translator needs to construct the
# corresponding ORM row. They intentionally mirror the column shape closely
# so :meth:`apply` reads like a literal materialisation.


@dataclass
class SectorSpec:
    sector_id: int
    name: str
    region_int_id: int  # bang's int → resolved to UUID via maps in `apply`
    cluster_int_id: int
    x_coord: int
    y_coord: int
    z_coord: int
    type: SectorType
    security_level: int
    hazard_level: int
    nav_hazards: Dict[str, Any]
    nav_beacons: List[Dict[str, Any]]
    special_features: List[str]
    is_discovered: bool
    description: Optional[str] = None


@dataclass
class WarpSpec:
    from_sector_int: int
    to_sector_int: int
    is_bidirectional: bool
    turn_cost: int
    warp_stability: float


@dataclass
class StationSpec:
    sector_int_id: int
    name: str
    station_class: StationClass
    station_type: StationType
    status: StationStatus
    commodities: Dict[str, Dict[str, Any]]
    services: Dict[str, Any]
    is_spacedock: bool
    description: Optional[str] = None


@dataclass
class PlanetSpec:
    sector_int_id: int
    name: str
    planet_type: PlanetType
    status: PlanetStatus
    owner_id: Optional[uuid.UUID]
    habitability_score: int
    max_population: int
    max_colonists: int
    population: int
    fuel_ore: int
    organics: int
    equipment: int
    colonists: int
    citadel_level: int
    citadel_drone_capacity: int
    citadel_safe_credits: int


@dataclass
class ClusterSpec:
    cluster_int_id: int
    name: str
    type: ClusterType
    sector_range_start: int
    sector_range_end: int
    sector_count: int
    x_coord: int
    y_coord: int
    z_coord: int
    warp_stability: float
    economic_value: int
    recommended_ship_class: str
    max_warps: int
    island_group_id: Optional[int]
    is_discovered: bool
    is_hidden: bool
    special_features: List[str]


@dataclass
class FormationSpec:
    formation_int_id: int
    type: str  # stored as string; we widen the enum at construction time
    name: str
    anchor_sector_int: int
    interior_sector_ints: List[int]
    properties: Dict[str, Any]
    is_discovered: bool


@dataclass
class RegionInsertPlan:
    region_type: RegionType
    universe_seed: int
    total_sectors: int
    clusters: List[ClusterSpec]
    sectors: List[SectorSpec]
    warps: List[WarpSpec]
    stations: List[StationSpec]
    planets: List[PlanetSpec]
    formations: List[FormationSpec]
    fedspace_sector_ints: List[int]
    special_location_by_sector: Dict[int, str]  # sector_id → slug
    raw_npc_rosters: List[Dict[str, Any]]
    raw_universe: Dict[str, Any]  # verbatim, lands on Galaxy.bang_snapshot


@dataclass
class InsertPlan:
    """Container for the planned writes across all 3 regions."""

    galaxy_name: str
    bang_version: str
    bang_seed: int
    bang_config_hash: str
    bang_snapshot: Dict[str, Any]
    generation_warnings: List[Dict[str, Any]]
    regions: Dict[RegionType, RegionInsertPlan]


# ---------------------------------------------------------------------------
# The service
# ---------------------------------------------------------------------------


class BangImportService:
    """Glue between the bang Docker sidecar and the canonical gameserver schema.

    Public entrypoints map 1:1 to the four phases of a generation job:

    * :meth:`validate_only`  — preview seed; no DB row written.
    * :meth:`invoke_bang`    — run bang once for one region.
    * :meth:`translate`      — pure planning step.
    * :meth:`apply`          — atomic DB write.

    The top-level orchestrator :meth:`run_generation_job` strings them
    together; admin routes call into ``run_generation_job`` via
    :class:`fastapi.BackgroundTasks`.
    """

    def __init__(
        self,
        bang_image: str = DEFAULT_BANG_IMAGE,
        *,
        subprocess_runner: Optional[Callable[..., subprocess.CompletedProcess]] = None,
        log_sink: Optional[Callable[[str], Awaitable[None]]] = None,
    ) -> None:
        """Construct a translator.

        Args:
            bang_image: Pinned bang Docker image (``repo:tag``). Tests pass a
                no-op value because they short-circuit :meth:`invoke_bang`.
            subprocess_runner: Override for :func:`subprocess.run`. Used by
                unit tests to mock the bang invocation.
            log_sink: Async callable that receives every stderr line emitted
                by bang. The orchestrator wires this to append to
                ``BangGenerationJob.log_text``.
        """
        self.bang_image = bang_image
        self._run = subprocess_runner or subprocess.run
        self._log_sink = log_sink

    # ----- invocation -----------------------------------------------------

    def invoke_bang(
        self,
        config: BangConfig,
        timeout_seconds: int = 300,
    ) -> ParsedUniverse:
        """Spawn ``docker run sw2102-bang`` for one region; parse stdout JSON.

        The image is invoked with ``--json-out`` so stdout contains exactly
        the Universe JSON; stderr carries progress/warnings which we
        forward to ``self._log_sink`` if set.

        Raises:
            RuntimeError: If the subprocess exits non-zero, the JSON fails
                to parse, or the resulting Universe fails the shape check
                in :func:`_validate_universe_shape`.
        """
        args = self._build_docker_args(config)
        logger.info("invoke_bang: %s", args)
        try:
            completed = self._run(
                args,
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:  # pragma: no cover - integration path
            raise RuntimeError(
                f"bang timed out after {timeout_seconds}s for region "
                f"{config.region_type}"
            ) from exc

        if completed.stderr:
            self._forward_stderr(completed.stderr)
        if completed.returncode != 0:
            raise RuntimeError(
                f"bang exited {completed.returncode} for region "
                f"{config.region_type}: {completed.stderr[-2000:]}"
            )
        try:
            payload = json.loads(completed.stdout)
        except json.JSONDecodeError as exc:
            raise RuntimeError(
                f"bang produced invalid JSON for region "
                f"{config.region_type}: {exc}"
            ) from exc

        _validate_universe_shape(payload, region_type=config.region_type)
        return ParsedUniverse(region_type=config.region_type, raw=payload)

    def validate_only(self, config: BangConfig) -> ValidationReport:
        """Run bang with ``--validate-only``; return stats + warnings inline."""
        args = self._build_docker_args(config, validate_only=True)
        logger.info("validate_only: %s", args)
        completed = self._run(
            args,
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
        if completed.returncode not in (0, 2):
            raise RuntimeError(
                f"bang --validate-only exited {completed.returncode}: "
                f"{completed.stderr[-1000:]}"
            )
        try:
            payload = json.loads(completed.stdout or "{}")
        except json.JSONDecodeError:
            payload = {}
        return ValidationReport(
            stats=payload.get("stats", {}),
            warnings=payload.get("warnings", []),
            validation=payload.get("validation", {}),
        )

    # ----- pure translation ----------------------------------------------

    def translate(
        self,
        universes: Dict[RegionType, ParsedUniverse],
        region_metadata: Dict[str, Any],
    ) -> InsertPlan:
        """Build an :class:`InsertPlan` from up to 3 parsed Universes.

        This is the heart of the schema map: pure, deterministic, no DB
        side effects. ``region_metadata`` carries operator-supplied fields
        (galaxy name, per-region UUIDs if pre-created, etc.).
        """
        if not universes:
            raise ValueError("translate() requires at least one Universe")

        bang_versions = {u.version for u in universes.values()}
        if len(bang_versions) != 1:
            raise ValueError(
                f"Inconsistent bang versions across regions: {bang_versions}"
            )
        bang_version = next(iter(bang_versions))

        # Seed is shared across the 3 sub-invocations by construction
        # (caller derives sub-seeds and we record the master). Per the plan,
        # the master seed lives on Galaxy.bang_seed; sub-seeds are reproducible
        # from it.
        master_seed = int(region_metadata.get("master_seed", 0))

        # Canonical-JSON SHA-256 per Job Model Author's convention: sorted
        # keys, no whitespace separators, UTF-8 encoding. 64 chars hex.
        config_hash_input = json.dumps(
            {rt: u.raw.get("config", {}) for rt, u in universes.items()},
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        config_hash = hashlib.sha256(config_hash_input).hexdigest()

        per_region: Dict[RegionType, RegionInsertPlan] = {}
        warnings: List[Dict[str, Any]] = []

        for region_type, universe in universes.items():
            plan = self._translate_region(region_type, universe)
            if region_type == "terran_space":
                # Enforce the legacy starter-region invariants per the
                # GalaxyGenerator audit's "Top 3 risks".
                plan = self._apply_terran_space_invariants(plan, warnings)
            per_region[region_type] = plan

        # `Galaxy.bang_snapshot` carries the **full Universe blob per region**
        # (per Job Model Author's contract): reproducibility + version-debug
        # without a staging schema. NPC roster extraction happens on read
        # (Q3 strategy A) — rosters are already part of Universe.npcRosters
        # inside each region's blob, so no separate stash is needed.
        # Also include orchestrator-supplied region metadata (region_id,
        # galaxy_name overrides) so `apply()` can wire FK references.
        bang_snapshot: Dict[str, Any] = {
            "config_hash": config_hash,
            "regions": {
                rt: {
                    "universe": plan.raw_universe,
                    # Slot kept open for the orchestrator to fill before apply():
                    # `region_id` (UUID of the pre-created Region row).
                    **(
                        region_metadata.get("regions", {}).get(rt, {})
                    ),
                }
                for rt, plan in per_region.items()
            },
        }

        return InsertPlan(
            galaxy_name=str(region_metadata.get("galaxy_name", "SectorWars Galaxy")),
            bang_version=bang_version,
            bang_seed=master_seed,
            bang_config_hash=config_hash,
            bang_snapshot=bang_snapshot,
            generation_warnings=warnings,
            regions=per_region,
        )

    # ----- atomic write ---------------------------------------------------

    async def apply(self, plan: InsertPlan, session: AsyncSession) -> Galaxy:
        """Persist the :class:`InsertPlan` in a single transaction.

        The caller controls the transaction (per
        :meth:`run_generation_job`'s ``session.begin()`` block); we only
        ``flush()`` so PKs surface for later FK references.

        Insert order (per the integration plan):
            Galaxy → Regions → Clusters → Sectors → Warps → Stations
                → Planets → SpecialFormations
        """
        galaxy = Galaxy(
            name=plan.galaxy_name,
            import_state=GalaxyImportState.GENERATING,
            bang_version=plan.bang_version,
            bang_seed=plan.bang_seed,
            bang_config_hash=plan.bang_config_hash,
            bang_snapshot=plan.bang_snapshot,
            generation_warnings=plan.generation_warnings,
        )
        session.add(galaxy)
        await session.flush()

        # The translator does NOT create Region rows — the orchestrator
        # supplies a pre-created region_id per region (per ADR-0069 §52,
        # operator owns Region metadata). We resolve them from
        # plan.bang_snapshot["regions"][rt]["region_id"] when present.
        region_ids: Dict[RegionType, uuid.UUID] = {}
        for rt, region_snapshot in plan.bang_snapshot.get("regions", {}).items():
            rid = region_snapshot.get("region_id") if isinstance(region_snapshot, dict) else None
            if rid is not None:
                region_ids[rt] = (
                    rid if isinstance(rid, uuid.UUID) else uuid.UUID(str(rid))
                )

        for region_type, region_plan in plan.regions.items():
            region_id = region_ids.get(region_type)
            if region_id is None:
                raise ValueError(
                    f"apply() missing region_id for {region_type} — orchestrator "
                    "must pre-create the Region row and pass its UUID via "
                    "InsertPlan.bang_snapshot['regions'][region_type]['region_id']"
                )
            await self._apply_region(session, region_plan, region_id)

        # Final state flip lives on the same transaction as the inserts so
        # there is no observable partial state.
        galaxy.import_state = GalaxyImportState.READY  # type: ignore[assignment]
        await session.flush()
        return galaxy

    async def _apply_region(
        self,
        session: AsyncSession,
        region_plan: RegionInsertPlan,
        region_id: uuid.UUID,
    ) -> None:
        """Write one region's clusters, sectors, warps, stations, planets, formations."""
        cluster_uuid_by_int: Dict[int, uuid.UUID] = {}
        for cs in region_plan.clusters:
            cluster = Cluster(
                region_id=region_id,
                name=cs.name,
                type=cs.type,
                sector_count=cs.sector_count,
                x_coord=cs.x_coord,
                y_coord=cs.y_coord,
                z_coord=cs.z_coord,
                warp_stability=cs.warp_stability,
                economic_value=cs.economic_value,
                recommended_ship_class=cs.recommended_ship_class,
                is_discovered=cs.is_discovered,
                is_hidden=cs.is_hidden,
                special_features=cs.special_features,
                stats={
                    "sector_range_start": cs.sector_range_start,
                    "sector_range_end": cs.sector_range_end,
                    "max_warps": cs.max_warps,
                    "island_group_id": cs.island_group_id,
                },
            )
            session.add(cluster)
            await session.flush()
            cluster_uuid_by_int[cs.cluster_int_id] = cluster.id  # type: ignore[assignment]

        sector_uuid_by_int: Dict[int, uuid.UUID] = {}
        for ss in region_plan.sectors:
            cluster_uuid = cluster_uuid_by_int[ss.cluster_int_id]
            sector = Sector(
                sector_id=ss.sector_id,
                sector_number=ss.sector_id,
                name=ss.name,
                region_id=region_id,
                cluster_id=cluster_uuid,
                type=ss.type,
                security_level=ss.security_level,
                hazard_level=ss.hazard_level,
                x_coord=ss.x_coord,
                y_coord=ss.y_coord,
                z_coord=ss.z_coord,
                nav_hazards=ss.nav_hazards,
                nav_beacons=ss.nav_beacons,
                special_features=ss.special_features,
                is_discovered=ss.is_discovered,
                description=ss.description,
            )
            session.add(sector)
            await session.flush()
            sector_uuid_by_int[ss.sector_id] = sector.id  # type: ignore[assignment]

        # Warps — direct INSERT into the association table for batching.
        for w in region_plan.warps:
            await session.execute(
                sector_warps.insert().values(
                    source_sector_id=sector_uuid_by_int[w.from_sector_int],
                    destination_sector_id=sector_uuid_by_int[w.to_sector_int],
                    is_bidirectional=w.is_bidirectional,
                    turn_cost=w.turn_cost,
                    warp_stability=w.warp_stability,
                )
            )

        for stsp in region_plan.stations:
            station = Station(
                name=stsp.name,
                sector_id=stsp.sector_int_id,
                sector_uuid=sector_uuid_by_int[stsp.sector_int_id],
                region_id=region_id,
                station_class=stsp.station_class,
                type=stsp.station_type,
                status=stsp.status,
                commodities=stsp.commodities,
                services=stsp.services,
                is_spacedock=stsp.is_spacedock,
                description=stsp.description,
            )
            session.add(station)

        for ps in region_plan.planets:
            planet = Planet(
                name=ps.name,
                sector_id=ps.sector_int_id,
                sector_uuid=sector_uuid_by_int[ps.sector_int_id],
                region_id=region_id,
                owner_id=ps.owner_id,
                type=ps.planet_type,
                status=ps.status,
                habitability_score=ps.habitability_score,
                max_population=ps.max_population,
                max_colonists=ps.max_colonists,
                population=ps.population,
                fuel_ore=ps.fuel_ore,
                organics=ps.organics,
                equipment=ps.equipment,
                colonists=ps.colonists,
                citadel_level=ps.citadel_level,
                citadel_drone_capacity=ps.citadel_drone_capacity,
                citadel_safe_credits=ps.citadel_safe_credits,
            )
            session.add(planet)

        for fs in region_plan.formations:
            formation_enum = _coerce_formation_type(fs.type)
            formation = SpecialFormation(
                region_id=region_id,
                type=formation_enum,
                anchor_sector_id=sector_uuid_by_int[fs.anchor_sector_int],
                interior_sector_ids=[
                    sector_uuid_by_int[i] for i in fs.interior_sector_ints
                ],
                properties={**fs.properties, "name": fs.name},
                is_discovered=fs.is_discovered,
            )
            session.add(formation)

        await session.flush()

    # ----- top-level orchestration ---------------------------------------

    async def run_generation_job(
        self,
        job_id: uuid.UUID,
        params: BangConfig,
        *,
        session_factory: Optional[Callable[[], Any]] = None,
        region_metadata: Optional[Dict[str, Any]] = None,
        emit_event: Optional[Callable[[str, Dict[str, Any]], Awaitable[None]]] = None,
    ) -> None:
        """End-to-end: lock → bang × 3 → translate → apply → mark complete.

        See :meth:`apply` for the transactional shape.

        Args:
            job_id: Pre-existing :class:`BangGenerationJob` row id.
            params: The form payload that opened the job; only its
                ``seed`` / ``region_type`` fields are used here (the
                per-region BangConfigs are derived below).
            session_factory: Async session factory. Defaults to the
                gameserver's :data:`AsyncSessionLocal`. Tests can inject
                a sqlite-backed factory.
            region_metadata: ``{"galaxy_name": str, "regions": {<rt>: {"region_id": UUID}}}``.
                Operator-supplied; required because the translator does
                NOT create Region rows itself.
            emit_event: Optional realtime-bus hook; called with
                ``("galaxy.imported", {"galaxy_id": ...})`` on success.
        """
        if session_factory is None:
            from src.core.database import AsyncSessionLocal as _Session  # type: ignore[import-not-found]
            session_factory = _Session

        region_metadata = dict(region_metadata or {})
        region_metadata.setdefault("master_seed", params.seed)
        region_metadata.setdefault("galaxy_name", "SectorWars Galaxy")

        start_ts = time.monotonic()

        async with session_factory() as session:
            # Acquire advisory lock; if another job holds it, fail fast.
            locked = (
                await session.execute(
                    text("SELECT pg_try_advisory_lock(:k)"),
                    {"k": GALAXY_GEN_LOCK_KEY},
                )
            ).scalar()
            if not locked:
                await self._mark_job_failed(
                    session,
                    job_id,
                    "another galaxy-generation job is already running",
                )
                await session.commit()
                return

            try:
                await self._set_job_status(session, job_id, BangGenerationJobStatus.RUNNING)
                await session.commit()

                # Three sub-invocations — sub-seeds derived from master so
                # callers can re-run any region with the same shape.
                universes: Dict[RegionType, ParsedUniverse] = {}
                region_types: Tuple[RegionType, ...] = (
                    "player_owned",
                    "terran_space",
                    "central_nexus",
                )
                for offset, region_type in enumerate(region_types):
                    sub_config = BangConfig(
                        seed=params.seed + offset,
                        sectors=(
                            params.sectors if region_type == "player_owned"
                            else (_EXPECTED_SECTOR_COUNT[region_type] or params.sectors)
                        ),
                        region_type=region_type,
                    )
                    parsed = await asyncio.to_thread(
                        self.invoke_bang, sub_config, 300
                    )
                    universes[region_type] = parsed
                    await self._append_log(
                        session,
                        job_id,
                        f"[{region_type}] parsed {parsed.total_sectors} sectors\n",
                    )

                plan = self.translate(universes, region_metadata)

                async with session.begin():
                    galaxy = await self.apply(plan, session)

                duration_ms = int((time.monotonic() - start_ts) * 1000)
                await self._mark_job_complete(
                    session, job_id, duration_ms, plan.generation_warnings
                )
                await session.commit()

                if emit_event is not None:
                    await emit_event(
                        "galaxy.imported",
                        {"galaxy_id": str(galaxy.id), "job_id": str(job_id)},
                    )
            except Exception as exc:  # pragma: no cover - integration path
                logger.exception("run_generation_job failed: %s", exc)
                try:
                    await session.rollback()
                except Exception:  # noqa: S110, BLE001 - best-effort rollback
                    logger.debug("rollback after failure also failed", exc_info=True)
                await self._mark_job_failed(session, job_id, str(exc))
                await session.commit()
            finally:
                await session.execute(
                    text("SELECT pg_advisory_unlock(:k)"),
                    {"k": GALAXY_GEN_LOCK_KEY},
                )
                await session.commit()

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _build_docker_args(
        self, config: BangConfig, *, validate_only: bool = False
    ) -> List[str]:
        """Build the ``docker run …`` argv for one bang invocation.

        BangConfig fields are snake_case (Python convention); bang's CLI
        flags are kebab-case. Translation table lives in ``_CLI_FLAG_MAP``;
        omitted fields fall back to bang's defaults (per ADR-0069's
        "GenerationRequest is optional" contract).
        """
        cmd: List[str] = [
            "docker",
            "run",
            "--rm",
            "-i",
            self.bang_image,
            "--seed",
            str(config.seed),
            "--sectors",
            str(config.sectors),
            "--region-type",
            config.region_type,
        ]
        # Only pass optional flags when they have been set.
        config_dict = config.model_dump(exclude_none=True)
        for field_name, cli_flag in _CLI_FLAG_MAP.items():
            if field_name in config_dict and field_name not in {
                "seed",
                "sectors",
                "region_type",
            }:
                value = config_dict[field_name]
                # `stardock_enabled` is a bool toggle; everything else has
                # a value argument.
                if isinstance(value, bool):
                    if value:
                        cmd.append(cli_flag)
                    continue
                cmd.extend([cli_flag, str(value)])
        if validate_only:
            cmd.append("--validate-only")
        else:
            cmd.append("--json-out")
        return cmd

    def _forward_stderr(self, blob: str) -> None:
        """Pump bang's stderr into the configured log sink, line-by-line."""
        sink = self._log_sink
        if sink is None:
            return
        for line in blob.splitlines(keepends=True):
            try:
                asyncio.get_event_loop().create_task(sink(line))  # type: ignore[arg-type]
            except RuntimeError:
                # No running loop — best-effort fallback; tests can ignore.
                pass

    # ----- region translation pieces -------------------------------------

    # noqa: C901 - straight-line schema mapping; splitting would obscure
    # the field-by-field structure that mirrors the schema map document.
    def _translate_region(  # noqa: C901
        self, region_type: RegionType, universe: ParsedUniverse
    ) -> RegionInsertPlan:
        raw = universe.raw
        bang_clusters = raw.get("clusters") or []
        if not bang_clusters:
            raise ValueError(
                f"{region_type} Universe missing required `clusters[]` "
                "(v1.3.0 contract)"
            )

        # Build cluster specs first; we need them to resolve per-sector
        # cluster ids without walking N×K each time.
        cluster_specs: List[ClusterSpec] = []
        cluster_by_sector_int: Dict[int, ClusterSpec] = {}
        for bc in bang_clusters:
            cs = ClusterSpec(
                cluster_int_id=int(bc["id"]),
                name=str(bc["name"]),
                type=ClusterType[str(bc["type"])],
                sector_range_start=int(bc["sectorRangeStart"]),
                sector_range_end=int(bc["sectorRangeEnd"]),
                sector_count=int(bc.get("sectorCount", 0)),
                x_coord=int(bc.get("coords", {}).get("x", 0)),
                y_coord=int(bc.get("coords", {}).get("y", 0)),
                z_coord=int(bc.get("coords", {}).get("z", 0)),
                warp_stability=float(bc.get("warpStability", 1.0)),
                economic_value=int(bc.get("economicValue", 50)),
                recommended_ship_class=str(
                    bc.get("recommendedShipClass", "light_freighter")
                ),
                max_warps=int(bc.get("maxWarps", 6)),
                island_group_id=(
                    int(bc["islandGroupId"]) if bc.get("islandGroupId") is not None else None
                ),
                is_discovered=bool(bc.get("isDiscovered", True)),
                is_hidden=bool(bc.get("isHidden", False)),
                special_features=(
                    ["fedspace"]
                    if int(bc["sectorRangeStart"]) == 1 and region_type == "terran_space"
                    else []
                ),
            )
            cluster_specs.append(cs)
            for sid in range(cs.sector_range_start, cs.sector_range_end + 1):
                cluster_by_sector_int[sid] = cs

        # Special locations — a sector→slug map so naming overrides apply.
        special_location_by_sector: Dict[int, str] = {}
        for sl in raw.get("specialLocations") or []:
            sid = int(sl["sectorId"])
            special_location_by_sector[sid] = str(sl["type"])

        fedspace_sectors = [int(s) for s in raw.get("fedspaceSectors") or []]
        fedspace_set = set(fedspace_sectors)

        # Sectors
        sector_specs: List[SectorSpec] = []
        station_specs: List[StationSpec] = []
        planet_specs: List[PlanetSpec] = []
        bang_sectors = raw.get("sectors") or {}
        for sid_str, sector_payload in bang_sectors.items():
            sid = int(sid_str)
            cluster_spec = cluster_by_sector_int.get(sid)
            if cluster_spec is None:
                raise ValueError(
                    f"{region_type} sector {sid} not covered by any cluster range"
                )

            sector_name = self._derive_sector_name(sid, special_location_by_sector)
            nebula = sector_payload.get("nebula")
            sector_type = SectorType.NEBULA if nebula else SectorType.STANDARD

            special_features: List[str] = []
            if sid in special_location_by_sector:
                special_features.append(
                    f"special_location:{special_location_by_sector[sid]}"
                )
            if nebula:
                special_features.append(f"nebula_type:{nebula.get('type', 'normal')}")
                special_features.append(
                    f"nebula_density:{int(nebula.get('density', 0))}"
                )
            if sid in fedspace_set:
                special_features.append("fedspace")

            security_level = (
                10 if sid in fedspace_set
                else _SECURITY_BY_CLUSTER_TYPE.get(cluster_spec.type, 5)
            )
            hazard_level = max(
                0, 10 - security_level
            )  # cheap default; bang.navHazards add to this later

            position = sector_payload.get("position", {})
            nav_hazards: Dict[str, Any] = (
                {"nebula_density": int(nebula["density"])} if nebula else {}
            )
            bang_hazards = sector_payload.get("navHazards") or []
            if bang_hazards:
                nav_hazards["hazards"] = list(bang_hazards)

            beacon = sector_payload.get("beacon")
            nav_beacons = [{"text": str(beacon)}] if beacon else []

            sector_specs.append(
                SectorSpec(
                    sector_id=sid,
                    name=sector_name,
                    region_int_id=0,  # filled at apply time via outer region_id
                    cluster_int_id=cluster_spec.cluster_int_id,
                    x_coord=int(position.get("x", 0)),
                    y_coord=int(position.get("y", 0)),
                    z_coord=int(position.get("z", 0)),
                    type=sector_type,
                    security_level=security_level,
                    hazard_level=hazard_level,
                    nav_hazards=nav_hazards,
                    nav_beacons=nav_beacons,
                    special_features=special_features,
                    is_discovered=bool(sector_payload.get("explored", False)),
                )
            )

            port = sector_payload.get("port")
            if port is not None:
                station_specs.append(self._build_station_spec(sid, port))

            for planet_payload in sector_payload.get("planets") or []:
                planet_specs.append(self._build_planet_spec(sid, planet_payload))

        # Warps
        warp_specs: List[WarpSpec] = []
        cluster_stability_by_int = {cs.cluster_int_id: cs.warp_stability for cs in cluster_specs}
        for w in raw.get("warps") or []:
            from_int = int(w["from"])
            to_int = int(w["to"])
            cluster = cluster_by_sector_int.get(from_int)
            warp_specs.append(
                WarpSpec(
                    from_sector_int=from_int,
                    to_sector_int=to_int,
                    is_bidirectional=not bool(w.get("oneWay", False)),
                    turn_cost=1,
                    warp_stability=cluster_stability_by_int.get(
                        cluster.cluster_int_id if cluster else 0, 1.0
                    ),
                )
            )

        # Formations
        formation_specs: List[FormationSpec] = []
        for f in raw.get("specialFormations") or []:
            formation_specs.append(
                FormationSpec(
                    formation_int_id=int(f["id"]),
                    type=str(f["type"]),
                    name=str(f.get("name", "")),
                    anchor_sector_int=int(f["anchorSectorId"]),
                    interior_sector_ints=[int(i) for i in f.get("interiorSectorIds") or []],
                    properties={
                        k: v
                        for k, v in (f.get("properties") or {}).items()
                        if k not in ("clusterId", "endpointClusterId")
                    },
                    is_discovered=bool(f.get("isDiscovered", False)),
                )
            )

        raw_rosters = list(raw.get("npcRosters") or [])

        return RegionInsertPlan(
            region_type=region_type,
            universe_seed=universe.seed,
            total_sectors=universe.total_sectors,
            clusters=cluster_specs,
            sectors=sector_specs,
            warps=warp_specs,
            stations=station_specs,
            planets=planet_specs,
            formations=formation_specs,
            fedspace_sector_ints=fedspace_sectors,
            special_location_by_sector=special_location_by_sector,
            raw_npc_rosters=raw_rosters,
            raw_universe=raw,
        )

    def _build_station_spec(self, sector_id: int, port: Dict[str, Any]) -> StationSpec:
        klass = int(port.get("class", 0))
        is_spacedock = bool(port.get("isSpaceDock", False))
        station_class = StationClass(klass) if klass in {c.value for c in StationClass} else StationClass.CLASS_0
        if is_spacedock:
            # Per the legacy SpaceDock recipe — full service hub flags.
            station_type = StationType.SHIPYARD
        else:
            station_type = _STATION_TYPE_BY_CLASS.get(klass, StationType.TRADING)

        commodities = _build_full_commodities(port.get("commodities") or {})
        services = _build_default_services(is_spacedock)
        return StationSpec(
            sector_int_id=sector_id,
            name=str(port.get("name", f"Station {sector_id}")),
            station_class=station_class,
            station_type=station_type,
            status=StationStatus.OPERATIONAL,
            commodities=commodities,
            services=services,
            is_spacedock=is_spacedock,
        )

    def _build_planet_spec(self, sector_id: int, p: Dict[str, Any]) -> PlanetSpec:
        bang_type = str(p.get("type", "barren"))
        planet_type = _PLANET_TYPE_MAP.get(bang_type, PlanetType.BARREN)
        hab = int(p.get("habitabilityScore", 0))
        citadel = p.get("citadel") or {}
        status = (
            PlanetStatus.COLONIZED
            if citadel
            else (
                PlanetStatus.HABITABLE if hab >= 40 else PlanetStatus.UNINHABITABLE
            )
        )
        owner_raw = p.get("owner")
        owner_uuid: Optional[uuid.UUID] = None
        if owner_raw:
            try:
                owner_uuid = uuid.UUID(str(owner_raw))
            except ValueError:
                # bang emits opaque strings (e.g., faction codes) until ADR-0069
                # roster materialization fully populates UUIDs. Leave owner_id
                # null and stash the original on Planet.economy via apply().
                owner_uuid = None
        return PlanetSpec(
            sector_int_id=sector_id,
            name=str(p.get("name", f"Planet {sector_id}")),
            planet_type=planet_type,
            status=status,
            owner_id=owner_uuid,
            habitability_score=hab,
            max_population=int(p.get("maxPopulation", 0)),
            max_colonists=int(p.get("maxColonists", 0)),
            population=0,
            fuel_ore=int(p.get("ore", 0)),
            organics=int(p.get("organics", 0)),
            equipment=int(p.get("equipment", 0)),
            colonists=int(p.get("colonists", 0)),
            citadel_level=int(citadel.get("level", 0)) if citadel else 0,
            citadel_drone_capacity=(
                int(citadel.get("droneCapacity", 0)) if citadel else 0
            ),
            citadel_safe_credits=(
                int(citadel.get("safeContents", 0)) if citadel else 0
            ),
        )

    @staticmethod
    def _derive_sector_name(
        sector_id: int, special_location_by_sector: Dict[int, str]
    ) -> str:
        slug = special_location_by_sector.get(sector_id)
        if slug == "terra":
            return "Terra"
        if slug == "stardock":
            return "Stardock"
        if slug == "rylan":
            return "Rylan"
        if slug == "alpha_centauri":
            return "Alpha Centauri"
        if slug == "fringe_homeworld":
            return "Fringe Homeworld"
        return f"Sector {sector_id}"

    def _apply_terran_space_invariants(
        self,
        plan: RegionInsertPlan,
        warnings: List[Dict[str, Any]],
    ) -> RegionInsertPlan:
        """Force the legacy starter-region invariants.

        Per the legacy GalaxyGenerator audit's "Top 3 risks":
        * Sector 1 must be safe (hazard=0, radiation=0, STANDARD type) and
          host the canonical "Earth Station" + "New Earth" planet (8 B pop).
        * Sector 10 must host a CLASS_11 Shipyard SpaceDock with the
          legacy service flags.
        """
        # Sector 1 — safe
        sector_1 = next((s for s in plan.sectors if s.sector_id == 1), None)
        if sector_1 is None:
            warnings.append(
                {
                    "category": "STARTER_INVARIANT",
                    "code": "INV-001",
                    "message": "terran_space Sector 1 missing; cannot enforce starter invariants",
                }
            )
            return plan
        sector_1.security_level = 10
        sector_1.hazard_level = 0
        sector_1.type = SectorType.STANDARD
        if "fedspace" not in sector_1.special_features:
            sector_1.special_features.append("fedspace")

        # Earth Station — drop any existing Sector-1 station, install canonical.
        plan.stations = [s for s in plan.stations if s.sector_int_id != 1]
        plan.stations.insert(
            0,
            StationSpec(
                sector_int_id=1,
                name="Earth Station",
                station_class=StationClass.CLASS_1,
                station_type=StationType.TRADING,
                status=StationStatus.OPERATIONAL,
                commodities=_build_full_commodities({}),
                services=_build_default_services(is_spacedock=False),
                is_spacedock=False,
                description="The canonical starter station for Terran Space.",
            ),
        )

        # New Earth — drop any Sector-1 planets, install canonical.
        plan.planets = [p for p in plan.planets if p.sector_int_id != 1]
        plan.planets.insert(
            0,
            PlanetSpec(
                sector_int_id=1,
                name="New Earth",
                planet_type=PlanetType.TERRAN,
                status=PlanetStatus.COLONIZED,
                owner_id=None,
                habitability_score=95,
                max_population=8_000_000_000,
                max_colonists=1000,
                population=8_000_000_000,
                fuel_ore=0,
                organics=0,
                equipment=0,
                colonists=0,
                citadel_level=0,
                citadel_drone_capacity=0,
                citadel_safe_credits=0,
            ),
        )

        # SpaceDock at sector 10 — replace whatever bang produced.
        plan.stations = [s for s in plan.stations if s.sector_int_id != 10]
        plan.stations.append(
            StationSpec(
                sector_int_id=10,
                name="Stardock",
                station_class=StationClass.CLASS_11,
                station_type=StationType.SHIPYARD,
                status=StationStatus.OPERATIONAL,
                commodities=_build_full_commodities({}),
                services={
                    "ship_dealer": True,
                    "ship_repair": True,
                    "ship_maintenance": True,
                    "ship_upgrades": True,
                    "insurance": True,
                    "drone_shop": True,
                    "genesis_dealer": True,
                    "mine_dealer": True,
                    "diplomatic_services": False,
                    "storage_rental": True,
                    "market_intelligence": True,
                    "refining_facility": True,
                    "luxury_amenities": False,
                },
                is_spacedock=True,
                description="Full-service Shipyard SpaceDock.",
            )
        )
        return plan

    # ----- job-table I/O --------------------------------------------------

    async def _set_job_status(
        self, session: AsyncSession, job_id: uuid.UUID, status: BangGenerationJobStatus
    ) -> None:
        job = await session.get(BangGenerationJob, job_id)
        if job is not None:
            job.status = status  # type: ignore[assignment]

    async def _mark_job_complete(
        self,
        session: AsyncSession,
        job_id: uuid.UUID,
        duration_ms: int,
        warnings: List[Dict[str, Any]],
    ) -> None:
        job = await session.get(BangGenerationJob, job_id)
        if job is None:
            return
        job.status = BangGenerationJobStatus.COMPLETE  # type: ignore[assignment]
        job.completed_at = datetime.now(timezone.utc)  # type: ignore[assignment]
        job.duration_ms = duration_ms  # type: ignore[assignment]
        job.warnings_json = warnings  # type: ignore[assignment]

    async def _mark_job_failed(
        self, session: AsyncSession, job_id: uuid.UUID, msg: str
    ) -> None:
        job = await session.get(BangGenerationJob, job_id)
        if job is None:
            return
        job.status = BangGenerationJobStatus.FAILED  # type: ignore[assignment]
        job.completed_at = datetime.now(timezone.utc)  # type: ignore[assignment]
        job.error_message = msg  # type: ignore[assignment]

    async def _append_log(
        self, session: AsyncSession, job_id: uuid.UUID, line: str
    ) -> None:
        await session.execute(
            text(
                "UPDATE bang_generation_jobs SET log_text = COALESCE(log_text,'') || :line WHERE id = :id"
            ),
            {"line": line, "id": job_id},
        )


# ---------------------------------------------------------------------------
# Module-level helpers (pure)
# ---------------------------------------------------------------------------


def _build_full_commodities(
    bang_commodities: Dict[str, Dict[str, Any]],
) -> Dict[str, Dict[str, Any]]:
    """Project bang's commodity payload onto the 9-commodity wire.

    Per ADR-0062 E-D1, every Station.commodities dict carries all 9 keys
    including ``precious_metals``. Bang may omit a key (only emitting
    commodities the station actively trades); we fill in the defaults so
    the gameserver Market service can rely on every key being present.
    """
    out: Dict[str, Dict[str, Any]] = {}
    for key in COMMODITY_WIRE_ORDER:
        defaults = _COMMODITY_DEFAULTS[key]
        bang_entry = bang_commodities.get(key) or {}
        action = str(bang_entry.get("action", "")).upper()
        buys = action == "B"
        sells = action == "S"
        out[key] = {
            "quantity": int(bang_entry.get("quantity", 0)),
            "capacity": int(bang_entry.get("capacity", defaults["capacity"])),
            "base_price": int(defaults["base_price"]),
            "current_price": int(defaults["base_price"]),
            "production_rate": int(
                bang_entry.get("regenRate", defaults["production_rate"])
            ),
            "price_variance": int(defaults["price_variance"]),
            "buys": buys,
            "sells": sells,
        }
    return out


def _build_default_services(is_spacedock: bool) -> Dict[str, Any]:
    """Return the default :attr:`Station.services` dict.

    SpaceDocks get the legacy full-service flag set so genesis_service /
    drone_service / terraforming_service keep functioning (the audit's
    "SpaceDock dependency for genesis / drones / mines" risk).
    """
    if is_spacedock:
        return {
            "ship_dealer": True,
            "ship_repair": True,
            "ship_maintenance": True,
            "ship_upgrades": True,
            "insurance": True,
            "drone_shop": True,
            "genesis_dealer": True,
            "mine_dealer": True,
            "diplomatic_services": False,
            "storage_rental": True,
            "market_intelligence": True,
            "refining_facility": True,
            "luxury_amenities": False,
        }
    return {
        "ship_dealer": False,
        "ship_repair": True,
        "ship_maintenance": True,
        "ship_upgrades": False,
        "insurance": False,
        "drone_shop": False,
        "genesis_dealer": False,
        "mine_dealer": False,
        "diplomatic_services": False,
        "storage_rental": False,
        "market_intelligence": False,
        "refining_facility": False,
        "luxury_amenities": False,
    }


def _coerce_formation_type(value: str) -> SpecialFormationType:
    """Return the SpecialFormationType matching ``value``.

    The Postgres enum is extended by the Job Model Author's
    ``bang_schema_decisions`` migration with ``LOST_SECTOR``,
    ``LOST_CLUSTER``, ``ARCHIPELAGO`` (per resolved decision Q6). At
    import time we widen any of those values to the SQLAlchemy enum even
    if it has not yet been re-imported; the migration guarantees the
    underlying Postgres enum knows them.
    """
    try:
        return SpecialFormationType[value]
    except KeyError as exc:
        # Build an enum-like sentinel via Enum value lookup
        for member in SpecialFormationType:
            if member.value == value:
                return member
        raise ValueError(
            f"Unknown SpecialFormationType {value!r}; migration may be missing"
        ) from exc


def _validate_universe_shape(payload: Dict[str, Any], *, region_type: RegionType) -> None:
    """Spot-check the bang JSON contract; raise on shape violations."""
    required = ("version", "seed", "totalSectors", "sectors", "warps")
    missing = [k for k in required if k not in payload]
    if missing:
        raise ValueError(
            f"bang Universe missing required keys for {region_type}: {missing}"
        )
    if not str(payload["version"]).startswith("1."):
        raise ValueError(
            f"bang version {payload['version']!r} not in supported 1.x line"
        )
    expected = _EXPECTED_SECTOR_COUNT.get(region_type)
    if expected is not None and int(payload["totalSectors"]) != expected:
        raise ValueError(
            f"{region_type} expected {expected} sectors; got {payload['totalSectors']}"
        )


__all__ = [
    "BangImportService",
    "InsertPlan",
    "ParsedUniverse",
    "RegionInsertPlan",
    "SectorSpec",
    "WarpSpec",
    "StationSpec",
    "PlanetSpec",
    "ClusterSpec",
    "FormationSpec",
    "ValidationReport",
    "GALAXY_GEN_LOCK_KEY",
    "COMMODITY_WIRE_ORDER",
]

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
import random
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple

import docker
from docker import errors as docker_errors
from requests.exceptions import ReadTimeout
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.market_bootstrap import build_market_prices
from src.core.station_class_map import apply_class_pattern
from src.models.bang_generation_job import (
    BangGenerationJob,
    BangGenerationJobStatus,
)
from src.models.cluster import Cluster, ClusterType
from src.models.galaxy import Galaxy, GalaxyImportState
from src.models.planet import Planet, PlanetStatus, PlanetType
from src.models.region import Region
from src.models.sector import Sector, SectorType, sector_warps
from src.models.special_formation import SpecialFormation, SpecialFormationType
from src.models.warp_tunnel import WarpTunnel, WarpTunnelType
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
#: :meth:`BangImportService._build_bang_args`. ``validator_strictness``
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
    sector_id: int  # GLOBAL: shifted by _offset_region_sector_ids
    # ADR-0005: region-LOCAL number, captured BEFORE the global offset is
    # applied. _offset_region_sector_ids deliberately never touches this, so it
    # stays 1..N per region and feeds the compound key + Sector.sector_number.
    sector_number: int
    # ADR-0005: marks this region's Capital Sector (welcome hub). True for the
    # offset-anchor capital (region-local sector 1) unless bang says otherwise.
    is_capital: bool
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
    # 'A' | 'B' | None — ADR-0041 Phase 10.5 TradeDock seeding
    tradedock_tier: Optional[str] = None


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
    # ADR-0005: region-LOCAL number of this region's Capital Sector. Read from
    # bang's `capitalSector` (default 1 — the offset-anchor capital).
    capital_sector_number: int
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
        docker_client: Optional["docker.DockerClient"] = None,
        log_sink: Optional[Callable[[str], Awaitable[None]]] = None,
    ) -> None:
        """Construct a translator.

        Args:
            bang_image: Pinned bang Docker image (``repo:tag``). Tests pass a
                no-op value because they short-circuit :meth:`invoke_bang`.
            docker_client: Override for the :class:`docker.DockerClient`
                used to spawn bang containers. Defaults to ``docker.from_env()``
                which reads ``DOCKER_HOST`` (the gameserver Dockerfile +
                compose pin this at ``unix:///var/run/docker.sock``). Tests
                inject a :class:`unittest.mock.MagicMock` exposing the
                ``containers.run`` → container → ``wait`` / ``logs`` chain.
            log_sink: Async callable that receives every stderr line emitted
                by bang. The orchestrator wires this to append to
                ``BangGenerationJob.log_text``.
        """
        self.bang_image = bang_image
        self._docker = docker_client or docker.from_env()
        self._log_sink = log_sink

    # ----- invocation -----------------------------------------------------

    def invoke_bang(
        self,
        config: BangConfig,
        timeout_seconds: int = 300,
    ) -> ParsedUniverse:
        """Spawn a bang container for one region; parse stdout JSON.

        Uses the docker-py SDK to start the pinned bang image with
        ``--json-out`` so stdout contains exactly the Universe JSON; stderr
        carries progress/warnings which we forward to ``self._log_sink``
        if set.

        Raises:
            RuntimeError: If the container exits non-zero, the JSON fails
                to parse, or the resulting Universe fails the shape check
                in :func:`_validate_universe_shape`.
        """
        bang_args = self._build_bang_args(config)
        logger.info("invoke_bang: image=%s args=%s", self.bang_image, bang_args)
        stdout, stderr = self._run_bang_container(bang_args, timeout_seconds, region_type=config.region_type)

        if stderr:
            self._forward_stderr(stderr)
        try:
            payload = json.loads(stdout)
        except json.JSONDecodeError as exc:
            raise RuntimeError(
                f"bang produced invalid JSON for region "
                f"{config.region_type}: {exc}"
            ) from exc

        _validate_universe_shape(payload, region_type=config.region_type)
        return ParsedUniverse(region_type=config.region_type, raw=payload)

    def validate_only(self, config: BangConfig) -> ValidationReport:
        """Run bang with ``--validate-only``; return stats + warnings inline."""
        bang_args = self._build_bang_args(config, validate_only=True)
        logger.info("validate_only: image=%s args=%s", self.bang_image, bang_args)
        stdout, stderr = self._run_bang_container(
            bang_args, timeout_seconds=120, region_type=config.region_type,
            allow_exit_codes=(0, 2),
        )
        try:
            payload = json.loads(stdout or "{}")
        except json.JSONDecodeError:
            payload = {}
        return ValidationReport(
            stats=payload.get("stats", {}),
            warnings=payload.get("warnings", []),
            validation=payload.get("validation", {}),
        )

    def _run_bang_container(
        self,
        bang_args: List[str],
        timeout_seconds: int,
        *,
        region_type: str,
        allow_exit_codes: Tuple[int, ...] = (0,),
    ) -> Tuple[str, str]:
        """Spawn one bang container via docker-py, return (stdout, stderr).

        Replaces the prior ``subprocess.run(['docker','run',...])`` shell-out.
        We removed ``docker-ce-cli`` from the gameserver image to drop ~16
        critical and ~11 high CVEs that ship bundled in the CLI's Go vendor
        tree (golang.org/x/crypto, golang.org/x/net, github.com/docker/*).

        Lifecycle:
          1. ``containers.run(image, command=bang_args, detach=True)`` —
             returns immediately with a Container handle.
          2. ``container.wait(timeout=N)`` blocks for exit; ``ReadTimeout``
             surfaces on overrun.
          3. ``container.logs(stdout=…, stderr=…)`` reads the buffered
             output AFTER exit. (bang invocations are short; the prior
             subprocess.run also buffered everything in capture_output mode,
             so we are not losing real-time stderr behaviour.)
          4. ``container.remove(force=True)`` ensures cleanup even on the
             error paths — mirrors the prior ``--rm`` flag semantics.
        """
        container = None
        try:
            try:
                container = self._docker.containers.run(
                    self.bang_image,
                    command=bang_args,
                    detach=True,
                    stdout=True,
                    stderr=True,
                    stdin_open=True,
                )
            except docker_errors.ImageNotFound as exc:
                raise RuntimeError(
                    f"bang image not found: {self.bang_image}"
                ) from exc
            except docker_errors.APIError as exc:
                raise RuntimeError(
                    f"docker API error starting bang for {region_type}: {exc}"
                ) from exc

            try:
                result = container.wait(timeout=timeout_seconds)
            except ReadTimeout as exc:
                # docker-py surfaces a requests.ReadTimeout on container.wait
                # overrun. Kill the container so it doesn't dangle, then
                # surface the same error shape the subprocess.TimeoutExpired
                # path used to raise.
                try:
                    container.kill()
                except Exception:  # pragma: no cover - best effort
                    logger.warning("failed to kill timed-out bang container", exc_info=True)
                raise RuntimeError(
                    f"bang timed out after {timeout_seconds}s for region {region_type}"
                ) from exc

            status_code = int(result.get("StatusCode", -1))
            stdout = container.logs(stdout=True, stderr=False).decode("utf-8", errors="replace")
            stderr = container.logs(stdout=False, stderr=True).decode("utf-8", errors="replace")

            if status_code not in allow_exit_codes:
                raise RuntimeError(
                    f"bang exited {status_code} for region "
                    f"{region_type}: {stderr[-2000:]}"
                )
            return stdout, stderr
        finally:
            if container is not None:
                try:
                    container.remove(v=True, force=True)
                except Exception:  # pragma: no cover - best effort cleanup
                    logger.warning("failed to remove bang container", exc_info=True)

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

        # bang emits each region's sector IDs starting at 1, but sectors.sector_id
        # is globally unique in the gameserver schema. Offset each region's
        # sector-id space so the three regions occupy disjoint ranges.
        # Iterate in CANONICAL order — terran_space first so its local
        # sector 1 (Sol / Earth Station, enforced by _apply_terran_space_
        # invariants) lands at global sector_id 1. The docs are canon:
        #   DOCS/API/v1/player.aispec
        #       "Initial player spawn: Sector 1 of Terran Space"
        #   DOCS/API/v1/sectors-planets.aispec
        #       "fixed at sector 1 in Terran Space"
        # Invariants run BEFORE offsetting so they still match bang's
        # local numbering, then we shift the whole region together.
        REGION_ORDER: Tuple[RegionType, ...] = (
            "terran_space", "player_owned", "central_nexus",
        )
        running_offset = 0
        for region_type in REGION_ORDER:
            universe = universes.get(region_type)
            if universe is None:
                continue
            plan = self._translate_region(region_type, universe)
            if region_type == "terran_space":
                # Enforce the legacy starter-region invariants per the
                # GalaxyGenerator audit's "Top 3 risks".
                plan = self._apply_terran_space_invariants(plan, warnings)
            # ADR-0041 Phase 10.5: seed TradeDocks per region quota
            plan = self._apply_tradedock_seeding(region_type, plan, warnings)
            if running_offset:
                self._offset_region_sector_ids(plan, running_offset)
            per_region[region_type] = plan
            running_offset += plan.total_sectors

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

    @staticmethod
    def _offset_region_sector_ids(plan: RegionInsertPlan, offset: int) -> None:
        """Shift every sector-id reference in ``plan`` by ``offset``.

        sectors.sector_id is globally unique in the gameserver schema, but
        bang emits each region's sectors starting at 1. translate() calls
        this between regions so the three regions occupy disjoint ranges.
        Mutates in place; touches every sector-id slot on the plan
        (SectorSpec, WarpSpec, StationSpec, PlanetSpec, FormationSpec,
        fedspace list, special_location map).
        """
        if offset <= 0:
            return
        for s in plan.sectors:
            # ADR-0005: shift sector_id (global) ONLY. sector_number stays
            # region-local and is_capital stays as marked — both must survive
            # the offset untouched so the compound key remains region-scoped.
            s.sector_id += offset
        for w in plan.warps:
            w.from_sector_int += offset
            w.to_sector_int += offset
        for st in plan.stations:
            st.sector_int_id += offset
        for p in plan.planets:
            p.sector_int_id += offset
        for f in plan.formations:
            f.anchor_sector_int += offset
            f.interior_sector_ints = [i + offset for i in f.interior_sector_ints]
        plan.fedspace_sector_ints = [i + offset for i in plan.fedspace_sector_ints]
        plan.special_location_by_sector = {
            (k + offset): v for k, v in plan.special_location_by_sector.items()
        }

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

        gate_sector_by_region: Dict[RegionType, uuid.UUID] = {}
        for region_type, region_plan in plan.regions.items():
            region_id = region_ids.get(region_type)
            if region_id is None:
                raise ValueError(
                    f"apply() missing region_id for {region_type} — orchestrator "
                    "must pre-create the Region row and pass its UUID via "
                    "InsertPlan.bang_snapshot['regions'][region_type]['region_id']"
                )
            gate_sector_by_region[region_type] = await self._apply_region(
                session, region_plan, region_id
            )

        # Inter-region warp gates. Bang only generates the in-region
        # sector adjacency graph (sector_warps); to actually make the
        # galaxy navigable end-to-end we wire the three regions through
        # central_nexus with NATURAL warp tunnels here. See
        # DOCS/PLANS/bang-integration-schema-map.md — "Cross-region warp
        # gates remain gameserver-managed and go into warp_tunnels".
        nexus_gate = gate_sector_by_region.get("central_nexus")
        if nexus_gate is not None:
            for spoke_rt in ("player_owned", "terran_space"):
                spoke_gate = gate_sector_by_region.get(spoke_rt)
                if spoke_gate is None:
                    continue
                session.add(WarpTunnel(
                    name=f"{spoke_rt.replace('_', ' ').title()} ↔ Central Nexus",
                    origin_sector_id=spoke_gate,
                    destination_sector_id=nexus_gate,
                    type=WarpTunnelType.NATURAL,
                    is_bidirectional=True,
                    description=f"Auto-generated gate linking {spoke_rt} to central_nexus.",
                ))

        # Final state flip lives on the same transaction as the inserts so
        # there is no observable partial state.
        galaxy.import_state = GalaxyImportState.READY  # type: ignore[assignment]
        await session.flush()
        return galaxy

    async def apply_additional_region(
        self,
        galaxy_id: uuid.UUID,
        region_plan: RegionInsertPlan,
        region_id: uuid.UUID,
        session: AsyncSession,
    ) -> uuid.UUID:
        """Splice a single freshly-generated region into an existing galaxy.

        Companion to :meth:`apply`. Used by the "Add Player-Owned Region"
        admin flow when an operator wants to grow an existing galaxy
        instead of regenerating it from scratch. The new region's clusters
        and sectors are appended to the galaxy's sector-id keyspace by
        the orchestrator (which pre-offsets the spec list past the current
        max sector_id), and one fresh NATURAL warp tunnel is wired
        between the new region's gate sector and the existing
        central_nexus gate so the addition is reachable end-to-end.

        Returns the gate sector UUID of the new region (mirrors the
        return-shape contract of :meth:`_apply_region` for symmetry).
        """
        galaxy = await session.get(Galaxy, galaxy_id)
        if galaxy is None:
            raise ValueError(f"apply_additional_region: galaxy {galaxy_id} not found")

        # Look up central_nexus's gate sector — the lowest-numbered sector
        # within central_nexus, per the same convention apply() uses when
        # picking gates. The galaxy MUST already have a central_nexus
        # region for inter-region routing to make sense; if it does not,
        # we still write the region but skip the tunnel and emit a
        # warning so the operator can see what happened.
        nexus_gate_row = (
            await session.execute(
                text(
                    "SELECT s.id FROM sectors s "
                    "JOIN regions r ON s.region_id = r.id "
                    "WHERE r.region_type = 'central_nexus' "
                    "ORDER BY s.sector_id ASC LIMIT 1"
                )
            )
        ).first()
        nexus_gate_id: Optional[uuid.UUID] = nexus_gate_row[0] if nexus_gate_row else None

        # Write the region's content. _apply_region already returns the
        # region's gate sector UUID (lowest sector_id in its offset range).
        new_gate = await self._apply_region(session, region_plan, region_id)

        # Inter-region tunnel: new region ↔ central_nexus. Mirrors the
        # hub-and-spoke pattern apply() uses on full generation.
        if nexus_gate_id is not None:
            session.add(WarpTunnel(
                name="Player Owned ↔ Central Nexus",
                origin_sector_id=new_gate,
                destination_sector_id=nexus_gate_id,
                type=WarpTunnelType.NATURAL,
                is_bidirectional=True,
                description="Auto-generated gate linking new player_owned region to central_nexus.",
            ))

        # Track the new region in bang_snapshot.additional_regions so the
        # wipe endpoint can find it (the existing bang_snapshot.regions
        # dict is keyed by region_type and would collide on player_owned).
        snapshot = dict(galaxy.bang_snapshot or {})  # copy to mark dirty for SQLA JSON change-detection
        additional = list(snapshot.get("additional_regions") or [])
        additional.append({
            "region_id": str(region_id),
            "region_type": region_plan.region_type,
            "total_sectors": region_plan.total_sectors,
        })
        snapshot["additional_regions"] = additional
        galaxy.bang_snapshot = snapshot  # type: ignore[assignment]

        await session.flush()
        return new_gate

    async def run_add_region_job(
        self,
        job_id: uuid.UUID,
        galaxy_id: uuid.UUID,
        params: BangConfig,
        *,
        region_metadata: Optional[Dict[str, Any]] = None,
        session_factory: Optional[Callable[[], AsyncSession]] = None,
        emit_event: Optional[Callable[[str, Dict[str, Any]], Awaitable[None]]] = None,
    ) -> None:
        """Orchestrate an additive player_owned region for an existing galaxy.

        Mirrors :meth:`run_generation_job` but runs bang only ONCE (the
        new region) and writes via :meth:`apply_additional_region`. The
        orchestrator computes the global sector-id offset by reading the
        current max(sector_id) on the galaxy, so the new region's
        sector_ids start past the existing keyspace and don't collide.

        ``region_metadata['regions']['player_owned']['region_id']`` must
        contain the UUID of the pre-created Region row (created by the
        route handler in the same transaction as the BangGenerationJob).
        """
        if session_factory is None:
            from src.core.database import AsyncSessionLocal as _Session  # type: ignore[import-not-found]
            session_factory = _Session

        region_metadata = dict(region_metadata or {})
        region_metadata.setdefault("master_seed", params.seed)

        start_ts = time.monotonic()

        async with session_factory() as session:
            locked = (
                await session.execute(
                    text("SELECT pg_try_advisory_lock(:k)"),
                    {"k": GALAXY_GEN_LOCK_KEY},
                )
            ).scalar()
            if not locked:
                await self._mark_job_failed(
                    session, job_id, "another galaxy-generation job is already running"
                )
                await session.commit()
                return

            try:
                await self._set_job_status(session, job_id, BangGenerationJobStatus.RUNNING)
                await session.commit()

                # Compute the offset so the new region's sector_ids land
                # past the existing galaxy's max(sector_id). +1 keeps the
                # range disjoint with zero overlap.
                current_max_row = (
                    await session.execute(
                        text("SELECT COALESCE(MAX(sector_id), 0) FROM sectors")
                    )
                ).scalar()
                sector_id_offset = int(current_max_row or 0)

                # Run bang once for player_owned. We do NOT reuse
                # invoke_bang's offsetting; the orchestrator-supplied
                # offset is applied after _translate_region returns so
                # the per-region invariants run on bang's local
                # numbering first (consistent with translate()).
                sub_config = BangConfig(
                    seed=params.seed,
                    sectors=params.sectors,
                    region_type="player_owned",
                )
                parsed = await asyncio.to_thread(self.invoke_bang, sub_config, 300)
                await self._append_log(
                    session, job_id,
                    f"[player_owned +offset={sector_id_offset}] parsed {parsed.total_sectors} sectors\n",
                )
                await session.commit()

                # Translate ONE region.
                region_plan = self._translate_region("player_owned", parsed)
                if sector_id_offset > 0:
                    self._offset_region_sector_ids(region_plan, sector_id_offset)

                region_id_str = (
                    region_metadata.get("regions", {})
                    .get("player_owned", {})
                    .get("region_id")
                )
                if region_id_str is None:
                    raise ValueError(
                        "run_add_region_job: region_metadata missing "
                        "regions.player_owned.region_id"
                    )
                region_id = (
                    region_id_str if isinstance(region_id_str, uuid.UUID)
                    else uuid.UUID(str(region_id_str))
                )

                async with session.begin():
                    await self.apply_additional_region(
                        galaxy_id, region_plan, region_id, session
                    )

                duration_ms = int((time.monotonic() - start_ts) * 1000)
                await self._mark_job_complete(
                    session, job_id, duration_ms, []
                )
                await session.commit()

                # ADR-0069 Phase 12.5c — seed NPCs for the freshly-added
                # region after its content commits. The add-region snapshot is
                # tracked under bang_snapshot.additional_regions WITHOUT a
                # universe blob, so it carries no bang rosters; bootstrap still
                # seeds the topology-derived trader roster for the new region.
                await self._bootstrap_regions_post_commit([region_id])

                if emit_event is not None:
                    await emit_event(
                        "galaxy.region_added",
                        {"galaxy_id": str(galaxy_id), "job_id": str(job_id), "region_id": str(region_id)},
                    )
            except Exception as exc:
                logger.error("run_add_region_job failed: %s", exc, exc_info=True)
                await self._mark_job_failed(session, job_id, str(exc))
                await session.commit()
            finally:
                await session.execute(
                    text("SELECT pg_advisory_unlock(:k)"),
                    {"k": GALAXY_GEN_LOCK_KEY},
                )
                await session.commit()

    # ----- content-scoped wipe + regeneration ----------------------------
    #
    # Per ADR-0005, a region's *identity* is stable across regenerations: a
    # `force` regen wipes only CONTENT (clusters, sectors, warps, stations,
    # market_prices, planets, special_formations) and re-imports into the
    # SAME Region row, preserving the Region UUID and every operator/
    # customer-bound field (owner_id, paypal_subscription_id,
    # subscription_status, governance_type, tax_rate, name/display_name,
    # cultural identity, treasury, …). Destroying and re-creating the Region
    # row — the legacy behaviour — would orphan paid subscriptions and
    # governance state, so the regen path NEVER deletes the regions row.

    #: Region columns that are operator/customer-bound *identity* and must
    #: survive a content-only regeneration untouched. Documented here as the
    #: single source of truth for the regen contract (ADR-0005). `total_sectors`
    #: is intentionally NOT in this set: it is a content-derived count and is
    #: refreshed to match the freshly imported region.
    REGION_IDENTITY_COLUMNS: Tuple[str, ...] = (
        "id",
        "name",
        "display_name",
        "region_type",
        "owner_id",
        "subscription_tier",
        "paypal_subscription_id",
        "subscription_status",
        "subscription_started_at",
        "subscription_expires_at",
        "last_payment_at",
        "next_billing_at",
        "status",
        "created_at",
        "governance_type",
        "voting_threshold",
        "election_frequency_days",
        "constitutional_text",
        "tax_rate",
        "trade_bonuses",
        "economic_specialization",
        "starting_credits",
        "starting_ship",
        "language_pack",
        "aesthetic_theme",
        "traditions",
        "social_hierarchy",
        "treasury_balance",
    )

    async def wipe_region_content(
        self, session: AsyncSession, region_id: uuid.UUID
    ) -> None:
        """Delete every CONTENT row owned by ``region_id`` — keep the Region.

        Tears down clusters / sectors / warps / stations / market_prices /
        planets / special_formations for a single region while leaving the
        ``regions`` row (and its operator/customer-bound identity columns)
        completely untouched.

        Ordering mirrors the hard-delete galaxy path's FK dance, but scoped
        to one region and stopping short of the Region row:

        1. ``special_formations`` first — ``anchor_sector_id`` is
           ``ON DELETE RESTRICT`` against ``sectors``, so they must go before
           their anchor sectors.
        2. ``sectors`` next — ``ON DELETE CASCADE`` on ``sectors.id`` reaches
           ``sector_warps`` (both endpoints), ``warp_tunnels`` (both
           endpoints), ``stations`` (→ ``market_prices`` / ``price_history`` /
           ``price_alerts`` via their own CASCADE), and ``planets``.
        3. ``clusters`` last — sectors are already gone, so the cluster→sector
           CASCADE is a no-op; we delete them explicitly because the regen
           keeps the parent Region (cluster rows would otherwise survive and
           accumulate on every regeneration).

        Idempotent: deleting from an already-empty region is a no-op, so a
        retried / partial regen is safe to re-run.
        """
        await session.execute(
            text("DELETE FROM special_formations WHERE region_id = :rid"),
            {"rid": region_id},
        )
        await session.execute(
            text("DELETE FROM sectors WHERE region_id = :rid"),
            {"rid": region_id},
        )
        await session.execute(
            text("DELETE FROM clusters WHERE region_id = :rid"),
            {"rid": region_id},
        )

    async def apply_regeneration(
        self,
        galaxy_id: uuid.UUID,
        plan: InsertPlan,
        existing_region_ids: Dict[RegionType, uuid.UUID],
        session: AsyncSession,
    ) -> Galaxy:
        """Re-import a freshly translated :class:`InsertPlan` into an EXISTING
        galaxy + EXISTING regions, preserving region identity.

        Companion to :meth:`apply` (which always creates fresh Galaxy/Region
        identities). The regen path instead:

        * Reuses the existing :class:`Galaxy` row (refreshing its bang_*
          provenance + snapshot in place — no new Galaxy id).
        * Wipes each target region's CONTENT via :meth:`wipe_region_content`.
        * Re-writes content into the SAME region ids; the ``regions`` rows
          (and their identity columns) are never deleted.
        * Refreshes each region's ``total_sectors`` to match the new content
          (a content-derived count, not identity).

        The caller owns the transaction (see :meth:`run_regeneration_job`).
        """
        galaxy = await session.get(Galaxy, galaxy_id)
        if galaxy is None:
            raise ValueError(f"apply_regeneration: galaxy {galaxy_id} not found")

        galaxy.import_state = GalaxyImportState.GENERATING  # type: ignore[assignment]
        await session.flush()

        # Wipe content for every region we are about to re-import. Wipe ALL
        # first, THEN re-import — bang re-emits the same global sector_id
        # space (offsets are recomputed deterministically by translate()),
        # so a region's new sectors can collide with another region's old
        # sectors if we interleave wipe/import per-region.
        for region_type in plan.regions:
            region_id = existing_region_ids.get(region_type)
            if region_id is None:
                raise ValueError(
                    f"apply_regeneration: missing existing region_id for "
                    f"{region_type}; cannot preserve region identity"
                )
            await self.wipe_region_content(session, region_id)
        await session.flush()

        gate_sector_by_region: Dict[RegionType, uuid.UUID] = {}
        for region_type, region_plan in plan.regions.items():
            region_id = existing_region_ids[region_type]
            gate_sector_by_region[region_type] = await self._apply_region(
                session, region_plan, region_id
            )
            # total_sectors is content-derived; refresh it to match the new
            # import. Constrained by valid_region_type_sector_count, which
            # the bang config already honours for each region type.
            region_row = await session.get(Region, region_id)
            if region_row is not None:
                region_row.total_sectors = region_plan.total_sectors  # type: ignore[assignment]

        # Re-wire inter-region NATURAL warp gates (the old ones cascaded away
        # with the wiped sectors). Mirrors apply()'s hub-and-spoke pattern.
        nexus_gate = gate_sector_by_region.get("central_nexus")
        if nexus_gate is not None:
            for spoke_rt in ("player_owned", "terran_space"):
                spoke_gate = gate_sector_by_region.get(spoke_rt)
                if spoke_gate is None:
                    continue
                session.add(WarpTunnel(
                    name=f"{spoke_rt.replace('_', ' ').title()} ↔ Central Nexus",
                    origin_sector_id=spoke_gate,
                    destination_sector_id=nexus_gate,
                    type=WarpTunnelType.NATURAL,
                    is_bidirectional=True,
                    description=f"Auto-generated gate linking {spoke_rt} to central_nexus.",
                ))

        # Refresh galaxy provenance in place — same Galaxy id, new snapshot.
        galaxy.bang_version = plan.bang_version  # type: ignore[assignment]
        galaxy.bang_seed = plan.bang_seed  # type: ignore[assignment]
        galaxy.bang_config_hash = plan.bang_config_hash  # type: ignore[assignment]
        galaxy.bang_snapshot = plan.bang_snapshot  # type: ignore[assignment]
        galaxy.generation_warnings = plan.generation_warnings  # type: ignore[assignment]
        if plan.galaxy_name:
            galaxy.name = plan.galaxy_name  # type: ignore[assignment]
        galaxy.import_state = GalaxyImportState.READY  # type: ignore[assignment]
        await session.flush()
        return galaxy

    async def run_regeneration_job(
        self,
        job_id: uuid.UUID,
        galaxy_id: uuid.UUID,
        params: BangConfig,
        existing_region_ids: Dict[RegionType, uuid.UUID],
        *,
        session_factory: Optional[Callable[[], Any]] = None,
        region_metadata: Optional[Dict[str, Any]] = None,
        emit_event: Optional[Callable[[str, Dict[str, Any]], Awaitable[None]]] = None,
    ) -> None:
        """End-to-end content-only regeneration of an EXISTING galaxy.

        Mirrors :meth:`run_generation_job` but targets pre-existing
        Galaxy + Region rows: bang × 3 → translate (re-using the existing
        region ids so the snapshot points back at them) → wipe content +
        re-import via :meth:`apply_regeneration`. Region identity is
        preserved throughout.

        ``existing_region_ids`` maps each region_type to the UUID of the
        already-persisted Region row to re-import into; the route handler
        resolves these from the galaxy's ``bang_snapshot`` before dispatch.
        """
        if session_factory is None:
            from src.core.database import AsyncSessionLocal as _Session  # type: ignore[import-not-found]
            session_factory = _Session

        region_metadata = dict(region_metadata or {})
        region_metadata.setdefault("master_seed", params.seed)
        # Thread the EXISTING region ids into translate()'s snapshot so the
        # re-import wires content back to the same Region rows.
        region_metadata.setdefault(
            "regions",
            {rt: {"region_id": str(rid)} for rt, rid in existing_region_ids.items()},
        )

        start_ts = time.monotonic()

        async with session_factory() as session:
            locked = (
                await session.execute(
                    text("SELECT pg_try_advisory_lock(:k)"),
                    {"k": GALAXY_GEN_LOCK_KEY},
                )
            ).scalar()
            if not locked:
                await self._mark_job_failed(
                    session, job_id,
                    "another galaxy-generation job is already running",
                )
                await session.commit()
                return

            try:
                await self._set_job_status(session, job_id, BangGenerationJobStatus.RUNNING)
                await session.commit()

                universes: Dict[RegionType, ParsedUniverse] = {}
                region_types: Tuple[RegionType, ...] = (
                    "player_owned",
                    "terran_space",
                    "central_nexus",
                )
                # Only regenerate the region types we actually have an
                # existing Region row for (defensive: a galaxy might pre-date
                # one of the three region types).
                region_types = tuple(
                    rt for rt in region_types if rt in existing_region_ids
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
                    parsed = await asyncio.to_thread(self.invoke_bang, sub_config, 300)
                    universes[region_type] = parsed
                    await self._append_log(
                        session, job_id,
                        f"[regen {region_type}] parsed {parsed.total_sectors} sectors\n",
                    )
                    await session.commit()

                plan = self.translate(universes, region_metadata)

                async with session.begin():
                    galaxy = await self.apply_regeneration(
                        galaxy_id, plan, existing_region_ids, session
                    )

                duration_ms = int((time.monotonic() - start_ts) * 1000)
                await self._mark_job_complete(
                    session, job_id, duration_ms, plan.generation_warnings
                )
                await session.commit()

                # ADR-0069 Phase 12.5c — re-seed initial NPCs from the
                # re-materialized rosters after the regen transaction commits.
                # wipe_region_content does NOT delete NPCRoster/NPCCharacter
                # rows (content-only), so the idempotent bootstrap reconciles:
                # surviving rosters no-op, any new ones from the fresh snapshot
                # are seeded.
                await self._bootstrap_regions_post_commit(
                    self._imported_region_ids(plan)
                )

                if emit_event is not None:
                    await emit_event(
                        "galaxy.regenerated",
                        {"galaxy_id": str(galaxy.id), "job_id": str(job_id)},
                    )
            except Exception as exc:  # pragma: no cover - integration path
                logger.exception("run_regeneration_job failed: %s", exc)
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

    async def _apply_region(
        self,
        session: AsyncSession,
        region_plan: RegionInsertPlan,
        region_id: uuid.UUID,
    ) -> uuid.UUID:
        """Write one region's clusters, sectors, warps, stations, planets, formations.

        Returns the UUID of the region's gate sector — the first sector in
        the region's offset id range. apply() uses this to wire up
        inter-region WarpTunnel rows after every region is in place.
        """
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
                # ADR-0005: region-LOCAL number (was erroneously the global
                # sector_id). Drives uq_sectors_region_sector_number.
                sector_number=ss.sector_number,
                is_capital=ss.is_capital,
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

        created_stations: List[Station] = []
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
                tradedock_tier=stsp.tradedock_tier,
                description=stsp.description,
            )
            session.add(station)
            created_stations.append(station)

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

        # MarketPrice rows — same transaction. The trading endpoint reads
        # from the market_prices table, not the commodities JSONB, so a
        # station without rows is invisible to trade. PKs are assigned by
        # the flush above. apply_additional_region inherits this via
        # _apply_region.
        for station in created_stations:
            for market_price in build_market_prices(
                station.id, station.commodities
            ):
                session.add(market_price)

        # ADR-0005: stamp the region's Capital Sector number (region-local).
        # Runs in every apply path (full gen, regen, additional region) because
        # they all route through _apply_region. Default 1 (offset-anchor capital)
        # when the plan lacks an explicit value.
        region_row = await session.get(Region, region_id)
        if region_row is not None:
            region_row.capital_sector_number = (  # type: ignore[assignment]
                region_plan.capital_sector_number
            )

        # Gate sector = the lowest-numbered sector in this region's offset
        # range. apply() uses it as the inter-region warp tunnel endpoint.
        gate_sector_int = min(sector_uuid_by_int)
        return sector_uuid_by_int[gate_sector_int]

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
                    # Commit progress so the SSE stream surfaces the line
                    # immediately and so the next iteration's auto-begun
                    # transaction starts clean.
                    await session.commit()

                plan = self.translate(universes, region_metadata)

                async with session.begin():
                    galaxy = await self.apply(plan, session)

                duration_ms = int((time.monotonic() - start_ts) * 1000)
                await self._mark_job_complete(
                    session, job_id, duration_ms, plan.generation_warnings
                )
                await session.commit()

                # ADR-0069 Phase 12.5c — seed initial NPCs from the rosters
                # bang materialized, now that the import has committed and the
                # sectors are visible to a fresh session. Best-effort.
                await self._bootstrap_regions_post_commit(
                    self._imported_region_ids(plan)
                )

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

    @staticmethod
    def _imported_region_ids(plan: InsertPlan) -> List[uuid.UUID]:
        """Resolve the region UUIDs an :class:`InsertPlan` writes to.

        Mirrors :meth:`apply`'s resolution: the orchestrator stashes each
        pre-created Region UUID in
        ``plan.bang_snapshot['regions'][region_type]['region_id']``. Returns
        them de-duplicated, in plan order, skipping any region that lacks a
        resolvable id (defensive — ``apply`` would already have raised).
        """
        seen: set = set()
        ordered: List[uuid.UUID] = []
        snapshot_regions = (plan.bang_snapshot or {}).get("regions", {})
        for rt in plan.regions:
            region_snapshot = snapshot_regions.get(rt)
            rid = (
                region_snapshot.get("region_id")
                if isinstance(region_snapshot, dict)
                else None
            )
            if rid is None:
                continue
            rid_uuid = rid if isinstance(rid, uuid.UUID) else uuid.UUID(str(rid))
            if rid_uuid not in seen:
                seen.add(rid_uuid)
                ordered.append(rid_uuid)
        return ordered

    @staticmethod
    async def _bootstrap_regions_post_commit(
        region_ids: List[uuid.UUID],
    ) -> None:
        """ADR-0069 Phase 12.5c: seed initial NPCs from the rosters that were
        just materialized, AFTER the import transaction has committed.

        Runs ``npc_scheduler_service.bootstrap_region_sync`` once per imported
        region. It must run post-commit (not inside ``apply``'s transaction)
        for two reasons:

        * The bootstrap reads from the live ``sectors`` table to derive each
          roster's global host-sector offset (``_region_offset_map``); those
          rows are only visible to a fresh session after the import commits.
        * A roster-spawn failure must not roll back a successful region import
          — the rosters are persisted, so the next scheduler boot (or a manual
          re-run) re-attempts the spawn idempotently.

        Best-effort: every step it calls is idempotent by ``bang_roster_ref``,
        and a failure here is logged, never raised — the import is already
        durable. Bootstrapping one region of a galaxy bootstraps the whole
        galaxy (the underlying steps are galaxy-scoped + idempotent), so for
        a full 3-region generation only the FIRST distinct region needs a
        call; the rest no-op. We still iterate so the regen/add-region paths
        (which may target a subset) are covered uniformly.
        """
        # Lazy import: the scheduler service imports heavy NPC machinery and
        # is only needed on the post-import path, not for translate-only tests.
        from src.services.npc_scheduler_service import bootstrap_region_sync

        for region_id in region_ids:
            try:
                stats = await asyncio.to_thread(bootstrap_region_sync, region_id)
                logger.info(
                    "post-import NPC bootstrap for region %s: %s",
                    region_id, stats,
                )
            except Exception:  # pragma: no cover - best-effort, non-fatal
                logger.exception(
                    "post-import NPC bootstrap failed for region %s "
                    "(rosters persisted; scheduler boot will retry)",
                    region_id,
                )

    def _build_bang_args(
        self, config: BangConfig, *, validate_only: bool = False
    ) -> List[str]:
        """Build the bang CLI argv (no docker prefix).

        Returned list is passed verbatim as the ``command`` kwarg to
        ``containers.run`` — docker-py prepends the image's ENTRYPOINT
        (bang v1.3.1+ defaults to ``node /app/dist/cli.js``).

        BangConfig fields are snake_case (Python convention); bang's CLI
        flags are kebab-case. Translation table lives in ``_CLI_FLAG_MAP``;
        omitted fields fall back to bang's defaults (per ADR-0069's
        "GenerationRequest is optional" contract).
        """
        cmd: List[str] = [
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

        # ADR-0005: region-local Capital Sector number. bang reports it via
        # `capitalSector` (schema 1.3.4); default to 1 — the offset-anchor
        # capital — when bang omits the key. This is region-LOCAL: it must be
        # captured here, BEFORE _offset_region_sector_ids shifts sector_id.
        capital_sector_number = int(raw.get("capitalSector", 1) or 1)

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

            # ADR-0005: `sid` here is region-LOCAL (offsetting happens later in
            # _offset_region_sector_ids and only touches sector_id). Capture it
            # as sector_number so the region-local number survives the offset.
            # Capital marker: prefer bang's per-sector `isCapital` (schema
            # 1.3.4); fall back to "local sector == capital_sector_number" so
            # exactly the offset-anchor capital is flagged when bang omits it.
            is_capital = bool(
                sector_payload.get("isCapital", sid == capital_sector_number)
            )

            sector_specs.append(
                SectorSpec(
                    sector_id=sid,
                    sector_number=sid,
                    is_capital=is_capital,
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
                station_specs.append(
                    self._build_station_spec(sid, port, universe.seed)
                )

            for planet_payload in sector_payload.get("planets") or []:
                planet_specs.append(self._build_planet_spec(sid, planet_payload))

        # Warps. Defensively dedupe on the (from, to) pair: sector_warps has
        # composite pkey (source_sector_id, destination_sector_id) and bang
        # has been observed emitting the same directed edge more than once
        # in a single Universe (presumed bug upstream), which would crash
        # apply() with a UniqueViolationError on the second insert.
        warp_specs: List[WarpSpec] = []
        seen_warp_pairs: set = set()
        cluster_stability_by_int = {cs.cluster_int_id: cs.warp_stability for cs in cluster_specs}
        for w in raw.get("warps") or []:
            from_int = int(w["from"])
            to_int = int(w["to"])
            pair = (from_int, to_int)
            if pair in seen_warp_pairs:
                continue
            seen_warp_pairs.add(pair)
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
            capital_sector_number=capital_sector_number,
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

    def _build_station_spec(
        self, sector_id: int, port: Dict[str, Any], universe_seed: int
    ) -> StationSpec:
        klass = int(port.get("class", 0))
        is_spacedock = bool(port.get("isSpaceDock", False))
        station_class = StationClass(klass) if klass in {c.value for c in StationClass} else StationClass.CLASS_0
        if is_spacedock:
            # Per the legacy SpaceDock recipe — full service hub flags.
            station_type = StationType.SHIPYARD
        else:
            station_type = _STATION_TYPE_BY_CLASS.get(klass, StationType.TRADING)

        name = str(port.get("name", f"Station {sector_id}"))
        commodities = _build_full_commodities(port.get("commodities") or {})
        # Class-pattern finalization fully OVERRIDES bang's per-commodity
        # B/S flags (per SYSTEMS/bang-import-pipeline §11 / Appendix A) and
        # stocks the station so it is tradeable on first dock. Seeded per
        # station so re-importing the same universe is byte-identical.
        commodities = apply_class_pattern(
            commodities,
            station_class,
            random.Random(f"{universe_seed}:{sector_id}:{name}"),
        )
        services = _build_default_services(is_spacedock)
        return StationSpec(
            sector_int_id=sector_id,
            name=name,
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
        # CLASS_0 per ADR-0005 (Sol capital): the class pattern makes it sell
        # colonists, and we additionally force low-quantity orientation stock
        # of the standard commodities (galaxy-generation step 8: "standard
        # commodities in low quantities for orientation trades").
        earth_commodities = apply_class_pattern(
            _build_full_commodities({}),
            StationClass.CLASS_0,
            random.Random(f"{plan.universe_seed}:1:Earth Station"),
        )
        for key, qty in (("ore", 300), ("organics", 250), ("fuel", 400)):
            earth_commodities[key]["sells"] = True
            earth_commodities[key]["quantity"] = qty
        plan.stations = [s for s in plan.stations if s.sector_int_id != 1]
        plan.stations.insert(
            0,
            StationSpec(
                sector_int_id=1,
                name="Earth Station",
                station_class=StationClass.CLASS_0,
                station_type=StationType.TRADING,
                status=StationStatus.OPERATIONAL,
                commodities=earth_commodities,
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

        # SpaceDock at sector 10 — replace whatever bang produced. Same
        # class-pattern finalization as every other station, otherwise the
        # injected spec is fully inert (no buys/sells → no MarketPrice rows).
        plan.stations = [s for s in plan.stations if s.sector_int_id != 10]
        plan.stations.append(
            StationSpec(
                sector_int_id=10,
                name="Stardock",
                station_class=StationClass.CLASS_11,
                station_type=StationType.SHIPYARD,
                status=StationStatus.OPERATIONAL,
                commodities=apply_class_pattern(
                    _build_full_commodities({}),
                    StationClass.CLASS_11,
                    random.Random(f"{plan.universe_seed}:10:Stardock"),
                ),
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

    # ----- ADR-0041 Phase 10.5: TradeDock seeding -------------------------

    _TRADEDOCK_QUOTAS = {
        # region_type -> list of tiers to seed (ADR-0041 #phase-105):
        # Terran Space: 1 Tier-A guaranteed (Federation zone, local 1-99);
        # Central Nexus: 3 (1 Tier-A + 2 Tier-B);
        # player-owned regions are owner-funded, never auto-seeded
        # (tradedock-shipyard #galaxy-generation-seeding).
        "terran_space": ["A"],
        "central_nexus": ["A", "B", "B"],
        "player_owned": [],
    }

    _TRADEDOCK_NAMES = {
        "A": ["TradeDock Prime", "TradeDock Apex"],
        "B": ["TradeDock Meridian", "TradeDock Crucible", "TradeDock Bastion"],
    }

    # Tier-A flagships carry a UNIQUE name per region (both regions drawing
    # 'TradeDock Prime' from the shared rotation produced duplicates):
    # Terran Space keeps 'TradeDock Prime'; the Central Nexus flagship is
    # 'TradeDock Nexus Prime'. Tier-B names stay distinct via the shared
    # rotation above. Keep in sync with repair_tradedocks.py.
    _TRADEDOCK_TIER_A_NAMES_BY_REGION = {
        "terran_space": ["TradeDock Prime", "TradeDock Apex"],
        "central_nexus": ["TradeDock Nexus Prime", "TradeDock Nexus Apex"],
    }

    def _apply_tradedock_seeding(
        self,
        region_type: str,
        plan: RegionInsertPlan,
        warnings: List[Dict[str, Any]],
    ) -> RegionInsertPlan:
        """Seed per-region TradeDocks (ADR-0041 Phase 10.5).

        Runs while sector ids are still region-local, so the canonical
        "Federation zone, sector range 1-99" placement for Terran Space
        (tradedock-shipyard #galaxy-generation-seeding) reads directly.
        Nexus docks prefer the upper (EXPANSE-ward) half of the region —
        a documented simplification of "EXPANSE zones near population
        centres" pending zone metadata in the import plan.
        """
        tiers = self._TRADEDOCK_QUOTAS.get(region_type, [])
        if not tiers:
            return plan

        rng = random.Random(f"{plan.universe_seed}:tradedock:{region_type}")
        occupied = {st.sector_int_id for st in plan.stations}
        total = plan.total_sectors

        if region_type == "terran_space":
            candidate_pool = [i for i in range(2, min(100, total + 1)) if i not in occupied]
        else:
            lower = max(2, total // 2)
            candidate_pool = [i for i in range(lower, total + 1) if i not in occupied]

        name_counters = {"A": 0, "B": 0}
        for tier in tiers:
            if not candidate_pool:
                warnings.append(
                    {
                        "category": "TRADEDOCK_SEEDING",
                        "code": "TD-001",
                        "message": f"{region_type}: no free sector for Tier-{tier} TradeDock",
                    }
                )
                continue
            sector_int = candidate_pool.pop(rng.randrange(len(candidate_pool)))
            occupied.add(sector_int)
            if tier == "A":
                names = self._TRADEDOCK_TIER_A_NAMES_BY_REGION.get(
                    region_type, self._TRADEDOCK_NAMES["A"]
                )
            else:
                names = self._TRADEDOCK_NAMES[tier]
            name = names[name_counters[tier] % len(names)]
            name_counters[tier] += 1

            plan.stations.append(
                StationSpec(
                    sector_int_id=sector_int,
                    name=name,
                    station_class=StationClass.CLASS_11,
                    station_type=StationType.SHIPYARD,
                    status=StationStatus.OPERATIONAL,
                    commodities=apply_class_pattern(
                        _build_full_commodities({}),
                        StationClass.CLASS_11,
                        random.Random(f"{plan.universe_seed}:{sector_int}:{name}"),
                    ),
                    services={
                        "ship_dealer": True,
                        "ship_repair": True,
                        "ship_maintenance": True,
                        "ship_upgrades": True,
                        "insurance": True,
                        "drone_shop": True,
                        "genesis_dealer": False,
                        "mine_dealer": True,
                        "diplomatic_services": False,
                        "storage_rental": True,
                        "market_intelligence": True,
                        "refining_facility": True,
                        "luxury_amenities": tier == "A",
                    },
                    is_spacedock=False,
                    tradedock_tier=tier,
                    description=(
                        "Tier-A TradeDock — Warp-Jumper-capable construction shipyard."
                        if tier == "A"
                        else "Tier-B TradeDock — standard construction shipyard."
                    ),
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

        # Reap the Region rows the route handler pre-created for this job.
        # POST /admin/galaxy/jobs commits three Region rows (one per region
        # type) before the bang job runs; if apply() never gets to create
        # the matching Galaxy + Sectors, those Regions are orphans. They
        # match by the deterministic name pattern bang-{job_id}-{rt} that
        # bang_galaxy.create_bang_job uses.
        await session.execute(
            text("DELETE FROM regions WHERE name LIKE :prefix"),
            {"prefix": f"bang-{job_id}-%"},
        )

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

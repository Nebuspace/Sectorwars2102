"""Pirate ecosystem service -- population score / target / cap / cleansed-state
foundation (WO-PIRATE-ECO-1).

Canon: sw2102-docs/SYSTEMS/pirate-ecosystem.md (ADR-0048), pirate-holding-raid.md
(ADR-0047 strength fields). See src/models/pirate_holding.py and
src/models/pirate_kill_log.py for the model-level divergence notes.

Scope: THIS module ships the FOUNDATION only -- population scoring, target
population, the population cap check, cleansed-region detection, the
Region.pirate_ecosystem_state snapshot read/refresh, and a first-cut
eligible-sector finder (holding/station exclusion only). The weekly growth
tick, daughter spawning, evolution tick, and the capture-transaction kill-log
insert are explicitly OUT of scope (ECO-2/ECO-3 follow-ups per the WO) --
functions here are the building blocks those ticks will call.

Design pattern: every DB-touching function has a "pure core" that does the
actual math/logic against plain values (never touching the session), plus a
thin DB-query wrapper around it. This keeps the population-score / target /
cap arithmetic and the cleansed-state transition unit-testable without any
mock-session machinery -- the pure cores accept lists/dicts/timestamps
directly.

Sync Session throughout; every function flushes, never commits -- the caller
(the eventual weekly-tick service / an API route) owns the transaction
boundary, matching medal_service.py / research_service.py.
"""

from __future__ import annotations

import logging
import math
import random
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional
import uuid

from sqlalchemy import func
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from src.models.pirate_holding import PirateHolding, PirateHoldingTier
from src.models.pirate_kill_log import PirateKillLog
from src.models.region import Region, RegionStatus
from src.models.sector import Sector
from src.models.station import Station

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Constants (pirate-ecosystem.md)
# ---------------------------------------------------------------------------

# Population weight per tier (:45-64).
TIER_WEIGHT: Dict[PirateHoldingTier, int] = {
    PirateHoldingTier.CAMP: 1,
    PirateHoldingTier.OUTPOST: 3,
    PirateHoldingTier.STRONGHOLD: 10,
}

CAP_MULTIPLIER = 1.5  # :401-411, MAX_POPULATION_PER_REGION = base_target * 1.5
CLEANSED_DAYS = 7  # :343-377 zero-population-days threshold to declare Cleansed
CLEANSED_BONUS_WINDOW_DAYS = 30  # :66-93 / :343-377 bonus + marker-expiry window
CLEANSED_SUPPRESSION_FACTOR = 0.50  # :66-93 extra *= 0.50 while the Cleansed window is active
KILL_WEIGHT_SUPPRESSION_RATE = 0.05  # :66-93 suppression_modifier slope
MIN_SUPPRESSION_MODIFIER = 0.20  # :66-93 floor

# [NO-CANON] Canon's REGION_BASELINE_TARGET table (:85-93) is keyed by
# "region size (sectors)" with buckets up to 1,200, and says the 1,201+
# bucket should "scale up proportionally" -- no numeric anchor/slope is
# specified for that extrapolation. We anchor at the last defined point
# (1200 -> 35) and scale linearly. Flagged for the docs repo. Region has no
# `size_tier` column (canon's pseudocode references `region.size_tier`,
# which doesn't exist on the model) -- bucketed from `Region.total_sectors`
# instead, which is the only sizing field the model actually has.
_BASELINE_TARGET_BUCKETS = (
    (300, 12.0),
    (600, 22.0),
    (800, 30.0),
    (1200, 35.0),
)

# Default pirate_ecosystem_state shape (pirate-ecosystem.md:379-399, the
# 11-field snapshot). Timestamps are stored as ISO 8601 strings -- JSONB has
# no native datetime type.
DEFAULT_ECOSYSTEM_STATE: Dict[str, Any] = {
    "base_target": None,
    "current_population_score": 0,
    "current_target": None,
    "suppression_modifier": 1.0,
    "kill_weight_last_30_days": 0,
    "zero_population_since": None,
    "cleansed_at": None,
    "last_growth_tick_at": None,
    "last_growth_action": None,
    "last_evolution_tick_at": None,
    "evolutions_since_creation": 0,
}


# ---------------------------------------------------------------------------
# Small shared helpers
# ---------------------------------------------------------------------------

def _now(now: Optional[datetime]) -> datetime:
    return now if now is not None else datetime.now(timezone.utc)


def _iso(dt: Optional[datetime]) -> Optional[str]:
    return dt.isoformat() if dt else None


def _parse_iso(value: Any) -> Optional[datetime]:
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(value)


def tier_weight(tier: Any) -> int:
    """Normalizes a tier (enum member or bare string, e.g. from a lightweight
    test fixture) to its TIER_WEIGHT. Raises KeyError on an unknown tier --
    deliberately loud rather than silently scoring 0."""
    key = tier if isinstance(tier, PirateHoldingTier) else PirateHoldingTier(str(tier))
    return TIER_WEIGHT[key]


# ---------------------------------------------------------------------------
# Population score (pirate-ecosystem.md:45-64)
# ---------------------------------------------------------------------------

def score_holdings(holdings: List[Any]) -> int:
    """Pure population-score core. Sums TIER_WEIGHT over PIRATE-CONTROLLED
    holdings only (owner_player_id IS NULL) -- a player-captured holding
    contributes zero (:64). Accepts any iterable of holding-like objects
    exposing `.tier` and `.owner_player_id` -- real ORM rows or lightweight
    test fixtures (SimpleNamespace) interchangeably."""
    return sum(
        tier_weight(h.tier) for h in holdings if getattr(h, "owner_player_id", None) is None
    )


def compute_population_score(db: Session, region_id: uuid.UUID) -> int:
    """DB-backed wrapper: fetches this region's holdings and scores them."""
    holdings = db.query(PirateHolding).filter(PirateHolding.region_id == region_id).all()
    return score_holdings(holdings)


# ---------------------------------------------------------------------------
# Target population (pirate-ecosystem.md:66-93)
# ---------------------------------------------------------------------------

def base_target_for_total_sectors(total_sectors: int) -> float:
    """REGION_BASELINE_TARGET, bucketed from Region.total_sectors -- see the
    [NO-CANON] note above `_BASELINE_TARGET_BUCKETS`."""
    for ceiling, target in _BASELINE_TARGET_BUCKETS:
        if total_sectors <= ceiling:
            return target
    last_ceiling, last_target = _BASELINE_TARGET_BUCKETS[-1]
    return last_target * (total_sectors / last_ceiling)


def suppression_modifier(
    kill_weight: int,
    *,
    cleansed_at: Optional[datetime],
    now: Optional[datetime] = None,
) -> float:
    """Pure core of the suppression math (:66-93). `cleansed_at` is the
    parsed pirate_ecosystem_state.cleansed_at (None if not Cleansed)."""
    now = _now(now)
    modifier = max(MIN_SUPPRESSION_MODIFIER, 1.0 - KILL_WEIGHT_SUPPRESSION_RATE * kill_weight)
    if cleansed_at is not None:
        days_cleansed = (now - cleansed_at).days
        if days_cleansed < CLEANSED_BONUS_WINDOW_DAYS:
            modifier *= CLEANSED_SUPPRESSION_FACTOR
    return modifier


def compute_target_population(
    total_sectors: int,
    *,
    kill_weight: int = 0,
    cleansed_at: Optional[datetime] = None,
    now: Optional[datetime] = None,
) -> float:
    """Pure core (:66-93). No DB access -- callers resolve total_sectors /
    kill_weight / cleansed_at from Region + PirateKillLog +
    pirate_ecosystem_state first (see compute_target_population_for_region)."""
    base = base_target_for_total_sectors(total_sectors)
    return base * suppression_modifier(kill_weight, cleansed_at=cleansed_at, now=now)


def sum_kill_weights(
    db: Session,
    region_id: uuid.UUID,
    *,
    days: int = 30,
    now: Optional[datetime] = None,
) -> int:
    """DB-backed rolling kill-weight sum (:113-120)."""
    cutoff = _now(now) - timedelta(days=days)
    total = (
        db.query(func.coalesce(func.sum(PirateKillLog.kill_weight), 0))
        .filter(PirateKillLog.region_id == region_id, PirateKillLog.created_at >= cutoff)
        .scalar()
    )
    return int(total or 0)


def compute_target_population_for_region(
    db: Session, region: Region, *, now: Optional[datetime] = None
) -> float:
    """DB-backed wrapper composing sum_kill_weights + the region's current
    pirate_ecosystem_state.cleansed_at with the pure compute_target_population
    core."""
    now = _now(now)
    kill_weight = sum_kill_weights(db, region.id, days=30, now=now)
    state = region.pirate_ecosystem_state or {}
    cleansed_at = _parse_iso(state.get("cleansed_at"))
    return compute_target_population(
        region.total_sectors, kill_weight=kill_weight, cleansed_at=cleansed_at, now=now
    )


# ---------------------------------------------------------------------------
# Population cap (pirate-ecosystem.md:401-411)
# ---------------------------------------------------------------------------

def max_population_for_total_sectors(total_sectors: int) -> float:
    return base_target_for_total_sectors(total_sectors) * CAP_MULTIPLIER


def would_exceed_max_population(current_score: int, tier_being_added: Any, total_sectors: int) -> bool:
    """Pure core (:401-411). `current_score` should already exclude
    player-captured holdings (score_holdings/compute_population_score do
    this) -- the cap only governs pirate-controlled presence."""
    new_score = current_score + tier_weight(tier_being_added)
    return new_score > max_population_for_total_sectors(total_sectors)


def would_exceed_max_population_for_region(
    db: Session, region: Region, tier_being_added: Any
) -> bool:
    """DB-backed wrapper."""
    current_score = compute_population_score(db, region.id)
    return would_exceed_max_population(current_score, tier_being_added, region.total_sectors)


# ---------------------------------------------------------------------------
# Cleansed-region detection (pirate-ecosystem.md:343-377)
# ---------------------------------------------------------------------------

def update_cleansed_state(state: Dict[str, Any], current_score: int, now: Optional[datetime] = None) -> Dict[str, Any]:
    """Pure core of cleansed-region detection (:343-377).

    `state` is the pirate_ecosystem_state JSONB shape (or a fresh dict
    default-initialized by the caller). Mutates and returns the SAME dict --
    JSONB values are plain ISO-8601 strings (JSONB has no native datetime
    type). The caller is responsible for reassigning the column + calling
    flag_modified so SQLAlchemy detects the change (plain JSONB mutation is
    NOT auto-tracked by the ORM).

    Idempotent: re-running on an already-Cleansed state with the same zero
    score does not re-stamp cleansed_at or reset zero_population_since.

    NOTE: canon's pseudocode (:348-375) also emits a `region_cleansed`
    realtime event and awards the Pirate Hunter medal at the moment
    cleansed_at is first stamped. That side-effecting wiring is explicitly
    OUT of scope for this foundation WO (belongs with the weekly growth-tick
    service, ECO-2) -- this function only updates the state dict.
    """
    now = _now(now)
    zero_since = _parse_iso(state.get("zero_population_since"))
    cleansed_at = _parse_iso(state.get("cleansed_at"))

    if current_score == 0:
        if zero_since is None:
            zero_since = now
        elif cleansed_at is None and (now - zero_since).days >= CLEANSED_DAYS:
            cleansed_at = now
        # else: already zero and either not yet at the 7-day mark, or
        # already Cleansed -- nothing changes (idempotent).
    else:
        zero_since = None
        if cleansed_at is not None and (now - cleansed_at).days >= CLEANSED_BONUS_WINDOW_DAYS:
            cleansed_at = None
        # else (cleansed_at set and still within 30 days, or never set):
        # cleansed_at is left as-is -- the bonus decays via
        # suppression_modifier's own days_cleansed check, not cleared here.

    state["zero_population_since"] = _iso(zero_since)
    state["cleansed_at"] = _iso(cleansed_at)
    return state


# ---------------------------------------------------------------------------
# Region.pirate_ecosystem_state snapshot (pirate-ecosystem.md:379-399)
# ---------------------------------------------------------------------------

def get_pirate_ecosystem_state(region: Region) -> Dict[str, Any]:
    """Lazy-init READ ONLY: returns region.pirate_ecosystem_state, defaulting
    to a fresh copy of DEFAULT_ECOSYSTEM_STATE when NULL (failure-modes
    table, :432). Does NOT write back -- callers that need the default
    persisted call refresh_pirate_ecosystem_snapshot."""
    if region.pirate_ecosystem_state:
        return dict(region.pirate_ecosystem_state)
    return dict(DEFAULT_ECOSYSTEM_STATE)


def refresh_pirate_ecosystem_snapshot(
    db: Session, region: Region, *, now: Optional[datetime] = None
) -> Dict[str, Any]:
    """Recomputes + writes the Region.pirate_ecosystem_state JSONB snapshot
    from the live PirateHolding / PirateKillLog tables (the authoritative
    source -- this column is a fast-path read cache, :399).

    Ordering mirrors the canon weekly tick (:36): cleansed-state check runs
    BEFORE the target computation, so a freshly-qualifying Cleansed window is
    reflected in this same snapshot's suppression_modifier/current_target.

    Mutates region.pirate_ecosystem_state via reassignment + flag_modified
    (plain JSONB mutation is not self-tracking) and flushes. Caller commits.
    """
    now = _now(now)
    state = get_pirate_ecosystem_state(region)

    current_score = compute_population_score(db, region.id)
    kill_weight = sum_kill_weights(db, region.id, days=30, now=now)

    update_cleansed_state(state, current_score, now)
    cleansed_at = _parse_iso(state.get("cleansed_at"))

    base_target = base_target_for_total_sectors(region.total_sectors)
    modifier = suppression_modifier(kill_weight, cleansed_at=cleansed_at, now=now)
    target = base_target * modifier

    state["base_target"] = base_target
    state["current_population_score"] = current_score
    state["current_target"] = target
    state["suppression_modifier"] = modifier
    state["kill_weight_last_30_days"] = kill_weight

    region.pirate_ecosystem_state = state
    flag_modified(region, "pirate_ecosystem_state")
    db.flush()
    return state


# ---------------------------------------------------------------------------
# Eligible-sector finder -- foundation slice (exclusion only)
# ---------------------------------------------------------------------------

def find_eligible_sectors(
    db: Session, region_id: uuid.UUID, candidate_sector_ids: List[int]
) -> List[int]:
    """First-cut eligible-sector finder (WO-PIRATE-ECO-1 foundation slice).

    Excludes any candidate already carrying a PirateHolding or a Station
    (matched by the GLOBAL sector_id integer both tables share). The full
    canon selection surface (avoid_starter_cluster / avoid_phase11_anchors /
    prefer_low_patrol_density / zone-affinity radius, pirate-ecosystem.md
    :182-224) is NOT implemented here -- deferred to ECO-2's growth-tick
    spawn algorithm, which is this function's only caller-to-be.

    [NO-CANON]: candidate_sector_ids sourcing (which sectors of the region
    are even considered) is left to the caller -- canon's find_eligible_sectors
    signature bundles region-wide sector enumeration together with all the
    exclusion filters into one call; this foundation slice only implements
    the exclusion half instructed by this WO.

    Preserves the input order of candidate_sector_ids that survive.
    """
    if not candidate_sector_ids:
        return []

    occupied_by_holding = {
        sid
        for (sid,) in db.query(PirateHolding.sector_id)
        .filter(
            PirateHolding.region_id == region_id,
            PirateHolding.sector_id.in_(candidate_sector_ids),
        )
        .all()
    }
    occupied_by_station = {
        sid
        for (sid,) in db.query(Station.sector_id)
        .filter(Station.sector_id.in_(candidate_sector_ids))
        .all()
    }
    occupied = occupied_by_holding | occupied_by_station
    return [sid for sid in candidate_sector_ids if sid not in occupied]


# ---------------------------------------------------------------------------
# Weekly growth tick -- daughter spawning + seed fallback
# (pirate-ecosystem.md:122-277). WO-PIRATE-ECO-2.
# ---------------------------------------------------------------------------

# Spawn parent pick weight (:235-241): Stronghold parents are the most
# likely to spawn a daughter (they have the resources to seed one).
SPAWN_PARENT_WEIGHT: Dict[PirateHoldingTier, int] = {
    PirateHoldingTier.CAMP: 1,
    PirateHoldingTier.OUTPOST: 2,
    PirateHoldingTier.STRONGHOLD: 4,
}

# Daughter-tier distribution by parent tier (:227-233). "skip" = propagation
# fails this attempt (parent doesn't produce a daughter this tick).
SPAWN_DISTRIBUTION: Dict[PirateHoldingTier, Dict[Any, float]] = {
    PirateHoldingTier.CAMP: {
        PirateHoldingTier.CAMP: 0.70,
        "skip": 0.30,
    },
    PirateHoldingTier.OUTPOST: {
        PirateHoldingTier.CAMP: 0.50,
        PirateHoldingTier.OUTPOST: 0.30,
        "skip": 0.20,
    },
    PirateHoldingTier.STRONGHOLD: {
        PirateHoldingTier.CAMP: 0.50,
        PirateHoldingTier.OUTPOST: 0.30,
        PirateHoldingTier.STRONGHOLD: 0.20,
        "skip": 0.0,
    },
}

GROWTH_TOLERANCE_BAND = 3  # :138 -- delta < 3 -> no_growth, don't bother spawning
GROWTH_MAX_SITES_PER_TICK = 5  # :141 -- cap at +5/week/region
GROWTH_SITES_PER_DELTA_UNIT = 3  # :141 -- sites_to_spawn = ceil(delta / 3)


def _week_start(dt: datetime) -> datetime:
    """The most recent UTC Sunday 00:00 at/before ``dt`` (:32, "Weekly UTC
    Sunday 00:00"). The idempotence key for run_weekly_tick -- two calls
    whose ``now`` falls in the SAME week-bucket are the same tick window."""
    dt = dt.astimezone(timezone.utc)
    # datetime.weekday(): Monday=0 ... Sunday=6. Days elapsed since the most
    # recent Sunday:
    days_since_sunday = (dt.weekday() + 1) % 7
    day_start = dt.replace(hour=0, minute=0, second=0, microsecond=0)
    return day_start - timedelta(days=days_since_sunday)


def _weighted_choice(choices: List[Any], weights: List[float], rng: Any = None) -> Any:
    """Single weighted pick. ``rng`` is injectable (anything exposing
    ``.choices(population, weights=..., k=1)``, e.g. a seeded
    ``random.Random()``) for deterministic tests; defaults to the stdlib
    ``random`` module, matching the rest of the codebase's random usage
    (e.g. combat_service)."""
    picker = rng if rng is not None else random
    return picker.choices(choices, weights=weights, k=1)[0]


def _eligible_region_sectors(db: Session, region_id: uuid.UUID) -> List[int]:
    """All GLOBAL sector_ids in this region, filtered through
    find_eligible_sectors (holding/station exclusion). [NO-CANON]: canon's
    find_eligible_sectors bundles region-wide enumeration with the exclusion
    filters in one call (see find_eligible_sectors' own docstring); this is
    the enumeration half this WO's spawn/seed functions need."""
    candidate_ids = [
        sid for (sid,) in db.query(Sector.sector_id).filter(Sector.region_id == region_id).all()
    ]
    return find_eligible_sectors(db, region_id, candidate_ids)


def spawn_daughter_holding(
    db: Session, region: Region, *, now: Optional[datetime] = None, rng: Any = None
) -> Optional[PirateHolding]:
    """Spawn one daughter holding off an existing pirate-controlled parent
    (pirate-ecosystem.md:151-243). Returns the created row, or None if the
    attempt fails/is capped/has nowhere to land.

    [NO-CANON, resolved pragmatically]: canon's :158 cap check calls
    ``would_exceed_max_population(region)`` with NO tier argument, but the
    function is DEFINED at :406 taking ``(region, tier_being_added)`` -- an
    internal inconsistency in the doc (you cannot check "would adding X
    exceed the cap" without first knowing X). Resolved here by checking the
    cap AFTER the daughter tier is chosen -- the only semantically coherent
    order -- rather than before.

    [NO-CANON, deferred -- flagged for DECISIONS]: zone-affinity radius
    weighting (:182-224), the roving_fleet_camp warp-reachability check
    (:205-209, structurally inapplicable anyway -- PirateHoldingTier has no
    ROVING_FLEET_CAMP member), and the avoid_starter_cluster /
    avoid_phase11_anchors / prefer_low_patrol_density site-selection
    refinements are NOT implemented -- this picks UNIFORMLY at random among
    find_eligible_sectors' output (holding/station exclusion only).
    parent_holding_id lineage and the composition roll are also deferred,
    same as ECO-1's own documented PirateHolding-model omissions (no
    parent_holding_id/composition columns) -- NPC-roster materialization for
    a new holding's garrison is Lane B / npc_scheduler territory, explicitly
    out of this WO's scope per the dispatch brief.
    """
    now = _now(now)
    parents = (
        db.query(PirateHolding)
        .filter(
            PirateHolding.region_id == region.id,
            PirateHolding.owner_player_id.is_(None),
        )
        .all()
    )
    if not parents:
        # Fully cleansed (or never seeded) -- fall back to seed spawning (:169-170).
        return seed_spawn_camp(db, region, now=now, rng=rng)

    parent = _weighted_choice(
        parents, [SPAWN_PARENT_WEIGHT[p.tier] for p in parents], rng=rng
    )
    distribution = SPAWN_DISTRIBUTION[parent.tier]
    outcomes = list(distribution.keys())
    daughter_tier = _weighted_choice(outcomes, [distribution[o] for o in outcomes], rng=rng)
    if daughter_tier == "skip":
        return None  # propagation failed this attempt (:180)

    current_score = compute_population_score(db, region.id)
    if would_exceed_max_population(current_score, daughter_tier, region.total_sectors):
        logger.info(
            "pirate.daughter_spawn_capped region=%s tier=%s",
            region.id, daughter_tier.value,
        )
        return None

    eligible = _eligible_region_sectors(db, region.id)
    if not eligible:
        return None

    picker = rng if rng is not None else random
    anchor_sector_id = picker.choice(eligible)

    holding = PirateHolding(
        region_id=region.id,
        sector_id=anchor_sector_id,
        tier=daughter_tier,
        owner_player_id=None,
        current_strength=1.0,
    )
    db.add(holding)
    db.flush()
    return holding


def seed_spawn_camp(
    db: Session, region: Region, *, now: Optional[datetime] = None, rng: Any = None
) -> Optional[PirateHolding]:
    """Bootstrap a fresh Camp when a region is fully cleansed / has no
    parent holdings (pirate-ecosystem.md:245-277). Returns the created row,
    or None if capped/has nowhere to land.

    [NO-CANON, deferred]: canon restricts seed placement to Frontier-zone
    sectors (``zone_filter=['frontier']``, :260) -- this WO has no zone
    integration for pirate site selection (same omission as
    spawn_daughter_holding), so seeding draws from ALL find_eligible_sectors
    output in the region, not Frontier-only. composition roll deferred
    (Lane B, NPC-roster materialization).
    """
    now = _now(now)
    current_score = compute_population_score(db, region.id)
    if would_exceed_max_population(current_score, PirateHoldingTier.CAMP, region.total_sectors):
        return None

    eligible = _eligible_region_sectors(db, region.id)
    if not eligible:
        return None

    picker = rng if rng is not None else random
    anchor_sector_id = picker.choice(eligible)

    holding = PirateHolding(
        region_id=region.id,
        sector_id=anchor_sector_id,
        tier=PirateHoldingTier.CAMP,
        owner_player_id=None,
        current_strength=1.0,
    )
    db.add(holding)
    db.flush()
    return holding


def run_weekly_tick(
    db: Session, region: Region, *, now: Optional[datetime] = None, rng: Any = None
) -> Dict[str, Any]:
    """The weekly growth tick for ONE region (pirate-ecosystem.md:122-149).
    Per ADR-0060 X-I1: non-active regions are skipped entirely (no state
    accrual during lifecycle wind-down); cleanup during termination is a
    separate orchestrator concern.

    Scope note: this handles GROWTH only. Evolution (:279-341) is a
    SEPARATE top-level step of the outer weekly service loop per canon's own
    architecture diagram (:36-42, growth and evolution are sibling steps b/c
    under the same per-region loop, not nested) -- ``evolution_tick`` is
    this module's independent sibling function; wiring "for each region,
    for each holding, call both" is the outer scheduler's job (Lane B,
    deferred).

    IDEMPOTENT per tick window: a second call with a ``now`` in the SAME
    UTC week (Sunday 00:00 boundary, :32) as the persisted
    ``last_growth_tick_at`` is a no-op -- returns
    ``{"action": "already_ticked_this_window"}`` without touching
    population/holdings/state.
    """
    now = _now(now)
    if region.status != RegionStatus.ACTIVE.value:
        return {"action": "skipped", "reason": f"region_status={region.status}"}

    state = get_pirate_ecosystem_state(region)
    last_tick_at = _parse_iso(state.get("last_growth_tick_at"))
    if last_tick_at is not None and _week_start(last_tick_at) == _week_start(now):
        return {
            "action": "already_ticked_this_window",
            "last_growth_tick_at": _iso(last_tick_at),
        }

    current = compute_population_score(db, region.id)
    target = compute_target_population_for_region(db, region, now=now)
    delta = target - current

    if delta < GROWTH_TOLERANCE_BAND:
        result: Dict[str, Any] = {"action": "no_growth", "current": current, "target": target}
    else:
        sites_to_spawn = min(
            GROWTH_MAX_SITES_PER_TICK,
            math.ceil(delta / GROWTH_SITES_PER_DELTA_UNIT),
        )
        spawned: List[str] = []
        for _ in range(sites_to_spawn):
            holding = spawn_daughter_holding(db, region, now=now, rng=rng)
            if holding is not None:
                spawned.append(str(holding.id))
        result = {
            "action": "growth",
            "spawned": spawned,
            "current": current,
            "target": target,
        }

    state["last_growth_tick_at"] = _iso(now)
    state["last_growth_action"] = result["action"]
    region.pirate_ecosystem_state = state
    flag_modified(region, "pirate_ecosystem_state")
    db.flush()
    return result


# ---------------------------------------------------------------------------
# Evolution tick -- tier promotion (pirate-ecosystem.md:279-341). WO-PIRATE-ECO-2.
# ---------------------------------------------------------------------------

NEXT_TIER: Dict[PirateHoldingTier, PirateHoldingTier] = {
    PirateHoldingTier.CAMP: PirateHoldingTier.OUTPOST,
    PirateHoldingTier.OUTPOST: PirateHoldingTier.STRONGHOLD,
}

EVOLUTION_THRESHOLD_DAYS: Dict[PirateHoldingTier, int] = {
    PirateHoldingTier.CAMP: 30,
    PirateHoldingTier.OUTPOST: 60,
}  # :299

EVOLUTION_CHANCE: Dict[PirateHoldingTier, float] = {
    PirateHoldingTier.CAMP: 0.20,
    PirateHoldingTier.OUTPOST: 0.10,
}  # :316

EVOLUTION_FULL_STRENGTH_THRESHOLD = 0.95  # :289/:292

# Formations that satisfy the Outpost->Stronghold promotion prereq (:307-313).
_STRONGHOLD_FORMATION_TYPES = {"BUBBLE", "DEAD_END_BUBBLE"}


def _stronghold_promotion_formation_ok(db: Session, holding: PirateHolding) -> bool:
    """True iff holding.sector_id sits inside a Bubble or Dead-End-Bubble
    SpecialFormation, anchor OR interior (:307-313, the Outpost->Stronghold
    promotion prereq). PirateHolding.sector_id is the GLOBAL integer
    convention (see the model's own divergence note); SpecialFormation keys
    sectors by sectors.id UUID, so this resolves that UUID first."""
    from src.models.special_formation import SpecialFormation

    sector = db.query(Sector).filter(Sector.sector_id == holding.sector_id).first()
    if sector is None:
        return False
    formations = (
        db.query(SpecialFormation)
        .filter(
            SpecialFormation.region_id == holding.region_id,
            (SpecialFormation.anchor_sector_id == sector.id)
            | (SpecialFormation.interior_sector_ids.contains([sector.id])),
        )
        .all()
    )
    return any(f.type.name in _STRONGHOLD_FORMATION_TYPES for f in formations)


def _would_exceed_cap_after_promotion(
    db: Session, holding: PirateHolding, next_tier: PirateHoldingTier
) -> bool:
    """[NO-CANON, resolved pragmatically]: canon's evolution_tick cap check
    (:304) calls ``would_exceed_max_population(holding.region, holding.tier)``
    -- passing the holding's CURRENT (pre-promotion) tier as the "tier being
    added" argument. Applied literally that double-counts the holding (its
    current weight is already inside current_population_score) and ignores
    that a promotion only adds the DELTA between the old and new tier
    weights, not the new tier's full weight. Resolved here with the
    semantically-correct delta check: does swapping this ONE holding's
    weight from its current tier to ``next_tier`` push the region over cap?
    """
    current_score = compute_population_score(db, holding.region_id)
    projected = current_score - tier_weight(holding.tier) + tier_weight(next_tier)
    total_sectors = (
        db.query(Region.total_sectors).filter(Region.id == holding.region_id).scalar()
    )
    return projected > max_population_for_total_sectors(total_sectors or 0)


def promote_holding_tier(holding: PirateHolding, *, now: Optional[datetime] = None) -> Dict[str, Any]:
    """Execute a tier promotion on an already-cleared holding (:321-328).
    Mutates ``holding`` in place; the caller flushes (mirrors
    update_cleansed_state's own "mutate the dict, caller persists" contract).

    [NO-CANON, deferred -- Lane B territory]: composition re-roll, NPCRoster
    additions (Outpost gains enforcers, Stronghold adds a Lord+captains), and
    the citadel recovery-tick pacing are NOT performed here -- this WO's
    PirateHolding model carries no composition/roster columns (ECO-1's own
    documented omission), and NPC materialization is npc_scheduler
    territory. The `region_cleansed`-style realtime event canon fires on
    promotion (:328, `pirate.holding_evolved`) is ALSO deferred -- Lane C
    (websocket telemetry), explicitly out of this WO's scope.
    """
    now = _now(now)
    old_tier = holding.tier
    new_tier = NEXT_TIER[old_tier]
    holding.tier = new_tier
    # :327 -- promotion resets the evolution clock, exactly like a real
    # damage event does (both use last_damage_at as the clock anchor).
    holding.last_damage_at = now
    return {
        "action": "evolved",
        "holding_id": str(holding.id),
        "old_tier": old_tier.value,
        "new_tier": new_tier.value,
    }


def evolution_tick(
    db: Session, holding: PirateHolding, *, now: Optional[datetime] = None, rng: Any = None
) -> Dict[str, Any]:
    """Evaluate ONE pirate-controlled holding for tier promotion
    (pirate-ecosystem.md:279-341). Flushes on an actual promotion; a no-op
    result touches nothing.

    Player-captured holdings are not evaluated -- promotion only applies to
    pirate-controlled sites (mirrors compute_population_score's own
    owner_player_id IS NULL gate); callers should not pass a captured
    holding, but this defends anyway rather than promoting a player's asset.
    """
    now = _now(now)
    if holding.owner_player_id is not None:
        return {"action": "none", "reason": "player_captured"}
    if holding.tier == PirateHoldingTier.STRONGHOLD:
        return {"action": "none", "reason": "max_tier"}
    if holding.current_strength < EVOLUTION_FULL_STRENGTH_THRESHOLD:
        return {"action": "none", "reason": "not_full_strength"}

    # :288-296 -- untouched-at-full-strength clock: created_at if NEVER
    # damaged, else the last damage event (which promote_holding_tier's own
    # reset, or a real combat hit, both advance).
    threshold_met_at = holding.last_damage_at or holding.created_at
    days_untouched = (now - threshold_met_at).days
    required = EVOLUTION_THRESHOLD_DAYS[holding.tier]
    if days_untouched < required:
        return {"action": "none", "reason": "clock_not_met", "days_untouched": days_untouched}

    next_tier = NEXT_TIER[holding.tier]
    if _would_exceed_cap_after_promotion(db, holding, next_tier):
        return {"action": "none", "reason": "capped"}

    if holding.tier == PirateHoldingTier.OUTPOST:
        if not _stronghold_promotion_formation_ok(db, holding):
            # :309-313 -- no qualifying formation: suppress promotion AND
            # reset the clock (same as a real damage event).
            holding.last_damage_at = now
            db.flush()
            return {"action": "suppressed", "reason": "no_formation"}

    chance = EVOLUTION_CHANCE[holding.tier]
    picker = rng if rng is not None else random
    if picker.random() < chance:
        result = promote_holding_tier(holding, now=now)
        db.flush()
        return result

    return {"action": "none", "reason": "roll_failed"}


# ---------------------------------------------------------------------------
# Cleansed-state side effects -- Pirate Hunter medal + the region_cleansed
# realtime-event seam (pirate-ecosystem.md:356-377). WO-PIRATE-ECO-2 --
# captured from ECO-1's own explicit deferral (see update_cleansed_state's
# docstring above).
# ---------------------------------------------------------------------------

CLEANSED_MEDAL_TOP_N = 3  # :377 -- "top 3 attackers"


def top_attackers_by_kill_weight(
    db: Session, region_id: uuid.UUID, *, days: int = 30, limit: int = CLEANSED_MEDAL_TOP_N,
    now: Optional[datetime] = None,
) -> List[uuid.UUID]:
    """The top ``limit`` distinct attacker_player_ids by SUMMED kill_weight
    in the last ``days`` for this region (:361, :377 "top recent
    attackers"). NULL attacker_player_id rows (a defensive nullability this
    codebase's PirateKillLog model carries, see its own divergence note) are
    excluded -- there is no player to credit."""
    cutoff = _now(now) - timedelta(days=days)
    rows = (
        db.query(PirateKillLog.attacker_player_id, func.sum(PirateKillLog.kill_weight).label("w"))
        .filter(
            PirateKillLog.region_id == region_id,
            PirateKillLog.created_at >= cutoff,
            PirateKillLog.attacker_player_id.isnot(None),
        )
        .group_by(PirateKillLog.attacker_player_id)
        .order_by(func.sum(PirateKillLog.kill_weight).desc())
        .limit(limit)
        .all()
    )
    return [player_id for player_id, _weight in rows]


def _dispatch_pirate_hunter_medals(
    db: Session, region: Region, attacker_ids: List[uuid.UUID]
) -> None:
    """Best-effort medal dispatch, mirroring combat_service._dispatch_bounty_
    medals' defensive contract EXACTLY: resolved by getattr (never a
    parse-time import of a symbol that may not exist), any failure logged
    and swallowed -- a medal hiccup must never break the weekly tick.

    [NO-CANON gap, flagged for DECISIONS -- NOT silently invented]: canon
    names a PER-REGION medal, `'Pirate Hunter — {region_name}'` (:377), but
    this codebase's medal catalog (medal_service.MEDAL_CATALOG /
    medal_catalog.py) is a STATIC dict of fixed medal_ids -- there is no
    existing 'pirate_hunter' entry and no mechanism for a dynamically
    region-named medal (every other medal is a fixed catalog id). This
    dispatcher calls the hook defensively with a STABLE medal_id
    ("combat.pirate_hunter") on the assumption a future catalog entry lands
    under that id; until one does, award_medal's own "unknown medal_id"
    guard makes this a harmless no-op (a logged warning, not a raised
    exception) rather than a crash. The region-name PARAMETERIZATION is not
    implemented --
    that requires a real catalog-shape decision (a template mechanism, or
    dropping the per-region naming) this dispatcher cannot make silently.
    """
    if not attacker_ids:
        return
    try:
        import src.services.medal_service as _medal_module

        award = getattr(_medal_module, "award_medal", None)
        if not callable(award):
            return
        for player_id in attacker_ids:
            try:
                award(
                    db, player_id, "combat.pirate_hunter",
                    awarded_via="pirate_ecosystem",
                    context_payload={"region_id": str(region.id), "region_name": region.name},
                )
            except Exception as e:  # never let one bad award break the batch
                logger.error(
                    "Pirate Hunter medal award failed for player %s region %s: %s",
                    player_id, region.id, e,
                )
    except Exception as e:  # never let a medal hiccup break the weekly tick
        logger.error("Pirate Hunter medal dispatch hook failed: %s", e)


def _emit_region_cleansed_event(region: Region, *, now: Optional[datetime] = None) -> None:
    """MARKED SEAM for Lane C (websocket telemetry), deferred out of this
    WO's scope per the dispatch brief ("do NOT touch... websocket_service.py").
    Canon (:358-362) fires a `region_cleansed` realtime event here with
    {region_id, cleansed_at, recent_attackers}. Currently a documented
    no-op -- Lane C wires the actual connection_manager dispatch when it
    lands; this function exists so that call site is already in the right
    place and doesn't need a second edit to THIS file later."""
    logger.info(
        "pirate.region_cleansed region=%s at=%s (realtime emit deferred to Lane C)",
        region.id, _iso(_now(now)),
    )


def update_cleansed_state_for_region(
    db: Session, region: Region, *, now: Optional[datetime] = None
) -> Dict[str, Any]:
    """DB-backed wrapper around the pure update_cleansed_state (unchanged,
    see its own docstring) that ALSO fires the side effects canon's
    pseudocode bundles into this step (:356-363): the Pirate Hunter medal
    to the top recent attackers, and the region_cleansed event seam. Fires
    ONLY on the genuine None->set transition (never re-fires on an
    already-Cleansed re-run, matching the pure core's own idempotence)."""
    now = _now(now)
    state = get_pirate_ecosystem_state(region)
    was_cleansed = state.get("cleansed_at") is not None

    current_score = compute_population_score(db, region.id)
    update_cleansed_state(state, current_score, now)

    region.pirate_ecosystem_state = state
    flag_modified(region, "pirate_ecosystem_state")

    newly_cleansed = (not was_cleansed) and (state.get("cleansed_at") is not None)
    if newly_cleansed:
        attackers = top_attackers_by_kill_weight(db, region.id, days=30, now=now)
        _dispatch_pirate_hunter_medals(db, region, attackers)
        _emit_region_cleansed_event(region, now=now)

    db.flush()
    return state


__all__ = [
    "TIER_WEIGHT",
    "CAP_MULTIPLIER",
    "CLEANSED_DAYS",
    "CLEANSED_BONUS_WINDOW_DAYS",
    "CLEANSED_SUPPRESSION_FACTOR",
    "KILL_WEIGHT_SUPPRESSION_RATE",
    "MIN_SUPPRESSION_MODIFIER",
    "DEFAULT_ECOSYSTEM_STATE",
    "tier_weight",
    "score_holdings",
    "compute_population_score",
    "base_target_for_total_sectors",
    "suppression_modifier",
    "compute_target_population",
    "sum_kill_weights",
    "compute_target_population_for_region",
    "max_population_for_total_sectors",
    "would_exceed_max_population",
    "would_exceed_max_population_for_region",
    "update_cleansed_state",
    "get_pirate_ecosystem_state",
    "refresh_pirate_ecosystem_snapshot",
    "find_eligible_sectors",
    # WO-PIRATE-ECO-2
    "SPAWN_PARENT_WEIGHT",
    "SPAWN_DISTRIBUTION",
    "GROWTH_TOLERANCE_BAND",
    "GROWTH_MAX_SITES_PER_TICK",
    "GROWTH_SITES_PER_DELTA_UNIT",
    "spawn_daughter_holding",
    "seed_spawn_camp",
    "run_weekly_tick",
    "NEXT_TIER",
    "EVOLUTION_THRESHOLD_DAYS",
    "EVOLUTION_CHANCE",
    "EVOLUTION_FULL_STRENGTH_THRESHOLD",
    "promote_holding_tier",
    "evolution_tick",
    "CLEANSED_MEDAL_TOP_N",
    "top_attackers_by_kill_weight",
    "update_cleansed_state_for_region",
]

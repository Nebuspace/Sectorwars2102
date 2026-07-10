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
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional
import uuid

from sqlalchemy import func
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from src.models.pirate_holding import PirateHolding, PirateHoldingTier
from src.models.pirate_kill_log import PirateKillLog
from src.models.region import Region
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
]

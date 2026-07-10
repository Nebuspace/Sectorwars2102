"""
Station-protection security-tier lifecycle: acquisition default, the canon
upgrade/downgrade ladder, and the recurring upkeep skim.

Canon reference: FEATURES/economy/station-protection.md (sw2102-docs). This
lane (WO-STN-SEC-1 lane 2) builds the OWNER-FACING tier lifecycle only —
tractor beam, guards/NPCs, and the anti-attack/anti-board guarantees are
separate lanes (anti-attack shipped as WO-CB1, see src.models.station
security_level / security_rank). This module owns the four-tier ladder
(none < basic < standard < premium), stored in the EXISTING Station.security
JSONB column (additive nullable, migration a1d7c4e92f6b) — no schema change.

LAZY ENGINE (matches port_ownership_service's listing/takeover pattern):
there is no background worker. upgrade_security_tier / downgrade_security_
tier / get_security_status all call _settle_pending() first, which flips a
completed pending op to its target tier and clears the pending keys. This is
IDEMPOTENT: the flip happens under the station's row lock
(with_for_update), so two "simultaneous" completion reads are serialized by
Postgres — the first flips and clears, the second sees already-cleared
pending keys and no-ops. No commit anywhere here; the calling route owns the
transaction (matches every sibling service in this codebase).

Canon durations ("24 real-time hours", "72 real-time hours", "7 real-time
days") are treated as CANONICAL hours passed through src.core.game_time
(GAME_TIME_SCALE compresses every window uniformly on dev) — despite the
literal "real-time" phrasing, this matches the established convention for
every other multi-hour window in this codebase (port_ownership_service's
GRACE_HOURS / COUNTER_WINDOW_HOURS / MILITARY_DECLARATION_HOURS), so the
security ladder scales the same way as every other timed system on dev.

DOCUMENTED INTERPRETATIONS (NO-CANON, flagged for bless):
  * Pending-op shape — station.security carries "upgrade_to" (target tier
    string) + "upgrade_completes_at" (ISO datetime) for a pending upgrade,
    or "downgrade_completes_at" (ISO datetime) alone for a pending downgrade
    (the downgrade target is always deterministic — one tier down from
    current — so no "downgrade_to" key is needed). Exactly ONE of an
    upgrade or a downgrade may be pending at a time; a second
    upgrade/downgrade request while one is already pending is rejected
    (400) rather than queued or overwritten.
  * Acquisition default — see port_ownership_service._transfer_station:
    ACQUISITION_DEFAULT_TIER ("basic") is written ONLY to an unconfigured
    (security NULL/non-dict) station on ownership transfer; an
    already-tiered station keeps its tier (acquisition is never a
    downgrade).
  * Upkeep realization — see port_ownership_service.realize_port_revenue:
    the canon "~5/10/20% of station revenue" recurring upkeep is skimmed
    from the OWNER's leg of the EXISTING 40/30/30 fee-distribution split at
    fee-realize time (never an extra deduction from gross, never touching
    the defense_fund/operating_fund buckets — those already fund the
    SEPARATE siege-defense infrastructure per port-ownership.md). Floored
    via min(owner, upkeep) so a fee-realize event can never produce a
    negative owner credit / treasury decrement. The skimmed amount
    accumulates in station.security["upkeep_collected"] for future
    observability (canon's "security pane... Defense budget current
    balance + 7-day burn rate" is a future lane); it is currently a sink —
    no consumer spends it yet (STATION_SECURITY guard wages / drone
    replenishment are 📐 design-only per the canon Status note).
  * Upgrade cost funding source — deducted from the OWNER's PERSONAL
    credits at initiation (mirrors place_offer's station-purchase escrow
    debit), not from station.treasury_balance. A tier upgrade is modeled as
    a capital purchase the owner makes personally, exactly like buying the
    station itself.

Lock-ordering contract (matches port_ownership_service / docking_service):
the STATION row is locked first, then the single OWNER player row. No
function here commits; the calling route owns the transaction.
"""
import logging
from datetime import datetime, UTC
from typing import Any, Dict, Optional

from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from src.core import game_time
from src.models.player import Player
from src.models.station import SECURITY_TIER_RANK, Station, security_tier_rank

logger = logging.getLogger(__name__)


class StationSecurityError(Exception):
    """Raised on invalid station-security actions; carries an HTTP status hint."""

    def __init__(self, status_code: int, detail: str):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


# ---------------------------------------------------------------------------
# Canon constants (FEATURES/economy/station-protection.md)
# ---------------------------------------------------------------------------

# Single source of truth for tier ordering — derived from the SAME rank
# table src.models.station.security_level / security_rank already read
# (WO-CB1), so the ladder can never drift from the model's own ordering.
SECURITY_TIER_ORDER = sorted(SECURITY_TIER_RANK, key=SECURITY_TIER_RANK.get)  # none,basic,standard,premium

# "Player-owned stations default to Basic" (canon "Security tiers").
ACQUISITION_DEFAULT_TIER = "basic"

# Canon "Tier upgrade cost" table — one-time cost, keyed by the TARGET tier.
SECURITY_UPGRADE_COST = {
    "basic": 50_000,
    "standard": 200_000,
    "premium": 750_000,
}

# Canon "Construction time" column, keyed by the TARGET tier. Treated as
# CANONICAL hours through src.core.game_time (see module docstring).
SECURITY_UPGRADE_HOURS = {
    "basic": 24.0,
    "standard": 72.0,
    "premium": 7 * 24.0,
}

# Canon "Downgrading is free but takes 24 hours ... one-step" — a single
# constant regardless of the current tier (downgrade always drops exactly
# one rung).
SECURITY_DOWNGRADE_HOURS = 24.0

# Canon "Tier upgrade cost > Recurring upkeep" — ~5/10/20% of station
# revenue, keyed by the CURRENT tier (see realization interpretation above).
SECURITY_UPKEEP_PCT = {
    "none": 0.0,
    "basic": 0.05,
    "standard": 0.10,
    "premium": 0.20,
}


# ---------------------------------------------------------------------------
# Pure helpers (no DB) — unit-tested directly
# ---------------------------------------------------------------------------

def _aware(dt: datetime) -> datetime:
    return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt


def _parse_iso(raw: Optional[str]) -> Optional[datetime]:
    if not raw:
        return None
    try:
        return _aware(datetime.fromisoformat(raw))
    except (ValueError, TypeError):
        return None


def next_tier_up(current: str) -> Optional[str]:
    """The tier immediately above `current`, or None if already at the top
    (premium)."""
    rank = security_tier_rank(current)
    if rank >= len(SECURITY_TIER_ORDER) - 1:
        return None
    return SECURITY_TIER_ORDER[rank + 1]


def next_tier_down(current: str) -> Optional[str]:
    """The tier immediately below `current`, or None if already at the
    floor (none) — nothing to downgrade."""
    rank = security_tier_rank(current)
    if rank <= 0:
        return None
    return SECURITY_TIER_ORDER[rank - 1]


def upkeep_pct_for(tier: Optional[str]) -> float:
    """Canon recurring-upkeep percentage for a security tier. Unknown/blank
    tiers read as "none" (0.0) — the conservative default, matching
    security_tier_rank's own fallback."""
    return SECURITY_UPKEEP_PCT.get((tier or "none").lower(), 0.0)


def upkeep_for_gross(gross: int, tier: Optional[str]) -> int:
    """Recurring security-tier upkeep skimmed from one realized-revenue
    event. Pure integer floor; gross<=0 -> 0. See realize_port_revenue's
    integration (skims from the OWNER leg, floored via min so it can never
    exceed that leg)."""
    if gross <= 0:
        return 0
    return int(gross * upkeep_pct_for(tier))


def apply_acquisition_default(station: Any) -> bool:
    """Station-protection acquisition default (canon "Security tiers":
    "Player-owned stations default to Basic"). Bumps an UNCONFIGURED
    station (security NULL/non-dict — the security_level property's own
    conservative "none" default) to {'tier': ACQUISITION_DEFAULT_TIER}. An
    ALREADY-TIERED station (security is already a dict, any tier including
    an explicit "none") is left completely UNCHANGED — acquisition must
    never downgrade or reset an existing tier. Called from
    port_ownership_service._transfer_station on every ownership transfer.

    Pure mutation, no DB/lock/flush — caller MUST flag_modified(station,
    'security') when this returns True. Returns whether a mutation
    occurred."""
    if isinstance(station.security, dict):
        return False
    station.security = {"tier": ACQUISITION_DEFAULT_TIER}
    return True


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

def _lock_station(db: Session, station_id) -> Station:
    station = (
        db.query(Station)
        .filter(Station.id == station_id)
        .populate_existing()
        .with_for_update()
        .first()
    )
    if station is None:
        raise StationSecurityError(404, "Station not found")
    return station


def _lock_owner(db: Session, player_id) -> Player:
    player = (
        db.query(Player)
        .filter(Player.id == player_id)
        .populate_existing()
        .with_for_update()
        .first()
    )
    if player is None:
        raise StationSecurityError(404, "Player not found")
    return player


def _require_owner(station: Station, owner: Player) -> None:
    if station.owner_id != owner.id:
        raise StationSecurityError(403, "Only the station owner can do that")


def _security(station: Station) -> Dict[str, Any]:
    """Mutable handle on station.security (created with tier 'none' if
    absent/non-dict — mirrors security_level's own conservative default).
    Caller MUST flag_modified(station, 'security') after mutating."""
    if not isinstance(station.security, dict):
        station.security = {"tier": "none"}
    return station.security


def _settle_pending(station: Station, now: datetime) -> bool:
    """Idempotently flip a completed pending upgrade/downgrade in-place on
    `station.security`. Returns True if a mutation occurred (caller must
    flag_modified + flush). Safe to call redundantly — once settled, both
    pending keys read as absent/None and a second call is a no-op, which is
    exactly what makes two "simultaneous" completion reads under the same
    station row lock safe: the first caller to reach here (serialized by
    with_for_update) wins the flip; the second sees nothing left to settle.

    Deliberately NON-mutating when station.security isn't already a dict —
    an unconfigured station can have no pending op, so there is nothing to
    settle and no reason to eagerly materialize a {'tier': 'none'} dict
    before validation runs (matches port_ownership_service's own
    validate-first-mutate-last discipline: _price_modifiers-style handles
    are only opened once a write is actually going to happen)."""
    if not isinstance(station.security, dict):
        return False
    sec = station.security
    mutated = False

    upgrade_to = sec.get("upgrade_to")
    upgrade_at = _parse_iso(sec.get("upgrade_completes_at"))
    if upgrade_to and upgrade_at is not None and now >= upgrade_at:
        sec["tier"] = upgrade_to
        sec["upgrade_to"] = None
        sec["upgrade_completes_at"] = None
        mutated = True

    downgrade_at = _parse_iso(sec.get("downgrade_completes_at"))
    if downgrade_at is not None and now >= downgrade_at:
        target = next_tier_down(sec.get("tier"))
        sec["tier"] = target if target is not None else "none"
        sec["downgrade_completes_at"] = None
        mutated = True

    return mutated


def _has_pending_op(sec: Dict[str, Any]) -> bool:
    return bool(sec.get("upgrade_to")) or sec.get("downgrade_completes_at") is not None


def _status_payload(station: Station) -> Dict[str, Any]:
    sec = station.security if isinstance(station.security, dict) else {}
    return {
        "station_id": str(station.id),
        "tier": station.security_level,
        "pending_upgrade_to": sec.get("upgrade_to"),
        "upgrade_completes_at": sec.get("upgrade_completes_at"),
        "pending_downgrade": sec.get("downgrade_completes_at") is not None,
        "downgrade_completes_at": sec.get("downgrade_completes_at"),
        "upkeep_collected": int(sec.get("upkeep_collected", 0) or 0),
    }


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_security_status(
    db: Session, station: Station, now: Optional[datetime] = None
) -> Dict[str, Any]:
    """Lazily settle any completed pending op, then return the station's
    security-tier state. No owner gate — any authenticated player may read a
    station's tier (matches get_station_listing_status's public-read
    convention; a docking player needs to know the tier before undocking)."""
    now = now or datetime.now(UTC)
    station = _lock_station(db, station.id)
    if _settle_pending(station, now):
        flag_modified(station, "security")
        db.flush()
    return _status_payload(station)


def upgrade_security_tier(
    db: Session, station: Station, owner: Player, now: Optional[datetime] = None
) -> Dict[str, Any]:
    """Initiate a one-step upgrade (none->basic->standard->premium). Deducts
    the EXACT canon cost from the owner's personal credits at initiation;
    the tier itself flips only once the clock-injectable, GAME_TIME_SCALE-
    scaled construction window completes (settled lazily on a later read —
    see _settle_pending). Insufficient credits -> 400, zero deduction, zero
    pending key written. A tier-skip (e.g. none->standard) is impossible —
    the target is always exactly one rung above the CURRENT tier. Rejects a
    second upgrade/downgrade while one is already pending (one pending op
    per station)."""
    now = now or datetime.now(UTC)
    station = _lock_station(db, station.id)
    _require_owner(station, owner)

    # Settle a completed pending op FIRST, as its own atomic step (mirrors
    # resolve_listing's lazy-settle-on-read). Flushed immediately so the
    # flip is durable independent of whatever this NEW upgrade request goes
    # on to do (including reject).
    if _settle_pending(station, now):
        flag_modified(station, "security")
        db.flush()

    # Read-only validation against the (possibly just-settled) state — no
    # mutable JSONB handle is opened yet, matching set_fee_distribution's
    # validate-first-mutate-last discipline: a rejected request must leave
    # an unconfigured station.security exactly as it was (None), never
    # eagerly materialize a {'tier': 'none'} placeholder.
    sec_read = station.security if isinstance(station.security, dict) else {}
    current = station.security_level
    target = next_tier_up(current)
    if target is None:
        raise StationSecurityError(
            400, f"{station.name} is already at the maximum security tier (premium)"
        )
    if _has_pending_op(sec_read):
        raise StationSecurityError(
            400, f"{station.name} already has a pending security-tier change"
        )

    cost = SECURITY_UPGRADE_COST[target]
    owner_row = _lock_owner(db, owner.id)
    if (owner_row.credits or 0) < cost:
        raise StationSecurityError(
            400,
            f"Upgrading {station.name} to {target} costs {cost:,} credits; "
            f"you have {owner_row.credits:,}",
        )
    owner_row.credits -= cost

    completes_at = game_time.scaled_deadline(SECURITY_UPGRADE_HOURS[target], start=now)
    sec = _security(station)   # only NOW create/normalize -- validation fully passed
    sec["upgrade_to"] = target
    sec["upgrade_completes_at"] = completes_at.isoformat()
    flag_modified(station, "security")
    db.flush()
    logger.info(
        "Station %s security upgrade initiated: %s -> %s, cost %s, completes %s",
        station.id, current, target, cost, completes_at.isoformat(),
    )
    return {
        "station_id": str(station.id),
        "current_tier": current,
        "upgrade_to": target,
        "cost": cost,
        "completes_at": completes_at.isoformat(),
        "credits": owner_row.credits,
    }


def downgrade_security_tier(
    db: Session, station: Station, owner: Player, now: Optional[datetime] = None
) -> Dict[str, Any]:
    """Initiate a free, one-step downgrade (premium->standard->basic->none),
    completing after the canon 24-(canonical-)hour dismissal window (settled
    lazily on a later read). Rejects a second upgrade/downgrade while one is
    already pending, and rejects downgrading a station already at 'none'."""
    now = now or datetime.now(UTC)
    station = _lock_station(db, station.id)
    _require_owner(station, owner)

    if _settle_pending(station, now):
        flag_modified(station, "security")
        db.flush()

    sec_read = station.security if isinstance(station.security, dict) else {}
    current = station.security_level
    target = next_tier_down(current)
    if target is None:
        raise StationSecurityError(
            400, f"{station.name} has no security tier to downgrade from"
        )
    if _has_pending_op(sec_read):
        raise StationSecurityError(
            400, f"{station.name} already has a pending security-tier change"
        )

    completes_at = game_time.scaled_deadline(SECURITY_DOWNGRADE_HOURS, start=now)
    sec = _security(station)   # only NOW create/normalize -- validation fully passed
    sec["downgrade_completes_at"] = completes_at.isoformat()
    flag_modified(station, "security")
    db.flush()
    logger.info(
        "Station %s security downgrade initiated: %s -> %s, completes %s",
        station.id, current, target, completes_at.isoformat(),
    )
    return {
        "station_id": str(station.id),
        "current_tier": current,
        "downgrade_to": target,
        "cost": 0,
        "completes_at": completes_at.isoformat(),
    }

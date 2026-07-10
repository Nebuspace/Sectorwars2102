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
import random
from datetime import UTC, datetime
from typing import Any, Dict, Optional

from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from src.core import game_time
from src.models.player import Player
from src.models.ship import Ship, ShipType
from src.models.station import (
    SECURITY_TIER_PROTECTED_MIN_RANK,
    SECURITY_TIER_RANK,
    Station,
    security_tier_rank,
)

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


# ---------------------------------------------------------------------------
# Guarantee #2 -- Anti-theft tractor beam
# (FEATURES/economy/station-protection.md:77-111 "Anti-theft tractor beam" +
# "Tractor strength tiers"; WO-P2-econ-station-tractor-lock)
#
# When a ship attempts to undock at a security_rank >= basic station (the
# SAME threshold Guarantee #1 uses -- SECURITY_TIER_PROTECTED_MIN_RANK), the
# undock route calls check_tractor_lock() with the canon 3-check deny-list:
#   1. Ship.stolen_status is True
#   2. Player.personal_reputation < station's wanted_threshold (canon default
#      -500 -- Station has no dedicated wanted_threshold/deny_list_player_ids
#      COLUMNS; per this WO's NO-MIGRATION constraint, both live as OPTIONAL
#      keys inside the EXISTING station.security JSONB, matching the exact
#      architecture canon's own "Station.security JSONB shape" section
#      describes for tractor_lock_active/tractor_locked_ship_id -- this is
#      an established pattern in this file (see module docstring), not a
#      new one. Unset -> the canon default.
#   3. player.id in station.security["deny_list_player_ids"] (owner-set,
#      defaults empty -- no UI to populate it yet; the gate exists so a
#      future owner-facing endpoint has somewhere to write it)
#
# A hit creates a PER-SHIP lock entry in station.security["tractor_locks"]
# (keyed by ship id, not a single station-wide slot -- canon: "the station
# can lock multiple ships independently"). The reason/strength FREEZE at
# lock time; only break-free or surrender clears it (canon: "the tractor
# lock applies only at undock" -- there is no automatic release).
# ---------------------------------------------------------------------------

# Canon "Security tiers" table column "Tractor strength", keyed by the SAME
# tier string security_level/security_rank already use.
TRACTOR_STRENGTH_BY_TIER = {
    "basic": "weak",
    "standard": "strong",
    "premium": "immobilizing",
}

# Canon "Tractor strength tiers" pinned break-roll numbers (station-
# protection.md:107-109, verbatim per the Rook canon-audit). success_chance
# is a PER-ATTEMPT probability -- this build resolves one break_attempt()
# call as one roll (not a per-game-turn tick loop; canon's "25% chance per
# turn... expected break time ~10 turns" describes the STATISTICAL outcome
# of repeated attempts, each costing the pinned turns, not a scheduler-level
# mechanic this WO builds). Immobilizing has no canon engine%/turns of its
# own (only "0% chance... cannot escape under any circumstances"); NO-CANON:
# reuses Strong's 90%/20 cost profile since it is the adjacent guarded tier
# and canon gives no third number to pin -- flagged for bless.
TRACTOR_BREAK_PARAMS = {
    "weak":         {"success_chance": 0.25, "engine_pct": 75, "turns": 10},
    "strong":       {"success_chance": 0.10, "engine_pct": 90, "turns": 20},
    "immobilizing": {"success_chance": 0.0,  "engine_pct": 90, "turns": 20},
}

# Canon "Schema impact" table default (station-protection.md:182) -- reused
# here as the JSONB fallback (see class docstring above) rather than a
# column default.
DEFAULT_WANTED_THRESHOLD = -500

# NO-CANON pins (flagged for bless): canon states a "10-25% of cargo value"
# RANGE for the surrender fine without specifying resolution, and a
# "reputation hit" with no magnitude at all.
SURRENDER_FINE_PCT = 0.15
# Matches PersonalReputationService.REPUTATION_TRIGGERS["attack_innocent"]
# (-100) -- surrendering a stolen/wanted ship to station security is treated
# as the same order of misconduct as attacking an innocent player.
SURRENDER_REPUTATION_PENALTY = -100
SURRENDER_REPUTATION_REASON = "station_tractor_surrender"


def _break_attempt_cost_label(strength: str) -> str:
    if strength == "immobilizing":
        return "impossible"
    p = TRACTOR_BREAK_PARAMS[strength]
    return f"{p['engine_pct']}-pct engine + {p['turns']} turns"


def _lock_payload(station: Station, ship: Ship, reason: str, strength: str) -> Dict[str, Any]:
    return {
        "error": "ERR_STATION_TRACTOR_LOCK",
        "station_id": str(station.id),
        "ship_id": str(ship.id),
        "tractor_strength": strength,
        "reason": reason,
        "break_attempt_cost": _break_attempt_cost_label(strength),
    }


def tractor_lock_reason(station: Station, ship: Ship, player: Player) -> Optional[str]:
    """Canon deny-list check (station-protection.md:81-83), evaluated in
    canon order; the first hit wins. None -> the pilot is clean and Ship.
    stolen_status/personal_reputation/deny-list all pass. Pure/DB-free."""
    if bool(getattr(ship, "stolen_status", False)):
        return "stolen_ship"
    sec = station.security if isinstance(station.security, dict) else {}
    try:
        threshold = int(sec.get("wanted_threshold", DEFAULT_WANTED_THRESHOLD))
    except (TypeError, ValueError):
        threshold = DEFAULT_WANTED_THRESHOLD
    if (player.personal_reputation or 0) < threshold:
        return "wanted_pilot"
    deny_list = {str(pid) for pid in (sec.get("deny_list_player_ids") or [])}
    if str(player.id) in deny_list:
        return "deny_listed"
    return None


def get_tractor_lock_status(station: Station, ship: Ship) -> Dict[str, Any]:
    """Public read: whether `ship` is currently tractor-locked at `station`.
    Pure/DB-free -- no row lock needed for a read."""
    sec = station.security if isinstance(station.security, dict) else {}
    locks = sec.get("tractor_locks") or {}
    lock = locks.get(str(ship.id))
    if not isinstance(lock, dict):
        return {"locked": False}
    strength = TRACTOR_STRENGTH_BY_TIER.get(station.security_level, "weak")
    return {
        "locked": True,
        "reason": lock.get("reason"),
        "tractor_strength": strength,
        "break_attempts": int(lock.get("break_attempts", 0) or 0),
        "break_attempt_cost": _break_attempt_cost_label(strength),
    }


def check_tractor_lock(
    db: Session, station: Station, ship: Ship, player: Player, now: Optional[datetime] = None
) -> Optional[Dict[str, Any]]:
    """Guarantee #2 undock gate. Called from the undock route BEFORE any
    turn charge (mirrors Guarantee #1's own "reject before any turn charge"
    discipline in combat_service.py). Returns the ERR_STATION_TRACTOR_LOCK
    payload if the ship must be held (creating or re-surfacing its lock
    record on station.security), or None for a clean undock -- a clean
    pilot at a sub-basic-tier station takes this branch and the JSONB is
    never touched. FLUSH-ONLY; the caller (undock route) owns the
    transaction and must commit before raising the 4xx so the lock record
    persists even though the request itself is rejected.

    Once locked, the reason/strength FREEZE at the lock-time snapshot --
    re-evaluating tractor_lock_reason on every repeat undock attempt would
    let a pilot's improving reputation silently unlock them without ever
    breaking free or surrendering, which canon never describes as a release
    path."""
    if station.security_rank < SECURITY_TIER_PROTECTED_MIN_RANK:
        return None

    station = _lock_station(db, station.id)
    strength = TRACTOR_STRENGTH_BY_TIER.get(station.security_level, "weak")
    sec = _security(station)
    locks = sec.setdefault("tractor_locks", {})
    existing = locks.get(str(ship.id))
    if isinstance(existing, dict):
        return _lock_payload(station, ship, existing.get("reason", "stolen_ship"), strength)

    reason = tractor_lock_reason(station, ship, player)
    if reason is None:
        return None

    now = now or datetime.now(UTC)
    locks[str(ship.id)] = {
        "reason": reason,
        "locked_at": now.isoformat(),
        "break_attempts": 0,
    }
    flag_modified(station, "security")
    db.flush()
    logger.info(
        "Tractor lock engaged: ship %s at station %s reason=%s tier=%s strength=%s",
        ship.id, station.id, reason, station.security_level, strength,
    )
    return _lock_payload(station, ship, reason, strength)


def attempt_tractor_break(
    db: Session, station: Station, player: Player, now: Optional[datetime] = None
) -> Dict[str, Any]:
    """Guarantee #2 break-free attempt (station-protection.md:97-101,
    107-109). Requires an existing lock on the player's CURRENT ship at
    THIS station. Spends the tier's pinned turn cost REGARDLESS of outcome
    ("Failed breaks cost the turns regardless") via ONE random.random() roll
    against the tier's pinned per-attempt success_chance (Immobilizing's 0.0
    can structurally never beat `< 0.0` -- always fails, no special case
    needed). On success, completes the undock the pilot was originally
    attempting (mirrors trading.py's undock_from_port completion steps) and
    clears the lock; on failure, the lock persists with break_attempts
    incremented. Raises StationSecurityError (400) on no ship / no active
    lock / insufficient turns."""
    now = now or datetime.now(UTC)
    ship = getattr(player, "current_ship", None)
    if ship is None:
        raise StationSecurityError(400, "You have no active ship")

    station = _lock_station(db, station.id)
    sec = station.security if isinstance(station.security, dict) else {}
    locks = sec.get("tractor_locks") or {}
    lock = locks.get(str(ship.id))
    if not isinstance(lock, dict):
        raise StationSecurityError(400, "Your ship is not tractor-locked at this station")

    strength = TRACTOR_STRENGTH_BY_TIER.get(station.security_level, "weak")
    params = TRACTOR_BREAK_PARAMS[strength]
    cost = params["turns"]

    player = _lock_owner(db, player.id)
    # ADR-0004: continuous lazy regen before the affordability check (same
    # discipline as trading.py's undock_from_port).
    from src.services.turn_service import regenerate_turns, spend_turns
    regenerate_turns(db, player)
    if (player.turns or 0) < cost:
        raise StationSecurityError(
            400, f"Breaking free needs {cost} turns; you have {player.turns}"
        )

    spend_turns(player, cost)
    success = random.random() < params["success_chance"]

    if success:
        del locks[str(ship.id)]
        flag_modified(station, "security")

        # Complete the undock the pilot was originally attempting -- mirrors
        # trading.py's undock_from_port completion steps exactly (slip
        # release, docked flags, haggle-session clear). Duplicated rather
        # than imported/shared: this codebase's established per-service
        # convention (see escape_pod_service.py's own graph-helper
        # duplication note) for a handful of lines that live at a route
        # boundary this service doesn't own.
        from src.services import docking_service
        docking_service.release(db, None, player)
        player.is_docked = False
        player.current_port_id = None
        try:
            from src.services.haggle_service import clear_docking_session_haggles
            clear_docking_session_haggles(player)
        except Exception:
            logger.warning("clearing docking-session haggle state failed", exc_info=True)

        db.flush()
        logger.info(
            "Tractor break SUCCESS: ship %s escaped %s-tier station %s (attempt #%s)",
            ship.id, strength, station.id, int(lock.get("break_attempts", 0)) + 1,
        )
        return {
            "success": True,
            "outcome": "escaped",
            "tractor_strength": strength,
            "turns_spent": cost,
            "turns_remaining": player.turns,
        }

    lock["break_attempts"] = int(lock.get("break_attempts", 0) or 0) + 1
    flag_modified(station, "security")
    db.flush()
    logger.info(
        "Tractor break FAILED: ship %s attempt #%s at %s-tier station %s",
        ship.id, lock["break_attempts"], strength, station.id,
    )
    return {
        "success": False,
        "outcome": "still_locked",
        "tractor_strength": strength,
        "turns_spent": cost,
        "turns_remaining": player.turns,
        "break_attempts": lock["break_attempts"],
    }


def _cargo_credit_value(ship: Ship) -> int:
    """Sum of ship.cargo.contents valued at commodity_economy.base_price
    per unit. Unknown commodities price at 0 (base_price's own convention)."""
    from src.core.commodity_economy import base_price
    contents = (ship.cargo or {}).get("contents") or {}
    total = 0
    for commodity, qty in contents.items():
        try:
            total += int(qty) * base_price(commodity)
        except (TypeError, ValueError):
            continue
    return total


def _notify_registered_owner(ship: Ship, ship_name: str, station: Station, surrendering_player_id) -> None:
    """Best-effort WebSocket notice to the ship's registered/legal owner
    (mirrors docking_service._notify_bumped's try/except pattern -- a
    missing/quiet socket must never fail the surrender transaction).
    Skipped when there is no distinct owner to notify (the surrendering
    pilot IS the registered owner, or the ship predates the registry)."""
    owner_id = getattr(ship, "registered_owner_id", None) or getattr(ship, "owner_id", None)
    if owner_id is None or owner_id == surrendering_player_id:
        return
    try:
        import asyncio

        from src.services.websocket_service import connection_manager

        loop = asyncio.get_running_loop()
        loop.create_task(connection_manager.send_personal_message(str(owner_id), {
            "type": "ship_recovered_impounded",
            "message": (
                f"Your ship '{ship_name}' was surrendered to station security at "
                f"{station.name} and is being held for retrieval."
            ),
            "ship_id": str(ship.id),
            "station_id": str(station.id),
        }))
    except Exception:
        logger.debug("Skipped WebSocket impound notice (no loop or socket)", exc_info=True)


def surrender_tractor_locked_ship(
    db: Session, station: Station, player: Player, now: Optional[datetime] = None
) -> Dict[str, Any]:
    """Guarantee #2 surrender option (station-protection.md:97-99):
    "Abandon the ship at the station; security takes custody." Marks the
    ship Ship.is_abandoned/abandoned_at -- the SAME derelict marker
    escape_pod_service.eject_to_escape_pod uses (NOT destruction/insurance;
    the hull stays intact, held for a future retract/transfer flow that is
    Wave-2 per ship_registry_service's own docstring and not built here).
    Fines the pilot SURRENDER_FINE_PCT of cargo value, applies
    SURRENDER_REPUTATION_PENALTY personal_reputation via
    PersonalReputationService, logs an IMPOUNDED ShipRegistry event, reseats
    the pilot into an Escape Pod at the SAME sector (they stay docked --
    they never left), and best-effort notifies the ship's registered owner.

    Deliberately does NOT build canon's "serious violations result in
    arrest and detention... (Design-only)" clause -- that clause is itself
    marked design-only in canon and there is no detention/turn-freeze
    mechanic anywhere in this codebase to hook it to."""
    now = now or datetime.now(UTC)
    station = _lock_station(db, station.id)
    player = _lock_owner(db, player.id)

    ship = getattr(player, "current_ship", None)
    if ship is None:
        raise StationSecurityError(400, "You have no active ship")
    if ship.type == ShipType.ESCAPE_POD:
        raise StationSecurityError(400, "An Escape Pod cannot be surrendered")

    sec = station.security if isinstance(station.security, dict) else {}
    locks = sec.get("tractor_locks") or {}
    lock = locks.get(str(ship.id))
    if not isinstance(lock, dict):
        raise StationSecurityError(400, "Your ship is not tractor-locked at this station")

    fine = int(_cargo_credit_value(ship) * SURRENDER_FINE_PCT)
    player.credits = max(0, (player.credits or 0) - fine)

    from src.services.personal_reputation_service import PersonalReputationService
    PersonalReputationService(db).adjust_reputation(
        player.id, SURRENDER_REPUTATION_PENALTY, SURRENDER_REPUTATION_REASON
    )

    del locks[str(ship.id)]
    flag_modified(station, "security")

    abandoned_ship_id = ship.id
    abandoned_ship_name = ship.name
    ship.is_abandoned = True
    ship.abandoned_at = now
    ship.current_pilot_id = None

    try:
        from src.models.ship_registry import RegistryEventType
        from src.services.ship_registry_service import append_registry_event
        append_registry_event(
            db, ship=ship, event_type=RegistryEventType.IMPOUNDED,
            original_owner_id=getattr(ship, "registered_owner_id", None),
            previous_owner_id=getattr(ship, "registered_owner_id", None),
            acting_party_id=player.id,
            port_id=station.id,
        )
    except Exception:
        logger.warning("ShipRegistry IMPOUNDED event failed for ship %s", ship.id, exc_info=True)

    from src.services.ship_service import ShipService
    escape_pod = ShipService(db)._ensure_escape_pod(player, player.current_sector_id)
    player.current_ship_id = escape_pod.id

    db.flush()

    _notify_registered_owner(ship, abandoned_ship_name, station, player.id)

    logger.info(
        "Tractor surrender: ship %s abandoned at station %s by player %s (fine=%s cr, rep=%s)",
        abandoned_ship_id, station.id, player.id, fine, SURRENDER_REPUTATION_PENALTY,
    )
    return {
        "success": True,
        "outcome": "surrendered",
        "abandoned_ship_id": str(abandoned_ship_id),
        "abandoned_ship_name": abandoned_ship_name,
        "fine": fine,
        "reputation_penalty": SURRENDER_REPUTATION_PENALTY,
        "credits": player.credits,
        "new_ship_id": str(escape_pod.id),
    }

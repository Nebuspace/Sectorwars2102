"""Station Service — station lifecycle operations (destruction & recovery).

CANON: FEATURES/economy/trading.md § "Destruction & recovery".

A destroyed station enters a 24-(canonical-)hour automated rebuild cycle.
During recovery:
  * the station is NON-FUNCTIONAL — no docking, no trading, no services;
  * defenses are inactive;
  * cargo, drones, and credits in storage are PRESERVED.
After 24 canonical hours the station AUTO-REBUILDS at reduced capacity
(50% of pre-destruction commodity inventory; defenses must be re-purchased).

This module is the canonical home for that lifecycle. It owns two halves:

  1. ``mark_station_destroyed`` — the DESTRUCTION operation: flips the
     existing ``Station.is_destroyed`` flag, sets the existing
     ``Station.recovery_time`` DateTime (the 24-canonical-hour rebuild
     deadline), and SNAPSHOTS pre-destruction commodity quantities into the
     additive ``Station.ownership`` JSONB so the 50% rebuild is
     deterministic. Stored cargo / drones / credits (treasury_balance,
     defense_fund / operating_fund ledgers, defense drone counts) are left
     UNTOUCHED — only the rebuild-able commodity inventory is reduced, and
     only at recovery time.

  2. ``recover_station`` / ``is_recovery_due`` — the RECOVERY half, driven
     by the NPC scheduler's periodic station-recovery sweep
     (npc_scheduler_service._run_station_recovery_sync). Rebuilds the
     commodity inventory to 50% of the destruction-time snapshot, clears
     the destroyed flag + timer, and zeroes the active defense drones (canon:
     "defenses must be re-purchased").

  3. ``is_station_functional`` — the single authoritative "can players
     interact with this station?" predicate. The docking / trading routes
     and ``trading_service.can_player_trade`` SHOULD call this to refuse
     docking/trading for the 24h window; wiring those call sites lives in
     their own lanes (this lane owns only this service + the scheduler
     sweep) and is flagged as a follow-up.

STORAGE — additive JSONB, NO migration: the destruction state reuses the
existing ``Station.is_destroyed`` (Boolean) + ``Station.recovery_time``
(DateTime) columns (model-only until now) and adds two keys under the
existing nullable ``Station.ownership`` JSONB:
  * ownership['destroyed_at']         — ISO wall-clock destruction anchor
  * ownership['destroyed_inventory']  — {commodity: pre-destruction qty}
A re-run of the recovery sweep after rebuild finds is_destroyed False and
no-ops; the snapshot keys are removed on rebuild so a future destruction
re-snapshots cleanly. No new column, no DDL.

TIME: the 24h window is CANONICAL hours (GAME_TIME_SCALE-aware), matching
every other player-facing timer in the scheduler (recruit stage, senior
tenure, genesis formation). ``recovery_time`` is the absolute wall-clock
deadline (game_time.scaled_deadline(24)), so on dev (scale 144) a station
rebuilds 10 wall-clock minutes after destruction and the proof is
observable. canonical_hours_since(destroyed_at) is the restart-proof
cross-check.

LOCK ORDER: station row first (this service only touches the station row;
callers that also touch player rows must take the station lock first).
"""

import logging
from datetime import datetime, UTC
from typing import Any, Dict, Optional

from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from src.core import game_time
from src.models.station import Station, StationStatus

logger = logging.getLogger(__name__)


# CANON (FEATURES/economy/trading.md § Destruction & recovery): the rebuild
# cycle is 24 hours and the station rebuilds at 50% of pre-destruction
# commodity inventory. These two numbers are canonical — NOT NO-CANON.
STATION_RECOVERY_HOURS = 24.0
STATION_REBUILD_INVENTORY_FRACTION = 0.5


# JSONB sub-keys on Station.ownership (additive; no migration).
_DESTROYED_AT_KEY = "destroyed_at"
_DESTROYED_INVENTORY_KEY = "destroyed_inventory"


def _ledger(station: Station) -> Dict[str, Any]:
    """Mutable handle on station.ownership (created if absent). Caller MUST
    flag_modified(station, 'ownership') after mutating. Mirrors
    port_ownership_service._ledger so the two services share one JSONB
    convention on the same column."""
    if station.ownership is None:
        station.ownership = {}
    return station.ownership


def is_station_functional(station: Station) -> bool:
    """Authoritative predicate: may players dock / trade / use services here?

    False during the 24h destroyed-recovery window (no docking, no trading,
    no services, defenses inactive — all of trading.md's recovery clauses
    flow from this one flag). True for an operational station.

    Callers in the docking / trading lanes SHOULD gate on this (follow-up:
    those routes are not in this lane). It deliberately reads ONLY the
    durable ``is_destroyed`` flag so it is cheap and side-effect-free."""
    return not bool(getattr(station, "is_destroyed", False))


def mark_station_destroyed(
    db: Session, station: Station, now: Optional[datetime] = None
) -> Dict[str, Any]:
    """Mark ``station`` destroyed and start its 24-canonical-hour rebuild cycle.

    Effects (all on the station row only — caller owns the transaction, NO
    commit here):
      * is_destroyed = True (non-functional: no docking/trading/services;
        defenses inactive — every recovery clause keys off this flag);
      * status = ABANDONED (the StationStatus closest to "ruined / awaiting
        rebuild"; flipped back to OPERATIONAL on recovery);
      * recovery_time = absolute wall-clock deadline 24 CANONICAL hours out
        (game_time.scaled_deadline → dev-observable at scale 144);
      * ownership['destroyed_at'] = ISO wall-clock anchor (restart-proof
        cross-check);
      * ownership['destroyed_inventory'] = {commodity: pre-destruction
        quantity} snapshot, so the 50% rebuild is DETERMINISTIC regardless
        of any stock ticks that might otherwise run (they don't — a
        destroyed station is excluded from tick candidates).

    PRESERVED (untouched): treasury_balance (credits), ownership defense_fund
    / operating_fund ledgers, defenses['defense_drones'] / patrol_ships
    (drones in storage), and the commodity inventory itself (it is NOT
    halved here — it is rebuilt from the snapshot at recovery, so the
    pre-destruction value is the basis even though the live stock is left
    as-is during the window).

    Idempotent: marking an already-destroyed station refreshes nothing and
    returns status 'already_destroyed' (it does NOT re-snapshot — that would
    overwrite the original pre-destruction inventory with the live, possibly
    already-reduced one).

    Returns a summary dict. Raises NOTHING for the happy path; the caller is
    responsible for having locked the station row (with_for_update) before
    calling — this matches the global station-then-player lock order.
    """
    now = now or datetime.now(UTC)

    if bool(getattr(station, "is_destroyed", False)):
        # Already in a recovery cycle — do not clobber the original snapshot.
        return {
            "station_id": str(station.id),
            "status": "already_destroyed",
            "recovery_time": station.recovery_time.isoformat()
            if station.recovery_time
            else None,
        }

    # Snapshot pre-destruction commodity quantities for a deterministic 50%
    # rebuild. Snapshot ONLY the quantity (the rebuildable inventory); the
    # rest of each commodity sub-doc (capacity, base_price, production_rate,
    # buy/sell flags) is preserved live on the station and untouched.
    commodities = station.commodities or {}
    snapshot: Dict[str, int] = {}
    for name, data in commodities.items():
        if isinstance(data, dict):
            snapshot[name] = int(data.get("quantity", 0) or 0)

    ledger = _ledger(station)
    ledger[_DESTROYED_AT_KEY] = now.isoformat()
    ledger[_DESTROYED_INVENTORY_KEY] = snapshot
    flag_modified(station, "ownership")

    station.is_destroyed = True
    station.status = StationStatus.ABANDONED
    station.last_attacked = now
    # Absolute wall-clock deadline for the 24 CANONICAL-hour window. SQL
    # candidate filters (recovery_time <= now) read this directly; the
    # destroyed_at anchor is the restart-proof canonical-hours cross-check.
    station.recovery_time = game_time.scaled_deadline(STATION_RECOVERY_HOURS, now)

    db.flush()

    logger.info(
        "Station %s (%s) destroyed — non-functional until %s "
        "(24 canonical h); %d commodities snapshotted for 50%% rebuild",
        station.id,
        station.name,
        station.recovery_time.isoformat() if station.recovery_time else "?",
        len(snapshot),
    )

    return {
        "station_id": str(station.id),
        "status": "destroyed",
        "recovery_time": station.recovery_time.isoformat()
        if station.recovery_time
        else None,
        "snapshot_commodities": len(snapshot),
    }


def is_recovery_due(station: Station, now: Optional[datetime] = None) -> bool:
    """True iff ``station`` is destroyed AND its 24-canonical-hour window has
    elapsed, so the recovery sweep should rebuild it now.

    Uses the canonical-hours cross-check against the durable
    ownership['destroyed_at'] anchor (restart-proof: the process-relative
    clock can reset, the stored anchor cannot). Falls back to the absolute
    recovery_time deadline if the anchor is missing (e.g. a station marked
    destroyed by a path that predates this service)."""
    if not bool(getattr(station, "is_destroyed", False)):
        return False

    now = now or datetime.now(UTC)

    destroyed_at_raw = (station.ownership or {}).get(_DESTROYED_AT_KEY)
    if destroyed_at_raw:
        try:
            destroyed_at = datetime.fromisoformat(destroyed_at_raw)
            return (
                game_time.canonical_hours_since(destroyed_at, now)
                >= STATION_RECOVERY_HOURS
            )
        except (ValueError, TypeError):
            pass  # corrupt anchor — fall through to the deadline column

    # Fallback: the absolute wall-clock deadline already encodes the scaled
    # window. recovery_time None → never recovers via this path (defensive;
    # mark_station_destroyed always sets it).
    if station.recovery_time is None:
        return False
    deadline = station.recovery_time
    if deadline.tzinfo is None:
        deadline = deadline.replace(tzinfo=UTC)
    return now >= deadline


def recover_station(
    db: Session, station: Station, now: Optional[datetime] = None
) -> Dict[str, Any]:
    """Auto-rebuild a destroyed station at 50% of its pre-destruction
    commodity inventory (CANON). Caller MUST have locked the station row and
    SHOULD have confirmed is_recovery_due — but this is defensive and
    re-checks the destroyed flag so a racing caller can't double-rebuild.

    Effects (station row only — caller owns the transaction, NO commit):
      * each commodity quantity -> floor(snapshot_qty * 0.5), clamped to the
        commodity's own capacity (defensive; 50% of a stored qty can never
        exceed capacity, but the clamp protects against a corrupt snapshot);
      * is_destroyed = False, recovery_time = None, status = OPERATIONAL;
      * defenses active-drone pools (defense_drones, patrol_ships) reset to 0
        — CANON: "defenses must be re-purchased". The defensive STRUCTURE
        stats (hull_armor / shield_pool / defensive_fire) fall back to the
        model defaults and are NOT touched (they make the rebuilt station a
        deterrent again, which is correct — the canon line is about the
        re-PURCHASED drone/grid defenses, not the structural HP);
      * snapshot keys removed from ownership so a future destruction
        re-snapshots cleanly; preserved buckets (defense_fund / operating_
        fund / treasury) are left intact.

    Idempotent: an already-recovered (not-destroyed) station no-ops with
    status 'not_destroyed'.

    Returns a summary dict."""
    now = now or datetime.now(UTC)

    if not bool(getattr(station, "is_destroyed", False)):
        return {"station_id": str(station.id), "status": "not_destroyed"}

    snapshot: Dict[str, Any] = dict(
        (station.ownership or {}).get(_DESTROYED_INVENTORY_KEY, {}) or {}
    )

    commodities = station.commodities or {}
    rebuilt = 0
    for name, data in commodities.items():
        if not isinstance(data, dict):
            continue
        if name in snapshot:
            pre_qty = int(snapshot.get(name, 0) or 0)
        else:
            # No snapshot entry (commodity added after destruction, or a
            # pre-service destruction with no snapshot): rebuild from the
            # live quantity so the station still comes back at 50%.
            pre_qty = int(data.get("quantity", 0) or 0)
        capacity = int(data.get("capacity", pre_qty) or pre_qty)
        new_qty = int(pre_qty * STATION_REBUILD_INVENTORY_FRACTION)
        data["quantity"] = max(0, min(new_qty, capacity))
        data["current_price"] = data.get("current_price", data.get("base_price", 0))
        rebuilt += 1
    flag_modified(station, "commodities")

    # CANON: defenses must be re-purchased — zero the active drone/patrol
    # pools. Structural defense stats fall back to model defaults (the
    # resolver .get()s them), keeping the rebuilt station a deterrent.
    defenses = station.defenses or {}
    if isinstance(defenses, dict):
        if "defense_drones" in defenses:
            defenses["defense_drones"] = 0
        if "patrol_ships" in defenses:
            defenses["patrol_ships"] = 0
        # active-toggle defenses reset off; they are re-purchased
        if "auto_turrets" in defenses:
            defenses["auto_turrets"] = False
        if "defense_grid" in defenses:
            defenses["defense_grid"] = False
        station.defenses = defenses
        flag_modified(station, "defenses")

    # Clear the destruction state.
    station.is_destroyed = False
    station.recovery_time = None
    station.status = StationStatus.OPERATIONAL

    # Remove the snapshot keys (preserve all other ownership buckets).
    if station.ownership:
        station.ownership.pop(_DESTROYED_AT_KEY, None)
        station.ownership.pop(_DESTROYED_INVENTORY_KEY, None)
        flag_modified(station, "ownership")

    db.flush()

    logger.info(
        "Station %s (%s) auto-rebuilt at 50%% inventory (%d commodities); "
        "defenses zeroed (must be re-purchased)",
        station.id,
        station.name,
        rebuilt,
    )

    return {
        "station_id": str(station.id),
        "status": "recovered",
        "commodities_rebuilt": rebuilt,
    }

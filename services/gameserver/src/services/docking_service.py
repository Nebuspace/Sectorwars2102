"""
Docking slip allocation service.

Canon reference: FEATURES/economy/docking-slips (sw2102-docs). Stations expose
a finite TRANSIENT slip pool; a docked ship occupies one slip. When the pool
is full, players join a FIFO wait queue OR pay 5x the docking fee to bump the
longest-tenured occupant with >= 4 canonical hours of tenure. Tenure is
measured through src.core.game_time (GAME_TIME_SCALE compresses time on dev).

REPUTATION GATE: Station.reputation_threshold is an integer representing the
minimum faction reputation value (Reputation.current_value) a player must have
with the station's controlling faction before they are allowed to dock.
  - If the station has no faction_affiliation the gate is skipped.
  - If the player has no reputation record they are treated as 0 (neutral).
  - On failure, acquire() returns {'status': 'reputation_denied', ...} without
    queuing or granting a slip. The route should translate this to HTTP 403.

LONG-TERM MOORING: canon defines a second slip class, 'long_term', for
multi-day stays (1–30 days, 200 cr/day). The slip count for long-term slips is
a separate pool from transient slips (see long_term_capacity_for). The service
tracks long-term occupancies using the same DockingSlipOccupancy table with
slip_class='long_term'. The mooring functions acquire_long_term() and
release_long_term() mirror the transient equivalents; they do NOT participate
in the transient bump mechanism.

Concurrency / lock-ordering contract (documented to avoid deadlocks):
  1. The STATION row is locked first (SELECT ... FOR UPDATE) by `acquire` and
     `bump`. This serializes all slip grants/bumps per station, so occupancy
     counts can never over-commit a slip.
  2. When PLAYER rows must be locked (bump charges the bumper and evicts the
     occupant), they are locked in ASCENDING player-id order. Re-locking a row
     already held by this transaction is a no-op, so callers that pre-locked
     one of the players remain safe.
  3. No function here commits; the calling route owns the transaction and
     issues a single commit.

BACKFILL NOTE: players docked before this feature have no occupancy row. The
occupancy table is the source of truth for slot consumption — legacy docked
players simply don't hold slips (acceptable), and `release` tolerates a
missing row silently.
"""
import logging
from typing import Any, Dict, List, Optional, Tuple
from uuid import UUID

from sqlalchemy.orm import Session

from src.core import game_time
from src.models.docking import DockingQueueEntry, DockingSlipOccupancy
from src.models.player import Player
from src.models.station import Station

logger = logging.getLogger(__name__)

# Canon: long-term mooring rental (FEATURES/economy/docking-slips §Slip rental
# fee structure). All long-term mooring costs the same regardless of station
# class; the scarcity comes from the small pool, not the price tier.
LONG_TERM_MOORING_RATE_PER_DAY = 200  # cr/day
LONG_TERM_MOORING_MAX_DAYS = 30       # canonical upper limit per booking

# Canon: bump costs 5x the docking fee; occupant must have >= 4 canonical
# hours of tenure to be bumpable.
BUMP_COST_MULTIPLIER = 5
BUMP_MIN_TENURE_HOURS = 4.0


def _realize_fee(db: Session, station: Station, fee: int) -> None:
    """Route a collected station fee through the canon 40/30/30 revenue split
    (defense_fund / operating_fund / owner-treasury) instead of crediting the
    owner treasury at 100%.

    Delegates to port_ownership_service.realize_port_revenue, which owns the
    split, re-locks the station row (a no-op here — the caller already holds the
    station lock per this module's lock-ordering contract), and flushes. The
    import is lazy to avoid a service-layer import cycle, mirroring the in-
    function import pattern used by _notify_bumped / _player_faction_rep_for_station.

    Defensive: if the revenue hook is unavailable or raises, fall back to the
    legacy 100%-to-treasury credit so a live docking path can never break.
    """
    try:
        from src.services.port_ownership_service import realize_port_revenue

        realize_port_revenue(db, station, int(fee))
    except Exception:
        logger.warning(
            "realize_port_revenue failed for station=%s fee=%s; "
            "falling back to direct treasury credit",
            getattr(station, "id", None),
            fee,
            exc_info=True,
        )
        station.treasury_balance = (station.treasury_balance or 0) + int(fee)


class BumpError(Exception):
    """Raised when a bump attempt is invalid; carries an HTTP status hint."""

    def __init__(self, status_code: int, detail: str):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


class ReputationGateError(Exception):
    """Raised when docking is denied because the player's faction reputation
    is below the station's threshold. Carries an HTTP 403 hint."""

    def __init__(self, detail: str, rep_value: int, threshold: int):
        super().__init__(detail)
        self.status_code = 403
        self.detail = detail
        self.rep_value = rep_value
        self.threshold = threshold


# ---------------------------------------------------------------------------
# Reputation gate — FEATURES/economy/docking-slips §Reputation gate
# ---------------------------------------------------------------------------

def _player_faction_rep_for_station(db: Session, player: Player, station: Station) -> int:
    """Return the player's current_value toward the station's controlling
    faction, or 0 if the station is unaffiliated or no record exists.

    Mirrors the pattern in construction_service._faction_rep_tier and
    trading_service (all use Faction.name + Reputation.current_value).
    """
    faction_name = getattr(station, "faction_affiliation", None)
    if not faction_name:
        return 0
    try:
        from src.models.faction import Faction
        from src.models.reputation import Reputation

        faction = db.query(Faction).filter(Faction.name == faction_name).first()
        if faction is None:
            return 0
        rep = (
            db.query(Reputation)
            .filter(
                Reputation.player_id == player.id,
                Reputation.faction_id == faction.id,
            )
            .first()
        )
        return rep.current_value if rep is not None else 0
    except Exception:
        logger.warning(
            "reputation gate lookup failed for player=%s station=%s; defaulting to 0",
            player.id,
            station.id,
            exc_info=True,
        )
        return 0


def check_reputation_gate(
    db: Session, station: Station, player: Player
) -> Tuple[bool, int, int]:
    """Check whether `player` meets `station`'s reputation_threshold.

    Returns (allowed, player_rep_value, threshold).
      allowed=True  — player may dock
      allowed=False — player is denied; route should surface HTTP 403

    Rationale: reputation_threshold lives on Station (nullable=False, default 0).
    A threshold of 0 (the default) means anyone can dock — only stations that
    have been explicitly configured with a positive threshold actually gate.
    """
    threshold = getattr(station, "reputation_threshold", 0) or 0
    if threshold <= 0:
        return True, 0, threshold

    rep_value = _player_faction_rep_for_station(db, player, station)
    allowed = rep_value >= threshold
    return allowed, rep_value, threshold


def slip_capacity_for(station: Station) -> int:
    """Transient slip capacity by station kind (canon table).

    Precedence: tradedock_tier is checked BEFORE is_spacedock, which is
    checked before the station-class buckets.
    """
    tier = getattr(station, "tradedock_tier", None)
    if tier == "A":
        return 24
    if tier == "B":
        return 20
    if getattr(station, "is_spacedock", False):
        return 30
    cls = station.station_class.value if station.station_class is not None else None
    if cls == 0:        # CLASS_0 capital — Starport Prime vs regional Capital
        # Canon (docking-slips §Per-station-class slip counts):
        #   Central Nexus Starport Prime → 200 transient
        #   regional Capital station     →  80 transient
        # Both are CLASS_0; the is_starport_prime flag is the discriminator.
        if getattr(station, "is_starport_prime", False):
            return 200
        return 80
    if cls in (1, 2):
        return 8
    if cls is not None and 3 <= cls <= 6:
        return 12
    if cls is not None and 7 <= cls <= 10:
        return 20
    if cls == 11:
        return 24
    # station_class is non-nullable, so this is defensive only.
    return 12


def long_term_capacity_for(station: Station) -> int:
    """Long-term mooring slip count by station kind (canon table).

    Canon: FEATURES/economy/docking-slips §Per-station-class slip counts.
    Same precedence as slip_capacity_for: tradedock_tier > is_spacedock >
    station_class.  Stations with no long-term slips return 0; acquiring a
    long-term slip at such a station immediately returns 'unavailable'.
    """
    tier = getattr(station, "tradedock_tier", None)
    if tier == "A":
        return 8
    if tier == "B":
        return 8
    if getattr(station, "is_spacedock", False):
        return 10
    cls = station.station_class.value if station.station_class is not None else None
    if cls == 0:        # CLASS_0 capital — Starport Prime vs regional Capital
        # Canon (docking-slips §Per-station-class slip counts):
        #   Central Nexus Starport Prime → 50 long-term
        #   regional Capital station     → 30 long-term
        # Both are CLASS_0; the is_starport_prime flag is the discriminator.
        # (Fixes the prior bug where every CLASS_0 station returned 50.)
        if getattr(station, "is_starport_prime", False):
            return 50
        return 30
    if cls in (1, 2):
        return 2
    if cls is not None and 3 <= cls <= 6:
        return 4
    if cls is not None and 7 <= cls <= 10:
        return 6
    if cls == 11:
        return 8
    return 2


def docking_fee_for(station: Station) -> int:
    """Transient docking fee in credits.

    Canon names docking fees (they fund the station treasury) but does not
    specify amounts — this table is the documented interpretation:
    capital CLASS_0 50cr · class 1-2 25 · 3-6 50 · 7-10 100 · 11 150 ·
    spacedock 200 · tradedock 250. Same precedence as slip_capacity_for.
    """
    tier = getattr(station, "tradedock_tier", None)
    if tier in ("A", "B"):
        return 250
    if getattr(station, "is_spacedock", False):
        return 200
    cls = station.station_class.value if station.station_class is not None else None
    if cls == 0:
        return 50
    if cls in (1, 2):
        return 25
    if cls is not None and 3 <= cls <= 6:
        return 50
    if cls is not None and 7 <= cls <= 10:
        return 100
    if cls == 11:
        return 150
    return 50


def occupant_tenure_hours(occupancy: DockingSlipOccupancy, now=None) -> float:
    """Canonical hours this occupancy has held its slip (GAME_TIME_SCALE aware)."""
    return game_time.canonical_hours_since(occupancy.docked_at, now)


def is_bumpable(occupancy: DockingSlipOccupancy, now=None) -> bool:
    """Canon: an occupant becomes bumpable at >= 4 canonical hours of tenure."""
    return occupant_tenure_hours(occupancy, now) >= BUMP_MIN_TENURE_HOURS


def _transient_occupancies(db: Session, station_id) -> List[DockingSlipOccupancy]:
    return (
        db.query(DockingSlipOccupancy)
        .filter(
            DockingSlipOccupancy.station_id == station_id,
            DockingSlipOccupancy.slip_class == "transient",
        )
        .order_by(DockingSlipOccupancy.docked_at.asc())
        .all()
    )


def _queue_entries(db: Session, station_id) -> List[DockingQueueEntry]:
    return (
        db.query(DockingQueueEntry)
        .filter(DockingQueueEntry.station_id == station_id)
        .order_by(DockingQueueEntry.created_at.asc())
        .all()
    )


def _bumpable_summary(db: Session, occupancies: List[DockingSlipOccupancy]) -> List[Dict[str, Any]]:
    """Occupants eligible to be bumped, longest tenure first."""
    out: List[Dict[str, Any]] = []
    for occ in occupancies:
        if not is_bumpable(occ):
            continue
        occupant = db.query(Player).filter(Player.id == occ.player_id).first()
        name = None
        if occupant is not None:
            name = occupant.nickname or (occupant.user.username if occupant.user else None)
        out.append({
            "player_id": str(occ.player_id),
            "name": name or "Unknown",
            "tenure_hours": round(occupant_tenure_hours(occ), 2),
        })
    out.sort(key=lambda b: b["tenure_hours"], reverse=True)
    return out


def acquire(db: Session, station: Station, player: Player, ship_id: Optional[UUID] = None) -> Dict[str, Any]:
    """Try to claim a transient slip for `player` at `station`.

    Locks the station row to serialize slot grants, then counts occupancy
    rows (the source of truth — legacy docked players without rows do not
    consume slips). Does NOT commit.

    Returns one of:
      {'status': 'granted', 'occupancy', 'capacity', 'occupied'}
      {'status': 'queued',  'position', 'queue_length', 'capacity',
       'occupied', 'bumpable'}   — player is (already) in the FIFO queue
      {'status': 'full',    'capacity', 'occupied', 'queue_length',
       'bumpable'}               — full and player is not queued

    Queue fairness: a free slot is granted only if the queue is empty or the
    player is head-of-queue (consuming their entry). A queued non-head player
    — or any walk-up while others are waiting — gets a position response
    instead of jumping the line.
    """
    # Lock the station row: serializes all slip grants/bumps for this station.
    station = db.query(Station).filter(Station.id == station.id).with_for_update().first()

    # Reputation gate: check AFTER the station lock so the threshold we read
    # is current. Only gates positive thresholds — threshold=0 (default) is
    # always open. Does NOT queue the player; a denied player is turned away.
    allowed, rep_value, threshold = check_reputation_gate(db, station, player)
    if not allowed:
        return {
            "status": "reputation_denied",
            "rep_value": rep_value,
            "threshold": threshold,
            "detail": (
                f"Docking denied: your standing with this station's faction is {rep_value}; "
                f"minimum required is {threshold}. Improve your reputation to dock here."
            ),
        }

    capacity = slip_capacity_for(station)
    occupancies = _transient_occupancies(db, station.id)
    occupied = len(occupancies)
    queue = _queue_entries(db, station.id)

    # Ghost-head purge: an abandoned head entry (player already docked
    # somewhere, or no longer in this sector) would otherwise block every
    # free slot at this station forever — walk-ups enqueue behind it, the
    # head never returns, and bump refuses because slots are free.
    pruned = False
    while queue:
        head = queue[0]
        head_player = db.query(Player).filter(Player.id == head.player_id).first()
        head_is_stale = (
            head_player is None
            or head_player.is_docked
            or head_player.current_sector_id != station.sector_id
        )
        if head_is_stale and head.player_id != player.id:
            db.delete(head)
            queue.pop(0)
            pruned = True
            continue
        break
    if pruned:
        db.flush()

    my_entry = next((q for q in queue if q.player_id == player.id), None)
    my_position = (queue.index(my_entry) + 1) if my_entry else None

    if occupied < capacity:
        # Fairness gate: only the head of a non-empty queue may take the slot.
        if not queue or (my_entry is not None and my_position == 1):
            # Consume ALL of this player's queue entries galaxy-wide — a
            # granted player must never linger as a ghost head elsewhere
            db.query(DockingQueueEntry).filter(
                DockingQueueEntry.player_id == player.id
            ).delete(synchronize_session=False)
            occupancy = DockingSlipOccupancy(
                station_id=station.id,
                player_id=player.id,
                ship_id=ship_id,
                slip_class="transient",
            )
            db.add(occupancy)
            db.flush()
            return {
                "status": "granted",
                "occupancy": occupancy,
                "capacity": capacity,
                "occupied": occupied + 1,
            }
        # Free slot exists but it belongs to the queue head, not this player.
        if my_entry is not None:
            return {
                "status": "queued",
                "position": my_position,
                "queue_length": len(queue),
                "capacity": capacity,
                "occupied": occupied,
                "bumpable": _bumpable_summary(db, occupancies),
            }
        return {
            "status": "full",
            "capacity": capacity,
            "occupied": occupied,
            "queue_length": len(queue),
            "bumpable": _bumpable_summary(db, occupancies),
        }

    # All slips taken.
    if my_entry is not None:
        return {
            "status": "queued",
            "position": my_position,
            "queue_length": len(queue),
            "capacity": capacity,
            "occupied": occupied,
            "bumpable": _bumpable_summary(db, occupancies),
        }
    return {
        "status": "full",
        "capacity": capacity,
        "occupied": occupied,
        "queue_length": len(queue),
        "bumpable": _bumpable_summary(db, occupancies),
    }


def release(db: Session, station: Optional[Station], player: Player) -> bool:
    """Release the player's slip, if any. Tolerates a missing row silently
    (players docked before this feature never held one). Does NOT commit.
    """
    occupancy = db.query(DockingSlipOccupancy).filter(
        DockingSlipOccupancy.player_id == player.id
    ).first()
    if occupancy is None:
        return False
    db.delete(occupancy)
    return True


def _notify_bumped(user_id, station_name: str) -> None:
    """Best-effort WebSocket notice to the evicted player.

    Imported inside the function and wrapped in try/except, mirroring the
    pattern used by src/api/routes/status.py — a missing/quiet socket must
    never fail the bump transaction.
    """
    try:
        import asyncio
        from src.services.websocket_service import connection_manager

        loop = asyncio.get_running_loop()
        loop.create_task(connection_manager.send_personal_message(str(user_id), {
            "type": "docking_slip_bumped",
            "message": (
                f"Your ship has been bumped from its docking slip at {station_name}. "
                "You have been undocked."
            ),
            "station_name": station_name,
        }))
    except Exception:
        logger.debug("Skipped WebSocket bump notice (no loop or socket)", exc_info=True)


def bump(db: Session, station: Station, bumper: Player, occupant_player_id) -> Dict[str, Any]:
    """Pay 5x the docking fee to evict a long-tenured occupant and take the slot.

    Validates tenure (>= 4 canonical hours via game_time.scaled_elapsed),
    charges the bumper, credits the station treasury, evicts the occupant
    (is_docked=False, current_port_id=None), and grants the freed slip to the
    bumper. Does NOT commit; raises BumpError with an HTTP status hint on any
    validation failure.

    Lock order (deadlock avoidance, see module docstring): station row first,
    then BOTH player rows in ASCENDING player-id order. The bumper's row may
    already be locked by the caller; re-locking it here is a harmless no-op
    within the same transaction.
    """
    # 1. Lock the station row — serializes against acquire() and other bumps.
    station = db.query(Station).filter(Station.id == station.id).with_for_update().first()

    occupancy = db.query(DockingSlipOccupancy).filter(
        DockingSlipOccupancy.station_id == station.id,
        DockingSlipOccupancy.player_id == occupant_player_id,
        DockingSlipOccupancy.slip_class == "transient",
    ).first()
    if occupancy is None:
        raise BumpError(404, "That occupant does not hold a slip at this station")

    if occupancy.player_id == bumper.id:
        raise BumpError(400, "You cannot bump yourself")

    # Bumping is only meaningful when the pool is full.
    capacity = slip_capacity_for(station)
    occupied = len(_transient_occupancies(db, station.id))
    if occupied < capacity:
        raise BumpError(400, "Transient slips are available — dock normally instead of bumping")

    tenure = occupant_tenure_hours(occupancy)
    if tenure < BUMP_MIN_TENURE_HOURS:
        raise BumpError(
            400,
            f"Occupant has only {tenure:.1f} canonical hours of tenure; "
            f"{BUMP_MIN_TENURE_HOURS:g} hours required before they can be bumped",
        )

    # 2. Lock both player rows in ASCENDING player-id order (deadlock avoidance).
    locked: Dict[Any, Player] = {}
    for pid in sorted([bumper.id, occupancy.player_id]):
        row = db.query(Player).filter(Player.id == pid).with_for_update().first()
        if row is not None:
            locked[pid] = row
    bumper = locked.get(bumper.id, bumper)
    occupant = locked.get(occupancy.player_id)
    if occupant is None:
        raise BumpError(404, "Occupant player no longer exists")

    # 3. Charge the bumper 5x the docking fee; fee funds the station treasury.
    fee = docking_fee_for(station)
    cost = fee * BUMP_COST_MULTIPLIER
    if bumper.credits < cost:
        raise BumpError(
            400,
            f"Insufficient credits to bump. Need {cost} (5x the {fee}cr docking fee), "
            f"have {bumper.credits}",
        )
    bumper.credits -= cost
    # Route the bump fee through the canon 40/30/30 split (defense/operating/owner)
    # rather than crediting the owner treasury at 100%.
    _realize_fee(db, station, cost)

    # 4. Evict the occupant. No refund of their original fee (canon is silent;
    #    the eviction is the cost of overstaying).
    occupant.is_docked = False
    occupant.current_port_id = None
    evicted_info = {
        "player_id": str(occupant.id),
        "name": occupant.nickname or (occupant.user.username if occupant.user else "Unknown"),
        "tenure_hours": round(tenure, 2),
    }
    db.delete(occupancy)
    db.flush()
    # Notification deferred to the route AFTER commit — firing it here would
    # tell the occupant they were evicted even if the transaction rolls back.
    evicted_info["_notify_user_id"] = occupant.user_id

    # 5. Grant the freed slip to the bumper; consume any queue entry they held
    #    at this station (they paid to skip the line).
    db.query(DockingQueueEntry).filter(
        DockingQueueEntry.station_id == station.id,
        DockingQueueEntry.player_id == bumper.id,
    ).delete(synchronize_session=False)

    new_occupancy = DockingSlipOccupancy(
        station_id=station.id,
        player_id=bumper.id,
        ship_id=bumper.current_ship_id,
        slip_class="transient",
        fee_paid=cost,
    )
    db.add(new_occupancy)
    db.flush()

    return {
        "occupancy": new_occupancy,
        "evicted": evicted_info,
        "cost": cost,
        "fee": fee,
        "capacity": capacity,
        "occupied": occupied,  # net unchanged: one out, one in
    }


# ---------------------------------------------------------------------------
# Long-term mooring — FEATURES/economy/docking-slips §Long-term mooring
# ---------------------------------------------------------------------------

def acquire_long_term(
    db: Session,
    station: Station,
    player: Player,
    days: int,
    ship_id: Optional[UUID] = None,
) -> Dict[str, Any]:
    """Claim a long-term mooring slip for `player` at `station`.

    Canon: long-term slips are separate from transient slips; they do NOT
    participate in the bump mechanism. The player pays `days` * 200 cr upfront
    (canon: 200 cr/day, optional pre-book). Reputation gate is applied.

    Returns one of:
      {'status': 'granted',             'occupancy', 'capacity', 'occupied',
       'days', 'fee_paid'}
      {'status': 'full',                'capacity', 'occupied'}
      {'status': 'unavailable',         'detail'}   — station has 0 LT slips
      {'status': 'reputation_denied',   'rep_value', 'threshold', 'detail'}
      {'status': 'insufficient_credits', 'need', 'have'}

    Does NOT commit; the calling route owns the transaction.
    """
    if days < 1 or days > LONG_TERM_MOORING_MAX_DAYS:
        return {
            "status": "invalid_days",
            "detail": f"Long-term mooring requires 1–{LONG_TERM_MOORING_MAX_DAYS} days; got {days}",
        }

    # Lock the station row first (same ordering contract as acquire/bump).
    station = db.query(Station).filter(Station.id == station.id).with_for_update().first()

    # Reputation gate.
    allowed, rep_value, threshold = check_reputation_gate(db, station, player)
    if not allowed:
        return {
            "status": "reputation_denied",
            "rep_value": rep_value,
            "threshold": threshold,
            "detail": (
                f"Docking denied: your standing with this station's faction is {rep_value}; "
                f"minimum required is {threshold}."
            ),
        }

    capacity = long_term_capacity_for(station)
    if capacity == 0:
        return {
            "status": "unavailable",
            "detail": f"{station.name} has no long-term mooring slips",
        }

    occupied_rows = (
        db.query(DockingSlipOccupancy)
        .filter(
            DockingSlipOccupancy.station_id == station.id,
            DockingSlipOccupancy.slip_class == "long_term",
        )
        .count()
    )
    if occupied_rows >= capacity:
        return {"status": "full", "capacity": capacity, "occupied": occupied_rows}

    fee = days * LONG_TERM_MOORING_RATE_PER_DAY
    # Lock the player row to safely deduct credits (no other player row
    # involved here, so no ordering concern beyond station-first).
    player_locked = (
        db.query(Player).filter(Player.id == player.id).with_for_update().first()
    )
    if player_locked is None:
        return {"status": "error", "detail": "Player not found"}
    if player_locked.credits < fee:
        return {"status": "insufficient_credits", "need": fee, "have": player_locked.credits}

    player_locked.credits -= fee
    # Route the mooring fee through the canon 40/30/30 split (defense/operating/owner)
    # rather than crediting the owner treasury at 100%.
    _realize_fee(db, station, fee)

    occupancy = DockingSlipOccupancy(
        station_id=station.id,
        player_id=player.id,
        ship_id=ship_id,
        slip_class="long_term",
        fee_paid=fee,
    )
    db.add(occupancy)
    db.flush()

    logger.info(
        "Long-term mooring granted: player=%s station=%s days=%d fee=%d",
        player.id, station.id, days, fee,
    )
    return {
        "status": "granted",
        "occupancy": occupancy,
        "capacity": capacity,
        "occupied": occupied_rows + 1,
        "days": days,
        "fee_paid": fee,
    }


def release_long_term(db: Session, station: Optional[Station], player: Player) -> bool:
    """Release a long-term mooring slip held by `player`. Tolerates a missing
    row silently. Does NOT commit; the calling route owns the transaction.

    Note: no fee refund is issued on release — canon is silent on refunds for
    pre-paid long-term mooring; the fee is treated as consumed on grant.
    """
    occupancy = (
        db.query(DockingSlipOccupancy)
        .filter(
            DockingSlipOccupancy.player_id == player.id,
            DockingSlipOccupancy.slip_class == "long_term",
        )
        .first()
    )
    if occupancy is None:
        return False
    db.delete(occupancy)
    logger.info("Long-term mooring released: player=%s", player.id)
    return True

"""
Ship construction (TradeDock shipyard) service.

Canon reference: FEATURES/economy/tradedock-shipyard + ADR-0039 (sw2102-docs).
Drives the ConstructionReservation state machine:

    requested -> queued -> hold_active -> deposit_collected -> frame_assembly
    -> systems_integration -> outfitting -> final_assembly -> complete
    -> claimed | cancelled | forfeited

LAZY ENGINE: there is no background worker. `advance()` is called on every
read/write and settles everything time-based for the reservation's station:
hold expiries, rent forfeitures, phase completions (chaining multiple phases
if the player was away), claim-window expiries, and queue promotions. All
durations are CANONICAL and pass through src.core.game_time, so
GAME_TIME_SCALE compresses every window uniformly on dev.

DOCUMENTED INTERPRETATIONS (where canon is silent or summarized):
  * Queue ordering is (faction_rep_tier desc, deposit desc, created_at asc) —
    simplified from canon's full sort key.
  * The 24h hold is confirmed by paying the keel_laid milestone; payment
    transitions hold_active -> deposit_collected and starts the rent clock.
  * Resource checkpoints are the documented interpretation of the doc's
    per-phase delivery thresholds: frame_assembly needs >= 25% of every
    bundle resource; systems_integration needs 100% ore + 100% equipment and
    >= 50% organics; outfitting needs 100% of everything.
  * Milestone payments, the deposit, and rent are banked into the station
    treasury immediately. Forfeit redistribution and refunds are paid back
    OUT of the treasury (which may go briefly negative on a refund — the
    station "sold" an asset to cover it).
  * Claim-window forfeit: the station sells the finished ship; the player is
    credited 70% of total_cost (canon's sell-back minus 30%) and the treasury
    nets total_cost minus that refund.
  * Cancel refunds 50% of cash paid so far; after the hull_complete milestone
    is paid, cancel is a sell-back at 70% of cash paid. Resources are never
    refunded (ADR-0039: deliveries are atomic and irreversible).
  * Rent forfeiture keeps all payments and resources (already banked).

Lock-ordering contract (matches docking_service): the STATION row is locked
first (`advance` and `create_reservation` both take it FOR UPDATE) — this
serializes slip accounting and treasury movement per station — then PLAYER
rows are locked as needed. No function here commits; the calling route owns
the transaction and issues a single commit.
"""
import logging
import math
from datetime import datetime, UTC
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from src.core import game_time
from src.models.construction import ConstructionReservation
from src.models.faction import Faction
from src.models.player import Player
from src.models.reputation import Reputation
from src.models.ship import Ship, ShipType
from src.models.station import Station

logger = logging.getLogger(__name__)


class ConstructionError(Exception):
    """Raised on invalid construction actions; carries an HTTP status hint."""

    def __init__(self, status_code: int, detail: str):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


# ---------------------------------------------------------------------------
# Canon tables
# ---------------------------------------------------------------------------

# Total all-in project cost (credits) and build duration (canonical days).
SHIP_BUILD_SPECS: Dict[str, Dict[str, int]] = {
    "SCOUT_SHIP":      {"total_cost": 40_000,    "build_days": 3},
    "FAST_COURIER":    {"total_cost": 65_000,    "build_days": 4},
    "LIGHT_FREIGHTER": {"total_cost": 100_000,   "build_days": 5},
    "CARGO_HAULER":    {"total_cost": 320_000,   "build_days": 7},
    "DEFENDER":        {"total_cost": 380_000,   "build_days": 8},
    "COLONY_SHIP":     {"total_cost": 640_000,   "build_days": 10},
    "CARRIER":         {"total_cost": 1_900_000, "build_days": 14},
    "WARP_JUMPER":     {"total_cost": 1_000_000, "build_days": 14},
}

# Build phases split 20/30/30/20 of the ship's build days.
PHASE_ORDER = ["frame_assembly", "systems_integration", "outfitting", "final_assembly"]
PHASE_SPLITS = {
    "frame_assembly": 0.20,
    "systems_integration": 0.30,
    "outfitting": 0.30,
    "final_assembly": 0.20,
}

# Payment milestones: deposit 10% (at queue entry), keel-laid 25% (gates
# frame_assembly), hull-complete 25% (due end of systems_integration — gates
# outfitting), final 40% (required at claim).
MILESTONE_ORDER = ["deposit", "keel_laid", "hull_complete", "final"]
MILESTONE_FRACTIONS = {"deposit": 0.10, "keel_laid": 0.25, "hull_complete": 0.25, "final": 0.40}
# A phase will not START until its gating milestone is paid.
PHASE_MILESTONE_GATE = {"frame_assembly": "keel_laid", "outfitting": "hull_complete"}

# Resource bundle: per 1,000 credits of total_cost — 5 ore, 2 equipment,
# 1 organics (rounded). Delivered in batches from the current ship's cargo.
RESOURCE_KEYS = ("ore", "equipment", "organics")
RESOURCE_UNITS_PER_1000_CREDITS = {"ore": 5, "equipment": 2, "organics": 1}

# Documented interpretation of the doc's per-phase delivery thresholds:
# fraction of each required resource that must be delivered before the phase
# may start. final_assembly has no gate (outfitting already required 100%).
PHASE_RESOURCE_CHECKPOINTS: Dict[str, Dict[str, float]] = {
    "frame_assembly":      {"ore": 0.25, "equipment": 0.25, "organics": 0.25},
    "systems_integration": {"ore": 1.0,  "equipment": 1.0,  "organics": 0.50},
    "outfitting":          {"ore": 1.0,  "equipment": 1.0,  "organics": 1.0},
    "final_assembly":      {},
}

HOLD_HOURS = 24.0                  # canonical hold window for a freed slip
CLAIM_WINDOW_HOURS = 7 * 24.0      # canonical claim window after completion
RENT_RATE_PER_DAY = 0.005          # daily slip rent = total_cost x 0.5%
RENT_FORFEIT_DAYS = 3.0            # 3 consecutive canonical days unpaid
RENT_MAX_PREPAY_DAYS = 30          # pay-rent pre-pays up to 30 canonical days
CANCEL_REFUND_FRACTION = 0.50
CANCEL_REFUND_FRACTION_AFTER_HULL = 0.70   # post-hull cancel = 70% sell-back
CLAIM_FORFEIT_REFUND_FRACTION = 0.70       # missed claim: sell-back minus 30%

# Slip pools by TradeDock tier: B = 12 standard; A = 8 standard + 4
# specialized. Carrier and Warp Jumper require Tier-A; Warp Jumper consumes
# a SPECIALIZED slip.
SLIP_POOLS = {
    "B": {"standard": 12, "specialized": 0},
    "A": {"standard": 8, "specialized": 4},
}
TIER_A_ONLY_TYPES = {"CARRIER", "WARP_JUMPER"}
SPECIALIZED_SLIP_TYPES = {"WARP_JUMPER"}

# States that consume a construction slip: hold_active RESERVES one; the
# build states OCCUPY one until the ship is claimed/lost.
SLIP_HOLDING_STATES = {
    "hold_active", "deposit_collected", "frame_assembly",
    "systems_integration", "outfitting", "final_assembly",
}
# Rent accrues while occupying a slip (deposit_collected..final_assembly).
RENT_STATES = {
    "deposit_collected", "frame_assembly", "systems_integration",
    "outfitting", "final_assembly",
}
# Deliveries open once the slip is secured and close when outfitting ends
# (100% of the bundle is required to start outfitting anyway).
DELIVERY_STATES = {"deposit_collected", "frame_assembly", "systems_integration", "outfitting"}
TERMINAL_STATES = {"claimed", "cancelled", "forfeited"}


# ---------------------------------------------------------------------------
# Pure helpers (no DB) — unit-tested directly
# ---------------------------------------------------------------------------

def _aware(dt: datetime) -> datetime:
    return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt


def resource_bundle(total_cost: int) -> Dict[str, int]:
    """Required ore/equipment/organics for a build (per-1,000-credit ratios)."""
    return {
        key: int(round(total_cost / 1000 * units))
        for key, units in RESOURCE_UNITS_PER_1000_CREDITS.items()
    }


def milestone_amounts(total_cost: int) -> Dict[str, int]:
    """Credit amount per milestone; 'final' absorbs rounding so they sum exactly."""
    amounts = {
        name: int(round(total_cost * MILESTONE_FRACTIONS[name]))
        for name in MILESTONE_ORDER[:-1]
    }
    amounts["final"] = total_cost - sum(amounts.values())
    return amounts


def phase_hours(ship_type: str, phase: str) -> float:
    """Canonical hours a phase runs (build_days x 24 x phase split)."""
    return SHIP_BUILD_SPECS[ship_type]["build_days"] * 24.0 * PHASE_SPLITS[phase]


def daily_rent(total_cost: int) -> int:
    """Slip rent per canonical day while occupying a slip."""
    return max(1, int(round(total_cost * RENT_RATE_PER_DAY)))


def split_forfeited_deposit(deposit: int) -> tuple:
    """Hold-forfeit split: 50% to the next-in-queue reservation as credit,
    50% (plus any odd credit) to the station treasury."""
    to_next = deposit // 2
    return to_next, deposit - to_next


def cancel_refund(credits_paid: int, hull_complete_paid: bool) -> int:
    """Cancel refund: 50% of cash paid; 70% sell-back once hull-complete is paid."""
    fraction = CANCEL_REFUND_FRACTION_AFTER_HULL if hull_complete_paid else CANCEL_REFUND_FRACTION
    return int(credits_paid * fraction)


def claim_forfeit_refund(total_cost: int) -> int:
    """Missed claim window: station sells the ship; player gets 70% of total cost."""
    return int(total_cost * CLAIM_FORFEIT_REFUND_FRACTION)


def checkpoint_shortfall(
    required: Dict[str, int], delivered: Dict[str, int], phase: str
) -> Dict[str, int]:
    """Units of each resource still needed before `phase` may start (empty = met)."""
    shortfall: Dict[str, int] = {}
    for key, fraction in PHASE_RESOURCE_CHECKPOINTS.get(phase, {}).items():
        needed = math.ceil((required or {}).get(key, 0) * fraction)
        have = (delivered or {}).get(key, 0)
        if have < needed:
            shortfall[key] = needed - have
    return shortfall


def checkpoint_met(required: Dict[str, int], delivered: Dict[str, int], phase: str) -> bool:
    return not checkpoint_shortfall(required, delivered, phase)


def phase_start_blockers(reservation: Any, phase: str) -> List[str]:
    """Human-readable list of why `phase` cannot start yet (empty = may start)."""
    blockers: List[str] = []
    gate = PHASE_MILESTONE_GATE.get(phase)
    if gate and not (reservation.milestones or {}).get(gate):
        amount = milestone_amounts(reservation.total_cost)[gate]
        blockers.append(f"milestone '{gate}' unpaid ({amount:,} credits)")
    shortfall = checkpoint_shortfall(
        reservation.resources_required, reservation.resources_delivered, phase
    )
    if shortfall:
        needs = ", ".join(f"{qty} {key}" for key, qty in shortfall.items())
        blockers.append(f"resource checkpoint unmet (deliver {needs})")
    return blockers


def rent_overdue_canonical_days(reservation: Any, now: Optional[datetime] = None) -> float:
    """Canonical days of unpaid rent (0.0 when paid up or no slip occupied)."""
    if reservation.state not in RENT_STATES or reservation.rent_paid_until is None:
        return 0.0
    hours = game_time.canonical_hours_since(reservation.rent_paid_until, now)
    return max(0.0, hours / 24.0)


def rent_owed_amount(reservation: Any, now: Optional[datetime] = None) -> int:
    """Credits owed for rent: each STARTED canonical day past rent_paid_until."""
    overdue = rent_overdue_canonical_days(reservation, now)
    if overdue <= 0:
        return 0
    return math.ceil(overdue) * daily_rent(reservation.total_cost)


def _rent_forfeit_due(reservation: Any, now: Optional[datetime] = None) -> bool:
    """3 consecutive canonical days of unpaid rent forfeits the build."""
    return rent_overdue_canonical_days(reservation, now) >= RENT_FORFEIT_DAYS


def _progress_phases(reservation: Any, now: datetime) -> bool:
    """Advance the phase clock as far as `now` allows. Pure on the reservation.

    A phase state with phase_deadline NULL is PAUSED (its gates are unmet);
    when gates clear, the clock restarts with the FULL phase duration from
    `now` (canon: a phase will not start until its milestone is paid).
    Chained completions anchor each next phase at the previous deadline, so a
    player away for a week loses no build time. Returns True if anything
    changed; sets updated_at when it does.
    """
    changed = False

    # Enter the first phase once its gates clear.
    if reservation.state == "deposit_collected":
        first = PHASE_ORDER[0]
        if phase_start_blockers(reservation, first):
            return changed
        reservation.state = first
        reservation.phase_deadline = game_time.scaled_deadline(
            phase_hours(reservation.ship_type, first), start=now
        )
        changed = True

    while reservation.state in PHASE_ORDER:
        phase = reservation.state
        if reservation.phase_deadline is None:
            # Paused mid-pipeline: start the clock if the gates have cleared.
            if phase_start_blockers(reservation, phase):
                break
            reservation.phase_deadline = game_time.scaled_deadline(
                phase_hours(reservation.ship_type, phase), start=now
            )
            changed = True
            break
        deadline = _aware(reservation.phase_deadline)
        if now < deadline:
            break
        # Phase finished at `deadline`; chain into the next one.
        idx = PHASE_ORDER.index(phase)
        if idx + 1 < len(PHASE_ORDER):
            nxt = PHASE_ORDER[idx + 1]
            reservation.state = nxt
            if phase_start_blockers(reservation, nxt):
                reservation.phase_deadline = None  # paused until gates clear
            else:
                reservation.phase_deadline = game_time.scaled_deadline(
                    phase_hours(reservation.ship_type, nxt), start=deadline
                )
            changed = True
        else:
            reservation.state = "complete"
            reservation.phase_deadline = None
            reservation.claim_expires_at = game_time.scaled_deadline(
                CLAIM_WINDOW_HOURS, start=deadline
            )
            changed = True
            break

    if changed:
        reservation.updated_at = now
    return changed


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

def _lock_station(db: Session, station_id) -> Station:
    station = db.query(Station).filter(Station.id == station_id).with_for_update().first()
    if station is None:
        raise ConstructionError(404, "Station not found")
    return station


def _lock_player(db: Session, player_id) -> Player:
    player = db.query(Player).filter(Player.id == player_id).with_for_update().first()
    if player is None:
        raise ConstructionError(404, "Player not found")
    return player


def _require_tradedock(station: Station) -> Dict[str, int]:
    tier = getattr(station, "tradedock_tier", None)
    pools = SLIP_POOLS.get(tier)
    if pools is None:
        raise ConstructionError(400, f"{station.name} has no TradeDock shipyard")
    return pools


def _faction_rep_tier(db: Session, player_id, station: Station) -> int:
    """Numeric reputation tier (-8..+8) toward the station's faction; 0 when
    the station is unaffiliated or the player has no reputation record."""
    if not station.faction_affiliation:
        return 0
    faction = db.query(Faction).filter(Faction.name == station.faction_affiliation).first()
    if faction is None:
        return 0
    rep = db.query(Reputation).filter(
        Reputation.player_id == player_id,
        Reputation.faction_id == faction.id,
    ).first()
    return rep.numeric_level if rep is not None else 0


def _sorted_queue(
    db: Session, station: Station, reservations: List[ConstructionReservation]
) -> List[ConstructionReservation]:
    """Queued reservations in promotion order — (faction_rep_tier desc,
    deposit desc, created_at asc); simplified from canon's full sort key."""
    queued = [r for r in reservations if r.state == "queued"]
    return sorted(
        queued,
        key=lambda r: (
            -_faction_rep_tier(db, r.player_id, station),
            -(r.deposit_paid or 0),
            _aware(r.created_at) if r.created_at else datetime.now(UTC),
        ),
    )


def _slip_usage(reservations: List[ConstructionReservation]) -> Dict[str, int]:
    usage = {"standard": 0, "specialized": 0}
    for r in reservations:
        if r.state in SLIP_HOLDING_STATES:
            usage["specialized" if r.uses_specialized_slip else "standard"] += 1
    return usage


# ---------------------------------------------------------------------------
# THE lazy engine
# ---------------------------------------------------------------------------

def advance(db: Session, reservation: ConstructionReservation, now: Optional[datetime] = None) -> Station:
    """Settle everything time-based for the reservation's station, then return
    the LOCKED station row (callers reuse it for treasury movement).

    Called on every read and before every mutation. Locks the station row
    first — the per-station serialization point for slip accounting.
    """
    now = now or datetime.now(UTC)
    station = _lock_station(db, reservation.station_id)
    # Re-read the reservation UNDER the station lock with fresh attributes.
    # with_for_update() alone returns the identity-mapped instance with
    # stale state, so two concurrent claims could both see 'complete' and
    # duplicate the ship; populate_existing() forces a refresh from the
    # locked row (gate-review finding).
    db.query(ConstructionReservation).filter(
        ConstructionReservation.id == reservation.id
    ).populate_existing().with_for_update().first()
    _advance_station(db, station, now)
    return station


def _advance_station(db: Session, station: Station, now: datetime) -> None:
    """Process the station's whole pipeline. Caller holds the station lock."""
    pools = SLIP_POOLS.get(getattr(station, "tradedock_tier", None))
    if pools is None:
        return  # Station lost its shipyard designation; nothing to drive.

    reservations: List[ConstructionReservation] = (
        db.query(ConstructionReservation)
        .filter(
            ConstructionReservation.station_id == station.id,
            ConstructionReservation.state.notin_(list(TERMINAL_STATES)),
        )
        .order_by(ConstructionReservation.created_at.asc())
        .all()
    )

    # 1. Expired holds -> forfeited; deposit split 50% to the next-in-queue
    #    reservation as credit, 50% stays in the station treasury. The whole
    #    deposit was banked at queue entry; the credit is honored later as a
    #    milestone DISCOUNT (pay_milestone banks cash_due only), so no funds
    #    move here — debiting the treasury too would fund the credit twice
    #    and leave the station with 0% of the forfeit instead of canon's 50%.
    for res in reservations:
        if res.state != "hold_active" or res.hold_expires_at is None:
            continue
        if now < _aware(res.hold_expires_at):
            continue
        res.state = "forfeited"
        res.updated_at = now
        to_next, _to_treasury = split_forfeited_deposit(res.deposit_paid or 0)
        queue = _sorted_queue(db, station, reservations)
        if queue and to_next > 0:
            queue[0].queue_bonus_credit = (queue[0].queue_bonus_credit or 0) + to_next
            queue[0].updated_at = now
        logger.info(
            "Construction hold expired: reservation %s forfeited at station %s "
            "(deposit %s, %s redistributed)",
            res.id, station.id, res.deposit_paid, to_next if queue else 0,
        )

    # 2. Phase progression FIRST: a build whose phases all completed while
    #    the player was away must reach 'complete' (where rent stops
    #    accruing) before rent forfeiture is evaluated — otherwise a
    #    finished hull could be forfeited for rent that canonically never
    #    came due (gate-review finding).
    for res in reservations:
        if res.state == "deposit_collected" or res.state in PHASE_ORDER:
            _progress_phases(res, now)

    # 3. Rent forfeitures: 3 consecutive canonical days unpaid loses the
    #    build (resources and payments stay banked; the slip frees).
    for res in reservations:
        if res.state in RENT_STATES and _rent_forfeit_due(res, now):
            res.state = "forfeited"
            res.phase_deadline = None
            res.rent_owed_since = res.rent_paid_until
            res.updated_at = now
            logger.info(
                "Construction rent forfeit: reservation %s at station %s", res.id, station.id
            )

    # 4. Post-progression bookkeeping and claim-window expiry.
    for res in reservations:
        if res.state == "deposit_collected" or res.state in PHASE_ORDER:
            # Surface the rent-owed marker lazily.
            if res.state in RENT_STATES:
                res.rent_owed_since = (
                    res.rent_paid_until
                    if rent_overdue_canonical_days(res, now) > 0 else None
                )
        if (
            res.state == "complete"
            and res.claim_expires_at is not None
            and now >= _aware(res.claim_expires_at)
        ):
            # Missed claim: the station sells the ship and credits the player
            # 70% of total cost (canon sell-back minus 30%); the treasury nets
            # the sale price minus that refund.
            refund = claim_forfeit_refund(res.total_cost)
            player = db.query(Player).filter(Player.id == res.player_id).with_for_update().first()
            if player is not None:
                player.credits += refund
            station.treasury_balance = (station.treasury_balance or 0) + res.total_cost - refund
            res.state = "forfeited"
            res.updated_at = now
            logger.info(
                "Construction claim window expired: reservation %s forfeited, "
                "%s credits refunded", res.id, refund,
            )

    # 4. Promotions: while a slip is free, the front of the queue gets a
    #    24 canonical-hour hold on it. Standard and specialized pools are
    #    independent; the first queued reservation whose pool has room is
    #    promoted (a blocked specialized build does not block standard ones).
    usage = _slip_usage(reservations)
    free = {
        "standard": max(0, pools["standard"] - usage["standard"]),
        "specialized": max(0, pools["specialized"] - usage["specialized"]),
    }
    for res in _sorted_queue(db, station, reservations):
        pool_key = "specialized" if res.uses_specialized_slip else "standard"
        if free[pool_key] <= 0:
            continue
        res.state = "hold_active"
        res.hold_expires_at = game_time.scaled_deadline(HOLD_HOURS, start=now)
        res.updated_at = now
        free[pool_key] -= 1
        logger.info(
            "Construction slip hold granted: reservation %s at station %s "
            "(expires %s)", res.id, station.id, res.hold_expires_at,
        )

    db.flush()


# ---------------------------------------------------------------------------
# Player-facing operations (routes own the commit)
# ---------------------------------------------------------------------------

def quote(db: Session, station: Station, now: Optional[datetime] = None) -> Dict[str, Any]:
    """Full cost/duration/resource breakdown for every buildable ship type,
    plus the station's current slip and queue picture. Locks the station and
    settles its pipeline first so slip counts are current."""
    now = now or datetime.now(UTC)
    station = _lock_station(db, station.id)
    pools = _require_tradedock(station)
    tier = station.tradedock_tier
    _advance_station(db, station, now)

    reservations = (
        db.query(ConstructionReservation)
        .filter(
            ConstructionReservation.station_id == station.id,
            ConstructionReservation.state.notin_(list(TERMINAL_STATES)),
        )
        .all()
    )
    usage = _slip_usage(reservations)
    queue_length = sum(1 for r in reservations if r.state == "queued")

    quotes = []
    for ship_type, spec in SHIP_BUILD_SPECS.items():
        cost = spec["total_cost"]
        days = spec["build_days"]
        requires_tier_a = ship_type in TIER_A_ONLY_TYPES
        available = not (requires_tier_a and tier != "A")
        quotes.append({
            "ship_type": ship_type,
            "total_cost": cost,
            "build_days": days,
            "deposit": milestone_amounts(cost)["deposit"],
            "milestones": milestone_amounts(cost),
            "resources_required": resource_bundle(cost),
            "daily_rent": daily_rent(cost),
            "phases": {p: phase_hours(ship_type, p) for p in PHASE_ORDER},
            "requires_tier_a": requires_tier_a,
            "uses_specialized_slip": ship_type in SPECIALIZED_SLIP_TYPES,
            "available": available,
            "unavailable_reason": (
                None if available else f"{ship_type} requires a Tier-A TradeDock"
            ),
        })

    return {
        "station_id": str(station.id),
        "station_name": station.name,
        "tradedock_tier": tier,
        "slips": {
            "standard": {"capacity": pools["standard"], "in_use": usage["standard"]},
            "specialized": {"capacity": pools["specialized"], "in_use": usage["specialized"]},
        },
        "queue_length": queue_length,
        "hold_hours": HOLD_HOURS,
        "claim_window_hours": CLAIM_WINDOW_HOURS,
        "rent_rate_per_day": RENT_RATE_PER_DAY,
        "quotes": quotes,
    }


def create_reservation(
    db: Session,
    station: Station,
    player: Player,
    ship_type: str,
    ship_name: Optional[str] = None,
    now: Optional[datetime] = None,
) -> ConstructionReservation:
    """Place a build order: validates tier gating, charges the 10% deposit,
    and enters the queue (requested -> queued in one transaction)."""
    now = now or datetime.now(UTC)

    spec = SHIP_BUILD_SPECS.get(ship_type)
    if spec is None:
        buildable = ", ".join(sorted(SHIP_BUILD_SPECS))
        raise ConstructionError(
            400, f"'{ship_type}' cannot be built here. Buildable types: {buildable}"
        )
    # Belt-and-braces: every buildable type must be a real ShipType.
    if ship_type not in ShipType.__members__:
        raise ConstructionError(400, f"Unknown ship type '{ship_type}'")

    # Lock order: station first (slip/treasury serialization), then player.
    station = _lock_station(db, station.id)
    _require_tradedock(station)
    if ship_type in TIER_A_ONLY_TYPES and station.tradedock_tier != "A":
        raise ConstructionError(
            400,
            f"{ship_type} construction requires a Tier-A TradeDock; "
            f"{station.name} is Tier-{station.tradedock_tier}",
        )

    player = _lock_player(db, player.id)
    total_cost = spec["total_cost"]
    deposit = milestone_amounts(total_cost)["deposit"]
    if player.credits < deposit:
        raise ConstructionError(
            400,
            f"Insufficient credits for the {deposit:,}-credit deposit "
            f"(10% of {total_cost:,}). Have {player.credits:,}",
        )

    # Charge the deposit; it banks into the station treasury immediately.
    player.credits -= deposit
    station.treasury_balance = (station.treasury_balance or 0) + deposit

    reservation = ConstructionReservation(
        station_id=station.id,
        player_id=player.id,
        ship_type=ship_type,
        # 'requested' exists only inside this transaction: the deposit payment
        # is what moves requested -> queued, and both happen here.
        state="queued",
        ship_name=ship_name,
        total_cost=total_cost,
        deposit_paid=deposit,
        credits_paid=deposit,
        milestones={"deposit": True, "keel_laid": False, "hull_complete": False, "final": False},
        resources_required=resource_bundle(total_cost),
        resources_delivered={},
        uses_specialized_slip=ship_type in SPECIALIZED_SLIP_TYPES,
        created_at=now,
        updated_at=now,
    )
    db.add(reservation)
    db.flush()

    # A free slip may grant the hold immediately.
    _advance_station(db, station, now)

    logger.info(
        "Construction reservation created: %s %s at station %s for player %s",
        reservation.id, ship_type, station.id, player.id,
    )
    return reservation


def deliver(
    db: Session,
    reservation: ConstructionReservation,
    player: Player,
    amounts: Dict[str, int],
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """ADR-0039 atomic batch delivery from the player's CURRENT ship cargo.

    One transaction, irreversible, no return path. Wrong-type resources are
    rejected before anything is committed; partial correct-type batches are
    accepted. Over-delivery beyond the bundle is rejected (it could never be
    returned)."""
    now = now or datetime.now(UTC)
    advance(db, reservation, now)

    if reservation.state not in DELIVERY_STATES:
        if reservation.state == "hold_active":
            raise ConstructionError(
                400,
                "Secure your slip first: pay the 'keel_laid' milestone to "
                "confirm the hold, then deliver resources",
            )
        raise ConstructionError(
            400, f"Deliveries are not accepted in state '{reservation.state}'"
        )

    # Wrong-type rejection BEFORE any mutation (ADR-0039).
    unknown = [k for k in amounts if k not in RESOURCE_KEYS]
    if unknown:
        raise ConstructionError(
            400,
            f"Rejected resource type(s): {', '.join(sorted(unknown))}. "
            f"This build accepts only {', '.join(RESOURCE_KEYS)}",
        )
    batch = {k: int(v) for k, v in amounts.items() if v}
    if not batch:
        raise ConstructionError(400, "Nothing to deliver — all quantities are zero")
    if any(v < 0 for v in batch.values()):
        raise ConstructionError(400, "Delivery quantities must be positive")

    player = _lock_player(db, player.id)
    ship = db.query(Ship).filter(
        Ship.id == player.current_ship_id,
        Ship.owner_id == player.id,
    ).first()
    if ship is None:
        raise ConstructionError(404, "No active ship found")

    cargo = ship.cargo or {"used": 0, "capacity": 50, "contents": {}}
    contents = cargo.get("contents", {})
    required = reservation.resources_required or {}
    delivered = dict(reservation.resources_delivered or {})

    # Validate the whole batch before mutating anything (atomicity).
    for key, qty in batch.items():
        have = contents.get(key, 0)
        if have < qty:
            raise ConstructionError(
                400, f"Your ship holds only {have} {key}; tried to deliver {qty}"
            )
        remaining = required.get(key, 0) - delivered.get(key, 0)
        if qty > remaining:
            raise ConstructionError(
                400,
                f"This build needs only {remaining} more {key} "
                f"(deliveries are irreversible — over-delivery is rejected)",
            )

    # Commit the batch: out of the cargo hold, into the build. No return path.
    for key, qty in batch.items():
        contents[key] = contents.get(key, 0) - qty
        if contents[key] <= 0:
            contents.pop(key, None)
        delivered[key] = delivered.get(key, 0) + qty
    cargo["contents"] = contents
    cargo["used"] = max(0, cargo.get("used", 0) - sum(batch.values()))
    ship.cargo = cargo
    flag_modified(ship, "cargo")

    reservation.resources_delivered = delivered
    flag_modified(reservation, "resources_delivered")
    reservation.updated_at = now

    # The delivery may clear a checkpoint and unpause the phase clock.
    _progress_phases(reservation, now)
    db.flush()

    return {
        "delivered": batch,
        "resources_delivered": delivered,
        "resources_required": required,
        "state": reservation.state,
    }


def pay_milestone(
    db: Session,
    reservation: ConstructionReservation,
    player: Player,
    milestone: str,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Pay a project milestone. Paying keel_laid during the hold confirms the
    slip (hold_active -> deposit_collected) and starts the rent clock."""
    now = now or datetime.now(UTC)
    station = advance(db, reservation, now)

    if milestone not in MILESTONE_ORDER:
        raise ConstructionError(
            400, f"Unknown milestone '{milestone}'. Milestones: {', '.join(MILESTONE_ORDER)}"
        )
    if milestone == "deposit":
        raise ConstructionError(400, "The deposit was collected when the order entered the queue")

    milestones = dict(reservation.milestones or {})
    if milestones.get(milestone):
        raise ConstructionError(400, f"Milestone '{milestone}' is already paid")
    if reservation.state in TERMINAL_STATES:
        raise ConstructionError(400, f"This reservation is {reservation.state}")
    if reservation.state in ("requested", "queued"):
        raise ConstructionError(
            400, "Milestones become payable once a construction slip is held"
        )

    # Enforce milestone order: every earlier milestone must already be paid.
    for earlier in MILESTONE_ORDER[: MILESTONE_ORDER.index(milestone)]:
        if not milestones.get(earlier):
            raise ConstructionError(
                400, f"Milestone '{earlier}' must be paid before '{milestone}'"
            )

    due = milestone_amounts(reservation.total_cost)[milestone]
    # Forfeit-redistribution credit offsets the cash due (interpretation:
    # the credit lives in the treasury already, so only cash moves now).
    bonus_applied = min(reservation.queue_bonus_credit or 0, due)
    cash_due = due - bonus_applied

    player = _lock_player(db, player.id)
    if player.credits < cash_due:
        raise ConstructionError(
            400,
            f"Insufficient credits for milestone '{milestone}': need {cash_due:,}"
            + (f" (after {bonus_applied:,} queue credit)" if bonus_applied else "")
            + f", have {player.credits:,}",
        )

    player.credits -= cash_due
    station.treasury_balance = (station.treasury_balance or 0) + cash_due
    reservation.queue_bonus_credit = (reservation.queue_bonus_credit or 0) - bonus_applied
    reservation.credits_paid = (reservation.credits_paid or 0) + cash_due
    milestones[milestone] = True
    reservation.milestones = milestones
    flag_modified(reservation, "milestones")
    reservation.updated_at = now

    # Hold confirmation: keel_laid during the hold secures the slip and
    # starts the rent clock (documented interpretation).
    if milestone == "keel_laid" and reservation.state == "hold_active":
        reservation.state = "deposit_collected"
        reservation.hold_expires_at = None
        reservation.rent_paid_until = now
        reservation.rent_owed_since = None

    # The payment may clear a milestone gate and unpause the phase clock.
    _progress_phases(reservation, now)
    db.flush()

    return {
        "milestone": milestone,
        "amount_due": due,
        "queue_credit_applied": bonus_applied,
        "cash_paid": cash_due,
        "credits_remaining": player.credits,
        "state": reservation.state,
    }


def pay_rent(
    db: Session,
    reservation: ConstructionReservation,
    player: Player,
    days: int,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Pay slip rent for `days` canonical days (pre-pay up to 30 days ahead)."""
    now = now or datetime.now(UTC)
    station = advance(db, reservation, now)

    if reservation.state not in RENT_STATES:
        raise ConstructionError(
            400, f"No slip rent accrues in state '{reservation.state}'"
        )
    if days < 1 or days > RENT_MAX_PREPAY_DAYS:
        raise ConstructionError(
            400, f"Rent is payable 1-{RENT_MAX_PREPAY_DAYS} days at a time"
        )

    paid_until = _aware(reservation.rent_paid_until) if reservation.rent_paid_until else now
    new_until = game_time.scaled_deadline(days * 24.0, start=paid_until)
    limit = game_time.scaled_deadline(RENT_MAX_PREPAY_DAYS * 24.0, start=now)
    if new_until > limit:
        # How many days fit under the 30-canonical-day prepay cap?
        overshoot_hours = game_time.canonical_hours_since(limit, new_until)
        max_days = days - math.ceil(overshoot_hours / 24.0)
        raise ConstructionError(
            400,
            f"Rent can be pre-paid at most {RENT_MAX_PREPAY_DAYS} canonical days "
            f"ahead; you can pay up to {max(0, max_days)} day(s) right now",
        )

    rate = daily_rent(reservation.total_cost)
    cost = rate * days
    player = _lock_player(db, player.id)
    if player.credits < cost:
        raise ConstructionError(
            400,
            f"Insufficient credits: {days} day(s) of rent at {rate:,}/day "
            f"costs {cost:,}, have {player.credits:,}",
        )

    player.credits -= cost
    station.treasury_balance = (station.treasury_balance or 0) + cost
    reservation.rent_paid_until = new_until
    reservation.rent_owed_since = new_until if new_until <= now else None
    reservation.updated_at = now
    db.flush()

    return {
        "days_paid": days,
        "daily_rent": rate,
        "total_paid": cost,
        "rent_paid_until": new_until.isoformat(),
        "credits_remaining": player.credits,
    }


def claim(
    db: Session,
    reservation: ConstructionReservation,
    player: Player,
    now: Optional[datetime] = None,
) -> Ship:
    """Claim the finished ship: requires state complete and the final
    milestone paid. The ship is created via ShipService.create_ship (cargo
    comes spec-correct) at the TradeDock's sector with the custom name."""
    now = now or datetime.now(UTC)
    station = advance(db, reservation, now)

    if reservation.state == "forfeited":
        raise ConstructionError(
            400, "The claim window expired — the ship was sold and 70% of its cost refunded"
        )
    if reservation.state != "complete":
        raise ConstructionError(
            400, f"The build is not ready to claim (state: '{reservation.state}')"
        )
    if not (reservation.milestones or {}).get("final"):
        amount = milestone_amounts(reservation.total_cost)["final"]
        raise ConstructionError(
            400, f"Pay the 'final' milestone ({amount:,} credits) before claiming"
        )

    from src.services.ship_service import ShipService

    name = reservation.ship_name or None
    ship = ShipService(db).create_ship(
        ShipType[reservation.ship_type],
        player.id,
        station.sector_id,
        name=name,
    )
    reservation.state = "claimed"
    reservation.updated_at = now
    db.flush()

    logger.info(
        "Construction claimed: reservation %s -> ship %s for player %s",
        reservation.id, ship.id, player.id,
    )
    return ship


def cancel(
    db: Session,
    reservation: ConstructionReservation,
    player: Player,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Cancel before completion. Refunds 50% of cash paid so far — except
    after hull-complete, where cancel is a sell-back at 70% of cash paid.
    Resources are never refunded (ADR-0039)."""
    now = now or datetime.now(UTC)
    station = advance(db, reservation, now)

    if reservation.state in TERMINAL_STATES:
        raise ConstructionError(400, f"This reservation is already {reservation.state}")
    if reservation.state == "complete":
        raise ConstructionError(
            400, "The build is complete — claim it (or let the claim window lapse)"
        )

    refund = cancel_refund(
        reservation.credits_paid or 0,
        bool((reservation.milestones or {}).get("hull_complete")),
    )
    player = _lock_player(db, player.id)
    player.credits += refund
    station.treasury_balance = (station.treasury_balance or 0) - refund
    reservation.state = "cancelled"
    reservation.phase_deadline = None
    reservation.hold_expires_at = None
    reservation.updated_at = now
    db.flush()

    logger.info(
        "Construction cancelled: reservation %s, %s credits refunded", reservation.id, refund
    )
    return {
        "refund": refund,
        "credits_paid": reservation.credits_paid,
        "credits_remaining": player.credits,
        "resources_refunded": 0,  # never (ADR-0039)
    }


# ---------------------------------------------------------------------------
# Status payload
# ---------------------------------------------------------------------------

def status_payload(
    db: Session,
    reservation: ConstructionReservation,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Full reservation status (call after advance()): phase progress %,
    ISO deadlines, rent owed, checkpoint shortfalls, queue position."""
    now = now or datetime.now(UTC)
    required = reservation.resources_required or {}
    delivered = reservation.resources_delivered or {}
    amounts = milestone_amounts(reservation.total_cost)

    payload: Dict[str, Any] = {
        "id": str(reservation.id),
        "station_id": str(reservation.station_id),
        "ship_type": reservation.ship_type,
        "ship_name": reservation.ship_name,
        "state": reservation.state,
        "total_cost": reservation.total_cost,
        "deposit_paid": reservation.deposit_paid,
        "credits_paid": reservation.credits_paid,
        "queue_bonus_credit": reservation.queue_bonus_credit,
        "milestones": {
            name: {"amount": amounts[name], "paid": bool((reservation.milestones or {}).get(name))}
            for name in MILESTONE_ORDER
        },
        "resources_required": required,
        "resources_delivered": delivered,
        "uses_specialized_slip": bool(reservation.uses_specialized_slip),
        "created_at": _aware(reservation.created_at).isoformat() if reservation.created_at else None,
        "phase_deadline": (
            _aware(reservation.phase_deadline).isoformat() if reservation.phase_deadline else None
        ),
        "hold_expires_at": (
            _aware(reservation.hold_expires_at).isoformat() if reservation.hold_expires_at else None
        ),
        "claim_expires_at": (
            _aware(reservation.claim_expires_at).isoformat() if reservation.claim_expires_at else None
        ),
    }

    # Queue position (promotion order, not raw creation order).
    if reservation.state == "queued":
        station = db.query(Station).filter(Station.id == reservation.station_id).first()
        peers = (
            db.query(ConstructionReservation)
            .filter(
                ConstructionReservation.station_id == reservation.station_id,
                ConstructionReservation.state == "queued",
            )
            .all()
        )
        order = _sorted_queue(db, station, peers) if station else peers
        payload["queue_position"] = next(
            (i + 1 for i, r in enumerate(order) if r.id == reservation.id), None
        )
        payload["queue_length"] = len(order)

    # Phase progress (% of the running phase, plus overall build %).
    if reservation.state in PHASE_ORDER:
        completed = sum(
            PHASE_SPLITS[p] for p in PHASE_ORDER[: PHASE_ORDER.index(reservation.state)]
        )
        phase_progress = 0.0
        if reservation.phase_deadline is not None:
            wall_seconds = phase_hours(reservation.ship_type, reservation.state) * 3600.0 / game_time.GAME_TIME_SCALE
            remaining = (_aware(reservation.phase_deadline) - now).total_seconds()
            phase_progress = min(1.0, max(0.0, 1.0 - remaining / wall_seconds)) if wall_seconds else 1.0
        else:
            payload["paused"] = True
            payload["needs"] = phase_start_blockers(reservation, reservation.state)
        payload["phase_progress_percent"] = round(phase_progress * 100, 1)
        payload["overall_progress_percent"] = round(
            (completed + PHASE_SPLITS[reservation.state] * phase_progress) * 100, 1
        )
    elif reservation.state == "deposit_collected":
        payload["paused"] = True
        payload["needs"] = phase_start_blockers(reservation, PHASE_ORDER[0])
        payload["phase_progress_percent"] = 0.0
        payload["overall_progress_percent"] = 0.0
    elif reservation.state in ("complete", "claimed"):
        payload["overall_progress_percent"] = 100.0

    # Checkpoint shortfalls for the next gated phase.
    if reservation.state in DELIVERY_STATES:
        if reservation.state == "deposit_collected":
            upcoming = PHASE_ORDER[0]
        else:
            idx = PHASE_ORDER.index(reservation.state)
            upcoming = PHASE_ORDER[min(idx + 1, len(PHASE_ORDER) - 1)]
        payload["next_checkpoint"] = {
            "phase": upcoming,
            "shortfall": checkpoint_shortfall(required, delivered, upcoming),
        }

    # Rent picture.
    if reservation.state in RENT_STATES:
        payload["rent"] = {
            "daily_rent": daily_rent(reservation.total_cost),
            "paid_until": (
                _aware(reservation.rent_paid_until).isoformat()
                if reservation.rent_paid_until else None
            ),
            "overdue_canonical_days": round(rent_overdue_canonical_days(reservation, now), 2),
            "owed": rent_owed_amount(reservation, now),
            "forfeit_after_days": RENT_FORFEIT_DAYS,
        }

    if reservation.state == "hold_active":
        payload["needs"] = [
            "pay the 'keel_laid' milestone to confirm the slip before the hold expires"
        ]

    return payload

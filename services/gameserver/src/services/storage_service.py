"""StorageLocker deposit-flow -- WO-STORE-DEPOSIT-FLOW (STORAGE-HEIST S1),
builds on WO-STORE-LOCKER-MODEL's schema (src/models/storage_locker.py,
migration 61b7e6f4ff93). Multi-trip deposit: a player docked at a
contract's destination Station rents/reuses a locker, deposits the
contract's commodity from their ship's cargo in installments
(ContractCargoDeposit audit rows), and once the locker's accumulated
total reaches the contract's required quantity, the contract is
completed by DELEGATING to contract_service.complete() -- the canonical
completer; this module never reimplements escrow/payout.

SYNC Session, FLUSH-ONLY -- matches contract_service.py's own convention
exactly (the route owns the commit): every deposit call either
accumulates toward, or actually invokes contract_service's own guarded-
transition completion path, in the SAME transaction.

CARGO BRIDGE (documented design decision -- see this WO's own report for
the full reasoning): contract_service.complete() reads its required
quantity directly from player.current_ship.cargo -- it has no concept of
a locker, and reimplementing its escrow/payout logic here was explicitly
out of scope ("call the canonical completer"). So the moment a deposit
brings the locker's accumulated total to >= contract.quantity, this
module temporarily materializes the FULL required quantity onto the
player's CURRENT ship's cargo dict, then calls complete() unmodified --
which decrements that exact amount back off in its own code path. Net
effect on the ship's cargo: zero (the injection is never committed on
its own; complete()'s own decrement happens in the same flush before any
commit reaches the database). This lets ANY ship be docked at the final
deposit, matching canon's "any ship can fulfill any contract over enough
trips" -- the delivering ship doesn't have to be the one that carried
every earlier installment.

LOCK ORDER: Contract is read UNLOCKED throughout -- contract_service.py
never row-locks a Contract (its own module docstring: "No SELECT ... FOR
UPDATE is needed; the guarded UPDATE *is* the lock" -- _guarded_
transition's atomic UPDATE...WHERE status=:from is the concurrency gate).
This module's own new lock family is Locker (the shared resource
concurrent deposit attempts race on -- locked FIRST, mirroring warp_gate_
service's own gate-before-player convention for the same "shared
contested row first" reason) -> Player (SECOND, via contract_service.
_load_player(for_update=True) -- reused, not reimplemented) -> Ship
(THIRD, for the cargo RMW). get_or_create_locker only ever locks Player
(there is no Locker row to lock on a first call) -- a single-resource
lock cannot participate in an AB-BA deadlock against this module's own
Locker-then-Player ordering.

RENT (WO-STORE-FEE-ACCRUAL, D16/D17/D18 -- Max's ruling, delegated):
settle_fee() charges flat rent (locker.rent_rate cr/unit/day, wall-clock)
via a continuous-accrue-and-round-once ledger (D18, see settle_fee's own
docstring) so no salami-slicing and no per-trip minimum-tax. deposit_
cargo() settles BEFORE the deposit for every non-completing installment
(unchanged), but for the installment that completes the contract, settle-
ment is deferred until AFTER contract_service.complete()'s payout credits
the player (D17 -- settling first would floor the bill to near-zero at
the player's poorest moment). settle_fee's re-lock of Locker/Player rows
this function already holds is a harmless same-session re-acquire, not a
new lock-order hazard -- see both functions' own docstrings for the full
reasoning.
"""
import logging
import uuid
from datetime import datetime
from decimal import ROUND_FLOOR, Decimal
from typing import Any, Dict, Optional

from sqlalchemy import func
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from src.models.contract import Contract, ContractStatus
from src.models.ship import Ship
from src.models.storage_locker import ContractCargoDeposit, StorageLocker, StorageLockerStatus
from src.services import contract_service

logger = logging.getLogger(__name__)

_SECONDS_PER_DAY = Decimal(86400)


def _stored_units(db: Session, locker_id: uuid.UUID) -> int:
    """Sum of ContractCargoDeposit.quantity across ALL commodities for a
    locker -- deliberately not filtered to one commodity_type (today
    every contract-tied locker only ever holds its own single commodity,
    but a future standalone/claimable locker with contract_id=None has
    no contract to read commodity_type from at all, so this stays
    correct for that case with zero changes needed later)."""
    total = (
        db.query(func.coalesce(func.sum(ContractCargoDeposit.quantity), 0))
        .filter(ContractCargoDeposit.locker_id == locker_id)
        .scalar()
    )
    return int(total or 0)


class StorageError(Exception):
    """400-class: player-facing validation failure. .args[0] is the
    human-readable detail string the route layer surfaces. Messages that
    carry a stable machine-readable reason are prefixed with a snake_case
    code, matching contract_service.py's own convention."""


class StorageNotFoundError(StorageError):
    """404-class."""


def _load_contract(db: Session, contract_id: uuid.UUID) -> Contract:
    contract = db.query(Contract).filter(Contract.id == contract_id).first()
    if contract is None:
        raise StorageNotFoundError(f"Contract {contract_id} not found")
    return contract


def get_or_create_locker(
    db: Session, player_id: uuid.UUID, contract_id: uuid.UUID,
) -> StorageLocker:
    """One StorageLocker per (player, contract) -- idempotent get-or-
    create. A second call for the same pair returns the EXISTING locker
    rather than minting a duplicate.

    Locks the Player row BEFORE the existence-check-then-insert so two
    concurrent calls for the SAME player+contract pair serialize on it
    (there is no Locker row to lock yet on the very first call -- the
    Player lock is the only resource available to guard the race). A
    unique (owner_player_id, contract_id) index (migration <followup>)
    is the belt-and-suspenders DB-level guarantee for any future call
    path that might bypass this lock."""
    contract = _load_contract(db, contract_id)
    if contract.status != ContractStatus.ACCEPTED:
        raise StorageError(
            f"stale_status: contract {contract.id} is '{contract.status.value}', not 'accepted'"
        )
    if contract.acceptor_player_id != player_id:
        raise StorageError("This contract is not accepted by you")

    player = contract_service._load_player(db, player_id, for_update=True)

    existing = (
        db.query(StorageLocker)
        .filter(StorageLocker.owner_player_id == player_id, StorageLocker.contract_id == contract_id)
        .first()
    )
    if existing is not None:
        return existing

    locker = StorageLocker(
        id=uuid.uuid4(),
        owner_player_id=player.id,
        station_id=contract.destination_station_id,
        contract_id=contract_id,
        status=StorageLockerStatus.ACTIVE,
    )
    db.add(locker)
    db.flush()
    logger.info("Player %s rented locker %s for contract %s", player_id, locker.id, contract_id)
    return locker


def settle_fee(
    db: Session, locker_id: uuid.UUID, *, now: Optional[datetime] = None,
    stored_units_override: Optional[int] = None,
) -> Dict[str, Any]:
    """Settle accrued rent for the elapsed period since `last_fee_
    settled_at`, at the locker's own `rent_rate` cr/unit/day (D16).
    Settle-on-access: this is the ONE settlement path -- there is no
    scheduler in S1 -- called both standalone and internally by
    deposit_cargo. Idempotent over a zero-elapsed-time re-settle (a
    no-op, no double-charge).

    `stored_units_override`: normally this reads the locker's CURRENT
    live stored-units count (as of this call). deposit_cargo's
    COMPLETING branch passes the PRE-final-deposit count explicitly here
    instead (D17, Max's ruling) -- see deposit_cargo's own docstring for
    why: by the time that branch settles, the final deposit row already
    exists, so the live count would over-count units that weren't
    actually sitting in the locker for the elapsed period being billed.

    TIME DOMAIN (verified, not assumed -- see this WO's own report):
    wall-clock, matching contract_service.py's OWN convention exactly
    (grepped: zero references to GAME_TIME_SCALE/scaled_deadline/
    game_time anywhere in that module -- contract deadlines and expiry
    sweeps all run on real datetime.now(UTC)). warp_gate_service.py uses
    GAME_TIME_SCALE for ITS OWN durations, but this locker is a contract-
    economy construct, so contract_service.py -- not warp_gate_service.py
    -- is the directly relevant precedent to match.

    D18 (Max's ruling) -- CONTINUOUS-ACCRUE-AND-ROUND-ONCE, closing the
    salami-slicing gap (many micro-settlements each individually
    rounding to 0cr) WITHOUT per-trip-taxing a legitimate multi-trip
    fulfillment (charging >=1cr on every trip regardless of how little
    time/units it actually represents). Mechanism: `accrued_fee` is a
    MONOTONICALLY INCREASING, never-reset cents-precision ledger of the
    full theoretical fee ever computed (NOT "money actually collected" --
    that reading was this WO's original design, superseded by D18). Each
    settlement adds this period's precise fee (rounded to cents via
    _round_credits, matching the column's own Numeric(19,2) precision --
    NOT left at arbitrary sub-cent precision, which the column can't
    hold between calls anyway) to that ledger, then charges only the
    WHOLE credits newly crossed since the last settlement (floor(new) -
    floor(old)) -- a tiny fractional contribution that doesn't cross a
    whole-credit boundary charges 0 THIS call but is never lost (it's
    still sitting in the ledger, waiting for a future call to push it
    over). A large single-trip contribution that crosses several whole
    credits at once charges all of them in one shot -- no double-billing
    across separate trips, no zero-billing an entire long-held locker.

    FLOOR-AND-FORGIVE KEPT (D17, matching contract_service.abandon()'s
    own exact convention, `player.credits = max(0, credits - penalty)`):
    if the owner can't fully afford the newly-crossed whole-credit
    charge, they pay what they can down to 0 -- the shortfall is
    forgiven, never tracked as debt. The ledger (`accrued_fee`) still
    advances by the FULL theoretical period fee regardless -- once a
    whole-credit boundary is crossed, it's considered "spent" (forgiven
    or collected) and is never re-billed on a later call; this is what
    keeps the no-debt invariant genuinely no-debt rather than deferred.

    Money math: Decimal throughout, ROUND_HALF_UP for the per-period
    fee (contract_service._round_credits, reused not re-derived), FLOOR
    for the whole-credit-crossing delta (never ROUND_HALF_UP there --
    a boundary is "crossed" only once fully reached). FLUSH-ONLY -- the
    route owns the commit, matching every other function in this
    module."""
    now = now or contract_service._now()

    locker = db.query(StorageLocker).filter(StorageLocker.id == locker_id).with_for_update().first()
    if locker is None:
        raise StorageNotFoundError(f"Locker {locker_id} not found")

    days_elapsed = (
        contract_service._as_decimal((now - locker.last_fee_settled_at).total_seconds())
        / _SECONDS_PER_DAY
    )
    if days_elapsed <= 0:
        # Re-settle over the same (or an out-of-order/clock-skew) instant
        # -- a clean no-op, never a negative or double charge.
        return {
            "locker_id": str(locker.id), "days_settled": 0, "units_settled": 0,
            "fee_charged": 0, "accrued_fee_total": float(locker.accrued_fee or 0),
        }

    stored_units = (
        stored_units_override if stored_units_override is not None else _stored_units(db, locker.id)
    )
    if stored_units <= 0:
        # Nothing stored -- no rent accrues, but the anchor still
        # advances so a later settle doesn't re-count this empty period.
        locker.last_fee_settled_at = now
        db.flush()
        return {
            "locker_id": str(locker.id), "days_settled": float(days_elapsed), "units_settled": 0,
            "fee_charged": 0, "accrued_fee_total": float(locker.accrued_fee or 0),
        }

    period_fee = contract_service._round_credits(
        Decimal(stored_units) * contract_service._as_decimal(locker.rent_rate) * days_elapsed
    )

    old_ledger = locker.accrued_fee or Decimal("0")
    new_ledger = old_ledger + period_fee
    old_whole = int(old_ledger.to_integral_value(rounding=ROUND_FLOOR))
    new_whole = int(new_ledger.to_integral_value(rounding=ROUND_FLOOR))
    charge_due = new_whole - old_whole  # D18: only the newly-crossed whole credits

    owner = contract_service._load_player(db, locker.owner_player_id, for_update=True)
    actual_charge = min(charge_due, owner.credits or 0) if charge_due > 0 else 0
    owner.credits = (owner.credits or 0) - actual_charge
    locker.accrued_fee = new_ledger  # ledger always advances by the full period fee
    locker.last_fee_settled_at = now
    db.flush()

    logger.info(
        "Locker %s settled %.4f days: %d units x %s/unit/day -> ledger %s (+%s), "
        "%d credits charged this call (owner %s)",
        locker.id, days_elapsed, stored_units, locker.rent_rate, new_ledger, period_fee,
        actual_charge, locker.owner_player_id,
    )
    return {
        "locker_id": str(locker.id), "days_settled": float(days_elapsed), "units_settled": stored_units,
        "fee_charged": actual_charge, "accrued_fee_total": float(new_ledger),
    }


def _load_and_lock_deposit_targets(
    db: Session, locker_id: uuid.UUID, player_id: uuid.UUID,
) -> tuple:
    """Locks + validates the Locker, then loads its Contract, then locks
    + validates the Player -- the module's own Locker-then-Player order
    (see module docstring's LOCK ORDER section). Raises StorageError /
    StorageNotFoundError on any guard failure. Pulled out of deposit_
    cargo's own body purely to keep that function's cyclomatic
    complexity under the ruff C901 gate (genuinely enforced -- `C90` is
    in this project's pyproject.toml `[tool.ruff] select`, not just
    available) -- no behavior change from the pre-extraction inline
    version."""
    locker = db.query(StorageLocker).filter(StorageLocker.id == locker_id).with_for_update().first()
    if locker is None:
        raise StorageNotFoundError(f"Locker {locker_id} not found")
    if locker.owner_player_id != player_id:
        raise StorageError("This locker does not belong to you")
    if locker.status != StorageLockerStatus.ACTIVE:
        raise StorageError(
            f"locker_not_active: locker {locker.id} is '{locker.status.value}', not 'active'"
        )
    if locker.contract_id is None:
        raise StorageError("This locker is not tied to a contract")

    contract = _load_contract(db, locker.contract_id)
    if contract.status != ContractStatus.ACCEPTED:
        raise StorageError(
            f"stale_status: contract {contract.id} is '{contract.status.value}', not 'accepted'"
        )

    player = contract_service._load_player(db, player_id, for_update=True)
    if not player.is_docked or player.current_port_id != locker.station_id:
        raise StorageError(
            "wrong_station: you must be docked at the locker's station to deposit"
        )
    if locker.station_id != contract.destination_station_id:
        # Structurally unreachable via get_or_create_locker (which always
        # pins locker.station_id = contract.destination_station_id at
        # creation) -- checked directly rather than assumed, so a future
        # locker-relocation feature can never silently violate this
        # invariant without a loud rejection here.
        raise StorageError(
            "wrong_station: the locker's station no longer matches the contract's destination"
        )

    return locker, contract, player


def deposit_cargo(
    db: Session, locker_id: uuid.UUID, player_id: uuid.UUID, quantity: int,
) -> Dict[str, Any]:
    """Deposit `quantity` units of the locker's contract commodity from
    the player's current ship's cargo into the locker (a
    ContractCargoDeposit audit row). Auto-completes the contract -- via
    contract_service.complete(), see module docstring's "CARGO BRIDGE" --
    the moment the locker's accumulated deposits reach the contract's
    required quantity. FLUSH-ONLY; the route owns the commit.

    D17 (Max's ruling, PAYOUT-then-settle) -- when THIS deposit is the
    one that completes the contract, rent is settled AFTER contract_
    service.complete()'s payout credits the player, not before. Settling
    first would floor the final bill to near-zero at the player's
    poorest moment (right before they get paid), making the fee inert
    for exactly the case it exists to charge. Every OTHER (non-
    completing) deposit keeps the original settle-before-deposit
    ordering -- there's no payout event to reorder around."""
    if quantity <= 0:
        raise StorageError("invalid_quantity: deposit quantity must be positive")

    # Locker locked FIRST, Player locked SECOND -- see module docstring's
    # LOCK ORDER section (and _load_and_lock_deposit_targets's own).
    locker, contract, player = _load_and_lock_deposit_targets(db, locker_id, player_id)

    # Peek ahead: will THIS deposit push the locker to full quantity?
    # Both sides of this equality are captured atomically under the
    # Locker row lock already held above -- no concurrent writer can
    # insert a ContractCargoDeposit between this read and the one below,
    # so old_stored_units + quantity is guaranteed to equal the real
    # post-deposit accumulated count computed further down.
    quantity_required = int(contract.quantity or 0)
    old_stored_units = _stored_units(db, locker.id)
    will_complete = (old_stored_units + quantity) >= quantity_required

    settlement: Optional[Dict[str, Any]] = None
    if not will_complete:
        # WO-STORE-FEE-ACCRUAL: settle-before-deposit ordering (D17
        # unchanged for the non-completing case -- no payout event to
        # reorder around). Settles rent for the OLD stored-units count
        # (whatever was sitting in the locker BEFORE this deposit) over
        # the elapsed period since last_fee_settled_at, then advances
        # the anchor to now -- so the period this NEW deposit's units
        # are about to join never gets back-charged for time they
        # weren't actually stored. settle_fee re-locks the Locker row
        # it's already holding (a harmless idempotent re-acquire, same
        # session) then locks the Player row -- consistent with this
        # function's own Locker-then-Player order.
        settlement = settle_fee(db, locker.id, now=contract_service._now())

    # Ship locked THIRD, for the cargo RMW.
    ship = (
        db.query(Ship)
        .filter(Ship.id == player.current_ship_id, Ship.owner_id == player.id)
        .with_for_update()
        .first()
    )
    if ship is None:
        raise StorageError("No active ship to deposit cargo from")
    cargo = ship.cargo if isinstance(ship.cargo, dict) else {}
    contents = dict(cargo.get("contents") or {})
    held = int(contents.get(contract.commodity_type, 0) or 0)
    if held < quantity:
        raise StorageError(
            f"insufficient_cargo: you have {held} {contract.commodity_type}, "
            f"tried to deposit {quantity}"
        )

    # --- All guards passed -- mutate. ---
    contents[contract.commodity_type] = held - quantity
    cargo["contents"] = contents
    cargo["used"] = sum(int(q) for q in contents.values() if isinstance(q, (int, float)))
    ship.cargo = cargo
    flag_modified(ship, "cargo")

    deposit_row = ContractCargoDeposit(
        id=uuid.uuid4(), locker_id=locker.id, commodity=contract.commodity_type,
        quantity=quantity, deposited_by=player_id,
    )
    db.add(deposit_row)
    db.flush()

    accumulated = _stored_units(db, locker.id)

    completed = False
    complete_result: Optional[Dict[str, Any]] = None
    if accumulated >= quantity_required:
        # CARGO BRIDGE -- see module docstring. Materialize the full
        # required quantity onto the ship's cargo so contract_service.
        # complete()'s own (unmodified) cargo check + decrement passes;
        # net effect on the ship is zero once complete() finishes, all
        # inside this same flush/transaction.
        #
        # FRAGILE COUPLING -- verified against contract_service.complete()'s
        # actual source (WO-STORE-DEPOSIT-FLOW report) rather than assumed;
        # re-verify these three facts if complete() is ever touched:
        #   1. It decrements the ship's cargo by EXACTLY int(contract.
        #      quantity or 0) -- the same value injected below. If that
        #      computation ever changes, net-zero breaks.
        #   2. It ONLY reads/validates/decrements cargo -- no other side
        #      effect keyed off cargo (a history log, a value metric) that
        #      would see and record this phantom amount.
        #   3. It never capacity-checks cargo["used"] against cargo
        #      ["capacity"] -- confirmed no "capacity" reference anywhere
        #      in contract_service.py. If a capacity guard is ever added
        #      to complete(), this transient over-capacity injection would
        #      need a different bridge (or complete() would need a
        #      cargo_source parameter instead -- the fallback design,
        #      deliberately not built here to keep this change isolated).
        # Also confirmed: Ship's only mapper-level event listeners
        # (ship_registry.py) fire on before_insert/after_insert only --
        # never on an UPDATE to an existing row's cargo, so this injection
        # (an UPDATE) can't trigger them.
        bridge_cargo = ship.cargo if isinstance(ship.cargo, dict) else {}
        bridge_contents = dict(bridge_cargo.get("contents") or {})
        bridge_contents[contract.commodity_type] = (
            int(bridge_contents.get(contract.commodity_type, 0) or 0) + quantity_required
        )
        bridge_cargo["contents"] = bridge_contents
        bridge_cargo["used"] = sum(
            int(q) for q in bridge_contents.values() if isinstance(q, (int, float))
        )
        ship.cargo = bridge_cargo
        flag_modified(ship, "cargo")
        db.flush()

        # contract_service.complete() is the canonical completer -- never
        # reimplemented here. Any exception it raises propagates straight
        # through this function uncaught: the route's existing rollback
        # then discards the WHOLE deposit attempt, including the
        # installment that would have triggered completion (a clean
        # all-or-nothing failure is safer than a locker silently stuck at
        # exactly full quantity with no way to re-trigger completion).
        complete_result = contract_service.complete(db, contract.id, player_id)
        locker.status = StorageLockerStatus.RELEASED
        completed = True

        # D17 (Max's ruling): settle the FINAL rent period AFTER the
        # completion payout above has already credited the player, not
        # before -- see this function's own docstring. stored_units_
        # override=old_stored_units: the units THIS deposit just added
        # never actually sat in the locker accruing rent -- they arrive
        # and are immediately bridged back out by complete() above, so
        # billing them here would charge for storage time that never
        # happened. settle_fee re-locks the Locker/Player rows this
        # function already holds (harmless idempotent re-acquire).
        settlement = settle_fee(
            db, locker.id, now=contract_service._now(), stored_units_override=old_stored_units,
        )
        logger.info(
            "Locker %s reached full quantity (%d/%d %s) -- contract %s auto-completed by player %s",
            locker.id, accumulated, quantity_required, contract.commodity_type, contract.id, player_id,
        )
    else:
        logger.info(
            "Player %s deposited %d %s into locker %s (%d/%d)",
            player_id, quantity, contract.commodity_type, locker.id, accumulated, quantity_required,
        )

    if settlement is None:
        # Defensive fallback -- structurally unreachable given the Locker
        # row lock (the peek's old_stored_units + quantity == accumulated
        # invariant, see its own comment above) but money code doesn't
        # get to silently NoneType-crash if that invariant is ever
        # violated by a future edit.
        settlement = settle_fee(db, locker.id, now=contract_service._now())

    return {
        "locker_id": str(locker.id),
        "deposited": quantity,
        "accumulated": accumulated,
        "quantity_required": quantity_required,
        "fee_charged": settlement["fee_charged"],
        "completed": completed,
        "complete_result": complete_result,
    }

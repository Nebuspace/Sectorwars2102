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
"""
import logging
import uuid
from typing import Any, Dict, Optional

from sqlalchemy import func
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from src.models.contract import Contract, ContractStatus
from src.models.ship import Ship
from src.models.storage_locker import ContractCargoDeposit, StorageLocker, StorageLockerStatus
from src.services import contract_service

logger = logging.getLogger(__name__)


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


def deposit_cargo(
    db: Session, locker_id: uuid.UUID, player_id: uuid.UUID, quantity: int,
) -> Dict[str, Any]:
    """Deposit `quantity` units of the locker's contract commodity from
    the player's current ship's cargo into the locker (a
    ContractCargoDeposit audit row). Auto-completes the contract -- via
    contract_service.complete(), see module docstring's "CARGO BRIDGE" --
    the moment the locker's accumulated deposits reach the contract's
    required quantity. FLUSH-ONLY; the route owns the commit."""
    if quantity <= 0:
        raise StorageError("invalid_quantity: deposit quantity must be positive")

    # Locker locked FIRST -- the shared resource concurrent deposit
    # attempts on the SAME locker race on (see module docstring's LOCK
    # ORDER section).
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

    # Player locked SECOND, per the documented order above.
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

    accumulated = (
        db.query(func.coalesce(func.sum(ContractCargoDeposit.quantity), 0))
        .filter(
            ContractCargoDeposit.locker_id == locker.id,
            ContractCargoDeposit.commodity == contract.commodity_type,
        )
        .scalar()
    )
    accumulated = int(accumulated or 0)
    quantity_required = int(contract.quantity or 0)

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
        logger.info(
            "Locker %s reached full quantity (%d/%d %s) -- contract %s auto-completed by player %s",
            locker.id, accumulated, quantity_required, contract.commodity_type, contract.id, player_id,
        )
    else:
        logger.info(
            "Player %s deposited %d %s into locker %s (%d/%d)",
            player_id, quantity, contract.commodity_type, locker.id, accumulated, quantity_required,
        )

    return {
        "locker_id": str(locker.id),
        "deposited": quantity,
        "accumulated": accumulated,
        "quantity_required": quantity_required,
        "completed": completed,
        "complete_result": complete_result,
    }

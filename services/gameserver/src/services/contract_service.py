"""
Trade Contract lifecycle -- WO-ECON-CONTRACT-1-KERNEL lane 2. Only the
`posted -> accepted -> completed` / `abandon` / `expire` transitions on
`cargo_delivery` are exercised here (the single-acceptor, no-load-step
happy path this WO proves end-to-end per contracts.md:421-431 step 2).
Player-issued posting/escrow, bulk-procurement partial fulfillment,
insurance, and disputes are later build steps and this module never
touches those columns.

SYNC Session throughout -- matches slipdrive_service.py / escape_pod_
service.py / fuel_delivery_service.py (this WO's own direct precedent) and
api/routes/trading.py's own `db: Session = Depends(get_db)` convention
despite its route defs being `async def`. FLUSH-ONLY -- the route (or the
scheduler wrapper, for the sweep) owns the commit.

CONCURRENCY -- accept/complete/abandon each go through `_guarded_
transition`: a single atomic `UPDATE contracts SET status=:to WHERE
id=:id AND status=:from` (Postgres re-checks the WHERE clause against the
row's live state at write time and serializes concurrent writers to the
same row -- the second writer's WHERE simply matches 0 rows once the first
commits its status change). No `SELECT ... FOR UPDATE` is needed; the
guarded UPDATE *is* the lock. `accept` explicitly charges the acceptance
fee AFTER a successful guarded transition, never before -- a race loser
raises ContractConflictError and is never billed (dispatch's "409s
feeless").

The transition table (`LEGAL_TRANSITIONS`) is consulted BEFORE the DB
round-trip and is real, load-bearing DATA, not decoration -- removing an
edge from it (see test_contract_service.py's mutation test) makes an
otherwise-valid, DB-verified transition 409 without ever touching the
database.
"""
import logging
import uuid
from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Dict, FrozenSet, Optional

from sqlalchemy import update
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from src.models.contract import Contract, ContractIssuerType, ContractStatus
from src.models.player import Player

logger = logging.getLogger(__name__)


class ContractError(Exception):
    """400-class: player-facing validation failure. .args[0] is the
    human-readable detail string the route layer surfaces."""


class ContractNotFoundError(ContractError):
    """404-class."""


class ContractConflictError(ContractError):
    """409-class: an illegal or raced state transition. No mutation
    occurred -- the caller's view of the contract was stale."""


# --- state machine (real data, not decoration -- see module docstring) ---

LEGAL_TRANSITIONS: Dict[ContractStatus, FrozenSet[ContractStatus]] = {
    ContractStatus.POSTED: frozenset({ContractStatus.ACCEPTED, ContractStatus.EXPIRED}),
    ContractStatus.ACCEPTED: frozenset({ContractStatus.COMPLETED, ContractStatus.CANCELLED}),
}

# [NO-CANON] contracts.md:41 gives the acceptance-fee PERCENTAGE (2.0, now
# Contract.acceptance_fee_pct's default) but never states a rounding rule
# for the resulting cash amount. HALF_UP to the cent is the ordinary
# commercial-rounding default and is what this kernel pins; proposed to
# DECISIONS.md for ratification. At the current 2.00% default this never
# actually exercises a non-terminating case (2% of any whole-cent payment
# is itself exact to 2dp) -- the rule matters once acceptance_fee_pct is
# tuned to a non-round percentage in a later balance pass.
_CENTS = Decimal("0.01")


def _round_credits(amount: Decimal) -> Decimal:
    return amount.quantize(_CENTS, rounding=ROUND_HALF_UP)


def _now() -> datetime:
    return datetime.now(UTC)


def _as_decimal(value: Any) -> Decimal:
    return value if isinstance(value, Decimal) else Decimal(str(value))


def _guarded_transition(
    db: Session,
    contract: Contract,
    from_status: ContractStatus,
    to_status: ContractStatus,
    **column_updates: Any,
) -> Contract:
    """Atomically move `contract` from `from_status` to `to_status`, or
    raise ContractConflictError with NO mutation. See module docstring."""
    if to_status not in LEGAL_TRANSITIONS.get(from_status, frozenset()):
        raise ContractConflictError(
            f"illegal_transition: {from_status.value} -> {to_status.value} "
            f"is not in the contract state machine"
        )
    values = {"status": to_status, **column_updates}
    stmt = (
        update(Contract)
        .where(Contract.id == contract.id, Contract.status == from_status)
        .values(**values)
    )
    result = db.execute(stmt)
    if result.rowcount == 0:
        raise ContractConflictError(
            f"stale_status: contract {contract.id} is no longer "
            f"'{from_status.value}' -- it was already transitioned "
            f"(lost a race, expired, or the deadline swept it)"
        )
    for key, value in values.items():
        setattr(contract, key, value)
    return contract


def _load_contract(db: Session, contract_id: uuid.UUID) -> Contract:
    contract = db.query(Contract).filter(Contract.id == contract_id).first()
    if contract is None:
        raise ContractNotFoundError(f"Contract {contract_id} not found")
    return contract


def _load_player(db: Session, player_id: uuid.UUID) -> Player:
    player = db.query(Player).filter(Player.id == player_id).first()
    if player is None:
        raise ContractError(f"Player {player_id} not found")
    return player


# --- transitions -----------------------------------------------------------

def accept(
    db: Session, contract_id: uuid.UUID, acceptor_player_id: uuid.UUID,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Accept a `posted` contract. Charges the acceptance fee ONLY after
    the guarded transition succeeds -- a race loser (or an attempt on an
    already-expired contract) pays nothing. FLUSH-ONLY."""
    now = now or _now()
    contract = _load_contract(db, contract_id)

    if contract.status != ContractStatus.POSTED:
        raise ContractConflictError(
            f"stale_status: contract {contract.id} is '{contract.status.value}', not 'posted'"
        )
    if contract.issuer_type == ContractIssuerType.PLAYER and contract.issuer_id == acceptor_player_id:
        raise ContractError("Cannot accept your own contract")
    if contract.deadline is not None and now >= contract.deadline:
        raise ContractConflictError("expired: this contract's deadline has already passed")

    acceptor = _load_player(db, acceptor_player_id)
    fee = _round_credits(_as_decimal(contract.payment) * _as_decimal(contract.acceptance_fee_pct) / Decimal(100))
    if Decimal(acceptor.credits or 0) < fee:
        raise ContractError(
            f"insufficient_credits: acceptance fee is {fee}, you have {acceptor.credits or 0}"
        )

    _guarded_transition(
        db, contract, ContractStatus.POSTED, ContractStatus.ACCEPTED,
        acceptor_player_id=acceptor_player_id, accepted_at=now,
    )

    acceptor.credits = int(Decimal(acceptor.credits or 0) - fee)
    db.flush()

    logger.info(
        "Player %s accepted contract %s (fee %s)", acceptor_player_id, contract.id, fee,
    )
    return {
        "id": str(contract.id),
        "status": contract.status.value,
        "acceptor_player_id": str(acceptor_player_id),
        "accepted_at": now,
        "acceptance_fee_charged": float(fee),
        "remaining_balance": acceptor.credits,
        "deadline": contract.deadline,
    }


def complete(
    db: Session, contract_id: uuid.UUID, player_id: uuid.UUID,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Deliver-at-destination: the acceptor must be docked at the
    contract's destination station with `quantity` units of
    `commodity_type` in their ship's cargo. Validates, decrements cargo,
    pays, and transitions accepted -> completed. Wrong station or short
    cargo raise WITHOUT any state change. FLUSH-ONLY.

    [NO-CANON] cargo must be player-sourced (bought/carried through the
    ordinary trading flow) -- the canon "one-time discounted pickup right
    at the origin station" (contracts.md:115) is a separate mechanic that
    would need to hook trading.py's buy path and is not built by this
    kernel; flagged as a follow-up, not silently invented here."""
    now = now or _now()
    contract = _load_contract(db, contract_id)
    if contract.acceptor_player_id != player_id:
        raise ContractError("This contract is not accepted by you")
    # Cheap advisory pre-check, BEFORE the cargo check below -- a legitimate
    # retry (the first call already decremented cargo) must not surface a
    # confusing "insufficient cargo" for a contract that's actually already
    # completed. The atomic guarded transition further down remains the
    # authoritative concurrency gate for a genuine race between two
    # completion attempts; this is purely about giving an ORDINARY
    # (non-racing) retry the right error.
    if contract.status != ContractStatus.ACCEPTED:
        raise ContractConflictError(
            f"stale_status: contract {contract.id} is '{contract.status.value}', not 'accepted'"
        )

    player = _load_player(db, player_id)
    if not player.is_docked or player.current_port_id != contract.destination_station_id:
        raise ContractConflictError(
            "wrong_station: you must be docked at the contract's destination "
            "station to complete delivery"
        )

    ship = player.current_ship
    if ship is None:
        raise ContractError("No active ship to deliver cargo from")
    cargo = ship.cargo if isinstance(ship.cargo, dict) else {}
    contents = dict(cargo.get("contents") or {})
    quantity_required = int(contract.quantity or 0)
    held = int(contents.get(contract.commodity_type, 0) or 0)
    if held < quantity_required:
        raise ContractError(
            f"insufficient_cargo: contract requires {quantity_required} "
            f"{contract.commodity_type}, you have {held}"
        )

    _guarded_transition(
        db, contract, ContractStatus.ACCEPTED, ContractStatus.COMPLETED, completed_at=now,
    )

    contents[contract.commodity_type] = held - quantity_required
    cargo["contents"] = contents
    cargo["used"] = sum(int(q) for q in contents.values() if isinstance(q, (int, float)))
    ship.cargo = cargo
    flag_modified(ship, "cargo")

    payout = int(_round_credits(_as_decimal(contract.payment)))
    player.credits = (player.credits or 0) + payout
    db.flush()

    logger.info(
        "Player %s completed contract %s, paid %d credits", player_id, contract.id, payout,
    )
    return {
        "id": str(contract.id),
        "status": contract.status.value,
        "completed_at": now,
        "payout": payout,
        "credits": player.credits,
    }


def abandon(
    db: Session, contract_id: uuid.UUID, player_id: uuid.UUID,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Walk away from an accepted contract. Charges the 1.0x penalty,
    clamped to 0 -- this kernel has no debt-ledger model to record a
    deficit against (contracts.md:324's "debt that must be cleared" isn't
    built anywhere in this codebase; NO-CANON, flagged as a follow-up
    rather than invented here). FLUSH-ONLY."""
    now = now or _now()
    contract = _load_contract(db, contract_id)
    if contract.acceptor_player_id != player_id:
        raise ContractError("This contract is not accepted by you")
    if contract.status != ContractStatus.ACCEPTED:
        raise ContractConflictError(
            f"stale_status: contract {contract.id} is '{contract.status.value}', not 'accepted'"
        )

    player = _load_player(db, player_id)

    _guarded_transition(db, contract, ContractStatus.ACCEPTED, ContractStatus.CANCELLED)

    penalty = int(_round_credits(_as_decimal(contract.penalty)))
    player.credits = max(0, (player.credits or 0) - penalty)
    db.flush()

    logger.info(
        "Player %s abandoned contract %s (penalty %d)", player_id, contract.id, penalty,
    )
    return {
        "id": str(contract.id),
        "status": contract.status.value,
        "penalty_charged": penalty,
        "credits": player.credits,
    }


def sweep_expired_contracts(db: Session, now: Optional[datetime] = None) -> Dict[str, int]:
    """Bulk-expire every `posted` contract whose deadline has strictly
    passed. Bulk conditional UPDATE (not per-row `_guarded_transition` --
    a batch sweep has no single caller to race against; the WHERE clause
    is its own safety net against a second scheduler instance). FLUSH-ONLY
    -- the scheduler wrapper commits."""
    now = now or _now()
    stmt = (
        update(Contract)
        .where(Contract.status == ContractStatus.POSTED, Contract.deadline < now)
        .values(status=ContractStatus.EXPIRED)
    )
    result = db.execute(stmt)
    db.flush()
    return {"expired": result.rowcount or 0}

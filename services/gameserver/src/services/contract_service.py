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
from typing import Any, Dict, FrozenSet, List, Optional

from sqlalchemy import update
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from src.models.contract import Contract, ContractEscrowState, ContractIssuerType, ContractStatus, ContractType
from src.models.player import Player
from src.models.resource import Resource
from src.models.station import Station, StationStatus

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
    # WO-ECON-CONTRACT-2-PLAYER-ESCROW adds POSTED -> CANCELLED (issuer
    # withdraws pre-accept, contracts.md:69) -- purely additive; no
    # existing NPC-facing function transitions POSTED -> CANCELLED, so
    # this new edge is inert for the NPC path.
    ContractStatus.POSTED: frozenset({ContractStatus.ACCEPTED, ContractStatus.EXPIRED, ContractStatus.CANCELLED}),
    # WO-DRIFT-econ-accepted-deadline-expiry adds ACCEPTED -> EXPIRED. Canon
    # (contracts.md's state-transition matrix) puts this edge on
    # `in_progress`, not `accepted` -- this codebase collapses `in_progress`
    # into `accepted` (no code path ever sets IN_PROGRESS), so this is the
    # code-equivalent of canon's in_progress -> expired edge. See
    # sweep_expired_accepted_contracts's own docstring for the transition.
    ContractStatus.ACCEPTED: frozenset({ContractStatus.COMPLETED, ContractStatus.CANCELLED, ContractStatus.EXPIRED}),
}

# --- WO-ECON-CONTRACT-2-PLAYER-ESCROW: player-posted contract constants ---

# contracts.md:245 -- concrete canon floor. Verified: neither ADR-0062 nor
# contracts.md gives payment/quantity/penalty bounds for player posting --
# only this one concrete deadline floor. Not invented further.
PLAYER_POST_MIN_DEADLINE_HOURS = 1.0

# contracts.md:69 -- issuer refund on a pre-accept withdrawal. The
# remaining 1% is a "posting-fee sink" (:165) -- this codebase has no
# treasury/sink model to receive it, so it is simply never credited
# anywhere (evaporates), matching dispatch's own "no treasury sink may
# exist" note. [NO-CANON] flagged, not invented.
PLAYER_POST_CANCEL_REFUND_PCT_PRE_ACCEPT = Decimal("99.0")

# contracts.md:76/:166 -- post-accept ("mutual") cancel kill-fee is TWO
# components: the acceptance-fee-equivalent (already stored per-contract
# as acceptance_fee_pct, reused here) PLUS a flat 10% cancellation
# component. [NO-CANON] base for the 10%: the escrow table's Issuer
# column literally says "escrow - accept_fee - 10% kill-fee" without
# naming the 10%'s base; this kernel uses `payment` (matching accept_fee's
# own payment-relative base, not a compound escrow-relative one) --
# proposed to DECISIONS. Per the escrow table (NOT the state-diagram's
# terser prose) BOTH deducted components sink -- "Kill-fee -> escrow
# sink", acceptor column reads flat "0" -- the acceptor is paid nothing.
PLAYER_POST_CANCEL_FEE_PCT_POST_ACCEPT = Decimal("10.0")

# contracts.md:245 -- concrete canon cap.
MAX_ACTIVE_PLAYER_POSTINGS_PER_REGION = 10

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


# --- WO-ECON-CONTRACT-2-PLAYER-ESCROW: posting-validation helpers ---------

def _is_valid_commodity(db: Session, commodity_type: str) -> bool:
    """Live-registry check -- NOT a hardcoded enum, matches contract.py's
    own commodity_type docstring."""
    return (
        db.query(Resource)
        .filter(Resource.name == commodity_type, Resource.is_active.is_(True))
        .first()
        is not None
    )


def _is_player_blocklisted(db: Session, issuer_player_id: uuid.UUID) -> bool:
    """[NO-CANON] contracts.md:245 requires "caller not blocklisted" at
    POST time (a platform-level posting gate on the issuer themselves --
    distinct from :368's ACCEPT-time "acceptor has active hostility with
    issuer" pairwise check, which is a separate, unbuilt gap in `accept()`
    out of THIS WO's scope). No blocklist/suspension model exists ANYWHERE
    in this codebase (verified: no Blocklist/BlockedPlayer model, no
    block_list column). This is a documented NO-OP SEAM, not a silently
    invented mechanism -- always returns False (never blocks) until a real
    blocklist model exists for a future WO to wire here. Exercised by a
    monkeypatch-to-True test to prove the seam is actually consulted, not
    decorative."""
    return False


def _active_player_postings_in_region(db: Session, issuer_player_id: uuid.UUID, region_id: uuid.UUID) -> int:
    """contracts.md:245 -- "active postings by caller < 10 per region".
    Player-issued rows only (`issuer_id` is the player's own id for these,
    see contract.py's issuer_id docstring -- NPC rows can never match this
    filter). Contract has no region_id of its own, so this resolves each
    active posting's destination station's region in a second pass rather
    than a literal SQL join (matches this codebase's DB-free-testable
    multi-query convention over ORM join syntax)."""
    active = (
        db.query(Contract)
        .filter(
            Contract.issuer_type == ContractIssuerType.PLAYER,
            Contract.issuer_id == issuer_player_id,
            Contract.status.in_([ContractStatus.POSTED, ContractStatus.ACCEPTED]),
        )
        .all()
    )
    if not active:
        return 0
    station_ids = {c.destination_station_id for c in active if c.destination_station_id is not None}
    stations = db.query(Station.id, Station.region_id).filter(Station.id.in_(station_ids)).all()
    region_by_station = {s.id: s.region_id for s in stations}
    return sum(1 for c in active if region_by_station.get(c.destination_station_id) == region_id)


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
    # WO-ECON-CONTRACT-2-PLAYER-ESCROW: the payout above is identical code
    # for NPC and player-issued contracts -- for NPC rows it mints (NPC
    # credits are canonically infinite, contracts.md:155); for player-issued
    # rows the credits already left the issuer's wallet at POST time (see
    # post_player_contract), so this is a RELEASE of already-held funds,
    # not new money. The only difference is marking escrow terminally
    # released -- a second completion attempt is already impossible via
    # the guarded transition above; this just makes the terminal state
    # explicit for any future reader/query of escrow_state.
    if contract.issuer_type == ContractIssuerType.PLAYER:
        contract.escrow_state = ContractEscrowState.RELEASED
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

    # WO-ECON-CONTRACT-2-PLAYER-ESCROW addition: when the ACCEPTOR walks
    # away from a PLAYER-issued contract, the issuer's escrow (debited in
    # full at post time, see post_player_contract) would otherwise be
    # stranded forever -- nothing else in this kernel ever credits it
    # back. Refund it in FULL -- no kill-fee; the issuer did nothing
    # wrong, and the acceptor's own penalty above (unchanged) is the only
    # cost of this outcome. [NO-CANON]: canon's "acceptor walks" language
    # (contracts.md:75) is bulk_procurement-specific (returns to
    # `posted`, not `cancelled`) and doesn't literally cover this kernel's
    # own single-acceptor cargo_delivery abandon mechanic (built in
    # WO-ECON-CONTRACT-1-KERNEL, unchanged here) -- this closes a real
    # escrow-conservation gap rather than citing a specific canon row.
    # NPC-issued rows (escrow_amount always 0) are byte-unchanged.
    if contract.issuer_type == ContractIssuerType.PLAYER:
        issuer = _load_player(db, contract.issuer_id)
        refund = int(_round_credits(_as_decimal(contract.escrow_amount)))
        issuer.credits = (issuer.credits or 0) + refund
        contract.escrow_state = ContractEscrowState.REFUNDING
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
    passed. contracts.md:71 -- "deadline expires unaccepted -> expired,
    escrow returned to issuer": a PLAYER-issued row with escrow still HELD
    gets a per-row pass that flips status AND refunds the issuer, mirroring
    abandon()'s exact refund idiom (issuer credited `contract.escrow_amount`,
    `escrow_state` -> REFUNDING) rather than inventing a new one. NPC-issued
    rows (escrow_amount always 0, escrow_state stays HELD by default) never
    match the player-only filter below and fall straight through to the
    original bulk conditional UPDATE, byte-unchanged from WO-ECON-CONTRACT-
    1-KERNEL.

    IDEMPOTENCY: the per-row query only selects `escrow_state == HELD` --
    a row already REFUNDING/RELEASED/DISPUTED (already handled by an
    earlier sweep tick, or by a race with the issuer's own cancel/abandon)
    is excluded and never double-refunded. Each candidate is drained via
    `.first()` rather than `.all()` (this module's `_active_player_
    postings_in_region` precedent for a materialized list doesn't apply
    here -- see test_contract_service.py's sibling fake, which never grew
    an `.all()` method): every per-row UPDATE re-checks `status ==
    posted` in its WHERE clause -- the same optimistic-concurrency guard
    `_guarded_transition` uses -- so a row raced away between the SELECT
    and the UPDATE (by a live player's own cancel/abandon on a *different*
    session, since the CEXP advisory lock only serializes concurrent
    *sweep* instances) is simply skipped; the loop's next SELECT is a
    fresh server-side-filtered query, so a truly raced row's now-updated
    status excludes it from every subsequent iteration -- no infinite loop.
    A per-row UPDATE that DOES match is applied to the in-memory `contract`
    object too (same convention as `_guarded_transition`'s own trailing
    `setattr` loop -- a raw Core `update()` never syncs the ORM identity
    map on its own).

    Bulk conditional UPDATE for everything else (not per-row `_guarded_
    transition` -- a batch sweep has no single caller to race against; the
    WHERE clause is its own safety net against a second scheduler
    instance). FLUSH-ONLY -- the scheduler wrapper commits inside the CEXP
    advisory lock, so the whole per-row-refund + bulk-expire pass is one
    atomic transaction."""
    now = now or _now()

    expired_with_refund = 0
    while True:
        candidate = (
            db.query(Contract)
            .filter(
                Contract.status == ContractStatus.POSTED,
                Contract.deadline < now,
                Contract.issuer_type == ContractIssuerType.PLAYER,
                Contract.escrow_state == ContractEscrowState.HELD,
            )
            .first()
        )
        if candidate is None:
            break

        row_stmt = (
            update(Contract)
            .where(Contract.id == candidate.id, Contract.status == ContractStatus.POSTED)
            .values(status=ContractStatus.EXPIRED)
        )
        result = db.execute(row_stmt)
        if result.rowcount == 0:
            # Raced away between the SELECT above and this UPDATE (a live
            # cancel/abandon on a different session/connection) -- no
            # mutation occurred, so the next iteration's fresh SELECT
            # simply won't return this row again (its real DB status has
            # already moved past 'posted').
            continue
        candidate.status = ContractStatus.EXPIRED

        if candidate.escrow_amount and candidate.escrow_amount > 0:
            issuer = _load_player(db, candidate.issuer_id)
            refund = int(_round_credits(_as_decimal(candidate.escrow_amount)))
            issuer.credits = (issuer.credits or 0) + refund
            candidate.escrow_state = ContractEscrowState.REFUNDING
        expired_with_refund += 1

    bulk_stmt = (
        update(Contract)
        .where(Contract.status == ContractStatus.POSTED, Contract.deadline < now)
        .values(status=ContractStatus.EXPIRED)
    )
    bulk_result = db.execute(bulk_stmt)
    db.flush()
    return {"expired": expired_with_refund + (bulk_result.rowcount or 0)}


def sweep_expired_accepted_contracts(db: Session, now: Optional[datetime] = None) -> Dict[str, int]:
    """WO-DRIFT-econ-accepted-deadline-expiry: bulk-expire every `accepted`
    contract whose deadline has strictly passed without completion.
    Canon (contracts.md's state-transition matrix) puts this edge on
    `in_progress -> expired`; this codebase never sets IN_PROGRESS (`accept`
    -> `ACCEPTED` is the terminal pre-completion state here), so this sweep
    is the code-equivalent of canon's in_progress deadline-expiry, not a new
    concept -- see the LEGAL_TRANSITIONS comment above.

    ACCEPTOR PENALTY -- canon-backed (contracts.md's Penalties section:
    "penalty credits are debited from the acceptor's account"). Charges
    `contract.penalty`, clamped to 0, the EXACT idiom `abandon()` already
    uses for the player-initiated failure case (this sweep is the
    deadline-initiated twin of that same failure).

    [NO-CANON] issuer escrow disposition on an ACCEPTOR-caused failure is
    NOT canon-pinned. contracts.md's escrow table's `Expired / failed` row
    shows no explicit issuer credit for this case ("Issuer" column reads
    "--"), while the separate Penalties section only speaks to the
    acceptor's own debit -- the two sections don't agree on what happens to
    the issuer's escrow here. Two plausible readings: (a) REFUND the issuer
    in full -- they never received their goods, mirroring `abandon()`'s and
    `sweep_expired_contracts`'s own proven refund idiom; (b) FORFEIT the
    escrow -- the issuer eats the loss too, on top of the acceptor's
    penalty (a harsher "escrow sink" reading closer to the post-accept
    mutual-cancel kill-fee's own precedent). This kernel builds (a), the
    conservative default that reuses an already-proven idiom rather than
    inventing new escrow-sink behavior, and flags it here for a real
    DECISIONS ruling rather than silently picking one. Gated on
    `escrow_state == HELD` -- a row already REFUNDING/RELEASED/DISPUTED
    (an earlier tick, or a race with the issuer's own cancel) is excluded,
    same idempotency guard `sweep_expired_contracts` uses. NPC-issued rows
    (escrow_amount always 0) never match the PLAYER-only gate.

    Every candidate -- NPC or PLAYER-issued -- needs an individualized
    acceptor-penalty Python touch, so unlike `sweep_expired_contracts` this
    sweep has no bulk-shortcut for a "most rows need nothing but a status
    flip" majority; every row goes through the per-row loop. The per-row
    transition is a raw guarded `update()` -- NOT `_guarded_transition` --
    on purpose: `_guarded_transition` conflates two different failure modes
    under one exception (an ILLEGAL edge per LEGAL_TRANSITIONS, which would
    recur identically on every retry of the SAME row, vs. a genuinely-RACED
    row, which won't recur since a fresh SELECT excludes it once its real
    DB status has moved on) -- catching both the same way here would let a
    LEGAL_TRANSITIONS regression (the table missing the ACCEPTED -> EXPIRED
    edge this WO adds) spin this `while True` forever on the same
    permanently-illegal candidate instead of failing loudly. The raw UPDATE
    below can only ever fail for the second reason (no table consultation
    to fail), exactly mirroring sweep_expired_contracts's own per-row idiom:
    a row raced away between the SELECT and the UPDATE (a live
    complete()/abandon() on a different session -- the CEXP advisory lock
    only serializes concurrent *sweep* instances) is simply skipped; the
    next iteration's fresh, server-side-filtered SELECT excludes it (its
    real DB status has already moved past 'accepted') -- no infinite loop.
    A per-row UPDATE that DOES match is applied to the in-memory `candidate`
    object too (same convention `sweep_expired_contracts` and
    `_guarded_transition` both use -- a raw Core `update()` never syncs the
    ORM identity map on its own). FLUSH-ONLY -- the scheduler wrapper
    commits inside the same CEXP advisory lock sweep_expired_contracts
    uses."""
    now = now or _now()

    expired = 0
    while True:
        candidate = (
            db.query(Contract)
            .filter(
                Contract.status == ContractStatus.ACCEPTED,
                Contract.deadline < now,
            )
            .first()
        )
        if candidate is None:
            break

        row_stmt = (
            update(Contract)
            .where(Contract.id == candidate.id, Contract.status == ContractStatus.ACCEPTED)
            .values(status=ContractStatus.EXPIRED)
        )
        result = db.execute(row_stmt)
        if result.rowcount == 0:
            # Raced away between the SELECT above and this UPDATE (a live
            # complete()/abandon() on a different session/connection) -- no
            # mutation occurred, so the next iteration's fresh SELECT simply
            # won't return this row again (its real DB status has already
            # moved past 'accepted').
            continue
        candidate.status = ContractStatus.EXPIRED

        acceptor = _load_player(db, candidate.acceptor_player_id)
        penalty = int(_round_credits(_as_decimal(candidate.penalty)))
        acceptor.credits = max(0, (acceptor.credits or 0) - penalty)

        if candidate.issuer_type == ContractIssuerType.PLAYER and candidate.escrow_state == ContractEscrowState.HELD:
            issuer = _load_player(db, candidate.issuer_id)
            refund = int(_round_credits(_as_decimal(candidate.escrow_amount)))
            issuer.credits = (issuer.credits or 0) + refund
            candidate.escrow_state = ContractEscrowState.REFUNDING

        expired += 1

    db.flush()
    return {"expired": expired}


# --- WO-ECON-CONTRACT-2-PLAYER-ESCROW: player-issued posting + cancel ----

def post_player_contract(
    db: Session,
    issuer_player_id: uuid.UUID,
    destination_station_id: uuid.UUID,
    commodity_type: str,
    quantity: int,
    payment: Decimal,
    deadline: datetime,
    origin_station_id: Optional[uuid.UUID] = None,
    insurance_pool_reserve: Decimal = Decimal("0"),
    posting_stations: Optional[List[uuid.UUID]] = None,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Post a player-issued `cargo_delivery` contract (the only type this
    stage supports -- contracts.md:421-431 step 4). Debits `escrow_amount
    = payment + insurance_pool_reserve` from the issuer at POST time
    (contracts.md:159) -- that debit is the only place this kernel ever
    removes credits from a player's wallet for a contract; `complete`
    later releases it to the acceptor, `cancel_player_contract` refunds
    it (partially) to the issuer. FLUSH-ONLY.

    [NO-CANON] `insurance_pool_reserve` defaults to 0. Verified first:
    neither ADR-0062 E-I2 (insurance PREMIUM cancellation-refund math, a
    DIFFERENT concept -- the acceptor's optional coverage purchased via a
    would-be `/insure` endpoint) nor E-I3 (dispute escalation criteria)
    fixes a value or formula for the ISSUER's posting-time pool reserve.
    This stage builds no `/insure` endpoint or tier-selection UI, so the
    insurance-pool-reserve mechanic stays dormant (reservable via this
    parameter for a future caller, defaulting to 0 -- escrow_amount ==
    payment) until that follow-up WO lands.

    Validation order matches contracts.md:245 (quantity/payment sanity is
    NOT canon-specified -- no bound exists beyond ">0"; not invented
    further):
    """
    now = now or _now()
    if quantity <= 0:
        raise ContractError("quantity must be positive")
    if payment <= 0:
        raise ContractError("payment must be positive")
    if insurance_pool_reserve < 0:
        raise ContractError("insurance_pool_reserve cannot be negative")
    if not _is_valid_commodity(db, commodity_type):
        raise ContractError(f"unknown_commodity: '{commodity_type}' is not in the live resource registry")

    destination = db.query(Station).filter(Station.id == destination_station_id).first()
    if destination is None:
        raise ContractError("Destination station not found")
    # [NO-CANON] "not offline" (contracts.md:245): StationStatus has no
    # literal OFFLINE/DESTROYED/INACCESSIBLE member (canon's dispute-
    # resolution prose at :385 uses that exact language but it was never
    # implemented as real enum values -- a pre-existing doc/code naming
    # gap this WO doesn't silently paper over). ABANDONED is the closest
    # real member (genuinely non-functional); used as the proxy here.
    if destination.status == StationStatus.ABANDONED:
        raise ContractError("Destination station is offline")

    if deadline is None or (deadline - now).total_seconds() < PLAYER_POST_MIN_DEADLINE_HOURS * 3600:
        raise ContractError(
            f"deadline must be at least {PLAYER_POST_MIN_DEADLINE_HOURS} hour(s) out"
        )

    issuer = _load_player(db, issuer_player_id)
    if _is_player_blocklisted(db, issuer_player_id):
        raise ContractError("You are blocklisted from posting contracts")

    region_id = destination.region_id
    if region_id is not None:
        active_in_region = _active_player_postings_in_region(db, issuer_player_id, region_id)
        if active_in_region >= MAX_ACTIVE_PLAYER_POSTINGS_PER_REGION:
            raise ContractError(
                f"posting_cap_reached: {active_in_region} active postings in this region "
                f"(max {MAX_ACTIVE_PLAYER_POSTINGS_PER_REGION})"
            )

    escrow_amount = _round_credits(_as_decimal(payment) + _as_decimal(insurance_pool_reserve))
    if Decimal(issuer.credits or 0) < escrow_amount:
        raise ContractError(
            f"insufficient_credits: posting requires {escrow_amount} credits held in escrow, "
            f"you have {issuer.credits or 0}"
        )

    issuer.credits = int(Decimal(issuer.credits or 0) - escrow_amount)

    contract = Contract(
        id=uuid.uuid4(),
        issuer_type=ContractIssuerType.PLAYER,
        issuer_id=issuer_player_id,
        contract_type=ContractType.CARGO_DELIVERY,
        status=ContractStatus.POSTED,
        origin_station_id=origin_station_id,
        destination_station_id=destination_station_id,
        commodity_type=commodity_type,
        quantity=quantity,
        payment=_round_credits(_as_decimal(payment)),
        penalty=_round_credits(_as_decimal(payment)),  # contracts.md:40 -- default 1.0x payment
        acceptance_fee_pct=Decimal("2.0"),
        escrow_amount=escrow_amount,
        escrow_state=ContractEscrowState.HELD,
        deadline=deadline,
        posted_at=now,
        posting_stations=posting_stations or [destination_station_id],
    )
    db.add(contract)
    db.flush()

    logger.info(
        "Player %s posted contract %s (escrow %s debited)",
        issuer_player_id, contract.id, escrow_amount,
    )
    return {
        "id": str(contract.id),
        "status": contract.status.value,
        "escrow_amount": float(escrow_amount),
        "escrow_state": contract.escrow_state.value,
        "posted_at": now,
        "acceptance_fee_pct": float(contract.acceptance_fee_pct),
        "credits": issuer.credits,
    }


def cancel_player_contract(
    db: Session, contract_id: uuid.UUID, issuer_player_id: uuid.UUID,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Issuer-only cancellation. Two reachable matrix rows (contracts.md's
    escrow table, :157-167 -- NOT the terser state-diagram prose, which
    disagrees on who the kill-fee benefits; the detailed table is
    authoritative here):

    - `posted` -> `cancelled` (:69/:165): issuer refund = escrow x 99%.
      The remaining 1% is a posting-fee sink -- never credited anywhere
      (no treasury model exists).
    - `accepted` -> `cancelled` (:76/:166, "mutual" in the state-diagram
      prose but built here as issuer-unilateral -- this WO adds no
      acceptor-consent flow, [NO-CANON] simplification flagged): issuer
      refund = escrow - accept_fee_equivalent - (payment x 10%). The
      acceptor receives NOTHING (the escrow table's Acceptor column is a
      flat "0" for this row -- both deducted components sink; contradicts
      a plausible reading of the state-diagram's own terser "kill-fee"
      prose, which the detailed table wins per this codebase's docs-win
      convention).

    Disputed/other statuses are not cancellable this stage -- dispute
    adjudication is explicitly out of scope; the `disputed` status is
    never reached by any code this WO ships.

    PAST-DEADLINE ACCEPTED GUARD (WO-DRIFT-econ-accepted-deadline-expiry,
    Mack HIGH #1): before that WO, `accepted` could never reach `expired`
    (LEGAL_TRANSITIONS[ACCEPTED] was only {COMPLETED, CANCELLED}), so an
    issuer cancelling a long-overdue accepted contract never competed with
    anything. That WO's new ACCEPTED -> EXPIRED sweep edge makes the race
    live: with no deadline check here, an issuer's ordinary unilateral
    cancel around the same moment the periodic sweep ticks silently waives
    the acceptor's WO-guaranteed deadline-failure penalty (this branch
    never touches acceptor.credits at all) AND nets the issuer a WORSE
    refund than sweep_expired_accepted_contracts's own full-escrow refund
    -- no attacker or malice required, just an issuer clicking cancel at
    the wrong moment. Once the deadline has passed, an accepted contract
    routes exclusively through the sweep (acceptor penalized, escrow
    settled per that sweep's own NO-CANON-flagged disposition) -- the
    unilateral-cancel path is withdrawn. FLUSH-ONLY."""
    now = now or _now()
    contract = _load_contract(db, contract_id)
    if contract.issuer_type != ContractIssuerType.PLAYER or contract.issuer_id != issuer_player_id:
        raise ContractError("This contract was not posted by you")

    issuer = _load_player(db, issuer_player_id)

    if contract.status == ContractStatus.POSTED:
        _guarded_transition(db, contract, ContractStatus.POSTED, ContractStatus.CANCELLED)
        refund = _round_credits(
            _as_decimal(contract.escrow_amount) * PLAYER_POST_CANCEL_REFUND_PCT_PRE_ACCEPT / Decimal(100)
        )
    elif contract.status == ContractStatus.ACCEPTED:
        if contract.deadline is not None and now >= contract.deadline:
            raise ContractConflictError(
                f"past_deadline: contract {contract.id}'s deadline has already passed -- "
                "it will be expired (acceptor penalized, escrow settled) on the next "
                "scheduler sweep, not cancelled"
            )
        _guarded_transition(db, contract, ContractStatus.ACCEPTED, ContractStatus.CANCELLED)
        accept_fee_equivalent = _as_decimal(contract.payment) * _as_decimal(contract.acceptance_fee_pct) / Decimal(100)
        cancel_fee = _as_decimal(contract.payment) * PLAYER_POST_CANCEL_FEE_PCT_POST_ACCEPT / Decimal(100)
        refund = _round_credits(
            max(Decimal(0), _as_decimal(contract.escrow_amount) - accept_fee_equivalent - cancel_fee)
        )
    else:
        raise ContractConflictError(
            f"stale_status: contract {contract.id} is '{contract.status.value}', not cancellable"
        )

    issuer.credits = (issuer.credits or 0) + int(refund)
    contract.escrow_state = ContractEscrowState.REFUNDING
    db.flush()

    logger.info(
        "Player %s cancelled contract %s (refund %s)", issuer_player_id, contract.id, refund,
    )
    return {
        "id": str(contract.id),
        "status": contract.status.value,
        "refund": float(refund),
        "credits": issuer.credits,
    }

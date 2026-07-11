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
from typing import Any, Callable, Dict, FrozenSet, List, Optional

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


def _to_credits_int(amount: Decimal) -> int:
    """WO-ECON-CONTRACT-MONEY-HARDEN (Mack LOW #3): Player.credits is a
    whole-credit integer column, but fee/penalty/refund amounts are
    PERCENTAGES of a payment (2% acceptance fee, 10% cancel fee, etc.) --
    routinely non-integer even after _round_credits' own cents-precision
    rounding (2% of 101 credits = 2.02). Plain `int(some_decimal)`
    TRUNCATES toward zero, silently discarding the fractional remainder
    on every single conversion -- up to a whole credit evaporates each
    time the fractional part is >= 0.50 (e.g. int(Decimal("2.50")) == 2,
    not the correct round-half-up 3). This is the ONE place that
    Decimal-to-int conversion happens; every call site in this module
    that used to write `int(<a Decimal expression>)` now calls this
    instead."""
    return int(amount.to_integral_value(rounding=ROUND_HALF_UP))


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


def _load_player(db: Session, player_id: uuid.UUID, *, for_update: bool = False) -> Player:
    """WO-ECON-CONTRACT-MONEY-HARDEN: every credit-mutating call site now
    passes for_update=True -- matches the codebase's established
    with_for_update() convention (bounty_service.py / citadel_service.py /
    docking_service.py / trading_service.py) this module was previously
    the sole money-touching holdout on. Locking here (the point where the
    row is first read) rather than right before the eventual `.credits =`
    assignment protects the WHOLE read-check-mutate sequence each caller
    runs in between -- e.g. accept()'s `Decimal(acceptor.credits) < fee`
    balance check -- not just the final write."""
    query = db.query(Player).filter(Player.id == player_id)
    if for_update:
        query = query.populate_existing().with_for_update()
    player = query.first()
    if player is None:
        raise ContractError(f"Player {player_id} not found")
    return player


def _load_two_players_for_update(
    db: Session, id_a: uuid.UUID, id_b: uuid.UUID,
) -> tuple[Player, Player]:
    """Lock two distinct Player rows for a single operation that touches
    both (abandon()'s acceptor+issuer refund, sweep_expired_accepted_
    contracts' acceptor-penalty+issuer-refund) in a CONSISTENT order --
    ascending by id -- regardless of which one is the semantic "first"
    party. Without this, two concurrent operations that both need to lock
    the SAME pair of players (e.g. player X abandoning a contract issued
    by player Y, racing player Y abandoning a DIFFERENT contract issued by
    player X) could acquire the pair in opposite order and deadlock.
    Ordering by id makes that structurally impossible: every dual-lock
    call site in this module funnels through this one function, so any
    two concurrent callers touching the same pair always agree on which
    row to lock first. Returns (player_a, player_b) in the SAME order as
    (id_a, id_b) were passed -- only the DB-side lock ACQUISITION order is
    normalized internally; the caller's semantic pairing is unaffected."""
    if id_a == id_b:
        # Defensive only -- accept() already rejects self-accept for
        # player-issued contracts, so acceptor_player_id != issuer_id is
        # guaranteed by the time either dual-lock call site here runs.
        # Never attempt to lock the same row twice regardless.
        player = _load_player(db, id_a, for_update=True)
        return player, player
    if id_a < id_b:
        player_a = _load_player(db, id_a, for_update=True)
        player_b = _load_player(db, id_b, for_update=True)
    else:
        player_b = _load_player(db, id_b, for_update=True)
        player_a = _load_player(db, id_a, for_update=True)
    return player_a, player_b


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

    acceptor = _load_player(db, acceptor_player_id, for_update=True)
    fee = _round_credits(_as_decimal(contract.payment) * _as_decimal(contract.acceptance_fee_pct) / Decimal(100))
    if Decimal(acceptor.credits or 0) < fee:
        raise ContractError(
            f"insufficient_credits: acceptance fee is {fee}, you have {acceptor.credits or 0}"
        )

    _guarded_transition(
        db, contract, ContractStatus.POSTED, ContractStatus.ACCEPTED,
        acceptor_player_id=acceptor_player_id, accepted_at=now,
    )

    acceptor.credits = _to_credits_int(Decimal(acceptor.credits or 0) - fee)
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

    player = _load_player(db, player_id, for_update=True)
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

    payout = _to_credits_int(_round_credits(_as_decimal(contract.payment)))
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

    # WO-ECON-CONTRACT-MONEY-HARDEN: lock BOTH players (when this is a
    # player-issued contract) up front, via the consistent-ordering
    # helper -- determined here, before _guarded_transition, since the
    # dual-lock decision needs contract.issuer_type/issuer_id, both
    # already available on the just-loaded `contract`. Locking player-
    # first-then-issuer unconditionally (the original code's shape) would
    # deadlock against another abandon() call where the ROLES are
    # reversed (player X abandoning a contract issued by Y, racing player
    # Y abandoning a different contract issued by X) -- see
    # _load_two_players_for_update's own docstring.
    if contract.issuer_type == ContractIssuerType.PLAYER:
        player, issuer = _load_two_players_for_update(db, player_id, contract.issuer_id)
    else:
        player = _load_player(db, player_id, for_update=True)
        issuer = None

    _guarded_transition(db, contract, ContractStatus.ACCEPTED, ContractStatus.CANCELLED)

    penalty = _to_credits_int(_round_credits(_as_decimal(contract.penalty)))
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
    if issuer is not None:
        refund = _to_credits_int(_round_credits(_as_decimal(contract.escrow_amount)))
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
            # WO-ECON-CONTRACT-MONEY-HARDEN Mack MEDIUM #2: the row's own
            # status flip above is already applied to the shared
            # transaction -- only the credit-side refund below (a single-
            # player lock + mutation, no cross-row deadlock concern) is
            # savepoint-isolated. A failure here (e.g. the issuer row
            # vanished -- not reachable today, no hard-delete path exists,
            # but cheap to harden against) previously rolled back the
            # WHOLE shared transaction, discarding every other row this
            # sweep already processed and re-selecting the same poisoned
            # row on every future tick forever. Now it rolls back just
            # this row's refund and the sweep moves on to the next
            # candidate.
            try:
                with db.begin_nested():
                    issuer = _load_player(db, candidate.issuer_id, for_update=True)
                    refund = _to_credits_int(_round_credits(_as_decimal(candidate.escrow_amount)))
                    issuer.credits = (issuer.credits or 0) + refund
                    candidate.escrow_state = ContractEscrowState.REFUNDING
            except Exception:
                logger.exception(
                    "sweep_expired_contracts: issuer refund failed for contract "
                    "%s (status already EXPIRED in this same sweep pass; "
                    "refund/escrow_state reverted, sweep continues)", candidate.id,
                )
        expired_with_refund += 1

    bulk_stmt = (
        update(Contract)
        .where(Contract.status == ContractStatus.POSTED, Contract.deadline < now)
        .values(status=ContractStatus.EXPIRED)
    )
    bulk_result = db.execute(bulk_stmt)
    db.flush()
    return {"expired": expired_with_refund + (bulk_result.rowcount or 0)}


def sweep_expired_accepted_contracts(
    db: Session, now: Optional[datetime] = None,
    expiry_gate: Optional[Callable[[Session, Contract], bool]] = None,
) -> Dict[str, int]:
    """WO-DRIFT-econ-accepted-deadline-expiry: bulk-expire every `accepted`
    contract whose deadline has strictly passed without completion.
    Canon (contracts.md's state-transition matrix) puts this edge on
    `in_progress -> expired`; this codebase never sets IN_PROGRESS (`accept`
    -> `ACCEPTED` is the terminal pre-completion state here), so this sweep
    is the code-equivalent of canon's in_progress deadline-expiry, not a new
    concept -- see the LEGAL_TRANSITIONS comment above.

    `expiry_gate` (WO-STORE-EXPIRY-CLAIMABLE + D19, deposit-wins is a
    REQUIRED semantic): an optional per-candidate veto, called as
    `expiry_gate(db, candidate)` BEFORE the guarded UPDATE below.
    `False` DEFERS that one candidate's expiry entirely this tick (no
    UPDATE attempted, no penalty charged, contract stays ACCEPTED,
    picked up again on a later tick if still overdue then). `True`
    proceeds exactly as the un-gated path always has. `None` (the
    default) means every candidate proceeds -- EVERY OTHER caller of
    this function (direct test calls, any future caller) is completely
    unaffected. This module stays ignorant of WHAT the gate checks or
    WHY -- keeping the circular import boundary intact (storage_service
    already imports this module at load time; this module must never
    import storage_service back) -- see storage_service.gate_contract_
    expiry_on_locker for the actual storage-linked-locker probe the
    scheduler wires in, and its own docstring for why deferring here is
    what makes a completing deposit_cargo call deterministically WIN a
    deadline race instead of losing an ordinary first-committer coin
    flip.

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
    row, which won't recur since its real DB status has moved on) --
    catching both the same way here would let a LEGAL_TRANSITIONS
    regression (the table missing the ACCEPTED -> EXPIRED edge this WO
    adds) spin forever on the same permanently-illegal candidate instead
    of failing loudly. The raw UPDATE below can only ever fail for the
    second reason (no table consultation to fail).

    CANDIDATES GATHERED UPFRONT (`.all()`), NOT a `while True` + repeated
    `.first()` (WO-STORE-EXPIRY-CLAIMABLE): the original shape re-ran the
    SAME server-side-filtered SELECT every iteration specifically so a
    row that raced away between selects (a live complete()/abandon() on
    a different session) was excluded from ever being re-attempted. But
    a GATE-DEFERRED candidate (see `expiry_gate` above) doesn't change
    its own status at all -- leaving it ACCEPTED -- so a repeated fresh
    SELECT would keep re-matching and re-deferring the SAME row FOREVER,
    an infinite loop this WO's own gate would introduce into the old
    shape. Gathering the full candidate list ONCE up front and iterating
    it in Python sidesteps this entirely (a deferred candidate is simply
    never revisited within this pass) WITHOUT weakening the raced-away
    protection: that protection never actually depended on re-querying --
    the guarded UPDATE's own `rowcount == 0 -> continue` below ALREADY
    independently excludes a row whose real DB status moved on between
    the upfront SELECT and this row's own turn in the loop, identical in
    effect to the old shape's query-level exclusion, just discovered one
    step later. A per-row UPDATE that DOES match is applied to the
    in-memory `candidate` object too (same convention `sweep_expired_
    contracts` and `_guarded_transition` both use -- a raw Core
    `update()` never syncs the ORM identity map on its own).

    A gate that ACQUIRES a Locker's lock (returns True) but whose
    contract then turns out to be raced-away (rowcount == 0 -- completed
    or abandoned by a different session between the upfront SELECT and
    this candidate's turn) leaves this transaction holding that Locker's
    lock, unused, until the whole sweep commits -- not a bug (nothing
    else in this transaction reaches for it again in a way that could
    cycle), just briefly held longer than strictly needed; noted here so
    a future reader doesn't mistake it for a leak.

    `.all()` gathers the FULL candidate set into memory upfront -- fine
    at this sweep's actual scale (the per-tick overdue-accepted-contract
    backlog is small; the sweep runs every CONTRACT_EXPIRE_SWEEP_SECONDS
    and never lets a large backlog accumulate in steady state), but this
    is not a bulk-safe pattern for an UNBOUNDED backlog -- if that
    assumption ever stops holding, chunk the query rather than assuming
    `.all()` still scales.

    FLUSH-ONLY -- the scheduler wrapper commits inside the same CEXP
    advisory lock sweep_expired_contracts uses."""
    now = now or _now()

    candidates = (
        db.query(Contract)
        .filter(
            Contract.status == ContractStatus.ACCEPTED,
            Contract.deadline < now,
        )
        .all()
    )

    expired = 0
    for candidate in candidates:
        if expiry_gate is not None and not expiry_gate(db, candidate):
            # Deferred -- a storage-linked contract whose Locker is
            # currently held by a live completing deposit_cargo call
            # (see expiry_gate's own docstring above). Left ACCEPTED,
            # untouched, no penalty; picked up again on a later tick if
            # still overdue then.
            continue

        row_stmt = (
            update(Contract)
            .where(Contract.id == candidate.id, Contract.status == ContractStatus.ACCEPTED)
            .values(status=ContractStatus.EXPIRED)
        )
        result = db.execute(row_stmt)
        if result.rowcount == 0:
            # Raced away between the upfront SELECT and this UPDATE (a
            # live complete()/abandon() on a different session) -- no
            # mutation occurred; simply skipped, see this function's own
            # docstring for why the upfront `.all()` doesn't weaken this
            # protection.
            continue
        candidate.status = ContractStatus.EXPIRED

        # WO-ECON-CONTRACT-MONEY-HARDEN: same savepoint-isolation shape as
        # sweep_expired_contracts (Mack MEDIUM #2) -- the status flip above
        # already landed on the shared transaction; only the credit-side
        # penalty+refund below is wrapped, so a failure here reverts just
        # this row's credit effects and the sweep continues past it. The
        # acceptor/issuer dual-lock (Mack HIGH #1) uses the SAME consistent-
        # ordering helper abandon() does, for the identical deadlock reason
        # -- this sweep and a live abandon()/cancel_player_contract() call
        # on a related contract could otherwise lock the same pair of
        # players in opposite order.
        try:
            with db.begin_nested():
                needs_issuer_refund = (
                    candidate.issuer_type == ContractIssuerType.PLAYER
                    and candidate.escrow_state == ContractEscrowState.HELD
                )
                if needs_issuer_refund:
                    acceptor, issuer = _load_two_players_for_update(
                        db, candidate.acceptor_player_id, candidate.issuer_id
                    )
                else:
                    acceptor = _load_player(db, candidate.acceptor_player_id, for_update=True)
                    issuer = None

                penalty = _to_credits_int(_round_credits(_as_decimal(candidate.penalty)))
                acceptor.credits = max(0, (acceptor.credits or 0) - penalty)

                if issuer is not None:
                    refund = _to_credits_int(_round_credits(_as_decimal(candidate.escrow_amount)))
                    issuer.credits = (issuer.credits or 0) + refund
                    candidate.escrow_state = ContractEscrowState.REFUNDING
        except Exception:
            logger.exception(
                "sweep_expired_accepted_contracts: acceptor penalty/issuer "
                "refund failed for contract %s (status already EXPIRED in "
                "this same sweep pass; credit-side effects reverted, sweep "
                "continues)", candidate.id,
            )

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

    issuer = _load_player(db, issuer_player_id, for_update=True)
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

    issuer.credits = _to_credits_int(Decimal(issuer.credits or 0) - escrow_amount)

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

    issuer = _load_player(db, issuer_player_id, for_update=True)

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

    issuer.credits = (issuer.credits or 0) + _to_credits_int(refund)
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

"""Trade Contract shared escrow/lock PRIMITIVES -- WO-CONTRACT-REFACTOR-
SPLIT. Split out of the former monolithic `contract_service.py` (which had
grown past this project's 1500-line Python guideline across three
successive WOs) as a PURE MOVE, zero behavior change. This module holds
everything every transition in the contract domain shares: the exception
hierarchy, the state machine, the player-posting/insurance-cancellation
constants, the Decimal/credits rounding helpers, and the row-lock/guarded-
transition machinery. `contract_service.py` (lifecycle), `contract_
insurance.py`, and `contract_dispute.py` all import from here; this module
imports from none of them (leaf of the dependency graph) -- see each
sibling's own module docstring for the layering. `contract_service.py`
re-exports the names external callers (routes, scheduler, storage_service,
tests) already reach via `contract_service.<name>` -- see its own
docstring for the full re-export list.

SYNC Session throughout -- matches slipdrive_service.py / escape_pod_
service.py / fuel_delivery_service.py and api/routes/trading.py's own
`db: Session = Depends(get_db)` convention despite its route defs being
`async def`. FLUSH-ONLY -- the route (or the scheduler wrapper, for the
sweep) owns the commit.

CONCURRENCY -- accept/complete/abandon (and every other status-changing
call in the three sibling modules) each go through `_guarded_transition`:
a single atomic `UPDATE contracts SET status=:to WHERE id=:id AND
status=:from` (Postgres re-checks the WHERE clause against the row's live
state at write time and serializes concurrent writers to the same row --
the second writer's WHERE simply matches 0 rows once the first commits its
status change). No `SELECT ... FOR UPDATE` is needed; the guarded UPDATE
*is* the lock.

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

from src.models.contract import Contract, ContractEscrowState, ContractStatus
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
    # WO-ECON-CONTRACT-2-PLAYER-ESCROW adds POSTED -> CANCELLED (issuer
    # withdraws pre-accept, contracts.md:69) -- purely additive; no
    # existing NPC-facing function transitions POSTED -> CANCELLED, so
    # this new edge is inert for the NPC path.
    ContractStatus.POSTED: frozenset({ContractStatus.ACCEPTED, ContractStatus.EXPIRED, ContractStatus.CANCELLED}),
    # WO-DRIFT-econ-accepted-deadline-expiry adds ACCEPTED -> EXPIRED. Canon
    # (contracts.md's state-transition matrix) puts this edge on
    # `in_progress`, not `accepted` -- this codebase collapses `in_progress`
    # into `accepted` for every OTHER type (no code path but bulk_
    # procurement's own deliver()/walk_away_bulk_procurement() ever sets
    # IN_PROGRESS -- WO-CONTRACT-3b-BULK), so this is the code-equivalent
    # of canon's in_progress -> expired edge for cargo_delivery/express_
    # delivery/hazardous_transport. See sweep_expired_accepted_contracts's
    # own docstring for the transition.
    #
    # WO-CONTRACT-3b-BULK adds two more edges, both bulk_procurement-only:
    # -> IN_PROGRESS (deliver()'s own first-partial edge, contracts.md:91's
    # "partial_fulfilled bridges accepted -> in_progress") and -> POSTED
    # (walk_away_bulk_procurement()'s own edge, contracts.md:78's "acceptor
    # walks (bulk_procurement) -> posted"). A single deliver() call
    # covering the FULL remaining quantity in one shot can also go straight
    # ACCEPTED -> COMPLETED without ever passing through IN_PROGRESS --
    # deliver()'s own docstring covers why that's legal.
    ContractStatus.ACCEPTED: frozenset({
        ContractStatus.COMPLETED, ContractStatus.CANCELLED, ContractStatus.EXPIRED,
        ContractStatus.IN_PROGRESS, ContractStatus.POSTED,
    }),
    # WO-CONTRACT-3b-BULK: bulk_procurement's own IN_PROGRESS states, the
    # first real consumer of this status value anywhere in the codebase
    # (every other type "collapses in_progress into accepted", see the
    # ACCEPTED entry's own comment). -> IN_PROGRESS is a SELF-loop
    # (deliver()'s "stays in_progress" case, another partial delivered but
    # some quantity still remains) -- the guarded UPDATE's own WHERE clause
    # (`status == from_status`) is still the correct optimistic-concurrency
    # guard even when from_status == to_status: a row raced away between
    # load and write (status changed by a concurrent call) still matches 0
    # rows and correctly raises ContractConflictError. -> COMPLETED is the
    # final partial. -> POSTED is walk_away_bulk_procurement()'s own edge
    # when the acceptor walks away AFTER at least one partial delivery
    # (contracts.md:78/:130).
    ContractStatus.IN_PROGRESS: frozenset({
        ContractStatus.IN_PROGRESS, ContractStatus.COMPLETED, ContractStatus.POSTED,
    }),
    # WO-CONTRACT-2-DISPUTE-T1: an EXPIRED contract's acceptor can dispute
    # the failure (contracts.md:390) -- filing flips EXPIRED -> DISPUTED.
    # From DISPUTED, Tier-1/Tier-2 resolution lands on COMPLETED (the
    # dispute proved delivery happened) or CANCELLED (every other Tier-1/
    # Tier-2 outcome this build resolves -- see file_dispute's and
    # resolve_dispute's own docstrings for which outcome maps where).
    ContractStatus.EXPIRED: frozenset({ContractStatus.DISPUTED}),
    ContractStatus.DISPUTED: frozenset({ContractStatus.COMPLETED, ContractStatus.CANCELLED}),
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
# WO-CONTRACT-REFACTOR-SPLIT: also consulted by contract_dispute.py's
# `_tier1_issuer_unilateral_cancellation` settlement math (same kill-fee
# shape applied to a dispute-driven issuer-cancellation finding) -- shared
# here rather than duplicated, hence living in the core module rather than
# alongside cancel_player_contract() in contract_service.py.
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


def _settle_dispute_escrow(contract: Any, issuer: Optional[Any], acceptor: Any, nominal: int) -> int:
    """WO-CONTRACT-2b-HOLD-ESCROW (Max ruling C, R1) -- REPLACES `_bounded_
    transfer` (deleted; see this function's own git history for the prior
    implementation and the mack-CRITICAL bounded-never-mint fix it
    originally shipped, which this function preserves and generalizes).

    `_bounded_transfer` drew an issuer-funded dispute payout from the
    issuer's CURRENT WALLET -- necessary at the time because `sweep_
    expired_accepted_contracts` refunded the issuer's escrow immediately
    at expiry, so by the time ANY dispute could be filed there was no
    "escrow" left, only whatever the issuer's balance happened to still
    hold (an ordinary non-adversarial sequence -- issuer spends their
    refunded escrow, THEN a dispute resolves against them -- could hollow
    the payout to ~0, or a wallet-bounded clamp could mint if the debit/
    credit legs weren't kept in lockstep; `_bounded_transfer`'s own
    `min(issuer.credits, amount)` closed the mint half of that, but not
    the hollow-payout half). WO-CONTRACT-2b-HOLD-ESCROW closes the other
    half: escrow now stays HELD through the entire 48h dispute window
    (contract_service.py's sweep no longer refunds it at expiry), so this
    function draws from the REAL, held `contract.escrow_amount` instead.

    PLAYER-issued (`issuer is not None`): `contract.escrow_amount` is
    WHOLE-CREDIT at every persisted state (R3 -- see `_compute_claim_
    offset`'s own docstring, contract_insurance.py: `payment` and
    `insurance_pool_reserve` are both `multiple_of=1` at the API schema,
    and every subsequent adjustment to `escrow_amount` anywhere in this
    codebase is whole-minus-whole) -- `min(nominal, escrow_amount)` is
    therefore an EXACT whole-credit bound, never a rounding boundary.
    Whatever's left (`escrow_amount - actual`) returns to the issuer in
    this SAME call -- no terminal dispute outcome may ever leave a
    residual `escrow_amount` stranded on the row. `escrow_state` becomes
    `RELEASED` iff the acceptor received anything, else `REFUNDING` --
    matching `complete()`'s/`abandon()`'s own established vocabulary
    (RELEASED = "acceptor got paid", REFUNDING = "issuer got it back").
    Callers MUST invoke this UNCONDITIONALLY for every terminal dispute
    outcome, even when `nominal == 0` (PARTIAL_PAYOUT/REFUND/PENALTY) --
    the escrow's remainder still needs to return to the issuer; a
    `nominal == 0` outcome is NOT a no-op here the way it was for
    `_bounded_transfer` (which callers only invoked when `nominal > 0`,
    since there was nothing else for it to do).

    NPC-issued (`issuer is None`): unbounded mint of `nominal`, BYTE-
    IDENTICAL to `_bounded_transfer`'s own NPC branch (NPC credits are
    canonically infinite, contracts.md:155, matching `complete()`'s own
    established mint-for-NPC precedent) -- `contract.escrow_amount` is
    always 0 for an NPC row and is never touched.

    Returns the ACTUAL amount credited to the acceptor (<= `nominal` for
    a PLAYER-issued row; == `nominal` for NPC) -- callers MUST treat this,
    never the requested `nominal`, as the true payout, matching `_bounded_
    transfer`'s own established return-value contract."""
    if issuer is None:
        if nominal > 0:
            acceptor.credits = (acceptor.credits or 0) + nominal
        return nominal

    escrow_amount = _to_credits_int(_round_credits(_as_decimal(contract.escrow_amount or 0)))
    actual = min(nominal, escrow_amount) if nominal > 0 else 0
    remainder = escrow_amount - actual

    if actual > 0:
        acceptor.credits = (acceptor.credits or 0) + actual
    if remainder > 0:
        issuer.credits = (issuer.credits or 0) + remainder
    contract.escrow_amount = Decimal("0")
    contract.escrow_state = ContractEscrowState.RELEASED if actual > 0 else ContractEscrowState.REFUNDING
    return actual

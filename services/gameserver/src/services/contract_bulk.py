"""Trade Contract BULK_PROCUREMENT partial fulfillment -- WO-CONTRACT-3b-
BULK, split out of `contract_service.py` (which had grown past this
project's 1500-line Python guideline the moment this build landed there --
same threshold, same remedy, WO-CONTRACT-REFACTOR-SPLIT already documents
this exact pattern for `contract_insurance.py`/`contract_dispute.py`).
Pure move for the two new functions themselves; zero behavior change from
how they were first written.

RETIRED (WO-CONTRACT-4-BULK, Max-ruled 2026-07-17): this module's own
pro-rata direct-delivery model is SUPERSEDED, not deleted. Max chose the
STATION-LOCKER fulfillment path instead (deposit_cargo -> complete(),
storage_service.py -- already built for cargo_delivery, extended to
bulk_procurement by WO-4) for every bulk_procurement contract going
forward. `deliver()` and `walk_away_bulk_procurement()` below stay fully
dormant -- neither is wired to any route, and `post_player_contract`'s
new bulk_procurement support (WO-4) never calls either -- kept in place
for now (a later cleanup WO may remove them outright) rather than deleted
mid-WO-4, to keep this build's blast radius to the locker path only. See
each function's own docstring for its own one-line RETIRED pointer.

`deliver()` / `walk_away_bulk_procurement()` are the two new lifecycle
functions bulk_procurement needs beyond `accept()` (UNCHANGED -- see this
module's own NO-CANON-correction note below) and `complete()` (never
reached directly by bulk; `deliver()` itself transitions straight to
COMPLETED on the final partial). FUNCTION ONLY -- nothing yet GENERATES or
POSTS a bulk_procurement row anywhere in this codebase
(contract_generator.py's own WO-CONTRACT-3-NPCGEN-TYPES build produced
express_delivery/hazardous_transport only; `post_player_contract`,
contract_service.py, still hardcodes cargo_delivery) -- these two
functions are built and DB-free-tested against hand-constructed fixtures,
matching this codebase's own established `resolve_dispute`-style "function
only" precedent (contract_dispute.py) until a future WO wires a real
posting/generation path to them.

[VERIFY-FIRST FINDING, premise correction] the WO-3b dispatch's own brief
proposed a NEW `acceptance_fee_charged_at` column so the 2% acceptance fee
is charged ONLY on the very first accept, never on a re-accept after a
walk-away ("2%-fee-once"). Re-reading contracts.md's own worked example
(:184) before building that column found it CONTRADICTS canon: "Player C
accepts, debited a FRESH 10 cr fee (no fee-stacking)... Acceptance fees
forfeit by walked acceptors are sunk." Every accept() call -- first-ever or
a re-accept on a walked-away bulk contract -- charges its own fresh fee;
"no fee-stacking" describes the fee never COMPOUNDING per call, not that
it's charged once for the contract's whole lifetime. `accept()` (contract_
service.py) is therefore UNCHANGED by this WO: its existing unconditional
per-call fee charge is already exactly what bulk_procurement needs, and NO
new column is added -- flagged explicitly in this WO's report rather than
silently building the (wrong) fee-once mechanic the dispatch described.

Depends on `contract_escrow_core.py` (the shared primitives/state-machine,
leaf of the dependency graph) and `contract_insurance.py`'s mid-term-
cancellation refund helper (the SAME dependency shape `contract_dispute.py`
already has -- "dispute also depends on insurance's refund helper", per
that module's own docstring). Imports from neither `contract_service.py`
nor `contract_dispute.py` (no cycle). `contract_service.py` re-exports
`deliver`/`walk_away_bulk_procurement` for external callers already
reaching `contract_service.<name>` -- see its own docstring for the full
re-export list.

SYNC Session / FLUSH-ONLY / guarded-UPDATE-is-the-lock conventions -- see
`contract_escrow_core.py`'s own module docstring, unchanged here."""
import logging
import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any, Dict, Optional

from sqlalchemy import update
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from src.models.contract import Contract, ContractEscrowState, ContractIssuerType, ContractStatus, ContractType
from src.services.contract_escrow_core import (
    LEGAL_TRANSITIONS,
    ContractConflictError,
    ContractError,
    _as_decimal,
    _guarded_transition,
    _load_contract,
    _load_player,
    _now,
    _round_credits,
    _to_credits_int,
)
from src.services.contract_insurance import _compute_insurance_cancellation_refund, _refresh_contract_insurance_snapshot

logger = logging.getLogger(__name__)


def _validate_bulk_delivery_request(contract: Any, player_id: uuid.UUID, quantity_delivered: int) -> None:
    """The ownership/type/status/quantity guard clauses for deliver() --
    extracted so that function's own McCabe complexity stays under this
    codebase's threshold (a cheap, justified extraction for newly-written
    code -- see gameserver-ruff-c901-not-enforced in monk's own memory
    notes). Raises exactly as deliver() would inline; the docking/cargo
    checks stay in deliver() itself since they flow directly into the
    mutation that follows them."""
    if getattr(contract, "contract_type", None) != ContractType.BULK_PROCUREMENT:
        raise ContractError("not_bulk_procurement: deliver() only applies to bulk_procurement contracts")
    if contract.acceptor_player_id != player_id:
        raise ContractError("This contract is not accepted by you")
    if contract.status not in (ContractStatus.ACCEPTED, ContractStatus.IN_PROGRESS):
        raise ContractConflictError(
            f"stale_status: contract {contract.id} is '{contract.status.value}', "
            "not 'accepted' or 'in_progress'"
        )
    if quantity_delivered is None or quantity_delivered <= 0:
        raise ContractError("invalid_quantity: quantity_delivered must be positive")


def _compute_bulk_delivery_payout(
    contract: Any, quantity_delivered: int, total_quantity: int, completes_now: bool,
) -> int:
    """Pure whole-credit-early (R3) payout computation for ONE deliver()
    call -- extracted out of deliver() so that function's own McCabe
    complexity stays manageable (see gameserver-ruff-c901-not-enforced in
    monk's own memory notes for this codebase's own tolerance/precedent
    for this exact kind of cheap extraction). See deliver()'s own
    docstring for the full money-invariant reasoning.

    `completes_now=True` pays the EXACT remainder (`payment_int -
    already_paid_int`), never the naive per-delivery pro-rata share --
    this is what makes the running total reconcile to EXACTLY `payment`
    on completion even when earlier partials' pro-rata shares didn't sum
    evenly. Worked example: `payment=1000, quantity=3`, delivered one unit
    at a time -- each unit's naive 1/3 share rounds to 333 (HALF_UP of
    333.33), and three of those would sum to only 999, silently shorting
    the acceptor 1 credit even though the FULL quantity was delivered.
    The completing delivery instead receives whatever's genuinely left
    (`1000 - 666 = 334` for the third unit here), landing exactly on
    1000 -- same "no terminal outcome may ever leave a residual stranded"
    reconciliation idiom `_settle_dispute_escrow` (contract_escrow_core.py)
    already establishes for a different terminal event.

    `completes_now=False` stays the conservative per-delivery clamp
    (`min(payment_int - already_paid_int, pro_rata_int)`, floored at 0) --
    an INTERMEDIATE partial must never pay ahead of its own pro-rata
    schedule, only the delivery that actually finishes the contract
    reconciles the shortfall."""
    payment_int = _to_credits_int(_round_credits(_as_decimal(contract.payment)))
    already_paid_int = _to_credits_int(_round_credits(_as_decimal(contract.partial_fulfilled_payout or 0)))
    if completes_now:
        return max(0, payment_int - already_paid_int)
    pro_rata_int = _to_credits_int(
        _round_credits(_as_decimal(contract.payment) * Decimal(quantity_delivered) / Decimal(total_quantity))
    )
    return max(0, min(payment_int - already_paid_int, pro_rata_int))


def _guarded_deliver(
    db: Session, contract: Any, from_status: ContractStatus, to_status: ContractStatus,
    expected_partial_fulfilled_amount: Optional[int], **column_updates: Any,
) -> Any:
    """deliver()'s own compare-and-swap twin of `_guarded_transition` --
    mack CRITICAL (WO-3b money-path gate REVISE, live-reproduced with real
    2-session SQLAlchemy+SQLite): `deliver()`'s intermediate-partial edge
    is a SELF-LOOP (IN_PROGRESS -> IN_PROGRESS, "another partial, still
    not complete") -- it does NOT change `status`, so `_guarded_
    transition`'s status-only `WHERE status = :from_status` silently
    stops being a lock for that one edge: two concurrent deliver() calls
    both reading the SAME stale `partial_fulfilled_amount` would BOTH pass
    that WHERE clause (status never moved), and the SECOND writer's UPDATE
    overwrites the FIRST's contribution -- a genuine lost update (mack's
    repro: contract at 4/10, caller A commits to 7/10, caller B's stale
    4/10 snapshot then overwrites to 6/10, silently losing A's +3 units /
    +300 credits, plus an under-counted `partial_fulfilled_payout` that
    breaches the whole-credit conservation invariant on the next call).

    FIX: fold `Contract.partial_fulfilled_amount == expected_partial_
    fulfilled_amount` (the exact value `deliver()` read at call start,
    BEFORE any of its own computation) into the SAME WHERE clause as the
    status check -- applied UNIFORMLY to every deliver() transition, not
    just the self-loop (the ACCEPTED-origin edges get it for free, at
    zero cost, since `expected_partial_fulfilled_amount` is trivially
    correct there too -- one code path, no self-loop-only special case).
    This makes the whole call a real compare-and-swap: whichever writer's
    UPDATE commits first wins; the loser's WHERE clause no longer matches
    (the row's `partial_fulfilled_amount` has already moved), `rowcount ==
    0`, and it raises WITHOUT any mutation -- the same "the guarded UPDATE
    IS the lock" principle every other transition in this codebase
    already relies on, just with the extra column folded into the SAME
    atomic WHERE clause instead of a separate `SELECT ... FOR UPDATE`. A
    `populate_existing()`-style refresh-then-recompute does NOT close this
    -- refresh, recompute, then write is still a TOCTOU pair with an
    unbounded window between the refresh and the write; only folding the
    compare into the WHERE clause makes the check-and-write one atomic
    statement.

    `expected_partial_fulfilled_amount` is passed EXACTLY as read from
    `contract.partial_fulfilled_amount` -- `None` or an `int`, never pre-
    normalized to 0 -- because the column is `nullable=True` with no
    default (a genuinely fresh, never-yet-delivered-against row could
    hold real SQL NULL, not literal 0) and `NULL = 0` is NULL (never TRUE)
    in SQL: comparing a normalized-to-0 Python value with `==` against a
    column that's actually NULL would silently fail to match a legitimate
    first delivery. `.is_(None)` vs `== <int>` is chosen per the ACTUAL
    value read, never both at once (no `or_()`/`coalesce()` needed).

    Not folded into `_guarded_transition` itself (10+ existing dependents,
    a plain status-only WHERE is exactly right for every one of them) --
    a narrower bespoke sibling for the one extra predicate `deliver()`
    alone needs, matching this codebase's own `_guarded_insure`/`_guarded_
    file_dispute` precedent (contract_insurance.py / contract_dispute.py)
    for exactly this class of need."""
    if to_status not in LEGAL_TRANSITIONS.get(from_status, frozenset()):
        raise ContractConflictError(
            f"illegal_transition: {from_status.value} -> {to_status.value} "
            f"is not in the contract state machine"
        )
    values = {"status": to_status, **column_updates}
    cas_predicate = (
        Contract.partial_fulfilled_amount.is_(None)
        if expected_partial_fulfilled_amount is None
        else Contract.partial_fulfilled_amount == expected_partial_fulfilled_amount
    )
    stmt = (
        update(Contract)
        .where(Contract.id == contract.id, Contract.status == from_status, cas_predicate)
        .values(**values)
    )
    result = db.execute(stmt)
    if result.rowcount == 0:
        raise ContractConflictError(
            f"concurrent_delivery: contract {contract.id} was modified by another "
            "delivery in the meantime -- retry"
        )
    for key, value in values.items():
        setattr(contract, key, value)
    return contract


def deliver(
    db: Session, contract_id: uuid.UUID, player_id: uuid.UUID, quantity_delivered: int,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """RETIRED (WO-CONTRACT-4-BULK) -- dormant, not wired to any route;
    Max chose the station-locker fulfillment path instead (see this
    module's own docstring). Kept function-only for now.

    Bulk-procurement partial delivery -- contracts.md:130: "Partial
    deliveries by the acceptor credit pro-rata at the per-unit rate."
    Validates docked-at-destination + cargo held (same shape as
    `complete()`'s own cargo check), credits `(quantity_delivered /
    contract.quantity) x payment` pro-rata, and monotonically increments
    `partial_fulfilled_amount` (ADR-0049, contracts.md:54 -- "once N units
    are credited, remaining quota is fixed at (quantity - N) regardless of
    acceptor walk-away"). Transitions ACCEPTED -> IN_PROGRESS on the first
    partial that does NOT complete the contract, stays IN_PROGRESS (self-
    loop) on subsequent partials, -> COMPLETED (from either ACCEPTED or
    IN_PROGRESS) the moment the FULL remaining quantity is delivered --
    contracts.md:91's own "partial_fulfilled bridges accepted ->
    in_progress" only describes the MULTI-call case; a single delivery
    covering the entire quantity in one call never needs to bridge through
    IN_PROGRESS at all, matching `complete()`'s own atomic single-shot
    shape for that degenerate (N=1 delivery) case. FLUSH-ONLY.

    MONEY INVARIANT (this WO's own money-path gate target): cumulative
    `partial_fulfilled_payout` NEVER exceeds `payment`, AND reconciles to
    EXACTLY `payment` the moment the contract completes (never silently
    shorts the acceptor a credit or two to rounding drift across many
    small partials either) -- see `_compute_bulk_delivery_payout`'s own
    docstring for the two-branch formula (conservative clamp for an
    intermediate partial, exact-remainder reconciliation for the
    completing one) and its worked rounding example. Computed WHOLE-
    CREDIT-EARLY throughout (R3 idiom, matching WO-2b-HOLD-ESCROW's own
    claim-offset rewrite -- see `_compute_claim_offset`'s docstring,
    contract_insurance.py) -- every comparison is an exact integer bound,
    never a Decimal rounding boundary. For an NPC-issued row this mints up
    to `payment` total across every delivery combined (same "NPC credits
    are canonically infinite" precedent `complete()` already establishes,
    contracts.md:155); for a PLAYER-issued row this is a partial RELEASE
    of the escrow the issuer already funded at post time (same non-mint
    reasoning `complete()`'s own WO-ECON-CONTRACT-2-PLAYER-ESCROW comment
    gives) -- `escrow_amount` itself is left untouched by every partial
    (mirroring `complete()`'s own minimal-touch precedent, which never
    decrements escrow_amount either); only `escrow_state` flips to
    RELEASED at the terminal COMPLETED event.

    Rejects (no state change) an over-delivery request (`quantity_
    delivered` greater than the remaining quota) rather than silently
    clamping it -- matches `complete()`'s own "raise WITHOUT any state
    change" convention for a caller-side mismatch, and avoids any ambiguity
    about what happens to cargo the caller claimed but this call didn't
    actually consume."""
    now = now or _now()
    contract = _load_contract(db, contract_id)
    _validate_bulk_delivery_request(contract, player_id, quantity_delivered)

    total_quantity = int(contract.quantity or 0)
    # Captured RAW (None or int, never pre-normalized) -- this exact value
    # feeds _guarded_deliver's compare-and-swap predicate below. See that
    # function's own docstring for why the raw shape matters (NULL vs
    # literal 0 are NOT the same thing to compare against in SQL).
    raw_partial_fulfilled_amount = contract.partial_fulfilled_amount
    already_delivered = int(raw_partial_fulfilled_amount or 0)
    remaining_quantity = total_quantity - already_delivered
    if quantity_delivered > remaining_quantity:
        raise ContractError(
            f"exceeds_remaining_quota: {quantity_delivered} requested, only "
            f"{remaining_quantity} of {total_quantity} remain"
        )

    player = _load_player(db, player_id, for_update=True)
    if not player.is_docked or player.current_port_id != contract.destination_station_id:
        raise ContractConflictError(
            "wrong_station: you must be docked at the contract's destination "
            "station to deliver"
        )
    ship = player.current_ship
    if ship is None:
        raise ContractError("No active ship to deliver cargo from")
    cargo = ship.cargo if isinstance(ship.cargo, dict) else {}
    contents = dict(cargo.get("contents") or {})
    held = int(contents.get(contract.commodity_type, 0) or 0)
    if held < quantity_delivered:
        raise ContractError(
            f"insufficient_cargo: delivering {quantity_delivered} "
            f"{contract.commodity_type}, you have {held}"
        )

    new_delivered_total = already_delivered + quantity_delivered
    completes_now = new_delivered_total >= total_quantity
    to_status = ContractStatus.COMPLETED if completes_now else ContractStatus.IN_PROGRESS
    from_status = contract.status

    # Whole-credit-early (R3) -- see _compute_bulk_delivery_payout's own
    # docstring, and this function's own MONEY INVARIANT paragraph above.
    already_paid_int = _to_credits_int(_round_credits(_as_decimal(contract.partial_fulfilled_payout or 0)))
    payout_this_delivery = _compute_bulk_delivery_payout(contract, quantity_delivered, total_quantity, completes_now)

    transition_kwargs: Dict[str, Any] = {
        "partial_fulfilled_amount": new_delivered_total,
        "partial_fulfilled_payout": Decimal(already_paid_int + payout_this_delivery),
    }
    if completes_now:
        transition_kwargs["completed_at"] = now
    # WO-3b money-path gate REVISE (mack CRITICAL) -- _guarded_deliver,
    # NOT the shared _guarded_transition: the self-loop (IN_PROGRESS ->
    # IN_PROGRESS) edge needs the extra partial_fulfilled_amount
    # compare-and-swap predicate; see that function's own docstring.
    _guarded_deliver(
        db, contract, from_status, to_status, raw_partial_fulfilled_amount, **transition_kwargs
    )

    contents[contract.commodity_type] = held - quantity_delivered
    cargo["contents"] = contents
    cargo["used"] = sum(int(q) for q in contents.values() if isinstance(q, (int, float)))
    ship.cargo = cargo
    flag_modified(ship, "cargo")

    if payout_this_delivery > 0:
        player.credits = (player.credits or 0) + payout_this_delivery

    if completes_now and contract.issuer_type == ContractIssuerType.PLAYER:
        contract.escrow_state = ContractEscrowState.RELEASED

    db.flush()

    logger.info(
        "Player %s delivered %d/%d units of bulk_procurement contract %s "
        "(paid %d credits, status now %s)",
        player_id, quantity_delivered, total_quantity, contract.id, payout_this_delivery, to_status.value,
    )
    return {
        "id": str(contract.id),
        "status": contract.status.value,
        "delivered_this_call": quantity_delivered,
        "partial_fulfilled_amount": contract.partial_fulfilled_amount,
        "payout_this_delivery": payout_this_delivery,
        "partial_fulfilled_payout": float(contract.partial_fulfilled_payout),
        "credits": player.credits,
        "completed_at": now if completes_now else None,
    }


def walk_away_bulk_procurement(
    db: Session, contract_id: uuid.UUID, player_id: uuid.UUID, now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """RETIRED (WO-CONTRACT-4-BULK) -- dormant, not wired to any route;
    Max chose the station-locker fulfillment path instead, where a bulk
    contract's abandon() goes through contract_service.abandon()'s own
    now-bulk-aware dispatch (dynamic penalty + EXPIRED, not this
    function's "returns to posted, no penalty" model) -- see this
    module's own docstring and contract_service.abandon()'s own
    [NO-CANON, superseding] note. Kept function-only for now.

    Bulk-procurement walk-away -- contracts.md:78/:130: "acceptor walks
    (bulk_procurement) -> posted (partial banked, fee forfeit)". DISTINCT
    from `abandon()` (contract_service.py -- cargo_delivery/express_
    delivery/hazardous_transport's single-acceptor walk-away, which
    charges `contract.penalty` and terminally CANCELS): bulk's own walk-
    away charges NO penalty, reverts to POSTED (not CANCELLED, so a new
    acceptor can pick up the remainder) rather than reusing `abandon()`,
    and PRESERVES `partial_fulfilled_amount`/`partial_fulfilled_payout`
    untouched by simply never writing to them (the ADR-0049 monotonic-
    counter guarantee, contracts.md:54). The acceptance fee paid at
    accept() time is NEVER refunded here -- canon's own worked example
    (contracts.md:184): "Acceptance fees forfeit by walked acceptors are
    sunk" -- this function simply never touches player credits for the fee
    side, no explicit forfeit step needed (it was already spent at
    accept() time). Legal from BOTH ACCEPTED (walked away before any
    partial delivery) and IN_PROGRESS (walked away after at least one
    partial) -- LEGAL_TRANSITIONS (contract_escrow_core.py) gates both
    edges. FLUSH-ONLY."""
    now = now or _now()
    contract = _load_contract(db, contract_id)
    if getattr(contract, "contract_type", None) != ContractType.BULK_PROCUREMENT:
        raise ContractError(
            "not_bulk_procurement: walk_away_bulk_procurement() only applies to bulk_procurement contracts"
        )
    if contract.acceptor_player_id != player_id:
        raise ContractError("This contract is not accepted by you")
    if contract.status not in (ContractStatus.ACCEPTED, ContractStatus.IN_PROGRESS):
        raise ContractConflictError(
            f"stale_status: contract {contract.id} is '{contract.status.value}', "
            "not 'accepted' or 'in_progress'"
        )

    player = _load_player(db, player_id, for_update=True)

    # WO-1a-CORE (mack CRITICAL #2) idiom, same as abandon()'s own: refresh
    # AFTER the lock, BEFORE the insurance_coverage_tier check below -- see
    # _refresh_contract_insurance_snapshot's own docstring for the exploit
    # this closes.
    _refresh_contract_insurance_snapshot(db, contract)

    from_status = contract.status
    # NOTE: `accepted_at` is deliberately NOT cleared here (unlike
    # `acceptor_player_id`) -- matching abandon()'s own established
    # precedent, which never touches it either. Two independent reasons:
    # (1) a fresh accept() unconditionally overwrites it (`accepted_at=
    # now`, contract_service.py's own `accept()`), so clearing it here is
    # pure redundancy the next accept() would immediately undo; (2) it
    # would actively BREAK the insurance-refund computation below, which
    # needs the PRE-transition `accepted_at` as `_compute_insurance_
    # cancellation_refund`'s "elapsed = cancelled_at - accepted_at" clock
    # start -- nulling it first (found via a failing test) silently
    # zeroed out a real refund a walking acceptor was owed.
    _guarded_transition(
        db, contract, from_status, ContractStatus.POSTED,
        acceptor_player_id=None,
    )

    # WO-CONTRACT-1-INSURANCE (ADR-0062 E-I2), same mid-term-cancellation
    # refund idiom abandon() already uses -- orthogonal to the walk-away
    # itself, applies whenever the walking acceptor held coverage.
    insurance_refund = 0
    if contract.insurance_coverage_tier is not None:
        insurance_refund = _to_credits_int(_compute_insurance_cancellation_refund(contract, now))
        if insurance_refund > 0:
            player.credits = (player.credits or 0) + insurance_refund
    db.flush()

    logger.info(
        "Player %s walked away from bulk_procurement contract %s "
        "(partial_fulfilled_amount=%s preserved, insurance refund %d)",
        player_id, contract.id, contract.partial_fulfilled_amount, insurance_refund,
    )
    return {
        "id": str(contract.id),
        "status": contract.status.value,
        "partial_fulfilled_amount": contract.partial_fulfilled_amount,
        "partial_fulfilled_payout": float(contract.partial_fulfilled_payout),
        "insurance_refund": insurance_refund,
        "credits": player.credits,
    }

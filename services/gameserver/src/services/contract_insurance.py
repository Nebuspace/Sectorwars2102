"""Trade Contract INSURANCE -- WO-CONTRACT-1-INSURANCE, split out of the
former monolithic `contract_service.py` by WO-CONTRACT-REFACTOR-SPLIT
(pure move, zero behavior change). Three-tier insurance engine: `insure()`
(the acceptor-only `POST /contracts/{id}/insure` endpoint function) plus
the E-I2 mid-term-cancellation refund helper (`_compute_insurance_
cancellation_refund`) that `abandon()` / `cancel_player_contract()` /
`file_dispute()` / `resolve_dispute()` all reuse for their own "mid-term
cancellation" credit movements -- those call sites live in `contract_
service.py` (lifecycle) and `contract_dispute.py` respectively, both of
which import from here; this module imports only from `contract_escrow_
core.py` (never the reverse -- no circular import).

Insurance CLAIM handling was originally built as a self-reported "my ship
is gone" payout and excised in the same round that first shipped this
module -- cipher's gate found it a farmable money-mint with no real
destruction-event verification behind it. WO-CONTRACT-1b-CLAIM-SAFETY
rebuilds it on a structurally different, non-farmable model: a CLAIM is
never a positive payout, only a PENALTY-OFFSET (`_compute_claim_offset` /
`apply_claim_offset` below) -- it reduces what the acceptor owes on a
real, guarded contract-failure event (`sweep_expired_accepted_contracts`,
contract_service.py), drawn from `Contract.insurance_pool_reserve` (a
real, persisted per-contract balance -- see that column's own docstring,
models/contract.py) rather than self-reported. Since nothing is ever
CREDITED, only a debit reduced, there is no positive sum to fabricate.
`insurance_claim_filed` stays unused/always-false on the schema -- this
rebuild has no dedicated "file a claim" action for the acceptor to call;
the offset applies automatically inside the existing expiry sweep,
matching the escrow table's own "insured acceptor: insurer pays penalty"
row (contracts.md), not a separate player-initiated claim endpoint.

SYNC Session / FLUSH-ONLY / guarded-UPDATE-is-the-lock conventions --
see `contract_escrow_core.py`'s own module docstring, unchanged here."""
import logging
import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any, Dict, Optional

from sqlalchemy import update
from sqlalchemy.orm import Session

from src.models.contract import Contract, ContractInsuranceCoverageTier, ContractStatus
from src.services.contract_escrow_core import (
    ContractConflictError,
    ContractError,
    _as_decimal,
    _load_contract,
    _load_player,
    _now,
    _round_credits,
    _to_credits_int,
)

logger = logging.getLogger(__name__)

# --- WO-CONTRACT-1-INSURANCE: insurance-tier constants ---------------------

# contracts.md:61 (schema) + the Risk & insurance table (:359-365) --
# verbatim: "premiums 2% / 5% / 10% of contract commodity value
# respectively". [NO-CANON] "contract commodity value" is never a stored
# column on this model (only `payment`, which for NPC rows is commodity_
# value scaled by distance_factor/urgency_factor multipliers >= 1.0 --
# contract_generator.compute_cargo_delivery_payment -- and for player rows
# is whatever the issuer typed in, with no formula tying it to a commodity
# quantity at all). `payment` is the only value FROZEN on the row this
# service can read without a fresh, non-deterministic live TradingService
# price lookup (which would make the SAME contract's premium different
# depending on when /insure happens to be called) -- pinned as the base,
# proposed to DECISIONS.md. For NPC rows this is a conservative (insurer-
# favorable) over-estimate of true commodity_value; for player rows
# `payment` IS the only value-of-the-deal signal that exists.
INSURANCE_PREMIUM_PCT: Dict[ContractInsuranceCoverageTier, Decimal] = {
    ContractInsuranceCoverageTier.BASIC: Decimal("2.0"),
    ContractInsuranceCoverageTier.STANDARD: Decimal("5.0"),
    ContractInsuranceCoverageTier.HAZARD: Decimal("10.0"),
}

# ADR-0062 E-I2's own consequences section names this exact constant:
# "The 10% retention is a one-line config (INSURANCE_CANCELLATION_FEE =
# 0.10), live-tunable." Applied as a MULTIPLIER on the pro-rata refund
# base (refund = base * (1 - fee)), matching E-I2's own pseudocode
# (`return refund_base * 0.90`) -- see _compute_insurance_cancellation_
# refund below, which quotes that pseudocode verbatim.
INSURANCE_CANCELLATION_FEE = Decimal("0.10")

# --- WO-CONTRACT-1b-CLAIM-SAFETY: claim-offset deductible ladder -----------

# Max's ruled model, verbatim: "Deductible ladder = Basic 5% / Standard
# 10% / Hazard 15% (ADR-0061 parallel, blessed)." Verified: ADR-0061 is
# "Group C -- combat correctness and rank wiring", not an insurance ADR by
# name -- the actual parallel is `ship-insurance.md`'s own Tiers table
# ("Deductible" column: BASIC 5% / STANDARD 10% / PREMIUM 15%), which that
# doc itself attributes to ADR-0061 ("deductible applied per ADR-0061" --
# ship_service.py's own `_calculate_insurance_payout` docstring, same
# citation). Same three percentages, same tier ORDER (contract insurance's
# third tier is named HAZARD rather than ship insurance's PREMIUM, but
# maps to the identical 15% deductible slot) -- confirmed, not invented.
CLAIM_DEDUCTIBLE_PCT: Dict[ContractInsuranceCoverageTier, Decimal] = {
    ContractInsuranceCoverageTier.BASIC: Decimal("5.0"),
    ContractInsuranceCoverageTier.STANDARD: Decimal("10.0"),
    ContractInsuranceCoverageTier.HAZARD: Decimal("15.0"),
}


# --- WO-CONTRACT-1-INSURANCE: helpers --------------------------------------

def _compute_insurance_cancellation_refund(contract: Any, cancelled_at: datetime) -> Decimal:
    """ADR-0062 E-I2, quoted verbatim:

        def cancellation_refund(contract, cancelled_at):
            elapsed = cancelled_at - contract.started_at
            remaining_fraction = max(0.0, 1 - elapsed / contract.duration)
            refund_base = contract.insurance_premium * remaining_fraction
            return refund_base * 0.90    # 10% cancellation fee

    [NO-CANON] `contract.started_at` / `contract.duration` are not literal
    columns on this codebase's Contract model. `accepted_at` is the closest
    analog to `started_at` -- coverage can only be purchased once a
    contract is ACCEPTED (contracts.md:357 "purchased at acceptance time";
    `insure()` below gates on status==accepted), so the policy's clock
    starts there. `duration` is derived as `deadline - accepted_at` (the
    contract's own committed delivery window) rather than a separately
    stored value. Returns 0 (no refund) if no premium was ever paid, or if
    the timestamps needed to compute a window are missing/degenerate --
    callers only invoke this when `insurance_coverage_tier is not None`,
    but a belt-and-suspenders zero-window guard costs nothing here.

    WO-1a-CORE (mack LOW): `remaining_fraction` is clamped to [0, 1] on
    BOTH ends, not just the floor the ADR pseudocode itself specifies
    (`max(0.0, ...)`, no ceiling). A `cancelled_at` before `accepted_at`
    (a caller passing a `now` that's earlier than the accept timestamp --
    e.g. a backward clock adjustment, or a test/caller bug) makes `elapsed`
    negative, which without an upper clamp inflates `remaining_fraction`
    past 1.0 and refunds MORE than the premium actually paid. The ADR's
    own pseudocode has this exact gap; closed here rather than propagated."""
    premium = _as_decimal(contract.insurance_premium_paid or 0)
    if premium <= 0 or contract.accepted_at is None or contract.deadline is None:
        return Decimal("0")
    duration_seconds = (contract.deadline - contract.accepted_at).total_seconds()
    if duration_seconds <= 0:
        return Decimal("0")
    elapsed_seconds = (cancelled_at - contract.accepted_at).total_seconds()
    remaining_fraction = min(
        Decimal("1"),
        max(Decimal("0"), Decimal("1") - Decimal(str(elapsed_seconds)) / Decimal(str(duration_seconds))),
    )
    refund_base = premium * remaining_fraction
    return _round_credits(refund_base * (Decimal("1") - INSURANCE_CANCELLATION_FEE))


def _refresh_contract_insurance_snapshot(db: Session, contract: Contract) -> None:
    """WO-1a-CORE (mack CRITICAL #1 + #2, cancel_player_contract and
    abandon respectively -- same root cause, one shared fix). `contract`
    is read via `_load_contract` BEFORE any player lock is acquired in
    both callers. A concurrent `insure()` call's own atomic UPDATE
    (`_guarded_insure`) can commit in the window between that unlocked
    read and the caller's player lock(s) being acquired -- without a
    refresh, the in-memory `insurance_coverage_tier` / `insurance_
    premium_paid` stay at their PRE-insure() values (None / 0) even
    though a real, committed policy now exists on the row, silently
    forfeiting the acceptor's premium on a mid-term cancel/abandon with
    ZERO contention required:
      - cancel_player_contract: the stale read fed `needs_acceptor_lock`,
        so the acceptor was never locked at all and the refund branch
        (`if acceptor is not None`) never ran.
      - abandon: the acceptor IS always locked (unconditionally), but the
        later `if contract.insurance_coverage_tier is not None` check
        still read the same stale, never-refreshed Python object.

    MUST be called AFTER the relevant player lock(s) are acquired, never
    before -- calling it earlier just moves the same race to a different
    window (a concurrent insure() could still land in the gap between an
    early refresh and the lock).

    `.populate_existing()` on a query keyed to `contract.id` -- already
    identity-mapped in this Session from the earlier `_load_contract`
    call -- overwrites THIS EXACT Python object's attributes with the
    live row, mirroring this module's own `_load_player(for_update=True)`
    populate_existing convention (see that function's own docstring for
    the identical "protects the read-check-mutate sequence" rationale).
    No `with_for_update()` here: no Contract-row lock is ever taken this
    way anywhere in this module -- the atomic guarded-UPDATE-WHERE is the
    lock for every Contract mutation (see module docstring); this is a
    pure identity-map refresh, not a row lock, and refreshes ALL of
    `contract`'s columns (not just the insurance ones) -- if `status` also
    moved concurrently (e.g. a sweep raced in too), the caller's own
    subsequent status-branch dispatch and `_guarded_transition`'s atomic
    UPDATE-WHERE remain the authoritative safety net regardless."""
    db.query(Contract).filter(Contract.id == contract.id).populate_existing().first()


# --- WO-CONTRACT-1b-CLAIM-SAFETY: claim-offset engine -----------------------

def _compute_claim_offset(contract: Any, penalty: Decimal) -> Dict[str, Decimal]:
    """PURE (no mutation, no DB access) -- the core of the rebuilt claim
    mechanism. Max's ruled model, verbatim: "CLAIM = PENALTY-OFFSET, never
    a positive payout... the acceptor eats penalty x deductible; the
    covered penalty x (1 - deductible) is settled by the insurer... The
    acceptor never RECEIVES credits -- they simply OWE LESS."

    No coverage tier: `acceptor_debit == penalty`, `pool_draw == 0`
    -- byte-identical to this codebase's pre-existing uninsured behavior
    (the caller's own unconditional `acceptor.credits -= penalty` idiom).

    With a tier: the deductible floor (`CLAIM_DEDUCTIBLE_PCT`) is what the
    acceptor owes NO MATTER WHAT -- never waived, never drawn from the
    pool. The remaining nominal share (`penalty - acceptor_floor`) is what
    the pool WOULD cover if it could; `pool_draw` is that nominal share
    bounded to what `contract.insurance_pool_reserve` (the real, persisted
    claims-fund balance -- see that column's own docstring, models/
    contract.py) actually holds RIGHT NOW, exactly the same "never more
    than the payer's actual balance" shape `_bounded_transfer` already
    established for issuer-funded dispute settlements (contract_escrow_
    core.py) -- this is that same principle applied to a POOL instead of
    a player wallet. `acceptor_debit = penalty - pool_draw` is therefore
    ALWAYS >= `acceptor_floor` and ALWAYS <= `penalty`: a fully-funded pool
    lands the acceptor exactly on the deductible floor; an empty or
    partially-drained pool degrades smoothly toward (at worst) the full,
    uninsured penalty -- never negative, never a positive credit, never
    more than what was actually owed to begin with. This closes the mint
    the ORIGINAL (excised) claim design left open: nothing is ever ADDED
    to the acceptor's balance, only a debit is reduced, so there is no
    positive sum for a fabricated "my ship is gone" self-report to farm.

    `pool_draw` is clamped to [0, min(insurer_nominal, pool_balance)] --
    a corrupt or negative `insurance_pool_reserve` (should never occur;
    the column is not-null-default-0 and only ever decremented by this
    exact function) cannot make `pool_draw` negative and inflate
    `acceptor_debit` above `penalty`.

    Returns `{"acceptor_debit": Decimal, "pool_draw": Decimal}` --
    `acceptor_debit` is what the caller charges the acceptor (replacing
    the old unconditional `penalty`); `pool_draw` is how much the caller
    must both (a) subtract from `contract.insurance_pool_reserve` and (b)
    subtract from any SAME-transaction issuer escrow refund for this
    contract -- see `sweep_expired_accepted_contracts`'s own docstring for
    why (b) is load-bearing: refunding the issuer their full escrow while
    the pool absorbed part of the acceptor's penalty is itself a mint,
    just moved to the other side of the ledger."""
    penalty = _round_credits(_as_decimal(penalty))
    if contract.insurance_coverage_tier is None:
        return {"acceptor_debit": penalty, "pool_draw": Decimal("0")}

    deductible_pct = CLAIM_DEDUCTIBLE_PCT[contract.insurance_coverage_tier]
    acceptor_floor = _round_credits(penalty * deductible_pct / Decimal(100))
    insurer_nominal = penalty - acceptor_floor

    pool_balance = _as_decimal(contract.insurance_pool_reserve or 0)
    pool_draw = min(insurer_nominal, pool_balance)
    if pool_draw < 0:
        pool_draw = Decimal("0")

    return {"acceptor_debit": penalty - pool_draw, "pool_draw": pool_draw}


def apply_claim_offset(contract: Any, penalty: Decimal) -> Dict[str, Decimal]:
    """Mutating twin of `_compute_claim_offset` -- draws `contract.
    insurance_pool_reserve` down in place by exactly `pool_draw` (never
    below 0, guaranteed by the pure function's own clamping) and returns
    the SAME `{"acceptor_debit", "pool_draw"}` dict for the caller to act
    on. Consumed per-actual-failure-event tied to THIS ONE contract --
    each contract carries its OWN `insurance_pool_reserve`, drawn at most
    ONCE (a contract can only ever reach the ACCEPTED -> EXPIRED guarded
    transition a single time; see `sweep_expired_accepted_contracts`'s own
    guarded-UPDATE-WHERE, which is what makes re-entry structurally
    impossible, not this function) -- this is what closes the "one loss
    claims every contract" exploit the original design left open: there
    is no shared, cross-contract pool or ship-loss event object for a
    single incident to draw against N times. No DB write of its own --
    the caller's existing `db.flush()` (already at the end of the sweep
    pass) persists this mutation, matching every other in-place attribute
    assignment already in that function (e.g. `candidate.escrow_state =
    ...`)."""
    offset = _compute_claim_offset(contract, penalty)
    if offset["pool_draw"] > 0:
        contract.insurance_pool_reserve = _as_decimal(contract.insurance_pool_reserve or 0) - offset["pool_draw"]
    return offset


def _guarded_insure(
    db: Session, contract: Contract, tier: ContractInsuranceCoverageTier, premium: Decimal,
) -> Contract:
    """The insure()-time twin of `_guarded_transition` -- an atomic
    `UPDATE ... WHERE id=:id AND status='accepted' AND insurance_coverage_
    tier IS NULL` closes the double-insure race the SAME way every other
    transition in this module closes its own race: the WHERE clause IS the
    lock, no `SELECT ... FOR UPDATE` on the Contract row needed. Not folded
    into `_guarded_transition` itself -- that helper is status-machine-
    specific (LEGAL_TRANSITIONS membership, from_status/to_status), and
    `insure()` doesn't change `status` at all; this is a narrower sibling
    for the one column-pair it actually claims."""
    stmt = (
        update(Contract)
        .where(
            Contract.id == contract.id,
            Contract.status == ContractStatus.ACCEPTED,
            Contract.insurance_coverage_tier.is_(None),
        )
        .values(insurance_coverage_tier=tier, insurance_premium_paid=premium)
    )
    result = db.execute(stmt)
    if result.rowcount == 0:
        raise ContractConflictError(
            f"stale_status: contract {contract.id} is not eligible for insurance "
            "-- it is not 'accepted', or it already carries a coverage tier"
        )
    contract.insurance_coverage_tier = tier
    contract.insurance_premium_paid = premium
    return contract


def insure(
    db: Session, contract_id: uuid.UUID, acceptor_player_id: uuid.UUID,
    tier: ContractInsuranceCoverageTier, now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """`POST /contracts/{id}/insure` (contracts.md:219/:224). Buy one of the
    three coverage tiers on an ACCEPTED contract.

    VERIFY-FIRST FINDING (WO-CONTRACT-1-INSURANCE): the WO's own paraphrase
    ("tier selection at accept") reads as folding insurance into accept()
    itself. Canon disagrees with that paraphrase on TWO independent axes,
    and per this module's own established docs-win convention (see
    cancel_player_contract's docstring: "the detailed table is authoritative
    ... NOT the terser ... prose") both point the same way:
      1. The escrow table (:166-176) lists "Accept" and "Insure" as
         SEPARATE phases with separate triggers (`POST .../accept` charges
         only the acceptance fee; `POST .../insure` is its own row,
         `-premium`).
      2. The API surface table (:208-224) lists `POST /contracts/{id}/
         insure` as its OWN endpoint, distinct from `/accept`.
    Built here as the separate endpoint the two detailed tables agree on --
    "purchased at acceptance time" (:357's prose) reads as "once the
    contract is in the accepted phase of its life", not "in the same call".

    Only the ACCEPTOR may insure (they carry the delivery risk -- schema
    line 63's "acceptor claims insurance for ship loss"), and only once:
    re-insuring / upgrading an already-insured contract is [NO-CANON]
    out of scope this build (canon says nothing about upgrades for
    CONTRACT insurance, unlike ship-insurance.md's explicit BASIC->
    STANDARD->PREMIUM upgrade path) -- `_guarded_insure`'s WHERE clause
    rejects it uniformly with the same race-safe idiom that also closes
    the concurrent-double-insure race. A race loser is NEVER charged the
    premium (the guarded UPDATE runs BEFORE any credit mutation, same
    "fee-less race loser" principle as accept()). FLUSH-ONLY."""
    now = now or _now()
    contract = _load_contract(db, contract_id)
    if contract.acceptor_player_id != acceptor_player_id:
        raise ContractError("This contract is not accepted by you")
    if contract.status != ContractStatus.ACCEPTED:
        raise ContractConflictError(
            f"stale_status: contract {contract.id} is '{contract.status.value}', not 'accepted'"
        )
    if contract.insurance_coverage_tier is not None:
        raise ContractError("already_insured: this contract already carries a coverage tier")
    if not isinstance(tier, ContractInsuranceCoverageTier):
        raise ContractError(f"unknown_tier: '{tier}' is not a valid insurance tier")

    # [NO-CANON] premium base -- see INSURANCE_PREMIUM_PCT's own module-level
    # comment for why `contract.payment` (not a live market lookup) is the
    # pinned "contract commodity value" proxy.
    premium = _round_credits(_as_decimal(contract.payment) * INSURANCE_PREMIUM_PCT[tier] / Decimal(100))

    acceptor = _load_player(db, acceptor_player_id, for_update=True)
    if Decimal(acceptor.credits or 0) < premium:
        raise ContractError(
            f"insufficient_credits: {tier.value} premium is {premium}, you have {acceptor.credits or 0}"
        )

    _guarded_insure(db, contract, tier, premium)

    acceptor.credits = _to_credits_int(Decimal(acceptor.credits or 0) - premium)
    db.flush()

    logger.info(
        "Player %s insured contract %s at %s tier (premium %s)",
        acceptor_player_id, contract.id, tier.value, premium,
    )
    return {
        "id": str(contract.id),
        "insurance_coverage_tier": tier.value,
        "insurance_premium_paid": float(premium),
        "credits": acceptor.credits,
    }

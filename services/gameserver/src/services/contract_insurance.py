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

Insurance CLAIM handling (paying out on a ship destroyed in transit) was
built and then excised in the same round that first shipped this module --
cipher's gate found the claim's self-reported "my ship is gone" check a
farmable money-mint with no real destruction-event verification behind
it; that half is deferred to a dedicated, design-gated WO-1b-CLAIM-SAFETY.
The `insurance_claim_filed` column stays on the schema (harmless, always
false today) for whenever that WO lands.

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

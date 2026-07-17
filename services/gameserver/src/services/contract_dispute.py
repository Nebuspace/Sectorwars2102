"""Trade Contract DISPUTE filing + Tier-1/Tier-2 resolution --
WO-CONTRACT-2-DISPUTE-T1, split out of the former monolithic `contract_
service.py` by WO-CONTRACT-REFACTOR-SPLIT (pure move, zero behavior
change). Dispute filing + Tier-1 automated arbitration (`file_dispute`)
and the Tier-2 admin ruling interface (`resolve_dispute` -- FUNCTION ONLY,
the admin route is impl-admin-ui's lane) -- see this module's own
NO-CANON pins below (no delivery-event log exists for cargo-manifest
verification; the expiry sweep already releases escrow before any dispute
can be filed, reconciled as a fresh credit movement rather than
re-touching the emptied escrow ledger; reputation/cooldowns are unbuilt
anywhere in this codebase).

Imports primitives from `contract_escrow_core.py` and the insurance
mid-term-cancellation refund helper from `contract_insurance.py` (never
the reverse -- see each sibling's own module docstring for the full
dependency layering). `contract_service.py` (lifecycle) re-exports
`file_dispute` / `resolve_dispute` for external callers already reaching
`contract_service.file_dispute` / `.resolve_dispute` -- see its own
docstring for the full re-export list.

Reputation columns everywhere in this module (reward/penalty/forgive/
reverse language in canon's own tables) are DELIBERATELY NOT applied
anywhere below. contracts.md's own Reputation Effects section (verified
again for this WO): "reputation_reward and reputation_penalty are
written on the contract row at posting time but are never READ by
complete() or abandon() -- design-only." Grepped this module for any
existing reputation code: zero hits. There is nothing anywhere in this
codebase for a dispute resolution to pause, apply, forgive, or reverse
-- building a reputation side effect here would be inventing the FIRST
consumer of a system nothing else wires either. `_is_reputation_
penalty_paused` below is the one exception: a real, correct, testable
GATE a future reputation-application pass would consult, built now so
it exists and is provably wired, exactly like `_is_player_blocklisted`'s
established no-op-seam precedent (contract_service.py) -- not a false
claim that reputation is applied end-to-end today.

Cooldowns ("24h cooldown on that issuer", "72h cooldown; account flag
on repeat") are the SAME situation -- contracts.md's own Reputation
Effects section already flags "bans the player... for a cooldown" as
design-only elsewhere in this doc; no cooldown/ban model exists
anywhere in this codebase (grepped). Not invented here either.

SYNC Session / FLUSH-ONLY / guarded-UPDATE-is-the-lock conventions --
see `contract_escrow_core.py`'s own module docstring, unchanged here."""
import logging
import uuid
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any, Dict, Optional

from sqlalchemy.orm import Session

from src.models.contract import (
    ContractDisputeResolution,
    ContractEscrowState,
    ContractIssuerType,
    ContractStatus,
)
from src.models.station import Station, StationStatus
from src.services.contract_escrow_core import (
    PLAYER_POST_CANCEL_FEE_PCT_POST_ACCEPT,
    ContractConflictError,
    ContractError,
    _as_decimal,
    _bounded_transfer,
    _guarded_transition,
    _load_contract,
    _load_player,
    _load_two_players_for_update,
    _now,
    _round_credits,
    _to_credits_int,
)
from src.services.contract_insurance import _compute_insurance_cancellation_refund

logger = logging.getLogger(__name__)

# --- WO-CONTRACT-2-DISPUTE-T1: dispute constants ----------------------------

# contracts.md:390 -- "within 48 game-hours of the failure timestamp".
# [NO-CANON] game-hours vs wall-hours: this module NEVER imports
# `src.core.game_time` (GAME_TIME_SCALE) anywhere -- verified by grep.
# EVERY existing timed check in this file (accept()'s deadline check,
# cancel_player_contract's past-deadline guard, both expiry sweeps -- see
# contract_service.py) uses raw `datetime`/`timedelta` wall-clock
# arithmetic against `contract.deadline`, which is itself GENERATED as a
# wall-clock window by contract_generator.py -- that generator's own
# pick_deadline_hours() docstring explicitly rejects GAME_TIME_SCALE for
# contracts: "matching this codebase's dominant wall-clock-storage
# convention for timed state ... rather than introducing a new GAME_TIME_
# SCALE-adjusted deadline surface UNIQUE TO CONTRACTS." Dispute windows
# measure from that SAME wall-clock `deadline` field (see FAILURE
# TIMESTAMP below) -- treating THIS one new window as GAME_TIME_SCALE-
# scaled while every sibling contract-deadline check stays wall-clock
# would make the SAME contract row mix two incompatible time domains.
# Pinned as literal wall-clock hours, matching every other timed check in
# this domain; proposed to DECISIONS.md.
DISPUTE_FILING_WINDOW_HOURS = 48

# [NO-CANON] FAILURE TIMESTAMP: neither contracts.md nor ADR-0062 names
# the literal column. Contract has NO `expired_at`/`failed_at` column --
# verified: `sweep_expired_contracts`/`sweep_expired_accepted_contracts`
# (contract_service.py) only ever set `status = EXPIRED`, never stamp a
# timestamp anywhere. `contract.deadline` is the closest stable, already-
# stored proxy -- for a deadline-lapse expiry (the ONLY expiry mechanism
# this codebase's shipped sweeps produce), the failure moment IS, by
# definition, the deadline itself. Pinned as the failure timestamp;
# proposed to DECISIONS.md.

# contracts.md:394 -- "Tier 1: automated arbitration (within 1 game-hour)".
# This build runs Tier-1 SYNCHRONOUSLY inside file_dispute() itself
# (see that function's own docstring) -- it resolves in milliseconds,
# trivially inside any interpretation of this window; no separate
# scheduled sweep is introduced for it.

# ADR-0062 E-I3 -- "Disputed value > 100,000 cr" (verbatim threshold).
DISPUTE_HIGH_VALUE_THRESHOLD = Decimal("100000")


def _is_reputation_penalty_paused(contract: Any) -> bool:
    """Real, correct, testable gate for a future reputation-application
    pass to consult (contracts.md:390 -- filing "pauses the reputation
    penalty"). Paused for exactly the window the contract sits in
    DISPUTED status -- resolution (Tier-1 or Tier-2) always moves it to
    a terminal status (COMPLETED/CANCELLED), at which point the pause
    naturally lifts because the gate's own condition stops matching."""
    return contract.status == ContractStatus.DISPUTED


def _tier1_cargo_manifest_match(contract: Any) -> bool:
    """contracts.md:398 -- 'Cargo manifest match': `Cargo.logs` shows the
    expected commodity/quantity arrived at the destination at delivery
    time +/- 5 minutes. [NO-CANON, documented no-op seam] NO SUCH MODEL
    EXISTS anywhere in this codebase -- grepped for a `Cargo` model, a
    delivery-event log, or any historical per-contract fulfillment
    record: zero hits. `MarketTransaction` (enhanced_market_transactions)
    is the closest REAL transaction log in this codebase, but it records
    ORDINARY station buy/sell trades -- contract `complete()` never
    writes to it (contract fulfillment is a direct cargo decrement +
    status flip, no market trade involved), so it evidences nothing
    about contract delivery specifically. A heuristic against the
    acceptor's CURRENT ship cargo (do they still hold the right
    commodity/quantity?) was considered and REJECTED -- trivially
    gameable (hold cargo you never delivered, dispute, "prove" you have
    it), the exact class of farmable-money-path issue WO-1b-CLAIM-SAFETY
    was created to fix for the insurance claim. Always returns False --
    documented no-op seam, same convention as `_is_player_blocklisted`
    -- until a real per-contract delivery-event log exists for a future
    WO to wire here. Exercised by a monkeypatch-to-True test proving the
    seam is genuinely consulted, not decorative."""
    return False


def _tier1_destination_unreachable(db: Session, contract: Any) -> bool:
    """contracts.md:399 -- `Station.status` was offline/destroyed/
    inaccessible AT THE FAILURE MOMENT. [NO-CANON] TWO gaps, both already
    established elsewhere in this module: (1) `StationStatus` has no
    literal OFFLINE/DESTROYED/INACCESSIBLE member -- `ABANDONED` is the
    EXISTING proxy this module already uses (see `post_player_contract`'s
    own `destination.status == StationStatus.ABANDONED` check and its
    docstring, contract_service.py). (2) no historical station-status log
    exists -- this checks the station's CURRENT status, not a snapshot at
    the failure moment. Reasonable proxy given Tier-1 resolution runs
    synchronously at filing time, itself gated within 48 game-hours of the
    failure -- an outage severe enough to strand a delivery typically
    persists across that window, and this errs toward the acceptor (no
    false negative from a station that's STILL down when the dispute is
    filed). The ONE genuinely resolvable Tier-1 case in this codebase's
    current shipped reality -- see this module's own header comment for
    why the other two are documented no-op seams."""
    station = db.query(Station).filter(Station.id == contract.destination_station_id).first()
    return station is not None and station.status == StationStatus.ABANDONED


def _tier1_issuer_unilateral_cancellation(contract: Any) -> bool:
    """contracts.md:400 -- 'contract history shows issuer cancelled after
    acceptor accepted'. [NO-CANON, documented no-op seam] STRUCTURALLY
    UNREACHABLE for any contract this function is ever called against.
    `file_dispute` only accepts a filing on a `status == EXPIRED`
    contract, reached EXCLUSIVELY via the deadline-expiry sweep's own
    guarded UPDATE (`WHERE status == 'accepted'`). `cancel_player_
    contract`'s ACCEPTED branch is a SEPARATE atomic guarded transition
    (ACCEPTED -> CANCELLED, via the SAME `_guarded_transition` machinery)
    -- once that succeeds, the contract is CANCELLED, permanently
    excluded from the expiry sweep's own status filter, and can never
    reach EXPIRED. The two terminal states are mutually exclusive BY
    CONSTRUCTION: a contract that is EXPIRED, by definition, was never
    successfully cancelled by its issuer. No audit trail records a
    FAILED cancel attempt either (a blocked past-deadline cancel 409s
    with zero mutation -- nothing to read back). Always returns False --
    documented no-op seam, same convention as `_is_player_blocklisted`
    -- kept as a real, exercised branch (not silently dropped) in case a
    future WO adds a cancellation audit trail or a code path that can
    leave this trace. Exercised by a monkeypatch-to-True test proving
    both the branch AND its settlement math are correct even though
    unreachable in production today."""
    return False


def _ei3_both_parties_dispute(contract: Any) -> bool:
    """ADR-0062 E-I3 -- 'both parties dispute -- buyer and seller each
    file dispute claims'. [NO-CANON, documented no-op seam] STRUCTURALLY
    UNREACHABLE: contracts.md:390 -- 'Only the acceptor can file' -- this
    build has no issuer-side filing path at all (no route, no service
    function), so a contract can never carry two independent dispute
    filings to compare. Always returns False."""
    return False


def _ei3_evidence_trail_incomplete(db: Session, contract: Any) -> bool:
    """ADR-0062 E-I3 -- 'evidence trail incomplete -- combat/market/
    delivery log rows missing for the disputed timeframe (e.g. a server
    outage gap)'. [NO-CANON] no delivery-event log exists at all in this
    codebase (see `_tier1_cargo_manifest_match`'s own docstring) -- but
    treating EVERY dispute as unconditionally 'evidence incomplete'
    would defeat Tier-1's one genuinely resolvable case (destination-
    unreachable, which never needed a delivery log to begin with) by
    escalating everything regardless. The one REAL, checkable signal
    available: the destination Station row itself no longer resolving
    (hard-deleted) -- genuine missing data, not an unbuilt feature.
    Almost always False in practice (stations aren't hard-deleted), which
    honestly reflects the real signal this codebase has, not an invented
    one."""
    station = db.query(Station).filter(Station.id == contract.destination_station_id).first()
    return station is None


def _ei3_high_value(contract: Any) -> bool:
    """ADR-0062 E-I3 -- 'disputed value > 100,000 cr -- high-value
    disputes always go to admin review, even with complete logs'.
    Verbatim threshold, real and checkable against `contract.payment`."""
    return _as_decimal(contract.payment) > DISPUTE_HIGH_VALUE_THRESHOLD


def _apply_dispute_insurance_refund(contract: Any, acceptor: Any, now: datetime) -> int:
    """WO-CONTRACT-2-DISPUTE-T1-REVISE (mack LOW): `insure()` only
    requires `status == ACCEPTED` -- a contract can carry an insurance
    tier (and a paid premium) all the way into `EXPIRED` (neither expiry
    sweep clears `insurance_coverage_tier`/`insurance_premium_paid`), yet
    every dispute-driven CANCELLED outcome left the premium unaddressed
    entirely. Reuses the EXACT SAME `_compute_insurance_cancellation_
    refund` idiom `abandon()`/`cancel_player_contract()` already use
    (contract_service.py) for their own "mid-term cancellation" (ADR-0062
    E-I2) -- called ONLY for a CANCELLED outcome, never COMPLETED
    (contracts.md:62's "on completion: released to insurer, not
    refunded" -- the SAME rule `complete()` already honors by simply
    never touching this column; callers of this helper must gate on
    `target_status == CANCELLED` themselves, matching that precedent
    rather than re-deriving it here).

    [NO-CANON, informational -- refund it pro-rata like abandon/cancel
    do, chosen over silently documenting this as a follow-up] by
    construction this ALWAYS evaluates to 0 for any dispute today:
    `_compute_insurance_cancellation_refund`'s `remaining_fraction` is
    `max(0, 1 - elapsed/duration)`, and a dispute can only ever be filed
    on an EXPIRED contract -- i.e. `elapsed >= duration` (the deadline
    has passed) ALREADY holds by definition before any dispute can
    exist, so `remaining_fraction` is always exactly 0 by the time this
    runs. Wired in anyway for correctness/forward-compatibility (not a
    gap silently left open) rather than skipped -- the helper is already
    tested and safe, costs nothing to call, and a future WO that changes
    what "cancelled_at" means for this calculation gets it for free
    without anyone having to remember this seam exists."""
    if contract.insurance_coverage_tier is None:
        return 0
    refund = _to_credits_int(_compute_insurance_cancellation_refund(contract, now))
    if refund > 0:
        acceptor.credits = (acceptor.credits or 0) + refund
    return refund


# --- WO-CONTRACT-2-DISPUTE-T1: filing + Tier-1 automated arbitration -------

def file_dispute(
    db: Session, contract_id: uuid.UUID, acceptor_player_id: uuid.UUID,
    reason: str, evidence_snapshot: Optional[str] = None, now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """contracts.md:390 -- acceptor-only filing on a FAILED (`EXPIRED`)
    contract, within `DISPUTE_FILING_WINDOW_HOURS` of the failure
    timestamp (`contract.deadline` -- see that constant's own module-
    level comment for both the wall-clock-vs-game-hour and the failure-
    timestamp NO-CANON pins). Filing atomically flips `status: EXPIRED ->
    DISPUTED` + `escrow_state -> DISPUTED` + stamps `dispute_filed_at` /
    `dispute_notes` in ONE guarded UPDATE -- the SAME atomic-UPDATE-WHERE
    idiom every other transition in this domain uses, so a double-file
    attempt is rejected the identical "race loser touches nothing" way
    (WHERE status='expired' matches zero rows once the first filing
    lands) -- no separate `dispute_filed_at IS NULL` pre-check needed or
    safe (same reasoning `_guarded_insure`'s own docstring gives for its
    sibling column-claim guard, contract_insurance.py).

    TIER 1 runs SYNCHRONOUSLY, immediately after a successful filing, in
    this SAME function/transaction -- trivially within contracts.md:394's
    "within 1 game-hour" window (this resolves in milliseconds, not a
    separately-scheduled sweep). Tries each of the three canon cases in
    canon's own order (`_tier1_cargo_manifest_match` ->
    `_tier1_destination_unreachable` -> `_tier1_issuer_unilateral_
    cancellation` -- see each helper's own docstring for which are real
    vs. documented no-op seams in this codebase today) and settles per
    contracts.md:398-400 on the first match. If NONE match, the contract
    stays DISPUTED with escrow frozen (already flipped at filing) and
    `escalated_to_admin` is set per the OR of the three ADR-0062 E-I3
    criteria (`_ei3_both_parties_dispute` / `_ei3_evidence_trail_
    incomplete` / `_ei3_high_value`) -- landing in the `(status,
    dispute_filed_at)`-indexed Tier-2 queue for `resolve_dispute` (owned
    by the admin route, impl-admin-ui's lane) to pick up later.

    [NO-CANON, MAJOR] "ESCROW" IN EVERY SETTLEMENT BELOW IS ALREADY GONE.
    A dispute can only be filed on an EXPIRED contract -- but this
    codebase's OWN shipped `sweep_expired_accepted_contracts` (WO-DRIFT-
    econ-accepted-deadline-expiry, unrelated to and unchanged by this WO,
    contract_service.py) ALREADY refunds a PLAYER-issuer's escrow to
    them, in the SAME transaction that flips status to EXPIRED -- by the
    time ANY dispute could be filed, `escrow_state` is already
    `refunding` and the issuer already has their money back.
    contracts.md's dispute-resolution prose ("escrow frozen", "Escrow ->
    acceptor in full") reads as if escrow is STILL held at dispute time,
    which is TRUE for the ACCEPTED-contract disputes canon's prose seems
    to picture, but this codebase only reaches EXPIRED (and therefore
    only reaches a dispute) via a path that already released it.
    Reconciled here (and in resolve_dispute below) by treating every
    "escrow -> X" settlement as a FRESH credit movement of `contract.
    payment` between issuer and acceptor, computed from the frozen
    `payment`/`acceptance_fee_pct` columns (deterministic, same values
    `accept()` itself used) rather than manipulating the (already-
    emptied) `escrow_amount`/`escrow_state` ledger a second time --
    achieving the same NET settlement canon specifies without double-
    counting or fabricating funds.

    WO-CONTRACT-2-DISPUTE-T1-REVISE (mack CRITICAL): every issuer-funded
    movement below is a BOUNDED transfer via `_bounded_transfer` (see its
    own docstring, contract_escrow_core.py), NEVER a clamped-debit-plus-
    unconditional-credit -- the acceptor collects ONLY what the issuer's
    balance can actually cover, never the full nominal amount regardless
    of it. An issuer who has since spent their refunded escrow does not
    go negative, AND does not cause credits to be minted from nothing
    either. Flagged prominently here rather than silently picked;
    proposed to DECISIONS.md as a real architectural gap between canon's
    dispute-resolution model and this codebase's already-shipped,
    already-gated expiry-sweep behavior (which this WO does not touch or
    reopen).

    Reputation pause/reward/forgive/reverse and cooldowns: see this
    module's own header comment -- nothing is applied, nothing exists yet
    to apply it to; `_is_reputation_penalty_paused` is the one real,
    tested gate built for a future consumer.

    The sweep's own credit-penalty (charged to the acceptor AT EXPIRY,
    before any dispute could exist) is NEVER reversed by any Tier-1
    outcome here -- canon's own settlement bullets (:398-400) never
    mention reversing it either, for any of the three cases. [NO-CANON]
    flagged, not invented; proposed to DECISIONS.md alongside the escrow
    reconciliation above. FLUSH-ONLY."""
    now = now or _now()
    contract = _load_contract(db, contract_id)
    if contract.acceptor_player_id != acceptor_player_id:
        raise ContractError("This contract is not accepted by you")
    if contract.status != ContractStatus.EXPIRED:
        raise ContractConflictError(
            f"stale_status: contract {contract.id} is '{contract.status.value}', not 'expired' "
            "-- only a failed (expired) contract can be disputed"
        )
    failure_timestamp = contract.deadline
    if failure_timestamp is None or now - failure_timestamp > timedelta(hours=DISPUTE_FILING_WINDOW_HOURS):
        raise ContractError(
            f"dispute_window_closed: disputes must be filed within {DISPUTE_FILING_WINDOW_HOURS} "
            "hours of the failure timestamp"
        )

    if contract.issuer_type == ContractIssuerType.PLAYER:
        acceptor, issuer = _load_two_players_for_update(db, acceptor_player_id, contract.issuer_id)
    else:
        acceptor = _load_player(db, acceptor_player_id, for_update=True)
        issuer = None

    # [NO-CANON] `evidence_snapshot` (contracts.md:295's request shape) has
    # no dedicated column -- `dispute_notes` (Text, free-form) is the only
    # persistence target this schema offers for ANY dispute-filing text.
    # Folded in rather than silently dropped.
    notes = reason if not evidence_snapshot else f"{reason}\n\nEvidence: {evidence_snapshot}"
    _guarded_transition(
        db, contract, ContractStatus.EXPIRED, ContractStatus.DISPUTED,
        escrow_state=ContractEscrowState.DISPUTED, dispute_filed_at=now, dispute_notes=notes,
    )

    resolution: Optional[str] = None
    payout = 0
    insurance_refund = 0

    if _tier1_cargo_manifest_match(contract):
        resolution = "cargo_manifest_match"
        # WO-CONTRACT-2-DISPUTE-T1-REVISE (cipher MEDIUM, fixed now even
        # though this seam is a documented no-op today -- see
        # _bounded_transfer's own docstring): bounded, never a mint.
        nominal_payout = _to_credits_int(_round_credits(_as_decimal(contract.payment)))
        payout = _bounded_transfer(issuer, acceptor, nominal_payout)
        if issuer is not None:
            contract.escrow_state = ContractEscrowState.RELEASED
        _guarded_transition(db, contract, ContractStatus.DISPUTED, ContractStatus.COMPLETED, completed_at=now)
    elif _tier1_destination_unreachable(db, contract):
        resolution = "destination_unreachable"
        # Self-refund of the acceptor's OWN accept-time fee -- never
        # issuer-funded, no bound needed (confirmed clear, unchanged).
        payout = _to_credits_int(
            _round_credits(_as_decimal(contract.payment) * _as_decimal(contract.acceptance_fee_pct) / Decimal(100))
        )
        acceptor.credits = (acceptor.credits or 0) + payout
        _guarded_transition(db, contract, ContractStatus.DISPUTED, ContractStatus.CANCELLED)
        insurance_refund = _apply_dispute_insurance_refund(contract, acceptor, now)
    elif _tier1_issuer_unilateral_cancellation(contract):
        resolution = "issuer_cancellation"
        # WO-CONTRACT-2-DISPUTE-T1-REVISE (mack LOW, fixed now even
        # though this seam is unreachable in production today): bounded,
        # never a mint -- same fix as cargo_manifest_match above.
        accept_fee_equivalent = _as_decimal(contract.payment) * _as_decimal(contract.acceptance_fee_pct) / Decimal(100)
        cancel_fee = _as_decimal(contract.payment) * PLAYER_POST_CANCEL_FEE_PCT_POST_ACCEPT / Decimal(100)
        nominal_payout = _to_credits_int(_round_credits(accept_fee_equivalent + cancel_fee))
        payout = _bounded_transfer(issuer, acceptor, nominal_payout)
        _guarded_transition(db, contract, ContractStatus.DISPUTED, ContractStatus.CANCELLED)
        insurance_refund = _apply_dispute_insurance_refund(contract, acceptor, now)
    else:
        contract.escalated_to_admin = (
            _ei3_both_parties_dispute(contract)
            or _ei3_evidence_trail_incomplete(db, contract)
            or _ei3_high_value(contract)
        )
        # status stays DISPUTED, escrow stays DISPUTED (frozen) -- lands
        # in the Tier-2 queue for resolve_dispute.

    db.flush()

    logger.info(
        "Player %s filed dispute on contract %s (tier1 resolution: %s, escalated: %s)",
        acceptor_player_id, contract.id, resolution or "unresolved",
        getattr(contract, "escalated_to_admin", False),
    )
    return {
        "id": str(contract.id),
        "status": contract.status.value,
        "dispute_filed_at": now,
        "tier1_resolution": resolution,
        "escalated_to_admin": bool(getattr(contract, "escalated_to_admin", False)),
        "payout": payout,
        "insurance_refund": insurance_refund,
        "credits": acceptor.credits,
    }


def _plan_dispute_outcome(
    outcome: ContractDisputeResolution, payment: Decimal, acceptance_fee: Decimal, now: datetime,
) -> tuple[int, int, ContractStatus, Dict[str, Any]]:
    """The 5-outcome Settlement column (contracts.md:410-414) -- PURE, NO
    CREDIT MUTATION (WO-CONTRACT-2-DISPUTE-T1-REVISE, mack HIGH: split out
    of the credit-touching step specifically so `resolve_dispute` can run
    its guarded transition BEFORE touching any credits -- see that
    function's own docstring). Also keeps `resolve_dispute`'s own
    cyclomatic complexity under this codebase's ruff C901 threshold, same
    as before -- same math, same branches, just named and now mutation-
    free. See `resolve_dispute`'s own docstring for the full per-outcome
    canon citation and NO-CANON reasoning (escrow reconciliation, the
    PARTIAL_PAYOUT delivered=0 pin, PENALTY's no-op, SPLIT's status pin)
    -- not repeated here.

    Returns `(issuer_funded_nominal, acceptor_only_amount, target_status,
    extra_guarded_transition_column_updates)`:
      - `issuer_funded_nominal`: the REQUESTED amount the caller should
        run through `_bounded_transfer` AFTER its guarded transition
        succeeds (0 if this outcome moves nothing issuer-side).
      - `acceptor_only_amount`: an UNCONDITIONAL acceptor credit that
        never draws from the issuer at all (the acceptance-fee refund --
        money the acceptor already paid at accept() time and the issuer
        never held, same "self-refund" shape as `file_dispute`'s own
        destination_unreachable case) -- applied directly, no bound
        needed, 0 if this outcome refunds no fee."""
    if outcome == ContractDisputeResolution.FULL_PAYOUT:
        return _to_credits_int(_round_credits(payment)), 0, ContractStatus.COMPLETED, {"completed_at": now}

    if outcome == ContractDisputeResolution.PARTIAL_PAYOUT:
        return 0, 0, ContractStatus.CANCELLED, {}

    if outcome == ContractDisputeResolution.REFUND:
        return 0, _to_credits_int(acceptance_fee), ContractStatus.CANCELLED, {}

    if outcome == ContractDisputeResolution.PENALTY:
        return 0, 0, ContractStatus.CANCELLED, {}

    if outcome == ContractDisputeResolution.SPLIT:
        half_payment = _to_credits_int(_round_credits(payment / Decimal(2)))
        fee_refund = _to_credits_int(acceptance_fee)
        return half_payment, fee_refund, ContractStatus.CANCELLED, {}

    raise ContractError(  # pragma: no cover -- resolve_dispute's isinstance check already excludes this
        f"unknown_outcome: '{outcome}' is not a valid dispute resolution"
    )


def resolve_dispute(
    db: Session, contract_id: uuid.UUID, admin_id: uuid.UUID,
    outcome: ContractDisputeResolution, notes: Optional[str] = None, now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Tier-2 admin ruling (contracts.md:404-416) -- FUNCTION ONLY per
    this WO's scope; the `POST /contracts/{id}/resolve-dispute` admin
    route (and `admin_id`'s auth/scope check) is impl-admin-ui's lane,
    not mounted here. `admin_id` is accepted and logged but not
    otherwise validated by this function -- the caller's route is
    responsible for confirming `admin_id` actually holds the resolving
    scope before calling this.

    Only callable on a `DISPUTED` contract (i.e. one Tier-1 could not
    resolve, or a fresh SECOND `resolve_dispute` call on an already-
    resolved one -- both rejected via the SAME guarded-UPDATE idiom
    every transition in this domain uses: the WHERE clause requires
    `status == 'disputed'`, so a second ruling attempt races and loses
    exactly like a double-file or double-claim would).

    WO-CONTRACT-2-DISPUTE-T1-REVISE (mack HIGH): GUARD-FIRST ordering.
    `_guarded_transition` runs BEFORE any credit is touched -- matching
    every sibling transition in this domain (`accept`, `insure`,
    `file_dispute`'s own two Tier-1 guards) and, critically, making this
    function SELF-safe against a concurrent double-resolve without
    depending on the calling route's rollback discipline: the Tier-2
    admin route this function serves is impl-admin-ui's, NOT YET BUILT,
    and this function cannot assume it will `db.rollback()` on
    `ContractError` the same careful way this repo's OWN routes do. A
    race loser's guarded UPDATE matches zero rows and raises BEFORE
    `_plan_dispute_outcome`'s numbers are ever applied to a single
    credits field -- worst case on a misbehaving caller is a raised
    exception with ZERO mutation, never a half-applied settlement sitting
    in the session waiting to be accidentally flushed later.

    See `file_dispute`'s own docstring for the [NO-CANON, MAJOR] escrow-
    reconciliation note -- it applies IDENTICALLY here: every "Escrow ->
    X" settlement below is a FRESH credit movement of `contract.payment`
    (or a fraction of it) via `_bounded_transfer` (mack CRITICAL -- see
    that helper's own docstring: NEVER a mint, the acceptor collects only
    what the issuer's balance can actually cover), not a manipulation of
    the already-emptied `escrow_amount`/`escrow_state` ledger. Reputation/
    cooldown columns in canon's own table are NOT applied (see this
    module's own header comment) -- only the Settlement column is built.
    Insurance premium: see `_apply_dispute_insurance_refund`'s own
    docstring -- applied for every CANCELLED outcome, always evaluates to
    0 today, wired in anyway.

    Five outcomes (contracts.md:410-414):
      - FULL_PAYOUT: acceptor gets the full `payment`, bounded-transferred
        from the issuer if player-issued (NPC-issued: minted, matching
        `complete()`'s own NPC precedent, contract_service.py). ->
        COMPLETED.
      - PARTIAL_PAYOUT: `(delivered / expected) x payment` to acceptor,
        remainder to issuer. [NO-CANON] no real "units delivered" signal
        exists for a cargo_delivery dispute -- `partial_fulfilled_amount`
        is a bulk_procurement-only field cargo_delivery never sets (and
        bulk_procurement's own partial-fulfillment mechanic is itself
        still schema-only per contracts.md:439). Pinned at `delivered =
        0` (the conservative floor: nothing provably delivered) until a
        real per-contract delivery signal exists. [CORRECTED, WO-REVISE
        LOW (a)] this does NOT net the same as REFUND -- REFUND still
        credits the acceptor their acceptance fee back (canon's own
        table names it explicitly for REFUND); PARTIAL_PAYOUT's own
        canon bullet never mentions a fee refund at all, and at
        delivered=0 the acceptor collects NOTHING here, not even the
        fee -- a genuinely HARSHER outcome than REFUND, not an
        equivalent one. Proposed to DECISIONS.md. -> CANCELLED.
      - REFUND (acceptor non-negligent): acceptance fee back to the
        acceptor (issuer already holds the rest via the earlier sweep
        refund -- no further issuer-side movement needed, this is an
        acceptor-only self-refund same as `file_dispute`'s destination_
        unreachable case, never issuer-funded). -> CANCELLED.
      - PENALTY (acceptor fault/fabrication): acceptance fee forfeit --
        it was ALREADY sunk at accept() time and is never refunded by
        ANY path in this domain (contracts.md's own Penalties section:
        "acceptance fee is not refunded"), so this outcome is a pure
        no-credit-movement close-out: the dispute is formally resolved
        confirming the original failure stands, nothing changes hands
        beyond that. -> CANCELLED.
      - SPLIT (shared responsibility): HALF of `payment` bounded-
        transferred from the issuer if player-issued, PLUS the
        acceptance fee refunded in full (acceptor-only, same as REFUND).
        [NO-CANON] status mapping for SPLIT isn't literal in canon's
        table (unlike the other four, which read naturally as COMPLETED/
        CANCELLED) -- pinned to CANCELLED (not a clean completion)
        rather than COMPLETED, proposed to DECISIONS.md. -> CANCELLED.

    FLUSH-ONLY."""
    now = now or _now()
    contract = _load_contract(db, contract_id)
    if contract.status != ContractStatus.DISPUTED:
        raise ContractConflictError(
            f"stale_status: contract {contract.id} is '{contract.status.value}', not 'disputed'"
        )
    if not isinstance(outcome, ContractDisputeResolution):
        raise ContractError(f"unknown_outcome: '{outcome}' is not a valid dispute resolution")

    if contract.issuer_type == ContractIssuerType.PLAYER:
        acceptor, issuer = _load_two_players_for_update(db, contract.acceptor_player_id, contract.issuer_id)
    else:
        acceptor = _load_player(db, contract.acceptor_player_id, for_update=True)
        issuer = None

    payment = _as_decimal(contract.payment)
    acceptance_fee = _round_credits(payment * _as_decimal(contract.acceptance_fee_pct) / Decimal(100))

    # PURE planning step -- computes WHAT would move and the target
    # status, touches ZERO credits (mack HIGH fix).
    issuer_funded_nominal, acceptor_only_amount, target_status, extra_updates = _plan_dispute_outcome(
        outcome, payment, acceptance_fee, now,
    )

    # Guard FIRST: a concurrent double-resolve (or a raced Tier-1
    # resolution that somehow already moved this contract off DISPUTED)
    # is rejected HERE, before any credit mutation below ever runs.
    _guarded_transition(
        db, contract, ContractStatus.DISPUTED, target_status,
        dispute_resolution=outcome, dispute_resolved_at=now,
        dispute_notes=notes if notes is not None else contract.dispute_notes,
        escalated_to_admin=False,
        **extra_updates,
    )

    # Only now, with the guard already won, touch credits.
    debited = _bounded_transfer(issuer, acceptor, issuer_funded_nominal) if issuer_funded_nominal > 0 else 0
    if acceptor_only_amount > 0:
        acceptor.credits = (acceptor.credits or 0) + acceptor_only_amount
    amount = debited + acceptor_only_amount

    insurance_refund = 0
    if target_status == ContractStatus.CANCELLED:
        insurance_refund = _apply_dispute_insurance_refund(contract, acceptor, now)

    db.flush()

    logger.info(
        "Admin %s resolved dispute on contract %s (%s, %d credits to acceptor)",
        admin_id, contract.id, outcome.value, amount,
    )
    return {
        "id": str(contract.id),
        "status": contract.status.value,
        "dispute_resolution": outcome.value,
        "dispute_resolved_at": now,
        "amount_to_acceptor": amount,
        "insurance_refund": insurance_refund,
        "credits": acceptor.credits,
    }

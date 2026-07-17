"""
Trade Contract lifecycle -- WO-ECON-CONTRACT-1-KERNEL lane 2. Only the
`posted -> accepted -> completed` / `abandon` / `expire` transitions on
`cargo_delivery` are exercised here (the single-acceptor, no-load-step
happy path this WO proves end-to-end per contracts.md:421-431 step 2).
Player-issued posting/escrow (WO-ECON-CONTRACT-2-PLAYER-ESCROW) and the
three-tier insurance engine -- `insure()`, plus the E-I2 mid-term-
cancellation refund wired into `abandon()` / `cancel_player_contract()`
(WO-1a-CORE) -- are now live. Insurance CLAIM handling (paying out on a
ship destroyed in transit) was built and then excised in the same round
-- cipher's gate found the claim's self-reported "my ship is gone" check
a farmable money-mint with no real destruction-event verification behind
it; that half is deferred to a dedicated, design-gated WO-1b-CLAIM-SAFETY.
The `insurance_claim_filed` column stays on the schema (harmless, always
false today) for whenever that WO lands. Dispute filing + Tier-1
automated arbitration (`file_dispute`) and the Tier-2 admin ruling
interface (`resolve_dispute` -- FUNCTION ONLY, the admin route is
impl-admin-ui's lane) are now live too (WO-CONTRACT-2-DISPUTE-T1) -- see
that pair's own docstrings, and this module's dispute-section header
comment, for the significant NO-CANON pins involved (no delivery-event
log exists for cargo-manifest verification; the expiry sweep already
releases escrow before any dispute can be filed, reconciled as a fresh
credit movement rather than re-touching the emptied escrow ledger;
reputation/cooldowns are unbuilt anywhere in this codebase). Bulk-
procurement partial fulfillment remains a later build step this module
never touches.

FILE SIZE: this module is now well past this project's 1500-line Python
guideline (grew from three successive WOs building directly into it, per
explicit direction each time -- "same file, your context carries"). Not
refactored here; flagged for a future dedicated split WO. A clean split
is non-trivial: the escrow/lock helpers (`_load_player`, `_load_two_
players_for_update`, `_guarded_transition`, `_round_credits`, etc.) are
ALL private (underscore-prefixed) and shared by every transition in this
file, including disputes -- contract_generator.py (this domain's one
existing sibling-file precedent) is a clean split only because it never
needs any of them.

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
from datetime import UTC, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Callable, Dict, FrozenSet, List, Optional

from sqlalchemy import update
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from src.models.contract import (
    Contract,
    ContractDisputeResolution,
    ContractEscrowState,
    ContractInsuranceCoverageTier,
    ContractIssuerType,
    ContractStatus,
    ContractType,
)
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
PLAYER_POST_CANCEL_FEE_PCT_POST_ACCEPT = Decimal("10.0")

# contracts.md:245 -- concrete canon cap.
MAX_ACTIVE_PLAYER_POSTINGS_PER_REGION = 10

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

# --- WO-CONTRACT-2-DISPUTE-T1: dispute constants ----------------------------

# contracts.md:390 -- "within 48 game-hours of the failure timestamp".
# [NO-CANON] game-hours vs wall-hours: this module NEVER imports
# `src.core.game_time` (GAME_TIME_SCALE) anywhere -- verified by grep.
# EVERY existing timed check in this file (accept()'s deadline check,
# cancel_player_contract's past-deadline guard, both expiry sweeps) uses
# raw `datetime`/`timedelta` wall-clock arithmetic against `contract.
# deadline`, which is itself GENERATED as a wall-clock window by
# contract_generator.py -- that generator's own pick_deadline_hours()
# docstring explicitly rejects GAME_TIME_SCALE for contracts: "matching
# this codebase's dominant wall-clock-storage convention for timed state
# ... rather than introducing a new GAME_TIME_SCALE-adjusted deadline
# surface UNIQUE TO CONTRACTS." Dispute windows measure from that SAME
# wall-clock `deadline` field (see FAILURE TIMESTAMP below) -- treating
# THIS one new window as GAME_TIME_SCALE-scaled while every sibling
# contract-deadline check stays wall-clock would make the SAME contract
# row mix two incompatible time domains. Pinned as literal wall-clock
# hours, matching every other timed check in this module; proposed to
# DECISIONS.md.
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


# --- WO-CONTRACT-2-DISPUTE-T1: Tier-1 case checks + E-I3 criteria ----------
#
# Reputation columns everywhere in this section (reward/penalty/forgive/
# reverse language in canon's own tables) are DELIBERATELY NOT applied
# anywhere below. contracts.md's own Reputation Effects section (verified
# again for this WO): "reputation_reward and reputation_penalty are
# written on the contract row at posting time but are never READ by
# complete() or abandon() -- design-only." Grepped this module for any
# existing reputation code: zero hits. There is nothing anywhere in this
# codebase for a dispute resolution to pause, apply, forgive, or reverse
# -- building a reputation side effect here would be inventing the FIRST
# consumer of a system nothing else wires either. `_is_reputation_
# penalty_paused` below is the one exception: a real, correct, testable
# GATE a future reputation-application pass would consult, built now so
# it exists and is provably wired, exactly like `_is_player_blocklisted`'s
# established no-op-seam precedent -- not a false claim that reputation
# is applied end-to-end today.
#
# Cooldowns ("24h cooldown on that issuer", "72h cooldown; account flag
# on repeat") are the SAME situation -- contracts.md's own Reputation
# Effects section already flags "bans the player... for a cooldown" as
# design-only elsewhere in this doc; no cooldown/ban model exists
# anywhere in this codebase (grepped). Not invented here either.

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
    docstring). (2) no historical station-status log exists -- this
    checks the station's CURRENT status, not a snapshot at the failure
    moment. Reasonable proxy given Tier-1 resolution runs synchronously
    at filing time, itself gated within 48 game-hours of the failure --
    an outage severe enough to strand a delivery typically persists
    across that window, and this errs toward the acceptor (no false
    negative from a station that's STILL down when the dispute is
    filed). The ONE genuinely resolvable Tier-1 case in this codebase's
    current shipped reality -- see this module's own dispute-section
    header comment for why the other two are documented no-op seams."""
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


def _bounded_transfer(issuer: Optional[Any], acceptor: Any, amount: int) -> int:
    """WO-CONTRACT-2-DISPUTE-T1-REVISE (mack CRITICAL): every issuer-
    funded dispute settlement MUST be a conservation-safe BOUNDED
    TRANSFER, never a mint. The bug this closes: crediting the acceptor
    the FULL nominal `amount` while debiting the issuer `max(0, credits -
    amount)` (clamped) manufactures credits from nothing whenever the
    issuer's balance is less than `amount` -- an entirely ORDINARY,
    non-adversarial sequence (issuer's escrow was refunded in full at
    expiry by the sweep, they spend it on something else, THEN a dispute
    resolves against them) mints the full payment out of thin air with
    zero attacker required. Fixed: the acceptor collects ONLY what the
    issuer actually has right now (`min(issuer.credits, amount)`) --
    never more. If the issuer is broke, the acceptor's payout is
    genuinely, silently reduced; this codebase has no debt-ledger model
    (same NO-CANON precedent `abandon()`'s own credit debits already
    established) to guarantee the nominal amount some other way. The
    alternative -- a debt ledger, or holding escrow through the entire
    dispute window so a guaranteed payout is always available -- is a
    bigger architectural change flagged for Max's call, not built here.

    For an NPC-issued contract (`issuer is None`), NPC credits are
    canonically infinite (contracts.md:155, matching `complete()`'s own
    established mint-for-NPC precedent) -- the full `amount` mints, no
    bound needed; there is no real wallet to overdraw.

    Returns the ACTUAL amount transferred (<= `amount`) -- callers MUST
    treat this, never the requested `amount`, as the true payout."""
    if issuer is None:
        acceptor.credits = (acceptor.credits or 0) + amount
        return amount
    debited = min(issuer.credits or 0, amount)
    issuer.credits = (issuer.credits or 0) - debited
    acceptor.credits = (acceptor.credits or 0) + debited
    return debited


def _apply_dispute_insurance_refund(contract: Any, acceptor: Any, now: datetime) -> int:
    """WO-CONTRACT-2-DISPUTE-T1-REVISE (mack LOW): `insure()` only
    requires `status == ACCEPTED` -- a contract can carry an insurance
    tier (and a paid premium) all the way into `EXPIRED` (neither expiry
    sweep clears `insurance_coverage_tier`/`insurance_premium_paid`), yet
    every dispute-driven CANCELLED outcome left the premium unaddressed
    entirely. Reuses the EXACT SAME `_compute_insurance_cancellation_
    refund` idiom `abandon()`/`cancel_player_contract()` already use for
    their own "mid-term cancellation" (ADR-0062 E-I2) -- called ONLY for
    a CANCELLED outcome, never COMPLETED (contracts.md:62's "on
    completion: released to insurer, not refunded" -- the SAME rule
    `complete()` already honors by simply never touching this column;
    callers of this helper must gate on `target_status == CANCELLED`
    themselves, matching that precedent rather than re-deriving it here).

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
    # WO-CONTRACT-1-INSURANCE: deliberately NOT touched here. contracts.md:62
    # -- "On completion: released to insurer (not refunded)" -- is satisfied
    # by simply never reading/crediting back `insurance_premium_paid`
    # anywhere in this function; it was already debited from the acceptor
    # at insure() time and stays gone.
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

    # WO-1a-CORE (mack CRITICAL #2): refresh AFTER the lock, BEFORE the
    # insurance_coverage_tier check below -- see _refresh_contract_
    # insurance_snapshot's own docstring for the exploit this closes.
    _refresh_contract_insurance_snapshot(db, contract)

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

    # WO-CONTRACT-1-INSURANCE (ADR-0062 E-I2): a walk-away is a "mid-term
    # cancellation" of the ACCEPTED contract -- if the acceptor (== `player`
    # here, already locked above regardless of issuer_type) holds a
    # coverage tier, their premium refunds pro-rata, minus the 10%
    # cancellation fee, in this SAME transaction. Orthogonal to the
    # issuer-escrow branch above (NPC-issued rows can carry insurance too --
    # this is never gated on `issuer is not None`).
    insurance_refund = 0
    if contract.insurance_coverage_tier is not None:
        insurance_refund = _to_credits_int(_compute_insurance_cancellation_refund(contract, now))
        if insurance_refund > 0:
            player.credits = (player.credits or 0) + insurance_refund
    db.flush()

    logger.info(
        "Player %s abandoned contract %s (penalty %d, insurance refund %d)",
        player_id, contract.id, penalty, insurance_refund,
    )
    return {
        "id": str(contract.id),
        "status": contract.status.value,
        "penalty_charged": penalty,
        "insurance_refund": insurance_refund,
        "credits": player.credits,
    }


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
    unilateral-cancel path is withdrawn.

    WO-1a-CORE (ADR-0062 E-I2): the `accepted` branch is ALSO a
    "mid-term cancellation" for insurance purposes -- if the acceptor
    holds a coverage tier, their premium refunds pro-rata (minus the 10%
    cancellation fee) in this SAME transaction, credited to the ACCEPTOR
    (the policyholder), never the issuer. This is why the acceptor is
    dual-locked alongside the issuer below -- the dual-lock decision is
    made up front from the just-loaded (still UNLOCKED) `contract` row,
    before any lock is acquired -- the same up-front-decision shape
    `abandon()` uses for its own conditional dual-lock, and for the
    identical deadlock-safety reason (a consistent ascending-id
    acquisition order across every concurrent caller that might touch
    the same two players).

    mack CRITICAL #1 (fixed): the dual-lock decision used to ALSO gate on
    `contract.insurance_coverage_tier is not None` -- a column a
    concurrent `insure()` call can change. A racing insure() that
    committed between this function's unlocked `_load_contract` read and
    this decision left the acceptor never locked at all, so the refund
    branch further down silently never ran even though a real premium
    had just been paid -- zero contention required, not a rare edge.
    Fixed: the lock decision now gates ONLY on `status == accepted and
    acceptor_player_id is not None` (both stable, safe to read pre-lock
    -- `acceptor_player_id` is set once at accept() time and never
    changes again). The Contract row's insurance columns are then
    refreshed via `_refresh_contract_insurance_snapshot` AFTER the lock is
    acquired -- see that helper's own docstring. FLUSH-ONLY."""
    now = now or _now()
    contract = _load_contract(db, contract_id)
    if contract.issuer_type != ContractIssuerType.PLAYER or contract.issuer_id != issuer_player_id:
        raise ContractError("This contract was not posted by you")

    needs_acceptor_lock = (
        contract.status == ContractStatus.ACCEPTED
        and contract.acceptor_player_id is not None
    )
    if needs_acceptor_lock:
        issuer, acceptor = _load_two_players_for_update(db, issuer_player_id, contract.acceptor_player_id)
        # WO-1a-CORE (mack CRITICAL #1): refresh AFTER the lock -- see
        # _refresh_contract_insurance_snapshot's own docstring.
        _refresh_contract_insurance_snapshot(db, contract)
    else:
        issuer = _load_player(db, issuer_player_id, for_update=True)
        acceptor = None

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

    insurance_refund = 0
    if acceptor is not None:
        # acceptor is locked (and contract's insurance columns freshly
        # refreshed) whenever there's an ACCEPTED contract with an
        # acceptor -- regardless of whether a tier turns out to be held.
        # _compute_insurance_cancellation_refund itself already returns 0
        # when no premium was ever paid, so this is safe to call
        # unconditionally rather than re-checking insurance_coverage_tier
        # here too.
        insurance_refund = _to_credits_int(_compute_insurance_cancellation_refund(contract, now))
        if insurance_refund > 0:
            acceptor.credits = (acceptor.credits or 0) + insurance_refund
    db.flush()

    logger.info(
        "Player %s cancelled contract %s (refund %s, insurance refund %d)",
        issuer_player_id, contract.id, refund, insurance_refund,
    )
    return {
        "id": str(contract.id),
        "status": contract.status.value,
        "refund": float(refund),
        "insurance_refund": insurance_refund,
        "credits": issuer.credits,
    }


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
    idiom every other transition in this module uses, so a double-file
    attempt is rejected the identical "race loser touches nothing" way
    (WHERE status='expired' matches zero rows once the first filing
    lands) -- no separate `dispute_filed_at IS NULL` pre-check needed or
    safe (same reasoning `_guarded_insure`'s own docstring gives for its
    sibling column-claim guard).

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
    econ-accepted-deadline-expiry, unrelated to and unchanged by this WO)
    ALREADY refunds a PLAYER-issuer's escrow to them, in the SAME
    transaction that flips status to EXPIRED -- by the time ANY dispute
    could be filed, `escrow_state` is already `refunding` and the issuer
    already has their money back. contracts.md's dispute-resolution
    prose ("escrow frozen", "Escrow -> acceptor in full") reads as if
    escrow is STILL held at dispute time, which is TRUE for the ACCEPTED-
    contract disputes canon's prose seems to picture, but this codebase
    only reaches EXPIRED (and therefore only reaches a dispute) via a
    path that already released it. Reconciled here (and in resolve_
    dispute below) by treating every "escrow -> X" settlement as a FRESH
    credit movement of `contract.payment` between issuer and acceptor,
    computed from the frozen `payment`/`acceptance_fee_pct` columns
    (deterministic, same values `accept()` itself used) rather than
    manipulating the (already-emptied) `escrow_amount`/`escrow_state`
    ledger a second time -- achieving the same NET settlement canon
    specifies without double-counting or fabricating funds.

    WO-CONTRACT-2-DISPUTE-T1-REVISE (mack CRITICAL): every issuer-funded
    movement below is a BOUNDED transfer via `_bounded_transfer` (see its
    own docstring), NEVER a clamped-debit-plus-unconditional-credit --
    the acceptor collects ONLY what the issuer's balance can actually
    cover, never the full nominal amount regardless of it. An issuer who
    has since spent their refunded escrow does not go negative, AND does
    not cause credits to be minted from nothing either. Flagged
    prominently here rather than silently picked; proposed to
    DECISIONS.md as a real architectural gap between canon's dispute-
    resolution model and this codebase's already-shipped, already-gated
    expiry-sweep behavior (which this WO does not touch or reopen).

    Reputation pause/reward/forgive/reverse and cooldowns: see this
    module's own dispute-section header comment -- nothing is applied,
    nothing exists yet to apply it to; `_is_reputation_penalty_paused`
    is the one real, tested gate built for a future consumer.

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
    every transition in this module uses: the WHERE clause requires
    `status == 'disputed'`, so a second ruling attempt races and loses
    exactly like a double-file or double-claim would).

    WO-CONTRACT-2-DISPUTE-T1-REVISE (mack HIGH): GUARD-FIRST ordering.
    `_guarded_transition` runs BEFORE any credit is touched -- matching
    every sibling transition in this module (`accept`, `insure`,
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
    module's dispute-section header comment) -- only the Settlement
    column is built. Insurance premium: see `_apply_dispute_insurance_
    refund`'s own docstring -- applied for every CANCELLED outcome,
    always evaluates to 0 today, wired in anyway.

    Five outcomes (contracts.md:410-414):
      - FULL_PAYOUT: acceptor gets the full `payment`, bounded-transferred
        from the issuer if player-issued (NPC-issued: minted, matching
        `complete()`'s own NPC precedent). -> COMPLETED.
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
        ANY path in this module (contracts.md's own Penalties section:
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

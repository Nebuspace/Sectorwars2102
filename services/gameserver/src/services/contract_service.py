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

WO-CONTRACT-REFACTOR-SPLIT: this module was split (pure move, zero
behavior change) once it grew past this project's 1500-line Python
guideline across three successive WOs building directly into it. The
shared escrow/lock PRIMITIVES (`_load_player`, `_load_contract`,
`_guarded_transition`, `_load_two_players_for_update`, `_bounded_
transfer`, `LEGAL_TRANSITIONS`, the Decimal/credits rounding helpers,
the exception hierarchy, and the player-posting/kill-fee constants) now
live in `contract_escrow_core.py`; the three-tier insurance engine
(`insure`, the E-I2 mid-term-cancellation refund helper, the insurance-
tier constants) now lives in `contract_insurance.py`; dispute filing +
Tier-1/Tier-2 resolution (`file_dispute`, `resolve_dispute`, the Tier-1/
E-I3 case-check seams, the dispute constants) now lives in `contract_
dispute.py`. THIS module keeps the LIFECYCLE functions (`accept`,
`complete`, `abandon`, `post_player_contract`, `cancel_player_contract`,
both expiry sweeps) plus the posting-validation helpers only `post_
player_contract` needs (`_is_valid_commodity`, `_is_player_blocklisted`,
`_active_player_postings_in_region`) -- and RE-EXPORTS every name an
existing external caller (routes/contracts.py, storage_service.py, the
scheduler, and the test suite) already reaches via `contract_service.
<name>`, so every one of those call sites keeps working UNCHANGED. See
each sibling module's own docstring for the full dependency layering
(core has no internal dependencies; insurance and dispute both depend on
core; dispute also depends on insurance's refund helper; this module
depends on all three).

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
from datetime import datetime
from decimal import Decimal
from typing import Any, Callable, Dict, List, Optional

from sqlalchemy import update
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from src.models.contract import (
    Contract,
    ContractEscrowState,
    ContractIssuerType,
    ContractStatus,
    ContractType,
)
from src.models.resource import Resource
from src.models.station import Station, StationStatus
from src.services.contract_dispute import _ei3_both_parties_dispute as _ei3_both_parties_dispute
from src.services.contract_dispute import _ei3_evidence_trail_incomplete as _ei3_evidence_trail_incomplete
from src.services.contract_dispute import _ei3_high_value as _ei3_high_value
from src.services.contract_dispute import _is_reputation_penalty_paused as _is_reputation_penalty_paused
from src.services.contract_dispute import _tier1_cargo_manifest_match as _tier1_cargo_manifest_match
from src.services.contract_dispute import _tier1_destination_unreachable as _tier1_destination_unreachable
from src.services.contract_dispute import (
    _tier1_issuer_unilateral_cancellation as _tier1_issuer_unilateral_cancellation,
)
from src.services.contract_dispute import file_dispute as file_dispute
from src.services.contract_dispute import resolve_dispute as resolve_dispute
from src.services.contract_escrow_core import LEGAL_TRANSITIONS as LEGAL_TRANSITIONS

# --- WO-CONTRACT-REFACTOR-SPLIT: re-exports for backward compatibility ----
# Every name below is either used directly by this module's own lifecycle
# functions (a real dependency, not just a shim) or re-exported PURELY so
# `contract_service.<name>` keeps resolving for existing external callers
# (routes/contracts.py, storage_service.py, the scheduler, the test suite)
# that reach into this module by name -- see this module's own docstring.
# Names that are NEVER referenced by this file's own code are imported
# with an explicit `as <same name>` self-alias (the same "intentional
# re-export" idiom this codebase already establishes for renamed lifts --
# see shared-constant-lift-backcompat-reexport in monk's own memory notes)
# so ruff's F401 doesn't flag them as unused.
from src.services.contract_escrow_core import (
    MAX_ACTIVE_PLAYER_POSTINGS_PER_REGION,
    PLAYER_POST_CANCEL_FEE_PCT_POST_ACCEPT,
    PLAYER_POST_CANCEL_REFUND_PCT_PRE_ACCEPT,
    PLAYER_POST_MIN_DEADLINE_HOURS,
    ContractConflictError,
    ContractError,
    _as_decimal,
    _guarded_transition,
    _load_contract,
    _load_player,
    _load_two_players_for_update,
    _now,
    _round_credits,
    _to_credits_int,
)
from src.services.contract_escrow_core import ContractNotFoundError as ContractNotFoundError
from src.services.contract_insurance import _compute_insurance_cancellation_refund, _refresh_contract_insurance_snapshot
from src.services.contract_insurance import apply_claim_offset as apply_claim_offset
from src.services.contract_insurance import insure as insure

logger = logging.getLogger(__name__)


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
    concept -- see the LEGAL_TRANSITIONS comment (contract_escrow_core.py).

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
    `contract.penalty` (clamped to 0), the EXACT idiom `abandon()` already
    uses for the player-initiated failure case (this sweep is the
    deadline-initiated twin of that same failure) -- UNLESS an insurance
    tier is held, in which case WO-CONTRACT-1b-CLAIM-SAFETY's `apply_
    claim_offset` (contract_insurance.py) reduces the charge per the
    deductible ladder, bounded by `insurance_pool_reserve`. This is
    deliberately the ONLY failure-event call site wired to the offset --
    `abandon()`'s penalty is explicitly, canonically UNCOVERED
    (contracts.md's Risk & Insurance section: "Insurance does not cover
    wilful abandonment") and stays unchanged; a deadline lapse reached via
    THIS sweep is this codebase's only actual proxy for "cargo/ship lost
    in transit" (no separate ship-destruction-to-contract hook exists),
    matching the escrow table's "insured acceptor: insurer pays penalty"
    row for a deadline-lapse expiry.

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

    WO-CONTRACT-1b-CLAIM-SAFETY: this "full escrow_amount refund" reading
    (a) is now netted against `apply_claim_offset`'s `pool_draw` for this
    SAME contract, in this SAME nested transaction -- `escrow_amount`
    already includes whatever pool the issuer funded at post time
    (`payment + insurance_pool_reserve`), so refunding it in full while
    the pool just absorbed part of the acceptor's penalty would mint
    credits (see `apply_claim_offset`'s own docstring). Uninsured or
    zero-pool contracts see `pool_draw == 0` -- refund math is byte-
    identical to before this WO.

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

                # WO-CONTRACT-1b-CLAIM-SAFETY: `candidate` was gathered by
                # the upfront, UNLOCKED `.all()` above -- a concurrent
                # insure() can commit its coverage tier in the window
                # between that read and the player lock(s) just acquired,
                # the identical race `_refresh_contract_insurance_
                # snapshot`'s own docstring documents for abandon()/
                # cancel_player_contract(). MUST run AFTER the lock(s),
                # BEFORE `apply_claim_offset` reads insurance_coverage_
                # tier/insurance_pool_reserve below -- refreshes ALL of
                # `candidate`'s columns in place (including the pool).
                _refresh_contract_insurance_snapshot(db, candidate)

                penalty = _round_credits(_as_decimal(candidate.penalty))
                offset = apply_claim_offset(candidate, penalty)
                acceptor.credits = max(0, (acceptor.credits or 0) - _to_credits_int(offset["acceptor_debit"]))

                if issuer is not None:
                    # WO-CONTRACT-1b-CLAIM-SAFETY: the pool draw above is
                    # money this codebase already took from the issuer's
                    # wallet at post_player_contract() time and folded into
                    # escrow_amount -- refunding the FULL escrow_amount
                    # here while ALSO having just reduced the acceptor's
                    # penalty by that same pool_draw would mint credits
                    # (acceptor pays less, issuer loses nothing, net new
                    # money). Netting it out of the refund is what makes
                    # the offset a TRANSFER (issuer's reserve -> acceptor's
                    # reduced debit) rather than a mint -- see _compute_
                    # claim_offset's own docstring for the full reasoning.
                    refund = _to_credits_int(
                        _round_credits(_as_decimal(candidate.escrow_amount)) - offset["pool_draw"]
                    )
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

    WO-CONTRACT-1b-CLAIM-SAFETY: this parameter is now persisted onto its
    own `Contract.insurance_pool_reserve` column (previously folded into
    `escrow_amount` only, with the split lost immediately -- see that
    column's own docstring) -- it is the real, drawable balance `sweep_
    expired_accepted_contracts`'s claim-offset math consumes when an
    insured acceptor's contract fails. `contract_generator.py` (the only
    live generator -- NPC cargo_delivery) never sets this, so every
    NPC-issued contract's pool stays 0 and any insurance tier on it
    degrades to zero coverage (the acceptor still pays the full penalty)
    -- real coverage exists only for player-posted contracts whose issuer
    chose to fund a reserve. Not addressed here -- seeding a default NPC
    pool is out of this WO's scope.

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
        # WO-CONTRACT-1b-CLAIM-SAFETY: persisted separately from escrow_
        # amount now -- see Contract.insurance_pool_reserve's own column
        # docstring. Still folded into escrow_amount above (the issuer is
        # debited payment + pool at post time, unchanged); this is the
        # remaining balance a covered claim draws down from.
        insurance_pool_reserve=_round_credits(_as_decimal(insurance_pool_reserve)),
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

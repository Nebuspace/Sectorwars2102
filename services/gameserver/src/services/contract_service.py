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
procurement partial fulfillment (`deliver`, `walk_away_bulk_procurement`)
is now live too (WO-CONTRACT-3b-BULK) -- both now live in their own
sibling module, `contract_bulk.py` (see WO-CONTRACT-REFACTOR-SPLIT below
for why; this module re-exports both by name, unchanged for callers), see
that module's own docstring and `sweep_expired_accepted_contracts`'s own
"[KNOWN GAP]" note below for the one deliberately-unbuilt piece (an
in_progress-status deadline sweep). Nothing in this codebase yet
GENERATES or POSTS a bulk_procurement row (contract_generator.py's own
WO-CONTRACT-3-NPCGEN-TYPES build produced express_delivery/hazardous_
transport only; `post_player_contract` below still hardcodes
cargo_delivery) -- `deliver`/`walk_away_bulk_procurement` are built and
DB-free-tested against hand-constructed fixtures, function-only until a
future WO wires a real posting/generation path to them, matching this
module's own established `resolve_dispute`-style "function only"
precedent.

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
dispute.py`; bulk_procurement's own partial-fulfillment lifecycle
(`deliver`, `walk_away_bulk_procurement`) now lives in `contract_bulk.py`
(WO-CONTRACT-3b-BULK -- same growth-past-1500-lines trigger, same pure-
move remedy, applied the moment this build landed rather than in a
separate follow-up pass). THIS module keeps the LIFECYCLE functions
(`accept`, `complete`, `abandon`, `post_player_contract`, `cancel_
player_contract`, both expiry sweeps) plus the posting-validation
helpers only `post_player_contract` needs (`_is_valid_commodity`, `_is_
player_blocklisted`, `_active_player_postings_in_region`) -- and
RE-EXPORTS every name an existing external caller (routes/contracts.py,
storage_service.py, the scheduler, and the test suite) already reaches
via `contract_service.<name>`, so every one of those call sites keeps
working UNCHANGED. See each sibling module's own docstring for the full
dependency layering (core has no internal dependencies; insurance and
dispute both depend on core; dispute also depends on insurance's refund
helper; bulk also depends on insurance's refund helper, the SAME shape
dispute has; this module depends on all four).

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
from datetime import datetime, timedelta
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

# WO-CONTRACT-3-NPCGEN-TYPES: FactionType (below) + apply_faction_rep_delta
# (bottom of the services import block below) are consumed by complete()'s
# hazardous_transport completion-penalty branch ONLY -- see that function's
# own comment. Top-level import mirrors contraband_service.py's own
# established convention for this exact helper (no circular-import risk:
# faction_service.py has no contract_service/contract_dispute/contract_
# escrow_core dependency).
from src.models.faction import FactionType
from src.models.resource import Resource
from src.models.station import Station, StationStatus
from src.services.contract_bulk import deliver as deliver
from src.services.contract_bulk import walk_away_bulk_procurement as walk_away_bulk_procurement
from src.services.contract_dispute import DISPUTE_FILING_WINDOW_HOURS as DISPUTE_FILING_WINDOW_HOURS
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
from src.services.faction_service import apply_faction_rep_delta

logger = logging.getLogger(__name__)

# WO-CONTRACT-3-NPCGEN-TYPES: canon's own early-completion bonus formula
# (contracts.md:323 -- "up to +25% of payment if delivered with greater
# than 50% of the time window remaining. Linear scale between 0-25% above
# the 50% threshold.") is EXACT, not NO-CANON -- but canon marks it
# design-only for every contract type ("complete() pays the flat `payment`
# amount with no bonus calculation today"). This WO wires it for
# `express_delivery` ONLY (its own dispatch's explicit scope, "early-
# arrival bonus" under the express_delivery build lane) -- extending it to
# `cargo_delivery`/other types is a natural follow-up, not built here.
EARLY_ARRIVAL_BONUS_THRESHOLD_PCT = Decimal("0.50")
EARLY_ARRIVAL_BONUS_CAP_PCT = Decimal("0.25")


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


def _compute_early_arrival_bonus(contract: Any, now: datetime) -> int:
    """express_delivery's early-arrival bonus -- extracted out of
    complete() so that function's own McCabe complexity stays flat as this
    WO adds a new gated branch (this codebase tolerates far worse
    elsewhere, but a cheap extraction here is worth doing -- see gameserver
    -ruff-c901-not-enforced in monk's own memory notes). Pure given
    (contract, now); see EARLY_ARRIVAL_BONUS_*'s own comment for the
    canon-cited formula. Returns 0 for every non-express contract or one
    delivered at/before the 50% threshold -- never negative.

    `getattr(contract, "contract_type", None)`, not a direct attribute
    read: `contract_type` predates this WO on the real ORM column (NOT
    NULL, every live row has one) but several PRE-EXISTING test fixtures
    across this test suite (e.g. test_contract_escrow.py's own local
    `_npc_contract()` helper) build a minimal SimpleNamespace without it --
    a direct `contract.contract_type` access crashes those call sites with
    AttributeError. Matches this codebase's own established defensive-
    getattr convention for exactly this "older test double, newer optional
    read" situation (see production-rate-multiplier-getattr-safety /
    region-snapshot-getattr-not-attr in monk's own memory notes)."""
    if getattr(contract, "contract_type", None) != ContractType.EXPRESS_DELIVERY:
        return 0
    if not contract.posted_at or not contract.deadline:
        return 0
    window_seconds = (contract.deadline - contract.posted_at).total_seconds()
    if window_seconds <= 0:
        return 0
    remaining_seconds = (contract.deadline - now).total_seconds()
    remaining_frac = Decimal(remaining_seconds) / Decimal(window_seconds)
    if remaining_frac <= EARLY_ARRIVAL_BONUS_THRESHOLD_PCT:
        return 0
    bonus_pct = (
        EARLY_ARRIVAL_BONUS_CAP_PCT
        * (remaining_frac - EARLY_ARRIVAL_BONUS_THRESHOLD_PCT)
        / (Decimal("1.0") - EARLY_ARRIVAL_BONUS_THRESHOLD_PCT)
    )
    bonus_pct = min(bonus_pct, EARLY_ARRIVAL_BONUS_CAP_PCT)
    return _to_credits_int(_round_credits(_as_decimal(contract.payment) * bonus_pct))


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
    # WO-CONTRACT-3-NPCGEN-TYPES: express_delivery's early-arrival bonus --
    # canon's exact formula (contracts.md:323, see EARLY_ARRIVAL_BONUS_*'s
    # own comment at this module's top and _compute_early_arrival_bonus's
    # own docstring for the extracted formula). Bounded (a fraction of
    # `contract.payment`, itself bounded by the generator's own quantity/
    # price caps) and whole-credit, same rounding idiom as `payout` above
    # -- never an unbounded or mis-scaled mint. Gated to express_delivery
    # ONLY, matching this WO's scope.
    early_arrival_bonus = _compute_early_arrival_bonus(contract, now)
    player.credits = (player.credits or 0) + payout + early_arrival_bonus
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

    # WO-CONTRACT-3-NPCGEN-TYPES: hazardous_transport's completion-time
    # faction penalty -- contracts.md:420 "apply a faction penalty on
    # completion (the law-side faction loses standing)". `reputation_
    # penalty` is set on the row at GENERATION time (contract_generator.py,
    # HAZARDOUS_TRANSPORT_FEDERATION_REP_PENALTY) -- this is the first real
    # READER of that column (contract_dispute.py's own module docstring
    # notes it was, until now, written but never read anywhere). Applied
    # via the SAME sync, flush-only `apply_faction_rep_delta` combat_
    # service.py / contraband_service.py already use for exactly this kind
    # of in-transaction faction-rep hook -- not a new mechanism, reusing
    # the one this codebase already has. Guarded on a truthy (non-None,
    # non-zero) value so a hazardous_transport row somehow missing one
    # (e.g. a pre-this-WO row, impossible today but defensive) never
    # crashes completion. Both attrs read via getattr(..., None) -- see
    # _compute_early_arrival_bonus's own docstring for why (older test
    # fixtures across this suite predate both `contract_type` and
    # `reputation_penalty` being read here).
    if getattr(contract, "contract_type", None) == ContractType.HAZARDOUS_TRANSPORT and getattr(
        contract, "reputation_penalty", None
    ):
        apply_faction_rep_delta(
            db, player_id, FactionType.FEDERATION, int(contract.reputation_penalty),
            reason="hazardous_transport_contract_completed",
        )

    db.flush()

    logger.info(
        "Player %s completed contract %s, paid %d credits (+%d early-arrival bonus)",
        player_id, contract.id, payout, early_arrival_bonus,
    )
    return {
        "id": str(contract.id),
        "status": contract.status.value,
        "completed_at": now,
        "payout": payout,
        "early_arrival_bonus": early_arrival_bonus,
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
    atomic transaction.

    WO-CONTRACT-LOCK-ORDER (axis-1, task #54): the issuer is now locked
    BEFORE the guarded status-flip UPDATE below, not after -- this sweep
    previously locked Contract-then-Player while every API action
    (accept/abandon/cancel/insure/file_dispute/resolve_dispute, all via
    `_load_player`/`_load_two_players_for_update`) locks Player-then-
    Contract, an AB-BA cross-table deadlock risk between this sweep and
    any of them touching the same contract's issuer. Reordering to
    Player-then-Contract here closes it, matching the codebase-wide
    convention. A candidate whose guarded UPDATE then rowcounts 0 (raced
    away by a concurrent cancel/abandon) still leaves the issuer lock (if
    one was taken) held until this sweep tick's own commit -- accepted
    tradeoff, not released early, no sentinel-exception machinery added.
    A SEPARATE axis (sweeps' own cross-tick player-lock ORDER vs the
    ascending-by-id order `_load_two_players_for_update` already enforces
    for every two-player API call) is a real but much narrower deadlock
    risk, deliberately DEFERRED as a tracked follow-up -- see task #54's
    axis-2 analysis; both the deadlock-victim sweep and any API-side
    victim are already money-safe (per-candidate savepoint recovery) and,
    after this WO's route-level fix, availability-safe (clean 409 retry)."""
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

        needs_refund = candidate.escrow_amount and candidate.escrow_amount > 0
        issuer = None
        if needs_refund:
            # WO-CONTRACT-LOCK-ORDER: the lock itself is savepoint-isolated
            # (a SEPARATE savepoint from the credit-mutation one below, and
            # the guarded status-flip in between stays un-savepointed, both
            # per Mack MEDIUM #2's original split) -- a genuine failure here
            # (a real deadlock/lock-timeout OperationalError, or the
            # defensive vanished-row case) would otherwise abort the WHOLE
            # outer transaction (Postgres aborts the transaction on any
            # failed statement, not just the Python frame), reintroducing
            # exactly the whole-sweep-crash bug Mack MEDIUM #2 fixed --
            # just relocated to this now-earlier lock instead of the old
            # later one. ROLLBACK TO SAVEPOINT here costs nothing: the lock
            # was never granted if the statement itself failed, and no
            # other mutation has happened yet for this candidate.
            #
            # A failure here does NOT `continue` (this is a `while True`
            # loop re-querying the SAME server-side filter every iteration
            # -- unlike `continue` after a genuinely raced-away row, `issuer`
            # failing to lock changes nothing in the DB, so a `continue`
            # here would re-select this SAME candidate forever, an infinite
            # loop). Instead `issuer` stays None and the guarded status-flip
            # below still runs -- the row still leaves this candidate set
            # (guaranteeing forward progress), just with no refund attempted
            # this tick; escrow stays HELD, picked up later by `sweep_
            # expired_dispute_window`'s own undisputed-refund pass (matching
            # the pre-existing "not reachable today, cheap to harden
            # against" resting state Mack MEDIUM #2 already accepted for a
            # post-guard credit-side failure).
            try:
                with db.begin_nested():
                    issuer = _load_player(db, candidate.issuer_id, for_update=True)
            except Exception:
                logger.exception(
                    "sweep_expired_contracts: issuer lock failed for contract "
                    "%s -- status will still be flipped below (no refund "
                    "this tick)", candidate.id,
                )
                issuer = None

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
            # already moved past 'posted'). `issuer` (if locked above) is
            # simply held, unused, until this sweep tick's own commit --
            # see this function's own WO-CONTRACT-LOCK-ORDER note above.
            continue
        candidate.status = ContractStatus.EXPIRED

        if issuer is not None:
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
    `in_progress -> expired`; for cargo_delivery/express_delivery/
    hazardous_transport `accept` -> `ACCEPTED` is still the terminal
    pre-completion state (no code path for those types ever sets
    IN_PROGRESS), so this sweep is the code-equivalent of canon's
    in_progress deadline-expiry for them -- see the LEGAL_TRANSITIONS
    comment (contract_escrow_core.py).

    [KNOWN GAP, WO-CONTRACT-3b-BULK] bulk_procurement's own deliver()/
    walk_away_bulk_procurement() (this module) ARE real IN_PROGRESS
    writers now -- this sweep's own candidate query below still filters
    on `status == ACCEPTED` only, so a bulk_procurement contract that has
    had at least one partial delivery (status IN_PROGRESS) and then blows
    past its deadline is NOT picked up by this sweep or charged any
    penalty. Extending this sweep's candidate set (or adding a sibling
    IN_PROGRESS-scoped one) to cover that case is a real, un-invented
    follow-up -- flagged here rather than silently left undocumented or
    silently built without a design ruling on what an in-progress bulk
    failure should even cost (canon gives no bulk-specific in_progress
    deadline-expiry number; the generic Penalties section's "1x payment
    debit" doesn't obviously apply to a PARTIALLY-fulfilled contract).

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

    [SUPERSEDED by WO-CONTRACT-2b-HOLD-ESCROW, Max R (option C)] issuer
    escrow disposition on an ACCEPTOR-caused failure was previously an
    immediate refund at THIS sweep (see git history for the WO-1a-CORE /
    WO-CONTRACT-1b-CLAIM-SAFETY -era reasoning) -- Max ruled that hollow,
    since by the time a dispute could be filed (contracts.md:390, within
    48 game-hours of the failure) the escrow was already gone, so `file_
    dispute`/`resolve_dispute`'s own issuer-funded payouts could only ever
    draw from the issuer's CURRENT wallet (likely already spent) rather
    than a real, held source. THIS sweep no longer touches the issuer's
    credits or `escrow_state` AT ALL -- escrow stays `HELD` through the
    dispute window; `escrow_amount` is the ONLY thing this sweep still
    adjusts (the pool draw below), and its eventual disposition (refund
    if undisputed, or a bounded payout if disputed) happens later, in
    `sweep_expired_dispute_window` or `file_dispute`/`resolve_dispute`
    respectively -- see each one's own docstring for the atomic guard
    (`_guarded_file_dispute`) that makes the two mutually exclusive no
    matter which commits first.

    WO-CONTRACT-1b-CLAIM-SAFETY (still applies, target changed): the pool
    draw below is still netted OUT of the held ledger in this SAME nested
    transaction -- `escrow_amount` already includes whatever pool the
    issuer funded at post time (`payment + insurance_pool_reserve`), so
    letting it sit un-adjusted while the acceptor's penalty is ALREADY
    reduced by that same draw would let a LATER disposition (deferred
    refund or dispute payout) hand the issuer money that was already spent
    covering the acceptor's claim -- see `_compute_claim_offset`'s own
    docstring for the full reasoning, now also covering why this stays
    whole-credit-exact (R3) across an arbitrarily deferred disposition.
    Uninsured or zero-pool contracts see `pool_draw == 0` -- `escrow_
    amount` is untouched, byte-identical to before this WO.

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

    WO-CONTRACT-LOCK-ORDER (axis-1, task #54): the acceptor is now locked
    BEFORE the guarded status-flip UPDATE below, not after -- the docstring
    note further down ("no separate issuer lock is needed... the guarded
    status-flip UPDATE just above already row-locks the Contract") still
    holds for THAT purpose (serializing against `insure()`'s own acceptor
    lock), but this sweep separately participated in the Contract-vs-
    Player AB-BA cross-table deadlock every OTHER contract-mutating API
    action already avoids (all Player-then-Contract via `_load_player`/
    `_load_two_players_for_update`). Reordering here closes it. A
    candidate whose guarded UPDATE then rowcounts 0 (raced away) still
    leaves the acceptor lock held until this sweep tick's own commit --
    accepted tradeoff, matching `sweep_expired_contracts`'s identical note.
    The sweeps' own cross-tick player-lock ORDER (axis-2) remains a
    separate, deferred, tracked follow-up -- see task #54's axis-2
    analysis.

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

        # WO-CONTRACT-LOCK-ORDER (axis-1, task #54): locked BEFORE the
        # guarded status-flip UPDATE below -- see this function's own
        # docstring note above. Savepoint-isolated (SEPARATE from the
        # credit-mutation savepoint further below, guarded status-flip
        # left un-savepointed in between, matching sweep_expired_
        # contracts' identical restructuring and Mack MEDIUM #2's
        # original split) -- an uncaught failure here (real deadlock/
        # lock-timeout, or the defensive vanished-row case) would
        # otherwise abort the whole outer transaction and crash the rest
        # of this sweep's candidates, the exact bug Mack MEDIUM #2 fixed.
        #
        # A failure here does NOT `continue` past the status-flip (unlike
        # a genuinely raced-away row, below) -- `acceptor` stays None and
        # the guarded status-flip still runs; the row is still counted in
        # `expired` and still leaves ACCEPTED, just with no penalty/pool-
        # draw applied this tick, matching Mack MEDIUM #2's own accepted
        # resting state for a post-guard credit-side failure.
        acceptor = None
        try:
            with db.begin_nested():
                acceptor = _load_player(db, candidate.acceptor_player_id, for_update=True)
        except Exception:
            logger.exception(
                "sweep_expired_accepted_contracts: acceptor lock failed for "
                "contract %s -- status will still be flipped below (no "
                "penalty/pool-draw this tick)", candidate.id,
            )
            acceptor = None

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
            # protection. `acceptor` is held, unused, until this sweep
            # tick's own commit -- see this function's own WO-CONTRACT-
            # LOCK-ORDER note above.
            continue
        candidate.status = ContractStatus.EXPIRED

        # WO-ECON-CONTRACT-MONEY-HARDEN: same savepoint-isolation shape as
        # sweep_expired_contracts (Mack MEDIUM #2) -- the status flip above
        # already landed on the shared transaction; only the credit-side
        # penalty below is wrapped, so a failure here reverts just this
        # row's credit effects and the sweep continues past it.
        #
        # WO-CONTRACT-2b-HOLD-ESCROW: this block no longer refunds the
        # issuer at all -- ONLY the acceptor's own wallet is touched here,
        # so the acceptor/issuer dual-lock (Mack HIGH #1, WO-ECON-CONTRACT-
        # MONEY-HARDEN) is GONE. Escrow is now HELD through the 48h dispute
        # window (see `sweep_expired_dispute_window`'s own docstring for
        # the eventual undisputed-refund, and `file_dispute`'s `_guarded_
        # file_dispute` for the disputed path) -- `candidate.escrow_state`
        # is deliberately left untouched (still HELD) below; only `escrow_
        # amount` itself is adjusted for the pool draw. A single acceptor
        # lock is sufficient: `insure()` also locks the acceptor before its
        # own guarded UPDATE, which is the ONLY race this function's own
        # `_refresh_contract_insurance_snapshot` call needs to be
        # serialized against (see that helper's own docstring) -- no
        # concurrent write anywhere in this codebase touches `escrow_
        # amount` for an ACCEPTED-then-just-EXPIRED contract except this
        # sweep itself, and the guarded status-flip UPDATE just above
        # already row-locks the Contract for the remainder of this
        # transaction, so no separate issuer lock is needed to protect the
        # `escrow_amount` mutation below either.
        #
        # `acceptor is None` means the lock attempt above itself failed
        # (see this function's own WO-CONTRACT-LOCK-ORDER note) -- no
        # penalty/pool-draw is attempted at all in that case (there is no
        # locked acceptor object to mutate), matching Mack MEDIUM #2's
        # accepted resting state.
        if acceptor is None:
            expired += 1
            continue
        try:
            with db.begin_nested():
                # WO-CONTRACT-1b-CLAIM-SAFETY: `candidate` was gathered by
                # the upfront, UNLOCKED `.all()` above -- a concurrent
                # insure() can commit its coverage tier in the window
                # between that read and the acceptor lock just acquired,
                # the identical race `_refresh_contract_insurance_
                # snapshot`'s own docstring documents for abandon()/
                # cancel_player_contract(). MUST run AFTER the lock, BEFORE
                # `apply_claim_offset` reads insurance_coverage_tier/
                # insurance_pool_reserve below -- refreshes ALL of
                # `candidate`'s columns in place (including the pool).
                _refresh_contract_insurance_snapshot(db, candidate)

                penalty = _round_credits(_as_decimal(candidate.penalty))
                offset = apply_claim_offset(candidate, penalty)
                acceptor.credits = max(0, (acceptor.credits or 0) - _to_credits_int(offset["acceptor_debit"]))

                # WO-CONTRACT-2b-HOLD-ESCROW: `pool_draw` (whole-credit,
                # R3 -- see _compute_claim_offset's own docstring) is money
                # this codebase already took from the issuer's wallet at
                # post_player_contract() time and folded into escrow_
                # amount. It must be consumed out of the HELD ledger right
                # now, at the SAME moment the acceptor's penalty is reduced
                # by it -- otherwise a LATER disposition (the deferred
                # refund sweep, or a dispute payout) would still be working
                # from the pool's full, undrawn value and could refund/pay
                # out money that was already spent reducing the acceptor's
                # debit (the exact mint WO-1b closed at expiry-time, now
                # reopened at disposition-time). Whole minus whole is
                # exactly whole (R3) -- `escrow_amount` needs no further
                # rounding at whatever later moment disposes of it.
                if candidate.issuer_type == ContractIssuerType.PLAYER and offset["pool_draw"] > 0:
                    candidate.escrow_amount = _round_credits(_as_decimal(candidate.escrow_amount)) - offset["pool_draw"]
        except Exception:
            logger.exception(
                "sweep_expired_accepted_contracts: acceptor penalty/pool-draw "
                "failed for contract %s (status already EXPIRED in this same "
                "sweep pass; credit-side effects reverted, sweep continues)",
                candidate.id,
            )

        expired += 1

    db.flush()
    return {"expired": expired}


def sweep_expired_dispute_window(db: Session, now: Optional[datetime] = None) -> Dict[str, int]:
    """WO-CONTRACT-2b-HOLD-ESCROW (Max R, option C) -- the DEFERRED half of
    the held-escrow design `sweep_expired_accepted_contracts` starts:
    once an EXPIRED contract's `DISPUTE_FILING_WINDOW_HOURS` (48h,
    contract_dispute.py) has strictly elapsed with NO dispute filed, its
    held `escrow_amount` finally returns to the PLAYER issuer -- the same
    "escrow returns to the party that never received their goods" idiom
    `sweep_expired_contracts`'s own player-refund branch already uses
    (contract_service.py, this module), just fired on a completely
    different candidate set and a much longer fuse.

    CANDIDATE SET: `status == EXPIRED AND escrow_state == HELD AND
    deadline < now - DISPUTE_FILING_WINDOW_HOURS` -- deliberately its OWN
    function, not folded into `sweep_expired_accepted_contracts` (which
    only ever sees ACCEPTED-status rows) or `sweep_expired_contracts`
    (POSTED-status rows only): three different statuses, three different
    triggers, matching this module's established one-function-per-concern
    convention for contract expiry sweeps.

    THE BOUNDARY, EXACTLY COMPLEMENTARY TO `file_dispute`'s OWN CHECK:
    `file_dispute` rejects a filing when `now - contract.deadline >
    DISPUTE_FILING_WINDOW_HOURS` (contract_dispute.py). This sweep's own
    filter, `deadline < now - window` (algebraically `now > deadline +
    window`), is the IDENTICAL strict inequality -- there is no instant
    where neither this sweep nor a filing attempt can act, and no instant
    where both would consider a fresh, undisputed row eligible.

    THE RACE AT THAT EXACT BOUNDARY -- closed at the ROW level, NOT by the
    CEXP advisory lock this sweep runs under (that lock only serializes
    this sweep against ITS OWN sibling sweeps / a second gameserver
    instance's pass; the ordinary `file_dispute` API call takes no such
    lock). Both this sweep's per-row guarded UPDATE below and `file_
    dispute`'s own `_guarded_file_dispute` (contract_dispute.py) gate on
    the IDENTICAL two-column WHERE (`status == 'expired' AND escrow_state
    == 'held'`) -- Postgres serializes any two concurrent UPDATEs
    targeting the same row (the second blocks on the row lock, then
    re-evaluates its WHERE against the now-committed state once
    unblocked), so exactly ONE of {this sweep's refund, a dispute filing}
    ever wins per contract: whichever commits first flips the pair away
    from `(expired, held)`, and the loser's own guarded UPDATE matches
    zero rows -- this sweep's `rowcount == 0 -> continue` (the SAME
    "raced row, skip it" idiom every sibling sweep in this module uses)
    or `_guarded_file_dispute`'s own `ContractConflictError` on the
    dispute side. Neither ordering can double-dispose the same escrow.

    WHOLE-CREDIT (R3): `escrow_amount` is guaranteed exactly whole at
    this point (never touched between expiry and here except by `sweep_
    expired_accepted_contracts`'s own whole-minus-whole pool-draw
    subtraction, see `_compute_claim_offset`'s own docstring) -- the
    refund below needs no rounding step, `_to_credits_int` is a lossless,
    defensive conversion here, not a real rounding operation.

    NPC-issued rows never reach this candidate set at all (`escrow_
    amount` is always 0 for them and `sweep_expired_accepted_contracts`
    never touches their `escrow_state`, which stays HELD by column
    default -- but `escrow_amount == 0` means the per-row refund branch
    below is a no-op for any NPC row that somehow matched, belt-and-
    suspenders). FLUSH-ONLY -- folded into the SAME CEXP advisory lock +
    single commit as its sibling sweeps; see contract_sweeps.py's `_run_
    contract_expire_sweep_sync`.

    STRANDED-ESCROW ATOMICITY (cipher MEDIUM, WO-2b gate): the guarded
    `escrow_state -> REFUNDING` UPDATE and its Python mirror live INSIDE
    the SAME `db.begin_nested()` savepoint as the actual refund below --
    NOT split across the guard-then-savepoint shape `sweep_expired_
    accepted_contracts` uses for ITS OWN per-row block. That split is
    safe over THERE because a mid-refund failure leaves a RECOVERABLE
    state (status stays ACCEPTED... no, EXPIRED + escrow_state HELD,
    simply re-processed by a later tick). It is NOT safe HERE: this
    sweep's guard is the LAST possible mutation of `escrow_state` away
    from HELD before a dispute could ever claim the row again (`_guarded_
    file_dispute` gates on that SAME `escrow_state == 'held'` predicate)
    -- if the guard flip committed but a transient failure THEN rolled
    back only the refund/zeroing, the row would be left `escrow_state=
    REFUNDING` with a non-zero `escrow_amount` and NO issuer credit:
    permanently stranded, since NEITHER this sweep NOR `_guarded_file_
    dispute` will ever touch a row that isn't `(expired, held)` again.
    Folding the guard into the SAME nested block means a failure anywhere
    inside it (including a raced-away `rowcount == 0`, which does NOT
    raise -- see below) rolls the WHOLE row back to its pre-savepoint
    state, exactly matching `file_dispute`/`resolve_dispute`'s own
    convention of keeping a guard and its settlement in ONE atomic
    boundary, never split.

    A `rowcount == 0` (raced away -- a dispute won the race for this row,
    or a sibling sweep instance already claimed it) is NOT an error and
    is NOT logged as one: the savepoint simply does nothing and commits
    empty, matching the "raced row, skip it" idiom every sibling sweep in
    this module already uses -- only a GENUINE failure during the refund
    itself (e.g. the issuer row vanishing) reaches the `except` below.

    TWO DISTINCT FAILURE MODES, BOTH MONEY-SAFE BY THE SAME MECHANISM
    (mack CRITICAL, WO-2b gate; mechanism precision from Rook's SA 2.0
    trace): (a) an ORDINARY transient failure (e.g. `_load_player` raising
    for one candidate -- a vanished row, not reachable via any hard-delete
    path today, but cheap to harden against) and (b) this sweep being the
    LOSING side of a genuine Postgres DEADLOCK (contracts.md's own cross-
    cutting lock-ordering finding, tracked separately) both resolve the
    SAME way: the abort happens INSIDE this candidate's `db.begin_nested()`
    block (a deadlock is detected exactly where the conflicting lock
    acquisition happens -- here, at `_load_player`'s own row lock, the
    same place a plain exception would surface) -- SQLAlchemy issues
    `ROLLBACK TO SAVEPOINT` for that ONE candidate, reverting its guard
    flip + refund together (the atomicity fix above is what makes the
    guard flip part of that rollback at all), this function's own per-
    candidate `except` catches it, logs, and `continue`s -- and CRITICALLY
    the OUTER sweep transaction SURVIVES: it is not aborted, only that one
    savepoint was rolled back. The loop proceeds to the NEXT candidate
    normally, and `_run_contract_expire_sweep_sync`'s single `db.commit()`
    at the end of the tick DOES commit -- including every OTHER candidate
    this same tick already refunded. Net effect: the deadlocked/failed
    candidate reverts to EXPIRED+HELD and is retried on a later tick,
    while its SIBLINGS in the same tick still commit their refunds --
    NOT a whole-tick rollback.

    Mode (a) is proven DB-free (`TestDisputeWindowRefundAtomicity`, test_
    contract_escrow.py -- a genuine snapshot/restore fake, not the shared
    no-op one, since a no-op fake can't distinguish "reverted" from "never
    happened"). Mode (b) cannot be exercised DB-free (no fake here models
    real Postgres deadlock detection or SAVEPOINT semantics) -- the load-
    bearing claim still owed at the deploy window is a live-Postgres,
    forced-AB-BA test asserting BOTH halves: the deadlocked candidate
    reverts to EXPIRED+HELD, AND a sibling candidate in the same tick
    still successfully commits its refund.

    WO-CONTRACT-LOCK-ORDER (axis-1, task #54): the issuer lock (when
    `needs_refund`) is now the FIRST statement inside the savepoint below,
    acquired BEFORE the guarded `escrow_state -> REFUNDING` UPDATE, not
    after -- this sweep previously locked Contract-then-Player like its
    siblings, an AB-BA risk against `file_dispute`'s own Player-then-
    Contract order on the identical row (both gate on the SAME two-column
    WHERE, see above). Reordering closes it; the atomicity guarantee above
    (mode (a) and (b) both revert the WHOLE candidate via ROLLBACK TO
    SAVEPOINT) is unchanged by WHERE inside the savepoint the lock sits --
    it is still the very first thing that can fail, so a failure there
    reverts nothing else (there is nothing else yet to revert). A
    candidate whose guard then rowcounts 0 (raced away) still leaves the
    issuer lock (if taken) held, unused, until this sweep tick's own
    commit -- same accepted tradeoff as this module's other two sweeps.
    The sweeps' own cross-tick player-lock ORDER (axis-2, distinct from
    this Contract-vs-Player axis) remains a separate, deferred, tracked
    follow-up -- see task #54's axis-2 analysis; already money-safe (this
    same savepoint-revert mechanism) and, after this WO's route-level
    fix, availability-safe (clean 409 retry) either way."""
    now = now or _now()
    window_cutoff = now - timedelta(hours=DISPUTE_FILING_WINDOW_HOURS)

    candidates = (
        db.query(Contract)
        .filter(
            Contract.status == ContractStatus.EXPIRED,
            Contract.escrow_state == ContractEscrowState.HELD,
            Contract.deadline < window_cutoff,
        )
        .all()
    )

    refunded = 0
    for candidate in candidates:
        disposed = False
        try:
            with db.begin_nested():
                # WO-CONTRACT-LOCK-ORDER (axis-1, task #54): locked FIRST,
                # before the guarded UPDATE below -- see this function's
                # own docstring note above. `needs_refund` reads fields
                # that nothing mutates between the upfront `.all()` and
                # here, so evaluating it before the guard (rather than
                # after, as before) reads the identical value.
                needs_refund = (
                    candidate.issuer_type == ContractIssuerType.PLAYER
                    and candidate.escrow_amount and candidate.escrow_amount > 0
                )
                issuer = _load_player(db, candidate.issuer_id, for_update=True) if needs_refund else None

                row_stmt = (
                    update(Contract)
                    .where(
                        Contract.id == candidate.id,
                        Contract.status == ContractStatus.EXPIRED,
                        Contract.escrow_state == ContractEscrowState.HELD,
                    )
                    .values(escrow_state=ContractEscrowState.REFUNDING)
                )
                result = db.execute(row_stmt)
                if result.rowcount == 0:
                    # Raced away -- see this function's own docstring for
                    # why this can never double-dispose the escrow. Not a
                    # failure: fall through, the savepoint commits empty.
                    # `issuer` (if locked above) is simply held, unused,
                    # until this sweep tick's own commit -- see this
                    # function's own WO-CONTRACT-LOCK-ORDER note above.
                    pass
                else:
                    candidate.escrow_state = ContractEscrowState.REFUNDING

                    if issuer is not None:
                        refund = _to_credits_int(_round_credits(_as_decimal(candidate.escrow_amount)))
                        issuer.credits = (issuer.credits or 0) + refund
                        candidate.escrow_amount = Decimal("0")
                    disposed = True
        except Exception:
            logger.exception(
                "sweep_expired_dispute_window: refund failed for contract %s "
                "-- the guard flip and the credit movement share ONE savepoint, "
                "so both revert together; the row stays EXPIRED+HELD and is "
                "retried on a later sweep tick, never stranded", candidate.id,
            )
            continue

        if disposed:
            refunded += 1

    db.flush()
    return {"refunded": refunded}


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

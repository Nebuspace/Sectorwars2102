"""StorageLocker deposit-flow -- WO-STORE-DEPOSIT-FLOW (STORAGE-HEIST S1),
builds on WO-STORE-LOCKER-MODEL's schema (src/models/storage_locker.py,
migration 61b7e6f4ff93). Multi-trip deposit: a player docked at a
contract's destination Station rents/reuses a locker, deposits the
contract's commodity from their ship's cargo in installments
(ContractCargoDeposit audit rows), and once the locker's accumulated
total reaches the contract's required quantity, the contract is
completed by DELEGATING to contract_service.complete() -- the canonical
completer; this module never reimplements escrow/payout.

SYNC Session, FLUSH-ONLY -- matches contract_service.py's own convention
exactly (the route owns the commit): every deposit call either
accumulates toward, or actually invokes contract_service's own guarded-
transition completion path, in the SAME transaction.

CARGO BRIDGE (documented design decision -- see this WO's own report for
the full reasoning): contract_service.complete() reads its required
quantity directly from player.current_ship.cargo -- it has no concept of
a locker, and reimplementing its escrow/payout logic here was explicitly
out of scope ("call the canonical completer"). So the moment a deposit
brings the locker's accumulated total to >= contract.quantity, this
module temporarily materializes the FULL required quantity onto the
player's CURRENT ship's cargo dict, then calls complete() unmodified --
which decrements that exact amount back off in its own code path. Net
effect on the ship's cargo: zero (the injection is never committed on
its own; complete()'s own decrement happens in the same flush before any
commit reaches the database). This lets ANY ship be docked at the final
deposit, matching canon's "any ship can fulfill any contract over enough
trips" -- the delivering ship doesn't have to be the one that carried
every earlier installment.

LOCK ORDER: Contract is read UNLOCKED throughout -- contract_service.py
never row-locks a Contract (its own module docstring: "No SELECT ... FOR
UPDATE is needed; the guarded UPDATE *is* the lock" -- _guarded_
transition's atomic UPDATE...WHERE status=:from is the concurrency gate).
This module's own new lock family is Locker (the shared resource
concurrent deposit attempts race on -- locked FIRST, mirroring warp_gate_
service's own gate-before-player convention for the same "shared
contested row first" reason) -> Player (SECOND, via contract_service.
_load_player(for_update=True) -- reused, not reimplemented) -> Ship
(THIRD, for the cargo RMW). get_or_create_locker only ever locks Player
(there is no Locker row to lock on a first call) -- a single-resource
lock cannot participate in an AB-BA deadlock against this module's own
Locker-then-Player ordering.

RENT (WO-STORE-FEE-ACCRUAL, D16/D17/D18 -- Max's ruling, delegated):
settle_fee() charges flat rent (locker.rent_rate cr/unit/day, wall-clock)
via a continuous-accrue-and-round-once ledger (D18, see settle_fee's own
docstring) so no salami-slicing and no per-trip minimum-tax. deposit_
cargo() settles BEFORE the deposit for every non-completing installment
(unchanged), but for the installment that completes the contract, settle-
ment is deferred until AFTER contract_service.complete()'s payout credits
the player (D17 -- settling first would floor the bill to near-zero at
the player's poorest moment). settle_fee's re-lock of Locker/Player rows
this function already holds is a harmless same-session re-acquire, not a
new lock-order hazard -- see both functions' own docstrings for the full
reasoning.

EXPIRY -> CLAIMABLE -> RETRIEVE (WO-STORE-EXPIRY-CLAIMABLE + D19): miss
the contract's deadline before reaching full quantity and the locker
converts to CLAIMABLE storage -- the player keeps whatever was
deposited, rent keeps ticking, retrieve later (canon, storage_locker.py's
own model docstring). sweep_expired_lockers() runs inside contract_
sweeps.py's `_run_contract_expire_sweep_sync`, the SAME transaction as
the contract-expiry sweep, right after it -- NOT a settle-on-access
check, since expiry is a scheduler-driven event with no natural "access"
moment of its own to hang a check off. D19 (deposit-wins is a REQUIRED
semantic, orchestrator-ruled): contract_service.sweep_expired_accepted_
contracts is called with `expiry_gate=gate_contract_expiry_on_locker` --
see that function's own docstring for the full mechanism (a Locker-lock-
first probe that both DEFERS a contended contract's expiry, letting an
in-flight completing deposit_cargo call win deterministically, AND kills
a confirmed AB-BA deadlock by making the sweep's own lock order
consistently Locker-then-Contract, matching deposit_cargo everywhere
else in the codebase). The
NEW-CONTRACT-GETS-A-NEW-LOCKER invariant is a structural property of
get_or_create_locker's own existing (owner, contract_id) lookup, not new
code: converting a locker sets contract_id -> NULL, and a query filtering
`contract_id == <a real new contract's id>` can never match a NULL row,
so a player who lets a contract lapse and then accepts a new one for the
same station always mints a genuinely fresh locker -- the old claimable
deposits can never silently count toward a different contract's
completion. retrieve_claimable_cargo() is deposit_cargo's mirror-image
(same Locker->Player->Ship lock order) with one asymmetry: capacity is
enforced on the way OUT too (unlike the deposit-side cargo bridge, which
is a same-transaction phantom that nets to zero, retrieved cargo actually
PERSISTS on the ship) -- see that function's own docstring for why
partial multi-trip retrieve (not reject-if-over) is the right call.
"""
import logging
import uuid
from datetime import datetime
from decimal import ROUND_FLOOR, Decimal
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import func
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from src.models.contract import Contract, ContractStatus
from src.models.player import Player
from src.models.ship import Ship
from src.models.storage_locker import ContractCargoDeposit, StorageLocker, StorageLockerStatus
from src.services import contract_service

logger = logging.getLogger(__name__)

_SECONDS_PER_DAY = Decimal(86400)


def _stored_units(db: Session, locker_id: uuid.UUID) -> int:
    """Sum of ContractCargoDeposit.quantity across ALL commodities for a
    locker -- deliberately not filtered to one commodity_type (today
    every contract-tied locker only ever holds its own single commodity,
    but a future standalone/claimable locker with contract_id=None has
    no contract to read commodity_type from at all, so this stays
    correct for that case with zero changes needed later)."""
    total = (
        db.query(func.coalesce(func.sum(ContractCargoDeposit.quantity), 0))
        .filter(ContractCargoDeposit.locker_id == locker_id)
        .scalar()
    )
    return int(total or 0)


def _consume_deposits(db: Session, locker_id: uuid.UUID, take: int) -> str:
    """WO-STORE-EXPIRY-CLAIMABLE (retrieve): consumes `take` units from a
    locker's ContractCargoDeposit audit rows, oldest-first (deposited_at
    ascending) -- a defensible, deterministic choice since these rows
    are fungible units of the SAME commodity, not economically distinct
    lots. Deletes rows fully consumed; reduces the one partially-consumed
    boundary row in place, leaving it for a later retrieve call.

    SINGLE-COMMODITY ONLY -- the S1 invariant every contract-tied locker
    holds exactly one commodity type (see _stored_units' own docstring:
    the ONLY way a locker gets deposits is deposit_cargo, which always
    writes contract.commodity_type). Raises loudly rather than silently
    picking a mix if that invariant is ever violated by a future S2
    change. Caller (retrieve_claimable_cargo) guarantees `take` <=
    the locker's current total via _stored_units, so this always fully
    consumes `take` units and never runs out of rows mid-loop."""
    rows = (
        db.query(ContractCargoDeposit)
        .filter(ContractCargoDeposit.locker_id == locker_id)
        .order_by(ContractCargoDeposit.deposited_at)
        .all()
    )
    commodities = {row.commodity for row in rows}
    if len(commodities) > 1:
        raise StorageError(
            f"multi_commodity_locker: locker {locker_id} holds {sorted(commodities)} -- "
            "retrieval only supports a single commodity per locker (S1 invariant)"
        )
    commodity = next(iter(commodities))

    remaining = take
    for row in rows:
        if remaining <= 0:
            break
        if row.quantity <= remaining:
            remaining -= row.quantity
            db.delete(row)
        else:
            row.quantity -= remaining
            remaining = 0
    return commodity


class StorageError(Exception):
    """400-class: player-facing validation failure. .args[0] is the
    human-readable detail string the route layer surfaces. Messages that
    carry a stable machine-readable reason are prefixed with a snake_case
    code, matching contract_service.py's own convention."""


class StorageNotFoundError(StorageError):
    """404-class."""


def _load_contract(db: Session, contract_id: uuid.UUID) -> Contract:
    contract = db.query(Contract).filter(Contract.id == contract_id).first()
    if contract is None:
        raise StorageNotFoundError(f"Contract {contract_id} not found")
    return contract


def get_or_create_locker(
    db: Session, player_id: uuid.UUID, contract_id: uuid.UUID,
) -> StorageLocker:
    """One StorageLocker per (player, contract) -- idempotent get-or-
    create. A second call for the same pair returns the EXISTING locker
    rather than minting a duplicate.

    Locks the Player row BEFORE the existence-check-then-insert so two
    concurrent calls for the SAME player+contract pair serialize on it
    (there is no Locker row to lock yet on the very first call -- the
    Player lock is the only resource available to guard the race). A
    unique (owner_player_id, contract_id) index (migration <followup>)
    is the belt-and-suspenders DB-level guarantee for any future call
    path that might bypass this lock."""
    contract = _load_contract(db, contract_id)
    if contract.status != ContractStatus.ACCEPTED:
        raise StorageError(
            f"stale_status: contract {contract.id} is '{contract.status.value}', not 'accepted'"
        )
    if contract.acceptor_player_id != player_id:
        raise StorageError("This contract is not accepted by you")

    player = contract_service._load_player(db, player_id, for_update=True)

    existing = (
        db.query(StorageLocker)
        .filter(StorageLocker.owner_player_id == player_id, StorageLocker.contract_id == contract_id)
        .first()
    )
    if existing is not None:
        return existing

    locker = StorageLocker(
        id=uuid.uuid4(),
        owner_player_id=player.id,
        station_id=contract.destination_station_id,
        contract_id=contract_id,
        status=StorageLockerStatus.ACTIVE,
    )
    db.add(locker)
    db.flush()
    logger.info("Player %s rented locker %s for contract %s", player_id, locker.id, contract_id)
    return locker


def gate_contract_expiry_on_locker(db: Session, contract: Contract) -> bool:
    """WO-STORE-EXPIRY-CLAIMABLE + D19 (deposit-wins is a REQUIRED
    semantic, orchestrator-ruled -- not merely an accepted side effect):
    the `expiry_gate` contract_service.sweep_expired_accepted_contracts
    calls for EACH candidate, BEFORE expiring it (see that function's
    own docstring for the full gate contract). Returns `True` ("safe to
    expire now") or `False` ("defer this contract's expiry -- a live
    completing deposit_cargo call currently holds its Locker").

    TWO-STEP, deliberately disambiguating "locker held" from "locker
    genuinely doesn't exist" (the nuance mack flagged):
    1. Plain, UNLOCKED existence check -- does this contract even have
       an ACTIVE StorageLocker at all? No -> `True` immediately (the
       overwhelming majority of contracts; zero extra locking attempted
       for the common non-storage case).
    2. A locker exists -> probe it via a SKIP LOCKED acquisition (a
       real SQLAlchemy/Postgres feature, `with_for_update(skip_locked=
       True)` -- not hand-rolled). Because step 1 already confirmed a
       matching row EXISTS, a `None` result here is UNAMBIGUOUSLY
       "exists but contended" (a live deposit_cargo call already holds
       it, per its own Locker-first order) -- never confusable with
       "doesn't exist". Returns `False` (defer).
    3. A row comes back -> THIS transaction now holds that Locker's
       lock, in Locker-then-Contract order (matching deposit_cargo's
       own order exactly) -- the caller's subsequent Contract UPDATE is
       therefore safe. This is what makes the WHOLE codebase
       consistently Locker-then-Contract for any transaction touching
       both (grepped: storage_service.py and contract_sweeps.py are the
       ONLY two places anywhere in src/ that ever combine a Contract
       lock and a Locker lock in one transaction; storage_service.py
       already always locks Locker first everywhere else) -- killing
       the AB-BA cycle structurally, not just making one side non-
       blocking. Returns `True`.

    Deliberately does NOT settle or convert the locker itself here -- it
    only ACQUIRES the lock as a gate. sweep_expired_lockers (run right
    after sweep_expired_accepted_contracts, same transaction) does the
    actual settle+CLAIMABLE-flip; its own settle_fee call's `with_for_
    update()` re-acquires THIS SAME lock, a harmless same-session
    re-acquire (the established pattern throughout this module -- see
    sweep_expired_lockers's own docstring)."""
    locker = (
        db.query(StorageLocker)
        .filter(
            StorageLocker.contract_id == contract.id, StorageLocker.status == StorageLockerStatus.ACTIVE,
        )
        .first()
    )
    if locker is None:
        return True

    acquired = (
        db.query(StorageLocker)
        .filter(StorageLocker.id == locker.id)
        .with_for_update(skip_locked=True)
        .first()
    )
    return acquired is not None


def get_bulk_locker_state(db: Session, contract: Contract) -> Optional[Tuple[uuid.UUID, int]]:
    """WO-CONTRACT-4-BULK: the public read contract_service.py's own
    bulk_procurement walk-away-penalty sites (the ACCEPTED-sweep's per-
    candidate body and abandon()) call to learn a bulk contract's actual
    Locker fill. Reuses `gate_contract_expiry_on_locker`'s EXACT ACTIVE-
    locker existence-lookup shape (same filter, same UNLOCKED plain
    SELECT) -- deliberately NOT a `with_for_update()` acquisition here:
    by the time the sweep's per-candidate body calls this, the gate
    (wired as `expiry_gate`) has ALREADY acquired this exact Locker's row
    lock earlier in the SAME transaction (see that function's own
    docstring for why every re-read after it is a harmless same-session
    re-acquire) -- a plain re-SELECT here just observes that already-
    locked, stable state. abandon()'s own call site has no such upstream
    gate (a single, voluntary, synchronous action, not a batch sweep) --
    see that function's own docstring for why an unlocked committed-read
    is an acceptable bound there too. Returns `(locker_id, stored_units)`
    if an ACTIVE locker exists for this contract, else `None` (the
    degenerate case -- no deposits were ever made, or the locker was
    already converted/claimed) -- the caller falls back to the static
    `Contract.penalty` default in that case. `_stored_units` itself stays
    private/internal, unchanged."""
    locker = (
        db.query(StorageLocker)
        .filter(
            StorageLocker.contract_id == contract.id, StorageLocker.status == StorageLockerStatus.ACTIVE,
        )
        .first()
    )
    if locker is None:
        return None
    return locker.id, _stored_units(db, locker.id)


def list_claimable_lockers(db: Session, player_id: uuid.UUID) -> List[Dict[str, Any]]:
    """WO-CONTRACT-5 (P2, the value-trap fix): every CLAIMABLE locker this
    player owns, enriched with the commodity/quantity actually deposited
    -- storage.py had zero GET routes before this WO, so a CLAIMABLE
    locker's cargo (converted by `sweep_expired_lockers` -- see that
    function's own docstring) had no reachable client path to even LEARN
    its own locker_id, let alone call the already-built `POST /retrieve`.

    LANDMINE (a WO-CONTRACT-4-BULK consequence): `sweep_expired_lockers`
    NULLS `contract_id` on the ACTIVE -> CLAIMABLE conversion (this
    module's own `locker.contract_id = None`) -- so a claimable locker's
    commodity/quantity can NEVER be read via its (gone) Contract; they
    live ONLY in this locker's own `ContractCargoDeposit` audit rows. A
    naive join against Contract here would silently return an empty/null
    commodity for every claimable locker -- do NOT join Contract at all.

    Grouped, SINGLE query (`locker_id, commodity, SUM(quantity)`) over
    `ContractCargoDeposit` for every candidate locker at once -- avoids
    N+1 (one query total, not one per locker). S1's own established
    invariant (one commodity per locker, see `ContractCargoDeposit`'s
    own docstring/`_stored_units`'s own note) means at most one grouped
    row per locker_id, so a plain dict keyed on locker_id is safe -- no
    multi-commodity last-write-wins ambiguity.

    Server-side owner-scoped (`owner_player_id == player_id`) -- NEVER
    trusts a client-supplied id, so a player can never list (or thereby
    even learn the existence of) another player's lockers. Pure read --
    no lock acquired, no mutation, no commit (the caller's route never
    commits either)."""
    lockers = (
        db.query(StorageLocker)
        .filter(
            StorageLocker.owner_player_id == player_id, StorageLocker.status == StorageLockerStatus.CLAIMABLE,
        )
        .all()
    )
    if not lockers:
        return []

    locker_ids = [locker.id for locker in lockers]
    deposit_totals = (
        db.query(
            ContractCargoDeposit.locker_id,
            ContractCargoDeposit.commodity,
            func.sum(ContractCargoDeposit.quantity),
        )
        .filter(ContractCargoDeposit.locker_id.in_(locker_ids))
        .group_by(ContractCargoDeposit.locker_id, ContractCargoDeposit.commodity)
        .all()
    )
    stored_by_locker: Dict[uuid.UUID, Tuple[Optional[str], int]] = {
        row_locker_id: (commodity, int(total)) for row_locker_id, commodity, total in deposit_totals
    }

    return [
        {
            "locker": locker,
            "commodity": stored_by_locker.get(locker.id, (None, 0))[0],
            "storedUnits": stored_by_locker.get(locker.id, (None, 0))[1],
        }
        for locker in lockers
    ]


def sweep_expired_lockers(db: Session, now: Optional[datetime] = None) -> Dict[str, int]:
    """WO-STORE-EXPIRY-CLAIMABLE: converts every ACTIVE locker whose tied
    Contract has expired into CLAIMABLE storage -- canon: "miss the
    deadline -> cargo converts to CLAIMABLE storage (the player keeps
    it, rent keeps ticking, retrieve later)" (storage_locker.py's own
    model docstring). Settles the final rent period up to `now`, then
    flips status -> CLAIMABLE and nulls contract_id (the locker's own
    columns already support this with zero migration -- see the model's
    own comments; CLAIMABLE was forward-built into the enum, contract_id
    was already nullable with ondelete=SET NULL).

    TRIGGER: hooked into the EXISTING contract-expiry sweep rather than a
    new scheduled entry. contract_service.sweep_expired_accepted_contracts
    is the sole path that ever transitions a Contract ACCEPTED -> EXPIRED
    (grepped LEGAL_TRANSITIONS -- no other edge produces it), and it
    already runs inside contract_sweeps.py's `_run_contract_expire_sweep_
    sync` under the CEXP advisory lock, in the SAME transaction, before a
    single `db.commit()`. This function is called immediately after it,
    in that SAME transaction -- so a candidate's Contract.status ==
    EXPIRED read here always reflects that SAME pass's just-flushed
    flips (contract_service's own sweep ends with db.flush(), not
    commit). Reusing CEXP (not a new lock/cadence key) mirrors the
    file's own precedent for combining the `posted` and `accepted`
    expiry sweeps under one lock/tick -- both are "contract expiry",
    this is a third facet of the same event, not an independent concern.

    NOT a raw bulk UPDATE (unlike sweep_expired_contracts' bulk-shortcut
    for its majority case): every candidate needs an individualized
    settle_fee call (locks + rent math + a Player credit touch), so this
    is a plain per-row loop, matching sweep_expired_accepted_contracts'
    own reasoning for why ITS sweep can't bulk-shortcut either.

    RACE SAFETY / D19 DEPOSIT-WINS (WO-STORE-EXPIRY-CLAIMABLE, verified
    against a concurrent deposit_cargo call, not assumed safe -- an
    earlier version of this docstring analyzed a race here that turned
    out to be a confirmed AB-BA deadlock; see gate_contract_expiry_on_
    locker's own docstring for the fix, and D19's own ruling for why
    "deposit wins the deadline race" is a REQUIRED semantic, not an
    accepted side effect): contract_service.sweep_expired_accepted_
    contracts is now ALWAYS called with `expiry_gate=gate_contract_
    expiry_on_locker` by the scheduler -- for every storage-linked
    contract EXPIRED in THIS pass, that gate already SUCCESSFULLY
    acquired its Locker's row lock (Locker-then-Contract order, matching
    deposit_cargo's own order) before the contract was ever expired. A
    contract whose Locker was contended (a live completing deposit)
    never reaches EXPIRED status in this pass at all -- it stays
    ACCEPTED, deferred to a later tick, and the in-flight deposit
    completes uncontested (deposit-wins). This means by the time THIS
    function runs, every EXPIRED candidate's Locker lock is GUARANTEED
    already held by this same transaction -- the settle_fee call below
    (which does its own `with_for_update()`) is therefore always a
    harmless same-session re-acquire, never a fresh contended
    acquisition; there is no remaining race to analyze here at all, it
    was fully resolved upstream by the gate."""
    now = now or contract_service._now()

    active_lockers = db.query(StorageLocker).filter(StorageLocker.status == StorageLockerStatus.ACTIVE).all()
    converted = 0
    for locker in active_lockers:
        if locker.contract_id is None:
            # Defensive only -- every S1 ACTIVE locker is contract-tied at
            # creation (get_or_create_locker always sets it); a future S2
            # standalone-rented locker (no contract at all) would land
            # here too and correctly has nothing to expire against.
            continue
        contract = _load_contract(db, locker.contract_id)
        if contract.status != ContractStatus.EXPIRED:
            continue

        settle_fee(db, locker.id, now=now)
        locker.status = StorageLockerStatus.CLAIMABLE
        locker.contract_id = None
        converted += 1
        logger.info(
            "Locker %s converted to CLAIMABLE -- contract %s expired without reaching full quantity",
            locker.id, contract.id,
        )

    db.flush()
    return {"converted": converted}


def settle_fee(
    db: Session, locker_id: uuid.UUID, *, now: Optional[datetime] = None,
    stored_units_override: Optional[int] = None,
) -> Dict[str, Any]:
    """Settle accrued rent for the elapsed period since `last_fee_
    settled_at`, at the locker's own `rent_rate` cr/unit/day (D16).
    Settle-on-access: this is the ONE settlement path -- there is no
    scheduler in S1 -- called both standalone and internally by
    deposit_cargo. Idempotent over a zero-elapsed-time re-settle (a
    no-op, no double-charge).

    `stored_units_override`: normally this reads the locker's CURRENT
    live stored-units count (as of this call). deposit_cargo's
    COMPLETING branch passes the PRE-final-deposit count explicitly here
    instead (D17, Max's ruling) -- see deposit_cargo's own docstring for
    why: by the time that branch settles, the final deposit row already
    exists, so the live count would over-count units that weren't
    actually sitting in the locker for the elapsed period being billed.

    TIME DOMAIN (verified, not assumed -- see this WO's own report):
    wall-clock, matching contract_service.py's OWN convention exactly
    (grepped: zero references to GAME_TIME_SCALE/scaled_deadline/
    game_time anywhere in that module -- contract deadlines and expiry
    sweeps all run on real datetime.now(UTC)). warp_gate_service.py uses
    GAME_TIME_SCALE for ITS OWN durations, but this locker is a contract-
    economy construct, so contract_service.py -- not warp_gate_service.py
    -- is the directly relevant precedent to match.

    D18 (Max's ruling) -- CONTINUOUS-ACCRUE-AND-ROUND-ONCE, closing the
    salami-slicing gap (many micro-settlements each individually
    rounding to 0cr) WITHOUT per-trip-taxing a legitimate multi-trip
    fulfillment (charging >=1cr on every trip regardless of how little
    time/units it actually represents). Mechanism: `accrued_fee` is a
    MONOTONICALLY INCREASING, never-reset cents-precision ledger of the
    full theoretical fee ever computed (NOT "money actually collected" --
    that reading was this WO's original design, superseded by D18). Each
    settlement adds this period's precise fee (rounded to cents via
    _round_credits, matching the column's own Numeric(19,2) precision --
    NOT left at arbitrary sub-cent precision, which the column can't
    hold between calls anyway) to that ledger, then charges only the
    WHOLE credits newly crossed since the last settlement (floor(new) -
    floor(old)) -- a tiny fractional contribution that doesn't cross a
    whole-credit boundary charges 0 THIS call but is never lost (it's
    still sitting in the ledger, waiting for a future call to push it
    over). A large single-trip contribution that crosses several whole
    credits at once charges all of them in one shot -- no double-billing
    across separate trips, no zero-billing an entire long-held locker.

    FLOOR-AND-FORGIVE KEPT (D17, matching contract_service.abandon()'s
    own exact convention, `player.credits = max(0, credits - penalty)`):
    if the owner can't fully afford the newly-crossed whole-credit
    charge, they pay what they can down to 0 -- the shortfall is
    forgiven, never tracked as debt. The ledger (`accrued_fee`) still
    advances by the FULL theoretical period fee regardless -- once a
    whole-credit boundary is crossed, it's considered "spent" (forgiven
    or collected) and is never re-billed on a later call; this is what
    keeps the no-debt invariant genuinely no-debt rather than deferred.

    Money math: Decimal throughout, ROUND_HALF_UP for the per-period
    fee (contract_service._round_credits, reused not re-derived), FLOOR
    for the whole-credit-crossing delta (never ROUND_HALF_UP there --
    a boundary is "crossed" only once fully reached). FLUSH-ONLY -- the
    route owns the commit, matching every other function in this
    module."""
    now = now or contract_service._now()

    locker = db.query(StorageLocker).filter(StorageLocker.id == locker_id).with_for_update().first()
    if locker is None:
        raise StorageNotFoundError(f"Locker {locker_id} not found")

    days_elapsed = (
        contract_service._as_decimal((now - locker.last_fee_settled_at).total_seconds())
        / _SECONDS_PER_DAY
    )
    if days_elapsed <= 0:
        # Re-settle over the same (or an out-of-order/clock-skew) instant
        # -- a clean no-op, never a negative or double charge.
        return {
            "locker_id": str(locker.id), "days_settled": 0, "units_settled": 0,
            "fee_charged": 0, "accrued_fee_total": float(locker.accrued_fee or 0),
        }

    stored_units = (
        stored_units_override if stored_units_override is not None else _stored_units(db, locker.id)
    )
    if stored_units <= 0:
        # Nothing stored -- no rent accrues, but the anchor still
        # advances so a later settle doesn't re-count this empty period.
        locker.last_fee_settled_at = now
        db.flush()
        return {
            "locker_id": str(locker.id), "days_settled": float(days_elapsed), "units_settled": 0,
            "fee_charged": 0, "accrued_fee_total": float(locker.accrued_fee or 0),
        }

    period_fee = contract_service._round_credits(
        Decimal(stored_units) * contract_service._as_decimal(locker.rent_rate) * days_elapsed
    )

    old_ledger = locker.accrued_fee or Decimal("0")
    new_ledger = old_ledger + period_fee
    old_whole = int(old_ledger.to_integral_value(rounding=ROUND_FLOOR))
    new_whole = int(new_ledger.to_integral_value(rounding=ROUND_FLOOR))
    charge_due = new_whole - old_whole  # D18: only the newly-crossed whole credits

    owner = contract_service._load_player(db, locker.owner_player_id, for_update=True)
    actual_charge = min(charge_due, owner.credits or 0) if charge_due > 0 else 0
    owner.credits = (owner.credits or 0) - actual_charge
    locker.accrued_fee = new_ledger  # ledger always advances by the full period fee
    locker.last_fee_settled_at = now
    db.flush()

    logger.info(
        "Locker %s settled %.4f days: %d units x %s/unit/day -> ledger %s (+%s), "
        "%d credits charged this call (owner %s)",
        locker.id, days_elapsed, stored_units, locker.rent_rate, new_ledger, period_fee,
        actual_charge, locker.owner_player_id,
    )
    return {
        "locker_id": str(locker.id), "days_settled": float(days_elapsed), "units_settled": stored_units,
        "fee_charged": actual_charge, "accrued_fee_total": float(new_ledger),
    }


def _prelock_deposit_guard(db: Session, locker_id: uuid.UUID, player_id: uuid.UUID) -> None:
    """WO-STORE-EXPIRY-CLAIMABLE Q2 mitigation-a (cipher MEDIUM-HIGH,
    orchestrator-ruled ADDRESS): a SCALAR-ONLY existence+ownership+
    station pre-check, called BEFORE _load_and_lock_deposit_targets ever
    acquires a row lock. Raises StorageError/StorageNotFoundError on
    failure; returns None on success (the caller re-reads everything for
    real via the locked path further down -- this function's ONLY job is
    to reject a doomed request as early and cheaply as possible).
    Extracted into its own function purely to keep _load_and_lock_
    deposit_targets under the ruff C901 gate.

    THE VECTOR THIS CLOSES: previously the wrong-station rejection only
    fired AFTER the Locker's with_for_update() lock had already been
    taken -- a player docked anywhere could POST /deposit against their
    OWN locker for free (fails the station check, rolls back, zero
    cargo/turns/credits cost, no rate limit), contending the Locker's
    lock on every attempt. Against the deposit-wins expiry gate (which
    defers a contract's expiry whenever its Locker is momentarily
    contended), this was a free, unlimited lever to probabilistically
    dodge the deadline penalty.

    SCALAR-ONLY, NOT FULL-ORM (mack's CRITICAL catch on the first
    version of this fix -- money-RMW identity-map poisoning, confirmed
    via a real-SQLAlchemy repro, not theoretical): an earlier draft did
    an unlocked FULL-ORM read of both Locker and Player here. That loads
    each into the session's identity map; the LATER "authoritative"
    locked re-read of the SAME PK (_load_and_lock_deposit_targets' own
    with_for_update(), and settle_fee's own internal Player re-load)
    returns the SAME cached Python object -- with_for_update() acquires
    the real DB lock but does NOT refresh attribute values onto an
    already-mapped object without `.populate_existing()`. The
    "authoritative" read was therefore silently STALE, and since this
    locker's owner IS the depositing player, that staleness reached
    settle_fee's own `owner.credits -= charge` -- a genuine lost-update
    on player.credits against any concurrently-committed change in the
    window. Reading ONLY columns (`Query(Model.col, ...)` -- a
    tuple/Row, never a mapped entity) never touches the identity map at
    all, so EVERY later full-ORM read (in _load_and_lock_deposit_targets
    and in settle_fee) is genuinely the FIRST load of that PK this
    transaction -- real, fresh, correctly FOR UPDATE-locked data. See
    construction_service.py:797 / citadel_service.py for this
    codebase's OWN existing `populate_existing()` convention for the
    cases that truly need a full-object refresh instead -- deliberately
    NOT used here, since a clean scalar pre-check avoids the identity
    map entirely rather than needing a refresh from it.

    OWNERSHIP-FIRST (cipher LOW-MED: closes a station-existence oracle
    -- a non-owner probing arbitrary locker_ids could previously
    distinguish "not yours" from "wrong station" per locker, leaking
    which station a given locker sits at). A non-owner gets the SAME
    generic "does not belong to you" rejection regardless of station --
    no station information ever reaches someone who isn't this locker's
    owner. `owner_player_id` is immutable exactly like `station_id`
    (grepped: zero writers anywhere, set once at creation), so this
    scalar read is TOCTOU-safe for the identical reason.

    TOCTOU-SAFE for both immutable scalars (verified, not assumed --
    grepped the whole tree for any `.station_id =` / `.owner_player_id
    =` targeting a StorageLocker: zero writers besides get_or_create_
    locker's own constructor call at creation): this pre-check's reads
    are GUARANTEED identical to the later locked reads -- no window for
    either to change between the two. The PLAYER's own position CAN
    legitimately change (they could undock/move), so this pre-check's
    read of current_port_id is a cheap, EARLY, non-authoritative reject
    only -- the ORIGINAL post-lock station check in _load_and_lock_
    deposit_targets stays in place UNCHANGED as the truly authoritative
    one (now genuinely fresh, since the identity map was never poisoned
    on the way here); this is additive, not a replacement. Status/
    contract checks stay exactly where they were -- this fix targets
    the specific zero-barrier "spam your OWN locker from anywhere"
    vector cipher confirmed, not a refactor of the whole guard chain."""
    pre_check_row = (
        db.query(StorageLocker.station_id, StorageLocker.owner_player_id)
        .filter(StorageLocker.id == locker_id)
        .first()
    )
    if pre_check_row is None:
        raise StorageNotFoundError(f"Locker {locker_id} not found")
    pre_check_station_id, pre_check_owner_id = pre_check_row
    if pre_check_owner_id != player_id:
        raise StorageError("This locker does not belong to you")
    pre_check_port_id = db.query(Player.current_port_id).filter(Player.id == player_id).scalar()
    if pre_check_port_id != pre_check_station_id:
        raise StorageError(
            "wrong_station: you must be docked at the locker's station to deposit"
        )


def _load_and_lock_deposit_targets(
    db: Session, locker_id: uuid.UUID, player_id: uuid.UUID,
) -> tuple:
    """Locks + validates the Locker, then loads its Contract, then locks
    + validates the Player -- the module's own Locker-then-Player order
    (see module docstring's LOCK ORDER section). Raises StorageError /
    StorageNotFoundError on any guard failure. Pulled out of deposit_
    cargo's own body purely to keep that function's cyclomatic
    complexity under the ruff C901 gate (genuinely enforced -- `C90` is
    in this project's pyproject.toml `[tool.ruff] select`, not just
    available) -- no behavior change from the pre-extraction inline
    version.

    Calls _prelock_deposit_guard() FIRST, before any lock -- see that
    function's own docstring for the full Q2 mitigation-a story (the
    free-spam vector it closes, the identity-map-poisoning bug its
    scalar-only design avoids, and the ownership-first station-oracle
    fix). Everything below this point is the ORIGINAL locked flow,
    unchanged."""
    _prelock_deposit_guard(db, locker_id, player_id)

    locker = db.query(StorageLocker).filter(StorageLocker.id == locker_id).with_for_update().first()
    if locker is None:
        raise StorageNotFoundError(f"Locker {locker_id} not found")
    if locker.owner_player_id != player_id:
        raise StorageError("This locker does not belong to you")
    if locker.status != StorageLockerStatus.ACTIVE:
        raise StorageError(
            f"locker_not_active: locker {locker.id} is '{locker.status.value}', not 'active'"
        )
    if locker.contract_id is None:
        raise StorageError("This locker is not tied to a contract")

    contract = _load_contract(db, locker.contract_id)
    if contract.status != ContractStatus.ACCEPTED:
        raise StorageError(
            f"stale_status: contract {contract.id} is '{contract.status.value}', not 'accepted'"
        )

    player = contract_service._load_player(db, player_id, for_update=True)
    if not player.is_docked or player.current_port_id != locker.station_id:
        raise StorageError(
            "wrong_station: you must be docked at the locker's station to deposit"
        )
    if locker.station_id != contract.destination_station_id:
        # Structurally unreachable via get_or_create_locker (which always
        # pins locker.station_id = contract.destination_station_id at
        # creation) -- checked directly rather than assumed, so a future
        # locker-relocation feature can never silently violate this
        # invariant without a loud rejection here.
        raise StorageError(
            "wrong_station: the locker's station no longer matches the contract's destination"
        )

    return locker, contract, player


def deposit_cargo(
    db: Session, locker_id: uuid.UUID, player_id: uuid.UUID, quantity: int,
) -> Dict[str, Any]:
    """Deposit `quantity` units of the locker's contract commodity from
    the player's current ship's cargo into the locker (a
    ContractCargoDeposit audit row). Auto-completes the contract -- via
    contract_service.complete(), see module docstring's "CARGO BRIDGE" --
    the moment the locker's accumulated deposits reach the contract's
    required quantity. FLUSH-ONLY; the route owns the commit.

    D17 (Max's ruling, PAYOUT-then-settle) -- when THIS deposit is the
    one that completes the contract, rent is settled AFTER contract_
    service.complete()'s payout credits the player, not before. Settling
    first would floor the final bill to near-zero at the player's
    poorest moment (right before they get paid), making the fee inert
    for exactly the case it exists to charge. Every OTHER (non-
    completing) deposit keeps the original settle-before-deposit
    ordering -- there's no payout event to reorder around."""
    if quantity <= 0:
        raise StorageError("invalid_quantity: deposit quantity must be positive")

    # Locker locked FIRST, Player locked SECOND -- see module docstring's
    # LOCK ORDER section (and _load_and_lock_deposit_targets's own).
    locker, contract, player = _load_and_lock_deposit_targets(db, locker_id, player_id)

    # Peek ahead: will THIS deposit push the locker to full quantity?
    # Both sides of this equality are captured atomically under the
    # Locker row lock already held above -- no concurrent writer can
    # insert a ContractCargoDeposit between this read and the one below,
    # so old_stored_units + quantity is guaranteed to equal the real
    # post-deposit accumulated count computed further down.
    quantity_required = int(contract.quantity or 0)
    old_stored_units = _stored_units(db, locker.id)
    will_complete = (old_stored_units + quantity) >= quantity_required

    settlement: Optional[Dict[str, Any]] = None
    if not will_complete:
        # WO-STORE-FEE-ACCRUAL: settle-before-deposit ordering (D17
        # unchanged for the non-completing case -- no payout event to
        # reorder around). Settles rent for the OLD stored-units count
        # (whatever was sitting in the locker BEFORE this deposit) over
        # the elapsed period since last_fee_settled_at, then advances
        # the anchor to now -- so the period this NEW deposit's units
        # are about to join never gets back-charged for time they
        # weren't actually stored. settle_fee re-locks the Locker row
        # it's already holding (a harmless idempotent re-acquire, same
        # session) then locks the Player row -- consistent with this
        # function's own Locker-then-Player order.
        settlement = settle_fee(db, locker.id, now=contract_service._now())

    # Ship locked THIRD, for the cargo RMW.
    ship = (
        db.query(Ship)
        .filter(Ship.id == player.current_ship_id, Ship.owner_id == player.id)
        .with_for_update()
        .first()
    )
    if ship is None:
        raise StorageError("No active ship to deposit cargo from")
    cargo = ship.cargo if isinstance(ship.cargo, dict) else {}
    contents = dict(cargo.get("contents") or {})
    held = int(contents.get(contract.commodity_type, 0) or 0)
    if held < quantity:
        raise StorageError(
            f"insufficient_cargo: you have {held} {contract.commodity_type}, "
            f"tried to deposit {quantity}"
        )

    # --- All guards passed -- mutate. ---
    contents[contract.commodity_type] = held - quantity
    cargo["contents"] = contents
    cargo["used"] = sum(int(q) for q in contents.values() if isinstance(q, (int, float)))
    ship.cargo = cargo
    flag_modified(ship, "cargo")

    deposit_row = ContractCargoDeposit(
        id=uuid.uuid4(), locker_id=locker.id, commodity=contract.commodity_type,
        quantity=quantity, deposited_by=player_id,
    )
    db.add(deposit_row)
    db.flush()

    accumulated = _stored_units(db, locker.id)

    completed = False
    complete_result: Optional[Dict[str, Any]] = None
    if accumulated >= quantity_required:
        # CARGO BRIDGE -- see module docstring. Materialize the full
        # required quantity onto the ship's cargo so contract_service.
        # complete()'s own (unmodified) cargo check + decrement passes;
        # net effect on the ship is zero once complete() finishes, all
        # inside this same flush/transaction.
        #
        # FRAGILE COUPLING -- verified against contract_service.complete()'s
        # actual source (WO-STORE-DEPOSIT-FLOW report) rather than assumed;
        # re-verify these three facts if complete() is ever touched:
        #   1. It decrements the ship's cargo by EXACTLY int(contract.
        #      quantity or 0) -- the same value injected below. If that
        #      computation ever changes, net-zero breaks.
        #   2. It ONLY reads/validates/decrements cargo -- no other side
        #      effect keyed off cargo (a history log, a value metric) that
        #      would see and record this phantom amount.
        #   3. It never capacity-checks cargo["used"] against cargo
        #      ["capacity"] -- confirmed no "capacity" reference anywhere
        #      in contract_service.py. If a capacity guard is ever added
        #      to complete(), this transient over-capacity injection would
        #      need a different bridge (or complete() would need a
        #      cargo_source parameter instead -- the fallback design,
        #      deliberately not built here to keep this change isolated).
        # Also confirmed: Ship's only mapper-level event listeners
        # (ship_registry.py) fire on before_insert/after_insert only --
        # never on an UPDATE to an existing row's cargo, so this injection
        # (an UPDATE) can't trigger them.
        bridge_cargo = ship.cargo if isinstance(ship.cargo, dict) else {}
        bridge_contents = dict(bridge_cargo.get("contents") or {})
        bridge_contents[contract.commodity_type] = (
            int(bridge_contents.get(contract.commodity_type, 0) or 0) + quantity_required
        )
        bridge_cargo["contents"] = bridge_contents
        bridge_cargo["used"] = sum(
            int(q) for q in bridge_contents.values() if isinstance(q, (int, float))
        )
        ship.cargo = bridge_cargo
        flag_modified(ship, "cargo")
        db.flush()

        # contract_service.complete() is the canonical completer -- never
        # reimplemented here. Any exception it raises propagates straight
        # through this function uncaught: the route's existing rollback
        # then discards the WHOLE deposit attempt, including the
        # installment that would have triggered completion (a clean
        # all-or-nothing failure is safer than a locker silently stuck at
        # exactly full quantity with no way to re-trigger completion).
        complete_result = contract_service.complete(db, contract.id, player_id)
        locker.status = StorageLockerStatus.RELEASED
        completed = True

        # D17 (Max's ruling): settle the FINAL rent period AFTER the
        # completion payout above has already credited the player, not
        # before -- see this function's own docstring. stored_units_
        # override=old_stored_units: the units THIS deposit just added
        # never actually sat in the locker accruing rent -- they arrive
        # and are immediately bridged back out by complete() above, so
        # billing them here would charge for storage time that never
        # happened. settle_fee re-locks the Locker/Player rows this
        # function already holds (harmless idempotent re-acquire).
        settlement = settle_fee(
            db, locker.id, now=contract_service._now(), stored_units_override=old_stored_units,
        )
        logger.info(
            "Locker %s reached full quantity (%d/%d %s) -- contract %s auto-completed by player %s",
            locker.id, accumulated, quantity_required, contract.commodity_type, contract.id, player_id,
        )
    else:
        logger.info(
            "Player %s deposited %d %s into locker %s (%d/%d)",
            player_id, quantity, contract.commodity_type, locker.id, accumulated, quantity_required,
        )

    if settlement is None:
        # Defensive fallback -- structurally unreachable given the Locker
        # row lock (the peek's old_stored_units + quantity == accumulated
        # invariant, see its own comment above) but money code doesn't
        # get to silently NoneType-crash if that invariant is ever
        # violated by a future edit.
        settlement = settle_fee(db, locker.id, now=contract_service._now())

    return {
        "locker_id": str(locker.id),
        "deposited": quantity,
        "accumulated": accumulated,
        "quantity_required": quantity_required,
        "fee_charged": settlement["fee_charged"],
        "completed": completed,
        "complete_result": complete_result,
    }


def _load_and_lock_retrieve_targets(
    db: Session, locker_id: uuid.UUID, player_id: uuid.UUID, quantity: Optional[int],
) -> tuple:
    """Locks + validates the Locker, then the Player -- retrieve_
    claimable_cargo's own Locker-then-Player order. Raises StorageError /
    StorageNotFoundError on any guard failure. Pulled out purely to keep
    retrieve_claimable_cargo's own cyclomatic complexity under the ruff
    C901 gate, same reasoning as _load_and_lock_deposit_targets."""
    locker = db.query(StorageLocker).filter(StorageLocker.id == locker_id).with_for_update().first()
    if locker is None:
        raise StorageNotFoundError(f"Locker {locker_id} not found")
    if locker.owner_player_id != player_id:
        raise StorageError("This locker does not belong to you")
    if locker.status != StorageLockerStatus.CLAIMABLE:
        raise StorageError(
            f"locker_not_claimable: locker {locker.id} is '{locker.status.value}', not 'claimable'"
        )

    player = contract_service._load_player(db, player_id, for_update=True)
    if not player.is_docked or player.current_port_id != locker.station_id:
        raise StorageError(
            "wrong_station: you must be docked at the locker's station to retrieve"
        )
    if quantity is not None and quantity <= 0:
        raise StorageError("invalid_quantity: retrieve quantity must be positive")

    return locker, player


def _resolve_retrieve_take(quantity: Optional[int], available: int, capacity_left: int) -> int:
    """How many units THIS retrieve call actually moves onto the ship --
    see retrieve_claimable_cargo's own docstring for the partial-retrieve
    design (explicit `quantity` validated against both what's stored and
    what fits; omitted `quantity` greedily takes as much as fits).
    Extracted alongside _load_and_lock_retrieve_targets for the same
    C901 reason."""
    if quantity is not None:
        if quantity > available:
            raise StorageError(
                f"insufficient_stored: locker holds {available}, tried to retrieve {quantity}"
            )
        if quantity > capacity_left:
            raise StorageError(
                f"insufficient_cargo_capacity: {capacity_left} free, tried to retrieve {quantity}"
            )
        return quantity
    take = min(available, capacity_left)
    if take <= 0:
        raise StorageError("insufficient_cargo_capacity: no free cargo space to retrieve into")
    return take


def retrieve_claimable_cargo(
    db: Session, locker_id: uuid.UUID, player_id: uuid.UUID, quantity: Optional[int] = None,
) -> Dict[str, Any]:
    """WO-STORE-EXPIRY-CLAIMABLE: retrieves cargo from a CLAIMABLE locker
    (see sweep_expired_lockers) back onto the player's current ship.

    CAPACITY (Max's brief flagged this as the open design call --
    PARTIAL RETRIEVE, not reject-if-over): `quantity` is OPTIONAL. Omit
    it to take as much as fits in one trip, up to everything stored; a
    ship too small to take it all in a single trip retrieves the rest on
    a LATER call -- the locker simply stays CLAIMABLE with the leftover,
    still accruing rent. This mirrors the deposit side's own multi-trip
    design (this entire kernel's reason to exist -- a small ship
    shouldn't be permanently locked out of cargo it legitimately owns
    just because it can't carry it all at once). Pass an explicit
    `quantity` to take a specific smaller amount instead (validated
    against BOTH what's stored and what fits); the retrieve route's own
    request body makes this an optional field for exactly this reason.
    Reject-if-over was considered and rejected: it would permanently
    strand a large claimable balance behind "go find/rent a bigger ship"
    with no in-feature way out, for cargo the player already rightfully
    owns.

    Released once EMPTY (remaining <= 0 after this call) -- an emptied
    claimable locker has nothing left to justify existing (and nothing
    left to accrue rent against); stays CLAIMABLE otherwise.

    LOCK ORDER: Locker -> Player -> Ship, matching deposit_cargo's own
    order exactly (retrieve is the mirror-image operation; reusing the
    SAME order means no new AB-BA surface is introduced). settle_fee's
    own re-lock of Locker/Player is the same harmless same-session
    re-acquire used throughout this module. FLUSH-ONLY -- the route owns
    the commit."""
    locker, player = _load_and_lock_retrieve_targets(db, locker_id, player_id, quantity)

    # Rent settled up to now BEFORE computing what's retrievable -- the
    # player owes rent on everything stored right up to this instant,
    # regardless of how much they're about to walk away with.
    settlement = settle_fee(db, locker.id, now=contract_service._now())

    available = _stored_units(db, locker.id)
    if available <= 0:
        # Already fully retrieved on an earlier call -- release the
        # (now-empty) locker rather than leaving a claimable husk with
        # zero cargo still accruing rent against nothing.
        locker.status = StorageLockerStatus.RELEASED
        db.flush()
        return {
            "locker_id": str(locker.id), "retrieved": 0, "commodity": None,
            "remaining": 0, "released": True, "fee_charged": settlement["fee_charged"],
        }

    # Ship locked THIRD, for the cargo RMW.
    ship = (
        db.query(Ship)
        .filter(Ship.id == player.current_ship_id, Ship.owner_id == player.id)
        .with_for_update()
        .first()
    )
    if ship is None:
        raise StorageError("No active ship to retrieve cargo into")

    cargo = ship.cargo if isinstance(ship.cargo, dict) else {}
    capacity = int(cargo.get("capacity", 0) or 0)
    used = int(cargo.get("used", 0) or 0)
    capacity_left = max(0, capacity - used)
    take = _resolve_retrieve_take(quantity, available, capacity_left)

    commodity = _consume_deposits(db, locker.id, take)

    contents = dict(cargo.get("contents") or {})
    contents[commodity] = int(contents.get(commodity, 0) or 0) + take
    cargo["contents"] = contents
    cargo["used"] = used + take
    ship.cargo = cargo
    flag_modified(ship, "cargo")

    remaining = available - take
    if remaining <= 0:
        locker.status = StorageLockerStatus.RELEASED
    db.flush()

    logger.info(
        "Player %s retrieved %d %s from claimable locker %s (%d remaining)",
        player_id, take, commodity, locker.id, remaining,
    )
    return {
        "locker_id": str(locker.id), "retrieved": take, "commodity": commodity,
        "remaining": remaining, "released": remaining <= 0, "fee_charged": settlement["fee_charged"],
    }

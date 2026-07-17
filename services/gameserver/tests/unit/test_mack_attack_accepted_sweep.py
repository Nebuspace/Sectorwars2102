"""ADVERSARIAL QA -- mack-econ-deadline attack pass on WO-DRIFT-econ-
accepted-deadline-expiry (sweep_expired_accepted_contracts, contract_
service.py:514-617). Read-only attack tests, NOT part of the feature's own
proof suite -- these exist to attack the sweep's own claims: (1) the
while-loop's "raced row -> continue -> next fresh SELECT excludes it, no
infinite loop" claim, (2) no double-charge/refund across a live
complete()/cancel_player_contract() racing the sweep, (3) an economic-
outcome-divergence finding in cancel_player_contract's missing deadline
gate -- HIGH, fixed in this same WO's revise (see TestCancelPlayerContract
RacesTheSweepWithDivergentEconomics's own docstring for the before/after).
DB-free -- same real SQLAlchemy WHERE-clause interpreter convention as
test_contract_service.py / test_contract_escrow.py.
"""
from __future__ import annotations

import operator
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from typing import Any, Dict, List, Optional

import pytest
from sqlalchemy.exc import OperationalError
from sqlalchemy.sql.operators import in_op

from src.models.contract import Contract, ContractEscrowState, ContractIssuerType, ContractStatus
from src.services import contract_service
from src.services.contract_service import ContractConflictError

# --- WHERE-clause interpreter (real SQLAlchemy clauses, not scripted) --- #

def _match(row: Any, cond: Any) -> bool:
    col_name = cond.left.key
    row_val = getattr(row, col_name, None)
    if cond.operator is operator.eq:
        return row_val == cond.right.value
    if cond.operator is in_op:
        return row_val in cond.right.value
    if cond.operator is operator.lt:
        return row_val < cond.right.value
    if cond.operator is operator.ne:
        # WO-CONTRACT-57 addendum: _bulk_expire_remaining_posted_contracts
        # now excludes the per-candidate loop's own eligible set via a
        # `!=` predicate (issuer_type != PLAYER / escrow_state != HELD)
        # instead of matching the earlier missing operator.
        return row_val != cond.right.value
    raise NotImplementedError(f"unsupported operator {cond.operator!r}")


class _FakeResult:
    def __init__(self, rowcount: int) -> None:
        self.rowcount = rowcount


class _FakeQuery:
    def __init__(self, rows: List[Any], criteria: Optional[List[Any]] = None,
                 lock_log: Optional[List[Any]] = None,
                 lock_failures: Optional[Dict[Any, Exception]] = None) -> None:
        self._rows = rows
        self._criteria = criteria or []
        self._lock_log = lock_log
        # WO-CONTRACT-57 addendum: id -> Exception to RAISE instead of
        # locking -- models a real Postgres SET LOCAL lock_timeout firing
        # (SQLSTATE 55P03) or a genuine deadlock (40P01) at the exact
        # `with_for_update()` call, DB-free. Only meaningful for the
        # Player query (see _RacySession.query()); Contract queries never
        # pass this.
        self._lock_failures = lock_failures

    def filter(self, *conditions: Any) -> "_FakeQuery":
        return _FakeQuery(
            self._rows, self._criteria + list(conditions), self._lock_log, self._lock_failures,
        )

    def with_for_update(self) -> "_FakeQuery":
        # WO-ECON-CONTRACT-MONEY-HARDEN: no-op passthrough for the row
        # lock itself (a single-threaded fake can't simulate a real
        # Postgres lock -- see contract_service._load_player's own
        # docstring), BUT records the locked id's ACQUISITION ORDER when
        # the session was built with a lock_log -- this is what
        # TestDualLockConsistentOrdering below asserts against, proving
        # the code's ordering logic without needing real concurrency.
        for cond in self._criteria:
            if getattr(cond.left, "key", None) == "id":
                # WO-CONTRACT-57 addendum: a registered lock_failures
                # entry fires BEFORE the lock_log append below -- a real
                # failed lock acquisition never succeeds, so it must never
                # be recorded as "locked" either.
                if self._lock_failures is not None and cond.right.value in self._lock_failures:
                    raise self._lock_failures[cond.right.value]
                if self._lock_log is not None:
                    self._lock_log.append(cond.right.value)
        return self

    def populate_existing(self) -> "_FakeQuery":
        # WO-MONEY-REREAD-CLASS: no-op passthrough -- _load_player /
        # _load_two_players_for_update now chain .populate_existing() ahead
        # of .with_for_update() on every for_update=True re-read. Deliberately
        # does NOT touch _lock_log (that recording lives in with_for_update(),
        # called right after this in the real chain, so acquisition-order
        # assertions below are unaffected). Inherited by _RacyContractQuery
        # too. See money-reread-class-fake-query-passthrough in mack's
        # project memory.
        return self

    def first(self) -> Any:
        for row in self._rows:
            if all(_match(row, c) for c in self._criteria):
                return row
        return None

    def all(self) -> List[Any]:
        # WO-STORE-EXPIRY-CLAIMABLE + D19 / ticket #43 fast-follow:
        # sweep_expired_accepted_contracts gathers its candidates upfront
        # via `.all()` now (not a repeated `.first()` while-loop) -- this
        # fake never grew the method, silently stranding every test in
        # this file that drives the real sweep (AttributeError before any
        # assertion ever ran). See _RacyContractQuery's own `.all()`
        # override for how the race-injection mechanism adapts to this
        # shape (this base implementation never fires a race -- only used
        # directly by the Player query, which has none).
        return [row for row in self._rows if all(_match(row, c) for c in self._criteria)]


class _RacyContractQuery(_FakeQuery):
    """Wraps the plain Contract query. On `.first()`, if the returned row's
    id has a pending race registered on the owning session, fires it
    (once, then discards it) BEFORE returning -- modeling a concurrent
    committer's write landing in the exact instant between the sweep's
    SELECT and its own guarded UPDATE a statement later. Also counts every
    SELECT and trips a hard circuit breaker past MAX_SELECTS, so a genuine
    infinite loop fails the test loudly and fast instead of hanging the
    run.

    WO-STORE-EXPIRY-CLAIMABLE + D19 / ticket #43 fast-follow: `sweep_
    expired_accepted_contracts` gathers its candidates via ONE upfront
    `.all()` now, not a repeated `.first()` while-loop -- `.first()`'s
    race logic above is UNREACHABLE for that sweep today (kept, unchanged,
    for the OTHER functions in this file that still call `_load_contract`
    -> a real `.first()`, e.g. `complete()`/`abandon()`/`accept()`).
    `.all()` gets its OWN parallel override below: fires every pending
    race for every row IN THE GATHERED LIST, immediately, once, right as
    the single SELECT returns them. This is observably IDENTICAL to
    firing each race individually at that row's own later per-row turn --
    the sweep's per-row guarded UPDATE always re-checks the LIVE row via
    a fresh WHERE-clause match (see `_RacySession.execute()`), never the
    possibly-stale Python object gathered by `.all()` -- so a race fired
    early still correctly fails to match once the per-row loop reaches
    that candidate's own guarded UPDATE."""

    MAX_SELECTS = 1000

    def __init__(self, rows: List[Any], criteria: List[Any], session: "_RacySession") -> None:
        super().__init__(rows, criteria)
        self._session = session

    def filter(self, *conditions: Any) -> "_RacyContractQuery":
        return _RacyContractQuery(self._rows, self._criteria + list(conditions), self._session)

    def first(self) -> Any:
        self._session.select_calls += 1
        if self._session.select_calls > self.MAX_SELECTS:
            raise AssertionError(
                "sweep_expired_accepted_contracts spun past MAX_SELECTS under "
                "the injected race -- infinite loop"
            )
        row = super().first()
        if row is not None and row.id in self._session.races:
            mutate = self._session.races.pop(row.id)
            mutate(row)
        return row

    def all(self) -> List[Any]:
        self._session.select_calls += 1
        rows = super().all()
        for row in rows:
            if row.id in self._session.races:
                mutate = self._session.races.pop(row.id)
                mutate(row)
        return rows


class _FakeNestedTransaction:
    """WO-ECON-CONTRACT-MONEY-HARDEN: no-op savepoint passthrough -- see
    test_contract_service.py's sibling copy of this class for the full
    rationale (proves the sweep's own try/except catches and continues
    past a failing row; does not attempt to fake real SAVEPOINT rollback
    of Python attribute mutations)."""

    def __enter__(self) -> "_FakeNestedTransaction":
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> bool:
        return False


class _RacySession:
    """Same execute()/flush()/commit() contract as the sibling test files'
    _FakeSession, plus a `races` map (contract_id -> mutator, applied once
    on that row's next SELECT), a SELECT call counter, and a
    `player_lock_log` list recording the ORDER Player rows were locked in
    (WO-ECON-CONTRACT-MONEY-HARDEN Mack HIGH #1's dual-lock ordering --
    see TestDualLockConsistentOrdering)."""

    def __init__(self, *, contracts: Optional[List[Any]] = None, players: Optional[List[Any]] = None,
                 player_lock_failures: Optional[Dict[Any, Exception]] = None) -> None:
        self.contracts = contracts or []
        self.players = players or []
        self.flush_calls = 0
        self.races: Dict[Any, Any] = {}
        self.select_calls = 0
        self.player_lock_log: List[Any] = []
        # WO-CONTRACT-57 addendum: id -> Exception a Player lock attempt
        # should raise instead of succeeding (see _FakeQuery's own note).
        self.player_lock_failures = player_lock_failures

    def query(self, model: Any) -> _FakeQuery:
        if model is Contract:
            return _RacyContractQuery(self.contracts, [], self)
        from src.models.player import Player
        if model is Player:
            return _FakeQuery(
                self.players, lock_log=self.player_lock_log, lock_failures=self.player_lock_failures,
            )
        raise AssertionError(f"unexpected query for {model!r}")

    def execute(self, stmt: Any) -> _FakeResult:
        values = {col.name: bind.value for col, bind in stmt._values.items()}
        matched = 0
        for row in self.contracts:
            if all(_match(row, c) for c in stmt._where_criteria):
                for k, v in values.items():
                    setattr(row, k, v)
                matched += 1
        return _FakeResult(matched)

    def flush(self) -> None:
        self.flush_calls += 1

    def begin_nested(self) -> _FakeNestedTransaction:
        return _FakeNestedTransaction()

    def commit(self) -> None:
        raise AssertionError("service functions are flush-only -- the route/scheduler commits")


# --- fixtures ------------------------------------------------------------ #

def _player(**overrides: Any) -> SimpleNamespace:
    base = dict(id=uuid.uuid4(), credits=5000, is_docked=False, current_port_id=None, current_ship=None)
    base.update(overrides)
    return SimpleNamespace(**base)


# WO-CONTRACT-1b-CLAIM-SAFETY: sweep_expired_accepted_contracts now
# unconditionally reads insurance_coverage_tier/insurance_pool_reserve on
# every candidate (the claim-offset engine) -- required on both fixtures
# below, not just insurance-specific tests.
_INSURANCE_DEFAULTS = dict(
    insurance_coverage_tier=None, insurance_premium_paid=Decimal("0"),
    insurance_claim_filed=False, insurance_pool_reserve=Decimal("0"),
)


def _npc_contract(**overrides: Any) -> SimpleNamespace:
    base = dict(
        id=uuid.uuid4(), issuer_type=ContractIssuerType.NPC, issuer_id=uuid.uuid4(),
        acceptor_player_id=None, origin_station_id=uuid.uuid4(), destination_station_id=uuid.uuid4(),
        commodity_type="ore", quantity=50, status=ContractStatus.ACCEPTED,
        payment=Decimal("1000.00"), penalty=Decimal("1000.00"), acceptance_fee_pct=Decimal("2.0"),
        escrow_amount=Decimal("0"), escrow_state=ContractEscrowState.HELD,
        deadline=datetime(2026, 1, 2, tzinfo=UTC), posted_at=datetime(2026, 1, 1, tzinfo=UTC),
        accepted_at=datetime(2026, 1, 1, 1, tzinfo=UTC), completed_at=None,
        **_INSURANCE_DEFAULTS,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def _player_contract(**overrides: Any) -> SimpleNamespace:
    """A PLAYER-issued ACCEPTED contract, built directly (bypassing
    post_player_contract/accept) exactly like test_contract_escrow.py's
    own TestDoubleReleaseImpossibility / already-refunding fixtures."""
    base = dict(
        id=uuid.uuid4(), issuer_type=ContractIssuerType.PLAYER, issuer_id=uuid.uuid4(),
        acceptor_player_id=None, origin_station_id=uuid.uuid4(), destination_station_id=uuid.uuid4(),
        commodity_type="ore", quantity=50, status=ContractStatus.ACCEPTED,
        payment=Decimal("1000.00"), penalty=Decimal("1000.00"), acceptance_fee_pct=Decimal("2.0"),
        escrow_amount=Decimal("1000.00"), escrow_state=ContractEscrowState.HELD,
        deadline=datetime(2026, 1, 2, tzinfo=UTC), posted_at=datetime(2026, 1, 1, tzinfo=UTC),
        posting_stations=[uuid.uuid4()], accepted_at=datetime(2026, 1, 1, 1, tzinfo=UTC), completed_at=None,
        **_INSURANCE_DEFAULTS,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


_NOW = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)


@pytest.mark.unit
class TestNoInfiniteLoopUnderInjectedRace:
    """Attack goal 1, UPDATED for the `.all()`-based candidate-gathering
    shape (WO-STORE-EXPIRY-CLAIMABLE + D19 -- see sweep_expired_accepted_
    contracts's own docstring for why the OLD `while True: .first()` loop
    was replaced): the original "infinite loop" concern is now
    STRUCTURALLY impossible -- there is exactly ONE SELECT per sweep call
    (the upfront `.all()`), so there is nothing left to re-select. These
    tests now pin the SIMPLER, still load-bearing property that replaced
    it: a row that races away (via a live complete()/abandon() on another
    session) in the window between that one upfront SELECT and its OWN
    later per-row guarded UPDATE is skipped cleanly -- no side effect, no
    crash, siblings unaffected -- and exactly one SELECT ever fires,
    regardless of how many candidates race away."""

    def test_raced_row_is_skipped_once_never_reselected_loop_terminates(self) -> None:
        acceptor1 = _player(credits=5000)
        acceptor2 = _player(credits=5000)
        # c1 will race away to CANCELLED (a concurrent abandon() landing)
        # right as the sweep's upfront `.all()` SELECT returns it, before
        # its own per-row guarded UPDATE runs (see _RacyContractQuery.all()
        # -- fires every pending race immediately for every gathered row;
        # observably identical to firing exactly at that row's own later
        # turn, since the per-row guarded UPDATE always re-checks the LIVE
        # row, never the possibly-stale gathered Python object).
        c1 = _npc_contract(deadline=_NOW - timedelta(hours=1), acceptor_player_id=acceptor1.id, penalty=Decimal("300"))
        c2 = _npc_contract(deadline=_NOW - timedelta(minutes=1), acceptor_player_id=acceptor2.id, penalty=Decimal("200"))
        db = _RacySession(contracts=[c1, c2], players=[acceptor1, acceptor2])
        db.races[c1.id] = lambda row: setattr(row, "status", ContractStatus.CANCELLED)

        result = contract_service.sweep_expired_accepted_contracts(db, now=_NOW)

        # c1 raced away -- the guarded UPDATE's WHERE (status==ACCEPTED)
        # matches 0 rows once the race has landed, so the sweep's `continue`
        # fires and NO side effect touches it.
        assert c1.status == ContractStatus.CANCELLED  # untouched by the sweep
        assert acceptor1.credits == 5000  # NOT penalized -- raced row never reaches that code

        # c2 is entirely unaffected by c1's race -- processed normally.
        assert c2.status == ContractStatus.EXPIRED
        assert acceptor2.credits == 4800

        assert result == {"expired": 1}  # only the genuine winner counted
        # Exactly 2 SELECTs: the single upfront `.all()` (1), plus ONE
        # `.first()` from `_refresh_contract_insurance_snapshot` (WO-
        # CONTRACT-2b-HOLD-ESCROW) for c2 -- the one candidate that
        # actually clears its guarded UPDATE (c1's own raced-away guard
        # fails first, so it `continue`s BEFORE ever reaching that
        # refresh -- no second SELECT for the raced row). The OLD while-
        # loop's per-iteration re-SELECT of a RACED row no longer exists
        # structurally; that's what this count still pins -- 2, not 3+.
        assert db.select_calls == 2

    def test_every_candidate_races_away_loop_still_terminates_at_zero(self) -> None:
        """Worst case: EVERY due row races away in the window before its
        own per-row guarded UPDATE. Proves the `.all()`-gathered candidate
        list is processed to completion regardless -- no candidate is ever
        re-fetched or re-attempted, so a 100%-raced batch still terminates
        cleanly with a correct `{"expired": 0}`, not a hang or crash."""
        acceptors = [_player(credits=5000) for _ in range(5)]
        contracts = [
            _npc_contract(deadline=_NOW - timedelta(minutes=i + 1), acceptor_player_id=a.id, penalty=Decimal("100"))
            for i, a in enumerate(acceptors)
        ]
        db = _RacySession(contracts=contracts, players=acceptors)
        for c in contracts:
            db.races[c.id] = lambda row: setattr(row, "status", ContractStatus.COMPLETED)

        result = contract_service.sweep_expired_accepted_contracts(db, now=_NOW)

        assert result == {"expired": 0}  # every single one raced away
        for c in contracts:
            assert c.status == ContractStatus.COMPLETED  # sweep never touched them further
        for a in acceptors:
            assert a.credits == 5000  # nobody penalized -- all raced away before the guard
        # Exactly 1 SELECT regardless of how many of the 5 candidates
        # raced away -- the upfront `.all()` gathers them all in one shot.
        assert db.select_calls == 1


@pytest.mark.unit
class TestCompleteVsSweepInterleaving:
    """Attack goal 3: a contract the acceptor completes at (effectively) the
    same instant the sweep expires it. Both orderings modeled -- the row-
    lock-backed guarded UPDATE means only ONE of {complete, sweep} can ever
    be the one that actually mutates status; verify neither ordering
    produces an incoherent state (EXPIRED-but-paid, or COMPLETED-but-
    penalized-and-refunded)."""

    def _setup(self) -> tuple:
        issuer = _player(credits=5000)
        acceptor = _player(credits=5000, is_docked=True)
        from src.models.ship import Ship, ShipType
        acceptor.current_ship = Ship(
            id=uuid.uuid4(), name="Freighter", type=ShipType.LIGHT_FREIGHTER, sector_id=1,
            is_destroyed=False, cargo={"capacity": 500, "used": 50, "contents": {"ore": 50}},
        )
        contract = _player_contract(
            issuer_id=issuer.id, acceptor_player_id=acceptor.id,
            deadline=_NOW - timedelta(minutes=1),  # already past -- both paths are "eligible"
        )
        acceptor.current_port_id = contract.destination_station_id
        db = _RacySession(contracts=[contract], players=[issuer, acceptor])
        return db, issuer, acceptor, contract

    def test_complete_wins_first_sweep_then_sees_nothing_to_expire(self) -> None:
        db, issuer, acceptor, contract = self._setup()

        # complete()'s guarded UPDATE lands first (models the delivery
        # request's commit landing microseconds before the scheduler tick).
        contract_service.complete(db, contract.id, acceptor.id, now=_NOW)
        assert contract.status == ContractStatus.COMPLETED
        paid_balance = acceptor.credits
        # Fixture is built directly (bypassing accept()), so no acceptance
        # fee was ever sunk here -- just the payment landing on top of the
        # starting balance.
        assert paid_balance == 5000 + 1000

        result = contract_service.sweep_expired_accepted_contracts(db, now=_NOW + timedelta(minutes=5))

        assert result == {"expired": 0}  # the sweep's SELECT (status==ACCEPTED) never sees it
        assert contract.status == ContractStatus.COMPLETED  # untouched
        assert acceptor.credits == paid_balance  # no penalty applied on top of the payout
        assert contract.escrow_state == ContractEscrowState.RELEASED  # not re-refunded to issuer

    def test_sweep_wins_first_late_complete_attempt_409s_no_incoherent_state(self) -> None:
        db, issuer, acceptor, contract = self._setup()

        # sweep's guarded UPDATE lands first (models the scheduler tick
        # landing microseconds before the acceptor's in-flight delivery
        # request reaches the DB).
        result = contract_service.sweep_expired_accepted_contracts(db, now=_NOW)
        assert result == {"expired": 1}
        assert contract.status == ContractStatus.EXPIRED
        penalized_balance = acceptor.credits
        assert penalized_balance == 5000 - 1000  # penalty charged
        # WO-CONTRACT-2b-HOLD-ESCROW: escrow is HELD, not refunded, at
        # expiry -- superseded assertion, see module docstring.
        assert issuer.credits == 5000

        # The acceptor's already-in-flight completion attempt now hits a
        # dead contract. complete()'s own pre-check (status != ACCEPTED)
        # raises BEFORE any cargo/credit mutation runs.
        with pytest.raises(ContractConflictError):
            contract_service.complete(db, contract.id, acceptor.id, now=_NOW)

        # No incoherent state: not paid on top of being penalized, no
        # second escrow movement.
        assert contract.status == ContractStatus.EXPIRED
        assert acceptor.credits == penalized_balance
        assert issuer.credits == 5000  # still HELD
        assert contract.escrow_state == ContractEscrowState.HELD


@pytest.mark.unit
class TestCancelPlayerContractRacesTheSweepWithDivergentEconomics:
    """FIXED FINDING (not one of the six attack goals verbatim, but
    directly responsive to goals 3/4) -- originally: cancel_player_
    contract's ACCEPTED branch had NO deadline check, so it stayed fully
    callable by the issuer on a contract that had ALREADY missed its
    deadline and was equally eligible for sweep_expired_accepted_
    contracts's expire-with-penalty path. Before WO-DRIFT-econ-accepted-
    deadline-expiry, ACCEPTED contracts could never reach EXPIRED at all
    (LEGAL_TRANSITIONS[ACCEPTED] was only {COMPLETED, CANCELLED}) so no
    such race existed; that WO's own new ACCEPTED->EXPIRED edge made it
    live for the first time -- an ordinary issuer clicking "cancel" around
    the same moment the periodic sweep ticks would otherwise (no attacker,
    no malice required) silently let the acceptor dodge the deadline
    penalty entirely, and net the issuer a WORSE refund than simply
    waiting for the sweep would have.

    FIX (same WO's revise): cancel_player_contract's ACCEPTED branch now
    gates on the deadline (contract_service.py's PAST-DEADLINE ACCEPTED
    GUARD, in that function's own docstring) -- once the deadline has
    passed, the unilateral-cancel path is withdrawn and the contract routes
    exclusively through the sweep. The second test below now proves the
    GATE rather than the divergence it used to demonstrate."""

    def _build_pair(self) -> tuple:
        issuer = _player(credits=5000)
        acceptor = _player(credits=4980)  # already paid the 2% (20) accept fee
        contract = _player_contract(
            issuer_id=issuer.id, acceptor_player_id=acceptor.id,
            deadline=_NOW - timedelta(minutes=1),  # already past
        )
        return issuer, acceptor, contract

    def test_sweep_wins_acceptor_penalized_issuer_escrow_held(self) -> None:
        """WO-CONTRACT-2b-HOLD-ESCROW: supersedes the prior immediate-
        full-refund pin -- the issuer's escrow is now HELD at expiry, not
        refunded, pending the 48h dispute window (see contract_service.py's
        own module docstring for the design change)."""
        issuer, acceptor, contract = self._build_pair()
        db = _RacySession(contracts=[contract], players=[issuer, acceptor])

        contract_service.sweep_expired_accepted_contracts(db, now=_NOW)

        assert contract.status == ContractStatus.EXPIRED
        assert acceptor.credits == 4980 - 1000  # penalized in full, as designed
        assert issuer.credits == 5000  # NOT refunded yet -- escrow stays HELD
        assert contract.escrow_state == ContractEscrowState.HELD

    def test_issuer_cancel_past_deadline_is_now_blocked_gate_holds_no_divergence(self) -> None:
        issuer, acceptor, contract = self._build_pair()
        db = _RacySession(contracts=[contract], players=[issuer, acceptor])

        # The past-deadline guard raises BEFORE _guarded_transition or any
        # credit mutation runs -- the contract stays exactly as it was,
        # still eligible for the sweep.
        with pytest.raises(ContractConflictError, match="past_deadline"):
            contract_service.cancel_player_contract(db, contract.id, issuer.id, now=_NOW)

        assert contract.status == ContractStatus.ACCEPTED  # unchanged -- no mutation occurred
        assert acceptor.credits == 4980  # untouched
        assert issuer.credits == 5000  # untouched, NOT the old 880 divergent refund

        # The contract is still exactly where sweep_expired_accepted_
        # contracts expects it -- the blocked cancel didn't strand it in
        # some third state. The sweep now enforces the acceptor's penalty
        # exactly as the WO guarantees, with no cancel-shaped escape hatch.
        # WO-CONTRACT-2b-HOLD-ESCROW: the issuer's escrow is HELD (not
        # refunded) at expiry -- a different design layer on top of this
        # test's own mack HIGH #1 fix, not a divergence.
        result = contract_service.sweep_expired_accepted_contracts(db, now=_NOW)
        assert result == {"expired": 1}
        assert contract.status == ContractStatus.EXPIRED
        assert acceptor.credits == 4980 - 1000  # penalty enforced, not dodged
        assert issuer.credits == 5000  # escrow HELD, not refunded


# ---------------------------------------------------------------------------
# WO-ECON-CONTRACT-MONEY-HARDEN -- the 3 hardening legs (Mack HIGH #1 /
# MEDIUM #2 / LOW #3). Same DB-free fake-session convention as the attack
# tests above; extended here as the regression base per the WO's own
# instruction.
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestDualLockConsistentOrdering:
    """Leg 1 (Mack HIGH #1): every credit-mutating call site now locks its
    Player row(s) via with_for_update(); the two call sites that touch
    TWO players in one operation (abandon()'s acceptor+issuer refund,
    sweep_expired_accepted_contracts' acceptor-penalty+issuer-refund) must
    acquire them in a CONSISTENT order (ascending by id) or two concurrent
    operations locking the same pair in opposite roles could deadlock.
    This is a single-threaded fake -- it cannot demonstrate the actual
    deadlock-freedom under real concurrent Postgres transactions (that is
    the orchestrator's live-Postgres leg), but the ORDERING property
    itself, the necessary precondition for that guarantee, is fully
    provable here: _RacySession's player_lock_log records the id each
    Player query was locked with, in call order."""

    def test_abandon_locks_ascending_by_id_regardless_of_acceptor_issuer_role(self) -> None:
        low_id, high_id = sorted([uuid.uuid4(), uuid.uuid4()])

        # Case A: the ACCEPTOR happens to have the lower id.
        acceptor_a = _player(id=low_id, credits=5000)
        issuer_a = _player(id=high_id, credits=5000)
        contract_a = _player_contract(
            issuer_id=issuer_a.id, acceptor_player_id=acceptor_a.id,
            deadline=_NOW + timedelta(hours=1),  # future -- exercise abandon(), not the sweep
        )
        db_a = _RacySession(contracts=[contract_a], players=[acceptor_a, issuer_a])
        contract_service.abandon(db_a, contract_a.id, acceptor_a.id, now=_NOW)
        assert db_a.player_lock_log == [low_id, high_id]

        # Case B: the ISSUER happens to have the lower id -- the exact
        # role-reversal that would deadlock against Case A's shape if
        # locking simply went "acceptor first, then issuer" unconditionally
        # (a concurrent Case-A-shaped abandon() on a different contract
        # sharing this same pair would lock high_id-then-low_id instead).
        acceptor_b = _player(id=high_id, credits=5000)
        issuer_b = _player(id=low_id, credits=5000)
        contract_b = _player_contract(
            issuer_id=issuer_b.id, acceptor_player_id=acceptor_b.id,
            deadline=_NOW + timedelta(hours=1),
        )
        db_b = _RacySession(contracts=[contract_b], players=[acceptor_b, issuer_b])
        contract_service.abandon(db_b, contract_b.id, acceptor_b.id, now=_NOW)
        assert db_b.player_lock_log == [low_id, high_id]

    def test_sweep_expired_accepted_contracts_player_issued_locks_only_the_acceptor(self) -> None:
        """WO-CONTRACT-2b-HOLD-ESCROW (R2): superseded the prior dual-lock
        pin this test used to make. The sweep no longer refunds the
        issuer's wallet at expiry (escrow is HELD instead -- see
        contract_service.py's own module docstring for the design
        change), so it no longer needs to lock the issuer's Player row at
        all -- only `escrow_amount` (a Contract-row field, already write-
        locked by the guarded ACCEPTED -> EXPIRED status-flip UPDATE that
        landed earlier in this same transaction) moves for a PLAYER-
        issued row. A single acceptor lock is sufficient -- see
        `_compute_claim_offset`'s and this sweep's own docstrings for why
        that alone still protects the concurrent-insure() race the dual-
        lock used to also happen to cover. This is a POSITIVE side effect
        (one fewer dual-lock call site to reason about for deadlock
        ordering), not a regression -- mirrors `test_npc_issued_single_
        player_site_locks_only_the_acceptor`'s own single-lock pattern,
        just for a PLAYER-issued row instead of NPC."""
        low_id, high_id = sorted([uuid.uuid4(), uuid.uuid4()])
        acceptor = _player(id=high_id, credits=5000)  # issuer has the LOWER id here
        issuer = _player(id=low_id, credits=5000)
        contract = _player_contract(
            issuer_id=issuer.id, acceptor_player_id=acceptor.id,
            deadline=_NOW - timedelta(minutes=1),
        )
        db = _RacySession(contracts=[contract], players=[acceptor, issuer])

        contract_service.sweep_expired_accepted_contracts(db, now=_NOW)

        assert db.player_lock_log == [high_id]  # acceptor only -- issuer never locked

    def test_npc_issued_single_player_site_locks_only_the_acceptor(self) -> None:
        """NPC-issued contracts never trigger the dual-lock path -- only
        the acceptor is ever locked, and only once."""
        acceptor = _player(credits=5000)
        contract = _npc_contract(
            status=ContractStatus.ACCEPTED, acceptor_player_id=acceptor.id,
            deadline=_NOW + timedelta(hours=1),
        )
        db = _RacySession(contracts=[contract], players=[acceptor])

        contract_service.abandon(db, contract.id, acceptor.id, now=_NOW)

        assert db.player_lock_log == [acceptor.id]


@pytest.mark.unit
class TestCrossSweepGloballyAscendingLockOrder:
    """WO-CONTRACT-57 (axis-2): `_run_contract_expire_sweep_sync`
    (contract_sweeps.py) runs `sweep_expired_contracts`, `sweep_expired_
    accepted_contracts`, and `sweep_expired_dispute_window` in ONE shared
    tick transaction. #54 (axis-1, WO-CONTRACT-LOCK-ORDER) already ordered
    each sweep's OWN Player-lock before its OWN Contract guard, but the 3
    sweeps' candidate-query orders have no relationship to each other or
    to player_id -- a Player-vs-Player AB-BA risk against any concurrent
    API call's own ascending-id dual-lock order (`_load_two_players_for_
    update`). `contract_service.run_contract_expiry_sweeps` visits every
    candidate across all 3 sweeps in ONE globally player_id-ascending
    merged order -- these tests prove the resulting `player_lock_log` (the
    ORDER `_load_player(for_update=True)` actually fired in) is exactly
    ascending, even when every sweep's OWN candidates are seeded so the
    HISTORICAL per-sweep call order (contracts, then accepted, then
    dispute-window) would have locked them descending -- the shape a
    per-sweep-only sort could never catch (see that function's own
    docstring for the [10,20,90]+[5,30] counterexample: concatenating two
    internally-sorted lists is not itself sorted)."""

    def test_merged_dispatch_locks_players_strictly_ascending_across_all_3_sweeps(self) -> None:
        low_id, mid_id, high_id = sorted([uuid.uuid4(), uuid.uuid4(), uuid.uuid4()])

        # sweep_expired_contracts candidate -- issuer at the HIGH id. This
        # sweep runs FIRST in the historical per-sweep call order, so a
        # naive per-sweep-sequential dispatch would lock high_id first.
        issuer_high = _player(id=high_id, credits=5000)
        c_contracts = _player_contract(
            status=ContractStatus.POSTED, issuer_id=high_id,
            escrow_amount=Decimal("1000.00"), deadline=_NOW - timedelta(hours=1),
        )
        # A sibling sweep_expired_contracts candidate with NO refund owed
        # (escrow_amount == 0) -- locks no player at all; must not disrupt
        # the ascending property of the OTHER, lock-needing candidates.
        c_contracts_no_lock = _player_contract(
            status=ContractStatus.POSTED, issuer_id=uuid.uuid4(),
            escrow_amount=Decimal("0"), deadline=_NOW - timedelta(hours=1),
        )

        # sweep_expired_accepted_contracts candidate -- acceptor at the LOW
        # id. This sweep runs SECOND historically.
        acceptor_low = _player(id=low_id, credits=5000)
        c_accepted = _npc_contract(
            deadline=_NOW - timedelta(minutes=1), acceptor_player_id=low_id, penalty=Decimal("200"),
        )

        # sweep_expired_dispute_window candidate -- issuer at the MID id.
        # This sweep runs THIRD/last historically.
        issuer_mid = _player(id=mid_id, credits=5000)
        c_dispute = _player_contract(
            status=ContractStatus.EXPIRED, issuer_id=mid_id,
            escrow_amount=Decimal("500.00"), escrow_state=ContractEscrowState.HELD,
            deadline=_NOW - timedelta(hours=50),  # > 48h dispute-filing window
        )

        db = _RacySession(
            contracts=[c_contracts, c_contracts_no_lock, c_accepted, c_dispute],
            players=[issuer_high, acceptor_low, issuer_mid],
        )

        posted_result, accepted_result, dispute_result = contract_service.run_contract_expiry_sweeps(db, now=_NOW)

        # The historical per-sweep call order would have locked
        # [high_id, low_id, mid_id] -- NOT ascending. The merged dispatch
        # instead locks exactly the ascending sequence, and the no-lock
        # candidate contributes nothing to the log at all.
        assert db.player_lock_log == [low_id, mid_id, high_id]
        assert db.player_lock_log == sorted(db.player_lock_log)

        # Every candidate was actually processed -- the restructure didn't
        # silently drop anything.
        assert c_contracts.status == ContractStatus.EXPIRED
        assert c_contracts_no_lock.status == ContractStatus.EXPIRED
        assert c_accepted.status == ContractStatus.EXPIRED
        assert c_dispute.escrow_state == ContractEscrowState.REFUNDING
        assert posted_result == {"expired": 2}
        assert accepted_result == {"expired": 1}
        assert dispute_result == {"refunded": 1}

    def test_run_contract_expiry_sweeps_applies_a_real_expiry_gate_before_any_lock(self) -> None:
        """mack LOW #3: `run_contract_expiry_sweeps` applies `expiry_gate`
        to sweep_expired_accepted_contracts' candidates BEFORE they ever
        contribute a sort key or touch a player lock (see this module's
        own docstring) -- the D19 deposit-wins gate is a money-adjacent
        behavior (storage_service.gate_contract_expiry_on_locker is the
        real production gate) that deserves a PERMANENT regression guard
        with a REAL callable, not just a mocked pass-through. Converts a
        throwaway scratch probe into a lasting test: a gate that defers
        ONE of two ACCEPTED candidates by id must leave that candidate
        completely untouched (never locked, never counted) while its
        sibling still processes and pays its penalty normally."""
        deferred_acceptor = _player(credits=5000)
        deferred_contract = _npc_contract(
            deadline=_NOW - timedelta(hours=1), acceptor_player_id=deferred_acceptor.id, penalty=Decimal("300"),
        )
        processed_acceptor = _player(credits=5000)
        processed_contract = _npc_contract(
            deadline=_NOW - timedelta(minutes=1), acceptor_player_id=processed_acceptor.id, penalty=Decimal("200"),
        )

        def gate(db: Any, contract: Any) -> bool:
            return contract.id != deferred_contract.id

        db = _RacySession(
            contracts=[deferred_contract, processed_contract],
            players=[deferred_acceptor, processed_acceptor],
        )

        posted_result, accepted_result, dispute_result = contract_service.run_contract_expiry_sweeps(
            db, now=_NOW, expiry_gate=gate,
        )

        # Gate-deferred -- status untouched, NEVER locked (never contributed
        # a sort key or reached the pre-pass), uncounted.
        assert deferred_contract.status == ContractStatus.ACCEPTED
        assert deferred_acceptor.id not in db.player_lock_log
        assert deferred_acceptor.credits == 5000
        # Its sibling, ungated, still processes and pays its penalty.
        assert processed_contract.status == ContractStatus.EXPIRED
        assert processed_acceptor.credits == 5000 - 200
        assert posted_result == {"expired": 0}
        assert accepted_result == {"expired": 1}
        assert dispute_result == {"refunded": 0}


@pytest.mark.unit
class TestLockTimeoutDegradesGracefully:
    """WO-CONTRACT-57 addendum (hub-required, precision refinement): the
    hub-ruled BLOCKING mechanism is bounded by a txn-scoped `SET LOCAL
    lock_timeout` (contract_sweeps.py) so a hung concurrent API
    transaction can't freeze a whole sweep tick. A timed-out acquisition
    surfaces as `OperationalError` -- but only a genuine lock_timeout
    (Postgres SQLSTATE 55P03) may be silently DEFERRED (status untouched,
    re-picked next tick, zero money moved); anything else (a real
    deadlock, 40P01 -- which the ascending-order invariant this WO builds
    should make IMPOSSIBLE, so its appearance would itself be a bug worth
    surfacing loudly -- or any other OperationalError) must NOT be
    silently deferred, and instead falls through to #54's own existing
    per-candidate resting state (loud `logger.exception`, flip-anyway,
    no refund/penalty this tick) -- exactly like today's missing-player
    case, never a new silent-swallow path. These tests prove the
    discrimination is by SQLSTATE, not a blanket `except OperationalError`."""

    def test_lock_timeout_55p03_defers_the_candidate_untouched_and_uncounted(self) -> None:
        contended_issuer_id = uuid.uuid4()  # deliberately never added to db.players
        c1 = _player_contract(
            status=ContractStatus.POSTED, issuer_id=contended_issuer_id,
            escrow_amount=Decimal("1000.00"), deadline=_NOW - timedelta(hours=1),
        )
        real_issuer = _player(credits=5000)
        c2 = _player_contract(
            status=ContractStatus.POSTED, issuer_id=real_issuer.id,
            escrow_amount=Decimal("500.00"), deadline=_NOW - timedelta(minutes=1),
        )
        lock_timeout = OperationalError("stmt", {}, SimpleNamespace(pgcode="55P03"))
        db = _RacySession(
            contracts=[c1, c2], players=[real_issuer],
            player_lock_failures={contended_issuer_id: lock_timeout},
        )

        result = contract_service.sweep_expired_contracts(db, now=_NOW)

        # c1 -- the contended candidate -- is DEFERRED: status completely
        # untouched (unlike the missing-player case, which flips anyway),
        # never counted, no money moved, and its (never-granted) lock
        # never lands in player_lock_log.
        assert c1.status == ContractStatus.POSTED
        assert contended_issuer_id not in db.player_lock_log
        # c2 -- unaffected sibling in the SAME sweep pass -- still
        # processes and refunds normally. The tick did not abort or spin.
        assert c2.status == ContractStatus.EXPIRED
        assert real_issuer.credits == 5000 + 500
        assert result == {"expired": 1}  # only c2 counted

    def test_non_lock_timeout_operational_error_on_issuer_lock_is_not_silently_deferred(self) -> None:
        """mack LOW #2: the mirror of `test_non_lock_timeout_operational_
        error_is_not_silently_deferred` below, but for sweep_expired_
        contracts' OWN issuer lock (contract_service.py:750-765) -- a
        SEPARATE except block from the acceptor-lock one, byte-identical
        discrimination logic but its own code path, needing its own
        direct coverage rather than relying on the acceptor-lock test to
        stand in for it. A DIFFERENT SQLSTATE (40P01, a genuine deadlock
        -- which the ascending-order invariant should make impossible, so
        its appearance is itself a bug worth loud attention) on the
        issuer lock must NOT take the transient-defer branch; it falls
        through to #54's existing flip-anyway resting state instead."""
        deadlocked_issuer_id = uuid.uuid4()  # deliberately never added to db.players
        c1 = _player_contract(
            status=ContractStatus.POSTED, issuer_id=deadlocked_issuer_id,
            escrow_amount=Decimal("1000.00"), deadline=_NOW - timedelta(hours=1),
        )
        real_issuer = _player(credits=5000)
        c2 = _player_contract(
            status=ContractStatus.POSTED, issuer_id=real_issuer.id,
            escrow_amount=Decimal("500.00"), deadline=_NOW - timedelta(minutes=1),
        )
        deadlock = OperationalError("stmt", {}, SimpleNamespace(pgcode="40P01"))
        db = _RacySession(
            contracts=[c1, c2], players=[real_issuer],
            player_lock_failures={deadlocked_issuer_id: deadlock},
        )

        result = contract_service.sweep_expired_contracts(db, now=_NOW)

        # c1 is NOT deferred -- flip-anyway, counted, no refund this tick
        # (matches TestPerRowSavepointIsolation's own missing-issuer
        # precedent exactly).
        assert c1.status == ContractStatus.EXPIRED
        assert c2.status == ContractStatus.EXPIRED
        assert real_issuer.credits == 5000 + 500  # c2's refund still landed
        assert result == {"expired": 2}  # BOTH counted -- c1 flip-anyway, c2 processed normally

    def test_non_lock_timeout_operational_error_is_not_silently_deferred(self) -> None:
        """A DIFFERENT SQLSTATE (here: 40P01, a genuine deadlock -- which
        the ascending-order invariant should make unreachable, so its
        appearance is itself a bug worth loud attention) must NOT take the
        transient-defer branch. It falls through to #54's existing
        flip-anyway resting state instead -- proving the discrimination
        is precise, not a blanket `except OperationalError`."""
        deadlocked_acceptor_id = uuid.uuid4()  # deliberately never added to db.players
        c1 = _npc_contract(
            deadline=_NOW - timedelta(hours=1),
            acceptor_player_id=deadlocked_acceptor_id, penalty=Decimal("300"),
        )
        real_acceptor = _player(credits=5000)
        c2 = _npc_contract(
            deadline=_NOW - timedelta(minutes=1),
            acceptor_player_id=real_acceptor.id, penalty=Decimal("200"),
        )
        deadlock = OperationalError("stmt", {}, SimpleNamespace(pgcode="40P01"))
        db = _RacySession(
            contracts=[c1, c2], players=[real_acceptor],
            player_lock_failures={deadlocked_acceptor_id: deadlock},
        )

        result = contract_service.sweep_expired_accepted_contracts(db, now=_NOW)

        # c1 is NOT deferred -- a non-55P03 OperationalError takes the
        # SAME path a missing player already does: flip-anyway, counted,
        # no penalty this tick (matches TestPerRowSavepointIsolation's
        # own missing-acceptor precedent exactly).
        assert c1.status == ContractStatus.EXPIRED
        assert c2.status == ContractStatus.EXPIRED
        assert real_acceptor.credits == 5000 - 200
        assert result == {"expired": 2}  # BOTH counted -- c1 flip-anyway, c2 processed normally

    def test_run_contract_expiry_sweeps_defers_a_contended_candidate_from_either_sweep(self) -> None:
        """The same 55P03-defer proof, but through the ACTUAL production
        entry point (`run_contract_expiry_sweeps`, what `contract_sweeps.
        py` calls every tick) rather than a standalone wrapper -- proves
        the defer survives the merged cross-sweep dispatch too, and (this
        is what actually caught a real bug during this addendum's build)
        that `sweep_expired_contracts`' trailing bulk-expire pass does NOT
        re-catch and flip-anyway a candidate `_process_one_sweep_expired_
        contracts_candidate` already deferred -- a deferred candidate is
        still `status == posted`, still `issuer_type == PLAYER`, still
        `escrow_state == HELD`, the EXACT shape a naive bulk `WHERE status
        == posted` would silently re-match."""
        contended_issuer_id = uuid.uuid4()
        c_contracts = _player_contract(
            status=ContractStatus.POSTED, issuer_id=contended_issuer_id,
            escrow_amount=Decimal("1000.00"), deadline=_NOW - timedelta(hours=1),
        )
        contended_acceptor_id = uuid.uuid4()
        c_accepted = _npc_contract(
            deadline=_NOW - timedelta(minutes=1),
            acceptor_player_id=contended_acceptor_id, penalty=Decimal("300"),
        )
        real_acceptor = _player(credits=5000)
        c_accepted_sibling = _npc_contract(
            deadline=_NOW - timedelta(minutes=1),
            acceptor_player_id=real_acceptor.id, penalty=Decimal("200"),
        )
        lock_timeout_issuer = OperationalError("stmt", {}, SimpleNamespace(pgcode="55P03"))
        lock_timeout_acceptor = OperationalError("stmt", {}, SimpleNamespace(pgcode="55P03"))
        db = _RacySession(
            contracts=[c_contracts, c_accepted, c_accepted_sibling], players=[real_acceptor],
            player_lock_failures={
                contended_issuer_id: lock_timeout_issuer,
                contended_acceptor_id: lock_timeout_acceptor,
            },
        )

        posted_result, accepted_result, dispute_result = contract_service.run_contract_expiry_sweeps(db, now=_NOW)

        # Both contended candidates are DEFERRED -- status completely
        # untouched, including surviving sweep_expired_contracts' OWN
        # trailing bulk-expire pass (the bug this test was written to
        # catch).
        assert c_contracts.status == ContractStatus.POSTED
        assert c_accepted.status == ContractStatus.ACCEPTED
        # Their unaffected siblings still fully process in the SAME tick.
        assert c_accepted_sibling.status == ContractStatus.EXPIRED
        assert real_acceptor.credits == 5000 - 200
        assert posted_result == {"expired": 0}
        assert accepted_result == {"expired": 1}
        assert dispute_result == {"refunded": 0}


@pytest.mark.unit
class TestPerRowSavepointIsolation:
    """Leg 2 (Mack MEDIUM #2): before this WO, an unhandled exception
    anywhere in a per-row credit-mutation body (e.g. a vanished Player row
    -- not reachable today, no hard-delete path exists, but cheap to
    harden against) propagated out of the ENTIRE while-loop, discarding
    every OTHER row's already-applied work in the same shared transaction
    and re-selecting the same poisoned row on the next scheduler tick
    forever. Each per-row credit body is now wrapped in its own
    db.begin_nested() savepoint -- a failure there is caught, logged, and
    the sweep moves on to the next candidate. (The row's own STATUS flip
    happens BEFORE the savepoint begins and is therefore NOT reverted by
    a savepoint failure -- see contract_service.py's own comment on this
    tradeoff.)"""

    def test_sweep_expired_accepted_contracts_survives_a_missing_acceptor_row(self) -> None:
        acceptor2 = _player(credits=5000)
        missing_acceptor_id = uuid.uuid4()  # deliberately never added to db.players
        c1 = _npc_contract(
            deadline=_NOW - timedelta(hours=1),
            acceptor_player_id=missing_acceptor_id, penalty=Decimal("300"),
        )
        c2 = _npc_contract(
            deadline=_NOW - timedelta(minutes=1),
            acceptor_player_id=acceptor2.id, penalty=Decimal("200"),
        )
        db = _RacySession(contracts=[c1, c2], players=[acceptor2])

        # Must complete without raising -- the missing-player ContractError
        # inside c1's savepoint body is caught and logged, not propagated.
        result = contract_service.sweep_expired_accepted_contracts(db, now=_NOW)

        assert c1.status == ContractStatus.EXPIRED
        # c2 -- the OTHER row -- was fully processed despite c1's failure.
        assert c2.status == ContractStatus.EXPIRED
        assert acceptor2.credits == 5000 - 200
        assert result == {"expired": 2}

    def test_sweep_expired_contracts_survives_a_missing_issuer_row(self) -> None:
        missing_issuer_id = uuid.uuid4()  # deliberately never added to db.players
        c1 = _player_contract(
            status=ContractStatus.POSTED, issuer_id=missing_issuer_id,
            escrow_amount=Decimal("1000.00"), deadline=_NOW - timedelta(hours=1),
        )
        real_issuer = _player(credits=5000)
        c2 = _player_contract(
            status=ContractStatus.POSTED, issuer_id=real_issuer.id,
            escrow_amount=Decimal("500.00"), deadline=_NOW - timedelta(minutes=1),
        )
        db = _RacySession(contracts=[c1, c2], players=[real_issuer])

        result = contract_service.sweep_expired_contracts(db, now=_NOW)

        assert c1.status == ContractStatus.EXPIRED
        assert c2.status == ContractStatus.EXPIRED
        assert real_issuer.credits == 5000 + 500  # c2's refund still landed
        assert result == {"expired": 2}

    def test_run_contract_expiry_sweeps_survives_a_missing_player_from_any_sweep(self) -> None:
        """WO-CONTRACT-57 (axis-2): the merged `run_contract_expiry_
        sweeps` dispatch calls the SAME per-candidate bodies the two tests
        above already proved isolate a lock failure -- this proves that
        isolation survives being invoked from a cross-sweep merged order
        too: a missing PLAYER row for one sweep's candidate does not
        prevent a DIFFERENT sweep's sibling candidate (processed elsewhere
        in the SAME merged dispatch) from completing normally. Per the
        hub's ruling (blocking, not skip-locked), the missing-player
        candidate still flip-anyway EXPIREs -- #54's original resting
        state, unchanged."""
        missing_issuer_id = uuid.uuid4()  # deliberately never added to db.players
        c1 = _player_contract(
            status=ContractStatus.POSTED, issuer_id=missing_issuer_id,
            escrow_amount=Decimal("1000.00"), deadline=_NOW - timedelta(hours=1),
        )
        real_acceptor = _player(credits=5000)
        c2 = _npc_contract(
            deadline=_NOW - timedelta(minutes=1),
            acceptor_player_id=real_acceptor.id, penalty=Decimal("200"),
        )
        db = _RacySession(contracts=[c1, c2], players=[real_acceptor])

        posted_result, accepted_result, dispute_result = contract_service.run_contract_expiry_sweeps(db, now=_NOW)

        assert c1.status == ContractStatus.EXPIRED  # flip-anyway, no refund -- #54 preserved
        assert c2.status == ContractStatus.EXPIRED  # sibling from a DIFFERENT sweep unaffected
        assert real_acceptor.credits == 5000 - 200
        assert posted_result == {"expired": 1}
        assert accepted_result == {"expired": 1}
        assert dispute_result == {"refunded": 0}


@pytest.mark.unit
class TestRoundHalfUpCreditConversion:
    """Leg 3 (Mack LOW #3): plain int(some_decimal) TRUNCATES toward zero
    rather than rounding -- a fee/penalty/refund landing on a fractional-
    credit remainder >= 0.50 silently lost a whole credit on every
    occurrence before this fix. 125 credits at a 2% fee lands EXACTLY on
    2.50 -- the sharpest possible case (round-half-up must go UP to 3, not
    truncate down to 2)."""

    def test_accept_fee_exactly_half_credit_rounds_up_not_down(self) -> None:
        acceptor = _player(credits=1000)
        contract = _npc_contract(
            status=ContractStatus.POSTED,
            payment=Decimal("125.00"), acceptance_fee_pct=Decimal("2.0"),
            deadline=_NOW + timedelta(hours=1),
        )
        db = _RacySession(contracts=[contract], players=[acceptor])

        result = contract_service.accept(db, contract.id, acceptor.id, now=_NOW)

        # 125 * 2% = 2.50 exactly -- starting balance 1000 - fee 2.50 =
        # 997.50, exactly halfway between 997 and 998. ROUND_HALF_UP takes
        # this to 998; plain int() truncation (the old behavior) would
        # have produced 997 instead -- a full credit's difference from a
        # single conversion.
        assert result["acceptance_fee_charged"] == 2.50
        assert acceptor.credits == 998

    def test_sweep_penalty_exactly_half_credit_rounds_up(self) -> None:
        acceptor = _player(credits=1000)
        contract = _npc_contract(
            deadline=_NOW - timedelta(minutes=1), penalty=Decimal("2.50"),
            acceptor_player_id=acceptor.id,
        )
        db = _RacySession(contracts=[contract], players=[acceptor])

        contract_service.sweep_expired_accepted_contracts(db, now=_NOW)

        assert acceptor.credits == 1000 - 3

    def test_cancel_player_contract_refund_exactly_half_credit_rounds_up(self) -> None:
        issuer = _player(credits=1000)
        # ACCEPTED-branch refund = escrow - accept_fee_equivalent -
        # cancel_fee. escrow(127.50) - accept_fee(125*2%=2.50) -
        # cancel_fee(125*10%=12.50) = 112.50 exactly.
        contract = _player_contract(
            status=ContractStatus.ACCEPTED, issuer_id=issuer.id,
            payment=Decimal("125.00"), acceptance_fee_pct=Decimal("2.0"),
            escrow_amount=Decimal("127.50"), deadline=_NOW + timedelta(hours=1),
        )
        db = _RacySession(contracts=[contract], players=[issuer])

        result = contract_service.cancel_player_contract(db, contract.id, issuer.id, now=_NOW)

        # ROUND_HALF_UP takes 112.50 to 113, not the truncated 112.
        assert result["refund"] == 112.50
        assert issuer.credits == 1000 + 113

    def test_post_contract_schema_rejects_fractional_payment(self) -> None:
        """The OTHER half of Leg 3: a fractional payment is now rejected
        at the API schema layer (contracts.py's PostContractRequest),
        never reaching post_player_contract at all -- a whole-credit
        Player.credits column can never honor a fractional payment
        exactly regardless of how carefully the service-side rounding is
        done, so it's refused up front rather than silently coerced."""
        from pydantic import ValidationError

        from src.api.routes.contracts import PostContractRequest

        base_kwargs = dict(
            destination_station_id=str(uuid.uuid4()),
            commodity_type="ore",
            quantity=10,
            deadline=_NOW + timedelta(hours=2),
        )
        # Whole-credit payments pass, including a ".00"-formatted Decimal
        # (multiple_of checks the numeric VALUE, not string precision).
        PostContractRequest(payment=Decimal("1000"), **base_kwargs)
        PostContractRequest(payment=Decimal("1000.00"), **base_kwargs)

        with pytest.raises(ValidationError):
            PostContractRequest(payment=Decimal("1000.50"), **base_kwargs)

    def test_post_contract_schema_rejects_fractional_insurance_pool_reserve(self) -> None:
        """WO-CONTRACT-1b-CLAIM-SAFETY (cipher MEDIUM): a fractional
        `insurance_pool_reserve` lets the claim-offset sweep's `refund =
        escrow_amount - pool_draw` and `acceptor_debit = penalty -
        pool_draw` round INDEPENDENTLY to whole credits -- since `refund -
        acceptor_debit == reserve` exactly in real arithmetic, a fractional
        reserve can make one round down and the other round up, minting
        ~1cr per cycle. Rejected at the schema layer, same `multiple_of=1`
        idiom as `payment` above -- the rounding lever never reaches
        post_player_contract at all."""
        from pydantic import ValidationError

        from src.api.routes.contracts import PostContractRequest

        base_kwargs = dict(
            destination_station_id=str(uuid.uuid4()),
            commodity_type="ore",
            quantity=10,
            payment=Decimal("1000"),
            deadline=_NOW + timedelta(hours=2),
        )
        # Whole-credit reserves pass, including a ".00"-formatted Decimal
        # and the default (0, never even passed).
        PostContractRequest(**base_kwargs)
        PostContractRequest(insurance_pool_reserve=Decimal("500"), **base_kwargs)
        PostContractRequest(insurance_pool_reserve=Decimal("500.00"), **base_kwargs)

        with pytest.raises(ValidationError):
            PostContractRequest(insurance_pool_reserve=Decimal("500.01"), **base_kwargs)

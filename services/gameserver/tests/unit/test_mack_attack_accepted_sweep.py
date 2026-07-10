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
    raise NotImplementedError(f"unsupported operator {cond.operator!r}")


class _FakeResult:
    def __init__(self, rowcount: int) -> None:
        self.rowcount = rowcount


class _FakeQuery:
    def __init__(self, rows: List[Any], criteria: Optional[List[Any]] = None) -> None:
        self._rows = rows
        self._criteria = criteria or []

    def filter(self, *conditions: Any) -> "_FakeQuery":
        return _FakeQuery(self._rows, self._criteria + list(conditions))

    def first(self) -> Any:
        for row in self._rows:
            if all(_match(row, c) for c in self._criteria):
                return row
        return None


class _RacyContractQuery(_FakeQuery):
    """Wraps the plain Contract query. On `.first()`, if the returned row's
    id has a pending race registered on the owning session, fires it
    (once, then discards it) BEFORE returning -- modeling a concurrent
    committer's write landing in the exact instant between the sweep's
    SELECT and its own guarded UPDATE a statement later. Also counts every
    SELECT and trips a hard circuit breaker past MAX_SELECTS, so a genuine
    infinite loop fails the test loudly and fast instead of hanging the
    run."""

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


class _RacySession:
    """Same execute()/flush()/commit() contract as the sibling test files'
    _FakeSession, plus a `races` map (contract_id -> mutator, applied once
    on that row's next SELECT) and a SELECT call counter."""

    def __init__(self, *, contracts: Optional[List[Any]] = None, players: Optional[List[Any]] = None) -> None:
        self.contracts = contracts or []
        self.players = players or []
        self.flush_calls = 0
        self.races: Dict[Any, Any] = {}
        self.select_calls = 0

    def query(self, model: Any) -> _FakeQuery:
        if model is Contract:
            return _RacyContractQuery(self.contracts, [], self)
        from src.models.player import Player
        if model is Player:
            return _FakeQuery(self.players)
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

    def commit(self) -> None:
        raise AssertionError("service functions are flush-only -- the route/scheduler commits")


# --- fixtures ------------------------------------------------------------ #

def _player(**overrides: Any) -> SimpleNamespace:
    base = dict(id=uuid.uuid4(), credits=5000, is_docked=False, current_port_id=None, current_ship=None)
    base.update(overrides)
    return SimpleNamespace(**base)


def _npc_contract(**overrides: Any) -> SimpleNamespace:
    base = dict(
        id=uuid.uuid4(), issuer_type=ContractIssuerType.NPC, issuer_id=uuid.uuid4(),
        acceptor_player_id=None, origin_station_id=uuid.uuid4(), destination_station_id=uuid.uuid4(),
        commodity_type="ore", quantity=50, status=ContractStatus.ACCEPTED,
        payment=Decimal("1000.00"), penalty=Decimal("1000.00"), acceptance_fee_pct=Decimal("2.0"),
        escrow_amount=Decimal("0"), escrow_state=ContractEscrowState.HELD,
        deadline=datetime(2026, 1, 2, tzinfo=UTC), posted_at=datetime(2026, 1, 1, tzinfo=UTC),
        accepted_at=datetime(2026, 1, 1, 1, tzinfo=UTC), completed_at=None,
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
    )
    base.update(overrides)
    return SimpleNamespace(**base)


_NOW = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)


@pytest.mark.unit
class TestNoInfiniteLoopUnderInjectedRace:
    """Attack goal 1: construct the adversarial case the sweep's own
    docstring claims can't cause a spin -- a row that races away (via a
    live complete()/abandon() on another session) in the exact instant
    between the per-iteration SELECT and the guarded UPDATE."""

    def test_raced_row_is_skipped_once_never_reselected_loop_terminates(self) -> None:
        acceptor1 = _player(credits=5000)
        acceptor2 = _player(credits=5000)
        # c1 will race away to CANCELLED (a concurrent abandon() landing)
        # right as the sweep's SELECT returns it, before its own guarded
        # UPDATE runs.
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
        # 3 SELECTs: c1 (raced, discovered gone), c2 (real), then None
        # (loop exit) -- NOT open-ended. This is the load-bearing assertion:
        # the raced row is never re-offered by a subsequent SELECT.
        assert db.select_calls == 3

    def test_every_candidate_races_away_loop_still_terminates_at_zero(self) -> None:
        """Worst case for the "spin forever" theory: EVERY due row races
        away. If the SELECT/UPDATE pairing could ever re-select an already-
        raced row, this is where it would show up as a hang (caught by the
        MAX_SELECTS circuit breaker instead)."""
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
        # 6 SELECTs: 5 raced-and-discovered-gone + 1 final None. Bounded,
        # not open-ended -- the loop does not retry a raced row.
        assert db.select_calls == 6


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
        assert issuer.credits == 5000 + 1000  # full escrow refund

        # The acceptor's already-in-flight completion attempt now hits a
        # dead contract. complete()'s own pre-check (status != ACCEPTED)
        # raises BEFORE any cargo/credit mutation runs.
        with pytest.raises(ContractConflictError):
            contract_service.complete(db, contract.id, acceptor.id, now=_NOW)

        # No incoherent state: not paid on top of being penalized, no
        # second escrow movement.
        assert contract.status == ContractStatus.EXPIRED
        assert acceptor.credits == penalized_balance
        assert issuer.credits == 5000 + 1000
        assert contract.escrow_state == ContractEscrowState.REFUNDING


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

    def test_sweep_wins_acceptor_penalized_issuer_refunded_in_full(self) -> None:
        issuer, acceptor, contract = self._build_pair()
        db = _RacySession(contracts=[contract], players=[issuer, acceptor])

        contract_service.sweep_expired_accepted_contracts(db, now=_NOW)

        assert contract.status == ContractStatus.EXPIRED
        assert acceptor.credits == 4980 - 1000  # penalized in full, as designed
        assert issuer.credits == 5000 + 1000  # full escrow refund

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
        result = contract_service.sweep_expired_accepted_contracts(db, now=_NOW)
        assert result == {"expired": 1}
        assert contract.status == ContractStatus.EXPIRED
        assert acceptor.credits == 4980 - 1000  # penalty enforced, not dodged
        assert issuer.credits == 5000 + 1000  # full escrow refund, not the worse 880

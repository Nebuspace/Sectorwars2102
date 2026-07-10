"""WO-ECON-CONTRACT-1-KERNEL lane 5 -- contract_service.py: accept /
complete / abandon / sweep_expired_contracts.

DB-free: a real SQLAlchemy WHERE-clause interpreter (not a scripted mock)
backs both `.filter(...).first()/.count()` and `db.execute(update(...))`,
so a genuine race between two `accept()` calls against the SAME fake
in-memory row is provable behaviorally -- no test special-cases the second
call's outcome, it falls out of the same guarded-UPDATE machinery
production code runs. Mirrors this codebase's established fake-query-
filter-interpreter-pattern / sqla-update-values-db-free-proof conventions.

`flag_modified` (in `complete()`, cargo mutation) requires a REAL ORM
instance -- a `_real_ship()` helper builds one; every other fixture is a
plain SimpleNamespace.
"""
from __future__ import annotations

import operator
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from typing import Any, List, Optional

import pytest
from sqlalchemy.sql.operators import in_op

from src.models.contract import Contract, ContractIssuerType, ContractStatus
from src.models.ship import Ship, ShipType
from src.services import contract_service
from src.services.contract_service import (
    ContractConflictError,
    ContractError,
    ContractNotFoundError,
)

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

    def count(self) -> int:
        return sum(1 for row in self._rows if all(_match(row, c) for c in self._criteria))


class _FakeSession:
    def __init__(self, *, contracts: Optional[List[Any]] = None, players: Optional[List[Any]] = None) -> None:
        self.contracts = contracts or []
        self.players = players or []
        self.flush_calls = 0

    def query(self, model: Any) -> _FakeQuery:
        if model is Contract:
            return _FakeQuery(self.contracts)
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
        raise AssertionError("service functions are flush-only -- the route commits")


# --- fixtures ------------------------------------------------------------ #

def _real_ship(**overrides: Any) -> Ship:
    """flag_modified() requires a real ORM instance -- see module docstring."""
    base = dict(
        id=uuid.uuid4(), name="Test Freighter", type=ShipType.LIGHT_FREIGHTER,
        sector_id=1, is_destroyed=False,
        cargo={"capacity": 500, "used": 60, "contents": {"ore": 60}},
    )
    base.update(overrides)
    return Ship(**base)


def _contract(**overrides: Any) -> SimpleNamespace:
    base = dict(
        id=uuid.uuid4(),
        issuer_type=ContractIssuerType.NPC,
        issuer_id=uuid.uuid4(),
        acceptor_player_id=None,
        origin_station_id=uuid.uuid4(),
        destination_station_id=uuid.uuid4(),
        commodity_type="ore",
        quantity=50,
        status=ContractStatus.POSTED,
        payment=Decimal("1000.00"),
        penalty=Decimal("1000.00"),
        acceptance_fee_pct=Decimal("2.0"),
        deadline=datetime(2026, 1, 2, tzinfo=UTC),
        posted_at=datetime(2026, 1, 1, tzinfo=UTC),
        accepted_at=None,
        completed_at=None,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def _player(**overrides: Any) -> SimpleNamespace:
    base = dict(
        id=uuid.uuid4(), credits=10000, is_docked=False, current_port_id=None, current_ship=None,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


_NOW = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)


@pytest.mark.unit
class TestAccept:
    def test_happy_path_charges_fee_and_transitions(self) -> None:
        c = _contract(payment=Decimal("1000.00"), acceptance_fee_pct=Decimal("2.0"))
        acceptor = _player(credits=500)
        db = _FakeSession(contracts=[c], players=[acceptor])

        result = contract_service.accept(db, c.id, acceptor.id, now=_NOW)

        assert result["acceptance_fee_charged"] == 20.0
        assert c.status == ContractStatus.ACCEPTED
        assert c.acceptor_player_id == acceptor.id
        assert c.accepted_at == _NOW
        assert acceptor.credits == 480
        assert db.flush_calls == 1

    @pytest.mark.parametrize("payment,expected_fee", [(1, 0.02), (100, 2.00), (101, 2.02)])
    def test_fee_math_edge_cases(self, payment: int, expected_fee: float) -> None:
        c = _contract(payment=Decimal(str(payment)), acceptance_fee_pct=Decimal("2.0"))
        acceptor = _player(credits=100000)
        db = _FakeSession(contracts=[c], players=[acceptor])

        result = contract_service.accept(db, c.id, acceptor.id, now=_NOW)

        assert result["acceptance_fee_charged"] == expected_fee

    def test_concurrent_accept_second_caller_409s_feeless(self) -> None:
        """Real race, not scripted: TWO accept() calls against the SAME
        fake row. The first's guarded UPDATE flips status to accepted; the
        second's WHERE (status='posted') then matches zero rows."""
        c = _contract(payment=Decimal("1000.00"))
        winner = _player(credits=5000)
        loser = _player(credits=5000)
        db = _FakeSession(contracts=[c], players=[winner, loser])

        contract_service.accept(db, c.id, winner.id, now=_NOW)
        with pytest.raises(ContractConflictError, match="stale_status"):
            contract_service.accept(db, c.id, loser.id, now=_NOW)

        assert loser.credits == 5000  # feeless -- never charged
        assert c.acceptor_player_id == winner.id  # unchanged by the loser's attempt

    def test_accept_after_sweep_expired_409s(self) -> None:
        """Same guarded-UPDATE mechanism handles a raced accept-vs-expire:
        the sweep flips status to expired first, accept's WHERE (status=
        'posted') then matches zero rows -- no special-casing needed."""
        c = _contract(deadline=_NOW - timedelta(hours=1))
        acceptor = _player(credits=5000)
        db = _FakeSession(contracts=[c], players=[acceptor])

        swept = contract_service.sweep_expired_contracts(db, now=_NOW)
        assert swept == {"expired": 1}
        assert c.status == ContractStatus.EXPIRED

        with pytest.raises(ContractConflictError):
            contract_service.accept(db, c.id, acceptor.id, now=_NOW)
        assert acceptor.credits == 5000

    def test_deadline_already_passed_rejects_before_sweep_runs(self) -> None:
        c = _contract(deadline=_NOW - timedelta(minutes=1))
        acceptor = _player()
        db = _FakeSession(contracts=[c], players=[acceptor])
        with pytest.raises(ContractConflictError, match="expired"):
            contract_service.accept(db, c.id, acceptor.id, now=_NOW)

    def test_insufficient_credits_rejected(self) -> None:
        c = _contract(payment=Decimal("1000.00"), acceptance_fee_pct=Decimal("2.0"))
        acceptor = _player(credits=5)
        db = _FakeSession(contracts=[c], players=[acceptor])
        with pytest.raises(ContractError, match="insufficient_credits"):
            contract_service.accept(db, c.id, acceptor.id, now=_NOW)
        assert c.status == ContractStatus.POSTED  # no mutation

    def test_cannot_accept_own_player_issued_contract(self) -> None:
        issuer_id = uuid.uuid4()
        c = _contract(issuer_type=ContractIssuerType.PLAYER, issuer_id=issuer_id)
        acceptor = _player(id=issuer_id, credits=5000)
        db = _FakeSession(contracts=[c], players=[acceptor])
        with pytest.raises(ContractError, match="own contract"):
            contract_service.accept(db, c.id, acceptor.id, now=_NOW)

    def test_not_found_raises_404_class(self) -> None:
        db = _FakeSession(contracts=[], players=[])
        with pytest.raises(ContractNotFoundError):
            contract_service.accept(db, uuid.uuid4(), uuid.uuid4(), now=_NOW)


@pytest.mark.unit
class TestComplete:
    def _accepted_setup(self, **contract_overrides: Any):
        destination_id = uuid.uuid4()
        c = _contract(
            status=ContractStatus.ACCEPTED, destination_station_id=destination_id,
            commodity_type="ore", quantity=50, payment=Decimal("3000.00"),
        )
        c.acceptor_player_id = None
        for k, v in contract_overrides.items():
            setattr(c, k, v)
        ship = _real_ship(cargo={"capacity": 500, "used": 80, "contents": {"ore": 80}})
        player = _player(
            credits=1000, is_docked=True, current_port_id=destination_id, current_ship=ship,
        )
        c.acceptor_player_id = player.id
        db = _FakeSession(contracts=[c], players=[player])
        return db, c, player, ship

    def test_happy_path_pays_and_decrements_cargo(self) -> None:
        db, c, player, ship = self._accepted_setup()
        result = contract_service.complete(db, c.id, player.id, now=_NOW)

        assert result["payout"] == 3000
        assert c.status == ContractStatus.COMPLETED
        assert c.completed_at == _NOW
        assert player.credits == 4000
        assert ship.cargo["contents"]["ore"] == 30  # 80 - 50
        assert ship.cargo["used"] == 30

    def test_wrong_station_409s_no_state_change(self) -> None:
        db, c, player, ship = self._accepted_setup()
        player.current_port_id = uuid.uuid4()  # docked, but at the WRONG station
        with pytest.raises(ContractConflictError, match="wrong_station"):
            contract_service.complete(db, c.id, player.id, now=_NOW)
        assert c.status == ContractStatus.ACCEPTED
        assert player.credits == 1000

    def test_not_docked_409s(self) -> None:
        db, c, player, ship = self._accepted_setup()
        player.is_docked = False
        with pytest.raises(ContractConflictError, match="wrong_station"):
            contract_service.complete(db, c.id, player.id, now=_NOW)

    def test_insufficient_cargo_rejected_no_state_change(self) -> None:
        db, c, player, ship = self._accepted_setup()
        ship.cargo["contents"]["ore"] = 10  # need 50
        with pytest.raises(ContractError, match="insufficient_cargo"):
            contract_service.complete(db, c.id, player.id, now=_NOW)
        assert c.status == ContractStatus.ACCEPTED
        assert ship.cargo["contents"]["ore"] == 10  # untouched

    def test_double_complete_is_idempotent_no_double_pay(self) -> None:
        db, c, player, ship = self._accepted_setup()
        contract_service.complete(db, c.id, player.id, now=_NOW)
        assert player.credits == 4000

        # Retry against the now-completed contract: raises, does NOT re-pay
        # or re-decrement cargo a second time.
        with pytest.raises(ContractConflictError):
            contract_service.complete(db, c.id, player.id, now=_NOW)
        assert player.credits == 4000
        assert ship.cargo["contents"]["ore"] == 30

    def test_not_your_contract_rejected(self) -> None:
        db, c, player, ship = self._accepted_setup()
        stranger = _player(is_docked=True, current_port_id=c.destination_station_id)
        db.players.append(stranger)
        with pytest.raises(ContractError, match="not accepted by you"):
            contract_service.complete(db, c.id, stranger.id, now=_NOW)


@pytest.mark.unit
class TestAbandon:
    def test_happy_path_charges_penalty(self) -> None:
        c = _contract(status=ContractStatus.ACCEPTED, penalty=Decimal("1000.00"))
        player = _player(credits=1500)
        c.acceptor_player_id = player.id
        db = _FakeSession(contracts=[c], players=[player])

        result = contract_service.abandon(db, c.id, player.id, now=_NOW)

        assert result["penalty_charged"] == 1000
        assert c.status == ContractStatus.CANCELLED
        assert player.credits == 500

    def test_penalty_clamped_at_zero_not_negative(self) -> None:
        c = _contract(status=ContractStatus.ACCEPTED, penalty=Decimal("1000.00"))
        player = _player(credits=100)
        c.acceptor_player_id = player.id
        db = _FakeSession(contracts=[c], players=[player])

        contract_service.abandon(db, c.id, player.id, now=_NOW)
        assert player.credits == 0

    def test_only_from_accepted(self) -> None:
        c = _contract(status=ContractStatus.POSTED)
        player = _player()
        c.acceptor_player_id = player.id
        db = _FakeSession(contracts=[c], players=[player])
        with pytest.raises(ContractConflictError):
            contract_service.abandon(db, c.id, player.id, now=_NOW)


@pytest.mark.unit
class TestTransitionMatrixMutation:
    """The transition table is real, load-bearing DATA -- removing an edge
    breaks an otherwise DB-verified-legal transition, proving the dict is
    actually consulted rather than decorative."""

    def test_removing_accepted_to_completed_edge_409s_a_valid_transition(self, monkeypatch: pytest.MonkeyPatch) -> None:
        destination_id = uuid.uuid4()
        c = _contract(
            status=ContractStatus.ACCEPTED, destination_station_id=destination_id,
            quantity=10, payment=Decimal("500"),
        )
        ship = _real_ship(cargo={"capacity": 500, "used": 10, "contents": {"ore": 10}})
        player = _player(credits=0, is_docked=True, current_port_id=destination_id, current_ship=ship)
        c.acceptor_player_id = player.id
        db = _FakeSession(contracts=[c], players=[player])

        stripped = {
            ContractStatus.POSTED: contract_service.LEGAL_TRANSITIONS[ContractStatus.POSTED],
            ContractStatus.ACCEPTED: frozenset({ContractStatus.CANCELLED}),  # COMPLETED removed
        }
        monkeypatch.setattr(contract_service, "LEGAL_TRANSITIONS", stripped)

        with pytest.raises(ContractConflictError, match="illegal_transition"):
            contract_service.complete(db, c.id, player.id, now=_NOW)
        assert c.status == ContractStatus.ACCEPTED  # untouched -- DB round-trip never happened

    @pytest.mark.parametrize(
        "from_status,action",
        [
            (ContractStatus.ACCEPTED, "accept"),
            (ContractStatus.COMPLETED, "accept"),
            (ContractStatus.CANCELLED, "accept"),
            (ContractStatus.EXPIRED, "accept"),
            (ContractStatus.POSTED, "complete"),
            (ContractStatus.COMPLETED, "complete"),
            (ContractStatus.CANCELLED, "complete"),
            (ContractStatus.POSTED, "abandon"),
            (ContractStatus.COMPLETED, "abandon"),
            (ContractStatus.EXPIRED, "abandon"),
        ],
    )
    def test_illegal_transition_sweep_all_409(self, from_status: ContractStatus, action: str) -> None:
        destination_id = uuid.uuid4()
        c = _contract(status=from_status, destination_station_id=destination_id, deadline=_NOW + timedelta(hours=1))
        ship = _real_ship(cargo={"capacity": 500, "used": 50, "contents": {"ore": 50}})
        player = _player(credits=5000, is_docked=True, current_port_id=destination_id, current_ship=ship)
        c.acceptor_player_id = player.id
        db = _FakeSession(contracts=[c], players=[player])

        func = getattr(contract_service, action)
        with pytest.raises(ContractConflictError):
            func(db, c.id, player.id, now=_NOW)
        assert c.status == from_status  # never mutated


@pytest.mark.unit
class TestSweepExpiredContracts:
    def test_only_posted_and_strictly_past_deadline_swept(self) -> None:
        posted_past = _contract(status=ContractStatus.POSTED, deadline=_NOW - timedelta(minutes=1))
        posted_future = _contract(status=ContractStatus.POSTED, deadline=_NOW + timedelta(minutes=1))
        posted_exact = _contract(status=ContractStatus.POSTED, deadline=_NOW)  # NOT strictly past
        accepted_past = _contract(status=ContractStatus.ACCEPTED, deadline=_NOW - timedelta(minutes=1))
        db = _FakeSession(contracts=[posted_past, posted_future, posted_exact, accepted_past])

        result = contract_service.sweep_expired_contracts(db, now=_NOW)

        assert result == {"expired": 1}
        assert posted_past.status == ContractStatus.EXPIRED
        assert posted_future.status == ContractStatus.POSTED
        assert posted_exact.status == ContractStatus.POSTED
        assert accepted_past.status == ContractStatus.ACCEPTED  # untouched -- not posted


@pytest.mark.unit
class TestSweepExpiredAcceptedContracts:
    """WO-DRIFT-econ-accepted-deadline-expiry -- the ACCEPTED-past-deadline
    twin of TestSweepExpiredContracts above. NPC-issued fixtures only here
    (no escrow_amount/escrow_state on this file's plain `_contract()`
    fixture -- the escrow-refund branch is gated on `issuer_type ==
    PLAYER` and never reads those attrs for an NPC row); the PLAYER-issued
    escrow-conservation half lives in test_contract_escrow.py alongside its
    sibling abandon()/sweep_expired_contracts refund-idiom tests."""

    def test_only_accepted_and_strictly_past_deadline_swept(self) -> None:
        acceptor = _player(credits=5000)
        accepted_past = _contract(
            status=ContractStatus.ACCEPTED, deadline=_NOW - timedelta(minutes=1),
            acceptor_player_id=acceptor.id,
        )
        accepted_future = _contract(
            status=ContractStatus.ACCEPTED, deadline=_NOW + timedelta(minutes=1),
            acceptor_player_id=acceptor.id,
        )
        accepted_exact = _contract(
            status=ContractStatus.ACCEPTED, deadline=_NOW, acceptor_player_id=acceptor.id,
        )  # NOT strictly past
        posted_past = _contract(status=ContractStatus.POSTED, deadline=_NOW - timedelta(minutes=1))
        db = _FakeSession(
            contracts=[accepted_past, accepted_future, accepted_exact, posted_past],
            players=[acceptor],
        )

        result = contract_service.sweep_expired_accepted_contracts(db, now=_NOW)

        assert result == {"expired": 1}
        assert accepted_past.status == ContractStatus.EXPIRED
        assert accepted_future.status == ContractStatus.ACCEPTED
        assert accepted_exact.status == ContractStatus.ACCEPTED
        assert posted_past.status == ContractStatus.POSTED  # untouched -- not accepted

    def test_charges_acceptor_the_penalty_npc_issued(self) -> None:
        acceptor = _player(credits=5000)
        c = _contract(
            status=ContractStatus.ACCEPTED, deadline=_NOW - timedelta(minutes=1),
            acceptor_player_id=acceptor.id, penalty=Decimal("750.00"),
        )
        db = _FakeSession(contracts=[c], players=[acceptor])

        result = contract_service.sweep_expired_accepted_contracts(db, now=_NOW)

        assert result == {"expired": 1}
        assert c.status == ContractStatus.EXPIRED
        assert acceptor.credits == 4250  # 5000 - 750, same flat-penalty math as abandon()

    def test_penalty_clamped_at_zero_not_negative(self) -> None:
        acceptor = _player(credits=100)
        c = _contract(
            status=ContractStatus.ACCEPTED, deadline=_NOW - timedelta(minutes=1),
            acceptor_player_id=acceptor.id, penalty=Decimal("1000.00"),
        )
        db = _FakeSession(contracts=[c], players=[acceptor])

        contract_service.sweep_expired_accepted_contracts(db, now=_NOW)
        assert acceptor.credits == 0

    def test_multiple_due_rows_all_expired_and_charged(self) -> None:
        acceptor = _player(credits=5000)
        c1 = _contract(
            status=ContractStatus.ACCEPTED, deadline=_NOW - timedelta(hours=1),
            acceptor_player_id=acceptor.id, penalty=Decimal("100.00"),
        )
        c2 = _contract(
            status=ContractStatus.ACCEPTED, deadline=_NOW - timedelta(minutes=1),
            acceptor_player_id=acceptor.id, penalty=Decimal("200.00"),
        )
        db = _FakeSession(contracts=[c1, c2], players=[acceptor])

        result = contract_service.sweep_expired_accepted_contracts(db, now=_NOW)

        assert result == {"expired": 2}
        assert c1.status == ContractStatus.EXPIRED
        assert c2.status == ContractStatus.EXPIRED
        assert acceptor.credits == 4700  # 5000 - 100 - 200

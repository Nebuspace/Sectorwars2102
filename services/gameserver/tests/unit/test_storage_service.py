"""WO-STORE-DEPOSIT-FLOW -- storage_service.get_or_create_locker() /
deposit_cargo().

DB-free: a real SQLAlchemy WHERE-clause interpreter backs both
`.filter(...).first()` and `db.execute(update(...))` (the SAME
`_guarded_transition` machinery contract_service.complete() itself runs
on), so the "deposit_cargo actually delegates to the canonical
completer" tests exercise REAL contract_service.complete() logic, not a
mock standing in for it -- proving the cargo-bridge integration
end-to-end, not just that some function got called. Mirrors this
codebase's established fake-query-filter-interpreter-pattern /
sqla-update-values-db-free-proof convention (test_contract_service.py's
own precedent, extended here with Ship/StorageLocker/
ContractCargoDeposit support plus a fake aggregate-sum query for
storage_service's own accumulated-quantity check).

`flag_modified` (cargo mutations, in both storage_service and
contract_service.complete()) requires a REAL ORM instance -- `_ship()`
builds one, matching test_contract_service.py's own `_real_ship()`
precedent.
"""
from __future__ import annotations

import operator
import uuid
from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace
from typing import Any, List, Optional

import pytest
from sqlalchemy.sql.operators import in_op

from src.models.contract import Contract, ContractIssuerType, ContractStatus
from src.models.player import Player
from src.models.ship import Ship, ShipType
from src.models.storage_locker import ContractCargoDeposit, StorageLocker, StorageLockerStatus
from src.services import storage_service
from src.services.storage_service import StorageError, StorageNotFoundError


# --- WHERE-clause interpreter (real SQLAlchemy clauses) ------------------ #

def _match(row: Any, cond: Any) -> bool:
    col_name = cond.left.key
    row_val = getattr(row, col_name, None)
    if cond.operator is operator.eq:
        return row_val == cond.right.value
    if cond.operator is in_op:
        return row_val in cond.right.value
    raise NotImplementedError(f"unsupported operator {cond.operator!r}")


class _FakeResult:
    def __init__(self, rowcount: int) -> None:
        self.rowcount = rowcount


class _FakeQuery:
    def __init__(
        self, rows: List[Any], criteria: Optional[List[Any]] = None,
        session: Optional["_FakeSession"] = None, entity: Optional[str] = None,
    ) -> None:
        self._rows = rows
        self._criteria = criteria or []
        self._session = session
        self._entity = entity

    def filter(self, *conditions: Any) -> "_FakeQuery":
        return _FakeQuery(self._rows, self._criteria + list(conditions), self._session, self._entity)

    def with_for_update(self) -> "_FakeQuery":
        if self._session is not None:
            self._session.for_update_calls.append(self._entity)
        return self

    def _matching(self) -> List[Any]:
        return [row for row in self._rows if all(_match(row, c) for c in self._criteria)]

    def first(self) -> Any:
        matches = self._matching()
        return matches[0] if matches else None

    def all(self) -> List[Any]:
        return self._matching()


class _FakeSumQuery:
    """Stands in for db.query(func.coalesce(func.sum(...), 0)).filter(...)
    .scalar() -- storage_service's own accumulated-quantity check. Not a
    general aggregate engine; this file's only sum query is over
    ContractCargoDeposit.quantity, so this is deliberately narrow."""

    def __init__(self, rows: List[Any], criteria: Optional[List[Any]] = None) -> None:
        self._rows = rows
        self._criteria = criteria or []

    def filter(self, *conditions: Any) -> "_FakeSumQuery":
        return _FakeSumQuery(self._rows, self._criteria + list(conditions))

    def scalar(self) -> int:
        matches = [row for row in self._rows if all(_match(row, c) for c in self._criteria)]
        return sum(int(row.quantity) for row in matches)


class _FakeSession:
    def __init__(
        self, *, contracts=None, players=None, ships=None, lockers=None, deposits=None,
    ) -> None:
        self.contracts = contracts or []
        self.players = players or []
        # Auto-derived from each player's own current_ship, matching the
        # beacon/gate test suites' own convention, so every EXISTING
        # _FakeSession(players=[...]) call site keeps working without
        # individually passing ships= too.
        self.ships = ships if ships is not None else [
            p.current_ship for p in self.players if getattr(p, "current_ship", None) is not None
        ]
        self.lockers = lockers or []
        self.deposits = deposits or []
        self.flush_calls = 0
        self.for_update_calls: List[Optional[str]] = []

    def query(self, *entities: Any) -> Any:
        head = entities[0]
        if head is Contract:
            return _FakeQuery(self.contracts, session=self, entity="Contract")
        if head is Player:
            return _FakeQuery(self.players, session=self, entity="Player")
        if head is Ship:
            return _FakeQuery(self.ships, session=self, entity="Ship")
        if head is StorageLocker:
            return _FakeQuery(self.lockers, session=self, entity="StorageLocker")
        if head is ContractCargoDeposit:
            return _FakeQuery(self.deposits, session=self, entity="ContractCargoDeposit")
        # Only remaining query shape this module issues: the aggregate
        # func.coalesce(func.sum(ContractCargoDeposit.quantity), 0).
        return _FakeSumQuery(self.deposits)

    def add(self, obj: Any) -> None:
        if isinstance(obj, StorageLocker):
            self.lockers.append(obj)
        elif isinstance(obj, ContractCargoDeposit):
            self.deposits.append(obj)

    def execute(self, stmt: Any) -> _FakeResult:
        # _guarded_transition's atomic UPDATE contracts SET ... WHERE ...
        # (contract_service.complete's own concurrency gate -- reused
        # here for real, not mocked).
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


# --- fixtures -------------------------------------------------------------- #

def _contract(**overrides: Any) -> SimpleNamespace:
    base = dict(
        id=uuid.uuid4(),
        issuer_type=ContractIssuerType.NPC,
        issuer_id=uuid.uuid4(),
        acceptor_player_id=None,
        origin_station_id=uuid.uuid4(),
        destination_station_id=uuid.uuid4(),
        commodity_type="ore",
        quantity=100,
        status=ContractStatus.ACCEPTED,
        payment=Decimal("1000.00"),
        penalty=Decimal("1000.00"),
        acceptance_fee_pct=Decimal("2.0"),
        deadline=datetime(2026, 1, 2, tzinfo=UTC),
        posted_at=datetime(2026, 1, 1, tzinfo=UTC),
        accepted_at=datetime(2026, 1, 1, tzinfo=UTC),
        completed_at=None,
        escrow_state=None,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def _ship(**overrides: Any) -> Ship:
    """flag_modified() (both storage_service's own deposit + contract_
    service.complete()'s cargo decrement) requires a REAL ORM instance."""
    base = dict(
        id=uuid.uuid4(), name="Test Freighter", type=ShipType.LIGHT_FREIGHTER,
        owner_id=uuid.uuid4(), sector_id=1, is_destroyed=False,
        cargo={"capacity": 500, "used": 0, "contents": {}},
    )
    base.update(overrides)
    return Ship(**base)


def _player(**overrides: Any) -> SimpleNamespace:
    ship = overrides.pop("current_ship", None)
    player_id = overrides.pop("id", None) or uuid.uuid4()
    if ship is None:
        ship = _ship(owner_id=player_id, cargo={"capacity": 500, "used": 60, "contents": {"ore": 60}})
    else:
        # storage_service's own Ship query filters on Ship.owner_id ==
        # player.id -- a caller-supplied custom ship must always belong
        # to THIS player, regardless of whatever owner_id _ship()'s own
        # default happened to generate.
        ship.owner_id = player_id
    base = dict(
        id=player_id, credits=10000, is_docked=True, current_port_id=None,
        current_ship_id=ship.id, current_ship=ship,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def _locker(contract: SimpleNamespace, owner_id: uuid.UUID, **overrides: Any) -> StorageLocker:
    base = dict(
        id=uuid.uuid4(), owner_player_id=owner_id, station_id=contract.destination_station_id,
        contract_id=contract.id, status=StorageLockerStatus.ACTIVE,
        rent_rate=Decimal("1"), accrued_fee=Decimal("0"),
    )
    base.update(overrides)
    return StorageLocker(**base)


def _deposit(locker: StorageLocker, **overrides: Any) -> ContractCargoDeposit:
    base = dict(
        id=uuid.uuid4(), locker_id=locker.id, commodity="ore", quantity=10,
        deposited_by=uuid.uuid4(),
    )
    base.update(overrides)
    return ContractCargoDeposit(**base)


# --- get_or_create_locker --------------------------------------------------- #

@pytest.mark.unit
class TestGetOrCreateLocker:
    def test_creates_a_new_locker_at_the_destination_station(self) -> None:
        contract = _contract()
        player = _player(id=contract.acceptor_player_id or uuid.uuid4())
        contract.acceptor_player_id = player.id
        db = _FakeSession(contracts=[contract], players=[player])

        locker = storage_service.get_or_create_locker(db, player.id, contract.id)

        assert locker.station_id == contract.destination_station_id
        assert locker.owner_player_id == player.id
        assert locker.contract_id == contract.id
        assert locker.status == StorageLockerStatus.ACTIVE
        assert locker in db.lockers

    def test_second_call_returns_the_existing_locker_not_a_duplicate(self) -> None:
        contract = _contract()
        player = _player(id=uuid.uuid4())
        contract.acceptor_player_id = player.id
        db = _FakeSession(contracts=[contract], players=[player])

        first = storage_service.get_or_create_locker(db, player.id, contract.id)
        second = storage_service.get_or_create_locker(db, player.id, contract.id)

        assert first.id == second.id
        assert len(db.lockers) == 1

    def test_rejects_contract_not_accepted_by_this_player(self) -> None:
        contract = _contract(acceptor_player_id=uuid.uuid4())
        player = _player(id=uuid.uuid4())
        db = _FakeSession(contracts=[contract], players=[player])

        with pytest.raises(StorageError, match="not accepted by you"):
            storage_service.get_or_create_locker(db, player.id, contract.id)

    def test_rejects_contract_not_in_accepted_status(self) -> None:
        contract = _contract(status=ContractStatus.POSTED, acceptor_player_id=None)
        player = _player(id=uuid.uuid4())
        db = _FakeSession(contracts=[contract], players=[player])

        with pytest.raises(StorageError, match="stale_status"):
            storage_service.get_or_create_locker(db, player.id, contract.id)

    def test_locks_the_player_row(self) -> None:
        contract = _contract()
        player = _player(id=uuid.uuid4())
        contract.acceptor_player_id = player.id
        db = _FakeSession(contracts=[contract], players=[player])

        storage_service.get_or_create_locker(db, player.id, contract.id)

        assert "Player" in db.for_update_calls


# --- deposit_cargo: accumulation + guards ----------------------------------- #

@pytest.mark.unit
class TestDepositAccumulates:
    def test_deposit_creates_an_audit_row_and_removes_ship_cargo(self) -> None:
        contract = _contract(quantity=100)
        player = _player(id=uuid.uuid4())
        contract.acceptor_player_id = player.id
        player.current_port_id = contract.destination_station_id
        locker = _locker(contract, player.id)
        db = _FakeSession(contracts=[contract], players=[player], lockers=[locker])

        result = storage_service.deposit_cargo(db, locker.id, player.id, 30)

        assert result["deposited"] == 30
        assert result["accumulated"] == 30
        assert result["completed"] is False
        assert player.current_ship.cargo["contents"]["ore"] == 30  # 60 - 30
        assert len(db.deposits) == 1
        assert db.deposits[0].quantity == 30

    def test_multiple_installments_accumulate(self) -> None:
        contract = _contract(quantity=100)
        player = _player(id=uuid.uuid4(), current_ship=_ship(cargo={"capacity": 500, "used": 200, "contents": {"ore": 200}}))
        contract.acceptor_player_id = player.id
        player.current_port_id = contract.destination_station_id
        locker = _locker(contract, player.id)
        db = _FakeSession(contracts=[contract], players=[player], lockers=[locker])

        storage_service.deposit_cargo(db, locker.id, player.id, 40)
        result = storage_service.deposit_cargo(db, locker.id, player.id, 35)

        assert result["accumulated"] == 75
        assert result["completed"] is False
        assert len(db.deposits) == 2


@pytest.mark.unit
class TestDepositGuards:
    def test_wrong_station_rejected(self) -> None:
        contract = _contract()
        player = _player(id=uuid.uuid4())
        contract.acceptor_player_id = player.id
        player.current_port_id = uuid.uuid4()  # NOT the locker's station
        locker = _locker(contract, player.id)
        db = _FakeSession(contracts=[contract], players=[player], lockers=[locker])

        with pytest.raises(StorageError, match="wrong_station"):
            storage_service.deposit_cargo(db, locker.id, player.id, 10)
        assert db.deposits == []

    def test_not_owner_rejected(self) -> None:
        contract = _contract()
        owner = _player(id=uuid.uuid4())
        stranger = _player(id=uuid.uuid4())
        contract.acceptor_player_id = owner.id
        locker = _locker(contract, owner.id)
        db = _FakeSession(contracts=[contract], players=[owner, stranger], lockers=[locker])

        with pytest.raises(StorageError, match="does not belong to you"):
            storage_service.deposit_cargo(db, locker.id, stranger.id, 10)

    def test_insufficient_cargo_rejected(self) -> None:
        contract = _contract()
        player = _player(id=uuid.uuid4(), current_ship=_ship(cargo={"capacity": 500, "used": 5, "contents": {"ore": 5}}))
        contract.acceptor_player_id = player.id
        player.current_port_id = contract.destination_station_id
        locker = _locker(contract, player.id)
        db = _FakeSession(contracts=[contract], players=[player], lockers=[locker])

        with pytest.raises(StorageError, match="insufficient_cargo"):
            storage_service.deposit_cargo(db, locker.id, player.id, 50)
        assert db.deposits == []
        assert player.current_ship.cargo["contents"]["ore"] == 5  # untouched

    def test_wrong_state_locker_not_active_rejected(self) -> None:
        contract = _contract()
        player = _player(id=uuid.uuid4())
        contract.acceptor_player_id = player.id
        player.current_port_id = contract.destination_station_id
        locker = _locker(contract, player.id, status=StorageLockerStatus.RELEASED)
        db = _FakeSession(contracts=[contract], players=[player], lockers=[locker])

        with pytest.raises(StorageError, match="locker_not_active"):
            storage_service.deposit_cargo(db, locker.id, player.id, 10)

    def test_wrong_state_contract_not_accepted_rejected(self) -> None:
        contract = _contract(status=ContractStatus.COMPLETED)
        player = _player(id=uuid.uuid4())
        contract.acceptor_player_id = player.id
        player.current_port_id = contract.destination_station_id
        locker = _locker(contract, player.id)
        db = _FakeSession(contracts=[contract], players=[player], lockers=[locker])

        with pytest.raises(StorageError, match="stale_status"):
            storage_service.deposit_cargo(db, locker.id, player.id, 10)

    def test_invalid_quantity_rejected(self) -> None:
        contract = _contract()
        player = _player(id=uuid.uuid4())
        contract.acceptor_player_id = player.id
        locker = _locker(contract, player.id)
        db = _FakeSession(contracts=[contract], players=[player], lockers=[locker])

        with pytest.raises(StorageError, match="invalid_quantity"):
            storage_service.deposit_cargo(db, locker.id, player.id, 0)
        with pytest.raises(StorageError, match="invalid_quantity"):
            storage_service.deposit_cargo(db, locker.id, player.id, -5)

    def test_locker_not_found_404s(self) -> None:
        player = _player(id=uuid.uuid4())
        db = _FakeSession(players=[player])
        with pytest.raises(StorageNotFoundError):
            storage_service.deposit_cargo(db, uuid.uuid4(), player.id, 10)


# --- concurrency: lock order ------------------------------------------------ #

@pytest.mark.unit
class TestDepositLockOrder:
    def test_locker_locked_before_player_before_ship(self) -> None:
        contract = _contract(quantity=100)
        player = _player(id=uuid.uuid4())
        contract.acceptor_player_id = player.id
        player.current_port_id = contract.destination_station_id
        locker = _locker(contract, player.id)
        db = _FakeSession(contracts=[contract], players=[player], lockers=[locker])

        storage_service.deposit_cargo(db, locker.id, player.id, 10)

        assert db.for_update_calls == ["StorageLocker", "Player", "Ship"]


# --- complete-on-full: the cargo-bridge delegation to contract_service ----- #

@pytest.mark.unit
class TestCompleteOnFullQuantity:
    def test_final_deposit_auto_completes_via_contract_service(self) -> None:
        """The integration proof: deposit_cargo genuinely drives REAL
        contract_service.complete() logic (guarded transition, payout,
        cargo decrement) via the fake session's shared execute()/query()
        machinery -- not a mocked stand-in."""
        contract = _contract(quantity=50, payment=Decimal("2000.00"))
        player = _player(id=uuid.uuid4(), credits=100)
        contract.acceptor_player_id = player.id
        player.current_port_id = contract.destination_station_id
        locker = _locker(contract, player.id)
        db = _FakeSession(contracts=[contract], players=[player], lockers=[locker])

        result = storage_service.deposit_cargo(db, locker.id, player.id, 50)

        assert result["completed"] is True
        assert result["accumulated"] == 50
        assert contract.status == ContractStatus.COMPLETED
        assert player.credits == 100 + 2000  # contract_service.complete's own payout
        assert locker.status == StorageLockerStatus.RELEASED

    def test_ship_cargo_nets_to_zero_after_the_bridge_and_complete(self) -> None:
        """The cargo-bridge's own core invariant: the ship's cargo for
        this commodity ends up at (starting - deposited), the SAME as if
        the deposit alone had happened -- the bridge's temporary
        injection is fully corrected by complete()'s own decrement,
        never externally observable."""
        contract = _contract(quantity=50)
        player = _player(id=uuid.uuid4(), current_ship=_ship(cargo={"capacity": 500, "used": 50, "contents": {"ore": 50}}))
        contract.acceptor_player_id = player.id
        player.current_port_id = contract.destination_station_id
        locker = _locker(contract, player.id)
        db = _FakeSession(contracts=[contract], players=[player], lockers=[locker])

        storage_service.deposit_cargo(db, locker.id, player.id, 50)

        assert player.current_ship.cargo["contents"]["ore"] == 0

    def test_multi_trip_then_final_installment_completes(self) -> None:
        contract = _contract(quantity=100, payment=Decimal("500.00"))
        player = _player(id=uuid.uuid4(), credits=0, current_ship=_ship(cargo={"capacity": 500, "used": 200, "contents": {"ore": 200}}))
        contract.acceptor_player_id = player.id
        player.current_port_id = contract.destination_station_id
        locker = _locker(contract, player.id)
        db = _FakeSession(contracts=[contract], players=[player], lockers=[locker])

        r1 = storage_service.deposit_cargo(db, locker.id, player.id, 60)
        assert r1["completed"] is False

        r2 = storage_service.deposit_cargo(db, locker.id, player.id, 40)
        assert r2["completed"] is True
        assert r2["accumulated"] == 100
        assert contract.status == ContractStatus.COMPLETED
        assert player.credits == 500

    def test_deposit_from_a_different_ship_than_earlier_installments_still_completes(self) -> None:
        """Canon: 'any ship can fulfill any contract over enough trips' --
        the final delivering ship doesn't have to be the one that carried
        every earlier installment."""
        contract = _contract(quantity=50, payment=Decimal("100.00"))
        player = _player(id=uuid.uuid4(), credits=0)
        contract.acceptor_player_id = player.id
        player.current_port_id = contract.destination_station_id
        locker = _locker(contract, player.id)
        # Two prior installments already recorded (as if deposited by an
        # earlier, different ship) -- only the audit trail matters, not
        # which ship carried them.
        earlier_a = _deposit(locker, quantity=20, deposited_by=player.id)
        earlier_b = _deposit(locker, quantity=20, deposited_by=player.id)
        db = _FakeSession(
            contracts=[contract], players=[player], lockers=[locker],
            deposits=[earlier_a, earlier_b],
        )
        # A DIFFERENT, freshly-docked ship carries the final 10 units.
        player.current_ship = _ship(owner_id=player.id, cargo={"capacity": 500, "used": 10, "contents": {"ore": 10}})
        player.current_ship_id = player.current_ship.id
        db.ships = [player.current_ship]

        result = storage_service.deposit_cargo(db, locker.id, player.id, 10)

        assert result["completed"] is True
        assert result["accumulated"] == 50
        assert contract.status == ContractStatus.COMPLETED
        assert player.credits == 100

    def test_small_ship_completes_a_contract_far_larger_than_its_own_hold(self) -> None:
        """The feature's entire raison d'etre (team-lead's own framing):
        a 50-hold ship completing a 150-unit contract over three trips,
        each individually under the ship's own capacity. The bridge's
        FINAL injection transiently pushes cargo['used'] to 150 against
        a capacity of only 50 -- neither the bridge nor contract_service.
        complete() capacity-checks (verified against complete()'s actual
        source in this WO's report), so this must still complete
        cleanly. Regression pin: if a capacity check is EVER added to
        complete() later, this test is the loud, specific alarm that a
        future contributor broke the over-capacity delivery path."""
        contract = _contract(quantity=150, payment=Decimal("300.00"))
        player = _player(
            id=uuid.uuid4(), credits=0,
            current_ship=_ship(cargo={"capacity": 50, "used": 50, "contents": {"ore": 50}}),
        )
        contract.acceptor_player_id = player.id
        player.current_port_id = contract.destination_station_id
        locker = _locker(contract, player.id)
        db = _FakeSession(contracts=[contract], players=[player], lockers=[locker])

        # Trip 1: a 50-hold ship can never carry more than 50 at once.
        r1 = storage_service.deposit_cargo(db, locker.id, player.id, 50)
        assert r1["completed"] is False
        assert r1["accumulated"] == 50

        # Trip 2: "return to the origin, reload" -- same ship, refilled.
        player.current_ship.cargo = {"capacity": 50, "used": 50, "contents": {"ore": 50}}
        r2 = storage_service.deposit_cargo(db, locker.id, player.id, 50)
        assert r2["completed"] is False
        assert r2["accumulated"] == 100

        # Trip 3: final installment -- triggers the bridge injecting the
        # FULL 150-unit requirement onto a 50-capacity ship.
        player.current_ship.cargo = {"capacity": 50, "used": 50, "contents": {"ore": 50}}
        r3 = storage_service.deposit_cargo(db, locker.id, player.id, 50)

        assert r3["completed"] is True
        assert r3["accumulated"] == 150
        assert contract.status == ContractStatus.COMPLETED
        assert player.credits == 300
        assert locker.status == StorageLockerStatus.RELEASED
        # Net-zero holds even in the over-capacity case: nothing left
        # over from the phantom injection once complete() decrements it.
        assert player.current_ship.cargo["contents"]["ore"] == 0

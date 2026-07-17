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
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from typing import Any, List, Optional

import pytest
from sqlalchemy.sql.operators import in_op

from src.models.contract import Contract, ContractEscrowState, ContractIssuerType, ContractStatus, ContractType
from src.models.player import Player
from src.models.ship import Ship, ShipType
from src.models.storage_locker import ContractCargoDeposit, StorageLocker, StorageLockerStatus
from src.services import contract_service, storage_service
from src.services.storage_service import StorageError, StorageNotFoundError


# --- WHERE-clause interpreter (real SQLAlchemy clauses) ------------------ #

def _match(row: Any, cond: Any) -> bool:
    col_name = cond.left.key
    row_val = getattr(row, col_name, None)
    if cond.operator is operator.eq:
        return row_val == cond.right.value
    if cond.operator is in_op:
        return row_val in cond.right.value
    if cond.operator is operator.lt:
        # WO-STORE-EXPIRY-CLAIMABLE: contract_service.sweep_expired_
        # accepted_contracts' own `Contract.deadline < now` candidate
        # query -- driven for real (not mocked) in the combined-sweep
        # tests below, mirroring test_contract_service.py's own _match.
        return row_val < cond.right.value
    raise NotImplementedError(f"unsupported operator {cond.operator!r}")


class _FakeNestedTransaction:
    """No-op savepoint passthrough for db.begin_nested() -- sweep_
    expired_accepted_contracts' own per-row credit-effects isolation.
    Mirrors test_contract_service.py's own _FakeNestedTransaction
    exactly; never swallows an exception (a single-threaded fake can't
    reproduce real SAVEPOINT rollback of Python attribute mutations)."""

    def __enter__(self) -> "_FakeNestedTransaction":
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> bool:
        return False


class _FakeResult:
    def __init__(self, rowcount: int) -> None:
        self.rowcount = rowcount


class _FakeQuery:
    def __init__(
        self, rows: List[Any], criteria: Optional[List[Any]] = None,
        session: Optional["_FakeSession"] = None, entity: Optional[str] = None,
        order_by_cols: Optional[List[Any]] = None,
    ) -> None:
        self._rows = rows
        self._criteria = criteria or []
        self._session = session
        self._entity = entity
        self._order_by_cols = order_by_cols or []

    def filter(self, *conditions: Any) -> "_FakeQuery":
        return _FakeQuery(
            self._rows, self._criteria + list(conditions), self._session, self._entity, self._order_by_cols,
        )

    def order_by(self, *columns: Any) -> "_FakeQuery":
        # WO-STORE-EXPIRY-CLAIMABLE: _consume_deposits' oldest-first
        # retrieval order. Stable-sorts by each column's .key, LAST
        # column first (Python's sort stability makes repeated single-
        # key sorts equivalent to a real multi-column ORDER BY) -- this
        # file only ever passes one column, but this stays correct if a
        # future caller passes more.
        return _FakeQuery(
            self._rows, self._criteria, self._session, self._entity, self._order_by_cols + list(columns),
        )

    def with_for_update(self, skip_locked: bool = False) -> "_FakeQuery":
        if self._session is not None:
            self._session.for_update_calls.append(self._entity)
        if skip_locked and self._session is not None and self._session.contended_locker_ids:
            # WO-STORE-EXPIRY-CLAIMABLE + D19: gate_contract_expiry_on_
            # locker's own skip_locked probe -- a single-threaded fake
            # can't reproduce REAL Postgres row contention, so this is an
            # explicit, documented SIMULATION: rows whose id is listed in
            # the session's contended_locker_ids are excluded from THIS
            # query's results, standing in for "another transaction
            # already holds this row's lock." Real contention proof is
            # the live-Postgres two-connection CI leg, not this.
            rows = [r for r in self._rows if getattr(r, "id", None) not in self._session.contended_locker_ids]
            return _FakeQuery(rows, self._criteria, self._session, self._entity, self._order_by_cols)
        return self

    def populate_existing(self) -> "_FakeQuery":
        # WO-MONEY-REREAD-CLASS: no-op passthrough -- contract_service.
        # _load_player now chains .populate_existing() ahead of .with_for_
        # update() on every for_update=True re-read (settle_fee's owner
        # re-lock, _load_and_lock_deposit_targets' / _load_and_lock_retrieve_
        # targets' player re-lock). Deliberately does NOT touch
        # self._session.for_update_calls -- that recording lives in
        # with_for_update() itself, called right after this in the real
        # chain, so this file's lock-order assertions (TestDepositLockOrder /
        # TestSettleFee's own) are unaffected. See money-reread-class-fake-
        # query-passthrough in mack's project memory.
        return self

    def _matching(self) -> List[Any]:
        matches = [row for row in self._rows if all(_match(row, c) for c in self._criteria)]
        for col in reversed(self._order_by_cols):
            matches.sort(key=lambda row: getattr(row, col.key))
        return matches

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


class _FakeScalarQuery:
    """Stands in for db.query(Model.col1, Model.col2, ...) -- COLUMN-ONLY
    queries (Q2 mitigation-a's own scalar pre-check shape, deliberately
    never populating a full ORM object into the session's identity map
    -- see _prelock_deposit_guard's own docstring for why). .first()
    returns a bare value for a single column or a tuple for multiple
    (matching real SQLAlchemy Row unpacking closely enough for this
    file's own tuple-unpack / .scalar() call sites); .scalar() returns
    the bare single-column value."""

    def __init__(self, rows: List[Any], columns: List[Any], criteria: Optional[List[Any]] = None) -> None:
        self._rows = rows
        self._columns = columns
        self._criteria = criteria or []

    def filter(self, *conditions: Any) -> "_FakeScalarQuery":
        return _FakeScalarQuery(self._rows, self._columns, self._criteria + list(conditions))

    def _matching(self) -> List[Any]:
        return [row for row in self._rows if all(_match(row, c) for c in self._criteria)]

    def _extract(self, row: Any) -> Any:
        values = tuple(getattr(row, col.key) for col in self._columns)
        return values[0] if len(values) == 1 else values

    def first(self) -> Any:
        matches = self._matching()
        return self._extract(matches[0]) if matches else None

    def scalar(self) -> Any:
        return self.first()


class _FakeSession:
    def __init__(
        self, *, contracts=None, players=None, ships=None, lockers=None, deposits=None,
        contended_locker_ids=None,
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
        # WO-STORE-EXPIRY-CLAIMABLE + D19: locker ids to simulate as
        # SKIP LOCKED-contended -- see _FakeQuery.with_for_update's own
        # comment for what this stands in for (and its limits).
        self.contended_locker_ids = contended_locker_ids or set()

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
        if hasattr(head, "class_"):
            # Column-only query (Q2 mitigation-a's scalar pre-check
            # shape, e.g. db.query(StorageLocker.station_id, ...)) --
            # route by the column's OWN mapped class, never touching the
            # identity map (no _FakeQuery/session tracking at all, since
            # a real scalar query never populates an ORM object either).
            rows_by_class = {
                Contract: self.contracts, Player: self.players, Ship: self.ships,
                StorageLocker: self.lockers, ContractCargoDeposit: self.deposits,
            }
            return _FakeScalarQuery(rows_by_class[head.class_], list(entities))
        # Only remaining query shape this module issues: the aggregate
        # func.coalesce(func.sum(ContractCargoDeposit.quantity), 0).
        return _FakeSumQuery(self.deposits)

    def add(self, obj: Any) -> None:
        if isinstance(obj, StorageLocker):
            self.lockers.append(obj)
        elif isinstance(obj, ContractCargoDeposit):
            self.deposits.append(obj)

    def delete(self, obj: Any) -> None:
        # WO-STORE-EXPIRY-CLAIMABLE: _consume_deposits deletes a fully-
        # consumed ContractCargoDeposit row.
        if isinstance(obj, ContractCargoDeposit) and obj in self.deposits:
            self.deposits.remove(obj)
        elif isinstance(obj, StorageLocker) and obj in self.lockers:
            self.lockers.remove(obj)

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

    def begin_nested(self) -> _FakeNestedTransaction:
        return _FakeNestedTransaction()

    def commit(self) -> None:
        raise AssertionError("service functions are flush-only -- the route commits")


# --- fixtures -------------------------------------------------------------- #

def _contract(**overrides: Any) -> SimpleNamespace:
    base = dict(
        id=uuid.uuid4(),
        issuer_type=ContractIssuerType.NPC,
        issuer_id=uuid.uuid4(),
        acceptor_player_id=None,
        # WO-CONTRACT-4-BULK: _process_one_sweep_expired_accepted_contracts_
        # candidate (contract_service.py) and abandon() now read `contract.
        # contract_type` unconditionally -- every fixture-built contract
        # needs this attribute, not just the tests that care about it.
        contract_type=ContractType.CARGO_DELIVERY,
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
        # WO-CONTRACT-1b-CLAIM-SAFETY: sweep_expired_accepted_contracts now
        # unconditionally reads these on every candidate (the claim-offset
        # engine) -- required on every fixture that function is exercised
        # against, not just insurance-specific tests.
        insurance_coverage_tier=None,
        insurance_premium_paid=Decimal("0"),
        insurance_claim_filed=False,
        insurance_pool_reserve=Decimal("0"),
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
    # last_fee_settled_at / created_at are server_default=func.now() --
    # a fresh, unflushed Python object never gets those without a real DB
    # round-trip, so a DB-free fixture must set them explicitly. Pinned
    # to "now" so an ordinary deposit test's settle_fee call sees ~0
    # elapsed days (a clean no-op) and isn't surprised by an unrelated
    # rent charge -- tests that actually exercise fee accrual override
    # last_fee_settled_at explicitly to the past.
    base = dict(
        id=uuid.uuid4(), owner_player_id=owner_id, station_id=contract.destination_station_id,
        contract_id=contract.id, status=StorageLockerStatus.ACTIVE,
        rent_rate=Decimal("1"), accrued_fee=Decimal("0"),
        last_fee_settled_at=datetime.now(UTC), created_at=datetime.now(UTC),
    )
    base.update(overrides)
    return StorageLocker(**base)


def _deposit(locker: StorageLocker, **overrides: Any) -> ContractCargoDeposit:
    # deposited_at is server_default=func.now() -- same reasoning as
    # _locker's own last_fee_settled_at/created_at fix: a fresh,
    # unflushed Python object never gets it without a real DB round-
    # trip. WO-STORE-EXPIRY-CLAIMABLE's _consume_deposits sorts by this
    # column, so it needs a real, sortable default now (previously no
    # code ever read it). Tests needing a SPECIFIC order override it
    # explicitly per-row.
    base = dict(
        id=uuid.uuid4(), locker_id=locker.id, commodity="ore", quantity=10,
        deposited_by=uuid.uuid4(), deposited_at=datetime.now(UTC),
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
        # Q2 mitigation-a (cipher MEDIUM-HIGH): the pre-lock station
        # check rejects BEFORE the Locker's with_for_update() lock is
        # ever acquired -- zero-cost, unlimited free-spam attempts
        # against your own locker previously acquired+contended this
        # lock on every single failed attempt.
        assert db.for_update_calls == []

    def test_wrong_station_never_contends_the_lock_even_with_a_real_locker_owned_by_the_caller(self) -> None:
        """The confirmed free-spam vector, spelled out directly: a
        player who owns a perfectly valid, ACTIVE locker but calls from
        the wrong station gets rejected without EVER acquiring that
        locker's row lock -- unlimited free attempts cost nothing and
        contend nothing, closing cipher's finding against the deposit-
        wins expiry gate (a contended locker used to be a lever to
        probabilistically defer your own deadline penalty)."""
        contract = _contract()
        player = _player(id=uuid.uuid4())
        contract.acceptor_player_id = player.id
        player.current_port_id = uuid.uuid4()  # anywhere but the locker's station
        locker = _locker(contract, player.id, status=StorageLockerStatus.ACTIVE)
        db = _FakeSession(contracts=[contract], players=[player], lockers=[locker])

        for _ in range(5):  # "unlimited free attempts" -- prove it holds repeatedly
            with pytest.raises(StorageError, match="wrong_station"):
                storage_service.deposit_cargo(db, locker.id, player.id, 10)

        assert db.for_update_calls == []
        assert locker.status == StorageLockerStatus.ACTIVE  # completely untouched

    def test_not_owner_rejected(self) -> None:
        contract = _contract()
        owner = _player(id=uuid.uuid4())
        stranger = _player(id=uuid.uuid4())
        contract.acceptor_player_id = owner.id
        locker = _locker(contract, owner.id)
        db = _FakeSession(contracts=[contract], players=[owner, stranger], lockers=[locker])

        with pytest.raises(StorageError, match="does not belong to you"):
            storage_service.deposit_cargo(db, locker.id, stranger.id, 10)

    def test_not_owner_rejected_the_same_way_regardless_of_station(self) -> None:
        """Q2 mitigation-a, cipher LOW-MED (the station-existence
        oracle): _prelock_deposit_guard checks ownership BEFORE station,
        so a non-owner gets the IDENTICAL generic rejection whether
        they're nowhere near the locker's station OR standing right at
        it -- no station information is ever revealed to someone probing
        a locker_id they don't own."""
        contract = _contract()
        owner = _player(id=uuid.uuid4())
        stranger_elsewhere = _player(id=uuid.uuid4())
        stranger_at_the_station = _player(id=uuid.uuid4())
        contract.acceptor_player_id = owner.id
        locker = _locker(contract, owner.id)
        stranger_elsewhere.current_port_id = uuid.uuid4()  # nowhere near it
        stranger_at_the_station.current_port_id = locker.station_id  # right at it
        db = _FakeSession(
            contracts=[contract], players=[owner, stranger_elsewhere, stranger_at_the_station],
            lockers=[locker],
        )

        with pytest.raises(StorageError, match="does not belong to you"):
            storage_service.deposit_cargo(db, locker.id, stranger_elsewhere.id, 10)
        with pytest.raises(StorageError, match="does not belong to you"):
            storage_service.deposit_cargo(db, locker.id, stranger_at_the_station.id, 10)

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
    def test_locker_locked_before_player_before_settle_before_ship(self) -> None:
        """WO-STORE-FEE-ACCRUAL: settle_fee's own internal re-lock of the
        Locker row (a harmless idempotent re-acquire, same session) now
        sits between deposit_cargo's own Player lock and its Ship lock --
        settle_fee doesn't lock Player here because this is a fresh
        locker with zero prior stored units (no rent owed yet), so its
        own Player-lock branch never fires. See the sibling test below
        for the WITH-stored-units case, where settle_fee's Player lock
        also appears."""
        contract = _contract(quantity=100)
        player = _player(id=uuid.uuid4())
        contract.acceptor_player_id = player.id
        player.current_port_id = contract.destination_station_id
        locker = _locker(contract, player.id)
        db = _FakeSession(contracts=[contract], players=[player], lockers=[locker])

        storage_service.deposit_cargo(db, locker.id, player.id, 10)

        assert db.for_update_calls == ["StorageLocker", "Player", "StorageLocker", "Ship"]

    def test_settle_fee_locks_locker_then_player_when_rent_is_owed(self) -> None:
        """The full lock chain when settle_fee's OWN Player-lock branch
        fires (stored_units > 0, some rent genuinely owed): deposit_
        cargo's Locker -> deposit_cargo's Player -> settle_fee's Locker
        (re-lock) -> settle_fee's Player (re-lock, to charge the fee) ->
        deposit_cargo's Ship."""
        contract = _contract(quantity=100)
        player = _player(id=uuid.uuid4(), credits=1000)
        contract.acceptor_player_id = player.id
        player.current_port_id = contract.destination_station_id
        locker = _locker(
            contract, player.id,
            last_fee_settled_at=datetime.now(UTC) - timedelta(days=1),
        )
        db = _FakeSession(contracts=[contract], players=[player], lockers=[locker])
        # A prior deposit already sitting in the locker -- something for
        # the elapsed day of rent to actually apply to.
        db.deposits = [_deposit(locker, quantity=20, deposited_by=player.id)]

        storage_service.deposit_cargo(db, locker.id, player.id, 10)

        assert db.for_update_calls == ["StorageLocker", "Player", "StorageLocker", "Player", "Ship"]


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


# --- WO-STORE-FEE-ACCRUAL: settle_fee -------------------------------------- #

@pytest.mark.unit
class TestSettleFee:
    def test_fee_is_units_times_rate_times_days(self) -> None:
        contract = _contract()
        player = _player(id=uuid.uuid4(), credits=1000)
        contract.acceptor_player_id = player.id
        locker = _locker(
            contract, player.id, rent_rate=Decimal("1"),
            last_fee_settled_at=datetime.now(UTC) - timedelta(days=2),
        )
        db = _FakeSession(contracts=[contract], players=[player], lockers=[locker])
        db.deposits = [_deposit(locker, quantity=30, deposited_by=player.id)]

        result = storage_service.settle_fee(db, locker.id)

        # 30 units x 1cr/unit/day x 2 days = 60 credits.
        assert result["fee_charged"] == 60
        assert player.credits == 1000 - 60
        assert locker.accrued_fee == Decimal("60")

    def test_fractional_cents_floor_not_half_up_the_remainder_stays_pending(self) -> None:
        """D18 (continuous-accrue-and-round-once) supersedes this WO's
        original per-period ROUND_HALF_UP design: 100 units x
        0.015cr/unit/day x 1 day = 1.50 credits exactly (already whole
        cents via _round_credits). Only the WHOLE credit already crossed
        (floor(1.50) - floor(0) = 1) is charged THIS call -- it is NOT
        bumped up to 2. The 0.50 remainder isn't lost: it stays pending
        in the ledger (accrued_fee == 1.50) for a future settlement to
        eventually cross."""
        contract = _contract()
        player = _player(id=uuid.uuid4(), credits=1000)
        contract.acceptor_player_id = player.id
        locker = _locker(
            contract, player.id, rent_rate=Decimal("0.015"),
            last_fee_settled_at=datetime.now(UTC) - timedelta(days=1),
        )
        db = _FakeSession(contracts=[contract], players=[player], lockers=[locker])
        db.deposits = [_deposit(locker, quantity=100, deposited_by=player.id)]

        result = storage_service.settle_fee(db, locker.id)

        assert result["fee_charged"] == 1
        assert player.credits == 999
        assert locker.accrued_fee == Decimal("1.50")

    def test_resettle_at_the_same_instant_is_a_noop(self) -> None:
        contract = _contract()
        player = _player(id=uuid.uuid4(), credits=1000)
        contract.acceptor_player_id = player.id
        locker = _locker(
            contract, player.id, last_fee_settled_at=datetime.now(UTC) - timedelta(days=1),
        )
        db = _FakeSession(contracts=[contract], players=[player], lockers=[locker])
        db.deposits = [_deposit(locker, quantity=10, deposited_by=player.id)]

        fixed_now = locker.last_fee_settled_at + timedelta(days=1)
        r1 = storage_service.settle_fee(db, locker.id, now=fixed_now)
        assert r1["fee_charged"] == 10  # 10 units x 1cr x 1 day
        credits_after_first_settle = player.credits

        # Re-settle at the EXACT same instant -- zero elapsed time.
        r2 = storage_service.settle_fee(db, locker.id, now=fixed_now)

        assert r2["fee_charged"] == 0
        assert r2["days_settled"] == 0
        assert player.credits == credits_after_first_settle  # untouched -- no double-charge

    def test_credits_floor_at_zero_forgives_the_shortfall(self) -> None:
        """Matches contract_service.abandon()'s own floor-and-forgive
        convention -- the player pays what they can down to 0, the
        shortfall is simply forgiven, never tracked as debt. D18
        supersedes this WO's original design on ONE point: accrued_fee
        is now the continuous THEORETICAL ledger (not "only what was
        actually collected") -- it advances to the FULL 50 regardless of
        the floor-and-forgive below, so the forgiven 45 is genuinely
        forgiven and is never re-billed on a later settle."""
        contract = _contract()
        player = _player(id=uuid.uuid4(), credits=5)
        contract.acceptor_player_id = player.id
        locker = _locker(
            contract, player.id, rent_rate=Decimal("1"),
            last_fee_settled_at=datetime.now(UTC) - timedelta(days=5),
        )
        db = _FakeSession(contracts=[contract], players=[player], lockers=[locker])
        db.deposits = [_deposit(locker, quantity=10, deposited_by=player.id)]

        result = storage_service.settle_fee(db, locker.id)

        # Theoretical fee: 10 units x 1cr x 5 days = 50 -- far more than
        # the player's 5 credits.
        assert result["fee_charged"] == 5
        assert player.credits == 0
        assert locker.accrued_fee == Decimal("50")

    def test_forgiven_shortfall_is_never_re_billed_on_a_later_settle(self) -> None:
        """The other half of the floor-and-forgive-is-genuine invariant:
        once a whole-credit boundary is crossed and partially forgiven,
        a LATER settle (even with the player now flush with credits)
        must not re-attempt collecting the forgiven remainder -- only
        NEWLY-crossed boundaries from the new period are ever charged."""
        contract = _contract()
        player = _player(id=uuid.uuid4(), credits=5)
        contract.acceptor_player_id = player.id
        anchor = datetime.now(UTC) - timedelta(days=5)
        locker = _locker(contract, player.id, rent_rate=Decimal("1"), last_fee_settled_at=anchor)
        db = _FakeSession(contracts=[contract], players=[player], lockers=[locker])
        db.deposits = [_deposit(locker, quantity=10, deposited_by=player.id)]

        # First settle: theoretical 50, only 5 collectible -- forgiven.
        r1 = storage_service.settle_fee(db, locker.id, now=anchor + timedelta(days=5))
        assert r1["fee_charged"] == 5
        assert player.credits == 0

        # Player comes into money, and a full day passes with the SAME
        # 10 units still stored -- a second, genuinely NEW 10cr owed.
        player.credits = 1000
        r2 = storage_service.settle_fee(db, locker.id, now=anchor + timedelta(days=6))

        # Only the NEW day's 10cr is charged -- the earlier forgiven 45
        # never resurfaces as a hidden debt.
        assert r2["fee_charged"] == 10
        assert player.credits == 990
        assert locker.accrued_fee == Decimal("60")  # 50 (forgiven) + 10 (new)

    def test_no_stored_units_advances_anchor_without_charging(self) -> None:
        contract = _contract()
        player = _player(id=uuid.uuid4(), credits=1000)
        contract.acceptor_player_id = player.id
        original_anchor = datetime.now(UTC) - timedelta(days=3)
        locker = _locker(contract, player.id, last_fee_settled_at=original_anchor)
        db = _FakeSession(contracts=[contract], players=[player], lockers=[locker])
        # No deposits at all -- empty locker.

        result = storage_service.settle_fee(db, locker.id)

        assert result["fee_charged"] == 0
        assert player.credits == 1000  # untouched
        assert locker.last_fee_settled_at > original_anchor  # anchor still advanced
        # The empty-locker branch never needs to lock Player at all.
        assert "Player" not in db.for_update_calls

    def test_settle_locks_locker_then_player_when_rent_is_owed(self) -> None:
        contract = _contract()
        player = _player(id=uuid.uuid4(), credits=1000)
        contract.acceptor_player_id = player.id
        locker = _locker(
            contract, player.id, last_fee_settled_at=datetime.now(UTC) - timedelta(days=1),
        )
        db = _FakeSession(contracts=[contract], players=[player], lockers=[locker])
        db.deposits = [_deposit(locker, quantity=10, deposited_by=player.id)]

        storage_service.settle_fee(db, locker.id)

        assert db.for_update_calls == ["StorageLocker", "Player"]

    def test_settle_fee_locker_not_found_404s(self) -> None:
        db = _FakeSession()
        with pytest.raises(StorageNotFoundError):
            storage_service.settle_fee(db, uuid.uuid4())

    def test_d18_salami_slicing_closed_tiny_periods_eventually_charge(self) -> None:
        """D18's whole reason to exist: the 1-unit-top-off script -- many
        settle calls, each individually well under a whole credit
        (0.30cr/day here), used to round to 0 FOREVER under the old
        per-period rounding. Under continuous-accrue-and-round-once, the
        ledger keeps every fractional contribution, so the 4th call
        (cumulative 1.20) finally crosses the first whole-credit
        boundary and charges it."""
        contract = _contract()
        player = _player(id=uuid.uuid4(), credits=1000)
        contract.acceptor_player_id = player.id
        anchor = datetime.now(UTC)
        locker = _locker(
            contract, player.id, rent_rate=Decimal("0.30"), last_fee_settled_at=anchor,
        )
        db = _FakeSession(contracts=[contract], players=[player], lockers=[locker])
        db.deposits = [_deposit(locker, quantity=1, deposited_by=player.id)]

        charges = []
        for day in range(1, 5):
            result = storage_service.settle_fee(db, locker.id, now=anchor + timedelta(days=day))
            charges.append(result["fee_charged"])

        assert charges == [0, 0, 0, 1]  # never evades forever -- the 4th call crosses 1.20
        assert player.credits == 999
        assert locker.accrued_fee == Decimal("1.20")

    def test_d18_no_per_trip_minimum_tax_on_a_legit_multi_trip_settlement(self) -> None:
        """The other half of D18's guard: a legitimate multi-trip
        fulfillment must NOT be charged a >=1cr minimum on every single
        settlement. Three periods at 0.50cr each (1.50cr theoretical
        total) charge exactly 1 credit total across all three calls --
        never 3 (one minimum-charge per trip)."""
        contract = _contract()
        player = _player(id=uuid.uuid4(), credits=1000)
        contract.acceptor_player_id = player.id
        anchor = datetime.now(UTC)
        locker = _locker(
            contract, player.id, rent_rate=Decimal("0.50"), last_fee_settled_at=anchor,
        )
        db = _FakeSession(contracts=[contract], players=[player], lockers=[locker])
        db.deposits = [_deposit(locker, quantity=1, deposited_by=player.id)]

        charges = []
        for day in range(1, 4):
            result = storage_service.settle_fee(db, locker.id, now=anchor + timedelta(days=day))
            charges.append(result["fee_charged"])

        assert charges == [0, 1, 0]  # NOT [1, 1, 1] -- no per-call minimum tax
        assert sum(charges) == 1
        assert player.credits == 999
        assert locker.accrued_fee == Decimal("1.50")

    def test_stored_units_override_bills_the_pre_deposit_count(self) -> None:
        """The parameter D17's deferred-settle call in deposit_cargo
        relies on: when supplied, stored_units_override wins over the
        live _stored_units() query entirely -- proven here by a locker
        whose ACTUAL live deposits (30) differ from the override (5),
        confirming the override -- not the live count -- drives the
        charge."""
        contract = _contract()
        player = _player(id=uuid.uuid4(), credits=1000)
        contract.acceptor_player_id = player.id
        locker = _locker(
            contract, player.id, rent_rate=Decimal("1"),
            last_fee_settled_at=datetime.now(UTC) - timedelta(days=1),
        )
        db = _FakeSession(contracts=[contract], players=[player], lockers=[locker])
        db.deposits = [_deposit(locker, quantity=30, deposited_by=player.id)]  # live count: 30

        result = storage_service.settle_fee(db, locker.id, stored_units_override=5)

        # 5 (override) x 1cr x 1 day = 5 -- NOT 30 x 1cr x 1 day = 30.
        assert result["fee_charged"] == 5
        assert player.credits == 995


# --- WO-STORE-FEE-ACCRUAL: D17 payout-then-settle reorder ------------------ #

@pytest.mark.unit
class TestD17PayoutThenSettle:
    def test_completion_payout_lands_before_the_final_rent_settle(self) -> None:
        """The direct proof of the reorder: the player starts BROKE (0
        credits), so a pre-payout settle would floor the whole fee to 0
        and forgive it entirely. D17 settles AFTER contract_service.
        complete()'s payout credits the player -- so the fee is
        genuinely, fully collectible out of the money the player just
        earned, not floored-and-forgiven at their poorest moment."""
        contract = _contract(quantity=15, payment=Decimal("1000.00"))
        player = _player(id=uuid.uuid4(), credits=0)
        contract.acceptor_player_id = player.id
        player.current_port_id = contract.destination_station_id
        anchor = datetime.now(UTC) - timedelta(days=10)
        locker = _locker(contract, player.id, rent_rate=Decimal("1"), last_fee_settled_at=anchor)
        db = _FakeSession(contracts=[contract], players=[player], lockers=[locker])
        # 5 units already sitting in the locker for the full 10-day
        # period -- a real 50cr rent bill by the time the final
        # installment lands.
        db.deposits = [_deposit(locker, quantity=5, deposited_by=player.id)]

        result = storage_service.deposit_cargo(db, locker.id, player.id, 10)

        assert result["completed"] is True
        assert contract.status == ContractStatus.COMPLETED
        # If settled BEFORE payout (the pre-D17 bug), fee_charged would
        # floor to 0 (player had 0 credits) and player.credits would
        # land at exactly 1000. D17 collects the full 50cr bill against
        # the flush post-payout balance instead.
        assert result["fee_charged"] == 50
        assert player.credits == 1000 - 50

    def test_final_installments_units_are_not_billed_for_storage_time_never_incurred(self) -> None:
        """stored_units_override=old_stored_units (5, the pre-final-
        deposit count) drives the settlement -- NOT the post-deposit
        accumulated total (15). The final 10 units are bridged straight
        back out by complete() the instant they arrive; billing them for
        the 10-day period would charge rent on storage time that never
        actually happened."""
        contract = _contract(quantity=15, payment=Decimal("500.00"))
        player = _player(id=uuid.uuid4(), credits=100)
        contract.acceptor_player_id = player.id
        player.current_port_id = contract.destination_station_id
        anchor = datetime.now(UTC) - timedelta(days=10)
        locker = _locker(contract, player.id, rent_rate=Decimal("1"), last_fee_settled_at=anchor)
        db = _FakeSession(contracts=[contract], players=[player], lockers=[locker])
        db.deposits = [_deposit(locker, quantity=5, deposited_by=player.id)]

        result = storage_service.deposit_cargo(db, locker.id, player.id, 10)

        # 5 units (pre-existing) x 1cr x 10 days = 50 -- NOT 15 x 1 x 10
        # = 150, which is what billing the post-deposit total would give.
        assert result["fee_charged"] == 50
        assert player.credits == 100 + 500 - 50


# --- WO-STORE-EXPIRY-CLAIMABLE: sweep_expired_lockers ----------------------- #

@pytest.mark.unit
class TestSweepExpiredLockers:
    def test_active_locker_with_expired_contract_converts_to_claimable(self) -> None:
        now = datetime.now(UTC)
        contract = _contract(status=ContractStatus.EXPIRED, deadline=now - timedelta(hours=1))
        player = _player(id=uuid.uuid4(), credits=10000)
        contract.acceptor_player_id = player.id
        locker = _locker(
            contract, player.id, status=StorageLockerStatus.ACTIVE, rent_rate=Decimal("1"),
            last_fee_settled_at=now - timedelta(days=2),
        )
        db = _FakeSession(contracts=[contract], players=[player], lockers=[locker])
        db.deposits = [_deposit(locker, quantity=20, deposited_by=player.id)]

        result = storage_service.sweep_expired_lockers(db, now=now)

        assert result["converted"] == 1
        assert locker.status == StorageLockerStatus.CLAIMABLE
        assert locker.contract_id is None
        assert locker.last_fee_settled_at == now
        # 20 units x 1cr/unit/day x 2 days = 40 -- rent settled as part
        # of the conversion, not skipped.
        assert player.credits == 10000 - 40

    def test_active_locker_with_still_accepted_contract_untouched(self) -> None:
        contract = _contract(status=ContractStatus.ACCEPTED)
        player = _player(id=uuid.uuid4())
        contract.acceptor_player_id = player.id
        locker = _locker(contract, player.id, status=StorageLockerStatus.ACTIVE)
        db = _FakeSession(contracts=[contract], players=[player], lockers=[locker])

        result = storage_service.sweep_expired_lockers(db)

        assert result["converted"] == 0
        assert locker.status == StorageLockerStatus.ACTIVE
        assert locker.contract_id == contract.id

    def test_already_claimable_locker_untouched_not_reprocessed(self) -> None:
        contract = _contract(status=ContractStatus.EXPIRED)
        player = _player(id=uuid.uuid4())
        locker = _locker(
            contract, player.id, status=StorageLockerStatus.CLAIMABLE, contract_id=None,
            accrued_fee=Decimal("7.50"),
        )
        db = _FakeSession(contracts=[contract], players=[player], lockers=[locker])

        result = storage_service.sweep_expired_lockers(db)

        assert result["converted"] == 0
        assert locker.accrued_fee == Decimal("7.50")  # untouched -- no re-settle

    def test_released_locker_untouched(self) -> None:
        contract = _contract(status=ContractStatus.EXPIRED)
        player = _player(id=uuid.uuid4())
        locker = _locker(contract, player.id, status=StorageLockerStatus.RELEASED, contract_id=None)
        db = _FakeSession(contracts=[contract], players=[player], lockers=[locker])

        result = storage_service.sweep_expired_lockers(db)

        assert result["converted"] == 0

    def test_combined_with_accepted_sweep_contract_fails_and_locker_converts(self) -> None:
        """The real scheduler order: contract_service.sweep_expired_
        accepted_contracts runs FIRST (flips ACCEPTED -> EXPIRED, charges
        the acceptor penalty), THEN storage_service.sweep_expired_lockers
        (sees that SAME pass's just-flushed EXPIRED status, converts +
        settles rent) -- exactly how contract_sweeps.py's `_run_contract_
        expire_sweep_sync` calls them, in one transaction."""
        now = datetime.now(UTC)
        contract = _contract(
            status=ContractStatus.ACCEPTED, deadline=now - timedelta(hours=1), penalty=Decimal("300.00"),
        )
        player = _player(id=uuid.uuid4(), credits=1000)
        contract.acceptor_player_id = player.id
        locker = _locker(
            contract, player.id, status=StorageLockerStatus.ACTIVE, rent_rate=Decimal("1"),
            last_fee_settled_at=now - timedelta(days=1),
        )
        db = _FakeSession(contracts=[contract], players=[player], lockers=[locker])
        db.deposits = [_deposit(locker, quantity=10, deposited_by=player.id)]

        accepted_result = contract_service.sweep_expired_accepted_contracts(db, now=now)
        locker_result = storage_service.sweep_expired_lockers(db, now=now)

        assert accepted_result["expired"] == 1
        assert contract.status == ContractStatus.EXPIRED
        assert locker_result["converted"] == 1
        assert locker.status == StorageLockerStatus.CLAIMABLE
        assert locker.contract_id is None
        # Penalty (300) applied first by the accepted-sweep, THEN rent
        # (10 units x 1cr x 1 day = 10) settled by the locker-sweep:
        # 1000 - 300 - 10 = 690. Proves both effects land, in order.
        assert player.credits == 690

    def test_new_contract_after_expiry_mints_a_fresh_locker_invariant(self) -> None:
        """THE explicit acceptance criterion (cipher's pre-emptive catch):
        new contract = new locker row. Proven via the REAL get_or_create_
        locker function, not a mock -- the fresh locker's own id differs
        from the claimable one, and the old claimable deposits (tied to
        the OLD locker's id) do not count toward the NEW locker's
        accumulated total."""
        now = datetime.now(UTC)
        old_contract = _contract(status=ContractStatus.EXPIRED, deadline=now - timedelta(hours=1))
        player = _player(id=uuid.uuid4())
        old_contract.acceptor_player_id = player.id
        old_locker = _locker(old_contract, player.id, status=StorageLockerStatus.ACTIVE)
        db = _FakeSession(contracts=[old_contract], players=[player], lockers=[old_locker])
        db.deposits = [_deposit(old_locker, quantity=30, deposited_by=player.id)]

        storage_service.sweep_expired_lockers(db, now=now)
        assert old_locker.status == StorageLockerStatus.CLAIMABLE
        assert old_locker.contract_id is None

        # A NEW contract, same player, same destination station.
        new_contract = _contract(
            status=ContractStatus.ACCEPTED, destination_station_id=old_contract.destination_station_id,
            quantity=20,
        )
        new_contract.acceptor_player_id = player.id
        db.contracts.append(new_contract)

        new_locker = storage_service.get_or_create_locker(db, player.id, new_contract.id)

        assert new_locker.id != old_locker.id  # genuinely fresh, not re-linked
        assert new_locker.contract_id == new_contract.id
        assert new_locker.status == StorageLockerStatus.ACTIVE
        # The old claimable deposits (30 units) are tied to old_locker.id
        # -- they must NOT count toward the new locker's total, or the
        # new contract could false-complete without a single new deposit.
        assert storage_service._stored_units(db, new_locker.id) == 0
        assert storage_service._stored_units(db, old_locker.id) == 30  # untouched, still claimable


# --- WO-STORE-EXPIRY-CLAIMABLE: retrieve_claimable_cargo -------------------- #

@pytest.mark.unit
class TestRetrieveClaimableCargo:
    def test_full_retrieve_releases_locker_and_loads_ship(self) -> None:
        contract = _contract()
        player = _player(
            id=uuid.uuid4(), current_ship=_ship(cargo={"capacity": 500, "used": 0, "contents": {}}),
        )
        player.current_port_id = contract.destination_station_id
        locker = _locker(contract, player.id, status=StorageLockerStatus.CLAIMABLE, contract_id=None)
        db = _FakeSession(contracts=[contract], players=[player], lockers=[locker])
        db.deposits = [_deposit(locker, quantity=50, deposited_by=player.id)]

        result = storage_service.retrieve_claimable_cargo(db, locker.id, player.id)

        assert result["retrieved"] == 50
        assert result["commodity"] == "ore"
        assert result["remaining"] == 0
        assert result["released"] is True
        assert locker.status == StorageLockerStatus.RELEASED
        assert player.current_ship.cargo["contents"]["ore"] == 50
        assert player.current_ship.cargo["used"] == 50
        assert db.deposits == []  # fully consumed

    def test_capacity_constrained_retrieve_stays_claimable_with_remainder(self) -> None:
        """The capacity nuance the WO flagged: a locker holding more than
        the ship can carry retrieves as much as fits, leaves the rest
        CLAIMABLE for a later trip -- never rejects outright."""
        contract = _contract()
        player = _player(
            id=uuid.uuid4(), current_ship=_ship(cargo={"capacity": 50, "used": 0, "contents": {}}),
        )
        player.current_port_id = contract.destination_station_id
        locker = _locker(contract, player.id, status=StorageLockerStatus.CLAIMABLE, contract_id=None)
        db = _FakeSession(contracts=[contract], players=[player], lockers=[locker])
        db.deposits = [_deposit(locker, quantity=150, deposited_by=player.id)]

        result = storage_service.retrieve_claimable_cargo(db, locker.id, player.id)

        assert result["retrieved"] == 50
        assert result["remaining"] == 100
        assert result["released"] is False
        assert locker.status == StorageLockerStatus.CLAIMABLE
        assert player.current_ship.cargo["used"] == 50

    def test_explicit_quantity_retrieves_exactly_that_much(self) -> None:
        contract = _contract()
        player = _player(
            id=uuid.uuid4(), current_ship=_ship(cargo={"capacity": 500, "used": 0, "contents": {}}),
        )
        player.current_port_id = contract.destination_station_id
        locker = _locker(contract, player.id, status=StorageLockerStatus.CLAIMABLE, contract_id=None)
        db = _FakeSession(contracts=[contract], players=[player], lockers=[locker])
        db.deposits = [_deposit(locker, quantity=100, deposited_by=player.id)]

        result = storage_service.retrieve_claimable_cargo(db, locker.id, player.id, quantity=30)

        assert result["retrieved"] == 30
        assert result["remaining"] == 70
        assert result["released"] is False
        assert locker.status == StorageLockerStatus.CLAIMABLE

    def test_explicit_quantity_exceeding_available_rejected(self) -> None:
        contract = _contract()
        player = _player(id=uuid.uuid4())
        player.current_port_id = contract.destination_station_id
        locker = _locker(contract, player.id, status=StorageLockerStatus.CLAIMABLE, contract_id=None)
        db = _FakeSession(contracts=[contract], players=[player], lockers=[locker])
        db.deposits = [_deposit(locker, quantity=50, deposited_by=player.id)]

        with pytest.raises(StorageError, match="insufficient_stored"):
            storage_service.retrieve_claimable_cargo(db, locker.id, player.id, quantity=100)

    def test_explicit_quantity_exceeding_capacity_rejected(self) -> None:
        contract = _contract()
        player = _player(
            id=uuid.uuid4(), current_ship=_ship(cargo={"capacity": 50, "used": 0, "contents": {}}),
        )
        player.current_port_id = contract.destination_station_id
        locker = _locker(contract, player.id, status=StorageLockerStatus.CLAIMABLE, contract_id=None)
        db = _FakeSession(contracts=[contract], players=[player], lockers=[locker])
        db.deposits = [_deposit(locker, quantity=100, deposited_by=player.id)]

        with pytest.raises(StorageError, match="insufficient_cargo_capacity"):
            storage_service.retrieve_claimable_cargo(db, locker.id, player.id, quantity=60)

    def test_wrong_station_rejected(self) -> None:
        contract = _contract()
        player = _player(id=uuid.uuid4())
        player.current_port_id = uuid.uuid4()  # NOT the locker's station
        locker = _locker(contract, player.id, status=StorageLockerStatus.CLAIMABLE, contract_id=None)
        db = _FakeSession(contracts=[contract], players=[player], lockers=[locker])

        with pytest.raises(StorageError, match="wrong_station"):
            storage_service.retrieve_claimable_cargo(db, locker.id, player.id)

    def test_not_owner_rejected(self) -> None:
        contract = _contract()
        owner = _player(id=uuid.uuid4())
        stranger = _player(id=uuid.uuid4())
        locker = _locker(contract, owner.id, status=StorageLockerStatus.CLAIMABLE, contract_id=None)
        db = _FakeSession(contracts=[contract], players=[owner, stranger], lockers=[locker])

        with pytest.raises(StorageError, match="does not belong to you"):
            storage_service.retrieve_claimable_cargo(db, locker.id, stranger.id)

    def test_locker_not_claimable_rejected(self) -> None:
        contract = _contract()
        player = _player(id=uuid.uuid4())
        contract.acceptor_player_id = player.id
        locker = _locker(contract, player.id, status=StorageLockerStatus.ACTIVE)
        db = _FakeSession(contracts=[contract], players=[player], lockers=[locker])

        with pytest.raises(StorageError, match="locker_not_claimable"):
            storage_service.retrieve_claimable_cargo(db, locker.id, player.id)

    def test_retrieve_from_already_emptied_locker_releases_and_returns_zero(self) -> None:
        contract = _contract()
        player = _player(id=uuid.uuid4())
        player.current_port_id = contract.destination_station_id
        locker = _locker(contract, player.id, status=StorageLockerStatus.CLAIMABLE, contract_id=None)
        db = _FakeSession(contracts=[contract], players=[player], lockers=[locker])
        # No deposits at all -- already fully retrieved on an earlier call.

        result = storage_service.retrieve_claimable_cargo(db, locker.id, player.id)

        assert result["retrieved"] == 0
        assert result["commodity"] is None
        assert result["released"] is True
        assert locker.status == StorageLockerStatus.RELEASED

    def test_multi_trip_retrieve_second_call_gets_the_rest(self) -> None:
        contract = _contract()
        player = _player(
            id=uuid.uuid4(), current_ship=_ship(cargo={"capacity": 50, "used": 0, "contents": {}}),
        )
        player.current_port_id = contract.destination_station_id
        locker = _locker(contract, player.id, status=StorageLockerStatus.CLAIMABLE, contract_id=None)
        db = _FakeSession(contracts=[contract], players=[player], lockers=[locker])
        db.deposits = [_deposit(locker, quantity=80, deposited_by=player.id)]

        r1 = storage_service.retrieve_claimable_cargo(db, locker.id, player.id)
        assert r1["retrieved"] == 50
        assert r1["released"] is False
        assert locker.status == StorageLockerStatus.CLAIMABLE

        # "Return to base, unload" -- same ship, emptied for the next trip.
        player.current_ship.cargo = {"capacity": 50, "used": 0, "contents": {}}
        r2 = storage_service.retrieve_claimable_cargo(db, locker.id, player.id)

        assert r2["retrieved"] == 30
        assert r2["remaining"] == 0
        assert r2["released"] is True
        assert locker.status == StorageLockerStatus.RELEASED

    def test_rent_settled_on_retrieve(self) -> None:
        contract = _contract()
        player = _player(id=uuid.uuid4(), credits=1000)
        player.current_port_id = contract.destination_station_id
        anchor = datetime.now(UTC) - timedelta(days=2)
        locker = _locker(
            contract, player.id, status=StorageLockerStatus.CLAIMABLE, contract_id=None,
            rent_rate=Decimal("1"), last_fee_settled_at=anchor,
        )
        db = _FakeSession(contracts=[contract], players=[player], lockers=[locker])
        db.deposits = [_deposit(locker, quantity=20, deposited_by=player.id)]

        result = storage_service.retrieve_claimable_cargo(db, locker.id, player.id)

        # 20 units x 1cr x 2 days = 40 -- charged as part of retrieval,
        # not skipped just because the locker's already claimable.
        assert result["fee_charged"] == 40
        assert player.credits == 960

    def test_lock_order_locker_then_player_then_ship(self) -> None:
        contract = _contract()
        player = _player(id=uuid.uuid4(), credits=1000)
        player.current_port_id = contract.destination_station_id
        locker = _locker(
            contract, player.id, status=StorageLockerStatus.CLAIMABLE, contract_id=None,
            rent_rate=Decimal("1"), last_fee_settled_at=datetime.now(UTC) - timedelta(days=1),
        )
        db = _FakeSession(contracts=[contract], players=[player], lockers=[locker])
        db.deposits = [_deposit(locker, quantity=10, deposited_by=player.id)]

        storage_service.retrieve_claimable_cargo(db, locker.id, player.id)

        # Locker -> Player (this function's own order) -> settle_fee's
        # re-lock of Locker -> Player (rent genuinely owed) -> Ship.
        assert db.for_update_calls == ["StorageLocker", "Player", "StorageLocker", "Player", "Ship"]

    def test_consume_deposits_fifo_partial_boundary_row(self) -> None:
        """_consume_deposits' own oldest-first consumption: three 10-unit
        rows at staggered timestamps, retrieving 15 -- the oldest row is
        fully consumed (deleted), the second is left at 5 (partially
        consumed), the third is untouched."""
        contract = _contract()
        player = _player(
            id=uuid.uuid4(), current_ship=_ship(cargo={"capacity": 500, "used": 0, "contents": {}}),
        )
        player.current_port_id = contract.destination_station_id
        locker = _locker(contract, player.id, status=StorageLockerStatus.CLAIMABLE, contract_id=None)
        t0 = datetime.now(UTC) - timedelta(hours=3)
        row_oldest = _deposit(locker, quantity=10, deposited_at=t0)
        row_middle = _deposit(locker, quantity=10, deposited_at=t0 + timedelta(hours=1))
        row_newest = _deposit(locker, quantity=10, deposited_at=t0 + timedelta(hours=2))
        db = _FakeSession(contracts=[contract], players=[player], lockers=[locker])
        db.deposits = [row_newest, row_oldest, row_middle]  # deliberately out of order

        result = storage_service.retrieve_claimable_cargo(db, locker.id, player.id, quantity=15)

        assert result["retrieved"] == 15
        assert len(db.deposits) == 2
        assert row_oldest not in db.deposits  # fully consumed, deleted
        assert row_middle.quantity == 5  # partially consumed, boundary row
        assert row_newest.quantity == 10  # untouched


# --- WO-STORE-EXPIRY-CLAIMABLE + D19: gate_contract_expiry_on_locker ------- #

@pytest.mark.unit
class TestGateContractExpiryOnLocker:
    def test_no_active_locker_at_all_proceeds_true_with_zero_locking(self) -> None:
        contract = _contract()
        db = _FakeSession(contracts=[contract])

        result = storage_service.gate_contract_expiry_on_locker(db, contract)

        assert result is True
        # The common non-storage case: zero locking attempted at all.
        assert db.for_update_calls == []

    def test_active_locker_but_belongs_to_a_different_contract_proceeds_true(self) -> None:
        contract = _contract()
        other_contract = _contract()
        player = _player(id=uuid.uuid4())
        unrelated_locker = _locker(other_contract, player.id, status=StorageLockerStatus.ACTIVE)
        db = _FakeSession(contracts=[contract, other_contract], lockers=[unrelated_locker])

        result = storage_service.gate_contract_expiry_on_locker(db, contract)

        assert result is True
        assert db.for_update_calls == []

    def test_claimable_or_released_locker_does_not_gate_proceeds_true(self) -> None:
        """A locker that's already CLAIMABLE/RELEASED for this contract
        (a stale/already-processed row) isn't a live in-flight deposit
        -- the existence check itself filters to status == ACTIVE, so
        this correctly falls through to the no-locker path."""
        contract = _contract()
        player = _player(id=uuid.uuid4())
        contract.acceptor_player_id = player.id
        stale_locker = _locker(contract, player.id, status=StorageLockerStatus.RELEASED)
        db = _FakeSession(contracts=[contract], players=[player], lockers=[stale_locker])

        result = storage_service.gate_contract_expiry_on_locker(db, contract)

        assert result is True

    def test_active_locker_uncontended_acquires_and_proceeds_true(self) -> None:
        contract = _contract()
        player = _player(id=uuid.uuid4())
        contract.acceptor_player_id = player.id
        locker = _locker(contract, player.id, status=StorageLockerStatus.ACTIVE)
        db = _FakeSession(contracts=[contract], players=[player], lockers=[locker])

        result = storage_service.gate_contract_expiry_on_locker(db, contract)

        assert result is True
        # Existence check (unlocked) + the skip_locked acquisition probe.
        assert db.for_update_calls == ["StorageLocker"]

    def test_active_locker_contended_defers_false(self) -> None:
        """The disambiguating case (mack's own flagged nuance): step 1
        already proved the locker EXISTS, so step 2's None result here
        is unambiguously CONTENDED, not absent."""
        contract = _contract()
        player = _player(id=uuid.uuid4())
        contract.acceptor_player_id = player.id
        locker = _locker(contract, player.id, status=StorageLockerStatus.ACTIVE)
        db = _FakeSession(
            contracts=[contract], players=[player], lockers=[locker],
            contended_locker_ids={locker.id},
        )

        result = storage_service.gate_contract_expiry_on_locker(db, contract)

        assert result is False

    def test_gate_never_settles_or_converts_the_locker_itself(self) -> None:
        """The gate ONLY acquires the lock as a probe -- it must never
        settle rent or flip status; that's sweep_expired_lockers' own
        job, run separately right after."""
        contract = _contract()
        player = _player(id=uuid.uuid4())
        contract.acceptor_player_id = player.id
        locker = _locker(
            contract, player.id, status=StorageLockerStatus.ACTIVE, accrued_fee=Decimal("0"),
        )
        db = _FakeSession(contracts=[contract], players=[player], lockers=[locker])

        storage_service.gate_contract_expiry_on_locker(db, contract)

        assert locker.status == StorageLockerStatus.ACTIVE  # unchanged
        assert locker.accrued_fee == Decimal("0")  # unsettled
        assert locker.contract_id == contract.id  # not nulled


@pytest.mark.unit
class TestGetBulkLockerState:
    """WO-CONTRACT-4-BULK: `get_bulk_locker_state` -- the public read
    contract_service.py's bulk_procurement walk-away-penalty sites call
    to learn a contract's actual Locker fill. Reuses `gate_contract_
    expiry_on_locker`'s exact ACTIVE-locker lookup shape (same filter,
    same UNLOCKED plain SELECT -- see this function's own docstring for
    why no `with_for_update()` is needed here)."""

    def test_no_active_locker_returns_none(self) -> None:
        contract = _contract()
        db = _FakeSession(contracts=[contract])

        assert storage_service.get_bulk_locker_state(db, contract) is None

    def test_active_locker_with_deposits_returns_id_and_stored_units(self) -> None:
        contract = _contract()
        player = _player(id=uuid.uuid4())
        contract.acceptor_player_id = player.id
        locker = _locker(contract, player.id, status=StorageLockerStatus.ACTIVE)
        db = _FakeSession(
            contracts=[contract], players=[player], lockers=[locker],
            deposits=[_deposit(locker, quantity=7, deposited_by=player.id)],
        )

        result = storage_service.get_bulk_locker_state(db, contract)

        assert result == (locker.id, 7)

    def test_active_locker_with_zero_deposits_returns_id_and_zero(self) -> None:
        """A Locker was rented (get_or_create_locker) but no deposit
        landed yet -- still ACTIVE, still returns a real state, just
        stored_units == 0 (the caller's own formula degenerates to the
        full static penalty for this, same as the no-locker-at-all case
        numerically, but this function itself still reports the Locker
        honestly rather than treating "empty" as "absent")."""
        contract = _contract()
        player = _player(id=uuid.uuid4())
        contract.acceptor_player_id = player.id
        locker = _locker(contract, player.id, status=StorageLockerStatus.ACTIVE)
        db = _FakeSession(contracts=[contract], players=[player], lockers=[locker])

        result = storage_service.get_bulk_locker_state(db, contract)

        assert result == (locker.id, 0)

    def test_claimable_locker_is_not_active_returns_none(self) -> None:
        """Already converted/claimed -- not a LIVE locker for this
        contract anymore, matches gate_contract_expiry_on_locker's own
        ACTIVE-only filter exactly."""
        contract = _contract()
        player = _player(id=uuid.uuid4())
        contract.acceptor_player_id = player.id
        stale_locker = _locker(contract, player.id, status=StorageLockerStatus.CLAIMABLE)
        db = _FakeSession(contracts=[contract], players=[player], lockers=[stale_locker])

        assert storage_service.get_bulk_locker_state(db, contract) is None

    def test_never_acquires_a_lock(self) -> None:
        """Deliberately a plain read -- the caller's own transaction
        already holds this Locker's lock (from the expiry_gate probe
        earlier in the same tick) or doesn't need to (abandon()'s own
        bounded-imprecision reasoning, see that function's own
        docstring) -- this function must never independently acquire
        one."""
        contract = _contract()
        player = _player(id=uuid.uuid4())
        contract.acceptor_player_id = player.id
        locker = _locker(contract, player.id, status=StorageLockerStatus.ACTIVE)
        db = _FakeSession(contracts=[contract], players=[player], lockers=[locker])

        storage_service.get_bulk_locker_state(db, contract)

        assert db.for_update_calls == []


# --- WO-STORE-EXPIRY-CLAIMABLE + D19: deposit-wins-at-the-deadline -------- #

@pytest.mark.unit
class TestDepositWinsAtTheDeadlineIntegration:
    def test_gated_sweep_defers_a_contended_contract_leaving_it_accepted(self) -> None:
        """Structural proof of the deposit-wins mechanism, driving the
        REAL contract_service.sweep_expired_accepted_contracts with the
        REAL gate_contract_expiry_on_locker wired in exactly as the
        scheduler wires them -- not mocks standing in for either. A
        contract whose Locker is (simulated) contended stays ACCEPTED,
        untouched, no penalty charged; a sibling contract with no locker
        at all still expires normally in the SAME pass."""
        now = datetime.now(UTC)
        contended_contract = _contract(
            status=ContractStatus.ACCEPTED, deadline=now - timedelta(hours=1), penalty=Decimal("500.00"),
        )
        player = _player(id=uuid.uuid4(), credits=1000)
        contended_contract.acceptor_player_id = player.id
        contended_locker = _locker(contended_contract, player.id, status=StorageLockerStatus.ACTIVE)

        plain_contract = _contract(
            status=ContractStatus.ACCEPTED, deadline=now - timedelta(hours=1), penalty=Decimal("100.00"),
        )
        plain_contract.acceptor_player_id = player.id

        db = _FakeSession(
            contracts=[contended_contract, plain_contract], players=[player],
            lockers=[contended_locker], contended_locker_ids={contended_locker.id},
        )

        result = contract_service.sweep_expired_accepted_contracts(
            db, now=now, expiry_gate=storage_service.gate_contract_expiry_on_locker,
        )

        # Only the plain (non-storage) contract expired -- the contended
        # one was deferred, deposit-wins-style, untouched and unpenalized.
        assert result == {"expired": 1}
        assert contended_contract.status == ContractStatus.ACCEPTED
        assert plain_contract.status == ContractStatus.EXPIRED
        assert player.credits == 900  # only the plain contract's 100cr penalty charged
        assert contended_locker.status == StorageLockerStatus.ACTIVE  # untouched too

    def test_gate_deferral_does_not_infinite_loop_the_sweep(self) -> None:
        """The regression this WO's own .all()-restructure fixes: a
        candidate the gate ALWAYS defers must not spin the sweep
        forever -- it terminates having correctly left that one
        candidate untouched."""
        now = datetime.now(UTC)
        contract = _contract(status=ContractStatus.ACCEPTED, deadline=now - timedelta(hours=1))
        player = _player(id=uuid.uuid4())
        contract.acceptor_player_id = player.id
        db = _FakeSession(contracts=[contract], players=[player])

        always_defer = lambda db, candidate: False  # noqa: E731

        result = contract_service.sweep_expired_accepted_contracts(db, now=now, expiry_gate=always_defer)

        assert result == {"expired": 0}
        assert contract.status == ContractStatus.ACCEPTED  # deferred, not stuck mid-loop

    def test_deferral_holds_for_a_bulk_procurement_candidate_too(self) -> None:
        """WO-CONTRACT-4-BULK (mack-bulk gate item 4): D19 deposit-wins is
        proven above for a generic (cargo_delivery-shaped) candidate --
        `gate_contract_expiry_on_locker` itself never reads `contract_
        type` at all (grepped: its own query filters purely on `StorageLocker.
        contract_id`/`.status`), so the mechanism is structurally type-
        agnostic, but this closes the loop with an EXPLICIT bulk_procurement
        candidate rather than leaving it as an inference. A locker
        (simulated) contended by a live completing deposit_cargo call
        defers the bulk contract's expiry entirely -- untouched, no
        dynamic penalty computed at all this tick."""
        now = datetime.now(UTC)
        contract = _contract(
            contract_type=ContractType.BULK_PROCUREMENT,
            status=ContractStatus.ACCEPTED, deadline=now - timedelta(hours=1),
            quantity=10, payment=Decimal("1000.00"), penalty=Decimal("1000.00"),
        )
        player = _player(id=uuid.uuid4(), credits=1000)
        contract.acceptor_player_id = player.id
        locker = _locker(contract, player.id, status=StorageLockerStatus.ACTIVE)
        db = _FakeSession(
            contracts=[contract], players=[player], lockers=[locker],
            contended_locker_ids={locker.id},
        )

        result = contract_service.sweep_expired_accepted_contracts(
            db, now=now, expiry_gate=storage_service.gate_contract_expiry_on_locker,
        )

        assert result == {"expired": 0}
        assert contract.status == ContractStatus.ACCEPTED  # deferred -- the in-flight deposit wins
        assert player.credits == 1000  # zero penalty charged this tick


# --- WO-CONTRACT-4-BULK: full-lifecycle conservation (mack-bulk gate, item 1) #

@pytest.mark.unit
class TestBulkProcurementFullLifecycleConservation:
    """The integration proof this WO's own design brief flagged as OWED
    (needs Lane B's NPC-gen + Lane A's locker/penalty machinery both
    present, which they now are). Drives the REAL post-time escrow state
    through REAL deposit_cargo / sweep_expired_accepted_contracts /
    sweep_expired_lockers / abandon / sweep_expired_dispute_window --
    `get_bulk_locker_state` is NEVER mocked in this class (unlike
    test_contract_service.py's/test_mack_attack_accepted_sweep.py's own
    dispatch-only unit tests): this file's _FakeSession genuinely models
    StorageLocker/ContractCargoDeposit, so its real aggregate-sum query
    runs for real here -- the strongest proof available DB-free.

    CONSERVATION INVARIANT (player-issued): payment can only ever move
    FROM escrow TO the acceptor (complete) or back TO the issuer (any
    walk-away -- full refund, no kill-fee, contract_service.py's own
    unchanged convention) or be DESTROYED as the acceptor's own SEPARATE
    walk-away-penalty debit (a sink -- nobody's gain, see _compute_bulk_
    walkaway_penalty's own docstring). So (issuer.credits + acceptor.
    credits), once escrow's disposition has fully resolved, always equals
    (initial issuer.credits + initial escrow_amount + initial acceptor.
    credits) MINUS whatever was destroyed as a penalty -- exactly, never
    more (double-pay) and never less (a silent mint/loss elsewhere). For
    an NPC-issued contract there is no issuer wallet to conserve against
    (NPC credits are canonically infinite, contracts.md:155) -- only the
    acceptor's own trajectory is asserted there."""

    def test_player_issued_complete_at_full_quota_zero_penalty(self) -> None:
        """Terminal path 1/3: full delivery, multi-trip (6 then 4) --
        proves the SAME conservation holds whether the completing deposit
        is the first or the Nth installment."""
        issuer = _player(id=uuid.uuid4(), credits=4000)  # already debited 1000cr escrow at "post" time
        acceptor = _player(
            id=uuid.uuid4(), credits=200,
            current_ship=_ship(cargo={"capacity": 500, "used": 10, "contents": {"ore": 10}}),
        )
        contract = _contract(
            contract_type=ContractType.BULK_PROCUREMENT, issuer_type=ContractIssuerType.PLAYER,
            issuer_id=issuer.id, acceptor_player_id=acceptor.id,
            quantity=10, payment=Decimal("1000.00"), penalty=Decimal("1000.00"),
            escrow_amount=Decimal("1000.00"), escrow_state=ContractEscrowState.HELD,
        )
        acceptor.current_port_id = contract.destination_station_id
        locker = _locker(contract, acceptor.id)
        db = _FakeSession(contracts=[contract], players=[issuer, acceptor], lockers=[locker])
        initial_total = issuer.credits + int(contract.escrow_amount) + acceptor.credits  # 5200

        r1 = storage_service.deposit_cargo(db, locker.id, acceptor.id, 6)
        assert r1["completed"] is False
        r2 = storage_service.deposit_cargo(db, locker.id, acceptor.id, 4)
        assert r2["completed"] is True

        assert contract.status == ContractStatus.COMPLETED
        assert acceptor.credits == 200 + 1000  # full payment, zero penalty
        assert issuer.credits == 4000  # untouched post-escrow-debit -- never re-charged
        assert locker.status == StorageLockerStatus.RELEASED
        # escrow_amount is left un-zeroed by complete() (a stale marker,
        # not a live balance once escrow_state == RELEASED) -- excluded
        # from the final total on purpose, matching this class's own
        # docstring ("once escrow's disposition has fully resolved").
        final_total = issuer.credits + acceptor.credits
        assert final_total == initial_total  # zero-sum: no mint, no loss, no destroyed sink

    def test_player_issued_deadline_lapse_dynamic_penalty_locker_claimable_issuer_refunded(self) -> None:
        """Terminal path 2/3: deadline strictly lapses mid-deposit (3/10
        stored). Dynamic penalty = 1000 x 7/10 = 700cr, destroyed (NOT
        paid to the issuer -- punitive-sink convention). Locker ->
        CLAIMABLE via the SAME tick's sweep_expired_lockers (acceptor
        keeps the 3 already-deposited units -- a cargo asset, outside
        this credit-conservation accounting, per Max's own two-mechanics-
        decoupling ruling). Issuer refunded FULL escrow (1000cr) via the
        SEPARATE, later sweep_expired_dispute_window pass, undisputed."""
        now = datetime.now(UTC)
        issuer = _player(id=uuid.uuid4(), credits=4000)
        # WELL above the 700cr dynamic penalty -- `abandon()`/the sweep
        # both clamp a debit to `max(0, credits - penalty)`, which would
        # silently mask an under-penalization bug behind a floor-at-zero
        # coincidence; a comfortably positive post-penalty balance is
        # what actually exercises the exact subtraction.
        acceptor = _player(
            id=uuid.uuid4(), credits=2000,
            current_ship=_ship(cargo={"capacity": 500, "used": 3, "contents": {"ore": 3}}),
        )
        contract = _contract(
            contract_type=ContractType.BULK_PROCUREMENT, issuer_type=ContractIssuerType.PLAYER,
            issuer_id=issuer.id, acceptor_player_id=acceptor.id,
            quantity=10, payment=Decimal("1000.00"), penalty=Decimal("1000.00"),
            escrow_amount=Decimal("1000.00"), escrow_state=ContractEscrowState.HELD,
            deadline=now - timedelta(hours=1),
        )
        acceptor.current_port_id = contract.destination_station_id
        locker = _locker(contract, acceptor.id, last_fee_settled_at=now)
        db = _FakeSession(contracts=[contract], players=[issuer, acceptor], lockers=[locker])
        initial_total = issuer.credits + int(contract.escrow_amount) + acceptor.credits  # 7000

        deposit_result = storage_service.deposit_cargo(db, locker.id, acceptor.id, 3)
        assert deposit_result["completed"] is False

        sweep_result = contract_service.sweep_expired_accepted_contracts(
            db, now=now, expiry_gate=storage_service.gate_contract_expiry_on_locker,
        )
        assert sweep_result == {"expired": 1}
        assert contract.status == ContractStatus.EXPIRED
        assert acceptor.credits == 2000 - 700  # 7/10 remaining x 1000, destroyed sink
        assert contract.escrow_amount == Decimal("1000.00")  # untouched -- uninsured, zero pool draw
        assert contract.escrow_state == ContractEscrowState.HELD  # dispute window hasn't run yet

        locker_sweep_result = storage_service.sweep_expired_lockers(db, now=now)
        assert locker_sweep_result == {"converted": 1}
        assert locker.status == StorageLockerStatus.CLAIMABLE  # NOT stranded
        assert locker.contract_id is None

        dispute_result = contract_service.sweep_expired_dispute_window(db, now=now + timedelta(hours=49))
        assert dispute_result == {"refunded": 1}
        assert contract.escrow_state == ContractEscrowState.REFUNDING
        assert issuer.credits == 4000 + 1000  # full refund, no kill-fee

        final_total = issuer.credits + acceptor.credits
        assert final_total == initial_total - 700  # exactly the destroyed penalty, no more, no less

    def test_player_issued_abandon_mid_deposit_dynamic_penalty_immediate_refund_locker_claimable(self) -> None:
        """Terminal path 3/3: explicit abandon() mid-deposit (3/10
        stored, deadline still in the FUTURE -- proves this is genuinely
        the voluntary-walkaway path, not a disguised deadline-lapse).
        SAME 700cr dynamic penalty formula as the sweep path, but the
        issuer refund is IMMEDIATE (inside abandon() itself), not
        deferred to the dispute window -- and the Locker only needs a
        LATER sweep_expired_lockers tick to flip CLAIMABLE (abandon()
        itself never touches the Locker row at all -- it only flips the
        Contract to EXPIRED, matching sweep_expired_lockers' own
        `status == EXPIRED` gate with no deadline check). No monkeypatch
        of get_bulk_locker_state anywhere here -- the REAL Locker/deposit
        rows drive the real formula, the strongest proof available for
        the stranded-locker bug this WO fixes."""
        now = datetime.now(UTC)
        issuer = _player(id=uuid.uuid4(), credits=4000)
        # WELL above the 700cr dynamic penalty -- see the sibling
        # deadline-lapse test's own comment for why (avoids the max(0,
        # ...) debit-floor masking an under-penalization bug).
        acceptor = _player(
            id=uuid.uuid4(), credits=2000,
            current_ship=_ship(cargo={"capacity": 500, "used": 3, "contents": {"ore": 3}}),
        )
        contract = _contract(
            contract_type=ContractType.BULK_PROCUREMENT, issuer_type=ContractIssuerType.PLAYER,
            issuer_id=issuer.id, acceptor_player_id=acceptor.id,
            quantity=10, payment=Decimal("1000.00"), penalty=Decimal("1000.00"),
            escrow_amount=Decimal("1000.00"), escrow_state=ContractEscrowState.HELD,
            deadline=now + timedelta(hours=48),  # future -- a genuine voluntary walk-away
        )
        acceptor.current_port_id = contract.destination_station_id
        locker = _locker(contract, acceptor.id, last_fee_settled_at=now)
        db = _FakeSession(contracts=[contract], players=[issuer, acceptor], lockers=[locker])
        initial_total = issuer.credits + int(contract.escrow_amount) + acceptor.credits  # 7000

        deposit_result = storage_service.deposit_cargo(db, locker.id, acceptor.id, 3)
        assert deposit_result["completed"] is False

        abandon_result = contract_service.abandon(db, contract.id, acceptor.id, now=now)

        assert abandon_result["penalty_charged"] == 700  # 7/10 remaining x 1000, real locker fill
        assert contract.status == ContractStatus.EXPIRED  # NOT CANCELLED -- lets the sweep pick this up
        assert acceptor.credits == 2000 - 700
        assert issuer.credits == 4000 + 1000  # full refund, IMMEDIATE (not deferred)
        assert contract.escrow_state == ContractEscrowState.REFUNDING
        assert locker.status == StorageLockerStatus.ACTIVE  # abandon() itself never touches the Locker

        locker_sweep_result = storage_service.sweep_expired_lockers(db, now=now)
        assert locker_sweep_result == {"converted": 1}
        assert locker.status == StorageLockerStatus.CLAIMABLE  # NOT stranded -- the WO-4 strand-fix
        assert locker.contract_id is None

        final_total = issuer.credits + acceptor.credits
        assert final_total == initial_total - 700  # exactly the destroyed penalty, no more, no less

    def test_npc_issued_complete_at_full_quota(self) -> None:
        """NPC-issued mint model: no issuer wallet to conserve against
        (escrow_amount is always 0 for an NPC row) -- only the acceptor's
        own payout is asserted."""
        acceptor = _player(
            id=uuid.uuid4(), credits=200,
            current_ship=_ship(cargo={"capacity": 500, "used": 10, "contents": {"ore": 10}}),
        )
        contract = _contract(
            contract_type=ContractType.BULK_PROCUREMENT, issuer_type=ContractIssuerType.NPC,
            acceptor_player_id=acceptor.id,
            quantity=10, payment=Decimal("1000.00"), penalty=Decimal("1000.00"),
            escrow_amount=Decimal("0"), escrow_state=ContractEscrowState.HELD,
        )
        acceptor.current_port_id = contract.destination_station_id
        locker = _locker(contract, acceptor.id)
        db = _FakeSession(contracts=[contract], players=[acceptor], lockers=[locker])

        result = storage_service.deposit_cargo(db, locker.id, acceptor.id, 10)

        assert result["completed"] is True
        assert contract.status == ContractStatus.COMPLETED
        assert acceptor.credits == 200 + 1000  # minted, full payment, zero penalty
        assert locker.status == StorageLockerStatus.RELEASED

    def test_npc_issued_deadline_lapse_dynamic_penalty_destroyed_no_issuer_side_effect(self) -> None:
        """NPC-issued deadline-lapse: the SAME dynamic-penalty formula
        applies (contract_type-gated, not issuer_type-gated), destroyed
        as a sink -- there is no issuer wallet for it to ever land in.
        Also closes the "no double-pay" requirement from a different
        angle: `escrow_state`'s column default is `HELD` even for an
        NPC row (models/contract.py -- NOT NULL, `default=ContractEscrowState.
        HELD`; `sweep_expired_accepted_contracts` never touches it for
        ANY issuer_type, see that candidate body's own docstring), so an
        NPC row genuinely DOES land in `sweep_expired_dispute_window`'s
        own candidate set (status==EXPIRED AND escrow_state==HELD AND
        past the window -- that filter never checks issuer_type at all).
        `needs_refund`'s own `issuer_type == PLAYER` guard is what
        actually keeps this side-effect-free: the guarded escrow_state
        flip still fires (a harmless HELD->REFUNDING mutation on a
        zero-balance row), but `issuer` stays None, so `acceptor.credits`
        is provably untouched by this sweep -- verified here empirically,
        not assumed from reading `needs_refund` alone."""
        now = datetime.now(UTC)
        # WELL above the 700cr dynamic penalty -- see the player-issued
        # deadline-lapse test's own comment for why.
        acceptor = _player(
            id=uuid.uuid4(), credits=2000,
            current_ship=_ship(cargo={"capacity": 500, "used": 3, "contents": {"ore": 3}}),
        )
        contract = _contract(
            contract_type=ContractType.BULK_PROCUREMENT, issuer_type=ContractIssuerType.NPC,
            acceptor_player_id=acceptor.id,
            quantity=10, payment=Decimal("1000.00"), penalty=Decimal("1000.00"),
            escrow_amount=Decimal("0"), escrow_state=ContractEscrowState.HELD,
            deadline=now - timedelta(hours=1),
        )
        acceptor.current_port_id = contract.destination_station_id
        locker = _locker(contract, acceptor.id, last_fee_settled_at=now)
        db = _FakeSession(contracts=[contract], players=[acceptor], lockers=[locker])

        deposit_result = storage_service.deposit_cargo(db, locker.id, acceptor.id, 3)
        assert deposit_result["completed"] is False

        sweep_result = contract_service.sweep_expired_accepted_contracts(
            db, now=now, expiry_gate=storage_service.gate_contract_expiry_on_locker,
        )
        assert sweep_result == {"expired": 1}
        assert contract.status == ContractStatus.EXPIRED
        assert acceptor.credits == 2000 - 700  # destroyed sink, no one's gain

        locker_sweep_result = storage_service.sweep_expired_lockers(db, now=now)
        assert locker_sweep_result == {"converted": 1}
        assert locker.status == StorageLockerStatus.CLAIMABLE  # NOT stranded

        # The dispute-window sweep DOES pick this row up (see docstring
        # above) but must be a true no-op on credits -- `needs_refund`'s
        # own issuer_type guard, proven live against this same db.
        dispute_result = contract_service.sweep_expired_dispute_window(db, now=now + timedelta(hours=49))
        assert dispute_result == {"refunded": 1}
        assert contract.escrow_state == ContractEscrowState.REFUNDING  # flipped, but...
        assert acceptor.credits == 2000 - 700  # ...untouched -- no issuer wallet, no side effect

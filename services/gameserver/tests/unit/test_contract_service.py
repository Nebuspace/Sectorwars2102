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
from sqlalchemy.sql.elements import Null
from sqlalchemy.sql.operators import in_op, is_

from src.models.contract import (
    Contract,
    ContractInsuranceCoverageTier,
    ContractIssuerType,
    ContractStatus,
    ContractType,
)
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
    if cond.operator is is_:
        # WO-CONTRACT-1-INSURANCE: `.is_(None)` (insure()'s
        # `Contract.insurance_coverage_tier.is_(None)` double-insure
        # guard). `cond.right` for an IS clause is a SQL singleton
        # (Null()/True_()/False_()), NOT a BindParameter -- it has no
        # `.value` attribute (verified: AttributeErrors on
        # `.value` for both `Null` and `True_`) -- so `isinstance`
        # against the singleton type is the only correct read, not a
        # `.value` access.
        if isinstance(cond.right, Null):
            return row_val is None
        raise NotImplementedError(f"unsupported IS operand {cond.right!r}")
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

    def with_for_update(self) -> "_FakeQuery":
        # WO-ECON-CONTRACT-MONEY-HARDEN: no-op passthrough. A single-
        # threaded fake session can't simulate a real Postgres row lock;
        # this just keeps the query chain (`.filter(...).with_for_update()
        # .first()`) working. The actual locking behavior is proven live
        # on Postgres, not here -- see contract_service._load_player's own
        # docstring.
        return self

    def populate_existing(self) -> "_FakeQuery":
        # WO-MONEY-REREAD-CLASS: no-op passthrough -- contract_service.
        # _load_player now chains .populate_existing() ahead of .with_for_
        # update() on every for_update=True re-read (identity-map freshness
        # guard). This fake has no identity map to refresh; the passthrough
        # just matches the real chainable-Query shape so the fixed route
        # code doesn't AttributeError. See money-reread-class-fake-query-
        # passthrough in mack's project memory.
        return self

    def first(self) -> Any:
        for row in self._rows:
            if all(_match(row, c) for c in self._criteria):
                return row
        return None

    def all(self) -> List[Any]:
        # WO-STORE-EXPIRY-CLAIMABLE + D19: sweep_expired_accepted_
        # contracts gathers its candidates upfront now (the expiry_gate
        # deferral fix -- see that function's own docstring for why a
        # repeated fresh .first() would infinite-loop on a deferred row).
        return [row for row in self._rows if all(_match(row, c) for c in self._criteria)]

    def count(self) -> int:
        return sum(1 for row in self._rows if all(_match(row, c) for c in self._criteria))


class _FakeNestedTransaction:
    """WO-ECON-CONTRACT-MONEY-HARDEN: no-op savepoint passthrough for
    db.begin_nested(). Never swallows an exception (__exit__ returns
    False) -- proves the sweep's OWN try/except around the `with` block
    catches and continues past a failing row; does not attempt to fake
    real SAVEPOINT rollback of Python attribute mutations (a single-
    threaded fake can't reproduce that faithfully -- see test_mack_
    attack_accepted_sweep.py's own poisoned-row test for what CAN be
    proven this way vs. what needs live Postgres)."""

    def __enter__(self) -> "_FakeNestedTransaction":
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> bool:
        return False


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

    def begin_nested(self) -> _FakeNestedTransaction:
        return _FakeNestedTransaction()

    def commit(self) -> None:
        raise AssertionError("service functions are flush-only -- the route commits")


class _DeferredInsureContractQuery:
    """See `_StaleSnapshotFakeSession`'s own docstring for what this
    models. Wraps a normal `_FakeQuery` for Contract, delegating every
    method -- only `.first()` is special-cased, and only when `.populate_
    existing()` was chained first."""

    def __init__(self, inner: _FakeQuery, session: "_StaleSnapshotFakeSession") -> None:
        self._inner = inner
        self._session = session
        self._populate_existing = False

    def filter(self, *conditions: Any) -> "_DeferredInsureContractQuery":
        self._inner = self._inner.filter(*conditions)
        return self

    def with_for_update(self) -> "_DeferredInsureContractQuery":
        self._inner = self._inner.with_for_update()
        return self

    def populate_existing(self) -> "_DeferredInsureContractQuery":
        self._populate_existing = True
        self._inner = self._inner.populate_existing()
        return self

    def first(self) -> Any:
        row = self._inner.first()
        if self._populate_existing and row is not None and self._session._pending_insure is not None:
            tier, premium = self._session._pending_insure
            row.insurance_coverage_tier = tier
            row.insurance_premium_paid = premium
            self._session._pending_insure = None  # applies exactly once
        return row

    def all(self) -> List[Any]:
        return self._inner.all()

    def count(self) -> int:
        return self._inner.count()


class _StaleSnapshotFakeSession(_FakeSession):
    """WO-1a-CORE (mack CRITICAL #1 + #2 regression coverage). The shared
    `_FakeQuery.populate_existing()` above is a documented no-op
    passthrough -- correct for every OTHER test in this file (this fake
    has no identity map to refresh), but insufficient to prove THIS fix,
    which is specifically about what happens when the post-lock
    `.populate_existing()` refresh DOES pick up a change a concurrent
    insure() call made.

    `queue_concurrent_insure(tier, premium)` primes a pending mutation
    that applies to the Contract row EXACTLY ONCE, on the first query
    that chains `.populate_existing()` -- i.e. `_load_contract`'s own
    plain (non-populate_existing) call still returns the ORIGINAL,
    uninsured snapshot, modeling "the unlocked read happened before the
    concurrent insure() committed"; the LATER `_refresh_contract_
    insurance_snapshot` call (which does chain `.populate_existing()`)
    picks up the change, modeling "the concurrent insure() committed
    sometime before our post-lock refresh".

    This proves the fix's STRUCTURAL post-condition (refresh happens,
    and downstream code acts on its result) -- not genuine cross-
    connection SQLAlchemy identity-map caching, which needs live
    Postgres to reproduce faithfully (see identity-map-poisons-locked-
    reread project memory for the general class of bug this belongs to)."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._pending_insure: Optional[tuple] = None

    def queue_concurrent_insure(self, tier: Any, premium: Decimal) -> None:
        self._pending_insure = (tier, premium)

    def query(self, model: Any) -> Any:
        q = super().query(model)
        if model is Contract:
            return _DeferredInsureContractQuery(q, self)
        return q


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
        contract_type=ContractType.CARGO_DELIVERY,
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
        # WO-CONTRACT-1-INSURANCE
        insurance_coverage_tier=None,
        insurance_premium_paid=Decimal("0"),
        insurance_claim_filed=False,
        escrow_amount=Decimal("0"),
        escrow_state=None,
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

    def test_insurance_pro_rata_refund_worked_example(self) -> None:
        """ADR-0062 E-I2 worked example, quoted math: accepted_at=T,
        deadline=T+10h (duration 36000s), premium=100, walked away at
        T+4h (elapsed 14400s) -> elapsed/duration=0.4, remaining_fraction
        =0.6, refund = 100 * 0.6 * 0.90 = 54.00 EXACTLY. Orthogonal to the
        walk-away penalty (this contract is NPC-issued -- no issuer-escrow
        branch fires at all; the insurance refund is independent of
        issuer_type)."""
        accepted_at = _NOW
        deadline = _NOW + timedelta(hours=10)
        c = _contract(
            status=ContractStatus.ACCEPTED, penalty=Decimal("500.00"),
            accepted_at=accepted_at, deadline=deadline,
            insurance_coverage_tier=ContractInsuranceCoverageTier.STANDARD,
            insurance_premium_paid=Decimal("100.00"),
        )
        acceptor = _player(credits=2000)
        c.acceptor_player_id = acceptor.id
        db = _FakeSession(contracts=[c], players=[acceptor])
        cancelled_at = accepted_at + timedelta(hours=4)

        result = contract_service.abandon(db, c.id, acceptor.id, now=cancelled_at)

        assert result["penalty_charged"] == 500
        assert result["insurance_refund"] == 54
        assert acceptor.credits == 2000 - 500 + 54  # net -446

    def test_no_insurance_refund_field_when_uninsured(self) -> None:
        c = _contract(status=ContractStatus.ACCEPTED, penalty=Decimal("500.00"))
        acceptor = _player(credits=2000)
        c.acceptor_player_id = acceptor.id
        db = _FakeSession(contracts=[c], players=[acceptor])

        result = contract_service.abandon(db, c.id, acceptor.id, now=_NOW)

        assert result["insurance_refund"] == 0
        assert acceptor.credits == 2000 - 500  # untouched by any insurance math

    def test_insurance_refund_is_zero_once_deadline_reached(self) -> None:
        """remaining_fraction = max(0, 1 - elapsed/duration) floors at 0 --
        cancelling AT (or after) the deadline pays out no refund, not a
        negative one."""
        accepted_at = _NOW
        deadline = _NOW + timedelta(hours=2)
        c = _contract(
            status=ContractStatus.ACCEPTED, penalty=Decimal("100.00"),
            accepted_at=accepted_at, deadline=deadline,
            insurance_coverage_tier=ContractInsuranceCoverageTier.BASIC,
            insurance_premium_paid=Decimal("40.00"),
        )
        acceptor = _player(credits=1000)
        c.acceptor_player_id = acceptor.id
        db = _FakeSession(contracts=[c], players=[acceptor])

        result = contract_service.abandon(db, c.id, acceptor.id, now=deadline)

        assert result["insurance_refund"] == 0


@pytest.mark.unit
class TestInsure:
    @pytest.mark.parametrize(
        "tier,expected_premium",
        [
            (ContractInsuranceCoverageTier.BASIC, 20.0),
            (ContractInsuranceCoverageTier.STANDARD, 50.0),
            (ContractInsuranceCoverageTier.HAZARD, 100.0),
        ],
    )
    def test_premium_charged_per_tier(
        self, tier: ContractInsuranceCoverageTier, expected_premium: float,
    ) -> None:
        c = _contract(status=ContractStatus.ACCEPTED, payment=Decimal("1000.00"))
        acceptor = _player(credits=5000)
        c.acceptor_player_id = acceptor.id
        db = _FakeSession(contracts=[c], players=[acceptor])

        result = contract_service.insure(db, c.id, acceptor.id, tier, now=_NOW)

        assert result["insurance_premium_paid"] == expected_premium
        assert result["insurance_coverage_tier"] == tier.value
        assert c.insurance_coverage_tier == tier
        assert c.insurance_premium_paid == Decimal(str(expected_premium))
        assert acceptor.credits == 5000 - expected_premium
        assert db.flush_calls == 1

    def test_not_accepted_rejected(self) -> None:
        c = _contract(status=ContractStatus.POSTED)
        acceptor = _player(credits=5000)
        c.acceptor_player_id = acceptor.id
        db = _FakeSession(contracts=[c], players=[acceptor])
        with pytest.raises(ContractConflictError, match="stale_status"):
            contract_service.insure(db, c.id, acceptor.id, ContractInsuranceCoverageTier.BASIC, now=_NOW)

    def test_not_your_contract_rejected(self) -> None:
        c = _contract(status=ContractStatus.ACCEPTED)
        c.acceptor_player_id = uuid.uuid4()
        stranger = _player(credits=5000)
        db = _FakeSession(contracts=[c], players=[stranger])
        with pytest.raises(ContractError, match="not accepted by you"):
            contract_service.insure(db, c.id, stranger.id, ContractInsuranceCoverageTier.BASIC, now=_NOW)

    def test_already_insured_rejected_and_feeless(self) -> None:
        c = _contract(
            status=ContractStatus.ACCEPTED,
            insurance_coverage_tier=ContractInsuranceCoverageTier.BASIC,
            insurance_premium_paid=Decimal("20.00"),
        )
        acceptor = _player(credits=5000)
        c.acceptor_player_id = acceptor.id
        db = _FakeSession(contracts=[c], players=[acceptor])
        with pytest.raises(ContractError, match="already_insured"):
            contract_service.insure(db, c.id, acceptor.id, ContractInsuranceCoverageTier.STANDARD, now=_NOW)
        assert acceptor.credits == 5000  # never charged
        assert c.insurance_coverage_tier == ContractInsuranceCoverageTier.BASIC  # unchanged

    def test_insufficient_credits_rejected_no_mutation(self) -> None:
        c = _contract(status=ContractStatus.ACCEPTED, payment=Decimal("1000.00"))
        acceptor = _player(credits=5)
        c.acceptor_player_id = acceptor.id
        db = _FakeSession(contracts=[c], players=[acceptor])
        with pytest.raises(ContractError, match="insufficient_credits"):
            contract_service.insure(db, c.id, acceptor.id, ContractInsuranceCoverageTier.HAZARD, now=_NOW)
        assert c.insurance_coverage_tier is None
        assert acceptor.credits == 5

    def test_concurrent_double_insure_second_caller_rejected_feeless(self) -> None:
        """TWO insure() calls against the SAME fake row, same shape as
        TestAccept.test_concurrent_accept_second_caller_409s_feeless: the
        first call's trailing setattr (inside `_guarded_insure`) mutates
        the SAME in-memory `contract` object both calls share, so the
        second call's own Python-level `already_insured` pre-check --
        exactly like accept()'s `status != POSTED` pre-check -- fires
        first in this SEQUENTIAL single-threaded fake, before `_guarded_
        insure`'s atomic UPDATE-WHERE is even reached. That UPDATE-WHERE
        is what closes the GENUINE two-CONNECTION race (two callers each
        holding their own stale pre-commit read) -- unprovable without
        live Postgres (see this module's own docstring / test_fleet_
        battle_locks.py's documented precedent for why). What IS provable
        here, and is exactly what accept()'s own sequential-race test
        proves too: the sequential second attempt is rejected AND
        feeless -- the premium is debited exactly once, never twice."""
        c = _contract(status=ContractStatus.ACCEPTED, payment=Decimal("1000.00"))
        acceptor = _player(credits=5000)
        c.acceptor_player_id = acceptor.id
        db = _FakeSession(contracts=[c], players=[acceptor])

        contract_service.insure(db, c.id, acceptor.id, ContractInsuranceCoverageTier.BASIC, now=_NOW)
        balance_after_first = acceptor.credits
        assert balance_after_first == 4980  # 5000 - 20 (2% of 1000)

        with pytest.raises(ContractError, match="already_insured"):
            contract_service.insure(db, c.id, acceptor.id, ContractInsuranceCoverageTier.HAZARD, now=_NOW)

        # Conservation: the race loser's attempt charged NOTHING -- the
        # premium was debited exactly once.
        assert acceptor.credits == balance_after_first
        assert c.insurance_coverage_tier == ContractInsuranceCoverageTier.BASIC  # unchanged by the loser


@pytest.mark.unit
class TestInsurePremiumCompletionInteraction:
    """WO-1a-CORE: the claim-specific tests that lived alongside this one
    were excised (claim handling deferred to WO-1b-CLAIM-SAFETY). This
    single test is unrelated to the claim -- it only exercises insure()
    + complete() -- so it survives, just relocated out of the now-deleted
    TestFileInsuranceClaim class."""

    def test_completion_never_refunds_the_premium(self) -> None:
        """contracts.md:62 -- 'On completion: released to insurer (not
        refunded)'. complete() is a DIFFERENT transition entirely
        (ACCEPTED -> COMPLETED, not CANCELLED) -- proves the premium the
        acceptor already paid at insure() time is simply never touched by
        complete()'s own payout math."""
        destination_id = uuid.uuid4()
        c = _contract(
            status=ContractStatus.ACCEPTED, destination_station_id=destination_id,
            commodity_type="ore", quantity=50, payment=Decimal("3000.00"),
            insurance_coverage_tier=ContractInsuranceCoverageTier.HAZARD,
            insurance_premium_paid=Decimal("300.00"),
        )
        ship = _real_ship(cargo={"capacity": 500, "used": 80, "contents": {"ore": 80}})
        player = _player(credits=1000, is_docked=True, current_port_id=destination_id, current_ship=ship)
        c.acceptor_player_id = player.id
        db = _FakeSession(contracts=[c], players=[player])

        contract_service.complete(db, c.id, player.id, now=_NOW)

        assert c.status == ContractStatus.COMPLETED
        assert player.credits == 1000 + 3000  # payment only -- no premium refund folded in
        assert c.insurance_premium_paid == Decimal("300.00")  # untouched


@pytest.mark.unit
class TestInsureVsAbandonCancelStalenessRace:
    """WO-1a-CORE (mack CRITICAL #1 + #2): a real insure() commit landing
    in the window between abandon()'s / cancel_player_contract()'s
    initial UNLOCKED `_load_contract` read and their post-lock refresh
    used to leave the acceptor's just-paid premium silently forfeited --
    ZERO contention required, not a rare edge (see each function's own
    docstring for the exact exploit). `_StaleSnapshotFakeSession` (see
    its own docstring) simulates exactly that window. A genuine cross-
    connection SQLAlchemy identity-map proof needs live Postgres; this
    proves the fix's structural post-condition: once refreshed, the
    premium IS refunded, not forfeited."""

    def test_cancel_player_contract_picks_up_a_concurrently_insured_tier(self) -> None:
        issuer = _player(credits=5000)
        acceptor = _player(credits=5000)
        accepted_at = _NOW - timedelta(hours=4)
        c = _contract(
            status=ContractStatus.ACCEPTED, issuer_type=ContractIssuerType.PLAYER,
            issuer_id=issuer.id, escrow_amount=Decimal("1000.00"), payment=Decimal("1000.00"),
            deadline=accepted_at + timedelta(hours=10), accepted_at=accepted_at,
            insurance_coverage_tier=None, insurance_premium_paid=Decimal("0"),
        )
        c.acceptor_player_id = acceptor.id
        db = _StaleSnapshotFakeSession(contracts=[c], players=[issuer, acceptor])
        # Simulate: insure() (STANDARD, 50cr premium) committed AFTER
        # cancel_player_contract's initial _load_contract read but BEFORE
        # its post-lock refresh.
        db.queue_concurrent_insure(ContractInsuranceCoverageTier.STANDARD, Decimal("50.00"))

        result = contract_service.cancel_player_contract(db, c.id, issuer.id, now=_NOW)

        # Pre-fix: needs_acceptor_lock read the STALE tier=None -> acceptor
        # never locked -> insurance_refund would be 0, premium forfeited
        # with the issuer paying nothing for it either -- a pure sink.
        # Post-fix: refresh picks up the STANDARD tier -> pro-rata refund
        # (elapsed=4h of a 10h window -> remaining_fraction=0.6 -> 50 *
        # 0.6 * 0.90 = 27.00) lands on the acceptor.
        assert result["refund"] == 880.0  # unchanged issuer kill-fee math: 1000-20-100
        assert result["insurance_refund"] == 27
        assert acceptor.credits == 5000 + 27
        # NOTE: this fixture builds the Contract directly (not via
        # post_player_contract), so the issuer's 1000cr escrow was never
        # actually debited from their starting balance in this test --
        # only the kill-fee refund itself lands.
        assert issuer.credits == 5000 + 880

    def test_abandon_picks_up_a_concurrently_insured_tier(self) -> None:
        acceptor = _player(credits=5000)
        accepted_at = _NOW - timedelta(hours=4)
        c = _contract(
            status=ContractStatus.ACCEPTED, penalty=Decimal("500.00"),
            deadline=accepted_at + timedelta(hours=10), accepted_at=accepted_at,
            insurance_coverage_tier=None, insurance_premium_paid=Decimal("0"),
        )
        c.acceptor_player_id = acceptor.id
        db = _StaleSnapshotFakeSession(contracts=[c], players=[acceptor])
        # Simulate: insure() (STANDARD, 100cr premium) committed AFTER
        # abandon()'s initial _load_contract read but BEFORE its post-
        # lock refresh. NPC-issued (default issuer_type) -- proves the
        # fix applies on the single-player-lock branch too, not just the
        # dual-lock (PLAYER-issued) one.
        db.queue_concurrent_insure(ContractInsuranceCoverageTier.STANDARD, Decimal("100.00"))

        result = contract_service.abandon(db, c.id, acceptor.id, now=_NOW)

        # elapsed=4h of a 10h window -> remaining_fraction=0.6 -> 100 *
        # 0.6 * 0.90 = 54.00 (pre-fix: 0, premium forfeited).
        assert result["penalty_charged"] == 500
        assert result["insurance_refund"] == 54
        assert acceptor.credits == 5000 - 500 + 54


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

    def test_expiry_gate_none_default_is_unaffected(self) -> None:
        """WO-STORE-EXPIRY-CLAIMABLE + D19: the new expiry_gate parameter
        defaults to None -- every OTHER caller (including every existing
        test above, called positionally/keyword without it) must behave
        identically to before it existed."""
        acceptor = _player(credits=5000)
        c = _contract(
            status=ContractStatus.ACCEPTED, deadline=_NOW - timedelta(minutes=1),
            acceptor_player_id=acceptor.id, penalty=Decimal("300.00"),
        )
        db = _FakeSession(contracts=[c], players=[acceptor])

        result = contract_service.sweep_expired_accepted_contracts(db, now=_NOW, expiry_gate=None)

        assert result == {"expired": 1}
        assert c.status == ContractStatus.EXPIRED
        assert acceptor.credits == 4700

    def test_expiry_gate_defers_one_candidate_others_still_expire(self) -> None:
        """The gate contract itself, isolated from storage_service's own
        gate_contract_expiry_on_locker implementation (that integration
        lives in test_storage_service.py): a gate that vetoes ONE
        specific contract leaves it ACCEPTED and unpenalized while its
        sibling still expires normally in the SAME pass."""
        acceptor = _player(credits=5000)
        deferred = _contract(
            status=ContractStatus.ACCEPTED, deadline=_NOW - timedelta(minutes=1),
            acceptor_player_id=acceptor.id, penalty=Decimal("999.00"),
        )
        expires_normally = _contract(
            status=ContractStatus.ACCEPTED, deadline=_NOW - timedelta(minutes=1),
            acceptor_player_id=acceptor.id, penalty=Decimal("100.00"),
        )
        db = _FakeSession(contracts=[deferred, expires_normally], players=[acceptor])

        def gate(db, candidate):
            return candidate.id != deferred.id

        result = contract_service.sweep_expired_accepted_contracts(db, now=_NOW, expiry_gate=gate)

        assert result == {"expired": 1}
        assert deferred.status == ContractStatus.ACCEPTED  # vetoed, untouched
        assert expires_normally.status == ContractStatus.EXPIRED
        assert acceptor.credits == 4900  # only the 100cr penalty charged

    def test_expiry_gate_always_deferring_terminates_no_infinite_loop(self) -> None:
        """THE regression this WO's own .all()-based rewrite fixes: the
        original while-True + repeated-fresh-.first() shape would spin
        forever on a candidate the gate ALWAYS defers, since deferring
        doesn't change the row's own status. This must terminate
        cleanly, leaving the candidate untouched."""
        acceptor = _player(credits=5000)
        c = _contract(
            status=ContractStatus.ACCEPTED, deadline=_NOW - timedelta(minutes=1),
            acceptor_player_id=acceptor.id,
        )
        db = _FakeSession(contracts=[c], players=[acceptor])

        result = contract_service.sweep_expired_accepted_contracts(
            db, now=_NOW, expiry_gate=lambda db, candidate: False,
        )

        assert result == {"expired": 0}
        assert c.status == ContractStatus.ACCEPTED  # deferred forever this tick, not corrupted

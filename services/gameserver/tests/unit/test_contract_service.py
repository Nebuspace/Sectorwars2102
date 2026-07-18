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
    ContractDisputeResolution,
    ContractEscrowState,
    ContractInsuranceCoverageTier,
    ContractIssuerType,
    ContractStatus,
    ContractType,
)
from src.models.ship import Ship, ShipType
from src.models.station import StationStatus
from src.services import contract_bulk, contract_dispute, contract_escrow_core, contract_service, storage_service
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
    if cond.operator is operator.ne:
        # WO-CONTRACT-57 addendum: _bulk_expire_remaining_posted_contracts
        # now excludes the per-candidate loop's own eligible set via a
        # `!=` predicate (issuer_type != PLAYER / escrow_state != HELD).
        return row_val != cond.right.value
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
    def __init__(
        self, *, contracts: Optional[List[Any]] = None, players: Optional[List[Any]] = None,
        stations: Optional[List[Any]] = None,
    ) -> None:
        self.contracts = contracts or []
        self.players = players or []
        self.stations = stations or []
        self.flush_calls = 0

    def query(self, model: Any) -> _FakeQuery:
        if model is Contract:
            return _FakeQuery(self.contracts)
        from src.models.player import Player
        if model is Player:
            return _FakeQuery(self.players)
        from src.models.station import Station
        if model is Station:
            return _FakeQuery(self.stations)
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
        faction_id=None,
        deadline=datetime(2026, 1, 2, tzinfo=UTC),
        posted_at=datetime(2026, 1, 1, tzinfo=UTC),
        accepted_at=None,
        completed_at=None,
        # WO-CONTRACT-1-INSURANCE
        insurance_coverage_tier=None,
        insurance_premium_paid=Decimal("0"),
        insurance_claim_filed=False,
        # WO-CONTRACT-1b-CLAIM-SAFETY
        insurance_pool_reserve=Decimal("0"),
        escrow_amount=Decimal("0"),
        escrow_state=None,
        # WO-CONTRACT-2-DISPUTE-T1
        dispute_filed_at=None,
        dispute_resolution=None,
        dispute_resolved_at=None,
        dispute_notes=None,
        escalated_to_admin=False,
        # WO-CONTRACT-3-NPCGEN-TYPES
        reputation_penalty=None,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def _player(**overrides: Any) -> SimpleNamespace:
    base = dict(
        id=uuid.uuid4(), credits=10000, is_docked=False, current_port_id=None, current_ship=None,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def _station(**overrides: Any) -> SimpleNamespace:
    base = dict(id=uuid.uuid4(), status=StationStatus.OPERATIONAL)
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
class TestCompleteExpressEarlyArrivalBonus:
    """WO-CONTRACT-3-NPCGEN-TYPES: canon's exact formula (contracts.md:323)
    -- linear 0-25% bonus on `payment` once MORE than 50% of the
    [posted_at, deadline] window remains at delivery, gated to
    express_delivery only."""

    def _setup(self, **contract_overrides: Any):
        destination_id = uuid.uuid4()
        posted_at = datetime(2026, 1, 1, 0, 0, tzinfo=UTC)
        deadline = datetime(2026, 1, 1, 2, 0, tzinfo=UTC)  # 2h window
        c = _contract(
            status=ContractStatus.ACCEPTED, destination_station_id=destination_id,
            commodity_type="ore", quantity=50, payment=Decimal("2000.00"),
            contract_type=ContractType.EXPRESS_DELIVERY,
            posted_at=posted_at, deadline=deadline,
        )
        for k, v in contract_overrides.items():
            setattr(c, k, v)
        ship = _real_ship(cargo={"capacity": 500, "used": 80, "contents": {"ore": 80}})
        player = _player(credits=1000, is_docked=True, current_port_id=destination_id, current_ship=ship)
        c.acceptor_player_id = player.id
        db = _FakeSession(contracts=[c], players=[player])
        return db, c, player

    def test_delivered_with_875pct_window_remaining_pays_partial_bonus(self) -> None:
        db, c, player = self._setup()
        now = datetime(2026, 1, 1, 0, 15, tzinfo=UTC)  # 15min elapsed of 2h -> 87.5% remaining
        result = contract_service.complete(db, c.id, player.id, now=now)
        # bonus_pct = 0.25 x (0.875-0.5)/(1-0.5) = 0.1875 -> 2000 x 0.1875 = 375
        assert result["early_arrival_bonus"] == 375
        assert result["payout"] == 2000
        assert player.credits == 1000 + 2000 + 375

    def test_delivered_at_exactly_50pct_remaining_pays_no_bonus(self) -> None:
        """Canon says "greater than 50%" -- exactly 50% is excluded."""
        db, c, player = self._setup()
        now = datetime(2026, 1, 1, 1, 0, tzinfo=UTC)  # 1h elapsed of 2h -> exactly 50% remaining
        result = contract_service.complete(db, c.id, player.id, now=now)
        assert result["early_arrival_bonus"] == 0
        assert player.credits == 1000 + 2000

    def test_delivered_instantly_caps_at_25pct(self) -> None:
        db, c, player = self._setup()
        now = c.posted_at  # delivered the instant it was posted -- 100% window remaining
        result = contract_service.complete(db, c.id, player.id, now=now)
        assert result["early_arrival_bonus"] == 500  # 2000 x 0.25 cap

    def test_clock_skew_before_posted_at_still_caps_at_25pct_never_more(self) -> None:
        """Money-path gate follow-up (cipher, WO-3 gate pass): `now <
        posted_at` drives remaining_frac ABOVE 1.0 (the pre-min() bonus_pct
        formula would compute > 0.25) -- `min(bonus_pct, EARLY_ARRIVAL_
        BONUS_CAP_PCT)` is the ONLY thing standing between an arbitrary
        clock-skew value and an uncapped payout. Cipher fuzzed this up to
        1000 years before posted_at; mack swept remaining_frac to 150% --
        both confirmed the cap holds. Pinned here as a permanent
        regression test (the 0/50/87.5/100% cases above never exercise
        remaining_frac > 1.0) at two payment scales so a future change to
        the clamp ordering can't silently reintroduce an unbounded mint."""
        db, c, player = self._setup(payment=Decimal("2000.00"))
        now = c.posted_at - timedelta(hours=1)  # 1h BEFORE posted_at -- negative elapsed
        result = contract_service.complete(db, c.id, player.id, now=now)
        assert result["early_arrival_bonus"] == 500  # capped at 2000 x 0.25, same as the 100% case

        # A second, more extreme skew (cipher's own 1000-year stress case) at
        # a different payment scale, to prove the cap is payment-relative
        # (still exactly 25%) and not merely coincidentally correct at 2000.
        db2, c2, player2 = self._setup(payment=Decimal("100.00"))
        now_extreme = c2.posted_at - timedelta(days=365 * 1000)
        result2 = contract_service.complete(db2, c2.id, player2.id, now=now_extreme)
        assert result2["early_arrival_bonus"] == 25  # capped at 100 x 0.25, never more

    def test_delivered_late_in_window_pays_no_bonus(self) -> None:
        db, c, player = self._setup()
        now = datetime(2026, 1, 1, 1, 55, tzinfo=UTC)  # 5min left of 2h
        result = contract_service.complete(db, c.id, player.id, now=now)
        assert result["early_arrival_bonus"] == 0

    def test_cargo_delivery_never_gets_the_bonus_even_delivered_instantly(self) -> None:
        db, c, player = self._setup(contract_type=ContractType.CARGO_DELIVERY)
        now = c.posted_at
        result = contract_service.complete(db, c.id, player.id, now=now)
        assert result["early_arrival_bonus"] == 0
        assert player.credits == 1000 + 2000


@pytest.mark.unit
class TestCompleteHazardousTransportReputationPenalty:
    """WO-CONTRACT-3-NPCGEN-TYPES (partial): contracts.md:420 -- "apply a
    faction penalty on completion". `reputation_penalty` is set on the
    contract row at GENERATION time (contract_generator.py); complete() is
    the first real READER of it, applied via the same, already-wired
    apply_faction_rep_delta helper combat_service.py / contraband_service.py
    use for this exact kind of in-transaction faction-rep hook."""

    def _accepted_setup(self, **contract_overrides: Any):
        destination_id = uuid.uuid4()
        c = _contract(
            status=ContractStatus.ACCEPTED, destination_station_id=destination_id,
            commodity_type="ore", quantity=50, payment=Decimal("3000.00"),
            contract_type=ContractType.HAZARDOUS_TRANSPORT, reputation_penalty=-30,
        )
        for k, v in contract_overrides.items():
            setattr(c, k, v)
        ship = _real_ship(cargo={"capacity": 500, "used": 80, "contents": {"ore": 80}})
        player = _player(credits=1000, is_docked=True, current_port_id=destination_id, current_ship=ship)
        c.acceptor_player_id = player.id
        db = _FakeSession(contracts=[c], players=[player])
        return db, c, player

    def test_completing_hazardous_transport_applies_federation_rep_penalty(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        calls: List[Any] = []
        monkeypatch.setattr(
            contract_service, "apply_faction_rep_delta",
            lambda db, player_id, faction_type, delta, reason: calls.append(
                (player_id, faction_type, delta, reason)
            ),
        )
        db, c, player = self._accepted_setup()
        contract_service.complete(db, c.id, player.id, now=_NOW)

        assert len(calls) == 1
        called_player_id, called_faction_type, called_delta, called_reason = calls[0]
        from src.models.faction import FactionType
        assert called_player_id == player.id
        assert called_faction_type == FactionType.FEDERATION
        assert called_delta == -30  # the contract row's own reputation_penalty, not a hardcoded literal
        assert called_reason == "hazardous_transport_contract_completed"

    def test_cargo_delivery_never_applies_a_reputation_penalty(self, monkeypatch: pytest.MonkeyPatch) -> None:
        calls: List[Any] = []
        monkeypatch.setattr(
            contract_service, "apply_faction_rep_delta",
            lambda *a, **kw: calls.append((a, kw)),
        )
        c = _contract(
            status=ContractStatus.ACCEPTED, contract_type=ContractType.CARGO_DELIVERY,
            commodity_type="ore", quantity=50, payment=Decimal("1000.00"),
        )
        ship = _real_ship(cargo={"capacity": 500, "used": 80, "contents": {"ore": 80}})
        player = _player(credits=1000, is_docked=True, current_port_id=c.destination_station_id, current_ship=ship)
        c.acceptor_player_id = player.id
        db = _FakeSession(contracts=[c], players=[player])

        contract_service.complete(db, c.id, player.id, now=_NOW)
        assert calls == []

    def test_hazardous_transport_with_no_reputation_penalty_set_does_not_crash(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Defensive guard -- a hazardous_transport row somehow missing its
        reputation_penalty (impossible via this WO's own generator, which
        always sets it) must never crash completion or call the helper
        with a None/0 delta."""
        calls: List[Any] = []
        monkeypatch.setattr(
            contract_service, "apply_faction_rep_delta",
            lambda *a, **kw: calls.append((a, kw)),
        )
        db, c, player = self._accepted_setup(reputation_penalty=None)
        result = contract_service.complete(db, c.id, player.id, now=_NOW)
        assert calls == []
        assert result["status"] == "completed"


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
class TestComputeBulkWalkawayPenalty:
    """WO-CONTRACT-4-BULK: `_compute_bulk_walkaway_penalty`'s formula (a)
    in isolation -- pure math, no DB/session needed. `payment x
    (quantity - stored_units) / quantity`, whole-credit ROUND_HALF_UP,
    clamped >= 0."""

    def test_partial_fill_charges_the_remaining_fraction(self) -> None:
        # 7/10 remaining.
        assert contract_service._compute_bulk_walkaway_penalty(
            Decimal("1000"), 10, 3,
        ) == Decimal("700.00")

    def test_full_delivery_charges_zero(self) -> None:
        assert contract_service._compute_bulk_walkaway_penalty(
            Decimal("1000"), 10, 10,
        ) == Decimal("0")

    def test_zero_delivery_charges_the_full_payment(self) -> None:
        """Degenerate resting value -- matches post_player_contract's own
        static `penalty = payment` default exactly."""
        assert contract_service._compute_bulk_walkaway_penalty(
            Decimal("1000"), 10, 0,
        ) == Decimal("1000.00")

    def test_over_delivered_stored_units_clamps_to_zero_not_negative(self) -> None:
        """Defensive -- stored_units should never exceed quantity in
        practice (deposit_cargo completes the contract at exactly full
        quota), but a penalty must never go negative if it somehow did."""
        assert contract_service._compute_bulk_walkaway_penalty(
            Decimal("1000"), 10, 15,
        ) == Decimal("0")

    def test_fractional_result_rounds_half_up(self) -> None:
        # 4/7 * 1000 = 571.42857... -> rounds up to 571.43.
        assert contract_service._compute_bulk_walkaway_penalty(
            Decimal("1000"), 7, 3,
        ) == Decimal("571.43")

    def test_zero_quantity_defensive_guard(self) -> None:
        """quantity <= 0 should never reach here (post_player_contract
        already rejects it at creation) -- defensive zero, not a
        division-by-zero crash."""
        assert contract_service._compute_bulk_walkaway_penalty(
            Decimal("1000"), 0, 0,
        ) == Decimal("0")


@pytest.mark.unit
class TestAbandonBulkProcurement:
    """WO-CONTRACT-4-BULK: abandon()'s bulk-aware dispatch fix -- a
    bulk_procurement contract's walk-away penalty is DYNAMIC (formula-a,
    from the Locker's actual fill) instead of the static `contract.
    penalty` column, and its terminal status is EXPIRED (not CANCELLED)
    so the existing sweep_expired_lockers picks up and converts its
    Locker to CLAIMABLE on the next tick -- the stranded-locker bug this
    WO fixes. `storage_service.get_bulk_locker_state` is mocked (a local/
    deferred import at the call site) rather than modeling a real
    StorageLocker row -- this fake session's WHERE-interpreter doesn't
    support the aggregate func.sum() query _stored_units uses; the
    contract-service-side dispatch/formula logic is what's under test
    here, not storage_service's own internals (covered by its own test
    file)."""

    def test_bulk_with_active_locker_charges_dynamic_penalty_and_expires(self, monkeypatch) -> None:
        issuer = _player(credits=5000)
        acceptor = _player(credits=2000)
        c = _contract(
            status=ContractStatus.ACCEPTED, contract_type=ContractType.BULK_PROCUREMENT,
            issuer_type=ContractIssuerType.PLAYER, issuer_id=issuer.id,
            acceptor_player_id=acceptor.id,
            quantity=10, payment=Decimal("1000.00"), penalty=Decimal("1000.00"),
            escrow_amount=Decimal("1000.00"), escrow_state=ContractEscrowState.HELD,
        )
        db = _FakeSession(contracts=[c], players=[issuer, acceptor])
        locker_id = uuid.uuid4()
        monkeypatch.setattr(storage_service, "get_bulk_locker_state", lambda db, contract: (locker_id, 3))

        result = contract_service.abandon(db, c.id, acceptor.id, now=_NOW)

        # 7/10 remaining -> 700cr dynamic penalty, NOT the static 1000.
        assert result["penalty_charged"] == 700
        assert acceptor.credits == 2000 - 700
        assert c.status == ContractStatus.EXPIRED  # NOT CANCELLED -- lets sweep_expired_lockers pick up the locker
        # Issuer still refunded IMMEDIATELY and IN FULL -- unchanged from cargo_delivery's own convention.
        assert issuer.credits == 5000 + 1000
        assert c.escrow_state == ContractEscrowState.REFUNDING  # gates dispute-window's HELD filter -- no double-refund

    def test_bulk_with_no_locker_falls_back_to_static_penalty(self, monkeypatch) -> None:
        """Degenerate case -- no active Locker at all (never deposited
        anything): undelivered == full quantity, penalty == payment,
        identical to the static default."""
        acceptor = _player(credits=2000)
        c = _contract(
            status=ContractStatus.ACCEPTED, contract_type=ContractType.BULK_PROCUREMENT,
            issuer_type=ContractIssuerType.NPC,
            acceptor_player_id=acceptor.id,
            quantity=10, payment=Decimal("1000.00"), penalty=Decimal("1000.00"),
        )
        db = _FakeSession(contracts=[c], players=[acceptor])
        monkeypatch.setattr(storage_service, "get_bulk_locker_state", lambda db, contract: None)

        result = contract_service.abandon(db, c.id, acceptor.id, now=_NOW)

        assert result["penalty_charged"] == 1000  # == static default, unchanged
        assert acceptor.credits == 2000 - 1000
        assert c.status == ContractStatus.EXPIRED

    def test_non_bulk_abandon_stays_byte_identical(self) -> None:
        """cargo_delivery (or any other non-bulk type) must be COMPLETELY
        unaffected by this WO -- static penalty, CANCELLED terminal
        status, no storage_service call at all (no monkeypatch needed --
        a real, un-mocked storage_service.get_bulk_locker_state would
        crash on this fake session's unsupported aggregate query if this
        path were ever mistakenly reached, so this test also proves the
        bulk branch is correctly gated, not just that its output matches)."""
        acceptor = _player(credits=2000)
        c = _contract(
            status=ContractStatus.ACCEPTED, contract_type=ContractType.CARGO_DELIVERY,
            acceptor_player_id=acceptor.id, penalty=Decimal("1000.00"),
        )
        db = _FakeSession(contracts=[c], players=[acceptor])

        result = contract_service.abandon(db, c.id, acceptor.id, now=_NOW)

        assert result["penalty_charged"] == 1000
        assert acceptor.credits == 2000 - 1000
        assert c.status == ContractStatus.CANCELLED  # unchanged, NOT EXPIRED


@pytest.mark.unit
class TestDeliver:
    """WO-CONTRACT-3b-BULK -- contract_bulk.py's `deliver()`, re-exported
    as `contract_service.deliver`."""

    def _accepted_setup(self, *, quantity: int, payment: Decimal, **contract_overrides: Any):
        destination_id = uuid.uuid4()
        c = _contract(
            status=ContractStatus.ACCEPTED, destination_station_id=destination_id,
            contract_type=ContractType.BULK_PROCUREMENT,
            commodity_type="ore", quantity=quantity, payment=payment,
            partial_fulfilled_amount=0, partial_fulfilled_payout=Decimal("0"),
        )
        for k, v in contract_overrides.items():
            setattr(c, k, v)
        ship = _real_ship(cargo={"capacity": 5000, "used": 500, "contents": {"ore": 500}})
        player = _player(credits=1000, is_docked=True, current_port_id=destination_id, current_ship=ship)
        c.acceptor_player_id = player.id
        db = _FakeSession(contracts=[c], players=[player])
        return db, c, player

    def test_single_delivery_covering_full_quantity_completes_directly(self) -> None:
        """A one-shot delivery of the ENTIRE quantity never bridges
        through IN_PROGRESS -- straight ACCEPTED -> COMPLETED."""
        db, c, player = self._accepted_setup(quantity=100, payment=Decimal("1000.00"))
        result = contract_service.deliver(db, c.id, player.id, 100, now=_NOW)

        assert result["status"] == "completed"
        assert result["payout_this_delivery"] == 1000
        assert c.status == ContractStatus.COMPLETED
        assert c.partial_fulfilled_amount == 100
        assert c.partial_fulfilled_payout == Decimal("1000")
        assert player.credits == 1000 + 1000
        assert c.completed_at == _NOW

    def test_partial_then_completing_delivery_sums_to_exact_payment(self) -> None:
        db, c, player = self._accepted_setup(quantity=100, payment=Decimal("1000.00"))

        first = contract_service.deliver(db, c.id, player.id, 40, now=_NOW)
        assert first["status"] == "in_progress"
        assert first["payout_this_delivery"] == 400
        assert c.status == ContractStatus.IN_PROGRESS
        assert c.partial_fulfilled_amount == 40
        assert player.credits == 1000 + 400

        second = contract_service.deliver(db, c.id, player.id, 60, now=_NOW)
        assert second["status"] == "completed"
        assert second["payout_this_delivery"] == 600
        assert c.status == ContractStatus.COMPLETED
        assert c.partial_fulfilled_amount == 100
        assert c.partial_fulfilled_payout == Decimal("1000")
        assert player.credits == 1000 + 400 + 600  # exactly payment, split across two calls

    def test_rounding_drift_across_many_small_partials_still_sums_exactly(self) -> None:
        """MONEY-PATH GATE TARGET: payment=1000, quantity=3 -- each unit's
        naive 1/3 share rounds HALF_UP to 333 (333.33 -> 333). Three
        deliveries of 1 unit each would sum to only 999 if every delivery
        used the naive pro-rata share -- the completing delivery's exact-
        remainder reconciliation (_compute_bulk_delivery_payout) must
        instead land on the true 1000."""
        db, c, player = self._accepted_setup(quantity=3, payment=Decimal("1000.00"))

        r1 = contract_service.deliver(db, c.id, player.id, 1, now=_NOW)
        assert r1["payout_this_delivery"] == 333
        r2 = contract_service.deliver(db, c.id, player.id, 1, now=_NOW)
        assert r2["payout_this_delivery"] == 333
        r3 = contract_service.deliver(db, c.id, player.id, 1, now=_NOW)
        assert r3["payout_this_delivery"] == 334  # exact remainder, NOT another naive 333
        assert r3["status"] == "completed"

        total_paid = r1["payout_this_delivery"] + r2["payout_this_delivery"] + r3["payout_this_delivery"]
        assert total_paid == 1000  # exactly payment -- never over, never short
        assert c.partial_fulfilled_payout == Decimal("1000")
        assert player.credits == 1000 + 1000

    def test_over_delivery_rejected_no_state_change(self) -> None:
        db, c, player = self._accepted_setup(quantity=100, payment=Decimal("1000.00"))
        with pytest.raises(ContractError, match="exceeds_remaining_quota"):
            contract_service.deliver(db, c.id, player.id, 150, now=_NOW)
        assert c.status == ContractStatus.ACCEPTED
        assert c.partial_fulfilled_amount == 0
        assert player.credits == 1000

    def test_over_delivery_after_a_partial_also_rejected(self) -> None:
        db, c, player = self._accepted_setup(quantity=100, payment=Decimal("1000.00"))
        contract_service.deliver(db, c.id, player.id, 40, now=_NOW)
        with pytest.raises(ContractError, match="exceeds_remaining_quota"):
            contract_service.deliver(db, c.id, player.id, 61, now=_NOW)  # only 60 remain
        assert c.status == ContractStatus.IN_PROGRESS
        assert c.partial_fulfilled_amount == 40  # untouched by the rejected call

    def test_invalid_quantity_rejected(self) -> None:
        db, c, player = self._accepted_setup(quantity=100, payment=Decimal("1000.00"))
        with pytest.raises(ContractError, match="invalid_quantity"):
            contract_service.deliver(db, c.id, player.id, 0, now=_NOW)
        with pytest.raises(ContractError, match="invalid_quantity"):
            contract_service.deliver(db, c.id, player.id, -5, now=_NOW)

    def test_wrong_station_rejected(self) -> None:
        db, c, player = self._accepted_setup(quantity=100, payment=Decimal("1000.00"))
        player.current_port_id = uuid.uuid4()
        with pytest.raises(ContractConflictError, match="wrong_station"):
            contract_service.deliver(db, c.id, player.id, 10, now=_NOW)

    def test_insufficient_cargo_rejected(self) -> None:
        db, c, player = self._accepted_setup(quantity=100, payment=Decimal("1000.00"))
        player.current_ship.cargo["contents"]["ore"] = 5
        with pytest.raises(ContractError, match="insufficient_cargo"):
            contract_service.deliver(db, c.id, player.id, 10, now=_NOW)
        assert c.status == ContractStatus.ACCEPTED  # no state change

    def test_not_your_contract_rejected(self) -> None:
        db, c, player = self._accepted_setup(quantity=100, payment=Decimal("1000.00"))
        stranger = _player(is_docked=True, current_port_id=c.destination_station_id)
        db.players.append(stranger)
        with pytest.raises(ContractError, match="not accepted by you"):
            contract_service.deliver(db, c.id, stranger.id, 10, now=_NOW)

    def test_non_bulk_contract_type_rejected(self) -> None:
        db, c, player = self._accepted_setup(
            quantity=100, payment=Decimal("1000.00"), contract_type=ContractType.CARGO_DELIVERY,
        )
        with pytest.raises(ContractError, match="not_bulk_procurement"):
            contract_service.deliver(db, c.id, player.id, 10, now=_NOW)

    def test_posted_status_rejected(self) -> None:
        db, c, player = self._accepted_setup(quantity=100, payment=Decimal("1000.00"), status=ContractStatus.POSTED)
        with pytest.raises(ContractConflictError, match="stale_status"):
            contract_service.deliver(db, c.id, player.id, 10, now=_NOW)

    def test_completed_status_rejected(self) -> None:
        db, c, player = self._accepted_setup(
            quantity=100, payment=Decimal("1000.00"), status=ContractStatus.COMPLETED,
        )
        with pytest.raises(ContractConflictError, match="stale_status"):
            contract_service.deliver(db, c.id, player.id, 10, now=_NOW)

    def test_cargo_decremented_by_delivered_quantity(self) -> None:
        db, c, player = self._accepted_setup(quantity=100, payment=Decimal("1000.00"))
        contract_service.deliver(db, c.id, player.id, 40, now=_NOW)
        assert player.current_ship.cargo["contents"]["ore"] == 460  # 500 - 40
        assert player.current_ship.cargo["used"] == 460

    def test_player_issued_completion_releases_escrow_state(self) -> None:
        db, c, player = self._accepted_setup(
            quantity=100, payment=Decimal("1000.00"), issuer_type=ContractIssuerType.PLAYER,
            escrow_amount=Decimal("1000.00"), escrow_state=ContractEscrowState.HELD,
        )
        contract_service.deliver(db, c.id, player.id, 100, now=_NOW)
        assert c.escrow_state == ContractEscrowState.RELEASED
        assert c.escrow_amount == Decimal("1000.00")  # untouched -- deliver() never decrements it

    def test_player_issued_partial_leaves_escrow_state_untouched(self) -> None:
        db, c, player = self._accepted_setup(
            quantity=100, payment=Decimal("1000.00"), issuer_type=ContractIssuerType.PLAYER,
            escrow_amount=Decimal("1000.00"), escrow_state=ContractEscrowState.HELD,
        )
        contract_service.deliver(db, c.id, player.id, 40, now=_NOW)
        assert c.escrow_state == ContractEscrowState.HELD  # not released until fully complete


@pytest.mark.unit
class TestWalkAwayBulkProcurement:
    """WO-CONTRACT-3b-BULK -- contract_bulk.py's
    `walk_away_bulk_procurement()`, re-exported as `contract_service.
    walk_away_bulk_procurement`. DISTINCT from `abandon()`: no penalty, no
    issuer-escrow refund, reverts to POSTED (not CANCELLED)."""

    def _setup(self, *, status: ContractStatus = ContractStatus.ACCEPTED, **contract_overrides: Any) -> Any:
        c = _contract(
            status=status, contract_type=ContractType.BULK_PROCUREMENT,
            commodity_type="ore", quantity=100, payment=Decimal("1000.00"), penalty=Decimal("1000.00"),
            partial_fulfilled_amount=0, partial_fulfilled_payout=Decimal("0"),
        )
        for k, v in contract_overrides.items():
            setattr(c, k, v)
        player = _player(credits=1000)
        c.acceptor_player_id = player.id
        db = _FakeSession(contracts=[c], players=[player])
        return db, c, player

    def test_walk_away_from_accepted_zero_partials_reverts_to_posted(self) -> None:
        db, c, player = self._setup()
        result = contract_service.walk_away_bulk_procurement(db, c.id, player.id, now=_NOW)

        assert result["status"] == "posted"
        assert c.status == ContractStatus.POSTED
        assert c.acceptor_player_id is None
        assert c.partial_fulfilled_amount == 0

    def test_walk_away_does_not_clear_accepted_at(self) -> None:
        """`accepted_at` is deliberately preserved (unlike `acceptor_
        player_id`) -- see walk_away_bulk_procurement's own docstring/
        inline note: clearing it would silently zero the insurance-refund
        computation, which needs the pre-transition value."""
        original_accepted_at = _NOW - timedelta(hours=1)
        db, c, player = self._setup(accepted_at=original_accepted_at)
        contract_service.walk_away_bulk_procurement(db, c.id, player.id, now=_NOW)
        assert c.accepted_at == original_accepted_at

    def test_walk_away_from_in_progress_preserves_partial_fulfilled_amount(self) -> None:
        db, c, player = self._setup(
            status=ContractStatus.IN_PROGRESS,
            partial_fulfilled_amount=40, partial_fulfilled_payout=Decimal("400.00"),
        )
        result = contract_service.walk_away_bulk_procurement(db, c.id, player.id, now=_NOW)

        assert result["status"] == "posted"
        assert c.status == ContractStatus.POSTED
        assert c.acceptor_player_id is None
        # ADR-0049 monotonic counter -- untouched by the walk-away.
        assert c.partial_fulfilled_amount == 40
        assert c.partial_fulfilled_payout == Decimal("400.00")
        assert result["partial_fulfilled_amount"] == 40

    def test_walk_away_charges_no_penalty(self) -> None:
        """DISTINCT from abandon(): contract.penalty is never charged."""
        db, c, player = self._setup()
        contract_service.walk_away_bulk_procurement(db, c.id, player.id, now=_NOW)
        assert player.credits == 1000  # unchanged -- no penalty deduction

    def test_non_bulk_contract_type_rejected(self) -> None:
        db, c, player = self._setup(contract_type=ContractType.CARGO_DELIVERY)
        with pytest.raises(ContractError, match="not_bulk_procurement"):
            contract_service.walk_away_bulk_procurement(db, c.id, player.id, now=_NOW)

    def test_not_your_contract_rejected(self) -> None:
        db, c, player = self._setup()
        stranger = _player()
        db.players.append(stranger)
        with pytest.raises(ContractError, match="not accepted by you"):
            contract_service.walk_away_bulk_procurement(db, c.id, stranger.id, now=_NOW)

    def test_posted_status_rejected(self) -> None:
        db, c, player = self._setup(status=ContractStatus.POSTED)
        with pytest.raises(ContractConflictError, match="stale_status"):
            contract_service.walk_away_bulk_procurement(db, c.id, player.id, now=_NOW)

    def test_completed_status_rejected(self) -> None:
        db, c, player = self._setup(status=ContractStatus.COMPLETED)
        with pytest.raises(ContractConflictError, match="stale_status"):
            contract_service.walk_away_bulk_procurement(db, c.id, player.id, now=_NOW)

    def test_insurance_pro_rata_refund_worked_example(self) -> None:
        """Same ADR-0062 E-I2 worked example abandon()'s own test uses
        (accepted_at=T, deadline=T+10h, premium=100, walked at T+4h ->
        refund 54.00 exactly) -- proves walk_away_bulk_procurement() reuses
        the SAME refund helper, independent of the no-penalty/reverts-to-
        posted differences from abandon()."""
        accepted_at = _NOW
        deadline = _NOW + timedelta(hours=10)
        db, c, player = self._setup(
            accepted_at=accepted_at, deadline=deadline,
            insurance_coverage_tier=ContractInsuranceCoverageTier.STANDARD,
            insurance_premium_paid=Decimal("100.00"),
        )
        player.credits = 2000
        walked_at = accepted_at + timedelta(hours=4)

        result = contract_service.walk_away_bulk_procurement(db, c.id, player.id, now=walked_at)

        assert result["insurance_refund"] == 54
        assert player.credits == 2000 + 54  # no penalty deducted, unlike abandon()'s -500+54

    def test_reaccept_after_walkaway_charges_a_fresh_fee(self) -> None:
        """[VERIFY-FIRST FINDING] canon's own worked example (contracts.md
        :184): "Player C accepts, debited a FRESH 10 cr fee (no fee-
        stacking)" -- proves accept() (UNCHANGED by this WO) charges a
        real, non-zero fee on a re-accept after a bulk walk-away, not the
        'fee-once' mechanic originally (incorrectly) proposed."""
        db, c, player = self._setup(payment=Decimal("500.00"), acceptance_fee_pct=Decimal("2.0"))
        contract_service.walk_away_bulk_procurement(db, c.id, player.id, now=_NOW)
        assert c.status == ContractStatus.POSTED

        new_acceptor = _player(credits=1000)
        db.players.append(new_acceptor)
        result = contract_service.accept(db, c.id, new_acceptor.id, now=_NOW)

        assert result["acceptance_fee_charged"] == 10.0  # 2% of 500 -- a REAL, fresh charge
        assert new_acceptor.credits == 990
        assert c.status == ContractStatus.ACCEPTED
        assert c.acceptor_player_id == new_acceptor.id


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
        # WO-CONTRACT-REFACTOR-SPLIT: patch the REAL binding `_guarded_
        # transition` reads from -- it lives in contract_escrow_core.py
        # now, and its own `LEGAL_TRANSITIONS.get(...)` lookup resolves in
        # THAT module's globals, not contract_service's re-exported copy.
        monkeypatch.setattr(contract_escrow_core, "LEGAL_TRANSITIONS", stripped)

        with pytest.raises(ContractConflictError, match="illegal_transition"):
            contract_service.complete(db, c.id, player.id, now=_NOW)
        assert c.status == ContractStatus.ACCEPTED  # untouched -- DB round-trip never happened

    def test_removing_accepted_to_in_progress_edge_409s_a_valid_bulk_delivery(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """WO-CONTRACT-3b-BULK: proves the two new BULK edges added to
        LEGAL_TRANSITIONS are real, consulted data too -- not just the
        pre-existing ones. Status itself is a LEGAL pre-check pass
        (ACCEPTED, deliver()'s own guard clauses never object) -- only the
        stripped table entry makes `_guarded_deliver` itself reject it.

        Patches `contract_bulk.LEGAL_TRANSITIONS`, NOT `contract_escrow_
        core.LEGAL_TRANSITIONS` -- see module-split-monkeypatch-target-trap
        in monk's own memory notes: `_guarded_deliver` (contract_bulk.py,
        WO-3b money-path gate REVISE) reads LEGAL_TRANSITIONS via a VALUE
        import (`from ...contract_escrow_core import LEGAL_TRANSITIONS`),
        which binds an INDEPENDENT name in contract_bulk's own globals --
        patching the source module's attribute never reaches it. `_guarded_
        transition` itself (contract_escrow_core.py) is the one exception
        that reads the name in its OWN module's globals, which is why
        every OTHER test in this class patches contract_escrow_core
        directly."""
        destination_id = uuid.uuid4()
        c = _contract(
            status=ContractStatus.ACCEPTED, destination_station_id=destination_id,
            contract_type=ContractType.BULK_PROCUREMENT,
            commodity_type="ore", quantity=100, payment=Decimal("1000.00"),
            partial_fulfilled_amount=0, partial_fulfilled_payout=Decimal("0"),
        )
        ship = _real_ship(cargo={"capacity": 500, "used": 50, "contents": {"ore": 50}})
        player = _player(credits=1000, is_docked=True, current_port_id=destination_id, current_ship=ship)
        c.acceptor_player_id = player.id
        db = _FakeSession(contracts=[c], players=[player])

        stripped = {
            ContractStatus.POSTED: contract_service.LEGAL_TRANSITIONS[ContractStatus.POSTED],
            ContractStatus.ACCEPTED: frozenset({ContractStatus.CANCELLED}),  # IN_PROGRESS removed
        }
        monkeypatch.setattr(contract_bulk, "LEGAL_TRANSITIONS", stripped)

        with pytest.raises(ContractConflictError, match="illegal_transition"):
            contract_service.deliver(db, c.id, player.id, 40, now=_NOW)  # partial -- would be ACCEPTED -> IN_PROGRESS
        assert c.status == ContractStatus.ACCEPTED  # untouched
        assert c.partial_fulfilled_amount == 0

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

    def test_sweeps_express_and_hazardous_types_identically_to_cargo(self) -> None:
        """WO-CONTRACT-3-NPCGEN-TYPES: this sweep filters purely on
        status/deadline (never reads `contract_type`) -- pin that the two
        new types this WO generates expire exactly like cargo_delivery,
        with zero sweep-side code changes needed."""
        express_past = _contract(
            status=ContractStatus.POSTED, deadline=_NOW - timedelta(minutes=1),
            contract_type=ContractType.EXPRESS_DELIVERY,
        )
        hazardous_past = _contract(
            status=ContractStatus.POSTED, deadline=_NOW - timedelta(minutes=1),
            contract_type=ContractType.HAZARDOUS_TRANSPORT, reputation_penalty=-30,
        )
        db = _FakeSession(contracts=[express_past, hazardous_past])

        result = contract_service.sweep_expired_contracts(db, now=_NOW)

        assert result == {"expired": 2}
        assert express_past.status == ContractStatus.EXPIRED
        assert hazardous_past.status == ContractStatus.EXPIRED


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


# --- WO-CONTRACT-2-DISPUTE-T1 ------------------------------------------- #

@pytest.mark.unit
class TestFileDispute:
    def _expired_contract(self, **overrides: Any) -> SimpleNamespace:
        deadline = overrides.pop("deadline", _NOW - timedelta(hours=4))
        base = dict(
            status=ContractStatus.EXPIRED, deadline=deadline,
            payment=Decimal("1000.00"), acceptance_fee_pct=Decimal("2.0"),
            # WO-CONTRACT-2b-HOLD-ESCROW: escrow_state=HELD is now REQUIRED
            # for `_guarded_file_dispute`'s own guard to succeed regardless
            # of issuer_type -- matches the real column's own server_
            # default (Contract.escrow_state defaults to HELD). escrow_
            # amount defaults to `payment` (the realistic no-insurance-pool
            # held ledger a PLAYER-issued contract would carry) -- NPC-
            # issued rows never read this value at all (`_settle_dispute_
            # escrow`'s NPC branch mints unconditionally), so the default
            # is harmless for them.
            escrow_state=ContractEscrowState.HELD,
            escrow_amount=Decimal("1000.00"),
        )
        base.update(overrides)
        return _contract(**base)

    def test_non_acceptor_rejected(self) -> None:
        c = self._expired_contract()
        acceptor = _player()
        c.acceptor_player_id = acceptor.id
        stranger = _player()
        db = _FakeSession(contracts=[c], players=[acceptor, stranger])
        with pytest.raises(ContractError, match="not accepted by you"):
            contract_service.file_dispute(db, c.id, stranger.id, "I delivered it", now=_NOW)

    def test_only_expired_contracts_disputable(self) -> None:
        c = _contract(status=ContractStatus.ACCEPTED)
        acceptor = _player()
        c.acceptor_player_id = acceptor.id
        db = _FakeSession(contracts=[c], players=[acceptor])
        with pytest.raises(ContractConflictError, match="stale_status"):
            contract_service.file_dispute(db, c.id, acceptor.id, "reason", now=_NOW)

    def test_within_window_at_47h59m59s_is_accepted(self) -> None:
        deadline = _NOW - timedelta(hours=48) + timedelta(seconds=1)
        c = self._expired_contract(deadline=deadline)
        acceptor = _player(credits=5000)
        c.acceptor_player_id = acceptor.id
        db = _FakeSession(contracts=[c], players=[acceptor], stations=[_station()])

        result = contract_service.file_dispute(db, c.id, acceptor.id, "reason", now=_NOW)

        assert result["dispute_filed_at"] == _NOW
        assert c.dispute_filed_at == _NOW

    def test_48h_plus_1s_rejected(self) -> None:
        deadline = _NOW - timedelta(hours=48) - timedelta(seconds=1)
        c = self._expired_contract(deadline=deadline)
        acceptor = _player()
        c.acceptor_player_id = acceptor.id
        db = _FakeSession(contracts=[c], players=[acceptor])
        with pytest.raises(ContractError, match="dispute_window_closed"):
            contract_service.file_dispute(db, c.id, acceptor.id, "reason", now=_NOW)

    def test_double_file_rejected_via_guarded_transition(self) -> None:
        c = self._expired_contract()
        acceptor = _player(credits=5000)
        c.acceptor_player_id = acceptor.id
        db = _FakeSession(contracts=[c], players=[acceptor])

        contract_service.file_dispute(db, c.id, acceptor.id, "first filing", now=_NOW)
        assert c.status == ContractStatus.DISPUTED
        balance_after_first = acceptor.credits

        with pytest.raises(ContractConflictError, match="stale_status"):
            contract_service.file_dispute(db, c.id, acceptor.id, "second filing", now=_NOW)

        # Conservation: the second, raced attempt is a complete no-op.
        assert acceptor.credits == balance_after_first

    def test_evidence_snapshot_folded_into_dispute_notes(self) -> None:
        c = self._expired_contract()
        acceptor = _player()
        c.acceptor_player_id = acceptor.id
        db = _FakeSession(contracts=[c], players=[acceptor])

        contract_service.file_dispute(
            db, c.id, acceptor.id, "cargo was delivered", evidence_snapshot="manifest-url-123", now=_NOW,
        )

        assert "cargo was delivered" in c.dispute_notes
        assert "manifest-url-123" in c.dispute_notes

    # --- Tier-1 case resolution -------------------------------------- #
    #
    # WO-CONTRACT-REFACTOR-SPLIT: the monkeypatch-to-True seam tests below
    # patch `contract_dispute` (not `contract_service`) -- `file_dispute`
    # itself lives in contract_dispute.py now, and its unqualified
    # `_tier1_cargo_manifest_match(...)` / `_tier1_issuer_unilateral_
    # cancellation(...)` calls resolve in THAT module's globals, not
    # contract_service's re-exported copy.

    def test_tier1_destination_unreachable_resolves_and_refunds_fee(self) -> None:
        station = _station(status=StationStatus.ABANDONED)
        c = self._expired_contract(destination_station_id=station.id)
        acceptor = _player(credits=5000)
        c.acceptor_player_id = acceptor.id
        db = _FakeSession(contracts=[c], players=[acceptor], stations=[station])

        result = contract_service.file_dispute(db, c.id, acceptor.id, "station was offline", now=_NOW)

        assert result["tier1_resolution"] == "destination_unreachable"
        assert result["payout"] == 20  # 2% of 1000 acceptance fee, refunded in full
        assert c.status == ContractStatus.CANCELLED
        assert acceptor.credits == 5020
        assert c.escalated_to_admin is False

    def test_tier1_station_present_but_not_abandoned_does_not_resolve(self) -> None:
        station = _station(status=StationStatus.OPERATIONAL)
        c = self._expired_contract(destination_station_id=station.id)
        acceptor = _player(credits=5000)
        c.acceptor_player_id = acceptor.id
        db = _FakeSession(contracts=[c], players=[acceptor], stations=[station])

        result = contract_service.file_dispute(db, c.id, acceptor.id, "reason", now=_NOW)

        assert result["tier1_resolution"] is None
        assert c.status == ContractStatus.DISPUTED  # unresolved, escrow stays frozen
        assert c.escrow_state == ContractEscrowState.DISPUTED
        assert acceptor.credits == 5000  # untouched

    def test_tier1_cargo_manifest_match_seam_is_actually_consulted(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Proves `_tier1_cargo_manifest_match` is a real, wired branch --
        not decorative -- via the SAME monkeypatch-to-True idiom this
        module already uses for `_is_player_blocklisted`."""
        c = self._expired_contract()
        acceptor = _player(credits=5000)
        c.acceptor_player_id = acceptor.id
        db = _FakeSession(contracts=[c], players=[acceptor])
        monkeypatch.setattr(contract_dispute, "_tier1_cargo_manifest_match", lambda contract: True)

        result = contract_service.file_dispute(db, c.id, acceptor.id, "delivered on time", now=_NOW)

        assert result["tier1_resolution"] == "cargo_manifest_match"
        assert result["payout"] == 1000
        assert c.status == ContractStatus.COMPLETED
        assert c.completed_at == _NOW
        assert acceptor.credits == 6000

    def test_tier1_issuer_cancellation_seam_is_actually_consulted(self, monkeypatch: pytest.MonkeyPatch) -> None:
        issuer = _player(credits=5000)
        c = self._expired_contract(issuer_type=ContractIssuerType.PLAYER, issuer_id=issuer.id)
        acceptor = _player(credits=5000)
        c.acceptor_player_id = acceptor.id
        db = _FakeSession(contracts=[c], players=[issuer, acceptor])
        monkeypatch.setattr(contract_dispute, "_tier1_issuer_unilateral_cancellation", lambda contract: True)

        result = contract_service.file_dispute(db, c.id, acceptor.id, "issuer cancelled after accept", now=_NOW)

        # kill_fee = accept_fee_equivalent(20) + cancel_fee(100) = 120,
        # drawn from the held escrow (default 1000) -- remainder (880)
        # returns to the issuer (WO-CONTRACT-2b-HOLD-ESCROW: no wallet
        # debit here, the issuer's wallet was already charged at post
        # time, long before this hand-built EXPIRED fixture's snapshot).
        assert result["tier1_resolution"] == "issuer_cancellation"
        assert result["payout"] == 120
        assert c.status == ContractStatus.CANCELLED
        assert acceptor.credits == 5120
        assert issuer.credits == 5880

    def test_tier1_issuer_cancellation_npc_issued_no_issuer_debit_no_crash(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        c = self._expired_contract()  # default NPC-issued
        acceptor = _player(credits=5000)
        c.acceptor_player_id = acceptor.id
        db = _FakeSession(contracts=[c], players=[acceptor])
        monkeypatch.setattr(contract_dispute, "_tier1_issuer_unilateral_cancellation", lambda contract: True)

        result = contract_service.file_dispute(db, c.id, acceptor.id, "reason", now=_NOW)

        assert result["payout"] == 120
        assert acceptor.credits == 5120  # mints for NPC, same precedent as complete()/abandon()

    def test_tier1_cargo_manifest_match_insufficient_escrow_bounded_never_mints(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """WO-CONTRACT-2-DISPUTE-T1-REVISE (cipher MEDIUM) / WO-CONTRACT-2b-
        HOLD-ESCROW: the bounded-never-mint discipline now applies to the
        HELD escrow ledger, not the issuer's wallet (see `_settle_dispute_
        escrow`'s own docstring, contract_escrow_core.py) -- an under-
        funded escrow caps the payout, never mints the shortfall. The
        issuer's own wallet is untouched: there is nothing left in escrow
        to return once it's fully drawn."""
        issuer = _player(credits=5000)
        c = self._expired_contract(
            issuer_type=ContractIssuerType.PLAYER, issuer_id=issuer.id, escrow_amount=Decimal("15.00"),
        )
        acceptor = _player(credits=5000)
        c.acceptor_player_id = acceptor.id
        db = _FakeSession(contracts=[c], players=[issuer, acceptor])
        monkeypatch.setattr(contract_dispute, "_tier1_cargo_manifest_match", lambda contract: True)

        result = contract_service.file_dispute(db, c.id, acceptor.id, "delivered on time", now=_NOW)

        assert result["payout"] == 15  # NOT the nominal 1000 -- only 15cr was held
        assert issuer.credits == 5000  # untouched
        assert acceptor.credits == 5015

    def test_tier1_issuer_cancellation_insufficient_escrow_bounded_never_mints(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        issuer = _player(credits=5000)
        c = self._expired_contract(
            issuer_type=ContractIssuerType.PLAYER, issuer_id=issuer.id, escrow_amount=Decimal("7.00"),
        )
        acceptor = _player(credits=5000)
        c.acceptor_player_id = acceptor.id
        db = _FakeSession(contracts=[c], players=[issuer, acceptor])
        monkeypatch.setattr(contract_dispute, "_tier1_issuer_unilateral_cancellation", lambda contract: True)

        result = contract_service.file_dispute(db, c.id, acceptor.id, "reason", now=_NOW)

        assert result["payout"] == 7  # NOT the nominal 120 kill-fee -- only 7cr was held
        assert issuer.credits == 5000  # untouched
        assert acceptor.credits == 5007

    def test_dispute_driven_cancellation_computes_insurance_refund(self) -> None:
        """WO-CONTRACT-2-DISPUTE-T1-REVISE (mack LOW (c)): a CANCELLED
        dispute outcome now runs the SAME `_compute_insurance_
        cancellation_refund` idiom abandon()/cancel_player_contract()
        use. By construction this always evaluates to 0 for a dispute
        (see `_apply_dispute_insurance_refund`'s own docstring: elapsed
        >= duration already holds once a contract is EXPIRED) -- proven
        here explicitly rather than just trusted, so a future change to
        that assumption gets caught."""
        station = _station(status=StationStatus.ABANDONED)
        c = self._expired_contract(
            destination_station_id=station.id,
            insurance_coverage_tier=ContractInsuranceCoverageTier.STANDARD,
            insurance_premium_paid=Decimal("50.00"),
        )
        acceptor = _player(credits=5000)
        c.acceptor_player_id = acceptor.id
        db = _FakeSession(contracts=[c], players=[acceptor], stations=[station])

        result = contract_service.file_dispute(db, c.id, acceptor.id, "station offline", now=_NOW)

        assert result["tier1_resolution"] == "destination_unreachable"
        assert result["insurance_refund"] == 0  # always 0 -- proven, not assumed
        assert acceptor.credits == 5020  # only the acceptance-fee payout, no insurance top-up

    # --- unresolvable -> Tier-2 escalation ----------------------------- #

    def test_unresolvable_escalates_via_high_value(self) -> None:
        station = _station(status=StationStatus.OPERATIONAL)
        c = self._expired_contract(destination_station_id=station.id, payment=Decimal("150000.00"))
        acceptor = _player(credits=5000)
        c.acceptor_player_id = acceptor.id
        db = _FakeSession(contracts=[c], players=[acceptor], stations=[station])

        result = contract_service.file_dispute(db, c.id, acceptor.id, "high value dispute", now=_NOW)

        assert result["tier1_resolution"] is None
        assert result["escalated_to_admin"] is True
        assert c.status == ContractStatus.DISPUTED
        assert c.escrow_state == ContractEscrowState.DISPUTED  # stays frozen
        assert acceptor.credits == 5000  # untouched, no payout while unresolved

    def test_unresolvable_low_value_with_station_present_not_escalated(self) -> None:
        """Under $100k, station resolves (exists, not abandoned) -> none
        of the three E-I3 criteria match -- escalated_to_admin stays
        False, but the contract still sits DISPUTED in the general
        (status, dispute_filed_at)-indexed queue regardless."""
        station = _station(status=StationStatus.OPERATIONAL)
        c = self._expired_contract(destination_station_id=station.id, payment=Decimal("1000.00"))
        acceptor = _player(credits=5000)
        c.acceptor_player_id = acceptor.id
        db = _FakeSession(contracts=[c], players=[acceptor], stations=[station])

        result = contract_service.file_dispute(db, c.id, acceptor.id, "reason", now=_NOW)

        assert result["escalated_to_admin"] is False
        assert c.status == ContractStatus.DISPUTED


@pytest.mark.unit
class TestTier1AndEI3HelpersDirect:
    def test_cargo_manifest_match_always_false(self) -> None:
        assert contract_service._tier1_cargo_manifest_match(_contract()) is False

    def test_issuer_unilateral_cancellation_always_false(self) -> None:
        assert contract_service._tier1_issuer_unilateral_cancellation(_contract()) is False

    def test_destination_unreachable_true_only_for_abandoned_station(self) -> None:
        station_id = uuid.uuid4()
        c = _contract(destination_station_id=station_id)
        abandoned = _station(id=station_id, status=StationStatus.ABANDONED)
        db = _FakeSession(stations=[abandoned])
        assert contract_service._tier1_destination_unreachable(db, c) is True

        operational = _station(id=station_id, status=StationStatus.OPERATIONAL)
        db2 = _FakeSession(stations=[operational])
        assert contract_service._tier1_destination_unreachable(db2, c) is False

        db3 = _FakeSession(stations=[])
        assert contract_service._tier1_destination_unreachable(db3, c) is False

    def test_ei3_both_parties_dispute_always_false(self) -> None:
        assert contract_service._ei3_both_parties_dispute(_contract()) is False

    def test_ei3_evidence_trail_incomplete_true_when_station_missing(self) -> None:
        c = _contract(destination_station_id=uuid.uuid4())
        db = _FakeSession(stations=[])
        assert contract_service._ei3_evidence_trail_incomplete(db, c) is True

    def test_ei3_evidence_trail_incomplete_false_when_station_exists(self) -> None:
        station = _station()
        c = _contract(destination_station_id=station.id)
        db = _FakeSession(stations=[station])
        assert contract_service._ei3_evidence_trail_incomplete(db, c) is False

    def test_ei3_high_value_threshold_boundary(self) -> None:
        assert contract_service._ei3_high_value(_contract(payment=Decimal("100000.00"))) is False
        assert contract_service._ei3_high_value(_contract(payment=Decimal("100000.01"))) is True


@pytest.mark.unit
class TestReputationPenaltyPauseGate:
    def test_gate_tracks_disputed_status_only(self) -> None:
        c = _contract(status=ContractStatus.ACCEPTED)
        assert contract_service._is_reputation_penalty_paused(c) is False
        c.status = ContractStatus.EXPIRED
        assert contract_service._is_reputation_penalty_paused(c) is False
        c.status = ContractStatus.DISPUTED
        assert contract_service._is_reputation_penalty_paused(c) is True
        c.status = ContractStatus.CANCELLED
        assert contract_service._is_reputation_penalty_paused(c) is False

    def test_penalty_applies_on_plain_expiry_gate_false_paused_true_after_filing(self) -> None:
        """WO's own Accept criterion, verbatim: 'penalty applies on expiry
        WITHOUT a filing, is PAUSED with one.' The credit-penalty
        sweep_expired_accepted_contracts already applies is unaffected by
        this WO (unchanged, pre-existing behavior) -- proven here
        alongside the NEW pause gate to show both halves of the claim in
        one flow."""
        acceptor = _player(credits=5000)
        c = _contract(
            status=ContractStatus.ACCEPTED, deadline=_NOW - timedelta(hours=1),
            penalty=Decimal("300.00"), payment=Decimal("1000.00"),
            # WO-CONTRACT-2b-HOLD-ESCROW: matches the real column's own
            # default -- the sweep no longer touches escrow_state for an
            # NPC-issued row (never did), so it must already be HELD here
            # for the later file_dispute call's own guard to succeed.
            escrow_state=ContractEscrowState.HELD,
        )
        c.acceptor_player_id = acceptor.id
        db = _FakeSession(contracts=[c], players=[acceptor])

        contract_service.sweep_expired_accepted_contracts(db, now=_NOW)
        assert c.status == ContractStatus.EXPIRED
        assert acceptor.credits == 4700  # credit penalty already applied on expiry
        assert contract_service._is_reputation_penalty_paused(c) is False  # no filing -- NOT paused

        contract_service.file_dispute(db, c.id, acceptor.id, "reason", now=_NOW)
        assert contract_service._is_reputation_penalty_paused(c) is True  # filed -- PAUSED


@pytest.mark.unit
class TestResolveDispute:
    def _disputed_contract(self, **overrides: Any) -> SimpleNamespace:
        base = dict(
            status=ContractStatus.DISPUTED, payment=Decimal("1000.00"),
            acceptance_fee_pct=Decimal("2.0"), dispute_filed_at=_NOW - timedelta(hours=2),
            # WO-CONTRACT-2b-HOLD-ESCROW: `_settle_dispute_escrow` reads
            # `contract.escrow_amount` as the real, held draw source now
            # (never the issuer's wallet) -- default it to `payment` (the
            # realistic no-insurance-pool held ledger), same convention as
            # TestFileDispute's own `_expired_contract` fixture.
            escrow_state=ContractEscrowState.DISPUTED,
            escrow_amount=Decimal("1000.00"),
        )
        base.update(overrides)
        return _contract(**base)

    def test_full_payout_debits_issuer_credits_acceptor(self) -> None:
        issuer = _player(credits=5000)
        c = self._disputed_contract(issuer_type=ContractIssuerType.PLAYER, issuer_id=issuer.id)
        acceptor = _player(credits=5000)
        c.acceptor_player_id = acceptor.id
        db = _FakeSession(contracts=[c], players=[issuer, acceptor])

        result = contract_service.resolve_dispute(
            db, c.id, uuid.uuid4(), ContractDisputeResolution.FULL_PAYOUT, notes="proven delivered", now=_NOW,
        )

        # WO-CONTRACT-2b-HOLD-ESCROW: drawn from the held escrow (default
        # 1000), not the issuer's wallet -- fully drawn, remainder 0, so
        # the issuer's wallet stays untouched (no debit; it was already
        # charged at post time, before this hand-built DISPUTED fixture's
        # snapshot).
        assert result["amount_to_acceptor"] == 1000
        assert c.status == ContractStatus.COMPLETED
        assert c.completed_at == _NOW
        assert acceptor.credits == 6000
        assert issuer.credits == 5000
        assert c.dispute_resolution == ContractDisputeResolution.FULL_PAYOUT
        assert c.dispute_resolved_at == _NOW
        assert c.dispute_notes == "proven delivered"

    def test_full_payout_npc_issued_mints_no_debit(self) -> None:
        c = self._disputed_contract()  # default NPC-issued
        acceptor = _player(credits=5000)
        c.acceptor_player_id = acceptor.id
        db = _FakeSession(contracts=[c], players=[acceptor])

        result = contract_service.resolve_dispute(
            db, c.id, uuid.uuid4(), ContractDisputeResolution.FULL_PAYOUT, now=_NOW,
        )

        assert result["amount_to_acceptor"] == 1000
        assert acceptor.credits == 6000

    def test_partial_payout_pinned_at_zero_delivered(self) -> None:
        c = self._disputed_contract()
        acceptor = _player(credits=5000)
        c.acceptor_player_id = acceptor.id
        db = _FakeSession(contracts=[c], players=[acceptor])

        result = contract_service.resolve_dispute(
            db, c.id, uuid.uuid4(), ContractDisputeResolution.PARTIAL_PAYOUT, now=_NOW,
        )

        assert result["amount_to_acceptor"] == 0
        assert c.status == ContractStatus.CANCELLED
        assert acceptor.credits == 5000

    def test_partial_payout_is_harsher_than_refund_not_equivalent(self) -> None:
        """WO-CONTRACT-2-DISPUTE-T1-REVISE (mack LOW (a)): the OLD
        docstring claimed PARTIAL_PAYOUT nets the same as REFUND for
        cargo_delivery -- FALSE. REFUND explicitly credits the acceptor
        their acceptance fee back (canon names it for REFUND); PARTIAL_
        PAYOUT's own canon bullet never mentions a fee refund at all, so
        at delivered=0 the acceptor collects LESS under PARTIAL_PAYOUT
        (nothing) than under REFUND (the fee) -- proven side-by-side on
        two otherwise-identical contracts."""
        acceptor_partial = _player(credits=5000)
        c_partial = self._disputed_contract()
        c_partial.acceptor_player_id = acceptor_partial.id
        db_partial = _FakeSession(contracts=[c_partial], players=[acceptor_partial])

        acceptor_refund = _player(credits=5000)
        c_refund = self._disputed_contract()
        c_refund.acceptor_player_id = acceptor_refund.id
        db_refund = _FakeSession(contracts=[c_refund], players=[acceptor_refund])

        partial_result = contract_service.resolve_dispute(
            db_partial, c_partial.id, uuid.uuid4(), ContractDisputeResolution.PARTIAL_PAYOUT, now=_NOW,
        )
        refund_result = contract_service.resolve_dispute(
            db_refund, c_refund.id, uuid.uuid4(), ContractDisputeResolution.REFUND, now=_NOW,
        )

        assert partial_result["amount_to_acceptor"] == 0
        assert refund_result["amount_to_acceptor"] == 20
        assert partial_result["amount_to_acceptor"] != refund_result["amount_to_acceptor"]

    def test_refund_credits_acceptance_fee_only(self) -> None:
        c = self._disputed_contract()
        acceptor = _player(credits=5000)
        c.acceptor_player_id = acceptor.id
        db = _FakeSession(contracts=[c], players=[acceptor])

        result = contract_service.resolve_dispute(db, c.id, uuid.uuid4(), ContractDisputeResolution.REFUND, now=_NOW)

        assert result["amount_to_acceptor"] == 20  # 2% of 1000
        assert c.status == ContractStatus.CANCELLED
        assert acceptor.credits == 5020

    def test_penalty_moves_no_credits(self) -> None:
        c = self._disputed_contract()
        acceptor = _player(credits=5000)
        c.acceptor_player_id = acceptor.id
        db = _FakeSession(contracts=[c], players=[acceptor])

        result = contract_service.resolve_dispute(db, c.id, uuid.uuid4(), ContractDisputeResolution.PENALTY, now=_NOW)

        assert result["amount_to_acceptor"] == 0
        assert c.status == ContractStatus.CANCELLED
        assert acceptor.credits == 5000

    def test_split_half_payment_plus_fee_debits_issuer(self) -> None:
        issuer = _player(credits=5000)
        c = self._disputed_contract(issuer_type=ContractIssuerType.PLAYER, issuer_id=issuer.id)
        acceptor = _player(credits=5000)
        c.acceptor_player_id = acceptor.id
        db = _FakeSession(contracts=[c], players=[issuer, acceptor])

        result = contract_service.resolve_dispute(db, c.id, uuid.uuid4(), ContractDisputeResolution.SPLIT, now=_NOW)

        # half_payment=500 drawn from the held escrow (default 1000, WO-
        # CONTRACT-2b-HOLD-ESCROW), fee_refund=20 self-refund -> 520 to
        # acceptor; the escrow's other half (500 remainder) returns to
        # the issuer (a CREDIT here, not a wallet debit).
        assert result["amount_to_acceptor"] == 520
        assert c.status == ContractStatus.CANCELLED
        assert acceptor.credits == 5520
        assert issuer.credits == 5500

    def test_split_insufficient_escrow_half_payment_bounded_fee_refund_unbounded(self) -> None:
        """The half-payment component draws from the HELD escrow (bounded,
        WO-CONTRACT-2b-HOLD-ESCROW); the acceptance-fee-refund component
        is an unconditional acceptor self-refund (same as REFUND's own
        fee) -- proves the two halves of SPLIT's settlement are
        independently bounded/unbounded, not accidentally coupled."""
        issuer = _player(credits=5000)
        c = self._disputed_contract(
            issuer_type=ContractIssuerType.PLAYER, issuer_id=issuer.id,
            escrow_amount=Decimal("8.00"),  # far less than the 500 half-payment
        )
        acceptor = _player(credits=5000)
        c.acceptor_player_id = acceptor.id
        db = _FakeSession(contracts=[c], players=[issuer, acceptor])

        result = contract_service.resolve_dispute(db, c.id, uuid.uuid4(), ContractDisputeResolution.SPLIT, now=_NOW)

        # bounded half-payment (8, not 500 -- the escrow only held 8) +
        # unbounded fee refund (20) = 28.
        assert result["amount_to_acceptor"] == 28
        assert issuer.credits == 5000  # untouched -- escrow fully drawn, nothing left to return
        assert acceptor.credits == 5028

    def test_guard_runs_before_any_credit_mutation(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """WO-CONTRACT-2-DISPUTE-T1-REVISE (mack HIGH): directly proves
        the reordering, not just its consequence -- monkeypatches
        `_guarded_transition` to raise (simulating a lost race) and
        asserts ZERO credits moved, on BOTH players, before the
        exception propagates. Complements `test_double_resolve_rejected_
        conservation` below (which proves the same thing via a REAL
        second call, not a monkeypatch).

        WO-CONTRACT-REFACTOR-SPLIT: patches `contract_dispute` -- `resolve_
        dispute` lives there now and its unqualified `_guarded_transition(
        ...)` call resolves in that module's globals, not contract_
        service's re-exported copy."""
        issuer = _player(credits=5000)
        c = self._disputed_contract(issuer_type=ContractIssuerType.PLAYER, issuer_id=issuer.id)
        acceptor = _player(credits=5000)
        c.acceptor_player_id = acceptor.id
        db = _FakeSession(contracts=[c], players=[issuer, acceptor])

        def _raise_conflict(*args: Any, **kwargs: Any) -> None:
            raise ContractConflictError("stale_status: simulated lost race")

        monkeypatch.setattr(contract_dispute, "_guarded_transition", _raise_conflict)

        with pytest.raises(ContractConflictError, match="stale_status"):
            contract_service.resolve_dispute(db, c.id, uuid.uuid4(), ContractDisputeResolution.FULL_PAYOUT, now=_NOW)

        assert issuer.credits == 5000  # untouched
        assert acceptor.credits == 5000  # untouched

    def test_full_payout_cancelled_never_applies_insurance_refund_completed_status(self) -> None:
        """FULL_PAYOUT resolves to COMPLETED, not CANCELLED -- insurance
        refund logic must NOT fire (contracts.md:62's completion rule),
        even if the contract happens to carry a tier."""
        c = self._disputed_contract(
            insurance_coverage_tier=ContractInsuranceCoverageTier.BASIC, insurance_premium_paid=Decimal("20.00"),
        )
        acceptor = _player(credits=5000)
        c.acceptor_player_id = acceptor.id
        db = _FakeSession(contracts=[c], players=[acceptor])

        result = contract_service.resolve_dispute(
            db, c.id, uuid.uuid4(), ContractDisputeResolution.FULL_PAYOUT, now=_NOW,
        )

        assert result["insurance_refund"] == 0
        assert c.status == ContractStatus.COMPLETED
        assert c.insurance_premium_paid == Decimal("20.00")  # untouched, not refunded

    def test_insufficient_escrow_bounded_never_mints(self) -> None:
        """WO-CONTRACT-2-DISPUTE-T1-REVISE (mack CRITICAL regression) /
        WO-CONTRACT-2b-HOLD-ESCROW: the PRE-fix version of this test
        asserted issuer->0 AND acceptor+=full payment simultaneously -- a
        straight mint (10 in, 1000 out). Fixed behavior: the acceptor
        collects ONLY what the HELD escrow actually holds (`min(escrow_
        amount, nominal)`), never more -- the issuer's own wallet is
        untouched (nothing left in escrow to return)."""
        issuer = _player(credits=5000)
        c = self._disputed_contract(
            issuer_type=ContractIssuerType.PLAYER, issuer_id=issuer.id,
            escrow_amount=Decimal("10.00"),  # far less than the 1000 full_payout would need
        )
        acceptor = _player(credits=5000)
        c.acceptor_player_id = acceptor.id
        db = _FakeSession(contracts=[c], players=[issuer, acceptor])

        result = contract_service.resolve_dispute(
            db, c.id, uuid.uuid4(), ContractDisputeResolution.FULL_PAYOUT, now=_NOW,
        )

        assert issuer.credits == 5000  # untouched -- escrow fully drawn, nothing left to return
        assert acceptor.credits == 5010  # collected ONLY the escrow's actual 10cr -- NOT the full 1000
        assert result["amount_to_acceptor"] == 10  # the ACTUAL transferred amount, not the nominal 1000

    def test_solvent_escrow_still_pays_full_nominal_amount(self) -> None:
        """Sibling to the insufficient-escrow case above -- when the held
        escrow CAN cover it, the bounded draw is indistinguishable from a
        full payout (the bound simply never binds); the escrow's
        remainder (0 here) returns to the issuer, leaving their wallet
        unchanged."""
        issuer = _player(credits=5000)
        c = self._disputed_contract(issuer_type=ContractIssuerType.PLAYER, issuer_id=issuer.id)
        acceptor = _player(credits=5000)
        c.acceptor_player_id = acceptor.id
        db = _FakeSession(contracts=[c], players=[issuer, acceptor])

        result = contract_service.resolve_dispute(
            db, c.id, uuid.uuid4(), ContractDisputeResolution.FULL_PAYOUT, now=_NOW,
        )

        assert issuer.credits == 5000  # untouched -- escrow (default 1000) fully drawn, no remainder
        assert acceptor.credits == 6000
        assert result["amount_to_acceptor"] == 1000

    def test_only_disputed_contracts_resolvable(self) -> None:
        c = _contract(status=ContractStatus.ACCEPTED)
        acceptor = _player()
        c.acceptor_player_id = acceptor.id
        db = _FakeSession(contracts=[c], players=[acceptor])
        with pytest.raises(ContractConflictError, match="stale_status"):
            contract_service.resolve_dispute(db, c.id, uuid.uuid4(), ContractDisputeResolution.REFUND, now=_NOW)

    def test_double_resolve_rejected_conservation(self) -> None:
        c = self._disputed_contract()
        acceptor = _player(credits=5000)
        c.acceptor_player_id = acceptor.id
        db = _FakeSession(contracts=[c], players=[acceptor])

        contract_service.resolve_dispute(db, c.id, uuid.uuid4(), ContractDisputeResolution.REFUND, now=_NOW)
        assert c.status == ContractStatus.CANCELLED
        balance_after_first = acceptor.credits

        with pytest.raises(ContractConflictError, match="stale_status"):
            contract_service.resolve_dispute(db, c.id, uuid.uuid4(), ContractDisputeResolution.REFUND, now=_NOW)

        assert acceptor.credits == balance_after_first  # race loser is a complete no-op

    def test_unknown_outcome_rejected(self) -> None:
        c = self._disputed_contract()
        acceptor = _player()
        c.acceptor_player_id = acceptor.id
        db = _FakeSession(contracts=[c], players=[acceptor])
        with pytest.raises(ContractError, match="unknown_outcome"):
            contract_service.resolve_dispute(db, c.id, uuid.uuid4(), "not_a_real_outcome", now=_NOW)


@pytest.mark.unit
class TestDisputeContractRequestLengthCaps:
    """WO-CONTRACT-2-DISPUTE-T1-REVISE (mack LOW (b)): `reason` (2000) /
    `evidence_snapshot` (500) are capped at the request boundary --
    Pydantic-level, no FastAPI TestClient/DB needed."""

    def test_reason_within_cap_accepted(self) -> None:
        from src.api.routes.contracts import DisputeContractRequest
        req = DisputeContractRequest(reason="x" * 2000)
        assert len(req.reason) == 2000

    def test_reason_over_cap_rejected(self) -> None:
        from pydantic import ValidationError

        from src.api.routes.contracts import DisputeContractRequest
        with pytest.raises(ValidationError):
            DisputeContractRequest(reason="x" * 2001)

    def test_evidence_snapshot_within_cap_accepted(self) -> None:
        from src.api.routes.contracts import DisputeContractRequest
        req = DisputeContractRequest(reason="ok", evidence_snapshot="y" * 500)
        assert len(req.evidence_snapshot) == 500

    def test_evidence_snapshot_over_cap_rejected(self) -> None:
        from pydantic import ValidationError

        from src.api.routes.contracts import DisputeContractRequest
        with pytest.raises(ValidationError):
            DisputeContractRequest(reason="ok", evidence_snapshot="y" * 501)

    def test_empty_reason_still_rejected(self) -> None:
        """Pre-existing min_length=1 guard, unaffected by this REVISE --
        confirms the new max_length Field() didn't accidentally drop it."""
        from pydantic import ValidationError

        from src.api.routes.contracts import DisputeContractRequest
        with pytest.raises(ValidationError):
            DisputeContractRequest(reason="")


@pytest.mark.unit
class TestSerializeContractDisputeNotesPartyGate:
    """WO-CONTRACT-2-DISPUTE-T1-REVISE addendum: dispute_notes is free-text
    reason/evidence, potentially sensitive -- a non-party player must not
    read another player's dispute_notes even though a contract UUID is
    discoverable via the public /board endpoint (GET /{contract_id} has no
    ownership scoping; that broader gap is ticket #37, out of scope here).
    _serialize_contract now includes dispute_notes ONLY when the caller is
    a party (issuer_id / acceptor_player_id)."""

    def _disputed_contract(self) -> SimpleNamespace:
        issuer_id = uuid.uuid4()
        acceptor_id = uuid.uuid4()
        return _contract(
            issuer_id=issuer_id,
            acceptor_player_id=acceptor_id,
            status=ContractStatus.DISPUTED,
            dispute_notes="the cargo never arrived, station logs attached",
        )

    def test_non_party_caller_omits_dispute_notes(self) -> None:
        from src.api.routes.contracts import _serialize_contract
        c = self._disputed_contract()
        stranger_id = uuid.uuid4()
        payload = _serialize_contract(c, stranger_id)
        assert payload["dispute_notes"] is None

    def test_no_caller_id_omits_dispute_notes(self) -> None:
        """Defensive default: an unset caller_player_id must never leak the
        field (fail-closed, not fail-open)."""
        from src.api.routes.contracts import _serialize_contract
        c = self._disputed_contract()
        payload = _serialize_contract(c)
        assert payload["dispute_notes"] is None

    def test_issuer_sees_dispute_notes(self) -> None:
        from src.api.routes.contracts import _serialize_contract
        c = self._disputed_contract()
        payload = _serialize_contract(c, c.issuer_id)
        assert payload["dispute_notes"] == c.dispute_notes

    def test_acceptor_sees_dispute_notes(self) -> None:
        from src.api.routes.contracts import _serialize_contract
        c = self._disputed_contract()
        payload = _serialize_contract(c, c.acceptor_player_id)
        assert payload["dispute_notes"] == c.dispute_notes

    def test_non_sensitive_dispute_fields_stay_visible_to_non_party(self) -> None:
        """The addendum's KEEP list: dispute_resolution / dispute_filed_at /
        dispute_resolved_at / escalated_to_admin are fine to expose to
        anyone -- only dispute_notes is gated."""
        from src.api.routes.contracts import _serialize_contract
        c = self._disputed_contract()
        c.dispute_filed_at = _NOW
        c.escalated_to_admin = True
        stranger_id = uuid.uuid4()
        payload = _serialize_contract(c, stranger_id)
        assert payload["dispute_filed_at"] is not None
        assert payload["escalated_to_admin"] is True

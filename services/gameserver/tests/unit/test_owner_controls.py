"""WO-ECON-OWNER-CONTROLS -- two canon port-ownership owner controls:

1. The 90% treasury-withdrawal cushion cap (port-ownership.md:361-367):
   every withdrawal is capped so a mandatory 10% operating cushion always
   remains in the treasury.
2. The owner-tunable defense/owner fee-distribution rebalance
   (port-ownership.md:224-243): defense in [0.30,0.60], owner in
   [0.10,0.50], operating FIXED at 0.30, and the three buckets must sum to
   1.0.

DB-free, mirrors test_warp_gate_toll.py's _FakeQuery/_FakeSession pattern:
filter()/populate_existing()/with_for_update() are no-ops returning self (the
test controls exactly what's registered per model); commit() is a hard
failure (these are flush-only service functions -- the route owns the
transaction).

Station stand-ins are REAL (transient, unpersisted) Station() ORM instances,
never SimpleNamespace: set_fee_distribution calls flag_modified(station,
"price_modifiers"), which needs a genuine declarative-mapped instance
(_sa_instance_state) -- a bare SimpleNamespace raises AttributeError
(combat-resolver-deterministic-random-pattern / test_first_login_
persistence.py's identical reasoning). The persistence-proof tests reset
committed_state to simulate "freshly loaded from DB" so get_history(...)
.has_changes() detects a REAL subsequent write, not the object's own
construction (test_first_login_persistence.py's _fresh_committed_player).
Player stand-ins stay SimpleNamespace -- credits is a plain scalar column,
never flag_modified.

Acceptance-criteria map:
  Treasury cushion:
    TestTreasuryWithdrawalCap::test_pure_helper_floor_semantics
    TestTreasuryWithdrawalCap::test_exactly_cap_allowed
    TestTreasuryWithdrawalCap::test_cap_plus_one_rejected
    TestTreasuryWithdrawalCap::test_error_names_the_cushion
    TestTreasuryWithdrawalCap::test_odd_balance_floor_boundary
    TestTreasuryWithdrawalCap::test_non_owner_rejected
    TestTreasuryWithdrawalCap::test_non_positive_amount_rejected
  Fee-distribution bounds:
    TestFeeDistributionBounds::test_defense_below_floor_rejected
    TestFeeDistributionBounds::test_defense_above_ceiling_rejected
    TestFeeDistributionBounds::test_owner_below_floor_rejected
    TestFeeDistributionBounds::test_owner_above_ceiling_rejected
    TestFeeDistributionBounds::test_non_summing_combo_rejected
    TestFeeDistributionBounds::test_valid_rebalance_persists
    TestFeeDistributionBounds::test_boundary_values_allowed
    TestFeeDistributionBounds::test_non_owner_rejected
  Realization (split per station, legacy default):
    TestFeeDistributionRealization::test_stored_override_split_ledger_verifiable
    TestFeeDistributionRealization::test_legacy_default_split_byte_identical
  Read-side clamp of hand-edited/out-of-range JSONB:
    TestFeeDistributionReadSideClamp::test_defense_above_ceiling_clamped
    TestFeeDistributionReadSideClamp::test_owner_below_floor_clamped
    TestFeeDistributionReadSideClamp::test_operating_key_in_jsonb_is_ignored
    TestFeeDistributionReadSideClamp::test_non_numeric_value_falls_back_to_default
    TestFeeDistributionReadSideClamp::test_realize_port_revenue_uses_clamped_read
  Persistence (flag_modified survives a fresh-session re-read):
    TestFeeDistributionPersistence::test_write_registers_as_dirty_on_a_fresh_session_baseline
"""
import uuid
from types import SimpleNamespace

import pytest
from sqlalchemy import inspect as sa_inspect
from sqlalchemy.orm.attributes import get_history

from src.models.player import Player
from src.models.station import Station
from src.services import port_ownership_service as po
from src.services.port_ownership_service import PortOwnershipError

# ---------------------------------------------------------------------------
# Fixtures / fakes
# ---------------------------------------------------------------------------

def _fresh_committed_station(
    *,
    owner_id=None,
    treasury_balance=0,
    price_modifiers=None,
    ownership=None,
    tax_rate=0.10,
):
    """A real (transient) Station() ORM instance with committed_state reset
    to simulate 'freshly loaded from DB, nothing dirty yet' -- get_history
    needs this baseline to detect a REAL subsequent change rather than
    trivially reporting the object's own construction as a change."""
    station = Station()
    station.id = uuid.uuid4()
    station.name = "Test Station"
    station.owner_id = owner_id
    station.treasury_balance = treasury_balance
    station.price_modifiers = price_modifiers if price_modifiers is not None else {}
    station.ownership = ownership
    station.tax_rate = tax_rate
    insp = sa_inspect(station)
    insp.committed_state.clear()
    insp._commit_all(insp.dict)
    return station


def _fake_player(**overrides):
    base = dict(id=uuid.uuid4(), credits=100_000)
    base.update(overrides)
    return SimpleNamespace(**base)


class _FakeQuery:
    def __init__(self, result):
        self._result = result

    def filter(self, *a, **k):
        return self

    def populate_existing(self):
        return self

    def with_for_update(self, *a, **k):
        return self

    def first(self):
        return self._result


class _FakeSession:
    """Maps Station/Player to their registered fake row. query() for an
    unregistered model raises -- deliberate, proves a code path never
    touches Player when it shouldn't (e.g. realize_port_revenue never locks
    a player row at all)."""

    def __init__(self, *, station=None, player=None):
        self._station = station
        self._player = player
        self.flush_calls = 0

    def query(self, model):
        if model is Station:
            return _FakeQuery(self._station)
        if model is Player:
            return _FakeQuery(self._player)
        raise AssertionError(f"unexpected query for {model!r}")

    def add(self, obj):
        raise AssertionError("owner-control functions never db.add()")

    def flush(self):
        self.flush_calls += 1

    def commit(self):
        raise AssertionError("service functions are flush-only -- the route commits")

    def rollback(self):
        pass


# ---------------------------------------------------------------------------
# Treasury withdrawal cushion
# ---------------------------------------------------------------------------

class TestTreasuryWithdrawalCap:
    """Canon 'Owner withdrawals' (port-ownership.md:361-367): at most 90% of
    the current balance may leave in one withdrawal -- a mandatory 10%
    operating cushion always remains."""

    def test_pure_helper_floor_semantics(self):
        assert po.treasury_withdrawal_cap(1000) == 900
        assert po.treasury_withdrawal_cap(101) == 90    # floor(90.9) == 90
        assert po.treasury_withdrawal_cap(105) == 94    # floor(94.5) == 94
        assert po.treasury_withdrawal_cap(9) == 8        # floor(8.1) == 8
        assert po.treasury_withdrawal_cap(0) == 0

    def test_exactly_cap_allowed(self):
        owner = _fake_player(credits=0)
        station = _fresh_committed_station(owner_id=owner.id, treasury_balance=1000)
        session = _FakeSession(station=station, player=owner)

        result = po.withdraw_treasury(session, station, owner, 900)

        assert result["withdrawn"] == 900
        assert result["treasury_balance"] == 100
        assert owner.credits == 900
        assert session.flush_calls == 1

    def test_cap_plus_one_rejected(self):
        owner = _fake_player(credits=0)
        station = _fresh_committed_station(owner_id=owner.id, treasury_balance=1000)
        session = _FakeSession(station=station, player=owner)

        with pytest.raises(PortOwnershipError) as exc:
            po.withdraw_treasury(session, station, owner, 901)

        assert exc.value.status_code == 400
        assert station.treasury_balance == 1000   # untouched
        assert owner.credits == 0                  # untouched

    def test_error_names_the_cushion(self):
        owner = _fake_player(credits=0)
        station = _fresh_committed_station(owner_id=owner.id, treasury_balance=1000)
        session = _FakeSession(station=station, player=owner)

        with pytest.raises(PortOwnershipError) as exc:
            po.withdraw_treasury(session, station, owner, 901)

        assert "10%" in exc.value.detail
        assert "900" in exc.value.detail   # names the actual computed cap

    def test_odd_balance_floor_boundary(self):
        # 101-credit balance: cap floors to 90, not rounds up to 91.
        owner = _fake_player(credits=0)
        station = _fresh_committed_station(owner_id=owner.id, treasury_balance=101)
        session = _FakeSession(station=station, player=owner)

        with pytest.raises(PortOwnershipError):
            po.withdraw_treasury(session, station, owner, 91)

        result = po.withdraw_treasury(session, station, owner, 90)
        assert result["withdrawn"] == 90
        assert result["treasury_balance"] == 11

    def test_non_owner_rejected(self):
        owner = _fake_player()
        intruder = _fake_player()
        station = _fresh_committed_station(owner_id=owner.id, treasury_balance=1000)
        session = _FakeSession(station=station, player=intruder)

        with pytest.raises(PortOwnershipError) as exc:
            po.withdraw_treasury(session, station, intruder, 100)
        assert exc.value.status_code == 403

    def test_non_positive_amount_rejected(self):
        owner = _fake_player()
        station = _fresh_committed_station(owner_id=owner.id, treasury_balance=1000)
        session = _FakeSession(station=station, player=owner)

        with pytest.raises(PortOwnershipError):
            po.withdraw_treasury(session, station, owner, 0)
        with pytest.raises(PortOwnershipError):
            po.withdraw_treasury(session, station, owner, -50)


# ---------------------------------------------------------------------------
# Fee-distribution rebalance -- bounds + sum invariant
# ---------------------------------------------------------------------------

class TestFeeDistributionBounds:
    """Canon rebalance bounds (port-ownership.md:228-241): defense in
    [0.30,0.60], owner in [0.10,0.50], operating fixed at 0.30, and the
    three buckets must sum to 1.0."""

    def test_defense_below_floor_rejected(self):
        owner = _fake_player()
        station = _fresh_committed_station(owner_id=owner.id)
        session = _FakeSession(station=station, player=owner)

        with pytest.raises(PortOwnershipError) as exc:
            po.set_fee_distribution(session, station, owner, 0.29, 0.41)

        assert exc.value.status_code == 400
        assert "30%" in exc.value.detail
        assert station.price_modifiers == {}   # untouched

    def test_defense_above_ceiling_rejected(self):
        owner = _fake_player()
        station = _fresh_committed_station(owner_id=owner.id)
        session = _FakeSession(station=station, player=owner)

        with pytest.raises(PortOwnershipError) as exc:
            po.set_fee_distribution(session, station, owner, 0.61, 0.39)

        assert exc.value.status_code == 400
        assert "60%" in exc.value.detail
        assert station.price_modifiers == {}

    def test_owner_below_floor_rejected(self):
        owner = _fake_player()
        station = _fresh_committed_station(owner_id=owner.id)
        session = _FakeSession(station=station, player=owner)

        with pytest.raises(PortOwnershipError) as exc:
            po.set_fee_distribution(session, station, owner, 0.60, 0.09)

        assert exc.value.status_code == 400
        assert "10%" in exc.value.detail
        assert station.price_modifiers == {}

    def test_owner_above_ceiling_rejected(self):
        owner = _fake_player()
        station = _fresh_committed_station(owner_id=owner.id)
        session = _FakeSession(station=station, player=owner)

        with pytest.raises(PortOwnershipError) as exc:
            po.set_fee_distribution(session, station, owner, 0.30, 0.51)

        assert exc.value.status_code == 400
        assert "50%" in exc.value.detail
        assert station.price_modifiers == {}

    def test_non_summing_combo_rejected(self):
        # Both individually in-bounds, but 0.30 + 0.10 + 0.30 = 0.70 != 1.0.
        owner = _fake_player()
        station = _fresh_committed_station(owner_id=owner.id)
        session = _FakeSession(station=station, player=owner)

        with pytest.raises(PortOwnershipError) as exc:
            po.set_fee_distribution(session, station, owner, 0.30, 0.10)

        assert exc.value.status_code == 400
        assert "100%" in exc.value.detail
        assert station.price_modifiers == {}

    def test_valid_rebalance_persists(self):
        owner = _fake_player()
        station = _fresh_committed_station(owner_id=owner.id)
        session = _FakeSession(station=station, player=owner)

        result = po.set_fee_distribution(session, station, owner, 0.50, 0.20)

        assert result["defense_pct"] == pytest.approx(0.50)
        assert result["owner_pct"] == pytest.approx(0.20)
        assert result["operating_pct"] == pytest.approx(0.30)
        assert station.price_modifiers["fee_defense_pct"] == pytest.approx(0.50)
        assert station.price_modifiers["fee_owner_pct"] == pytest.approx(0.20)
        assert session.flush_calls == 1

    def test_boundary_values_allowed(self):
        # Canon bounds are inclusive.
        owner = _fake_player()
        station = _fresh_committed_station(owner_id=owner.id)
        session = _FakeSession(station=station, player=owner)

        result = po.set_fee_distribution(session, station, owner, 0.60, 0.10)

        assert result["defense_pct"] == pytest.approx(0.60)
        assert result["owner_pct"] == pytest.approx(0.10)

        # Defense at ITS floor (0.30): with operating fixed at 0.30, owner
        # must be 0.40 to sum to 1.0 -- owner's own ceiling (0.50) is
        # actually unreachable given the fixed operating share (0.20 would
        # be needed on the defense side, below defense's 0.30 floor).
        station2 = _fresh_committed_station(owner_id=owner.id)
        session2 = _FakeSession(station=station2, player=owner)
        result2 = po.set_fee_distribution(session2, station2, owner, 0.30, 0.40)
        assert result2["defense_pct"] == pytest.approx(0.30)
        assert result2["owner_pct"] == pytest.approx(0.40)

    def test_non_owner_rejected(self):
        owner = _fake_player()
        intruder = _fake_player()
        station = _fresh_committed_station(owner_id=owner.id)
        session = _FakeSession(station=station, player=intruder)

        with pytest.raises(PortOwnershipError) as exc:
            po.set_fee_distribution(session, station, intruder, 0.40, 0.30)
        assert exc.value.status_code == 403
        assert station.price_modifiers == {}


# ---------------------------------------------------------------------------
# Realization -- split_revenue reads the station's effective pcts
# ---------------------------------------------------------------------------

class TestFeeDistributionRealization:
    """realize_port_revenue distributes gross revenue per THIS station's
    effective fee split -- a stored override, or the canon 40/30/30 default
    for a legacy/never-rebalanced station."""

    def test_stored_override_split_ledger_verifiable(self):
        station = _fresh_committed_station(
            owner_id=uuid.uuid4(),
            price_modifiers={"fee_defense_pct": 0.50, "fee_owner_pct": 0.20},
        )
        session = _FakeSession(station=station)   # realize_port_revenue never locks a Player row

        result = po.realize_port_revenue(session, station, 10_000)

        assert result["defense"] == 5_000
        assert result["owner"] == 2_000
        assert result["operating"] == 3_000
        assert result["defense"] + result["owner"] + result["operating"] == 10_000
        assert station.ownership["defense_fund"] == 5_000
        assert station.ownership["operating_fund"] == 3_000
        assert station.treasury_balance == 2_000

    def test_legacy_default_split_byte_identical(self):
        legacy_station = _fresh_committed_station(owner_id=uuid.uuid4())   # no fee keys at all
        session = _FakeSession(station=legacy_station)

        result = po.realize_port_revenue(session, legacy_station, 10_000)

        # Byte-identical to the pre-existing canon 40/30/30 default split.
        assert (result["defense"], result["owner"], result["operating"]) == (4_000, 3_000, 3_000)


# ---------------------------------------------------------------------------
# Read-side clamp -- a hand-edited or legacy out-of-range JSONB value can
# never widen the canon bounds on read.
# ---------------------------------------------------------------------------

class TestFeeDistributionReadSideClamp:
    def test_defense_above_ceiling_clamped(self):
        station = _fresh_committed_station(price_modifiers={"fee_defense_pct": 0.95})
        defense_pct, owner_pct, operating_pct = po._effective_fee_split_pcts(station)
        assert defense_pct == po.FEE_DEFENSE_PCT_MAX
        assert owner_pct == po.OWNER_PCT       # untouched key falls back to default
        assert operating_pct == po.OPERATING_PCT

    def test_owner_below_floor_clamped(self):
        station = _fresh_committed_station(price_modifiers={"fee_owner_pct": 0.01})
        _, owner_pct, _ = po._effective_fee_split_pcts(station)
        assert owner_pct == po.FEE_OWNER_PCT_MIN

    def test_operating_key_in_jsonb_is_ignored(self):
        # A rogue "fee_operating_pct" key is NEVER read -- operating is
        # always the fixed canon 30%, even if something wrote a value there.
        station = _fresh_committed_station(
            price_modifiers={
                "fee_operating_pct": 0.99,
                "fee_defense_pct": 0.40,
                "fee_owner_pct": 0.30,
            }
        )
        defense_pct, owner_pct, operating_pct = po._effective_fee_split_pcts(station)
        assert operating_pct == po.OPERATING_PCT == 0.30
        assert defense_pct == pytest.approx(0.40)
        assert owner_pct == pytest.approx(0.30)

    def test_non_numeric_value_falls_back_to_default(self):
        station = _fresh_committed_station(price_modifiers={"fee_defense_pct": "not-a-number"})
        defense_pct, _, _ = po._effective_fee_split_pcts(station)
        assert defense_pct == po.DEFENSE_PCT

    def test_realize_port_revenue_uses_clamped_read(self):
        # A directly hand-edited out-of-bounds JSONB (bypassing the
        # setter's own validation) still can't push revenue distribution
        # past the canon bounds.
        station = _fresh_committed_station(
            owner_id=uuid.uuid4(),
            price_modifiers={"fee_defense_pct": 0.95, "fee_owner_pct": 0.95},
        )
        session = _FakeSession(station=station)

        result = po.realize_port_revenue(session, station, 10_000)

        # defense clamped to 0.60 -> 6000; operating fixed 0.30 -> 3000;
        # owner takes the remainder (1000), never the hand-edited 9500.
        assert result["defense"] == 6_000
        assert result["operating"] == 3_000
        assert result["owner"] == 1_000


# ---------------------------------------------------------------------------
# Persistence proof -- flag_modified fires on the write
# ---------------------------------------------------------------------------

class TestFeeDistributionPersistence:
    """get_history(...).has_changes() is exactly the signal SQLAlchemy's own
    unit-of-work flush logic consults to decide whether a column belongs in
    the next UPDATE -- proving the write would survive a real flush/commit,
    not just an in-memory dict mutation invisible to the ORM."""

    def test_write_registers_as_dirty_on_a_fresh_session_baseline(self):
        owner = _fake_player()
        station = _fresh_committed_station(owner_id=owner.id, price_modifiers={})
        session = _FakeSession(station=station, player=owner)

        # Baseline: nothing dirty right after the simulated "fresh load".
        assert get_history(station, "price_modifiers").has_changes() is False

        po.set_fee_distribution(session, station, owner, 0.50, 0.20)

        assert get_history(station, "price_modifiers").has_changes() is True

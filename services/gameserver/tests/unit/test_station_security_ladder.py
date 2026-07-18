"""WO-STN-SEC-1 lane 2 -- the owner security-tier lifecycle: acquisition
default, the canon upgrade/downgrade ladder, and the recurring-upkeep skim.

Canon: FEATURES/economy/station-protection.md "Security tiers" /
"Tier upgrade cost" / "Fee distribution". Costs: none->basic 50k/24h,
basic->standard 200k/72h, standard->premium 750k/7d; downgrade free/24h/
one-step; upkeep ~5/10/20% of station revenue by tier.

DB-free, mirrors test_owner_controls.py's _FakeQuery/_FakeSession pattern:
filter()/populate_existing()/with_for_update() are no-ops returning self;
commit() is a hard failure (flush-only service functions -- the route owns
the transaction). Station stand-ins exercising flag_modified are REAL
(transient, unpersisted) Station() ORM instances with committed_state reset
(get_history needs a genuine baseline); Player stand-ins stay SimpleNamespace
-- credits is a plain scalar column, never flag_modified (test_owner_controls.
py's identical convention). Pure-helper tests use lightweight SimpleNamespace
station stand-ins too.

Acceptance-criteria map:
  Ladder state machine (costs / clocks / skip-reject / concurrent-reject /
  insufficient-credits):
    TestUpgradeCosts::test_costs_exact_per_target_tier
    TestUpgradeLadder::test_clock_injectable_and_scale_respected
    TestUpgradeLadder::test_tier_skip_impossible_always_one_rung
    TestUpgradeLadder::test_concurrent_upgrade_while_upgrade_pending_rejected
    TestUpgradeLadder::test_concurrent_upgrade_while_downgrade_pending_rejected
    TestUpgradeLadder::test_already_at_premium_rejected
    TestUpgradeLadder::test_insufficient_credits_zero_mutation
    TestUpgradeLadder::test_non_owner_rejected
    TestDowngradeLadder::test_free_one_step_clock_scaled
    TestDowngradeLadder::test_already_at_none_rejected
    TestDowngradeLadder::test_concurrent_downgrade_while_upgrade_pending_rejected
    TestDowngradeLadder::test_non_owner_rejected
  Acquisition default + no-downgrade regression:
    TestAcquisitionDefault::test_unconfigured_station_defaults_to_basic
    TestAcquisitionDefault::test_already_tiered_station_unchanged_basic
    TestAcquisitionDefault::test_already_tiered_station_unchanged_premium
    TestAcquisitionDefault::test_explicit_none_tier_unchanged
    TestAcquisitionDefault::test_transfer_station_wires_the_default
  Idempotent completion flip:
    TestIdempotentCompletion::test_settle_pending_flips_upgrade_once
    TestIdempotentCompletion::test_second_settle_after_flip_is_noop
    TestIdempotentCompletion::test_settle_pending_flips_downgrade_once
    TestIdempotentCompletion::test_not_yet_due_leaves_pending_untouched
    TestIdempotentCompletion::test_status_read_settles_lazily
  JSONB write hygiene (flag_modified + get_history pins):
    TestPersistence::test_upgrade_registers_as_dirty_on_fresh_session_baseline
    TestPersistence::test_downgrade_registers_as_dirty_on_fresh_session_baseline
  Upkeep realization:
    TestUpkeepRealization::test_pure_helper_pct_and_floor
    TestUpkeepRealization::test_none_tier_byte_identical_to_pre_upkeep_behavior
    TestUpkeepRealization::test_basic_tier_skims_five_percent_from_owner_leg
    TestUpkeepRealization::test_premium_tier_skims_twenty_percent_from_owner_leg
    TestUpkeepRealization::test_upkeep_never_drives_owner_leg_negative
    TestUpkeepRealization::test_unowned_station_no_upkeep_applied
  Route auth conventions:
    TestRouteAuthConventions::test_status_route_requires_auth_deps
    TestRouteAuthConventions::test_upgrade_route_requires_auth_deps
    TestRouteAuthConventions::test_downgrade_route_requires_auth_deps
    TestRouteAuthConventions::test_routes_take_no_target_tier_param
"""
import inspect
import uuid
from datetime import datetime, timedelta, UTC
from types import SimpleNamespace

import pytest
from sqlalchemy import inspect as sa_inspect
from sqlalchemy.orm.attributes import get_history

from src.core import game_time
from src.models.player import Player
from src.models.station import SECURITY_TIER_RANK, Station
from src.services import port_ownership_service as po
from src.services import station_security_service as sts
from src.services.station_security_service import StationSecurityError

FIXED_NOW = datetime(2102, 6, 1, 12, 0, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def real_time_scale(monkeypatch):
    """Default every test to canonical (unscaled) time; individual tests
    override GAME_TIME_SCALE where they need to prove the scaling wire-up."""
    monkeypatch.setattr(game_time, "GAME_TIME_SCALE", 1.0)


# ---------------------------------------------------------------------------
# Fixtures / fakes
# ---------------------------------------------------------------------------

def _fresh_committed_station(*, owner_id=None, security=None, treasury_balance=0,
                              ownership=None, price_modifiers=None):
    """A real (transient) Station() ORM instance with committed_state reset
    to simulate 'freshly loaded from DB, nothing dirty yet' -- get_history
    needs this baseline (test_owner_controls.py's identical pattern)."""
    station = Station()
    station.id = uuid.uuid4()
    station.name = "Test Station"
    station.owner_id = owner_id
    station.security = security
    station.treasury_balance = treasury_balance
    station.ownership = ownership
    station.price_modifiers = price_modifiers if price_modifiers is not None else {}
    station.tax_rate = 0.10
    insp = sa_inspect(station)
    insp.committed_state.clear()
    insp._commit_all(insp.dict)
    return station


def _fake_player(**overrides):
    """SimpleNamespace stand-in -- credits is a plain scalar column, never
    flag_modified (matches test_owner_controls.py's identical _fake_player)."""
    base = dict(id=uuid.uuid4(), credits=1_000_000)
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
    touches Player when it shouldn't (e.g. get_security_status /
    downgrade_security_tier never lock a player row for credits)."""

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
        raise AssertionError("station_security_service functions never db.add()")

    def flush(self):
        self.flush_calls += 1

    def commit(self):
        raise AssertionError("service functions are flush-only -- the route commits")

    def rollback(self):
        pass


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------

class TestPureHelpers:
    def test_tier_order_matches_model_rank_table(self):
        # Single source of truth: the ladder's ordering is DERIVED from
        # src.models.station.SECURITY_TIER_RANK, never a second hand-typed list.
        assert sts.SECURITY_TIER_ORDER == ["none", "basic", "standard", "premium"]
        assert sts.SECURITY_TIER_ORDER == sorted(SECURITY_TIER_RANK, key=SECURITY_TIER_RANK.get)

    def test_next_tier_up(self):
        assert sts.next_tier_up("none") == "basic"
        assert sts.next_tier_up("basic") == "standard"
        assert sts.next_tier_up("standard") == "premium"
        assert sts.next_tier_up("premium") is None

    def test_next_tier_down(self):
        assert sts.next_tier_down("premium") == "standard"
        assert sts.next_tier_down("standard") == "basic"
        assert sts.next_tier_down("basic") == "none"
        assert sts.next_tier_down("none") is None

    def test_unknown_tier_treated_as_none(self):
        assert sts.next_tier_up("bogus") == "basic"
        assert sts.next_tier_down("bogus") is None


# ---------------------------------------------------------------------------
# Acquisition default (+ no-downgrade-on-acquisition regression)
# ---------------------------------------------------------------------------

class TestAcquisitionDefault:
    def test_unconfigured_station_defaults_to_basic(self):
        station = SimpleNamespace(security=None)
        assert sts.apply_acquisition_default(station) is True
        assert station.security == {"tier": "basic"}

    def test_non_dict_security_defaults_to_basic(self):
        # A future seeder bug writing a scalar/list -- same conservative
        # "unconfigured" treatment as security_level's own reader.
        station = SimpleNamespace(security="garbage")
        assert sts.apply_acquisition_default(station) is True
        assert station.security == {"tier": "basic"}

    def test_already_tiered_station_unchanged_basic(self):
        station = SimpleNamespace(security={"tier": "basic", "other": 1})
        assert sts.apply_acquisition_default(station) is False
        assert station.security == {"tier": "basic", "other": 1}

    def test_already_tiered_station_unchanged_premium(self):
        # Acquiring a Premium-tier station (e.g. re-sale) never resets it.
        station = SimpleNamespace(security={"tier": "premium"})
        assert sts.apply_acquisition_default(station) is False
        assert station.security == {"tier": "premium"}

    def test_explicit_none_tier_unchanged(self):
        # A station explicitly configured to "none" is still a DICT -- it
        # is not "unconfigured" and must not be bumped to basic.
        station = SimpleNamespace(security={"tier": "none"})
        assert sts.apply_acquisition_default(station) is False
        assert station.security == {"tier": "none"}

    def test_transfer_station_wires_the_default(self):
        """Integration proof that port_ownership_service._transfer_station
        actually calls the shared helper (not just the unit re-tested in
        isolation)."""
        import inspect as _inspect
        src = _inspect.getsource(po._transfer_station)
        assert "apply_acquisition_default" in src


# ---------------------------------------------------------------------------
# Upgrade ladder
# ---------------------------------------------------------------------------

class TestUpgradeCosts:
    def test_costs_exact_per_target_tier(self):
        assert sts.SECURITY_UPGRADE_COST == {
            "basic": 50_000,
            "standard": 200_000,
            "premium": 750_000,
        }

    def test_hours_exact_per_target_tier(self):
        assert sts.SECURITY_UPGRADE_HOURS == {
            "basic": 24.0,
            "standard": 72.0,
            "premium": 7 * 24.0,
        }
        assert sts.SECURITY_DOWNGRADE_HOURS == 24.0


class TestUpgradeLadder:
    def test_clock_injectable_and_scale_respected(self, monkeypatch):
        monkeypatch.setattr(game_time, "GAME_TIME_SCALE", 12.0)  # 12x dev compression
        owner = _fake_player(credits=1_000_000)
        station = _fresh_committed_station(owner_id=owner.id, security=None)
        session = _FakeSession(station=station, player=owner)

        result = sts.upgrade_security_tier(session, station, owner, now=FIXED_NOW)

        # Canon 24-hour basic-tier construction window, compressed 12x ->
        # completes 2 wall-clock hours after the injected `now`, never the
        # raw unscaled 24.
        assert result["completes_at"] == (FIXED_NOW + timedelta(hours=2)).isoformat()

    def test_upgrade_from_unconfigured_targets_basic(self):
        owner = _fake_player(credits=1_000_000)
        station = _fresh_committed_station(owner_id=owner.id, security=None)
        session = _FakeSession(station=station, player=owner)

        result = sts.upgrade_security_tier(session, station, owner, now=FIXED_NOW)

        assert result["current_tier"] == "none"
        assert result["upgrade_to"] == "basic"
        assert result["cost"] == 50_000
        assert owner.credits == 950_000
        expected_deadline = game_time.scaled_deadline(24.0, start=FIXED_NOW)
        assert result["completes_at"] == expected_deadline.isoformat()
        assert station.security["upgrade_to"] == "basic"
        assert station.security["upgrade_completes_at"] == expected_deadline.isoformat()
        assert station.security["tier"] == "none"   # NOT flipped yet -- only flips on settlement
        assert session.flush_calls == 1

    def test_tier_skip_impossible_always_one_rung(self):
        # Drive the ladder through all three upgrades one at a time,
        # settling each before the next -- never able to jump none->standard.
        owner = _fake_player(credits=2_000_000)
        station = _fresh_committed_station(owner_id=owner.id, security=None)
        session = _FakeSession(station=station, player=owner)

        r1 = sts.upgrade_security_tier(session, station, owner, now=FIXED_NOW)
        assert r1["upgrade_to"] == "basic"
        # Settle the completed upgrade before attempting the next one.
        later = FIXED_NOW + timedelta(hours=25)
        sts.get_security_status(session, station, now=later)
        assert station.security["tier"] == "basic"

        r2 = sts.upgrade_security_tier(session, station, owner, now=later)
        assert r2["current_tier"] == "basic"
        assert r2["upgrade_to"] == "standard"   # never "premium" -- always exactly one rung

    def test_concurrent_upgrade_while_upgrade_pending_rejected(self):
        owner = _fake_player(credits=1_000_000)
        station = _fresh_committed_station(owner_id=owner.id, security=None)
        session = _FakeSession(station=station, player=owner)

        sts.upgrade_security_tier(session, station, owner, now=FIXED_NOW)
        credits_after_first = owner.credits

        with pytest.raises(StationSecurityError) as exc:
            sts.upgrade_security_tier(session, station, owner, now=FIXED_NOW)
        assert exc.value.status_code == 400
        assert owner.credits == credits_after_first   # not double-charged

    def test_concurrent_upgrade_while_downgrade_pending_rejected(self):
        owner = _fake_player(credits=1_000_000)
        station = _fresh_committed_station(
            owner_id=owner.id, security={"tier": "standard"}
        )
        session = _FakeSession(station=station, player=owner)

        sts.downgrade_security_tier(session, station, owner, now=FIXED_NOW)

        with pytest.raises(StationSecurityError) as exc:
            sts.upgrade_security_tier(session, station, owner, now=FIXED_NOW)
        assert exc.value.status_code == 400
        assert station.security.get("upgrade_to") is None   # no upgrade key written

    def test_already_at_premium_rejected(self):
        owner = _fake_player(credits=1_000_000)
        station = _fresh_committed_station(
            owner_id=owner.id, security={"tier": "premium"}
        )
        session = _FakeSession(station=station, player=owner)

        with pytest.raises(StationSecurityError) as exc:
            sts.upgrade_security_tier(session, station, owner, now=FIXED_NOW)
        assert exc.value.status_code == 400
        assert "maximum" in exc.value.detail.lower()

    def test_insufficient_credits_zero_mutation(self):
        owner = _fake_player(credits=100)   # far short of 50,000
        station = _fresh_committed_station(owner_id=owner.id, security=None)
        session = _FakeSession(station=station, player=owner)

        with pytest.raises(StationSecurityError) as exc:
            sts.upgrade_security_tier(session, station, owner, now=FIXED_NOW)

        assert exc.value.status_code == 400
        assert owner.credits == 100                       # zero deduction
        assert station.security is None                   # zero pending key written
        # get_history sees no dirty write either (nothing was ever assigned).
        assert get_history(station, "security").has_changes() is False

    def test_non_owner_rejected(self):
        owner = _fake_player()
        intruder = _fake_player()
        station = _fresh_committed_station(owner_id=owner.id, security=None)
        session = _FakeSession(station=station, player=intruder)

        with pytest.raises(StationSecurityError) as exc:
            sts.upgrade_security_tier(session, station, intruder, now=FIXED_NOW)
        assert exc.value.status_code == 403
        assert station.security is None


# ---------------------------------------------------------------------------
# Downgrade ladder
# ---------------------------------------------------------------------------

class TestDowngradeLadder:
    def test_free_one_step_clock_scaled(self):
        owner = _fake_player(credits=1_000_000)
        station = _fresh_committed_station(
            owner_id=owner.id, security={"tier": "premium"}
        )
        session = _FakeSession(station=station, player=owner)

        result = sts.downgrade_security_tier(session, station, owner, now=FIXED_NOW)

        assert result["current_tier"] == "premium"
        assert result["downgrade_to"] == "standard"   # one rung, never further
        assert result["cost"] == 0
        assert owner.credits == 1_000_000   # untouched -- downgrade is free
        expected_deadline = game_time.scaled_deadline(24.0, start=FIXED_NOW)
        assert result["completes_at"] == expected_deadline.isoformat()
        assert station.security["downgrade_completes_at"] == expected_deadline.isoformat()
        assert station.security["tier"] == "premium"   # not flipped yet

    def test_already_at_none_rejected(self):
        owner = _fake_player()
        station = _fresh_committed_station(owner_id=owner.id, security={"tier": "none"})
        session = _FakeSession(station=station, player=owner)

        with pytest.raises(StationSecurityError) as exc:
            sts.downgrade_security_tier(session, station, owner, now=FIXED_NOW)
        assert exc.value.status_code == 400

    def test_concurrent_downgrade_while_upgrade_pending_rejected(self):
        owner = _fake_player(credits=1_000_000)
        station = _fresh_committed_station(owner_id=owner.id, security=None)
        session = _FakeSession(station=station, player=owner)

        sts.upgrade_security_tier(session, station, owner, now=FIXED_NOW)

        with pytest.raises(StationSecurityError) as exc:
            sts.downgrade_security_tier(session, station, owner, now=FIXED_NOW)
        assert exc.value.status_code == 400
        assert station.security.get("downgrade_completes_at") is None

    def test_non_owner_rejected(self):
        owner = _fake_player()
        intruder = _fake_player()
        station = _fresh_committed_station(
            owner_id=owner.id, security={"tier": "standard"}
        )
        session = _FakeSession(station=station, player=intruder)

        with pytest.raises(StationSecurityError) as exc:
            sts.downgrade_security_tier(session, station, intruder, now=FIXED_NOW)
        assert exc.value.status_code == 403


# ---------------------------------------------------------------------------
# Idempotent lazy completion
# ---------------------------------------------------------------------------

class TestIdempotentCompletion:
    def test_settle_pending_flips_upgrade_once(self):
        station = _fresh_committed_station(
            security={
                "tier": "basic",
                "upgrade_to": "standard",
                "upgrade_completes_at": FIXED_NOW.isoformat(),
            }
        )
        mutated = sts._settle_pending(station, FIXED_NOW + timedelta(seconds=1))
        assert mutated is True
        assert station.security["tier"] == "standard"
        assert station.security["upgrade_to"] is None
        assert station.security["upgrade_completes_at"] is None

    def test_second_settle_after_flip_is_noop(self):
        station = _fresh_committed_station(
            security={
                "tier": "basic",
                "upgrade_to": "standard",
                "upgrade_completes_at": FIXED_NOW.isoformat(),
            }
        )
        later = FIXED_NOW + timedelta(hours=1)
        assert sts._settle_pending(station, later) is True
        # Second call, simulating a "simultaneous" completion read that lost
        # the row-lock race -- must be a true no-op.
        assert sts._settle_pending(station, later) is False
        assert station.security["tier"] == "standard"

    def test_settle_pending_flips_downgrade_once(self):
        station = _fresh_committed_station(
            security={"tier": "standard", "downgrade_completes_at": FIXED_NOW.isoformat()}
        )
        mutated = sts._settle_pending(station, FIXED_NOW + timedelta(seconds=1))
        assert mutated is True
        assert station.security["tier"] == "basic"
        assert station.security["downgrade_completes_at"] is None

    def test_not_yet_due_leaves_pending_untouched(self):
        deadline = FIXED_NOW + timedelta(hours=24)
        station = _fresh_committed_station(
            security={
                "tier": "basic",
                "upgrade_to": "standard",
                "upgrade_completes_at": deadline.isoformat(),
            }
        )
        mutated = sts._settle_pending(station, FIXED_NOW)   # well before the deadline
        assert mutated is False
        assert station.security["tier"] == "basic"
        assert station.security["upgrade_to"] == "standard"

    def test_status_read_settles_lazily(self):
        deadline = FIXED_NOW + timedelta(hours=24)
        station = _fresh_committed_station(
            security={
                "tier": "basic",
                "upgrade_to": "standard",
                "upgrade_completes_at": deadline.isoformat(),
            }
        )
        session = _FakeSession(station=station)

        result = sts.get_security_status(session, station, now=deadline)

        assert result["tier"] == "standard"
        assert result["pending_upgrade_to"] is None
        assert session.flush_calls == 1


# ---------------------------------------------------------------------------
# JSONB write hygiene
# ---------------------------------------------------------------------------

class TestPersistence:
    """get_history(...).has_changes() is the same signal SQLAlchemy's own
    flush logic consults -- proving the write would survive a real
    flush/commit (test_owner_controls.py's identical reasoning)."""

    def test_upgrade_registers_as_dirty_on_fresh_session_baseline(self):
        owner = _fake_player(credits=1_000_000)
        station = _fresh_committed_station(owner_id=owner.id, security=None)
        session = _FakeSession(station=station, player=owner)

        assert get_history(station, "security").has_changes() is False
        sts.upgrade_security_tier(session, station, owner, now=FIXED_NOW)
        assert get_history(station, "security").has_changes() is True

    def test_downgrade_registers_as_dirty_on_fresh_session_baseline(self):
        owner = _fake_player(credits=1_000_000)
        station = _fresh_committed_station(
            owner_id=owner.id, security={"tier": "standard"}
        )
        session = _FakeSession(station=station, player=owner)

        assert get_history(station, "security").has_changes() is False
        sts.downgrade_security_tier(session, station, owner, now=FIXED_NOW)
        assert get_history(station, "security").has_changes() is True


# ---------------------------------------------------------------------------
# Upkeep realization (integration with port_ownership_service.realize_port_revenue)
# ---------------------------------------------------------------------------

class TestUpkeepRealization:
    def test_pure_helper_pct_and_floor(self):
        assert sts.upkeep_pct_for(None) == 0.0
        assert sts.upkeep_pct_for("none") == 0.0
        assert sts.upkeep_pct_for("basic") == pytest.approx(0.05)
        assert sts.upkeep_pct_for("standard") == pytest.approx(0.10)
        assert sts.upkeep_pct_for("premium") == pytest.approx(0.20)
        assert sts.upkeep_for_gross(0, "premium") == 0
        assert sts.upkeep_for_gross(-100, "premium") == 0
        assert sts.upkeep_for_gross(10_000, "premium") == 2_000

    def test_none_tier_byte_identical_to_pre_upkeep_behavior(self):
        station = _fresh_committed_station(owner_id=uuid.uuid4(), security=None)
        session = _FakeSession(station=station)

        result = po.realize_port_revenue(session, station, 10_000)

        # Byte-identical to the canon 40/30/30 default: no upkeep skim.
        assert (result["defense"], result["owner"], result["operating"]) == (4_000, 3_000, 3_000)
        assert result["security_upkeep"] == 0
        assert station.treasury_balance == 3_000

    def test_basic_tier_skims_five_percent_from_owner_leg(self):
        station = _fresh_committed_station(
            owner_id=uuid.uuid4(), security={"tier": "basic"}
        )
        session = _FakeSession(station=station)

        result = po.realize_port_revenue(session, station, 10_000)

        # defense/operating buckets are UNTOUCHED by the upkeep skim.
        assert result["defense"] == 4_000
        assert result["operating"] == 3_000
        # owner leg (3,000) minus 5% of gross (500) = 2,500.
        assert result["security_upkeep"] == 500
        assert result["owner"] == 2_500
        assert station.treasury_balance == 2_500
        assert station.ownership["defense_fund"] == 4_000
        assert station.ownership["operating_fund"] == 3_000
        assert station.security["upkeep_collected"] == 500

    def test_premium_tier_skims_twenty_percent_from_owner_leg(self):
        station = _fresh_committed_station(
            owner_id=uuid.uuid4(), security={"tier": "premium"}
        )
        session = _FakeSession(station=station)

        result = po.realize_port_revenue(session, station, 10_000)

        assert result["security_upkeep"] == 2_000   # 20% of 10,000 gross
        assert result["owner"] == 1_000              # 3,000 owner leg - 2,000 upkeep
        assert station.treasury_balance == 1_000

    def test_upkeep_never_drives_owner_leg_negative(self):
        # A pathological hand-edited fee split could shrink the owner leg
        # below what a naive upkeep-of-gross skim would take; min(owner,...)
        # must floor the skim at the owner leg itself.
        station = _fresh_committed_station(
            owner_id=uuid.uuid4(),
            security={"tier": "premium"},   # 20% of gross upkeep
            price_modifiers={"fee_defense_pct": 0.60, "fee_owner_pct": 0.10},  # owner leg shrunk
        )
        session = _FakeSession(station=station)

        result = po.realize_port_revenue(session, station, 10_000)

        # owner leg before upkeep = 1,000 (10%); 20% of gross would be 2,000,
        # which exceeds the owner leg -- floored to consume ALL of it, never negative.
        assert result["security_upkeep"] == 1_000
        assert result["owner"] == 0
        assert station.treasury_balance == 0

    def test_unowned_station_no_upkeep_applied(self):
        # Unowned stations already fold the "owner" leg into operating
        # (pre-existing behavior) -- the tier read is moot since owner_id is
        # None, but pin that the upkeep hook never fires for them either.
        station = _fresh_committed_station(owner_id=None, security={"tier": "premium"})
        session = _FakeSession(station=station)

        result = po.realize_port_revenue(session, station, 10_000)

        assert result["security_upkeep"] == 0
        assert result.get("owner") == 0
        assert station.treasury_balance == 0


# ---------------------------------------------------------------------------
# Route auth conventions (static introspection -- DB-free, no TestClient)
# ---------------------------------------------------------------------------

class TestRouteAuthConventions:
    """Mirrors routes/port_ownership.py's convention: 401-anon is enforced
    structurally by the get_current_user/get_current_player FastAPI
    dependencies (no valid token -> the dependency itself raises before the
    handler body runs); 403-non-owner is enforced service-side by
    _require_owner. Static signature introspection proves the wiring
    without needing a live DB/TestClient."""

    def _assert_auth_deps(self, fn):
        from src.auth.dependencies import get_current_player, get_current_user
        sig = inspect.signature(fn)
        params = sig.parameters
        assert "current_user" in params
        assert "current_player" in params
        assert params["current_user"].default.dependency is get_current_user
        assert params["current_player"].default.dependency is get_current_player

    def test_status_route_requires_auth_deps(self):
        from src.api.routes import station_security as ss
        self._assert_auth_deps(ss.get_station_security_status)

    def test_upgrade_route_requires_auth_deps(self):
        from src.api.routes import station_security as ss
        self._assert_auth_deps(ss.upgrade_station_security)

    def test_downgrade_route_requires_auth_deps(self):
        from src.api.routes import station_security as ss
        self._assert_auth_deps(ss.downgrade_station_security)

    def test_routes_take_no_target_tier_param(self):
        # The upgrade/downgrade target is ALWAYS the deterministic
        # next/previous rung -- no request body lets a caller pick a tier
        # (which is how the API-level defends the tier-skip-is-impossible
        # invariant, on top of the service-level check).
        from src.api.routes import station_security as ss
        for fn in (ss.upgrade_station_security, ss.downgrade_station_security):
            params = set(inspect.signature(fn).parameters)
            assert params == {"station_id", "db", "current_user", "current_player"}

    def test_router_prefix_and_paths(self):
        from src.api.routes import station_security as ss
        assert ss.router.prefix == "/station-security"
        paths = {(frozenset(r.methods), r.path) for r in ss.router.routes}
        assert (frozenset({"GET"}), "/station-security/stations/{station_id}") in paths
        assert (frozenset({"POST"}), "/station-security/stations/{station_id}/upgrade") in paths
        assert (frozenset({"POST"}), "/station-security/stations/{station_id}/downgrade") in paths

    def test_router_registered_in_api(self):
        import inspect as _inspect
        from src.api import api as api_module
        src = _inspect.getsource(api_module)
        assert "station_security_router" in src

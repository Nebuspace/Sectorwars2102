"""WO-P2-econ-station-tractor-lock -- Guarantee #2, anti-theft tractor beam.

Canon: FEATURES/economy/station-protection.md:77-111 "Anti-theft tractor
beam" + "Tractor strength tiers" (pinned break-roll numbers verbatim per the
Rook canon-audit): Weak 25%/attempt @ 75% engine, ~10 turns; Strong 10%/
attempt @ 90% engine, ~20 turns; Immobilizing 0% -- break attempts ALWAYS
fail. Guarantee #1's sibling threshold (security_rank >= basic) is reused
unchanged (SECURITY_TIER_PROTECTED_MIN_RANK).

DB-free, mirrors test_station_security_ladder.py's _FakeQuery/_FakeSession/
_fresh_committed_station pattern (same repo convention, re-declared per this
codebase's per-test-file fixture style -- see that file's own docstring).
Station stand-ins are REAL (transient) Station() instances with
committed_state reset (flag_modified/get_history needs a genuine baseline);
Player/Ship stand-ins are SimpleNamespace. Cross-service calls that would
need models this FakeSession doesn't map (ShipRegistry, the escape-pod
Ship row) are monkeypatched at their call site -- station_security_service
only ever queries Station/Player itself; everything else is a sibling
service this file does not re-test.

Acceptance-criteria map:
  Pure helpers / pinned canon numbers:
    TestPinnedConstants::*
    TestTractorLockReason::*
  Undock-gate lock creation/freeze (Guarantee #2 predicate):
    TestCheckTractorLock::*
  Undock-route wiring (reject-before-turn-charge, clean-pilot no-op):
    TestUndockRouteWiring::*
  Break-free state machine (pinned %/turns, RNG-mockable via
  station_security_service.random, Immobilizing always-fails):
    TestAttemptBreak::*
  Surrender state machine (fine/rep/abandon/reseat):
    TestSurrender::*
"""
import inspect
import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from sqlalchemy import inspect as sa_inspect

from src.models.player import Player
from src.models.ship import ShipType
from src.models.station import Station
from src.services import station_security_service as sts
from src.services.station_security_service import StationSecurityError

FIXED_NOW = datetime(2102, 6, 1, 12, 0, 0, tzinfo=UTC)


# ---------------------------------------------------------------------------
# Fixtures / fakes (mirrors test_station_security_ladder.py)
# ---------------------------------------------------------------------------

def _fresh_committed_station(*, security=None, name="Test Station"):
    station = Station()
    station.id = uuid.uuid4()
    station.name = name
    station.security = security
    insp = sa_inspect(station)
    insp.committed_state.clear()
    insp._commit_all(insp.dict)
    return station


def _fake_ship(**overrides):
    base = dict(
        id=uuid.uuid4(),
        name="Test Hull",
        type=ShipType.LIGHT_FREIGHTER,
        stolen_status=None,
        cargo={"contents": {}},
        is_abandoned=False,
        abandoned_at=None,
        current_pilot_id=None,
        registered_owner_id=None,
        owner_id=None,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def _fake_player(**overrides):
    base = dict(
        id=uuid.uuid4(),
        credits=10_000,
        personal_reputation=0,
        turns=100,
        lifetime_turns_spent=0,
        current_sector_id=42,
        current_ship=None,
        is_docked=True,
        current_port_id=None,
    )
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
    """Maps Station/Player to their registered fake row -- station_security_
    service itself never queries any other model. query() for an
    unregistered model raises, deliberately: proves a code path never
    touches something it shouldn't."""

    def __init__(self, *, station=None, player=None):
        self._station = station
        self._player = player
        self.flush_calls = 0
        self.added = []

    def query(self, model):
        if model is Station:
            return _FakeQuery(self._station)
        if model is Player:
            return _FakeQuery(self._player)
        raise AssertionError(f"unexpected query for {model!r}")

    def add(self, obj):
        self.added.append(obj)

    def flush(self):
        self.flush_calls += 1

    def commit(self):
        raise AssertionError("service functions are flush-only -- the route commits")

    def rollback(self):
        pass


# ---------------------------------------------------------------------------
# Pinned canon constants
# ---------------------------------------------------------------------------

class TestPinnedConstants:
    def test_strength_by_tier(self):
        assert sts.TRACTOR_STRENGTH_BY_TIER == {
            "basic": "weak",
            "standard": "strong",
            "premium": "immobilizing",
        }

    def test_break_params_exact_pinned_numbers(self):
        # Verbatim per the Rook canon-audit (station-protection.md:107-109).
        assert sts.TRACTOR_BREAK_PARAMS["weak"]["success_chance"] == 0.25
        assert sts.TRACTOR_BREAK_PARAMS["weak"]["engine_pct"] == 75
        assert sts.TRACTOR_BREAK_PARAMS["weak"]["turns"] == 10
        assert sts.TRACTOR_BREAK_PARAMS["strong"]["success_chance"] == 0.10
        assert sts.TRACTOR_BREAK_PARAMS["strong"]["engine_pct"] == 90
        assert sts.TRACTOR_BREAK_PARAMS["strong"]["turns"] == 20
        assert sts.TRACTOR_BREAK_PARAMS["immobilizing"]["success_chance"] == 0.0

    def test_break_attempt_cost_label(self):
        assert sts._break_attempt_cost_label("weak") == "75-pct engine + 10 turns"
        assert sts._break_attempt_cost_label("strong") == "90-pct engine + 20 turns"
        assert sts._break_attempt_cost_label("immobilizing") == "impossible"

    def test_default_wanted_threshold_matches_canon(self):
        assert sts.DEFAULT_WANTED_THRESHOLD == -500


# ---------------------------------------------------------------------------
# Guarantee #2 deny-list predicate (pure)
# ---------------------------------------------------------------------------

class TestTractorLockReason:
    def test_clean_pilot_is_none(self):
        station = SimpleNamespace(security={})
        ship = _fake_ship(stolen_status=False)
        player = _fake_player(personal_reputation=0)
        assert sts.tractor_lock_reason(station, ship, player) is None

    def test_stolen_ship_wins_first(self):
        station = SimpleNamespace(security={"wanted_threshold": -9999})
        ship = _fake_ship(stolen_status=True)
        # Even a squeaky-clean rep score is overridden by stolen_status.
        player = _fake_player(personal_reputation=1000)
        assert sts.tractor_lock_reason(station, ship, player) == "stolen_ship"

    def test_stolen_status_none_reads_as_not_stolen(self):
        # Ship.stolen_status is nullable Boolean; NULL and False both mean
        # not-stolen (P10's own contract).
        station = SimpleNamespace(security={})
        ship = _fake_ship(stolen_status=None)
        player = _fake_player(personal_reputation=0)
        assert sts.tractor_lock_reason(station, ship, player) is None

    def test_wanted_pilot_below_default_threshold(self):
        station = SimpleNamespace(security={})
        ship = _fake_ship(stolen_status=False)
        player = _fake_player(personal_reputation=-501)  # < -500 default
        assert sts.tractor_lock_reason(station, ship, player) == "wanted_pilot"

    def test_wanted_pilot_exactly_at_threshold_is_not_wanted(self):
        # Canon predicate is strict "<", not "<=".
        station = SimpleNamespace(security={})
        ship = _fake_ship(stolen_status=False)
        player = _fake_player(personal_reputation=-500)
        assert sts.tractor_lock_reason(station, ship, player) is None

    def test_wanted_pilot_respects_station_override_threshold(self):
        station = SimpleNamespace(security={"wanted_threshold": -100})
        ship = _fake_ship(stolen_status=False)
        player = _fake_player(personal_reputation=-150)
        assert sts.tractor_lock_reason(station, ship, player) == "wanted_pilot"

    def test_deny_listed_player(self):
        pid = uuid.uuid4()
        station = SimpleNamespace(security={"deny_list_player_ids": [str(pid)]})
        ship = _fake_ship(stolen_status=False)
        player = _fake_player(id=pid, personal_reputation=0)
        assert sts.tractor_lock_reason(station, ship, player) == "deny_listed"

    def test_deny_list_does_not_false_positive_other_players(self):
        station = SimpleNamespace(security={"deny_list_player_ids": [str(uuid.uuid4())]})
        ship = _fake_ship(stolen_status=False)
        player = _fake_player(personal_reputation=0)
        assert sts.tractor_lock_reason(station, ship, player) is None


# ---------------------------------------------------------------------------
# check_tractor_lock -- the undock-time gate
# ---------------------------------------------------------------------------

class TestCheckTractorLock:
    def test_sub_basic_tier_never_locks_even_a_stolen_ship(self):
        station = _fresh_committed_station(security={"tier": "none"})
        db = _FakeSession(station=station)
        ship = _fake_ship(stolen_status=True)
        player = _fake_player(personal_reputation=-9999)
        result = sts.check_tractor_lock(db, station, ship, player, now=FIXED_NOW)
        assert result is None
        assert station.security.get("tractor_locks") is None
        assert db.flush_calls == 0

    def test_clean_pilot_at_basic_tier_is_none_and_untouched(self):
        station = _fresh_committed_station(security={"tier": "basic"})
        db = _FakeSession(station=station)
        ship = _fake_ship(stolen_status=False)
        player = _fake_player(personal_reputation=0)
        result = sts.check_tractor_lock(db, station, ship, player, now=FIXED_NOW)
        assert result is None
        assert db.flush_calls == 0

    def test_stolen_ship_locks_at_basic_weak(self):
        station = _fresh_committed_station(security={"tier": "basic"})
        db = _FakeSession(station=station)
        ship = _fake_ship(stolen_status=True)
        player = _fake_player(personal_reputation=0)
        result = sts.check_tractor_lock(db, station, ship, player, now=FIXED_NOW)
        assert result == {
            "error": "ERR_STATION_TRACTOR_LOCK",
            "station_id": str(station.id),
            "ship_id": str(ship.id),
            "tractor_strength": "weak",
            "reason": "stolen_ship",
            "break_attempt_cost": "75-pct engine + 10 turns",
        }
        locks = station.security["tractor_locks"]
        assert locks[str(ship.id)]["reason"] == "stolen_ship"
        assert locks[str(ship.id)]["break_attempts"] == 0
        assert db.flush_calls == 1

    def test_wanted_pilot_locks_at_standard_strong(self):
        station = _fresh_committed_station(security={"tier": "standard"})
        db = _FakeSession(station=station)
        ship = _fake_ship(stolen_status=False)
        player = _fake_player(personal_reputation=-600)
        result = sts.check_tractor_lock(db, station, ship, player, now=FIXED_NOW)
        assert result["tractor_strength"] == "strong"
        assert result["reason"] == "wanted_pilot"
        assert result["break_attempt_cost"] == "90-pct engine + 20 turns"

    def test_deny_listed_locks_at_premium_immobilizing(self):
        pid = uuid.uuid4()
        station = _fresh_committed_station(
            security={"tier": "premium", "deny_list_player_ids": [str(pid)]}
        )
        db = _FakeSession(station=station)
        ship = _fake_ship(stolen_status=False)
        player = _fake_player(id=pid, personal_reputation=0)
        result = sts.check_tractor_lock(db, station, ship, player, now=FIXED_NOW)
        assert result["tractor_strength"] == "immobilizing"
        assert result["reason"] == "deny_listed"
        assert result["break_attempt_cost"] == "impossible"

    def test_repeat_attempt_freezes_original_reason(self):
        # A lock, once engaged, does not re-derive on a second undock
        # attempt -- clearing stolen_status mid-lock must not silently
        # release the ship (only break/surrender does).
        station = _fresh_committed_station(security={"tier": "basic"})
        db = _FakeSession(station=station)
        ship = _fake_ship(stolen_status=True)
        player = _fake_player(personal_reputation=0)
        first = sts.check_tractor_lock(db, station, ship, player, now=FIXED_NOW)
        assert first["reason"] == "stolen_ship"

        ship.stolen_status = False  # thief "cleans up" the flag mid-lock
        second = sts.check_tractor_lock(db, station, ship, player, now=FIXED_NOW)
        assert second["reason"] == "stolen_ship"  # still frozen, not re-derived

    def test_two_ships_lock_independently_at_the_same_station(self):
        station = _fresh_committed_station(security={"tier": "basic"})
        db = _FakeSession(station=station)
        ship_a = _fake_ship(stolen_status=True)
        ship_b = _fake_ship(stolen_status=True)
        player = _fake_player(personal_reputation=0)
        sts.check_tractor_lock(db, station, ship_a, player, now=FIXED_NOW)
        sts.check_tractor_lock(db, station, ship_b, player, now=FIXED_NOW)
        locks = station.security["tractor_locks"]
        assert str(ship_a.id) in locks
        assert str(ship_b.id) in locks
        assert len(locks) == 2

    def test_get_tractor_lock_status_public_read(self):
        station = _fresh_committed_station(security={"tier": "basic"})
        db = _FakeSession(station=station)
        ship = _fake_ship(stolen_status=True)
        player = _fake_player(personal_reputation=0)
        sts.check_tractor_lock(db, station, ship, player, now=FIXED_NOW)

        status = sts.get_tractor_lock_status(station, ship)
        assert status["locked"] is True
        assert status["reason"] == "stolen_ship"
        assert status["tractor_strength"] == "weak"
        assert status["break_attempts"] == 0

        clean_ship = _fake_ship()
        assert sts.get_tractor_lock_status(station, clean_ship) == {"locked": False}


# ---------------------------------------------------------------------------
# Undock-route wiring (structural proof -- reject before turn charge)
# ---------------------------------------------------------------------------

class TestUndockRouteWiring:
    def test_undock_route_calls_check_tractor_lock_before_turn_check(self):
        from src.api.routes import trading
        src = inspect.getsource(trading.undock_from_port)
        lock_idx = src.index("check_tractor_lock")
        turn_check_idx = src.index("UNDOCKING_TURN_COST")
        # The FIRST mention of UNDOCKING_TURN_COST is the local-constant
        # definition itself; the tractor check must land before the second
        # mention (the actual affordability `if` guard).
        second_turn_idx = src.index("UNDOCKING_TURN_COST", turn_check_idx + 1)
        assert lock_idx < second_turn_idx

    def test_undock_route_rejects_with_403(self):
        from src.api.routes import trading
        src = inspect.getsource(trading.undock_from_port)
        assert "status_code=403" in src
        assert "check_tractor_lock" in src


# ---------------------------------------------------------------------------
# attempt_tractor_break -- break-free state machine
# ---------------------------------------------------------------------------

class TestAttemptBreak:
    def _locked_station_and_ship(self, tier, reason="stolen_ship"):
        station = _fresh_committed_station(security={"tier": tier})
        ship = _fake_ship(stolen_status=(reason == "stolen_ship"))
        station.security["tractor_locks"] = {
            str(ship.id): {"reason": reason, "locked_at": FIXED_NOW.isoformat(), "break_attempts": 0}
        }
        return station, ship

    def _patch_side_effects(self, monkeypatch):
        monkeypatch.setattr(
            "src.services.turn_service.regenerate_turns", lambda db, player: None
        )
        monkeypatch.setattr(
            "src.services.docking_service.release", lambda db, station, player: False
        )
        monkeypatch.setattr(
            "src.services.haggle_service.clear_docking_session_haggles", lambda player: None
        )

    def test_no_current_ship_raises(self, monkeypatch):
        self._patch_side_effects(monkeypatch)
        station, _ = self._locked_station_and_ship("basic")
        db = _FakeSession(station=station)
        player = _fake_player(current_ship=None)
        with pytest.raises(StationSecurityError) as exc:
            sts.attempt_tractor_break(db, station, player, now=FIXED_NOW)
        assert exc.value.status_code == 400

    def test_no_active_lock_raises(self, monkeypatch):
        self._patch_side_effects(monkeypatch)
        station = _fresh_committed_station(security={"tier": "basic"})
        ship = _fake_ship()
        player = _fake_player(current_ship=ship)
        db = _FakeSession(station=station, player=player)
        with pytest.raises(StationSecurityError) as exc:
            sts.attempt_tractor_break(db, station, player, now=FIXED_NOW)
        assert exc.value.status_code == 400
        assert "not tractor-locked" in exc.value.detail

    def test_insufficient_turns_raises_and_spends_nothing(self, monkeypatch):
        self._patch_side_effects(monkeypatch)
        station, ship = self._locked_station_and_ship("basic")
        player = _fake_player(current_ship=ship, turns=3)  # weak needs 10
        db = _FakeSession(station=station, player=player)
        with pytest.raises(StationSecurityError) as exc:
            sts.attempt_tractor_break(db, station, player, now=FIXED_NOW)
        assert exc.value.status_code == 400
        assert player.turns == 3  # untouched

    def test_weak_tier_success_below_25_pct_escapes_and_clears_lock(self, monkeypatch):
        self._patch_side_effects(monkeypatch)
        monkeypatch.setattr("src.services.station_security_service.random.random", lambda: 0.24)
        station, ship = self._locked_station_and_ship("basic")
        player = _fake_player(current_ship=ship, turns=50, is_docked=True, current_port_id=station.id)
        db = _FakeSession(station=station, player=player)

        result = sts.attempt_tractor_break(db, station, player, now=FIXED_NOW)

        assert result["success"] is True
        assert result["outcome"] == "escaped"
        assert result["tractor_strength"] == "weak"
        assert result["turns_spent"] == 10
        assert player.turns == 40  # 50 - 10, spent regardless
        assert player.is_docked is False
        assert player.current_port_id is None
        assert str(ship.id) not in (station.security.get("tractor_locks") or {})

    def test_weak_tier_failure_at_25_pct_boundary_stays_locked(self, monkeypatch):
        self._patch_side_effects(monkeypatch)
        # random() == success_chance is NOT a success (strict "<").
        monkeypatch.setattr("src.services.station_security_service.random.random", lambda: 0.25)
        station, ship = self._locked_station_and_ship("basic")
        player = _fake_player(current_ship=ship, turns=50, is_docked=True, current_port_id=station.id)
        db = _FakeSession(station=station, player=player)

        result = sts.attempt_tractor_break(db, station, player, now=FIXED_NOW)

        assert result["success"] is False
        assert result["outcome"] == "still_locked"
        assert result["break_attempts"] == 1
        assert player.turns == 40  # turns cost regardless of outcome
        assert player.is_docked is True  # never undocked
        assert str(ship.id) in station.security["tractor_locks"]
        assert station.security["tractor_locks"][str(ship.id)]["break_attempts"] == 1

    def test_strong_tier_success_below_10_pct_escapes(self, monkeypatch):
        self._patch_side_effects(monkeypatch)
        monkeypatch.setattr("src.services.station_security_service.random.random", lambda: 0.09)
        station, ship = self._locked_station_and_ship("standard")
        player = _fake_player(current_ship=ship, turns=50)
        db = _FakeSession(station=station, player=player)

        result = sts.attempt_tractor_break(db, station, player, now=FIXED_NOW)

        assert result["success"] is True
        assert result["tractor_strength"] == "strong"
        assert result["turns_spent"] == 20
        assert player.turns == 30

    def test_strong_tier_failure_at_10_pct_boundary_stays_locked(self, monkeypatch):
        self._patch_side_effects(monkeypatch)
        monkeypatch.setattr("src.services.station_security_service.random.random", lambda: 0.10)
        station, ship = self._locked_station_and_ship("standard")
        player = _fake_player(current_ship=ship, turns=50)
        db = _FakeSession(station=station, player=player)

        result = sts.attempt_tractor_break(db, station, player, now=FIXED_NOW)

        assert result["success"] is False
        assert result["turns_spent"] == 20
        assert player.turns == 30

    def test_immobilizing_always_fails_even_at_roll_zero(self, monkeypatch):
        self._patch_side_effects(monkeypatch)
        # The most favorable possible roll for the pilot -- still fails.
        monkeypatch.setattr("src.services.station_security_service.random.random", lambda: 0.0)
        station, ship = self._locked_station_and_ship("premium")
        player = _fake_player(current_ship=ship, turns=50)
        db = _FakeSession(station=station, player=player)

        result = sts.attempt_tractor_break(db, station, player, now=FIXED_NOW)

        assert result["success"] is False
        assert result["outcome"] == "still_locked"
        assert result["tractor_strength"] == "immobilizing"
        # Failed breaks cost the turns regardless (canon).
        assert result["turns_spent"] == 20
        assert player.turns == 30

    def test_immobilizing_never_succeeds_across_many_rolls(self, monkeypatch):
        self._patch_side_effects(monkeypatch)
        station, ship = self._locked_station_and_ship("premium")
        for roll in (0.0, 0.001, 0.3, 0.5, 0.999):
            monkeypatch.setattr(
                "src.services.station_security_service.random.random", lambda r=roll: r
            )
            station.security["tractor_locks"][str(ship.id)] = {
                "reason": "stolen_ship", "locked_at": FIXED_NOW.isoformat(), "break_attempts": 0
            }
            player = _fake_player(current_ship=ship, turns=50)
            db = _FakeSession(station=station, player=player)
            result = sts.attempt_tractor_break(db, station, player, now=FIXED_NOW)
            assert result["success"] is False


# ---------------------------------------------------------------------------
# surrender_tractor_locked_ship
# ---------------------------------------------------------------------------

class TestSurrender:
    def _patch_side_effects(self, monkeypatch, escape_pod):
        monkeypatch.setattr(
            "src.services.ship_service.ShipService._ensure_escape_pod",
            lambda self, player, sector_id: escape_pod,
        )
        registry_spy = Mock()
        monkeypatch.setattr(
            "src.services.ship_registry_service.append_registry_event", registry_spy
        )
        return registry_spy

    def _locked_station_and_ship(self, cargo_contents=None):
        station = _fresh_committed_station(security={"tier": "basic"})
        ship = _fake_ship(
            stolen_status=True,
            cargo={"contents": cargo_contents or {}},
        )
        station.security["tractor_locks"] = {
            str(ship.id): {"reason": "stolen_ship", "locked_at": FIXED_NOW.isoformat(), "break_attempts": 0}
        }
        return station, ship

    def test_no_current_ship_raises(self, monkeypatch):
        escape_pod = _fake_ship(type=ShipType.ESCAPE_POD)
        self._patch_side_effects(monkeypatch, escape_pod)
        station = _fresh_committed_station(security={"tier": "basic"})
        player = _fake_player(current_ship=None)
        db = _FakeSession(station=station, player=player)
        with pytest.raises(StationSecurityError) as exc:
            sts.surrender_tractor_locked_ship(db, station, player, now=FIXED_NOW)
        assert exc.value.status_code == 400

    def test_no_active_lock_raises(self, monkeypatch):
        escape_pod = _fake_ship(type=ShipType.ESCAPE_POD)
        self._patch_side_effects(monkeypatch, escape_pod)
        station = _fresh_committed_station(security={"tier": "basic"})
        ship = _fake_ship()
        player = _fake_player(current_ship=ship)
        db = _FakeSession(station=station, player=player)
        with pytest.raises(StationSecurityError) as exc:
            sts.surrender_tractor_locked_ship(db, station, player, now=FIXED_NOW)
        assert exc.value.status_code == 400
        assert "not tractor-locked" in exc.value.detail

    def test_escape_pod_cannot_be_surrendered(self, monkeypatch):
        escape_pod = _fake_ship(type=ShipType.ESCAPE_POD)
        self._patch_side_effects(monkeypatch, escape_pod)
        station = _fresh_committed_station(security={"tier": "basic"})
        # The player's CURRENT ship is itself already an Escape Pod, locked.
        piloted_pod = _fake_ship(type=ShipType.ESCAPE_POD, stolen_status=True)
        station.security["tractor_locks"] = {
            str(piloted_pod.id): {
                "reason": "stolen_ship", "locked_at": FIXED_NOW.isoformat(), "break_attempts": 0,
            }
        }
        player = _fake_player(current_ship=piloted_pod)
        db = _FakeSession(station=station, player=player)
        with pytest.raises(StationSecurityError) as exc:
            sts.surrender_tractor_locked_ship(db, station, player, now=FIXED_NOW)
        assert exc.value.status_code == 400

    def test_surrender_fines_15_pct_of_real_cargo_value(self, monkeypatch):
        from src.core.commodity_economy import base_price
        escape_pod = _fake_ship(type=ShipType.ESCAPE_POD)
        self._patch_side_effects(monkeypatch, escape_pod)
        station, ship = self._locked_station_and_ship(cargo_contents={"ore": 100})
        player = _fake_player(current_ship=ship, credits=10_000)
        db = _FakeSession(station=station, player=player)

        expected_cargo_value = 100 * base_price("ore")
        expected_fine = int(expected_cargo_value * sts.SURRENDER_FINE_PCT)

        result = sts.surrender_tractor_locked_ship(db, station, player, now=FIXED_NOW)

        assert result["fine"] == expected_fine
        assert player.credits == 10_000 - expected_fine

    def test_surrender_fine_never_drives_credits_negative(self, monkeypatch):
        escape_pod = _fake_ship(type=ShipType.ESCAPE_POD)
        self._patch_side_effects(monkeypatch, escape_pod)
        station, ship = self._locked_station_and_ship(cargo_contents={"ore": 100_000})
        player = _fake_player(current_ship=ship, credits=10)
        db = _FakeSession(station=station, player=player)

        result = sts.surrender_tractor_locked_ship(db, station, player, now=FIXED_NOW)

        assert player.credits == 0
        assert result["fine"] > 10

    def test_surrender_applies_reputation_penalty(self, monkeypatch):
        escape_pod = _fake_ship(type=ShipType.ESCAPE_POD)
        self._patch_side_effects(monkeypatch, escape_pod)
        station, ship = self._locked_station_and_ship()
        player = _fake_player(current_ship=ship, personal_reputation=50)
        db = _FakeSession(station=station, player=player)

        result = sts.surrender_tractor_locked_ship(db, station, player, now=FIXED_NOW)

        assert result["reputation_penalty"] == sts.SURRENDER_REPUTATION_PENALTY
        assert player.personal_reputation == 50 + sts.SURRENDER_REPUTATION_PENALTY

    def test_surrender_marks_ship_abandoned_not_destroyed(self, monkeypatch):
        escape_pod = _fake_ship(type=ShipType.ESCAPE_POD)
        self._patch_side_effects(monkeypatch, escape_pod)
        station, ship = self._locked_station_and_ship()
        player = _fake_player(current_ship=ship)
        db = _FakeSession(station=station, player=player)

        sts.surrender_tractor_locked_ship(db, station, player, now=FIXED_NOW)

        assert ship.is_abandoned is True
        assert ship.abandoned_at == FIXED_NOW
        assert ship.current_pilot_id is None
        assert not hasattr(ship, "is_destroyed") or ship.is_destroyed is False

    def test_surrender_clears_the_lock(self, monkeypatch):
        escape_pod = _fake_ship(type=ShipType.ESCAPE_POD)
        self._patch_side_effects(monkeypatch, escape_pod)
        station, ship = self._locked_station_and_ship()
        player = _fake_player(current_ship=ship)
        db = _FakeSession(station=station, player=player)

        sts.surrender_tractor_locked_ship(db, station, player, now=FIXED_NOW)

        assert str(ship.id) not in station.security["tractor_locks"]

    def test_surrender_reseats_pilot_into_escape_pod(self, monkeypatch):
        escape_pod = _fake_ship(type=ShipType.ESCAPE_POD)
        self._patch_side_effects(monkeypatch, escape_pod)
        station, ship = self._locked_station_and_ship()
        player = _fake_player(current_ship=ship)
        db = _FakeSession(station=station, player=player)

        result = sts.surrender_tractor_locked_ship(db, station, player, now=FIXED_NOW)

        assert player.current_ship_id == escape_pod.id
        assert result["new_ship_id"] == str(escape_pod.id)

    def test_surrender_logs_impounded_registry_event(self, monkeypatch):
        escape_pod = _fake_ship(type=ShipType.ESCAPE_POD)
        registry_spy = self._patch_side_effects(monkeypatch, escape_pod)
        station, ship = self._locked_station_and_ship()
        player = _fake_player(current_ship=ship)
        db = _FakeSession(station=station, player=player)

        sts.surrender_tractor_locked_ship(db, station, player, now=FIXED_NOW)

        assert registry_spy.call_count == 1
        _, kwargs = registry_spy.call_args
        from src.models.ship_registry import RegistryEventType
        assert kwargs["event_type"] == RegistryEventType.IMPOUNDED
        assert kwargs["ship"] is ship

    def test_surrender_tolerates_registry_event_failure(self, monkeypatch):
        escape_pod = _fake_ship(type=ShipType.ESCAPE_POD)
        monkeypatch.setattr(
            "src.services.ship_service.ShipService._ensure_escape_pod",
            lambda self, player, sector_id: escape_pod,
        )
        monkeypatch.setattr(
            "src.services.ship_registry_service.append_registry_event",
            Mock(side_effect=RuntimeError("boom")),
        )
        station, ship = self._locked_station_and_ship()
        player = _fake_player(current_ship=ship)
        db = _FakeSession(station=station, player=player)

        # Must not raise -- the registry event is best-effort.
        result = sts.surrender_tractor_locked_ship(db, station, player, now=FIXED_NOW)
        assert result["success"] is True

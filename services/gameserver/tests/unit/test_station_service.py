"""Unit tests — station_service.py (station destruction & 24h recovery cycle).

No test file existed for this service. DB-free: mark_station_destroyed /
recover_station only mutate the station row and call db.flush() (no
queries), so a trivial no-op _FakeDb suffices. A real (unattached) Station
model instance is used throughout because every mutation path calls
flag_modified() on a JSONB column (ownership/commodities/defenses), which
requires a real mapped instance. game_time.scaled_deadline /
canonical_hours_since are the REAL functions (pure, GAME_TIME_SCALE-aware,
read once at import time) -- expected values are computed by calling them
directly rather than hardcoding wall-clock arithmetic, so these tests hold
regardless of the process's configured GAME_TIME_SCALE.

Sections:
  TestIsStationFunctional — the single is_destroyed-gated predicate.
  TestMarkStationDestroyed — idempotent already-destroyed short-circuit
    (no re-snapshot), the commodity-quantity snapshot (dict-shaped entries
    only), the recovery_time deadline via the real scaled_deadline, and
    that stored cargo/drones/credits are left untouched.
  TestIsRecoveryDue — the canonical-hours-since-anchor primary check, the
    corrupt/missing-anchor fallback to the recovery_time deadline, and the
    naive-datetime coercion on that fallback path.
  TestRecoverStation — not-destroyed no-op, the 50%-of-snapshot rebuild
    clamped to capacity, the added-after-destruction commodity falling
    back to live quantity, the defense-pool zeroing (drones/patrol reset
    to 0, toggles reset to False), and snapshot-key removal while other
    ownership buckets are preserved.
"""
from datetime import UTC, datetime, timedelta

from src.core import game_time
from src.models.station import Station, StationStatus
from src.services.station_service import (
    STATION_RECOVERY_HOURS,
    is_recovery_due,
    is_station_functional,
    mark_station_destroyed,
    recover_station,
)


class _FakeDb:
    def flush(self):
        pass


def _station(**kwargs):
    s = Station()
    s.id = kwargs.pop("id", "station-1")
    s.name = kwargs.pop("name", "Trade Post Alpha")
    s.is_destroyed = kwargs.pop("is_destroyed", False)
    s.status = kwargs.pop("status", StationStatus.OPERATIONAL)
    s.recovery_time = kwargs.pop("recovery_time", None)
    s.ownership = kwargs.pop("ownership", None)
    s.commodities = kwargs.pop("commodities", {})
    s.defenses = kwargs.pop("defenses", {})
    s.last_attacked = kwargs.pop("last_attacked", None)
    for k, v in kwargs.items():
        setattr(s, k, v)
    return s


# ---------------------------------------------------------------------------
# is_station_functional
# ---------------------------------------------------------------------------


class TestIsStationFunctional:
    def test_operational_station_is_functional(self):
        assert is_station_functional(_station(is_destroyed=False)) is True

    def test_destroyed_station_is_not_functional(self):
        assert is_station_functional(_station(is_destroyed=True)) is False


# ---------------------------------------------------------------------------
# mark_station_destroyed
# ---------------------------------------------------------------------------


class TestMarkStationDestroyed:
    def test_already_destroyed_short_circuits_without_resnapshotting(self):
        original_snapshot = {"ore": 999}
        s = _station(
            is_destroyed=True,
            recovery_time=datetime(2026, 1, 1, tzinfo=UTC),
            ownership={"destroyed_inventory": original_snapshot},
            commodities={"ore": {"quantity": 1}},
        )
        result = mark_station_destroyed(_FakeDb(), s)
        assert result["status"] == "already_destroyed"
        assert s.ownership["destroyed_inventory"] == original_snapshot

    def test_fresh_destruction_sets_flags_and_deadline(self):
        now = datetime(2026, 8, 9, 12, 0, 0, tzinfo=UTC)
        s = _station(commodities={"ore": {"quantity": 40, "capacity": 100}})
        result = mark_station_destroyed(_FakeDb(), s, now=now)

        assert s.is_destroyed is True
        assert s.status == StationStatus.ABANDONED
        assert s.last_attacked == now
        assert s.recovery_time == game_time.scaled_deadline(STATION_RECOVERY_HOURS, now)
        assert result["status"] == "destroyed"

    def test_snapshots_only_dict_shaped_commodity_entries(self):
        now = datetime(2026, 8, 9, tzinfo=UTC)
        s = _station(
            commodities={
                "ore": {"quantity": 40},
                "garbage": "not-a-dict",
                "organics": {"quantity": 10},
            }
        )
        mark_station_destroyed(_FakeDb(), s, now=now)
        snapshot = s.ownership["destroyed_inventory"]
        assert snapshot == {"ore": 40, "organics": 10}

    def test_missing_commodities_snapshots_empty_without_crashing(self):
        now = datetime(2026, 8, 9, tzinfo=UTC)
        s = _station(commodities=None)
        result = mark_station_destroyed(_FakeDb(), s, now=now)
        assert result["snapshot_commodities"] == 0
        assert s.ownership["destroyed_inventory"] == {}

    def test_destroyed_at_anchor_is_the_iso_wall_clock_time(self):
        now = datetime(2026, 8, 9, 12, 30, 0, tzinfo=UTC)
        s = _station()
        mark_station_destroyed(_FakeDb(), s, now=now)
        assert s.ownership["destroyed_at"] == now.isoformat()

    def test_stored_credits_and_defenses_are_left_untouched(self):
        now = datetime(2026, 8, 9, tzinfo=UTC)
        s = _station(
            ownership={"treasury_balance": 5000},
            defenses={"defense_drones": 12, "patrol_ships": 3},
        )
        mark_station_destroyed(_FakeDb(), s, now=now)
        assert s.ownership["treasury_balance"] == 5000
        assert s.defenses == {"defense_drones": 12, "patrol_ships": 3}


# ---------------------------------------------------------------------------
# is_recovery_due
# ---------------------------------------------------------------------------


class TestIsRecoveryDue:
    def test_not_destroyed_is_never_due(self):
        s = _station(is_destroyed=False)
        assert is_recovery_due(s) is False

    def test_due_when_canonical_hours_since_anchor_meets_threshold(self):
        destroyed_at = datetime(2026, 8, 8, 0, 0, 0, tzinfo=UTC)
        # Choose `now` safely past the real scaled_deadline boundary (mirrors
        # mark_station_destroyed's own deadline math) -- a point exactly AT
        # the deadline risks float rounding (24/SCALE*SCALE != 24.0 exactly
        # for some SCALE values) flipping the >= comparison either way.
        deadline = game_time.scaled_deadline(STATION_RECOVERY_HOURS, destroyed_at)
        past_deadline = deadline + timedelta(seconds=1)
        s = _station(
            is_destroyed=True,
            ownership={"destroyed_at": destroyed_at.isoformat()},
        )
        assert is_recovery_due(s, now=past_deadline) is True

    def test_not_due_before_the_window_elapses(self):
        destroyed_at = datetime(2026, 8, 8, 0, 0, 0, tzinfo=UTC)
        just_before = destroyed_at + timedelta(seconds=1)
        s = _station(
            is_destroyed=True,
            ownership={"destroyed_at": destroyed_at.isoformat()},
        )
        assert is_recovery_due(s, now=just_before) is False

    def test_corrupt_anchor_falls_back_to_recovery_time_deadline(self):
        now = datetime(2026, 8, 9, tzinfo=UTC)
        s = _station(
            is_destroyed=True,
            ownership={"destroyed_at": "not-a-real-timestamp"},
            recovery_time=now - timedelta(seconds=1),
        )
        assert is_recovery_due(s, now=now) is True

    def test_missing_anchor_falls_back_to_recovery_time_deadline(self):
        now = datetime(2026, 8, 9, tzinfo=UTC)
        s = _station(
            is_destroyed=True,
            ownership={},
            recovery_time=now - timedelta(seconds=1),
        )
        assert is_recovery_due(s, now=now) is True

    def test_missing_anchor_and_recovery_time_is_never_due(self):
        s = _station(is_destroyed=True, ownership={}, recovery_time=None)
        assert is_recovery_due(s) is False

    def test_naive_recovery_time_fallback_is_coerced_to_aware(self):
        now = datetime(2026, 8, 9, tzinfo=UTC)
        naive_deadline = (now - timedelta(seconds=1)).replace(tzinfo=None)
        s = _station(is_destroyed=True, ownership={}, recovery_time=naive_deadline)
        # Would raise (naive vs aware comparison) if not coerced.
        assert is_recovery_due(s, now=now) is True


# ---------------------------------------------------------------------------
# recover_station
# ---------------------------------------------------------------------------


class TestRecoverStation:
    def test_not_destroyed_is_a_no_op(self):
        s = _station(is_destroyed=False)
        result = recover_station(_FakeDb(), s)
        assert result["status"] == "not_destroyed"

    def test_rebuilds_commodities_to_half_the_snapshot(self):
        s = _station(
            is_destroyed=True,
            ownership={"destroyed_inventory": {"ore": 40}},
            commodities={"ore": {"quantity": 5, "capacity": 100}},
        )
        recover_station(_FakeDb(), s)
        assert s.commodities["ore"]["quantity"] == 20

    def test_rebuild_is_clamped_to_capacity(self):
        s = _station(
            is_destroyed=True,
            ownership={"destroyed_inventory": {"ore": 400}},
            commodities={"ore": {"quantity": 0, "capacity": 100}},
        )
        recover_station(_FakeDb(), s)
        assert s.commodities["ore"]["quantity"] == 100

    def test_commodity_added_after_destruction_rebuilds_from_live_quantity(self):
        s = _station(
            is_destroyed=True,
            ownership={"destroyed_inventory": {"ore": 40}},
            commodities={
                "ore": {"quantity": 5, "capacity": 100},
                "organics": {"quantity": 20, "capacity": 100},
            },
        )
        recover_station(_FakeDb(), s)
        assert s.commodities["organics"]["quantity"] == 10

    def test_clears_destruction_state(self):
        s = _station(
            is_destroyed=True,
            recovery_time=datetime(2026, 1, 1, tzinfo=UTC),
            ownership={"destroyed_inventory": {}, "destroyed_at": "x"},
        )
        recover_station(_FakeDb(), s)
        assert s.is_destroyed is False
        assert s.recovery_time is None
        assert s.status == StationStatus.OPERATIONAL

    def test_removes_snapshot_keys_but_preserves_other_ownership_buckets(self):
        s = _station(
            is_destroyed=True,
            ownership={
                "destroyed_inventory": {"ore": 10},
                "destroyed_at": "x",
                "treasury_balance": 5000,
            },
        )
        recover_station(_FakeDb(), s)
        assert "destroyed_inventory" not in s.ownership
        assert "destroyed_at" not in s.ownership
        assert s.ownership["treasury_balance"] == 5000

    def test_zeroes_active_defense_drone_and_patrol_pools(self):
        s = _station(
            is_destroyed=True,
            ownership={"destroyed_inventory": {}},
            defenses={"defense_drones": 12, "patrol_ships": 3},
        )
        recover_station(_FakeDb(), s)
        assert s.defenses["defense_drones"] == 0
        assert s.defenses["patrol_ships"] == 0

    def test_resets_active_toggle_defenses_to_false(self):
        s = _station(
            is_destroyed=True,
            ownership={"destroyed_inventory": {}},
            defenses={"auto_turrets": True, "defense_grid": True},
        )
        recover_station(_FakeDb(), s)
        assert s.defenses["auto_turrets"] is False
        assert s.defenses["defense_grid"] is False

    def test_returns_rebuilt_commodity_count(self):
        s = _station(
            is_destroyed=True,
            ownership={"destroyed_inventory": {"ore": 10, "organics": 20}},
            commodities={
                "ore": {"quantity": 0, "capacity": 100},
                "organics": {"quantity": 0, "capacity": 100},
            },
        )
        result = recover_station(_FakeDb(), s)
        assert result["status"] == "recovered"
        assert result["commodities_rebuilt"] == 2

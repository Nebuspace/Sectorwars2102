"""Unit tests for the docking slip service (pure functions, no DB).

Covers the canon transient-slip capacity table, the documented docking fee
interpretation table, and the bump tenure gate including GAME_TIME_SCALE
compression (src/core/game_time.py).
"""
from datetime import datetime, timedelta, UTC
from types import SimpleNamespace

import pytest

from src.core import game_time
from src.models.station import StationClass
from src.services import docking_service


def make_station(station_class=StationClass.CLASS_3, is_spacedock=False, tradedock_tier=None):
    """Lightweight stand-in carrying the attributes the pure helpers read."""
    return SimpleNamespace(
        station_class=station_class,
        is_spacedock=is_spacedock,
        tradedock_tier=tradedock_tier,
    )


class TestSlipCapacityTable:
    """Canon: CLASS_0 capital 80 · 1-2: 8 · 3-6: 12 · 7-10: 20 · 11: 24 ·
    spacedock 30 · tradedock B 20 · tradedock A 24."""

    @pytest.mark.parametrize("station_class,expected", [
        (StationClass.CLASS_0, 80),   # capital
        (StationClass.CLASS_1, 8),
        (StationClass.CLASS_2, 8),
        (StationClass.CLASS_3, 12),
        (StationClass.CLASS_4, 12),
        (StationClass.CLASS_5, 12),
        (StationClass.CLASS_6, 12),
        (StationClass.CLASS_7, 20),
        (StationClass.CLASS_8, 20),
        (StationClass.CLASS_9, 20),
        (StationClass.CLASS_10, 20),
        (StationClass.CLASS_11, 24),
    ])
    def test_class_buckets(self, station_class, expected):
        assert docking_service.slip_capacity_for(make_station(station_class)) == expected

    def test_spacedock(self):
        station = make_station(StationClass.CLASS_5, is_spacedock=True)
        assert docking_service.slip_capacity_for(station) == 30

    def test_tradedock_tier_b(self):
        station = make_station(StationClass.CLASS_5, tradedock_tier="B")
        assert docking_service.slip_capacity_for(station) == 20

    def test_tradedock_tier_a(self):
        station = make_station(StationClass.CLASS_5, tradedock_tier="A")
        assert docking_service.slip_capacity_for(station) == 24

    def test_tradedock_tier_checked_before_spacedock(self):
        station = make_station(StationClass.CLASS_5, is_spacedock=True, tradedock_tier="A")
        assert docking_service.slip_capacity_for(station) == 24

    def test_spacedock_checked_before_class_buckets(self):
        station = make_station(StationClass.CLASS_0, is_spacedock=True)
        assert docking_service.slip_capacity_for(station) == 30


# NOTE: docking_fee_for no longer keys off station_class/is_spacedock/
# tradedock_tier -- it's the canon ship-SIZE x security-TIER matrix
# (FEATURES/economy/station-protection.md §Docking fee economics). The old
# TestDockingFeeTable here asserted a station-class-based table whose own
# docstring admitted it was an undocumented interpretation, not canon; it's
# been replaced by the full matrix suite in test_station_security_fees.py.


def test_bump_cost_is_five_times_fee():
    assert docking_service.BUMP_COST_MULTIPLIER == 5


def make_occupancy(docked_minutes_ago: float):
    return SimpleNamespace(
        docked_at=datetime.now(UTC) - timedelta(minutes=docked_minutes_ago)
    )


class TestBumpTenureGate:
    """Bump requires >= 4 CANONICAL hours of tenure; GAME_TIME_SCALE stretches
    wall-clock elapsed time so the gate opens early on dev."""

    def test_under_four_hours_not_bumpable_at_real_time(self, monkeypatch):
        monkeypatch.setattr(game_time, "GAME_TIME_SCALE", 1.0)
        occupancy = make_occupancy(docked_minutes_ago=3 * 60)  # 3 wall hours
        assert docking_service.is_bumpable(occupancy) is False

    def test_over_four_hours_bumpable_at_real_time(self, monkeypatch):
        monkeypatch.setattr(game_time, "GAME_TIME_SCALE", 1.0)
        occupancy = make_occupancy(docked_minutes_ago=5 * 60)  # 5 wall hours
        assert docking_service.is_bumpable(occupancy) is True

    def test_scale_compresses_tenure(self, monkeypatch):
        # At scale 144, 2 wall minutes = 4.8 canonical hours -> bumpable.
        monkeypatch.setattr(game_time, "GAME_TIME_SCALE", 144.0)
        occupancy = make_occupancy(docked_minutes_ago=2)
        assert docking_service.occupant_tenure_hours(occupancy) == pytest.approx(4.8, rel=0.05)
        assert docking_service.is_bumpable(occupancy) is True

    def test_scale_does_not_open_gate_too_early(self, monkeypatch):
        # At scale 144, 1 wall minute = 2.4 canonical hours -> still protected.
        monkeypatch.setattr(game_time, "GAME_TIME_SCALE", 144.0)
        occupancy = make_occupancy(docked_minutes_ago=1)
        assert docking_service.is_bumpable(occupancy) is False

    def test_gate_threshold_is_inclusive(self, monkeypatch):
        monkeypatch.setattr(game_time, "GAME_TIME_SCALE", 1.0)
        now = datetime.now(UTC)
        occupancy = SimpleNamespace(docked_at=now - timedelta(hours=4))
        assert docking_service.is_bumpable(occupancy, now=now) is True

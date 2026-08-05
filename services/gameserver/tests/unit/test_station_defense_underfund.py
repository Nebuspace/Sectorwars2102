"""ADR-0093 §3 defense-underfunding cascade — streak math + downgrade edges.

DB-free: reuses the FakeSession / fresh Station pattern from
test_station_security_ladder.py. Covers process_station_defense_deficit
(the pure per-station kernel); the Galaxy.state day-gate is a thin wrapper
mirroring run_daily_scan_gated and is not re-proven here.
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace
from typing import Any, List

from sqlalchemy import inspect as sa_inspect

from src.models.station import Station
from src.services import station_security_service as sts
from src.services.port_ownership_service import FEE_DEFENSE_PCT_KEY


def _fresh_station(
    *,
    owner_id=None,
    security=None,
    price_modifiers=None,
):
    station = Station()
    station.id = uuid.uuid4()
    station.name = "Deficit Dock"
    station.owner_id = owner_id
    station.security = security
    station.price_modifiers = price_modifiers if price_modifiers is not None else {}
    station.treasury_balance = 0
    station.ownership = {}
    station.tax_rate = 0.10
    insp = sa_inspect(station)
    insp.committed_state.clear()
    insp._commit_all(insp.dict)
    return station


def _owner(**overrides):
    base = dict(id=uuid.uuid4(), credits=1_000_000)
    base.update(overrides)
    return SimpleNamespace(**base)


class _FakeQuery:
    def __init__(self, result):
        self._result = result

    def filter(self, *a, **k):
        return self

    def with_for_update(self, *a, **k):
        return self

    def order_by(self, *a, **k):
        return self

    def first(self):
        if isinstance(self._result, list):
            return self._result[0] if self._result else None
        return self._result

    def all(self):
        if isinstance(self._result, list):
            return list(self._result)
        return [] if self._result is None else [self._result]


class _FakeSession:
    def __init__(self, *, station=None, player=None):
        self._station = station
        self._player = player
        self.added: List[Any] = []
        self.flush_calls = 0

    def query(self, model):
        name = getattr(model, "__name__", str(model))
        if name == "Station":
            return _FakeQuery(self._station)
        if name == "Player":
            return _FakeQuery(self._player)
        raise AssertionError(f"unexpected query for {model!r}")

    def add(self, obj):
        self.added.append(obj)

    def flush(self):
        self.flush_calls += 1

    def commit(self):
        raise AssertionError("flush-only")


def _underfunded_mods(pct: float = 0.30) -> dict:
    return {FEE_DEFENSE_PCT_KEY: pct}


def _funded_mods(pct: float = 0.40) -> dict:
    return {FEE_DEFENSE_PCT_KEY: pct}


class TestStreakMath:
    def test_day1_warns_once_and_does_not_drop_tier(self):
        owner = _owner()
        station = _fresh_station(
            owner_id=owner.id,
            security={"tier": "standard"},
            price_modifiers=_underfunded_mods(0.30),
        )
        db = _FakeSession(station=station, player=owner)
        a1 = sts.process_station_defense_deficit(db, station, this_day=100, owner=owner)
        assert a1["underfunded"] is True
        assert a1["days"] == 1
        assert a1["warned"] is True
        assert a1["tier_dropped"] is False
        assert a1["forced_none"] is False
        assert station.security["tier"] == "standard"
        assert station.security[sts.DEFICIT_WARNED_KEY] is True
        assert len(db.added) == 1
        assert db.added[0].priority == "high"

        # Same day re-entry is a no-op
        a2 = sts.process_station_defense_deficit(db, station, this_day=100, owner=owner)
        assert a2["skipped"] is True
        assert a2["days"] == 1
        assert len(db.added) == 1

    def test_day3_drops_one_tier_once(self):
        owner = _owner()
        station = _fresh_station(
            owner_id=owner.id,
            security={
                "tier": "premium",
                sts.DEFICIT_DAYS_KEY: 2,
                sts.DEFICIT_LAST_DAY_KEY: 101,
                sts.DEFICIT_WARNED_KEY: True,
            },
            price_modifiers=_underfunded_mods(0.32),
        )
        db = _FakeSession(station=station, player=owner)
        action = sts.process_station_defense_deficit(
            db, station, this_day=102, owner=owner
        )
        assert action["days"] == 3
        assert action["tier_dropped"] is True
        assert action["forced_none"] is False
        assert station.security["tier"] == "standard"
        assert station.security[sts.DEFICIT_STEP_KEY] is True
        # Pending voluntary ops must be cleared by the auto drop
        assert station.security.get("upgrade_to") in (None,)
        assert station.security.get("downgrade_completes_at") in (None,)

        # Day 4 must NOT drop again (step already applied)
        action2 = sts.process_station_defense_deficit(
            db, station, this_day=103, owner=owner
        )
        assert action2["days"] == 4
        assert action2["tier_dropped"] is False
        assert station.security["tier"] == "standard"

    def test_day7_forces_none_and_urgent_notify(self):
        owner = _owner()
        station = _fresh_station(
            owner_id=owner.id,
            security={
                "tier": "basic",
                sts.DEFICIT_DAYS_KEY: 6,
                sts.DEFICIT_LAST_DAY_KEY: 106,
                sts.DEFICIT_WARNED_KEY: True,
                sts.DEFICIT_STEP_KEY: True,
            },
            price_modifiers=_underfunded_mods(0.30),
        )
        db = _FakeSession(station=station, player=owner)
        action = sts.process_station_defense_deficit(
            db, station, this_day=107, owner=owner
        )
        assert action["days"] == 7
        assert action["forced_none"] is True
        assert station.security["tier"] == "none"
        assert any(m.priority == "urgent" for m in db.added)

    def test_funded_resets_streak(self):
        owner = _owner()
        station = _fresh_station(
            owner_id=owner.id,
            security={
                "tier": "standard",
                sts.DEFICIT_DAYS_KEY: 4,
                sts.DEFICIT_LAST_DAY_KEY: 50,
                sts.DEFICIT_WARNED_KEY: True,
                sts.DEFICIT_STEP_KEY: True,
            },
            price_modifiers=_funded_mods(0.40),
        )
        db = _FakeSession(station=station, player=owner)
        action = sts.process_station_defense_deficit(
            db, station, this_day=51, owner=owner
        )
        assert action["underfunded"] is False
        assert sts.DEFICIT_DAYS_KEY not in station.security
        assert sts.DEFICIT_WARNED_KEY not in station.security
        assert station.security["tier"] == "standard"

    def test_threshold_boundary_exactly_35_is_funded(self):
        owner = _owner()
        station = _fresh_station(
            owner_id=owner.id,
            security={"tier": "basic", sts.DEFICIT_DAYS_KEY: 2},
            price_modifiers=_underfunded_mods(0.35),
        )
        db = _FakeSession(station=station, player=owner)
        action = sts.process_station_defense_deficit(
            db, station, this_day=10, owner=owner
        )
        assert action["underfunded"] is False
        assert sts.DEFICIT_DAYS_KEY not in station.security

    def test_unowned_skipped(self):
        station = _fresh_station(
            owner_id=None,
            security={"tier": "premium"},
            price_modifiers=_underfunded_mods(0.30),
        )
        db = _FakeSession(station=station)
        action = sts.process_station_defense_deficit(db, station, this_day=1)
        assert action["skipped"] is True
        assert station.security.get("tier") == "premium"


class TestImmediateTierClearPending:
    def test_day3_clears_pending_upgrade(self):
        owner = _owner()
        station = _fresh_station(
            owner_id=owner.id,
            security={
                "tier": "basic",
                "upgrade_to": "standard",
                "upgrade_completes_at": "2102-06-02T12:00:00+00:00",
                sts.DEFICIT_DAYS_KEY: 2,
                sts.DEFICIT_LAST_DAY_KEY: 1,
                sts.DEFICIT_WARNED_KEY: True,
            },
            price_modifiers=_underfunded_mods(0.30),
        )
        db = _FakeSession(station=station, player=owner)
        sts.process_station_defense_deficit(db, station, this_day=2, owner=owner)
        assert station.security["tier"] == "none"  # basic → one step → none
        assert station.security.get("upgrade_to") is None
        assert station.security.get("upgrade_completes_at") is None

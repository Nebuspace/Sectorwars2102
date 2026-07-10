"""Unit tests — Living NPC System pure logic.

Covers the plan's unit-test surface that needs no database: schedule
block resolution (including multi-day route cycles), the canonical
clock at monkeypatched GAME_TIME_SCALE (patched as a module attribute —
the game_time helpers read it at call time), respawn-cooldown deadline
math, trader schedule construction, Federation squad tiers, and the
held-sweep wrapper-level pins (WO-CMB-SUSPECT-LIFE-1 / WO-RT-TEAM-REP /
WO-PIRATE-ECO-2 loop wiring).
"""

import logging
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import patch

from src.core import game_time
from src.services.npc_engagement_service import _federation_squad_size
from src.services.npc_scheduler_service import (
    _run_pirate_ecosystem_tick_sync,
    _run_suspect_clear_sweep_sync,
    _run_team_reputation_sweep_sync,
    canonical_day_number,
    canonical_minute_of_day,
    canonical_weekday,
    resolve_schedule_block,
)
from src.services.npc_spawn_service import RESPAWN_COOLDOWN_MINUTES
from src.services.npc_trading_service import build_trader_schedule

# ---------------------------------------------------------------------------
# Canonical clock
# ---------------------------------------------------------------------------

class TestCanonicalClock:
    def test_minute_of_day_matches_utc_at_scale_one(self, monkeypatch):
        monkeypatch.setattr(game_time, "GAME_TIME_SCALE", 1.0)
        now = datetime(2026, 6, 12, 13, 45, 0, tzinfo=UTC)
        assert canonical_minute_of_day(now) == 13 * 60 + 45

    def test_weekday_matches_datetime_at_scale_one(self, monkeypatch):
        monkeypatch.setattr(game_time, "GAME_TIME_SCALE", 1.0)
        now = datetime(2026, 6, 12, 12, 0, 0, tzinfo=UTC)  # a Friday
        assert canonical_weekday(now) == now.weekday() == 4

    def test_scale_accelerates_the_day(self, monkeypatch):
        monkeypatch.setattr(game_time, "GAME_TIME_SCALE", 144.0)
        t0 = datetime(2026, 6, 12, 0, 0, 0, tzinfo=UTC)
        # At scale 144 a canonical day passes every 10 wall-clock minutes.
        assert (
            canonical_day_number(t0 + timedelta(minutes=10))
            == canonical_day_number(t0) + 1
        )

    def test_respawn_cooldown_scales(self, monkeypatch):
        monkeypatch.setattr(game_time, "GAME_TIME_SCALE", 144.0)
        start = datetime(2026, 6, 12, 0, 0, 0, tzinfo=UTC)
        deadline = game_time.scaled_deadline(
            RESPAWN_COOLDOWN_MINUTES / 60.0, start
        )
        # 15 canonical minutes at scale 144 ≈ 6.25 wall seconds.
        assert abs((deadline - start).total_seconds() - 6.25) < 0.01


# ---------------------------------------------------------------------------
# Schedule block resolution
# ---------------------------------------------------------------------------

_BLOCKS = [
    {"start_minute": 0, "end_minute": 480, "activity": "sleep"},
    {"start_minute": 480, "end_minute": 1200, "activity": "patrol"},
]


class TestResolveScheduleBlock:
    def test_basic_match(self):
        schedule = {"blocks": _BLOCKS}
        assert resolve_schedule_block(schedule, 100, 0)["activity"] == "sleep"
        assert resolve_schedule_block(schedule, 480, 0)["activity"] == "patrol"
        assert resolve_schedule_block(schedule, 1300, 0) is None

    def test_empty_schedule(self):
        assert resolve_schedule_block({}, 100, 0) is None
        assert resolve_schedule_block({"blocks": []}, 100, 0) is None

    def test_shift_offset_wraps(self):
        schedule = {"blocks": _BLOCKS, "shift_offset_hours": 8}
        # 23:00 + 8h shift → 07:00 → sleep block.
        assert resolve_schedule_block(schedule, 23 * 60, 0)["activity"] == "sleep"

    def test_weekly_override(self):
        schedule = {
            "blocks": _BLOCKS,
            "weekly_overrides": [
                {"weekday": 6, "blocks": [
                    {"start_minute": 0, "end_minute": 1440, "activity": "personal"},
                ]},
            ],
        }
        assert resolve_schedule_block(schedule, 600, 6)["activity"] == "personal"
        assert resolve_schedule_block(schedule, 600, 2)["activity"] == "patrol"

    def test_malformed_blocks_tolerated(self):
        schedule = {"blocks": [
            {"start_minute": "x", "end_minute": None, "activity": "sleep"},
            {"start_minute": 0, "end_minute": 1440, "activity": "patrol"},
        ]}
        assert resolve_schedule_block(schedule, 600, 0)["activity"] == "patrol"

    def test_route_cycle_selects_by_day(self):
        schedule = {
            "route_cycle": {
                "cycle_days": 2,
                "days": {
                    "0": [{"start_minute": 0, "end_minute": 1440,
                           "activity": "commute"}],
                    "1": [{"start_minute": 0, "end_minute": 1440,
                           "activity": "work_station"}],
                },
            },
        }
        assert resolve_schedule_block(schedule, 600, 0, day_number=10)[
            "activity"] == "commute"
        assert resolve_schedule_block(schedule, 600, 0, day_number=11)[
            "activity"] == "work_station"


# ---------------------------------------------------------------------------
# Trader schedule construction
# ---------------------------------------------------------------------------

class TestBuildTraderSchedule:
    def _route(self):
        return [
            {"station_id": "a" * 32, "sector_id": 10, "buy_here": ["ore"]},
            {"station_id": "b" * 32, "sector_id": 20, "buy_here": []},
        ]

    def test_cycle_length_is_two_days_per_stop(self):
        schedule = build_trader_schedule(self._route())
        assert schedule["route_cycle"]["cycle_days"] == 4
        assert set(schedule["route_cycle"]["days"].keys()) == {"0", "1", "2", "3"}

    def test_transit_and_trade_day_shapes(self):
        schedule = build_trader_schedule(self._route())
        days = schedule["route_cycle"]["days"]
        transit = days["0"]
        assert transit[0]["activity"] == "sleep"
        assert transit[1]["activity"] == "commute"
        assert transit[1]["location_ref"]["sector_id"] == 10

        trade = days["1"]
        activities = [b["activity"] for b in trade]
        assert activities == ["sleep", "work_station", "socialize"]
        assert trade[1]["location_ref"]["stop_index"] == 0

    def test_route_persisted_for_trade_stops(self):
        schedule = build_trader_schedule(self._route())
        assert schedule["trade_route"][0]["buy_here"] == ["ore"]


# ---------------------------------------------------------------------------
# Federation squad tiers (police-forces.md, mapped onto code rep bands)
# ---------------------------------------------------------------------------

class TestFederationSquadTiers:
    def _player(self, rep):
        return SimpleNamespace(personal_reputation=rep)

    def test_low_tier_single_officer(self):
        assert _federation_squad_size(self._player(-100)) == (1, False)

    def test_medium_tier(self):
        assert _federation_squad_size(self._player(-300)) == (2, False)

    def test_high_tier(self):
        assert _federation_squad_size(self._player(-600)) == (3, False)

    def test_public_enemy_brings_the_captain(self):
        assert _federation_squad_size(self._player(-900)) == (3, True)


# ---------------------------------------------------------------------------
# Held-sweep wrapper-level pins (WO-CMB-SUSPECT-LIFE-1 / WO-RT-TEAM-REP /
# WO-PIRATE-ECO-2) — no existing sweep wrapper in npc_scheduler_service.py
# had a wrapper-level unit test before this (they are session-management
# glue only, per _run_contract_generation_sync's own comment); one pin per
# new wrapper proves the specific, load-bearing contract every sweep here
# depends on: a raising core is CAUGHT (rollback, safe default returned)
# rather than propagating out of asyncio.to_thread and crashing the
# scheduler's while-True loop.
# ---------------------------------------------------------------------------

class _FakeLockDB:
    """Minimal SessionLocal() stand-in: db.execute(...).scalar() always
    reports the lock acquired; commit/rollback/close just record whether
    they fired, matching a real session shape closely enough for a
    wrapper-level pin (no live Postgres)."""
    def __init__(self):
        self.committed = False
        self.rolled_back = False
        self.closed = False

    def execute(self, *args, **kwargs):
        return SimpleNamespace(scalar=lambda: True)

    def query(self, *args, **kwargs):
        raise RuntimeError("query layer exploded")

    def commit(self):
        self.committed = True

    def rollback(self):
        self.rolled_back = True

    def close(self):
        self.closed = True


class TestHeldSweepWiringNeverBreaksTheLoop:
    def test_suspect_clear_sweep_survives_a_raising_core(self):
        db = _FakeLockDB()
        with patch("src.core.database.SessionLocal", return_value=db), patch(
            "src.services.suspect_service.clear_expired_suspects",
            side_effect=RuntimeError("boom"),
        ):
            result = _run_suspect_clear_sweep_sync()
        assert result == 0
        assert db.rolled_back is True
        assert db.committed is False
        assert db.closed is True

    def test_team_reputation_sweep_survives_a_raising_core(self):
        db = _FakeLockDB()
        with patch("src.core.database.SessionLocal", return_value=db), patch(
            "src.services.team_reputation_service.sweep_due_team_reputations",
            side_effect=RuntimeError("boom"),
        ):
            result = _run_team_reputation_sweep_sync()
        assert result == {"due": 0, "recalculated": 0}
        assert db.rolled_back is True
        assert db.committed is False
        assert db.closed is True

    def test_pirate_ecosystem_tick_sweep_survives_a_raising_core(self):
        # The fake db's own query(...) raises directly (simulating the
        # region-scan query itself failing) — exercises the wrapper's
        # OUTER try/except without needing to mock two levels deep into
        # run_weekly_tick/evolution_tick.
        db = _FakeLockDB()
        with patch("src.core.database.SessionLocal", return_value=db):
            result = _run_pirate_ecosystem_tick_sync()
        assert result == {
            "regions_ticked": 0, "growth_actions": 0,
            "holdings_evaluated": 0, "evolutions": 0,
        }
        assert db.rolled_back is True
        assert db.committed is False
        assert db.closed is True

    def test_locked_elsewhere_skips_cleanly_without_touching_the_core(self, caplog):
        """A skip-on-contention path (lock held by another instance) must
        NOT call the core at all, and must NOT roll back (there is nothing
        to roll back — the lock-check itself is the only statement run).

        WO-SWEEP-SILENT-SWEEPS: this path used to return 0 with NO log line
        at all — indistinguishable, from the log, from "ran and found
        nothing due" (the caller only logs `if cleared:`). caplog pins that
        it now logs, so a lock-contention run is visible even though its
        return value is identical to a legitimate empty run."""
        class _LockHeldDB(_FakeLockDB):
            def execute(self, *args, **kwargs):
                return SimpleNamespace(scalar=lambda: False)

        db = _LockHeldDB()
        with caplog.at_level(logging.INFO), patch(
            "src.core.database.SessionLocal", return_value=db,
        ), patch(
            "src.services.suspect_service.clear_expired_suspects",
        ) as mock_core:
            result = _run_suspect_clear_sweep_sync()
        assert result == 0
        mock_core.assert_not_called()
        assert db.closed is True
        assert any(
            "lock busy, skipped" in r.getMessage() for r in caplog.records
        ), [r.getMessage() for r in caplog.records]

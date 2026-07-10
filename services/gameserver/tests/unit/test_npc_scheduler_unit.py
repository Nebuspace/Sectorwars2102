"""Unit tests — Living NPC System pure logic.

Covers the plan's unit-test surface that needs no database: schedule
block resolution (including multi-day route cycles), the canonical
clock at monkeypatched GAME_TIME_SCALE (patched as a module attribute —
the game_time helpers read it at call time), respawn-cooldown deadline
math, trader schedule construction, Federation squad tiers, and the
held-sweep wrapper-level pins (WO-CMB-SUSPECT-LIFE-1 / WO-RT-TEAM-REP /
WO-PIRATE-ECO-2 loop wiring).
"""

import asyncio
import logging
import time
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

import pytest

from src.core import game_time
from src.models.galaxy import Galaxy
from src.services.npc_engagement_service import _federation_squad_size
from src.services.npc_scheduler_service import (
    SUSPECT_CLEAR_SWEEP_SECONDS,
    _contract_generation_loop,
    _npc_scheduler_main_loop,
    _run_pirate_ecosystem_tick_sync,
    _run_suspect_clear_sweep_sync,
    _run_team_reputation_sweep_sync,
    _sweep_due_and_advance,
    _SUSPECT_CLEAR_STATE_KEY,
    canonical_day_number,
    canonical_minute_of_day,
    canonical_weekday,
    npc_scheduler_loop,
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
    wrapper-level pin (no live Postgres).

    query(Galaxy) succeeds (WO-SCHED-CADENCE-DRIFT's durable due-check reads
    Galaxy.state before any of these wrappers reach their real core) —
    returns a real, detached Galaxy row with an empty state dict, so
    _sweep_due_and_advance reports "due" and the wrapper proceeds to the
    core these tests actually pin. Any OTHER entity still raises — these
    tests deliberately kept that to simulate "the core's own query layer
    exploded", now exercised just past the due-check instead of at it."""
    def __init__(self):
        self.committed = False
        self.rolled_back = False
        self.closed = False

    def execute(self, *args, **kwargs):
        return SimpleNamespace(scalar=lambda: True)

    def query(self, *entities, **kwargs):
        from src.models.galaxy import Galaxy
        if entities and entities[0] is Galaxy:
            galaxy = Galaxy(id=uuid4(), created_at=datetime(2020, 1, 1, tzinfo=UTC), state={})
            return SimpleNamespace(order_by=lambda *a, **k: SimpleNamespace(first=lambda: galaxy))
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


# ---------------------------------------------------------------------------
# WO-SCHED-CADENCE-DRIFT — durable, wall-clock, restart-safe cadence for the
# sub-daily loop-table sweeps. Two falsifiers per the WO: cadence must track
# WALL-CLOCK elapsed regardless of how many times the wrapper was invoked in
# between (the drift bug this replaces was an ITERATION counter, not a
# clock), and the anchor must survive what a process restart does — a fresh
# SessionLocal() with no in-process state carried over (the restart-reset
# bug this replaces was `elapsed = 0` on every process start).
# ---------------------------------------------------------------------------

class _FakeDurableCadenceDB:
    """Session stand-in sharing ONE persistent Galaxy row across every fresh
    instance — mirrors the real world exactly: a fresh SessionLocal() call
    (as happens on every real sweep tick, or after a process restart) still
    reads the SAME durable row from the DB, never an in-process counter."""
    def __init__(self, galaxy: Galaxy):
        self.galaxy = galaxy
        self.committed = False
        self.rolled_back = False
        self.closed = False

    def execute(self, *args, **kwargs):
        return SimpleNamespace(scalar=lambda: True)  # advisory lock always free

    def query(self, *entities, **kwargs):
        if entities and entities[0] is Galaxy:
            galaxy = self.galaxy
            return SimpleNamespace(order_by=lambda *a, **k: SimpleNamespace(first=lambda: galaxy))
        raise RuntimeError("query layer exploded")

    def commit(self):
        self.committed = True

    def rollback(self):
        self.rolled_back = True

    def close(self):
        self.closed = True


class TestSweepDueAndAdvance:
    def test_cadence_tracks_wall_clock_not_invocation_count(self):
        """Drift falsifier: MANY rapid consecutive calls, all still inside
        the interval by wall-clock, must not fire — no matter how many of
        them there are. This is exactly the old bug's shape: an iteration
        counter that advances by a fixed amount PER CALL would eventually
        cross the threshold purely from call-count; a wall-clock check
        driven by the caller-supplied `now` never does, until `now` itself
        genuinely crosses the interval."""
        galaxy = Galaxy(id=uuid4(), created_at=datetime(2020, 1, 1, tzinfo=UTC), state={})
        t0 = datetime(2026, 1, 1, tzinfo=UTC)
        interval = 300

        # First call: nothing stamped yet -- due, fires, stamps t0.
        assert _sweep_due_and_advance(_FakeDurableCadenceDB(galaxy), "k", interval, t0) is True

        # 250 rapid "iterations" 1 second apart (a fast/idle loop) -- every
        # single one is still inside the 300s window and must not fire,
        # regardless of the call COUNT (250 calls, zero fires).
        for i in range(1, 251):
            due = _sweep_due_and_advance(_FakeDurableCadenceDB(galaxy), "k", interval, t0 + timedelta(seconds=i))
            assert due is False, f"fired early at +{i}s on call #{i} — tracking call count, not wall-clock"

        # Wall-clock genuinely crosses the interval -- fires, independent of
        # the 250 calls that just happened.
        assert _sweep_due_and_advance(_FakeDurableCadenceDB(galaxy), "k", interval, t0 + timedelta(seconds=300)) is True

    def test_cadence_survives_a_simulated_restart(self):
        """Restart falsifier: stamp the anchor, then read it back through a
        BRAND NEW db/session instance (no object identity, no in-process
        state shared with the stamping call) — exactly what a fresh
        SessionLocal() after a process restart does. Must still correctly
        report not-due until the real interval has elapsed, proving the
        guarantee lives in the persisted row, not in anything the old
        `elapsed = 0` process-relative counter tracked."""
        galaxy = Galaxy(id=uuid4(), created_at=datetime(2020, 1, 1, tzinfo=UTC), state={})
        t0 = datetime(2026, 1, 1, tzinfo=UTC)
        interval = 1800

        assert _sweep_due_and_advance(_FakeDurableCadenceDB(galaxy), "k", interval, t0) is True

        # A brand new fake session (simulates a fresh SessionLocal() post-
        # restart) reading the SAME underlying galaxy row, well before the
        # interval elapses -- must still say "not due".
        restarted_db = _FakeDurableCadenceDB(galaxy)
        assert _sweep_due_and_advance(restarted_db, "k", interval, t0 + timedelta(seconds=5)) is False

        # Once the real interval has elapsed, another fresh "post-restart"
        # session correctly sees it's due again.
        another_restart_db = _FakeDurableCadenceDB(galaxy)
        assert _sweep_due_and_advance(another_restart_db, "k", interval, t0 + timedelta(seconds=1800)) is True

    def test_wrapper_wiring_skips_the_real_core_when_not_yet_due(self):
        """Ties the mechanism to a REAL sweep wrapper (not just the bare
        helper): two back-to-back calls to _run_suspect_clear_sweep_sync,
        sharing one persistent Galaxy row across fresh SessionLocal()
        instances (mirroring what actually happens tick-to-tick) — the
        second call, happening a real wall-clock instant later (nowhere
        near SUSPECT_CLEAR_SWEEP_SECONDS), must skip the core entirely."""
        galaxy = Galaxy(id=uuid4(), created_at=datetime(2020, 1, 1, tzinfo=UTC), state={})

        with patch(
            "src.core.database.SessionLocal", side_effect=lambda: _FakeDurableCadenceDB(galaxy),
        ), patch(
            "src.services.suspect_service.clear_expired_suspects", return_value=3,
        ) as mock_core:
            first = _run_suspect_clear_sweep_sync()
            second = _run_suspect_clear_sweep_sync()

        assert first == 3
        assert second == 0
        mock_core.assert_called_once()
        assert _SUSPECT_CLEAR_STATE_KEY in galaxy.state
        # Sanity: the interval this wrapper actually uses is what gated the
        # second call, not some other/default value.
        assert SUSPECT_CLEAR_SWEEP_SECONDS > 0


# ---------------------------------------------------------------------------
# WO-SCHED-LOOP-WEDGE — contract generation must never be able to starve the
# fast sweeps (expiry/suspect/team-rep/PECO) by running inline ahead of them
# in the same coroutine. Confirmed live on heimdall, 2026-07-10: 0bc6e1f made
# generation reachable every tick instead of once per elapsed-drifted window
# (WO-SCHED-CADENCE-DRIFT), and the first real-scale post-restart pass never
# returned — zero main-loop iterations completed for the ~17min observation
# window, because generation sat sequentially ahead of every other sweep in
# the same while-True body. Fix: generation now runs on its own independent
# asyncio task (_contract_generation_loop), never awaited inline by the main
# loop (_npc_scheduler_main_loop).
# ---------------------------------------------------------------------------

class TestContractGenerationDecoupling:
    @pytest.mark.asyncio
    async def test_slow_generation_does_not_block_a_concurrent_fast_task(self, monkeypatch):
        """The falsifier, run for real — not just read from the source. A
        blocking sync call standing in for a slow/heavy generation pass
        runs inside _contract_generation_loop's own asyncio.to_thread,
        concurrently with a fast counter task standing in for the main
        loop's other sweeps. Pre-fix, these were sequentially awaited in
        ONE coroutine — the fast task could not have advanced until the
        slow call returned. Post-fix, they're independent asyncio tasks:
        the counter keeps ticking the whole time the slow call is still
        blocked in its own OS thread."""
        def _slow_sync_call():
            time.sleep(0.4)
            return 0

        monkeypatch.setattr(
            "src.services.npc_scheduler_service._run_contract_generation_sync",
            _slow_sync_call,
        )

        fast_ticks = 0

        async def _fast_task():
            nonlocal fast_ticks
            while True:
                await asyncio.sleep(0.02)
                fast_ticks += 1

        gen_task = asyncio.create_task(_contract_generation_loop())
        fast_task = asyncio.create_task(_fast_task())
        try:
            # The slow sync call is still sleeping for most of this window
            # — if the fast task were blocked behind it (the pre-fix
            # shape: one coroutine, sequential awaits), fast_ticks would
            # still be 0 here.
            await asyncio.sleep(0.35)
            assert fast_ticks >= 10, f"fast task starved — only {fast_ticks} tick(s) in 0.35s"
        finally:
            gen_task.cancel()
            fast_task.cancel()
            for t in (gen_task, fast_task):
                try:
                    await t
                except asyncio.CancelledError:
                    pass

    @pytest.mark.asyncio
    async def test_npc_scheduler_loop_runs_generation_as_an_independent_task(self, monkeypatch):
        """Wiring pin: npc_scheduler_loop must create _contract_generation_
        loop via asyncio.create_task (an independent task, never an inline
        await) before running the main loop, and must cancel + await it
        once the main loop exits (for ANY reason) — never left orphaned."""
        import src.services.npc_scheduler_service as svc

        gen_loop_started = asyncio.Event()
        gen_loop_cancelled = asyncio.Event()

        async def _fake_contract_generation_loop():
            gen_loop_started.set()
            try:
                await asyncio.sleep(3600)  # would hang forever if never cancelled
            except asyncio.CancelledError:
                gen_loop_cancelled.set()
                raise

        async def _fake_main_loop():
            # Simulates the main loop exiting (crash, cancellation, or —
            # here — just returning) shortly after the generation task has
            # genuinely started.
            await asyncio.sleep(0.05)

        monkeypatch.setattr(svc, "_contract_generation_loop", _fake_contract_generation_loop)
        monkeypatch.setattr(svc, "_npc_scheduler_main_loop", _fake_main_loop)
        for name in (
            "_repair_orphan_schedules_sync", "_seed_trader_rosters_sync",
            "_bulk_fill_traders_sync", "_assign_trader_notoriety_sync",
            "_relocate_stranded_npcs_sync", "_disperse_law_patrols_sync",
        ):
            monkeypatch.setattr(svc, name, lambda: 0)
        monkeypatch.setattr(svc, "_assign_trader_missions_sync", lambda: {})

        await svc.npc_scheduler_loop()

        assert gen_loop_started.is_set()
        assert gen_loop_cancelled.is_set()

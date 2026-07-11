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
import threading
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
    _run_contract_expire_sweep_sync,
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

    def test_contract_expire_sweep_survives_a_raising_core(self):
        """WO-DRIFT-econ-contract-sweep-advisory-lock (expire half): same
        raising-core contract as every other held-sweep wrapper above — a
        lock acquirer whose core explodes rolls back and returns 0 rather
        than propagating out of asyncio.to_thread."""
        db = _FakeLockDB()
        with patch("src.core.database.SessionLocal", return_value=db), patch(
            "src.services.contract_service.sweep_expired_contracts",
            side_effect=RuntimeError("boom"),
        ):
            result = _run_contract_expire_sweep_sync()
        assert result == 0
        assert db.rolled_back is True
        assert db.committed is False
        assert db.closed is True

    def test_contract_expire_sweep_combines_posted_and_accepted_counts(self):
        """WO-DRIFT-econ-accepted-deadline-expiry: the CONTRACT_EXPIRE
        driver runs sweep_expired_accepted_contracts alongside sweep_
        expired_contracts under the SAME CEXP lock/due-check/commit -- the
        wrapper's return value is their sum, and both cores are reached
        exactly once.

        WO-STORE-EXPIRY-CLAIMABLE + D19: storage_service.sweep_expired_
        lockers also runs under this SAME lock (a third facet of
        "contract expiry" -- see contract_sweeps.py's own docstring) and
        must be mocked too, or it hits _FakeLockDB's real query layer
        (which raises for anything but Galaxy) and the whole sweep rolls
        back instead of committing. sweep_expired_accepted_contracts is
        called with `expiry_gate=gate_contract_expiry_on_locker` now
        (the deposit-wins/deadlock-fix mechanism -- see test_contract_
        service.py's own gate tests) -- since this test mocks the whole
        function, the gate argument is passed through untouched and
        doesn't affect this test's own count-combining assertion."""
        db = _FakeLockDB()
        with patch("src.core.database.SessionLocal", return_value=db), patch(
            "src.services.contract_service.sweep_expired_contracts",
            return_value={"expired": 3},
        ) as mock_posted, patch(
            "src.services.contract_service.sweep_expired_accepted_contracts",
            return_value={"expired": 2},
        ) as mock_accepted, patch(
            "src.services.storage_service.sweep_expired_lockers",
            return_value={"converted": 1},
        ) as mock_lockers:
            result = _run_contract_expire_sweep_sync()
        assert result == 5
        mock_posted.assert_called_once()
        mock_accepted.assert_called_once()
        mock_lockers.assert_called_once()
        assert db.committed is True
        assert db.closed is True

    def test_contract_expire_sweep_wires_the_deposit_wins_gate(self):
        """D19 (deposit-wins REQUIRED): the scheduler must actually PASS
        storage_service.gate_contract_expiry_on_locker as sweep_expired_
        accepted_contracts' own expiry_gate kwarg -- a regression pin
        against silently dropping the gate in a future refactor (which
        would quietly revert to first-committer-wins with no test
        failure anywhere else, since a missing kwarg just falls back to
        the gate's own None-default)."""
        db = _FakeLockDB()
        with patch("src.core.database.SessionLocal", return_value=db), patch(
            "src.services.contract_service.sweep_expired_contracts", return_value={"expired": 0},
        ), patch(
            "src.services.contract_service.sweep_expired_accepted_contracts",
            return_value={"expired": 0},
        ) as mock_accepted, patch(
            "src.services.storage_service.sweep_expired_lockers", return_value={"converted": 0},
        ), patch(
            "src.services.storage_service.gate_contract_expiry_on_locker",
        ) as mock_gate:
            _run_contract_expire_sweep_sync()

        mock_accepted.assert_called_once_with(db, expiry_gate=mock_gate)

    def test_contract_expire_sweep_locked_elsewhere_skips_cleanly_without_touching_the_core(self, caplog):
        """Mirrors test_locked_elsewhere_skips_cleanly_without_touching_the_
        core above (WO-SWEEP-SILENT-SWEEPS discipline applied to the newly
        lock-guarded expire sweep): a lock-busy tick must not reach
        sweep_expired_contracts at all, and must log the skip so it's
        distinguishable, in the log, from a legitimate ran-and-found-
        nothing-due tick."""
        class _LockHeldDB(_FakeLockDB):
            def execute(self, *args, **kwargs):
                return SimpleNamespace(scalar=lambda: False)

        db = _LockHeldDB()
        with caplog.at_level(logging.INFO), patch(
            "src.core.database.SessionLocal", return_value=db,
        ), patch(
            "src.services.contract_service.sweep_expired_contracts",
        ) as mock_core:
            result = _run_contract_expire_sweep_sync()
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
        def _slow_sync_call(cancel_event=None):
            # WO-SCHED-GEN-ORPHAN-CANCEL: _contract_generation_loop now
            # always passes its own cancel_event positionally — accept it
            # (unused here) so this stand-in matches the real call shape.
            time.sleep(0.4)
            return 0

        monkeypatch.setattr(
            "src.services.scheduler.core_loop._run_contract_generation_sync",
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
        from src.services.scheduler import core_loop

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

        # WO-QUALITY-techdebt-scheduler-split: npc_scheduler_loop now lives in
        # core_loop.py and resolves these names via ITS OWN module globals (they
        # moved there together) -- patch core_loop, not the shim.
        monkeypatch.setattr(core_loop, "_contract_generation_loop", _fake_contract_generation_loop)
        monkeypatch.setattr(core_loop, "_npc_scheduler_main_loop", _fake_main_loop)
        for name in (
            "_repair_orphan_schedules_sync", "_seed_trader_rosters_sync",
            "_bulk_fill_traders_sync", "_assign_trader_notoriety_sync",
            "_relocate_stranded_npcs_sync", "_disperse_law_patrols_sync",
        ):
            monkeypatch.setattr(core_loop, name, lambda: 0)
        monkeypatch.setattr(core_loop, "_assign_trader_missions_sync", lambda: {})

        await svc.npc_scheduler_loop()

        assert gen_loop_started.is_set()
        assert gen_loop_cancelled.is_set()

    def test_run_contract_generation_sync_uses_three_separate_short_sessions(self, monkeypatch):
        """WO-SCHED-LOOP-WEDGE structural falsifier: the pure-Python
        compute phase must run AFTER the read session has already
        closed, and the write phase must open a THIRD, separate session
        — proving no open transaction spans the compute (the exact 'idle
        in transaction for 28+ minutes' shape the orchestrator's live
        capture on heimdall found)."""
        import src.services.npc_scheduler_service as svc
        from src.services.scheduler import contract_sweeps

        events: list = []

        class _TrackedDB:
            def __init__(self, label):
                self.label = label
                events.append(("open", label))

            def execute(self, *a, **k):
                return SimpleNamespace(scalar=lambda: True)

            def commit(self):
                events.append(("commit", self.label))

            def rollback(self):
                events.append(("rollback", self.label))

            def close(self):
                events.append(("close", self.label))

        labels = iter(["peek", "read", "write"])
        monkeypatch.setattr(
            "src.core.database.SessionLocal", lambda: _TrackedDB(next(labels)),
        )
        # _run_contract_generation_sync (contract_sweeps.py) resolves these via
        # its OWN imported copy from _common -- patch there, not the shim.
        monkeypatch.setattr(contract_sweeps, "_sweep_is_due", lambda db, *a, **k: True)
        monkeypatch.setattr(contract_sweeps, "_sweep_due_and_advance", lambda db, *a, **k: True)

        def _fake_gather(db):
            events.append(("gather", db.label))
            return "INPUTS-SENTINEL"

        def _fake_compute(inputs):
            assert inputs == "INPUTS-SENTINEL"
            events.append(("compute", None))  # no db argument at all
            return "BATCH-SENTINEL"

        def _fake_write(db, batch, now=None):
            assert batch == "BATCH-SENTINEL"
            events.append(("write", db.label))
            return 7

        monkeypatch.setattr(
            "src.services.contract_generator.gather_contract_generation_inputs", _fake_gather,
        )
        monkeypatch.setattr(
            "src.services.contract_generator.compute_contract_generation_batch", _fake_compute,
        )
        monkeypatch.setattr(
            "src.services.contract_generator.write_contract_generation_batch", _fake_write,
        )

        result = svc._run_contract_generation_sync()

        assert result == 7
        assert [lbl for (kind, lbl) in events if kind == "open"] == ["peek", "read", "write"]
        read_close_idx = events.index(("close", "read"))
        compute_idx = events.index(("compute", None))
        write_open_idx = events.index(("open", "write"))
        assert read_close_idx < compute_idx, "read session still open when compute started"
        assert write_open_idx > compute_idx, "write session opened before compute finished"
        assert events[-2] == ("commit", "write")
        assert events[-1] == ("close", "write")

    def test_write_phase_lock_busy_skips_write_without_running_generation(self, monkeypatch, caplog):
        """WO-SCHED-GEN-LOCK: a not-acquired _CONTRACT_GENERATION_LOCK_KEY
        must return 0 WITHOUT calling write_contract_generation_batch (no
        double-write across gameserver instances), and — matching the
        WO-SWEEP-SILENT-SWEEPS observability lesson — must log the skip
        rather than returning silently."""
        import src.services.npc_scheduler_service as svc
        from src.services.scheduler import contract_sweeps

        class _LockBusyDB:
            def __init__(self):
                self.closed = False

            def execute(self, *a, **k):
                return SimpleNamespace(scalar=lambda: False)  # lock held elsewhere

            def close(self):
                self.closed = True

        monkeypatch.setattr("src.core.database.SessionLocal", lambda: _LockBusyDB())
        monkeypatch.setattr(contract_sweeps, "_sweep_is_due", lambda db, *a, **k: True)
        monkeypatch.setattr(
            "src.services.contract_generator.gather_contract_generation_inputs",
            lambda db: "INPUTS-SENTINEL",
        )
        monkeypatch.setattr(
            "src.services.contract_generator.compute_contract_generation_batch",
            lambda inputs: "BATCH-SENTINEL",
        )
        write_mock_calls = []
        monkeypatch.setattr(
            "src.services.contract_generator.write_contract_generation_batch",
            lambda db, batch, now=None: write_mock_calls.append(batch) or 99,
        )

        with caplog.at_level(logging.INFO):
            result = svc._run_contract_generation_sync()

        assert result == 0
        assert write_mock_calls == []
        assert any(
            "lock busy, skipped" in r.getMessage() for r in caplog.records
        ), [r.getMessage() for r in caplog.records]


# ---------------------------------------------------------------------------
# WO-SCHED-GEN-ORPHAN-CANCEL (F1) — asyncio.to_thread's underlying OS thread
# can't be interrupted once running: cancelling the awaiting
# _contract_generation_loop task returns control to the event loop in ~1ms,
# but the thread running _run_contract_generation_sync keeps executing —
# straight into the write phase's advisory-lock acquisition + open write
# transaction if nothing stops it. cancel_event is the guard: set from the
# async side the instant cancellation is observed, checked at every phase
# boundary in the sync thread, before the write phase (the lock/txn holder)
# is ever entered.
# ---------------------------------------------------------------------------

class TestContractGenerationCancelEvent:
    def test_cancel_observed_mid_flight_skips_write_phase_and_lock_entirely(self, monkeypatch):
        """The exact race F1 describes: cancellation lands WHILE the
        orphaned thread is still inside compute — simulated here by having
        the compute stand-in itself flip cancel_event, standing in for the
        async side calling cancel_event.set() concurrently while this
        thread runs. The write session must never even be OPENED, so the
        CGEN advisory lock (acquired via write_db.execute(...)) is
        structurally unreachable, not just unused, and
        write_contract_generation_batch must never run."""
        import src.services.npc_scheduler_service as svc
        from src.services.scheduler import contract_sweeps

        events: list = []
        cancel_event = threading.Event()
        opened_labels = ["peek", "read"]  # a "write" session must NEVER open
        open_count = [0]

        class _TrackedDB:
            def __init__(self, label):
                self.label = label
                events.append(("open", label))

            def execute(self, *a, **k):
                events.append(("execute", self.label))
                return SimpleNamespace(scalar=lambda: True)

            def commit(self):
                events.append(("commit", self.label))

            def rollback(self):
                events.append(("rollback", self.label))

            def close(self):
                events.append(("close", self.label))

        def _open_db():
            idx = open_count[0]
            open_count[0] += 1
            label = opened_labels[idx] if idx < len(opened_labels) else f"UNEXPECTED-{idx}"
            return _TrackedDB(label)

        monkeypatch.setattr("src.core.database.SessionLocal", _open_db)
        monkeypatch.setattr(contract_sweeps, "_sweep_is_due", lambda db, *a, **k: True)

        def _fake_gather(db):
            events.append(("gather", None))
            return "INPUTS-SENTINEL"

        def _fake_compute(inputs):
            assert inputs == "INPUTS-SENTINEL"
            events.append(("compute", None))
            # Stand-in for the async side observing this task's own
            # cancellation and calling cancel_event.set() WHILE this
            # thread is still mid-flight, ahead of the write phase.
            cancel_event.set()
            return "BATCH-SENTINEL"

        write_called = []

        def _fake_write(db, batch, now=None):
            write_called.append(True)
            return 1

        monkeypatch.setattr(
            "src.services.contract_generator.gather_contract_generation_inputs", _fake_gather,
        )
        monkeypatch.setattr(
            "src.services.contract_generator.compute_contract_generation_batch", _fake_compute,
        )
        monkeypatch.setattr(
            "src.services.contract_generator.write_contract_generation_batch", _fake_write,
        )

        result = svc._run_contract_generation_sync(cancel_event)

        # write_contract_generation_batch is asserted via a call-tracking
        # list rather than a raise-inside-the-mock: _run_contract_
        # generation_sync's own write phase wraps its body in `except
        # Exception:` — a raise from inside the mock would just be
        # swallowed there and silently produce the SAME result==0, proving
        # nothing about whether the write phase actually ran.
        assert result == 0
        assert write_called == [], f"write phase ran once cancel_event was set: {events}"
        assert ("compute", None) in events, "compute phase should still have run before cancellation landed"
        assert all(lbl != "write" for (kind, lbl) in events if kind == "open"), \
            f"write session was opened after cancellation — the CGEN lock became reachable: {events}"
        assert not any(kind == "execute" for kind, _ in events), \
            f"the CGEN advisory lock was acquired after cancellation: {events}"

    def test_cancel_preset_before_the_call_skips_gather_and_compute_too(self, monkeypatch):
        """A coarser falsifier for the same guard: cancel_event already
        set BEFORE the call even starts must stop the pass immediately
        after peek — never reaching the (comparatively expensive)
        gather/compute phases at all, not just write.

        Uses call-tracking rather than a raise-inside-the-mock for the
        same reason as the mid-flight test above: gather/compute/write
        each run inside the sync function's own `except Exception:`, which
        would silently swallow a raise and still report result == 0 even
        if the phase actually ran."""
        import src.services.npc_scheduler_service as svc
        from src.services.scheduler import contract_sweeps

        cancel_event = threading.Event()
        cancel_event.set()

        peek_db = SimpleNamespace(close=lambda: None)
        monkeypatch.setattr("src.core.database.SessionLocal", lambda: peek_db)
        monkeypatch.setattr(contract_sweeps, "_sweep_is_due", lambda db, *a, **k: True)

        called = {"gather": False, "compute": False, "write": False}

        monkeypatch.setattr(
            "src.services.contract_generator.gather_contract_generation_inputs",
            lambda db: called.__setitem__("gather", True) or "INPUTS-SENTINEL",
        )
        monkeypatch.setattr(
            "src.services.contract_generator.compute_contract_generation_batch",
            lambda inputs: called.__setitem__("compute", True) or "BATCH-SENTINEL",
        )
        monkeypatch.setattr(
            "src.services.contract_generator.write_contract_generation_batch",
            lambda db, batch, now=None: called.__setitem__("write", True) or 1,
        )

        result = svc._run_contract_generation_sync(cancel_event)
        assert result == 0
        assert called == {"gather": False, "compute": False, "write": False}, called

    def test_default_none_cancel_event_behaves_exactly_as_before(self, monkeypatch):
        """The default (no Event passed) must never gate anything — every
        pre-F1 caller/test keeps working unmodified."""
        import src.services.npc_scheduler_service as svc
        from src.services.scheduler import contract_sweeps

        monkeypatch.setattr(contract_sweeps, "_sweep_is_due", lambda db, *a, **k: True)
        db_stub = SimpleNamespace(
            execute=lambda *a, **k: SimpleNamespace(scalar=lambda: True),
            commit=lambda: None, rollback=lambda: None, close=lambda: None,
        )
        monkeypatch.setattr("src.core.database.SessionLocal", lambda: db_stub)
        monkeypatch.setattr(contract_sweeps, "_sweep_due_and_advance", lambda db, *a, **k: True)
        monkeypatch.setattr(
            "src.services.contract_generator.gather_contract_generation_inputs",
            lambda db: "INPUTS-SENTINEL",
        )
        monkeypatch.setattr(
            "src.services.contract_generator.compute_contract_generation_batch",
            lambda inputs: "BATCH-SENTINEL",
        )
        monkeypatch.setattr(
            "src.services.contract_generator.write_contract_generation_batch",
            lambda db, batch, now=None: 5,
        )

        assert svc._run_contract_generation_sync() == 5
        assert svc._run_contract_generation_sync(None) == 5


# ---------------------------------------------------------------------------
# WO-SCHED-GEN-PEEK-LOG (F2) — peek was the only one of the four phases
# (peek/gather/compute/write) without its own try/except + phase-attributed
# log; a peek crash landed in the generic loop handler with no phase
# attribution, breaking the SILENT-SWEEPS observability discipline the
# other three phases already enforce (see gather/compute/write's own
# logger.exception(...) calls in _run_contract_generation_sync).
# ---------------------------------------------------------------------------

class TestContractGenerationPeekPhaseObservability:
    def test_peek_phase_exception_is_caught_logged_and_returns_zero(self, monkeypatch, caplog):
        import src.services.npc_scheduler_service as svc
        from src.services.scheduler import contract_sweeps

        class _RaisingPeekDB:
            def __init__(self):
                self.closed = False

            def close(self):
                self.closed = True

        db = _RaisingPeekDB()
        monkeypatch.setattr("src.core.database.SessionLocal", lambda: db)

        def _boom(*a, **k):
            raise RuntimeError("peek boom")

        monkeypatch.setattr(contract_sweeps, "_sweep_is_due", _boom)

        with caplog.at_level(logging.INFO):
            result = svc._run_contract_generation_sync()

        assert result == 0
        assert db.closed is True
        assert any(
            "peek phase failed" in r.getMessage() for r in caplog.records
        ), [r.getMessage() for r in caplog.records]

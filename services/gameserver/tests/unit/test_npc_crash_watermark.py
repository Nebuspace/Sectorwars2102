"""P9-realtime-npc-crash-watermark (2026-07-16): per-loop crash-recovery
watermark + bounded restart catch-up, per npc-scheduler.md's "Crash
recovery" section (previously a 📐 design-only marker). Extends the
durable Galaxy.state sweep-anchor discipline (_read_sweep_anchor /
_sweep_due_and_advance, WO-SCHED-CADENCE-DRIFT) to Loop A/B/C's own
last-completed-cycle marker -- no migration, same JSONB row.

Follows the SAME fake-session convention test_npc_scheduler_unit.py's own
TestSweepDueAndAdvance / _FakeDurableCadenceDB already established for this
exact durable-anchor mechanism: one persistent Galaxy row shared across
every fresh fake-session instance, mirroring a real fresh SessionLocal()
reading the same durable row after a process restart.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest

from src.models.galaxy import Galaxy
from src.services.scheduler import presence_helpers
from src.services.scheduler._common import (
    LOOP_A_CRASH_CATCHUP_MAX_TICKS,
    LOOP_A_SECONDS,
    LOOP_A_WATERMARK_STATE_KEY,
    LOOP_B_SECONDS,
    LOOP_B_WATERMARK_STATE_KEY,
    LOOP_C_WATERMARK_STATE_KEY,
    _loop_a_catchup_ticks,
    _read_sweep_anchor,
    _stamp_loop_watermark,
)


class _FakeWatermarkDB:
    """Shares ONE Galaxy row across every fresh instance -- mirrors a real
    fresh SessionLocal() reading the same durable row, exactly like
    test_npc_scheduler_unit.py's own _FakeDurableCadenceDB."""

    def __init__(self, galaxy: Galaxy):
        self.galaxy = galaxy
        self.commit_count = 0
        self.rollback_count = 0
        self.closed = False

    def execute(self, *_a, **_k):
        return SimpleNamespace(scalar=lambda: True)  # advisory lock always free

    def query(self, *entities, **_k):
        if entities and entities[0] is Galaxy:
            galaxy = self.galaxy
            return SimpleNamespace(order_by=lambda *a, **k: SimpleNamespace(first=lambda: galaxy))
        raise AssertionError(f"unexpected query entities: {entities!r}")

    def commit(self):
        self.commit_count += 1

    def rollback(self):
        self.rollback_count += 1

    def close(self):
        self.closed = True


@pytest.mark.unit
class TestLoopACatchupTicks:
    def test_fresh_galaxy_no_watermark_yields_zero(self) -> None:
        galaxy = Galaxy(id=uuid4(), created_at=datetime(2020, 1, 1, tzinfo=UTC), state={})
        assert _loop_a_catchup_ticks(_FakeWatermarkDB(galaxy), datetime.now(UTC)) == 0

    def test_small_gap_is_uncapped_and_exact(self) -> None:
        t0 = datetime(2026, 1, 1, tzinfo=UTC)
        galaxy = Galaxy(
            id=uuid4(), created_at=datetime(2020, 1, 1, tzinfo=UTC),
            state={LOOP_A_WATERMARK_STATE_KEY: (t0 - timedelta(seconds=600)).isoformat()},
        )
        # 600s gap / LOOP_A_SECONDS(60) = 10 missed ticks, well under the cap.
        assert _loop_a_catchup_ticks(_FakeWatermarkDB(galaxy), t0) == 10

    def test_six_hour_gap_is_bounded_by_the_catchup_cap(self) -> None:
        """The Accept criterion's exact scenario: stamp Loop A's watermark
        6h in the past. 6h / 60s = 360 missed ticks -- must clamp to
        LOOP_A_CRASH_CATCHUP_MAX_TICKS, not replay all 360 synchronously
        (the GIL/burst-length caution this WO was briefed with)."""
        t0 = datetime(2026, 1, 1, tzinfo=UTC)
        galaxy = Galaxy(
            id=uuid4(), created_at=datetime(2020, 1, 1, tzinfo=UTC),
            state={LOOP_A_WATERMARK_STATE_KEY: (t0 - timedelta(hours=6)).isoformat()},
        )
        assert _loop_a_catchup_ticks(_FakeWatermarkDB(galaxy), t0) == LOOP_A_CRASH_CATCHUP_MAX_TICKS


@pytest.mark.unit
class TestStampLoopWatermark:
    def test_stamps_the_given_key_to_the_given_instant(self) -> None:
        galaxy = Galaxy(id=uuid4(), created_at=datetime(2020, 1, 1, tzinfo=UTC), state={})
        db = _FakeWatermarkDB(galaxy)
        at = datetime(2026, 3, 1, 12, 0, 0, tzinfo=UTC)

        _stamp_loop_watermark(db, LOOP_B_WATERMARK_STATE_KEY, at)

        _, read_back = _read_sweep_anchor(db, LOOP_B_WATERMARK_STATE_KEY)
        assert read_back == at

    def test_does_not_disturb_other_loops_watermarks(self) -> None:
        t0 = datetime(2026, 1, 1, tzinfo=UTC)
        galaxy = Galaxy(
            id=uuid4(), created_at=datetime(2020, 1, 1, tzinfo=UTC),
            state={LOOP_A_WATERMARK_STATE_KEY: t0.isoformat()},
        )
        db = _FakeWatermarkDB(galaxy)
        _stamp_loop_watermark(db, LOOP_B_WATERMARK_STATE_KEY, t0 + timedelta(minutes=5))

        _, loop_a = _read_sweep_anchor(db, LOOP_A_WATERMARK_STATE_KEY)
        assert loop_a == t0  # untouched


@pytest.mark.unit
class TestRunLoopCrashCatchupSync:
    """The full Accept-criterion proof: stamp Loop A's watermark 6h in the
    past, run the catch-up entry point once -- assert (a) Loop A's real
    per-tick driver is invoked exactly the bounded-cap number of times with
    consecutive tick values (the same distinctness property continuous
    operation would have produced), (b) NO events are ever collected/
    returned anywhere (structural: the function's own return type carries
    only counts, never an events list -- there is no path for a catch-up
    frame to reach _broadcast_events at all), (c) the watermark stamps
    forward to the boundary actually reached, and (d) that stamp lands in
    the SAME transaction/commit as the replayed work."""

    def _seed(self, hours_ago: float = 6.0):
        now = datetime.now(UTC)
        watermark = now - timedelta(hours=hours_ago)
        galaxy = Galaxy(
            id=uuid4(), created_at=datetime(2020, 1, 1, tzinfo=UTC),
            state={LOOP_A_WATERMARK_STATE_KEY: watermark.isoformat()},
        )
        return now, watermark, galaxy

    def test_bounded_replay_zero_broadcast_frames_atomic_watermark_stamp(self, monkeypatch) -> None:
        now, watermark, galaxy = self._seed(hours_ago=6.0)
        sessions: list = []

        def _session_factory():
            db = _FakeWatermarkDB(galaxy)
            sessions.append(db)
            return db

        monkeypatch.setattr("src.core.database.SessionLocal", _session_factory)

        calls_a: list = []

        def fake_run_loop_a(db, tick=0):
            calls_a.append(tick)
            # A real per-tick call would return leg-started/leg-halted
            # events -- returned here to prove the catch-up caller
            # actually DISCARDS them (never appears in the result).
            return [{"type": "npc_leg_started", "would_be_stale": True}]

        monkeypatch.setattr(presence_helpers, "run_loop_a", fake_run_loop_a)
        monkeypatch.setattr(presence_helpers, "run_loop_b", lambda db: [{"type": "npc_roster_event"}])
        monkeypatch.setattr(presence_helpers, "run_loop_c", lambda db: [])

        result = presence_helpers._run_loop_crash_catchup_sync()

        # (a) bounded replay, consecutive tick values -- exactly the
        # LOOP_A_CRASH_CATCHUP_MAX_TICKS cap, since 6h of downtime vastly
        # exceeds it.
        assert calls_a == list(range(1, LOOP_A_CRASH_CATCHUP_MAX_TICKS + 1))
        assert result["loop_a_ticks_replayed"] == LOOP_A_CRASH_CATCHUP_MAX_TICKS

        # (b) zero broadcast frames anywhere -- structural: the return
        # value carries only counts, never an events list, so there is no
        # path for a catch-up frame to reach _broadcast_events at all.
        assert set(result.keys()) == {
            "loop_a_ticks_replayed", "loop_b_caught_up", "loop_c_caught_up",
        }
        assert all(isinstance(v, int) for v in result.values())

        # (c) watermark stamped to the boundary ACTUALLY reached (bounded
        # replay * interval past the ORIGINAL watermark), not all the way
        # to `now` -- the untraveled remainder of the 6h gap stays visible
        # to the next catch-up check instead of being silently dropped.
        _, stamped = _read_sweep_anchor(_FakeWatermarkDB(galaxy), LOOP_A_WATERMARK_STATE_KEY)
        expected = watermark + timedelta(seconds=LOOP_A_CRASH_CATCHUP_MAX_TICKS * LOOP_A_SECONDS)
        assert stamped == expected
        assert stamped < now  # bounded -- did NOT fully catch up to now in one wake

        # (d) same-transaction contract: exactly one commit on Loop A's own
        # work session, covering both the replayed work and the stamp.
        loop_a_session = sessions[1]  # index 0 = lock_db, 1 = Loop A's work_db
        assert loop_a_session.commit_count == 1
        assert loop_a_session.rollback_count == 0

        # Loop B/C: first-ever watermark (none seeded) -- due immediately,
        # single pass each, also fully atomic and event-suppressed.
        assert result["loop_b_caught_up"] == 1
        assert result["loop_c_caught_up"] == 1

    def test_crash_mid_replay_rolls_back_the_whole_bounded_batch(self, monkeypatch) -> None:
        """same-transaction / crash-between proof, the _sweep_due_and_
        advance contract applied to Loop A's own bounded batch: an
        exception partway through the replay must leave the watermark at
        its ORIGINAL value -- never partially advanced -- so the next
        restart retries the SAME bounded catch-up from scratch."""
        now, watermark, galaxy = self._seed(hours_ago=1.0)  # 60 missed ticks, capped at 24
        sessions: list = []

        def _session_factory():
            db = _FakeWatermarkDB(galaxy)
            sessions.append(db)
            return db

        monkeypatch.setattr("src.core.database.SessionLocal", _session_factory)

        calls_a: list = []

        def crashing_run_loop_a(db, tick=0):
            calls_a.append(tick)
            if tick == 3:
                raise RuntimeError("simulated crash mid-replay")
            return []

        monkeypatch.setattr(presence_helpers, "run_loop_a", crashing_run_loop_a)
        monkeypatch.setattr(presence_helpers, "run_loop_b", lambda db: [])
        monkeypatch.setattr(presence_helpers, "run_loop_c", lambda db: [])

        result = presence_helpers._run_loop_crash_catchup_sync()

        assert calls_a == [1, 2, 3]  # stopped exactly at the crash, never continued
        assert result["loop_a_ticks_replayed"] == 0  # exception path never sets this

        # Watermark untouched -- still the ORIGINAL stale value, not
        # partially advanced to tick 2's boundary.
        _, stamped = _read_sweep_anchor(_FakeWatermarkDB(galaxy), LOOP_A_WATERMARK_STATE_KEY)
        assert stamped == watermark

        loop_a_session = sessions[1]
        assert loop_a_session.commit_count == 0
        assert loop_a_session.rollback_count == 1

    def test_nothing_missed_is_a_clean_noop_for_loop_a(self, monkeypatch) -> None:
        """Watermark freshly stamped (no gap) -- catch-up must not replay
        anything or touch the watermark at all."""
        now = datetime.now(UTC)
        galaxy = Galaxy(
            id=uuid4(), created_at=datetime(2020, 1, 1, tzinfo=UTC),
            state={
                LOOP_A_WATERMARK_STATE_KEY: now.isoformat(),
                LOOP_B_WATERMARK_STATE_KEY: now.isoformat(),
                LOOP_C_WATERMARK_STATE_KEY: now.isoformat(),
            },
        )
        sessions: list = []

        def _session_factory():
            db = _FakeWatermarkDB(galaxy)
            sessions.append(db)
            return db

        monkeypatch.setattr("src.core.database.SessionLocal", _session_factory)

        calls_a: list = []
        monkeypatch.setattr(presence_helpers, "run_loop_a", lambda db, tick=0: calls_a.append(tick) or [])
        monkeypatch.setattr(presence_helpers, "run_loop_b", lambda db: [])
        monkeypatch.setattr(presence_helpers, "run_loop_c", lambda db: [])

        result = presence_helpers._run_loop_crash_catchup_sync()

        assert calls_a == []
        assert result == {"loop_a_ticks_replayed": 0, "loop_b_caught_up": 0, "loop_c_caught_up": 0}
        # Loop A's own session rolled back cleanly (nothing to do), never committed.
        loop_a_session = sessions[1]
        assert loop_a_session.commit_count == 0
        assert loop_a_session.rollback_count == 1

    def test_advisory_lock_held_elsewhere_skips_catchup_entirely(self, monkeypatch) -> None:
        galaxy = Galaxy(
            id=uuid4(), created_at=datetime(2020, 1, 1, tzinfo=UTC),
            state={LOOP_A_WATERMARK_STATE_KEY: (datetime.now(UTC) - timedelta(hours=6)).isoformat()},
        )

        class _LockHeldDB(_FakeWatermarkDB):
            def execute(self, *_a, **_k):
                return SimpleNamespace(scalar=lambda: False)  # lock NOT acquired

        monkeypatch.setattr("src.core.database.SessionLocal", lambda: _LockHeldDB(galaxy))

        calls_a: list = []
        monkeypatch.setattr(presence_helpers, "run_loop_a", lambda db, tick=0: calls_a.append(tick) or [])

        result = presence_helpers._run_loop_crash_catchup_sync()

        assert calls_a == []
        assert result == {"loop_a_ticks_replayed": 0, "loop_b_caught_up": 0, "loop_c_caught_up": 0}

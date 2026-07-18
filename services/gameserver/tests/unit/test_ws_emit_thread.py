"""QUEUE-ISP-WS-EMIT-THREAD: emit_leg_started/emit_leg_halted must never
raise "no current event loop" from tick_npc_legs's worker thread (core_loop.
py dispatches Loop A's whole tick body via asyncio.to_thread), and must not
spam a full traceback at DEBUG on every leg for the two EXPECTED lifecycle
states (no loop captured yet / loop closed).

A plain synchronous pytest test function has no running event loop either
(same as a ThreadPoolExecutor worker thread) — that's exactly what
_resolve_broadcast_loop's fallback path needs to be exercised, without any
real threading. This is the "fake-loop unit proving the emit path is
invoked" the WO asks for: a MagicMock stand-in loop + a patched
asyncio.run_coroutine_threadsafe, asserting the NEW path actually fires
(not the old raise-then-swallow path).
"""
from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pytest

from src.services import intrasystem_movement_service as isp

POSE = {
    "x_pct": 10.0, "y_pct": 10.0, "heading_deg": 0.0,
    "phase": "orienting", "burning": False, "leg": None,
}


@pytest.fixture(autouse=True)
def _reset_scheduler_loop():
    """Module-level global -- isolate each test from the others."""
    isp.set_scheduler_event_loop(None)
    yield
    isp.set_scheduler_event_loop(None)


def test_emit_leg_started_schedules_via_captured_loop_no_running_loop_needed(monkeypatch):
    """The core regression: called with NO running loop in this thread
    (exactly tick_npc_legs's own ThreadPoolExecutor situation) must NOT
    raise, and must actually invoke run_coroutine_threadsafe against the
    loop captured at scheduler startup -- not silently no-op, not raise."""
    fake_loop = MagicMock()
    fake_loop.is_closed.return_value = False
    isp.set_scheduler_event_loop(fake_loop)

    scheduled = {}

    def fake_run_coroutine_threadsafe(coro, loop):
        scheduled["coro"] = coro
        scheduled["loop"] = loop
        coro.close()  # avoid an "unawaited coroutine" ResourceWarning
        return MagicMock()

    monkeypatch.setattr(asyncio, "run_coroutine_threadsafe", fake_run_coroutine_threadsafe)

    isp.emit_leg_started(1, "ship-1", True, POSE)  # must not raise

    assert scheduled.get("loop") is fake_loop
    assert scheduled.get("coro") is not None


def test_emit_leg_halted_uses_the_same_shared_path(monkeypatch):
    """emit_leg_halted (the /helm/intrasystem/halt route's own emit,
    formerly a duplicated inline copy of the same buggy pattern) shares the
    identical fix."""
    fake_loop = MagicMock()
    fake_loop.is_closed.return_value = False
    isp.set_scheduler_event_loop(fake_loop)

    scheduled = {}

    def fake_run_coroutine_threadsafe(coro, loop):
        scheduled["type"] = None
        scheduled["loop"] = loop
        coro.close()
        return MagicMock()

    monkeypatch.setattr(asyncio, "run_coroutine_threadsafe", fake_run_coroutine_threadsafe)

    isp.emit_leg_halted(1, "ship-2", False, POSE)  # must not raise
    assert scheduled.get("loop") is fake_loop


def test_emit_leg_started_prefers_a_real_running_loop_over_the_captured_one(monkeypatch):
    """A caller that DOES have a running loop on its own thread (a real
    async route handler, e.g. /helm/intrasystem/burn) must use THAT loop,
    not the one captured at scheduler startup — correct even if the
    scheduler is disabled and never captured one at all."""
    captured = {}

    def fake_run_coroutine_threadsafe(coro, loop):
        captured["loop"] = loop
        coro.close()
        return MagicMock()

    monkeypatch.setattr(asyncio, "run_coroutine_threadsafe", fake_run_coroutine_threadsafe)
    isp.set_scheduler_event_loop(None)  # scheduler never captured one

    async def _call_from_inside_a_running_loop():
        isp.emit_leg_started(1, "ship-3", True, POSE)
        return asyncio.get_running_loop()

    real_loop = asyncio.new_event_loop()
    try:
        the_loop = real_loop.run_until_complete(_call_from_inside_a_running_loop())
    finally:
        real_loop.close()

    assert captured.get("loop") is the_loop


def test_emit_leg_started_no_captured_loop_skips_quietly_no_traceback_spam(monkeypatch):
    """QUEUE-ISP-WS-EMIT-THREAD's other ask: kill the per-leg DEBUG
    traceback spam. The expected 'no loop yet' lifecycle state logs a
    short line WITHOUT exc_info=True — not a full traceback on every leg."""
    calls = []
    monkeypatch.setattr(isp.logger, "debug", lambda *a, **k: calls.append((a, k)))

    isp.emit_leg_started(1, "ship-4", True, POSE)  # must not raise

    assert len(calls) == 1
    _args, kwargs = calls[0]
    assert kwargs.get("exc_info") is not True


def test_emit_leg_started_closed_loop_skips_quietly():
    fake_loop = MagicMock()
    fake_loop.is_closed.return_value = True
    isp.set_scheduler_event_loop(fake_loop)

    isp.emit_leg_started(1, "ship-5", True, POSE)  # must not raise

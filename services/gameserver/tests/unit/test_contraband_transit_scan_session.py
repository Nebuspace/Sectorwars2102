"""WO-K2 regression — the transit-scan hook must not end the caller's
transaction on the common path.

THE BUG THIS PINS (caught by ci-core-loop-playthrough on 011308a5, 2026-08-03).
The first revision of ``MovementService._roll_contraband_transit_scan`` evaluated
the [OPEN-9] cooldown gate *under* the player/ship ``FOR UPDATE`` lock. That meant
a declined scan could return still holding locks, so the hook called
``self.db.rollback()`` to release them — on the caller's own session, on the path
taken by *nearly every jump in the game* (a clean hold declines with
``no_contraband``).

``Session.rollback()`` expires every ORM instance the request is still using, and
``api/routes/player.py:653`` reads ``player.turns`` as a **default argument** —
so it is evaluated on every single move, immediately after this hook. Result:
``POST /player/move/{id}`` → 500, ``ObjectDeletedError``.

``_roll_mechanical_failure`` looks like it does the same thing and does not: its
``rollback()`` lives only in an exception handler, so it never runs on a healthy
request. That difference is the whole bug.

THE FIX: every gate is evaluated before any lock is taken, so the common path
acquires nothing and the hook leaves the transaction alone. Where a lock *is*
taken it is released with ``commit()`` — which this request path demonstrably
survives, since ``_execute_movement`` commits mid-request and the route reads
``player`` afterwards.

These tests assert the CONTRACT rather than the plumbing: which of
commit/rollback the hook is allowed to call, for each outcome shape. A fake
session records the calls, so no DB is needed and the assertion is exact.
"""
from types import SimpleNamespace
from uuid import uuid4

import pytest

from src.services.movement_service import MovementService


class RecordingSession:
    """Records transaction-control calls. Everything else is inert."""

    def __init__(self):
        self.calls = []

    def commit(self):
        self.calls.append("commit")

    def rollback(self):
        self.calls.append("rollback")

    def query(self, *_a, **_kw):  # pragma: no cover - not reached in these tests
        raise AssertionError("scan_in_transit was not stubbed out")


@pytest.fixture
def svc_and_session(monkeypatch):
    session = RecordingSession()
    svc = MovementService.__new__(MovementService)  # no __init__ / no DB
    svc.db = session
    return svc, session


def _stub_scan(monkeypatch, outcome):
    """Replace ContrabandService.scan_in_transit with a canned outcome.

    Patched on the class, since the hook imports it lazily inside the call.
    """
    from src.services import contraband_service

    monkeypatch.setattr(
        contraband_service.ContrabandService,
        "scan_in_transit",
        lambda self, **kw: outcome,
    )


def _player():
    return SimpleNamespace(id=uuid4(), current_ship_id=uuid4(), turns=10)


# Every no-scan reason the gate chain can produce BEFORE taking a lock. These are
# the hot paths — a clean hold is the normal state of almost every ship.
@pytest.mark.parametrize(
    "reason",
    ["no_contraband", "not_higher_security", "cooldown", "same_sector",
     "unknown_sector", "ship_not_found"],
)
def test_unlocked_decline_touches_neither_commit_nor_rollback(
    svc_and_session, monkeypatch, reason
):
    svc, session = svc_and_session
    _stub_scan(monkeypatch, {"scanned": False, "locked": False, "reason": reason})
    result = {}

    svc._roll_contraband_transit_scan(_player(), 1, 2, result)

    assert session.calls == [], (
        f"declined with '{reason}' before locking, so the hook must leave the "
        f"caller's transaction alone — got {session.calls}"
    )
    assert "contraband_scan" not in result


@pytest.mark.parametrize("reason", ["cooldown_race", "no_contraband", "ship_not_found"])
def test_locked_decline_commits_and_never_rolls_back(
    svc_and_session, monkeypatch, reason
):
    """A lock WAS taken, so the transaction must end — but with commit.
    rollback here would expire the caller's ORM instances (the original bug)."""
    svc, session = svc_and_session
    _stub_scan(monkeypatch, {"scanned": False, "locked": True, "reason": reason})
    result = {}

    svc._roll_contraband_transit_scan(_player(), 1, 2, result)

    assert session.calls == ["commit"], f"expected exactly one commit, got {session.calls}"
    assert "rollback" not in session.calls
    assert "contraband_scan" not in result


def test_clean_scan_commits_and_stays_invisible_to_the_player(
    svc_and_session, monkeypatch
):
    """A scan that happened but found nothing still writes the cooldown anchor,
    so it must commit — and must NOT surface, or it leaks the security topology
    the roll is scored on."""
    svc, session = svc_and_session
    _stub_scan(monkeypatch, {
        "scanned": True, "locked": True, "detected": False, "reason": None,
    })
    result = {}

    svc._roll_contraband_transit_scan(_player(), 1, 2, result)

    assert session.calls == ["commit"]
    assert "contraband_scan" not in result


def test_bust_commits_and_surfaces(svc_and_session, monkeypatch):
    svc, session = svc_and_session
    _stub_scan(monkeypatch, {
        "scanned": True, "locked": True, "detected": True, "reason": "detected",
        "commodity": "WEAPONS", "confiscated_units": 4, "confiscated_value": 800,
        "fine": 3200, "heat": "wanted", "remaining_credits": 100,
    })
    result = {}

    svc._roll_contraband_transit_scan(_player(), 1, 2, result)

    assert session.calls == ["commit"]
    assert result["contraband_scan"]["detected"] is True
    assert result["contraband_scan"]["fine"] == 3200
    assert "3,200" in result["contraband_scan"]["message"]


def test_a_raising_scan_never_escapes_into_an_already_committed_move(
    svc_and_session, monkeypatch
):
    """The move is durable before this hook runs. A scan failure must degrade to
    a log line, never a 500 on a jump that already happened. rollback IS correct
    here — the session may be unusable, and this path is rare rather than
    every-request."""
    svc, session = svc_and_session
    from src.services import contraband_service

    def _boom(self, **kw):
        raise RuntimeError("simulated scan failure")

    monkeypatch.setattr(
        contraband_service.ContrabandService, "scan_in_transit", _boom
    )
    result = {"success": True}

    svc._roll_contraband_transit_scan(_player(), 1, 2, result)  # must not raise

    assert session.calls == ["rollback"]
    assert result == {"success": True}, "a failed scan must not alter the move result"


def test_hook_is_wired_into_all_three_movement_success_paths():
    """A smuggler must not be able to pick a transport mode that skips the scan.
    Counts call sites in the source so deleting one is caught here rather than in
    a live session."""
    import inspect

    src = inspect.getsource(MovementService.move_player_to_sector)
    assert src.count("_roll_contraband_transit_scan(") == 3, (
        "expected the scan on all three success branches "
        "(player gate / direct warp / warp tunnel)"
    )

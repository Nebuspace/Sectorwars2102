"""ADR-0054 X-D3 -- GC-lapse 7-day sweep call-shape proof.

Mirrors the house convention for the other coarse scheduler sweeps (own
SessionLocal, advisory-lock-then-release, single UPDATE...RETURNING commit):
proves _run_gc_lapse_sweep_sync issues the advisory lock BEFORE the UPDATE,
that the UPDATE's WHERE clause matches the 7-day/is_galactic_citizen=TRUE/
gc_lapsed_at-not-null contract, and that a lock-miss (a second gameserver
instance already running the sweep) short-circuits to a no-op.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from src.services.scheduler import economy_sweeps as sweeps
from src.services.scheduler._common import _GC_LAPSE_LOCK_KEY, GC_LAPSE_DAYS


class _FakeResult:
    def __init__(self, scalar_value=None, rows=None):
        self._scalar_value = scalar_value
        self._rows = rows or []

    def scalar(self):
        return self._scalar_value

    def fetchall(self):
        return self._rows


class _FakeDB:
    def __init__(self, got_lock: bool, lapsed_ids):
        self.got_lock = got_lock
        self.lapsed_ids = lapsed_ids
        self.calls = []
        self.committed = 0
        self.rolled_back = 0

    def execute(self, stmt, params=None):
        sql = str(stmt)
        self.calls.append((sql, params))
        if "pg_try_advisory_xact_lock" in sql:
            return _FakeResult(scalar_value=self.got_lock)
        if "UPDATE players" in sql:
            return _FakeResult(rows=[(pid,) for pid in self.lapsed_ids])
        raise AssertionError(f"unexpected statement: {sql}")

    def commit(self):
        self.committed += 1

    def rollback(self):
        self.rolled_back += 1

    def close(self):
        pass


def test_lock_acquired_before_the_update_and_update_flips_players():
    fake_db = _FakeDB(got_lock=True, lapsed_ids=["p1", "p2"])
    with patch("src.core.database.SessionLocal", return_value=fake_db):
        result = sweeps._run_gc_lapse_sweep_sync()

    assert result == {"lapsed": 2}
    assert len(fake_db.calls) == 2, "expected exactly [lock, UPDATE]"
    lock_sql, lock_params = fake_db.calls[0]
    assert "pg_try_advisory_xact_lock" in lock_sql
    assert lock_params == {"key": _GC_LAPSE_LOCK_KEY}

    update_sql, update_params = fake_db.calls[1]
    assert "gc_lapsed_at IS NOT NULL" in update_sql
    assert "is_galactic_citizen = TRUE" in update_sql
    assert "SET is_galactic_citizen = FALSE, gc_lapsed_at = NULL" in update_sql
    # The cutoff must be exactly GC_LAPSE_DAYS (7) days in the past.
    cutoff = update_params["cutoff"]
    now = datetime.now(timezone.utc)
    expected = now - timedelta(days=GC_LAPSE_DAYS)
    assert abs((cutoff - expected).total_seconds()) < 5

    assert fake_db.committed == 2  # lock-claim commit + update commit


def test_lock_miss_short_circuits_to_a_no_op():
    """A second gameserver instance already holding the advisory lock must
    see zero mutation, not a double-flip."""
    fake_db = _FakeDB(got_lock=False, lapsed_ids=["p1"])
    with patch("src.core.database.SessionLocal", return_value=fake_db):
        result = sweeps._run_gc_lapse_sweep_sync()

    assert result == {"lapsed": 0}
    assert len(fake_db.calls) == 1, "must not reach the UPDATE without the lock"
    assert fake_db.committed == 0

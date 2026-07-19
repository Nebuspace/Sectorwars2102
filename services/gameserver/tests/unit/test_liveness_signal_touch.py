"""QUEUE-LIVENESS-SIGNAL (2026-07-16): a throttled, post-auth API-activity
touch consumed by presence_helpers._is_presence_fresh via the one-site swap
(func.coalesce(Player.last_activity_at, Player.last_game_login)) -- closes
the live repro where a JWT-injected seat's presence entry was pruned every
sweep pass because last_game_login only ever refreshes on the login route.

HARD CONSTRAINT under test: _touch_liveness_signal is pure post-auth
telemetry, called at the END of get_current_player (auth/dependencies.py)
AFTER every 401/404 raise in that dependency chain has already happened. It
must never raise (a DB hiccup is swallowed) and its own logic never feeds
back into any conditional that could alter an auth outcome -- these tests
prove the throttle/write behavior of that function in isolation, DB-free.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from src.auth.dependencies import _LIVENESS_TOUCH_THROTTLE, _touch_liveness_signal


class _FakeDB:
    def __init__(self, *, raise_on_commit: bool = False):
        self.commit_count = 0
        self.rollback_count = 0
        self._raise_on_commit = raise_on_commit

    def commit(self):
        self.commit_count += 1
        if self._raise_on_commit:
            raise RuntimeError("simulated DB failure")

    def rollback(self):
        self.rollback_count += 1


def _player(last_activity_at=None):
    return SimpleNamespace(last_activity_at=last_activity_at)


@pytest.mark.unit
class TestLivenessSignalThrottle:
    def test_first_touch_with_no_prior_value_writes_and_commits(self) -> None:
        player = _player(last_activity_at=None)
        db = _FakeDB()

        _touch_liveness_signal(db, player)

        assert player.last_activity_at is not None
        assert db.commit_count == 1

    def test_second_touch_within_throttle_window_does_not_write_again(self) -> None:
        """Two requests within N minutes -> exactly ONE write -- the core
        throttle behavioral contract."""
        now = datetime.now(timezone.utc)
        player = _player(last_activity_at=now - timedelta(minutes=1))
        db = _FakeDB()

        _touch_liveness_signal(db, player)

        # Still the same (1-minute-old) value -- 5-minute throttle not yet due.
        assert player.last_activity_at == now - timedelta(minutes=1)
        assert db.commit_count == 0
        assert db.rollback_count == 0

    def test_touch_after_throttle_window_elapses_writes_again(self) -> None:
        now = datetime.now(timezone.utc)
        stale = now - _LIVENESS_TOUCH_THROTTLE - timedelta(seconds=1)
        player = _player(last_activity_at=stale)
        db = _FakeDB()

        _touch_liveness_signal(db, player)

        assert player.last_activity_at != stale
        assert player.last_activity_at > stale
        assert db.commit_count == 1

    def test_naive_datetime_on_the_player_row_is_treated_as_utc_not_a_crash(self) -> None:
        """A pre-existing row written before this column had timezone-aware
        writers (or a driver quirk) must not raise on the naive/aware
        comparison -- the throttle degrades safely either way."""
        now = datetime.now(timezone.utc)
        naive_recent = (now - timedelta(minutes=1)).replace(tzinfo=None)
        player = _player(last_activity_at=naive_recent)
        db = _FakeDB()

        _touch_liveness_signal(db, player)  # must not raise

        assert db.commit_count == 0  # still within the throttle window

    def test_db_failure_is_swallowed_never_raises(self) -> None:
        """HARD CONSTRAINT proof: a DB hiccup on this write must never
        surface as a request failure -- get_current_player calls this with
        zero exception handling of its own, relying entirely on this
        function to never raise."""
        player = _player(last_activity_at=None)
        db = _FakeDB(raise_on_commit=True)

        _touch_liveness_signal(db, player)  # must not raise

        assert db.commit_count == 1
        assert db.rollback_count == 1

    def test_throttle_constant_is_comfortably_inside_presence_stale_minutes(self) -> None:
        """Structural sanity pin: the write throttle must stay meaningfully
        smaller than the presence sweep's staleness cutoff, or an active
        player could still read as stale between writes."""
        from src.services.scheduler._common import PRESENCE_STALE_MINUTES

        assert _LIVENESS_TOUCH_THROTTLE < timedelta(minutes=PRESENCE_STALE_MINUTES)

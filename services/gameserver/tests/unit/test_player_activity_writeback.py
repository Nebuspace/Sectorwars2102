"""Unit coverage for the PlayerSession/PlayerActivity Postgres writeback
(WO-BUILD-RETENTION-SIGNALS-WRITEBACK).

Before this change, ``PlayerActivityService`` wrote ONLY to Redis --
``PlayerSession`` and ``PlayerActivity`` were durable tables with zero
writers, so 3 of the 7 ``RetentionService`` at-risk signals
(``declining_session_length``, ``early_logout_streak``, and -- still
dormant, see below -- ``economic_loss_streak``) and ``Region.
active_players_30d`` were structurally dead: their SELECTs always read
empty tables. This proves ``track_login`` / ``track_logout`` now durably
open/complete a ``PlayerSession`` row and write "login"/"logout"
``PlayerActivity`` rows, with concrete before/after data.

No live DB is used. Per the codebase's mock-only unit-test convention (see
test_route_runs_retention.py's FakeSession), ``FakeSession`` interprets the
real ``.query(...).filter(...).first()`` calls the SUT makes against an
in-memory row list.

Note: ``economic_loss_streak`` remains dormant after this change --
``track_activity`` (which would populate ``PlayerActivity`` for
trade_buy/trade_sell) has zero production call sites; wiring the trading
services is a separate, larger follow-up (see retention_service.py's
"SIGNAL DATA-SOURCE STATUS" note and this WO's final report).
"""
from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timedelta

import pytest

from src.services import player_activity_service as pas_module
from src.services.player_activity_service import ActivityEventType, PlayerActivityService
from src.models.player import Player
from src.models.player_analytics import PlayerActivity, PlayerSession


# --------------------------------------------------------------------------- #
# Fakes
# --------------------------------------------------------------------------- #

class FakePlayer:
    def __init__(self, player_id, current_sector_id=42):
        self.id = player_id
        self.current_sector_id = current_sector_id
        self.last_game_login = None


class FakeQuery:
    """Stand-in for db.query(Model): filter()/first() against an in-memory
    store, matching on the ONE identity predicate the SUT actually issues
    (Model.id == value or Model.player_id == value)."""

    def __init__(self, store, model):
        self._store = store
        self._model = model
        self._conditions = []

    def filter(self, *conditions):
        self._conditions.extend(conditions)
        return self

    def _matches(self, row):
        for cond in self._conditions:
            column = cond.left.key
            expected = cond.right.value
            actual = getattr(row, column)
            # UUID columns are compared against str ids by the SUT; normalize.
            if str(actual) != str(expected):
                return False
        return True

    def first(self):
        for row in self._store.get(self._model, []):
            if self._matches(row):
                return row
        return None


class FakeSession:
    """Minimal sync db double: query()/add()/flush()/commit()/rollback()."""

    def __init__(self, players=None):
        self.store = {PlayerSession: [], PlayerActivity: []}
        if players:
            self.store[Player] = list(players)
        self.committed = 0
        self.rolled_back = 0

    def query(self, model):
        return FakeQuery(self.store, model)

    def add(self, obj):
        self.store.setdefault(type(obj), []).append(obj)
        if isinstance(obj, PlayerSession) and obj.id is None:
            obj.id = uuid.uuid4()

    def flush(self):
        for obj in self.store.get(PlayerSession, []):
            if obj.id is None:
                obj.id = uuid.uuid4()

    def commit(self):
        self.committed += 1

    def rollback(self):
        self.rolled_back += 1


class FakeRedisPool:
    def __init__(self):
        self.sets = {}

    async def sadd(self, key, member):
        self.sets.setdefault(key, set()).add(member)

    async def srem(self, key, member):
        self.sets.setdefault(key, set()).discard(member)

    async def lpush(self, key, value):
        pass

    async def ltrim(self, key, lo, hi):
        pass

    async def expire(self, key, ttl):
        pass


class FakeRedisService:
    """In-memory cache_set/get/delete, mirroring redis_service.RedisService's
    JSON-in/JSON-out contract closely enough for this SUT."""

    def __init__(self):
        self._data = {}
        self.redis_pool = FakeRedisPool()

    async def cache_set(self, key, value, ttl=None):
        self._data[key] = value

    async def cache_get(self, key):
        return self._data.get(key)

    async def cache_delete(self, key):
        self._data.pop(key, None)


class _FixedClockDatetime(datetime):
    """Patches player_activity_service's `datetime.utcnow()` to a scripted
    sequence so login->logout duration is deterministic and assertable."""

    _queue: list = []

    @classmethod
    def utcnow(cls):
        if cls._queue:
            return cls._queue.pop(0)
        return datetime(2026, 1, 1, 0, 0, 0)


@pytest.fixture
def clock(monkeypatch):
    monkeypatch.setattr(pas_module, "datetime", _FixedClockDatetime)
    _FixedClockDatetime._queue = []
    yield _FixedClockDatetime
    _FixedClockDatetime._queue = []


def _run(coro):
    return asyncio.run(coro)


# --------------------------------------------------------------------------- #
# track_login
# --------------------------------------------------------------------------- #

class TestTrackLoginWriteback:
    def test_login_opens_durable_session_and_activity_row(self, clock):
        player_id = str(uuid.uuid4())
        player = FakePlayer(player_id, current_sector_id=7)
        db = FakeSession(players=[player])
        redis = FakeRedisService()
        svc = PlayerActivityService(redis=redis)

        now = datetime(2026, 1, 1, 10, 0, 0)
        clock._queue = [now, now, now]  # login + _record_event + defensive extra

        session_data = _run(svc.track_login(player_id, db=db))

        # Before: db.store had zero PlayerSession/PlayerActivity rows.
        # After: exactly one of each, correctly linked.
        assert len(db.store[PlayerSession]) == 1
        assert len(db.store[PlayerActivity]) == 1

        db_session = db.store[PlayerSession][0]
        assert db_session.player_id == player_id
        assert db_session.start_time == now
        assert db_session.end_time is None

        activity_row = db.store[PlayerActivity][0]
        assert activity_row.activity_type == ActivityEventType.LOGIN
        assert activity_row.player_id == player_id
        assert activity_row.session_id == db_session.id
        assert activity_row.sector_id == 7  # Player.current_sector_id

        # last_game_login refreshed too.
        assert player.last_game_login == now

        # db_session_id stashed on the Redis-persisted session dict so
        # track_logout can complete the row.
        assert session_data["db_session_id"] == str(db_session.id)
        assert db.committed >= 1
        assert db.rolled_back == 0

    def test_login_without_db_is_redis_only_and_unchanged(self, clock):
        """No db arg -> no durable writes attempted at all (back-compat: any
        other future caller that doesn't pass db behaves exactly as before)."""
        player_id = str(uuid.uuid4())
        redis = FakeRedisService()
        svc = PlayerActivityService(redis=redis)
        clock._queue = [datetime(2026, 1, 1, 10, 0, 0)] * 3

        session_data = _run(svc.track_login(player_id))

        assert "db_session_id" not in session_data


# --------------------------------------------------------------------------- #
# track_logout
# --------------------------------------------------------------------------- #

class TestTrackLogoutWriteback:
    def test_logout_completes_session_and_writes_logout_activity(self, clock):
        player_id = str(uuid.uuid4())
        player = FakePlayer(player_id, current_sector_id=99)
        db = FakeSession(players=[player])
        redis = FakeRedisService()
        svc = PlayerActivityService(redis=redis)

        login_at = datetime(2026, 1, 1, 10, 0, 0)
        logout_at = login_at + timedelta(minutes=37)

        clock._queue = [login_at, login_at, login_at]
        _run(svc.track_login(player_id, db=db))

        clock._queue = [logout_at, logout_at, logout_at, logout_at]
        summary = _run(svc.track_logout(player_id, db=db))

        db_session = db.store[PlayerSession][0]
        # Before logout, end_time/duration_minutes were None (asserted via
        # the login test above); after logout, both are populated.
        assert db_session.end_time == logout_at
        assert db_session.duration_minutes == 37

        activity_rows = db.store[PlayerActivity]
        assert len(activity_rows) == 2  # login + logout
        logout_row = activity_rows[-1]
        assert logout_row.activity_type == ActivityEventType.LOGOUT
        assert str(logout_row.session_id) == str(db_session.id)
        assert logout_row.sector_id == 99
        assert logout_row.player_id == player_id

        assert summary["duration_seconds"] == pytest.approx(37 * 60)

    def test_logout_without_prior_login_db_session_id_is_a_safe_no_op(self, clock):
        """No matching db_session_id on the Redis session (e.g. it expired,
        or login happened before this writeback shipped) -> defensive
        no-write, never raises."""
        player_id = str(uuid.uuid4())
        player = FakePlayer(player_id)
        db = FakeSession(players=[player])
        redis = FakeRedisService()
        svc = PlayerActivityService(redis=redis)

        clock._queue = [datetime(2026, 1, 1, 10, 0, 0)] * 4

        summary = _run(svc.track_logout(player_id, db=db))

        assert db.store[PlayerSession] == []
        assert db.store[PlayerActivity] == []
        assert summary is not None


# --------------------------------------------------------------------------- #
# Region.active_players_30d precondition (WO-G18 recompute reads exactly
# these PlayerActivity fields: player_id, sector_id, timestamp)
# --------------------------------------------------------------------------- #

class TestActivePlayers30dPrecondition:
    def test_login_logout_produce_activity_rows_the_region_recompute_can_join(self, clock):
        """The WO-G18 sweep joins PlayerActivity.sector_id -> Sector.sector_id
        and COUNT(DISTINCT player_id) within a 30-day window. Before this
        writeback, PlayerActivity was always empty so every region's count
        was always 0. This proves a login+logout cycle now leaves behind
        PlayerActivity rows satisfying that join/window predicate for a
        DISTINCT set of players -- i.e. the recompute is no longer
        structurally starved. (Exercising the full sweep query itself needs
        a live DB/Sector join, per the codebase's DB-free unit convention.)
        """
        now = datetime(2026, 1, 1, 12, 0, 0)
        window_start = now - timedelta(days=30)

        players = [str(uuid.uuid4()) for _ in range(3)]
        db = FakeSession()
        redis = FakeRedisService()
        svc = PlayerActivityService(redis=redis)

        for pid in players:
            db.store.setdefault(Player, []).append(FakePlayer(pid, current_sector_id=5))
            clock._queue = [now, now, now]
            _run(svc.track_login(pid, db=db))
            clock._queue = [now, now, now, now]
            _run(svc.track_logout(pid, db=db))

        rows_in_window = [
            r for r in db.store[PlayerActivity]
            if r.timestamp >= window_start and r.sector_id is not None
        ]
        distinct_players = {r.player_id for r in rows_in_window}

        assert len(rows_in_window) == 6  # login + logout per player
        assert distinct_players == set(players)
        assert len(distinct_players) == 3  # non-zero, matching WO's Accept

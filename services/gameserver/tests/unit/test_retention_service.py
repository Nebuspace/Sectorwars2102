"""Unit tests — retention_service.py (per-player at-risk signal computation,
WO-RE2, OPERATIONS/retention.md).

No test file existed for this service. Read-only throughout, so DB-free
testing is a plain fake: a `_FakeDb` keyed by either the full mapped CLASS
(a single-arg `.query(Player)` call) or, for the module's several
column-tuple queries (`.query(PlayerSession.duration_minutes)`,
`.query(CombatLog.attacker_id, CombatLog.defender_id, CombatLog.outcome)`,
etc.), by `(owning_class, tuple_of_column_names)` derived from each
InstrumentedAttribute's `.class_`/`.key` -- a single FIFO queue entry per
distinct query shape is enough since `compute_player_signals` issues each
shape at most once per call. `_canonical_days_inactive` / `_canonical_
cutoff` take an explicit `now`, so the real `game_time.canonical_hours_
since` is exercised directly rather than mocked (matching the established
"replay the real function" convention) -- with `GAME_TIME_SCALE` unset in
this test's dummy env, it defaults to 1.0, so canonical days == calendar
days throughout.

Sections:
  TestCanonicalDaysInactive — never-logged-in -> None; real canonical math.
  TestRecentSessionDurations — tuple-row -> int-list mapping.
  TestDecliningSessionLength — the >30%-drop-AND-monotonic double gate.
  TestEarlyLogoutStreak — the 3-consecutive-sub-5-minute streak.
  TestNegativeCombatStreak — kill/death tally from CombatLog, the
    COMBAT_MIN_EVENTS floor, draw/escaped counted as neither.
  TestEconomicLossStreak — net credit delta vs holdings, div-by-zero guard.
  TestSocialIsolation — teamless-AND-no-recent-message double gate.
  TestCanonicalCutoff — the wall-clock/GAME_TIME_SCALE conversion.
  TestComputePlayerSignals — the full per-player orchestration: missing
    player, dormant-vs-lapsed mutual exclusion, multi-signal detail, and a
    clean player tripping nothing.
"""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

from src.core import game_time
from src.models.combat_log import CombatLog
from src.models.message import Message
from src.models.player import Player
from src.models.player_analytics import PlayerActivity, PlayerSession
from src.services.retention_service import (
    COMBAT_MIN_EVENTS,
    DECLINING_MIN_SESSIONS,
    DORMANT_DAYS,
    EARLY_LOGOUT_STREAK,
    LAPSED_DAYS,
    RetentionService,
)


class _FakeQuery:
    def __init__(self, value):
        self._value = value

    def filter(self, *_args, **_kwargs):
        return self

    def order_by(self, *_args, **_kwargs):
        return self

    def limit(self, *_args, **_kwargs):
        return self

    def first(self):
        return self._value

    def all(self):
        return self._value if self._value is not None else []


def _query_key(args):
    if len(args) == 1 and isinstance(args[0], type):
        return args[0]
    return (args[0].class_, tuple(a.key for a in args))


class _FakeDb:
    def __init__(self, results=None):
        self._queues = {k: list(v) for k, v in (results or {}).items()}

    def query(self, *args):
        key = _query_key(args)
        queue = self._queues.get(key, [])
        value = queue.pop(0) if queue else None
        return _FakeQuery(value)


def _player(**kwargs):
    p = Player()
    p.id = kwargs.pop("id", uuid4())
    p.credits = kwargs.pop("credits", 10000)
    p.team_id = kwargs.pop("team_id", None)
    p.last_game_login = kwargs.pop("last_game_login", None)
    for k, v in kwargs.items():
        setattr(p, k, v)
    return p


PS_DURATION_KEY = (PlayerSession, ("duration_minutes",))
COMBAT_KEY = (CombatLog, ("attacker_id", "defender_id", "outcome"))
ACTIVITY_KEY = (PlayerActivity, ("activity_type", "credits_involved"))
MESSAGE_ID_KEY = (Message, ("id",))


# ---------------------------------------------------------------------------
# _canonical_days_inactive
# ---------------------------------------------------------------------------


class TestCanonicalDaysInactive:
    def test_never_logged_in_is_none(self):
        player = _player(last_game_login=None)
        svc = RetentionService(_FakeDb())
        assert svc._canonical_days_inactive(player, datetime.now(UTC)) is None

    def test_ten_days_since_login(self):
        now = datetime(2026, 8, 20, tzinfo=UTC)
        player = _player(last_game_login=now - timedelta(days=10))
        svc = RetentionService(_FakeDb())
        assert svc._canonical_days_inactive(player, now) == 10

    def test_less_than_a_day_floors_to_zero(self):
        now = datetime(2026, 8, 20, tzinfo=UTC)
        player = _player(last_game_login=now - timedelta(hours=5))
        svc = RetentionService(_FakeDb())
        assert svc._canonical_days_inactive(player, now) == 0


# ---------------------------------------------------------------------------
# _recent_session_durations
# ---------------------------------------------------------------------------


class TestRecentSessionDurations:
    def test_maps_tuple_rows_to_an_int_list(self):
        db = _FakeDb(results={PS_DURATION_KEY: [[(30,), (25,), (10,)]]})
        svc = RetentionService(db)
        assert svc._recent_session_durations(uuid4()) == [30, 25, 10]

    def test_no_sessions_is_empty(self):
        db = _FakeDb(results={PS_DURATION_KEY: [[]]})
        svc = RetentionService(db)
        assert svc._recent_session_durations(uuid4()) == []


# ---------------------------------------------------------------------------
# _declining_session_length
# ---------------------------------------------------------------------------


class TestDecliningSessionLength:
    def test_fewer_than_the_window_returns_none(self):
        svc = RetentionService(_FakeDb())
        assert svc._declining_session_length([10, 10, 10, 10]) is None

    def test_small_drop_does_not_trip(self):
        svc = RetentionService(_FakeDb())
        # newest 90, oldest 100 -> 10% drop, below the 30% threshold
        assert svc._declining_session_length([90, 92, 95, 98, 100]) is None

    def test_large_monotonic_drop_trips(self):
        svc = RetentionService(_FakeDb())
        durations = [10, 20, 40, 60, 100]  # newest-first; chrono = [100,60,40,20,10]
        result = svc._declining_session_length(durations)
        assert result is not None
        assert result["window_sessions"] == DECLINING_MIN_SESSIONS
        assert result["durations_oldest_to_newest"] == [100, 60, 40, 20, 10]
        assert result["observed_drop_pct"] == 0.9

    def test_large_drop_but_non_monotonic_series_does_not_trip(self):
        svc = RetentionService(_FakeDb())
        # newest(10) vs oldest(100) is a big drop, but the series bounces
        # (chrono: 100, 60, 90, 20, 10 -- 60 -> 90 is an INCREASE).
        durations = [10, 20, 90, 60, 100]
        assert svc._declining_session_length(durations) is None

    def test_zero_oldest_duration_is_a_noop_guard(self):
        svc = RetentionService(_FakeDb())
        assert svc._declining_session_length([0, 0, 0, 0, 0]) is None


# ---------------------------------------------------------------------------
# _early_logout_streak
# ---------------------------------------------------------------------------


class TestEarlyLogoutStreak:
    def test_fewer_than_the_streak_length_returns_none(self):
        svc = RetentionService(_FakeDb())
        assert svc._early_logout_streak([2, 3]) is None

    def test_three_consecutive_short_sessions_trips(self):
        svc = RetentionService(_FakeDb())
        result = svc._early_logout_streak([1, 2, 3, 30, 40])
        assert result is not None
        assert result["streak_len"] == EARLY_LOGOUT_STREAK
        assert result["observed_recent_minutes"] == [1, 2, 3]

    def test_one_long_session_in_the_streak_window_blocks_it(self):
        svc = RetentionService(_FakeDb())
        assert svc._early_logout_streak([1, 2, 10, 3, 4]) is None


# ---------------------------------------------------------------------------
# _negative_combat_streak
# ---------------------------------------------------------------------------


class TestNegativeCombatStreak:
    def test_fewer_than_min_events_returns_none(self):
        player_id = uuid4()
        rows = [(player_id, uuid4(), "attacker_win"), (player_id, uuid4(), "defender_win")]
        db = _FakeDb(results={COMBAT_KEY: [rows]})
        svc = RetentionService(db)
        assert svc._negative_combat_streak(player_id, datetime.now(UTC)) is None

    def test_more_deaths_than_kills_trips(self):
        player_id = uuid4()
        rows = [
            (player_id, uuid4(), "defender_win"),  # attacker, lost -> death
            (player_id, uuid4(), "defender_win"),  # attacker, lost -> death
            (uuid4(), player_id, "attacker_win"),  # defender, lost -> death
            (uuid4(), player_id, "defender_win"),  # defender, won -> kill
        ]
        db = _FakeDb(results={COMBAT_KEY: [rows]})
        svc = RetentionService(db)
        result = svc._negative_combat_streak(player_id, datetime.now(UTC))
        assert result is not None
        assert result["kills"] == 1
        assert result["deaths"] == 3
        assert result["min_events"] == COMBAT_MIN_EVENTS

    def test_kills_greater_or_equal_to_deaths_does_not_trip(self):
        player_id = uuid4()
        rows = [
            (player_id, uuid4(), "attacker_win"),
            (player_id, uuid4(), "attacker_win"),
            (uuid4(), player_id, "attacker_win"),
        ]
        db = _FakeDb(results={COMBAT_KEY: [rows]})
        svc = RetentionService(db)
        assert svc._negative_combat_streak(player_id, datetime.now(UTC)) is None

    def test_draws_and_escapes_count_as_neither(self):
        player_id = uuid4()
        rows = [
            (player_id, uuid4(), "draw"),
            (player_id, uuid4(), "escaped"),
            (player_id, uuid4(), "defender_win"),  # death
        ]
        db = _FakeDb(results={COMBAT_KEY: [rows]})
        svc = RetentionService(db)
        # only 1 decisive combat -- below COMBAT_MIN_EVENTS (3)
        assert svc._negative_combat_streak(player_id, datetime.now(UTC)) is None


# ---------------------------------------------------------------------------
# _economic_loss_streak
# ---------------------------------------------------------------------------


class TestEconomicLossStreak:
    def test_zero_holdings_never_trips(self):
        player = _player(credits=0)
        db = _FakeDb(results={ACTIVITY_KEY: [[("trade_buy", 500)]]})
        svc = RetentionService(db)
        assert svc._economic_loss_streak(player, datetime.now(UTC)) is None

    def test_net_loss_exceeding_half_holdings_trips(self):
        player = _player(credits=1000)
        rows = [("trade_buy", 600), ("trade_sell", 50)]  # net -550, > 50% of 1000
        db = _FakeDb(results={ACTIVITY_KEY: [rows]})
        svc = RetentionService(db)
        result = svc._economic_loss_streak(player, datetime.now(UTC))
        assert result is not None
        assert result["net_credits"] == -550
        assert result["holdings"] == 1000

    def test_net_loss_under_the_threshold_does_not_trip(self):
        player = _player(credits=1000)
        rows = [("trade_buy", 300), ("trade_sell", 200)]  # net -100, 10% of 1000
        db = _FakeDb(results={ACTIVITY_KEY: [rows]})
        svc = RetentionService(db)
        assert svc._economic_loss_streak(player, datetime.now(UTC)) is None

    def test_net_gain_does_not_trip(self):
        player = _player(credits=1000)
        rows = [("trade_sell", 800), ("trade_buy", 100)]  # net +700
        db = _FakeDb(results={ACTIVITY_KEY: [rows]})
        svc = RetentionService(db)
        assert svc._economic_loss_streak(player, datetime.now(UTC)) is None


# ---------------------------------------------------------------------------
# _social_isolation
# ---------------------------------------------------------------------------


class TestSocialIsolation:
    def test_teamed_player_never_trips(self):
        player = _player(team_id=uuid4())
        svc = RetentionService(_FakeDb())  # no Message query issued at all
        assert svc._social_isolation(player, datetime.now(UTC)) is None

    def test_teamless_with_no_recent_message_trips(self):
        player = _player(team_id=None)
        db = _FakeDb(results={MESSAGE_ID_KEY: [None]})
        svc = RetentionService(db)
        result = svc._social_isolation(player, datetime.now(UTC))
        assert result is not None
        assert result["teamless"] is True

    def test_teamless_with_a_recent_message_does_not_trip(self):
        player = _player(team_id=None)
        db = _FakeDb(results={MESSAGE_ID_KEY: [uuid4()]})
        svc = RetentionService(db)
        assert svc._social_isolation(player, datetime.now(UTC)) is None


# ---------------------------------------------------------------------------
# _canonical_cutoff
# ---------------------------------------------------------------------------


class TestCanonicalCutoff:
    def test_default_scale_is_calendar_days(self):
        now = datetime(2026, 8, 20, tzinfo=UTC)
        cutoff = RetentionService._canonical_cutoff(now, 14)
        assert cutoff == now - timedelta(days=14)

    def test_matches_the_real_scale_formula(self):
        now = datetime(2026, 8, 20, tzinfo=UTC)
        cutoff = RetentionService._canonical_cutoff(now, 7)
        expected_seconds = 7 * 86400 / game_time.GAME_TIME_SCALE
        assert cutoff == now - timedelta(seconds=expected_seconds)


# ---------------------------------------------------------------------------
# compute_player_signals (integration)
# ---------------------------------------------------------------------------


def _empty_signal_db(player):
    """A player found, but every downstream signal query comes back empty --
    the 'healthy player, nothing to report' baseline other tests override."""
    return _FakeDb(
        results={
            Player: [player],
            PS_DURATION_KEY: [[]],
            COMBAT_KEY: [[]],
            ACTIVITY_KEY: [[]],
            MESSAGE_ID_KEY: [uuid4()],  # recent message -> not socially isolated
        }
    )


class TestComputePlayerSignals:
    def test_missing_player_returns_empty(self):
        db = _FakeDb(results={Player: [None]})
        svc = RetentionService(db)
        assert svc.compute_player_signals(uuid4()) == {"tripped": [], "detail": {}}

    def test_healthy_player_trips_nothing(self):
        player = _player(last_game_login=datetime.now(UTC), team_id=uuid4())
        db = _empty_signal_db(player)
        svc = RetentionService(db)
        result = svc.compute_player_signals(player.id)
        assert result == {"tripped": [], "detail": {}}

    def test_dormant_but_not_lapsed(self):
        now = datetime(2026, 8, 20, tzinfo=UTC)
        player = _player(last_game_login=now - timedelta(days=DORMANT_DAYS), team_id=uuid4())
        db = _empty_signal_db(player)
        svc = RetentionService(db)
        result = svc.compute_player_signals(player.id, now=now)
        assert result["tripped"] == ["dormant_session"]
        assert result["detail"]["dormant_session"]["observed_days"] == DORMANT_DAYS

    def test_lapsed_supersedes_dormant(self):
        now = datetime(2026, 8, 20, tzinfo=UTC)
        player = _player(last_game_login=now - timedelta(days=LAPSED_DAYS), team_id=uuid4())
        db = _empty_signal_db(player)
        svc = RetentionService(db)
        result = svc.compute_player_signals(player.id, now=now)
        assert result["tripped"] == ["lapsed"]
        assert "dormant_session" not in result["detail"]

    def test_multiple_signals_trip_together(self):
        now = datetime(2026, 8, 20, tzinfo=UTC)
        player = _player(
            last_game_login=now - timedelta(days=LAPSED_DAYS),
            team_id=None,
            credits=1000,
        )
        db = _FakeDb(
            results={
                Player: [player],
                PS_DURATION_KEY: [[]],
                COMBAT_KEY: [[]],
                ACTIVITY_KEY: [[("trade_buy", 600), ("trade_sell", 50)]],  # net -550
                MESSAGE_ID_KEY: [None],  # no recent message -> isolated
            }
        )
        svc = RetentionService(db)
        result = svc.compute_player_signals(player.id, now=now)
        assert set(result["tripped"]) == {"lapsed", "economic_loss_streak", "social_isolation"}
        assert result["detail"]["economic_loss_streak"]["net_credits"] == -550
        assert result["detail"]["social_isolation"]["teamless"] is True

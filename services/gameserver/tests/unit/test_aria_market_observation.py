"""Unit coverage for the sync market-observation entry point
(WO-ARIA-MARKET-OBS): ``record_market_observation_sync`` and the sync
helpers it depends on (``_validate_player_at_port_sync``,
``_log_security_event_sync``, the now-sync ``_identify_price_patterns`` /
``_calculate_anomaly_score``).

No live DB is used. Per the codebase's mock-only unit-test convention (see
test_aria_observation_log.py's FakeObsSession), ``FakeMarketObsSession``
interprets the REAL SQLAlchemy query()/filter()/first() clauses the SUT
builds against a live, mutable in-memory row store -- built from REAL
``Player`` / ``Station`` / ``ARIAMarketIntelligence`` / ``ARIASecurityLog``
ORM instances (never hand-rolled duck-types), so the real docking check
(``Player.is_docked`` + ``current_sector_id`` vs ``station.sector_id``) and
the real JSON-column mutation-tracking gotcha are both exercised for real.

record_market_observation_sync is entirely sync (Session, not
AsyncSession) -- see the service module's OBSERVATION LOG / market-
observation section docstrings for why. This fake session is a plain sync
double, no asyncio anywhere in this file.
"""
from __future__ import annotations

import inspect
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import inspect as sa_inspect
from sqlalchemy.orm import attributes
from sqlalchemy.sql import operators

from src.core import game_time
from src.models.aria_personal_intelligence import ARIAMarketIntelligence, ARIASecurityLog
from src.models.player import Player
from src.models.station import Station
from src.services.aria_personal_intelligence_service import ARIAPersonalIntelligenceService

NOW = datetime(2026, 7, 10, 12, 0, 0, tzinfo=UTC)

# The exact WALL-CLOCK offset that corresponds to the SUT's 10-CANONICAL-
# minute dedup window at whatever GAME_TIME_SCALE this process resolved at
# import time -- computed the same way scaled_elapsed does, so this file
# stays correct regardless of GAME_TIME_SCALE (confirmed 1.0 in the
# documented pytest invocation, but never hardcoded here).
WINDOW = timedelta(minutes=10)
WALL_WINDOW = WINDOW / game_time.GAME_TIME_SCALE


# --------------------------------------------------------------------------- #
# In-memory fake session -- interprets the SUT's real SQLAlchemy clauses
# --------------------------------------------------------------------------- #

def _condition_matches(row, condition) -> bool:
    left, op = condition.left, condition.operator
    actual = getattr(row, left.key)
    if op is operators.eq:
        return actual == condition.right.value
    raise AssertionError(f"unhandled operator {op!r} on column {left.key!r}")


class _FakeFilterFirstQuery:
    """db.query(Model).filter(...).first() -- the only query shape every
    lookup in record_market_observation_sync / its sync helpers uses."""

    def __init__(self, store, session):
        self._store = store
        self._session = session
        self._conditions: tuple = ()

    def filter(self, *conditions):
        self._conditions = self._conditions + conditions
        return self

    def first(self):
        self._session.queries += 1
        matching = [r for r in self._store if all(_condition_matches(r, c) for c in self._conditions)]
        return matching[0] if matching else None


class FakeMarketObsSession:
    """Minimal sync db double: dispatches db.query(Model) by the model
    class itself (every query in this SUT is a whole-row query, never a
    column-projection query)."""

    def __init__(self, players=(), stations=(), intelligences=(), security_logs=()):
        self.players = list(players)
        self.stations = list(stations)
        self.intelligences = list(intelligences)
        self.security_logs = list(security_logs)
        self.queries = 0
        self.added = []

    def add(self, obj):
        self.added.append(obj)
        if isinstance(obj, ARIAMarketIntelligence):
            self.intelligences.append(obj)
        elif isinstance(obj, ARIASecurityLog):
            self.security_logs.append(obj)
        else:
            raise AssertionError(f"unexpected db.add() of {type(obj)!r}")

    def query(self, model):
        if model is Player:
            return _FakeFilterFirstQuery(self.players, self)
        if model is Station:
            return _FakeFilterFirstQuery(self.stations, self)
        if model is ARIAMarketIntelligence:
            return _FakeFilterFirstQuery(self.intelligences, self)
        raise AssertionError(f"unexpected query owner {model!r}")


# --------------------------------------------------------------------------- #
# Fixture data -- real Player / Station instances (never duck-types), so the
# real docking check is exercised for real.
# --------------------------------------------------------------------------- #

PLAYER = uuid.uuid4()
STATION_A = uuid.uuid4()
STATION_B = uuid.uuid4()
SECTOR_5 = 5
SECTOR_7 = 7


def _docked_player(*, player_id=PLAYER, sector_id=SECTOR_5) -> Player:
    # Service methods are always called with str(PLAYER)/str(STATION_*)
    # (matches every real caller's str-typed signature) -- store the SAME
    # string representation so the fake session's equality filter
    # (actual == right.value) actually matches, exactly as it would
    # against a real UUID column comparing to a UUID-typed bind.
    return Player(id=str(player_id), is_docked=True, current_sector_id=sector_id)


def _undocked_player(*, player_id=PLAYER, sector_id=SECTOR_5) -> Player:
    return Player(id=str(player_id), is_docked=False, current_sector_id=sector_id)


def _station(*, station_id=STATION_A, sector_id=SECTOR_5, sector_uuid=None, name="Auriga Prime") -> Station:
    return Station(
        id=str(station_id), name=name, sector_id=sector_id,
        sector_uuid=sector_uuid or uuid.uuid4(),
    )


@pytest.fixture()
def service() -> ARIAPersonalIntelligenceService:
    return ARIAPersonalIntelligenceService()


@pytest.fixture()
def db() -> FakeMarketObsSession:
    return FakeMarketObsSession(
        players=[_docked_player()],
        stations=[_station()],
    )


def _three_commodity_payload():
    return [
        {"commodity": "ORGANICS", "price": 50, "quantity": 100},
        {"commodity": "ORE", "price": 30, "quantity": 200},
        {"commodity": "FUEL", "price": 10, "quantity": 500},
    ]


# --------------------------------------------------------------------------- #
# Sync entry point
# --------------------------------------------------------------------------- #

class TestIsGenuinelySync:
    def test_record_market_observation_sync_is_not_a_coroutine(self, service):
        assert not inspect.iscoroutinefunction(service.record_market_observation_sync)

    def test_validate_player_at_port_sync_is_not_a_coroutine(self, service):
        assert not inspect.iscoroutinefunction(service._validate_player_at_port_sync)

    def test_log_security_event_sync_is_not_a_coroutine(self, service):
        assert not inspect.iscoroutinefunction(service._log_security_event_sync)


# --------------------------------------------------------------------------- #
# Per-commodity row creation
# --------------------------------------------------------------------------- #

class TestPerCommodityRows:
    def test_three_commodity_market_creates_three_rows(self, service, db):
        service.record_market_observation_sync(str(PLAYER), str(STATION_A), _three_commodity_payload(), db)

        assert len(db.intelligences) == 3
        commodities = {i.commodity for i in db.intelligences}
        assert commodities == {"ORGANICS", "ORE", "FUEL"}
        for intel in db.intelligences:
            assert len(intel.price_observations) == 1
            assert intel.data_points == 1

    def test_empty_market_creates_zero_rows_no_error(self, service, db):
        service.record_market_observation_sync(str(PLAYER), str(STATION_A), [], db)
        assert db.intelligences == []

    def test_none_market_creates_zero_rows_no_error(self, service, db):
        service.record_market_observation_sync(str(PLAYER), str(STATION_A), None, db)
        assert db.intelligences == []

    def test_entry_with_no_commodity_is_skipped_others_recorded(self, service, db):
        payload = [
            {"commodity": None, "price": 10},
            {"commodity": "ORE", "price": 30},
        ]
        service.record_market_observation_sync(str(PLAYER), str(STATION_A), payload, db)
        assert len(db.intelligences) == 1
        assert db.intelligences[0].commodity == "ORE"

    def test_price_none_is_skipped(self, service, db):
        payload = [{"commodity": "LUXURY", "price": None, "quantity": 5}]
        service.record_market_observation_sync(str(PLAYER), str(STATION_A), payload, db)
        assert db.intelligences == []

    def test_price_zero_is_recorded_as_real_market_state(self, service, db):
        payload = [{"commodity": "SCRAP", "price": 0, "quantity": 5}]
        service.record_market_observation_sync(str(PLAYER), str(STATION_A), payload, db)
        assert len(db.intelligences) == 1
        assert db.intelligences[0].price_observations[0]["price"] == 0

    def test_missing_quantity_defaults_to_zero(self, service, db):
        payload = [{"commodity": "GEMS", "price": 900}]
        service.record_market_observation_sync(str(PLAYER), str(STATION_A), payload, db)
        assert db.intelligences[0].price_observations[0]["quantity"] == 0


# --------------------------------------------------------------------------- #
# Window dedup -- spans both hook sites (dock + market view are the SAME
# call shape from the service's point of view; see class docstring).
# --------------------------------------------------------------------------- #

class TestWindowDedup:
    def test_duplicate_call_within_window_dock_then_view_appends_zero(self, service, db, monkeypatch):
        import src.services.aria_personal_intelligence_service as svc_module

        clock = {"now": NOW}

        class _Clock(datetime):
            @classmethod
            def now(cls, tz=None):
                return clock["now"]

        monkeypatch.setattr(svc_module, "datetime", _Clock)

        payload = [{"commodity": "ORE", "price": 30, "quantity": 200}]

        # "dock" hook fires
        service.record_market_observation_sync(str(PLAYER), str(STATION_A), payload, db)
        assert len(db.intelligences) == 1
        assert len(db.intelligences[0].price_observations) == 1

        # "market view" hook fires 30 wall-seconds later, well inside the window
        clock["now"] = NOW + timedelta(seconds=30)
        service.record_market_observation_sync(str(PLAYER), str(STATION_A), payload, db)

        assert len(db.intelligences) == 1  # no NEW row
        assert len(db.intelligences[0].price_observations) == 1  # zero additional appends
        assert db.intelligences[0].data_points == 1

    def test_duplicate_call_within_window_view_then_view_appends_zero(self, service, db, monkeypatch):
        """Same call shape, different conceptual trigger label -- the service
        has no notion of which hook site called it, so a second "view" call
        is identical in every observable way to a second "dock" call."""
        import src.services.aria_personal_intelligence_service as svc_module

        clock = {"now": NOW}

        class _Clock(datetime):
            @classmethod
            def now(cls, tz=None):
                return clock["now"]

        monkeypatch.setattr(svc_module, "datetime", _Clock)

        payload = [{"commodity": "ORE", "price": 30, "quantity": 200}]

        service.record_market_observation_sync(str(PLAYER), str(STATION_A), payload, db)
        clock["now"] = NOW + timedelta(minutes=2)
        service.record_market_observation_sync(str(PLAYER), str(STATION_A), payload, db)
        clock["now"] = NOW + timedelta(minutes=4)
        service.record_market_observation_sync(str(PLAYER), str(STATION_A), payload, db)

        assert len(db.intelligences) == 1
        assert len(db.intelligences[0].price_observations) == 1

    def test_just_inside_window_boundary_is_deduped(self, service, db, monkeypatch):
        import src.services.aria_personal_intelligence_service as svc_module

        clock = {"now": NOW}

        class _Clock(datetime):
            @classmethod
            def now(cls, tz=None):
                return clock["now"]

        monkeypatch.setattr(svc_module, "datetime", _Clock)

        payload = [{"commodity": "ORE", "price": 30}]
        service.record_market_observation_sync(str(PLAYER), str(STATION_A), payload, db)

        # A hair under the canonical window -- must still dedup (strict <).
        clock["now"] = NOW + WALL_WINDOW - timedelta(seconds=1)
        service.record_market_observation_sync(str(PLAYER), str(STATION_A), payload, db)

        assert len(db.intelligences[0].price_observations) == 1

    def test_just_outside_window_boundary_is_recorded(self, service, db, monkeypatch):
        import src.services.aria_personal_intelligence_service as svc_module

        clock = {"now": NOW}

        class _Clock(datetime):
            @classmethod
            def now(cls, tz=None):
                return clock["now"]

        monkeypatch.setattr(svc_module, "datetime", _Clock)

        payload = [{"commodity": "ORE", "price": 30}]
        service.record_market_observation_sync(str(PLAYER), str(STATION_A), payload, db)

        # Exactly at the canonical window boundary -- NOT "< window", so it
        # must record (the SUT's skip condition is a strict less-than).
        clock["now"] = NOW + WALL_WINDOW
        service.record_market_observation_sync(str(PLAYER), str(STATION_A), payload, db)

        assert len(db.intelligences[0].price_observations) == 2

    def test_a_genuinely_new_commodity_records_even_inside_another_commoditys_window(self, service, db, monkeypatch):
        """A payload where ORE was JUST seen (still within its own dedup
        window) but LUXURY is brand new to this player+station must still
        record LUXURY -- the dedup gate is per-commodity, not a blanket
        per-call skip."""
        import src.services.aria_personal_intelligence_service as svc_module

        clock = {"now": NOW}

        class _Clock(datetime):
            @classmethod
            def now(cls, tz=None):
                return clock["now"]

        monkeypatch.setattr(svc_module, "datetime", _Clock)

        service.record_market_observation_sync(
            str(PLAYER), str(STATION_A), [{"commodity": "ORE", "price": 30}], db,
        )
        clock["now"] = NOW + timedelta(seconds=30)
        service.record_market_observation_sync(
            str(PLAYER), str(STATION_A),
            [{"commodity": "ORE", "price": 31}, {"commodity": "LUXURY", "price": 900}],
            db,
        )

        by_commodity = {i.commodity: i for i in db.intelligences}
        assert len(by_commodity["ORE"].price_observations) == 1  # deduped
        assert len(by_commodity["LUXURY"].price_observations) == 1  # genuinely new


# --------------------------------------------------------------------------- #
# Unvisited-port spoof
# --------------------------------------------------------------------------- #

class TestUnvisitedPortSpoof:
    def test_undocked_player_records_nothing_and_writes_security_log(self, service):
        db = FakeMarketObsSession(players=[_undocked_player()], stations=[_station()])

        service.record_market_observation_sync(str(PLAYER), str(STATION_A), _three_commodity_payload(), db)

        assert db.intelligences == []
        assert len(db.security_logs) == 1
        assert db.security_logs[0].event_type == "invalid_market_observation"

    def test_docked_but_wrong_sector_records_nothing_and_writes_security_log(self, service):
        # Player docked, but in a DIFFERENT sector than the station -- the
        # real trading.py convention (current_sector_id vs station.sector_id)
        # must reject this exactly like an undocked player.
        player = _docked_player(sector_id=SECTOR_7)
        db = FakeMarketObsSession(players=[player], stations=[_station(sector_id=SECTOR_5)])

        service.record_market_observation_sync(str(PLAYER), str(STATION_A), _three_commodity_payload(), db)

        assert db.intelligences == []
        assert len(db.security_logs) == 1

    def test_unknown_player_records_nothing_and_writes_security_log(self, service):
        db = FakeMarketObsSession(players=[], stations=[_station()])

        service.record_market_observation_sync(str(PLAYER), str(STATION_A), _three_commodity_payload(), db)

        assert db.intelligences == []
        assert len(db.security_logs) == 1


# --------------------------------------------------------------------------- #
# JSON mutation persistence -- pins the flag_modified/reassignment fix.
# Uses sqlalchemy.orm.attributes.get_history() on a REAL mapped instance
# with its committed_state baseline reset to simulate "freshly loaded,
# nothing dirty yet" -- see the codebase's established get-history-jsonb-
# dirty-tracking-proof technique. An in-place .append() on this baseline
# leaves has_changes() False (silently lost at flush); a REASSIGNMENT
# flips it True.
# --------------------------------------------------------------------------- #

class TestJsonMutationPersistence:
    def test_second_window_reassigns_and_sqlalchemy_sees_it_as_changed(self, service, db, monkeypatch):
        import src.services.aria_personal_intelligence_service as svc_module

        clock = {"now": NOW}

        class _Clock(datetime):
            @classmethod
            def now(cls, tz=None):
                return clock["now"]

        monkeypatch.setattr(svc_module, "datetime", _Clock)

        payload = [{"commodity": "ORE", "price": 30}]
        service.record_market_observation_sync(str(PLAYER), str(STATION_A), payload, db)
        intel = db.intelligences[0]

        # Simulate "this row was already committed/loaded" -- reset the
        # SQLAlchemy change-tracking baseline to the current (first-window)
        # state, so the SECOND window's write is what gets diffed.
        insp = sa_inspect(intel)
        insp.committed_state.clear()
        insp._commit_all(insp.dict)
        assert attributes.get_history(intel, "price_observations").has_changes() is False

        clock["now"] = NOW + WALL_WINDOW * 2  # well past the dedup window
        service.record_market_observation_sync(str(PLAYER), str(STATION_A), [{"commodity": "ORE", "price": 35}], db)

        assert len(intel.price_observations) == 2
        assert attributes.get_history(intel, "price_observations").has_changes() is True

    def test_falsifiability_in_place_append_on_the_same_baseline_is_invisible_to_sqlalchemy(self):
        """Companion tripwire (not exercising the SUT): proves the ABOVE
        test's methodology can actually detect the bug it claims to pin --
        the OLD buggy write pattern (in-place .append()) against the exact
        same reset-baseline fixture must leave has_changes() False, or the
        assertion above would be vacuous."""
        intel = ARIAMarketIntelligence(
            player_id=str(PLAYER), station_id=str(STATION_A), sector_id=uuid.uuid4(),
            commodity="ORE", price_observations=[{"price": 30}], average_price=30.0,
            data_points=1,
        )
        insp = sa_inspect(intel)
        insp.committed_state.clear()
        insp._commit_all(insp.dict)

        intel.price_observations.append({"price": 35})  # the OLD buggy pattern

        assert len(intel.price_observations) == 2  # the Python object DID change...
        history = attributes.get_history(intel, "price_observations")
        assert history.has_changes() is False  # ...but SQLAlchemy never saw it


# --------------------------------------------------------------------------- #
# Pattern identification re-anchor (>=10 observations required; see
# _identify_price_patterns -- unchanged pure logic, now sync).
# --------------------------------------------------------------------------- #

class TestPatternIdentification:
    def test_ten_plus_visits_to_one_port_yields_non_empty_patterns_port_b_stays_empty(self, service, monkeypatch):
        import src.services.aria_personal_intelligence_service as svc_module

        db = FakeMarketObsSession(
            players=[_docked_player()],
            stations=[
                _station(station_id=STATION_A, sector_id=SECTOR_5),
                _station(station_id=STATION_B, sector_id=SECTOR_5, name="Port B"),
            ],
        )

        clock = {"now": NOW}

        class _Clock(datetime):
            @classmethod
            def now(cls, tz=None):
                return clock["now"]

        monkeypatch.setattr(svc_module, "datetime", _Clock)

        # 12 separate visits to STATION_A, each well past the dedup window.
        for i in range(12):
            clock["now"] = NOW + WALL_WINDOW * 2 * (i + 1)
            service.record_market_observation_sync(
                str(PLAYER), str(STATION_A), [{"commodity": "ORE", "price": 30 + i}], db,
            )

        # A single visit to STATION_B -- far below the 10-observation floor.
        clock["now"] = NOW + WALL_WINDOW * 100
        service.record_market_observation_sync(
            str(PLAYER), str(STATION_B), [{"commodity": "ORE", "price": 50}], db,
        )

        by_station = {i.station_id: i for i in db.intelligences}
        assert by_station[str(STATION_A)].data_points == 12
        assert by_station[str(STATION_A)].identified_patterns != []

        assert by_station[str(STATION_B)].data_points == 1
        # Column(JSON, default=list)'s Python-callable default is only
        # applied by a real flush, which never happens in this DB-free
        # harness -- an unflushed row's never-assigned field is None, not
        # [] (never assigned since data_points < MIN_DATA_POINTS_FOR_PREDICTION).
        assert not by_station[str(STATION_B)].identified_patterns


# --------------------------------------------------------------------------- #
# Failure isolation -- never propagates.
# --------------------------------------------------------------------------- #

class TestFailureIsolation:
    def test_db_query_raising_does_not_propagate(self, service):
        class ExplodingSession(FakeMarketObsSession):
            def query(self, model):
                raise RuntimeError("boom -- simulated query failure")

        boom_db = ExplodingSession()
        # Must not raise.
        service.record_market_observation_sync(str(PLAYER), str(STATION_A), _three_commodity_payload(), boom_db)
        assert boom_db.intelligences == []

    def test_db_add_raising_does_not_propagate(self, service):
        class ExplodingAddSession(FakeMarketObsSession):
            def add(self, obj):
                raise RuntimeError("boom -- simulated db.add() failure")

        boom_db = ExplodingAddSession(players=[_docked_player()], stations=[_station()])
        service.record_market_observation_sync(str(PLAYER), str(STATION_A), _three_commodity_payload(), boom_db)
        assert boom_db.intelligences == []

    def test_never_commits_caller_owns_that(self, service, db):
        """FLUSH-ONLY contract: FakeMarketObsSession has no .commit() at
        all -- if the SUT ever called db.commit() this test would
        AttributeError."""
        service.record_market_observation_sync(str(PLAYER), str(STATION_A), _three_commodity_payload(), db)
        # no exception -> no commit() was attempted

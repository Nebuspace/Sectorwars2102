"""Unit coverage for the EconomicMetrics daily-snapshot enrichment
(WO-ECON-METRICS-ENRICH).

The daily snapshot (npc_scheduler_service._run_economic_metrics_snapshot_
sync) wrote only 9 of EconomicMetrics' ~24 columns; the calculators for
inflation/health/volatility/wealth-disparity already existed in
EconomyAnalyticsService but were never wired in. This file proves
``_compute_daily_economic_enrichment`` -- the pure, session-injectable core
extracted for this WO (mirrors reconcile_region_treasuries's pure-fn split,
see test_treasury_reconciliation.py) -- against a live, mutable in-memory
fake, never a real DB.

TEST BOUNDARY: EconomyAnalyticsService._calculate_inflation_rates /
_calculate_price_volatility / _calculate_wealth_distribution are EXISTING,
already-shipped calculators (used today by the admin dashboard's
get_economic_metrics()) -- this WO's job is to WIRE them into the sweep,
not re-prove their internal correctness. Those three are monkeypatched to
canned returns so the assertions below are about the NEW aggregation/
persist code, not about re-litigating settled calculator internals.
_calculate_health_score is exercised for REAL (pure Python, no DB) to prove
the indicators/velocity/wealth_dist wiring into it is correct.

No live DB is used. Per the codebase's mock-only unit-test convention (see
test_treasury_reconciliation.py's FakeReconSession / test_route_runs_
retention.py's FakeRouteRunQuery), the fake session below interprets the
REAL SQLAlchemy filter()/group_by()/having()/order_by() clauses the SUT
builds, and a shared counter increments on every terminal call (.all() /
.first()) that would be a real DB round trip -- proving the enrichment
issues a FIXED, small number of queries regardless of scale (no per-player
or per-transaction Python loop).
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from typing import Any, Dict
from unittest.mock import MagicMock

import pytest
from sqlalchemy.sql import operators

import src.services.npc_scheduler_service as npc_scheduler_service
from src.models.market_transaction import MarketPrice, MarketTransaction
from src.models.player import Player
from src.services.economy_analytics_service import EconomyAnalyticsService
from src.services.npc_scheduler_service import _compute_daily_economic_enrichment

NOW = datetime(2026, 7, 8, 12, 0, 0)
WINDOW_START = NOW - timedelta(hours=24)


# --------------------------------------------------------------------------- #
# In-memory fake rows -- only the columns the enrichment actually reads
# --------------------------------------------------------------------------- #

class FakePlayerRow:
    def __init__(self, credits, is_active=True):
        self.credits = credits
        self.is_active = is_active


class FakeTxnRow:
    def __init__(self, *, commodity, quantity, timestamp, total_value,
                 sector_id=None, station_id=None, player_id=None):
        self.id = uuid.uuid4()
        self.commodity = commodity
        self.quantity = quantity
        self.timestamp = timestamp
        self.total_value = total_value
        self.sector_id = sector_id
        self.station_id = station_id
        self.player_id = player_id


class FakePriceRow:
    def __init__(self, commodity, buy_price, sell_price):
        self.commodity = commodity
        self.buy_price = buy_price
        self.sell_price = sell_price


# --------------------------------------------------------------------------- #
# In-memory fake session -- interprets the SUT's real query shapes
# --------------------------------------------------------------------------- #

def _condition_matches(row, condition) -> bool:
    left, op = condition.left, condition.operator
    actual = getattr(row, left.key)
    if op is operators.eq:
        return actual == condition.right.value
    if op is operators.ge:
        return actual >= condition.right.value
    if op is operators.is_not:
        return actual is not None
    if op is operators.is_:
        # Column.is_(True)/(False) -- right is a valueless True_/False_
        # sentinel (no .value), not a BindParameter.
        truthy = type(condition.right).__name__ == "True_"
        return bool(actual) is truthy
    raise AssertionError(f"unhandled operator {op!r} on column {left.key!r}")


class _FakeSimpleQuery:
    """db.query(<col>, ...).filter(...).all() with no grouping -- used for
    the Player credits fetch and the unfiltered MarketPrice fetch."""

    def __init__(self, store, session, entities):
        self._store = store
        self._session = session
        self._entities = entities
        self._conditions: tuple = ()

    def filter(self, *conditions):
        self._conditions = self._conditions + conditions
        return self

    def all(self):
        self._session.queries += 1
        matching = [r for r in self._store if all(_condition_matches(r, c) for c in self._conditions)]
        keys = [e.key for e in self._entities]
        return [tuple(getattr(r, k) for k in keys) for r in matching]


class _FakeTxnGroupQuery:
    """Interprets db.query(<group_col>[, <agg_func>(<value_col>)]).filter(...)
    .group_by(<group_col>)[.having(func.<fn>(<col>) >= <val>)][.order_by(
    <agg_func>(<col>).desc())].all()/.first() against an in-memory
    MarketTransaction row store -- the shape every one of the enrichment's
    new GROUP BY queries uses (sum/count/min reducers only)."""

    _REDUCERS = {"sum": sum, "count": len, "min": min}

    def __init__(self, store, session, entities):
        self._store = store
        self._session = session
        self._entities = entities
        self._conditions: tuple = ()
        self._group_key = None
        self._having = None

    def filter(self, *conditions):
        self._conditions = self._conditions + conditions
        return self

    def group_by(self, col):
        self._group_key = col.key
        return self

    def having(self, condition):
        self._having = condition
        return self

    def order_by(self, *_args):
        return self  # sort direction is inferred from the aggregate below

    def _groups(self):
        matching = [r for r in self._store if all(_condition_matches(r, c) for c in self._conditions)]
        groups: Dict[Any, list] = {}
        for row in matching:
            groups.setdefault(getattr(row, self._group_key), []).append(row)
        if self._having is not None:
            fn_name = self._having.left.name
            col_key = self._having.left.clauses.clauses[0].key
            threshold = self._having.right.value
            reducer = self._REDUCERS[fn_name]
            groups = {
                k: rows for k, rows in groups.items()
                if reducer([getattr(r, col_key) for r in rows]) >= threshold
            }
        return groups

    def _rows(self):
        groups = self._groups()
        agg = self._entities[1] if len(self._entities) > 1 else None
        out = []
        for key, group_rows in groups.items():
            if agg is None:
                out.append((key,))
                continue
            fn_name = agg.name
            if fn_name == "count":
                value = len(group_rows)
            else:
                col_key = agg.clauses.clauses[0].key
                value = self._REDUCERS[fn_name]([getattr(r, col_key) for r in group_rows])
            out.append((key, value))
        if agg is not None:
            out.sort(key=lambda r: r[1], reverse=True)
        return out

    def all(self):
        self._session.queries += 1
        return self._rows()

    def first(self):
        self._session.queries += 1
        rows = self._rows()
        return rows[0] if rows else None


class FakeEnrichmentSession:
    """Minimal db double: dispatches db.query(*cols) by the owning mapped
    class of the first column (a real InstrumentedAttribute exposes
    ``.class_``). ``queries`` counts every real round trip."""

    def __init__(self, *, players=(), transactions=(), prices=()):
        self.queries = 0
        self.players = list(players)
        self.transactions = list(transactions)
        self.prices = list(prices)

    def query(self, *cols):
        owner = getattr(cols[0], "class_", cols[0])
        if owner is Player:
            return _FakeSimpleQuery(self.players, self, cols)
        if owner is MarketTransaction:
            return _FakeTxnGroupQuery(self.transactions, self, cols)
        if owner is MarketPrice:
            return _FakeSimpleQuery(self.prices, self, cols)
        raise AssertionError(f"unexpected query owner {owner!r}")


# --------------------------------------------------------------------------- #
# Shared fixture -- no ties, so most_traded/most_active_sector/most_valuable_
# station/new_traders each have one unambiguous winner.
# --------------------------------------------------------------------------- #

def _fixture_session():
    player_a = uuid.uuid4()  # new trader: first-ever txn inside the window
    player_b = uuid.uuid4()  # new trader: first-ever txn inside the window
    player_c = uuid.uuid4()  # NOT new: first-ever txn predates the window

    transactions = [
        # player_a: ore, sector 5, station_1 -- inside window
        FakeTxnRow(commodity="ore", quantity=10, timestamp=NOW - timedelta(hours=1),
                   total_value=1000, sector_id=5, station_id="station_1", player_id=player_a),
        # player_b: ore, sector 5, station_1 -- inside window
        FakeTxnRow(commodity="ore", quantity=15, timestamp=NOW - timedelta(hours=2),
                   total_value=1500, sector_id=5, station_id="station_1", player_id=player_b),
        # player_b: fuel, sector 7, station_2 -- inside window
        FakeTxnRow(commodity="fuel", quantity=5, timestamp=NOW - timedelta(minutes=30),
                   total_value=200, sector_id=7, station_id="station_2", player_id=player_b),
        # player_c: ore, OUTSIDE the window -- excludes player_c from
        # new_traders and must not inflate any window-scoped aggregate.
        FakeTxnRow(commodity="ore", quantity=100, timestamp=NOW - timedelta(hours=48),
                   total_value=5000, sector_id=5, station_id="station_1", player_id=player_c),
        # player_c: fuel, sector 9, station_3 -- inside window, but player_c's
        # FIRST-ever trade (above) predates the window, so not "new".
        FakeTxnRow(commodity="fuel", quantity=1, timestamp=NOW - timedelta(minutes=10),
                   total_value=50, sector_id=9, station_id="station_3", player_id=player_c),
    ]
    players = [
        FakePlayerRow(credits=80000),
        FakePlayerRow(credits=20000),
        FakePlayerRow(credits=50000),
    ]
    prices = [
        FakePriceRow("ore", buy_price=14, sell_price=20),
        FakePriceRow("fuel", buy_price=10, sell_price=15),
    ]
    return FakeEnrichmentSession(players=players, transactions=transactions, prices=prices), {
        "player_a": player_a, "player_b": player_b, "player_c": player_c,
    }


@pytest.fixture(autouse=True)
def _mock_existing_calculators(monkeypatch):
    """Monkeypatch the three EXISTING EconomyAnalyticsService calculators to
    canned, non-default returns -- see module docstring's TEST BOUNDARY.
    Held on the instance's class so ``EconomyAnalyticsService(db)`` inside
    the SUT picks them up regardless of how it constructs the service."""
    monkeypatch.setattr(
        EconomyAnalyticsService, "_calculate_inflation_rates",
        lambda self: {"ore": 5.0, "fuel": -1.0},
    )
    monkeypatch.setattr(
        EconomyAnalyticsService, "_calculate_price_volatility",
        lambda self: {"ore": 4.0, "fuel": 2.0},
    )
    monkeypatch.setattr(
        EconomyAnalyticsService, "_calculate_wealth_distribution",
        lambda self: {
            "gini_coefficient": 0.42,
            "wealth_brackets": {"poor": 0, "middle": 1, "wealthy": 1, "ultra_wealthy": 0},
            "total_players": 2,
            "median_wealth": 30000,
        },
    )


# --------------------------------------------------------------------------- #
# Core acceptance criteria
# --------------------------------------------------------------------------- #

class TestCoreEnrichment:
    def test_snapshot_carries_computed_fields(self):
        session, _ids = _fixture_session()

        fields = _compute_daily_economic_enrichment(
            session, window_start=WINDOW_START, credit_velocity=0.25,
        )

        # Non-default health score, in the model's documented 0-1 scale.
        assert fields["economic_health_score"] != 0.5
        assert 0.0 <= fields["economic_health_score"] <= 1.0

        # Computed inflation / volatility -- mean of the (mocked) per-
        # commodity calculator output, proving the reduction wiring.
        assert fields["inflation_rate"] == pytest.approx((5.0 + -1.0) / 2)
        assert fields["market_volatility"] == pytest.approx((4.0 + 2.0) / 2)

        # Dominant commodity in the trailing-24h window is "ore" (25 units:
        # player_a's 10 + player_b's 15; player_c's 100 units are OUTSIDE
        # the window and must not count).
        assert fields["most_traded_commodity"] == "ore"
        assert fields["least_traded_commodity"] == "fuel"

        # Disparity + median/richest credits.
        assert 0.0 < fields["economic_disparity_index"] <= 1.0
        assert fields["economic_disparity_index"] == pytest.approx(0.42)
        assert fields["median_player_credits"] == 30000
        assert fields["richest_player_credits"] == 80000  # real MAX(Player.credits)

        # New traders: player_a + player_b (first-ever trade inside the
        # window); player_c excluded (first-ever trade predates it).
        assert fields["new_traders"] == 2

        # Sector/station leaders (no ties in the fixture).
        assert fields["most_active_sector"] == 5
        assert fields["most_valuable_station"] == "station_1"

        # commodity_price_index / average_profit_margin are real numbers,
        # not left at the untouched column default, given live MarketPrice
        # rows exist.
        assert fields["commodity_price_index"] != 100.0
        assert fields["average_profit_margin"] != 0.0

    def test_determinism_across_a_rerun(self):
        session, _ids = _fixture_session()

        first = _compute_daily_economic_enrichment(
            session, window_start=WINDOW_START, credit_velocity=0.25,
        )
        second = _compute_daily_economic_enrichment(
            session, window_start=WINDOW_START, credit_velocity=0.25,
        )

        assert first == second


# --------------------------------------------------------------------------- #
# Boundedness -- the enrichment is a FIXED number of queries regardless of
# scale (no per-player / per-transaction Python loop).
# --------------------------------------------------------------------------- #

class TestBoundedness:
    def test_query_count_is_fixed_regardless_of_scale(self):
        small_session, _ = _fixture_session()

        large_players = [FakePlayerRow(credits=1000 * i) for i in range(1, 51)]
        large_transactions = []
        for i in range(200):
            large_transactions.append(FakeTxnRow(
                commodity=f"commodity_{i % 7}",
                quantity=i + 1,
                timestamp=NOW - timedelta(minutes=i),
                total_value=(i + 1) * 10,
                sector_id=i % 11,
                station_id=f"station_{i % 13}",
                player_id=uuid.uuid4(),
            ))
        large_prices = [FakePriceRow(f"commodity_{i}", buy_price=10 + i, sell_price=15 + i) for i in range(7)]
        large_session = FakeEnrichmentSession(
            players=large_players, transactions=large_transactions, prices=large_prices,
        )

        _compute_daily_economic_enrichment(small_session, window_start=WINDOW_START, credit_velocity=0.1)
        _compute_daily_economic_enrichment(large_session, window_start=WINDOW_START, credit_velocity=0.1)

        # richest_player_credits (Player fetch) + most/least traded
        # commodity + most_active_sector + most_valuable_station +
        # new_traders + commodity_price_index/average_profit_margin
        # (shared MarketPrice fetch) = 6 fixed round trips. The three
        # EXISTING analytics calculators are monkeypatched away (see
        # TEST BOUNDARY) so they contribute zero queries to THIS count --
        # this pins the NEW code's own query cost, not the pre-existing
        # calculators' internal cost.
        assert small_session.queries == 6
        assert large_session.queries == 6


# --------------------------------------------------------------------------- #
# Calculator-failure degradation -- one calculator raising must not abort
# the snapshot, and must leave ONLY the fields it feeds at the column
# default.
# --------------------------------------------------------------------------- #

class TestDegradation:
    def test_one_calculator_raising_leaves_only_its_fields_at_default(self, monkeypatch):
        session, _ids = _fixture_session()
        warn_mock = MagicMock()
        monkeypatch.setattr(npc_scheduler_service.logger, "warning", warn_mock)

        def _boom(self):
            raise RuntimeError("inflation calculator exploded")

        monkeypatch.setattr(EconomyAnalyticsService, "_calculate_inflation_rates", _boom)

        fields = _compute_daily_economic_enrichment(
            session, window_start=WINDOW_START, credit_velocity=0.25,
        )

        # The failed calculator's own field stays at the column default...
        assert fields["inflation_rate"] == 0.0
        warn_mock.assert_called()

        # ...but every OTHER field still computed normally -- the snapshot
        # was not aborted by the one failure.
        assert fields["market_volatility"] == pytest.approx((4.0 + 2.0) / 2)
        assert fields["most_traded_commodity"] == "ore"
        assert fields["new_traders"] == 2
        assert fields["richest_player_credits"] == 80000
        assert fields["economic_disparity_index"] == pytest.approx(0.42)

    def test_a_second_calculator_can_fail_independently(self, monkeypatch):
        """Falsifiability: a DIFFERENT calculator failing degrades a
        DIFFERENT field, proving the try/except blocks are genuinely
        per-field and not one shared catch-all."""
        session, _ids = _fixture_session()

        def _boom(self):
            raise RuntimeError("wealth distribution exploded")

        monkeypatch.setattr(EconomyAnalyticsService, "_calculate_wealth_distribution", _boom)

        fields = _compute_daily_economic_enrichment(
            session, window_start=WINDOW_START, credit_velocity=0.25,
        )

        assert fields["economic_disparity_index"] == 0.0  # default, not 0.42
        assert fields["median_player_credits"] == 0        # default, not 30000
        assert fields["inflation_rate"] == pytest.approx((5.0 + -1.0) / 2)  # unaffected
        assert fields["most_traded_commodity"] == "ore"  # unaffected

"""Unit coverage for the ARIA observation log + recommendation-aggregate
engine (WO-ARIA-OBS-LOG, ADR-0038).

No live DB is used. Per the codebase's mock-only unit-test convention (see
test_route_runs_retention.py's FakeRouteRunQuery / test_economic_metrics_
enrich.py's _FakeTxnGroupQuery), ``FakeObsSession`` interprets the REAL
SQLAlchemy query()/filter()/group_by()/having()/first()/delete() clauses
the service builds against a live, mutable in-memory row store -- built
from REAL ``ARIATradingObservation`` / ``ARIAQuantumCache`` ORM instances
(not hand-rolled duck-types), so the enum-typed ``action`` /
``outcome_classification`` columns and the SQL-aggregate clause shapes are
exercised for real.

record_trade_observation / get_top_routes / get_reliable_commodities /
get_watch_out_commodities / compute_recommendation_aggregates are all
SYNC methods on a sync ``Session`` (see the service module's OBSERVATION
LOG section docstring for why) -- this fake session is a plain sync
double, no asyncio anywhere in this file.
"""
from __future__ import annotations

import ast
import inspect
import os
import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy.sql import operators

from src.models.aria_personal_intelligence import (
    ARIAPersonalMemory,
    ARIAQuantumCache,
    ARIATradingObservation,
    ObservationAction,
    ObservationOutcome,
)
from src.services.aria_personal_intelligence_service import ARIAPersonalIntelligenceService

NOW = datetime(2026, 7, 10, 12, 0, 0, tzinfo=UTC)


# --------------------------------------------------------------------------- #
# In-memory fake session -- interprets the SUT's real SQLAlchemy clauses
# --------------------------------------------------------------------------- #

def _condition_matches(row, condition) -> bool:
    left, op = condition.left, condition.operator
    col_key = left.key
    actual = getattr(row, col_key)
    right = condition.right

    if op is operators.eq:
        return actual == right.value
    if op is operators.ge:
        if actual is None:
            return False
        return actual >= right.value
    if op is operators.gt:
        if actual is None:
            return False
        return actual > right.value
    if op is operators.le:
        if actual is None:
            return False
        return actual <= right.value
    if op is operators.is_not:
        # .isnot(None) -- right is a Null() sentinel (no .value); other
        # is_not() uses (e.g. Column.is_(True)) aren't emitted by this SUT.
        if type(right).__name__ == "Null":
            return actual is not None
        raise AssertionError(f"unhandled is_not right-hand sentinel {right!r}")
    raise AssertionError(f"unhandled operator {op!r} on column {col_key!r}")


class _FakeObsGroupQuery:
    """Interprets db.query(<group_col>, ..., func.sum(<col>)|func.count(<col>))
    .filter(...).group_by(<group_col>, ...)[.having(func.count(<col>) >= N)]
    .all() -- the shape every observation-log aggregate query in the SUT
    uses. Selected columns are always [the group_by columns in order,
    followed by aggregate func expressions], matching the SUT's own
    query-construction convention (see service module docstrings)."""

    def __init__(self, store, session, columns):
        self._store = store
        self._session = session
        self._columns = columns
        self._conditions: tuple = ()
        self._group_cols: tuple = ()
        self._having = None

    def filter(self, *conditions):
        self._conditions = self._conditions + conditions
        return self

    def group_by(self, *cols):
        self._group_cols = cols
        return self

    def having(self, condition):
        self._having = condition
        return self

    def _matching(self):
        return [r for r in self._store if all(_condition_matches(r, c) for c in self._conditions)]

    def _groups(self):
        groups: dict = {}
        for row in self._matching():
            key = tuple(getattr(row, c.key) for c in self._group_cols)
            groups.setdefault(key, []).append(row)
        if self._having is not None:
            fn_name = self._having.left.name
            col_key = self._having.left.clauses.clauses[0].key
            threshold = self._having.right.value
            assert self._having.operator is operators.ge, "only HAVING count >= N is used by the SUT"
            assert fn_name == "count", "only HAVING on count() is used by the SUT"
            # SQL COUNT(col) semantics -- excludes NULLs of that specific
            # column (equivalent to COUNT(*) here since every row has a
            # non-null id, but kept faithful rather than assumed).
            groups = {
                k: rows for k, rows in groups.items()
                if len([r for r in rows if getattr(r, col_key) is not None]) >= threshold
            }
        return groups

    def all(self):
        self._session.queries += 1
        self._session.group_queries += 1
        n_group = len(self._group_cols)
        agg_specs = self._columns[n_group:]
        out = []
        for key, rows in self._groups().items():
            projected = list(key)
            for agg in agg_specs:
                fn_name = agg.name
                if fn_name == "count":
                    projected.append(len(rows))
                elif fn_name == "sum":
                    col_key = agg.clauses.clauses[0].key
                    projected.append(sum(getattr(r, col_key) for r in rows))
                else:
                    raise AssertionError(f"unhandled aggregate function {fn_name!r}")
            out.append(tuple(projected))
        return out


class _FakeCacheQuery:
    """db.query(ARIAQuantumCache).filter(...).first()/.delete() -- the
    shape the recommendation-aggregate cache read/write/invalidate paths
    use."""

    def __init__(self, store, session, columns):
        self._store = store
        self._session = session
        self._conditions: tuple = ()

    def filter(self, *conditions):
        self._conditions = self._conditions + conditions
        return self

    def _matching(self):
        return [r for r in self._store if all(_condition_matches(r, c) for c in self._conditions)]

    def first(self):
        self._session.queries += 1
        rows = self._matching()
        return rows[0] if rows else None

    def delete(self, synchronize_session=False):
        victims = self._matching()
        for v in victims:
            self._store.remove(v)
        return len(victims)


class _NoOpSavepoint:
    """Stand-in for the real SQLAlchemy Session.begin_nested() context
    manager (WO-SWEEP-QUANTUM-CACHE-COLUMN's savepoint around
    _invalidate_aggregate_cache_sync's DELETE) -- no real transactional
    backing, success just falls through; a raised exception inside the
    `with` block propagates normally via Python's own context-manager
    protocol, matching record_trade_observation's own outer try/except
    (this method itself has no try/except of its own -- see its
    docstring). Real SAVEPOINT failure-isolation behavior is proven
    against a real SQLite Session in
    test_aria_quantum_cache_column_savepoint.py, not re-proven here."""

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False  # never swallow -- let the caller's own try/except handle it


class FakeObsSession:
    """Minimal sync db double: dispatches db.query(*cols) by owning mapped
    class (a real InstrumentedAttribute exposes ``.class_``; a bare model
    class dispatches on itself). ``queries`` counts every round trip;
    ``group_queries`` isolates the observation-log GROUP BY query cost
    specifically (used to prove cache-hit boundedness)."""

    def __init__(self, observations=(), cache_rows=(), memories=()):
        self.observations = list(observations)
        self.cache_rows = list(cache_rows)
        self.memories = list(memories)
        self.queries = 0
        self.group_queries = 0
        self.added = []
        self.flushes = 0

    def add(self, obj):
        self.added.append(obj)
        if isinstance(obj, ARIATradingObservation):
            self.observations.append(obj)
        elif isinstance(obj, ARIAQuantumCache):
            self.cache_rows.append(obj)
        elif isinstance(obj, ARIAPersonalMemory):
            self.memories.append(obj)
        else:
            raise AssertionError(f"unexpected db.add() of {type(obj)!r}")

    def query(self, *cols):
        owner = cols[0] if isinstance(cols[0], type) else getattr(cols[0], "class_", cols[0])
        if owner is ARIATradingObservation:
            return _FakeObsGroupQuery(self.observations, self, cols)
        if owner is ARIAQuantumCache:
            return _FakeCacheQuery(self.cache_rows, self, cols)
        if owner is ARIAPersonalMemory:
            # Same filter()/first() shape as the cache lookups (no grouping) --
            # _FakeCacheQuery is generic enough to serve both.
            return _FakeCacheQuery(self.memories, self, cols)
        raise AssertionError(f"unexpected query owner {owner!r}")

    def begin_nested(self):
        return _NoOpSavepoint()

    def flush(self):
        self.flushes += 1


# --------------------------------------------------------------------------- #
# Fixture data -- real ARIATradingObservation instances (never a duck-type
# stand-in), so the enum-typed columns and real mapped attributes are
# exercised for real.
# --------------------------------------------------------------------------- #

PLAYER = uuid.uuid4()
STATION_A = uuid.uuid4()
STATION_B = uuid.uuid4()
STATION_C = uuid.uuid4()
STATION_D = uuid.uuid4()


def _obs(
    *, commodity, action, source_station_id, dest_station_id=None,
    profit=None, player_id=None, quantity=10, unit_price=50,
) -> ARIATradingObservation:
    outcome = None
    if action is ObservationAction.sell and profit is not None:
        outcome = (
            ObservationOutcome.profit if profit > 0
            else ObservationOutcome.break_even if profit == 0
            else ObservationOutcome.loss
        )
    return ARIATradingObservation(
        id=uuid.uuid4(),
        # Service methods are called with str(PLAYER) (matches every real
        # caller's `player_id: str` signature) -- store the SAME string
        # representation here so the fake session's equality filter
        # (actual == right.value) actually matches, exactly as it would
        # against a real UUID column comparing to a UUID-typed bind.
        player_id=str(player_id) if player_id is not None else str(PLAYER),
        commodity=commodity,
        action=action,
        source_station_id=source_station_id,
        dest_station_id=dest_station_id,
        quantity=quantity,
        unit_price=unit_price,
        total_credits=quantity * unit_price,
        profit=profit,
        outcome_classification=outcome,
        observed_at=NOW,
    )


def _seeded_observations() -> list:
    rows = []

    # ROUTE 1: ORGANICS A->B, 5 profitable sells (all clear the 100cr floor).
    # count=5 >= 3, avg profit = 530.
    for profit in (500, 600, 550, 520, 480):
        rows.append(_obs(
            commodity="ORGANICS", action=ObservationAction.sell,
            source_station_id=STATION_A, dest_station_id=STATION_B, profit=profit,
        ))

    # ROUTE 2: ORE A->C, only 2 sells -- excluded by count < 3.
    for profit in (200, 250):
        rows.append(_obs(
            commodity="ORE", action=ObservationAction.sell,
            source_station_id=STATION_A, dest_station_id=STATION_C, profit=profit,
        ))

    # ROUTE 3: FUEL B->D, 3 wash-trade-band sells (all < 100cr) -- entirely
    # excluded from the top-routes candidate pool (not merely diluted).
    for profit in (50, 80, 90):
        rows.append(_obs(
            commodity="FUEL", action=ObservationAction.sell,
            source_station_id=STATION_B, dest_station_id=STATION_D, profit=profit,
        ))

    # ROUTE 4: LUXURY C->D, 3 qualifying sells, lower avg (100) than ROUTE 1
    # -- proves ranking + LIMIT ordering.
    for profit in (100, 100, 100):
        rows.append(_obs(
            commodity="LUXURY", action=ObservationAction.sell,
            source_station_id=STATION_C, dest_station_id=STATION_D, profit=profit,
        ))

    # sell with NO dest_station_id -- must be excluded from top-routes
    # candidate pool by the explicit dest_station_id IS NOT NULL filter.
    # Unique commodity (not ORGANICS) so this can never bleed into ROUTE 1's
    # reliable-commodities group, which ignores dest_station_id entirely.
    for _ in range(3):
        rows.append(_obs(
            commodity="WIDGETS", action=ObservationAction.sell,
            source_station_id=STATION_A, dest_station_id=None, profit=999,
        ))

    # RELIABLE-commodity negative case: COPPER at station_A, 5 sells, only
    # 3/5 clear the floor -- success_rate 0.6, below BOTH the reliable
    # (>=0.7) and watch-out (<=0.3) thresholds. True negative for both.
    # Unique commodity (not ORE) so it can't merge into ROUTE 2's (ORE, A, C)
    # top-routes group -- get_reliable_commodities groups by (commodity,
    # source_station) only, so a shared commodity here would have silently
    # inflated ROUTE 2's top-routes candidate pool too.
    for profit in (150, 150, 150, 50, 30):
        rows.append(_obs(
            commodity="COPPER", action=ObservationAction.sell,
            source_station_id=STATION_A, dest_station_id=STATION_C, profit=profit,
        ))

    # WATCH-OUT commodity: SCRAP, 5 sells across two stations (proves the
    # commodity-only, cross-station GROUP BY), only 1/5 clears the floor.
    for station, profit in (
        (STATION_A, 150), (STATION_A, -50), (STATION_A, -80),
        (STATION_B, 20), (STATION_B, 10),
    ):
        rows.append(_obs(
            commodity="SCRAP", action=ObservationAction.sell,
            source_station_id=station, dest_station_id=STATION_D, profit=profit,
        ))

    # Below the count>=5 floor despite a qualifying (low) success rate --
    # proves the sample-count HAVING gate is real, not just the rate check.
    for profit in (-100, -200):
        rows.append(_obs(
            commodity="RARE_GAS", action=ObservationAction.sell,
            source_station_id=STATION_A, dest_station_id=STATION_B, profit=profit,
        ))

    # Buy-leg noise -- profit is None, must never inflate any denominator.
    for _ in range(10):
        rows.append(_obs(
            commodity="ORGANICS", action=ObservationAction.buy,
            source_station_id=STATION_A, dest_station_id=None, profit=None,
        ))

    return rows


@pytest.fixture()
def service() -> ARIAPersonalIntelligenceService:
    return ARIAPersonalIntelligenceService()


@pytest.fixture()
def db() -> FakeObsSession:
    return FakeObsSession(observations=_seeded_observations())


# --------------------------------------------------------------------------- #
# record_trade_observation -- insert shape + sync entry point
# --------------------------------------------------------------------------- #

class TestRecordTradeObservation:
    def test_is_a_genuinely_sync_method(self, service):
        assert not inspect.iscoroutinefunction(service.record_trade_observation)

    def test_sell_leg_insert_shape_and_outcome_classification(self, service, db):
        trade_result = {
            "commodity": "ORGANICS",
            "action": "sell",
            "source_station_id": STATION_A,
            "dest_station_id": STATION_B,
            "source_sector_id": 5,
            "dest_sector_id": 7,
            "quantity": 20,
            "unit_price": 40,
            "total_credits": 800,
            "profit": 300,
        }
        result = service.record_trade_observation(str(PLAYER), trade_result, db)

        assert result is not None
        assert result in db.observations  # db.add() landed via the fake session
        assert result.commodity == "ORGANICS"
        assert result.action is ObservationAction.sell
        assert result.profit == 300
        assert result.outcome_classification is ObservationOutcome.profit
        assert result.source_sector_id == 5
        assert result.dest_sector_id == 7

    def test_buy_leg_has_no_outcome_classification(self, service, db):
        trade_result = {
            "commodity": "ORE", "action": "buy",
            "source_station_id": STATION_A, "quantity": 10,
            "unit_price": 20, "total_credits": 200,
        }
        result = service.record_trade_observation(str(PLAYER), trade_result, db)

        assert result is not None
        assert result.profit is None
        assert result.outcome_classification is None

    def test_loss_and_break_even_classification(self, service, db):
        loss = service.record_trade_observation(str(PLAYER), {
            "commodity": "ORE", "action": "sell", "source_station_id": STATION_A,
            "dest_station_id": STATION_B, "quantity": 5, "unit_price": 10,
            "total_credits": 50, "profit": -25,
        }, db)
        even = service.record_trade_observation(str(PLAYER), {
            "commodity": "ORE", "action": "sell", "source_station_id": STATION_A,
            "dest_station_id": STATION_B, "quantity": 5, "unit_price": 10,
            "total_credits": 50, "profit": 0,
        }, db)

        assert loss.outcome_classification is ObservationOutcome.loss
        assert even.outcome_classification is ObservationOutcome.break_even

    def test_never_commits_caller_owns_that(self, service, db):
        """FLUSH-FREE contract, mirrors record_combat_memory_sync: only
        db.add()s. FakeObsSession has no .commit() at all -- if the SUT
        ever called db.commit() this test would AttributeError."""
        service.record_trade_observation(str(PLAYER), {
            "commodity": "ORE", "action": "buy", "source_station_id": STATION_A,
            "quantity": 1, "unit_price": 1, "total_credits": 1,
        }, db)  # no exception -> no commit() was attempted


# --------------------------------------------------------------------------- #
# record_trade_memory_sync -- sync twin of record_trade_memory (team-lead
# addendum: trading.py's sync-Session routes cannot call the async
# record_trade_memory -- its internal ``await db.execute(select(...))``
# deterministically raises against a sync Session, swallowed by its own
# except, so zero ARIAPersonalMemory rows ever persisted through the trade
# path). Mechanical port of record_combat_memory_sync's precedent.
# --------------------------------------------------------------------------- #

class TestRecordTradeMemorySync:
    def test_is_a_genuinely_sync_method(self, service):
        assert not inspect.iscoroutinefunction(service.record_trade_memory_sync)

    def test_inserts_one_market_memory_with_expected_content(self, service, db):
        service.record_trade_memory_sync(str(PLAYER), {
            "station_name": "Auriga Prime", "action": "sell",
            "commodity": "ORGANICS", "quantity": 20, "total_value": 2000,
            "profit": 600,
        }, db)

        assert len(db.memories) == 1
        memory = db.memories[0]
        assert memory.memory_type == "market"
        assert memory.player_id == str(PLAYER)
        assert memory.confidence_level == pytest.approx(0.9)

        content = service._decrypt_memory(memory.memory_content["encrypted"])
        assert content["event"] == "trade_transaction"
        assert content["station_name"] == "Auriga Prime"
        assert content["action"] == "sell"
        assert content["commodity"] == "ORGANICS"
        assert content["quantity"] == 20
        assert content["total_value"] == 2000
        assert content["profit"] == 600

    def test_profitable_trade_raises_importance_above_baseline(self, service, db):
        service.record_trade_memory_sync(str(PLAYER), {
            "station_name": "X", "action": "sell", "commodity": "ORE",
            "quantity": 1, "total_value": 5000, "profit": 5000,
        }, db)
        assert db.memories[0].importance_score > 0.5

    def test_unprofitable_trade_uses_baseline_importance(self, service, db):
        service.record_trade_memory_sync(str(PLAYER), {
            "station_name": "X", "action": "buy", "commodity": "ORE",
            "quantity": 1, "total_value": 100,
        }, db)
        assert db.memories[0].importance_score == pytest.approx(0.5)

    def test_duplicate_trade_content_is_deduplicated_by_hash(self, service, db, monkeypatch):
        import src.services.aria_personal_intelligence_service as svc_module

        trade = {
            "station_name": "Auriga Prime", "action": "sell",
            "commodity": "ORGANICS", "quantity": 20, "total_value": 2000,
            "profit": 600,
        }

        # The memory's content (and therefore its dedup hash) includes a
        # datetime.now() timestamp -- pin the clock so two back-to-back
        # calls with identical trade data hash identically, genuinely
        # exercising the dedup path rather than relying on two calls
        # landing in the same microsecond by chance.
        class _FixedDatetime(datetime):
            @classmethod
            def now(cls, tz=None):
                return datetime(2026, 7, 10, 12, 0, 0, tzinfo=UTC)

        monkeypatch.setattr(svc_module, "datetime", _FixedDatetime)

        service.record_trade_memory_sync(str(PLAYER), trade, db)
        service.record_trade_memory_sync(str(PLAYER), trade, db)

        assert len(db.memories) == 1

    def test_never_commits_caller_owns_that(self, service, db):
        service.record_trade_memory_sync(str(PLAYER), {
            "station_name": "X", "action": "buy", "commodity": "ORE",
            "quantity": 1, "total_value": 1,
        }, db)  # FakeObsSession has no .commit() -- would AttributeError

    def test_db_query_raising_does_not_propagate(self, service, db):
        class ExplodingSession(FakeObsSession):
            def query(self, *cols):
                raise RuntimeError("boom -- simulated dedup lookup failure")

        boom_db = ExplodingSession()
        # Must not raise -- the non-blocking contract applies here too.
        service.record_trade_memory_sync(str(PLAYER), {
            "station_name": "X", "action": "buy", "commodity": "ORE",
            "quantity": 1, "total_value": 1,
        }, boom_db)
        assert boom_db.memories == []


# --------------------------------------------------------------------------- #
# Failure isolation -- the non-blocking contract: a raising insert must
# never propagate out of record_trade_observation.
# --------------------------------------------------------------------------- #

class TestFailureIsolation:
    def test_unrecognised_action_returns_none(self, service, db):
        before = len(db.observations)

        result = service.record_trade_observation(str(PLAYER), {
            "commodity": "ORE", "action": "transfer", "source_station_id": STATION_A,
            "quantity": 1, "unit_price": 1, "total_credits": 1,
        }, db)

        assert result is None
        assert len(db.observations) == before  # nothing was added

    def test_missing_required_key_does_not_raise(self, service, db):
        # No KeyError escapes -- missing "commodity" is fatal to construction
        # but must be swallowed, not propagated into a live trade.
        result = service.record_trade_observation(str(PLAYER), {
            "action": "sell", "source_station_id": STATION_A,
        }, db)
        assert result is None

    def test_db_add_raising_does_not_propagate(self, service, db):
        class ExplodingSession(FakeObsSession):
            def add(self, obj):
                raise RuntimeError("boom -- simulated db.add() failure")

        boom_db = ExplodingSession()
        result = service.record_trade_observation(str(PLAYER), {
            "commodity": "ORE", "action": "sell", "source_station_id": STATION_A,
            "dest_station_id": STATION_B, "quantity": 1, "unit_price": 1,
            "total_credits": 1, "profit": 500,
        }, boom_db)
        assert result is None  # swallowed, not raised


# --------------------------------------------------------------------------- #
# Aggregate semantics -- pinned exactly to OPERATIONS/aria.md:204-222
# --------------------------------------------------------------------------- #

class TestTopRoutes:
    def test_ranks_by_avg_profit_desc_and_excludes_under_sample_routes(self, service, db):
        routes = service.get_top_routes(str(PLAYER), db)

        keys = [(r["commodity"], r["source_station_id"], r["dest_station_id"]) for r in routes]
        assert keys[0] == ("ORGANICS", str(STATION_A), str(STATION_B))
        assert routes[0]["avg_profit"] == pytest.approx(530.0)
        assert routes[0]["sample_count"] == 5

        # ROUTE 2 (ORE, count=2 < 3) never appears.
        assert not any(r["commodity"] == "ORE" and r["sample_count"] < 3 for r in routes)
        assert ("ORE", str(STATION_A), str(STATION_C)) not in keys

    def test_wash_trade_route_is_fully_excluded_not_diluted(self, service, db):
        routes = service.get_top_routes(str(PLAYER), db)
        assert not any(r["commodity"] == "FUEL" for r in routes)

    def test_dest_station_none_rows_excluded_from_candidate_pool(self, service, db):
        routes = service.get_top_routes(str(PLAYER), db)
        assert all(r["dest_station_id"] is not None for r in routes)
        # The profit=999 dest=None rows must not leak into ORGANICS A->B's avg.
        organics_ab = next(r for r in routes if r["commodity"] == "ORGANICS")
        assert organics_ab["avg_profit"] == pytest.approx(530.0)
        assert organics_ab["sample_count"] == 5

    def test_limit_is_respected(self, service, db):
        routes = service.get_top_routes(str(PLAYER), db, limit=1)
        assert len(routes) == 1
        assert routes[0]["commodity"] == "ORGANICS"

    def test_lower_avg_qualifying_route_still_included_and_ranked_below(self, service, db):
        routes = service.get_top_routes(str(PLAYER), db)
        commodities = [r["commodity"] for r in routes]
        assert "LUXURY" in commodities
        assert commodities.index("ORGANICS") < commodities.index("LUXURY")


class TestReliableCommodities:
    def test_high_success_rate_group_is_included(self, service, db):
        reliable = service.get_reliable_commodities(str(PLAYER), db)
        match = next((r for r in reliable if r["commodity"] == "ORGANICS"), None)
        assert match is not None
        assert match["source_station_id"] == str(STATION_A)
        assert match["success_rate"] == pytest.approx(1.0)
        assert match["sample_count"] == 5

    def test_below_threshold_success_rate_excluded(self, service, db):
        reliable = service.get_reliable_commodities(str(PLAYER), db)
        # COPPER at station_A: 3/5 = 0.6, below the 0.7 floor.
        assert not any(
            r["commodity"] == "COPPER" and r["source_station_id"] == str(STATION_A)
            for r in reliable
        )

    def test_buy_leg_rows_never_inflate_the_denominator(self, service, db):
        # If buy-leg rows (profit=None) leaked into the count, ORGANICS'
        # sample_count would be > 5 and/or success_rate would drop below 1.0.
        reliable = service.get_reliable_commodities(str(PLAYER), db)
        match = next(r for r in reliable if r["commodity"] == "ORGANICS")
        assert match["sample_count"] == 5
        assert match["success_rate"] == pytest.approx(1.0)


class TestWatchOutCommodities:
    def test_low_success_rate_commodity_is_flagged(self, service, db):
        watch_outs = service.get_watch_out_commodities(str(PLAYER), db)
        match = next((r for r in watch_outs if r["commodity"] == "SCRAP"), None)
        assert match is not None
        assert match["success_rate"] == pytest.approx(0.2)
        assert match["sample_count"] == 5
        # commodity-only grouping -- no source_station_id key on this surface.
        assert "source_station_id" not in match

    def test_mid_range_success_rate_is_not_a_watch_out(self, service, db):
        watch_outs = service.get_watch_out_commodities(str(PLAYER), db)
        # COPPER's 0.6 success rate is neither reliable nor a watch-out.
        assert not any(r["commodity"] == "COPPER" for r in watch_outs)

    def test_under_sample_count_never_flagged_despite_zero_success(self, service, db):
        watch_outs = service.get_watch_out_commodities(str(PLAYER), db)
        # RARE_GAS: 2 samples, 0% success -- excluded by count < 5.
        assert not any(r["commodity"] == "RARE_GAS" for r in watch_outs)


# --------------------------------------------------------------------------- #
# compute_recommendation_aggregates -- caching + invalidation
# --------------------------------------------------------------------------- #

class TestRecommendationCache:
    def test_first_call_computes_and_populates_cache(self, service, db):
        bundle = service.compute_recommendation_aggregates(str(PLAYER), db)

        assert bundle["top_routes"]
        assert bundle["reliable_commodities"]
        assert bundle["watch_out_commodities"]
        assert db.group_queries > 0

        cached_rows = [c for c in db.cache_rows if c.cache_key == "recommendation_aggregates"]
        assert len(cached_rows) == 1
        assert cached_rows[0].player_id == str(PLAYER)
        assert cached_rows[0].sector_id is None  # the nullability relaxation in play
        assert cached_rows[0].ghost_results == bundle

    def test_second_call_within_ttl_hits_cache_zero_new_group_queries(self, service, db):
        service.compute_recommendation_aggregates(str(PLAYER), db)
        after_first = db.group_queries

        second = service.compute_recommendation_aggregates(str(PLAYER), db)

        assert db.group_queries == after_first  # no new GROUP BY round trips
        assert second["top_routes"]

    def test_new_observation_invalidates_the_cache(self, service, db):
        service.compute_recommendation_aggregates(str(PLAYER), db)
        assert len(db.cache_rows) == 1

        service.record_trade_observation(str(PLAYER), {
            "commodity": "ORGANICS", "action": "sell", "source_station_id": STATION_A,
            "dest_station_id": STATION_B, "quantity": 1, "unit_price": 500,
            "total_credits": 500, "profit": 500,
        }, db)

        # The cache row was deleted by the insert's invalidation call.
        assert len(db.cache_rows) == 0

        after_invalidate_queries = db.group_queries
        service.compute_recommendation_aggregates(str(PLAYER), db)
        assert db.group_queries > after_invalidate_queries  # recomputed for real
        assert len(db.cache_rows) == 1  # repopulated

    def test_force_refresh_bypasses_a_warm_cache(self, service, db):
        service.compute_recommendation_aggregates(str(PLAYER), db)
        after_first = db.group_queries

        service.compute_recommendation_aggregates(str(PLAYER), db, force_refresh=True)

        assert db.group_queries > after_first


# --------------------------------------------------------------------------- #
# Migration pins -- AST-based, no import of alembic `op` machinery.
# --------------------------------------------------------------------------- #

MIGRATION_PATH = os.path.join(
    os.path.dirname(__file__), "..", "..",
    "alembic", "versions", "eb772a1ab433_add_aria_trading_observations.py",
)


def _migration_ast() -> ast.Module:
    with open(MIGRATION_PATH) as f:
        return ast.parse(f.read())


class TestMigrationPins:
    def test_revision_chains_after_current_head(self):
        tree = _migration_ast()
        pinned = {}
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Assign)
                and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name)
                and node.targets[0].id in ("revision", "down_revision")
                and isinstance(node.value, ast.Constant)
            ):
                pinned[node.targets[0].id] = node.value.value

        assert pinned["revision"] == "eb772a1ab433"
        assert pinned["down_revision"] == "b601fcdaca25"

    def test_create_table_name_and_columns(self):
        tree = _migration_ast()
        create_call = next(
            node for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "create_table"
        )
        assert isinstance(create_call.args[0], ast.Constant)
        assert create_call.args[0].value == "aria_trading_observations"

        column_names = {
            arg.args[0].value
            for arg in create_call.args[1:]
            if isinstance(arg, ast.Call)
            and isinstance(arg.func, ast.Attribute)
            and arg.func.attr == "Column"
            and arg.args
            and isinstance(arg.args[0], ast.Constant)
        }
        expected = {
            "id", "player_id", "trade_id", "commodity", "action",
            "source_station_id", "dest_station_id", "source_sector_id",
            "dest_sector_id", "quantity", "unit_price", "total_credits",
            "profit", "hours_held", "outcome_classification", "observed_at",
            "matched_market_intel_id", "recommendation_id",
        }
        assert expected <= column_names

    def test_aria_quantum_cache_sector_id_relaxed_to_nullable(self):
        tree = _migration_ast()
        found = False
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "alter_column"
                and len(node.args) >= 2
                and isinstance(node.args[0], ast.Constant)
                and node.args[0].value == "aria_quantum_cache"
                and isinstance(node.args[1], ast.Constant)
                and node.args[1].value == "sector_id"
            ):
                for kw in node.keywords:
                    if kw.arg == "nullable" and isinstance(kw.value, ast.Constant) and kw.value.value is True:
                        found = True  # OR-accumulate -- downgrade()'s nullable=False call must not clear this
        assert found, "expected an op.alter_column('aria_quantum_cache', 'sector_id', ..., nullable=True)"

    def test_migration_module_imports_cleanly(self):
        """Belt-and-suspenders: the file is valid, importable Python (not
        just parseable AST) -- catches syntax the AST checks above
        wouldn't (e.g. a typo'd sa.* attribute)."""
        import importlib.util

        spec = importlib.util.spec_from_file_location("_wo_aria_obs_log_migration", MIGRATION_PATH)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        assert module.revision == "eb772a1ab433"
        assert module.down_revision == "b601fcdaca25"
        assert callable(module.upgrade)
        assert callable(module.downgrade)

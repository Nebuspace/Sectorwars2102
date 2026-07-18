"""Regression pin for WO-ARIA-GA-CLEANUP: the six zero-caller genetic-
algorithm/"ghost trading" functions (ADR-0038: "no genetic algorithm, no
fitness scoring") plus the flagged-addition dead pair
(record_sector_visit / its broken _validate_player_ship gate, which
queried the nonexistent Ship.player_id) are REMOVED from
aria_personal_intelligence_service.py -- not merely unreachable, GONE.

Two halves:

1. TestGASymbolsRemoved -- structural: the service class exposes none of
   the eight removed symbols, and the now-orphaned ARIATradingPattern
   model is no longer imported into the service module's namespace.
2. TestPlanTradeCascadeByteProtection / TestBuildPersonalTradeGraph --
   behavioral: the WO's two explicitly PROTECTED functions
   (plan_trade_cascade, _build_personal_trade_graph) still import and run
   correctly against a stubbed AsyncSession, proving the deletion surgery
   (four non-contiguous line-range cuts through a 2700+ line file) did not
   graze either of them.

FakeCascadeSession is a minimal AsyncSession double: it interprets the
REAL SQLAlchemy select(Model).where(...) statements plan_trade_cascade's
callee chain issues (column_descriptions[0]["entity"] to route by model,
whereclause decomposition for and_()-joined eq conditions) against a
live in-memory row store built from REAL ARIAExplorationMap /
ARIAMarketIntelligence ORM instances -- never hand-rolled duck-types,
matching this codebase's established mock-only unit-test convention (see
test_aria_progression.py / test_aria_market_observation.py's FakeSession
docstrings).
"""
from __future__ import annotations

import uuid

import pytest

from src.models.aria_personal_intelligence import ARIAExplorationMap, ARIAMarketIntelligence
from src.models.warp_tunnel import WarpTunnel
from src.services import aria_personal_intelligence_service as aria_service_module
from src.services.aria_personal_intelligence_service import ARIAPersonalIntelligenceService


# ---------------------------------------------------------------------------
# 1. Structural -- the symbols are GONE, not just unreachable
# ---------------------------------------------------------------------------

REMOVED_GA_METHODS = [
    "generate_quantum_states",
    "get_ghost_trade_prediction",
    "evolve_trading_pattern",
    "get_evolved_patterns",
    "_create_trading_pattern",
    "_classify_pattern_type",
]

REMOVED_FLAGGED_ADDITION_METHODS = [
    "record_sector_visit",
    "_validate_player_ship",
]


class TestGASymbolsRemoved:
    @pytest.mark.parametrize("name", REMOVED_GA_METHODS)
    def test_ga_method_not_on_service_class(self, name):
        assert not hasattr(ARIAPersonalIntelligenceService, name), (
            f"{name} should have been removed by WO-ARIA-GA-CLEANUP"
        )

    @pytest.mark.parametrize("name", REMOVED_FLAGGED_ADDITION_METHODS)
    def test_flagged_addition_method_not_on_service_class(self, name):
        assert not hasattr(ARIAPersonalIntelligenceService, name), (
            f"{name} (flagged addition -- zero callers, broken Ship.player_id "
            f"gate) should have been removed by WO-ARIA-GA-CLEANUP"
        )

    def test_arita_trading_pattern_no_longer_imported_into_service_module(self):
        """The service module used to import ARIATradingPattern for the
        four now-deleted GA functions; nothing else in the file needs it."""
        assert not hasattr(aria_service_module, "ARIATradingPattern")

    def test_protected_functions_are_untouched(self):
        """Sanity companion to the behavioral tests below -- the two
        PROTECTED functions must still exist (the deletion cut around
        them, not through them)."""
        assert hasattr(ARIAPersonalIntelligenceService, "plan_trade_cascade")
        assert hasattr(ARIAPersonalIntelligenceService, "_build_personal_trade_graph")


# ---------------------------------------------------------------------------
# 2. Behavioral byte-protection -- plan_trade_cascade / _build_personal_
# trade_graph still import and run against a stubbed AsyncSession.
# ---------------------------------------------------------------------------

def _eval_clause(cond, row):
    if hasattr(cond, "clauses") and hasattr(cond, "operator"):
        return all(_eval_clause(c, row) for c in cond.clauses)  # and_()
    key = cond.left.key
    value = getattr(row, key, None)
    rhs = cond.right.value if hasattr(cond.right, "value") else cond.right
    opname = getattr(cond.operator, "__name__", None)
    if opname == "eq":
        return value == rhs
    raise NotImplementedError(f"fake session: unsupported operator {cond.operator!r}")


class _FakeResult:
    def __init__(self, rows):
        self._rows = list(rows)

    def scalars(self):
        return self

    def all(self):
        return list(self._rows)

    def scalar_one_or_none(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return list(self._rows)


class FakeCascadeSession:
    """Minimal AsyncSession double covering ARIAExplorationMap /
    ARIAMarketIntelligence (db.execute(select(Model).where(and_(eq, eq,
    ...))) -> .scalars().all() / .scalar_one_or_none()) -- this file's own
    query surface (graph-building + GA-symbol-removal pins).

    WO-ARIA-CASCADE-PATH note: _find_profitable_paths (called by
    plan_trade_cascade, exercised by TestPlanTradeCascadeByteProtection
    below) now ALSO issues a sector_warps Core-table select and a
    WarpTunnel ORM select to build real adjacency -- this fake answers
    both with an EMPTY result (no configured adjacency data), which is
    the correct, deliberate behavior for these tests: they exist to pin
    ARIAExplorationMap/ARIAMarketIntelligence handling, not pathfinding
    (see test_aria_cascade_path.py's FakeCascadePathSession for the
    pathfinding-focused fake with real adjacency fixtures)."""

    def __init__(self, explorations=(), intelligences=()):
        self.explorations = list(explorations)
        self.intelligences = list(intelligences)
        self.executed = 0

    async def execute(self, stmt):
        self.executed += 1
        entity = None
        descs = getattr(stmt, "column_descriptions", None)
        if descs:
            entity = descs[0].get("entity")

        if entity is ARIAExplorationMap:
            rows = self.explorations
        elif entity is ARIAMarketIntelligence:
            rows = self.intelligences
        elif entity is WarpTunnel:
            return _FakeResult([])  # no adjacency configured -- see class docstring
        elif entity is None:
            # No ORM entity -- the sector_warps Core-table select. Same
            # "no adjacency known" resolution as the WarpTunnel branch.
            table_name = None
            try:
                table_name = stmt.get_final_froms()[0].name
            except Exception:
                pass
            if table_name != "sector_warps":
                raise AssertionError(f"unexpected query {stmt!r}")
            return _FakeResult([])
        else:
            raise AssertionError(f"unexpected query entity {entity!r}")

        where = stmt.whereclause
        if where is None:
            conditions = []
        elif hasattr(where, "clauses"):
            conditions = list(where.clauses)
        else:
            conditions = [where]

        matched = [r for r in rows if all(_eval_clause(c, r) for c in conditions)]
        return _FakeResult(matched)


PLAYER = uuid.uuid4()
SECTOR_A = uuid.uuid4()
SECTOR_B = uuid.uuid4()
STATION_A = uuid.uuid4()


def _exploration(*, sector_id=SECTOR_A, visit_count=3, trade_opportunity_score=0.6) -> ARIAExplorationMap:
    return ARIAExplorationMap(
        id=uuid.uuid4(), player_id=str(PLAYER), sector_id=str(sector_id),
        visit_count=visit_count, trade_opportunity_score=trade_opportunity_score,
    )


def _intelligence(
    *, sector_id=SECTOR_A, station_id=STATION_A, commodity="ORE",
    average_price=42.5, price_volatility=0.12, prediction_confidence=0.8, data_points=10,
) -> ARIAMarketIntelligence:
    return ARIAMarketIntelligence(
        id=uuid.uuid4(), player_id=str(PLAYER), sector_id=str(sector_id), station_id=str(station_id),
        commodity=commodity, average_price=average_price, price_volatility=price_volatility,
        prediction_confidence=prediction_confidence, data_points=data_points,
    )


@pytest.fixture()
def service() -> ARIAPersonalIntelligenceService:
    return ARIAPersonalIntelligenceService()


class TestBuildPersonalTradeGraph:
    @pytest.mark.asyncio
    async def test_builds_ports_keyed_by_station_and_commodity(self, service):
        exploration = _exploration(sector_id=SECTOR_A, visit_count=5, trade_opportunity_score=0.75)
        intel = _intelligence(
            sector_id=SECTOR_A, station_id=STATION_A, commodity="ORE",
            average_price=100.0, price_volatility=0.2, prediction_confidence=0.9, data_points=7,
        )
        db = FakeCascadeSession(intelligences=[intel])

        graph = await service._build_personal_trade_graph(str(PLAYER), [exploration], db)

        assert str(SECTOR_A) in graph
        node = graph[str(SECTOR_A)]
        assert node["visit_count"] == 5
        assert node["trade_opportunity"] == 0.75
        port = node["ports"][str(STATION_A)]["ORE"]
        assert port["avg_price"] == 100.0
        assert port["volatility"] == 0.2
        assert port["confidence"] == 0.9
        assert port["observations"] == 7

    @pytest.mark.asyncio
    async def test_sector_with_no_intelligence_is_excluded(self, service):
        exploration = _exploration(sector_id=SECTOR_B)
        db = FakeCascadeSession(intelligences=[])  # no intel for SECTOR_B

        graph = await service._build_personal_trade_graph(str(PLAYER), [exploration], db)

        assert graph == {}

    @pytest.mark.asyncio
    async def test_empty_exploration_list_yields_empty_graph(self, service):
        db = FakeCascadeSession()
        graph = await service._build_personal_trade_graph(str(PLAYER), [], db)
        assert graph == {}


class TestPlanTradeCascadeByteProtection:
    @pytest.mark.asyncio
    async def test_no_explored_sectors_returns_none(self, service):
        db = FakeCascadeSession()
        result = await service.plan_trade_cascade(str(PLAYER), str(SECTOR_A), 1000.0, 5, db)
        assert result is None

    @pytest.mark.asyncio
    async def test_explored_but_no_intelligence_reports_insufficient_exploration(self, service):
        exploration = _exploration(sector_id=SECTOR_A)
        db = FakeCascadeSession(explorations=[exploration], intelligences=[])

        result = await service.plan_trade_cascade(str(PLAYER), str(SECTOR_A), 1000.0, 5, db)

        assert result["error"] == "insufficient_exploration"
        assert result["explored_sectors"] == 1

    @pytest.mark.asyncio
    async def test_full_chain_runs_with_real_data_through_the_graph_builder(self, service):
        """Explored sectors AND market intelligence both present -- the
        REAL _build_personal_trade_graph runs against real data (not the
        empty-graph short-circuit above). The overall result still lands
        on "no_profitable_routes" because this fixture (and FakeCascadeSession,
        by design -- see its own docstring) configures no sector_warps/
        WarpTunnel adjacency at all, so the sole explored sector has no
        reachable sell leg -- NOT because _find_profitable_paths is a
        placeholder anymore (WO-ARIA-CASCADE-PATH replaced it with real
        Dijkstra pathfinding; see test_aria_cascade_path.py for that
        coverage). This test's job stays scoped to proving the graph-
        building half of the chain runs correctly with live data."""
        exploration = _exploration(sector_id=SECTOR_A, visit_count=2, trade_opportunity_score=0.4)
        intel = _intelligence(sector_id=SECTOR_A, station_id=STATION_A, commodity="FUEL")
        db = FakeCascadeSession(explorations=[exploration], intelligences=[intel])

        result = await service.plan_trade_cascade(str(PLAYER), str(SECTOR_A), 1000.0, 5, db)

        assert result["error"] == "no_profitable_routes"
        assert db.executed >= 2  # at least the explored-sectors + graph-building queries fired

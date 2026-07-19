"""Unit tests for WO-ARIA-CASCADE-PATH: real Dijkstra pathfinding behind
``_find_profitable_paths``, replacing the always-returns-``[]`` placeholder
``test_aria_ga_removed.py``'s ``TestPlanTradeCascadeByteProtection`` pinned
before this WO landed (that file's own docstring on the "full chain" test
names the placeholder explicitly -- this file is its follow-up, not a
duplicate).

DB-free, async fake session extending the established pattern
(``test_aria_ga_removed.py``'s ``FakeCascadeSession``) with the two new
query shapes ``_build_explored_adjacency`` adds: the Core ``sector_warps``
table select, and the ``WarpTunnel`` ORM select. Per this codebase's "each
test file keeps its own self-contained harness" convention (see
``test_aria_trade_hooks.py``'s module docstring), the fake session is
redefined here rather than imported from the sibling GA-removal file.
"""
from __future__ import annotations

import inspect
import types
import uuid

import pytest

from src.models.aria_personal_intelligence import ARIAExplorationMap, ARIAMarketIntelligence
from src.models.warp_tunnel import WarpTunnel, WarpTunnelStatus, WarpTunnelType
from src.services.aria_personal_intelligence_service import ARIAPersonalIntelligenceService


# ---------------------------------------------------------------------------
# Fake AsyncSession
# ---------------------------------------------------------------------------

def _eval_clause(cond, row):
    if hasattr(cond, "clauses") and hasattr(cond, "operator"):
        return all(_eval_clause(c, row) for c in cond.clauses)
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


class FakeCascadePathSession:
    """Async db double for plan_trade_cascade's FULL callee chain,
    including the new pathfinding helpers.

    ``sector_warps`` rows are returned UNFILTERED by this fake: the real
    ``_build_explored_adjacency`` does its own Python-side explored-set
    re-check on every row regardless of what the SQL WHERE clause would
    have restricted, so returning everything here is a STRONGER test of
    that real filtering logic, not a shortcut -- if the production
    filtering ever regressed, these tests would catch it. ``WarpTunnel``
    rows ARE filtered by ``status == ACTIVE`` here: the real code relies
    entirely on the SQL WHERE clause for that (no Python-side re-check),
    so the fake has to apply it for that behavior to be testable at all.
    """

    def __init__(self, explorations=(), intelligences=(), warp_rows=(), tunnels=()):
        self.explorations = list(explorations)
        self.intelligences = list(intelligences)
        self.warp_rows = list(warp_rows)
        self.tunnels = list(tunnels)
        self.executed = 0

    async def execute(self, stmt):
        self.executed += 1
        entity = None
        descs = getattr(stmt, "column_descriptions", None)
        if descs:
            entity = descs[0].get("entity")

        if entity is ARIAExplorationMap:
            return _FakeResult(self._filter(self.explorations, stmt.whereclause))
        if entity is ARIAMarketIntelligence:
            return _FakeResult(self._filter(self.intelligences, stmt.whereclause))
        if entity is WarpTunnel:
            active_only = [t for t in self.tunnels if t.status == WarpTunnelStatus.ACTIVE]
            return _FakeResult(active_only)

        table_name = stmt.get_final_froms()[0].name
        if table_name == "sector_warps":
            return _FakeResult(self.warp_rows)

        raise AssertionError(f"unexpected query {stmt!r}")

    @staticmethod
    def _filter(rows, where):
        if where is None:
            return list(rows)
        conditions = list(where.clauses) if hasattr(where, "clauses") else [where]
        return [r for r in rows if all(_eval_clause(c, r) for c in conditions)]


# ---------------------------------------------------------------------------
# Fixture builders
# ---------------------------------------------------------------------------

PLAYER = uuid.uuid4()
SECTOR_A = uuid.uuid4()             # start
SECTOR_B = uuid.uuid4()             # intermediate hop -- no market intel of its own
SECTOR_C = uuid.uuid4()             # 2 hops from A via B -- the profitable sell leg
SECTOR_UNEXPLORED = uuid.uuid4()    # NOT an explored_sectors / trade_graph key
STATION_A = uuid.uuid4()
STATION_C = uuid.uuid4()
STATION_UNEXPLORED = uuid.uuid4()


def _warp_row(source, dest, turn_cost=1, bidirectional=True):
    """A sector_warps Core-table row -- no ORM class exists for this
    association table, so a plain attribute-bearing object is the only
    reasonable fixture shape (matches how a real Core .fetchall() row
    exposes column values as attributes)."""
    return types.SimpleNamespace(
        source_sector_id=source, destination_sector_id=dest,
        turn_cost=turn_cost, is_bidirectional=bidirectional,
    )


def _tunnel(origin, dest, *, status=WarpTunnelStatus.ACTIVE, turn_cost=1, bidirectional=False):
    return WarpTunnel(
        id=uuid.uuid4(), name=f"tunnel-{uuid.uuid4().hex[:6]}",
        origin_sector_id=origin, destination_sector_id=dest,
        type=WarpTunnelType.STANDARD, status=status,
        is_bidirectional=bidirectional, turn_cost=turn_cost,
    )


def _trade_graph_entry(*, ports, visit_count=1, trade_opportunity=0.5):
    return {"ports": ports, "connections": [], "visit_count": visit_count, "trade_opportunity": trade_opportunity}


def _port_intel(avg_price, confidence=0.8, observations=5):
    return {"avg_price": avg_price, "volatility": 0.1, "confidence": confidence, "observations": observations}


@pytest.fixture()
def service() -> ARIAPersonalIntelligenceService:
    return ARIAPersonalIntelligenceService()


# ---------------------------------------------------------------------------
# Placeholder-removal pin
# ---------------------------------------------------------------------------

class TestPlaceholderRemoved:
    def test_source_no_longer_mentions_placeholder(self, service):
        source = inspect.getsource(service._find_profitable_paths)
        assert "placeholder" not in source.lower()

    def test_returns_something_other_than_an_always_empty_list_shape(self, service):
        """Sanity companion: the function must actually branch on inputs
        now (a trivial regression guard against re-introducing a bare
        `return []` as the ENTIRE body)."""
        source = inspect.getsource(service._find_profitable_paths)
        assert "heapq" not in source  # pathfinding lives in the helper, not inlined here
        assert "_dijkstra_hop_distances" in source
        assert "_build_explored_adjacency" in source


# ---------------------------------------------------------------------------
# Real multi-hop pathfinding -- known profitable chain, correct hop math
# ---------------------------------------------------------------------------

class TestFindProfitablePathsMultiHop:
    @pytest.mark.asyncio
    async def test_finds_a_two_hop_profitable_route_with_correct_hop_count(self, service):
        # A -> B -> C (2 hops, no direct A-C edge) via sector_warps.
        warp_rows = [
            _warp_row(SECTOR_A, SECTOR_B),
            _warp_row(SECTOR_B, SECTOR_C),
        ]
        trade_graph = {
            SECTOR_A: _trade_graph_entry(ports={STATION_A: {"ORE": _port_intel(10.0, confidence=0.9, observations=8)}}),
            SECTOR_B: _trade_graph_entry(ports={}),  # explored, but no ports -- pure waypoint
            SECTOR_C: _trade_graph_entry(ports={STATION_C: {"ORE": _port_intel(50.0, confidence=0.7, observations=4)}}),
        }
        db = FakeCascadePathSession(warp_rows=warp_rows)

        paths = await service._find_profitable_paths(
            str(PLAYER), SECTOR_A, trade_graph, target_profit=10.0, max_jumps=5, db=db,
        )

        assert len(paths) == 1
        route = paths[0]
        assert route["jumps"] == 2
        assert route["total_profit"] == pytest.approx(40.0)
        assert route["profit_per_jump"] == pytest.approx(20.0)
        assert route["confidence"] == pytest.approx(0.7)  # min(0.9, 0.7)
        assert [step["action"] for step in route["path"]] == ["buy", "sell"]
        assert route["path"][0]["sector_id"] == SECTOR_A
        assert route["path"][0]["station_id"] == STATION_A
        assert route["path"][1]["sector_id"] == SECTOR_C
        assert route["path"][1]["station_id"] == STATION_C

    @pytest.mark.asyncio
    async def test_route_beyond_max_jumps_is_excluded(self, service):
        # Same topology as above, but max_jumps=1 -- C is 2 hops away, out of budget.
        warp_rows = [_warp_row(SECTOR_A, SECTOR_B), _warp_row(SECTOR_B, SECTOR_C)]
        trade_graph = {
            SECTOR_A: _trade_graph_entry(ports={STATION_A: {"ORE": _port_intel(10.0)}}),
            SECTOR_B: _trade_graph_entry(ports={}),
            SECTOR_C: _trade_graph_entry(ports={STATION_C: {"ORE": _port_intel(50.0)}}),
        }
        db = FakeCascadePathSession(warp_rows=warp_rows)

        paths = await service._find_profitable_paths(
            str(PLAYER), SECTOR_A, trade_graph, target_profit=10.0, max_jumps=1, db=db,
        )

        assert paths == []

    @pytest.mark.asyncio
    async def test_warp_tunnel_edges_also_contribute_to_reachability(self, service):
        # A connects to C via an ACTIVE one-way WarpTunnel (no sector_warps rows at all).
        tunnels = [_tunnel(SECTOR_A, SECTOR_C, bidirectional=False)]
        trade_graph = {
            SECTOR_A: _trade_graph_entry(ports={STATION_A: {"FUEL": _port_intel(5.0)}}),
            SECTOR_C: _trade_graph_entry(ports={STATION_C: {"FUEL": _port_intel(30.0)}}),
        }
        db = FakeCascadePathSession(tunnels=tunnels)

        paths = await service._find_profitable_paths(
            str(PLAYER), SECTOR_A, trade_graph, target_profit=10.0, max_jumps=3, db=db,
        )

        assert len(paths) == 1
        assert paths[0]["jumps"] == 1

    @pytest.mark.asyncio
    async def test_inactive_warp_tunnel_is_not_traversable(self, service):
        tunnels = [_tunnel(SECTOR_A, SECTOR_C, status=WarpTunnelStatus.COLLAPSED, bidirectional=False)]
        trade_graph = {
            SECTOR_A: _trade_graph_entry(ports={STATION_A: {"FUEL": _port_intel(5.0)}}),
            SECTOR_C: _trade_graph_entry(ports={STATION_C: {"FUEL": _port_intel(30.0)}}),
        }
        db = FakeCascadePathSession(tunnels=tunnels)

        paths = await service._find_profitable_paths(
            str(PLAYER), SECTOR_A, trade_graph, target_profit=10.0, max_jumps=3, db=db,
        )

        assert paths == []


# ---------------------------------------------------------------------------
# ADR-0075 falsifier: a MORE profitable route through unexplored space is
# NEVER returned, even when a real warp edge to it exists.
# ---------------------------------------------------------------------------

class TestExploredOnlyADR0075:
    @pytest.mark.asyncio
    async def test_unexplored_sector_with_a_better_price_is_excluded(self, service):
        # A direct, real warp edge from A to the unexplored sector exists --
        # if adjacency weren't explored-restricted, this would be the
        # single best route in the whole fixture (huge margin, 1 hop).
        warp_rows = [
            _warp_row(SECTOR_A, SECTOR_UNEXPLORED),
            _warp_row(SECTOR_A, SECTOR_C),
        ]
        # trade_graph deliberately has NO key for SECTOR_UNEXPLORED -- it was
        # never explored, so _build_personal_trade_graph would never have
        # produced an entry for it either.
        trade_graph = {
            SECTOR_A: _trade_graph_entry(ports={STATION_A: {"ORE": _port_intel(10.0)}}),
            SECTOR_C: _trade_graph_entry(ports={STATION_C: {"ORE": _port_intel(20.0)}}),
        }
        db = FakeCascadePathSession(warp_rows=warp_rows)

        paths = await service._find_profitable_paths(
            str(PLAYER), SECTOR_A, trade_graph, target_profit=5.0, max_jumps=5, db=db,
        )

        assert len(paths) == 1
        assert paths[0]["path"][1]["sector_id"] == SECTOR_C  # the EXPLORED, lower-margin route
        assert all(step["sector_id"] != SECTOR_UNEXPLORED for route in paths for step in route["path"])


# ---------------------------------------------------------------------------
# Sparse-data / honest degradation
# ---------------------------------------------------------------------------

class TestSparseDataDegradesHonestly:
    @pytest.mark.asyncio
    async def test_start_sector_with_no_ports_returns_empty(self, service):
        trade_graph = {SECTOR_A: _trade_graph_entry(ports={})}
        db = FakeCascadePathSession()
        paths = await service._find_profitable_paths(
            str(PLAYER), SECTOR_A, trade_graph, target_profit=10.0, max_jumps=5, db=db,
        )
        assert paths == []

    @pytest.mark.asyncio
    async def test_no_reachable_sector_returns_empty(self, service):
        trade_graph = {SECTOR_A: _trade_graph_entry(ports={STATION_A: {"ORE": _port_intel(10.0)}})}
        db = FakeCascadePathSession(warp_rows=[])  # A is isolated
        paths = await service._find_profitable_paths(
            str(PLAYER), SECTOR_A, trade_graph, target_profit=10.0, max_jumps=5, db=db,
        )
        assert paths == []

    @pytest.mark.asyncio
    async def test_profit_below_target_is_excluded(self, service):
        warp_rows = [_warp_row(SECTOR_A, SECTOR_C)]
        trade_graph = {
            SECTOR_A: _trade_graph_entry(ports={STATION_A: {"ORE": _port_intel(10.0)}}),
            SECTOR_C: _trade_graph_entry(ports={STATION_C: {"ORE": _port_intel(12.0)}}),  # only +2 profit
        }
        db = FakeCascadePathSession(warp_rows=warp_rows)
        paths = await service._find_profitable_paths(
            str(PLAYER), SECTOR_A, trade_graph, target_profit=100.0, max_jumps=5, db=db,
        )
        assert paths == []

    @pytest.mark.asyncio
    async def test_missing_avg_price_is_skipped_not_crashed(self, service):
        """ARIAMarketIntelligence.average_price is nullable (< 5
        observations, per aria-companion.md:25) -- a sparse intel entry
        with avg_price=None must be skipped, never raise."""
        warp_rows = [_warp_row(SECTOR_A, SECTOR_C)]
        trade_graph = {
            SECTOR_A: _trade_graph_entry(ports={STATION_A: {"ORE": _port_intel(None)}}),
            SECTOR_C: _trade_graph_entry(ports={STATION_C: {"ORE": _port_intel(50.0)}}),
        }
        db = FakeCascadePathSession(warp_rows=warp_rows)
        paths = await service._find_profitable_paths(
            str(PLAYER), SECTOR_A, trade_graph, target_profit=1.0, max_jumps=5, db=db,
        )
        assert paths == []


# ---------------------------------------------------------------------------
# End-to-end: plan_trade_cascade now returns a REAL cascade, not an
# unconditional no_profitable_routes error dict.
# ---------------------------------------------------------------------------

class TestPlanTradeCascadeEndToEnd:
    @pytest.mark.asyncio
    async def test_full_chain_returns_a_real_cascade_plan(self, service):
        explorations = [
            ARIAExplorationMap(id=uuid.uuid4(), player_id=str(PLAYER), sector_id=str(SECTOR_A), visit_count=3, trade_opportunity_score=0.5),
            ARIAExplorationMap(id=uuid.uuid4(), player_id=str(PLAYER), sector_id=str(SECTOR_C), visit_count=2, trade_opportunity_score=0.4),
        ]
        intelligences = [
            ARIAMarketIntelligence(
                id=uuid.uuid4(), player_id=str(PLAYER), sector_id=str(SECTOR_A), station_id=str(STATION_A),
                commodity="ORE", average_price=10.0, price_volatility=0.1, prediction_confidence=0.9, data_points=8,
            ),
            ARIAMarketIntelligence(
                id=uuid.uuid4(), player_id=str(PLAYER), sector_id=str(SECTOR_C), station_id=str(STATION_C),
                commodity="ORE", average_price=60.0, price_volatility=0.1, prediction_confidence=0.6, data_points=6,
            ),
        ]
        # sector_warps rows carry the SAME string-typed sector ids as the
        # exploration/intelligence fixtures above -- exercising the real
        # end-to-end chain, where ARIAExplorationMap.sector_id (and
        # therefore every trade_graph key) is whatever type the caller
        # passed in, matching this file's own str(uuid) convention
        # throughout (str(PLAYER), str(STATION_A), etc.).
        warp_rows = [_warp_row(str(SECTOR_A), str(SECTOR_C))]
        db = FakeCascadePathSession(explorations=explorations, intelligences=intelligences, warp_rows=warp_rows)

        result = await service.plan_trade_cascade(str(PLAYER), str(SECTOR_A), 10.0, 5, db)

        assert "error" not in result
        assert result["total_profit"] == pytest.approx(50.0)
        assert result["total_jumps"] == 1
        assert result["profit_per_jump"] == pytest.approx(50.0)
        assert len(result["steps"]) == 2
        assert result["steps"][0]["action"] == "buy"
        assert result["steps"][1]["action"] == "sell"
        assert result["steps"][1]["sector"] == str(SECTOR_C)

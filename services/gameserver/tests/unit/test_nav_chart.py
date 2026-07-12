"""WO-PUX-NAVCHART — GET /api/v1/nav/chart (NavService.get_chart).

DB-free: hand-built fakes, no real DB/app (mirrors test_available_moves_
query_count.py's condition-interpreting FakeSession — .filter()/.where()
clauses are real SQLAlchemy expressions applied against in-memory row
stores, not asserted by call-arg inspection).

Scope boundary (deliberate): ``NavService.get_known_sector_ids`` and
``NavService._build_safety_by_sid`` are PRE-EXISTING, already-shipped
machinery (unchanged by this WO — see course-plotting.md, which ``plot()``
already relies on) — monkeypatched per the codebase's "mock already-shipped
calculators, don't re-fake internals" convention (test_sweep_enrichment.py
et al.). These tests exercise ``get_chart``'s OWN new logic: given a known
set (however sourced), render sectors/visited/current correctly, assemble
warp+tunnel edges within known space, and surface frontier stubs (id-only,
no leakage) for edges leaving known space. "Teammate visibility honored" is
pinned at this same boundary: a known_ids fixture that includes a
teammate-only sector (own-visited excludes it) proves get_chart surfaces it
as known-but-unvisited, matching course-plotting.md's "visited reflects the
player's own exploration map only, never team-mate membership" invariant.

Acceptance-criteria map:
  1  TestKnownSectorsRender::test_three_visited_plus_current_render_with_frontier_stub
  2  TestTeammateVisibility::test_teammate_known_sector_is_known_but_not_visited
  3  TestFrontierNoLeakage::test_frontier_carries_bare_id_only_no_name_or_type
  4  TestEdgeAssembly::test_bidirectional_and_oneway_warp_edges
  5  TestEdgeAssembly::test_active_tunnel_edge_both_directions_inactive_excluded
  6  TestEmptyKnownSet::test_empty_known_set_returns_empty_shape
  7  TestRouteWiring::test_get_nav_chart_route_delegates_to_nav_service
     TestRouteWiring::test_get_nav_chart_route_passes_bounded_true_through

WO-NAV-REACH-BACKEND (``?bounded=true`` server-side depth bound, additive --
default ``bounded=False`` is the exact behaviour above, unchanged):
  ``FakeChartSession`` gained an optional ``ship_specs`` store (empty by
  default) and ``_player``/new ``_ship``/``_ship_spec`` helpers gained a
  ``current_ship`` fixture -- only the bounded path ever touches either.
  8  TestBoundedChart::test_bounded_true_excludes_sectors_beyond_scanner_range_as_frontier
  9  TestBoundedChart::test_bounded_false_default_is_byte_identical_to_the_unbounded_call
 10  TestBoundedChart::test_no_ship_bounded_true_is_unbounded
 11  TestBoundedChart::test_directed_bfs_excludes_a_sector_reachable_only_via_the_reverse_edge
 12  TestBoundedChart::test_depth_cap_clamps_to_the_ceiling_even_with_a_larger_effective_range
 13  TestBoundedChart::test_revert_probe_without_bounding_the_far_sector_would_leak_into_sectors
     (non-vacuous companion to #8 -- proves #8 exercises real bounding
     logic, not harness luck, by reverting ``_bound_known_ids`` to a no-op
     and showing the SAME scenario then fails #8's assertion)
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace
from typing import Any, List

import pytest
from sqlalchemy.sql import operators
from sqlalchemy.sql.elements import False_, True_

from src.models.sector import Sector, SectorType
from src.models.ship import ShipSpecification, ShipType
from src.models.warp_tunnel import WarpTunnel, WarpTunnelStatus
from src.services.nav_service import CHART_BOUNDED_DEPTH_CEILING, NavService

# --------------------------------------------------------------------------- #
# In-memory fake session — interprets the SUT's real filter()/where() clauses
# (mirrors test_available_moves_query_count.py's _FakeQuery/_condition_matches)
# --------------------------------------------------------------------------- #

def _condition_value(right: Any) -> Any:
    if isinstance(right, True_):
        return True
    if isinstance(right, False_):
        return False
    return right.value


def _condition_matches(row: Any, condition: Any) -> bool:
    column = condition.left.key
    actual = getattr(row, column)
    op = condition.operator
    value = _condition_value(condition.right)
    if op is operators.eq:
        return actual == value
    if op is operators.in_op:
        return actual in value
    raise AssertionError(f"unhandled operator {op!r} on column {column!r}")


def _extract_conditions(clause: Any) -> List[Any]:
    if clause is None:
        return []
    if hasattr(clause, "clauses"):
        return list(clause.clauses)
    return [clause]


class _FakeQuery:
    """*columns* (WO-NAV-REACH-BACKEND) is None for the pre-existing
    ``.query(Model)`` full-row form; when set (a list of attribute names),
    ``.all()``/``.first()`` project each matching row down to a bare
    SimpleNamespace exposing only those attributes -- needed for
    ``_build_known_graph``'s ``.query(Sector.sector_id, Sector.id)`` (the
    pre-existing method the ``bounded=True`` path now newly exercises)."""

    def __init__(self, store: List[Any], columns: List[str] = None) -> None:
        self._store = store
        self._conditions: tuple = ()
        self._columns = columns

    def filter(self, *conditions: Any) -> "_FakeQuery":
        self._conditions = self._conditions + conditions
        return self

    def _matching(self) -> List[Any]:
        rows = [r for r in self._store if all(_condition_matches(r, c) for c in self._conditions)]
        if self._columns is None:
            return rows
        return [SimpleNamespace(**{col: getattr(r, col) for col in self._columns}) for r in rows]

    def all(self) -> List[Any]:
        return list(self._matching())

    def first(self) -> Any:
        rows = self._matching()
        return rows[0] if rows else None


class _FakeExecuteResult:
    def __init__(self, rows: List[Any]) -> None:
        self._rows = rows

    def fetchall(self) -> List[Any]:
        return self._rows


class FakeChartSession:
    """db double for NavService.get_chart: .query(Sector) / .query(WarpTunnel)
    / .query(ShipSpecification) route to their in-memory stores (real
    filter() clauses applied); .execute() interprets the raw
    sector_warps.select().where(...) statement.

    ``ship_specs`` defaults to [] -- only the ``bounded=True`` path
    (WO-NAV-REACH-BACKEND's ``_bound_known_ids``) ever queries
    ShipSpecification; every pre-existing unbounded-path test is unaffected."""

    def __init__(
        self,
        *,
        sectors: List[Any],
        tunnels: List[Any],
        warp_edges: List[Any],
        ship_specs: List[Any] = None,
    ) -> None:
        self._stores = {Sector: sectors, WarpTunnel: tunnels, ShipSpecification: ship_specs or []}
        self._warp_edges = warp_edges

    def query(self, *entities: Any) -> _FakeQuery:
        # Two forms in play: `.query(Model)` (full-row, pre-existing) and
        # `.query(Model.col_a, Model.col_b, ...)` (column projection --
        # `_build_known_graph`'s `.query(Sector.sector_id, Sector.id)`,
        # WO-NAV-REACH-BACKEND). Dispatch via isinstance(..., type) rather
        # than dict membership: an InstrumentedAttribute overrides `__eq__`
        # to build a SQL BinaryExpression, so `attr in self._stores` is not
        # a safe truthiness check the way `Model in self._stores` is.
        if len(entities) == 1 and isinstance(entities[0], type):
            model = entities[0]
            assert model in self._stores, f"unexpected query for {model!r}"
            return _FakeQuery(self._stores[model])
        model = entities[0].class_
        assert model in self._stores, f"unexpected query for {entities!r}"
        columns = [e.key for e in entities]
        return _FakeQuery(self._stores[model], columns=columns)

    def execute(self, stmt: Any) -> _FakeExecuteResult:
        conditions = _extract_conditions(stmt.whereclause)
        matching = [r for r in self._warp_edges if all(_condition_matches(r, c) for c in conditions)]
        return _FakeExecuteResult(matching)


# --------------------------------------------------------------------------- #
# Fixture helpers
# --------------------------------------------------------------------------- #

def _sector(sector_id: int, *, name: str = None) -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid.uuid4(),
        sector_id=sector_id,
        name=name or f"Sector {sector_id}",
        type=SectorType.STANDARD,
        x_coord=sector_id, y_coord=sector_id * 2, z_coord=0,
    )


def _warp_edge(
    src: SimpleNamespace, dst: SimpleNamespace, *, bidirectional: bool = True, turn_cost: int = 1
) -> SimpleNamespace:
    return SimpleNamespace(
        source_sector_id=src.id, destination_sector_id=dst.id, is_bidirectional=bidirectional,
        # turn_cost (sector_warps schema default 1) -- unused by get_chart's
        # own edge/frontier assembly, but read by the PRE-EXISTING
        # _build_known_graph (`row.turn_cost if row.turn_cost else 1`),
        # which the bounded=True path (WO-NAV-REACH-BACKEND) now calls.
        turn_cost=turn_cost,
    )


def _tunnel(src: SimpleNamespace, dst: SimpleNamespace, *, bidirectional: bool = True,
            status: WarpTunnelStatus = WarpTunnelStatus.ACTIVE, turn_cost: int = 1) -> SimpleNamespace:
    return SimpleNamespace(
        origin_sector_id=src.id, destination_sector_id=dst.id,
        is_bidirectional=bidirectional, status=status,
        # turn_cost -- see _warp_edge's comment; WarpTunnel.turn_cost is the
        # equivalent column _build_known_graph's tunnel loop reads.
        turn_cost=turn_cost,
    )


def _player(
    *, current_sector_id: int, team_id: Any = None, current_ship: Any = None
) -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid.uuid4(),
        current_sector_id=current_sector_id,
        team_id=team_id,
        current_ship=current_ship,
    )


def _ship(*, type: ShipType = ShipType.SCOUT_SHIP, upgrades: dict = None) -> SimpleNamespace:
    """Bare ship double for the ``bounded=True`` Rail-A path -- only `.type`
    (ShipSpecification lookup key) and `.upgrades` (ShipUpgradeService.
    get_sensor_level's JSONB source) are ever read on it."""
    return SimpleNamespace(type=type, upgrades=upgrades or {})


def _ship_spec(*, type: ShipType = ShipType.SCOUT_SHIP, scanner_range: int) -> SimpleNamespace:
    return SimpleNamespace(id=uuid.uuid4(), type=type, scanner_range=scanner_range)


def _patch_known(monkeypatch: pytest.MonkeyPatch, known_ids: set) -> None:
    monkeypatch.setattr(NavService, "get_known_sector_ids", lambda self, player: set(known_ids))


def _patch_own_visited(monkeypatch: pytest.MonkeyPatch, own_visited_ids: set) -> None:
    monkeypatch.setattr(
        NavService, "_build_safety_by_sid", lambda self, player: {sid: 0.5 for sid in own_visited_ids}
    )


# --------------------------------------------------------------------------- #
# Accept #1: 3 visited + current render, with a frontier stub
# --------------------------------------------------------------------------- #

@pytest.mark.unit
class TestKnownSectorsRender:
    def test_three_visited_plus_current_render_with_frontier_stub(self, monkeypatch: pytest.MonkeyPatch) -> None:
        current = _sector(1, name="Current")
        v1, v2, v3 = _sector(2, name="V1"), _sector(3, name="V2"), _sector(4, name="V3")
        unknown = _sector(99, name="Unexplored")  # adjacent, NOT in the known set
        sectors = [current, v1, v2, v3, unknown]

        known_ids = {current.sector_id, v1.sector_id, v2.sector_id, v3.sector_id}
        _patch_known(monkeypatch, known_ids)
        _patch_own_visited(monkeypatch, {v1.sector_id, v2.sector_id, v3.sector_id})  # current NOT personally visited

        warp_edges = [
            _warp_edge(current, v1),
            _warp_edge(v2, unknown, bidirectional=False),  # leaves known space -> frontier
        ]
        db = FakeChartSession(sectors=sectors, tunnels=[], warp_edges=warp_edges)
        player = _player(current_sector_id=current.sector_id)

        chart = NavService(db).get_chart(player)

        got_ids = {s["sector_id"] for s in chart["sectors"]}
        assert got_ids == known_ids  # exactly the known set -- unexplored sector never leaks into "sectors"

        by_id = {s["sector_id"]: s for s in chart["sectors"]}
        assert by_id[current.sector_id]["current"] is True
        assert by_id[current.sector_id]["visited"] is False  # current-but-unvisited per canon
        for sid in (v1.sector_id, v2.sector_id, v3.sector_id):
            assert by_id[sid]["visited"] is True
            assert by_id[sid]["current"] is False

        # v2 is the only known sector whose warp edge leaves known space into
        # `unknown`, so it is the (only, and therefore deterministic) `from`.
        assert chart["frontier"] == [{"id": unknown.sector_id, "from": v2.sector_id}]
        assert unknown.sector_id not in got_ids  # frontier sector never appears in "sectors"


# --------------------------------------------------------------------------- #
# Accept #2: teammate-known-but-not-personally-visited
# --------------------------------------------------------------------------- #

@pytest.mark.unit
class TestTeammateVisibility:
    def test_teammate_known_sector_is_known_but_not_visited(self, monkeypatch: pytest.MonkeyPatch) -> None:
        current = _sector(1)
        own = _sector(2, name="OwnVisit")
        teammate_only = _sector(3, name="TeammateVisit")  # known via corp-share, never personally flown
        sectors = [current, own, teammate_only]

        team_id = uuid.uuid4()
        known_ids = {current.sector_id, own.sector_id, teammate_only.sector_id}
        _patch_known(monkeypatch, known_ids)
        _patch_own_visited(monkeypatch, {own.sector_id})  # teammate_only deliberately excluded

        db = FakeChartSession(sectors=sectors, tunnels=[], warp_edges=[])
        player = _player(current_sector_id=current.sector_id, team_id=team_id)

        chart = NavService(db).get_chart(player)

        by_id = {s["sector_id"]: s for s in chart["sectors"]}
        assert teammate_only.sector_id in by_id  # corp-shared knowledge widens the known/plottable set
        assert by_id[teammate_only.sector_id]["visited"] is False  # but never marks it personally visited
        assert by_id[own.sector_id]["visited"] is True


# --------------------------------------------------------------------------- #
# Accept #3: frontier stubs carry id only -- no name/type/contents leakage
# --------------------------------------------------------------------------- #

@pytest.mark.unit
class TestFrontierNoLeakage:
    def test_frontier_carries_bare_id_only_no_name_or_type(self, monkeypatch: pytest.MonkeyPatch) -> None:
        current = _sector(1)
        secret = _sector(1234, name="TOP SECRET NEBULA")
        sectors = [current, secret]

        known_ids = {current.sector_id}
        _patch_known(monkeypatch, known_ids)
        _patch_own_visited(monkeypatch, set())

        warp_edges = [_warp_edge(current, secret)]
        db = FakeChartSession(sectors=sectors, tunnels=[], warp_edges=warp_edges)
        player = _player(current_sector_id=current.sector_id)

        chart = NavService(db).get_chart(player)

        assert chart["frontier"] == [{"id": secret.sector_id, "from": current.sector_id}]
        # `{id, from}` only -- no name/type/coordinate keys leak onto the stub.
        assert set(chart["frontier"][0].keys()) == {"id", "from"}
        assert secret.sector_id not in {s["sector_id"] for s in chart["sectors"]}
        assert not any(e["to"] == secret.sector_id or e["from"] == secret.sector_id for e in chart["edges"])
        # Defensive: the secret name never appears anywhere in the response.
        assert "TOP SECRET NEBULA" not in repr(chart)


# --------------------------------------------------------------------------- #
# WO-NAV-CHART-FRONTIER-EDGES: frontier `from` linkage, incl. deterministic
# tie-break when a frontier sector is reachable from >1 known sector.
# --------------------------------------------------------------------------- #

@pytest.mark.unit
class TestFrontierFromLinkage:
    def test_frontier_from_is_a_real_known_neighbor(self, monkeypatch: pytest.MonkeyPatch) -> None:
        current, v1 = _sector(1), _sector(2)
        unknown = _sector(99)
        sectors = [current, v1, unknown]

        known_ids = {current.sector_id, v1.sector_id}
        _patch_known(monkeypatch, known_ids)
        _patch_own_visited(monkeypatch, set())

        warp_edges = [_warp_edge(v1, unknown, bidirectional=False)]
        db = FakeChartSession(sectors=sectors, tunnels=[], warp_edges=warp_edges)
        player = _player(current_sector_id=current.sector_id)

        chart = NavService(db).get_chart(player)

        assert len(chart["frontier"]) == 1
        entry = chart["frontier"][0]
        assert entry["id"] == unknown.sector_id
        assert entry["from"] in known_ids  # `from` names a REAL known-sector neighbor of `id`
        assert entry["from"] == v1.sector_id  # the sector whose warp actually surfaced it

    def test_frontier_from_multiple_known_sources_picks_smallest_deterministically(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Two known sectors (ids 5 and 2) both warp out to the same unknown
        # sector -- `from` must deterministically be the SMALLEST known
        # sector_id among them (2), not source-row iteration order.
        hi, lo = _sector(5), _sector(2)
        unknown = _sector(99)
        sectors = [hi, lo, unknown]

        known_ids = {hi.sector_id, lo.sector_id}
        _patch_known(monkeypatch, known_ids)
        _patch_own_visited(monkeypatch, set())

        # Deliberately ordered so the LARGER sector_id's edge is seen first --
        # proves the tie-break compares values, not "first writer wins".
        warp_edges = [
            _warp_edge(hi, unknown, bidirectional=False),
            _warp_edge(lo, unknown, bidirectional=False),
        ]
        db = FakeChartSession(sectors=sectors, tunnels=[], warp_edges=warp_edges)
        player = _player(current_sector_id=hi.sector_id)

        chart = NavService(db).get_chart(player)

        assert chart["frontier"] == [{"id": unknown.sector_id, "from": lo.sector_id}]


# --------------------------------------------------------------------------- #
# Accept #4/#5: edge assembly -- bidirectional/one-way warps, tunnel status
# --------------------------------------------------------------------------- #

@pytest.mark.unit
class TestEdgeAssembly:
    def test_bidirectional_and_oneway_warp_edges(self, monkeypatch: pytest.MonkeyPatch) -> None:
        a, b, c = _sector(1), _sector(2), _sector(3)
        sectors = [a, b, c]
        known_ids = {a.sector_id, b.sector_id, c.sector_id}
        _patch_known(monkeypatch, known_ids)
        _patch_own_visited(monkeypatch, set())

        warp_edges = [
            _warp_edge(a, b, bidirectional=True),
            _warp_edge(b, c, bidirectional=False),
        ]
        db = FakeChartSession(sectors=sectors, tunnels=[], warp_edges=warp_edges)
        player = _player(current_sector_id=a.sector_id)

        chart = NavService(db).get_chart(player)
        edge_set = {(e["from"], e["to"], e["kind"]) for e in chart["edges"]}

        assert (a.sector_id, b.sector_id, "warp") in edge_set
        assert (b.sector_id, a.sector_id, "warp") in edge_set  # bidirectional -> both directions
        assert (b.sector_id, c.sector_id, "warp") in edge_set
        assert (c.sector_id, b.sector_id, "warp") not in edge_set  # one-way -> reverse absent
        assert len(chart["edges"]) == 3

    def test_active_tunnel_edge_both_directions_inactive_excluded(self, monkeypatch: pytest.MonkeyPatch) -> None:
        a, b, d = _sector(1), _sector(2), _sector(4)
        sectors = [a, b, d]
        known_ids = {a.sector_id, b.sector_id, d.sector_id}
        _patch_known(monkeypatch, known_ids)
        _patch_own_visited(monkeypatch, set())

        tunnels = [
            _tunnel(a, b, bidirectional=True, status=WarpTunnelStatus.ACTIVE),
            _tunnel(a, d, bidirectional=True, status=WarpTunnelStatus.COLLAPSED),
        ]
        db = FakeChartSession(sectors=sectors, tunnels=tunnels, warp_edges=[])
        player = _player(current_sector_id=a.sector_id)

        chart = NavService(db).get_chart(player)
        edge_set = {(e["from"], e["to"], e["kind"]) for e in chart["edges"]}

        assert (a.sector_id, b.sector_id, "tunnel") in edge_set
        assert (b.sector_id, a.sector_id, "tunnel") in edge_set
        assert not any(e["kind"] == "tunnel" and d.sector_id in (e["from"], e["to"]) for e in chart["edges"])
        assert len(chart["edges"]) == 2  # the COLLAPSED (non-ACTIVE) tunnel contributes nothing


# --------------------------------------------------------------------------- #
# Accept #6: empty known set
# --------------------------------------------------------------------------- #

@pytest.mark.unit
class TestEmptyKnownSet:
    def test_empty_known_set_returns_empty_shape(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _patch_known(monkeypatch, set())
        db = FakeChartSession(sectors=[], tunnels=[], warp_edges=[])
        player = _player(current_sector_id=1)

        chart = NavService(db).get_chart(player)
        assert chart == {"sectors": [], "edges": [], "frontier": []}


# --------------------------------------------------------------------------- #
# WO-NAV-REACH-BACKEND: ?bounded=true server-side depth bound
# --------------------------------------------------------------------------- #

@pytest.mark.unit
class TestBoundedChart:
    def test_bounded_true_excludes_sectors_beyond_scanner_range_as_frontier(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A 4-hop chain (1-2-3-4) with a ship whose effective scanner range
        is 2: BFS from sector 1 reaches {1, 2, 3} (depths 0/1/2); sector 4
        (depth 3) is excluded from `sectors` and demoted to a frontier stub
        `from` the known sector whose edge crosses the bound (3)."""
        s1, s2, s3, s4 = _sector(1), _sector(2), _sector(3), _sector(4)
        sectors = [s1, s2, s3, s4]
        known_ids = {s1.sector_id, s2.sector_id, s3.sector_id, s4.sector_id}
        _patch_known(monkeypatch, known_ids)
        _patch_own_visited(monkeypatch, set())

        warp_edges = [
            _warp_edge(s1, s2, bidirectional=True),
            _warp_edge(s2, s3, bidirectional=True),
            _warp_edge(s3, s4, bidirectional=True),
        ]
        spec = _ship_spec(scanner_range=2)
        db = FakeChartSession(sectors=sectors, tunnels=[], warp_edges=warp_edges, ship_specs=[spec])
        player = _player(current_sector_id=s1.sector_id, current_ship=_ship())

        chart = NavService(db).get_chart(player, bounded=True)

        got_ids = {s["sector_id"] for s in chart["sectors"]}
        assert got_ids == {s1.sector_id, s2.sector_id, s3.sector_id}  # s4 excluded -- beyond the 2-hop bound
        assert chart["frontier"] == [{"id": s4.sector_id, "from": s3.sector_id}]
        assert not any(s4.sector_id in (e["from"], e["to"]) for e in chart["edges"])

    def test_bounded_false_default_is_byte_identical_to_the_unbounded_call(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Adding the `bounded` kwarg must not perturb the pre-existing
        unbounded contract: an explicit `bounded=False` call and the bare
        (no-kwarg) call return the exact same shape on the exact same
        known-graph fixture used by Accept #1."""
        current = _sector(1, name="Current")
        v1, v2, v3 = _sector(2, name="V1"), _sector(3, name="V2"), _sector(4, name="V3")
        unknown = _sector(99, name="Unexplored")
        sectors = [current, v1, v2, v3, unknown]

        known_ids = {current.sector_id, v1.sector_id, v2.sector_id, v3.sector_id}
        _patch_known(monkeypatch, known_ids)
        _patch_own_visited(monkeypatch, {v1.sector_id, v2.sector_id, v3.sector_id})

        warp_edges = [
            _warp_edge(current, v1),
            _warp_edge(v2, unknown, bidirectional=False),
        ]
        db = FakeChartSession(sectors=sectors, tunnels=[], warp_edges=warp_edges)
        # No `current_ship` -- proves the byte-identical claim holds even
        # for a player who COULD take the bounded branch's ship-present arm.
        player = _player(current_sector_id=current.sector_id)

        chart_default = NavService(db).get_chart(player)
        chart_explicit_false = NavService(db).get_chart(player, bounded=False)

        assert chart_default == chart_explicit_false

    def test_no_ship_bounded_true_is_unbounded(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """`bounded=True` with `player.current_ship is None` has NO effect --
        the no-ship-is-unbounded fallback preserves the full known graph
        (and never touches the ShipSpecification store)."""
        s1, s2, s3 = _sector(1), _sector(2), _sector(3)
        sectors = [s1, s2, s3]
        known_ids = {s1.sector_id, s2.sector_id, s3.sector_id}
        _patch_known(monkeypatch, known_ids)
        _patch_own_visited(monkeypatch, set())

        warp_edges = [_warp_edge(s1, s2), _warp_edge(s2, s3)]
        # ship_specs deliberately empty -- if the bounded branch were
        # (incorrectly) entered for a no-ship player, the ShipSpecification
        # query would simply find nothing rather than erroring, but the BFS
        # would still incorrectly shrink known_ids; the id-set assertion
        # below is what actually proves the fallback fired.
        db = FakeChartSession(sectors=sectors, tunnels=[], warp_edges=warp_edges)
        player = _player(current_sector_id=s1.sector_id, current_ship=None)

        chart = NavService(db).get_chart(player, bounded=True)

        assert {s["sector_id"] for s in chart["sectors"]} == known_ids

    def test_directed_bfs_excludes_a_sector_reachable_only_via_the_reverse_edge(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A one-way warp A->B: standing AT B with a generous scanner range,
        A is NOT reachable (no A<-B edge exists in the directed adjacency)
        even though A is in the known set. Proves the bound walks the same
        DIRECTED graph plot()'s Dijkstra does (via _build_known_graph),
        not a symmetric "is it nearby" heuristic."""
        a, b = _sector(1), _sector(2)
        sectors = [a, b]
        known_ids = {a.sector_id, b.sector_id}
        _patch_known(monkeypatch, known_ids)
        _patch_own_visited(monkeypatch, set())

        warp_edges = [_warp_edge(a, b, bidirectional=False)]  # A -> B only
        spec = _ship_spec(scanner_range=10)  # generous -- direction is what excludes A, not depth
        db = FakeChartSession(sectors=sectors, tunnels=[], warp_edges=warp_edges, ship_specs=[spec])
        player = _player(current_sector_id=b.sector_id, current_ship=_ship())  # standing at B

        chart = NavService(db).get_chart(player, bounded=True)

        got_ids = {s["sector_id"] for s in chart["sectors"]}
        assert got_ids == {b.sector_id}  # A excluded -- only reachable via the reverse of a one-way edge

    def test_depth_cap_clamps_to_the_ceiling_even_with_a_larger_effective_range(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A scanner_range far exceeding CHART_BOUNDED_DEPTH_CEILING (12)
        still only reaches `ceiling` hops -- the ceiling, not the ship
        stat, is the effective cap once the ship stat exceeds it."""
        chain = [_sector(i) for i in range(1, CHART_BOUNDED_DEPTH_CEILING + 3)]  # 1..14
        sectors = list(chain)
        known_ids = {s.sector_id for s in chain}
        _patch_known(monkeypatch, known_ids)
        _patch_own_visited(monkeypatch, set())

        warp_edges = [
            _warp_edge(chain[i], chain[i + 1], bidirectional=True) for i in range(len(chain) - 1)
        ]
        spec = _ship_spec(scanner_range=999)
        db = FakeChartSession(sectors=sectors, tunnels=[], warp_edges=warp_edges, ship_specs=[spec])
        player = _player(current_sector_id=chain[0].sector_id, current_ship=_ship())

        chart = NavService(db).get_chart(player, bounded=True)

        got_ids = {s["sector_id"] for s in chart["sectors"]}
        expected_ids = {chain[i].sector_id for i in range(CHART_BOUNDED_DEPTH_CEILING + 1)}  # depths 0..12
        assert got_ids == expected_ids
        assert chain[CHART_BOUNDED_DEPTH_CEILING + 1].sector_id not in got_ids  # depth 13 -- beyond the ceiling

    def test_revert_probe_without_bounding_the_far_sector_would_leak_into_sectors(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Non-vacuous companion to the depth-exclusion test above: with
        `_bound_known_ids` reverted to a no-op (the pre-fix shape --
        `bounded=True` accepted but never actually narrows `known_ids`),
        the SAME 4-hop-chain scenario leaks sector 4 into `sectors` and
        omits the frontier stub -- proving the exclusion test is
        exercising real bounding logic, not harness luck."""
        s1, s2, s3, s4 = _sector(1), _sector(2), _sector(3), _sector(4)
        sectors = [s1, s2, s3, s4]
        known_ids = {s1.sector_id, s2.sector_id, s3.sector_id, s4.sector_id}
        _patch_known(monkeypatch, known_ids)
        _patch_own_visited(monkeypatch, set())

        warp_edges = [
            _warp_edge(s1, s2, bidirectional=True),
            _warp_edge(s2, s3, bidirectional=True),
            _warp_edge(s3, s4, bidirectional=True),
        ]
        spec = _ship_spec(scanner_range=2)
        db = FakeChartSession(sectors=sectors, tunnels=[], warp_edges=warp_edges, ship_specs=[spec])
        player = _player(current_sector_id=s1.sector_id, current_ship=_ship())

        # Revert: _bound_known_ids becomes a no-op, simulating the world
        # before this WO's BFS narrowing existed.
        monkeypatch.setattr(NavService, "_bound_known_ids", lambda self, player, known_ids: known_ids)

        chart = NavService(db).get_chart(player, bounded=True)

        got_ids = {s["sector_id"] for s in chart["sectors"]}
        assert got_ids == known_ids  # WITHOUT bounding, s4 leaks in -- the opposite of the fixed test's assertion
        assert chart["frontier"] == []  # and no frontier stub is produced for it


# --------------------------------------------------------------------------- #
# Accept #7: route wiring -- GET /nav/chart delegates to NavService.get_chart
# --------------------------------------------------------------------------- #

@pytest.mark.unit
class TestRouteWiring:
    @pytest.mark.asyncio
    async def test_get_nav_chart_route_delegates_to_nav_service(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from src.api.routes import nav as nav_routes

        sentinel_chart = {"sectors": [], "edges": [], "frontier": []}
        captured: dict = {}

        class _StubNavService:
            def __init__(self, db: Any) -> None:
                captured["db"] = db

            def get_chart(self, player: Any, bounded: bool = False) -> dict:
                captured["player"] = player
                captured["bounded"] = bounded
                return sentinel_chart

        monkeypatch.setattr(nav_routes, "NavService", _StubNavService)

        fake_db = object()
        fake_player = object()
        # bounded explicitly supplied both times -- a direct (non-ASGI)
        # function call resolves an unsupplied Query(...) parameter to its
        # FieldInfo sentinel, not the bool default, so passthrough is what
        # this test proves, not FastAPI's own query-parsing (covered
        # separately by FastAPI itself, not this unit's concern).
        result = await nav_routes.get_nav_chart(bounded=False, db=fake_db, current_player=fake_player)

        assert result is sentinel_chart
        assert captured["db"] is fake_db
        assert captured["player"] is fake_player
        assert captured["bounded"] is False

    @pytest.mark.asyncio
    async def test_get_nav_chart_route_passes_bounded_true_through(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from src.api.routes import nav as nav_routes

        sentinel_chart = {"sectors": [], "edges": [], "frontier": []}
        captured: dict = {}

        class _StubNavService:
            def __init__(self, db: Any) -> None:
                captured["db"] = db

            def get_chart(self, player: Any, bounded: bool = False) -> dict:
                captured["bounded"] = bounded
                return sentinel_chart

        monkeypatch.setattr(nav_routes, "NavService", _StubNavService)

        result = await nav_routes.get_nav_chart(bounded=True, db=object(), current_player=object())

        assert result is sentinel_chart
        assert captured["bounded"] is True

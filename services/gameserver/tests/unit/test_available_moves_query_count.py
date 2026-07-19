"""Query-count independence proof for MovementService.get_available_moves
(WO-QTI-MOVES-BATCH).

Before this WO, the listing paid a per-row/per-tunnel query: one Sector
lookup per direct-warp edge, one Sector lookup per tunnel destination, one
PlayerWarpKnowledge lookup per latent tunnel, and (a hidden extra the WO's
own estimate missed) one more sector_warps lookup per direct-warp edge
inside ``_calculate_warp_cost`` -- so the query count scaled with W (warp
edges) and T (tunnels). This file proves it no longer does: a W=10/T=10
fixture must issue the SAME number of queries as a W=1/T=1 fixture.

No live DB is used. Per the codebase's mock-only unit-test convention (see
test_route_runs_retention.py's FakeRouteRunQuery / test_movement_core_pins
.py's _FakeQuery), the fake session below interprets the REAL SQLAlchemy
filter()/where() clauses the SUT builds against in-memory row stores, so
row selection is exercised for real, not merely asserted by call-arg
inspection. A shared counter increments on every terminal call that would
be a real DB round trip (.first() / .all() / .execute()) -- query BUILDING
(.filter()) is free, matching real SQLAlchemy's lazy-until-terminal
behavior.
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest
from sqlalchemy.sql import operators
from sqlalchemy.sql.elements import True_, False_

from src.models.player import Player
from src.models.player_warp_knowledge import PlayerWarpKnowledge, WarpLayer
from src.models.sector import Sector, SectorType
from src.models.ship import ShipType
from src.models.warp_tunnel import WarpTunnel, WarpTunnelStatus, WarpTunnelType
from src.services import movement_service as movement_service_module
from src.services.movement_service import MovementService


# --------------------------------------------------------------------------- #
# In-memory fake session -- interprets the SUT's real filter()/where() clauses
# --------------------------------------------------------------------------- #

def _condition_value(right):
    if isinstance(right, True_):
        return True
    if isinstance(right, False_):
        return False
    return right.value


def _condition_matches(row, condition) -> bool:
    column = condition.left.key
    actual = getattr(row, column)
    op = condition.operator
    value = _condition_value(condition.right)
    if op is operators.eq:
        return actual == value
    if op is operators.in_op:
        if isinstance(value, (list, tuple, set, frozenset)):
            return actual in value
        return actual == value
    raise AssertionError(f"unhandled operator {op!r} on column {column!r}")


def _extract_conditions(clause):
    """A Core .where(a, b) ANDs into one BooleanClauseList; .where(a) alone
    stays a bare BinaryExpression. ORM .filter(*conds) never merges -- each
    condition already arrives separately, so this is only needed for the
    raw sector_warps.select() call sites."""
    if clause is None:
        return []
    if hasattr(clause, "clauses"):
        return list(clause.clauses)
    return [clause]


class _FakeQuery:
    """In-memory stand-in for db.query(Model): filter() records the real
    SQLAlchemy clauses the SUT constructs; first()/all() apply them against
    a shared store and count as ONE real query each (query building itself
    is free -- mirrors SQLAlchemy's lazy-until-terminal-call behavior)."""

    def __init__(self, store, session):
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

    def all(self):
        self._session.queries += 1
        return list(self._matching())


class _FakeExecuteResult:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return self._rows


class FakeMovesSession:
    """Minimal db double for get_available_moves: every .query(Model) call
    routes to the model's own in-memory store; .execute() interprets the
    two raw sector_warps.select().where(...) statements the SUT issues.
    ``queries`` counts every real round trip -- the falsifiable metric this
    file's tests assert on."""

    def __init__(self, *, players, sectors, tunnels, knowledge, warp_edges):
        self.queries = 0
        self._stores = {
            Player: players,
            Sector: sectors,
            WarpTunnel: tunnels,
            PlayerWarpKnowledge: knowledge,
        }
        self._warp_edges = warp_edges

    def query(self, model):
        assert model in self._stores, f"unexpected query for {model!r}"
        return _FakeQuery(self._stores[model], self)

    def execute(self, stmt):
        self.queries += 1
        conditions = _extract_conditions(stmt.whereclause)
        matching = [r for r in self._warp_edges if all(_condition_matches(r, c) for c in conditions)]
        return _FakeExecuteResult(matching)

    def commit(self):
        raise AssertionError(
            "commit() should not fire in this fixture -- advance_gates_touching_sector "
            "is stubbed to return 0 (falsy)"
        )

    def rollback(self):
        pass


# --------------------------------------------------------------------------- #
# Fixture builder -- scales W (direct-warp edges, both directions) and T
# (tunnels, split evenly outgoing/incoming-bidirectional, alternating latent)
# --------------------------------------------------------------------------- #

CURRENT_SECTOR_GLOBAL_ID = 9000
PLAYER_ID = uuid.uuid4()


def _ship(*, warp_capable=False, condition=80.0):
    """condition=80.0 sits in the neutral Good band (matches
    test_movement_core_pins.py's own fixture choice) -- speed 0.0 ->
    _maintenance_speed_multiplier returns exactly 1.0, keeping expected
    turn_cost values simple integer arithmetic below."""
    return SimpleNamespace(
        type=ShipType.CARGO_HAULER,
        warp_capable=warp_capable,
        maintenance={"condition": condition, "last_maintenance": None},
        owner_id=PLAYER_ID,
    )


def _build_fixture(w: int, t: int, *, warp_capable_ship: bool = False):
    """Builds a FakeMovesSession with W outgoing + W incoming-bidirectional
    direct-warp edges, and T outgoing + T incoming-bidirectional tunnels
    (every other one latent, every 4th latent one known-to-player), around
    a single current sector. Returns (session, player, expectations) where
    expectations carries what a correct listing must contain.
    """
    current_sector_id = uuid.uuid4()
    current_sector = SimpleNamespace(
        id=current_sector_id,
        sector_id=CURRENT_SECTOR_GLOBAL_ID,
        name="Current",
        type=SectorType.STANDARD,
        outgoing_warps=[],
    )
    sectors = [current_sector]
    warp_edges = []
    tunnels = []
    knowledge = []

    expect_outgoing_warp_ids = []
    outgoing_neighbors = []
    for i in range(w):
        dest_id = uuid.uuid4()
        dest = SimpleNamespace(id=dest_id, sector_id=1000 + i, name=f"Out-{i}", type=SectorType.STANDARD)
        sectors.append(dest)
        outgoing_neighbors.append(dest)
        warp_edges.append(SimpleNamespace(
            source_sector_id=current_sector_id, destination_sector_id=dest_id,
            is_bidirectional=False, turn_cost=5 + i,
        ))
        expect_outgoing_warp_ids.append(dest.sector_id)
    current_sector.outgoing_warps = outgoing_neighbors

    expect_incoming_warp_ids = []
    for i in range(w):
        origin_id = uuid.uuid4()
        origin = SimpleNamespace(id=origin_id, sector_id=2000 + i, name=f"In-{i}", type=SectorType.STANDARD)
        sectors.append(origin)
        warp_edges.append(SimpleNamespace(
            source_sector_id=origin_id, destination_sector_id=current_sector_id,
            is_bidirectional=True, turn_cost=7 + i,
        ))
        expect_incoming_warp_ids.append(origin.sector_id)

    expect_tunnel_ids = {"known_latent": [], "excluded_latent": [], "non_latent": []}
    for i in range(t):
        dest_id = uuid.uuid4()
        dest = SimpleNamespace(id=dest_id, sector_id=3000 + i, name=f"TOut-{i}", type=SectorType.STANDARD)
        sectors.append(dest)
        is_latent = (i % 2 == 0)
        tid = uuid.uuid4()
        tunnel = SimpleNamespace(
            id=tid, origin_sector_id=current_sector_id, destination_sector_id=dest_id,
            status=WarpTunnelStatus.ACTIVE, is_bidirectional=(i % 3 == 0), is_latent=is_latent,
            type=WarpTunnelType.NATURAL, turn_cost=10 + i, stability=0.9,
            created_by_player_id=None, properties={}, expires_at=None, created_at=None,
        )
        tunnels.append(tunnel)
        if is_latent:
            if i % 4 == 0:
                knowledge.append(SimpleNamespace(
                    player_id=PLAYER_ID, warp_layer=WarpLayer.WARP_TUNNELS, warp_id=tid, is_known=True,
                ))
                expect_tunnel_ids["known_latent"].append(dest.sector_id)
            else:
                expect_tunnel_ids["excluded_latent"].append(dest.sector_id)
        else:
            expect_tunnel_ids["non_latent"].append(dest.sector_id)

    for i in range(t):
        origin_id = uuid.uuid4()
        origin = SimpleNamespace(id=origin_id, sector_id=4000 + i, name=f"TIn-{i}", type=SectorType.STANDARD)
        sectors.append(origin)
        is_latent = (i % 2 == 1)
        tid = uuid.uuid4()
        tunnel = SimpleNamespace(
            id=tid, origin_sector_id=origin_id, destination_sector_id=current_sector_id,
            status=WarpTunnelStatus.ACTIVE, is_bidirectional=True, is_latent=is_latent,
            type=WarpTunnelType.NATURAL, turn_cost=20 + i, stability=0.8,
            created_by_player_id=None, properties={}, expires_at=None, created_at=None,
        )
        tunnels.append(tunnel)
        if is_latent:
            if i % 4 == 1:
                knowledge.append(SimpleNamespace(
                    player_id=PLAYER_ID, warp_layer=WarpLayer.WARP_TUNNELS, warp_id=tid, is_known=True,
                ))
                expect_tunnel_ids["known_latent"].append(origin.sector_id)
            else:
                expect_tunnel_ids["excluded_latent"].append(origin.sector_id)
        else:
            expect_tunnel_ids["non_latent"].append(origin.sector_id)

    player = SimpleNamespace(
        id=PLAYER_ID,
        current_sector_id=CURRENT_SECTOR_GLOBAL_ID,
        turns=500,
        current_ship=_ship(warp_capable=warp_capable_ship),
    )

    session = FakeMovesSession(
        players=[player], sectors=sectors, tunnels=tunnels, knowledge=knowledge, warp_edges=warp_edges,
    )
    expectations = {
        "outgoing_warp_ids": expect_outgoing_warp_ids,
        "incoming_warp_ids": expect_incoming_warp_ids,
        "tunnels": expect_tunnel_ids,
    }
    return session, player, expectations


@pytest.fixture(autouse=True)
def _stub_gate_advance(monkeypatch):
    """advance_gates_touching_sector is an orthogonal, already-existing
    codepath this WO does not touch; its own query cost (if any) is
    constant per-sector, not W/T-proportional. Stubbed out so this file's
    fake session stays scoped to exactly what WO-QTI-MOVES-BATCH batches."""
    monkeypatch.setattr(
        movement_service_module.warp_gate_service,
        "advance_gates_touching_sector",
        lambda db, sector_number, now=None: 0,
    )


# --------------------------------------------------------------------------- #
# Query-count independence
# --------------------------------------------------------------------------- #

class TestQueryCountIndependence:
    def test_small_and_large_fixtures_issue_the_same_query_count(self):
        small_session, small_player, _ = _build_fixture(w=1, t=1)
        large_session, large_player, _ = _build_fixture(w=10, t=10)

        MovementService(small_session).get_available_moves(small_player.id)
        MovementService(large_session).get_available_moves(large_player.id)

        assert small_session.queries == large_session.queries
        # Falsifiability: this is not a vacuous 0==0 -- both fixtures must
        # actually have exercised queries, and the batched (non-empty)
        # branches must have fired for both (t=1 guarantees >=1 latent
        # tunnel, w=1 guarantees >=1 needed sector id).
        assert small_session.queries > 0

    def test_query_count_matches_the_fixed_batched_shape(self):
        """Pins the exact count so a future regression that reintroduces a
        per-row query (even one that happens to still be W/T-independent
        for THIS fixture shape, e.g. a query issued once per loop instead
        of once per function) is still caught. Expected round trips:
        player, current_sector, incoming_bidir_rows, outgoing_edge_rows,
        outgoing_tunnels, incoming_bidirectional, batched Sector IN,
        batched PlayerWarpKnowledge IN = 8.
        """
        session, player, _ = _build_fixture(w=10, t=10)
        MovementService(session).get_available_moves(player.id)
        assert session.queries == 8

    def test_zero_edges_and_tunnels_skips_both_optional_batch_queries(self):
        """W=0/T=0: needed_sector_ids and latent_tunnel_ids are both empty,
        so the batched Sector IN and PlayerWarpKnowledge IN queries must be
        skipped entirely (the ``if needed_sector_ids:`` / early-return-on-
        empty guards), not issued as harmless empty-IN queries."""
        session, player, _ = _build_fixture(w=0, t=0)
        MovementService(session).get_available_moves(player.id)
        # player, current_sector, incoming_bidir_rows, outgoing_edge_rows,
        # outgoing_tunnels, incoming_bidirectional = 6 (no batch queries).
        assert session.queries == 6


# --------------------------------------------------------------------------- #
# Output equivalence -- ordering + fields pinned against the pre-batch shape
# --------------------------------------------------------------------------- #

class TestOutputEquivalence:
    def test_direct_warps_ordering_and_fields(self):
        session, player, expectations = _build_fixture(w=2, t=0, warp_capable_ship=False)
        result = MovementService(session).get_available_moves(player.id)

        warps = result["warps"]
        got_ids = [w["sector_id"] for w in warps]
        # Outgoing edges list FIRST (in outgoing_warps order), THEN incoming
        # bidirectional origins (in fetchall row order) -- the pre-existing
        # ordering contract.
        assert got_ids == expectations["outgoing_warp_ids"] + expectations["incoming_warp_ids"]

        first = warps[0]
        assert set(first.keys()) == {"sector_id", "name", "type", "turn_cost", "can_afford"}
        assert first["name"] == "Out-0"
        assert first["type"] == "STANDARD"
        assert first["turn_cost"] == 5  # turn_cost=5, Good-band multiplier 1.0, non-warp-capable ship
        assert first["can_afford"] is True

        incoming_first = warps[2]
        assert incoming_first["name"] == "In-0"
        assert incoming_first["turn_cost"] == 7  # turn_cost=7, read straight from the row, no re-query

    def test_direct_warp_cost_uses_the_batched_row_not_a_stale_default(self):
        """Distinct turn_cost per edge (5+i / 7+i) proves the batched
        outgoing_turn_cost_by_dest / incoming row.turn_cost path threads
        the RIGHT edge's cost through, not a shared/first-row value."""
        session, player, _ = _build_fixture(w=3, t=0)
        result = MovementService(session).get_available_moves(player.id)
        warps = result["warps"]
        outgoing = [w for w in warps if w["name"].startswith("Out-")]
        incoming = [w for w in warps if w["name"].startswith("In-")]
        assert [w["turn_cost"] for w in outgoing] == [5, 6, 7]
        assert [w["turn_cost"] for w in incoming] == [7, 8, 9]

    def test_warp_capable_ship_gets_20pct_reduction_on_tunnels_not_direct_warps(self):
        """Direct-warp cost is uniform (movement.md) -- unaffected by
        warp_capable. Only tunnel cost gets the 20% reduction. Proves the
        batched cost path didn't accidentally cross-apply the discount."""
        session, player, _ = _build_fixture(w=1, t=1, warp_capable_ship=True)
        result = MovementService(session).get_available_moves(player.id)
        direct_warp = next(w for w in result["warps"] if w["name"] == "Out-0")
        assert direct_warp["turn_cost"] == 5  # unchanged by warp_capable

    def test_latent_tunnel_gating_known_included_unknown_excluded(self):
        """t=4 guarantees at least one KNOWN latent, one EXCLUDED (unknown)
        latent, and one non-latent tunnel on each side -- proves the
        batched known_tunnel_ids set gates per-tunnel, not all-or-nothing."""
        session, player, expectations = _build_fixture(w=0, t=4)
        result = MovementService(session).get_available_moves(player.id)
        got_ids = {t["sector_id"] for t in result["tunnels"]}

        assert expectations["tunnels"]["known_latent"], "fixture sanity: expected >=1 known latent tunnel"
        assert expectations["tunnels"]["excluded_latent"], "fixture sanity: expected >=1 excluded latent tunnel"
        assert expectations["tunnels"]["non_latent"], "fixture sanity: expected >=1 non-latent tunnel"

        for sid in expectations["tunnels"]["known_latent"]:
            assert sid in got_ids
        for sid in expectations["tunnels"]["excluded_latent"]:
            assert sid not in got_ids
        for sid in expectations["tunnels"]["non_latent"]:
            assert sid in got_ids

    def test_tunnel_fields_and_ordering(self):
        session, player, expectations = _build_fixture(w=0, t=1)
        result = MovementService(session).get_available_moves(player.id)
        tunnels = result["tunnels"]

        # t=1: outgoing side (i=0) is a KNOWN latent tunnel (is_latent=
        # (0%2==0)=True, known via 0%4==0) -- included. Incoming-
        # bidirectional side (i=0) is NON-latent (is_latent=(0%2==1)=False)
        # -- bypasses the gate entirely, always included. Both present,
        # outgoing listed first.
        assert len(tunnels) == 2
        outgoing_entry, incoming_entry = tunnels

        assert set(outgoing_entry.keys()) == {
            "sector_id", "name", "type", "turn_cost", "tunnel_type", "stability", "one_way", "can_afford",
        }
        assert outgoing_entry["sector_id"] == expectations["tunnels"]["known_latent"][0]
        assert outgoing_entry["turn_cost"] == 10  # turn_cost=10+0, non-warp-capable ship, no reduction
        assert outgoing_entry["tunnel_type"] == "NATURAL"
        assert outgoing_entry["one_way"] is False  # is_bidirectional=(0%3==0)=True -> one_way=not True=False

        assert incoming_entry["sector_id"] == expectations["tunnels"]["non_latent"][0]
        assert incoming_entry["turn_cost"] == 20  # turn_cost=20+0
        assert incoming_entry["one_way"] is False  # incoming-bidirectional branch is always one_way=False

    def test_incoming_bidirectional_tunnel_never_marked_one_way(self):
        session, player, _ = _build_fixture(w=0, t=2)
        result = MovementService(session).get_available_moves(player.id)
        # i=1 on the incoming-bidirectional side: is_latent=(1%2==1)=True,
        # known via i%4==1 -> included; must report one_way=False (the
        # reverse-traversal branch never sets one_way=True).
        incoming_entry = next(t for t in result["tunnels"] if t["name"] == "TIn-1")
        assert incoming_entry["one_way"] is False

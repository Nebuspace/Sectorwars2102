"""
NavService — course-plotting for ADR-0072 Phase 1.

Implements Dijkstra over the player's *known* sector graph (visited sectors
from ARIAExplorationMap ∪ corp-shared exploration ∪ the player's current
sector) using a synchronous SQLAlchemy Session — the same pattern used by
MovementService and TradingService.

NOTE: route_optimizer.py (RouteOptimizer) uses AsyncSession exclusively and
cannot be called from sync routes without bridging infrastructure.  This
service writes its own small Dijkstra rather than pulling in that async
dependency.  The algorithm is structurally identical to RouteOptimizer's
_dijkstra_path but operates on sync queries and respects the known-sector
filter.

Pre-discovered public space (Terran Space / Central Nexus regions) is
**not** yet a persisted per-player knowledge layer — that is Pillar 2 of
ADR-0072.  Phase 1 scopes known space to:
  (a) player's own ARIAExplorationMap visits
  (b) corp-mate (team-mate) ARIAExplorationMap visits when player.team_id set
  (c) the player's current sector

Phase 2 will introduce the player_known_sectors table and the Federation
public-chart layer (Terran / Nexus sectors).  Until then players in those
regions discover connectivity by flying through it — consistent with the ADR's
"existing known graph" wording for Phase 1.
"""

from __future__ import annotations

import heapq
import logging
import math
from dataclasses import dataclass
from typing import Dict, List, Optional, Set, Tuple

from sqlalchemy import text
from sqlalchemy.orm import Session

from src.models.aria_personal_intelligence import ARIAExplorationMap
from src.models.player import Player
from src.models.sector import Sector, sector_warps
from src.models.warp_tunnel import WarpTunnel, WarpTunnelStatus

logger = logging.getLogger(__name__)

# Maximum hop count accepted for a route.  Plots exceeding this are refused
# with a clear message so pathfinding over a disconnected or very large
# known-graph cannot run indefinitely.
MAX_HOPS = 200

# Per-hop turn penalty applied to a hop's edge cost under the MIN_RISK
# objective, scaled by how *unsafe* the destination hop is.  A hop with
# safety_rating 1.0 (perfectly safe) adds no penalty; a hop with safety_rating
# 0.0 (most dangerous) adds the full RISK_WEIGHT to its turn cost.  The penalty
# rides on top of the real turn cost so a MIN_RISK route is still a physically
# sensible walk — a safer-but-longer path wins only when its accumulated safety
# savings outweigh the extra turns.  RISK_WEIGHT is the "turn-equivalents" a
# player is willing to spend to avoid one maximally-dangerous hop.
RISK_WEIGHT = 5.0

# Neutral safety used for hops the player has not personally visited (no
# visit-derived intelligence).  Mirrors ARIAExplorationMap.safety_rating's own
# default of 0.5 — charted/corp-shared-only hops carry no safety signal, so
# they are treated as neither safe nor dangerous under MIN_RISK weighting.
NEUTRAL_SAFETY = 0.5

# Routing objectives understood by NavService.  MIN_TIME is the default
# (Dijkstra by turn cost); MIN_RISK additionally penalises low-safety hops.
OBJECTIVE_MIN_TIME = "min_time"
OBJECTIVE_MIN_RISK = "min_risk"

# Ceiling on the server-computed depth bound for GET /nav/chart?bounded=true
# (WO-NAV-REACH-BACKEND).  NO-CANON kernel — mirrors the player-client's own
# navChartTransform.MAX_SCANNER_RANGE (12), which caps its client-side BFS
# depth the same way.  A player's *effective* scanner range (hull spec +
# stacked Sensor-upgrade bonus) can in principle exceed this; the ceiling
# keeps a single bounded-chart BFS from growing arbitrarily large regardless.
CHART_BOUNDED_DEPTH_CEILING = 12


@dataclass
class HopInfo:
    """Per-hop data returned in a successful plot response."""

    sector_id: int
    name: str
    turn_cost: int        # cost of the *edge arriving at* this hop
    visited: bool         # player's own ARIAExplorationMap has an entry
    safety_rating: Optional[float]   # visit-derived; null for unvisited hops
    via_tunnel: bool      # true when this hop was reached via a WarpTunnel row


class NavService:
    """
    Synchronous navigation service — course plotting over the player's known
    sector graph.

    Instantiate per request with the current db Session (mirror of
    MovementService / TradingService patterns).
    """

    def __init__(self, db: Session) -> None:
        self.db = db

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_known_sector_ids(self, player: Player) -> Set[int]:
        """
        Return the set of numeric sector_ids the player may plot through.

        Phase 1 sources:
          (a) Player's own ARIAExplorationMap (join to Sector for numeric id)
          (b) Team-mates' ARIAExplorationMap when player.team_id is set
              (corp-shared topology per ADR-0072)
          (c) The player's current sector (always reachable — they are there)
        """
        known: Set[int] = set()

        # (a) Own exploration map
        own_rows = (
            self.db.query(Sector.sector_id)
            .join(ARIAExplorationMap, ARIAExplorationMap.sector_id == Sector.id)
            .filter(ARIAExplorationMap.player_id == player.id)
            .all()
        )
        for (sid,) in own_rows:
            known.add(sid)

        # (b) Corp-shared: team-mate exploration maps
        if player.team_id is not None:
            # Fetch all player ids on the same team except self, then union
            # their exploration maps.  A single JOIN is cleaner and avoids
            # loading full Player rows.
            teammate_sids = (
                self.db.query(Sector.sector_id)
                .join(ARIAExplorationMap, ARIAExplorationMap.sector_id == Sector.id)
                .join(Player, Player.id == ARIAExplorationMap.player_id)
                .filter(
                    Player.team_id == player.team_id,
                    Player.id != player.id,
                )
                .all()
            )
            for (sid,) in teammate_sids:
                known.add(sid)

        # (c) Current sector — always included regardless of exploration state
        known.add(player.current_sector_id)

        return known

    def get_routing_sector_ids(self, player: Player) -> Set[int]:
        """
        Sector ids ARIA may traverse when laying in a multi-hop course.

        Extends ``get_known_sector_ids`` with **ring-1 exits**: every warp /
        tunnel destination that leaves a known sector. Those exits are already
        exposed by ``MovementService.get_available_moves`` when standing in the
        source sector (name included) — without them in the plot graph, a
        course like ``known → unvisited-exit → known`` is wrongly refused even
        though the pilot can fly it hop-by-hop by hand.

        Ring-1 is only one hop of fog past known space. Deeper unvisited
        topology stays unplottable until flown or charted.
        """
        known = self.get_known_sector_ids(player)
        return known | self._ring1_destination_ids(known)

    def _ring1_destination_ids(self, known_ids: Set[int]) -> Set[int]:
        """Numeric sector_ids one directed hop past *known_ids* via warps/tunnels."""
        if not known_ids:
            return set()

        known_sectors = (
            self.db.query(Sector.sector_id, Sector.id)
            .filter(Sector.sector_id.in_(known_ids))
            .all()
        )
        known_uuids = [row.id for row in known_sectors]
        if not known_uuids:
            return set()

        dest_uuids: Set[object] = set()

        warp_rows = self.db.execute(
            sector_warps.select().where(
                sector_warps.c.source_sector_id.in_(known_uuids)
            )
        ).fetchall()
        for row in warp_rows:
            dest_uuids.add(row.destination_sector_id)

        # Incoming bidir warps: standing in known dest, source is a ring-1 exit
        # the pilot can take in reverse (same as MovementService).
        incoming_bidir = self.db.execute(
            sector_warps.select().where(
                sector_warps.c.destination_sector_id.in_(known_uuids),
                sector_warps.c.is_bidirectional == True,  # noqa: E712
            )
        ).fetchall()
        for row in incoming_bidir:
            dest_uuids.add(row.source_sector_id)

        tunnel_out = (
            self.db.query(WarpTunnel.destination_sector_id)
            .filter(
                WarpTunnel.status == WarpTunnelStatus.ACTIVE,
                WarpTunnel.origin_sector_id.in_(known_uuids),
            )
            .all()
        )
        for (dest_id,) in tunnel_out:
            dest_uuids.add(dest_id)

        tunnel_in = (
            self.db.query(WarpTunnel.origin_sector_id)
            .filter(
                WarpTunnel.status == WarpTunnelStatus.ACTIVE,
                WarpTunnel.is_bidirectional == True,  # noqa: E712
                WarpTunnel.destination_sector_id.in_(known_uuids),
            )
            .all()
        )
        for (origin_id,) in tunnel_in:
            dest_uuids.add(origin_id)

        if not dest_uuids:
            return set()

        rows = (
            self.db.query(Sector.sector_id)
            .filter(Sector.id.in_(list(dest_uuids)))
            .all()
        )
        return {sid for (sid,) in rows} - known_ids

    def get_chart(self, player: Player, bounded: bool = False) -> Dict:
        """
        Assemble the player's KNOWN navigation surface for the NAV CHART
        cockpit view (WO-PUX-NAVCHART): every sector in the known graph
        (``get_known_sector_ids`` — visited ∪ corp-shared ∪ current, the
        same assembly ``plot()`` uses), the warp/tunnel edges between them,
        and "frontier" stubs — the numeric ``sector_id``s of unknown
        sectors one hop beyond a known one.

        Read-only, like ``plot()``. Frontier entries carry ONLY a
        ``sector_id`` (as ``id``) plus the numeric ``sector_id`` of the ONE
        known sector that surfaced it (``from``) — never name, type, or
        coordinates — so an unexplored neighbour's identity is never leaked
        through the chart (mirrors course-plotting.md's
        visit-gated-intelligence invariant: knowing an edge exists is not
        the same as knowing what's on the other end of it). ``from`` exists
        purely so a client can attach the frontier stub to the known graph
        it hangs off of; it carries no information about the frontier
        sector itself. When a frontier sector is reachable from more than
        one known sector, ``from`` is the SMALLEST known ``sector_id``
        among them (deterministic — one linkage is enough to attach the
        node, and this keeps repeated calls byte-identical for the same
        known-set).

        *bounded* (WO-NAV-REACH-BACKEND, default False — today's exact
        unbounded behaviour, byte-identical): when True and the player has
        a ``current_ship``, ``known_ids`` is narrowed with a server-side
        DIRECTED BFS (see ``_bound_known_ids``) capped at the player's
        effective scanner range, so the chart reports only what that ship
        could actually reach. The depth is always SERVER-computed from the
        ship, never a client-supplied number — a client skin cannot fake
        the radius. No ship -> unbounded, unconditionally (preserves the
        "current sector always included" invariant). Sectors excluded by
        the bound are demoted to frontier stubs by the unchanged edge/
        frontier assembly below — see ``_bound_known_ids``'s docstring.

        Returns:
          {"sectors": [{"sector_id", "name", "type", "x", "y", "z",
                        "visited", "current"}, ...],
           "edges": [{"from", "to", "kind": "warp"|"tunnel"}, ...],
           "frontier": [{"id": sector_id, "from": known_sector_id}, ...]}
        """
        known_ids = self.get_known_sector_ids(player)
        if not known_ids:
            return {"sectors": [], "edges": [], "frontier": []}

        if bounded and player.current_ship is not None:
            known_ids = self._bound_known_ids(player, known_ids)

        known_sectors = (
            self.db.query(Sector).filter(Sector.sector_id.in_(known_ids)).all()
        )
        uuid_to_sid: Dict[object, int] = {s.id: s.sector_id for s in known_sectors}
        known_uuids = list(uuid_to_sid.keys())

        # Own-visited set — the same visit-derived source plot() uses for
        # hop.visited (corp-shared membership makes a sector known/plottable
        # but does not mark it visited; only the player's OWN exploration
        # counts here).
        own_visited_ids = set(self._build_safety_by_sid(player).keys())

        sectors_payload = [
            {
                "sector_id": s.sector_id,
                "name": s.name,
                "type": s.type.value if hasattr(s.type, "value") else str(s.type),
                "x": s.x_coord,
                "y": s.y_coord,
                "z": s.z_coord,
                "visited": s.sector_id in own_visited_ids,
                "current": s.sector_id == player.current_sector_id,
            }
            for s in known_sectors
        ]

        warp_rows = self.db.execute(
            sector_warps.select().where(
                sector_warps.c.source_sector_id.in_(known_uuids)
            )
        ).fetchall()

        tunnel_rows = (
            self.db.query(WarpTunnel)
            .filter(
                WarpTunnel.status == WarpTunnelStatus.ACTIVE,
                WarpTunnel.origin_sector_id.in_(known_uuids),
            )
            .all()
        )

        # Resolve the numeric sector_id of any neighbour OUTSIDE the known
        # set — needed to report frontier stubs by id (name/type withheld).
        neighbour_uuids = {row.destination_sector_id for row in warp_rows}
        neighbour_uuids.update(t.destination_sector_id for t in tunnel_rows)
        unknown_uuids = neighbour_uuids - set(uuid_to_sid.keys())
        if unknown_uuids:
            extra = self.db.query(Sector).filter(Sector.id.in_(unknown_uuids)).all()
            for s in extra:
                uuid_to_sid[s.id] = s.sector_id

        edges, frontier = self._assemble_edges_and_frontier(
            warp_rows, tunnel_rows, uuid_to_sid, known_ids
        )

        return {
            "sectors": sectors_payload,
            "edges": edges,
            "frontier": frontier,
        }

    def _bound_known_ids(self, player: Player, known_ids: Set[int]) -> Set[int]:
        """
        WO-NAV-REACH-BACKEND: server-side depth bound for ``get_chart``'s
        ``bounded=True`` path. Only called when ``player.current_ship`` is
        not None (get_chart's no-ship-is-unbounded guard runs before this).

        The depth is SERVER-COMPUTED from the player's effective scanner
        range (hull spec + Sensor-upgrade bonus) — never a client-supplied
        int — via the same Rail-A pattern ``movement_service.py``'s scan()
        uses (:1461-1475), so a client skin cannot fake the radius.

        Runs a DIRECTED BFS (``_bfs_within_depth``) over the SAME adjacency
        ``_build_known_graph`` builds for ``plot()`` — respects
        ``is_bidirectional`` on both warps and tunnels, so a one-way edge is
        only walkable in its stored direction (the correct "can I actually
        reach it in N hops" semantic, not a symmetric "is it nearby" one).

        Returns the subset of *known_ids* reachable from
        ``player.current_sector_id`` within
        ``min(effective_scanner_range, CHART_BOUNDED_DEPTH_CEILING)`` hops.
        A known sector excluded by the bound is not deleted from the
        player's knowledge — it simply falls out of ``known_ids`` for THIS
        response, so the unchanged ``_assemble_edges_and_frontier`` pass
        that follows demotes it to a ``{id, from}`` frontier stub instead
        of a full sector entry. That is strictly MORE informative than the
        client's current all-known-included heuristic, not a defect.
        """
        ship = player.current_ship

        # --- Rail A: effective scanner_range (hull spec + SENSOR upgrade) ---
        # Mirrors movement_service.py:1461-1475's established pattern.
        from src.models.ship import ShipSpecification
        from src.services.ship_upgrade_service import ShipUpgradeService

        spec = (
            self.db.query(ShipSpecification)
            .filter(ShipSpecification.type == ship.type)
            .first()
        )
        base_range = spec.scanner_range if spec and spec.scanner_range is not None else 0
        effective_range = ShipUpgradeService.effective_scanner_range(ship, base_range)
        depth_cap = min(effective_range, CHART_BOUNDED_DEPTH_CEILING)

        graph, _ = self._build_known_graph(known_ids)
        return self._bfs_within_depth(graph, player.current_sector_id, depth_cap)

    @staticmethod
    def _bfs_within_depth(
        graph: Dict[int, List[Tuple[int, int, bool]]],
        start_sid: int,
        depth_cap: int,
    ) -> Set[int]:
        """
        Directed BFS over *graph* (an adjacency shaped like
        ``_build_known_graph``'s return — sector_id -> list of
        ``(neighbour_sid, turn_cost, via_tunnel)``, already respecting
        ``is_bidirectional``) from *start_sid*, capped at *depth_cap* hops.

        Returns the set of sector_ids reachable within the cap, including
        *start_sid* itself at depth 0 (so the "current sector always
        included" invariant holds even when *depth_cap* is 0).
        """
        reachable: Set[int] = {start_sid}
        frontier: Set[int] = {start_sid}
        depth = 0
        while frontier and depth < depth_cap:
            next_frontier: Set[int] = set()
            for sid in frontier:
                for (neighbour, _cost, _via_tunnel) in graph.get(sid, []):
                    if neighbour not in reachable:
                        reachable.add(neighbour)
                        next_frontier.add(neighbour)
            frontier = next_frontier
            depth += 1
        return reachable

    def _assemble_edges_and_frontier(
        self,
        warp_rows: List[object],
        tunnel_rows: List[WarpTunnel],
        uuid_to_sid: Dict[object, int],
        known_ids: Set[int],
    ) -> Tuple[List[Dict], List[Dict]]:
        """
        Walk *warp_rows* and *tunnel_rows* once each, splitting them into
        known↔known ``edges`` and known→unknown ``frontier`` stubs.  Factored
        out of ``get_chart`` purely to keep that method's branch count in
        check (mccabe C901) — no behaviour beyond what ``get_chart`` used to
        do inline.  See ``get_chart``'s docstring for the ``frontier``
        ``{"id", "from"}`` shape and the deterministic-smallest-``from``
        tie-break.
        """
        edges: List[Dict] = []
        edge_seen: Set[Tuple[int, int, str]] = set()
        # frontier_sid -> the smallest known sector_id observed surfacing it.
        frontier_from: Dict[int, int] = {}

        def add_edge(src_sid: int, dst_sid: int, kind: str) -> None:
            key = (src_sid, dst_sid, kind)
            if key in edge_seen:
                return
            edge_seen.add(key)
            edges.append({"from": src_sid, "to": dst_sid, "kind": kind})

        def add_frontier(frontier_sid: int, from_sid: int) -> None:
            existing = frontier_from.get(frontier_sid)
            if existing is None or from_sid < existing:
                frontier_from[frontier_sid] = from_sid

        for row in warp_rows:
            self._route_chart_edge(
                uuid_to_sid.get(row.source_sector_id),
                uuid_to_sid.get(row.destination_sector_id),
                "warp",
                row.is_bidirectional,
                known_ids,
                add_edge,
                add_frontier,
            )

        for tunnel in tunnel_rows:
            self._route_chart_edge(
                uuid_to_sid.get(tunnel.origin_sector_id),
                uuid_to_sid.get(tunnel.destination_sector_id),
                "tunnel",
                tunnel.is_bidirectional,
                known_ids,
                add_edge,
                add_frontier,
            )

        frontier = [
            {"id": fid, "from": frontier_from[fid]} for fid in sorted(frontier_from)
        ]
        return edges, frontier

    @staticmethod
    def _route_chart_edge(
        src_sid: Optional[int],
        dst_sid: Optional[int],
        kind: str,
        bidirectional: bool,
        known_ids: Set[int],
        add_edge,
        add_frontier,
    ) -> None:
        """
        Classify one warp/tunnel row as either a known↔known edge (recorded
        via *add_edge*, both directions when bidirectional) or a
        known→unknown frontier stub (recorded via *add_frontier*).  Shared by
        both the warp and tunnel loops in ``_assemble_edges_and_frontier`` —
        the two row shapes differ only in field names, resolved by the
        caller before this is invoked.
        """
        if src_sid is None or dst_sid is None:
            logger.warning("nav_service.get_chart: unresolved %s endpoint", kind)
            return
        if dst_sid in known_ids:
            add_edge(src_sid, dst_sid, kind)
            if bidirectional and src_sid != dst_sid:
                add_edge(dst_sid, src_sid, kind)
        else:
            add_frontier(dst_sid, src_sid)

    def _build_safety_by_sid(self, player: Player) -> Dict[int, Optional[float]]:
        """
        Map numeric ``Sector.sector_id`` -> the player's OWN visit-derived
        ``ARIAExplorationMap.safety_rating`` for every sector the player has
        personally flown.

        This is the single source of visit-derived safety used both to weight
        the MIN_RISK Dijkstra and to annotate hops in the response.  Sectors
        absent from this map are sectors the player has not personally visited
        (charted/corp-shared-only hops carry no safety intelligence — visit-
        gated intelligence stays visit-gated).  A present key whose value is
        None means a visited sector whose rating column is null.
        """
        rows = (
            self.db.query(Sector.sector_id, ARIAExplorationMap.safety_rating)
            .join(ARIAExplorationMap, ARIAExplorationMap.sector_id == Sector.id)
            .filter(ARIAExplorationMap.player_id == player.id)
            .all()
        )
        return {sid: safety for (sid, safety) in rows}

    def plot(
        self,
        player: Player,
        target_sector_id: int,
        objective: str = OBJECTIVE_MIN_TIME,
    ) -> Dict:
        """
        Compute a course from the player's current sector to *target_sector_id*.

        The routing surface is ``get_known_sector_ids`` (visited ∪ corp ∪ current)
        plus **ring-1 exits** — warp/tunnel destinations leaving known space.
        That lets multi-hop plots bridge through an unvisited exit the pilot can
        already see on available-moves (e.g. known → unscanned lane → known),
        which hand-flying allows but a visited-only graph wrongly refused.

        *objective* selects the routing semantics, mirroring RouteOptimizer's
        objective-weighted Dijkstra (see ADR-0072 consciousness tiers):

          - ``"min_time"`` (default): shortest path by turn cost — turn cost is
            physics, priced identically for every plottable hop.
          - ``"min_risk"``: the Awakened-tier alternative built from the
            player's visit-derived ``ARIAExplorationMap.safety_rating``.  Each
            hop's edge cost is penalised in proportion to how dangerous its
            destination is, so a safer-but-longer path is preferred when its
            accumulated safety savings outweigh the extra turns.  Hops the
            player has not personally flown carry no safety intelligence and
            are weighted with the neutral default (visit-gated intelligence
            stays visit-gated).

        Any unrecognised objective falls back to ``"min_time"``.

        Returns a dict matching the frozen contract:
          Reachable:
            {"success": True, "reachable": True, "target_sector_id": int,
             "hops": [...HopInfo dicts...], "total_turns": int}
          Unreachable:
            {"success": True, "reachable": False, "target_sector_id": int,
             "nearest_known": {"sector_id": int, "name": str} | None}
          Unknown target:
            {"success": True, "reachable": False, "target_sector_id": int,
             "nearest_known": ..., "error": "unknown sector"}
          Runaway guard:
            {"success": False, "message": "..."}
        """
        known_ids = self.get_known_sector_ids(player)
        # Ring-1 exits from known space are traversable for multi-hop plots
        # (bridges unvisited lanes that already appear on available_moves).
        routing_ids = known_ids | self._ring1_destination_ids(known_ids)
        start_sid = player.current_sector_id

        # Verify target sector exists (look up by numeric sector_id)
        target_sector = (
            self.db.query(Sector)
            .filter(Sector.sector_id == target_sector_id)
            .first()
        )

        if target_sector is None:
            # Unknown sector — return unreachable shape with error field
            nearest = self._nearest_known_sector(target_sector_id, known_ids)
            return {
                "success": True,
                "reachable": False,
                "target_sector_id": target_sector_id,
                "nearest_known": nearest,
                "error": "unknown sector",
                "reason": "unknown_sector",
            }

        # If already there, return a trivial empty-hop route
        if start_sid == target_sector_id:
            return {
                "success": True,
                "reachable": True,
                "target_sector_id": target_sector_id,
                "hops": [],
                "total_turns": 0,
            }

        # Target must be on the routing surface (known or ring-1 exit)
        if target_sector_id not in routing_ids:
            nearest = self._nearest_known_sector_euclidean(target_sector, known_ids)
            return {
                "success": True,
                "reachable": False,
                "target_sector_id": target_sector_id,
                "nearest_known": nearest,
                "reason": "uncharted",
            }

        # Build adjacency over routing ids (known ∪ ring-1)
        graph, edge_meta = self._build_known_graph(routing_ids)

        # Per-hop visit-derived safety, keyed by numeric sector_id, for the
        # player's OWN exploration map.  Built once here so it can both weight
        # the MIN_RISK Dijkstra and annotate the response without a re-query.
        sid_safety = self._build_safety_by_sid(player)

        # Select the edge-weight function for this plot's objective.  MIN_RISK
        # penalises a hop by how dangerous its *destination* is; everything
        # else (including unrecognised objectives) falls back to pure turn cost
        # (MIN_TIME).  Mirrors RouteOptimizer._dijkstra_path's weight_fn hook.
        if objective == OBJECTIVE_MIN_RISK:
            def weight_fn(neighbour_sid: int, edge_cost: int, _via_tunnel: bool) -> float:
                safety = sid_safety.get(neighbour_sid, NEUTRAL_SAFETY)
                if safety is None:
                    safety = NEUTRAL_SAFETY
                # safety 1.0 -> no penalty; safety 0.0 -> full RISK_WEIGHT
                return edge_cost + RISK_WEIGHT * (1.0 - safety)
        else:
            weight_fn = None  # default: weight == edge turn cost (MIN_TIME)

        # Run Dijkstra over the routing graph
        path_sids, costs, via_tunnel_flags = self._dijkstra(
            graph, edge_meta, start_sid, target_sector_id, weight_fn=weight_fn
        )

        if path_sids is None:
            # Target is on the routing surface but not reachable via directed
            # edges (one-way warps, or a disconnected component). Nearest must
            # be something the pilot can actually fly to — prefer a known
            # sector, never the target itself.
            reachable_from_here = self._bfs_within_depth(
                graph, start_sid, max(len(routing_ids), 1)
            )
            approach_ids = (reachable_from_here & known_ids) - {
                target_sector_id,
                start_sid,
            }
            if not approach_ids:
                approach_ids = reachable_from_here - {target_sector_id, start_sid}
            if not approach_ids:
                approach_ids = reachable_from_here - {target_sector_id}
            nearest = self._nearest_known_sector_euclidean(
                target_sector, approach_ids
            )
            return {
                "success": True,
                "reachable": False,
                "target_sector_id": target_sector_id,
                "nearest_known": nearest,
                "reason": "no_route",
            }

        # path_sids includes start; hops excludes start per contract
        hops_sids = path_sids[1:]  # drop the origin sector

        if len(hops_sids) > MAX_HOPS:
            return {
                "success": False,
                "message": (
                    f"Computed route is {len(hops_sids)} hops, which exceeds the "
                    f"{MAX_HOPS}-hop safety limit.  Break the journey into legs."
                ),
            }

        # Resolve hop sectors and build the response list
        # Build a map of sector_id -> Sector for the sectors on the path
        hop_sectors = (
            self.db.query(Sector)
            .filter(Sector.sector_id.in_(hops_sids))
            .all()
        )
        sector_map: Dict[int, Sector] = {s.sector_id: s for s in hop_sectors}

        # A hop is "visited" iff it appears in the player's OWN visit-derived
        # safety map (same ARIAExplorationMap rows as sid_safety, built above);
        # safety_rating is reported only for visited hops — charted/corp-shared
        # hops never carry safety intelligence (visit-gated stays visit-gated).
        hops: List[Dict] = []
        for i, sid in enumerate(hops_sids):
            sec = sector_map.get(sid)
            if sec is None:
                # Defensive: skip sectors that disappeared between graph build and now
                logger.warning("nav_service: sector %d missing from db during hop assembly", sid)
                continue

            visited = sid in sid_safety
            sr = sid_safety.get(sid) if visited else None
            via_tun = via_tunnel_flags[i] if i < len(via_tunnel_flags) else False

            hops.append({
                "sector_id": sec.sector_id,
                "name": sec.name,
                "turn_cost": costs[i],
                "visited": visited,
                "safety_rating": sr,
                "via_tunnel": via_tun,
            })

        total_turns = sum(h["turn_cost"] for h in hops)

        return {
            "success": True,
            "reachable": True,
            "target_sector_id": target_sector_id,
            "hops": hops,
            "total_turns": total_turns,
        }

    # ------------------------------------------------------------------
    # Graph construction (sync, known-sector-filtered)
    # ------------------------------------------------------------------

    def _build_known_graph(
        self,
        known_ids: Set[int],
    ) -> Tuple[Dict[int, List[Tuple[int, int, bool]]], Dict]:
        """
        Build an adjacency list limited to *known_ids*.

        Returns:
          graph: sector_id -> list of (neighbour_sid, turn_cost, via_tunnel)
          edge_meta: unused placeholder (reserved for future per-edge data)

        Both endpoints of every edge must be in known_ids — an edge that
        crosses into unknown space is excluded.

        Edge sources:
          1. sector_warps association table (bidirectional rows per bang schema)
          2. WarpTunnel rows with status ACTIVE (respect is_bidirectional)

        Turn costs:
          • sector_warps: uses the row's turn_cost column (default 1)
          • WarpTunnel: uses WarpTunnel.turn_cost column (default 1 when null)
        """
        # Map sector numeric id -> UUID for join resolution
        if not known_ids:
            return {}, {}

        known_sectors = (
            self.db.query(Sector.sector_id, Sector.id)
            .filter(Sector.sector_id.in_(known_ids))
            .all()
        )
        sid_to_uuid: Dict[int, object] = {row.sector_id: row.id for row in known_sectors}
        uuid_to_sid: Dict[object, int] = {v: k for k, v in sid_to_uuid.items()}

        graph: Dict[int, List[Tuple[int, int, bool]]] = {sid: [] for sid in known_ids}

        # 1. sector_warps — bang stores bidir connections as one row
        # (source, dest, is_bidirectional=True).  We must walk both
        # outgoing and incoming-bidir rows as MovementService does.
        known_uuids = list(sid_to_uuid.values())

        warp_rows = self.db.execute(
            sector_warps.select().where(
                sector_warps.c.source_sector_id.in_(known_uuids)
            )
        ).fetchall()

        for row in warp_rows:
            src_sid = uuid_to_sid.get(row.source_sector_id)
            dst_sid = uuid_to_sid.get(row.destination_sector_id)
            if src_sid is None or dst_sid is None:
                continue
            if dst_sid not in known_ids:
                continue
            tc = row.turn_cost if row.turn_cost else 1
            graph[src_sid].append((dst_sid, tc, False))
            if row.is_bidirectional and src_sid != dst_sid:
                graph.setdefault(dst_sid, []).append((src_sid, tc, False))

        # Note: incoming bidir rows where *source* is outside known_ids are
        # intentionally excluded — both endpoints must be in the known graph
        # for an edge to be traversable.  The first query already adds both
        # directions for all bidir rows whose source is known.

        # 2. WarpTunnel rows — ACTIVE status only; respect is_bidirectional
        tunnel_rows = (
            self.db.query(WarpTunnel)
            .filter(
                WarpTunnel.status == WarpTunnelStatus.ACTIVE,
                WarpTunnel.origin_sector_id.in_(known_uuids),
            )
            .all()
        )

        for tunnel in tunnel_rows:
            origin_sid = uuid_to_sid.get(tunnel.origin_sector_id)
            dest_sid = uuid_to_sid.get(tunnel.destination_sector_id)
            if origin_sid is None or dest_sid is None:
                continue
            if dest_sid not in known_ids:
                continue
            tc = tunnel.turn_cost if tunnel.turn_cost else 1
            graph.setdefault(origin_sid, []).append((dest_sid, tc, True))
            if tunnel.is_bidirectional:
                graph.setdefault(dest_sid, []).append((origin_sid, tc, True))

        # Note: bidir WarpTunnel rows whose *origin* is outside known_ids are
        # not added — both endpoints must be in the known graph.  The loop
        # above already adds the reverse edge for bidir tunnels whose origin
        # is known.

        return graph, {}

    # ------------------------------------------------------------------
    # Dijkstra (sync, turn-cost weighted)
    # ------------------------------------------------------------------

    def _dijkstra(
        self,
        graph: Dict[int, List[Tuple[int, int, bool]]],
        _edge_meta: Dict,
        src: int,
        dst: int,
        weight_fn=None,
    ) -> Tuple[Optional[List[int]], List[int], List[bool]]:
        """
        Standard cost-ordered Dijkstra returning
        (path_sids, per_hop_turn_costs, per_hop_via_tunnel).

        path_sids includes src.  per_hop_turn_costs[i] is the *real turn cost*
        of the edge arriving at path_sids[i+1] — turn cost is physics and is
        always reported to the player regardless of objective.  Returns
        (None, [], []) when dst is unreachable.

        *weight_fn* maps ``(neighbour_sid, edge_turn_cost, via_tunnel)`` to the
        numeric cost used to *order* the search (mirrors
        RouteOptimizer._dijkstra_path's weight_fn hook).  When None, the search
        is ordered by raw turn cost (MIN_TIME).  The priority-queue ordering and
        relaxation use this weighted cost, but the per-hop turn cost recorded
        for the response is always the real ``edge_turn_cost``.
        """
        if weight_fn is None:
            def weight_fn(_neighbour_sid, edge_turn_cost, _via_tunnel):  # noqa: E306
                return edge_turn_cost

        # dist tracks the *weighted* cost-to-reach used for Dijkstra ordering.
        dist: Dict[int, float] = {src: 0.0}
        prev: Dict[int, Optional[int]] = {src: None}
        prev_cost: Dict[int, int] = {src: 0}          # real turn cost of arriving edge
        prev_via_tunnel: Dict[int, bool] = {src: False}
        pq: List[Tuple[float, int]] = [(0.0, src)]

        while pq:
            cost, node = heapq.heappop(pq)

            if node == dst:
                break
            if cost > dist.get(node, math.inf):
                continue  # stale entry

            for (neighbour, edge_cost, via_tunnel) in graph.get(node, []):
                w = weight_fn(neighbour, edge_cost, via_tunnel)
                new_cost = cost + w
                if new_cost < dist.get(neighbour, math.inf):
                    dist[neighbour] = new_cost
                    prev[neighbour] = node
                    prev_cost[neighbour] = edge_cost
                    prev_via_tunnel[neighbour] = via_tunnel
                    heapq.heappush(pq, (new_cost, neighbour))

        if dst not in prev:
            return None, [], []

        # Reconstruct path
        path: List[int] = []
        node: Optional[int] = dst
        while node is not None:
            path.append(node)
            node = prev.get(node)
        path.reverse()  # src -> ... -> dst

        # Build per-hop costs and tunnel flags (skip index 0 = src)
        hop_costs = [prev_cost[path[i]] for i in range(1, len(path))]
        hop_via_tunnel = [prev_via_tunnel[path[i]] for i in range(1, len(path))]

        return path, hop_costs, hop_via_tunnel

    # ------------------------------------------------------------------
    # Nearest-known helpers
    # ------------------------------------------------------------------

    def _nearest_known_sector(
        self, target_sector_id: int, known_ids: Set[int]
    ) -> Optional[Dict]:
        """
        Nearest known sector by numeric distance to *target_sector_id* when the
        target doesn't exist in the db at all.  Falls back to returning the
        lowest-numbered known sector as a proxy (no coordinates available for
        a non-existent sector).
        """
        if not known_ids:
            return None
        # Pick the closest numeric id as a rough proxy
        closest_id = min(known_ids, key=lambda sid: abs(sid - target_sector_id))
        sec = (
            self.db.query(Sector)
            .filter(Sector.sector_id == closest_id)
            .first()
        )
        if sec is None:
            return None
        return {"sector_id": sec.sector_id, "name": sec.name}

    def _nearest_known_sector_euclidean(
        self, target_sector: Sector, known_ids: Set[int]
    ) -> Optional[Dict]:
        """
        Nearest known sector by 3D Euclidean distance to *target_sector*
        (using x_coord, y_coord, z_coord columns on Sector).
        """
        if not known_ids:
            return None

        tx = target_sector.x_coord or 0
        ty = target_sector.y_coord or 0
        tz = target_sector.z_coord or 0

        known_sectors = (
            self.db.query(Sector)
            .filter(Sector.sector_id.in_(known_ids))
            .all()
        )

        best_sec: Optional[Sector] = None
        best_dist = math.inf

        for sec in known_sectors:
            dx = (sec.x_coord or 0) - tx
            dy = (sec.y_coord or 0) - ty
            dz = (sec.z_coord or 0) - tz
            d = math.sqrt(dx * dx + dy * dy + dz * dz)
            if d < best_dist:
                best_dist = d
                best_sec = sec

        if best_sec is None:
            return None
        return {"sector_id": best_sec.sector_id, "name": best_sec.name}

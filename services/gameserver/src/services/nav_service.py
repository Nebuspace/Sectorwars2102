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
from dataclasses import dataclass, field
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


@dataclass(order=True)
class _PQEntry:
    """Priority-queue entry for Dijkstra (cost-ordered)."""

    cost: int
    sector_id: int = field(compare=False)


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

    def plot(self, player: Player, target_sector_id: int) -> Dict:
        """
        Compute a course from the player's current sector to *target_sector_id*.

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

        # Build the known-graph subset from the db
        graph, edge_meta = self._build_known_graph(known_ids)

        # Target must be in the known graph to be reachable
        if target_sector_id not in known_ids:
            nearest = self._nearest_known_sector_euclidean(target_sector, known_ids)
            return {
                "success": True,
                "reachable": False,
                "target_sector_id": target_sector_id,
                "nearest_known": nearest,
            }

        # Run Dijkstra over the known graph
        path_sids, costs, via_tunnel_flags = self._dijkstra(
            graph, edge_meta, start_sid, target_sector_id
        )

        if path_sids is None:
            # Target is in known_ids but not reachable through known graph
            # (could be a disconnected component)
            nearest = self._nearest_known_sector_euclidean(target_sector, known_ids)
            return {
                "success": True,
                "reachable": False,
                "target_sector_id": target_sector_id,
                "nearest_known": nearest,
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

        # Build set of sector UUIDs in the player's OWN exploration map
        own_visited_uuids: Set[str] = {
            str(row.sector_id)
            for row in self.db.query(ARIAExplorationMap.sector_id)
            .filter(ARIAExplorationMap.player_id == player.id)
            .all()
        }

        # Build safety-rating lookup: sector UUID -> safety_rating
        safety_map: Dict[str, Optional[float]] = {}
        own_exploration_rows = (
            self.db.query(ARIAExplorationMap)
            .filter(ARIAExplorationMap.player_id == player.id)
            .all()
        )
        for row in own_exploration_rows:
            safety_map[str(row.sector_id)] = row.safety_rating

        hops: List[Dict] = []
        for i, sid in enumerate(hops_sids):
            sec = sector_map.get(sid)
            if sec is None:
                # Defensive: skip sectors that disappeared between graph build and now
                logger.warning("nav_service: sector %d missing from db during hop assembly", sid)
                continue

            sec_uuid_str = str(sec.id)
            visited = sec_uuid_str in own_visited_uuids
            sr = safety_map.get(sec_uuid_str) if visited else None
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
    ) -> Tuple[Optional[List[int]], List[int], List[bool]]:
        """
        Standard Dijkstra returning (path_sids, per_hop_costs, per_hop_via_tunnel).

        path_sids includes src.  per_hop_costs[i] is the edge cost arriving at
        path_sids[i+1].  Returns (None, [], []) when dst is unreachable.
        """
        dist: Dict[int, int] = {src: 0}
        prev: Dict[int, Optional[int]] = {src: None}
        prev_cost: Dict[int, int] = {src: 0}
        prev_via_tunnel: Dict[int, bool] = {src: False}
        pq: List[_PQEntry] = [_PQEntry(cost=0, sector_id=src)]

        while pq:
            entry = heapq.heappop(pq)
            node = entry.sector_id
            cost = entry.cost

            if node == dst:
                break
            if cost > dist.get(node, math.inf):
                continue  # stale entry

            for (neighbour, edge_cost, via_tunnel) in graph.get(node, []):
                new_cost = cost + edge_cost
                if new_cost < dist.get(neighbour, math.inf):
                    dist[neighbour] = new_cost
                    prev[neighbour] = node
                    prev_cost[neighbour] = edge_cost
                    prev_via_tunnel[neighbour] = via_tunnel
                    heapq.heappush(pq, _PQEntry(cost=new_cost, sector_id=neighbour))

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

"""
Route Optimization Engine using Graph Algorithms

This module implements graph-based route optimization for the Sectorwars2102
game.  It builds a sector graph from warp tunnel connections and uses
Dijkstra's algorithm (via a priority queue) to find shortest / most-profitable
/ safest paths.  No external ML or SciPy dependencies are required.
"""

import logging
import heapq
import math
from typing import List, Dict, Any, Optional, Tuple, Set
from dataclasses import dataclass, field
from enum import Enum

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from src.models.sector import Sector, sector_warps
from src.models.station import Station
from src.models.warp_tunnel import WarpTunnel, WarpTunnelStatus

logger = logging.getLogger(__name__)


class RouteObjective(Enum):
    MAX_PROFIT = "max_profit"
    MIN_TIME = "min_time"
    MIN_RISK = "min_risk"
    BALANCED = "balanced"


@dataclass
class TradingOpportunity:
    """Represents a trading opportunity between two sectors."""
    from_sector_id: str
    to_sector_id: str
    commodity_id: str
    buy_price: float
    sell_price: float
    profit_per_unit: float
    max_quantity: int
    distance: int
    travel_time_hours: float
    risk_factor: float
    confidence: float


@dataclass
class OptimizedRoute:
    """Represents an optimized trading route."""
    sectors: List[str]
    opportunities: List[TradingOpportunity]
    total_profit: float
    total_distance: int
    total_time_hours: float
    total_risk: float
    cargo_efficiency: float
    profit_per_hour: float
    route_confidence: float
    route_type: str  # "direct", "linear", "circular", "hub_spoke"


@dataclass(order=True)
class _PQEntry:
    """Priority-queue entry for Dijkstra."""
    cost: float
    sector_id: str = field(compare=False)


# ------------------------------------------------------------------
# Graph edge: a connection between two sector_id integers
# ------------------------------------------------------------------
@dataclass
class _Edge:
    target_sector_id: int
    turn_cost: int
    hazard: int          # 0-10 sector hazard of the *target*
    stability: float     # warp tunnel stability 0.0-1.0


class RouteOptimizer:
    """
    Graph-based route optimizer.

    Builds an adjacency list from warp tunnel connections and the
    ``sector_warps`` association table, then applies Dijkstra's
    algorithm with configurable edge-weight functions to find
    optimal routes.
    """

    def __init__(self):
        # adjacency list: sector_id (int) -> list of _Edge
        self._graph: Dict[int, List[_Edge]] = {}
        # sector_id (int) -> Sector UUID str
        self._sid_to_uuid: Dict[int, str] = {}
        # sector_id (int) -> hazard_level
        self._hazard: Dict[int, int] = {}
        self._graph_built = False
        self.max_route_length = 10
        self.turn_cost_default = 1

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def find_optimal_route(
        self,
        db: AsyncSession,
        start_sector_id: str,
        player_id: str,
        cargo_capacity: int,
        max_route_time: float = 24.0,
        objective: RouteObjective = RouteObjective.MAX_PROFIT,
        risk_tolerance: float = 0.5,
    ) -> Optional[OptimizedRoute]:
        """
        Find the optimal trading route starting from *start_sector_id*.

        Parameters
        ----------
        start_sector_id : str
            Human-readable sector number **or** UUID string of the start sector.
        player_id : str
            The player requesting the route (for future personalisation).
        cargo_capacity : int
            How many units the player can carry.
        max_route_time : float
            Maximum hours for the route.
        objective : RouteObjective
            What to optimise for.
        risk_tolerance : float
            0.0 (safe) to 1.0 (risky).
        """
        try:
            if not self._graph_built:
                await self._build_graph(db)

            # Resolve start sector to integer sector_id
            start_sid = self._resolve_sector_id(start_sector_id)
            if start_sid is None or start_sid not in self._graph:
                logger.warning(f"Start sector {start_sector_id} not found in graph")
                return None

            # Gather trading opportunities across the map
            opportunities = await self._gather_trading_opportunities(
                db, start_sid, cargo_capacity, risk_tolerance
            )

            if not opportunities:
                logger.info("No trading opportunities found from this sector")
                return None

            # Build route depending on objective
            if objective == RouteObjective.MAX_PROFIT:
                route = self._build_profit_route(
                    start_sid, opportunities, cargo_capacity, max_route_time
                )
            elif objective == RouteObjective.MIN_TIME:
                route = self._build_time_route(
                    start_sid, opportunities, max_route_time
                )
            elif objective == RouteObjective.MIN_RISK:
                route = self._build_risk_route(
                    start_sid, opportunities, risk_tolerance, cargo_capacity
                )
            else:  # BALANCED
                route = self._build_balanced_route(
                    start_sid, opportunities, cargo_capacity,
                    max_route_time, risk_tolerance,
                )

            if route:
                route.route_confidence = self._route_confidence(route.opportunities)
                route.route_type = self._classify_route(route.sectors)

            return route

        except Exception as e:
            logger.error(f"Error finding optimal route: {e}")
            return None

    async def find_shortest_path(
        self,
        db: AsyncSession,
        from_sector_id: str,
        to_sector_id: str,
    ) -> Optional[List[int]]:
        """
        Find the shortest path (fewest warps) between two sectors.

        Returns a list of integer sector_ids forming the path, or ``None``
        if no path exists.
        """
        if not self._graph_built:
            await self._build_graph(db)

        src = self._resolve_sector_id(from_sector_id)
        dst = self._resolve_sector_id(to_sector_id)
        if src is None or dst is None:
            return None

        return self._dijkstra_path(src, dst, weight_fn=lambda e: e.turn_cost)

    async def find_arbitrage_opportunities(
        self,
        db: AsyncSession,
        player_sector_id: str,
        max_hops: int = 5,
        min_profit_margin: float = 0.10,
    ) -> List[TradingOpportunity]:
        """
        Find immediate arbitrage opportunities within *max_hops* jumps.
        """
        try:
            if not self._graph_built:
                await self._build_graph(db)

            start = self._resolve_sector_id(player_sector_id)
            if start is None:
                return []

            reachable = self._sectors_within_hops(start, max_hops)

            # Get stations in reachable sectors
            query = select(Station).where(
                Station.sector_id.in_(list(reachable)),
                Station.is_destroyed == False,  # noqa: E712
            )
            result = await db.execute(query)
            stations = result.scalars().all()

            return self._find_arbitrage_in_stations(
                stations, min_profit_margin, start
            )

        except Exception as e:
            logger.error(f"Error finding arbitrage: {e}")
            return []

    async def get_route_recommendations(
        self,
        db: AsyncSession,
        player_id: str,
        current_sector: str,
        player_preferences: Dict[str, Any],
    ) -> List[Dict[str, Any]]:
        """
        Return multiple route recommendations for different objectives.
        """
        cargo = player_preferences.get("cargo_capacity", 100)
        risk = player_preferences.get("risk_tolerance", 0.5)
        max_time = player_preferences.get("max_route_time", 12.0)

        recommendations = []
        for objective in [RouteObjective.MAX_PROFIT, RouteObjective.MIN_TIME, RouteObjective.BALANCED]:
            route = await self.find_optimal_route(
                db, current_sector, player_id, cargo, max_time, objective, risk
            )
            if route:
                recommendations.append({
                    "objective": objective.value,
                    "sectors": route.sectors,
                    "total_profit": route.total_profit,
                    "total_time": route.total_time_hours,
                    "total_distance": route.total_distance,
                    "profit_per_hour": route.profit_per_hour,
                    "risk_level": route.total_risk,
                    "confidence": route.route_confidence,
                    "route_type": route.route_type,
                    "description": self._describe_route(route, objective),
                    "opportunities": [
                        {
                            "from_sector": o.from_sector_id,
                            "to_sector": o.to_sector_id,
                            "commodity": o.commodity_id,
                            "buy_price": o.buy_price,
                            "sell_price": o.sell_price,
                            "profit_per_unit": o.profit_per_unit,
                            "max_quantity": o.max_quantity,
                            "confidence": o.confidence,
                        }
                        for o in route.opportunities
                    ],
                })

        recommendations.sort(
            key=lambda r: r["total_profit"] * r["confidence"], reverse=True
        )
        return recommendations

    # ------------------------------------------------------------------
    # Graph construction
    # ------------------------------------------------------------------

    async def _build_graph(self, db: AsyncSession) -> None:
        """
        Build the adjacency list from warp tunnels and sector_warps.
        """
        try:
            # 1. Load all sectors
            sectors_result = await db.execute(select(Sector))
            sectors = sectors_result.scalars().all()

            for s in sectors:
                sid = s.sector_id  # integer sector number
                self._graph.setdefault(sid, [])
                self._sid_to_uuid[sid] = str(s.id)
                self._hazard[sid] = s.hazard_level or 0

            # 2. Load warp tunnels (dedicated table)
            tunnels_result = await db.execute(
                select(WarpTunnel).where(
                    WarpTunnel.status == WarpTunnelStatus.ACTIVE
                )
            )
            tunnels = tunnels_result.scalars().all()

            for tunnel in tunnels:
                # Resolve origin/destination UUIDs to integer sector_ids
                origin_sid = self._uuid_to_sid(str(tunnel.origin_sector_id))
                dest_sid = self._uuid_to_sid(str(tunnel.destination_sector_id))
                if origin_sid is None or dest_sid is None:
                    continue

                turn_cost = tunnel.turn_cost or self.turn_cost_default
                stability = tunnel.stability if tunnel.stability is not None else 1.0

                self._graph[origin_sid].append(
                    _Edge(
                        target_sector_id=dest_sid,
                        turn_cost=turn_cost,
                        hazard=self._hazard.get(dest_sid, 0),
                        stability=stability,
                    )
                )

                if tunnel.is_bidirectional:
                    self._graph[dest_sid].append(
                        _Edge(
                            target_sector_id=origin_sid,
                            turn_cost=turn_cost,
                            hazard=self._hazard.get(origin_sid, 0),
                            stability=stability,
                        )
                    )

            # 3. Load sector_warps association table
            warps_result = await db.execute(select(sector_warps))
            warps = warps_result.fetchall()

            for warp in warps:
                src_uuid = str(warp.source_sector_id)
                dst_uuid = str(warp.destination_sector_id)
                src_sid = self._uuid_to_sid(src_uuid)
                dst_sid = self._uuid_to_sid(dst_uuid)
                if src_sid is None or dst_sid is None:
                    continue

                tc = warp.turn_cost if warp.turn_cost else self.turn_cost_default
                stab = warp.warp_stability if warp.warp_stability else 1.0

                self._graph[src_sid].append(
                    _Edge(
                        target_sector_id=dst_sid,
                        turn_cost=tc,
                        hazard=self._hazard.get(dst_sid, 0),
                        stability=stab,
                    )
                )
                if warp.is_bidirectional:
                    self._graph[dst_sid].append(
                        _Edge(
                            target_sector_id=src_sid,
                            turn_cost=tc,
                            hazard=self._hazard.get(src_sid, 0),
                            stability=stab,
                        )
                    )

            total_edges = sum(len(v) for v in self._graph.values())
            self._graph_built = True
            logger.info(
                f"Sector graph built: {len(self._graph)} sectors, {total_edges} edges"
            )

        except Exception as e:
            logger.error(f"Error building sector graph: {e}")
            self._graph_built = False

    # ------------------------------------------------------------------
    # Dijkstra's algorithm
    # ------------------------------------------------------------------

    def _dijkstra_path(
        self,
        src: int,
        dst: int,
        weight_fn=None,
    ) -> Optional[List[int]]:
        """
        Standard Dijkstra returning the shortest path as a list of sector_ids.

        *weight_fn* maps an ``_Edge`` to a numeric cost.  Defaults to turn_cost.
        """
        if weight_fn is None:
            weight_fn = lambda e: e.turn_cost  # noqa: E731

        dist: Dict[int, float] = {src: 0.0}
        prev: Dict[int, Optional[int]] = {src: None}
        pq: List[Tuple[float, int]] = [(0.0, src)]

        while pq:
            cost, node = heapq.heappop(pq)
            if node == dst:
                break
            if cost > dist.get(node, math.inf):
                continue

            for edge in self._graph.get(node, []):
                w = weight_fn(edge)
                new_cost = cost + w
                if new_cost < dist.get(edge.target_sector_id, math.inf):
                    dist[edge.target_sector_id] = new_cost
                    prev[edge.target_sector_id] = node
                    heapq.heappush(pq, (new_cost, edge.target_sector_id))

        if dst not in prev:
            return None

        # Reconstruct path
        path: List[int] = []
        current: Optional[int] = dst
        while current is not None:
            path.append(current)
            current = prev.get(current)
        path.reverse()
        return path

    def _dijkstra_distances(
        self,
        src: int,
        max_cost: float = math.inf,
        weight_fn=None,
    ) -> Dict[int, float]:
        """
        Return dict of {sector_id: cost} for all reachable sectors from *src*.
        """
        if weight_fn is None:
            weight_fn = lambda e: e.turn_cost  # noqa: E731

        dist: Dict[int, float] = {src: 0.0}
        pq: List[Tuple[float, int]] = [(0.0, src)]

        while pq:
            cost, node = heapq.heappop(pq)
            if cost > max_cost:
                break
            if cost > dist.get(node, math.inf):
                continue
            for edge in self._graph.get(node, []):
                w = weight_fn(edge)
                new_cost = cost + w
                if new_cost <= max_cost and new_cost < dist.get(edge.target_sector_id, math.inf):
                    dist[edge.target_sector_id] = new_cost
                    heapq.heappush(pq, (new_cost, edge.target_sector_id))

        return dist

    def _sectors_within_hops(self, start: int, max_hops: int) -> Set[int]:
        """BFS to find all sectors within *max_hops* jumps."""
        visited: Set[int] = {start}
        frontier: Set[int] = {start}

        for _ in range(max_hops):
            next_frontier: Set[int] = set()
            for sid in frontier:
                for edge in self._graph.get(sid, []):
                    if edge.target_sector_id not in visited:
                        visited.add(edge.target_sector_id)
                        next_frontier.add(edge.target_sector_id)
            frontier = next_frontier
            if not frontier:
                break

        return visited

    # ------------------------------------------------------------------
    # Trading opportunity discovery
    # ------------------------------------------------------------------

    async def _gather_trading_opportunities(
        self,
        db: AsyncSession,
        start_sid: int,
        cargo_capacity: int,
        risk_tolerance: float,
    ) -> List[TradingOpportunity]:
        """
        Gather trading opportunities from stations in sectors reachable
        from start_sid within a reasonable number of hops.
        """
        reachable = self._sectors_within_hops(start_sid, self.max_route_length)

        query = select(Station).where(
            Station.sector_id.in_(list(reachable)),
            Station.is_destroyed == False,  # noqa: E712
        )
        result = await db.execute(query)
        stations = result.scalars().all()

        return self._find_arbitrage_in_stations(stations, 0.05, start_sid)

    def _find_arbitrage_in_stations(
        self,
        stations: List[Any],
        min_margin: float,
        ref_sector: int,
    ) -> List[TradingOpportunity]:
        """
        Find profitable commodity trades between station pairs.
        """
        opportunities: List[TradingOpportunity] = []

        # Build per-commodity maps
        sellers: Dict[str, List[Tuple[Any, float, int]]] = {}  # commodity -> [(station, price, qty)]
        buyers: Dict[str, List[Tuple[Any, float, int]]] = {}

        for station in stations:
            if not station.commodities:
                continue
            for cname, cdata in station.commodities.items():
                price = cdata.get("current_price", cdata.get("base_price", 0))
                qty = cdata.get("quantity", 0)
                if price <= 0 or qty <= 0:
                    continue
                if cdata.get("sells", False):
                    sellers.setdefault(cname, []).append((station, float(price), qty))
                if cdata.get("buys", False):
                    buyers.setdefault(cname, []).append((station, float(price), qty))

        for commodity in set(sellers.keys()) & set(buyers.keys()):
            for sell_station, sell_price, sell_qty in sellers[commodity]:
                for buy_station, buy_price, buy_qty in buyers[commodity]:
                    if str(sell_station.id) == str(buy_station.id):
                        continue
                    profit = buy_price - sell_price
                    if sell_price <= 0:
                        continue
                    margin = profit / sell_price
                    if margin < min_margin:
                        continue

                    from_sid = sell_station.sector_id
                    to_sid = buy_station.sector_id

                    # Compute hop distance via graph
                    path = self._dijkstra_path(
                        from_sid, to_sid,
                        weight_fn=lambda e: e.turn_cost,
                    )
                    distance = len(path) - 1 if path else abs(from_sid - to_sid)
                    travel_time = distance * 0.5  # half-hour per hop

                    # Risk from sector hazard
                    dest_hazard = self._hazard.get(to_sid, 0)
                    risk_factor = dest_hazard / 10.0

                    # Confidence based on market volatility
                    vol = sell_station.market_volatility or 50
                    confidence = max(0.3, 1.0 - vol / 100.0)

                    max_qty = min(sell_qty, buy_qty)

                    opportunities.append(
                        TradingOpportunity(
                            from_sector_id=str(from_sid),
                            to_sector_id=str(to_sid),
                            commodity_id=commodity,
                            buy_price=sell_price,
                            sell_price=buy_price,
                            profit_per_unit=profit,
                            max_quantity=max_qty,
                            distance=distance,
                            travel_time_hours=travel_time,
                            risk_factor=risk_factor,
                            confidence=confidence,
                        )
                    )

        opportunities.sort(
            key=lambda o: o.profit_per_unit * o.max_quantity, reverse=True
        )
        return opportunities[:50]

    # ------------------------------------------------------------------
    # Route-building strategies
    # ------------------------------------------------------------------

    def _build_profit_route(
        self,
        start: int,
        opportunities: List[TradingOpportunity],
        cargo_capacity: int,
        max_time: float,
    ) -> Optional[OptimizedRoute]:
        """Greedy route maximising total profit."""
        return self._greedy_route(
            start, opportunities, cargo_capacity, max_time,
            score_fn=lambda o, cap: o.profit_per_unit * min(cap, o.max_quantity) * o.confidence,
        )

    def _build_time_route(
        self,
        start: int,
        opportunities: List[TradingOpportunity],
        max_time: float,
    ) -> Optional[OptimizedRoute]:
        """Route prioritising short travel time per profit unit."""
        return self._greedy_route(
            start, opportunities, 100, max_time,
            score_fn=lambda o, cap: (
                o.profit_per_unit * o.confidence / max(0.1, o.travel_time_hours)
            ),
            max_stops=3,
        )

    def _build_risk_route(
        self,
        start: int,
        opportunities: List[TradingOpportunity],
        risk_tolerance: float,
        cargo_capacity: int,
    ) -> Optional[OptimizedRoute]:
        """Route minimising risk while still profitable."""
        safe = [o for o in opportunities if o.risk_factor <= risk_tolerance]
        if not safe:
            return None
        return self._greedy_route(
            start, safe, cargo_capacity, 24.0,
            score_fn=lambda o, cap: (
                o.profit_per_unit * min(cap, o.max_quantity)
                * o.confidence * (1.0 - o.risk_factor)
            ),
        )

    def _build_balanced_route(
        self,
        start: int,
        opportunities: List[TradingOpportunity],
        cargo_capacity: int,
        max_time: float,
        risk_tolerance: float,
    ) -> Optional[OptimizedRoute]:
        """Balanced route weighing profit, time, and risk."""
        viable = [o for o in opportunities if o.risk_factor <= risk_tolerance + 0.1]
        if not viable:
            return None
        return self._greedy_route(
            start, viable, cargo_capacity, max_time,
            score_fn=lambda o, cap: (
                o.profit_per_unit * min(cap, o.max_quantity) * 0.4
                + (1.0 / max(0.1, o.travel_time_hours)) * 100 * 0.2
                + (1.0 - o.risk_factor) * 100 * 0.2
                + o.confidence * 100 * 0.2
            ),
        )

    def _greedy_route(
        self,
        start: int,
        opportunities: List[TradingOpportunity],
        cargo_capacity: int,
        max_time: float,
        score_fn=None,
        max_stops: int = None,
    ) -> Optional[OptimizedRoute]:
        """
        Build a route greedily: at each step pick the highest-scored
        opportunity reachable from the current sector.
        """
        if max_stops is None:
            max_stops = self.max_route_length

        current = start
        route_sectors = [str(current)]
        route_ops: List[TradingOpportunity] = []
        visited_pairs: Set[Tuple[str, str]] = set()
        total_time = 0.0

        for _ in range(max_stops):
            best_op = None
            best_score = -1.0

            for op in opportunities:
                pair = (op.from_sector_id, op.to_sector_id)
                if pair in visited_pairs:
                    continue
                if int(op.from_sector_id) != current:
                    continue
                if total_time + op.travel_time_hours > max_time:
                    continue

                score = score_fn(op, cargo_capacity) if score_fn else op.profit_per_unit
                if score > best_score:
                    best_score = score
                    best_op = op

            if best_op is None:
                break

            route_ops.append(best_op)
            route_sectors.append(best_op.to_sector_id)
            total_time += best_op.travel_time_hours
            current = int(best_op.to_sector_id)
            visited_pairs.add((best_op.from_sector_id, best_op.to_sector_id))

        if not route_ops:
            return None

        total_profit = sum(
            o.profit_per_unit * min(cargo_capacity, o.max_quantity)
            for o in route_ops
        )
        total_distance = sum(o.distance for o in route_ops)
        avg_risk = sum(o.risk_factor for o in route_ops) / len(route_ops)
        cargo_eff = len(route_ops) / max(1, len(route_sectors))

        return OptimizedRoute(
            sectors=route_sectors,
            opportunities=route_ops,
            total_profit=total_profit,
            total_distance=total_distance,
            total_time_hours=total_time,
            total_risk=avg_risk,
            cargo_efficiency=cargo_eff,
            profit_per_hour=total_profit / max(0.1, total_time),
            route_confidence=0.0,  # filled by caller
            route_type="",  # filled by caller
        )

    # ------------------------------------------------------------------
    # Utility helpers
    # ------------------------------------------------------------------

    def _resolve_sector_id(self, value: str) -> Optional[int]:
        """Convert a sector_id string (integer or UUID) to an int sector_id."""
        try:
            return int(value)
        except (ValueError, TypeError):
            pass
        # Try UUID lookup
        for sid, uuid_str in self._sid_to_uuid.items():
            if uuid_str == value:
                return sid
        return None

    def _uuid_to_sid(self, uuid_str: str) -> Optional[int]:
        """Reverse lookup: UUID -> integer sector_id."""
        for sid, u in self._sid_to_uuid.items():
            if u == uuid_str:
                return sid
        return None

    @staticmethod
    def _route_confidence(opportunities: List[TradingOpportunity]) -> float:
        if not opportunities:
            return 0.0
        return sum(o.confidence for o in opportunities) / len(opportunities)

    @staticmethod
    def _classify_route(sectors: List[str]) -> str:
        if len(sectors) < 3:
            return "direct"
        if sectors[0] == sectors[-1]:
            return "circular"
        unique_ratio = len(set(sectors)) / len(sectors)
        if unique_ratio < 0.7:
            return "hub_spoke"
        return "linear"

    @staticmethod
    def _describe_route(route: OptimizedRoute, objective: RouteObjective) -> str:
        n = len(route.sectors)
        if objective == RouteObjective.MAX_PROFIT:
            return f"High-profit {n}-sector route generating {route.total_profit:.0f} credits"
        elif objective == RouteObjective.MIN_TIME:
            return f"Quick {n}-sector route in {route.total_time_hours:.1f} hours"
        elif objective == RouteObjective.MIN_RISK:
            return f"Safe {n}-sector route with risk level {route.total_risk:.2f}"
        return f"Balanced {n}-sector route: {route.total_profit:.0f} credits in {route.total_time_hours:.1f}h"

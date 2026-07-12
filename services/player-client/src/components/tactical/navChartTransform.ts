import type { NavChartResponse } from '../../services/api';

/**
 * navChartTransform — pure transform from the deep `/nav/chart` graph
 * (WO-PUX-NAVCHART's full known-space response) into the scanner-bounded
 * node list NavigationMap's `sectors` prop consumes (WO-NAV-MULTIHOP-FEED).
 *
 * A player's known-space graph (visited ∪ corp-shared ∪ current) can grow
 * far larger than a single scanner sweep should render -- this bounds it to
 * a BFS neighborhood around the player's current sector, nearest-first, and
 * caps the absolute node count so the map never renders an unbounded graph.
 */

export interface NavNode {
  id: number;
  name: string;
  type?: string;
  connected_sectors: number[];
}

export interface NavChartTransformResult {
  sectors: NavNode[];
  frontierIds: number[];
  truncated: boolean;
}

const DEFAULT_SCANNER_RANGE = 12;
const MAX_SCANNER_RANGE = 12;
const NODE_CEILING = 150;

export function chartToNavSectors(
  chart: NavChartResponse,
  currentSectorId: number,
  scannerRange?: number
): NavChartTransformResult {
  // Defensive: NavChartResponse's shape is enforced by the TS type, not at
  // runtime -- a malformed/incomplete 200 response must not crash the map
  // (mirrors GalaxyMap.tsx's layoutKnownSectors:48-54).
  const chartSectors = chart?.sectors ?? [];
  const chartEdges = chart?.edges ?? [];
  const chartFrontier = chart?.frontier ?? [];
  if (!chartSectors.length) {
    return { sectors: [], frontierIds: [], truncated: false };
  }

  const depthCap = Math.min(MAX_SCANNER_RANGE, scannerRange ?? DEFAULT_SCANNER_RANGE);

  // Undirected adjacency among KNOWN sectors, built from chart.edges.
  // Undirected deliberately: chart.edges only records the traversal
  // direction of one-way warps/tunnels (nav_service.py add_edge), but a
  // known sector one hop away is still "nearby" for scanner-range/layout
  // purposes regardless of which direction the warp runs, and this mirrors
  // GameDashboard.tsx:874-905's own connected_sectors construction, which
  // is symmetric (a destination lists its current sector back) rather than
  // one-way. Also naturally dedupes: bidirectional edges already arrive as
  // two entries (`from<->to` both ways) and a sector reachable by both warp
  // and tunnel collapses to one adjacency entry (Set), mirroring the
  // dedupe intent of GalaxyMap.tsx's layoutKnownSectors.
  const sectorById = new Map(chartSectors.map((s) => [s.sector_id, s]));
  const adjacency = new Map<number, Set<number>>();
  const link = (a: number, b: number) => {
    if (!adjacency.has(a)) adjacency.set(a, new Set());
    adjacency.get(a)!.add(b);
  };
  for (const e of chartEdges) {
    if (!sectorById.has(e.from) || !sectorById.has(e.to)) continue; // defensive: unresolved endpoint
    link(e.from, e.to);
    link(e.to, e.from);
  }

  // BFS from currentSectorId, depth-capped at `depthCap` hops. Queue order
  // is a valid BFS order, so it is nearest-first by construction -- the
  // node-ceiling truncation below can simply slice it.
  const bfsOrder: number[] = [];
  const depthOf = new Map<number, number>();
  if (sectorById.has(currentSectorId)) {
    const queue: number[] = [currentSectorId];
    depthOf.set(currentSectorId, 0);
    for (let head = 0; head < queue.length; head++) {
      const id = queue[head];
      bfsOrder.push(id);
      const depth = depthOf.get(id)!;
      if (depth >= depthCap) continue;
      for (const neighbor of adjacency.get(id) ?? []) {
        if (depthOf.has(neighbor)) continue;
        depthOf.set(neighbor, depth + 1);
        queue.push(neighbor);
      }
    }
  }
  // Defensive: currentSectorId absent from chart.sectors (e.g. a stale
  // chart fetched just before/after a warp) -- fall back to the full known
  // set rather than rendering nothing; the node ceiling below still bounds
  // it, just without the nearest-first ordering a BFS would have given.
  const reachableIds = bfsOrder.length > 0 ? bfsOrder : chartSectors.map((s) => s.sector_id);

  let includedIds = reachableIds;
  let truncated = false;
  if (includedIds.length > NODE_CEILING) {
    truncated = true;
    // NO SILENT CAPS: a scanner-range/known-graph combination big enough to
    // truncate is exactly the case an operator needs visibility into.
    // eslint-disable-next-line no-console
    console.info(
      `chartToNavSectors: truncating ${includedIds.length} known sectors to ${NODE_CEILING} (nearest-first)`
    );
    includedIds = includedIds.slice(0, NODE_CEILING);
  }
  const includedSet = new Set(includedIds);

  const sectors: NavNode[] = includedIds.map((id) => {
    const s = sectorById.get(id)!;
    const connected = Array.from(adjacency.get(id) ?? []).filter((n) => includedSet.has(n));
    return {
      id: s.sector_id,
      name: s.name,
      type: s.type,
      connected_sectors: connected,
    };
  });

  // Frontier stubs: nav_service.get_chart (services/gameserver/src/services/
  // nav_service.py:224-259) guarantees every chart.frontier id is exactly
  // one hop from SOME known sector -- frontier_ids is populated only from
  // warp/tunnel rows whose source is a known sector. But it does NOT record
  // *which* known sector surfaced it: chart.edges only carries known<->known
  // links (add_edge is only called when the destination is itself known);
  // a known->frontier warp/tunnel row is folded into frontier_ids and
  // never becomes an edge. So there is no linkage in the /nav/chart
  // contract to attach a given frontier id to a specific known neighbor
  // (see report -- WO's "link to the known neighbor that surfaced it"
  // fallback branch is unreachable with the data this endpoint returns).
  //
  // The one case we CAN prove safe without that linkage: when the BFS+cap
  // above included the ENTIRE known graph (no sector was excluded by depth
  // or the node ceiling), nav_service's guarantee means every frontier id
  // is one hop from an INCLUDED known sector, even though we can't name
  // which one -- so it's safe to surface all of them (with an empty
  // connected_sectors, since the specific link is genuinely unknown). When
  // some known sectors were excluded, a given frontier id might only be
  // reachable via an excluded sector, and we cannot tell which case we're
  // in -- so frontier stubs are skipped entirely rather than fabricating a
  // link or risking a stub floating in the wrong part of the map ("skip
  // unlinkable stubs gracefully").
  const frontierIds: number[] = [];
  const allKnownIncluded = includedSet.size === chartSectors.length;
  if (allKnownIncluded) {
    for (const frontierId of chartFrontier) {
      if (includedSet.has(frontierId)) continue; // defensive: shouldn't happen
      sectors.push({
        id: frontierId,
        name: `Sector ${frontierId}`,
        type: 'frontier',
        connected_sectors: [],
      });
      frontierIds.push(frontierId);
    }
  }

  return { sectors, frontierIds, truncated };
}

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
  /** BFS hop-distance from the current sector (0 = current). Drives
   * NavigationMap's neon depth-tier styling (WO-NAV-CHART-POLISH sub-part
   * a). A frontier stub's depth is its source sector's depth + 1. */
  depth: number;
}

export interface NavChartTransformResult {
  sectors: NavNode[];
  frontierIds: number[];
  truncated: boolean;
  /** Known->known directed edges with no reverse counterpart in
   * chart.edges -- i.e. genuinely one-way warps/tunnels, filtered to pairs
   * where both endpoints survived the BFS+cap. Drives NavigationMap's
   * one-way arrow rendering (WO-NAV-CHART-POLISH sub-part c). */
  oneWayEdges: { from: number; to: number }[];
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
    return { sectors: [], frontierIds: [], truncated: false, oneWayEdges: [] };
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

  // Directed edges among rendered sectors, for one-way-warp arrow rendering
  // (WO-NAV-CHART-POLISH sub-part c). The adjacency built above is
  // deliberately undirected (a known sector one hop away is "nearby" for
  // layout purposes regardless of warp direction, per the comment on
  // `link` above) -- this is a SEPARATE pass over the raw directed
  // chart.edges that preserves direction, mirroring GalaxyMap.tsx's
  // layoutKnownSectors `isOneWay` technique (reverse-key lookup: a pair is
  // one-way when only ONE direction appears in chart.edges). `kind` is
  // ignored for this check (a from/to pair with a one-way warp AND a
  // one-way tunnel running opposite directions would read as bidirectional
  // -- an acceptable simplification; that configuration is rare and the
  // failure mode is merely "no arrow drawn", not a wrong one).
  const rawEdgeKeys = new Set(chartEdges.map((e) => `${e.from}>${e.to}`));
  const oneWaySeen = new Set<string>();
  const oneWayEdges: { from: number; to: number }[] = [];
  for (const e of chartEdges) {
    if (!includedSet.has(e.from) || !includedSet.has(e.to)) continue;
    if (rawEdgeKeys.has(`${e.to}>${e.from}`)) continue; // reverse exists -- bidirectional
    const key = `${e.from}>${e.to}`;
    if (oneWaySeen.has(key)) continue;
    oneWaySeen.add(key);
    oneWayEdges.push({ from: e.from, to: e.to });
  }

  const sectors: NavNode[] = includedIds.map((id) => {
    const s = sectorById.get(id)!;
    const connected = Array.from(adjacency.get(id) ?? []).filter((n) => includedSet.has(n));
    return {
      id: s.sector_id,
      name: s.name,
      type: s.type,
      connected_sectors: connected,
      depth: depthOf.get(id) ?? 0,
    };
  });

  // Frontier stubs: nav_service.get_chart (services/gameserver/src/services/
  // nav_service.py) guarantees every chart.frontier entry is exactly one hop
  // from the known sector named by its `from` field (WO-NAV-CHART-FRONTIER-
  // EDGES). WO-NAV-CHART-POLISH turns frontier RENDERING on and upgrades
  // inclusion from the old "was the entire known graph included" heuristic
  // to a per-stub check: a stub is included whenever ITS OWN `from` sector
  // survived the BFS+cap above, regardless of whether some UNRELATED known
  // sector was excluded elsewhere in the graph -- strictly more precise
  // than the old all-or-nothing gate (which skipped every stub the moment
  // truncation touched ANY sector, even ones with no bearing on that
  // stub's linkage). A stub whose `from` was itself excluded is still
  // skipped -- there is no source sector to honestly attach it to.
  //
  // connected_sectors: [stub.from] gives the stub a ONE-DIRECTIONAL spring
  // in NavigationMap's force layout (pulled toward its source; the source
  // is not pulled back -- see the sim's own per-node connections loop), so
  // it visually orbits near the known sector that surfaced it without
  // perturbing the rest of the graph. depth is the source's own depth + 1,
  // continuing the BFS depth gradient one hop past the known frontier.
  const frontierIds: number[] = [];
  for (const stub of chartFrontier) {
    if (includedSet.has(stub.id)) continue; // defensive: shouldn't happen
    if (!includedSet.has(stub.from)) continue; // unlinkable -- source excluded
    sectors.push({
      id: stub.id,
      name: `Sector ${stub.id}`,
      type: 'frontier',
      connected_sectors: [stub.from],
      depth: (depthOf.get(stub.from) ?? 0) + 1,
    });
    frontierIds.push(stub.id);
  }

  return { sectors, frontierIds, truncated, oneWayEdges };
}

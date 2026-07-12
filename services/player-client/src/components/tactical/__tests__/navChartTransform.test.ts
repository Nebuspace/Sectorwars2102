/**
 * navChartTransform — pure unit tests (WO-NAV-MULTIHOP-FEED sub-part a).
 * No DOM needed (vitest.config.ts default environment: 'node').
 */
import { describe, it, expect, vi, afterEach } from 'vitest';
import { chartToNavSectors } from '../navChartTransform';
import type { NavChartResponse, NavChartSector, NavChartEdge, NavChartFrontier } from '../../../services/api';

function sector(id: number, overrides: Partial<NavChartSector> = {}): NavChartSector {
  return {
    sector_id: id,
    name: `Sector ${id}`,
    type: 'normal',
    x: id,
    y: 0,
    z: 0,
    visited: true,
    current: false,
    ...overrides,
  };
}

function chart(
  sectors: NavChartSector[],
  edges: NavChartEdge[],
  frontier: NavChartFrontier[] = []
): NavChartResponse {
  return { sectors, edges, frontier };
}

afterEach(() => {
  vi.restoreAllMocks();
});

describe('chartToNavSectors', () => {
  it('dedupes a bidirectional edge into a single connection on each side', () => {
    const c = chart(
      [sector(1, { current: true }), sector(2)],
      [
        { from: 1, to: 2, kind: 'warp' },
        { from: 2, to: 1, kind: 'warp' },
      ]
    );
    const result = chartToNavSectors(c, 1);
    const byId = new Map(result.sectors.map((s) => [s.id, s]));
    expect(byId.get(1)?.connected_sectors).toEqual([2]);
    expect(byId.get(2)?.connected_sectors).toEqual([1]);
  });

  it('handles a one-way edge by treating connectivity as undirected (no crash, symmetric adjacency)', () => {
    const c = chart(
      [sector(1, { current: true }), sector(2)],
      [{ from: 1, to: 2, kind: 'warp' }] // no reverse entry -- one-way
    );
    const result = chartToNavSectors(c, 1);
    const byId = new Map(result.sectors.map((s) => [s.id, s]));
    expect(byId.get(1)?.connected_sectors).toEqual([2]);
    expect(byId.get(2)?.connected_sectors).toEqual([1]);
  });

  it('excludes a known sector beyond the depth cap', () => {
    // Linear chain 1-2-3-4-5-6, current = 1.
    const sectors = [1, 2, 3, 4, 5, 6].map((id) => sector(id, { current: id === 1 }));
    const edges: NavChartEdge[] = [];
    for (let i = 1; i < 6; i++) edges.push({ from: i, to: i + 1, kind: 'warp' });
    const c = chart(sectors, edges);

    const result = chartToNavSectors(c, 1, 2); // depth cap 2 -> reaches 1,2,3 only
    const ids = result.sectors.map((s) => s.id).sort((a, b) => a - b);
    expect(ids).toEqual([1, 2, 3]);
  });

  it('caps known-node count at 150, marks truncated, and logs a one-line notice with the pre-truncation count', () => {
    // Star graph: current sector 1 directly connected to 200 neighbors, all
    // at depth 1 -- well within the default depth cap, so only the node
    // ceiling truncates.
    const NEIGHBOR_COUNT = 200;
    const sectors = [sector(1, { current: true })];
    const edges: NavChartEdge[] = [];
    for (let i = 2; i <= NEIGHBOR_COUNT + 1; i++) {
      sectors.push(sector(i));
      edges.push({ from: 1, to: i, kind: 'warp' });
    }
    const c = chart(sectors, edges);
    const infoSpy = vi.spyOn(console, 'info').mockImplementation(() => {});

    const result = chartToNavSectors(c, 1);

    expect(result.sectors).toHaveLength(150);
    expect(result.truncated).toBe(true);
    expect(infoSpy).toHaveBeenCalledTimes(1);
    expect(infoSpy.mock.calls[0][0]).toContain(String(NEIGHBOR_COUNT + 1)); // pre-truncation count (201)
    expect(infoSpy.mock.calls[0][0]).toContain('150');
  });

  it('includes a frontier id as a stub node with type "frontier" and lists it in frontierIds', () => {
    const c = chart(
      [sector(1, { current: true }), sector(2)],
      [{ from: 1, to: 2, kind: 'warp' }],
      [{ id: 99, from: 2 }]
    );
    const result = chartToNavSectors(c, 1);

    const stub = result.sectors.find((s) => s.id === 99);
    expect(stub).toBeDefined();
    expect(stub?.type).toBe('frontier');
    expect(stub?.connected_sectors).toEqual([]);
    expect(result.frontierIds).toEqual([99]);
  });

  it('skips frontier stubs when the depth cap excluded part of the known graph (unlinkable)', () => {
    // Chain 1-2-3, current=1, depth cap 1 excludes sector 3 -- the known
    // graph is no longer complete, so a frontier id can't be proven to
    // hang off an included sector and must be skipped.
    const c = chart(
      [sector(1, { current: true }), sector(2), sector(3)],
      [
        { from: 1, to: 2, kind: 'warp' },
        { from: 2, to: 3, kind: 'warp' },
      ],
      [{ id: 99, from: 3 }]
    );
    const result = chartToNavSectors(c, 1, 1);
    expect(result.sectors.some((s) => s.type === 'frontier')).toBe(false);
    expect(result.frontierIds).toEqual([]);
  });

  it('returns an empty result without throwing for empty/malformed charts', () => {
    expect(chartToNavSectors({ sectors: [], edges: [], frontier: [] }, 1)).toEqual({
      sectors: [],
      frontierIds: [],
      truncated: false,
    });
    expect(chartToNavSectors(null as unknown as NavChartResponse, 1)).toEqual({
      sectors: [],
      frontierIds: [],
      truncated: false,
    });
    expect(chartToNavSectors(undefined as unknown as NavChartResponse, 1)).toEqual({
      sectors: [],
      frontierIds: [],
      truncated: false,
    });
    expect(
      chartToNavSectors({ sectors: undefined, edges: undefined, frontier: undefined } as unknown as NavChartResponse, 1)
    ).toEqual({ sectors: [], frontierIds: [], truncated: false });
  });

  it('respects scannerRange -- a smaller range yields a smaller node set than a larger one', () => {
    // Deep chain 1..15, current = 1.
    const sectors = Array.from({ length: 15 }, (_, i) => sector(i + 1, { current: i === 0 }));
    const edges: NavChartEdge[] = [];
    for (let i = 1; i < 15; i++) edges.push({ from: i, to: i + 1, kind: 'warp' });
    const c = chart(sectors, edges);

    const narrow = chartToNavSectors(c, 1, 2);
    const wide = chartToNavSectors(c, 1, 12);

    expect(narrow.sectors.map((s) => s.id).sort((a, b) => a - b)).toEqual([1, 2, 3]);
    expect(wide.sectors).toHaveLength(13); // depth cap 12 -> ids 1..13
    expect(narrow.sectors.length).toBeLessThan(wide.sectors.length);
  });
});

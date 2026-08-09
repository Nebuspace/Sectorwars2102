/**
 * nav3dFog — exits-only filter + galaxy→scene scale.
 */
import { describe, expect, it } from 'vitest';
import {
  LOCAL_CLUSTER_RADIUS,
  filterNav3DNodes,
  galaxyToSceneScale,
} from '../nav3dFog';

const node = (
  sector_id: number,
  knowledge: 'current' | 'reachable' | 'visited' | 'known',
  xyz: [number, number, number] = [0, 0, 0],
) => ({
  sector_id,
  knowledge,
  x: xyz[0],
  y: xyz[1],
  z: xyz[2],
});

describe('nav3dFog', () => {
  it('exports the local cluster radius constant', () => {
    expect(LOCAL_CLUSTER_RADIUS).toBe(42);
  });

  it('returns all nodes when exitsOnly is false', () => {
    const nodes = [node(1, 'current'), node(2, 'visited'), node(3, 'reachable')];
    expect(filterNav3DNodes(nodes, false)).toEqual(nodes);
  });

  it('keeps current/reachable and course hops in exits-only mode', () => {
    const nodes = [
      node(1, 'current'),
      node(2, 'reachable'),
      node(3, 'visited'),
      node(4, 'visited'),
    ];
    const filtered = filterNav3DNodes(nodes, true, new Set([4]));
    expect(filtered.map((n) => n.sector_id)).toEqual([1, 2, 4]);
  });

  it('scales from local cluster extent with a floor', () => {
    const origin = { x: 0, y: 0, z: 0 };
    // Tight local cluster → floor at 120 galaxy units
    const tight = [
      node(1, 'current', [0, 0, 0]),
      node(2, 'reachable', [10, 0, 0]),
    ];
    expect(galaxyToSceneScale(tight, origin)).toBeCloseTo(42 / 120);

    // Wide local cluster
    const wide = [
      node(1, 'current', [0, 0, 0]),
      node(2, 'reachable', [240, 0, 0]),
    ];
    expect(galaxyToSceneScale(wide, origin)).toBeCloseTo(42 / 240);
  });

  it('falls back to full known span when local extent is ~0', () => {
    const origin = { x: 0, y: 0, z: 0 };
    const nodes = [
      node(1, 'current', [0, 0, 0]),
      node(2, 'visited', [300, 0, 0]),
    ];
    expect(galaxyToSceneScale(nodes, origin)).toBeCloseTo(42 / 300);
  });
});

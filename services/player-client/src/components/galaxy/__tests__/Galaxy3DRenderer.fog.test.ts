import { describe, expect, it } from 'vitest';
import {
  filterNav3DNodes,
  galaxyToSceneScale,
} from '../nav3dFog';
import type { SectorKnowledge } from '../SectorNode3D';

type Node = {
  sector_id: number;
  x: number;
  y: number;
  z: number;
  knowledge: SectorKnowledge;
};

/** Shouden @ Stardock (#10) — subset of live unbounded chart coords. */
const SHOUDEN_NODES: Node[] = [
  { sector_id: 10, x: 277, y: -604, z: 102, knowledge: 'current' },
  { sector_id: 11, x: 63, y: -666, z: 90, knowledge: 'reachable' },
  { sector_id: 12, x: 393, y: -584, z: -157, knowledge: 'reachable' },
  { sector_id: 4, x: 221, y: -453, z: 4, knowledge: 'visited' },
  { sector_id: 1, x: 0, y: 0, z: 0, knowledge: 'visited' },
  { sector_id: 19, x: 783, y: -496, z: -200, knowledge: 'visited' },
  { sector_id: 39, x: 700, y: -293, z: -890, knowledge: 'visited' },
];

describe('filterNav3DNodes', () => {
  it('Show all keeps the visited fog trail', () => {
    const out = filterNav3DNodes(SHOUDEN_NODES, false);
    expect(out.map((n) => n.sector_id).sort((a, b) => a - b)).toEqual(
      [1, 4, 10, 11, 12, 19, 39],
    );
  });

  it('Exits only hides visited non-adjacent fog', () => {
    const out = filterNav3DNodes(SHOUDEN_NODES, true);
    expect(out.map((n) => n.sector_id).sort((a, b) => a - b)).toEqual(
      [10, 11, 12],
    );
  });

  it('Exits only keeps plotted course hops even if not adjacent', () => {
    const out = filterNav3DNodes(SHOUDEN_NODES, true, new Set([19]));
    expect(out.map((n) => n.sector_id).sort((a, b) => a - b)).toEqual(
      [10, 11, 12, 19],
    );
  });
});

describe('galaxyToSceneScale', () => {
  const origin = { x: 277, y: -604, z: 102 };

  it('scales from local cluster so fog sits farther out than exits', () => {
    const scale = galaxyToSceneScale(SHOUDEN_NODES, origin);
    const sceneDist = (n: Node) =>
      Math.hypot(n.x - origin.x, n.y - origin.y, n.z - origin.z) * scale;

    const exitDist = Math.max(
      sceneDist(SHOUDEN_NODES.find((n) => n.sector_id === 11)!),
      sceneDist(SHOUDEN_NODES.find((n) => n.sector_id === 12)!),
    );
    const fogDist = sceneDist(SHOUDEN_NODES.find((n) => n.sector_id === 1)!);

    // Local exits fit near the cluster radius; Terra fog is meaningfully farther.
    expect(exitDist).toBeLessThanOrEqual(42.01);
    expect(fogDist).toBeGreaterThan(exitDist * 1.5);
  });

  it('does not compress the whole trail into the local cluster radius', () => {
    const scale = galaxyToSceneScale(SHOUDEN_NODES, origin);
    const terra = SHOUDEN_NODES.find((n) => n.sector_id === 1)!;
    const terraScene = Math.hypot(
      terra.x - origin.x,
      terra.y - origin.y,
      terra.z - origin.z,
    ) * scale;
    // Old fit-everything-into-48 put Terra inside ~48; fog must extend past that.
    expect(terraScene).toBeGreaterThan(48);
  });
});

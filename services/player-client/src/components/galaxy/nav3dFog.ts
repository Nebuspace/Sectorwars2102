import type { SectorKnowledge } from './SectorNode3D';

/**
 * Scene radius for the *local* cluster (current + warp-now exits) after
 * scale. Visited fog keeps the same scale and therefore sits farther out in
 * real relative positions — an exposed trail, not a fit-everything blob.
 */
export const LOCAL_CLUSTER_RADIUS = 42;

/**
 * Pure filter for the Exits-only / Show-all toggle.
 * Exits-only = current + warp-now adjacent (+ any plotted course hops).
 * Show-all = full known chart including visited fog trail.
 */
export function filterNav3DNodes<T extends { sector_id: number; knowledge: SectorKnowledge }>(
  nodes: T[],
  exitsOnly: boolean,
  courseHopIds?: Set<number>,
): T[] {
  if (!exitsOnly) return nodes;
  const course = courseHopIds ?? new Set<number>();
  return nodes.filter(
    (n) =>
      n.knowledge === 'current'
      || n.knowledge === 'reachable'
      || course.has(n.sector_id),
  );
}

/**
 * Scale from galaxy coords → scene units using the local cluster's extent
 * so adjacent warps stay readable. Visited fog uses the same scale and
 * extends outward (fog of war), instead of compressing the whole trail
 * into one blob with the exits.
 */
export function galaxyToSceneScale(
  nodes: { sector_id: number; x: number; y: number; z: number; knowledge: SectorKnowledge }[],
  origin: { x: number; y: number; z: number },
  localClusterRadius: number = LOCAL_CLUSTER_RADIUS,
): number {
  const spanOf = (
    sample: { x: number; y: number; z: number }[],
  ): number => {
    let maxDist = 0;
    for (const n of sample) {
      const d = Math.hypot(n.x - origin.x, n.y - origin.y, n.z - origin.z);
      if (d > maxDist) maxDist = d;
    }
    return maxDist;
  };

  const local = nodes.filter(
    (n) => n.knowledge === 'current' || n.knowledge === 'reachable',
  );
  let maxDist = spanOf(local.length > 0 ? local : nodes);
  // Current-only (no exits yet) → fall back to full known span so fog
  // doesn't explode to infinity from a ~0 local radius.
  if (maxDist < 1e-3) maxDist = spanOf(nodes);
  // Floor: treat very tight clusters as at least ~120 galaxy units across
  // so scale stays finite and readable.
  return localClusterRadius / Math.max(maxDist, 120);
}

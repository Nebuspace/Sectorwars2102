/**
 * Vista Engine — Feature & hazard placement helpers
 *
 * Pure functions driving pipeline stages 9 (features) and 10 (hazards).
 * All randomness is drawn from SeededRng instances passed in from the SeedBus.
 * Every function draws a fixed, predictable count of values per logical unit so
 * the pipeline's determinism guarantee holds regardless of acceptance outcomes.
 *
 * TRUTHFULNESS (BRIEF §2.5): placeHazardOverlays emits an overlay for EVERY
 * hazard in site.hazards, unconditionally.  Desirability never suppresses a
 * hazard visual — a beautiful, high-richness world still carries its scars.
 *
 * No DOM, no Math.random(), no module-level mutable state.
 * Same SeededRng state + same arguments → byte-identical output.
 */

import { VistaInput, VistaModel, RGB, EnergySource } from '../contract';
import { SeededRng } from './rng';

// ---------------------------------------------------------------------------
// Internal math helpers
// ---------------------------------------------------------------------------

function lerp(a: number, b: number, t: number): number {
  return a + (b - a) * t;
}

function lerpRgb(a: RGB, b: RGB, t: number): RGB {
  return [
    Math.round(lerp(a[0], b[0], t)),
    Math.round(lerp(a[1], b[1], t)),
    Math.round(lerp(a[2], b[2], t)),
  ];
}

function clamp(v: number, lo: number, hi: number): number {
  return v < lo ? lo : v > hi ? hi : v;
}

function clamp01(v: number): number {
  return clamp(v, 0, 1);
}

// ---------------------------------------------------------------------------
// Poisson-disk scatter
// ---------------------------------------------------------------------------

/**
 * Candidate positions tried per desired point.
 * Always consumes exactly POISSON_CANDIDATES × 2 float draws per point,
 * regardless of whether the point is accepted, so the caller's downstream
 * draw sequence is unaffected by region density.
 */
const POISSON_CANDIDATES = 20;

/**
 * Deterministic Poisson-disk scatter: up to maxPoints non-overlapping points
 * in the normalized box [x0..x1] × [y0..y1] with separation ≥ minDist.
 *
 * Uses dart-throwing with bounded candidates (Bridson 2007, §2).
 *
 * RNG contract: each of the maxPoints iterations ALWAYS draws exactly
 * POISSON_CANDIDATES × 2 float values from `rng`, whether or not a point
 * is accepted.  Total consumption = maxPoints × POISSON_CANDIDATES × 2.
 * This is what makes Poisson-disk safe to use inside a named SeedBus stream
 * without disturbing any other stream's sequence.
 *
 * May return fewer than maxPoints points when the region is saturated.
 * Never throws — degrade gracefully.
 */
export function poissonDiskScatter(
  rng: SeededRng,
  maxPoints: number,
  x0: number,
  y0: number,
  x1: number,
  y1: number,
  minDist: number,
): [number, number][] {
  const points: [number, number][] = [];
  const w        = x1 - x0;
  const h        = y1 - y0;
  const minDist2 = minDist * minDist;

  for (let p = 0; p < maxPoints; p++) {
    let accepted = false;

    for (let k = 0; k < POISSON_CANDIDATES; k++) {
      // Always draw x + y, regardless of whether we've already accepted a point.
      // Remaining draws after acceptance are consumed silently to keep the
      // per-point total fixed at POISSON_CANDIDATES × 2.
      const cx = x0 + rng.next01() * w;
      const cy = y0 + rng.next01() * h;

      if (!accepted) {
        let ok = true;
        for (let j = 0; j < points.length; j++) {
          const dx = cx - points[j][0];
          const dy = cy - points[j][1];
          if (dx * dx + dy * dy < minDist2) { ok = false; break; }
        }
        if (ok) {
          points.push([cx, cy]);
          accepted = true;
        }
      }
    }
    // Exactly POISSON_CANDIDATES × 2 draws consumed for this point, placed or not.
  }

  return points;
}

// ---------------------------------------------------------------------------
// Scatter instance builder (shared by flora, rocks, glitter)
// ---------------------------------------------------------------------------

/**
 * Build one scatter group from pre-computed Poisson positions.
 *
 * RNG contract per instance:
 *   always draws 2 floats (scale, tint-mix);
 *   draws 1 additional float for glow variance when glowBase is defined.
 */
function buildScatterKind(
  rng: SeededRng,
  kind: string,
  positions: [number, number][],
  baseColor: RGB,
  highlightColor: RGB,
  glowBase?: number,
): VistaModel['layers']['features']['scatters'][number] {
  const instances: VistaModel['layers']['features']['scatters'][number]['instances'] = [];
  for (const pos of positions) {
    const scale   = 0.018 + rng.next01() * 0.040;
    const tintMix = rng.next01() * 0.22;
    const tint    = lerpRgb(baseColor, highlightColor, tintMix);
    if (glowBase !== undefined) {
      instances.push({
        pos,
        scale,
        tint,
        glow: clamp01(glowBase * (0.5 + rng.next01() * 0.5)),
      });
    } else {
      instances.push({ pos, scale, tint });
    }
  }
  return { kind, instances };
}

// ---------------------------------------------------------------------------
// Flora + rock scatters  (pipeline stage 9a)
// ---------------------------------------------------------------------------

/**
 * Place flora scatters on the ground plane using Poisson-disk spacing.
 *
 * Density is driven by both habitability (primary) and desirability (beauty
 * budget), letting the lab's habitability slider alone change the scene visibly:
 *   - Primary count cap: lerp(8, 22, desirability) — lush worlds hold more flora
 *   - Second flora variety: unlocked at hab > 0.55 AND desirability > 0.45
 *   - Glitter / sparkle accent: emitted at desirability > 0.55
 *
 * Glitter uses the 'glitter-spark' scatter kind with additive glow set to
 * palette.accent, so each planet type's energy signature shows through.
 */
export function placeFloraScatters(
  rng: SeededRng,
  floraKinds: readonly string[],
  palette: VistaModel['palette'],
  horizonY: number,
  hab01: number,
  desirability: number,
): VistaModel['layers']['features']['scatters'] {
  const scatters: VistaModel['layers']['features']['scatters'] = [];
  if (floraKinds.length === 0) return scatters;

  const white: RGB    = [255, 255, 255];
  const groundY1      = horizonY + (1 - horizonY) * 0.85;

  // Primary flora: count cap scales with desirability (more lush at high beauty).
  const maxPrimary   = Math.round(lerp(8, 22, desirability));
  const primaryCount = Math.round(lerp(0, maxPrimary, hab01));
  if (primaryCount > 0) {
    const kind    = rng.pick(floraKinds);
    // Tighter minimum spacing on high-desirability worlds (denser carpet).
    const minDist = lerp(0.07, 0.04, desirability);
    const pos     = poissonDiskScatter(rng, primaryCount, 0.02, horizonY, 0.98, groundY1, minDist);
    scatters.push(buildScatterKind(rng, kind, pos, palette.flora, white));
  }

  // Second flora variety: moderate-to-high hab + meaningful desirability unlocks it.
  if (floraKinds.length > 1 && hab01 > 0.55 && desirability > 0.45) {
    const secondKind  = rng.pick(floraKinds);
    const maxSecond   = Math.round(lerp(3, 10, desirability));
    const secondCount = Math.round(lerp(0, maxSecond, (hab01 - 0.55) / 0.45));
    if (secondCount > 0) {
      const groundY1b = horizonY + (1 - horizonY) * 0.80;
      const pos = poissonDiskScatter(rng, secondCount, 0.05, horizonY, 0.95, groundY1b, 0.06);
      scatters.push(buildScatterKind(rng, secondKind, pos, palette.flora, white));
    }
  }

  // Glitter / sparkle accent: a visual beauty-budget signal at high desirability.
  // Uses palette.accent as the base so energy signatures show (thermal glow,
  // tidal surf, solar flash, wind shimmer).
  if (desirability > 0.55) {
    const glitterCount = Math.round(lerp(0, 8, (desirability - 0.55) / 0.45));
    if (glitterCount > 0) {
      const glitterY1 = horizonY + (1 - horizonY) * 0.60;
      const pos       = poissonDiskScatter(rng, glitterCount, 0.03, horizonY, 0.97, glitterY1, 0.08);
      const glowBase  = clamp01(desirability * 0.9);
      const accentHi: RGB = [255, 255, 200];
      scatters.push(buildScatterKind(rng, 'glitter-spark', pos, palette.accent, accentHi, glowBase));
    }
  }

  return scatters;
}

/**
 * Place rock / non-flora scatter on the ground plane.
 * Count (2–7) and kind are seeded; rocks are always present on rocky profiles.
 * RNG contract: 1 (pick) + 1 (count) + count × POISSON_CANDIDATES × 2 (placement)
 * + placed × 2 (instance attrs) draws total.
 */
export function placeRockScatters(
  rng: SeededRng,
  rockKinds: readonly string[],
  palette: VistaModel['palette'],
  horizonY: number,
): VistaModel['layers']['features']['scatters'] {
  if (rockKinds.length === 0) return [];

  const kind     = rng.pick(rockKinds);
  const count    = rng.int(2, 7);
  const groundY1 = horizonY + (1 - horizonY) * 0.90;
  const pos      = poissonDiskScatter(rng, count, 0.02, horizonY, 0.98, groundY1, 0.09);

  // Darken the surface color for a weathered / shadowed rock look.
  const darkened: RGB = [
    Math.round(palette.surface[0] * 0.70),
    Math.round(palette.surface[1] * 0.70),
    Math.round(palette.surface[2] * 0.70),
  ];

  return [buildScatterKind(rng, kind, pos, palette.surface, darkened)];
}

// ---------------------------------------------------------------------------
// Deposit markers  (pipeline stage 9b — site-gated)
// ---------------------------------------------------------------------------

/**
 * Place one deposit marker per entry in the deposits array.
 * Richness drives intensity (visual prominence), not presence — every deposit
 * in site.deposits gets a marker regardless of richness value.
 * RNG contract: exactly 2 floats per deposit (x, y).
 */
export function placeDepositMarkers(
  rng: SeededRng,
  deposits: { kind: string; richness: number }[],
  depositVisuals: Record<string, string>,
  horizonY: number,
): VistaModel['layers']['features']['depositMarkers'] {
  return deposits.map(deposit => {
    const visual = (depositVisuals[deposit.kind] ?? 'ore-vein') as
      VistaModel['layers']['features']['depositMarkers'][number]['visual'];
    return {
      deposit:   deposit.kind,
      pos: [
        0.08 + rng.next01() * 0.84,
        horizonY + rng.next01() * (1 - horizonY) * 0.65,
      ] as [number, number],
      intensity: deposit.richness,
      visual,
    };
  });
}

// ---------------------------------------------------------------------------
// Energy marker  (pipeline stage 9c — site-gated)
// ---------------------------------------------------------------------------

/**
 * Place the energy-source marker at a seeded ground-plane position.
 * Intensity = tier / 4 (0.25 per tier step).
 * RNG contract: exactly 2 floats (x, y).
 */
export function placeEnergyMarker(
  rng: SeededRng,
  energy: { source: EnergySource; tier: 1 | 2 | 3 | 4; magnitude: number },
  horizonY: number,
): VistaModel['layers']['features']['energyMarker'] {
  return {
    source: energy.source,
    pos: [
      0.10 + rng.next01() * 0.80,
      horizonY + rng.next01() * (1 - horizonY) * 0.55,
    ] as [number, number],
    intensity: energy.tier / 4,
  };
}

// ---------------------------------------------------------------------------
// Hazard overlays  (pipeline stage 10 — site-gated)
// ---------------------------------------------------------------------------

/**
 * Build hazard overlays from a site's hazard array.
 *
 * TRUTHFULNESS CLAUSE (BRIEF §2.5): an overlay is emitted for EVERY element
 * of the `hazards` array, unconditionally.  Desirability is not a parameter
 * here — it is not consulted and cannot suppress a hazard.  A high-richness
 * VOLCANIC spot with a Magma Surge hazard gets both glittering thermal vents
 * (features) AND a visible lava-flow scar (hazard overlay) on top.
 *
 * RNG contract: exactly 4 floats per hazard (x0, x1-offset, y0, y1-offset).
 */
export function placeHazardOverlays(
  rng: SeededRng,
  hazards: { kind: string; severity: number; named: boolean }[],
  hazardVisuals: Record<string, string>,
  horizonY: number,
): VistaModel['layers']['hazards']['overlays'] {
  return hazards.map(hazard => {
    const visual = (hazardVisuals[hazard.kind] ?? 'impact-scar') as
      VistaModel['layers']['hazards']['overlays'][number]['visual'];

    // Seeded region quad covering a portion of the ground plane.
    const x0 = rng.next01() * 0.50;
    const x1 = x0 + 0.20 + rng.next01() * 0.50;
    const y0 = horizonY + rng.next01() * (1 - horizonY) * 0.40;
    const y1 = y0 + rng.next01() * 0.30;

    const region: [number, number][] = [
      [x0, y0], [x1, y0], [x1, y1], [x0, y1],
    ];
    return { hazard: hazard.kind, severity: hazard.severity, visual, region };
  });
}

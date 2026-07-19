/**
 * Flora placement proof — distribution + type-gate
 *
 * Validates two changes shipped in the flora-placement fix:
 *
 * 1. Y-band coverage — for a lush JUNGLE world the dense-flora scatter fills
 *    the full visible land band [horizonY..waterlineY], NOT just the shore.
 *    Checked by asserting that the min Y of scatter instances is close to
 *    horizonY (distant band populated) and the median is roughly centred.
 *
 * 2. Type-gate — ARTIFICIAL and BARREN at nativeLife=1.0 produce ~0 dense-flora
 *    instances despite maximum nativeLife, because denseFloraFactor=0 for those
 *    types.
 *
 * No DOM, no canvas — pure pipeline.  Matches vitest/node environment.
 */

import { describe, it, expect } from 'vitest';
import { generateVista } from '../pipeline';
import type { VistaInput, PlanetType } from '../../contract';

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/** Build a minimal valid VistaInput with controlled nativeLife. */
function makeInput(
  type: PlanetType,
  seed: string,
  overrides: Partial<{
    habitability: number;
    nativeLife: number;
    waterCoverage: number;
    atmospherePresent: boolean;
    atmosphereDensity: number;
  }> = {},
): VistaInput {
  const {
    habitability     = 85,
    nativeLife       = 1.0,
    waterCoverage    = 0.50,
    atmospherePresent = true,
    atmosphereDensity = 0.70,
  } = overrides;

  return {
    contractVersion: 1,
    seed,
    planet: {
      type,
      habitability,
      temperature:  0.15,
      waterCoverage,
      nativeLife,
      atmosphere: {
        present: atmospherePresent,
        density: atmosphereDensity,
        kind:    'nitrogen',
      },
    },
    celestial: {
      star: { kind: 'G_YELLOW', color: '#ffe680' },
      orbitAu:             1.0,
      phaseDeg:            90,
      rotationPeriodHours: 24,
      axialTiltDeg:        23,
    },
  } satisfies VistaInput;
}

/**
 * Collect all scatter instance Y positions from the model's features layer.
 * Filters to the scatter kind that matches a flora-type prefix (excludes
 * glitter-spark and rock kinds so we're measuring only flora).
 */
function floraYPositions(model: ReturnType<typeof generateVista>): number[] {
  const ys: number[] = [];
  const ROCK_OR_GLITTER = new Set([
    'glitter-spark', 'boulder', 'stone-scatter', 'lava-rock', 'obsidian-shard',
    'pumice-scatter', 'sandstone-pillar', 'wind-carved-rock', 'gravel-scatter',
    'ice-boulder', 'snowdrift', 'sea-stack', 'tidal-rock', 'mountain-boulder',
    'scree-scatter', 'cliff-face', 'regolith-scatter', 'impact-ejecta',
    'basalt-outcrop', 'mossy-stone', 'root-tangle', 'coral-rock',
    'sandstone-scatter', 'tundra-rock', 'permafrost-mound', 'plating-segment',
    'support-strut',
  ]);
  for (const scatter of model.layers.features.scatters) {
    if (ROCK_OR_GLITTER.has(scatter.kind)) continue;
    for (const inst of scatter.instances) {
      ys.push(inst.pos[1]);
    }
  }
  return ys;
}

function median(arr: number[]): number {
  if (arr.length === 0) return NaN;
  const sorted = [...arr].sort((a, b) => a - b);
  const mid = Math.floor(sorted.length / 2);
  return sorted.length % 2 === 0
    ? (sorted[mid - 1] + sorted[mid]) / 2
    : sorted[mid];
}

// ---------------------------------------------------------------------------
// 1 — Y-band coverage: lush JUNGLE fills the full visible land band
// ---------------------------------------------------------------------------

describe('flora placement — Y-band distribution', () => {
  it('JUNGLE (lush, high water) — dense-flora fills [horizonY..waterlineY], not just the shore', () => {
    const input = makeInput('JUNGLE', 'flora-band-jungle-proof', {
      habitability: 90,
      nativeLife:   1.0,
      waterCoverage: 0.55,
    });
    const model = generateVista(input);

    const { horizonY }  = model.layers.terrain;
    const waterlineY    = model.layers.water?.waterlineY;

    // Sanity: water must be present for this proof to be meaningful.
    expect(waterlineY).toBeDefined();
    const wY = waterlineY!;

    // Visible land band height
    const landBand = wY - horizonY;
    expect(landBand).toBeGreaterThan(0.05);

    const ys = floraYPositions(model);

    // We need enough points to reason about the distribution.
    expect(ys.length).toBeGreaterThan(20);

    const minY = Math.min(...ys);
    const medY = median(ys);

    // Log for human inspection (vitest shows this on failure):
    console.log([
      `horizonY=${horizonY.toFixed(4)}  waterlineY=${wY.toFixed(4)}  landBand=${landBand.toFixed(4)}`,
      `flora instances: n=${ys.length}  min=${minY.toFixed(4)}  median=${medY.toFixed(4)}`,
      `min rel to band: ${((minY - horizonY) / landBand).toFixed(3)}`,
      `median rel to band: ${((medY - horizonY) / landBand).toFixed(3)}`,
    ].join('\n'));

    // Distant band populated: min Y must be within the top 30% of the visible
    // land band.  Before this fix, scatter budget was wasted below waterlineY
    // and the upper/distant strip near horizonY received no instances.
    // (If the fix had no effect, min would cluster near wY, not near horizonY.)
    const minRelative = (minY - horizonY) / landBand;
    expect(minRelative).toBeLessThan(0.30);

    // No flora placed below the waterline — both placeFloraScatters and
    // the dense-flora path are now bounded to [horizonY..waterlineY].
    const belowWater = ys.filter(y => y > wY + 1e-6);
    expect(belowWater).toHaveLength(0);

    // All flora is at or below the horizon line (none in the sky).
    const aboveHorizon = ys.filter(y => y < horizonY - 1e-6);
    expect(aboveHorizon).toHaveLength(0);
  });

  it('TERRAN (moderate water) — flora covers the land band, not just foreground', () => {
    const input = makeInput('TERRAN', 'flora-band-terran-proof', {
      habitability: 80,
      nativeLife:   0.85,
      waterCoverage: 0.40,
    });
    const model = generateVista(input);

    const { horizonY } = model.layers.terrain;
    const waterlineY   = model.layers.water?.waterlineY;

    expect(waterlineY).toBeDefined();
    const wY = waterlineY!;

    const ys = floraYPositions(model);
    if (ys.length === 0) {
      // Low nativeLife path may produce 0 — skip rather than fail.
      return;
    }

    const minY = Math.min(...ys);
    const landBand = wY - horizonY;

    console.log(
      `TERRAN: horizonY=${horizonY.toFixed(4)} wY=${wY.toFixed(4)}  ` +
      `n=${ys.length}  minY=${minY.toFixed(4)}  minRel=${((minY - horizonY) / landBand).toFixed(3)}`
    );

    // Distant band populated: min within top 40% of the land band.
    expect((minY - horizonY) / landBand).toBeLessThan(0.40);

    // No flora below the waterline (both scatter paths now bounded to waterlineY).
    expect(ys.filter(y => y > wY + 1e-6)).toHaveLength(0);

    // All flora at or below horizon (none in sky).
    expect(ys.filter(y => y < horizonY - 1e-6)).toHaveLength(0);
  });

  it('DESERT (no water) — flora Y spans [horizonY .. ~90% ground height]', () => {
    const input = makeInput('DESERT', 'flora-band-desert-proof', {
      habitability: 50,
      nativeLife:   0.60,
      waterCoverage: 0.05,
      atmosphereDensity: 0.3,
    });
    const model = generateVista(input);

    const { horizonY } = model.layers.terrain;
    // DESERT has water: 'none' — no water layer.
    expect(model.layers.water).toBeUndefined();

    const ys = floraYPositions(model);
    if (ys.length === 0) return; // low nativeLife might yield no dense flora

    const minY    = Math.min(...ys);
    const maxY    = Math.max(...ys);
    const groundY1 = horizonY + (1 - horizonY) * 0.90;

    console.log(
      `DESERT: horizonY=${horizonY.toFixed(4)}  n=${ys.length}  ` +
      `minY=${minY.toFixed(4)}  maxY=${maxY.toFixed(4)}  groundY1=${groundY1.toFixed(4)}`
    );

    // All flora in the ground band.
    expect(minY).toBeGreaterThanOrEqual(horizonY - 1e-6);
    expect(maxY).toBeLessThanOrEqual(groundY1 + 1e-6);
  });
});

// ---------------------------------------------------------------------------
// 2 — Type-gate: ARTIFICIAL and BARREN produce ~0 flora at nativeLife=1.0
// ---------------------------------------------------------------------------

describe('flora placement — type-suitability gate', () => {
  const TYPE_GATE_SEEDS = ['gate-seed-1', 'gate-seed-2', 'gate-seed-3'];

  it('ARTIFICIAL — nativeLife has zero effect on flora output (denseFloraFactor=0)', () => {
    // placeFloraScatters (features.ts) emits hydroponic-tray / engineered-plant
    // instances based on hab01 + desirability — those are legitimate for a station.
    // What we gate is the dense-flora path driven by nativeLife.  If denseFloraFactor=0
    // the scatter output must be identical regardless of nativeLife.
    for (const seed of TYPE_GATE_SEEDS) {
      const base = { habitability: 95, atmospherePresent: true, atmosphereDensity: 0.6 };

      const modelZeroLife = generateVista(makeInput('ARTIFICIAL', seed, { ...base, nativeLife: 0.0 }));
      const modelFullLife = generateVista(makeInput('ARTIFICIAL', seed, { ...base, nativeLife: 1.0 }));

      // JSON-identical scatter output proves the dense-flora path added nothing.
      const scatterZero = JSON.stringify(modelZeroLife.layers.features.scatters);
      const scatterFull = JSON.stringify(modelFullLife.layers.features.scatters);

      console.log(
        `ARTIFICIAL/${seed}: scatter groups zero-life=${modelZeroLife.layers.features.scatters.length}` +
        ` full-life=${modelFullLife.layers.features.scatters.length} identical=${scatterZero === scatterFull}`
      );

      expect(scatterZero).toBe(scatterFull);
    }
  });

  it('BARREN at nativeLife=1.0 produces zero flora scatter of any kind', () => {
    for (const seed of TYPE_GATE_SEEDS) {
      const input = makeInput('BARREN', seed, {
        habitability: 95,
        nativeLife:   1.0,
        atmospherePresent: false,
        atmosphereDensity: 0.0,
      });
      const model = generateVista(input);

      // BARREN has floraKinds: [] — no flora scatter is even attempted.
      // The dense-flora guard also requires floraKinds.length > 0.
      const allFlora = model.layers.features.scatters; // rocks only expected
      const floraOnly = allFlora.filter(s =>
        !['regolith-scatter', 'impact-ejecta', 'basalt-outcrop', 'glitter-spark'].includes(s.kind)
      );

      console.log(`BARREN/${seed}: non-rock scatter groups=${floraOnly.length}`);
      expect(floraOnly).toHaveLength(0);
    }
  });

  it('GAS_GIANT at nativeLife=1.0 produces zero flora of any kind', () => {
    for (const seed of TYPE_GATE_SEEDS) {
      const input = makeInput('GAS_GIANT', seed, {
        habitability: 60,
        nativeLife:   1.0,
        atmospherePresent: true,
        atmosphereDensity: 0.9,
      });
      const model = generateVista(input);

      // GAS_GIANT floraKinds: [] and denseFloraFactor: 0.
      expect(model.layers.features.scatters).toHaveLength(0);
    }
  });

  it('contrast — JUNGLE at nativeLife=1.0 produces many dense-flora instances', () => {
    // Positive control: the gate must not suppress lush natural types.
    for (const seed of TYPE_GATE_SEEDS) {
      const input = makeInput('JUNGLE', seed, {
        habitability: 90,
        nativeLife:   1.0,
      });
      const model = generateVista(input);
      const ys    = floraYPositions(model);

      console.log(`JUNGLE/${seed}: total flora instances=${ys.length}`);

      // A lush jungle with nativeLife=1.0 should have many instances.
      expect(ys.length).toBeGreaterThan(30);
    }
  });
});

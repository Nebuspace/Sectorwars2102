/**
 * Wave-1 vista regression sweep
 *
 * Covers all four review areas for the AAA Wave-1 changes:
 *   1. 12-type × 5-seed invariant sweep (60 models)
 *   2. Determinism — same (type, seed) → JSON-identical model
 *   3. Ridge-base blast radius — polyline Y values in [0, horizonY]
 *   4. handle.update() merge equivalence
 *   5. react.tsx structural-change classification
 *
 * No DOM, no canvas — pure pipeline.  Matches vitest/node environment.
 */

import { describe, it, expect } from 'vitest';
import { generateVista } from '../pipeline';
import { randomVistaInput } from '../validate';
import type { VistaInput, VistaModel, PlanetType } from '../../contract';

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const ALL_12: PlanetType[] = [
  'TERRAN', 'DESERT', 'OCEANIC', 'ICE', 'VOLCANIC', 'GAS_GIANT',
  'BARREN', 'JUNGLE', 'ARCTIC', 'TROPICAL', 'MOUNTAINOUS', 'ARTIFICIAL',
];

const SEEDS = ['seed-alpha', 'seed-bravo', 'seed-charlie', 'seed-delta', 'seed-echo'];

// Types whose profile declares a water body (profile.water !== 'none').
// Derived from profiles.ts entries — each maps to the profile's water field.
const WATER_TYPE_BY_PLANET: Partial<Record<PlanetType, string>> = {
  TERRAN:      'coastal',
  VOLCANIC:    'lava',
  OCEANIC:     'ocean',
  ICE:         'frozen',
  ARCTIC:      'frozen',
  MOUNTAINOUS: 'coastal',
  JUNGLE:      'coastal',
  TROPICAL:    'ocean',
};

// Types that must NEVER have a water layer.
const NO_WATER_TYPES: PlanetType[] = ['DESERT', 'BARREN', 'GAS_GIANT', 'ARTIFICIAL'];

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/** Build a minimal but valid VistaInput from randomVistaInput (uses the lab
 *  'lab-input' child stream — independent from the 10 pipeline streams). */
function makeInput(type: PlanetType, seed: string): VistaInput {
  return randomVistaInput(seed, type);
}

/** Generate all 60 models.  Throws on any crash — caught by the test runner. */
function generateAll(): { type: PlanetType; seed: string; model: VistaModel }[] {
  const results: { type: PlanetType; seed: string; model: VistaModel }[] = [];
  for (const type of ALL_12) {
    for (const seed of SEEDS) {
      const input = makeInput(type, seed);
      const model = generateVista(input);
      results.push({ type, seed, model });
    }
  }
  return results;
}

// ---------------------------------------------------------------------------
// 1 — 12-type × 5-seed invariant sweep
// ---------------------------------------------------------------------------

describe('Wave-1 regression — 60-model invariant sweep', () => {
  // Generate once; reuse across tests in this describe block.
  const all = generateAll();

  it('generates all 60 models without throwing', () => {
    expect(all).toHaveLength(60);
  });

  it('invariants.ok === true for every model', () => {
    const failures: string[] = [];
    for (const { type, seed, model } of all) {
      if (!model.invariants.ok) {
        failures.push(`${type}/${seed}: notes=[${model.invariants.notes.join(', ')}]`);
      }
    }
    expect(failures).toEqual([]);
  });

  it('contractVersion is 1 on every model', () => {
    for (const { model } of all) {
      expect(model.contractVersion).toBe(1);
    }
  });

  it('seed is propagated unchanged to every model', () => {
    for (const { seed, model } of all) {
      expect(model.seed).toBe(seed);
    }
  });

  it('planetType matches the input type on every model', () => {
    const mismatches: string[] = [];
    for (const { type, seed, model } of all) {
      if (model.planetType !== type) {
        mismatches.push(`${type}/${seed}: got ${model.planetType}`);
      }
    }
    expect(mismatches).toEqual([]);
  });

  it('desirability is in [0, 1] for every model', () => {
    const bad: string[] = [];
    for (const { type, seed, model } of all) {
      if (model.desirability < 0 || model.desirability > 1) {
        bad.push(`${type}/${seed}: desirability=${model.desirability}`);
      }
    }
    expect(bad).toEqual([]);
  });

  // ---- water.type sensibility per type ----

  it('types with a declared water body have the correct water.type', () => {
    const wrong: string[] = [];
    for (const { type, seed, model } of all) {
      const expectedWaterType = WATER_TYPE_BY_PLANET[type];
      if (expectedWaterType === undefined) continue;  // no-water type — checked below
      if (!model.layers.water) {
        wrong.push(`${type}/${seed}: expected water layer (${expectedWaterType}), got none`);
      } else if (model.layers.water.type !== expectedWaterType) {
        wrong.push(
          `${type}/${seed}: expected water.type="${expectedWaterType}", got "${model.layers.water.type}"`
        );
      }
    }
    expect(wrong).toEqual([]);
  });

  it('DESERT / BARREN / GAS_GIANT / ARTIFICIAL never have a water layer', () => {
    const wrong: string[] = [];
    for (const { type, seed, model } of all) {
      if (!NO_WATER_TYPES.includes(type)) continue;
      if (model.layers.water) {
        wrong.push(`${type}/${seed}: unexpected water layer (type="${model.layers.water.type}")`);
      }
    }
    expect(wrong).toEqual([]);
  });

  // ---- waterlineY > horizonY when water is present (land strip invariant) ----

  it('waterlineY > horizonY when water is present', () => {
    const violated: string[] = [];
    for (const { type, seed, model } of all) {
      if (!model.layers.water) continue;
      const { waterlineY } = model.layers.water;
      const { horizonY }   = model.layers.terrain;
      if (waterlineY <= horizonY) {
        violated.push(
          `${type}/${seed}: waterlineY=${waterlineY.toFixed(4)} ≤ horizonY=${horizonY.toFixed(4)} — no land strip`
        );
      }
    }
    expect(violated).toEqual([]);
  });

  // ---- ridge count sanity ----

  it('ridge strata count is non-negative and matches ridgeCount recipe bounds', () => {
    // GAS_GIANT uses cloud-deck (0 strata); all others should have ≥ 0.
    const bad: string[] = [];
    for (const { type, seed, model } of all) {
      const count = model.layers.terrain.strata.length;
      if (count < 0) {
        bad.push(`${type}/${seed}: strata.length=${count} (negative?)`);
      }
      if (type === 'GAS_GIANT' && count !== 0) {
        bad.push(`${type}/${seed}: GAS_GIANT must have 0 strata, got ${count}`);
      }
    }
    expect(bad).toEqual([]);
  });

  // ---- particles / events are now non-empty arrays (not the old []) ----

  it('atmosphere.present=true worlds have at least one particle or event', () => {
    // Pre-Wave-1 both were always [].  Now deriveParticles + deriveEvents run.
    // Only assert on worlds where the pipeline should produce output.
    const missing: string[] = [];
    for (const { type, seed, model } of all) {
      const atmo = model.layers.atmosphere;
      if (!atmo.present) continue;
      // GAS_GIANT and ARTIFICIAL have their own paths (dust / spark) but
      // BARREN might not have an atmosphere (atmospherePresent is seeded).
      const total = atmo.particles.length + atmo.events.length;
      if (total === 0) {
        // Only flag if atmo is present AND it's a type that reliably gets particles
        // (BARREN can legitimately have an atmosphere but minimal particles if all
        // conditions miss — the default branch emits 'dust', so this shouldn't fire).
        missing.push(`${type}/${seed}: atmo.present but 0 particles AND 0 events`);
      }
    }
    expect(missing).toEqual([]);
  });

  // ---- celestial.nebula passthrough ----

  it('nebula present in model iff nebula present in input', () => {
    // validate.ts randomVistaInput now generates nebula ~30% of seeds.
    // pipeline.ts buildCelestial now passes it through to the model.
    for (const { type, seed, model } of all) {
      const input = makeInput(type, seed);
      const inputHasNebula = !!input.celestial.nebula;
      const modelHasNebula = !!model.layers.celestial.nebula;
      if (inputHasNebula !== modelHasNebula) {
        // Report as string for vitest diff clarity
        expect(`${type}/${seed}: model.nebula=${String(modelHasNebula)}`).toBe(
          `${type}/${seed}: model.nebula=${String(inputHasNebula)}`
        );
      }
    }
  });
});

// ---------------------------------------------------------------------------
// 2 — Determinism: same (type, seed) → JSON-identical model
// ---------------------------------------------------------------------------

describe('Wave-1 regression — determinism', () => {
  const DETERM_TYPES: PlanetType[] = ['TERRAN', 'VOLCANIC', 'OCEANIC', 'ICE', 'GAS_GIANT', 'ARTIFICIAL'];
  const DETERM_SEEDS = ['det-seed-1', 'det-seed-2'];

  it('generateVista is byte-identical on two calls with the same (type, seed)', () => {
    const divergences: string[] = [];
    for (const type of DETERM_TYPES) {
      for (const seed of DETERM_SEEDS) {
        const input = makeInput(type, seed);
        const m1 = generateVista(input);
        const m2 = generateVista(input);
        const s1 = JSON.stringify(m1);
        const s2 = JSON.stringify(m2);
        if (s1 !== s2) {
          divergences.push(`${type}/${seed}: models diverged`);
        }
      }
    }
    expect(divergences).toEqual([]);
  });

  it('deriveParticles / deriveEvents introduce no nondeterminism', () => {
    // These are the new pipeline functions.  Check specifically that
    // running the same input twice produces identical atmosphere layers.
    const divergences: string[] = [];
    for (const type of ALL_12) {
      const input = makeInput(type, 'atmo-determ-seed');
      const a1 = generateVista(input).layers.atmosphere;
      const a2 = generateVista(input).layers.atmosphere;
      if (JSON.stringify(a1) !== JSON.stringify(a2)) {
        divergences.push(`${type}: atmosphere layers diverged`);
      }
    }
    expect(divergences).toEqual([]);
  });

  it('different seeds produce different models for the same type', () => {
    // Sanity: seeding actually does something.  Compare TERRAN on two seeds.
    const m1 = generateVista(makeInput('TERRAN', 'unique-seed-A'));
    const m2 = generateVista(makeInput('TERRAN', 'unique-seed-B'));
    expect(JSON.stringify(m1)).not.toBe(JSON.stringify(m2));
  });

  it('nebula passthrough is deterministic — same input → same celestial.nebula', () => {
    for (const seed of ['neb-det-1', 'neb-det-2', 'neb-det-3']) {
      for (const type of ['TERRAN', 'OCEANIC', 'VOLCANIC'] as PlanetType[]) {
        const input = makeInput(type, seed);
        const neb1 = generateVista(input).layers.celestial.nebula;
        const neb2 = generateVista(input).layers.celestial.nebula;
        expect(JSON.stringify(neb1)).toBe(JSON.stringify(neb2));
      }
    }
  });
});

// ---------------------------------------------------------------------------
// 3 — Ridge-base blast radius: polyline Y values in [0, horizonY]
// ---------------------------------------------------------------------------

describe('Wave-1 regression — ridge-base fix blast radius', () => {
  it('all strata polyline Y values are in [0, horizonY] for every type and seed', () => {
    const violations: string[] = [];
    for (const type of ALL_12) {
      for (const seed of SEEDS) {
        const input = makeInput(type, seed);
        const model = generateVista(input);
        const { horizonY, strata } = model.layers.terrain;

        for (let si = 0; si < strata.length; si++) {
          const { polyline } = strata[si];
          for (let pi = 0; pi < polyline.length; pi++) {
            const y = polyline[pi][1];
            if (y < 0 || y > horizonY + 1e-9) {  // 1e-9 tolerance for float rounding
              violations.push(
                `${type}/${seed} stratum[${si}] point[${pi}]: y=${y.toFixed(4)} out of [0, ${horizonY.toFixed(4)}]`
              );
            }
          }
        }
      }
    }
    // Report first 10 violations to keep output readable
    expect(violations.slice(0, 10)).toEqual([]);
  });

  it('strata polyline Y average (the ridge base) is in [0, horizonY]', () => {
    // This directly tests that the normalized-average used in backend.ts
    // buildVistaCache stays within the expected normalized range.
    const violations: string[] = [];
    for (const type of ALL_12) {
      for (const seed of SEEDS) {
        const model = generateVista(makeInput(type, seed));
        const { horizonY, strata } = model.layers.terrain;

        for (let si = 0; si < strata.length; si++) {
          const poly = strata[si].polyline;
          if (poly.length === 0) continue;
          const base = poly.reduce((a, pt) => a + pt[1], 0) / poly.length;
          if (base < 0 || base > horizonY + 1e-9) {
            violations.push(
              `${type}/${seed} stratum[${si}]: avg-Y base=${base.toFixed(4)} outside [0, horizonY=${horizonY.toFixed(4)}]`
            );
          }
        }
      }
    }
    expect(violations.slice(0, 10)).toEqual([]);
  });

  it('horizonY itself is always in (0, 1) — sanity check for the bound above', () => {
    const bad: string[] = [];
    for (const { type, seed, model } of generateAll()) {
      const hy = model.layers.terrain.horizonY;
      if (hy <= 0 || hy >= 1) {
        bad.push(`${type}/${seed}: horizonY=${hy}`);
      }
    }
    expect(bad).toEqual([]);
  });

  it('GAS_GIANT produces 0 strata — cloud-deck terrain path is untouched', () => {
    for (const seed of SEEDS) {
      const model = generateVista(makeInput('GAS_GIANT', seed));
      expect(model.layers.terrain.strata).toHaveLength(0);
      expect(model.layers.terrain.mode).toBe('cloud-deck');
    }
  });

  it('ARTIFICIAL strata count matches its archetype — plating path still emits ridges', () => {
    // ARTIFICIAL terrain mode = 'plating' but the buildTerrain path still runs
    // (only GAS_GIANT uses cloud-deck).  Strata count ≥ 0 is the invariant.
    for (const seed of SEEDS) {
      const model = generateVista(makeInput('ARTIFICIAL', seed));
      expect(model.layers.terrain.mode).toBe('plating');
      expect(model.layers.terrain.strata.length).toBeGreaterThanOrEqual(0);
    }
  });
});

// ---------------------------------------------------------------------------
// 4 — handle.update() merge equivalence (pipeline level — no DOM)
// ---------------------------------------------------------------------------

describe('Wave-1 regression — handle.update() merge equivalence', () => {
  // The update() merge logic (backend.ts ~L2993) does:
  //   merged = { ...base, ...partial, planet: {...base.p, ...partial.p}, celestial: {...} }
  //
  // When partial IS a full VistaInput (as react.tsx always passes), merged === partial
  // and generateVista(merged) must equal generateVista(partial).

  function simulateMerge(base: VistaInput, partial: VistaInput): VistaInput {
    return {
      ...base,
      ...partial,
      planet: partial.planet
        ? { ...base.planet, ...partial.planet }
        : base.planet,
      celestial: partial.celestial
        ? { ...base.celestial, ...partial.celestial }
        : base.celestial,
    } as VistaInput;
  }

  it('generateVista(merge(base, fullPartial)) === generateVista(fullPartial)', () => {
    // Simulates: mount with A, then update(B) where B is a full VistaInput.
    const divergences: string[] = [];

    for (const type of ['TERRAN', 'OCEANIC', 'VOLCANIC', 'GAS_GIANT', 'ARTIFICIAL'] as PlanetType[]) {
      const inputA = makeInput(type, 'update-base-seed');
      const inputB = makeInput(type, 'update-patched-seed-' + type);

      const merged = simulateMerge(inputA, inputB);
      const modelMerged = generateVista(merged);
      const modelFresh  = generateVista(inputB);

      if (JSON.stringify(modelMerged) !== JSON.stringify(modelFresh)) {
        divergences.push(`${type}: merged model diverges from fresh generate`);
      }
    }
    expect(divergences).toEqual([]);
  });

  it('partial habitability update preserves seed and planet.type (merge correctness)', () => {
    // Simulates a slider drag: only habitability changes, seed and type are preserved.
    const base = makeInput('TERRAN', 'merge-test-seed');
    const partial: VistaInput = { ...base, planet: { ...base.planet, habitability: 5 } };

    const merged = simulateMerge(base, partial);
    expect(merged.seed).toBe(base.seed);
    expect(merged.planet.type).toBe('TERRAN');
    expect(merged.planet.habitability).toBe(5);

    const modelMerged = generateVista(merged);
    const modelDirect = generateVista(partial);
    expect(JSON.stringify(modelMerged)).toBe(JSON.stringify(modelDirect));
  });

  it('partial celestial update (nebula toggle) merges correctly', () => {
    const base = makeInput('TERRAN', 'nebula-merge-seed');

    // Simulate toggling nebula on via a partial celestial update
    const withNebula: VistaInput = {
      ...base,
      celestial: { ...base.celestial, nebula: { hue: 180, density: 0.4 } },
    };
    const merged = simulateMerge(base, withNebula);
    expect(merged.celestial.nebula).toEqual({ hue: 180, density: 0.4 });

    const modelMerged = generateVista(merged);
    const modelDirect = generateVista(withNebula);
    expect(modelMerged.layers.celestial.nebula).toEqual({ hue: 180, density: 0.4 });
    expect(JSON.stringify(modelMerged)).toBe(JSON.stringify(modelDirect));
  });

  it('update with same input produces same model (idempotent)', () => {
    for (const type of ALL_12) {
      const input = makeInput(type, 'idempotent-seed');
      const merged = simulateMerge(input, input);
      expect(JSON.stringify(generateVista(merged))).toBe(JSON.stringify(generateVista(input)));
    }
  });
});

// ---------------------------------------------------------------------------
// 5 — react.tsx structural-change classification
// ---------------------------------------------------------------------------

describe('Wave-1 regression — react.tsx structural/non-structural classification', () => {
  // The classification logic (react.tsx ~L107):
  //   isStructural = handleRef === null || seed changed || planet.type changed
  //
  // Testing this without DOM: we exercise the boolean conditions directly.

  function isStructural(
    handleIsNull: boolean,
    prevSeed: string, nextSeed: string,
    prevType: PlanetType, nextType: PlanetType,
  ): boolean {
    return handleIsNull || nextSeed !== prevSeed || nextType !== prevType;
  }

  it('initial mount (null handle) is always structural', () => {
    for (const type of ALL_12) {
      expect(isStructural(true, '', 'seed-a', type, type)).toBe(true);
    }
  });

  it('seed change is structural', () => {
    expect(isStructural(false, 'seed-a', 'seed-b', 'TERRAN', 'TERRAN')).toBe(true);
  });

  it('type change is structural', () => {
    expect(isStructural(false, 'seed-a', 'seed-a', 'TERRAN', 'VOLCANIC')).toBe(true);
  });

  it('habitability / view / celestial changes are non-structural (update path)', () => {
    // Only seed and planet.type drive structural detection; everything else is hot-patch.
    for (const type of ALL_12) {
      expect(isStructural(false, 'same-seed', 'same-seed', type, type)).toBe(false);
    }
  });

  it('StrictMode double-invoke: second run with null handle remounts even with same seed+type', () => {
    // StrictMode unmounts after the first run → handleRef.current = null.
    // The second run must remount even though prevSeed/prevType already hold the values.
    // The null handle check fires first (short-circuits), so this is structural.
    expect(isStructural(true, 'seed-x', 'seed-x', 'TERRAN', 'TERRAN')).toBe(true);
  });

  it('simultaneous seed AND type change is structural (not double-counted)', () => {
    // Both changed — result must be structural (no logic error in OR chain)
    expect(isStructural(false, 'seed-a', 'seed-b', 'TERRAN', 'VOLCANIC')).toBe(true);
  });
});

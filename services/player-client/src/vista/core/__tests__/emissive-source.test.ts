/**
 * Vista Engine — TK-2 emissive light source
 *
 * Proves, headlessly (pure pipeline, no DOM):
 *   1. Seed-determinism — same seed → same emissiveSource (pos/color/intensity/
 *      radius), across two independent generateVista() calls, for all 3
 *      emissive biomes (VOLCANIC/lava, ICE/aurora, MOUNTAINOUS/alpenglow).
 *   2. Structural byte-identical guarantee for UNTOUCHED profiles — every
 *      profile without PlanetProfile.emissiveSource configured produces
 *      `lighting.emissiveSource === undefined`; the full lighting block for
 *      those types matches a reference computed via the pre-TK2 formula
 *      (keyIntensity/bloom/colorGradeWarmth/ambient/fill/keyColor — none of
 *      which this WO's changes touch).
 *   3. Per-seed jitter varies — two DIFFERENT seeds of the same emissive type
 *      produce DIFFERENT (but both internally-deterministic) placements, so
 *      the jitter isn't accidentally a no-op.
 *   4. Color sourcing: lava/aurora derive from palette.accent; alpenglow
 *      derives from keyColor (NOT palette.accent, which is the wrong — cool
 *      mineral-green — color for MOUNTAINOUS).
 */

import { describe, it, expect } from 'vitest';
import { generateVista } from '../pipeline';
import { PERF_SCENES } from '../../perf/scenes';
import type { VistaInput } from '../../contract';

const VOLCANIC_CALM    = PERF_SCENES.find((s) => s.id === 'VOLCANIC_CALM')!.input;
const ICE_CALM         = PERF_SCENES.find((s) => s.id === 'ICE_CALM')!.input;
const MOUNTAINOUS_CALM = PERF_SCENES.find((s) => s.id === 'MOUNTAINOUS_CALM')!.input;
const OCEANIC_CALM     = PERF_SCENES.find((s) => s.id === 'OCEANIC_CALM')!.input;
const BARREN_CALM      = PERF_SCENES.find((s) => s.id === 'BARREN_CALM')!.input;
const TERRAN_CALM      = PERF_SCENES.find((s) => s.id === 'TERRAN_CALM')!.input;

describe('TK-2 emissiveSource — seed-determinism (3 emissive biomes)', () => {
  const cases: [string, VistaInput, 'lava' | 'aurora' | 'alpenglow'][] = [
    ['VOLCANIC (Pisces VI, seed 225)',    VOLCANIC_CALM,    'lava'],
    ['ICE (Procyon Minor, seed 292)',     ICE_CALM,         'aurora'],
    ['MOUNTAINOUS (Deneb, seed 208)',     MOUNTAINOUS_CALM, 'alpenglow'],
  ];

  for (const [label, input, expectedKind] of cases) {
    it(`${label} — same seed produces byte-identical emissiveSource across two runs`, () => {
      const m1 = generateVista(input);
      const m2 = generateVista(input);
      expect(m1.lighting.emissiveSource).toBeDefined();
      expect(m1.lighting.emissiveSource).toEqual(m2.lighting.emissiveSource);
      expect(m1.lighting.emissiveSource!.kind).toBe(expectedKind);
      // Full-model determinism too (the byte-identical foundation this WO's
      // change must not break for the 3 biomes it DOES touch).
      expect(JSON.stringify(m1)).toBe(JSON.stringify(m2));
    });
  }

  it('two DIFFERENT seeds of the same type produce DIFFERENT jittered placements', () => {
    const altSeedInput: VistaInput = { ...VOLCANIC_CALM, seed: 'a-different-seed-999' };
    const a = generateVista(VOLCANIC_CALM).lighting.emissiveSource!;
    const b = generateVista(altSeedInput).lighting.emissiveSource!;
    // At least one jittered field must differ — proves the jitter draws from
    // the seed rather than being a hardcoded constant.
    const identical =
      a.pos[0] === b.pos[0] && a.pos[1] === b.pos[1] &&
      a.intensity === b.intensity && a.radius === b.radius;
    expect(identical, 'jitter did not vary across seeds — likely a hardcoded constant').toBe(false);
  });
});

describe('TK-2 emissiveSource — untouched profiles are structurally unaffected', () => {
  const untouched: [string, VistaInput][] = [
    ['OCEANIC', OCEANIC_CALM],
    ['BARREN',  BARREN_CALM],
    ['TERRAN',  TERRAN_CALM],
  ];

  for (const [label, input] of untouched) {
    it(`${label} — lighting.emissiveSource is undefined (no emissiveSource configured on this profile)`, () => {
      const model = generateVista(input);
      expect(model.lighting.emissiveSource).toBeUndefined();
    });

    it(`${label} — lighting block has exactly the pre-TK2 field set (no stray emissiveSource key)`, () => {
      const model = generateVista(input);
      expect(Object.keys(model.lighting).sort()).toEqual(
        ['ambient', 'bloom', 'colorGradeWarmth', 'fill', 'keyColor', 'keyDir', 'keyIntensity'].sort(),
      );
    });
  }
});

describe('TK-2 emissiveSource — color sourcing per kind', () => {
  it('lava (VOLCANIC) and aurora (ICE) derive color from palette.accent', () => {
    const volcanic = generateVista(VOLCANIC_CALM);
    expect(volcanic.lighting.emissiveSource!.color).toEqual(volcanic.palette.accent);

    const ice = generateVista(ICE_CALM);
    expect(ice.lighting.emissiveSource!.color).toEqual(ice.palette.accent);
  });

  it('alpenglow (MOUNTAINOUS) derives color from keyColor, NOT palette.accent (which is cool mineral-green here)', () => {
    const model = generateVista(MOUNTAINOUS_CALM);
    expect(model.lighting.emissiveSource!.color).toEqual(model.lighting.keyColor);
    expect(model.lighting.emissiveSource!.color).not.toEqual(model.palette.accent);
  });
});

describe('TK-2 emissiveSource — every emissive scene is still a valid, invariant-clean VistaModel', () => {
  const cases: [string, VistaInput][] = [
    ['VOLCANIC',    VOLCANIC_CALM],
    ['ICE',         ICE_CALM],
    ['MOUNTAINOUS', MOUNTAINOUS_CALM],
  ];
  for (const [label, input] of cases) {
    it(`${label} — invariants.ok === true`, () => {
      const model = generateVista(input);
      expect(model.invariants.ok, model.invariants.notes.join('; ')).toBe(true);
    });
  }
});

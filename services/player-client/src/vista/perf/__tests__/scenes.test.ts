/**
 * Vista Engine — perf benchmark reference scenes  (PERF-HARNESS sub-part (b))
 *
 * Proves the 12-scene set matches the WO's declared biome/seed/load-state
 * spec exactly, and that every scene is a valid, non-throwing VistaInput
 * (generateVista().invariants.ok) — i.e. these scenes are actually renderable
 * in principle, not just well-typed literals. Pure pipeline, no DOM.
 */

import { describe, it, expect } from 'vitest';
import { generateVista } from '../../core/pipeline';
import { PERF_SCENES, TARGET_FRAME_MS, FLOOR_FRAME_MS } from '../scenes';

const EXPECTED = [
  { planetType: 'VOLCANIC', locationName: 'Pisces VI', seed: '225' },
  { planetType: 'ICE', locationName: 'Procyon Minor', seed: '292' },
  { planetType: 'OCEANIC', locationName: 'Antares VI', seed: '98' },
  { planetType: 'BARREN', locationName: 'Polaris-7', seed: '58' },
  { planetType: 'TERRAN', locationName: 'New Earth', seed: '1' },
  { planetType: 'MOUNTAINOUS', locationName: 'Deneb', seed: '208' },
] as const;

describe('PERF_SCENES — shape', () => {
  it('has exactly 12 scenes: 6 biomes × (CALM, EXTREME)', () => {
    expect(PERF_SCENES).toHaveLength(12);
  });

  it('covers exactly the 6 WO-declared biome/location/seed triples, each with CALM + EXTREME', () => {
    for (const biome of EXPECTED) {
      const calm = PERF_SCENES.find((s) => s.id === `${biome.planetType}_CALM`);
      const extreme = PERF_SCENES.find((s) => s.id === `${biome.planetType}_EXTREME`);
      expect(calm, `${biome.planetType}_CALM missing`).toBeDefined();
      expect(extreme, `${biome.planetType}_EXTREME missing`).toBeDefined();

      for (const scene of [calm!, extreme!]) {
        expect(scene.planetType).toBe(biome.planetType);
        expect(scene.locationName).toBe(biome.locationName);
        expect(scene.input.seed).toBe(biome.seed);
      }
    }
  });

  it('CALM and EXTREME of the same biome share the same seed (same world, different load)', () => {
    for (const biome of EXPECTED) {
      const calm = PERF_SCENES.find((s) => s.id === `${biome.planetType}_CALM`)!;
      const extreme = PERF_SCENES.find((s) => s.id === `${biome.planetType}_EXTREME`)!;
      expect(calm.input.seed).toBe(extreme.input.seed);
    }
  });

  it('CALM scenes carry zero hazards and no moons/rings/siblings/nebula (the load floor)', () => {
    for (const scene of PERF_SCENES.filter((s) => s.load === 'CALM')) {
      expect(scene.input.site?.hazards).toEqual([]);
      expect(scene.input.celestial.moons).toBeUndefined();
      expect(scene.input.celestial.rings).toBeUndefined();
      expect(scene.input.celestial.siblings).toBeUndefined();
      expect(scene.input.celestial.nebula).toBeUndefined();
    }
  });

  it('EXTREME scenes carry hazards (one named) + moons + rings + siblings + nebula (the load ceiling)', () => {
    for (const scene of PERF_SCENES.filter((s) => s.load === 'EXTREME')) {
      expect(scene.input.site?.hazards.length).toBeGreaterThanOrEqual(2);
      expect(scene.input.site?.hazards.some((h) => h.named)).toBe(true);
      expect(scene.input.celestial.moons?.length).toBeGreaterThanOrEqual(2);
      expect(scene.input.celestial.rings).toBe(true);
      expect(scene.input.celestial.siblings?.length).toBeGreaterThanOrEqual(2);
      expect(scene.input.celestial.nebula).toBeDefined();
    }
  });
});

describe('PERF_SCENES — every scene is a valid, renderable VistaInput', () => {
  for (const scene of PERF_SCENES) {
    it(`${scene.id} produces invariants.ok === true`, () => {
      const model = generateVista(scene.input);
      expect(model.invariants.ok, model.invariants.notes.join('; ')).toBe(true);
      expect(model.planetType).toBe(scene.planetType);
    });
  }
});

describe('budget constants', () => {
  it('TARGET_FRAME_MS < FLOOR_FRAME_MS', () => {
    expect(TARGET_FRAME_MS).toBeLessThan(FLOOR_FRAME_MS);
  });
  it('matches the WO-stated 7ms target / 33ms (30fps) floor', () => {
    expect(TARGET_FRAME_MS).toBe(7);
    expect(FLOOR_FRAME_MS).toBe(33);
  });
});

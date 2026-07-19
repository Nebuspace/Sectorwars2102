/**
 * landedVistaAdapter — unit tests
 *
 * Asserts the adapter's four core contracts:
 *   (a) Valid PlanetTypes produce generateVista-clean VistaInput (invariants.ok === true).
 *   (b) Same seedKey → deepEqual VistaInput on repeated calls (full determinism).
 *   (c) deposits and hazards are always [] (truthfulness policy).
 *   (d) Unmappable planetType → null (caller falls back to legacy renderer).
 *
 * No DOM, no canvas — pure pipeline tests.
 */

import { describe, it, expect } from 'vitest';
import {
  adaptLandedSceneToVistaInput,
  type LandedVistaSource,
} from '../landedVistaAdapter';
import { generateVista } from '../../../vista/core/pipeline';

// ---------------------------------------------------------------------------
// (a) Valid PlanetTypes → invariants.ok === true
// ---------------------------------------------------------------------------

describe('valid planet types produce clean VistaInput (invariants.ok)', () => {
  const mappable: Array<{ label: string; src: LandedVistaSource }> = [
    {
      label: 'TERRAN',
      src: {
        seedKey:      'test-adapt-TERRAN',
        planetType:   'TERRAN',
        habitability: 80,
        citadelLevel: 3,
        orbitAu:      1.0,
        star:         { kind: 'G_YELLOW', color: '#fff4d0' },
      },
    },
    {
      label: 'VOLCANIC (hostile)',
      src: {
        seedKey:      'test-adapt-VOLCANIC',
        planetType:   'VOLCANIC',
        habitability: 8,
        citadelLevel: 1,
        orbitAu:      0.3,
        star:         { kind: 'M_DWARF', color: '#ff8060' },
      },
    },
    {
      label: 'BARREN (no atmosphere)',
      src: {
        seedKey:      'test-adapt-BARREN',
        planetType:   'BARREN',
        habitability: 5,
        citadelLevel: 2,
      },
    },
    {
      label: 'ARTIFICIAL',
      src: {
        seedKey:      'test-adapt-ARTIFICIAL',
        planetType:   'ARTIFICIAL',
        habitability: 62,
        citadelLevel: 2,
        moons:        1,
        siblingCount: 2,
      },
    },
    {
      label: 'ICE (low hab, cold)',
      src: {
        seedKey:      'test-adapt-ICE',
        planetType:   'ICE',
        habitability: 18,
        citadelLevel: 2,
        orbitAu:      2.2,
        star:         { kind: 'K_ORANGE', color: '#ffcc80' },
      },
    },
    {
      label: 'GAS_GIANT',
      src: {
        seedKey:      'test-adapt-GAS_GIANT',
        planetType:   'GAS_GIANT',
        habitability: 0,
        citadelLevel: 1,
        orbitAu:      5.2,
      },
    },
    {
      label: 'MOUNTAINOUS with moons',
      src: {
        seedKey:      'test-adapt-MOUNTAINOUS',
        planetType:   'MOUNTAINOUS',
        habitability: 52,
        citadelLevel: 4,
        moons:        2,
      },
    },
  ];

  for (const { label, src } of mappable) {
    it(`${label} → generateVista completes with invariants.ok`, () => {
      const input = adaptLandedSceneToVistaInput(src);
      expect(input).not.toBeNull();

      const model = generateVista(input!);
      expect(model.invariants.ok).toBe(true);
      expect(model.planetType).toBe(src.planetType?.toUpperCase());
    });
  }
});

// ---------------------------------------------------------------------------
// (b) Determinism — same seedKey → identical VistaInput
// ---------------------------------------------------------------------------

describe('determinism — same seedKey produces identical VistaInput', () => {
  const src: LandedVistaSource = {
    seedKey:      'determinism-test-seed-42',
    planetType:   'JUNGLE',
    habitability: 75,
    citadelLevel: 2,
    orbitAu:      0.9,
    star:         { kind: 'G_YELLOW', color: '#fff4d0' },
    moons:        2,
    siblingCount: 1,
  };

  it('two calls with identical src produce deepEqual VistaInput', () => {
    const a = adaptLandedSceneToVistaInput(src);
    const b = adaptLandedSceneToVistaInput(src);
    expect(a).not.toBeNull();
    expect(b).not.toBeNull();
    expect(a).toEqual(b);
  });

  it('seed field is the seedKey verbatim', () => {
    const input = adaptLandedSceneToVistaInput(src);
    expect(input!.seed).toBe(src.seedKey);
  });

  it('different seedKeys produce different phaseDeg', () => {
    const a = adaptLandedSceneToVistaInput({ ...src, seedKey: 'seed-alpha' });
    const b = adaptLandedSceneToVistaInput({ ...src, seedKey: 'seed-beta' });
    // phaseDeg is one of the seed-derived fields — different seeds should (almost
    // always) produce different values.  If this flaps, the hash function is broken.
    expect(a!.celestial.phaseDeg).not.toBe(b!.celestial.phaseDeg);
  });
});

// ---------------------------------------------------------------------------
// (c) Truthfulness — deposits and hazards are always []
// ---------------------------------------------------------------------------

describe('truthfulness — deposits and hazards are always empty', () => {
  const ALL_TYPES: string[] = [
    'TERRAN', 'DESERT', 'OCEANIC', 'ICE', 'VOLCANIC', 'GAS_GIANT',
    'BARREN', 'JUNGLE', 'ARCTIC', 'TROPICAL', 'MOUNTAINOUS', 'ARTIFICIAL',
  ];

  for (const type of ALL_TYPES) {
    it(`${type}: site.deposits === [] and site.hazards === []`, () => {
      const input = adaptLandedSceneToVistaInput({
        seedKey:    `truthfulness-${type}`,
        planetType: type,
      });
      expect(input).not.toBeNull();
      expect(input!.site!.deposits).toEqual([]);
      expect(input!.site!.hazards).toEqual([]);
    });
  }

  it('hazard-truthfulness invariant: generateVista with empty hazards emits 0 overlays', () => {
    // Belt-and-suspenders: confirm the pipeline respects the empty hazard array —
    // the hazard-truthfulness suite proves non-empty → overlays present; this
    // proves empty → overlays absent.
    const input = adaptLandedSceneToVistaInput({
      seedKey:    'truthfulness-pipeline-check',
      planetType: 'TERRAN',
      habitability: 85,
      citadelLevel: 3,
    });
    const model = generateVista(input!);
    expect(model.layers.hazards.overlays).toHaveLength(0);
  });
});

// ---------------------------------------------------------------------------
// (d) Unmappable planetType → null
// ---------------------------------------------------------------------------

describe('unmappable planetType returns null', () => {
  const unmappable = ['ZONE', 'UNKNOWN', 'WORMHOLE', '', 'null', '???', '12345'];

  for (const type of unmappable) {
    it(`"${type}" → null`, () => {
      const result = adaptLandedSceneToVistaInput({
        seedKey:    'unmappable-test',
        planetType: type,
      });
      expect(result).toBeNull();
    });
  }

  it('absent planetType → null', () => {
    const result = adaptLandedSceneToVistaInput({ seedKey: 'unmappable-absent' });
    expect(result).toBeNull();
  });
});

// ---------------------------------------------------------------------------
// Alias resolution
// ---------------------------------------------------------------------------

describe('planet type alias normalization', () => {
  const aliases: Array<[string, string]> = [
    ['lava',     'VOLCANIC'],
    ['tundra',   'ARCTIC'],
    ['frozen',   'ICE'],
    ['garden',   'TERRAN'],
    ['forest',   'JUNGLE'],
    ['paradise', 'TROPICAL'],
    ['station',  'ARTIFICIAL'],
    ['gas-giant','GAS_GIANT'],
  ];

  for (const [alias, canonical] of aliases) {
    it(`"${alias}" resolves to ${canonical}`, () => {
      const input = adaptLandedSceneToVistaInput({
        seedKey:    `alias-${alias}`,
        planetType: alias,
      });
      expect(input).not.toBeNull();
      expect(input!.planet.type).toBe(canonical);
    });
  }

  it('case-insensitive: "terran" and "TERRAN" both resolve', () => {
    const lower = adaptLandedSceneToVistaInput({ seedKey: 'ci-lower', planetType: 'terran' });
    const upper = adaptLandedSceneToVistaInput({ seedKey: 'ci-upper', planetType: 'TERRAN' });
    expect(lower!.planet.type).toBe('TERRAN');
    expect(upper!.planet.type).toBe('TERRAN');
  });
});

// ---------------------------------------------------------------------------
// Runtime boundary hardening — NaN / Infinity / wrong types
//
// The adapter is the untyped-runtime→engine boundary: cockpit data arrives
// without TypeScript guarantees.  These tests lock the "NEVER throw, ALWAYS
// a valid VistaInput or null" contract.
// ---------------------------------------------------------------------------

describe('runtime boundary hardening — non-string planetType must not throw', () => {
  const nonStrings: unknown[] = [42, 0, {}, [], null, true, false, Symbol('x')];

  for (const value of nonStrings) {
    it(`planetType=${String(value)} (${typeof value}) → null, no throw`, () => {
      expect(() => {
        const result = adaptLandedSceneToVistaInput({
          seedKey:    'hardening-type',
          planetType: value as unknown as string,
        });
        expect(result).toBeNull();
      }).not.toThrow();
    });
  }
});

describe('runtime boundary hardening — NaN / Infinity / out-of-range habitability', () => {
  const badValues = [NaN, Infinity, -Infinity, -100, 200, 1e9];

  for (const v of badValues) {
    it(`habitability=${v} → planet.habitability finite and in [0,100]`, () => {
      const input = adaptLandedSceneToVistaInput({
        seedKey:      `hardening-hab-${v}`,
        planetType:   'TERRAN',
        habitability: v,
      });
      expect(input).not.toBeNull();
      expect(Number.isFinite(input!.planet.habitability)).toBe(true);
      expect(input!.planet.habitability).toBeGreaterThanOrEqual(0);
      expect(input!.planet.habitability).toBeLessThanOrEqual(100);
    });
  }

  it('habitability=NaN → generateVista.invariants.ok===true (no NaN in model)', () => {
    const input = adaptLandedSceneToVistaInput({
      seedKey: 'hardening-hab-nan-pipeline', planetType: 'TERRAN', habitability: NaN,
    });
    const model = generateVista(input!);
    expect(model.invariants.ok).toBe(true);
    expect(Number.isFinite(model.desirability)).toBe(true);
    expect(Number.isFinite(model.layers.sky.starCount)).toBe(true);
    expect(Number.isFinite(model.lighting.bloom)).toBe(true);
    expect(Number.isFinite(model.lighting.colorGradeWarmth)).toBe(true);
  });

  it('habitability=200 → clamped to 100, pipeline still clean', () => {
    const input = adaptLandedSceneToVistaInput({
      seedKey: 'hardening-hab-200', planetType: 'JUNGLE', habitability: 200,
    });
    expect(input!.planet.habitability).toBe(100);
    expect(generateVista(input!).invariants.ok).toBe(true);
  });
});

describe('runtime boundary hardening — NaN / out-of-range citadelLevel', () => {
  const badLevels = [NaN, Infinity, -Infinity, -1, 6, 100];

  for (const v of badLevels) {
    it(`citadelLevel=${v} → site.citadelCeiling finite and in {1..5}`, () => {
      const input = adaptLandedSceneToVistaInput({
        seedKey:      `hardening-citadel-${v}`,
        planetType:   'TERRAN',
        citadelLevel: v,
      });
      expect(input).not.toBeNull();
      const cc = input!.site!.citadelCeiling;
      expect(Number.isFinite(cc)).toBe(true);
      expect(cc).toBeGreaterThanOrEqual(1);
      expect(cc).toBeLessThanOrEqual(5);
    });
  }

  it('citadelLevel=NaN → defaults to citadelCeiling=1', () => {
    const input = adaptLandedSceneToVistaInput({
      seedKey: 'hardening-citadel-nan', planetType: 'MOUNTAINOUS', citadelLevel: NaN,
    });
    expect(input!.site!.citadelCeiling).toBe(1);
  });

  it('citadelLevel=0 → citadelCeiling clamped to 1', () => {
    const input = adaptLandedSceneToVistaInput({
      seedKey: 'hardening-citadel-0', planetType: 'OCEANIC', citadelLevel: 0,
    });
    expect(input!.site!.citadelCeiling).toBe(1);
  });
});

describe('runtime boundary hardening — NaN orbitAu', () => {
  it('orbitAu=NaN → celestial.orbitAu is the finite default 0.5', () => {
    const input = adaptLandedSceneToVistaInput({
      seedKey: 'hardening-orbit-nan', planetType: 'TERRAN', orbitAu: NaN,
    });
    expect(Number.isFinite(input!.celestial.orbitAu)).toBe(true);
    expect(input!.celestial.orbitAu).toBe(0.5);
  });

  it('orbitAu=Infinity → celestial.orbitAu clamped to max finite value', () => {
    const input = adaptLandedSceneToVistaInput({
      seedKey: 'hardening-orbit-inf', planetType: 'BARREN', orbitAu: Infinity,
    });
    expect(Number.isFinite(input!.celestial.orbitAu)).toBe(true);
  });
});

describe('runtime boundary hardening — NaN / huge moons and siblingCount', () => {
  it('moons=NaN → no throw, no moons array', () => {
    expect(() => {
      const input = adaptLandedSceneToVistaInput({
        seedKey: 'hardening-moons-nan', planetType: 'TERRAN', moons: NaN,
      });
      expect(input!.celestial.moons).toBeUndefined();
    }).not.toThrow();
  });

  it('moons=Infinity → no throw, moons capped at 3', () => {
    const input = adaptLandedSceneToVistaInput({
      seedKey: 'hardening-moons-inf', planetType: 'TERRAN', moons: Infinity,
    });
    const count = input!.celestial.moons?.length ?? 0;
    expect(count).toBeLessThanOrEqual(3);
  });

  it('siblingCount=NaN → no throw, no siblings array', () => {
    expect(() => {
      const input = adaptLandedSceneToVistaInput({
        seedKey: 'hardening-sib-nan', planetType: 'TERRAN', siblingCount: NaN,
      });
      expect(input!.celestial.siblings).toBeUndefined();
    }).not.toThrow();
  });

  it('siblingCount=99999 → no throw, siblings capped at 2', () => {
    const input = adaptLandedSceneToVistaInput({
      seedKey: 'hardening-sib-huge', planetType: 'OCEANIC', siblingCount: 99999,
    });
    const count = input!.celestial.siblings?.length ?? 0;
    expect(count).toBeLessThanOrEqual(2);
  });
});

// ---------------------------------------------------------------------------
// Field defaults and ranges
// ---------------------------------------------------------------------------

describe('field defaults and contract ranges', () => {
  it('orbitAu falls back to 0.5 when absent', () => {
    const input = adaptLandedSceneToVistaInput({ seedKey: 'defaults-orbit', planetType: 'TERRAN' });
    expect(input!.celestial.orbitAu).toBe(0.5);
  });

  it('habitability defaults to 50 when absent', () => {
    const input = adaptLandedSceneToVistaInput({ seedKey: 'defaults-hab', planetType: 'DESERT' });
    expect(input!.planet.habitability).toBe(50);
  });

  it('citadelCeiling is at least 1 when citadelLevel is 0', () => {
    const input = adaptLandedSceneToVistaInput({
      seedKey: 'defaults-citadel0', planetType: 'TERRAN', citadelLevel: 0,
    });
    expect(input!.site!.citadelCeiling).toBeGreaterThanOrEqual(1);
  });

  it('citadelCeiling matches clamped citadelLevel', () => {
    const input = adaptLandedSceneToVistaInput({
      seedKey: 'defaults-citadel4', planetType: 'MOUNTAINOUS', citadelLevel: 4,
    });
    expect(input!.site!.citadelCeiling).toBe(4);
  });

  it('usableSlots is within the contract range 6–32', () => {
    for (const type of ['GAS_GIANT', 'TERRAN', 'VOLCANIC', 'JUNGLE'] as const) {
      const input = adaptLandedSceneToVistaInput({ seedKey: `range-slots-${type}`, planetType: type });
      const slots = input!.site!.usableSlots;
      expect(slots).toBeGreaterThanOrEqual(6);
      expect(slots).toBeLessThanOrEqual(32);
    }
  });

  it('absent star falls back to G_YELLOW defaults', () => {
    const input = adaptLandedSceneToVistaInput({
      seedKey: 'defaults-star', planetType: 'OCEANIC', star: null,
    });
    expect(input!.celestial.star.kind).toBe('G_YELLOW');
    expect(input!.celestial.star.color).toBe('#fff4d0');
  });

  it('moons: 0 → no moons array', () => {
    const input = adaptLandedSceneToVistaInput({ seedKey: 'no-moons', planetType: 'BARREN', moons: 0 });
    expect(input!.celestial.moons).toBeUndefined();
  });

  it('moons: 2 → two moon entries', () => {
    const input = adaptLandedSceneToVistaInput({ seedKey: 'two-moons', planetType: 'TERRAN', moons: 2 });
    expect(input!.celestial.moons).toHaveLength(2);
  });

  it('moons cap at 3 even if more are provided', () => {
    const input = adaptLandedSceneToVistaInput({ seedKey: 'cap-moons', planetType: 'TERRAN', moons: 99 });
    expect(input!.celestial.moons!.length).toBeLessThanOrEqual(3);
  });

  it('contractVersion is 1', () => {
    const input = adaptLandedSceneToVistaInput({ seedKey: 'contract-ver', planetType: 'ARCTIC' });
    expect(input!.contractVersion).toBe(1);
  });
});

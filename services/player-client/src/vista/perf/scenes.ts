/**
 * Vista Engine — Perf benchmark reference scenes  (PERF-HARNESS sub-part (b))
 *
 * 12 fixed VistaInput literals = 6 biome "critic seeds" (WO-PERF-HARNESS) ×
 * 2 load states.  Same seed used for both load states of a biome — CALM and
 * EXTREME are the SAME world under different scene circumstances, not
 * different worlds, so any frameMs delta between them isolates the cost of
 * the extra draw work rather than archetype/palette variance.
 *
 *   VOLCANIC    — Pisces VI      seed '225'
 *   ICE         — Procyon Minor  seed '292'
 *   OCEANIC     — Antares VI     seed '98'
 *   BARREN      — Polaris-7      seed '58'
 *   TERRAN      — New Earth      seed '1'
 *   MOUNTAINOUS — Deneb          seed '208'
 *
 * Load states drive the dimensions that actually cost renderer time:
 *   CALM    — site.hazards: [] (zero particle emission), no moons/rings/
 *             siblings/nebula.  Floor case: sky + sun + terrain + water +
 *             deposit markers only.
 *   EXTREME — 2 severe hazards (one named, forcing its glyph into the sky
 *             per contract.ts's site.hazards doc) → particle emission +
 *             hazard overlays; 2 moons (one ringed) + rings + 2 siblings +
 *             nebula wash → maximum celestial draw count; one extra deposit
 *             → more feature markers.  Ceiling case: every optional layer
 *             the pipeline can currently emit, active at once.
 *
 * planet.* base fields (habitability/atmosphere/temperature/waterCoverage)
 * are held CONSTANT between CALM/EXTREME of the same biome — varying them
 * would change desirability/bloom and conflate "different load" with
 * "different world quality".  Values are drawn from vista/lab/VistaProof.tsx's
 * FIXED_INPUTS for the matching PlanetType (the established per-type visual
 * fixture set) so these scenes render exactly as realistic as the existing
 * proof harness, just under this WO's named seeds instead of VistaProof's own.
 *
 * Pure data — no DOM, no pipeline calls.  Safe to import from vitest or a
 * browser-driven benchmark alike.
 */

import type { VistaInput, PlanetType } from '../contract';

export type LoadState = 'CALM' | 'EXTREME';

export interface PerfScene {
  /** Stable id, e.g. 'VOLCANIC_CALM'. */
  id: string;
  planetType: PlanetType;
  /** Human-readable in-game location name (table/log labeling only). */
  locationName: string;
  load: LoadState;
  input: VistaInput;
}

// ---------------------------------------------------------------------------
// Per-biome base fields (shared between a biome's CALM and EXTREME variant)
// ---------------------------------------------------------------------------

interface BiomeBase {
  planetType: PlanetType;
  locationName: string;
  seed: string;
  planet: VistaInput['planet'];
  celestialBase: Pick<VistaInput['celestial'], 'star' | 'orbitAu' | 'phaseDeg' | 'rotationPeriodHours' | 'axialTiltDeg'>;
  siteBase: Pick<VistaInput['site'] & object, 'shape' | 'usableSlots' | 'citadelCeiling' | 'energy' | 'deposits'>;
  /** Extra deposit added only in the EXTREME variant (more feature markers). */
  extraDeposit: { kind: string; richness: number };
  /** The two hazards used in the EXTREME variant (first one named — forces sky visual). */
  extremeHazards: { kind: string; severity: number; named: boolean }[];
}

const BIOMES: readonly BiomeBase[] = [
  {
    planetType: 'VOLCANIC',
    locationName: 'Pisces VI',
    seed: '225',
    planet: {
      type: 'VOLCANIC', habitability: 12,
      atmosphere: { present: true, kind: 'sulfurous', density: 0.90 },
      nativeLife: 0.10, temperature: 0.88, waterCoverage: 0.05,
    },
    celestialBase: {
      star: { kind: 'M_DWARF', color: '#ff8060' },
      orbitAu: 0.3, phaseDeg: 60, rotationPeriodHours: 200, axialTiltDeg: 2,
    },
    siteBase: {
      shape: 'COMPACT', usableSlots: 8, citadelCeiling: 1,
      energy: { source: 'GEOTHERMAL', tier: 4, magnitude: 0.95 },
      deposits: [{ kind: 'mineral', richness: 0.78 }, { kind: 'gas', richness: 0.60 }],
    },
    extraDeposit: { kind: 'crystal', richness: 0.50 },
    extremeHazards: [
      { kind: 'lava', severity: 0.95, named: true },
      { kind: 'seismic', severity: 0.85, named: false },
    ],
  },
  {
    planetType: 'ICE',
    locationName: 'Procyon Minor',
    seed: '292',
    planet: {
      type: 'ICE', habitability: 18,
      atmosphere: { present: true, kind: null, density: 0.40 },
      nativeLife: 0.08, temperature: -0.85, waterCoverage: 0.72,
    },
    celestialBase: {
      star: { kind: 'K_ORANGE', color: '#ffcc80' },
      orbitAu: 2.2, phaseDeg: 60, rotationPeriodHours: 48, axialTiltDeg: 5,
    },
    siteBase: {
      shape: 'COMPACT', usableSlots: 10, citadelCeiling: 2,
      energy: { source: 'GEOTHERMAL', tier: 1, magnitude: 0.45 },
      deposits: [{ kind: 'ice', richness: 0.90 }, { kind: 'ore', richness: 0.35 }],
    },
    extraDeposit: { kind: 'mineral', richness: 0.40 },
    extremeHazards: [
      { kind: 'snow', severity: 0.90, named: true },
      { kind: 'seismic', severity: 0.50, named: false },
    ],
  },
  {
    planetType: 'OCEANIC',
    locationName: 'Antares VI',
    seed: '98',
    planet: {
      type: 'OCEANIC', habitability: 70,
      atmosphere: { present: true, kind: null, density: 0.78 },
      nativeLife: 0.65, temperature: 0.20, waterCoverage: 0.90,
    },
    celestialBase: {
      star: { kind: 'G_YELLOW', color: '#fff4d0' },
      orbitAu: 1.05, phaseDeg: 60, rotationPeriodHours: 26, axialTiltDeg: 12,
    },
    siteBase: {
      shape: 'ENGINEERED', usableSlots: 14, citadelCeiling: 3,
      energy: { source: 'TIDAL', tier: 3, magnitude: 0.80 },
      deposits: [{ kind: 'gas', richness: 0.55 }, { kind: 'organic', richness: 0.72 }],
    },
    extraDeposit: { kind: 'crystal', richness: 0.35 },
    extremeHazards: [
      { kind: 'flood', severity: 0.90, named: true },
      { kind: 'storm', severity: 0.80, named: false },
    ],
  },
  {
    planetType: 'BARREN',
    locationName: 'Polaris-7',
    seed: '58',
    planet: {
      type: 'BARREN', habitability: 5,
      atmosphere: { present: false, kind: null, density: 0.0 },
      nativeLife: 0.0, temperature: 0.05, waterCoverage: 0.0,
    },
    celestialBase: {
      star: { kind: 'G_YELLOW', color: '#fff4d0' },
      orbitAu: 1.8, phaseDeg: 60, rotationPeriodHours: 60, axialTiltDeg: 1,
    },
    siteBase: {
      shape: 'COMPACT', usableSlots: 8, citadelCeiling: 2,
      energy: { source: 'SOLAR', tier: 1, magnitude: 0.35 },
      deposits: [{ kind: 'ore', richness: 0.65 }, { kind: 'mineral', richness: 0.50 }],
    },
    extraDeposit: { kind: 'gas', richness: 0.30 },
    extremeHazards: [
      { kind: 'radiation', severity: 0.85, named: true },
      { kind: 'impact', severity: 0.60, named: false },
    ],
  },
  {
    planetType: 'TERRAN',
    locationName: 'New Earth',
    seed: '1',
    planet: {
      type: 'TERRAN', habitability: 85,
      atmosphere: { present: true, kind: null, density: 0.70 },
      nativeLife: 0.55, temperature: 0.10, waterCoverage: 0.55,
    },
    celestialBase: {
      star: { kind: 'G_YELLOW', color: '#fff4d0' },
      orbitAu: 1.0, phaseDeg: 60, rotationPeriodHours: 24, axialTiltDeg: 23,
    },
    siteBase: {
      shape: 'SPRAWLING', usableSlots: 18, citadelCeiling: 3,
      energy: { source: 'SOLAR', tier: 2, magnitude: 0.65 },
      deposits: [{ kind: 'ore', richness: 0.70 }, { kind: 'food', richness: 0.80 }],
    },
    extraDeposit: { kind: 'crystal', richness: 0.45 },
    extremeHazards: [
      { kind: 'storm', severity: 0.85, named: true },
      { kind: 'flood', severity: 0.60, named: false },
    ],
  },
  {
    planetType: 'MOUNTAINOUS',
    locationName: 'Deneb',
    seed: '208',
    planet: {
      type: 'MOUNTAINOUS', habitability: 52,
      atmosphere: { present: true, kind: null, density: 0.55 },
      nativeLife: 0.30, temperature: -0.10, waterCoverage: 0.18,
    },
    celestialBase: {
      star: { kind: 'K_ORANGE', color: '#ffd090' },
      orbitAu: 1.1, phaseDeg: 60, rotationPeriodHours: 30, axialTiltDeg: 30,
    },
    siteBase: {
      shape: 'TERRACED', usableSlots: 12, citadelCeiling: 4,
      energy: { source: 'GEOTHERMAL', tier: 2, magnitude: 0.60 },
      deposits: [{ kind: 'ore', richness: 0.85 }, { kind: 'crystal', richness: 0.62 }],
    },
    extraDeposit: { kind: 'mineral', richness: 0.55 },
    extremeHazards: [
      { kind: 'seismic', severity: 0.85, named: true },
      { kind: 'snow', severity: 0.55, named: false },
    ],
  },
];

// ---------------------------------------------------------------------------
// Scene assembly
// ---------------------------------------------------------------------------

function buildScene(biome: BiomeBase, load: LoadState): PerfScene {
  const input: VistaInput = {
    contractVersion: 1,
    seed: biome.seed,
    planet: biome.planet,
    celestial: load === 'CALM'
      ? { ...biome.celestialBase }
      : {
          ...biome.celestialBase,
          rings: true,
          moons: [
            { sizeClass: 3, phaseDeg: 90, hasRings: true },
            { sizeClass: 2, phaseDeg: 220, hasRings: false },
          ],
          siblings: [
            { kind: 'GAS_GIANT', sizeClass: 3, phaseDeg: 140, hue: 30, sat: 0.55 },
            { kind: 'TERRAN', sizeClass: 2, phaseDeg: 300, hue: 200, sat: 0.40 },
          ],
          nebula: { hue: 260, density: 0.6 },
        },
    site: {
      shape: biome.siteBase.shape,
      usableSlots: biome.siteBase.usableSlots,
      citadelCeiling: biome.siteBase.citadelCeiling,
      energy: biome.siteBase.energy,
      deposits: load === 'CALM'
        ? biome.siteBase.deposits
        : [...biome.siteBase.deposits, biome.extraDeposit],
      hazards: load === 'CALM' ? [] : biome.extremeHazards,
    },
  };

  return {
    id: `${biome.planetType}_${load}`,
    planetType: biome.planetType,
    locationName: biome.locationName,
    load,
    input,
  };
}

/** All 12 reference scenes: 6 biomes × (CALM, EXTREME), in WO declaration order. */
export const PERF_SCENES: readonly PerfScene[] = BIOMES.flatMap((biome) => [
  buildScene(biome, 'CALM'),
  buildScene(biome, 'EXTREME'),
]);

// ---------------------------------------------------------------------------
// Budget constants (consumed by benchmark.ts's classification + PerfOverlay)
// ---------------------------------------------------------------------------

/** Target frame budget in ms (≈143fps headroom) — the "green" threshold. */
export const TARGET_FRAME_MS = 7;
/** Floor frame budget in ms (30fps) — below this the scene is failing outright. */
export const FLOOR_FRAME_MS = 33;

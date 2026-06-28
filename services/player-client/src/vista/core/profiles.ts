/**
 * Vista Engine — Planet Profiles
 *
 * Per-type data tables: base palette anchors, archetype variants, terrain
 * noise bounds, coherence guards, and shape weights.  Adding a new planet
 * type = one new entry in PROFILES; no pipeline code changes required.
 *
 * MVP entries: TERRAN + VOLCANIC.  All 12 types land in Phase 1.
 *
 * Palette anchors are sourced from the shipped landedPalette() in
 * SolarSystemViewscreen.tsx (L1811).  Deviations are noted per profile.
 */

import { PlanetType, RGB, GridShape } from '../contract';

// ---------------------------------------------------------------------------
// Supporting type aliases  (match the relevant VistaModel union strings exactly)
// ---------------------------------------------------------------------------

/** Matches VistaModel terrain.groundPlane.material union. */
export type GroundMaterial =
  | 'rock' | 'sand' | 'ice' | 'soil' | 'basalt' | 'regolith' | 'plating' | 'canopy';

/** Matches VistaModel atmosphere.clouds.kind union. */
export type CloudKind =
  | 'cumulus' | 'ash' | 'dust' | 'cirrus' | 'banded' | 'none';

/** Matches VistaModel layers.water.type union. */
export type WaterType =
  | 'ocean' | 'coastal' | 'tidal-flat' | 'frozen' | 'lava';

/** Matches VistaModel terrain.landmarks.kind union. */
export type LandmarkKind =
  | 'cone' | 'caldera' | 'arch' | 'mesa' | 'crater' | 'spire' | 'canyon' | 'glacier';

// ---------------------------------------------------------------------------
// TerrainRecipe
// ---------------------------------------------------------------------------

/**
 * Noise bounds for one archetype's terrain generation stage.
 * All pairs are [min, max] inclusive; the pipeline samples uniformly within
 * each range via the named 'terrain' PRNG sub-stream.
 */
export interface TerrainRecipe {
  /** Integer number of parallax ridge strata to generate. */
  ridgeCount: [number, number];
  /** Normalized Y horizon position (0 = top of canvas, 1 = bottom). */
  horizonY: [number, number];
  /**
   * Strata noise roughness fraction (0 = smooth sine-like, 1 = jagged).
   * Controls the amplitude of the high-frequency micro-jitter pass.
   */
  roughness: [number, number];
  /** Ridge peak amplitude as a fraction of canvas height (0–1). */
  amplitude: [number, number];
  /** Ground-plane surface material assigned to this archetype. */
  groundMaterial: GroundMaterial;
}

// ---------------------------------------------------------------------------
// CoherenceGuard
// ---------------------------------------------------------------------------

/**
 * Per-type envelope that keeps any seed unmistakably this planet type.
 * Two different seeds may look dramatically different; they must still read
 * as the same type.  (BRIEF §2.4 coherence guards.)
 */
export interface CoherenceGuard {
  /**
   * Per-channel max jitter in 0–255 integer space (approximate ΔE).
   * Palette draws may shift at most this many units per channel from the
   * base anchor.
   */
  deltaEEnvelope: number;
  /**
   * Hard amplitude band across ALL archetypes of this type [min, max].
   * Ensures a TERRAN world never acquires VOLCANIC jaggedness.
   */
  amplitudeBand: [number, number];
  /** Hard roughness band across ALL archetypes [min, max]. */
  roughnessBand: [number, number];
  /**
   * Landmark kinds allowed anywhere in this planet type.
   * Each ArchetypeEntry further restricts to its own subset — archetype-level
   * locks prevent e.g. a glacier appearing in a lava plain.
   */
  landmarkAllowList: ReadonlyArray<LandmarkKind>;
  /** Cloud kinds that may appear for this type. */
  cloudAllowList: ReadonlyArray<CloudKind>;
  /** Water types that may appear for this type (empty = no water ever). */
  waterAllowList: ReadonlyArray<WaterType>;
}

// ---------------------------------------------------------------------------
// ArchetypeEntry
// ---------------------------------------------------------------------------

/** One macro scene variant within a planet type. */
export interface ArchetypeEntry {
  /**
   * Stable identifier, e.g. 'volcanic.caldera-rim'.
   * Written verbatim to VistaModel.archetype — never change for a shipped entry.
   */
  id: string;
  /** Relative pick weight (higher = more common for this type). */
  weight: number;
  /** Noise bounds for terrain generation in this archetype. */
  terrain: TerrainRecipe;
  /**
   * Landmark kinds unlocked in this archetype; must be a subset of the
   * type-level coherence.landmarkAllowList.
   */
  landmarks: ReadonlyArray<LandmarkKind>;
}

// ---------------------------------------------------------------------------
// BasePalette
// ---------------------------------------------------------------------------

/**
 * sRGB anchor colors for a planet type.
 * Pipeline applies per-seed jitter within coherence.deltaEEnvelope before
 * writing to VistaModel.  Sourced from the shipped landedPalette() per type.
 */
export interface BasePalette {
  skyTop: RGB;
  skyHorizon: RGB;
  /** Atmospheric scatter/glow band color at the horizon. */
  scatterBand: RGB;
  /** Far parallax ridge (faintest / most atmospherically hazy). */
  ridgeFar: RGB;
  ridgeMid: RGB;
  /** Near parallax ridge (darkest / closest to viewer). */
  ridgeNear: RGB;
  /** Ground-plane base surface color. */
  surface: RGB;
  /** Flora tint at maximum habitability (lush, vivid). */
  floraMax: RGB;
  /** Flora tint at zero habitability (sparse, muted). */
  floraMin: RGB;
  /** Optional water body base color (omit for dry types). */
  water?: RGB;
  /** Optional foam / wave-crest color. */
  foam?: RGB;
  /** Default accent for this type: deposit glow / energy signature. */
  accent: RGB;
}

// ---------------------------------------------------------------------------
// PlanetProfile
// ---------------------------------------------------------------------------

/** The complete per-type data record consumed by the pipeline. */
export interface PlanetProfile {
  type: PlanetType;
  basePalette: BasePalette;
  coherence: CoherenceGuard;
  /** 3–6 macro archetypes; the seeded 'archetype' stream picks one. */
  archetypes: ReadonlyArray<ArchetypeEntry>;
  /**
   * Default water mode.  'none' → no water layer emitted even if the
   * input's waterCoverage > 0 (surface type doesn't support it).
   */
  water: 'none' | 'coastal' | 'ocean' | 'frozen' | 'lava';
  /** Flora scatter sprite kinds for this type. */
  floraKinds: ReadonlyArray<string>;
  /** Rock / non-flora scatter kinds for this type. */
  rockKinds: ReadonlyArray<string>;
  /** Cloud kind to emit when atmosphere is present (absent = 'none'). */
  defaultCloud: CloudKind;
  /**
   * Relative GridShape weights (M12 moderate-correlation from BRIEF §2.6).
   * Shapes absent from the map have effective weight 0.
   */
  shapeWeights: Partial<Record<GridShape, number>>;
  /** Named hazard kind → VistaModel overlay visual string for this type. */
  hazardVisuals: Record<string, string>;
  /** Deposit kind → VistaModel depositMarker visual string for this type. */
  depositVisuals: Record<string, string>;
}

// ---------------------------------------------------------------------------
// TERRAN profile
// ---------------------------------------------------------------------------
// Palette sourced from landedPalette() 'TERRAN' case (SolarSystemViewscreen.tsx
// L1853–L1859).  scatterBand derived from shipped glow rgba(150,230,200,0.4).
// surface, floraMin, water, foam, accent are canonical (not in shipped palette).
// ---------------------------------------------------------------------------

const TERRAN_PROFILE: PlanetProfile = {
  type: 'TERRAN',

  basePalette: {
    skyTop:      [4,   18,  31],   // '#04121f' — shipped
    skyHorizon:  [47,  140, 116],  // '#2f8c74' — shipped
    scatterBand: [150, 230, 200],  // from shipped glow rgba
    ridgeFar:    [20,   70,  60],  // '#14463c' — shipped
    ridgeMid:    [13,   47,  41],  // '#0d2f29' — shipped
    ridgeNear:   [6,    26,  22],  // '#061a16' — shipped
    surface:     [38,   72,  44],  // dark green loam (canonical)
    floraMax:    [90,  210, 130],  // shipped flora '90, 210, 130'
    floraMin:    [55,  100,  65],  // sparse/pale (canonical)
    water:       [28,   88, 128],  // coastal blue-grey (canonical)
    foam:        [175, 218, 210],  // seafoam (canonical)
    accent:      [80,  220, 150],  // teal mineral glow (canonical)
  },

  coherence: {
    deltaEEnvelope: 18,
    amplitudeBand:  [0.12, 0.55],
    roughnessBand:  [0.08, 0.42],
    landmarkAllowList: ['cone', 'arch', 'mesa', 'crater', 'canyon'],
    cloudAllowList:    ['cumulus', 'cirrus', 'none'],
    waterAllowList:    ['coastal', 'ocean', 'tidal-flat'],
  },

  archetypes: [
    {
      id: 'terran.highland-meadow',
      weight: 30,
      terrain: {
        ridgeCount:     [3, 5],
        horizonY:       [0.42, 0.50],
        roughness:      [0.14, 0.30],
        amplitude:      [0.24, 0.44],
        groundMaterial: 'soil',
      },
      landmarks: ['cone', 'arch', 'mesa'],
    },
    {
      id: 'terran.coastal-plain',
      weight: 25,
      terrain: {
        ridgeCount:     [2, 3],
        horizonY:       [0.52, 0.62],
        roughness:      [0.08, 0.18],
        amplitude:      [0.12, 0.28],
        groundMaterial: 'soil',
      },
      landmarks: ['arch', 'mesa'],
    },
    {
      id: 'terran.river-delta',
      weight: 20,
      terrain: {
        ridgeCount:     [3, 4],
        horizonY:       [0.46, 0.56],
        roughness:      [0.10, 0.22],
        amplitude:      [0.18, 0.34],
        groundMaterial: 'soil',
      },
      landmarks: ['arch', 'mesa', 'cone'],
    },
    {
      id: 'terran.canyon-vale',
      weight: 15,
      terrain: {
        ridgeCount:     [3, 5],
        horizonY:       [0.38, 0.48],
        roughness:      [0.24, 0.40],
        amplitude:      [0.34, 0.54],
        groundMaterial: 'rock',
      },
      landmarks: ['canyon', 'arch', 'mesa', 'crater'],
    },
    {
      id: 'terran.temperate-forest',
      weight: 10,
      terrain: {
        ridgeCount:     [4, 5],
        horizonY:       [0.40, 0.52],
        roughness:      [0.16, 0.32],
        amplitude:      [0.26, 0.48],
        groundMaterial: 'canopy',
      },
      landmarks: ['cone', 'arch'],
    },
  ],

  water:        'coastal',
  floraKinds:   ['grass-tuft', 'fern-cluster', 'broad-tree', 'shrub-patch'],
  rockKinds:    ['boulder', 'stone-scatter'],
  defaultCloud: 'cumulus',

  shapeWeights: {
    SPRAWLING: 70,
    TERRACED:  30,
  },

  hazardVisuals: {
    flood:      'flood-zone',
    storm:      'storm-cell',
    megafauna:  'megafauna-marker',
    radiation:  'radiation-haze',
  },

  depositVisuals: {
    ore:      'ore-vein',
    crystal:  'crystal',
    biomass:  'biolumin',
    gas:      'gas-seep',
    water:    'hydrocarbon-pool',
  },
};

// ---------------------------------------------------------------------------
// VOLCANIC profile
// ---------------------------------------------------------------------------
// Palette sourced from landedPalette() 'VOLCANIC' case (L1835–L1843).
// scatterBand from shipped haze '255, 90, 20'.
// ridges carry the shipped warm→dark depth ramp (L1838–1843 comment).
// surface, floraMin, water(lava), foam(ash), accent are canonical.
// ---------------------------------------------------------------------------

const VOLCANIC_PROFILE: PlanetProfile = {
  type: 'VOLCANIC',

  basePalette: {
    skyTop:      [18,    3,   5],  // '#120305' — shipped
    skyHorizon:  [138,  46,  10],  // '#8a2e0a' — shipped
    scatterBand: [255,  90,  20],  // from shipped haze '255, 90, 20'
    ridgeFar:    [58,   21,  16],  // '#3a1510' — warmer/lighter (distance)
    ridgeMid:    [38,   16,   8],  // '#261008' — shipped
    ridgeNear:   [20,    8,   6],  // '#140806' — near-black (closest)
    surface:     [22,   10,   6],  // dark basalt (canonical)
    floraMax:    [90,  120,  70],  // sparse heat-tolerant growth (shipped flora)
    floraMin:    [50,   58,  35],  // near-barren ash scrub (canonical)
    water:       [200,  58,  10],  // lava-flow orange-red (canonical)
    foam:        [115,  78,  55],  // ash/pumice surface (canonical)
    accent:      [255,  90,  20],  // lava glow (matches shipped glow rgba)
  },

  coherence: {
    deltaEEnvelope: 15,
    amplitudeBand:  [0.14, 0.72],
    roughnessBand:  [0.14, 0.56],
    landmarkAllowList: ['cone', 'caldera', 'crater', 'spire'],
    cloudAllowList:    ['ash', 'none'],
    waterAllowList:    ['lava'],
  },

  archetypes: [
    {
      id: 'volcanic.caldera-rim',
      weight: 28,
      terrain: {
        ridgeCount:     [3, 5],
        horizonY:       [0.38, 0.50],
        roughness:      [0.28, 0.44],
        amplitude:      [0.34, 0.60],
        groundMaterial: 'basalt',
      },
      landmarks: ['caldera', 'cone', 'crater'],
    },
    {
      id: 'volcanic.lava-plain',
      weight: 24,
      terrain: {
        ridgeCount:     [2, 3],
        horizonY:       [0.50, 0.62],
        roughness:      [0.14, 0.26],
        amplitude:      [0.14, 0.28],
        groundMaterial: 'basalt',
      },
      landmarks: ['crater', 'cone'],
    },
    {
      id: 'volcanic.obsidian-ridge',
      weight: 26,
      terrain: {
        ridgeCount:     [4, 5],
        horizonY:       [0.34, 0.48],
        roughness:      [0.36, 0.54],
        amplitude:      [0.44, 0.70],
        groundMaterial: 'basalt',
      },
      landmarks: ['spire', 'cone', 'caldera'],
    },
    {
      id: 'volcanic.ash-fields',
      weight: 22,
      terrain: {
        ridgeCount:     [3, 4],
        horizonY:       [0.44, 0.58],
        roughness:      [0.20, 0.34],
        amplitude:      [0.18, 0.38],
        groundMaterial: 'rock',
      },
      landmarks: ['crater', 'cone'],
    },
  ],

  water:        'lava',
  floraKinds:   [],                // no flora; rock/crystal scatter only
  rockKinds:    ['lava-rock', 'obsidian-shard', 'pumice-scatter'],
  defaultCloud: 'ash',

  shapeWeights: {
    COMPACT:   55,
    IRREGULAR: 35,
    TERRACED:  10,
  },

  hazardVisuals: {
    'magma-surge': 'lava-flow',
    seismic:       'fault-line',
    'toxic-gas':   'radiation-haze',
    radiation:     'radiation-haze',
  },

  depositVisuals: {
    ore:        'ore-vein',
    crystal:    'crystal',
    geothermal: 'thermal-vent',
    gas:        'gas-seep',
  },
};

// ---------------------------------------------------------------------------
// Generic fallback
// ---------------------------------------------------------------------------
// Used for any PlanetType not yet in PROFILES (Phase 1 will fill in the
// remaining 10).  Draws from the shipped violet-dusk default branch in
// landedPalette() (L1881–1890) — the same path used for unknown types there.
// ---------------------------------------------------------------------------

const GENERIC_PROFILE: PlanetProfile = {
  type: 'BARREN', // closest shipped treatment

  basePalette: {
    skyTop:      [18,    8,  34],  // '#120822' violet-dusk shipped
    skyHorizon:  [106,  74, 138],  // '#6a4a8a' shipped
    scatterBand: [170, 120, 240],  // shipped haze '170, 120, 240'
    ridgeFar:    [58,   42,  79],  // '#3a2a4f' shipped
    ridgeMid:    [36,   26,  51],  // '#241a33' shipped
    ridgeNear:   [18,   12,  28],  // '#120c1c' shipped
    surface:     [28,   18,  42],
    floraMax:    [150, 130, 190],  // shipped flora '150, 130, 190'
    floraMin:    [80,   70, 100],
    accent:      [140, 100, 200],
  },

  coherence: {
    deltaEEnvelope: 20,
    amplitudeBand:  [0.12, 0.60],
    roughnessBand:  [0.10, 0.50],
    landmarkAllowList: ['crater', 'mesa', 'arch', 'cone', 'spire', 'canyon', 'caldera', 'glacier'],
    cloudAllowList:    ['cumulus', 'ash', 'dust', 'cirrus', 'banded', 'none'],
    waterAllowList:    ['ocean', 'coastal', 'tidal-flat', 'frozen', 'lava'],
  },

  archetypes: [
    {
      id: 'generic.unknown-terrain',
      weight: 1,
      terrain: {
        ridgeCount:     [3, 4],
        horizonY:       [0.44, 0.54],
        roughness:      [0.18, 0.32],
        amplitude:      [0.22, 0.40],
        groundMaterial: 'regolith',
      },
      landmarks: ['crater', 'mesa'],
    },
  ],

  water:        'none',
  floraKinds:   [],
  rockKinds:    ['boulder', 'stone-scatter'],
  defaultCloud: 'none',

  shapeWeights: {
    IRREGULAR: 60,
    COMPACT:   40,
  },

  hazardVisuals:  {},
  depositVisuals: {
    ore:     'ore-vein',
    crystal: 'crystal',
    gas:     'gas-seep',
  },
};

// ---------------------------------------------------------------------------
// Profile registry + lookup
// ---------------------------------------------------------------------------

const PROFILES: Partial<Record<PlanetType, PlanetProfile>> = {
  TERRAN:   TERRAN_PROFILE,
  VOLCANIC: VOLCANIC_PROFILE,
};

/**
 * Look up the profile for a planet type.  Falls back to GENERIC_PROFILE for
 * any type not yet implemented, so the engine never throws on unknown types
 * (BRIEF §2.2 degradation rule: "unknown PlanetType → generic fallback").
 */
export function getProfile(type: PlanetType): PlanetProfile {
  return PROFILES[type] ?? GENERIC_PROFILE;
}

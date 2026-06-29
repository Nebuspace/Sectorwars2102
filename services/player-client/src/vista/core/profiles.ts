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
// TypeGrade — per-type "film stock" for the post-process compositor
// ---------------------------------------------------------------------------

/**
 * Per-type color-grade parameters applied by the post-process compositor.
 * Each field is optional; absent values fall back to post.ts defaults.
 * Think of each entry as the "film stock" that makes a TERRAN feel lush-golden,
 * a BARREN feel harsh-silver, a VOLCANIC feel scorched-orange, etc.
 */
export interface TypeGrade {
  /**
   * Additive warmth bias stacked on top of model.lighting.colorGradeWarmth.
   * -1 (push harder cold) … +1 (push harder warm).
   */
  warmthBias: number;

  /**
   * Vignette darkness at the frame edges (0 = none, 1 = full black ring).
   * Absent → post.ts default of 0.55.
   * Use higher values for dramatic/hostile types; lower for open/airy worlds.
   */
  vignetteStrength?: number;

  /**
   * Film-grain intensity multiplier (1 = default scaling; 0 = disable grain;
   * 2 = double grain).  Absent → 1.  Base intensity is already inverse to
   * desirability; this scales that computed value.
   */
  grainScale?: number;
}

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

  /**
   * Optional warm secondary accent (sodium/amber).
   * Absent for all natural types.  Set only for ARTIFICIAL, where it provides
   * a two-tone contrast pair: cold conduit (accent) + warm window-light (accentWarm).
   */
  accentWarm?: RGB;
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
  /**
   * Terrain rendering mode.  Absent → 'surface'.
   * 'cloud-deck' → GAS_GIANT: banded cloud horizon, no solid terrain or ground plane.
   * 'plating'    → ARTIFICIAL: flat engineered substrate.
   */
  terrainMode?: 'surface' | 'cloud-deck' | 'plating';
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

  /**
   * Optional emissive window/signage grid parameters.
   * Absent for all natural types.  Set only for ARTIFICIAL: the renderer
   * draws a deterministic seed-driven grid of lit/dark cells on the plating
   * surface and on spire structures.
   * color: sRGB of the window glow; density: fraction of cells lit (0–1).
   */
  emissive?: { color: RGB; density: number };

  /**
   * Per-type "film stock" for the post-process compositor.
   * Absent → post.ts defaults (warmthBias=0, vignetteStrength=0.55, grainScale=1).
   * Set these to give each planet type a distinct cinematic character.
   */
  grade?: TypeGrade;
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

  // Warm-golden film stock: lush highlights, gentle vignette, minimal grain.
  grade: { warmthBias: 0.15, vignetteStrength: 0.45, grainScale: 0.7 },
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

  // Scorched film stock: pushed warm (embers/magma), deep vignette, heavy grain.
  grade: { warmthBias: 0.30, vignetteStrength: 0.65, grainScale: 1.5 },
};

// ---------------------------------------------------------------------------
// OCEANIC profile
// ---------------------------------------------------------------------------
// Palette sourced from landedPalette() 'OCEANIC' case (L1860–L1866).
// scatterBand from shipped haze '110, 190, 220'.
// surface, water, foam, accent are canonical.
// ---------------------------------------------------------------------------

const OCEANIC_PROFILE: PlanetProfile = {
  type: 'OCEANIC',

  basePalette: {
    skyTop:      [3,   16,  31],   // '#03101f' — shipped
    skyHorizon:  [42,  127, 158],  // '#2a7f9e' — shipped
    scatterBand: [110, 190, 220],  // from shipped haze '110, 190, 220'
    ridgeFar:    [15,  63,  85],   // '#0f3f55' — shipped
    ridgeMid:    [10,  43,  60],   // '#0a2b3c' — shipped
    ridgeNear:   [5,   24,  36],   // '#051824' — shipped
    surface:     [8,   38,  55],   // dark seafloor rock/sediment (canonical)
    floraMax:    [80,  200, 160],  // shipped flora '80, 200, 160'
    floraMin:    [45,  100,  80],  // sparse seagrass (canonical)
    water:       [28,  95,  145],  // open deep ocean (canonical)
    foam:        [200, 232, 240],  // white seafoam (canonical)
    accent:      [100, 220, 240],  // bioluminescent teal (canonical)
  },

  coherence: {
    deltaEEnvelope: 16,
    amplitudeBand:  [0.08, 0.45],
    roughnessBand:  [0.06, 0.30],
    landmarkAllowList: ['arch', 'mesa', 'crater'],
    cloudAllowList:    ['cumulus', 'cirrus', 'none'],
    waterAllowList:    ['ocean', 'coastal', 'tidal-flat'],
  },

  archetypes: [
    {
      id: 'oceanic.open-swell',
      weight: 30,
      terrain: {
        ridgeCount:     [2, 3],
        horizonY:       [0.48, 0.58],
        roughness:      [0.06, 0.16],
        amplitude:      [0.08, 0.22],
        groundMaterial: 'rock',
      },
      landmarks: ['arch', 'mesa'],
    },
    {
      id: 'oceanic.archipelago',
      weight: 25,
      terrain: {
        ridgeCount:     [3, 5],
        horizonY:       [0.40, 0.52],
        roughness:      [0.12, 0.24],
        amplitude:      [0.18, 0.38],
        groundMaterial: 'rock',
      },
      landmarks: ['arch', 'mesa', 'crater'],
    },
    {
      id: 'oceanic.delta-flats',
      weight: 25,
      terrain: {
        ridgeCount:     [2, 3],
        horizonY:       [0.52, 0.64],
        roughness:      [0.06, 0.14],
        amplitude:      [0.06, 0.18],
        groundMaterial: 'sand',
      },
      landmarks: ['arch', 'mesa'],
    },
    {
      id: 'oceanic.storm-coast',
      weight: 20,
      terrain: {
        ridgeCount:     [3, 5],
        horizonY:       [0.36, 0.50],
        roughness:      [0.18, 0.30],
        amplitude:      [0.24, 0.44],
        groundMaterial: 'rock',
      },
      landmarks: ['arch', 'crater', 'mesa'],
    },
  ],

  water:        'ocean',
  floraKinds:   ['kelp-forest', 'seagrass-patch', 'coral-cluster'],
  rockKinds:    ['sea-stack', 'tidal-rock'],
  defaultCloud: 'cumulus',

  shapeWeights: {
    LINEAR:    50,
    IRREGULAR: 35,
    COMPACT:   15,
  },

  hazardVisuals: {
    flood:    'flood-zone',
    storm:    'storm-cell',
    tsunami:  'flood-zone',
    seismic:  'fault-line',
  },

  depositVisuals: {
    ore:     'ore-vein',
    crystal: 'crystal',
    biomass: 'biolumin',
    gas:     'gas-seep',
    water:   'hydrocarbon-pool',
  },

  // Cool-blue film stock: ocean worlds read marine + airy.
  grade: { warmthBias: 0.02, vignetteStrength: 0.45, grainScale: 0.8 },
};

// ---------------------------------------------------------------------------
// DESERT profile
// ---------------------------------------------------------------------------
// Palette sourced from landedPalette() 'DESERT' case (L1867–L1873).
// scatterBand from shipped haze '230, 160, 70'.
// surface, accent are canonical (no water body on desert worlds).
// ---------------------------------------------------------------------------

const DESERT_PROFILE: PlanetProfile = {
  type: 'DESERT',

  basePalette: {
    skyTop:      [25,  11,   4],   // '#190b04' — shipped
    skyHorizon:  [192, 122,  46],  // '#c07a2e' — shipped
    scatterBand: [230, 160,  70],  // from shipped haze '230, 160, 70'
    ridgeFar:    [92,  48,  20],   // '#5c3014' — shipped
    ridgeMid:    [60,  31,  12],   // '#3c1f0c' — shipped
    ridgeNear:   [32,  16,   6],   // '#201006' — shipped
    surface:     [72,  40,  14],   // baked sand/laterite (canonical)
    floraMax:    [150, 170,  90],  // shipped flora '150, 170, 90'
    floraMin:    [100,  90,  50],  // dry scrub (canonical)
    accent:      [230, 160,  40],  // heat shimmer / mineral gold (canonical)
  },

  coherence: {
    deltaEEnvelope: 20,
    amplitudeBand:  [0.10, 0.55],
    roughnessBand:  [0.08, 0.46],
    landmarkAllowList: ['mesa', 'crater', 'arch', 'canyon', 'cone'],
    cloudAllowList:    ['dust', 'none'],
    waterAllowList:    [],
  },

  archetypes: [
    {
      id: 'desert.dune-sea',
      weight: 32,
      terrain: {
        ridgeCount:     [3, 5],
        horizonY:       [0.44, 0.56],
        roughness:      [0.08, 0.20],
        amplitude:      [0.14, 0.34],
        groundMaterial: 'sand',
      },
      landmarks: ['arch', 'mesa', 'cone'],
    },
    {
      id: 'desert.mesa-canyon',
      weight: 26,
      terrain: {
        ridgeCount:     [3, 5],
        horizonY:       [0.36, 0.50],
        roughness:      [0.28, 0.46],
        amplitude:      [0.34, 0.55],
        groundMaterial: 'rock',
      },
      landmarks: ['canyon', 'arch', 'mesa', 'crater', 'cone'],
    },
    {
      id: 'desert.salt-flat',
      weight: 22,
      terrain: {
        ridgeCount:     [2, 3],
        horizonY:       [0.54, 0.64],
        roughness:      [0.06, 0.14],
        amplitude:      [0.06, 0.16],
        groundMaterial: 'sand',
      },
      landmarks: ['arch', 'crater'],
    },
    {
      id: 'desert.rocky-erg',
      weight: 20,
      terrain: {
        ridgeCount:     [3, 4],
        horizonY:       [0.42, 0.54],
        roughness:      [0.18, 0.34],
        amplitude:      [0.20, 0.42],
        groundMaterial: 'rock',
      },
      landmarks: ['crater', 'mesa', 'arch'],
    },
  ],

  water:        'none',
  floraKinds:   ['desert-scrub', 'cactiform', 'dry-grass-tuft'],
  rockKinds:    ['sandstone-pillar', 'wind-carved-rock', 'gravel-scatter'],
  defaultCloud: 'dust',

  shapeWeights: {
    SPRAWLING: 60,
    LINEAR:    30,
    COMPACT:   10,
  },

  hazardVisuals: {
    'dust-storm':    'dust-front',
    'diurnal-swing': 'radiation-haze',
    radiation:       'radiation-haze',
    micrometeor:     'impact-scar',
  },

  depositVisuals: {
    ore:     'ore-vein',
    crystal: 'crystal',
    gas:     'gas-seep',
    water:   'hydrocarbon-pool',
  },

  // Sun-baked film stock: high-key amber, deep vignette, gritty grain.
  grade: { warmthBias: 0.22, vignetteStrength: 0.58, grainScale: 1.3 },
};

// ---------------------------------------------------------------------------
// ICE profile
// ---------------------------------------------------------------------------
// Palette sourced from landedPalette() 'ICE' case (L1844–L1852).
// scatterBand from shipped haze '215, 235, 248'.
// Note: ridges reverse the standard depth ramp — glacial blue in back,
// pale snow-white foreground (the shipped comment explains the visual intent).
// surface, water(frozen), foam, accent are canonical.
// ---------------------------------------------------------------------------

const ICE_PROFILE: PlanetProfile = {
  type: 'ICE',

  basePalette: {
    skyTop:      [12,  22,  34],   // '#0c1622' — shipped
    skyHorizon:  [188, 220, 236],  // '#bcdcec' — shipped
    scatterBand: [215, 235, 248],  // from shipped haze '215, 235, 248'
    ridgeFar:    [127, 166, 196],  // '#7fa6c4' — shipped (glacial-blue depths)
    ridgeMid:    [182, 210, 228],  // '#b6d2e4' — shipped
    ridgeNear:   [230, 241, 248],  // '#e6f1f8' — shipped (pale snow foreground)
    surface:     [195, 222, 238],  // snowpack / ice surface (canonical)
    floraMax:    [150, 190, 170],  // shipped flora '150, 190, 170'
    floraMin:    [100, 145, 125],  // sparse frost lichen (canonical)
    water:       [155, 208, 230],  // glacial melt pool / frozen (canonical)
    foam:        [240, 248, 255],  // bright white snow (canonical)
    accent:      [120, 200, 255],  // ice-crystal refraction glow (canonical)
  },

  coherence: {
    deltaEEnvelope: 14,
    amplitudeBand:  [0.10, 0.52],
    roughnessBand:  [0.08, 0.36],
    landmarkAllowList: ['glacier', 'crater', 'mesa', 'canyon'],
    cloudAllowList:    ['cirrus', 'none'],
    waterAllowList:    ['frozen'],
  },

  archetypes: [
    {
      id: 'ice.glacier-shelf',
      weight: 30,
      terrain: {
        ridgeCount:     [3, 4],
        horizonY:       [0.46, 0.58],
        roughness:      [0.08, 0.20],
        amplitude:      [0.14, 0.32],
        groundMaterial: 'ice',
      },
      landmarks: ['glacier', 'crater', 'mesa'],
    },
    {
      id: 'ice.fractured-ice',
      weight: 25,
      terrain: {
        ridgeCount:     [3, 5],
        horizonY:       [0.38, 0.52],
        roughness:      [0.22, 0.36],
        amplitude:      [0.28, 0.50],
        groundMaterial: 'ice',
      },
      landmarks: ['glacier', 'canyon', 'crater'],
    },
    {
      id: 'ice.ice-plain',
      weight: 25,
      terrain: {
        ridgeCount:     [2, 3],
        horizonY:       [0.52, 0.62],
        roughness:      [0.06, 0.14],
        amplitude:      [0.06, 0.18],
        groundMaterial: 'ice',
      },
      landmarks: ['crater', 'glacier'],
    },
    {
      id: 'ice.polar-peaks',
      weight: 20,
      terrain: {
        ridgeCount:     [4, 5],
        horizonY:       [0.34, 0.48],
        roughness:      [0.18, 0.32],
        amplitude:      [0.30, 0.52],
        groundMaterial: 'ice',
      },
      landmarks: ['glacier', 'mesa', 'canyon'],
    },
  ],

  water:        'frozen',
  floraKinds:   ['frost-moss', 'ice-lichen'],
  rockKinds:    ['ice-boulder', 'snowdrift'],
  defaultCloud: 'cirrus',

  shapeWeights: {
    COMPACT:  50,
    TERRACED: 30,
    LINEAR:   20,
  },

  hazardVisuals: {
    blizzard:   'snow-band',
    permafrost: 'snow-band',
    radiation:  'radiation-haze',
  },

  depositVisuals: {
    ore:     'ore-vein',
    crystal: 'crystal',
    gas:     'gas-seep',
    water:   'hydrocarbon-pool',
  },

  // Glacial film stock: cool-blue cast, crisp vignette, fine grain.
  grade: { warmthBias: -0.32, vignetteStrength: 0.58, grainScale: 0.9 },
};

// ---------------------------------------------------------------------------
// ARCTIC profile
// ---------------------------------------------------------------------------
// Tundra-and-polar-ice mood: distinct from ICE in three ways — grounded
// palette (gray-brown tundra influence vs pure glacial blue), coastal water
// allowed (partially thawed deltas), and aurora-weighted hazard table.
// No shipped landedPalette() anchor; palette is canonical tundra inference.
// ---------------------------------------------------------------------------

const ARCTIC_PROFILE: PlanetProfile = {
  type: 'ARCTIC',

  basePalette: {
    skyTop:      [8,   12,  22],   // deep arctic sky (canonical)
    skyHorizon:  [138, 178, 210],  // pale cold horizon (canonical)
    scatterBand: [178, 208, 228],  // icy scatter band (canonical)
    ridgeFar:    [88,  118, 152],  // tundra-gray distance (canonical)
    ridgeMid:    [62,  88,  118],  // stone/permafrost mid (canonical)
    ridgeNear:   [38,  55,   75],  // dark tundra foreground (canonical)
    surface:     [68,  92,   82],  // tundra/permafrost surface (canonical)
    floraMax:    [100, 160, 110],  // tundra grass / low scrub (canonical)
    floraMin:    [60,  88,   66],  // sparse arctic ground cover (canonical)
    water:       [118, 172, 200],  // partially frozen coastal (canonical)
    foam:        [220, 235, 245],  // sea-ice / snow spray (canonical)
    accent:      [140, 220, 255],  // aurora-tint / crystal glow (canonical)
  },

  coherence: {
    deltaEEnvelope: 16,
    amplitudeBand:  [0.08, 0.48],
    roughnessBand:  [0.10, 0.40],
    landmarkAllowList: ['glacier', 'mesa', 'crater', 'canyon'],
    cloudAllowList:    ['cirrus', 'none'],
    waterAllowList:    ['frozen', 'coastal'],
  },

  archetypes: [
    {
      id: 'arctic.tundra-plain',
      weight: 30,
      terrain: {
        ridgeCount:     [2, 4],
        horizonY:       [0.48, 0.60],
        roughness:      [0.10, 0.22],
        amplitude:      [0.10, 0.26],
        groundMaterial: 'soil',
      },
      landmarks: ['mesa', 'crater'],
    },
    {
      id: 'arctic.polar-ice-cap',
      weight: 28,
      terrain: {
        ridgeCount:     [3, 4],
        horizonY:       [0.42, 0.54],
        roughness:      [0.08, 0.20],
        amplitude:      [0.14, 0.34],
        groundMaterial: 'ice',
      },
      landmarks: ['glacier', 'crater', 'mesa'],
    },
    {
      id: 'arctic.aurora-coast',
      weight: 22,
      terrain: {
        ridgeCount:     [2, 3],
        horizonY:       [0.44, 0.56],
        roughness:      [0.12, 0.26],
        amplitude:      [0.12, 0.28],
        groundMaterial: 'rock',
      },
      landmarks: ['mesa', 'canyon', 'glacier'],
    },
    {
      id: 'arctic.frozen-delta',
      weight: 20,
      terrain: {
        ridgeCount:     [2, 3],
        horizonY:       [0.50, 0.62],
        roughness:      [0.08, 0.18],
        amplitude:      [0.08, 0.20],
        groundMaterial: 'ice',
      },
      landmarks: ['crater', 'mesa'],
    },
  ],

  water:        'frozen',
  floraKinds:   ['tundra-grass', 'arctic-shrub', 'frost-moss'],
  rockKinds:    ['tundra-rock', 'permafrost-mound'],
  defaultCloud: 'cirrus',

  shapeWeights: {
    COMPACT:  45,
    TERRACED: 30,
    LINEAR:   25,
  },

  hazardVisuals: {
    blizzard:   'snow-band',
    permafrost: 'snow-band',
    radiation:  'radiation-haze',
  },

  depositVisuals: {
    ore:     'ore-vein',
    crystal: 'crystal',
    gas:     'gas-seep',
    water:   'hydrocarbon-pool',
  },

  // Tundra film stock: cold steel-blue, heavier vignette for polar isolation.
  grade: { warmthBias: -0.22, vignetteStrength: 0.60, grainScale: 1.0 },
};

// ---------------------------------------------------------------------------
// MOUNTAINOUS profile
// ---------------------------------------------------------------------------
// Palette sourced from landedPalette() 'MOUNTAINOUS' case (L1822–L1828).
// scatterBand from shipped haze '170, 175, 190'.
// Highest amplitude band of the surface types; highest roughness for
// scree/alpine terrain.  surface, water(coastal), foam, accent are canonical.
// ---------------------------------------------------------------------------

const MOUNTAINOUS_PROFILE: PlanetProfile = {
  type: 'MOUNTAINOUS',

  basePalette: {
    skyTop:      [12,  14,  18],   // '#0c0e12' — shipped
    skyHorizon:  [107, 111, 126],  // '#6b6f7e' — shipped
    scatterBand: [170, 175, 190],  // from shipped haze '170, 175, 190'
    ridgeFar:    [74,  78,  91],   // '#4a4e5b' — shipped
    ridgeMid:    [51,  54,  63],   // '#33363f' — shipped
    ridgeNear:   [25,  27,  34],   // '#191b22' — shipped
    surface:     [52,  62,  52],   // highland rock / alpine loam (canonical)
    floraMax:    [120, 150, 110],  // shipped flora '120, 150, 110'
    floraMin:    [70,  88,  65],   // sparse alpine growth (canonical)
    water:       [62,  88,  118],  // glacial melt / mountain stream (canonical)
    foam:        [198, 210, 220],  // snowcap / thin mist (canonical)
    accent:      [158, 198, 178],  // mineral vein glow (canonical)
  },

  coherence: {
    deltaEEnvelope: 16,
    amplitudeBand:  [0.20, 0.72],
    roughnessBand:  [0.16, 0.52],
    landmarkAllowList: ['cone', 'crater', 'canyon', 'arch', 'mesa'],
    cloudAllowList:    ['cumulus', 'cirrus', 'none'],
    waterAllowList:    ['coastal'],
  },

  archetypes: [
    {
      id: 'mountainous.alpine-peaks',
      weight: 30,
      terrain: {
        ridgeCount:     [4, 5],
        horizonY:       [0.34, 0.46],
        roughness:      [0.32, 0.50],
        amplitude:      [0.42, 0.70],
        groundMaterial: 'rock',
      },
      landmarks: ['cone', 'crater', 'canyon'],
    },
    {
      id: 'mountainous.mountain-vale',
      weight: 28,
      terrain: {
        ridgeCount:     [3, 5],
        horizonY:       [0.38, 0.52],
        roughness:      [0.22, 0.38],
        amplitude:      [0.30, 0.54],
        groundMaterial: 'soil',
      },
      landmarks: ['arch', 'canyon', 'mesa'],
    },
    {
      id: 'mountainous.scree-ridge',
      weight: 22,
      terrain: {
        ridgeCount:     [3, 5],
        horizonY:       [0.36, 0.50],
        roughness:      [0.34, 0.52],
        amplitude:      [0.36, 0.62],
        groundMaterial: 'rock',
      },
      landmarks: ['canyon', 'cone', 'arch'],
    },
    {
      id: 'mountainous.terraced-plateau',
      weight: 20,
      terrain: {
        ridgeCount:     [3, 4],
        horizonY:       [0.40, 0.54],
        roughness:      [0.16, 0.30],
        amplitude:      [0.22, 0.44],
        groundMaterial: 'rock',
      },
      landmarks: ['mesa', 'arch', 'crater'],
    },
  ],

  water:        'coastal',
  floraKinds:   ['alpine-grass', 'highland-shrub', 'conifer-silhouette'],
  rockKinds:    ['mountain-boulder', 'scree-scatter', 'cliff-face'],
  defaultCloud: 'cumulus',

  shapeWeights: {
    TERRACED: 55,
    COMPACT:  35,
    LINEAR:   10,
  },

  hazardVisuals: {
    seismic:   'fault-line',
    avalanche: 'snow-band',
    radiation: 'radiation-haze',
  },

  depositVisuals: {
    ore:        'ore-vein',
    crystal:    'crystal',
    geothermal: 'thermal-vent',
    gas:        'gas-seep',
  },

  // Alpine film stock: muted neutral, deep vignette for dramatic peaks.
  grade: { warmthBias: -0.05, vignetteStrength: 0.60, grainScale: 1.0 },
};

// ---------------------------------------------------------------------------
// BARREN profile
// ---------------------------------------------------------------------------
// Palette sourced from landedPalette() 'BARREN' case (L1874–L1880).
// scatterBand from shipped haze '160, 160, 180'.
// Airless world: no water, no flora, no cloud.  Black daytime sky with dense
// stars, hard shadows, regolith.  surface, accent are canonical.
// ---------------------------------------------------------------------------

const BARREN_PROFILE: PlanetProfile = {
  type: 'BARREN',

  basePalette: {
    skyTop:      [10,  10,  18],   // '#0a0a12' — shipped
    skyHorizon:  [90,  90,  110],  // '#5a5a6e' — shipped
    scatterBand: [160, 160, 180],  // from shipped haze '160, 160, 180'
    ridgeFar:    [58,  58,  74],   // '#3a3a4a' — shipped
    ridgeMid:    [38,  38,  47],   // '#26262f' — shipped
    ridgeNear:   [19,  19,  24],   // '#131318' — shipped
    surface:     [32,  32,  42],   // dark airless regolith (canonical)
    floraMax:    [120, 140, 120],  // shipped flora '120, 140, 120' (unused)
    floraMin:    [68,  80,  68],   // (unused — no flora on barren) (canonical)
    accent:      [200, 200, 240],  // radiation shimmer / mineral glint (canonical)
  },

  coherence: {
    deltaEEnvelope: 12,
    amplitudeBand:  [0.12, 0.58],
    roughnessBand:  [0.14, 0.50],
    landmarkAllowList: ['crater', 'mesa', 'spire', 'canyon'],
    cloudAllowList:    ['none'],
    waterAllowList:    [],
  },

  archetypes: [
    {
      id: 'barren.impact-plain',
      weight: 32,
      terrain: {
        ridgeCount:     [2, 4],
        horizonY:       [0.44, 0.56],
        roughness:      [0.14, 0.28],
        amplitude:      [0.12, 0.30],
        groundMaterial: 'regolith',
      },
      landmarks: ['crater', 'mesa'],
    },
    {
      id: 'barren.fractured-mesa',
      weight: 28,
      terrain: {
        ridgeCount:     [3, 4],
        horizonY:       [0.38, 0.52],
        roughness:      [0.24, 0.42],
        amplitude:      [0.28, 0.50],
        groundMaterial: 'regolith',
      },
      landmarks: ['mesa', 'canyon', 'spire'],
    },
    {
      id: 'barren.crater-field',
      weight: 25,
      terrain: {
        ridgeCount:     [3, 5],
        horizonY:       [0.40, 0.54],
        roughness:      [0.18, 0.34],
        amplitude:      [0.20, 0.44],
        groundMaterial: 'rock',
      },
      landmarks: ['crater', 'spire', 'mesa'],
    },
    {
      id: 'barren.highland-waste',
      weight: 15,
      terrain: {
        ridgeCount:     [3, 5],
        horizonY:       [0.34, 0.48],
        roughness:      [0.28, 0.48],
        amplitude:      [0.36, 0.58],
        groundMaterial: 'rock',
      },
      landmarks: ['canyon', 'crater', 'mesa'],
    },
  ],

  water:        'none',
  floraKinds:   [],
  rockKinds:    ['regolith-scatter', 'impact-ejecta', 'basalt-outcrop'],
  defaultCloud: 'none',

  shapeWeights: {
    IRREGULAR: 55,
    COMPACT:   35,
    LINEAR:    10,
  },

  hazardVisuals: {
    radiation:   'radiation-haze',
    micrometeor: 'impact-scar',
    seismic:     'fault-line',
  },

  depositVisuals: {
    ore:     'ore-vein',
    crystal: 'crystal',
    gas:     'gas-seep',
  },

  // Airless film stock: cold-silver, hard vignette, heavy grain — harsh and hostile.
  grade: { warmthBias: -0.20, vignetteStrength: 0.68, grainScale: 1.8 },
};

// ---------------------------------------------------------------------------
// JUNGLE profile
// ---------------------------------------------------------------------------
// No shipped landedPalette() anchor.  Palette is canonical dense-canopy
// inference: near-black saturated sky, vivid emerald ridges, biolumin accent.
// Highest flora density of any surface type; coastal water for river deltas.
// ---------------------------------------------------------------------------

const JUNGLE_PROFILE: PlanetProfile = {
  type: 'JUNGLE',

  basePalette: {
    skyTop:      [2,   14,   6],   // near-black deep-jungle sky (canonical)
    skyHorizon:  [28,  98,  62],   // dense jungle horizon (canonical)
    scatterBand: [78,  178, 118],  // jungle haze — green-tinted (canonical)
    ridgeFar:    [14,  54,  34],   // dark canopy distance (canonical)
    ridgeMid:    [10,  38,  24],   // dense mid-canopy (canonical)
    ridgeNear:   [5,   22,  14],   // near-black foreground canopy (canonical)
    surface:     [12,  44,  22],   // jungle floor / mulch (canonical)
    floraMax:    [58,  198,  88],  // vivid jungle growth (canonical)
    floraMin:    [34,  104,  54],  // dense-but-shadowed undergrowth (canonical)
    water:       [18,  68,  88],   // dark river / swamp (canonical)
    foam:        [98,  168, 138],  // algae-tinged surface (canonical)
    accent:      [78,  255, 138],  // bioluminescent glow (canonical)
  },

  coherence: {
    deltaEEnvelope: 18,
    amplitudeBand:  [0.14, 0.54],
    roughnessBand:  [0.12, 0.44],
    landmarkAllowList: ['cone', 'arch', 'mesa', 'canyon'],
    cloudAllowList:    ['cumulus', 'none'],
    waterAllowList:    ['coastal', 'tidal-flat'],
  },

  archetypes: [
    {
      id: 'jungle.deep-canopy',
      weight: 32,
      terrain: {
        ridgeCount:     [4, 5],
        horizonY:       [0.36, 0.50],
        roughness:      [0.18, 0.36],
        amplitude:      [0.28, 0.52],
        groundMaterial: 'canopy',
      },
      landmarks: ['cone', 'arch'],
    },
    {
      id: 'jungle.river-gorge',
      weight: 26,
      terrain: {
        ridgeCount:     [3, 5],
        horizonY:       [0.34, 0.48],
        roughness:      [0.24, 0.44],
        amplitude:      [0.34, 0.54],
        groundMaterial: 'soil',
      },
      landmarks: ['canyon', 'arch', 'mesa'],
    },
    {
      id: 'jungle.mangrove-coast',
      weight: 22,
      terrain: {
        ridgeCount:     [2, 4],
        horizonY:       [0.46, 0.58],
        roughness:      [0.10, 0.24],
        amplitude:      [0.12, 0.30],
        groundMaterial: 'soil',
      },
      landmarks: ['arch', 'mesa'],
    },
    {
      id: 'jungle.ancient-forest',
      weight: 20,
      terrain: {
        ridgeCount:     [4, 5],
        horizonY:       [0.38, 0.52],
        roughness:      [0.16, 0.32],
        amplitude:      [0.24, 0.48],
        groundMaterial: 'canopy',
      },
      landmarks: ['cone', 'mesa', 'arch'],
    },
  ],

  water:        'coastal',
  floraKinds:   ['canopy-tree', 'fern-layer', 'vine-cluster', 'biolumin-plant'],
  rockKinds:    ['mossy-stone', 'root-tangle'],
  defaultCloud: 'cumulus',

  shapeWeights: {
    SPRAWLING: 50,
    IRREGULAR: 40,
    TERRACED:  10,
  },

  hazardVisuals: {
    megafauna: 'megafauna-marker',
    flood:     'flood-zone',
    spore:     'radiation-haze',
  },

  depositVisuals: {
    ore:     'ore-vein',
    crystal: 'crystal',
    biomass: 'biolumin',
    gas:     'gas-seep',
    water:   'hydrocarbon-pool',
  },

  // Dense-canopy film stock: slight warm-green tint, heavy vignette for claustrophobia.
  grade: { warmthBias: 0.08, vignetteStrength: 0.65, grainScale: 0.9 },
};

// ---------------------------------------------------------------------------
// TROPICAL profile
// ---------------------------------------------------------------------------
// Brightest and most saturated of all types — the high-desirability visual
// signal.  Beach/lagoon character with vivid turquoise water distinguishes it
// from jungle's deep-canopy darkness.  No shipped anchor; canonical inference.
// ---------------------------------------------------------------------------

const TROPICAL_PROFILE: PlanetProfile = {
  type: 'TROPICAL',

  basePalette: {
    skyTop:      [3,   20,  55],   // brilliant tropical blue (canonical)
    skyHorizon:  [58,  168, 200],  // vivid turquoise horizon (canonical)
    scatterBand: [128, 210, 235],  // warm tropical haze (canonical)
    ridgeFar:    [18,  78,  98],   // distant coastal headland (canonical)
    ridgeMid:    [12,  54,  70],   // mid headland (canonical)
    ridgeNear:   [8,   32,  44],   // foreground tropical rock (canonical)
    surface:     [198, 178, 118],  // bright sandy beach (canonical)
    floraMax:    [52,  198,  98],  // vivid tropical flora (canonical)
    floraMin:    [38,  148,  72],  // lush undergrowth (canonical)
    water:       [28,  138, 178],  // bright tropical water (canonical)
    foam:        [218, 240, 248],  // brilliant white surf (canonical)
    accent:      [255, 208,  58],  // solar / golden mineral accent (canonical)
  },

  coherence: {
    deltaEEnvelope: 20,
    amplitudeBand:  [0.08, 0.44],
    roughnessBand:  [0.06, 0.30],
    landmarkAllowList: ['arch', 'mesa', 'cone', 'crater'],
    cloudAllowList:    ['cumulus', 'none'],
    waterAllowList:    ['ocean', 'coastal'],
  },

  archetypes: [
    {
      id: 'tropical.coral-coast',
      weight: 30,
      terrain: {
        ridgeCount:     [2, 4],
        horizonY:       [0.46, 0.58],
        roughness:      [0.06, 0.18],
        amplitude:      [0.10, 0.26],
        groundMaterial: 'sand',
      },
      landmarks: ['arch', 'mesa'],
    },
    {
      id: 'tropical.lagoon-basin',
      weight: 28,
      terrain: {
        ridgeCount:     [2, 3],
        horizonY:       [0.52, 0.64],
        roughness:      [0.06, 0.14],
        amplitude:      [0.06, 0.18],
        groundMaterial: 'sand',
      },
      landmarks: ['arch', 'crater'],
    },
    {
      id: 'tropical.paradise-shore',
      weight: 22,
      terrain: {
        ridgeCount:     [2, 3],
        horizonY:       [0.50, 0.60],
        roughness:      [0.08, 0.20],
        amplitude:      [0.08, 0.22],
        groundMaterial: 'sand',
      },
      landmarks: ['cone', 'arch'],
    },
    {
      id: 'tropical.rainforest-ridge',
      weight: 20,
      terrain: {
        ridgeCount:     [3, 5],
        horizonY:       [0.38, 0.52],
        roughness:      [0.16, 0.30],
        amplitude:      [0.22, 0.44],
        groundMaterial: 'canopy',
      },
      landmarks: ['cone', 'mesa', 'arch'],
    },
  ],

  water:        'ocean',
  floraKinds:   ['palm-silhouette', 'tropical-shrub', 'flower-cluster', 'tall-grass'],
  rockKinds:    ['coral-rock', 'sandstone-scatter'],
  defaultCloud: 'cumulus',

  shapeWeights: {
    SPRAWLING: 45,
    LINEAR:    35,
    IRREGULAR: 20,
  },

  hazardVisuals: {
    flood:     'flood-zone',
    megafauna: 'megafauna-marker',
    storm:     'storm-cell',
  },

  depositVisuals: {
    ore:     'ore-vein',
    crystal: 'crystal',
    biomass: 'biolumin',
    gas:     'gas-seep',
    water:   'hydrocarbon-pool',
  },

  // Paradise film stock: warm + bright, open airy vignette, near-zero grain.
  grade: { warmthBias: 0.25, vignetteStrength: 0.35, grainScale: 0.4 },
};

// ---------------------------------------------------------------------------
// GAS_GIANT profile
// ---------------------------------------------------------------------------
// Special case: terrainMode 'cloud-deck' — banded cloud horizon replaces
// solid terrain and ground plane.  No landmark kinds, no water body.
// Archetype terrain params describe cloud-band morphology (amplitude = band
// height; roughness = turbulence) — interpreted by the renderer, not the
// surface primitive pipeline.  Shape: ENGINEERED only (uncolonizable).
// No shipped anchor; palette is canonical Jupiter-family amber/cream inference.
// ---------------------------------------------------------------------------

const GAS_GIANT_PROFILE: PlanetProfile = {
  type: 'GAS_GIANT',
  terrainMode: 'cloud-deck',

  basePalette: {
    skyTop:      [8,   10,  28],   // deep space above atmosphere (canonical)
    skyHorizon:  [138, 108,  78],  // warm amber cloud-top horizon (canonical)
    scatterBand: [208, 172, 128],  // upper cloud scatter (canonical)
    ridgeFar:    [188, 158,  98],  // far cloud bands — pale ochre (canonical)
    ridgeMid:    [218, 192, 142],  // mid cloud band — cream (canonical)
    ridgeNear:   [162, 118,  72],  // near cloud band — deeper storm layer (canonical)
    surface:     [118,  88,  52],  // deep storm layer / compressed gas (canonical)
    floraMax:    [198, 178, 118],  // unused / gas-body chromatics (canonical)
    floraMin:    [148, 128,  88],  // unused (canonical)
    accent:      [255, 198,  98],  // lightning / storm glow (canonical)
  },

  coherence: {
    deltaEEnvelope: 25,
    amplitudeBand:  [0.20, 0.65],
    roughnessBand:  [0.08, 0.35],
    landmarkAllowList: [],         // cloud-deck: no terrain landmarks
    cloudAllowList:    ['banded'],
    waterAllowList:    [],
  },

  archetypes: [
    {
      id: 'gas-giant.banded-storm',
      weight: 35,
      terrain: {
        ridgeCount:     [3, 5],
        horizonY:       [0.40, 0.55],
        roughness:      [0.18, 0.35],
        amplitude:      [0.28, 0.60],
        groundMaterial: 'rock',    // unused in cloud-deck mode
      },
      landmarks: [],               // cloud-deck: no terrain landmarks
    },
    {
      id: 'gas-giant.ammonia-deck',
      weight: 30,
      terrain: {
        ridgeCount:     [4, 5],
        horizonY:       [0.44, 0.58],
        roughness:      [0.08, 0.20],
        amplitude:      [0.20, 0.45],
        groundMaterial: 'rock',
      },
      landmarks: [],
    },
    {
      id: 'gas-giant.deep-vortex',
      weight: 20,
      terrain: {
        ridgeCount:     [3, 4],
        horizonY:       [0.36, 0.52],
        roughness:      [0.22, 0.35],
        amplitude:      [0.38, 0.65],
        groundMaterial: 'rock',
      },
      landmarks: [],
    },
    {
      id: 'gas-giant.polar-hex',
      weight: 15,
      terrain: {
        ridgeCount:     [3, 4],
        horizonY:       [0.42, 0.56],
        roughness:      [0.12, 0.25],
        amplitude:      [0.24, 0.48],
        groundMaterial: 'rock',
      },
      landmarks: [],
    },
  ],

  water:        'none',
  floraKinds:   [],
  rockKinds:    [],
  defaultCloud: 'banded',

  shapeWeights: {
    ENGINEERED: 100,
  },

  hazardVisuals: {
    atmospheric: 'storm-cell',
    radiation:   'radiation-haze',
  },

  depositVisuals: {
    gas:     'gas-seep',
    crystal: 'crystal',
  },

  // Jovian film stock: warm amber banding, strong vignette for drama, low grain.
  grade: { warmthBias: 0.15, vignetteStrength: 0.70, grainScale: 0.5 },
};

// ---------------------------------------------------------------------------
// ARTIFICIAL profile
// ---------------------------------------------------------------------------
// Special case: terrainMode 'plating' — flat engineered substrate; ground
// plane material is always 'plating'.  Spires are the sole landmark kind
// (antenna towers, comm arrays).  Shape: ENGINEERED 100 only.
// No shipped anchor; palette is canonical metallic-station inference.
// ---------------------------------------------------------------------------

const ARTIFICIAL_PROFILE: PlanetProfile = {
  type: 'ARTIFICIAL',
  terrainMode: 'plating',

  basePalette: {
    skyTop:      [8,   10,  20],   // dark space / low orbit — CANONICAL, never lifted
    skyHorizon:  [38,  58,  88],   // atmospheric glow / station haze (canonical)
    scatterBand: [78,  108, 150],  // light-pollution scatter (canonical)
    ridgeFar:    [64,  78,  102],  // distant superstructure silhouette (lifted from [52,62,82])
    ridgeMid:    [48,  60,  80],   // mid structure tier (lifted from [38,48,65])
    ridgeNear:   [32,  42,  60],   // near foreground structure (lifted from [24,30,46])
    surface:     [68,  82,  100],  // engineered plating surface (lifted from [48,58,72])
    floraMax:    [78,  178, 118],  // hydroponics / engineered plants (canonical)
    floraMin:    [38,  78,  58],   // sparse engineered vegetation (canonical)
    accent:      [78,  198, 255],  // cold: energy / plasma conduit glow (canonical)
    accentWarm:  [255, 175, 55],   // warm: sodium/amber window & signage glow (ARTIFICIAL only)
  },

  coherence: {
    deltaEEnvelope: 10,
    amplitudeBand:  [0.06, 0.35],
    roughnessBand:  [0.04, 0.20],
    landmarkAllowList: ['spire'],  // antenna towers / comm arrays
    cloudAllowList:    ['none'],
    waterAllowList:    [],
  },

  archetypes: [
    {
      id: 'artificial.orbital-platform',
      weight: 35,
      terrain: {
        ridgeCount:     [2, 3],
        horizonY:       [0.52, 0.62],
        roughness:      [0.04, 0.12],
        amplitude:      [0.06, 0.20],
        groundMaterial: 'plating',
      },
      landmarks: ['spire'],
    },
    {
      id: 'artificial.industrial-district',
      weight: 28,
      terrain: {
        ridgeCount:     [3, 4],
        horizonY:       [0.44, 0.56],
        roughness:      [0.08, 0.18],
        amplitude:      [0.14, 0.34],
        groundMaterial: 'plating',
      },
      landmarks: ['spire'],
    },
    {
      id: 'artificial.hab-ring',
      weight: 22,
      terrain: {
        ridgeCount:     [2, 3],
        horizonY:       [0.46, 0.58],
        roughness:      [0.06, 0.14],
        amplitude:      [0.10, 0.26],
        groundMaterial: 'plating',
      },
      landmarks: ['spire'],
    },
    {
      id: 'artificial.landing-pad',
      weight: 15,
      terrain: {
        ridgeCount:     [2, 3],
        horizonY:       [0.56, 0.66],
        roughness:      [0.04, 0.10],
        amplitude:      [0.04, 0.14],
        groundMaterial: 'plating',
      },
      landmarks: ['spire'],
    },
  ],

  water:        'none',
  floraKinds:   ['hydroponic-tray', 'engineered-plant'],
  rockKinds:    ['plating-segment', 'support-strut'],
  defaultCloud: 'none',

  shapeWeights: {
    ENGINEERED: 100,
  },

  hazardVisuals: {
    'power-surge':  'radiation-haze',
    'hull-breach':  'radiation-haze',
    radiation:      'radiation-haze',
  },

  depositVisuals: {
    ore:     'ore-vein',
    crystal: 'crystal',
    gas:     'gas-seep',
  },

  // Emissive window/signage grid — ARTIFICIAL only.
  // The renderer uses density to decide which plating-panel cells are lit and
  // draws warm-tinted rectangles at those positions, seeded from model.seed.
  emissive: { color: [255, 200, 100], density: 0.52 },

  // Industrial film stock: cold-steel cast, strong vignette, low grain (clean metal).
  grade: { warmthBias: -0.15, vignetteStrength: 0.60, grainScale: 0.6 },
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
  TERRAN:      TERRAN_PROFILE,
  VOLCANIC:    VOLCANIC_PROFILE,
  OCEANIC:     OCEANIC_PROFILE,
  DESERT:      DESERT_PROFILE,
  ICE:         ICE_PROFILE,
  ARCTIC:      ARCTIC_PROFILE,
  MOUNTAINOUS: MOUNTAINOUS_PROFILE,
  BARREN:      BARREN_PROFILE,
  JUNGLE:      JUNGLE_PROFILE,
  TROPICAL:    TROPICAL_PROFILE,
  GAS_GIANT:   GAS_GIANT_PROFILE,
  ARTIFICIAL:  ARTIFICIAL_PROFILE,
};

/**
 * Look up the profile for a planet type.  Falls back to GENERIC_PROFILE for
 * any type not yet implemented, so the engine never throws on unknown types
 * (BRIEF §2.2 degradation rule: "unknown PlanetType → generic fallback").
 */
export function getProfile(type: PlanetType): PlanetProfile {
  return PROFILES[type] ?? GENERIC_PROFILE;
}

/**
 * True when `type` has a dedicated profile (vs. resolving to GENERIC_PROFILE).
 * Single-sourced from the PROFILES registry keys so the invariants check can
 * never drift from the implemented set (was a hardcoded {TERRAN,VOLCANIC} list).
 */
export function isProfiledType(type: PlanetType): boolean {
  return type in PROFILES;
}

/** The PlanetTypes that have a dedicated profile — single-sourced from the
 *  PROFILES registry so describe()/UIs never drift from the implemented set. */
export const PROFILED_TYPES = Object.keys(PROFILES) as PlanetType[];

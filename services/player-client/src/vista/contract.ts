/**
 * Vista Engine — Canonical Contract
 *
 * Single source of truth for all types the vista engine and its consumers share.
 * Every other module in `src/vista/` imports from here; nothing in here imports
 * from anywhere else.  Pure types — no runtime logic.
 *
 * Bump VISTA_CONTRACT_VERSION (integer) ONLY on a breaking change to VistaInput
 * or VistaModel.  All consumers compare against this constant at boot.
 */

// ---------------------------------------------------------------------------
// Contract version
// ---------------------------------------------------------------------------

export const VISTA_CONTRACT_VERSION = 1;

// ---------------------------------------------------------------------------
// Planet types  (all 12; matches planet.py on the gameserver)
// ---------------------------------------------------------------------------

export type PlanetType =
  | 'TERRAN'
  | 'DESERT'
  | 'OCEANIC'
  | 'ICE'
  | 'VOLCANIC'
  | 'GAS_GIANT'
  | 'BARREN'
  | 'JUNGLE'
  | 'ARCTIC'
  | 'TROPICAL'
  | 'MOUNTAINOUS'
  | 'ARTIFICIAL';

// ---------------------------------------------------------------------------
// Supporting types (referenced by VistaInput / VistaModel / PlanetProfile)
// ---------------------------------------------------------------------------

/** Buildable-grid shape; drives terrain footprint carving and archetype weighting. */
export type GridShape =
  | 'COMPACT'
  | 'TERRACED'
  | 'LINEAR'
  | 'IRREGULAR'
  | 'SPRAWLING'
  | 'ENGINEERED';

/** Primary energy source at an expedition site; drives visual energy-signature. */
export type EnergySource = 'GEOTHERMAL' | 'TIDAL' | 'SOLAR' | 'WIND';

/** Spectral classification of a star; drives disc size, color, and scene light table. */
export type StarKind =
  | 'M_DWARF'
  | 'K_ORANGE'
  | 'G_YELLOW'
  | 'F_WHITE'
  | 'A_BLUE'
  | 'B_BLUE_GIANT'
  | 'O_BLUE_SUPER'
  | 'RED_GIANT'
  | 'WHITE_DWARF'
  | 'NEUTRON'
  | 'BLACK_HOLE';

// ---------------------------------------------------------------------------
// VistaInput  — everything the engine consumes; nothing it doesn't
// ---------------------------------------------------------------------------
//
// [MVP]  required for the smallest viable slice (P0 lab)
// [P1]   lands with 12-type coverage + intra-type variation (Phase 1)
// [P2]   lands with celestial depth + atmospheric events (Phase 2)
// [ADR]  depends on ADR-0091 expedition data existing in code (Phases 6+)
// [P7]   depends on grid-on-terrain subsystem (Phase 7)
//
// Degradation rules (engine never throws on partial input):
//   site absent   → skip deposit/energy/hazard layers; desirability = habitability-only
//   grid absent   → no overlay
//   moons/siblings absent → empty sky bodies
//   unknown deposit.kind / hazard.kind / PlanetType → generic fallback, never a throw
//
// ---------------------------------------------------------------------------

export interface VistaInput {
  /** Must equal VISTA_CONTRACT_VERSION (1). Discriminant for schema evolution. */
  contractVersion: 1;

  /** [MVP] Sole entropy source.  Derived from persisted data — never re-rolled per frame. */
  seed: string;

  planet: {
    /** [MVP] One of the 12 PlanetTypes.  Unknown values → generic fallback. */
    type: PlanetType;

    /** [MVP] 0–100.  Drives lushness, starfield density, flora tint, and beauty budget. */
    habitability: number;

    /** [MVP] Atmosphere toggle + flavor + density (0–1).  present=false → vacuum path. */
    atmosphere: {
      present: boolean;
      kind: string | null;
      density: number;
    };

    /** [P1] 0–1 affinity — bioluminescence / spore / megafauna visual-risk signal. */
    nativeLife: number;

    /** [P1] Normalized temperature: -1 (frozen) .. +1 (molten); drives palette warmth. */
    temperature: number;

    /** [P1] 0–1 water fraction.  OCEANIC ~0.85, DESERT ~0. */
    waterCoverage: number;
  };

  celestial: {
    /** [MVP] Primary (and optional secondary) star for sky/sun/stars. */
    star: {
      kind: StarKind;
      /** Hex color string, e.g. '#fff4d0'. */
      color: string;
      secondary?: { kind: StarKind; color: string };
    };

    /** [P2] Planet's orbital distance in AU.  vs habitableZone → insolation. */
    orbitAu: number;

    /** [P2] Orbital phase in degrees (0–359). */
    phaseDeg: number;

    /** [P2] Rotation period in hours.  180–600 → tidally locked (frozen sun). */
    rotationPeriodHours: number;

    /** [P2] Axial tilt in degrees (0–45).  Drives sun-arc height and polar cases. */
    axialTiltDeg: number;

    /** [P2] Up to 3 moons, each with size class, orbital phase, and optional rings. */
    moons?: {
      sizeClass: number;
      phaseDeg: number;
      hasRings?: boolean;
    }[];

    /** [P2] Whether this planet has a visible ring system. */
    rings?: boolean;

    /** [P2] Distant sibling bodies visible in the sky. */
    siblings?: {
      kind: string;
      sizeClass: number;
      phaseDeg: number;
      hue: number;
      sat: number;
    }[];

    /** [P2] Sector-level nebula wash in the night sky. */
    nebula?: { hue: number; density: number };

    /** [P2] Habitable zone bounds for insolation calculation. */
    habitableZone?: { innerAu: number; outerAu: number };
  };

  /**
   * [ADR] The expedition site roll from ADR-0091.
   * Absent → no resource/hazard layers; desirability degrades to habitability-only.
   */
  site?: {
    shape: GridShape;

    /** 6–32 usable build slots. */
    usableSlots: number;

    /** Maximum citadel level this site can support (1–5). */
    citadelCeiling: 1 | 2 | 3 | 4 | 5;

    energy: {
      source: EnergySource;
      tier: 1 | 2 | 3 | 4;
      magnitude: number;
    };

    /** Resource deposits; richness 0–1 (normalized from terrain_bonus). */
    deposits: { kind: string; richness: number }[];

    /** Hazards; severity 0–1.  Named hazards force their visual into the sky. */
    hazards: { kind: string; severity: number; named: boolean }[];

    /** [P1] 0–1 cosmetic defensibility signal (cliffs / chokepoints). */
    defensibility?: number;
  };

  /**
   * [P7] Live grid / structure state.
   * Absent → no overlay.  Present → engine emits a GridOverlay in the model.
   */
  grid?: {
    cols: number;
    rows: number;

    /**
     * Reveal fraction by citadel level (ADR-0091 §5):
     * 0.35 / 0.55 / 0.70 / 0.85 / 1.0
     */
    revealFraction: number;

    buildings?: {
      x: number;
      y: number;
      kind: string;
      level: number;
      complete: boolean;
    }[];

    plots?: {
      x: number;
      y: number;
      terrain?: string;
      hazard?: boolean;
      clearable?: boolean;
    }[];
  };

  /**
   * [MVP] Animation / lab state.  NOT part of model determinism — the renderer
   * reads this directly; it never flows through generate().
   */
  view?: {
    /** 0–1 along the day cycle (lab scrubber). */
    timeOfDay?: number;
    weatherOverride?: string | null;
    quality?: 'low' | 'med' | 'high';
  };
}

// ---------------------------------------------------------------------------
// VistaModel  — data, not draw-calls; a back-to-front layer stack
// ---------------------------------------------------------------------------

/** sRGB triple, each channel 0–255 (integer). */
export type RGB = [number, number, number];

/**
 * [P7] Ground-plane coordinate frame emitted by generateVista when grid input
 * is present.  The game-owned overlay reads cell transforms from here; it never
 * sees raw game state.  Building at plots[i].(x,y) → index = y*cols + x →
 * cells[index].transform.  Swapping the engine re-anchors all buildings
 * automatically — no migration.
 */
export interface GridOverlay {
  space: 'screen2d' | 'world3d';

  /** World-space origin of the grid (top-left corner in screen2d). */
  origin: number[];

  /** Right-axis basis vector (unit, in screen space). */
  uBasis: number[];

  /** Down-axis basis vector (unit, in screen space). */
  vBasis: number[];

  cellSize: number;

  cells: {
    /** y*cols + x, mapping directly to plots[]/buildings[] coordinates. */
    index: number;

    /** Whether this cell is inside the usable footprint (vs out-of-shape margins). */
    inSilhouette: boolean;

    /** Where to draw / seat a building: [x, y, scaleX, scaleY] or a full 4×4 matrix. */
    transform: number[];

    occluded?: boolean;
    groundNormal?: number[];

    /** Per-type seating hint: 'stilts', 'pad', 'rig', 'platform', 'slab', etc. */
    terrainHint?: string;
  }[];
}

export interface VistaModel {
  contractVersion: 1;
  seed: string;
  planetType: PlanetType;

  /**
   * The macro scene variant chosen for this seed, e.g. 'volcanic.caldera-rim'.
   * Encodes the archetype table entry chosen in stage 2 of the pipeline.
   */
  archetype: string;

  /**
   * Composite beauty budget 0–1.  Drives bloom, saturation, colorGradeWarmth,
   * life density, and sky depth.  Players should be able to glance and read quality.
   *
   * Wired: the post-process compositor (render/canvas2d/post.ts) consumes this
   * field directly — vignette strength, split-tone grade intensity, and film-grain
   * grit all scale from it.  Low desirability (hostile worlds) → heavier vignette,
   * stronger grain; high desirability (lush worlds) → open grade, minimal grain.
   */
  desirability: number;

  palette: {
    skyTop: RGB;
    skyHorizon: RGB;
    scatterBand: RGB;

    /** One per parallax stratum, ordered far→near (darkening with atmospheric haze). */
    ridge: RGB[];

    surface: RGB;
    geologyBands: RGB[];
    flora: RGB;
    water?: RGB;
    foam?: RGB;

    /** Deposit / energy glow tint. */
    accent: RGB;

    /**
     * Optional warm secondary accent (sodium/amber city-light).
     * Present only for ARTIFICIAL; absent for all 11 natural types.
     * Contrast pair: cold conduit glow (accent) + warm window light (accentWarm).
     */
    accentWarm?: RGB;
  };

  lighting: {
    /** Sun azimuth + elevation on the sky dome [0–360, -90–90]. */
    keyDir: [number, number];

    keyColor: RGB;
    keyIntensity: number;
    ambient: RGB;
    fill: RGB;

    /** 0–1; rises with desirability (more saturation / god-ray intensity). */
    bloom: number;

    /** -1 (cold/blue) .. +1 (warm/golden). */
    colorGradeWarmth: number;

    /**
     * [TK-2] Optional emissive light SOURCE — tints nearby layers via
     * additive gradient overlays and feeds the existing bloom pass (no
     * post.ts changes needed; bloom already re-composites any bright pixels
     * drawScene produces). DISTINCT from layers.terrain.emissive (the
     * ARTIFICIAL-only window/signage grid) — this is a natural glow (lava,
     * aurora, alpenglow), not a building light. Present only for profiles
     * with PlanetProfile.emissiveSource configured (profiles.ts); absent for
     * every other type, so their models are byte-identical to before this
     * field existed.
     */
    emissiveSource?: {
      kind: 'lava' | 'aurora' | 'alpenglow';
      /** Normalized screen position (0–1 × 0–1); seed-jittered from the profile's base. */
      pos: [number, number];
      color: RGB;
      /** 0–1 glow strength; seed-jittered from the profile's base. */
      intensity: number;
      /** Normalized influence radius, fraction of canvas width. */
      radius: number;
    };
  };

  layers: {
    sky: {
      gradient: { stop: number; color: RGB }[];
      scatterBands: { y: number; color: RGB; width: number }[];

      /** Atmospheric haze layer.  density=0 when atmosphere absent. */
      haze: { density: number; color: RGB };

      /** ~30 (hab 0) .. ~200 (hab 100).  Full density at midday in vacuum. */
      starCount: number;
    };

    celestial: {
      suns: {
        pos: [number, number];
        radiusPx: number;
        color: RGB;
        glow: number;
        special?: 'accretion' | 'pulsar';
      }[];
      moons: {
        pos: [number, number];
        radiusPx: number;
        litFraction: number;
        hasRings: boolean;
      }[];
      distant: {
        pos: [number, number];
        radiusPx: number;
        hue: number;
        sat: number;
      }[];
      ringArc?: {
        tiltDeg: number;
        innerR: number;
        outerR: number;
        color: RGB;
      };

      /** Stable key into the starfield noise — varies by seed, cached per model. */
      starfieldSeedKey: string;

      /** Sector-level nebula wash; absent when no nebula in the input celestial data. */
      nebula?: { hue: number; density: number };
    };

    atmosphere: {
      present: boolean;
      clouds: {
        kind: 'cumulus' | 'ash' | 'dust' | 'cirrus' | 'banded' | 'none';
        coverage: number;
        color: RGB;

        /** Cloud drift speed multiplier; 0 = frozen (vacuum / tidally-locked). */
        drift: number;
      };

      /** At most one primary + one ambient event active at once (§3.3). */
      events: { kind: string; intensity: number; tint: RGB }[];

      particles: {
        kind: 'rain' | 'snow' | 'ash' | 'dust' | 'spark' | 'spore' | 'ember';
        rate: number;
        color: RGB;
      }[];
    };

    terrain: {
      /**
       * Rendering mode for the terrain layer.  Absent → 'surface'.
       * 'cloud-deck' → GAS_GIANT: cloud-band horizon replaces ridge/ground-plane.
       * 'plating'    → ARTIFICIAL: flat engineered substrate.
       */
      mode?: 'surface' | 'cloud-deck' | 'plating';

      /**
       * Optional emissive window/signage grid parameters.
       * Present only for ARTIFICIAL; absent for all 11 natural types.
       * color: sRGB of the window glow; density: fraction of grid cells lit (0–1).
       */
      emissive?: { color: RGB; density: number };

      /** Normalized Y position of the horizon line (0=top, 1=bottom). */
      horizonY: number;

      /** Parallax ridge strata, ordered far→near. */
      strata: {
        polyline: [number, number][];
        fill: RGB;

        /** Multiplier for parallax scrolling speed (0=static background). */
        parallax: number;
      }[];

      groundPlane: {
        poly: [number, number][];
        material:
          | 'rock'
          | 'sand'
          | 'ice'
          | 'soil'
          | 'basalt'
          | 'regolith'
          | 'plating'
          | 'canopy';

        /** Normalized slope values sampled left→right across the ground plane. */
        slopeProfile: number[];
      };

      landmarks: {
        kind:
          | 'cone'
          | 'caldera'
          | 'arch'
          | 'mesa'
          | 'crater'
          | 'spire'
          | 'canyon'
          | 'glacier';
        pos: [number, number];
        scale: number;
      }[];
    };

    /**
     * [TK-1] Hero-landform — the single dominant midground focal feature
     * (WO-VISTA-TK1). Present only for the 6 planet types with a hero shape
     * assigned (profiles.ts's PlanetProfile.heroLandform: VOLCANIC=cone,
     * ICE=glacier, OCEANIC=sea-stack, BARREN=mesa, MOUNTAINOUS=massif,
     * TERRAN=delta-bluff); absent for all other types, so their models are
     * byte-identical to before this field existed.
     * Consumed by drawHeroLandform (render/canvas2d/hero.ts); ignored elsewhere.
     */
    hero?: {
      shape: 'cone' | 'glacier' | 'sea-stack' | 'mesa' | 'massif' | 'delta-bluff';

      /** Normalized [x, y] anchor — matches terrain.landmarks.pos convention. */
      pos: [number, number];

      /** Dimensionless size multiplier — heroes read larger than background landmarks. */
      scale: number;
    };

    water?: {
      waterlineY: number;
      type: 'ocean' | 'coastal' | 'tidal-flat' | 'frozen' | 'lava';
      color: RGB;
      foam: RGB;
      waveAmp: number;
      chop: number;

      /** Foam intensity multiplier (calm=1, storm=3). */
      foamMul: number;

      /** Spray particle speed multiplier. */
      spraySpeedMul: number;
    };

    features: {
      /** Poisson-disk scatter instances per kind. */
      scatters: {
        kind: string;
        instances: {
          pos: [number, number];
          scale: number;
          tint: RGB;
          glow?: number;
        }[];
      }[];

      /** Visible resource-deposit markers (absent when site absent). */
      depositMarkers: {
        deposit: string;
        pos: [number, number];
        intensity: number;
        visual:
          | 'ore-vein'
          | 'gas-seep'
          | 'thermal-vent'
          | 'hydrocarbon-pool'
          | 'crystal'
          | 'biolumin';
      }[];

      /** Energy signature marker (absent when site absent). */
      energyMarker?: {
        source: EnergySource;
        pos: [number, number];
        intensity: number;
      };
    };

    hazards: {
      /** Hazard overlays drawn on top of features (absent when site absent). */
      overlays: {
        hazard: string;
        severity: number;
        visual:
          | 'lava-flow'
          | 'fault-line'
          | 'storm-cell'
          | 'radiation-haze'
          | 'flood-zone'
          | 'snow-band'
          | 'dust-front'
          | 'megafauna-marker'
          | 'impact-scar';
        region: [number, number][];
      }[];
    };

    /** [P7] Absent until grid input is provided and the overlay subsystem is built. */
    grid?: GridOverlay;
  };

  animation: {
    /** Nominal day-cycle duration in seconds (360 at G_YELLOW / Sol). */
    dayCycleSeconds: number;

    /** Actual planet rotation period in hours (∞ → tidally locked). */
    rotationPeriodHours: number;
  };

  /**
   * Validation receipt from the pipeline.  ok=true means the input was clean
   * and all stages completed normally; notes[] carries non-fatal warnings.
   */
  invariants: { ok: boolean; notes: string[] };
}

// ---------------------------------------------------------------------------
// Engine swap boundary — the three public interfaces
// ---------------------------------------------------------------------------

/** Canvas target supplied by the host when mounting a VistaModel. */
export interface VistaTarget {
  canvas: HTMLCanvasElement;
  backend: 'canvas2d' | 'webgl';
}

/**
 * Live handle returned by VistaEngine.mount().  The caller drives animation
 * externally; the engine itself uses no timers or rAF internally.
 */
export interface VistaHandle {
  /** Advance the animation clock.  `seconds` is wall-clock elapsed since mount. */
  setTime(seconds: number): void;

  /** Resize the canvas, preserving the current model.  DPR-aware. */
  resize(w: number, h: number): void;

  /**
   * Hot-patch one or more VistaInput fields without a full pipeline rebuild.
   * Used by the lab's toggles and the grid-reveal path.
   */
  update(partial: Partial<VistaInput>): void;

  /** Release all GPU / canvas resources.  Call on component unmount. */
  dispose(): void;
}

/**
 * The public engine interface.  Implementors expose generate() (pure, headless)
 * and mount() (canvas renderer).  The swap boundary: any backend that satisfies
 * this interface is a valid drop-in replacement.
 */
export interface VistaEngine {
  readonly contractVersion: number;

  /**
   * Pure + deterministic.  No DOM, no game imports, no side-effects.
   * Identical VistaInput (minus view) → byte-identical VistaModel.
   */
  generate(input: VistaInput): VistaModel;

  /**
   * Mount the model onto a canvas target and begin rendering.
   * Returns a VistaHandle; dispose() when done.
   */
  mount(model: VistaModel, target: VistaTarget): VistaHandle;

  /** Enumerate supported planet types and available backends. */
  describe(): { types: PlanetType[]; backends: ('canvas2d' | 'webgl')[] };
}

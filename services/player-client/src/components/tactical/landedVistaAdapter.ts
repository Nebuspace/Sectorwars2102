/**
 * Landed-scene → VistaInput adapter  (Lane A1 — cockpit integration milestone W7)
 *
 * Bridges the cockpit's landed-scene props to the vista engine's VistaInput
 * contract so the engine can render the landed planet surface.
 *
 * TRUTHFULNESS POLICY (critical — the engine enforces a hazard-truthfulness invariant):
 *   `deposits` and `hazards` are always set to `[]`.  The cockpit's landed-scene
 *   does not carry game-state resource or hazard data, so none is fabricated.
 *   Only cosmetic/ambient fields are derived deterministically from the seed and
 *   per-PlanetType defaults: atmosphere, nativeLife, temperature, waterCoverage,
 *   site.shape, usableSlots, energy, phaseDeg, rotationPeriodHours, axialTiltDeg.
 *   The scene is plausible and visually stable but never misrepresents actual
 *   resources or hazards to the player.
 */

import type {
  VistaInput,
  PlanetType,
  StarKind,
  GridShape,
  EnergySource,
} from '../../vista/contract';
import { SeededRng, deriveChildSeed } from '../../vista/core/rng';

// ---------------------------------------------------------------------------
// Public interfaces
// ---------------------------------------------------------------------------

/**
 * The subset of landed-scene state the cockpit can supply.
 * Wire from SolarSystemViewscreen props + system snapshot.
 */
export interface LandedVistaSource {
  /** Raw planet type string from the cockpit (case-insensitive, common aliases tolerated). */
  planetType?: string;
  /** 0–100 habitability score.  Drives flora vs desolation budget.  Defaults to 50. */
  habitability?: number;
  /** 0–5 citadel level; drives site.citadelCeiling and energy tier.  Defaults to 1. */
  citadelLevel?: number;
  /** Distance-to-sun in AU; drives insolation.  Falls back to 0.5 when absent. */
  orbitAu?: number;
  /** Primary star kind + hex color from the system snapshot; null or absent → G_YELLOW. */
  star?: { kind: string; color: string } | null;
  /** Number of moons (cosmetic sky bodies only — no game-state rings data here). */
  moons?: number;
  /** Count of other bodies in the system for cosmetic sky-context siblings. */
  siblingCount?: number;
  /**
   * STABLE identifier driving determinism.  Same seedKey → identical VistaInput
   * on every call.  Typically the planet's game ID or the sector ID.
   * Must never be Math.random() or Date.now() derived.
   */
  seedKey: string;
}

// ---------------------------------------------------------------------------
// Internal lookup tables  (anchored to the FIXED_INPUTS fixtures in VistaProof.tsx)
// ---------------------------------------------------------------------------

const VALID_PLANET_TYPES = new Set<PlanetType>([
  'TERRAN', 'DESERT', 'OCEANIC', 'ICE', 'VOLCANIC', 'GAS_GIANT',
  'BARREN', 'JUNGLE', 'ARCTIC', 'TROPICAL', 'MOUNTAINOUS', 'ARTIFICIAL',
]);

/**
 * Case-folded aliases → canonical PlanetType.
 * Handles the game server's exact strings (uppercase) as well as common
 * English variants a client might receive.
 */
const PLANET_TYPE_ALIASES: Readonly<Record<string, PlanetType>> = {
  // canonical (lowercase for lookup)
  terran: 'TERRAN', desert: 'DESERT', oceanic: 'OCEANIC', ice: 'ICE',
  volcanic: 'VOLCANIC', gas_giant: 'GAS_GIANT', gasgiant: 'GAS_GIANT',
  'gas-giant': 'GAS_GIANT', barren: 'BARREN', jungle: 'JUNGLE',
  arctic: 'ARCTIC', tropical: 'TROPICAL', mountainous: 'MOUNTAINOUS',
  artificial: 'ARTIFICIAL',
  // common English variants
  lava: 'VOLCANIC', magma: 'VOLCANIC',
  tundra: 'ARCTIC', frozen: 'ICE', frost: 'ICE',
  ocean: 'OCEANIC', water: 'OCEANIC',
  paradise: 'TROPICAL', rainforest: 'JUNGLE', forest: 'JUNGLE',
  rock: 'BARREN', rocky: 'BARREN', dead: 'BARREN',
  plains: 'TERRAN', earth: 'TERRAN', garden: 'TERRAN',
  mountain: 'MOUNTAINOUS', mountains: 'MOUNTAINOUS',
  station: 'ARTIFICIAL', synthetic: 'ARTIFICIAL', hab: 'ARTIFICIAL', habitat: 'ARTIFICIAL',
};

/** Case-folded star kind strings → canonical StarKind. */
const STAR_KIND_MAP: Readonly<Record<string, StarKind>> = {
  m_dwarf: 'M_DWARF', k_orange: 'K_ORANGE', g_yellow: 'G_YELLOW',
  f_white: 'F_WHITE', a_blue: 'A_BLUE', b_blue_giant: 'B_BLUE_GIANT',
  o_blue_super: 'O_BLUE_SUPER', red_giant: 'RED_GIANT',
  white_dwarf: 'WHITE_DWARF', neutron: 'NEUTRON', black_hole: 'BLACK_HOLE',
};

/** Cosmetic per-type defaults anchored to the VistaProof.tsx FIXED_INPUTS. */
interface TypeDefaults {
  atmosPresent: boolean;
  atmosKind: string | null;
  atmosDensity: number;
  nativeLife: number;
  temperature: number;
  waterCoverage: number;
  shape: GridShape;
  baseSlots: number;
  energySource: EnergySource;
  rotationPeriodHours: number;
  axialTiltDeg: number;
}

const TYPE_DEFAULTS: Readonly<Record<PlanetType, TypeDefaults>> = {
  TERRAN:      { atmosPresent: true,  atmosKind: null,        atmosDensity: 0.70, nativeLife: 0.55, temperature:  0.10, waterCoverage: 0.55, shape: 'SPRAWLING',  baseSlots: 18, energySource: 'SOLAR',      rotationPeriodHours:  24, axialTiltDeg: 23 },
  DESERT:      { atmosPresent: true,  atmosKind: null,        atmosDensity: 0.45, nativeLife: 0.15, temperature:  0.70, waterCoverage: 0.02, shape: 'SPRAWLING',  baseSlots: 16, energySource: 'SOLAR',      rotationPeriodHours:  36, axialTiltDeg: 20 },
  OCEANIC:     { atmosPresent: true,  atmosKind: null,        atmosDensity: 0.78, nativeLife: 0.65, temperature:  0.20, waterCoverage: 0.90, shape: 'ENGINEERED', baseSlots: 14, energySource: 'TIDAL',      rotationPeriodHours:  26, axialTiltDeg: 12 },
  ICE:         { atmosPresent: true,  atmosKind: null,        atmosDensity: 0.40, nativeLife: 0.08, temperature: -0.85, waterCoverage: 0.72, shape: 'COMPACT',    baseSlots: 10, energySource: 'GEOTHERMAL', rotationPeriodHours:  48, axialTiltDeg:  5 },
  VOLCANIC:    { atmosPresent: true,  atmosKind: 'sulfurous', atmosDensity: 0.90, nativeLife: 0.10, temperature:  0.88, waterCoverage: 0.05, shape: 'COMPACT',    baseSlots:  8, energySource: 'GEOTHERMAL', rotationPeriodHours: 200, axialTiltDeg:  2 },
  GAS_GIANT:   { atmosPresent: true,  atmosKind: null,        atmosDensity: 1.00, nativeLife: 0.00, temperature: -0.35, waterCoverage: 0.00, shape: 'ENGINEERED', baseSlots:  6, energySource: 'SOLAR',      rotationPeriodHours:  10, axialTiltDeg:  3 },
  BARREN:      { atmosPresent: false, atmosKind: null,        atmosDensity: 0.00, nativeLife: 0.00, temperature:  0.05, waterCoverage: 0.00, shape: 'COMPACT',    baseSlots:  8, energySource: 'SOLAR',      rotationPeriodHours:  60, axialTiltDeg:  1 },
  JUNGLE:      { atmosPresent: true,  atmosKind: null,        atmosDensity: 0.80, nativeLife: 0.88, temperature:  0.40, waterCoverage: 0.45, shape: 'IRREGULAR',  baseSlots: 14, energySource: 'WIND',       rotationPeriodHours:  28, axialTiltDeg: 15 },
  ARCTIC:      { atmosPresent: true,  atmosKind: null,        atmosDensity: 0.45, nativeLife: 0.12, temperature: -0.75, waterCoverage: 0.30, shape: 'COMPACT',    baseSlots: 10, energySource: 'GEOTHERMAL', rotationPeriodHours:  28, axialTiltDeg: 45 },
  TROPICAL:    { atmosPresent: true,  atmosKind: null,        atmosDensity: 0.75, nativeLife: 0.60, temperature:  0.48, waterCoverage: 0.68, shape: 'LINEAR',     baseSlots: 16, energySource: 'TIDAL',      rotationPeriodHours:  22, axialTiltDeg:  8 },
  MOUNTAINOUS: { atmosPresent: true,  atmosKind: null,        atmosDensity: 0.55, nativeLife: 0.30, temperature: -0.10, waterCoverage: 0.18, shape: 'TERRACED',   baseSlots: 12, energySource: 'GEOTHERMAL', rotationPeriodHours:  30, axialTiltDeg: 30 },
  ARTIFICIAL:  { atmosPresent: true,  atmosKind: null,        atmosDensity: 0.45, nativeLife: 0.70, temperature:  0.20, waterCoverage: 0.00, shape: 'ENGINEERED', baseSlots: 14, energySource: 'SOLAR',      rotationPeriodHours:  24, axialTiltDeg: 10 },
};

/** Planet type kinds used for cosmetic sky sibling bodies. */
const SIBLING_KINDS: readonly string[] = ['GAS_GIANT', 'TERRAN', 'BARREN', 'DESERT', 'OCEANIC'];

// ---------------------------------------------------------------------------
// Internal helpers
// ---------------------------------------------------------------------------

/**
 * Sanitize a numeric input from untrusted caller data.
 * Returns `def` when v is not a finite number (catches NaN, ±Infinity,
 * undefined, non-number types); otherwise clamps to [min, max].
 *
 * This is the SINGLE numeric entry-point for every src.* field — nothing
 * from the caller reaches the VistaInput without passing through here.
 */
function num(v: unknown, def: number, min: number, max: number): number {
  return (typeof v === 'number' && Number.isFinite(v))
    ? Math.min(max, Math.max(min, v))
    : def;
}

/**
 * Accept `unknown` so that truthy non-strings (42, {}, []) are handled
 * at runtime even when TypeScript callers pass LandedVistaSource.planetType.
 */
function normalizePlanetType(raw: unknown): PlanetType | null {
  if (typeof raw !== 'string' || !raw) return null;
  // Exact uppercase match first (the common server-side format).
  const upper = raw.toUpperCase() as PlanetType;
  if (VALID_PLANET_TYPES.has(upper)) return upper;
  // Alias table: fold to lowercase, collapse spaces/hyphens to underscores.
  const folded = raw.toLowerCase().replace(/[\s-]+/g, '_');
  return PLANET_TYPE_ALIASES[folded] ?? null;
}

function normalizeStarKind(raw: string | undefined): StarKind {
  if (!raw) return 'G_YELLOW';
  return STAR_KIND_MAP[raw.toLowerCase()] ?? 'G_YELLOW';
}

/** citadelLevel 1–5 → energy tier 1–4. */
function energyTierForLevel(level: 1 | 2 | 3 | 4 | 5): 1 | 2 | 3 | 4 {
  if (level <= 1) return 1;
  if (level <= 3) return 2;
  if (level <= 4) return 3;
  return 4;
}

// ---------------------------------------------------------------------------
// Public adapter
// ---------------------------------------------------------------------------

/**
 * Adapt a cockpit landed-scene source to a VistaInput the engine can render.
 *
 * @returns A valid VistaInput (generateVista will set invariants.ok === true),
 *          or `null` when `src.planetType` cannot be mapped to a known
 *          PlanetType — caller should fall back to the legacy renderer.
 *
 * All output fields are derived DETERMINISTICALLY from `src.seedKey` combined
 * with per-type defaults.  No Math.random(), no Date.now().  Same seedKey ⇒
 * byte-identical VistaInput on every call.
 *
 * TRUTHFULNESS: `deposits` and `hazards` are always `[]` — see module-level JSDoc.
 */
export function adaptLandedSceneToVistaInput(src: LandedVistaSource): VistaInput | null {
  const planetType = normalizePlanetType(src.planetType);
  if (!planetType) return null;

  const defaults = TYPE_DEFAULTS[planetType];

  // All numeric src fields pass through num() — NaN/Infinity/wrong-type never
  // reaches the VistaInput.
  const habitability  = num(src.habitability,  50,  0,   100);
  const orbitAu       = num(src.orbitAu,       0.5, 0.01, 200);
  const rawMoons      = num(src.moons,          0,   0,   99);
  const rawSiblings   = num(src.siblingCount,   0,   0,   99);

  // citadelCeiling must be 1–5; num() first (catches NaN/Infinity), then
  // round + clamp.  The `as 1|2|3|4|5` cast is valid because the value is
  // guaranteed finite and in [1,5] after both operations.
  const clampedLevel = Math.max(
    1,
    Math.min(5, Math.round(num(src.citadelLevel, 1, 0, 5))),
  ) as 1 | 2 | 3 | 4 | 5;

  // Independent child RNG streams — named so adding/reordering streams never
  // shifts another stream's draws (same SplitMix32 isolation as the pipeline's SeedBus).
  const rngCelestial = new SeededRng(deriveChildSeed(src.seedKey, 'adapter:celestial'));
  const rngSite      = new SeededRng(deriveChildSeed(src.seedKey, 'adapter:site'));
  const rngMoons     = new SeededRng(deriveChildSeed(src.seedKey, 'adapter:moons'));
  const rngSiblings  = new SeededRng(deriveChildSeed(src.seedKey, 'adapter:siblings'));

  // -- celestial fields (positional/orbital — seed-derived) --
  const phaseDeg = rngCelestial.int(0, 359);

  // Rotation period: ±15% variation on the per-type baseline; min 8 h.
  const rotVariance         = rngCelestial.next01() * 0.30 - 0.15;
  const rotationPeriodHours = Math.max(8, Math.round(defaults.rotationPeriodHours * (1 + rotVariance)));

  // Axial tilt: ±5° variation on the per-type baseline; clamp 0–45.
  const axialTiltDeg = Math.max(0, Math.min(45, defaults.axialTiltDeg + rngCelestial.int(-5, 5)));

  // Star: use cockpit-supplied kind + color; fall back to a G_YELLOW Sol analogue.
  const starKind  = normalizeStarKind(src.star?.kind);
  const starColor = (src.star?.color) || '#fff4d0';

  // Moons: cosmetic sky bodies only (no rings data from the cockpit); cap at 3.
  const moonCount = Math.min(3, Math.max(0, rawMoons));
  const moons = moonCount > 0
    ? Array.from({ length: moonCount }, () => ({
        sizeClass: rngMoons.int(1, 3),
        phaseDeg:  rngMoons.int(0, 359),
      }))
    : undefined;

  // Siblings: cosmetic distant bodies for sky context; cap at 2.
  const sibCount = Math.min(2, Math.max(0, rawSiblings));
  const siblings = sibCount > 0
    ? Array.from({ length: sibCount }, () => ({
        kind:      rngSiblings.pick(SIBLING_KINDS),
        sizeClass: rngSiblings.int(1, 3),
        phaseDeg:  rngSiblings.int(0, 359),
        hue:       rngSiblings.int(0, 359),
        sat:       0.30 + rngSiblings.next01() * 0.40,
      }))
    : undefined;

  // -- site fields --
  // usableSlots: per-type base ± 2, clamped to the contract range 6–32.
  const usableSlots = Math.max(6, Math.min(32, defaults.baseSlots + rngSite.int(-2, 2)));

  // Energy magnitude: seed-derived in [0.30, 0.90].
  const energyMagnitude = 0.30 + rngSite.next01() * 0.60;

  return {
    contractVersion: 1,
    seed: src.seedKey,

    planet: {
      type:         planetType,
      habitability,
      atmosphere: {
        present: defaults.atmosPresent,
        kind:    defaults.atmosKind,
        density: defaults.atmosDensity,
      },
      nativeLife:    defaults.nativeLife,
      temperature:   defaults.temperature,
      waterCoverage: defaults.waterCoverage,
    },

    celestial: {
      star:                { kind: starKind, color: starColor },
      orbitAu,
      phaseDeg,
      rotationPeriodHours,
      axialTiltDeg,
      ...(moons    !== undefined && { moons }),
      ...(siblings !== undefined && { siblings }),
    },

    site: {
      shape:          defaults.shape,
      usableSlots,
      citadelCeiling: clampedLevel,
      energy: {
        source:    defaults.energySource,
        tier:      energyTierForLevel(clampedLevel),
        magnitude: energyMagnitude,
      },
      // TRUTHFULNESS: deposits and hazards are always [] — see module-level JSDoc.
      deposits: [],
      hazards:  [],
    },
  };
}

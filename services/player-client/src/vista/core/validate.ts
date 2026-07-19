/**
 * Vista Engine — Input validation and random input synthesis
 *
 * validateInput:   called by the engine at the start of every generate() run and
 *                  at mount time.  Returns {ok, errors[]} — NEVER throws.
 *
 * randomVistaInput: the lab's "Randomize" helper.  Builds a valid MVP-level
 *                   VistaInput (habitability / atmosphere / star) from the seeded
 *                   PRNG.  Uses a dedicated child stream so it does not consume
 *                   any of the 10 pipeline sub-streams used inside generateVista().
 */

import { VistaInput, PlanetType, StarKind, VISTA_CONTRACT_VERSION } from '../contract';
import { SeededRng, deriveChildSeed } from './rng';

// ---------------------------------------------------------------------------
// Internal helpers
// ---------------------------------------------------------------------------

/** All known PlanetType values for membership checks. */
const VALID_PLANET_TYPES: readonly PlanetType[] = [
  'TERRAN', 'DESERT', 'OCEANIC', 'ICE', 'VOLCANIC', 'GAS_GIANT',
  'BARREN', 'JUNGLE', 'ARCTIC', 'TROPICAL', 'MOUNTAINOUS', 'ARTIFICIAL',
];

/** All known StarKind values for membership checks. */
const VALID_STAR_KINDS: readonly StarKind[] = [
  'M_DWARF', 'K_ORANGE', 'G_YELLOW', 'F_WHITE', 'A_BLUE',
  'B_BLUE_GIANT', 'O_BLUE_SUPER', 'RED_GIANT', 'WHITE_DWARF', 'NEUTRON', 'BLACK_HOLE',
];

function clamp01(v: number): boolean {
  return typeof v === 'number' && v >= 0 && v <= 1;
}

// ---------------------------------------------------------------------------
// validateInput
// ---------------------------------------------------------------------------

/**
 * Validate a VistaInput.  Returns {ok: true, errors: []} on a clean input;
 * {ok: false, errors: [...]} with human-readable messages on any problem.
 *
 * The engine degrades gracefully on missing optional fields (site, grid, moons,
 * etc.) — those are not errors.  Only missing required fields and out-of-range
 * values are reported.  Unknown PlanetType / StarKind values are reported as
 * warnings (engine uses a generic fallback, not a crash).
 *
 * Guarantees: this function never throws.
 */
export function validateInput(input: VistaInput): { ok: boolean; errors: string[] } {
  const errors: string[] = [];

  // -- contract version --
  if (input == null) {
    return { ok: false, errors: ['input is null or undefined'] };
  }
  if ((input as VistaInput).contractVersion !== 1) {
    errors.push(
      `contractVersion must be 1, got ${(input as unknown as { contractVersion: unknown }).contractVersion}`
    );
  }

  // -- seed --
  if (!input.seed || typeof input.seed !== 'string' || input.seed.trim() === '') {
    errors.push('seed must be a non-empty string');
  }

  // -- planet (required block) --
  if (!input.planet) {
    errors.push('planet is required');
  } else {
    if (!VALID_PLANET_TYPES.includes(input.planet.type)) {
      // Note: engine falls back to generic, this is a warning
      errors.push(
        `planet.type "${input.planet.type}" is not a known PlanetType — engine will use generic fallback`
      );
    }
    if (
      typeof input.planet.habitability !== 'number' ||
      input.planet.habitability < 0 ||
      input.planet.habitability > 100
    ) {
      errors.push('planet.habitability must be a number in 0..100');
    }
    if (!input.planet.atmosphere || typeof input.planet.atmosphere.present !== 'boolean') {
      errors.push('planet.atmosphere.present (boolean) is required');
    } else {
      if (typeof input.planet.atmosphere.density !== 'number' || !clamp01(input.planet.atmosphere.density)) {
        errors.push('planet.atmosphere.density must be a number in 0..1');
      }
    }
    // P1 optional fields — validate range when provided
    if (input.planet.nativeLife !== undefined && !clamp01(input.planet.nativeLife)) {
      errors.push('planet.nativeLife must be 0..1 when present');
    }
    if (
      input.planet.temperature !== undefined &&
      (typeof input.planet.temperature !== 'number' ||
        input.planet.temperature < -1 ||
        input.planet.temperature > 1)
    ) {
      errors.push('planet.temperature must be -1..+1 when present');
    }
    if (input.planet.waterCoverage !== undefined && !clamp01(input.planet.waterCoverage)) {
      errors.push('planet.waterCoverage must be 0..1 when present');
    }
  }

  // -- celestial (required block) --
  if (!input.celestial) {
    errors.push('celestial is required');
  } else {
    if (!input.celestial.star) {
      errors.push('celestial.star is required');
    } else {
      if (!VALID_STAR_KINDS.includes(input.celestial.star.kind)) {
        errors.push(
          `celestial.star.kind "${input.celestial.star.kind}" is not a known StarKind — engine will use G_YELLOW fallback`
        );
      }
      if (!input.celestial.star.color || typeof input.celestial.star.color !== 'string') {
        errors.push('celestial.star.color must be a non-empty hex string');
      }
    }
    // P2 optional range checks
    if (
      input.celestial.orbitAu !== undefined &&
      (typeof input.celestial.orbitAu !== 'number' || input.celestial.orbitAu <= 0)
    ) {
      errors.push('celestial.orbitAu must be > 0 when present');
    }
    if (
      input.celestial.rotationPeriodHours !== undefined &&
      (typeof input.celestial.rotationPeriodHours !== 'number' || input.celestial.rotationPeriodHours <= 0)
    ) {
      errors.push('celestial.rotationPeriodHours must be > 0 when present');
    }
  }

  // -- site (optional, validate when present) --
  if (input.site != null) {
    const { site } = input;
    if (typeof site.usableSlots !== 'number' || site.usableSlots < 6 || site.usableSlots > 32) {
      errors.push('site.usableSlots must be 6..32');
    }
    if (![1, 2, 3, 4, 5].includes(site.citadelCeiling)) {
      errors.push('site.citadelCeiling must be 1, 2, 3, 4, or 5');
    }
    if (!site.energy || ![1, 2, 3, 4].includes(site.energy.tier)) {
      errors.push('site.energy.tier must be 1..4');
    }
    if (!Array.isArray(site.deposits)) {
      errors.push('site.deposits must be an array');
    }
    if (!Array.isArray(site.hazards)) {
      errors.push('site.hazards must be an array');
    }
  }

  // -- grid (optional, validate when present) --
  if (input.grid != null) {
    const { grid } = input;
    if (typeof grid.cols !== 'number' || grid.cols < 1) {
      errors.push('grid.cols must be >= 1');
    }
    if (typeof grid.rows !== 'number' || grid.rows < 1) {
      errors.push('grid.rows must be >= 1');
    }
    if (!clamp01(grid.revealFraction)) {
      errors.push('grid.revealFraction must be 0..1');
    }
  }

  return { ok: errors.length === 0, errors };
}

// ---------------------------------------------------------------------------
// randomVistaInput  — the lab's "Randomize" helper
// ---------------------------------------------------------------------------

/**
 * Approximate canonical hex colors for each StarKind.
 * Used only by randomVistaInput for lab preview — palette.ts owns authoritative
 * per-scene colors derived from star.kind at pipeline time.
 */
const STAR_COLORS: Record<StarKind, string> = {
  M_DWARF:      '#ff6030',
  K_ORANGE:     '#ffaa60',
  G_YELLOW:     '#fff4d0',
  F_WHITE:      '#e8f0ff',
  A_BLUE:       '#c8d8ff',
  B_BLUE_GIANT: '#b0c8ff',
  O_BLUE_SUPER: '#a0b8ff',
  RED_GIANT:    '#ff3010',
  WHITE_DWARF:  '#e0f0ff',
  NEUTRON:      '#d0c0ff',
  BLACK_HOLE:   '#0a0010',
};

/**
 * Build a valid MVP VistaInput from a string seed and a PlanetType.
 * Used by the lab's Randomize button; the resulting input is then passed
 * directly to VistaEngine.generate().
 *
 * The PRNG stream used here ('lab-input') is distinct from the 10 pipeline
 * sub-streams in SeedBus — it does not consume or shift any of them.
 */
export function randomVistaInput(seed: string, type: PlanetType): VistaInput {
  const rng = new SeededRng(deriveChildSeed(seed, 'lab-input'));

  const starKind = VALID_STAR_KINDS[rng.int(0, VALID_STAR_KINDS.length - 1)];
  const starColor = STAR_COLORS[starKind];

  // Planets without atmospheres (vacuum worlds) should lean toward absent,
  // but the toggle is the authoritative lab control — default sensibly by type.
  const atmosphereAbsentByDefault: PlanetType[] = ['BARREN'];
  const atmospherePresent = atmosphereAbsentByDefault.includes(type)
    ? rng.next01() > 0.8   // rare atmosphere on barren/airless
    : rng.next01() > 0.1;  // usually present on everything else

  // Reasonable rotation period distribution matching §3.2 guidance
  let rotationPeriodHours: number;
  const rotRoll = rng.next01();
  if (rotRoll < 0.05) {
    rotationPeriodHours = Math.round(rng.int(180, 600) * 10) / 10;  // tidally locked
  } else if (rotRoll < 0.40) {
    rotationPeriodHours = Math.round(rng.int(60, 140) * 10) / 10;   // fast spinner
  } else {
    rotationPeriodHours = Math.round(rng.int(200, 480) * 10) / 10;  // large rocky
  }

  // ── Seeded celestial features (drawn after all §3.2 fields) ────────────────
  // Order within this block is fixed; adding new features must go at the end to
  // avoid shifting the PRNG stream for seeds already in use.

  // Ring system on the planet itself (arc visible overhead in the sky)
  const rings = rng.next01() < 0.20 ? true : undefined;

  // Secondary star — binary system (~15% of seeds)
  let secondary: { kind: StarKind; color: string } | undefined;
  if (rng.next01() < 0.15) {
    const secKind = VALID_STAR_KINDS[rng.int(0, VALID_STAR_KINDS.length - 1)];
    secondary = { kind: secKind, color: STAR_COLORS[secKind] };
  }

  // Moons: 0–3, uniform distribution across 0,1,2,3 (rng.int is inclusive-uniform).
  const moonCount = rng.int(0, 3);
  const moons: NonNullable<VistaInput['celestial']['moons']> = [];
  for (let i = 0; i < moonCount; i++) {
    moons.push({
      sizeClass: rng.int(1, 3),
      phaseDeg:  rng.int(0, 359),
      hasRings:  rng.next01() < 0.10 ? true : undefined,
    });
  }

  // Sibling bodies visible in the sky (~40% of seeds have 1–2 siblings)
  const SIBLING_KINDS = ['GAS_GIANT', 'BARREN', 'TERRAN', 'ICE', 'VOLCANIC', 'OCEANIC'] as const;
  const siblingCount = rng.next01() < 0.40 ? rng.int(1, 2) : 0;
  const siblings: NonNullable<VistaInput['celestial']['siblings']> = [];
  for (let i = 0; i < siblingCount; i++) {
    siblings.push({
      kind:      SIBLING_KINDS[rng.int(0, SIBLING_KINDS.length - 1)],
      sizeClass: rng.int(1, 3),
      phaseDeg:  rng.int(0, 359),
      hue:       rng.int(0, 359),
      sat:       Math.round((rng.next01() * 0.6 + 0.2) * 100) / 100,
    });
  }

  // Sector nebula wash (~30% of seeds)
  let nebula: NonNullable<VistaInput['celestial']['nebula']> | undefined;
  if (rng.next01() < 0.30) {
    nebula = {
      hue:     rng.int(0, 359),
      density: Math.round((rng.next01() * 0.7 + 0.1) * 100) / 100,
    };
  }

  return {
    contractVersion: VISTA_CONTRACT_VERSION,
    seed,

    planet: {
      type,
      habitability: rng.int(0, 100),
      atmosphere: {
        present: atmospherePresent,
        kind: null,    // lab starts neutral; the pipeline derives kind from type
        density: rng.next01(),
      },
      nativeLife: rng.next01(),
      temperature: rng.next01() * 2 - 1,
      waterCoverage: rng.next01(),
    },

    celestial: {
      star: { kind: starKind, color: starColor, secondary },
      orbitAu: Math.round((rng.next01() * 1.8 + 0.3) * 100) / 100,
      phaseDeg: rng.int(0, 359),
      rotationPeriodHours,
      axialTiltDeg: rng.int(0, 45),
      moons:    moons.length > 0 ? moons : undefined,
      rings,
      siblings: siblings.length > 0 ? siblings : undefined,
      nebula,
    },
  };
}

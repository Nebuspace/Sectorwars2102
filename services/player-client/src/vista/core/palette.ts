/**
 * Vista Engine — Palette derivation
 *
 * derivePalette: data-drives the landedPalette() logic from a PlanetProfile,
 * applying per-seed jitter within the type's coherence envelope and honoring
 * habitability (flora lushness) + atmosphere (haze / vacuum path).
 *
 * Ported from SolarSystemViewscreen.tsx landedPalette() (L1811).  The key
 * difference: colors are now driven by PlanetProfile data, not hardcoded
 * switch cases — adding a type is a data-only change in profiles.ts.
 *
 * Pure function: no DOM, no Math.random(), no module-level mutable state.
 * Same arguments → byte-identical result.
 */

import { VistaInput, VistaModel, RGB } from '../contract';
import { SeededRng } from './rng';
import { PlanetProfile, ArchetypeEntry } from './profiles';

// ---------------------------------------------------------------------------
// Internal math helpers
// ---------------------------------------------------------------------------

function clamp(v: number, lo: number, hi: number): number {
  return v < lo ? lo : v > hi ? hi : v;
}

function clamp01(v: number): number {
  return clamp(v, 0, 1);
}

function lerp(a: number, b: number, t: number): number {
  return a + (b - a) * t;
}

function lerpRgb(a: RGB, b: RGB, t: number): RGB {
  return [
    Math.round(lerp(a[0], b[0], t)),
    Math.round(lerp(a[1], b[1], t)),
    Math.round(lerp(a[2], b[2], t)),
  ];
}

/**
 * Apply seeded per-channel jitter clamped to ±maxDelta.
 * Draws exactly 3 values from rng (one per channel).  Channels are clamped
 * to [0, 255] so the result is always a valid sRGB triple.
 */
function jitterRgb(rng: SeededRng, base: RGB, maxDelta: number): RGB {
  return [
    clamp(Math.round(base[0] + (rng.next01() * 2 - 1) * maxDelta), 0, 255),
    clamp(Math.round(base[1] + (rng.next01() * 2 - 1) * maxDelta), 0, 255),
    clamp(Math.round(base[2] + (rng.next01() * 2 - 1) * maxDelta), 0, 255),
  ];
}

// ---------------------------------------------------------------------------
// Hex → RGB
// ---------------------------------------------------------------------------

/**
 * Parse a '#rrggbb' hex string to an sRGB triple.
 * Returns [0, 0, 0] on parse failure — never throws; the engine degrades
 * gracefully on bad star.color input (BRIEF §2.2 degradation rule).
 */
export function hexToRgb(hex: string): RGB {
  const m = /^#?([0-9a-fA-F]{2})([0-9a-fA-F]{2})([0-9a-fA-F]{2})$/.exec(hex.trim());
  if (!m) return [0, 0, 0];
  return [parseInt(m[1], 16), parseInt(m[2], 16), parseInt(m[3], 16)];
}

// ---------------------------------------------------------------------------
// Accent derivation (deposit glow / energy signature)
// ---------------------------------------------------------------------------

/**
 * Derive the scene accent color from the site's primary energy source.
 * Absent site → fall back to the profile's default accent.
 *
 * Energy source → accent (truthful visual signal, BRIEF §2.5):
 *   GEOTHERMAL → orange/red thermal glow
 *   TIDAL      → blue-white surf highlight
 *   SOLAR      → bright yellow
 *   WIND       → pale blue-white
 */
function deriveAccent(profile: PlanetProfile, input: VistaInput): RGB {
  if (!input.site) return profile.basePalette.accent;
  switch (input.site.energy.source) {
    case 'GEOTHERMAL': return [255,  90,  20];
    case 'TIDAL':      return [160, 220, 240];
    case 'SOLAR':      return [255, 230,  80];
    case 'WIND':       return [200, 220, 255];
  }
}

// ---------------------------------------------------------------------------
// derivePalette  — public export
// ---------------------------------------------------------------------------

/**
 * Derive the full VistaModel palette from profile data, seeded jitter, and
 * the runtime VistaInput.
 *
 * Pipeline stages:
 *   1. Base anchors from profile.basePalette
 *   2. Per-seed jitter within coherence.deltaEEnvelope (palette rng stream)
 *   3. Habitability → flora lerp (floraMin → floraMax at hab 0 → 100)
 *   4. Atmosphere absent → vacuum path: sky goes near-black, scatterBand = [0,0,0]
 *   5. Water + foam present only for aquatic profile types
 *   6. Geology bands: 2 steps interpolated between surface and ridgeFar
 *   7. Accent from site energy source or profile default
 *
 * @param profile   Planet's PlanetProfile data record.
 * @param input     Full VistaInput (reads habitability, atmosphere, site).
 * @param archetype Archetype chosen in pipeline stage 2 (used for future
 *                  per-archetype palette sub-variants; carried in signature
 *                  per WO contract).
 * @param rng       The 'palette' named sub-stream from SeedBus.
 */
export function derivePalette(
  profile: PlanetProfile,
  input: VistaInput,
  archetype: ArchetypeEntry,
  rng: SeededRng,
): VistaModel['palette'] {
  // Suppress unused-variable warning; archetype is reserved for P1 sub-variants.
  void archetype;

  const bp          = profile.basePalette;
  const env         = profile.coherence.deltaEEnvelope;
  const hab01       = clamp01(input.planet.habitability / 100);
  const atmoPresent = input.planet.atmosphere.present;

  // Stage 1–2: anchors + jitter.  Sky top gets 60% of the envelope (it's a
  // large solid fill — too much jitter reads as "different type").
  const skyHorizon = jitterRgb(rng, bp.skyHorizon, env);
  const ridgeFar   = jitterRgb(rng, bp.ridgeFar,   env * 0.8);
  const ridgeMid   = jitterRgb(rng, bp.ridgeMid,   env * 0.8);
  const ridgeNear  = jitterRgb(rng, bp.ridgeNear,  env * 0.6);
  const surface    = jitterRgb(rng, bp.surface,    env * 0.7);

  // Stage 4: atmosphere absent → vacuum sky (near-black, faint blue tint).
  // The 0.15 factor keeps the sky from being fully black on dark monitor
  // calibrations while reading unmistakably as "no atmosphere."
  const skyTopBase: RGB = atmoPresent
    ? jitterRgb(rng, bp.skyTop, env * 0.6)
    : [
        Math.round(bp.skyTop[0] * 0.15),
        Math.round(bp.skyTop[1] * 0.15),
        Math.round(bp.skyTop[2] * 0.18),
      ];

  // Temperature tint: cold (temp ≤ −1) shifts skyTop toward icy blue;
  // hot (temp ≥ +1) shifts toward amber/orange.  Vacuum sky unchanged — its
  // near-black hue already reads as extreme environment.
  const temp01: number = clamp01((input.planet.temperature + 1) / 2);  // −1..+1 → 0..1
  const ICY_BLUE:  RGB = [120, 160, 220];
  const AMBER_HOT: RGB = [210, 130,  60];
  const skyTop: RGB = atmoPresent
    ? (temp01 < 0.5
        ? lerpRgb(skyTopBase, ICY_BLUE,  (0.5 - temp01) * 0.55)   // up to 27% blue push at temp=−1
        : lerpRgb(skyTopBase, AMBER_HOT, (temp01 - 0.5) * 0.44))  // up to 22% amber push at temp=+1
    : skyTopBase;

  // Stage 4b: scatter band disappears completely in vacuum (no Rayleigh).
  const scatterBand: RGB = atmoPresent
    ? jitterRgb(rng, bp.scatterBand, env * 0.5)
    : [0, 0, 0];

  // Stage 3: habitability → flora tint.  Lerp from floraMin (barren) to
  // floraMax (lush).  High hab also brightens the flora slightly (×1.1
  // on all channels, clamped) to make lush worlds visually pop.
  const floraBase = lerpRgb(bp.floraMin, bp.floraMax, hab01);
  const floraBright = hab01 > 0.7 ? 1.10 : 1.0;
  const flora: RGB = [
    clamp(Math.round(floraBase[0] * floraBright), 0, 255),
    clamp(Math.round(floraBase[1] * floraBright), 0, 255),
    clamp(Math.round(floraBase[2] * floraBright), 0, 255),
  ];

  // Stage 5: water + foam only when the profile declares an aquatic mode.
  const water: RGB | undefined = bp.water
    ? jitterRgb(rng, bp.water, env * 0.5)
    : undefined;
  const foam: RGB | undefined = bp.foam
    ? jitterRgb(rng, bp.foam, env * 0.4)
    : undefined;

  // Stage 6: geology bands — 2 interpolation steps between surface and far
  // ridge color, giving the ground plane visible rock strata variation.
  const geologyBands: RGB[] = [
    lerpRgb(surface, ridgeFar, 0.33),
    lerpRgb(surface, ridgeFar, 0.66),
  ];

  // Stage 7: accent.
  const accent = deriveAccent(profile, input);

  // Stage 8: warm secondary accent — passed through without jitter (it is a
  // designed engineering constant, not a natural palette sample).
  // Present only when the profile sets it (currently: ARTIFICIAL only).
  const accentWarm: RGB | undefined = bp.accentWarm;

  return {
    skyTop,
    skyHorizon,
    scatterBand,
    ridge:        [ridgeFar, ridgeMid, ridgeNear],
    surface,
    geologyBands,
    flora,
    ...(water      !== undefined ? { water }      : {}),
    ...(foam       !== undefined ? { foam  }      : {}),
    accent,
    ...(accentWarm !== undefined ? { accentWarm } : {}),
  };
}

/**
 * Vista Engine — Core generation pipeline
 *
 * generateVista(input): VistaModel
 *   Pure + deterministic.  No DOM, no Math.random(), no Date.now(), no game
 *   imports, no module-level mutable state.  Same VistaInput (ignoring .view)
 *   → byte-identical VistaModel every call, every JS runtime.
 *
 * Implements the §2.7 ordered pipeline:
 *   1 profile lookup
 *   2 archetype pick          (SeedBus 'archetype' stream)
 *   3 palette derivation      (SeedBus 'palette'   stream)
 *   4 desirability + lighting
 *   5 sky + celestial         (SeedBus 'sky' + 'celestial' streams)
 *   6 atmosphere              (SeedBus 'atmo'      stream)
 *   7 terrain                 (SeedBus 'terrain'   stream)
 *   8 water / lava            (SeedBus 'water'     stream; aquatic only)
 *   9 features                (SeedBus 'features'  stream)
 *  10 hazards                 (SeedBus 'hazard'    stream; site-gated)
 *  11 grid overlay            (SeedBus 'grid'      stream; no-op at P0)
 *  12 assemble + validate → invariants
 *
 * site absent  → stages 9 (depositMarkers/energyMarker) and 10 no-op.
 * grid absent  → stage 11 no-op (layers.grid omitted from output).
 *
 * Reference canvas for pixel coordinates: 1440 × 900 (BRIEF §3.4).
 * The renderer scales these at draw time.
 */

import { VistaInput, VistaModel, RGB } from '../contract';
import { SeedBus, SeededRng, deriveChildSeed } from './rng';
import { getProfile, isProfiledType, ArchetypeEntry, LandmarkKind, WaterType } from './profiles';
import { derivePalette, hexToRgb } from './palette';
import {
  placeFloraScatters,
  placeRockScatters,
  placeDepositMarkers,
  placeEnergyMarker,
  placeHazardOverlays,
  poissonDiskScatter,
} from './features';

// TerrainMode: declared in contract.ts + profiles.ts by Lane 1
// (PlanetProfile.terrainMode field + VistaModel.layers.terrain.mode field).
// Defined locally here so Lane 3 compiles independently.  Remove this alias
// and the two casts in generateVista when Lane 1 lands.
type TerrainMode = 'surface' | 'cloud-deck' | 'plating';

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

/** Average of a numeric array; returns 0 for empty input. */
function avg(arr: number[]): number {
  if (arr.length === 0) return 0;
  return arr.reduce((s, v) => s + v, 0) / arr.length;
}

/** Sample uniformly within [lo, hi] using the rng. */
function sampleRange(rng: SeededRng, lo: number, hi: number): number {
  return lo + rng.next01() * (hi - lo);
}

// ---------------------------------------------------------------------------
// Stage 4a — scoreDesirability  (BRIEF §2.5)
// ---------------------------------------------------------------------------

/**
 * Composite beauty budget 0–1.  When site is absent (P0 / pre-expedition),
 * degrades to habitability-only as specified in BRIEF §2.5.
 */
function scoreDesirability(input: VistaInput): number {
  const hab = input.planet.habitability / 100;
  if (!input.site) return clamp01(hab);                         // [MVP] degraded mode
  const rich   = avg(input.site.deposits.map(d => d.richness)); // weight 0.30
  const energy = input.site.energy.tier / 4;                    // weight 0.15
  const safe   = 1 - avg(input.site.hazards.map(h => h.severity)); // weight 0.15
  return clamp01(0.40 * hab + 0.30 * rich + 0.15 * energy + 0.15 * safe);
}

// ---------------------------------------------------------------------------
// Stage 4b — deriveLighting
// ---------------------------------------------------------------------------

/**
 * Star kind → scene light table (BRIEF §3.2).
 * disc× = radiusPx multiplier at 1440px reference width.
 * dayBright = keyIntensity ceiling.
 */
const STAR_LIGHT_TABLE: Record<string, { discMul: number; dayBright: number }> = {
  M_DWARF:      { discMul: 1.25, dayBright: 0.75 },
  K_ORANGE:     { discMul: 1.10, dayBright: 0.85 },
  G_YELLOW:     { discMul: 1.00, dayBright: 1.00 },
  F_WHITE:      { discMul: 0.95, dayBright: 1.05 },
  A_BLUE:       { discMul: 0.85, dayBright: 1.10 },
  B_BLUE_GIANT: { discMul: 1.20, dayBright: 1.15 },
  O_BLUE_SUPER: { discMul: 1.40, dayBright: 1.20 },
  RED_GIANT:    { discMul: 1.80, dayBright: 0.80 },
  WHITE_DWARF:  { discMul: 0.40, dayBright: 0.70 },
  NEUTRON:      { discMul: 0.25, dayBright: 0.40 },
  BLACK_HOLE:   { discMul: 0.00, dayBright: 0.10 },
};

function deriveLighting(
  input: VistaInput,
  palette: VistaModel['palette'],
  desirability: number,
  sunAzimuth: number,
  sunElevation: number,
): VistaModel['lighting'] {
  const starKind  = input.celestial.star.kind;
  const starColor = hexToRgb(input.celestial.star.color);
  const table     = STAR_LIGHT_TABLE[starKind] ?? STAR_LIGHT_TABLE.G_YELLOW;

  // keyIntensity: star day-brightness ceiling, modulated by desirability
  // (beautiful worlds get slightly warmer/brighter lighting)
  const keyIntensity = clamp01(table.dayBright * (0.85 + desirability * 0.15));

  // bloom: rises with desirability (drives god-rays, saturation boost)
  const bloom = clamp01(desirability * 0.9 + 0.05);

  // colorGradeWarmth: temperature is the primary thermal signal (−1=frozen → +1=molten).
  // Cold worlds grade blue/cool; hot worlds grade amber/warm.
  // Desirability + habitability add a secondary warmth boost on beautiful worlds.
  const hab01  = clamp01(input.planet.habitability / 100);
  const temp01 = clamp01((input.planet.temperature + 1) / 2);   // −1..+1 → 0..1
  const colorGradeWarmth = clamp(
    lerp(-0.7, 0.9, temp01 * 0.65 + desirability * 0.25 + hab01 * 0.10),
    -1, 1,
  );

  // ambient: soft tint from sky horizon color (light bounces off atmosphere)
  const atmoFactor = input.planet.atmosphere.present ? 0.35 : 0.10;
  const ambient: RGB = [
    Math.round(palette.skyHorizon[0] * atmoFactor),
    Math.round(palette.skyHorizon[1] * atmoFactor),
    Math.round(palette.skyHorizon[2] * atmoFactor),
  ];

  // fill: cool, dim counterpoint to the key (fill from the opposite sky region)
  const fill: RGB = [
    Math.round(palette.skyTop[0] * 0.18),
    Math.round(palette.skyTop[1] * 0.22),
    Math.round(palette.skyTop[2] * 0.28),
  ];

  // keyColor: blend star color toward warm white at high desirability
  const warm: RGB = [255, 240, 210];
  const keyColor = lerpRgb(starColor, warm, desirability * 0.4);

  return {
    keyDir:           [sunAzimuth, sunElevation],
    keyColor,
    keyIntensity,
    ambient,
    fill,
    bloom,
    colorGradeWarmth,
  };
}

// ---------------------------------------------------------------------------
// Stage 5a — buildSky
// ---------------------------------------------------------------------------

function buildSky(
  input: VistaInput,
  palette: VistaModel['palette'],
  rng: SeededRng,
  desirability: number,
): VistaModel['layers']['sky'] {
  const atmoPresent = input.planet.atmosphere.present;
  const atmoDensity = atmoPresent ? input.planet.atmosphere.density : 0;
  const hab01       = clamp01(input.planet.habitability / 100);

  // Gradient: 2 stops normally; 3 when atmosphere is present (scatter band)
  const gradient: { stop: number; color: RGB }[] = [
    { stop: 0,    color: palette.skyTop },
    { stop: 1,    color: palette.skyHorizon },
  ];
  if (atmoPresent && atmoDensity > 0.1) {
    gradient.splice(1, 0, {
      stop:  0.72 + rng.next01() * 0.08,
      color: palette.scatterBand,
    });
  }

  // Scatter bands: appear near the horizon when atmosphere is present
  const scatterBands: { y: number; color: RGB; width: number }[] = [];
  if (atmoPresent && atmoDensity > 0.2) {
    const bandCount = rng.int(1, 2);
    for (let i = 0; i < bandCount; i++) {
      scatterBands.push({
        y:     0.78 - rng.next01() * 0.15,  // just above horizon
        color: palette.scatterBand,
        // Width scales with density: sparse at low density, full band at high density.
        // The rng draw is preserved (same sequence); only the output scaling changes.
        width: (0.04 + rng.next01() * 0.08) * clamp01(0.3 + atmoDensity * 0.7),
      });
    }
  }

  // Haze: quadratic density curve for dramatic aerial-perspective at high density
  // and near-vacuum clarity approaching zero.  Vacuum → exactly 0 (no haze).
  const hazeDensityRaw = atmoPresent ? atmoDensity : 0;
  const haze: { density: number; color: RGB } = {
    density: clamp01(hazeDensityRaw * (0.3 + hazeDensityRaw * 0.7)),
    color:   palette.scatterBand,
  };

  // starCount: 30 (low-hab atmo) → up to 220 (high-hab + high-desirability).
  // Desirability boosts night-sky density — a lush, rich world glitters.
  // Vacuum worlds always show full density regardless (BRIEF §3.3 airless stars-at-noon).
  const starBase  = Math.round(lerp(30, 180, hab01));
  const starCount = atmoPresent
    ? Math.round(clamp(starBase * lerp(0.80, 1.35, desirability), 30, 220))
    : 200;

  return { gradient, scatterBands, haze, starCount };
}

// ---------------------------------------------------------------------------
// Stage 5b — buildCelestial
// ---------------------------------------------------------------------------

/** Reference canvas dimensions for pixel-position calculations (BRIEF §3.4). */
const REF_W = 1440;
const REF_H = 900;
const SKY_H = REF_H * 0.78; // sky dome height in reference pixels

function buildCelestial(
  input: VistaInput,
  rng: SeededRng,
): VistaModel['layers']['celestial'] {
  const starKind  = input.celestial.star.kind;
  const starColor = hexToRgb(input.celestial.star.color);
  const table     = STAR_LIGHT_TABLE[starKind] ?? STAR_LIGHT_TABLE.G_YELLOW;

  // Sun: seeded position within the sky dome.
  // azimuth in [30, 330] (degrees; avoids extreme left/right edges).
  // elevation in [20, 70] (degrees above horizon; mid-day range for P0).
  const sunAzimuth   = rng.int(30, 330);
  const sunElevation = rng.int(20, 70);
  const sunX = (sunAzimuth / 360) * REF_W;
  const sunY = (1 - sunElevation / 90) * SKY_H;

  // Base sun radius: 28px at G_YELLOW reference; scaled by disc multiplier.
  const sunRadiusPx = Math.round(28 * table.discMul);

  // glow: stronger for hotter / higher-desirability contexts
  const sunGlow = clamp01(table.dayBright * 0.7);

  const suns: VistaModel['layers']['celestial']['suns'] = [];

  if (table.discMul > 0) {
    const special: 'accretion' | 'pulsar' | undefined =
      starKind === 'BLACK_HOLE' ? 'accretion'
      : starKind === 'NEUTRON'  ? 'pulsar'
      : undefined;
    suns.push({
      pos:      [sunX, sunY],
      radiusPx: sunRadiusPx,
      color:    starColor,
      glow:     sunGlow,
      ...(special ? { special } : {}),
    });
  } else {
    // BLACK_HOLE: accretion disc marker at horizon; no disc
    suns.push({
      pos:      [sunX, SKY_H * 0.8],
      radiusPx: 0,
      color:    [0, 0, 0],
      glow:     0,
      special:  'accretion',
    });
  }

  // Secondary star (binary system)
  if (input.celestial.star.secondary) {
    const sec = input.celestial.star.secondary;
    const secTable = STAR_LIGHT_TABLE[sec.kind] ?? STAR_LIGHT_TABLE.G_YELLOW;
    const secColor = hexToRgb(sec.color);
    const secOffsetX = rng.int(-200, 200);
    const secOffsetY = rng.int(-80, 80);
    if (secTable.discMul > 0) {
      suns.push({
        pos:      [clamp(sunX + secOffsetX, 80, REF_W - 80), clamp(sunY + secOffsetY, 20, SKY_H * 0.6)],
        radiusPx: Math.round(20 * secTable.discMul),
        color:    secColor,
        glow:     clamp01(secTable.dayBright * 0.5),
      });
    }
  }

  // Moons (P2 input; emit empty when absent)
  const moons: VistaModel['layers']['celestial']['moons'] = [];
  if (input.celestial.moons) {
    for (const moon of input.celestial.moons) {
      const mx = rng.int(100, REF_W - 100);
      const my = rng.int(40, Math.round(SKY_H * 0.55));
      const litFraction = clamp01((moon.phaseDeg % 360) / 360);
      moons.push({
        pos:         [mx, my],
        radiusPx:    Math.max(4, Math.round(moon.sizeClass * 3.5)),
        litFraction,
        hasRings:    moon.hasRings ?? false,
      });
    }
  }

  // Distant siblings (P2)
  const distant: VistaModel['layers']['celestial']['distant'] = [];
  if (input.celestial.siblings) {
    for (const sib of input.celestial.siblings) {
      const dx = rng.int(80, REF_W - 80);
      const dy = rng.int(30, Math.round(SKY_H * 0.5));
      distant.push({
        pos:      [dx, dy],
        radiusPx: Math.max(3, Math.round(sib.sizeClass * 2.5)),
        hue:      sib.hue,
        sat:      sib.sat,
      });
    }
  }

  // Ring arc for this planet (P2)
  let ringArc: VistaModel['layers']['celestial']['ringArc'] | undefined;
  if (input.celestial.rings) {
    ringArc = {
      tiltDeg: rng.int(10, 40),
      innerR:  58,
      outerR:  96,
      color:   [200, 195, 170],  // pale ring default
    };
  }

  // Stable starfield key: deterministic from seed, cached by renderer
  const starfieldSeedKey = `${input.seed}:starfield`;

  return {
    suns,
    moons,
    distant,
    starfieldSeedKey,
    ...(ringArc                  ? { ringArc }                           : {}),
    ...(input.celestial.nebula   ? { nebula: input.celestial.nebula }    : {}),
  };
}

// ---------------------------------------------------------------------------
// Stage 6 helpers — deriveParticles / deriveEvents
// ---------------------------------------------------------------------------

/**
 * Derive atmosphere particle emitters from the planet's physical attributes.
 *
 * Priority order (first match wins):
 *   no atmosphere   → []  (BARREN / vacuum)
 *   plating mode    → spark  (ARTIFICIAL tech signature)
 *   cloud-deck mode → dust   (GAS_GIANT band turbulence)
 *   temp ≥ 0.65     → ember + ash  (VOLCANIC)
 *   temp ≤ −0.25    → snow   (ICE, ARCTIC)
 *   wet + temperate → rain [+ spore at nativeLife ≥ 0.4]  (TERRAN, OCEANIC, JUNGLE, TROPICAL…)
 *   dry + warm      → dust   (DESERT)
 *   nativeLife ≥ 0.5 → spore  (exotic life-heavy mild worlds)
 *   default         → faint dust
 *
 * Fully deterministic: rates are derived from stable planet attributes
 * (temperature, waterCoverage, nativeLife), not from rng draws.
 * Colors are taken from the already-derived palette.
 */
function deriveParticles(
  input: VistaInput,
  profile: ReturnType<typeof getProfile>,
  palette: VistaModel['palette'],
): VistaModel['layers']['atmosphere']['particles'] {
  if (!input.planet.atmosphere.present) return [];

  const temp     = input.planet.temperature;    // −1..+1
  const water    = input.planet.waterCoverage;  //  0..1
  const life     = input.planet.nativeLife;     //  0..1
  const atmoKind = (input.planet.atmosphere.kind ?? '').toLowerCase();

  type P = VistaModel['layers']['atmosphere']['particles'][number];
  const out: P[] = [];

  // ARTIFICIAL — engineered substrate; sparks from active circuitry
  if (profile.terrainMode === 'plating') {
    out.push({ kind: 'spark', rate: clamp01(0.25 + life * 0.10), color: palette.accent });
    return out;
  }

  // GAS_GIANT cloud-deck — band-turbulence dust; not rain/snow (no surface)
  if (profile.terrainMode === 'cloud-deck') {
    out.push({ kind: 'dust', rate: clamp01(0.20 + Math.abs(temp) * 0.15), color: palette.skyHorizon });
    return out;
  }

  // Very hot / volcanic atmosphere — ember (primary) + ash (secondary)
  if (temp >= 0.65 || atmoKind.includes('volcanic') || atmoKind.includes('sulfur')) {
    out.push({ kind: 'ember', rate: clamp01(0.45 + temp * 0.30), color: palette.accent });
    out.push({ kind: 'ash',   rate: 0.40,                         color: [120, 110, 100] as RGB });
    return out;
  }

  // Cold / frozen atmosphere — snow
  if (temp <= -0.25 || atmoKind.includes('frozen') || atmoKind.includes('cryo')) {
    const snowRate = clamp01(0.40 + Math.max(0, -temp - 0.25) * 0.55);
    out.push({ kind: 'snow', rate: snowRate, color: [235, 246, 255] as RGB });
    return out;
  }

  // Wet + temperate — rain (primary); spore secondary at high nativeLife
  if (water >= 0.25 && temp > -0.25 && temp < 0.65) {
    out.push({ kind: 'rain',  rate: clamp01(0.30 + water * 0.50), color: [200, 220, 240] as RGB });
    if (life >= 0.40) {
      out.push({ kind: 'spore', rate: clamp01(life * 0.50), color: palette.flora });
    }
    return out;
  }

  // Dry + warm — dust (DESERT and similar arid worlds)
  if (water < 0.25 && temp > -0.15) {
    out.push({ kind: 'dust', rate: clamp01(0.30 + (1 - water) * 0.25), color: palette.scatterBand });
    return out;
  }

  // High nativeLife in mild dry conditions — spore dominates
  if (life >= 0.50) {
    out.push({ kind: 'spore', rate: clamp01(life * 0.55), color: palette.flora });
    return out;
  }

  // Default — faint ambient dust for any remaining atmospheric world
  out.push({ kind: 'dust', rate: clamp01(0.15 + water * 0.10), color: palette.scatterBand });
  return out;
}

/**
 * Derive weather/storm events from site hazards and atmosphere state.
 *
 * Matches the kind strings the renderer's skyDarken path consumes:
 *   'storm' | 'overcast' | 'ash-storm' → heavy darken (×0.6)
 *   'rain'  | 'snow'                   → light darken (×0.3)
 *
 * §3.3 cap: at most one primary + one ambient event active at once.
 * Volcanic atmosphere type always contributes an ambient 'ash-storm' event.
 */
function deriveEvents(
  input: VistaInput,
): VistaModel['layers']['atmosphere']['events'] {
  if (!input.planet.atmosphere.present) return [];

  const temp     = input.planet.temperature;
  const atmoKind = (input.planet.atmosphere.kind ?? '').toLowerCase();

  type E = VistaModel['layers']['atmosphere']['events'][number];
  const out: E[] = [];

  // Volcanic ambient: permanently active ash-storm event
  if (temp >= 0.65 || atmoKind.includes('volcanic') || atmoKind.includes('sulfur')) {
    out.push({
      kind:      'ash-storm',
      intensity: clamp01(0.30 + temp * 0.35),
      tint:      [180, 120, 80] as RGB,
    });
  }

  // Hazard-driven events; respect the §3.3 two-event cap
  if (input.site) {
    for (const h of input.site.hazards) {
      if (out.length >= 2) break;
      const k = h.kind.toLowerCase();
      if (k.includes('storm') || k.includes('lightning') || k.includes('hurricane') || k.includes('flood')) {
        out.push({ kind: 'storm',    intensity: h.severity,       tint: [140, 150, 170] as RGB });
      } else if ((k.includes('ash') || k.includes('erupt') || k.includes('smoke')) &&
                 !out.some(e => e.kind === 'ash-storm')) {
        out.push({ kind: 'ash-storm', intensity: h.severity,      tint: [160, 140, 120] as RGB });
      } else if (k.includes('blizzard') || k.includes('snowsquall')) {
        out.push({ kind: 'snow',     intensity: h.severity,       tint: [220, 235, 250] as RGB });
      } else if (k.includes('rain') || k.includes('precipit') || k.includes('monsoon')) {
        out.push({ kind: 'rain',     intensity: h.severity,       tint: [180, 200, 230] as RGB });
      } else if (k.includes('dust') || k.includes('sandstorm')) {
        out.push({ kind: 'overcast', intensity: h.severity * 0.6, tint: [190, 170, 130] as RGB });
      }
    }
  }

  return out;
}

// ---------------------------------------------------------------------------
// Stage 6 — buildAtmosphere
// ---------------------------------------------------------------------------

function buildAtmosphere(
  input: VistaInput,
  profile: ReturnType<typeof getProfile>,
  palette: VistaModel['palette'],
  rng: SeededRng,
): VistaModel['layers']['atmosphere'] {
  const atmoPresent = input.planet.atmosphere.present;
  const atmoDensity = input.planet.atmosphere.density;

  // Pick a cloud kind allowed by the profile's coherence guard.
  // When atmosphere is absent or density is very low, use 'none'.
  const validClouds = profile.coherence.cloudAllowList;
  const defaultCloud = profile.defaultCloud;
  const cloudKind: VistaModel['layers']['atmosphere']['clouds']['kind'] = !atmoPresent
    ? 'none'
    : (validClouds.includes(defaultCloud) ? defaultCloud : 'none');

  const cloudCoverage  = atmoPresent ? clamp01(atmoDensity * (0.4 + rng.next01() * 0.6)) : 0;
  const cloudDrift     = atmoPresent ? clamp01(0.3 + rng.next01() * 0.7) : 0;
  const cloudColor     = atmoPresent
    ? lerpRgb(palette.skyHorizon, [255, 255, 255], 0.55)
    : [0, 0, 0] as RGB;

  return {
    present: atmoPresent,
    clouds: {
      kind:     cloudKind,
      coverage: cloudCoverage,
      color:    cloudColor,
      drift:    cloudDrift,
    },
    events:    deriveEvents(input),
    particles: deriveParticles(input, profile, palette),
  };
}

// ---------------------------------------------------------------------------
// Stage 7 — buildTerrain
// ---------------------------------------------------------------------------

/**
 * Generate one ridge polyline.
 * Produces nSegments+1 [x, y] normalized points (x in [0,1], y in [0,1]).
 * The silhouette goes left→right along the ridge top; the renderer fills
 * everything below it.
 *
 * Each vertex y is a sum of:
 *   - a control height (low-frequency shape, one per segment)
 *   - a micro-roughness jitter (high-frequency, amplitude = roughness * 0.35)
 * Clamped to [0, horizonY] so ridges never bleed into the ground plane.
 */
function buildRidgePolyline(
  rng: SeededRng,
  nSegments: number,
  horizonY: number,
  amplitude: number,
  roughness: number,
): [number, number][] {
  // Sample one control height per vertex
  const heights: number[] = [];
  for (let i = 0; i <= nSegments; i++) {
    heights.push(rng.next01());
  }

  const poly: [number, number][] = [];
  for (let i = 0; i <= nSegments; i++) {
    const x = i / nSegments;
    // Smoothstep between adjacent control heights for low-freq shape
    const prev = heights[Math.max(0, i - 1)];
    const curr = heights[i];
    const t = rng.next01();
    const smooth = prev + (curr - prev) * (t * t * (3 - 2 * t));
    // Micro roughness jitter on top
    const micro = roughness > 0 ? (rng.next01() - 0.5) * roughness * 0.35 : 0;
    const h = clamp01(smooth + micro);
    // Ridge rises above horizon: y decreases with height
    const y = clamp(horizonY - h * amplitude, 0.0, horizonY);
    poly.push([x, y]);
  }

  return poly;
}

/**
 * Get a ridge fill color by interpolating through the palette.ridge array
 * based on the stratum's index (0=far, total-1=near).
 */
function getRidgeColor(ridgePalette: RGB[], idx: number, total: number): RGB {
  if (ridgePalette.length === 0) return [30, 30, 30];
  if (total <= 1) return ridgePalette[0];
  const t        = idx / (total - 1);           // 0=far, 1=near
  const rawPos   = t * (ridgePalette.length - 1);
  const lo       = Math.floor(rawPos);
  const hi       = Math.min(lo + 1, ridgePalette.length - 1);
  return lerpRgb(ridgePalette[lo], ridgePalette[hi], rawPos - lo);
}

function buildTerrain(
  archetype: ArchetypeEntry,
  palette: VistaModel['palette'],
  coherence: ReturnType<typeof getProfile>['coherence'],
  rng: SeededRng,
): VistaModel['layers']['terrain'] {
  const recipe = archetype.terrain;

  // Sample noise bounds, clamped to the type-level coherence envelopes.
  const horizonY = sampleRange(rng, recipe.horizonY[0], recipe.horizonY[1]);

  const rawAmp  = sampleRange(rng, recipe.amplitude[0], recipe.amplitude[1]);
  const amplitude = clamp(rawAmp, coherence.amplitudeBand[0], coherence.amplitudeBand[1]);

  const rawRough  = sampleRange(rng, recipe.roughness[0], recipe.roughness[1]);
  const roughness = clamp(rawRough, coherence.roughnessBand[0], coherence.roughnessBand[1]);

  const ridgeCount = rng.int(recipe.ridgeCount[0], recipe.ridgeCount[1]);

  const POLY_SEGS = 16; // vertices per ridge polyline

  // Build strata far→near.  Far strata are lower-amplitude (distance
  // compression); near strata get the full amplitude.
  const strata: VistaModel['layers']['terrain']['strata'] = [];
  for (let i = 0; i < ridgeCount; i++) {
    const depthFraction = ridgeCount > 1 ? i / (ridgeCount - 1) : 0.5; // 0=far, 1=near
    // Far ridges get 55% amplitude; near ridges get 100%
    const stratAmp  = amplitude * lerp(0.55, 1.0, depthFraction);
    // Far ridges are smoother (atmospheric perspective smooths texture)
    const stratRough = roughness * lerp(0.4, 1.0, depthFraction);

    const polyline = buildRidgePolyline(rng, POLY_SEGS, horizonY, stratAmp, stratRough);
    const fill     = getRidgeColor(palette.ridge, i, ridgeCount);
    // Parallax: far strata move least, near most
    const parallax = lerp(0.04, 0.22, depthFraction);

    strata.push({ polyline, fill, parallax });
  }

  // Ground plane: simple normalized quad from horizon to canvas bottom
  const groundPoly: [number, number][] = [
    [0, horizonY], [1, horizonY], [1, 1.0], [0, 1.0],
  ];

  // Slope profile: 10 normalized slope samples across the ground plane
  const slopeProfile: number[] = [];
  for (let i = 0; i < 10; i++) {
    slopeProfile.push((rng.next01() * 2 - 1) * 0.28);
  }

  // Landmarks: seeded from archetype's allow-list
  const landmarks: VistaModel['layers']['terrain']['landmarks'] = [];
  if (archetype.landmarks.length > 0) {
    const maxLm   = Math.min(3, archetype.landmarks.length);
    const lmCount = maxLm === 0 ? 0 : rng.int(1, maxLm);
    for (let i = 0; i < lmCount; i++) {
      const kind = rng.pick(archetype.landmarks) as LandmarkKind;
      landmarks.push({
        kind,
        pos: [
          0.08 + rng.next01() * 0.84,              // x: avoid far edges
          horizonY * (0.60 + rng.next01() * 0.30), // y: near the horizon
        ],
        scale: 0.04 + rng.next01() * 0.14,
      });
    }
  }

  return {
    horizonY,
    strata,
    groundPlane: {
      poly:         groundPoly,
      material:     recipe.groundMaterial,
      slopeProfile,
    },
    landmarks,
  };
}

// ---------------------------------------------------------------------------
// Stage 8 — buildWater  (aquatic profiles only)
// ---------------------------------------------------------------------------

function buildWater(
  profile: ReturnType<typeof getProfile>,
  palette: VistaModel['palette'],
  horizonY: number,
  waterCoverage: number,
  rng: SeededRng,
): VistaModel['layers']['water'] | undefined {
  if (profile.water === 'none') return undefined;
  // Verify this type allows this water type (coherence guard)
  if (!profile.coherence.waterAllowList.includes(profile.water as WaterType)) return undefined;

  // waterCoverage drives where the shoreline falls on the canvas (0=top, 1=bottom).
  // HIGH coverage (e.g. OCEANIC ~0.85) → thin land strip → waterlineY close to horizonY.
  // LOW  coverage (e.g. MOUNTAINOUS ~0.30) → wide land strip → waterlineY well below.
  // One seeded jitter draw (±0.03) adds per-seed variation while preserving the signal.
  //
  // profile.waterFootprint (WO-VISTA-MOUNTAINOUS-IDENTITY) overrides coverageBase
  // and the waveAmp/chop sample ranges below — present only on MOUNTAINOUS, so
  // every other profile's formula and rng-draw sequence is byte-identical to
  // before (same number of sampleRange/next01 calls either way, only the
  // bounds passed to them differ when the override is present).
  const wf = profile.waterFootprint;
  const coverageBase = wf?.coverageBase ?? lerp(0.45, 0.05, clamp01(waterCoverage));
  const jitter       = (rng.next01() - 0.5) * 0.06;  // same 1 rng draw consumed as before
  const waterlineY   = clamp(
    horizonY + coverageBase + jitter,
    horizonY + 0.04,                          // minimum: always a visible land strip above water
    Math.min(horizonY + 0.55, 0.97),          // maximum: enough water visible + canvas guard
  );
  const [waveAmpMin, waveAmpMax] = wf?.waveAmpRange ?? [0.004, 0.018];
  const [chopMin, chopMax]       = wf?.chopRange     ?? [0.1, 0.75];
  const waveAmp    = sampleRange(rng, waveAmpMin, waveAmpMax);
  const chop       = sampleRange(rng, chopMin, chopMax);
  const foamMul    = sampleRange(rng, 1.0, 2.4);
  const spraySpeedMul = sampleRange(rng, 0.8, 1.5);

  const waterColor = palette.water ?? [30, 80, 120];
  const foamColor  = palette.foam  ?? [200, 220, 220];

  return {
    waterlineY,
    type:          profile.water as VistaModel['layers']['water'] extends undefined ? never : VistaModel['layers']['water']['type'],
    color:         waterColor,
    foam:          foamColor,
    waveAmp,
    chop,
    foamMul,
    spraySpeedMul,
  };
}

// ---------------------------------------------------------------------------
// Stage 7a — buildCloudDeckTerrain  (GAS_GIANT special case)
// ---------------------------------------------------------------------------

/**
 * Emit a cloud-deck terrain layer for gas giants.
 *
 * GAS_GIANTs have no solid surface: no terrain strata, no landmarks, no rock
 * ground plane.  The horizonY here divides the sky wedge above from the
 * banded cloud deck below; the renderer branches on terrain.mode = 'cloud-deck'
 * to draw cloud bands rather than rock ridges.
 *
 * Draws exactly 1 float from rng (horizonY jitter in [0.50..0.60]).
 * Leaving most of the terrain rng stream unconsumed is intentional — other
 * streams are independent and are unaffected.
 */
function buildCloudDeckTerrain(rng: SeededRng): VistaModel['layers']['terrain'] {
  // Horizon sits mid-to-lower in the frame: generous sky area + cloud deck below.
  const horizonY   = 0.50 + rng.next01() * 0.10;   // [0.50..0.60]

  const groundPoly: [number, number][] = [
    [0, horizonY], [1, horizonY], [1, 1.0], [0, 1.0],
  ];

  return {
    horizonY,
    strata:    [],           // no ridge silhouettes — cloud banding lives in atmosphere layer
    groundPlane: {
      poly:         groundPoly,
      material:     'regolith', // stand-in; renderer overrides when mode = 'cloud-deck'
      slopeProfile: new Array(10).fill(0) as number[],
    },
    landmarks: [],           // no solid-surface landmarks on a gas giant
  };
}

// ---------------------------------------------------------------------------
// Stage 9 — buildFeatures
// ---------------------------------------------------------------------------

function buildFeatures(
  input: VistaInput,
  profile: ReturnType<typeof getProfile>,
  palette: VistaModel['palette'],
  horizonY: number,
  desirability: number,
  rng: SeededRng,
  waterlineY?: number,
): VistaModel['layers']['features'] {
  const hab01 = clamp01(input.planet.habitability / 100);

  // ── floraBandY1 — computed early (needed by citadel anchor + dense-flora loop) ──
  // Dry worlds use 90% of the ground height; aquatic worlds cap at the waterline.
  const groundY1    = horizonY + (1 - horizonY) * 0.90;
  const floraBandY1 = waterlineY !== undefined
    ? Math.min(waterlineY, groundY1)
    : groundY1;

  // ── Citadel clearing — site-gated; NO-OP when site absent or citadelCeiling=0 ──
  //
  // Derives a deterministic anchor position from an isolated 'citadel-anchor' child
  // seed so no other named RNG stream is shifted.  The anchor is the ground-level
  // base of the citadel structure in model space [0..1 × horizonY..1].
  //
  // Flora within clearingR of the anchor is dropped (after Poisson positions are
  // placed so RNG draw counts stay fixed).  Dense-flora draws (scale/tintMix/kind)
  // are always consumed regardless of acceptance to preserve the downstream sequence.
  //
  // Scale: citadelClearR grows with level so a larger base gets a wider clearing.
  //   L1 → 0.085  L2 → 0.100  L3 → 0.115  L4 → 0.130  L5 → 0.145
  const citadelLevel   = (input.site?.citadelCeiling ?? 0) as number;
  const planetType     = input.planet.type;
  // River mode: TERRAN + JUNGLE with water use a river corridor as the primary
  // citadel-prominence mechanism.  All other types (TROPICAL etc.) use the circular
  // clearing fallback — a river would look forced in those visual languages.
  const riverMode      = citadelLevel > 0 && waterlineY !== undefined
    && (planetType === 'TERRAN' || planetType === 'JUNGLE');
  let   citadelAnchorX = 0;
  let   citadelAnchorY = horizonY;
  let   citadelClearR2 = 0;
  // Corridor clearing in model space [0..1] — sized slightly WIDER than the backend's
  // visual water channel so no flora stands inside the rendered river strip.
  //   corridorHalfTop    : half-width at citadelAnchorY (narrow, far/horizon end).
  //   corridorHalfBottom : half-width at waterlineY      (wide,   near/viewer end).
  let   corridorHalfTop    = 0;
  let   corridorHalfBottom = 0;
  if (citadelLevel > 0) {
    const citRng      = new SeededRng(deriveChildSeed(input.seed, 'citadel-anchor'));
    // X: center-biased [0.38..0.62] — never clipped at canvas edges.
    citadelAnchorX    = 0.38 + citRng.next01() * 0.24;
    // Y: upper 18% of the visible land band — appears mid-ground, just past the horizon.
    citadelAnchorY    = horizonY + (floraBandY1 - horizonY) * 0.18;
    const clearR      = 0.07 + citadelLevel * 0.015;
    citadelClearR2    = clearR * clearR;
    if (riverMode) {
      corridorHalfTop    = 0.042 + citadelLevel * 0.008;  // narrow at citadel (far end)
      corridorHalfBottom = 0.072 + citadelLevel * 0.010;  // wide at waterline (near viewer)
    }
  }
  // River meander arm in MODEL space — mirrors the backend exactly so the flora
  // corridor tracks the rendered channel at every Y rather than padding an envelope.
  //
  // Both this pipeline code and backend.ts drawRiverCorridor use the SplitMix32
  // algorithm (SeededRng = splitmix32 — identical byte output for the same seed).
  // The same deriveChildSeed key ('river-meander') and the same coefficient (1.6)
  // mean riverRandV and mrng() in the backend produce the IDENTICAL float, so
  // mAmpFrac (model space) === mAmp / minDim (backend pixel space) exactly.
  //
  // Curve: sin(2π·tf) completes one full oscillation between citadel (tf=0) and
  // waterline (tf=1) — curves right then left (or vice versa), returning to the
  // citadel X at both ends.  This is the same formula used in backend.ts.
  //
  // NO-OP (mAmpFrac=0) when riverMode is false.
  let mAmpFrac = 0;
  if (riverMode) {
    const riverRng   = new SeededRng(deriveChildSeed(input.seed, 'river-meander'));
    const riverRandV = riverRng.next01();                           // mirrors mrng() in backend
    const hwTopFrac  = 0.032 + citadelLevel * 0.006;               // matches backend hwTopPx / minDim
    mAmpFrac = (riverRandV - 0.5) * hwTopFrac * 1.6;              // max ±0.8 × hwTopFrac
  }

  // Flora + rock: Poisson-disk placement via features.ts helpers.
  // Flora density scales with both habitability and desirability (beauty budget)
  // so the lab's habitability slider alone produces visibly different scenes.
  // Pass waterlineY so the regular scatter is also bounded to the visible land band.
  const rawFloraScatters = placeFloraScatters(
    rng, profile.floraKinds, palette, horizonY, hab01, desirability, waterlineY,
    profile.floraKindWeights,
  );
  // Citadel clearing for primary flora: post-filter (no RNG draws at filter time).
  // Circular footprint + river corridor (riverMode) — flora absent from both zones.
  const floraScatters = citadelLevel > 0
    ? rawFloraScatters.map(g => ({
        ...g,
        instances: g.instances.filter(inst => {
          const [px, py] = inst.pos;
          // Circular clearing around the citadel base.
          const dx = px - citadelAnchorX;
          const dy = py - citadelAnchorY;
          if (dx * dx + dy * dy < citadelClearR2) return false;
          // River corridor: tapered channel from citadelAnchorY down to waterlineY.
          if (riverMode && py >= citadelAnchorY && py <= waterlineY!) {
            const tf = (py - citadelAnchorY) / Math.max(waterlineY! - citadelAnchorY, 1e-6);
            const hw          = corridorHalfTop + (corridorHalfBottom - corridorHalfTop) * tf;
            const centerOff   = mAmpFrac * Math.sin(2 * Math.PI * tf);
            if (Math.abs(px - citadelAnchorX - centerOff) < hw) return false;
          }
          return true;
        }),
      }))
    : rawFloraScatters;
  const rockScatters  = placeRockScatters(rng, profile.rockKinds, palette, horizonY);
  const scatters      = [...floraScatters, ...rockScatters];

  // Dense life scatter: nativeLife × hab01 drives count up to ~200 additional instances.
  // Both must be high ("lush") for full density — the multiplicative interaction keeps
  // barren worlds (nativeLife≈0 or hab≈0) near zero regardless of the other value.
  //
  // denseFloraFactor gates the maximum per planet type: 0.0 for engineered / airless /
  // gassy worlds (ARTIFICIAL, BARREN, GAS_GIANT, VOLCANIC) so nativeLife can never
  // grow a forest on a station hull or an airless rock.  Lush natural types stay at 1.0.
  //
  // Y-band: scatter is bounded to [horizonY .. floraBandY1] where floraBandY1 is
  // waterlineY when water is present — so every point in the budget lands in the
  // VISIBLE land band, not under water.  Dry worlds fall back to 90% of the ground
  // height (the previous behaviour).
  //
  // ISOLATION: drawn from an independent child seed ('dense-flora') so that changing
  // nativeLife never shifts the deposit/energyMarker rng positions downstream.
  //
  // KIND MIX: instances are distributed across floraKinds using per-type weights
  // (profile.floraKindWeights) rather than a single uniformly-picked kind for the
  // whole batch.  This corrects the TERRAN 8:1 grass:tree ratio and makes JUNGLE
  // canopy-tree dominant.  Each instance draws 3 floats: scale + tintMix + kind.
  //
  // DEPTH GRADIENT: scale rises with pos[1] — FAR instances (near horizonY) are
  // small (~0.03–0.07); NEAR instances (near floraBandY1) are larger (~0.12–0.16).
  const nativeLife     = clamp01(input.planet.nativeLife);
  const lifeHab        = clamp01(nativeLife * hab01);
  const rawDenseCount  = Math.round(lerp(0, 200, lifeHab * lifeHab));
  // floraBandY1 is computed above (early, before primary scatter) — do not recompute.
  // Shore-height multiplier: scale dense-flora count proportionally to the
  // available land band so thin-shore worlds (OCEANIC, shoreH≈0.11) get fewer
  // plants and avoid the over-packed spindly-band look.
  // referenceShoreH=0.20 is chosen so TERRAN (shoreH≈0.26–0.32) and JUNGLE
  // (shoreH≈0.20–0.26, jitter ±0.03) both clamp to 1.0 — byte-identical output.
  // OCEANIC (shoreH≈0.08–0.14) lands at ~0.40–0.70 × count, a meaningful drop.
  // minFrac=0.25 ensures even the thinnest possible shore keeps some native life.
  // DETERMINISM: poissonDiskScatter draws the same RNG sequence from the front,
  // so wide shores where shoreFrac=1.0 produce byte-identical output to before.
  const shoreH          = floraBandY1 - horizonY;
  const referenceShoreH = 0.20;
  const shoreFrac       = clamp(shoreH / referenceShoreH, 0.25, 1.0);
  // Canopy ease (WO-VISTA-CANOPY-POLISH): on the river-primary layout the full-density
  // dense-flora band read as wall-to-wall canopy off to the flanks of the corridor,
  // crowding the breathing room the citadel + river are meant to open up.  Ease the
  // count and open the spacing a touch — gated strictly on riverMode (forest world +
  // citadel + water) so citadelLevel=0 and non-river worlds stay byte-identical.
  const canopyEaseCount  = riverMode ? 0.80 : 1.0;
  const canopyEaseSpread = riverMode ? 1.15 : 1.0;
  const lifeDenseCount   = Math.round(rawDenseCount * profile.denseFloraFactor * shoreFrac * canopyEaseCount);
  if (lifeDenseCount > 0 && profile.floraKinds.length > 0) {
    const denseRng  = new SeededRng(deriveChildSeed(input.seed, 'dense-flora'));
    // Tighter minimum spacing at high density so instances pack without z-fighting.
    const minDist   = lerp(0.035, 0.008, lifeHab) * canopyEaseSpread;
    const positions = poissonDiskScatter(denseRng, lifeDenseCount, 0.01, horizonY, 0.99, floraBandY1, minDist);
    if (positions.length > 0) {
      const white: RGB = [255, 255, 255];
      const floraKindArr = profile.floraKinds as readonly string[];
      // Kind weights: per-type bias (e.g. tree-heavy for forest archetypes) or
      // equal-weight fallback when floraKindWeights is absent / length-mismatched.
      const kindWeights = (
        profile.floraKindWeights &&
        profile.floraKindWeights.length === floraKindArr.length
      ) ? [...profile.floraKindWeights]
        : floraKindArr.map(() => 1);

      // Accumulate instances per kind → separate scatter groups so the renderer
      // can treat each kind independently (LOD, sprite set, etc.).
      const kindMap = new Map<string, VistaModel['layers']['features']['scatters'][number]['instances']>();
      const bandHeight = Math.max(floraBandY1 - horizonY, 1e-6);
      for (const pos of positions) {
        // Always consume the 3 per-instance draws (scale, tintMix, kind) before the
        // citadel check so that clearing does not shift the denseRng sequence for
        // subsequent un-cleared instances — same principle as poissonDiskScatter's
        // fixed-draw-count contract.
        const depthFrac = clamp01((pos[1] - horizonY) / bandHeight);
        const baseScale = lerp(0.030, 0.120, depthFrac);
        const scale     = baseScale + denseRng.next01() * 0.040;  // draw 1
        const tintMix   = denseRng.next01() * 0.25;                // draw 2
        const kind      = denseRng.pickWeighted(floraKindArr, kindWeights);  // draw 3
        // Citadel clearing: circular footprint + river corridor (riverMode).
        if (citadelLevel > 0) {
          const [px, py] = pos;
          const dx = px - citadelAnchorX;
          const dy = py - citadelAnchorY;
          if (dx * dx + dy * dy < citadelClearR2) continue;
          if (riverMode && py >= citadelAnchorY && py <= waterlineY!) {
            const tf = (py - citadelAnchorY) / Math.max(waterlineY! - citadelAnchorY, 1e-6);
            const hw          = corridorHalfTop + (corridorHalfBottom - corridorHalfTop) * tf;
            const centerOff   = mAmpFrac * Math.sin(2 * Math.PI * tf);
            if (Math.abs(px - citadelAnchorX - centerOff) < hw) continue;
          }
        }
        if (!kindMap.has(kind)) kindMap.set(kind, []);
        kindMap.get(kind)!.push({ pos, scale, tint: lerpRgb(palette.flora, white, tintMix) });
      }
      for (const [kind, instances] of kindMap) {
        scatters.push({ kind, instances });
      }
    }
  }

  // Hero foreground trees: 2–5 extra-large instances anchored near the bottom of
  // the visible land band to give a towering-canopy ceiling for forested worlds.
  // Gated on profile.heroFloraKind (only TERRAN + JUNGLE) and lifeDenseCount > 0
  // (minimum lushness required for the forest to have a ceiling at all).
  // Uses its own isolated child seed ('hero-flora') — never shifts dense-flora
  // or features streams.
  if (profile.heroFloraKind && lifeDenseCount > 0) {
    const heroRng   = new SeededRng(deriveChildSeed(input.seed, 'hero-flora'));
    const heroCount = heroRng.int(2, 5);
    // Near band: bottom 20% of the visible ground — unambiguously foreground.
    const heroY0  = floraBandY1 - (floraBandY1 - horizonY) * 0.20;
    const heroPos = poissonDiskScatter(heroRng, heroCount, 0.05, heroY0, 0.95, floraBandY1, 0.12);
    if (heroPos.length > 0) {
      const white: RGB = [255, 255, 255];
      const heroInstances: VistaModel['layers']['features']['scatters'][number]['instances'] = [];
      for (const pos of heroPos) {
        // Always draw scale + tintMix before the citadel check so the heroRng
        // sequence stays fixed for any un-cleared trees downstream.
        const scale   = 0.160 + heroRng.next01() * 0.060;  // 0.16..0.22
        const tintMix = heroRng.next01() * 0.15;
        // Citadel clearing: circular footprint + river corridor (riverMode).
        if (citadelLevel > 0) {
          const [px, py] = pos;
          const dx = px - citadelAnchorX;
          const dy = py - citadelAnchorY;
          if (dx * dx + dy * dy < citadelClearR2) continue;
          if (riverMode && py >= citadelAnchorY && py <= waterlineY!) {
            const tf = (py - citadelAnchorY) / Math.max(waterlineY! - citadelAnchorY, 1e-6);
            const hw          = corridorHalfTop + (corridorHalfBottom - corridorHalfTop) * tf;
            const centerOff   = mAmpFrac * Math.sin(2 * Math.PI * tf);
            if (Math.abs(px - citadelAnchorX - centerOff) < hw) continue;
          }
        }
        heroInstances.push({ pos, scale, tint: lerpRgb(palette.flora, white, tintMix) });
      }
      scatters.push({ kind: profile.heroFloraKind, instances: heroInstances });
    }
  }

  // Deposit markers and energy marker: site-gated (BRIEF §2.2 degradation).
  const depositMarkers = input.site
    ? placeDepositMarkers(rng, input.site.deposits, profile.depositVisuals, horizonY)
    : [];

  const energyMarker = input.site
    ? placeEnergyMarker(rng, input.site.energy, horizonY)
    : undefined;

  // Citadel anchor scatter — site-gated; NO-OP when citadelLevel=0.
  //
  // The 'citadel' kind is a single-instance sentinel that carries the model-space
  // anchor position and level-derived scale.  It is NEVER drawn by drawScatterInstances;
  // instead the backend picks it up in step 5g (drawCitadelStructure) and draws it
  // AFTER flora so the structure always occludes the canopy at its footprint.
  //
  // Scale: (0.08 + level × 0.04) — at h=900 and scaleFactor=0.85:
  //   L1 → 0.12 × 900 × 0.85 = 92 px   L2 → 122 px   L3 → 153 px   L5 → 214 px
  // Tint: neutral structural gray-white ([210, 215, 220]) — contrasts with any
  //   organic flora tints; backend overrides body/shadow/glow from model.palette.
  if (citadelLevel > 0) {
    const citScale = 0.08 + citadelLevel * 0.04;
    const citTint: RGB = [210, 215, 220];
    scatters.push({
      kind: 'citadel',
      instances: [{ pos: [citadelAnchorX, citadelAnchorY], scale: citScale, tint: citTint }],
    });
  }

  return {
    scatters,
    depositMarkers,
    ...(energyMarker ? { energyMarker } : {}),
  };
}

// ---------------------------------------------------------------------------
// Stage 10 — buildHazards  (site-gated)
// ---------------------------------------------------------------------------

function buildHazards(
  input: VistaInput,
  profile: ReturnType<typeof getProfile>,
  horizonY: number,
  rng: SeededRng,
): VistaModel['layers']['hazards'] {
  // site absent → no hazard layer (BRIEF §2.2 degradation)
  if (!input.site) return { overlays: [] };

  // TRUTHFULNESS (BRIEF §2.5): placeHazardOverlays emits an overlay for every
  // hazard unconditionally — desirability never suppresses a hazard visual.
  const overlays = placeHazardOverlays(
    rng, input.site.hazards, profile.hazardVisuals, horizonY,
  );
  return { overlays };
}

// ---------------------------------------------------------------------------
// Stage 11 — grid  (no-op at P0; [P7])
// ---------------------------------------------------------------------------
// When input.grid is absent the layer is simply omitted from the output.
// When input.grid is present at P0, we emit a placeholder no-op grid overlay
// so the contract shape is satisfied; the real grid-on-terrain subsystem lands
// in [P7] (BRIEF §5.4).

function buildGrid(
  input: VistaInput,
  _rng: SeededRng,                // 'grid' stream reserved — never call rng in P0 grid
): VistaModel['layers']['grid'] {
  if (!input.grid) return undefined;

  // P0 no-op: emit a minimal identity grid so the contract field is present
  // but the renderer treats it as "not yet projected."
  const cols  = input.grid.cols;
  const rows  = input.grid.rows;
  const cells: VistaModel['layers']['grid'] extends undefined
    ? never
    : NonNullable<VistaModel['layers']['grid']>['cells'] = [];

  for (let y = 0; y < rows; y++) {
    for (let x = 0; x < cols; x++) {
      cells.push({
        index:        y * cols + x,
        inSilhouette: true,
        transform:    [x, y, 1, 1], // identity: no terrain projection yet
      });
    }
  }

  return {
    space:    'screen2d',
    origin:   [0, 0],
    uBasis:   [1, 0],
    vBasis:   [0, 1],
    cellSize: 40,
    cells,
  };
}

// ---------------------------------------------------------------------------
// Stage 12 — validate + assemble invariants
// ---------------------------------------------------------------------------

/**
 * Checks a named set of critical numeric model scalars for non-finite values
 * (NaN / Infinity / -Infinity).  Any offending field name is appended to `notes`
 * so that `assembleInvariants` will flip `ok` to false.
 *
 * Exported for direct unit-testing.  Callers pass a flat Record<name, value>
 * where the key is the dotted model path (e.g. 'lighting.bloom') so that the
 * note is readable without inspecting the pipeline internals.
 */
export function checkFiniteFields(
  notes: string[],
  fields: Record<string, number>,
): void {
  for (const [name, value] of Object.entries(fields)) {
    if (!Number.isFinite(value)) {
      notes.push(`non-finite field: ${name}`);
    }
  }
}

function assembleInvariants(notes: string[]): VistaModel['invariants'] {
  return { ok: notes.length === 0, notes };
}

// ---------------------------------------------------------------------------
// generateVista  — public export
// ---------------------------------------------------------------------------

/**
 * The §2.7 ordered pipeline.  Pure + deterministic.
 *
 * Critical purity contract:
 *   - All randomness from SeedBus(input.seed) named streams only.
 *   - No DOM access, no Math.random(), no Date.now(), no side effects.
 *   - input.view is NEVER read here (renderer-only; not part of determinism).
 */
export function generateVista(input: VistaInput): VistaModel {
  const notes: string[] = [];
  const bus = SeedBus(input.seed);

  // ── Stage 1: profile lookup ─────────────────────────────────────────────
  const profile = getProfile(input.planet.type);
  if (!isProfiledType(input.planet.type)) {
    notes.push(`planet.type "${input.planet.type}" uses generic fallback profile`);
  }

  // ── Stage 2: archetype pick ─────────────────────────────────────────────
  const archetypeWeights = profile.archetypes.map(a => a.weight);
  const archetype        = bus.archetype.pickWeighted(
    profile.archetypes as readonly ArchetypeEntry[],
    archetypeWeights,
  );

  // ── Stage 3: palette derivation ─────────────────────────────────────────
  const palette = derivePalette(profile, input, archetype, bus.palette);

  // ── Stage 4: desirability + lighting ────────────────────────────────────
  const desirability = scoreDesirability(input);

  // Sun position needed for both lighting and celestial; derive here from
  // celestial stream so it's consistent between the two stages.
  const sunAzimuth   = bus.celestial.int(30, 330);
  const sunElevation = bus.celestial.int(20, 70);

  const lighting = deriveLighting(input, palette, desirability, sunAzimuth, sunElevation);

  // ── Stage 5: sky + celestial ─────────────────────────────────────────────
  const sky       = buildSky(input, palette, bus.sky, desirability);
  // Celestial rng was already consumed for sun position above; the remaining
  // draws (secondary star, moons, siblings) continue from the same stream.
  const celestial = buildCelestial(input, bus.celestial);

  // ── Stage 6: atmosphere ──────────────────────────────────────────────────
  const atmosphere = buildAtmosphere(input, profile, palette, bus.atmo);

  // ── Stage 7: terrain ─────────────────────────────────────────────────────
  // Read terrainMode from the profile (PlanetProfile.terrainMode, contract.ts).
  const terrainMode: TerrainMode = profile.terrainMode ?? 'surface';

  // GAS_GIANT → cloud-deck: no strata, no landmarks, no rock ground plane.
  // All others → normal terrain path (ARTIFICIAL override applied below).
  const terrainBase: VistaModel['layers']['terrain'] =
    terrainMode === 'cloud-deck'
      ? buildCloudDeckTerrain(bus.terrain)
      : buildTerrain(archetype, palette, profile.coherence, bus.terrain);

  // ARTIFICIAL ('plating'): force flat plating material + zero slope;
  // keep whatever landmarks the archetype provides (spires on engineered terrain).
  const terrainLayer: VistaModel['layers']['terrain'] =
    terrainMode === 'plating'
      ? {
          ...terrainBase,
          groundPlane: {
            ...terrainBase.groundPlane,
            material:     'plating',
            slopeProfile: new Array(10).fill(0) as number[],
          },
        }
      : terrainBase;

  // Attach terrain.mode for the renderer (VistaModel.layers.terrain.mode, contract.ts).
  // Also thread the profile's emissive params when present (currently: ARTIFICIAL only);
  // absent for all 11 natural types so their models are byte-identical to before.
  const terrain: VistaModel['layers']['terrain'] = {
    ...terrainLayer,
    mode: terrainMode,
    ...(profile.emissive ? { emissive: profile.emissive } : {}),
  };

  // ── Stage 8: water / lava (aquatic profiles only) ───────────────────────
  const water = buildWater(profile, palette, terrain.horizonY, input.planet.waterCoverage, bus.water);

  // ── Stage 9: features ────────────────────────────────────────────────────
  // Pass waterlineY so dense-flora scatter fills the visible land band only.
  const features = buildFeatures(
    input, profile, palette, terrain.horizonY, desirability, bus.features, water?.waterlineY,
  );

  // ── Stage 10: hazards (site-gated) ──────────────────────────────────────
  const hazards = buildHazards(input, profile, terrain.horizonY, bus.hazard);

  // ── Stage 11: grid overlay (no-op at P0; [P7]) ──────────────────────────
  // grid stream is reserved; pass rng but buildGrid does NOT draw from it at P0.
  const grid = buildGrid(input, bus.grid);

  // ── Stage 12: assemble + validate ───────────────────────────────────────
  // Guard: any non-finite value in a critical numeric model field silently
  // corrupts the renderer.  Check the known scalar outputs here where all
  // pipeline stages have run; a single bad value flips ok=false.
  checkFiniteFields(notes, {
    desirability,
    'lighting.keyIntensity':     lighting.keyIntensity,
    'lighting.bloom':            lighting.bloom,
    'lighting.colorGradeWarmth': lighting.colorGradeWarmth,
    'layers.sky.starCount':      sky.starCount,
    'layers.sky.haze.density':   sky.haze.density,
  });
  const invariants = assembleInvariants(notes);

  // Animation: dayCycleSeconds fixed at 360s for P0 (P1 wires rotationPeriodHours)
  const rotationPeriodHours = input.celestial.rotationPeriodHours ?? 24;
  const dayCycleSeconds = rotationPeriodHours >= 180
    ? 0      // tidally locked → frozen (0 = no cycle; renderer treats as FROZEN_DAY_PHASE)
    : Math.max(60, Math.round(360 * (rotationPeriodHours / 24)));

  return {
    contractVersion: 1,
    seed:            input.seed,
    planetType:      input.planet.type,
    archetype:       archetype.id,
    desirability,
    palette,
    lighting,
    layers: {
      sky,
      celestial,
      atmosphere,
      terrain,
      ...(water    ? { water }    : {}),
      features,
      hazards,
      ...(grid     ? { grid }     : {}),
    },
    animation: {
      dayCycleSeconds,
      rotationPeriodHours,
    },
    invariants,
  };
}

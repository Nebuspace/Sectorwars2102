/**
 * Vista Engine — canvas2d renderer backend
 *
 * Port of the landed-scene draw functions from SolarSystemViewscreen.tsx,
 * adapted to consume a VistaModel (pre-computed layer stack) instead of
 * live game state (LandedPalette / LandedCtx).
 *
 * Architecture:
 *   buildVistaCache()  → precomputes star layouts, moon arc params, wave
 *                        geometry, and gradient objects from the VistaModel.
 *   drawScene()        → the per-frame compositor; reads the cache + live t.
 *   mount()            → public entry point; returns a VistaHandle.
 *
 * Cache-key strategy:
 *   seed | atmoKind | habBucket | dayBucket | w | h
 *   Rebuilds on canvas resize, new model, or UTC-day rollover (sea state).
 *
 * Vacuum path (model.layers.atmosphere.present === false):
 *   • Sky gradient clamped to near-black regardless of day cycle.
 *   • Stars always at full visibility (not damped by sun altitude).
 *   • No haze, no clouds, no precipitation.
 *   • Hard horizon line.
 */

import { VistaModel, VistaTarget, VistaHandle, VistaInput, RGB } from '../../contract';
import { SeededRng, deriveChildSeed } from '../../core/rng';
import { generateVista } from '../../core/pipeline';
import { shadeFlank, rimLight, aoPool } from './lighting';
import { postProcess, buildGrainPattern } from './post';
import { getProfile } from '../../core/profiles';

// ---------------------------------------------------------------------------
// Day-cycle constants — verbatim from SolarSystemViewscreen.tsx L1952–1954
// ---------------------------------------------------------------------------

/** Nominal day-cycle duration in seconds. */
export const DAY_CYCLE_SECONDS = 360;
/** Frozen reduced-motion phase: a pleasant high-morning sun, calm + stable. */
const FROZEN_DAY_PHASE = 0.40;

// ---------------------------------------------------------------------------
// SKY_Y_SCALE — single-sourced constant shared by skyProjection AND the sun-Y
// formula. All sky bodies (sun, moons, siblings) arc through the same dome.
// Verbatim from SolarSystemViewscreen.tsx L1998.
// ---------------------------------------------------------------------------
export const SKY_Y_SCALE = 0.78;

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

type DayCycle = {
  dayPhase: number;   // 0..1: 0=midnight, 0.25=sunrise, 0.5=noon, 0.75=sunset
  sunAlt: number;     // -1 (deep below) … +1 (zenith)
  sunUp: boolean;
  bright: number;     // 0 (night) … 1 (full day)
  warm: number;       // 0..1 extra warm bias near sunrise/sunset
  skyDim: number;     // 0 (noon) … 1 (midnight)
  bodyBright: number; // moon/planet prominence: 1 at night → faint by day
};

type StarSeed = {
  x: number; y: number;
  size: number;
  twPhase: number;
  twSpeed: number;
  baseAlpha: number;
};

type MoonParam = {
  r: number;
  arcRate: number;
  arcOffset: number;
  arcDir: number;
  illum: number;    // pre-computed lit fraction from model (used as base)
  tint: string;     // 'r, g, b' CSS string
  mareTint: string;
};

type SkyPlanetParam = {
  r: number;
  arcRate: number;
  arcOffset: number;
  arcDir: number;
  hue: number;
  sat: number;
  baseColor: string;
  bandColor: string;
  rimColor: string;
  rings: boolean;
  alpha: number;
};

type WaveLine = {
  yFrac: number;
  amp: number;
  wavelength: number;
  speed: number;
  phase: number;
  alpha: number;
  lineW: number;
  dir: number;
  swellRate: number;
  swellPhase: number;
  crossAmp: number;
  crossWavelength: number;
  fine: boolean;
  chopAmp: number;
  chopWavelength: number;
  tilt: number;
};

type CloudParam = {
  x: number;
  speed: number;
  w: number;
  hFrac: number;
  yFrac: number;
  alpha: number;
  // WO-V2-CLOUDS-RAYS: kind-distinct, multi-layer parallax rendering
  kind: 'cumulus' | 'cirrus' | 'ash' | 'overcast';
  layer: 0 | 1 | 2;       // parallax depth: 0=far/small/slow, 1=mid, 2=near/large/fast
  lobeCount: number;       // cumulus only: number of billowy lobes; 0 for other kinds
  lobeOffsets: number[];   // cumulus only: seeded x-offset per lobe (0..1 of cloud width)
};

type ParticleSeed = {
  x: number;
  y: number;
  size: number;
  phase: number;
  speed: number;
  drift: number;
  warm: number;
};

/**
 * Pre-baked screen geometry for a single terrain landmark.
 * Computed once in buildVistaCache, drawn every frame by drawLandmarks().
 * All positions are absolute pixel coordinates derived from model pos (0..1)
 * and canvas dimensions at cache-build time.
 */
type LandmarkGeom = {
  kind: string;        // 'cone' | 'caldera' | 'mesa' | 'spire' | 'crater' | 'arch' | 'canyon' | 'glacier'
  cx: number;          // centre x on screen
  baseY: number;       // y of the terrain ground line (landmarks rise upward from here)
  height: number;      // total landmark height in px
  width: number;       // base half-width in px
  fillColor: string;   // main silhouette fill ('rgba(…)' CSS string)
  accentColor: string; // apex / rim accent color — used for volcanic glow + glacier sheen
  useAccent: boolean;  // whether to draw the accent pass
};

type VistaCache = {
  key: string;
  ctx: CanvasRenderingContext2D;
  w: number;
  h: number;
  horizonY: number;

  model: VistaModel;
  hasAtmosphere: boolean;

  // Day-cycle phase offset derived from seed (so different worlds start at
  // different points in their day)
  dayPhaseOffset: number;

  // Star color (primary sun) as { r, g, b }
  sc: { r: number; g: number; b: number };

  // Sun arc params
  sunR: number;
  coronaR: number;
  sunAzDir: number;
  coreWhite: number;
  hasCompanion: boolean;
  c2: { r: number; g: number; b: number };
  c2side: number;
  c2r: number;

  // Star field
  stars: StarSeed[];

  // Moons
  moons: MoonParam[];

  // Distant sky planets
  skyPlanets: SkyPlanetParam[];

  // Terrain ridge data from model strata — V2-DEPTH: real polyline + aerial-tint fields
  ridgePts: {
    pts: number[];            // 48-pt micro-roughness noise tile (seeded per stratum)
    poly: [number, number][]; // real strata polyline from model (17 pts, evenly-spaced X)
    period: number;
    speed: number;
    microAmp: number;         // bilateral micro-jitter amplitude in normalized Y (far≈0.004, near≈0.022)
    color: string;            // opaque base fill CSS (retained for logging; tint applied at draw)
    fillRGB: RGB;             // raw fill RGB tuple for per-frame aerial-perspective blend
    depthFrac: number;        // 0=far, 1=near
  }[];

  // Water
  hasWater: boolean;
  waterTopY: number;        // pixel Y of the waterline (waterlineY*h); h when no water
  waves: WaveLine[];
  waterBand: CanvasGradient | null;
  foamMul: number;
  reflTint: string;
  waterType: string;        // 'ocean' | 'coastal' | 'tidal-flat' | 'frozen' | 'lava' | ''
  waterColor: string;       // 'r, g, b' CSS channel string from model.layers.water.color
  foamColor: string;        // 'r, g, b' CSS channel string from model.layers.water.foam

  // Atmosphere
  hazeColor: string;
  hazeStrength: number;
  skyDarken: number;

  // Clouds
  clouds: CloudParam[];
  cloudTint: string;
  cloudKind: string;   // model.layers.atmosphere.clouds.kind

  // WO-V2-CLOUDS-RAYS: pre-baked night-sky + god-ray seeds
  godRaySeeds: { angle: number; spread: number; lenFrac: number; alphaMul: number }[];
  shootingStarSeeds: { x0: number; y0: number; x1: number; y1: number; phase: number; speed: number }[];
  galacticBand: { angle: number; width: number; cx: number; cy: number } | null;

  // Particles
  particles: ParticleSeed[];
  particleKind: string;

  // Terrain landmarks — pre-baked geometry from model.layers.terrain.landmarks
  landmarks: LandmarkGeom[];

  // Cached gradient objects (bound to this ctx; rebuilt on remount)
  glowGrad: CanvasGradient;

  // Terrain mode derived from the model (Lane 1 contract field; optional).
  // 'surface' | 'cloud-deck' | 'plating' — defaults to 'surface' when absent
  // so the normal P0 path is never disturbed.
  terrainMode: string;

  // Cloud-deck bands — GAS_GIANT special case (empty for all other modes)
  cloudBands: { yFrac: number; thickFrac: number; rgb: RGB; speed: number; alpha: number }[];

  // Plating grid cell size in pixels — ARTIFICIAL special case
  platingPx: number;

  // Deposit markers — pre-baked screen coords + visual descriptor
  depositScreens: { sx: number; sy: number; visual: string; intensity: number }[];

  // Energy source marker — null when the model has none
  energyScreen: { sx: number; sy: number; source: string; intensity: number } | null;

  // Hazard overlay screen polygons — model 0..1 region mapped to pixel coords
  hazardScreens: { visual: string; severity: number; pts: [number, number][] }[];

  // Feature scatters — flora/rock/glitter instances with baked screen coords
  scatterScreens: {
    kind: string;
    instances: { sx: number; sy: number; sizePx: number; tint: RGB; glow: number }[];
  }[];
};

// ---------------------------------------------------------------------------
// Pure-math helpers — verbatim from SolarSystemViewscreen.tsx
// ---------------------------------------------------------------------------

/** Deterministic PRNG — SplitMix32. Verbatim from SolarSystemViewscreen.tsx L268. */
function splitmix32(seed: number): () => number {
  let s = seed >>> 0;
  return () => {
    s = (s + 0x9e3779b9) >>> 0;
    let t = s ^ (s >>> 16);
    t = Math.imul(t, 0x21f0aaad);
    t = t ^ (t >>> 15);
    t = Math.imul(t, 0x735a2d97);
    return ((t ^ (t >>> 15)) >>> 0) / 4294967296;
  };
}

/** Parse a '#rrggbb' hex string. Fallback → warm white. */
function hexToRgb(c: string | undefined): { r: number; g: number; b: number } {
  const m = /^#?([0-9a-f]{6})$/i.exec(c || '');
  if (!m) return { r: 255, g: 240, b: 220 };
  const n = parseInt(m[1], 16);
  return { r: (n >> 16) & 255, g: (n >> 8) & 255, b: n & 255 };
}

/** Format an RGB tuple (from VistaModel) as a CSS 'r, g, b' channel string. */
function rgb(c: RGB): string {
  return `${c[0]}, ${c[1]}, ${c[2]}`;
}

/** CSS string for an RGB tuple with alpha. */
function rgba(c: RGB, a: number): string {
  return `rgba(${c[0]}, ${c[1]}, ${c[2]}, ${a.toFixed(3)})`;
}

// ---------------------------------------------------------------------------
// dayCycleAt — verbatim from SolarSystemViewscreen.tsx L1973
// Resolve the live day-cycle factors at time t (seconds).
// Reduced-motion (t=0) freezes at FROZEN_DAY_PHASE.
// ---------------------------------------------------------------------------
function dayCycleAt(t: number, phaseOffset: number): DayCycle {
  const dayPhase = t === 0
    ? FROZEN_DAY_PHASE
    : (((t / DAY_CYCLE_SECONDS) + phaseOffset) % 1 + 1) % 1;
  const sunAngle = (dayPhase - 0.25) * Math.PI * 2;
  const sunAlt = Math.sin(sunAngle);
  const sunUp = sunAlt > 0.02;
  const bright = Math.max(0.06, Math.min(1, 0.5 + sunAlt * 1.4));
  const warm = sunUp ? Math.max(0, 1 - Math.abs(sunAlt) * 3.2) : 0;
  const skyDim = Math.max(0, Math.min(0.82, 0.5 - sunAlt * 0.95));
  const bodyBright = Math.max(0.18, Math.min(1, 0.55 - sunAlt * 0.85));
  return { dayPhase, sunAlt, sunUp, bright, warm, skyDim, bodyBright };
}

// ---------------------------------------------------------------------------
// skyProjection — verbatim from SolarSystemViewscreen.tsx L2008
// Parametric arc for a celestial body across the sky.
// ---------------------------------------------------------------------------
function skyProjection(
  t: number, rate: number, phaseOffset: number, azDir: number,
  w: number, horizonY: number
): { x: number; y: number; alt: number; up: boolean; fade: number; azFrac: number } {
  const phase = t === 0
    ? phaseOffset
    : ((((t / (DAY_CYCLE_SECONDS * rate)) + phaseOffset) % 1) + 1) % 1;
  const ang = phase * Math.PI * 2;
  const alt = Math.sin(ang);
  const az = phase;
  const xu = azDir > 0 ? az : 1 - az;
  const x = w * (0.06 + xu * 0.88);
  // Shared SKY_Y_SCALE — sun, moons, and siblings all arc through the same dome
  const y = horizonY - Math.max(0, alt) * horizonY * SKY_Y_SCALE;
  const up = alt > 0.0;
  const fade = Math.max(0, Math.min(1, alt * 3.0));
  return { x, y, alt, up, fade, azFrac: xu };
}

// ---------------------------------------------------------------------------
// skyDir — verbatim from SolarSystemViewscreen.tsx L2032
// Unit sky-direction vector for terminator lighting.
// ---------------------------------------------------------------------------
function skyDir(alt: number, azFrac: number): { x: number; y: number; z: number } {
  const altAng = alt * (Math.PI / 2);
  const azAng = azFrac * Math.PI;
  const ca = Math.cos(altAng);
  return { x: ca * Math.cos(azAng), y: ca * Math.sin(azAng), z: Math.sin(altAng) };
}

// ---------------------------------------------------------------------------
// Cache builder
// ---------------------------------------------------------------------------

/** Sentinel: one cached renderer per active mount (module-level singleton). */
let _cache: VistaCache | null = null;

function buildVistaCache(
  ctx: CanvasRenderingContext2D,
  model: VistaModel,
  w: number,
  h: number
): VistaCache {
  const horizonY = h * model.layers.terrain.horizonY;
  const hasAtmosphere = model.layers.atmosphere.present;
  const habN = model.desirability;         // 0..1, drives star density + alpha
  const dayBucket = Math.floor(Date.now() / 86400000);

  // ---- PRNG streams seeded from the model seed (consistent sub-streams) ----
  const baseSeed = deriveChildSeed(model.seed, 'renderer');
  const sfSeed   = deriveChildSeed(model.layers.celestial.starfieldSeedKey, 'stars');
  const moonSeed = deriveChildSeed(model.seed, 'moons');
  const skyPlSeed = deriveChildSeed(model.seed, 'skyplanets');
  const waveSeed  = deriveChildSeed(model.seed, 'waves');
  const cloudSeed = deriveChildSeed(model.seed, 'clouds');
  const partSeed  = deriveChildSeed(model.seed, 'particles');
  const sunSeed   = deriveChildSeed(model.seed, 'sun');

  const rngBase = splitmix32(baseSeed);
  const rngSf   = splitmix32(sfSeed);
  const rngMoon = splitmix32(moonSeed);
  const rngSkPl = splitmix32(skyPlSeed);
  const rngWave = splitmix32(waveSeed);
  const rngCloud = splitmix32(cloudSeed);
  const rngPart  = splitmix32(partSeed);
  const rngSun   = splitmix32(sunSeed);

  // Day-phase offset so each world starts at a different time of day
  const dayPhaseOffset = rngBase();

  // ---- Primary star color ----
  const primarySun = model.layers.celestial.suns[0];
  const sc = primarySun
    ? { r: primarySun.color[0], g: primarySun.color[1], b: primarySun.color[2] }
    : hexToRgb('#fff4d0');

  // ---- Sun arc params ----
  const prox = Math.max(0.05, 1 - Math.min(1, ((model.animation.dayCycleSeconds / DAY_CYCLE_SECONDS) - 0.5)));
  const sunR = primarySun
    ? Math.max(6, Math.min(Math.min(w, h) * 0.13, primarySun.radiusPx * (w / 1440)))
    : Math.max(6, Math.min(w, h) * 0.05);
  const coronaR = Math.min(Math.hypot(w, h) * 0.55, sunR * (5 + prox * 4));
  const coreWhite = Math.round(160 + prox * 95);
  const sunAzDir = rngSun() > 0.5 ? 1 : -1;

  // Secondary (companion) sun
  let hasCompanion = false;
  let c2 = { r: 0, g: 0, b: 0 };
  let c2side = 1;
  let c2r = 0;
  if (model.layers.celestial.suns.length > 1) {
    hasCompanion = true;
    const s2 = model.layers.celestial.suns[1];
    c2 = { r: s2.color[0], g: s2.color[1], b: s2.color[2] };
    c2side = rngSun() > 0.5 ? 1 : -1;
    c2r = sunR * 0.55;
  }

  // ---- Star field ----
  // Vacuum worlds get dense starfields even at noon (no atmosphere dims them).
  // Atmospheric worlds dim the starfield with daylight (standard).
  const starCount = model.layers.sky.starCount;
  const nightBoost = hasAtmosphere ? 1.0 : 1.5; // vacuum → more stars always visible
  const stars: StarSeed[] = [];
  for (let i = 0; i < starCount; i++) {
    const x = rngSf() * w;
    const y = rngSf() * (horizonY * 0.92);
    const size = 0.3 + rngSf() * 0.9;
    const twSpeed = 0.6 + rngSf() * 1.4;
    const baseAlpha = (0.12 + rngSf() * 0.35) * (0.4 + (1 - habN) * 0.6) * nightBoost;
    stars.push({ x, y, size, twPhase: i, twSpeed, baseAlpha });
  }

  // ---- Moon arc params (seeded; positions arc per frame via skyProjection) ----
  const moons: MoonParam[] = [];
  for (let i = 0; i < model.layers.celestial.moons.length; i++) {
    const m = model.layers.celestial.moons[i];
    const arcRate = 0.7 + rngMoon() * 0.5;
    const arcOffset = rngMoon();
    const arcDir = rngMoon() > 0.5 ? 1 : -1;
    const r = Math.max(7, m.radiusPx * (Math.min(w, h) / 900));
    // Moon tint: inherit the star's color cast (warm-star → amber moons)
    const warmth = rngMoon();
    const tintR = Math.min(240, Math.round(160 + sc.r * 0.30 + warmth * 22));
    const tintG = Math.min(238, Math.round(155 + sc.g * 0.22 + (1 - warmth) * 10));
    const tintB = Math.min(238, Math.round(170 + sc.b * 0.28 - warmth * 15));
    const tint = `${tintR}, ${tintG}, ${tintB}`;
    const mareTint = [tintR, tintG, tintB].map((v) => Math.max(0, v - 40)).join(', ');
    moons.push({ r, arcRate, arcOffset, arcDir, illum: m.litFraction, tint, mareTint });
  }

  // ---- Sky planet arc params (seeded; positions arc per frame) ----
  const skyPlanets: SkyPlanetParam[] = [];
  for (let i = 0; i < model.layers.celestial.distant.length; i++) {
    const d = model.layers.celestial.distant[i];
    const r = Math.max(5, d.radiusPx * (Math.min(w, h) / 900));
    const arcRate = 1.4 + rngSkPl() * 1.2;
    const arcOffset = (i + 0.3) / Math.max(1, model.layers.celestial.distant.length) + rngSkPl() * 0.15;
    const arcDir = rngSkPl() > 0.5 ? 1 : -1;
    const { hue, sat } = d;
    // Classify by hue/sat to pick a treatment for coloring bands
    const treatment = sat < 20 ? 'ICE' : hue < 40 || hue > 320 ? 'VOLCANIC' : hue < 80 ? 'DESERT' : hue < 160 ? 'TERRAN' : 'BARREN';
    let baseColor: string, bandColor: string, rimColor: string;
    if (treatment === 'VOLCANIC') {
      baseColor = `hsl(${hue}, ${sat}%, 30%)`;
      bandColor = `hsla(20, 90%, 55%, 0.5)`;
      rimColor = `hsla(30, 100%, 60%, 0.5)`;
    } else if (treatment === 'ICE') {
      baseColor = `hsl(${hue}, ${Math.max(8, sat - 20)}%, 78%)`;
      bandColor = `hsla(${hue}, ${sat}%, 88%, 0.6)`;
      rimColor = `hsla(${hue}, 20%, 95%, 0.5)`;
    } else if (treatment === 'DESERT') {
      baseColor = `hsl(${hue}, ${sat}%, 52%)`;
      bandColor = `hsla(${hue - 12}, ${sat}%, 44%, 0.6)`;
      rimColor = `hsla(${hue}, ${sat}%, 72%, 0.4)`;
    } else if (treatment === 'TERRAN') {
      baseColor = `hsl(${hue}, ${sat}%, 46%)`;
      bandColor = `hsla(${hue + 8}, ${sat}%, 38%, 0.55)`;
      rimColor = `hsla(${hue}, ${sat}%, 70%, 0.5)`;
    } else {
      baseColor = `hsl(${hue}, ${Math.max(6, sat - 24)}%, 42%)`;
      bandColor = `hsla(${hue}, ${sat}%, 32%, 0.5)`;
      rimColor = `hsla(${hue}, 10%, 70%, 0.4)`;
    }
    const alpha = 0.4 + Math.min(r, 30) / 30 * 0.3;
    skyPlanets.push({ r, arcRate, arcOffset, arcDir, hue, sat, baseColor, bandColor, rimColor, rings: !!d.radiusPx, alpha });
  }

  // ---- Terrain ridges from model strata (V2-DEPTH) ----
  // Consumes the REAL strata[].polyline for the macro ridge silhouette; the
  // 48-pt pts array is kept only for per-pixel micro-roughness jitter layered on
  // top.  depthFrac (0=far, 1=near) drives amplitude grading + aerial-tint blend.
  const ridgePts: VistaCache['ridgePts'] = model.layers.terrain.strata.map((s, i) => {
    const rng = splitmix32(deriveChildSeed(model.seed, `ridge${i}`));
    const pts: number[] = [];
    for (let p = 0; p < 48; p++) pts.push(rng());

    const n = model.layers.terrain.strata.length;
    const depthFrac = n > 1 ? i / (n - 1) : 0;  // 0=far, 1=near

    // Micro-jitter amplitude: tiny bilateral noise on top of the real polyline shape.
    // Far ridges are smoother (haze softens surface texture at distance); near rougher.
    const microAmp = 0.004 + depthFrac * 0.018;

    return {
      pts,
      poly: s.polyline,
      period: Math.max(w * 2, 1200),
      speed: s.parallax * 3.0,     // parallax → scroll speed; far=slow, near=fast
      microAmp,
      color: rgba(s.fill, 1),
      fillRGB: s.fill,
      depthFrac,
    };
  });

  // ---- Water ----
  // waterTopY = model's waterlineY in pixels: the WATER SURFACE, not the horizon.
  // Terrain (ridges + land strip) renders above this; water band fills below it.
  const waterLayer = model.layers.water;
  const hasWater = !!waterLayer;
  const waterTopY = hasWater && waterLayer ? Math.round(waterLayer.waterlineY * h) : h;
  const waterType  = waterLayer ? waterLayer.type : '';
  // Water and foam color strings from model palette (palette.water / palette.foam).
  const wc = waterLayer
    ? { r: waterLayer.color[0], g: waterLayer.color[1], b: waterLayer.color[2] }
    : { r: 28, g: 88, b: 128 };
  const fc = waterLayer
    ? { r: waterLayer.foam[0], g: waterLayer.foam[1], b: waterLayer.foam[2] }
    : { r: 175, g: 218, b: 210 };
  const waterColor = `${wc.r}, ${wc.g}, ${wc.b}`;
  const foamColor  = `${fc.r}, ${fc.g}, ${fc.b}`;
  const waves: WaveLine[] = [];
  let waterBand: CanvasGradient | null = null;
  const foamMul = waterLayer ? Math.max(1, waterLayer.foamMul) : 1;
  let reflTint = `${sc.r}, ${sc.g}, ${sc.b}`;

  if (hasWater && waterLayer) {
    // Type-branched gradient — each water type uses palette.water/foam, NOT hardcoded blue.
    waterBand = ctx.createLinearGradient(0, waterTopY, 0, h);
    if (waterType === 'lava') {
      // Lava sea: fiery orange-red glow; no rolling-sea animation below.
      waterBand.addColorStop(0,    `rgba(${wc.r}, ${wc.g}, ${wc.b}, 0.92)`);
      waterBand.addColorStop(0.35, `rgba(${Math.round(wc.r * 0.72)}, ${Math.round(wc.g * 0.38)}, ${Math.round(Math.max(2, wc.b * 0.18))}, 0.96)`);
      waterBand.addColorStop(0.75, `rgba(${Math.round(wc.r * 0.42)}, ${Math.round(wc.g * 0.18)}, ${Math.round(Math.max(2, wc.b * 0.08))}, 0.98)`);
      waterBand.addColorStop(1,    `rgba(${Math.max(6, Math.round(wc.r * 0.18))}, 4, 2, 0.99)`);
    } else if (waterType === 'frozen') {
      // Frozen sea: pale ice sheet from palette.water; no rolling-sea animation.
      waterBand.addColorStop(0,    `rgba(${wc.r}, ${wc.g}, ${wc.b}, 0.84)`);
      waterBand.addColorStop(0.5,  `rgba(${Math.round(wc.r * 0.88)}, ${Math.round(wc.g * 0.90)}, ${Math.round(wc.b * 0.93)}, 0.92)`);
      waterBand.addColorStop(1,    `rgba(${Math.round(wc.r * 0.72)}, ${Math.round(wc.g * 0.76)}, ${Math.round(wc.b * 0.82)}, 0.96)`);
    } else {
      // ocean / coastal / tidal-flat: depth-graduated from palette.water surface to deep.
      waterBand.addColorStop(0,    `rgba(${wc.r}, ${wc.g}, ${wc.b}, 0.90)`);
      waterBand.addColorStop(0.45, `rgba(${Math.round(wc.r * 0.48)}, ${Math.round(wc.g * 0.52)}, ${Math.round(wc.b * 0.60)}, 0.96)`);
      waterBand.addColorStop(0.85, `rgba(${Math.round(wc.r * 0.22)}, ${Math.round(wc.g * 0.24)}, ${Math.round(wc.b * 0.30)}, 0.98)`);
      waterBand.addColorStop(1,    `rgba(${Math.round(wc.r * 0.10)}, ${Math.round(wc.g * 0.11)}, ${Math.round(wc.b * 0.14)}, 0.99)`);
    }

    // Wave generation: ocean / coastal / tidal-flat only.
    // frozen = static ice sheet; lava = static emissive surface — no rolling-sea animation.
    if (waterType !== 'frozen' && waterType !== 'lava') {
    const wh = h - waterTopY;
    const baseSwells = 11;
    const choppiness = Math.max(0, waterLayer.chop);
    const waveCountMul = 0.8 + choppiness * 0.6;
    const waveAmpMul = Math.max(0.5, waterLayer.waveAmp);
    const whitecapDensity = Math.min(1, 0.3 + choppiness * 0.7);
    const swellCount = Math.max(6, Math.round(baseSwells * waveCountMul));
    for (let i = 0; i < swellCount; i++) {
      const lin = i / (swellCount - 1);
      const f = lin * lin;
      const sizeJitter = 0.6 + rngWave() * 0.9;
      const swellTilt = f > 0.4
        ? (rngWave() - 0.5) * 0.10 * ((f - 0.4) / 0.6)
        : (rngWave() * 0.001);
      waves.push({
        yFrac: f,
        amp: (2 + f * 16) * sizeJitter * waveAmpMul,
        wavelength: (90 + f * 320) * (0.6 + rngWave() * 0.9),
        speed: (0.5 + f * 1.4) * (0.7 + rngWave() * 0.7),
        phase: rngWave() * Math.PI * 2,
        alpha: 0.5 + f * 0.4,
        lineW: 1 + f * 2.6,
        dir: rngWave() < 0.78 ? 1 : -1,
        swellRate: 0.25 + rngWave() * 0.5,
        swellPhase: rngWave() * Math.PI * 2,
        crossAmp: (2 + f * 7) * (0.5 + rngWave() * 0.9),
        crossWavelength: (160 + f * 360) * (0.7 + rngWave() * 0.7),
        fine: false,
        chopAmp: (0.6 + f * 1.2) * choppiness,
        chopWavelength: (10 + f * 24) * (0.7 + rngWave() * 0.6),
        tilt: swellTilt,
      });
    }
    // Fine ripple lines between swells
    const fineCount = Math.round(10 + 8 * waveAmpMul);
    for (let i = 0; i < fineCount; i++) {
      const f = rngWave();
      waves.push({
        yFrac: 0.1 + f * 0.88,
        amp: (1 + f * 3) * (0.6 + waveAmpMul * 0.4),
        wavelength: (24 + f * 70) * (0.7 + rngWave() * 0.6),
        speed: (1.2 + f * 2.2) * (0.8 + rngWave() * 0.6),
        phase: rngWave() * Math.PI * 2,
        alpha: 0.10 + f * 0.16,
        lineW: 0.6 + f * 0.8,
        dir: rngWave() < 0.7 ? 1 : -1,
        swellRate: 0.4 + rngWave() * 0.9,
        swellPhase: rngWave() * Math.PI * 2,
        crossAmp: (1 + f * 2) * (0.5 + rngWave() * 0.6),
        crossWavelength: 80 + f * 180,
        fine: true,
        chopAmp: (0.4 + f * 0.8) * choppiness,
        chopWavelength: 8 + f * 16,
        tilt: 0,
      });
    }
    } // end if (waterType !== 'frozen' && waterType !== 'lava')

    // Reflection tint tracks the moon if present, else the sun
    if (moons.length > 0) {
      let bestIllum = moons[0].illum;
      let bestTint = moons[0].tint;
      for (const m of moons) {
        if (m.illum > bestIllum) { bestIllum = m.illum; bestTint = m.tint; }
      }
      reflTint = bestTint;
    }
  }

  // ---- Atmosphere haze ----
  const haze = model.layers.sky.haze;
  const hazeRgb = haze.color;
  const hazeColor = `${hazeRgb[0]}, ${hazeRgb[1]}, ${hazeRgb[2]}`;
  const hazeStrength = hasAtmosphere ? haze.density : 0;

  // ---- Sky overcast / weather darkening from atmosphere events ----
  const events = model.layers.atmosphere.events;
  let skyDarken = 0;
  for (const ev of events) {
    if (ev.kind === 'storm' || ev.kind === 'overcast' || ev.kind === 'ash-storm') {
      skyDarken = Math.max(skyDarken, ev.intensity * 0.6);
    } else if (ev.kind === 'rain' || ev.kind === 'snow') {
      skyDarken = Math.max(skyDarken, ev.intensity * 0.3);
    }
  }

  // ---- Cloud strips — multi-layer, kind-distinct (WO-V2-CLOUDS-RAYS) ----
  const clouds: CloudParam[] = [];
  const cloudLayer = model.layers.atmosphere.clouds;
  const cloudKind = cloudLayer.kind;
  const cloudTint = cloudLayer.color
    ? `${cloudLayer.color[0]}, ${cloudLayer.color[1]}, ${cloudLayer.color[2]}`
    : '200, 210, 230';

  if (hasAtmosphere && cloudKind !== 'none' && cloudLayer.coverage > 0.05) {
    const coverage  = cloudLayer.coverage;
    const driftMul  = 0.5 + cloudLayer.drift * 0.5;

    if (cloudKind === 'cumulus') {
      // Three parallax layers — far (0), mid (1), near (2) — visually distinct by
      // size, speed, height, and opacity.  Far clouds are small/slow/high;
      // near clouds are large/fast/low.
      const layerCounts:  number[]            = [2, 3, 3];
      const yRanges:      [number, number][]  = [[0.06, 0.20], [0.12, 0.36], [0.20, 0.52]];
      const scales:       number[]            = [0.50, 0.78, 1.00];
      const speedBase:    number[]            = [0.30, 0.55, 0.80];
      const speedRange:   number[]            = [0.40, 0.65, 0.80];
      const alphaFactor:  number[]            = [0.52, 0.70, 0.88];

      for (let li = 0; li < 3; li++) {
        for (let i = 0; i < layerCounts[li]; i++) {
          const lobeCount = 3 + Math.floor(rngCloud() * 3);   // 3–5 lobes
          const lobeOffsets: number[] = [];
          for (let lb = 0; lb < lobeCount; lb++) lobeOffsets.push(rngCloud());
          const [yMin, yMax] = yRanges[li];
          clouds.push({
            x:     rngCloud() * w * 1.6,
            speed: (speedBase[li] + rngCloud() * speedRange[li]) * driftMul,
            w:     w * (0.12 + rngCloud() * 0.20) * scales[li] * (0.55 + coverage * 0.45),
            hFrac: (0.07 + rngCloud() * 0.07) * scales[li],
            yFrac: yMin + rngCloud() * (yMax - yMin),
            alpha: alphaFactor[li] * coverage * (0.50 + rngCloud() * 0.50),
            kind:  'cumulus',
            layer: li as 0 | 1 | 2,
            lobeCount,
            lobeOffsets,
          });
        }
      }
      // Overcast deck — wide low-alpha band added when coverage is high
      if (coverage >= 0.75) {
        clouds.push({
          x:     0,
          speed: 0.12 * driftMul,
          w:     w * 1.1,
          hFrac: 0.14,
          yFrac: 0.06,
          alpha: (coverage - 0.60) * 0.65,
          kind: 'overcast', layer: 0, lobeCount: 0, lobeOffsets: [],
        });
      }
    } else if (cloudKind === 'cirrus') {
      // Single high-altitude layer — thin horizontal feathered streaks.
      const count = 5 + Math.round(rngCloud() * 4);
      for (let i = 0; i < count; i++) {
        clouds.push({
          x:     rngCloud() * w * 1.6,
          speed: (0.35 + rngCloud() * 0.45) * driftMul,
          w:     w * (0.22 + rngCloud() * 0.32),
          hFrac: 0.015 + rngCloud() * 0.012,   // very thin
          yFrac: 0.04  + rngCloud() * 0.28,    // high in sky
          alpha: (0.22 + rngCloud() * 0.38) * coverage,
          kind: 'cirrus', layer: 0, lobeCount: 0, lobeOffsets: [],
        });
      }
    } else if (cloudKind === 'ash' || cloudKind === 'dust') {
      // Two layers of turbulent irregular masses.
      // Color tinting (grey-brown vs tan-dust) comes from cloudTint set by the pipeline.
      const layerCounts2 = [3, 2];
      for (let li = 0; li < 2; li++) {
        for (let i = 0; i < layerCounts2[li]; i++) {
          clouds.push({
            x:     rngCloud() * w * 1.6,
            speed: (0.40 + rngCloud() * 0.60) * driftMul,
            w:     w * (0.16 + rngCloud() * 0.26),
            hFrac: 0.08 + rngCloud() * 0.14,
            yFrac: (li === 0 ? 0.08 : 0.22) + rngCloud() * 0.22,
            alpha: (0.28 + rngCloud() * 0.44) * coverage,
            kind: 'ash', layer: li as 0 | 1, lobeCount: 0, lobeOffsets: [],
          });
        }
      }
    }
    // 'banded' → GAS_GIANT uses drawCloudDeck; no sky clouds needed here.
    // 'none'   → guarded above by cloudKind !== 'none'.
  }

  // ---- God-ray seeds — seeded wedge fan from the sun (WO-V2-CLOUDS-RAYS) ----
  const rngRay   = splitmix32(deriveChildSeed(model.seed, 'godrays'));
  const rngShoot = splitmix32(deriveChildSeed(model.seed, 'shootstars'));
  const rngGal   = splitmix32(deriveChildSeed(model.seed, 'galband'));

  const godRaySeeds: VistaCache['godRaySeeds'] = [];
  if (hasAtmosphere) {
    const rayCount = 6 + Math.floor(rngRay() * 5);   // 6–10 rays
    for (let i = 0; i < rayCount; i++) {
      // Fan from ~60° left of straight-down to ~60° right (centred on π/2 = straight down)
      godRaySeeds.push({
        angle:    Math.PI / 2 - Math.PI / 3 + rngRay() * (Math.PI * 2 / 3),
        spread:   0.022 + rngRay() * 0.038,    // angular half-width of each wedge (radians)
        lenFrac:  0.80  + rngRay() * 0.55,     // fraction of (horizonY − sunY) to extend
        alphaMul: 0.25  + rngRay() * 0.75,
      });
    }
  }

  // ---- Shooting star seeds — brief streaks for the night sky ----
  const shootingStarSeeds: VistaCache['shootingStarSeeds'] = [];
  if (hasAtmosphere) {
    const ssCount = 2 + Math.floor(rngShoot() * 2);
    for (let i = 0; i < ssCount; i++) {
      const x0    = rngShoot() * w;
      const y0    = rngShoot() * horizonY * 0.55;
      const angle = Math.PI * 0.25 + rngShoot() * Math.PI * 0.50;  // 45°–135° (downward)
      const len   = 55 + rngShoot() * 110;
      shootingStarSeeds.push({
        x0, y0,
        x1:    x0 + Math.cos(angle) * len,
        y1:    y0 + Math.sin(angle) * len,
        phase: rngShoot() * Math.PI * 2,
        speed: 0.35 + rngShoot() * 0.70,
      });
    }
  }

  // ---- Galactic / milky-way band seed — diagonal strip of faint star density ----
  const galacticBand: VistaCache['galacticBand'] = hasAtmosphere ? {
    angle: (0.22 + rngGal() * 0.28) * Math.PI,
    width: w * (0.11 + rngGal() * 0.10),
    cx:    w * (0.22 + rngGal() * 0.56),
    cy:    horizonY * (0.18 + rngGal() * 0.44),
  } : null;

  // ---- Particles ----
  const particles: ParticleSeed[] = [];
  const atmoParticles = model.layers.atmosphere.particles;
  let particleKind = 'FAINT';
  if (atmoParticles.length > 0) {
    const p0 = atmoParticles[0];
    particleKind = p0.kind.toUpperCase();
    const count = Math.round(20 + p0.rate * 40);
    for (let i = 0; i < count; i++) {
      particles.push({
        x: rngPart() * w,
        y: rngPart() * h,
        size: 0.6 + rngPart() * 1.4,
        phase: rngPart() * Math.PI * 2,
        speed: 0.4 + rngPart() * 1.2,
        drift: (rngPart() - 0.5) * 2,
        warm: rngPart(),
      });
    }
  } else if (hasAtmosphere) {
    // sparse ambient motes on atmospheric worlds
    particleKind = 'FAINT';
    for (let i = 0; i < 12; i++) {
      particles.push({
        x: rngPart() * w,
        y: rngPart() * h,
        size: 0.5 + rngPart() * 1.0,
        phase: rngPart() * Math.PI * 2,
        speed: 0.2 + rngPart() * 0.6,
        drift: (rngPart() - 0.5),
        warm: rngPart(),
      });
    }
  }

  // ---- Terrain landmarks — geometry baked from model.layers.terrain.landmarks ----
  // Positions (pos[0], pos[1]) are 0..1 normalized screen coords. We anchor all
  // landmarks to horizonY (the terrain ground line); they rise upward from there.
  // Colors: base = darkened geologyBands[0] (or surface); VOLCANIC cone/caldera get
  // an accent-glow pass from palette.accent; glacier uses a pale blue-white tint.
  const geoBase = model.palette.geologyBands.length > 0
    ? model.palette.geologyBands[0]
    : model.palette.surface;
  const accentRgb = model.palette.accent;

  const landmarks: LandmarkGeom[] = model.layers.terrain.landmarks.map((lm) => {
    const cx = lm.pos[0] * w;
    const baseY = horizonY;
    // height and width scale with `lm.scale` (dimensionless multiplier, ~0.2..2.0)
    // Clamp so landmarks never clip the top of the canvas or span the full width.
    const height = Math.max(24, Math.min(h * 0.48, lm.scale * h * 0.30));
    const width  = Math.max(18, Math.min(w * 0.38, lm.scale * w * 0.16));

    // Base silhouette: ~30% darker than the geology/surface palette so it reads as
    // a terrain feature against the ground without blending into it.
    const dr = Math.round(geoBase[0] * 0.55);
    const dg = Math.round(geoBase[1] * 0.55);
    const db = Math.round(geoBase[2] * 0.55);
    let fillColor = `rgba(${dr}, ${dg}, ${db}, 0.95)`;
    let accentColor = `rgba(${accentRgb[0]}, ${accentRgb[1]}, ${accentRgb[2]}, 0.7)`;
    let useAccent = false;

    if (lm.kind === 'glacier') {
      // Glacier: pale blue-white wedge — blend base toward ice-white
      fillColor = `rgba(${Math.min(255, Math.round(dr * 0.4 + 200))}, ${Math.min(255, Math.round(dg * 0.4 + 218))}, ${Math.min(255, Math.round(db * 0.4 + 235))}, 0.88)`;
      accentColor = 'rgba(230, 248, 255, 0.55)';
      useAccent = true;
    } else if (lm.kind === 'cone' || lm.kind === 'caldera') {
      // VOLCANIC cone/caldera: add an accent glow at the apex / rim.
      // The accent color is palette.accent (deposit/energy glow tint).
      useAccent = accentRgb[0] > 80 || accentRgb[2] < accentRgb[0]; // warm/hot tint check
    }

    return { kind: lm.kind, cx, baseY, height, width, fillColor, accentColor, useAccent };
  });

  // ---- Cached horizon glow gradient ----
  const gx = w * (0.25 + rngBase() * 0.5);
  const glowGrad = ctx.createLinearGradient(0, horizonY, 0, h);
  const glowRgb = model.palette.scatterBand;
  glowGrad.addColorStop(0, rgba(glowRgb, 0.18));
  glowGrad.addColorStop(1, rgba(glowRgb, 0));

  // ---- Terrain mode (contract.ts terrain.mode; absent → 'surface' so P0 path is unchanged) ----
  const terrainMode = model.layers.terrain.mode ?? 'surface';

  // ---- Cloud-deck bands — GAS_GIANT (mode === 'cloud-deck') ----
  const cloudBands: VistaCache['cloudBands'] = [];
  if (terrainMode === 'cloud-deck') {
    const rngBand = splitmix32(deriveChildSeed(model.seed, 'cloudbands'));
    const bandCount = 5 + Math.round(rngBand() * 2);  // 5–7 receding layers
    const ridgeArr = model.palette.ridge;
    for (let bi = 0; bi < bandCount; bi++) {
      const tFar = bi / Math.max(1, bandCount - 1);   // 0 = near horizon, 1 = foreground
      // Tint: far bands take skyHorizon; near bands blend toward the ridge palette
      const palA = model.palette.skyHorizon;
      const palB = ridgeArr[Math.min(bi, ridgeArr.length - 1)] ?? palA;
      const blend = tFar * 0.55;
      const bandRgb: RGB = [
        Math.round(palA[0] * (1 - blend) + palB[0] * blend),
        Math.round(palA[1] * (1 - blend) + palB[1] * blend),
        Math.round(palA[2] * (1 - blend) + palB[2] * blend),
      ];
      cloudBands.push({
        yFrac:     tFar,
        thickFrac: 0.040 + (1 - tFar) * 0.14 + rngBand() * 0.05,  // far=thin, near=thick
        rgb:       bandRgb,
        speed:     (0.5 + rngBand() * 0.8) * (0.3 + tFar * 0.7),  // far=slow, near=fast
        alpha:     0.52 + rngBand() * 0.32,
      });
    }
  }

  // ---- Plating grid cell size (ARTIFICIAL: mode === 'plating') ----
  const platingPx = Math.max(28, Math.round(w * 0.062));  // ~89px @1440

  // ---- Deposit markers — bake screen coords from model 0..1 positions ----
  // y anchors to the ground band (horizonY + a fraction of groundH so markers
  // sit on the terrain, not floating above it).
  const groundH = h - horizonY;
  const depositScreens: VistaCache['depositScreens'] =
    model.layers.features.depositMarkers.map((dm) => ({
      sx: dm.pos[0] * w,
      sy: horizonY + dm.pos[1] * groundH * 0.65,
      visual: dm.visual,
      intensity: dm.intensity,
    }));

  // ---- Energy marker — screen coords (null when absent) ----
  const emLayer = model.layers.features.energyMarker;
  const energyScreen = emLayer
    ? {
        sx: emLayer.pos[0] * w,
        sy: horizonY + emLayer.pos[1] * groundH * 0.50,
        source: emLayer.source,
        intensity: emLayer.intensity,
      }
    : null;

  // ---- Hazard overlay polygons — map 0..1 region to screen pixels ----
  // A missing/empty region defaults to full-ground coverage so every hazard
  // is guaranteed to render somewhere visible.
  const hazardScreens: VistaCache['hazardScreens'] = model.layers.hazards.overlays.map((hz) => {
    const pts: [number, number][] = hz.region.length >= 3
      ? hz.region.map(([rx, ry]) => [rx * w, horizonY + ry * groundH] as [number, number])
      : [[0, horizonY], [w, horizonY], [w, h], [0, h]];
    return { visual: hz.visual, severity: hz.severity, pts };
  });

  // ---- Feature scatters — bake screen coords for flora / rock / glitter instances ----
  // groundH is already computed above.  sizePx uses a small fraction of min(w,h) so
  // scattered vegetation stays proportional at any canvas size.
  // When water is present, cap scatter sy to the land strip (above waterlineY) so
  // flora/rocks don't appear in the water zone.
  const scatterMaxY = hasWater ? waterTopY - 4 : h;
  const scatterScreens: VistaCache['scatterScreens'] = model.layers.features.scatters.map((group) => ({
    kind: group.kind,
    instances: group.instances.map((inst) => ({
      sx:     inst.pos[0] * w,
      sy:     Math.min(horizonY + inst.pos[1] * groundH * 0.80, scatterMaxY),
      sizePx: Math.max(2, inst.scale * Math.min(w, h) * 0.012),
      tint:   inst.tint,
      glow:   inst.glow ?? 0,
    })),
  }));

  return {
    key: '', // filled by caller
    ctx,
    w, h,
    horizonY,
    model,
    hasAtmosphere,
    dayPhaseOffset,
    sc,
    sunR, coronaR, sunAzDir, coreWhite,
    hasCompanion, c2, c2side, c2r,
    stars,
    moons,
    skyPlanets,
    ridgePts,
    hasWater,
    waterTopY,
    waves,
    waterBand,
    foamMul,
    reflTint,
    waterType,
    waterColor,
    foamColor,
    hazeColor,
    hazeStrength,
    skyDarken,
    clouds,
    cloudTint,
    cloudKind,
    godRaySeeds,
    shootingStarSeeds,
    galacticBand,
    particles,
    particleKind,
    landmarks,
    glowGrad,
    terrainMode,
    cloudBands,
    platingPx,
    depositScreens,
    energyScreen,
    hazardScreens,
    scatterScreens,
  };
}

// ---------------------------------------------------------------------------
// drawWeatherSky — adapted from SolarSystemViewscreen.tsx L5358
// Darkens + biome-tints the sky by the storm/weather tier.
// ---------------------------------------------------------------------------
function drawWeatherSky(
  ctx: CanvasRenderingContext2D,
  w: number,
  horizonY: number,
  skyDarken: number,
  hazeColor: string
): void {
  const sd = skyDarken;
  const [r, g, b] = hazeColor.split(',').map((s) => parseInt(s.trim(), 10));
  ctx.save();
  const dark = ctx.createLinearGradient(0, 0, 0, horizonY * 1.1);
  dark.addColorStop(0, `rgba(18, 22, 30, ${(sd * 0.8).toFixed(3)})`);
  dark.addColorStop(1, `rgba(28, 34, 44, ${(sd * 0.4).toFixed(3)})`);
  ctx.fillStyle = dark;
  ctx.fillRect(0, 0, w, horizonY * 1.1);
  const tint = ctx.createLinearGradient(0, 0, 0, horizonY * 1.1);
  tint.addColorStop(0, `rgba(${r}, ${g}, ${b}, ${(sd * 0.22).toFixed(3)})`);
  tint.addColorStop(1, `rgba(${r}, ${g}, ${b}, ${(sd * 0.10).toFixed(3)})`);
  ctx.fillStyle = tint;
  ctx.fillRect(0, 0, w, horizonY * 1.1);
  ctx.restore();
}

// ---------------------------------------------------------------------------
// drawLandedSkyPlanets — adapted from SolarSystemViewscreen.tsx L5495
// Distant sibling bodies arcing across the sky.
// ---------------------------------------------------------------------------
function drawLandedSkyPlanets(
  ctx: CanvasRenderingContext2D,
  w: number,
  horizonY: number,
  t: number,
  cache: VistaCache,
  dc: DayCycle
): void {
  for (let i = 0; i < cache.skyPlanets.length; i++) {
    const p = cache.skyPlanets[i];
    const pos = skyProjection(t, p.arcRate, p.arcOffset, p.arcDir, w, horizonY);
    if (!pos.up) continue;
    const px = pos.x, py = pos.y;
    const a = p.alpha * pos.fade * (0.45 + dc.bodyBright * 0.85);
    if (a <= 0.01) continue;
    ctx.save();
    ctx.globalAlpha = a;
    ctx.save();
    ctx.beginPath();
    ctx.arc(px, py, p.r, 0, Math.PI * 2);
    ctx.clip();
    ctx.fillStyle = p.baseColor;
    ctx.fillRect(px - p.r, py - p.r, p.r * 2, p.r * 2);
    ctx.fillStyle = p.bandColor;
    ctx.fillRect(px - p.r, py - p.r * 0.1, p.r * 2, p.r * 0.5);
    const lg = ctx.createRadialGradient(px - p.r * 0.3, py - p.r * 0.3, p.r * 0.1, px, py, p.r * 1.2);
    lg.addColorStop(0, 'rgba(255,255,255,0.16)');
    lg.addColorStop(0.7, 'rgba(255,255,255,0)');
    lg.addColorStop(1, 'rgba(0,0,0,0.1)');
    ctx.fillStyle = lg;
    ctx.fillRect(px - p.r, py - p.r, p.r * 2, p.r * 2);
    ctx.restore();
    const shimmer = t === 0 ? 0.5 : 0.4 + 0.2 * Math.sin(t * 0.4 + i);
    ctx.strokeStyle = p.rimColor;
    ctx.globalAlpha = a * shimmer;
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.arc(px, py, p.r + 0.5, 0, Math.PI * 2);
    ctx.stroke();
    if (p.rings) {
      ctx.globalAlpha = a * 0.7;
      ctx.strokeStyle = p.rimColor;
      ctx.lineWidth = 1.2;
      ctx.beginPath();
      ctx.ellipse(px, py, p.r * 1.8, p.r * 0.5, -0.3, 0, Math.PI * 2);
      ctx.stroke();
    }
    ctx.restore();
  }
}

// ---------------------------------------------------------------------------
// drawLandedMoons — adapted from SolarSystemViewscreen.tsx L5569
// Phased discs arcing across the sky; terminator lit from sun direction.
// ---------------------------------------------------------------------------
function drawLandedMoons(
  ctx: CanvasRenderingContext2D,
  w: number,
  horizonY: number,
  t: number,
  cache: VistaCache,
  dc: DayCycle,
  sunWorldX: number,
  sunWorldY: number,
  sunAlt: number,
  sunAzFrac: number
): void {
  const prom = dc.bodyBright;
  const sunVec = skyDir(sunAlt, sunAzFrac);
  const lightDir = Math.atan2(sunWorldY - horizonY, sunWorldX - cache.w / 2);
  const lightCos = Math.cos(lightDir);
  const lightSin = Math.sin(lightDir);

  for (let i = 0; i < cache.moons.length; i++) {
    const m = cache.moons[i];
    const pos = skyProjection(t, m.arcRate, m.arcOffset, m.arcDir, w, horizonY);
    if (!pos.up) continue;
    const mx = pos.x, my = pos.y;
    const ext = pos.fade;
    const breathe = t === 0 ? 1 : 0.96 + 0.04 * Math.sin(t * 0.3 + i);

    // Physical phase from angular separation between moon and sun
    const moonVec = skyDir(pos.alt, pos.azFrac);
    const cosSep = Math.max(-1, Math.min(1,
      sunVec.x * moonVec.x + sunVec.y * moonVec.y + sunVec.z * moonVec.z));
    const illum = (1 - cosSep) / 2;
    const nearSun = Math.max(0, 1 - illum * 2.2);
    const sunWash = 1 - nearSun * 0.75;
    const dayMoon = dc.sunUp;

    // Smooth daytime fade — verbatim from SolarSystemViewscreen.tsx L5616
    const sunAltNorm = Math.max(0, Math.min(1, (dc.sunAlt + 0.12) / 0.42));
    const sunAltSmooth = sunAltNorm * sunAltNorm * (3 - 2 * sunAltNorm);
    const dayFaint = 0.15 + (1 - sunAltSmooth) * 0.85;

    const nightGlow = 1.0 + (1 - dc.bright) * 2.0;
    ctx.save();
    ctx.globalCompositeOperation = 'lighter';
    const innerHalo = ctx.createRadialGradient(mx, my, m.r * 0.2, mx, my, m.r * 2.0);
    innerHalo.addColorStop(0, `rgba(${m.tint}, ${(0.10 * prom * ext * sunWash * dayFaint * nightGlow).toFixed(3)})`);
    innerHalo.addColorStop(1, `rgba(${m.tint}, 0)`);
    ctx.fillStyle = innerHalo;
    ctx.fillRect(mx - m.r * 2.0, my - m.r * 2.0, m.r * 4.0, m.r * 4.0);
    const outerR = m.r * (2.6 + (1 - dc.bright) * 1.4);
    const outerHalo = ctx.createRadialGradient(mx, my, m.r * 0.6, mx, my, outerR);
    outerHalo.addColorStop(0, `rgba(${m.tint}, ${(0.12 * prom * ext * sunWash * dayFaint * nightGlow).toFixed(3)})`);
    outerHalo.addColorStop(1, `rgba(${m.tint}, 0)`);
    ctx.fillStyle = outerHalo;
    ctx.fillRect(mx - outerR, my - outerR, outerR * 2, outerR * 2);
    ctx.restore();

    // Lit disc
    ctx.save();
    ctx.globalAlpha = (0.5 + 0.5 * prom) * breathe * ext * sunWash * dayFaint;
    ctx.fillStyle = `rgb(${m.tint})`;
    ctx.beginPath();
    ctx.arc(mx, my, m.r, 0, Math.PI * 2);
    ctx.fill();
    ctx.globalAlpha *= 0.4;
    ctx.fillStyle = `rgba(${m.mareTint}, 1)`;
    ctx.beginPath();
    ctx.arc(mx - m.r * 0.3, my - m.r * 0.2, m.r * 0.28, 0, Math.PI * 2);
    ctx.arc(mx + m.r * 0.25, my + m.r * 0.3, m.r * 0.2, 0, Math.PI * 2);
    ctx.fill();
    ctx.restore();

    // Terminator (night/twilight only)
    if (!dayMoon && illum < 0.985) {
      ctx.save();
      ctx.globalAlpha = ext * sunWash;
      ctx.beginPath();
      ctx.arc(mx, my, m.r, 0, Math.PI * 2);
      ctx.clip();
      const k = (illum - 0.5) * 2;
      const dx = -lightCos * m.r * k;
      const dy = -lightSin * m.r * k;
      const sr = m.r * (1.0 + (1 - Math.abs(k)) * 0.04);
      ctx.globalCompositeOperation = 'source-over';
      ctx.fillStyle = 'rgba(8, 10, 18, 0.82)';
      ctx.beginPath();
      ctx.arc(mx + dx, my + dy, sr, 0, Math.PI * 2);
      ctx.fill();
      ctx.restore();
    }
  }
}

// ---------------------------------------------------------------------------
// drawLandedParticles — adapted from SolarSystemViewscreen.tsx L5789
// Atmospheric foreground particles driven by model.layers.atmosphere.particles.
// ---------------------------------------------------------------------------
function drawLandedParticles(
  ctx: CanvasRenderingContext2D,
  w: number,
  h: number,
  t: number,
  cache: VistaCache
): void {
  const kind = cache.particleKind;
  const horizonY = cache.horizonY;
  ctx.save();
  if (kind === 'EMBER') {
    ctx.globalCompositeOperation = 'lighter';
    const RISE = h * 0.22;
    for (let i = 0; i < cache.particles.length; i++) {
      const p = cache.particles[i];
      const x = p.x + Math.sin(t * 1.1 + p.phase) * 7 + p.drift * 3;
      const rise = ((p.y % RISE) + t * (10 + p.speed * 16)) % RISE;
      const y = (horizonY + RISE) - rise;
      const flick = 0.4 + 0.6 * Math.abs(Math.sin(t * 3 + p.phase));
      const a = Math.min(1, 0.6 * flick * (1 - rise / RISE));
      const r = (1.1 + p.size * 1.1) * (1 - rise / RISE * 0.4);
      ctx.globalAlpha = a * 0.5;
      const hg = ctx.createRadialGradient(x, y, 0, x, y, r * 2.4);
      hg.addColorStop(0, p.warm > 0.5 ? 'rgba(255, 170, 70, 1)' : 'rgba(255, 100, 35, 1)');
      hg.addColorStop(1, 'rgba(255, 60, 10, 0)');
      ctx.fillStyle = hg;
      ctx.fillRect(x - r * 2.4, y - r * 2.4, r * 4.8, r * 4.8);
      ctx.globalAlpha = a;
      ctx.fillStyle = p.warm > 0.5 ? 'rgba(255, 210, 140, 1)' : 'rgba(255, 130, 60, 1)';
      ctx.beginPath(); ctx.arc(x, y, Math.max(0.8, r * 0.5), 0, Math.PI * 2); ctx.fill();
    }
  } else if (kind === 'SNOW') {
    ctx.fillStyle = 'rgba(235, 246, 255, 1)';
    for (let i = 0; i < cache.particles.length; i++) {
      const p = cache.particles[i];
      const fall = (p.y + t * (12 + p.speed * 16)) % h;
      const wind = p.drift * t * 2;
      const x = (((p.x + Math.sin(t * 0.6 + p.phase) * 12 + wind) % w) + w) % w;
      ctx.globalAlpha = Math.min(1, 0.45 + p.warm * 0.4);
      ctx.beginPath();
      ctx.arc(x, fall, p.size * 0.7, 0, Math.PI * 2);
      ctx.fill();
    }
  } else if (kind === 'DUST' || kind === 'ASH') {
    ctx.globalCompositeOperation = 'lighter';
    for (let i = 0; i < cache.particles.length; i++) {
      const p = cache.particles[i];
      const x = (((p.x + t * (30 + p.speed * 40)) % (w * 1.2)) + w * 1.2) % (w * 1.2) - w * 0.1;
      const y = horizonY + 6 + ((p.y + Math.sin(t * 0.7 + p.phase) * 4) % Math.max(1, h - horizonY - 6));
      ctx.globalAlpha = Math.min(1, 0.12 + p.warm * 0.12);
      ctx.fillStyle = kind === 'ASH' ? 'rgba(140, 130, 120, 1)' : 'rgba(230, 190, 120, 1)';
      ctx.fillRect(x, y, p.size * 1.6, p.size * 0.7);
    }
  } else if (kind === 'RAIN') {
    ctx.globalCompositeOperation = 'lighter';
    ctx.strokeStyle = 'rgba(200, 220, 240, 1)';
    ctx.lineWidth = 1;
    for (let i = 0; i < cache.particles.length; i++) {
      const p = cache.particles[i];
      const fallSpeed = 260;
      const sx = (((p.x + t * fallSpeed * p.speed * 0.15) % (w * 1.4)) + w * 1.4) % (w * 1.4) - w * 0.2;
      const sy = (p.y * h + t * fallSpeed * p.speed) % h;
      ctx.globalAlpha = p.drift * 0.5 * 0.6;
      ctx.beginPath();
      ctx.moveTo(sx, sy);
      ctx.lineTo(sx + 0.15 * p.size * 8, sy + p.size * 14);
      ctx.stroke();
    }
  } else if (kind === 'SPORE') {
    ctx.globalCompositeOperation = 'lighter';
    const top = horizonY;
    const band = Math.max(8, h - top);
    for (let i = 0; i < cache.particles.length; i++) {
      const p = cache.particles[i];
      const x = (((p.x + Math.sin(t * 0.3 + p.phase) * 12 + t * (2 + p.speed * 2)) % w) + w) % w;
      const y = top + band * 0.55 + ((p.y + Math.sin(t * 0.4 + p.phase) * 8) % (band * 0.45));
      const pulse = 0.3 + 0.7 * Math.abs(Math.sin(t * 0.8 + p.phase));
      ctx.globalAlpha = 0.22 * pulse;
      ctx.fillStyle = p.warm > 0.5 ? 'rgba(190, 255, 170, 1)' : 'rgba(180, 140, 255, 1)';
      ctx.beginPath();
      ctx.arc(x, y, p.size * 0.7, 0, Math.PI * 2);
      ctx.fill();
    }
  } else if (kind === 'SPARK') {
    ctx.globalCompositeOperation = 'lighter';
    for (let i = 0; i < cache.particles.length; i++) {
      const p = cache.particles[i];
      const x = (((p.x + t * (20 + p.speed * 30)) % (w * 1.2)) + w * 1.2) % (w * 1.2) - w * 0.1;
      const y = horizonY + ((p.y + t * (8 + p.speed * 12)) % Math.max(1, h - horizonY));
      const flick = 0.4 + 0.6 * Math.abs(Math.sin(t * 4 + p.phase));
      ctx.globalAlpha = 0.5 * flick;
      ctx.fillStyle = p.warm > 0.5 ? 'rgba(255, 200, 80, 1)' : 'rgba(200, 240, 255, 1)';
      ctx.fillRect(x, y, p.size, p.size);
    }
  } else {
    // FAINT — sparse motes drifting near the surface
    const top = horizonY;
    const band = Math.max(8, h - top);
    for (let i = 0; i < cache.particles.length; i++) {
      const p = cache.particles[i];
      const x = (((p.x + t * (3 + p.speed * 4)) % w) + w) % w;
      const y = top + ((p.y + Math.sin(t * 0.3 + p.phase) * 6) % band);
      ctx.globalAlpha = 0.05 + p.warm * 0.06;
      ctx.fillStyle = '#cfd8e6';
      ctx.fillRect(x, y, p.size, p.size);
    }
  }
  ctx.restore();
}

// ---------------------------------------------------------------------------
// drawLandmarks — draws model.layers.terrain.landmarks as simple filled
// silhouettes after the terrain ridges.  Each kind maps to a distinct shape:
//
//   cone     — volcanic triangle, optional accent-glow rim and crater dot.
//   caldera  — broad flat-topped trapezoid with a central V-notch.
//   mesa     — wide flat-topped butte (wide top, steep sides).
//   spire    — tall thin triangle.
//   crater   — shallow ellipse rim sitting on the ground line.
//   arch     — two pillars + a quadratic arc between their tops.
//   canyon   — dark downward V-notch cut into the ground.
//   glacier  — pale angled wedge tinted toward ice-white.
//
// Landmarks are drawn as back-to-front silhouettes so they sit visibly ON
// the terrain, not floating above it.  No per-frame animation — they are
// pure static geometry plus a day-cycle brightness modulation.
// ---------------------------------------------------------------------------
function drawLandmarks(
  ctx: CanvasRenderingContext2D,
  cache: VistaCache,
  dc: DayCycle
): void {
  if (cache.landmarks.length === 0) return;
  // Modulate silhouette darkness with the day cycle: slightly brighter at noon
  // (backlit edge), darkest at night (pure silhouette).
  const brightK = 0.7 + dc.bright * 0.3;
  // Directional shading source — consumed per-face inside the loop below.
  const lighting = cache.model.lighting;
  ctx.save();

  for (const lm of cache.landmarks) {
    const { kind, cx, baseY, height, width } = lm;

    // Per-landmark directional shading: right-facing surface (azimuth 0° = screen-right)
    // vs left-facing (180°).  shadeFlank applies ambient+fill floor so shadow faces
    // are never crushed black.
    const rightShade = shadeFlank(lighting, 0);
    const leftShade  = shadeFlank(lighting, 180);
    const litIsRight = rightShade.mult > leftShade.mult;

    // Fake AO contact shadow — drawn before the fill so it sits under the geometry.
    // Width × 0.6 approximates the visible ground-contact footprint.
    aoPool(ctx, cx, baseY, width * 0.6, lighting);

    // Apply day-cycle modulation to fill opacity (directional mults applied per-face below)
    ctx.globalAlpha = brightK;

    if (kind === 'cone') {
      // Triangle split into left/right halves so each flank gets its own directional
      // brightness.  Reads unmistakably as a volcano with a hard lit/shadow terminator.
      ctx.fillStyle = lm.fillColor;

      // Shadow-side flank (whichever faces away from the key light)
      ctx.globalAlpha = brightK * (litIsRight ? leftShade.mult : rightShade.mult);
      ctx.beginPath();
      if (litIsRight) {
        ctx.moveTo(cx - width, baseY);
        ctx.lineTo(cx,         baseY);
        ctx.lineTo(cx,         baseY - height);
      } else {
        ctx.moveTo(cx,         baseY - height);
        ctx.lineTo(cx + width, baseY);
        ctx.lineTo(cx,         baseY);
      }
      ctx.closePath();
      ctx.fill();

      // Lit flank (sun-facing side)
      ctx.globalAlpha = brightK * (litIsRight ? rightShade.mult : leftShade.mult);
      ctx.beginPath();
      if (litIsRight) {
        ctx.moveTo(cx,         baseY - height);
        ctx.lineTo(cx + width, baseY);
        ctx.lineTo(cx,         baseY);
      } else {
        ctx.moveTo(cx - width, baseY);
        ctx.lineTo(cx,         baseY);
        ctx.lineTo(cx,         baseY - height);
      }
      ctx.closePath();
      ctx.fill();

      // Key-colour tint on the lit face — shifts the warm flank toward the sun's hue.
      const coneLitTint = litIsRight ? rightShade.tint : leftShade.tint;
      ctx.save();
      ctx.globalCompositeOperation = 'lighter';
      ctx.globalAlpha = brightK * 0.10;
      ctx.fillStyle = coneLitTint;
      ctx.beginPath();
      if (litIsRight) {
        ctx.moveTo(cx, baseY - height); ctx.lineTo(cx + width, baseY); ctx.lineTo(cx, baseY);
      } else {
        ctx.moveTo(cx - width, baseY); ctx.lineTo(cx, baseY); ctx.lineTo(cx, baseY - height);
      }
      ctx.closePath();
      ctx.fill();
      ctx.restore();

      // Sun-relative rim light on the outer edge of the lit flank.
      const coneRim = rimLight(lighting, litIsRight ? 0 : 180, 'sun');
      if (coneRim.mult > 0.005) {
        ctx.save();
        ctx.globalCompositeOperation = 'lighter';
        ctx.globalAlpha = brightK * coneRim.mult * 1.2;
        ctx.strokeStyle = coneRim.tint;
        ctx.lineWidth = 2;
        ctx.beginPath();
        if (litIsRight) {
          ctx.moveTo(cx, baseY - height); ctx.lineTo(cx + width, baseY);
        } else {
          ctx.moveTo(cx - width, baseY); ctx.lineTo(cx, baseY - height);
        }
        ctx.stroke();
        ctx.restore();
      }

      // Restore brightK before the accent pass (accent uses its own save/restore
      // so its alpha is independent, but reset here for clarity)
      ctx.globalAlpha = brightK;

      // Optional accent: warm glow rim just below the apex and a hot crater dot.
      if (lm.useAccent) {
        ctx.save();
        ctx.globalCompositeOperation = 'lighter';
        ctx.globalAlpha = dc.bright * 0.18 + 0.08;
        // Rim glow: thin stroke along the upper flanks
        ctx.strokeStyle = lm.accentColor;
        ctx.lineWidth = 2;
        ctx.beginPath();
        ctx.moveTo(cx - width * 0.55, baseY - height * 0.55);
        ctx.lineTo(cx, baseY - height);
        ctx.lineTo(cx + width * 0.55, baseY - height * 0.55);
        ctx.stroke();
        // Crater dot: small filled circle at the apex
        ctx.globalAlpha = 0.55;
        ctx.fillStyle = lm.accentColor;
        ctx.beginPath();
        ctx.arc(cx, baseY - height, Math.max(2, width * 0.06), 0, Math.PI * 2);
        ctx.fill();
        ctx.restore();
      }

    } else if (kind === 'caldera') {
      // Broad flat-topped trapezoid with a central V-notch — split at the centre
      // so each outer flank gets its own directional brightness.
      const topW   = width * 0.55;   // flat top narrower than base
      const notchW = width * 0.14;   // notch mouth width
      const notchD = height * 0.16;  // notch depth
      const notchY = baseY - height + notchD;

      ctx.fillStyle = lm.fillColor;

      // Shadow half
      ctx.globalAlpha = brightK * (litIsRight ? leftShade.mult : rightShade.mult);
      ctx.beginPath();
      if (litIsRight) {
        ctx.moveTo(cx - width, baseY);
        ctx.lineTo(cx - topW,   baseY - height);
        ctx.lineTo(cx - notchW, baseY - height);
        ctx.lineTo(cx,          notchY);
        ctx.lineTo(cx,          baseY);
      } else {
        ctx.moveTo(cx,          baseY);
        ctx.lineTo(cx,          notchY);
        ctx.lineTo(cx + notchW, baseY - height);
        ctx.lineTo(cx + topW,   baseY - height);
        ctx.lineTo(cx + width,  baseY);
      }
      ctx.closePath();
      ctx.fill();

      // Lit half
      ctx.globalAlpha = brightK * (litIsRight ? rightShade.mult : leftShade.mult);
      ctx.beginPath();
      if (litIsRight) {
        ctx.moveTo(cx,          baseY);
        ctx.lineTo(cx,          notchY);
        ctx.lineTo(cx + notchW, baseY - height);
        ctx.lineTo(cx + topW,   baseY - height);
        ctx.lineTo(cx + width,  baseY);
      } else {
        ctx.moveTo(cx - width, baseY);
        ctx.lineTo(cx - topW,   baseY - height);
        ctx.lineTo(cx - notchW, baseY - height);
        ctx.lineTo(cx,          notchY);
        ctx.lineTo(cx,          baseY);
      }
      ctx.closePath();
      ctx.fill();

      // Restore for accent pass
      ctx.globalAlpha = brightK;

      // Accent: warm glow in the caldera bowl
      if (lm.useAccent) {
        ctx.save();
        ctx.globalCompositeOperation = 'lighter';
        ctx.globalAlpha = 0.30;
        const cg = ctx.createRadialGradient(cx, baseY - height + notchD, 0, cx, baseY - height + notchD, notchW * 2.5);
        cg.addColorStop(0, lm.accentColor);
        cg.addColorStop(1, 'rgba(255, 60, 0, 0)');
        ctx.fillStyle = cg;
        ctx.fillRect(cx - notchW * 2.5, baseY - height - notchD, notchW * 5, notchD * 3);
        ctx.restore();
      }

    } else if (kind === 'mesa') {
      // Wide flat-topped butte — sloping side faces split left/right; top face
      // shaded by the sky-facing (azimuth 270°) component.
      const topW      = width * 0.80;
      const h2        = height * 0.65;
      const topShadeM = shadeFlank(lighting, 270);  // sky-facing top surface

      ctx.fillStyle = lm.fillColor;

      // Shadow sloping face
      ctx.globalAlpha = brightK * (litIsRight ? leftShade.mult : rightShade.mult);
      ctx.beginPath();
      if (litIsRight) {
        ctx.moveTo(cx - width, baseY);
        ctx.lineTo(cx - topW,  baseY - h2);
        ctx.lineTo(cx,         baseY - h2);
        ctx.lineTo(cx,         baseY);
      } else {
        ctx.moveTo(cx,         baseY);
        ctx.lineTo(cx,         baseY - h2);
        ctx.lineTo(cx + topW,  baseY - h2);
        ctx.lineTo(cx + width, baseY);
      }
      ctx.closePath();
      ctx.fill();

      // Lit sloping face
      ctx.globalAlpha = brightK * (litIsRight ? rightShade.mult : leftShade.mult);
      ctx.beginPath();
      if (litIsRight) {
        ctx.moveTo(cx,         baseY);
        ctx.lineTo(cx,         baseY - h2);
        ctx.lineTo(cx + topW,  baseY - h2);
        ctx.lineTo(cx + width, baseY);
      } else {
        ctx.moveTo(cx - width, baseY);
        ctx.lineTo(cx - topW,  baseY - h2);
        ctx.lineTo(cx,         baseY - h2);
        ctx.lineTo(cx,         baseY);
      }
      ctx.closePath();
      ctx.fill();

      // Pale top-face edge highlight — brightness modulated by how much sky the top sees
      ctx.save();
      ctx.globalCompositeOperation = 'lighter';
      ctx.globalAlpha = dc.bright * topShadeM.mult * 0.15;
      ctx.strokeStyle = 'rgba(220, 210, 190, 0.7)';
      ctx.lineWidth = 1.5;
      ctx.beginPath();
      ctx.moveTo(cx - topW, baseY - h2);
      ctx.lineTo(cx + topW, baseY - h2);
      ctx.stroke();
      ctx.restore();

    } else if (kind === 'spire') {
      // Tall thin triangle split into left/right halves for directional shading.
      // Reads as a rock needle or alien antenna mast with a crisp lit/shadow divide.
      const sw = width * 0.22;
      const sh = height * 1.3;
      const spireH  = Math.min(sh, cache.horizonY * 1.1);
      const spireTY = baseY - spireH;
      ctx.fillStyle = lm.fillColor;

      // Shadow half
      ctx.globalAlpha = brightK * (litIsRight ? leftShade.mult : rightShade.mult);
      ctx.beginPath();
      if (litIsRight) {
        ctx.moveTo(cx - sw, baseY); ctx.lineTo(cx, baseY); ctx.lineTo(cx, spireTY);
      } else {
        ctx.moveTo(cx, spireTY); ctx.lineTo(cx + sw, baseY); ctx.lineTo(cx, baseY);
      }
      ctx.closePath();
      ctx.fill();

      // Lit half
      ctx.globalAlpha = brightK * (litIsRight ? rightShade.mult : leftShade.mult);
      ctx.beginPath();
      if (litIsRight) {
        ctx.moveTo(cx, spireTY); ctx.lineTo(cx + sw, baseY); ctx.lineTo(cx, baseY);
      } else {
        ctx.moveTo(cx - sw, baseY); ctx.lineTo(cx, baseY); ctx.lineTo(cx, spireTY);
      }
      ctx.closePath();
      ctx.fill();

      // Sun-side rim on the lit-flank outer edge — spires have a pronounced rim
      // because they're very thin and the silhouette edge is always visible.
      const spireRim = rimLight(lighting, litIsRight ? 0 : 180, 'sun');
      if (spireRim.mult > 0.005) {
        ctx.save();
        ctx.globalCompositeOperation = 'lighter';
        ctx.globalAlpha = brightK * spireRim.mult * 1.4;
        ctx.strokeStyle = spireRim.tint;
        ctx.lineWidth = 1.5;
        ctx.beginPath();
        if (litIsRight) {
          ctx.moveTo(cx, spireTY); ctx.lineTo(cx + sw, baseY);
        } else {
          ctx.moveTo(cx - sw, baseY); ctx.lineTo(cx, spireTY);
        }
        ctx.stroke();
        ctx.restore();
      }

      // Restore brightK before the ARTIFICIAL emissive pass
      ctx.globalAlpha = brightK;

      // ARTIFICIAL emissive pass — warm window bands + cold conduit edge traces.
      // Guard: accentWarm is only set on ARTIFICIAL so this is a zero-cost no-op
      // for all 11 natural types.
      const accentWarmSp = cache.model.palette.accentWarm;
      if (accentWarmSp) {
        const [wr, wg, wb] = accentWarmSp;
        const [cr, cg, cb] = cache.model.palette.accent;   // cold cyan conduit

        // Per-spire PRNG seed: position-keyed so each tower has its own lit pattern.
        const rngSp = splitmix32(deriveChildSeed(cache.model.seed, `spire-em-${Math.round(cx)}`));

        ctx.save();
        ctx.globalCompositeOperation = 'lighter';

        // Warm window bands — horizontal dashes at deterministic heights along the spire.
        // Always consume 2 rng values per band (lit-roll + alpha-roll) so the
        // sequence is stable across branches.
        const nBands = 4 + Math.floor(rngSp() * 4);   // 4–7 bands, 1 consume
        for (let bi = 0; bi < nBands; bi++) {
          const yFrac  = 0.12 + (bi / Math.max(1, nBands - 1)) * 0.72;
          const bandY  = spireTY + spireH * yFrac;
          // Spire width at bandY (linear interpolation; 0 at tip, sw*2 at base)
          const wAtY   = sw * 2 * (1 - (baseY - bandY) / spireH);
          const bandW  = Math.max(3, wAtY * 0.55);
          const bandH2 = Math.max(1.5, bandW * 0.16);
          const litRoll   = rngSp();           // consume 1
          const alphaRoll = rngSp();           // consume 1 (always)
          // splitmix32() returns [0,1) — compare against the fraction directly.
          const lit = litRoll < 0.62;
          if (lit) {
            ctx.globalAlpha = (0.48 + alphaRoll * 0.42) * brightK;
            ctx.fillStyle = `rgb(${wr}, ${wg}, ${wb})`;
            ctx.fillRect(
              Math.round(cx - bandW * 0.5),
              Math.round(bandY - bandH2 * 0.5),
              Math.round(bandW),
              Math.max(1, Math.round(bandH2)),
            );
          }
        }

        // Cold conduit traces — thin lines along the left and right spire edges.
        // Reads as illuminated structural conduit running up the antenna mast.
        ctx.globalAlpha = (0.14 * dc.bright + 0.06) * brightK;
        ctx.strokeStyle = `rgba(${cr}, ${cg}, ${cb}, 0.88)`;
        ctx.lineWidth = 1;
        ctx.beginPath();
        ctx.moveTo(cx - sw, baseY);
        ctx.lineTo(cx, spireTY);
        ctx.stroke();
        ctx.beginPath();
        ctx.moveTo(cx + sw, baseY);
        ctx.lineTo(cx, spireTY);
        ctx.stroke();

        ctx.restore();
      }

    } else if (kind === 'crater') {
      // Shallow ellipse rim sitting on the ground line.
      // Only the above-ground rim arc is drawn so it reads as a depression.
      const rx = width * 0.9;
      const ry = height * 0.20;
      ctx.fillStyle = lm.fillColor;
      // Rim fill: a thin filled ellipse ring
      ctx.beginPath();
      ctx.ellipse(cx, baseY, rx, ry, 0, Math.PI, Math.PI * 2);  // upper half
      ctx.lineTo(cx + rx, baseY);
      ctx.closePath();
      ctx.fill();
      // Inner floor: slightly darker
      ctx.save();
      ctx.globalAlpha *= 0.6;
      ctx.beginPath();
      ctx.ellipse(cx, baseY + ry * 0.3, rx * 0.70, ry * 0.55, 0, 0, Math.PI * 2);
      ctx.fill();
      ctx.restore();

    } else if (kind === 'arch') {
      // Two pillars shaded by their dominant outer face; arch span by the sky-facing top.
      const ow        = width * 0.22;
      const iw        = width * 0.48;
      const ah        = height * 0.85;
      const topShadeA = shadeFlank(lighting, 270);  // arch keystone faces upward

      ctx.fillStyle = lm.fillColor;
      // Left pillar: dominant outer face is LEFT (azimuth 180°)
      ctx.globalAlpha = brightK * leftShade.mult;
      ctx.fillRect(cx - iw - ow, baseY - ah, ow, ah);
      // Right pillar: dominant outer face is RIGHT (azimuth 0°)
      ctx.globalAlpha = brightK * rightShade.mult;
      ctx.fillRect(cx + iw, baseY - ah, ow, ah);
      // Arch span — sky-facing keystone
      ctx.globalAlpha = brightK * topShadeA.mult;
      ctx.beginPath();
      ctx.moveTo(cx - iw - ow, baseY - ah);
      ctx.lineTo(cx - iw, baseY - ah);
      ctx.quadraticCurveTo(cx, baseY - height, cx + iw, baseY - ah);
      ctx.lineTo(cx + iw + ow, baseY - ah);
      ctx.quadraticCurveTo(cx, baseY - ah * 1.1, cx - iw - ow, baseY - ah);
      ctx.closePath();
      ctx.fill();

    } else if (kind === 'canyon') {
      // Dark downward V-notch — left wall faces inward (right, azimuth 0°),
      // right wall faces inward (left, azimuth 180°).  Canyons are inherently dark
      // so mults are scaled down to 0.82 to preserve that shadowed character.
      const cd  = height * 0.55;
      const cw2 = width * 0.75;
      ctx.fillStyle = lm.fillColor;

      // Left wall (inward normal = right-facing)
      ctx.globalAlpha = brightK * rightShade.mult * 0.82;
      ctx.beginPath();
      ctx.moveTo(cx - cw2, baseY);
      ctx.lineTo(cx,       baseY + cd);
      ctx.lineTo(cx,       baseY);
      ctx.closePath();
      ctx.fill();

      // Right wall (inward normal = left-facing)
      ctx.globalAlpha = brightK * leftShade.mult * 0.82;
      ctx.beginPath();
      ctx.moveTo(cx,       baseY);
      ctx.lineTo(cx,       baseY + cd);
      ctx.lineTo(cx + cw2, baseY);
      ctx.closePath();
      ctx.fill();

      // Pale rim highlight along the canyon edge
      ctx.save();
      ctx.globalCompositeOperation = 'lighter';
      ctx.globalAlpha = dc.bright * 0.10;
      ctx.strokeStyle = 'rgba(180, 170, 160, 0.6)';
      ctx.lineWidth = 1.2;
      ctx.beginPath();
      ctx.moveTo(cx - cw2, baseY);
      ctx.lineTo(cx, baseY + cd);
      ctx.lineTo(cx + cw2, baseY);
      ctx.stroke();
      ctx.restore();

    } else if (kind === 'glacier') {
      // Pale blue-white wedge — primarily sky-facing so the top surface (azimuth 270°)
      // drives the shading.  A higher sun hits the glacier more directly.
      const gw        = width * 1.1;
      const gh        = height * 0.55;
      const topShadeG = shadeFlank(lighting, 270);

      ctx.globalAlpha = brightK * topShadeG.mult;
      ctx.fillStyle = lm.fillColor;
      ctx.beginPath();
      ctx.moveTo(cx - gw,        baseY);
      ctx.lineTo(cx - gw * 0.4,  baseY - gh);
      ctx.lineTo(cx + gw * 0.55, baseY - gh * 0.3);
      ctx.lineTo(cx + gw,        baseY);
      ctx.closePath();
      ctx.fill();

      // Restore before the accent/ice-sheen pass
      ctx.globalAlpha = brightK;

      // Ice sheen: a soft lighter edge along the top
      if (lm.useAccent) {
        ctx.save();
        ctx.globalCompositeOperation = 'lighter';
        ctx.globalAlpha = dc.bright * 0.22;
        ctx.strokeStyle = lm.accentColor;
        ctx.lineWidth = 2;
        ctx.beginPath();
        ctx.moveTo(cx - gw * 0.4, baseY - gh);
        ctx.lineTo(cx + gw * 0.55, baseY - gh * 0.3);
        ctx.stroke();
        ctx.restore();
      }
    }
    // unknown kind → skip (degrade gracefully, never throw)
  }

  ctx.restore();
}

// ---------------------------------------------------------------------------
// drawCloudDeck — GAS_GIANT terrain mode.
// Replaces terrain ridges + ground plane with a banded cloud-deck horizon.
// Called only when model.layers.terrain.mode === 'cloud-deck'.
// The platform silhouette in the foreground reads as "floating above cloud tops."
// ---------------------------------------------------------------------------
function drawCloudDeck(
  ctx: CanvasRenderingContext2D,
  w: number,
  h: number,
  t: number,
  model: VistaModel,
  cache: VistaCache,
  dc: DayCycle
): void {
  const { horizonY } = cache;
  const pal = model.palette;
  const deckH = h - horizonY;

  // Deep atmospheric base — gradient from the horizon tint down to a dark purple base
  {
    const [hr, hg, hb] = pal.skyHorizon;
    const [tr, tg, tb] = pal.skyTop;
    const base = ctx.createLinearGradient(0, horizonY, 0, h);
    base.addColorStop(0,   `rgb(${Math.round(hr * 0.82)}, ${Math.round(hg * 0.82)}, ${Math.round(hb * 0.82)})`);
    base.addColorStop(0.4, `rgb(${Math.round(hr * 0.35 + tr * 0.12)}, ${Math.round(hg * 0.35 + tg * 0.12)}, ${Math.round(hb * 0.35 + tb * 0.15)})`);
    base.addColorStop(1,   `rgb(${Math.round(tr * 0.18)}, ${Math.round(tg * 0.18)}, ${Math.round(tb * 0.25)})`);
    ctx.fillStyle = base;
    ctx.fillRect(0, horizonY, w, deckH);
  }

  // Receding cloud bands — each baked in buildVistaCache with a parallax speed.
  // Far bands (low yFrac) drift slowly; near bands drift fast — suggests depth.
  for (const band of cache.cloudBands) {
    const bandY   = horizonY + band.yFrac * deckH;
    const thick   = band.thickFrac * deckH;
    const drift   = t === 0 ? 0 : (t * band.speed * 10) % w;
    const widthR  = 0.55 + band.yFrac * 0.55;  // wider near-camera, narrow at horizon
    const [br, bg2, bb] = band.rgb;
    const alpha   = band.alpha * (0.65 + dc.bright * 0.35);

    ctx.save();
    ctx.globalCompositeOperation = 'source-over';
    const bgrad = ctx.createLinearGradient(0, bandY - thick, 0, bandY + thick);
    bgrad.addColorStop(0,    `rgba(${br}, ${bg2}, ${bb}, 0)`);
    bgrad.addColorStop(0.30, `rgba(${br}, ${bg2}, ${bb}, ${(alpha * 0.85).toFixed(3)})`);
    bgrad.addColorStop(0.70, `rgba(${br}, ${bg2}, ${bb}, ${alpha.toFixed(3)})`);
    bgrad.addColorStop(1,    `rgba(${br}, ${bg2}, ${bb}, 0)`);
    ctx.fillStyle = bgrad;
    // Ellipse suggests a curved cloud-layer receding toward the horizon
    ctx.beginPath();
    ctx.ellipse(w * 0.5 + drift, bandY, w * widthR, thick, 0, 0, Math.PI * 2);
    ctx.fill();
    ctx.restore();
  }

  // Foreground platform / observation-deck silhouette — reads as "standing on a
  // floating structure above the cloud tops."
  {
    const platW  = w * 0.30;
    const railH  = Math.max(4, h * 0.028);
    const pilH   = Math.min(h * 0.16, deckH * 0.55);
    const pilW   = Math.max(4, w * 0.014);
    const railY  = h - pilH - railH;
    const [sr, sg, sb] = pal.surface;
    const platCol = `rgba(${Math.round(sr * 0.35)}, ${Math.round(sg * 0.38)}, ${Math.round(sb * 0.45)}, 0.94)`;

    ctx.save();
    ctx.fillStyle = platCol;
    ctx.fillRect(w * 0.5 - platW * 0.5, railY, platW, railH);
    for (const pf of [0.15, 0.38, 0.62, 0.85]) {
      ctx.fillRect(w * 0.5 - platW * 0.5 + platW * pf - pilW * 0.5, railY + railH, pilW, pilH);
    }
    // Rail accent edge — subtle energy/tint glow from palette.accent
    ctx.save();
    ctx.globalCompositeOperation = 'lighter';
    ctx.globalAlpha = 0.18 * dc.bright;
    const [ar, ag, ab] = pal.accent;
    ctx.strokeStyle = `rgba(${ar}, ${ag}, ${ab}, 0.9)`;
    ctx.lineWidth = 2;
    ctx.beginPath();
    ctx.moveTo(w * 0.5 - platW * 0.5, railY);
    ctx.lineTo(w * 0.5 + platW * 0.5, railY);
    ctx.stroke();
    ctx.restore();
    ctx.restore();
  }
}

// ---------------------------------------------------------------------------
// drawPlating — ARTIFICIAL terrain mode.
// Replaces terrain ridges with a flat engineered plating ground: a solid fill
// plus a perspective-squashed seam grid.
// Landmarks (spires/towers the model emits) are drawn afterward by the normal
// drawLandmarks call in drawScene.
// ---------------------------------------------------------------------------
function drawPlating(
  ctx: CanvasRenderingContext2D,
  w: number,
  h: number,
  t: number,
  model: VistaModel,
  cache: VistaCache,
  dc: DayCycle
): void {
  const { horizonY, platingPx } = cache;
  const pal = model.palette;
  const groundH = h - horizonY;
  const [sr, sg, sb] = pal.surface;

  // Base plating fill — gradient lighter at horizon, darker at camera
  const fill = ctx.createLinearGradient(0, horizonY, 0, h);
  fill.addColorStop(0,   `rgb(${Math.round(sr * 0.72)}, ${Math.round(sg * 0.72)}, ${Math.round(sb * 0.78)})`);
  fill.addColorStop(0.5, `rgb(${Math.round(sr * 0.55)}, ${Math.round(sg * 0.55)}, ${Math.round(sb * 0.60)})`);
  fill.addColorStop(1,   `rgb(${Math.round(sr * 0.38)}, ${Math.round(sg * 0.38)}, ${Math.round(sb * 0.42)})`);
  ctx.fillStyle = fill;
  ctx.fillRect(0, horizonY, w, groundH);

  // Panel seam grid: horizontal seams with perspective crowding near the horizon,
  // vertical seams parallel.
  ctx.save();
  const seamAlpha = 0.18 + dc.bright * 0.08;
  // Seam lines: a lighter tint than the base
  ctx.strokeStyle = `rgba(${Math.min(255, Math.round(sr * 1.4 + 30))}, ${Math.min(255, Math.round(sg * 1.4 + 30))}, ${Math.min(255, Math.round(sb * 1.4 + 35))}, ${seamAlpha.toFixed(3)})`;
  ctx.lineWidth = 1;

  // Horizontal seams — exponential crowding so they converge at horizonY
  const rows = Math.ceil(groundH / platingPx) + 2;
  for (let ri = 0; ri <= rows; ri++) {
    const tFrac = Math.pow(ri / rows, 1.8);
    const yLine = horizonY + tFrac * groundH;
    if (yLine > h) break;
    ctx.beginPath(); ctx.moveTo(0, yLine); ctx.lineTo(w, yLine); ctx.stroke();
  }

  // Vertical seams — uniform spacing (not perspective-converging in 2.5D)
  const cols = Math.ceil(w / platingPx) + 1;
  for (let ci = 0; ci <= cols; ci++) {
    const xLine = ci * platingPx;
    ctx.beginPath(); ctx.moveTo(xLine, horizonY); ctx.lineTo(xLine, h); ctx.stroke();
  }

  // Panel bevel top-edge highlight — a thin lighter line just below each seam
  const bevelAlpha = 0.09 * dc.bright * (0.6 + 0.4 * Math.sin(t * 0.3 + 1));
  ctx.strokeStyle = `rgba(220, 230, 245, ${bevelAlpha.toFixed(3)})`;
  ctx.lineWidth = 1;
  for (let ri = 1; ri <= rows; ri++) {
    const prevFrac = Math.pow((ri - 1) / rows, 1.8);
    const yLine = horizonY + prevFrac * groundH + 2;
    if (yLine > h) break;
    ctx.beginPath(); ctx.moveTo(0, yLine); ctx.lineTo(w, yLine); ctx.stroke();
  }

  ctx.restore();

  // ---- Emissive window/signage grid — ARTIFICIAL only ----
  // A deterministic seed-driven grid of lit/dark cells that reads as windows
  // and signage panels on a space station surface at night.
  // The warm accent (accentWarm) is used for colour; cells are toggled on/off
  // via splitmix32 seeded from model.seed so the pattern is per-seed stable.
  const emissiveParams = model.layers.terrain.emissive;
  const accentWarm = model.palette.accentWarm;
  if (emissiveParams && accentWarm) {
    const [wr, wg, wb] = accentWarm;
    const { density } = emissiveParams;
    const rngEm = splitmix32(deriveChildSeed(model.seed, 'emissive'));

    // Window cell: a small rectangle centred in each plating panel.
    // Size scales with panel size so smaller panels = smaller windows.
    const winW = Math.max(3, Math.round(platingPx * 0.28));
    const winH = Math.max(2, Math.round(platingPx * 0.18));

    ctx.save();
    ctx.globalCompositeOperation = 'lighter';

    for (let ci = 0; ci < cols; ci++) {
      const xLeft = ci * platingPx;
      const xCentre = xLeft + platingPx * 0.5;
      for (let ri = 0; ri < rows; ri++) {
        // Per-cell: always draw 2 rng values so the sequence is position-stable.
        const litRoll  = rngEm();          // lit/dark decision
        const alphaRaw = rngEm();          // per-cell brightness variation

        const tFracPrev = Math.pow(ri       / rows, 1.8);
        const tFracNext = Math.pow((ri + 1) / rows, 1.8);
        const yTop  = horizonY + tFracPrev * groundH;
        const yBot  = horizonY + tFracNext * groundH;
        const panH  = yBot - yTop;
        if (yTop >= h) break;

        // Cells near the horizon are smaller in perspective; skip if too tiny
        if (panH < winH * 0.8) continue;

        // splitmix32() returns [0,1) — compare against density directly.
        const lit = litRoll < density;
        if (!lit) continue;

        const alpha = 0.52 + alphaRaw * 0.40;
        ctx.globalAlpha = alpha * (0.55 + dc.bright * 0.45);

        const wx = Math.round(xCentre - winW * 0.5);
        const wy = Math.round(yTop + (panH - winH) * 0.5);
        ctx.fillStyle = `rgb(${wr}, ${wg}, ${wb})`;
        ctx.fillRect(wx, wy, winW, Math.max(1, winH));
      }
    }

    // Warm city-light bleed just above the ground-horizon seam: a narrow
    // additive gradient that reads as ambient light from massed windows.
    ctx.globalAlpha = 0.12 * dc.bright + 0.05;
    const bleedH = Math.min(groundH * 0.22, h * 0.08);
    const warmBleed = ctx.createLinearGradient(0, horizonY, 0, horizonY + bleedH);
    warmBleed.addColorStop(0,   `rgba(${wr}, ${wg}, ${wb}, 0.70)`);
    warmBleed.addColorStop(1,   `rgba(${wr}, ${wg}, ${wb}, 0)`);
    ctx.fillStyle = warmBleed;
    ctx.fillRect(0, horizonY, w, bleedH);

    ctx.restore();
  }
}

// ---------------------------------------------------------------------------
// drawDepositGlyph — one deposit-marker canvas glyph at (sx, sy).
// Each visual maps to a recognizable shape, tinted from palette.accent.
// ---------------------------------------------------------------------------
function drawDepositGlyph(
  ctx: CanvasRenderingContext2D,
  w: number,
  h: number,
  t: number,
  sx: number,
  sy: number,
  visual: string,
  intensity: number,
  accentRgb: RGB
): void {
  const sz  = Math.max(12, Math.min(w * 0.025, h * 0.04) * (0.6 + intensity * 0.7));
  const [ar, ag, ab] = accentRgb;
  ctx.save();

  if (visual === 'ore-vein') {
    // Jagged zigzag vein exposed in the terrain surface, in accent color
    ctx.strokeStyle = `rgba(${ar}, ${ag}, ${ab}, ${(0.55 + intensity * 0.35).toFixed(3)})`;
    ctx.lineWidth   = Math.max(1.5, sz * 0.14);
    ctx.lineCap     = 'round';
    const vw = sz * 1.8;
    ctx.beginPath();
    ctx.moveTo(sx - vw * 0.5, sy);
    for (let i = 1; i <= 5; i++) {
      ctx.lineTo(sx - vw * 0.5 + (i / 5) * vw, sy - (i % 2 === 1 ? sz * 0.55 : sz * 0.18));
    }
    ctx.stroke();
    // Glint glow around the vein apex
    ctx.globalCompositeOperation = 'lighter';
    ctx.globalAlpha = 0.25 * intensity;
    const gg = ctx.createRadialGradient(sx, sy - sz * 0.3, 0, sx, sy - sz * 0.3, sz * 0.7);
    gg.addColorStop(0, `rgba(${ar}, ${ag}, ${ab}, 0.9)`);
    gg.addColorStop(1, `rgba(${ar}, ${ag}, ${ab}, 0)`);
    ctx.fillStyle = gg;
    ctx.fillRect(sx - sz * 0.7, sy - sz * 0.9, sz * 1.4, sz * 1.0);

  } else if (visual === 'gas-seep') {
    // Wispy upward puffs — greenish-yellow accent tinted
    const gR = Math.round(ar * 0.3 + 110);
    const gG = Math.round(ag * 0.4 + 140);
    const gB = Math.round(ab * 0.2 + 60);
    ctx.globalCompositeOperation = 'lighter';
    for (let pi = 0; pi < 3; pi++) {
      const px2  = sx + (pi - 1) * sz * 0.7;
      const rise = t === 0 ? sz * 0.4 : sz * 0.4 + (t * 8 + pi * 2.1) % (sz * 1.4);
      const pr   = sz * (0.30 + pi * 0.10);
      const pg   = ctx.createRadialGradient(px2, sy - rise, 0, px2, sy - rise, pr * 1.8);
      pg.addColorStop(0, `rgba(${gR}, ${gG}, ${gB}, ${(0.30 * intensity).toFixed(3)})`);
      pg.addColorStop(1, `rgba(${gR}, ${gG}, ${gB}, 0)`);
      ctx.fillStyle = pg;
      ctx.fillRect(px2 - pr * 1.8, sy - rise - pr * 1.8, pr * 3.6, pr * 3.6);
    }

  } else if (visual === 'thermal-vent') {
    // Steam column widening upward + bright base glow
    const ventH  = sz * 2.0;
    const topW   = sz * 0.80;
    const botW   = sz * 0.22;
    ctx.globalCompositeOperation = 'lighter';
    const vg = ctx.createLinearGradient(sx, sy, sx, sy - ventH);
    vg.addColorStop(0,   `rgba(${ar}, ${ag}, ${ab}, ${(0.55 * intensity).toFixed(3)})`);
    vg.addColorStop(0.6, `rgba(230, 230, 240, ${(0.30 * intensity).toFixed(3)})`);
    vg.addColorStop(1,   `rgba(230, 230, 240, 0)`);
    ctx.fillStyle = vg;
    ctx.beginPath();
    ctx.moveTo(sx - botW, sy);
    ctx.quadraticCurveTo(sx - topW * 0.5, sy - ventH * 0.5, sx - topW, sy - ventH);
    ctx.lineTo(sx + topW, sy - ventH);
    ctx.quadraticCurveTo(sx + topW * 0.5, sy - ventH * 0.5, sx + botW, sy);
    ctx.closePath();
    ctx.fill();
    const bg = ctx.createRadialGradient(sx, sy, 0, sx, sy, sz * 0.9);
    bg.addColorStop(0, `rgba(${ar}, ${ag}, ${ab}, ${(0.6 * intensity).toFixed(3)})`);
    bg.addColorStop(1, `rgba(${ar}, ${ag}, ${ab}, 0)`);
    ctx.fillStyle = bg;
    ctx.fillRect(sx - sz * 0.9, sy - sz * 0.9, sz * 1.8, sz * 1.8);

  } else if (visual === 'hydrocarbon-pool') {
    // Dark oval pool on the ground with a shimmering highlight
    const pw = sz * 1.6;
    const ph = sz * 0.50;
    ctx.globalCompositeOperation = 'source-over';
    const pg = ctx.createRadialGradient(sx, sy, 0, sx, sy, pw);
    pg.addColorStop(0,   `rgba(${Math.round(ar * 0.15)}, ${Math.round(ag * 0.12)}, ${Math.round(ab * 0.20)}, ${(0.75 * intensity).toFixed(3)})`);
    pg.addColorStop(0.7, `rgba(${Math.round(ar * 0.10)}, ${Math.round(ag * 0.10)}, ${Math.round(ab * 0.15)}, ${(0.55 * intensity).toFixed(3)})`);
    pg.addColorStop(1,   `rgba(${Math.round(ar * 0.05)}, ${Math.round(ag * 0.05)}, ${Math.round(ab * 0.08)}, 0)`);
    ctx.fillStyle = pg;
    ctx.save(); ctx.scale(1, ph / pw);
    ctx.beginPath(); ctx.arc(sx, sy * (pw / ph), pw, 0, Math.PI * 2); ctx.fill();
    ctx.restore();
    // Shimmer stripe
    ctx.globalCompositeOperation = 'lighter';
    ctx.globalAlpha = 0.25 * intensity * (t === 0 ? 1 : 0.5 + 0.5 * Math.sin(t * 1.5));
    ctx.strokeStyle = `rgba(${ar}, ${ag}, ${ab}, 1)`;
    ctx.lineWidth   = Math.max(1, sz * 0.08);
    ctx.beginPath();
    ctx.ellipse(sx - pw * 0.15, sy - ph * 0.1, pw * 0.35, ph * 0.18, -0.3, 0, Math.PI * 2);
    ctx.stroke();

  } else if (visual === 'crystal') {
    // Cluster of angular gem shapes glowing in accent color
    ctx.globalCompositeOperation = 'lighter';
    for (let ci = 0; ci < 4; ci++) {
      const cAngle = (ci / 4) * Math.PI * 2 + Math.PI * 0.15;
      const cDist  = sz * (0.22 + ci * 0.18);
      const cx2    = sx + Math.cos(cAngle) * cDist;
      const cy2    = sy + Math.sin(cAngle) * cDist * 0.4;  // flatten to ground plane
      const ch     = sz * (0.60 + ci * 0.15);
      const cw2    = ch * 0.32;
      ctx.globalAlpha = (0.55 + intensity * 0.35) * (ci < 2 ? 0.9 : 0.6);
      ctx.fillStyle   = `rgba(${ar}, ${ag}, ${ab}, 1)`;
      ctx.beginPath();
      ctx.moveTo(cx2, cy2 - ch);
      ctx.lineTo(cx2 + cw2, cy2 - ch * 0.4);
      ctx.lineTo(cx2, cy2);
      ctx.lineTo(cx2 - cw2, cy2 - ch * 0.4);
      ctx.closePath();
      ctx.fill();
    }
    // Halo glow around cluster
    ctx.globalAlpha = 0.18 * intensity;
    const glowR = sz * 1.1;
    const cg = ctx.createRadialGradient(sx, sy - sz * 0.3, 0, sx, sy - sz * 0.3, glowR);
    cg.addColorStop(0, `rgba(${ar}, ${ag}, ${ab}, 0.8)`);
    cg.addColorStop(1, `rgba(${ar}, ${ag}, ${ab}, 0)`);
    ctx.fillStyle = cg;
    ctx.fillRect(sx - glowR, sy - glowR - sz * 0.3, glowR * 2, glowR * 2);

  } else if (visual === 'biolumin') {
    // Soft pulsing glowing dots — bioluminescent cyan-green tinted by accent
    const blR = Math.round(ar * 0.3 + 40);
    const blG = Math.round(ag * 0.5 + 100);
    const blB = Math.round(ab * 0.5 + 140);
    ctx.globalCompositeOperation = 'lighter';
    for (let di = 0; di < 5; di++) {
      const dAngle = (di / 5) * Math.PI * 2;
      const dDist  = sz * (0.30 + di * 0.08);
      const dx     = sx + Math.cos(dAngle) * dDist;
      const dy     = sy + Math.sin(dAngle) * dDist * 0.45;
      const pulse  = t === 0 ? 1.0 : 0.5 + 0.5 * Math.sin(t * 1.2 + di * 1.3);
      const dr     = sz * (0.18 + di * 0.04);
      const dg2    = ctx.createRadialGradient(dx, dy, 0, dx, dy, dr * 2.2);
      dg2.addColorStop(0, `rgba(${blR}, ${blG}, ${blB}, ${(0.7 * pulse * intensity).toFixed(3)})`);
      dg2.addColorStop(1, `rgba(${blR}, ${blG}, ${blB}, 0)`);
      ctx.fillStyle = dg2;
      ctx.fillRect(dx - dr * 2.2, dy - dr * 2.2, dr * 4.4, dr * 4.4);
      ctx.globalAlpha = 0.7 * pulse * intensity;
      ctx.fillStyle   = `rgba(${Math.min(255, blR + 40)}, ${Math.min(255, blG + 40)}, ${Math.min(255, blB + 20)}, 1)`;
      ctx.beginPath(); ctx.arc(dx, dy, Math.max(1.5, dr * 0.4), 0, Math.PI * 2); ctx.fill();
      ctx.globalAlpha = 1;
    }

  } else {
    // Generic fallback: accent-colored radial glow at the marker position
    ctx.globalCompositeOperation = 'lighter';
    ctx.globalAlpha = 0.4 * intensity;
    const fg = ctx.createRadialGradient(sx, sy, 0, sx, sy, sz);
    fg.addColorStop(0, `rgba(${ar}, ${ag}, ${ab}, 0.8)`);
    fg.addColorStop(1, `rgba(${ar}, ${ag}, ${ab}, 0)`);
    ctx.fillStyle = fg;
    ctx.fillRect(sx - sz, sy - sz, sz * 2, sz * 2);
  }

  ctx.restore();
}

// ---------------------------------------------------------------------------
// drawEnergyGlyph — energy-source marker glyph at (sx, sy).
// source: 'GEOTHERMAL' | 'TIDAL' | 'SOLAR' | 'WIND'
// ---------------------------------------------------------------------------
function drawEnergyGlyph(
  ctx: CanvasRenderingContext2D,
  w: number,
  h: number,
  t: number,
  sx: number,
  sy: number,
  source: string,
  intensity: number,
  accentRgb: RGB
): void {
  const sz  = Math.max(16, Math.min(w * 0.040, h * 0.055) * (0.5 + intensity * 0.6));
  const [ar, ag, ab] = accentRgb;
  ctx.save();
  ctx.globalCompositeOperation = 'lighter';

  if (source === 'GEOTHERMAL') {
    // Large steam geyser column with base orange-accent glow
    const colH = sz * 3.5;
    const topW = sz * 1.2;
    const botW = sz * 0.3;
    const col  = ctx.createLinearGradient(sx, sy, sx, sy - colH);
    col.addColorStop(0,   `rgba(${ar}, ${Math.min(255, ag + 40)}, ${ab}, ${(0.7 * intensity).toFixed(3)})`);
    col.addColorStop(0.4, `rgba(210, 225, 240, ${(0.50 * intensity).toFixed(3)})`);
    col.addColorStop(1,   `rgba(200, 220, 240, 0)`);
    ctx.fillStyle = col;
    ctx.beginPath();
    ctx.moveTo(sx - botW, sy);
    ctx.quadraticCurveTo(sx - topW * 0.6, sy - colH * 0.55, sx - topW, sy - colH);
    ctx.lineTo(sx + topW, sy - colH);
    ctx.quadraticCurveTo(sx + topW * 0.6, sy - colH * 0.55, sx + botW, sy);
    ctx.closePath();
    ctx.fill();
    const bg = ctx.createRadialGradient(sx, sy, 0, sx, sy, sz * 1.5);
    bg.addColorStop(0, `rgba(${ar}, ${Math.min(255, ag + 20)}, ${ab}, ${(0.8 * intensity).toFixed(3)})`);
    bg.addColorStop(1, `rgba(${ar}, ${ag}, ${ab}, 0)`);
    ctx.fillStyle = bg;
    ctx.fillRect(sx - sz * 1.5, sy - sz * 1.5, sz * 3, sz * 3);

  } else if (source === 'TIDAL') {
    // Three concentric wave arcs (semi-circles, open downward)
    ctx.strokeStyle = `rgba(${ar}, ${ag}, ${ab}, ${(0.65 * intensity).toFixed(3)})`;
    ctx.lineWidth   = Math.max(1.5, sz * 0.10);
    ctx.lineCap     = 'round';
    for (let wi = 0; wi < 3; wi++) {
      const wr    = sz * (0.35 + wi * 0.45);
      const shift = t === 0 ? 0 : Math.sin(t * 1.5 + wi * 1.0) * 0.06;
      ctx.globalAlpha = (0.70 - wi * 0.18) * intensity;
      ctx.beginPath();
      ctx.arc(sx, sy, wr, Math.PI + shift, Math.PI * 2 - shift);
      ctx.stroke();
    }

  } else if (source === 'SOLAR') {
    // Eight-ray sunburst + center disc, slowly rotating
    const rayCount = 8;
    const innerR   = sz * 0.25;
    const outerR   = sz * 0.90;
    const rot      = t === 0 ? 0 : t * 0.12;
    ctx.strokeStyle = `rgba(${ar}, ${ag}, ${ab}, ${(0.70 * intensity).toFixed(3)})`;
    ctx.lineWidth   = Math.max(1.5, sz * 0.08);
    ctx.lineCap     = 'round';
    for (let ri = 0; ri < rayCount; ri++) {
      const ang = (ri / rayCount) * Math.PI * 2 + rot;
      ctx.globalAlpha = (0.60 + (ri % 2) * 0.25) * intensity;
      ctx.beginPath();
      ctx.moveTo(sx + Math.cos(ang) * innerR, sy + Math.sin(ang) * innerR);
      ctx.lineTo(sx + Math.cos(ang) * outerR, sy + Math.sin(ang) * outerR);
      ctx.stroke();
    }
    ctx.globalAlpha = 0.75 * intensity;
    ctx.fillStyle   = `rgba(${ar}, ${ag}, ${ab}, 0.9)`;
    ctx.beginPath(); ctx.arc(sx, sy, innerR, 0, Math.PI * 2); ctx.fill();

  } else if (source === 'WIND') {
    // Three nested spiral arcs suggesting airflow, slowly rotating
    ctx.strokeStyle = `rgba(${ar}, ${ag}, ${ab}, ${(0.70 * intensity).toFixed(3)})`;
    ctx.lineWidth   = Math.max(1.5, sz * 0.09);
    ctx.lineCap     = 'round';
    const rot = t === 0 ? 0 : t * 0.20;
    for (let wi = 0; wi < 3; wi++) {
      const r = sz * (0.25 + wi * 0.22);
      ctx.globalAlpha = (0.55 + wi * 0.12) * intensity;
      ctx.beginPath();
      ctx.arc(sx, sy, r, (wi / 3) * Math.PI * 2 + rot, (wi / 3) * Math.PI * 2 + rot + Math.PI * 1.35);
      ctx.stroke();
    }

  } else {
    // Generic fallback: pulsing ring
    ctx.globalAlpha = 0.5 * intensity;
    ctx.strokeStyle = `rgba(${ar}, ${ag}, ${ab}, 0.8)`;
    ctx.lineWidth   = Math.max(2, sz * 0.12);
    ctx.beginPath(); ctx.arc(sx, sy, sz * 0.6, 0, Math.PI * 2); ctx.stroke();
  }

  ctx.restore();
}

// ---------------------------------------------------------------------------
// drawHazardGlyph — one hazard overlay glyph in its screen region polygon.
//
// TRUTHFULNESS (§2.5): all hazards use source-over (NOT lighter), and carry
// a minimum alpha floor so they remain visible even on bright/lush worlds.
// A scarred beautiful world still shows its scar.
// ---------------------------------------------------------------------------
function drawHazardGlyph(
  ctx: CanvasRenderingContext2D,
  w: number,
  h: number,
  t: number,
  visual: string,
  severity: number,
  pts: [number, number][]
): void {
  if (pts.length < 2) return;

  // Bounding box for quick fills + center calc
  let minX = Infinity, maxX = -Infinity, minY = Infinity, maxY = -Infinity;
  for (const [px, py] of pts) {
    if (px < minX) minX = px; if (px > maxX) maxX = px;
    if (py < minY) minY = py; if (py > maxY) maxY = py;
  }
  const rW = Math.max(1, maxX - minX);
  const rH = Math.max(1, maxY - minY);
  const cx = (minX + maxX) * 0.5;
  const cy = (minY + maxY) * 0.5;

  // TRUTHFULNESS: alpha floor — hazard never renders below this on any world.
  // source-over prevents the bloom/lighter pass from washing the overlay out.
  const alphaFloor = 0.32 + severity * 0.38;

  ctx.save();
  ctx.globalCompositeOperation = 'source-over';

  if (visual === 'lava-flow') {
    // Orange-red wash with diagonal animated flow stripes
    ctx.save();
    ctx.beginPath();
    for (let i = 0; i < pts.length; i++) {
      if (i === 0) ctx.moveTo(pts[i][0], pts[i][1]); else ctx.lineTo(pts[i][0], pts[i][1]);
    }
    ctx.closePath();
    ctx.clip();
    const lg = ctx.createLinearGradient(minX, minY, maxX, maxY);
    lg.addColorStop(0,   `rgba(200, 60, 10, ${(alphaFloor * 0.70).toFixed(3)})`);
    lg.addColorStop(0.5, `rgba(240, 100, 20, ${(alphaFloor * 0.85).toFixed(3)})`);
    lg.addColorStop(1,   `rgba(180, 40, 5,  ${(alphaFloor * 0.60).toFixed(3)})`);
    ctx.fillStyle = lg;
    ctx.fillRect(minX, minY, rW, rH);
    ctx.globalAlpha = 0.18 * severity;
    ctx.strokeStyle = `rgba(255, 200, 60, 1)`;
    ctx.lineWidth   = Math.max(2, rH * 0.10);
    for (let si = 0; si < 5; si++) {
      const ox      = minX + (si / 4) * rW;
      const flowOff = t === 0 ? 0 : (t * 12 * severity) % rH;
      ctx.beginPath();
      ctx.moveTo(ox - rH, maxY + flowOff); ctx.lineTo(ox + rH, minY + flowOff);
      ctx.stroke();
    }
    ctx.restore();

  } else if (visual === 'fault-line') {
    // Dark jagged crack across the region — always contrasts with the ground
    const segs = 8;
    ctx.globalAlpha = alphaFloor;
    ctx.strokeStyle = `rgba(30, 18, 10, 1)`;
    ctx.lineWidth   = Math.max(2, rW * 0.035);
    ctx.lineCap     = 'round'; ctx.lineJoin = 'round';
    ctx.beginPath(); ctx.moveTo(minX, cy);
    for (let si = 1; si <= segs; si++) {
      ctx.lineTo(minX + (si / segs) * rW, cy + (si % 2 === 0 ? 1 : -1) * rH * (0.18 + severity * 0.15));
    }
    ctx.stroke();
    // Pale highlight on one edge for depth
    ctx.strokeStyle = `rgba(180, 140, 80, ${(alphaFloor * 0.55).toFixed(3)})`;
    ctx.lineWidth   = Math.max(1, rW * 0.010);
    ctx.beginPath(); ctx.moveTo(minX, cy - 2);
    for (let si = 1; si <= segs; si++) {
      ctx.lineTo(minX + (si / segs) * rW, cy + (si % 2 === 0 ? 1 : -1) * rH * (0.18 + severity * 0.15) - 2);
    }
    ctx.stroke();

  } else if (visual === 'storm-cell') {
    // Rotating spiral arms + dark eye
    const rot    = t === 0 ? 0 : t * 0.6 * severity;
    const maxR   = Math.min(rW, rH) * 0.48;
    ctx.strokeStyle = `rgba(80, 100, 140, 1)`;
    ctx.lineWidth   = Math.max(2, Math.min(rW, rH) * 0.060);
    ctx.lineCap     = 'round';
    for (let arm = 0; arm < 3; arm++) {
      const baseAng = (arm / 3) * Math.PI * 2 + rot;
      ctx.globalAlpha = alphaFloor * (0.45 + arm * 0.10);
      ctx.beginPath();
      for (let step = 0; step <= 40; step++) {
        const frac = step / 40;
        const r    = maxR * (1 - frac * 0.88);
        const ang  = baseAng + frac * Math.PI * 3.2;
        const px2  = cx + Math.cos(ang) * r;
        const py2  = cy + Math.sin(ang) * r * 0.55;
        if (step === 0) ctx.moveTo(px2, py2); else ctx.lineTo(px2, py2);
      }
      ctx.stroke();
    }
    ctx.globalAlpha = alphaFloor * 0.55;
    ctx.fillStyle   = 'rgba(20, 25, 40, 0.8)';
    ctx.beginPath();
    ctx.ellipse(cx, cy, Math.min(rW, rH) * 0.08, Math.min(rW, rH) * 0.06, 0, 0, Math.PI * 2);
    ctx.fill();

  } else if (visual === 'radiation-haze') {
    // Sickly yellow-green wash + pulsing particle dots
    ctx.save();
    ctx.beginPath();
    for (let i = 0; i < pts.length; i++) {
      if (i === 0) ctx.moveTo(pts[i][0], pts[i][1]); else ctx.lineTo(pts[i][0], pts[i][1]);
    }
    ctx.closePath();
    ctx.clip();
    const rg = ctx.createRadialGradient(cx, cy, 0, cx, cy, Math.max(rW, rH) * 0.6);
    rg.addColorStop(0,   `rgba(160, 200, 60, ${(alphaFloor * 0.55).toFixed(3)})`);
    rg.addColorStop(0.6, `rgba(110, 160, 30, ${(alphaFloor * 0.40).toFixed(3)})`);
    rg.addColorStop(1,   `rgba(80, 120, 20, 0)`);
    ctx.fillStyle = rg;
    ctx.fillRect(minX, minY, rW, rH);
    const pulse = t === 0 ? 1 : 0.6 + 0.4 * Math.sin(t * 2.5);
    ctx.globalAlpha = 0.45 * severity;
    ctx.fillStyle   = `rgba(190, 230, 80, 1)`;
    for (let di = 0; di < 10; di++) {
      const dx2 = minX + ((di * 137.5) % rW);
      const dy2 = minY + ((di * 97.3)  % rH);
      const dr2 = Math.max(1.5, rW * 0.012) * pulse;
      ctx.beginPath(); ctx.arc(dx2, dy2, dr2, 0, Math.PI * 2); ctx.fill();
    }
    ctx.restore();

  } else if (visual === 'flood-zone') {
    // Blue semi-transparent wash with ripple lines
    const fg2 = ctx.createLinearGradient(minX, minY, minX, maxY);
    fg2.addColorStop(0,   `rgba(40, 80, 160, ${(alphaFloor * 0.45).toFixed(3)})`);
    fg2.addColorStop(0.5, `rgba(30, 65, 140, ${(alphaFloor * 0.60).toFixed(3)})`);
    fg2.addColorStop(1,   `rgba(20, 50, 110, ${(alphaFloor * 0.40).toFixed(3)})`);
    ctx.fillStyle = fg2;
    ctx.beginPath();
    for (let i = 0; i < pts.length; i++) {
      if (i === 0) ctx.moveTo(pts[i][0], pts[i][1]); else ctx.lineTo(pts[i][0], pts[i][1]);
    }
    ctx.closePath();
    ctx.fill();
    ctx.globalAlpha = 0.22 * severity;
    ctx.strokeStyle = 'rgba(140, 200, 255, 1)';
    ctx.lineWidth   = Math.max(1, rH * 0.035);
    for (let ri = 1; ri <= 3; ri++) {
      const ry2  = minY + (ri / 4) * rH;
      const rOff = t === 0 ? 0 : Math.sin(t * 1.2 + ri) * rW * 0.04;
      ctx.beginPath();
      ctx.moveTo(minX, ry2 + rOff); ctx.lineTo(maxX, ry2 - rOff); ctx.stroke();
    }

  } else if (visual === 'snow-band') {
    // White horizontal stripes across the region
    const stripeCount = Math.max(3, Math.round(rH / 12));
    ctx.lineWidth = Math.max(1, rH / stripeCount * 0.35);
    ctx.lineCap   = 'butt';
    for (let si = 0; si < stripeCount; si++) {
      const sy2 = minY + (si + 0.5) / stripeCount * rH;
      ctx.globalAlpha = alphaFloor * (si % 2 === 0 ? 0.80 : 0.40);
      ctx.strokeStyle  = `rgba(230, 240, 255, ${alphaFloor.toFixed(3)})`;
      ctx.beginPath(); ctx.moveTo(minX, sy2); ctx.lineTo(maxX, sy2); ctx.stroke();
    }

  } else if (visual === 'dust-front') {
    // Tan/brown gradient wall advancing across the region
    const advance = t === 0 ? 0.5 : ((t * 0.04 * severity) % 1.0);
    const frontX  = minX + advance * rW;
    const df      = ctx.createLinearGradient(frontX - rW * 0.3, 0, frontX + rW * 0.05, 0);
    df.addColorStop(0,   `rgba(180, 130, 70, 0)`);
    df.addColorStop(0.6, `rgba(180, 130, 70, ${(alphaFloor * 0.65).toFixed(3)})`);
    df.addColorStop(1,   `rgba(150, 100, 50, ${(alphaFloor * 0.45).toFixed(3)})`);
    ctx.fillStyle = df;
    ctx.fillRect(minX, minY, rW, rH);

  } else if (visual === 'megafauna-marker') {
    // Pawprint silhouette — recognizable animal presence signal
    ctx.globalAlpha = alphaFloor * 0.65;
    ctx.fillStyle   = `rgba(20, 14, 8, 0.85)`;
    const pawR = Math.min(rW, rH) * 0.22;
    ctx.beginPath(); ctx.ellipse(cx, cy + pawR * 0.3, pawR, pawR * 0.75, 0, 0, Math.PI * 2); ctx.fill();
    const toeR = pawR * 0.32;
    for (const [tx, ty] of [
      [cx - pawR * 0.65, cy - pawR * 0.55] as [number, number],
      [cx - pawR * 0.20, cy - pawR * 0.85] as [number, number],
      [cx + pawR * 0.25, cy - pawR * 0.85] as [number, number],
      [cx + pawR * 0.70, cy - pawR * 0.55] as [number, number],
    ]) {
      ctx.beginPath(); ctx.arc(tx, ty, toeR, 0, Math.PI * 2); ctx.fill();
    }

  } else if (visual === 'impact-scar') {
    // Circular crater ring with ejecta halo and dark interior
    const craterR = Math.min(rW, rH) * 0.40;
    // Ejecta spray
    ctx.globalAlpha = alphaFloor;
    ctx.strokeStyle = `rgba(160, 140, 110, ${(alphaFloor * 0.45).toFixed(3)})`;
    ctx.lineWidth   = Math.max(2, craterR * 0.20);
    ctx.beginPath(); ctx.arc(cx, cy, craterR * 1.18, 0, Math.PI * 2); ctx.stroke();
    // Rim
    ctx.strokeStyle = `rgba(80, 65, 45, 1)`;
    ctx.lineWidth   = Math.max(2, craterR * 0.10);
    ctx.beginPath(); ctx.arc(cx, cy, craterR, 0, Math.PI * 2); ctx.stroke();
    // Dark interior
    const ifill = ctx.createRadialGradient(cx, cy, 0, cx, cy, craterR * 0.88);
    ifill.addColorStop(0, `rgba(25, 20, 15, ${(alphaFloor * 0.55).toFixed(3)})`);
    ifill.addColorStop(1, `rgba(25, 20, 15, 0)`);
    ctx.fillStyle = ifill;
    ctx.beginPath(); ctx.arc(cx, cy, craterR * 0.88, 0, Math.PI * 2); ctx.fill();

  } else {
    // Generic fallback: tinted polygon — unknown visual kinds degrade gracefully
    ctx.globalAlpha = alphaFloor * 0.40;
    ctx.fillStyle   = 'rgba(200, 60, 60, 1)';
    ctx.beginPath();
    for (let i = 0; i < pts.length; i++) {
      if (i === 0) ctx.moveTo(pts[i][0], pts[i][1]); else ctx.lineTo(pts[i][0], pts[i][1]);
    }
    ctx.closePath();
    ctx.fill();
  }

  // Suppress unused-param warnings (w, h referenced for sizing in calling code)
  void w; void h;
  ctx.restore();
}

// ---------------------------------------------------------------------------
// drawScatterInstances — renders model.layers.features.scatters.
//
// Two-pass design to avoid composite bleed:
//   Pass 1 (source-over): flora tufts + rock blobs — all non-glitter kinds.
//   Pass 2 (lighter):     glitter-spark — additive halo driven by inst.glow.
// After pass 2, ctx.restore() resets globalCompositeOperation to source-over.
//
// Kind classification:
//   rock / boulder / stone / gravel / pebble / regolith / rubble
//                       → flattened ellipse blob + shadow
//   glitter-spark       → 4-point star + additive radial glow halo
//   everything else     → vegetation tuft (3–4 upward-curving strokes)
//   truly unknown       → generic tinted dot (never throws)
// ---------------------------------------------------------------------------
function drawScatterInstances(
  ctx: CanvasRenderingContext2D,
  t: number,
  cache: VistaCache,
  dc: DayCycle
): void {
  if (cache.scatterScreens.length === 0) return;

  const brightK  = 0.55 + dc.bright * 0.45;
  const lighting = cache.model.lighting;

  // Directional shadow offset: shadow falls opposite the key-light azimuth.
  // keyDir[0] is the sun azimuth in screen degrees (0° = screen-right).
  // cos(keyAzDeg) gives the horizontal component; negate to get shadow direction.
  const shadowAzRad = lighting.keyDir[0] * Math.PI / 180;
  const shadowOffX  = -Math.cos(shadowAzRad);  // unit vector, scaled per instance below
  const shadowKeyK  = Math.max(0.3, Math.min(1, lighting.keyIntensity));

  const isRock = (kind: string): boolean => {
    const k = kind.toLowerCase();
    return k.includes('rock') || k.includes('boulder') || k.includes('stone') ||
           k.includes('gravel') || k.includes('pebble') || k.includes('regolith') ||
           k.includes('rubble');
  };

  // ---- Pass 1: source-over (flora tufts + rock blobs) ----
  ctx.save();
  ctx.globalCompositeOperation = 'source-over';

  for (const group of cache.scatterScreens) {
    if (group.kind === 'glitter-spark') continue;

    const rock = isRock(group.kind);
    ctx.globalAlpha = brightK * 0.75;

    for (const inst of group.instances) {
      const { sx, sy, sizePx, tint } = inst;
      const [tr, tg, tb] = tint;

      if (rock) {
        // Rounded blob — slightly flattened to sit on the ground
        ctx.fillStyle = `rgb(${tr}, ${tg}, ${tb})`;
        ctx.beginPath();
        ctx.ellipse(sx, sy, sizePx, sizePx * 0.60, 0, 0, Math.PI * 2);
        ctx.fill();
        // Directional cast shadow: offset opposite the key-light azimuth.
        // shadowOffX is the unit horizontal direction; scale by sizePx × 0.40.
        const castX = sx + shadowOffX * sizePx * 0.40;
        const castY = sy + sizePx * 0.28;
        ctx.globalAlpha = brightK * shadowKeyK * 0.22;
        ctx.fillStyle   = 'rgba(0, 0, 0, 0.5)';
        ctx.beginPath();
        ctx.ellipse(castX, castY, sizePx * 0.85, sizePx * 0.22, 0, 0, Math.PI * 2);
        ctx.fill();
        ctx.globalAlpha = brightK * 0.75;

      } else {
        // Vegetation tuft: 3–4 blades curving upward, spread by sizePx.
        // Blade count is deterministic from position (no per-frame RNG).
        const blades = 3 + (Math.round(sx + sy) & 1);
        ctx.strokeStyle = `rgb(${tr}, ${tg}, ${tb})`;
        ctx.lineWidth   = Math.max(0.8, sizePx * 0.22);
        ctx.lineCap     = 'round';
        for (let bi = 0; bi < blades; bi++) {
          const spread = (bi / (blades - 1) - 0.5) * sizePx * 1.6;
          const lean   = spread * 0.28;  // blades lean outward
          ctx.beginPath();
          ctx.moveTo(sx + spread * 0.3, sy);
          ctx.quadraticCurveTo(
            sx + spread * 0.5 + lean, sy - sizePx * 0.6,
            sx + spread + lean,       sy - sizePx
          );
          ctx.stroke();
        }
      }
    }
  }

  ctx.restore();

  // ---- Pass 2: additive composite for glitter-spark ----
  // Check first so we don't enter the save/restore for worlds with no glitter.
  let hasGlitter = false;
  for (const group of cache.scatterScreens) {
    if (group.kind === 'glitter-spark') { hasGlitter = true; break; }
  }
  if (!hasGlitter) return;

  ctx.save();
  ctx.globalCompositeOperation = 'lighter';

  for (const group of cache.scatterScreens) {
    if (group.kind !== 'glitter-spark') continue;

    for (const inst of group.instances) {
      const { sx, sy, sizePx, tint, glow } = inst;
      const [tr, tg, tb] = tint;
      const glowStr = glow * brightK;

      // Radial glow halo — intensity driven by the `glow` field
      if (glowStr > 0.02) {
        const haloR = sizePx * (2.0 + glow * 2.5);
        const halo  = ctx.createRadialGradient(sx, sy, 0, sx, sy, haloR);
        halo.addColorStop(0, `rgba(${tr}, ${tg}, ${tb}, ${(glowStr * 0.55).toFixed(3)})`);
        halo.addColorStop(1, `rgba(${tr}, ${tg}, ${tb}, 0)`);
        ctx.globalAlpha = 1;
        ctx.fillStyle   = halo;
        ctx.fillRect(sx - haloR, sy - haloR, haloR * 2, haloR * 2);
      }

      // 4-point star: two full arms + two shorter diagonal arms
      const starLen = sizePx * (1.0 + glow * 0.5);
      const starR   = Math.min(255, tr + 60);
      const starG   = Math.min(255, tg + 60);
      const starB   = Math.min(255, tb + 60);
      ctx.strokeStyle = `rgb(${starR}, ${starG}, ${starB})`;
      ctx.lineCap     = 'round';

      ctx.lineWidth   = Math.max(0.8, sizePx * 0.30);
      ctx.globalAlpha = Math.min(1, glowStr * 1.2 + 0.4);
      ctx.beginPath(); ctx.moveTo(sx - starLen, sy); ctx.lineTo(sx + starLen, sy); ctx.stroke();
      ctx.beginPath(); ctx.moveTo(sx, sy - starLen); ctx.lineTo(sx, sy + starLen); ctx.stroke();

      const diagLen   = starLen * 0.55;
      ctx.lineWidth   = Math.max(0.6, sizePx * 0.18);
      ctx.globalAlpha = Math.min(1, glowStr * 0.8 + 0.25);
      ctx.beginPath(); ctx.moveTo(sx - diagLen, sy - diagLen); ctx.lineTo(sx + diagLen, sy + diagLen); ctx.stroke();
      ctx.beginPath(); ctx.moveTo(sx + diagLen, sy - diagLen); ctx.lineTo(sx - diagLen, sy + diagLen); ctx.stroke();
    }
  }

  // ctx.restore() resets globalCompositeOperation to 'source-over' — no bleed into
  // the haze / particles / night-dim layers that follow.
  ctx.restore();
}

// ---------------------------------------------------------------------------
// WO-V2-CLOUDS-RAYS helpers
// ---------------------------------------------------------------------------

// drawCumulusCloud — billowy cloud with lit-top lobes and a dark-base underside.
// Each lobe is a radial gradient centred slightly above the cloud-centre Y so the
// top is bright (sky-lit) and the underside is shaded.
function drawCumulusCloud(
  ctx: CanvasRenderingContext2D,
  cx: number, cy: number,
  cloudW: number, cloudH: number,
  alpha: number,
  tr: number, tg: number, tb: number,
  lobeCount: number,
  lobeOffsets: number[],
): void {
  const lobeR = Math.max(8, cloudH * 0.82);
  const litR = Math.min(255, tr + 58);
  const litG = Math.min(255, tg + 58);
  const litB = Math.min(255, tb + 52);
  const shR  = Math.max(0, tr - 38);
  const shG  = Math.max(0, tg - 38);
  const shB  = Math.max(0, tb - 32);

  // Dark base underside — a single wide linear gradient across the cloud belly
  const baseTop = cy;
  const baseBtm = cy + lobeR * 0.55;
  const sg = ctx.createLinearGradient(cx, baseTop, cx, baseBtm);
  sg.addColorStop(0, `rgba(${shR}, ${shG}, ${shB}, 0)`);
  sg.addColorStop(1, `rgba(${shR}, ${shG}, ${shB}, ${(alpha * 0.48).toFixed(3)})`);
  ctx.fillStyle = sg;
  ctx.fillRect(cx - cloudW * 0.62, baseTop, cloudW * 1.24, lobeR * 0.55);

  // Lit-top lobe per section — bright centre fading outward
  for (let lb = 0; lb < lobeCount; lb++) {
    const lx = cx - cloudW * 0.42 + (lobeOffsets[lb] ?? lb / lobeCount) * cloudW * 0.84;
    const ly = cy - lobeR * 0.08;
    const lg = ctx.createRadialGradient(lx, ly - lobeR * 0.20, 0, lx, ly, lobeR);
    lg.addColorStop(0,    `rgba(${litR}, ${litG}, ${litB}, ${(alpha * 1.00).toFixed(3)})`);
    lg.addColorStop(0.45, `rgba(${tr}, ${tg}, ${tb},  ${(alpha * 0.68).toFixed(3)})`);
    lg.addColorStop(0.80, `rgba(${tr}, ${tg}, ${tb},  ${(alpha * 0.20).toFixed(3)})`);
    lg.addColorStop(1,    `rgba(${tr}, ${tg}, ${tb},  0)`);
    ctx.fillStyle = lg;
    ctx.beginPath();
    ctx.arc(lx, ly, lobeR, 0, Math.PI * 2);
    ctx.fill();
  }
}

// drawCirrusCloud — thin, wispy horizontal streak with feathered ends.
// Three sub-strokes at slightly offset Y positions give a layered wispy look.
function drawCirrusCloud(
  ctx: CanvasRenderingContext2D,
  cx: number, cy: number,
  cloudW: number, cloudH: number,
  alpha: number,
  tr: number, tg: number, tb: number,
): void {
  const halfH = Math.max(1.5, cloudH * 0.55);
  const alphas   = [0.88, 1.00, 0.62];
  const widthMul = [0.72, 1.00, 0.56];
  const yOffsets = [-halfH * 0.55, 0, halfH * 0.50];

  for (let si = 0; si < 3; si++) {
    const sy  = cy + yOffsets[si];
    const hw  = cloudW * 0.5 * widthMul[si];
    const hh  = Math.max(1, halfH * (1.0 - si * 0.22));
    const a   = alpha * alphas[si];
    const hg  = ctx.createLinearGradient(cx - hw, sy, cx + hw, sy);
    hg.addColorStop(0,    `rgba(${tr}, ${tg}, ${tb}, 0)`);
    hg.addColorStop(0.12, `rgba(${tr}, ${tg}, ${tb}, ${a.toFixed(3)})`);
    hg.addColorStop(0.88, `rgba(${tr}, ${tg}, ${tb}, ${a.toFixed(3)})`);
    hg.addColorStop(1,    `rgba(${tr}, ${tg}, ${tb}, 0)`);
    ctx.fillStyle = hg;
    ctx.fillRect(cx - hw, sy - hh, hw * 2, hh * 2);
  }
}

// drawAshCloud — turbulent irregular mass used for volcanic ash and desert dust.
// Multiple overlapping blobs at deterministic offsets (no Math.random in draw path).
// A subtle t-driven scale pulse gives a slow churning turbulence feel.
function drawAshCloud(
  ctx: CanvasRenderingContext2D,
  cx: number, cy: number,
  cloudW: number, cloudH: number,
  alpha: number,
  tr: number, tg: number, tb: number,
  t: number,
  layer: number,
): void {
  const blobCount = 3 + layer;
  const baseR     = Math.max(6, cloudH * 0.68);
  for (let bi = 0; bi < blobCount; bi++) {
    // Deterministic offsets via cheap prime-step hash — stable across frames
    const bx = cx + (((bi * 127 + 13) % 100) / 100 - 0.50) * cloudW * 0.80;
    const by = cy + (((bi *  53 +  7) % 100) / 100 - 0.50) * cloudH * 0.60;
    const br = baseR * (0.48 + ((bi * 31 + 11) % 100) / 100 * 0.72);
    // Slow turbulent pulse — deterministic via bi phase offset, never Math.random
    const pulse = t === 0 ? 1 : 1 + 0.07 * Math.sin(t * 0.38 + bi * 1.27);
    const rg = ctx.createRadialGradient(bx, by, 0, bx, by, br * pulse);
    rg.addColorStop(0,    `rgba(${tr}, ${tg}, ${tb}, ${(alpha * 0.82).toFixed(3)})`);
    rg.addColorStop(0.55, `rgba(${tr}, ${tg}, ${tb}, ${(alpha * 0.44).toFixed(3)})`);
    rg.addColorStop(1,    `rgba(${tr}, ${tg}, ${tb}, 0)`);
    ctx.fillStyle = rg;
    ctx.fillRect(bx - br * pulse, by - br * pulse, br * pulse * 2, br * pulse * 2);
  }
}

// drawNightSky — nebula wash, galactic band, and shooting stars.
// All effects are gated on starVisibility so they fade out cleanly by day.
// Night/twilight check: caller passes starVisibility > 0 threshold.
function drawNightSky(
  ctx: CanvasRenderingContext2D,
  w: number,
  horizonY: number,
  t: number,
  cache: VistaCache,
  starVisibility: number,
): void {
  const nebula = cache.model.layers.celestial.nebula;

  // Nebula wash — two offset radial lobes for a diffuse tinted glow in the night sky.
  // Uses 'screen' composite so it brightens rather than painting over stars.
  if (nebula && starVisibility > 0.25) {
    const hue = nebula.hue;
    const den = nebula.density * starVisibility;
    ctx.save();
    ctx.globalCompositeOperation = 'screen';
    // Primary lobe
    const r1 = Math.max(w, horizonY) * 0.82;
    const nx1 = w * 0.35;
    const ny1 = horizonY * 0.28;
    const nG1 = ctx.createRadialGradient(nx1, ny1, 0, nx1, ny1, r1);
    nG1.addColorStop(0,   `hsla(${hue}, 62%, 24%, ${(den * 0.30).toFixed(3)})`);
    nG1.addColorStop(0.5, `hsla(${hue}, 48%, 16%, ${(den * 0.14).toFixed(3)})`);
    nG1.addColorStop(1,   `hsla(${hue}, 32%, 10%, 0)`);
    ctx.fillStyle = nG1;
    ctx.fillRect(0, 0, w, horizonY);
    // Secondary lobe — shifted hue for depth
    const r2  = r1 * 0.62;
    const nx2 = w * 0.68;
    const ny2 = horizonY * 0.44;
    const nG2 = ctx.createRadialGradient(nx2, ny2, 0, nx2, ny2, r2);
    nG2.addColorStop(0, `hsla(${(hue + 28) % 360}, 56%, 20%, ${(den * 0.20).toFixed(3)})`);
    nG2.addColorStop(1, `hsla(${(hue + 28) % 360}, 40%, 10%, 0)`);
    ctx.fillStyle = nG2;
    ctx.fillRect(0, 0, w, horizonY);
    ctx.restore();
  }

  // Galactic / milky-way band — seeded diagonal strip of concentrated star haze.
  // The band origin and angle are baked in buildVistaCache so they're stable per seed.
  if (cache.galacticBand && starVisibility > 0.40) {
    const gb  = cache.galacticBand;
    const gba = 0.10 * starVisibility;
    ctx.save();
    ctx.globalCompositeOperation = 'screen';
    ctx.translate(gb.cx, gb.cy);
    ctx.rotate(gb.angle);
    const bGrad = ctx.createLinearGradient(-gb.width, 0, gb.width, 0);
    bGrad.addColorStop(0,    `rgba(200, 205, 240, 0)`);
    bGrad.addColorStop(0.28, `rgba(200, 205, 240, ${(gba * 0.80).toFixed(3)})`);
    bGrad.addColorStop(0.50, `rgba(210, 215, 255, ${gba.toFixed(3)})`);
    bGrad.addColorStop(0.72, `rgba(200, 205, 240, ${(gba * 0.80).toFixed(3)})`);
    bGrad.addColorStop(1,    `rgba(200, 205, 240, 0)`);
    ctx.fillStyle = bGrad;
    // Extend the band far enough in the rotated direction to cross the full sky
    ctx.fillRect(-gb.width, -horizonY * 2, gb.width * 2, horizonY * 4);
    ctx.restore();
  }

  // Shooting stars — brief streaks cycling in and out; deeply gated on starVisibility.
  // Guard t > 0: the proof harness captures at t=0 (daytime) so this never fires there.
  if (starVisibility > 0.65 && t > 0 && cache.shootingStarSeeds.length > 0) {
    ctx.save();
    ctx.globalCompositeOperation = 'lighter';
    for (const ss of cache.shootingStarSeeds) {
      // Each star fires once per ~17–45 s cycle; brief bright peak near sin=1.
      const cycleT    = (t * ss.speed * 0.35 + ss.phase) % (Math.PI * 2);
      const peakAlpha = Math.max(0, Math.sin(cycleT) - 0.78) * (1 / 0.22);
      if (peakAlpha < 0.01) continue;
      const finalAlpha = peakAlpha * starVisibility;
      const sg = ctx.createLinearGradient(ss.x0, ss.y0, ss.x1, ss.y1);
      sg.addColorStop(0,    `rgba(255, 255, 255, ${(finalAlpha * 0.95).toFixed(3)})`);
      sg.addColorStop(0.35, `rgba(220, 235, 255, ${(finalAlpha * 0.50).toFixed(3)})`);
      sg.addColorStop(1,    `rgba(200, 220, 255, 0)`);
      ctx.strokeStyle = sg;
      ctx.lineWidth   = 1.8;
      ctx.lineCap     = 'round';
      ctx.globalAlpha = 1;
      ctx.beginPath();
      ctx.moveTo(ss.x0, ss.y0);
      ctx.lineTo(ss.x1, ss.y1);
      ctx.stroke();
    }
    ctx.restore();
  }
}

// drawGodRays — stylized wedge rays fanning from the sun through cloud gaps.
// Rays are drawn BEFORE the cloud layer so clouds occlude them naturally — rays
// appear in the gaps between cloud masses.  Uses 'lighter' composite for
// additive glow.  Intensity peaks at low sun (golden hour).
function drawGodRays(
  ctx: CanvasRenderingContext2D,
  w: number,
  horizonY: number,
  sunX: number,
  sunY: number,
  cache: VistaCache,
  dc: DayCycle,
): void {
  // Only draw when atmosphere + clouds are present and sun is up
  if (!dc.sunUp || !cache.hasAtmosphere || cache.cloudKind === 'none' || cache.clouds.length === 0) return;
  // Rays are strongest near the horizon (sunAlt ≈ 0) and fade at zenith
  const horizonPeak = Math.max(0, 1.0 - dc.sunAlt * 2.4);
  const bloom       = cache.model.lighting.bloom;
  const rayAlpha    = bloom * dc.bright * horizonPeak * 0.28;
  if (rayAlpha < 0.005) return;

  const { sc } = cache;
  ctx.save();
  ctx.globalCompositeOperation = 'lighter';
  for (const ray of cache.godRaySeeds) {
    const baseDist = (horizonY - sunY) * ray.lenFrac;
    if (baseDist <= 0) continue;
    const aL   = ray.angle - ray.spread;
    const aR   = ray.angle + ray.spread;
    const x1   = sunX + Math.cos(aL) * baseDist;
    const y1   = sunY + Math.sin(aL) * baseDist;
    const x2   = sunX + Math.cos(aR) * baseDist;
    const y2   = sunY + Math.sin(aR) * baseDist;
    const midX = (x1 + x2) * 0.5;
    const midY = (y1 + y2) * 0.5;
    const rg   = ctx.createLinearGradient(sunX, sunY, midX, midY);
    const a0   = rayAlpha * ray.alphaMul;
    rg.addColorStop(0,   `rgba(${sc.r}, ${sc.g}, ${sc.b}, ${a0.toFixed(3)})`);
    rg.addColorStop(0.6, `rgba(${sc.r}, ${sc.g}, ${sc.b}, ${(a0 * 0.28).toFixed(3)})`);
    rg.addColorStop(1,   `rgba(${sc.r}, ${sc.g}, ${sc.b}, 0)`);
    ctx.fillStyle = rg;
    ctx.beginPath();
    ctx.moveTo(sunX, sunY);
    ctx.lineTo(x1, y1);
    ctx.lineTo(x2, y2);
    ctx.closePath();
    ctx.fill();
  }
  ctx.restore();
}

// ---------------------------------------------------------------------------
// drawScene — the per-frame compositor
//
// Ported from drawLandedScene (SolarSystemViewscreen.tsx L3477), adapted to
// read from VistaCache / VistaModel instead of LandedCache / live game state.
//
// VACUUM PATH (atmosphere.present === false):
//   • Sky clamped to near-black regardless of bright.
//   • starVisibility = 1.0 always.
//   • No haze, no clouds, no precipitation drawn.
//   • Hard horizon (no scatter bands).
// ---------------------------------------------------------------------------
function drawScene(
  ctx: CanvasRenderingContext2D,
  w: number,
  h: number,
  t: number,
  model: VistaModel,
  cache: VistaCache
): void {
  const { horizonY, hasAtmosphere, sc } = cache;
  const pal = model.palette;

  // --- LIVE DAY/NIGHT CYCLE ---
  const dc = dayCycleAt(t, cache.dayPhaseOffset);

  // 1) Sky gradient — consume model.layers.sky.gradient stops + day-cycle brightness.
  //    The pipeline emits 2–3 stops (zenith, optional scatter-band, horizon) already
  //    colour-matched to the palette; we apply the same day-cycle brightness curve
  //    (0.30 dim → 1.0 full) and warm sunrise/sunset tint per stop.
  //    scatterBands: thin atmospheric colour strips just above the horizon (WO-V2-CLOUDS-RAYS).
  {
    let g: CanvasGradient;
    if (!hasAtmosphere) {
      // VACUUM: near-black sky regardless of sun position
      g = ctx.createLinearGradient(0, 0, 0, horizonY * 1.15);
      g.addColorStop(0,   'rgb(2, 2, 6)');
      g.addColorStop(0.6, 'rgb(4, 4, 12)');
      g.addColorStop(1,   'rgb(8, 8, 20)');
    } else {
      const b    = dc.bright;
      const warm = dc.warm;
      const skyGrad = model.layers.sky.gradient;
      g = ctx.createLinearGradient(0, 0, 0, horizonY * 1.15);
      if (skyGrad.length >= 2) {
        // Drive each stop through the day-cycle brightness curve.
        // Stops near 1.0 (horizon) get more warm sunrise/sunset tint than the zenith.
        for (const stop of skyGrad) {
          const [cr, cg, cb] = stop.color;
          const wt  = stop.stop;   // warmth weight increases toward horizon
          const r   = Math.round(Math.min(255, cr * (0.30 + b * 0.70) + warm * 42 * wt));
          const cg2 = Math.round(Math.min(255, cg * (0.30 + b * 0.70) + warm * 16 * wt));
          const cb2 = Math.round(Math.min(255, cb * (0.30 + b * 0.70)));
          g.addColorStop(stop.stop, `rgb(${r}, ${cg2}, ${cb2})`);
        }
      } else {
        // Fallback: manual two-stop from palette (should never reach here in practice)
        const topBase = pal.skyTop;
        const horBase = pal.skyHorizon;
        const topR = Math.round(Math.min(255, topBase[0] * (0.30 + b * 0.70) + warm * 15));
        const topG = Math.round(Math.min(255, topBase[1] * (0.30 + b * 0.70) + warm *  5));
        const topB = Math.round(Math.min(255, topBase[2] * (0.30 + b * 0.70)));
        const horR = Math.round(Math.min(255, horBase[0] * (0.40 + b * 0.60) + warm * 40));
        const horG = Math.round(Math.min(255, horBase[1] * (0.40 + b * 0.60) + warm * 18));
        const horB = Math.round(Math.min(255, horBase[2] * (0.40 + b * 0.60)));
        g.addColorStop(0,   `rgb(${topR}, ${topG}, ${topB})`);
        g.addColorStop(0.6, `rgb(${Math.round((topR+horR)/2)}, ${Math.round((topG+horG)/2)}, ${Math.round((topB+horB)/2)})`);
        g.addColorStop(1,   `rgb(${horR}, ${horG}, ${horB})`);
      }
    }
    ctx.fillStyle = g;
    ctx.fillRect(0, 0, w, h);

    // Scatter bands — thin atmospheric colour strips above the horizon.
    // Drawn with 'screen' composite so they glow without washing out the gradient.
    if (hasAtmosphere && dc.bright > 0.08) {
      const scatterBands = model.layers.sky.scatterBands;
      if (scatterBands.length > 0) {
        ctx.save();
        ctx.globalCompositeOperation = 'screen';
        for (const band of scatterBands) {
          const bandCY = band.y * horizonY;
          const bandHH = band.width * horizonY * 0.5;
          const [br, bg2, bb] = band.color;
          const bAlpha = dc.bright * 0.16;
          const bGrad  = ctx.createLinearGradient(0, bandCY - bandHH, 0, bandCY + bandHH);
          bGrad.addColorStop(0,   `rgba(${br}, ${bg2}, ${bb}, 0)`);
          bGrad.addColorStop(0.5, `rgba(${br}, ${bg2}, ${bb}, ${bAlpha.toFixed(3)})`);
          bGrad.addColorStop(1,   `rgba(${br}, ${bg2}, ${bb}, 0)`);
          ctx.fillStyle = bGrad;
          ctx.fillRect(0, bandCY - bandHH, w, bandHH * 2);
        }
        ctx.restore();
      }
    }
  }

  // 1a) Sunrise/sunset atmospheric band (atmospheric worlds only)
  if (hasAtmosphere && dc.warm > 0.04) {
    const bandH = horizonY * (0.22 + dc.warm * 0.28);
    const warmAlpha = dc.warm * 0.52;
    const wR = Math.min(255, Math.round(255 * 0.96 + sc.r * 0.04));
    const wG = Math.min(255, Math.round(100 + sc.g * 0.18));
    const wB = Math.min(255, Math.round(20 + sc.b * 0.22));
    const sunriseBand = ctx.createLinearGradient(0, horizonY - bandH, 0, horizonY);
    sunriseBand.addColorStop(0,   `rgba(${wR}, ${wG}, ${wB}, 0)`);
    sunriseBand.addColorStop(0.5, `rgba(${wR}, ${Math.max(0, wG - 30)}, ${Math.max(0, wB - 10)}, ${(warmAlpha * 0.55).toFixed(3)})`);
    sunriseBand.addColorStop(1,   `rgba(${wR}, ${Math.max(0, wG - 60)}, 10, ${warmAlpha.toFixed(3)})`);
    ctx.save();
    ctx.globalCompositeOperation = 'lighter';
    ctx.fillStyle = sunriseBand;
    ctx.fillRect(0, horizonY - bandH, w, bandH + 2);
    ctx.restore();
  }

  // 1b) Weather sky overlay
  if (hasAtmosphere && cache.skyDarken > 0) {
    drawWeatherSky(ctx, w, horizonY, cache.skyDarken, cache.hazeColor);
  }

  // 2) Starfield — layout cached; twinkle per frame
  //    VACUUM: always full brightness.
  //    ATMOSPHERIC: fades out by day (starVisibility driven by sun altitude).
  const starVisibility = hasAtmosphere
    ? Math.max(0, Math.min(1, 1 - (dc.sunAlt + 0.15) * 1.3))
    : 1.0;
  if (cache.stars.length > 0 && starVisibility > 0.02) {
    ctx.save();
    ctx.fillStyle = '#dfe7f5';
    for (let i = 0; i < cache.stars.length; i++) {
      const s = cache.stars[i];
      const tw = t === 0 ? 0.75 : 0.5 + 0.5 * Math.sin(t * s.twSpeed + s.twPhase);
      ctx.globalAlpha = s.baseAlpha * tw * starVisibility;
      ctx.beginPath();
      ctx.arc(s.x, s.y, s.size, 0, Math.PI * 2);
      ctx.fill();
    }
    ctx.restore();
  }

  // 2c) Night sky — nebula wash, galactic band, shooting stars (WO-V2-CLOUDS-RAYS).
  //     Gated on starVisibility so effects fade out cleanly well before sunrise.
  if (hasAtmosphere && starVisibility > 0.25) {
    drawNightSky(ctx, w, horizonY, t, cache, starVisibility);
  }

  // 2d) God-rays — wedge fan from the sun, drawn BEFORE clouds so cloud masses
  //     occlude them and the open gaps show through (WO-V2-CLOUDS-RAYS).
  //     Sun position duplicated here (same formula as step 3) so we can draw rays
  //     ahead of the sun disc.
  if (dc.sunUp && hasAtmosphere && cache.godRaySeeds.length > 0) {
    const rayPhase  = dc.dayPhase;
    const rayXu     = cache.sunAzDir > 0 ? rayPhase : 1 - rayPhase;
    const raySunX   = w * (0.06 + rayXu * 0.88);
    const raySunY   = horizonY - Math.max(-0.05, dc.sunAlt) * horizonY * SKY_Y_SCALE;
    drawGodRays(ctx, w, horizonY, raySunX, raySunY, cache, dc);
  }

  // 2b) Clouds — kind-distinct parallax layers (WO-V2-CLOUDS-RAYS).
  //     Replaces the old single-style radial-gradient blob.
  //     cumulus: lit-top / dark-base lobes  |  cirrus: thin feathered streaks
  //     ash:     turbulent irregular masses  |  overcast: wide low-alpha deck
  if (hasAtmosphere && cache.clouds.length > 0) {
    const [cloudTR, cloudTG, cloudTB] = cache.model.layers.atmosphere.clouds.color;
    const span = w * 1.6;
    ctx.save();
    ctx.globalCompositeOperation = 'source-over';
    for (const c of cache.clouds) {
      const cx = (((c.x + t * c.speed) % span) + span) % span - w * 0.3;
      const ch = h * c.hFrac;
      const cy = Math.min(horizonY * c.yFrac * 2, horizonY - ch - 4);
      if (c.kind === 'cumulus') {
        drawCumulusCloud(ctx, cx, cy, c.w, ch, c.alpha,
          cloudTR, cloudTG, cloudTB, c.lobeCount, c.lobeOffsets);
      } else if (c.kind === 'cirrus') {
        drawCirrusCloud(ctx, cx, cy, c.w, ch, c.alpha, cloudTR, cloudTG, cloudTB);
      } else if (c.kind === 'ash') {
        drawAshCloud(ctx, cx, cy, c.w, ch, c.alpha,
          cloudTR, cloudTG, cloudTB, t, c.layer);
      } else {
        // Overcast deck — full-width band, linear gradient top→bottom
        const og = ctx.createLinearGradient(0, cy, 0, cy + ch);
        og.addColorStop(0, `rgba(${cloudTR}, ${cloudTG}, ${cloudTB}, ${(c.alpha * 0.82).toFixed(3)})`);
        og.addColorStop(1, `rgba(${cloudTR}, ${cloudTG}, ${cloudTB}, ${(c.alpha * 0.28).toFixed(3)})`);
        ctx.fillStyle = og;
        ctx.fillRect(0, cy, w, ch);
      }
    }
    ctx.restore();
  }

  // 3) THE SUN — arcs east→west on the day cycle.
  //    Uses SKY_Y_SCALE (same as skyProjection) so the sun arcs through the same dome.
  const { sunR, coronaR } = cache;
  const sunPhase = dc.dayPhase;
  const sunXu = cache.sunAzDir > 0 ? sunPhase : 1 - sunPhase;
  const sunX = w * (0.06 + sunXu * 0.88);
  // Sun y uses SKY_Y_SCALE — single-sourced with skyProjection
  const sunY = horizonY - Math.max(-0.05, dc.sunAlt) * horizonY * SKY_Y_SCALE;
  // TRUE (unclamped) position used for moon lighting even when below horizon
  const sunWorldX = sunX;
  const sunWorldY = horizonY - dc.sunAlt * horizonY * SKY_Y_SCALE;

  if (dc.sunUp) {
    ctx.save();
    ctx.globalCompositeOperation = 'lighter';
    const horizonFade = Math.max(0.25, Math.min(1, dc.sunAlt * 4));
    // VACUUM: no weather dim on the sun disc
    const sunDim = (hasAtmosphere ? (1 - cache.skyDarken * 0.8) : 1) * horizonFade;
    const breathe = t === 0 ? 1 : 0.92 + 0.08 * Math.sin(t * 0.5);
    const coronaGrad = ctx.createRadialGradient(sunX, sunY, 0, sunX, sunY, coronaR);
    coronaGrad.addColorStop(0, `rgba(${sc.r}, ${sc.g}, ${sc.b}, ${(0.35).toFixed(3)})`);
    coronaGrad.addColorStop(0.35, `rgba(${sc.r}, ${sc.g}, ${sc.b}, ${(0.12).toFixed(3)})`);
    coronaGrad.addColorStop(1, `rgba(${sc.r}, ${sc.g}, ${sc.b}, 0)`);
    ctx.globalAlpha = breathe * sunDim;
    ctx.fillStyle = coronaGrad;
    ctx.fillRect(sunX - coronaR, sunY - coronaR, coronaR * 2, coronaR * 2);
    const cw = cache.coreWhite;
    const discGrad = ctx.createRadialGradient(sunX, sunY, 0, sunX, sunY, sunR);
    discGrad.addColorStop(0, `rgba(${Math.min(255, sc.r + cw * 0.4)}, ${Math.min(255, sc.g + cw * 0.4)}, ${Math.min(255, sc.b + cw * 0.4)}, 0.98)`);
    discGrad.addColorStop(0.6, `rgba(${sc.r}, ${sc.g}, ${sc.b}, 0.95)`);
    discGrad.addColorStop(1, `rgba(${sc.r}, ${sc.g}, ${sc.b}, 0.5)`);
    ctx.globalAlpha = sunDim;
    ctx.fillStyle = discGrad;
    ctx.beginPath();
    ctx.arc(sunX, sunY, sunR, 0, Math.PI * 2);
    ctx.fill();
    if (cache.hasCompanion) {
      const { c2, c2side, c2r } = cache;
      const c2x = sunX + sunR * 4.5 * c2side;
      const c2y = sunY + sunR * 1.8;
      const cc = ctx.createRadialGradient(c2x, c2y, 0, c2x, c2y, c2r * 4);
      cc.addColorStop(0, `rgba(${c2.r}, ${c2.g}, ${c2.b}, 0.4)`);
      cc.addColorStop(1, `rgba(${c2.r}, ${c2.g}, ${c2.b}, 0)`);
      ctx.fillStyle = cc;
      ctx.fillRect(c2x - c2r * 4, c2y - c2r * 4, c2r * 8, c2r * 8);
      ctx.beginPath();
      ctx.arc(c2x, c2y, c2r, 0, Math.PI * 2);
      ctx.fillStyle = `rgba(${Math.min(255, c2.r + 60)}, ${Math.min(255, c2.g + 60)}, ${Math.min(255, c2.b + 60)}, 0.95)`;
      ctx.fill();
    }
    ctx.restore();
  }

  // 3a) Sibling planets arcing across the sky
  if (cache.skyPlanets.length > 0) {
    drawLandedSkyPlanets(ctx, w, horizonY, t, cache, dc);
  }

  // 3b) Moons
  if (cache.moons.length > 0) {
    drawLandedMoons(ctx, w, horizonY, t, cache, dc, sunWorldX, sunWorldY, dc.sunAlt, sunXu);
  }

  // 4) Horizon glow
  ctx.save();
  ctx.globalCompositeOperation = 'lighter';
  ctx.globalAlpha = Math.max(0.3, dc.bright);
  ctx.fillStyle = cache.glowGrad;
  ctx.fillRect(0, 0, w, h);
  if (dc.sunUp) {
    const shg = ctx.createRadialGradient(sunX, horizonY, 0, sunX, horizonY, Math.max(w, h) * 0.35);
    const sa = 0.22 * Math.max(0.2, dc.bright) * (1 + dc.warm * 0.8);
    shg.addColorStop(0, `rgba(${sc.r}, ${sc.g}, ${sc.b}, ${sa.toFixed(3)})`);
    shg.addColorStop(1, `rgba(${sc.r}, ${sc.g}, ${sc.b}, 0)`);
    ctx.globalAlpha = 1;
    ctx.fillStyle = shg;
    ctx.fillRect(0, 0, w, h);
  }
  ctx.restore();

  // 4b) Water body — depth gradient + wave crests
  if (cache.hasWater && cache.waterBand) {
    const wt = cache.waterTopY;
    const wh = h - wt;
    ctx.save();
    ctx.fillStyle = cache.waterBand;
    ctx.fillRect(0, wt, w, wh);

    const crestRGB = dc.sunUp
      ? `${Math.min(255, sc.r + 30)}, ${Math.min(255, sc.g + 50)}, ${Math.min(255, sc.b + 60)}`
      : '150, 185, 210';

    for (let wi = 0; wi < cache.waves.length; wi++) {
      const wv = cache.waves[wi];
      const f = wv.yFrac;
      const bob = t === 0 ? 0 : Math.sin(t * wv.swellRate * 0.7 + wv.swellPhase) * (4 + f * 18);
      const baseY = wt + f * wh + bob;
      const drift = t === 0 ? 0 : t * wv.speed * 24 * wv.dir;
      const crossDrift = t === 0 ? 0 : t * 5 * wv.dir;
      const chopDrift = t === 0 ? 0 : t * (12 + f * 30) * wv.dir;
      const swellA = t === 0 ? 1 : 1 + 0.4 * Math.sin(t * wv.swellRate + wv.swellPhase);
      const amp = wv.amp * swellA;
      const yAt = (x: number): number =>
        baseY
        + Math.sin((x + drift) / wv.wavelength * Math.PI * 2 + wv.phase) * amp
        + Math.sin((x + crossDrift) / wv.crossWavelength * Math.PI * 2 + wv.swellPhase) * wv.crossAmp
        + (wv.chopAmp > 0 ? Math.sin((x + chopDrift) / wv.chopWavelength * Math.PI * 2 + wv.phase * 2) * wv.chopAmp : 0)
        + wv.tilt * (x - w * 0.5);

      if (wv.fine) {
        ctx.save();
        ctx.globalCompositeOperation = 'lighter';
        ctx.globalAlpha = wv.alpha;
        ctx.strokeStyle = `rgba(${crestRGB}, 1)`;
        ctx.lineWidth = wv.lineW;
        ctx.beginPath();
        for (let x = 0; x <= w; x += 12) { const y = yAt(x); if (x === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y); }
        ctx.stroke();
        ctx.restore();
        continue;
      }
      const slab = 6 + f * 30;
      ctx.save();
      ctx.beginPath();
      ctx.moveTo(0, yAt(0));
      for (let x = 0; x <= w; x += 10) ctx.lineTo(x, yAt(x));
      for (let x = w; x >= 0; x -= 10) ctx.lineTo(x, yAt(x) + slab);
      ctx.closePath();
      const faceGrad = ctx.createLinearGradient(0, baseY - amp, 0, baseY + slab);
      faceGrad.addColorStop(0, `rgba(${Math.round(70 + f * 60)}, ${Math.round(140 + f * 50)}, ${Math.round(175 + f * 40)}, ${(0.30 + f * 0.22).toFixed(3)})`);
      faceGrad.addColorStop(1, 'rgba(6, 26, 48, 0)');
      ctx.fillStyle = faceGrad;
      ctx.fill();
      ctx.restore();
      ctx.save();
      ctx.globalCompositeOperation = 'lighter';
      ctx.globalAlpha = 0.28 + f * 0.45;
      ctx.strokeStyle = `rgba(${crestRGB}, 1)`;
      ctx.lineWidth = wv.lineW;
      ctx.beginPath();
      for (let x = 0; x <= w; x += 8) { const y = yAt(x); if (x === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y); }
      ctx.stroke();
      ctx.restore();
    }

    // Water surface waterline foam — color from model palette.foam
    ctx.save();
    ctx.globalCompositeOperation = 'lighter';
    ctx.strokeStyle = `rgba(${cache.foamColor}, 1)`;
    ctx.lineWidth = 1.4 * Math.min(2.5, cache.foamMul);
    ctx.beginPath();
    for (let x = 0; x <= w; x += 8) {
      const drift = t === 0 ? 0 : t * 18;
      const fy = wt + 1 + Math.sin((x + drift) / 40 * Math.PI * 2) * 1.6 * cache.foamMul;
      if (x === 0) ctx.moveTo(x, fy); else ctx.lineTo(x, fy);
    }
    ctx.globalAlpha = Math.min(0.6, (0.22 + (t === 0 ? 0 : 0.06 * Math.sin(t * 2))) * cache.foamMul);
    ctx.stroke();
    ctx.restore();
    ctx.restore();
  }

  // 5) Terrain layer — mode-branched.
  //    'cloud-deck' → GAS_GIANT floating cloud horizon (no ridges, no ground plane)
  //    'plating'    → ARTIFICIAL engineered flat surface (no ridges)
  //    'surface'/default → P0 parallax ridges + ground plane (unchanged)
  if (cache.terrainMode === 'cloud-deck') {
    drawCloudDeck(ctx, w, h, t, model, cache, dc);
  } else if (cache.terrainMode === 'plating') {
    drawPlating(ctx, w, h, t, model, cache, dc);
  } else {
    // Default surface path — ridges + ground plane draw regardless of water presence.
    // When water is present, ridge fills clip to [0, waterTopY] so they remain visible
    // as distant terrain features but don't obscure the water band below.
    // The land strip [horizonY..waterTopY] is always filled when water is present.
    const wTopY = cache.waterTopY;  // waterlineY*h, or h if no water
    if (cache.ridgePts.length > 0) {
      // Live sky horizon color for aerial-perspective tint — matches the sky gradient
      // computed in step 1 so far ridges desaturate/lift into the same atmosphere.
      const horBase = pal.skyHorizon;
      const b = dc.bright;
      const hazeR = hasAtmosphere ? Math.min(255, Math.round(horBase[0] * (0.4 + b * 0.6))) : 8;
      const hazeG = hasAtmosphere ? Math.min(255, Math.round(horBase[1] * (0.4 + b * 0.6))) : 8;
      const hazeB = hasAtmosphere ? Math.min(255, Math.round(horBase[2] * (0.4 + b * 0.6))) : 20;
      // Normalized horizon Y bound (model space) — clamps ridge peaks inside sky dome.
      const horizonNorm = model.layers.terrain.horizonY;
      const layerCount = cache.ridgePts.length;

      ctx.save();
      if (cache.hasWater) {
        // Clip ridges to above the waterline — distant terrain visible on the horizon
        ctx.beginPath();
        ctx.rect(0, 0, w, wTopY);
        ctx.clip();
      }

      for (let li = 0; li < layerCount; li++) {
        const layer = cache.ridgePts[li];
        const depthFrac = layer.depthFrac;  // 0=far, 1=near

        // All layers scroll; near layers (large speed) scroll fastest → proper parallax depth.
        const off = t * layer.speed;
        const period = layer.period;
        const microN = layer.pts.length;
        const poly = layer.poly;
        const polyN = poly.length;

        // Per-stratum aerial-perspective tint: blend fill toward live sky horizon color.
        // Far ridges (depthFrac≈0) are 55% hazed; near ridges (≈1) are ~5% hazed.
        // Vacuum worlds use a very faint tint (no atmosphere = no scattering).
        const hazeAmt = 0.55 * (1 - depthFrac) * (hasAtmosphere ? 1.0 : 0.15);
        const hazeAmtC = 1 - hazeAmt;
        const fr = Math.round(layer.fillRGB[0] * hazeAmtC + hazeR * hazeAmt);
        const fg = Math.round(layer.fillRGB[1] * hazeAmtC + hazeG * hazeAmt);
        const fb = Math.round(layer.fillRGB[2] * hazeAmtC + hazeB * hazeAmt);

        ctx.beginPath();
        ctx.moveTo(0, h);
        for (let x = 0; x <= w; x += 8) {
          // Tiling parallax scroll — wraps across the wider-than-screen period
          const xScroll = (((x + off) % period) + period) % period;
          const xFrac = xScroll / period;

          // Sample real polyline for macro ridge shape.
          // Polyline X is evenly spaced at i/(polyN-1) so we interpolate directly.
          let macroY: number;
          if (polyN < 2) {
            macroY = polyN === 1 ? poly[0][1] : horizonNorm;
          } else {
            const fi = xFrac * (polyN - 1);
            const i0 = Math.floor(fi);
            const i1 = Math.min(i0 + 1, polyN - 1);
            const frac = fi - i0;
            const sm = frac * frac * (3 - 2 * frac);  // smoothstep
            macroY = poly[i0][1] * (1 - sm) + poly[i1][1] * sm;
          }

          // Micro-roughness noise — bilateral jitter on top of the macro polyline shape.
          // microAmp is depth-graded (far=0.004, near=0.022) for smooth-far / rough-near.
          const fi2 = xFrac * microN;
          const mi0 = Math.floor(fi2) % microN;
          const mi1 = (mi0 + 1) % microN;
          const mf = fi2 - Math.floor(fi2);
          const ms = mf * mf * (3 - 2 * mf);
          const noise = layer.pts[mi0] * (1 - ms) + layer.pts[mi1] * ms;  // [0, 1]
          const micro = (noise - 0.5) * 2 * layer.microAmp;               // ±microAmp

          // Clamp to [0, horizonNorm] — peaks stay inside sky dome, above ground plane.
          const yNorm = Math.max(0, Math.min(horizonNorm, macroY + micro));
          ctx.lineTo(x, yNorm * h);
        }
        ctx.lineTo(w, h);
        ctx.closePath();
        ctx.fillStyle = `rgb(${fr}, ${fg}, ${fb})`;
        ctx.fill();

        // Interleaved haze veil between ridge layers (not after the near/front layer).
        // A thin atmosphere-colored gradient after each stratum reinforces depth —
        // each successive range appears through progressively thicker air.
        if (li < layerCount - 1 && hasAtmosphere) {
          const veilAlpha = 0.07 * (1 - depthFrac) * Math.max(0.2, dc.bright);
          if (veilAlpha > 0.004) {
            const veilGrad = ctx.createLinearGradient(0, 0, 0, horizonY);
            veilGrad.addColorStop(0,    `rgba(${hazeR}, ${hazeG}, ${hazeB}, ${(veilAlpha * 0.25).toFixed(3)})`);
            veilGrad.addColorStop(0.65, `rgba(${hazeR}, ${hazeG}, ${hazeB}, ${veilAlpha.toFixed(3)})`);
            veilGrad.addColorStop(1,    `rgba(${hazeR}, ${hazeG}, ${hazeB}, ${(veilAlpha * 0.4).toFixed(3)})`);
            ctx.fillStyle = veilGrad;
            ctx.fillRect(0, 0, w, horizonY);
          }
        }
      }
      ctx.restore();
    }

    // 5a) Ground / land-strip fill.
    // Without water: fills from horizonY to h (full ground band when no ridges present).
    // With water: always fills the land strip [horizonY..waterTopY] (the foreshore
    // between the terrain horizon and the waterline, even when ridges are also present).
    // When ridges are present and no water: ridges provide all fill — no extra rect needed.
    if (cache.ridgePts.length === 0 || cache.hasWater) {
      const groundY    = horizonY;
      const groundBtm  = cache.hasWater ? wTopY : h;
      if (groundBtm > groundY) {
        ctx.fillStyle = rgba(model.palette.surface, 1);
        ctx.fillRect(0, groundY, w, groundBtm - groundY);
      }
    }
  }

  // 5b) Terrain landmarks — silhouettes from model.layers.terrain.landmarks.
  //     Drawn for 'surface' and 'plating'; GAS_GIANT emits none so this is a no-op.
  if (cache.landmarks.length > 0) {
    drawLandmarks(ctx, cache, dc);
  }

  // 5f) Feature scatters — flora tufts / rock blobs / glitter-sparks.
  //     Drawn after landmarks (scatters sit on the ground surface), before resource
  //     markers (which are more prominent signals on top of ambient scatter).
  //     Glitter-spark uses additive composite; reset to source-over afterward.
  if (cache.scatterScreens.length > 0) {
    drawScatterInstances(ctx, t, cache, dc);
  }

  // 5c) Deposit markers — ore-vein / gas-seep / thermal-vent / hydrocarbon-pool /
  //     crystal / biolumin — drawn after terrain so they sit on the ground surface.
  for (const dm of cache.depositScreens) {
    drawDepositGlyph(ctx, w, h, t, dm.sx, dm.sy, dm.visual, dm.intensity, model.palette.accent);
  }

  // 5d) Energy source marker — GEOTHERMAL / TIDAL / SOLAR / WIND
  if (cache.energyScreen) {
    const em = cache.energyScreen;
    drawEnergyGlyph(ctx, w, h, t, em.sx, em.sy, em.source, em.intensity, model.palette.accent);
  }

  // 5e) Hazard overlays — drawn with source-over + alpha floor (Truthfulness clause §2.5).
  //     Must remain visible even on high-desirability lush worlds: source-over prevents
  //     the bloom/lighter composite from washing the glyph out.
  for (const hz of cache.hazardScreens) {
    drawHazardGlyph(ctx, w, h, t, hz.visual, hz.severity, hz.pts);
  }

  // 6) Atmosphere haze overlay (atmospheric worlds only)
  if (hasAtmosphere && cache.hazeStrength > 0.05) {
    ctx.save();
    ctx.globalCompositeOperation = 'source-over';
    const hazeGrad = ctx.createLinearGradient(0, horizonY * 0.7, 0, horizonY * 1.05);
    const [hr, hg, hb] = cache.hazeColor.split(',').map((s) => parseInt(s.trim(), 10));
    hazeGrad.addColorStop(0, `rgba(${hr}, ${hg}, ${hb}, 0)`);
    hazeGrad.addColorStop(1, `rgba(${hr}, ${hg}, ${hb}, ${(cache.hazeStrength * 0.35 * dc.bright).toFixed(3)})`);
    ctx.fillStyle = hazeGrad;
    ctx.fillRect(0, horizonY * 0.7, w, horizonY * 0.35);
    ctx.restore();
  }

  // 7) Particles — foreground atmospheric effects
  if (cache.particles.length > 0) {
    drawLandedParticles(ctx, w, h, t, cache);
  }

  // 8) Scene-level night dim (atmospheric worlds only — vacuum has no atmosphere
  //    to scatter and scatter the light so the night side stays ink-black anyway)
  if (hasAtmosphere && dc.skyDim > 0.1) {
    ctx.save();
    ctx.globalCompositeOperation = 'source-over';
    ctx.fillStyle = `rgba(4, 6, 14, ${(dc.skyDim * 0.5).toFixed(3)})`;
    ctx.fillRect(0, 0, w, h);
    ctx.restore();
  }
}

// ---------------------------------------------------------------------------
// mount — public entry point
// Returns a VistaHandle; the caller drives setTime(), resize(), and dispose().
// ---------------------------------------------------------------------------
export function mount(model: VistaModel, target: VistaTarget): VistaHandle {
  const canvas = target.canvas;
  let ctx = canvas.getContext('2d') as CanvasRenderingContext2D;
  let w = canvas.width;
  let h = canvas.height;
  let currentModel = model;
  // Tracks the last VistaInput so update(partial) can merge and regenerate.
  // Undefined until the first update() call; react.tsx always passes the full
  // VistaInput so the merge never loses unset fields.
  let currentInput: VistaInput | undefined;
  let rafId: number | null = null;

  // Offscreen scene buffer — allocated once here, resized on resize().
  // drawScene() renders into offscreen; postProcess() composites to the visible canvas.
  // This is the single shared buffer mandated by WO-V2-POST (no per-frame allocation).
  const offscreen = document.createElement('canvas');
  offscreen.width  = w;
  offscreen.height = h;
  let offCtx = offscreen.getContext('2d') as CanvasRenderingContext2D;

  // Bloom scratch buffer — quarter-res; allocated once here, resized on resize().
  // postProcess() downscales the scene into this, blurs it, and composites back
  // additively.  Keeping it at 1/4 res makes the CSS filter blur much cheaper.
  const bloomScratch = document.createElement('canvas');
  bloomScratch.width  = Math.max(1, Math.ceil(w / 4));
  bloomScratch.height = Math.max(1, Math.ceil(h / 4));

  // Deterministic grain tile — rebuilt when model.seed changes, not per-frame.
  let grainSeedKey = model.seed;
  let grainTile    = buildGrainPattern(model);

  // Cache key incorporating everything that invalidates the pre-baked geometry.
  // Day-bucket busts the cache daily (sea state, weather tier are daily-deterministic).
  function makeKey(m: VistaModel, cw: number, ch: number): string {
    const habBucket = Math.round(m.desirability * 20); // 5% buckets
    const atmoKind = m.layers.atmosphere.present ? (m.layers.atmosphere.clouds.kind) : 'vacuum';
    const citadelLevel = m.desirability; // proxy (full site data comes via pipeline)
    const dayBucket = Math.floor(Date.now() / 86400000);
    return `${m.seed}|${atmoKind}|${habBucket}|${Math.round(citadelLevel * 10)}|${dayBucket}|${cw}|${ch}`;
  }

  function getOrBuildCache(m: VistaModel, cw: number, ch: number): VistaCache {
    const key = makeKey(m, cw, ch);
    // Rebuild when key changes or offscreen context identity changes (remount/resize).
    if (!_cache || _cache.key !== key || _cache.ctx !== offCtx) {
      const c = buildVistaCache(offCtx, m, cw, ch);
      c.key = key;
      _cache = c;
    }
    return _cache;
  }

  let currentT = 0;

  function render(): void {
    // Rebuild grain tile when model.seed changes (new planet loaded via update()).
    if (currentModel.seed !== grainSeedKey) {
      grainSeedKey = currentModel.seed;
      grainTile    = buildGrainPattern(currentModel);
    }
    const cache = getOrBuildCache(currentModel, w, h);
    // Draw scene into the offscreen scene buffer.
    drawScene(offCtx, w, h, currentT, currentModel, cache);
    // Post-process chain: blit → bloom → vignette → split-tone grade → film grain.
    const profile = getProfile(currentModel.planetType);
    postProcess(
      ctx, offscreen, w, h, currentModel, grainTile, profile.grade,
      bloomScratch, currentInput?.view?.quality,
    );
  }

  // Initial render at t=0 (reduced-motion / frozen frame)
  render();

  return {
    setTime(seconds: number): void {
      currentT = seconds;
      render();
    },

    resize(newW: number, newH: number): void {
      canvas.width = newW;
      canvas.height = newH;
      // Re-acquire visible context after resize (Chrome invalidates it on some resize paths).
      const newCtx = canvas.getContext('2d');
      if (newCtx) ctx = newCtx;
      // Resize offscreen scene buffer to match.
      offscreen.width  = newW;
      offscreen.height = newH;
      const newOffCtx = offscreen.getContext('2d');
      if (newOffCtx) offCtx = newOffCtx;
      // Resize bloom scratch to stay at quarter-res.
      bloomScratch.width  = Math.max(1, Math.ceil(newW / 4));
      bloomScratch.height = Math.max(1, Math.ceil(newH / 4));
      w = newW;
      h = newH;
      // Force cache rebuild by clearing the singleton (key includes dimensions).
      _cache = null;
      render();
    },

    update(partial: Partial<VistaInput>): void {
      // Hot-patch: merge partial into the tracked input, regenerate the model
      // via the pipeline, swap it into the live mount, and re-render — all on
      // the EXISTING canvas.  No dispose, no clearRect, no flash.
      //
      // Merge strategy: one level deep on the nested objects so that a partial
      // { planet: { habitability: 0.8 } } only overrides the changed field
      // rather than replacing the whole planet object.  react.tsx passes the
      // full VistaInput, so either path produces a complete input.
      const base: VistaInput = currentInput ?? (partial as VistaInput);
      const merged: VistaInput = {
        ...base,
        ...partial,
        planet: partial.planet
          ? { ...base.planet, ...partial.planet }
          : base.planet,
        celestial: partial.celestial
          ? { ...base.celestial, ...partial.celestial }
          : base.celestial,
      } as VistaInput;
      currentInput = merged;
      currentModel  = generateVista(merged);
      _cache = null;
      render();
    },

    dispose(): void {
      if (rafId !== null) {
        cancelAnimationFrame(rafId);
        rafId = null;
      }
      _cache = null;
      offCtx.clearRect(0, 0, w, h);
      ctx.clearRect(0, 0, w, h);
    },
  };
}

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

  // Terrain ridge data from model (direct view into model strata)
  ridgePts: { pts: number[]; period: number; speed: number; base: number; amp: number; color: string }[];

  // Water
  hasWater: boolean;
  waterTopY: number;
  waves: WaveLine[];
  waterBand: CanvasGradient | null;
  foamMul: number;
  reflTint: string;

  // Atmosphere
  hazeColor: string;
  hazeStrength: number;
  skyDarken: number;

  // Clouds
  clouds: CloudParam[];
  cloudTint: string;

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

  // ---- Terrain ridges from model strata ----
  // model.layers.terrain.strata gives us pre-computed polylines; we convert them
  // to the noise-ridge format (random noise pts + scroll speed from parallax).
  const ridgePts: VistaCache['ridgePts'] = model.layers.terrain.strata.map((s, i) => {
    const rng = splitmix32(deriveChildSeed(model.seed, `ridge${i}`));
    const pts: number[] = [];
    for (let p = 0; p < 48; p++) pts.push(rng());
    const base = s.polyline.length > 0
      ? (s.polyline.reduce((a, pt) => a + pt[1], 0) / s.polyline.length) / h
      : (0.6 + i * 0.12);
    return {
      pts,
      period: Math.max(w * 2, 1200),
      speed: s.parallax * 3.0,     // parallax → scroll speed
      base,
      amp: 0.1 + (i / Math.max(1, model.layers.terrain.strata.length - 1)) * 0.06,
      color: rgba(s.fill, 1),
    };
  });

  // ---- Water ----
  const waterLayer = model.layers.water;
  const hasWater = !!waterLayer;
  const waterTopY = hasWater ? horizonY : h;
  const waves: WaveLine[] = [];
  let waterBand: CanvasGradient | null = null;
  const foamMul = waterLayer ? Math.max(1, waterLayer.foamMul) : 1;
  let reflTint = `${sc.r}, ${sc.g}, ${sc.b}`;

  if (hasWater && waterLayer) {
    const surf = {
      r: Math.round(40 + sc.r * 0.18),
      g: Math.round(120 + sc.g * 0.18),
      b: Math.round(150 + sc.b * 0.15),
    };
    waterBand = ctx.createLinearGradient(0, waterTopY, 0, h);
    waterBand.addColorStop(0, `rgba(${surf.r}, ${surf.g}, ${surf.b}, 0.92)`);
    waterBand.addColorStop(0.4, 'rgba(18, 78, 116, 0.95)');
    waterBand.addColorStop(0.8, 'rgba(10, 46, 78, 0.97)');
    waterBand.addColorStop(1, 'rgba(5, 24, 46, 0.98)');

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

  // ---- Cloud strips ----
  const clouds: CloudParam[] = [];
  const cloudLayer = model.layers.atmosphere.clouds;
  const cloudTint = cloudLayer.color
    ? `${cloudLayer.color[0]}, ${cloudLayer.color[1]}, ${cloudLayer.color[2]}`
    : '200, 210, 230';
  if (hasAtmosphere && cloudLayer.kind !== 'none' && cloudLayer.coverage > 0.05) {
    const cloudCount = Math.round(4 + cloudLayer.coverage * 8);
    for (let i = 0; i < cloudCount; i++) {
      clouds.push({
        x: rngCloud() * w * 1.6,
        speed: (0.6 + rngCloud() * 0.8) * (0.5 + cloudLayer.drift * 0.5),
        w: w * (0.18 + rngCloud() * 0.28),
        hFrac: 0.06 + rngCloud() * 0.10,
        yFrac: 0.15 + rngCloud() * 0.55,
        alpha: (0.04 + rngCloud() * 0.10) * cloudLayer.coverage,
      });
    }
  }

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
  const scatterScreens: VistaCache['scatterScreens'] = model.layers.features.scatters.map((group) => ({
    kind: group.kind,
    instances: group.instances.map((inst) => ({
      sx:     inst.pos[0] * w,
      sy:     horizonY + inst.pos[1] * groundH * 0.80,
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
    hazeColor,
    hazeStrength,
    skyDarken,
    clouds,
    cloudTint,
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
  ctx.save();

  for (const lm of cache.landmarks) {
    const { kind, cx, baseY, height, width } = lm;

    // Apply day-cycle modulation to fill opacity
    ctx.globalAlpha = brightK;

    if (kind === 'cone') {
      // Triangle: base width, angled flanks, pointed apex.
      // Reads unmistakably as a volcano when used with a VOLCANIC archetype.
      ctx.fillStyle = lm.fillColor;
      ctx.beginPath();
      ctx.moveTo(cx - width, baseY);
      ctx.lineTo(cx, baseY - height);
      ctx.lineTo(cx + width, baseY);
      ctx.closePath();
      ctx.fill();
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
      // Broad flat-topped trapezoid with a central V-notch at the summit,
      // suggesting a blow-out crater.  Wider base, flatter than a cone.
      const topW = width * 0.55;       // flat top is narrower than base
      const notchW = width * 0.14;     // notch mouth width at the top
      const notchD = height * 0.16;    // depth of the central depression
      ctx.fillStyle = lm.fillColor;
      ctx.beginPath();
      ctx.moveTo(cx - width, baseY);              // bottom-left
      ctx.lineTo(cx - topW, baseY - height);      // top-left
      ctx.lineTo(cx - notchW, baseY - height);    // notch-left shoulder
      ctx.lineTo(cx, baseY - height + notchD);    // notch bottom
      ctx.lineTo(cx + notchW, baseY - height);    // notch-right shoulder
      ctx.lineTo(cx + topW, baseY - height);      // top-right
      ctx.lineTo(cx + width, baseY);              // bottom-right
      ctx.closePath();
      ctx.fill();
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
      // Wide flat-topped butte: nearly as wide at the top as the base, low
      // aspect ratio so it reads as a plateau rather than a peak.
      const topW = width * 0.80;   // wide flat top
      const h2 = height * 0.65;   // lower than a mountain (mesa is flat, not peaked)
      ctx.fillStyle = lm.fillColor;
      ctx.beginPath();
      ctx.moveTo(cx - width, baseY);
      ctx.lineTo(cx - topW, baseY - h2);
      ctx.lineTo(cx + topW, baseY - h2);
      ctx.lineTo(cx + width, baseY);
      ctx.closePath();
      ctx.fill();
      // Pale top-face edge highlight
      ctx.save();
      ctx.globalCompositeOperation = 'lighter';
      ctx.globalAlpha = dc.bright * 0.12;
      ctx.strokeStyle = 'rgba(220, 210, 190, 0.7)';
      ctx.lineWidth = 1.5;
      ctx.beginPath();
      ctx.moveTo(cx - topW, baseY - h2);
      ctx.lineTo(cx + topW, baseY - h2);
      ctx.stroke();
      ctx.restore();

    } else if (kind === 'spire') {
      // Tall thin triangle — reads as an isolated rock needle or alien spire.
      const sw = width * 0.22;  // very narrow base
      const sh = height * 1.3;  // very tall (can exceed the "standard" height cap)
      ctx.fillStyle = lm.fillColor;
      ctx.beginPath();
      ctx.moveTo(cx - sw, baseY);
      ctx.lineTo(cx, baseY - Math.min(sh, cache.horizonY * 1.1));
      ctx.lineTo(cx + sw, baseY);
      ctx.closePath();
      ctx.fill();

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
      // Two pillars with a quadratic arc connecting their tops.
      // Reads as a natural rock arch or alien bridging structure.
      const ow = width * 0.22;  // outer pillar half-width
      const iw = width * 0.48;  // inner edge (gap between the pillars)
      const ah = height * 0.85; // arch height at the keystone
      ctx.fillStyle = lm.fillColor;
      // Left pillar
      ctx.fillRect(cx - iw - ow, baseY - ah, ow, ah);
      // Right pillar
      ctx.fillRect(cx + iw, baseY - ah, ow, ah);
      // Arch span (filled path from the two pillar tops, arcing upward)
      ctx.beginPath();
      ctx.moveTo(cx - iw - ow, baseY - ah);
      ctx.lineTo(cx - iw, baseY - ah);
      ctx.quadraticCurveTo(cx, baseY - height, cx + iw, baseY - ah);
      ctx.lineTo(cx + iw + ow, baseY - ah);
      ctx.quadraticCurveTo(cx, baseY - ah * 1.1, cx - iw - ow, baseY - ah);
      ctx.closePath();
      ctx.fill();

    } else if (kind === 'canyon') {
      // Dark downward V-notch into the foreground terrain.
      // Represents a deep crack or ravine; sits ON the ground line, opens downward.
      const cd = height * 0.55;   // depth of the notch below the ground line
      const cw2 = width * 0.75;   // mouth width at the surface
      ctx.fillStyle = lm.fillColor;
      ctx.beginPath();
      ctx.moveTo(cx - cw2, baseY);
      ctx.lineTo(cx, baseY + cd);
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
      // Pale blue-white wedge: a wide angled mass reminiscent of a glacier
      // flowing from one side.  Wider at the top-left, tapering to the right.
      const gw = width * 1.1;
      const gh = height * 0.55;
      ctx.fillStyle = lm.fillColor;
      ctx.beginPath();
      ctx.moveTo(cx - gw, baseY);
      ctx.lineTo(cx - gw * 0.4, baseY - gh);
      ctx.lineTo(cx + gw * 0.55, baseY - gh * 0.3);
      ctx.lineTo(cx + gw, baseY);
      ctx.closePath();
      ctx.fill();
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

  const brightK = 0.55 + dc.bright * 0.45;

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
        // Subtle cast shadow below
        ctx.globalAlpha = brightK * 0.22;
        ctx.fillStyle   = 'rgba(0, 0, 0, 0.5)';
        ctx.beginPath();
        ctx.ellipse(sx, sy + sizePx * 0.28, sizePx * 0.85, sizePx * 0.22, 0, 0, Math.PI * 2);
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

  // 1) Sky gradient — rebuilt per frame from palette + day cycle
  {
    let g: CanvasGradient;
    if (!hasAtmosphere) {
      // VACUUM: near-black sky regardless of sun position
      g = ctx.createLinearGradient(0, 0, 0, horizonY * 1.15);
      g.addColorStop(0, 'rgb(2, 2, 6)');
      g.addColorStop(0.6, 'rgb(4, 4, 12)');
      g.addColorStop(1, 'rgb(8, 8, 20)');
    } else {
      const b = dc.bright;
      const warm = dc.warm;
      const topBase = pal.skyTop;
      const horBase = pal.skyHorizon;
      const topR = Math.round(Math.min(255, topBase[0] * (0.3 + b * 0.7) + warm * 15));
      const topG = Math.round(Math.min(255, topBase[1] * (0.3 + b * 0.7) + warm * 5));
      const topB = Math.round(Math.min(255, topBase[2] * (0.3 + b * 0.7)));
      const horR = Math.round(Math.min(255, horBase[0] * (0.4 + b * 0.6) + warm * 40));
      const horG = Math.round(Math.min(255, horBase[1] * (0.4 + b * 0.6) + warm * 18));
      const horB = Math.round(Math.min(255, horBase[2] * (0.4 + b * 0.6)));
      const midR = Math.round((topR + horR) / 2);
      const midG = Math.round((topG + horG) / 2);
      const midB = Math.round((topB + horB) / 2);
      g = ctx.createLinearGradient(0, 0, 0, horizonY * 1.15);
      g.addColorStop(0, `rgb(${topR}, ${topG}, ${topB})`);
      g.addColorStop(0.6, `rgb(${midR}, ${midG}, ${midB})`);
      g.addColorStop(1, `rgb(${horR}, ${horG}, ${horB})`);
    }
    ctx.fillStyle = g;
    ctx.fillRect(0, 0, w, h);
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

  // 2b) Drifting cloud bands (atmospheric worlds only)
  if (hasAtmosphere && cache.clouds.length > 0) {
    ctx.save();
    for (let i = 0; i < cache.clouds.length; i++) {
      const c = cache.clouds[i];
      const span = w * 1.6;
      const cx = (((c.x + t * c.speed) % span) + span) % span - w * 0.3;
      const chh = h * c.hFrac;
      const cy = Math.min(horizonY * c.yFrac * 2, horizonY - chh - 4);
      ctx.globalCompositeOperation = 'lighter';
      const g = ctx.createRadialGradient(cx, cy, 0, cx, cy, c.w);
      g.addColorStop(0, `rgba(${cache.cloudTint}, ${c.alpha.toFixed(3)})`);
      g.addColorStop(1, `rgba(${cache.cloudTint}, 0)`);
      ctx.fillStyle = g;
      ctx.save(); ctx.translate(cx, cy); ctx.scale(1, chh / c.w); ctx.translate(-cx, -cy);
      ctx.fillRect(cx - c.w, cy - c.w, c.w * 2, c.w * 2);
      ctx.restore();
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

    // Water surface waterline foam
    ctx.save();
    ctx.globalCompositeOperation = 'lighter';
    ctx.strokeStyle = 'rgba(220, 240, 250, 1)';
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
    // Default surface path — EXACTLY as P0 (no changes to this branch)
    if (!cache.hasWater && cache.ridgePts.length > 0) {
      for (let li = 0; li < cache.ridgePts.length; li++) {
        const layer = cache.ridgePts[li];
        const isFront = li === cache.ridgePts.length - 1;
        const off = isFront ? 0 : t * layer.speed;
        const period = layer.period;
        const n = layer.pts.length;
        ctx.beginPath();
        ctx.moveTo(0, h);
        for (let x = 0; x <= w; x += 8) {
          const u = (((x + off) % period) + period) % period;
          const fi = (u / period) * n;
          const i0 = Math.floor(fi) % n;
          const i1 = (i0 + 1) % n;
          const frac = fi - Math.floor(fi);
          const s = frac * frac * (3 - 2 * frac);
          const v = layer.pts[i0] * (1 - s) + layer.pts[i1] * s;
          const yTop = h * layer.base - v * h * layer.amp;
          ctx.lineTo(x, yTop);
        }
        ctx.lineTo(w, h);
        ctx.closePath();
        ctx.fillStyle = layer.color;
        ctx.fill();
      }
    }

    // 5a) Ground plane (fallback if no ridges or water)
    if (cache.ridgePts.length === 0 && !cache.hasWater) {
      const groundY = horizonY;
      const gfill = rgba(model.palette.surface, 1);
      ctx.fillStyle = gfill;
      ctx.fillRect(0, groundY, w, h - groundY);
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
  let rafId: number | null = null;

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
    // Rebuild when key changes or canvas context identity changes (remount)
    if (!_cache || _cache.key !== key || _cache.ctx !== ctx) {
      const c = buildVistaCache(ctx, m, cw, ch);
      c.key = key;
      _cache = c;
    }
    return _cache;
  }

  let currentT = 0;

  function render(): void {
    const cache = getOrBuildCache(currentModel, w, h);
    drawScene(ctx, w, h, currentT, currentModel, cache);
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
      // Re-acquire context after resize (Chrome invalidates it on some resize paths)
      const newCtx = canvas.getContext('2d');
      if (newCtx) ctx = newCtx;
      w = newW;
      h = newH;
      // Force cache rebuild by clearing the singleton (key includes dimensions)
      _cache = null;
      render();
    },

    update(partial: Partial<VistaInput>): void {
      // Hot-patch: merge partial into a new model via the generate pipeline.
      // For now, force a cache bust and re-render with the existing model.
      // A full update requires calling generate() (Lane B) externally.
      _cache = null;
      render();
    },

    dispose(): void {
      if (rafId !== null) {
        cancelAnimationFrame(rafId);
        rafId = null;
      }
      _cache = null;
      ctx.clearRect(0, 0, w, h);
    },
  };
}

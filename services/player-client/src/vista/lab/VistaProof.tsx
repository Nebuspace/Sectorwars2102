/**
 * Vista Proof Harness — DEV-only, dead-code-eliminated from prod builds.
 *
 * Renders a fixed VistaInput at clock=0 (frozen frame) for deterministic
 * Playwright screenshot comparison.  All inputs are hardcoded literals —
 * randomVistaInput is never called here so before/after captures are
 * always comparable against the same pixel budget.
 *
 * Route: /lab/vista-proof                    → default named-storm TERRAN scene
 * Route: /lab/vista-proof?type=JUNGLE        → FIXED_INPUTS['JUNGLE']   (daytime, t=0)
 * Route: /lab/vista-proof?type=OCEANIC&phase=night → FIXED_INPUTS['OCEANIC'] at 3am
 *
 * Supported ?type= values (14):
 *   TERRAN · JUNGLE · TROPICAL · MOUNTAINOUS · ICE · VOLCANIC · OCEANIC · BARREN · DESERT
 *   BLACK_HOLE · NEUTRON · RING_ARC · RINGED_MOON · PHASED_SIBLING
 * Unknown or absent ?type → falls back to the original named-storm PROOF_INPUT
 * so the existing vista-named-storm-proof.spec.ts continues to pass unchanged.
 *
 * Supported ?phase= values:
 *   day (default) — t=0, frozen at FROZEN_DAY_PHASE=0.40; sun always up.
 *   night         — seed-specific clock placing the scene at 3am (sunAlt≈−0.71,
 *                   sunUp=false); starfields, moon glitter, and night-sky FX visible.
 *
 * Readiness protocol:
 *   A polling rAF loop reads the canvas pixel buffer (getImageData) and marks
 *   ready only when non-black pixels are confirmed.  A single rAF would race
 *   with the ResizeObserver: rAF fires BEFORE ResizeObserver in the browser
 *   rendering loop, so a one-shot gate would fire before the first resize+redraw
 *   cycle that VistaCanvas's ResizeObserver triggers.  The poll survives any
 *   ordering of effects, ResizeObserver, and paint callbacks.
 *
 * P2 reuse:
 *   When named→sky lands, add a second test that asserts storm-cell overlays
 *   appear above horizonY in the sky region.  The PROOF_INPUT hazard spec is
 *   intentionally identical to hazard-truthfulness.test.ts's regression anchor
 *   (different seed, same structure) so the two suites stay aligned.
 */

import { useState, useEffect } from 'react';
import type { VistaInput } from '../contract';
import VistaCanvas from '../react';
import { SeededRng, deriveChildSeed } from '../core/rng';
import { DAY_CYCLE_SECONDS } from '../render/canvas2d/backend';

// ---------------------------------------------------------------------------
// Night-clock helper
// ---------------------------------------------------------------------------
//
// Computes the clock value (seconds) that places a specific seed at 3 am
// (dayPhase=0.875, sunAlt≈−0.71, sunUp=false) so night-only FX are visible.
//
// Mirrors the exact RNG chain in backend.ts buildVistaCache():
//   phaseOffset = splitmix32(deriveChildSeed(model.seed, 'renderer'))()
// SeededRng from rng.ts uses the same SplitMix32 algorithm as the inline
// splitmix32 in backend.ts, so the outputs are byte-identical.
//
// At t = nightClockFor(seed):
//   dayCycleAt(t, phaseOffset).dayPhase ≡ 0.875
//   → sunAlt = sin((0.875−0.25)×2π) ≈ −0.707 → sunUp = false

function nightClockFor(seed: string): number {
  const phaseOffset = new SeededRng(deriveChildSeed(seed, 'renderer')).next01();
  const targetPhase = 0.875;   // 3 am — sun clearly below horizon
  const tFrac = ((targetPhase - phaseOffset) % 1 + 1) % 1;
  return tFrac * DAY_CYCLE_SECONDS;
}

// ---------------------------------------------------------------------------
// Default proof input (named-storm regression anchor)
// ---------------------------------------------------------------------------
//
// TERRAN, hab=92 (lush — desirability will exceed 0.7).
// Hazards:
//   storm  severity=0.85, named=true  → TERRAN profile maps storm → 'storm-cell'
//   flood  severity=0.60, named=false → TERRAN profile maps flood → 'flood-zone'
//
// Fix-A contract: neither overlay resolves to 'impact-scar'.
// Both glyphs must be visually distinct: storm-cell (spiral+eye) ≠ flood-zone (ripple-wash).

const PROOF_INPUT: VistaInput = {
  contractVersion: 1,
  seed: 'proof-named-storm-001',

  planet: {
    type:         'TERRAN',
    habitability: 92,
    atmosphere: {
      present: true,
      kind:    null,
      density: 0.75,
    },
    nativeLife:    0.65,
    temperature:   0.15,
    waterCoverage: 0.55,
  },

  celestial: {
    star:                { kind: 'G_YELLOW', color: '#fff4d0' },
    orbitAu:             1.0,
    phaseDeg:            160,
    rotationPeriodHours: 24,
    axialTiltDeg:        23,
  },

  site: {
    shape:          'SPRAWLING',
    usableSlots:    18,
    citadelCeiling: 3,
    energy: { source: 'SOLAR', tier: 2, magnitude: 0.65 },
    deposits: [
      { kind: 'ore',     richness: 0.72 },
      { kind: 'crystal', richness: 0.45 },
    ],
    hazards: [
      { kind: 'storm', severity: 0.85, named: true  },   // storm → storm-cell
      { kind: 'flood', severity: 0.60, named: false },   // flood → flood-zone
    ],
  },
};

// ---------------------------------------------------------------------------
// Multi-type fixed inputs
// ---------------------------------------------------------------------------
//
// One COMPLETE VistaInput literal per planet type.  These are fixed literals —
// NOT calls to randomVistaInput — so the same input runs on any branch and
// a pixel diff between before/after reflects only engine changes, not entropy.
//
// Coverage: 9 of the 12 PlanetTypes (GAS_GIANT / ARCTIC / ARTIFICIAL deferred).

const FIXED_INPUTS: Record<string, VistaInput> = {

  TERRAN: {
    contractVersion: 1,
    seed: 'type-proof-TERRAN-001',
    planet: {
      type:         'TERRAN',
      habitability: 85,
      atmosphere:   { present: true, kind: null, density: 0.70 },
      nativeLife:   0.55,
      temperature:  0.10,
      waterCoverage: 0.55,
    },
    celestial: {
      star:                { kind: 'G_YELLOW', color: '#fff4d0' },
      orbitAu:             1.0,
      phaseDeg:            60,
      rotationPeriodHours: 24,
      axialTiltDeg:        23,
    },
    site: {
      shape: 'SPRAWLING', usableSlots: 18, citadelCeiling: 3,
      energy: { source: 'SOLAR', tier: 2, magnitude: 0.65 },
      deposits: [
        { kind: 'ore',  richness: 0.70 },
        { kind: 'food', richness: 0.80 },
      ],
      hazards: [
        { kind: 'storm', severity: 0.60, named: false },
      ],
    },
  },

  JUNGLE: {
    contractVersion: 1,
    seed: 'type-proof-JUNGLE-001',
    planet: {
      type:         'JUNGLE',
      habitability: 78,
      atmosphere:   { present: true, kind: null, density: 0.80 },
      nativeLife:   0.88,
      temperature:  0.40,
      waterCoverage: 0.45,
    },
    celestial: {
      star:                { kind: 'G_YELLOW', color: '#fff4d0' },
      orbitAu:             0.9,
      phaseDeg:            60,
      rotationPeriodHours: 28,
      axialTiltDeg:        15,
    },
    site: {
      shape: 'IRREGULAR', usableSlots: 14, citadelCeiling: 2,
      energy: { source: 'WIND', tier: 1, magnitude: 0.55 },
      deposits: [
        { kind: 'organic', richness: 0.82 },
        { kind: 'gas',     richness: 0.40 },
      ],
      hazards: [
        { kind: 'megafauna', severity: 0.70, named: false },
        { kind: 'flood',     severity: 0.45, named: false },
      ],
    },
  },

  TROPICAL: {
    contractVersion: 1,
    seed: 'type-proof-TROPICAL-001',
    planet: {
      type:         'TROPICAL',
      habitability: 74,
      atmosphere:   { present: true, kind: null, density: 0.75 },
      nativeLife:   0.60,
      temperature:  0.48,
      waterCoverage: 0.68,
    },
    celestial: {
      star:                { kind: 'G_YELLOW', color: '#fff4d0' },
      orbitAu:             0.95,
      phaseDeg:            60,
      rotationPeriodHours: 22,
      axialTiltDeg:        8,
    },
    site: {
      shape: 'LINEAR', usableSlots: 16, citadelCeiling: 3,
      energy: { source: 'TIDAL', tier: 2, magnitude: 0.70 },
      deposits: [
        { kind: 'food',    richness: 0.76 },
        { kind: 'crystal', richness: 0.38 },
      ],
      hazards: [
        { kind: 'storm', severity: 0.75, named: true },
      ],
    },
  },

  MOUNTAINOUS: {
    contractVersion: 1,
    seed: 'type-proof-MOUNTAINOUS-001',
    planet: {
      type:         'MOUNTAINOUS',
      habitability: 52,
      atmosphere:   { present: true, kind: null, density: 0.55 },
      nativeLife:   0.30,
      temperature:  -0.10,
      waterCoverage: 0.18,
    },
    celestial: {
      star:                { kind: 'K_ORANGE', color: '#ffd090' },
      orbitAu:             1.1,
      phaseDeg:            60,
      rotationPeriodHours: 30,
      axialTiltDeg:        30,
    },
    site: {
      shape: 'TERRACED', usableSlots: 12, citadelCeiling: 4,
      energy: { source: 'GEOTHERMAL', tier: 2, magnitude: 0.60 },
      deposits: [
        { kind: 'ore',     richness: 0.85 },
        { kind: 'crystal', richness: 0.62 },
      ],
      hazards: [
        { kind: 'seismic', severity: 0.65, named: false },
        { kind: 'snow',    severity: 0.40, named: false },
      ],
    },
  },

  ICE: {
    contractVersion: 1,
    seed: 'type-proof-ICE-001',
    planet: {
      type:         'ICE',
      habitability: 18,
      atmosphere:   { present: true, kind: null, density: 0.40 },
      nativeLife:   0.08,
      temperature:  -0.85,
      waterCoverage: 0.72,
    },
    celestial: {
      star:                { kind: 'K_ORANGE', color: '#ffcc80' },
      orbitAu:             2.2,
      phaseDeg:            60,
      rotationPeriodHours: 48,
      axialTiltDeg:        5,
    },
    site: {
      shape: 'COMPACT', usableSlots: 10, citadelCeiling: 2,
      energy: { source: 'GEOTHERMAL', tier: 1, magnitude: 0.45 },
      deposits: [
        { kind: 'ice', richness: 0.90 },
        { kind: 'ore', richness: 0.35 },
      ],
      hazards: [
        { kind: 'snow', severity: 0.80, named: false },
      ],
    },
  },

  VOLCANIC: {
    contractVersion: 1,
    seed: 'type-proof-VOLCANIC-001',
    planet: {
      type:         'VOLCANIC',
      habitability: 12,
      atmosphere:   { present: true, kind: 'sulfurous', density: 0.90 },
      nativeLife:   0.10,
      temperature:  0.88,
      waterCoverage: 0.05,
    },
    celestial: {
      star:                { kind: 'M_DWARF', color: '#ff8060' },
      orbitAu:             0.3,
      phaseDeg:            60,
      rotationPeriodHours: 200,
      axialTiltDeg:        2,
    },
    site: {
      shape: 'COMPACT', usableSlots: 8, citadelCeiling: 1,
      energy: { source: 'GEOTHERMAL', tier: 4, magnitude: 0.95 },
      deposits: [
        { kind: 'mineral', richness: 0.78 },
        { kind: 'gas',     richness: 0.60 },
      ],
      hazards: [
        { kind: 'lava',    severity: 0.90, named: true  },
        { kind: 'seismic', severity: 0.75, named: false },
      ],
    },
  },

  OCEANIC: {
    contractVersion: 1,
    seed: 'type-proof-OCEANIC-001',
    planet: {
      type:         'OCEANIC',
      habitability: 70,
      atmosphere:   { present: true, kind: null, density: 0.78 },
      nativeLife:   0.65,
      temperature:  0.20,
      waterCoverage: 0.90,
    },
    celestial: {
      star:                { kind: 'G_YELLOW', color: '#fff4d0' },
      orbitAu:             1.05,
      phaseDeg:            60,
      rotationPeriodHours: 26,
      axialTiltDeg:        12,
    },
    site: {
      shape: 'ENGINEERED', usableSlots: 14, citadelCeiling: 3,
      energy: { source: 'TIDAL', tier: 3, magnitude: 0.80 },
      deposits: [
        { kind: 'gas',     richness: 0.55 },
        { kind: 'organic', richness: 0.72 },
      ],
      hazards: [
        { kind: 'flood', severity: 0.70, named: false },
        { kind: 'storm', severity: 0.55, named: false },
      ],
    },
  },

  BARREN: {
    contractVersion: 1,
    seed: 'type-proof-BARREN-001',
    planet: {
      type:         'BARREN',
      habitability: 5,
      atmosphere:   { present: false, kind: null, density: 0.0 },
      nativeLife:   0.0,
      temperature:  0.05,
      waterCoverage: 0.0,
    },
    celestial: {
      star:                { kind: 'G_YELLOW', color: '#fff4d0' },
      orbitAu:             1.8,
      phaseDeg:            60,
      rotationPeriodHours: 60,
      axialTiltDeg:        1,
    },
    site: {
      shape: 'COMPACT', usableSlots: 8, citadelCeiling: 2,
      energy: { source: 'SOLAR', tier: 1, magnitude: 0.35 },
      deposits: [
        { kind: 'ore',     richness: 0.65 },
        { kind: 'mineral', richness: 0.50 },
      ],
      hazards: [
        { kind: 'radiation', severity: 0.60, named: false },
        { kind: 'impact',    severity: 0.40, named: false },
      ],
    },
  },

  DESERT: {
    contractVersion: 1,
    seed: 'type-proof-DESERT-001',
    planet: {
      type:         'DESERT',
      habitability: 22,
      atmosphere:   { present: true, kind: null, density: 0.45 },
      nativeLife:   0.15,
      temperature:  0.70,
      waterCoverage: 0.02,
    },
    celestial: {
      star:                { kind: 'A_BLUE', color: '#e0eeff' },
      orbitAu:             1.4,
      phaseDeg:            60,
      rotationPeriodHours: 36,
      axialTiltDeg:        20,
    },
    site: {
      shape: 'SPRAWLING', usableSlots: 16, citadelCeiling: 2,
      energy: { source: 'SOLAR', tier: 3, magnitude: 0.88 },
      deposits: [
        { kind: 'mineral', richness: 0.72 },
        { kind: 'ore',     richness: 0.45 },
      ],
      hazards: [
        { kind: 'dust', severity: 0.75, named: false },
      ],
    },
  },

  // ── WO-V3-CELESTIAL special-case draw-path coverage ────────────────────────

  // BLACK_HOLE → suns[0].special === 'accretion' → drawAccretionDisc()
  BLACK_HOLE: {
    contractVersion: 1,
    seed: 'v3-proof-BLACK_HOLE-001',
    planet: {
      type:         'BARREN',
      habitability: 0,
      atmosphere:   { present: false, kind: null, density: 0.0 },
      nativeLife:   0.0,
      temperature:  0.5,
      waterCoverage: 0.0,
    },
    celestial: {
      star:                { kind: 'BLACK_HOLE', color: '#0a0010' },
      orbitAu:             0.5,
      phaseDeg:            60,
      rotationPeriodHours: 24,
      axialTiltDeg:        5,
    },
    site: {
      shape: 'SPRAWLING', usableSlots: 12, citadelCeiling: 1,
      energy: { source: 'SOLAR', tier: 1, magnitude: 0.10 },
      deposits: [],
      hazards: [],
    },
  },

  // NEUTRON → suns[0].special === 'pulsar' → drawPulsar()
  NEUTRON: {
    contractVersion: 1,
    seed: 'v3-proof-NEUTRON-001',
    planet: {
      type:         'BARREN',
      habitability: 0,
      atmosphere:   { present: false, kind: null, density: 0.0 },
      nativeLife:   0.0,
      temperature:  0.7,
      waterCoverage: 0.0,
    },
    celestial: {
      star:                { kind: 'NEUTRON', color: '#d0c0ff' },
      orbitAu:             0.3,
      phaseDeg:            60,
      rotationPeriodHours: 24,
      axialTiltDeg:        5,
    },
    site: {
      shape: 'SPRAWLING', usableSlots: 12, citadelCeiling: 1,
      energy: { source: 'SOLAR', tier: 1, magnitude: 0.12 },
      deposits: [],
      hazards: [],
    },
  },

  // RING_ARC — rings:true → pipeline emits ringArc → drawRingArc()
  RING_ARC: {
    contractVersion: 1,
    seed: 'v3-proof-RING_ARC-001',
    planet: {
      type:         'TERRAN',
      habitability: 70,
      atmosphere:   { present: true, kind: null, density: 0.65 },
      nativeLife:   0.50,
      temperature:  0.05,
      waterCoverage: 0.50,
    },
    celestial: {
      star:                { kind: 'G_YELLOW', color: '#fff4d0' },
      orbitAu:             1.0,
      phaseDeg:            60,
      rotationPeriodHours: 24,
      axialTiltDeg:        35,    // high tilt → visible arc
      rings:               true,
    },
    site: {
      shape: 'SPRAWLING', usableSlots: 16, citadelCeiling: 3,
      energy: { source: 'SOLAR', tier: 2, magnitude: 0.65 },
      deposits: [{ kind: 'mineral', richness: 0.60 }],
      hazards: [],
    },
  },

  // RINGED_MOON — moon with hasRings:true → ring ellipse drawn in drawLandedMoons()
  RINGED_MOON: {
    contractVersion: 1,
    seed: 'v3-proof-RINGED_MOON-001',
    planet: {
      type:         'TERRAN',
      habitability: 72,
      atmosphere:   { present: true, kind: null, density: 0.70 },
      nativeLife:   0.45,
      temperature:  0.08,
      waterCoverage: 0.52,
    },
    celestial: {
      star:                { kind: 'G_YELLOW', color: '#fff4d0' },
      orbitAu:             1.0,
      phaseDeg:            60,
      rotationPeriodHours: 24,
      axialTiltDeg:        15,
      moons: [
        { sizeClass: 3, phaseDeg: 90,  hasRings: true },
        { sizeClass: 2, phaseDeg: 200, hasRings: false },
      ],
    },
    site: {
      shape: 'SPRAWLING', usableSlots: 16, citadelCeiling: 3,
      energy: { source: 'SOLAR', tier: 2, magnitude: 0.68 },
      deposits: [{ kind: 'ore', richness: 0.55 }],
      hazards: [],
    },
  },

  // PHASED_SIBLING — sibling body in the sky → phase terminator in drawLandedSkyPlanets()
  PHASED_SIBLING: {
    contractVersion: 1,
    seed: 'v3-proof-PHASED_SIBLING-001',
    planet: {
      type:         'TERRAN',
      habitability: 68,
      atmosphere:   { present: true, kind: null, density: 0.72 },
      nativeLife:   0.40,
      temperature:  0.12,
      waterCoverage: 0.48,
    },
    celestial: {
      star:                { kind: 'G_YELLOW', color: '#fff4d0' },
      orbitAu:             1.0,
      phaseDeg:            60,
      rotationPeriodHours: 24,
      axialTiltDeg:        10,
      siblings: [
        { kind: 'GAS_GIANT', sizeClass: 3, phaseDeg: 140, hue: 35,  sat: 0.55 },
        { kind: 'TERRAN',    sizeClass: 2, phaseDeg: 280, hue: 195, sat: 0.40 },
      ],
    },
    site: {
      shape: 'SPRAWLING', usableSlots: 16, citadelCeiling: 3,
      energy: { source: 'SOLAR', tier: 2, magnitude: 0.65 },
      deposits: [{ kind: 'food', richness: 0.62 }],
      hazards: [],
    },
  },
};

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

// Maximum rAF iterations before giving up and marking ready anyway (so the
// spec's content guard applies its verdict rather than hanging indefinitely).
const MAX_SETTLE_FRAMES = 60;

export default function VistaProof() {
  // Select input: ?type=<PLANET_TYPE> → FIXED_INPUTS lookup; absent/unknown → PROOF_INPUT.
  // Select phase: ?phase=night → night-mode clock (3 am, sun below horizon); default = day.
  const params     = new URLSearchParams(window.location.search);
  const typeParam  = params.get('type') ?? '';
  const phaseParam = params.get('phase') ?? 'day';
  const isNight    = phaseParam === 'night';

  // ---------------------------------------------------------------------------
  // Slider override params — each 0..1, applied on top of the base type's
  // FIXED_INPUT.  Intended for DRIVEN before/after proof captures (slider-pairs
  // spec).  All fields are optional: absent params leave the base value intact.
  //
  //   ?waterCoverage=  0..1  → planet.waterCoverage (direct, 0–1 contract range)
  //   ?temperature=    0..1  → planet.temperature mapped linearly to -1..+1
  //                            (0 = frozen / -1, 1 = molten / +1)
  //   ?nativeLife=     0..1  → planet.nativeLife (direct)
  //   ?atmDensity=     0..1  → planet.atmosphere.density (direct); atmosphere.present
  //                            is inherited from the base (not forced off at low density)
  //   ?habitability=   0..1  → planet.habitability scaled to 0-100 (contract scale)
  // ---------------------------------------------------------------------------
  const parseSlider = (key: string): number | undefined => {
    const v = params.get(key);
    if (v === null) return undefined;
    const n = parseFloat(v);
    return isNaN(n) ? undefined : Math.max(0, Math.min(1, n));
  };

  const ovWaterCoverage = parseSlider('waterCoverage');
  const ovTemperature   = parseSlider('temperature');
  const ovNativeLife    = parseSlider('nativeLife');
  const ovAtmDensity    = parseSlider('atmDensity');
  const ovHabitability  = parseSlider('habitability');

  const baseInput: VistaInput = FIXED_INPUTS[typeParam] ?? PROOF_INPUT;

  // Merge overrides.  Only fields with an explicit URL param are touched; all
  // other planet fields (type, atmosphere.kind, deposits, hazards, etc.) are
  // inherited unchanged from the base input.
  const hasOverrides =
    ovWaterCoverage !== undefined || ovTemperature !== undefined ||
    ovNativeLife    !== undefined || ovAtmDensity  !== undefined ||
    ovHabitability  !== undefined;

  const activeInput: VistaInput = hasOverrides ? {
    ...baseInput,
    planet: {
      ...baseInput.planet,
      ...(ovWaterCoverage !== undefined && { waterCoverage: ovWaterCoverage }),
      ...(ovTemperature   !== undefined && { temperature:   ovTemperature * 2 - 1 }),
      ...(ovNativeLife    !== undefined && { nativeLife:    ovNativeLife }),
      ...(ovAtmDensity    !== undefined && {
        atmosphere: { ...baseInput.planet.atmosphere, density: ovAtmDensity },
      }),
      ...(ovHabitability  !== undefined && { habitability:  Math.round(ovHabitability * 100) }),
    },
  } : baseInput;

  // Build a label that lists active override keys for the harness footer.
  const overrideKeys = (
    [
      ovWaterCoverage !== undefined && 'waterCoverage',
      ovTemperature   !== undefined && 'temperature',
      ovNativeLife    !== undefined && 'nativeLife',
      ovAtmDensity    !== undefined && 'atmDensity',
      ovHabitability  !== undefined && 'habitability',
    ] as (string | false)[]
  ).filter(Boolean) as string[];

  const activeLabel = overrideKeys.length > 0
    ? `type: ${typeParam || 'default'} [${overrideKeys.join(',')}]`
    : (typeParam && FIXED_INPUTS[typeParam]
        ? `type: ${typeParam}`
        : 'named-storm (default)');

  // At day (default), t=0 freezes the scene at FROZEN_DAY_PHASE=0.40 (sun always up).
  // At night, we compute the seed-specific clock that places the scene at 3 am
  // so starfields, moon glitter, and night-sky FX are visible.
  const activeClock = isNight ? nightClockFor(activeInput.seed) : 0;

  // Readiness gate for Playwright.
  //
  // WHY POLL INSTEAD OF A SINGLE RAF:
  // requestAnimationFrame fires BEFORE ResizeObserver in the browser rendering
  // loop.  VistaCanvas's ResizeObserver fires on its initial observation (frame
  // N+1 after mount), sets canvas.width = w (clearing the buffer), then calls
  // handle.resize() → render() (redrawing it).  A single-rAF gate fires in
  // that same frame BEFORE the ResizeObserver clears-and-redraws, so Playwright
  // could screenshot an empty canvas even though render() has already been called.
  //
  // The polling loop reads the canvas pixel buffer directly (getImageData, NOT
  // the compositor) and advances until non-black pixels are confirmed.  This
  // is immune to compositor timing and ResizeObserver ordering.
  const [ready, setReady] = useState(false);
  useEffect(() => {
    let rafId: number;
    let attempts = 0;

    function poll() {
      attempts++;

      const container = document.querySelector('[data-testid="vista-proof-container"]');
      const canvas = container?.querySelector('canvas') as HTMLCanvasElement | null;

      if (canvas && canvas.width > 1 && canvas.height > 1) {
        try {
          const ctx = canvas.getContext('2d');
          if (ctx) {
            // Sample the center strip (sky + upper terrain) for color content.
            // A lush TERRAN world always has sky pixels well above the 5/5/5 threshold.
            const sW = Math.min(200, canvas.width);
            const sH = Math.min(100, canvas.height);
            const ox = Math.floor((canvas.width  - sW) / 2);
            const oy = Math.floor((canvas.height - sH) / 2);
            const { data } = ctx.getImageData(ox, oy, sW, sH);

            let colorCount = 0;
            for (let i = 0; i < data.length; i += 16) { // sample every 4th pixel
              if (data[i] > 5 || data[i + 1] > 5 || data[i + 2] > 5) colorCount++;
            }

            if (colorCount >= 20) {
              setReady(true);
              return; // canvas has real content — signal Playwright
            }
          }
        } catch {
          // getImageData can throw on tainted canvases — skip and retry
        }
      }

      if (attempts < MAX_SETTLE_FRAMES) {
        rafId = requestAnimationFrame(poll);
      } else {
        // Cap reached — mark ready so the spec's content guard applies its verdict
        setReady(true);
      }
    }

    rafId = requestAnimationFrame(poll);
    return () => cancelAnimationFrame(rafId);
  }, []);

  return (
    <div style={{ background: '#000', width: '100vw', height: '100vh', display: 'flex', flexDirection: 'column', alignItems: 'center' }}>
      {/* Fixed-size container — 1440×900 gives the canvas a definite layout
          size for getBoundingClientRect() in headless Chromium (DPR=1). */}
      <div
        data-testid="vista-proof-container"
        style={{ width: 1440, height: 900, position: 'relative', marginTop: 20 }}
      >
        <VistaCanvas input={activeInput} clock={activeClock} />
      </div>

      <div style={{ color: '#666', fontSize: 11, fontFamily: 'monospace', marginTop: 8 }}>
        Vista Proof &nbsp;|&nbsp; {activeLabel} &nbsp;|&nbsp; seed: {activeInput.seed} &nbsp;|&nbsp;
        {isNight ? ` t=${activeClock.toFixed(1)}s (night/3am)` : ' t=0 (frozen/day)'} &nbsp;|&nbsp; DEV-only
      </div>

      {/* Playwright readiness gate: appears only after the canvas pixel poll
          confirms non-black content.  Playwright waits on this element.
          The spec then reads the canvas via toDataURL() (direct buffer read,
          not compositor capture) to avoid any compositor-timing races. */}
      {ready && <div data-testid="vista-proof-ready" style={{ display: 'none' }} aria-hidden="true" />}
    </div>
  );
}

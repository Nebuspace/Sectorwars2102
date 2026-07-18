import { useEffect, useRef, type RefObject } from 'react';
import { SeededRng, deriveChildSeed } from '../../vista/core/rng';
import type { SystemBody } from './SolarSystemViewscreen';
import { bodyPosition, bodySizeEm, type StarAnchor, type SafeOrbitRadii } from './windshieldTableauLayout';
import { useTableauFx, type TableauFxHarness, type TableauFxMapper, type PctPoint } from './tableauFxHarness';

/**
 * drawPlanetTableau — WO-AAA-SOLAR-TABLEAU phase 2 (Planet-2D). Per the design
 * brief (`audit/design-briefs/aaa-solar-tableau-2026-07-18.md` §"KEY: an AAA
 * planet renderer ALREADY EXISTS"), this is mostly a PORT of the proven
 * per-type treatments already shipping in `SolarSystemViewscreen.tsx`'s
 * orbit-closeup/landed canvas — extracted and adapted here rather than pulled
 * in-place from that (already over the 1500-line TS cap) walled-off file, so
 * the flight tableau's overlay canvas gets the same fidelity without growing
 * either file further. Ported functions cite their SolarSystemViewscreen.tsx
 * source lines; net-new effects (independent cloud-drift, night-side city
 * lights) are marked as such.
 *
 * FLAG (duplication, not resolved here — matches the brief's own call):
 * `treatmentFor`/`drawPlanetSurface`/`drawRingHalf`/`drawFormingEffect` now
 * exist in TWO places (SolarSystemViewscreen.tsx's closeup/landed renderer
 * and this file's flight-tableau renderer). A future consolidation could
 * hoist the shared treatment logic into one module both import — left for a
 * later pass since the brief explicitly chose "port" over "extract-in-place
 * from the walled-off file" as the lower-risk path for this WO.
 *
 * Consumes the shared `tableauFxHarness` clock (WO phase 1) so this canvas's
 * implied light direction and the sun's (Phase 3, separate WebGL canvas)
 * agree frame-to-frame — both register against the SAME `t`.
 */

// Same seed namespace windshieldTableauLayout.ts's moonOrbits/scanPosition
// already use for this component family, so every seeded visual stream in
// the tableau (moons, scan glyphs, planet surfaces) lives under one root and
// can never accidentally collide with an unrelated system's seed stream.
const NS = 'windshield-tableau';

// ---------------------------------------------------------------------------
// Ported constants (SolarSystemViewscreen.tsx:415)
// ---------------------------------------------------------------------------

/** Planets rotating on their own axis (calm, per-planet rate). */
const SPIN_SCALE = 0.5;

/** NET-NEW: the independent cloud-drift layer's own rate multiplier — bigger
 *  than 1 so clouds visibly slide across the (also-rotating) surface below
 *  rather than riding in lockstep with it. */
const CLOUD_DRIFT_RATE = 1.65;

/** Vertical squash applied to the forming-effect's spiralling dust motes,
 *  matching SolarSystemViewscreen.tsx:406's own orbital-ellipse squash. */
const SQUASH = 0.35;

// ---------------------------------------------------------------------------
// Surface treatments — ported from SolarSystemViewscreen.tsx:558-571
// ---------------------------------------------------------------------------

export type Treatment = 'GAS_GIANT' | 'BARREN' | 'ICE' | 'VOLCANIC' | 'DESERT' | 'TERRAN' | 'OCEANIC';

/** Ported verbatim (SolarSystemViewscreen.tsx:560) — the switch never
 *  returns 'MOUNTAINOUS' (that source's Treatment union carries it only for
 *  its own PROC_FLAVOR/popup-text table, which this renderer has no need
 *  of), so this module's Treatment type is narrowed to the 7 kinds actually
 *  produced. */
export function treatmentFor(kind: string): Treatment {
  const k = (kind || '').toUpperCase().replace('PLANETTYPE.', '');
  switch (k) {
    case 'GAS_GIANT': return 'GAS_GIANT';
    case 'ICE': case 'FROZEN': case 'ARCTIC': case 'C_CLASS': return 'ICE';
    case 'VOLCANIC': case 'H_CLASS': return 'VOLCANIC';
    case 'DESERT': case 'K_CLASS': return 'DESERT';
    case 'TERRAN': case 'TERRA': case 'TROPICAL': case 'M_CLASS': case 'JUNGLE': return 'TERRAN';
    case 'OCEANIC': case 'O_CLASS': return 'OCEANIC';
    default: return 'BARREN'; // BARREN, MOUNTAINOUS, D_CLASS, unknown
  }
}

// ---------------------------------------------------------------------------
// Atmosphere halo — NEW wiring (the ported source's own atmosphere gradient,
// SolarSystemViewscreen.tsx:1448, only ever fires from the orbit-closeup
// view; the flight tableau's per-body loop never called it at all). Scaled
// by type/habitability per the brief, instead of the ported code's one flat
// alpha for every world.
// ---------------------------------------------------------------------------

const ATMO_BASE_ALPHA: Record<Treatment, number> = {
  GAS_GIANT: 0.30,
  TERRAN: 0.22,
  OCEANIC: 0.24,
  ICE: 0.14,
  DESERT: 0.12,
  VOLCANIC: 0.08,
  BARREN: 0.04,
};

function drawAtmosphereHalo(
  ctx: CanvasRenderingContext2D,
  x: number,
  y: number,
  r: number,
  hue: number,
  treatment: Treatment,
  habitability?: number,
): void {
  const habBonus = (treatment === 'TERRAN' || treatment === 'OCEANIC') && typeof habitability === 'number'
    ? (habitability / 100) * 0.14
    : 0;
  const alpha = ATMO_BASE_ALPHA[treatment] + habBonus;
  if (alpha <= 0.01) return; // airless rock — not worth the extra draw call
  const atm = ctx.createRadialGradient(x, y, r * 0.92, x, y, r * 1.4);
  atm.addColorStop(0, `hsla(${hue}, 70%, 62%, ${alpha})`);
  atm.addColorStop(1, `hsla(${hue}, 70%, 62%, 0)`);
  ctx.fillStyle = atm;
  ctx.beginPath();
  ctx.arc(x, y, r * 1.4, 0, Math.PI * 2);
  ctx.fill();
}

// ---------------------------------------------------------------------------
// Rings — ported verbatim (SolarSystemViewscreen.tsx:925-946)
// ---------------------------------------------------------------------------

/** Tilted ring ellipse — half='back' draws behind the planet, half='front'
 *  draws over it (caller invokes both, planet surface drawn in between, for
 *  correct occlusion). */
function drawRingHalfTableau(
  ctx: CanvasRenderingContext2D,
  x: number,
  y: number,
  r: number,
  hue: number,
  tilt: number,
  half: 'back' | 'front'
): void {
  const start = half === 'back' ? Math.PI : 0;
  const end = half === 'back' ? Math.PI * 2 : Math.PI;
  ctx.beginPath();
  ctx.ellipse(x, y, r * 1.9, r * 0.55, tilt, start, end);
  ctx.strokeStyle = `hsla(${hue + 30}, 38%, 70%, 0.5)`;
  ctx.lineWidth = Math.max(1, r * 0.16);
  ctx.stroke();
  ctx.beginPath();
  ctx.ellipse(x, y, r * 1.62, r * 0.46, tilt, start, end);
  ctx.strokeStyle = `hsla(${hue + 30}, 30%, 82%, 0.35)`;
  ctx.lineWidth = Math.max(0.6, r * 0.06);
  ctx.stroke();
}

// ---------------------------------------------------------------------------
// Genesis terraforming overlay — ported from SolarSystemViewscreen.tsx:854-
// 922, minus its own "GENESIS FORMING…" text label: this canvas layer sits
// UNDER the DOM `.pltag` (WindshieldTableau.tsx), which already renders the
// body's name at the same anchor — porting a second canvas-drawn text block
// at the same `y + r + 6` offset would collide with/duplicate it. The visual
// effect (halo, spiralling dust, pulsing containment ring) is preserved in
// full; only the redundant text draw is trimmed.
// ---------------------------------------------------------------------------

function drawFormingEffectTableau(
  ctx: CanvasRenderingContext2D,
  x: number,
  y: number,
  r: number,
  hue: number,
  t: number,
  seed: number,
): void {
  const rng = new SeededRng((seed ^ 0x6e617363) >>> 0);
  const pulseA = 0.5 + 0.5 * Math.sin(t * 2.1);
  const pulseB = 0.5 + 0.5 * Math.sin(t * 1.7 + 1.2);

  ctx.save();
  // Accretion halo (additive glow around the nascent world).
  ctx.globalCompositeOperation = 'lighter';
  const halo = ctx.createRadialGradient(x, y, r * 0.8, x, y, r * 1.9);
  halo.addColorStop(0, `hsla(${hue}, 80%, 70%, ${0.18 * pulseA})`);
  halo.addColorStop(0.5, `hsla(${hue + 30}, 60%, 55%, ${0.10 * pulseB})`);
  halo.addColorStop(1, `hsla(${hue}, 70%, 50%, 0)`);
  ctx.fillStyle = halo;
  ctx.beginPath();
  ctx.arc(x, y, r * 1.9, 0, Math.PI * 2);
  ctx.fill();

  // Dust motes spiralling inward (each on its own infall phase).
  const PARTICLES = 22;
  for (let i = 0; i < PARTICLES; i++) {
    const base = rng.next01() * Math.PI * 2;
    const speed = 0.3 + rng.next01() * 0.5;
    const phase = (t * 0.09 + rng.next01()) % 1;
    const ang = base + t * speed;
    const dist = r * (2.0 - 0.95 * phase);
    const px = x + Math.cos(ang) * dist;
    const py = y + Math.sin(ang) * dist * SQUASH;
    ctx.globalAlpha = phase * 0.85;
    ctx.fillStyle = `hsl(${hue + rng.next01() * 40 - 20}, 90%, 75%)`;
    ctx.beginPath();
    ctx.arc(px, py, 0.8 + rng.next01() * 1.2, 0, Math.PI * 2);
    ctx.fill();
  }
  ctx.restore();

  // Pulsing dashed containment ring.
  ctx.save();
  ctx.globalAlpha = 0.55 + 0.45 * pulseA;
  ctx.strokeStyle = `hsl(${hue}, 85%, 72%)`;
  ctx.lineWidth = 1.6;
  ctx.setLineDash([4, 3]);
  ctx.beginPath();
  ctx.arc(x, y, r + 4 + pulseB * 3, 0, Math.PI * 2);
  ctx.stroke();
  ctx.restore();
}

// ---------------------------------------------------------------------------
// NET-NEW: independent cloud-drift (TERRAN/OCEANIC only) — a second cloud
// layer that rotates at its OWN rate, decoupled from the surface's own axial
// spin above, so it visibly slides across the continents/ocean beneath it
// rather than riding fixed to them like the ported treatment's own static
// cloud flecks (baked into the TERRAN/OCEANIC switch cases inside
// drawPlanetSurfaceTableau, same as the proven renderer). Called while still
// inside the caller's disc clip, right after the axial-spin transform is
// popped, with its own independent rotate.
// ---------------------------------------------------------------------------

function drawCloudDrift(
  ctx: CanvasRenderingContext2D,
  x: number,
  y: number,
  r: number,
  tiltRad: number,
  t: number,
  seed: number,
  rotH: number,
): void {
  const rng = new SeededRng(deriveChildSeed(NS, `cloud-drift:${seed}`));
  const driftSpin = (t * SPIN_SCALE * CLOUD_DRIFT_RATE * Math.PI * 2) / Math.max(1, rotH * 4);
  ctx.save();
  ctx.translate(x, y);
  ctx.rotate(tiltRad + driftSpin);
  ctx.translate(-x, -y);
  const flecks = 4 + Math.floor(rng.next01() * 5);
  for (let i = 0; i < flecks; i++) {
    const a = rng.next01() * Math.PI * 2;
    const d = rng.next01() * r * 0.88;
    ctx.beginPath();
    ctx.ellipse(
      x + Math.cos(a) * d, y + Math.sin(a) * d,
      r * (0.11 + rng.next01() * 0.13), r * 0.05, rng.next01() * Math.PI, 0, Math.PI * 2
    );
    ctx.fillStyle = 'rgba(255, 255, 255, 0.36)';
    ctx.fill();
  }
  ctx.restore();
}

// ---------------------------------------------------------------------------
// NET-NEW: night-side city lights (`body.owned` only) — "the one genuinely-
// new planet effect" per the brief. Each light has a FIXED angle in the
// surface's own rotating frame (the same tiltRad+spin the switch-case
// continents above rotate under) but is only painted once its CURRENT screen
// angle has turned onto the dark hemisphere (a cos test against the star
// direction) — so lights visibly sweep into/out of view as the world turns,
// rather than a static scatter that ignores day/night. Positions are
// computed with plain trig (not a ctx.rotate transform) since each dot needs
// its own live visibility test before it's worth drawing at all. Called
// AFTER the terminator's dark overlay is painted, so lights read as a light
// SOURCE popping through the dark rather than being dimmed by it.
// ---------------------------------------------------------------------------

function drawCityLights(
  ctx: CanvasRenderingContext2D,
  x: number,
  y: number,
  r: number,
  tiltRad: number,
  spin: number,
  angToStar: number,
  seed: number,
): void {
  const rng = new SeededRng(deriveChildSeed(NS, `city-lights:${seed}`));
  const count = 10 + Math.floor(rng.next01() * 12);
  ctx.save();
  ctx.globalCompositeOperation = 'lighter';
  for (let i = 0; i < count; i++) {
    const localAngle = rng.next01() * Math.PI * 2;
    const dist = rng.next01() * r * 0.86;
    const size = 0.4 + rng.next01() * 0.6;
    const warmth = rng.next01();
    const screenAngle = localAngle + tiltRad + spin;
    // cos(delta from the star direction): +1 facing the star (noon) .. -1
    // directly opposite (deep night). -0.15 gives a small buffer past the
    // exact terminator line before a light switches on, so none flicker
    // right at the day/night edge.
    if (Math.cos(screenAngle - angToStar) > -0.15) continue;
    const lx = x + Math.cos(screenAngle) * dist;
    const ly = y + Math.sin(screenAngle) * dist;
    ctx.beginPath();
    ctx.arc(lx, ly, size, 0, Math.PI * 2);
    ctx.fillStyle = `hsla(${36 + warmth * 24}, 90%, ${62 + warmth * 15}%, ${0.55 + warmth * 0.35})`;
    ctx.fill();
  }
  ctx.restore();
}

// ---------------------------------------------------------------------------
// Planet surface — ported from SolarSystemViewscreen.tsx:613-849
// (drawPlanetSurface), with the two net-new layers above spliced in at the
// same insertion points the brief calls out: cloud-drift right after the
// axial-spin transform pops (still disc-clipped, star-fixed baseline before
// its own independent rotate), city-lights right after the day/night
// terminator gradient is painted (still disc-clipped).
// ---------------------------------------------------------------------------

function drawPlanetSurfaceTableau(
  ctx: CanvasRenderingContext2D,
  body: SystemBody,
  treatment: Treatment,
  x: number,
  y: number,
  r: number,
  starX: number,
  starY: number,
  seed: number,
  t: number,
): void {
  const rng = new SeededRng(seed);
  const hue = body.palette.hue;
  const sat = body.palette.sat;

  ctx.save();
  ctx.beginPath();
  ctx.arc(x, y, r, 0, Math.PI * 2);
  ctx.clip();

  // --- Axial rotation: spin the surface beneath the (fixed) day/night
  // lighting so the world visibly turns on its own tilted axis. Per-planet
  // rate (rotation_period_hours — gas giants fast, big worlds slow), so no
  // two worlds spin in lockstep; axial_tilt_deg skews the spin axis. Falls
  // back to a seed-derived value for skeletons that predate the fields.
  const rotH = body.rotation_period_hours ?? (12 + (seed % 36));
  const tiltRad = ((body.axial_tilt_deg ?? (seed % 46)) * Math.PI) / 180;
  const spin = (t * SPIN_SCALE * Math.PI * 2) / Math.max(1, rotH * 4);
  ctx.save();
  ctx.translate(x, y);
  ctx.rotate(tiltRad + spin);
  ctx.translate(-x, -y);

  switch (treatment) {
    case 'GAS_GIANT': {
      ctx.fillStyle = `hsl(${hue}, ${sat}%, 38%)`;
      ctx.fillRect(x - r, y - r, r * 2, r * 2);
      const bands = 3 + Math.floor(rng.next01() * 4); // 3-6 bands
      const bandH = (r * 2) / bands;
      for (let i = 0; i < bands; i++) {
        const hueShift = (rng.next01() - 0.5) * 34;
        const light = 30 + rng.next01() * 22;
        ctx.fillStyle = `hsla(${hue + hueShift}, ${sat}%, ${light}%, 0.8)`;
        ctx.fillRect(x - r, y - r + i * bandH, r * 2, bandH * (0.7 + rng.next01() * 0.3));
      }
      if (rng.next01() < 0.45) {
        // Oval storm spot
        ctx.beginPath();
        ctx.ellipse(
          x + (rng.next01() - 0.5) * r * 1.1,
          y + (rng.next01() - 0.5) * r * 0.9,
          r * 0.24, r * 0.12, 0.15, 0, Math.PI * 2
        );
        ctx.fillStyle = `hsla(${hue + 25}, ${Math.min(100, sat + 12)}%, 62%, 0.85)`;
        ctx.fill();
      }
      break;
    }
    case 'BARREN': {
      ctx.fillStyle = `hsl(${hue}, ${Math.round(sat * 0.35)}%, 36%)`;
      ctx.fillRect(x - r, y - r, r * 2, r * 2);
      const craters = 8 + Math.floor(rng.next01() * 9);
      for (let i = 0; i < craters; i++) {
        const a = rng.next01() * Math.PI * 2;
        const d = rng.next01() * r * 0.85;
        const cr2 = r * (0.05 + rng.next01() * 0.11);
        ctx.beginPath();
        ctx.arc(x + Math.cos(a) * d, y + Math.sin(a) * d, cr2, 0, Math.PI * 2);
        ctx.fillStyle = `hsla(${hue}, ${Math.round(sat * 0.3)}%, ${18 + rng.next01() * 10}%, 0.7)`;
        ctx.fill();
      }
      break;
    }
    case 'ICE': {
      ctx.fillStyle = `hsl(${hue}, ${Math.round(sat * 0.5)}%, 76%)`;
      ctx.fillRect(x - r, y - r, r * 2, r * 2);
      // Brighter polar caps
      ctx.fillStyle = 'rgba(255, 255, 255, 0.75)';
      ctx.beginPath();
      ctx.ellipse(x, y - r * 0.82, r * 0.7, r * 0.3, 0, 0, Math.PI * 2);
      ctx.fill();
      ctx.beginPath();
      ctx.ellipse(x, y + r * 0.82, r * 0.7, r * 0.3, 0, 0, Math.PI * 2);
      ctx.fill();
      // Faint crack lines
      const cracks = 3 + Math.floor(rng.next01() * 3);
      ctx.strokeStyle = `hsla(${hue}, 45%, 52%, 0.5)`;
      ctx.lineWidth = Math.max(0.6, r * 0.04);
      for (let i = 0; i < cracks; i++) {
        let cx = x + (rng.next01() - 0.5) * r;
        let cy = y + (rng.next01() - 0.5) * r;
        ctx.beginPath();
        ctx.moveTo(cx, cy);
        const segs = 3 + Math.floor(rng.next01() * 2);
        for (let s = 0; s < segs; s++) {
          cx += (rng.next01() - 0.5) * r * 0.8;
          cy += (rng.next01() - 0.5) * r * 0.5;
          ctx.lineTo(cx, cy);
        }
        ctx.stroke();
      }
      break;
    }
    case 'VOLCANIC': {
      ctx.fillStyle = `hsl(${hue}, 30%, 13%)`;
      ctx.fillRect(x - r, y - r, r * 2, r * 2);
      // Glowing fissures (additive)
      ctx.save();
      ctx.globalCompositeOperation = 'lighter';
      const fissures = 3 + Math.floor(rng.next01() * 4);
      ctx.lineWidth = Math.max(0.7, r * 0.05);
      for (let i = 0; i < fissures; i++) {
        let fx = x + (rng.next01() - 0.5) * r * 1.2;
        let fy = y + (rng.next01() - 0.5) * r * 1.2;
        ctx.strokeStyle = `hsla(${14 + rng.next01() * 14}, 95%, ${48 + rng.next01() * 14}%, 0.85)`;
        ctx.beginPath();
        ctx.moveTo(fx, fy);
        const segs = 3 + Math.floor(rng.next01() * 2);
        for (let s = 0; s < segs; s++) {
          fx += (rng.next01() - 0.5) * r * 0.7;
          fy += (rng.next01() - 0.5) * r * 0.6;
          ctx.lineTo(fx, fy);
        }
        ctx.stroke();
      }
      // Ember glow haze
      const ember = ctx.createRadialGradient(x, y, r * 0.1, x, y, r);
      ember.addColorStop(0, 'rgba(255, 90, 20, 0.18)');
      ember.addColorStop(1, 'rgba(255, 60, 0, 0)');
      ctx.fillStyle = ember;
      ctx.fillRect(x - r, y - r, r * 2, r * 2);
      ctx.restore();
      break;
    }
    case 'DESERT': {
      ctx.fillStyle = `hsl(${hue}, ${sat}%, 54%)`;
      ctx.fillRect(x - r, y - r, r * 2, r * 2);
      // Warm dune bands
      const dunes = 3 + Math.floor(rng.next01() * 2);
      const dh = (r * 2) / dunes;
      for (let i = 0; i < dunes; i++) {
        ctx.fillStyle = `hsla(${hue + (rng.next01() - 0.5) * 16}, ${sat}%, ${44 + rng.next01() * 18}%, 0.45)`;
        ctx.fillRect(x - r, y - r + i * dh + (rng.next01() - 0.5) * dh * 0.3, r * 2, dh * 0.6);
      }
      // Lighter mottling
      const motts = 6 + Math.floor(rng.next01() * 6);
      for (let i = 0; i < motts; i++) {
        const a = rng.next01() * Math.PI * 2;
        const d = rng.next01() * r * 0.8;
        ctx.beginPath();
        ctx.arc(x + Math.cos(a) * d, y + Math.sin(a) * d, r * (0.04 + rng.next01() * 0.07), 0, Math.PI * 2);
        ctx.fillStyle = `hsla(${hue}, ${Math.round(sat * 0.7)}%, 70%, 0.5)`;
        ctx.fill();
      }
      break;
    }
    case 'TERRAN': {
      // Living world — ocean base, green continents, cloud flecks.
      const tHue = 208 + ((hue % 24) - 12) * 0.5;
      const tLight = 38 + (rng.next01() - 0.5) * 6;
      ctx.fillStyle = `hsl(${tHue}, 64%, ${tLight}%)`;
      ctx.fillRect(x - r, y - r, r * 2, r * 2);
      const continents = 4 + Math.floor(rng.next01() * 4);
      for (let i = 0; i < continents; i++) {
        const a = rng.next01() * Math.PI * 2;
        const d = rng.next01() * r * 0.75;
        ctx.beginPath();
        ctx.arc(x + Math.cos(a) * d, y + Math.sin(a) * d, r * (0.14 + rng.next01() * 0.2), 0, Math.PI * 2);
        ctx.fillStyle = `hsla(${110 + rng.next01() * 30}, 42%, ${30 + rng.next01() * 12}%, 0.9)`;
        ctx.fill();
      }
      const clouds = 5 + Math.floor(rng.next01() * 5);
      for (let i = 0; i < clouds; i++) {
        const a = rng.next01() * Math.PI * 2;
        const d = rng.next01() * r * 0.85;
        ctx.beginPath();
        ctx.ellipse(x + Math.cos(a) * d, y + Math.sin(a) * d, r * (0.1 + rng.next01() * 0.12), r * 0.05, rng.next01() * Math.PI, 0, Math.PI * 2);
        ctx.fillStyle = 'rgba(255, 255, 255, 0.45)';
        ctx.fill();
      }
      break;
    }
    case 'OCEANIC': {
      const oHue = 214 + ((hue % 24) - 12) * 0.5;
      const oLight = 36 + (rng.next01() - 0.5) * 6;
      ctx.fillStyle = `hsl(${oHue}, 70%, ${oLight}%)`;
      ctx.fillRect(x - r, y - r, r * 2, r * 2);
      // Sparse island chains
      const islands = 2 + Math.floor(rng.next01() * 3);
      for (let i = 0; i < islands; i++) {
        const a = rng.next01() * Math.PI * 2;
        const d = rng.next01() * r * 0.7;
        ctx.beginPath();
        ctx.arc(x + Math.cos(a) * d, y + Math.sin(a) * d, r * (0.05 + rng.next01() * 0.07), 0, Math.PI * 2);
        ctx.fillStyle = 'hsla(42, 45%, 55%, 0.85)';
        ctx.fill();
      }
      const clouds = 4 + Math.floor(rng.next01() * 5);
      for (let i = 0; i < clouds; i++) {
        const a = rng.next01() * Math.PI * 2;
        const d = rng.next01() * r * 0.85;
        ctx.beginPath();
        ctx.ellipse(x + Math.cos(a) * d, y + Math.sin(a) * d, r * (0.1 + rng.next01() * 0.12), r * 0.05, rng.next01() * Math.PI, 0, Math.PI * 2);
        ctx.fillStyle = 'rgba(255, 255, 255, 0.4)';
        ctx.fill();
      }
      break;
    }
  }

  ctx.restore(); // end axial-spin transform — terminator + city-lights + rim stay star-fixed

  // NET-NEW: independent cloud-drift — its own rotate, still disc-clipped.
  if (treatment === 'TERRAN' || treatment === 'OCEANIC') {
    drawCloudDrift(ctx, x, y, r, tiltRad, t, seed, rotH);
  }

  // --- Day/night terminator: dark gradient on the side away from the star ---
  const angToStar = Math.atan2(starY - y, starX - x);
  const lx = x + Math.cos(angToStar) * r;
  const ly = y + Math.sin(angToStar) * r;
  const dxx = x - Math.cos(angToStar) * r;
  const dyy = y - Math.sin(angToStar) * r;
  const term = ctx.createLinearGradient(lx, ly, dxx, dyy);
  term.addColorStop(0, 'rgba(0, 0, 18, 0)');
  term.addColorStop(0.55, 'rgba(0, 0, 18, 0.12)');
  term.addColorStop(1, 'rgba(0, 0, 18, 0.72)');
  ctx.fillStyle = term;
  ctx.fillRect(x - r, y - r, r * 2, r * 2);

  // NET-NEW: night-side city lights for claimed worlds.
  if (body.owned) {
    drawCityLights(ctx, x, y, r, tiltRad, spin, angToStar, seed);
  }

  ctx.restore(); // pop the disc clip

  // --- 1px rim light toward the star ---
  ctx.beginPath();
  ctx.arc(x, y, Math.max(0.5, r - 0.5), angToStar - 1.05, angToStar + 1.05);
  ctx.strokeStyle = 'rgba(255, 255, 255, 0.35)';
  ctx.lineWidth = 1;
  ctx.stroke();
}

// ---------------------------------------------------------------------------
// Public draw entry — one call per frame, orchestrating every layer above
// for every body. Pure function of its arguments (no DOM/React) so it's
// directly unit-testable against a mock 2D context.
// ---------------------------------------------------------------------------

/** One body's already-resolved tableau placement — `xPct`/`yPct` from the
 *  SAME `bodyPosition()` WindshieldTableau.tsx's own `.pl` DOM button uses
 *  (called with the identical `star`/`safeRadii` inputs, so the canvas-drawn
 *  disc can never drift from its DOM sibling — both are the same pure
 *  function evaluated on the same inputs), `rPx` the disc's rendered radius
 *  in CSS px (mirrors the `.sun` star-tag's own `(sizeEm/2)*bandBox.remPx`
 *  conversion, WindshieldTableau.tsx:1768). */
export interface TableauPlanetBody {
  body: SystemBody;
  xPct: number;
  yPct: number;
  rPx: number;
}

export function drawPlanetTableau(
  ctx: CanvasRenderingContext2D,
  sectorId: number,
  planets: TableauPlanetBody[],
  t: number,
  mapper: TableauFxMapper,
  star: PctPoint | null,
): void {
  const starPx = star ? mapper(star.xPct, star.yPct) : null;
  for (const { body, xPct, yPct, rPx } of planets) {
    if (rPx <= 0) continue;
    const { x, y } = mapper(xPct, yPct);
    // Light source = the star's own mapped position (SolarSystemViewscreen.
    // tsx:834's terminator convention). A star-less snapshot (rare/edge —
    // SystemSnapshot.star is nullable) falls back to a light source off-
    // screen upper-left, matching drawOrbitCloseup's own off-screen-light
    // fallback idiom (SolarSystemViewscreen.tsx:1443).
    const starX = starPx ? starPx.x : x - 400;
    const starY = starPx ? starPx.y : y - 200;
    const seed = deriveChildSeed(NS, `planet-surface:${sectorId}:${body.slot}`);
    const treatment = treatmentFor(body.kind);
    const ringTilt = -0.32 + ((seed % 100) / 100 - 0.5) * 0.3;
    const forming = body.formation_status === 'forming';

    drawAtmosphereHalo(ctx, x, y, rPx, body.palette.hue, treatment, body.habitability);
    if (body.rings) drawRingHalfTableau(ctx, x, y, rPx, body.palette.hue, ringTilt, 'back');
    if (forming) {
      // The nascent world shows through faintly while it coalesces (matches
      // SolarSystemViewscreen.tsx:1799-1805's own dim-and-overlay).
      ctx.save();
      ctx.globalAlpha = 0.35 + 0.15 * Math.sin(t * 1.2);
      drawPlanetSurfaceTableau(ctx, body, treatment, x, y, rPx, starX, starY, seed, t);
      ctx.restore();
      drawFormingEffectTableau(ctx, x, y, rPx, body.palette.hue, t, seed);
    } else {
      drawPlanetSurfaceTableau(ctx, body, treatment, x, y, rPx, starX, starY, seed, t);
    }
    if (body.rings) drawRingHalfTableau(ctx, x, y, rPx, body.palette.hue, ringTilt, 'front');
  }
}

// ---------------------------------------------------------------------------
// React wrapper — owns its own canvas + harness registration so Phase 3
// (wiring this into WindshieldTableau.tsx) only has to mount one component
// with the same star/safeRadii/bandBox values it already computes for the
// DOM `.pl`/`.sun` buttons, rather than hand-rolling harness plumbing.
// ---------------------------------------------------------------------------

export interface PlanetTableauLayerProps {
  /** Same containerRef WindshieldTableau.tsx passes to its own `.ssv-tableau`
   *  root — the box every %-anchor (`.sun`/`.pl`) is positioned against. */
  containerRef: RefObject<HTMLElement | null>;
  /** WO-AAA-SOLAR-TABLEAU phase 3: the ONE shared tableauFxHarness instance
   *  (WindshieldTableau's own `useTableauFx(sceneSpaceRef)`, registered once
   *  and handed to both this layer and `StarDisc` so the sun's implied
   *  light direction and the planets' terminator/rim never phase-drift onto
   *  two independent clocks). `undefined` (prop omitted entirely) keeps this
   *  component's original standalone behavior — it creates + owns its OWN
   *  harness off `containerRef`, for any caller that mounts this layer on
   *  its own. `null` (prop passed, not yet ready — e.g. the parent's own
   *  `useTableauFx` hasn't mounted yet) waits rather than falling back, so a
   *  caller that opted into sharing never accidentally spins up a second,
   *  independent clock during the one-render startup gap. */
  harness?: TableauFxHarness | null;
  sectorId: number;
  bodies: SystemBody[];
  /** WindshieldTableau.tsx's own `star` memo (starAnchor(...)) — always a
   *  resolved StarAnchor, never null (starAnchor itself absorbs a null
   *  system.star with a fallback factor). */
  star: StarAnchor;
  safeRadii?: SafeOrbitRadii;
  /** px-per-1em at the tableau's own font-size context — WindshieldTableau.
   *  tsx's own `bandBox?.remPx` (BandGeometry, measured via ResizeObserver).
   *  Undefined before the first measurement renders nothing rather than
   *  guessing a size (matches every other "no band yet" no-op convention in
   *  this file family, e.g. `bandBox ?? undefined` at WindshieldTableau.tsx:
   *  1768/1811). */
  remPx: number | undefined;
}

export function PlanetTableauLayer({
  containerRef, harness: sharedHarness, sectorId, bodies, star, safeRadii, remPx,
}: PlanetTableauLayerProps) {
  const canvasRef = useRef<HTMLCanvasElement>(null);

  // `harness` prop omitted entirely (`undefined`) -> standalone mode, self-
  // create off `containerRef` exactly as before. Prop passed (even `null`,
  // still-mounting) -> shared mode, this `useTableauFx(containerRef)` call
  // is kept (rules of hooks -- always called) but pointed at a ref that's
  // NEVER attached to a DOM node, so its own mount effect's `if (!el)
  // return;` permanently no-ops (no second rAF/ResizeObserver ever spins
  // up) — see this prop's own doc-comment on the `undefined` vs `null`
  // distinction.
  const standalone = sharedHarness === undefined;
  const unusedContainerRef = useRef<HTMLElement | null>(null);
  const ownHarness = useTableauFx(standalone ? containerRef : unusedContainerRef);
  const harness = standalone ? ownHarness : (sharedHarness ?? null);

  // Latest props read by the registered draw callback via a ref — the ONE
  // registration made on mount never needs to re-register just because a
  // fresh system snapshot arrived; the harness calls `draw` with a fresh
  // `t`/`mapper` every frame regardless.
  const latestRef = useRef({ sectorId, bodies, star, safeRadii, remPx });
  latestRef.current = { sectorId, bodies, star, safeRadii, remPx };

  useEffect(() => {
    if (!harness || !canvasRef.current) return;
    const canvas = canvasRef.current;
    return harness.register(canvas, (t, mapper, size) => {
      const ctx = canvas.getContext('2d');
      if (!ctx) return;
      // Backing store is device-px (manageSize:true); every coordinate this
      // module computes is CSS-px (mapper's own contract) — the same
      // setTransform(dpr,...) convention SolarSystemViewscreen.tsx:7861 uses
      // for its own canvas.
      ctx.setTransform(size.dpr, 0, 0, size.dpr, 0, 0);
      ctx.clearRect(0, 0, size.cssWidth, size.cssHeight);
      const live = latestRef.current;
      if (!live.remPx) return; // band not yet measured — nothing to draw
      const planets: TableauPlanetBody[] = live.bodies.map((body) => {
        const pos = bodyPosition(live.star, body, live.safeRadii);
        return { body, xPct: pos.xPct, yPct: pos.yPct, rPx: (bodySizeEm(body) / 2) * (live.remPx as number) };
      });
      drawPlanetTableau(ctx, live.sectorId, planets, t, mapper, live.star);
    });
  }, [harness]);

  return (
    <canvas
      ref={canvasRef}
      className="planet-tableau-fx"
      style={{ position: 'absolute', inset: 0, width: '100%', height: '100%', pointerEvents: 'none' }}
      aria-hidden="true"
    />
  );
}

/**
 * Vista Proof Harness — DEV-only, dead-code-eliminated from prod builds.
 *
 * Renders a fixed VistaInput at clock=0 (frozen frame) for deterministic
 * Playwright screenshot comparison.  Input is hardcoded here — no UI toggles,
 * no URL params — so every run is byte-identical.
 *
 * Route: /lab/vista-proof  (App.tsx registers it inside import.meta.env.DEV)
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

// ---------------------------------------------------------------------------
// Fixed proof input
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
// Component
// ---------------------------------------------------------------------------

// Maximum rAF iterations before giving up and marking ready anyway (so the
// spec's content guard applies its verdict rather than hanging indefinitely).
const MAX_SETTLE_FRAMES = 60;

export default function VistaProof() {
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
      {/* Fixed-size container — 900×560 gives the canvas a definite layout
          size for getBoundingClientRect() in headless Chromium (DPR=1). */}
      <div
        data-testid="vista-proof-container"
        style={{ width: 900, height: 560, position: 'relative', marginTop: 20 }}
      >
        <VistaCanvas input={PROOF_INPUT} clock={0} />
      </div>

      <div style={{ color: '#666', fontSize: 11, fontFamily: 'monospace', marginTop: 8 }}>
        Vista Proof &nbsp;|&nbsp; seed: {PROOF_INPUT.seed} &nbsp;|&nbsp; t=0 (frozen) &nbsp;|&nbsp; DEV-only
      </div>

      {/* Playwright readiness gate: appears only after the canvas pixel poll
          confirms non-black content.  Playwright waits on this element.
          The spec then reads the canvas via toDataURL() (direct buffer read,
          not compositor capture) to avoid any compositor-timing races. */}
      {ready && <div data-testid="vista-proof-ready" style={{ display: 'none' }} aria-hidden="true" />}
    </div>
  );
}

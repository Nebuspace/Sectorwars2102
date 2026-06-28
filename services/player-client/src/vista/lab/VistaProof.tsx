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
 *   VistaCanvas mounts and paints synchronously in its useEffect.
 *   VistaProof's useEffect schedules a requestAnimationFrame that fires
 *   AFTER that first canvas paint.  When it fires, [data-testid="vista-proof-ready"]
 *   is added to the DOM.  Playwright waits for that element before screenshotting.
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

export default function VistaProof() {
  // Readiness gate for Playwright.  The rAF fires after the canvas2d backend
  // has flushed its first drawHazardGlyph calls, so the DOM marker appears
  // only after the canvas visually contains the hazard glyphs.
  const [ready, setReady] = useState(false);
  useEffect(() => {
    const id = requestAnimationFrame(() => setReady(true));
    return () => cancelAnimationFrame(id);
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

      {/* Playwright readiness gate: rendered in the DOM after one rAF,
          by which time the canvas has painted its initial frame.
          Playwright: await page.locator('[data-testid="vista-proof-ready"]').waitFor({ state: 'attached' }) */}
      {ready && <div data-testid="vista-proof-ready" style={{ display: 'none' }} aria-hidden="true" />}
    </div>
  );
}

/**
 * windshieldTableauLayout — FULL-FIDELITY perf sweep (QUEUE-PERF-TEST-FOOTPRINT,
 * 2026-07-16), split out of windshieldTableauLayout.test.ts's
 * "safeOrbitRadii / orbitalPosition(safeRadii) — T1-A in-band invariant"
 * describe block. Carries the ORIGINAL fine-grained grid (0.02 AU step,
 * 2deg step) and full sector lists for all three in-band sweep tests that
 * block used to run inline — not just the one the ticket happened to name
 * ("holds at station-scale footprint margins too"); the other two shared
 * the same grid density and were independently measured slow enough to
 * exceed their own 20s override in isolation too.
 *
 * This file is deliberately slow BY DESIGN and excluded from the default
 * `npm test` run (see vitest.config.ts's own `exclude`) — invoke explicitly
 * via `npm run test:perf` (routes through vitest.perf.config.ts, the ONLY
 * config that includes `*.perfsweep.test.ts`).
 *
 * Why it's slow: harness weight, NOT math. The largest of the three (the
 * station-scale sweep, 41 sectors x the full grid) is roughly 1.8M
 * `expect()` calls. The equivalent 19,680-position sweep in the Python
 * server-side twin (services/gameserver/src/services/intrasystem_layout.py,
 * the byte-for-byte parity port of this same math) runs in 105ms — Vitest's
 * per-assertion overhead at this iteration count is what's slow, not the
 * underlying geometry. Smaller, fast representative samples of these SAME
 * three tests (coarser step, fewer sectors) live in
 * windshieldTableauLayout.test.ts and run every `npm test` for regression
 * sensitivity; this file preserves the FULL original coverage.
 */
import { describe, it, expect } from 'vitest';
import {
  starAnchor,
  orbitalPosition,
  safeOrbitRadii,
  BODY_SIZE_EM_MAX,
  ORBIT_AU_MAX,
  type BandGeometry,
} from '../windshieldTableauLayout';

describe('safeOrbitRadii / orbitalPosition(safeRadii) — T1-A in-band invariant — FULL SWEEP (perf)', () => {
  // Mirrors windshieldTableauLayout.test.ts's own FLIGHT_BAND/ARIA2_BAND
  // exactly (see that file's doc-comment for the 1440x334.7px/18.09px-
  // root-em derivation from cockpit-shell.css + game-layout.css at
  // 1440x900, uiscale=1).
  const FLIGHT_BAND: BandGeometry = { widthPx: 1440, heightPx: 334.7, remPx: 18.09 };
  const ARIA2_BAND: BandGeometry = { widthPx: 1440, heightPx: 226.1, remPx: 18.09 }; // 12.5em

  // Same ceilings the main test file uses.
  const MAX_OBJECT_EM = 3.2; // WindshieldTableau.tsx's own OBJECT_FOOTPRINT_EM_MAX
  const STATION_EM_WIDTH = 20; // STATION_FOOTPRINT_EM_WIDTH_MAX
  const STATION_EM_HEIGHT = 5; // STATION_FOOTPRINT_EM_HEIGHT_MAX

  const STEP_AU = 0.02;
  const STEP_DEG = 2;

  function assertInBand(band: BandGeometry, sectorSamples: number[], emWidth: number, emHeight: number) {
    const halfObjXPct = ((emWidth / 2) * band.remPx / band.widthPx) * 100;
    const halfObjYPct = ((emHeight / 2) * band.remPx / band.heightPx) * 100;
    for (const sectorId of sectorSamples) {
      const star = starAnchor(sectorId, null);
      const radii = safeOrbitRadii(star, band, emWidth, emHeight);
      for (let au = 0.2; au <= ORBIT_AU_MAX + 1e-9; au += STEP_AU) {
        for (let deg = 0; deg < 360; deg += STEP_DEG) {
          const pos = orbitalPosition(star, au, deg, radii);
          expect(pos.xPct - halfObjXPct).toBeGreaterThanOrEqual(-1e-6);
          expect(pos.xPct + halfObjXPct).toBeLessThanOrEqual(100 + 1e-6);
          expect(pos.yPct - halfObjYPct).toBeGreaterThanOrEqual(-1e-6);
          expect(pos.yPct + halfObjYPct).toBeLessThanOrEqual(100 + 1e-6);
        }
      }
    }
  }

  it('the footprint ceiling used below stays a superset of BODY_SIZE_EM_MAX (drift guard)', () => {
    expect(MAX_OBJECT_EM).toBeGreaterThanOrEqual(BODY_SIZE_EM_MAX);
  });

  it(
    'every (orbit_au, phase_deg) in the live contract range stays fully in-band, across a spread of sectors, at the flight-mode band height',
    () => {
      assertInBand(FLIGHT_BAND, [1, 2, 5, 9, 21, 40, 77], MAX_OBJECT_EM, MAX_OBJECT_EM); // 21 = the live symptom sector; 77 = the WindshieldTableau.test.tsx fixture sector
    },
    60_000,
  );

  it(
    'also holds at the tighter ARIA-2 panel-mode band height (12.5em) -- the fix isn\'t tuned to one specific height',
    () => {
      assertInBand(ARIA2_BAND, [1, 21, 77], MAX_OBJECT_EM, MAX_OBJECT_EM);
    },
    60_000,
  );

  it(
    'holds at station-scale footprint margins too (20em wide x 5em tall), across ALL 41 sectors 0-40 -- ' +
    'the star-anchor-inside-the-margin edge case a live proof caught',
    () => {
      const sectors = Array.from({ length: 41 }, (_, i) => i); // 0..40
      assertInBand(FLIGHT_BAND, sectors, STATION_EM_WIDTH, STATION_EM_HEIGHT);
    },
    120_000, // generous, explicit -- this file is opted-in/on-demand only, never part of the default timed run
  );
});

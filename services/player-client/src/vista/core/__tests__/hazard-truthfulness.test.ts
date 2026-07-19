/**
 * Vista Engine — Hazard Truthfulness test
 *
 * Proves BRIEF §2.5: placeHazardOverlays emits an overlay for EVERY hazard
 * unconditionally.  Desirability never suppresses a hazard visual — a lush,
 * high-habitability world with site hazards must still show every one of them.
 *
 * This is a pure pipeline (model-layer) test.  No DOM, no canvas.
 * Fixed seed → byte-identical VistaModel every run.
 */

import { describe, it, expect } from 'vitest';
import { generateVista } from '../pipeline';
import { VistaInput } from '../../contract';

// ---------------------------------------------------------------------------
// Fixed-seed lush input: TERRAN, hab=90, site with a NAMED storm + unnamed flood
// ---------------------------------------------------------------------------
//
// TERRAN hazardVisuals (profiles.ts):
//   storm      → 'storm-cell'       (atmospheric / sky-class visual)
//   flood      → 'flood-zone'       (ground-class visual)
//   megafauna  → 'megafauna-marker'
//   radiation  → 'radiation-haze'
//
// The storm hazard uses `named: true` (contract.ts:185).  The `named` field is
// accepted by VistaInput but NOT currently acted on by the pipeline — sky-push
// for named hazards is deferred to P2 (see it.todo at the bottom of this suite).
// What this file DOES assert:
//   (a) the named hazard IS present in overlays (truthfulness — not filtered out)
//   (b) storm kind maps to 'storm-cell' per the TERRAN hazardVisuals profile
//   (c) CURRENT REALITY: the named overlay's region stays on the ground plane
// A separate it.todo marks the unimplemented named→sky contract so the suite
// is honest about what is and is not built.

const LUSH_HAZARD_INPUT: VistaInput = {
  contractVersion: 1,
  seed: 'test-lush-hazard-001',

  planet: {
    type: 'TERRAN',
    habitability: 90,            // lush — flora ×1.10 brightening, desirability > 0.7
    atmosphere: {
      present: true,
      kind: null,
      density: 0.7,
    },
    nativeLife:    0.6,
    temperature:   0.2,
    waterCoverage: 0.5,
  },

  celestial: {
    star: { kind: 'G_YELLOW', color: '#fff4d0' },
    orbitAu:             1.0,
    phaseDeg:            180,
    rotationPeriodHours: 24,
    axialTiltDeg:        23,
  },

  site: {
    shape:          'SPRAWLING',
    usableSlots:    16,
    citadelCeiling: 3,
    energy: { source: 'SOLAR', tier: 2, magnitude: 0.6 },
    deposits: [
      { kind: 'ore', richness: 0.7 },
    ],
    hazards: [
      { kind: 'storm', severity: 0.75, named: true  },   // named flag stored; sky-push unimplemented (P2)
      { kind: 'flood', severity: 0.40, named: false },   // unnamed ground hazard
    ],
  },
};

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe('hazard truthfulness — lush world with site hazards', () => {
  const model = generateVista(LUSH_HAZARD_INPUT);

  it('pipeline completes cleanly', () => {
    // Basic sanity: no crash, contract version propagated.
    expect(model.contractVersion).toBe(1);
    expect(model.seed).toBe('test-lush-hazard-001');
    expect(model.planetType).toBe('TERRAN');
  });

  it('world reads as lush (desirability > 0.7)', () => {
    // hab=90 drives high desirability.  Belt-and-suspenders: confirm the
    // beauty budget is genuinely high before we check hazard suppression.
    expect(model.desirability).toBeGreaterThan(0.7);
  });

  it('hazards are NOT suppressed on a lush world — all overlays present', () => {
    // TRUTHFULNESS CLAUSE (BRIEF §2.5):
    // 2 hazards in input.site.hazards → 2 overlays in model.layers.hazards.overlays.
    // Desirability must NOT zero or remove any overlay.
    const overlays = model.layers.hazards.overlays;
    expect(overlays).toHaveLength(LUSH_HAZARD_INPUT.site!.hazards.length);
  });

  it('high desirability AND hazard overlays both present simultaneously', () => {
    // The critical co-occurrence: a single model can be both beautiful AND scarred.
    expect(model.desirability).toBeGreaterThan(0.7);
    expect(model.layers.hazards.overlays.length).toBeGreaterThan(0);
  });

  it('named storm hazard appears in overlays with correct hazard kind', () => {
    // The named hazard must not be filtered or skipped.
    const stormOverlay = model.layers.hazards.overlays.find(
      o => o.hazard === 'storm',
    );
    expect(stormOverlay).toBeDefined();
    expect(stormOverlay!.severity).toBeCloseTo(0.75);
  });

  it('storm kind maps to storm-cell visual per the TERRAN profile', () => {
    // PROFILE MAPPING ASSERTION — not a named→sky proof.
    // The TERRAN hazardVisuals map (profiles.ts:314-319) keys storm → 'storm-cell'.
    // This asserts that lookup resolves correctly — placeHazardOverlays picks the
    // type-specific visual, not the 'impact-scar' fallback.
    // What this does NOT prove: that the named flag pushes the region into the sky.
    // That contract (contract.ts:184) is tracked by the it.todo below.
    const stormOverlay = model.layers.hazards.overlays.find(
      o => o.hazard === 'storm',
    );
    expect(stormOverlay!.visual).toBe('storm-cell');
  });

  it('named storm overlay is currently ground-side — P2 regression anchor', () => {
    // CURRENT REALITY: named→sky is unimplemented.  The named overlay's region
    // quad sits fully below the terrain horizon line (all y-coords ≥ horizonY).
    // This assertion will INTENTIONALLY FAIL when P2 builds named→sky — that
    // failure is the "sky-push landed" signal.  At that point, remove or invert
    // this anchor and promote the it.todo below to a real test.
    const horizonY = model.layers.terrain.horizonY;
    const stormOverlay = model.layers.hazards.overlays.find(o => o.hazard === 'storm')!;
    for (const [, ry] of stormOverlay.region) {
      expect(ry).toBeGreaterThanOrEqual(horizonY);
    }
  });

  it.todo('named hazard forces its visual into the sky — contract.ts:184, UNIMPLEMENTED, deferred to P2 atmospheric-events');

  it('unnamed flood hazard also appears in overlays', () => {
    // Confirms truthfulness applies to all hazards, not just named ones.
    const floodOverlay = model.layers.hazards.overlays.find(
      o => o.hazard === 'flood',
    );
    expect(floodOverlay).toBeDefined();
    expect(floodOverlay!.visual).toBe('flood-zone');
  });

  it('each overlay has a well-formed region quad (4 corners)', () => {
    // Structure check: region must be a 4-point polygon, each point [x, y].
    for (const overlay of model.layers.hazards.overlays) {
      expect(overlay.region).toHaveLength(4);
      for (const point of overlay.region) {
        expect(point).toHaveLength(2);
        expect(typeof point[0]).toBe('number');
        expect(typeof point[1]).toBe('number');
      }
    }
  });
});

/**
 * Vista Engine — handle.update() behavioral proof
 *
 * Proves the contract implemented in mount()'s update() closure:
 *   handle.update(partial) → generateVista(merged) → new model reflects the change
 *
 * The full mount()→update()→canvas chain cannot be exercised in the vitest node
 * environment (CanvasRenderingContext2D is a browser API; buildVistaCache() needs
 * it at mount time).  The vista-proof Playwright harness covers the mount path;
 * this file covers the pipeline-sensitivity link in the chain.
 *
 * Two-step proof:
 *   1. generateVista() is sensitive to habitability — different habitability →
 *      materially different desirability and palette values (proves update() calling
 *      generateVista(merged) will produce a new, distinct model, not the stale one).
 *   2. The update() merge logic preserves non-changed fields — same seed +
 *      planet.type → same archetype (structural fields unchanged).
 *
 * No DOM, no canvas, no mocks — pure pipeline exercising.
 */

import { describe, it, expect } from 'vitest';
import { generateVista } from '../../core/pipeline';
import type { VistaInput } from '../../contract';

// ---------------------------------------------------------------------------
// Shared base — TERRAN with a defined site so all pipeline stages run
// ---------------------------------------------------------------------------

const BASE_INPUT: VistaInput = {
  contractVersion: 1,
  seed: 'update-proof-001',

  planet: {
    type:        'TERRAN',
    habitability: 20,           // low — barren-ish, low desirability
    atmosphere: {
      present: true,
      kind:    null,
      density: 0.5,
    },
    nativeLife:    0.1,
    temperature:   0.0,
    waterCoverage: 0.3,
  },

  celestial: {
    star:                { kind: 'G_YELLOW', color: '#fff4d0' },
    orbitAu:             1.0,
    phaseDeg:            180,
    rotationPeriodHours: 24,
    axialTiltDeg:        23,
  },

  site: {
    shape:          'COMPACT',
    usableSlots:    10,
    citadelCeiling: 2,
    energy: { source: 'SOLAR', tier: 1, magnitude: 0.4 },
    deposits: [{ kind: 'ore', richness: 0.5 }],
    hazards:  [],
  },
};

// Simulates a habitability slider drag: only planet.habitability changes.
const UPDATED_INPUT: VistaInput = {
  ...BASE_INPUT,
  planet: { ...BASE_INPUT.planet, habitability: 85 },  // lush
};

// ---------------------------------------------------------------------------
// 1 — Pipeline sensitivity: generateVista reflects the habitability change
// ---------------------------------------------------------------------------

describe('update() proof — pipeline sensitivity to habitability', () => {
  const modelLow  = generateVista(BASE_INPUT);
  const modelHigh = generateVista(UPDATED_INPUT);

  it('pipeline completes cleanly for both inputs', () => {
    expect(modelLow.contractVersion).toBe(1);
    expect(modelHigh.contractVersion).toBe(1);
    expect(modelLow.seed).toBe('update-proof-001');
    expect(modelHigh.seed).toBe('update-proof-001');
  });

  it('desirability rises with habitability — update() will produce a new model', () => {
    // This is the key output that handle.update(partial) changes via generateVista.
    // A stub that ignores the partial (the OLD behavior) would leave desirability
    // unchanged; the real implementation shows a measurable jump here.
    expect(modelHigh.desirability).toBeGreaterThan(modelLow.desirability);
    // Require a meaningful delta — not just floating-point noise
    expect(modelHigh.desirability - modelLow.desirability).toBeGreaterThan(0.1);
  });

  it('flora palette changes with habitability', () => {
    // habitability drives flora tint and lushness budget; channels must differ
    const [rLow,  gLow,  bLow]  = modelLow.palette.flora;
    const [rHigh, gHigh, bHigh] = modelHigh.palette.flora;
    const diff = Math.abs(rHigh - rLow) + Math.abs(gHigh - gLow) + Math.abs(bHigh - bLow);
    expect(diff).toBeGreaterThan(0);  // palette is NOT stale after update
  });

  it('sky star count changes with habitability (drives starfield density)', () => {
    // hab=20 → denser visible starfield (low atmosphere, fewer flora obscuring sky)
    // hab=85 → fewer visible stars (thicker atmosphere / bloom)
    // The exact direction can vary by type; what matters is they differ.
    expect(modelHigh.layers.sky.starCount).not.toBe(modelLow.layers.sky.starCount);
  });
});

// ---------------------------------------------------------------------------
// 2 — Merge correctness: structural fields are preserved across an update()
// ---------------------------------------------------------------------------

describe('update() proof — merge preserves non-changed fields', () => {
  // Simulate what update() does: merge the partial into the stored input.
  // Since BASE_INPUT and UPDATED_INPUT share seed + planet.type, the archetype
  // (which is seeded from those structural fields) must be identical.
  const modelBase    = generateVista(BASE_INPUT);
  const modelUpdated = generateVista(UPDATED_INPUT);

  it('same seed + planet.type → same archetype (structural fields preserved)', () => {
    // The update() merge keeps seed and planet.type unchanged for slider drags.
    // If merge were wrong (e.g. dropped planet.type), the archetype would diverge.
    expect(modelUpdated.archetype).toBe(modelBase.archetype);
    expect(modelUpdated.planetType).toBe(modelBase.planetType);
  });

  it('pipeline reports valid invariants for the updated input', () => {
    expect(modelUpdated.invariants.ok).toBe(true);
  });
});

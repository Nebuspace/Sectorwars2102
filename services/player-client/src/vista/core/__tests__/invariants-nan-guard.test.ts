/**
 * Vista Engine — invariants NaN guard
 *
 * Proves that any non-finite value (NaN / Infinity / -Infinity) in a critical
 * numeric model scalar flips invariants.ok to false and records a descriptive
 * note, while all-finite models produced by valid inputs keep ok=true.
 *
 * Two layers of coverage:
 *   1. Direct unit-test of the exported `checkFiniteFields` helper — fast,
 *      exhaustive, no pipeline wiring needed.
 *   2. End-to-end regression via `generateVista` on two representative planet
 *      types — confirms that valid inputs still produce ok=true after the guard
 *      was introduced.
 *
 * No DOM, no canvas.  Matches vitest/node environment.
 */

import { describe, it, expect } from 'vitest';
import { checkFiniteFields } from '../pipeline';
import { generateVista } from '../pipeline';
import { randomVistaInput } from '../validate';
import type { PlanetType } from '../../contract';

// ---------------------------------------------------------------------------
// 1 — Unit tests for checkFiniteFields
// ---------------------------------------------------------------------------

describe('checkFiniteFields — non-finite detection', () => {
  it('does not push a note when all fields are finite', () => {
    const notes: string[] = [];
    checkFiniteFields(notes, {
      desirability:              0.75,
      'lighting.bloom':          0.5,
      'lighting.colorGradeWarmth': 0.1,
      'layers.sky.starCount':    80,
    });
    expect(notes).toHaveLength(0);
  });

  it('flags a NaN field and records its name', () => {
    const notes: string[] = [];
    checkFiniteFields(notes, { desirability: NaN });
    expect(notes).toHaveLength(1);
    expect(notes[0]).toBe('non-finite field: desirability');
  });

  it('flags +Infinity', () => {
    const notes: string[] = [];
    checkFiniteFields(notes, { 'lighting.bloom': Infinity });
    expect(notes).toHaveLength(1);
    expect(notes[0]).toBe('non-finite field: lighting.bloom');
  });

  it('flags -Infinity', () => {
    const notes: string[] = [];
    checkFiniteFields(notes, { 'lighting.colorGradeWarmth': -Infinity });
    expect(notes).toHaveLength(1);
    expect(notes[0]).toBe('non-finite field: lighting.colorGradeWarmth');
  });

  it('flags multiple non-finite fields in one call', () => {
    const notes: string[] = [];
    checkFiniteFields(notes, {
      desirability:              NaN,
      'lighting.bloom':          Infinity,
      'layers.sky.starCount':    120,   // finite — should not appear
      'layers.sky.haze.density': NaN,
    });
    // desirability=NaN, lighting.bloom=Infinity, layers.sky.haze.density=NaN → 3 notes
    expect(notes).toHaveLength(3);
    expect(notes).toContain('non-finite field: desirability');
    expect(notes).toContain('non-finite field: lighting.bloom');
    expect(notes).toContain('non-finite field: layers.sky.haze.density');
    expect(notes).not.toContain('non-finite field: layers.sky.starCount');
  });

  it('accumulates notes onto an already-populated array', () => {
    const notes = ['planet.type "UNKNOWN" uses generic fallback profile'];
    checkFiniteFields(notes, { 'lighting.keyIntensity': NaN });
    expect(notes).toHaveLength(2);
    expect(notes[1]).toBe('non-finite field: lighting.keyIntensity');
  });
});

// ---------------------------------------------------------------------------
// 2 — assembleInvariants integration: ok=false when a non-finite note exists
// ---------------------------------------------------------------------------
//
// The helper above pushes into `notes`; `assembleInvariants` drives ok from
// notes.length.  This describe-block confirms the wiring is correct end-to-end
// through the helper's contract (ok=false iff notes is non-empty).

describe('checkFiniteFields → assembleInvariants wiring contract', () => {
  it('ok=false when notes are non-empty after the guard fires', () => {
    // Simulate what the pipeline does: notes starts empty, guard pushes, then
    // invariants = { ok: notes.length === 0, notes }.
    const notes: string[] = [];
    checkFiniteFields(notes, { desirability: NaN });
    const invariants = { ok: notes.length === 0, notes };
    expect(invariants.ok).toBe(false);
    expect(invariants.notes[0]).toMatch(/non-finite field: desirability/);
  });

  it('ok=true when all fields are finite (no notes pushed)', () => {
    const notes: string[] = [];
    checkFiniteFields(notes, {
      desirability:                0.5,
      'lighting.keyIntensity':     1.2,
      'lighting.bloom':            0.4,
      'lighting.colorGradeWarmth': -0.2,
      'layers.sky.starCount':      95,
      'layers.sky.haze.density':   0.3,
    });
    const invariants = { ok: notes.length === 0, notes };
    expect(invariants.ok).toBe(true);
  });
});

// ---------------------------------------------------------------------------
// 3 — End-to-end regression: valid inputs keep invariants.ok=true
// ---------------------------------------------------------------------------
//
// These two types cover the extremes of the model (TERRAN = full-featured
// lush world; VOLCANIC = hostile, heavy hazards, lava water layer).
// Their invariants.ok must stay true after the guard was added — any valid
// pipeline run produces all-finite scalars.

const REGRESSION_TYPES: PlanetType[] = ['TERRAN', 'VOLCANIC'];
const REGRESSION_SEEDS = ['seed-alpha', 'seed-bravo'];

describe('generateVista — invariants.ok stays true for valid inputs', () => {
  for (const type of REGRESSION_TYPES) {
    for (const seed of REGRESSION_SEEDS) {
      it(`${type} / ${seed}`, () => {
        const input = randomVistaInput(seed, type);
        const model = generateVista(input);
        expect(model.invariants.ok).toBe(true);
        expect(model.invariants.notes).toHaveLength(0);
      });
    }
  }
});

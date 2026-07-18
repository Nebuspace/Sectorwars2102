/**
 * Vista Engine — perfCollector behavior  (PERF-HARNESS sub-part (b))
 *
 * Proves the pinned interface's contract:
 *   - disabled (default) → every method is a true no-op, snapshot() reads zeros.
 *   - enabled → frameStart() resets, record()/recordAlloc() accumulate,
 *     recordParticles() overwrites, frameEnd() computes frameMs and rolls fps,
 *     snapshot() returns a detached copy.
 *
 * perfCollector is a module-level SINGLETON (by design — one collector shared
 * across the whole renderer). Every test that cares about fps timing controls
 * `performance.now()` via a spy so it owns its own clock end-to-end and is
 * immune to leftover `_lastFrameEnd` state from a prior test or run order —
 * asserting "fps===0 on the very first frameEnd()" against the REAL clock
 * would be order-dependent (a previous test's frameEnd() call already set
 * `_lastFrameEnd`), so this file never relies on that.
 *
 * No DOM — perfCollector only calls the global `performance.now()`, available
 * in vitest's node environment (Node 16+ exposes it without any DOM shim).
 */

import { describe, it, expect, beforeEach, vi } from 'vitest';
import { perfCollector } from '../collector';

/** Spy performance.now() to return `times` in sequence, one value per call. */
function mockClock(times: number[]): void {
  let i = 0;
  vi.spyOn(performance, 'now').mockImplementation(() => {
    const t = times[Math.min(i, times.length - 1)];
    i++;
    return t;
  });
}

beforeEach(() => {
  perfCollector.enabled = false;
  vi.restoreAllMocks();
});

describe('perfCollector — disabled (default) is a true no-op', () => {
  it('snapshot() reads all zeros / empty when never enabled', () => {
    const snap = perfCollector.snapshot();
    expect(snap.particleCount).toBe(0);
    expect(snap.allocChurn).toBe(0);
    expect(Object.keys(snap.layers)).toHaveLength(0);
  });

  it('record/recordParticles/recordAlloc/frameStart/frameEnd do nothing while disabled', () => {
    perfCollector.frameStart();
    perfCollector.record('sky', 5);
    perfCollector.recordParticles(99);
    perfCollector.recordAlloc(3);
    perfCollector.frameEnd();
    const snap = perfCollector.snapshot();
    expect(snap.layers).toEqual({});
    expect(snap.particleCount).toBe(0);
    expect(snap.allocChurn).toBe(0);
    expect(snap.frameMs).toBe(0);
    expect(snap.fps).toBe(0);
  });
});

describe('perfCollector — enabled', () => {
  it('frameStart() resets per-frame accumulators (particles/alloc/layers from a prior frame do not leak)', () => {
    mockClock([0, 10, 20, 30]);
    perfCollector.enabled = true;

    perfCollector.frameStart();
    perfCollector.record('sky', 2);
    perfCollector.recordParticles(10);
    perfCollector.recordAlloc(4);

    perfCollector.frameStart(); // new frame — must wipe the above
    const snap = perfCollector.snapshot();
    expect(snap.layers).toEqual({});
    expect(snap.particleCount).toBe(0);
    expect(snap.allocChurn).toBe(0);
  });

  it('record() accumulates multiple calls for the same layer within a frame', () => {
    perfCollector.enabled = true;
    perfCollector.frameStart();
    perfCollector.record('sky', 2.5);
    perfCollector.record('sky', 1.5);
    perfCollector.record('terrain', 3);
    const snap = perfCollector.snapshot();
    expect(snap.layers.sky).toBeCloseTo(4.0);
    expect(snap.layers.terrain).toBeCloseTo(3.0);
  });

  it('recordParticles() overwrites (point-in-time count, not a sum)', () => {
    perfCollector.enabled = true;
    perfCollector.frameStart();
    perfCollector.recordParticles(50);
    perfCollector.recordParticles(80);
    expect(perfCollector.snapshot().particleCount).toBe(80);
  });

  it('recordAlloc() accumulates (default n=1)', () => {
    perfCollector.enabled = true;
    perfCollector.frameStart();
    perfCollector.recordAlloc();
    perfCollector.recordAlloc();
    perfCollector.recordAlloc(5);
    expect(perfCollector.snapshot().allocChurn).toBe(7);
  });

  it('frameEnd() computes frameMs from frameStart\'s timestamp', () => {
    mockClock([100 /* frameStart */, 106.5 /* frameEnd */]);
    perfCollector.enabled = true;
    perfCollector.frameStart();
    perfCollector.frameEnd();
    expect(perfCollector.snapshot().frameMs).toBeCloseTo(6.5);
  });

  it('fps rolls (EMA) toward 1000/delta over sustained frames at a constant inter-frame delta', () => {
    // perfCollector is a module-level singleton with no reset() in the pinned
    // interface, so _fps can carry a residual from an earlier test in this
    // file — asserting an exact "0 on the very first call" would be order-
    // dependent. Instead: drive 100 frames at a constant 10ms delta (target
    // fps=100) and assert convergence. EMA weight 0.9^100 ≈ 2.7e-5, so any
    // plausible leftover _fps contributes a negligible fraction — this holds
    // regardless of run order or prior test state.
    // frameStart() and frameEnd() each consume one performance.now() call, so
    // 100 iterations need 200 timestamps. frameEnd lands 5ms after its own
    // frameStart; the next iteration's frameStart lands 5ms after THAT — so
    // consecutive frameEnd() reads are exactly 10ms apart (5, 15, 25, ...).
    const times: number[] = [];
    for (let i = 0; i < 100; i++) times.push(i * 10, i * 10 + 5); // [0,5, 10,15, 20,25, ...]
    mockClock(times);
    perfCollector.enabled = true;

    let fps = 0;
    for (let i = 0; i < 100; i++) {
      perfCollector.frameStart();
      perfCollector.frameEnd();
      fps = perfCollector.snapshot().fps;
    }
    expect(fps).toBeCloseTo(100, 0);
  });

  it('snapshot() returns a detached copy — mutating it does not affect the collector', () => {
    perfCollector.enabled = true;
    perfCollector.frameStart();
    perfCollector.record('sky', 1);
    const snap = perfCollector.snapshot();
    snap.layers.sky = 999;
    snap.layers.injected = 42;
    const again = perfCollector.snapshot();
    expect(again.layers.sky).toBeCloseTo(1);
    expect(again.layers.injected).toBeUndefined();
  });
});

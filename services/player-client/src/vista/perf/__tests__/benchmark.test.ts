/**
 * Vista Engine — benchmark runner  (PERF-HARNESS sub-part (b))
 *
 * Two independent proofs:
 *   1. runBenchmark() aggregation/classification/table logic, exercised
 *      against a mock SceneRunner producing synthetic (but scene-varying)
 *      PerfSnapshots — no rendering, no browser, proves the ENGINE.
 *   2. checkPipelineDeterminism() against the REAL pipeline (no mocks) for
 *      all 12 scenes — proves the byte-identical foundation: generateVista()
 *      output is fully reproducible per seed, so instrumenting the renderer
 *      (a separate lane) cannot perturb the model the renderer reads from.
 */

import { describe, it, expect } from 'vitest';
import { runBenchmark, checkPipelineDeterminism, formatTable, type SceneRunner } from '../benchmark';
import { PERF_SCENES, TARGET_FRAME_MS, FLOOR_FRAME_MS } from '../scenes';
import type { PerfSnapshot } from '../collector';

// ---------------------------------------------------------------------------
// 1 — runBenchmark() engine, against a mock runner
// ---------------------------------------------------------------------------

/** Synthetic frameMs per scene index, deliberately spanning all 3 budget bands. */
function fakeSnapshotFor(index: number): PerfSnapshot {
  const frameMsByBand = [TARGET_FRAME_MS - 1, (TARGET_FRAME_MS + FLOOR_FRAME_MS) / 2, FLOOR_FRAME_MS + 5];
  const frameMs = frameMsByBand[index % 3];
  return {
    layers: { sky: frameMs * 0.2, terrain: frameMs * 0.5, features: frameMs * 0.3 },
    particleCount: 10 + index,
    allocChurn: index,
    fps: 1000 / frameMs,
    frameMs,
  };
}

describe('runBenchmark — aggregation engine (mock runner, no rendering)', () => {
  it('runs every scene exactly once, in order, and classifies budget correctly', async () => {
    let callCount = 0;
    const runner: SceneRunner = async (scene, model) => {
      expect(model.planetType).toBe(scene.planetType); // model was generated from THIS scene's input
      return fakeSnapshotFor(callCount++);
    };

    const report = await runBenchmark(runner);

    expect(report.results).toHaveLength(PERF_SCENES.length);
    expect(callCount).toBe(PERF_SCENES.length);
    report.results.forEach((r, i) => expect(r.scene.id).toBe(PERF_SCENES[i].id));

    // index%3===0 → TARGET-1 → TARGET; ===1 → mid → FLOOR; ===2 → FLOOR+5 → OVER_FLOOR
    expect(report.results[0].budget).toBe('TARGET');
    expect(report.results[1].budget).toBe('FLOOR');
    expect(report.results[2].budget).toBe('OVER_FLOOR');
  });

  it('runs a caller-supplied scene subset instead of the full 12 when given one', async () => {
    const subset = PERF_SCENES.slice(0, 2);
    const runner: SceneRunner = async () => fakeSnapshotFor(0);
    const report = await runBenchmark(runner, subset);
    expect(report.results).toHaveLength(2);
  });

  it('report.json round-trips the results (machine-readable)', async () => {
    const runner: SceneRunner = async () => fakeSnapshotFor(0);
    const report = await runBenchmark(runner, PERF_SCENES.slice(0, 1));
    const parsed = JSON.parse(report.json);
    expect(parsed).toHaveLength(1);
    expect(parsed[0].scene.id).toBe(PERF_SCENES[0].id);
    expect(parsed[0].budget).toBe('TARGET');
  });

  it('report.table lists every scene id and a per-layer breakdown section', async () => {
    const runner: SceneRunner = async () => fakeSnapshotFor(0);
    const report = await runBenchmark(runner, PERF_SCENES);
    for (const scene of PERF_SCENES) {
      expect(report.table).toContain(scene.id);
    }
    expect(report.table).toContain('Per-layer breakdown:');
    expect(report.table).toContain('sky=');
  });
});

describe('formatTable — standalone formatter', () => {
  it('handles a scene with no layer data without throwing', async () => {
    const runner: SceneRunner = async () => ({
      layers: {}, particleCount: 0, allocChurn: 0, fps: 0, frameMs: 0,
    });
    const report = await runBenchmark(runner, PERF_SCENES.slice(0, 1));
    expect(formatTable(report.results)).toContain('(no layer data)');
  });
});

// ---------------------------------------------------------------------------
// 2 — Pipeline determinism, REAL generateVista(), all 12 scenes, no mocks
// ---------------------------------------------------------------------------

describe('checkPipelineDeterminism — real pipeline, no rendering', () => {
  it('every scene reproduces a byte-identical VistaModel across two generateVista() calls', () => {
    const results = checkPipelineDeterminism();
    expect(results).toHaveLength(PERF_SCENES.length);
    for (const r of results) {
      expect(r.identical, `${r.sceneId} was NOT structurally identical across two runs`).toBe(true);
    }
  });

  it('accepts a caller-supplied scene subset', () => {
    const results = checkPipelineDeterminism(PERF_SCENES.slice(0, 3));
    expect(results).toHaveLength(3);
  });
});

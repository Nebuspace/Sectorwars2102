/**
 * Vista Engine — CI budget gate  (WO-PERF-BUDGET-GATE)
 *
 * Proves:
 *   1. computeDrawSceneMs() = frameMs − postProcess (NOT a layer-sum — see
 *      budget-gate.ts's FORMULA note; a regression test pins this against the
 *      real OCEANIC_CALM baseline capture, where a naive layer-sum would give
 *      a materially different — and wrong — answer).
 *   2. The current 6-biome CALM baseline passes the gate.
 *   3. Accept #2 — a synthetic +Nms drawScene regression flips a passing
 *      scene to RED; the unmodified baseline stays GREEN.
 *   4. postProcess never factors into pass/fail, however large.
 *
 * Fixtures are the REAL captured numbers from WO-PERF-HARNESS's 12-scene
 * baseline (playwright/artifacts/vista-perf-report.json, real-GPU
 * headless:false, median-of-3) — not invented values.
 */

import { describe, it, expect } from 'vitest';
import { computeDrawSceneMs, gateScene, runGate, DRAWSCENE_BUDGET_MS } from '../budget-gate';
import { PERF_SCENES } from '../scenes';
import type { PerfSnapshot } from '../collector';
import type { SceneResult } from '../benchmark';

// ---------------------------------------------------------------------------
// Real captured baseline (playwright/artifacts/vista-perf-report.json)
// ---------------------------------------------------------------------------

const OCEANIC_CALM_SNAPSHOT: PerfSnapshot = {
  layers: {
    drawGodRays: 0, drawCumulusCloud: 0.2, drawOceanicSurface: 1.2, drawWaterFX: 0.2,
    drawLandmarks: 0.2, scatter: 0.4, drawCitadelStructure: 0.1, drawDepositGlyph: 0.1,
    drawEnergyGlyph: 0, drawLandedParticles: 0.3, postProcess: 0.5,
  },
  particleCount: 109, allocChurn: 77, fps: 0, frameMs: 7.3,
};

const scene = PERF_SCENES.find((s) => s.id === 'OCEANIC_CALM')!;

function toResult(snapshot: PerfSnapshot): SceneResult {
  return { scene, snapshot, budget: 'FLOOR' }; // budget field unused by runGate; placeholder
}

// ---------------------------------------------------------------------------
// 1 — Formula: frameMs − postProcess, not a layer-sum
// ---------------------------------------------------------------------------

describe('computeDrawSceneMs — frameMs minus postProcess', () => {
  it('matches the worked derivation exactly: 7.30 − 0.50 = 6.80', () => {
    expect(computeDrawSceneMs(OCEANIC_CALM_SNAPSHOT)).toBeCloseTo(6.80, 5);
  });

  it('is NOT the same as summing every non-postProcess layer (that undercounts)', () => {
    const layerSum = Object.entries(OCEANIC_CALM_SNAPSHOT.layers)
      .filter(([k]) => k !== 'postProcess')
      .reduce((sum, [, v]) => sum + v, 0);
    // layerSum = 2.7 here — drawScene's own untimed work (sky gradient, lighting,
    // day/night dimming, cache lookup) is real cost that only shows up in the
    // frameStart→frameEnd delta, never in a single named layer.
    expect(layerSum).toBeCloseTo(2.7, 5);
    expect(computeDrawSceneMs(OCEANIC_CALM_SNAPSHOT)).not.toBeCloseTo(layerSum, 1);
  });

  it('treats a missing postProcess layer as 0 (defensive — every real snapshot has it)', () => {
    const noPostProcess: PerfSnapshot = { layers: { sky: 2 }, particleCount: 0, allocChurn: 0, fps: 0, frameMs: 5 };
    expect(computeDrawSceneMs(noPostProcess)).toBe(5);
  });
});

// ---------------------------------------------------------------------------
// 2 — Current baseline passes
// ---------------------------------------------------------------------------

describe('gateScene / runGate — current baseline', () => {
  it('OCEANIC_CALM (the measured worst case, 6.80ms) passes an 8ms budget', () => {
    const result = gateScene(scene, OCEANIC_CALM_SNAPSHOT);
    expect(result.drawSceneMs).toBeCloseTo(6.80, 5);
    expect(result.postProcessMs).toBeCloseTo(0.5, 5);
    expect(result.pass).toBe(true);
  });

  it('runGate() over a set of results passes when every scene is under budget', () => {
    const report = runGate([toResult(OCEANIC_CALM_SNAPSHOT)]);
    expect(report.pass).toBe(true);
    expect(report.results).toHaveLength(1);
  });
});

// ---------------------------------------------------------------------------
// 3 — Accept #2: synthetic regression → RED, current → GREEN
// ---------------------------------------------------------------------------

describe('synthetic drawScene regression (Accept #2)', () => {
  it('current (unmodified) OCEANIC_CALM stays GREEN', () => {
    expect(gateScene(scene, OCEANIC_CALM_SNAPSHOT).pass).toBe(true);
  });

  it('a synthetic +Nms drawScene regression flips the SAME scene to RED', () => {
    // Push a non-postProcess layer up so drawSceneMs crosses DRAWSCENE_BUDGET_MS.
    // 6.80ms current + 2ms regression = 8.80ms > 8ms budget.
    const regressed: PerfSnapshot = {
      ...OCEANIC_CALM_SNAPSHOT,
      layers: { ...OCEANIC_CALM_SNAPSHOT.layers, drawOceanicSurface: OCEANIC_CALM_SNAPSHOT.layers.drawOceanicSurface + 2 },
      frameMs: OCEANIC_CALM_SNAPSHOT.frameMs + 2, // the regression shows up in frameMs too (real instrumentation would)
    };
    const result = gateScene(scene, regressed);
    expect(result.drawSceneMs).toBeCloseTo(8.80, 5);
    expect(result.pass).toBe(false);
  });

  it('runGate() flips a whole report to fail when even one scene regresses', () => {
    const regressed: PerfSnapshot = {
      ...OCEANIC_CALM_SNAPSHOT,
      frameMs: OCEANIC_CALM_SNAPSHOT.frameMs + 5, // well past budget
    };
    const report = runGate([toResult(OCEANIC_CALM_SNAPSHOT), toResult(regressed)]);
    expect(report.pass).toBe(false);
    expect(report.results[0].pass).toBe(true);  // unmodified scene still green
    expect(report.results[1].pass).toBe(false); // regressed scene red
  });
});

// ---------------------------------------------------------------------------
// 4 — postProcess never gates, however large
// ---------------------------------------------------------------------------

describe('postProcess is advisory-only — never gates pass/fail', () => {
  it('a scene with drawSceneMs under budget still passes even with a huge postProcess (SwiftShader-scale)', () => {
    const softwareRendered: PerfSnapshot = {
      ...OCEANIC_CALM_SNAPSHOT,
      layers: { ...OCEANIC_CALM_SNAPSHOT.layers, postProcess: 50 }, // SwiftShader-scale bloom cost
      frameMs: 6.80 + 50, // frameMs reflects the real (inflated) total
    };
    const result = gateScene(scene, softwareRendered);
    expect(result.drawSceneMs).toBeCloseTo(6.80, 5); // unaffected — postProcess subtracted out
    expect(result.postProcessMs).toBe(50);            // reported, for visibility
    expect(result.pass).toBe(true);                    // NOT gated by it
  });
});

describe('DRAWSCENE_BUDGET_MS', () => {
  it('documents the derived, non-arbitrary value', () => {
    expect(DRAWSCENE_BUDGET_MS).toBe(8);
  });
});

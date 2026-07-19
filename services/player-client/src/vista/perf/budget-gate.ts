/**
 * Vista Engine — CI budget gate  (WO-PERF-BUDGET-GATE, the A0 pair)
 *
 * Hard-gates on drawScene-ONLY cost, EXCLUDING `postProcess` (bloom/vignette/
 * grade/grain — the GPU-bound canvas `ctx.filter` blur pass). postProcess is
 * ~150x inflated under headless Chromium's default SwiftShader software
 * rasterizer (measured in WO-PERF-HARNESS: 35-50ms software vs 0.3ms real-GPU
 * — see `.claude/agent-memory/monk/headless-chromium-swiftshader-perf-trap.md`).
 * A gate that included postProcess would always-fail in any CI container
 * (none have a real GPU), making the gate useless. drawScene-only work (path
 * fills/strokes, sky gradients, lighting compositing) is CPU-bound and
 * env-portable — see the SwiftShader-vs-real-GPU comparison this WO's driver
 * produces for the empirical proof.
 *
 * postProcess/bloom stays fully measured (every GateResult still carries it)
 * as an ADVISORY number for the Mac headless:false real-GPU reference run —
 * documented, never blocking.
 *
 * FORMULA — drawSceneMs = frameMs − layers.postProcess, NOT a sum of the
 * other named layers. render()'s single frameStart()/frameEnd() bracket
 * (backend.ts:8440/8455) wraps drawScene() + postProcess() together, so
 * frameMs already includes postProcess; subtracting it is exact. Summing the
 * OTHER named layers instead would undercount — most of drawScene()'s own
 * cost (sky gradient, lighting compositing, day/night dimming, cache lookup)
 * is never wrapped in an individual `timed()` call and only shows up in the
 * frameStart→frameEnd delta, not in any single named layer. Verified against
 * the real 12-scene baseline: layer-sum undercounts by ~1.5-4.6ms per scene.
 */

import type { PerfSnapshot } from './collector';
import type { PerfScene } from './scenes';
import type { SceneResult } from './benchmark';

/**
 * Hard CI budget, ms, for drawScene-only cost (frameMs − postProcess).
 *
 * Derivation (WO-PERF-HARNESS's 12-scene baseline, real-GPU headless:false,
 * median-of-3 — playwright/artifacts/vista-perf-report.json):
 *   OCEANIC_CALM   frameMs 7.30 − postProcess 0.50 = 6.80ms  ← worst case
 *   OCEANIC_EXTREME                                = 6.50ms
 *   TERRAN_EXTREME                                 = 6.10ms
 *   every other scene                              ≤ 3.70ms
 * OCEANIC's water/cloud layering (drawOceanicSurface + drawWaterFX +
 * drawCumulusCloud) is the single most expensive non-bloom path measured
 * across all 12 scenes.
 *
 * Headroom: +~18% over the measured worst-case, to absorb ordinary
 * run-to-run scheduling/GC jitter without becoming a flaky gate —
 * 6.80 * 1.18 ≈ 8.02, rounded to a clean 8ms.
 */
export const DRAWSCENE_BUDGET_MS = 8;

/** frameMs minus the postProcess layer — see the FORMULA note above for why this, not a layer-sum. */
export function computeDrawSceneMs(snapshot: PerfSnapshot): number {
  const postProcessMs = snapshot.layers.postProcess ?? 0;
  // Clamp — a postProcess layer sample can exceed frameMs (jitter between two
  // separately-recorded timings), which would otherwise report a negative
  // draw-scene cost.
  return Math.max(0, snapshot.frameMs - postProcessMs);
}

export interface GateResult {
  sceneId: string;
  drawSceneMs: number;
  /** Advisory only — never factors into `pass`. */
  postProcessMs: number;
  pass: boolean;
}

export function gateScene(scene: PerfScene, snapshot: PerfSnapshot): GateResult {
  const drawSceneMs = computeDrawSceneMs(snapshot);
  return {
    sceneId: scene.id,
    drawSceneMs,
    postProcessMs: snapshot.layers.postProcess ?? 0,
    pass: drawSceneMs <= DRAWSCENE_BUDGET_MS,
  };
}

export interface GateReport {
  pass: boolean;
  results: GateResult[];
}

/** Gate a full benchmark run's results (from benchmark.ts's runBenchmark()). */
export function runGate(sceneResults: readonly SceneResult[]): GateReport {
  const results = sceneResults.map((r) => gateScene(r.scene, r.snapshot));
  return { pass: results.every((r) => r.pass), results };
}

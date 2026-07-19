/**
 * Vista Engine — Headless perf benchmark runner  (PERF-HARNESS sub-part (b))
 *
 * STATUS (see the sub-part (b) report to the lead for the full writeup):
 * this file is browser-agnostic on purpose — everything below is provable
 * today, in vitest, with zero DOM.  Actually driving instrumented render()
 * calls needs a real canvas (backend.ts's mount() takes an HTMLCanvasElement;
 * there is no headless-node canvas path in this repo — see
 * render/canvas2d/backend.update.test.ts's own doc comment).  That means the
 * 12-scene table this WO ultimately wants is produced by pointing
 * runBenchmark() at a SceneRunner backed by a real (headless) Chromium page —
 * a thin adapter that navigates to a lab route, drives frames, and reads
 * perfCollector.snapshot() back out.  That adapter is NOT shipped here: the
 * lab route it needs (arbitrary VistaInput injection, not just `?type=` +
 * sliders) doesn't exist yet and lives outside this lane's `vista/perf/`
 * boundary.  See the sub-part (b) report for the proposed hook.
 *
 * What IS provable headlessly, right now, with no browser:
 *   - generateVista() determinism per scene (checkPipelineDeterminism) — the
 *     pipeline itself (Lane B) never touches perfCollector, so instrumenting
 *     the renderer (Lane C) cannot perturb it; this is the byte-identical
 *     guarantee's foundation, proven for real against all 12 scenes.
 *   - The aggregation/classification/table-formatting engine
 *     (runBenchmark + formatTable), exercised in tests against a mock
 *     SceneRunner producing synthetic PerfSnapshots.
 */

import { generateVista } from '../core/pipeline';
import { PERF_SCENES, TARGET_FRAME_MS, FLOOR_FRAME_MS, type PerfScene } from './scenes';
import type { PerfSnapshot } from './collector';
import type { VistaModel } from '../contract';

// ---------------------------------------------------------------------------
// Runner contract — a browser-driven adapter implements this
// ---------------------------------------------------------------------------

/**
 * Renders `scene` (its already-generated `model`) and returns the settled
 * PerfSnapshot for it.  Implementations decide their own warm-up/settle
 * policy (e.g. discard the first N frames before sampling) — runBenchmark()
 * takes whatever snapshot it's handed at face value.
 */
export type SceneRunner = (scene: PerfScene, model: VistaModel) => Promise<PerfSnapshot>;

export type BudgetVerdict = 'TARGET' | 'FLOOR' | 'OVER_FLOOR';

export interface SceneResult {
  scene: PerfScene;
  snapshot: PerfSnapshot;
  budget: BudgetVerdict;
}

export interface BenchmarkReport {
  results: SceneResult[];
  table: string;
  json: string;
}

function classifyBudget(frameMs: number): BudgetVerdict {
  if (frameMs <= TARGET_FRAME_MS) return 'TARGET';
  if (frameMs <= FLOOR_FRAME_MS) return 'FLOOR';
  return 'OVER_FLOOR';
}

/**
 * Runs every scene through `runner` (generating its VistaModel first via the
 * pure pipeline) and assembles a report.  Scenes run sequentially — a
 * browser-backed runner is driving one shared page, so concurrent scenes
 * would contend for the same canvas/rAF loop.
 */
export async function runBenchmark(
  runner: SceneRunner,
  scenes: readonly PerfScene[] = PERF_SCENES,
): Promise<BenchmarkReport> {
  const results: SceneResult[] = [];

  for (const scene of scenes) {
    const model = generateVista(scene.input);
    const snapshot = await runner(scene, model);
    results.push({ scene, snapshot, budget: classifyBudget(snapshot.frameMs) });
  }

  return {
    results,
    table: formatTable(results),
    json: JSON.stringify(results, null, 2),
  };
}

// ---------------------------------------------------------------------------
// Table formatting
// ---------------------------------------------------------------------------

function pad(s: string, width: number): string {
  return s.length >= width ? s : s + ' '.repeat(width - s.length);
}

/** Deterministic per-scene per-layer ms table (console-readable). */
export function formatTable(results: readonly SceneResult[]): string {
  const header = [
    pad('Scene', 22),
    pad('frameMs', 9),
    pad('fps', 7),
    pad('particles', 10),
    pad('alloc', 7),
    'budget',
  ].join(' ');

  const rows = results.map((r) => {
    const s = r.snapshot;
    return [
      pad(r.scene.id, 22),
      pad(s.frameMs.toFixed(2), 9),
      pad(s.fps.toFixed(1), 7),
      pad(String(s.particleCount), 10),
      pad(String(s.allocChurn), 7),
      r.budget,
    ].join(' ');
  });

  const layerLines = results.map((r) => {
    const entries = Object.entries(r.snapshot.layers);
    const layerStr = entries.length
      ? entries.map(([k, v]) => `${k}=${v.toFixed(2)}ms`).join(' ')
      : '(no layer data)';
    return `  ${pad(r.scene.id, 22)} ${layerStr}`;
  });

  return [
    header,
    '-'.repeat(header.length),
    ...rows,
    '',
    'Per-layer breakdown:',
    ...layerLines,
  ].join('\n');
}

// ---------------------------------------------------------------------------
// Structural determinism check — pure pipeline, no rendering, no browser
// ---------------------------------------------------------------------------
//
// Substitutes for a literal "rng-draw-count" invariant: rng.ts's SeededRng
// has no draw counter to read.  Full VistaModel structural equality across
// two generateVista() calls on the same input is a strictly stronger
// guarantee (it implies the draw sequence — and everything derived from it —
// reproduced exactly), so it's used here in place of a counter that doesn't
// exist.  This runs against the REAL pipeline (no mocks) for all 12 scenes.

export interface DeterminismResult {
  sceneId: string;
  identical: boolean;
}

export function checkPipelineDeterminism(
  scenes: readonly PerfScene[] = PERF_SCENES,
): DeterminismResult[] {
  return scenes.map((scene) => {
    const a = generateVista(scene.input);
    const b = generateVista(scene.input);
    return { sceneId: scene.id, identical: JSON.stringify(a) === JSON.stringify(b) };
  });
}

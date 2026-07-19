/**
 * Vista Perf — CI Budget Gate driver  (WO-PERF-BUDGET-GATE)
 *
 * Hard-gates the 6 CALM reference scenes on drawScene-only cost (see
 * src/vista/perf/budget-gate.ts). Run under TWO configs for two different
 * purposes:
 *
 *   playwright.vista-budget-gate.config.ts        (headless:true,  default)
 *     → the REAL CI gate. Runs under SwiftShader (software) deliberately —
 *       that's what any CI container gets, no real GPU available. This is
 *       the config that actually fails a build.
 *
 *   playwright.vista-budget-gate.headed.config.ts (headless:false)
 *     → REFERENCE ONLY, for the portability proof (Accept #1): confirms
 *       drawScene-only numbers are GPU-rendering-independent by comparing
 *       against the software run. Never wired into CI.
 *
 * PERF_GATE_MODE env var (set by the invoking npm/shell command, not by
 * Playwright config) picks the output filename so both runs' reports survive
 * side by side for the comparison step.
 */

import { test, expect } from '@playwright/test';
import * as fs from 'fs';
import * as path from 'path';
import type { Page } from '@playwright/test';
import { runBenchmark, type SceneRunner } from '../../src/vista/perf/benchmark';
import { PERF_SCENES } from '../../src/vista/perf/scenes';
import { runGate, DRAWSCENE_BUDGET_MS } from '../../src/vista/perf/budget-gate';
import type { PerfSnapshot } from '../../src/vista/perf/collector';
import type { PerfScene } from '../../src/vista/perf/scenes';

const ARTIFACTS_DIR = path.resolve(process.cwd(), 'playwright/artifacts');
const MODE = process.env.PERF_GATE_MODE === 'gpu' ? 'gpu' : 'software';
const REPORT_JSON = path.join(ARTIFACTS_DIR, `vista-budget-gate-report-${MODE}.json`);

/** The 6 reference scenes for the gate — one per biome, CALM load (the WO's baseline set). */
const GATE_SCENES = PERF_SCENES.filter((s) => s.load === 'CALM');

const REPEATS_PER_SCENE = 3;

test.beforeAll(() => {
  fs.mkdirSync(ARTIFACTS_DIR, { recursive: true });
});

async function loadScene(page: Page, scene: PerfScene): Promise<void> {
  await page.addInitScript((input) => {
    (window as unknown as { __VISTA_PERF_INPUT__: unknown }).__VISTA_PERF_INPUT__ = input;
  }, scene.input);
  await page.goto('/lab/vista-proof?perf=1');
  await page.waitForSelector('[data-testid="vista-proof-ready"]', { state: 'attached', timeout: 15_000 });
}

async function readSnapshot(page: Page): Promise<PerfSnapshot> {
  return page.evaluate(() => {
    const c = (window as unknown as { __perfCollector?: { snapshot(): PerfSnapshot } }).__perfCollector;
    if (!c) throw new Error('window.__perfCollector is not exposed — collector.ts DEV-gate not active');
    return c.snapshot();
  });
}

test(`budget gate — 6 CALM reference scenes (mode: ${MODE})`, async ({ context }) => {
  const runner: SceneRunner = async (scene) => {
    const samples: PerfSnapshot[] = [];
    for (let i = 0; i < REPEATS_PER_SCENE; i++) {
      const page = await context.newPage();
      await loadScene(page, scene);
      await page.waitForTimeout(150);
      samples.push(await readSnapshot(page));
      await page.close();
    }
    samples.sort((a, b) => a.frameMs - b.frameMs);
    return samples[Math.floor(samples.length / 2)];
  };

  const benchmarkReport = await runBenchmark(runner, GATE_SCENES);
  const gate = runGate(benchmarkReport.results);

  fs.writeFileSync(REPORT_JSON, JSON.stringify(gate, null, 2));

  console.log(`\n[budget-gate:${MODE}] DRAWSCENE_BUDGET_MS=${DRAWSCENE_BUDGET_MS}`);
  console.log(
    ['sceneId', 'drawSceneMs', 'postProcessMs', 'pass'].join('\t'),
  );
  for (const r of gate.results) {
    console.log([r.sceneId, r.drawSceneMs.toFixed(2), r.postProcessMs.toFixed(2), r.pass].join('\t'));
  }
  console.log(`[budget-gate:${MODE}] overall pass=${gate.pass}`);
  console.log(`[budget-gate:${MODE}] report → ${REPORT_JSON}\n`);

  // The actual CI-failing assertion. Only the `software` (headless:true, real
  // CI shape) mode is meant to gate a build; the `gpu` reference run asserts
  // the same thing too (both SHOULD currently pass — that's the portability
  // proof), but is never wired into a CI job.
  expect(gate.results).toHaveLength(GATE_SCENES.length);
  expect(gate.pass, JSON.stringify(gate.results.filter((r) => !r.pass), null, 2)).toBe(true);
});

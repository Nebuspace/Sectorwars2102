/**
 * Vista Perf Benchmark — WO-PERF-HARNESS driver.
 *
 * The live-render leg of sub-part (b): drives the 12 PERF_SCENES through a
 * real (GPU-accelerated — see playwright.vista-perf.config.ts's headless:false
 * note) Chromium instance via VistaProof's `__VISTA_PERF_INPUT__` injection
 * hook (VistaProof.tsx:674), reads perfCollector.snapshot() back via the
 * window.__perfCollector exposure (collector.ts), and feeds the results
 * through benchmark.ts's runBenchmark() for the per-layer ms table.
 *
 * A fresh `page` per (scene, repeat) — NOT `page.addInitScript()` reused on
 * one page across navigations — because Playwright's own docs say the
 * execution order of multiple accumulated init scripts on one page is
 * "not defined"; a fresh page per measurement means exactly one init script
 * ever runs there, no ambiguity.
 *
 * Outside src/ (Accept: vitest's `include: ['src/**\/*.{test,spec}.{ts,tsx}']`
 * would otherwise try — and fail — to collect this as a vitest test).
 */

import { test, expect } from '@playwright/test';
import * as fs from 'fs';
import * as path from 'path';
import type { Page } from '@playwright/test';
import { runBenchmark, type SceneRunner } from '../../src/vista/perf/benchmark';
import { PERF_SCENES } from '../../src/vista/perf/scenes';
import type { PerfSnapshot } from '../../src/vista/perf/collector';
import type { PerfScene } from '../../src/vista/perf/scenes';

const ARTIFACTS_DIR = path.resolve(process.cwd(), 'playwright/artifacts');
const REPORT_JSON    = path.join(ARTIFACTS_DIR, 'vista-perf-report.json');
const REPORT_TABLE   = path.join(ARTIFACTS_DIR, 'vista-perf-report.txt');

/** Repeats per scene — median frameMs wins, smoothing any residual cold-start noise. */
const REPEATS_PER_SCENE = 3;

test.beforeAll(() => {
  fs.mkdirSync(ARTIFACTS_DIR, { recursive: true });
});

/** Navigate a fresh page to VistaProof with `scene.input` injected, wait for a settled paint. */
async function loadScene(page: Page, scene: PerfScene, perfOn: boolean): Promise<void> {
  await page.addInitScript((input) => {
    (window as unknown as { __VISTA_PERF_INPUT__: unknown }).__VISTA_PERF_INPUT__ = input;
  }, scene.input);

  const url = perfOn ? '/lab/vista-proof?perf=1' : '/lab/vista-proof';
  await page.goto(url);
  await page.waitForSelector('[data-testid="vista-proof-ready"]', { state: 'attached', timeout: 15_000 });
}

/** Read the FULL per-layer PerfSnapshot via the dev-only window.__perfCollector exposure. */
async function readSnapshot(page: Page): Promise<PerfSnapshot> {
  return page.evaluate(() => {
    const c = (window as unknown as { __perfCollector?: { snapshot(): PerfSnapshot } }).__perfCollector;
    if (!c) throw new Error('window.__perfCollector is not exposed — collector.ts DEV-gate not active');
    return c.snapshot();
  });
}

// ---------------------------------------------------------------------------
// Accept #2 — headless (real-GPU-headed) 12-scene per-layer ms table
// ---------------------------------------------------------------------------

test('12-scene perf benchmark — per-layer ms table', async ({ context }) => {
  const runner: SceneRunner = async (scene) => {
    const samples: PerfSnapshot[] = [];
    for (let i = 0; i < REPEATS_PER_SCENE; i++) {
      const page = await context.newPage();
      await loadScene(page, scene, /* perfOn */ true);
      await page.waitForTimeout(150); // settle past the ready-gate's own paint
      samples.push(await readSnapshot(page));
      await page.close();
    }
    // Median by frameMs — smooths residual per-run noise without discarding
    // structural per-layer data (we return the full snapshot at the median index).
    samples.sort((a, b) => a.frameMs - b.frameMs);
    return samples[Math.floor(samples.length / 2)];
  };

  const report = await runBenchmark(runner, PERF_SCENES);

  fs.writeFileSync(REPORT_JSON, report.json);
  fs.writeFileSync(REPORT_TABLE, report.table);

  console.log('\n' + report.table + '\n');
  console.log(`[perf] JSON  → ${REPORT_JSON}`);
  console.log(`[perf] Table → ${REPORT_TABLE}`);

  expect(report.results).toHaveLength(PERF_SCENES.length);
  for (const r of report.results) {
    expect(r.snapshot.frameMs, `${r.scene.id} produced a non-positive frameMs`).toBeGreaterThan(0);
    expect(Object.keys(r.snapshot.layers).length, `${r.scene.id} produced no layer data`).toBeGreaterThan(0);
  }
});

// ---------------------------------------------------------------------------
// Accept #3 — pixel non-perturbation: perfCollector instrumentation must not
// change rendered output. 6 biomes, CALM load (matches the WO's "6 biomes"
// framing for this leg — EXTREME is covered by the per-scene table above
// exercising the same draw paths with perfCollector on).
// ---------------------------------------------------------------------------

const CALM_SCENES = PERF_SCENES.filter((s) => s.load === 'CALM');

for (const scene of CALM_SCENES) {
  test(`pixel non-perturbation — ${scene.id} (perfCollector off vs on)`, async ({ context }) => {
    const pageOff = await context.newPage();
    await loadScene(pageOff, scene, /* perfOn */ false);
    const dataUrlOff = await pageOff.evaluate(() => {
      const c = document.querySelector('[data-testid="vista-proof-container"] canvas') as HTMLCanvasElement;
      return c.toDataURL('image/png');
    });
    await pageOff.close();

    const pageOn = await context.newPage();
    await loadScene(pageOn, scene, /* perfOn */ true);
    const dataUrlOn = await pageOn.evaluate(() => {
      const c = document.querySelector('[data-testid="vista-proof-container"] canvas') as HTMLCanvasElement;
      return c.toDataURL('image/png');
    });
    await pageOn.close();

    const bufOff = Buffer.from(dataUrlOff.split(',')[1], 'base64');
    const bufOn  = Buffer.from(dataUrlOn.split(',')[1], 'base64');

    fs.writeFileSync(path.join(ARTIFACTS_DIR, `perf-nonperturbation-${scene.id}-off.png`), bufOff);
    fs.writeFileSync(path.join(ARTIFACTS_DIR, `perf-nonperturbation-${scene.id}-on.png`), bufOn);

    expect(bufOff.length, 'a real rendered scene PNG must not be a blank frame').toBeGreaterThan(20_000);
    expect(bufOn.equals(bufOff), `${scene.id}: perfCollector instrumentation changed rendered pixels`).toBe(true);
  });
}

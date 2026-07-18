/**
 * Vista TK-2 Emissive Light Source — pixel proof.
 *
 * Two proofs, both against REAL saved baselines captured BEFORE this WO
 * (playwright/artifacts/perf-nonperturbation-*_CALM-off.png — written
 * 2026-07-10 by WO-PERF-HARNESS's sub-part (b), well before any TK-2 code
 * existed):
 *
 *   1. UNTOUCHED biomes (OCEANIC/BARREN/TERRAN — no PlanetProfile.emissiveSource
 *      configured) — a fresh capture must be BYTE-IDENTICAL to the pre-TK2
 *      baseline. This is the WO's core safety constraint.
 *
 *   2. EMISSIVE biomes (VOLCANIC/lava, ICE/aurora, MOUNTAINOUS/alpenglow) —
 *      a fresh capture must DIFFER from the pre-TK2 baseline (proves the
 *      glow actually changed something, not a silently-dead code path), and
 *      is saved as the requested "per-biome glow screenshot at the 3
 *      emissive seeds" proof artifact.
 *
 * Uses the SAME 6 CALM scenes as WO-PERF-HARNESS/WO-PERF-BUDGET-GATE
 * (src/vista/perf/scenes.ts) via the SAME __VISTA_PERF_INPUT__ injection
 * hook (VistaProof.tsx:674) — no new lab-route surface needed.
 */

import { test, expect } from '@playwright/test';
import * as fs from 'fs';
import * as path from 'path';
import { PERF_SCENES } from '../../src/vista/perf/scenes';

const ARTIFACTS_DIR = path.resolve(process.cwd(), 'playwright/artifacts');

const UNTOUCHED = ['OCEANIC_CALM', 'BARREN_CALM', 'TERRAN_CALM'];
const EMISSIVE  = ['VOLCANIC_CALM', 'ICE_CALM', 'MOUNTAINOUS_CALM'];

async function captureCanvas(page: import('@playwright/test').Page, sceneId: string): Promise<Buffer> {
  const scene = PERF_SCENES.find((s) => s.id === sceneId)!;
  await page.addInitScript((input) => {
    (window as unknown as { __VISTA_PERF_INPUT__: unknown }).__VISTA_PERF_INPUT__ = input;
  }, scene.input);
  await page.goto('/lab/vista-proof');
  await page.waitForSelector('[data-testid="vista-proof-ready"]', { state: 'attached', timeout: 15_000 });

  const dataUrl = await page.evaluate(() => {
    const c = document.querySelector('[data-testid="vista-proof-container"] canvas') as HTMLCanvasElement;
    return c.toDataURL('image/png');
  });
  return Buffer.from(dataUrl.split(',')[1], 'base64');
}

test.beforeAll(() => {
  fs.mkdirSync(ARTIFACTS_DIR, { recursive: true });
});

for (const sceneId of UNTOUCHED) {
  test(`untouched biome byte-identical — ${sceneId}`, async ({ page }) => {
    const baselinePath = path.join(ARTIFACTS_DIR, `perf-nonperturbation-${sceneId}-off.png`);
    expect(fs.existsSync(baselinePath), `pre-TK2 baseline missing: ${baselinePath}`).toBe(true);
    const baseline = fs.readFileSync(baselinePath);

    const fresh = await captureCanvas(page, sceneId);

    expect(fresh.length, 'a real rendered scene PNG must not be a blank frame').toBeGreaterThan(20_000);
    expect(
      fresh.equals(baseline),
      `${sceneId}: TK-2 changed pixels for an UNTOUCHED biome (no emissiveSource configured) — this must never happen`,
    ).toBe(true);
  });
}

for (const sceneId of EMISSIVE) {
  test(`emissive biome shows a new glow — ${sceneId}`, async ({ page }) => {
    const baselinePath = path.join(ARTIFACTS_DIR, `perf-nonperturbation-${sceneId}-off.png`);
    expect(fs.existsSync(baselinePath), `pre-TK2 baseline missing: ${baselinePath}`).toBe(true);
    const baseline = fs.readFileSync(baselinePath);

    const fresh = await captureCanvas(page, sceneId);
    const outPath = path.join(ARTIFACTS_DIR, `tk2-glow-${sceneId}.png`);
    fs.writeFileSync(outPath, fresh);

    expect(fresh.length, 'a real rendered scene PNG must not be a blank frame').toBeGreaterThan(20_000);
    expect(
      fresh.equals(baseline),
      `${sceneId}: expected the TK-2 glow to change rendered pixels vs the pre-TK2 baseline, but they're identical`,
    ).toBe(false);

    console.log(`[tk2] ${sceneId} glow proof → ${outPath} (${fresh.length} bytes, baseline was ${baseline.length})`);
  });
}

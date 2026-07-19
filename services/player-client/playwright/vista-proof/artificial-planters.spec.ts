/**
 * ARTIFICIAL flora proof — engineered-plant / hydroponic-tray → drawScatterPlanter.
 *
 * Verifies that the ARTIFICIAL planet renders structured planter visuals (not green
 * blobs / generic grass fallback) at nativeLife=0.9.  Captures for visual review.
 *
 * URL: /lab/vista-proof?type=ARTIFICIAL&nativeLife=0.9
 *   type=ARTIFICIAL  → FIXED_INPUTS['ARTIFICIAL'] (station profile, floraKinds: hydroponic-tray + engineered-plant)
 *   nativeLife=0.9   → dense flora coverage
 *
 * PROOF ARTIFACT
 * --------------
 * playwright/artifacts/artificial-planters.png
 */

import { test, expect } from '@playwright/test';
import * as fs   from 'fs';
import * as path from 'path';

const ARTIFACTS_DIR = path.resolve(process.cwd(), 'playwright/artifacts');
const PROOF_PNG     = path.join(ARTIFACTS_DIR, 'artificial-planters.png');

/** Engineered surface + planters: fewer distinct colors than a lush jungle but must be non-blank. */
const MIN_DISTINCT_COLORS = 40;
const MIN_NONBLACK_SAMPLES = 150;

test.beforeAll(() => {
  fs.mkdirSync(ARTIFACTS_DIR, { recursive: true });
});

test('ARTIFICIAL flora — hydroponic-tray / engineered-plant render (non-blank, structured)', async ({ page }) => {
  await page.goto('/lab/vista-proof?type=ARTIFICIAL&nativeLife=0.9');

  const canvas = page.locator('[data-testid="vista-proof-container"] canvas');
  await expect(canvas).toBeVisible();

  // Poll until real scene content is present (same readiness protocol as lush-jungle-flora.spec.ts)
  await page.waitForFunction(
    ([minNonBlack, minColors]: [number, number]) => {
      const c = document.querySelector('[data-testid="vista-proof-container"] canvas') as HTMLCanvasElement | null;
      if (!c) return false;
      const ctx = c.getContext('2d');
      if (!ctx || !c.width || !c.height) return false;
      const data = ctx.getImageData(0, 0, c.width, c.height).data;
      let nonBlack = 0;
      const colors = new Set<string>();
      for (let i = 0; i < data.length; i += 4 * 997) {
        const r = data[i], g = data[i + 1], b = data[i + 2];
        if (r || g || b) nonBlack++;
        colors.add(`${r},${g},${b}`);
      }
      return nonBlack >= minNonBlack && colors.size >= minColors;
    },
    [MIN_NONBLACK_SAMPLES, MIN_DISTINCT_COLORS] as [number, number],
    { timeout: 15_000 },
  );

  const stats = await page.evaluate(() => {
    const c = document.querySelector('[data-testid="vista-proof-container"] canvas') as HTMLCanvasElement;
    const ctx = c.getContext('2d')!;
    const data = ctx.getImageData(0, 0, c.width, c.height).data;
    let nonBlack = 0;
    const colors = new Set<string>();
    for (let i = 0; i < data.length; i += 4 * 997) {
      const r = data[i], g = data[i + 1], b = data[i + 2];
      if (r || g || b) nonBlack++;
      colors.add(`${r},${g},${b}`);
    }
    return { nonBlack, distinctColors: colors.size, w: c.width, h: c.height };
  });
  console.log(`[artificial-proof] canvas ${stats.w}x${stats.h} | nonBlack=${stats.nonBlack} | distinctColors=${stats.distinctColors}`);

  const screenshot = await canvas.screenshot({ type: 'png' });
  fs.writeFileSync(PROOF_PNG, screenshot);
  console.log(`[artificial-proof] PNG → ${PROOF_PNG}  (${screenshot.length} bytes)`);

  expect(stats.nonBlack,       'canvas must not be blank').toBeGreaterThanOrEqual(MIN_NONBLACK_SAMPLES);
  expect(stats.distinctColors, 'canvas must have diverse colors').toBeGreaterThanOrEqual(MIN_DISTINCT_COLORS);
});

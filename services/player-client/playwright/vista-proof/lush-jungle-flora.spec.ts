/**
 * FLORA-RENDER density proof — lush JUNGLE capture.
 *
 * Verifies that the land band reads as a dense forest at max-lush settings
 * (nativeLife=0.95, habitability=0.95) after the FLORA-RENDER LOD + sizing overhaul.
 *
 * URL params feed into VistaProof.tsx slider-override logic:
 *   ?type=JUNGLE               → FIXED_INPUTS['JUNGLE'] as base
 *   &nativeLife=0.95           → planet.nativeLife = 0.95
 *   &habitability=0.95         → planet.habitability = round(0.95 * 100) = 95
 *
 * With the sizing fix (flora scaleFactor=0.85), primary flora sizePx ≈ 14–44px
 * and dense scatter (lifeDenseCount ≈ 162) sizePx ≈ 8–27px — readable silhouettes.
 *
 * PROOF ARTIFACT
 * --------------
 * playwright/artifacts/flora-lush-jungle.png
 */

import { test, expect } from '@playwright/test';
import * as fs   from 'fs';
import * as path from 'path';

const ARTIFACTS_DIR = path.resolve(process.cwd(), 'playwright/artifacts');
const PROOF_PNG     = path.join(ARTIFACTS_DIR, 'flora-lush-jungle.png');

/** Min distinct colors — a forest has many green shades + sky + terrain. */
const MIN_DISTINCT_COLORS = 80;
/** Min non-black samples — dense land coverage. */
const MIN_NONBLACK_SAMPLES = 300;

test.beforeAll(() => {
  fs.mkdirSync(ARTIFACTS_DIR, { recursive: true });
});

test('FLORA-RENDER — lush JUNGLE reads as dense forest (non-blank, multi-color)', async ({ page }) => {
  await page.goto('/lab/vista-proof?type=JUNGLE&nativeLife=0.95&habitability=0.95');

  const canvas = page.locator('[data-testid="vista-proof-container"] canvas');
  await expect(canvas).toBeVisible();

  // Poll until real scene content is present (same readiness protocol as the
  // named-storm anchor — prevents false passes on blank/loading frames).
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

  // Capture pixel stats for the log
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
  console.log(`[flora-proof] canvas ${stats.w}x${stats.h} | nonBlack=${stats.nonBlack} | distinctColors=${stats.distinctColors}`);

  // Screenshot for visual review
  const screenshot = await canvas.screenshot({ type: 'png' });
  fs.writeFileSync(PROOF_PNG, screenshot);
  console.log(`[flora-proof] PNG → ${PROOF_PNG}  (${screenshot.length} bytes)`);

  // Assertions — non-blank and color-rich
  expect(stats.nonBlack,       'canvas must not be blank').toBeGreaterThanOrEqual(MIN_NONBLACK_SAMPLES);
  expect(stats.distinctColors, 'canvas must have diverse colors').toBeGreaterThanOrEqual(MIN_DISTINCT_COLORS);
});

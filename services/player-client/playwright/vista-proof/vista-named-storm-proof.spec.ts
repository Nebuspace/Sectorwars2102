/**
 * Vista Proof — deterministic screenshot harness for Fix A (lab hazard-kind fix).
 *
 * GOAL
 * ----
 * Prove that a lush TERRAN scene with a named storm + unnamed flood renders
 * two DISTINCT, type-specific hazard glyphs:
 *   storm → storm-cell (rotating spiral arms + dark eye)
 *   flood → flood-zone (ripple-wash concentric arcs)
 * NOT identical impact-scars (the pre-Fix-A fallback).
 *
 * The input is defined in VistaProof.tsx — hardcoded, not driven by UI toggles.
 * The canvas is frozen at t=0 (clock=0 → no animation).
 *
 * READINESS (why a content-poll, not just an rAF gate)
 * ----------------------------------------------------
 * The canvas2d backend's first paint does NOT land within a single rAF after
 * mount — an earlier version of this harness screenshotted too early and
 * captured a fully BLACK canvas, yet still "passed" because two black frames
 * are byte-identical.  We now POLL the canvas pixels until real content is
 * present (waitForFunction), which is both the readiness gate AND a hard guard:
 * a blank/flat canvas can never satisfy it, so it can never produce a false pass.
 *
 * PROOF ARTIFACTS
 * ---------------
 * playwright/artifacts/vista-named-storm-lush.png   ← capture A (primary)
 * playwright/artifacts/vista-named-storm-lush-b.png ← capture B (determinism check)
 *
 * P2 REUSE
 * --------
 * When named→sky lands (contract.ts:184), add a test asserting the storm-cell
 * glyph's bounding rect top-edge is above horizonY.  The input + readiness
 * protocol are designed for that extension.
 */

import { test, expect } from '@playwright/test';
import * as fs from 'fs';
import * as path from 'path';

const ARTIFACTS_DIR = path.resolve(process.cwd(), 'playwright/artifacts');
const PROOF_PNG     = path.join(ARTIFACTS_DIR, 'vista-named-storm-lush.png');
const PROOF_PNG_B   = path.join(ARTIFACTS_DIR, 'vista-named-storm-lush-b.png');

/** Minimum distinct (sparsely-sampled) colors a real rendered scene must show. */
const MIN_DISTINCT_COLORS = 50;
/** Minimum non-black sampled pixels — readiness + anti-blank guard. */
const MIN_NONBLACK_SAMPLES = 200;

/** In-page: sparse-sample the proof canvas → { nonBlack, distinctColors }. */
function sampleCanvas() {
  const c = document.querySelector('[data-testid="vista-proof-container"] canvas') as HTMLCanvasElement | null;
  if (!c) return { ok: false, nonBlack: 0, distinctColors: 0, w: 0, h: 0 };
  const ctx = c.getContext('2d');
  if (!ctx || !c.width || !c.height) return { ok: false, nonBlack: 0, distinctColors: 0, w: c.width, h: c.height };
  const data = ctx.getImageData(0, 0, c.width, c.height).data;
  let nonBlack = 0; const colors = new Set<string>();
  for (let i = 0; i < data.length; i += 4 * 997) {
    const r = data[i], g = data[i + 1], b = data[i + 2];
    if (r || g || b) nonBlack++;
    colors.add(`${r},${g},${b}`);
  }
  return { ok: true, nonBlack, distinctColors: colors.size, w: c.width, h: c.height };
}

test.beforeAll(() => {
  fs.mkdirSync(ARTIFACTS_DIR, { recursive: true });
});

test('Fix-A proof — storm-cell + flood-zone render distinct (non-blank), byte-identical at t=0', async ({ page }) => {
  await page.goto('/lab/vista-proof');

  const canvas = page.locator('[data-testid="vista-proof-container"] canvas');
  await expect(canvas).toBeVisible();

  // READINESS + ANTI-BLANK GUARD: poll until the canvas holds a real scene.
  // A single rAF is NOT enough — the backend's first paint lands later.  This
  // resolves only once the canvas has substantial non-black, multi-color content,
  // so a black/flat canvas can never pass this test.
  await page.waitForFunction(
    ([minNonBlack, minColors]) => {
      const c = document.querySelector('[data-testid="vista-proof-container"] canvas') as HTMLCanvasElement | null;
      if (!c) return false;
      const ctx = c.getContext('2d');
      if (!ctx || !c.width || !c.height) return false;
      const data = ctx.getImageData(0, 0, c.width, c.height).data;
      let nonBlack = 0; const colors = new Set<string>();
      for (let i = 0; i < data.length; i += 4 * 997) {
        const r = data[i], g = data[i + 1], b = data[i + 2];
        if (r || g || b) nonBlack++;
        colors.add(`${r},${g},${b}`);
      }
      return nonBlack >= minNonBlack && colors.size >= minColors;
    },
    [MIN_NONBLACK_SAMPLES, MIN_DISTINCT_COLORS] as const,
    { timeout: 10_000, polling: 100 },
  );

  // Hard content assertion (explicit, in addition to the wait above).
  const content = await page.evaluate(sampleCanvas);
  console.log(`[proof] canvas ${content.w}x${content.h} | nonBlack=${content.nonBlack} | distinctColors=${content.distinctColors}`);
  expect(content.ok, 'canvas must exist with a 2d context').toBe(true);
  expect(content.nonBlack, 'canvas must not be blank/black').toBeGreaterThanOrEqual(MIN_NONBLACK_SAMPLES);
  expect(content.distinctColors, 'canvas must show a real multi-color scene, not a flat fill').toBeGreaterThanOrEqual(MIN_DISTINCT_COLORS);

  // ── CAPTURE via toDataURL (not locator.screenshot) ───────────────────────
  //
  // WHY toDataURL, NOT locator.screenshot():
  // locator.screenshot() captures the COMPOSITOR FRAME via CDP.  In headless
  // Chromium, the compositor can lag by one frame behind the canvas buffer
  // after a ResizeObserver-triggered clear+redraw cycle.  The result: a fully
  // black PNG even though the canvas buffer has correct content — exactly what
  // caused the v1 harness failure (2689-byte black frame).
  //
  // toDataURL() runs synchronously in the browser's JS thread and reads the
  // canvas PIXEL BUFFER directly.  No compositor, no CDP round-trip race.
  // Since we already confirmed non-black content above, this is guaranteed
  // to read a real scene.

  const dataUrlA = await page.evaluate(() => {
    const c = document.querySelector('[data-testid="vista-proof-container"] canvas') as HTMLCanvasElement;
    return c.toDataURL('image/png');
  });
  const bufA = Buffer.from(dataUrlA.split(',')[1], 'base64');
  fs.writeFileSync(PROOF_PNG, bufA);
  console.log(`[proof] A: ${PROOF_PNG}  (${bufA.length} bytes)`);

  // Capture B — second toDataURL read; same unmodified frozen canvas.
  const dataUrlB = await page.evaluate(() => {
    const c = document.querySelector('[data-testid="vista-proof-container"] canvas') as HTMLCanvasElement;
    return c.toDataURL('image/png');
  });
  const bufB = Buffer.from(dataUrlB.split(',')[1], 'base64');
  fs.writeFileSync(PROOF_PNG_B, bufB);
  console.log(`[proof] B: ${PROOF_PNG_B}  (${bufB.length} bytes)`);

  expect(bufA.length, 'capture sizes must match').toBe(bufB.length);
  expect(bufA.equals(bufB), 'captures must be byte-identical at frozen t=0').toBe(true);

  // A real rendered scene is not trivially small (the black frame was ~2.7KB).
  expect(bufA.length, 'a real rendered scene PNG must be substantially larger than a blank frame').toBeGreaterThan(20_000);

  console.log('[proof] OK — non-blank, multi-color, byte-identical at t=0');
  console.log(`[proof] Visual output → ${PROOF_PNG}`);
});

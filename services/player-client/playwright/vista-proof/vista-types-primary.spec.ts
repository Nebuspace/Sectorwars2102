/**
 * Vista Type Coverage — 9 primary planet types (deterministic before/after harness).
 *
 * GOAL
 * ----
 * Capture a frozen (t=0) render for each of the 9 primary planet types and assert
 * each produces a real, non-blank, multi-color scene.  Captures land in
 * playwright/artifacts/ for side-by-side visual comparison.
 *
 * Types: TERRAN · JUNGLE · TROPICAL · MOUNTAINOUS · ICE · VOLCANIC · OCEANIC · BARREN · DESERT
 *
 * Each type uses a fixed VistaInput literal in VistaProof.tsx — randomVistaInput is
 * never called, so before/after captures are directly comparable (same input, different engine).
 *
 * SPLIT NOTE
 * ----------
 * This file covers the 9 primary planet types only.  The 5 V3-CELESTIAL special-case
 * draw paths (BLACK_HOLE, NEUTRON, RING_ARC, RINGED_MOON, PHASED_SIBLING) live in
 * vista-types-celestial.spec.ts so each playwright invocation stays ≤12 navigations.
 * (Together the two files replace the original vista-types.spec.ts.)
 *
 * READINESS / CAPTURE
 * -------------------
 * Mirrors the strategy in vista-named-storm-proof.spec.ts: waitForFunction polls
 * the canvas pixel buffer until non-black content is confirmed, then toDataURL()
 * reads the buffer directly (not via CDP compositor) to avoid lag races.
 */

import { test, expect } from '@playwright/test';
import * as fs from 'fs';
import * as path from 'path';

const ARTIFACTS_DIR = path.resolve(process.cwd(), 'playwright/artifacts');

/** Primary planet types this suite covers (9 of 12 — GAS_GIANT/ARCTIC/ARTIFICIAL deferred). */
const TYPES = [
  'TERRAN',
  'JUNGLE',
  'TROPICAL',
  'MOUNTAINOUS',
  'ICE',
  'VOLCANIC',
  'OCEANIC',
  'BARREN',
  'DESERT',
] as const;

type CoveredType = typeof TYPES[number];

/** Minimum distinct (sparsely-sampled) colors a real rendered scene must show. */
const MIN_DISTINCT_COLORS = 50;
/** Minimum non-black sampled pixels — readiness + anti-blank guard. */
const MIN_NONBLACK_SAMPLES = 200;
/** Minimum PNG byte size — a blank/black frame is ~2.7 KB. */
const MIN_PNG_BYTES = 20_000;

/** In-page: sparse-sample the proof canvas → { ok, nonBlack, distinctColors, w, h }. */
function sampleCanvas() {
  const c = document.querySelector('[data-testid="vista-proof-container"] canvas') as HTMLCanvasElement | null;
  if (!c) return { ok: false, nonBlack: 0, distinctColors: 0, w: 0, h: 0 };
  const ctx = c.getContext('2d');
  if (!ctx || !c.width || !c.height) return { ok: false, nonBlack: 0, distinctColors: 0, w: c.width, h: c.height };
  const data = ctx.getImageData(0, 0, c.width, c.height).data;
  let nonBlack = 0;
  const colors = new Set<string>();
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

// One test per type — sequential (workers=1 in the config), isolated failures.
for (const type of TYPES) {
  test(`type coverage — ${type} renders non-blank at t=0`, async ({ page }) => {
    await page.goto(`/lab/vista-proof?type=${type}`);

    const canvas = page.locator('[data-testid="vista-proof-container"] canvas');
    await expect(canvas).toBeVisible();

    // READINESS + ANTI-BLANK GUARD: poll until the canvas holds a real scene.
    await page.waitForFunction(
      ([minNonBlack, minColors]) => {
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
      [MIN_NONBLACK_SAMPLES, MIN_DISTINCT_COLORS] as const,
      { timeout: 15_000, polling: 100 },
    );

    // Hard content assertions.
    const content = await page.evaluate(sampleCanvas);
    console.log(
      `[type-proof] ${type}: canvas ${content.w}x${content.h}` +
      ` | nonBlack=${content.nonBlack} | distinctColors=${content.distinctColors}`,
    );
    expect(content.ok,             `${type}: canvas must exist with a 2d context`).toBe(true);
    expect(content.nonBlack,       `${type}: canvas must not be blank/black`).toBeGreaterThanOrEqual(MIN_NONBLACK_SAMPLES);
    expect(content.distinctColors, `${type}: must show a real multi-color scene`).toBeGreaterThanOrEqual(MIN_DISTINCT_COLORS);

    // CAPTURE via toDataURL (buffer read — immune to CDP compositor lag).
    const dataUrl = await page.evaluate(() => {
      const c = document.querySelector(
        '[data-testid="vista-proof-container"] canvas',
      ) as HTMLCanvasElement;
      return c.toDataURL('image/png');
    });
    const buf = Buffer.from(dataUrl.split(',')[1], 'base64');

    // VISTA_RUN_LABEL=before for a worktree BEFORE run; defaults to 'after'.
    const runLabel = process.env.VISTA_RUN_LABEL ?? 'after';
    const outPath = path.join(ARTIFACTS_DIR, `type-${(type as CoveredType).toLowerCase()}-${runLabel}.png`);
    fs.writeFileSync(outPath, buf);
    console.log(`[type-proof] ${runLabel.toUpperCase()}: ${outPath}  (${buf.length} bytes)`);

    expect(
      buf.length,
      `${type}: PNG must be substantially larger than a blank frame (~2.7 KB)`,
    ).toBeGreaterThan(MIN_PNG_BYTES);
  });
}

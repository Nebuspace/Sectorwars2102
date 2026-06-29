/**
 * Wave-3 visual proof captures — daytime and night passes.
 *
 * Captures deterministic frozen/timed frames for the new Wave-3 FX zones:
 *   Day  (t=0, FROZEN_DAY_PHASE, sun always up):
 *     TERRAN · JUNGLE · TROPICAL · OCEANIC · VOLCANIC · ICE · DESERT
 *     → flora silhouettes, water glitter, atmospheric particles visible
 *   Night (seed-specific 3am clock, sunAlt≈−0.71, sunUp=false):
 *     OCEANIC    → moon glitter on dark water
 *     BLACK_HOLE → starfield + dark sky with accretion-disc FX context
 *     RING_ARC   → ring arc in night sky
 *
 * OUTPUT FILES (10 total):
 *   playwright/artifacts/w3-<type>-day.png    (7 files)
 *   playwright/artifacts/w3-<label>-night.png (3 files)
 *
 * ASSERTIONS: each capture must show ≥50 distinct colors AND PNG size >20 KB.
 * These prove the scene is non-blank — a black/flat canvas compresses to ~2.7 KB.
 *
 * EXISTING SPECS UNCHANGED: this file adds captures only; it does not replace
 * vista-types.spec.ts, vista-named-storm-proof.spec.ts, or lab-viewport.spec.ts.
 *
 * READINESS: mirrors the anti-blank waitForFunction used in vista-types.spec.ts.
 * Night captures use lenient wait thresholds (any canvas content) so a legitimately
 * dark sky does not cause a timeout — the final assertions enforce the real bar.
 */

import { test, expect } from '@playwright/test';
import * as fs from 'fs';
import * as path from 'path';

const ARTIFACTS_DIR = path.resolve(process.cwd(), 'playwright/artifacts');

/** Minimum distinct (sparsely-sampled) colors the rendered scene must show. */
const MIN_DISTINCT_COLORS = 50;
/** Minimum PNG byte size — a blank/black frame is ~2.7 KB. */
const MIN_PNG_BYTES = 20_000;

// ---------------------------------------------------------------------------
// Canvas sampler — inline function used by page.evaluate() + waitForFunction()
// Identical implementation to vista-types.spec.ts so results are directly
// comparable.
// ---------------------------------------------------------------------------

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

/**
 * In-page readiness poll — waits until the canvas has at least some content.
 * Uses lenient thresholds so night / vacuum / very-dark scenes don't timeout.
 * The `waitForFunction` variant below passes [nonBlackMin, colorMin] as args.
 */
const readinessFn = ([nb, nc]: readonly [number, number]): boolean => {
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
  return nonBlack >= nb && colors.size >= nc;
};

test.beforeAll(() => {
  fs.mkdirSync(ARTIFACTS_DIR, { recursive: true });
});

// ---------------------------------------------------------------------------
// DAY CAPTURES — 7 planet types, frozen at t=0 (FROZEN_DAY_PHASE, sun always up)
// ---------------------------------------------------------------------------

const DAY_TYPES = [
  'TERRAN',
  'JUNGLE',
  'TROPICAL',
  'OCEANIC',
  'VOLCANIC',
  'ICE',
  'DESERT',
] as const;

for (const type of DAY_TYPES) {
  test(`W3 day — ${type} renders non-blank at t=0`, async ({ page }) => {
    await page.goto(`/lab/vista-proof?type=${type}`);

    const canvas = page.locator('[data-testid="vista-proof-container"] canvas');
    await expect(canvas).toBeVisible();

    // Content readiness: poll until the canvas shows real scene content.
    // Uses the same anti-blank guard pattern as vista-types.spec.ts.
    await page.waitForFunction(
      readinessFn,
      [200, 50] as const,   // day: expect ≥200 non-black + ≥50 colors
      { timeout: 20_000, polling: 100 },
    );

    const content = await page.evaluate(sampleCanvas);
    console.log(
      `[w3-day] ${type}: canvas ${content.w}x${content.h}` +
      ` | nonBlack=${content.nonBlack} | distinctColors=${content.distinctColors}`,
    );

    expect(content.ok,             `${type}: canvas must exist with a 2d context`).toBe(true);
    expect(content.distinctColors, `${type}: must show ≥${MIN_DISTINCT_COLORS} distinct colors`)
      .toBeGreaterThanOrEqual(MIN_DISTINCT_COLORS);

    // Capture via toDataURL (buffer read, not compositor capture — immune to CDP lag).
    const dataUrl = await page.evaluate(() => {
      const c = document.querySelector(
        '[data-testid="vista-proof-container"] canvas',
      ) as HTMLCanvasElement;
      return c.toDataURL('image/png');
    });
    const buf     = Buffer.from(dataUrl.split(',')[1], 'base64');
    const outPath = path.join(ARTIFACTS_DIR, `w3-${(type as string).toLowerCase()}-day.png`);
    fs.writeFileSync(outPath, buf);

    console.log(`[w3-day] CAPTURED: ${outPath}  (${buf.length} bytes)`);
    expect(buf.length, `${type}: PNG must exceed ${MIN_PNG_BYTES} bytes`).toBeGreaterThan(MIN_PNG_BYTES);
  });
}

// ---------------------------------------------------------------------------
// NIGHT CAPTURES — 3 special cases at 3am (seed-specific clock; sunUp=false)
// VistaProof computes the clock via nightClockFor() when ?phase=night is present.
// ---------------------------------------------------------------------------

const NIGHT_CASES = [
  {
    key:         'OCEANIC',
    label:       'oceanic',
    description: 'moon glitter on dark water surface',
  },
  {
    key:         'BLACK_HOLE',
    label:       'black-hole',
    description: 'starfield and dark accretion context at night',
  },
  {
    key:         'RING_ARC',
    label:       'ring-arc',
    description: 'planetary ring arc visible in night sky',
  },
] as const;

for (const { key, label, description } of NIGHT_CASES) {
  test(`W3 night — ${key} (${description})`, async ({ page }) => {
    await page.goto(`/lab/vista-proof?type=${key}&phase=night`);

    const canvas = page.locator('[data-testid="vista-proof-container"] canvas');
    await expect(canvas).toBeVisible();

    // Night readiness: lenient thresholds — a legitimately dark sky must not timeout.
    // Even at 3am: terrain has color (rock/soil/water above black) + stars + film grain.
    await page.waitForFunction(
      readinessFn,
      [50, 20] as const,    // night: lenient wait — any canvas content is fine
      { timeout: 20_000, polling: 100 },
    );

    const content = await page.evaluate(sampleCanvas);
    console.log(
      `[w3-night] ${key}: canvas ${content.w}x${content.h}` +
      ` | nonBlack=${content.nonBlack} | distinctColors=${content.distinctColors}`,
    );

    expect(content.ok,             `${key}: canvas must exist with a 2d context`).toBe(true);
    expect(content.distinctColors, `${key}: night scene must show ≥${MIN_DISTINCT_COLORS} distinct colors`)
      .toBeGreaterThanOrEqual(MIN_DISTINCT_COLORS);

    const dataUrl = await page.evaluate(() => {
      const c = document.querySelector(
        '[data-testid="vista-proof-container"] canvas',
      ) as HTMLCanvasElement;
      return c.toDataURL('image/png');
    });
    const buf     = Buffer.from(dataUrl.split(',')[1], 'base64');
    const outPath = path.join(ARTIFACTS_DIR, `w3-${label}-night.png`);
    fs.writeFileSync(outPath, buf);

    console.log(`[w3-night] CAPTURED: ${outPath}  (${buf.length} bytes)`);
    expect(buf.length, `${key}: night PNG must exceed ${MIN_PNG_BYTES} bytes`).toBeGreaterThan(MIN_PNG_BYTES);
  });
}

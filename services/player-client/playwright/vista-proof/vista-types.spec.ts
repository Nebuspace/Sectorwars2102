/**
 * Vista Type Coverage — deterministic AFTER/BEFORE screenshot harness.
 *
 * GOAL
 * ----
 * Capture a frozen (t=0) render for each of the 9 primary planet types and
 * assert each produces a real, non-blank, multi-color scene.  The captures
 * are written to playwright/artifacts/ for side-by-side visual comparison.
 *
 * Types: TERRAN · JUNGLE · TROPICAL · MOUNTAINOUS · ICE · VOLCANIC · OCEANIC · BARREN · DESERT
 *
 * Each type uses a fixed VistaInput literal defined in VistaProof.tsx —
 * randomVistaInput is never called so before/after captures are directly
 * comparable (same input, different engine).
 *
 * READINESS (reused from vista-named-storm-proof.spec.ts)
 * -------------------------------------------------------
 * Page waits for [data-testid="vista-proof-ready"] (set by VistaProof's
 * rAF pixel-poll) before capturing.  Additionally, a waitForFunction polls
 * the canvas pixel buffer — the same anti-blank guard used in the storm spec
 * — so a black/flat canvas cannot produce a false pass.
 *
 * CAPTURE
 * -------
 * toDataURL() (not locator.screenshot) reads the canvas pixel buffer directly,
 * avoiding CDP compositor lag.  Identical to the approach in the storm spec.
 *
 * ARTIFACTS
 * ---------
 * playwright/artifacts/type-<lowercase-type>-after.png   ← 9 AFTER captures
 * playwright/artifacts/type-<lowercase-type>-before.png  ← 9 BEFORE captures
 *   (BEFORE written by a separate run against commit 1fb4cec — see CLAUDE.md)
 */

import { test, expect } from '@playwright/test';
import * as fs from 'fs';
import * as path from 'path';

const ARTIFACTS_DIR = path.resolve(process.cwd(), 'playwright/artifacts');

/** Planet types this suite covers (9 of 12 — GAS_GIANT/ARCTIC/ARTIFICIAL deferred). */
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

// Generate one test per type — sequential (workers=1 in the config), isolated
// failures, matching the per-type output file naming.
for (const type of TYPES) {
  test(`type coverage — ${type} renders non-blank at t=0`, async ({ page }) => {
    await page.goto(`/lab/vista-proof?type=${type}`);

    const canvas = page.locator('[data-testid="vista-proof-container"] canvas');
    await expect(canvas).toBeVisible();

    // READINESS + ANTI-BLANK GUARD: poll until the canvas holds a real scene.
    // Reuses the exact strategy from vista-named-storm-proof.spec.ts — see that
    // file's inline comment for the full rationale on why a content-poll is needed
    // instead of a single rAF.
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

    // Hard content assertions (explicit, in addition to the waitForFunction above).
    const content = await page.evaluate(sampleCanvas);
    console.log(
      `[type-proof] ${type}: canvas ${content.w}x${content.h}` +
      ` | nonBlack=${content.nonBlack} | distinctColors=${content.distinctColors}`,
    );
    expect(content.ok,           `${type}: canvas must exist with a 2d context`).toBe(true);
    expect(content.nonBlack,     `${type}: canvas must not be blank/black`).toBeGreaterThanOrEqual(MIN_NONBLACK_SAMPLES);
    expect(content.distinctColors, `${type}: must show a real multi-color scene`).toBeGreaterThanOrEqual(MIN_DISTINCT_COLORS);

    // ── CAPTURE via toDataURL (not locator.screenshot) ────────────────────────
    //
    // toDataURL() reads the canvas PIXEL BUFFER synchronously in the JS thread.
    // locator.screenshot() captures via CDP (compositor frame) which can lag one
    // frame behind the buffer after a ResizeObserver clear+redraw cycle —
    // producing a black PNG even when the buffer is correct.
    const dataUrl = await page.evaluate(() => {
      const c = document.querySelector(
        '[data-testid="vista-proof-container"] canvas',
      ) as HTMLCanvasElement;
      return c.toDataURL('image/png');
    });
    const buf = Buffer.from(dataUrl.split(',')[1], 'base64');

    // VISTA_RUN_LABEL=before for the worktree BEFORE run; defaults to 'after'.
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

// ---------------------------------------------------------------------------
// WO-V3-CELESTIAL draw-path coverage
// Verify that the 5 special draw paths introduced in Wave 3 each produce a
// real, non-blank scene.  The thresholds are intentionally relaxed vs the
// regular type suite — a BLACK_HOLE / NEUTRON world is legitimately very dark.
// ---------------------------------------------------------------------------

const V3_SPECIAL_CASES = [
  { key: 'BLACK_HOLE',     label: 'accretion disc',       minColors: 30, minNonBlack: 100 },
  { key: 'NEUTRON',        label: 'pulsar beams',         minColors: 30, minNonBlack: 100 },
  { key: 'RING_ARC',       label: 'overhead ring arc',    minColors: 50, minNonBlack: 200 },
  { key: 'RINGED_MOON',    label: 'ringed moon',          minColors: 50, minNonBlack: 200 },
  { key: 'PHASED_SIBLING', label: 'phased sibling body',  minColors: 50, minNonBlack: 200 },
] as const;

for (const { key, label, minColors, minNonBlack } of V3_SPECIAL_CASES) {
  test(`V3-CELESTIAL draw-path — ${key} (${label}) renders non-blank`, async ({ page }) => {
    await page.goto(`/lab/vista-proof?type=${key}`);

    const canvas = page.locator('[data-testid="vista-proof-container"] canvas');
    await expect(canvas).toBeVisible();

    await page.waitForFunction(
      ([nb, nc]) => {
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
      },
      [minNonBlack, minColors] as const,
      { timeout: 15_000, polling: 100 },
    );

    const content = await page.evaluate(sampleCanvas);
    console.log(
      `[v3-proof] ${key}: canvas ${content.w}x${content.h}` +
      ` | nonBlack=${content.nonBlack} | distinctColors=${content.distinctColors}`,
    );
    expect(content.ok,             `${key}: canvas must exist`).toBe(true);
    expect(content.nonBlack,       `${key}: must not be blank`).toBeGreaterThanOrEqual(minNonBlack);
    expect(content.distinctColors, `${key}: must show a multi-color scene`).toBeGreaterThanOrEqual(minColors);
  });
}

/**
 * Vista Type Coverage — V3-CELESTIAL draw-path special cases (5 types).
 *
 * GOAL
 * ----
 * Verify that the 5 special draw paths introduced in Wave 3 each produce a
 * real, non-blank scene.  Thresholds are intentionally relaxed vs the primary
 * type suite — BLACK_HOLE / NEUTRON worlds are legitimately very dark.
 *
 * Types: BLACK_HOLE · NEUTRON · RING_ARC · RINGED_MOON · PHASED_SIBLING
 *
 * SPLIT NOTE
 * ----------
 * This file covers the 5 V3-CELESTIAL special cases only.  The 9 primary
 * planet types live in vista-types-primary.spec.ts.  Together the two files
 * replace the original vista-types.spec.ts so each playwright invocation stays
 * ≤12 navigations.
 *
 * READINESS / CAPTURE
 * -------------------
 * Mirrors the strategy in vista-named-storm-proof.spec.ts: waitForFunction polls
 * the canvas pixel buffer until some non-black content is confirmed.  Thresholds
 * are lenient for dark (vacuum/black-hole) scenes — the final assertions enforce
 * the real bar.
 */

import { test, expect } from '@playwright/test';
import * as fs from 'fs';
import * as path from 'path';

const ARTIFACTS_DIR = path.resolve(process.cwd(), 'playwright/artifacts');

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

// ---------------------------------------------------------------------------
// V3-CELESTIAL draw-path coverage — 5 special cases
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

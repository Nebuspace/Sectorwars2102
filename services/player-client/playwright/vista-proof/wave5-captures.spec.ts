/**
 * Wave-5 visual proof captures — the 4 per-type signature surfaces.
 *
 *   OCEANIC      day + night — swell field + sun/moon-glitter + archipelago islets
 *   TROPICAL     day + night — turquoise shallows + reef ring + palm-fringe shore
 *   MOUNTAINOUS  day         — layered snow-capped ridgelines + scree/treeline
 *   ARCTIC       night + day — auroral curtains (night) + frozen tundra/hummocks
 *
 * OUTPUT: playwright/artifacts/w5-<type>-<phase>.png
 *
 * Capture mechanism mirrors wave3-captures.spec.ts exactly: toDataURL buffer
 * read (immune to CDP compositor lag) + content-poll readiness + a hard
 * non-blank guard (≥50 distinct colors, PNG > 20 KB — a black frame is ~2.7 KB).
 * Night uses lenient readiness so a legitimately dark sky doesn't time out.
 */
import { test, expect } from '@playwright/test';
import * as fs from 'fs';
import * as path from 'path';

const ARTIFACTS_DIR = path.resolve(process.cwd(), 'playwright/artifacts');
const MIN_DISTINCT_COLORS = 50;
const MIN_PNG_BYTES = 20_000;

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

test.beforeAll(() => { fs.mkdirSync(ARTIFACTS_DIR, { recursive: true }); });

// type, phase ('' = day | 'night'), readiness [nonBlackMin, colorMin]
const CASES: Array<[string, '' | 'night', readonly [number, number]]> = [
  ['OCEANIC',     '',      [200, 50]],
  ['OCEANIC',     'night', [50, 20]],
  ['TROPICAL',    '',      [200, 50]],
  ['TROPICAL',    'night', [50, 20]],
  ['MOUNTAINOUS', '',      [200, 50]],
  ['ARCTIC',      'night', [50, 20]],
  ['ARCTIC',      '',      [200, 50]],
];

for (const [type, phase, readiness] of CASES) {
  const label = phase === 'night' ? `${type} night` : `${type} day`;
  test(`W5 — ${label} renders its signature non-blank`, async ({ page }) => {
    const q = phase === 'night' ? `?type=${type}&phase=night` : `?type=${type}`;
    await page.goto(`/lab/vista-proof${q}`);

    const canvas = page.locator('[data-testid="vista-proof-container"] canvas');
    await expect(canvas).toBeVisible();

    await page.waitForFunction(readinessFn, readiness, { timeout: 20_000, polling: 100 });

    const content = await page.evaluate(sampleCanvas);
    console.log(`[w5] ${label}: ${content.w}x${content.h} | nonBlack=${content.nonBlack} | colors=${content.distinctColors}`);

    expect(content.ok, `${label}: canvas + 2d context`).toBe(true);
    expect(content.distinctColors, `${label}: ≥${MIN_DISTINCT_COLORS} colors`).toBeGreaterThanOrEqual(MIN_DISTINCT_COLORS);

    const dataUrl = await page.evaluate(() => {
      const c = document.querySelector('[data-testid="vista-proof-container"] canvas') as HTMLCanvasElement;
      return c.toDataURL('image/png');
    });
    const buf = Buffer.from(dataUrl.split(',')[1], 'base64');
    const suffix = phase === 'night' ? 'night' : 'day';
    const outPath = path.join(ARTIFACTS_DIR, `w5-${type.toLowerCase()}-${suffix}.png`);
    fs.writeFileSync(outPath, buf);

    console.log(`[w5] CAPTURED: ${outPath} (${buf.length} bytes)`);
    expect(buf.length, `${label}: PNG > ${MIN_PNG_BYTES} bytes`).toBeGreaterThan(MIN_PNG_BYTES);
  });
}

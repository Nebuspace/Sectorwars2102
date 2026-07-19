/**
 * Wave-6 visual proof captures — the 3 tech/dead-world signature surfaces.
 *
 *   ARTIFICIAL   day + night — megastructure skyline; windows glow at night
 *   GAS_GIANT    day         — full-frame banded atmosphere + storm oval (no ground)
 *   BARREN       day + night — crater field + terminator shadow + dense starfield
 *
 * OUTPUT: playwright/artifacts/w6-<type>-<phase>.png
 *
 * Same capture mechanism as wave5-captures.spec.ts: toDataURL buffer read +
 * content-poll readiness + hard non-blank guard (≥50 colors, PNG > 20 KB).
 * GAS_GIANT/BARREN are airless or surface-less — readiness stays lenient so a
 * legitimately stark frame doesn't time out; final assertions enforce the bar.
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
  ['ARTIFICIAL', '',      [200, 50]],
  ['ARTIFICIAL', 'night', [50, 20]],
  ['GAS_GIANT',  '',      [200, 50]],
  ['BARREN',     '',      [100, 40]],
  ['BARREN',     'night', [40, 20]],
];

for (const [type, phase, readiness] of CASES) {
  const label = phase === 'night' ? `${type} night` : `${type} day`;
  test(`W6 — ${label} renders its signature non-blank`, async ({ page }) => {
    const q = phase === 'night' ? `?type=${type}&phase=night` : `?type=${type}`;
    await page.goto(`/lab/vista-proof${q}`);

    const canvas = page.locator('[data-testid="vista-proof-container"] canvas');
    await expect(canvas).toBeVisible();

    await page.waitForFunction(readinessFn, readiness, { timeout: 20_000, polling: 100 });

    const content = await page.evaluate(sampleCanvas);
    console.log(`[w6] ${label}: ${content.w}x${content.h} | nonBlack=${content.nonBlack} | colors=${content.distinctColors}`);

    expect(content.ok, `${label}: canvas + 2d context`).toBe(true);
    expect(content.distinctColors, `${label}: ≥${MIN_DISTINCT_COLORS} colors`).toBeGreaterThanOrEqual(MIN_DISTINCT_COLORS);

    const dataUrl = await page.evaluate(() => {
      const c = document.querySelector('[data-testid="vista-proof-container"] canvas') as HTMLCanvasElement;
      return c.toDataURL('image/png');
    });
    const buf = Buffer.from(dataUrl.split(',')[1], 'base64');
    const suffix = phase === 'night' ? 'night' : 'day';
    const outPath = path.join(ARTIFACTS_DIR, `w6-${type.toLowerCase()}-${suffix}.png`);
    fs.writeFileSync(outPath, buf);

    console.log(`[w6] CAPTURED: ${outPath} (${buf.length} bytes)`);
    expect(buf.length, `${label}: PNG > ${MIN_PNG_BYTES} bytes`).toBeGreaterThan(MIN_PNG_BYTES);
  });
}

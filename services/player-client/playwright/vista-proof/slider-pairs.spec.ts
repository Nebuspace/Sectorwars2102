/**
 * Slider DRIVEN Proof — min/max pair captures for the 5 living-detail sliders.
 *
 * GOAL
 * ----
 * For each of the 5 slider inputs (waterCoverage, temperature, nativeLife,
 * atmDensity, habitability), capture a MIN (0.05) and MAX (0.95) render using
 * the TERRAN fixed input as the base scene.  Assert each capture is non-blank.
 * Log whether min ≠ max (visual diff) — once PIPELINE-LIVENESS integrates and
 * the sliders drive the model, the pairs WILL differ.
 *
 * URL OVERRIDE MECHANISM
 * ----------------------
 * VistaProof.tsx reads ?<slider>=<0..1> and merges it onto the selected
 * FIXED_INPUT.  Parameter mappings:
 *   waterCoverage  0..1 → planet.waterCoverage  (direct)
 *   temperature    0..1 → planet.temperature mapped to -1..+1 (0=frozen, 1=hot)
 *   nativeLife     0..1 → planet.nativeLife  (direct)
 *   atmDensity     0..1 → planet.atmosphere.density  (direct)
 *   habitability   0..1 → planet.habitability ×100  (contract scale 0-100)
 *
 * NAVIGATIONS: 5 sliders × 2 captures (MIN + MAX) = 10 total (≤12 limit).
 *
 * OUTPUT FILES (10):
 *   playwright/artifacts/slider-waterCoverage-min.png
 *   playwright/artifacts/slider-waterCoverage-max.png
 *   playwright/artifacts/slider-temperature-{min,max}.png
 *   playwright/artifacts/slider-nativeLife-{min,max}.png
 *   playwright/artifacts/slider-atmDensity-{min,max}.png
 *   playwright/artifacts/slider-habitability-{min,max}.png
 *
 * ASSERTIONS
 *   - Each PNG > 20 KB (non-blank frame guard — a blank canvas is ~2.7 KB)
 *   - min ≠ max is LOGGED but NOT a hard assertion: pairs will be byte-identical
 *     until PIPELINE-LIVENESS integrates; once integrated, they will differ and
 *     this comment can be replaced with `expect(differ).toBe(true)`.
 *
 * Config: playwright.vista-proof.group-d.config.ts
 */

import { test, expect, type Page } from '@playwright/test';
import * as fs from 'fs';
import * as path from 'path';

const ARTIFACTS_DIR = path.resolve(process.cwd(), 'playwright/artifacts');

const BASE_TYPE     = 'TERRAN';
const MIN_VAL       = 0.05;
const MAX_VAL       = 0.95;
/** Minimum PNG byte size — a blank/black frame compresses to ~2.7 KB. */
const MIN_PNG_BYTES = 20_000;
/** Minimum distinct colors for the content readiness poll. Lenient for dark scenes. */
const READY_MIN_COLORS   = 30;
const READY_MIN_NONBLACK = 50;

const SLIDERS = [
  'waterCoverage',
  'temperature',
  'nativeLife',
  'atmDensity',
  'habitability',
] as const;

type SliderKey = typeof SLIDERS[number];

// ---------------------------------------------------------------------------
// In-page helpers (serialized to the browser by Playwright — no module-scope
// references; all inputs must be passed as arguments).
// ---------------------------------------------------------------------------

/** Waits until the canvas has enough non-black pixels and distinct colors. */
function readinessFn([nb, nc]: readonly [number, number]): boolean {
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
}

/** Reads the proof canvas → base64 PNG data URL. */
function captureDataUrl(): string {
  const c = document.querySelector(
    '[data-testid="vista-proof-container"] canvas',
  ) as HTMLCanvasElement;
  return c.toDataURL('image/png');
}

/** Samples the proof canvas → { ok, nonBlack, distinctColors, w, h }. */
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

// ---------------------------------------------------------------------------
// Navigation helper — navigates to the proof URL, waits for readiness, and
// returns the canvas PNG as a Buffer.
// ---------------------------------------------------------------------------

async function captureAt(page: Page, slider: SliderKey, val: number): Promise<Buffer> {
  await page.goto(`/lab/vista-proof?type=${BASE_TYPE}&${slider}=${val}`);

  const canvas = page.locator('[data-testid="vista-proof-container"] canvas');
  await expect(canvas).toBeVisible();

  // Poll until the canvas has real rendered content — immune to rAF/ResizeObserver
  // ordering races (same anti-blank strategy as vista-types-primary.spec.ts).
  await page.waitForFunction(
    readinessFn,
    [READY_MIN_NONBLACK, READY_MIN_COLORS] as const,
    { timeout: 20_000, polling: 100 },
  );

  const content = await page.evaluate(sampleCanvas);
  console.log(
    `[slider-pairs] ${slider}=${val}: canvas ${content.w}x${content.h}` +
    ` | nonBlack=${content.nonBlack} | distinctColors=${content.distinctColors}`,
  );

  // toDataURL reads the pixel BUFFER directly — no CDP compositor lag.
  const dataUrl = await page.evaluate(captureDataUrl);
  return Buffer.from(dataUrl.split(',')[1], 'base64');
}

// ---------------------------------------------------------------------------
// Setup
// ---------------------------------------------------------------------------

test.beforeAll(() => {
  fs.mkdirSync(ARTIFACTS_DIR, { recursive: true });
});

// ---------------------------------------------------------------------------
// One test per slider — each navigates twice (MIN then MAX).
// ---------------------------------------------------------------------------

for (const slider of SLIDERS) {
  test(`slider ${slider}: min/max pair non-blank`, async ({ page }) => {
    // ── MIN capture ─────────────────────────────────────────────────────────
    const bufMin  = await captureAt(page, slider, MIN_VAL);
    const minPath = path.join(ARTIFACTS_DIR, `slider-${slider}-min.png`);
    fs.writeFileSync(minPath, bufMin);
    console.log(`[slider-pairs] ${slider} MIN: ${minPath}  (${bufMin.length} bytes)`);

    // ── MAX capture ─────────────────────────────────────────────────────────
    const bufMax  = await captureAt(page, slider, MAX_VAL);
    const maxPath = path.join(ARTIFACTS_DIR, `slider-${slider}-max.png`);
    fs.writeFileSync(maxPath, bufMax);
    console.log(`[slider-pairs] ${slider} MAX: ${maxPath}  (${bufMax.length} bytes)`);

    // ── Non-blank assertions (hard) ─────────────────────────────────────────
    expect(
      bufMin.length,
      `${slider} MIN PNG must be larger than a blank frame (~2.7 KB)`,
    ).toBeGreaterThan(MIN_PNG_BYTES);
    expect(
      bufMax.length,
      `${slider} MAX PNG must be larger than a blank frame (~2.7 KB)`,
    ).toBeGreaterThan(MIN_PNG_BYTES);

    // ── Differ check (informational until PIPELINE-LIVENESS integrates) ──────
    //
    // Once the pipeline wires slider values into the vista model, min and max
    // will produce visually different scenes and the byte buffers will differ.
    // Until then (PIPELINE-LIVENESS not yet integrated) both renders use the
    // same model state and the buffers may be byte-identical — which is expected.
    //
    // TODO: harden to `expect(differ, ...).toBe(true)` once PIPELINE-LIVENESS
    //       is integrated and slider-pairs confirms visual divergence end-to-end.
    const differ = !bufMin.equals(bufMax);
    if (differ) {
      console.log(`[slider-pairs] ${slider}: min≠max — pipeline is driving visual change ✓`);
    } else {
      console.warn(
        `[slider-pairs] ${slider}: min==max — byte-identical (PIPELINE-LIVENESS not yet integrated; ` +
        `expected once integrated)`,
      );
    }
  });
}

/**
 * Vista Lab Viewport Stability — regression guard for the ResizeObserver shrink bug.
 *
 * PROBLEM (fixed in react.tsx)
 * ----------------------------
 * The ResizeObserver callback set `canvas.style.width/height = contentRect.{w,h}`.
 * Mutating the CSS layout of the *observed* element (the canvas itself) re-fires the
 * observer on each paint cycle.  Sub-pixel / DPR-rounding drift caused the canvas to
 * shrink by a fraction of a pixel on every structural input change (type or seed
 * change → mountEngine() → ResizeObserver fires again → new slightly-smaller value
 * gets pinned → repeat).  After 10+ changes the shrink became visually measurable.
 *
 * FIX
 * ---
 * The observer now watches the PARENT CONTAINER (not the canvas), and never writes
 * `canvas.style.*` at all.  The canvas CSS is `width:100%;height:100%` — the
 * container is the layout source of truth.  Only the drawing-buffer attributes
 * (`canvas.width/height`, via handle.resize) are updated; those do NOT change CSS
 * layout and do NOT re-fire the observer.
 *
 * THIS SPEC
 * ---------
 * Navigate /lab/vista.  Record canvas clientWidth/clientHeight before and after
 * ≥10 structural + non-structural input changes.  Assert the dimensions are
 * identical (within ≤1 px sub-pixel tolerance) — the shrink-per-change pattern
 * would produce 10–50 px of drift and fail this assertion.
 *
 * Config: playwright.vista-proof.config.ts (local Vite dev server, port 5174,
 * no Docker dependency, no auth required).
 */

import { test, expect } from '@playwright/test';
import * as fs from 'fs';
import * as path from 'path';

const ARTIFACTS_DIR  = path.resolve(process.cwd(), 'playwright/artifacts');
const CANVAS_SEL     = '[data-testid="vista-lab-canvas-box"] canvas';

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/**
 * In-page content guard: returns true once the canvas holds a real rendered
 * scene (non-black pixel count ≥ 20 in the centre strip).
 *
 * Passed as an argument-taking function to page.waitForFunction() so `sel` is
 * serialised cleanly — avoids closure-capture surprises.
 */
function pageHasContent(sel: string): boolean {
  const c = document.querySelector(sel) as HTMLCanvasElement | null;
  if (!c || !c.width || !c.height) return false;
  const ctx = c.getContext('2d');
  if (!ctx) return false;
  const sW = Math.min(200, c.width);
  const sH = Math.min(100, c.height);
  const ox = Math.floor((c.width  - sW) / 2);
  const oy = Math.floor((c.height - sH) / 2);
  const { data } = ctx.getImageData(ox, oy, sW, sH);
  let count = 0;
  for (let i = 0; i < data.length; i += 16) {
    if (data[i] > 5 || data[i + 1] > 5 || data[i + 2] > 5) count++;
  }
  return count >= 20;
}

// ---------------------------------------------------------------------------
// Setup
// ---------------------------------------------------------------------------

test.beforeAll(() => {
  fs.mkdirSync(ARTIFACTS_DIR, { recursive: true });
});

// ---------------------------------------------------------------------------
// Test
// ---------------------------------------------------------------------------

test('lab viewport clientWidth/clientHeight stay constant after ≥10 input changes', async ({ page }) => {
  await page.goto('/lab/vista');

  // ── Readiness: wait for initial render ─────────────────────────────────────
  const canvas = page.locator(CANVAS_SEL);
  await expect(canvas).toBeVisible();

  await page.waitForFunction(pageHasContent, CANVAS_SEL, { timeout: 20_000, polling: 100 });

  // ── Capture baseline dimensions ────────────────────────────────────────────
  const initial = await canvas.evaluate((el: HTMLCanvasElement) => ({
    w: el.clientWidth,
    h: el.clientHeight,
  }));
  console.log(`[lab-viewport] initial: ${initial.w}×${initial.h}`);
  expect(initial.w, 'initial clientWidth must be > 0').toBeGreaterThan(0);
  expect(initial.h, 'initial clientHeight must be > 0').toBeGreaterThan(0);

  // ── ≥10 input changes ─────────────────────────────────────────────────────
  //
  // Mix of structural (type/seed) and non-structural (slider) changes to exercise
  // both the full remount path (mountEngine) and the hot-patch path (handle.update).
  //
  // Structural changes are the primary driver of the shrink bug: each one calls
  // mountEngine() which reads getBoundingClientRect(), then the ResizeObserver
  // fires again against the (now CSS-pinned) canvas.

  // 4 × type change (structural — triggers full dispose → generate → mount)
  for (const type of ['DESERT', 'ICE', 'VOLCANIC', 'OCEANIC']) {
    await page.click(`[data-testid="vista-lab-type-${type}"]`);
    // Wait for the new scene to render before the next change, giving the
    // ResizeObserver one full cycle to fire (if the bug were present).
    await page.waitForFunction(pageHasContent, CANVAS_SEL, { timeout: 10_000, polling: 100 });
  }

  // 4 × reseed (structural)
  for (let i = 0; i < 4; i++) {
    await page.click('[data-testid="vista-lab-reseed"]');
    await page.waitForFunction(pageHasContent, CANVAS_SEL, { timeout: 10_000, polling: 100 });
  }

  // 3 × habitability slider (non-structural — routes via handle.update())
  for (const val of [25, 75, 50]) {
    await page.locator('[data-testid="vista-lab-habitability"]').evaluate(
      (el: HTMLInputElement, v: number) => {
        el.value = String(v);
        el.dispatchEvent(new Event('input', { bubbles: true }));
      },
      val,
    );
    // 50 ms is enough for a ResizeObserver callback to fire in headless Chromium.
    await page.waitForTimeout(50);
  }

  // Total: 4 + 4 + 3 = 11 changes (≥ 10 required).

  // ── Settle ────────────────────────────────────────────────────────────────
  // Allow any pending ResizeObserver callbacks and RAF cycles to drain before
  // taking the final measurement.
  await page.waitForTimeout(200);

  // ── Assert stable dimensions ──────────────────────────────────────────────
  const after = await canvas.evaluate((el: HTMLCanvasElement) => ({
    w: el.clientWidth,
    h: el.clientHeight,
  }));
  console.log(`[lab-viewport] after 11 changes: ${after.w}×${after.h}`);

  // ≤1 px tolerance covers sub-pixel layout rounding across platforms/zoom levels.
  // The shrink bug produced 1–5 px drift *per structural change* → 10–50 px total.
  expect(
    Math.abs(after.w - initial.w),
    `clientWidth must not drift (initial=${initial.w}, after=${after.w})`,
  ).toBeLessThanOrEqual(1);
  expect(
    Math.abs(after.h - initial.h),
    `clientHeight must not drift (initial=${initial.h}, after=${after.h})`,
  ).toBeLessThanOrEqual(1);

  // ── Screenshot ────────────────────────────────────────────────────────────
  // toDataURL() reads the canvas pixel buffer synchronously (not via CDP
  // compositor), matching the anti-lag strategy used by the other vista specs.
  const dataUrl = await page.evaluate((sel: string) => {
    const c = document.querySelector(sel) as HTMLCanvasElement;
    return c.toDataURL('image/png');
  }, CANVAS_SEL);

  const buf     = Buffer.from(dataUrl.split(',')[1], 'base64');
  const outPath = path.join(ARTIFACTS_DIR, 'lab-viewport-stable.png');
  fs.writeFileSync(outPath, buf);
  console.log(`[lab-viewport] screenshot: ${outPath}  (${buf.length} bytes)`);
  expect(buf.length, 'screenshot PNG must be non-trivial').toBeGreaterThan(5_000);
});

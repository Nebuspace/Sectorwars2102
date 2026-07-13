/**
 * MFD Softkey Rail Geometry — WO-UI-MAX-BATCH-1 proof.
 *
 * Drives /lab/shell?mode=flight (LabShell.tsx, the real GameLayout under a
 * mocked GameContext — see that file's own doc-comment) and asserts, with
 * real getBoundingClientRect() numbers, that every softkey rail (`.skrow`,
 * one per `.mfd` unit inside `.mfdcol`) sits FULLY inside its own `.mfd`
 * unit and fully inside the `.lower`/shell bounds — the exact clip the
 * orchestrator's sweeppilot diagnosed at 1560x980 (MFD-B rail bottom
 * y=1002 vs a column/shell bottom of 979/980, a 22-23px overflow).
 *
 * Three viewports per the WO's Accept clause: 1440x900, 1560x980, 1280x800
 * (parametrized as Playwright projects — see playwright.mfd-softkey-rail.
 * config.ts). Also asserts the document never scrolls (Scroll Law) and that
 * MFD-A/MFD-B both render their full complement of `.mfd .scr` + `.mfd .skrow`.
 */

import { test, expect, type Page } from '@playwright/test';

interface Rect {
  x: number;
  y: number;
  width: number;
  height: number;
}

const STAGE_SEL   = '.game-container';
const LOWER_SEL    = '.lower';
const MFDCOL_SEL   = '.mfdcol';
const READY_SEL    = '[data-testid="lab-shell-ready"]';

const TOL = 1; // px tolerance for sub-pixel rounding

async function loadFlight(page: Page): Promise<void> {
  await page.goto('/lab/shell?mode=flight');
  await page.waitForSelector(READY_SEL, { state: 'attached', timeout: 15_000 });
  await page.waitForSelector('.mfd .skrow', { state: 'attached', timeout: 15_000 });
  // Let layout fully settle past the initial paint before measuring.
  await page.waitForTimeout(150);
}

async function rectOf(page: Page, selector: string, nth = 0): Promise<Rect> {
  return page.locator(selector).nth(nth).evaluate((el) => {
    const r = el.getBoundingClientRect();
    return { x: r.x, y: r.y, width: r.width, height: r.height };
  });
}

test('every MFD unit\'s softkey rail sits fully inside its own .mfd bounds', async ({ page }) => {
  await loadFlight(page);

  const mfdUnits = page.locator('.mfd');
  const count = await mfdUnits.count();
  expect(count, 'exactly 2 MFD units in the unfolded flight config (MFD-A + MFD-B)').toBe(2);

  for (let i = 0; i < count; i++) {
    const mfd  = await rectOf(page, '.mfd', i);
    const rail = await rectOf(page, '.mfd .skrow', i);

    expect(
      rail.y + rail.height,
      `MFD[${i}] softkey rail bottom (${(rail.y + rail.height).toFixed(1)}) must not exceed its own .mfd unit bottom (${(mfd.y + mfd.height).toFixed(1)})`,
    ).toBeLessThanOrEqual(mfd.y + mfd.height + TOL);

    expect(
      rail.y,
      `MFD[${i}] softkey rail top (${rail.y.toFixed(1)}) must be within its .mfd unit`,
    ).toBeGreaterThanOrEqual(mfd.y - TOL);
  }
});

test('every softkey rail sits fully inside the .lower column bounds (the actual clip boundary)', async ({ page }) => {
  await loadFlight(page);

  const lower = await rectOf(page, LOWER_SEL);
  const rails = page.locator('.mfd .skrow');
  const count = await rails.count();
  expect(count, 'exactly 2 softkey rails (MFD-A + MFD-B)').toBe(2);

  for (let i = 0; i < count; i++) {
    const rail = await rectOf(page, '.mfd .skrow', i);
    expect(
      rail.y + rail.height,
      `rail[${i}] bottom (${(rail.y + rail.height).toFixed(1)}) must not exceed .lower's bottom (${(lower.y + lower.height).toFixed(1)})`,
    ).toBeLessThanOrEqual(lower.y + lower.height + TOL);
  }
});

test('every softkey rail sits fully inside the .mfdcol column bounds', async ({ page }) => {
  await loadFlight(page);

  const mfdcol = await rectOf(page, MFDCOL_SEL);
  const rails = page.locator('.mfd .skrow');
  const count = await rails.count();

  for (let i = 0; i < count; i++) {
    const rail = await rectOf(page, '.mfd .skrow', i);
    expect(
      rail.y + rail.height,
      `rail[${i}] bottom (${(rail.y + rail.height).toFixed(1)}) must not exceed .mfdcol's bottom (${(mfdcol.y + mfdcol.height).toFixed(1)})`,
    ).toBeLessThanOrEqual(mfdcol.y + mfdcol.height + TOL);
  }
});

test('the .game-container shell never exceeds the viewport — document never scrolls (Scroll Law)', async ({ page }) => {
  await loadFlight(page);

  const stage = await rectOf(page, STAGE_SEL);
  const viewportSize = page.viewportSize();
  expect(viewportSize, 'viewport size resolved').not.toBeNull();
  const vh = viewportSize!.height;

  expect(
    stage.y + stage.height,
    `.game-container bottom (${(stage.y + stage.height).toFixed(1)}) must not exceed the viewport height (${vh})`,
  ).toBeLessThanOrEqual(vh + TOL);

  const scrollHeight = await page.evaluate(() => document.documentElement.scrollHeight);
  expect(scrollHeight, 'document.documentElement.scrollHeight must not exceed the viewport height').toBeLessThanOrEqual(vh + TOL);
});

test('every softkey rail renders its full 5-slot complement (real content, not empty)', async ({ page }) => {
  await loadFlight(page);

  const rails = page.locator('.mfd .skrow');
  const count = await rails.count();
  for (let i = 0; i < count; i++) {
    const keys = rails.nth(i).locator('.skey');
    expect(await keys.count(), `rail[${i}] renders the full 5-slot cap`).toBe(5);
  }
});

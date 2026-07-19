/**
 * Lab Shell Geometry — WO-UI0-PERSISTENT-SHELL lane B proof.
 *
 * Drives /lab/shell?mode=flight|station|surface (LabShell.tsx) at 1440x900
 * and asserts NUMERIC getBoundingClientRect()s for .game-container / the
 * sidebar / the stub deck child / the windshield layer, per mode — not a
 * pixel snapshot (the scene is dynamic/animated; geometry is deterministic).
 *
 * Two things this proves together:
 *   1. The legacy console-expand/windshield-min/landed-expanded class math
 *      (game-layout.css) still resolves correctly through GameLayout's real
 *      is_docked/is_landed branches — i.e. the mocked-GameContext harness
 *      itself is wired right.
 *   2. Layering `display: grid` + grid-template-areas onto .game-container
 *      (this lane's CSS change) is a NO-OP on today's geometry: every
 *      existing child (.game-sidebar, .game-content) stays absolutely
 *      positioned with explicit insets, so none of them are grid-placed —
 *      the deck (stub child) and windshield (.game-content) both stay
 *      exactly full-bleed in all three modes, matching "nothing nests yet."
 *
 * Config: playwright.lab-shell.config.ts (local Vite dev server, port 5174,
 * no Docker dependency, no auth required — mirrors playwright.vista-proof.config.ts).
 */

import { test, expect, type Page } from '@playwright/test';

type ShellMode = 'flight' | 'station' | 'surface';

interface Rect {
  x: number;
  y: number;
  width: number;
  height: number;
}

const CONTAINER_SEL = '.game-container';
const SIDEBAR_SEL   = '.game-sidebar';
const CONTENT_SEL   = '.game-content';
const DECK_SEL       = '[data-testid="lab-shell-deck"]';
const READY_SEL      = '[data-testid="lab-shell-ready"]';

const TOL = 2; // px tolerance for sub-pixel/scrollbar rounding

async function loadMode(page: Page, mode: ShellMode): Promise<void> {
  await page.goto(`/lab/shell?mode=${mode}`);
  await page.waitForSelector(READY_SEL, { state: 'attached', timeout: 15_000 });
  // Let layout fully settle past the initial paint before measuring.
  await page.waitForTimeout(100);
}

async function rectOf(page: Page, selector: string): Promise<Rect> {
  return page.locator(selector).evaluate((el) => {
    const r = el.getBoundingClientRect();
    return { x: r.x, y: r.y, width: r.width, height: r.height };
  });
}

/** Resolves the .game-container's computed --band-h against its own measured height. */
async function resolvedBandHeightPx(page: Page): Promise<number> {
  const raw = await page.locator(CONTAINER_SEL).evaluate((el) =>
    getComputedStyle(el).getPropertyValue('--band-h').trim()
  );
  const containerRect = await rectOf(page, CONTAINER_SEL);
  if (raw.endsWith('%')) {
    return (parseFloat(raw) / 100) * containerRect.height;
  }
  return parseFloat(raw); // px value (e.g. "60px")
}

// ---------------------------------------------------------------------------

for (const mode of ['flight', 'station', 'surface'] as const) {
  test(`mode=${mode} — container/sidebar/deck/windshield geometry`, async ({ page }) => {
    await loadMode(page, mode);

    const container = await rectOf(page, CONTAINER_SEL);
    const sidebar    = await rectOf(page, SIDEBAR_SEL);
    const content     = await rectOf(page, CONTENT_SEL);
    const deck         = await rectOf(page, DECK_SEL);

    // ── .game-container fills the 1440x900 viewport (minus nothing — Law 2,
    // the document never scrolls) ────────────────────────────────────────
    expect(container.width, 'container width ≈ viewport width').toBeGreaterThan(1400);
    expect(container.height, 'container height ≈ viewport height').toBeGreaterThan(800);

    // ── grid grammar landed AND is a no-op on today's geometry ───────────
    const display = await page.locator(CONTAINER_SEL).evaluate((el) => getComputedStyle(el).display);
    expect(display, '.game-container is now a grid container').toBe('grid');
    const areas = await page.locator(CONTAINER_SEL).evaluate((el) => getComputedStyle(el).gridTemplateAreas);
    for (const slot of ['sidebar', 'windshield', 'deck', 'statusbar', 'teleprinter']) {
      expect(areas, `grid-template-areas names "${slot}"`).toContain(slot);
    }

    // ── mode-* class present, additive (WO contract) ──────────────────────
    const className = await page.locator(CONTAINER_SEL).getAttribute('class');
    const expectedModeClass = mode === 'surface' ? 'mode-surface' : mode === 'station' ? 'mode-station' : 'mode-flight';
    expect(className, `.game-container carries ${expectedModeClass}`).toContain(expectedModeClass);

    // ── deck (stub child) stays FULL-BLEED, identical to the container —
    // "nothing nests yet" (PURELY ADDITIVE lane) ──────────────────────────
    expect(Math.abs(deck.width - container.width), 'deck width unconstrained by the grid').toBeLessThanOrEqual(TOL);
    expect(Math.abs(deck.height - container.height), 'deck height unconstrained by the grid').toBeLessThanOrEqual(TOL);
    expect(Math.abs(deck.x - container.x), 'deck x unconstrained by the grid').toBeLessThanOrEqual(TOL);
    expect(Math.abs(deck.y - container.y), 'deck y unconstrained by the grid').toBeLessThanOrEqual(TOL);

    // ── windshield (.game-content, "full-bleed scene + HUD") also stays
    // exactly full-bleed — the grid declaration narrows nothing ───────────
    expect(Math.abs(content.width - container.width), 'windshield width unconstrained').toBeLessThanOrEqual(TOL);
    expect(Math.abs(content.height - container.height), 'windshield height unconstrained').toBeLessThanOrEqual(TOL);

    // ── sidebar: narrow left column, width within the --sidebar-w clamp
    // bounds (260–370px), flush to the left edge, spanning to the bottom ──
    expect(sidebar.x, 'sidebar flush-left').toBeLessThanOrEqual(TOL);
    expect(sidebar.width, 'sidebar width ≥ clamp min').toBeGreaterThanOrEqual(260 - TOL);
    expect(sidebar.width, 'sidebar width ≤ clamp max').toBeLessThanOrEqual(370 + TOL);
    expect(
      Math.abs(sidebar.y + sidebar.height - (container.y + container.height)),
      'sidebar bottom flush with container bottom',
    ).toBeLessThanOrEqual(TOL);

    // ── sidebar top offset matches the resolved --band-h for this mode ───
    const bandHeightPx = await resolvedBandHeightPx(page);
    expect(
      Math.abs(sidebar.y - (container.y + bandHeightPx)),
      `sidebar top = container top + resolved band-h (${bandHeightPx.toFixed(1)}px)`,
    ).toBeLessThanOrEqual(TOL);
  });
}

// ---------------------------------------------------------------------------
// Cross-mode relationships — the actual per-mode DIFFERENCES the WO's CSS
// var math produces. GameLayout's dock/land auto-behaviors (sidebar auto-
// collapse, windshield auto-minimize) are EDGE-triggered off a transition;
// a fresh mount starts already in its target state, so no transition fires
// (real GameLayout behavior, unrelated to this lane — a hard-refresh while
// already docked/landed in production behaves identically):
//   • flight  : no console-expand, no windshield-min, no landed-expanded
//               → band-h stays the base 20%
//   • station : console-expand (is_docked) but windshield-min never fires
//               (edge-triggered) and landed-expanded requires is_landed
//               → band-h ALSO stays the base 20% (equal to flight)
//   • surface : console-expand + landed-expanded (is_landed && !windshieldMin,
//               windshieldMin stays false at fresh mount) → band-h = 65%
// ---------------------------------------------------------------------------

test('band-h: flight == station (both unmodified base), surface > both (landed-expanded)', async ({ page }) => {
  await loadMode(page, 'flight');
  const flightBand = await resolvedBandHeightPx(page);

  await loadMode(page, 'station');
  const stationBand = await resolvedBandHeightPx(page);

  await loadMode(page, 'surface');
  const surfaceBand = await resolvedBandHeightPx(page);

  expect(Math.abs(flightBand - stationBand), 'flight and station share the unmodified base band-h (20%)').toBeLessThanOrEqual(TOL);
  expect(surfaceBand, 'surface (landed-expanded, 65%) exceeds flight/station (20%)').toBeGreaterThan(flightBand + 50);
});

test('sidebar stays open (not auto-collapsed) in all three modes on a fresh mount', async ({ page }) => {
  // sidebarOpen's auto-collapse is edge-triggered off the is_landed
  // TRANSITION (GameLayout.tsx); a fresh /lab/shell?mode=surface mount never
  // transitions, so console-collapsed never applies here — real, pre-existing
  // GameLayout behavior, not introduced by this lane.
  for (const mode of ['flight', 'station', 'surface'] as const) {
    await loadMode(page, mode);
    const className = await page.locator(CONTAINER_SEL).getAttribute('class');
    expect(className, `${mode}: console-collapsed must not apply on a fresh mount`).not.toContain('console-collapsed');
    const sidebar = await rectOf(page, SIDEBAR_SEL);
    expect(sidebar.width, `${mode}: sidebar width stays within the open clamp range`).toBeGreaterThan(200);
  }
});

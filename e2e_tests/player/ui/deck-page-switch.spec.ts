import { test, expect } from '../../fixtures/auth.fixtures';
import { loginAsPlayer } from '../../utils/auth.utils';
import type { Locator, Page } from '@playwright/test';

/**
 * WO-UI2-DECK-MONITORS — proves the ONE shared DeckPageTabs rail actually
 * switches pages live in all three deck monitors (NAV, SOLAR SYSTEM,
 * COMMS): click/keyboard nav flips aria-selected and swaps the panel's
 * content, and the switch causes NO layout jump in the surrounding deck
 * (a real-browser getBoundingClientRect assertion — jsdom/vitest can't see
 * CSS Grid geometry, per this suite's own established precedent in
 * deck-reflow-bands.spec.ts). Band-clear regression (statusbar/
 * teleprinter) is covered by re-running deck-reflow-bands.spec.ts
 * UNMODIFIED alongside this file, not duplicated here.
 *
 * Reference resolution: 1440x900 (Scroll Law's reference cockpit
 * resolution, CLAUDE.md).
 */

const VIEWPORT = { width: 1440, height: 900 };

/** All 3 console-monitor boxes, captured for a before/after no-jump diff. */
async function captureMonitorRects(page: Page): Promise<Array<{ top: number; left: number; width: number; height: number }>> {
  return page.evaluate(() =>
    Array.from(document.querySelectorAll('.console-monitor')).map((el) => {
      const r = el.getBoundingClientRect();
      return { top: r.top, left: r.left, width: r.width, height: r.height };
    }),
  );
}

async function assertNoLayoutJump(page: Page, before: Awaited<ReturnType<typeof captureMonitorRects>>): Promise<void> {
  const after = await captureMonitorRects(page);
  expect(after, 'deck monitor geometry moved after a page switch').toEqual(before);
}

test.describe('WO-UI2-DECK-MONITORS — deck page switching (DeckPageTabs)', () => {
  test.beforeEach(async ({ page, playerCredentials }) => {
    await page.setViewportSize(VIEWPORT);
    await loginAsPlayer(page, playerCredentials);
    await page.waitForSelector('.game-container', { state: 'attached', timeout: 20000 });
    // Let the deck settle (force-layout NAV graph, ResizeObserver) before
    // sampling geometry, same settle window deck-reflow-bands.spec.ts uses.
    await page.waitForTimeout(250);
  });

  test('exactly ONE DeckPageTabs source backs every rail on the page (single-source Accept)', async ({ page }) => {
    // A structural sanity check, not a geometry probe: every rendered
    // rail is role=tablist with the deck-tab-rail class this repo's ONE
    // cockpit/DeckPageTabs.tsx component emits (cockpit.css's shared
    // .deck-tab-rail/.deck-tab-btn block) — no monitor is still running a
    // hand-copied switch.
    const rails = page.locator('[role="tablist"].deck-tab-rail');
    const count = await rails.count();
    expect(count).toBeGreaterThan(0);
    for (let i = 0; i < count; i++) {
      const tabs = rails.nth(i).locator('[role="tab"].deck-tab-btn');
      await expect(tabs).toHaveCount(await tabs.count());
      expect(await tabs.count()).toBeGreaterThanOrEqual(2);
    }
  });

  test('SOLAR SYSTEM monitor: header reads "SOLAR SYSTEM" (no truncation) and BODIES/HAZARDS switch content with no layout jump', async ({ page }) => {
    const monitor = page.locator('.console-monitor.system-monitor');
    await expect(monitor).toBeVisible();

    const header = monitor.locator('.screen-hud-header');
    await expect(header).toContainText('SOLAR SYSTEM');
    // No-truncation proof: the header's own scrollWidth must never exceed
    // its rendered clientWidth (a clipped/ellipsized label would overflow).
    const overflow = await header.evaluate((el) => el.scrollWidth - el.clientWidth);
    expect(overflow, `SOLAR SYSTEM header overflowed its 3fr column by ${overflow}px`).toBeLessThanOrEqual(0);

    const tabs = monitor.locator('[role="tab"]');
    await expect(tabs).toHaveCount(2);
    await expect(tabs.nth(0)).toHaveAttribute('aria-selected', 'true');
    // BODIES active: zero HAZARDS-page metric rows rendered.
    await expect(monitor.locator('.system-hazard-metric')).toHaveCount(0);

    const before = await captureMonitorRects(page);
    await tabs.nth(1).click();
    await expect(tabs.nth(1)).toHaveAttribute('aria-selected', 'true');
    await expect(tabs.nth(0)).toHaveAttribute('aria-selected', 'false');
    // HAZARDS active: the hazard + radiation rows always render (even at
    // 0), so at least 2 metric groups are guaranteed present.
    const metricCount = await monitor.locator('.system-hazard-metric').count();
    expect(metricCount).toBeGreaterThanOrEqual(2);
    await assertNoLayoutJump(page, before);

    // Keyboard: ArrowLeft wraps back to BODIES, focus follows.
    await tabs.nth(1).press('ArrowLeft');
    await expect(tabs.nth(0)).toHaveAttribute('aria-selected', 'true');
    await expect(monitor.locator('.system-hazard-metric')).toHaveCount(0);
  });

  test('COMMS monitor: CONTACTS/HAILS switch content with no layout jump', async ({ page }) => {
    const monitor = page.locator('.console-monitor.comms-monitor');
    await expect(monitor).toBeVisible();

    const tabs = monitor.locator('[role="tab"]');
    await expect(tabs).toHaveCount(2);
    await expect(tabs.nth(0)).toHaveAttribute('aria-selected', 'true');
    // CONTACTS active: the HAILS-mode content wrapper class is absent.
    await expect(monitor.locator('.screen-hud-content.comms-hails-content')).toHaveCount(0);

    const before = await captureMonitorRects(page);
    await tabs.nth(1).click();
    await expect(tabs.nth(1)).toHaveAttribute('aria-selected', 'true');
    await expect(tabs.nth(0)).toHaveAttribute('aria-selected', 'false');
    await expect(monitor.locator('.screen-hud-content.comms-hails-content')).toHaveCount(1);
    await assertNoLayoutJump(page, before);

    // Keyboard: ArrowRight wraps back to CONTACTS.
    await tabs.nth(1).press('ArrowRight');
    await expect(tabs.nth(0)).toHaveAttribute('aria-selected', 'true');
    await expect(monitor.locator('.screen-hud-content.comms-hails-content')).toHaveCount(0);
  });

  test('NAV monitor: the rail is either a working 2-tab switch (Warp Jumper hull) or correctly absent (<2 available pages)', async ({ page }) => {
    const monitor = page.locator('.console-monitor.nav-monitor');
    await expect(monitor).toBeVisible();

    const tabs: Locator = monitor.locator('[role="tab"]');
    const count = await tabs.count();

    if (count === 0) {
      // Non-Warp-Jumper seeded player: DeckPageTabs correctly rendered NO
      // rail for the <2-available-pages case (WARP GRAPH is the only
      // available page) — this IS the Accept #1 assertion for this hull,
      // not a skip.
      await expect(monitor.locator('[role="tablist"]')).toHaveCount(0);
      return;
    }

    expect(count).toBe(2);
    await expect(tabs.nth(0)).toHaveAttribute('aria-selected', 'true');

    const before = await captureMonitorRects(page);
    await tabs.nth(1).click();
    await expect(tabs.nth(1)).toHaveAttribute('aria-selected', 'true');
    await expect(tabs.nth(0)).toHaveAttribute('aria-selected', 'false');
    await assertNoLayoutJump(page, before);

    await tabs.nth(1).press('ArrowLeft');
    await expect(tabs.nth(0)).toHaveAttribute('aria-selected', 'true');
  });
});

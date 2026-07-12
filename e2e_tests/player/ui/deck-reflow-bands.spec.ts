import { test, expect } from '../../fixtures/auth.fixtures';
import { loginAsPlayer } from '../../utils/auth.utils';
import type { Page } from '@playwright/test';

/**
 * WO-UI1-DECK-REFLOW — proves the deck (and the sidebar MFD screens) stop
 * ABOVE the statusbar/teleprinter grid rows instead of extending underneath
 * them. jsdom (the vitest suite) cannot see this: it never lays out CSS
 * Grid `auto` row tracks or `position: absolute` against a real box, so
 * this is a real-browser geometry assertion, run against the dev host over
 * Tailscale per e2e_tests/README.md.
 *
 * Reference resolution is 1440x900 (Scroll Law's reference cockpit
 * resolution, CLAUDE.md). Band coordinates match the WO's own verify-first
 * evidence: status-bar band y696-752 (56px row, ends at the 900px viewport
 * bottom minus wherever the teleprinter row currently sits above it),
 * teleprinter band y768-900.
 */

const VIEWPORT = { width: 1440, height: 900 };

/** Deck/scene classes that must NEVER appear in the statusbar/teleprinter
 *  bands once WO-UI1-DECK-REFLOW's `.game-content`/`.game-sidebar` bottom
 *  reservation (game-layout.css) is in effect. Matches the WO's own
 *  verify-first evidence (a NAV `circle.node-circle`, a
 *  `svg.navigation-map-svg`) plus the containing chrome those live inside. */
const DECK_LEAKAGE_SELECTORS = [
  '.console-monitor',
  '.monitor-screen',
  '.cockpit-console',
  '.game-sidebar',
  '.node-circle',
  '.navigation-map-svg',
  '.game-content',
] as const;

/** Samples elementsFromPoint across the FULL deck width (right of the
 *  sidebar, x>465 per the WO's own probe — the sidebar's own occlusion is
 *  the same defect class but out of this WO's falsifiable DoD) at several
 *  x offsets and every y in the given band, asserting none of them belong
 *  to a deck/scene element. */
async function assertBandClear(
  page: Page,
  band: { yStart: number; yEnd: number; yStep: number; xs: number[] },
): Promise<void> {
  const leaks = await page.evaluate(
    ({ band: b, selectors }) => {
      const hits: Array<{ x: number; y: number; selector: string; tag: string; cls: string }> = [];
      for (let y = b.yStart; y <= b.yEnd; y += b.yStep) {
        for (const x of b.xs) {
          const stack = document.elementsFromPoint(x, y);
          for (const el of stack) {
            for (const sel of selectors) {
              if (el.matches(sel)) {
                hits.push({ x, y, selector: sel, tag: el.tagName, cls: (el as HTMLElement).className?.toString?.() ?? '' });
              }
            }
          }
        }
      }
      return hits;
    },
    { band, selectors: DECK_LEAKAGE_SELECTORS },
  );

  expect(leaks, `deck/scene element(s) found under the band: ${JSON.stringify(leaks, null, 2)}`).toEqual([]);
}

test.describe('WO-UI1-DECK-REFLOW — deck stops above the statusbar/teleprinter bands', () => {
  test.beforeEach(async ({ page, playerCredentials }) => {
    await page.setViewportSize(VIEWPORT);
    await loginAsPlayer(page, playerCredentials);
    await page.waitForSelector('.game-container', { state: 'attached', timeout: 20000 });
    // Let the teleprinter's own layout (and GameLayout's ResizeObserver
    // measurement of it) settle before sampling geometry.
    await page.waitForTimeout(250);
  });

  test('space (flight) view: status-bar band is deck-clear @1440x900', async ({ page }) => {
    // x>465 keeps the probe inside the deck/scene column (right of
    // --sidebar-w, which maxes at 370px) — the WO's own verify-first probe
    // coordinates.
    await assertBandClear(page, { yStart: 696, yEnd: 752, yStep: 8, xs: [500, 700, 900, 1100, 1300] });
  });

  test('space (flight) view: teleprinter band is deck-clear @1440x900', async ({ page }) => {
    await assertBandClear(page, { yStart: 768, yEnd: 899, yStep: 8, xs: [500, 700, 900, 1100, 1300] });
  });

  test('docked (.console-expand) view: both bands stay deck-clear @1440x900', async ({ page }) => {
    // Forcing a live dock (station approach + dock action) needs seeded,
    // reachable station state this suite doesn't own; .console-expand is a
    // pure CSS-variant class GameLayout applies from playerState.is_docked
    // (GameLayout.tsx) with NO other markup change, so toggling it directly
    // is a faithful probe of the CSS variant itself -- exactly what DoD
    // item 4 ("holds in ... docked views") is asking this test to prove.
    await page.evaluate(() => {
      document.querySelector('.game-container')?.classList.add('console-expand');
    });
    await page.waitForTimeout(250);

    await assertBandClear(page, { yStart: 696, yEnd: 752, yStep: 8, xs: [500, 700, 900, 1100, 1300] });
    await assertBandClear(page, { yStart: 768, yEnd: 899, yStep: 8, xs: [500, 700, 900, 1100, 1300] });
  });

  test('zero document overflow — the shell never grows past the viewport', async ({ page }) => {
    const { docHeight, viewportHeight } = await page.evaluate(() => ({
      docHeight: document.documentElement.scrollHeight,
      viewportHeight: window.innerHeight,
    }));
    expect(docHeight).toBe(viewportHeight);
  });

  test('each deck monitor is independently scrollable, not overflow-visible', async ({ page }) => {
    const overflows = await page.evaluate(() => {
      const monitors = Array.from(document.querySelectorAll('.monitor-screen'));
      return monitors.map((el) => {
        const style = window.getComputedStyle(el);
        const content = el.querySelector('.screen-hud-content, .trading-content, .planetary-ops-content, .comms-inbox-list, .contacts-compact-list');
        const contentStyle = content ? window.getComputedStyle(content) : null;
        return {
          monitorOverflow: style.overflow,
          contentOverflowY: contentStyle?.overflowY ?? null,
        };
      });
    });

    expect(overflows.length).toBeGreaterThan(0);
    for (const m of overflows) {
      // The outer monitor clips (never spills into a sibling monitor or the
      // reserved bands); the inner content region is the one that scrolls.
      expect(m.monitorOverflow).toBe('hidden');
      expect(['auto', 'scroll']).toContain(m.contentOverflowY);
    }
  });
});

import { test, expect } from '../../fixtures/auth.fixtures';
import { loginAsPlayer } from '../../utils/auth.utils';
import type { Page } from '@playwright/test';

/**
 * WO-UI0-SCALE-LAW — locks THREE invariants together, at every supported
 * `--ui-scale` and viewport combination, not just the 1440x900 reference:
 *   1. The already-shipped scale mechanism (`#root { zoom: var(--ui-scale) }`,
 *      index.css) never produces a scrolling document (Scroll Law / Law 2).
 *   2. The statusbar row pin (game-layout.css: `grid-template-rows: ...
 *      var(--statusbar-h) auto` — was `... auto auto`) holds the statusbar
 *      row at its real 56px, not an inflated `overflow:visible`-driven
 *      auto-track height (see game-layout.css's doc-comment above
 *      grid-template-rows for the root cause).
 *   3. WO-UI1-DECK-REFLOW's deck/sidebar reservation (`.game-content`/
 *      `.game-sidebar` bottom edge = `--statusbar-h + --teleprinter-h`,
 *      deck-reflow-bands.spec.ts) stays deck-clear as scale/viewport change
 *      the pixel geometry those CSS vars resolve against.
 *
 * Unlike deck-reflow-bands.spec.ts (hardcoded 1440x900 band coordinates,
 * intentionally — it locks ONE known-good geometry), this suite computes
 * each band's bounding box LIVE per cell via `getBoundingClientRect()` so
 * the same assertion is portable across the whole scale x viewport matrix.
 * Scale/docked state are poked directly via the CSS custom property /
 * `.console-expand` class GameLayout itself reads (rather than driving the
 * live Settings UI slider or a live docking flow) — same technique
 * deck-reflow-bands.spec.ts already uses for `.console-expand`; the CSS
 * geometry under test doesn't care HOW `--ui-scale` got set, only that it's
 * set, and this repo has no dev-host route to force a `--ui-scale` value
 * from the URL.
 */

const SCALES = [0.6, 1.0, 1.2] as const;

const VIEWPORTS = [
  { width: 1440, height: 900, label: '1440x900-reference' },
  { width: 1024, height: 768, label: '1024x768-mid' },
  { width: 900, height: 560, label: '900x560-floor' },
] as const;

const MODES = ['space', 'docked'] as const;

/** Same deck/scene leakage set deck-reflow-bands.spec.ts locks — a
 *  console/monitor/scene element must never be reachable (via
 *  `elementsFromPoint`) inside the statusbar/teleprinter bands. */
const DECK_LEAKAGE_SELECTORS = [
  '.console-monitor',
  '.monitor-screen',
  '.cockpit-console',
  '.game-sidebar',
  '.node-circle',
  '.navigation-map-svg',
  '.game-content',
] as const;

interface BandCheckResult {
  found: boolean;
  rect: { top: number; bottom: number; left: number; right: number } | null;
  leaks: Array<{ x: number; y: number; selector: string }>;
}

/**
 * Live-measures the given band element's own box, then samples
 * `elementsFromPoint` across its full rendered width/height (a handful of
 * interior x/y offsets — enough density to catch a leak without being
 * viewport-size-specific) and returns any deck/scene hits found within it.
 */
async function checkBandClear(page: Page, bandSelector: string): Promise<BandCheckResult> {
  return page.evaluate(
    ({ bandSelector: sel, selectors }) => {
      const el = document.querySelector(sel);
      if (!el) return { found: false, rect: null, leaks: [] };
      const rect = el.getBoundingClientRect();
      const leaks: Array<{ x: number; y: number; selector: string }> = [];
      const xStep = Math.max(8, Math.floor(rect.width / 6));
      const yStep = Math.max(6, Math.floor(rect.height / 4));
      for (let y = Math.ceil(rect.top) + 2; y <= Math.floor(rect.bottom) - 2; y += yStep) {
        for (let x = Math.ceil(rect.left) + 4; x <= Math.floor(rect.right) - 4; x += xStep) {
          const stack = document.elementsFromPoint(x, y);
          for (const hit of stack) {
            for (const s of selectors) {
              if (hit.matches(s)) leaks.push({ x, y, selector: s });
            }
          }
        }
      }
      return {
        found: true,
        rect: { top: rect.top, bottom: rect.bottom, left: rect.left, right: rect.right },
        leaks,
      };
    },
    { bandSelector, selectors: DECK_LEAKAGE_SELECTORS },
  );
}

for (const scale of SCALES) {
  for (const viewport of VIEWPORTS) {
    for (const mode of MODES) {
      test.describe(`ui-scale ${scale} @ ${viewport.label} — ${mode}`, () => {
        test.beforeEach(async ({ page, playerCredentials }) => {
          await page.setViewportSize({ width: viewport.width, height: viewport.height });
          await loginAsPlayer(page, playerCredentials);
          await page.waitForSelector('.game-container', { state: 'attached', timeout: 20000 });

          // Poke `--ui-scale` directly — the exact custom property
          // SettingsContext's useLayoutEffect writes to document.documentElement
          // (index.css: `#root { zoom: var(--ui-scale) }`). Bypasses the
          // Settings UI/localStorage round trip entirely so this suite tests
          // the CSS mechanism, not the settings-persistence flow (that's
          // SettingsContext's own concern, covered elsewhere).
          await page.evaluate((s) => {
            document.documentElement.style.setProperty('--ui-scale', String(s));
          }, scale);

          if (mode === 'docked') {
            // Matches deck-reflow-bands.spec.ts's own technique: `.console-expand`
            // is a pure CSS-variant class GameLayout applies from playerState
            // (is_docked || is_landed) with no other markup change, so setting
            // it directly is a faithful probe of the CSS variant.
            await page.evaluate(() => {
              document.querySelector('.game-container')?.classList.add('console-expand');
            });
          }

          // Let the zoom reflow + GameLayout's ResizeObserver (--teleprinter-h)
          // settle before sampling geometry.
          await page.waitForTimeout(300);
        });

        test('document never scrolls', async ({ page }) => {
          const overflow = await page.evaluate(() => {
            const html = document.documentElement;
            return {
              scrollHeight: html.scrollHeight,
              clientHeight: html.clientHeight,
              scrollWidth: html.scrollWidth,
              clientWidth: html.clientWidth,
            };
          });
          expect(overflow.scrollHeight, JSON.stringify(overflow)).toBe(overflow.clientHeight);
          expect(overflow.scrollWidth, JSON.stringify(overflow)).toBe(overflow.clientWidth);
        });

        test('statusbar band is deck-clear', async ({ page }) => {
          const result = await checkBandClear(page, '.status-bar');
          expect(result.found, 'no .status-bar found').toBe(true);
          expect(result.leaks, `deck/scene leak(s) in statusbar band: ${JSON.stringify(result.leaks, null, 2)}`).toEqual([]);
        });

        test('teleprinter band is deck-clear', async ({ page }) => {
          const result = await checkBandClear(page, '.teleprinter');
          expect(result.found, 'no .teleprinter found').toBe(true);
          expect(result.leaks, `deck/scene leak(s) in teleprinter band: ${JSON.stringify(result.leaks, null, 2)}`).toEqual([]);
        });

        test('statusbar row renders at its declared height, not an inflated auto-track', async ({ page }) => {
          // WO-UI0-SCALE-LAW: pins grid-template-rows' 3rd track to
          // var(--statusbar-h) instead of `auto`, so this must hold at every
          // scale/viewport, not just the 1440x900 reference the pin was
          // measured against.
          const heights = await page.evaluate(() => {
            const statusBar = document.querySelector('.status-bar');
            const styles = statusBar ? getComputedStyle(statusBar) : null;
            return {
              boxHeight: statusBar?.getBoundingClientRect().height ?? null,
              declaredHeight: styles ? parseFloat(styles.height) : null,
              declaredMinHeight: styles ? parseFloat(styles.minHeight) : null,
            };
          });
          expect(heights.boxHeight).not.toBeNull();
          // Rendered box height must equal the declared min-height (56px,
          // statusbar.css) at every scale — `zoom` uniformly scales CSS
          // pixels, so the RAW getBoundingClientRect() value legitimately
          // moves with scale (e.g. ~33.6px at 0.6x); comparing box height to
          // the SAME element's own declared/min-height (which zoom also
          // scales identically) keeps this assertion scale-independent.
          expect(heights.boxHeight).toBeCloseTo(heights.declaredMinHeight!, 1);
        });
      });
    }
  }
}

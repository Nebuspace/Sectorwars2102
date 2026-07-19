import { defineConfig, devices } from '@playwright/test';

/**
 * Vista Proof Playwright configuration — SMOKE GROUP (2 navigations).
 *
 * Separate from playwright.config.ts (which targets the Docker stack on port 3000).
 * This config starts a LOCAL Vite dev server on port 5174 so that:
 *   • import.meta.env.DEV === true (the /lab/vista-proof route is live)
 *   • No Docker dependency — runs entirely on the Mac
 *   • No auth required — the proof page has no API calls
 *
 * This config is the SMOKE group: named-storm regression anchor + viewport stability.
 * It intentionally covers only 2 navigations so it runs fast (~30 s) and reliably.
 *
 * FULL SUITE — run each group separately to stay under the ~12-nav Vite exhaustion
 * threshold.  From services/player-client/:
 *
 *   Group smoke (2 navs)  — named-storm + lab-viewport (this config):
 *     npx playwright test -c playwright.vista-proof.config.ts
 *
 *   Group A (7 navs)  — named-storm + lab-viewport + V3-celestial types:
 *     npx playwright test -c playwright.vista-proof.group-a.config.ts
 *
 *   Group B (9 navs)  — 9 primary planet types:
 *     npx playwright test -c playwright.vista-proof.group-b.config.ts
 *
 *   Group C (10 navs) — Wave-3 day + night captures:
 *     npx playwright test -c playwright.vista-proof.group-c.config.ts
 *
 *   Group D (10 navs) — slider min/max pair captures (DRIVEN proofs):
 *     npx playwright test -c playwright.vista-proof.group-d.config.ts
 *
 * The webServer.reuseExistingServer=true means you can pre-start vite
 * ("npx vite --port 5174") to skip the startup wait on repeated runs.
 *
 * If Chromium is not installed:
 *   npx playwright install chromium
 */
export default defineConfig({
  testDir:       './playwright/vista-proof',
  // Smoke group: only the named-storm anchor and the viewport stability spec.
  // Without testMatch, testDir auto-discovery would pick up all spec files and
  // exceed the ~12-nav Vite dev-server exhaustion threshold.
  testMatch:     ['vista-named-storm-proof.spec.ts', 'lab-viewport.spec.ts'],
  fullyParallel: false,
  forbidOnly:    !!process.env.CI,
  retries:       0,
  workers:       1,
  reporter:      'list',
  use: {
    baseURL:    'http://localhost:5174',
    trace:      'off',
    screenshot: 'off',  // the spec drives its own explicit screenshots
    video:      'off',
    // Headless Chromium, DPR=1 — yields deterministic canvas pixel output
  },
  projects: [
    {
      name: 'chromium',
      use: { ...devices['Desktop Chrome'] },
    },
  ],
  webServer: {
    command:             'npx vite --port 5174',
    url:                 'http://localhost:5174',
    reuseExistingServer: true,
    stdout:              'pipe',
    stderr:              'pipe',
    timeout:             30_000,
  },
});

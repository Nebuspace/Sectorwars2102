import { defineConfig, devices } from '@playwright/test';

/**
 * Vista Proof Playwright configuration.
 *
 * Separate from playwright.config.ts (which targets the Docker stack on port 3000).
 * This config starts a LOCAL Vite dev server on port 5174 so that:
 *   • import.meta.env.DEV === true (the /lab/vista-proof route is live)
 *   • No Docker dependency — runs entirely on the Mac
 *   • No auth required — the proof page has no API calls
 *
 * Usage (from services/player-client/):
 *   npx playwright test -c playwright.vista-proof.config.ts
 *
 * The webServer.reuseExistingServer=true means you can pre-start vite
 * ("npx vite --port 5174") to skip the startup wait on repeated runs.
 *
 * If Chromium is not installed:
 *   npx playwright install chromium
 */
export default defineConfig({
  testDir:       './playwright/vista-proof',
  fullyParallel: false,   // single-spec, determinism check requires ordered captures
  forbidOnly:    !!process.env.CI,
  retries:       0,
  workers:       1,       // single worker to keep the two captures sequential
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

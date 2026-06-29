/**
 * Vista Proof — W7 parity capture config (12 navigations).
 *
 * Run from services/player-client/:
 *   npx playwright test -c playwright.vista-proof.w7parity.config.ts
 *
 * Captures OLD (drawLandedScene) vs ENGINE (VistaCanvas) side-by-side PNGs
 * for all 12 landed-capable planet types into playwright/artifacts/w7-parity-*.png.
 *
 * Reuses an existing vite dev server on :5174 if one is running.
 * macOS has no `timeout` command — playwright's --timeout flag controls test
 * timeout; vite dev server management is handled by the webServer block.
 */
import { defineConfig, devices } from '@playwright/test';

export default defineConfig({
  testDir:       './playwright/vista-proof',
  testMatch:     ['w7-parity-captures.spec.ts'],
  fullyParallel: false,
  forbidOnly:    !!process.env.CI,
  retries:       0,
  workers:       1,
  reporter:      'list',
  use: {
    baseURL:    'http://localhost:5174',
    trace:      'off',
    screenshot: 'off',
    video:      'off',
  },
  projects: [
    { name: 'chromium', use: { ...devices['Desktop Chrome'] } },
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

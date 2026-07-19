/**
 * ARTIFICIAL flora proof — one-shot config.
 * Captures an ARTIFICIAL (engineered station) scene to verify hydroponic-tray /
 * engineered-plant render as structured drawScatterPlanter visuals, not green blobs.
 *
 * Run from services/player-client/:
 *   npx playwright test -c playwright.artificial-proof.config.ts
 */
import { defineConfig, devices } from '@playwright/test';

export default defineConfig({
  testDir:       './playwright/vista-proof',
  testMatch:     ['artificial-planters.spec.ts'],
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

/**
 * Vista Proof — Group D config (10 navigations).
 *
 * Covers: slider min/max pair captures for the 5 DRIVEN living-detail sliders
 *         (waterCoverage, temperature, nativeLife, atmDensity, habitability).
 *
 * Each test navigates twice (MIN=0.05, MAX=0.95) per slider → 5 × 2 = 10 navs.
 * Output: playwright/artifacts/slider-<name>-{min,max}.png  (10 files)
 *
 * Run from services/player-client/:
 *   npx playwright test -c playwright.vista-proof.group-d.config.ts
 *
 * See playwright.vista-proof.config.ts for the full group runbook.
 */
import { defineConfig, devices } from '@playwright/test';

export default defineConfig({
  testDir:   './playwright/vista-proof',
  testMatch: ['slider-pairs.spec.ts'],
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

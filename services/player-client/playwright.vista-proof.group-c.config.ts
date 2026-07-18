/**
 * Vista Proof — Group C config (10 navigations).
 *
 * Covers: Wave-3 FX captures — 7 day types + 3 night special cases.
 *
 * Day  (t=0): TERRAN · JUNGLE · TROPICAL · OCEANIC · VOLCANIC · ICE · DESERT
 * Night (3am): OCEANIC · BLACK_HOLE · RING_ARC
 *
 * Run from services/player-client/:
 *   npx playwright test -c playwright.vista-proof.group-c.config.ts
 *
 * See playwright.vista-proof.config.ts for the full group runbook.
 */
import { defineConfig, devices } from '@playwright/test';

export default defineConfig({
  testDir:   './playwright/vista-proof',
  testMatch: ['wave3-captures.spec.ts'],
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

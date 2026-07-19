/**
 * Vista Proof — Group B config (9 navigations).
 *
 * Covers: 9 primary planet types
 *         (TERRAN · JUNGLE · TROPICAL · MOUNTAINOUS · ICE · VOLCANIC · OCEANIC · BARREN · DESERT).
 *
 * Set VISTA_RUN_LABEL=before to write *-before.png captures for comparison:
 *   VISTA_RUN_LABEL=before npx playwright test -c playwright.vista-proof.group-b.config.ts
 *
 * Run from services/player-client/:
 *   npx playwright test -c playwright.vista-proof.group-b.config.ts
 *
 * See playwright.vista-proof.config.ts for the full group runbook.
 */
import { defineConfig, devices } from '@playwright/test';

export default defineConfig({
  testDir:   './playwright/vista-proof',
  testMatch: ['vista-types-primary.spec.ts'],
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

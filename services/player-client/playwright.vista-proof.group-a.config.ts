/**
 * Vista Proof — Group A config (7 navigations).
 *
 * Covers: named-storm regression anchor (1) + lab-viewport stability (1)
 *         + V3-CELESTIAL draw-path proofs (5).
 *
 * Run from services/player-client/:
 *   npx playwright test -c playwright.vista-proof.group-a.config.ts
 *
 * See playwright.vista-proof.config.ts for the full group runbook.
 */
import { defineConfig, devices } from '@playwright/test';

export default defineConfig({
  testDir:   './playwright/vista-proof',
  testMatch: [
    'vista-named-storm-proof.spec.ts',
    'lab-viewport.spec.ts',
    'vista-types-celestial.spec.ts',
  ],
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

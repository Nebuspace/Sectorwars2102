/**
 * Vista Proof — Wave-5 capture config (7 navigations).
 *
 * Run from services/player-client/:
 *   npx playwright test -c playwright.vista-proof.wave5.config.ts
 *
 * Captures the 4 Wave-5 per-type signatures (OCEANIC / TROPICAL / MOUNTAINOUS /
 * ARCTIC) day + night into playwright/artifacts/w5-*.png. Reuses an existing
 * vite dev server on :5174 if one is running.
 */
import { defineConfig, devices } from '@playwright/test';

export default defineConfig({
  testDir:   './playwright/vista-proof',
  testMatch: ['wave5-captures.spec.ts'],
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

/**
 * Vista Proof — Wave-6 capture config (5 navigations).
 *
 * Run from services/player-client/:
 *   npx playwright test -c playwright.vista-proof.wave6.config.ts
 *
 * Captures the 3 Wave-6 signatures (ARTIFICIAL / GAS_GIANT / BARREN) into
 * playwright/artifacts/w6-*.png. Reuses an existing vite dev server on :5174.
 */
import { defineConfig, devices } from '@playwright/test';

export default defineConfig({
  testDir:   './playwright/vista-proof',
  testMatch: ['wave6-captures.spec.ts'],
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

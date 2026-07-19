/**
 * FLORA-RENDER density proof — one-shot config.
 * Captures a lush JUNGLE frame to verify flora reads as a dense forest.
 *
 * Run from services/player-client/:
 *   npx playwright test -c playwright.flora-proof.config.ts
 */
import { defineConfig, devices } from '@playwright/test';

export default defineConfig({
  testDir:       './playwright/vista-proof',
  testMatch:     ['lush-jungle-flora.spec.ts'],
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

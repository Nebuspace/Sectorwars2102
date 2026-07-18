import { defineConfig, devices } from '@playwright/test';

/**
 * Vista TK-2 Emissive Light Source — pixel proof driver.
 *
 * Headless (default) is correct here: this is a PIXEL comparison, not a
 * timing measurement — software vs hardware rasterization does not change
 * canvas pixel OUTPUT (see playwright.vista-perf.config.ts's SwiftShader
 * note), only speed. No headless:false override needed.
 */
export default defineConfig({
  testDir:       './playwright/vista-perf',
  testMatch:     ['vista-tk2-emissive.spec.ts'],
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

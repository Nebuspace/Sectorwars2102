import { defineConfig, devices } from '@playwright/test';

/**
 * PERMANENT regression config — real-Chromium guard for the Skia Canvas2D /
 * GLSL float32 mount-relative-clock bug class (see TableauFreezeRepro.tsx's
 * own header for the full explanation). Mirrors playwright.lab-shell.
 * config.ts's own isolated-vite pattern: a LOCAL Vite dev server on its own
 * port (5199, distinct from the lab-shell/vista-proof family's 5174 to avoid
 * colliding with any other concurrently-running agent session) so
 * import.meta.env.DEV is true and the /lab/tableau-freeze-repro route is
 * live, no Docker dependency.
 *
 * Run:  npx playwright test -c playwright.tableau-freeze-repro.config.ts
 */
export default defineConfig({
  testDir:       './playwright/e2e',
  testMatch:     ['tableau-freeze-repro.spec.ts'],
  fullyParallel: false,
  forbidOnly:    !!process.env.CI,
  retries:       0,
  workers:       1,
  reporter:      'list',
  use: {
    baseURL:    'http://localhost:5199',
    trace:      'off',
    screenshot: 'off',
    video:      'off',
  },
  projects: [
    {
      name: 'chromium',
      use: { ...devices['Desktop Chrome'], viewport: { width: 1440, height: 900 } },
    },
  ],
  webServer: {
    command:             'npx vite --port 5199',
    url:                 'http://localhost:5199',
    reuseExistingServer: true,
    stdout:              'pipe',
    stderr:              'pipe',
    timeout:             30_000,
  },
});

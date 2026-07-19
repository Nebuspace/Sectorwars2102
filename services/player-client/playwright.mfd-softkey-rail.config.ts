import { defineConfig, devices } from '@playwright/test';

/**
 * MFD Softkey Rail Geometry — WO-UI-MAX-BATCH-1 proof.
 *
 * Sibling of playwright.lab-shell.config.ts (same LabShell route, same local
 * Vite dev server on port 5174, no Docker/auth dependency) with its OWN
 * testMatch so it doesn't collide with lab-shell-geometry.spec.ts's config
 * (that spec/config pair belongs to the WO-UI0-PERSISTENT-SHELL lane and is
 * left untouched here). Parametrizes the 3 viewports the WO's Accept clause
 * requires (1440x900, 1560x980, 1280x800) as separate projects so a single
 * `npx playwright test -c playwright.mfd-softkey-rail.config.ts` run proves
 * all three.
 *
 * Run:  npx playwright test -c playwright.mfd-softkey-rail.config.ts
 */
export default defineConfig({
  testDir:       './playwright/e2e',
  testMatch:     ['mfd-softkey-rail-geometry.spec.ts'],
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
      name: 'v1440x900',
      use: { ...devices['Desktop Chrome'], viewport: { width: 1440, height: 900 } },
    },
    {
      name: 'v1560x980',
      use: { ...devices['Desktop Chrome'], viewport: { width: 1560, height: 980 } },
    },
    {
      name: 'v1280x800',
      use: { ...devices['Desktop Chrome'], viewport: { width: 1280, height: 800 } },
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

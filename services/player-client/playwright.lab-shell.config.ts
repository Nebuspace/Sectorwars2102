import { defineConfig, devices } from '@playwright/test';

/**
 * Lab Shell Playwright configuration — WO-UI0-PERSISTENT-SHELL lane B.
 *
 * Separate from playwright.config.ts (which targets the Docker stack on port
 * 3000). Mirrors playwright.vista-proof.config.ts: starts a LOCAL Vite dev
 * server on port 5174 so that:
 *   • import.meta.env.DEV === true (the /lab/shell route is live)
 *   • No Docker dependency — runs entirely on the Mac
 *   • No auth required — LabShell mocks GameContext directly, no API calls
 *
 * The webServer.reuseExistingServer=true means you can pre-start vite
 * ("npx vite --port 5174") to skip the startup wait on repeated runs, and
 * this config can share an already-running instance with the vista-proof
 * family of configs (same port, same convention).
 *
 * Run:  npx playwright test -c playwright.lab-shell.config.ts
 */
export default defineConfig({
  testDir:       './playwright/e2e',
  testMatch:     ['lab-shell-geometry.spec.ts'],
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
      // viewport MUST come after the devices['Desktop Chrome'] spread — that
      // preset carries its own default viewport (1280x720) which otherwise
      // wins over anything set at the top-level `use` above.
      use: { ...devices['Desktop Chrome'], viewport: { width: 1440, height: 900 } },
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

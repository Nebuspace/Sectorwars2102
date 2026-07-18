import { defineConfig, devices } from '@playwright/test';

/**
 * Vista CI Budget Gate — WO-PERF-BUDGET-GATE.
 *
 * THE REAL CI GATE. Deliberately headless:true (the default) — this is what
 * every CI container gets (no real GPU), and that's exactly the point: the
 * gate only checks drawScene-only cost (frameMs − postProcess), which is
 * CPU-bound and portable, so it can run correctly under SwiftShader software
 * rendering without postProcess's ~150x software inflation ever entering the
 * pass/fail decision. See src/vista/perf/budget-gate.ts's header comment for
 * the full rationale, and playwright.vista-budget-gate.headed.config.ts for
 * the (non-CI, reference-only) portability comparison run.
 */
export default defineConfig({
  testDir:       './playwright/vista-perf',
  testMatch:     ['vista-budget-gate.spec.ts'],
  fullyParallel: false,
  forbidOnly:    !!process.env.CI,
  retries:       0,
  workers:       1,
  reporter:      'list',
  timeout:       120_000,
  use: {
    baseURL:    'http://localhost:5174',
    trace:      'off',
    screenshot: 'off',
    video:      'off',
    // headless left at the Playwright default (true) — deliberately, see header.
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

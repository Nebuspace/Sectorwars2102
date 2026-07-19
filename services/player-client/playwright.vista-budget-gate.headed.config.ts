import { defineConfig, devices } from '@playwright/test';

/**
 * Vista CI Budget Gate — REFERENCE-ONLY portability run.
 *
 * NOT wired into CI. Runs the SAME gate spec as playwright.vista-budget-gate.config.ts
 * but with headless:false (real GPU, per the SwiftShader trap documented in
 * playwright.vista-perf.config.ts and src/vista/perf/budget-gate.ts). Its
 * sole purpose is the portability proof: confirm drawScene-only numbers
 * measured here match the real CI (headless/software) run within noise —
 * i.e. that excluding postProcess actually achieves the env-independence the
 * gate design depends on. Compare its output
 * (playwright/artifacts/vista-budget-gate-report-gpu.json) against the CI
 * config's (…-report-software.json).
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
    headless:   false, // reference-only — see header note
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

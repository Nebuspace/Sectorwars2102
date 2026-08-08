import { defineConfig, devices } from '@playwright/test';

const PORT = Number(process.env.ADMIN_UI_SMOKE_PORT || 4173);
const BASE_URL = process.env.ADMIN_UI_URL || `http://127.0.0.1:${PORT}`;

/**
 * Package-local Playwright config for admin-ui route smoke (WO-ADM-ROUTE-SMOKE-E2E).
 * Starts a Vite preview of the built UI — no gameserver required (API stubbed in specs).
 */
export default defineConfig({
  testDir: './playwright/e2e',
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  workers: process.env.CI ? 1 : undefined,
  reporter: [['list'], ['html', { open: 'never', outputFolder: 'playwright-report' }]],
  timeout: 30_000,
  expect: { timeout: 10_000 },
  use: {
    baseURL: BASE_URL,
    trace: 'on-first-retry',
    screenshot: 'only-on-failure',
    viewport: { width: 1440, height: 900 },
  },
  projects: [
    {
      name: 'chromium',
      use: { ...devices['Desktop Chrome'] },
    },
  ],
  webServer: {
    command: `npm run build:novcheck && npx vite preview --host 127.0.0.1 --port ${PORT} --strictPort`,
    url: BASE_URL,
    reuseExistingServer: !process.env.CI,
    timeout: 180_000,
  },
  outputDir: './playwright-test-results',
});

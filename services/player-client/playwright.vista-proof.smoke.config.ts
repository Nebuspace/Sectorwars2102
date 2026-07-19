import { defineConfig, devices } from '@playwright/test';
export default defineConfig({
  testDir: './playwright/vista-proof', testMatch: ['w7-live-mount-smoke.spec.ts'],
  fullyParallel: false, retries: 0, workers: 1, reporter: 'list',
  use: { baseURL: 'http://localhost:5174', trace: 'off', screenshot: 'off', video: 'off' },
  projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'] } }],
  webServer: { command: 'npx vite --port 5174', url: 'http://localhost:5174', reuseExistingServer: true, timeout: 30000 },
});

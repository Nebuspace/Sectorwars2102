/**
 * Vista Proof BEFORE config — runs against the worktree at commit 1fb4cec
 * (pre-Wave-1 water/terrain refactor) so before/after captures are comparable.
 *
 * Usage:
 *   1. Start Vite in the worktree:
 *        cd /tmp/vista-before/services/player-client
 *        node_modules/.bin/vite --port 5175
 *   2. From services/player-client/ (main tree):
 *        VISTA_RUN_LABEL=before npx playwright test \
 *          -c playwright.vista-proof-before.config.ts \
 *          playwright/vista-proof/vista-types.spec.ts
 *
 * Artifacts land in the MAIN tree's playwright/artifacts/ (process.cwd() = main tree).
 */
import { defineConfig, devices } from '@playwright/test';

export default defineConfig({
  testDir:       './playwright/vista-proof',
  fullyParallel: false,
  forbidOnly:    !!process.env.CI,
  retries:       0,
  workers:       1,
  reporter:      'list',
  use: {
    baseURL:    'http://localhost:5175',
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
  // Vite is pre-started in the worktree; reuseExistingServer connects to it.
  webServer: {
    command:             'node_modules/.bin/vite --port 5175',
    url:                 'http://localhost:5175',
    reuseExistingServer: true,
    cwd:                 '/tmp/vista-before/services/player-client',
    stdout:              'pipe',
    stderr:              'pipe',
    timeout:             30_000,
  },
});

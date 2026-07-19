import { defineConfig, devices } from '@playwright/test';

/**
 * Vista Perf Benchmark — WO-PERF-HARNESS driver config.
 *
 * Separate from playwright.config.ts (Docker stack) and every playwright.vista-proof*.config.ts
 * (pixel-diff proofs) for one critical reason: this run needs REAL GPU-accelerated
 * canvas rendering, not just deterministic pixels.
 *
 * headless:false — LOAD-BEARING, do not remove.
 *   Verified live (2026-07-10) on this Mac: default headless Chromium reports
 *   `ANGLE ... SwiftShader Device ... SwiftShader driver` as its WebGL renderer —
 *   a SOFTWARE rasterizer. postProcess() (canvas ctx.filter blur, the bloom pass)
 *   measured ~35-50ms under SwiftShader vs ~0.3ms under headed Chromium (which
 *   reports a real `ANGLE ... Metal Renderer` GPU on this Mac) — a ~150x
 *   distortion that would misclassify every scene as OVER_FLOOR regardless of
 *   actual per-frame cost. macOS's WindowServer makes a real (if invisible)
 *   Chromium window launchable headlessly-from-a-shell — `headless:false` here
 *   does NOT require an attached display or open any visible window a human
 *   needs to look at; it only requests the real compositor path.
 *   The pixel-diff proof configs (playwright.vista-proof*.config.ts) correctly
 *   stay headless — they only need deterministic PIXELS, not real timing, and
 *   software vs hardware rasterization does not change canvas pixel OUTPUT.
 */
export default defineConfig({
  testDir:       './playwright/vista-perf',
  fullyParallel: false,
  forbidOnly:    !!process.env.CI,
  retries:       0,
  workers:       1,
  reporter:      'list',
  timeout:       120_000, // 12 scenes × fresh page + settle time
  use: {
    baseURL:    'http://localhost:5174',
    trace:      'off',
    screenshot: 'off',
    video:      'off',
    headless:   false, // see LOAD-BEARING note above
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

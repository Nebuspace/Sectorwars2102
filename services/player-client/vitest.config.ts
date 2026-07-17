import { defineConfig, configDefaults } from 'vitest/config';

export default defineConfig({
  test: {
    // Pure pipeline tests: no DOM, no canvas.  Node env keeps the suite lean
    // and dependency-free (no jsdom, no happy-dom).  All vista/core tests must
    // stay importable in node — no browser globals allowed in that path.
    environment: 'node',
    // Vitest owns src/** unit tests only.  Playwright specs under playwright/**
    // (and e2e_tests/**) use the Playwright runner, NOT vitest — its test API
    // (test.beforeAll etc.) is incompatible and would fail collection here.
    include: ['src/**/*.{test,spec}.{ts,tsx}'],
    exclude: [...configDefaults.exclude, 'playwright/**', 'e2e_tests/**', '**/*.perfsweep.test.ts'],
  },
});

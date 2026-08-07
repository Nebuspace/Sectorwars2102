import { defineConfig, configDefaults } from 'vitest/config';
import react from '@vitejs/plugin-react';

// admin-ui unit tests: standard jsdom-by-default config. Unlike
// player-client's vitest.config.ts (which defaults to `environment: 'node'`
// for a large pure-pipeline test suite, with jsdom opted in per-file), this
// suite is component/hook tests written from scratch — jsdom is the right
// default for all of it, no per-file override needed.
export default defineConfig({
  plugins: [react()],
  test: {
    environment: 'jsdom',
    // @testing-library/react's automatic post-test DOM cleanup only
    // self-registers when it detects a global `afterEach` (i.e. `globals:
    // true`); without it, unmounted trees from a prior test in the same
    // file leak into the next `render()` call (duplicate-element query
    // failures). See setup.ts for the jest-dom matcher import.
    globals: true,
    setupFiles: ['./src/test/setup.ts'],
    // Vitest owns src/** unit tests only. Playwright specs live under
    // playwright/** and use the Playwright runner, NOT vitest — its test
    // API (test.beforeAll etc.) is incompatible and would fail collection
    // here. Mirrors player-client's own exclude pattern.
    include: ['src/**/*.{test,spec}.{ts,tsx}'],
    exclude: [...configDefaults.exclude, 'playwright/**', 'e2e_tests/**'],
  },
});

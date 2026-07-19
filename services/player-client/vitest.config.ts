import { fileURLToPath } from 'node:url';
import { defineConfig, configDefaults } from 'vitest/config';

export default defineConfig({
  resolve: {
    alias: {
      // AbortSignal realm-mismatch fix for jsdom test files (Node 24):
      // vitest resolves a per-file `// @vitest-environment <name>` that
      // isn't a builtin ("node"/"jsdom"/"happy-dom"/"edge-runtime") as the
      // package `vitest-environment-<name>` — that lookup goes through
      // Vite's own resolver, so a plain alias is enough to point it at a
      // local file with no real package needed. (A relative/absolute path
      // directly in the docblock does NOT work: vitest's docblock parser
      // only captures `[\w-]+`, i.e. no dots or slashes — a bare name is
      // the only thing that fits.) See src/test/environments/jsdomNodeFetch.ts
      // for what this environment actually fixes and why.
      'vitest-environment-jsdomnodefetch': fileURLToPath(
        new URL('./src/test/environments/jsdomNodeFetch.ts', import.meta.url),
      ),
    },
  },
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

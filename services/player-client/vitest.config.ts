import { defineConfig } from 'vitest/config';

export default defineConfig({
  test: {
    // Pure pipeline tests: no DOM, no canvas.  Node env keeps the suite lean
    // and dependency-free (no jsdom, no happy-dom).  All vista/core tests must
    // stay importable in node — no browser globals allowed in that path.
    environment: 'node',
  },
});

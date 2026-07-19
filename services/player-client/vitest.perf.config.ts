import { defineConfig } from 'vitest/config';

// QUEUE-PERF-TEST-FOOTPRINT (2026-07-16): dedicated config for the small set
// of deliberately-slow, full-fidelity sweep tests (*.perfsweep.test.ts) that
// vitest.config.ts's own `exclude` keeps OUT of the default `npm test` run
// (harness weight, not math — see each perfsweep file's own doc-comment for
// the Python-twin benchmark citation proving the underlying computation is
// fast; it's Vitest's per-assertion overhead at very high iteration counts
// that's slow). Invoke explicitly via `npm run test:perf` when touching the
// underlying math, or in a dedicated (non-blocking) CI lane if one is added
// later — never as part of the default suite.
export default defineConfig({
  test: {
    environment: 'node',
    include: ['src/**/*.perfsweep.test.ts'],
  },
});

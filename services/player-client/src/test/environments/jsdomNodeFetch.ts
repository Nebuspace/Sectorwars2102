import type { Environment } from 'vitest/runtime';
import { builtinEnvironments } from 'vitest/runtime';

const jsdomEnvironment = builtinEnvironments.jsdom;

/**
 * Wraps vitest's builtin "jsdom" environment to fix a realm mismatch between
 * jsdom's AbortController/AbortSignal and Node's native ones.
 *
 * Root cause: vitest's jsdom environment copies jsdom's own
 * AbortController/AbortSignal implementations onto `globalThis` for the
 * duration of a test file (jsdom implements these classes but not `fetch`
 * itself, so `fetch`/`Request` stay native either way). Starting with
 * Node 24, the bundled undici that backs the native global `fetch()` does a
 * strict `instanceof` brand check on any `signal` passed into
 * `new Request(...)`. A signal produced by jsdom's AbortController is a
 * different class (a different realm), so that check fails:
 *   TypeError: RequestInit: Expected signal ("AbortSignal {}") to be an
 *   instance of AbortSignal.
 * even though it is, by API shape, a fully spec-compliant AbortSignal. This
 * bites the moment any code under test calls `fetch()` with a signal from
 * `new AbortController()` — e.g. react-router's client-side `navigate()`,
 * which does exactly that on every navigation.
 *
 * Fix: after jsdom's own setup() has populated the globals for a test file,
 * restore Node's native AbortController/AbortSignal (captured from `global`
 * a moment earlier, before jsdom's setup() had a chance to overwrite them).
 * jsdom's own teardown() already restores whatever was on `global` before
 * its setup() ran, key-by-key, so it naturally puts these back too — no
 * extra teardown logic needed here.
 *
 * Opt a test file into this via:
 *   // @vitest-environment jsdomnodefetch
 * (vitest resolves an unrecognized per-file `@vitest-environment` name as
 * the package `vitest-environment-<name>`; `vitest.config.ts` aliases that
 * specifier to this file — see the alias comment there for why a bare name
 * is required and a relative path in the docblock does not work.)
 */
const jsdomNodeFetch: Environment = {
  ...jsdomEnvironment,
  name: 'jsdom-node-fetch',
  async setup(global, options) {
    const nativeAbortController = global.AbortController;
    const nativeAbortSignal = global.AbortSignal;
    const result = await jsdomEnvironment.setup(global, options);
    global.AbortController = nativeAbortController;
    global.AbortSignal = nativeAbortSignal;
    return result;
  },
};

export default jsdomNodeFetch;

/**
 * StatusBar — vitals-cluster distribution CSS pin (WO-UIPC-STATUSBAR-VITALS-
 * LAYOUT). jsdom has no real layout engine (confirmed baseline: `.status-bar`
 * / `.sb-vitals` `getBoundingClientRect()` reads all-zero under createRoot
 * mounts here, and vitest's default `css: false` doesn't inject stylesheet
 * text into the DOM for computed-style reads either — see
 * StatusBar.smoke.test.tsx / GameLayout.statusBarIntegration.test.tsx, both
 * of which stick to DOM-presence assertions for the same reason). So this is
 * a structural SOURCE-level pin, not a geometry one: parses the actual
 * `.sb-vitals { ... }` rule block out of statusbar.css and asserts the fixed
 * property is present INSIDE that block (not merely mentioned in a nearby
 * comment — the block is sliced from the selector's own `{` to its matching
 * `}`, so a doc-comment quoting the same words can't false-positive this).
 * Full 1440x900 pixel geometry is the Orchestrator's Playwright re-verify.
 */
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, it, expect } from 'vitest';

const CSS_PATH = resolve(__dirname, '../statusbar.css');
const css = readFileSync(CSS_PATH, 'utf-8');

function ruleBlock(selector: string): string {
  const start = css.indexOf(`${selector} {`);
  if (start === -1) {
    throw new Error(`selector "${selector}" not found in statusbar.css`);
  }
  const braceOpen = css.indexOf('{', start);
  const braceClose = css.indexOf('}', braceOpen);
  return css.slice(braceOpen + 1, braceClose);
}

describe('statusbar.css — vitals cluster distribution', () => {
  it('.sb-vitals grows to fill the row (flex:1, min-width:0) — the outer distribution was never broken', () => {
    const block = ruleBlock('.sb-vitals');
    expect(block).toMatch(/flex:\s*1\s+1\s+auto\s*;/);
    expect(block).toMatch(/min-width:\s*0\s*;/);
  });

  it('.sb-vitals spreads its own children across its (grown) width — the actual fix', () => {
    const block = ruleBlock('.sb-vitals');
    expect(block).toMatch(/justify-content:\s*space-between\s*;/);
  });

  it('outer .status-bar row still pins name/location left and settings/logout right via flex:0 (untouched by this fix)', () => {
    const statusBarBlock = ruleBlock('.status-bar');
    expect(statusBarBlock).toMatch(/display:\s*flex\s*;/);
    const dossierLocationBlock = ruleBlock('.sb-dossier,\n.sb-location');
    expect(dossierLocationBlock).toMatch(/flex:\s*0\s+0\s+auto\s*;/);
  });

  // DEFECT FIX (orchestrator, stage) — LogoutButton renders the SHARED
  // `.logout-button` class (auth.css: `width: 100%`, built for its OTHER
  // context, UserProfile.tsx's full-width sidebar item). Pre-fix,
  // `.sb-logout-btn` set `flex: 0 0 auto` but never its OWN `width`, so
  // that 100% applied unchallenged: flex-basis:auto deferred to it,
  // flex-shrink:0 refused to shrink from it, and LOGOUT exploded to ~the
  // full row (measured live: 1414.41px @1440 via a headless-Chromium
  // static-CSS-isolation proof against the real stylesheet, reproducing
  // the exact orchestrator-reported number), starving the sibling
  // `.sb-vitals` (flex:1 1 auto; min-width:0, tested above) down to 0px —
  // same real-Chromium proof measured it collapsing to exactly 0. This is
  // the SOURCE-level regression pin (see file-header rationale); the full
  // pixel proof is Playwright/Chromium-only, not re-creatable in jsdom.
  it('.sb-logout-btn: LOGOUT is bounded, not a full-width strip (the fixed defect)', () => {
    const block = ruleBlock('.status-bar .sb-logout-btn');
    // Explicit width override — must NOT be missing (that was the actual
    // bug: no width declaration here at all let auth.css's 100% win by
    // default, no cascade contest required).
    expect(block).toMatch(/width:\s*auto\s*;/);
    // A hard ceiling, independent of any future auth.css change to
    // `.logout-button`'s own width value.
    expect(block).toMatch(/max-width:\s*6rem\s*;/);
    // Still flex:0 0 auto (grow:0 -- LOGOUT never grows to consume slack
    // from a shrinking `.sb-vitals` either).
    expect(block).toMatch(/flex:\s*0\s+0\s+auto\s*;/);
  });

  it('.sb-logout-btn: selector is `.status-bar`-ancestor-scoped, outranking auth.css\'s bare `.logout-button` (0,1,0) regardless of stylesheet load order', () => {
    expect(css).toMatch(/\.status-bar \.sb-logout-btn \{/);
    // The old bare (unscoped) selector must be gone, not just shadowed by
    // a duplicate rule later in the cascade.
    expect(css).not.toMatch(/\n\.sb-logout-btn \{/);
  });
});

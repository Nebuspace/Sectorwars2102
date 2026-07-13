/**
 * StatusBar — shell re-emission CSS pin (WO-UI0-SHELL-TRANSPLANT Leaf L1;
 * supersedes the prior WO-UIPC-STATUSBAR-VITALS-LAYOUT pin, which asserted
 * `.sb-vitals`'s OWN `flex:1 1 auto`/`justify-content:space-between` — that
 * mechanism is retired outright, not merely relocated, now that the
 * artifact's own `.grow` spacer (cockpit-shell.css) does the row's
 * right-pushing job instead).
 *
 * jsdom has no real layout engine (confirmed baseline: `getBoundingClientRect()`
 * reads all-zero under createRoot mounts here, and vitest's default
 * `css: false` doesn't inject stylesheet text into the DOM for computed-style
 * reads either — see StatusBar.smoke.test.tsx / GameLayout.
 * statusBarIntegration.test.tsx, both of which stick to DOM-presence
 * assertions for the same reason). So this is a structural SOURCE-level pin,
 * not a geometry one: parses the actual rule block for a given selector out
 * of statusbar.css and asserts a fixed property is present INSIDE that block
 * (not merely mentioned in a nearby comment — the block is sliced from the
 * selector's own `{` to its matching `}`, so a doc-comment quoting the same
 * words can't false-positive this). Full 1440x900 pixel geometry is the
 * Orchestrator's Playwright re-verify.
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

describe('statusbar.css — WO-UI0-SHELL-TRANSPLANT Leaf L1 re-emission', () => {
  it('.sb-vitals is `display: contents` — a pure DOM grouping node, no longer the flex-growing region', () => {
    const block = ruleBlock('.sb-vitals');
    expect(block).toMatch(/display:\s*contents\s*;/);
    // The retired mechanism must actually be gone, not just shadowed by a
    // later rule with the same selector.
    expect(block).not.toMatch(/flex:\s*1\s+1\s+auto/);
    expect(block).not.toMatch(/justify-content:\s*space-between/);
  });

  it('outer `.game-container .status-bar` row is trimmed to the dropdown-escape mechanism only (`.sbar` now owns the flex-row geometry)', () => {
    const block = ruleBlock('.game-container .status-bar');
    expect(block).toMatch(/position:\s*relative\s*;/);
    expect(block).toMatch(/z-index:\s*25\s*;/);
    expect(block).toMatch(/overflow:\s*visible\s*;/);
    // The old fixed-height flex-row geometry (now `.sbar`'s job,
    // cockpit-shell.css) must actually be gone from this rule.
    expect(block).not.toMatch(/display:\s*flex/);
    expect(block).not.toMatch(/height:\s*56px/);
    expect(block).not.toMatch(/grid-area/);
  });

  it('.vit.sb-credits is a 2-class compound selector, not bare `.sb-credits` (deterministically outranks `.vit`\'s own bare color rule regardless of CSS load order)', () => {
    expect(css).toMatch(/\.vit\.sb-credits\s*\{/);
    expect(css).not.toMatch(/\n\.sb-credits\s*\{/);
    const block = ruleBlock('.vit.sb-credits');
    expect(block).toMatch(/color:\s*var\(--credits-color/);
  });

  it('.sb-drones absorbs its own layout (no more nested `.sb-drones .sb-v` — no "DRONES" label wrapper in the re-emitted markup)', () => {
    const block = ruleBlock('.sb-drones');
    expect(block).toMatch(/display:\s*inline-flex\s*;/);
    expect(block).toMatch(/gap:\s*0\.4rem\s*;/);
    // (a `.not.toMatch` on the bare selector text would false-fail against
    // this very doc-comment's own mention of it -- require the rule's `{`.)
    expect(css).not.toMatch(/\.sb-drones \.sb-v\s*\{/);
  });

  it('the dossier max-height cap is scale-law-correct (`var(--svh)`, not a raw `100vh` that ignores #root\'s `zoom: var(--ui-scale)`)', () => {
    const block = ruleBlock('.sb-dossier-panel');
    expect(block).toMatch(/max-height:\s*calc\(var\(--svh\)\s*-\s*4\.5rem\)\s*;/);
    expect(block).not.toMatch(/max-height:\s*calc\(100vh/);
  });

  it('REP badge has no per-tier `--rep-color` grading left — `.repb` (cockpit-shell.css, fixed green) is the only rule now', () => {
    expect(css).not.toMatch(/\.sb-rep-badge\s*\{/);
    expect(css).not.toMatch(/--rep-color/);
  });

  it('[⚙]/[⏻] no longer carry the old fixed-size `.sb-icon-btn` square skin or the LogoutButton width-defect fix (both dead now that both are plain `.chip`s)', () => {
    expect(css).not.toMatch(/\.sb-icon-btn\s*\{/);
    expect(css).not.toMatch(/\.status-bar \.sb-logout-btn\s*\{/);
    expect(css).not.toMatch(/\.sb-logout-btn:hover\s*\{/);
  });
});

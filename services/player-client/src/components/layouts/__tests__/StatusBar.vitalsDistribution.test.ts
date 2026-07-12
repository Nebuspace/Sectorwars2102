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
});

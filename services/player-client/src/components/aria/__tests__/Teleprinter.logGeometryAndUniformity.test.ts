// ---- WO-UI-MAX-BATCH-1 REVISE (Max #22-24) — static source-level proof for
// the two claims jsdom can't see (this repo's default vitest `css: false`
// means an imported stylesheet's computed style is never applied to jsdom —
// see WindshieldTableau.test.tsx's own `.ssv-popup-title` precedent, same
// idiom used here): (b) LOG's open height is PINNED to the band's own
// per-mode height by construction (not a live measurement that could drift
// out of sync), and (c) the teleprinter's 3 views (ticker/PANEL/LOG) share
// ONE button/input/body-text token set, not three per-build-pass ones.
//
// Reads the real CSS/TSX source text and asserts on VALUES (never bare
// absence of a string — a "does NOT contain X" check false-fails on
// reformatting; see memory note source-grep-test-self-defeat), so a future
// refactor that keeps the same computed values but reformats the file stays
// green, while an actual VALUE drift (the geometry/uniformity claims this
// REVISE exists to guarantee) fails loudly.
import { describe, it, expect } from 'vitest';
import fs from 'node:fs';
import path from 'node:path';

const teleprinterCss = fs.readFileSync(
  path.resolve(__dirname, '../teleprinter.css'),
  'utf8'
);
const gameLayoutCss = fs.readFileSync(
  path.resolve(__dirname, '../../layouts/game-layout.css'),
  'utf8'
);
const cockpitShellCss = fs.readFileSync(
  path.resolve(__dirname, '../../layouts/cockpit-shell.css'),
  'utf8'
);
const teleprinterTsx = fs.readFileSync(
  path.resolve(__dirname, '../Teleprinter.tsx'),
  'utf8'
);

/** Extracts a single rule block's declaration body by selector (first
 *  match). Selector is matched literally (regex-escaped) immediately
 *  followed by `{...}` — mirrors WindshieldTableau.test.tsx's own
 *  `.ssv-popup-title` extraction idiom. */
function ruleBlock(css: string, selector: string): string {
  const escaped = selector.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  const match = css.match(new RegExp(`${escaped}\\s*\\{([^}]*)\\}`));
  expect(match, `expected to find a rule block for "${selector}"`).not.toBeNull();
  return match![1];
}

describe('LOG overlay geometry — .telelog height is PINNED to the band rect, not measured live', () => {
  it('teleprinter.css sizes the open LOG overlay off --tp-log-h (game-layout.css), the SAME token the band-height rules read', () => {
    const openRule = ruleBlock(teleprinterCss, '.teleprinter.tp-log-open .telelog');
    expect(openRule).toMatch(/height:\s*var\(--tp-log-h/);
  });

  it('--tp-log-h (game-layout.css) is byte-identical to `.band`\'s own per-mode height, in EVERY mode', () => {
    const bandFlight = cockpitShellCss.match(/--band-h-flight:\s*([\d.]+em)/);
    expect(bandFlight, 'expected cockpit-shell.css to declare --band-h-flight').not.toBeNull();

    const tpLogFlight = gameLayoutCss.match(/\.game-container\s*\{[^}]*--tp-log-h:\s*([\d.]+em)/s);
    expect(tpLogFlight, 'expected .game-container { --tp-log-h: ... } in game-layout.css').not.toBeNull();
    expect(tpLogFlight![1]).toBe(bandFlight![1]);

    const bandStation = ruleBlock(gameLayoutCss, '.game-container.mode-station .band');
    const tpLogStation = ruleBlock(gameLayoutCss, '.game-container.mode-station');
    const bandStationH = bandStation.match(/height:\s*([\d.]+em)/)![1];
    const tpLogStationH = tpLogStation.match(/--tp-log-h:\s*([\d.]+em)/)![1];
    expect(tpLogStationH).toBe(bandStationH);

    const bandSurface = ruleBlock(gameLayoutCss, '.game-container.mode-surface .band');
    const tpLogSurface = ruleBlock(gameLayoutCss, '.game-container.mode-surface');
    const bandSurfaceH = bandSurface.match(/height:\s*([\d.]+em)/)![1];
    const tpLogSurfaceH = tpLogSurface.match(/--tp-log-h:\s*([\d.]+em)/)![1];
    expect(tpLogSurfaceH).toBe(bandSurfaceH);
  });

  it('the root escapes overflow:hidden while LOG is open, so the overlay (which bleeds above the row via bottom:100%) is actually visible', () => {
    const openRootRule = ruleBlock(teleprinterCss, '.teleprinter.tp-log-open');
    expect(openRootRule).toMatch(/overflow:\s*visible/);
  });
});

describe('Visual uniformity across the 3 views (ticker/PANEL/LOG) — ONE shared token set, not per-pass drift', () => {
  it('every teleprinter button carries the shared .tkey class in its JSX (ticker XMIT/PANEL-toggle/LOG-toggle, PANEL\'s own XMIT + 3 mode tabs)', () => {
    const buttonClassNames = [
      /className="tkey tp-ticker-xmit"/,
      /className="tkey tp-panel-toggle"/,
      /className="tkey tp-log-toggle"/,
      /className="tkey tp-xmit"/,
      /className=\{`tkey tp-mode-btn tp-mode-\$\{m\.id\}/,
    ];
    for (const pattern of buttonClassNames) {
      expect(teleprinterTsx).toMatch(pattern);
    }
  });

  it('PANEL\'s own input carries the shared .tin class in its JSX, byte-identical base styling to the ticker\'s own input', () => {
    expect(teleprinterTsx).toMatch(/className=\{`tin tp-input/);
  });

  it('no teleprinter button re-declares its own base font-family/font-size/color/background/border — .tkey (cockpit-shell.css) is the ONLY base declaration; this file only layers state deltas', () => {
    // Positive-value check, not a bare-absence grep (memory:
    // source-grep-test-self-defeat) — the ONLY teleprinter.css rule blocks
    // whose selector chain ends in one of these button classes are the
    // documented state-delta ones; assert each of THOSE contains only
    // delta properties (border-color/color/box-shadow/opacity/cursor), not
    // a re-declared font-family/font-size.
    const deltaBlocks = [
      ruleBlock(teleprinterCss, '.tkey:disabled'),
      ruleBlock(teleprinterCss, '.tkey:hover:not(:disabled)'),
      ruleBlock(teleprinterCss, ".tkey[aria-pressed='true']"),
      ruleBlock(teleprinterCss, '.tp-mode-btn.active'),
    ];
    for (const block of deltaBlocks) {
      expect(block).not.toMatch(/font-family\s*:/);
      expect(block).not.toMatch(/font-size\s*:/);
    }
  });

  it('PANEL\'s body-text (.tp-line) shares the SAME .95em font-size as LOG\'s own .a/.p lines (cockpit-shell.css .telelog .lines) — was .68rem, an absolute page-root unit that drifted from the local .tele em-cascade', () => {
    const tpLineBlock = ruleBlock(teleprinterCss, '.tp-line');
    const telelogLinesBlock = ruleBlock(cockpitShellCss, '.telelog .lines');
    // Numeric compare (not raw string) — cockpit-shell.css's own convention
    // drops the leading zero (`.95em`) while this file spells it out
    // (`0.95em`); both are the SAME computed value, so a string compare
    // would false-fail on formatting alone.
    const tpLineSize = parseFloat(tpLineBlock.match(/font-size:\s*([\d.]+)em/)![1]);
    const telelogLinesSize = parseFloat(telelogLinesBlock.match(/font-size:\s*([\d.]+)em/)![1]);
    expect(tpLineSize).toBe(telelogLinesSize);
  });

  it('PANEL\'s ai/user line colors are byte-identical to .tline (ticker) and .telelog\'s own .a/.p (cockpit-shell.css) — amber2 for ai, phosphor-green #7ce6a0 for user', () => {
    const aiBlock = ruleBlock(teleprinterCss, '.tp-line.ai .tp-prefix,\n.tp-line.ai .tp-text');
    expect(aiBlock).toMatch(/color:\s*var\(--amber2/);

    const userBlock = ruleBlock(teleprinterCss, '.tp-line.user .tp-prefix,\n.tp-line.user .tp-text');
    expect(userBlock).toMatch(/color:\s*#7ce6a0/);

    const telelogA = ruleBlock(cockpitShellCss, '.telelog .a');
    expect(telelogA).toMatch(/color:\s*var\(--amber2\)/);
    const telelogP = ruleBlock(cockpitShellCss, '.telelog .p');
    expect(telelogP).toMatch(/color:\s*#7CE6A0/i);
  });

  it('PANEL\'s focus-expand ring reuses the SAME amber token .tin:focus already uses (was cyan — a divergent MFD accent on an otherwise phosphor-green/amber input)', () => {
    const tinFocus = ruleBlock(teleprinterCss, '.tin:focus');
    const tpInputFocused = ruleBlock(teleprinterCss, '.tp-input-focused');
    const tinColor = tinFocus.match(/border-color:\s*([^;]+);/)![1].trim();
    const inputColor = tpInputFocused.match(/border-color:\s*([^;]+);/)![1].trim();
    expect(inputColor).toBe(tinColor);
  });
});

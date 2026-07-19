#!/usr/bin/env node
/**
 * check-css-vars.js — player-client
 *
 * WO-CI-TOKEN-GATE. Blocking gate: fails the build if any `var(--x)` in the
 * CSS source tree references a custom property that is defined NOWHERE in
 * the codebase.
 *
 * Why this exists: an unresolvable var() does not fall back to a visible
 * "broken" state — per the CSS spec, when a custom property has no
 * registered value AND the var() call has no fallback, the entire
 * declaration it appears in is treated as invalid and is dropped from the
 * cascade. The property silently reverts to its inherited/initial value
 * instead. There is no red squiggle, no console warning, no visual crash —
 * a border/background/outline just quietly stops being the color it was
 * supposed to be. This class of bug shipped twice in one day on this
 * exact codebase before this gate existed: `--accent-primary` +
 * `--primary-green` (149 usage sites across 10 files, QUEUE-CSSVAR-DEFECT)
 * and the `--cockpit-*` family (108 usage sites across 2 files,
 * QUEUE-CSSVAR-COCKPIT) -- the second incident included a keyboard-focus
 * outline that had been invisible in PRODUCTION since the same day it
 * shipped. Only a machine reliably catches this; a human reviewing a diff
 * has no reason to suspect a var() reference is wrong, because nothing
 * about reading it looks broken.
 *
 * Definition sources this checker recognizes as "resolves the var" (a
 * custom property counts as defined if EITHER is true anywhere in the repo
 * -- this is deliberately NOT cascade/scope-aware; see "What this checker
 * does NOT do" below):
 *   1. A CSS custom-property declaration `--x: <value>;` in any selector,
 *      in any .css file under src/ (including a JS/vite-processed
 *      pre-processor step is not needed -- this is plain CSS).
 *   2. Runtime JS/TSX injection:
 *      a. `element.style.setProperty('--x', ...)` (or `.setProperty("--x"`
 *         / `` .setProperty(`--x` ``) call sites anywhere in .ts/.tsx.
 *      b. An inline `style={{ '--x': ... }}` object key, or any other
 *         object-literal string-quoted key shaped like a custom property
 *         (`'--x':` / `"--x":` / `` `--x`: ``) anywhere in .ts/.tsx --
 *         this single pattern also naturally covers
 *         src/themes/themes/cockpit.ts's ThemeProvider `cssVariables`
 *         object (~29 vars applied to the DOM at runtime by
 *         src/themes/ThemeProvider.tsx) without needing to special-case
 *         that file by path.
 *
 * A var() call WITH a fallback (`var(--x, someValue)`) is technically
 * CSS-valid even if `--x` is never defined anywhere (the fallback saves it
 * from the "whole declaration dropped" failure mode) -- but this gate
 * still flags it unless `--x` is defined or allowlisted. Rationale: a
 * fallback is where THIS codebase's undefined-var bugs have historically
 * been discovered (grep for `var(--name, ` is exactly how the two prior
 * incidents' evidence trails were built), and a permanently-unresolved var
 * riding on a fallback is either dead weight (delete the indirection) or a
 * real design gap (needs a value) -- either way it's worth a human
 * decision, tracked via the allowlist, not silent tolerance.
 *
 * What this checker does NOT do (documented limitations, not oversights):
 *   - It is NOT cascade-aware. A var defined only inside `.foo { --x: red }`
 *     counts as "defined" globally, even for a var(--x) reference in a
 *     file whose selector is never a descendant of .foo at runtime. This
 *     mirrors how the last two incidents were actually diagnosed by hand
 *     (grep for a `--x:` declaration ANYWHERE was the bar for "not a
 *     phantom var") and keeps the check a static source-text property
 *     rather than requiring a real DOM/JSX render to resolve. A
 *     wrong-scope-but-technically-declared-somewhere var is a real but
 *     different bug class (see --accent-primary/--surface-primary, which
 *     WERE "defined somewhere" and still broke 8 other files) --
 *     catching that needs a render-time tool, not a static one.
 *   - It does not parse SCSS/LESS nesting, CSS Modules, or any
 *     preprocessor syntax -- this repo has none of those (plain .css +
 *     inline style objects only, verified: no styled-components/emotion
 *     dependency in package.json).
 *   - It does not validate CSS Custom Property SYNTAX (`@property` typed
 *     custom properties) -- this repo doesn't use `@property` today.
 *
 * Parser: uses postcss (already present in node_modules as a transitive
 * dependency of the vite build chain -- verified via `require.resolve`,
 * NOT added to package.json as a new direct dependency) for AST-accurate
 * declaration/value extraction, so this doesn't trip over `//` inside a
 * url(), nested @media blocks, or a `>` inside a calc() the way a naive
 * regex would. If postcss is ever not resolvable (a future vite upgrade
 * drops it from the tree), this script falls back to a plain-regex parser
 * (see `regexFallbackParse` below) with the following known limitations:
 * it strips /* *‌/ comments but does not track @media/nesting depth (not
 * needed for var()-name extraction) and can mis-split a `var()` call that
 * itself contains a nested `var()` inside its fallback more than one level
 * deep (rare in this codebase; every real occurrence checked by hand has
 * at most one level, e.g. `var(--planet-accent, var(--accent-primary))`).
 *
 * Run: node scripts/check-css-vars.js
 *      npm run check:tokens
 * Exit code: 0 = every var() resolves (defined or allowlisted); 1 = at
 * least one does not (file:line list printed).
 */

const fs = require('fs');
const path = require('path');

const SRC_DIR = path.join(__dirname, '..', 'src');
const ALLOWLIST_PATH = path.join(__dirname, 'css-var-allowlist.json');

// ─── postcss (optional, preferred) ─────────────────────────────────────────
let postcss = null;
try {
  postcss = require('postcss');
} catch (e) {
  postcss = null;
}

// ─── File discovery ─────────────────────────────────────────────────────────
function walkDir(dir, exts, results = []) {
  const entries = fs.readdirSync(dir, { withFileTypes: true });
  for (const e of entries) {
    const full = path.join(dir, e.name);
    if (e.isDirectory()) {
      if (e.name === 'node_modules' || e.name === '__tests__' || e.name === '__fixtures__') continue;
      walkDir(full, exts, results);
    } else if (e.isFile() && exts.some((x) => e.name.endsWith(x))) {
      results.push(full);
    }
  }
  return results;
}

// ─── CSS side: collect var() usages + --x declarations ─────────────────────
// usages: [{ name, file, line, hasFallback }]
// declared: Set<string>
function collectFromCssPostcss(cssFiles) {
  const usages = [];
  const declared = new Set();

  for (const file of cssFiles) {
    const src = fs.readFileSync(file, 'utf8');
    let root;
    try {
      root = postcss.parse(src, { from: file });
    } catch (e) {
      // Unparsable CSS is a build problem in its own right, but not this
      // gate's job to report -- vite's own build step already catches
      // syntax errors. Skip rather than crash the whole gate on one bad file.
      continue;
    }
    root.walkDecls((decl) => {
      if (decl.prop.startsWith('--')) {
        declared.add(decl.prop);
      }
      // A single value can contain multiple var() calls (e.g. a
      // multi-stop gradient) -- scan the whole value string, not just
      // the first match.
      const line = decl.source && decl.source.start ? decl.source.start.line : 0;
      for (const m of matchAllVarCalls(decl.value)) {
        usages.push({ name: m.name, file, line, hasFallback: m.hasFallback });
      }
    });
  }
  return { usages, declared };
}

// Regex-fallback CSS parser (used only if postcss fails to `require`).
function collectFromCssRegex(cssFiles) {
  const usages = [];
  const declared = new Set();
  const stripComments = (t) => t.replace(/\/\*[\s\S]*?\*\//g, '');

  for (const file of cssFiles) {
    const raw = fs.readFileSync(file, 'utf8');
    const clean = stripComments(raw);
    const lines = clean.split('\n');
    lines.forEach((line, idx) => {
      // `g`-flag loop, not a single `.match()` -- a minified/compact rule
      // (e.g. cockpit-shell.css's `.stage, .game-container { --a:1; --b:2;
      // ... }` all on one line) declares several custom properties on the
      // SAME line; a non-global match only ever sees the first one and
      // silently drops the rest, which would make this fallback path
      // report false positives for every var after the first per line.
      const declRe = /(--[a-zA-Z0-9_-]+)\s*:/g;
      let dm;
      while ((dm = declRe.exec(line)) !== null) declared.add(dm[1]);
      for (const m of matchAllVarCalls(line)) {
        usages.push({ name: m.name, file, line: idx + 1, hasFallback: m.hasFallback });
      }
    });
  }
  return { usages, declared };
}

// Shared var(...) extractor -- handles ONE level of nested var() inside a
// fallback (documented limitation for deeper nesting, see file header).
function matchAllVarCalls(value) {
  if (!value || !value.includes('var(')) return [];
  const out = [];
  const re = /var\(\s*(--[a-zA-Z0-9_-]+)\s*(,)?/g;
  let m;
  while ((m = re.exec(value)) !== null) {
    out.push({ name: m[1], hasFallback: !!m[2] });
  }
  return out;
}

// ─── JS/TSX side: collect runtime-injected --x names ────────────────────────
function collectFromJs(jsFiles) {
  const declared = new Set();
  // setProperty('--x', ...) / .setProperty("--x", ...
  const setPropRe = /\.setProperty\(\s*['"`](--[a-zA-Z0-9_-]+)['"`]/g;
  // Any string-quoted object key shaped like a custom property, followed by
  // a colon -- covers inline `style={{ '--hdg': ... }}` AND
  // cssVariables: { '--color-primary': ... } in the theme files without
  // needing to special-case either by path.
  const objectKeyRe = /['"`](--[a-zA-Z0-9_-]+)['"`]\s*:/g;

  for (const file of jsFiles) {
    const src = fs.readFileSync(file, 'utf8');
    let m;
    while ((m = setPropRe.exec(src)) !== null) declared.add(m[1]);
    while ((m = objectKeyRe.exec(src)) !== null) declared.add(m[1]);
  }
  return declared;
}

// ─── Allowlist ───────────────────────────────────────────────────────────────
function loadAllowlist(allowlistPath) {
  if (!fs.existsSync(allowlistPath)) return {};
  const data = JSON.parse(fs.readFileSync(allowlistPath, 'utf8'));
  const map = {};
  for (const entry of data.allowlist || []) {
    map[entry.name] = entry;
  }
  return map;
}

// ─── Core check (pure-ish: returns a result, no process.exit) ──────────────
// Extracted so scripts/check-css-vars.selftest.js can point this at a
// throwaway fixture directory instead of the real src/ tree and assert on
// the returned exit code + reported file:line, without shelling out to a
// child process. `log` defaults to console.log; the self-test passes a
// capturing function instead so its own output stays quiet unless it fails.
function runCheck({ srcDir = SRC_DIR, allowlistPath = ALLOWLIST_PATH, log = console.log } = {}) {
  const cssFiles = walkDir(srcDir, ['.css']);
  const jsFiles = walkDir(srcDir, ['.ts', '.tsx']);

  const usePostcss = postcss !== null;
  const { usages, declared: cssDeclared } = usePostcss
    ? collectFromCssPostcss(cssFiles)
    : collectFromCssRegex(cssFiles);
  const jsDeclared = collectFromJs(jsFiles);
  const allowlist = loadAllowlist(allowlistPath);

  const allDeclared = new Set([...cssDeclared, ...jsDeclared]);

  log('=== SectorWars player-client CSS custom-property gate ===\n');
  log(`Parser: ${usePostcss ? 'postcss (AST-accurate)' : 'regex fallback (postcss not resolvable)'}`);
  log(`Scanned ${cssFiles.length} .css files, ${jsFiles.length} .ts/.tsx files.`);
  log(`Found ${allDeclared.size} distinct custom-property names defined (CSS decl or JS injection).`);
  log(`Found ${usages.length} var(...) usage sites.\n`);

  // Group unresolved usages by name.
  const unresolvedByName = new Map();
  for (const u of usages) {
    if (allDeclared.has(u.name)) continue;
    if (!unresolvedByName.has(u.name)) unresolvedByName.set(u.name, []);
    unresolvedByName.get(u.name).push(u);
  }

  let hardFailures = 0;
  let allowlisted = 0;
  const failureDetails = []; // [{ name, sites: [{file, line, hasFallback}] }]

  const sortedNames = [...unresolvedByName.keys()].sort();
  if (sortedNames.length === 0) {
    log('[OK] Every var() reference resolves to a known definition.\n');
  }

  for (const name of sortedNames) {
    const sites = unresolvedByName.get(name);
    const entry = allowlist[name];
    if (entry) {
      allowlisted++;
      log(`[ALLOWLISTED] ${name}  (${sites.length} site(s)) -- ${entry.reason}`);
    } else {
      hardFailures++;
      failureDetails.push({ name, sites });
      log(`[UNDEFINED] ${name}  (${sites.length} site(s), never defined and not allowlisted):`);
      for (const s of sites.slice(0, 10)) {
        const rel = path.relative(path.join(__dirname, '..'), s.file);
        log(`    ${rel}:${s.line}${s.hasFallback ? '  (has fallback -- still flagged, see file header)' : '  (BARE -- CSS-invalid, silently drops the whole declaration)'}`);
      }
      if (sites.length > 10) log(`    ... +${sites.length - 10} more`);
    }
  }

  log('\n─────────────────────────────────────────────────');
  if (hardFailures > 0) {
    log(`FAIL — ${hardFailures} undefined custom-propert${hardFailures === 1 ? 'y' : 'ies'} not allowlisted, ${allowlisted} allowlisted.`);
  } else {
    log(`PASS — 0 undefined custom properties outside the allowlist (${allowlisted} allowlisted, tracked with justification).`);
  }

  return {
    exitCode: hardFailures > 0 ? 1 : 0,
    hardFailures,
    allowlisted,
    failureDetails,
  };
}

// ─── CLI entry point ─────────────────────────────────────────────────────────
if (require.main === module) {
  const result = runCheck();
  process.exit(result.exitCode);
}

module.exports = { runCheck, walkDir };

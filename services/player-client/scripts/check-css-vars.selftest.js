#!/usr/bin/env node
/**
 * check-css-vars.selftest.js — player-client
 *
 * WO-CI-TOKEN-GATE proof-of-life: plants a deliberately-broken fixture in a
 * throwaway temp directory (NOT under src/, so it can never be picked up by
 * a real `npm run check:tokens` run against the actual tree) and asserts
 * that check-css-vars.js's runCheck() correctly:
 *   1. FAILS (exit code 1) on an undefined, non-allowlisted var() reference,
 *   2. names the exact file:line of the offending usage in its output,
 *   3. still PASSES (exit code 0) on a sibling fixture where the same var
 *      is defined, and
 *   4. still PASSES on a var() that resolves via the JS-injection side
 *      (inline `style={{ '--x': ... }}`), proving that detection path
 *      independently of the CSS-declaration path.
 * Cleans up the temp directory whether it passes or fails.
 *
 * This is NOT part of the `npm run check:tokens` gate itself (that only
 * scans src/) -- it's a regression test FOR the gate, proving the tool
 * still does its job if check-css-vars.js is ever edited.
 *
 * Run: node scripts/check-css-vars.selftest.js
 * Exit code: 0 = all 4 assertions passed; 1 = at least one failed (details
 * printed).
 */

const fs = require('fs');
const os = require('os');
const path = require('path');
const { runCheck } = require('./check-css-vars');

function makeFixtureDir() {
  return fs.mkdtempSync(path.join(os.tmpdir(), 'check-css-vars-selftest-'));
}

function writeAllowlist(dir, allowlist) {
  const p = path.join(dir, 'allowlist.json');
  fs.writeFileSync(p, JSON.stringify({ allowlist }, null, 2));
  return p;
}

let failures = 0;
function assert(condition, label) {
  if (condition) {
    console.log(`  [PASS] ${label}`);
  } else {
    failures++;
    console.log(`  [FAIL] ${label}`);
  }
}

console.log('=== check-css-vars.js self-test ===\n');

// ── Case 1: undefined bare var() must fail, with the right file:line ──────
{
  console.log('Case 1: undefined, non-allowlisted var() reference');
  const dir = makeFixtureDir();
  const cssPath = path.join(dir, 'broken.css');
  // 5 blank lines pad the target declaration to line 6, so the assertion
  // below is proving the checker reports the CORRECT line, not just "a"
  // line, ruling out an off-by-one that would silently always point at
  // line 1 and still look like it passed.
  fs.writeFileSync(
    cssPath,
    '\n\n\n\n\n.broken-fixture {\n  border-color: var(--totally-undefined-token);\n}\n'
  );
  const allowlistPath = writeAllowlist(dir, []);
  const quiet = [];
  const result = runCheck({ srcDir: dir, allowlistPath, log: (l) => quiet.push(l) });

  assert(result.exitCode === 1, 'exit code is 1');
  assert(result.hardFailures === 1, 'exactly 1 hard failure reported');
  assert(
    result.failureDetails.length === 1 &&
      result.failureDetails[0].name === '--totally-undefined-token',
    'the failing var name is reported correctly'
  );
  const site = result.failureDetails[0] && result.failureDetails[0].sites[0];
  assert(!!site && site.file === cssPath && site.line === 7, `file:line points at ${cssPath}:7 (got ${site && site.file}:${site && site.line})`);
  assert(!!site && site.hasFallback === false, 'correctly identified as a BARE (no-fallback) reference');

  fs.rmSync(dir, { recursive: true, force: true });
}

// ── Case 2: same var, but defined -- must pass ─────────────────────────────
{
  console.log('\nCase 2: same var, but defined in CSS -- must pass');
  const dir = makeFixtureDir();
  fs.writeFileSync(
    path.join(dir, 'fixed.css'),
    ':root {\n  --totally-undefined-token: #ff00ff;\n}\n.ok {\n  border-color: var(--totally-undefined-token);\n}\n'
  );
  const allowlistPath = writeAllowlist(dir, []);
  const quiet = [];
  const result = runCheck({ srcDir: dir, allowlistPath, log: (l) => quiet.push(l) });
  assert(result.exitCode === 0, 'exit code is 0 once the var is defined');

  fs.rmSync(dir, { recursive: true, force: true });
}

// ── Case 3: undefined but allowlisted -- must pass, not silently ignored ──
{
  console.log('\nCase 3: undefined but present in the allowlist -- must pass');
  const dir = makeFixtureDir();
  fs.writeFileSync(
    path.join(dir, 'allowlisted.css'),
    '.also-broken {\n  color: var(--deliberately-unresolved);\n}\n'
  );
  const allowlistPath = writeAllowlist(dir, [
    { name: '--deliberately-unresolved', reason: 'fixture case' },
  ]);
  const quiet = [];
  const result = runCheck({ srcDir: dir, allowlistPath, log: (l) => quiet.push(l) });
  assert(result.exitCode === 0, 'exit code is 0 when allowlisted');
  assert(result.allowlisted === 1, 'the allowlisted count is 1');

  fs.rmSync(dir, { recursive: true, force: true });
}

// ── Case 4: var only resolves via JS/TSX injection -- must pass ───────────
{
  console.log('\nCase 4: var only defined via inline style={{ \'--x\': ... }} -- must pass');
  const dir = makeFixtureDir();
  fs.writeFileSync(
    path.join(dir, 'uses-it.css'),
    '.js-set {\n  outline-color: var(--js-injected-only);\n}\n'
  );
  fs.writeFileSync(
    path.join(dir, 'Component.tsx'),
    "const el = <div style={{ '--js-injected-only': '#123456' }} />;\n"
  );
  const allowlistPath = writeAllowlist(dir, []);
  const quiet = [];
  const result = runCheck({ srcDir: dir, allowlistPath, log: (l) => quiet.push(l) });
  assert(result.exitCode === 0, 'exit code is 0 when only JS-injected');

  fs.rmSync(dir, { recursive: true, force: true });
}

console.log('\n─────────────────────────────────────────────────');
if (failures > 0) {
  console.log(`SELF-TEST FAIL — ${failures} assertion(s) failed. The gate itself may not be trustworthy.`);
  process.exit(1);
} else {
  console.log('SELF-TEST PASS — the checker correctly fails on undefined refs (with accurate file:line), passes once defined, respects the allowlist, and detects JS-injected definitions.');
  process.exit(0);
}

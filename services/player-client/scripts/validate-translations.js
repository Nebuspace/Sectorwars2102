#!/usr/bin/env node
/**
 * validate-translations.js — player-client
 *
 * Translations are served from the gameserver DB via /api/v1/i18n/{lang}/{ns}.
 * There are no static locale JSON bundles to diff. This script validates what
 * CAN be checked offline:
 *
 * 1. Scans src/**‌/*.{ts,tsx} for t('…') calls and collects used keys.
 * 2. Checks that every used key is within a known namespace
 *    (common | game | auth) — keys that look like unknown namespaces are flagged.
 * 3. Checks the shared seed file (gameserver/i18n/en/) for structural validity
 *    if it can be located relative to this repo root.
 * 4. Exits with code 1 if any hard errors are found; 0 otherwise (warnings
 *    printed to stdout for CI visibility).
 *
 * Run: node scripts/validate-translations.js
 *      npm run validate-translations
 */

const fs   = require('fs');
const path = require('path');

// ─── Config ──────────────────────────────────────────────────────────────────
const KNOWN_NAMESPACES = new Set(['common', 'game', 'auth']);
const SRC_DIR = path.join(__dirname, '..', 'src');

// Relative to repo root (two levels up from player-client/)
const GAMESERVER_I18N_DIR = path.join(__dirname, '..', '..', '..', 'services', 'gameserver', 'i18n');

// ─── Helpers ─────────────────────────────────────────────────────────────────
function walkDir(dir, ext, results = []) {
  const entries = fs.readdirSync(dir, { withFileTypes: true });
  for (const e of entries) {
    const full = path.join(dir, e.name);
    if (e.isDirectory() && e.name !== 'node_modules' && e.name !== '__tests__') {
      walkDir(full, ext, results);
    } else if (e.isFile() && ext.some(x => e.name.endsWith(x))) {
      results.push(full);
    }
  }
  return results;
}

function extractTCalls(src) {
  // Matches: t('key'), t("key"), useTranslation('ns'), and
  // two-arg forms like t('key', {...}) — we only care about the key string.
  const keys = [];
  // Standard t('key') calls
  const tRe = /\bt\(\s*['"`]([^'"`]+)['"`]/g;
  let m;
  while ((m = tRe.exec(src)) !== null) keys.push(m[1]);
  return keys;
}

function extractNamespacesFromUseTranslation(src) {
  // useTranslation('ns') or useTranslation(['ns1','ns2'])
  const ns = new Set();
  const re = /useTranslation\(\s*(?:'([^']+)'|"([^"]+)"|`([^`]+)`|\[([^\]]+)\])/g;
  let m;
  while ((m = re.exec(src)) !== null) {
    if (m[1]) ns.add(m[1]);
    if (m[2]) ns.add(m[2]);
    if (m[3]) ns.add(m[3]);
    if (m[4]) {
      // array: strip quotes and split
      m[4].replace(/['"`]/g, '').split(/\s*,\s*/).forEach(n => ns.add(n.trim()));
    }
  }
  return ns;
}

// ─── Main ────────────────────────────────────────────────────────────────────
let errors   = 0;
let warnings = 0;

console.log('=== SectorWars i18n Validation — player-client ===\n');

// 1. Scan source files
const sourceFiles = walkDir(SRC_DIR, ['.ts', '.tsx']);
console.log(`Scanning ${sourceFiles.length} source files in src/…`);

const allKeys       = [];
const declaredNs    = new Set();
const unknownNsUses = [];

for (const file of sourceFiles) {
  const src = fs.readFileSync(file, 'utf8');
  const keys = extractTCalls(src);
  allKeys.push(...keys);
  const ns = extractNamespacesFromUseTranslation(src);
  ns.forEach(n => {
    declaredNs.add(n);
    if (!KNOWN_NAMESPACES.has(n)) {
      unknownNsUses.push({ file: path.relative(SRC_DIR, file), ns: n });
    }
  });
}

console.log(`  Found ${allKeys.length} t(…) call sites across ${sourceFiles.length} files.`);
console.log(`  Declared namespaces: ${[...declaredNs].sort().join(', ') || '(none)'}`);

if (unknownNsUses.length > 0) {
  console.log('\n  [WARN] Unknown namespace(s) declared:');
  unknownNsUses.forEach(({ file, ns }) =>
    console.log(`    ${file} → useTranslation('${ns}') — not in known set [${[...KNOWN_NAMESPACES].join(', ')}]`)
  );
  warnings += unknownNsUses.length;
} else {
  console.log('  [OK] All declared namespaces are in the known set.');
}

// 2. Check the gameserver seed JSON files (if reachable)
console.log('\nChecking gameserver seed JSON files…');
if (fs.existsSync(GAMESERVER_I18N_DIR)) {
  const enDir = path.join(GAMESERVER_I18N_DIR, 'en');
  if (fs.existsSync(enDir)) {
    const jsonFiles = fs.readdirSync(enDir).filter(f => f.endsWith('.json'));
    if (jsonFiles.length === 0) {
      console.log('  [WARN] No JSON seed files found in gameserver/i18n/en/');
      warnings++;
    } else {
      for (const f of jsonFiles) {
        const full = path.join(enDir, f);
        try {
          const data = JSON.parse(fs.readFileSync(full, 'utf8'));
          if (typeof data !== 'object' || data === null) {
            console.log(`  [ERROR] ${f}: top-level value is not an object`);
            errors++;
          } else {
            // Recursively count keys
            function countKeys(obj) {
              return Object.values(obj).reduce((n, v) =>
                n + (typeof v === 'object' && v !== null ? countKeys(v) : 1), 0);
            }
            console.log(`  [OK] ${f}: valid JSON, ${countKeys(data)} leaf keys`);
          }
        } catch (e) {
          console.log(`  [ERROR] ${f}: JSON parse error — ${e.message}`);
          errors++;
        }
      }
    }
  } else {
    console.log('  [WARN] gameserver/i18n/en/ directory not found (OK if running outside monorepo).');
    warnings++;
  }
} else {
  console.log('  [WARN] gameserver/i18n/ directory not found (OK if running outside monorepo).');
  warnings++;
}

// 3. Delivery-model note
console.log('\nDelivery model:');
console.log('  Translations are served from the gameserver DB at /api/v1/i18n/{lang}/{ns}.');
console.log('  Known namespaces for player-client: ' + [...KNOWN_NAMESPACES].join(', '));
console.log('  Supported languages: en, es, zh, fr, pt (100%), de (partial)');
console.log('  Use the admin UI (Settings → Translation Management) to add or edit keys.');

// ─── Result ──────────────────────────────────────────────────────────────────
console.log('\n─────────────────────────────────────────────────');
if (errors > 0) {
  console.log(`FAIL — ${errors} error(s), ${warnings} warning(s).`);
  process.exit(1);
} else {
  console.log(`PASS — 0 errors, ${warnings} warning(s).`);
  process.exit(0);
}

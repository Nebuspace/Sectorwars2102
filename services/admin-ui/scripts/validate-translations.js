#!/usr/bin/env node
/**
 * validate-translations.js — admin-ui
 *
 * Translations are served from the gameserver DB via /api/v1/i18n/{lang}/{ns}.
 * There are no static locale JSON bundles inside admin-ui to diff. This script
 * validates what CAN be checked offline:
 *
 * 1. Scans src/**‌/*.{ts,tsx} for t('…') calls and collects used keys.
 * 2. Checks that every declared useTranslation namespace is within the known
 *    admin-ui set (common | admin | auth) — unknown namespaces are flagged.
 * 3. Checks the shared seed files (gameserver/i18n/<lang>/) for structural
 *    validity if they can be located relative to this repo root.
 * 4. AMBER-mode key-set consistency: for every NON-reference language directory
 *    present under gameserver/i18n/, compares each namespace's flattened key set
 *    against the `en` reference and flags MISSING keys (in en, absent in lang)
 *    and EXTRA keys (in lang, absent in en).
 * 5. Exits with code 1 if any hard errors are found; 0 otherwise (warnings
 *    printed to stdout for CI visibility).
 *
 * Run: node scripts/validate-translations.js
 *      npm run i18n:validate
 */

const fs   = require('fs');
const path = require('path');

// ─── Config ──────────────────────────────────────────────────────────────────
// admin-ui i18n config (src/i18n.ts): defaultNS 'common', ns ['common','admin','auth']
const KNOWN_NAMESPACES = new Set(['common', 'admin', 'auth']);
// admin-ui supported languages (src/i18n.ts SUPPORTED_LANGUAGES)
const SUPPORTED_LANGUAGES = ['en', 'es', 'zh', 'fr', 'pt', 'de'];
const REFERENCE_LANG = 'en';
const SRC_DIR = path.join(__dirname, '..', 'src');

// Relative to repo root (two levels up from admin-ui/)
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
  // Matches t('key'), t("key"), and two-arg forms like t('key', {...}) — we
  // only care about the key string.
  const keys = [];
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

// Flatten a nested object into dotted leaf keys (e.g. { a: { b: 1 } } -> ['a.b']).
function flattenKeys(obj, prefix = '', out = []) {
  for (const [k, v] of Object.entries(obj)) {
    const full = prefix ? `${prefix}.${k}` : k;
    if (v !== null && typeof v === 'object' && !Array.isArray(v)) {
      flattenKeys(v, full, out);
    } else {
      out.push(full);
    }
  }
  return out;
}

// Read every <ns>.json in a language dir into { ns: Set<flatKey> }.
function readLangKeySets(langDir) {
  const result = {};
  const files = fs.readdirSync(langDir).filter(f => f.endsWith('.json'));
  for (const f of files) {
    const ns = f.replace(/\.json$/, '');
    try {
      const data = JSON.parse(fs.readFileSync(path.join(langDir, f), 'utf8'));
      result[ns] = new Set(flattenKeys(data));
    } catch (e) {
      result[ns] = { __error: e.message };
    }
  }
  return result;
}

// ─── Main ────────────────────────────────────────────────────────────────────
let errors   = 0;
let warnings = 0;

console.log('=== SectorWars i18n Validation — admin-ui ===\n');

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
let referenceKeySets = null;       // { ns: Set<flatKey> } for the reference lang
let presentLangs     = [];          // language dirs actually present

if (fs.existsSync(GAMESERVER_I18N_DIR)) {
  presentLangs = fs.readdirSync(GAMESERVER_I18N_DIR, { withFileTypes: true })
    .filter(e => e.isDirectory())
    .map(e => e.name)
    .sort();

  const enDir = path.join(GAMESERVER_I18N_DIR, REFERENCE_LANG);
  if (fs.existsSync(enDir)) {
    const jsonFiles = fs.readdirSync(enDir).filter(f => f.endsWith('.json'));
    if (jsonFiles.length === 0) {
      console.log(`  [WARN] No JSON seed files found in gameserver/i18n/${REFERENCE_LANG}/`);
      warnings++;
    } else {
      for (const f of jsonFiles) {
        const full = path.join(enDir, f);
        try {
          const data = JSON.parse(fs.readFileSync(full, 'utf8'));
          if (typeof data !== 'object' || data === null) {
            console.log(`  [ERROR] ${REFERENCE_LANG}/${f}: top-level value is not an object`);
            errors++;
          } else {
            console.log(`  [OK] ${REFERENCE_LANG}/${f}: valid JSON, ${flattenKeys(data).length} leaf keys`);
          }
        } catch (e) {
          console.log(`  [ERROR] ${REFERENCE_LANG}/${f}: JSON parse error — ${e.message}`);
          errors++;
        }
      }
      referenceKeySets = readLangKeySets(enDir);
    }
  } else {
    console.log(`  [WARN] gameserver/i18n/${REFERENCE_LANG}/ directory not found (OK if running outside monorepo).`);
    warnings++;
  }
} else {
  console.log('  [WARN] gameserver/i18n/ directory not found (OK if running outside monorepo).');
  warnings++;
}

// 3. AMBER-mode cross-locale key-set consistency
console.log('\nKey-set consistency (AMBER): comparing each locale against the reference…');
if (referenceKeySets) {
  const otherLangs = presentLangs.filter(l => l !== REFERENCE_LANG);

  // Configured-but-absent languages are a WARN (translation seeds not yet added).
  const missingLangDirs = SUPPORTED_LANGUAGES.filter(l => !presentLangs.includes(l));
  if (missingLangDirs.length > 0) {
    console.log(`  [WARN] Configured language(s) with no seed dir under gameserver/i18n/: ${missingLangDirs.join(', ')}`);
    console.log('         (Translations may be DB-only for these; add seed dirs to enable offline key-set validation.)');
    warnings++;
  }

  if (otherLangs.length === 0) {
    console.log(`  [OK] Only the reference locale (${REFERENCE_LANG}) has seed files — nothing to cross-check offline.`);
  } else {
    for (const lang of otherLangs) {
      const langDir  = path.join(GAMESERVER_I18N_DIR, lang);
      const langSets = readLangKeySets(langDir);

      for (const ns of Object.keys(referenceKeySets)) {
        const refKeys  = referenceKeySets[ns];
        const langKeys = langSets[ns];

        if (!langKeys) {
          console.log(`  [WARN] ${lang}: namespace '${ns}' present in ${REFERENCE_LANG} but no ${ns}.json — entire namespace untranslated.`);
          warnings++;
          continue;
        }
        if (langKeys.__error) {
          console.log(`  [ERROR] ${lang}/${ns}.json: JSON parse error — ${langKeys.__error}`);
          errors++;
          continue;
        }

        const missing = [...refKeys].filter(k => !langKeys.has(k)).sort();
        const extra   = [...langKeys].filter(k => !refKeys.has(k)).sort();

        if (missing.length === 0 && extra.length === 0) {
          console.log(`  [OK] ${lang}/${ns}: key set matches ${REFERENCE_LANG} (${refKeys.size} keys).`);
        } else {
          if (missing.length > 0) {
            console.log(`  [WARN] ${lang}/${ns}: ${missing.length} MISSING key(s) (present in ${REFERENCE_LANG}, absent in ${lang}):`);
            missing.forEach(k => console.log(`           - ${k}`));
            warnings += missing.length;
          }
          if (extra.length > 0) {
            console.log(`  [WARN] ${lang}/${ns}: ${extra.length} EXTRA key(s) (absent in ${REFERENCE_LANG}, present in ${lang}):`);
            extra.forEach(k => console.log(`           + ${k}`));
            warnings += extra.length;
          }
        }
      }
    }
  }
} else {
  console.log('  [SKIP] Reference seed files unavailable — cannot run key-set consistency offline.');
}

// 4. Delivery-model note
console.log('\nDelivery model:');
console.log('  Translations are served from the gameserver DB at /api/v1/i18n/{lang}/{ns}.');
console.log('  Known namespaces for admin-ui: ' + [...KNOWN_NAMESPACES].join(', '));
console.log('  Supported languages: ' + SUPPORTED_LANGUAGES.join(', '));
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

#!/usr/bin/env node
/**
 * extract-strings.js — admin-ui
 *
 * Scans src/**‌/*.{ts,tsx} for i18next t('…') call sites and emits a sorted
 * JSON array of unique keys to stdout. Use this as a draft checklist when
 * adding new translatable content — compare the output against the DB via
 * the admin Translation Management page (Settings → Translation Management)
 * to spot keys that are used in code but not yet present in the catalog.
 *
 * Translations are served from the gameserver DB at /api/v1/i18n/{lang}/{ns}
 * (admin-ui namespaces: common | admin | auth) — there are no static locale
 * JSON bundles in this service to extract into, so this script emits the used
 * key set rather than mutating a catalog file.
 *
 * Usage:
 *   npm run i18n:extract
 *   npm run i18n:extract > /tmp/admin-ui-keys.json
 */

const fs   = require('fs');
const path = require('path');

const SRC_DIR = path.join(__dirname, '..', 'src');

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

const sourceFiles = walkDir(SRC_DIR, ['.ts', '.tsx']);
const keys = new Set();
const tRe  = /\bt\(\s*['"`]([^'"`]+)['"`]/g;

for (const file of sourceFiles) {
  const src = fs.readFileSync(file, 'utf8');
  let m;
  while ((m = tRe.exec(src)) !== null) keys.add(m[1]);
}

process.stdout.write(JSON.stringify([...keys].sort(), null, 2) + '\n');
process.stderr.write(`\nExtracted ${keys.size} unique key(s) from ${sourceFiles.length} file(s).\n`);

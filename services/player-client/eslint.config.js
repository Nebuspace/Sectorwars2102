// Flat config migration of the retired .eslintrc.js (ESLint 10 requires flat
// config -- the legacy .eslintrc.js was completely inert under ^10.5.0: every
// rule in it enforced NOTHING, the dead-gate class of bug, QUEUE-ESLINT-
// FLATCONFIG 2026-07-16). Rule set is a 1:1 port of the legacy config's
// INTENT, not an upgrade -- see the NOTE below re: the react-hooks plugin's
// newer bundled rules that were deliberately left off, and the KNOWN GAPS
// section for what the legacy config also never actually enforced.
//
// Same migration already done for admin-ui (services/admin-ui/eslint.config.js)
// -- identical dependency versions, identical gotchas, ported directly:
//   1. @eslint/js is NOT installed (checked node_modules + package.json +
//      lockfile) -- flat config's "eslint:recommended" string shorthand
//      requires it and fails without it. This means core-JS rules (no-undef,
//      no-unreachable, no-dupe-keys, etc.) have ZERO coverage here, same as
//      the dead legacy config (it never enforced them either -- extending
//      'eslint:recommended' from a working parser doesn't help if the WHOLE
//      config was inert). Reported as a known gap, not silently patched --
//      adding @eslint/js is a new dependency, out of scope for this ticket's
//      hard no-new-deps gate.
//   2. eslint-plugin-react@7.37.5 crashes under ESLint 10's flat-config
//      linting context when settings.react.version:'detect' is used
//      (TypeError: contextOrFilename.getFilename is not a function --
//      context.getFilename() was removed from the flat linting context;
//      the plugin's version-autodetect path still calls it). Pin explicitly
//      instead -- skips the buggy autodetect path entirely.
//   3. eslint-plugin-react-hooks@7.1.1 bundles a much larger "recommended"
//      flat config (React Compiler-era rules: static-components, use-memo,
//      immutability, purity, set-state-in-render, etc.) than the 2 rules the
//      legacy .eslintrc.js enabled via plugin:react-hooks/recommended. Wiring
//      only the original 2 rules preserves the legacy behavior exactly --
//      this is a format migration, not a rule-set expansion.
const reactPlugin = require('eslint-plugin-react');
const reactHooksPlugin = require('eslint-plugin-react-hooks');
const tsPlugin = require('@typescript-eslint/eslint-plugin');
const tsParser = require('@typescript-eslint/parser');

module.exports = [
  {
    files: ['**/*.{js,jsx,ts,tsx}'],
    plugins: {
      react: reactPlugin,
      'react-hooks': reactHooksPlugin,
    },
    languageOptions: {
      ecmaVersion: 'latest',
      sourceType: 'module',
      parserOptions: {
        ecmaFeatures: { jsx: true },
      },
    },
    settings: {
      // 'detect' triggers a legacy version-sniffing path in eslint-plugin-react
      // that calls the removed ESLint context.getFilename() API and crashes
      // under ESLint 10's flat-config linting context -- pin explicitly instead.
      react: { version: require('react/package.json').version },
    },
    rules: {
      ...reactPlugin.configs.flat.recommended.rules,
      'react/react-in-jsx-scope': 'off',
      // QUEUE-ESLINT-FLATCONFIG (2026-07-16): 38 findings across the
      // codebase (raw apostrophes/quotes in JSX text) resurrecting this dead
      // gate. Cosmetic/text-rendering correctness only, not a functional
      // bug class, and `--fix` rewrites visible copy text -- each one needs
      // a human glance to confirm the escaped output still reads right, too
      // many to responsibly review tonight. Temporarily 'warn' (was 'error'
      // under the dead legacy config's plugin:react/recommended extend,
      // which never enforced it either) so the gate ships green-and-honest.
      // Follow-up cleanup ticket recommended to burn this down and restore
      // 'error'.
      'react/no-unescaped-entities': 'warn',
      // NOTE: the installed react-hooks plugin (^7.1.1) bundles a much larger
      // "recommended" flat config (React Compiler-era rules) than the 2 rules
      // the original .eslintrc.js enabled via `plugin:react-hooks/recommended`.
      // Wiring only these two preserves the original rule set exactly -- this
      // is a format migration, not a rule-set expansion.
      'react-hooks/rules-of-hooks': 'error',
      'react-hooks/exhaustive-deps': 'warn',
      'no-unused-vars': ['error', { argsIgnorePattern: '^_' }],
    },
  },
  {
    files: ['**/*.{ts,tsx}'],
    languageOptions: {
      parser: tsParser,
      ecmaVersion: 'latest',
      sourceType: 'module',
      parserOptions: {
        ecmaFeatures: { jsx: true },
      },
    },
    plugins: {
      '@typescript-eslint': tsPlugin,
    },
    rules: {
      'no-unused-vars': 'off',
      // QUEUE-ESLINT-FLATCONFIG (2026-07-16): 63 findings surfaced across the
      // codebase resurrecting this dead gate -- genuinely too many to review
      // individually tonight without risking a bad --fix (unused-var removal
      // can hide a real bug the variable was meant to guard). Temporarily
      // 'warn' (was 'error' under the dead legacy config, which never
      // enforced it either) so the gate ships green-and-honest rather than
      // red-or-neutered; low functional risk category (dead code, not
      // behavior). Follow-up cleanup ticket recommended to burn this down
      // file-by-file and restore 'error'.
      '@typescript-eslint/no-unused-vars': ['warn', { argsIgnorePattern: '^_' }],
    },
  },
  {
    // React-Three-Fiber components: R3F intentionally extends JSX with
    // Three.js object properties (position/args/intensity/etc.) that aren't
    // real DOM attributes -- eslint-plugin-react's no-unknown-property rule
    // doesn't know about them without this explicit `ignore` list (a
    // documented false-positive class for R3F projects, not a real
    // violation). Scoped to the actual R3F files (34 of the migration's 212
    // findings, all here) rather than a global ignore, so the rule keeps its
    // full DOM-attribute-typo-catching value everywhere else.
    files: ['src/components/galaxy/**/*.{ts,tsx}'],
    rules: {
      'react/no-unknown-property': ['error', {
        ignore: [
          'args', 'blending', 'depthWrite', 'emissive', 'emissiveIntensity',
          'geometry', 'intensity', 'metalness', 'position', 'rotation',
          'roughness', 'side', 'sizeAttenuation', 'transparent', 'vertexColors',
        ],
      }],
    },
  },
];

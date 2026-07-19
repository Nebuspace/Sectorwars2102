// Flat config migration of the retired .eslintrc.js (ESLint 10 requires flat config).
// Rule set is a 1:1 port, not an upgrade -- see eslint.config.js NOTE below re: the
// react-hooks plugin's newer bundled rules that were deliberately left off.
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
      // NOTE: the installed react-hooks plugin (^7.1.1) bundles a much larger
      // "recommended" flat config (React Compiler-era rules) than the 2 rules
      // the original .eslintrc.js enabled via `plugin:react-hooks/recommended`.
      // Wiring only these two preserves the original rule set exactly -- this
      // is a format migration, not a rule-set expansion. Flagged separately.
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
      '@typescript-eslint/no-unused-vars': ['error', { argsIgnorePattern: '^_' }],
    },
  },
];

import js from '@eslint/js'
import globals from 'globals'
import reactHooks from 'eslint-plugin-react-hooks'
import reactRefresh from 'eslint-plugin-react-refresh'
import tseslint from 'typescript-eslint'
import { defineConfig, globalIgnores } from 'eslint/config'

export default defineConfig([
  globalIgnores(['dist']),
  {
    files: ['**/*.{ts,tsx}'],
    extends: [
      js.configs.recommended,
      tseslint.configs.recommended,
      reactHooks.configs.flat.recommended,
      reactRefresh.configs.vite,
    ],
    languageOptions: {
      ecmaVersion: 2020,
      globals: globals.browser,
    },
  },
  {
    // Browser tests run in Node, not the browser, and they deliberately handle
    // untyped API JSON: the whole point of asserting against a live service is
    // that the response is whatever the server actually sent, not a shape we
    // declared. Typing those reads would assert our assumptions rather than
    // the server's behaviour. React Fast Refresh does not apply here at all.
    files: ['tests/**/*.ts', 'playwright.config.ts'],
    languageOptions: {
      globals: { ...globals.node },
    },
    rules: {
      '@typescript-eslint/no-explicit-any': 'off',
      'react-refresh/only-export-components': 'off',
      // Playwright names the fixture callback's second parameter `use`, which
      // the React plugin reads as a hook call in a non-component function.
      // There is no React in this directory.
      'react-hooks/rules-of-hooks': 'off',
    },
  },
])

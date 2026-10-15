import { chromium, defineConfig } from '@playwright/test'
import fs from 'node:fs'

// Pinned Chromium for @playwright/test 1.62.1; fall back to the system browser with a warning (§12.1 step 6).
const pinned = (() => {
  try {
    return fs.existsSync(chromium.executablePath())
  } catch {
    return false
  }
})()
if (!pinned) console.warn('[e2e] pinned Playwright Chromium not installed; using /usr/bin/chromium')

export default defineConfig({
  testDir: './tests',
  globalSetup: './global-setup.ts',
  globalTeardown: './global-teardown.ts',
  workers: 1,
  fullyParallel: false,
  retries: 0,
  timeout: 120_000,
  expect: { timeout: 15_000 },
  reporter: [['list'], ['json', { outputFile: '.stack/results.json' }]],
  outputDir: './.stack/test-output',
  use: {
    viewport: { width: 1440, height: 900 },
    colorScheme: 'light',
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
    actionTimeout: 15_000,
    ...(pinned ? {} : { launchOptions: { executablePath: '/usr/bin/chromium' } }),
  },
})

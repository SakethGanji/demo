import { defineConfig } from '@playwright/test'
import config from './playwright.config'

export default defineConfig({
  ...config,
  testDir: '.',
  testMatch: 'capture.spec.ts',
  reporter: 'list',
  outputDir: '.stack/capture-output',
})

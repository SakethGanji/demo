// Playwright config used by `tb run` / `tb verify`. Output location comes from TB_RUN_DIR.
import { defineConfig, devices } from '@playwright/test';
import { join, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
import { loadTarget } from './src/config.ts';

const here = dirname(fileURLToPath(import.meta.url));
const runDir = process.env.TB_RUN_DIR ?? join(here, 'runs', 'adhoc');
const target = loadTarget(process.env.TB_TARGET ?? join(here, 'targets', 'demo.json'));

export default defineConfig({
  testDir: join(here, 'tests'),
  outputDir: join(runDir, 'test-results'),
  fullyParallel: false,
  workers: 1,
  retries: 0,
  timeout: 30_000,
  expect: { timeout: 5_000 },
  reporter: [['list'], ['junit', { outputFile: join(runDir, 'junit.xml') }]],
  use: {
    baseURL: target.baseUrl,
    trace: 'on',
    video: 'on',
    screenshot: 'only-on-failure',
    headless: true,
    actionTimeout: 10_000,
    navigationTimeout: 15_000,
  },
  projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'] } }],
});

import { defineConfig } from '@playwright/test'

// Two servers: a real API on a fresh, empty data folder and Vite proxying to it. Every test uploads the data it
// needs through the API. Ports are off the dev defaults so the suite runs beside `make dev`.
const API_PORT = 8291
const LIVE_PORT = 5291

export default defineConfig({
  testDir: 'e2e',
  timeout: 60_000,
  // Real AutoGluon fits share one API that fits one run at a time; two workers keep the queue short.
  workers: 2,
  expect: { timeout: 15_000 },
  fullyParallel: true,
  retries: 0,
  reporter: [['list']],
  use: {
    trace: 'off',
    viewport: { width: 1440, height: 900 },
    // Pre-installed Chromium (see the environment notes); Playwright finds it through PLAYWRIGHT_BROWSERS_PATH.
    launchOptions: process.env.PW_CHROMIUM_PATH ? { executablePath: process.env.PW_CHROMIUM_PATH } : {},
  },
  projects: [
    { name: 'live', use: { baseURL: `http://127.0.0.1:${LIVE_PORT}` }, testMatch: /(live|pages)\.spec\.ts/ },
  ],
  webServer: [
    {
      command: `rm -rf ../.e2e-data && ENSEMBLE_DATA_DIR=../.e2e-data .venv/bin/python -m uvicorn app.main:app --port ${API_PORT} --log-level warning`,
      cwd: 'backend',
      url: `http://127.0.0.1:${API_PORT}/api/health`,
      timeout: 120_000,
      reuseExistingServer: false,
    },
    {
      command: `npx vite --port ${LIVE_PORT} --strictPort`,
      env: { ENSEMBLE_API_URL: `http://127.0.0.1:${API_PORT}` },
      url: `http://127.0.0.1:${LIVE_PORT}`,
      timeout: 60_000,
      reuseExistingServer: false,
    },
  ],
})

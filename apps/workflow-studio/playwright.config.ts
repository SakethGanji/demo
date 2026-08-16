import { defineConfig, devices } from '@playwright/test'

/**
 * Browser tests for the datasets surface.
 *
 * These drive the real app against a real analytics-service — no mocks, no
 * fixtures baked into the client. That is deliberate: every bug this suite
 * exists to catch (a 204 parsed as a failure, a cursor replayed against the
 * wrong spec, a masked column leaking through a profile) lives precisely in the
 * seam between the UI and the service, which a mocked test cannot see.
 *
 * The studio talks cross-origin to :8001 and picks its backend by HOSTNAME, so
 * the base URL must be `localhost` exactly — a LAN IP falls through to the
 * empty default and every call 404s against Vite instead.
 */
export default defineConfig({
  testDir: './tests',
  // Fixtures are per-test and self-named, so tests do not collide.
  fullyParallel: true,
  workers: process.env.CI ? 2 : 4,
  forbidOnly: !!process.env.CI,
  retries: 0,
  reporter: process.env.CI ? [['list'], ['html', { open: 'never' }]] : [['list']],
  timeout: 45_000,
  expect: { timeout: 10_000 },

  globalSetup: './tests/global-setup.ts',
  globalTeardown: './tests/global-teardown.ts',

  use: {
    baseURL: process.env.UI_BASE || 'http://localhost:5174',
    viewport: { width: 1440, height: 1000 },
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
  },

  projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'] } }],

  // Reuse a dev server if one is already up; otherwise start one. The API is
  // NOT started here — global-setup checks it and fails with instructions,
  // because silently booting a database-backed service would hide a misconfig.
  webServer: {
    command: 'npx vite --port 5174 --strictPort',
    url: process.env.UI_BASE || 'http://localhost:5174',
    reuseExistingServer: true,
    timeout: 60_000,
  },
})

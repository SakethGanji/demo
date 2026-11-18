import { test as base, expect, type ConsoleMessage, type Page } from '@playwright/test'

export const ROUTES = ['home', 'quick-train', 'autopilot', 'gym', 'experiments', 'explain', 'registry', 'settings'] as const

/**
 * Every test page: external requests (web fonts) are answered locally so the suite is offline and deterministic,
 * and console errors and page errors are collected. In mock mode the dev proxy answers /api with 503 by design;
 * the browser logs that as a failed resource, which is the expected signal, so only those are allowed.
 */
export const test = base.extend<{ errors: string[] }>({
  errors: async ({ page }, use, info) => {
    const errors: string[] = []
    await page.route(/^https?:\/\/(fonts\.googleapis\.com|fonts\.gstatic\.com)\//, (r) => r.fulfill({ status: 200, contentType: 'text/css', body: '' }))
    const mock = info.project.name === 'mock'
    page.on('console', (m: ConsoleMessage) => {
      if (m.type() !== 'error') return
      if (mock && m.location().url.includes('/api/') && m.text().includes('503')) return
      errors.push(`${m.text()} @ ${m.location().url}`)
    })
    page.on('pageerror', (e) => errors.push(`pageerror: ${e.message}`))
    await use(errors)
  },
})

export { expect }

export async function open(page: Page, route: string) {
  await page.goto(`/#/${route}`)
  await expect(page.locator('.workspace')).toBeVisible()
}

export const apiMode = (page: Page) => page.getByTestId('api-mode')

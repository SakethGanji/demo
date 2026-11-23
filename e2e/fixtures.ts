import { test as base, expect, type APIRequestContext, type ConsoleMessage, type Page } from '@playwright/test'

export const ROUTES = ['home', 'build', 'quick-train', 'vela', 'gym', 'models', 'registry', 'settings'] as const

/**
 * Every test page: external requests (web fonts) are answered locally so the suite is offline and deterministic,
 * and console errors and page errors are collected.
 */
export const test = base.extend<{ errors: string[] }>({
  errors: async ({ page }, use) => {
    const errors: string[] = []
    await page.route(/^https?:\/\/(fonts\.googleapis\.com|fonts\.gstatic\.com)\//, (r) => r.fulfill({ status: 200, contentType: 'text/css', body: '' }))
    page.on('console', (m: ConsoleMessage) => {
      if (m.type() !== 'error') return
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

export type UploadedDataset = { id: string; filename: string; name?: string }

/** Upload a CSV through the API, the way Build does; the suite starts on an empty data folder. */
export async function uploadCsv(request: APIRequestContext, filename: string, csv: string): Promise<UploadedDataset> {
  const up = await request.post('/api/datasets', { multipart: { file: { name: filename, mimeType: 'text/csv', buffer: Buffer.from(csv) } } })
  expect(up.status(), await up.text()).toBe(201)
  return up.json()
}

/** A seeded generator, so every generated table is the same on every run. */
function seeded(seed: number) {
  let s = seed
  const u = () => { s = (s * 16807) % 2147483647; return s / 2147483647 }
  const gauss = () => Math.sqrt(-2 * Math.log(u() + 1e-12)) * Math.cos(2 * Math.PI * u())
  return { u, gauss }
}

/** Binary credit table: an id, numeric and categorical drivers, a region for fairness checks, defaulted Yes/No. */
export function creditCsv(n = 1200) {
  const { u, gauss } = seeded(7)
  const rows = ['loan_id,utilization,income,tenure_months,region,product,defaulted']
  for (let i = 0; i < n; i++) {
    const util = Math.min(1, Math.max(0, 0.45 + 0.22 * gauss()))
    const income = Math.round(52000 + 18000 * gauss())
    const tenure = Math.floor(u() * 120)
    const region = ['north', 'south', 'east', 'west'][i % 4]
    const product = ['card', 'personal', 'auto'][Math.floor(u() * 3)]
    const z = 3.2 * util - income / 60000 - tenure / 150 + (product === 'card' ? 0.4 : 0) + 0.6 * gauss() - 0.9
    rows.push(`L${100000 + i},${util.toFixed(4)},${income},${tenure},${region},${product},${z > 0 ? 'Yes' : 'No'}`)
  }
  return rows.join('\n') + '\n'
}

/** Regression table: a loan amount from income, term and purpose. */
export function loanAmountCsv(n = 900) {
  const { u, gauss } = seeded(13)
  const rows = ['application_id,income,term_months,purpose,credit_score,loan_amount_usd']
  for (let i = 0; i < n; i++) {
    const income = Math.round(48000 + 20000 * gauss())
    const term = [12, 24, 36, 60][Math.floor(u() * 4)]
    const purpose = ['home', 'car', 'debt', 'other'][Math.floor(u() * 4)]
    const score = Math.round(680 + 60 * gauss())
    const amount = Math.max(500, 0.18 * income + 90 * term + (purpose === 'home' ? 6000 : 0) + 12 * (score - 680) + 1500 * gauss())
    rows.push(`A${i},${income},${term},${purpose},${score},${amount.toFixed(0)}`)
  }
  return rows.join('\n') + '\n'
}

const TOPICS: Record<string, string[]> = {
  Billing: ['invoice', 'charged', 'refund', 'payment', 'billing'],
  Security: ['password', 'login', 'suspicious', 'locked', 'phishing'],
  Shipping: ['delivery', 'package', 'courier', 'tracking', 'shipped'],
  Technical: ['crash', 'error', 'bug', 'update', 'install'],
}

/** Multiclass text table: support tickets whose words carry the category. */
export function ticketsCsv(n = 800) {
  const { u } = seeded(29)
  const filler = ['please', 'help', 'with', 'my', 'account', 'today', 'thanks', 'again', 'the', 'issue']
  const cats = Object.keys(TOPICS)
  const rows = ['ticket_id,channel,message,category']
  for (let i = 0; i < n; i++) {
    const cat = cats[i % cats.length]
    const words = Array.from({ length: 10 }, () => (u() < 0.4 ? TOPICS[cat][Math.floor(u() * 5)] : filler[Math.floor(u() * filler.length)]))
    rows.push(`T${i},${['email', 'chat', 'phone'][Math.floor(u() * 3)]},"${words.join(' ')}",${cat}`)
  }
  return rows.join('\n') + '\n'
}

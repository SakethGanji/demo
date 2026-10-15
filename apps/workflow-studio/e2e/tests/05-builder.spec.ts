/**
 * From backend to UI (T20–T24).
 */
import type { Page } from '@playwright/test'
import { test, expect, type EngineApi } from '../fixtures/test'
import { seedProject, seedRefunds, type P } from '../fixtures/seed'

const UI_PROMPT = 'A refunds dashboard: show the refund count and a form to create a refund. '

async function createUi(page: Page, api: EngineApi, p: P, studioUrl: string, routes: string[], prompt = UI_PROMPT) {
  await page.goto(`${studioUrl}/p/${p.slug}?dialog=create-ui`)
  const dlg = page.getByTestId('create-ui-dialog')
  await expect(dlg).toBeVisible()
  for (const name of ['createRefund', 'listRefunds', 'refundStats']) {
    const box = dlg.getByTestId(`create-ui-route-check-${name}`)
    const want = routes.includes(name)
    const checked = (await box.getAttribute('data-checked')) !== null
    if (checked !== want) await box.click()
  }
  await dlg.getByTestId('create-ui-name').fill('Refunds dashboard')
  await dlg.getByTestId('create-ui-prompt').fill(prompt)
  const chat = page.waitForRequest((r) => r.url().endsWith('/api/ai/app-builder/chat') && r.method() === 'POST')
  await dlg.getByTestId('create-ui-submit').click()
  const req = await chat
  await expect(page).toHaveURL(/\/apps\/app_[^?]+$/)
  const appId = new URL(page.url()).pathname.split('/').pop()!
  return { appId, chatBody: req.postDataJSON() as { project_id: string; route_ids: string[]; message: string; app_id: string } }
}

async function waitForTurn(page: Page) {
  await expect(page.getByText('Built a refunds dashboard on your typed client.')).toBeVisible({ timeout: 60_000 })
}

test('T20 create UI from the backend', async ({ page, api, s }) => {
  const p = await seedProject(api, 'T20')
  const r = await seedRefunds(api, p)
  await api.clearLlmCalls()
  const long = UI_PROMPT + 'x'.repeat(1200)
  const { appId, chatBody } = await createUi(page, api, p, s.studioUrl, ['createRefund', 'listRefunds', 'refundStats'], long)
  const ids = [r.routeId('createRefund'), r.routeId('listRefunds'), r.routeId('refundStats')].sort()
  const app = await api.get<{ definition: { studio?: { route_ids: string[] } }; description?: string; project_id: string }>(`/apps/${appId}`)
  expect(app.project_id).toBe(p.id)
  expect([...(app.definition.studio?.route_ids ?? [])].sort()).toEqual(ids)
  expect(chatBody.project_id).toBe(p.id)
  expect([...chatBody.route_ids].sort()).toEqual(ids)
  expect(chatBody.message).toBe(long) // the full prompt travels in the chat
  expect((app.description ?? '').length).toBeLessThanOrEqual(1000)
  expect(app.description).toBe(long.slice(0, 1000))
  await waitForTurn(page)
  const calls = await api.llmCalls()
  expect(calls.map((c) => c.rule_id)).toEqual(['builder-write', 'builder-done'])
  const system = String(calls[0].messages.find((m) => m.role === 'system')?.content)
  for (const op of ['createRefund', 'listRefunds', 'refundStats']) expect(system).toMatch(new RegExp(`${op}.*← focus`))
})

test('T21 builder writes against the typed client', async ({ page, api, s, net }) => {
  const p = await seedProject(api, 'T21')
  await seedRefunds(api, p)
  await page.addInitScript(() => {
    const w = window as unknown as { __opStates: string[] }
    w.__opStates = []
    document.addEventListener('DOMContentLoaded', () => {
      new MutationObserver(() => {
        const el = document.querySelector('[data-testid="builder-op-refundStats"]')
        const st = el?.getAttribute('data-state')
        if (st && w.__opStates[w.__opStates.length - 1] !== st) w.__opStates.push(st)
      }).observe(document.body, { subtree: true, childList: true, attributes: true })
    })
  })
  const { appId } = await createUi(page, api, p, s.studioUrl, ['refundStats', 'createRefund'])
  await waitForTurn(page)
  await expect(page.getByText(/Wrote App\.tsx/)).toBeVisible()
  // ✓ only after the preview's observed 2xx
  await expect(page.getByTestId('builder-op-refundStats')).toHaveAttribute('data-state', 'ok', { timeout: 30_000 })
  const states = await page.evaluate(() => (window as unknown as { __opStates: string[] }).__opStates)
  expect(states.indexOf('ok')).toBeGreaterThan(0) // it was something else before
  const okAt = net.responses.findIndex((x) => x.status === 200 && x.url.includes(`/api/p/${p.slug}/refunds/stats`))
  expect(okAt).toBeGreaterThanOrEqual(0)
  const app = await api.get<{ files: Array<{ path: string }> }>(`/apps/${appId}`)
  expect(app.files.map((f) => f.path)).toContain('App.tsx')
  expect(app.files.map((f) => f.path)).not.toContain('api/client.ts')
  const usage = await api.get<Array<{ operation: string; status: string; file: string; line: number }>>(`/apps/${appId}/api-usage`)
  const stats = usage.find((u) => u.operation === 'refundStats')!
  expect(stats.status).toBe('ok')
  expect(stats.file).toBe('App.tsx')
  expect(stats.line).toBeGreaterThan(0)
})

test('T22 route picker selection is saved with the app', async ({ page, api, s }) => {
  const p = await seedProject(api, 'T22')
  const r = await seedRefunds(api, p)
  const { appId } = await createUi(page, api, p, s.studioUrl, ['createRefund', 'listRefunds', 'refundStats'])
  await waitForTurn(page)
  await page.getByTestId('builder-routes-chip').click()
  const picker = page.getByTestId('builder-route-picker')
  await picker.getByTestId('route-picker-row-listRefunds').click()
  const put = page.waitForResponse((x) => x.url().endsWith(`/api/apps/${appId}`) && x.request().method() === 'PUT')
  await picker.getByTestId('route-picker-done').click()
  expect((await put).status()).toBe(200)
  const two = [r.routeId('createRefund'), r.routeId('refundStats')].sort()
  const stored = () => api.get<{ definition: { studio: { route_ids: string[] } } }>(`/apps/${appId}`).then((a) => [...a.definition.studio.route_ids].sort())
  expect(await stored()).toEqual(two)
  // the next chat turn sends the two ids; its auto-save keeps them
  const chat = page.waitForRequest((x) => x.url().endsWith('/api/ai/app-builder/chat'))
  await page.getByTestId('chat-input').fill('Make the count bigger')
  await page.getByTestId('chat-send').click()
  expect([...(await chat).postDataJSON().route_ids].sort()).toEqual(two)
  await expect(page.getByText('Built a refunds dashboard on your typed client.')).toHaveCount(2, { timeout: 60_000 })
  expect(await stored()).toEqual(two)
  // a manual save keeps them, and they survive a reload
  await page.getByTestId('builder-save').click()
  await expect.poll(stored).toEqual(two)
  await page.reload()
  await page.getByTestId('builder-routes-chip').click()
  await expect(page.getByTestId('builder-route-picker').getByTestId('route-picker-row-listRefunds')).not.toHaveAttribute('data-checked', 'true')
  await expect(page.getByTestId('builder-route-picker').getByTestId('route-picker-row-refundStats')).toHaveAttribute('data-checked', 'true')
  expect(await stored()).toEqual(two)
})

test('T23 the preview calls the backend through the bridge', async ({ page, api, s }) => {
  const p = await seedProject(api, 'T23')
  await seedRefunds(api, p)
  const tokenRes = page.waitForResponse((x) => /\/apps\/[^/]+\/preview-token$/.test(x.url()) && x.status() === 201)
  const statsReq = page.waitForRequest((x) => x.url().includes(`/api/p/${p.slug}/refunds/stats`))
  await createUi(page, api, p, s.studioUrl, ['refundStats', 'createRefund'])
  const token = await (await tokenRes).json()
  expect(String(token.expires_at)).toMatch(/Z$/)
  await waitForTurn(page)
  const req = await statsReq
  expect(new URL(req.url()).origin).toBe(s.engineUrl)
  expect(req.headers()['authorization']).toMatch(/^Bearer .+/)
  const frame = page.frameLocator('[data-testid="preview-iframe"]')
  const count = async () => (await api.get<{ count: number }>(`/projects/${p.id}/tables/refunds/rows?count=true`)).count
  await expect(frame.getByTestId('refund-count')).toHaveText(String(await count()))
  const before = await count()
  await frame.getByTestId('refund-amount').fill('7.5')
  await frame.getByTestId('refund-submit').click()
  await expect.poll(count).toBe(before + 1)
  await expect(frame.getByTestId('refund-count')).toHaveText(String(before + 1))
})

test('T24 builder stream error', async ({ page, api, s }) => {
  const p = await seedProject(api, 'T24')
  await seedRefunds(api, p)
  await createUi(page, api, p, s.studioUrl, ['refundStats'])
  await waitForTurn(page)
  await api.clearLlmCalls()
  const res = page.waitForResponse((x) => x.url().endsWith('/api/ai/app-builder/chat'))
  await page.getByTestId('chat-input').fill('Break please __llm_fail__')
  await page.getByTestId('chat-send').click()
  const body = await (await res).text()
  await expect(page.getByTestId('builder-stream-error')).toBeVisible()
  await expect(page.getByTestId('builder-stream-retry')).toBeVisible()
  const events = [...body.matchAll(/^event: (\w+)\ndata: (.*)$/gm)].map((m) => ({ event: m[1], data: m[2] }))
  const last = events.slice(-2)
  expect(JSON.parse(last[0].data).type).toBe('error')
  expect(last[1].event).toBe('done')
  expect((await api.llmCalls()).map((c) => c.rule_id)).toEqual(['fail'])
})

/**
 * Generate → draft → test → save, with the fake LLM (T3–T7, T10, T14–T16, T18, T41, T42).
 */
import type { Page } from '@playwright/test'
import { test, expect, type EngineApi } from '../fixtures/test'
import { cursorLine, editorText, openModule, save, setSource } from '../fixtures/editor'
import { readSse, watchSse } from '../fixtures/sse'
import { fixture, refundsAppFile, seedApp, seedModule, seedProject, seedRefunds, seedSecret, seedTable, type P } from '../fixtures/seed'

const REFUNDS_PROMPT = 'Store refunds in a table. POST /refunds creates one, GET /refunds lists them, GET /refunds/stats totals them by reason __refunds__'

/** Records every phase chip / composer state that appears, however briefly. */
async function recordComposer(page: Page) {
  await page.evaluate(() => {
    const w = window as unknown as { __seen: Set<string> }
    w.__seen = new Set()
    const scan = () =>
      document.querySelectorAll('[data-testid^="phase-"], [data-testid="composer-streaming"]').forEach((el) => {
        w.__seen.add(el.getAttribute('data-testid')!)
        const ph = el.getAttribute('data-phase')
        if (ph) w.__seen.add(`status:${ph}`)
      })
    new MutationObserver(scan).observe(document.body, { subtree: true, childList: true, attributes: true })
  })
}
const seen = (page: Page) => page.evaluate(() => [...(window as unknown as { __seen: Set<string> }).__seen])

async function generate(page: Page, prompt: string) {
  await expect(page.getByTestId('composer')).toBeVisible()
  const input = page.getByTestId('composer-input')
  if (!(await input.isVisible())) await page.getByTestId('composer-submit').click()
  await input.fill(prompt)
  await input.press('Enter')
}

async function seedT3(api: EngineApi, test: string) {
  const p = await seedProject(api, test)
  const value = `sk_live_${Math.random().toString(36).slice(2)}${Math.random().toString(36).slice(2)}`
  await seedSecret(api, p, 'PAYMENTS_TOKEN', value, ['dev'], ['127.0.0.1'])
  await seedTable(api, p, 'orders', [{ name: 'region', type: 'string', required: true }])
  return { p, value }
}

async function generateRefunds(page: Page, api: EngineApi, p: P, s: { studioUrl: string }) {
  await page.goto(`${s.studioUrl}/p/${p.slug}/backend/~new`)
  await expect(page.getByTestId('composer')).toBeVisible()
  await recordComposer(page)
  await api.clearLlmCalls()
  await generate(page, REFUNDS_PROMPT)
  await expect(page.getByTestId('composer-result')).toBeVisible({ timeout: 60_000 })
  await expect(page).toHaveURL(new RegExp(`/p/${p.slug}/backend/refunds$`))
}

test('T3 generate a module', async ({ page, api, s }) => {
  const { p, value } = await seedT3(api, 'T3')
  await generateRefunds(page, api, p, s)
  // phases streamed
  const states = await seen(page)
  expect(states).toEqual(expect.arrayContaining(['composer-streaming', 'phase-context', 'phase-generating', 'phase-testing']))
  // the draft is in the editor, not saved
  await expect.poll(() => editorText(page)).toContain('def refund_stats(req):')
  await expect(page.getByTestId('composer-routes')).toContainText('3 routes')
  const st = page.getByTestId('composer-selftest')
  await expect(st).toHaveAttribute('data-passed', '2')
  await expect(st).toHaveAttribute('data-total', '2')
  await expect(page.locator('[data-testid="composer-tables"][data-table="refunds"]')).toContainText('Create table refunds on save')
  await expect(page.getByTestId('outline-draft-table-refunds')).toContainText('created on save')
  await expect(page.getByTestId('outline-new-route')).toHaveCount(3)
  // the generated draft took the module's name: no leftover "untitled" draft entry
  await expect(page.locator('[data-testid^="outline-module-"]')).toHaveCount(1)
  await expect(page.getByTestId('outline-module-refunds')).toContainText('draft')
  expect(await api.get<unknown[]>(`/projects/${p.id}/modules`)).toEqual([])
  expect((await api.get<Array<{ name: string }>>(`/projects/${p.id}/tables`)).map((t) => t.name)).not.toContain('refunds')
  // call log: the right rule, the real context and schema, no secret value anywhere
  const calls = await api.llmCalls()
  expect(calls.map((c) => c.rule_id)).toEqual(['gen-refunds'])
  const system = JSON.stringify(calls[0].messages.filter((m) => m.role === 'system'))
  expect(system).toContain('http_request')
  expect(system).toContain('PAYMENTS_TOKEN')
  expect(system).toContain('orders')
  expect(JSON.stringify(calls[0].tools)).toContain('emit_module')
  expect(JSON.stringify(calls[0].tools)).toContain('module_name')
  expect(JSON.stringify(calls)).not.toContain(value)
  // self-test executions are recorded as such
  const runs = await api.get<Array<{ trigger: { type: string } }>>(`/projects/${p.id}/runs?trigger=generate_self_test`)
  expect(runs.length).toBe(2)
  // The visual projection consumes the completed AI draft without saving it or calling the LLM again.
  await page.getByTestId('backend-view-visual').click()
  await expect(page.getByTestId('map-sync')).toContainText('Synced with current code')
  await expect(page.getByTestId('map-function-refund_stats')).toBeVisible()
  expect(await api.get<unknown[]>(`/projects/${p.id}/modules`)).toEqual([])
  expect((await api.llmCalls()).length).toBe(calls.length)
})

test('T4 secret values are scrubbed before the LLM', async ({ page, api, s }) => {
  const p = await seedProject(api, 'T4')
  const r = await seedRefunds(api, p)
  await openModule(page, s.studioUrl, p.slug, 'refunds')
  await setSource(page, `${fixture('modules/refunds.py')}\n# debug token: ${r.value}\n`)
  await api.clearLlmCalls()
  await generate(page, 'Keep everything as is __scrub__')
  await expect(page.getByTestId('composer-result')).toBeVisible({ timeout: 60_000 })
  await expect(page.getByTestId('warning-secret_scrubbed')).toBeVisible()
  const calls = await api.llmCalls()
  expect(calls.map((c) => c.rule_id)).toEqual(['gen-scrub'])
  const all = JSON.stringify(calls)
  expect(all).not.toContain(r.value)
  const current = calls[0].messages.find((m) => typeof m.content === 'string' && m.content.startsWith('Current module refunds.py:'))
  expect(current, 'the pinned Current module message').toBeTruthy()
  expect(String(current!.content)).toContain('***')
})

test('T5 draft test of GET /refunds/stats', async ({ page, api, s }) => {
  const { p, value } = await seedT3(api, 'T5')
  await generateRefunds(page, api, p, s)
  await fetch(`${s.upstreamUrl}/__reset`)
  await page.getByTestId('test-route-picker').click()
  await page.getByTestId('test-route-option-refundStats').click()
  await expect(page.getByTestId('test-note')).toContainText("refunds doesn't exist yet: reads return empty")
  await page.getByTestId('test-send').click()
  await expect(page.getByTestId('test-response').getByTestId('status-pill')).toHaveAttribute('data-status', '200')
  await expect(page.getByTestId('response-body')).toContainText('"count": 0')
  // the upstream received the real secret; the UI only ever shows ***
  const log = (await (await fetch(`${s.upstreamUrl}/__log`)).json()) as Array<{ path: string; headers: Record<string, string> }>
  expect(log.find((x) => x.path === '/v1/balance')?.headers.authorization).toBe(`Bearer ${value}`)
  // the trace shows the secret's NAME as the broker's placeholder, never its value
  await expect(page.getByTestId('trace')).toContainText('{{ $vars.PAYMENTS_TOKEN }}')
  expect(await page.evaluate(() => document.body.innerText)).not.toContain(value)
  const runs = await api.get<Array<{ trigger: { type: string; module_name?: string } }>>(`/projects/${p.id}/runs?trigger=draft_test`)
  expect(runs.length).toBe(1)
  expect(runs[0].trigger.module_name).toBe('refunds')
})

test('T6 draft write before save → Create table now', async ({ page, api, s }) => {
  const { p } = await seedT3(api, 'T6')
  await generateRefunds(page, api, p, s)
  await page.getByTestId('test-route-picker').click()
  await page.getByTestId('test-route-option-createRefund').click()
  await page.getByTestId('req-body').fill('{"reason":"damaged","amount":5}')
  await page.getByTestId('test-send').click()
  await expect(page.getByTestId('test-response').getByTestId('status-pill')).toHaveAttribute('data-status', '502')
  const card = page.getByTestId('table-not-found')
  await expect(card).toContainText("Table refunds doesn't exist yet")
  await card.getByTestId('create-table-now').click()
  await expect(card).toContainText('Table refunds created')
  const t = await api.get<Array<{ name: string; columns: Array<{ name: string }> }>>(`/projects/${p.id}/tables`)
  expect(t.find((x) => x.name === 'refunds')?.columns.map((c) => c.name)).toEqual(['reason', 'amount'])
  await page.getByTestId('test-send').click()
  await expect(page.getByTestId('test-response').getByTestId('status-pill')).toHaveAttribute('data-status', '200')
  const rows = await api.get<{ count: number }>(`${await api.tablePath(p.id, 'refunds')}/rows?count=true`)
  expect(rows.count).toBe(1)
})

test('T7 save creates the tables, then the module', async ({ page, api, s, net }) => {
  const { p } = await seedT3(api, 'T7')
  await generateRefunds(page, api, p, s)
  await expect(page.getByTestId('create-table-refunds')).toBeChecked()
  await save(page)
  await expect(page.getByText('Saved refunds.py · 3 routes created · table refunds created')).toBeVisible()
  // Live analysis also POSTs to /modules?dry_run=true; count persisted writes.
  const writes = net.responses.filter((r) => r.method === 'POST' && /\/(tables|modules)$/.test(new URL(r.url).pathname) && new URL(r.url).searchParams.get('dry_run') !== 'true')
  expect(writes.map((w) => new URL(w.url).pathname.split('/').pop())).toEqual(['tables', 'modules'])
  const routes = await api.get<Array<{ name: string; source: string; handler: { line: number } }>>(`/projects/${p.id}/routes`)
  expect(routes.map((r) => r.name).sort()).toEqual(['createRefund', 'listRefunds', 'refundStats'])
  expect(routes.every((r) => r.source === 'code')).toBe(true)
  // the outline settles: no draft badges, real routes
  await expect(page.getByTestId('outline-new-route')).toHaveCount(0)
  await expect(page.getByTestId('outline-route-refundStats')).toBeVisible()
  // client.ts has the operations
  await page.getByTestId('inspector-tab-client').click()
  for (const op of ['createRefund', 'listRefunds', 'refundStats']) await expect(page.getByTestId('client-ts-source')).toContainText(op)
  // "Open in editor" from a run lands on handler.line
  const create = routes.find((r) => r.name === 'createRefund')!
  await page.getByTestId('outline-route-createRefund').click()
  await page.getByTestId('inspector-tab-test').click()
  await page.getByTestId('req-tab-body').click()
  await page.getByTestId('req-body').fill('{"reason":"x","amount":1}')
  await page.getByTestId('test-send').click()
  await expect(page.getByTestId('test-response').getByTestId('status-pill')).toHaveAttribute('data-status', '200')
  await page.getByTestId('test-execution-link').click()
  await page.getByTestId('run-open-editor').click()
  await expect(page.getByTestId('backend-view')).toBeVisible()
  await expect.poll(() => cursorLine(page)).toBe(create.handler.line)
  // Overview lists the routes (the first-run guide gives way to it once all 4 steps are done: route tested + an app, T43)
  await seedApp(api, p, 'Refunds app', [refundsAppFile()], [routes.find((r) => r.name === 'refundStats')!.id])
  await page.reload()
  await page.getByTestId('outline-overview').click()
  for (const op of ['createRefund', 'listRefunds', 'refundStats']) await expect(page.getByTestId(`route-row-${op}`)).toBeVisible()
})

test('T10 missing-secret widget', async ({ page, api, s }) => {
  const p = await seedProject(api, 'T10')
  await seedRefunds(api, p)
  await openModule(page, s.studioUrl, p.slug, 'refunds')
  await api.clearLlmCalls()
  await generate(page, 'Notify the webhook on each refund __webhook__')
  await expect(page.getByTestId('composer-result')).toBeVisible({ timeout: 60_000 })
  expect((await api.llmCalls()).map((c) => c.rule_id)).toEqual(['gen-secret-missing'])
  await expect(page.getByTestId('composer-attempts')).toHaveCount(0) // attempts: 1
  const widget = page.getByTestId('secret-widget-REFUNDS_WEBHOOK_KEY')
  await expect(widget).toHaveAttribute('data-kind', 'missing_everywhere')
  await page.getByTestId('secret-widget-fix-REFUNDS_WEBHOOK_KEY').click()
  const pop = page.getByTestId('secret-fix-popover')
  await expect(pop.getByTestId('widget-secret-key')).toHaveValue('REFUNDS_WEBHOOK_KEY')
  await pop.getByTestId('widget-secret-value').fill('whk_e2e_value_123')
  await pop.getByTestId('widget-secret-save').click()
  await expect(widget).toHaveCount(0)
  const secrets = await api.get<Array<{ key: string; environment: string }>>(`/projects/${p.id}/secrets`)
  expect(secrets.filter((x) => x.key === 'REFUNDS_WEBHOOK_KEY').map((x) => x.environment).sort()).toEqual(['dev', 'prod'])
})

test('T14 repair loop', async ({ page, api, s }) => {
  const p = await seedProject(api, 'T14')
  await page.goto(`${s.studioUrl}/p/${p.slug}/backend/~new`)
  await expect(page.getByTestId('composer')).toBeVisible()
  await recordComposer(page)
  await api.clearLlmCalls()
  await generate(page, 'Make a module __broken__')
  await expect(page.getByTestId('composer-result')).toBeVisible({ timeout: 60_000 })
  expect(await seen(page)).toContain('phase-repairing') // the persistent chip (a transient status value can be coalesced)
  await expect(page.getByTestId('composer-attempts')).toHaveAttribute('data-attempts', '2')
  const calls = await api.llmCalls()
  expect(calls.map((c) => c.rule_id)).toEqual(['gen-broken', 'repair'])
  const last = calls[1].messages.filter((m) => m.role === 'user').pop()!
  expect(String(last.content).startsWith('Fix these problems in the module you emitted:')).toBe(true)
  expect(String(last.content)).toMatch(/line 7/)
  await expect.poll(() => editorText(page)).toContain('def create_item(req):')
})

test('T15 LLM failure, then a retried success', async ({ page, api, s }) => {
  const p = await seedProject(api, 'T15')
  await seedModule(api, p, 'hello', 'hello.py')
  await openModule(page, s.studioUrl, p.slug, 'hello')
  const before = await editorText(page)
  await api.clearLlmCalls()
  await watchSse(page, '/modules/generate')
  await generate(page, 'x __llm_fail__')
  await expect(page.getByTestId('composer-error')).toBeVisible()
  expect(await editorText(page)).toBe(before)
  const events = await readSse(page, '/modules/generate')
  const last2 = events.slice(-2)
  expect(last2[0][0]).toBe('message')
  expect(JSON.parse(last2[0][1]).type).toBe('error')
  expect(last2[1][0]).toBe('done')
  await page.getByTestId('composer-retry').click()
  await api.clearLlmCalls()
  await generate(page, 'Say hello __llm_flaky__')
  await expect(page.getByTestId('composer-result')).toBeVisible({ timeout: 60_000 })
  expect((await api.llmCalls()).map((c) => c.rule_id)).toEqual(['flaky', 'flaky', 'flaky-ok'])
})

test('T16 concurrent generation in one project', async ({ page, context, api, s, net }) => {
  const { p } = await seedT3(api, 'T16')
  const pageB = await context.newPage()
  await page.goto(`${s.studioUrl}/p/${p.slug}/backend/~new`)
  await pageB.goto(`${s.studioUrl}/p/${p.slug}/backend/~new`)
  await expect(page.getByTestId('composer')).toBeVisible()
  await expect(pageB.getByTestId('composer')).toBeVisible()
  await pageB.getByTestId('composer-input').fill(REFUNDS_PROMPT)
  await net.expect([{ method: 'POST', url: /\/modules\/generate$/, status: 409 }], async () => {
    await generate(page, REFUNDS_PROMPT)
    await expect(page.getByTestId('composer-streaming')).toBeVisible()
    await pageB.getByTestId('composer-input').press('Enter')
    await expect(pageB.getByTestId('composer-busy')).toContainText('Another generation for this project is running')
  })
  await expect(page.getByTestId('composer-result')).toBeVisible({ timeout: 60_000 })
  await pageB.close()
})

test('T18 traceback link goes to the line', async ({ page, api, s }) => {
  const p = await seedProject(api, 'T18')
  await seedModule(api, p, 'hello', 'hello.py')
  await openModule(page, s.studioUrl, p.slug, 'hello')
  const src = [
    'from studio import api',
    '',
    '',
    'def helper(x):',
    '    return x * 2',
    '',
    '',
    '@api.get("/hello", name="hello")',
    'def hello(req):',
    '    total = helper(21)',
    '    data = {"total": total}',
    '    raise ValueError("boom on purpose")',
    '    return data',
    '',
  ].join('\n')
  await setSource(page, src)
  await page.getByTestId('test-send').click()
  await expect(page.getByTestId('test-response').getByTestId('status-pill')).toHaveAttribute('data-status', '502') // run-draft: a failing handler is an inner 502 (§10.2)
  await expect(page.getByTestId('run-error-message')).toContainText('ValueError: boom on purpose')
  await page.getByTestId('go-to-line').click()
  await expect.poll(() => cursorLine(page)).toBe(12)
  const runs = await api.get<Array<{ id: string }>>(`/projects/${p.id}/runs?trigger=draft_test`)
  const detail = await api.get<{ metadata: { error?: { line: number } } }>(`/projects/${p.id}/runs/${runs[0].id}`)
  expect(detail.metadata.error?.line ?? 12).toBe(12)
})

test('T41 llm_unavailable', async ({ page, api, s, net }) => {
  const p = await seedProject(api, 'T41')
  await api.testApi('PUT', 'fake-llm/state', { unavailable: true })
  try {
    await page.goto(`${s.studioUrl}/p/${p.slug}/backend/~new`)
    await net.expect([{ method: 'POST', url: /\/modules\/generate$/, status: 503 }], async () => {
      await generate(page, 'anything __refunds__')
      await expect(page.getByTestId('composer-unavailable')).toContainText("AI isn't configured on this server")
    })
    // Write it myself still works
    await page.getByTestId('start-from-template').click()
    await page.getByTestId('template-menu-crud').click()
    await page.getByTestId('new-module-name').fill('items')
    await page.getByTestId('template-crud').click()
    await page.getByTestId('new-module-create').click()
    await expect.poll(() => editorText(page)).toContain('items = studio.db.table("items")')
    await save(page)
    await expect(page.getByText(/Saved items\.py/)).toBeVisible()
    expect((await api.get<Array<{ name: string }>>(`/projects/${p.id}/modules`)).map((m) => m.name)).toEqual(['items'])
  } finally {
    await api.testApi('PUT', 'fake-llm/state', { unavailable: false })
  }
})

test('T42 sandbox unavailable', async ({ page, api, s }) => {
  const p = await seedProject(api, 'T42')
  await seedModule(api, p, 'hello', 'hello.py')
  await api.testApi('PUT', 'sandbox', { available: false })
  try {
    await openModule(page, s.studioUrl, p.slug, 'hello')
    await expect(page.getByTestId('sandbox-badge')).toHaveAttribute('data-available', 'false')
    await expect(page.getByTestId('sandbox-unavailable')).toContainText("Python can't run on this server")
    await expect(page.getByTestId('test-send')).toBeDisabled()
    await setSource(page, fixture('modules/hello.py').replace('"world"', '"still saves"'))
    await save(page)
    await expect(page.getByText('Saved hello.py')).toBeVisible()
    const m = (await api.get<Array<{ id: string }>>(`/projects/${p.id}/modules`))[0]
    expect((await api.get<{ source: string }>(`/projects/${p.id}/modules/${m.id}`)).source).toContain('still saves')
  } finally {
    await api.testApi('PUT', 'sandbox', { available: true })
  }
})

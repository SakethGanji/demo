/**
 * Secrets, Data, Runs (T2, T19, T35, T37, T38).
 */
import { test, expect, rand } from '../fixtures/test'
import { dockMore } from '../fixtures/dock'
import { openModule } from '../fixtures/editor'
import { seedModule, seedModuleSource, seedProject, seedRows, seedSecret, seedTable } from '../fixtures/seed'

test('T2 setup step 1 creates a write-only secret', async ({ page, api, s, net }) => {
  net.captureBodies = true
  const p = await seedProject(api, 'T2')
  const value = `sk_live_${rand(10)}${rand(10)}`
  await page.goto(`${s.studioUrl}/p/${p.slug}`)
  await page.getByTestId('setup-secret-name').fill('PAYMENTS_TOKEN')
  await page.getByTestId('setup-secret-value').fill(value)
  await page.getByTestId('setup-secret-hosts-input').fill('127.0.0.1')
  await page.getByTestId('setup-secret-hosts-input').press('Enter')
  await expect(page.getByTestId('setup-secret-env-dev')).toBeChecked()
  await expect(page.getByTestId('setup-secret-env-prod')).toBeChecked()
  await page.getByTestId('setup-secret-save').click()
  await expect(page.getByTestId('setup-step-1-state')).toHaveAttribute('data-done', 'true')
  await dockMore(page, 'secrets')
  await expect(page.getByTestId('secret-cell-PAYMENTS_TOKEN-dev')).toHaveAttribute('data-state', 'set')
  await expect(page.getByTestId('secret-cell-PAYMENTS_TOKEN-prod')).toHaveAttribute('data-state', 'set')
  const rows = await api.get<Array<{ key: string; environment: string; value: unknown; allowed_hosts: string[] }>>(`/projects/${p.id}/secrets`)
  const mine = rows.filter((r) => r.key === 'PAYMENTS_TOKEN')
  expect(mine.map((r) => r.environment).sort()).toEqual(['dev', 'prod'])
  expect(mine.every((r) => r.value === null && r.allowed_hosts.includes('127.0.0.1'))).toBe(true)
  // the value is nowhere: not on the page, not in any API response the studio received
  expect(await page.evaluate(() => document.body.innerText)).not.toContain(value)
  expect(net.bodies.length).toBeGreaterThan(5)
  expect(net.bodies.filter((b) => b.text.includes(value)).map((b) => b.url)).toEqual([])
})

test('T19 data view', async ({ page, api, s, net }) => {
  const p = await seedProject(api, 'T19')
  await seedTable(api, p, 'refunds')
  await seedRows(api, p, 'refunds', [
    { reason: 'damaged', amount: 84.5 },
    { reason: 'late', amount: 12 },
  ])
  await page.goto(`${s.studioUrl}/p/${p.slug}/data/refunds`)
  await expect(page.getByTestId('data-row')).toHaveCount(2)
  await expect(page.getByTestId('data-panel')).toContainText('84.5')
  const count = async () => (await api.get<{ count: number }>(`${await api.tablePath(p.id, 'refunds')}/rows?count=true`)).count
  expect(await count()).toBe(2)
  // invalid row → 422 row_invalid, field error at the cell
  await page.getByTestId('data-add-row').click()
  await page.getByTestId('new-row-reason').fill('wrong size')
  await page.getByTestId('new-row-amount').fill('abc')
  await net.expect([{ method: 'POST', url: /\/tables\/[^/]+\/rows$/, status: 422 }], async () => {
    await page.getByTestId('new-row-save').click()
    await expect(page.getByTestId('row-invalid')).toBeVisible()
  })
  expect(await count()).toBe(2)
  await page.getByTestId('new-row-amount').fill('19.5')
  await page.getByTestId('new-row-save').click()
  await expect(page.getByTestId('data-row')).toHaveCount(3)
  expect(await count()).toBe(3)
  // a cell edit persists after reload
  const late = page.getByTestId('data-row').filter({ hasText: 'late' })
  const id = await late.getAttribute('data-row-id')
  await late.getByTestId('cell-reason').dblclick()
  await page.getByTestId('cell-editor').fill('very late')
  await page.getByTestId('cell-editor').press('Enter')
  await expect(late.getByTestId('cell-reason')).toHaveText('very late')
  await expect.poll(async () => (await api.get<{ data: { reason: string } }>(`${await api.tablePath(p.id, 'refunds')}/rows/${id}`)).data.reason).toBe('very late')
  await page.reload()
  await expect(page.locator(`[data-testid="data-row"][data-row-id="${id}"]`).getByTestId('cell-reason')).toHaveText('very late')
})

test('T35 environment switch is the viewing environment', async ({ page, api, s }) => {
  const p = await seedProject(api, 'T35')
  await seedSecret(api, p, 'PAYMENTS_TOKEN', `sk_${rand(16)}`, ['dev', 'prod'], ['127.0.0.1'])
  await seedTable(api, p, 'refunds')
  await seedModule(api, p, 'refunds', 'refunds.py')
  const routes = await api.get<Array<{ id: string; name: string }>>(`/projects/${p.id}/routes`)
  const stats = routes.find((r) => r.name === 'refundStats')!
  // prod runs via the router (live environment), a dev run via a route test
  for (let i = 0; i < 2; i++) {
    const r = await api.raw('GET', `/api/p/${p.slug}/refunds/stats`)
    expect(r.status).toBe(200)
  }
  await api.post(`/projects/${p.id}/routes/${stats.id}/test`, { environment: 'dev', params: {}, query: {}, headers: {}, body: null })
  await page.goto(`${s.studioUrl}/p/${p.slug}/runs`)
  await page.getByTestId('env-prod').click()
  await expect(page.getByTestId('run-row')).toHaveCount(2)
  for (const env of await page.getByTestId('run-row').evaluateAll((rs) => rs.map((r) => r.getAttribute('data-env')))) expect(env).toBe('prod')
  // Secrets highlights the prod column
  await dockMore(page, 'secrets')
  await expect(page.getByTestId('secrets-col-prod')).toHaveAttribute('data-viewing', 'true')
  // client.ts follows the viewing env, the route test still runs in dev
  await page.goto(`${s.studioUrl}/p/${p.slug}/backend/refunds?route=refundStats`)
  await page.getByTestId('inspector-tab-client').click()
  await expect(page.getByTestId('client-env')).toContainText('prod')
  await page.getByTestId('inspector-tab-test').click()
  const req = page.waitForRequest((r) => r.url().endsWith(`/routes/${stats.id}/test`))
  await page.getByTestId('test-send').click()
  expect((await req).postDataJSON().environment).toBe('dev')
  await expect(page.getByTestId('test-response').getByTestId('status-pill')).toHaveAttribute('data-status', '200')
})

test('T37 runs pagination', async ({ page, api, s }) => {
  const p = await seedProject(api, 'T37')
  await seedModule(api, p, 'hello', 'hello.py')
  for (let i = 0; i < 60; i++) expect((await api.raw('GET', `/api/p/${p.slug}/hello`)).status).toBe(200)
  await page.goto(`${s.studioUrl}/p/${p.slug}/runs`)
  await page.getByTestId('runs-env-all').click()
  await expect(page.getByTestId('run-row')).toHaveCount(50)
  await page.getByTestId('runs-load-more').click()
  await expect(page.getByTestId('run-row')).toHaveCount(60)
  const ids = await page.getByTestId('run-row').evaluateAll((rs) => rs.map((r) => r.getAttribute('data-run-id')))
  expect(new Set(ids).size).toBe(60)
  await expect(page.getByTestId('runs-load-more')).toHaveCount(0)
  // an unknown cursor is an empty page with no next cursor
  const r = await api.raw<unknown[]>('GET', `/api/projects/${p.id}/runs?limit=50&before=exec_does_not_exist`)
  expect(r.status).toBe(200)
  expect(r.body).toEqual([])
  expect(r.headers.get('x-next-cursor')).toBeNull()
})

test('T38 redaction everywhere', async ({ page, api, s, net }) => {
  net.captureBodies = true
  const p = await seedProject(api, 'T38')
  const value = `sk_leak_${rand(12)}`
  await seedSecret(api, p, 'PAYMENTS_TOKEN', value, ['dev', 'prod'], ['127.0.0.1'])
  const src = `from studio import api
import studio

UPSTREAM = "${s.upstreamUrl}"


@api.get("/leak", name="leak")
def leak(req):
    r = studio.nodes.http_request(
        method="GET",
        url=f"{UPSTREAM}/v1/leak",
        headers=[{"name": "Authorization", "value": f"Bearer {studio.secret('PAYMENTS_TOKEN')}"}],
    )
    return {"echo": r[0]["body"]["echo"], "upstream_headers": r[0]["headers"]}


@api.get("/leak-fail", name="leakFail")
def leak_fail(req):
    r = studio.nodes.http_request(
        method="GET",
        url=f"{UPSTREAM}/v1/leak",  # the JSON echo: the handler receives whatever the broker returns
        headers=[{"name": "Authorization", "value": f"Bearer {studio.secret('PAYMENTS_TOKEN')}"}],
    )
    raise ValueError(f"upstream said {r[0]['body']}")
`
  await seedModuleSource(api, p, 'leaks', src)
  // the router response itself is redacted (body + headers)
  const direct = await api.raw('GET', `/api/p/${p.slug}/leak`)
  expect(direct.text).not.toContain(value)
  expect(direct.text).toContain('***')
  expect(JSON.stringify([...direct.headers.entries()])).not.toContain(value)
  // UI route tests: body, headers, error message, trace
  await openModule(page, s.studioUrl, p.slug, 'leaks', '?route=leak')
  await page.getByTestId('test-send').click()
  await expect(page.getByTestId('test-response').getByTestId('status-pill')).toHaveAttribute('data-status', '200')
  await expect(page.getByTestId('response-body')).toContainText('***')
  await page.getByTestId('res-tab-headers').click()
  await page.getByTestId('res-tab-body').click()
  // a successful call's trace shows the secret's NAME (the broker placeholder), never the value
  await expect(page.getByTestId('trace')).toContainText('{{ $vars.PAYMENTS_TOKEN }}')
  await page.getByTestId('test-route-picker').click()
  await page.getByTestId('test-route-option-leakFail').click()
  await page.getByTestId('test-send').click()
  await expect(page.getByTestId('run-error-message')).toContainText('***')
  // run sheet
  await page.getByTestId('test-execution-link').click()
  const sheet = page.getByTestId('run-sheet')
  await expect(sheet.getByTestId('run-error')).toContainText('***')
  await expect(sheet.getByTestId('trace')).toContainText('{{ $vars.PAYMENTS_TOKEN }}') // the name, never the value
  expect(await page.evaluate(() => document.body.innerText)).not.toContain(value)
  expect(net.bodies.filter((b) => b.text.includes(value)).map((b) => b.url)).toEqual([])
  // backend copies (run detail, broker calls) are redacted too
  const runs = await api.get<Array<{ id: string }>>(`/projects/${p.id}/runs`)
  for (const r of runs) expect(JSON.stringify(await api.get(`/projects/${p.id}/runs/${r.id}`))).not.toContain(value)
})

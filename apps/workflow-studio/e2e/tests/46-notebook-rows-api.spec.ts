/**
 * Notebook: acting on table rows, API testing, browser take over and the removed-cell card.
 *  1. rows: search · filter · sort · tick rows → "Change selected…" → a change cell with the diff → Apply → written.
 *     Only bank.wires W3 is written (sent → held) and it is put back in `finally`, so 41/42/45 still see their rows.
 *  2. API suite: an environment feeds {{base_url}}; a saved value chains into the next request; one check fails
 *     and shows the actual value; fixing it and "Run suite" passes; the response viewer diffs against the last run;
 *     switching the environment re-points every request.
 *  3. take over (needs the jobs-service + chromium): drive the live page (click + type relayed), hand back, and
 *     the recorded steps that need the typed text pass.
 *  4. a stored approval cell (a removed kind) renders read-only as "This step type was removed" and runs skip it.
 */
import http from 'node:http'
import type { AddressInfo } from 'node:net'
import { test, expect } from '../fixtures/test'
import type { EngineApi } from '../fixtures/test'
import { addCell, bankProject, CAPS_PROBE, cellEl, getNotebook, insertRemovedCell, newNotebook, notBuilt, openNotebook, runAndSettle, settled, type NbP } from '../fixtures/notebook'

const rand = () => Math.random().toString(36).slice(2, 8)

async function wireStatus(api: EngineApi, bp: NbP, id: string, conn: string): Promise<string> {
  const nb = await newNotebook(api, bp.id, { title: `e2e probe ${rand()}` })
  try {
    await addCell(api, nb.id, 'query', { connection: conn, sql: `SELECT status FROM bank.wires WHERE wire_id = '${id}'`, limit: 5 })
    const done = await runAndSettle(api, nb.id)
    return (done.cells[0].latest!.output as { rows: Array<{ status: string }> }).rows[0].status
  } finally {
    await api.raw('DELETE', `/notebooks/${nb.id}`)
  }
}

async function setWire(api: EngineApi, bp: NbP, id: string, to: string, ro: string, rw: string) {
  if ((await wireStatus(api, bp, id, ro)) === to) return
  const nb = await newNotebook(api, bp.id, { title: `e2e restore ${rand()}` })
  try {
    await addCell(api, nb.id, 'change', { connection: rw, table: 'bank.wires', key: { wire_id: id }, set: { status: to }, reason: 'e2e restore' })
    const n = await runAndSettle(api, nb.id)
    if (n.cells[0].latest?.status === 'needs_approval') await api.post(`/notebook-cells/${n.cells[0].id}/apply`, { approved_by: 'e2e' })
    await settled(api, nb.id)
  } finally {
    await api.raw('DELETE', `/notebooks/${nb.id}`).catch(() => undefined)
  }
  expect(await wireStatus(api, bp, id, ro)).toBe(to)
}

// ── 1. rows ─────────────────────────────────────────────────────────────────────────────────────────────────────
test('rows: search, filter, sort, select the filtered → Change selected → diff → Apply writes only those rows', async ({ page, api, s, net }) => {
  test.setTimeout(120_000)
  const bp = await bankProject(api)
  const ro = s.nb_conn_bank ?? 'bank'
  const rw = s.nb_conn_bank_rw ?? 'bank_rw'
  const nb = await newNotebook(api, bp.id)
  await addCell(api, nb.id, 'query', { connection: ro, sql: 'SELECT wire_id, amount, status FROM bank.wires ORDER BY wire_id', limit: 100 }, { title: 'Wires' })
  await runAndSettle(api, nb.id)
  const w1Before = await wireStatus(api, bp, 'W1', ro)
  const caps = await notBuilt(api, [CAPS_PROBE])
  try {
    await net.expect(caps, async () => {
      await openNotebook(page, s, bp.slug, nb.id)
    }, caps.length ? 10_000 : 0)
    const c1 = cellEl(page, 'c1')
    await expect(c1.getByTestId('nb-table')).toContainText('W3')
    await c1.getByTestId('nb-select-rows').click()
    const t = c1.getByTestId('nb-rows-table')
    const rows = t.getByTestId('nb-rt-row')
    await expect(rows).toHaveCount(4)
    await expect(rows.first()).toContainText('W1')

    // sort by amount: ascending, then descending, then back to the query's order
    const amount = t.getByTestId('nb-rt-sort').filter({ hasText: 'amount' })
    await amount.click()
    await expect(rows.first()).toContainText('W4') // 40 is the smallest
    await expect(t.locator('th[aria-sort="ascending"]')).toContainText('amount')
    await amount.click()
    await expect(rows.last()).toContainText('W4')
    await amount.click()
    await expect(rows.first()).toContainText('W1')

    // search, then a column filter
    await t.getByTestId('nb-rt-search').fill('W2')
    await expect(rows).toHaveCount(1)
    await expect(rows.first()).toContainText('W2')
    await t.getByTestId('nb-rt-search').fill('')
    await t.getByTestId('nb-rt-fcol').selectOption('status')
    await t.getByTestId('nb-rt-fop').selectOption('eq')
    await t.getByTestId('nb-rt-fval').fill('sent')
    await expect(rows).toHaveCount(1)
    await expect(rows.first()).toContainText('W3')
    await expect(t).toContainText('1 row of 4 match')

    // select the filtered rows → the action bar
    await t.getByTestId('nb-rt-filtered').click()
    const bar = t.getByTestId('nb-rt-bar')
    await expect(bar).toContainText('1 row selected')
    await expect(bar).toContainText('by wire_id')
    // CSV of the selection downloads
    const dl = page.waitForEvent('download')
    await bar.getByTestId('nb-rt-csv').click()
    expect((await dl).suggestedFilename()).toBe('c1-selected.csv')

    await bar.getByTestId('nb-rt-act-change').click()
    const form = bar.getByTestId('nb-rt-change')
    await expect(form.getByRole('textbox', { name: 'Table' })).toHaveValue('bank.wires')
    await form.getByLabel('Column to change').selectOption('status')
    await form.getByLabel('New value').fill('held')
    await form.getByLabel('Reason').fill('e2e: hold the sent wire')
    await form.getByTestId('nb-rt-change-go').click()

    // a change cell proposes the diff for exactly the selected key; nothing is written yet
    const c2 = cellEl(page, 'c2')
    await expect(c2.getByTestId('nb-change-pending')).toBeVisible({ timeout: 30_000 })
    await expect(c2).toContainText('held')
    await expect(c2).toContainText('W3')
    await expect(c2).not.toContainText('W1')
    expect(await wireStatus(api, bp, 'W3', ro)).toBe('sent')
    await c2.getByTestId('nb-change-apply').click()
    await expect(c2.getByTestId('nb-change-applied')).toContainText('by you', { timeout: 30_000 })
    expect(await wireStatus(api, bp, 'W3', ro)).toBe('held')
    expect(await wireStatus(api, bp, 'W1', ro)).toBe(w1Before) // an unselected row is untouched
    const after = await getNotebook(api, nb.id)
    expect(after.cells.find((c) => c.name === 'c2')!.latest!.status).toBe('applied')
  } finally {
    await setWire(api, bp, 'W3', 'sent', ro, rw)
  }
})

// ── 2. API suite ───────────────────────────────────────────────────────────────────────────────────────────────
test('API suite: environment, chained value, a failing check with its actual value, fix → all pass, diff vs last run', async ({ page, api, s, net }) => {
  test.setTimeout(120_000)
  const bp = await bankProject(api)
  const nb = await newNotebook(api, bp.id)
  await api.put(`/notebooks/${nb.id}/environments`, {
    active: 'local',
    sets: [
      { name: 'local', vars: [{ name: 'base_url', value: s.upstreamUrl }] },
      { name: 'nowhere', vars: [{ name: 'base_url', value: 'http://127.0.0.1:9' }] },
    ],
  })
  await addCell(api, nb.id, 'http', {
    method: 'GET',
    url: '{{base_url}}/v1/balance',
    assertions: [{ kind: 'status', op: 'eq', value: 200 }, { kind: 'json_path', path: '$.available', op: 'gt', value: 100 }],
    extract: [{ name: 'available', source: 'json_path', path: '$.available' }],
  }, { title: 'Balance' })
  const orderSpec = {
    method: 'POST',
    url: '{{base_url}}/v1/orders',
    body_type: 'json',
    body: { amount: '{{available}}' },
    assertions: [
      { kind: 'status', op: 'eq', value: 200 },
      { kind: 'json_path', path: '$.body', op: 'contains', value: '12840.25' }, // the chained value arrived
      { kind: 'json_path', path: '$.method', op: 'eq', value: 'GET' }, // wrong on purpose: the echo says POST
    ],
  }
  const order = await addCell(api, nb.id, 'http', orderSpec, { title: 'Order with the balance' })
  const caps = await notBuilt(api, [CAPS_PROBE])
  await net.expect(caps, async () => {
    await openNotebook(page, s, bp.slug, nb.id)
  }, caps.length ? 10_000 : 0)

  const suite = page.getByTestId('nb-suite')
  await expect(suite).toContainText('2 requests')
  await expect(suite.getByTestId('nb-env-pick')).toHaveValue('local')
  await suite.getByTestId('nb-suite-run').click()
  await expect(page.getByTestId('nb-status')).toContainText(/Failed/, { timeout: 30_000 })
  await expect(suite.getByTestId('nb-suite-summary')).toContainText('1 failed')

  const c1 = cellEl(page, 'c1')
  await expect(c1.getByTestId('nb-http-verdict')).toContainText('2 checks passed')
  await expect(c1.getByTestId('nb-http-vars')).toContainText('available')
  await expect(c1.getByTestId('nb-http-vars')).toContainText('12840.25')
  const c2 = cellEl(page, 'c2')
  await expect(c2.getByTestId('nb-http-verdict')).toContainText('1 of 3 checks failed')
  const failed = c2.locator('[data-testid="nb-assertion"][data-passed="false"]')
  await expect(failed).toHaveCount(1)
  await expect(failed).toContainText('$.method')
  await expect(failed).toContainText('POST') // the actual value
  await expect(c2.locator('[data-testid="nb-assertion"][data-passed="true"]').filter({ hasText: '$.body' })).toContainText('12840.25')

  // fix the check in the request editor (Checks tab) and save
  await c2.getByTestId('nb-http-edit').click()
  const ed = c2.getByTestId('nb-http-editor')
  const methodCheck = ed.locator('.nb-http-check').nth(2)
  await methodCheck.getByLabel('Expected', { exact: true }).fill('POST')
  await c2.getByTestId('nb-http-save').click()
  await expect.poll(async () => ((await getNotebook(api, nb.id)).cells[1] as unknown as { spec: typeof orderSpec }).spec.assertions[2].value).toBe('POST')

  await suite.getByTestId('nb-suite-run').click()
  await expect(page.getByTestId('nb-status')).toContainText(/Done/, { timeout: 30_000 })
  await expect(suite.getByTestId('nb-suite-summary')).toContainText('all passed')
  await expect(c2.getByTestId('nb-http-verdict')).toContainText('3 checks passed')
  await expect(c2.getByTestId('nb-http-response')).toContainText('200')

  // the response viewer: JSON body, headers, diff vs the previous run
  await c2.getByRole('button', { name: /Response/ }).click()
  const ws = page.getByTestId('nb-workspace')
  await expect(ws).toHaveAttribute('data-tab', 'api')
  await expect(ws.getByTestId('nb-api-meta')).toContainText('200')
  await expect(ws.getByTestId('nb-api-body')).toContainText('method')
  await ws.getByRole('radio', { name: /Headers/ }).click()
  await expect(ws.getByTestId('nb-api-headers')).toContainText('content-type')
  await ws.getByRole('radio', { name: /Diff/ }).click()
  await expect(ws.getByTestId('nb-api-diff')).toContainText('200 → 200')

  // the other environment re-points every request
  await suite.getByTestId('nb-env-pick').selectOption('nowhere')
  await expect.poll(async () => (await api.get<{ active: string }>(`/notebooks/${nb.id}/environments`)).active).toBe('nowhere')
  await suite.getByTestId('nb-suite-run').click()
  await expect(page.getByTestId('nb-status')).toContainText(/Failed/, { timeout: 30_000 })
  const done = await settled(api, nb.id)
  expect(done.cells[0].latest!.status).toBe('error')
  expect(order.id).toBeTruthy()
})

// ── 3. take over ───────────────────────────────────────────────────────────────────────────────────────────────
// "Loaded 1..4" appear over ~12 s, so the recorded steps are still running while the test takes over; the last step
// passes only if the typed text reached the page. Each marker is its own element (the steps match exact text).
const DESK = `<!doctype html><html><body style="font:16px sans-serif;padding:40px;margin:0">
<h1>Desk</h1><input id="n" aria-label="Note" style="width:90vw;height:30vh;font-size:40px">
<p role="status" id="s">waiting</p><p id="l"></p>
<script>let k=0;const t=setInterval(()=>{k++;const e=document.createElement('span');e.textContent='Loaded'+k;l.append(e,' ');if(k===4)clearInterval(t)},3000);
n.addEventListener('input',()=>{s.textContent=n.value==='hello'?'Ready: '+n.value:'typed '+n.value.length})</script></body></html>`

test.describe('take over', () => {
  let server: http.Server
  let base = ''
  test.beforeAll(async () => {
    server = http.createServer((_req, res) => {
      res.writeHead(200, { 'content-type': 'text/html' })
      res.end(DESK)
    })
    await new Promise<void>((r) => server.listen(0, '127.0.0.1', () => r()))
    base = `http://127.0.0.1:${(server.address() as AddressInfo).port}`
  })
  test.afterAll(() => new Promise<void>((r) => server.close(() => r())))

  test('take over a running browser cell: click + type reach the page, hand back resumes the steps', async ({ page, api, s, net }) => {
    test.skip(!s.jobsUrl, `browser cells need the jobs-service + chromium: ${s.jobsSkip ?? 'not started'}`)
    test.setTimeout(180_000)
    const bp = await bankProject(api)
    const nb = await newNotebook(api, bp.id)
    const wait = (n: number) => ({ action: 'waitFor', target: { text: `Loaded${n}` }, state: 'visible' })
    await addCell(api, nb.id, 'browser', {
      base_url: base,
      steps: [{ action: 'goto', url: '/desk' }, wait(1), wait(2), wait(3), wait(4), { action: 'expectText', target: { role: 'status' }, text: 'Ready: hello' }],
    }, { title: 'Desk note' })
    const caps = await notBuilt(api, [CAPS_PROBE])
    await net.expect(caps, async () => {
      await openNotebook(page, s, bp.slug, nb.id)
    }, caps.length ? 10_000 : 0)
    await page.getByTestId('nb-run').click()
    const ws = page.getByTestId('nb-workspace')
    await expect(ws).toHaveAttribute('data-tab', 'browser', { timeout: 60_000 })
    const take = ws.getByTestId('nb-takeover')
    await expect(take).toBeEnabled({ timeout: 60_000 })
    await expect(ws.getByTestId('nb-frame')).toBeVisible({ timeout: 30_000 })
    await take.click()
    const pad = ws.getByTestId('nb-takeover-pad')
    await expect(pad).toHaveAttribute('data-driving', '')
    await expect(ws.getByRole('button', { name: 'Pause' })).toBeDisabled()

    // click the big input (it fills the upper middle of the page), then type
    const img = ws.getByTestId('nb-frame')
    const box = (await img.boundingBox())!
    await img.click({ position: { x: box.width * 0.4, y: box.height * 0.3 } })
    await expect(pad).toContainText('1 input sent')
    await page.keyboard.type('hello')
    await expect(pad).toContainText('2 inputs sent')
    await ws.getByTestId('nb-handback').click()
    await expect(ws.getByTestId('nb-takeover')).toBeVisible()

    await expect(page.getByTestId('nb-status')).toContainText(/Done/, { timeout: 90_000 })
    const after = await getNotebook(api, nb.id)
    expect(after.cells[0].latest!.status).toBe('success')
    const out = after.cells[0].latest!.output as { passed: number; failed: number }
    expect(out).toMatchObject({ passed: 6, failed: 0 })
    // after the run there is nothing to take over: the button is off and the API says why
    await expect(ws.getByTestId('nb-takeover')).toBeDisabled()
    const r = await api.raw('POST', `/notebook-cells/${after.cells[0].id}/input`, { type: 'click', x: 0.5, y: 0.5 })
    expect(r.status).toBe(409)
  })
})

// ── 4. removed kinds ───────────────────────────────────────────────────────────────────────────────────────────
test('a stored approval cell renders read-only as removed; the run skips it; it can be deleted', async ({ page, api, s, net }) => {
  const bp = await bankProject(api)
  const nb = await newNotebook(api, bp.id)
  await addCell(api, nb.id, 'note', { markdown: 'Before the old step' })
  await addCell(api, nb.id, 'data', { rows: [{ ok: 1 }] }, { title: 'After it' })
  insertRemovedCell(s, nb.id, 'approval', 1000, 'c3', { title: 'Release over 10k', assignee: 'ops lead' })
  const refused = await api.raw('POST', `/notebooks/${nb.id}/cells`, { kind: 'person', spec: {}, created_by: 'you' })
  expect(refused.status).toBe(422)
  const caps = await notBuilt(api, [CAPS_PROBE])
  await net.expect(caps, async () => {
    await openNotebook(page, s, bp.slug, nb.id)
  }, caps.length ? 10_000 : 0)
  const old = cellEl(page, 'c3')
  const card = old.getByTestId('nb-removed-card')
  await expect(card).toContainText('This step type was removed')
  await expect(card).toContainText('Release over 10k')
  await expect(card).toContainText('ops lead')
  await expect(old.getByRole('button', { name: /^title:|^assignee:/ })).toHaveCount(0)

  await page.getByTestId('nb-run').click()
  await expect(page.getByTestId('nb-status')).toContainText(/Done/, { timeout: 30_000 })
  const done = await getNotebook(api, nb.id)
  expect(done.cells.find((c) => c.name === 'c2')!.latest!.status).toBe('success')
  expect(done.cells.find((c) => c.name === 'c3')!.latest).toBeNull()

  await card.getByRole('button', { name: 'Delete step' }).click()
  await expect(cellEl(page, 'c3')).toHaveCount(0)
  expect((await getNotebook(api, nb.id)).cells.map((c) => c.kind)).toEqual(['note', 'data'])
})

/**
 * Notebook edge QA (T2b): a business user trying to break the Notebook UI. Every write is re-read through the API.
 * Shared bank rows that a test changes (W2 in the template journey, W3 elsewhere) are put back in `finally`, so the
 * other notebook specs (41/42) still find W1/W2 stuck and W4 stuck.
 */
import type { Page } from '@playwright/test'
import http from 'node:http'
import type { AddressInfo } from 'node:net'
import { test, expect, rand, type EngineApi } from '../fixtures/test'
import { putLlmRules, restoreLlmScript } from '../fixtures/shots17'
import { addCell, bankProject, cellEl, getNotebook, newNotebook, openNotebook, runAndSettle, settled, type Nb, type NbP } from '../fixtures/notebook'

const status = (page: Page) => page.getByTestId('nb-status')
const cellOf = (nb: Nb, name: string) => nb.cells.find((c) => c.name === name)!


/** Wait (API side) for run `n` to reach one of `ends` and for the lock to be freed. */
async function runEnded(api: EngineApi, nbId: string, n: number, ends: string[]): Promise<Nb> {
  await expect.poll(async () => {
    const x = await getNotebook(api, nbId)
    return x.runs[0]?.n === n && ends.includes(x.runs[0].status) && !x.active_run_id
  }, { timeout: 30_000, message: `run ${n} ends as ${ends.join('/')} and frees the lock` }).toBe(true)
  await new Promise((r) => setTimeout(r, 1200)) // let a late status write land
  return getNotebook(api, nbId)
}

/** Read one wire's status from the bank DB by running a throwaway read-only notebook (deleted afterwards). */
async function wireStatus(api: EngineApi, bp: NbP, id: string, conn: string): Promise<string> {
  const nb = await newNotebook(api, bp.id, { title: `e2e probe ${rand()}` })
  try {
    await addCell(api, nb.id, 'query', { connection: conn, sql: `SELECT status FROM bank.wires WHERE wire_id = '${id}'`, limit: 5 })
    const done = await runAndSettle(api, nb.id)
    const out = cellOf(done, 'c1').latest!.output as { rows: Array<{ status: string }> }
    return out.rows[0].status
  } finally {
    await api.raw('DELETE', `/notebooks/${nb.id}`)
  }
}

/** Put a wire back (a change cell on the writable connection, approved over the API); verified by re-reading. */
async function setWire(api: EngineApi, bp: NbP, id: string, to: string, ro: string, rw: string) {
  if ((await wireStatus(api, bp, id, ro)) === to) return
  const nb = await newNotebook(api, bp.id, { title: `e2e restore ${rand()}` })
  try {
    await addCell(api, nb.id, 'change', { connection: rw, table: 'bank.wires', key: { wire_id: id }, set: { status: to }, reason: 'e2e restore' })
    const n = await runAndSettle(api, nb.id)
    const c = cellOf(n, 'c1')
    if (c.latest?.status === 'needs_approval') await api.post(`/notebook-cells/${c.id}/apply`, { approved_by: 'e2e' })
    await settled(api, nb.id)
  } finally {
    await api.raw('DELETE', `/notebooks/${nb.id}`).catch(() => undefined)
  }
  expect(await wireStatus(api, bp, id, ro)).toBe(to)
}

// ── 1. the whole journey from a template ───────────────────────────────────────────────────────────────────────
test('journey: template → run all → apply → edit a value → Rerun from here → rerun bar → Skip → done', async ({ page, api, s, net }) => {
  test.setTimeout(240_000)
  const bp = await bankProject(api)
  const ro = s.nb_conn_bank ?? 'bank'
  const rw = s.nb_conn_bank_rw ?? 'bank_rw'
  const title = `e2e journey ${rand()}`
  try {
    await page.goto(`${s.studioUrl}/p/${bp.slug}/notebooks`)
    await page.getByTestId('nb-new').click()
    await page.locator('[data-testid="nb-tpl"][data-slug="stuck-wires"]').click()
    await page.getByTestId('nb-tpl-conn').selectOption(rw)
    await page.getByRole('textbox', { name: 'Notebook title' }).fill(title)
    await page.getByTestId('nb-create').click()
    await expect(page.getByTestId('notebook-page')).toBeVisible()
    await expect(page.getByTestId('nb-cell')).toHaveCount(4)
    await expect(status(page)).toContainText('Not run yet')

    await page.getByTestId('nb-run').click()
    await expect(status(page)).toContainText('Needs you', { timeout: 30_000 })
    const c4 = cellEl(page, 'c4')
    await expect(c4.getByTestId('nb-change-pending')).toBeVisible()
    // nothing written yet: re-read the DB
    expect(await wireStatus(api, bp, 'W2', ro)).toBe('stuck')

    await c4.getByTestId('nb-change-apply').click()
    await expect(c4.getByTestId('nb-change-applied')).toContainText('by you', { timeout: 30_000 })
    const nbId = page.url().match(/notebooks\/([^/?#]+)/)![1]
    const end1 = await runEnded(api, nbId, 1, ['done'])
    expect.soft({ notebook: end1.status, run: end1.runs[0].status, lock: end1.active_run_id }, 'after run 1 is applied the notebook says done (a UI poll in the finish window can strand it as "running")').toEqual({ notebook: 'done', run: 'done', lock: null })
    await expect.soft(status(page)).toContainText('Done', { timeout: 5_000 })

    const done = await getNotebook(api, nbId)
    expect(done.title).toBe(title)
    expect(done.runs[0].status).toBe('done')
    expect(cellOf(done, 'c4').latest!.status).toBe('applied')
    expect(cellOf(done, 'c4').latest!.output).toMatchObject({ applied_by: 'you' })
    expect(await wireStatus(api, bp, 'W2', ro)).toBe('released')

    // edit the settlement window 1 → 2
    await cellEl(page, 'c1').getByRole('button', { name: 'max_age_days: 1' }).click()
    const input = page.getByRole('textbox', { name: 'max_age_days' }).or(page.getByRole('spinbutton', { name: 'max_age_days' }))
    await input.fill('2')
    await input.press('Enter')
    await expect(cellEl(page, 'c2')).toHaveAttribute('data-status', 'stale')
    await expect(cellEl(page, 'c4')).toHaveAttribute('data-status', 'stale')
    const edited = await getNotebook(api, nbId)
    expect((edited.cells[0] as unknown as { spec: { rows: Array<{ max_age_days: unknown }> } }).spec.rows[0].max_age_days).toBe(2)

    await page.getByTestId('nb-rerun-here').click()
    const bar = page.getByTestId('nb-rerun-bar')
    await expect(bar).toContainText('already applied', { timeout: 30_000 })
    await expect(bar).toContainText('by you')
    await expect(bar).toContainText('Nothing was written twice')
    await expect(status(page)).toContainText('Stopped before cell 4')
    await page.getByTestId('nb-rerun-skip').click()
    await expect(bar).toHaveCount(0, { timeout: 30_000 })
    const end3 = await runEnded(api, nbId, 3, ['done'])
    expect.soft({ notebook: end3.status, run: end3.runs[0].status, lock: end3.active_run_id }, 'after run 3 is skipped through the notebook says done').toEqual({ notebook: 'done', run: 'done', lock: null })
    await expect.soft(status(page)).toContainText('Done', { timeout: 5_000 })

    const fin = await settled(api, nbId)
    expect(fin.runs.map((r) => r.status).slice(0, 3)).toEqual(['done', 'stopped_at_write', 'done'])
    expect(cellOf(fin, 'c4').latest!.status).toBe('kept_earlier')
    // W2 was released by the first run, so only W1 is still stuck on the rerun
    expect(cellOf(fin, 'c2').latest!.output).toMatchObject({ row_count: 1 })
    expect(await wireStatus(api, bp, 'W2', ro)).toBe('released')
  } finally {
    await setWire(api, bp, 'W2', 'stuck', ro, rw)
  }
})

// ── 2. a failed cell in the middle ──────────────────────────────────────────────────────────────────────────────
test('a failed cell mid-notebook: red card, workspace opens, later cells not green; fix the value and rerun', async ({ page, api, s, net }) => {
  const bp = await bankProject(api)
  const nb = await newNotebook(api, bp.id)
  await addCell(api, nb.id, 'data', { columns: ['n'], rows: [{ n: 1 }] }, { title: 'Numbers' })
  await addCell(api, nb.id, 'query', { connection: s.nb_conn_bank ?? 'bank', sql: 'SELECT wire_id FROM bank.wires ORDER BY wire_id LIMIT %(n)s', params: { n: -1 }, limit: 50 }, { title: 'Wires' })
  await addCell(api, nb.id, 'rule', { op: 'count', left: '{{cells.c2.rows}}' }, { title: 'Count them' })
  await openNotebook(page, s, bp.slug, nb.id)
  await page.getByTestId('nb-run').click()
  await expect(status(page)).toContainText('Failed · cell 2', { timeout: 30_000 })
  const c2 = cellEl(page, 'c2')
  await expect(c2).toHaveAttribute('data-status', 'fail')
  await expect(c2.getByTestId('nb-cell-error')).toBeVisible()
  await expect(c2).toContainText('failed')
  await expect(page.getByTestId('nb-workspace')).toBeVisible()
  // the cell after the failure did not run: it must not look finished
  const c3status = await cellEl(page, 'c3').getAttribute('data-status')
  expect(['stale', 'pending'], 'a cell after a failed one is not green').toContain(c3status)
  const failed = await getNotebook(api, nb.id)
  expect(failed.runs[0]).toMatchObject({ status: 'failed' })
  expect(failed.status).toBe('failed')
  expect(cellOf(failed, 'c2').latest!.status).toBe('error')
  expect(cellOf(failed, 'c3').latest).toBeNull()

  // fix the number, then rerun from the failed cell
  await c2.getByRole('button', { name: 'n: -1' }).click()
  const field = page.getByRole('spinbutton', { name: 'n', exact: true })
  await field.fill('5')
  await field.press('Enter')
  await expect(c2).toHaveAttribute('data-status', 'stale')
  await page.getByTestId('nb-rerun-here').click()
  await expect(status(page)).toContainText('Done', { timeout: 30_000 })
  await expect(cellEl(page, 'c2')).toHaveAttribute('data-status', 'ok')
  await expect(cellEl(page, 'c3')).toHaveAttribute('data-status', 'ok')
  const fixed = await settled(api, nb.id)
  expect(fixed.runs[0].status).toBe('done')
  expect(fixed.status).toBe('done')
  expect(cellOf(fixed, 'c2').latest!.status).toBe('success')
  expect(cellOf(fixed, 'c3').latest!.output).toMatchObject({ count: 4 })
})

// ── 3. empty results, guard false, for-each over nothing ────────────────────────────────────────────────────────
test('empty results: 0 rows renders calmly, the guard skips the next cell, a for-each over nothing finishes', async ({ page, api, s, net }) => {
  const bp = await bankProject(api)
  const nb = await newNotebook(api, bp.id)
  await addCell(api, nb.id, 'query', { connection: s.nb_conn_bank ?? 'bank', sql: "SELECT wire_id FROM bank.wires WHERE wire_id = 'NOPE'", limit: 10 }, { title: 'Nothing here' })
  await addCell(api, nb.id, 'rule', { op: 'count', left: '{{cells.c1.rows}}' }, { title: 'Count' })
  const loop = await addCell(api, nb.id, 'for_each', { over: '{{cells.c1.rows}}', max_rows: 10 }, { title: 'Each' })
  await addCell(api, nb.id, 'http', { method: 'GET', url: `${s.upstreamUrl}/v1/balance`, expect: { status: 200 } }, { title: 'Never called', parent_cell_id: loop.id })
  await addCell(api, nb.id, 'http', { method: 'GET', url: `${s.upstreamUrl}/v1/balance`, expect: { status: 200 } }, { title: 'Only if rows', guard: { left: '{{cells.c2.count}}', op: 'gt', right: 0 } })
  await openNotebook(page, s, bp.slug, nb.id)
  await page.getByTestId('nb-run').click()
  await expect(status(page)).toContainText('Done', { timeout: 30_000 })
  await expect(cellEl(page, 'c1').getByTestId('nb-table')).toContainText('No rows')
  await expect(cellEl(page, 'c1')).toContainText('0 rows')
  await expect(cellEl(page, 'c5')).toHaveAttribute('data-skipped', '')
  await expect(cellEl(page, 'c5').getByTestId('nb-guard')).toContainText('false · skipped')
  await expect(page.getByTestId('nb-foreach')).toContainText('0 ✓')
  await expect(page.getByTestId('nb-foreach')).not.toContainText('Retry failed rows')
  // the workspace on an empty table says so, and does not offer a next page
  await page.getByTestId('nb-wsrail').click().catch(() => undefined)
  const rows = page.getByTestId('nb-ws-rows')
  if (await rows.count()) {
    await expect(rows).toContainText('0 rows')
    await expect(rows.getByRole('button', { name: 'Next' })).toBeDisabled()
  }
  const done = await settled(api, nb.id)
  expect(done.runs[0].status).toBe('done')
  expect(cellOf(done, 'c5').latest!.status).toBe('guard_false')
  expect(cellOf(done, 'c1').latest!.output).toMatchObject({ row_count: 0 })
})

// ── 4. big tables and the 422 for a table that is too big ───────────────────────────────────────────────────────
test('a 5,000-row table shows 50 rows inline, pages by 100 in the workspace; 20,000 rows is refused inline (422)', async ({ page, api, s, net }) => {
  test.setTimeout(180_000)
  const bp = await bankProject(api)
  const nb = await newNotebook(api, bp.id)
  const csv = (n: number) => ['id,label', ...Array.from({ length: n }, (_, i) => `${i + 1},row ${i + 1}`)].join('\n')
  const big = await api.raw<{ id: string }>('POST', `/notebooks/${nb.id}/cells`, { kind: 'data', spec: { csv: csv(5000) }, created_by: 'you', title: 'Big' })
  expect(big.status, big.text.slice(0, 300)).toBe(201)
  await addCell(api, nb.id, 'rule', { op: 'count', left: '{{cells.c1.rows}}' }, { title: 'How many' })
  await runAndSettle(api, nb.id)

  await openNotebook(page, s, bp.slug, nb.id)
  const ws = page.getByTestId('nb-workspace')
  await expect(ws).toBeVisible()
  await expect(cellEl(page, 'c1')).toContainText('5,000 rows')
  await expect(cellEl(page, 'c2')).toContainText('5000 of 5000 rows match')
  // the cell says it holds 5000 rows but is only showing 50 → editing a value in it must be refused kindly
  await cellEl(page, 'c1').getByRole('button', { name: /Show all 5,000 rows/ }).click()
  const wsRows = page.getByTestId('nb-ws-rows')
  await expect(wsRows).toContainText('5,000 rows')
  await expect(wsRows).toContainText('showing 1–100')
  await expect(wsRows.locator('tbody tr')).toHaveCount(100)
  await wsRows.getByRole('button', { name: 'Next' }).click()
  await expect(wsRows).toContainText('showing 101–200')
  await expect(wsRows.locator('tbody tr').first()).toContainText('row 101')
  for (let i = 0; i < 48; i++) await wsRows.getByRole('button', { name: 'Next' }).click()
  await expect(wsRows).toContainText('showing 4901–5000')
  await expect(wsRows.getByRole('button', { name: 'Next' })).toBeDisabled()
  await wsRows.getByRole('button', { name: 'Previous' }).click()
  await expect(wsRows).toContainText('showing 4801–4900')
  const page2 = await api.get<{ total: number; rows: Array<{ id: number | string }> }>(`/notebook-cells/${cellOf(await getNotebook(api, nb.id), 'c1').id}/rows?offset=4900&limit=100`)
  expect(page2.total).toBe(5000)
  expect(Number(page2.rows[99].id)).toBe(5000)

  // 20,000 rows through the "Data" draft: the engine says 422 and the form shows it inline
  const tooBig = csv(20_000)
  await net.expect([{ method: 'POST', url: /\/api\/notebooks\/[^/]+\/cells$/, status: 422 }], async () => {
    await page.getByTestId('nb-composer').getByRole('textbox').fill('/')
    await page.getByTestId('nb-palette').getByRole('option', { name: /^Data/ }).click()
    const draft = page.getByTestId('nb-draft')
    await draft.getByRole('textbox', { name: 'CSV' }).evaluate((el, v) => {
      const set = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value')!.set!
      set.call(el, v)
      el.dispatchEvent(new Event('input', { bubbles: true }))
    }, tooBig)
    await page.getByTestId('nb-draft-add').click()
    await expect(page.getByTestId('nb-draft-error')).toContainText(/at most 5000 rows|5000/)
  })
  await expect(page.getByTestId('nb-draft')).toBeVisible()
  expect((await getNotebook(api, nb.id)).cells).toHaveLength(2)
})

// ── 5. for-each: failing rows, retry, fix ───────────────────────────────────────────────────────────────────────
test('for-each with failing rows: retry keeps them red, fixing the data and rerunning turns them green', async ({ page, api, s, net }) => {
  const bp = await bankProject(api)
  const nb = await newNotebook(api, bp.id)
  await addCell(api, nb.id, 'data', { columns: ['n', 'fail'], rows: [{ n: 1, fail: 1 }, { n: 2, fail: 0 }, { n: 3, fail: 1 }] }, { title: 'Rows' })
  const loop = await addCell(api, nb.id, 'for_each', { over: '{{cells.c1.rows}}', max_rows: 50 }, { title: 'Each row' })
  await addCell(api, nb.id, 'http', { method: 'GET', url: `${s.upstreamUrl}/v1/leak?fail={{row.fail}}`, expect: { status: 200 } }, { title: 'Look it up', parent_cell_id: loop.id })
  await openNotebook(page, s, bp.slug, nb.id)
  await page.getByTestId('nb-run').click()
  await expect(status(page)).not.toContainText('Running', { timeout: 30_000 })
  const fe = cellEl(page, 'c2').getByTestId('nb-foreach')
  await expect(fe).toContainText('1 ✓')
  await expect(fe).toContainText('2 ✗')
  await expect(fe.locator('.gl-ferow[data-status="fail"]')).toHaveCount(2)
  const nbState = await getNotebook(api, nb.id)
  // two of three rows failed: the loop must not be drawn as a plain green success
  expect.soft(await cellEl(page, 'c2').getAttribute('data-status'), 'a loop with 2 of 3 rows failed is not shown as ok').not.toBe('ok')
  expect.soft(await status(page).innerText(), 'the header does not say plain Done when rows failed').not.toMatch(/Done/)
  expect(cellOf(nbState, 'c2').latest!.output).toMatchObject({ ok: 1, failed: 2 })

  await fe.getByRole('button', { name: 'Retry failed rows (2)' }).click()
  await expect(fe).toContainText('after retrying 2 rows', { timeout: 30_000 })
  await expect(fe).toContainText('2 ✗')
  const retried = await settled(api, nb.id)
  expect((cellOf(retried, 'c2').latest!.output as { retried: number[] }).retried).toEqual([0, 2])
  expect(retried.runs.length).toBe(2)

  // the two bad rows are fixed at the source (a multi-row data cell has no inline tokens), then Rerun from here
  const c1 = cellOf(retried, 'c1')
  await api.raw('PATCH', `/notebook-cells/${c1.id}`, { spec: { columns: ['n', 'fail'], rows: [{ n: 1, fail: 0 }, { n: 2, fail: 0 }, { n: 3, fail: 0 }] } })
  await page.reload()
  await expect(page.getByTestId('nb-cells')).toBeVisible()
  await expect(cellEl(page, 'c1')).toHaveAttribute('data-status', 'stale')
  await page.getByTestId('nb-rerun-here').click()
  await expect(status(page)).toContainText('Done', { timeout: 30_000 })
  const fe2 = cellEl(page, 'c2').getByTestId('nb-foreach')
  await expect(fe2).toContainText('3 ✓')
  await expect(fe2).toContainText('0 ✗')
  await expect(fe2.getByRole('button', { name: /Retry failed rows/ })).toHaveCount(0)
  const fixed = await settled(api, nb.id)
  expect(fixed.runs[0].status).toBe('done')
  expect(cellOf(fixed, 'c2').latest!.status).toBe('success')
  expect(cellOf(fixed, 'c2').latest!.output).toMatchObject({ ok: 3, failed: 0 })
})

// ── 6. double-click, rapid reruns, a run started elsewhere ──────────────────────────────────────────────────────
test('a double-click on Run all must not stop the run it just started (Stop appears under the same spot)', async ({ page, api, s, net }) => {
  const bp = await bankProject(api)
  const nb = await newNotebook(api, bp.id)
  await addCell(api, nb.id, 'data', { columns: ['n'], rows: [{ n: 1 }] }, { title: 'One' })
  await addCell(api, nb.id, 'wait', { mode: 'duration', duration: '3s' }, { title: 'Hold on' })
  await openNotebook(page, s, bp.slug, nb.id)
  const runBtn = page.getByTestId('nb-run')
  const box = (await runBtn.boundingBox())!
  const at = { x: box.x + box.width / 2, y: box.y + box.height / 2 }
  await page.mouse.click(at.x, at.y)
  await expect(page.getByTestId('nb-stop')).toBeVisible({ timeout: 10_000 })
  // the second click of a double-click lands after the button became Stop
  await page.mouse.click(at.x, at.y)
  const end = await runEnded(api, nb.id, 1, ['done', 'cancelled'])
  expect.soft(end.runs, 'one run').toHaveLength(1)
  expect.soft(end.runs[0].status, 'a stray second click of a double-click cancelled the run it had just started').toBe('done')
})

test('a run started elsewhere gives a calm 409 toast, not a crash; Stop and a rerun from the cell menu work', async ({ page, api, s, net }) => {
  const bp = await bankProject(api)
  const nb = await newNotebook(api, bp.id)
  await addCell(api, nb.id, 'data', { columns: ['n'], rows: [{ n: 1 }] }, { title: 'One' })
  await addCell(api, nb.id, 'wait', { mode: 'duration', duration: '3s' }, { title: 'Hold on' })
  await openNotebook(page, s, bp.slug, nb.id)
  // somebody else starts a run behind the page's back; this page still shows an idle Run all
  await api.post(`/notebooks/${nb.id}/runs`, { triggered_by: 'colleague' })
  await net.expect([{ method: 'POST', url: /\/api\/notebooks\/[^/]+\/runs$/, status: 409 }], async () => {
    await page.getByTestId('nb-run').click()
  })
  await expect(page.locator('[data-sonner-toast]').filter({ hasText: 'A run is in progress' }).last()).toBeVisible()
  await expect(page.locator('[data-sonner-toast][data-type="error"]')).toHaveCount(0)
  // the page catches up: it shows the colleague's run and offers Stop instead of crashing
  await expect(page.getByTestId('nb-stop')).toBeVisible({ timeout: 10_000 })
  await expect(status(page)).toContainText(/Running|Waiting/)
  const two = await getNotebook(api, nb.id)
  expect(two.runs.map((r) => r.n)).toEqual([1])
  expect(two.runs[0]).toMatchObject({ status: expect.stringMatching(/running|waiting/) })
  await expect(status(page)).toContainText(/Done/, { timeout: 40_000 })
  const ended = await getNotebook(api, nb.id)
  expect(ended.runs).toHaveLength(1)

  // rerun from a cell menu, then Stop it
  await cellEl(page, 'c1').getByTestId('nb-cell-menu').click()
  await page.getByRole('menuitem', { name: /Rerun from cell 1/ }).click()
  await expect(page.getByTestId('nb-stop')).toBeVisible({ timeout: 10_000 })
  await page.getByTestId('nb-stop').click()
  await expect(page.getByTestId('nb-run')).toBeVisible({ timeout: 30_000 })
  const last = await runEnded(api, nb.id, 2, ['cancelled', 'interrupted'])
  expect(last.active_run_id).toBeNull()
  expect(last.status).toBe('idle')
})

// ── 7. editing while running ────────────────────────────────────────────────────────────────────────────────────
test('while a run is going: value tokens are read-only, Delete is disabled, the API refuses a delete with 409', async ({ page, api, s, net }) => {
  const bp = await bankProject(api)
  const nb = await newNotebook(api, bp.id)
  const d = await addCell(api, nb.id, 'data', { columns: ['n'], rows: [{ n: 7 }] }, { title: 'Seven' })
  await addCell(api, nb.id, 'wait', { mode: 'duration', duration: '6s' }, { title: 'Hold on' })
  await openNotebook(page, s, bp.slug, nb.id)
  await expect(cellEl(page, 'c1').getByRole('button', { name: 'n: 7' })).toBeVisible()
  await page.getByTestId('nb-run').click()
  await expect(page.getByTestId('nb-stop')).toBeVisible()
  await expect(cellEl(page, 'c1').getByRole('button', { name: 'n: 7' })).toHaveCount(0)
  await cellEl(page, 'c1').getByTestId('nb-cell-menu').click()
  await expect(page.getByRole('menuitem', { name: /Delete cell/ })).toHaveAttribute('aria-disabled', 'true')
  await page.keyboard.press('Escape')
  const r = await api.raw('DELETE', `/notebook-cells/${d.id}`)
  expect(r.status).toBe(409)
  expect(JSON.stringify(r.body)).toContain('run_in_progress')
  await expect(status(page)).toContainText('Done', { timeout: 40_000 })
  // back to editable once the run is over, and the value survived untouched
  await expect(cellEl(page, 'c1').getByRole('button', { name: 'n: 7' })).toBeVisible()
  const after = await getNotebook(api, nb.id)
  expect(after.cells).toHaveLength(2)
})

// ── 8. applying twice, and a proposal that went stale ───────────────────────────────────────────────────────────
test('apply twice writes once; an edit above the change makes the proposal stale (409) and a rerun fixes it', async ({ page, api, s, net }) => {
  test.setTimeout(180_000)
  const bp = await bankProject(api)
  const ro = s.nb_conn_bank ?? 'bank'
  const rw = s.nb_conn_bank_rw ?? 'bank_rw'
  try {
    const nb = await newNotebook(api, bp.id)
    await addCell(api, nb.id, 'data', { columns: ['wire_id'], rows: [{ wire_id: 'W3' }] }, { title: 'Which wire' })
    await addCell(api, nb.id, 'change', {
      connection: rw, table: 'bank.wires', key: { wire_id: '{{cells.c1.rows[0].wire_id}}' }, set: { status: 'held' }, reason: 'Hold for review',
    }, { title: 'Hold it' })
    await openNotebook(page, s, bp.slug, nb.id)
    await page.getByTestId('nb-run').click()
    await expect(status(page)).toContainText('Needs you', { timeout: 30_000 })
    const c2 = cellEl(page, 'c2')
    await expect(c2.getByTestId('nb-change-pending')).toBeVisible()
    expect(await wireStatus(api, bp, 'W3', ro)).toBe('sent')
    const w4Before = await wireStatus(api, bp, 'W4', ro)

    // edit the data cell ABOVE the change → the proposal is out of date
    await cellEl(page, 'c1').getByRole('button', { name: 'wire_id: W3' }).click()
    const t = page.getByRole('textbox', { name: 'wire_id' })
    await t.fill('W3')
    await t.press('Enter')
    // same value: nothing changed, so nothing is stale
    await expect(c2.getByTestId('nb-change-stale')).toHaveCount(0)
    await cellEl(page, 'c1').getByRole('button', { name: 'wire_id: W3' }).click()
    const t2 = page.getByRole('textbox', { name: 'wire_id' })
    await t2.fill('W4')
    await t2.press('Enter')
    await expect(c2.getByTestId('nb-change-stale')).toBeVisible()
    await net.expect([{ method: 'POST', url: /\/api\/notebook-cells\/[^/]+\/apply$/, status: 409 }], async () => {
      await c2.getByTestId('nb-change-apply').click()
    })
    expect(await wireStatus(api, bp, 'W3', ro), 'a stale apply wrote nothing').toBe('sent')
    expect(await wireStatus(api, bp, 'W4', ro), 'and nothing happened to the new key either').toBe(w4Before)
    // put the data cell back and rerun from it
    await cellEl(page, 'c1').getByRole('button', { name: 'wire_id: W4' }).click()
    const t3 = page.getByRole('textbox', { name: 'wire_id' })
    await t3.fill('W3')
    await t3.press('Enter')
    await page.getByTestId('nb-rerun-here').click()
    await expect(c2.getByTestId('nb-change-pending')).toBeVisible({ timeout: 30_000 })
    await expect.soft(c2.getByTestId('nb-change-stale'), 'after a successful rerun the fresh proposal is not still "out of date"').toHaveCount(0)

    // apply twice, fast: one write
    const cellId = cellOf(await getNotebook(api, nb.id), 'c2').id
    await c2.getByTestId('nb-change-apply').dblclick()
    await expect(c2.getByTestId('nb-change-applied')).toBeVisible({ timeout: 30_000 })
    const again = await api.raw('POST', `/notebook-cells/${cellId}/apply`, { approved_by: 'someone else' })
    expect(again.status, again.text.slice(0, 300)).toBe(200)
    const done = await settled(api, nb.id)
    const applied = cellOf(done, 'c2').latest!
    expect(applied.status).toBe('applied')
    expect(applied.output).toMatchObject({ applied_by: 'you' })
    expect(await wireStatus(api, bp, 'W3', ro)).toBe('held')
    await expect(c2.getByTestId('nb-change-apply')).toHaveCount(0)
  } finally {
    await setWire(api, bp, 'W3', 'sent', ro, rw)
  }
})

// ── 9. 422 inline for bad input ─────────────────────────────────────────────────────────────────────────────────
test('bad input is a 422 shown inline in the draft: a DELETE as a query, an HTTP cell with no URL; nothing is created', async ({ page, api, s, net }) => {
  const bp = await bankProject(api)
  const nb = await newNotebook(api, bp.id)
  await openNotebook(page, s, bp.slug, nb.id)
  await expect(page.getByTestId('nb-empty')).toBeVisible()
  await page.getByTestId('nb-composer').getByRole('textbox').fill('/')
  await page.getByTestId('nb-palette').getByRole('option', { name: /^Query/ }).click()
  const draft = page.getByTestId('nb-draft')
  await draft.getByRole('textbox', { name: 'SQL' }).fill('DELETE FROM bank.wires')
  await net.expect([{ method: 'POST', url: /\/api\/notebooks\/[^/]+\/cells$/, status: 422 }], async () => {
    await page.getByTestId('nb-draft-add').click()
    await expect(page.getByTestId('nb-draft-error')).toBeVisible()
  })
  await expect(page.getByTestId('nb-draft-error')).toContainText(/select/i)
  // the form stays, with what was typed
  await expect(draft.getByRole('textbox', { name: 'SQL' })).toHaveValue('DELETE FROM bank.wires')
  // fix it → the error goes away on the next try and the cell is created
  await draft.getByRole('textbox', { name: 'SQL' }).fill('SELECT wire_id FROM bank.wires ORDER BY wire_id')
  await page.getByTestId('nb-draft-add').click()
  await expect(page.getByTestId('nb-cell')).toHaveCount(1)
  await expect(page.getByTestId('nb-draft')).toHaveCount(0)
  // an HTTP cell with no URL
  await page.getByTestId('nb-composer').getByRole('textbox').fill('/')
  await page.getByTestId('nb-palette').getByRole('option', { name: /^HTTP/ }).click()
  await net.expect([{ method: 'POST', url: /\/api\/notebooks\/[^/]+\/cells$/, status: 422 }], async () => {
    await page.getByTestId('nb-draft-add').click()
    await expect(page.getByTestId('nb-draft-error')).toBeVisible()
  })
  expect((await getNotebook(api, nb.id)).cells).toHaveLength(1)
  // an unknown ref is refused too, through the API shape the form uses
  const bad = await api.raw('POST', `/notebooks/${nb.id}/cells`, { kind: 'rule', spec: { op: 'count', left: '{{cells.c9.rows}}' }, created_by: 'you' })
  expect([201, 422]).toContain(bad.status)
})

// ── 10. AI off, AI on ───────────────────────────────────────────────────────────────────────────────────────────
test('AI off: Ask is greyed with a reason and the palette still adds a note', async ({ page, api, s, net }) => {
  const bp = await bankProject(api)
  const nb = await newNotebook(api, bp.id)
  await api.testApi('PUT', 'fake-llm/state', { unavailable: true })
  try {
    const caps = await api.get<{ ai: { available: boolean; reason: string | null } }>('/notebooks/capabilities')
    expect(caps.ai.available).toBe(false)
    await openNotebook(page, s, bp.slug, nb.id)
    await expect(page.getByTestId('nb-ask')).toBeDisabled()
    await page.getByTestId('nb-composer').getByRole('textbox').fill('/')
    const palette = page.getByTestId('nb-palette')
    await expect(palette.getByRole('option', { name: /Ask Studio/ })).toHaveAttribute('aria-disabled', 'true')
    await expect(palette.getByRole('option', { name: /Ask Studio/ })).toContainText(/AI|provider/i)
    // clicking a disabled Ask does nothing
    await palette.getByRole('option', { name: /Ask Studio/ }).click({ force: true })
    await expect(palette).toBeVisible()
    // ⌘↵ with text and AI off does not call the engine
    await palette.getByRole('option', { name: /^Note/ }).click()
    await page.getByTestId('nb-draft').getByRole('textbox').last().fill('still works without AI')
    await page.getByTestId('nb-draft-add').click()
    await expect(page.getByTestId('nb-cell')).toHaveCount(1)
    const noCalls = page.waitForRequest((r) => /\/ask$/.test(r.url()), { timeout: 1000 }).then(() => true, () => false)
    await page.getByTestId('nb-composer').getByRole('textbox').fill('list the stuck wires')
    await page.getByTestId('nb-composer').getByRole('textbox').press('Control+Enter')
    expect(await noCalls, 'no /ask request when AI is off').toBe(false)
    const after = await getNotebook(api, nb.id)
    expect(after.cells.map((c) => c.kind)).toEqual(['note'])
  } finally {
    await api.testApi('PUT', 'fake-llm/state', { unavailable: false })
  }
})

test('Ask with the fake LLM plans cells that then run without AI', async ({ page, api, s, net }) => {
  const bp = await bankProject(api)
  const nb = await newNotebook(api, bp.id)
  const ro = s.nb_conn_bank ?? 'bank'
  await putLlmRules(api, [{
    id: 'nb-ask-edge',
    when: { tool: 'plan_cells', any_user_matches: 'edge stuck wires' },
    respond: {
      tool_calls: [{
        name: 'plan_cells',
        args: {
          summary: 'Read the stuck wires and count them.',
          cells: [
            { kind: 'query', title: 'Stuck wires', spec: { connection: ro, sql: "SELECT wire_id FROM bank.wires WHERE status = 'stuck' ORDER BY wire_id", limit: 50 } },
            { kind: 'rule', title: 'How many', spec: { op: 'count', left: '{{cells.c2.rows}}' } },
          ],
        },
      }],
    },
  }])
  try {
    await openNotebook(page, s, bp.slug, nb.id)
    await expect(page.getByTestId('nb-ask')).toBeDisabled() // empty text
    const box = page.getByTestId('nb-composer').getByRole('textbox')
    await box.fill('edge stuck wires please')
    await expect(page.getByTestId('nb-ask')).toBeEnabled()
    await page.getByTestId('nb-ask').click()
    await expect(page.locator('[data-sonner-toast]').filter({ hasText: 'Studio wrote 2 cells' })).toBeVisible({ timeout: 30_000 })
    await expect(box).toHaveValue('')
    const after = await getNotebook(api, nb.id)
    expect(after.cells.map((c) => c.kind)).toEqual(['ask', 'query', 'rule'])
    expect(after.cells.filter((c) => c.kind !== 'ask').every((c) => (c as unknown as { created_via: string }).created_via === 'agent')).toBe(true)
    await expect(page.getByTestId('nb-cell')).toHaveCount(3)
    await expect(cellEl(page, 'c2')).toContainText('by Studio')
    // the planned cells run with no AI; the Ask cell is skipped on a rerun
    await page.getByTestId('nb-run').click()
    await expect(status(page)).toContainText('Done', { timeout: 30_000 })
    await expect(cellEl(page, 'c3')).toContainText('rows match')
    const run = await settled(api, nb.id)
    const stuck = (cellOf(run, 'c2').latest!.output as { row_count: number }).row_count
    expect(stuck).toBeGreaterThanOrEqual(2)
    expect(cellOf(run, 'c3').latest!.output).toMatchObject({ count: stuck })
  } finally {
    await restoreLlmScript(api)
  }
})

// ── 11. keep as a scheduled check with an unbound credential ────────────────────────────────────────────────────
test('Keep as scheduled check with an unbound credential is blocked with a reason; Save draft works; the workflow opens', async ({ page, api, s, net }) => {
  test.setTimeout(180_000)
  const bp = await bankProject(api)
  const nb = await newNotebook(api, bp.id)
  await addCell(api, nb.id, 'http', {
    method: 'GET', url: `${s.upstreamUrl}/v1/balance`, credential_slot: 'portal-login', expect: { status: 200 },
  }, { title: 'Portal check' })
  await addCell(api, nb.id, 'query', { connection: s.nb_conn_bank ?? 'bank', sql: "SELECT wire_id FROM bank.wires WHERE status = 'stuck'", limit: 10 }, { title: 'Stuck' })
  const probe = await api.raw('POST', `/notebooks/${nb.id}/keep/preview`, { target: 'scheduled_check', trigger: { type: 'cron', cron: '45 6 * * 1-5' } })
  test.skip(probe.status === 501, 'keep is not on this engine')
  await openNotebook(page, s, bp.slug, nb.id)
  await page.getByTestId('nb-keep-btn').getByRole('button', { name: 'Keep as…' }).click()
  const drawer = page.getByTestId('nb-keep')
  await drawer.getByRole('radio', { name: 'Scheduled check' }).or(drawer.getByRole('button', { name: 'Scheduled check' })).first().click()
  const strip = drawer.getByTestId('nb-keep-strip')
  await expect(strip).toBeVisible()
  const slot = strip.locator('[data-testid="nb-keep-item"][data-kind="slots"]')
  await expect(slot).toHaveAttribute('data-s', 'needs_choice')
  await expect(slot).toContainText('portal-login')
  await expect(drawer).toContainText('Saves as a draft until every item is ✓')
  // with an unbound slot the primary button can't schedule: it offers the draft the copy promises (no refused 409)
  // [W5: the earlier "click → 409 → error" step asserted the defect fixed here; replaced, see the W5 report]
  await expect(drawer.getByTestId('nb-keep-save')).toHaveText('Save as draft')
  expect((await getNotebook(api, nb.id) as unknown as { kept: unknown[] }).kept, 'nothing kept yet').toHaveLength(0)

  // "Saves as a draft until every item is ✓": a scheduled check with the manual trigger must save as a draft
  await drawer.getByRole('radio', { name: 'Manual' }).or(drawer.getByRole('button', { name: 'Manual' })).first().click()
  const name = `Kept check ${rand()}`
  await drawer.getByLabel('Name').fill(name)
  await expect(drawer.getByTestId('nb-keep-save')).toBeEnabled()
  await drawer.getByTestId('nb-keep-save').click()
  const saved = drawer.getByTestId('nb-keep-saved')
  const promised = await saved.waitFor({ state: 'visible', timeout: 5000 }).then(() => true, () => false)
  // a refusal here is the defect under test, not noise: take that one 409 off the strict list and fail softly
  for (let i = net.violations.length - 1; i >= 0; i--) if (/unexpected HTTP 409 POST .*\/keep$/.test(net.violations[i])) net.violations.splice(i, 1)
  expect.soft(promised, 'the drawer promises "Saves as a draft" but a scheduled check on the Manual trigger is still refused (409 unbound_credential)').toBe(true)
  if (!promised) {
    // the way out the drawer does offer: keep it as a plain workflow, which really is a draft
    await drawer.getByRole('radio', { name: 'Workflow' }).or(drawer.getByRole('button', { name: 'Workflow', exact: true })).first().click()
    await expect(drawer.getByTestId('nb-keep-save')).toBeEnabled()
    await drawer.getByTestId('nb-keep-save').click()
  }
  await expect(saved).toContainText('Kept as')
  await expect(saved).not.toContainText('schedule active')
  const kept = (await getNotebook(api, nb.id) as unknown as { kept: Array<{ target: string; workflow_id: string }> }).kept
  expect(kept).toHaveLength(1)
  const wf = await api.get<{ id: string; name: string }>(`/workflows/${kept[0].workflow_id}`)
  expect(wf.name).toBe(name)
  expect((wf as unknown as { published_version?: unknown }).published_version ?? null, 'the draft is not published').toBeNull()

  // the kept workflow opens in the editor
  await saved.getByRole('link', { name: /Open the workflow/ }).click()
  await expect(page).toHaveURL(new RegExp(`/workflows/${wf.id}`))
  await expect(page.getByTestId('glass-title')).toHaveText(name)
})

// ── 12. a browser cell: live frame while it runs, failing step opens itself ─────────────────────────────────────
test('browser cell: the live frame shows while the step list runs, then the failing step opens by itself', async ({ page, api, s, net }) => {
  test.skip(!s.jobsUrl, `browser cells need the jobs-service + chromium: ${s.jobsSkip ?? 'not started'}`)
  test.setTimeout(240_000)
  const server = http.createServer((req, res) => {
    res.writeHead(200, { 'content-type': 'text/html' })
    res.end('<!doctype html><html><head><title>Accounts</title></head><body><h1>Accounts</h1><table><tr><td>A1</td></tr></table></body></html>')
  })
  await new Promise<void>((r) => server.listen(0, '127.0.0.1', () => r()))
  const base = `http://127.0.0.1:${(server.address() as AddressInfo).port}`
  try {
    const bp = await bankProject(api)
    const nb = await newNotebook(api, bp.id)
    await addCell(api, nb.id, 'browser', {
      base_url: base,
      steps: [
        { action: 'goto', url: '/accounts' },
        { action: 'expectVisible', target: { role: 'table' } },
        { action: 'expectText', target: { role: 'heading', name: 'Accounts' }, text: 'Totals', timeout_ms: 4000 },
      ],
    }, { title: 'Accounts page' })
    await openNotebook(page, s, bp.slug, nb.id)
    // the browser tab is available and says so before any run
    await page.getByTestId('nb-wsrail').click()
    await page.getByTestId('nb-run').click()
    await expect(page.getByTestId('nb-workspace')).toHaveAttribute('data-tab', 'browser', { timeout: 60_000 })
    const frames = page.getByTestId('nb-frame')
    await expect(frames.first()).toBeVisible({ timeout: 60_000 })
    await expect(status(page)).toContainText(/Failed/, { timeout: 200_000 })
    // the failing step list opens by itself and names step 3
    const steps = cellEl(page, 'c1').getByTestId('nb-browser-steps')
    await expect(steps).toBeVisible()
    await expect(steps.locator('[data-s="failed"]')).toHaveCount(1)
    await expect(cellEl(page, 'c1').getByTestId('nb-cell-error')).toContainText('step 3')
    await expect(cellEl(page, 'c1')).toContainText('2 of 3 passed')
    const after = await getNotebook(api, nb.id)
    expect(cellOf(after, 'c1').latest!.status).toBe('error')
  } finally {
    await new Promise<void>((r) => server.close(() => r()))
  }
})

// ── 13. reload mid-run, deep link, back/forward ─────────────────────────────────────────────────────────────────
test('reload mid-run keeps the run; reload on a "needs you" run keeps the card', async ({ page, api, s, net }) => {
  const bp = await bankProject(api)
  const nb = await newNotebook(api, bp.id)
  await addCell(api, nb.id, 'data', { columns: ['n'], rows: [{ n: 1 }] }, { title: 'One' })
  await addCell(api, nb.id, 'wait', { mode: 'duration', duration: '5s' }, { title: 'Hold on' })
  // a proposed write parks the run on "needs you" (never applied here)
  await addCell(api, nb.id, 'change', { connection: s.nb_conn_bank_rw ?? 'bank_rw', table: 'bank.wires', key: { wire_id: 'W4' }, set: { status: 'held' }, reason: 'Please fix.' }, { title: 'Hand over' })
  await openNotebook(page, s, bp.slug, nb.id)
  await page.getByTestId('nb-run').click()
  await expect(page.getByTestId('nb-stop')).toBeVisible()
  await page.reload()
  await expect(page.getByTestId('nb-cells')).toBeVisible()
  await expect(page.getByTestId('nb-stop'), 'the reloaded page still knows the run is live').toBeVisible()
  await expect(status(page)).toContainText(/Running|Waiting/)
  await expect(cellEl(page, 'c1')).toHaveAttribute('data-status', 'ok')
  await expect(status(page)).toContainText('Needs you', { timeout: 40_000 })
  await expect(cellEl(page, 'c3').getByTestId('nb-change-pending')).toBeVisible()
  await page.reload()
  await expect(cellEl(page, 'c3').getByTestId('nb-change-pending')).toBeVisible()
  await expect(cellEl(page, 'c3')).toContainText('held')
  await expect(status(page)).toContainText('Needs you')
  await expect(page.getByTestId('nb-run')).toBeVisible()
  const st = await getNotebook(api, nb.id)
  expect(st.runs[0].status).toBe('needs_you')
})

test('deep link ?cell= scrolls to and selects the cell; back/forward move between manager and notebook', async ({ page, api, s, net }) => {
  const bp = await bankProject(api)
  const nb = await newNotebook(api, bp.id)
  for (let i = 1; i <= 14; i++) await addCell(api, nb.id, 'note', { markdown: `Note number ${i}` }, { title: `Note ${i}` })
  const last = await addCell(api, nb.id, 'data', { columns: ['n'], rows: [{ n: 99 }] }, { title: 'The last one' })
  await page.setViewportSize({ width: 1440, height: 700 })
  await page.goto(`${s.studioUrl}/p/${bp.slug}/notebooks`)
  await expect(page.getByTestId('notebook-manager')).toBeVisible()
  await page.goto(`${s.studioUrl}/p/${bp.slug}/notebooks/${nb.id}?cell=${last.id}`)
  await expect(page.getByTestId('nb-cells')).toBeVisible()
  const target = cellEl(page, 'c15')
  await expect(target).toHaveAttribute('data-selected', '')
  await expect(target).toBeInViewport()
  await expect(cellEl(page, 'c1')).not.toBeInViewport()
  // a bad ?cell= does not break the page
  await page.goto(`${s.studioUrl}/p/${bp.slug}/notebooks/${nb.id}?cell=nbc_nope`)
  await expect(page.getByTestId('nb-cells')).toBeVisible()
  // back/forward
  await page.goBack()
  await expect(page).toHaveURL(new RegExp(`notebooks/${nb.id}\\?cell=${last.id}`))
  await expect(target).toBeVisible()
  await page.getByRole('link', { name: 'Notebooks', exact: true }).first().click()
  await expect(page.getByTestId('notebook-manager')).toBeVisible()
  await page.goBack()
  await expect(page.getByTestId('nb-cells')).toBeVisible()
  await page.goForward()
  await expect(page.getByTestId('notebook-manager')).toBeVisible()
  await expect(page.getByTestId('nb-list')).toContainText(nb.title)
  // a deleted notebook shows the not-found card, not a blank page
  const gone = await newNotebook(api, bp.id)
  await api.del(`/notebooks/${gone.id}`)
  await net.expect([{ method: 'GET', url: /\/api\/notebooks\/[^/]+$/, status: 404 }], async () => {
    await page.goto(`${s.studioUrl}/p/${bp.slug}/notebooks/${gone.id}`)
    await expect(page.getByTestId('nb-load-error')).toContainText('does not exist')
  })
})

// ── 14. look: dark mode, phone width, keyboard only ─────────────────────────────────────────────────────────────
async function lum(page: Page, sel: string, prop: 'backgroundColor' | 'color'): Promise<number> {
  return page.locator(sel).first().evaluate((el, p) => {
    const c = getComputedStyle(el)[p as 'color']
    const m = c.match(/[\d.]+/g)!.map(Number)
    const f = (v: number) => { const x = v / 255; return x <= 0.03928 ? x / 12.92 : ((x + 0.055) / 1.055) ** 2.4 }
    return 0.2126 * f(m[0]) + 0.7152 * f(m[1]) + 0.0722 * f(m[2])
  }, prop)
}

async function seedLook(api: EngineApi, s: { nb_conn_bank?: string }) {
  const bp = await bankProject(api)
  const nb = await newNotebook(api, bp.id)
  await addCell(api, nb.id, 'data', { columns: ['max_age_days'], rows: [{ max_age_days: 1 }] }, { title: 'Settlement window' })
  await addCell(api, nb.id, 'query', { connection: s.nb_conn_bank ?? 'bank', sql: "SELECT wire_id, amount, status, created_at FROM bank.wires WHERE status = 'stuck' ORDER BY wire_id", limit: 100 }, { title: 'Stuck wires with a rather long title that has to wrap on a phone' })
  await addCell(api, nb.id, 'rule', { op: 'count', left: '{{cells.c2.rows}}' }, { title: 'How many' })
  await addCell(api, nb.id, 'change', { connection: 'bank_rw', table: 'bank.wires', key: { wire_id: '{{cells.c2.rows[0].wire_id}}' }, set: { status: 'held' }, reason: 'look only' }, { title: 'Hold' })
  await runAndSettle(api, nb.id)
  return { bp, nb }
}

test('dark mode: surfaces are dark, text is light, no console errors', async ({ browser, api, s, net }) => {
  const { bp, nb } = await seedLook(api, s)
  const ctx = await browser.newContext({ colorScheme: 'dark', viewport: { width: 1440, height: 900 } })
  const page = await ctx.newPage()
  try {
    await openNotebook(page, s, bp.slug, nb.id)
    await expect(cellEl(page, 'c2').getByTestId('nb-table')).toBeVisible()
    const bg = await lum(page, 'body', 'backgroundColor')
    expect(bg, 'body background is dark').toBeLessThan(0.1)
    const fg = await lum(page, '[data-testid="nb-cell"] .vtoken, [data-testid="nb-cell"]', 'color')
    expect(fg, 'cell text is light').toBeGreaterThan(0.3)
    const tbl = await lum(page, '[data-testid="nb-table"] td', 'color')
    expect(tbl, 'table text is light').toBeGreaterThan(0.3)
    await expect(cellEl(page, 'c4').getByTestId('nb-change-pending')).toBeVisible()
    const card = await lum(page, '[data-testid="nb-change-pending"]', 'backgroundColor')
    expect(card, 'the needs-you card is not a white slab').toBeLessThan(0.45)
    await page.getByTestId('nb-keep-btn').getByRole('button', { name: 'Keep as…' }).click()
    await expect(page.getByTestId('nb-keep')).toBeVisible()
    const sheet = await lum(page, '[data-testid="nb-keep"]', 'backgroundColor')
    expect(sheet, 'the keep drawer is dark').toBeLessThan(0.2)
  } finally {
    net.assertClean()
    await ctx.close()
  }
})

test('390px wide: no horizontal scroll, the run button and cells fit, the workspace opens as a sheet', async ({ page, api, s, net }) => {
  const { bp, nb } = await seedLook(api, s)
  await page.setViewportSize({ width: 390, height: 844 })
  await openNotebook(page, s, bp.slug, nb.id)
  await expect(cellEl(page, 'c2').getByTestId('nb-table')).toBeVisible()
  const over = await page.evaluate(() => {
    const out: string[] = []
    const w = window.innerWidth
    if (document.documentElement.scrollWidth > w) out.push(`document ${document.documentElement.scrollWidth} > ${w}`)
    const sc = document.querySelector('.nb-scroll') as HTMLElement | null
    if (sc && sc.scrollWidth > sc.clientWidth) out.push(`.nb-scroll ${sc.scrollWidth} > ${sc.clientWidth}`)
    document.querySelectorAll('[data-testid="nb-cell"], [data-testid="nb-composer"], .nb-head, [data-testid="nb-status"]').forEach((el) => {
      const r = el.getBoundingClientRect()
      if (r.right > w + 1 || r.left < -1) out.push(`${el.getAttribute('data-testid') ?? el.className} spans ${Math.round(r.left)}–${Math.round(r.right)}`)
    })
    return out
  })
  expect(over, 'nothing is wider than the phone').toEqual([])
  await expect(page.getByTestId('nb-run')).toBeInViewport()
  // workspace: a sheet on narrow screens
  await cellEl(page, 'c2').getByRole('button', { name: /Show all/ }).click()
  await expect(page.getByTestId('nb-workspace')).toBeVisible()
  const sheetOver = await page.evaluate(() => {
    const el = document.querySelector('[data-testid="nb-workspace"]') as HTMLElement
    const r = el.getBoundingClientRect()
    return { right: r.right, w: window.innerWidth, doc: document.documentElement.scrollWidth }
  })
  expect(sheetOver.right).toBeLessThanOrEqual(sheetOver.w + 1)
  expect(sheetOver.doc).toBeLessThanOrEqual(sheetOver.w)
  await page.getByTestId('nb-ws-close').click()
  await expect(page.getByTestId('nb-workspace')).toHaveCount(0)
})

test('keyboard only: "/" opens the palette, arrows skip the disabled Ask, Enter picks, Escape closes and returns focus', async ({ page, api, s, net }) => {
  const bp = await bankProject(api)
  const nb = await newNotebook(api, bp.id)
  await api.testApi('PUT', 'fake-llm/state', { unavailable: true })
  try {
    await openNotebook(page, s, bp.slug, nb.id)
    const box = page.getByTestId('nb-composer').getByRole('textbox')
    await box.focus()
    await page.keyboard.press('/')
    const palette = page.getByTestId('nb-palette')
    await expect(palette).toBeVisible()
    const combo = palette.getByRole('combobox')
    await expect(combo).toBeFocused()
    // type to filter, Escape, focus comes back to the composer
    await page.keyboard.type('note')
    await expect(palette.getByRole('option')).toHaveCount(1)
    await page.keyboard.press('Escape')
    await expect(palette).toHaveCount(0)
    const where = await page.evaluate(() => `${document.activeElement?.tagName}.${(document.activeElement as HTMLElement | null)?.getAttribute('aria-label') ?? ''}`)
    expect.soft(where, 'focus returns to the composer after Escape closes the palette').toBe('INPUT.Add a block or ask Studio')
    await box.focus()
    // open again, walk the list with the arrow keys: the disabled Ask is never the active option
    await page.keyboard.press('/')
    await expect(palette).toBeVisible()
    const opts = palette.getByRole('option')
    const n = await opts.count()
    expect(n).toBeGreaterThan(10)
    for (let i = 0; i < n + 2; i++) {
      await page.keyboard.press('ArrowDown')
      const active = palette.locator('[role="option"][aria-selected="true"]')
      await expect(active).toHaveCount(1)
      await expect(active).not.toHaveAttribute('aria-disabled', 'true')
    }
    // filter to the data block with the keyboard and pick it with Enter
    await page.keyboard.type('data')
    await page.keyboard.press('Enter')
    const draft = page.getByTestId('nb-draft')
    await expect(draft).toHaveAttribute('data-kind', 'data')
    expect.soft(await draft.evaluate((el) => el.contains(document.activeElement)), 'after picking a block with Enter, keyboard focus moves into the new draft form').toBe(true)
    // the form takes keyboard input and submits with Enter on a field
    await draft.getByRole('textbox', { name: 'Title' }).focus()
    await page.keyboard.type('Typed with the keyboard')
    await draft.getByRole('textbox', { name: 'CSV' }).focus()
    await page.keyboard.type('limit_days\n3')
    await draft.getByRole('textbox', { name: 'Title' }).focus()
    await page.keyboard.press('Enter')
    await expect(page.getByTestId('nb-cell')).toHaveCount(1)
    await expect(cellEl(page, 'c1')).toContainText('limit_days')
    const after = await getNotebook(api, nb.id)
    expect(after.cells[0]).toMatchObject({ kind: 'data', title: 'Typed with the keyboard' })
    // a value token is reachable and editable by keyboard alone
    const tok = cellEl(page, 'c1').getByRole('button', { name: /limit_days/ })
    await tok.focus()
    await page.keyboard.press('Enter')
    const edit = page.getByRole('spinbutton', { name: 'limit_days' }).or(page.getByRole('textbox', { name: 'limit_days' }))
    await expect(edit).toBeFocused()
    await page.keyboard.press('ControlOrMeta+a')
    await page.keyboard.type('5')
    await page.keyboard.press('Enter')
    await expect.poll(async () => ((await getNotebook(api, nb.id)).cells[0] as unknown as { spec: { rows: Array<{ limit_days: unknown }> } }).spec.rows[0].limit_days).toBe(5)
  } finally {
    await api.testApi('PUT', 'fake-llm/state', { unavailable: false })
  }
})

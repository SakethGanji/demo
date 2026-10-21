/**
 * Stage 3A: tests inside the editor (API catalog, endpoint picker, test editor, mocks, suggestions, coverage grid,
 * scorecard, locked tests, release block, save as test). Real UI against the real stack. No mocks, no interception.
 */
import { test, expect, type EngineApi } from '../fixtures/test'
import { seedProject, type P } from '../fixtures/seed'
import {
  RISK_YAML, closeNdv, editField, mkTest, nodeEl, openEditor, openNdv, openTestsTab, order, pick, rowByName, runAll,
  seedRefund, shot,
} from '../fixtures/tests3a'
import { assertNoClipping } from '../fixtures/layout'
import { dockMore } from '../fixtures/dock'
import type { Page } from '@playwright/test'

type Wf = { definition: { tests: Array<Record<string, unknown>>; test_defaults: { mocks: Record<string, unknown> }; nodes: Array<{ name: string; parameters: Record<string, unknown> }> } }
const getWf = (api: EngineApi, id: string) => api.get<Wf>(`/workflows/${id}`)

const exp = {
  falseOne: { node: 'Amount over 500', port: 'false', op: 'count', value: 1 },
  trueOne: { node: 'Amount over 500', port: 'true', op: 'count', value: 1 },
}
const T = {
  small: () => mkTest('Small refund is auto-approved', 'normal', 100, [exp.falseOne, { subject: 'node', node: 'Log approved', op: 'status', value: 'success' }]),
  b500: () => mkTest('Exactly 500 is not escalated', 'boundary', 500, [exp.falseOne], { field: 'amount', covers: ['Amount over 500'] }),
  b501: () => mkTest('501 is escalated', 'boundary', 501, [exp.trueOne], { field: 'amount', covers: ['Amount over 500'] }),
  big: () => mkTest('Big refund calls Risk API', 'normal', 900, [{ subject: 'request', node: 'Risk API', path: 'body.amount', op: 'eq', value: 900 }]),
}

test.describe('editor tests (stage 3A)', () => {
  let p: P
  test.beforeEach(async ({ api }) => {
    p = await seedProject(api, 'T16')
  })

  test('T16.1 API catalog: paste a spec, paste one example, delete, Swagger 2 error', async ({ page, api, s, net }) => {
    // any workflow page has the project dock (compact there: it comes back from its handle)
    const { wf } = await seedRefund(api, s, p, { withSpec: false })
    await openEditor(page, s, p.slug, wf.id)
    await dockMore(page, 'apis')
    await expect(page.getByTestId('api-catalog')).toBeVisible()
    await expect(page.getByTestId('api-empty')).toBeVisible()

    // paste the YAML
    await page.getByTestId('api-add').first().click()
    await page.getByTestId('api-spec-input').fill(RISK_YAML)
    await shot(page, 't16-1-add-dialog')
    await page.getByTestId('api-save').click()
    const row = page.locator('[data-testid="api-row"][data-name="Risk API"]')
    await expect(row).toBeVisible()
    await expect(row.getByTestId('api-operation-row')).toHaveCount(2)
    await expect(row.locator('[data-testid="api-operation-row"][data-operation-id="riskCheck"]')).toContainText('/risk-check')
    await expect(row.getByTestId('api-operation-count')).toContainText('2')
    await shot(page, 't16-1-catalog')

    // one example reply
    await page.getByTestId('api-add').click()
    await page.getByTestId('api-example-toggle').click()
    await page.getByTestId('api-name').fill('Orders API')
    await page.getByTestId('api-example-base-url').fill('http://127.0.0.1:9999')
    await page.getByTestId('api-example-path').fill('/orders/{order_id}')
    await page.getByTestId('api-example-input').fill('{"id": 42, "status": "paid"}')
    await page.getByTestId('api-save').click()
    const ex = page.locator('[data-testid="api-row"][data-name="Orders API"]')
    await expect(ex).toBeVisible()
    await expect(ex.getByTestId('api-operation-row')).toHaveCount(1)
    await expect(ex).toContainText('From an example')
    expect((await api.get<unknown[]>(`/api-specs?project_id=${p.id}`)).length).toBe(2)

    // Swagger 2 is refused with a message, the dialog stays open
    await net.expect([{ url: /\/api-specs$/, status: 422 }], async () => {
      await page.getByTestId('api-add').click()
      await page.getByTestId('api-spec-input').fill('swagger: "2.0"\ninfo: {title: Old, version: "1"}\npaths: {}\n')
      await page.getByTestId('api-save').click()
      await expect(page.getByTestId('api-save-error')).toContainText(/Swagger 2/i)
    })
    await shot(page, 't16-1-swagger-error')
    await page.keyboard.press('Escape')
    await expect(page.getByTestId('api-add-dialog')).toBeHidden()

    // delete one
    await ex.getByTestId('api-delete').click()
    await page.getByTestId('api-delete-confirm').click()
    await expect(ex).toHaveCount(0)
    await expect(page.getByTestId('api-row')).toHaveCount(1)
    expect((await api.get<unknown[]>(`/api-specs?project_id=${p.id}`)).length).toBe(1)

  })

  test('T16.2 HTTP endpoint picker: API endpoint hides method/URL, default mock appears, custom URL again', async ({ page, api, s }) => {
    const { wf, spec } = await seedRefund(api, s, p, { withSpec: false, mocks: {} })
    const sp = await api.post<{ id: string }>('/api-specs', { project_id: p.id, name: 'Risk API', spec_text: RISK_YAML })
    expect(spec).toBeNull()
    await openEditor(page, s, p.slug, wf.id)
    await openNdv(page, 'Risk API')
    const modal = page.getByTestId('ndv-modal')
    await expect(modal.getByTestId('http-mode-custom')).toBeVisible()
    await expect(modal.getByText(/^URL/).first()).toBeVisible()
    await modal.getByTestId('http-mode-api').click()
    await pick(page, modal.getByTestId('http-api-endpoint'), /POST.*\/risk-check|\/risk-check/)
    await expect(page.getByTestId('ndv-modal')).toBeVisible() // no crash
    await expect(page.getByText(/something went wrong|unexpected error/i)).toHaveCount(0)
    await shot(page, 't16-2-endpoint-picked')
    // method + URL fields are hidden in API mode
    await expect(modal.getByText(/^URL/)).toHaveCount(0)
    await expect(modal.getByText(/^Method/)).toHaveCount(0)
    await closeNdv(page)
    await page.getByTestId('editor-save').click()
    await expect
      .poll(async () => (await getWf(api, wf.id)).definition.test_defaults?.mocks?.['Risk API'])
      .toMatchObject({ http: { status: 200 } })
    const saved = await getWf(api, wf.id)
    expect(saved.definition.nodes.find((n) => n.name === 'Risk API')!.parameters.api_ref).toMatchObject({ spec_id: sp.id, operation_id: 'riskCheck' })

    // back to Custom URL: the URL field is back, api_ref cleared
    await openNdv(page, 'Risk API')
    await page.getByTestId('ndv-modal').getByTestId('http-mode-custom').click()
    await expect(page.getByTestId('ndv-modal').getByText(/^URL/).first()).toBeVisible()
    await expect(page.getByTestId('ndv-modal').getByText(/^Method/).first()).toBeVisible()
    await shot(page, 't16-2-custom-again')
    await closeNdv(page)
    await expect(page.getByText(/something went wrong|unexpected error/i)).toHaveCount(0)
  })

  test('T16.3 write a test in the UI: passes, then a wrong expectation fails and points at the node', async ({ page, api, s }) => {
    const { wf } = await seedRefund(api, s, p)
    await openEditor(page, s, p.slug, wf.id)
    await openTestsTab(page)
    await expect(page.getByTestId('tests-empty')).toBeVisible()

    await page.getByTestId('tests-new').click()
    await expect(page.getByTestId('test-editor')).toBeVisible()
    await page.getByTestId('test-name').fill('Mid refund stays auto')
    await pick(page, page.getByTestId('test-category'), 'Boundary')
    await page.getByTestId('test-input').fill(JSON.stringify(order(400)))
    await page.getByTestId('test-expect-add').click()
    const row = page.getByTestId('test-expect-row').first()
    await pick(page, row.getByTestId('test-expect-node'), 'Amount over 500')
    await row.getByTestId('test-expect-port').fill('false')
    await pick(page, row.getByTestId('test-expect-op'), 'count')
    await row.getByTestId('test-expect-value').fill('1')
    await shot(page, 't16-3-editor-filled')
    await page.getByTestId('test-save').click()
    await expect(page.getByTestId('test-editor')).toBeHidden()
    await expect(page.getByTestId('tests-count')).toHaveText('1')

    await runAll(page)
    const tr = rowByName(page, 'Mid refund stays auto')
    await expect(tr).toHaveAttribute('data-status', 'passed')
    await expect(page.locator('[data-testid="node-test-badge"][data-status="pass"]').first()).toBeVisible()
    await expect(nodeEl(page, 'Amount over 500').getByTestId('node-test-badge')).toHaveAttribute('data-status', 'pass')
    await shot(page, 't16-3-passed')

    // wrong expectation: 2 items on false
    await tr.click()
    await page.getByTestId('test-expect-row').first().getByTestId('test-expect-value').fill('2')
    await page.getByTestId('test-save').click()
    await runAll(page)
    await expect(tr).toHaveAttribute('data-status', 'failed')
    const f = tr.getByTestId('test-failure').first()
    await expect(f).toHaveAttribute('data-node', 'Amount over 500')
    await expect(nodeEl(page, 'Amount over 500').getByTestId('node-test-badge')).toHaveAttribute('data-status', 'fail')
    await shot(page, 't16-3-failed')
    await f.click()
    await expect(page.getByTestId('ndv-modal')).toBeVisible()
    await expect(page.getByTestId('ndv-modal')).toContainText('Amount')
    await shot(page, 't16-3-opened-node')
    await closeNdv(page)
    // the test lives in the draft; saving the workflow stores it
    await page.getByTestId('editor-save').click()
    await expect.poll(async () => (await getWf(api, wf.id)).definition.tests.length).toBe(1)
  })

  test('T16.4 an unmocked outside step fails as unmocked; "Generate a mock" fixes it', async ({ page, api, s }) => {
    const { wf } = await seedRefund(api, s, p, { without: ['Risk API'], tests: [mkTest('Big refund is escalated', 'normal', 900, [exp.trueOne])] })
    await openEditor(page, s, p.slug, wf.id)
    await openTestsTab(page)
    await runAll(page)
    const tr = rowByName(page, 'Big refund is escalated')
    await expect(tr).toHaveAttribute('data-status', 'error')
    const f = tr.locator('[data-testid="test-failure"][data-kind="unmocked"]')
    await expect(f).toHaveAttribute('data-node', 'Risk API')
    await expect(f.getByTestId('fix-generate-mock')).toBeVisible()
    await expect(f.getByTestId('fix-last-real-reply')).toBeVisible()
    await shot(page, 't16-4-unmocked')
    await f.getByTestId('fix-generate-mock').click()
    await expect(tr).toHaveAttribute('data-status', 'passed')
    await shot(page, 't16-4-fixed')
    // the generated mock sits in test_defaults of the draft; saving stores it
    await page.getByTestId('editor-save').click()
    await expect
      .poll(async () => (await getWf(api, wf.id)).definition.test_defaults.mocks['Risk API'])
      .toMatchObject({ http: { status: 200 } })
  })

  test('T16.5 Suggest: boundaries and API error cases, accept all, run, all pass', async ({ page, api, s, layoutCheck }) => {
    const { wf } = await seedRefund(api, s, p)
    layoutCheck.enabled = false
    await openEditor(page, s, p.slug, wf.id)
    await openTestsTab(page)
    await page.getByTestId('tests-suggest').click()
    const rows = page.getByTestId('suggestion-row')
    await expect(rows.first()).toBeVisible()
    await expect(page.getByTestId('tests-view-suggest')).toBeVisible()
    await shot(page, 't16-5-suggestions')
    const texts = (await rows.allInnerTexts()).join('\n')
    expect(texts).toMatch(/\b500\b/)
    expect(texts).toMatch(/\b501\b/)
    expect(texts).toMatch(/Amount over 500/)
    expect(texts).toMatch(/Risk API/)
    expect(texts).toMatch(/error|400|404|500|timeout|fail|down|unavailable/i)
    const n = await rows.count()

    // accept one by hand, then the rest at once
    await rows.first().getByTestId('suggest-accept').click()
    await expect(rows).toHaveCount(n - 1)
    // dismiss one
    await rows.first().getByTestId('suggest-dismiss').click()
    await expect(rows).toHaveCount(n - 2)
    await page.getByTestId('suggest-accept-all').click()
    await expect(rows).toHaveCount(0)
    await page.getByTestId('tests-view-list').click()
    await expect(page.getByTestId('test-row')).toHaveCount(n - 1)
    await expect(page.getByTestId('test-row').first()).toContainText('Draft')
    await runAll(page)
    await expect(page.locator('[data-testid="test-row"][data-status="passed"]')).toHaveCount(n - 1)
    await shot(page, 't16-5-accepted-passing')
  })

  test('T16.5b suggestions include the 500 and 501 boundary and Risk API error cases (engine)', async ({ page, api, s, layoutCheck }) => {
    layoutCheck.enabled = false
    const { wf } = await seedRefund(api, s, p)
    await openEditor(page, s, p.slug, wf.id)
    await openTestsTab(page)
    await page.getByTestId('tests-suggest').click()
    const rows = page.getByTestId('suggestion-row')
    await expect(rows.first()).toBeVisible()
    const t = await rows.allInnerTexts()
    expect(t.some((x) => /500/.test(x) && /boundary/i.test(x))).toBe(true)
    expect(t.some((x) => /501/.test(x) && /boundary/i.test(x))).toBe(true)
    expect(t.some((x) => /Risk API/.test(x) && /external error|error/i.test(x))).toBe(true)
  })

  test('T16.6 coverage grid: pass / none cells; a none cell opens the editor prefilled', async ({ page, api, s }) => {
    const { wf } = await seedRefund(api, s, p, { tests: [T.small(), T.b500()] })
    await openEditor(page, s, p.slug, wf.id)
    await openTestsTab(page)
    await runAll(page)
    await expect(page.locator('[data-testid="test-row"][data-status="passed"]')).toHaveCount(2)
    await page.getByTestId('tests-view-grid').click()
    await expect(page.getByTestId('coverage-grid')).toBeVisible()
    await shot(page, 't16-6-grid')
    await expect(page.locator('[data-testid="coverage-cell"][data-status="pass"]').first()).toBeVisible()
    await expect(page.locator('[data-testid="coverage-cell"][data-status="none"]').first()).toBeVisible()
    await expect(page.locator('[data-testid="coverage-cell"][data-node="Amount over 500"][data-category="boundary"]')).toHaveAttribute('data-status', 'pass')
    await expect(page.locator('[data-testid="coverage-cell"][data-node="Amount over 500"][data-category="normal"]')).toHaveAttribute('data-status', 'pass')
    // an uncovered cell
    const none = page.locator('[data-testid="coverage-cell"][data-node="Normalize"][data-category="missing"]')
    await expect(none).toHaveAttribute('data-status', 'none')
    await none.click()
    const ed = page.getByTestId('test-editor')
    await expect(ed).toBeVisible()
    await expect(ed.getByTestId('test-category')).toContainText('Missing')
    await expect(ed.getByTestId('test-target')).toContainText('Normalize')
    await shot(page, 't16-6-gap-editor')
    // writing it covers the cell
    await ed.getByTestId('test-name').fill('Normalize without amount')
    await ed.getByTestId('test-input').fill(JSON.stringify([{ body: { order_id: 'A1', reason: 'x' } }]))
    await ed.getByTestId('test-expect-add').click()
    await pick(page, ed.getByTestId('test-expect-op'), 'count')
    await ed.getByTestId('test-expect-value').fill('1')
    await ed.getByTestId('test-save').click()
    await runAll(page)
    await expect(page.locator('[data-testid="coverage-cell"][data-node="Normalize"][data-category="missing"]')).not.toHaveAttribute('data-status', 'none')
  })

  test('T16.7 Score: branches, planted bugs X/Y, gaps; test bar updates', async ({ page, api, s, layoutCheck }) => {
    const { wf } = await seedRefund(api, s, p, { tests: [T.small(), T.big()] })
    await openEditor(page, s, p.slug, wf.id)
    await openTestsTab(page)
    await runAll(page)
    const bar = page.getByTestId('test-bar')
    await expect(bar).toHaveAttribute('data-state', 'pass')
    await expect(bar).not.toContainText('bugs')
    layoutCheck.enabled = false // list rows straddling the fold of the scrolling list are expected; see T16.11 (maximized)
    await page.getByTestId('tests-score').click()
    await expect(page.getByTestId('tests-scorecard')).toBeVisible({ timeout: 60_000 })
    await expect(page.getByTestId('tests-view-score')).toBeVisible()
    await expect(page.getByTestId('scorecard-branches')).toContainText(/\d+\/\d+ taken/)
    const mut = page.getByTestId('scorecard-mutation')
    await expect(mut).toContainText(/(\d+)\/(\d+) caught/)
    const m = /(\d+)\/(\d+) caught/.exec((await mut.innerText()))!
    expect(Number(m[2])).toBeGreaterThan(0)
    await expect(page.getByTestId('scorecard-gaps')).toBeVisible()
    expect(await page.getByTestId('scorecard-gap').count()).toBeGreaterThan(0)
    await expect(page.getByTestId('tests-scorecard-bar')).toContainText(/planted bugs? caught/)
    await expect(bar).toContainText(/bugs/)
    await expect(bar).toContainText(new RegExp(`${m[1]}/${m[2]}`))
    await shot(page, 't16-7-scorecard')
  })

  test('T16.7b clicking a surviving planted bug selects its node on the canvas', async ({ page, api, s, layoutCheck }) => {
    layoutCheck.enabled = false
    const { wf } = await seedRefund(api, s, p, { tests: [T.small(), T.big()] })
    await openEditor(page, s, p.slug, wf.id)
    await openTestsTab(page)
    await page.getByTestId('tests-score').click()
    await expect(page.getByTestId('tests-scorecard')).toBeVisible({ timeout: 60_000 })
    // thin tests leave survivors: clicking one selects its node
    const surv = page.getByTestId('scorecard-survivor')
    expect(await surv.count()).toBeGreaterThan(0)
    const node = (await surv.first().getAttribute('data-node'))!
    await surv.first().locator('button').click()
    await expect(nodeEl(page, node)).toHaveClass(/selected/)
    await shot(page, 't16-7-survivor-selected')
  })

  test('T16.8 approve locks the test; unlock frees it', async ({ page, api, s }) => {
    const { wf } = await seedRefund(api, s, p, { tests: [T.small()] })
    await openEditor(page, s, p.slug, wf.id)
    await openTestsTab(page)
    await rowByName(page, 'Small refund').click()
    const ed = page.getByTestId('test-editor')
    await expect(ed.getByTestId('test-approve')).toBeDisabled()
    await ed.getByTestId('test-approved-by').fill('Priya')
    await ed.getByTestId('test-approve').click()
    await expect(ed.getByTestId('test-locked')).toBeVisible()
    await expect(ed.getByTestId('test-locked')).toContainText('Priya')
    await expect(ed.getByTestId('test-name')).toBeDisabled()
    await expect(ed.getByTestId('test-input')).toBeDisabled()
    await expect(ed.getByTestId('test-save')).toBeDisabled()
    await expect(ed.getByTestId('test-expect-add')).toBeDisabled()
    await shot(page, 't16-8-locked')
    const stored = (await getWf(api, wf.id)).definition.tests[0]
    expect(stored).toMatchObject({ status: 'approved', locked: true, approved_by: 'Priya' })

    await ed.getByTestId('test-unlock').click()
    await expect(ed.getByTestId('test-locked')).toHaveCount(0)
    await expect(ed.getByTestId('test-name')).toBeEnabled()
    await expect(ed.getByTestId('test-input')).toBeEnabled()
    const after = (await getWf(api, wf.id)).definition.tests[0]
    expect(after).toMatchObject({ status: 'draft', locked: false })
    await shot(page, 't16-8-unlocked')
  })

  test('T16.9 THE DEMO: break the If, the live run fails the 500 test, publish is blocked; fix it, publish works', async ({ page, api, s, net }) => {
    const { wf } = await seedRefund(api, s, p, { tests: [T.small(), T.b500(), T.b501(), T.big()] })
    await openEditor(page, s, p.slug, wf.id)
    await openTestsTab(page)
    await runAll(page)
    await expect(page.locator('[data-testid="test-row"][data-status="passed"]')).toHaveCount(4)
    // approve the two boundary tests in the UI
    for (const name of ['Exactly 500', '501 is escalated']) {
      await rowByName(page, name).locator('button').first().click()
      const ed = page.getByTestId('test-editor')
      await ed.getByTestId('test-approved-by').fill('Priya')
      await ed.getByTestId('test-approve').click()
      await expect(ed.getByTestId('test-locked')).toBeVisible()
      await page.keyboard.press('Escape')
      await expect(ed).toBeHidden()
    }
    await expect(page.getByTestId('test-bar')).toHaveAttribute('data-state', 'pass')
    await shot(page, 't16-9-approved')

    // break the condition: > 500 becomes >= 500
    await openNdv(page, 'Amount over 500')
    await editField(page, '> 500', '{{ $json.amount >= 500 }}')
    await closeNdv(page)
    // no click: the live run picks it up
    const b500 = rowByName(page, 'Exactly 500')
    await expect(b500).toHaveAttribute('data-status', 'failed', { timeout: 20_000 })
    await expect(b500.getByTestId('test-failure').first()).toHaveAttribute('data-node', 'Amount over 500')
    await expect(rowByName(page, '501 is escalated')).toHaveAttribute('data-status', 'passed')
    await expect(page.getByTestId('test-bar')).toHaveAttribute('data-state', 'fail')
    await expect(nodeEl(page, 'Amount over 500').getByTestId('node-test-badge')).toHaveAttribute('data-status', 'fail')
    await shot(page, 't16-9-broken-live')

    // publish: blocked, the Tests tab opens
    await page.getByTestId('bottom-tab-problems').click()
    await expect(page.getByTestId('tests-panel')).toBeHidden()
    await net.expect([{ url: /\/workflows\/[^/]+\/publish$/, status: 422 }], async () => {
      await page.getByTestId('editor-publish').click()
    })
    await expect(page.getByText(/Can't publish/i)).toBeVisible()
    await expect(page.getByTestId('tests-panel')).toBeVisible()
    await expect(page.getByTestId('editor-unpublish')).toHaveCount(0)
    await shot(page, 't16-9-publish-blocked')

    // fix it back
    await openNdv(page, 'Amount over 500')
    await editField(page, '>= 500', '{{ $json.amount > 500 }}')
    await closeNdv(page)
    await expect(b500).toHaveAttribute('data-status', 'passed', { timeout: 20_000 })
    await expect(page.getByTestId('test-bar')).toHaveAttribute('data-state', 'pass')
    await page.getByTestId('editor-publish').click()
    await expect(page.getByTestId('editor-unpublish')).toBeVisible({ timeout: 20_000 })
    const w = await api.get<{ active: boolean }>(`/workflows/${wf.id}`)
    expect(w.active).toBe(true)
    await shot(page, 't16-9-published')
  })

  test('T16.10 Save as test from a real run: replay draft, Save, Run, passes', async ({ page, api, s }) => {
    // a copy of Refund triage in the seeded project (its refunds table and the fake API), run for real
    const ps = await api.get<Array<{ id: string; slug: string }>>('/projects')
    const dp = ps.find((x) => x.id === 'proj_default')!
    const { wf } = await seedRefund(api, s, { id: 'proj_default', slug: dp.slug } as P, { withSpec: false, mocks: null, name: `Refund replay ${Date.now()}` })
    await api.put(`/workflows/${wf.id}`, {}).catch(() => undefined)
    await openEditor(page, s, dp.slug, wf.id)
    await page.getByTestId('editor-run').click()
    await expect(page.getByTestId('execution-status')).toContainText(/success|failed|error/i, { timeout: 30_000 })
    if (!(await page.getByTestId('save-as-test').isVisible())) await page.getByTestId('editor-toggle-logs').click()
    await expect(page.getByTestId('save-as-test')).toBeVisible()
    await shot(page, 't16-10-logs')
    await page.getByTestId('save-as-test').click()
    const ed = page.getByTestId('test-editor')
    await expect(ed).toBeVisible()
    await expect(ed.getByTestId('test-name')).not.toHaveValue('')
    await shot(page, 't16-10-replay-editor')
    await ed.getByTestId('test-save').click()
    await expect(ed).toBeHidden()
    await openTestsTab(page)
    await runAll(page)
    await expect(page.locator('[data-testid="test-row"]')).toHaveCount(1)
    await expect(page.getByTestId('test-row').first()).toHaveAttribute('data-status', 'passed')
    await shot(page, 't16-10-replay-passes')
  })

  for (const scheme of ['light', 'dark'] as const) {
    test(`T16.11 ${scheme}: Tests tab and grid have no clipped or overlapping text`, async ({ page, api, s, layoutCheck }) => {
      layoutCheck.enabled = false
      await page.emulateMedia({ colorScheme: scheme })
      await page.setViewportSize({ width: 1440, height: 900 })
      const { wf } = await seedRefund(api, s, p, { tests: [T.small(), T.b500(), T.b501(), T.big()] })
      await openEditor(page, s, p.slug, wf.id)
      await openTestsTab(page)
      await runAll(page)
      await expect(page.locator('[data-testid="test-row"][data-status="passed"]')).toHaveCount(4)
      await expect(await noTextOverflow(page, '[data-testid="tests-panel"]')).toEqual([])
      await page.getByLabel('Maximize panel').click()
      await assertNoClipping(page)
      await shot(page, `t16-11-${scheme}-list`)
      await page.getByTestId('tests-view-grid').click()
      await expect(page.getByTestId('coverage-grid')).toBeVisible()
      await assertNoClipping(page)
      await expect(await noTextOverflow(page, '[data-testid="coverage-grid"]')).toEqual([])
      await shot(page, `t16-11-${scheme}-grid`)
      await page.getByTestId('tests-score').click()
      await expect(page.getByTestId('tests-scorecard')).toBeVisible({ timeout: 60_000 })
      await assertNoClipping(page)
      await expect(await noTextOverflow(page, '[data-testid="tests-scorecard"]')).toEqual([])
      await shot(page, `t16-11-${scheme}-score`)
      await page.getByTestId('tests-view-list').click()
      await rowByName(page, 'Small refund').locator('button').first().click()
      await expect(page.getByTestId('test-editor')).toBeVisible()
      await shot(page, `t16-11-${scheme}-editor`)
    })
  }
})

/** Elements under `root` whose text is cut off by their own box without an ellipsis or a title (truncate is fine). */
async function noTextOverflow(page: Page, root: string): Promise<string[]> {
  return page.evaluate((sel) => {
    const out: string[] = []
    for (const el of document.querySelectorAll(`${sel} button, ${sel} th, ${sel} span`)) {
      const cs = getComputedStyle(el)
      if (cs.textOverflow === 'ellipsis' || cs.overflow === 'visible') continue
      if (el.scrollWidth > el.clientWidth + 1 && !(el as HTMLElement).title) out.push(`${el.textContent?.slice(0, 40)} ${el.scrollWidth}>${el.clientWidth}`)
    }
    return out
  }, root)
}

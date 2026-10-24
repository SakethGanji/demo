/**
 * Stage 1: run and debug in the editor (disable, error output, test step, run to / from,
 * past runs, history, command palette, and the refund-triage demo). Real UI against the real stack.
 */
import type { Locator, Page } from '@playwright/test'
import fs from 'node:fs'
import path from 'node:path'
import { test, expect, rand, type EngineApi } from '../fixtures/test'
import { seedProject, seedWorkflow, type P } from '../fixtures/seed'
import type { StackState } from '../global-setup'
import { legacyEditor } from '../fixtures/legacy'

// these specs guard the legacy canvas editor (the glass editor is the default)
test.beforeEach(async ({ page }) => legacyEditor(page))

type Items = Array<{ json: Record<string, unknown> }>
interface ExecDetail {
  id: string
  status: string
  errors: Array<{ node_name: string; error: string }>
  node_data: Record<string, Items>
}

const setNode = (name: string, label: string, field: string, value: string, x: number) => ({
  name,
  type: 'Set',
  label,
  parameters: { mode: 'manual', fields: [{ name: field, value, type: 'string' }] },
  position: { x, y: 0 },
})
const link = (a: string, b: string, out = 'main') => ({ source_node: a, source_output: out, target_node: b, target_input: 'main' })

/** Start → A → B → C, every node adds one field. */
function chain(name: string) {
  return {
    name,
    nodes: [{ name: 'Start', type: 'Start', parameters: {}, position: { x: 0, y: 0 } }, setNode('A', 'Step A', 'a', '1', 240), setNode('B', 'Step B', 'b', '2', 480), setNode('C', 'Step C', 'c', '3', 720)],
    connections: [link('Start', 'A'), link('A', 'B'), link('B', 'C')],
  }
}

async function execs(api: EngineApi, wfId: string) {
  const list = await api.get<Array<{ id: string; start_time: string }>>(`/executions?workflow_id=${wfId}`)
  return list.sort((x, y) => (x.start_time < y.start_time ? 1 : x.start_time > y.start_time ? -1 : 0))
}
async function latest(api: EngineApi, wfId: string): Promise<ExecDetail> {
  const l = await execs(api, wfId)
  return api.get<ExecDetail>(`/executions/${l[0].id}`)
}
const nodeEl = (page: Page, name: string): Locator => page.locator(`.react-flow__node[data-id="${name}"]`)

async function openEditor(page: Page, s: StackState, slug: string, wfId: string, search = '') {
  await page.goto(`${s.studioUrl}/p/${slug}/workflows/${wfId}${search}`)
  await expect(page.getByTestId('workflow-navbar')).toBeVisible()
  await expect(page.locator('.react-flow__node').first()).toBeVisible()
}
async function rightClick(page: Page, name: string) {
  await nodeEl(page, name).click({ button: 'right' })
}
async function ctx(page: Page, name: string, testId: string) {
  await rightClick(page, name)
  await page.getByTestId(testId).click()
}
async function openNdv(page: Page, name: string) {
  await nodeEl(page, name).dblclick()
  await expect(page.getByTestId('ndv-modal')).toBeVisible()
}
async function outputJson(page: Page) {
  await page.getByTestId('ndv-output-mode-json').click()
}
async function closeNdv(page: Page) {
  await page.getByTestId('ndv-close').click()
  await expect(page.getByTestId('ndv-modal')).toBeHidden()
}
async function runAndWait(page: Page) {
  await page.getByTestId('editor-run').click()
  await expect(page.getByTestId('execution-status')).toContainText(/success|failed|error/i, { timeout: 30_000 })
}
/** the run row is finalised slightly after the stream reports it */
async function settled(api: EngineApi, wfId: string) {
  await expect.poll(async () => (await latest(api, wfId)).status, { timeout: 20_000 }).not.toBe('running')
  return latest(api, wfId)
}
async function setUrl(page: Page, value: string) {
  await page.getByTestId('ndv-tab-parameters').click()
  // the field shows its value as text until it is clicked
  await page.getByTestId('ndv-modal').getByText(/^http:\/\/127\.0\.0\.1:\d+\//).first().click()
  const url = page.getByTestId('ndv-modal').getByLabel(/^URL/).first()
  await url.fill(value)
  await expect(url).toHaveValue(value)
}
async function openErrorHandling(page: Page) {
  await page.getByTestId('ndv-tab-parameters').click()
  if (!(await page.getByTestId('node-setting-on-error').isVisible())) await page.getByTestId('ndv-section-errors').click()
}
async function snap(page: Page, name: string) {
  await page.screenshot({ path: test.info().outputPath(`${name}.png`) })
}
async function projectSlug(api: EngineApi, id: string) {
  const ps = await api.get<Array<{ id: string; slug: string }>>('/projects')
  return ps.find((p) => p.id === id)!.slug
}
/**
 * The failed run seeded by global-setup. The engine keeps only the newest 100 runs, so in a full-suite run the
 * seed is pruned before this spec starts; then an equivalent failed run is created the same way the seed did
 * (draft on the failing route, one POST /run with amount 900, draft back on the ok route).
 */
async function seededFailedRun(api: EngineApi, s: StackState): Promise<string> {
  const r = await api.raw('GET', `/executions/${s.refund_failed_execution_id}`)
  if (r.status === 200) return s.refund_failed_execution_id
  type Def = { nodes: Array<{ name: string; parameters: Record<string, unknown> }>; connections: unknown[] }
  const def = (await api.get<{ definition: Def }>(`/workflows/${s.refund_workflow_id}`)).definition
  const withUrl = (url: string) => def.nodes.map((n) => (n.name === 'Risk API' ? { ...n, parameters: { ...n.parameters, url } } : n))
  await api.put(`/workflows/${s.refund_workflow_id}`, { nodes: withUrl(s.fake_api_fail_url), connections: def.connections })
  const run = await api.post<{ execution_id: string; status: string }>(`/workflows/${s.refund_workflow_id}/run`, {
    input_data: { order_id: 'ord_1042', amount: 900, reason: 'item arrived damaged' },
  })
  expect(run.status).toBe('failed')
  await api.put(`/workflows/${s.refund_workflow_id}`, { nodes: withUrl(s.fake_api_ok_url), connections: def.connections })
  return run.execution_id
}
function refundDef(s: StackState, name: string, url: string, webhookPath = `refund-${rand()}`) {
  const raw = fs.readFileSync(path.resolve(__dirname, '../../../workflow-engine/tests/fixtures/workflows/refund_triage.json'), 'utf8')
  const def = JSON.parse(raw.replace('__REFUND_API_URL__', url))
  def.name = name
  def.nodes[0].parameters.path = webhookPath
  return def
}

test.describe('editor run + debug', () => {
  let p: P
  test.beforeEach(async ({ api }) => {
    p = await seedProject(api, 'T14')
  })

  test('T14.1 disable a node (context menu and D) passes items through', async ({ page, api, s }) => {
    const wf = await seedWorkflow(api, p, chain('Disable'))
    await openEditor(page, s, p.slug, wf.id)
    await ctx(page, 'B', 'ctx-toggle-disable')
    await expect(nodeEl(page, 'B').locator('.node-disabled')).toHaveCount(1)
    // plain D toggles it back, and again
    await nodeEl(page, 'B').click()
    await page.keyboard.press('d')
    await expect(page.locator('.node-disabled')).toHaveCount(0)
    await page.keyboard.press('d')
    await expect(page.locator('.node-disabled')).toHaveCount(1)
    await expect(nodeEl(page, 'B').locator('.node-disabled')).toHaveCount(1)
    await snap(page, 'disabled-node')
    await runAndWait(page)
    const ex = await settled(api, wf.id)
    expect(ex.status).toBe('success')
    expect(ex.node_data.A[0].json).toMatchObject({ a: '1' })
    expect(ex.node_data.B).toEqual(ex.node_data.A) // passthrough: output == input
    expect(ex.node_data.B[0].json).not.toHaveProperty('b')
    expect(ex.node_data.C[0].json).toMatchObject({ a: '1', c: '3' })
    // the node panel shows the passthrough items as B's output
    await openNdv(page, 'B')
    await outputJson(page)
    await expect(page.getByTestId('ndv-modal')).toContainText('"a": "1"')
    await expect(page.getByTestId('ndv-modal')).not.toContainText('"b": "2"')
    await closeNdv(page)
    // the flag survives save + reload
    await page.getByTestId('editor-save').click()
    await page.reload()
    await expect(page.locator('.node-disabled')).toHaveCount(1)
  })

  test('T14.2 error output: connect the error handle, run, the error branch executes', async ({ page, api, s }) => {
    const wf = await seedWorkflow(api, p, {
      name: 'Error branch',
      nodes: [
        { name: 'Start', type: 'Start', parameters: {}, position: { x: 0, y: 0 } },
        // /drop closes the connection with no reply, so the node fails (HTTP 500 does not fail it)
        { name: 'Http', type: 'HttpRequest', label: 'Call API', parameters: { method: 'GET', url: `${s.fake_api_fail_url}`, responseType: 'json' }, position: { x: 240, y: 0 } },
        setNode('Handled', 'Handled', 'handled', 'yes', 560),
      ].map((n) => (n.name === 'Handled' ? { ...n, position: { x: 560, y: 200 } } : n)),
      connections: [link('Start', 'Http')],
    })
    await openEditor(page, s, p.slug, wf.id)
    await expect(nodeEl(page, 'Http').getByTestId('node-error-handle')).toHaveCount(0)
    await openNdv(page, 'Http')
    await openErrorHandling(page)
    await page.getByTestId('node-setting-on-error').click()
    await page.getByTestId('on-error-error-output').click()
    await closeNdv(page)
    const handle = nodeEl(page, 'Http').getByTestId('node-error-handle')
    await expect(handle).toHaveCount(1)
    const target = nodeEl(page, 'Handled').locator('.react-flow__handle.target').first()
    const a = (await handle.boundingBox())!
    const b = (await target.boundingBox())!
    await page.mouse.move(a.x + a.width / 2, a.y + a.height / 2)
    await page.mouse.down()
    await page.mouse.move((a.x + b.x) / 2, (a.y + b.y) / 2, { steps: 8 })
    await page.mouse.move(b.x + b.width / 2, b.y + b.height / 2, { steps: 8 })
    await page.mouse.up()
    await expect(page.locator('.react-flow__edge')).toHaveCount(2)
    await snap(page, 'error-output-connected')
    await runAndWait(page)
    await expect(page.getByTestId('execution-status')).toContainText(/success/i)
    const wfDraft = await api.get<{ definition: { connections: Array<{ source_output: string }> } }>(`/workflows/${wf.id}`)
    expect(wfDraft.definition.connections.map((c) => c.source_output)).toContain('error')
    const ex = await settled(api, wf.id)
    expect(ex.status).toBe('success')
    expect(ex.errors).toEqual([])
    expect(ex.node_data.Handled).toHaveLength(1)
    expect(JSON.stringify(ex.node_data.Handled[0].json)).toMatch(/"error"/)
    expect(ex.node_data.Handled[0].json.error).toMatchObject({ node: 'Http' })
    expect(ex.node_data.Handled[0].json).toMatchObject({ handled: 'yes' })
    await snap(page, 'error-output-run')
  })

  test('T14.3 test step from the node panel runs only that node', async ({ page, api, s }) => {
    const wf = await seedWorkflow(api, p, chain('Test step'))
    await openEditor(page, s, p.slug, wf.id)
    await runAndWait(page)
    await expect.poll(async () => (await execs(api, wf.id)).length).toBe(1)
    await settled(api, wf.id)
    const before = await execs(api, wf.id)
    expect(before).toHaveLength(1)
    await openNdv(page, 'B')
    // typed input
    await page.getByTestId('ndv-test-source-input').click()
    const editor = page.getByTestId('ndv-test-input-editor').locator('.cm-content')
    await editor.click()
    await page.keyboard.press('ControlOrMeta+a')
    await page.keyboard.press('Delete')
    await page.keyboard.insertText('{"a": "typed", "extra": 7}')
    await page.getByTestId('ndv-test-step').click()
    await expect.poll(async () => (await execs(api, wf.id)).length).toBe(2)
    let ex = await settled(api, wf.id)
    expect(Object.keys(ex.node_data)).toEqual(['B'])
    expect(ex.node_data.B[0].json).toMatchObject({ a: 'typed', extra: 7, b: '2' })
    await expect(page.getByTestId('ndv-status')).toContainText('Success')
    await outputJson(page)
    await expect(page.getByTestId('ndv-modal')).toContainText('"a": "typed"')
    await snap(page, 'test-step-typed')
    // last run: A's output of the earlier full run is the input
    await page.getByTestId('ndv-test-source-last-run').click()
    await expect(page.getByTestId('ndv-test-step')).toBeEnabled()
    await page.getByTestId('ndv-test-step').click()
    await expect.poll(async () => (await execs(api, wf.id)).length).toBe(3)
    ex = await settled(api, wf.id)
    expect(Object.keys(ex.node_data)).toEqual(['B'])
    expect(ex.node_data.B[0].json).toMatchObject({ a: '1', b: '2' })
    expect(ex.node_data.B[0].json).not.toHaveProperty('extra')
    await expect(page.getByTestId('ndv-test-error')).toHaveCount(0)
    await snap(page, 'test-step-last-run')
    await closeNdv(page)
    // the other nodes kept the full run's data: C never got a new output
    const all = await execs(api, wf.id)
    const withC = []
    for (const e of all) if ('C' in (await api.get<ExecDetail>(`/executions/${e.id}`)).node_data) withC.push(e.id)
    expect(withC).toEqual([before[0].id])
  })

  test('T14.4 run to here stops there; run from here re-runs the downstream', async ({ page, api, s }) => {
    const wf = await seedWorkflow(api, p, chain('Run to from'))
    await openEditor(page, s, p.slug, wf.id)
    await ctx(page, 'B', 'ctx-run-to')
    await expect.poll(async () => (await execs(api, wf.id)).length).toBe(1)
    let ex = await settled(api, wf.id)
    ex = await settled(api, wf.id)
    expect(Object.keys(ex.node_data).sort()).toEqual(['A', 'B', 'Start'])
    expect(ex.node_data.C).toBeUndefined()
    await snap(page, 'run-to-here')
    const runTo = ex.id
    // run from C: the upstream outputs come from the run-to execution
    await ctx(page, 'C', 'ctx-run-from')
    await expect.poll(async () => (await execs(api, wf.id)).length).toBe(2)
    ex = await settled(api, wf.id)
    expect(ex.id).not.toBe(runTo)
    expect(ex.node_data.C[0].json).toMatchObject({ a: '1', b: '2', c: '3' })
    expect(ex.node_data.Start).toBeUndefined() // upstream nodes are not re-run
    expect(ex.node_data.A).toBeUndefined()
    // run from B: B and C run again
    await ctx(page, 'B', 'ctx-run-from')
    await expect.poll(async () => (await execs(api, wf.id)).length).toBe(3)
    ex = await settled(api, wf.id)
    expect(Object.keys(ex.node_data).sort()).toEqual(['B', 'C'])
    await snap(page, 'run-from-here')
  })

  test('T14.5 open a past run in the editor and pin its data', async ({ page, api, s }) => {
    const slug = await projectSlug(api, 'proj_default')
    const failedId = await seededFailedRun(api, s)
    await page.goto(`${s.studioUrl}/p/${slug}/runs`)
    await page.getByTestId('runs-env-all').click()
    await page.locator(`[data-testid="run-row"][data-run-id="${failedId}"]`).dblclick()
    await expect(page.getByTestId('run-open-in-editor')).toBeVisible()
    await page.getByTestId('run-open-in-editor').click()
    await expect(page).toHaveURL(new RegExp(`/workflows/${s.refund_workflow_id}\\?execution=${failedId}`))
    const banner = page.getByTestId('viewing-run-banner')
    await expect(banner).toBeVisible()
    await expect(banner).toContainText(failedId)
    await snap(page, 'viewing-run')
    // nodes before the failure have output
    await openNdv(page, 'Normalize')
    await expect(page.getByTestId('ndv-status')).toContainText('Success')
    await outputJson(page)
    await expect(page.getByTestId('ndv-modal')).toContainText('ord_1042')
    await closeNdv(page)
    // the failed node shows its error
    await openNdv(page, 'Risk API')
    await expect(page.getByTestId('ndv-status')).toContainText('Error')
    await snap(page, 'viewing-run-failed-node')
    await closeNdv(page)
    // select a node and pin its output
    await nodeEl(page, 'Normalize').click()
    await page.getByTestId('viewing-run-pin').click()
    await openNdv(page, 'Normalize')
    await expect(page.getByTestId('ndv-output-pin')).toHaveAttribute('aria-label', 'Unpin data')
    await closeNdv(page)
    // back to live clears the view
    await page.getByTestId('viewing-run-back-to-live').click()
    await expect(banner).toBeHidden()
    await expect(page).not.toHaveURL(/execution=/)
    await openNdv(page, 'Risk API')
    await expect(page.getByTestId('ndv-status')).toHaveCount(0)
    await closeNdv(page)
  })

  test('T14.6 history: diff markers on the canvas, then restore', async ({ page, api, s }) => {
    const wf = await seedWorkflow(api, p, {
      name: 'History',
      nodes: [
        { name: 'Start', type: 'Start', parameters: {}, position: { x: 0, y: 0 } },
        { name: 'Http', type: 'HttpRequest', label: 'Call API', parameters: { method: 'GET', url: s.fake_api_ok_url, responseType: 'json' }, position: { x: 240, y: 0 } },
        setNode('C', 'Step C', 'c', '3', 480),
      ],
      connections: [link('Start', 'Http'), link('Http', 'C')],
    })
    await openEditor(page, s, p.slug, wf.id)
    await page.getByTestId('editor-publish').click()
    await expect(page.getByTestId('editor-status')).toContainText(/published|active|live/i)
    // edit: change Http's URL (changed), delete C (removed), duplicate Http (added)
    await openNdv(page, 'Http')
    await setUrl(page, `${s.fake_api_ok_url}?edited=1`)
    await closeNdv(page)
    await nodeEl(page, 'C').click()
    await page.keyboard.press('Delete')
    await expect(nodeEl(page, 'C')).toHaveCount(0)
    await nodeEl(page, 'Http').click()
    await page.keyboard.press('ControlOrMeta+d')
    await expect(page.locator('.react-flow__node')).toHaveCount(3) // Start, Http, the copy
    await page.getByTestId('editor-save').click()
    await expect(page.getByTestId('editor-saved')).toHaveText('Saved')
    await page.getByTestId('navbar-history').click()
    await expect(page.getByTestId('history-sheet')).toBeVisible()
    await page.getByTestId('history-version-1').click()
    await expect(page.locator('[data-diff="removed"]')).toHaveCount(1) // C is in v1 only, shown as a ghost
    await expect(page.locator('[data-diff="added"]')).toHaveCount(1) // the copy
    await expect(page.locator('[data-diff="changed"]')).toHaveCount(1) // Http
    await snap(page, 'history-diff')
    await page.getByTestId('history-restore').click()
    await expect(nodeEl(page, 'C')).toHaveCount(1)
    await expect(page.locator('.react-flow__node')).toHaveCount(3) // Start, Http, C: the edit is gone
    const d = (await api.get<{ definition: { nodes: Array<{ name: string; parameters: { url?: string } }> } }>(`/workflows/${wf.id}`)).definition
    expect(d.nodes.map((n) => n.name).sort()).toEqual(['C', 'Http', 'Start'])
    expect(d.nodes.find((n) => n.name === 'Http')!.parameters.url).toBe(s.fake_api_ok_url)
    await snap(page, 'history-restored')
  })

  test('T14.7 command palette: Tidy up moves the nodes', async ({ page, api, s }) => {
    const wf = await seedWorkflow(api, p, {
      ...chain('Tidy'),
      nodes: chain('x').nodes.map((n, i) => ({ ...n, position: { x: i * 37, y: i % 2 === 0 ? 0 : 350 - i * 20 } })),
    })
    await openEditor(page, s, p.slug, wf.id)
    const positions = async () =>
      page.locator('.react-flow__node').evaluateAll((els) => Object.fromEntries(els.map((e) => [e.getAttribute('data-id'), (e as HTMLElement).style.transform])))
    const before = await positions()
    await page.locator('.react-flow__pane').click({ position: { x: 600, y: 600 } })
    await page.keyboard.press('ControlOrMeta+k')
    await expect(page.getByTestId('command-palette')).toBeVisible()
    await page.getByTestId('command-palette-input').fill('tidy')
    await snap(page, 'command-palette')
    await page.getByTestId('cmd-tidy-up').click()
    await expect(page.getByTestId('command-palette')).toBeHidden()
    await expect.poll(async () => JSON.stringify(await positions())).not.toBe(JSON.stringify(before))
    const after = await positions()
    // laid out left to right: x strictly increases along the chain
    const xs = ['Start', 'A', 'B', 'C'].map((n) => Number(/translate\((-?[\d.]+)px/.exec(after[n])![1]))
    expect(xs).toEqual([...xs].sort((m, n) => m - n))
    expect(new Set(xs).size).toBe(4)
    await snap(page, 'tidy-up')
  })
})

test('T14.8 the demo: debug the failed Refund triage run end to end', async ({ page, api, s }) => {
  const wfId = s.refund_workflow_id
  const slug = await projectSlug(api, 'proj_default')
  const failedId = await seededFailedRun(api, s)
  const httpDraft = (url: string) => api.get<{ definition: { nodes: Array<{ name: string; parameters: { url?: string } }> } }>(`/workflows/${wfId}`).then((w) => w.definition.nodes.find((n) => n.name === 'Risk API')!.parameters.url === url)
  // v1 = the broken workflow (HTTP node on the failing route), published so History has a version
  const cur = (await api.get<{ definition: { nodes: Array<{ name: string; parameters: Record<string, unknown> }>; connections: unknown[] } }>(`/workflows/${wfId}`)).definition
  const broken = cur.nodes.map((n) => (n.name === 'Risk API' ? { ...n, parameters: { ...n.parameters, url: s.fake_api_fail_url } } : n))
  await api.put(`/workflows/${wfId}`, { nodes: broken, connections: cur.connections })
  await api.post(`/workflows/${wfId}/publish`, { message: 'v1 broken' })

  // 1. open the failed run in the editor
  await page.goto(`${s.studioUrl}/p/${slug}/runs`)
  await page.getByTestId('runs-env-all').click()
  await page.locator(`[data-testid="run-row"][data-run-id="${failedId}"]`).dblclick()
  await page.getByTestId('run-open-in-editor').click()
  await expect(page.getByTestId('viewing-run-banner')).toBeVisible()
  // 2. data on every node that ran (Webhook, Normalize, Amount over 500), error on the HTTP node
  for (const name of ['Webhook', 'Normalize', 'Amount over 500']) {
    await openNdv(page, name)
    await expect(page.getByTestId('ndv-status')).toContainText('Success')
    await closeNdv(page)
  }
  await openNdv(page, 'Risk API')
  await expect(page.getByTestId('ndv-status')).toContainText('Error')
  await snap(page, 'demo-1-failed-run')
  // 3. fix the HTTP node: point it at the working route
  await page.getByTestId('ndv-tab-parameters').click()
  await setUrl(page, s.fake_api_ok_url)
  // 4. test that step alone, from the last run's input
  const execsBefore = (await execs(api, wfId)).length
  await page.getByTestId('ndv-test-source-last-run').click()
  await page.getByTestId('ndv-test-step').click()
  await expect.poll(async () => (await execs(api, wfId)).length).toBe(execsBefore + 1)
  let ex = await settled(api, wfId)
  expect(ex.errors).toEqual([])
  expect(Object.keys(ex.node_data)).toEqual(['Risk API'])
  expect(ex.node_data['Risk API'][0].json).toMatchObject({ statusCode: 200, body: { ok: true } })
  await expect(page.getByTestId('ndv-status')).toContainText('Success')
  await snap(page, 'demo-2-fixed-step')
  await closeNdv(page)
  // 5. run from that node: it and the branch below it run, the upstream outputs are reused
  await ctx(page, 'Risk API', 'ctx-run-from')
  await expect.poll(async () => (await execs(api, wfId)).length).toBe(execsBefore + 2)
  ex = await settled(api, wfId)
  expect(ex.status).toBe('success')
  expect(Object.keys(ex.node_data).sort()).toEqual(['Log escalated', 'Risk API'])
  await snap(page, 'demo-3-run-from')
  // 6. restore the previous version from History
  await page.getByTestId('editor-save').click()
  await page.getByTestId('navbar-history').click()
  await page.getByTestId('history-version-1').click()
  await expect(page.locator('[data-diff="changed"]')).toHaveCount(1)
  await snap(page, 'demo-4-history')
  await page.getByTestId('history-restore').click()
  await expect.poll(() => httpDraft(s.fake_api_fail_url)).toBe(true)
  await snap(page, 'demo-5-restored')
  // leave the shared workflow on the working route for anything that runs after
  const after = (await api.get<{ definition: { nodes: Array<{ name: string; parameters: Record<string, unknown> }>; connections: unknown[] } }>(`/workflows/${wfId}`)).definition
  await api.put(`/workflows/${wfId}`, { nodes: after.nodes.map((n) => (n.name === 'Risk API' ? { ...n, parameters: { ...n.parameters, url: s.fake_api_ok_url } } : n)), connections: after.connections })
})

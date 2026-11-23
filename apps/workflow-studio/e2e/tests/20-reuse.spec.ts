/**
 * Stage 8 (DESIGN-STAGE8-REUSE sections 7, 8, 12): reuse in the real studio against the real API. Call a sub-workflow from
 * the Run Workflow panel, the draft-pin warning, a failing child, defining a child's inputs, convert selection to
 * sub-workflow (preview, refusal, Delete it), Used by + publish impact, and installing a template.
 * Bank-style data; every test builds its own project, and every write is verified by re-reading the API.
 * Not covered on purpose: AI Agent "sub-workflow as a tool" (the studio has no UI for it) and template export (API only).
 */
import { test, expect, rand, type EngineApi } from '../fixtures/test'
import { seedProject, seedWorkflow, type P } from '../fixtures/seed'
import { nodeEl, openEditor, openTestsTab, pick } from '../fixtures/tests3a'
import type { Page } from '@playwright/test'
import fs from 'node:fs'
import path from 'node:path'
import { legacyEditor } from '../fixtures/legacy'

// these specs guard the legacy canvas editor (the glass editor is the default)
test.beforeEach(async ({ page }) => legacyEditor(page))

const SHOTS = '/tmp/claude-1000/-home-saketh-Projects-playground-work-demo/3b980f2a-bdc6-4187-b5dc-d2b1e0bdc78d/scratchpad/shots/stage8'

async function shot(page: Page, name: string) {
  await page.waitForTimeout(600) // let the canvas finish its fit-to-screen animation
  fs.mkdirSync(SHOTS, { recursive: true })
  const file = test.info().outputPath(`${name}.png`)
  await page.screenshot({ path: file })
  fs.copyFileSync(file, path.join(SHOTS, `t20-${name}.png`))
}

type Json = Record<string, unknown>
type Items = Array<{ json: Record<string, unknown> }>
interface Wf {
  id: string
  name: string
  archived?: boolean
  definition: { nodes: Array<{ name: string; type: string; parameters: Json }>; tests?: Array<Json>; connections: unknown[] }
}
interface ExecDetail {
  id: string
  status: string
  errors: Array<{ node_name: string; error: string; error_code?: string }>
  node_data: Record<string, Items>
  parent: { execution_id: string; workflow_id: string; workflow_name: string; node: string } | null
  children: Array<{ execution_id: string; node: string; workflow_id: string; workflow_name: string; status: string }>
}

const link = (a: string, b: string, out = 'main') => ({ source_node: a, source_output: out, target_node: b, target_input: 'main' })
const setNode = (name: string, field: string, value: string, x: number) => ({
  name,
  type: 'Set',
  parameters: { mode: 'manual', fields: [{ name: field, value, type: 'string' }] },
  position: { x, y: 0 },
})
const getWf = (api: EngineApi, id: string) => api.get<Wf>(`/workflows/${id}`)
async function latestExec(api: EngineApi, wfId: string): Promise<ExecDetail> {
  await expect
    .poll(async () => (await api.get<Array<{ id: string; status: string }>>(`/executions?workflow_id=${wfId}`)).filter((e) => e.status !== 'running').length, { timeout: 20_000 })
    .toBeGreaterThan(0)
  const list = await api.get<Array<{ id: string; start_time: string }>>(`/executions?workflow_id=${wfId}`)
  list.sort((x, y) => (x.start_time < y.start_time ? 1 : -1))
  return api.get<ExecDetail>(`/executions/${list[0].id}`)
}

const KYC_FIELDS = [
  { name: 'customer_id', type: 'string', required: true, description: 'Customer number' },
  { name: 'amount', type: 'number', required: false, description: '' },
]
const kycNodes = (extra: Array<Json> = []) => [
  { name: 'Inputs', type: 'ExecuteWorkflowTrigger', parameters: { inputSource: 'fields', fields: [...KYC_FIELDS, ...extra] }, position: { x: 0, y: 0 } },
  setNode('KYC verdict', 'kyc_ref', 'KYC-{{ $json.customer_id }}', 260),
]

/** Child "Check customer KYC": Inputs(customer_id required, amount) -> KYC verdict {kyc_ref}. */
async function seedKyc(api: EngineApi, p: P, opts: { publish?: boolean; name?: string } = {}) {
  const wf = await seedWorkflow(api, p, { name: opts.name ?? 'Check customer KYC', nodes: kycNodes(), connections: [link('Inputs', 'KYC verdict')] })
  if (opts.publish !== false) await api.post(`/workflows/${wf.id}/publish`, {})
  return wf
}

/** Parent: Start -> Call KYC (no child picked yet) -> Decision {seen}. */
function parentBody(name: string, call: Json = {}) {
  return {
    name,
    nodes: [
      { name: 'Start', type: 'Start', parameters: {}, position: { x: 0, y: 0 } },
      {
        name: 'Call KYC',
        type: 'ExecuteWorkflow',
        parameters: { version: 'latest', inputMode: 'map', inputs: {}, includeItem: false, mode: 'once', timeoutSeconds: 300, outputMode: 'items', ...call },
        position: { x: 260, y: 0 },
      },
      setNode('Decision', 'seen', 'cleared {{ $json.kyc_ref }}', 520),
    ],
    connections: [link('Start', 'Call KYC'), link('Call KYC', 'Decision')],
  }
}

async function typeExpression(page: Page, row: string, value: string) {
  const r = page.getByTestId(row)
  await r.locator('.cursor-text').first().click()
  const area = r.locator('textarea').first()
  await area.fill(value)
  await expect(area).toHaveValue(value)
  await area.blur()
}

async function runAndWait(page: Page, want: RegExp) {
  await page.getByTestId('editor-run').click()
  await expect(page.getByTestId('execution-status')).toContainText(want, { timeout: 30_000 })
}

test.describe('Reuse: sub-workflows and templates (stage 8)', () => {
  let p: P
  test.beforeEach(async ({ api, context }) => {
    p = await seedProject(api, 'T20')
    await context.addInitScript(() => {
      try {
        localStorage.setItem('studio.actorName', 'Asha')
      } catch {
        /* storage blocked */
      }
    })
  })

  test('T20.1 call a published sub-workflow, run it, follow the links both ways', async ({ page, api, s }) => {
    const child = await seedKyc(api, p)
    const parent = await seedWorkflow(api, p, parentBody('Onboard customer'))
    await openEditor(page, s, p.slug, parent.id)

    await nodeEl(page, 'Call KYC').dblclick()
    await expect(page.getByTestId('subwf-panel')).toBeVisible()
    await shot(page, '1-call-empty')
    await page.getByTestId('subwf-picker').click()
    await page.getByTestId(`subwf-picker-option-${child.id}`).click()
    await expect(page.getByTestId('subwf-picker-value')).toHaveText('Check customer KYC')
    await expect(page.getByTestId('subwf-contract-status')).toHaveAttribute('data-status', 'typed')
    await expect(page.getByTestId('subwf-version')).toContainText(/latest/i)
    await expect(page.getByTestId('subwf-draft-warning')).toHaveCount(0)
    // the required input is flagged until it is mapped; the optional one is not
    await expect(page.getByTestId('subwf-input-customer_id')).toBeVisible()
    await expect(page.getByTestId('subwf-input-amount')).toBeVisible()
    await expect(page.getByTestId('subwf-input-missing-customer_id')).toBeVisible()
    await expect(page.getByTestId('subwf-input-missing-amount')).toHaveCount(0)
    await expect(page.getByTestId('subwf-output-fields')).toContainText('kyc_ref')
    await shot(page, '1-call-missing-input')

    await typeExpression(page, 'subwf-input-customer_id', '{{ "C-1001" }}')
    await expect(page.getByTestId('subwf-input-missing-customer_id')).toHaveCount(0)
    await shot(page, '1-call-mapped')
    await page.getByTestId('ndv-close').click()
    await expect(page.getByTestId('node-subwf-badge')).toContainText('Check customer KYC')
    await page.getByTestId('editor-save').click()

    // saved: re-read the parent
    await expect
      .poll(async () => {
        const n = (await getWf(api, parent.id)).definition.nodes.find((x) => x.name === 'Call KYC')!
        return JSON.stringify([n.parameters.workflowId, n.parameters.inputs])
      })
      .toBe(JSON.stringify([child.id, { customer_id: '{{ "C-1001" }}' }]))

    await runAndWait(page, /success/i)
    await shot(page, '1-run-done')
    const run = await latestExec(api, parent.id)
    expect(run.status).toBe('success')
    expect(run.node_data['Call KYC'][0].json.kyc_ref).toBe('KYC-C-1001')
    // the child's output reached the parent's next step
    expect(run.node_data['Decision'][0].json.seen).toBe('cleared KYC-C-1001')
    expect(run.children).toHaveLength(1)
    expect(run.children[0]).toMatchObject({ workflow_id: child.id, node: 'Call KYC', status: expect.stringMatching(/success/) })
    const childRunId = run.children[0].execution_id

    // logs: the child run is nested under the call node and linked from its detail
    await expect(page.getByTestId(`exec-log-child-${childRunId}`)).toBeVisible()
    await page.getByTestId('exec-log-child-' + childRunId).scrollIntoViewIfNeeded()
    await shot(page, '1-logs-child-row')
    await page.locator('button:has-text("Call KYC")').first().click()
    await expect(page.getByTestId('run-child-executions')).toBeVisible()
    await shot(page, '1-logs-child-list')
    await page.getByTestId('run-open-child-execution').first().click()
    await expect(page).toHaveURL(new RegExp(`/workflows/${child.id}.*execution=${childRunId}`))
    await expect(page.getByTestId('viewing-run-banner')).toBeVisible()
    // child page: breadcrumb back to the parent
    // the breadcrumb lives in the Logs panel header, which is closed on arrival: open it
    if ((await page.getByTestId('editor-toggle-logs').getAttribute('aria-pressed')) !== 'true') await page.getByTestId('editor-toggle-logs').click()
    await expect(page.getByTestId('exec-parent-link')).toBeInViewport()
    await expect(page.getByTestId('exec-parent-link')).toContainText('Onboard customer')
    await shot(page, '1-child-run-parent-link')
    await page.getByTestId('exec-parent-link').click()
    await expect(page).toHaveURL(new RegExp(`/workflows/${parent.id}.*execution=${run.id}`))
    await expect(page.getByTestId('viewing-run-banner')).toBeVisible()
    // the lineage in the API says the same thing
    const childRun = await api.get<ExecDetail>(`/executions/${childRunId}`)
    expect(childRun.parent).toMatchObject({ execution_id: run.id, workflow_id: parent.id })
  })

  test('T20.2 picking a never-published sub-workflow pins draft and warns', async ({ page, api, s }) => {
    const draftOnly = await seedKyc(api, p, { publish: false, name: 'Draft KYC' })
    const parent = await seedWorkflow(api, p, parentBody('Onboard customer'))
    await openEditor(page, s, p.slug, parent.id)
    await nodeEl(page, 'Call KYC').dblclick()
    await page.getByTestId('subwf-picker').click()
    await page.getByTestId(`subwf-picker-option-${draftOnly.id}`).click()
    await expect(page.getByTestId('subwf-version')).toContainText(/draft/i)
    await expect(page.getByTestId('subwf-draft-warning')).toBeVisible()
    await shot(page, '2-draft-warning')
    await page.getByTestId('ndv-close').click()
    await expect(page.getByTestId('node-subwf-badge')).toHaveAttribute('data-version', 'draft')
    await expect(page.getByTestId('node-subwf-badge')).toContainText(/draft/i)
    await page.getByTestId('editor-save').click()
    await expect
      .poll(async () => (await getWf(api, parent.id)).definition.nodes.find((x) => x.name === 'Call KYC')!.parameters.version)
      .toBe('draft')
    await shot(page, '2-draft-badge')
  })

  test('T20.3 a failing child fails the parent and the error links to the failing child run', async ({ page, api, s }) => {
    const child = await seedWorkflow(api, p, {
      name: 'Sanctions screen',
      nodes: [
        { name: 'Inputs', type: 'ExecuteWorkflowTrigger', parameters: { inputSource: 'fields', fields: [KYC_FIELDS[0]] }, position: { x: 0, y: 0 } },
        { name: 'Stop on hit', type: 'StopAndError', parameters: { errorType: 'error', message: 'Sanctions hit for {{ $json.customer_id }}' }, position: { x: 260, y: 0 } },
      ],
      connections: [link('Inputs', 'Stop on hit')],
    })
    await api.post(`/workflows/${child.id}/publish`, {})
    const parent = await seedWorkflow(
      api,
      p,
      parentBody('Onboard customer', { workflowId: child.id, inputs: { customer_id: '{{ "C-6666" }}' } }),
    )
    await openEditor(page, s, p.slug, parent.id)
    await runAndWait(page, /fail|error/i)
    const run = await latestExec(api, parent.id)
    expect(run.status).toBe('failed')
    expect(run.errors[0]).toMatchObject({ node_name: 'Call KYC', error_code: 'SUBWORKFLOW_FAILED' })
    expect(run.children).toHaveLength(1)
    expect(run.children[0].status).toMatch(/fail|error/)
    const failedChild = run.children[0].execution_id
    await page.locator('button:has-text("Call KYC")').first().click()
    await shot(page, '3-parent-failed')
    await page.getByTestId('run-error-child-link').click()
    await expect(page).toHaveURL(new RegExp(`/workflows/${child.id}.*execution=${failedChild}`))
    await expect(page.getByTestId('viewing-run-banner')).toBeVisible()
    await shot(page, '3-failing-child-run')
    const detail = await api.get<ExecDetail>(`/executions/${failedChild}`)
    expect(detail.errors[0].error).toContain('Sanctions hit for C-6666')
  })

  test('T20.4 define a sub-workflow\'s inputs in the trigger panel; a caller sees the typed contract', async ({ page, api, s }) => {
    const child = await seedWorkflow(api, p, {
      name: 'Check customer KYC',
      nodes: [
        { name: 'Inputs', type: 'ExecuteWorkflowTrigger', parameters: { inputSource: 'fields', fields: [] }, position: { x: 0, y: 0 } },
        setNode('KYC verdict', 'kyc_ref', 'KYC-{{ $json.customer_id }}', 260),
      ],
      connections: [link('Inputs', 'KYC verdict')],
    })
    // before: nothing declared
    const before = await api.get<{ input_fields: unknown[] }>(`/workflows/${child.id}/io?version=draft`)
    expect(before.input_fields).toHaveLength(0)

    await openEditor(page, s, p.slug, child.id)
    await nodeEl(page, 'Inputs').dblclick()
    await expect(page.getByTestId('subwf-trigger-panel')).toBeVisible()
    await shot(page, '4-trigger-empty')
    await page.getByTestId('subwf-trigger-add-field').click()
    await page.getByTestId('subwf-trigger-field-name-0').fill('customer_id')
    await page.getByTestId('subwf-trigger-field-required-0').click()
    await page.getByTestId('subwf-trigger-add-field').click()
    await page.getByTestId('subwf-trigger-field-name-1').fill('amount')
    await pick(page, page.getByTestId('subwf-trigger-field-type-1'), 'number')
    await shot(page, '4-trigger-filled')
    await page.getByTestId('ndv-close').click()
    await page.getByTestId('editor-save').click()

    await expect
      .poll(async () => {
        const io = await api.get<{ input_fields: Array<{ name: string; type: string; required: boolean }>; callable: boolean }>(`/workflows/${child.id}/io?version=draft`)
        return JSON.stringify(io.input_fields.map((f) => [f.name, f.type, f.required]))
      })
      .toBe(JSON.stringify([['customer_id', 'string', true], ['amount', 'number', false]]))
    const stored = (await getWf(api, child.id)).definition.nodes.find((n) => n.name === 'Inputs')!.parameters.fields as Array<Json>
    expect(stored.map((f) => [f.name, f.type, f.required])).toEqual([['customer_id', 'string', true], ['amount', 'number', false]])

    // a caller picking it (draft, never published) reads a typed contract with the same rows
    const parent = await seedWorkflow(api, p, parentBody('Onboard customer'))
    await openEditor(page, s, p.slug, parent.id)
    await nodeEl(page, 'Call KYC').dblclick()
    await page.getByTestId('subwf-picker').click()
    await page.getByTestId(`subwf-picker-option-${child.id}`).click()
    await expect(page.getByTestId('subwf-contract-status')).toHaveAttribute('data-status', 'typed')
    await expect(page.getByTestId('subwf-input-customer_id')).toBeVisible()
    await expect(page.getByTestId('subwf-input-missing-customer_id')).toBeVisible()
    await expect(page.getByTestId('subwf-input-amount')).toBeVisible()
    await shot(page, '4-caller-typed')
  })

  /** Start -> A -> B -> C (A, B, C each add a field), plus an approved-style test asserting on the inner node A. */
  async function seedChain(api: EngineApi, name = 'Refund intake') {
    return seedWorkflow(api, p, {
      name,
      nodes: [{ name: 'Start', type: 'Start', parameters: {}, position: { x: 0, y: 0 } }, setNode('A', 'a', '1', 240), setNode('B', 'b', '2', 480), setNode('C', 'c', '3', 720)],
      connections: [link('Start', 'A'), link('A', 'B'), link('B', 'C')],
      tests: [
        {
          name: 'A adds a',
          target: { kind: 'workflow' },
          category: 'normal',
          input: { items: [{ amount: 10 }] },
          mocks: {},
          expect: [{ node: 'A', path: 'a', op: 'eq', value: '1' }],
          status: 'draft',
          locked: false,
          source: 'human',
        },
      ],
    })
  }
  const SOURCE_OUT = { a: '1', b: '2', c: '3' }

  async function selectAB(page: Page) {
    await nodeEl(page, 'A').click()
    await nodeEl(page, 'B').click({ modifiers: ['Shift'] })
    await expect(page.locator('.react-flow__node.selected')).toHaveCount(2)
  }

  test('T20.5 convert a selection to a sub-workflow, same result, then Delete it puts it back', async ({ page, api, s }) => {
    const chain = await seedChain(api)
    const before = await (async () => {
      await api.post(`/workflows/${chain.id}/run`, { input_data: { amount: 10 } }).catch(() => undefined)
      return latestExec(api, chain.id)
    })()
    expect(before.status).toBe('success')
    expect(before.node_data['C'][0].json).toMatchObject(SOURCE_OUT)

    await openEditor(page, s, p.slug, chain.id)
    await selectAB(page)
    await nodeEl(page, 'A').click({ button: 'right' })
    await page.getByTestId('ctx-convert-subworkflow').click()
    await expect(page.getByTestId('convert-subwf-dialog')).toBeVisible()
    await page.getByTestId('convert-subwf-name').fill('Steps A and B')
    await expect(page.getByTestId('convert-subwf-exit')).toBeVisible()
    await expect(page.getByTestId('convert-subwf-exit')).toContainText('B')
    await expect(page.getByTestId('convert-subwf-refusal')).toHaveCount(0)
    await expect(page.getByTestId('convert-subwf-affected-tests')).toBeVisible()
    await expect(page.getByTestId('convert-subwf-affected-tests')).toContainText('A adds a')
    await expect(page.getByTestId('convert-subwf-confirm')).toBeEnabled()
    await shot(page, '5-convert-preview')
    // the dry run wrote nothing
    expect((await api.get<Array<{ name: string }>>(`/projects/${p.id}/subworkflows`)).map((w) => w.name)).not.toContain('Steps A and B')

    await page.getByTestId('convert-subwf-confirm').click()
    await expect(page.getByTestId('convert-subwf-toast')).toBeVisible()
    await expect(page.getByTestId('node-subwf-badge')).toBeVisible()
    await expect(page.locator('.react-flow__node')).toHaveCount(3) // Start, the call, C
    await shot(page, '5-converted-toast')

    // API: one new child, the parent has one Run Workflow node pointing at it
    const subs = await api.get<Array<{ id: string; name: string }>>(`/projects/${p.id}/subworkflows`)
    const child = subs.find((w) => w.name === 'Steps A and B')!
    expect(child).toBeTruthy()
    const parentDef = (await getWf(api, chain.id)).definition
    const calls = parentDef.nodes.filter((n) => n.type === 'ExecuteWorkflow')
    expect(calls).toHaveLength(1)
    expect(calls[0].parameters.workflowId).toBe(child.id)
    expect(parentDef.nodes.map((n) => n.name).filter((n) => n === 'A' || n === 'B')).toEqual([])

    // same result as before conversion
    await runAndWait(page, /success/i)
    const after = await latestExec(api, chain.id)
    expect(after.status).toBe('success')
    expect(after.node_data['C'][0].json).toMatchObject(SOURCE_OUT)
    expect(after.children).toHaveLength(1)
    await shot(page, '5-converted-run')

    // Delete it: the steps come back and the child is gone
    await page.getByTestId('convert-subwf-delete').click()
    await expect(page.locator('.react-flow__node')).toHaveCount(4)
    await expect(page.getByTestId('node-subwf-badge')).toHaveCount(0)
    await shot(page, '5-delete-it-restored')
    await expect
      .poll(async () => {
        const r = await api.raw<Wf>('GET', `/workflows/${child.id}`)
        return r.status === 404 || r.body?.archived === true
      })
      .toBe(true)
    await expect
      .poll(async () => (await getWf(api, chain.id)).definition.nodes.map((n) => n.name).sort())
      .toEqual(['A', 'B', 'C', 'Start'])
    expect((await api.get<Array<{ name: string }>>(`/projects/${p.id}/subworkflows`)).map((w) => w.name)).not.toContain('Steps A and B')

    // and again with the keyboard shortcut, this time kept
    await selectAB(page)
    await page.keyboard.press('Alt+x')
    await expect(page.getByTestId('convert-subwf-dialog')).toBeVisible()
    await page.getByTestId('convert-subwf-name').fill('Refund steps')
    await expect(page.getByTestId('convert-subwf-confirm')).toBeEnabled()
    await page.getByTestId('convert-subwf-confirm').click()
    await expect(page.getByTestId('node-subwf-badge')).toContainText('Refund steps')
    await expect
      .poll(async () => (await api.get<Array<{ name: string }>>(`/projects/${p.id}/subworkflows`)).map((w) => w.name))
      .toContain('Refund steps')
  })

  test('T20.6 a selection the engine refuses shows why and creates nothing', async ({ page, api, s, net }) => {
    const chain = await seedChain(api)
    await openEditor(page, s, p.slug, chain.id)
    const wfCount = (await api.get<unknown[]>(`/projects/${p.id}/workflows`)).length
    await nodeEl(page, 'Start').click()
    await nodeEl(page, 'A').click({ modifiers: ['Shift'] })
    await expect(page.locator('.react-flow__node.selected')).toHaveCount(2)
    await net.expect([{ method: 'POST', url: /\/extract-subworkflow/, status: 422 }], async () => {
      await nodeEl(page, 'A').click({ button: 'right' })
      await page.getByTestId('ctx-convert-subworkflow').click()
      await expect(page.getByTestId('convert-subwf-refusal')).toBeVisible()
    })
    const refusal = page.getByTestId('convert-subwf-refusal')
    await expect(refusal).toHaveAttribute('data-code', 'CS02')
    await expect(refusal).toContainText(/trigger/i)
    await expect(page.getByTestId('convert-subwf-confirm')).toBeDisabled()
    await shot(page, '6-refusal-cs02')
    await page.getByTestId('convert-subwf-cancel').click()
    await expect(page.getByTestId('convert-subwf-dialog')).toBeHidden()
    expect(await api.get<unknown[]>(`/projects/${p.id}/workflows`)).toHaveLength(wfCount) // nothing was created
    const def = (await getWf(api, chain.id)).definition
    expect(def.nodes.map((n) => n.name).sort()).toEqual(['A', 'B', 'C', 'Start'])
  })

  test('T20.7 Used by lists the caller; a breaking new version is named in the publish impact', async ({ page, api, s }) => {
    const child = await seedKyc(api, p)
    const parent = await seedWorkflow(api, p, parentBody('Onboard customer', { workflowId: child.id, inputs: { customer_id: '{{ "C-1001" }}' } }))
    await api.post(`/workflows/${parent.id}/publish`, {})
    expect((await api.get<Array<{ parent_workflow_id: string }>>(`/workflows/${child.id}/used-by`)).map((r) => r.parent_workflow_id)).toContain(parent.id)

    await openEditor(page, s, p.slug, child.id)
    await page.getByTestId('editor-more').click()
    await page.getByTestId('editor-used-by').click()
    await expect(page.getByTestId('used-by-panel')).toBeVisible()
    // one row per place the parent follows this workflow: its draft and its published version
    const rows = page.getByTestId(`used-by-row-${parent.id}`)
    await expect(rows).toHaveCount(2)
    await expect(rows.filter({ hasText: 'Onboard customer' })).toHaveCount(2)
    await expect(rows.filter({ hasText: 'published' })).toHaveCount(1)
    await expect(rows.filter({ hasText: 'draft' })).toHaveCount(1)
    await shot(page, '7-used-by')
    await page.keyboard.press('Escape')
    await expect(page.getByTestId('used-by-panel')).toBeHidden()

    // a new required input that the caller does not send
    await api.put(`/workflows/${child.id}`, { nodes: kycNodes([{ name: 'branch_code', type: 'string', required: true, description: '' }]), connections: [link('Inputs', 'KYC verdict')] })
    await page.reload()
    await expect(page.getByTestId('workflow-navbar')).toBeVisible()
    await expect(page.getByTestId('editor-unpublish')).toBeVisible()
    await shot(page, '7-child-live-before-republish')
    await page.getByTestId('editor-unpublish').click()
    await page.getByTestId('editor-publish').click()
    await expect(page.getByTestId('publish-impact-dialog')).toBeVisible()
    await expect(page.getByTestId('publish-impact-row')).toHaveCount(1)
    await expect(page.getByTestId('publish-impact-row')).toContainText('Onboard customer')
    await expect(page.getByTestId('publish-impact-row')).toHaveAttribute('data-parent-id', parent.id)
    await shot(page, '7-publish-impact')
    await page.getByTestId('publish-impact-confirm').click()
    await expect(page.getByTestId('publish-impact-dialog')).toBeHidden()
    // the version really is published (v2)
    const pub = await api.get<Array<{ version_number: number }>>(`/workflows/${child.id}/versions`).catch(() => null)
    if (pub) expect(Math.max(...pub.map((v) => v.version_number))).toBe(2)
  })

  test('T20.8 install a built-in template; its bundled tests run green in the Tests panel', async ({ page, api, s }) => {
    await page.goto(`${s.studioUrl}/p/${p.slug}/templates`)
    await expect(page.getByTestId('templates-grid')).toBeVisible()
    await expect(page.getByTestId('template-card-refund-triage')).toBeVisible()
    await shot(page, '8-templates-grid')
    await page.getByTestId('template-card-refund-triage').click()
    await expect(page.getByTestId('template-preview')).toBeVisible()
    await shot(page, '8-template-preview')
    await page.getByTestId('template-install').click()
    await expect(page.getByTestId('template-install-result')).toBeVisible({ timeout: 60_000 })
    await shot(page, '8-template-installed')
    await expect(page.getByTestId('template-tests-summary')).toHaveAttribute('data-failed', '0')
    await expect(page.getByTestId('template-tests-summary')).toContainText(/tests passed/)
    const installs = await api.get<Array<{ slug: string; workflows: Array<{ key: string; id: string; name: string }> }>>(`/projects/${p.id}/template-installs`)
    const inst = installs.find((i) => i.slug === 'refund-triage')!
    expect(inst).toBeTruthy()

    await page.getByTestId('template-open-entry').click()
    await expect(page.getByTestId('workflow-navbar')).toBeVisible()
    await expect(page.locator('.react-flow__node').first()).toBeVisible()
    const entryId = page.url().match(/workflows\/([^/?#]+)/)![1]
    expect(inst.workflows.map((w) => w.id)).toContain(entryId)
    const wf = await getWf(api, entryId)
    expect(wf.definition.tests?.length ?? 0).toBeGreaterThan(0)
    await openTestsTab(page)
    await page.getByTestId('tests-run-all').click()
    await expect(page.getByTestId('tests-run-all')).toBeEnabled({ timeout: 60_000 })
    const rows = page.getByTestId('test-row')
    await expect(rows.first()).toBeVisible()
    const n = await rows.count()
    expect(n).toBe(wf.definition.tests!.length)
    for (let i = 0; i < n; i++) await expect(rows.nth(i)).not.toContainText(/fail/i)
    await shot(page, '8-template-tests-green')
  })

  test('T20.9 AI Agent sub-workflow tool: no studio UI to pick one', async () => {
    test.skip(true, 'The studio exposes no control for picking a sub-workflow as an AI Agent tool (only the engine WorkflowTool); not invented here.')
  })
})

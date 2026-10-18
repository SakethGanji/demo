/**
 * Stage 6 (DESIGN-STAGE6-AI-BUILDER section 12, E2E): the AI builder in the real UI against the real API and the engine's
 * fake LLM (scripted replies from apps/workflow-engine/tests/fixtures/fake_llm/ai_builder/*.json). No live model.
 * New workflow from an outline, modify in the editor with Discard, red tests with Keep anyway, test change requests,
 * node assist, chat cards (and the Stage 8 template suggestion), Undo, and the not-configured error.
 * Written from the design doc, not from the code.
 */
import { test, expect, rand, type EngineApi } from '../fixtures/test'
import { seedProject, type P } from '../fixtures/seed'
import { nodeEl, openEditor, openTestsTab } from '../fixtures/tests3a'
import { restoreLlmScript } from '../fixtures/shots17'
import type { Page } from '@playwright/test'
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const HERE = path.dirname(fileURLToPath(import.meta.url))
const SCRIPTS = path.resolve(HERE, '../../../workflow-engine/tests/fixtures/fake_llm/ai_builder')
const SHOTS = '/tmp/claude-1000/-home-saketh-Projects-playground-work-demo/3b980f2a-bdc6-4187-b5dc-d2b1e0bdc78d/scratchpad/shots/stage6'

async function shot(page: Page, name: string) {
  await page.waitForTimeout(600) // let the canvas finish its fit-to-screen animation
  fs.mkdirSync(SHOTS, { recursive: true })
  const file = test.info().outputPath(`${name}.png`)
  await page.screenshot({ path: file })
  fs.copyFileSync(file, path.join(SHOTS, `${name}.png`))
}

const REQUEST = 'Amounts over 500 are marked for review, the rest are approved automatically, and the caller always gets the status back.'
const REFUND = 'Classify refund requests, pay small refunds at once and send larger refunds to a manager for approval.'
// the engine caches spec answers by prompt hash, so every request text is made unique
const uniq = (text: string) => `${text} (${rand(8)})`

type Rule = Record<string, unknown>
const rulesOf = (name: string): Rule[] => JSON.parse(fs.readFileSync(path.join(SCRIPTS, `${name}.json`), 'utf8')).rules
const only = (rules: Rule[], id: string): Rule => rules.find((r) => r.id === id)!
const route = (kind: string, answer?: string): Rule => ({
  id: 'route',
  when: { system_matches: '^WF_AI:CHAT_ROUTE\\n' },
  respond: { text: JSON.stringify({ route: kind, node: null, reason: 't', ...(answer ? { answer } : {}) }) },
})

/** Install a hand-written fake script (replaces the stack's baseline script; `restoreLlmScript` puts it back). */
async function use(api: EngineApi, nameOrRules: string | Rule[]) {
  const rules = typeof nameOrRules === 'string' ? rulesOf(nameOrRules) : nameOrRules
  await api.testApi('PUT', 'fake-llm/script', { rules })
  await api.clearLlmCalls()
}

interface View {
  id: string
  status: string
  workflow_id: string | null
  accepted_failing?: boolean
  kept_by?: string | null
  spec: { rules: Array<{ id: string; text: string }>; tests: Array<{ id: string; name: string; locked?: boolean; status?: string }> }
  report: { passed: boolean; rounds: unknown[]; stopped_because: string } | null
  test_change_requests: Array<{ test_id: string; reason: string }>
  usage: { llm_calls: number }
}
type Wf = {
  name: string
  archived?: boolean
  definition: {
    nodes: Array<{ name: string; type: string; parameters: Record<string, unknown> }>
    tests: Array<{ id: string; name: string; locked?: boolean; status?: string; approved_by?: string }>
  }
}
const getWf = (api: EngineApi, id: string) => api.get<Wf>(`/workflows/${id}`)
const getProposal = (api: EngineApi, id: string) => api.get<View>(`/ai/proposals/${id}`)
const nodeNames = (w: Wf) => w.definition.nodes.map((n) => n.name)

/** The whole flow over the API (used to seed a workflow the AI built, with approved tests). */
async function apiBuild(api: EngineApi, p: P, script: string, request = uniq(REQUEST), keep = true) {
  await use(api, script)
  const spec = await api.post<{ proposal: View }>('/ai/builder/spec', { project_id: p.id, request, actor: 'Asha' })
  const id = spec.proposal.id
  await api.post(`/ai/proposals/${id}/spec/approve`, { actor: 'Asha' })
  const built = await api.post<View>(`/ai/proposals/${id}/build`, { actor: 'Asha' })
  if (!keep) return { id, built, workflowId: null as string | null }
  const kept = await api.post<{ workflow_id: string }>(`/ai/proposals/${id}/keep`, { actor: 'Asha' })
  return { id, built, workflowId: kept.workflow_id }
}

/** Compute fee: a Set step with a wrong field name, an approved test that expects fee = 10 for amount 1000. */
async function seedFeeWorkflow(api: EngineApi, p: P) {
  const t = {
    id: 't_ab000001', name: 'fee is 1% of the amount', target: { kind: 'workflow' }, category: 'normal',
    input: { items: [{ body: { amount: 1000 }, headers: {}, query: {}, method: 'POST' }] }, mocks: {},
    expect: [{ node: 'Compute fee', path: 'fee', op: 'eq', value: 10 }],
  }
  const wf = await api.post<{ id: string }>(`/projects/${p.id}/workflows`, {
    name: `Fee ${rand(4)}`, settings: {}, tests: [t],
    nodes: [
      { name: 'Receive amount', type: 'Webhook', parameters: { method: 'POST', path: `fee-${rand(8)}`, responseMode: 'lastNode' }, position: { x: 0, y: 0 } },
      { name: 'Compute fee', type: 'Set', parameters: { mode: 'manual', keepOnlySet: true, fields: [{ name: 'fee', value: '{{ $json.body.amnt * 0.01 }}' }] }, position: { x: 280, y: 0 } },
      { name: 'Reply', type: 'Respond', parameters: { statusCode: 200, body: { fee: '{{ $json.fee }}' } }, position: { x: 560, y: 0 } },
    ],
    connections: [
      { source_node: 'Receive amount', target_node: 'Compute fee', source_output: 'main', target_input: 'main' },
      { source_node: 'Compute fee', target_node: 'Reply', source_output: 'main', target_input: 'main' },
    ],
  })
  await api.post(`/workflows/${wf.id}/tests/t_ab000001/approve`, { approved_by: 'sam' })
  return wf.id
}

const BUILD_URL = /\/ai\/proposals\/[^/]+\/build/

/** The canvas puts `data-diff` on the node's own element inside React Flow's wrapper. */
const marked = (page: Page, name: string) => nodeEl(page, name).locator('[data-diff]')

const rightTab = (page: Page, label: string) => page.getByRole('button', { name: label, exact: true })

/** The AI lives in the editor's Build tab (the old "Change" tab, same testid). */
async function openChangeTab(page: Page) {
  await page.getByTestId('editor-toggle-ai').click()
  await rightTab(page, 'Build').click()
  await expect(page.getByTestId('ai-editor-tab')).toBeVisible()
}

/** The proposal on screen is named by `?proposal=` (the /ai-build/<id> page is gone). */
async function proposalIdOf(page: Page): Promise<string> {
  await expect(page).toHaveURL(/[?&]proposal=[^&]+/)
  return new URL(page.url()).searchParams.get('proposal')!
}

const panelState = (page: Page) => page.getByTestId('ai-builder-panel')
const canvasStage = (page: Page) => page.locator('[data-stage]').first()

async function openAiChat(page: Page) {
  await page.getByTestId('editor-toggle-ai').click()
  await rightTab(page, 'AI').click()
}

async function sendChat(page: Page, text: string) {
  const box = page.getByPlaceholder('Ask a question or describe a change...')
  await box.fill(text)
  await box.press('Enter')
}

test.describe('AI builder (stage 6)', () => {
  let p: P
  test.beforeEach(async ({ api, context }) => {
    p = await seedProject(api, 'T19')
    // the person's name is asked once and remembered; every test except T19.1 starts with it known
    await context.addInitScript(() => {
      try {
        if (!sessionStorage.getItem('e2e.noActor')) localStorage.setItem('studio.actorName', 'Asha')
      } catch {
        /* storage blocked */
      }
    })
    await restoreLlmScript(api)
  })
  test.afterAll(async ({ api }) => {
    await restoreLlmScript(api)
    await api.testApi('PUT', 'fake-llm/state', { unavailable: false })
  })

  test('T19.1 new workflow from the outline: plan, edit a rule, approve, progress, review with marks, keep, locked tests', async ({ page, api, s, context }) => {
    await context.addInitScript(() => {
      try {
        sessionStorage.setItem('e2e.noActor', '1')
        localStorage.removeItem('studio.actorName')
      } catch {
        /* storage blocked */
      }
    })
    await use(api, 'repairable')
    await page.goto(`${s.studioUrl}/p/${p.slug}/workflows`)
    await page.getByTestId('ai-build-open').click()
    await expect(page.getByTestId('ai-builder-panel')).toBeVisible()
    await expect(page).toHaveURL(/\/workflows\/new\?ai=1/)

    // the name is asked once
    await page.getByTestId('ai-actor-name-input').fill('Asha Patel')
    await page.getByTestId('ai-actor-name-save').click()
    await expect(page.getByTestId('ai-request-input')).toBeVisible()
    await shot(page, 't19-1-request')
    await page.getByTestId('ai-request-input').fill(uniq(REQUEST))
    await page.getByTestId('ai-request-submit').click()

    // the plan: rules, steps and tests
    await expect(page.getByTestId('ai-spec-panel')).toBeVisible()
    await expect(page.getByTestId('ai-spec-summary')).toBeVisible()
    for (const id of ['r1', 'r2', 'r3']) await expect(page.getByTestId(`ai-spec-rule-${id}`)).toBeVisible()
    for (let i = 0; i < 5; i++) await expect(page.getByTestId(`ai-spec-step-${i}`)).toBeVisible()
    const specTests = page.locator('[data-testid^="ai-spec-test-"]:not([data-testid^="ai-spec-test-edit-"]):not([data-testid="ai-spec-test-add"])')
    await expect(specTests).toHaveCount(3)
    await expect(page.getByTestId('ai-spec-approve')).toBeEnabled()
    const proposalId = await proposalIdOf(page)
    await expect(page).toHaveURL(/\/workflows\/new\?/) // still the editor, no separate page
    await shot(page, 't19-1-spec')

    // edit one rule; the edit is saved to the proposal (no reload needed)
    const edited = 'Amounts over 500 are marked for review by a person.'
    await page.getByTestId('ai-spec-rule-text-r1').fill(edited)
    await expect.poll(async () => (await getProposal(api, proposalId)).spec.rules.find((r) => r.id === 'r1')?.text, { timeout: 15_000 }).toBe(edited)
    expect((await getProposal(api, proposalId)).status).toBe('spec_draft')
    expect((await api.get<unknown[]>(`/projects/${p.id}/workflows`)).length).toBe(0) // nothing saved yet

    // approve and build; hold the answer back a little so the 750 ms poll can show the rounds the server already finished
    await page.route(BUILD_URL, async (r) => {
      const resp = await r.fetch()
      await new Promise((res) => setTimeout(res, 3500))
      await r.fulfill({ response: resp })
    })
    await page.getByTestId('ai-spec-approve').click()
    await expect(page.getByTestId('ai-build-progress')).toBeVisible()
    // the steps are on the editor canvas while the build is still running
    await expect(nodeEl(page, 'Receive amount')).toBeVisible({ timeout: 15_000 })
    await expect(page.getByTestId('ai-review-panel')).toHaveCount(0)
    await expect(page.getByTestId('ai-build-round-0')).toBeVisible({ timeout: 10_000 })
    await expect(page.getByTestId('ai-build-round-1')).toBeVisible({ timeout: 10_000 })
    // round 0 had a refused change and a failing test, round 1 fixed them
    await expect(page.getByTestId('ai-build-round-0')).toContainText(/refused/)
    await expect(page.getByTestId('ai-build-round-0')).not.toHaveAttribute('data-failed', '0')
    await expect(page.getByTestId('ai-build-round-1')).toHaveAttribute('data-errors', '0')
    await expect(page.getByTestId('ai-build-round-1')).toHaveAttribute('data-failed', '0')
    await shot(page, 't19-1-progress')

    // review
    await expect(page.getByTestId('ai-review-panel')).toBeVisible({ timeout: 30_000 })
    await page.unroute(BUILD_URL)
    await expect(page.getByTestId('ai-review-summary')).toHaveAttribute('data-passed', 'true')
    for (const n of ['Receive amount', 'Over limit?', 'Mark for review', 'Mark auto approved', 'Reply']) {
      await expect(page.getByTestId(`ai-review-diff-${n}`)).toHaveAttribute('data-kind', 'added')
      await expect(marked(page, n)).toHaveAttribute('data-diff', 'added') // the marks are on the canvas
    }
    const reviewTests = page.locator('[data-testid^="ai-review-test-"][data-status]')
    await expect(reviewTests).toHaveCount(3)
    for (const st of await reviewTests.evaluateAll((els) => els.map((e) => e.getAttribute('data-status')))) expect(st).toBe('passed')
    const usage = page.getByTestId('ai-usage')
    await expect(usage).toBeVisible()
    expect(Number(await usage.getAttribute('data-calls'))).toBeGreaterThanOrEqual(3) // spec + build + one repair
    await expect(page.getByTestId('ai-keep')).toBeEnabled()
    await shot(page, 't19-1-review')

    // the server agrees: ready, two rounds, nothing saved
    const ready = await getProposal(api, proposalId)
    expect(ready).toMatchObject({ status: 'ready', actor: 'Asha Patel' })
    expect(ready.report!.passed).toBe(true)
    expect(ready.report!.rounds.length).toBe(2)
    expect((await api.get<unknown[]>(`/projects/${p.id}/workflows`)).length).toBe(0)

    // keep creates the workflow; open it in the editor
    await page.getByTestId('ai-keep-name').fill('Amount triage (e2e)')
    await page.getByTestId('ai-keep').click()
    await expect(page.getByTestId('ai-kept')).toBeVisible()
    await shot(page, 't19-1-kept')
    const kept = await getProposal(api, proposalId)
    expect(kept.status).toBe('kept')
    expect(kept.kept_by).toBe('Asha Patel')
    const wfId = kept.workflow_id!
    expect(wfId).toBeTruthy()
    const stored = await getWf(api, wfId)
    expect(stored.name).toBe('Amount triage (e2e)')
    expect(nodeNames(stored)).toEqual(expect.arrayContaining(['Receive amount', 'Over limit?', 'Mark for review', 'Mark auto approved', 'Reply']))
    expect(stored.definition.tests.length).toBe(3)
    for (const t of stored.definition.tests) expect(t).toMatchObject({ status: 'approved', locked: true })
    // the person's edit to the rule reached the stored proposal spec
    expect(kept.spec.rules.find((r) => r.id === 'r1')!.text).toBe(edited)

    // the editor is already on the new workflow: its URL carries the workflow id, the marks melt away
    await expect(page).toHaveURL(new RegExp(`/workflows/${wfId}(\\?|$)`))
    await expect(page.getByTestId('workflow-navbar')).toBeVisible()
    await expect(canvasStage(page)).toHaveAttribute('data-stage', 'off', { timeout: 15_000 })
    await expect(page.locator('.react-flow__node').first()).toBeVisible()
    await expect(nodeEl(page, 'Over limit?')).toBeVisible()
    await expect(marked(page, 'Over limit?')).toHaveCount(0)
    await openTestsTab(page)
    const rows = page.getByTestId('test-row')
    await expect(rows).toHaveCount(3)
    for (let i = 0; i < 3; i++) await expect(rows.nth(i).getByLabel('Approved and locked')).toBeVisible()
    await rows.first().click()
    await expect(page.getByTestId('test-locked')).toBeVisible()
    await shot(page, 't19-1-tests-locked')
  })

  test('T19.2 modify request from the editor: marks on the canvas, Discard leaves the draft unchanged', async ({ page, api, s }) => {
    const { workflowId } = await apiBuild(api, p, 'first_try')
    const before = await getWf(api, workflowId!)
    await use(api, 'modify_flag_high_value')
    await openEditor(page, s, p.slug, workflowId!)
    await openChangeTab(page)
    await shot(page, 't19-2-change-tab')
    await page.getByTestId('ai-request-input').fill(uniq('Also flag amounts over 10000 as high value'))
    await page.getByTestId('ai-request-submit').click()
    await expect(page.getByTestId('ai-spec-panel')).toBeVisible()
    const proposalId = await proposalIdOf(page)
    expect((await getProposal(api, proposalId)).workflow_id).toBe(workflowId)
    await page.getByTestId('ai-spec-approve').click()
    await expect(page.getByTestId('ai-review-panel')).toBeVisible({ timeout: 30_000 })
    await expect(page.getByTestId('ai-review-summary')).toHaveAttribute('data-passed', 'true')
    await expect(page.getByTestId('ai-review-diff-Flag high value')).toHaveAttribute('data-kind', 'added')
    await expect(marked(page, 'Flag high value')).toHaveAttribute('data-diff', 'added')
    // steps the change did not touch are on the canvas without a mark
    await expect(nodeEl(page, 'Reply')).toBeVisible()
    await expect(marked(page, 'Reply')).toHaveCount(0)
    await shot(page, 't19-2-marks')
    // a proposal never touches the stored draft before Keep
    expect(await getWf(api, workflowId!)).toEqual(before)

    await page.getByTestId('ai-discard').click()
    await expect(page.getByTestId('ai-discarded')).toBeVisible()
    await shot(page, 't19-2-discarded')
    expect((await getProposal(api, proposalId)).status).toBe('discarded')
    const after = await getWf(api, workflowId!)
    expect(after).toEqual(before) // re-read from the API
    expect(nodeNames(after)).not.toContain('Flag high value')

    // the canvas is back to the draft: the stage is off and the AI's step is gone
    await expect(canvasStage(page)).toHaveAttribute('data-stage', 'off', { timeout: 15_000 })
    await expect(page.getByTestId('workflow-navbar')).toBeVisible()
    await expect(nodeEl(page, 'Over limit?')).toBeVisible()
    await expect(nodeEl(page, 'Flag high value')).toHaveCount(0)
  })

  test('T19.3 red tests: Keep asks to confirm, then keeps with accepted_failing', async ({ page, api, s }) => {
    await use(api, 'unrepairable')
    await page.goto(`${s.studioUrl}/p/${p.slug}/ai-build/new`)
    await expect(page).toHaveURL(/\/workflows\/new/)
    await page.getByTestId('ai-request-input').fill(uniq(REQUEST))
    await page.getByTestId('ai-request-submit').click()
    await expect(page.getByTestId('ai-spec-panel')).toBeVisible()
    const proposalId = await proposalIdOf(page)
    await page.getByTestId('ai-spec-approve').click()
    await expect(page.getByTestId('ai-review-panel')).toBeVisible({ timeout: 60_000 })
    await expect(page.getByTestId('ai-review-summary')).toHaveAttribute('data-passed', 'false')
    await expect(page.getByTestId('ai-review-summary')).toContainText(/failing|repair attempts/i)
    await expect(page.locator('[data-testid^="ai-review-test-"][data-status="failed"]').first()).toBeVisible()
    const view = await getProposal(api, proposalId)
    expect(view.status).toBe('ready')
    expect(view.report).toMatchObject({ passed: false, stopped_because: 'rounds_exhausted' })
    await shot(page, 't19-3-red-review')

    // Keep opens a confirm dialog; going back saves nothing
    await page.getByTestId('ai-keep').click()
    await expect(page.getByTestId('ai-keep-confirm-failing')).toBeVisible()
    await shot(page, 't19-3-confirm')
    await page.getByRole('button', { name: 'Go back' }).click()
    await expect(page.getByTestId('ai-keep-confirm-failing')).toBeHidden()
    expect((await getProposal(api, proposalId)).status).toBe('ready')
    expect((await api.get<unknown[]>(`/projects/${p.id}/workflows`)).length).toBe(0)

    await page.getByTestId('ai-keep').click()
    await page.getByTestId('ai-keep-confirm-failing').click()
    await expect(page.getByTestId('ai-kept')).toContainText('failing tests')
    const kept = await getProposal(api, proposalId)
    expect(kept).toMatchObject({ status: 'kept', accepted_failing: true })
    const stored = await getWf(api, kept.workflow_id!)
    expect(stored.definition.tests.length).toBe(3)
    // the kept event records the choice
    const ev = await api.get<{ items: Array<{ event: string; payload: Record<string, unknown> }> }>(`/ai/proposals/${proposalId}/events`)
    expect(JSON.stringify(ev.items.filter((e) => e.event === 'kept'))).toContain('accepted_failing')
  })

  test('T19.4 a test change request is shown and cannot be applied', async ({ page, api, s }) => {
    await use(api, 'test_change_attempt')
    await page.goto(`${s.studioUrl}/p/${p.slug}/ai-build/new`)
    await expect(page).toHaveURL(/\/workflows\/new/)
    await page.getByTestId('ai-request-input').fill(uniq(REQUEST))
    await page.getByTestId('ai-request-submit').click()
    await expect(page.getByTestId('ai-spec-panel')).toBeVisible()
    const proposalId = await proposalIdOf(page)
    const planned = (await getProposal(api, proposalId)).spec.tests.map((t) => t.name)
    await page.getByTestId('ai-spec-approve').click()
    await expect(page.getByTestId('ai-review-panel')).toBeVisible({ timeout: 60_000 })

    const req = page.locator('[data-testid^="ai-review-test-change-"]:not([data-testid="ai-review-test-changes"])').first()
    await expect(req).toBeVisible()
    await expect(req).toContainText('500 is the limit')
    await expect(req).toContainText(/never change on their own/i)
    // no apply / accept button for it anywhere
    await expect(req.getByRole('button', { name: /apply|accept|replace|use this test|change the test/i })).toHaveCount(0)
    await expect(page.getByRole('button', { name: /apply/i })).toHaveCount(0)
    await expect(req.getByRole('button')).toHaveCount(1) // only "Use this in my request"
    await shot(page, 't19-4-test-change-request')
    // "Use this in my request" only fills the revise box
    await req.getByRole('button', { name: 'Use this in my request' }).click()
    await expect(page.getByTestId('ai-revise-input')).toHaveValue(/About the test/)

    // the approved tests are exactly what was approved: the weakening and the sneaky approved test were refused
    const view = await getProposal(api, proposalId)
    expect(view.test_change_requests.length).toBeGreaterThan(0)
    expect(view.spec.tests.map((t) => t.name)).toEqual(planned)
    const proposedNames = JSON.stringify(view.spec.tests)
    expect(proposedNames).not.toContain('weakened')
    expect(proposedNames).not.toContain('sneaky')
    expect((await api.get<unknown[]>(`/projects/${p.id}/workflows`)).length).toBe(0)
  })

  test('T19.5 node assist: write an expression and keep it; explain shows an answer', async ({ page, api, s }) => {
    const wfId = await seedFeeWorkflow(api, p)
    await use(api, 'node_write_expression')
    await openEditor(page, s, p.slug, wfId)
    await nodeEl(page, 'Compute fee').dblclick()
    await expect(page.getByTestId('ndv-modal')).toBeVisible()
    await page.getByTestId('node-ai-menu').click()
    await expect(page.getByTestId('node-ai-fix-error')).toBeVisible()
    await shot(page, 't19-5-menu')
    await page.getByTestId('node-ai-write-expression').click()
    const dlg = page.getByTestId('node-ai-dialog')
    await expect(dlg).toBeVisible()
    await page.getByTestId('node-ai-field').fill('fields[0].value')
    await page.getByTestId('node-ai-instruction').fill('one percent of the amount')
    await shot(page, 't19-5-dialog')
    await page.getByTestId('node-ai-submit').click()
    const review = page.getByTestId('node-ai-review')
    await expect(review).toBeVisible({ timeout: 30_000 })
    await expect(review.getByTestId('ai-review-summary')).toHaveAttribute('data-passed', 'true')
    await expect(review.getByTestId('ai-review-diff-Compute fee')).toHaveAttribute('data-kind', 'changed')
    await expect(marked(page, 'Compute fee')).toHaveAttribute('data-diff', 'changed') // marked on the live canvas
    await shot(page, 't19-5-review')
    // nothing is saved before Keep
    expect((await getWf(api, wfId)).definition.nodes.find((n) => n.name === 'Compute fee')!.parameters.fields).toEqual([{ name: 'fee', value: '{{ $json.body.amnt * 0.01 }}' }])

    await review.getByTestId('ai-keep').click()
    await expect(review.getByTestId('ai-kept')).toBeVisible()
    await expect
      .poll(async () => ((await getWf(api, wfId)).definition.nodes.find((n) => n.name === 'Compute fee')!.parameters.fields as Array<{ value: string }>)[0].value)
      .toBe('{{ $json.body.amount * 0.01 }}')
    // the approved test is untouched and still locked
    expect((await getWf(api, wfId)).definition.tests[0]).toMatchObject({ id: 't_ab000001', locked: true, status: 'approved' })

    // explain: an answer, no proposal to review
    await use(api, 'node_explain')
    await page.keyboard.press('Escape')
    await expect(page.getByTestId('node-ai-review')).toBeHidden()
    await nodeEl(page, 'Compute fee').dblclick()
    await expect(page.getByTestId('ndv-modal')).toBeVisible()
    await page.getByTestId('node-ai-menu').click()
    await page.getByTestId('node-ai-explain').click()
    await page.getByTestId('node-ai-instruction').fill(`what does this do? ${rand(6)}`)
    await page.getByTestId('node-ai-submit').click()
    await expect(page.getByTestId('node-ai-answer')).toContainText('one percent of the amount')
    await shot(page, 't19-5-explain')
    const list = await api.get<{ items: Array<{ status: string; kind: string }> }>(`/ai/proposals?project_id=${p.id}&workflow_id=${wfId}`)
    expect(list.items.map((i) => i.status)).toContain('answered')
  })

  test('T19.6 chat: a build message makes a plan card; approve builds; the proposal card opens the review', async ({ page, api, s }) => {
    const { workflowId } = await apiBuild(api, p, 'first_try')
    await use(api, [route('build'), only(rulesOf('first_try'), 'spec'), only(rulesOf('first_try'), 'build')])
    await openEditor(page, s, p.slug, workflowId!)
    await openAiChat(page)
    await sendChat(page, uniq('Build me a workflow: ' + REQUEST))
    const card = page.getByTestId('ai-chat-spec-card')
    await expect(card).toBeVisible({ timeout: 30_000 })
    await expect(card).toContainText('Amount triage')
    await expect(card).toContainText('3 rules')
    await expect(page.getByTestId('ai-template-suggestion')).toHaveCount(0) // nothing fits this request
    await shot(page, 't19-6-spec-card')
    const before = (await api.get<unknown[]>(`/projects/${p.id}/workflows`)).length
    await page.getByTestId('ai-chat-spec-approve').click()
    const pc = page.getByTestId('ai-chat-proposal-card')
    await expect(pc).toBeVisible({ timeout: 60_000 })
    await expect(pc).toHaveAttribute('data-status', 'ready')
    await shot(page, 't19-6-proposal-card')
    // chat never saves a workflow
    expect((await api.get<unknown[]>(`/projects/${p.id}/workflows`)).length).toBe(before)
    await page.getByTestId('ai-chat-open-review').click()
    await expect(page).toHaveURL(/\/workflows\/[^?]+\?.*proposal=/)
    await expect(panelState(page)).toBeVisible()
    await expect(page.getByTestId('ai-review-panel')).toBeVisible()
    await expect(page.getByTestId('ai-review-summary')).toHaveAttribute('data-passed', 'true')
    await shot(page, 't19-6-review-from-chat')
    // the panel scrolls; bring the last control (Ask for this change) fully into view like a person would
    await page.getByTestId('ai-revise-submit').scrollIntoViewIfNeeded()
  })

  test('T19.7 Undo after Keep restores the draft (re-read from the API)', async ({ page, api, s }) => {
    const { workflowId } = await apiBuild(api, p, 'first_try')
    const before = await getWf(api, workflowId!)
    expect(nodeNames(before)).not.toContain('Flag high value')
    // a modify proposal built over the API, reviewed and kept in the UI
    await use(api, 'modify_flag_high_value')
    const spec = await api.post<{ proposal: View }>('/ai/builder/spec', { project_id: p.id, workflow_id: workflowId, request: uniq('Also flag amounts over 10000 as high value'), actor: 'Asha' })
    const pid = spec.proposal.id
    await api.post(`/ai/proposals/${pid}/spec/approve`, { actor: 'Asha' })
    const built = await api.post<View>(`/ai/proposals/${pid}/build`, { actor: 'Asha' })
    expect(built.status).toBe('ready')
    await page.goto(`${s.studioUrl}/p/${p.slug}/ai-build/${pid}`) // old URL: redirects into the editor
    await expect(page).toHaveURL(new RegExp(`/workflows/${workflowId}.*proposal=${pid}`))
    await expect(page.getByTestId('ai-review-panel')).toBeVisible()
    await page.getByTestId('ai-keep').click()
    await expect(page.getByTestId('ai-kept')).toBeVisible()
    await expect.poll(async () => nodeNames(await getWf(api, workflowId!))).toContain('Flag high value')
    await shot(page, 't19-7-kept-toast')

    // the toast has an Undo button
    const toast = page.locator('[data-sonner-toast]').filter({ hasText: 'Change kept' })
    await expect(toast).toBeVisible()
    await toast.getByRole('button', { name: 'Undo' }).click()
    await expect(page.getByTestId('ai-undone')).toBeVisible()
    await expect.poll(async () => (await getProposal(api, pid)).status).toBe('undone')
    const after = await getWf(api, workflowId!)
    expect(nodeNames(after)).toEqual(nodeNames(before))
    expect(after.definition.nodes.map((n) => [n.name, n.type, n.parameters])).toEqual(before.definition.nodes.map((n) => [n.name, n.type, n.parameters]))
    expect(after.definition.tests.map((t) => t.id)).toEqual(before.definition.tests.map((t) => t.id))
    await shot(page, 't19-7-undone')
  })

  test('T19.8 ai-error carries the code ai_not_configured when no model is available', async ({ page, api, s, net }) => {
    await use(api, 'first_try')
    await page.goto(`${s.studioUrl}/p/${p.slug}/ai-build/new`)
    await expect(page).toHaveURL(/\/workflows\/new/)
    await expect(page.getByTestId('ai-request-input')).toBeVisible()
    await expect(page.getByTestId('ai-not-configured')).toHaveCount(0)
    try {
      await api.testApi('PUT', 'fake-llm/state', { unavailable: true })
      await page.getByTestId('ai-request-input').fill(uniq(REQUEST))
      await net.expect([{ method: 'POST', url: /\/ai\/builder\/spec/, status: 503 }], async () => {
        await page.getByTestId('ai-request-submit').click()
      })
      const err = page.getByTestId('ai-error')
      await expect(err).toBeVisible()
      await expect(err).toHaveAttribute('data-code', 'ai_not_configured')
      await shot(page, 't19-8-not-configured')
      // the request is kept so it can be sent again, and nothing was drafted
      await expect(page.getByTestId('ai-request-input')).toHaveValue(/Amounts over 500/)
      await expect(page.getByTestId('ai-spec-panel')).toHaveCount(0)
      expect((await api.get<{ items: unknown[] }>(`/ai/proposals?project_id=${p.id}`)).items.length).toBe(0)
    } finally {
      await api.testApi('PUT', 'fake-llm/state', { unavailable: false })
    }
  })

  test('T19.9 Stage 8: a refund request in the chat gets a template card; dismiss keeps the plan, install adds the template', async ({ page, api, s }) => {
    const { workflowId } = await apiBuild(api, p, 'first_try')
    await use(api, [route('build'), only(rulesOf('first_try'), 'spec'), only(rulesOf('first_try'), 'build')])
    await openEditor(page, s, p.slug, workflowId!)
    await openAiChat(page)
    await sendChat(page, uniq(REFUND))
    const card = page.getByTestId('ai-chat-spec-card')
    await expect(card).toBeVisible({ timeout: 30_000 })
    const sug = page.getByTestId('ai-template-suggestion')
    await expect(sug).toBeVisible()
    await expect(sug).toHaveAttribute('data-slug', 'refund-triage')
    await shot(page, 't19-9-suggestion')
    // nothing is installed until the person says so
    expect((await api.get<unknown[]>(`/projects/${p.id}/template-installs`)).length).toBe(0)
    await page.getByTestId('ai-template-suggestion-dismiss').click()
    await expect(sug).toHaveCount(0)
    await expect(card).toBeVisible()
    await expect(page.getByTestId('ai-chat-spec-approve')).toBeEnabled()
    expect((await api.get<unknown[]>(`/projects/${p.id}/template-installs`)).length).toBe(0)

    // a second request: this time install
    await sendChat(page, uniq(REFUND))
    await expect(page.getByTestId('ai-template-suggestion')).toBeVisible({ timeout: 30_000 })
    await page.getByTestId('ai-template-suggestion-install').click()
    await expect(page.getByTestId('ai-template-suggestion-installed')).toBeVisible({ timeout: 60_000 })
    await expect(page.getByTestId('ai-template-suggestion-open')).toBeVisible()
    await shot(page, 't19-9-installed')
    const installs = await api.get<Array<{ slug?: string; template_slug?: string }>>(`/projects/${p.id}/template-installs`)
    // the template and the template it depends on (customer-notify) are installed, once each
    expect(installs.map((i) => i.slug ?? i.template_slug).sort()).toEqual(['customer-notify', 'refund-triage'])
    // the plan is untouched: both plan cards are still there
    await expect(page.getByTestId('ai-chat-spec-card')).toHaveCount(2)
  })
})

/** Record every value `data-build-state` ever takes and every URL the page shows (the sidebar's walk through the build). */
async function recordStates(page: Page) {
  await page.evaluate(() => {
    const w = window as unknown as { __states: string[]; __urls: string[] }
    w.__states = []
    w.__urls = [location.pathname + location.search]
    const note = () => {
      const st = document.querySelector('[data-testid="ai-builder-panel"]')?.getAttribute('data-build-state')
      if (st && w.__states[w.__states.length - 1] !== st) w.__states.push(st)
      const u = location.pathname + location.search
      if (w.__urls[w.__urls.length - 1] !== u) w.__urls.push(u)
    }
    new MutationObserver(note).observe(document.documentElement, { subtree: true, childList: true, attributes: true, attributeFilter: ['data-build-state'] })
    setInterval(note, 25)
    note()
  })
}
const statesSeen = (page: Page) => page.evaluate(() => (window as unknown as { __states: string[] }).__states)
const urlsSeen = (page: Page) => page.evaluate(() => (window as unknown as { __urls: string[] }).__urls)

/** Real nodes on the canvas (blueprint outlines and the "add" placeholder excluded). */
const realNodes = (page: Page) => page.locator('.react-flow__node:not([data-id^="bp:"]):not([data-id="addNodes"]):not(.react-flow__node-addNodes)')
/** The camera frames the graph in the part of the canvas the sidebar leaves free: no node box touches the panel. */
async function expectNodesClearOfPanel(page: Page) {
  await expect
    .poll(
      async () => {
        const panel = await page.getByTestId('ai-builder-panel').boundingBox()
        const boxes = await realNodes(page).evaluateAll((els) => els.map((e) => { const r = e.getBoundingClientRect(); return { id: e.getAttribute('data-id'), l: r.left, r: r.right, t: r.top, b: r.bottom } }))
        const bad = boxes.filter((b) => b.r > panel!.x + 1 || b.l < 0 || b.t < 0 || b.b > 900).map((b) => `${b.id} x ${Math.round(b.l)}-${Math.round(b.r)} (panel starts at ${Math.round(panel!.x)})`)
        return bad
      },
      { message: 'nodes under or outside the visible canvas', timeout: 5000 },
    )
    .toEqual([])
}
const blueprints = (page: Page) => page.getByTestId('blueprint-node')

/** Drive the request -> plan part of a new build in the editor. */
async function askForShowcase(page: Page, api: EngineApi, s: { studioUrl: string }, slug: string) {
  await use(api, 'showcase')
  await page.goto(`${s.studioUrl}/p/${slug}/workflows/new?ai=1`)
  await expect(page.getByTestId('ai-request-input')).toBeVisible()
  await page.getByTestId('ai-request-input').fill(uniq('Classify refund requests; pay small refunds at once, send larger ones to a manager, always reply with the status.'))
  await page.getByTestId('ai-request-submit').click()
  await expect(panelState(page)).toHaveAttribute('data-build-state', 'plan', { timeout: 30_000 })
}

test.describe('AI builder in the editor (stage 6, motion build)', () => {
  let p: P
  test.beforeEach(async ({ api, context }) => {
    p = await seedProject(api, 'T19m')
    await context.addInitScript(() => {
      try { localStorage.setItem('studio.actorName', 'Asha') } catch { /* storage blocked */ }
    })
    await restoreLlmScript(api)
  })
  test.afterAll(async ({ api }) => {
    await restoreLlmScript(api)
  })

  test.describe('with the real motion', () => {
    test.use({ motion: 'on' })

    test('T19.10 live build plays on the editor canvas: ask, thinking, plan with outlines, building, review, kept, no blank frame', async ({ page, api, s }) => {
      await use(api, 'showcase')
      await page.goto(`${s.studioUrl}/p/${p.slug}/workflows/new?ai=1`)
      // the Build tab is open on an empty canvas, with the hint pointing at it
      await expect(panelState(page)).toHaveAttribute('data-build-state', 'ask')
      await expect(page.getByTestId('ai-empty-hint')).toBeVisible()
      await expect(page.getByTestId('ai-request-box')).toBeVisible()
      await expect(realNodes(page)).toHaveCount(0)
      await recordStates(page)

      await page.getByTestId('ai-request-input').fill(uniq('Classify refund requests; pay small refunds at once, send larger ones to a manager, always reply with the status.'))
      await page.getByTestId('ai-request-submit').click()
      await expect(panelState(page)).toHaveAttribute('data-build-state', 'thinking')
      await expect(page.getByTestId('ai-thinking')).toBeVisible()
      await expect(page.getByTestId('ai-thinking-timer')).toBeVisible()

      // plan: the sidebar lists the steps, the canvas shows a dashed outline for each, no real node yet
      await expect(panelState(page)).toHaveAttribute('data-build-state', 'plan', { timeout: 30_000 })
      await expect(page.getByTestId('ai-spec-panel')).toBeVisible()
      const pid = await proposalIdOf(page)
      const steps = (await getProposal(api, pid)).spec as unknown as { steps: unknown[] }
      expect(steps.steps.length).toBe(7)
      await expect(blueprints(page)).toHaveCount(7)
      await expect(canvasStage(page)).toHaveAttribute('data-stage', 'blueprint')
      await expect(page.getByTestId('ai-stage-pill')).toBeVisible()
      await expect(page.getByTestId('ai-empty-hint')).toHaveCount(0)
      await expect(realNodes(page)).toHaveCount(0)

      // approve: the real nodes appear on this canvas while the round log is still short
      await page.getByTestId('ai-spec-approve').click()
      await expect(panelState(page)).toHaveAttribute('data-build-state', 'building')
      await expect(canvasStage(page)).toHaveAttribute('data-playback', 'playing')
      await expect(realNodes(page).first()).toBeVisible({ timeout: 30_000 })
      await expect(page.getByTestId('ai-review-panel')).toHaveCount(0)
      await expect(page).toHaveURL(/\/workflows\/new\?/) // the same editor page the whole time
      await expect(page.getByTestId('ai-build-round-1')).toBeVisible({ timeout: 60_000 })
      await expect(canvasStage(page)).toHaveAttribute('data-playback', 'done', { timeout: 60_000 })
      await expect(panelState(page)).toHaveAttribute('data-build-state', 'review', { timeout: 60_000 })
      await expect(realNodes(page)).toHaveCount(7)
      await expect(blueprints(page)).toHaveCount(0)
      await expect(page.locator('.react-flow__node[data-id^="bp:"]')).toHaveCount(0)
      await expect(canvasStage(page)).toHaveAttribute('data-stage', 'review')
      await expect(page.getByTestId('ai-proposal-canvas')).toHaveAttribute('data-nodes', '7')
      await expect(page.getByTestId('ai-review-summary')).toHaveAttribute('data-passed', 'true')
      await expectNodesClearOfPanel(page)
      await shot(page, 't19-10-review')
      // round 0 failed a test, round 1 fixed it
      await expect(page.getByTestId('ai-build-round-0')).toHaveCount(0) // the log gives way to the review

      // keep: the URL becomes the workflow's id and no node is ever hidden or missing on the way
      await page.evaluate(() => {
        const w = window as unknown as { __blank: string[]; __stop: boolean }
        w.__blank = []
        w.__stop = false
        const tick = () => {
          const nodes = [...document.querySelectorAll<HTMLElement>('.react-flow__node:not([data-id^="bp:"]):not(.react-flow__node-addNodes)')]
          if (nodes.length < 7) w.__blank.push(`only ${nodes.length} nodes`)
          for (const n of nodes) if (getComputedStyle(n).visibility === 'hidden') w.__blank.push(`hidden ${n.dataset.id}`)
          if (!w.__stop) requestAnimationFrame(tick)
        }
        requestAnimationFrame(tick)
      })
      await page.getByTestId('ai-keep-name').fill('Refund approval (e2e)')
      await page.getByTestId('ai-keep').click()
      await expect(panelState(page)).toHaveAttribute('data-build-state', 'kept', { timeout: 20_000 })
      await expect(page.getByTestId('ai-kept')).toBeVisible()
      const wfId = (await getProposal(api, pid)).workflow_id!
      await expect(page).toHaveURL(new RegExp(`/workflows/${wfId}(\\?|$)`))
      await expect(canvasStage(page)).toHaveAttribute('data-stage', 'off', { timeout: 20_000 })
      await page.waitForTimeout(500)
      const blank = await page.evaluate(() => { const w = window as unknown as { __blank: string[]; __stop: boolean }; w.__stop = true; return w.__blank })
      expect(blank, 'frames with missing or hidden nodes around Keep').toEqual([])
      await expect(realNodes(page)).toHaveCount(7)
      await expect(marked(page, 'Over limit?')).toHaveCount(0)

      // the sidebar walked ask -> thinking -> plan -> building -> review -> kept, in that order
      const seen = await statesSeen(page)
      expect(seen.filter((x, i) => seen.indexOf(x) === i)).toEqual(['ask', 'thinking', 'plan', 'building', 'review', 'kept'])
      expect(await statesSeen(page)).toEqual(seen)
      const urls = await urlsSeen(page)
      expect(urls[urls.length - 1]).toMatch(new RegExp(`/workflows/${wfId}`))
      expect(urls.some((u) => u.includes('/ai-build'))).toBe(false)
      const stored = await getWf(api, wfId)
      expect(stored.name).toBe('Refund approval (e2e)')
      expect(stored.definition.nodes.length).toBe(7)
    })

    test('T19.11 Skip finishes the round: the nodes are all on the canvas within half a second', async ({ page, api, s }) => {
      await askForShowcase(page, api, s, p.slug)
      await page.getByTestId('ai-spec-approve').click()
      await expect(canvasStage(page)).toHaveAttribute('data-playback', 'playing')
      await expect(page.getByTestId('ai-build-skip')).toBeVisible()
      await expect(realNodes(page).first()).toBeVisible({ timeout: 30_000 })
      const before = await realNodes(page).count()
      await page.getByTestId('ai-build-skip').click()
      await expect(realNodes(page)).toHaveCount(7, { timeout: 500 })
      expect(before).toBeLessThanOrEqual(7)
      await expect(blueprints(page)).toHaveCount(0)
      await expect(panelState(page)).toHaveAttribute('data-build-state', 'review', { timeout: 60_000 })
    })

    test('T19.12 blueprint: one outline per planned step after the plan, none after the build', async ({ page, api, s }) => {
      await askForShowcase(page, api, s, p.slug)
      const pid = await proposalIdOf(page)
      const n = (await getProposal(api, pid)).spec.rules && ((await getProposal(api, pid)).spec as unknown as { steps: Array<{ name: string }> }).steps
      await expect(page.locator('.react-flow__node[data-id^="bp:"]')).toHaveCount(n.length)
      for (const st of n) await expect(page.locator(`[data-testid="blueprint-node"][data-name="${st.name}"]`)).toBeVisible()
      await shot(page, 't19-12-blueprint')
      await page.getByTestId('ai-spec-approve').click()
      await expect(panelState(page)).toHaveAttribute('data-build-state', 'review', { timeout: 90_000 })
      await expect(page.locator('.react-flow__node[data-id^="bp:"]')).toHaveCount(0)
      await expect(realNodes(page)).toHaveCount(n.length)
    })

    test('T19.16 hovering a log row lights its node on the canvas', async ({ page, api, s }) => {
      await askForShowcase(page, api, s, p.slug)
      await page.getByTestId('ai-spec-approve').click()
      await expect(page.getByTestId('ai-build-round-0')).toBeVisible({ timeout: 30_000 })
      const row = page.locator('[data-testid="ai-log-row"][data-kind="add"][data-node="Over limit?"]').first()
      await expect(row).toBeVisible({ timeout: 60_000 })
      await expect(nodeEl(page, 'Over limit?')).toBeVisible()
      const glow = () => nodeEl(page, 'Over limit?').locator('div.cursor-grab').first().evaluate((el) => getComputedStyle(el).boxShadow)
      // the node's glow is scaled while it animates in: take the resting baseline only once two reads 300ms apart agree
      let off = ''
      await expect.poll(async () => {
        const a = await glow()
        await page.waitForTimeout(300)
        const b = await glow()
        off = b
        return a === b
      }, { timeout: 20_000 }).toBe(true)
      await row.hover() // the log goes away when the build ends, so this has to be quick
      await expect(row).toHaveAttribute('data-lit', 'true', { timeout: 3000 })
      await expect.poll(glow, { timeout: 3000 }).not.toBe(off)
      await page.mouse.move(5, 5)
      await expect.poll(glow, { timeout: 5000 }).toBe(off)
    })
  })

  test('T19.13 the canvas is view-only during review; Keep restores editing', async ({ page, api, s }) => {
    const { workflowId } = await apiBuild(api, p, 'first_try')
    const before = await getWf(api, workflowId!)
    await use(api, 'modify_flag_high_value')
    await openEditor(page, s, p.slug, workflowId!)
    await openChangeTab(page)
    await page.getByTestId('ai-request-input').fill(uniq('Also flag amounts over 10000 as high value'))
    await page.getByTestId('ai-request-submit').click()
    await page.getByTestId('ai-spec-approve').click()
    await expect(panelState(page)).toHaveAttribute('data-build-state', 'review', { timeout: 30_000 })
    await expect(canvasStage(page)).toHaveAttribute('data-stage', 'review')

    const positions = () => realNodes(page).evaluateAll((els) => els.map((e) => [e.getAttribute('data-id'), (e as HTMLElement).style.transform]).sort())
    await expectNodesClearOfPanel(page)
    const count = await realNodes(page).count()
    const pos0 = await positions()
    // drag a node
    const box = (await nodeEl(page, 'Mark for review').boundingBox())!
    await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2)
    await page.mouse.down()
    await page.mouse.move(box.x + box.width / 2 + 120, box.y + box.height / 2 + 90, { steps: 6 })
    await page.mouse.up()
    expect(await positions()).toEqual(pos0)
    // Delete and Ctrl+Z on a selected node
    const nb = (await nodeEl(page, 'Mark for review').boundingBox())! // a real click at the node: view-only means the canvas ignores it
    await page.mouse.click(nb.x + nb.width / 2, nb.y + nb.height / 2)
    await page.keyboard.press('Delete')
    await page.keyboard.press('Control+z')
    await page.keyboard.press('Backspace')
    await expect(realNodes(page)).toHaveCount(count)
    expect(await positions()).toEqual(pos0)
    await expect(nodeEl(page, 'Mark for review')).toBeVisible()
    expect(await getWf(api, workflowId!)).toEqual(before)

    // Keep, and the canvas is editable again
    await page.getByTestId('ai-keep').click()
    await expect(panelState(page)).toHaveAttribute('data-build-state', 'kept', { timeout: 20_000 })
    await expect(canvasStage(page)).toHaveAttribute('data-stage', 'off', { timeout: 20_000 })
    const kept = await realNodes(page).count()
    expect(kept).toBe(count)
    await nodeEl(page, 'Flag high value').click()
    await page.keyboard.press('Delete')
    await expect(realNodes(page)).toHaveCount(kept - 1)
  })

  test('T19.14 the pill\'s Discard works and leaves the draft unchanged', async ({ page, api, s }) => {
    const { workflowId } = await apiBuild(api, p, 'first_try')
    const before = await getWf(api, workflowId!)
    await use(api, 'modify_flag_high_value')
    await openEditor(page, s, p.slug, workflowId!)
    await openChangeTab(page)
    await page.getByTestId('ai-request-input').fill(uniq('Also flag amounts over 10000 as high value'))
    await page.getByTestId('ai-request-submit').click()
    await page.getByTestId('ai-spec-approve').click()
    await expect(panelState(page)).toHaveAttribute('data-build-state', 'review', { timeout: 30_000 })
    const pid = await proposalIdOf(page)
    await expect(page.getByTestId('ai-stage-pill')).toHaveAttribute('data-mode', 'review')
    await expect(page.getByTestId('ai-stage-pill')).toContainText('Previewing AI change')
    await expect(nodeEl(page, 'Flag high value')).toBeVisible()
    await page.getByTestId('ai-stage-discard').click()
    await expect(panelState(page)).toHaveAttribute('data-build-state', 'discarded', { timeout: 20_000 })
    await expect.poll(async () => (await getProposal(api, pid)).status).toBe('discarded')
    await expect(canvasStage(page)).toHaveAttribute('data-stage', 'off', { timeout: 20_000 })
    await expect(nodeEl(page, 'Flag high value')).toHaveCount(0)
    await expect(nodeEl(page, 'Over limit?')).toBeVisible()
    expect(await getWf(api, workflowId!)).toEqual(before)
  })

  test('T19.15 the pill\'s Keep works and saves the change', async ({ page, api, s }) => {
    const { workflowId } = await apiBuild(api, p, 'first_try')
    await use(api, 'modify_flag_high_value')
    await openEditor(page, s, p.slug, workflowId!)
    await openChangeTab(page)
    await page.getByTestId('ai-request-input').fill(uniq('Also flag amounts over 10000 as high value'))
    await page.getByTestId('ai-request-submit').click()
    await page.getByTestId('ai-spec-approve').click()
    await expect(panelState(page)).toHaveAttribute('data-build-state', 'review', { timeout: 30_000 })
    const pid = await proposalIdOf(page)
    await page.getByTestId('ai-stage-keep').click()
    await expect(panelState(page)).toHaveAttribute('data-build-state', 'kept', { timeout: 20_000 })
    await expect.poll(async () => (await getProposal(api, pid)).status).toBe('kept')
    expect(nodeNames(await getWf(api, workflowId!))).toContain('Flag high value')
    await expect(canvasStage(page)).toHaveAttribute('data-stage', 'off', { timeout: 20_000 })
    await expect(nodeEl(page, 'Flag high value')).toBeVisible()
  })

  test('T19.17 the old /ai-build URLs redirect into the editor', async ({ page, api, s }) => {
    await page.goto(`${s.studioUrl}/p/${p.slug}/ai-build/new`)
    await expect(page).toHaveURL(/\/workflows\/new\?ai=1/)
    await expect(page.getByTestId('ai-builder-panel')).toBeVisible()
    await expect(page.getByTestId('ai-request-box')).toBeVisible()

    const { workflowId } = await apiBuild(api, p, 'first_try')
    await page.goto(`${s.studioUrl}/p/${p.slug}/ai-build/new?workflow=${workflowId}`)
    await expect(page).toHaveURL(new RegExp(`/workflows/${workflowId}\\?ai=1`))
    await expect(page.getByTestId('ai-request-box')).toBeVisible()
    await expect(nodeEl(page, 'Over limit?')).toBeVisible()

    // a proposal that changes a workflow, and one that makes a new workflow
    await use(api, 'modify_flag_high_value')
    const spec = await api.post<{ proposal: View }>('/ai/builder/spec', { project_id: p.id, workflow_id: workflowId, request: uniq('Also flag amounts over 10000 as high value'), actor: 'Asha' })
    await page.goto(`${s.studioUrl}/p/${p.slug}/ai-build/${spec.proposal.id}`)
    await expect(page).toHaveURL(new RegExp(`/workflows/${workflowId}.*proposal=${spec.proposal.id}`))
    await expect(page.getByTestId('ai-spec-panel')).toBeVisible()

    await use(api, 'first_try')
    const fresh = await api.post<{ proposal: View }>('/ai/builder/spec', { project_id: p.id, request: uniq(REQUEST), actor: 'Asha' })
    await page.goto(`${s.studioUrl}/p/${p.slug}/ai-build/${fresh.proposal.id}`)
    await expect(page).toHaveURL(new RegExp(`/workflows/new.*proposal=${fresh.proposal.id}`))
    await expect(page.getByTestId('ai-spec-panel')).toBeVisible()
    await page.waitForTimeout(1000) // let the plan finish sliding in before the end-of-test layout check
  })
})

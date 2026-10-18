/**
 * Stage 5 (DESIGN-STAGE5-UIS §10.4, testids §11): the public form and run page, the approvals inbox and rail badge, a task
 * (custom screen), approve / reject and the run finishing, the editor's Interface panel (preview, chips, an editor Run
 * that ends "waiting for approval" with a task link), and the chat interface. Real UI against the real stack; the AI step
 * is pinned (AIAgent output is {response, structured}) and the chat reply comes from the fake LLM.
 * Every scenario builds its own project, so inbox counts are exact.
 */
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { test, expect, rand, stack, type EngineApi } from '../fixtures/test'
import { seedProject, seedWorkflow, type P } from '../fixtures/seed'
import { OwnFakeApi, putLlmRules, restoreLlmScript, shot } from '../fixtures/shots17'
import type { Page } from '@playwright/test'

const HERE = path.dirname(fileURLToPath(import.meta.url))
const FIX = path.resolve(HERE, '../../../workflow-engine/tests/fixtures/workflows')
const SUMMARY = 'Customer reports a damaged item; the amount is above the auto-approval limit.'
const CLASSIFY = { response: 'ok', structured: { category: 'damaged', summary: SUMMARY, suggest: 'approve' } }

const fake = new OwnFakeApi()
test.beforeAll(async () => {
  await fake.start()
})
test.afterAll(() => {
  fake.stop()
})

interface Ui {
  p: P
  wfId: string
  slug: string // interface slug
}

/** Refund triage (UI) in its own project: interface saved, Classify pinned under `structured`, published. */
async function seedRefundUi(api: EngineApi, name: string, opts: { publish?: boolean } = {}): Promise<Ui> {
  const p = await seedProject(api, name)
  const body = JSON.parse(fs.readFileSync(path.join(FIX, 'refund_triage_ui.json'), 'utf8').replace(/__REFUND_API_URL__/g, fake.url))
  body.name = `Refund triage (UI) ${rand(4)}`
  const wf = await seedWorkflow(api, p, body)
  await api.put(`/workflows/${wf.id}/interface`, body.interface)
  const nodes = (body.nodes as Array<Record<string, unknown>>).map((n) => (n.name === 'Classify' ? { ...n, pinned_data: [{ json: CLASSIFY }] } : n))
  await api.put(`/workflows/${wf.id}`, { nodes })
  if (opts.publish !== false) await api.post(`/workflows/${wf.id}/publish`, {})
  return { p, wfId: wf.id, slug: body.interface.slug as string }
}

interface Submitted {
  execution_id: string
  run_token: string
}
async function submitRefund(api: EngineApi, ui: Ui, order = 'A-10042', amount = 742): Promise<Submitted> {
  const r = await api.raw<Submitted>('POST', `/i/${ui.p.slug}/${ui.slug}/submit`, { fields: { order_id: order, amount, reason: 'damaged', email: 'dana@example.com' } }, { 'Idempotency-Key': `e2e-${rand(10)}` })
  expect(r.status, r.text).toBe(202)
  return r.body
}
const runUrl = (ui: Ui, sub: Submitted) => `${stack().studioUrl}/i/${ui.p.slug}/${ui.slug}/runs/${sub.execution_id}?t=${sub.run_token}`

async function waitRunStatus(page: Page, status: string | RegExp, timeout = 40_000) {
  await expect(page.getByTestId('iface-run-status')).toHaveAttribute('data-status', status, { timeout })
}
async function openInbox(page: Page, ui: Ui) {
  await page.goto(`${stack().studioUrl}/p/${ui.p.slug}/approvals`)
  await expect(page.getByTestId('approvals-page')).toBeVisible()
}
async function refundCalls(): Promise<Array<{ n: number; idempotency_key: string | null; body: string }>> {
  return ((await (await fetch(`${fake.url}/_refunds`)).json()) as { calls: Array<{ n: number; idempotency_key: string | null; body: string }> }).calls
}
async function openFirstTask(page: Page, ui: Ui) {
  await openInbox(page, ui)
  const row = page.getByTestId('approvals-row').first()
  await expect(row).toBeVisible({ timeout: 30_000 })
  await row.click()
  await expect(page.getByTestId('task-detail')).toBeVisible()
}

test.describe('interfaces (stage 5)', () => {
  test.beforeEach(async ({ api }) => {
    await fetch(`${fake.url}/_refunds/reset`, { method: 'POST' })
    await restoreLlmScript(api)
  })
  test.afterAll(async ({ api }) => {
    await restoreLlmScript(api)
  })

  test('T18.1 public form: validation, submit, the run page shows waiting for the manager', async ({ page, api, s, layoutCheck }) => {
    layoutCheck.enabled = false
    const ui = await seedRefundUi(api, 'T18a')
    await page.goto(`${s.studioUrl}/i/${ui.p.slug}/${ui.slug}`)
    await expect(page.getByTestId('iface-form')).toBeVisible()
    await expect(page.getByTestId('iface-title')).toContainText('Refund request')
    await shot(page, 't18-1-form-empty')

    // a bad order id is refused before anything is sent
    let submits = 0
    page.on('request', (r) => {
      if (r.method() === 'POST' && /\/submit/.test(r.url())) submits++
    })
    await page.getByTestId('iface-field-order_id').fill('bad')
    await page.getByTestId('iface-field-amount').fill('742')
    await page.getByTestId('iface-field-reason-option-damaged').click()
    await page.getByTestId('iface-field-email').fill('dana@example.com')
    await page.getByTestId('iface-submit').click()
    await expect(page.getByTestId('iface-field-error-order_id')).toBeVisible()
    await expect(page.getByTestId('iface-form')).toBeVisible()
    await shot(page, 't18-1-form-invalid')
    expect(submits, 'a refused form must not reach the server').toBe(0)

    await page.getByTestId('iface-field-order_id').fill('A-10099')
    await page.getByTestId('iface-submit').click()
    await expect(page).toHaveURL(/\/runs\/.+/)
    await expect(page.getByTestId('iface-run-status')).toBeVisible()
    await waitRunStatus(page, 'waiting', 40_000)
    // the task is listed by its title (the Approval node's title template: "Refund <order> · $<amount>")
    await expect(page.getByTestId('iface-run-waiting')).toContainText(/Waiting for .*A-10099/)
    await shot(page, 't18-1-run-waiting')

    // re-read through the inbox: one open task titled after the order
    await openInbox(page, ui)
    await expect(page.getByTestId('approvals-row')).toHaveCount(1, { timeout: 30_000 })
    await expect(page.getByTestId('approvals-row').first()).toContainText('A-10099')
    await page.goBack()

    // the run page survives a reload (state lives in Postgres, not in the page)
    await page.reload()
    await waitRunStatus(page, 'waiting')
  })

  test('T18.2 approvals inbox: rail badge, custom task screen, approve, the run finishes and the refund is paid once', async ({ page, api }) => {
    const ui = await seedRefundUi(api, 'T18b')
    const sub = await submitRefund(api, ui)
    await page.goto(runUrl(ui, sub))
    await waitRunStatus(page, 'waiting')

    await openInbox(page, ui)
    await expect(page.getByTestId('approvals-row')).toHaveCount(1, { timeout: 30_000 })
    const row = page.getByTestId('approvals-row').first()
    await expect(row).toHaveAttribute('data-status', 'open')
    await expect(row).toContainText('A-10042')
    await expect(page.getByTestId('rail-approvals-count').first()).toHaveText('1')
    await shot(page, 't18-2-inbox')

    await row.click()
    const detail = page.getByTestId('task-detail')
    await expect(detail).toBeVisible()
    // the custom screen: heading, amount, the AI summary callout, the approver form and the decision buttons
    await expect(page.getByTestId('screen-root')).toBeVisible()
    await expect(page.getByTestId('screen-heading').first()).toContainText('Manager review')
    await expect(page.getByTestId('screen-amount')).toContainText('$742.00')
    await expect(page.getByTestId('screen-callout')).toContainText(SUMMARY)
    await expect(detail).toContainText('Over $500 limit')
    await expect(page.getByTestId('task-approve')).toBeEnabled()
    await expect(page.getByTestId('task-reject')).toBeEnabled()
    await shot(page, 't18-2-task')

    await page.getByTestId('task-decided-by').fill('Dana Manager')
    await page.getByTestId('task-approve').click()
    await expect(page.getByTestId('task-outcome')).toContainText(/Approved/i)
    await expect(page.getByTestId('task-approve')).toBeDisabled()
    await expect(page.getByTestId('task-reject')).toBeDisabled()
    await shot(page, 't18-2-approved')

    // the run page flips to success with the result
    await page.goto(runUrl(ui, sub))
    await waitRunStatus(page, 'success')
    await expect(page.getByTestId('iface-run-result')).toContainText(/approved|issued/i)
    await shot(page, 't18-2-run-success')

    // the refund was paid exactly once, with the resume key
    await expect.poll(async () => (await refundCalls()).length).toBe(1)
    const calls = await refundCalls()
    expect(calls[0].idempotency_key).toBeTruthy()
    expect(calls[0].body).toContain('A-10042')

    // the inbox no longer shows it as open and the badge is gone
    await openInbox(page, ui)
    await expect(page.getByTestId('rail-approvals-count')).toHaveCount(0)
  })

  test('T18.3 reject path: the requester sees "not approved" and nothing is paid', async ({ page, api }) => {
    const ui = await seedRefundUi(api, 'T18c')
    const sub = await submitRefund(api, ui, 'A-20001', 650)
    await openFirstTask(page, ui)
    await page.getByTestId('task-decided-by').fill('Dana Manager')
    await page.getByTestId('task-comment').fill('Photo does not show damage')
    await page.getByTestId('task-reject').click()
    await expect(page.getByTestId('task-outcome')).toContainText(/Reject/i)
    await expect(page.getByTestId('task-reject')).toBeDisabled()
    await shot(page, 't18-3-rejected')
    await page.goto(runUrl(ui, sub))
    await waitRunStatus(page, 'success')
    await expect(page.getByTestId('iface-run-result')).toContainText(/not approved/i)
    await shot(page, 't18-3-run-rejected')
    expect(await refundCalls()).toHaveLength(0)
  })

  test('T18.4 double-clicking Approve records one decision and pays once', async ({ page, api }) => {
    const ui = await seedRefundUi(api, 'T18d')
    const sub = await submitRefund(api, ui, 'A-30001', 900)
    await openFirstTask(page, ui)
    await page.getByTestId('task-decided-by').fill('Dana Manager')
    await page.getByTestId('task-approve').dblclick()
    await expect(page.getByTestId('task-outcome')).toContainText(/Approved/i)
    await page.goto(runUrl(ui, sub))
    await waitRunStatus(page, 'success')
    await expect.poll(async () => (await refundCalls()).length).toBe(1)
    await page.waitForTimeout(1500)
    expect(await refundCalls()).toHaveLength(1)
  })

  test('T18.5 editor: interface panel, wired chips, live preview of the task screen, node chips, and a Run that waits with a task link', async ({ page, api, s }) => {
    const ui = await seedRefundUi(api, 'T18e')
    const sub = await submitRefund(api, ui, 'A-10099', 742) // live data for the preview
    await page.goto(runUrl(ui, sub))
    await waitRunStatus(page, 'waiting')

    await page.goto(`${s.studioUrl}/p/${ui.p.slug}/workflows/${ui.wfId}`)
    await expect(page.getByTestId('workflow-navbar')).toBeVisible()
    await expect(page.getByTestId('navbar-tab-interface')).toContainText(/Interface/)
    await page.getByTestId('navbar-tab-interface').click()
    const panel = page.getByTestId('interface-panel')
    await expect(panel).toBeVisible()
    await expect(panel.getByTestId('interface-kind-custom')).toHaveAttribute('aria-pressed', 'true')
    await expect(panel.getByTestId('interface-url')).toContainText(`/i/${ui.p.slug}/${ui.slug}`)
    const wired = panel.getByTestId('interface-wired')
    await expect(wired.getByTestId('wired-chip-input')).toContainText('order_id')
    await expect(wired.getByTestId('wired-chip-input')).toContainText('amount')
    await expect(wired.getByTestId('wired-chip-input')).toContainText('reason')
    await expect(wired.getByTestId('wired-chip-input')).toContainText('email')
    await expect(wired.getByTestId('wired-chip-task')).toContainText('Manager review')
    await expect(wired.getByTestId('wired-chip-context')).toContainText('Classify')
    await expect(wired.getByTestId('wired-chip-result')).toContainText('Show result')

    // the live preview renders the task screen from the real run
    await expect(panel.getByTestId('interface-preview')).toHaveAttribute('data-kind', 'custom')
    await panel.getByTestId('interface-preview-screen-select').click()
    await page.getByRole('option', { name: /Task: Manager review/ }).click()
    const preview = panel.getByTestId('interface-preview')
    await expect(preview).toContainText('A-10099')
    await expect(preview).toContainText('$742.00')
    await expect(preview).toContainText('Over $500 limit')
    await shot(page, 't18-5-interface-panel-preview')

    // canvas chips on the form trigger and the approval node
    await expect(page.getByTestId('node-form-fields').first()).toContainText(/4 fields/)
    await expect(page.getByTestId('node-approval-custom-ui').first()).toContainText('Custom UI')
    await expect(page.getByTestId('node-approval-waiting').first()).toContainText(/1/)
    await shot(page, 't18-5-node-chips')

    // an editor Run pauses at the approval and links to the task
    await page.getByTestId('editor-run').click()
    await expect(page.getByTestId('execution-status')).toHaveAttribute('data-status', 'waiting', { timeout: 60_000 })
    await expect(page.getByTestId('execution-status')).toContainText(/waiting for approval/i)
    const link = page.getByTestId('execution-waiting-link').first()
    await expect(link).toBeVisible()
    await expect(link).toHaveAttribute('href', /\/approvals\/.+/)
    await shot(page, 't18-5-editor-run-waiting')
    await link.click()
    await expect(page.getByTestId('task-detail')).toBeVisible()
    await expect(page.getByTestId('task-title')).toContainText(/Refund/)
  })

  test('T18.6 chat interface: a message and its reply, history survives a reload', async ({ page, api, s, layoutCheck }) => {
    layoutCheck.enabled = false
    const REPLY = 'Your replacement card was posted on 14 October.'
    await putLlmRules(api, [{ id: 'e2e-support-chat', when: { system_matches: 'SUPPORT_CHAT' }, respond: { text: REPLY } }])
    const p = await seedProject(api, 'T18f')
    const body = JSON.parse(fs.readFileSync(path.join(FIX, 'chat_support_ui.json'), 'utf8'))
    body.name = `Support chat ${rand(4)}`
    const wf = await seedWorkflow(api, p, body)
    await api.put(`/workflows/${wf.id}/interface`, body.interface)
    await api.post(`/workflows/${wf.id}/publish`, {})

    await page.goto(`${s.studioUrl}/i/${p.slug}/${body.interface.slug}`)
    await expect(page.getByTestId('iface-chat')).toBeVisible()
    await page.getByTestId('iface-chat-input').fill('Where is my replacement card?')
    await page.getByTestId('iface-chat-send').click()
    const user = page.locator('[data-testid="iface-chat-message"][data-role="user"]')
    const bot = page.locator('[data-testid="iface-chat-message"][data-role="assistant"]')
    await expect(user.last()).toContainText('Where is my replacement card?')
    await expect(bot.last()).toContainText(REPLY, { timeout: 40_000 })
    await shot(page, 't18-6-chat-reply')
    await expect(page.getByTestId('iface-chat-busy')).toHaveCount(0)

    // second turn
    await page.getByTestId('iface-chat-input').fill('Thanks')
    await page.getByTestId('iface-chat-send').click()
    await expect(bot).toHaveCount(2, { timeout: 40_000 })

    await page.reload()
    await expect(page.getByTestId('iface-chat')).toBeVisible()
    await expect(user.first()).toContainText('Where is my replacement card?')
    await expect(bot.first()).toContainText(REPLY)
    await expect(user).toHaveCount(2)
    await shot(page, 't18-6-chat-reload')
  })
})

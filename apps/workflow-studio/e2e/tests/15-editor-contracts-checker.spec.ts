/**
 * Stage 2: contracts, the mistake checker, autocomplete without running, n8n import.
 * Real UI against the real stack (engine + studio + Postgres). No mocks, no route interception.
 */
import type { Locator, Page } from '@playwright/test'
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { test, expect, type EngineApi } from '../fixtures/test'
import { seedProject, seedWorkflow, type P } from '../fixtures/seed'
import type { StackState } from '../global-setup'

const HERE = path.dirname(fileURLToPath(import.meta.url))
const SHOTS = '/tmp/claude-1000/-home-saketh-Projects-playground-work/07d49b96-ad12-42c5-9569-58e6b1f9be16/scratchpad/shots2'

const link = (a: string, b: string, out = 'main') => ({ source_node: a, source_output: out, target_node: b, target_input: 'main' })
const start = { name: 'Start', type: 'Start', parameters: {}, position: { x: 0, y: 0 } }
const setNode = (name: string, field: string, value: string, x: number) => ({
  name,
  type: 'Set',
  label: name,
  parameters: { mode: 'manual', fields: [{ name: field, value, type: 'string' }] },
  position: { x, y: 0 },
})
const nodeEl = (page: Page, name: string): Locator => page.locator(`.react-flow__node[data-id="${name}"]`)

async function openEditor(page: Page, s: StackState, slug: string, wfId: string) {
  await page.goto(`${s.studioUrl}/p/${slug}/workflows/${wfId}`)
  await expect(page.getByTestId('workflow-navbar')).toBeVisible()
  await expect(page.locator('.react-flow__node').first()).toBeVisible()
}
async function openNdv(page: Page, name: string) {
  await nodeEl(page, name).dblclick()
  await expect(page.getByTestId('ndv-modal')).toBeVisible()
  await page.getByTestId('ndv-tab-parameters').click()
}
async function closeNdv(page: Page) {
  await page.getByTestId('ndv-close').click()
  await expect(page.getByTestId('ndv-modal')).toBeHidden()
}
/** Edit the (single) text field of the open node panel whose current text matches `current`. */
async function editField(page: Page, current: string | RegExp, next: string) {
  const modal = page.getByTestId('ndv-modal')
  await modal.locator('.cursor-text').filter({ hasText: current }).first().click()
  const area = modal.locator('textarea').first()
  await area.fill(next)
  await expect(area).toHaveValue(next)
  await area.blur()
}
async function shot(page: Page, name: string) {
  fs.mkdirSync(SHOTS, { recursive: true })
  const file = test.info().outputPath(`${name}.png`)
  await page.screenshot({ path: file })
  fs.copyFileSync(file, path.join(SHOTS, `${name}.png`))
}
async function projectSlug(api: EngineApi, id: string) {
  const ps = await api.get<Array<{ id: string; slug: string }>>('/projects')
  return ps.find((p) => p.id === id)!.slug
}
async function runAndWait(page: Page) {
  await page.getByTestId('editor-run').click()
  await expect(page.getByTestId('execution-status')).toContainText(/success|failed|error/i, { timeout: 30_000 })
}
async function openProblems(page: Page) {
  const tab = page.getByTestId('bottom-tab-problems')
  // the bottom panel may be collapsed (its open state is remembered per browser)
  if (!(await tab.click({ trial: true, timeout: 1500 }).then(() => true, () => false))) await page.getByTestId('editor-toggle-logs').click()
  await tab.click()
}
const errMarks = (page: Page) => page.locator('[data-testid="node-check-mark"][data-check="error"]')

test.describe('editor contracts + checker', () => {
  let p: P
  test.beforeEach(async ({ api }) => {
    p = await seedProject(api, 'T15')
  })

  test('T15.1 wrong field shows a mark, a field message and a Problems row; fixing clears them', async ({ page, api, s }) => {
    const wf = await seedWorkflow(api, p, {
      name: 'Wrong field',
      nodes: [start, setNode('A', 'a', '1', 240), setNode('B', 'x', '{{ $json.b }}', 480)],
      connections: [link('Start', 'A'), link('A', 'B')],
    })
    await openEditor(page, s, p.slug, wf.id)
    await expect(nodeEl(page, 'B').locator('[data-testid="node-check-mark"][data-check="error"]')).toBeVisible({ timeout: 10_000 })
    await expect(nodeEl(page, 'A').getByTestId('node-check-mark')).toHaveCount(0)
    await shot(page, 't15-1-node-mark')

    await openNdv(page, 'B')
    await expect(page.getByTestId('ndv-modal').locator('[data-testid^="param-check-"]').first()).toBeVisible()
    await expect(page.getByTestId('param-check-fields')).toContainText(/\bb\b/)
    await shot(page, 't15-1-param-check')
    await closeNdv(page)

    await openProblems(page)
    await expect(page.getByTestId('problems-count')).toHaveText('1')
    const row = page.locator('[data-testid="problem-row"][data-code="WF002"]')
    await expect(row).toHaveCount(1)
    await shot(page, 't15-1-problems')
    await row.click()
    await expect(page.getByTestId('ndv-modal')).toBeVisible()
    await page.getByTestId('ndv-tab-parameters').click()

    await editField(page, '$json.b', '{{ $json.a }}')
    await closeNdv(page)
    await expect(errMarks(page)).toHaveCount(0, { timeout: 10_000 })
    await expect(page.getByTestId('problems-count')).toHaveCount(0)
    await expect(page.getByTestId('problem-row')).toHaveCount(0)
    await shot(page, 't15-1-fixed')
  })

  test('T15.2 autocomplete and input panel use the contract before any run', async ({ page, api, s }) => {
    const wf = await seedWorkflow(api, p, {
      name: 'Never run',
      nodes: [start, setNode('A', 'alpha', '1', 240), setNode('B', 'x', '', 480)],
      connections: [link('Start', 'A'), link('A', 'B')],
    })
    expect((await api.get<unknown[]>(`/executions?workflow_id=${wf.id}`)).length).toBe(0)
    await openEditor(page, s, p.slug, wf.id)
    // give the live check time to land
    await page.waitForTimeout(1500)
    await openNdv(page, 'B')
    const modal = page.getByTestId('ndv-modal')
    // input panel: contract fields
    const fields = modal.locator('[data-testid="contract-fields"] [data-source="contract"]')
    await expect(fields.filter({ hasText: 'alpha' }).first()).toBeVisible({ timeout: 10_000 })
    await shot(page, 't15-2-input-contract')
    // expression editor: type, then open the variable list
    await modal.locator('.cursor-text').nth(1).click() // the Value field
    const area = modal.locator('textarea').first()
    await area.fill('{{ $json.')
    await modal.getByText('Insert variable...').click()
    const suggestion = modal.locator('button[data-source="contract"]').filter({ hasText: '$json.alpha' })
    await expect(suggestion.first()).toBeVisible()
    await shot(page, 't15-2-autocomplete-fixed')
  })

  test('T15.2b typing $json. opens the suggestions, filters, and Enter inserts', async ({ page, api, s }) => {
    const wf = await seedWorkflow(api, p, {
      name: 'Insert variable',
      nodes: [start, setNode('A', 'alpha', '1', 240), setNode('B', 'x', '', 480)],
      connections: [link('Start', 'A'), link('A', 'B')],
    })
    await openEditor(page, s, p.slug, wf.id)
    await page.waitForTimeout(1500)
    await openNdv(page, 'B')
    const modal = page.getByTestId('ndv-modal')
    await modal.locator('.cursor-text').nth(1).click()
    const area = modal.locator('textarea').first()
    await area.click()
    await area.pressSequentially('{{ $json.')
    const item = modal.locator('button[data-source="contract"]').filter({ hasText: '$json.alpha' })
    await expect(item.first()).toBeVisible({ timeout: 5000 })
    await shot(page, 't15-2b-typed-trigger-fixed')
    await area.pressSequentially('al')
    await expect(modal.locator('[data-testid="expression-suggestions"] button')).toHaveCount(1)
    await area.press('Enter')
    await expect(area).toHaveValue('{{ $json.alpha')
    await expect(modal.getByTestId('expression-suggestions')).toHaveCount(0)
    // Escape closes without inserting
    await area.fill('')
    await area.pressSequentially('{{ $json.')
    await expect(item.first()).toBeVisible()
    await area.press('Escape')
    await expect(modal.getByTestId('expression-suggestions')).toHaveCount(0)
  })

  test('T15.3 learn, approve and remove a contract', async ({ page, api, s }) => {
    const wf = await seedWorkflow(api, p, {
      name: 'Learn',
      nodes: [
        start,
        { name: 'Http', type: 'HttpRequest', label: 'Http', parameters: { method: 'GET', url: s.fake_api_ok_url, responseType: 'json' }, position: { x: 240, y: 0 } },
        setNode('Use', 'd', '{{ $node["Http"].json.decision }}', 480),
      ],
      connections: [link('Start', 'Http'), link('Http', 'Use')],
    })
    await openEditor(page, s, p.slug, wf.id)
    await expect(nodeEl(page, 'Http').getByTestId('node-contract-badge')).toHaveCount(0)
    await runAndWait(page)
    await openNdv(page, 'Http')
    await shot(page, 't15-3-output-header-fixed')
    await page.getByTestId('ndv-contract-learn').click()
    const dlg = page.getByTestId('contract-dialog')
    await expect(dlg).toBeVisible()
    await expect(dlg).toContainText('decision')
    await expect(dlg).toContainText('ok')
    await page.getByTestId('contract-approved-by').fill('Saketh')
    await shot(page, 't15-3-contract-dialog-fixed')
    await page.getByTestId('contract-approve').click()
    await expect(dlg).toBeHidden()
    await closeNdv(page)
    await expect(nodeEl(page, 'Http').getByTestId('node-contract-badge')).toBeVisible()
    await shot(page, 't15-3-badge')
    const saved = await api.get<{ definition: { nodes: Array<{ name: string; contract?: { approved_by: string; outputs: { main: { properties: Record<string, unknown> } } } }> } }>(`/workflows/${wf.id}`)
    const c = saved.definition.nodes.find((n) => n.name === 'Http')!.contract!
    expect(c.approved_by).toBe('Saketh')
    expect(JSON.stringify(c.outputs)).toContain('decision')
    // the referenced field is in the contract, so no error yet
    await expect(errMarks(page)).toHaveCount(0)

    // reference a field the contract does not have
    await openNdv(page, 'Use')
    await editField(page, '$node["Http"].json.decision', '{{ $node["Http"].json.nonexistent }}')
    await closeNdv(page)
    // the engine deliberately downgrades WF002 on an *observed* (sampled) shape to a warning, so the mark is amber
    await expect(nodeEl(page, 'Use').locator('[data-testid="node-check-mark"][data-check="warning"]')).toBeVisible({ timeout: 10_000 })
    await openProblems(page)
    await expect(page.locator('[data-testid="problem-row"][data-code="WF002"]')).toHaveCount(1)
    await shot(page, 't15-3-mark-after-contract')

    // remove the contract: badge gone
    await openNdv(page, 'Http')
    await page.getByTestId('ndv-contract-remove').click()
    await closeNdv(page)
    await expect(nodeEl(page, 'Http').getByTestId('node-contract-badge')).toHaveCount(0)
    await expect(page.getByTestId('node-check-mark')).toHaveCount(0, { timeout: 10_000 })
  })

  test('T15.4 import an n8n workflow from the workflows list', async ({ page, s }) => {
    const fixture = fs.readFileSync(path.resolve(HERE, '../../../workflow-engine/tests/fixtures/n8n/4867_ai_powered_stock_market_summary_bot.json'), 'utf8')
    await page.goto(`${s.studioUrl}/p/${p.slug}/workflows`)
    await page.getByTestId('import-n8n').click()
    await expect(page.getByTestId('import-n8n-dialog')).toBeVisible()
    await expect(page.getByTestId('import-n8n-confirm')).toBeDisabled()
    await page.getByTestId('import-n8n-input').fill(fixture)
    await page.getByTestId('import-n8n-preview').click()
    const report = page.getByTestId('import-n8n-report')
    await expect(report).toBeVisible({ timeout: 20_000 })
    const mapped = Number(await page.getByTestId('import-n8n-mapped').getAttribute('data-count'))
    const unmapped = Number(await page.getByTestId('import-n8n-unmapped').getAttribute('data-count'))
    const disabledCode = Number(await page.getByTestId('import-n8n-disabled-code').getAttribute('data-count'))
    expect(mapped).toBeGreaterThan(3)
    expect(unmapped).toBeGreaterThan(0) // openAi + slack have no equivalent
    expect(disabledCode).toBe(1)
    await expect(page.getByTestId('import-n8n-unmapped')).toContainText(/openAi|slack/i)
    await shot(page, 't15-4-report')
    await page.getByTestId('import-n8n-confirm').click()
    await expect(page).toHaveURL(/\/workflows\/[^/]+$/, { timeout: 20_000 })
    await expect(page.getByTestId('workflow-navbar')).toBeVisible()
    // 4867 has 21 nodes, 11 of them sticky notes (dropped)
    await expect(page.locator('.react-flow__node').first()).toBeVisible()
    await expect(page.locator('.react-flow__node')).toHaveCount(10)
    expect(await page.locator('.node-disabled').count()).toBeGreaterThanOrEqual(disabledCode)
    await shot(page, 't15-4-imported')
  })

  test('T15.5 publish is blocked by errors, the Problems tab opens, fixing allows it', async ({ page, api, s, net }) => {
    const wf = await seedWorkflow(api, p, {
      name: 'Blocked',
      nodes: [start, setNode('A', 'a', '1', 240), setNode('B', 'x', '{{ $json.zzz }}', 480)],
      connections: [link('Start', 'A'), link('A', 'B')],
    })
    await openEditor(page, s, p.slug, wf.id)
    await expect(errMarks(page)).toHaveCount(1, { timeout: 10_000 })
    await net.expect([{ method: 'POST', url: /\/workflows\/[^/]+\/publish$/, status: 422 }], async () => {
      await page.getByTestId('editor-publish').click()
      await expect(page.getByText(/Can't publish/i).first()).toBeVisible()
    })
    await expect(page.locator('[data-testid="problem-row"][data-code="WF002"]')).toHaveCount(1)
    await shot(page, 't15-5-blocked')
    await openNdv(page, 'B')
    await editField(page, '$json.zzz', '{{ $json.a }}')
    await closeNdv(page)
    await expect(errMarks(page)).toHaveCount(0, { timeout: 10_000 })
    await page.getByTestId('editor-publish').click()
    await expect(page.getByText(/Published v/i).first()).toBeVisible({ timeout: 15_000 })
    await expect(page.getByTestId('editor-unpublish')).toBeVisible()
    await shot(page, 't15-5-published')
  })

  test('T15.6 seeded Refund triage has no error marks', async ({ page, api, s }) => {
    const slug = await projectSlug(api, 'proj_default')
    await openEditor(page, s, slug, s.refund_workflow_id)
    // wait until the live check has landed (Problems tab renders its state), then assert
    await page.waitForTimeout(2000)
    await openProblems(page)
    await expect(page.locator('[data-check="error"]')).toHaveCount(0)
    await shot(page, 't15-6-refund')
  })
})

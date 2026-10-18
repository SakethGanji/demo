/** Stage 3A (spec 16) helpers: seed a Refund triage copy with a Risk API spec, open the editor and the Tests tab. */
import type { Locator, Page } from '@playwright/test'
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { test, expect, rand, type EngineApi } from './test'
import { seedTable, seedWorkflow, type P } from './seed'
import type { StackState } from '../global-setup'

const HERE = path.dirname(fileURLToPath(import.meta.url))
export const SHOTS = '/tmp/claude-1000/-home-saketh-Projects-playground-work/3d72fd25-6bc4-4d76-88a5-8e3acd22aeb6/scratchpad/shots16'
export const RISK_YAML = fs.readFileSync(path.resolve(HERE, '../../../workflow-engine/tests/fixtures/openapi/risk_api.yaml'), 'utf8')

export async function shot(page: Page, name: string) {
  fs.mkdirSync(SHOTS, { recursive: true })
  const file = test.info().outputPath(`${name}.png`)
  await page.screenshot({ path: file })
  fs.copyFileSync(file, path.join(SHOTS, `${name}.png`))
}

export const order = (amount: number | string, extra: Record<string, unknown> = {}) => [
  { body: { order_id: 'A1', amount, reason: 'damaged', ...extra } },
]

export type Json = Record<string, unknown>

/** A TestCase body as the API stores it. */
export function mkTest(name: string, category: string, amount: number, expectList: Json[], extra: Json = {}): Json {
  return {
    name,
    target: { kind: 'workflow' },
    input: { items: order(amount) },
    mocks: {},
    expect: expectList,
    category,
    status: 'draft',
    locked: false,
    source: 'human',
    ...extra,
  }
}

export const DEFAULT_MOCKS = {
  'Risk API': { http: { status: 200, body: { decision: 'approve', score: 12 } } },
  'Log escalated': { items: [{ id: 1 }] },
  'Log approved': { items: [{ id: 2 }] },
}

export interface SeedOpts {
  tests?: Json[]
  /** default mocks; `null` = none at all, omit the Risk API entry with `without` */
  mocks?: Record<string, unknown> | null
  without?: string[]
  withSpec?: boolean
  name?: string
}

export async function seedRefund(api: EngineApi, s: StackState, p: P, opts: SeedOpts = {}) {
  const raw = fs
    .readFileSync(path.resolve(HERE, '../../../workflow-engine/tests/fixtures/workflows/refund_triage.json'), 'utf8')
    .replace('__REFUND_API_URL__', s.fake_api_ok_url)
  const def = JSON.parse(raw)
  def.name = opts.name ?? 'Refund triage (tests)'
  def.nodes[0].parameters.path = `refund-${rand()}`
  let spec: { id: string; operations: Array<{ operation_id: string }> } | null = null
  if (opts.withSpec !== false) {
    spec = await api.post('/api-specs', { project_id: p.id, name: 'Risk API', spec_text: RISK_YAML })
    const op = spec!.operations.find((o) => o.operation_id === 'riskCheck') ?? spec!.operations[0]
    for (const n of def.nodes) if (n.name === 'Risk API') n.parameters.api_ref = { spec_id: spec!.id, operation_id: op.operation_id }
  }
  const mocks = { ...(opts.mocks === null ? {} : (opts.mocks ?? DEFAULT_MOCKS)) } as Record<string, unknown>
  for (const w of opts.without ?? []) delete mocks[w]
  def.test_defaults = { mocks }
  def.tests = opts.tests ?? []
  if (p.id !== 'proj_default') {
    await seedTable(api, p, 'refunds', [
      { name: 'order_id', type: 'string', required: false },
      { name: 'amount', type: 'number', required: false },
      { name: 'reason', type: 'string', required: false },
      { name: 'status', type: 'string', required: false },
    ])
  }
  const wf = await seedWorkflow(api, p, def)
  return { wf, spec }
}

export const nodeEl = (page: Page, name: string): Locator => page.locator(`.react-flow__node[data-id="${name}"]`)

export async function openEditor(page: Page, s: StackState, slug: string, wfId: string) {
  await page.goto(`${s.studioUrl}/p/${slug}/workflows/${wfId}`)
  await expect(page.getByTestId('workflow-navbar')).toBeVisible()
  await expect(page.locator('.react-flow__node').first()).toBeVisible()
}

export async function openTestsTab(page: Page) {
  const tab = page.getByTestId('bottom-tab-tests')
  if (!(await tab.click({ trial: true, timeout: 1500 }).then(() => true, () => false))) await page.getByTestId('editor-toggle-logs').click()
  await tab.click()
  await expect(page.getByTestId('tests-panel')).toBeVisible()
}

export async function runAll(page: Page) {
  await page.getByTestId('tests-run-all').click()
  await expect(page.getByTestId('tests-run-all')).toBeEnabled()
}

export const rowByName = (page: Page, name: string | RegExp): Locator =>
  page.getByTestId('test-row').filter({ hasText: name })

/** Pick an option of a base-ui Select by its trigger testid. */
export async function pick(page: Page, trigger: Locator, option: string | RegExp) {
  await trigger.click()
  await page.getByRole('option', { name: option, exact: typeof option === 'string' }).click()
}

export async function openNdv(page: Page, name: string) {
  await nodeEl(page, name).dblclick()
  await expect(page.getByTestId('ndv-modal')).toBeVisible()
  await page.getByTestId('ndv-tab-parameters').click()
}
export async function closeNdv(page: Page) {
  await page.getByTestId('ndv-close').click()
  await expect(page.getByTestId('ndv-modal')).toBeHidden()
}
/** Edit the text field of the open node panel whose current text matches `current`. */
export async function editField(page: Page, current: string | RegExp, next: string) {
  const modal = page.getByTestId('ndv-modal')
  await modal.locator('.cursor-text').filter({ hasText: current }).first().click()
  const area = modal.locator('textarea').first()
  await area.fill(next)
  await expect(area).toHaveValue(next)
  await area.blur()
}

export async function closeNodeCreator(page: Page) {
  // the node creator side panel can be open on load; close it if it is in the way
  const close = page.getByTestId('node-creator-dismiss')
  if (await close.isVisible().catch(() => false)) await close.click()
}

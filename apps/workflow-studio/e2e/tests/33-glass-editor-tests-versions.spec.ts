/**
 * The glass editor's tests and versions (BUILD-PLAN §2 W4b): a run saved as a test from the drawer, Run all,
 * the green tests chip; Publish v1, the draft-vs-v1 diff on the canvas, Restore v1. Real UI, real engine.
 */
import type { Page } from '@playwright/test'
import { test, expect, rand, type EngineApi } from '../fixtures/test'
import { seedProject, seedWorkflow, type P } from '../fixtures/seed'
import type { StackState } from '../global-setup'

const link = (a: string, b: string) => ({ source_node: a, source_output: 'main', target_node: b, target_input: 'main' })

function greetDef(name: string) {
  return {
    name,
    nodes: [
      { name: 'Start', type: 'Start', parameters: {}, position: { x: 0, y: 0 } },
      { name: 'Shape', type: 'Set', label: 'Shape person', parameters: { mode: 'manual', fields: [{ name: 'name', value: 'Ada', type: 'string' }] }, position: { x: 280, y: 0 } },
      { name: 'Greet', type: 'Set', label: 'Greet them', parameters: { mode: 'manual', fields: [{ name: 'greeting', value: 'hi', type: 'string' }] }, position: { x: 560, y: 0 } },
    ],
    connections: [link('Start', 'Shape'), link('Shape', 'Greet')],
  }
}

async function openEditor(page: Page, s: StackState, slug: string, wfId: string) {
  await page.addInitScript(() => {
    try {
      localStorage.setItem('workflow-studio:glass:view', 'editor')
    } catch {
      /* ignore */
    }
  })
  await page.goto(`${s.studioUrl}/p/${slug}/workflows/${wfId}`)
  await expect(page.getByTestId('glass-editor')).toHaveAttribute('data-view', 'editor')
  await expect(page.getByTestId('glass-edit')).toBeVisible()
  await expect(page.getByTestId('glass-node').first()).toBeVisible()
}

const node = (page: Page, name: string) => page.locator(`[data-testid="glass-node"][data-name="${name}"]`)

async function saveDraft(page: Page) {
  const btn = page.getByTestId('editor-save')
  if (await btn.isVisible()) await btn.click()
  await expect(page.getByTestId('editor-saved')).toHaveText('Saved')
}

async function rename(page: Page, name: string, to: string) {
  await node(page, name).click()
  await page.getByTestId('inspector-name').click()
  await page.getByTestId('inspector-name-input').fill(to)
  await page.getByTestId('inspector-name-input').press('Enter')
  await expect(node(page, name)).toContainText(to)
}

const seed = (api: EngineApi, p: P) => seedWorkflow(api, p, greetDef(`Greet ${rand(4)}`))

test.describe('glass editor · tests and versions', () => {
  let p: P
  test.beforeEach(async ({ api }) => {
    p = await seedProject(api, 'T33')
  })

  test('T33.1 save a run as a test, run all, the tests chip turns green', async ({ page, api, s }) => {
    const wf = await seed(api, p)
    const run = await api.post<{ execution_id: string; status: string }>(`/workflows/${wf.id}/run`, { input_data: { who: 'Ada' } })
    expect(run.status).toBe('success')
    await openEditor(page, s, p.slug, wf.id)
    await expect(page.getByTestId('test-bar')).toHaveCount(0)

    await page.getByTestId('ge-drawer-toggle').click()
    await page.getByTestId('drawer-tab-runs').click()
    const row = page.locator(`[data-testid="drawer-run-row"][data-id="${run.execution_id}"]`)
    await expect(row).toBeVisible()
    await row.getByTestId('drawer-run-save-test').click()
    await expect(page.getByTestId('test-bar')).toBeVisible()
    await saveDraft(page)
    await expect
      .poll(async () => (await api.get<{ definition: { tests?: unknown[] } }>(`/workflows/${wf.id}`)).definition.tests?.length ?? 0)
      .toBe(1)

    await page.getByTestId('drawer-tab-tests').click()
    await expect(page.getByTestId('tests-panel')).toBeVisible()
    await expect(page.getByTestId('test-row')).toHaveCount(1)
    await page.getByTestId('tests-run-all').click()
    await expect(page.getByTestId('test-bar')).toHaveAttribute('data-state', 'pass', { timeout: 30_000 })
    await expect(page.getByTestId('test-row')).toHaveAttribute('data-status', 'passed')
    await expect(page.getByTestId('tests-count')).toHaveText('1')

    // the chip opens the Tests tab
    await page.getByTestId('drawer-tab-runs').click()
    await page.getByTestId('test-bar').click()
    await expect(page.getByTestId('ge-drawer')).toHaveAttribute('data-tab', 'tests')
  })

  test('T33.2 publish v1, compare the draft with v1 on the canvas, restore v1', async ({ page, api, s }) => {
    const wf = await seed(api, p)
    await openEditor(page, s, p.slug, wf.id)

    await page.getByTestId('ge-versions').click()
    await expect(page.getByTestId('history-empty')).toBeVisible()
    await page.keyboard.press('Escape')

    await page.getByTestId('ge-publish').click()
    await page.getByTestId('editor-publish').click()
    await expect(page.getByTestId('ge-versions')).toContainText('v1', { timeout: 20_000 })

    await rename(page, 'Greet', 'Say hello')
    await saveDraft(page)
    await expect(page.getByTestId('ge-changes')).toHaveAttribute('data-count', '1')

    await page.getByTestId('ge-versions').click()
    await page.getByTestId('history-version-1').click()
    const bar = page.getByTestId('history-diff-summary')
    await expect(bar).toContainText('Draft vs v1: 0 new · 1 changed · 0 removed')

    await page.getByTestId('history-restore').click()
    await expect(bar).toBeHidden()
    await expect(node(page, 'Greet')).toContainText('Greet them')
    await expect(page.getByTestId('ge-changes')).toHaveAttribute('data-count', '0')
    await expect
      .poll(async () => (await api.get<{ definition: { nodes: Array<{ name: string; label?: string }> } }>(`/workflows/${wf.id}`)).definition.nodes.find((n) => n.name === 'Greet')?.label)
      .toBe('Greet them')
  })
})

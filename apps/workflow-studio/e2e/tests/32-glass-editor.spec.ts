/**
 * The glass editor's edit side (BUILD-PLAN §2 W4b): the inspector (rename, undo/redo, save, publish), the
 * n8n-style node modal (double-click / Enter, three panes, Test step, Esc), data passing (edge counts, the edge
 * popover, drag a field into a parameter with a resolved preview, a single step run on the upstream data),
 * the test payload and the run strip + drawer, and the step catalog. Real UI against the real engine.
 * Each test seeds its own project and workflow.
 */
import type { Page } from '@playwright/test'
import { test, expect, rand, type EngineApi } from '../fixtures/test'
import { seedProject, seedWorkflow, type P } from '../fixtures/seed'
import type { StackState } from '../global-setup'

const link = (a: string, b: string) => ({ source_node: a, source_output: 'main', target_node: b, target_input: 'main' })

/** Start → Shape {name: Ada} → Greet {greeting: "hi"}. */
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
  await expect(page.getByTestId('ge-draftbar')).toBeVisible()
  await expect(page.getByTestId('glass-node').first()).toBeVisible()
  await expect(page.getByTestId('workflow-navbar')).toHaveCount(0)
}

const node = (page: Page, name: string) => page.locator(`[data-testid="glass-node"][data-name="${name}"]`)

async function seed(api: EngineApi, p: P) {
  return seedWorkflow(api, p, greetDef(`Greet ${rand(4)}`))
}

/** Save now if the draft is unsaved (autosave may already have done it). */
async function saveDraft(page: Page) {
  const btn = page.getByTestId('editor-save')
  if (await btn.isVisible()) await btn.click()
  await expect(page.getByTestId('editor-saved')).toHaveText('Saved')
}

async function runFromStrip(page: Page) {
  await page.getByTestId('ge-strip-run').click()
  await expect(page.getByTestId('ge-timeline-step')).toHaveCount(3, { timeout: 30_000 })
  await expect(page.getByTestId('ge-strip-run')).toBeVisible({ timeout: 30_000 })
  for (const n of ['Start', 'Shape', 'Greet']) await expect(page.locator(`[data-testid="ge-timeline-step"][data-name="${n}"]`)).toHaveAttribute('data-status', 'success')
}

test.describe('glass editor · edit side', () => {
  let p: P
  test.beforeEach(async ({ api }) => {
    p = await seedProject(api, 'T32')
  })

  test('T32.1 inspector: rename, undo/redo, save, publish v1', async ({ page, api, s }) => {
    const wf = await seed(api, p)
    await openEditor(page, s, p.slug, wf.id)
    await expect(page.getByTestId('ge-versions')).toContainText('never published')

    await node(page, 'Greet').click()
    const insp = page.getByTestId('ge-inspector')
    await expect(insp).toHaveAttribute('data-node', 'Greet')
    await expect(page.getByTestId('inspector-setup')).toBeVisible()
    await page.getByTestId('inspector-name').click()
    await page.getByTestId('inspector-name-input').fill('Say hello')
    await page.getByTestId('inspector-name-input').press('Enter')
    await expect(node(page, 'Greet')).toContainText('Say hello')

    await page.getByTestId('ge-undo').click()
    await expect(node(page, 'Greet')).toContainText('Greet them')
    await page.getByTestId('ge-redo').click()
    await expect(node(page, 'Greet')).toContainText('Say hello')

    await page.getByTestId('inspector-tab-settings').click()
    await page.getByTestId('ndv-notes').fill('says hello')
    await saveDraft(page)
    await expect
      .poll(async () => (await api.get<{ definition: { nodes: Array<{ name: string; label?: string }> } }>(`/workflows/${wf.id}`)).definition.nodes.find((n) => n.name === 'Greet')?.label)
      .toBe('Say hello')

    await page.getByTestId('ge-publish').click()
    await page.getByTestId('ge-publish-message').fill('first cut')
    await page.getByTestId('editor-publish').click()
    await expect(page.getByTestId('ge-versions')).toContainText('v1', { timeout: 20_000 })
    const versions = await api.get<Array<{ version_number: number; message?: string }>>(`/workflows/${wf.id}/versions`)
    expect(versions.map((v) => v.version_number)).toContain(1)
    await expect(page.getByTestId('ge-changes')).toHaveAttribute('data-count', '0')

    // a change after publishing shows up in "N changes ▾" and points back at its step
    await node(page, 'Shape').click()
    await page.getByTestId('inspector-name').click()
    await page.getByTestId('inspector-name-input').fill('Shape the person')
    await page.getByTestId('inspector-name-input').press('Enter')
    await expect(page.getByTestId('ge-changes')).toHaveAttribute('data-count', '1')
    await page.getByTestId('ge-changes').click()
    await expect(page.getByTestId('ge-changes-pop').getByTestId('ge-change-row')).toHaveCount(1)
    await expect(page.getByTestId('ge-change-row')).toHaveAttribute('data-op', 'chg')
  })

  test('T32.2 double-click opens the node modal: three panes, Test step shows output, Esc closes', async ({ page, api, s }) => {
    const wf = await seed(api, p)
    await api.post(`/workflows/${wf.id}/run`, { input_data: {} })
    await openEditor(page, s, p.slug, wf.id)

    await node(page, 'Shape').dblclick()
    const modal = page.getByTestId('ndv-modal')
    await expect(modal).toBeVisible()
    await expect(page.getByTestId('ndv-scrim')).toBeVisible()
    for (const pane of ['ndv-pane-input', 'ndv-pane-setup', 'ndv-pane-output']) await expect(page.getByTestId(pane)).toBeVisible()
    const box = await modal.boundingBox()
    expect(box!.width).toBeGreaterThan(1440 * 0.85)
    expect(box!.height).toBeGreaterThan(900 * 0.75)

    await page.getByTestId('ndv-test-step').click()
    await expect(page.getByTestId('ndv-test-ok')).toContainText('1 item out', { timeout: 30_000 })
    await expect(page.getByTestId('ndv-pane-output')).toContainText('name')
    await page.getByTestId('ndv-output-mode-json').click()
    await expect(page.getByTestId('ndv-pane-output')).toContainText('Ada')

    // header picker: next step, then back
    await page.getByTestId('ndv-next').click()
    await expect(page.getByTestId('ndv-node-select')).toContainText('Greet them')
    await page.getByTestId('ndv-prev').click()
    await expect(page.getByTestId('ndv-node-select')).toContainText('Shape person')

    await page.keyboard.press('Escape')
    await expect(modal).toBeHidden()
    // the single-click compact inspector stays; Enter on the selected step opens the modal
    await expect(page.getByTestId('ge-inspector')).toBeVisible()
    await node(page, 'Greet').click()
    await page.locator('.react-flow__pane').focus().catch(() => undefined)
    await node(page, 'Greet').press('Enter')
    await expect(modal).toBeVisible()
    await page.getByTestId('ndv-back').click()
    await expect(modal).toBeHidden()
  })

  test('T32.3 data passing: edge counts, edge popover, drag a field into a parameter, test the step on the upstream data', async ({ page, api, s }) => {
    const wf = await seed(api, p)
    await openEditor(page, s, p.slug, wf.id)
    await runFromStrip(page)

    const counts = page.getByTestId('edge-count')
    await expect(counts).toHaveCount(2)
    for (let i = 0; i < 2; i++) await expect(counts.nth(i)).toHaveAttribute('data-count', '1')
    const toGreet = page.locator('[data-testid="edge-count"][data-target="Greet"]')
    await toGreet.click()
    const pop = page.getByTestId('edge-popover')
    await expect(pop).toBeVisible()
    await expect(pop).toContainText('Ada')
    await page.getByTestId('edge-popover-json').click()
    await expect(page.getByTestId('edge-popover-pre')).toContainText('"name": "Ada"')
    await page.getByTestId('edge-popover-open').click()

    const modal = page.getByTestId('ndv-modal')
    await expect(modal).toBeVisible()
    await expect(page.getByTestId('ndv-node-select')).toContainText('Greet them')
    const input = page.getByTestId('ndv-pane-input')
    await expect(input).toContainText('Shape person')
    await expect(input).toContainText('name')

    const source = input.locator('[draggable="true"]').filter({ has: page.locator('span.font-mono', { hasText: /^name$/ }) }).first()
    const target = page.getByTestId('ndv-pane-setup').locator('div.field', { hasText: /^\s*hi\s*$/ }).first()
    await source.dragTo(target)
    await expect(page.getByTestId('ndv-pane-setup')).toContainText('$json.name')
    await expect(page.getByTestId('expr-preview').first()).toContainText('hi Ada')

    await page.getByTestId('ndv-test-step').click()
    await expect(page.getByTestId('ndv-test-ok')).toContainText('1 item out', { timeout: 30_000 })
    await page.getByTestId('ndv-output-mode-json').click()
    await expect(page.getByTestId('ndv-pane-output')).toContainText('hi Ada')
    await page.keyboard.press('Escape')
    await expect(modal).toBeHidden()

    await saveDraft(page)
    await expect
      .poll(async () => (await api.get<{ definition: { nodes: Array<{ name: string; parameters: { fields: Array<{ value: string }> } }> } }>(`/workflows/${wf.id}`)).definition.nodes.find((n) => n.name === 'Greet')!.parameters.fields[0].value)
      .toContain('{{ $json.name }}')
  })

  test('T32.4 test payload, Run, the timeline and the drawer (runs, logs)', async ({ page, api, s }) => {
    const wf = await seed(api, p)
    await openEditor(page, s, p.slug, wf.id)
    await expect(page.getByTestId('ge-timeline-empty')).toBeVisible()

    await node(page, 'Start').click()
    await expect(page.getByTestId('ge-test-payload')).toBeVisible()
    const editor = page.getByTestId('payload-editor').locator('.cm-content')
    await editor.click()
    await page.keyboard.press('ControlOrMeta+a')
    await page.keyboard.insertText('{"who": "Grace"}')
    await expect(page.getByTestId('ge-payload-validity')).toContainText('Valid JSON · 1 fields')
    await page.getByTestId('ge-payload-run').click()
    await expect(page.getByTestId('ge-timeline-step')).toHaveCount(3, { timeout: 30_000 })
    await expect(page.getByTestId('ge-run-total')).toBeVisible()

    await page.getByTestId('ge-drawer-toggle').click()
    await expect(page.getByTestId('ge-drawer')).toBeVisible()
    await page.getByTestId('drawer-tab-logs').click()
    await expect(page.getByTestId('log-row')).toHaveCount(3)
    await page.locator('[data-testid="log-row"][data-name="Start"] button').first().click()
    await expect(page.getByTestId('log-output')).toContainText('Grace')

    await page.getByTestId('drawer-tab-runs').click()
    await expect(page.getByTestId('drawer-run-row')).toHaveCount(1, { timeout: 20_000 })
    await expect(page.getByTestId('drawer-run-row')).toHaveAttribute('data-status', /success|completed/, { timeout: 20_000 })

    // clicking a timeline step selects it in the inspector
    await page.locator('[data-testid="ge-timeline-step"][data-name="Greet"]').click()
    await expect(page.getByTestId('ge-inspector')).toHaveAttribute('data-node', 'Greet')
    await page.getByTestId('drawer-close').click()
    await expect(page.getByTestId('ge-drawer')).toBeHidden()
  })

  test('T32.5 catalog: add a connected step from the + handle, then delete it', async ({ page, api, s }) => {
    const wf = await seed(api, p)
    await openEditor(page, s, p.slug, wf.id)
    await expect(page.getByTestId('glass-node')).toHaveCount(3)

    await node(page, 'Greet').click()
    await node(page, 'Greet').getByTestId('glass-node-add').click()
    const cat = page.getByTestId('ge-catalog')
    await expect(cat).toBeVisible()
    await expect(cat).toContainText('Add a step after Greet them')
    await page.getByTestId('ge-catalog-search').fill('Set')
    await cat.locator('[data-testid="ge-catalog-item"][data-type="Set"]').click()
    await expect(cat).toBeHidden()
    await expect(page.getByTestId('glass-node')).toHaveCount(4)
    await expect(page.locator('.react-flow__edge')).toHaveCount(3)
    await expect(page.getByTestId('ge-inspector')).toBeVisible()
    const added = await page.getByTestId('ge-inspector').getAttribute('data-node')
    expect(added).not.toBe('Greet')

    await page.getByTestId('inspector-delete').click()
    await expect(page.getByTestId('glass-node')).toHaveCount(3)
    await expect(page.locator('.react-flow__edge')).toHaveCount(2)

    // the tools bar + opens it too; Escape closes it
    await page.getByTestId('ge-tool-add').click()
    await expect(cat).toBeVisible()
    await page.getByTestId('ge-catalog-search').press('Escape')
    await expect(cat).toBeHidden()
  })
})

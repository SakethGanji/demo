/**
 * Glass editor parity walk (PARITY.md rows marked "yes"): what 31-33 do not cover, driven like a user against
 * the real engine. Stop a run, Pinned / Typed test input, Unpublish, the publish impact dialog, Retry, sticky
 * notes, Tidy / zoom / minimap, Run to / Run from, the context menu, ⌘K, copy / paste / delete / undo, the
 * Page | Editor | Definition toggle keeping your place, and the edge cases (empty workflow, a failing step,
 * 36 steps, rapid toggling, unsaved changes on navigation, dark mode). Every write is re-read from the API.
 */
import type { Page } from '@playwright/test'
import { test, expect, rand, type EngineApi } from '../fixtures/test'
import { seedProject, seedWorkflow, type P } from '../fixtures/seed'
import type { StackState } from '../global-setup'

type Json = Record<string, unknown>
const link = (a: string, b: string) => ({ source_node: a, source_output: 'main', target_node: b, target_input: 'main' })
const setN = (name: string, field: string, value: string, x: number, extra: Json = {}) => ({
  name,
  type: 'Set',
  label: name,
  parameters: { mode: 'manual', fields: [{ name: field, value, type: 'string' }] },
  position: { x, y: 0 },
  ...extra,
})
const START = { name: 'Start', type: 'Start', parameters: {}, position: { x: 0, y: 0 } }

/** Start → Shape {name: Ada} → Greet {greeting: "hi {{ $json.name }}"}. */
function greetDef(name: string, shapeExtra: Json = {}): Json {
  return {
    name,
    nodes: [START, setN('Shape', 'name', 'Ada', 280, shapeExtra), setN('Greet', 'greeting', 'hi {{ $json.name }}', 560)],
    connections: [link('Start', 'Shape'), link('Shape', 'Greet')],
  }
}

interface Wf {
  id: string
  active: boolean
  definition: { nodes: Array<{ name: string; type: string; label?: string; disabled?: boolean; position: { x: number; y: number }; parameters: Json; pinnedData?: Array<{ json: Json }> }>; connections: unknown[] }
}
interface ExecRow {
  id: string
  status: string
  start_time: string
}
interface ExecDetail {
  id: string
  status: string
  errors: Array<{ node_name: string; error: string }>
  node_data: Record<string, Array<{ json: Json }>>
}
const getWf = (api: EngineApi, id: string) => api.get<Wf>(`/workflows/${id}`)
const listExecs = (api: EngineApi, wf: string) => api.get<ExecRow[]>(`/executions?workflow_id=${wf}`)

/** The newest run after `before` runs existed, once it is no longer running. */
async function nextExec(api: EngineApi, wfId: string, before: number): Promise<ExecDetail> {
  await expect.poll(async () => (await listExecs(api, wfId)).filter((e) => e.status !== 'running').length, { timeout: 30_000 }).toBeGreaterThan(before)
  const list = (await listExecs(api, wfId)).sort((a, b) => (a.start_time < b.start_time ? 1 : -1))
  return api.get<ExecDetail>(`/executions/${list[0].id}`)
}

async function openView(page: Page, s: StackState, slug: string, wfId: string, view: 'page' | 'editor' | 'definition') {
  await page.addInitScript((v) => {
    try {
      localStorage.setItem('workflow-studio:glass:view', v)
    } catch {
      /* ignore */
    }
  }, view)
  await page.goto(`${s.studioUrl}/p/${slug}/workflows/${wfId}`)
  await expect(page.getByTestId('glass-editor')).toHaveAttribute('data-view', view)
  if (view === 'editor') {
    await expect(page.getByTestId('glass-edit')).toBeVisible()
    await expect(page.getByTestId('glass-node').first()).toBeVisible()
  }
}
const openEditor = (page: Page, s: StackState, slug: string, id: string) => openView(page, s, slug, id, 'editor')
const node = (page: Page, name: string) => page.locator(`[data-testid="glass-node"][data-name="${name}"]`)
/** Page and Canvas ('editor') are the two views on the switch; the definition opens from the header's ⋯ menu. */
const switchTo = async (page: Page, v: 'page' | 'editor' | 'definition', opts?: { noWaitAfter?: boolean }) => {
  if (v !== 'definition') return page.getByTestId('glass-view-switch').locator(`[data-value="${v}"]`).click(opts)
  await page.getByTestId('glass-more').click(opts)
  await page.getByTestId('glass-more-definition').click(opts)
}

async function saveDraft(page: Page) {
  const btn = page.getByTestId('editor-save')
  if (await btn.isVisible()) await btn.click()
  await expect(page.getByTestId('editor-saved')).toHaveText('Saved')
}
async function runFromStrip(page: Page, steps: number) {
  await page.getByTestId('ge-strip-run').click()
  await expect(page.getByTestId('ge-timeline-step')).toHaveCount(steps, { timeout: 60_000 })
  await expect(page.getByTestId('ge-strip-run')).toBeVisible({ timeout: 60_000 })
}
const scaleOf = (page: Page) =>
  page.locator('.react-flow__viewport').evaluate((el) => {
    const m = /scale\(([\d.]+)\)/.exec((el as HTMLElement).style.transform)
    return m ? Number(m[1]) : 1
  })
const translateOf = (page: Page) =>
  page.locator('.react-flow__viewport').evaluate((el) => {
    const m = /translate\((-?[\d.]+)px,\s*(-?[\d.]+)px\)/.exec((el as HTMLElement).style.transform)
    return m ? [Number(m[1]), Number(m[2])] : [0, 0]
  })
const xOf = (page: Page, name: string) => node(page, name).evaluate((el) => el.closest<HTMLElement>('.react-flow__node')!.getBoundingClientRect().x)
async function setCode(page: Page, testId: string, text: string) {
  const c = page.getByTestId(testId).locator('.cm-content')
  await c.click()
  await page.keyboard.press('ControlOrMeta+a')
  await page.keyboard.press('Delete')
  await page.keyboard.insertText(text)
}

const FAIL_CODE = "raise ValueError('kaboom')\n"
const failDef = (name: string): Json => ({
  name,
  nodes: [START, { name: 'Boom', type: 'Code', label: 'Boom', parameters: { code: FAIL_CODE }, position: { x: 280, y: 0 } }, setN('After', 'ok', 'yes', 560)],
  connections: [link('Start', 'Boom'), link('Boom', 'After')],
})

test.describe('glass editor · parity walk', () => {
  let p: P
  test.beforeEach(async ({ api }) => {
    p = await seedProject(api, 'T34')
  })
  const seed = (api: EngineApi, extra: Json = {}) => seedWorkflow(api, p, greetDef(`Greet ${rand(4)}`, extra))

  // ── C2 / C3 / C11: runs ────────────────────────────────────────────────────────────────
  test('T34.1 Stop ends a running run: the strip returns to Run and the engine run is not left running', async ({ page, api, s }) => {
    const wf = await seedWorkflow(api, p, {
      name: `Slow ${rand(4)}`,
      nodes: [START, { name: 'Pause', type: 'Wait', label: 'Pause', parameters: { unit: 'seconds', duration: 25 }, position: { x: 280, y: 0 } }, setN('Done', 'ok', 'yes', 560)],
      connections: [link('Start', 'Pause'), link('Pause', 'Done')],
    })
    await openEditor(page, s, p.slug, wf.id)
    await page.getByTestId('ge-strip-run').click()
    await expect(page.getByTestId('ge-strip-stop')).toBeVisible()
    await expect.poll(async () => (await listExecs(api, wf.id)).some((e) => e.status === 'running')).toBe(true)
    await page.getByTestId('ge-strip-stop').click()
    await expect(page.getByTestId('ge-strip-run')).toBeVisible()
    await expect(page.getByTestId('ge-strip-stop')).toHaveCount(0)
    // the engine side: the run must stop, not keep going to "success" 25 s later
    await expect
      .poll(async () => (await listExecs(api, wf.id))[0]?.status, { timeout: 10_000, message: 'engine run still "running" after Stop' })
      .not.toBe('running')
    const last = await api.get<ExecDetail>(`/executions/${(await listExecs(api, wf.id))[0].id}`)
    expect(last.node_data['Done'] ?? []).toHaveLength(0)
  })

  test('T34.2 Run to and Run from (inspector footer) run only the part asked, verified in the engine', async ({ page, api, s }) => {
    const wf = await seed(api)
    await openEditor(page, s, p.slug, wf.id)
    let before = (await listExecs(api, wf.id)).length
    await node(page, 'Shape').click()
    await page.getByTestId('inspector-run-to').click()
    await expect(page.getByTestId('inspector-ok')).toContainText('Ran up to this step', { timeout: 30_000 })
    const to = await nextExec(api, wf.id, before)
    expect(Object.keys(to.node_data).sort()).toEqual(['Shape', 'Start'])
    await expect(page.locator('[data-testid="ge-timeline-step"][data-name="Greet"]')).toHaveCount(0)

    before = (await listExecs(api, wf.id)).length
    await node(page, 'Greet').click()
    await page.getByTestId('inspector-run-from').click()
    await expect(page.getByTestId('inspector-ok')).toContainText('Ran from this step', { timeout: 30_000 })
    const from = await nextExec(api, wf.id, before)
    expect(from.node_data['Greet']?.[0]?.json).toMatchObject({ greeting: expect.stringContaining('hi') })
    await expect(page.locator('[data-testid="ge-timeline-step"][data-name="Greet"]')).toHaveAttribute('data-status', 'success')
  })

  test('T34.3 context menu: Run to here, Run from here, Test step and Disable act on the right step', async ({ page, api, s }) => {
    const wf = await seed(api)
    await openEditor(page, s, p.slug, wf.id)
    let before = (await listExecs(api, wf.id)).length
    await node(page, 'Shape').click({ button: 'right' })
    await expect(page.getByTestId('ctx-run-to')).toBeVisible()
    await page.getByTestId('ctx-run-to').click()
    const to = await nextExec(api, wf.id, before)
    expect(Object.keys(to.node_data)).not.toContain('Greet')
    expect(Object.keys(to.node_data)).toContain('Shape')

    before = (await listExecs(api, wf.id)).length
    await node(page, 'Greet').click({ button: 'right' })
    await page.getByTestId('ctx-run-from').click()
    const from = await nextExec(api, wf.id, before)
    expect(Object.keys(from.node_data)).toContain('Greet')

    before = (await listExecs(api, wf.id)).length
    await node(page, 'Shape').click({ button: 'right' })
    await page.getByTestId('ctx-test-step').click()
    await expect.poll(async () => (await listExecs(api, wf.id)).length, { timeout: 30_000 }).toBeGreaterThanOrEqual(before)

    await node(page, 'Greet').click({ button: 'right' })
    await page.getByTestId('ctx-toggle-disable').click()
    await expect(node(page, 'Greet')).toHaveClass(/disabled/)
    await saveDraft(page)
    await expect.poll(async () => (await getWf(api, wf.id)).definition.nodes.find((n) => n.name === 'Greet')?.disabled).toBe(true)
  })

  test('T34.4 a failing step: red on the strip, canvas and inspector with the real error; Retry (drawer and Page) starts a new run', async ({ page, api, s }) => {
    const wf = await seedWorkflow(api, p, failDef(`Fails ${rand(4)}`))
    await openEditor(page, s, p.slug, wf.id)
    await page.getByTestId('ge-strip-run').click()
    await expect(page.locator('[data-testid="ge-timeline-step"][data-name="Boom"]')).toHaveAttribute('data-status', 'error', { timeout: 30_000 })
    await expect(page.getByTestId('ge-run-total')).toContainText('failed')
    await expect(page.locator('[data-testid="ge-timeline-step"][data-name="After"]')).toHaveCount(0)
    const run = await nextExec(api, wf.id, 0)
    expect(run.status).toBe('failed')
    expect(run.errors[0]).toMatchObject({ node_name: 'Boom' })
    expect(run.errors[0].error).toContain('kaboom')

    await node(page, 'Boom').click()
    await page.getByTestId('inspector-tab-data').click()
    await expect(page.getByTestId('inspector-data-error')).toContainText('kaboom')

    // Retry from the drawer
    await page.getByTestId('ge-drawer-toggle').click()
    await page.getByTestId('drawer-tab-runs').click()
    const row = page.locator(`[data-testid="drawer-run-row"][data-id="${run.id}"]`)
    await expect(row).toBeVisible({ timeout: 20_000 })
    await row.getByTestId('drawer-run-retry').click()
    await expect.poll(async () => (await listExecs(api, wf.id)).length, { timeout: 30_000 }).toBe(2)
    const ids = (await listExecs(api, wf.id)).map((e) => e.id)
    expect(new Set(ids).size).toBe(2)

    // and on the Page: pick the failed run, ↻ Retry
    await switchTo(page, 'page')
    await page.getByTestId('glass-run-picker').click()
    await page.locator(`[data-testid="glass-run-option"][data-run-id="${run.id}"]`).click()
    await expect(page.getByTestId('glass-run-retry')).toBeVisible()
    await page.getByTestId('glass-run-retry').click()
    await expect.poll(async () => (await listExecs(api, wf.id)).length, { timeout: 30_000 }).toBe(3)
  })

  // ── A2 / A3: test-step input sources ────────────────────────────────────────────────────
  test('T34.5 node modal, Typed source: the step runs on the JSON typed; bad JSON is refused with a message', async ({ page, api, s }) => {
    const wf = await seed(api)
    await openEditor(page, s, p.slug, wf.id)
    await node(page, 'Greet').dblclick()
    await expect(page.getByTestId('ndv-modal')).toBeVisible()
    await page.getByTestId('ndv-test-source-input').click()
    await setCode(page, 'ndv-test-input-editor', '{"name": "Grace"}')
    await page.getByTestId('ndv-test-step').click()
    await expect(page.getByTestId('ndv-test-ok')).toContainText('1 item out', { timeout: 30_000 })
    await page.getByTestId('ndv-output-mode-json').click()
    await expect(page.getByTestId('ndv-pane-output')).toContainText('hi Grace')

    await setCode(page, 'ndv-test-input-editor', '[{"name": "A"}, {"name": "B"}, {"name": "C"}]')
    await page.getByTestId('ndv-test-step').click()
    await expect(page.getByTestId('ndv-test-ok')).toContainText('3 items out', { timeout: 30_000 })

    await setCode(page, 'ndv-test-input-editor', '{"name": ')
    await page.getByTestId('ndv-test-step').click()
    await expect(page.getByTestId('ndv-test-error')).toContainText('not valid JSON')
  })

  test('T34.6 node modal, Pinned source: the step runs on the upstream pinned data, not on what the step before makes', async ({ page, api, s }) => {
    const wf = await seed(api, { pinned_data: [{ json: { name: 'Pinned Pat' } }] })
    await openEditor(page, s, p.slug, wf.id)
    await expect(node(page, 'Shape').getByTestId('glass-node-pinned')).toBeVisible()
    await node(page, 'Greet').dblclick()
    await page.getByTestId('ndv-test-source-pinned').click()
    await page.getByTestId('ndv-test-step').click()
    await expect(page.getByTestId('ndv-test-ok')).toContainText('1 item out', { timeout: 30_000 })
    await page.getByTestId('ndv-output-mode-json').click()
    await expect(page.getByTestId('ndv-pane-output')).toContainText('hi Pinned Pat')
    await expect(page.getByTestId('ndv-pane-output')).not.toContainText('hi Ada')
  })

  test('T34.7 pin a step\'s output from a run, save, reload: the pin is on the step and in the API; unpin removes it', async ({ page, api, s }) => {
    const wf = await seed(api)
    await openEditor(page, s, p.slug, wf.id)
    await runFromStrip(page, 3)
    await node(page, 'Shape').click()
    await page.getByTestId('inspector-tab-data').click()
    await page.getByTestId('inspector-pin').click()
    await expect(node(page, 'Shape').getByTestId('glass-node-pinned')).toBeVisible()
    await saveDraft(page)
    await expect.poll(async () => (await getWf(api, wf.id)).definition.nodes.find((n) => n.name === 'Shape')?.pinnedData?.[0]?.json).toMatchObject({ name: 'Ada' })
    await page.reload()
    await expect(node(page, 'Shape').getByTestId('glass-node-pinned')).toBeVisible()
    await node(page, 'Shape').click()
    await page.getByTestId('inspector-tab-data').click()
    await page.getByTestId('inspector-unpin').click()
    await expect(node(page, 'Shape').getByTestId('glass-node-pinned')).toHaveCount(0)
    await saveDraft(page)
    await expect.poll(async () => (await getWf(api, wf.id)).definition.nodes.find((n) => n.name === 'Shape')?.pinnedData?.length ?? 0).toBe(0)
  })

  test('T34.8 passing items on: a step that makes 3 items shows 3 on the edge, in the popover, and in the next step\'s input and test', async ({ page, api, s }) => {
    const wf = await seedWorkflow(api, p, {
      name: `Fan ${rand(4)}`,
      nodes: [
        START,
        { name: 'Fan', type: 'Code', label: 'Fan out', parameters: { code: "return [{'json': {'n': i}} for i in range(3)]\n" }, position: { x: 280, y: 0 } },
        setN('Tag', 'tag', 'n={{ $json.n }}', 560),
      ],
      connections: [link('Start', 'Fan'), link('Fan', 'Tag')],
    })
    await openEditor(page, s, p.slug, wf.id)
    await runFromStrip(page, 3)
    const run = await nextExec(api, wf.id, 0)
    expect(run.node_data['Fan']).toHaveLength(3)
    const toTag = page.locator('[data-testid="edge-count"][data-target="Tag"]')
    await expect(toTag).toHaveAttribute('data-count', '3')
    await toTag.click()
    await page.getByTestId('edge-popover-json').click()
    const pre = page.getByTestId('edge-popover-pre')
    for (const n of [0, 1, 2]) await expect(pre).toContainText(`"n": ${n}`)
    await page.getByTestId('edge-popover-open').click()
    await expect(page.getByTestId('ndv-pane-input')).toContainText('3 items')
    await page.getByTestId('ndv-test-step').click()
    await expect(page.getByTestId('ndv-test-ok')).toContainText('3 items out', { timeout: 30_000 })
    await page.getByTestId('ndv-output-mode-json').click()
    for (const n of [0, 1, 2]) await expect(page.getByTestId('ndv-pane-output')).toContainText(`n=${n}`)
  })

  // ── F3 / F4 ─────────────────────────────────────────────────────────────────────────────
  test('T34.9 Unpublish from the publish popover takes the workflow offline; the Page header then shows Draft and can publish again', async ({ page, api, s }) => {
    const wf = await seed(api)
    await api.post(`/workflows/${wf.id}/publish`, {})
    expect((await getWf(api, wf.id)).active).toBe(true)
    await openEditor(page, s, p.slug, wf.id)
    await expect(page.getByTestId('ge-versions')).toContainText('v1 live')
    await page.getByTestId('ge-publish').click()
    await page.getByTestId('editor-unpublish').click()
    await expect.poll(async () => (await getWf(api, wf.id)).active).toBe(false)
    await expect(page.getByTestId('ge-versions')).not.toContainText('live')

    await switchTo(page, 'page')
    await expect(page.getByTestId('glass-status')).toContainText('Draft')
    await page.getByTestId('glass-more').click()
    await page.getByTestId('glass-publish').click()
    await expect.poll(async () => (await getWf(api, wf.id)).active).toBe(true)
    await expect(page.getByTestId('glass-status')).toContainText('Live')
    const versions = await api.get<Array<{ version_number: number }>>(`/workflows/${wf.id}/versions`)
    expect(Math.max(...versions.map((v) => v.version_number))).toBe(2)
    await page.getByTestId('glass-more').click()
    await page.getByTestId('glass-unpublish').click()
    await expect.poll(async () => (await getWf(api, wf.id)).active).toBe(false)
  })

  test('T34.10 publish impact: a breaking change to a sub-workflow names the parent that follows "latest"', async ({ page, api, s }) => {
    const fields = [{ name: 'customer_id', type: 'string', required: true, description: '' }]
    const inputs = (extra: Json[] = []) => ({ name: 'Inputs', type: 'ExecuteWorkflowTrigger', parameters: { inputSource: 'fields', fields: [...fields, ...extra] }, position: { x: 0, y: 0 } })
    const child = await seedWorkflow(api, p, { name: `Child ${rand(4)}`, nodes: [inputs(), setN('Verdict', 'ref', 'K-{{ $json.customer_id }}', 260)], connections: [link('Inputs', 'Verdict')] })
    await api.post(`/workflows/${child.id}/publish`, {})
    const parent = await seedWorkflow(api, p, {
      name: `Parent ${rand(4)}`,
      nodes: [
        START,
        { name: 'Call', type: 'ExecuteWorkflow', parameters: { workflowId: child.id, version: 'latest', inputMode: 'map', inputs: { customer_id: '{{ "C-1" }}' }, includeItem: false, mode: 'once', timeoutSeconds: 300, outputMode: 'items' }, position: { x: 260, y: 0 } },
      ],
      connections: [link('Start', 'Call')],
    })
    await api.post(`/workflows/${parent.id}/publish`, {})
    // a new required input the caller does not send
    await api.put(`/workflows/${child.id}`, {
      nodes: [inputs([{ name: 'branch_code', type: 'string', required: true, description: '' }]), setN('Verdict', 'ref', 'K-{{ $json.customer_id }}', 260)],
      connections: [link('Inputs', 'Verdict')],
    })
    await openEditor(page, s, p.slug, child.id)
    await page.getByTestId('ge-publish').click()
    await page.getByTestId('editor-publish').click()
    await expect(page.getByTestId('publish-impact-dialog')).toBeVisible({ timeout: 20_000 })
    await expect(page.getByTestId('publish-impact-row')).toHaveCount(1)
    await expect(page.getByTestId('publish-impact-row')).toHaveAttribute('data-parent-id', parent.id)
    await page.getByTestId('publish-impact-confirm').click()
    await expect(page.getByTestId('publish-impact-dialog')).toBeHidden()
    const versions = await api.get<Array<{ version_number: number }>>(`/workflows/${child.id}/versions`)
    expect(Math.max(...versions.map((v) => v.version_number))).toBe(2)
  })

  // ── E: canvas ──────────────────────────────────────────────────────────────────────────
  test('T34.11 sticky notes: add by tool and by S, write one, delete one; they survive Save and a reload', async ({ page, api, s }) => {
    const wf = await seed(api)
    await openEditor(page, s, p.slug, wf.id)
    const notes = page.locator('.react-flow__node-stickyNote')
    await page.getByTestId('ge-tool-note').click()
    await expect(notes).toHaveCount(1)
    await page.locator('.react-flow__pane').click({ position: { x: 700, y: 650 } })
    await page.keyboard.press('s')
    await expect(notes).toHaveCount(2)
    // both notes were dropped in the same spot and the newest is on top: write on it, move it away, delete the other
    const top = notes.last()
    await top.getByText('Double-click to edit...').dblclick()
    await top.locator('textarea').fill('Check sanctions list first')
    await top.locator('textarea').blur()
    await expect(top).toContainText('Check sanctions list first')
    const nb = (await top.boundingBox())!
    await page.mouse.move(nb.x + nb.width / 2, nb.y + nb.height - 10)
    await page.mouse.down()
    await page.mouse.move(nb.x + nb.width / 2 - 450, nb.y + nb.height - 10 + 120, { steps: 8 })
    await page.mouse.up()
    await notes.first().click()
    await notes.first().getByTitle('Delete note').click()
    await expect(notes).toHaveCount(1)
    await saveDraft(page)
    await page.reload()
    await expect(page.getByTestId('glass-node').first()).toBeVisible()
    await expect(page.locator('.react-flow__node-stickyNote'), 'sticky note lost on reload').toHaveCount(1)
    await expect(page.locator('.react-flow__node-stickyNote')).toContainText('Check sanctions list first')
  })

  test('T34.12 Tidy up lays the chain out left to right without overlap and Save keeps it; zoom tools and shortcuts change the scale; Fit brings everything back', async ({ page, api, s }) => {
    const wf = await seedWorkflow(api, p, {
      ...greetDef(`Tidy ${rand(4)}`),
      nodes: greetDef('x').nodes && (greetDef('x').nodes as Json[]).map((n, i) => ({ ...n, position: { x: i * 31, y: i % 2 ? 380 : 0 } })),
    })
    await openEditor(page, s, p.slug, wf.id)
    await page.getByTestId('ge-tool-tidy').click()
    await expect
      .poll(async () => {
        const xs = [await xOf(page, 'Start'), await xOf(page, 'Shape'), await xOf(page, 'Greet')]
        return xs[0] < xs[1] && xs[1] < xs[2] && xs[1] - xs[0] > 150
      })
      .toBe(true)
    await saveDraft(page)
    await expect
      .poll(async () => {
        const n = (await getWf(api, wf.id)).definition.nodes
        const x = (name: string) => n.find((v) => v.name === name)!.position.x
        return x('Start') < x('Shape') && x('Shape') < x('Greet')
      })
      .toBe(true)

    const tools = page.getByTestId('ge-tools')
    const s0 = await scaleOf(page)
    await tools.getByRole('button', { name: 'Zoom in' }).click()
    await expect.poll(() => scaleOf(page)).toBeGreaterThan(s0)
    const s1 = await scaleOf(page)
    await tools.getByRole('button', { name: 'Zoom out' }).click()
    await tools.getByRole('button', { name: 'Zoom out' }).click()
    await expect.poll(() => scaleOf(page)).toBeLessThan(s1)
    const s2 = await scaleOf(page)
    await page.locator('.react-flow__pane').click({ position: { x: 700, y: 700 } })
    await page.keyboard.press('ControlOrMeta+=')
    await expect.poll(() => scaleOf(page)).toBeGreaterThan(s2)
    await page.keyboard.press('ControlOrMeta+-')
    await page.keyboard.press('ControlOrMeta+-')
    await page.keyboard.press('ControlOrMeta+-')
    await expect.poll(() => scaleOf(page)).toBeLessThan(s2)
    // push the graph off screen, then Fit
    await page.locator('.react-flow__pane').click({ position: { x: 700, y: 700 } })
    await page.getByTestId('ge-tool-hand').click()
    await page.mouse.move(700, 700)
    await page.mouse.down()
    await page.mouse.move(1300, 120, { steps: 6 })
    await page.mouse.up()
    await page.getByTestId('ge-tool-pointer').click()
    await page.getByTestId('ge-tool-fit').click()
    await expect
      .poll(async () => {
        const vp = page.viewportSize()!
        for (const n of ['Start', 'Greet']) {
          const b = await node(page, n).boundingBox()
          if (!b || b.x < 0 || b.x + b.width > vp.width || b.y < 0 || b.y + b.height > vp.height) return false
        }
        return true
      })
      .toBe(true)
  })

  test('T34.13 copy, paste, duplicate, delete and undo with the keyboard change the graph and Save writes it', async ({ page, api, s }) => {
    const wf = await seed(api)
    await openEditor(page, s, p.slug, wf.id)
    await node(page, 'Greet').click()
    await page.keyboard.press('ControlOrMeta+c')
    await page.keyboard.press('ControlOrMeta+v')
    await expect(page.getByTestId('glass-node')).toHaveCount(4)
    await page.keyboard.press('ControlOrMeta+z')
    await expect(page.getByTestId('glass-node')).toHaveCount(3)
    await page.keyboard.press('ControlOrMeta+Shift+Z')
    await expect(page.getByTestId('glass-node')).toHaveCount(4)
    await node(page, 'Shape').click()
    await page.keyboard.press('ControlOrMeta+d')
    await expect(page.getByTestId('glass-node')).toHaveCount(5)
    await node(page, 'Shape').click()
    await page.keyboard.press('d')
    await expect(node(page, 'Shape')).toHaveClass(/disabled/)
    await page.keyboard.press('d')
    await expect(node(page, 'Shape')).not.toHaveClass(/disabled/)
    // delete the original Greet
    await node(page, 'Greet').click()
    await page.keyboard.press('Delete')
    await expect(page.getByTestId('glass-node')).toHaveCount(4)
    await page.keyboard.press('ControlOrMeta+z')
    await expect(page.getByTestId('glass-node')).toHaveCount(5)
    await page.keyboard.press('Delete')
    await expect(page.getByTestId('glass-node')).toHaveCount(4)
    await saveDraft(page)
    await expect.poll(async () => (await getWf(api, wf.id)).definition.nodes.length).toBe(4)
    const names = (await getWf(api, wf.id)).definition.nodes.map((n) => n.name)
    expect(names).toContain('Start')
    expect(names).toContain('Shape')
    expect(names.filter((n) => n !== 'Start' && n !== 'Shape').length).toBe(2)
    // N opens the catalog, T the trigger catalog, Escape closes, ? shows the shortcut help
    await page.locator('.react-flow__pane').click({ position: { x: 700, y: 700 } })
    await page.keyboard.press('n')
    await expect(page.getByTestId('ge-catalog')).toBeVisible()
    await page.keyboard.press('Escape')
    await expect(page.getByTestId('ge-catalog')).toBeHidden()
    await page.locator('.react-flow__pane').click({ position: { x: 700, y: 700 } })
    await page.keyboard.press('Shift+?')
    await expect(page.getByRole('dialog')).toContainText(/shortcut/i)
  })

  test('T34.14 ⌘K: Run workflow runs it, Add node opens the catalog, step actions are disabled until a step is selected, Tidy / Fit work', async ({ page, api, s }) => {
    const wf = await seed(api)
    await openEditor(page, s, p.slug, wf.id)
    await page.locator('.react-flow__pane').click({ position: { x: 700, y: 700 } })
    await page.keyboard.press('ControlOrMeta+k')
    await expect(page.getByTestId('command-palette')).toBeVisible()
    await expect(page.getByTestId('cmd-run-to')).toHaveAttribute('aria-disabled', 'true')
    await page.getByTestId('command-palette-input').fill('add')
    await page.getByTestId('cmd-add-node').click()
    await expect(page.getByTestId('ge-catalog')).toBeVisible()
    await page.getByTestId('ge-catalog-search').press('Escape')

    await page.locator('.react-flow__pane').click({ position: { x: 700, y: 700 } })
    await page.keyboard.press('ControlOrMeta+k')
    await page.getByTestId('command-palette-input').fill('run workflow')
    await page.getByTestId('cmd-run').click()
    await expect(page.getByTestId('ge-timeline-step')).toHaveCount(3, { timeout: 30_000 })
    expect((await nextExec(api, wf.id, 0)).status).toBe('success')

    await node(page, 'Shape').click()
    await page.keyboard.press('ControlOrMeta+k')
    await page.getByTestId('command-palette-input').fill('disable')
    await page.getByTestId('cmd-toggle-disable').click()
    await expect(node(page, 'Shape')).toHaveClass(/disabled/)
    await page.keyboard.press('ControlOrMeta+k')
    await page.getByTestId('command-palette-input').fill('version')
    await page.getByTestId('cmd-history').click()
    await expect(page.getByTestId('ge-versions-pop')).toBeVisible()
    await page.keyboard.press('Escape')
    await page.keyboard.press('ControlOrMeta+k')
    await page.getByTestId('command-palette-input').fill('fit')
    await page.getByTestId('cmd-zoom-fit').click()
    await expect(page.getByTestId('command-palette')).toBeHidden()
  })

  test('T34.15 minimap: it shows every step, follows panning, and clicking it moves the view', async ({ page, api, s }) => {
    const wf = await seed(api)
    await openEditor(page, s, p.slug, wf.id)
    const mm = page.locator('.react-flow__minimap')
    await expect(mm).toBeVisible()
    await expect(mm.locator('.react-flow__minimap-node')).toHaveCount(3)
    const mask = () => mm.locator('.react-flow__minimap-mask').getAttribute('d')
    const m0 = await mask()
    await page.getByTestId('ge-tools').getByRole('button', { name: 'Zoom in' }).click()
    await page.getByTestId('ge-tools').getByRole('button', { name: 'Zoom in' }).click()
    await expect.poll(mask).not.toBe(m0)
    const t0 = await translateOf(page)
    const box = (await mm.boundingBox())!
    await page.mouse.click(box.x + box.width * 0.9, box.y + box.height * 0.8)
    await expect.poll(async () => JSON.stringify(await translateOf(page)), { message: 'clicking the minimap did not move the view' }).not.toBe(JSON.stringify(t0))
  })

  // ── the Page | Editor | Definition toggle ────────────────────────────────────────────────
  test('T34.16 the toggle keeps your place (from the Page): the picked run and the selected step arrive in the Editor', async ({ page, api, s }) => {
    const wf = await seed(api)
    const run = await api.post<{ execution_id: string }>(`/workflows/${wf.id}/run`, { input_data: {} })
    await openView(page, s, p.slug, wf.id, 'page')
    await page.getByTestId('glass-run-picker').click()
    await page.locator(`[data-testid="glass-run-option"][data-run-id="${run.execution_id}"]`).click()
    await page.getByTestId('glass-outline-step').filter({ hasText: 'Shape' }).first().click()
    await expect(page.locator('.gp-toc-step.is-on')).toContainText('Shape')

    await switchTo(page, 'editor')
    await expect(page.getByTestId('ge-timeline-step'), 'the run picked on the Page is not on the canvas').toHaveCount(3)
    await expect(node(page, 'Shape'), 'the step selected on the Page is not selected in the Editor').toHaveClass(/sel/)
    await switchTo(page, 'definition')
    await expect(page.locator('[data-testid="glass-definition-node"].is-on')).toContainText('Shape')
  })

  test('T34.16b the toggle keeps your place (from the Editor): selection, a rename and the run follow to Page and Definition and back', async ({ page, api, s }) => {
    const wf = await seed(api)
    await openEditor(page, s, p.slug, wf.id)
    await runFromStrip(page, 3)
    await node(page, 'Shape').click()
    await page.getByTestId('inspector-name').click()
    await page.getByTestId('inspector-name-input').fill('Shape the person')
    await page.getByTestId('inspector-name-input').press('Enter')
    await expect(node(page, 'Shape')).toContainText('Shape the person')

    await switchTo(page, 'definition')
    await expect(page.getByTestId('glass-definition')).toContainText('Shape the person')
    await expect(page.locator('[data-testid="glass-definition-node"].is-on')).toContainText('Shape')
    await switchTo(page, 'page')
    await expect(page.getByTestId('glass-editor')).toContainText('Shape the person')
    await expect(page.locator('.gp-toc-step.is-on')).toContainText('Shape the person')
    await switchTo(page, 'editor')
    await expect(page.getByTestId('ge-inspector')).toHaveAttribute('data-node', 'Shape')
    await expect(node(page, 'Shape')).toContainText('Shape the person')
    await expect(page.getByTestId('ge-timeline-step')).toHaveCount(3)
  })

  test('T34.16c the run just made in the Editor is the run the Page shows (its results are not thrown away by the toggle)', async ({ page, api, s }) => {
    const wf = await seed(api)
    await openEditor(page, s, p.slug, wf.id)
    await runFromStrip(page, 3)
    await nextExec(api, wf.id, 0)
    await switchTo(page, 'page')
    await expect(page.getByTestId('glass-run-picker'), 'Page says "Live · no run" right after a run in the Editor').not.toContainText('Live')
  })

  test('T34.17 rapid view toggling ends on the last view with a working canvas and no errors', async ({ page, api, s }) => {
    const wf = await seed(api)
    await openEditor(page, s, p.slug, wf.id)
    const order = ['page', 'editor', 'definition', 'editor', 'page', 'definition', 'page', 'editor', 'definition', 'editor'] as const
    for (const v of order) await switchTo(page, v, { noWaitAfter: true })
    for (let i = 0; i < 6; i++) {
      await page.getByTestId('glass-view-switch').locator('[data-value="editor"]').focus()
      await page.keyboard.press('ArrowRight')
      await page.keyboard.press('ArrowLeft')
    }
    await expect(page.getByTestId('glass-editor')).toHaveAttribute('data-view', 'editor')
    await expect(page.getByTestId('glass-node')).toHaveCount(3)
    await node(page, 'Shape').click()
    await expect(page.getByTestId('ge-inspector')).toHaveAttribute('data-node', 'Shape')
    await runFromStrip(page, 3)
    expect((await nextExec(api, wf.id, 0)).status).toBe('success')
  })

  // ── edge cases ───────────────────────────────────────────────────────────────────────────
  test('T34.18 a brand-new empty workflow opens in all three views with the empty hint; the first step added is saved as the workflow', async ({ page, api, s }) => {
    await openView(page, s, p.slug, 'new', 'editor').catch(async () => {
      await expect(page.getByTestId('glass-editor')).toBeVisible()
    })
    await expect(page.getByTestId('glass-edit')).toBeVisible()
    await expect(page.getByTestId('glass-node')).toHaveCount(0)
    await expect(page.getByTestId('ge-timeline-empty')).toBeVisible()
    await switchTo(page, 'page')
    await expect(page.getByTestId('glass-header')).toBeVisible()
    await switchTo(page, 'definition')
    await expect(page.getByTestId('glass-definition')).toBeVisible()
    await switchTo(page, 'editor')
    await page.getByTestId('ge-tool-add').click()
    await page.getByTestId('ge-catalog-search').fill('Set')
    await page.locator('[data-testid="ge-catalog-item"][data-type="Set"]').click()
    await expect(page.getByTestId('glass-node')).toHaveCount(1)
    const before = (await api.get<Array<{ id: string }>>(`/projects/${p.id}/workflows`)).length
    await page.getByTestId('editor-save').click()
    await expect.poll(async () => (await api.get<Array<{ id: string }>>(`/projects/${p.id}/workflows`)).length, { timeout: 20_000 }).toBe(before + 1)
    await expect(page).toHaveURL(/\/workflows\/(?!new)[\w-]+/)
    await expect(page.getByTestId('editor-saved')).toHaveText('Saved')
  })

  test('T34.22 deleting every step and pressing Save either saves an empty workflow or says it could not; "Saved" must not be shown while the engine keeps the old steps', async ({ page, api, s }) => {
    const wf = await seedWorkflow(api, p, { name: `Empty ${rand(4)}`, nodes: [START], connections: [] })
    await openEditor(page, s, p.slug, wf.id)
    await node(page, 'Start').click()
    await page.getByTestId('inspector-delete').click()
    await expect(page.getByTestId('glass-node')).toHaveCount(0)
    const btn = page.getByTestId('editor-save')
    if (await btn.isVisible()) await btn.click()
    await page.waitForTimeout(1500)
    const apiNodes = (await getWf(api, wf.id)).definition.nodes.length
    const label = (await page.getByTestId('editor-saved').textContent())?.trim()
    expect(apiNodes === 0 || label !== 'Saved', `UI says "${label}" but the engine still has ${apiNodes} step(s)`).toBe(true)
  })

  test('T34.23 renaming a step marks the draft unsaved, and Save writes the new name', async ({ page, api, s }) => {
    const wf = await seed(api)
    await openEditor(page, s, p.slug, wf.id)
    await node(page, 'Greet').click()
    await page.getByTestId('inspector-name').click()
    await page.getByTestId('inspector-name-input').fill('Say hello')
    await page.getByTestId('inspector-name-input').press('Enter')
    await expect(node(page, 'Greet')).toContainText('Say hello')
    await expect(page.getByTestId('editor-saved'), 'rename did not mark the draft unsaved').toHaveText('Unsaved')
    await page.getByTestId('editor-save').click()
    await expect.poll(async () => (await getWf(api, wf.id)).definition.nodes.find((n) => n.name === 'Greet')?.label).toBe('Say hello')
  })

  test('T34.19 a 36-step workflow opens, shows every step on canvas and minimap, runs end to end, and the Page lists all steps', async ({ page, api, s }) => {
    const N = 35
    const nodes: Json[] = [START]
    const conns: Json[] = []
    for (let i = 1; i <= N; i++) {
      nodes.push(setN(`Step ${i}`, `f${i}`, `v${i}`, i * 280))
      conns.push(link(i === 1 ? 'Start' : `Step ${i - 1}`, `Step ${i}`))
    }
    const wf = await seedWorkflow(api, p, { name: `Long ${rand(4)}`, nodes, connections: conns })
    const t0 = Date.now()
    await openEditor(page, s, p.slug, wf.id)
    await page.getByTestId('ge-tool-fit').click()
    await expect(page.getByTestId('glass-node')).toHaveCount(N + 1)
    expect(Date.now() - t0, 'opening 36 steps took over 20 s').toBeLessThan(20_000)
    await expect(page.locator('.react-flow__minimap .react-flow__minimap-node')).toHaveCount(N + 1)
    await runFromStrip(page, N + 1)
    const run = await nextExec(api, wf.id, 0)
    expect(run.status).toBe('success')
    expect(Object.keys(run.node_data)).toHaveLength(N + 1)
    await expect(page.getByTestId('edge-count')).toHaveCount(N)
    await expect(page.locator('[data-testid="ge-timeline-step"][data-status="success"]')).toHaveCount(N + 1)
    await page.getByTestId('ge-tool-tidy').click()
    await saveDraft(page)
    await switchTo(page, 'page')
    await expect(page.getByTestId('glass-outline-step')).toHaveCount(N + 1)
    await switchTo(page, 'definition')
    await expect(page.getByTestId('glass-definition-node')).toHaveCount(N + 1)
  })

  test('T34.20 unsaved changes on navigation are either kept or the user is stopped; they are never silently lost', async ({ page, api, s }) => {
    const wf = await seed(api)
    await openEditor(page, s, p.slug, wf.id)
    await node(page, 'Greet').click()
    await node(page, 'Greet').getByTestId('glass-node-add').click()
    await page.getByTestId('ge-catalog-search').fill('Set')
    await page.locator('[data-testid="ge-catalog-item"][data-type="Set"]').click()
    await expect(page.getByTestId('glass-node')).toHaveCount(4)
    await expect(page.getByTestId('editor-saved')).toHaveText('Unsaved')
    page.on('dialog', (d) => void d.dismiss())
    await page.getByTestId('glass-back').click()
    await page.waitForTimeout(1500)
    const stayed = page.url().includes(`/workflows/${wf.id}`)
    const count = (await getWf(api, wf.id)).definition.nodes.length
    expect(stayed || count === 4, `left the editor with an unsaved new step and nothing was saved (API has ${count} steps)`).toBe(true)
  })

  test.describe('dark mode', () => {
    test.use({ colorScheme: 'dark' })
    const lum = (css: string) => {
      const m = /rgba?\(([\d.]+)[, ]+([\d.]+)[, ]+([\d.]+)/.exec(css)
      return m ? (0.2126 * Number(m[1]) + 0.7152 * Number(m[2]) + 0.0722 * Number(m[3])) / 255 : -1
    }
    test('T34.21 the editor, inspector, modal, drawer and popovers are dark and readable, and a run still works', async ({ page, api, s }) => {
      const wf = await seed(api)
      await openEditor(page, s, p.slug, wf.id)
      await expect(page.locator('html')).toHaveClass(/dark/)
      const colour = (id: string, prop: 'color' | 'backgroundColor') => page.getByTestId(id).first().evaluate((el, pr) => getComputedStyle(el)[pr as 'color'], prop)
      expect(lum(await page.locator('body').evaluate((el) => getComputedStyle(el).backgroundColor))).toBeLessThan(0.3)
      expect(lum(await page.getByTestId('glass-editor').evaluate((el) => getComputedStyle(el).color))).toBeGreaterThan(0.5)
      expect(lum(await colour('ge-versions', 'color'))).toBeGreaterThan(0.5)
      const nodeTitle = await node(page, 'Shape').locator('b, h5, .nm, .kt').first().evaluate((el) => getComputedStyle(el).color)
      expect(lum(nodeTitle), 'node text too dark on dark').toBeGreaterThan(0.35)
      await runFromStrip(page, 3)
      await node(page, 'Shape').click()
      expect(lum(await colour('ge-inspector', 'color'))).toBeGreaterThan(0.5)
      await page.getByTestId('ge-drawer-toggle').click()
      expect(lum(await colour('ge-drawer', 'color'))).toBeGreaterThan(0.5)
      await page.getByTestId('ge-publish').click()
      expect(lum(await colour('ge-publish-pop', 'color'))).toBeGreaterThan(0.5)
      await page.keyboard.press('Escape')
      await page.getByTestId('drawer-close').click()
      await node(page, 'Greet').click()
      await page.getByTestId('inspector-expand').click()
      const modal = page.getByTestId('ndv-modal')
      await expect(modal).toBeVisible()
      expect(lum(await modal.evaluate((el) => getComputedStyle(el).color))).toBeGreaterThan(0.5)
      // the modal surface must not be a light panel: sample its background through the stack
      const bg = await modal.evaluate((el) => {
        for (let e: Element | null = el; e; e = e.parentElement) {
          const c = getComputedStyle(e).backgroundColor
          if (c && !/rgba?\(.*,\s*0\)$/.test(c) && c !== 'transparent') return c
        }
        return 'rgb(255,255,255)'
      })
      expect(lum(bg), `modal background ${bg} is light in dark mode`).toBeLessThan(0.4)
      await page.keyboard.press('Escape')
      await switchTo(page, 'page')
      await expect(page.locator('html')).toHaveClass(/dark/)
      expect(lum(await page.locator('.gp-main').first().evaluate((el) => getComputedStyle(el).color))).toBeGreaterThan(0.5)
    })
  })
})

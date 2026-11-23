/**
 * Verification of four recent fixes, in the real UI against the real API (T4):
 *  (a) convert to sub-workflow -> "Delete it" replaces the "Created..." toast with "Putting the steps back..." and a result toast
 *  (b) the publish toast shows the per-workflow version (a second publish reads v2)
 *  (c) a child run shows a link to its parent run inside the viewing-run banner
 *  (d) the "Used by N" chip counts distinct caller workflows, not reference rows
 */
import { test, expect, type EngineApi } from '../fixtures/test'
import { seedProject, seedWorkflow, type P } from '../fixtures/seed'
import { nodeEl, openEditor } from '../fixtures/tests3a'
import { legacyEditor } from '../fixtures/legacy'

// these specs guard the legacy canvas editor (the glass editor is the default)
test.beforeEach(async ({ page }) => legacyEditor(page))

type Json = Record<string, unknown>
const link = (a: string, b: string) => ({ source_node: a, source_output: 'main', target_node: b, target_input: 'main' })
const setNode = (name: string, field: string, value: string, x: number) => ({
  name, type: 'Set', parameters: { mode: 'manual', fields: [{ name: field, value, type: 'string' }] }, position: { x, y: 0 },
})
const kycNodes = () => [
  { name: 'Inputs', type: 'ExecuteWorkflowTrigger', parameters: { inputSource: 'fields', fields: [{ name: 'customer_id', type: 'string', required: true, description: '' }] }, position: { x: 0, y: 0 } },
  setNode('KYC verdict', 'kyc_ref', 'KYC-{{ $json.customer_id }}', 260),
]
const seedKyc = (api: EngineApi, p: P, name = 'Check customer KYC') => seedWorkflow(api, p, { name, nodes: kycNodes(), connections: [link('Inputs', 'KYC verdict')] })
const callerBody = (name: string, childId: string): Json => ({
  name,
  nodes: [
    { name: 'Start', type: 'Start', parameters: {}, position: { x: 0, y: 0 } },
    { name: 'Call KYC', type: 'ExecuteWorkflow', parameters: { workflowId: childId, version: 'latest', inputMode: 'map', inputs: { customer_id: '{{ "C-1" }}' }, includeItem: false, mode: 'once', timeoutSeconds: 300, outputMode: 'items' }, position: { x: 260, y: 0 } },
  ],
  connections: [link('Start', 'Call KYC')],
})

test.describe('Recent fixes (T4)', () => {
  let p: P
  test.beforeEach(async ({ api, context }) => {
    p = await seedProject(api, 'T21')
    await context.addInitScript(() => {
      try { localStorage.setItem('studio.actorName', 'Asha') } catch { /* storage blocked */ }
    })
  })

  test('T21.1 (a) Delete it swaps the Created toast for "Putting the steps back..." and then a result toast', async ({ page, api, s }) => {
    const chain = await seedWorkflow(api, p, {
      name: 'Refund intake',
      nodes: [{ name: 'Start', type: 'Start', parameters: {}, position: { x: 0, y: 0 } }, setNode('A', 'a', '1', 240), setNode('B', 'b', '2', 480), setNode('C', 'c', '3', 720)],
      connections: [link('Start', 'A'), link('A', 'B'), link('B', 'C')],
    })
    await openEditor(page, s, p.slug, chain.id)
    // record every toast text that ever appears (the loading toast can be short-lived)
    await page.evaluate(() => {
      const seen: string[] = []
      ;(window as unknown as { __toasts: string[] }).__toasts = seen
      new MutationObserver(() => {
        document.querySelectorAll('[data-sonner-toast]').forEach((t) => {
          const txt = (t.textContent ?? '').trim()
          if (txt && seen[seen.length - 1] !== txt) seen.push(txt)
        })
      }).observe(document.body, { subtree: true, childList: true, characterData: true })
    })
    await nodeEl(page, 'A').click()
    await nodeEl(page, 'B').click({ modifiers: ['Shift'] })
    await nodeEl(page, 'A').click({ button: 'right' })
    await page.getByTestId('ctx-convert-subworkflow').click()
    await page.getByTestId('convert-subwf-name').fill('Steps A and B')
    await expect(page.getByTestId('convert-subwf-confirm')).toBeEnabled()
    await page.getByTestId('convert-subwf-confirm').click()
    const created = page.getByTestId('convert-subwf-toast')
    await expect(created).toContainText('Created "Steps A and B"')
    await page.getByTestId('convert-subwf-delete').click()
    // the Created toast is gone, not left on screen next to the progress toast
    await expect(created).toHaveCount(0)
    await expect(page.locator('[data-sonner-toast]').filter({ hasText: 'Put the steps back and deleted the sub-workflow' })).toBeVisible()
    await expect(page.locator('[data-sonner-toast]').filter({ hasText: 'Created "Steps A and B"' })).toHaveCount(0)
    const seen = await page.evaluate(() => (window as unknown as { __toasts: string[] }).__toasts)
    expect(seen.some((t) => t.includes('Putting the steps back')), `toasts seen: ${JSON.stringify(seen)}`).toBe(true)
    await expect(page.locator('.react-flow__node')).toHaveCount(4)
  })

  test('T21.2 (b) the publish toast shows the workflow version: first publish v1, second v2', async ({ page, api, s }) => {
    const wf = await seedKyc(api, p, 'Version me')
    await openEditor(page, s, p.slug, wf.id)
    await page.getByTestId('editor-publish').click()
    await expect(page.locator('[data-sonner-toast]').filter({ hasText: 'Published v1' })).toBeVisible()
    await page.getByTestId('editor-unpublish').click()
    await expect(page.getByTestId('editor-publish')).toBeVisible()
    await page.locator('[data-sonner-toast]').filter({ hasText: 'Published v1' }).waitFor({ state: 'detached', timeout: 20_000 }).catch(() => undefined)
    await page.getByTestId('editor-publish').click()
    await expect(page.locator('[data-sonner-toast]').filter({ hasText: 'Published v2' })).toBeVisible()
    const versions = await api.get<Array<{ version_number: number }>>(`/workflows/${wf.id}/versions`)
    expect(Math.max(...versions.map((v) => v.version_number))).toBe(2)
  })

  test('T21.3 (c) a child run shows the parent-run link in the viewing-run banner', async ({ page, api, s }) => {
    const child = await seedKyc(api, p)
    await api.post(`/workflows/${child.id}/publish`, {})
    const parent = await seedWorkflow(api, p, callerBody('Onboard customer', child.id))
    await api.post(`/workflows/${parent.id}/run`, { input_data: {} })
    let parentRun = ''
    await expect.poll(async () => {
      const l = await api.get<Array<{ id: string; status: string }>>(`/executions?workflow_id=${parent.id}`)
      parentRun = l.find((e) => e.status !== 'running')?.id ?? ''
      return parentRun
    }, { timeout: 30_000 }).not.toBe('')
    const run = await api.get<{ status: string; children: Array<{ execution_id: string }> }>(`/executions/${parentRun}`)
    expect(run.status).toBe('success')
    const childRun = run.children[0].execution_id
    await page.goto(`${s.studioUrl}/p/${p.slug}/workflows/${child.id}?execution=${childRun}`)
    const banner = page.getByTestId('viewing-run-banner')
    await expect(banner).toBeVisible()
    const pl = banner.getByTestId('exec-parent-link-banner')
    await expect(pl).toBeVisible()
    await expect(pl).toContainText('Onboard customer')
    await pl.click()
    await expect(page).toHaveURL(new RegExp(`/workflows/${parent.id}.*execution=${parentRun}`))
    // a top-level run has no parent link in its banner
    await expect(page.getByTestId('viewing-run-banner')).toBeVisible()
    await expect(page.getByTestId('exec-parent-link-banner')).toHaveCount(0)
  })

  test('T21.4 (d) "Used by N" counts distinct callers: one caller with draft + published refs is 1, two callers is 2', async ({ page, api, s }) => {
    const child = await seedKyc(api, p)
    await api.post(`/workflows/${child.id}/publish`, {})
    const a = await seedWorkflow(api, p, callerBody('Caller A', child.id))
    await api.post(`/workflows/${a.id}/publish`, {}) // draft + published version both reference the child
    const rows = await api.get<Array<{ parent_workflow_id: string }>>(`/workflows/${child.id}/used-by`)
    expect(rows.length).toBeGreaterThanOrEqual(2) // reference rows
    expect(new Set(rows.map((r) => r.parent_workflow_id)).size).toBe(1)

    await openEditor(page, s, p.slug, child.id)
    const chip = page.getByTestId('editor-used-by-chip')
    await expect(chip).toHaveText(/Used by 1$/)

    await seedWorkflow(api, p, callerBody('Caller B', child.id))
    await page.reload()
    await expect(page.getByTestId('workflow-navbar')).toBeVisible()
    await expect(page.getByTestId('editor-used-by-chip')).toHaveText(/Used by 2$/)
  })
})

/**
 * The Overview ("Pulse") and the project dock (T25): the numbers come from the API, a failed workflow is the loudest
 * thing on the page and in the dock, and the dock switches between workflows and apps like apps (keyboard too).
 */
import { test, expect, type EngineApi } from '../fixtures/test'
import { showDock } from '../fixtures/dock'
import { answerWorkflow, refundsAppFile, seedApp, seedProject, seedRefunds, seedWorkflow, type P } from '../fixtures/seed'

function failingWorkflow(name: string) {
  return {
    name,
    nodes: [
      { name: 'Start', type: 'Start', parameters: {}, position: { x: 0, y: 0 } },
      { name: 'Sync', type: 'Code', parameters: { code: "raise ValueError('upstream is down')\n" }, position: { x: 240, y: 0 } },
    ],
    connections: [{ source_node: 'Start', source_output: 'main', target_node: 'Sync', target_input: 'main' }],
  }
}

async function seedPulse(api: EngineApi, p: P) {
  const r = await seedRefunds(api, p)
  const ok = await seedWorkflow(api, p, answerWorkflow('Nightly answer'))
  const bad = await seedWorkflow(api, p, failingWorkflow('Broken sync'))
  const app = await seedApp(api, p, 'Refunds desk', [refundsAppFile()], [r.routeId('refundStats')])
  await api.post(`/workflows/${ok.id}/run`, { input_data: {} })
  await api.post(`/workflows/${ok.id}/run`, { input_data: {} })
  // the failure is the point: whatever the run endpoint answers, the stored run is what the page reads
  await api.post(`/workflows/${bad.id}/run`, { input_data: {} }).catch(() => undefined)
  await expect
    .poll(async () => (await api.get<Array<{ workflow_id: string; status: string }>>(`/projects/${p.id}/runs?workflow_id=${bad.id}`)).map((x) => x.status))
    .toEqual(['failed'])
  return { ok, bad, app }
}

test('T25.1 Overview metrics, attention and the 24h clock render from the API', async ({ page, api, s }) => {
  const p = await seedProject(api, 'T25a')
  const { ok, bad } = await seedPulse(api, p)
  const summary = await api.get<{ health: { runs_24h: number; failed_24h: number } }>(`/projects/${p.id}/summary`)
  expect(summary.health.runs_24h).toBeGreaterThanOrEqual(3)
  expect(summary.health.failed_24h).toBeGreaterThanOrEqual(1)

  await page.goto(`${s.studioUrl}/p/${p.slug}`)
  await expect(page.getByTestId('overview-panel')).toBeVisible()
  // the numbers are the summary's, and each opens its page
  await expect(page.getByTestId('metric-runs-value')).toHaveText(String(summary.health.runs_24h))
  await expect(page.getByTestId('metric-failed-value')).toHaveText(String(summary.health.failed_24h))
  await expect(page.getByTestId('metric-approvals-value')).toHaveText('0')
  // the failure leads "Needs attention" and its row on the clock
  const items = page.getByTestId('attention').locator('[data-testid^="attention-"]')
  await expect(items.first()).toHaveAttribute('data-testid', `attention-failed:workflow:${bad.id}`)
  await expect(page.getByTestId(`attention-failed:workflow:${bad.id}`)).toContainText('Broken sync failed')
  // the headline counts exactly what is listed (nothing here is merely running)
  const keys = await items.evaluateAll((els) => els.map((e) => e.getAttribute('data-testid')))
  // seedRefunds sets its secret in the default env only, so the live env is missing it: a real second item
  expect(keys).toEqual([`attention-failed:workflow:${bad.id}`, 'attention-secrets'])
  await expect(page.getByTestId('pulse-headline')).toContainText('2 things need you.')
  await expect(page.getByTestId('pill-secrets')).toContainText('1 missing')
  await expect(page.getByTestId(`track-wf-${bad.id}`)).toHaveAttribute('data-state', 'failed')
  await expect(page.getByTestId(`track-wf-${ok.id}`)).toHaveAttribute('data-state', 'active')
  await expect(page.getByTestId(`track-wf-${ok.id}-lane`)).toHaveAttribute('data-count', '2')
  await expect(page.getByTestId(`track-wf-${ok.id}-lane`).locator('[data-tone="ok"]')).toHaveCount(2)
  // loudest first: the failed workflow is the first row
  await expect(page.getByTestId('pulse-workflows').locator('[data-testid^="track-wf-"][data-state]').first()).toHaveAttribute('data-testid', `track-wf-${bad.id}`)
  // the system pills carry real counts
  await expect(page.getByTestId('pill-backend')).toContainText('3 routes')
  await expect(page.getByTestId('pill-tables')).toContainText('1')

  await page.getByTestId('metric-failed').click()
  await expect(page).toHaveURL(new RegExp(`/p/${p.slug}/runs\\?status=failed`))
})

test('T25.2 the dock: status badges, switching between workflows and apps, keyboard, Launchpad', async ({ page, api, s }) => {
  const p = await seedProject(api, 'T25b')
  const { ok, bad, app } = await seedPulse(api, p)
  await page.goto(`${s.studioUrl}/p/${p.slug}`)
  const dock = page.getByTestId('dock')
  await expect(dock).toBeVisible()
  await expect(dock).not.toHaveAttribute('data-compact', '')
  const tile = (id: string) => page.getByTestId(id).locator('xpath=..')

  // the failed workflow has a red dot, the healthy one does not
  await expect(tile(`dock-wf-${bad.id}`).getByTestId('dock-badge-failed')).toBeVisible()
  await expect(tile(`dock-wf-${ok.id}`).getByTestId('dock-badge-failed')).toHaveCount(0)
  await expect(page.getByTestId(`dock-app-${app.id}`)).toBeVisible()

  // switch to a workflow: the editor opens, the dock goes compact and rings the workflow
  await page.getByTestId(`dock-wf-${ok.id}`).click()
  await expect(page).toHaveURL(new RegExp(`/p/${p.slug}/workflows/${ok.id}`))
  await expect(dock).toHaveAttribute('data-compact', '')
  await expect(page.getByTestId(`dock-wf-${ok.id}`)).toHaveAttribute('aria-current', 'page')

  // and on to the app, then back home; the workflow keeps its tile
  await showDock(page)
  await page.getByTestId(`dock-app-${app.id}`).click()
  await expect(page).toHaveURL(new RegExp(`/p/${p.slug}/apps/${app.id}`))
  await expect(page.getByTestId(`dock-app-${app.id}`)).toHaveAttribute('aria-current', 'page')
  await expect(page.getByTestId(`dock-wf-${ok.id}`)).not.toHaveAttribute('aria-current', 'page')
  await showDock(page)
  await page.getByTestId('dock-overview').click()
  await expect(page.getByTestId('overview-panel')).toBeVisible()
  await expect(dock).not.toHaveAttribute('data-compact', '')
  await expect(page.getByTestId(`dock-wf-${ok.id}`)).toBeVisible()

  // one tab stop; arrows move along the dock, Enter opens
  await page.getByTestId('dock-overview').focus()
  await page.keyboard.press('ArrowRight')
  await expect(page.getByTestId('dock-backend')).toBeFocused()
  await page.keyboard.press('ArrowRight')
  await page.keyboard.press('ArrowRight')
  await expect(page.getByTestId('dock-runs')).toBeFocused()
  await page.keyboard.press('Enter')
  await expect(page).toHaveURL(new RegExp(`/p/${p.slug}/runs`))
  await expect(page.getByTestId('dock-runs')).toHaveAttribute('data-active', '')

  // Launchpad finds any workflow or app by name
  await page.getByTestId('dock-launchpad').click()
  await page.getByTestId('launchpad-search').fill('Broken')
  await page.getByTestId(`launch-wf-${bad.id}`).click()
  await expect(page).toHaveURL(new RegExp(`/p/${p.slug}/workflows/${bad.id}`))
})

test('T25.3 phone width: 16px gutters, the dock scrolls inside itself, no horizontal page scroll', async ({ page, api, s }) => {
  const p = await seedProject(api, 'T25c')
  await seedPulse(api, p)
  await page.setViewportSize({ width: 390, height: 844 })
  await page.goto(`${s.studioUrl}/p/${p.slug}`)
  await expect(page.getByTestId('overview-panel')).toBeVisible()
  await expect(page.getByTestId('metric-runs-value')).toBeVisible()
  const widths = await page.evaluate(() => [document.documentElement.scrollWidth, document.documentElement.clientWidth])
  expect(widths[0]).toBeLessThanOrEqual(widths[1])
  const hero = await page.getByTestId('pulse-hero').boundingBox()
  expect(hero!.x).toBeGreaterThanOrEqual(16)
  const dock = await page.getByTestId('dock').boundingBox()
  expect(dock!.x).toBeGreaterThanOrEqual(8)
  expect(dock!.x + dock!.width).toBeLessThanOrEqual(390 - 8)
})

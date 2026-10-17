/**
 * Workflows page and responsive layout (T40, T45).
 */
import { test, expect } from '../fixtures/test'
import { assertNoClipping } from '../fixtures/layout'
import { openModule } from '../fixtures/editor'
import { answerWorkflow, refundsAppFile, seedApp, seedProject, seedRefunds, seedWorkflow } from '../fixtures/seed'

test('T40 workflows page: open and run a canvas workflow', async ({ page, api, s }) => {
  const p = await seedProject(api, 'T40')
  const wf = await seedWorkflow(api, p, answerWorkflow('The answer'))
  await page.goto(`${s.studioUrl}/p/${p.slug}`)
  await page.getByTestId('tab-workflows').click()
  const row = page.getByTestId(`workflow-row-${wf.id}`)
  await expect(row).toContainText('The answer')
  await row.click()
  await expect(page).toHaveURL(`${s.studioUrl}/p/${p.slug}/workflows/${wf.id}`)
  await page.getByTestId('editor-run').click()
  await expect(page.getByTestId('execution-status')).toContainText(/success/i, { timeout: 30_000 })
  await expect(page.getByText('42').first()).toBeVisible()
  // the stream reports success before the run row is finalised: poll the stored run
  const list = () => api.get<Array<{ workflow_id: string; status: string; trigger: { type: string } }>>(`/projects/${p.id}/runs?workflow_id=${wf.id}`)
  await expect.poll(async () => (await list()).map((r) => r.status)).toEqual(['success'])
  const runs = await list()
  expect(runs.length).toBe(1)
  expect(runs[0].status).toBe('success')
  expect(runs[0].trigger.type).toBe('manual')
  // the Runs view shows it as a manual run
  await page.goto(`${s.studioUrl}/p/${p.slug}/runs`)
  await page.getByTestId('runs-env-all').click()
  await expect(page.locator('[data-testid="run-row"][data-trigger="manual"]')).toHaveCount(1)
})

for (const vp of [
  { width: 1280, height: 800 },
  { width: 1024, height: 768 },
]) {
  test(`T45 responsive at ${vp.width}x${vp.height}`, async ({ page, api, s }) => {
    const p = await seedProject(api, `T45w${vp.width}`)
    const r = await seedRefunds(api, p)
    const app = await seedApp(api, p, 'Refunds app', [refundsAppFile()], [r.routeId('refundStats')])
    await page.setViewportSize(vp)

    // Overview
    await page.goto(`${s.studioUrl}/p/${p.slug}`)
    await expect(page.getByTestId('overview-panel')).toBeVisible()
    if (vp.width < 1280) await expect(page.getByTestId('outline-rail')).toBeVisible()
    else await expect(page.getByTestId('outline')).toBeVisible()
    await assertNoClipping(page)

    // Backend with a response shown: Send is fully visible and clickable
    await openModule(page, s.studioUrl, p.slug, 'refunds', '?route=refundStats')
    const send = page.getByTestId('test-send')
    await expect(send).toBeInViewport({ ratio: 1 })
    await send.click()
    await expect(page.getByTestId('test-response').getByTestId('status-pill')).toHaveAttribute('data-status', '200')
    await expect(send).toBeInViewport({ ratio: 1 })
    await assertNoClipping(page)

    // Runs sheet
    const runs = await api.get<Array<{ id: string }>>(`/projects/${p.id}/runs`)
    await page.goto(`${s.studioUrl}/p/${p.slug}/runs/${runs[0].id}`)
    await expect(page.getByTestId('run-sheet')).toBeVisible()
    await assertNoClipping(page)

    // Builder
    await page.goto(`${s.studioUrl}/p/${p.slug}/apps/${app.id}`)
    await expect(page.getByTestId('builder-preview-panel')).toBeVisible()
    if (vp.width < 1280) await expect(page.getByTestId('builder-rail-backend')).toBeVisible()
    await assertNoClipping(page)
  })
}

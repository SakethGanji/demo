/** Reproducible final UI captures; uses the same isolated stack and strict policies. */
import fs from 'node:fs'
import path from 'node:path'
import { test, expect } from './fixtures/test'
import { answerWorkflow, refundsAppFile, seedApp, seedProject, seedPublished, seedRefunds, seedRelease, seedRows, seedWorkflow } from './fixtures/seed'
import { openModule } from './fixtures/editor'
import { assertNoClipping } from './fixtures/layout'

test('capture the completed UI in light and dark', async ({ page, api, s }) => {
  test.setTimeout(180_000)
  const out = path.resolve('..', '..', 'screenshots', 'ui-built')
  fs.mkdirSync(out, { recursive: true })
  const p = await seedProject(api, 'screenshots', 'Payments Ops')
  const r = await seedRefunds(api, p, { secretEnvs: ['dev', 'prod'] })
  await seedRows(api, p, 'refunds', [
    { reason: 'Damaged item', amount: 84.5 },
    { reason: 'Duplicate charge', amount: 29 },
    { reason: 'Order cancelled', amount: 120 },
  ])
  const app = await seedApp(api, p, 'Refunds dashboard', [refundsAppFile()], [r.routeId('refundStats'), r.routeId('createRefund')])
  await seedPublished(api, app.id, `payments-${p.slug}`)
  await seedRelease(api, p)
  const wf = await seedWorkflow(api, p, answerWorkflow('Process refund'))
  await api.post(`/projects/${p.id}/routes/${r.routeId('refundStats')}/test`, { environment: 'dev' })
  const runs = await api.get<Array<{ id: string }>>(`/projects/${p.id}/runs`)
  const capture = async (name: string, ready: string, theme: string) => {
    await expect(page.getByTestId(ready)).toBeVisible()
    if (name === 'builder') await expect(page.frameLocator('[data-testid="preview-iframe"]').getByTestId('refund-count')).toHaveText('3', { timeout: 30_000 })
    if (name === 'release') await expect(page.getByTestId('release-check-summary')).toBeVisible()
    if (name === 'projects') await expect(page.getByTestId(`project-card-${p.slug}`)).toBeVisible()
    if (name === 'canvas') await expect(page.locator('.react-flow__node')).toHaveCount(2)
    await page.evaluate(() => document.fonts.ready)
    await page.waitForTimeout(350)
    await assertNoClipping(page)
    await page.screenshot({ path: path.join(out, `${name}-${theme}.png`), animations: 'disabled', fullPage: true })
  }
  for (const theme of ['light', 'dark']) {
    await page.goto(`${s.studioUrl}/p/${p.slug}`)
    if (theme === 'dark') await page.getByTestId('theme-toggle').click()
    await capture('project', 'overview-panel', theme)
    await openModule(page, s.studioUrl, p.slug, 'refunds', '?route=refundStats')
    await page.getByTestId('test-send').click()
    await expect(page.getByTestId('test-response').getByTestId('status-pill')).toHaveAttribute('data-status', '200')
    await capture('backend', 'code-editor', theme)
    for (const [name, url, ready] of [
      ['data', `/p/${p.slug}/data/refunds`, 'data-panel'],
      ['secrets', `/p/${p.slug}/secrets`, 'secrets-panel'],
      ['runs', `/p/${p.slug}/runs`, 'runs-panel'],
      ['run-detail', `/p/${p.slug}/runs/${runs[0].id}`, 'run-sheet'],
      ['workflows', `/p/${p.slug}/workflows`, 'workflows-page'],
      ['canvas', `/p/${p.slug}/workflows/${wf.id}`, 'editor-run'],
      ['settings', `/p/${p.slug}?sheet=settings`, 'settings-sheet'],
      ['release', `/p/${p.slug}?sheet=release`, 'release-sheet'],
      ['create-ui', `/p/${p.slug}?dialog=create-ui`, 'create-ui-dialog'],
      ['builder', `/p/${p.slug}/apps/${app.id}`, 'builder-preview-panel'],
    ]) {
      await page.goto(`${s.studioUrl}${url}`)
      await capture(name, ready, theme)
    }
    await page.getByTestId('builder-publish').click()
    await capture('publish', 'publish-dialog', theme)
    await page.goto(`${s.studioUrl}/projects`)
    await capture('projects', 'projects-home', theme)
  }
})

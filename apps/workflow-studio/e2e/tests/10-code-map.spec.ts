/** Native Studio, real engine/isolated DB. No prototype server or LLM calls. */
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { test, expect } from '../fixtures/test'
import { seedModuleSource, seedProject } from '../fixtures/seed'
import { openModule, setSource, cursorLine } from '../fixtures/editor'
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../../../..')
const complex = fs.readFileSync(path.join(root, 'design-mockups/visual-backend/code-map/examples/risk_operations.py'), 'utf8')
const shots = path.join(root, 'screenshots/ui-built')
test('native map: complex module, hover, pin, filters and exact source', async ({ page, api, s }) => {
  const p = await seedProject(api, 'map-complex')
  const saved = await seedModuleSource(api, p, 'risk_operations', complex)
  const calls = (await api.llmCalls()).length
  await page.setViewportSize({ width: 1800, height: 1100 })
  await openModule(page, s.studioUrl, p.slug, 'risk_operations')
  await page.getByTestId('backend-view-visual').click()
  await expect(page.getByTestId('map-sync')).toContainText('Synced with current code')
  await expect(page.locator('[data-testid^="map-function-"]')).toHaveCount(22)
  await expect(page.locator('.react-flow__edge')).toHaveCount(28)
  await page.getByTestId('map-auto-layout').click()
  await page.getByTestId('map-function-assess_risk').hover()
  await expect(page.locator('.code-map-function.is-muted')).toHaveCount(16)
  await page.getByRole('button', { name: 'Pin Assess risk', exact: true }).click()
  await page.mouse.move(5, 5)
  await expect(page.getByTestId('map-highlight')).toContainText('Pinned: assess_risk')
  const pinButton = page.getByRole('button', { name: 'Pin Assess risk', exact: true })
  const camera = await page.locator('.react-flow__viewport').getAttribute('style')
  await pinButton.press('Enter')
  await expect(pinButton).toHaveAttribute('aria-pressed', 'false')
  await pinButton.press('Enter')
  await expect(pinButton).toHaveAttribute('aria-pressed', 'true')
  expect(await page.locator('.react-flow__viewport').getAttribute('style')).toBe(camera)
  await page.getByTestId('map-focus').click()
  await page.getByLabel('Map direction').selectOption('dependencies')
  await page.getByLabel('Map depth').selectOption('2')
  await expect(page.locator('[data-testid^="map-function-"]')).toHaveCount(5)
  await page
    .getByTestId('map-function-assess_risk')
    .getByRole('button', { name: /Assess risk.*assess_risk/ })
    .click()
  await expect(page.locator('.react-flow__edge-path')).toHaveCount(4)
  await expect
    .poll(() =>
      page.getByTestId('map-canvas').evaluate((canvas) => {
        const area = canvas.getBoundingClientRect()
        return [...canvas.querySelectorAll('.code-map-function')].every((node) => {
          const box = node.getBoundingClientRect()
          return box.left >= area.left && box.right <= area.right + 1 && box.top >= area.top && box.bottom <= area.bottom + 1
        })
      }),
    )
    .toBe(true)
  await expect(page.getByRole('button', { name: 'Fit graph', exact: true })).toBeVisible()
  fs.mkdirSync(shots, { recursive: true })
  await page.waitForTimeout(300)
  await page.screenshot({ path: path.join(shots, 'backend-code-map-light.png') })
  await page.evaluate(() => localStorage.setItem('workflow-studio-theme', 'dark'))
  await page.reload()
  await expect(page.locator('html')).toHaveClass(/dark/)
  await page.getByTestId('backend-view-visual').click()
  await expect(page.getByTestId('map-sync')).toContainText('Synced with current code')
  await page.getByRole('button', { name: 'Pin Assess risk', exact: true }).click()
  await expect(page.getByRole('button', { name: 'Pin Assess risk', exact: true })).toHaveAttribute('aria-pressed', 'true')
  await expect(page.getByTestId('map-highlight')).toContainText('Pinned: assess_risk')
  await page.getByTestId('map-focus').click()
  await page.getByLabel('Map direction').selectOption('dependencies')
  await page.getByLabel('Map depth').selectOption('2')
  await page
    .getByTestId('map-function-assess_risk')
    .getByRole('button', { name: /Assess risk.*assess_risk/ })
    .click()
  await expect(page.locator('[data-testid^="map-function-"]')).toHaveCount(5)
  await expect(page.getByTestId('map-inspector')).toContainText('Assess risk')
  await page.waitForTimeout(300)
  await page.screenshot({ path: path.join(shots, 'backend-code-map-dark.png') })
  await page.getByLabel('Map function type').selectOption('connector')
  await expect(page.locator('[data-testid^="map-function-"]')).toHaveCount(2)
  await expect(page.getByTestId('map-sync')).toContainText('20 hidden')
  await page.getByRole('button', { name: 'Reset filters', exact: true }).click()
  await page.getByTestId('map-focus').click()
  await page.getByLabel('Search map functions').fill('no_matches')
  await expect(page.getByText('No functions match these filters.')).toBeVisible()
  await page.getByLabel('Search map functions').fill('assess_risk')
  await expect(page.locator('[data-testid^="map-function-"]')).toHaveCount(1)
  await page.getByTestId('map-open-source').click()
  await expect(page.getByTestId('code-editor').locator('.cm-content')).toBeVisible()
  await expect.poll(() => cursorLine(page)).toBe(63)
  const after = await api.get<{ source: string; source_sha: string }>(`/projects/${p.id}/modules/${saved.module.id}`)
  expect(after.source).toBe(complex)
  expect(after.source_sha).toBe(saved.module.source_sha)
  expect((await api.llmCalls()).length).toBe(calls)
})
test('native map: drafts, invalid syntax, obsolete responses and module switching', async ({ page, api, s, net }) => {
  const p = await seedProject(api, 'map-draft'),
    original = 'def helper():\n    return 1\n\ndef entry():\n    return helper()\n'
  const saved = await seedModuleSource(api, p, 'first', original)
  await seedModuleSource(api, p, 'second', 'def second_only(): return 2\n')
  await openModule(page, s.studioUrl, p.slug, 'first')
  await page.getByTestId('backend-view-split').click()
  await expect(page.getByTestId('map-sync')).toContainText('Synced with current code')
  await expect(page.locator('.react-flow__edge')).toHaveCount(1)
  await setSource(page, original + '\ndef new_rule():\n    return helper()\n')
  await expect(page.getByTestId('map-function-new_rule')).toBeVisible()
  await expect(page.locator('.react-flow__edge')).toHaveCount(2)
  await net.expect([{ method: 'PUT', url: /modules\/[^/]+\?dry_run=true/, status: 422 }], async () => {
    await setSource(page, 'def broken(')
    await expect(page.getByTestId('map-sync')).toContainText('Invalid draft')
    await expect(page.getByTestId('map-sync')).toContainText('LAST VALID VIEW')
    await expect(page.getByTestId('map-open-source')).toBeDisabled()
  })
  let first = true
  await page.route('**/modules/code-map', async (route) => {
    const response = await route.fetch()
    if (first) {
      first = false
      await new Promise((resolve) => setTimeout(resolve, 1000))
    }
    await route.fulfill({ response })
  })
  await setSource(page, original + '\ndef obsolete_rule(): return 1\n')
  await page.waitForRequest('**/modules/code-map')
  await setSource(page, original + '\ndef latest_rule(): return 2\n')
  await expect(page.getByTestId('map-function-latest_rule')).toBeVisible()
  await page.waitForTimeout(1100)
  await expect(page.getByTestId('map-function-obsolete_rule')).toHaveCount(0)
  await page.unroute('**/modules/code-map')
  await page.goto(`${s.studioUrl}/p/${p.slug}/backend/second`)
  await page.getByTestId('backend-view-visual').click()
  await expect(page.getByTestId('map-function-second_only')).toBeVisible()
  await expect(page.getByTestId('map-function-helper')).toHaveCount(0)
  expect((await api.get<{ source: string }>(`/projects/${p.id}/modules/${saved.module.id}`)).source).toBe(original)
})

test('native map: recoverable analysis outage, empty modules, unnamed draft and narrow screen', async ({ page, api, s, net }) => {
  const p = await seedProject(api, 'map-errors')
  await seedModuleSource(api, p, 'empty', '# No functions yet\n')
  await openModule(page, s.studioUrl, p.slug, 'empty')
  await page.getByTestId('backend-view-visual').click()
  await expect(page.getByText('No top-level functions in this module.')).toBeVisible()
  await page.getByTestId('backend-view-code').click()
  await page.route('**/modules/code-map', (route) =>
    route.fulfill({
      status: 503,
      contentType: 'application/json',
      body: JSON.stringify({ detail: { message: 'Analysis temporarily unavailable' } }),
    }),
  )
  await net.expect([{ method: 'POST', url: /modules\/code-map$/, status: 503 }], async () => {
    await page.getByTestId('backend-view-split').click()
    await expect(page.getByTestId('map-sync')).toContainText('Analysis failed')
    await expect(page.getByTestId('code-editor').locator('.cm-content')).toBeVisible()
  })
  await page.unroute('**/modules/code-map')
  await page.getByRole('button', { name: 'Retry analysis' }).click()
  await expect(page.getByTestId('map-sync')).toContainText('Synced with current code')
  await page.goto(`${s.studioUrl}/p/${p.slug}/backend/~new`)
  await expect(page.getByTestId('code-editor').locator('.cm-content')).toBeVisible()
  await setSource(page, 'def unnamed_draft(): return 1\n')
  await page.getByTestId('backend-view-visual').click()
  await expect(page.getByTestId('map-function-unnamed_draft')).toBeVisible()
  await page.setViewportSize({ width: 1000, height: 900 })
  await page.getByTestId('map-auto-layout').click()
  await expect(page.getByTestId('map-function-unnamed_draft')).toBeVisible()
  const modules = await api.get<Array<{ name: string }>>(`/projects/${p.id}/modules`)
  expect(modules.map((m) => m.name)).toEqual(['empty'])
})

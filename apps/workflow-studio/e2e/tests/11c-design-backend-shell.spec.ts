/**
 * Design features, Backend and Shell (INTEGRATION-TEST-PLAN §3A): AI flow Keep → Undo, route picker search,
 * request-path art, ⌘K, ⌘P, ⇧R, sidebar [data-active], the Workflows list inside the workspace.
 * Each scenario runs in both themes.
 */
import { test, expect } from '../fixtures/test'
import { expectTheme, THEMES, type Theme } from '../fixtures/design'
import { editorText, openModule } from '../fixtures/editor'
import { answerWorkflow, seedModule, seedProject, seedRefunds, seedTable, seedWorkflow } from '../fixtures/seed'

for (const theme of THEMES) {
  test.describe(`backend + shell · ${theme}`, () => {
    test.use({ colorScheme: theme as Theme })

    test('AI flow: Keep folds the diff, Undo restores the previous source', async ({ page, api, s }) => {
      const p = await seedProject(api, 'B1')
      await seedRefunds(api, p)
      await openModule(page, s.studioUrl, p.slug, 'refunds')
      await expectTheme(page, theme)
      const before = await editorText(page)
      expect(before).not.toContain('REFUNDS_WEBHOOK_KEY')
      const savedSha = (await api.get<Array<{ source_sha: string }>>(`/projects/${p.id}/modules`))[0].source_sha

      await expect(page.getByTestId('composer')).toBeVisible()
      const input = page.getByTestId('composer-input')
      if (!(await input.isVisible())) await page.getByTestId('composer-submit').click()
      await input.fill('Notify the webhook on each refund __webhook__')
      await input.press('Enter')
      await expect(page.getByTestId('composer-result')).toBeVisible({ timeout: 60_000 })

      // the draft is in the editor and the flow shows the diff with Keep
      await expect.poll(() => editorText(page)).toContain('REFUNDS_WEBHOOK_KEY')
      const diff = page.getByTestId('composer-diff-view')
      await expect(diff).toBeVisible()
      await expect(diff.locator('[data-diff="add"]').first()).toBeVisible()
      await expect(diff.locator('[data-diff="del"]').first()).toBeVisible()

      // Keep → the diff folds into the "applied" line; the editor keeps the new code
      await page.getByTestId('composer-diff-view-apply').click()
      const applied = page.getByTestId('composer-applied')
      await expect(applied).toBeVisible()
      await expect(page.getByTestId('composer-diff-view')).toHaveCount(0)
      await expect(page.getByTestId('composer-flow-head')).toContainText('Kept')
      expect(await editorText(page)).toContain('REFUNDS_WEBHOOK_KEY')

      // Undo → the previous source is back and the draft is gone; nothing was saved either way
      await page.getByTestId('composer-applied-undo').click()
      await expect.poll(() => editorText(page)).toBe(before)
      await expect(page.getByTestId('composer-result')).toHaveCount(0)
      await expect(page.getByTestId('composer-applied')).toHaveCount(0)
      expect((await api.get<Array<{ source_sha: string }>>(`/projects/${p.id}/modules`))[0].source_sha).toBe(savedSha)
    })

    test('route picker: search filters, test-route-option-* stays, selection changes the route', async ({ page, api, s }) => {
      const p = await seedProject(api, 'B2')
      await seedRefunds(api, p)
      await openModule(page, s.studioUrl, p.slug, 'refunds', '?route=listRefunds')
      await expectTheme(page, theme)
      const picker = page.getByTestId('test-route-picker')
      await expect(picker).toContainText('/refunds')
      await picker.click()
      const search = page.getByPlaceholder('Find a route…')
      await expect(search).toBeVisible()
      for (const n of ['createRefund', 'listRefunds', 'refundStats']) await expect(page.getByTestId(`test-route-option-${n}`)).toBeVisible()
      await search.fill('stats')
      await expect(page.getByTestId('test-route-option-refundStats')).toBeVisible()
      await expect(page.getByTestId('test-route-option-createRefund')).toHaveCount(0)
      await expect(page.getByTestId('test-route-option-listRefunds')).toHaveCount(0)
      // searching by method works too
      await search.fill('post')
      await expect(page.getByTestId('test-route-option-createRefund')).toBeVisible()
      await expect(page.getByTestId('test-route-option-refundStats')).toHaveCount(0)
      await search.fill('zzz-nothing')
      await expect(page.getByText('No route matches')).toBeVisible()
      await search.fill('stats')
      await page.getByTestId('test-route-option-refundStats').click()
      await expect(page.getByTestId('test-route-picker')).toContainText('/refunds/stats')
      await expect(page.getByTestId('test-send')).toBeVisible()
    })

    test('request-path art shows the node calls after Send', async ({ page, api, s }) => {
      const p = await seedProject(api, 'B3')
      await seedRefunds(api, p)
      await openModule(page, s.studioUrl, p.slug, 'refunds', '?route=refundStats')
      await expectTheme(page, theme)
      const art = page.getByTestId('request-path-art')
      await expect(art.getByTestId('request-path')).toBeVisible()
      await expect(art.getByTestId('request-path-call')).toHaveCount(0)
      await expect(art.getByTestId('request-path-ms')).toHaveText('—')

      await page.getByTestId('test-send').click()
      await expect(page.getByTestId('test-response').getByTestId('status-pill')).toHaveAttribute('data-status', '200')
      // refundStats makes an HTTP call and reads the table: both are drawn from the real trace
      await expect(art.getByTestId('request-path-call')).toHaveCount(2)
      for (const st of await art.getByTestId('request-path-call').evaluateAll((els) => els.map((e) => e.getAttribute('data-status')))) expect(st).toBe('ok')
      await expect(art.getByTestId('request-path-ms')).toHaveText(/^\d+ ms$/)
      await expect(art.getByTestId('request-path')).toHaveAttribute('aria-label', /made 2 node calls/)
    })

    test('⌘K palette: footer hints, search and navigation', async ({ page, api, s }) => {
      const p = await seedProject(api, 'S1')
      await seedTable(api, p, 'refunds')
      await seedModule(api, p, 'hello', 'hello.py')
      await page.goto(`${s.studioUrl}/p/${p.slug}`)
      await expectTheme(page, theme)
      await expect(page.getByTestId('overview-panel')).toBeVisible()
      await page.keyboard.press('ControlOrMeta+k')
      const palette = page.getByTestId('command-palette')
      await expect(palette).toBeVisible()
      // footer hints
      for (const hint of ['move', 'open', 'close', 'backend', 'data', 'runs']) await expect(palette).toContainText(hint)
      await expect(palette.locator('kbd, [data-slot="kbd"]').first()).toBeVisible()
      // groups for what the project has
      await expect(palette).toContainText('Modules')
      await expect(palette).toContainText('Tables')
      await expect(palette).toContainText('Actions')
      await palette.getByRole('combobox').fill('table refunds')
      await expect(palette.getByRole('option')).toHaveCount(1)
      await page.keyboard.press('Enter')
      await expect(page).toHaveURL(new RegExp(`/p/${p.slug}/data/refunds`))
      await expect(palette).toHaveCount(0)
      // reopen, a module, then Esc closes
      await page.keyboard.press('ControlOrMeta+k')
      await expect(palette).toBeVisible()
      await expect(palette.getByRole('combobox')).toHaveValue('')
      await palette.getByRole('combobox').fill('module hello')
      await expect(palette.getByRole('option')).toHaveCount(1)
      await page.keyboard.press('Enter')
      await expect(page).toHaveURL(new RegExp(`/p/${p.slug}/backend/hello`))
      await page.keyboard.press('ControlOrMeta+k')
      await expect(palette).toBeVisible()
      await page.keyboard.press('Escape')
      await expect(palette).toHaveCount(0)
    })

    test('⌘P switches project', async ({ page, api, s }) => {
      const a = await seedProject(api, 'S2a')
      const b = await seedProject(api, 'S2b', `Switch Target ${Math.random().toString(36).slice(2, 8)}`)
      await page.goto(`${s.studioUrl}/p/${a.slug}`)
      await expectTheme(page, theme)
      await expect(page.getByTestId('project-switcher')).toBeVisible()
      await page.keyboard.press('ControlOrMeta+p')
      const search = page.getByPlaceholder('Find a project…')
      await expect(search).toBeVisible()
      await search.fill(b.name)
      await expect(page.getByTestId('project-switcher-menu').getByRole('option')).toHaveCount(1)
      await page.keyboard.press('Enter')
      await expect(page).toHaveURL(new RegExp(`/p/${b.slug}$`))
      await expect(page.getByTestId('project-switcher')).toContainText(b.name)
    })

    test('⇧R opens the release sheet, but not while typing', async ({ page, api, s }) => {
      const p = await seedProject(api, 'S3')
      await seedModule(api, p, 'hello', 'hello.py')
      await page.goto(`${s.studioUrl}/p/${p.slug}/runs`)
      await expectTheme(page, theme)
      await expect(page.getByTestId('runs-view')).toBeVisible()
      // typing a capital R in a field is just text
      await page.getByTestId('runs-path-filter').click()
      await page.keyboard.press('Shift+R')
      await expect(page.getByTestId('runs-path-filter')).toHaveValue('R')
      await expect(page.getByTestId('release-sheet')).toHaveCount(0)
      expect(page.url()).not.toContain('sheet=release')
      // outside a field it opens the sheet
      await page.getByTestId('runs-path-filter').fill('')
      await page.locator('body').click({ position: { x: 5, y: 400 } })
      await page.keyboard.press('Shift+R')
      await expect(page.getByTestId('release-sheet')).toBeVisible()
      expect(page.url()).toContain('sheet=release')
    })

    test('sidebar marks the active item with [data-active] as navigation changes', async ({ page, api, s }) => {
      const p = await seedProject(api, 'S4')
      await seedRefunds(api, p)
      const active = (id: string) => page.getByTestId(id)
      await page.goto(`${s.studioUrl}/p/${p.slug}`)
      await expectTheme(page, theme)
      await expect(active('outline-overview')).toHaveAttribute('data-active', '')
      await expect(active('outline-runs')).not.toHaveAttribute('data-active', '')

      await active('outline-runs').click()
      await expect(page).toHaveURL(new RegExp(`/p/${p.slug}/runs`))
      await expect(active('outline-runs')).toHaveAttribute('data-active', '')
      await expect(active('outline-overview')).not.toHaveAttribute('data-active', '')

      await active('outline-secrets').click()
      await expect(active('outline-secrets')).toHaveAttribute('data-active', '')
      await expect(active('outline-runs')).not.toHaveAttribute('data-active', '')

      await active('outline-table-refunds').click()
      await expect(page).toHaveURL(new RegExp(`/data/refunds`))
      await expect(active('outline-table-refunds')).toHaveAttribute('data-active', '')
      await expect(active('outline-secrets')).not.toHaveAttribute('data-active', '')

      // a module marks itself; its routes appear under it and mark themselves instead
      await active('outline-module-refunds').click()
      await expect(active('outline-module-refunds')).toHaveAttribute('data-active', '')
      await expect(active('outline-table-refunds')).not.toHaveAttribute('data-active', '')
      await active('outline-route-refundStats').click()
      await expect(page).toHaveURL(/route=refundStats/)
      await expect(active('outline-module-refunds')).not.toHaveAttribute('data-active', '')
      await expect(active('outline-route-refundStats')).toHaveAttribute('data-active', '')
      // exactly one active leaf in the nav at a time
      await expect(page.getByTestId('outline').locator('[data-active]')).toHaveCount(1)

      // browser back follows too
      await page.goBack()
      await expect(active('outline-module-refunds')).toHaveAttribute('data-active', '')
    })

    test('Workflows list renders inside the workspace', async ({ page, api, s }) => {
      const p = await seedProject(api, 'S5')
      await seedModule(api, p, 'hello', 'hello.py')
      const wf = await seedWorkflow(api, p, answerWorkflow('Nightly answer'))
      await page.goto(`${s.studioUrl}/p/${p.slug}`)
      await expectTheme(page, theme)
      await expect(page.getByTestId('tab-project')).toHaveAttribute('data-on', '')
      await page.getByTestId('tab-workflows').click()
      await expect(page).toHaveURL(new RegExp(`/p/${p.slug}/workflows$`))
      await expect(page.getByTestId('workflows-page')).toBeVisible()
      await expect(page.getByTestId('tab-workflows')).toHaveAttribute('data-on', '')
      await expect(page.getByTestId('tab-project')).not.toHaveAttribute('data-on', '')
      // it is a page of the workspace: sidebar and top bar are still there
      await expect(page.getByTestId('workspace')).toBeVisible()
      await expect(page.getByTestId('outline')).toBeVisible()
      await expect(page.getByTestId('topbar')).toBeVisible()
      await expect(page.getByTestId(`workflow-open-${wf.id}`)).toHaveText('Nightly answer')
      // g then w from another page lands here as well
      await page.getByTestId('outline-runs').click()
      await expect(page.getByTestId('runs-view')).toBeVisible()
      await page.locator('body').click({ position: { x: 5, y: 400 } })
      await page.keyboard.press('g')
      await page.keyboard.press('w')
      await expect(page).toHaveURL(new RegExp(`/p/${p.slug}/workflows$`))
      await expect(page.getByTestId('tab-workflows')).toHaveAttribute('data-on', '')
    })
  })
}

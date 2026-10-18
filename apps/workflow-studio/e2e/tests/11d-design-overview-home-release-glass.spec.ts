/**
 * Design features, Overview, Projects home, Release sheet and liquid glass (INTEGRATION-TEST-PLAN §3A).
 * Each scenario runs in both themes. The Overview 30-day grid is asserted through the cells' data attributes
 * (data-date / data-count / data-failed), not through where the numbers come from.
 */
import { test, expect, rand, type EngineApi } from '../fixtures/test'
import { backdateRuns, expectTheme, THEMES, type Theme } from '../fixtures/design'
import { answerWorkflow, refundsAppFile, seedApp, seedModule, seedProject, seedRefunds, seedWorkflow } from '../fixtures/seed'

async function fireRoute(api: EngineApi, pid: string, routeId: string) {
  await api.post(`/projects/${pid}/routes/${routeId}/test`, { environment: 'dev', params: {}, query: {}, headers: {}, body: null })
  const [newest] = await api.get<Array<{ id: string }>>(`/projects/${pid}/runs?limit=1`)
  return newest.id
}

for (const theme of THEMES) {
  test.describe(`overview · ${theme}`, () => {
    test.use({ colorScheme: theme as Theme, permissions: ['clipboard-read', 'clipboard-write'] })

    test('snippet: URL, curl and Python forms copy to the clipboard', async ({ page, api, s }) => {
      const p = await seedProject(api, 'O1')
      await seedRefunds(api, p)
      await page.goto(`${s.studioUrl}/p/${p.slug}`)
      await expectTheme(page, theme)
      const snip = page.getByTestId('base-url')
      await expect(snip).toBeVisible()
      const clip = () => page.evaluate(() => navigator.clipboard.readText())

      const url = (await snip.getByTestId('base-url-value').innerText()).trim()
      expect(url).toMatch(new RegExp(`/api/p/${p.slug}`))
      await snip.getByTestId('base-url-copy').click()
      await expect(page.getByText('Copied URL')).toBeVisible()
      await expect.poll(clip).toBe(url)

      await snip.getByTestId('base-url-curl').click()
      await expect(snip.getByTestId('base-url-value')).toContainText('curl ')
      const curl = (await snip.getByTestId('base-url-value').innerText()).trim()
      await snip.getByTestId('base-url-copy').click()
      await expect.poll(clip).toBe(curl)
      expect(curl).toContain(`/api/p/${p.slug}/`)

      await snip.getByTestId('base-url-python').click()
      await expect(snip.getByTestId('base-url-value')).toContainText('import requests')
      await snip.getByTestId('base-url-copy').click()
      await expect.poll(clip).toContain('requests.get(')
      await expect.poll(clip).toContain(`/api/p/${p.slug}/`)
    })

    test('stat tiles navigate to their pages', async ({ page, api, s }) => {
      const p = await seedProject(api, 'O2')
      const r = await seedRefunds(api, p)
      const app = await seedApp(api, p, 'Refunds app', [refundsAppFile()], [r.routeId('refundStats')])
      const back = async () => {
        await page.goto(`${s.studioUrl}/p/${p.slug}`)
        await expect(page.getByTestId('stat-strip')).toBeVisible()
      }
      await back()
      await expectTheme(page, theme)
      await expect(page.getByTestId('stat-routes-value')).toHaveText('3')
      await expect(page.getByTestId('stat-tables-value')).toHaveText('1')
      await expect(page.getByTestId('stat-apps-value')).toHaveText('1')
      await page.getByTestId('stat-routes').click()
      await expect(page).toHaveURL(new RegExp(`/p/${p.slug}/backend/refunds`))
      await back()
      await page.getByTestId('stat-tables').click()
      await expect(page).toHaveURL(new RegExp(`/p/${p.slug}/data`))
      await back()
      await page.getByTestId('stat-failed').click()
      await expect(page).toHaveURL(new RegExp(`/p/${p.slug}/runs`))
      await expect(page.getByTestId('runs-view')).toBeVisible()
      await back()
      await page.getByTestId('stat-apps').click()
      await expect(page).toHaveURL(new RegExp(`/p/${p.slug}/apps/${app.id}`))
      // the activity tile links to Runs too
      await back()
      await page.getByTestId('activity-view-all').click()
      await expect(page).toHaveURL(new RegExp(`/p/${p.slug}/runs`))
    })

    test('the Interface block opens the New interface dialog while there is none', async ({ page, api, s }) => {
      const p = await seedProject(api, 'O2b')
      await seedRefunds(api, p)
      await page.goto(`${s.studioUrl}/p/${p.slug}`)
      await expectTheme(page, theme)
      await expect(page.getByTestId('stat-apps-value')).toHaveText('0')
      await page.getByTestId('stat-apps').click()
      await expect(page.getByTestId('create-ui-dialog')).toBeVisible()
    })

    test('30-day activity grid: cells carry data-date, data-count and data-failed', async ({ page, api, s }) => {
      const p = await seedProject(api, 'O3')
      await seedModule(api, p, 'hello', 'hello.py')
      const routes = await api.get<Array<{ id: string; name: string }>>(`/projects/${p.id}/routes`)
      const rid = routes[0].id
      // 3 runs today and 2 about two days ago
      // (the engine prunes the oldest runs on every new run, so backdate only after all are created)
      const old: string[] = []
      for (let i = 0; i < 2; i++) old.push(await fireRoute(api, p.id, rid))
      for (let i = 0; i < 3; i++) await fireRoute(api, p.id, rid)
      backdateRuns(s, old, 48)

      await page.goto(`${s.studioUrl}/p/${p.slug}`)
      await expectTheme(page, theme)
      const grid = page.getByTestId('activity-30d')
      await expect(grid).toBeVisible()
      const cells = grid.locator('[role="gridcell"]')
      await expect(cells.first()).toBeVisible()
      const read = () =>
        cells.evaluateAll((els) => els.map((e) => ({ date: e.getAttribute('data-date')!, count: Number(e.getAttribute('data-count')), failed: Number(e.getAttribute('data-failed')) })))
      // wait for the aggregate to land (the grid renders empty days too, so poll the total)
      await expect.poll(async () => (await read()).reduce((n, c) => n + c.count, 0)).toBe(5)
      const days = await read()
      expect(days.length).toBeGreaterThanOrEqual(28)
      expect(days.length).toBeLessThanOrEqual(31)
      for (const d of days) {
        expect(d.date).toMatch(/^\d{4}-\d{2}-\d{2}$/)
        expect(Number.isInteger(d.count) && d.count >= 0).toBe(true)
        expect(Number.isInteger(d.failed) && d.failed >= 0 && d.failed <= d.count).toBe(true)
      }
      // consecutive ascending days, ending today (UTC or local date)
      const dates = days.map((d) => d.date)
      expect([...dates].sort()).toEqual(dates)
      expect(new Set(dates).size).toBe(dates.length)
      const today = [new Date().toISOString().slice(0, 10), new Date().toLocaleDateString('sv')]
      expect(today).toContain(dates[dates.length - 1])
      expect(days[days.length - 1].count).toBe(3)
      expect(days.reduce((n, c) => n + c.failed, 0)).toBe(0)
      expect(days.filter((d) => d.count > 0)).toHaveLength(2)
      // the 24h number counts today's runs
      await expect(page.getByTestId('stat-runs-value')).toHaveText(/^\d+$/)
    })
  })
}

for (const theme of THEMES) {
  test.describe(`projects home · ${theme}`, () => {
    test.use({ colorScheme: theme as Theme })

    test('a template creates the project and its table; the saved routes answer 200', async ({ page, open, api }) => {
      await open('/projects')
      await expectTheme(page, theme)
      await expect(page.getByTestId('home-templates')).toBeVisible()
      await page.getByTestId('template-crud').click()
      await expect(page).toHaveURL(/\/p\/[^/]+\/backend\/items$/, { timeout: 30_000 })
      const slug = new URL(page.url()).pathname.split('/')[2]
      const project = (await api.get<Array<{ id: string; slug: string; name: string }>>('/projects')).find((x) => x.slug === slug)!
      expect(project.name).toMatch(/^CRUD API/)
      // the table came with the project (the draft analyser could not infer its columns)
      const tables = await api.get<Array<{ name: string; columns: Array<{ name: string; type: string; required: boolean }> }>>(`/projects/${project.id}/tables`)
      expect(tables.map((t) => t.name)).toEqual(['items'])
      expect(tables[0].columns).toEqual([expect.objectContaining({ name: 'name', type: 'string', required: true })])
      // the module is an unsaved draft until saved
      await expect(page.getByTestId('code-editor').locator('.cm-content')).toBeVisible()
      expect(await api.get<unknown[]>(`/projects/${project.id}/modules`)).toEqual([])
      await page.getByTestId('code-editor').locator('.cm-content').click()
      await page.keyboard.press('ControlOrMeta+s')
      await expect(page.getByText(/Saved items\.py/)).toBeVisible()
      // CRUD routes answer
      const base = `/api/p/${slug}/items`
      expect((await api.raw('GET', base)).status).toBe(200)
      const created = await api.raw<{ id: number }>('POST', base, { name: 'first' })
      expect(created.status).toBe(200)
      expect((await api.raw('PUT', `${base}/${created.body.id}`, { name: 'renamed' })).status).toBe(200)
      const list = await api.raw<Array<{ data?: { name: string }; name?: string }>>('GET', base)
      expect(JSON.stringify(list.body)).toContain('renamed')
      expect((await api.raw('DELETE', `${base}/${created.body.id}`)).status).toBe(200)
    })

    test('"Describe it instead" opens the backend editor with the prompt and generates', async ({ page, open, api }) => {
      const urls: string[] = []
      page.on('framenavigated', (f) => f === page.mainFrame() && urls.push(f.url()))
      await open('/projects')
      await expectTheme(page, theme)
      const prompt = 'Store refunds in a table. POST /refunds creates one, GET /refunds lists them, GET /refunds/stats totals them by reason __refunds__'
      await api.clearLlmCalls()
      await page.getByTestId('home-describe-input').fill(prompt)
      await page.getByTestId('home-describe-generate').click()
      await expect(page.getByTestId('composer-result')).toBeVisible({ timeout: 60_000 })
      // it passed through backend/~new?generate=1&prompt=… and ended on the generated module
      const hop = urls.find((u) => u.includes('/backend/~new'))
      expect(hop, `navigations: ${urls.join(' → ')}`).toBeTruthy()
      expect(new URL(hop!).searchParams.get('generate')).toBe('1')
      expect(new URL(hop!).searchParams.get('prompt')).toBe(prompt)
      await expect(page).toHaveURL(/\/p\/[^/]+\/backend\/refunds$/)
      const calls = await api.llmCalls()
      expect(calls.map((c) => c.rule_id)).toEqual(['gen-refunds'])
      expect(JSON.stringify(calls[0].messages)).toContain('Store refunds in a table')
      // the project was named from the prompt
      const slug = new URL(page.url()).pathname.split('/')[2]
      const project = (await api.get<Array<{ slug: string; name: string }>>('/projects')).find((x) => x.slug === slug)!
      expect(project.name).toMatch(/^Store Refunds In A/)
    })

    test('the sticky top bar frosts only once content scrolls under it', async ({ page, open }) => {
      await page.setViewportSize({ width: 1440, height: 560 })
      await open('/projects')
      await expectTheme(page, theme)
      const home = page.getByTestId('projects-home')
      const bar = home.locator('header').first()
      await expect(bar).toBeVisible()
      await expect(bar).not.toHaveAttribute('data-scrolled', /.*/)
      const room = await home.evaluate((el) => el.scrollHeight - el.clientHeight)
      expect(room, 'the page must be tall enough to scroll').toBeGreaterThan(100)
      await home.evaluate((el) => (el.scrollTop = 300))
      await expect(bar).toHaveAttribute('data-scrolled', '')
      // frosted: a backdrop blur and a fill appear
      await expect.poll(() => bar.evaluate((el) => getComputedStyle(el).backdropFilter)).toContain('blur')
      await home.evaluate((el) => (el.scrollTop = 0))
      await expect(bar).not.toHaveAttribute('data-scrolled', /.*/)
      await expect.poll(() => bar.evaluate((el) => getComputedStyle(el).backdropFilter)).toBe('none')
    })

    test('the sky exists only on /projects and the landing page, never on working pages', async ({ page, open, api }) => {
      const p = await seedProject(api, 'H4')
      await seedModule(api, p, 'hello', 'hello.py')
      await seedWorkflow(api, p, answerWorkflow('Sky check'))
      const sky = page.getByTestId('home-sky')
      await open('/projects')
      await expectTheme(page, theme)
      await expect(page.getByTestId('projects-home')).toBeVisible()
      await expect(sky).toHaveCount(1)
      await open('/')
      await expect(page.getByTestId('landing-page')).toBeVisible()
      await expect(sky).toHaveCount(1)
      for (const path of ['', '/data', '/runs', '/secrets', '/workflows', '/backend/hello']) {
        await open(`/p/${p.slug}${path}`)
        await expect(page.getByTestId('workspace')).toBeVisible()
        await expect(page.getByTestId('topbar')).toBeVisible()
        await expect(sky, `home-sky on /p/${p.slug}${path}`).toHaveCount(0)
      }
    })
  })
}

for (const theme of THEMES) {
  test.describe(`release sheet · ${theme}`, () => {
    test.use({ colorScheme: theme as Theme })

    test('stepper states follow the checks; the inline secret fix completes Secrets; the beam runs only while promoting', async ({ page, api, s }) => {
      const p = await seedProject(api, 'RS1')
      await seedRefunds(api, p, { secretEnvs: ['dev'] }) // PAYMENTS_TOKEN missing in prod
      await page.goto(`${s.studioUrl}/p/${p.slug}?sheet=release`)
      await expectTheme(page, theme)
      const state = (id: string) => page.getByTestId(`release-flow-${id}`)
      const states = async () => {
        const out: Record<string, string | null> = {}
        for (const k of ['code', 'secrets', 'routes', 'promote']) out[k] = await state(k).getAttribute('data-state')
        return out
      }

      // snapshotting dev: nothing blocks it
      await expect(page.getByTestId('release-create')).toBeEnabled()
      await expect(state('promote')).toHaveAttribute('data-state', 'current')
      expect(await states()).toEqual({ code: 'done', secrets: 'done', routes: 'done', promote: 'current' })
      await expect(page.getByTestId('beam')).toHaveCount(0)
      await page.getByTestId('release-create').click()

      // promoting that snapshot to prod is blocked on Secrets: it is the current step, later ones wait
      await expect(page.getByTestId('check-secrets_present')).toHaveAttribute('data-status', 'fail')
      await expect(state('secrets')).toHaveAttribute('data-state', 'current')
      expect(await states()).toEqual({ code: 'done', secrets: 'current', routes: 'pending', promote: 'pending' })
      await expect(state('secrets').getByTestId('release-flow-blocked')).toBeVisible()
      await expect(page.getByTestId('release-promote')).toBeDisabled()

      // fix it inline (full form: the destination binding), re-run
      await page.getByTestId('secret-fix-PAYMENTS_TOKEN-form-more').click()
      await page.getByTestId('secret-fix-PAYMENTS_TOKEN-form-value').fill(`prod_${rand()}`)
      await page.getByTestId('secret-fix-PAYMENTS_TOKEN-form-hosts-input').fill('127.0.0.1')
      await page.getByTestId('secret-fix-PAYMENTS_TOKEN-form-hosts-input').press('Enter')
      await page.getByTestId('secret-fix-PAYMENTS_TOKEN-form-save').click()
      await page.getByTestId('secret-fix-rerun').click()
      await expect(page.getByTestId('check-secrets_present')).toHaveAttribute('data-status', 'pass')
      await expect(state('secrets')).toHaveAttribute('data-state', 'done')
      expect(await states()).toEqual({ code: 'done', secrets: 'done', routes: 'done', promote: 'current' })
      await expect(state('secrets').getByTestId('release-flow-blocked')).toHaveCount(0)
      await expect(page.getByTestId('release-promote')).toBeEnabled()

      // the beam is on while the promote request is in flight, and gone after
      await page.route(/\/releases\/[^/]+\/promote$/, async (route) => {
        await new Promise((r) => setTimeout(r, 1500))
        await route.continue()
      })
      await page.getByTestId('release-promote').click()
      await page.getByTestId('release-promote-confirm').click()
      await expect(page.getByTestId('beam')).toBeVisible()
      await expect(page.getByText(/prod now serves release/)).toBeVisible()
      await expect(page.getByTestId('beam')).toHaveCount(0)
      await expect(state('promote')).toHaveAttribute('data-state', 'done')
      await expect(page.getByTestId('release-env-prod-serving')).toBeVisible()
    })
  })
}

test.describe('liquid glass', () => {
  test.describe('with motion', () => {
    test.use({ reducedMotion: 'no-preference' })
    for (const theme of THEMES) {
      test(`refracts the sidebar lens, the segmented thumb and menu edges · ${theme}`, async ({ page, api, s, browserName }) => {
        test.skip(browserName !== 'chromium', 'liquid glass is Chromium only')
        const p = await seedProject(api, 'LG1')
        await seedRefunds(api, p)
        await page.emulateMedia({ colorScheme: theme })
        await page.goto(`${s.studioUrl}/p/${p.slug}`)
        await expectTheme(page, theme)
        const lens = page.locator('[data-slot="active-fill"]')
        await expect(lens).toHaveCount(1)
        await expect(lens).toHaveAttribute('data-liquid', '')
        await expect.poll(() => lens.evaluate((el) => (el as HTMLElement).style.backdropFilter)).toMatch(/^url\("?#lg-\d+"?\)/)
        // the filter it points at exists in the document
        const id = await lens.evaluate((el) => /#(lg-\d+)/.exec((el as HTMLElement).style.backdropFilter)![1])
        await expect(page.locator(`filter#${id}`)).toHaveCount(1)

        // the Runs segmented thumb
        await page.getByTestId('outline-runs').click()
        const thumb = page.getByTestId('runs-seg').locator('[data-slot="thumb"]')
        await expect(thumb).toHaveAttribute('data-liquid', '')
        await expect.poll(() => thumb.evaluate((el) => (el as HTMLElement).style.backdropFilter)).toContain('url(')

        // a dropdown popup gets the glass edge too
        await page.getByTestId('runs-trigger-filter').click()
        const popup = page.locator('[data-slot="select-content"], [role="listbox"]').first()
        await expect(popup).toBeVisible()
        await page.keyboard.press('Escape')
        await page.getByTestId('outline-table-refunds').click()
        await page.getByTestId('table-menu').click()
        await expect(page.getByTestId('table-import')).toBeVisible()
        const menu = page.locator('[data-slot="dropdown-menu-content"]')
        await expect(menu).toHaveAttribute('data-liquid', '')
        await expect.poll(() => menu.evaluate((el) => (el as HTMLElement).style.backdropFilter)).toContain('url(')
        // the menu's glass is released when it closes (no stale filters left behind)
        await page.keyboard.press('Escape')
        await expect(menu).toHaveCount(0)
        // popovers get it too (the project switcher)
        await page.getByTestId('project-switcher').click()
        const pop = page.locator('[data-slot="popover-content"]')
        await expect(pop).toHaveAttribute('data-liquid', '')
        await expect.poll(() => pop.evaluate((el) => (el as HTMLElement).style.backdropFilter)).toContain('url(')
      })
    }
  })

  test.describe('reduced motion', () => {
    test.use({ reducedMotion: 'reduce' })
    for (const theme of THEMES) {
      test(`falls back to the plain fills · ${theme}`, async ({ page, api, s }) => {
        const p = await seedProject(api, 'LG2')
        await seedRefunds(api, p)
        await page.emulateMedia({ colorScheme: theme, reducedMotion: 'reduce' })
        await page.goto(`${s.studioUrl}/p/${p.slug}`)
        await expectTheme(page, theme)
        const lens = page.locator('[data-slot="active-fill"]')
        await expect(lens).toHaveCount(1)
        await page.getByTestId('outline-runs').click()
        await expect(page.getByTestId('runs-seg')).toBeVisible()
        await page.getByTestId('outline-table-refunds').click()
        await page.getByTestId('table-menu').click()
        await expect(page.getByTestId('table-import')).toBeVisible()
        await page.keyboard.press('Escape')
        await page.getByTestId('project-switcher').click()
        await expect(page.locator('[data-slot="popover-content"]')).toBeVisible()
        // nothing anywhere is liquid, no filter was built, and the plain fills are in use
        await expect(page.locator('[data-liquid]')).toHaveCount(0)
        await expect(page.locator('filter[id^="lg-"]')).toHaveCount(0)
        await page.keyboard.press('Escape')
        await page.getByTestId('outline-runs').click()
        const thumb = page.getByTestId('runs-seg').locator('[data-slot="thumb"]')
        await expect(thumb).toBeVisible()
        expect(await thumb.evaluate((el) => (el as HTMLElement).style.backdropFilter)).toBe('')
        expect(await thumb.evaluate((el) => getComputedStyle(el).backgroundColor)).not.toBe('rgba(0, 0, 0, 0)')
        const fill = page.locator('[data-slot="active-fill"]')
        expect(await fill.evaluate((el) => getComputedStyle(el).backgroundColor)).not.toBe('rgba(0, 0, 0, 0)')
      })
    }
  })
})

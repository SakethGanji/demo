/**
 * Shell, projects home, old URLs, first run, theme (T1, T39, T43, T44).
 */
import { test, expect, rand } from '../fixtures/test'
import { answerWorkflow, refundsAppFile, seedApp, seedModule, seedProject, seedWorkflow } from '../fixtures/seed'

test('T1 projects home → create project', async ({ page, open, api }) => {
  const name = `Refunds Desk ${rand(4)}`
  await open('/projects')
  await expect(page.getByTestId('projects-home')).toBeVisible()
  await page.getByTestId('new-project').click()
  await page.getByTestId('new-project-name').fill(name)
  await page.getByTestId('new-project-submit').click()
  // first-run guide in the new project
  await expect(page.getByTestId('setup-guide')).toBeVisible()
  await expect(page.getByTestId('dock-overview')).toHaveAttribute('aria-label', 'Get started')
  const slug = new URL(page.url()).pathname.split('/')[2]
  // backend state: the project exists
  const projects = await api.get<Array<{ name: string; slug: string }>>('/projects')
  const created = projects.find((p) => p.name === name)
  expect(created?.slug).toBe(slug)
  // and its card is on /projects
  await open('/projects')
  await expect(page.getByTestId(`project-card-${slug}`)).toContainText(name)
})

test('T39 old URLs redirect', async ({ page, open, api, net, s }) => {
  const p = await seedProject(api, 'T39')
  const wf = await seedWorkflow(api, p, answerWorkflow('Legacy flow'))
  const app = await seedApp(api, p, 'Legacy app', [refundsAppFile()], [])

  await open(`/editor?workflowId=${wf.id}`)
  await expect(page).toHaveURL(`${s.studioUrl}/p/${p.slug}/workflows/${wf.id}`)

  await open(`/builder?appId=${app.id}`)
  await expect(page).toHaveURL(`${s.studioUrl}/p/${p.slug}/apps/${app.id}`)

  await open('/editor')
  await expect(page).toHaveURL(`${s.studioUrl}/p/default/workflows/new`)

  await open('/builder')
  await expect(page).toHaveURL(/\/p\/default\?dialog=create-ui/)
  await expect(page.getByTestId('create-ui-dialog')).toBeVisible()

  // unknown ids → /projects + toast
  await net.expect([{ method: 'GET', url: /\/api\/workflows\/wf_does_not_exist$/, status: 404 }], async () => {
    await open('/editor?workflowId=wf_does_not_exist')
    await expect(page).toHaveURL(`${s.studioUrl}/projects`)
    await expect(page.getByText('Workflow not found')).toBeVisible()
  })
  await net.expect([{ method: 'GET', url: /\/api\/apps\/app_does_not_exist$/, status: 404 }], async () => {
    await open('/builder?appId=app_does_not_exist')
    await expect(page).toHaveURL(`${s.studioUrl}/projects`)
  })
  // /p/{id} is replaced with /p/{slug}; an unknown project is a full-page not-found
  await open(`/p/${p.id}`)
  await expect(page).toHaveURL(`${s.studioUrl}/p/${p.slug}`)
  await net.expect([{ method: 'GET', url: /\/api\/projects\/no-such-project$/, status: 404 }], async () => {
    await open('/p/no-such-project/secrets')
    await expect(page.getByTestId('project-not-found')).toBeVisible()
  })
})

test('T43 first-run gating and completion', async ({ page, open, api }) => {
  const p = await seedProject(api, 'T43')
  await open(`/p/${p.slug}`)
  const guide = page.getByTestId('setup-guide')
  await expect(guide).toBeVisible()
  // steps 3–4 are gated until a route exists
  await expect(page.getByTestId('setup-step-3').getByTestId('needs-route')).toBeVisible()
  await expect(page.getByTestId('setup-step-4').getByTestId('needs-route')).toBeVisible()
  await expect(page.getByTestId('setup-progress')).toContainText('0 of 4')

  // 1. secret (UI)
  await page.getByTestId('setup-secret-name').fill('SETUP_TOKEN')
  await page.getByTestId('setup-secret-value').fill(`tok_${rand(12)}`)
  await page.getByTestId('setup-secret-save').click()
  await expect(page.getByTestId('setup-step-1-state')).toHaveAttribute('data-done', 'true')
  const secrets = await api.get<Array<{ key: string; environment: string; value: unknown }>>(`/projects/${p.id}/secrets`)
  expect(secrets.filter((x) => x.key === 'SETUP_TOKEN').map((x) => x.environment).sort()).toEqual(['dev', 'prod'])

  // 2. module (seeded through the API; the guide notices)
  await seedModule(api, p, 'hello', 'hello.py')
  await page.reload()
  await expect(page.getByTestId('setup-step-2-state')).toHaveAttribute('data-done', 'true')
  await expect(page.getByTestId('setup-step-3').getByTestId('needs-route')).toHaveCount(0)

  // 3. test a route (UI → backend view → Send)
  await page.getByTestId('setup-test-route').click()
  await expect(page.getByTestId('backend-view')).toBeVisible()
  await page.getByTestId('test-send').click()
  await expect(page.getByTestId('test-response').getByTestId('status-pill')).toHaveAttribute('data-status', '200')
  const runs = await api.get<Array<{ trigger: { type: string } }>>(`/projects/${p.id}/runs?trigger=route_test`)
  expect(runs.length).toBeGreaterThan(0)

  // 4. an app → the guide is replaced by the Overview
  const routes = await api.get<Array<{ id: string }>>(`/projects/${p.id}/routes`)
  await seedApp(api, p, 'Hello app', [refundsAppFile()], [routes[0].id])
  await page.reload() // seeded behind the UI's back, like the module in step 2
  await page.getByTestId('dock-overview').click()
  await expect(page.getByTestId('overview-panel')).toBeVisible()
  await expect(guide).toHaveCount(0)
  await open(`/p/${p.slug}?setup=1`)
  await expect(page.getByTestId('setup-progress')).toContainText('4 of 4')
})

test('T44 theme toggle', async ({ page, open, api }) => {
  const p = await seedProject(api, 'T44')
  await seedModule(api, p, 'hello', 'hello.py')
  await open(`/p/${p.slug}`)
  await expect(page.getByTestId('overview-panel')).toBeVisible()
  await page.getByTestId('theme-toggle').click()
  await expect(page.locator('html')).toHaveClass(/dark/)
  // the canvas is near-black space; the page is transparent so the sky shows through the glass
  const bg = await page.evaluate(() => getComputedStyle(document.body).backgroundColor)
  expect(bg).toBe('rgb(8, 9, 10)') // dark --canvas #08090a (neutral)
  const panelBg = await page.getByTestId('overview-panel').evaluate((el) => getComputedStyle(el).backgroundColor)
  expect(panelBg).toBe('rgba(0, 0, 0, 0)')
  // tiles are glass: the dark frost is white at 6%
  const glass = await page.evaluate(() => getComputedStyle(document.documentElement).getPropertyValue('--glass-fill').trim())
  expect(glass).toBe('rgb(255 255 255 / .03)')
  // the toggle persists across reload
  await page.reload()
  await expect(page.locator('html')).toHaveClass(/dark/)
  await page.getByTestId('theme-toggle').click()
  await expect(page.locator('html')).not.toHaveClass(/dark/)
  // light is pure white, never grey
  expect(await page.evaluate(() => getComputedStyle(document.body).backgroundColor)).toBe('rgb(255, 255, 255)')
})

/**
 * Backend view, manual path (T8, T9, T11, T12, T13, T17, T31, T32, T33, T34).
 */
import { test, expect } from '../fixtures/test'
import { editorText, errorLines, openModule, save, setSource } from '../fixtures/editor'
import { fixture, refundsAppFile, seedApp, seedModule, seedModuleSource, seedProject, seedRefunds, seedWorkflow, answerWorkflow } from '../fixtures/seed'

test('T8 saved-route tests', async ({ page, api, s }) => {
  const p = await seedProject(api, 'T8')
  await seedRefunds(api, p)
  await openModule(page, s.studioUrl, p.slug, 'refunds', '?route=createRefund')
  await expect(page.getByTestId('test-route-current')).toHaveText('/refunds')
  await page.getByTestId('req-tab-body').click()
  await page.getByTestId('req-body').fill('{"reason":"damaged","amount":84.5}')
  await page.getByTestId('test-send').click()
  const pill = page.getByTestId('test-response').getByTestId('status-pill')
  await expect(pill).toHaveAttribute('data-status', '200')
  await expect(page.getByTestId('response-body')).toContainText('"id"')
  // backend: the row exists and the run is a route_test
  const rows = await api.get<{ rows: Array<{ data: { amount: number } }>; count: number }>(`${await api.tablePath(p.id, 'refunds')}/rows?count=true`)
  expect(rows.count).toBe(1)
  expect(rows.rows[0].data.amount).toBe(84.5)
  // without amount → inner 422, shown amber
  await page.getByTestId('req-body').fill('{"reason":"lost"}')
  await page.getByTestId('test-send').click()
  await expect(pill).toHaveAttribute('data-status', '422')
  await expect(pill).toHaveClass(/text-warning-text/)
  // GET stats → count 1
  await page.getByTestId('test-route-picker').click()
  await page.getByTestId('test-route-option-refundStats').click()
  await page.getByTestId('test-send').click()
  await expect(pill).toHaveAttribute('data-status', '200')
  await expect(page.getByTestId('response-body')).toContainText('"count": 1')
  // trace shows the broker calls
  await expect(page.getByTestId('trace-call')).toHaveCount(2)
  const runs = await api.get<Array<{ id: string; trigger: { type: string }; route: string }>>(`/projects/${p.id}/runs?trigger=route_test`)
  expect(runs.length).toBe(3)
  expect(runs.every((x) => x.trigger.type === 'route_test')).toBe(true)
  // Runs view shows route_test and the run sheet shows the trace and metadata.request
  await page.getByTestId('test-execution-link').click()
  await expect(page).toHaveURL(new RegExp(`/runs/${runs[0].id}$`))
  const sheet = page.getByTestId('run-sheet')
  await expect(sheet).toBeVisible()
  await expect(sheet).toContainText('route_test')
  await expect(sheet.getByTestId('trace')).toBeVisible()
  await expect(sheet.getByTestId('run-request')).toBeVisible()
  const detail = await api.get<{ metadata: { request: unknown } }>(`/projects/${p.id}/runs/${runs[0].id}`)
  expect(detail.metadata.request).toBeTruthy()
})

test('T9 use as schema', async ({ page, api, s }) => {
  const p = await seedProject(api, 'T9')
  const r = await seedRefunds(api, p)
  const before = await api.get<Array<{ revision: number }>>(`/projects/${p.id}/spec/revisions`)
  await openModule(page, s.studioUrl, p.slug, 'refunds', '?route=createRefund')
  await page.getByTestId('req-tab-body').click()
  await page.getByTestId('req-body').fill('{"reason":"x"}')
  await page.getByTestId('test-send').click()
  await expect(page.getByTestId('test-response').getByTestId('status-pill')).toHaveAttribute('data-status', '422')
  await expect(page.getByTestId('use-as-schema')).toBeDisabled()
  // a 2xx enables it
  await page.getByTestId('test-route-picker').click()
  await page.getByTestId('test-route-option-refundStats').click()
  await page.getByTestId('test-send').click()
  await expect(page.getByTestId('test-response').getByTestId('status-pill')).toHaveAttribute('data-status', '200')
  await expect(page.getByTestId('use-as-schema')).toBeEnabled()
  await page.getByTestId('use-as-schema').click()
  await expect(page.getByText('Schema applied')).toBeVisible()
  const route = await api.get<{ response_schema: { properties: Record<string, unknown> } }>(`/projects/${p.id}/routes/${r.routeId('refundStats')}`)
  expect(route.response_schema.properties.count).toBeTruthy()
  const after = await api.get<Array<{ revision: number }>>(`/projects/${p.id}/spec/revisions`)
  expect(Math.max(...after.map((x) => x.revision))).toBe(Math.max(...before.map((x) => x.revision)) + 1)
})

test('T11 syntax error, then fix', async ({ page, api, s, net }) => {
  const p = await seedProject(api, 'T11')
  const m = await seedModule(api, p, 'hello', 'hello.py')
  const savedBefore = await api.get<{ source: string; source_sha: string }>(`/projects/${p.id}/modules/${m.module.id}`)
  await openModule(page, s.studioUrl, p.slug, 'hello')
  const broken = fixture('modules/broken.py')
  await net.expect(
    [
      // live analysis and the save's own preview both report it; nothing reaches the real PUT
      { method: 'PUT', url: /\/modules\/[^/]+\?dry_run=true$/, status: 422 },
    ],
    async () => {
      // the save path previews with a dry run; both report the syntax error
      await setSource(page, broken)
      await expect.poll(() => errorLines(page)).toEqual([7])
      await expect(page.getByTestId('status-errors')).toContainText('1 error')
      await save(page)
      await expect(page.getByTestId('editor-state')).toHaveText('1 error, not saved')
      await expect(page.getByText('Not saved: syntax error on line 7')).toBeVisible()
    },
  )
  // nothing was persisted
  const savedAfter = await api.get<{ source_sha: string }>(`/projects/${p.id}/modules/${m.module.id}`)
  expect(savedAfter.source_sha).toBe(savedBefore.source_sha)
  // a new module's live dry run creates nothing either
  const count = (await api.get<unknown[]>(`/projects/${p.id}/modules`)).length
  await net.expect([{ method: 'POST', url: /\/modules\?dry_run=true$/, status: 422 }], async () => {
    await page.goto(`${s.studioUrl}/p/${p.slug}?dialog=new-module`)
    await page.getByTestId('new-module-name').fill('scratch')
    await page.getByTestId('new-module-create').click()
    await expect(page.getByTestId('code-editor')).toBeVisible()
    await setSource(page, broken)
    await expect.poll(() => errorLines(page)).toEqual([7])
  })
  expect((await api.get<unknown[]>(`/projects/${p.id}/modules`)).length).toBe(count)
  // fix → saved
  await setSource(page, fixture('modules/broken_fixed.py'))
  await expect.poll(() => errorLines(page)).toEqual([])
  await save(page)
  await expect(page.getByText('Saved scratch.py')).toBeVisible()
  const mods = await api.get<Array<{ name: string }>>(`/projects/${p.id}/modules`)
  expect(mods.map((x) => x.name).sort()).toEqual(['hello', 'scratch'])
})

test('T12 autocomplete', async ({ page, api, s }) => {
  const p = await seedProject(api, 'T12')
  await seedRefunds(api, p)
  await openModule(page, s.studioUrl, p.slug, 'refunds')
  const content = page.getByTestId('code-editor').locator('.cm-content')
  await content.click()
  await page.keyboard.press('ControlOrMeta+End')
  await page.keyboard.press('Enter')
  await page.keyboard.type('x = studio.nodes.ht')
  const first = page.locator('.cm-tooltip-autocomplete li').first()
  await expect(first.locator('.cm-completionLabel')).toHaveText('http_request')
  await expect(first.locator('.cm-completionDetail')).toHaveText(/^\(\*, method, url/)
  await expect(first.getByTestId('completion-type')).toHaveText('HttpRequest')
  // CodeMirror ignores accept keys for 75 ms after the list opens (autocompletion `interactionDelay`)
  await page.waitForTimeout(250)
  await page.keyboard.press('Enter')
  await expect.poll(async () => (await editorText(page)).split('\n').pop()).toBe('x = studio.nodes.http_request(method="GET", url="")')
  await page.keyboard.press('ControlOrMeta+End')
  await page.keyboard.press('Enter')
  await page.keyboard.type('t = studio.db.table("')
  await expect(page.locator('.cm-tooltip-autocomplete .cm-completionLabel', { hasText: 'refunds' })).toBeVisible()
  await page.keyboard.press('Escape')
  await page.keyboard.press('ControlOrMeta+End')
  await page.keyboard.press('Enter')
  await page.keyboard.type('k = studio.secret("')
  await expect(page.locator('.cm-tooltip-autocomplete .cm-completionLabel', { hasText: 'PAYMENTS_TOKEN' })).toBeVisible()
})

test('T13 route conflict and invalid decorator', async ({ page, api, s, net }) => {
  const p = await seedProject(api, 'T13')
  const wf = await seedWorkflow(api, p, answerWorkflow('Create order'))
  await api.post(`/projects/${p.id}/routes`, { method: 'POST', path: '/orders', name: 'createOrder', handler: { type: 'workflow', workflow_id: wf.id } })
  await seedModule(api, p, 'hello', 'hello.py')
  await openModule(page, s.studioUrl, p.slug, 'hello')
  const src = `from studio import api\n\n\n@api.get("/hello", name="hello")\ndef hello(req):\n    return {"hello": "world"}\n\n\n@api.post("/orders", name="newOrder")\ndef new_order(req):\n    return {"ok": True}\n`
  const conflict = await net.expect(
    [
      { method: 'PUT', url: /\/modules\/[^/]+\?dry_run=true$/, status: 409 },
    ],
    async () => {
      await setSource(page, src)
      await expect.poll(() => errorLines(page)).toEqual([9])
      await page.getByTestId('code-editor').locator('.cm-lint-marker-error').hover()
      await expect(page.locator('.cm-tooltip-lint')).toContainText("POST /orders already exists (workflow 'Create order')")
      await save(page)
      await expect(page.getByText(/Not saved: route conflict on line 9/)).toBeVisible()
      return api.raw<{ detail: { code: string; conflicts: Array<{ handler: { name: string }; line: number }> } }>(
        'PUT',
        `/projects/${p.id}/modules/${(await api.get<Array<{ id: string }>>(`/projects/${p.id}/modules`))[0].id}?dry_run=true`,
        { source: src },
      )
    },
  )
  expect(conflict.status).toBe(409)
  expect(conflict.body.detail.code).toBe('route_conflict')
  expect(conflict.body.detail.conflicts[0].handler.name).toBe('Create order')
  expect(conflict.body.detail.conflicts[0].line).toBe(9)

  // auth=AUTH → invalid_decorator
  const bad = `from studio import api\n\nAUTH = "none"\n\n\n@api.get("/hello", name="hello", auth=AUTH)\ndef hello(req):\n    return {"hello": "world"}\n`
  await net.expect([{ method: 'PUT', url: /\/modules\/[^/]+\?dry_run=true$/, status: 422 }], async () => {
    await setSource(page, bad)
    await expect.poll(() => errorLines(page)).toEqual([6])
    await page.getByTestId('code-editor').locator('.cm-lint-marker-error').hover()
    await expect(page.locator('.cm-tooltip-lint')).toContainText('must be a string literal')
  })
})

test('T17 concurrent edit and stale draft restore', async ({ page, context, api, s, net }) => {
  const p = await seedProject(api, 'T17')
  const m = await seedModule(api, p, 'hello', 'hello.py')
  const pageB = await context.newPage()
  await openModule(page, s.studioUrl, p.slug, 'hello')
  await openModule(pageB, s.studioUrl, p.slug, 'hello')
  const srcA = fixture('modules/hello.py').replace('"world"', '"from A"')
  const srcB = fixture('modules/hello.py').replace('"world"', '"from B"')
  await setSource(page, srcA)
  await save(page)
  await expect(page.getByText('Saved hello.py')).toBeVisible()
  await setSource(pageB, srcB)
  await net.expect([{ method: 'PUT', url: /\/modules\/[^/]+\?dry_run=true$/, status: 409 }], async () => {
    await save(pageB)
    await expect(pageB.getByTestId('module-changed')).toBeVisible()
  })
  await pageB.getByTestId('module-changed-reload').click()
  await expect.poll(() => editorText(pageB)).toContain('"from A"')
  const saved = await api.get<{ source: string }>(`/projects/${p.id}/modules/${m.module.id}`)
  expect(saved.source).toContain('"from A"')

  // a dirty draft restored after someone else saved shows the stale banner
  await setSource(pageB, srcB)
  await expect(pageB.getByTestId('dirty-dot')).toBeVisible()
  await api.put(`/projects/${p.id}/modules/${m.module.id}`, { source: fixture('modules/hello.py').replace('"world"', '"from API"') })
  await pageB.reload()
  await expect(pageB.getByTestId('draft-restored-stale')).toBeVisible()
  await expect.poll(() => editorText(pageB)).toContain('"from B"')
  await pageB.getByTestId('stale-discard').click()
  await expect.poll(() => editorText(pageB)).toContain('"from API"')
  await pageB.close()
})

test('T31 breaking-change dialog', async ({ page, api, s }) => {
  const p = await seedProject(api, 'T31')
  const r = await seedRefunds(api, p)
  const app = await seedApp(api, p, 'Refunds app', [refundsAppFile()], [r.routeId('refundStats')])
  await openModule(page, s.studioUrl, p.slug, 'refunds')
  const src = fixture('modules/refunds.py')
  const withoutStats = src.slice(0, src.indexOf('@api.get("/refunds/stats"'))
  await setSource(page, withoutStats)
  await save(page)
  const dlg = page.getByTestId('breaking-dialog')
  await expect(dlg).toBeVisible()
  const usage = await api.get<Array<{ operation: string; line: number; file: string }>>(`/apps/${app.id}/api-usage`)
  const stats = usage.find((u) => u.operation === 'refundStats')!
  await expect(dlg.getByTestId('breaking-affected')).toContainText(`App.tsx:${stats.line}`)
  await dlg.getByTestId('breaking-confirm').click()
  await expect(page.getByText(/Saved refunds\.py/)).toBeVisible()
  await expect
    .poll(async () => (await api.get<Array<{ operation: string; status: string }>>(`/apps/${app.id}/api-usage`)).find((u) => u.operation === 'refundStats')?.status)
    .toBe('missing')
})

test('T32 auth loosening must be confirmed', async ({ page, api, s }) => {
  const p = await seedProject(api, 'T32')
  const src = `from studio import api\n\n\n@api.get("/secret-report", name="secretReport", auth="api_key")\ndef report(req):\n    return {"ok": True}\n`
  await seedModuleSource(api, p, 'reports', src)
  await openModule(page, s.studioUrl, p.slug, 'reports')
  await setSource(page, src.replace('auth="api_key"', 'auth="none"'))
  await save(page)
  const dlg = page.getByTestId('breaking-dialog')
  await expect(dlg).toBeVisible()
  await expect(dlg.locator('[data-kind="auth_loosened"]')).toBeVisible()
  // cancelling saves nothing
  await dlg.getByTestId('breaking-cancel').click()
  let route = (await api.get<Array<{ auth: string }>>(`/projects/${p.id}/routes`))[0]
  expect(route.auth).toBe('api_key')
  await save(page)
  await page.getByTestId('breaking-dialog').getByTestId('breaking-confirm').click()
  await expect(page.getByText(/Saved reports\.py/)).toBeVisible()
  route = (await api.get<Array<{ auth: string }>>(`/projects/${p.id}/routes`))[0]
  expect(route.auth).toBe('none')
})

test('T33 delete module', async ({ page, api, s }) => {
  const p = await seedProject(api, 'T33')
  const r = await seedRefunds(api, p)
  const app = await seedApp(api, p, 'Refunds app', [refundsAppFile()], [r.routeId('refundStats')])
  await openModule(page, s.studioUrl, p.slug, 'refunds')
  await page.getByTestId('module-menu').click()
  await page.getByTestId('module-delete').click()
  const dlg = page.getByTestId('delete-module-dialog')
  await expect(dlg.getByTestId('delete-module-routes').locator('[data-method]')).toHaveCount(3)
  // the app calls createRefund and refundStats → both routes carry the badge
  const usedBy = dlg.getByTestId('delete-module-used-by')
  await expect(usedBy).toHaveCount(2)
  for (const b of await usedBy.all()) await expect(b).toContainText('used by Refunds app')
  await expect(dlg).toContainText('table and its rows are kept')
  await expect(dlg.getByTestId('delete-module-confirm')).toBeDisabled()
  await dlg.getByTestId('delete-module-confirm-input').fill('refunds')
  await dlg.getByTestId('delete-module-confirm').click()
  await expect(page.getByText('Deleted refunds.py · 3 routes removed')).toBeVisible()
  await expect(page).toHaveURL(`${s.studioUrl}/p/${p.slug}`)
  expect(await api.get<unknown[]>(`/projects/${p.id}/routes`)).toEqual([])
  expect(await api.get<unknown[]>(`/projects/${p.id}/modules`)).toEqual([])
  const tables = await api.get<Array<{ name: string }>>(`/projects/${p.id}/tables`)
  expect(tables.map((t) => t.name)).toContain('refunds')
  await expect
    .poll(async () => (await api.get<Array<{ operation: string; status: string }>>(`/apps/${app.id}/api-usage`)).find((u) => u.operation === 'refundStats')?.status)
    .toBe('missing')
})

test('T34 route fields survive a module save', async ({ page, api, s }) => {
  const p = await seedProject(api, 'T34')
  const r = await seedRefunds(api, p)
  await openModule(page, s.studioUrl, p.slug, 'refunds', '?route=refundStats')
  await page.getByTestId('route-settings').click()
  await page.getByTestId('route-description').fill('Totals refunds by reason and shows the balance.')
  await page.getByTestId('route-settings-save').click()
  await expect(page.getByText('Saved settings for GET /refunds/stats')).toBeVisible()
  const desc = () => api.get<{ description: string }>(`/projects/${p.id}/routes/${r.routeId('refundStats')}`).then((x) => x.description)
  expect(await desc()).toBe('Totals refunds by reason and shows the balance.')
  // re-save the module with an unrelated change
  await setSource(page, fixture('modules/refunds.py') + '\n# touched\n')
  await save(page)
  await expect(page.getByText(/Saved refunds\.py/)).toBeVisible()
  expect(await desc()).toBe('Totals refunds by reason and shows the balance.')
})

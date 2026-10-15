/**
 * Publish, published runtime, release, settings (T25–T30, T27b, T36).
 */
import type { Page } from '@playwright/test'
import { test, expect, rand, type EngineApi } from '../fixtures/test'
import { refundsAppFile, seedApp, seedProject, seedPublished, seedRefunds, type P } from '../fixtures/seed'

async function refundsApp(api: EngineApi, test: string, opts: { secretEnvs?: string[] } = {}) {
  const p = await seedProject(api, test)
  const r = await seedRefunds(api, p, { secretEnvs: opts.secretEnvs ?? ['dev', 'prod'] })
  const app = await seedApp(api, p, 'Refunds app', [refundsAppFile()], [r.routeId('refundStats'), r.routeId('createRefund')])
  return { p, r, app }
}

async function openPublish(page: Page, studioUrl: string, p: P, appId: string) {
  await page.goto(`${studioUrl}/p/${p.slug}/apps/${appId}`)
  await page.getByTestId('builder-publish').click()
  await expect(page.getByTestId('publish-dialog')).toBeVisible()
}

test('T25 publish with pre-flight', async ({ page, context, api, s }) => {
  const { p, app } = await refundsApp(api, 'T25', { secretEnvs: ['dev'] })
  await openPublish(page, s.studioUrl, p, app.id)
  const dlg = page.getByTestId('publish-dialog')
  const slug = `refunds-${rand()}`
  await dlg.getByTestId('publish-slug').fill(slug)
  // PAYMENTS_TOKEN is used by the app's operations and missing in prod
  await expect(dlg.getByTestId('publish-pf-secret-PAYMENTS_TOKEN')).toBeVisible()
  await dlg.getByTestId('publish-add-secret-PAYMENTS_TOKEN').click()
  await page.getByTestId('create-secret-value').fill(`sk_prod_${rand(12)}`)
  await page.getByTestId('create-secret-save').click()
  await expect(dlg.getByTestId('publish-pf-secret-PAYMENTS_TOKEN')).toHaveCount(0)
  const secrets = await api.get<Array<{ key: string; environment: string }>>(`/projects/${p.id}/secrets`)
  expect(secrets.filter((x) => x.key === 'PAYMENTS_TOKEN').map((x) => x.environment).sort()).toEqual(['dev', 'prod'])
  await expect(dlg.getByTestId('publish-access-public')).toHaveAttribute('data-pressed', '')
  const pub = page.waitForRequest((x) => x.url().endsWith(`/api/apps/${app.id}/publish`))
  await dlg.getByTestId('publish-submit').click()
  expect((await pub).postDataJSON()).toMatchObject({ slug, access: 'public' })
  await expect(dlg.getByTestId('publish-success')).toBeVisible({ timeout: 60_000 })
  await expect(dlg.getByTestId('publish-url')).toContainText(`/a/${slug}`)
  const detail = await api.get<{ active: boolean; slug: string; access: string }>(`/apps/${app.id}`)
  expect(detail).toMatchObject({ active: true, slug, access: 'public' })
  // the published page calls the backend with an app token
  const pubPage = await context.newPage()
  const call = pubPage.waitForResponse((x) => x.url().includes(`/api/p/${p.slug}/refunds/stats`))
  await pubPage.goto(`${s.engineUrl}/a/${slug}`)
  const res = await call
  expect(res.status()).toBe(200)
  expect(res.request().headers()['authorization']).toMatch(/^Bearer .+/)
  const count = (await api.get<{ count: number }>(`/projects/${p.id}/tables/refunds/rows?count=true`)).count
  await expect(pubPage.getByTestId('refund-count')).toHaveText(String(count))
  await pubPage.close()
})

test('T26 slug taken', async ({ page, api, s, net }) => {
  const { p, r, app } = await refundsApp(api, 'T26')
  const slug = `taken-${rand()}`
  await seedPublished(api, app.id, slug)
  const other = await seedApp(api, p, 'Second app', [refundsAppFile()], [r.routeId('refundStats')])
  await openPublish(page, s.studioUrl, p, other.id)
  const dlg = page.getByTestId('publish-dialog')
  await dlg.getByTestId('publish-slug').fill(slug)
  await net.expect([{ method: 'POST', url: /\/publish$/, status: 409 }], async () => {
    await dlg.getByTestId('publish-submit').click()
    await expect(dlg.getByTestId('publish-slug-taken')).toBeVisible()
  })
  expect((await api.get<{ active: boolean }>(`/apps/${other.id}`)).active).toBe(false)
})

test('T27 the published app refreshes its token', async ({ page, api, s, net }) => {
  const { app } = await refundsApp(api, 'T27')
  await api.put(`/apps/${app.id}`, { token_ttl_s: 5 })
  const slug = `ttl-${rand()}`
  await seedPublished(api, app.id, slug)
  await page.goto(`${s.engineUrl}/a/${slug}`)
  await expect(page.getByTestId('refund-count')).not.toHaveText('…')
  await page.waitForTimeout(6500)
  const seenBefore = net.responses.length
  await net.expect([{ method: 'GET', url: /\/api\/p\//, status: 401 }], async () => {
    await page.getByTestId('refund-refresh').click()
    await expect.poll(() => net.responses.slice(seenBefore).filter((x) => x.url.includes('/api/p/') && x.status === 200).length).toBe(1)
  })
  const after = net.responses.slice(seenBefore)
  const unauthorized = after.filter((x) => x.url.includes('/api/p/') && x.status === 401)
  const refresh = after.filter((x) => x.url.endsWith(`/a/${slug}/_token`) && x.method === 'POST')
  expect(unauthorized.length).toBe(1)
  expect(refresh.map((x) => x.status)).toEqual([200])
  // order: 401, then the refresh, then the retried call
  const idx401 = after.indexOf(unauthorized[0])
  const idxRefresh = after.indexOf(refresh[0])
  const idxOk = after.findIndex((x) => x.url.includes('/api/p/') && x.status === 200)
  expect(idx401).toBeLessThan(idxRefresh)
  expect(idxRefresh).toBeLessThan(idxOk)
  await expect(page.getByTestId('refund-error')).toHaveCount(0)
})

test('T27b token refresh is authorised', async ({ api }) => {
  const { app, p, r } = await refundsApp(api, 'T27b')
  const slug = `pw-${rand()}`
  await seedPublished(api, app.id, slug, 'password', 'opensesame')
  const noCookie = await api.raw<{ error?: string; detail?: { code: string } }>('POST', `/a/${slug}/_token`)
  expect(noCookie.status).toBe(401)
  expect(JSON.stringify(noCookie.body)).toContain('unlock_required')
  const unlock = await fetch(`${api.base}/a/${slug}/_unlock`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
    body: 'password=opensesame',
    redirect: 'manual',
  })
  const cookie = unlock.headers.get('set-cookie') ?? ''
  expect(cookie).toMatch(/HttpOnly/i)
  expect(cookie).toMatch(/SameSite=Lax/i)
  expect(cookie).toMatch(new RegExp(`Path=/a/${slug}`, 'i'))
  const withCookie = await api.raw<{ token: string }>('POST', `/a/${slug}/_token`, undefined, { Cookie: cookie.split(';')[0] })
  expect(withCookie.status).toBe(200)
  expect(withCookie.body.token).toBeTruthy()
  // a private app does not exist publicly
  const priv = await seedApp(api, p, 'Private app', [refundsAppFile()], [r.routeId('refundStats')])
  const privSlug = `priv-${rand()}`
  await seedPublished(api, priv.id, privSlug, 'private')
  expect((await api.raw('POST', `/a/${privSlug}/_token`)).status).toBe(404)
})

test('T28 unpublish', async ({ page, api, s, net }) => {
  const { p, app } = await refundsApp(api, 'T28')
  const slug = `unpub-${rand()}`
  await seedPublished(api, app.id, slug)
  await openPublish(page, s.studioUrl, p, app.id)
  await page.getByTestId('publish-unpublish').click()
  await page.getByTestId('app-unpublish-confirm').click()
  await expect(page.getByTestId('app-publish-status')).toHaveAttribute('data-state', 'draft')
  expect((await api.get<{ active: boolean }>(`/apps/${app.id}`)).active).toBe(false)
  await net.expect([{ method: 'GET', url: new RegExp(`/a/${slug}$`), status: 404 }], async () => {
    await page.goto(`${s.engineUrl}/a/${slug}`)
  })
})

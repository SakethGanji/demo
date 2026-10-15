/** Release and settings scenarios missing from the original handoff. */
import type { Page } from '@playwright/test'
import { test, expect, rand, type EngineApi } from '../fixtures/test'
import { fixture, refundsAppFile, seedApp, seedModuleSource, seedProject, seedPublished, seedRefunds, seedRelease, type P } from '../fixtures/seed'
import { openModule, save, setSource } from '../fixtures/editor'

async function setup(api: EngineApi, name: string, secretEnvs = ['dev', 'prod']) {
  const p = await seedProject(api, name)
  const r = await seedRefunds(api, p, { secretEnvs })
  const app = await seedApp(api, p, 'Refunds dashboard', [refundsAppFile()], [r.routeId('refundStats'), r.routeId('createRefund')])
  const slug = `release-${rand()}`
  await seedPublished(api, app.id, slug)
  return { p, r, app, slug }
}

async function promote(page: Page) {
  await expect(page.getByTestId('release-promote')).toBeEnabled()
  await page.getByTestId('release-promote').click()
  await page.getByTestId('release-promote-confirm').click()
  await expect(page.getByText(/prod now serves release/)).toBeVisible()
}

async function browserStats(page: Page, p: P) {
  return page.evaluate(async (slug) => {
    const r = await fetch(`/api/p/${slug}/refunds/stats`)
    if (!r.ok) throw new Error(`stats returned ${r.status}`)
    return r.json()
  }, p.slug)
}

const changedSource = () => fixture('modules/refunds.py').replace('return {"count": len(rows),', 'return {"released_field": "v2", "count": len(rows),')

test('T29 release, fix missing secret, promote and keep the browser token', async ({ page, context, api, s }) => {
  const { p, slug } = await setup(api, 'T29', ['dev'])
  await page.goto(`${s.studioUrl}/p/${p.slug}?sheet=release`)
  await expect(page.getByTestId('release-create')).toBeEnabled()
  await page.getByTestId('release-create').click()
  await expect(page.getByTestId('check-secrets_present')).toHaveAttribute('data-status', 'fail')
  await expect(page.getByTestId('release-promote')).toBeDisabled()
  await page.getByTestId('secret-fix-PAYMENTS_TOKEN-form-value').fill(`prod_${rand()}`)
  // The full form carries the explicit destination binding needed by the broker.
  await page.getByTestId('secret-fix-PAYMENTS_TOKEN-form-more').click()
  await page.getByTestId('secret-fix-PAYMENTS_TOKEN-form-value').fill(`prod_${rand()}`)
  await page.getByTestId('secret-fix-PAYMENTS_TOKEN-form-hosts-input').fill('127.0.0.1')
  await page.getByTestId('secret-fix-PAYMENTS_TOKEN-form-hosts-input').press('Enter')
  await page.getByTestId('secret-fix-PAYMENTS_TOKEN-form-save').click()
  await page.getByTestId('secret-fix-rerun').click()
  await expect(page.getByTestId('check-secrets_present')).toHaveAttribute('data-status', 'pass')
  await promote(page)
  const pub = await context.newPage()
  await pub.goto(`${s.engineUrl}/a/${slug}`)
  await expect(pub.getByTestId('refund-count')).toHaveText('0')
  const token = await pub.evaluate(() => (window as unknown as { __APP_TOKEN: string }).__APP_TOKEN)
  expect(await browserStats(pub, p)).not.toHaveProperty('released_field')
  await openModule(page, s.studioUrl, p.slug, 'refunds')
  await setSource(page, changedSource())
  await save(page)
  await expect(page.getByText(/Saved refunds\.py/)).toBeVisible()
  expect(await browserStats(pub, p)).not.toHaveProperty('released_field')
  await page.goto(`${s.studioUrl}/p/${p.slug}?sheet=release`)
  await page.getByTestId('release-step-snapshot').click()
  await expect(page.getByTestId('release-create')).toBeEnabled()
  await page.getByTestId('release-create').click()
  await promote(page)
  expect(await browserStats(pub, p)).toHaveProperty('released_field', 'v2')
  expect(await pub.evaluate(() => (window as unknown as { __APP_TOKEN: string }).__APP_TOKEN)).toBe(token)
  const releases = await api.get<Array<{ environment: string; is_active: boolean; number: number }>>(`/projects/${p.id}/releases`)
  expect(releases.filter((r) => r.environment === 'prod' && r.is_active)).toHaveLength(1)
  await pub.close()
})

test('T30 rollback restores the earlier response and reports warnings', async ({ page, context, api, s }) => {
  const { p, r, slug } = await setup(api, 'T30')
  await api.post(`/projects/${p.id}/routes/stubs`, { routes: [{ name: 'future', method: 'GET', path: '/future' }] })
  const first = await seedRelease(api, p)
  await api.put(`/projects/${p.id}/modules/${r.mod.module.id}`, { source: changedSource() })
  await seedRelease(api, p)
  const pub = await context.newPage()
  await pub.goto(`${s.engineUrl}/a/${slug}`)
  await expect(pub.getByTestId('refund-count')).toHaveText('0')
  expect(await browserStats(pub, p)).toHaveProperty('released_field', 'v2')
  await page.goto(`${s.studioUrl}/p/${p.slug}?sheet=release`)
  await page.getByTestId('release-rollback-prod').click()
  await expect(page.getByText(/prod rolled back to release/)).toBeVisible()
  await expect(page.getByText(/Warnings:.*stub routes/)).toBeVisible()
  expect(await browserStats(pub, p)).not.toHaveProperty('released_field')
  const active = (await api.get<Array<{ id: string; is_active: boolean }>>(`/projects/${p.id}/releases?environment=prod`)).filter((r) => r.is_active)
  expect(active.map((r) => r.id)).toEqual([(first as { id: string }).id])
  await pub.close()
})

test('T36 settings rename and republish, secret scope, API keys and nonempty deletion', async ({ page, context, api, s, net }) => {
  const { p, slug } = await setup(api, 'T36')
  await page.goto(`${s.studioUrl}/p/${p.slug}?sheet=settings`)
  const nextSlug = `${p.slug}-new`
  await page.getByTestId('settings-slug').fill(nextSlug)
  await expect(page.getByTestId('slug-confirm')).toContainText('Published apps break until republished')
  await expect(page.getByTestId('settings-save')).toBeEnabled()
  await page.getByTestId('settings-save').click()
  await expect(page).toHaveURL(new RegExp(`/p/${nextSlug}\\?sheet=settings`))
  expect((await api.get<{ slug: string }>(`/projects/${p.id}`)).slug).toBe(nextSlug)
  const pub = await context.newPage()
  const call = pub.waitForResponse((r) => r.url().includes(`/api/p/${nextSlug}/refunds/stats`))
  await pub.goto(`${s.engineUrl}/a/${slug}`)
  expect((await call).status()).toBe(200)
  await expect(pub.getByTestId('refund-count')).toHaveText('0')
  expect((await api.raw('GET', `/api/p/${p.slug}/refunds/stats`)).status).toBe(404)
  const secrets = await api.get<Array<{ id: number }>>(`/projects/${p.id}/secrets`)
  expect((await api.raw('GET', `/variables/${secrets[0].id}`)).status).toBe(404)
  await seedModuleSource(api, p, 'keys', 'from studio import api\n@api.get("/key-check", name="keyCheck", auth="api_key")\ndef check(req):\n    return {"ok": True}\n')
  await page.getByTestId('settings-section-keys').click()
  await page.getByTestId('apikey-new').click()
  await page.getByTestId('apikey-name').fill('Browser test')
  await page.getByTestId('apikey-env-dev').click()
  await page.getByTestId('apikey-create').click()
  const key = await page.getByTestId('apikey-once-value').inputValue()
  expect(key.length).toBeGreaterThan(20)
  const keys = await api.get<Array<{ id: string; prefix: string; key?: string }>>(`/projects/${p.id}/api-keys`)
  expect(keys).toHaveLength(1)
  expect(JSON.stringify(keys)).not.toContain(key)
  await expect(page.getByTestId('apikey-prefix')).toHaveText(keys[0].prefix)
  expect((await api.raw('GET', `/api/p/${nextSlug}/key-check`, undefined, { Authorization: `Bearer ${key}` })).status).toBe(200)
  await page.getByRole('button', { name: 'I copied it' }).click()
  await expect(page.getByTestId('apikey-once')).toHaveCount(0)
  await page.getByTestId(`apikey-revoke-${keys[0].id}`).click()
  await page.getByTestId('apikey-revoke-confirm').click()
  await expect(page.getByTestId(`apikey-row-${keys[0].id}`)).toHaveAttribute('data-revoked', 'true')
  expect((await api.raw('GET', `/api/p/${nextSlug}/key-check`, undefined, { Authorization: `Bearer ${key}` })).status).toBe(401)
  await page.getByTestId('settings-section-danger').click()
  await page.getByTestId('danger-confirm').fill(nextSlug)
  await net.expect([{ method: 'DELETE', url: new RegExp(`/projects/${p.id}$`), status: 409 }], async () => {
    await page.getByTestId('danger-delete').click()
    await expect(page.getByTestId('project-not-empty')).toBeVisible()
  })
  await expect(page.getByTestId('not-empty-apps')).toContainText('1 app')
  expect((await api.get<{ id: string }>(`/projects/${p.id}`)).id).toBe(p.id)
  await pub.close()
})

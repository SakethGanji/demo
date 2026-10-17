/**
 * Regressions found by the live Marketplace scenario (apps/workflow-engine/tests/live/run_marketplace.py).
 */
import { test, expect } from '../fixtures/test'
import { refundsAppFile, seedApp, seedProject, seedRefunds, seedTable } from '../fixtures/seed'

test('/data?dialog=new-table keeps the dialog open when tables already exist (the /data redirect preserves search params)', async ({ page, api, s }) => {
  const p = await seedProject(api, 'L1')
  await seedTable(api, p, 'refunds')
  await page.goto(`${s.studioUrl}/p/${p.slug}/data?dialog=new-table`)
  // /data redirects to the first table; the deep-linked dialog must survive that redirect
  await expect(page).toHaveURL(new RegExp(`/p/${p.slug}/data/refunds\\?.*dialog=new-table`))
  const dialog = page.getByTestId('new-table-dialog')
  await expect(dialog).toBeVisible()
  await dialog.getByTestId('new-table-name').fill('customers')
  await dialog.getByTestId('new-table-columns-name-0').fill('email')
  await dialog.getByTestId('new-table-create').click()
  await expect(page).toHaveURL(new RegExp(`/data/customers`))
  expect((await api.get<Array<{ name: string }>>(`/projects/${p.id}/tables`)).map((t) => t.name).sort()).toEqual(['customers', 'refunds'])
})

test('finishing the setup guide shows the "You’re set up" toast once (outline and Overview both run the hook)', async ({ page, api, s }) => {
  const p = await seedProject(api, 'L2')
  await page.goto(`${s.studioUrl}/p/${p.slug}`)
  await expect(page.getByTestId('setup-guide')).toBeVisible() // marks the guide active for this project
  const r = await seedRefunds(api, p, { secretEnvs: ['dev', 'prod'] })
  await api.post(`/projects/${p.id}/routes/${r.routeId('refundStats')}/test`, { environment: 'dev', params: {}, query: {}, headers: {}, body: null })
  await seedApp(api, p, 'Refunds app', [refundsAppFile()], [r.routeId('refundStats')])
  await page.reload()
  const toast = page.getByText('You’re set up')
  await expect(toast.first()).toBeVisible()
  await page.waitForTimeout(500)
  await expect(toast).toHaveCount(1)
})

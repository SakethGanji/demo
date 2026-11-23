/**
 * Backend gaps (INTEGRATION-TEST-PLAN §0.3): chunked bulk CSV import, the HTTP status code on failed runs,
 * and the 30-day Activity grid fed by GET /projects/{p}/runs/daily.
 */
import type { Request } from '@playwright/test'
import { test, expect } from '../fixtures/test'
import { seedModuleSource, seedProject, seedTable } from '../fixtures/seed'

const MODULE = `from studio import api


@api.get("/ok", name="ok")
def ok(req):
    return {"ok": True}


@api.get("/boom", name="boom")
def boom(req):
    raise ValueError("kaput")
`

test('CSV import sends chunked bulk inserts (500 per request) and still reports progress', async ({ page, api, s }) => {
  const p = await seedProject(api, 'G1')
  await seedTable(api, p, 'refunds')
  await page.goto(`${s.studioUrl}/p/${p.slug}/data/refunds`)
  const posts: number[] = []
  page.on('request', (r: Request) => {
    if (r.method() === 'POST' && /\/tables\/[^/]+\/rows$/.test(r.url())) posts.push(r.postDataJSON()?.rows?.length ?? -1)
  })
  const lines = ['reason,amount']
  for (let i = 1; i <= 1200; i++) lines.push(`r${i},${i}`)
  await page.getByTestId('table-menu').click()
  await page.getByTestId('table-import').click()
  const sheet = page.getByTestId('import-sheet')
  await sheet.getByTestId('import-file').setInputFiles({ name: 'big.csv', mimeType: 'text/csv', buffer: Buffer.from(lines.join('\n')) })
  await expect(sheet.getByTestId('import-preview')).toContainText('1200 rows')
  await sheet.getByTestId('import-submit').click()
  await expect(sheet).toHaveCount(0, { timeout: 30_000 })
  expect(posts).toEqual([500, 500, 200])
  const base = await api.tablePath(p.id, 'refunds')
  expect((await api.get<{ count: number }>(`${base}/rows?count=true&limit=1`)).count).toBe(1200)
  await expect(page.getByTestId('data-row-count')).toContainText('1,200 rows')
})

test('a bad row in a chunk is dropped, the rest of the chunk still lands, and the toast names it', async ({ page, api, s, net }) => {
  const p = await seedProject(api, 'G2')
  await seedTable(api, p, 'refunds')
  await page.goto(`${s.studioUrl}/p/${p.slug}/data/refunds`)
  const lines = ['reason,amount']
  for (let i = 1; i <= 40; i++) lines.push(i === 17 ? 'bad,not-a-number' : `r${i},${i}`)
  await page.getByTestId('table-menu').click()
  await page.getByTestId('table-import').click()
  const sheet = page.getByTestId('import-sheet')
  await sheet.getByTestId('import-file').setInputFiles({ name: 'mixed.csv', mimeType: 'text/csv', buffer: Buffer.from(lines.join('\n')) })
  await net.expect([{ method: 'POST', url: /\/tables\/[^/]+\/rows$/, status: 422 }], async () => {
    await sheet.getByTestId('import-submit').click()
    await expect(sheet).toHaveCount(0, { timeout: 30_000 })
  })
  await expect(page.getByText('Imported 39 rows, 1 failed')).toBeVisible()
  await expect(page.getByText(/row 18: amount/)).toBeVisible()
  const base = await api.tablePath(p.id, 'refunds')
  expect((await api.get<{ count: number }>(`${base}/rows?count=true&limit=1`)).count).toBe(39)
})

test('failed runs show the HTTP status code on Runs and Overview; the Activity grid comes from /runs/daily', async ({ page, api, s }) => {
  const p = await seedProject(api, 'G3')
  await seedModuleSource(api, p, 'gaps', MODULE)
  const ids: string[] = []
  for (const path of ['ok', 'ok', 'ok', 'boom']) {
    const r = await api.raw('GET', `/api/p/${p.slug}/${path}`)
    ids.push(r.headers.get('x-execution-id')!)
  }
  const failedId = ids[3]
  const runs = await api.get<Array<{ id: string; status: string; status_code: number | null }>>(`/projects/${p.id}/runs`)
  expect(runs.find((r) => r.id === failedId)).toMatchObject({ status: 'failed', status_code: 502 })
  expect(runs.find((r) => r.id === ids[0])?.status_code).toBe(200)
  const daily = await api.get<Array<{ date: string; total: number; failed: number }>>(`/projects/${p.id}/runs/daily?days=30`)
  expect(daily).toHaveLength(30)
  expect(daily.reduce((n, d) => n + d.total, 0)).toBe(4)
  expect(daily.reduce((n, d) => n + d.failed, 0)).toBe(1)

  await page.goto(`${s.studioUrl}/p/${p.slug}/runs`)
  await page.getByTestId('runs-env-all').click()
  await expect(page.getByTestId('run-row')).toHaveCount(4)
  const failedRow = page.locator(`[data-testid="run-row"][data-run-id="${failedId}"]`)
  await expect(failedRow.getByTestId('run-status-code')).toHaveText('502')
  await expect(page.locator(`[data-testid="run-row"][data-run-id="${ids[0]}"]`).getByTestId('run-status-code')).toHaveCount(0)

  await page.goto(`${s.studioUrl}/p/${p.slug}`)
  const recent = page.getByTestId(`recent-run-${failedId}`)
  await expect(recent.getByTestId('recent-run-status-code')).toHaveText('502')
  const cells = page.getByTestId('activity-30d').locator('[role="gridcell"]')
  await expect(cells).toHaveCount(30)
  const total = await cells.evaluateAll((cs) => cs.reduce((n, c) => n + Number(c.getAttribute('data-count')), 0))
  const failed = await cells.evaluateAll((cs) => cs.reduce((n, c) => n + Number(c.getAttribute('data-failed')), 0))
  expect([total, failed]).toEqual([4, 1])
  await expect(page.getByTestId('activity-30d')).toContainText('1 failed')
})

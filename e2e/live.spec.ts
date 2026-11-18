// Live mode: the API is up, the status bar says so, and the New Model dataset step profiles server data.
import { apiMode, expect, open, test } from './fixtures'

test('status bar reports live mode with the API version', async ({ page, errors }) => {
  await open(page, 'home')
  await expect(apiMode(page)).toHaveAttribute('data-mode', 'live')
  await expect(apiMode(page)).toContainText('live')
  const health = await (await page.request.get('/api/health')).json()
  await expect(apiMode(page)).toContainText(health.version)
  expect(errors).toEqual([])
})

test('dataset step profiles a server sample', async ({ page, errors }) => {
  await open(page, 'quick-train')
  const list = page.getByTestId('dataset-list')
  await expect(list.getByTestId('dataset-support-tickets')).toBeVisible()
  await expect(list.getByTestId('dataset-account-churn')).toBeVisible()

  // It starts on the first server sample, profiled by the API.
  const profile = page.getByTestId('dataset-profile')
  await expect(profile).toHaveAttribute('data-source', 'api')
  await expect(page.getByTestId('profile-name')).toHaveText('support_tickets_sample.csv')
  await expect(page.getByTestId('profile-rows')).toContainText('20,000')
  await expect(page.getByTestId('profile-k')).toContainText('6')
  await expect(profile).toContainText('Generated sample')
  await expect(profile).toContainText('Security')
  await expect(page.getByTestId('profile-source')).toContainText('Profiled by the API')
  await expect(page.getByTestId('profile-quality')).toContainText('holdout=4,000')
  await expect(page.getByTestId('profile-warnings')).toContainText('rare')

  // The numbers on screen are the API's numbers.
  const api = await (await page.request.get('/api/datasets/support-tickets')).json()
  await expect(page.getByTestId('profile-quality')).toContainText(`dup=${api.profile.duplicates.rows}`)
  for (const c of api.profile.classes) await expect(profile).toContainText(`${(c.share * 100).toFixed(1)}%`)

  // Switch to the churn sample.
  await list.getByTestId('dataset-account-churn').click()
  await expect(page.getByTestId('profile-name')).toHaveText('account_churn_sample.csv')
  await expect(page.getByTestId('profile-rows')).toContainText('10,000')
  await expect(page.getByTestId('profile-k')).toContainText('2')
  await expect(profile).toContainText('nps')
  await expect(page.getByTestId('profile-quality')).toContainText('missing=')
  await expect(page.locator('.valid')).toContainText('target churned · K = 2')
  expect(errors).toEqual([])
})

test('uploading a CSV profiles it on the server', async ({ page, errors }) => {
  await open(page, 'quick-train')
  await expect(page.getByTestId('dataset-profile')).toHaveAttribute('data-source', 'api')
  const rows = ['id,message,label']
  for (let i = 0; i < 40; i++) rows.push(`${i},"order ${i} arrived on time and the item works well enough for me",${i % 4 === 0 ? 'bad' : 'good'}`)
  await page.getByTestId('dataset-file').setInputFiles({ name: 'e2e_upload.csv', mimeType: 'text/csv', buffer: Buffer.from(rows.join('\n')) })
  await expect(page.getByTestId('profile-name')).toHaveText('e2e_upload.csv')
  await expect(page.getByTestId('profile-rows')).toContainText('40')
  await expect(page.getByTestId('profile-k')).toContainText('2')
  await expect(page.getByTestId('profile-source')).toContainText('Profiled by the API')
  await expect(page.locator('.valid')).toContainText('target label')
  const listed = await (await page.request.get('/api/datasets')).json()
  expect(listed.datasets.map((d: { filename: string }) => d.filename)).toContain('e2e_upload.csv')
  expect(errors).toEqual([])
})

test('a rejected upload shows the API message', async ({ page }) => {
  await open(page, 'quick-train')
  await expect(page.getByTestId('dataset-profile')).toHaveAttribute('data-source', 'api')
  await page.getByTestId('dataset-file').setInputFiles({ name: 'one_row.csv', mimeType: 'text/csv', buffer: Buffer.from('a,b\n1,2\n') })
  await expect(page.getByRole('alert')).toContainText('fewer than two data rows')
  await expect(page.getByTestId('profile-name')).toHaveText('support_tickets_sample.csv')
})

test('a real AutoGluon run streams models and the stacking equation', async ({ page, errors }) => {
  test.setTimeout(240_000)
  await open(page, 'quick-train')
  await expect(page.getByTestId('dataset-profile')).toHaveAttribute('data-source', 'api')
  // A small upload so the fit is quick: two numeric signals and a category.
  const rows = ['row_id,x1,x2,grp,label']
  for (let i = 0; i < 160; i++) {
    const x1 = Math.sin(i * 1.7).toFixed(3), x2 = Math.cos(i * 0.9).toFixed(3), grp = ['a', 'b', 'c'][i % 3]
    rows.push(`r${i},${x1},${x2},${grp},${Number(x1) + 0.5 * Number(x2) > 0 ? 'yes' : 'no'}`)
  }
  await page.getByTestId('dataset-file').setInputFiles({ name: 'e2e_live_run.csv', mimeType: 'text/csv', buffer: Buffer.from(rows.join('\n')) })
  await expect(page.getByTestId('profile-name')).toHaveText('e2e_live_run.csv')
  await page.getByRole('button', { name: 'Continue →' }).click()
  await page.getByRole('button', { name: 'Continue →' }).click()
  // The run's budget, as the old minimal card used: a 60 s medium_quality fit.
  await page.getByRole('slider', { name: 'time_limit' }).focus()
  await page.getByLabel('time_limit value').fill('60')
  await page.getByLabel('time_limit value').press('Enter')
  await page.getByTestId('start-training').click()

  const race = page.getByTestId('race')
  await expect(race).toHaveAttribute('data-status', /queued|running|succeeded/, { timeout: 30_000 })
  await expect(race).toHaveAttribute('data-status', 'succeeded', { timeout: 200_000 })

  // What the stage shows is what the API recorded.
  const runId = (await page.getByTestId('race-run-id').textContent())!.trim()
  const run = await (await page.request.get(`/api/runs/${runId}`)).json()
  expect(run.status).toBe('succeeded')
  expect(run.config.time_limit).toBe(60)
  for (const r of run.leaderboard) await expect(race.getByTestId(`race-model-${r.model}`)).toHaveAttribute('data-status', 'done')
  const eq = page.getByTestId('race-equation')
  await expect(eq).toContainText(run.ensemble.model)
  for (const [m, w] of Object.entries(run.ensemble.weights as Record<string, number>)) if (w > 0) await expect(eq).toContainText(`${m} · w`), await expect(eq).toContainText(w.toFixed(4))
  await page.getByTestId('see-results').click()
  await expect(page.getByTestId('confusion')).toContainText(`n=${run.holdout.n_rows.toLocaleString()}`)
  expect(errors).toEqual([])
})

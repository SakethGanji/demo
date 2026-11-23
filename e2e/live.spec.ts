// Live mode: the API is up, the status bar says so, and a real run on an uploaded table streams into the race.
import { apiMode, expect, open, test, uploadCsv } from './fixtures'

test('status bar reports live mode with the API version', async ({ page, errors }) => {
  await open(page, 'home')
  await expect(apiMode(page)).toHaveAttribute('data-mode', 'live')
  await expect(apiMode(page)).toContainText('live')
  const health = await (await page.request.get('/api/health')).json()
  await expect(apiMode(page)).toContainText(health.version)
  expect(errors).toEqual([])
})

test('a real AutoGluon run streams models and the stacking equation', async ({ page, errors }) => {
  test.setTimeout(240_000)
  // A small upload so the fit is quick: two numeric signals and a category.
  const rows = ['row_id,x1,x2,grp,label']
  for (let i = 0; i < 160; i++) {
    const x1 = Math.sin(i * 1.7).toFixed(3), x2 = Math.cos(i * 0.9).toFixed(3), grp = ['a', 'b', 'c'][i % 3]
    rows.push(`r${i},${x1},${x2},${grp},${Number(x1) + 0.5 * Number(x2) > 0 ? 'yes' : 'no'}`)
  }
  const ds = await uploadCsv(page.request, 'e2e_live_run.csv', rows.join('\n'))
  // Build hands a dataset to New Model by id; the wizard opens on the Target step.
  await open(page, `quick-train?dataset=${ds.id}`)
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
  await expect(page.getByTestId('results')).toHaveAttribute('data-run', runId)
  expect(errors).toEqual([])
})

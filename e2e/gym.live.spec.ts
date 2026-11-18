// The Training Gym and Vela on the real engine: configure in Advanced (a slider, the raw fit_kwargs both ways, server
// validation), start a run that hands off to the live race, read Vela's computed findings, apply a patch, retrain the
// patched config and compare parent and child in Experiments. Every number checked against the API.
import { expect, open, test } from './fixtures'

const HP = { GBM: { num_boost_round: 30 }, LR: {} }

function leakyCsv(n = 700) {
  let seed = 11
  const rnd = () => { seed = (seed * 16807) % 2147483647; return seed / 2147483647 }
  const gauss = () => Math.sqrt(-2 * Math.log(rnd() + 1e-12)) * Math.cos(2 * Math.PI * rnd())
  const rows = ['x1,x2,region,collections_flag,outcome']
  for (let i = 0; i < n; i++) {
    const x1 = gauss(), x2 = gauss()
    const y = x1 + 0.5 * x2 + gauss() > 0.6
    rows.push(`${x1.toFixed(4)},${x2.toFixed(4)},${['n', 's', 'e'][i % 3]},${y ? 'sent' : 'none'},${y ? 'bad' : 'good'}`)
  }
  return rows.join('\n') + '\n'
}

test('gym: configure in Advanced, train, apply a Vela patch, retrain, compare', async ({ page, errors }) => {
  test.setTimeout(600_000)
  const up = await page.request.post('/api/datasets', { multipart: { file: { name: 'gym_leaky.csv', mimeType: 'text/csv', buffer: Buffer.from(leakyCsv()) } } })
  expect(up.status()).toBe(201)
  const ds = await up.json()

  await open(page, 'gym')
  await expect(page.getByTestId('gym-live')).toBeVisible({ timeout: 30_000 })
  await expect(page.getByTestId('prototype-banner')).toHaveCount(0)
  await page.getByTestId('gym-dataset').selectOption(ds.id)
  await expect(page.getByTestId('gym-title')).toContainText('outcome')

  // Advanced: the raw editor first (fit_kwargs → form), then a slider (form → fit_kwargs)
  await page.getByRole('tab', { name: 'Advanced' }).click()
  await page.getByTestId('gym-step-10').click()
  const raw = page.getByTestId('gym-raw-fit')
  await raw.fill(JSON.stringify({ hyperparameters: HP, num_bag_fold: 3 }))
  await expect(page.getByTestId('gym-raw-status')).toHaveText('1 invalid')
  await expect(page.getByTestId('gym-check-count')).toHaveText('✕ 1')
  await expect(page.getByTestId('gym-start')).toBeDisabled()
  await raw.fill(JSON.stringify({ hyperparameters: HP, num_bag_folds: 3 }))
  await expect(page.getByTestId('gym-raw-status')).toHaveText('valid')
  await expect(page.getByTestId('gym-call-code')).toContainText('num_bag_folds=3')
  await expect(page.getByTestId('gym-effective')).toContainText(/k\s*=\s*3/)

  await page.getByTestId('gym-step-5').click()
  await page.getByTestId('gym-hp-tab-GBM').click()
  await page.getByRole('textbox', { name: 'Learning rate value' }).fill('0.1')
  await page.getByRole('textbox', { name: 'Learning rate value' }).press('Enter')
  await expect(page.getByTestId('gym-formula-GBM')).toContainText('learning_rate = 0.1')
  await expect(page.getByTestId('gym-call-code')).toContainText('"learning_rate": 0.1')
  await page.getByTestId('gym-step-10').click()
  await expect(raw).toHaveValue(/"learning_rate": 0\.1/)

  // the server's estimate and AutoGluon's own decision agree with /api/gym/validate for the same body
  const body = { dataset_id: ds.id, label: 'outcome', problem_type: 'auto', eval_metric: null, presets: 'medium_quality', time_limit: 120,
    fit_kwargs: { hyperparameters: { GBM: { num_boost_round: 30, learning_rate: 0.1 }, LR: {} }, num_bag_folds: 3 }, predictor_kwargs: {},
    calibrate_threshold: 'auto', feature_importance: true, drop_columns: null }
  const v = await (await page.request.post('/api/gym/validate', { data: body })).json()
  expect(v.ok).toBe(true)
  await expect(page.getByTestId('gym-estimate')).toContainText(`k = ${v.estimate.k}`)
  await expect(page.getByTestId('gym-call-code')).toContainText('"learning_rate": 0.1')

  // Start Training hands off to the live race
  await page.getByTestId('gym-start').click()
  await expect(page).toHaveURL(/#\/quick-train\?run=run_/, { timeout: 30_000 })
  const parentId = new URL(page.url()).hash.split('run=')[1]
  const race = page.getByTestId('race')
  await expect(race).toBeVisible()
  await expect(race).toHaveAttribute('data-status', 'succeeded', { timeout: 300_000 })
  const parent = await (await page.request.get(`/api/runs/${parentId}`)).json()
  expect(parent.config.source).toBe('gym')
  expect(parent.resolved.fit_kwargs.num_bag_folds).toBe(3)
  expect(parent.resolved.fit_kwargs.hyperparameters.GBM.learning_rate).toBe(0.1)
  expect(parent.features).toContain('collections_flag')

  // Vela reads this run (the URL names it): the planted leak is flagged with its number
  if (!(await page.getByTestId('vela-run').isVisible())) await page.getByRole('button', { name: 'Ask Vela' }).click()
  await expect(page.getByTestId('vela-run')).toHaveValue(parentId)
  const vela = await (await page.request.get(`/api/runs/${parentId}/vela`)).json()
  const leak = vela.findings.find((f: { kind: string }) => f.kind === 'leakage')
  expect(leak.numbers.suspects[0].feature).toBe('collections_flag')
  const card = page.getByTestId('vela-finding-leakage')
  await expect(card).toContainText('collections_flag')
  await expect(card).toContainText(leak.numbers.suspects[0].single_score.toFixed(3))
  await expect(page.getByTestId('vela-source')).toContainText('wording:', { timeout: 60_000 })
  await page.getByTestId('vela-apply-leakage').click()

  // the patch lands in the Gym, on top of the parent's config
  await expect(page).toHaveURL(/#\/gym/)
  await expect(page.getByTestId('gym-patch-banner')).toContainText('drop_columns')
  await expect(page.getByTestId('gym-from')).toHaveText(`from ${parentId}`)
  await expect(page.getByTestId('gym-call-code')).toContainText('dropped: collections_flag')
  await expect(page.getByTestId('gym-call-code')).toContainText('"learning_rate": 0.1')
  await expect(card).toContainText('Applied')

  // retrain: a child run with the patch, then compare in Experiments
  await page.getByTestId('gym-start-now').click()
  await expect(page).toHaveURL(new RegExp(`#/quick-train\\?run=(?!${parentId})run_`), { timeout: 30_000 })
  const childId = new URL(page.url()).hash.split('run=')[1]
  await expect(page.getByTestId('race')).toHaveAttribute('data-status', 'succeeded', { timeout: 300_000 })
  const child = await (await page.request.get(`/api/runs/${childId}`)).json()
  expect(child.config.parent_run_id).toBe(parentId)
  expect(child.config.patch.id).toBe('leakage')
  expect(child.features).not.toContain('collections_flag')
  expect(child.resolved.fit_kwargs.hyperparameters.GBM.learning_rate).toBe(0.1)

  await page.getByTestId('see-results').click()
  await page.getByTestId('results-compare-parent').click()
  await expect(page).toHaveURL(/#\/experiments\?compare=/)
  const cmp = page.getByTestId('exp-patch-compare')
  await expect(cmp).toContainText(parentId)
  await expect(cmp).toContainText(childId)
  await expect(cmp).toContainText('drop_columns')
  const m = parent.eval_metric.name
  const d = child.holdout.metrics[m] - parent.holdout.metrics[m]
  expect(d).toBeLessThan(0) // honest: without the leaky column the holdout score drops
  await expect(page.getByTestId('exp-patch-delta')).toContainText(d.toFixed(4))
  await expect(page.getByTestId(`exp-lineage-${childId}`)).toHaveCount(0) // the table is on the Runs tab
  await page.getByRole('tab', { name: 'Runs' }).click()
  await expect(page.getByTestId(`exp-lineage-${childId}`)).toContainText(parentId)

  // export: the run as a script
  const py = await (await page.request.get(`/api/runs/${childId}/export.py`)).text()
  expect(py).toContain('DROP_COLUMNS = ["collections_flag"]')
  expect(errors).toEqual([])
})

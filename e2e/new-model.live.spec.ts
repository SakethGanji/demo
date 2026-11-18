// The New Model wizard in live mode, against the real API and real AutoGluon fits (small fast configs):
// upload → target → goal → the race → results → a custom ensemble → a prediction. Every number checked on screen is
// compared with what the API recorded.
import type { Page } from '@playwright/test'
import { expect, open, test } from './fixtures'

const FAST = { hyperparameters: { GBM: { num_boost_round: 40 }, LR: {}, KNN: {} } }

type Lb = { model: string; family: string; score_val_natural: number; score_test_natural: number; weights: Record<string, number> | null }

async function throughTheWizard(page: Page, fitKwargs: object, opts: { label?: string; expectTask: string }) {
  await page.getByRole('button', { name: 'Continue →' }).click()
  // Target: label and the inferred problem type
  if (opts.label) await page.getByTestId('label-select').selectOption(opts.label)
  await expect(page.getByTestId('problem-type').locator('button.on')).toHaveText(opts.expectTask)
  await expect(page.getByTestId('problem-type-note')).toContainText(`inferred: ${opts.expectTask}`)
  await page.getByRole('button', { name: 'Continue →' }).click()
  // Goal: preset ladder, budget, raw fit_kwargs
  await expect(page.getByTestId('preset-medium_quality')).toHaveAttribute('aria-pressed', 'true')
  await page.getByTestId('advanced').locator('.adv-toggle').click()
  await page.getByTestId('fit-kwargs').fill(JSON.stringify(fitKwargs))
  await expect(page.getByTestId('resolved-call')).toContainText('hyperparameters=')
  await page.getByTestId('start-training').click()
}

async function raceToTheEnd(page: Page) {
  const race = page.getByTestId('race')
  await expect(race).toBeVisible()
  await expect(race).toHaveAttribute('data-status', /queued|running|succeeded/, { timeout: 30_000 })
  await race.getByRole('button', { name: '3×' }).click()
  await expect(race).toContainText('≥ 0.47 s')
  await expect(race).toHaveAttribute('data-status', 'succeeded', { timeout: 280_000 })
  const runId = (await page.getByTestId('race-run-id').textContent())!.trim()
  expect(page.url()).toContain(`run=${runId}`)
  const run = await (await page.request.get(`/api/runs/${runId}`)).json()
  expect(run.status).toBe('succeeded')
  // one string per model, each settled at the score the API recorded
  for (const r of run.leaderboard as Lb[]) {
    const row = page.getByTestId(`race-model-${r.model}`)
    await expect(row).toHaveAttribute('data-status', 'done', { timeout: 20_000 })
  }
  // the ensemble formed, with the real weights in the equation
  const eq = page.getByTestId('race-equation')
  await expect(eq).toContainText(run.ensemble.model)
  for (const [m, w] of Object.entries(run.ensemble.weights as Record<string, number>)) {
    if (w > 0) { await expect(eq).toContainText(m); await expect(eq).toContainText(w.toFixed(4)) }
  }
  await expect(page.getByTestId('ensemble-merge')).toBeVisible()
  return run
}

async function checkResults(page: Page, run: { id: string; best_model: string; leaderboard: Lb[]; eval_metric: { name: string }; holdout: { metrics: Record<string, number> } }) {
  await page.getByTestId('see-results').click()
  await expect(page.getByTestId('results')).toHaveAttribute('data-run', run.id)
  const lb = page.getByTestId('leaderboard')
  for (const r of run.leaderboard) {
    const row = lb.getByTestId(`lb-row-${r.model}`)
    await expect(row).toHaveAttribute('data-best', r.model === run.best_model ? 'true' : 'false')
  }
  const metric = run.eval_metric.name
  const v = run.holdout.metrics[metric]
  await expect(page.getByTestId(`metric-value-${metric}`)).toHaveText(fmt(v))
  // the bootstrap interval brackets the point estimate
  const ci = await page.getByTestId(`metric-card-${metric}`).locator('.mcard-ci').textContent()
  const [lo, hi] = ci!.match(/\[([-\d.,]+), ([-\d.,]+)\]/)!.slice(1).map((s) => Number(s.replace(/,/g, '')))
  expect(lo).toBeLessThanOrEqual(v + 1e-9)
  expect(hi).toBeGreaterThanOrEqual(v - 1e-9)
}

async function customEnsemble(page: Page, runId: string, pick: string[]) {
  const ce = page.getByTestId('custom-ensemble')
  const opts = ce.locator('[data-testid^=ce-opt-]')
  for (let i = 0; i < await opts.count(); i++) {
    const m = (await opts.nth(i).getAttribute('data-testid'))!.replace('ce-opt-', '')
    await opts.nth(i).locator('input').setChecked(pick.includes(m))
  }
  await ce.getByTestId('ce-fit').click()
  await expect(ce.getByTestId('ce-verdict')).toBeVisible({ timeout: 120_000 })
  const run = await (await page.request.get(`/api/runs/${runId}`)).json()
  const res = run.custom_ensembles.at(-1)
  expect(res.base_models.sort()).toEqual([...pick].sort())
  const eq = ce.getByTestId('ce-equation')
  await expect(eq).toContainText(res.model)
  for (const [m, w] of Object.entries(res.weights as Record<string, number>)) if (w > 0) await expect(eq).toContainText(`${m} · w`), await expect(eq).toContainText(w.toFixed(4))
  // the comparison is the API's, and the verdict follows it honestly
  const compare = ce.getByTestId('ce-compare')
  await expect(compare).toContainText(fmt(res.scores.score_test_natural))
  await expect(compare).toContainText(res.best_single.model)
  await expect(ce.getByTestId('ce-verdict')).toContainText(res.vs_best_single.holdout.beats ? 'Better than' : res.n_selected === 1 ? 'one model' : 'not')
  // the new ensemble is on the leaderboard, marked as yours
  await expect(page.getByTestId(`lb-row-${res.model}`)).toContainText('yours')
  return res
}

/** The form starts on the profile's example values: send the same row to the API and compare. */
async function examplesRow(page: Page, datasetId: string, features: string[]) {
  const ds = await (await page.request.get(`/api/datasets/${datasetId}`)).json()
  const row: Record<string, unknown> = {}
  for (const c of ds.profile.columns) {
    if (!features.includes(c.name) || c.example === '') continue
    row[c.name] = c.type === 'number' ? Number(c.example) : c.example
  }
  return row
}

/** The app's number format (fmtScore in src/features/quick-train/wizard.ts). */
const fmtD = (v: number, d: number) => {
  const a = Math.abs(v)
  if (a >= 10_000) return v.toLocaleString('en-US', { maximumFractionDigits: 0 })
  if (a >= 100) return v.toLocaleString('en-US', { maximumFractionDigits: 1 })
  if (a >= 10) return v.toFixed(2)
  return v.toFixed(d)
}
const fmt = (v: number) => fmtD(v, 4)

test('binary: upload a fresh CSV, train, race, leaderboard, custom ensemble, prediction, resume after reload', async ({ page, errors }) => {
  test.setTimeout(420_000)
  await open(page, 'quick-train')
  await expect(page.getByTestId('dataset-profile')).toHaveAttribute('data-source', 'api')
  const rows = ['customer_id,balance,tenure,segment,txn_90d,defaulted']
  for (let i = 0; i < 420; i++) {
    const bal = (Math.sin(i * 1.3) * 5000 + 6000).toFixed(0), ten = (i * 7) % 60, seg = ['retail', 'sme', 'private'][i % 3], txn = (i * 13) % 50
    const risk = -Number(bal) / 4000 + (seg === 'sme' ? 0.8 : 0) - ten / 40 + Math.cos(i) * 0.7
    rows.push(`c${1000 + i},${bal},${ten},${seg},${txn},${risk > -1.6 ? 'yes' : 'no'}`)
  }
  await page.getByTestId('dataset-file').setInputFiles({ name: 'e2e_fresh_binary.csv', mimeType: 'text/csv', buffer: Buffer.from(rows.join('\n')) })
  await expect(page.getByTestId('profile-name')).toHaveText('e2e_fresh_binary.csv')
  await expect(page.locator('.valid')).toContainText('target defaulted · K = 2')
  // A few more models than FAST, so the reload below lands while the fit is still running.
  await throughTheWizard(page, { hyperparameters: { ...FAST.hyperparameters, RF: { n_estimators: 300 }, XT: { n_estimators: 300 }, CAT: { iterations: 200 } } }, { expectTask: 'binary' })

  // Reload mid-run: the race resumes from the URL by replaying the run's events.
  const race = page.getByTestId('race')
  await expect(race).toBeVisible()
  const runId = (await page.getByTestId('race-run-id').textContent())!.trim()
  await expect(page.locator('[data-testid^=race-model-][data-status=done]').first()).toBeVisible({ timeout: 120_000 })
  const before = await (await page.request.get(`/api/runs/${runId}`)).json()
  test.info().annotations.push({ type: 'reload', description: `run was ${before.status} at reload` })
  await page.reload()
  await expect(page.getByTestId('race-run-id').or(page.getByTestId('results'))).toBeVisible({ timeout: 30_000 })
  if (await page.getByTestId('results').isVisible()) await page.getByTestId('step-train').click()
  await expect(page.getByTestId('race-run-id')).toHaveText(runId)

  const run = await raceToTheEnd(page)
  expect(run.id).toBe(runId)
  expect(run.problem_type).toBe('binary')
  await checkResults(page, run)
  await expect(page.getByTestId('confusion')).toContainText(`n=${run.holdout.n_rows.toLocaleString()}`)

  const base = (run.leaderboard as Lb[]).filter((r) => !r.model.startsWith('WeightedEnsemble')).map((r) => r.model)
  await customEnsemble(page, run.id, base.slice(0, 2))

  const tp = page.getByTestId('try-prediction')
  await tp.getByTestId('tp-predict').click()
  await expect(tp.getByTestId('tp-class')).toBeVisible({ timeout: 60_000 })
  const row = await examplesRow(page, run.dataset_id, run.data.features)
  const api = await (await page.request.post(`/api/runs/${run.id}/predict`, { data: { rows: [row] } })).json()
  await expect(tp.getByTestId('tp-class')).toHaveText(String(api.predictions[0]))
  for (const [c, p] of Object.entries(api.probabilities[0] as Record<string, number>)) await expect(tp.getByTestId(`tp-prob-${c}`)).toContainText(`${(p * 100).toFixed(1)}%`)
  expect(errors).toEqual([])
})

test('regression: the loan-amount sample, residuals, custom ensemble, a predicted value', async ({ page, errors }) => {
  test.setTimeout(420_000)
  await open(page, 'quick-train')
  await expect(page.getByTestId('dataset-profile')).toHaveAttribute('data-source', 'api')
  await page.getByTestId('dataset-list').getByTestId('dataset-loan-amount').click()
  await expect(page.getByTestId('profile-name')).toHaveText('loan_amount_sample.csv')
  await expect(page.locator('.valid')).toContainText('target loan_amount_usd · regression')
  await expect(page.getByTestId('profile-target-stats')).toBeVisible()
  await throughTheWizard(page, FAST, { expectTask: 'regression' })
  const run = await raceToTheEnd(page)
  expect(run.problem_type).toBe('regression')
  expect(run.eval_metric.name).toBe('root_mean_squared_error')
  await checkResults(page, run)

  // residuals: recomputed in the browser from the holdout predictions, they agree with the API's RMSE
  const res = page.getByTestId('residuals')
  await expect(res).toContainText('Largest misses')
  const preds = await (await page.request.get(`/api/runs/${run.id}/holdout-predictions`)).json()
  const r = preds.y_true.map((y: number, i: number) => y - preds.y_pred[i])
  const rmse = Math.sqrt(r.reduce((s: number, v: number) => s + v * v, 0) / r.length)
  expect(Math.abs(rmse - run.holdout.metrics.root_mean_squared_error) / rmse).toBeLessThan(1e-6)
  const mean = r.reduce((s: number, v: number) => s + v, 0) / r.length
  await expect(page.locator('.plot-frame').filter({ hasText: 'Residuals' })).toContainText(`r̄=${fmtD(mean, 2)}`)

  const base = (run.leaderboard as Lb[]).filter((x) => !x.model.startsWith('WeightedEnsemble')).map((x) => x.model)
  const ce = await customEnsemble(page, run.id, base)
  expect(ce.greater_is_better).toBe(false)

  const tp = page.getByTestId('try-prediction')
  await tp.getByTestId('tp-predict').click()
  await expect(tp.getByTestId('tp-value')).toBeVisible({ timeout: 60_000 })
  const row = await examplesRow(page, run.dataset_id, run.data.features)
  const api = await (await page.request.post(`/api/runs/${run.id}/predict`, { data: { rows: [row] } })).json()
  expect(api.probabilities).toBeNull()
  const v = api.predictions[0] as number
  await expect(tp.getByTestId('tp-value')).toHaveText(Math.abs(v) >= 10_000 ? v.toLocaleString('en-US', { maximumFractionDigits: 0 }) : v.toLocaleString('en-US', { maximumFractionDigits: 1 }))
  await expect(tp).toContainText('1.96·σᵣ')
  expect(errors).toEqual([])
})

test('text: support tickets with the TF-IDF models, top words per class', async ({ page, errors }) => {
  test.setTimeout(480_000)
  await open(page, 'quick-train')
  await expect(page.getByTestId('profile-name')).toHaveText('support_tickets_sample.csv')
  await expect(page.locator('.valid')).toContainText('target category · K = 6')
  // TF-IDF models are added to these hyperparameters for the text columns
  await throughTheWizard(page, { hyperparameters: { GBM: { num_boost_round: 30 } } }, { expectTask: 'multiclass' })
  const run = await raceToTheEnd(page)
  const tfidf = (run.leaderboard as Lb[]).filter((r) => r.family === 'TFIDF_LR' || r.family === 'TFIDF_SVM')
  expect(tfidf.length).toBeGreaterThanOrEqual(1)
  for (const r of tfidf) await expect(page.getByTestId(`race-model-${r.model}`)).toContainText('TF-IDF')
  await checkResults(page, run)
  const tw = page.getByTestId('top-words')
  await expect(tw).toBeVisible()
  const model = Object.keys(run.text_models)[0]
  for (const [cls, words] of Object.entries(run.text_models[model] as Record<string, [string, number][]>)) {
    const card = tw.getByTestId(`top-words-${cls}`)
    await expect(card).toContainText(words[0][0])
    await expect(card).toContainText(words[0][1].toFixed(2))
  }
  await expect(page.getByTestId('confusion')).toContainText('Security')
  // the model card shows the family's objective from the catalogue
  await page.getByTestId(`lb-row-${tfidf[0].model}`).click()
  const fams = (await (await page.request.get('/api/models')).json()).families as { key: string; objective: string }[]
  await expect(page.getByTestId('model-objective')).toHaveText(fams.find((f) => f.key === tfidf[0].family)!.objective)
  // a text area for text in the prediction form
  await expect(page.getByTestId('tp-body')).toHaveJSProperty('tagName', 'TEXTAREA')
  expect(errors).toEqual([])
})

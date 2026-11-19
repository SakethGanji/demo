// Experiments and Explain in live mode, on real AutoGluon runs created over HTTP with small fast configs. Every number
// checked on screen is compared with what the API returns (the API's numbers are checked against scikit-learn,
// fairlearn and shap in backend/tests/test_understand.py).
import type { APIRequestContext, Page } from '@playwright/test'
import { expect, open, test } from './fixtures'

const fmtD = (v: number, d: number) => {
  const a = Math.abs(v)
  if (a >= 10_000) return v.toLocaleString('en-US', { maximumFractionDigits: 0 })
  if (a >= 100) return v.toLocaleString('en-US', { maximumFractionDigits: 1 })
  if (a >= 10) return v.toFixed(2)
  return v.toFixed(d)
}
const fmt = (v: number) => fmtD(v, 4)

type Run = {
  id: string; status: string; best_model: string; positive_class: string; problem_type: string; error: string | null
  holdout: { metrics: Record<string, number> }; eval_metric: { name: string }
  threshold: { value: number; source: string; metric: string | null } | null
}

async function train(request: APIRequestContext, body: Record<string, unknown>): Promise<Run> {
  const r = await request.post('/api/runs', { data: { time_limit: 60, feature_importance: false, ...body } })
  expect(r.status()).toBe(201)
  const { id } = await r.json()
  for (let i = 0; i < 600; i++) {
    const run = await (await request.get(`/api/runs/${id}`)).json()
    if (['succeeded', 'failed', 'cancelled'].includes(run.status)) { expect(run.status, run.error).toBe('succeeded'); return run }
    await new Promise((res) => setTimeout(res, 500))
  }
  throw new Error(`run ${id} did not finish`)
}

const CREDIT_FAST = { dataset_id: 'credit-default', eval_metric: 'roc_auc', fit_kwargs: { hyperparameters: { GBM: { num_boost_round: 60 }, LR: {} } } }
const get = async (page: Page, path: string) => (await page.request.get(path)).json()

test('explain: curves, the decision threshold, importance, SHAP, errors and fairness match the API', async ({ page, errors }) => {
  test.setTimeout(420_000)
  const run = await train(page.request, { ...CREDIT_FAST, calibrate_threshold: 'f1' })
  expect(run.threshold!.source).toBe('calibrated')
  const t = run.threshold!.value

  await open(page, `models?run=${run.id}&tab=Explain`)
  await expect(page.getByTestId('explain')).toHaveAttribute('data-run', run.id)
  await expect(page.getByTestId('explain-primary')).toHaveText(fmt(run.holdout.metrics.roc_auc))
  await expect(page.getByTestId('explain-threshold')).toHaveText(t.toFixed(3))
  // Performance: ROC / PR / calibration with the API's AUC, AP and Brier; confusion matrix at t
  const ex = await get(page, `/api/runs/${run.id}/explain`)
  const pos = String(run.positive_class)
  await expect(page.getByTestId('roc-plot')).toBeVisible()
  await expect(page.locator('.plot-frame', { hasText: 'ROC Curve' })).toContainText(`AUC ${fmt(ex.roc[pos].auc)}`)
  await expect(page.locator('.plot-frame', { hasText: 'Precision–Recall' })).toContainText(`AP ${fmt(ex.pr[pos].average_precision)}`)
  await expect(page.locator('.plot-frame', { hasText: 'Calibration' })).toContainText(`Brier=${fmt(ex.calibration.brier)}`)
  const cm = page.getByTestId('explain-confusion')
  for (const row of ex.confusion_matrix as number[][]) for (const v of row) await expect(cm).toContainText(String(v))

  // Threshold: the readout equals the API's analysis at the run's t; moving the slider reads the API's grid
  await page.getByTestId('tab-group-Advanced').click() // G11: Threshold is under Explain → Advanced
  await page.getByRole('tab', { name: 'Threshold' }).click()
  const a = await get(page, `/api/runs/${run.id}/threshold?cost_fp=1&cost_fn=5`)
  await expect(page.getByTestId('thr-t')).toHaveText(t.toFixed(3))
  await expect(page.getByTestId('thr-f1')).toHaveText(a.current.f1.toFixed(4))
  await expect(page.getByTestId('thr-tp')).toHaveText(String(a.current.tp))
  await expect(page.getByTestId('thr-cost')).toHaveText(a.current.cost.toLocaleString('en-US'))
  await page.getByTestId('thr-slider').fill('0.3')
  await expect(page.getByTestId('thr-t')).toHaveText('0.300')
  const at = (await get(page, `/api/runs/${run.id}/threshold?cost_fp=1&cost_fn=5&t=0.3`)).at
  for (const k of ['precision', 'recall', 'f1', 'mcc'] as const) await expect(page.getByTestId(`thr-${k}`)).toHaveText(at[k].toFixed(4))
  await expect(page.getByTestId('thr-bacc')).toHaveText(at.balanced_accuracy.toFixed(4))
  await expect(page.getByTestId('thr-fp')).toHaveText(String(at.fp))
  await expect(page.getByTestId('thr-fn')).toHaveText(String(at.fn))
  // "Use t" persists it in the predictor; the run and every later prediction use it
  await page.getByTestId('thr-use').click()
  await expect(page.getByTestId('thr-msg')).toContainText('saved in the predictor')
  expect((await get(page, `/api/runs/${run.id}`)).threshold.value).toBe(0.3)
  await expect(page.getByTestId('explain-threshold')).toHaveText('0.300')
  // "Calibrate threshold" runs AutoGluon's calibrate_decision_threshold and keeps it
  await page.getByTestId('thr-calibrate').click()
  await expect(page.getByTestId('thr-msg')).toContainText('calibrated for f1')
  const after = await get(page, `/api/runs/${run.id}`)
  expect(after.threshold.source).toBe('calibrated')
  await expect(page.getByTestId('explain-threshold')).toHaveText(after.threshold.value.toFixed(3))

  // Features & SHAP: permutation importance with its intervals, and a waterfall that adds up to f(x)
  await page.getByTestId('tab-group-Explain').click()
  await page.getByRole('tab', { name: 'Features & SHAP' }).click()
  await expect(page.getByTestId('importance')).toBeVisible({ timeout: 180_000 })
  const fi = await get(page, `/api/runs/${run.id}/feature-importance`)
  for (const f of fi.features.slice(0, 4)) await expect(page.getByTestId(`fi-${f.feature}`)).toContainText(fmt(f.importance))
  await expect(page.getByTestId('shap-waterfall')).toBeVisible({ timeout: 120_000 })
  const row = Number(await page.getByTestId('shap-row').inputValue())
  const s = await get(page, `/api/runs/${run.id}/shap?row=${row}`)
  await expect(page.getByTestId('shap-fx')).toHaveText(s.fx.toFixed(4))
  await expect(page.getByTestId('shap-base')).toHaveText(s.base_value.toFixed(4))
  const top = s.contributions[0]
  await expect(page.getByTestId(`shap-${top.feature}`)).toContainText(Math.abs(top.phi).toFixed(4))
  expect(Math.abs(s.fx - s.base_value - s.contributions.reduce((acc: number, c: { phi: number }) => acc + c.phi, 0))).toBeLessThan(1e-9)

  // Errors: the most confident mistakes, as the API ranks them; "why?" opens SHAP for that row
  await page.getByRole('tab', { name: 'Errors' }).click()
  const ex2 = await get(page, `/api/runs/${run.id}/explain`)
  const worst = page.getByTestId('worst-errors')
  await expect(worst.locator('tbody tr').first()).toContainText(String(ex2.worst[0].row))
  await expect(worst.locator('tbody tr').first()).toContainText(ex2.worst[0].p_true.toFixed(4))
  await page.getByTestId(`why-${ex2.worst[0].row}`).click()
  await expect(page.getByTestId('shap-row')).toHaveValue(String(ex2.worst[0].row), { timeout: 30_000 })
  await expect(page.getByTestId('shap-waterfall')).toBeVisible({ timeout: 120_000 })

  // Fairness: fairlearn's gaps by a chosen group column
  await page.getByRole('tab', { name: 'Fairness' }).click()
  await page.getByTestId('fair-group').selectOption('region')
  const f = await get(page, `/api/runs/${run.id}/fairness?group=region`)
  await expect(page.getByTestId('fair-dpd')).toHaveText(fmt(f.gaps.demographic_parity_difference))
  await expect(page.getByTestId('fair-eod')).toHaveText(fmt(f.gaps.equalized_odds_difference))
  for (const g of f.by_group) await expect(page.getByTestId('fair-table')).toContainText(g.group)

  // From Explain to the run's results in New Model
  await page.getByRole('button', { name: 'Results →' }).click()
  await expect(page.getByTestId('results')).toHaveAttribute('data-run', run.id)
  await expect(page.getByTestId('result-threshold')).toContainText(after.threshold.value.toFixed(3))
  await page.getByTestId('results-explain').click()
  await expect(page.getByTestId('explain')).toHaveAttribute('data-run', run.id)
  expect(errors).toEqual([])
})

test('models: real runs, filters, parallel coordinates, comparison and parameter diff', async ({ page, errors }) => {
  test.setTimeout(420_000)
  const a = await train(page.request, { ...CREDIT_FAST, calibrate_threshold: 'f1' })
  const b = await train(page.request, { dataset_id: 'credit-default', eval_metric: 'roc_auc', time_limit: 90, fit_kwargs: { hyperparameters: { GBM: { num_boost_round: 120 }, KNN: {} } } })
  const c = await train(page.request, { dataset_id: 'loan-amount', fit_kwargs: { hyperparameters: { GBM: { num_boost_round: 60 }, LR: {} } } })

  await open(page, 'models')
  await expect(page.getByTestId('experiments-live')).toBeVisible()
  for (const r of [a, b, c]) {
    await expect(page.getByTestId(`exp-row-${r.id}`)).toBeVisible()
    await expect(page.getByTestId(`exp-score-${r.id}`)).toHaveText(fmt(r.holdout.metrics[r.eval_metric.name]))
  }
  // filters: problem type, then a score floor in the run's own units
  await page.getByTestId('exp-type-regression').click()
  await expect(page.getByTestId(`exp-row-${c.id}`)).toBeVisible()
  await expect(page.getByTestId(`exp-row-${a.id}`)).toHaveCount(0)
  await page.getByTestId('exp-type-all').click()
  await page.getByTestId('exp-filter-q').fill(a.id)
  await expect(page.locator('[data-testid^=exp-row-]')).toHaveCount(1)
  await page.getByTestId('exp-filter-q').fill('')

  // select exactly a and b
  const rows = page.locator('[data-testid^=exp-row-]')
  for (let i = 0; i < await rows.count(); i++) {
    const id = (await rows.nth(i).getAttribute('data-testid'))!.replace('exp-row-', '')
    await rows.nth(i).locator('input[type=checkbox]').setChecked(id === a.id || id === b.id)
  }
  await expect(page.getByTestId('exp-summary')).toContainText('2 selected')
  // parallel coordinates of the credit-default runs: a string per run, the selected ones in the legend
  const pc = page.locator('.plot-frame', { has: page.getByTestId('run-pc') })
  await expect(pc).toContainText(a.id)
  await expect(pc).toContainText(b.id)
  await expect(pc).toContainText('credit-default')
  expect(await page.getByTestId('run-pc').locator('path.pc-line').count()).toBeGreaterThanOrEqual(2)

  // compare: the API's holdout metrics per run, ROC overlays
  await page.getByRole('tab', { name: 'Compare' }).click()
  for (const r of [a, b]) for (const k of ['roc_auc', 'f1', 'balanced_accuracy']) await expect(page.getByTestId(`cmp-${k}-${r.id}`)).toContainText(fmt(r.holdout.metrics[k]))
  await expect(page.getByTestId('compare-roc')).toBeVisible()
  await expect(page.getByTestId('compare-families')).toBeVisible()

  // parameter diff: the resolved fit calls side by side, differences only
  await page.getByRole('tab', { name: 'Parameter Diff' }).click()
  const diff = page.getByTestId('param-diff')
  await expect(diff.getByTestId('diff-calibrate_threshold')).toContainText('f1')
  await expect(diff.getByTestId('diff-calibrate_threshold')).toContainText('None')
  await expect(diff.getByTestId('diff-time_limit')).toContainText('90')
  await expect(diff.getByTestId('diff-hyperparameters.GBM.num_boost_round')).toContainText('120')
  await expect(diff.getByTestId('diff-threshold.source')).toContainText('calibrated')

  // Explain one run from the table
  await page.getByRole('tab', { name: 'Runs' }).click()
  await page.getByTestId(`exp-explain-${c.id}`).click()
  await expect(page.getByTestId('explain')).toHaveAttribute('data-run', c.id)
  await expect(page.getByTestId('resid-scatter')).toBeVisible()
  const ex = await get(page, `/api/runs/${c.id}/explain`)
  await expect(page.locator('.explain-hero')).toContainText(fmt(ex.rmse))
  expect(errors).toEqual([])
})

test('status bar shows the real machine, versions and the active run', async ({ page, errors }) => {
  test.setTimeout(240_000)
  await open(page, 'models')
  const st = await get(page, '/api/status')
  await expect(page.getByTestId('sb-cpu')).toContainText(`${st.cpu_count} CPUs`)
  await expect(page.getByTestId('sb-version')).toHaveText(`ENSEMBLE ${st.version}`)
  await expect(page.getByTestId('sb-engine')).toHaveText(`AutoGluon ${st.engine.version}`)
  await expect(page.getByTestId('cluster')).toContainText(`cpu × ${st.cpu_count}`)
  await expect(page.getByTestId('sidebar-who')).toContainText('Local workspace')
  const footer = await page.locator('.statusbar').innerText()
  for (const fake of ['Maya Lin', 'run_8a10', 'fold 3 of 5', 'class_weight', 'us-east-1', 'v3.0.0-rc1']) expect(footer).not.toContain(fake)
  // a run in flight appears in the status bar with its progress
  const r = await page.request.post('/api/runs', { data: { ...CREDIT_FAST, time_limit: 60, feature_importance: false, fit_kwargs: { hyperparameters: { GBM: { num_boost_round: 2000 }, LR: {}, KNN: {} } } } })
  const { id } = await r.json()
  await expect(page.getByTestId('sb-active')).toContainText(id, { timeout: 180_000 })
  await expect(page.getByTestId('sb-active')).toContainText('models')
  await page.request.post(`/api/runs/${id}/cancel`)
  await expect(page.getByTestId('sb-active')).not.toContainText(id, { timeout: 30_000 })
  expect(errors).toEqual([])
})

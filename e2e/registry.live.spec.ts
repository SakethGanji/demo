// Registry and use in live mode (S5): register the winner of a real AutoGluon run, approve it (four eyes), promote
// it, read its model card, Try It with SHAP reasons, batch-score a CSV with a download, and see PSI drift. Every number
// checked on screen is compared with the API's (the API's own numbers are checked independently in
// backend/tests/test_registry.py).
import { readFileSync } from 'node:fs'
import type { APIRequestContext } from '@playwright/test'
import { expect, open, test } from './fixtures'

type Run = { id: string; status: string; error: string | null; best_model: string; positive_class: string }

async function train(request: APIRequestContext, body: Record<string, unknown>): Promise<Run> {
  const r = await request.post('/api/runs', { data: { time_limit: 60, feature_importance: true, ...body } })
  expect(r.status()).toBe(201)
  const { id } = await r.json()
  for (let i = 0; i < 600; i++) {
    const run = await (await request.get(`/api/runs/${id}`)).json()
    if (['succeeded', 'failed', 'cancelled'].includes(run.status)) { expect(run.status, run.error).toBe('succeeded'); return run }
    await new Promise((res) => setTimeout(res, 500))
  }
  throw new Error(`run ${id} did not finish`)
}

/** A new month of applicants, made from the dataset's profile (deterministic): utilization pushed far up. */
function batchCsv(profile: { columns: { name: string; type: string; stats?: { mean: number; std: number; min: number; max: number }; top?: { value: string }[] }[] }, n = 400): string {
  let s = 7
  const rnd = () => { s = (s * 16807) % 2147483647; return s / 2147483647 }
  const cols = profile.columns.filter((c) => c.name !== 'defaulted')
  const rows = [cols.map((c) => c.name).join(',')]
  for (let i = 0; i < n; i++) {
    rows.push(cols.map((c) => {
      if (c.type === 'id') return `B-${i}`
      if (c.type === 'number' && c.stats) {
        const z = (rnd() + rnd() + rnd() - 1.5) * 2
        let v = c.stats.mean + z * c.stats.std
        if (c.name === 'utilization') v = c.stats.mean + 2.5 * c.stats.std + Math.abs(z) * 0.1
        v = Math.min(c.stats.max * 1.5, Math.max(c.stats.min, v))
        return Number.isInteger(c.stats.min) && Number.isInteger(c.stats.max) && c.stats.max > 20 ? Math.round(v) : v.toFixed(3)
      }
      const top = c.top ?? []
      return top.length ? top[Math.floor(rnd() * top.length)].value : ''
    }).join(','))
  }
  return rows.join('\n') + '\n'
}

test('registry: register the winner, approve, promote, card, Try It, batch score with a download, drift', async ({ page, errors }) => {
  test.setTimeout(600_000)
  const run = await train(page.request, { dataset_id: 'credit-default', eval_metric: 'roc_auc', fit_kwargs: { hyperparameters: { GBM: { num_boost_round: 60 }, LR: {} } } })

  // Register from the Experiments row
  await open(page, 'experiments')
  await page.getByTestId(`exp-register-${run.id}`).click()
  await expect(page.getByTestId('register-panel')).toBeVisible()
  await page.getByTestId('actor').fill('Dana Dev')
  await expect(page.getByTestId('frontier')).toBeVisible({ timeout: 120_000 })
  const lp = await (await page.request.get(`/api/runs/${run.id}/latency`)).json()
  for (const m of lp.models) await expect(page.getByTestId(`frontier-${m.model}`)).toBeVisible()
  await expect(page.getByTestId('register-run')).toHaveValue(run.id)
  await page.getByTestId('opt-refit').check()
  await expect(page.getByTestId('register-call')).toContainText("refit_full(model='best')")
  await page.getByTestId('register-desc').fill('e2e candidate')
  await page.getByTestId('register-submit').click()
  const detail = page.getByTestId('version-detail')
  await expect(detail).toBeVisible()
  await expect(detail).toHaveAttribute('data-status', 'ready', { timeout: 300_000 })
  const vid = (await detail.getAttribute('data-version'))!
  const v = await (await page.request.get(`/api/registry/versions/${vid}`)).json()
  expect(v.created_by).toBe('Dana Dev')
  expect(v.result.version.best_model).toBe(`${run.best_model}_FULL`)
  // The measured trade-off on screen is the API's
  const r = v.result
  await expect(page.getByTestId('speedup')).toHaveText(`${(r.source.latency.us_per_row_p50 / r.version.latency.us_per_row_p50).toFixed(2)}×`)
  await expect(page.getByTestId('size-ratio')).toHaveText((r.version.size_bytes / r.source.size_bytes).toFixed(3))
  await expect(page.getByTestId('prov-model')).toHaveText(v.model_hash)
  for (const step of ['clone', 'refit_full', 'deploy_copy', 'measure', 'hash', 'mlflow']) await expect(page.getByTestId(`step-${step}`)).toHaveAttribute('data-status', 'done')

  // Four eyes: the registrant cannot approve; a reviewer can; then promote to Production
  await expect(page.getByTestId('approve')).toBeDisabled()
  await expect(page.getByTestId('to-Production')).toBeDisabled()
  await page.getByTestId('actor').fill('Riley Review')
  await page.getByTestId('review-comment').fill('AUC interval clears the floor; card reviewed')
  await page.getByTestId('approve').click()
  await expect(page.getByTestId('review-done')).toContainText('approved')
  await page.getByTestId('to-Production').click()
  await expect(page.getByTestId(`version-${vid}`)).toHaveAttribute('data-stage', 'Production')
  await expect(page.getByTestId('kpi-production')).not.toHaveText('0')
  const audit = page.getByTestId('audit-log')
  await expect(audit).toContainText('Riley Review')
  await expect(audit.locator('tr[data-action=approved]').first()).toContainText('AUC interval clears the floor')
  const entries = (await (await page.request.get(`/api/registry/audit?version_id=${vid}`)).json()).entries
  expect(entries.map((e: { action: string }) => e.action).reverse()).toEqual(['registered', 'approved', 'stage_changed'])
  await expect(page.getByTestId('sb-registry')).toContainText('in production')

  // The model card, and its Markdown download
  await page.getByRole('tab', { name: 'Model Card' }).click()
  await expect(page.getByTestId('card-disclaimer')).toContainText('does not replace independent model validation')
  await expect(page.getByTestId('card-validation')).toContainText('roc_auc')
  const card = await (await page.request.get(`/api/registry/versions/${vid}/card`)).json()
  const auc = card.validation.holdout_ci.metrics.roc_auc
  await expect(page.getByTestId('card-metric-roc_auc')).toContainText(auc.value.toFixed(4))
  await page.getByTestId('card-purpose').fill('Flag likely defaults for manual review')
  await page.getByTestId('card-save').click()
  await expect(page.locator('[data-testid=card-use]')).toContainText('saved: purpose')
  const md = await (await page.request.get(`/api/registry/versions/${vid}/card?format=md&download=true`)).text()
  expect(md).toContain('Flag likely defaults for manual review')
  expect(md).toContain(v.model_hash)

  // Try It: the prediction and the SHAP reasons equal the API's for the row the form sent
  await page.getByRole('tab', { name: 'Try It' }).click()
  const [req] = await Promise.all([page.waitForRequest((q) => q.url().includes(`/registry/versions/${vid}/predict`)), page.getByTestId('try-predict').click()])
  await expect(page.getByTestId('try-reasons')).toBeVisible({ timeout: 120_000 })
  const sent = req.postDataJSON()
  expect(Object.keys(sent.row).length).toBeGreaterThan(8) // the form sent the profile's example, not an empty row
  const api = await (await page.request.post(`/api/registry/versions/${vid}/predict`, { data: sent })).json()
  await expect(page.getByTestId('try-class')).toHaveText(String(api.prediction))
  await expect(page.getByTestId('try-p')).toHaveText(api.probabilities[String(run.positive_class)].toFixed(4))
  await expect(page.getByTestId('try-shap-fx')).toHaveText(api.shap.fx.toFixed(4))
  await expect(page.getByTestId('reason-0')).toContainText(api.shap.reasons[0].feature)
  expect(Math.abs(api.shap.base_value + api.shap.contributions.reduce((a: number, c: { phi: number }) => a + c.phi, 0) - api.shap.fx)).toBeLessThan(1e-6)

  // Batch scoring: upload, progress, preview, download, drift
  await page.getByRole('tab', { name: 'Batch & Drift' }).click()
  const ds = await (await page.request.get('/api/datasets/credit-default')).json()
  const csv = batchCsv(ds.profile)
  await page.getByTestId('batch-file').setInputFiles({ name: 'applicants_2026_11.csv', mimeType: 'text/csv', buffer: Buffer.from(csv) })
  await page.getByTestId('batch-submit').click()
  const res = page.getByTestId('batch-result')
  await expect(res).toBeVisible({ timeout: 180_000 })
  await expect(page.getByTestId('batch-rows')).toHaveText('400')
  const bid = (await res.getAttribute('data-batch'))!
  const b = await (await page.request.get(`/api/registry/batches/${bid}`)).json()
  const d = b.result.drift
  for (const f of d.features) {
    if (f.psi == null) continue
    await expect(page.getByTestId(`psi-value-${f.feature}`)).toHaveText(f.psi.toFixed(4))
    await expect(page.getByTestId(`psi-${f.feature}`)).toHaveAttribute('data-band', f.band)
  }
  expect(d.features.find((f: { feature: string }) => f.feature === 'utilization').band).toBe('shift')
  await expect(page.getByTestId('psi-utilization')).toHaveAttribute('data-band', 'shift')
  await expect(page.getByTestId('batch-max-psi')).toHaveText(d.max_psi.toFixed(4))
  await expect(page.getByTestId('batch-pred-psi')).toHaveText(d.prediction.psi.toFixed(4))
  await expect(page.getByTestId('batch-preview')).toContainText('p_Yes')
  const [dl] = await Promise.all([page.waitForEvent('download'), page.getByTestId('batch-download').click()])
  expect(dl.suggestedFilename()).toContain('applicants_2026_11_scored')
  const text = readFileSync((await dl.path())!, 'utf8').trim().split('\n')
  expect(text.length).toBe(401)
  expect(text[0].split(',').slice(-3)).toEqual(['prediction', 'p_No', 'p_Yes'])
  await expect(page.getByTestId('audit-log').locator('tr[data-action=batch_scored]').first()).toContainText('applicants_2026_11.csv')
  expect(errors).toEqual([])
})

test('home: real runs, best scores, registry and the monitor; no demo identities in live mode', async ({ page, errors }) => {
  test.setTimeout(300_000)
  const run = await train(page.request, { dataset_id: 'loan-amount', feature_importance: false, fit_kwargs: { hyperparameters: { GBM: { num_boost_round: 40 } } } })
  await open(page, 'home')
  await expect(page.getByTestId('home-live')).toBeVisible()
  await expect(page.getByTestId('home-runs')).toContainText(run.id)
  const st = await (await page.request.get('/api/status')).json()
  await expect(page.getByTestId('home-read')).toContainText(`${st.runs} runs`)
  await expect(page.getByTestId('home-read')).toContainText(`${st.registry.stages.Production} in Production`)
  await expect(page.getByTestId('home-monitor')).toBeVisible({ timeout: 30_000 })
  await expect(page.getByTestId('home-best')).toContainText('Loan amount')
  const text = await page.locator('body').innerText()
  for (const fake of ['Maya Lin', 'Arun Patel', 'run_8a10', 'run_894a', 'v2.4.1', 'v3.0.0-rc1', '9 runs · 3 selected', 'zendesk']) expect(text, fake).not.toContain(fake)
  // Registry page in live mode has no scripted versions either
  await open(page, 'registry')
  await expect(page.getByTestId('registry')).toBeVisible()
  const t2 = await page.locator('.workspace').innerText()
  for (const fake of ['Maya Lin', 'Arun Patel', 'v2.4.1', 'zendesk', 'SIMULATED']) expect(t2, fake).not.toContain(fake)
  // Prototype pages say so in live mode
  await open(page, 'gym')
  await expect(page.getByTestId('prototype-banner')).toContainText('SIMULATED')
  expect(errors).toEqual([])
})

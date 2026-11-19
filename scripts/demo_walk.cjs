// F20: walk docs/demo-script.md (the 15-minute credit-risk story) click by click in a headless browser.
// Not a test suite: one pass through the screens the presenter clicks, a screenshot at each stop, and a check that
// every number the script promises is on screen (within run-to-run noise). Prints a table and writes results.json.
//
//   node scripts/demo_walk.cjs [ui_url] [out_dir]     (defaults: http://localhost:5190, docs/progress/F20)
// Needs `make dev` on a fresh workbench (`make reset` first), as the script's "Before the audience arrives" says.
const { chromium } = require('playwright')
const { execFileSync } = require('child_process')
const fs = require('fs')
const path = require('path')

const UI = process.argv[2] || 'http://localhost:5190'
const ROOT = path.join(__dirname, '..')
const OUT = process.argv[3] || path.join(ROOT, 'docs', 'progress', 'F20')
const PY = path.join(ROOT, 'backend', '.venv', 'bin', 'python')
const log = (...a) => console.log(new Date().toISOString().slice(11, 19), ...a)

const checks = []
const errors = []
// every number in a text, with thousands separators and Unicode minus normalised
const nums = (t) => (t.replace(/−/g, '-').replace(/(\d),(\d{3})/g, '$1$2').match(/[-+]?\d+(?:\.\d+)?/g) || []).map(Number)
function expectNum(step, what, text, want, tol) {
  const got = nums(text || '')
  const hit = got.find((x) => Math.abs(x - want) <= tol)
  checks.push({ step, what, want, tol, ok: hit !== undefined, seen: hit !== undefined ? hit : got.slice(0, 12).join(' ') })
  log(`  ${hit !== undefined ? '✓' : '✗'} ${what}: want ${want} ±${tol} · ${hit !== undefined ? `on screen ${hit}` : `not found in [${got.slice(0, 12).join(' ')}]`}`)
}
function expectText(step, what, text, re) {
  const ok = re.test(text || '')
  checks.push({ step, what, want: String(re), ok, seen: ok ? (text.match(re) || [''])[0] : (text || '').slice(0, 160) })
  log(`  ${ok ? '✓' : '✗'} ${what}: ${ok ? (text.match(re) || [''])[0] : `no ${re} in "${(text || '').slice(0, 160)}"`}`)
}

async function main() {
  fs.mkdirSync(OUT, { recursive: true })
  const browser = await chromium.launch()
  const ctx = await browser.newContext({ viewport: { width: 1920, height: 1080 }, deviceScaleFactor: 1 })
  const page = await ctx.newPage()
  page.on('pageerror', (e) => errors.push(`pageerror: ${e.message}`))
  page.on('console', (m) => { if (m.type() === 'error') errors.push(`console: ${m.text()}`) })
  const shot = async (name) => { await page.waitForTimeout(600); const p = path.join(OUT, `${name}.png`); await page.screenshot({ path: p }); log('  ▸', path.relative(ROOT, p)) }
  const go = async (route) => { await page.goto(`${UI}/#/${route}`, { waitUntil: 'networkidle' }); await page.waitForTimeout(800) }
  const api = async (p, opts) => { const r = opts ? await page.request.post(`${UI}${p}`, { data: opts }) : await page.request.get(`${UI}${p}`); return r.json() }
  const text = async (tid) => (await page.getByTestId(tid).first().innerText()).trim()
  const actor = async (name) => { await page.getByTestId('actor').fill(name); await page.waitForTimeout(300) }
  const ok = (r) => document.querySelector('[data-testid=race]')?.getAttribute('data-status') === r
  const steps = []
  const step = async (name, fn) => {
    const t0 = Date.now()
    log(`── ${name}`)
    try { await fn(); steps.push({ name, ok: true, s: Math.round((Date.now() - t0) / 1000) }) } catch (e) {
      steps.push({ name, ok: false, s: Math.round((Date.now() - t0) / 1000), error: String(e.message || e).slice(0, 400) })
      log(`  ✗ ${String(e.message || e).slice(0, 400)}`)
      await shot(`fail-${name.replace(/[^a-z0-9]+/gi, '-').toLowerCase()}`).catch(() => {})
    }
  }

  // New Model, as the script clicks it: sample → target → confirm → (ignore columns) → split → goal → advanced → start
  const newModel = async ({ ds, label, ignore = [], split, metric, fit, tag, shots = false }) => {
    await go('quick-train')
    // the wizard remembers the last run: "Train Another", then back to the Dataset step
    const again = page.getByRole('button', { name: 'Train Another' })
    if (await again.count()) await again.click()
    if (!(await page.getByTestId(`dataset-${ds}`).count())) await page.getByTestId('step-dataset').click().catch(() => {})
    await page.getByTestId(`dataset-${ds}`).click()
    await page.getByTestId('dataset-profile').waitFor({ timeout: 60_000 })
    await page.getByRole('button', { name: 'Continue →' }).click()
    await page.getByTestId('label-select').selectOption(label)
    // a sample names its target on upload (already confirmed); an uploaded CSV needs the explicit confirm (F4)
    await page.getByTestId('confirm-target').waitFor({ timeout: 60_000 })
    const cb = page.getByTestId('confirm-target-btn')
    if (await cb.count()) await cb.click()
    await page.getByTestId('confirm-target').and(page.locator('[data-confirmed="yes"]')).waitFor({ timeout: 60_000 })
    for (const c of ignore) await page.getByTestId(`coltype-${c}`).selectOption('id')
    await page.getByTestId('split-card').waitFor({ timeout: 30_000 })
    await page.waitForTimeout(1200)
    if (shots) { await page.getByTestId('split-card').scrollIntoViewIfNeeded(); await shot(`${tag}-split-random-warning`) }
    const warn = shots ? await text('split-warning').catch(() => '') : ''
    if (split) {
      await page.getByTestId(`split-${split}`).click()
      await page.getByTestId('split-card').and(page.locator(`[data-strategy="${split}"]`)).waitFor()
      await page.getByTestId('split-timeline').waitFor({ timeout: 30_000 }).catch(() => {})
      await page.waitForTimeout(1500)
      if (shots) { await page.getByTestId('split-card').scrollIntoViewIfNeeded(); await shot(`${tag}-split-oot`) }
    }
    const splitText = await text('split-card')
    await page.getByTestId('target-continue').click()
    if (metric) await page.getByTestId(`metric-${metric}`).click()
    await page.getByTestId('advanced').locator('.adv-toggle').click()
    await page.getByTestId('fit-kwargs').fill(JSON.stringify(fit))
    await page.getByTestId('resolved-call').filter({ hasText: 'hyperparameters=' }).waitFor()
    await page.getByTestId('start-training').click()
    await page.getByTestId('race').waitFor({ timeout: 30_000 })
    await page.waitForFunction(() => ['succeeded', 'failed'].includes(document.querySelector('[data-testid=race]')?.getAttribute('data-status')), null, { timeout: 900_000 })
    if (!(await page.evaluate(ok, 'succeeded'))) throw new Error(`${tag}: run did not succeed`)
    const runId = (await text('race-run-id'))
    await page.getByTestId('see-results').click()
    await page.getByTestId('results').waitFor({ timeout: 60_000 })
    await page.waitForTimeout(1500)
    return { runId, warn, splitText }
  }
  const register = async (runId, name, tag) => {
    await go(`registry?register=${runId}`)
    await actor('Dana Dev')
    await page.getByTestId('register-panel').waitFor({ timeout: 60_000 })
    await page.getByTestId('register-name').fill(name)
    if (tag) await shot(tag)
    await page.getByTestId('register-submit').click()
    await page.waitForFunction(() => ['ready', 'failed'].includes(document.querySelector('[data-testid=version-detail]')?.getAttribute('data-status')), null, { timeout: 900_000 })
    return page.getByTestId('version-detail').getAttribute('data-version')
  }
  const explainTab = async (runId, group, tab) => {
    await go(`explain?run=${runId}`)
    await page.getByTestId(`tab-group-${group}`).click()
    await page.getByRole('tab', { name: tab, exact: true }).click()
    await page.waitForTimeout(1500)
  }
  const versionTab = async (vid, tab) => {
    await go(`registry?version=${vid}`)
    await actor('Dana Dev')
    await page.getByRole('tab', { name: tab, exact: true }).click()
    await page.waitForTimeout(1500)
  }

  // the script: "Close Vela for now"
  await go('home')
  const vclose = page.getByRole('button', { name: 'Close Vela' })
  if (await vclose.count()) await vclose.click().catch(() => {})
  const S = {}
  // ── Before the audience arrives: the production champion and its challenger on loan-stream
  await step('prep: loan-stream champion + challenger, registered, approved, staged', async () => {
    const fit1 = { hyperparameters: { GBM: { num_boost_round: 200 }, LR: {} } }
    const fit2 = { hyperparameters: { XGB: { n_estimators: 200 } } }
    const a = await newModel({ ds: 'loan-stream', label: 'defaulted', split: 'time', metric: 'roc_auc', fit: fit1, tag: 'prep' })
    S.champRun = a.runId
    expectNum('prep', 'champion holdout ROC AUC', await text('result-primary'), 0.7826, 0.003)
    const b = await newModel({ ds: 'loan-stream', label: 'defaulted', split: 'time', metric: 'roc_auc', fit: fit2, tag: 'prep' })
    S.challRun = b.runId
    S.champ = await register(S.champRun, 'loan-stream')
    S.chall = await register(S.challRun, 'loan-stream')
    for (const [vid, stage] of [[S.champ, 'Production'], [S.chall, 'Staging']]) {
      await go(`registry?version=${vid}`)
      await actor('Riley Review')
      await page.getByTestId('review-comment').fill('Out-of-time holdout reviewed')
      await page.getByTestId('approve').click()
      await page.getByTestId('review-done').waitFor()
      await page.getByTestId(`to-${stage}`).click()
      await page.getByTestId(`version-${vid}`).and(page.locator(`[data-stage=${stage}]`)).waitFor({ timeout: 30_000 })
    }
    await shot('00-prep-registry')
  })

  // ── 1. Out of time, not at random
  await step('1 out-of-time vs random', async () => {
    const a = await newModel({ ds: 'credit-default', label: 'defaulted', ignore: ['customer_id', 'application_date'], split: 'time', metric: 'roc_auc',
      fit: { hyperparameters: { GBM: { num_boost_round: 40 }, RF: { n_estimators: 40 }, LR: {} } }, tag: '01', shots: true })
    S.A = a.runId
    expectText('1', 'random-split warning names application_date', a.warn, /'application_date' is a date/)
    expectNum('1', 'rows per customer in the warning', a.warn, 1.49, 0.005)
    expectText('1', 'cut-off 2024-06-20', a.splitText, /2024-06-20/)
    expectNum('1', 'train rows', a.splitText, 7675, 0)
    expectNum('1', 'tuning rows', a.splitText, 1925, 0)
    expectNum('1', 'holdout rows', a.splitText, 2400, 0)
    await shot('02-results-oot')
    expectNum('1', 'holdout ROC AUC (out-of-time)', await text('result-primary'), 0.7150, 0.003)
    await page.getByTestId('split-compare').scrollIntoViewIfNeeded()
    await page.getByTestId('split-twin-random-stratified').click()
    await page.getByTestId('split-compare-random-stratified').waitFor({ timeout: 600_000 })
    await page.getByTestId('split-optimism').waitFor({ timeout: 120_000 })
    await page.waitForTimeout(1500)
    await page.getByTestId('split-compare').scrollIntoViewIfNeeded()
    await shot('03-split-compare')
    expectNum('1', 'random twin ROC AUC', await text('split-compare-random-stratified'), 0.7537, 0.003)
    expectNum('1', 'optimism', await text('split-optimism'), 0.039, 0.002)
  })

  // ── 2. Business cost · 3. Monotone (in the Gym from that run)
  await step('2-3 Gym: cost + monotone, cost per 1,000, twin, monotonicity', async () => {
    await go(`quick-train?run=${S.A}`)
    await page.getByTestId('results-gym').click()
    await page.getByTestId('gym-live').waitFor({ timeout: 30_000 })
    await page.getByTestId('gym-step-3').click()
    await page.getByTestId('gym-cost-on').getByRole('radio', { name: 'on' }).click()
    await page.getByTestId('gym-cost-c_fp').fill('1')
    await page.getByTestId('gym-cost-c_fn').fill('10')
    await page.waitForTimeout(1500)
    expectNum('2', 't* = c_FP/(c_FP+c_FN)', await text('gym-cost'), 0.091, 0.0005)
    expectText('2', 'validation split kept: out-of-time', await text('gym-split'), /out-of-time/i)
    await page.getByTestId('gym-cost').scrollIntoViewIfNeeded()
    await shot('04-gym-cost')
    await page.getByRole('tab', { name: 'Advanced' }).click()
    await page.getByTestId('gym-step-4').click()
    await page.getByTestId('gym-mono-debt_to_income').getByRole('radio', { name: '↑ increasing' }).click()
    await page.getByTestId('gym-step-10').click()
    await page.getByTestId('gym-raw-fit').fill(JSON.stringify({ hyperparameters: { GBM: { num_boost_round: 150 }, XGB: { n_estimators: 150 }, CAT: { iterations: 150 } } }))
    await page.waitForTimeout(2000)
    await page.getByTestId('gym-step-4').click()
    await page.getByTestId('gym-mono-families').waitFor({ timeout: 30_000 })
    const fam = await text('gym-mono-families')
    expectText('3', 'monotone families: LightGBM, XGBoost, CatBoost', fam, /LightGBM.*XGBoost.*CatBoost|GBM_MONO.*XGB_MONO.*CAT_MONO/)
    await page.getByTestId('gym-monotone').scrollIntoViewIfNeeded()
    await shot('05-gym-monotone')
    await page.getByTestId('gym-start').click()
    await page.waitForURL(/#\/quick-train\?run=run_/, { timeout: 60_000 })
    await page.waitForFunction(() => ['succeeded', 'failed'].includes(document.querySelector('[data-testid=race]')?.getAttribute('data-status')), null, { timeout: 900_000 })
    S.B = (await text('race-run-id'))
    await page.getByTestId('see-results').click()
    await page.getByTestId('cost-card').waitFor({ timeout: 60_000 }).catch(() => {})
    await page.getByTestId('cost-compare').waitFor({ timeout: 60_000 })
    await page.getByTestId('cost-compare').scrollIntoViewIfNeeded()
    const cc = await text('cost-compare')
    expectNum('2', 'cost per 1,000 (cost run)', await text('cost-per-1000'), 702.5, 15)
    expectNum('2', 'cost-calibrated threshold t', cc, 0.061, 0.004)
    await page.getByTestId('metric-twin-roc_auc').click()
    await page.getByTestId('cost-delta').waitFor({ timeout: 600_000 })
    await page.waitForTimeout(1500)
    await page.getByTestId('cost-compare').scrollIntoViewIfNeeded()
    await shot('06-cost-compare')
    const cc2 = await text('cost-compare')
    expectNum('2', 'ROC AUC twin per 1,000', cc2, 857.1, 15)
    expectNum('2', 'Δ per 1,000', await text('cost-delta'), 154.6, 15)
    await explainTab(S.B, 'Explain', 'Monotonicity')
    await page.getByTestId('mono-viol').first().waitFor({ timeout: 300_000 })
    await page.waitForTimeout(1000)
    await shot('07-explain-monotonicity')
    expectText('3', '0 of 200 ICE curves violate', await text('mono-viol'), /^0 \/ 200$/)
  })

  // ── 4. Reason codes
  await step('4 register, reason codes, Try It, batch, model card', async () => {
    S.vB = await register(S.B, 'demo.credit-default', '08-register')
    await versionTab(S.vB, 'Reason Codes')
    await page.getByTestId('reason-codes').waitFor({ timeout: 60_000 })
    await shot('09-reason-codes')
    // the first adverse holdout row (the presenter's prepared applicant), and the first 400 holdout rows as a CSV
    const rowsJson = path.join(OUT, '.holdout.json')
    execFileSync(PY, ['-c', `
import json, sys, urllib.request
import numpy as np
sys.path.insert(0, ${JSON.stringify(path.join(ROOT, 'backend'))})
from app.samples import generate_credit
hp = json.load(urllib.request.urlopen(sys.argv[1] + '/api/runs/' + sys.argv[2] + '/holdout-predictions'))
df, _ = generate_credit()
rows = df.iloc[np.asarray(hp['rows'], dtype=int)].drop(columns=['defaulted']).head(400)
rows.to_csv(sys.argv[3] + '.csv', index=False)
open(sys.argv[3], 'w').write(rows.to_json(orient='records'))
`, UI, S.B, rowsJson])
    const recs = JSON.parse(fs.readFileSync(rowsJson, 'utf8'))
    let adv = null
    for (const r of recs.slice(0, 60)) {
      const o = await api(`/api/registry/versions/${S.vB}/predict`, { row: r, explain: false })
      if (o.reasons?.adverse) { adv = r; break }
    }
    if (!adv) throw new Error('no adverse row in the first 60 holdout rows')
    await versionTab(S.vB, 'Try It')
    for (const [k, x] of Object.entries(adv)) {
      const el = page.getByTestId(`try-${k}`)
      if (!(await el.count())) continue
      const tag = await el.evaluate((e) => e.tagName)
      if (tag === 'SELECT') await el.selectOption(String(x)); else await el.fill(String(x))
    }
    await page.getByTestId('try-predict').click()
    await page.getByTestId('try-reason-codes').waitFor({ timeout: 180_000 })
    await page.waitForTimeout(1500)
    await page.getByTestId('try-reason-codes').scrollIntoViewIfNeeded()
    await shot('10-try-it-reason-codes')
    const rc = await text('try-reason-codes')
    expectNum('4', 'Try It p̂ of the first adverse row', rc, 0.093, 0.004)
    expectText('4', 'four reasons R09 · R12 · R03 · R10', rc.replace(/\s+/g, ' '), /R09.*debt_to_income.*R12.*months_employed.*R03.*income_usd.*R10.*utilization/)
    await versionTab(S.vB, 'Batch & Drift')
    await page.getByTestId('batch-file').setInputFiles(rowsJson + '.csv')
    await page.getByTestId('batch-submit').click()
    await page.getByTestId('batch-result').waitFor({ timeout: 300_000 })
    await page.waitForTimeout(1500)
    await shot('11-batch-reasons')
    const br = await text('batch-reasons')
    expectNum('4', 'adverse rows of 400 in the batch', br, 126, 3)
    const job = await api(`/api/registry/versions/${S.vB}`)
    void job
    await versionTab(S.vB, 'Model Card')
    await page.getByTestId('card-diagnostics').waitFor({ timeout: 60_000 })
    await page.getByTestId('card-diagnostics').scrollIntoViewIfNeeded()
    await shot('12-model-card-diagnostics')
    const dg = await text('card-diagnostics')
    expectText('4', 'model card diagnostics: out-of-time split', dg, /out-of-time/)
    expectText('4', 'model card diagnostics: cost c_FN = 10', dg.replace(/\s+/g, ' '), /10/)
  })

  // ── 5. Scorecard
  await step('5 scorecard', async () => {
    await explainTab(S.B, 'Explain', 'Scorecard')
    await page.getByTestId('scorecard-delta').waitFor({ timeout: 300_000 })
    await page.waitForTimeout(1000)
    await shot('13-explain-scorecard')
    const hero = await text('scorecard-hero')
    expectNum('5', 'ΔAUC served − scorecard', await text('scorecard-delta'), 0.0010, 0.002)
    expectNum('5', 'scorecard AUC', hero, 0.6977, 0.003)
    expectNum('5', 'served AUC', hero, 0.6987, 0.003)
    expectText('5', '9-feature scorecard table', await text('scorecard-table'), /\S/)
  })

  // ── 6. Learning curve · 7. Model Doctor
  await step('6-7 learning curve, Model Doctor', async () => {
    await explainTab(S.B, 'Diagnose', 'Learning Curve')
    await page.getByTestId('lc-start').click()
    await page.getByTestId('lc-verdict').waitFor({ timeout: 900_000 })
    await page.waitForTimeout(1500)
    await shot('14-explain-learning-curve')
    expectText('6', 'verdict noisy', await text('lc-verdict'), /nois/i)
    expectNum('6', 'gain per doubling', await text('lc-slope'), 0.021, 0.01)
    const table = await text('lc-table').catch(() => text('lc-panel'))
    for (const n of [768, 1919, 3838, 7675]) expectNum('6', `learning-curve point n = ${n}`, table, n, 1)
    await explainTab(S.B, 'Diagnose', 'Model Doctor')
    await page.getByTestId('doctor-primary').first().waitFor({ timeout: 120_000 })
    await page.waitForTimeout(1500)
    await shot('15-explain-doctor')
    expectText('7', 'primary: Overfitting (minor)', await text('doctor-hero').catch(() => text('doctor-panel')), /Overfitting/)
    expectNum('7', 'gap in noise units (z)', await text('doctor-panel'), 4.4, 0.6)
  })

  // ── 8. Monitoring
  await step('8 monitoring: challenger, stream, alerts, retrain', async () => {
    await versionTab(S.champ, 'Monitoring')
    await page.getByTestId('monitoring').waitFor({ timeout: 60_000 })
    const chal = page.getByTestId('mon-challenger')
    await chal.locator('select').selectOption(S.chall)
    await chal.getByRole('button', { name: 'Set Challenger' }).click()
    await page.waitForTimeout(1500)
    await page.getByTestId('mon-feed-all').click()
    await page.getByTestId('mon-feed-one').filter({ hasText: 'All 12 months fed' }).waitFor({ timeout: 900_000 })
    await page.waitForTimeout(2500)
    await shot('16-monitoring')
    const r7 = await text('mon-row-2025-07')
    expectNum('8', 'PSI(debt_to_income) 2025-07', r7, 0.41, 0.03)
    expectText('8', 'first drift alert 2025-07', await text('mon-first-cov'), /2025-07/)
    const all = await text('monitoring')
    expectNum('8', 'observed default rate 2025-07', all, 0.351, 0.01)
    expectNum('8', 'p̂ 2025-07', all, 0.250, 0.01)
    expectNum('8', 'challenger ΔAUC (paired)', await text('mon-paired'), -0.0020, 0.0015)
    const ret = page.getByTestId('mon-retrain')
    await ret.scrollIntoViewIfNeeded()
    const rt = await text('mon-retrain')
    expectText('8', 'retrain window 2025-04-01 … 2025-09-30', rt, /2025-04-01 … 2025-09-30/)
    expectNum('8', 'retrain window rows', rt, 7200, 0)
    await page.getByTestId('mon-retrain-launch').click()
    await page.getByTestId('mon-retrain-out').waitFor({ timeout: 120_000 })
    await shot('17-monitoring-retrain')
    const href = await page.getByTestId('mon-retrain-out').locator('a').getAttribute('href')
    S.retrain = href.split('run=')[1]
    await go(`quick-train?run=${S.retrain}`)
    await page.waitForFunction(() => ['succeeded', 'failed'].includes(document.querySelector('[data-testid=race]')?.getAttribute('data-status')), null, { timeout: 900_000 }).catch(() => {})
    const r = await api(`/api/runs/${S.retrain}`)
    expectNum('8', 'retrained run holdout ROC AUC', String(r.holdout?.metrics?.roc_auc ?? ''), 0.7739, 0.004)
    await explainTab(S.champRun, 'Diagnose', 'Model Doctor')
    await page.getByTestId('doctor-primary').first().waitFor({ timeout: 120_000 })
    await page.waitForTimeout(1500)
    await shot('18-champion-doctor')
    const dp = await text('doctor-panel')
    expectText('8', 'champion doctor: Shift', dp, /Shift/)
    expectText('8', 'prescription: Retrain On Recent Data', dp, /Retrain On Recent Data/i)
  })

  await browser.close()
  const bad = checks.filter((c) => !c.ok)
  fs.writeFileSync(path.join(OUT, 'walkthrough.json'), JSON.stringify({ ids: S, steps, checks, errors }, null, 1))
  log(`done · steps ${steps.filter((s) => s.ok).length}/${steps.length} · numbers ${checks.length - bad.length}/${checks.length} · console errors ${errors.length}`)
  for (const c of bad) log('  ✗', c.step, c.what, 'want', c.want, 'seen', c.seen)
  for (const e of errors) log('  ✗', e.slice(0, 300))
  process.exit(bad.length || errors.length || steps.some((s) => !s.ok) ? 1 : 0)
}

main().catch((e) => { console.error(e); process.exit(1) })

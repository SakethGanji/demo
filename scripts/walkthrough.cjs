// The final end-to-end walkthrough in a headless browser, on three CSVs the build never saw (scripts/fresh_data.py).
// Not a test suite: one pass through the screens a person would click, with a screenshot at each stop and every
// console error collected. For each CSV: upload → label → goal → train (the live race) → results → Explain →
// Register (frontier, one-row latency) → approve (four eyes) → Production → Try It → batch score with drift.
//
//   node scripts/walkthrough.cjs [ui_url] [out_dir]      (defaults: http://localhost:5190, docs/progress/S9/walkthrough)
// Needs `make dev` running (API on :8000, UI on :5190) and Playwright (preinstalled, or `npm i -D playwright`).
const { chromium } = require('playwright')
const { execFileSync } = require('child_process')
const fs = require('fs')
const path = require('path')

const UI = process.argv[2] || 'http://localhost:5190'
const OUT = process.argv[3] || path.join(__dirname, '..', 'docs', 'progress', 'S9', 'walkthrough')
const ROOT = path.join(__dirname, '..')
const DATA = path.join(ROOT, 'backend', 'data', 'fresh')

const FLOWS = [
  { tag: 'fraud', file: 'card_fraud.csv', label: 'is_fraud', metric: 'average_precision', task: 'binary',
    fit: { hyperparameters: { GBM: { num_boost_round: 150 }, CAT: { iterations: 150 }, LR: {} }, num_bag_folds: 3 } },
  { tag: 'complaints', file: 'complaints.csv', label: 'product_team', metric: 'f1_macro', task: 'multiclass',
    fit: { hyperparameters: { TFIDF_LR: {}, TFIDF_SVM: {}, GBM: { num_boost_round: 80 } } } },
  { tag: 'deposits', file: 'deposit_balances.csv', label: 'avg_balance_next_q_gbp', metric: 'root_mean_squared_error', task: 'regression',
    fit: { hyperparameters: { GBM: { num_boost_round: 200 }, XT: {}, LR: {} } } },
]

const log = (...a) => console.log(new Date().toISOString().slice(11, 19), ...a)

async function main() {
  fs.mkdirSync(OUT, { recursive: true })
  execFileSync(path.join(ROOT, 'backend', '.venv', 'bin', 'python'), [path.join(ROOT, 'scripts', 'fresh_data.py'), DATA], { stdio: 'inherit' })
  const browser = await chromium.launch()
  const ctx = await browser.newContext({ viewport: { width: 1600, height: 1000 }, deviceScaleFactor: 1 })
  const page = await ctx.newPage()
  const errors = []
  page.on('pageerror', (e) => errors.push(`pageerror: ${e.message}`))
  page.on('console', (m) => { if (m.type() === 'error') errors.push(`console: ${m.text()}`) })
  const shot = async (name, opts = {}) => { const p = path.join(OUT, `${name}.png`); await page.screenshot({ path: p, ...opts }); log('  ▸', path.relative(ROOT, p)) }
  const go = async (route) => { await page.goto(`${UI}/#/${route}`, { waitUntil: 'networkidle' }); await page.waitForTimeout(800) }
  const results = []

  for (const f of FLOWS) {
    log(`── ${f.tag}: ${f.file}`)
    const t0 = Date.now()
    // 1. Upload
    await go('quick-train')
    await page.getByTestId('dataset-file').setInputFiles(path.join(DATA, f.file))
    await page.getByTestId('dataset-profile').filter({ hasText: f.file }).waitFor({ timeout: 60_000 })
    await shot(`${f.tag}-01-upload`)
    // 2. Label and problem type
    await page.getByRole('button', { name: 'Continue →' }).click()
    await page.getByTestId('label-select').selectOption(f.label)
    await page.getByTestId('problem-type').locator('button.on', { hasText: f.task }).waitFor({ timeout: 10_000 })
    // F4: confirming the target is an explicit step (draws that target's own holdout)
    const confirm = page.getByTestId('confirm-target-btn')
    if (await confirm.count()) { await confirm.click(); await page.getByTestId('confirm-target').and(page.locator('[data-confirmed="yes"]')).waitFor({ timeout: 60_000 }) }
    await shot(`${f.tag}-02-target`)
    // 3. Goal: the metric, the preset, a fast fit for the walkthrough
    await page.getByRole('button', { name: 'Continue →' }).click()
    const m = page.getByTestId(`metric-${f.metric}`)
    if (await m.count()) await m.click()
    await page.getByTestId('advanced').locator('.adv-toggle').click()
    await page.getByTestId('fit-kwargs').fill(JSON.stringify(f.fit))
    await page.getByTestId('resolved-call').filter({ hasText: 'hyperparameters=' }).waitFor()
    await shot(`${f.tag}-03-goal`)
    // 4. The race
    await page.getByTestId('start-training').click()
    const race = page.getByTestId('race')
    await race.waitFor({ timeout: 30_000 })
    await page.waitForTimeout(4000)
    await shot(`${f.tag}-04-race`)
    await race.getByRole('button', { name: '3×' }).click().catch(() => {})
    await page.waitForFunction(() => document.querySelector('[data-testid=race]')?.getAttribute('data-status') === 'succeeded', null, { timeout: 600_000 })
    await page.waitForTimeout(2500)
    await shot(`${f.tag}-05-race-done`)
    const runId = (await page.getByTestId('race-run-id').textContent()).trim()
    // 5. Results
    await page.getByTestId('see-results').click()
    await page.getByTestId('results').waitFor({ timeout: 60_000 })
    await page.waitForTimeout(1500)
    await shot(`${f.tag}-06-results`)
    const run = await (await page.request.get(`${UI}/api/runs/${runId}`)).json()
    const metric = run.eval_metric.name
    const score = run.holdout.metrics[metric]
    // 6. Explain (every tab this run has)
    await go(`explain?run=${runId}`)
    await page.getByRole('tab').first().waitFor({ timeout: 60_000 })
    const tabs = await page.getByRole('tab').allTextContents()
    for (const [i, t] of tabs.entries()) {
      await page.getByRole('tab', { name: t, exact: true }).click()
      await page.waitForTimeout(t.includes('SHAP') ? 9000 : 2500)
      await shot(`${f.tag}-07-explain-${i + 1}-${t.toLowerCase().replace(/[^a-z]+/g, '-')}`)
    }
    // 7. Register: frontier and one-row latency, then four eyes and Production
    await go(`registry?register=${runId}`)
    await page.getByTestId('actor').fill('Dana Dev')
    await page.getByTestId('frontier').waitFor({ timeout: 180_000 })
    await page.getByTestId('register-name').fill(`walkthrough.${f.tag}`)
    await page.getByTestId('register-desc').fill(`Fresh-data walkthrough: ${f.file}`)
    await shot(`${f.tag}-08-register`)
    await page.getByTestId('register-submit').click()
    const detail = page.getByTestId('version-detail')
    await page.waitForFunction(() => document.querySelector('[data-testid=version-detail]')?.getAttribute('data-status') === 'ready', null, { timeout: 600_000 })
    const vid = await detail.getAttribute('data-version')
    await page.getByTestId('actor').fill('Riley Review')
    await page.getByTestId('review-comment').fill('Holdout score and card reviewed on fresh data')
    await page.getByTestId('approve').click()
    await page.getByTestId('review-done').waitFor()
    await page.getByTestId('to-Production').click()
    await page.getByTestId(`version-${vid}`).and(page.locator('[data-stage=Production]')).waitFor({ timeout: 30_000 })
    await page.waitForTimeout(1000)
    await shot(`${f.tag}-09-approved`)
    // 8. Try It
    await page.getByRole('tab', { name: 'Try It' }).click()
    await page.getByTestId('try-predict').click()
    await page.getByTestId('try-result').waitFor({ timeout: 180_000 })
    await page.getByTestId('try-reasons').waitFor({ timeout: 180_000 }).catch(() => {})
    await page.waitForTimeout(1000)
    await shot(`${f.tag}-10-try-it`)
    const pred = (await page.getByTestId('try-result').innerText()).split('\n').slice(0, 3).join(' ')
    // 9. Batch score + drift
    await page.getByRole('tab', { name: 'Batch & Drift' }).click()
    const csv = fs.readFileSync(path.join(DATA, f.file), 'utf8').split('\n')
    const head = csv[0].split(',')
    const li = head.indexOf(f.label)
    const strip = (line) => { if (f.task === 'multiclass') return line; const c = line.split(','); c.splice(li, 1); return c.join(',') }
    const body = [f.task === 'multiclass' ? csv[0] : strip(csv[0]), ...csv.slice(1, 501).filter(Boolean).map(strip)].join('\n') + '\n'
    await page.getByTestId('batch-file').setInputFiles({ name: `${f.tag}_new_batch.csv`, mimeType: 'text/csv', buffer: Buffer.from(body) })
    await page.getByTestId('batch-submit').click()
    await page.getByTestId('batch-result').waitFor({ timeout: 300_000 })
    await page.waitForTimeout(1500)
    await shot(`${f.tag}-11-batch`)
    const rows = (await page.getByTestId('batch-rows').textContent()).trim()
    results.push({ tag: f.tag, run: runId, version: vid, metric, score, best: run.best_model, pred, batchRows: rows, seconds: Math.round((Date.now() - t0) / 1000) })
    log(`  ✓ ${runId} · ${run.best_model} · holdout ${metric} ${score.toFixed(4)} · ${vid} in Production · Try It: ${pred} · batch ${rows} rows`)
  }
  await browser.close()
  fs.writeFileSync(path.join(OUT, 'results.json'), JSON.stringify({ results, errors }, null, 1))
  log(`done · ${results.length}/${FLOWS.length} flows · ${errors.length} console errors`)
  for (const e of errors) log('  ✗', e.slice(0, 300))
  process.exit(results.length === FLOWS.length && errors.length === 0 ? 0 : 1)
}

main().catch((e) => { console.error(e); process.exit(1) })

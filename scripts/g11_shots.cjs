// G11 screenshots: every page of the five-item navigation, a run page of each kind, the Registry's Results and Try It per
// kind, mock mode, and three old routes landing on their redirects.
// node scripts/g11_shots.cjs <out_dir> [mock_ui]   (API on :8000 with runs of every kind, e.g. after `make smoke`;
// Vite on :5190; optionally a second Vite in mock mode, e.g. ENSEMBLE_API_URL=http://127.0.0.1:9 npx vite --port 5191)
const { chromium } = require('playwright')
const UI = 'http://localhost:5190'
const API = 'http://127.0.0.1:8000'
const OUT = process.argv[2] || 'docs/progress/G11'
const MOCK = process.argv[3] || 'http://localhost:5191'
const j = async (p) => (await fetch(API + p)).json()

async function main() {
  const runs = (await j('/api/runs?kind=all')).runs.filter((r) => r.status === 'succeeded')
  const reg = await j('/api/registry')
  const kind = (r) => r.kind || 'tabular'
  const run = (k, f = () => true) => runs.find((r) => kind(r) === k && f(r))
  // the registry list carries no result: a version's kind is its run's
  const runOf = (v) => runs.find((r) => r.id === v.run_id)
  const ver = (k, f = () => true) => reg.versions.find((v) => v.status === 'ready' && runOf(v) && kind(runOf(v)) === k && f(runOf(v)))
  const binary = run('tabular', (r) => r.problem_type === 'binary' && r.dataset_id === 'credit-default') ?? run('tabular', (r) => r.problem_type === 'binary')
  const regression = run('tabular', (r) => r.problem_type === 'regression')
  const vBin = ver('tabular', (r) => r.problem_type === 'binary' && r.dataset_id === 'credit-default') ?? ver('tabular', (r) => r.problem_type === 'binary')
  const ag = ((await j('/api/agent/sessions')).sessions || []).find((s) => s.status === 'idle')
  const id = (x) => (x ? x.id : 'missing')

  const shots = [
    ['01-home', '#/home'],
    ['02-build', '#/build'],
    ['03-build-sample', '#/build?dataset=credit-default'],
    ['04-new-model', '#/quick-train?dataset=credit-default'],
    ['05-build-forecast', '#/forecast'],
    ['06-build-anomaly', '#/anomaly'],
    ['07-build-import', '#/import'],
    ['08-build-expected-loss', '#/expected-loss'],
    ['09-build-segmented', '#/segmented'],
    ['10-build-survival', '#/survival'],
    ['11-build-reject-inference', '#/reject-inference'],
    ['12-build-multilabel', '#/multilabel'],
    ['13-build-advanced-gym', '#/gym'],
    ['14-models', '#/models'],
    ['15-run-binary-results', `#/models?run=${id(binary)}`],
    ['16-run-binary-explain', `#/models?run=${id(binary)}&tab=Explain`],
    ['17-run-binary-doctor', `#/models?run=${id(binary)}&tab=Doctor`, 'doctor-checks'],
    ['18-run-regression-explain-errors', `#/models?run=${id(regression)}&tab=Explain&sub=Errors`],
    ['19-run-forecast', `#/models?run=${id(run('forecast'))}`],
    ['20-run-expected-loss', `#/models?run=${id(run('expected_loss'))}`],
    ['21-registry', '#/registry'],
    ['22-registry-binary-overview', `#/registry?version=${id(vBin)}`],
    ['23-registry-binary-try-it', `#/registry?version=${id(vBin)}&tab=Try%20It`],
    ['24-registry-el-results', `#/registry?version=${id(ver('expected_loss'))}&tab=Results`],
    ['25-registry-seg-results', `#/registry?version=${id(ver('segmented'))}&tab=Results`],
    ['26-registry-surv-results', `#/registry?version=${id(ver('survival'))}&tab=Results`],
    ['27-registry-ml-results', `#/registry?version=${id(ver('multilabel'))}&tab=Results`],
    ['28-registry-surv-try-it', `#/registry?version=${id(ver('survival'))}&tab=Try%20It`],
    ['29-registry-el-try-it', `#/registry?version=${id(ver('expected_loss'))}&tab=Try%20It`],
    ['30-vela-agent', ag ? `#/vela?s=${ag.id}` : '#/vela'],
    ['31-vela-autopilot', '#/vela?mode=auto'],
    ['32-settings', '#/settings'],
    ['40-redirect-experiments', '#/experiments'],
    ['41-redirect-explain-shift', `#/explain?run=${id(binary)}&tab=Shift`],
    ['42-redirect-autopilot', '#/autopilot'],
  ]
  const browser = await chromium.launch()
  const ctx = await browser.newContext({ viewport: { width: 1366, height: 900 }, deviceScaleFactor: 1 })
  const page = await ctx.newPage()
  page.on('pageerror', (e) => console.log('pageerror', String(e)))
  await page.addInitScript(() => { localStorage.setItem('ensemble.actor', 'Dana Dev'); localStorage.setItem('ensemble.prefs', JSON.stringify({ velaOpen: 'Closed' })) })
  const take = async (base, name, hash, wait) => {
    await page.goto(`${base}/${hash}`)
    if (wait) await page.getByTestId(wait).waitFor({ timeout: 180_000 }).catch(() => {})
    await page.waitForTimeout(hash.includes('Explain') || hash.includes('Doctor') ? 9000 : 5000)
    // a selected version sits below the lineage and the versions table: show it
    if (hash.includes('version=')) await page.evaluate(() => document.querySelector('[data-testid=version-detail]')?.scrollIntoView())
    await page.screenshot({ path: `${OUT}/${name}.png` })
    console.log('saved', name, '→', page.url().split('#')[1])
  }
  await take(UI, '00-sidebar-expanded', '#/home')
  await page.locator('[aria-label="Collapse sidebar"]').click()
  await page.waitForTimeout(500)
  await page.screenshot({ path: `${OUT}/00-sidebar-collapsed.png` })
  await page.locator('[aria-label="Expand sidebar"]').click()
  const only = process.env.ONLY ? new RegExp(process.env.ONLY) : null
  for (const [name, hash, wait] of shots) if (!only || only.test(name)) await take(UI, name, hash, wait)
  // mock mode: the same app with the API offline
  for (const [name, hash] of [['50-mock-models', '#/models'], ['51-mock-run', '#/models?run=run_894a'], ['52-mock-run-explain', '#/models?run=run_894a&tab=Explain'], ['53-mock-build', '#/build']]) {
    try { await take(MOCK, name, hash) } catch (e) { console.log('mock skipped', name, String(e).slice(0, 80)) }
  }
  await browser.close()
}
main().catch((e) => { console.error(e); process.exit(1) })

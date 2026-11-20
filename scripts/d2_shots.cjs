// D2: screenshots (1366 wide) of the Model ladder card (Results) and Feature Lab (Build → Advanced).
//   node scripts/d2_shots.cjs docs/progress/D2 [ui=http://127.0.0.1:5190] [api=http://127.0.0.1:8000]
// Real-data runs come from backend/data/realdata/d2_results.json (scripts/realdata_d2.py); the crafted ones are the
// latest smoke runs (sections ladder and featurelab) found through the API.
const { chromium } = require('playwright')
const fs = require('fs')
const path = require('path')
const out = process.argv[2] || 'docs/progress/D2'
const ui = process.argv[3] || 'http://127.0.0.1:5190'
const apiUrl = process.argv[4] || 'http://127.0.0.1:8000'
const resPath = path.join(__dirname, '..', 'backend', 'data', 'realdata', 'd2_results.json')
const res = fs.existsSync(resPath) ? JSON.parse(fs.readFileSync(resPath, 'utf8')) : {}

const getJson = async (p) => (await fetch(`${apiUrl}${p}`)).json()

;(async () => {
  fs.mkdirSync(out, { recursive: true })
  const runs = (await getJson('/api/runs')).runs ?? []
  const dss = (await getJson('/api/datasets')).datasets ?? []
  const byDsName = (prefix) => {
    const d = dss.filter((x) => x.name.startsWith(prefix) && !x.name.includes('·')).map((x) => x.id)
    return runs.filter((r) => d.includes(r.dataset_id) && r.status === 'succeeded').map((r) => r.id)[0]
  }
  const ladders = [
    ['01-ladder-german-credit', res.german?.run], ['02-ladder-taiwan', res.taiwan?.run], ['03-ladder-bank-marketing', res.bank?.run],
    ['04-ladder-banking77', res.banking77?.run], ['05-ladder-crafted-simple', byDsName('ladder_simple')], ['06-ladder-crafted-stack', byDsName('ladder_complex')],
  ].filter(([, id]) => id)
  const b = await chromium.launch()
  const p = await b.newPage({ viewport: { width: 1366, height: 900 }, deviceScaleFactor: 1 })
  for (const [name, id] of ladders) {
    await p.goto(`${ui}/#/models?run=${encodeURIComponent(id)}&tab=Results`)
    await p.waitForSelector('[data-testid="ladder"]', { timeout: 240000 })
    await p.locator('[data-testid="ladder"]').scrollIntoViewIfNeeded()
    await p.evaluate(() => { const el = document.querySelector('[data-testid="ladder"]'); if (el) el.scrollIntoView({ block: 'start' }) })
    await p.waitForTimeout(500)
    await p.screenshot({ path: path.join(out, `${name}.png`) })
    console.log('shot', name, id)
  }
  // Serve <rung> → Register with that model preselected
  if (ladders[0]) {
    await p.goto(`${ui}/#/models?run=${encodeURIComponent(ladders[0][1])}&tab=Results`)
    await p.waitForSelector('[data-testid="ladder-serve"]', { timeout: 240000 })
    await p.click('[data-testid="ladder-serve"]')
    await p.waitForTimeout(6000)
    await p.screenshot({ path: path.join(out, '07-serve-opens-register.png') })
    console.log('shot 07-serve-opens-register')
  }
  // Feature Lab: Taiwan (real) and the planted ratio (crafted)
  const fl = [['08-featurelab-taiwan', res.featurelab_taiwan?.dataset],
              ['09-featurelab-planted-ratio', dss.filter((x) => x.name.startsWith('fl_ratio') && !x.name.includes('·')).map((x) => x.id).slice(-1)[0]]].filter(([, id]) => id)
  for (const [name, id] of fl) {
    await p.goto(`${ui}/#/build?dataset=${encodeURIComponent(id)}`)
    await p.waitForSelector('[data-testid="feature-lab"]', { timeout: 120000 })
    await p.waitForSelector('[data-testid="fl-job"]', { timeout: 60000 })
    await p.evaluate(() => { const el = document.querySelector('[data-testid="feature-lab"]'); if (el) { el.open = true; el.scrollIntoView({ block: 'start' }) } })
    await p.waitForTimeout(800)
    await p.screenshot({ path: path.join(out, `${name}.png`) })
    console.log('shot', name, id)
  }
  await b.close()
})()

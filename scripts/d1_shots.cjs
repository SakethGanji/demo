// D1: screenshots of Build's Data Report (1366 wide) on the four real datasets and one sample.
//   node scripts/d1_shots.cjs docs/progress/D1 [ui=http://127.0.0.1:5190]
// The real datasets' ids come from backend/data/realdata/uploaded.json (scripts/realdata_d1.py --upload).
const { chromium } = require('playwright')
const fs = require('fs')
const path = require('path')
const out = process.argv[2] || 'docs/progress/D1'
const ui = process.argv[3] || 'http://127.0.0.1:5190'
const ids = JSON.parse(fs.readFileSync(path.join(__dirname, '..', 'backend', 'data', 'realdata', 'uploaded.json'), 'utf8'))
const shots = [['01-taiwan', ids.taiwan], ['02-bank-marketing', ids.bank], ['03-german-credit', ids.german], ['04-banking77', ids.banking77], ['05-sample-credit-default', 'credit-default']]

;(async () => {
  fs.mkdirSync(out, { recursive: true })
  const b = await chromium.launch()
  const p = await b.newPage({ viewport: { width: 1366, height: 900 }, deviceScaleFactor: 1 })
  for (const [name, id] of shots) {
    await p.goto(`${ui}/#/build?dataset=${encodeURIComponent(id)}`)
    await p.waitForSelector('[data-testid="data-report"]', { timeout: 120000 })
    // wait for the label-quality model to finish (the card polls while it runs)
    for (let i = 0; i < 60 && (await p.locator('.dr-title .muted', { hasText: 'running' }).count()); i++) await p.waitForTimeout(2000)
    const first = p.locator('.data-report .dr-item').first()
    if (await first.count()) await first.locator('summary').click()
    await p.waitForTimeout(400)
    await p.screenshot({ path: path.join(out, `${name}.png`) })
    console.log('shot', name)
  }
  // the bank table after "Apply 2 fixes": the new version with its lineage (the original stays as it was)
  await p.goto(`${ui}/#/build?dataset=${encodeURIComponent(ids.bank)}`)
  await p.waitForSelector('[data-testid="dr-apply"]', { timeout: 120000 })
  await p.click('[data-testid="dr-apply"]')
  await p.waitForSelector('[data-testid="dr-made"]', { timeout: 120000 })
  await p.waitForSelector('[data-testid="dr-lineage"]', { timeout: 120000 })
  await p.waitForTimeout(600)
  await p.screenshot({ path: path.join(out, '06-bank-fixed-version.png') })
  console.log('shot 06-bank-fixed-version')
  await b.close()
})()

// D5: screenshots (1366 wide) of the full chain on real data: Data Report → ladder → Model Doctor (What's missing,
// How to improve) → Review, read from backend/data/realdata/results.json (scripts/realdata.py).
//   node scripts/d5_shots.cjs <out dir> <prefix>=<item> … [--ui=http://127.0.0.1:5190]
// Each item gives <prefix>-1-data-report, -2-ladder, -3-doctor, -4-review (when the item has a review) and, for the
// drift copy, -5-changes (Registry → Monitoring → Changes).
const { chromium } = require('playwright')
const fs = require('fs')
const path = require('path')
const args = process.argv.slice(2)
const out = args[0] || 'docs/progress/D5'
const ui = (args.find((a) => a.startsWith('--ui=')) || '--ui=http://127.0.0.1:5190').slice(5)
const pairs = args.slice(1).filter((a) => a.includes('=') && !a.startsWith('--')).map((a) => a.split('='))
const res = JSON.parse(fs.readFileSync(path.join(__dirname, '..', 'backend', 'data', 'realdata', 'results.json'), 'utf8'))

async function shoot(p, sel, file, top = false) {
  await p.waitForSelector(sel, { timeout: 600000 })
  await p.waitForTimeout(800)
  const el = await p.$(sel)
  await el.scrollIntoViewIfNeeded()
  const box = await el.boundingBox()
  await p.setViewportSize({ width: 1366, height: Math.min(Math.max(900, Math.ceil(box.height) + 200), 7000) })
  await p.waitForTimeout(500)
  if (top) await p.screenshot({ path: file })
  else await (await p.$(sel)).screenshot({ path: file })
  await p.setViewportSize({ width: 1366, height: 900 })
}

;(async () => {
  fs.mkdirSync(out, { recursive: true })
  const b = await chromium.launch()
  const p = await b.newPage({ viewport: { width: 1366, height: 900 }, deviceScaleFactor: 1 })
  for (const [prefix, item] of pairs) {
    const o = res[item]
    if (!o) { console.error('no item', item); continue }
    // 1. Build's Data Report (the label check finished long ago, so the card is complete)
    await p.goto(`${ui}/#/build?dataset=${encodeURIComponent(o.dataset)}`)
    await p.waitForSelector('[data-testid="data-report"]', { timeout: 120000 })
    for (let i = 0; i < 60 && (await p.locator('.dr-title .muted', { hasText: 'running' }).count()); i++) await p.waitForTimeout(2000)
    const first = p.locator('.data-report .dr-item').first()
    if (await first.count()) await first.locator('summary').click()
    await shoot(p, '[data-testid="data-report"]', path.join(out, `${prefix}-1-data-report.png`))
    const rid = o.run && o.run.run
    if (rid) {
      // 2. Results → the ladder
      await p.goto(`${ui}/#/models?run=${encodeURIComponent(rid)}&tab=Results`)
      await shoot(p, '[data-testid="ladder"]', path.join(out, `${prefix}-2-ladder.png`))
      // 3. Doctor: headline, What's missing, How to improve, the diagnosis
      await p.goto(`${ui}/#/models?run=${encodeURIComponent(rid)}&tab=Doctor`)
      await shoot(p, '[data-testid="doctor-panel"]', path.join(out, `${prefix}-3-doctor.png`))
      // 4. the review's journal and plan
      if (o.review && o.review.id) {
        await p.goto(`${ui}/#/models?run=${encodeURIComponent(rid)}&tab=Doctor&review=1`)
        await p.waitForSelector('[data-testid="review-plan"], [data-testid="review-journal"]', { timeout: 600000 })
        await shoot(p, '[data-testid="review-panel"]', path.join(out, `${prefix}-4-review.png`))
      }
    }
    // 5. post-deployment changes on the registered version
    if (o.postdeploy && o.postdeploy.version) {
      await p.goto(`${ui}/#/registry?version=${encodeURIComponent(o.postdeploy.version)}&tab=Monitoring`)
      await shoot(p, '[data-testid="changes-card"]', path.join(out, `${prefix}-5-changes.png`))
    }
    console.log('shot', prefix, item)
  }
  await b.close()
})().catch((e) => { console.error(e); process.exit(1) })

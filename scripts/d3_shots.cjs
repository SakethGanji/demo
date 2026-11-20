// D3: screenshots (1366 wide) of the Model Doctor 2: What's missing, How to improve, the primary finding, Holdout Errors.
//   node scripts/d3_shots.cjs <out dir> <name>=<run id> [<name>=<run id> …] [--ui=http://127.0.0.1:5190]
// Each run gives <name>-doctor.png (full page) and <name>-errors.png (the Holdout Errors sub-tab).
const { chromium } = require('playwright')
const fs = require('fs')
const path = require('path')
const args = process.argv.slice(2)
const out = args[0] || 'docs/progress/D3'
const ui = (args.find((a) => a.startsWith('--ui=')) || '--ui=http://127.0.0.1:5190').slice(5)
const pairs = args.slice(1).filter((a) => a.includes('=') && !a.startsWith('--')).map((a) => a.split('='))
const only = (args.find((a) => a.startsWith('--only=')) || '').slice(7)

/** Grow the viewport to the element's full height (the page scrolls inside a container, so fullPage would clip it). */
async function fit(p, sel) {
  const h = await p.evaluate((s) => { const el = document.querySelector(s); return el ? Math.ceil(el.getBoundingClientRect().height + el.getBoundingClientRect().top + 60) : 900 }, sel)
  await p.setViewportSize({ width: 1366, height: Math.min(Math.max(900, h), 6000) })
  await p.waitForTimeout(400)
}

;(async () => {
  fs.mkdirSync(out, { recursive: true })
  const b = await chromium.launch()
  const p = await b.newPage({ viewport: { width: 1366, height: 900 }, deviceScaleFactor: 1 })
  for (const [name, id] of pairs) {
    if (only !== 'errors') {
      await p.goto(`${ui}/#/models?run=${encodeURIComponent(id)}&tab=Doctor`)
      await p.waitForSelector('[data-testid="doctor-panel"]', { timeout: 600000 })
      await p.waitForTimeout(600)
      await fit(p, '[data-testid="doctor-panel"]')
      await p.screenshot({ path: path.join(out, `${name}-doctor.png`) })
      await p.setViewportSize({ width: 1366, height: 900 })
      console.log('shot', name, 'doctor', id)
    }
    if (only !== 'doctor') {
      await p.goto(`${ui}/#/models?run=${encodeURIComponent(id)}&tab=Doctor&sub=Holdout%20Errors`)
      await p.waitForSelector('[data-testid="holdout-errors"]', { timeout: 600000 })
      await p.waitForTimeout(500)
      await fit(p, '[data-testid="holdout-errors"]')
      await p.screenshot({ path: path.join(out, `${name}-errors.png`) })
      await p.setViewportSize({ width: 1366, height: 900 })
      console.log('shot', name, 'errors', id)
    }
  }
  await b.close()
})().catch((e) => { console.error(e); process.exit(1) })

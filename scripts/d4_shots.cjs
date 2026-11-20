// D4: screenshots (1366 wide) of the Review panel (Doctor tab) and the Registry Changes card.
//   node scripts/d4_shots.cjs <out dir> <name>=run:<run id> | <name>=version:<version id> … [--ui=http://127.0.0.1:5190]
// A run gives <name>-review.png (the Review card, journal and plan) and <name>-missing.png (What's missing with the
// domain gaps); a version gives <name>-changes.png (Registry → Monitoring → Changes).
const { chromium } = require('playwright')
const fs = require('fs')
const path = require('path')
const args = process.argv.slice(2)
const out = args[0] || 'docs/progress/D4'
const ui = (args.find((a) => a.startsWith('--ui=')) || '--ui=http://127.0.0.1:5190').slice(5)
const pairs = args.slice(1).filter((a) => a.includes('=') && !a.startsWith('--')).map((a) => a.split('='))

async function shoot(p, sel, file) {
  await p.waitForSelector(sel, { timeout: 600000 })
  await p.waitForTimeout(800)
  const el = await p.$(sel)
  await el.scrollIntoViewIfNeeded()
  const box = await el.boundingBox()
  await p.setViewportSize({ width: 1366, height: Math.min(Math.max(900, Math.ceil(box.height) + 200), 6000) })
  await p.waitForTimeout(400)
  await (await p.$(sel)).screenshot({ path: file })
  await p.setViewportSize({ width: 1366, height: 900 })
}

;(async () => {
  fs.mkdirSync(out, { recursive: true })
  const b = await chromium.launch()
  const p = await b.newPage({ viewport: { width: 1366, height: 900 }, deviceScaleFactor: 1 })
  for (const [name, ref] of pairs) {
    const [kind, id] = ref.split(':')
    if (kind === 'run') {
      await p.goto(`${ui}/#/models?run=${encodeURIComponent(id)}&tab=Doctor&review=1`)
      await p.waitForSelector('[data-testid="review-plan"], [data-testid="review-journal"]', { timeout: 600000 })
      await shoot(p, '[data-testid="review-panel"]', path.join(out, `${name}-review.png`))
      await shoot(p, '[data-testid="doctor-missing"]', path.join(out, `${name}-missing.png`))
    } else {
      await p.goto(`${ui}/#/registry?version=${encodeURIComponent(id)}&tab=Monitoring`)
      await shoot(p, '[data-testid="changes-card"]', path.join(out, `${name}-changes.png`))
    }
    console.log('shot', name, ref)
  }
  await b.close()
})().catch((e) => { console.error(e); process.exit(1) })

// F-A1: screenshots (1366 wide) of the screens the A1 fixes changed.
//   node scripts/fa1_shots.cjs <out dir> <name>=build:<dataset> | <name>=doctor:<run> | <name>=review:<run> … [--ui=http://127.0.0.1:5190]
const { chromium } = require('playwright')
const fs = require('fs')
const path = require('path')
const args = process.argv.slice(2)
const out = args[0] || 'docs/progress/F-A1'
const ui = (args.find((a) => a.startsWith('--ui=')) || '--ui=http://127.0.0.1:5190').slice(5)
const items = args.slice(1).filter((a) => a.includes('=') && !a.startsWith('--')).map((a) => { const [n, v] = a.split('='); const [k, id] = v.split(':'); return { n, k, id } })

async function shoot(p, sel, file) {
  await p.waitForSelector(sel, { timeout: 900000 })
  await p.waitForTimeout(1000)
  const box = await (await p.$(sel)).boundingBox()
  await p.setViewportSize({ width: 1366, height: Math.min(Math.max(900, Math.ceil(box.height) + 200), 7000) })
  await p.waitForTimeout(500)
  await (await p.$(sel)).screenshot({ path: file, animations: 'disabled', timeout: 60000 })
  await p.setViewportSize({ width: 1366, height: 900 })
}

;(async () => {
  fs.mkdirSync(out, { recursive: true })
  const b = await chromium.launch()
  const p = await b.newPage({ viewport: { width: 1366, height: 900 }, deviceScaleFactor: 1 })
  for (const { n, k, id } of items) {
    if (k === 'build') {
      await p.goto(`${ui}/#/build?dataset=${encodeURIComponent(id)}`)
      await p.waitForSelector('[data-testid="data-report"]', { timeout: 300000 })
      for (let i = 0; i < 60 && (await p.locator('.dr-title .muted', { hasText: 'running' }).count()); i++) await p.waitForTimeout(2000)
      const first = p.locator('.data-report .dr-item').first()
      if (await first.count()) await first.locator('summary').click()
      await shoot(p, '[data-testid="data-report"]', path.join(out, `${n}.png`))
    } else if (k === 'doctor') {
      await p.goto(`${ui}/#/models?run=${encodeURIComponent(id)}&tab=Doctor`)
      await shoot(p, '[data-testid="doctor-panel"]', path.join(out, `${n}.png`))
    } else if (k === 'review') {
      await p.goto(`${ui}/#/models?run=${encodeURIComponent(id)}&tab=Doctor&review=1`)
      await p.waitForSelector('[data-testid="review-plan"], [data-testid="review-journal"]', { timeout: 600000 })
      await shoot(p, '[data-testid="review-panel"]', path.join(out, `${n}.png`))
    } else if (k === 'results') {
      const t0 = Date.now()
      await p.goto(`${ui}/#/models?run=${encodeURIComponent(id)}&tab=Results`)
      await shoot(p, '[data-testid="ladder"]', path.join(out, `${n}.png`))
      console.log('results visible in', Date.now() - t0, 'ms')
    }
    console.log('shot', n)
  }
  await b.close()
})().catch((e) => { console.error(e); process.exit(1) })

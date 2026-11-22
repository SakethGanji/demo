// LB-C: a quick look at one screen (dev aid): node peek.cjs <url-hash> <out.png> [actor]
const { chromium } = require('playwright')
;(async () => {
  const [hash, out, sel, actor = 'Ana'] = process.argv.slice(2)
  const b = await chromium.launch()
  const p = await b.newPage({ viewport: { width: 1366, height: 900 }, deviceScaleFactor: Number(process.env.DSF || 1) })
  p.on('console', (m) => { if (m.type() === 'error') console.log('console:', m.text()) })
  p.on('pageerror', (e) => console.log('pageerror', e.message))
  await p.goto('http://127.0.0.1:5190/')
  await p.evaluate((a) => localStorage.setItem('ensemble.actor', a), actor)
  await p.goto(`http://127.0.0.1:5190/#/${hash}`)
  await p.waitForTimeout(4000)
  if (sel) { const l = p.locator(sel).first(); await l.scrollIntoViewIfNeeded(); const bb = await l.boundingBox(); await p.screenshot({ path: out, clip: { x: bb.x - 8, y: bb.y - 8, width: Math.min(bb.width + 16, 1366), height: Math.min(bb.height + 16, 3000) } }) }
  else await p.screenshot({ path: out })
  await b.close()
})()

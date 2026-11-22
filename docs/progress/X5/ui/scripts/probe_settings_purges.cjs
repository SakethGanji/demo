// X5 probe (not in U2/U3): open Settings as a given actor/team, log /purges responses + console errors, screenshot the lifecycle section.
//   node probe_settings_purges.cjs <actor> <team> <out png>
const { chromium } = require('playwright')
const [who, team, png] = process.argv.slice(2)
;(async () => {
  const b = await chromium.launch(); const p = await b.newPage({ viewport: { width: 1366, height: 900 }, deviceScaleFactor: 1 })
  const errs = []; p.on('console', (m) => { if (m.type() === 'error') errs.push(m.text()) })
  p.on('response', async (r) => { if (/\/api\/(purges|retention|admin\/lake\/gc)/.test(r.url())) console.log(r.status(), r.url().replace(/^.*\/api/, '/api'), (await r.text().catch(() => '')).slice(0, 200)) })
  await p.goto('http://127.0.0.1:5190/#/'); await p.evaluate(([w, t]) => { localStorage.setItem('ensemble.actor', w); localStorage.setItem('ensemble.team', t) }, [who, team])
  await p.goto('http://127.0.0.1:5190/#/settings'); await p.reload(); await p.waitForTimeout(4000)
  const l = p.locator('[data-testid="settings-lifecycle"]')
  if (await l.count()) { await l.scrollIntoViewIfNeeded(); const bb = await l.boundingBox(); await p.screenshot({ path: png, clip: { ...bb, height: Math.min(bb.height, 1600) } }); console.log('TEXT', (await l.innerText()).replace(/\s+/g, ' ').slice(0, 1500)) }
  console.log('console errors', errs.length, errs); await b.close()
})()

// handoff check: Opening rest vs Engine first frames after the handoff
import { chromium } from 'playwright'
const [, , theme = 'light', pre = 'tools/qa/h'] = process.argv
const b = await chromium.launch({ headless: false, args: ['--ignore-gpu-blocklist', '--force-device-scale-factor=1', '--window-size=1480,1040', '--ozone-platform=x11'] })
const p = await b.newPage({ viewport: { width: 1440, height: 900 } })
const errs = []; p.on('console', (m) => m.type() === 'error' && errs.push(m.text()))
await p.goto(`http://127.0.0.1:5191/out/landing-film.html?theme=${theme}`, { waitUntil: 'networkidle' })
await p.waitForFunction(() => window.film && window.film.scenes.length === 9)
await p.evaluate(() => { document.querySelectorAll('.k-halo').forEach((e) => e.style.visibility = 'hidden') })
await p.evaluate(() => window.film.rest(0)); await p.waitForTimeout(300)
await p.screenshot({ path: `${pre}-a.png` })
for (const t of (process.argv[4] || '0.6').split(',')) {
  await p.evaluate((t) => window.film._seek(1, +t, 0), t); await p.waitForTimeout(250)
  await p.screenshot({ path: `${pre}-t${t}.png` })
}
console.log(JSON.stringify(errs))
await b.close()

// live landing check + walkthrough recording (headed, GPU). usage: node landing-walk.mjs dark|light <outdir>
import { createRequire } from 'module'
const require = createRequire(new URL('../../../analytics-service/web/package.json', import.meta.url))
const { chromium } = require('playwright')
const [, , theme = 'dark', out = '.'] = process.argv
const dark = theme === 'dark'
const b = await chromium.launch({ headless: false, args: ['--ignore-gpu-blocklist', '--force-device-scale-factor=1', '--window-size=1440,1000', '--ozone-platform=x11'] })
const ctx = await b.newContext({ viewport: { width: 1440, height: 900 }, colorScheme: dark ? 'dark' : 'light', recordVideo: { dir: `${out}/vid-${theme}`, size: { width: 1440, height: 900 } } })
const p = await ctx.newPage()
const errs = [], failed = []
p.on('console', (m) => m.type() === 'error' && errs.push(m.text()))
p.on('pageerror', (e) => errs.push(String(e)))
p.on('requestfailed', (r) => failed.push(r.url() + ' ' + (r.failure()?.errorText || '')))
p.on('response', (r) => r.status() >= 400 && failed.push(r.status() + ' ' + r.url()))
await p.goto('http://localhost:5174/', { waitUntil: 'networkidle' })
await p.evaluate((d) => { document.documentElement.classList.toggle('dark', d) }, dark)
const fr = () => p.frame({ url: /landing-film\.html/ })
const fs = await p.waitForFunction(() => document.querySelector('[data-testid=landing-film]')?.contentWindow?.film?.scenes?.length === 9)
const inF = (fn, arg) => fr().evaluate(fn, arg)
const waitRest = async () => {
  return inF(async () => {
    let frames = 0, on = true, worst = 0, last = performance.now()
    const tick = (n) => { frames++; worst = Math.max(worst, n - last); last = n; if (on) requestAnimationFrame(tick) }
    requestAnimationFrame(tick)
    const t0 = performance.now()
    await new Promise((r) => setTimeout(r, 300))
    await new Promise((r) => { const c = () => (window.film._state().playing ? setTimeout(c, 50) : r()); c() })
    on = false
    const s = (performance.now() - t0) / 1000
    return { secs: +s.toFixed(1), fps: +(frames / s).toFixed(1), worst: +worst.toFixed(0), theme: document.documentElement.dataset.theme, cur: window.film._state().cur }
  })
}
const res = []
res.push({ stop: 0, ...(await waitRest()) })
await p.waitForTimeout(2200)
for (let i = 1; i < 9; i++) {
  await p.keyboard.press('ArrowDown')
  await p.waitForTimeout(250)
  const r = await waitRest()
  // still at rest: playing false and the scene timeline does not move (sample a transform twice)
  const still = await inF(async () => {
    const sc = document.querySelectorAll('#k-scenes > section')[window.film._state().cur]
    const els = [...sc.querySelectorAll('*')].filter((e) => !e.closest('.k-halo')).slice(0, 400)
    const snap = () => els.map((e) => e.style.cssText).join('|')
    const a = snap(); await new Promise((r) => setTimeout(r, 700)); return a === snap()
  })
  res.push({ stop: i, ...r, still })
  if (i === 3) {
    // pointer lean
    await p.mouse.move(1300, 150); await p.waitForTimeout(700)
    const lx = await inF(() => getComputedStyle(document.documentElement).getPropertyValue('--lx'))
    await p.mouse.move(720, 450, { steps: 8 }); await p.waitForTimeout(500)
    res.push({ pointer_lx_after_move: lx })
  }
  await p.waitForTimeout(2300)
}
await p.keyboard.press('ArrowDown'); await p.waitForTimeout(1800)
const quiet = await inF(() => document.getElementById('k-scenes').classList.contains('quiet'))
const endVisible = await p.locator('[data-testid=landing-get-started]').isVisible()
await p.waitForTimeout(1500)
// theme toggle: click the studio toggle, the film follows
const before = await inF(() => document.documentElement.dataset.theme)
await p.locator('[data-testid=landing-theme] button').first().click()
await p.waitForTimeout(500)
const after = await inF(() => document.documentElement.dataset.theme)
await p.locator('[data-testid=landing-theme] button').first().click()
await p.waitForTimeout(300)
console.log(JSON.stringify({ theme, errs, failed, res, quiet, endVisible, toggle: [before, after] }, null, 0))
await ctx.close(); await b.close()

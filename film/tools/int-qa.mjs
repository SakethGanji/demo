// integrator QA: play all 9 scenes in order in the built film, per theme; fps, errors, clipping, rest screenshots
// usage: node int-qa.mjs <theme> <w> <h> [outPrefix] [--headed] [--only=3,4]
import { chromium } from 'playwright'
const [, , theme = 'light', w = '1440', hh = '900', pre = ''] = process.argv
const headed = process.argv.includes('--headed')
const onlyArg = process.argv.find((a) => a.startsWith('--only='))
const only = onlyArg ? onlyArg.slice(7).split(',').map(Number) : null
const b = await chromium.launch(headed ? { headless: false, args: ['--ignore-gpu-blocklist', '--force-device-scale-factor=1', `--window-size=${+w + 40},${+hh + 140}`, '--ozone-platform=x11'] } : {})
const p = await b.newPage({ viewport: { width: +w, height: +hh } })
const errs = []
p.on('console', (m) => m.type() === 'error' && errs.push(m.text()))
p.on('pageerror', (e) => errs.push(String(e)))
await p.goto(`http://127.0.0.1:5191/out/landing-film.html?theme=${theme}`, { waitUntil: 'networkidle' })
await p.waitForFunction(() => window.film && window.film.scenes.length === 9)
await p.evaluate((d) => window.film.theme(d), theme === 'dark')
await p.waitForTimeout(400)
const res = []
for (let i = 0; i < 9; i++) {
  if (only && !only.includes(i)) { await p.evaluate((i) => window.film.rest(i), i); continue }
  const r = await p.evaluate(async (i) => {
    let frames = 0, worst = 0, last = performance.now()
    let on = true
    const tick = (now) => { frames++; worst = Math.max(worst, now - last); last = now; if (on) requestAnimationFrame(tick) }
    requestAnimationFrame(tick)
    const t0 = performance.now()
    window.film.play(i)
    await new Promise((res) => { const c = () => (window.film._state().playing ? setTimeout(c, 50) : res()); setTimeout(c, 100) })
    on = false
    const secs = (performance.now() - t0) / 1000
    // clipping: text leaves outside their window, or outside the viewport
    const sc = document.querySelectorAll('#k-scenes > section')[i]
    const bad = []
    const vw = innerWidth, vh = innerHeight
    const wins = [...sc.querySelectorAll('.k-win')]
    for (const el of sc.querySelectorAll('*')) {
      if (el.children.length || !el.textContent.trim()) continue
      const cs = getComputedStyle(el)
      if (cs.visibility === 'hidden') continue
      let o = 1, n = el
      while (n && n !== sc) { o *= +getComputedStyle(n).opacity; n = n.parentElement }
      if (o < 0.05) continue
      const rc = el.getBoundingClientRect()
      if (!rc.width) continue
      if (rc.left < -1 || rc.top < -1 || rc.right > vw + 1 || rc.bottom > vh + 1) bad.push('viewport:' + el.textContent.trim().slice(0, 30))
      const win = el.closest('.k-win')
      if (win) {
        const wr = win.getBoundingClientRect()
        if (rc.right > wr.right + 2 || rc.bottom > wr.bottom + 2 || rc.left < wr.left - 2) bad.push('win:' + el.textContent.trim().slice(0, 30))
      }
      if (el.scrollWidth > el.clientWidth + 2 && (cs.overflow === 'hidden' || cs.textOverflow === 'ellipsis') && cs.display !== 'inline') bad.push('trunc:' + el.textContent.trim().slice(0, 30))
    }
    return { i, secs: +secs.toFixed(2), fps: +(frames / secs).toFixed(1), worstMs: +worst.toFixed(1), bad: bad.slice(0, 12), nbad: bad.length }
  }, i)
  res.push(r)
  await p.waitForTimeout(300)
  if (pre) await p.screenshot({ path: `${pre}-${i}.png` })
}
console.log(JSON.stringify({ theme, w, hh, errors: errs, res }, null, 1))
await b.close()

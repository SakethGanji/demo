// live landing, reduced motion: every stop should land on its rest frame with no playback; sizes x themes
import { createRequire } from 'module'
const require = createRequire(new URL('../../../analytics-service/web/package.json', import.meta.url))
const { chromium } = require('playwright')
const out = []
const b = await chromium.launch({ headless: false, args: ['--ignore-gpu-blocklist', '--force-device-scale-factor=1', '--window-size=1960,1200', '--ozone-platform=x11'] })
for (const [w, h] of [[1280, 720], [1920, 1080]]) for (const th of ['dark', 'light']) {
  const ctx = await b.newContext({ viewport: { width: w, height: h }, colorScheme: th, reducedMotion: 'reduce' })
  const p = await ctx.newPage()
  const errs = []
  p.on('console', (m) => m.type() === 'error' && errs.push(m.text())); p.on('pageerror', (e) => errs.push(String(e)))
  p.on('response', (r) => r.status() >= 400 && errs.push(r.status() + ' ' + r.url()))
  await p.goto('http://localhost:5174/', { waitUntil: 'networkidle' })
  await p.waitForFunction(() => document.querySelector('[data-testid=landing-film]')?.contentWindow?.film?.scenes?.length === 9)
  const fr = () => p.frame({ url: /landing-film\.html/ })
  const stops = []
  for (let i = 0; i < 9; i++) {
    if (i) await p.keyboard.press('ArrowDown')
    await p.waitForTimeout(900)
    const r = await fr().evaluate(async () => {
      const sc = document.querySelectorAll('#k-scenes > section')[window.film._state().cur]
      const els = [...sc.querySelectorAll('*')].slice(0, 600)
      const snap = () => els.map((e) => e.style.cssText + getComputedStyle(e).transform + getComputedStyle(e).opacity).join('|')
      const a = snap(); await new Promise((r) => setTimeout(r, 600))
      const vw = innerWidth, vh = innerHeight; let off = 0
      for (const el of sc.querySelectorAll('*')) { if (el.children.length || !el.textContent.trim()) continue; const rc = el.getBoundingClientRect(); if (!rc.width) continue; if (rc.left < -1 || rc.top < -1 || rc.right > vw + 1 || rc.bottom > vh + 1) off++ }
      return { cur: window.film._state().cur, playing: window.film._state().playing, still: a === snap(), off }
    })
    stops.push(r)
    if (i === 1 || i === 2) await p.screenshot({ path: `tools/iq/rm-${w}-${th}-${i}.png` })
  }
  out.push({ w, th, errs, bad: stops.filter((s, i) => s.cur !== i || s.playing || !s.still || s.off), n: stops.length })
  await ctx.close()
}
console.log(JSON.stringify(out, null, 1))
await b.close()

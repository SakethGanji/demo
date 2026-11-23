import { createRequire } from 'module'
const require = createRequire(new URL('../../../analytics-service/web/package.json', import.meta.url))
const { chromium } = require('playwright')
const b = await chromium.launch()
const ctx = await b.newContext({ viewport: { width: 1280, height: 720 }, reducedMotion: 'reduce' })
const p = await ctx.newPage()
await p.goto('http://127.0.0.1:5191/out/landing-film.html', { waitUntil: 'networkidle' })
await p.waitForFunction(() => window.film && window.film.scenes.length === 9)
await p.evaluate(() => window.film.play(1)); await p.waitForTimeout(800)
console.log(await p.evaluate(() => { const sc = document.querySelectorAll('#k-scenes > section')[1]; const o = []
 for (const el of sc.querySelectorAll('*')) { if (el.children.length || !el.textContent.trim()) continue; const rc = el.getBoundingClientRect(); if (!rc.width) continue
  if (rc.left < -1 || rc.top < -1 || rc.right > innerWidth + 1 || rc.bottom > innerHeight + 1) { let op = 1, n = el; while (n && n !== sc) { op *= +getComputedStyle(n).opacity; n = n.parentElement } o.push([el.textContent.trim().slice(0, 20), op.toFixed(2), getComputedStyle(el).visibility, Math.round(rc.top)]) } } return JSON.stringify(o) }))
await b.close()

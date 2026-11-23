// usage: node tools/shot.mjs <url-path-and-query> <out.png> [width height] [waitMs] [--headed]
// prints console errors; exits 1 if any. Run from anywhere; serves from http://localhost:5191/
import { createRequire } from 'module'
const require = createRequire(new URL('../../../analytics-service/web/package.json', import.meta.url))
const { chromium } = require('playwright')
const [, , path, out, w = '1600', hh = '900', wait = '600'] = process.argv
const headed = process.argv.includes('--headed')
const port = process.env.FILM_PORT || '5191'
const b = await chromium.launch(headed ? { headless: false, args: ['--ignore-gpu-blocklist', '--force-device-scale-factor=1', `--window-size=${+w + 40},${+hh + 140}`, '--ozone-platform=x11'] } : {})
const p = await b.newPage({ viewport: { width: +w, height: +hh } })
const errs = []
p.on('console', (m) => m.type() === 'error' && errs.push(m.text()))
p.on('pageerror', (e) => errs.push(String(e)))
await p.goto(`http://localhost:${port}/${path}`, { waitUntil: 'networkidle' })
await p.waitForTimeout(+wait)
if (out && out !== '-') await p.screenshot({ path: out })
console.log(JSON.stringify({ errors: errs }))
await b.close()
process.exit(errs.length ? 1 : 0)

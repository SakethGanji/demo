import { chromium } from 'playwright'
const b = await chromium.launch({ headless: false, args: ['--ignore-gpu-blocklist', '--force-device-scale-factor=1', '--window-size=1480,1040', '--ozone-platform=x11'] })
const p = await b.newPage({ viewport: { width: 1440, height: 900 } })
await p.goto(`http://127.0.0.1:5191/out/landing-film.html?theme=dark`, { waitUntil: 'networkidle' })
await p.waitForFunction(() => window.film && window.film.scenes.length === 9)
await p.evaluate(() => window.film.theme(true)); await p.evaluate(() => window.film.rest(1)); await p.waitForTimeout(800)
await p.screenshot({ path: 'walk/poster.png' })
await b.close()

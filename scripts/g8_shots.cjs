// G8 screenshots: Build on the complaint-topics sample, the multi-label run page, Registry → Try It (a label set).
// node scripts/g8_shots.cjs <out_dir>   (needs the API on :8000 and Vite on :5190, and a registered smoke.multilabel version)
const { chromium } = require('playwright')
const UI = 'http://localhost:5190'
const API = 'http://127.0.0.1:8000'
const OUT = process.argv[2] || 'docs/progress/G8'
const j = async (p) => (await fetch(API + p)).json()

async function main() {
  const vs = (await j('/api/registry/versions')).versions
  const v = vs.filter((x) => x.status === 'ready' && x.model_name === 'smoke.multilabel').sort((a, b) => (a.created_at < b.created_at ? 1 : -1))[0]
  const pages = [
    ['1-build-multilabel', '#/build?dataset=complaint-topics', null, null],
    ['2-run-page', `#/multilabel?run=${v.run_id}`, '[data-testid=ml-pr]', null],
    ['3-registry-try-it', `#/registry?version=${v.id}&tab=Try%20It`, '[data-testid=ml-try]', '[data-testid=ml-try-predict]'],
  ]
  const browser = await chromium.launch()
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 }, deviceScaleFactor: 1 })
  const page = await ctx.newPage()
  page.on('pageerror', (e) => console.log('pageerror', String(e)))
  for (const [name, hash, wait, click] of pages) {
    await page.goto(`${UI}/${hash}`)
    await page.reload()
    await page.waitForLoadState('networkidle').catch(() => {})
    if (wait) await page.waitForSelector(wait, { timeout: 60000 })
    if (click) {
      await page.click(click)
      await page.waitForSelector('[data-testid=ml-try-scores]', { timeout: 60000 })
    }
    await page.waitForTimeout(2500)
    const h = await page.evaluate(() => { const ws = document.querySelector('.workspace'); return ws ? ws.scrollHeight : 900 })
    await page.setViewportSize({ width: 1440, height: Math.min(Math.max(900, h + 80), 3200) })
    await page.waitForTimeout(800)
    await page.screenshot({ path: `${OUT}/${name}.png` })
    await page.setViewportSize({ width: 1440, height: 900 })
    console.log('saved', name)
  }
  await browser.close()
}
main().catch((e) => { console.error(e); process.exit(1) })

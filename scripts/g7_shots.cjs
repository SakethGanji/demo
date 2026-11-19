// G7 screenshots: Build on the credit-rejects sample, the reject-inference setup, the run page (TTD comparison).
// node scripts/g7_shots.cjs <out_dir>   (needs the API on :8000 and Vite on :5190, and a finished reject-inference run)
const { chromium } = require('playwright')
const UI = 'http://localhost:5190'
const API = 'http://127.0.0.1:8000'
const OUT = process.argv[2] || 'docs/progress/G7'
const j = async (p) => (await fetch(API + p)).json()

async function main() {
  const runs = (await j('/api/runs?kind=reject_inference')).runs.filter((r) => r.status === 'succeeded')
  const pages = [
    ['1-build-reject-inference', '#/build?dataset=credit-rejects', null],
    ['2-setup', '#/reject-inference?dataset=credit-rejects&label=defaulted&decision=decision', '[data-testid=ri-setup]'],
    ['3-run-page', `#/reject-inference?run=${runs[0].id}`, '[data-testid=ri-calibration]'],
  ]
  const browser = await chromium.launch()
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 }, deviceScaleFactor: 1 })
  const page = await ctx.newPage()
  page.on('pageerror', (e) => console.log('pageerror', String(e)))
  for (const [name, hash, wait] of pages) {
    await page.goto(`${UI}/${hash}`)
    await page.reload()
    await page.waitForLoadState('networkidle').catch(() => {})
    if (wait) await page.waitForSelector(wait, { timeout: 60000 })
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

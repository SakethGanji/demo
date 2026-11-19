// G4 screenshots: Build on the fair-lending sample, Registry → Fair Lending, Explain → Fairness (BISG).
// node scripts/g4_shots.cjs <out_dir>   (needs the API on :8000 and Vite on :5190, and a registered fair-lending run)
const { chromium } = require('playwright')
const UI = 'http://localhost:5190'
const API = 'http://127.0.0.1:8000'
const OUT = process.argv[2] || 'docs/progress/G4'
const j = async (p) => (await fetch(API + p)).json()

async function main() {
  const vs = (await j('/api/registry/versions')).versions
  const v = vs.filter((x) => x.status === 'ready' && x.model_name === 'smoke.fair-lending').sort((a, b) => (a.created_at < b.created_at ? 1 : -1))[0]
  const pages = [
    ['1-build-fair-lending', '#/build?dataset=fair-lending', null],
    ['2-registry-fair-lending', `#/registry?version=${v.id}&tab=Fair%20Lending`, '[data-testid=fl-table]'],
    ['3-explain-fairness-bisg', `#/explain?run=${v.run_id}&tab=Fairness`, '[data-testid=fl-air-plot]'],
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

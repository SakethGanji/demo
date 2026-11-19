// G9 screenshots: Build → SQL query (preview of the demo join), the loaded dataset after a Refresh (lineage and
// versions), and the model card's SQL lineage.
// node scripts/g9_shots.cjs <out_dir>   (needs the API on :8000 and Vite on :5190, and a registered smoke.sql.* version)
const { chromium } = require('playwright')
const UI = 'http://localhost:5190'
const API = 'http://127.0.0.1:8000'
const OUT = process.argv[2] || 'docs/progress/G9'
const j = async (p) => (await fetch(API + p)).json()

async function shot(page, name) {
  await page.waitForTimeout(1200)
  const h = await page.evaluate(() => { const ws = document.querySelector('.workspace'); return ws ? ws.scrollHeight : 900 })
  await page.setViewportSize({ width: 1440, height: Math.min(Math.max(900, h + 80), 3200) })
  await page.waitForTimeout(600)
  await page.screenshot({ path: `${OUT}/${name}.png` })
  await page.setViewportSize({ width: 1440, height: 900 })
  console.log('saved', name)
}

async function main() {
  const browser = await chromium.launch()
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 }, deviceScaleFactor: 1 })
  const page = await ctx.newPage()
  page.on('pageerror', (e) => console.log('pageerror', String(e)))
  await page.goto(`${UI}/#/build`)
  await page.getByRole('tab', { name: 'SQL query' }).click()
  await page.waitForSelector('[data-testid=sql-tables]')
  await page.click('[data-testid=sql-preview]')
  await page.waitForSelector('[data-testid=sql-preview-table]', { timeout: 60000 })
  await shot(page, '1-build-sql-preview')
  await page.click('[data-testid=sql-load]')
  await page.waitForSelector('[data-testid=build-found]', { timeout: 120000 })
  await page.waitForSelector('[data-testid=sql-refresh]', { timeout: 60000 })
  await page.click('[data-testid=sql-refresh]')
  await page.waitForSelector('[data-testid=sql-lineage] details', { timeout: 120000 })
  await page.waitForTimeout(1500)
  await page.evaluate(() => document.querySelectorAll('[data-testid=sql-lineage] details').forEach((d) => { d.open = true }))
  await page.waitForSelector('[data-testid=build-suggestions]', { timeout: 60000 })
  await shot(page, '2-build-sql-dataset-refreshed')
  const vs = (await j('/api/registry/versions')).versions
  const v = vs.filter((x) => x.status === 'ready' && x.model_name.startsWith('smoke.sql')).sort((a, b) => (a.created_at < b.created_at ? 1 : -1))[0]
  if (v) {
    await page.goto(`${UI}/#/registry?version=${v.id}&tab=Model%20Card`)
    await page.reload()
    await page.waitForSelector('[data-testid=card-sql]', { timeout: 60000 })
    await page.locator('[data-testid=card-data]').scrollIntoViewIfNeeded()
    await page.waitForTimeout(1000)
    await page.locator('[data-testid=card-data]').screenshot({ path: `${OUT}/3-model-card-sql.png` })
    console.log('saved 3-model-card-sql')
  }
  await browser.close()
}
main().catch((e) => { console.error(e); process.exit(1) })

// G10 screenshots: the Vela page with a finished agent conversation (scenario 1 from the smoke), a live confirm card
// (the agent stopped before training), and Run It End To End reached through the old #/autopilot link.
// node scripts/g10_shots.cjs <out_dir>   (needs the API on :8000 with a planner, Vite on :5190, and `SMOKE_ONLY=agent` run once)
const { chromium } = require('playwright')
const UI = 'http://localhost:5190'
const API = 'http://127.0.0.1:8000'
const OUT = process.argv[2] || 'docs/progress/G10'
const j = async (p) => (await fetch(API + p)).json()

async function main() {
  const browser = await chromium.launch()
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 1100 }, deviceScaleFactor: 2 })
  const page = await ctx.newPage()
  page.on('pageerror', (e) => console.log('pageerror', String(e)))
  await page.addInitScript(() => localStorage.setItem('ensemble.agent.user', 'Maya Lin'))

  const ss = (await j('/api/agent/sessions')).sessions
  const s1 = ss.find((s) => s.title.startsWith('Train a default model on credit_default') && s.status === 'idle')  // the smoke's scenario 1
  if (s1) {
    await page.setViewportSize({ width: 1440, height: 1500 })
    await page.goto(`${UI}/#/vela?s=${s1.id}`)
    await page.waitForSelector('[data-testid=agent-answer]', { timeout: 30000 })
    await page.evaluate(() => { const d = [...document.querySelectorAll('[data-tool=model_doctor] details, [data-tool=compare_runs] details')]; d.forEach((x) => { x.open = false }) })
    await page.waitForTimeout(800)
    await page.evaluate(() => { const sc = document.querySelector('.ag-scroll'); if (sc) sc.scrollTop = 0 })
    await page.screenshot({ path: `${OUT}/1-vela-agent-scenario1.png` })
    await page.evaluate(() => { const sc = document.querySelector('.ag-scroll'); if (sc) sc.scrollTop = sc.scrollHeight })
    await page.waitForTimeout(400)
    console.log('saved 1', s1.id)
  }

  await page.setViewportSize({ width: 1440, height: 1000 })
  await page.goto(`${UI}/#/vela`)
  await page.waitForSelector('[data-testid=agent-input]')
  await page.fill('[data-testid=agent-input]', 'Train a churn model on account-churn and tell me how good it is.')
  await page.click('[data-testid=agent-send]')
  await page.waitForSelector('[data-testid=agent-confirm]', { timeout: 180000 })
  await page.waitForTimeout(600)
  await page.screenshot({ path: `${OUT}/2-vela-agent-confirm.png` })
  console.log('saved 2')
  await page.click('[data-testid=agent-skip]')
  await page.waitForTimeout(500)

  await page.goto(`${UI}/#/autopilot`)
  await page.waitForSelector('[data-testid=ap-brief]', { timeout: 30000 })
  await page.waitForTimeout(1500)
  console.log('redirected to', page.url())
  await page.screenshot({ path: `${OUT}/3-vela-run-it-end-to-end.png` })
  console.log('saved 3')
  await browser.close()
}
main().catch((e) => { console.error(e); process.exit(1) })

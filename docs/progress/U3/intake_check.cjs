// U3 extra check (not a suite): a second F-PII page, Smart Intake on Build, as Ana: masked evidence → Unmask (reason).
//   node intake_check.cjs <shots dir> <calls.txt> <state.json> <version id>
const { chromium } = require('playwright')
const fs = require('fs')
const path = require('path')
const [out, callsOut, stateFile, vid] = process.argv.slice(2)
const S = JSON.parse(fs.readFileSync(stateFile, 'utf8'))
;(async () => {
  const b = await chromium.launch()
  const p = await b.newPage({ viewport: { width: 1366, height: 900 }, deviceScaleFactor: 1 })
  const calls = ['\n## 7. Ana · Build (Smart Intake) on the newest version: masked statistics → Unmask (reason)']
  const errors = []
  p.on('console', (m) => { if (m.type() === 'error') errors.push(m.text()) })
  p.on('response', async (r) => { const u = r.url(); if (/\/api\/datasets\/[^/]+\/intake/.test(u)) { let j = ''; try { j = JSON.stringify(await r.json()) } catch { /* */ } calls.push(`${r.request().method()} ${u.replace(/^.*\/api/, '/api')} → ${r.status()} masked_columns=${(j.match(/"masked_columns":\[[^\]]*\]/) || [''])[0]} unmasked=${(j.match(/"unmasked":[a-z{][^,]*/) || [''])[0]}`) } })
  await p.goto(`http://127.0.0.1:5190/#/build?dataset=${vid}`)
  await p.evaluate(([w, t]) => { localStorage.setItem('ensemble.actor', w); localStorage.setItem('ensemble.team', t) }, ['Ana', S.cr]); await p.reload()
  const bar = '[data-testid="mask-bar"][data-kind="dataset"]'
  await p.waitForSelector(bar, { timeout: 120000 })
  await p.locator(bar).scrollIntoViewIfNeeded()
  await p.screenshot({ path: path.join(out, '19-intake-masked.png'), clip: await p.locator(bar).boundingBox() })
  await p.waitForFunction((s) => !document.querySelector(s + ' [data-testid="unmask"]').disabled, bar, { timeout: 15000 })
  await p.click(`${bar} [data-testid="unmask"]`)
  await p.fill(`${bar} [data-testid="unmask-reason"]`, 'checking the intake roles')
  await p.click(`${bar} [data-testid="unmask-go"]`)
  await p.waitForSelector(`${bar} [data-testid="unmasked-on"]`, { timeout: 120000 })
  await p.screenshot({ path: path.join(out, '20-intake-unmasked.png'), clip: await p.locator(bar).boundingBox() })
  calls.push(`console errors: ${errors.length}`, ...errors)
  fs.appendFileSync(callsOut, calls.join('\n') + '\n')
  await b.close()
})().catch((e) => { console.error(e); process.exitCode = 1 })

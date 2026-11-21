// DL3: screenshots of edit-as-version and the integrity remedy, 1366 wide: the edit grid, pending operations, the
// preview, the resulting diff and history; a version whose file changed on disk (with "Create a new version from this
// file"); the run page and lineage with the sha256 of the bytes each fit read.
//   node scripts/dl3_shots.cjs <out> <ids.json> <data dir> [ui=http://127.0.0.1:5190] [api=http://127.0.0.1:8000]
// ids.json: { "D": root dataset, "V3": a version to tamper with for the shot (restored after), "RUN_OK", "RUN_REFUSED" }
const { chromium } = require('playwright')
const fs = require('fs')
const path = require('path')
const [out, idsFile, dataDir] = process.argv.slice(2)
const ui = process.argv[5] || 'http://127.0.0.1:5190'
const api = process.argv[6] || 'http://127.0.0.1:8000'
const I = JSON.parse(fs.readFileSync(idsFile, 'utf8'))

;(async () => {
  fs.mkdirSync(out, { recursive: true })
  const b = await chromium.launch()
  const p = await b.newPage({ viewport: { width: 1366, height: 900 }, deviceScaleFactor: 1 })
  const errors = []
  p.on('console', (m) => { if (m.type() === 'error') errors.push(m.text()) })
  await p.goto(`${ui}/#/models`); await p.evaluate(() => localStorage.setItem('ensemble.actor', 'bob'))
  const shot = async (name, el) => {
    await p.waitForTimeout(700)
    if (el) await p.locator(el).first().scrollIntoViewIfNeeded()
    const opts = el ? { clip: await p.locator(el).first().boundingBox() } : {}
    await p.screenshot({ path: path.join(out, `${name}.png`), ...opts }); console.log('shot', name)
  }
  const page = async (id) => { await p.goto(`${ui}/#/models?tab=Data&dataset=${id}`); await p.reload(); await p.waitForSelector('[data-testid="data-versions"]', { timeout: 60000 }) }

  // 1. the editor on v1: the row grid with search and pages
  await page(I.D)
  await p.click('[data-testid="data-v-1"]')
  await p.click('[data-testid="data-edit-1"]')
  await p.waitForSelector('[data-testid="edit-grid"]', { timeout: 60000 })
  await shot('01-edit-grid', '[data-testid="data-editor"]')
  // 2. pending operations: a cell, a deleted row, a new column by formula, a rename
  await p.click('[data-testid="edit-cell-0-income"]')
  await p.fill('[data-testid="edit-cell-input"]', '52000')
  await p.keyboard.press('Enter')
  await p.click('[data-testid="edit-cell-6-region"]')
  await p.fill('[data-testid="edit-cell-input"]', 'north-east')
  await p.keyboard.press('Enter')
  await p.click('[data-testid="edit-del-4"]')
  await p.fill('[data-testid="edit-newcol-name"]', 'lti')
  await p.fill('[data-testid="edit-newcol-expr"]', 'loan_amount / income')
  await p.click('[data-testid="edit-newcol-add"]')
  await p.selectOption('[data-testid="edit-col"]', 'credit_score')
  await p.fill('[data-testid="edit-col-arg"]', 'fico')
  await p.click('[data-testid="edit-col-add"]')
  await shot('02-pending-ops', '[data-testid="data-editor"]')
  // 3. preview (nothing written), then the reason and Create vN
  await p.click('[data-testid="edit-preview-btn"]')
  await p.waitForSelector('[data-testid="edit-preview"]', { timeout: 60000 })
  await p.fill('[data-testid="edit-reason"]', 'CHG-2214 corrected income, removed a test row, LTI for the scorecard')
  await shot('03-preview', '[data-testid="edit-pending"]')
  await p.click('[data-testid="edit-create"]')
  await p.waitForSelector('[data-testid="data-diff"]', { timeout: 60000 })
  await p.waitForSelector('[data-testid="diff-edit"]', { timeout: 60000 })
  await shot('04-created-message', '[data-testid="data-page"] > .card')
  await shot('05-diff-of-the-edit', '[data-testid="data-diff"]')
  await shot('06-versions-history', '[data-testid="data-versions"]')

  // 7. a file changed outside the app: flagged, with the remedy (restored right after)
  const f = path.join(dataDir, 'datasets', I.V3, 'data.csv')
  const orig = fs.readFileSync(f, 'utf8')
  const L = orig.split('\n'); const c = L[7].split(','); c[1] = '123456.0'; L[7] = c.join(','); fs.writeFileSync(f, L.join('\n'))
  await fetch(`${api}/api/datasets/${I.V3}/verify`, { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ by: 'bob' }) })
  await page(I.D)
  await p.click(`[data-id="${I.V3}"]`)
  await p.click('[data-testid^="data-adopt-"]')
  await p.fill('[data-testid="data-adopt-reason"]', 'keep the hand-corrected file as its own version')
  await shot('07-integrity-mismatch-remedy', '[data-testid="data-versions"]')
  fs.writeFileSync(f, orig)
  await fetch(`${api}/api/datasets/${I.V3}/verify`, { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ by: 'bob' }) })

  // 8. lineage: each run with the sha256 of the bytes it read
  await page(I.D)
  await shot('08-lineage-data-sha', '[data-testid="project-tree"]')
  // 9. the run page: the data sha256, and the refused run on the tampered file
  for (const [name, id] of [['09-run-data-sha', I.RUN_OK], ['10-run-refused-tampered', I.RUN_REFUSED]]) {
    await p.goto(`${ui}/#/models?run=${id}`); await p.waitForSelector('[data-testid="run-page"]', { timeout: 60000 })
    await p.waitForTimeout(1500)
    await p.screenshot({ path: path.join(out, `${name}.png`), clip: { x: 0, y: 0, width: 1366, height: 420 } }); console.log('shot', name)
  }
  console.log('console errors:', errors.filter((e) => !/40[0-9]|409|410/.test(e)).length, errors.slice(0, 5))
  await b.close()
})()

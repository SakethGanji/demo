// DL2: screenshots of the governance fixes, 1366 wide: upload preview (warnings, clean, created as default with a
// reason), versions (reason, deleted v2 without ✓, archived), usage with a retired model, archive/delete (v1 only,
// holdout sample, withdraw my request), the retire flow in the Registry (ask, four eyes, approve), Build's note on an
// archived version, the list at 1366, search by deleted versions.
//   node scripts/dl2_shots.cjs <out> <ids.json> <dir with loans_tiny.csv and loans_v2.csv> [ui=http://127.0.0.1:5190]
const { chromium } = require('playwright')
const fs = require('fs')
const path = require('path')
const [out, idsFile, csvDir] = process.argv.slice(2)
const ui = process.argv[5] || 'http://127.0.0.1:5190'
const I = JSON.parse(fs.readFileSync(idsFile, 'utf8'))

;(async () => {
  fs.mkdirSync(out, { recursive: true })
  const b = await chromium.launch()
  const p = await b.newPage({ viewport: { width: 1366, height: 900 }, deviceScaleFactor: 1 })
  const errors = []
  p.on('console', (m) => { if (m.type() === 'error') errors.push(m.text()) })
  const as = async (who) => { await p.evaluate((w) => localStorage.setItem('ensemble.actor', w), who) }
  await p.goto(`${ui}/#/models`); await as('alice')
  const shot = async (name, el) => {
    await p.waitForTimeout(600)
    const opts = el ? { clip: await p.locator(el).boundingBox() } : {}
    await p.screenshot({ path: path.join(out, `${name}.png`), ...opts }); console.log('shot', name)
  }
  const page = async (id) => { await p.goto(`${ui}/#/models?tab=Data&dataset=${id}`); await p.reload(); await p.waitForSelector('[data-testid="data-versions"]', { timeout: 60000 }) }

  // 1. upload preview with warnings: created only, never "make default"
  await page(I.V1)
  await p.setInputFiles('[data-testid="data-upload-file"]', path.join(csvDir, 'loans_tiny.csv'))
  await p.waitForSelector('[data-testid="data-preview"]', { timeout: 60000 })
  await p.locator('[data-testid="data-preview"]').scrollIntoViewIfNeeded()
  await shot('01-upload-preview-warnings')
  await p.getByRole('button', { name: 'Cancel' }).click()
  // 2. a clean file: "Create … and make it the default", with a change ticket
  await p.setInputFiles('[data-testid="data-upload-file"]', path.join(csvDir, 'loans_v2.csv'))
  await p.waitForSelector('[data-testid="data-create-default"]', { timeout: 60000 })
  await p.fill('[data-testid="data-upload-reason"]', 'CHG-2210 re-delivered November extract')
  await shot('02-upload-preview-clean')
  await p.click('[data-testid="data-create-default"]')
  await p.waitForSelector('[data-testid="data-msg"]', { timeout: 120000 })
  await shot('03-created-and-default')
  // 3. versions: reasons, deleted v2 (no ✓), the default; diff parent → child of the default
  await p.locator('[data-testid="data-versions"]').scrollIntoViewIfNeeded()
  await shot('04-versions', '[data-testid="data-versions"]')
  await p.waitForSelector('[data-testid="diff-changed"]', { timeout: 60000 })
  await shot('05-default-diff-parent-to-child', '[data-testid="data-diff"] .card-head')
  // 4. used by v1: the champion, a retired model; archive/delete card with v1 only and the holdout sample
  await p.locator('[data-testid="data-v-1"]').click()
  await p.locator('[data-testid="data-usage"]').scrollIntoViewIfNeeded()
  await shot('06-used-by-with-retired')
  // 5. ask to delete the 3-row v3 as alice, then the requester's own "Withdraw my request"
  await p.selectOption('[data-testid="data-remove-target"]', I.V3)
  await p.fill('[data-testid="data-remove-reason"]', 'careless 3-row upload')
  await p.click('[data-testid="data-delete"]')
  await p.waitForSelector('[data-testid="data-req-withdraw"]', { timeout: 30000 })
  await p.locator('[data-testid="data-remove"]').scrollIntoViewIfNeeded()
  await p.selectOption('[data-testid="data-remove-target"]', `${I.V1}::only`)
  await p.check('[data-testid="data-freeze"]')
  await shot('07-remove-v1-only-and-withdraw', '[data-testid="data-remove"]')
  await p.click('[data-testid="data-req-withdraw"]')
  await p.waitForSelector('[data-testid="data-msg"]')
  // 6. Registry: retiring the champion is refused; retire another version, four eyes
  await p.goto(`${ui}/#/registry?version=${I.MV1}`); await p.waitForSelector('[data-testid="retire-box"]', { timeout: 60000 })
  await p.fill('[data-testid="review-comment"]', 'end of life: replaced')
  await p.click('[data-testid="retire-ask"]')
  await p.waitForSelector('[data-testid="retire-msg"]')
  await p.locator('[data-testid="review-card"]').scrollIntoViewIfNeeded()
  await shot('08-retire-champion-refused', '[data-testid="review-card"]')
  await p.goto(`${ui}/#/registry?version=${I.MV3}`); await p.reload(); await p.waitForSelector('[data-testid="retire-ask"]', { timeout: 60000 })
  await p.fill('[data-testid="review-comment"]', 'alt model not used: end of life')
  await p.click('[data-testid="retire-ask"]')
  await p.waitForSelector('[data-testid="retire-withdraw"]')
  await p.locator('[data-testid="review-card"]').scrollIntoViewIfNeeded()
  await shot('09-retire-asked-by-alice', '[data-testid="review-card"]')
  await as('carol'); await p.reload(); await p.waitForSelector('[data-testid="retire-approve"]', { timeout: 60000 })
  await p.locator('[data-testid="review-card"]').scrollIntoViewIfNeeded()
  await shot('10-retire-approve-as-carol', '[data-testid="review-card"]')
  await p.click('[data-testid="retire-approve"]')
  await p.waitForSelector('[data-testid="retired-line"]', { timeout: 30000 })
  await shot('11-retired', '[data-testid="review-card"]')
  await as('alice')
  // 7. Build: an archived version says so
  await p.goto(`${ui}/#/build?dataset=${I.PB}`); await p.reload()
  await p.waitForSelector('[data-testid="build-archived-note"]', { timeout: 60000 })
  await shot('12-build-archived-note')
  // 8. the list at 1366 (last change fits) and search by deleted versions
  await p.goto(`${ui}/#/models?tab=Data`); await p.waitForSelector('[data-testid="data-table"]')
  await shot('13-data-list-1366', '[data-testid="data-view"] > .card')
  await p.getByRole('button', { name: 'deleted', exact: true }).click(); await p.waitForTimeout(800)
  await shot('14-search-deleted-versions', '[data-testid="data-view"] > .card')
  console.log('console errors:', errors.filter((e) => !/409|410|403|Failed to load resource/.test(e)))
  await b.close()
})()

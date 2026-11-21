// DL4: screenshots at 1366 wide: Build recognises a CSV as a new version of a dataset already here (the prompt, the
// preview, the result); a split file changed on disk (verify, the banner, restore as drawn); an edit that drops the
// target refused; ZIP codes kept as text in the editor's grid.
//   node scripts/dl4_shots.cjs <out> <dataset root id> <data dir> <csv to drop in Build> [ui=http://127.0.0.1:5190]
const { chromium } = require('playwright')
const fs = require('fs')
const path = require('path')
const [out, D, dataDir, csv] = process.argv.slice(2)
const ui = process.argv[6] || 'http://127.0.0.1:5190'

;(async () => {
  fs.mkdirSync(out, { recursive: true })
  const b = await chromium.launch()
  const p = await b.newPage({ viewport: { width: 1366, height: 900 }, deviceScaleFactor: 1 })
  const errors = []
  p.on('console', (m) => { if (m.type() === 'error') errors.push(m.text()) })
  await p.goto(`${ui}/#/build`); await p.evaluate(() => localStorage.setItem('ensemble.actor', 'bob')); await p.reload()
  const shot = async (name, el) => {
    await p.waitForTimeout(700)
    if (el) await p.locator(el).first().scrollIntoViewIfNeeded()
    const opts = el ? { clip: await p.locator(el).first().boundingBox() } : {}
    await p.screenshot({ path: path.join(out, `${name}.png`), ...opts }); console.log('shot', name)
  }
  // 1. Build: drop a CSV that looks like an existing dataset → one clear choice
  await p.waitForSelector('[data-testid="build"] input[type=file]', { state: 'attached', timeout: 60000 })
  await p.locator('[data-testid="build"] input[type=file]').first().setInputFiles(csv)
  await p.waitForSelector('[data-testid="build-match"]', { timeout: 60000 })
  await shot('01-build-add-as-version-prompt')
  // 2. Add as new version → the same preview, reason and rules as Data → Upload a new version
  await p.click('[data-testid="match-add"]')
  await p.waitForSelector('[data-testid="data-preview"]', { timeout: 120000 })
  await p.fill('[data-testid="data-upload-reason"]', 'CHG-5001 November extract')
  await shot('02-build-add-as-version-preview', '[data-testid="build-match"]')
  await p.click('[data-testid="data-create-version"]')
  await p.waitForSelector('[data-testid="dr-made"]', { timeout: 120000 })
  await shot('03-build-version-created')
  // 4. a split file changed on disk: verify says SPLIT MISMATCH; the banner offers Restore the split as drawn
  const page = async () => { await p.goto(`${ui}/#/models?tab=Data&dataset=${D}`); await p.reload(); await p.waitForSelector('[data-testid="data-versions"]', { timeout: 60000 }) }
  for (const f of fs.readdirSync(path.join(dataDir, 'datasets', D, 'splits'))) {
    const fp = path.join(dataDir, 'datasets', D, 'splits', f)
    const sp = JSON.parse(fs.readFileSync(fp, 'utf8')); sp.holdout = sp.holdout.slice(0, 50); fs.writeFileSync(fp, JSON.stringify(sp))
  }
  await page()
  await p.click('[data-testid="data-v-1"]')
  await p.click('[data-testid="data-verify-1"]')
  await p.waitForSelector('[data-testid="data-integrity-1"]', { timeout: 60000 })
  await shot('04-split-mismatch-banner', '[data-testid="data-versions"]')
  await p.click('[data-testid="data-restore-splits-1"]')
  await p.waitForSelector('[data-testid="data-msg"]', { timeout: 60000 })
  await shot('05-split-restored', '[data-testid="data-page"] > .card')
  // 6. an edit that drops the target is refused, in words
  await page()
  await p.click('[data-testid="data-v-1"]')
  await p.click('[data-testid="data-edit-1"]')
  await p.waitForSelector('[data-testid="edit-grid"]', { timeout: 60000 })
  await p.fill('[data-testid="edit-q"]', '02139')
  await p.waitForTimeout(1200)
  await shot('06-edit-grid-zip-text', '[data-testid="data-editor"]')
  await p.selectOption('[data-testid="edit-col"]', 'default')
  await p.selectOption('[data-testid="edit-col-action"]', 'drop_column')
  await p.click('[data-testid="edit-col-add"]')
  await p.click('[data-testid="edit-preview-btn"]')
  await p.waitForSelector('[data-testid="data-msg"]', { timeout: 60000 })
  await shot('07-edit-drop-target-refused', '[data-testid="data-page"] > .card')
  console.log('console errors:', errors.length, errors.slice(0, 5))
  await b.close()
})()

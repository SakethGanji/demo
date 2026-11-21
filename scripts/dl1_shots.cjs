// DL1: screenshots of Models → Data (dataset list, versions, diff, used by, card, archive/delete, audit), Build's
// version strip and the lineage tree's link, 1366 wide.
//   node scripts/dl1_shots.cjs docs/progress/DL1 <root dataset id> [ui=http://127.0.0.1:5190]
const { chromium } = require('playwright')
const fs = require('fs')
const path = require('path')
const out = process.argv[2] || 'docs/progress/DL1'
const root = process.argv[3]
const ui = process.argv[4] || 'http://127.0.0.1:5190'

;(async () => {
  fs.mkdirSync(out, { recursive: true })
  const b = await chromium.launch()
  const p = await b.newPage({ viewport: { width: 1366, height: 900 }, deviceScaleFactor: 1 })
  const errors = []
  p.on('console', (m) => { if (m.type() === 'error') errors.push(m.text()) })
  await p.goto(`${ui}/#/models`)
  await p.evaluate(() => localStorage.setItem('ensemble.actor', 'alice'))
  const shot = async (name, opts = {}) => { await p.waitForTimeout(500); await p.screenshot({ path: path.join(out, `${name}.png`), ...opts }); console.log('shot', name) }

  await p.goto(`${ui}/#/models?tab=Data&dataset=${root}`)
  await p.waitForSelector('[data-testid="data-versions"]', { timeout: 60000 })
  await p.waitForSelector('[data-testid="data-diff"]', { timeout: 60000 })
  await shot('01-data-list-and-dataset')
  await p.locator('[data-testid="data-versions"]').scrollIntoViewIfNeeded()
  await shot('02-versions', { clip: await p.locator('[data-testid="data-versions"]').boundingBox() })
  // diff v1 → v2
  await p.locator('[data-testid="data-v-1"] input[name="dsA"]').check()
  await p.locator('[data-testid="data-v-2"] input[name="dsB"]').check()
  await p.waitForSelector('[data-testid="diff-changed"]', { timeout: 60000 })
  await p.locator('[data-testid="data-diff"] details summary').click()
  await p.locator('[data-testid="data-diff"]').scrollIntoViewIfNeeded()
  await shot('03-diff-v1-v2', { fullPage: false })
  const diff = await p.locator('[data-testid="data-diff"]').boundingBox()
  await p.screenshot({ path: path.join(out, '03b-diff-card.png'), clip: diff, fullPage: true }); console.log('shot 03b-diff-card')
  // used by (v2) + card
  await p.locator('[data-testid="data-v-2"]').click()
  await p.locator('[data-testid="data-usage"]').scrollIntoViewIfNeeded()
  await shot('04-used-by-and-card')
  // refused archive of the champion's version
  await p.locator('[data-testid="data-v-2"]').click(); await p.locator('[data-testid="data-archive-2"]').click()
  await p.waitForSelector('[data-testid="data-msg"]')
  await p.locator('[data-testid="data-msg"]').scrollIntoViewIfNeeded()
  await shot('05-archive-refused-in-use')
  // archive / delete card, audit, lineage
  await p.locator('[data-testid="data-remove"]').scrollIntoViewIfNeeded()
  await shot('06-delete-and-audit')
  await p.locator('.project-tree').scrollIntoViewIfNeeded()
  await shot('07-lineage-in-data')
  // Build: the picker lists datasets; the strip lists versions
  await p.goto(`${ui}/#/build?dataset=${root}`)
  await p.waitForSelector('[data-testid="build-versions"]', { timeout: 60000 })
  await shot('08-build-version-strip')
  // the lineage tree links to Data
  await p.goto(`${ui}/#/models?tab=Lineage`)
  await p.waitForSelector('[data-testid="models-lineage"]', { timeout: 60000 })
  await p.selectOption('[data-testid="project-switch"]', { index: 1 }).catch(() => {})
  await p.waitForSelector(`[data-testid="pt-data-${root}"]`, { timeout: 60000 }).catch(() => {})
  await shot('09-lineage-tree-data-link')
  // search
  await p.goto(`${ui}/#/models?tab=Data`)
  await p.waitForSelector('[data-testid="data-table"]')
  await p.fill('[data-testid="data-tag"]', 'retail')
  await p.waitForTimeout(800)
  await shot('10-search-by-tag')
  console.log('console errors:', errors.filter((e) => !/409|410|Failed to load resource/.test(e)))
  await b.close()
})()

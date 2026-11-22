// U4 read-only tour (not a suite): the data-platform screens the U2/U3 walks don't photograph, on a data dir the U2
// walk left behind. Nothing is committed: an upload is checked, then discarded; a join's frame is a cache hit.
//   node tour.cjs <ui base> <out dir> <prefix> <state.json> [only]
// state.json: {"root": ds_…, "cr": tm_…, "co": tm_…, "version": mv_…, "files": <U2 files dir>}
const { chromium } = require('playwright')
const fs = require('fs')
const path = require('path')
const [ui, out, prefix, stateFile, only] = process.argv.slice(2)
const S = JSON.parse(fs.readFileSync(stateFile, 'utf8'))
const tid = (s) => `[data-testid="${s}"]`

;(async () => {
  fs.mkdirSync(out, { recursive: true })
  const b = await chromium.launch()
  const p = await b.newPage({ viewport: { width: 1366, height: 900 }, deviceScaleFactor: Number(process.env.DSF || 2) })
  const errors = []
  p.on('console', (m) => { if (m.type() === 'error') errors.push(`[${p.url().replace(ui, '')}] ${m.text()}`) })
  p.on('pageerror', (e) => errors.push(`pageerror ${e.message}`))
  const as = async (who, team, hash) => {
    await p.goto(`${ui}/#/${hash}`)
    await p.evaluate(([w, t]) => { localStorage.setItem('ensemble.actor', w); localStorage.setItem('ensemble.team', t) }, [who, team]); await p.reload()
    await p.waitForTimeout(1200)
  }
  const shot = async (name, sel, maxH = 1800) => {
    if (only && !name.startsWith(only)) return
    await p.waitForTimeout(700)
    const l = p.locator(sel).first(); await l.scrollIntoViewIfNeeded()
    await l.evaluate((e) => { const r = e.getBoundingClientRect(); if (r.top < 70 || r.top > 200) { let s = e.parentElement; while (s && s.scrollHeight <= s.clientHeight) s = s.parentElement; (s || window).scrollBy(0, r.top - 80) } })
    await p.waitForTimeout(300)
    const bb = await l.boundingBox()
    await p.screenshot({ path: path.join(out, `${prefix}-${name}.png`), clip: { x: bb.x, y: bb.y, width: Math.min(bb.width, 1366), height: Math.min(bb.height, maxH) } })
    console.log('shot', `${prefix}-${name}`)
  }
  const ds = `build?view=data&dataset=${S.root}`
  try {
    await as('Ana', S.cr, 'build?view=data')
    await p.waitForSelector(tid('data-table'), { timeout: 30000 })
    await shot('t01-catalog', tid('data-view') + ' > .card')

    await as('Ana', S.cr, ds)
    await p.waitForSelector(tid('data-versions'), { timeout: 30000 })
    await p.waitForSelector(tid('data-tables'), { timeout: 30000 })
    await shot('t02-dataset-head', tid('data-page') + ' > .card')
    await shot('t03-versions', tid('data-versions'))
    await shot('t04-tables', tid('data-tables'))
    await p.waitForSelector(tid('data-diff'), { timeout: 60000 })
    await shot('t05-diff', tid('data-diff'))
    await p.waitForSelector(tid('data-usage'), { timeout: 30000 }); await p.waitForTimeout(1500)
    await shot('t06-usage-card', tid('data-usage') + ' >> xpath=..')
    await shot('t07-remove-audit', tid('data-remove') + ' >> xpath=..')
    await shot('t08-lineage', tid('project-tree') + ' >> xpath=../..')

    // joins: the saved join, its coverage, then its frame (a cache hit: the walk built it)
    await p.click(`${tid('join-list')} button >> nth=0`)
    await p.waitForSelector(tid('join-coverage'), { timeout: 60000 })
    await shot('t09-join-coverage', tid('join-builder'))
    await p.click(tid('join-frame'))
    await p.waitForSelector(tid('frame-preview'), { timeout: 120000 })
    await shot('t10-frame', tid('frame-result'))
    await shot('t11-governance', tid('governance'))
    await shot('t12-purge-card', tid('purge-card'))

    // the staged upload of two files: check, then discard (nothing is written)
    await as('Ana', S.cr, 'build?view=data')
    await p.waitForSelector(tid('data-table'), { timeout: 30000 })
    await p.click(tid('data-upload'))
    await p.setInputFiles(tid('up-file'), [path.join(S.files, 'book.xlsx'), path.join(S.files, 'bureau.csv')])
    await p.waitForSelector(`${tid('up-project')} option:has-text("PD 2026")`, { state: 'attached', timeout: 30000 })
    await p.selectOption(tid('up-project'), { label: 'PD 2026' })
    if (!only || 't13'.startsWith(only.slice(0, 3))) {
      await p.click(tid('up-check'))
      await p.waitForSelector(tid('up-plan'), { timeout: 120000 })
      await shot('t13-upload-plan', tid('up-plan'))
      await shot('t14-upload-check', tid('up-check-result'), 1100)
      await p.click(tid('up-discard'))
    }
    // the delta: a change set as the next version → Apply as delta → the plan with its counts; then discard
    await as('Ana', S.cr, ds)
    await p.waitForSelector(tid('data-upload-version'), { timeout: 30000 })
    if (!only || only.startsWith('t15') || only.startsWith('t16')) {
      await p.click(tid('data-upload-version'))
      await p.setInputFiles(tid('up-file'), path.join(S.files, 'bureau_delta.csv'))
      await p.click(tid('up-check'))
      await p.waitForSelector(tid('up-delta-hint'), { timeout: 120000 })
      await shot('t15-delta-hint', tid('up-flow'), 900)
      await p.click(tid('up-apply-delta'))
      await p.waitForSelector(tid('up-delta-bureau'), { timeout: 120000 })
      await shot('t16-delta-plan', tid('up-plan'))
      await p.click(tid('up-discard'))
    }

    // Carl: the shared version, masked, Unmask greyed
    await as('Carl', S.co, ds)
    await p.waitForSelector(tid('data-versions'), { timeout: 30000 })
    const ed = p.locator('[data-testid^="data-edit-"]').first()
    if (await ed.count()) { await ed.click(); await p.waitForSelector(tid('data-editor'), { timeout: 60000 }); await p.waitForTimeout(1500); await shot('t17-carl-editor', tid('data-editor'), 1000) }

    // Settings › Teams as Ada
    await as('Ada', S.cr, 'settings')
    await p.waitForSelector(tid('members-table'), { timeout: 30000 })
    await shot('t18-teams', tid('settings-teams'))

    // Registry: the join model's card (inputs, parity) and the batch form
    await as('Ana', S.cr, `registry?version=${S.version}`)
    for (let i = 0; i < 30 && !(await p.locator('[role="tab"]:has-text("Model Card")').count()); i++) await p.waitForTimeout(1000)
    await p.click('[role="tab"]:has-text("Model Card")')
    await p.waitForSelector(tid('card-inputs'), { timeout: 60000 })
    await shot('t19-card-inputs', tid('card-inputs'))
    await p.click('[role="tab"]:has-text("Batch & Drift")')
    await p.waitForSelector(tid('batch-files'), { state: 'attached', timeout: 60000 })
    await p.setInputFiles(tid('batch-files'), ['applications', 'payments', 'bureau'].map((t) => path.join(S.files, 'batch', `${t}.csv`)))
    await p.waitForSelector(tid('batch-table-map'), { timeout: 30000 })
    await shot('t20-batch-form', tid('batch-table-map') + ' >> xpath=..')
  } catch (e) { console.error('FAILED', e.message); await p.screenshot({ path: path.join(out, `${prefix}-zz-fail.png`), fullPage: false }) }
  console.log('errors', errors.length, errors)
  await b.close()
})()

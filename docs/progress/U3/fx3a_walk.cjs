// U3 (F-X3a hand-off) ad-hoc walk, not a suite: 1366 × 900, deviceScaleFactor 1. Three change sets on `book`:
// lossy_cast blocked → date_ambiguous → pick a format → commit → a wider-key delta → needs_ack → acknowledge → commit.
//   node fx3a_walk.cjs <shots dir> <files dir> <calls.txt> <state.json from fx3a_setup.py>
const { chromium } = require('playwright')
const fs = require('fs')
const path = require('path')
const [out, files, callsOut, stateFile] = process.argv.slice(2)
const S = JSON.parse(fs.readFileSync(stateFile, 'utf8'))
const ui = 'http://127.0.0.1:5190'
;(async () => {
  const b = await chromium.launch()
  const p = await b.newPage({ viewport: { width: 1366, height: 900 }, deviceScaleFactor: 1 })
  const calls = []
  const mark = (s) => { calls.push(`\n## ${s}`); console.log('##', s) }
  const errors = []
  p.on('console', (m) => { if (m.type() === 'error') errors.push(`[${p.url().replace(ui, '')}] ${m.text()}`) })
  p.on('response', async (r) => {
    const u = r.url(); if (!/\/api\/uploads/.test(u)) return
    const q = r.request(); let body = q.postData() || ''
    if ((q.headers()['content-type'] || '').startsWith('multipart')) body = '(multipart)'
    let res = ''; try { const j = await r.json(); res = JSON.stringify({ ...(j.blocks ? { blocks: j.blocks.map((x) => [x.code, x.column ?? x.columns]) } : {}), ...(j.warnings ? { warnings: j.warnings.map((x) => [x.code, x.needs_ack ?? null]) } : {}), ...(j.version_id ? { version_id: j.version_id, deltas: j.deltas } : {}), ...(j.detail ? { detail: j.detail.code ?? j.detail } : {}) }).slice(0, 400) } catch { /* */ }
    calls.push(`${q.method()} ${u.replace(/^.*\/api/, '/api')} ${body.slice(0, 260)} → ${r.status()} ${res}`)
  })
  const tid = (s) => `[data-testid="${s}"]`
  const shot = async (name, el) => {
    await p.waitForTimeout(600); const l = p.locator(el).first(); await l.scrollIntoViewIfNeeded()
    const bb = await l.boundingBox(); await p.screenshot({ path: path.join(out, `${name}.png`), clip: { x: bb.x, y: Math.max(bb.y, 0), width: Math.min(bb.width, 1366), height: Math.min(bb.height, 2400) } }); console.log('shot', name)
  }
  const openDs = async () => {
    await p.goto(`${ui}/#/build?view=data&dataset=${S.root}`)
    await p.evaluate(([w, t]) => { localStorage.setItem('ensemble.actor', w); localStorage.setItem('ensemble.team', t) }, ['Ana', S.cr]); await p.reload()
    await p.waitForSelector(tid('data-page'), { timeout: 30000 })
  }
  const asDelta = async (file) => {
    await openDs()
    await p.click(tid('data-upload-version'))
    await p.setInputFiles(tid('up-file'), [path.join(files, file)])
    await p.click(tid('up-check'))
    await p.waitForSelector(tid('up-delta-open'), { timeout: 120000 })
    await p.click(tid('up-delta-open'))
    await p.selectOption(tid('up-delta-table'), 'customers')
    await p.click(tid('up-apply-delta'))
    await p.waitForSelector(tid('up-plan'), { timeout: 120000 })
  }
  try {
    mark('A. Ana · customers_lossy.csv as a delta of customers (score 505.5, 506.25 into BIGINT) → blocked lossy_cast')
    await asDelta('customers_lossy.csv')
    await p.waitForSelector('[data-testid^="up-issue-lossy_cast"]', { timeout: 60000 })
    if (!(await p.isDisabled(tid('up-commit')))) throw new Error('Create is not disabled under lossy_cast')
    await shot('21-lossy-cast-blocked', tid('up-blocks'))
    await p.click(tid('up-discard'))

    mark('B. customers_dates.csv (since 03/02/2021, 04/05/2021, 11/12/2021) → blocked date_ambiguous → day first → check → Create')
    await asDelta('customers_dates.csv')
    await p.waitForSelector(tid('up-issue-date-ambiguous'), { timeout: 60000 })
    await p.selectOption(tid('up-date-format-since'), '%d/%m/%Y')
    await shot('22-date-ambiguous-picker', tid('up-blocks'))
    await p.click(tid('up-date-apply'))
    await p.waitForSelector(tid('up-delta-customers'), { timeout: 120000 })
    await p.waitForFunction(() => !document.querySelector('[data-testid="up-blocks"]'), null, { timeout: 60000 })
    await shot('23-date-format-plan', tid('up-plan'))
    await p.click(tid('up-commit'))
    await p.waitForSelector(`${tid('data-msg')}:has-text("Delta on customers")`, { timeout: 120000 })
    await shot('24-date-created', tid('data-msg'))

    mark('C. customers_widerkey.csv keyed on [cust_id, region] → key_invalidated + relation_dropped (needs_ack) → acknowledge → Create')
    await asDelta('customers_widerkey.csv')
    await p.fill(tid('up-key-customers'), 'cust_id, region'); await p.press(tid('up-key-customers'), 'Tab')
    await p.click(tid('up-recheck'))
    await p.waitForSelector(tid('up-integrity'), { timeout: 120000 })
    if (!(await p.isDisabled(tid('up-commit')))) throw new Error('Create is not disabled before the acknowledgement')
    await shot('25-needs-ack', tid('up-integrity'))
    await p.check(tid('up-ack-key_invalidated')); await p.check(tid('up-ack-relation_dropped'))
    await p.click(tid('up-commit'))
    await p.waitForSelector(`${tid('data-msg')}:has-text("Delta on customers")`, { timeout: 120000 })
    await shot('26-ack-created', tid('data-msg'))
  } catch (e) {
    console.error('WALK FAILED', e); await p.screenshot({ path: path.join(out, 'zz-fx3a-failure.png') }); process.exitCode = 1
  } finally {
    calls.push(`\nconsole errors: ${errors.length}`, ...errors)
    fs.appendFileSync(callsOut, calls.join('\n') + '\n'); await b.close()
  }
})()

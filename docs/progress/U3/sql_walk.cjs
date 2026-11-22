// U3 (F-X4b hand-off) ad-hoc walk: Build › SQL query with the project picker. 1366 × 900, dSF 1.
//   node sql_walk.cjs <shots dir> <calls.txt> <state.json> <platform db path>
const { chromium } = require('playwright')
const fs = require('fs'); const path = require('path')
const [out, callsOut, stateFile, platformDb] = process.argv.slice(2)
const S = JSON.parse(fs.readFileSync(stateFile, 'utf8')); const ui = 'http://127.0.0.1:5190'
;(async () => {
  const b = await chromium.launch(); const p = await b.newPage({ viewport: { width: 1366, height: 900 }, deviceScaleFactor: 1 })
  const calls = []; const errors = []
  const mark = (s) => { calls.push(`\n## ${s}`); console.log('##', s) }
  p.on('console', (m) => { if (m.type() === 'error') errors.push(m.text()) })
  p.on('response', async (r) => { const u = r.url(); if (!/\/api\/sql\/(tables|preview|datasets)/.test(u)) return
    let j = ''; try { j = JSON.stringify(await r.json()).slice(0, 260) } catch { /* */ }
    calls.push(`${r.request().method()} ${u.replace(/^.*\/api/, '/api')} ${(r.request().postData() || '').replace(/"password":"[^"]*"/, '"password":"***"').slice(0, 220)} → ${r.status()} ${j}`) })
  const tid = (s) => `[data-testid="${s}"]`
  const shot = async (name) => { await p.waitForTimeout(500); const l = p.locator(tid('sql-source')); await l.scrollIntoViewIfNeeded(); const bb = await l.boundingBox(); await p.screenshot({ path: path.join(out, `${name}.png`), clip: { ...bb, height: Math.min(bb.height, 1600) } }); console.log('shot', name) }
  const open = async (who) => {
    await p.goto(`${ui}/#/build`); await p.evaluate(([w, t]) => { localStorage.setItem('ensemble.actor', w); localStorage.setItem('ensemble.team', t) }, [who, S.cr]); await p.reload()
    await p.click('button:has-text("SQL query")'); await p.waitForSelector(tid('sql-source'))
    await p.waitForSelector(`${tid('sql-project')} option[value="${S.pd}"]`, { state: 'attached', timeout: 30000 })
  }
  const setUrl = async (u, q) => { await p.fill(tid('sql-url'), u); if (q) await p.fill(tid('sql-query'), q) }
  try {
    mark('1. Ana (maintainer) · project PD 2026 → sqlite file in ENSEMBLE_SQL_IMPORT_DIRS → List tables → Preview → Load')
    await open('Ana')
    await setUrl(`sqlite:///${S.db}`, 'SELECT branch_id, region, opened, loans, defaults FROM branches')
    await p.click(tid('sql-list')); await p.waitForSelector(tid('sql-tables'), { timeout: 60000 })
    await p.click(tid('sql-preview')); await p.waitForSelector(tid('sql-preview-table'), { timeout: 60000 })
    await shot('27-sql-preview')
    await p.click(tid('sql-load')); await p.waitForFunction(() => /dataset=ds_/.test(location.hash), null, { timeout: 120000 })
    calls.push(`→ the Build page opened ${await p.evaluate(() => location.hash)}`)
    mark('2. Ana · the platform DB as a source → 400 sql_platform_db; a file outside the import dir → 400 sql_path_refused')
    await open('Ana')
    await setUrl(`sqlite:///${platformDb}`, 'SELECT * FROM datasets')
    await p.click(tid('sql-preview')); await p.waitForSelector(tid('sql-err'), { timeout: 60000 })
    await shot('28-sql-platform-db')
    await setUrl('sqlite:////etc/hosts'); await p.click(tid('sql-preview'))
    await p.waitForSelector(`${tid('sql-err')}:has-text("sql_path_refused")`, { timeout: 60000 })
    await shot('29-sql-path-refused')
    mark('3. Vic (viewer) · data.import greyed on PD 2026 with the reason')
    await open('Vic'); await p.waitForSelector(tid('sql-why'), { timeout: 30000 })
    await shot('30-sql-viewer-greyed')
  } catch (e) { console.error('WALK FAILED', e); await p.screenshot({ path: path.join(out, 'zz-sql-failure.png') }); process.exitCode = 1 }
  finally { calls.push(`\nconsole errors: ${errors.length}`, ...errors); fs.appendFileSync(callsOut, calls.join('\n') + '\n'); await b.close() }
})()

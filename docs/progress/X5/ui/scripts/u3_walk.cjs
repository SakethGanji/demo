// U3 ad-hoc walk (not a suite): drives the UI at 1366 × 900, deviceScaleFactor 1, logs every /api call the UI makes and
// takes the screenshots. node walk.cjs <shots dir> <files dir> <calls.txt> <state.json from setup.py> [from step]
const { chromium } = require('playwright')
const fs = require('fs')
const path = require('path')
const [out, files, callsOut, stateFile, FROM] = process.argv.slice(2)
const ui = 'http://127.0.0.1:5190'
const API = 'http://127.0.0.1:8660/api'
const from = Number(FROM || 0)
const S = JSON.parse(fs.readFileSync(stateFile, 'utf8'))

;(async () => {
  fs.mkdirSync(out, { recursive: true })
  const b = await chromium.launch()
  const p = await b.newPage({ viewport: { width: 1366, height: 900 }, deviceScaleFactor: 1 })
  const calls = []
  const mark = (s) => { calls.push(`\n## ${s}`); console.log('##', s) }
  const noisy = /\.tsx?(\?|$)|\/api\/(health|status|runs\?kind=all&light|runs\/[^/]+\/events|llm|agent|vela|annotations\/|me$|authz\/check)/
  const short = (j) => JSON.stringify(j).slice(0, 420)
  p.on('response', async (r) => {
    const u = r.url(); if (!u.includes('/api/') || noisy.test(u)) return
    const q = r.request(); let body = q.postData() || ''
    if ((q.headers()['content-type'] || '').startsWith('multipart')) body = '(multipart: ' + [...body.matchAll(/name="([^"]+)"(?:; filename="([^"]+)")?/g)].map((m) => m[2] ? `${m[1]}=@${m[2]}` : m[1]).join(' ') + ')'
    let res = ''
    try { res = short(await r.json()) } catch { /* not json */ }
    calls.push(`${q.method()} ${u.replace(/^.*\/api/, '/api')} ${body ? body.slice(0, 300) + ' ' : ''}→ ${r.status()}${res ? ' ' + res : ''}`)
  })
  const errors = []
  p.on('console', (m) => { if (m.type() === 'error') errors.push(`[${p.url().replace(ui, '')}] ${m.text()}`) })
  p.on('pageerror', (e) => errors.push(`pageerror ${e.message}`))
  const shot = async (name, el) => {
    await p.waitForTimeout(700)
    let opts = { fullPage: false }
    if (el) { const l = p.locator(el).first(); await l.scrollIntoViewIfNeeded(); await l.evaluate((e) => { const r = e.getBoundingClientRect(); if (r.top < 70) { let s = e.parentElement; while (s && s.scrollHeight <= s.clientHeight) s = s.parentElement; (s || window).scrollBy(0, r.top - 80) } }); await p.waitForTimeout(200); const bb = await l.boundingBox(); opts = { clip: { x: bb.x, y: bb.y, width: Math.min(bb.width, 1366), height: Math.min(bb.height, 2400) } } }
    await p.screenshot({ path: path.join(out, `${name}.png`), ...opts }); console.log('shot', name)
  }
  const as = async (who, team, hash) => {
    await p.goto(`${ui}/#/${hash}`)
    await p.evaluate(([w, t]) => { localStorage.setItem('ensemble.actor', w); localStorage.setItem('ensemble.team', t) }, [who, team]); await p.reload()
    await p.waitForTimeout(1500)
  }
  // a step another person takes outside this walk's screens (an approval of a delete request, a hold): by API, logged
  const api = async (who, method, url, body) => {
    const r = await fetch(API + url, { method, headers: { 'Content-Type': 'application/json', 'X-Ensemble-Actor': who }, body: body ? JSON.stringify({ ...body, by: who }) : undefined })
    const j = await r.json().catch(() => null)
    calls.push(`(by API, ${who}) ${method} /api${url} ${body ? JSON.stringify(body).slice(0, 200) + ' ' : ''}→ ${r.status} ${short(j)}`)
    return j
  }
  const tid = (s) => `[data-testid="${s}"]`
  const st = {}
  const save = () => fs.writeFileSync(path.join(out, 'state.json'), JSON.stringify(st, null, 1))
  if (fs.existsSync(path.join(out, 'state.json'))) Object.assign(st, JSON.parse(fs.readFileSync(path.join(out, 'state.json'), 'utf8')))
  const openDs = async (who, team) => { await as(who, team, `build?view=data&dataset=${S.root}`); await p.waitForSelector(tid('data-page'), { timeout: 30000 }) }
  const settings = async (who, team) => { await as(who, team, 'settings'); await p.waitForSelector(tid('settings-lifecycle'), { timeout: 30000 }); await p.locator(tid('lc-manual')).scrollIntoViewIfNeeded() }

  try {
  if (from <= 1) {
    mark('1. Ana · Upload a new version… loans_changes.csv: operations in column "change" (new / chg / del), not _op')
    await openDs('Ana', S.cr)
    await p.click(tid('data-upload-version'))
    await p.setInputFiles(tid('up-file'), [path.join(files, 'loans_changes.csv')])
    await p.click(tid('up-check'))
    await p.waitForSelector(tid('up-delta-open'), { timeout: 120000 })
    await p.click(tid('up-delta-open'))
    await p.waitForSelector(tid('up-delta-hint'))
    await p.selectOption('[data-testid^="up-opcol-"]', 'change')
    await p.waitForTimeout(300)
    mark('1b. Apply as delta without mapping the values → 400 delta_op_unknown as a sentence')
    await p.click(tid('up-apply-delta'))
    await p.waitForSelector(tid('up-err'), { timeout: 120000 })
    await shot('01-delta-op-unknown', tid('up-flow'))
    mark('1c. map new → insert, chg → update, del → delete → Apply as delta → counts with the options in the plan')
    for (const [i, [v, k]] of [['new', 'insert'], ['chg', 'update'], ['del', 'delete']].entries()) {
      await p.click('[data-testid^="up-opadd-"]')
      await p.fill(`[data-testid$="-${i}"][data-testid^="up-opval-"]`, v)
      await p.selectOption(`[data-testid$="-${i}"][data-testid^="up-opkind-"]`, k)
    }
    await shot('02-delta-options', tid('up-delta-hint'))
    await p.click(tid('up-apply-delta'))
    await p.waitForSelector(tid('up-delta-loans'), { timeout: 120000 })
    await shot('03-delta-plan', tid('up-plan'))
    await p.click(tid('up-commit'))
    await p.waitForSelector(`${tid('data-msg')}:has-text("Delta on loans")`, { timeout: 120000 })
    await shot('04-delta-created', tid('data-msg'))
  }
  if (from <= 2) {
    mark('2. Vic (viewer) · the anomaly run: top rows masked, Unmask greyed by /authz/check')
    await as('Vic', S.cr, `models?run=${S.anomaly}`)
    await p.waitForSelector(tid('mask-bar'), { timeout: 60000 })
    await shot('05-anomaly-masked-vic', '.an-two >> nth=1')
    await shot('05b-mask-bar-vic', tid('mask-bar'))
    mark('2b. Ana (maintainer) · Unmask (reason) → the clear values, "this read is audited"')
    await as('Ana', S.cr, `models?run=${S.anomaly}`)
    await p.waitForSelector(tid('unmask'), { timeout: 60000 })
    await p.waitForFunction(() => !document.querySelector('[data-testid="unmask"]').disabled, null, { timeout: 15000 })
    await p.click(tid('unmask'))
    await p.fill(tid('unmask-reason'), 'QA of the top anomalies')
    await p.click(tid('unmask-go'))
    await p.waitForSelector(tid('unmasked-on'), { timeout: 60000 })
    await shot('06-anomaly-unmasked-ana', '.an-two >> nth=1')
    await shot('06b-mask-bar-ana', tid('mask-bar'))
  }
  if (from <= 3) {
    mark('3. Ben (owner) · Settings › Data lifecycle: retention plan with no period set')
    await settings('Ben', S.cr)
    await p.click(tid('rt-plan'))
    await p.waitForSelector(tid('rt-planview'), { timeout: 60000 })
    await shot('07-retention-no-period', tid('retention-card'))
    mark('3b. periods for this plan only: versions keep_days 0, keep_min 2 → Plan → Dry run')
    await p.fill(tid('rt-ov-versions-keep_days'), '0')
    await p.fill(tid('rt-ov-versions-keep_min'), '2')
    await p.click(tid('rt-plan'))
    await p.waitForSelector(`${tid('rt-counts')}`, { timeout: 60000 })
    await p.click(tid('rt-dry'))
    await p.waitForSelector(tid('rt-dryrun'), { timeout: 60000 })
    await shot('08-retention-dry-run', tid('retention-card'))
    mark('3c. confirm → Run')
    await p.check(tid('rt-confirm'))
    await p.click(tid('rt-run'))
    await p.waitForSelector(tid('rt-result'), { timeout: 120000 })
    await shot('09-retention-run', tid('retention-card'))
    st.requests = await p.evaluate(() => [...document.querySelectorAll('[data-testid="rt-result"] li')].map((x) => (x.textContent.match(/request (dsr_[a-z0-9]+)/) || [])[1]).filter(Boolean))
    save()
    mark('3d. Ana (maintainer) · the same card: Run greyed ("needs owner")')
    await settings('Ana', S.cr)
    await p.waitForSelector(tid('rt-run-why'), { timeout: 30000 })
    await shot('10-retention-ana-greyed', tid('retention-card'))
    mark('3e. Bo approves the delete requests the run made (four eyes; the dataset page has this already)')
    for (const r of st.requests) await api('Bo', 'POST', `/dataset-requests/${r}/decide`, { approve: true })
  }
  if (from <= 4) {
    mark('4. Ada (admin) · GC: disarmed → dry run → verify-all arms it → dry run → run')
    await settings('Ada', S.cr)
    await p.waitForSelector(tid('gc-armed'), { timeout: 30000 })
    await p.click(tid('gc-dry'))
    await p.waitForSelector(tid('gc-plan'), { timeout: 120000 })
    await shot('11-gc-disarmed-dry-run', tid('gc-card'))
    await p.click(tid('gc-verify-all'))
    await p.waitForSelector(`${tid('gc-armed')}:has-text("armed ")`, { timeout: 300000 })
    await p.click(tid('gc-dry'))
    await p.waitForTimeout(1500)
    await p.waitForSelector(tid('gc-plan'), { timeout: 120000 })
    await shot('12-gc-armed-dry-run', tid('gc-card'))
    await p.check(tid('gc-confirm'))
    await p.click(tid('gc-run'))
    await p.waitForSelector(tid('gc-result'), { timeout: 300000 })
    await shot('13-gc-run', tid('gc-card'))
  }
  if (from <= 5) {
    mark('5. Ada places a legal hold on the dataset (Settings › Teams has the form); Ana asks to purge ssn → 409 legal_hold as a sentence')
    const h = await api('Ada', 'POST', '/legal-holds', { scope_kind: 'dataset', scope_id: S.root, case_ref: 'CASE-2026-207', reason: 'regulator inquiry on the PD book' })
    st.hold = h.id; save()
    await openDs('Ana', S.cr)
    await p.click(tid('purge-open'))
    await p.check(tid('purge-col-ssn'))
    await p.fill(tid('purge-reason'), 'SSNs were never needed for the PD model; data minimisation request DPO-88')
    await p.click(tid('purge-send'))
    await p.waitForSelector(tid('purge-refused'), { timeout: 120000 })
    await shot('14-purge-held', tid('purge-card'))
    mark('5b. Ben · the retention plan under the hold: every version listed as held, naming the case')
    await settings('Ben', S.cr)
    await p.fill(tid('rt-ov-versions-keep_days'), '0')
    await p.click(tid('rt-plan'))
    await p.waitForSelector(tid('rt-planview'), { timeout: 60000 })
    await shot('14b-retention-held', tid('rt-planview'))
    await api('Adam', 'POST', `/legal-holds/${st.hold}/release`, { reason: 'inquiry closed, no data requested' })
  }
  if (from <= 6) {
    mark('6. Ana · request the purge again (hold released) → new version + impact report; her Approve is greyed (four eyes)')
    await openDs('Ana', S.cr)
    await p.click(tid('purge-open'))
    await p.check(tid('purge-col-ssn'))
    await p.fill(tid('purge-reason'), 'SSNs were never needed for the PD model; data minimisation request DPO-88')
    await p.click(tid('purge-send'))
    await p.waitForSelector(tid('purge-detail'), { timeout: 300000 })
    st.purge = await p.getAttribute(tid('purge-detail'), 'data-purge'); save()
    await shot('15-purge-impact', tid('purge-card'))
    mark('6b. Ben approves for Credit Risk on the dataset page')
    await openDs('Ben', S.cr)
    await p.waitForSelector(tid(`purge-approve-${S.cr}`), { timeout: 60000 })
    await p.waitForFunction((s) => !document.querySelector(s).disabled, `[data-testid="purge-approve-${S.cr}"]`, { timeout: 15000 })
    await p.click(tid(`purge-approve-${S.cr}`))
    await p.waitForSelector(tid(`purge-approved-${S.cr}`), { timeout: 120000 })
    await shot('16-purge-ben-approved', tid('purge-card'))
    mark('6c. Cole approves for Collections in Settings › Data lifecycle › Purges → executed')
    await settings('Cole', S.co)
    await p.waitForSelector(tid(`purge-approve-${S.co}`), { timeout: 60000 })
    await p.waitForFunction((s) => !document.querySelector(s).disabled, `[data-testid="purge-approve-${S.co}"]`, { timeout: 15000 })
    await p.click(tid(`purge-approve-${S.co}`))
    await p.waitForSelector(tid('purge-executed'), { timeout: 300000 })
    await shot('17-purge-executed', tid('purges-card'))
    mark('6d. Ada · the executed purge: re-verify the old runs → purged')
    await settings('Ada', S.cr)
    await p.click(tid(`purges-row-${st.purge}`)).catch(() => {})
    await p.waitForSelector(tid('purge-executed'), { timeout: 60000 })
    for (const r of [S.r_ana, S.r_carl]) {
      await p.waitForFunction((s) => document.querySelector(s) && !document.querySelector(s).disabled, `[data-testid="purge-verify-${r}"]`, { timeout: 15000 })
      await p.click(tid(`purge-verify-${r}`))
      await p.waitForSelector(tid(`purge-verified-${r}`), { timeout: 300000 })
    }
    await shot('18-purge-reverify', tid('purge-executed'))
  }
  } catch (e) {
    console.error('WALK FAILED', e)
    await p.screenshot({ path: path.join(out, 'zz-failure.png') })
    process.exitCode = 1
  } finally {
    calls.push(`\nconsole errors: ${errors.length}`, ...errors)
    fs.appendFileSync(callsOut, calls.join('\n') + '\n')
    await b.close()
  }
})()

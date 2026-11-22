// LB-C walk (ad hoc, not a suite) on the "Support tickets (sample)" dataset, acting as Ana:
//   1. Labels tab → New layer (category, 6 classes) → Create task (4 steps, people incl. a guest, gold) → task list;
//   2. Label 5 rows by keyboard (number key + Enter; S skips; arrows move);
//   3. LLM labeling: write a prompt, run it on the fake destination, the calibration / run report;
//   4. Take a snapshot;
//   5. Edit one labelled row's context (set_cells on `subject` → v2) and read the carry-over status against v2.
//   node walk.cjs <shots dir> <calls.txt>        (UI on :5190, API on :8000, DSF from the environment)
const { chromium } = require('playwright')
const fs = require('fs')
const path = require('path')
const [out, callsOut] = process.argv.slice(2)
const ui = process.env.UI || 'http://127.0.0.1:5190'
const api = process.env.API || 'http://127.0.0.1:8000/api'
const ROOT = 'support-tickets'
const CLASSES = ['Billing', 'Technical', 'Account', 'Shipping', 'Feature Request', 'Security']

;(async () => {
  fs.mkdirSync(out, { recursive: true })
  const b = await chromium.launch()
  const p = await b.newPage({ viewport: { width: 1366, height: 900 }, deviceScaleFactor: Number(process.env.DSF || 1) })
  const calls = []
  const mark = (s) => { calls.push(`\n## ${s}`); console.log('##', s) }
  const noisy = /\.tsx?(\?|$)|\/api\/(health|status|runs\?|llm\/|agent|vela|me$|authz\/check|teams|notes|changes|lineage)/
  p.on('response', async (r) => {
    const u = r.url(); if (!u.includes('/api/') || noisy.test(u)) return
    const q = r.request(); const body = q.postData() || ''
    let res = ''
    try { res = JSON.stringify(await r.json()).slice(0, 700) } catch { /* not json */ }
    calls.push(`${q.method()} ${u.replace(/^.*\/api/, '/api')} ${body ? body.slice(0, 400) + ' ' : ''}→ ${r.status()}${res ? ' ' + res : ''}`)
  })
  const errors = []
  p.on('console', (m) => { if (m.type() === 'error') errors.push(`[${p.url().replace(ui, '')}] ${m.text()}`) })
  p.on('pageerror', (e) => errors.push(`pageerror ${e.message}`))
  const tid = (s) => `[data-testid="${s}"]`
  const shot = async (name, el) => {
    await p.waitForTimeout(700)
    const l = p.locator(el).first(); await l.scrollIntoViewIfNeeded()
    const bb = await l.boundingBox()
    await p.screenshot({ path: path.join(out, `${name}.png`), clip: { x: Math.max(0, bb.x - 8), y: Math.max(0, bb.y - 8), width: Math.min(bb.width + 16, 1366), height: Math.min(bb.height + 16, 2400) } })
    console.log('shot', name)
  }
  const view = async (name) => { await p.waitForTimeout(700); await p.screenshot({ path: path.join(out, `${name}.png`) }); console.log('shot', name) }
  let failed = 0
  const step = async (name, fn) => { mark(name); try { await fn() } catch (e) { failed++; console.log('FAILED', e.message.split('\n')[0]) } }
  const open = async () => {
    await p.goto(`${ui}/#/build?view=data&dataset=${ROOT}&dtab=labels`)
    await p.waitForSelector(tid('labels-tab'), { timeout: 30000 })
  }

  // set-up by API (what the Governance card does): tag ticket_id as direct PII, so the LLM screen shows an exclusion
  const tag = await fetch(`${api}/datasets/${ROOT}/pii`, { method: 'PUT', headers: { 'Content-Type': 'application/json', 'X-Ensemble-Actor': 'Ana' },
    body: JSON.stringify({ by: 'Ana', columns: [{ column: 'ticket_id', tag: 'pii_direct', mask: 'hash' }] }) })
  console.log('PUT pii ticket_id →', tag.status)
  await p.goto(`${ui}/`)
  await p.evaluate(() => { localStorage.setItem('ensemble.actor', 'Ana'); for (const k of Object.keys(localStorage)) if (k.startsWith('ensemble.labels.llmRuns')) localStorage.removeItem(k) })
  await open()
  const labelled = []

  await step('1a. Labels tab: a new layer (category, 6 classes)', async () => {
    if (await p.locator(tid('lbl-layer-category')).count()) return
    await p.click(tid('lbl-new-layer'))
    await p.fill(tid('lbl-layer-name'), 'category')
    await p.selectOption(tid('lbl-layer-column'), 'category')
    await p.fill(tid('lbl-layer-classes'), CLASSES.join(', '))
    await p.click(tid('lbl-layer-create'))
    await p.waitForSelector(tid('lbl-layer-category'), { timeout: 20000 })
  })

  await step('1b. Create a task: schema, guidelines, queue, people (with a guest) and gold', async () => {
    await p.click(tid('lbl-new-task'))
    await p.waitForSelector(tid('lbl-task-setup'))
    await p.fill(tid('lbl-task-name'), 'Ticket triage · round 1')
    await shot('01-task-setup-schema', tid('lbl-task-setup'))
    await p.click(tid('lbl-task-next'))
    await p.fill(tid('lbl-task-guidelines'), '## Pick the team that would fix it\n- **Billing**: charges, refunds, invoices.\n- **Technical**: errors, crashes, slowness.\n- **Account**: login, profile, access.\n- **Shipping**: delivery, tracking, returns.\n- **Feature Request**: asks for something new.\n- **Security**: fraud, a breach, a compromised account (wins over Account).\n\nUnsure? **S** skips; **F** flags with a note.')
    await p.click(tid('lbl-task-next'))
    await p.fill(tid('lbl-task-n'), '40')
    await p.click(tid('lbl-task-next'))
    await p.click(tid('lbl-person-add'))
    await p.fill(tid('lbl-person-2'), 'Gus')  // no role in the team: joins as a guest
    await p.fill(tid('lbl-task-gold'), '3 = Billing\n7 = Technical\n11 = Shipping')
    await p.locator(`${tid('lbl-task-calib')} input[type=number]`).fill('400')
    await shot('02-task-setup-people-gold', tid('lbl-task-setup'))
    await p.click(tid('lbl-task-create'))
    await p.waitForSelector(tid('lbl-tasks'), { timeout: 60000 })
    await p.waitForSelector(`${tid('lbl-tasks')} tr`, { timeout: 20000 })
    console.log('msg:', (await p.locator(tid('lbl-msg')).innerText().catch(() => '')).replace(/\s+/g, ' '))
    await shot('03-task-list', tid('lbl-tasks'))
  })

  await step('2. Label 5 rows by keyboard', async () => {
    await p.locator('[data-testid^="lbl-task-open-"]').first().click()
    await p.waitForSelector(tid('lbl-row-id'), { timeout: 30000 })
    await p.locator(tid('lbl-item')).focus()
    for (let k = 0; k < 5; k++) {
      const rid = (await p.locator(tid('lbl-row-id')).innerText()).replace('#', '')
      const key = String((k % CLASSES.length) + 1)
      await p.keyboard.press(key)
      if (k === 2) await shot('04-labelling-picked', '.lbl-work')
      await p.keyboard.press('Enter')
      await p.waitForFunction((r) => !document.querySelector('[data-testid="lbl-row-id"]') || document.querySelector('[data-testid="lbl-row-id"]').textContent !== `#${r}`, rid, { timeout: 15000 }).catch(() => {})
      labelled.push({ row_id: Number(rid), value: CLASSES[Number(key) - 1] })
    }
    await p.keyboard.press('ArrowLeft')  // back to the last answered row (read-only)
    console.log('labelled:', JSON.stringify(labelled))
    await shot('05-labelling-after-5', '.lbl-work')
    await p.click(tid('lbl-mode-quality'))
    await p.waitForSelector(tid('lbl-quality'), { timeout: 20000 }).catch(() => {})
    await shot('06-quality', tid('lbl-screen-quality'))
    await p.click(tid('lbl-back'))
  })

  await step('3. LLM labeling on the fake destination', async () => {
    await p.waitForSelector(tid('lbl-open-llm'))
    await p.click(tid('lbl-open-llm'))
    await p.waitForSelector(tid('lbl-llm'))
    await p.waitForTimeout(1500)
    console.log('destination before:', (await p.locator(tid('lbl-llm-dest')).innerText()).replace(/\s+/g, ' '))
    if (await p.locator(tid('lbl-llm-off')).count()) {
      await shot('07-llm-off', tid('lbl-llm-off'))
      await p.click(tid('lbl-llm-enable'))
      await p.waitForFunction(() => /fake/.test(document.querySelector('[data-testid="lbl-llm-dest"]')?.textContent ?? ''), null, { timeout: 15000 })
    }
    console.log('destination:', (await p.locator(tid('lbl-llm-dest')).innerText()).replace(/\s+/g, ' '))
    await p.click(tid('lbl-llm-edit'))
    await p.click(tid('lbl-llm-save'))
    await p.waitForFunction(() => /v1/.test(document.querySelector('[data-testid="lbl-llm-prompt"]')?.textContent ?? ''), null, { timeout: 15000 })
    await p.click(tid('lbl-llm-preview'))
    await p.waitForSelector(tid('lbl-llm-preview-rows'), { timeout: 60000 })
    console.log('sent:', (await p.locator(tid('lbl-llm-sent')).innerText()).replace(/\s+/g, ' '), '| excluded:', (await p.locator(tid('lbl-llm-excluded')).innerText()).replace(/\s+/g, ' '))
    await p.click(tid('lbl-llm-calibrate'))
    await p.waitForSelector(tid('lbl-llm-calibration'), { timeout: 120000 })
    console.log('calibration:', (await p.locator(tid('lbl-llm-calibration')).innerText()).replace(/\s+/g, ' ').slice(0, 300))
    await shot('08-llm-calibration', tid('lbl-llm-report'))
    await p.fill(tid('lbl-llm-n'), '60')
    await p.fill(tid('lbl-llm-budget'), '2')
    await p.click(tid('lbl-llm-run'))
    await p.click(tid('lbl-llm-run-confirm'))
    await p.waitForSelector(tid('lbl-llm-runrow-label'), { timeout: 30000 })
    await p.waitForFunction(() => { const t = document.querySelector('[data-testid="lbl-llm-report"]')?.textContent ?? ''; return /Run llr|Run /.test(t) && /label ·/.test(t) && !/queued|running/.test(t) }, null, { timeout: 120000 })
    console.log('msg:', (await p.locator(tid('lbl-llm-msg')).innerText().catch(() => '')).replace(/\s+/g, ' '))
    console.log('report:', (await p.locator(tid('lbl-llm-report')).innerText()).replace(/\s+/g, ' ').slice(0, 400))
    await view('09-llm-page')
    await shot('10-llm-run-report', tid('lbl-llm-report'))
    await p.click(tid('lbl-back'))
  })

  await step('4. Take a snapshot', async () => {
    await p.waitForSelector(tid('lbl-snap-take'))
    await p.click(tid('lbl-snap-take'))
    await p.waitForSelector(`${tid('lbl-snap-list')} tbody tr`, { timeout: 60000 })
    console.log('msg:', (await p.locator(tid('lbl-msg')).innerText()).replace(/\s+/g, ' '))
    await p.locator('[data-testid^="lbl-snap-verify-"]').first().click()
    await shot('11-snapshot', tid('lbl-snapshots'))
  })

  await step('5. Edit one labelled row (subject) → v2; carry-over status against v2', async () => {
    const rid = labelled[0]?.row_id
    if (rid == null) throw new Error('no labelled row')
    const r = await fetch(`${api}/datasets/${ROOT}/edit`, { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-Ensemble-Actor': 'Ana' },
      body: JSON.stringify({ by: 'Ana', reason: 'LB-C walk: fix a ticket subject', ops: [{ op: 'set_cells', cells: [{ row_id: rid, column: 'subject', value: 'Charged twice for one order (edited)' }] }] }) })
    const j = await r.json()
    calls.push(`POST /api/datasets/${ROOT}/edit set_cells row ${rid} subject → ${r.status} ${JSON.stringify(j).slice(0, 300)}`)
    console.log('edit →', r.status, j.id ?? j.version_id ?? JSON.stringify(j).slice(0, 200))
    await open()
    await p.waitForSelector(tid('lbl-status-stats'), { timeout: 30000 })
    const opts = await p.locator(`${tid('lbl-status-version')} option`).allInnerTexts()
    await p.selectOption(tid('lbl-status-version'), { index: opts.findIndex((o) => o.startsWith('v2')) })
    await p.waitForFunction(() => /v2/.test(document.querySelector('[data-testid="lbl-status"] .card-title')?.textContent ?? ''), null, { timeout: 15000 })
    await p.waitForTimeout(1500)
    console.log('status v2:', (await p.locator(tid('lbl-status-stats')).innerText()).replace(/\s+/g, ' '))
    await shot('12-carry-over-after-edit', tid('lbl-status'))
    await view('13-labels-tab')
  })

  fs.writeFileSync(callsOut, calls.join('\n') + '\n\n## console errors\n' + (errors.join('\n') || '(none)') + '\n')
  console.log(`\n${failed} failed step(s); ${errors.length} console error(s)`)
  for (const e of errors) console.log('  ', e.slice(0, 300))
  await b.close()
})()

// U5 ad-hoc walk (not a suite), on the data the U2 walk leaves (v1 = 3 tables, v2 = a delta on bureau, not the primary):
//   1. the versions row of v2: the total over its tables, the breakdown per table on expand;
//   2. Edit v2 › bureau: + a row repeating an existing customer_id → preview lists key_invalidated + relation_dropped →
//      Create disabled → tick both → Create → 201 (acknowledge by code), and v3's row shows the bureau +1.
//   node walk.cjs <shots dir> <calls.txt> <dataset root>     (UI on :5190, DSF from the environment)
const { chromium } = require('playwright')
const fs = require('fs')
const path = require('path')
const [out, callsOut, ROOT] = process.argv.slice(2)
const ui = 'http://127.0.0.1:5190'

;(async () => {
  fs.mkdirSync(out, { recursive: true })
  const b = await chromium.launch()
  const p = await b.newPage({ viewport: { width: 1366, height: 900 }, deviceScaleFactor: Number(process.env.DSF || 1) })
  const calls = []
  const mark = (s) => { calls.push(`\n## ${s}`); console.log('##', s) }
  const noisy = /\.tsx?(\?|$)|\/api\/(health|status|runs\?|runs\/[^/]+\/events|llm|agent|vela|annotations|me$|authz\/check)/
  p.on('response', async (r) => {
    const u = r.url(); if (!u.includes('/api/') || noisy.test(u)) return
    const q = r.request(); const body = q.postData() || ''
    let res = ''
    try { res = JSON.stringify(await r.json()).slice(0, 900) } catch { /* not json */ }
    calls.push(`${q.method()} ${u.replace(/^.*\/api/, '/api')} ${body ? body.slice(0, 400) + ' ' : ''}→ ${r.status()}${res ? ' ' + res : ''}`)
  })
  const errors = []
  p.on('console', (m) => { if (m.type() === 'error') errors.push(`[${p.url().replace(ui, '')}] ${m.text()}`) })
  p.on('pageerror', (e) => errors.push(`pageerror ${e.message}`))
  const tid = (s) => `[data-testid="${s}"]`
  const shot = async (name, el) => {
    await p.waitForTimeout(600)
    const l = p.locator(el).first(); await l.scrollIntoViewIfNeeded()
    const bb = await l.boundingBox()
    await p.screenshot({ path: path.join(out, `${name}.png`), clip: { x: bb.x, y: bb.y, width: Math.min(bb.width, 1366), height: Math.min(bb.height, 2400) } })
    console.log('shot', name)
  }
  let failed = 0
  const step = async (name, fn) => { mark(name); try { await fn() } catch (e) { failed++; console.log('FAILED', e.message.split('\n')[0]) } }

  await p.goto(`${ui}/#/build?view=data&dataset=${ROOT}`)
  await p.evaluate(() => { localStorage.setItem('ensemble.actor', 'Ana') })
  await p.reload()
  await p.waitForSelector(tid('data-page'), { timeout: 30000 })

  await step('1. versions: v2 is a delta on bureau (not the primary table): total + per table', async () => {
    await p.waitForSelector(tid('data-tchg-total-2'), { timeout: 30000 })
    const total = (await p.locator(tid('data-tchg-total-2')).innerText()).replace(/\s+/g, ' ')
    console.log('v2 total:', total, '| title:', await p.locator(`${tid('data-tchg-2')} > summary`).getAttribute('title'))
    if (!/\+10 ~20 −5/.test(total)) throw new Error(`v2 total is ${total}, expected +10 ~20 −5`)
    await shot('01-versions-v2-total', '.data-versions')
    await p.click(`${tid('data-tchg-2')} > summary`)
    await p.waitForSelector(tid('data-tchg-2-bureau'))
    for (const t of ['applications', 'payments', 'bureau']) console.log('  ', (await p.locator(tid(`data-tchg-2-${t}`)).innerText()).replace(/\s+/g, ' '))
    await shot('02-versions-v2-per-table', '.data-versions')
  })

  await step('2. Edit v2 › bureau: + a row repeating an existing customer_id → Preview', async () => {
    await p.click(tid('data-v-2'))
    await p.selectOption(tid('table-picker'), 'bureau')
    await p.click(tid('data-edit-2'))
    await p.waitForSelector(tid('edit-grid'), { timeout: 30000 })
    const cid = (await p.locator('[data-testid^="edit-cell-"][data-testid$="-customer_id"]').first().innerText()).trim()
    console.log('repeating customer_id', cid)
    await p.click(tid('edit-add-row'))
    const cols = await p.locator('.edit-newrow input').evaluateAll((xs) => xs.map((x) => x.getAttribute('aria-label')))
    for (const c of cols) {
      const name = c.replace(/^new /, '')
      const v = name === 'customer_id' ? cid : (await p.locator(`[data-testid^="edit-cell-"][data-testid$="-${name}"]`).first().innerText()).trim()
      await p.fill(`.edit-newrow input[aria-label="${c}"]`, v)
    }
    await p.click(tid('edit-add-row-ok'))
    await p.click(tid('edit-preview-btn'))
    await p.waitForSelector(tid('up-integrity'), { timeout: 60000 })
    await p.fill(tid('edit-reason'), 'U5: a second bureau record for one customer')
    if (!(await p.locator(tid('edit-create')).isDisabled())) throw new Error('Create is enabled before the acknowledgement')
    console.log('Create disabled, title:', await p.locator(tid('edit-create')).getAttribute('title'))
    await shot('03-edit-needs-ack', tid('edit-pending'))
  })

  await step('3. tick key_invalidated only → still disabled; tick relation_dropped → Create → 201', async () => {
    await p.check(tid('up-ack-key_invalidated'))
    if (!(await p.locator(tid('edit-create')).isDisabled())) throw new Error('Create is enabled with one of two acknowledged')
    console.log('one ticked → still disabled:', await p.locator(tid('edit-create')).getAttribute('title'))
    await p.check(tid('up-ack-relation_dropped'))
    if (await p.locator(tid('edit-create')).isDisabled()) throw new Error('Create still disabled with both acknowledged')
    await shot('04-edit-acked', tid('edit-pending'))
    const [resp] = await Promise.all([
      p.waitForResponse((r) => r.url().includes('/edit') && r.request().method() === 'POST' && !(r.request().postData() || '').includes('"preview":true'), { timeout: 120000 }),
      p.click(tid('edit-create')),
    ])
    const body = JSON.parse(resp.request().postData())
    console.log('commit', resp.status(), 'acknowledge', JSON.stringify(body.acknowledge))
    if (resp.status() !== 201) throw new Error(`commit answered ${resp.status()}`)
    await p.waitForSelector(tid('data-msg'), { timeout: 60000 })
    await shot('05-edit-created', tid('data-msg'))
  })

  await step('4. versions: v3 (an edit of bureau) shows bureau +1 and the key / relation warnings', async () => {
    await p.waitForSelector(tid('data-tchg-total-3'), { timeout: 30000 })
    console.log('v3 total:', (await p.locator(tid('data-tchg-total-3')).innerText()).replace(/\s+/g, ' '))
    await p.click(`${tid('data-tchg-3')} > summary`)
    for (const t of ['applications', 'payments', 'bureau']) console.log('  ', (await p.locator(tid(`data-tchg-3-${t}`)).innerText()).replace(/\s+/g, ' '))
    await shot('06-versions-v3', '.data-versions')
  })

  fs.writeFileSync(callsOut, calls.join('\n') + '\n')
  console.log('failed', failed, 'errors', errors.length, errors)
  await b.close()
})()

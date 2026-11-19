// F19 visual pass: every page touched in F1–F18 at 1366 and 1920, full-page screenshots, and a horizontal-overflow probe.
// node scripts/f19_shots.cjs <out_dir> [filter]   (needs the API on :8000 and Vite on :5190 with runs to show)
const { chromium } = require('playwright')
const fs = require('fs')
const path = require('path')
const UI = 'http://localhost:5190'
const API = 'http://127.0.0.1:8000'
const OUT = process.argv[2]
const FILTER = process.argv[3] || ''

const j = async (p) => (await fetch(API + p)).json()

async function pickTargets() {
  const runs = (await j('/api/runs?kind=all&limit=1000')).runs
  const ok = runs.filter((r) => r.status === 'succeeded')
  const newest = (f) => ok.filter(f).sort((a, b) => (a.created_at < b.created_at ? 1 : -1))[0]
  const credit = newest((r) => r.dataset_id === 'credit-default' && r.scorecard?.available && r.monotone) || newest((r) => r.dataset_id === 'credit-default' && r.scorecard?.available) || newest((r) => r.dataset_id === 'credit-default')
  const creditLc = newest((r) => r.dataset_id === 'credit-default' && r.learning_curve?.status === 'succeeded') || credit
  const shift = newest((r) => r.shift?.status === 'succeeded') || credit
  const slices = newest((r) => r.slices && r.dataset_id === 'personal-loans') || newest((r) => r.slices)
  const tickets = newest((r) => r.dataset_id === 'support-tickets' && r.annotations) || newest((r) => r.dataset_id === 'support-tickets')
  const al = newest((r) => r.active_learning?.status) || tickets
  const quant = newest((r) => r.problem_type === 'quantile')
  const fc = newest((r) => r.kind === 'forecast')
  const an = newest((r) => r.kind === 'anomaly')
  const doc = newest((r) => r.doctor?.primary) || credit
  const vers = (await j('/api/registry/versions')).versions ?? (await j('/api/registry/versions'))
  const vok = vers.filter((v) => v.status === 'ready')
  const vnew = (f) => vok.filter(f).sort((a, b) => (a.created_at < b.created_at ? 1 : -1))[0]
  const vReasons = vnew((v) => /credit|reason|default/i.test(v.model_name ?? v.name ?? '')) || vok[0]
  const vStream = vnew((v) => /loan-stream/.test(v.model_name ?? '') && v.stage === 'Production') || vnew((v) => /loan-stream/.test(v.model_name ?? ''))
  return { credit, creditLc, shift, slices, tickets, al, quant, fc, an, doc, vReasons, vStream }
}

async function main() {
  fs.mkdirSync(OUT, { recursive: true })
  const t = await pickTargets()
  const id = (r) => r?.id
  console.log(Object.fromEntries(Object.entries(t).map(([k, v]) => [k, v?.id])))
  const pages = [
    ['01-home', '#/home'],
    ['02-new-model', '#/quick-train'],
    ['03-results-credit', `#/quick-train?run=${id(t.credit)}`],
    ['04-results-quantile', `#/quick-train?run=${id(t.quant)}`],
    ['05-explain-performance', `#/explain?run=${id(t.credit)}&tab=Performance`],
    ['06-explain-doctor', `#/explain?run=${id(t.doc)}&tab=Model%20Doctor`],
    ['07-explain-threshold', `#/explain?run=${id(t.credit)}&tab=Threshold`],
    ['08-explain-monotonicity', `#/explain?run=${id(t.credit)}&tab=Monotonicity`],
    ['09-explain-scorecard', `#/explain?run=${id(t.credit)}&tab=Scorecard`],
    ['10-explain-learning-curve', `#/explain?run=${id(t.creditLc)}&tab=Learning%20Curve`],
    ['11-explain-weak-slices', `#/explain?run=${id(t.slices)}&tab=Weak%20Slices`],
    ['12-explain-shift', `#/explain?run=${id(t.shift)}&tab=Shift`],
    ['13-explain-labelling-queue', `#/explain?run=${id(t.al)}&tab=Labelling%20Queue`],
    ['14-explain-annotators', `#/explain?run=${id(t.tickets)}&tab=Annotators`],
    ['15-explain-text', `#/explain?run=${id(t.tickets)}&tab=Text`],
    ['16-gym-goal', `#/gym?from=${id(t.credit)}`],
    ['17-registry', '#/registry'],
    ['18-registry-register', `#/registry?register=${id(t.credit)}`],
    ['19-version-card', `#/registry?version=${id(t.vReasons)}&tab=Model%20Card`],
    ['20-version-reasons', `#/registry?version=${id(t.vReasons)}&tab=Reason%20Codes`],
    ['21-version-monitoring', `#/registry?version=${id(t.vStream)}&tab=Monitoring`],
    ['22-forecast', `#/forecast?run=${id(t.fc)}`],
    ['23-anomaly', `#/anomaly?run=${id(t.an)}`],
    ['24-settings', '#/settings'],
    ['25-autopilot', '#/autopilot'],
    ['26-experiments', '#/experiments'],
  ].filter(([n]) => n.includes(FILTER))
  const browser = await chromium.launch()
  const report = []
  for (const w of [1366, 1920]) {
    const ctx = await browser.newContext({ viewport: { width: w, height: w === 1366 ? 768 : 1080 }, deviceScaleFactor: 1 })
    const page = await ctx.newPage()
    const errs = []
    page.on('pageerror', (e) => errs.push(String(e)))
    for (const [name, hash] of pages) {
      if (hash.includes('undefined')) { report.push(`${w} ${name}: skipped (no run)`); continue }
      errs.length = 0
      await page.goto(`${UI}/${hash}`)
      await page.reload()  // a hash change alone keeps a page's local state
      await page.waitForLoadState('networkidle').catch(() => {})
      await page.waitForTimeout(name.includes('gym') ? 4000 : 4000)
      // horizontal overflow inside .workspace (the app's scroll container): its own scrollWidth, and any element whose
      // right edge passes the workspace's without an ancestor (below the workspace) that scrolls or clips it
      const ov = await page.evaluate(() => {
        const ws = document.querySelector('.workspace')
        if (!ws) return { doc: -1, items: [], h: 0 }
        const right = ws.getBoundingClientRect().left + ws.clientWidth
        const out = { doc: ws.scrollWidth - ws.clientWidth, items: [], h: ws.scrollHeight - ws.clientHeight }
        for (const el of ws.querySelectorAll('*')) {
          const r = el.getBoundingClientRect()
          if (r.width === 0 || r.right <= right + 2) continue
          let p = el.parentElement, clipped = false
          while (p && p !== ws) { const st = getComputedStyle(p); if (/(auto|scroll|hidden|clip)/.test(st.overflowX)) { clipped = true; break } p = p.parentElement }
          if (!clipped) out.items.push(`${el.tagName.toLowerCase()}.${String(el.className).split(' ').slice(0, 2).join('.')} +${Math.round(r.right - right)}px`)
        }
        out.items = [...new Set(out.items)].slice(0, 6)
        return out
      })
      // full-length screenshot: grow the viewport by what the workspace scrolls
      const vh = w === 1366 ? 768 : 1080
      if (ov.h > 0) { await page.setViewportSize({ width: w, height: Math.min(vh + ov.h, 9000) }); await page.waitForTimeout(400) }
      const file = path.join(OUT, `${name}-${w}.png`)
      await page.screenshot({ path: file })
      await page.setViewportSize({ width: w, height: vh })
      report.push(`${w} ${name}: doc overflow ${ov.doc}px${ov.items.length ? ' · ' + ov.items.join(', ') : ''}${errs.length ? ' · errors: ' + errs.join(' | ').slice(0, 200) : ''}`)
      console.log(report[report.length - 1])
    }
    await ctx.close()
  }
  await browser.close()
  fs.writeFileSync(path.join(OUT, 'overflow-report.txt'), report.join('\n') + '\n')
}
main().catch((e) => { console.error(e); process.exit(1) })

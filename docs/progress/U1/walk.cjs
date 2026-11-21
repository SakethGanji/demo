// U1 ad-hoc walk: drives the UI at 1366 wide, logs every /api call the UI makes, takes the screenshots.
//   node u1_walk.cjs <shots dir> <files dir> <calls.txt>
const { chromium } = require('playwright')
const fs = require('fs')
const path = require('path')
const [out, files, callsOut] = process.argv.slice(2)
const ui = 'http://127.0.0.1:5190'; const TEAM = process.argv[5]

;(async () => {
  fs.mkdirSync(out, { recursive: true })
  const b = await chromium.launch()
  const p = await b.newPage({ viewport: { width: 1366, height: 900 }, deviceScaleFactor: 1 })
  const calls = []
  let phase = 'setup'
  const mark = (s) => { phase = s; calls.push(`\n## ${s}`) }
  const noisy = /\.tsx?(\?|$)|\/api\/(health|status|runs\?kind=all&light|runs\/[^/]+\/events|llm|agent|vela|annotations)/
  p.on('response', async (r) => {
    const u = r.url(); if (!u.includes('/api/') || noisy.test(u)) return
    const q = r.request(); let body = q.postData() || ''
    if ((q.headers()['content-type'] || '').startsWith('multipart')) body = '(multipart: ' + [...body.matchAll(/name="([^"]+)"(?:; filename="([^"]+)")?\r\n\r\n([^\r]{0,40})/g)].map((m) => m[2] ? `${m[1]}=@${m[2]}` : `${m[1]}=${m[3]}`).join(' ') + ')'
    let res = ''
    try { const j = await r.json(); res = summary(u, q.method(), j) } catch { /* not json */ }
    calls.push(`${q.method()} ${u.replace(/^.*\/api/, '/api')} ${body ? body.slice(0, 300) + ' ' : ''}→ ${r.status()}${res ? ' ' + res : ''}`)
  })
  const errors = []
  p.on('console', (m) => { if (m.type() === 'error') errors.push(m.text()) })
  const shot = async (name, el) => {
    await p.waitForTimeout(600)
    let opts = {}
    if (el) { const l = p.locator(el).first(); await l.scrollIntoViewIfNeeded(); opts = { clip: await l.boundingBox() } }
    await p.screenshot({ path: path.join(out, `${name}.png`), ...opts }); console.log('shot', name)
  }

  mark('0. who and which team (sidebar)')
  await p.goto(`${ui}/#/build`); await p.evaluate(() => { localStorage.setItem('ensemble.actor', 'Ana'); localStorage.removeItem('ensemble.team') }); await p.reload()
  await p.waitForSelector('[data-testid="team-select"] option[value="' + TEAM + '"]', { state: 'attached', timeout: 30000 })
  await p.selectOption('[data-testid="team-select"]', '' + TEAM + '')

  mark('1. Build › Data: New project (in the current team)')
  await p.click('[data-testid="build-tab-data"]')
  await p.waitForSelector('[data-testid="data-table"]', { timeout: 30000 })
  await p.click('[data-testid="project-new"]')
  await p.fill('[data-testid="project-new-name"]', 'PD 2026')
  await p.click('[data-testid="project-new-create"]')
  await p.waitForSelector('text=Project prj_', { timeout: 15000 })

  mark('2. Build › Start: drop pd_accounts.csv → match check → staged upload → /check → /commit')
  await p.click('[data-testid="build-tab-start"]')
  await p.locator('[data-testid="build"] input[type=file]').first().setInputFiles(path.join(files, 'pd_accounts.csv'))
  await p.waitForSelector('[data-testid="build-staged"] [data-testid="up-project"] option:has-text("PD 2026")', { state: 'attached', timeout: 30000 })
  await shot('02-build-staged-pick', '[data-testid="build-staged"]')
  await p.click('[data-testid="up-check"]')
  await p.waitForSelector('[data-testid="up-check-result"]', { timeout: 60000 })
  await shot('03-check-typed-columns', '[data-testid="build-staged"]')
  await p.click('[data-testid="up-commit"]')
  await p.waitForSelector('[data-testid="build-suggestions"]', { timeout: 120000 })
  await shot('04-created-intake')

  mark('3. run: the top suggestion → New Model → Start Training')
  await p.click('[data-testid="build-open"]')
  await p.waitForSelector('[data-testid="target-continue"]:not([disabled])', { timeout: 60000 })
  await p.click('[data-testid="target-continue"]')
  await p.waitForSelector('[data-testid="start-training"]:not([disabled])', { timeout: 60000 })
  await p.click('[data-testid="start-training"]')
  await p.waitForTimeout(4000)

  mark('4. Build: drop pd_accounts_dec.csv → "looks like" → Add as new version → /uploads?parent_version_id → /check → /commit {match}')
  await p.goto(`${ui}/#/build`)
  await p.locator('[data-testid="build"] input[type=file]').first().setInputFiles(path.join(files, 'pd_accounts_dec.csv'))
  await p.waitForSelector('[data-testid="build-match"]', { timeout: 60000 })
  await shot('05-build-looks-like', '[data-testid="build-match"]')
  await p.click('[data-testid="match-add"]')
  await p.waitForSelector('[data-testid="up-match"] [data-testid="up-methods"]', { timeout: 60000 })
  await shot('06-next-version-match', '[data-testid="build-match"]')
  await p.click('[data-testid="up-commit"]')
  await p.waitForSelector('[data-testid="dr-made"]', { timeout: 120000 })
  await shot('07-next-version-created', '[data-testid="dr-made"]')

  mark('5. Build › Data › dataset: Upload a new version (pd_accounts_jan.csv) → row hash → acknowledge → /commit {match, acknowledge}')
  await p.click('[data-testid="build-tab-data"]')
  await p.waitForSelector('[data-testid="data-table"] tbody tr', { timeout: 30000 })
  await shot('08-data-catalog')
  await p.locator('[data-testid="data-table"] tbody tr', { hasText: 'pd_accounts' }).first().click()
  await p.waitForSelector('[data-testid="data-upload-version"]', { timeout: 30000 })
  await p.click('[data-testid="data-upload-version"]')
  await p.locator('[data-testid="up-file"]').setInputFiles(path.join(files, 'pd_accounts_jan.csv'))
  await p.click('[data-testid="up-check"]')
  await p.waitForSelector('[data-testid="up-methods"]', { timeout: 60000 })
  await p.click('[data-testid="up-m-row_hash"]')
  await p.waitForSelector('[data-testid="up-ack"]')
  await shot('09-row-hash-needs-acknowledge', '[data-testid="up-flow"]')
  await p.click('[data-testid="up-ack-box"]')
  await p.click('[data-testid="up-commit"]')
  await p.waitForSelector('[data-testid="data-msg"]', { timeout: 120000 })
  await shot('10-acknowledged-created', '[data-testid="data-page"] > .card:first-child')

  mark('6. the old Models › Data link redirects to Build › Data')
  await p.goto(`${ui}/#/models?tab=Data`)
  await p.waitForSelector('[data-testid="build-data"]', { timeout: 30000 })
  calls.push('redirected to ' + p.url().replace(ui, ''))

  fs.writeFileSync(callsOut, calls.join('\n') + `\n\nconsole errors: ${errors.length}\n${errors.join('\n')}\n`)
  await b.close()
})().catch((e) => { console.error(e); process.exit(1) })

function summary(u, m, j) {
  if (u.includes('/uploads') && m === 'POST' && !u.includes('/check') && !u.includes('/commit')) return `{upload_id ${j.upload_id}, status ${j.status}, parent_version_id ${j.parent_version_id}, project_id ${j.project_id}, team_id ${j.team_id}}`
  if (u.endsWith('/check')) {
    const t = j.tables?.[0]; const nv = j.next_version
    return `{table ${t?.name} ${t?.n_rows}×${t?.n_cols}, schema [${(t?.schema || []).map((c) => `${c.name} ${c.type} "${c.reason}"`).join('; ')}], warnings [${(j.warnings || []).map((w) => w.code + ':' + w.column).join(', ')}], blocks ${j.blocks?.length}, check_sha256 ${j.check_sha256?.slice(0, 12)}…` +
      (nv ? `, next_version {candidates [${(nv.candidates || []).map((c) => `${c.columns.join('+')} score ${c.score} overlap ${c.overlap}`).join('; ')}], methods [${(nv.methods || []).map((x) => `${x.label}: matched ${x.matched} changed ${x.changed} added ${x.added} removed ${x.removed} holdout_identity_lost ${x.holdout_identity_lost}/${x.parent_holdout} needs_ack ${JSON.stringify(x.needs_acknowledge)}`).join('; ')}], preselect ${JSON.stringify(nv.preselect)}}` : '') + '}'
  }
  if (u.endsWith('/commit')) return `{version_id ${j.version_id}, version_no ${j.version_no}, root ${j.root_id}, n_rows ${j.n_rows}, team_id ${j.team_id}, project_id ${j.project_id}, default ${JSON.stringify(j.default)}${j.match ? `, match {${j.match.matched_by}: matched ${j.match.matched} changed ${j.match.changed} added ${j.match.added} removed ${j.match.removed} holdout_identity_lost ${j.match.holdout_identity_lost} acknowledged ${JSON.stringify(j.match.acknowledged)}}` : ''}, repeat ${j.repeat}}`
  if (u.includes('/projects') && m === 'POST') return `{id ${j.id}, name ${j.name}, team_id ${j.team_id}}`
  if (u.endsWith('/runs') && m === 'POST') return `{id ${j.id}, status ${j.status}, dataset_id ${j.dataset_id ?? j.config?.dataset_id}, label ${j.label ?? j.config?.label}}`
  if (u.includes('/datasets/match')) return `{match ${j.match ? `${j.match.name} v${j.match.v} (${j.match.version_id})` : null}}`
  if (u.endsWith('/me')) return `{teams [${j.teams.map((t) => t.slug + ' ' + t.role).join(', ')}], default_team ${j.default_team}}`
  if (u.includes('/projects?team=')) return `{${j.projects.length} projects: ${j.projects.map((x) => x.name).join(', ')}}`
  if (u.includes('/uploads/') && m === 'DELETE') return JSON.stringify(j).slice(0, 120)
  if (j && j.detail) return JSON.stringify(j.detail).slice(0, 400)
  return ''
}

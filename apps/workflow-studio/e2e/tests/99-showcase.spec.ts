/**
 * Showcase capture (T4): drives the scripted showcase.json build in the editor with the REAL motion at 1440x900,
 * records video, and takes screenshots at the key moments. Skipped unless SHOWCASE=1 (the normal suite never runs it).
 *   SHOWCASE=1 flock /tmp/e2e.lock npx playwright test -c e2e "tests/99-showcase"
 */
import { test, expect, rand } from '../fixtures/test'
import { seedProject } from '../fixtures/seed'
import { restoreLlmScript } from '../fixtures/shots17'
import type { Page } from '@playwright/test'
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const HERE = path.dirname(fileURLToPath(import.meta.url))
const SCRIPT = path.resolve(HERE, '../../../workflow-engine/tests/fixtures/fake_llm/ai_builder/showcase.json')
const OUT = '/tmp/claude-1000/-home-saketh-Projects-playground-work-demo/3b980f2a-bdc6-4187-b5dc-d2b1e0bdc78d/scratchpad/shots/motion'

test.skip(!process.env.SHOWCASE, 'showcase capture only runs with SHOWCASE=1')
test.use({ motion: 'on', viewport: { width: 1440, height: 900 }, video: { mode: 'on', size: { width: 1440, height: 900 } } })
test.describe.configure({ timeout: 240_000 })

let n = 0
const taken: string[] = []
async function snap(page: Page, moment: string) {
  fs.mkdirSync(OUT, { recursive: true })
  n += 1
  const file = path.join(OUT, `m-${String(n).padStart(2, '0')}-${moment}.png`)
  await page.screenshot({ path: file })
  taken.push(file)
}

test('99 showcase: a refund approval built live in the editor', async ({ page, api, s, context }) => {
  await context.addInitScript(() => {
    try { localStorage.setItem('studio.actorName', 'Asha') } catch { /* storage blocked */ }
  })
  const p = await seedProject(api, 'T99')
  const rules = JSON.parse(fs.readFileSync(SCRIPT, 'utf8')).rules
  await api.testApi('PUT', 'fake-llm/script', { rules })
  await api.clearLlmCalls()
  try {
    await page.goto(`${s.studioUrl}/p/${p.slug}/workflows/new?ai=1`)
    await expect(page.getByTestId('ai-empty-hint')).toBeVisible()
    await page.waitForTimeout(1200)
    await snap(page, 'empty-editor-hint')

    await page.getByTestId('ai-request-input').fill(`Classify refund requests; pay small refunds at once, send larger ones to a manager, always reply with the status. (${rand(6)})`)
    await page.waitForTimeout(500)
    await snap(page, 'request-typed')
    await page.getByTestId('ai-request-submit').click()
    await expect(page.getByTestId('ai-thinking')).toBeVisible()
    await page.waitForTimeout(900)
    await snap(page, 'thinking')

    const panel = page.getByTestId('ai-builder-panel')
    await expect(panel).toHaveAttribute('data-build-state', 'plan', { timeout: 30_000 })
    await expect(page.getByTestId('blueprint-node')).toHaveCount(7)
    await page.waitForTimeout(1500)
    await snap(page, 'blueprint-outlines')
    await page.getByTestId('ai-spec-step-4').hover().catch(() => undefined)
    await page.waitForTimeout(500)
    await snap(page, 'blueprint-hover-step')
    await page.mouse.move(400, 700)

    // sync proof: record when each node reaches an anim state and when each "add" row appears in the sidebar log
    await page.evaluate(() => {
      type Rec = { t: number; what: string; id: string; anim?: string }
      const recs: Rec[] = []
      ;(window as unknown as { __timeline: Rec[] }).__timeline = recs
      const idOf = (el: Element) => el.closest('[data-id]')?.getAttribute('data-id') ?? ''
      const noteAnim = (el: Element) => {
        const anim = el.getAttribute('data-anim')
        if (anim !== null && el.closest('.react-flow__node')) recs.push({ t: performance.now(), what: 'anim', id: idOf(el), anim })
      }
      const noteRows = (el: Element) => {
        const rows = el.matches('[data-testid="ai-log-row"][data-kind="add"]') ? [el] : [...el.querySelectorAll('[data-testid="ai-log-row"][data-kind="add"]')]
        for (const r of rows) recs.push({ t: performance.now(), what: 'row', id: r.getAttribute('data-node') ?? '' })
      }
      new MutationObserver((muts) => {
        for (const m of muts) {
          if (m.type === 'attributes' && m.target instanceof Element) noteAnim(m.target)
          for (const a of m.addedNodes) {
            if (!(a instanceof Element)) continue
            noteRows(a)
            if (a.hasAttribute('data-anim')) noteAnim(a)
            a.querySelectorAll('[data-anim]').forEach(noteAnim)
          }
        }
      }).observe(document.body, { attributes: true, attributeFilter: ['data-anim'], childList: true, subtree: true })
    })

    // keep the engine's round_shape events: they say where each node really lands (the head must go there, not to the outline)
    const shapes: unknown[] = []
    page.on('response', async (res) => {
      if (!/\/ai\/proposals\/[^/]+\/events/.test(res.url())) return
      const body = await res.json().catch(() => null)
      const walk = (v: unknown) => {
        if (Array.isArray(v)) v.forEach(walk)
        else if (v && typeof v === 'object') {
          const o = v as { seq?: number; event?: string; payload?: unknown }
          if (o.event === 'round_shape') { if (!shapes.some((x) => (x as { seq?: number }).seq === o.seq)) shapes.push({ seq: o.seq, payload: o.payload }) }
          else Object.values(o).forEach(walk)
        }
      }
      walk(body)
    })
    await page.getByTestId('ai-spec-approve').click()
    // the burst: six pictures of one node being built, 140 ms apart (each picture costs time, so sleep only the rest)
    await page.waitForSelector('.react-flow__node [data-anim="enter"], .react-flow__node[data-anim="enter"]', { timeout: 30_000 })
    const burst0 = Date.now()
    const burst: string[] = []
    for (let i = 0; i < 6; i++) {
      const wait = burst0 + i * 140 - Date.now()
      if (wait > 0) await page.waitForTimeout(wait)
      await snap(page, `construct-burst-${i + 1}`)
      burst.push(taken[taken.length - 1])
    }
    // watch the build: take a picture the first time each effect is on screen
    const want: Array<[string, string]> = [
      ['node-assembly-ring', 'construct-frame-drawing'],
      ['node-build-brackets', 'construct-plate'],
      ['node-build-scan', 'construct-scan'],
      ['node-lock-pulse', 'construct-lock'],
      ['edge-spark', 'edge-drawing-spark'],
      ['edge-port-catch', 'edge-port-catch'],
      ['build-head-grid', 'grid-ripple'],
      ['ai-presence', 'ai-presence-marker'],
      ['ai-log-head', 'log-row-lit-with-node'],
      ['ai-stage-progress', 'pill-progress'],
      ['node-shockwave', 'failed-test-shockwave'],
      ['node-change-chip', 'round2-fix-change-chip'],
      ['node-review-sweep', 'review-sweep'],
    ]
    const done = new Set<string>()
    const pollWant = async () => {
      for (const [tid, moment] of want) {
        if (done.has(tid)) continue
        if (await page.getByTestId(tid).first().isVisible().catch(() => false)) {
          done.add(tid)
          await snap(page, moment)
        }
      }
    }
    let roundOne = false
    const t0 = Date.now()
    while (Date.now() - t0 < 120_000) {
      const state = await panel.getAttribute('data-build-state')
      if (state === 'review') break
      await pollWant()
      if (!roundOne && (await page.getByTestId('ai-build-round-1').isVisible().catch(() => false))) {
        roundOne = true
        await page.waitForTimeout(400)
        await snap(page, 'round2-log-fix')
      }
      await page.waitForTimeout(40)
    }
    await expect(panel).toHaveAttribute('data-build-state', 'review')
    // the sidebar can reach Review before the canvas has finished playing: keep a picture of that frame
    if ((await page.locator('[data-stage]').first().getAttribute('data-playback')) === 'playing') await snap(page, 'sidebar-review-while-canvas-still-playing')
    await expect(page.locator('[data-stage]').first()).toHaveAttribute('data-playback', 'done', { timeout: 60_000 })
    // the pull-back (900 ms) and the review sweep (starts 300 ms in) play now: catch the sweep, then let the camera settle
    const settleUntil = Date.now() + 1600
    while (Date.now() < settleUntil) {
      await pollWant()
      await page.waitForTimeout(40)
    }
    await snap(page, 'review-diff-tints')

    // sync proof: every add row appeared within 150 ms of its node's lock, and no two nodes were in `enter` at once
    const timeline = await page.evaluate(() => (window as unknown as { __timeline: Array<{ t: number; what: string; id: string; anim?: string }> }).__timeline)
    const locks = new Map<string, number>()
    const entering = new Set<string>()
    let maxEntering = 0
    const gaps: Array<{ node: string; ms: number }> = []
    for (const r of timeline) {
      if (r.what === 'anim') {
        if (r.anim === 'enter') entering.add(r.id)
        else entering.delete(r.id)
        if (r.anim === 'lock' && !locks.has(r.id)) locks.set(r.id, r.t)
        maxEntering = Math.max(maxEntering, entering.size)
      }
    }
    // pair each node's FIRST add row with that node's FIRST lock; a later mount of the same row (a remount) is not a second sighting.
    // A row that shows up before its lock is a real defect and fails the bound (negative gap), so nothing is filtered away.
    const firstRow = new Map<string, number>()
    const rowMounts = new Map<string, number>()
    for (const r of timeline) {
      if (r.what !== 'row') continue
      rowMounts.set(r.id, (rowMounts.get(r.id) ?? 0) + 1)
      if (!firstRow.has(r.id)) firstRow.set(r.id, r.t)
    }
    for (const [node, lockAt] of locks) {
      if (firstRow.has(node)) gaps.push({ node, ms: Math.round(firstRow.get(node)! - lockAt) })
    }
    const remounted = [...rowMounts].filter(([, c]) => c > 1).map(([node]) => node)
    fs.mkdirSync(OUT, { recursive: true })
    fs.writeFileSync(path.join(OUT, 'shapes.json'), JSON.stringify(shapes, null, 2))
    fs.writeFileSync(path.join(OUT, 'timeline.json'), JSON.stringify({ locks: Object.fromEntries(locks), gaps, remounted, maxEntering, records: timeline }, null, 2))
    expect(locks.size, 'every built node reached lock').toBeGreaterThanOrEqual(7)
    expect(gaps.length, 'add rows seen').toBeGreaterThanOrEqual(7)
    for (const g of gaps) expect(Math.abs(g.ms), `row for ${g.node} vs its lock`).toBeLessThanOrEqual(150)
    expect(remounted, 'add rows mounted more than once (the log remounted)').toEqual([])
    expect(maxEntering, 'nodes in enter at once').toBeLessThanOrEqual(1)
    // hover a changed step in the review, then a node-under-pill check
    await page.locator('[data-testid^="ai-review-diff-"]').nth(1).hover().catch(() => undefined)
    await page.waitForTimeout(500)
    await snap(page, 'review-hover-focal-glow')
    await page.mouse.move(400, 700)

    await page.getByTestId('ai-keep-name').fill('Refund approval')
    await page.getByTestId('ai-keep').click()
    const settle = Date.now()
    while (Date.now() - settle < 4000) {
      if (await page.getByTestId('node-settle-flash').first().isVisible().catch(() => false)) {
        await snap(page, 'settle-flash-on-keep')
        break
      }
      await page.waitForTimeout(40)
    }
    await expect(panel).toHaveAttribute('data-build-state', 'kept', { timeout: 20_000 })
    await page.waitForTimeout(1800)
    await snap(page, 'kept-settled')
    fs.writeFileSync(path.join(OUT, 'moments.json'), JSON.stringify({ taken, burst, effectsSeen: [...done] }, null, 2))
  } finally {
    await restoreLlmScript(api)
  }
  const video = page.video()
  await page.close()
  if (video) await video.saveAs(path.join(OUT, 'showcase.webm'))
})

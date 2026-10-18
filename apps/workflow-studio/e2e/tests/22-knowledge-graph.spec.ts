/**
 * Knowledge page (PLAN-CAPGRAPH-UI section 2): the hero, the graph with its filters, search and focus view, the node sheet,
 * "What we can do", the memory and rebuild. Real studio against the real API, in its own project; the engine seeds the global
 * ontology at startup, so concepts exist without any setup. Motion is off (the fixture default), so every beat is at its end state.
 * Not covered on purpose: accepting or rejecting a proposed concept (a proposal needs LLM tagging, which the fake LLM does not script).
 */
import { test, expect } from '../fixtures/test'
import { seedProject, type P } from '../fixtures/seed'

interface Caps {
  rows: Array<{ concept: { key: string; label: string }; gap: boolean }>
  totals: { concepts: number; implemented: number; gaps: number }
}

const slugOf = (key: string) => key.replace(/^[a-z_]+:/, '')

test.describe('Knowledge page (T22)', () => {
  let p: P
  test.beforeEach(async ({ api, context }) => {
    p = await seedProject(api, 'T22')
    await api.post('/knowledge/rebuild', { project_id: p.id, llm_tagging: false })
    await context.addInitScript(() => {
      try { localStorage.setItem('studio.actorName', 'Asha') } catch { /* storage blocked */ }
    })
  })

  test('T22.1 the nav entry opens the page and the hero agrees with the API', async ({ page, api, open }) => {
    await open(`/p/${p.slug}`)
    await page.getByTestId('nav-knowledge').first().click()
    await expect(page).toHaveURL(new RegExp(`/p/${p.slug}/knowledge`))
    await expect(page.getByTestId('knowledge-page')).toBeVisible()
    const caps = await api.get<Caps>(`/knowledge/capabilities?project_id=${p.id}`)
    const ring = page.getByTestId('kg-hero-coverage')
    await expect(ring).toHaveAttribute('data-concepts', String(caps.totals.concepts))
    await expect(ring).toHaveAttribute('data-implemented', String(caps.totals.implemented))
    await expect(page.getByTestId('kg-hero-headline')).toContainText(`${caps.totals.implemented} of ${caps.totals.concepts}`)
    await expect(page.getByTestId('kg-stat-gaps')).toHaveAttribute('data-value', String(caps.totals.gaps))
    // first visit: no "since your last visit" chip
    await expect(page.getByTestId('kg-since-last')).toHaveCount(0)
  })

  test('T22.2 the graph shows concepts, and the kind chips and node-types chip filter it', async ({ page, open }) => {
    await open(`/p/${p.slug}/knowledge`)
    const graph = page.getByTestId('kg-graph')
    await expect(graph).toHaveAttribute('data-assembling', 'false')
    await expect(page.getByTestId('kg-node-concept-approval')).toBeVisible()
    await expect(page.getByTestId('kg-node-concept-approval')).toHaveAttribute('data-kind', 'concept')
    // node types are off by default
    await expect(page.getByTestId('kg-filter-node_type')).toHaveAttribute('aria-pressed', 'false')
    await expect(page.locator('[data-testid^="kg-node-node-"]')).toHaveCount(0)
    await page.getByTestId('kg-filter-node_type').click()
    await expect(page.locator('[data-testid^="kg-node-node-"]').first()).toBeVisible()
    // switch concepts off: they leave the canvas
    await page.getByTestId('kg-filter-concept').click()
    await expect(page.getByTestId('kg-filter-concept')).toHaveAttribute('aria-pressed', 'false')
    await expect(page.getByTestId('kg-node-concept-approval')).toHaveCount(0)
  })

  test('T22.3 clicking a node focuses it: URL, sheet with its slots, trail, Esc returns', async ({ page, open }) => {
    await open(`/p/${p.slug}/knowledge`)
    await page.getByTestId('kg-node-concept-approval').click()
    await expect(page).toHaveURL(/focus=concept(%3A|:)approval/)
    await expect(page.getByTestId('kg-graph')).toHaveAttribute('data-focus', 'concept:approval')
    const sheet = page.getByTestId('kg-node-sheet')
    await expect(sheet).toBeVisible()
    await expect(page.getByTestId('kg-node-sheet-title')).toHaveText('Approval')
    await expect(page.getByTestId('kg-node-sheet-slots')).toContainText('approver role')
    await expect(page.getByTestId('kg-focus-trail')).toContainText('Approval')
    await expect(page.getByTestId('kg-edge-label').first()).toBeVisible()
    // a link in the sheet re-focuses the graph on that node
    const link = sheet.locator('[data-testid^="kg-sheet-link-"]').first()
    const before = await page.getByTestId('kg-graph').getAttribute('data-focus')
    await link.click()
    await expect(page.getByTestId('kg-graph')).not.toHaveAttribute('data-focus', before ?? '')
    await page.keyboard.press('Escape')
    await expect(page.getByTestId('kg-node-sheet')).toHaveCount(0)
    await expect(page.getByTestId('kg-graph')).toHaveAttribute('data-focus', '')
  })

  test('T22.4 a deep link focuses a node, and search focuses another', async ({ page, open }) => {
    await open(`/p/${p.slug}/knowledge?focus=concept:escalation`)
    await expect(page.getByTestId('kg-node-sheet-title')).toHaveText('Escalation')
    await page.getByTestId('kg-overview').click()
    await expect(page.getByTestId('kg-node-sheet')).toHaveCount(0)
    await page.getByTestId('kg-search').click()
    await page.getByTestId('kg-search-menu-search').fill('Approval')
    await page.getByTestId('kg-search-item-concept-approval').click()
    await expect(page.getByTestId('kg-node-sheet-title')).toHaveText('Approval')
    await page.getByTestId('kg-depth-2').click()
    await expect(page).toHaveURL(/depth=2/)
  })

  test('T22.5 Build with this opens the new-workflow editor on the Build tab', async ({ page, open }) => {
    await open(`/p/${p.slug}/knowledge?focus=concept:approval`)
    await page.getByTestId('kg-build-with').click()
    await expect(page).toHaveURL(new RegExp(`/p/${p.slug}/workflows/new\\?.*ai=1`))
  })

  test('T22.6 What we can do: a row per concept, gaps hatched with Build with this, and the filter', async ({ page, api, open }) => {
    const caps = await api.get<Caps>(`/knowledge/capabilities?project_id=${p.id}`)
    await open(`/p/${p.slug}/knowledge?tab=matrix`)
    await expect(page.getByTestId('kg-matrix')).toBeVisible()
    await expect(page.locator('[data-testid^="kg-matrix-row-"]')).toHaveCount(caps.rows.length)
    const gap = caps.rows.find((r) => r.gap)
    test.skip(!gap, 'every concept is covered in this seed')
    const row = page.getByTestId(`kg-matrix-row-${slugOf(gap!.concept.key)}`)
    await expect(row).toHaveAttribute('data-gap', 'true')
    await expect(row.getByTestId('kg-matrix-gap')).toBeVisible()
    await page.getByTestId('kg-matrix-filter-gaps').click()
    await expect(page.locator('[data-testid^="kg-matrix-row-"]')).toHaveCount(caps.totals.gaps)
    await page.getByTestId(`kg-matrix-build-${slugOf(gap!.concept.key)}`).click()
    await expect(page).toHaveURL(new RegExp(`/p/${p.slug}/workflows/new`))
  })

  test('T22.7 a stat opens its view: gaps goes to the matrix filtered to gaps', async ({ page, open }) => {
    await open(`/p/${p.slug}/knowledge`)
    await page.getByTestId('kg-stat-gaps').click()
    await expect(page).toHaveURL(/tab=matrix/)
    await expect(page.getByTestId('kg-matrix')).toBeVisible()
    await expect(page.getByTestId('kg-matrix-filter-gaps')).toHaveAttribute('data-pressed', '')
  })

  test('T22.8 Rebuild knowledge reports what changed', async ({ page, open }) => {
    await open(`/p/${p.slug}/knowledge`)
    await page.getByTestId('kg-rebuild').click()
    await expect(page.locator('[data-sonner-toast]').filter({ hasText: 'Knowledge rebuilt' })).toBeVisible()
  })

  test('T22.9 Memory lists what was learned, and a new entry arriving on refetch is marked new', async ({ page, s, open }) => {
    const ev = (id: number, summary: string) => ({
      id, at: new Date().toISOString(), project_id: p.id, team_id: null, actor: 'Asha', trigger: 'kept_app', proposal_id: null, summary,
      payload: { pattern_key: `pattern:t22${id}`, validated_count: id, nodes_added: 2, validated: true, concepts: ['concept:approval'] },
    })
    let calls = 0
    await page.route(`${s.engineUrl}/api/knowledge/memory**`, (route) => {
      calls++
      const items = calls === 1 ? [ev(1, "Learned 'First app' from T22")] : [ev(2, "Learned 'Second app' from T22"), ev(1, "Learned 'First app' from T22")]
      return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ items, next_cursor: null }) })
    })
    await open(`/p/${p.slug}/knowledge?tab=memory`)
    await expect(page.getByTestId('kg-memory-item')).toHaveCount(1)
    await expect(page.getByTestId('kg-memory-item').first()).toHaveAttribute('data-new', 'false')
    // a window refocus refetches
    // browsers fire it bubbling up to window, where the query client listens
    await page.evaluate(() => document.dispatchEvent(new Event('visibilitychange', { bubbles: true })))
    await expect(page.getByTestId('kg-memory-item')).toHaveCount(2)
    await expect(page.locator('[data-testid="kg-memory-item"][data-id="2"]')).toHaveAttribute('data-new', 'true')
    await expect(page.getByTestId('kg-memory-open-2')).toHaveAttribute('href', /focus=pattern(%3A|:)t222/)
  })
})

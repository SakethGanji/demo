import { test, expect, goto, apiOk, type Harness } from './fixtures'
import type { Locator, Page } from '@playwright/test'

/**
 * THE STUDIO SHELL, in a real browser.
 *
 * `shape.spec.ts` proves the *contents* of the datasets surface adapt. This
 * file proves the chrome around them: the header and its route nav, the ⌘K
 * palette, the cockpit KPI strip, the query-token row, the rail's own header —
 * and, most importantly, the routes the shell is contractually forbidden to
 * touch.
 *
 * WHY THE ABSENCE TEST IS THE IMPORTANT ONE
 *
 * `_studio.tsx` is a PATHLESS LAYOUT ROUTE precisely so `/`, `/projects`,
 * `/editor` and `/builder` keep their existing layout: they are theming-only,
 * meaning they may pick up new token values and nothing else. If the shell
 * were ever moved to `__root`, all four would silently gain a 46px band and
 * reflow — a layout change to code that is out of scope, and one no unit test
 * can see. That regression is what `the theming-only routes carry no studio
 * chrome` exists to catch.
 *
 * Scope note vs the sibling specs: rail wide/narrow switching lives in
 * `shape.spec.ts`, rail search-vs-selection in `catalog.spec.ts`, and masking
 * of grid VALUES in `security.spec.ts`. What is here is the shell's own
 * reporting — the counts and the seat/masking statements the chrome makes.
 */

/** Every route that is a child of the pathless `_studio` layout route. */
const STUDIO_ROUTES = [
  '/data',
  '/catalog',
  '/query',
  '/aggregate',
  '/column',
  '/pivot',
  '/sampling',
  '/ingest',
  '/runs',
  '/admin',
  '/build',
  '/agents',
] as const

/** The four routes the shell must never wrap. See the header comment. */
const THEMING_ONLY_ROUTES = ['/', '/projects', '/editor', '/builder'] as const

/**
 * The shell header, identified by the one control only it renders.
 *
 * Deliberately not `getByRole('banner')`: on the full-takeover routes this
 * `<header>` is nested inside `<main>`, which strips the banner role, so the
 * role selector would report "absent" on routes where the header is present.
 */
function shellHeader(page: Page): Locator {
  return page.locator('header').filter({ has: page.getByLabel('Open command palette') })
}

/** The ⌘K dialog. Scoped so palette assertions cannot match the page behind it. */
function palette(page: Page): Locator {
  return page.locator('[data-slot="dialog-content"]').filter({
    has: page.locator('[data-slot="command-input"]'),
  })
}

/** One command group, addressed by its heading. */
function group(page: Page, heading: string): Locator {
  return palette(page)
    .locator('[data-slot="command-group"]')
    .filter({ has: page.locator(`[cmdk-group-heading]:text-is("${heading}")`) })
}

/** Group headings in DOM order — the evidence for "results outrank navigation". */
async function groupOrder(page: Page): Promise<string[]> {
  return palette(page)
    .locator('[cmdk-group-heading]')
    .evaluateAll((els) => els.map((e) => e.textContent?.trim() ?? ''))
}

/** A cockpit-strip metric, addressed by its eyebrow. */
function metric(page: Page, label: string): Locator {
  return page
    .locator('[data-slot="metric"]')
    .filter({ has: page.locator(`[data-slot="metric-label"]:text-is("${label}")`) })
}

async function openPalette(page: Page) {
  // Control, not Meta: the test browser is Linux, and the handler accepts
  // either — asserting on Control is asserting on the platform we run.
  await page.keyboard.press('Control+k')
  await expect(palette(page)).toBeVisible()
}

/**
 * `CockpitStrip` renders `compact()` from `shared/lib/format`. Reimplemented
 * here rather than imported so the test states the expected format
 * independently: importing the app's formatter would make any change to it
 * agree with itself.
 */
function compact(n: number): string {
  if (Math.abs(n) >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`
  if (Math.abs(n) >= 10_000) return `${(n / 1000).toFixed(0)}k`
  return n.toLocaleString()
}

/** Digits out of a rendered figure, so assertions compare numbers not strings. */
function digits(text: string | null): number {
  return Number((text ?? '').replace(/[^\d]/g, ''))
}

/**
 * The two — and only two — errors the theming-only routes emit here.
 *
 * Both are pre-existing and unrelated to this file's assertion:
 *
 *  - `/projects` and `/editor` call the workflow-engine backend, which is not
 *    running in this environment, so Chromium logs a refused connection per
 *    request. Nothing about "is the studio header here?" depends on that call
 *    succeeding.
 *  - `/editor` renders `WorkflowNavbar`, whose `TooltipTrigger` nests a
 *    `<button>` inside a `<button>`. React's dev-mode DOM-nesting warning is a
 *    real defect in code this spec does not own (see the report).
 *
 * Listed individually rather than as a blanket allow, so a NEW error on these
 * routes still fails the test.
 */
function allowUnrelatedBackendNoise(h: Harness) {
  h.allowError(/Failed to load resource: net::ERR_CONNECTION_REFUSED/)
  h.allowError(/validateDOMNesting/)
}

// ---------------------------------------------------------------------------
// The header and the route nav
// ---------------------------------------------------------------------------

test('the studio header is present on every route under the shell', async ({ page, h }) => {
  // A dataset exists so the data-driven routes render their real state rather
  // than an empty-catalog path that might skip the chrome entirely.
  await h.seed([{ id: 1, city: 'Oslo' }], 'hdr')

  // The nav's labels are read from the DOM once and then required to be
  // identical everywhere — the header is either the same header or it is not
  // the shell.
  await goto(page, '/data')
  const navLabels = await shellHeader(page)
    .locator('nav a')
    .evaluateAll((els) => els.map((e) => e.textContent?.trim() ?? ''))
  expect(navLabels.length).toBeGreaterThan(0)

  /** Routes the nav actually links to — the only ones it can mark. */
  const LINKED = new Set<string>(['/data', '/catalog', '/runs', '/admin'])

  for (const route of STUDIO_ROUTES) {
    await goto(page, route)
    await expect(shellHeader(page), `no shell header on ${route}`).toBeVisible()
    await expect(shellHeader(page).getByLabel('Open command palette')).toBeVisible()
    // Identity travels with the shell: every studio route can switch seat.
    await expect(shellHeader(page).getByLabel('Acting seat')).toBeVisible()

    const here = await shellHeader(page)
      .locator('nav a')
      .evaluateAll((els) => els.map((e) => e.textContent?.trim() ?? ''))
    expect(here, `nav differs on ${route}`).toEqual(navLabels)

    /*
     * Two nav items lit at once is worse than none — it makes the marker mean
     * nothing — so the ceiling is asserted everywhere.
     *
     * The floor only applies to the five destinations the bar links to. The
     * six dataset-scoped tools (`/query`, `/aggregate`, `/column`, `/pivot`,
     * `/sampling`, `/ingest`) are deliberately absent from the nav, which
     * means the header marks NOTHING on them. That is the documented trade
     * (see `StudioShell.tsx`), and it is asserted here so the trade stays
     * visible rather than being rediscovered as a bug.
     */
    const active = await shellHeader(page)
      .locator('nav a[data-status="active"]')
      .evaluateAll((els) => els.map((e) => e.textContent?.trim() ?? ''))
    expect(active.length, `${route} lights ${active.length} nav items`).toBeLessThanOrEqual(1)
    expect(active.length, `${route} should mark itself in the nav`).toBe(LINKED.has(route) ? 1 : 0)
  }
})

test('the theming-only routes carry no studio chrome', async ({ page, h }) => {
  allowUnrelatedBackendNoise(h)

  // The control: prove the selectors DO find the shell, so a false green here
  // cannot come from a selector that matches nothing anywhere.
  await goto(page, '/data')
  await expect(shellHeader(page)).toHaveCount(1)
  await expect(page.getByLabel('Open command palette')).toHaveCount(1)

  for (const route of THEMING_ONLY_ROUTES) {
    await goto(page, route)

    // The whole contract, three ways: no header band, no palette trigger, no
    // brand. Any one of them appearing means the shell has escaped `_studio`.
    await expect(shellHeader(page), `shell header leaked onto ${route}`).toHaveCount(0)
    await expect(
      page.getByLabel('Open command palette'),
      `palette trigger leaked onto ${route}`,
    ).toHaveCount(0)
    await expect(page.getByText('Command Studio'), `brand leaked onto ${route}`).toHaveCount(0)

    // The shortcut is registered by the shell, so it must be inert here too —
    // a global listener would be the same leak wearing a keyboard.
    await page.keyboard.press('Control+k')
    await expect(palette(page), `palette opened on ${route}`).toHaveCount(0)
  }
})

test('the nav marks the active route, and clicking one navigates client-side', async ({ page }) => {
  await goto(page, '/catalog')

  const nav = shellHeader(page).locator('nav a')
  const activeNow = async () =>
    nav.evaluateAll((els) =>
      els.filter((e) => e.getAttribute('data-status') === 'active').map((e) => e.textContent?.trim() ?? ''),
    )

  // Exactly one destination is marked, and it is the one we asked for.
  expect(await activeNow()).toEqual(['Catalog'])

  // A marker on `window` survives a client-side transition and does not
  // survive a document load — which is the only honest way to tell the two
  // apart from the outside.
  await page.evaluate(() => {
    ;(window as unknown as Record<string, unknown>).__shellNavProbe = 'alive'
  })

  await nav.filter({ hasText: 'Datasets' }).click()
  await expect(page).toHaveURL(/\/data(\?|$)/)
  expect(await activeNow()).toEqual(['Datasets'])

  const probe = await page.evaluate(
    () => (window as unknown as Record<string, unknown>).__shellNavProbe,
  )
  expect(probe, 'the nav triggered a full document load').toBe('alive')
})

// ---------------------------------------------------------------------------
// The ⌘K palette
// ---------------------------------------------------------------------------

test('the palette opens on ctrl+k and closes on escape', async ({ page }) => {
  await goto(page, '/data')
  await expect(palette(page)).toHaveCount(0)

  await openPalette(page)
  await expect(palette(page).locator('[data-slot="command-input"]')).toBeFocused()

  await page.keyboard.press('Escape')
  await expect(palette(page)).toHaveCount(0)

  // The trigger in the header is the same door.
  await page.getByLabel('Open command palette').click()
  await expect(palette(page)).toBeVisible()
  await page.keyboard.press('Escape')
  await expect(palette(page)).toHaveCount(0)
})

test('an empty palette offers navigation, and remembers the dataset you opened', async ({
  page,
  h,
}) => {
  const ds = await h.seed([{ id: 1, city: 'Oslo' }], 'recent')

  // Before anything is opened there is nothing to be recent, so the palette
  // must not invent a Recent group. Checked from a route that does NOT open a
  // dataset, because `/data` selects one on arrival.
  await goto(page, '/runs')
  await openPalette(page)
  const beforeGroups = await groupOrder(page)
  expect(beforeGroups).toContain('Go to')
  expect(beforeGroups).toContain('Tools')
  expect(beforeGroups).not.toContain('Recent')

  // The nav offered by the palette is the nav the header offers — same
  // destinations, so the two cannot drift apart unnoticed.
  const headerLabels = await shellHeader(page)
    .locator('nav a')
    .evaluateAll((els) => els.map((e) => e.textContent?.trim() ?? ''))
  const goTo = await group(page, 'Go to')
    .locator('[data-slot="command-item"]')
    .evaluateAll((els) => els.map((e) => e.textContent?.trim() ?? ''))
  for (const label of headerLabels) {
    // "Admin" is offered as "Governance" in the palette; compare the routes
    // that both actually reach instead of the words.
    if (label === 'Admin') {
      expect(goTo).toContain('Governance')
      continue
    }
    expect(goTo, `palette does not offer ${label}`).toContain(label)
  }

  await page.keyboard.press('Escape')

  // Open the dataset, then ask again.
  await goto(page, `/data?dataset=${ds.id}`)
  await expect(page.getByTestId('dataset-header')).toContainText(ds.name)

  await openPalette(page)
  const recent = group(page, 'Recent')
  await expect(recent).toBeVisible()
  await expect(recent.locator('[data-slot="command-item"]').first()).toContainText(ds.name)
})

test('typing a dataset name surfaces it under Datasets, and selecting it opens it', async ({
  page,
  h,
}) => {
  const ds = await h.seed(
    Array.from({ length: 4 }, (_, i) => ({ id: i, city: `city-${i}` })),
    'palds',
  )
  const needle = ds.name.replace(/\.csv$/, '')

  await goto(page, '/runs')
  await openPalette(page)
  await palette(page).locator('[data-slot="command-input"]').fill(needle)

  // The palette searches server-side, so what it lists must be what the
  // server answers for the same query — not a filter over a cached page.
  const fromApi = await apiOk<{ items: { id: string; name: string }[] }>(
    'GET',
    `/datasets?q=${encodeURIComponent(needle)}`,
  )
  expect(fromApi.items.map((d) => d.id)).toContain(ds.id)

  // The needle is a whole unique fixture name, so the server answers with one
  // row and the palette's six-result cap cannot be what makes this agree.
  expect(fromApi.items.length).toBe(1)

  const results = group(page, 'Datasets')
  await expect(results).toBeVisible()
  const shown = await results
    .locator('[data-slot="command-item"]')
    .evaluateAll((els) => els.map((e) => e.textContent?.trim() ?? ''))
  expect(shown.length).toBe(fromApi.items.length)
  for (const item of fromApi.items) {
    expect(shown.some((s) => s.includes(item.name)), `${item.name} missing from palette`).toBe(true)
  }

  // Results before navigation, once there is a query.
  const order = await groupOrder(page)
  expect(order.indexOf('Datasets')).toBeGreaterThanOrEqual(0)
  expect(order.indexOf('Datasets')).toBeLessThan(order.indexOf('Go to'))

  await results.locator('[data-slot="command-item"]').filter({ hasText: ds.name }).click()

  await expect(palette(page)).toHaveCount(0)
  await expect(page).toHaveURL(new RegExp(`/data\\?.*dataset=${ds.id}`))
  // The route change is not the point — landing on the right dataset is.
  await expect(page.getByTestId('dataset-header')).toContainText(ds.name)
})

test('typing a column name surfaces it under Columns, and selecting it opens that column', async ({
  page,
  h,
}) => {
  // One token in both the dataset name and a column name, so a single query
  // must produce BOTH groups — which is what makes the ordering assertion
  // below meaningful rather than vacuous.
  const token = `zshl${Date.now().toString(36).slice(-6)}`
  const column = `${token}_amount`
  const ds = await h.seed(
    Array.from({ length: 3 }, (_, i) => ({ id: i, [column]: i * 10 })),
    token,
  )

  // The column index is written during ingest; wait for the endpoint the
  // palette actually calls rather than racing it in the browser.
  await expect
    .poll(
      async () => {
        const r = await apiOk<{ items: { dataset_id: string; column_name: string }[] }>(
          'GET',
          `/search/columns?q=${encodeURIComponent(token)}&limit=6`,
        )
        return r.items.filter((c) => c.dataset_id === ds.id && c.column_name === column).length
      },
      { timeout: 20_000 },
    )
    .toBeGreaterThan(0)

  await goto(page, '/runs')
  await openPalette(page)
  await palette(page).locator('[data-slot="command-input"]').fill(token)

  const columns = group(page, 'Columns')
  await expect(columns).toBeVisible()
  const hit = columns.locator('[data-slot="command-item"]').filter({ hasText: column })
  await expect(hit).toHaveCount(1)
  // A column name alone is ambiguous across a catalog; the hit must carry the
  // dataset it belongs to.
  await expect(hit).toContainText(ds.name)

  // Results outrank navigation, with BOTH result groups ahead of it.
  const order = await groupOrder(page)
  expect(order.indexOf('Datasets')).toBeGreaterThanOrEqual(0)
  expect(order.indexOf('Columns')).toBeGreaterThanOrEqual(0)
  expect(order.indexOf('Datasets')).toBeLessThan(order.indexOf('Go to'))
  expect(order.indexOf('Columns')).toBeLessThan(order.indexOf('Go to'))
  expect(order.indexOf('Columns')).toBeLessThan(order.indexOf('Tools'))

  await hit.click()

  await expect(palette(page)).toHaveCount(0)
  await expect(page).toHaveURL(new RegExp(`/column\\?.*dataset=${ds.id}`))
  await expect(page).toHaveURL(new RegExp(`column=${column}`))
  await expect(page.getByTestId('column-name')).toHaveText(column)
})

test('Recent is per seat — one seat never sees another seat history', async ({ page, h }) => {
  const mine = await h.seed([{ id: 1, city: 'Oslo' }], 'seatA')
  const theirs = await h.seed([{ id: 2, city: 'Bergen' }], 'seatB')

  // Seat one opens `mine`.
  await goto(page, `/data?dataset=${mine.id}`)
  await expect(page.getByTestId('dataset-header')).toContainText(mine.name)
  await openPalette(page)
  await expect(group(page, 'Recent')).toContainText(mine.name)
  await page.keyboard.press('Escape')

  // Seat two opens `theirs`. Same browser, same origin — the isolation has to
  // come from the store being seat-keyed, not from a fresh profile.
  const viewer = await h.viewer()
  await h.asSeat(page, viewer.user_id, `${viewer.name} (${viewer.role})`)
  await goto(page, `/data?dataset=${theirs.id}`)
  await expect(page.getByTestId('dataset-header')).toContainText(theirs.name)

  await openPalette(page)
  const recent = group(page, 'Recent')
  await expect(recent).toContainText(theirs.name)
  await expect(recent, "seat A's history is visible to seat B").not.toContainText(mine.name)

  // And the whole palette, not just that group — a leak elsewhere is a leak.
  expect(await palette(page).innerText()).not.toContain(mine.name)
})

// ---------------------------------------------------------------------------
// The cockpit KPI strip
// ---------------------------------------------------------------------------

test('the cockpit strip counts exactly what the catalog returned', async ({ page, h }) => {
  const rows = 7
  const ds = await h.seed(
    Array.from({ length: rows }, (_, i) => ({ id: i, city: `city-${i}` })),
    'cockpit',
  )

  /**
   * Capture the catalog payload THIS page load received.
   *
   * An independent `GET /datasets` a second later is not comparable: other
   * agents drive this same API concurrently and seed datasets continuously, so
   * a strict count taken after the render would be racing them. Asserting
   * against the exact bytes the page was given still proves the arithmetic —
   * which is the thing that can be wrong — and cannot be raced.
   */
  const payloads: { items: { id: string; row_count: number | null }[] }[] = []
  page.on('response', async (r) => {
    const url = new URL(r.url())
    if (
      r.request().method() === 'GET' &&
      url.pathname.endsWith('/datasets') &&
      !url.searchParams.get('offset') &&
      r.status() === 200
    ) {
      try {
        payloads.push(await r.json())
      } catch {
        // A body that cannot be read is not the catalog response.
      }
    }
  })

  await goto(page, `/data?dataset=${ds.id}`)
  await expect(page.getByTestId('dataset-header')).toContainText(ds.name)

  // The body is read asynchronously, so wait for it rather than assume.
  await expect.poll(() => payloads.length, { timeout: 10_000 }).toBeGreaterThan(0)
  const catalog = payloads[payloads.length - 1]

  // A fresh read still has to know about the fixture, so the captured payload
  // is anchored to the live service rather than trusted on its own.
  const fresh = await apiOk<{ items: { id: string }[] }>(
    'GET',
    `/datasets?q=${encodeURIComponent(ds.name)}`,
  )
  expect(fresh.items.map((d) => d.id)).toContain(ds.id)
  expect(catalog.items.map((d) => d.id)).toContain(ds.id)

  const expectedCount = catalog.items.length
  const expectedRows = catalog.items.reduce((sum, d) => sum + (d.row_count ?? 0), 0)
  expect(expectedRows).toBeGreaterThanOrEqual(rows)

  await expect(metric(page, 'Datasets').locator('[data-slot="figure"]')).toHaveText(
    expectedCount.toLocaleString(),
  )
  await expect(metric(page, 'Rows stored').locator('[data-slot="figure"]')).toHaveText(
    compact(expectedRows),
  )

  // Classification is a LABEL. The strip has to say so, because a governance
  // count that looks like a control is worse than no count.
  await expect(metric(page, 'Classified').locator('[data-slot="footnote"]')).toHaveText(
    'label only — enforces nothing',
  )
  const classified = catalog.items.filter(
    (d) =>
      (d as { classification?: string }).classification === 'restricted' ||
      (d as { classification?: string }).classification === 'confidential',
  ).length
  await expect(metric(page, 'Classified').locator('[data-slot="figure"]')).toHaveText(
    String(classified),
  )
})

test('"Masked here" is about the dataset on screen, and names it', async ({ page, h }) => {
  const ds = await h.seed(
    Array.from({ length: 4 }, (_, i) => ({ id: i, ssn: `SSN-${i}` })),
    'maskedhere',
  )
  await h.markSensitive(ds.id, 'ssn')

  // Masking bites a viewer, not an admin, so the count is only non-zero from a
  // seat that is actually masked.
  const viewer = await h.viewer()
  await h.asSeat(page, viewer.user_id, `${viewer.name} (${viewer.role})`)
  await goto(page, `/data?dataset=${ds.id}`)
  await expect(page.getByTestId('dataset-header')).toContainText(ds.name)

  const masked = metric(page, 'Masked here')
  // One column was marked sensitive, and the grid's own badge is the second
  // witness — the strip must agree with it rather than count something else.
  await expect(masked.locator('[data-slot="figure"]')).toHaveText('1')
  await expect(masked.locator('[data-slot="footnote"]')).toHaveText(`on ${ds.name}`)
  expect(digits(await page.getByTestId('grid-masked-count').textContent())).toBe(1)

  // "here" has to mean here: a dataset with nothing sensitive reads 0 and
  // names ITSELF, not the previous dataset.
  const clean = await h.seed([{ id: 1, city: 'Oslo' }], 'maskednone')
  await goto(page, `/data?dataset=${clean.id}`)
  await expect(page.getByTestId('dataset-header')).toContainText(clean.name)
  await expect(masked.locator('[data-slot="figure"]')).toHaveText('0')
  await expect(masked.locator('[data-slot="footnote"]')).toHaveText(`on ${clean.name}`)
})

// ---------------------------------------------------------------------------
// The query-token row
// ---------------------------------------------------------------------------

test('the token row states the acting seat and the masking that seat is under', async ({
  page,
  h,
}) => {
  const ds = await h.seed(
    Array.from({ length: 6 }, (_, i) => ({ id: i, ssn: `SSN-${i}`, city: `city-${i}` })),
    'tokenrole',
  )
  await h.markSensitive(ds.id, 'ssn')

  const viewer = await h.viewer()
  const label = `${viewer.name} (${viewer.role})`
  await h.asSeat(page, viewer.user_id, label)
  await goto(page, `/data?dataset=${ds.id}`)
  await expect(page.getByTestId('dataset-header')).toContainText(ds.name)

  const row = page.getByTestId('query-token-row')
  // The seat's ROLE is stated beside the count, because the same query answers
  // differently per seat and a bare number invites the wrong comparison.
  //
  // The role, not the stored display label: the label is written once at switch
  // time and never reconciled, so after an admin changes a seat's role it goes
  // on naming the old one while the service masks per the new one. This asserts
  // the service's own answer from `/auth/me`.
  await expect(row).toContainText(`role ${viewer.role}`)

  // And the masking is stated here too, agreeing with the grid's badge.
  const gridMasked = digits(await page.getByTestId('grid-masked-count').textContent())
  expect(gridMasked).toBe(1)
  await expect(row).toContainText(`${gridMasked} masked`)

  // The reason is available, not merely the fact — a masked column cannot be
  // filtered or sorted, which is why it is absent from the filter list.
  const note = row.locator('[title*="not filterable"]')
  await expect(note).toHaveCount(1)
  await expect(note).toHaveAttribute('title', /ssn/)
})

/**
 * DEFECT — the stated role is a stored display string, not the seat's role.
 *
 * `QueryTokenRow` renders `identity.label` from `localStorage`. That label is
 * written once, by `SeatSwitcher`, at the moment of the switch. Nothing ever
 * reconciles it against the service, so the moment the seat's role changes in
 * `/admin` the row keeps stating the OLD role — forever, because localStorage
 * persists — while the service masks according to the new one. The number and
 * the seat beside it then disagree, which is the exact confusion the row was
 * added to prevent.
 *
 * This test forges the divergence directly (the label says admin, the seat is
 * a viewer) because that is the end state of the staleness above, and it is
 * reproducible without racing a role change.
 *
 * The row states the role the SERVICE is acting on, read from `GET /auth/me`,
 * rather than `identity.label` — which the seat switcher writes to
 * localStorage once and never reconciles, so after an admin changes a seat's
 * role it went on naming the old one while the service masked per the new one.
 * Fixed; this is the regression guard.
 */
test(
  'the token row states the seat real role, not a stale stored label',
  async ({ page, h }) => {
    const ds = await h.seed(
      Array.from({ length: 5 }, (_, i) => ({ id: i, ssn: `SSN-${i}` })),
      'rolestale',
    )
    await h.markSensitive(ds.id, 'ssn')

    const viewer = await h.viewer()
    // A stale label left behind by an earlier, higher-privileged seat.
    await h.asSeat(page, viewer.user_id, 'System (admin)')
    await goto(page, `/data?dataset=${ds.id}`)
    await expect(page.getByTestId('dataset-header')).toContainText(ds.name)

    // The service is unambiguous about who is acting: this seat is masked, so
    // it is NOT an admin.
    expect(digits(await page.getByTestId('grid-masked-count').textContent())).toBe(1)
    const me = await apiOk<{ memberships: { role: string }[] }>('GET', '/auth/me', {
      userId: viewer.user_id,
    })
    const realRole = me.memberships[0].role
    expect(realRole).toBe('viewer')

    const row = page.getByTestId('query-token-row')
    await expect(row, 'the row states a role the service is not acting on').toContainText(
      `role ${realRole}`,
    )
  },
)

test('the token row matched count agrees with the grid total', async ({ page, h }) => {
  const rows = 23
  const ds = await h.seed(
    Array.from({ length: rows }, (_, i) => ({ id: i, city: `city-${i % 4}` })),
    'matched',
  )
  await goto(page, `/data?dataset=${ds.id}`)
  await expect(page.getByTestId('dataset-header')).toContainText(ds.name)

  // Three independent readings of one number: the grid toolbar, the token row,
  // and the fixture. All three have to be the same, or one of them is lying.
  const gridTotal = digits(await page.getByTestId('row-count').textContent())
  expect(gridTotal).toBe(rows)

  const matchedText = await page.getByTestId('query-token-row').innerText()
  const matched = /matched\s+([\d,]+)/.exec(matchedText)
  expect(matched, `token row states no matched count: ${matchedText}`).not.toBeNull()
  expect(Number(matched![1].replace(/,/g, ''))).toBe(gridTotal)

  // Nothing is filtering, so matched is the whole sheet — and the row says so
  // by showing the total alongside.
  expect(matchedText).toContain(`/ ${compact(rows)}`)
})

// ---------------------------------------------------------------------------
// The contextual rail — extending `shape.spec.ts`, which owns wide/narrow mode
// ---------------------------------------------------------------------------

test('the rail counts the rows it renders, and marks the dataset on screen', async ({ page, h }) => {
  const ds = await h.seed([{ id: 1, city: 'Oslo' }], 'rail')
  await goto(page, `/data?dataset=${ds.id}`)
  await expect(page.getByTestId('dataset-header')).toContainText(ds.name)

  const rail = page.locator('aside').filter({ has: page.getByTestId('rail-search') })
  const rows = rail.getByTestId('rail-dataset')

  // The count in the rail's own header is the number of rows below it — not a
  // server `total` the list was truncated from. There is no testid on that
  // span, so it is addressed by its position after the rail's title.
  const rendered = await rows.count()
  expect(rendered).toBeGreaterThan(0)
  await expect(rail.locator('span.text-label + span').first()).toHaveText(String(rendered))

  // Exactly one row is current, and it is the dataset the main pane is showing.
  const current = rail.locator('[data-testid="rail-dataset"][aria-current="true"]')
  await expect(current).toHaveCount(1)
  await expect(current).toContainText(ds.name)

  // Filtering to this fixture leaves exactly it, and it stays selected.
  await rail.getByTestId('rail-search').fill(ds.name)
  await expect(rows).toHaveCount(1)
  await expect(rows.first()).toContainText(ds.name)
  await expect(page.getByTestId('dataset-header')).toContainText(ds.name)
})

test('the theme toggle flips the theme on the first press', async ({ page, h }) => {
  // It used to compare the STORED preference, which defaults to `system`, so
  // the first click computed `'system' === 'dark'` → false → set `dark`, which
  // was already the resolved theme. The button did nothing until pressed twice.
  const ds = await h.seed([{ id: 1 }], 'themetoggle')
  await goto(page, `/data?dataset=${ds.id}`)

  const resolved = () =>
    page.evaluate(() =>
      document.documentElement.classList.contains('light') ? 'light' : 'dark',
    )

  const before = await resolved()
  await page.getByLabel('Toggle theme').click()
  await expect.poll(resolved).not.toBe(before)

  // And back, so it is a toggle rather than a one-way switch.
  await page.getByLabel('Toggle theme').click()
  await expect.poll(resolved).toBe(before)
})

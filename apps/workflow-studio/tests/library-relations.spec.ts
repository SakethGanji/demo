import { test, expect, goto, api, apiOk, uniqueName, dismissToasts } from './fixtures'
import type { Page } from '@playwright/test'

/**
 * The library and relations lenses, driven in a real browser.
 *
 * `lenses.spec.ts` proves each of these surfaces renders at all. This proves
 * the two claims they exist to make, which are the ones a screenshot cannot
 * check:
 *
 *  - **Configuration is not a result.** A chart, a saved view and a definition
 *    store no rows; running one is a separate, explicit step, and only some
 *    runs write bytes. A panel that presents a saved definition as an answer is
 *    the failure mode.
 *  - **Every count states its population.** Matched join rows over the left
 *    side's rows, downloads over logged events, a render's rows over the
 *    source's total. A bare count is the silent-wrong-answer class this
 *    codebase spent an audit removing, reintroduced at the presentation layer.
 *
 * Plus the honesty constraints either lens can quietly break: retention is
 * stamped at write time and nothing sweeps on a timer (so "past window" means
 * *eligible*, never *deleted*), a capped discovery sweep must say it was
 * capped, the visible-edge count is not the dataset's total, and a lineage row
 * whose other side is invisible keeps the row while losing the name — because
 * a cross-tenant read is answered as absence, and "access denied" would
 * disclose the existence the 404 exists to hide.
 */

/* ------------------------------------------------------------------ helpers */

interface ApiPage<T> {
  items: T[]
  total: number
}

/** Deep-link to a dataset and open one lens. Never relies on the default. */
async function openLens(page: Page, datasetId: string, lens: 'library' | 'relationships') {
  await goto(page, `/data?dataset=${datasetId}`)
  await page.getByTestId(`lens-${lens}`).click()
  await expect(page.getByTestId(`lens-${lens}`)).toHaveAttribute('aria-pressed', 'true')
}

/**
 * The action button of a lens section, found through its heading.
 *
 * The three "New" buttons in the library dock are identical, so a bare
 * `getByRole('button', { name: 'New' })` matches whichever renders first —
 * exactly the scoping bug rule 4 exists for. The heading is the only thing
 * that tells them apart.
 */
function sectionAction(page: Page, title: RegExp) {
  return page.getByRole('heading', { name: title }).locator('xpath=following-sibling::button')
}

/** Re-read until the server has the row, then hand it back. */
async function pollFor<T>(read: () => Promise<T | undefined>, what: string): Promise<T> {
  let found: T | undefined
  await expect
    .poll(
      async () => {
        found = await read()
        return found !== undefined
      },
      { message: `${what} never appeared on a re-read` },
    )
    .toBe(true)
  return found as T
}

/** `coverageNote()` as `Stat` prints it, so the expectation is derived not typed. */
function coverageNote(counted: number, total: number, unit: string): string {
  const pct = ((counted / total) * 100).toFixed(1).replace(/\.0$/, '')
  return `over ${pct}% · ${counted.toLocaleString('en-US')} ${unit}`
}

interface ArtifactRow {
  filename: string
  file_type: string
  dataset_id: string | null
}

/** Artifacts the service has registered against one dataset. */
async function artifactsOf(datasetId: string): Promise<ArtifactRow[]> {
  const samples = await apiOk<ApiPage<ArtifactRow>>('GET', '/samples?limit=200')
  return samples.items.filter((a) => a.dataset_id === datasetId)
}

/* ============================================================== LIBRARY LENS */

test('the library groups artifacts by kind and states the clock each kind carries', async ({
  page,
  h,
}) => {
  const ds = await h.seed(
    Array.from({ length: 12 }, (_, i) => ({ region: ['EU', 'US'][i % 2], amount: i })),
    'artifacts',
  )

  // Three kinds, deliberately: one statement of retention has to cover a
  // block, and a single-kind list cannot show that it groups at all.
  const agg = await apiOk<{ result_file: string }>('POST', '/aggregate', {
    body: {
      dataset_id: ds.id,
      version_number: 1,
      sheet: 'data',
      group_by: ['region'],
      aggregations: [{ column: 'amount', function: 'sum', alias: 'total' }],
    },
  })
  await apiOk('POST', `/samples/${agg.result_file}/export?format=csv`)
  // An output that matched nothing — a real artifact holding zero rows.
  const emptyQuery = await apiOk<{ result_file: string; row_count: number }>(
    'POST',
    `/datasets/${ds.id}/versions/1/sql`,
    { body: { sql: 'SELECT * FROM data WHERE 1=0' } },
  )
  expect(emptyQuery.row_count).toBe(0)

  const stored = await artifactsOf(ds.id)
  expect(stored.map((a) => a.file_type).sort()).toEqual([
    'aggregation_output',
    'export',
    'query_output',
  ])

  await openLens(page, ds.id, 'library')

  // The list is the artifacts TABLE, so the rows on screen are exactly the
  // rows the service registered — no more (a bucket scan) and no fewer.
  await expect(page.getByTestId('artifact')).toHaveCount(stored.length)
  for (const a of stored) {
    await expect(
      page.getByTestId('artifact').filter({ has: page.locator(`span[title="${a.filename}"]`) }),
    ).toHaveCount(1)
  }

  const groups = page.getByTestId('artifact-group')
  await expect(groups).toHaveCount(3)

  // Retention per kind, as `app/features/files/services/retention.py` declares
  // it: export and query_output 7d, aggregation_output 30d. The panel must not
  // invent its own policy.
  await expect(groups.filter({ hasText: 'export_' })).toHaveText(/kept 7d/)
  await expect(groups.filter({ hasText: 'query_output' })).toHaveText(/kept 7d/)
  const aggregated = groups.filter({ hasText: 'aggregation_output' })
  await expect(aggregated).toHaveText(/kept 30d/)

  // Groups are ordered by what expires first, so the 7-day kinds lead and the
  // 30-day kind is last.
  await expect(groups.first()).toContainText('export')
  await expect(groups.last()).toContainText('aggregation_output')

  // And each row carries its own countdown against that clock.
  const countdown = aggregated.getByTestId('artifact-retention')
  await expect(countdown).toHaveText(/in \d+d/)
  await expect(countdown).toContainText('of 30d')

  // The download goes through /samples/{filename} — the artifact row is the
  // only thing that maps a filename to a storage key.
  const href = await page.getByTestId('artifact-download').first().getAttribute('href')
  expect(href).toContain('/samples/')

  // Nothing schedules `artifact_gc`. "Past window" is eligibility, not a
  // deletion, and the panel may never claim otherwise.
  const sweep = page
    .locator('[data-slot="guard"]')
    .filter({ hasText: 'Nothing runs the sweep on a schedule' })
  await expect(sweep).toContainText('eligible for collection, not collected')
  expect(await sweep.innerText()).not.toMatch(/delet/i)
  for (const text of await page.getByTestId('artifact-retention').allInnerTexts()) {
    expect(text).not.toMatch(/delet/i)
  }

  // Reading an EMPTY artifact back: zero rows is a state, not a statistic, so
  // the peek prints "not computed" rather than dividing by a zero population.
  // This is the only place in the library dock where an empty population is
  // reachable — see the usage test for why the events counter never is.
  const empty = page
    .getByTestId('artifact')
    .filter({ has: page.locator(`span[title="${emptyQuery.result_file}"]`) })
  await empty.locator('button[title="Read rows"]').click()
  const peekRows = empty.locator('[data-slot="stat"]').filter({ hasText: 'rows' })
  await expect(peekRows).toContainText('no rows — not computed')
  expect(await peekRows.innerText()).not.toContain('%')
})

test('usage states downloads and writes over the population of logged events', async ({
  page,
  h,
}) => {
  const ds = await h.seed([{ id: 1 }, { id: 2 }], 'usage')
  const readUsage = () =>
    apiOk<{ downloads: number; writes: number; total_events: number }>(
      'GET',
      `/datasets/${ds.id}/usage`,
    )

  // Upload posts to /upload, which is not a `/datasets/{id}` path, so a fresh
  // fixture starts at zero.
  expect((await readUsage()).total_events).toBe(0)

  // Give it a real population: two writes and one download, all on
  // `/datasets/{id}` paths, which is what `dataset_usage` counts.
  await apiOk('POST', `/datasets/${ds.id}/rules`, {
    body: {
      name: `nn-${Math.random().toString(36).slice(2, 8)}`,
      rule_type: 'not_null',
      sheet_selector: 'data',
      column_selector: 'id',
      parameters: {},
    },
  })
  await apiOk('PATCH', `/datasets/${ds.id}`, { body: { description: 'usage fixture' } })
  await apiOk('GET', `/datasets/${ds.id}/download?format=csv`)

  // The audit row is written by middleware, so wait for the service to agree
  // rather than assuming it has landed.
  await expect.poll(async () => (await readUsage()).total_events).toBe(3)
  const before = await readUsage()
  expect(before.downloads).toBe(1)

  await openLens(page, ds.id, 'library')

  /**
   * Opening the dataset is ITSELF an audited event — the grid's read is a
   * `POST /datasets/{id}/versions/{v}/sheets/{s}/query`, and `dataset_usage`
   * classifies any POST on a `/datasets/{id}` path as a write. So the panel's
   * population is whatever had landed when its `/usage` request was served,
   * which races the grid's query by design. The assertions below are therefore
   * derived from the DOM's own denominator and bracketed by two API reads,
   * rather than pinned to a number that is only true on one side of the race.
   *
   * The same fact makes the empty-population branch ("no events — not
   * computed") unreachable from this surface: by the time the panel renders,
   * at least one event exists. That branch is proved instead on the artifact
   * peek in the artifacts test, which reaches a genuinely empty population.
   */
  const after = await readUsage()

  const footnote = page.locator('[data-slot="footnote"]').filter({ hasText: /logged event/ })
  const shownTotal = Number(
    /([\d,]+) logged event/.exec(await footnote.innerText())![1].replace(/,/g, ''),
  )
  expect(shownTotal).toBeGreaterThanOrEqual(before.total_events)
  expect(shownTotal).toBeLessThanOrEqual(after.total_events)

  // `1 download` and `1 download of 4 events` are different claims, so the
  // count on screen is stated over that same population — and the percentage
  // is genuinely counted/total, not decoration.
  const stat = async (name: 'downloads' | 'writes') => {
    const text = await page.locator('[data-slot="stat"]').filter({ hasText: name }).innerText()
    const m = /over ([\d.]+)% · ([\d,]+) events/.exec(text)
    expect(m, `${name} carries no denominator: ${text}`).toBeTruthy()
    const counted = Number(m![2].replace(/,/g, ''))
    expect(m![1]).toBe(((counted / shownTotal) * 100).toFixed(1).replace(/\.0$/, ''))
    return counted
  }

  // Downloads cannot move while the page is open — nothing here downloads —
  // so this one is exact.
  expect(await stat('downloads')).toBe(before.downloads)
  const writes = await stat('writes')
  expect(writes).toBeGreaterThanOrEqual(before.writes)
  expect(writes).toBeLessThanOrEqual(after.writes)
})

test('the favourite mark round-trips through the catalog, not through the toast', async ({
  page,
  h,
}) => {
  const ds = await h.seed([{ id: 1 }], 'favourite')

  const isFavorite = async () => {
    const catalog = await apiOk<ApiPage<{ id: string; is_favorite?: boolean }>>(
      'GET',
      `/datasets?q=${encodeURIComponent(ds.name)}&limit=200`,
    )
    const row = catalog.items.find((d) => d.id === ds.id)
    expect(row, `fixture ${ds.name} missing from the catalog`).toBeTruthy()
    return Boolean(row?.is_favorite)
  }

  expect(await isFavorite()).toBe(false)

  await openLens(page, ds.id, 'library')
  const star = page.getByTestId('library-favorite')
  await expect(star).toContainText('Add to favourites')

  await star.click()
  await expect(star).toContainText('Favourited')
  // `is_favorite` is a field of the catalog listing, so the catalog is the
  // evidence — the button's own label is only the claim being checked.
  expect(await isFavorite()).toBe(true)

  await star.click()
  await expect(star).toContainText('Add to favourites')
  expect(await isFavorite()).toBe(false)
})

test('a chart is created, renamed, rendered with a denominator, and deleted', async ({
  page,
  h,
}) => {
  // Past 1000 rows a view-backed render reads one page and reports a larger
  // total, which is the only way a render's coverage is observably partial —
  // and a render that claims completeness for a clipped page is the exact lie
  // `total_rows` exists to prevent.
  const ds = await h.seed(
    Array.from({ length: 1100 }, (_, i) => ({
      id: i,
      region: ['EU', 'US', 'APAC'][i % 3],
      amount: i * 2,
    })),
    'chart',
  )

  // A chart renders a source it does not own. The source is fixture setup; the
  // chart itself is created through the UI.
  const view = await apiOk<{ id: string }>('POST', `/datasets/${ds.id}/views`, {
    body: {
      name: uniqueName('chartsrc'),
      sheet: 'data',
      version_selector: { mode: 'current' },
      query: { limit: 100 },
    },
  })

  await openLens(page, ds.id, 'library')

  const name = uniqueName('chart')
  await sectionAction(page, /^Charts \(/).click()
  await page.getByLabel('New chart name', { exact: true }).fill(name)
  await page.getByLabel('New chart source', { exact: true }).selectOption(`view:${view.id}`)
  await page.getByRole('button', { name: 'Save chart' }).click()

  // Re-read: the chart is a row, not a toast.
  const created = await pollFor(
    async () =>
      (
        await apiOk<ApiPage<{ id: string; name: string }>>('GET', `/datasets/${ds.id}/charts`)
      ).items.find((c) => c.name === name),
    `chart ${name}`,
  )
  const chartId = created.id

  await expect(page.getByTestId('saved-chart').filter({ hasText: name })).toHaveCount(1)

  // --- rename, then re-read ------------------------------------------------
  const renamed = `${name}-renamed`
  const entry = page.getByTestId('saved-chart').filter({ hasText: name })
  await entry.getByRole('button', { name: 'Open' }).click()
  await entry.getByLabel('Chart name', { exact: true }).fill(renamed)
  await entry.getByRole('button', { name: 'Rename' }).click()
  await expect
    .poll(
      async () =>
        (await apiOk<{ name: string }>('GET', `/datasets/${ds.id}/charts/${chartId}`)).name,
    )
    .toBe(renamed)

  // --- render: a read that persists nothing --------------------------------
  const after = page.getByTestId('saved-chart').filter({ hasText: renamed })
  await after.getByRole('button', { name: /Render/ }).click()

  // The same call the button makes, so the expected figures are measured, not
  // invented. A render writes no run row and no artifact, so repeating it has
  // no side effect to undo.
  const render = await apiOk<{ row_count: number; total_rows: number | null }>(
    'POST',
    `/datasets/${ds.id}/charts/${chartId}/render`,
  )
  const shown = render.row_count
  const total = render.total_rows ?? render.row_count
  expect(total).toBeGreaterThan(shown) // the fixture is sized so this is partial

  const rows = after.locator('[data-slot="stat"]').filter({ hasText: 'rows' })
  await expect(rows).toContainText(total.toLocaleString('en-US'))
  await expect(rows).toContainText(coverageNote(shown, total, 'rows'))
  await expect(after).toContainText('Nothing was stored')
  expect(await artifactsOf(ds.id)).toEqual([])

  // --- delete, then re-read ------------------------------------------------
  // Close the detail panel first. Deleting with it open makes `useChart`
  // refetch an id that no longer exists, and the resulting 404 is a state the
  // panel handles ("This chart is no longer there.") but a console error the
  // harness rightly refuses to ignore.
  await after.getByRole('button', { name: 'Close' }).click()

  // The Actions row is Open / Render / delete, and the delete carries an icon
  // only — so it is addressed by position rather than by an accessible name.
  await after.locator('button').nth(2).click()
  const dialog = page.locator('[data-slot="alert-dialog-content"]')
  await expect(dialog).toContainText('A chart owns no query logic')
  await dialog.getByRole('button', { name: 'Delete' }).click()

  // A delete that succeeded must not be reported as a failure — the bug that
  // put "verify writes by re-reading" in this suite's rules.
  await expect(page.locator('[data-sonner-toast][data-type="error"]')).toHaveCount(0)

  await expect
    .poll(async () =>
      (await apiOk<ApiPage<{ id: string }>>('GET', `/datasets/${ds.id}/charts`)).items.map(
        (c) => c.id,
      ),
    )
    .not.toContain(chartId)
  await expect(page.getByTestId('saved-chart').filter({ hasText: renamed })).toHaveCount(0)

  // Deleting an annotation removes no data: the source it rendered survives.
  const views = await apiOk<ApiPage<{ id: string }>>('GET', `/datasets/${ds.id}/views`)
  expect(views.items.map((v) => v.id)).toContain(view.id)
})

test('a saved view is configuration until it is run, and the run names version, sheet and rows', async ({
  page,
  h,
}) => {
  const ds = await h.seed(
    Array.from({ length: 12 }, (_, i) => ({ id: i, city: `c${i}` })),
    'view',
  )

  await openLens(page, ds.id, 'library')

  const name = uniqueName('view')
  await sectionAction(page, /^Saved views \(/).click()
  await page.getByLabel('New view name', { exact: true }).fill(name)

  // Saving is not running, and the form says so before the click.
  await expect(
    page.locator('[data-slot="guard"]').filter({ hasText: 'Creating it runs nothing' }),
  ).toContainText('Run is a separate step')

  await page.getByRole('button', { name: 'Save view' }).click()

  const saved = await pollFor(
    async () =>
      (
        await apiOk<ApiPage<{ id: string; name: string }>>('GET', `/datasets/${ds.id}/views`)
      ).items.find((v) => v.name === name),
    `view ${name}`,
  )

  // Creating a view computes nothing, so nothing is stored yet.
  expect(await artifactsOf(ds.id)).toEqual([])

  const entry = page.getByTestId('saved-view').filter({ hasText: name })
  await expect(entry).toHaveCount(1)
  await entry.getByRole('button', { name: 'Run' }).click()

  // The same execution, measured from the service, so the panel's figures are
  // compared against a run rather than against a number typed here.
  const run = await apiOk<{
    version_number: number
    sheet_name: string
    result: { items: unknown[]; total: number | null }
  }>('POST', `/datasets/${ds.id}/views/${saved.id}/run`, { body: {} })
  expect(run.result.total).toBe(12)

  await expect(entry).toContainText(`ran against v${run.version_number}`)
  await expect(entry).toContainText(run.sheet_name)
  await expect(entry.locator('[data-slot="stat"]').filter({ hasText: 'rows' })).toContainText(
    String(run.result.total),
  )

  // A view run reads rows and writes nothing — no artifact appears.
  expect(await artifactsOf(ds.id)).toEqual([])
})

test('a profile definition is saved configuration, and its run states a word and stores no file', async ({
  page,
  h,
}) => {
  const ds = await h.seed(
    Array.from({ length: 20 }, (_, i) => ({ amount: i * 3, region: ['EU', 'US'][i % 2] })),
    'definition',
  )

  await openLens(page, ds.id, 'library')

  const name = uniqueName('profiledef')
  await sectionAction(page, /^Saved analyses \(/).click()
  await page.getByLabel('New definition name', { exact: true }).fill(name)
  await page.getByRole('button', { name: 'Save definition' }).click()

  const definition = await pollFor(
    async () =>
      (
        await apiOk<ApiPage<{ id: string; name: string; kind: string }>>(
          'GET',
          `/datasets/${ds.id}/analytics`,
        )
      ).items.find((d) => d.name === name),
    `definition ${name}`,
  )
  expect(definition.kind).toBe('profile')

  const entry = page.getByTestId('saved-analysis').filter({ hasText: name })
  await expect(entry).toHaveCount(1)

  // The save raised a success toast, and the Toaster sits over the dock's
  // buttons. Clear it before reaching for one.
  await dismissToasts(page)

  // Saved, and by construction not yet computed: the run history says exactly
  // that, rather than showing an empty result as if it were an answer.
  await entry.getByRole('button', { name: 'Open' }).click()
  await expect(entry).toContainText('Never run')
  await expect(entry).toContainText('The definition is saved configuration')
  expect(
    (await apiOk<ApiPage<unknown>>('GET', `/datasets/${ds.id}/analytics/${definition.id}/runs`))
      .items,
  ).toEqual([])

  // Running it is the separate step.
  await dismissToasts(page)
  await entry.getByRole('button', { name: 'Run' }).click()

  // Poll to a TERMINAL status, not merely to the run's existence.
  //
  // A run can advance (`running` → `completed`) between the API read and the
  // DOM assertion, so snapshotting whatever status happened to be current and
  // then requiring the UI to still show it is a race — and it is the race that
  // made this test flaky under full-suite load. Settling first makes the
  // comparison meaningful: both sides are then looking at the same final word.
  const TERMINAL = /^(completed|succeeded|failed|error|cancelled|canceled)$/
  const runs = await pollFor(async () => {
    const page_ = await apiOk<ApiPage<{ status: string; artifact_id: string | null }>>(
      'GET',
      `/datasets/${ds.id}/analytics/${definition.id}/runs`,
    )
    if (page_.items.length !== 1) return undefined
    return TERMINAL.test(page_.items[0].status) ? page_.items : undefined
  }, `a settled run of ${name}`)

  // The status is shown as the word the service reports, not a code or a hue.
  expect(runs[0].status).toMatch(/^[a-z]+$/)
  await expect(entry).toContainText(runs[0].status)

  // A profile run returns statistics and stores no file — so there is nothing
  // to publish and nothing on a retention clock.
  expect(runs[0].artifact_id).toBeNull()
  await expect(entry).toContainText('no artifact written')
  await expect(entry).toContainText('A profile run returns statistics and stores no file')
  expect(await artifactsOf(ds.id)).toEqual([])
})

/* ============================================================ RELATIONS LENS */

/** Two sheets that reference each other imperfectly — 14 keys used, 10 exist. */
const joinWorkbook = () => ({
  Customers: Array.from({ length: 10 }, (_, i) => ({
    customer_id: i + 1,
    region_code: 10 + (i % 3),
  })),
  Orders: Array.from({ length: 20 }, (_, i) => ({
    order_id: i + 1,
    customer_id: (i % 14) + 1,
    region_code: 10 + (i % 3),
  })),
})

/** A `foreign_key` quality rule — the declaration `seed` projects onto an edge. */
async function fkRule(datasetId: string, column: string) {
  await apiOk('POST', `/datasets/${datasetId}/rules`, {
    body: {
      name: `fk-${column}-${Math.random().toString(36).slice(2, 8)}`,
      rule_type: 'foreign_key',
      sheet_selector: 'Orders',
      column_selector: column,
      parameters: { ref_sheet: 'Customers', ref_column: column },
    },
  })
}

interface Edge {
  id: string
  from_sheet: string | null
  from_column: string | null
  status: string
  method: string | null
}

const edgesOf = (datasetId: string) =>
  apiOk<ApiPage<Edge>>('GET', `/datasets/${datasetId}/relationships`)

test('seeding from foreign-key rules creates edges, and confirm and reject each persist', async ({
  page,
  h,
}) => {
  const wb = await h.seedWorkbook(joinWorkbook(), 'seed')
  await fkRule(wb.id, 'customer_id')
  await fkRule(wb.id, 'region_code')

  await openLens(page, wb.id, 'relationships')
  await expect(page.getByTestId('relationship')).toHaveCount(0)

  await page.getByTestId('relationship-seed').click()
  await expect(page.getByTestId('relationship')).toHaveCount(2)

  const seeded = await edgesOf(wb.id)
  expect(seeded.items.length).toBe(2)
  // Discovery never confirms: a seeded edge arrives as a candidate.
  expect(seeded.items.every((e) => e.status === 'suggested')).toBe(true)
  expect(seeded.items.every((e) => e.method === 'fk_rule')).toBe(true)

  const keep = seeded.items.find((e) => e.from_column === 'customer_id')!
  const drop = seeded.items.find((e) => e.from_column === 'region_code')!

  // The count on screen is what this seat can see, and the panel says so
  // rather than passing it off as the dataset's total.
  await expect(
    page.locator('[data-slot="metric"]').filter({ hasText: 'visible to this seat' }),
  ).toContainText(String(seeded.items.length))
  await expect(page.getByTestId('lens-body')).toContainText("not the dataset's total")

  // --- confirm persists ----------------------------------------------------
  await page
    .getByTestId('relationship')
    .filter({ hasText: `${keep.from_sheet}.${keep.from_column}` })
    .getByTestId('relationship-confirm')
    .click()
  await expect
    .poll(async () => (await edgesOf(wb.id)).items.find((e) => e.id === keep.id)?.status)
    .toBe('confirmed')

  // --- reject persists -----------------------------------------------------
  await page
    .getByTestId('relationship')
    .filter({ hasText: `${drop.from_sheet}.${drop.from_column}` })
    .getByTestId('relationship-reject')
    .click()
  await expect
    .poll(async () => (await edgesOf(wb.id)).items.find((e) => e.id === drop.id)?.status)
    .toBe('rejected')

  // Both verdicts are on screen, and a rejected edge is kept as a record.
  await expect(page.getByTestId('relationship')).toHaveCount(2)
  await expect(page.getByTestId('lens-body')).toContainText('1 confirmed · 1 rejected')
  await expect(page.getByTestId('lens-body')).toContainText('discovery will not re-propose it')
})

test('a capped suggest sweep reports the pairs it never probed', async ({ page, h }) => {
  // 400 probes take several seconds server-side, and the sweep runs twice: once
  // here to learn the true numbers, once through the button.
  test.setTimeout(180_000)

  // The candidate cap is 400 pairs. 401 `*_id` columns against a single `id`
  // column proposes 401, so the cap bites by exactly one — and the values do
  // not overlap, so nothing qualifies and no edge is written.
  const keyColumns = Array.from({ length: 401 }, (_, i) => `c${i}_id`)
  const wb = await h.seedWorkbook(
    {
      Keys: [{ id: 1 }, { id: 2 }, { id: 3 }],
      Facts: Array.from({ length: 3 }, (_, r) =>
        Object.fromEntries(keyColumns.map((c) => [c, 900 + r])),
      ),
    },
    'sweep',
  )

  const expected = await apiOk<{ pairs_examined: number; suggested: number; skipped: number }>(
    'POST',
    `/datasets/${wb.id}/relationships/suggest?sync=true`,
  )
  expect(expected.skipped).toBeGreaterThan(0)

  await openLens(page, wb.id, 'relationships')

  // Before any scan the panel must not imply one has run.
  await expect(page.getByTestId('lens-body')).toContainText('No scan has run from this panel')

  await page.getByTestId('relationship-suggest').click()
  await expect(page.getByTestId('lens-body')).toContainText('last scan, this session', {
    timeout: 120_000,
  })

  const metric = (label: string) =>
    page.locator('[data-slot="metric"]').filter({ hasText: new RegExp(`^${label}`) })
  await expect(metric('probed')).toContainText(expected.pairs_examined.toLocaleString('en-US'))
  await expect(metric('derived')).toContainText(String(expected.suggested))
  await expect(metric('skipped')).toContainText(String(expected.skipped))

  // Silence here would read as "nothing else exists". The sweep was capped,
  // and the panel has to say that it was.
  await expect(page.getByTestId('lens-body')).toContainText(
    `${expected.skipped.toLocaleString('en-US')} candidate pairs were never probed`,
  )
  await expect(page.getByTestId('lens-body')).toContainText('this sweep was not exhaustive')

  // Nothing cleared the threshold, so the edge list agrees with `derived`.
  expect((await edgesOf(wb.id)).items.length).toBe(expected.suggested)
})

test('the join builder offers confirmed edges only, and its preview divides by each side', async ({
  page,
  h,
}) => {
  const wb = await h.seedWorkbook(joinWorkbook(), 'join')
  await fkRule(wb.id, 'customer_id')
  await fkRule(wb.id, 'region_code')
  await apiOk('POST', `/datasets/${wb.id}/relationships/seed`)

  const seeded = await edgesOf(wb.id)
  const confirmed = seeded.items.find((e) => e.from_column === 'customer_id')!
  const candidate = seeded.items.find((e) => e.from_column === 'region_code')!
  await apiOk('POST', `/datasets/${wb.id}/relationships/${confirmed.id}/confirm`)

  await openLens(page, wb.id, 'relationships')

  // A join binds to a relationship id, and only a reviewed one. The candidate
  // is absent rather than greyed: "how do I enable this" has one answer, and
  // the review queue above is already asking it.
  // `evaluateAll` does not auto-wait, so the picker has to be there first.
  const options = page.getByTestId('join-relationship').locator('option')
  await expect(options).toHaveCount(1)
  const offered = await options.evaluateAll((els) =>
    els.map((e) => (e as HTMLOptionElement).value),
  )
  expect(offered).toEqual([confirmed.id])
  expect(offered).not.toContain(candidate.id)

  // And the service agrees: the unreviewed edge cannot drive a join at all.
  const refused = await api('POST', '/joins/execute', {
    body: { relationship_id: candidate.id, how: 'inner' },
  })
  expect(refused.status).toBe(409)
  expect(refused.body.code).toBe('relationship-not-confirmed')

  await page.getByTestId('join-preview').click()
  const warnings = page.getByTestId('join-warnings')
  await expect(warnings).toBeVisible()

  // Preview persists nothing, so measuring it again from here is free — and
  // the expected figures are then the service's, not this file's.
  const preview = await apiOk<{
    warnings: { left_rows: number; right_rows: number; unmatched_left_pct: number }
  }>('POST', '/joins/preview', { body: { relationship_id: confirmed.id, how: 'inner' } })
  const w = preview.warnings
  const matchedLeft = Math.max(
    0,
    w.left_rows - Math.round((w.left_rows * w.unmatched_left_pct) / 100),
  )
  expect(matchedLeft).toBeGreaterThan(0)
  // The fixture references ids beyond the customer list on purpose: a fully
  // matched join would hide a missing denominator behind a complete one.
  expect(matchedLeft).toBeLessThan(w.left_rows)

  // A match count with no denominator is the silent-wrong-answer class. Left
  // is the first statistic in the panel, right the second.
  const left = warnings.locator('[data-slot="stat"]').nth(0)
  await expect(left).toContainText('left')
  await expect(left).toContainText(matchedLeft.toLocaleString('en-US'))
  await expect(left).toContainText(coverageNote(matchedLeft, w.left_rows, 'left rows'))

  await expect(warnings).toContainText(String(w.left_rows))
  await expect(warnings).toContainText(String(w.right_rows))
  await expect(warnings).toContainText(
    'The API returns unmatched as a percentage of rows to two decimals',
  )

  // A preview is a measurement: nothing was stored.
  expect(await artifactsOf(wb.id)).toEqual([])
})

test('lineage keeps a row whose other side is invisible, and never calls it a refusal', async ({
  page,
  h,
}) => {
  const source = await h.seed(
    Array.from({ length: 10 }, (_, i) => ({ region: ['EU', 'US'][i % 2], amount: i })),
    'lineagesrc',
  )

  // Publishing a run is what records lineage: the new dataset carries a parent.
  const definition = await apiOk<{ id: string }>('POST', `/datasets/${source.id}/analytics`, {
    body: {
      name: uniqueName('agg'),
      kind: 'aggregate',
      sheet: 'data',
      version_selector: { mode: 'current' },
      params: {
        group_by: ['region'],
        aggregations: [{ column: 'amount', function: 'sum', alias: 'total' }],
      },
    },
  })
  const run = await apiOk<{ id: string; artifact_id: string | null }>(
    'POST',
    `/datasets/${source.id}/analytics/${definition.id}/run`,
  )
  // An aggregate run DOES write bytes — which is what makes it publishable.
  expect(run.artifact_id).not.toBeNull()
  const published = await apiOk<{ dataset_id: string }>(
    'POST',
    `/datasets/${source.id}/analytics/runs/${run.id}/publish`,
    { body: { mode: 'new_dataset', name: uniqueName('published') } },
  )

  // Remove the parent. `dataset_lineage.parent_dataset_id` is ON DELETE SET
  // NULL, so the edge survives with nothing to resolve — which is exactly what
  // a seat that is not a superuser sees for any parent it cannot read.
  await apiOk('DELETE', `/datasets/${source.id}`)

  const viewer = await h.viewer()
  const lineage = await apiOk<{ parents: { parent_visible: boolean }[] }>(
    'GET',
    `/datasets/${published.dataset_id}/lineage`,
    { userId: viewer.user_id },
  )
  expect(lineage.parents.length).toBe(1)
  expect(lineage.parents[0].parent_visible).toBe(false)

  await h.asSeat(page, viewer.user_id, 'Viewer')
  await openLens(page, published.dataset_id, 'relationships')

  // The row stays: the edge is real even when the name is not.
  const parent = page.getByTestId('lineage-parent')
  await expect(parent).toHaveCount(1)
  await expect(parent).toContainText('A dataset this seat cannot read')
  await expect(page.getByTestId('lens-body')).toContainText(
    'A row with no name is still a real edge',
  )

  // Never a refusal: a cross-tenant read is answered as absence, and "access
  // denied" would disclose the existence the 404 exists to hide.
  const body = await page.getByTestId('lens-body').innerText()
  expect(body).not.toMatch(/access denied/i)
  expect(body).not.toMatch(/\bdenied\b/i)
  expect(body).not.toMatch(/\bforbidden\b/i)
})

test('counterpart search finds a column this test seeded, by name', async ({ page, h }) => {
  // A token that exists nowhere else, so a hit proves the search reached this
  // fixture rather than matching something ambient.
  const token = `zq${Math.random().toString(36).slice(2, 8)}`
  const needle = `${token}_key`
  const ds = await h.seed(
    Array.from({ length: 4 }, (_, i) => ({ id: i, [needle]: `k${i}` })),
    'counterpart',
  )

  // The route is served from captured schemas in Postgres — no file I/O — so
  // it answers as soon as the version is current.
  const hits = await apiOk<
    ApiPage<{ column_name: string; dataset_id: string; sheet_name: string }>
  >('GET', `/search/columns?q=${encodeURIComponent(token)}&limit=6`)
  expect(hits.items.length).toBe(1)
  expect(hits.items[0].dataset_id).toBe(ds.id)
  expect(hits.items[0].column_name).toContain(token)

  await openLens(page, ds.id, 'relationships')

  const search = page.getByTestId('counterpart-search')
  await expect(search).toContainText('characters or more')

  await page.getByTestId('counterpart-query').fill(token)
  await expect(page.getByTestId('column-hit')).toHaveCount(1)
  await expect(page.getByTestId('column-hit')).toContainText(
    `${hits.items[0].sheet_name}.${hits.items[0].column_name}`,
  )

  // A shared column name is a lead, not an edge — the panel says so, because
  // a join still binds to a confirmed relationship.
  await expect(search).toContainText('a LEAD, not evidence')
})

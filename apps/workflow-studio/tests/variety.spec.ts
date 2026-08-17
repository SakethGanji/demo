import { test, expect, goto, api, apiOk, API_BASE, ADMIN, uniqueName } from './fixtures'
import type { Locator, Page } from '@playwright/test'

/**
 * PATHOLOGICAL DATA SHAPES — the "upload any tabular data" promise, tested.
 *
 * `shape.spec.ts` proves the shape RULES reach the DOM on three benched shapes.
 * This file goes after the shapes those rules were written for and the ones
 * they were not: 125 columns, one column, one row, a column that is entirely
 * null, column names that are hostile to a header cell, values that are hostile
 * to a renderer, a column that is 80% parsed, 210 distinct categories, and
 * numbers at the ends of the double range.
 *
 * Two things are asserted everywhere, on every page visited:
 *
 *  1. **No broken literal reaches the DOM.** `NaN`, `Infinity`, `undefined`,
 *     `[object Object]` and `Invalid Date` are each a specific bug wearing a
 *     specific costume, and one sweep catches the whole class. Every fixture
 *     below is deliberately free of those strings as DATA, so a hit is always
 *     the renderer's.
 *  2. **Every number is checked against a fresh API read**, never against a
 *     literal typed into this file. The profile is fetched here and the
 *     denominators are recomputed from it, so a UI that prints a confident
 *     wrong number fails rather than agreeing with a hard-coded expectation.
 *
 * Each test visits `/data` AND at least one analysis route, because the two
 * surfaces derive their shape decisions independently (`DatasetsPage` from the
 * sheet listing, `ColumnPage` from the sheet listing merged with a profile) and
 * a shape that only breaks on one of them is still broken.
 */

/* ------------------------------------------------------------------- types */

interface SheetColumn {
  name: string
  dtype?: string | null
}
interface SheetListing {
  items: { name: string; sheet_key: string; row_count?: number | null; columns: SheetColumn[] }[]
}
interface TopValue {
  value: unknown
  count: number
  percent: number
}
interface ProfiledColumn {
  name: string
  dtype: string
  count: number
  non_null_count?: number | null
  null_count?: number | null
  unique_count?: number | null
  top_values?: TopValue[] | null
  mean?: number | null
  median?: number | null
  std?: number | null
  min?: number | null
  max?: number | null
}
interface ProfileBody {
  row_count: number
  column_count: number
  columns: ProfiledColumn[]
}
interface RowPage {
  items: Record<string, unknown>[]
  total: number
}

/* ----------------------------------------------------------------- fixtures */

const csvCell = (v: unknown): string => {
  const s = v === null || v === undefined ? '' : String(v)
  return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s
}

/**
 * A raw-CSV seed, for the fixtures `h.seed` cannot express.
 *
 * `h.seed` builds its header from `Object.keys(rows[0])`, and a JavaScript
 * object cannot hold the same key twice — so the duplicate-name shape is
 * unreachable through it. Everything else here goes through `h.seed`; this
 * exists only for the header line. The name still carries `TEST_PREFIX`, so the
 * run's sweep cleans up after it exactly as it does for `h.seed`.
 */
async function seedCsv(
  header: readonly string[],
  rows: readonly (readonly unknown[])[],
  prefix: string,
): Promise<{ id: string; name: string }> {
  const csv = [
    header.map(csvCell).join(','),
    ...rows.map((r) => r.map(csvCell).join(',')),
  ].join('\n')
  const name = uniqueName(prefix)
  const fd = new FormData()
  fd.append('file', new Blob([csv], { type: 'text/csv' }), `${name}.csv`)
  const res = await fetch(`${API_BASE}/upload?sync=true`, {
    method: 'POST',
    headers: { 'X-User-Id': ADMIN },
    body: fd,
  })
  if (!res.ok) throw new Error(`seedCsv failed: ${res.status} ${(await res.text()).slice(0, 300)}`)
  const body = (await res.json()) as { dataset_id: string }
  return { id: body.dataset_id, name: `${name}.csv` }
}

/** The sheet as the service declares it — the source of truth for the header. */
const sheetOf = async (id: string) =>
  (await apiOk<SheetListing>('GET', `/datasets/${id}/versions/1/sheets`)).items[0]

/** A fresh profile. Every denominator asserted below is recomputed from this. */
const profileOf = async (id: string) =>
  apiOk<ProfileBody>('POST', '/profile', {
    body: {
      dataset_id: id,
      version_number: 1,
      sheet: 'data',
      include_histograms: true,
      include_duplicates: true,
      top_n: 5,
    },
  })

/** The rows, read back through the same endpoint the grid uses. */
const rowsOf = async (id: string, limit = 200) =>
  apiOk<RowPage>('POST', `/datasets/${id}/versions/1/sheets/data/query`, { body: { limit } })

const columnIn = (p: ProfileBody, name: string): ProfiledColumn => {
  const c = p.columns.find((x) => x.name === name)
  if (!c) throw new Error(`profile has no column ${name}: ${p.columns.map((x) => x.name)}`)
  return c
}

/* ------------------------------------------------------------------ sweeps */

/**
 * The literals that are never data and always a bug.
 *
 * `undefined` is included even though it is a real English-adjacent word: no
 * surface in this studio has a reason to print it, and every fixture here
 * avoids it, so a hit is a `${undefined}` that escaped.
 */
const BROKEN_LITERALS = ['NaN', 'Infinity', '[object Object]', 'Invalid Date', 'undefined'] as const

async function assertNothingBroken(page: Page, where: string) {
  const text = await page.locator('body').innerText()
  for (const bad of BROKEN_LITERALS) {
    const at = text.indexOf(bad)
    const context = at < 0 ? '' : text.slice(Math.max(0, at - 90), at + 90).replace(/\n/g, ' ⏎ ')
    expect(at, `${where} rendered the literal "${bad}" — …${context}…`).toBe(-1)
  }
}

/* ---------------------------------------------------------------- selectors */

/** The grid's data cells for one column, addressed through its header index. */
function gridColumn(page: Page, column: string, headerNames: readonly string[]): Locator {
  const idx = headerNames.indexOf(column)
  expect(idx, `column ${column} is not in the declared header`).toBeGreaterThanOrEqual(0)
  // +2: nth-child is 1-based and the ordinal gutter owns the first cell.
  return page.locator(`tbody tr td:nth-child(${idx + 2})`)
}

const gridHeaders = (page: Page) =>
  page.locator('thead th[data-column]').evaluateAll((els) =>
    els.map((e) => e.getAttribute('data-column') ?? ''),
  )

/** Every `<Stat>` in a block, with its name, rendered value and coverage note. */
async function statRows(scope: Locator) {
  return scope.locator('[data-slot="stat"]').evaluateAll((els) =>
    els.map((e) => ({
      name: e.querySelector('[data-slot="identifier"]')?.textContent?.trim() ?? '',
      value: e.querySelector('span.text-body')?.textContent?.trim() ?? '',
      note: e.querySelector('[data-slot="footnote"]')?.textContent?.trim() ?? '',
      partial: e.hasAttribute('data-partial'),
    })),
  )
}

const statNamed = (rows: Awaited<ReturnType<typeof statRows>>, name: string) => {
  const row = rows.find((r) => r.name === name)
  if (!row) throw new Error(`no stat named "${name}" — saw ${rows.map((r) => r.name).join(', ')}`)
  return row
}

/** The inline series colour of each bar in a `Distribution`. */
const barColours = (scope: Locator) =>
  scope.locator('[data-slot="magnitude-bar"] > div').evaluateAll((els) =>
    els.map((e) => (e as HTMLElement).style.background),
  )

/** Open `/column` and run the profile the page deliberately does not run itself. */
async function openColumn(page: Page, datasetId: string, column: string) {
  await goto(page, `/column?dataset=${datasetId}&column=${encodeURIComponent(column)}`)
  await expect(page.getByTestId('column-page')).toBeVisible()
  await page.getByTestId('run-profile').click()
  await expect(page.getByTestId('headline')).toBeVisible({ timeout: 30_000 })
}

/* ==========================================================================
 * 1 — VERY WIDE
 * ======================================================================== */

/** 125 columns: an identity, three prefixed families of 41, and a book code. */
const wideRows = (n: number) =>
  Array.from({ length: n }, (_, r) => {
    const row: Record<string, unknown> = { instrument_id: `RT-${40118822 + r}` }
    for (let c = 1; c <= 41; c++) row[`dv01_${c}m`] = (r + c) * 1.5
    for (let c = 1; c <= 41; c++) row[`cs01_${c}m`] = (r + c) * 2.5
    for (let c = 1; c <= 41; c++) row[`vega_${c}m`] = (r + c) * 0.5
    row.book = 'ALPHA'
    return row
  })

test('125 columns scroll rather than crush, and the rail becomes a manager', async ({
  page,
  h,
}) => {
  const ds = await h.seed(wideRows(8), 'wide125')
  const sheet = await sheetOf(ds.id)
  const names = sheet.columns.map((c) => c.name)
  expect(names.length, 'fixture must be past 120 columns').toBeGreaterThan(120)

  await goto(page, `/data?dataset=${ds.id}`)

  // The header the grid draws IS the sheet the service declares — same names,
  // same order, none lost off the end.
  expect(await gridHeaders(page)).toEqual(names)

  // R1 — the table sizes to content and the CONTAINER scrolls. `w-full` with
  // 125 columns does not scroll, it crushes: every cell collapses to ~9px and
  // the content overflows instead of truncating.
  const box = await page
    .locator('[data-slot="table-container"]')
    .first()
    .evaluate((e) => ({ scroll: e.scrollWidth, client: e.clientWidth }))
  expect(
    box.scroll,
    `the grid did not scroll: scrollWidth ${box.scroll} ≤ clientWidth ${box.client}`,
  ).toBeGreaterThan(box.client)

  // R1 — the rail defaults to the column manager, and the manager can filter.
  await expect(page.getByTestId('rail-mode-columns')).toHaveAttribute('aria-selected', 'true')
  await expect(page.getByTestId('grid-ruler')).toContainText(`${names.length} columns`)

  // R3 — one dtype statement instead of the same badge 123 times. The count is
  // recomputed from the declared dtypes, not asserted as a constant.
  const byType = new Map<string, number>()
  for (const c of sheet.columns) byType.set(c.dtype ?? 'unknown', (byType.get(c.dtype ?? '') ?? 0) + 1)
  const [domType, domCount] = [...byType.entries()].sort((a, b) => b[1] - a[1])[0]
  expect(domCount / names.length, 'fixture must have a dominant dtype').toBeGreaterThanOrEqual(0.95)
  await expect(page.getByTestId('type-strip')).toContainText(
    `${domCount} of ${names.length} columns are ${domType}`,
  )

  // Filtering the manager narrows to exactly the family, and the count comes
  // from the declared names rather than from a number typed here.
  const vega = names.filter((n) => n.startsWith('vega_')).length
  await page.getByTestId('column-filter').fill('vega_')
  await expect(page.getByTestId('column-manager-row')).toHaveCount(vega)

  await assertNothingBroken(page, '/data on a 125-column sheet')

  // The analysis route has to survive the same width.
  await goto(page, `/column?dataset=${ds.id}`)
  await expect(page.getByTestId('metric-columns').locator('[data-slot="figure"]')).toHaveText(
    String(names.length),
  )
  await expect(page.getByTestId('column-picker').locator('option')).toHaveCount(names.length)
  await assertNothingBroken(page, '/column on a 125-column sheet')
})

/* ==========================================================================
 * 2 — SINGLE COLUMN
 * ======================================================================== */

test('a single-column sheet divides by nothing it does not have', async ({ page, h }) => {
  // 40 distinct over 120 rows: below the identity threshold, so this exercises
  // the numeric distribution path rather than the identity panel.
  const ds = await h.seed(
    Array.from({ length: 120 }, (_, i) => ({ reading: i % 40 })),
    'onecol',
  )
  const sheet = await sheetOf(ds.id)
  expect(sheet.columns).toHaveLength(1)

  await goto(page, `/data?dataset=${ds.id}`)

  // R8 — one column is a list. The rail must NOT flip to a column manager.
  await expect(page.getByTestId('rail-mode-columns')).toHaveCount(0)
  await expect(page.getByTestId('row-count')).toHaveText(`${(await rowsOf(ds.id, 1)).total} rows`)
  await assertNothingBroken(page, '/data on a single-column sheet')

  await openColumn(page, ds.id, 'reading')

  // With one numeric column there is no second series, so Pearson r does not
  // exist. It must be WITHDRAWN with a reason, never rendered as an empty
  // matrix or as `r = NaN` against itself.
  await expect(page.getByTestId('correlations-card')).toContainText('Withdrawn')
  await expect(page.getByTestId('correlations-card')).toContainText(
    'needs at least two numeric columns',
  )
  await expect(page.getByTestId('correlation-row')).toHaveCount(0)

  const prof = await profileOf(ds.id)
  const col = columnIn(prof, 'reading')
  const stats = await statRows(page.getByTestId('stats-counts'))
  expect(statNamed(stats, 'rows').value).toBe(col.count.toLocaleString())
  expect(statNamed(stats, 'distinct').value).toBe((col.unique_count ?? 0).toLocaleString())

  await assertNothingBroken(page, '/column on a single-column sheet')
})

/* ==========================================================================
 * 3 — EFFECTIVELY EMPTY
 * ======================================================================== */

test('one row and an all-null column suppress ratios instead of printing 0%', async ({
  page,
  h,
}) => {
  // Both degenerate populations at once: a sheet of one row, and inside it a
  // column whose non-null count is zero — the denominator that produces `NaN%`
  // if anyone divides without checking.
  const ds = await h.seed([{ id: 1, label: 'only', all_null: null }], 'empty1')

  const prof = await profileOf(ds.id)
  const nulls = columnIn(prof, 'all_null')
  expect(nulls.non_null_count ?? 0, 'fixture must have a zero-filled column').toBe(0)

  await goto(page, `/data?dataset=${ds.id}`)
  const headers = await gridHeaders(page)
  // A null is an em dash, not the string "null" and not an empty cell that
  // reads as a value of zero length.
  await expect(gridColumn(page, 'all_null', headers)).toHaveText('—')
  await assertNothingBroken(page, '/data on a one-row sheet')

  await openColumn(page, ds.id, 'all_null')

  const counts = await statRows(page.getByTestId('stats-counts'))
  expect(statNamed(counts, 'filled').value).toBe('0')
  expect(statNamed(counts, 'null').value).toBe((nulls.null_count ?? 0).toLocaleString())

  // The whole point: `unique %` divides distinct by FILLED, and filled is zero.
  // It must be suppressed to an em dash — `0%` would be a claim (there are no
  // repeats), `NaN%` would be the raw division reaching the screen.
  const uniquePct = statNamed(counts, 'unique %').value
  expect(uniquePct, `unique % over a zero denominator rendered "${uniquePct}"`).toBe('—')

  // Every value statistic is over the filled rows, of which there are none.
  const values = await statRows(page.getByTestId('stats-values'))
  for (const name of ['min', 'max', 'mean', 'p50', 'std']) {
    expect(statNamed(values, name).value, `${name} over an empty population`).toBe('—')
  }

  // And no distribution is drawn from nothing.
  await expect(page.getByTestId('top-values')).toHaveCount(0)
  await expect(page.getByTestId('identity-panel')).toHaveCount(0)

  await assertNothingBroken(page, '/column on an all-null column')
})

/* ==========================================================================
 * 4 — HOSTILE NAMES
 * ======================================================================== */

const LONG_NAME =
  'revenue_recognised_in_the_reporting_currency_before_fx_and_intercompany_adjustments_2024'
const UNICODE_NAME = 'héllo_wörld_ünïcode_名前_Ω'
const QUOTED_NAME = 'say "hi"'
const DIGIT_NAME = '2024_revenue'

test('hostile column names stay addressable and distinguishable', async ({ page, h }) => {
  expect(LONG_NAME.length, 'the long name must be past 60 characters').toBeGreaterThan(60)

  // Two columns genuinely called `Amount` in the file. The header line is the
  // only place this can be expressed, which is why this fixture is raw CSV.
  const ds = await seedCsv(
    ['id', 'Amount', 'Amount', LONG_NAME, UNICODE_NAME, DIGIT_NAME, QUOTED_NAME],
    Array.from({ length: 6 }, (_, i) => [i + 1, i * 10, i * 100, `L${i}`, `U${i}`, i, `Q${i}`]),
    'names',
  )

  const sheet = await sheetOf(ds.id)
  const names = sheet.columns.map((c) => c.name)
  expect(names).toHaveLength(7)

  await goto(page, `/data?dataset=${ds.id}`)

  // The grid header is exactly the declared header — nothing dropped, nothing
  // reordered, nothing collapsed by the duplicate.
  expect(await gridHeaders(page)).toEqual(names)

  // The duplicate must be DISTINGUISHABLE on screen. Whether the service
  // de-duplicates on ingest or the UI addresses by ordinal, the requirement is
  // the same: two columns that arrived as `Amount` must not render as one
  // string, or "which column did I filter on?" is a coin flip.
  const amountish = names.filter((n) => n.startsWith('Amount'))
  expect(amountish, 'both Amount columns must survive').toHaveLength(2)
  expect(new Set(names).size, `header has indistinguishable duplicates: ${names}`).toBe(names.length)

  // The long name keeps its full text reachable even though the cell truncates.
  await expect(page.locator(`thead th[data-column="${LONG_NAME}"]`)).toHaveAttribute(
    'title',
    LONG_NAME,
  )
  // …and no header renders blank, which is what an unescaped name looks like.
  const headerText = await page
    .locator('thead th[data-column]')
    .evaluateAll((els) => els.map((e) => e.textContent?.trim() ?? ''))
  expect(headerText.filter((t) => t.length === 0)).toEqual([])

  await assertNothingBroken(page, '/data with hostile column names')

  // Each hostile name has to survive a deep link, which is where a quote, a
  // leading digit or a non-ASCII character gets mangled.
  for (const name of [UNICODE_NAME, QUOTED_NAME, DIGIT_NAME, LONG_NAME, amountish[1]]) {
    await goto(page, `/column?dataset=${ds.id}&column=${encodeURIComponent(name)}`)
    await expect(page.getByTestId('column-name')).toHaveText(name)
    await assertNothingBroken(page, `/column deep-linked to "${name}"`)
  }

  // The picker addresses columns by ordinal, so all seven are reachable even
  // when two of them started life with the same name.
  const options = await page
    .getByTestId('column-picker')
    .locator('option')
    .evaluateAll((els) => els.map((e) => (e as HTMLOptionElement).value))
  expect(options).toEqual(names.map((_, i) => String(i + 1)))

  // Nothing in that traversal threw. Stated here as well as in the harness
  // teardown, because "a hostile name crashed the page" is this test's subject
  // rather than an incidental failure.
  expect(h.errors, 'a hostile column name produced a console or page error').toEqual([])
})

/* ==========================================================================
 * 5 — HOSTILE VALUES  (XSS)
 * ======================================================================== */

const SCRIPT_VALUE = '<script>alert(1)</script>'
const IMG_VALUE = '<img src=x onerror="window.__xssFired = 1">'
const PADDED_VALUE = '  padded  '
const UNICODE_VALUE = 'emoji 🚀 ünïcode 中文 عربي'
const PUNCT_VALUE = 'has, a comma and "quotes" inside'
const HUGE_VALUE = `HEAD${'o'.repeat(2000)}TAIL`

test('an HTML-looking value is rendered as text, never interpreted', async ({ page, h }) => {
  const notes = [SCRIPT_VALUE, IMG_VALUE, PADDED_VALUE, UNICODE_VALUE, PUNCT_VALUE, HUGE_VALUE]
  const ds = await h.seed(
    notes.map((note, i) => ({ id: i + 1, note, kind: 'row' })),
    'hostileval',
  )

  // Verify the write by re-reading: whatever the grid shows has to match what
  // the service actually stored, byte for byte, including the padding.
  const stored = (await rowsOf(ds.id)).items.map((r) => String(r.note))
  expect(stored).toEqual(notes)

  // If anything executes, record it. `alert` is trapped as well as the img
  // handler, so both classic payloads are covered.
  await page.addInitScript(() => {
    ;(window as unknown as Record<string, unknown>).__xssFired = 0
    window.alert = () => {
      ;(window as unknown as Record<string, unknown>).__xssFired = 1
    }
  })

  await goto(page, `/data?dataset=${ds.id}`)

  // Nothing from the data became an ELEMENT. This is the assertion that
  // matters: React escapes by default, and this is what proves nobody reached
  // for `dangerouslySetInnerHTML` on the way to rendering a cell.
  const grid = page.locator('[data-slot="table-container"]').first()
  await expect(grid.locator('script')).toHaveCount(0)
  await expect(grid.locator('img')).toHaveCount(0)
  expect(await page.evaluate(() => (window as unknown as Record<string, unknown>).__xssFired)).toBe(
    0,
  )

  // Every stored value is present verbatim as the cell's title, which is how
  // truncation stays lossless.
  for (const value of stored) {
    await expect(
      page.locator(`tbody td[title="${value.replace(/"/g, '\\"')}"]`).first(),
      `no cell carries the exact stored value ${JSON.stringify(value.slice(0, 40))}`,
    ).toHaveCount(1)
  }

  // The script payload specifically: escaped in the markup, intact in the text.
  const scriptCell = page.locator('tbody td').filter({ hasText: 'alert(1)' }).first()
  const html = await scriptCell.innerHTML()
  expect(html).toContain('&lt;script&gt;')
  expect(html, 'a <script> tag reached the markup').not.toContain('<script')
  expect((await scriptCell.textContent())?.trim()).toBe(SCRIPT_VALUE)

  // A 2,008-character value truncates on screen and stays whole in the title.
  const hugeCell = page.locator(`tbody td[title="${HUGE_VALUE}"]`).first()
  const shown = (await hugeCell.textContent())?.trim() ?? ''
  expect(shown.length, `a ${HUGE_VALUE.length}-char value rendered ${shown.length} chars`).toBeLessThan(80)
  expect(shown).toContain('…')

  await assertNothingBroken(page, '/data with hostile values')

  // The analysis route renders the same values through a different component
  // path (sampled identifiers), so it needs the same proof.
  await openColumn(page, ds.id, 'note')
  const samples = page.getByTestId('sample-values')
  await expect(samples).toBeVisible()
  await expect(samples.locator('script')).toHaveCount(0)
  await expect(samples.locator('img')).toHaveCount(0)
  await expect(samples.locator(`[title="${SCRIPT_VALUE}"]`)).toHaveCount(1)
  expect(await page.evaluate(() => (window as unknown as Record<string, unknown>).__xssFired)).toBe(
    0,
  )

  await assertNothingBroken(page, '/column with hostile values')
})

/**
 * Middle truncation slices by UTF-16 code unit, and an emoji is two of them.
 *
 * `middleTruncate` cuts at `ceil((max-1)/2)` from the front and the same from
 * the back with no regard for surrogate pairs, so a value long enough to
 * truncate and dense enough in astral characters is cut through the middle of
 * one — leaving a lone surrogate, which every browser renders as U+FFFD.
 *
 * The full value survives in the `title`, so nothing is lost. What is wrong is
 * on screen: a cell that shows `A🚀🚀🚀…�` has invented a character the data
 * never contained, on a surface whose entire claim is that it shows you what is
 * actually there.
 */
test('an emoji is not cut in half by middle truncation', async ({ page, h }) => {
  // 'A' offsets the string by one character so the cut points fall inside a
  // surrogate pair rather than between two.
  //
  // 60 emoji, because `middleTruncate`'s `max` counts CHARACTERS, not UTF-16
  // code units — which is the whole point of the fix. At 40 emoji the value is
  // 81 code units but only 41 characters, so it now correctly fits the grid's
  // 44-character budget and never truncates. To exercise truncation the value
  // has to be long in characters.
  const value = `A${'🚀'.repeat(60)}`
  const ds = await h.seed([{ id: 1, payload: value }], 'surrogate')
  expect((await rowsOf(ds.id)).items[0].payload).toBe(value)

  await goto(page, `/data?dataset=${ds.id}`)
  const cell = page.locator(`tbody td[title="${value}"]`).first()
  await expect(cell).toHaveCount(1)

  const shown = (await cell.textContent()) ?? ''
  expect(shown, 'the value was long enough that truncation should have fired').toContain('…')
  expect(shown, `truncation produced a replacement character: ${shown}`).not.toContain('�')
  expect(
    /[\uD800-\uDBFF](?![\uDC00-\uDFFF])|(?<![\uD800-\uDBFF])[\uDC00-\uDFFF]/.test(shown),
    `truncation left a lone surrogate: ${JSON.stringify(shown)}`,
  ).toBe(false)
})

/* ==========================================================================
 * 6 — MIXED TYPES  (the flagship)
 * ======================================================================== */

/**
 * 100 rows, three columns:
 *   `id`            — complete, numeric.
 *   `amount_mixed`  — 80 integers, 15 unparseable, 5 empty. The service types
 *                     the whole column as text, so NO numeric statistic may be
 *                     offered over it.
 *   `score_sparse`  — 80 integers, 20 empty. Typed numeric, so `mean` IS
 *                     computed — over 80 of the 100 rows. That figure must
 *                     carry its denominator or it is a confident wrong number.
 */
const mixedRows = () =>
  Array.from({ length: 100 }, (_, i) => ({
    id: i,
    amount_mixed: i % 5 !== 0 ? i * 3 : i % 20 === 0 ? null : 'n/a',
    score_sparse: i % 5 === 0 ? null : i % 37,
  }))

test('a statistic over a partly-filled column always states its population', async ({ page, h }) => {
  const ds = await h.seed(mixedRows(), 'mixed')
  const prof = await profileOf(ds.id)

  const sparse = columnIn(prof, 'score_sparse')
  const mixed = columnIn(prof, 'amount_mixed')

  // The fixture is only interesting if it actually landed partial.
  expect(sparse.dtype).toBe('numeric')
  expect(sparse.non_null_count ?? 0).toBeLessThan(sparse.count)
  expect(sparse.mean, 'the service must have computed a mean to qualify').not.toBeNull()
  expect(mixed.dtype, 'a column with unparseable text must not be typed numeric').not.toBe('numeric')

  const filled = sparse.non_null_count ?? 0
  const pct = ((filled / sparse.count) * 100).toFixed(1).replace(/\.0$/, '')

  await openColumn(page, ds.id, 'score_sparse')

  // R11 on the badge: `int64 · 80%`. An unqualified type badge is a promise
  // that every row is of that type.
  await expect(page.getByTestId('column-type')).toContainText(
    `· ${Math.round((filled / sparse.count) * 100)}%`,
  )

  // R11 on the figures. The block states the shared population once…
  await expect(page.getByTestId('stats-values')).toContainText(
    `over ${pct}% · ${filled.toLocaleString()} rows`,
  )

  // …and each value statistic is individually marked partial, so a figure
  // read on its own still carries the denominator it was computed over.
  const values = await statRows(page.getByTestId('stats-values'))
  for (const name of ['min', 'max', 'mean', 'p50', 'std']) {
    const row = statNamed(values, name)
    expect(row.partial, `${name} is computed over ${filled}/${sparse.count} rows but is unqualified`).toBe(
      true,
    )
    expect(row.note, `${name} states no population`).toContain(`${filled.toLocaleString()} rows`)
  }
  // The mean actually shown is the mean the service computed — no re-derivation
  // over a different denominator on the way to the screen.
  expect(statNamed(values, 'mean').value).toBe(
    Number((sparse.mean as number).toFixed(3)).toLocaleString(),
  )

  // Counts describe every row, including the nulls, so THEY are complete.
  const counts = await statRows(page.getByTestId('stats-counts'))
  expect(statNamed(counts, 'rows').value).toBe(sparse.count.toLocaleString())
  expect(statNamed(counts, 'null').value).toBe((sparse.null_count ?? 0).toLocaleString())

  await assertNothingBroken(page, '/column on a partly-filled numeric column')

  // The text column: 15% of it is garbage, so no numeric claim may appear at
  // all. This is the silent-wrong-answer case — a `sum` here would drop a fifth
  // of the rows and print a confident total.
  await openColumn(page, ds.id, 'amount_mixed')
  const textValues = await statRows(page.getByTestId('stats-values'))
  for (const name of ['min', 'max', 'mean', 'p50', 'std']) {
    expect(
      statNamed(textValues, name).value,
      `${name} was printed for a column the service types as ${mixed.dtype}`,
    ).toBe('—')
  }
  const mixedFilled = mixed.non_null_count ?? 0
  await expect(page.getByTestId('column-type')).toContainText(
    `· ${Math.round((mixedFilled / mixed.count) * 100)}%`,
  )
  await assertNothingBroken(page, '/column on a mixed-type column')

  // …and wherever else the statistic surfaces. The analytics lens shows the
  // same column beside the grid, and its figures need the same denominator.
  await goto(page, `/data?dataset=${ds.id}`)
  await page.getByTestId('lens-analytics').click()
  const card = page.getByTestId('profile-column').filter({ hasText: 'score_sparse' })
  await expect(card).toBeVisible({ timeout: 30_000 })
  await expect(card, 'the lens prints min/med/max without saying how many rows fed them').toContainText(
    `${filled.toLocaleString()} filled`,
  )
  await assertNothingBroken(page, 'the analytics lens on a partly-filled column')
})

/* ==========================================================================
 * 7 — HIGH CARDINALITY
 * ======================================================================== */

/** 300 rows: 210 distinct SKUs, a 20-way bucket, and a 10-way quantity. */
const highCardRows = () =>
  Array.from({ length: 300 }, (_, i) => ({
    id: i,
    sku: `SKU-${String(i % 210).padStart(4, '0')}`,
    bucket20: `B${String(i % 20).padStart(2, '0')}`,
    qty: i % 10,
  }))

test('210 distinct values fold to at most eight plus an Other that carries its share', async ({
  page,
  h,
}) => {
  const ds = await h.seed(highCardRows(), 'highcard')
  const prof = await profileOf(ds.id)
  const sku = columnIn(prof, 'sku')

  expect(sku.unique_count ?? 0, 'fixture must be past 200 distinct').toBeGreaterThan(200)
  // Below the identity threshold, so this is a distribution and not the
  // identity panel — the fold is what is under test.
  expect((sku.unique_count ?? 0) / (sku.non_null_count || 1)).toBeLessThan(0.95)

  await goto(page, `/data?dataset=${ds.id}`)
  await assertNothingBroken(page, '/data on a 210-distinct column')

  await openColumn(page, ds.id, 'sku')

  // Scope to the top-values chart: the signature card below it draws a length
  // distribution with the same row testid.
  const chart = page.getByTestId('top-values')
  await expect(chart).toBeVisible()

  const listed = (sku.top_values ?? []).filter((t) => t.value !== null && t.value !== undefined)
  const filled = sku.non_null_count ?? 0
  const residualRows = filled - listed.reduce((s, t) => s + t.count, 0)
  expect(residualRows, 'fixture must leave a residual to fold').toBeGreaterThan(0)

  const drawn = chart.getByTestId('distribution-row')
  await expect(drawn).toHaveCount(listed.length)
  expect(listed.length, 'never more head bars than palette slots').toBeLessThanOrEqual(8)

  // The tail is FOLDED, with its own count AND share — not dropped off the
  // bottom, and not left as an unstated remainder.
  const other = chart.getByTestId('distribution-other')
  await expect(other).toHaveCount(1)
  const otherText = (await other.textContent()) ?? ''
  expect(otherText, 'the folded row does not state how many values it covers').toContain(
    `${(sku.unique_count ?? 0) - listed.length} other values`,
  )
  expect(otherText, 'the folded row does not carry its own count').toContain(
    residualRows.toLocaleString(),
  )
  const share = ((residualRows / filled) * 100).toFixed(1)
  expect(otherText, `the folded row does not carry its share (${share}%)`).toContain(`${share}%`)

  // NO NINTH COLOUR. Head bars take distinct palette slots inside 1…8; the
  // residual takes the beyond-the-palette graphite token, because cycling back
  // to slot 1 would put two identities in one hue.
  const colours = await barColours(chart)
  expect(colours).toHaveLength(listed.length + 1)
  const head = colours.slice(0, listed.length)
  for (const c of head) expect(c, `series colour outside the palette: ${c}`).toMatch(/^var\(--viz-[1-8]\)$/)
  expect(new Set(head).size, `two series share a colour: ${head.join(', ')}`).toBe(head.length)
  expect(colours[colours.length - 1], 'the folded row was given a palette hue').not.toMatch(/--viz-/)

  await assertNothingBroken(page, '/column on a 210-distinct column')
})

test('the aggregate fold keeps eight ranked groups and states the rest', async ({ page, h }) => {
  const ds = await h.seed(highCardRows(), 'aggfold')
  const prof = await profileOf(ds.id)
  const bucket = columnIn(prof, 'bucket20')
  expect(bucket.unique_count ?? 0, 'more groups than the page limit').toBeGreaterThan(12)

  await goto(page, `/aggregate?dataset=${ds.id}`)

  // The builder seeds itself from the first qualifying dimension and runs.
  const rows = page.getByTestId('result-row')
  await expect(rows.first()).toBeVisible({ timeout: 30_000 })
  const groups = await rows.count()
  expect(groups, 'the page must return more groups than the palette has slots').toBeGreaterThan(8)

  // R7 — eight bars, then one fold. The palette is never cycled.
  await expect(page.getByTestId('fold-bar')).toHaveCount(8)
  const other = page.getByTestId('fold-other')
  await expect(other).toHaveCount(1)
  await expect(other).toContainText(`${groups - 8} groups folded`)
  await expect(other).toContainText('folded, never dropped')

  const foldColours = await page
    .getByTestId('ranked-fold')
    .locator('[data-slot="magnitude-bar"] > div')
    .evaluateAll((els) => els.map((e) => (e as HTMLElement).style.background))
  expect(foldColours).toHaveLength(9)
  expect(foldColours[8], 'the folded group was given a palette hue').not.toMatch(/--viz-/)

  await assertNothingBroken(page, '/aggregate on a 20-group column')
})

/* ==========================================================================
 * 8 — NUMERIC EXTREMES
 * ======================================================================== */

const EXTREMES = [
  999_999_999_999_999, -999_999_999_999_999, 0, 123_456_789_012, -1, 4_503_599_627_370_496,
]

/** Large, negative, zero and long-tailed floats, well inside the double range. */
const extremeRows = () =>
  Array.from({ length: 48 }, (_, i) => ({
    id: i,
    big: EXTREMES[i % EXTREMES.length],
    neg: -(i + 1) * 0.5,
    zero: 0,
    floaty: [3.14159265358979, -2.718281828459045, 0.000000123456, 1.5, -0.000000000001][i % 5],
  }))

test('numeric extremes render as numbers, and negatives take no status colour', async ({
  page,
  h,
}) => {
  const ds = await h.seed(extremeRows(), 'extremes')
  const prof = await profileOf(ds.id)

  await goto(page, `/data?dataset=${ds.id}`)
  const headers = await gridHeaders(page)

  // A number is a number. `neg` is entirely negative and `big` mixes signs, so
  // if the grid painted sign as a status the two would differ.
  const negColour = await gridColumn(page, 'neg', headers).evaluateAll((els) =>
    els.map((e) => getComputedStyle(e).color),
  )
  const idColour = await gridColumn(page, 'id', headers).evaluateAll((els) =>
    els.map((e) => getComputedStyle(e).color),
  )
  expect(new Set(negColour).size, 'a negative column rendered in more than one ink').toBe(1)
  expect(negColour[0], 'negatives are inked differently from other numbers').toBe(idColour[0])

  // …and it is none of the reserved status hues, which are what "this is bad"
  // means everywhere else in the studio.
  const statusInks = await page.evaluate(() => {
    const s = getComputedStyle(document.documentElement)
    return ['--st-good', '--st-warn', '--st-crit', '--st-serious'].map((t) =>
      s.getPropertyValue(t).trim(),
    )
  })
  for (const ink of statusInks.filter(Boolean)) {
    expect(negColour[0].replace(/\s/g, ''), `negatives painted with ${ink}`).not.toBe(
      ink.replace(/\s/g, ''),
    )
  }

  // No status SHAPE either — the categorical dot is for closed vocabularies.
  await expect(gridColumn(page, 'neg', headers).locator('span[aria-hidden="true"]')).toHaveCount(0)

  await assertNothingBroken(page, '/data with numeric extremes')

  for (const column of ['big', 'neg', 'zero', 'floaty']) {
    await openColumn(page, ds.id, column)
    const col = columnIn(prof, column)
    const values = await statRows(page.getByTestId('stats-values'))
    // Every figure is the service's, formatted — never a client re-derivation
    // that could overflow on the way.
    for (const [name, raw] of [
      ['min', col.min],
      ['max', col.max],
      ['mean', col.mean],
    ] as const) {
      const shown = statNamed(values, name).value
      if (raw == null) {
        expect(shown).toBe('—')
      } else {
        expect(Number.isFinite(raw), `${column}.${name} arrived non-finite`).toBe(true)
        expect(shown).toBe(
          Number.isInteger(raw) ? raw.toLocaleString() : Number(raw.toFixed(3)).toLocaleString(),
        )
      }
    }
    await assertNothingBroken(page, `/column on ${column}`)
  }
})

/* ==========================================================================
 * 9 — THE DOUBLE CEILING  (a service defect, expressed rather than fixed)
 * ======================================================================== */

/**
 * `1e308` is a legal double and a legal CSV cell, and the service ingests it
 * happily — `/query` returns it and the grid draws it. `POST /profile` on the
 * same column answers 500.
 *
 * The correct behaviour is that profiling a column of legal doubles returns a
 * profile, so that is what this asserts. It is marked `test.fail()` because it
 * does not, today. See the report for the repro.
 */
// Regression guard. `POST /profile` used to 500 on a column containing 1e308:
// STDDEV squares its input, so one legitimate value near the double ceiling
// overflowed the accumulator to ±inf, which is not JSON-encodable. Because the
// profile is per-SHEET, that took out the column page, the analytics lens and
// the aggregate builder's capability gates for every column on the sheet.
// The service now returns the profile with `std: null` and names the statistic
// in `unavailable_stats`, so a consumer can say "not representable" rather than
// "no data".
test('profiling a column containing 1e308 does not 500', async ({ h }) => {
  const ds = await h.seed(
    [{ v: 1 }, { v: 2 }, { v: 3 }, { v: 1e308 }],
    'ceiling',
  )
  // The value round-trips through the read path without complaint…
  const back = await rowsOf(ds.id)
  expect(back.items.map((r) => r.v)).toContain(1e308)

  // …but the profile of the same column does not exist.
  const res = await api('POST', '/profile', {
    body: {
      dataset_id: ds.id,
      version_number: 1,
      sheet: 'data',
      include_histograms: true,
      include_duplicates: true,
      top_n: 5,
    },
  })
  expect(res.status, `POST /profile answered ${res.status}: ${JSON.stringify(res.body)}`).toBeLessThan(
    500,
  )
})

test('a statistic with no finite value is named, not left as an em dash', async ({ page, h }) => {
  // This test used to assert the studio survived a 500 from `/profile`. The
  // service no longer 500s here — STDDEV's accumulator overflow is handled —
  // so what is under test now is the honest reporting of the outcome.
  //
  // `std` on this column has no finite double. Returning `null` is correct;
  // rendering a bare em dash is not, because "not computed" and "too large to
  // represent" lead a reader to opposite conclusions. The service names the
  // statistic in `unavailable_stats` and the page has to say so.
  const ds = await h.seed([{ v: 1 }, { v: 2 }, { v: 3 }, { v: 1e308 }], 'ceilingui')

  // The grid still works: the value is legal and the read path returns it.
  await goto(page, `/data?dataset=${ds.id}`)
  const headers = await gridHeaders(page)
  const cells = await gridColumn(page, 'v', headers).allTextContents()
  expect(cells.some((c) => c.includes('e+308')), `grid drew ${cells.join(' | ')}`).toBe(true)
  await assertNothingBroken(page, '/data with a 1e308 cell')

  // The service's own answer first, so a UI assertion cannot pass against a
  // premise that has quietly changed.
  const profile = await apiOk<{
    columns: { name: string; std: number | null; mean: number | null; unavailable_stats?: string[] }[]
  }>('POST', '/profile', {
    body: { dataset_id: ds.id, version_number: 1, sheet: 'data', include_histograms: true },
  })
  const col = profile.columns.find((c) => c.name === 'v')!
  expect(col.unavailable_stats, 'the service did not flag std as unrepresentable').toContain('std')
  expect(col.std, 'an unrepresentable statistic must be null, never Infinity').toBeNull()
  expect(col.mean, 'the OTHER statistics must still be computed').not.toBeNull()

  await goto(page, `/column?dataset=${ds.id}&column=v`)
  await expect(page.getByTestId('column-page')).toBeVisible()
  await page.getByTestId('run-profile').click()

  // The page names the statistic and says why it is absent.
  const note = page.getByTestId('unavailable-stats')
  await expect(note).toBeVisible({ timeout: 20_000 })
  await expect(note).toContainText('std')
  await expect(note).toContainText(/overflow|no finite/i)

  // And the statistics that ARE representable are still shown — one bad
  // aggregate must not blank the column.
  await expect(page.getByTestId('stats-values')).toContainText('mean')

  await assertNothingBroken(page, '/column with an unrepresentable statistic')
})

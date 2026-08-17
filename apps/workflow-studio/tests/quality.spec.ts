import { test, expect, goto, api, apiOk } from './fixtures'
import type { Harness } from './fixtures'
import type { Locator, Page } from '@playwright/test'

/**
 * The quality lens.
 *
 * Rules, validation runs, the promotion gate, the health dimensions — and the
 * two §16 explorers (duplicates, missing data), which had no browser coverage
 * at all before this file.
 *
 * The explorers are the reason it is written this way. Every figure they print
 * is a count over a population, and the populations differ from row to row:
 * `group_count` is over the groups the server found, `duplicate_rows` over the
 * rows of the sheet, a probe row's `null_count` over COLUMNS. A screenshot
 * cannot tell a right answer from a plausible one, so every number asserted
 * here is either
 *
 *  1. derived from how the fixture was CONSTRUCTED — "exactly three e-mails
 *     occur twice, and exactly one of those pairs is identical in every
 *     column" is a property of the CSV, not of the service, or
 *  2. read back from the API in the same shape the browser asked for.
 *
 * Both wherever the chain can be closed: fixture → API → DOM.
 *
 * Assertions read the DOM structurally (`[data-slot]`) rather than by matching
 * a formatted sentence, because `innerText` spacing between flex children is
 * not something a test should be pinning.
 */

/* ------------------------------------------------------------------ helpers */

const rand = () => Math.random().toString(36).slice(2, 8)

/** Deep-link to our own fixture, then open the lens under test. */
async function openQuality(page: Page, datasetId: string) {
  await goto(page, `/data?dataset=${datasetId}`)
  await page.getByTestId('lens-quality').click()
  await expect(page.getByTestId('lens-quality')).toHaveAttribute('aria-pressed', 'true')
}

/** Rendered text, whitespace-collapsed. */
async function textOf(loc: Locator): Promise<string> {
  return (await loc.innerText()).replace(/\s+/g, ' ').trim()
}

async function textsOf(loc: Locator): Promise<string[]> {
  return (await loc.allInnerTexts()).map((t) => t.replace(/\s+/g, ' ').trim())
}

/** The first percentage in a string, as a number. Throws rather than NaN. */
function firstPercent(text: string): number {
  const m = /(-?[\d.]+)\s*%/.exec(text)
  if (!m) throw new Error(`no percentage in ${JSON.stringify(text)}`)
  return Number(m[1])
}

/**
 * One `<Stat>` taken apart: the name, the figure, and the denominator note.
 *
 * Read structurally on purpose — the note is where R11 lives, and "is the
 * denominator stated" must not degrade into "does this sentence still read the
 * same".
 */
async function readStat(root: Locator): Promise<{ name: string; value: string; note: string | null }> {
  const name = (await root.locator('[data-slot="identifier"]').first().innerText()).trim()
  const value = (
    await root.locator('span:not([data-slot="identifier"])').first().innerText()
  ).trim()
  const noteLoc = root.locator('[data-slot="footnote"]')
  const note = (await noteLoc.count())
    ? (await noteLoc.first().innerText()).replace(/\s+/g, ' ').trim()
    : null
  return { name, value, note }
}

interface DupGroup {
  key: Record<string, unknown>
  count: number
  examples?: Record<string, unknown>[] | null
}
interface DupResponse {
  columns: string[]
  exact: boolean
  row_count: number
  group_count: number
  duplicate_rows: number
  groups: DupGroup[]
  truncated: boolean
  masked_columns: string[]
}
interface MissingResponse {
  source: string
  row_count: number
  columns: { column: string; null_count: number; null_percent: number }[]
  rows_most_missing: { null_count: number; row: Record<string, unknown> }[]
  masked_columns: string[]
}
interface HealthResponse {
  dimensions: Record<string, { status: string; summary: string }>
}
interface RuleRow {
  id: string
  name: string
  rule_type: string
  scope_type: string
  severity: string
  enabled: boolean
  parameters: Record<string, unknown> | null
}
interface RunSummary {
  rules_failed: number
  error_failures: number
  warning_failures: number
}

const duplicatesUrl = (id: string, columns?: string) =>
  `/datasets/${id}/versions/1/sheets/data/duplicates?limit=25` +
  (columns ? `&columns=${encodeURIComponent(columns)}` : '')

const missingUrl = (id: string) => `/datasets/${id}/versions/1/sheets/data/missing`

/** The service's health vocabulary mapped onto the five status shapes. */
const STATUS_KIND: Record<string, string> = {
  ok: 'good',
  warn: 'warning',
  warning: 'warning',
  attention: 'serious',
  fail: 'critical',
  failed: 'critical',
}

/* ----------------------------------------------------------------- fixtures */

/**
 * 20 rows whose duplication is a property of this function, not of the service.
 *
 *  - 14 rows are unique in every column.
 *  - `dup1@x.io` occurs twice with EVERY column equal — the only exact duplicate.
 *  - `dup2@x.io` and `dup3@x.io` occur twice each, differing elsewhere.
 *
 * So grouping on `email` must find 3 groups over 6 rows, and grouping on every
 * column must find 1 group over 2 rows. The two answers differing is the point:
 * they are different questions.
 */
const DUP_SOLO = 14
const DUP_PAIRS = 3
const DUP_TOTAL_ROWS = DUP_SOLO + DUP_PAIRS * 2 // 20
const DUP_EMAIL_GROUPS = DUP_PAIRS // 3
const DUP_EMAIL_ROWS = DUP_PAIRS * 2 // 6
const DUP_LARGEST = 2 // every duplicate group is a pair
const DUP_EXACT_GROUPS = 1
const DUP_EXACT_ROWS = 2

function duplicateFixture(): Record<string, unknown>[] {
  const rows: Record<string, unknown>[] = []
  for (let i = 0; i < DUP_SOLO; i++) {
    rows.push({ email: `s${i}@x.io`, region: i % 2 ? 'EU' : 'US', amount: 100 + i })
  }
  // Identical in every column → an exact duplicate AND an e-mail duplicate.
  rows.push({ email: 'dup1@x.io', region: 'EU', amount: 1 })
  rows.push({ email: 'dup1@x.io', region: 'EU', amount: 1 })
  // Same e-mail, different everything else → an e-mail duplicate only.
  rows.push({ email: 'dup2@x.io', region: 'EU', amount: 2 })
  rows.push({ email: 'dup2@x.io', region: 'US', amount: 3 })
  rows.push({ email: 'dup3@x.io', region: 'US', amount: 4 })
  rows.push({ email: 'dup3@x.io', region: 'EU', amount: 5 })
  return rows
}

/**
 * 20 rows × 11 columns with a controlled number of empty cells per column — an
 * empty CSV cell is a null. `c1` is null in 10 rows, `c2` in 9 … `c10` in 1,
 * and `id` never. Eleven columns is deliberately more than the eight palette
 * slots, so the tail has to fold.
 */
const MISS_ROWS = 20
const MISS_COLS = 10
const HEAD_LIMIT = 8 // VIZ_SLOTS — above this the tail folds into "Other"

function missingFixture(): Record<string, unknown>[] {
  return Array.from({ length: MISS_ROWS }, (_, i) => {
    const row: Record<string, unknown> = { id: i + 1 }
    for (let j = 1; j <= MISS_COLS; j++) {
      // column `cj` is null in its first (MISS_COLS + 1 - j) rows.
      row[`c${j}`] = i < MISS_COLS + 1 - j ? null : `v${i}`
    }
    return row
  })
}

/** Nulls per column, counted from the fixture itself — the known answer. */
function nullCounts(rows: Record<string, unknown>[]): Record<string, number> {
  const out: Record<string, number> = {}
  for (const row of rows) {
    for (const [k, v] of Object.entries(row)) {
      out[k] = (out[k] ?? 0) + (v === null || v === undefined || v === '' ? 1 : 0)
    }
  }
  return out
}

/* --------------------------------------------------------------- duplicates */

test('the duplicates explorer answers a fixture whose duplication we constructed', async ({
  page,
  h,
}) => {
  const ds = await h.seed(duplicateFixture(), 'dupknown')
  await openQuality(page, ds.id)

  await page.getByTestId('duplicates-subset').selectOption('email')
  const explorer = page.getByTestId('duplicates-explorer')
  await expect(explorer).toContainText('duplicates found')

  // The same question the browser asked, asked directly. Both have to agree
  // with the CSV: a service answering 4 groups would be wrong, and so would a
  // panel rendering 4 for an answer of 3.
  const direct = await apiOk<DupResponse>('GET', duplicatesUrl(ds.id, 'email'))
  expect(direct.row_count).toBe(DUP_TOTAL_ROWS)
  expect(direct.group_count).toBe(DUP_EMAIL_GROUPS)
  expect(direct.duplicate_rows).toBe(DUP_EMAIL_ROWS)
  expect(Math.max(...direct.groups.map((g) => g.count))).toBe(DUP_LARGEST)
  expect(direct.exact).toBe(false)
  expect(direct.columns).toEqual(['email'])

  // Three stats over three DIFFERENT populations: groups, rows of the sheet,
  // and rows inside a duplicate group. Each must say which.
  const groups = await readStat(page.getByTestId('duplicates-group-count'))
  expect(groups.name).toBe('groups')
  expect(groups.value).toBe(String(direct.group_count))
  // Nothing was capped, so the panel must not imply the list is partial.
  expect(direct.truncated).toBe(false)
  expect(groups.note).toBeNull()

  const dupRows = await readStat(page.getByTestId('duplicates-row-count'))
  expect(dupRows.value).toBe(String(direct.duplicate_rows))
  expect(dupRows.note).toContain(`${direct.duplicate_rows} rows`)
  expect(firstPercent(dupRows.note!)).toBeCloseTo(
    (direct.duplicate_rows / direct.row_count) * 100,
    1,
  )

  const largest = await readStat(page.getByTestId('duplicates-largest'))
  expect(largest.value).toBe(String(DUP_LARGEST))
  // Over the DUPLICATE rows (2 of 6), never over the sheet (2 of 20).
  expect(firstPercent(largest.note!)).toBeCloseTo((DUP_LARGEST / direct.duplicate_rows) * 100, 1)

  // The share of the sheet, stated with its denominator rather than alone.
  const share = await textOf(explorer.getByText(/of the sheet/).first())
  expect(firstPercent(share)).toBeCloseTo((direct.duplicate_rows / direct.row_count) * 100, 1)
  expect(share).toContain(`${direct.duplicate_rows} of ${direct.row_count} rows`)
  expect(share).toContain('is a share of those duplicate rows, not of the sheet')

  // One row per group, keyed by the value the API returned, biggest first.
  const groupRows = page.getByTestId('duplicate-group')
  await expect(groupRows).toHaveCount(direct.groups.length)
  const rendered = await textsOf(groupRows)
  const shownCounts = await groupRows.evaluateAll((els) =>
    els.map((e) => e.querySelector('div')?.lastElementChild?.textContent?.trim() ?? ''),
  )
  for (const [i, g] of direct.groups.entries()) {
    expect(rendered[i], `group ${i} key`).toContain(String(g.key.email))
    expect(shownCounts[i], `group ${i} count`).toBe(String(g.count))
  }
  expect(direct.groups[0].count).toBe(Math.max(...direct.groups.map((g) => g.count)))

  // Example rows show the columns OUTSIDE the key — the ones that differ. Two
  // of the three columns are outside `email`.
  await groupRows.first().getByTestId('duplicate-examples-toggle').click()
  await expect(groupRows.first().getByTestId('duplicate-example')).toHaveCount(
    direct.groups[0].examples?.length ?? 0,
  )
  expect(await textOf(groupRows.first())).toContain('2 columns outside the key')
})

test('exact and subset duplicates are different questions and give different answers', async ({
  page,
  h,
}) => {
  const ds = await h.seed(duplicateFixture(), 'dupexact')
  await openQuality(page, ds.id)

  const explorer = page.getByTestId('duplicates-explorer')
  await expect(explorer).toBeVisible()

  // The default grouping is every column.
  const exact = await apiOk<DupResponse>('GET', duplicatesUrl(ds.id))
  expect(exact.exact).toBe(true)
  expect(exact.group_count).toBe(DUP_EXACT_GROUPS)
  expect(exact.duplicate_rows).toBe(DUP_EXACT_ROWS)

  await expect(explorer).toContainText('every column')
  expect((await readStat(page.getByTestId('duplicates-group-count'))).value).toBe(
    String(DUP_EXACT_GROUPS),
  )
  expect((await readStat(page.getByTestId('duplicates-row-count'))).value).toBe(
    String(DUP_EXACT_ROWS),
  )

  // Grouped on every column there is nothing outside the key, and saying so
  // beats rendering five visually identical lines.
  const firstGroup = page.getByTestId('duplicate-group').first()
  await firstGroup.getByTestId('duplicate-examples-toggle').click()
  expect(await textOf(firstGroup)).toContain('grouped on every column')
  await expect(firstGroup.getByTestId('duplicate-example')).toHaveCount(0)

  // Same rows, one column: a different question with a different answer.
  await page.getByTestId('duplicates-subset').selectOption('email')
  const subset = await apiOk<DupResponse>('GET', duplicatesUrl(ds.id, 'email'))
  expect(subset.group_count).not.toBe(exact.group_count)
  expect(subset.duplicate_rows).not.toBe(exact.duplicate_rows)

  await expect(page.getByTestId('duplicate-group')).toHaveCount(subset.groups.length)
  expect((await readStat(page.getByTestId('duplicates-group-count'))).value).toBe(
    String(subset.group_count),
  )
  expect((await readStat(page.getByTestId('duplicates-row-count'))).value).toBe(
    String(subset.duplicate_rows),
  )
  // And it names what it grouped on, so the reader knows which question this is.
  await expect(explorer).toContainText('email')
})

/* ------------------------------------------------------------- missing data */

test('the missing-data explorer ranks columns worst-first and folds the tail with its own share', async ({
  page,
  h,
}) => {
  const rows = missingFixture()
  const expectedNulls = nullCounts(rows)
  const ds = await h.seed(rows, 'missknown')
  await openQuality(page, ds.id)

  const explorer = page.getByTestId('missing-explorer')
  await expect(explorer).toBeVisible()

  // The API must agree with the CSV we wrote: an empty cell is a null.
  const direct = await apiOk<MissingResponse>('GET', missingUrl(ds.id))
  expect(direct.row_count).toBe(MISS_ROWS)
  expect(direct.columns.length).toBe(MISS_COLS + 1)
  for (const c of direct.columns) {
    expect(c.null_count, `null count for ${c.column}`).toBe(expectedNulls[c.column])
  }
  // Nothing profiles on upload, so the panel must say these numbers are live.
  expect(direct.source).toBe('computed')
  expect(await textOf(explorer)).toContain('computed live')

  const ranked = [...direct.columns].sort((a, b) => b.null_count - a.null_count)
  const head = ranked.slice(0, HEAD_LIMIT)
  const tail = ranked.slice(HEAD_LIMIT)
  const totalNulls = ranked.reduce((n, c) => n + c.null_count, 0)

  // Eight bars, worst first. Eleven columns against an eight-slot palette is
  // exactly the case the fold exists for.
  const columnRows = page.getByTestId('missing-column')
  await expect(columnRows).toHaveCount(HEAD_LIMIT)
  const names = await columnRows.evaluateAll((els) =>
    els.map((e) => e.querySelector('[data-slot="identifier"]')?.textContent?.trim() ?? ''),
  )
  expect(names).toEqual(head.map((c) => c.column))

  const renderedHead = await textsOf(columnRows)
  for (const [i, c] of head.entries()) {
    // "10 · 50%" — the count as the API reported it, the percentage a true share.
    const m = /(\d[\d,]*)\s*·\s*([\d.]+)%/.exec(renderedHead[i])
    expect(m, `no "count · percent" in ${JSON.stringify(renderedHead[i])}`).toBeTruthy()
    expect(Number(m![1].replace(/,/g, '')), `count for ${c.column}`).toBe(c.null_count)
    expect(Number(m![2]), `share for ${c.column}`).toBeCloseTo(
      (c.null_count / direct.row_count) * 100,
      1,
    )
  }

  // The tail is folded, not dropped: its own column count and its own share of
  // all missing values — which is a different denominator from the rows above.
  expect(tail.length).toBeGreaterThan(0)
  const other = await textOf(page.getByTestId('missing-column-other'))
  expect(other).toContain(`${tail.length} other column`)
  const tailValue = tail.reduce((n, c) => n + c.null_count, 0)
  const otherFigure = new RegExp(`\\b${tailValue}\\s*·\\s*([\\d.]+)%`).exec(other)
  expect(otherFigure, `no "${tailValue} · n%" in ${JSON.stringify(other)}`).toBeTruthy()
  expect(Number(otherFigure![1])).toBeCloseTo((tailValue / totalNulls) * 100, 1)
  expect(other).toContain('folded, not dropped')

  // `affected` counts COLUMNS, not rows — the unit is the whole point.
  const withNulls = direct.columns.filter((c) => c.null_count > 0).length
  const affected = await readStat(page.getByTestId('missing-affected'))
  expect(affected.name).toBe('affected')
  expect(affected.value).toBe(String(withNulls))
  expect(affected.note).toContain('columns')
  expect(affected.note).not.toContain('rows')

  const worstCol = ranked[0]
  const worst = await readStat(page.getByTestId('missing-worst'))
  expect(worst.value).toBe(String(worstCol.null_count))
  expect(firstPercent(worst.note!)).toBeCloseTo(
    (worstCol.null_count / direct.row_count) * 100,
    1,
  )
  await expect(explorer).toContainText(`worst column ${worstCol.column}`)

  // The probe is ranked by how many COLUMNS of a row are null.
  const probeRows = page.getByTestId('missing-row')
  await expect(probeRows).toHaveCount(direct.rows_most_missing.length)
  for (const [i, r] of direct.rows_most_missing.entries()) {
    const probe = await readStat(probeRows.nth(i).locator('[data-slot="stat"]'))
    expect(probe.name).toBe(`row ${i + 1}`)
    expect(probe.value).toBe(String(r.null_count))
    expect(probe.note, `probe row ${i} denominator`).toContain('columns')
  }

  expect(await textOf(explorer)).not.toContain('NaN')
})

test('a clean dataset is reported as clean, not as an empty chart', async ({ page, h }) => {
  const rows = Array.from({ length: 6 }, (_, i) => ({ id: i + 1, city: `city-${i + 1}` }))
  const ds = await h.seed(rows, 'clean')
  await openQuality(page, ds.id)

  // Assert the fixture really is clean against the service first, so this
  // tests the zero-state rather than a fetch that quietly failed.
  const dup = await apiOk<DupResponse>('GET', duplicatesUrl(ds.id))
  expect(dup.group_count).toBe(0)
  expect(dup.duplicate_rows).toBe(0)
  const miss = await apiOk<MissingResponse>('GET', missingUrl(ds.id))
  expect(miss.columns.every((c) => c.null_count === 0)).toBe(true)
  expect(miss.rows_most_missing).toEqual([])

  const duplicates = page.getByTestId('duplicates-explorer')
  await expect(duplicates).toContainText('no duplicates')
  const dupText = await textOf(duplicates)
  // The absence is stated over a named population, not left blank.
  expect(dupText).toContain(`${dup.row_count} rows`)
  await expect(page.getByTestId('duplicates-group-count')).toHaveCount(0)
  await expect(page.getByTestId('duplicate-group')).toHaveCount(0)
  expect(dupText).not.toContain('NaN')

  const missing = page.getByTestId('missing-explorer')
  await expect(missing).toBeVisible()
  const affected = await readStat(page.getByTestId('missing-affected'))
  expect(affected.value).toBe('0')
  expect(affected.note).toContain('columns')
  // Every column states its zero rather than rendering blank or NaN%.
  for (const text of await textsOf(page.getByTestId('missing-column'))) {
    expect(text).toMatch(/\b0\s*·\s*0%/)
  }
  // No probe rows invented for a sheet that has no nulls, and no fold for two
  // columns.
  await expect(page.getByTestId('missing-row')).toHaveCount(0)
  await expect(page.getByTestId('missing-column-other')).toHaveCount(0)
  expect(await textOf(missing)).not.toContain('NaN')
})

/* -------------------------------------------------------------------- rules */

test('a rule can be created, toggled off and on, edited and deleted — each re-read from the API', async ({
  page,
  h,
}) => {
  const ds = await h.seed(
    Array.from({ length: 5 }, (_, i) => ({ amount: i * 10 })),
    'rulelife',
  )
  await openQuality(page, ds.id)

  const readRules = () =>
    apiOk<{ items: RuleRow[]; total: number }>('GET', `/datasets/${ds.id}/rules`)

  // ── create ──────────────────────────────────────────────────────────────
  const name = `range-${rand()}`
  await page.getByTestId('rule-add-toggle').click()
  await page.getByTestId('rule-name').fill(name)
  await page.getByTestId('rule-type').selectOption('range')
  // The form states the scope the SERVER will derive, and never sends it.
  await expect(page.getByTestId('rule-form')).toContainText('scope column')
  await expect(page.getByTestId('rule-form')).toContainText('derived server-side, never sent')
  await page.getByTestId('rule-column').selectOption('amount')
  await page.getByTestId('rule-params').fill('{"min": 0, "max": 100}')
  await page.getByTestId('rule-severity-warning').click()
  await expect(page.getByTestId('rule-severity-warning')).toHaveAttribute('aria-pressed', 'true')
  await page.getByTestId('rule-save').click()
  await expect(page.getByTestId('rule-form')).toBeHidden()

  const created = (await readRules()).items.find((r) => r.name === name)
  expect(created, `rule ${name} not persisted`).toBeTruthy()
  expect(created!.rule_type).toBe('range')
  expect(created!.severity).toBe('warning')
  expect(created!.enabled).toBe(true)
  expect(created!.parameters).toEqual({ min: 0, max: 100 })
  // The scope the form promised is the scope the server derived.
  expect(created!.scope_type).toBe('column')

  // ── toggle off, then back on ────────────────────────────────────────────
  const row = page.getByTestId('rule').filter({ hasText: name })
  await expect(row).toHaveCount(1)
  const enabled = async () => (await readRules()).items.find((r) => r.id === created!.id)?.enabled

  await row.getByTestId('rule-toggle').click()
  await expect(row.getByTestId('rule-toggle')).toHaveText('off')
  await expect.poll(enabled, { message: 'disable never persisted' }).toBe(false)

  await row.getByTestId('rule-toggle').click()
  await expect(row.getByTestId('rule-toggle')).toHaveText('on')
  await expect.poll(enabled, { message: 're-enable never persisted' }).toBe(true)

  // ── edit: what is patchable, and what the UI says is not ────────────────
  await row.getByTestId('rule-edit').click()
  const form = page.getByTestId('rule-edit-form')
  await expect(form).toContainText('rule_type')
  await expect(form).toContainText('scope_type')
  await expect(form).toContainText('are not patchable')
  // Not merely unmentioned: there is no control that would let you try.
  await expect(form.getByTestId('rule-type')).toHaveCount(0)
  await expect(form.locator('select')).toHaveCount(0)

  const renamed = `${name}-edited`
  await page.getByTestId('rule-edit-name').fill(renamed)
  await page.getByTestId('rule-edit-severity-error').click()
  await page.getByTestId('rule-edit-save').click()
  await expect(form).toBeHidden()

  await expect
    .poll(
      async () => {
        const r = (await readRules()).items.find((x) => x.id === created!.id)
        return r ? { name: r.name, severity: r.severity, type: r.rule_type } : null
      },
      { message: 'rule edit never persisted' },
    )
    .toEqual({ name: renamed, severity: 'error', type: 'range' })

  // The UI's claim, checked against the service: a PATCH carrying them changes
  // nothing, so retyping a rule cannot invalidate the results that cite it.
  const patched = await apiOk<RuleRow>('PATCH', `/datasets/${ds.id}/rules/${created!.id}`, {
    body: { rule_type: 'unique', scope_type: 'sheet' },
  })
  expect(patched.rule_type).toBe('range')
  expect(patched.scope_type).toBe('column')

  // ── delete ──────────────────────────────────────────────────────────────
  const editedRow = page.getByTestId('rule').filter({ hasText: renamed })
  await expect(editedRow).toHaveCount(1)
  await editedRow.getByTestId('rule-delete').click()
  await expect(editedRow).toHaveCount(0)

  const after = await readRules()
  expect(after.items.some((r) => r.id === created!.id)).toBe(false)
  expect(after.total).toBe(0)
})

/**
 * A dataset with one `error`-severity rule and one `warning`-severity rule,
 * both of which PASS. That combination is the point: a screen full of passing
 * error-severity rules is what rule 7 exists to stop reading as a screen on
 * fire.
 */
async function seedSeverityPair(h: Harness) {
  const ds = await h.seed(
    Array.from({ length: 4 }, (_, i) => ({ amount: i + 1 })),
    'sev',
  )
  const errName = `sev-err-${rand()}`
  const warnName = `sev-warn-${rand()}`
  await apiOk('POST', `/datasets/${ds.id}/rules`, {
    body: {
      name: errName,
      rule_type: 'not_null',
      sheet_selector: 'data',
      column_selector: 'amount',
      severity: 'error',
    },
  })
  await apiOk('POST', `/datasets/${ds.id}/rules`, {
    body: {
      name: warnName,
      rule_type: 'range',
      sheet_selector: 'data',
      column_selector: 'amount',
      parameters: { min: 0, max: 1000 },
      severity: 'warning',
    },
  })
  return { ds, errName, warnName }
}

/** Resolve a utility class's colour the way the browser reports it. */
function inkOf(page: Page, className: string): Promise<string> {
  return page.evaluate((cls) => {
    const probe = document.createElement('span')
    probe.className = cls
    document.body.appendChild(probe)
    const color = getComputedStyle(probe).color
    probe.remove()
    return color
  }, className)
}

const severityStyle = (loc: Locator) =>
  loc.evaluate((el) => {
    const s = getComputedStyle(el)
    return { color: s.color, weight: Number(s.fontWeight) }
  })

test('severity is typographic — a passing error-severity rule is not painted red', async ({
  page,
  h,
}) => {
  const { ds, errName, warnName } = await seedSeverityPair(h)

  await openQuality(page, ds.id)
  await page.getByTestId('validate-run').click()
  await expect(page.getByTestId('validation-result')).toContainText('passed')

  // Both rules pass. This is the screen that must not read as on fire.
  const run = await apiOk<{ items: RunSummary[] }>(
    'GET',
    `/datasets/${ds.id}/versions/1/validations?limit=1`,
  )
  expect(run.items[0].rules_failed).toBe(0)

  const errRow = page.getByTestId('rule').filter({ hasText: errName })
  const warnRow = page.getByTestId('rule').filter({ hasText: warnName })
  // Status is what happened; severity is a property of the rule. Both rules
  // passed, so both marks are the "good" shape however severe the rule is.
  await expect(errRow.locator('[data-slot="status"]')).toHaveAttribute('data-status', 'good')
  await expect(warnRow.locator('[data-slot="status"]')).toHaveAttribute('data-status', 'good')

  // Distinguished by the WORD — severity survives a monochrome print.
  await expect(errRow.locator('[data-severity]')).toHaveText('error')
  await expect(warnRow.locator('[data-severity]')).toHaveText('warning')
  await expect(errRow.locator('[data-severity]')).toHaveAttribute('data-severity', 'error')

  const err = await severityStyle(errRow.locator('[data-severity]'))
  const warn = await severityStyle(warnRow.locator('[data-severity]'))

  // …and by weight. `error` is 600, `warning` is not.
  expect(err.weight).toBeGreaterThan(warn.weight)

  // Neither wears the destructive red — resolved through the same pipeline the
  // browser reports, so this is not a string-format coincidence.
  const destructive = await inkOf(page, 'text-destructive')
  expect(err.color).not.toBe(destructive)
  expect(warn.color).not.toBe(destructive)

  // And `error` wears exactly the ink the rule's own name wears.
  const nameColor = await errRow
    .getByTestId('rule-name-text')
    .evaluate((el) => getComputedStyle(el).color)
  expect(err.color).toBe(nameColor)
})

/**
 * DEFECT — severity loses its ink token, so the two levels differ only in
 * weight.
 *
 * `Severity` buys the distinction with weight AND value: `error` is `--t1` at
 * 600, `warning` is `--t3`. But every caller passes a size class through
 * `className` (`text-micro` in the rule row, the insight list and the create
 * form), and `cn` is `twMerge(clsx(...))`. `tailwind-merge` does not know
 * `text-micro` is a font size — no built-in group matches it — so it files it
 * under text-COLOUR and drops the colour the component set:
 *
 *   cn('whitespace-nowrap', 'text-muted-foreground', 'ml-auto shrink-0 text-micro')
 *     → 'whitespace-nowrap ml-auto shrink-0 text-micro'
 *
 * The `warning` span therefore inherits its parent's ink and renders in the
 * same colour as `error`. Nothing throws, nothing looks broken, and half of
 * rule 7 is gone. Expressed here as the behaviour that should hold; marked
 * failing rather than fixed, because `src/` belongs to another agent.
 */
test('severity levels differ in value, not only in weight', async ({ page, h }) => {
  const { ds, errName, warnName } = await seedSeverityPair(h)
  await openQuality(page, ds.id)

  const errRow = page.getByTestId('rule').filter({ hasText: errName })
  const warnRow = page.getByTestId('rule').filter({ hasText: warnName })
  await expect(errRow.locator('[data-severity]')).toHaveText('error')

  const err = await severityStyle(errRow.locator('[data-severity]'))
  const warn = await severityStyle(warnRow.locator('[data-severity]'))

  // The two levels must not be the same ink …
  expect(err.color).not.toBe(warn.color)
  // … and each must be the token its component declares.
  expect(err.color).toBe(await inkOf(page, 'text-foreground'))
  expect(warn.color).toBe(await inkOf(page, 'text-muted-foreground'))
})

/* ---------------------------------------------------------------- the gate */

test('the gate blocks tag promotion and says so — upload and the raw tag PUT stay ungated', async ({
  page,
  h,
}) => {
  // A dataset whose only rule fails at error severity on every version.
  const ds = await h.seed([{ amount: 5 }, { amount: '' }, { amount: 7 }], 'gate')
  await apiOk('POST', `/datasets/${ds.id}/rules`, {
    body: {
      name: `gate-${rand()}`,
      rule_type: 'not_null',
      sheet_selector: 'data',
      column_selector: 'amount',
      severity: 'error',
    },
  })

  await openQuality(page, ds.id)
  await page.getByTestId('validate-run').click()

  const result = page.getByTestId('validation-result')
  await expect(result).toContainText('failed')
  // The consequence is named, and it is exactly one thing.
  await expect(result).toContainText('blocks tag promotion')
  const body = page.getByTestId('lens-body')
  await expect(body).toContainText('gates exactly one thing: tag promotion')
  await expect(body).toContainText('Upload and publish are ungated')
  await expect(body).toContainText('warning failures never block anything')

  const runs = await apiOk<{ items: RunSummary[] }>(
    'GET',
    `/datasets/${ds.id}/versions/1/validations?limit=1`,
  )
  expect(runs.items[0].error_failures).toBeGreaterThan(0)

  // "Upload is ungated" — prove it rather than quoting the panel: a second
  // version uploads onto a dataset whose current version just failed.
  await h.addVersion(ds.id, [{ amount: '' }, { amount: 9 }])
  const versions = await apiOk<{ items: unknown[] }>('GET', `/datasets/${ds.id}/versions`)
  expect(versions.items.length).toBe(2)

  // The raw PUT is the documented escape hatch, and it is ungated too.
  const tag = `g${rand()}`
  await apiOk('PUT', `/datasets/${ds.id}/tags`, { body: { tag_name: tag, version_number: 1 } })

  // Promotion is the one operation that refuses. v2 has a completed run with
  // error failures, so the refusal names the failure, not the absence of a run.
  await apiOk('POST', `/datasets/${ds.id}/versions/2/validate`)
  const refused = await api('POST', `/datasets/${ds.id}/tags/${tag}/promote`, {
    body: { version_number: 2 },
  })
  expect(refused.status).toBe(409)
  expect(refused.body.code).toBe('validation-failed')

  // And the tag did not move.
  const tags = await apiOk<{ items: { tag_name: string; version_number: number }[] }>(
    'GET',
    `/datasets/${ds.id}/tags`,
  )
  expect(tags.items.find((t) => t.tag_name === tag)?.version_number).toBe(1)
})

test('warning failures never block: the panel says so and promotion proves it', async ({
  page,
  h,
}) => {
  // One error rule that passes, one warning rule that fails (500 > max 100).
  const rows = [{ amount: 5 }, { amount: 50 }, { amount: 500 }]
  const ds = await h.seed(rows, 'warngate')
  await apiOk('POST', `/datasets/${ds.id}/rules`, {
    body: {
      name: `present-${rand()}`,
      rule_type: 'not_null',
      sheet_selector: 'data',
      column_selector: 'amount',
      severity: 'error',
    },
  })
  await apiOk('POST', `/datasets/${ds.id}/rules`, {
    body: {
      name: `cap-${rand()}`,
      rule_type: 'range',
      sheet_selector: 'data',
      column_selector: 'amount',
      parameters: { min: 0, max: 100 },
      severity: 'warning',
    },
  })

  await openQuality(page, ds.id)
  await page.getByTestId('validate-run').click()

  const result = page.getByTestId('validation-result')
  await expect(result).toContainText('passed with warnings')
  await expect(result).toContainText('nothing is blocked')
  await expect(result).toContainText('advisory — never blocks')

  const runs = await apiOk<{ items: RunSummary[] }>(
    'GET',
    `/datasets/${ds.id}/versions/1/validations?limit=1`,
  )
  expect(runs.items[0].error_failures).toBe(0)
  expect(runs.items[0].warning_failures).toBeGreaterThan(0)

  // The claim under test: a run with warning failures still promotes.
  const tag = `w${rand()}`
  await apiOk('PUT', `/datasets/${ds.id}/tags`, { body: { tag_name: tag, version_number: 1 } })
  await h.addVersion(ds.id, rows)
  const v2 = await apiOk<RunSummary>('POST', `/datasets/${ds.id}/versions/2/validate`)
  expect(v2.error_failures).toBe(0)
  expect(v2.warning_failures).toBeGreaterThan(0)

  const promoted = await api('POST', `/datasets/${ds.id}/tags/${tag}/promote`, {
    body: { version_number: 2, reason: 'warnings do not gate' },
  })
  expect(promoted.status, JSON.stringify(promoted.body).slice(0, 200)).toBeLessThan(300)

  const tags = await apiOk<{ items: { tag_name: string; version_number: number }[] }>(
    'GET',
    `/datasets/${ds.id}/tags`,
  )
  expect(tags.items.find((t) => t.tag_name === tag)?.version_number).toBe(2)
})

/* ------------------------------------------------------------------ health */

test('an unprofiled dataset reads unknown as a word, and profiling changes it', async ({
  page,
  h,
}) => {
  const ds = await h.seed(
    Array.from({ length: 6 }, (_, i) => ({ id: i + 1, city: `city-${i}` })),
    'health',
  )
  await openQuality(page, ds.id)

  const before = await apiOk<HealthResponse>('GET', `/datasets/${ds.id}/health`)
  // Nothing profiles on upload, so these two have no input at all.
  expect(before.dimensions.missing_data.status).toBe('unknown')
  expect(before.dimensions.duplicates.status).toBe('unknown')

  const rows = page.getByTestId('health-dimension')
  await expect(rows).toHaveCount(Object.keys(before.dimensions).length)
  const keys = await rows.evaluateAll((els) =>
    els.map((e) => e.querySelector('[data-slot="identifier"]')?.textContent?.trim() ?? ''),
  )
  expect(new Set(keys)).toEqual(new Set(Object.keys(before.dimensions)))

  // Every dimension reports the status the API gave it, as a shape AND a word.
  for (const [key, dim] of Object.entries(before.dimensions)) {
    const status = rows.nth(keys.indexOf(key)).locator('[data-slot="status"]')
    await expect(status).toHaveAttribute('data-status', STATUS_KIND[dim.status] ?? 'unknown')
    await expect(status.locator('span:not([aria-hidden="true"])')).toHaveText(dim.status)
  }

  // `unknown` specifically: a word, not a grey dot on its own …
  const missingRow = rows.nth(keys.indexOf('missing_data'))
  const missingStatus = missingRow.locator('[data-slot="status"]')
  await expect(missingStatus.locator('span:not([aria-hidden="true"])')).toHaveText('unknown')
  // … and never dressed up as a pass.
  await expect(missingStatus).toHaveAttribute('data-status', 'unknown')
  await expect(missingStatus).not.toHaveAttribute('data-status', 'good')
  await expect(page.getByTestId('lens-body')).toContainText('until someone runs a profile')

  // The panel offers the run rather than leaving a dead end — take it.
  await page.getByTestId('profile-run').click()
  await expect
    .poll(
      async () =>
        (await apiOk<HealthResponse>('GET', `/datasets/${ds.id}/health`)).dimensions.missing_data
          .status,
      { message: 'profiling never changed the missing_data dimension' },
    )
    .not.toBe('unknown')

  const after = await apiOk<HealthResponse>('GET', `/datasets/${ds.id}/health`)
  await expect(
    rows.nth(keys.indexOf('missing_data')).locator('[data-slot="status"] span:not([aria-hidden="true"])'),
  ).toHaveText(after.dimensions.missing_data.status)

  // Insights are computed from the persisted profile, so they arrive with it.
  const profiled = await apiOk<{ items: { insights?: unknown[] }[] }>(
    'GET',
    `/datasets/${ds.id}/versions/1/profile-runs?limit=50`,
  )
  const insightCount = profiled.items.reduce((n, r) => n + (r.insights?.length ?? 0), 0)
  expect(insightCount).toBeGreaterThan(0)
  await expect(page.getByTestId('insight')).toHaveCount(Math.min(10, insightCount))
})

/* ------------------------------------------------------- masking, not error */

test('for a viewer the explorers mask rather than refuse, and name what was masked', async ({
  page,
  h,
}) => {
  // Four e-mails, each on two rows. The duplication is a property of the CSV,
  // and it has to survive masking or the report is useless to the seat that
  // most needs it.
  const rows = Array.from({ length: 8 }, (_, i) => ({
    id: i + 1,
    email: `SECRET-LEAKCHECK-${i % 4}@x.io`,
  }))
  const ds = await h.seed(rows, 'dupmask')
  await h.markSensitive(ds.id, 'email')

  const viewer = await h.viewer()
  await h.asSeat(page, viewer.user_id, 'Viewer')
  await openQuality(page, ds.id)

  await page.getByTestId('duplicates-subset').selectOption('email')
  const explorer = page.getByTestId('duplicates-explorer')

  // A refusal would be the wrong answer here: this endpoint masks.
  await expect(explorer).toContainText('duplicates found')
  await expect(explorer).toContainText('masked for this seat')
  await expect(explorer).toContainText('email')
  await expect(explorer).toContainText('stable pseudonym')

  const asViewer = await apiOk<DupResponse>('GET', duplicatesUrl(ds.id, 'email'), {
    userId: viewer.user_id,
  })
  const asAdmin = await apiOk<DupResponse>('GET', duplicatesUrl(ds.id, 'email'))
  expect(asViewer.masked_columns).toContain('email')
  expect(asAdmin.masked_columns).toEqual([])
  // Masking must not change the ANSWER — four pairs either way.
  expect(asAdmin.group_count).toBe(4)
  expect(asViewer.group_count).toBe(asAdmin.group_count)
  expect(asViewer.duplicate_rows).toBe(asAdmin.duplicate_rows)
  // Distinct groups stay distinct: four different pseudonyms, not one constant.
  const pseudonyms = asViewer.groups.map((g) => String(g.key.email))
  expect(new Set(pseudonyms).size).toBe(asViewer.groups.length)
  expect(JSON.stringify(asViewer)).not.toContain('SECRET-LEAKCHECK')

  await expect(page.getByTestId('duplicate-group')).toHaveCount(asViewer.groups.length)
  // And the value itself never reaches the page.
  expect(await page.getByTestId('lens-body').innerText()).not.toContain('SECRET-LEAKCHECK')

  // The missing report masks the same column and says so, rather than hiding
  // the fact that the column exists.
  const missing = await apiOk<MissingResponse>('GET', missingUrl(ds.id), {
    userId: viewer.user_id,
  })
  expect(missing.masked_columns).toContain('email')
  await expect(page.getByTestId('missing-explorer')).toContainText('masked')

  // For contrast, the refusal that IS a refusal: a computed profile over raw
  // values is 403 for this seat, which is why these two mask instead.
  const refusedProfile = await api('POST', '/profile', {
    body: { dataset_id: ds.id, version_number: 1, sheet: 'data' },
    userId: viewer.user_id,
  })
  expect(refusedProfile.status).toBe(403)
  expect(refusedProfile.body.code).toBe('sensitive-data-restricted')
})

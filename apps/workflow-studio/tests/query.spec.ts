import { test, expect, goto, api, apiOk, API_BASE } from './fixtures'
import type { Locator, Page } from '@playwright/test'

/**
 * The `/query` route — the filter builder, the operator palette, sort,
 * projection, cursor paging, save-as-view and the download panel.
 *
 * Three things shape every test in this file.
 *
 * 1. **The answers are derived, never written down twice.** `fixtureRows()` is
 *    the single source of truth: "12 rows are active" is
 *    `rows.filter(r => r.status === 'active').length`, computed in the test, so
 *    editing the fixture can never leave a stale magic number passing.
 *
 * 2. **The client's refusals are checked against the server's.** The palette
 *    greys the operators it believes the server would reject with `400
 *    operator-type-mismatch`. That belief is only worth something if it matches
 *    the real API, so one test drives all 36 operators against all three column
 *    kinds and compares the DOM's verdict with the service's.
 *
 * 3. **A masked column must be refused BEFORE the click.** Filtering, sorting or
 *    searching one is `400 sensitive-column-not-filterable` server-side — a
 *    steerable `total` is a binary search over the hidden value. So the controls
 *    have to be absent client-side, not fail on use.
 */

/* ------------------------------------------------------------------ fixture */

interface Row {
  /** So a Row is a `Record<string, unknown>`, which is what `h.seed` takes. */
  [column: string]: unknown
  row_id: number
  status: string
  region: string
  score: number
  signup_date: string
}

/**
 * The fixture: one number column, two text columns and a date column, because
 * operator gating is a function of dtype and a fixture without all three cannot
 * exercise it. (CSV inference gives BIGINT / VARCHAR / DATE, which is exactly
 * the three families `validate.py` distinguishes.)
 *
 * Scores are `7i mod 100`, injective for i < 30 — a sort test needs a key with
 * no ties and a paging test needs one with many, so they use different fixtures
 * rather than one that is bad at both.
 */
function fixtureRows(n = 30): Row[] {
  return Array.from({ length: n }, (_, i) => ({
    row_id: i + 1,
    status: i % 5 < 2 ? 'active' : i % 5 === 2 ? 'churned' : 'trial',
    region: ['north', 'south', 'east'][i % 3],
    score: (i * 7) % 100,
    signup_date: `2024-${String(1 + (i % 12)).padStart(2, '0')}-15`,
  }))
}

/** FastAPI publishes the live schema beside the API, so this is a fresh read. */
const OPENAPI_URL = `${API_BASE.replace(/\/api\/v1\/?$/, '')}/openapi.json`

/**
 * `Filter.op` off the running service — the authority this page claims to
 * mirror. Writing the 36 names out here would only test the test.
 */
async function declaredOperators(): Promise<string[]> {
  const res = await fetch(OPENAPI_URL)
  if (!res.ok) throw new Error(`GET ${OPENAPI_URL} → ${res.status}`)
  const doc = (await res.json()) as {
    components: { schemas: { Filter: { properties: { op: { enum: string[] } } } } }
  }
  return doc.components.schemas.Filter.properties.op.enum
}

/* ------------------------------------------------------------------ helpers */

/**
 * Data column headers, in the order the grid renders them.
 *
 * `[data-column]` scopes this to real columns: the grid also draws a row-ordinal
 * gutter header, which is chrome, and selecting by position would fold it in.
 */
const gridHeaders = (page: Page): Promise<string[]> =>
  page
    .locator('thead th[data-column]')
    .evaluateAll((els) => els.map((e) => e.getAttribute('data-column') ?? ''))

/** Every rendered value of one column, located by header rather than by index. */
async function columnValues(page: Page, column: string): Promise<string[]> {
  const headers = await gridHeaders(page)
  const idx = headers.indexOf(column)
  expect(idx, `"${column}" is in the grid (headers: ${headers.join(', ')})`).toBeGreaterThanOrEqual(
    0,
  )
  // +2 because nth-child is 1-based AND the first cell is the ordinal gutter.
  return page
    .locator(`tbody tr td:nth-child(${idx + 2})`)
    .evaluateAll((tds) => tds.map((td) => td.textContent?.trim() ?? ''))
}

/**
 * The value editor is rendered TWICE for the selected condition — once inline in
 * the row and once in the operator dock — so every operand write must be scoped
 * to its row or it is ambiguous under strict mode. Rule 4, at its narrowest.
 */
async function fillCondition(row: Locator, column: string, op: string, value?: string) {
  await row.getByTestId('condition-column').selectOption(column)
  await row.getByTestId('condition-op').selectOption(op)
  if (value !== undefined) await row.getByTestId('condition-value').fill(value)
}

/**
 * Append a condition to the ROOT group and fill it in.
 *
 * Conditions are addressed by ordinal, which is sound only because these tests
 * build the tree top-down: a group is appended after the root conditions that
 * precede it, so a root condition added before any group keeps its DOM
 * position. The component emits no per-node id to key off instead.
 */
async function addRootCondition(
  page: Page,
  column: string,
  op: string,
  value?: string,
): Promise<Locator> {
  const before = await page.getByTestId('condition-row').count()
  await page.getByTestId('add-filter').click()
  await expect(page.getByTestId('condition-row')).toHaveCount(before + 1)
  const row = page.getByTestId('condition-row').nth(before)
  await fillCondition(row, column, op, value)
  return row
}

/**
 * Commit the draft spec and wait for the page it produces.
 *
 * Waiting on `networkidle` alone is not enough: the grid keeps the previous
 * page on screen while the new one is in flight (react-query `placeholderData`),
 * so an assertion can read the OLD rows and pass against the wrong answer. Wait
 * for the POST itself, and tolerate its absence — re-running an unchanged spec
 * can be served from cache without a request.
 *
 * The leading settle is not belt-and-braces. A control touched just before Run
 * (the page-size segments commit immediately) leaves a query in flight, and
 * `waitForResponse` would match THAT one and return while the run's own request
 * was still going. That produced a paging walk whose first page was the
 * pre-sort page: 120 rows seen, 104 distinct.
 */
async function runQuery(page: Page) {
  await page.waitForLoadState('networkidle')
  const settled = page
    .waitForResponse((r) => r.request().method() === 'POST' && r.url().includes('/query'), {
      timeout: 5_000,
    })
    .catch(() => null)
  await page.getByTestId('run-query').click()
  await settled
  await page.waitForLoadState('networkidle')
}

const paletteOps = (page: Page, extra = ''): Promise<string[]> =>
  page
    .getByTestId('operator-palette')
    .locator(`button[data-testid^="operator-"]${extra}`)
    .evaluateAll((els) => els.map((e) => e.getAttribute('data-testid')!.slice('operator-'.length)))

/** Every operator the palette renders, applicable or not. */
const allPaletteOperators = (page: Page) => paletteOps(page)
/** Those the palette says the selected condition's column can actually take. */
const availableOperators = (page: Page) => paletteOps(page, '[data-available]')

/**
 * A well-typed operand per operator, so that a probe which comes back 400 does
 * so for the reason under test and not because the value was nonsense.
 */
const NO_OPERAND = new Set([
  'is_null',
  'is_not_null',
  'is_empty',
  'is_not_empty',
  'is_duplicate',
  'is_unique',
])
const PAIR = new Set(['between', 'not_between', 'len_between', 'date_between'])
const LIST = new Set(['in', 'not_in'])
const NUMERIC_OPERAND = new Set([
  'len_eq',
  'len_gt',
  'len_gte',
  'len_lt',
  'len_lte',
  'len_between',
  'top_n',
  'bottom_n',
  'last_n_days',
])
const FRACTION_OPERAND = new Set(['top_pct', 'bottom_pct'])
const DATE_OPERAND = new Set(['date_before', 'date_after', 'date_between'])

function probeFilter(op: string, column: string): Record<string, unknown> {
  const base = { column, op }
  if (NO_OPERAND.has(op)) return base
  if (PAIR.has(op)) {
    if (DATE_OPERAND.has(op)) return { ...base, value: ['2024-01-01', '2024-12-31'] }
    if (NUMERIC_OPERAND.has(op)) return { ...base, value: [1, 9] }
    return { ...base, value: [1, 99] }
  }
  if (LIST.has(op)) return { ...base, value: ['active'] }
  if (DATE_OPERAND.has(op)) return { ...base, value: '2024-06-01' }
  if (FRACTION_OPERAND.has(op)) return { ...base, value: 0.5 }
  if (NUMERIC_OPERAND.has(op)) return { ...base, value: 3 }
  return { ...base, value: 'active' }
}

const queryPath = (datasetId: string, version = 1) =>
  `/datasets/${datasetId}/versions/${version}/sheets/data/query`

/** Read a number the page prints, out of a container's own text. */
async function numberIn(scope: Locator, re: RegExp): Promise<number | null> {
  const m = (await scope.innerText()).match(re)
  return m ? Number(m[1]) : null
}

/* --------------------------------------------------- the operator vocabulary */

test('the palette offers exactly the operators Filter.op declares, and none of the three that do not exist', async ({
  page,
  h,
}) => {
  const declared = await declaredOperators()
  const ds = await h.seed(fixtureRows(), 'ops')

  await goto(page, `/query?dataset=${ds.id}`)
  await expect(page.getByTestId('query-page')).toBeVisible()

  const rendered = await allPaletteOperators(page)

  // Set equality both ways: a missing operator and an invented one are both
  // bugs, and asserting only a count would catch neither.
  expect([...rendered].sort()).toEqual([...declared].sort())
  expect(declared.length, 'the DSL is 36 operators wide').toBe(36)

  // The three that design drafts show and the API does not have.
  for (const ghost of ['istartswith', 'this_month', 'outlier']) {
    expect(declared, `${ghost} must not be in Filter.op`).not.toContain(ghost)
    expect(rendered, `${ghost} must not be offered by the palette`).not.toContain(ghost)
  }

  // The counts the page prints are that same number, not a decorative literal.
  await expect(page.getByText(`${declared.length} in the DSL`).first()).toBeVisible()
  await expect(page.getByTestId('op-search')).toHaveAttribute(
    'placeholder',
    new RegExp(`${declared.length} operators`),
  )

  // The dock's search narrows the list it renders.
  await page.getByTestId('op-search').fill('len_')
  const filtered = await allPaletteOperators(page)
  expect(filtered.length).toBeGreaterThan(0)
  expect(filtered).toEqual(declared.filter((o) => o.includes('len_')))
})

test('operator type-gating strikes what the column cannot take, and says what it needs instead', async ({
  page,
  h,
}) => {
  const ds = await h.seed(fixtureRows(), 'gating')
  await goto(page, `/query?dataset=${ds.id}`)

  const all = await allPaletteOperators(page)

  // ---- a NUMBER column refuses the text matchers, the length ops and the dates.
  await addRootCondition(page, 'score', 'eq', '1')
  const numeric = await availableOperators(page)
  const numericRefused = all.filter((o) => !numeric.includes(o))

  for (const op of ['contains', 'icontains', 'starts_with', 'regex', 'len_gt', 'len_between']) {
    expect(numericRefused, `${op} needs text, so a BIGINT column must not offer it`).toContain(op)
    const btn = page.getByTestId(`operator-${op}`)
    await expect(btn).toBeDisabled()
    // The reason is on the control, in the server's own vocabulary.
    await expect(btn).toHaveAttribute(
      'title',
      new RegExp(`400 operator-type-mismatch.*'${op}' requires a text column`),
    )
    // And it reads as withdrawn, not merely inert.
    await expect(btn.locator('.line-through')).toHaveText(op)
  }
  for (const op of ['date_before', 'date_after', 'date_between', 'last_n_days']) {
    expect(numericRefused, `${op} needs a date column`).toContain(op)
    await expect(page.getByTestId(`operator-${op}`)).toHaveAttribute(
      'title',
      /requires a date column/,
    )
  }
  // Comparison, set, range, rank and null ops are legal on ANY dtype — greying
  // them would invent a refusal the API does not make.
  for (const op of ['eq', 'gt', 'in', 'between', 'top_n', 'top_pct', 'is_null', 'is_duplicate']) {
    expect(numeric, `${op} is legal on every dtype`).toContain(op)
    await expect(page.getByTestId(`operator-${op}`)).toBeEnabled()
  }
  // The dock states the arithmetic it just did.
  await expect(
    page.getByText(`${numeric.length} of ${all.length} available`, { exact: true }).first(),
  ).toBeVisible()

  // The native picker in the row must agree with the palette, or the refusal is
  // cosmetic — the select is how most people will actually pick an operator.
  const rowRefused = await page
    .getByTestId('condition-row')
    .first()
    .getByTestId('condition-op')
    .locator('option[disabled]')
    .evaluateAll((els) => els.map((e) => (e as HTMLOptionElement).value))
  expect([...rowRefused].sort()).toEqual([...numericRefused].sort())

  // ---- a TEXT column takes the matchers and refuses only the four date ops.
  const row = page.getByTestId('condition-row').first()
  await row.getByTestId('condition-column').selectOption('status')
  const text = await availableOperators(page)
  expect(all.filter((o) => !text.includes(o)).sort()).toEqual(
    ['date_before', 'date_after', 'date_between', 'last_n_days'].sort(),
  )
  for (const op of ['contains', 'regex', 'len_gt']) {
    await expect(page.getByTestId(`operator-${op}`)).toBeEnabled()
  }

  // ---- a DATE column takes the date ops and refuses the twelve string ops.
  await row.getByTestId('condition-column').selectOption('signup_date')
  const dated = await availableOperators(page)
  for (const op of ['date_before', 'date_after', 'date_between', 'last_n_days']) {
    expect(dated, `${op} applies to a DATE column`).toContain(op)
  }
  expect(
    all.filter((o) => !dated.includes(o)).length,
    'the six text matchers plus the six length ops',
  ).toBe(12)

  // ---- changing the column must not carry an operator the new one refuses.
  await row.getByTestId('condition-column').selectOption('status')
  await row.getByTestId('condition-op').selectOption('contains')
  await expect(row).toHaveAttribute('data-op', 'contains')
  await row.getByTestId('condition-column').selectOption('score')
  // Carrying `contains` across would be a 400 discovered on Run.
  await expect(row).not.toHaveAttribute('data-op', 'contains')
  await expect(row.getByTestId('condition-op')).toHaveValue('eq')
})

test('every greyed operator is one the server really refuses, and every offered one is not', async ({
  page,
  h,
}) => {
  const ds = await h.seed(fixtureRows(), 'agree')
  await goto(page, `/query?dataset=${ds.id}`)

  const row = await addRootCondition(page, 'score', 'eq', '1')
  const all = await allPaletteOperators(page)

  // One column per dtype family the server distinguishes.
  for (const { column, kind } of [
    { column: 'score', kind: 'BIGINT' },
    { column: 'status', kind: 'VARCHAR' },
    { column: 'signup_date', kind: 'DATE' },
  ]) {
    await row.getByTestId('condition-column').selectOption(column)
    const offered = new Set(await availableOperators(page))
    expect(offered.size).toBeGreaterThan(0)

    const verdicts = await Promise.all(
      all.map(async (op) => {
        const res = await api('POST', queryPath(ds.id), {
          body: { limit: 1, filters: { logic: 'and', conditions: [probeFilter(op, column)] } },
        })
        return { op, status: res.status, code: (res.body as { code?: string } | null)?.code ?? null }
      }),
    )

    for (const v of verdicts) {
      if (offered.has(v.op)) {
        // It may still 400 on a bad operand — but never for the reason the
        // palette said it would not.
        expect(
          v.code,
          `${v.op} is offered on ${column} (${kind}) but the server calls it a type mismatch`,
        ).not.toBe('operator-type-mismatch')
      } else {
        expect(
          { op: v.op, status: v.status, code: v.code },
          `${v.op} is struck through on ${column} (${kind}), so the server must refuse it`,
        ).toEqual({ op: v.op, status: 400, code: 'operator-type-mismatch' })
      }
    }
  }
})

/* ---------------------------------------------------------------- filtering */

test('a filter narrows the result to exactly the rows that match', async ({ page, h }) => {
  const rows = fixtureRows()
  const expected = rows.filter((r) => r.status === 'active')
  const ds = await h.seed(rows, 'narrow')

  await goto(page, `/query?dataset=${ds.id}`)
  // Unfiltered first, so the narrowing below is a change and not a coincidence.
  await expect(page.getByTestId('matched-total')).toHaveText(String(rows.length))
  await expect(page.locator('tbody tr')).toHaveCount(rows.length)

  await addRootCondition(page, 'status', 'eq', 'active')
  await expect(page.getByText('unrun changes').first()).toBeVisible()
  await runQuery(page)

  expect(expected.length).toBeGreaterThan(0)
  expect(expected.length).toBeLessThan(rows.length)

  await expect(page.getByTestId('matched-total')).toHaveText(String(expected.length))
  await expect(page.locator('tbody tr')).toHaveCount(expected.length)
  await expect(page.getByText('unrun changes')).toHaveCount(0)

  // Not just the count — the identities.
  const shown = (await columnValues(page, 'row_id')).map(Number).sort((a, b) => a - b)
  expect(shown).toEqual(expected.map((r) => r.row_id).sort((a, b) => a - b))

  // And the compiled spec, asked of the API directly, agrees.
  const spec = JSON.parse((await page.getByTestId('compiled-spec').textContent()) ?? '{}')
  const viaApi = await apiOk<{ total: number }>('POST', queryPath(ds.id), { body: spec })
  expect(viaApi.total).toBe(expected.length)
})

test('nesting an OR group inside an AND root changes the answer, both ways', async ({ page, h }) => {
  const rows = fixtureRows()
  const ds = await h.seed(rows, 'nest')
  await goto(page, `/query?dataset=${ds.id}`)

  // root(and): status = active AND ( region = north OR score > 50 )
  await addRootCondition(page, 'status', 'eq', 'active')
  await page.getByTestId('add-group').click()
  await expect(page.getByTestId('filter-group')).toHaveCount(1)

  const group = page.getByTestId('filter-group')
  await group.getByTestId('add-filter-nested').click()
  await fillCondition(page.getByTestId('condition-row').nth(1), 'region', 'eq', 'north')
  await group.getByTestId('add-filter-nested').click()
  await fillCondition(page.getByTestId('condition-row').nth(2), 'score', 'gt', '50')

  // The tree's self-report is derived from the tree, so check it against the DOM.
  const conditions = await page.getByTestId('condition-row').count()
  const nested = await page.getByTestId('filter-group').count()
  await expect(page.getByTestId('tree-counts')).toContainText(`${conditions} conditions`)
  await expect(page.getByTestId('tree-counts')).toContainText(`${nested} nested groups`)
  // Root is depth 0, so one level of nesting is depth 1.
  await expect(page.getByTestId('tree-counts')).toContainText('depth 1')

  const inner = (r: Row) => r.region === 'north' || r.score > 50
  const andAnswer = rows.filter((r) => r.status === 'active' && inner(r))
  const orAnswer = rows.filter((r) => r.status === 'active' || inner(r))
  // A test that cannot tell the two apart proves nothing.
  expect(andAnswer.length).not.toBe(orAnswer.length)

  await runQuery(page)
  await expect(page.getByTestId('matched-total')).toHaveText(String(andAnswer.length))

  // Flip the ROOT to `or` — same three conditions, different answer.
  await page.getByTestId('root-logic-or').click()
  await runQuery(page)
  await expect(page.getByTestId('matched-total')).toHaveText(String(orAnswer.length))

  // Flip the NESTED group to `and`, keeping the root on `or`.
  await group
    .getByRole('radiogroup', { name: 'Group logic' })
    .getByRole('radio', { name: 'and' })
    .click()
  const bothAnswer = rows.filter(
    (r) => r.status === 'active' || (r.region === 'north' && r.score > 50),
  )
  expect(bothAnswer.length).not.toBe(orAnswer.length)
  await runQuery(page)
  await expect(page.getByTestId('matched-total')).toHaveText(String(bothAnswer.length))

  // Ungrouping splices the children into the parent: same conditions, flat, and
  // therefore a third answer.
  await group.getByTestId('ungroup').click()
  await expect(page.getByTestId('filter-group')).toHaveCount(0)
  await expect(page.getByTestId('condition-row')).toHaveCount(conditions)
  const flatAnswer = rows.filter(
    (r) => r.status === 'active' || r.region === 'north' || r.score > 50,
  )
  await runQuery(page)
  await expect(page.getByTestId('matched-total')).toHaveText(String(flatAnswer.length))
})

test('an incomplete condition is not compiled, so it never silently matches everything', async ({
  page,
  h,
}) => {
  const rows = fixtureRows()
  const ds = await h.seed(rows, 'incomplete')
  await goto(page, `/query?dataset=${ds.id}`)

  // `in` needs a non-empty list, and this one has none.
  await addRootCondition(page, 'status', 'in')
  await expect(page.getByTestId('matched-total')).toHaveText(String(rows.length))
  await expect(page.getByText('1 incomplete').first()).toBeVisible()

  const draft = JSON.parse((await page.getByTestId('compiled-spec').textContent()) ?? '{}')
  expect(draft.filters ?? null, 'an operand-less condition must not compile').toBeNull()

  // Fill it in and it starts biting.
  const row = page.getByTestId('condition-row').first()
  await row.getByTestId('condition-value').fill('active')
  await row.getByTestId('condition-value').press('Enter')
  await expect(row.getByTestId('value-chip')).toHaveText('active')
  await runQuery(page)
  await expect(page.getByTestId('matched-total')).toHaveText(
    String(rows.filter((r) => r.status === 'active').length),
  )
})

/* --------------------------------------------------------------------- sort */

test('the sort stack is an ordered array, and the tiebreak that makes paging safe is stated', async ({
  page,
  h,
}) => {
  const rows = fixtureRows()
  const ds = await h.seed(rows, 'sort')
  await goto(page, `/query?dataset=${ds.id}`)

  const unsorted = await columnValues(page, 'score')

  await page.getByTestId('sort-add').selectOption('status')
  await page.getByTestId('sort-add').selectOption('score')
  await expect(page.getByTestId('sort-row')).toHaveCount(2)
  await page.getByTestId('sort-dir-1-desc').click()
  await runQuery(page)

  // status ascending, then score descending — applied 1 → n, not one header arrow.
  const expected = [...rows].sort((a, b) => a.status.localeCompare(b.status) || b.score - a.score)
  await expect
    .poll(async () => columnValues(page, 'score'))
    .toEqual(expected.map((r) => String(r.score)))
  expect(await columnValues(page, 'status')).toEqual(expected.map((r) => r.status))
  // ...and that is genuinely a different order from the one we started with.
  expect(await columnValues(page, 'score')).not.toEqual(unsorted)

  // Rank markers on the headers carry the position and the direction.
  await expect(page.locator('thead th[data-column="status"]')).toContainText('1▲')
  await expect(page.locator('thead th[data-column="score"]')).toContainText('2▼')
  await expect(page.getByText('sort 2 keys + full-row tiebreak').first()).toBeVisible()
  // The reason cursor pages cannot skip or duplicate a tied row.
  await expect(page.getByTestId('sort-stack').locator('..')).toContainText(
    'every remaining column ascending',
  )

  // Reordering the array reorders the result.
  await page.getByLabel('Move score up').click()
  await runQuery(page)
  const swapped = [...rows].sort((a, b) => b.score - a.score || a.status.localeCompare(b.status))
  await expect
    .poll(async () => columnValues(page, 'score'))
    .toEqual(swapped.map((r) => String(r.score)))
  await expect(page.locator('thead th[data-column="score"]')).toContainText('1▼')

  // Removing a key drops it from the array and from the header.
  await page.getByTestId('sort-remove').first().click()
  await expect(page.getByTestId('sort-row')).toHaveCount(1)
  await runQuery(page)
  await expect(page.getByText('sort 1 key + full-row tiebreak').first()).toBeVisible()
  await expect(page.locator('thead th[data-column="score"]')).not.toContainText('1▼')
})

/* --------------------------------------------------------------- projection */

test('the projection decides which columns come back, and in which order', async ({ page, h }) => {
  const rows = fixtureRows()
  const ds = await h.seed(rows, 'project')
  const allColumns = Object.keys(rows[0])

  await goto(page, `/query?dataset=${ds.id}`)
  expect(await gridHeaders(page)).toEqual(allColumns)

  const dropped = ['region', 'signup_date']
  for (const c of dropped) await page.getByTestId(`projection-${c}`).click()

  const kept = allColumns.filter((c) => !dropped.includes(c))
  expect(await gridHeaders(page)).toEqual(kept)

  await runQuery(page)

  // The grid is drawn from the projection, so on its own it cannot prove the
  // API returned a narrower row. Send the compiled spec and look at the keys.
  const spec = JSON.parse((await page.getByTestId('compiled-spec').textContent()) ?? '{}')
  expect(spec.columns).toEqual(kept)
  const viaApi = await apiOk<{ items: Record<string, unknown>[] }>('POST', queryPath(ds.id), {
    body: spec,
  })
  expect(Object.keys(viaApi.items[0])).toEqual(kept)

  // A dropped value must not be sitting in the page either.
  expect(await page.locator('tbody').innerText()).not.toContain('north')

  // Filtering an UN-projected column is legal — the projection is what comes
  // back, not what may be computed over.
  await addRootCondition(page, 'region', 'eq', 'north')
  await runQuery(page)
  await expect(page.getByTestId('matched-total')).toHaveText(
    String(rows.filter((r) => r.region === 'north').length),
  )
  expect(await gridHeaders(page)).toEqual(kept)

  // "Minimum" keeps one column, because an empty `columns` means EVERY column
  // on the wire — the floor is one, not zero.
  await page.getByTestId('projection-none').click()
  expect(await gridHeaders(page)).toEqual([allColumns[0]])
  await page.getByTestId('projection-all').click()
  expect(await gridHeaders(page)).toEqual(allColumns)
})

/* ------------------------------------------------------------ cursor paging */

test('cursor paging walks every row exactly once, and offers no page to jump to', async ({
  page,
  h,
}) => {
  // 120 rows over 3 values: forty-way ties on the sort key, which is what a
  // non-total ORDER BY silently skips and duplicates rows on.
  const rows = Array.from({ length: 120 }, (_, i) => ({ row_id: i + 1, grp: i % 3 }))
  const ds = await h.seed(rows, 'paging')
  const pageSize = 25

  await goto(page, `/query?dataset=${ds.id}`)
  await expect(page.getByTestId('matched-total')).toHaveText(String(rows.length))

  await page.getByTestId(`page-size-${pageSize}`).click()
  await page.getByTestId('sort-add').selectOption('grp')
  await runQuery(page)
  await expect(page.locator('tbody tr')).toHaveCount(pageSize)

  // Pin the start of the walk: 25 rows are on screen either way, so a row count
  // cannot tell the sorted first page from the unsorted one it replaced. Ask
  // the API for page one of this exact spec and wait for the grid to be it.
  const spec = JSON.parse((await page.getByTestId('compiled-spec').textContent()) ?? '{}')
  const apiFirst = await apiOk<{ items: { row_id: number }[] }>('POST', queryPath(ds.id), {
    body: spec,
  })
  await expect
    .poll(async () => columnValues(page, 'row_id'))
    .toEqual(apiFirst.items.map((r) => String(r.row_id)))

  // A cursor is opaque and server-signed, not an offset.
  const cursor = (await page.getByTestId('next-cursor').textContent())?.trim() ?? ''
  expect(cursor.length).toBeGreaterThan(20)
  expect(cursor).not.toContain('null')

  const seen: string[] = []
  let pages = 0
  for (let guard = 0; guard < 12; guard++) {
    seen.push(...(await columnValues(page, 'row_id')))
    pages++
    const next = page.getByTestId('pager-next')
    if (await next.isDisabled()) break
    // Wait on the first CELL changing: a whole row never equals one id, so
    // comparing against that passes instantly and races ahead of the fetch.
    const before = (await columnValues(page, 'row_id'))[0]
    await next.click()
    await expect.poll(async () => (await columnValues(page, 'row_id'))[0]).not.toBe(before)
  }

  expect(pages).toBe(Math.ceil(rows.length / pageSize))
  // Completeness AND uniqueness in one assertion, with the counts in the message.
  expect(new Set(seen).size, `saw ${seen.length} rows, ${new Set(seen).size} distinct`).toBe(
    rows.length,
  )
  expect([...seen].map(Number).sort((a, b) => a - b)).toEqual(rows.map((r) => r.row_id))

  // The last page says so rather than offering a cursor it does not have. Note
  // the final page is SHORT (120 is not a multiple of 25), so the range's first
  // ordinal comes from the page count, not from the row total.
  await expect(page.getByTestId('next-cursor')).toHaveText('null — last page')
  await expect(page.getByTestId('pager-range')).toContainText(
    `${(pages - 1) * pageSize + 1}–${rows.length} of ${rows.length}`,
  )
  expect(await columnValues(page, 'row_id')).toHaveLength(rows.length % pageSize)

  // NO numbered pager: it is unbuildable on a cursor API, and the page says so
  // instead of drawing one. Page-size and direction segments are `role=radio`,
  // so a numerically-named BUTTON could only be a page jump.
  expect(await page.getByRole('button', { name: /^\s*\d+\s*$/ }).count()).toBe(0)
  await expect(page.locator('.line-through', { hasText: '2,329' })).toBeVisible()
  await expect(page.getByText('no page jumps').first()).toBeVisible()

  // Backwards returns to the exact first page.
  const lastPage = await columnValues(page, 'row_id')
  await page.getByTestId('pager-prev').click()
  await expect.poll(async () => columnValues(page, 'row_id')).not.toEqual(lastPage)
  for (let i = 0; i < 6 && (await page.getByTestId('pager-prev').isEnabled()); i++) {
    await page.getByTestId('pager-prev').click()
  }
  await expect(page.getByTestId('pager-prev')).toBeDisabled()
  await expect.poll(async () => columnValues(page, 'row_id')).toEqual(seen.slice(0, pageSize))
})

test('changing the spec while paged past page 1 is caught before the cursor is refused', async ({
  page,
  h,
}) => {
  const rows = Array.from({ length: 120 }, (_, i) => ({ row_id: i + 1, grp: i % 3 }))
  const ds = await h.seed(rows, 'conflict')

  await goto(page, `/query?dataset=${ds.id}`)
  await page.getByTestId('page-size-25').click()
  await expect(page.locator('tbody tr')).toHaveCount(25)

  const pageOne = await columnValues(page, 'row_id')
  await page.getByTestId('pager-next').click()
  await expect(page.getByTestId('pager-range')).toContainText('26–50')
  // The range is drawn from `pageIndex * limit` the instant the cursor is
  // pushed, so it turns over BEFORE the rows do. Wait for the rows themselves,
  // or "page two" is captured as a copy of page one.
  await expect.poll(async () => columnValues(page, 'row_id')).not.toEqual(pageOne)
  const pageTwo = await columnValues(page, 'row_id')

  // A cursor is signed against (version, spec hash), so replaying it against a
  // changed spec is `400 invalid-cursor`. The page must not let that happen.
  await page.getByTestId('sort-add').selectOption('grp')
  await runQuery(page)

  const conflict = page.getByTestId('cursor-conflict')
  await expect(conflict).toBeVisible()
  await expect(conflict).toContainText('400 invalid-cursor')
  await expect(conflict).toContainText('sort')
  await expect(page.getByTestId('pager-range')).toContainText('1–25')

  // Undo restores both the spec AND the place in the walk.
  await page.getByTestId('conflict-undo').click()
  await expect(conflict).toHaveCount(0)
  await expect(page.getByTestId('pager-range')).toContainText('26–50')
  await expect.poll(async () => columnValues(page, 'row_id')).toEqual(pageTwo)

  // Page size is NOT part of the cursor's hash, so changing it restarts the
  // walk without a conflict — claiming otherwise would refuse what the API
  // accepts.
  await page.getByTestId('pager-next').click()
  await expect(page.getByTestId('pager-range')).toContainText('51–75')
  await page.getByTestId('page-size-50').click()
  await expect(page.getByTestId('cursor-conflict')).toHaveCount(0)
  await expect(page.getByTestId('pager-range')).toContainText('1–50')
})

/* ------------------------------------------------------------------ masking */

test('a masked column cannot be filtered, sorted or searched — refused in the UI, not on click', async ({
  page,
  h,
}) => {
  const SENTINEL = 'SECRET-QUERYCHECK'
  const rows = fixtureRows(20).map((r) => ({ ...r, ssn: `${SENTINEL}-${r.row_id}` }))
  const ds = await h.seed(rows, 'masked')
  await h.markSensitive(ds.id, 'ssn')

  const viewer = await h.viewer()
  await h.asSeat(page, viewer.user_id, 'Viewer')
  await goto(page, `/query?dataset=${ds.id}`)

  // Masked, not refused: the rows still arrive.
  await expect(page.locator('tbody tr')).toHaveCount(rows.length)
  expect(await page.locator('body').innerText()).not.toContain(SENTINEL)
  expect(await page.content()).not.toContain(SENTINEL)

  // It is absent from the sort picker...
  const sortable = await page
    .getByTestId('sort-add')
    .locator('option')
    .evaluateAll((els) => els.map((e) => (e as HTMLOptionElement).value).filter(Boolean))
  expect(sortable).not.toContain('ssn')
  expect(sortable).toContain('status')

  // ...and from the filter tree's column picker.
  await page.getByTestId('add-filter').click()
  const filterable = await page
    .getByTestId('condition-row')
    .first()
    .getByTestId('condition-column')
    .locator('option')
    .evaluateAll((els) => els.map((e) => (e as HTMLOptionElement).value))
  expect(filterable).not.toContain('ssn')
  expect(filterable).toContain('status')

  // ...and the reason is stated where someone would go looking for the column.
  await expect(page.getByTestId('sort-refusal')).toContainText('400 sensitive-column-not-filterable')
  await expect(page.getByTestId('sort-refusal')).toContainText('ssn')

  // Search compiles to icontains over EVERY text column, masked ones included,
  // so it is disabled outright rather than refused after you type.
  await expect(page.getByTestId('search-input')).toBeDisabled()
  await expect(page.getByTestId('search-refusal')).toContainText(
    '400 sensitive-column-not-filterable',
  )

  // Projection stays allowed — the values come back masked, which is the whole
  // distinction between "may not be computed over" and "may not be seen".
  expect(await gridHeaders(page)).toContain('ssn')
  await expect(page.getByTestId('projection-list')).toContainText('masked')
  await expect(page.getByText('ssn masked for this seat').first()).toBeVisible()

  // The refusal the UI is modelling is real: prove it at the API, same seat.
  const refusedFilter = await api('POST', queryPath(ds.id), {
    body: { limit: 1, filters: { logic: 'and', conditions: [{ column: 'ssn', op: 'is_null' }] } },
    userId: viewer.user_id,
  })
  expect(refusedFilter.status).toBe(400)
  expect((refusedFilter.body as { code?: string }).code).toBe('sensitive-column-not-filterable')

  const refusedSort = await api('POST', queryPath(ds.id), {
    body: { limit: 1, sort: [{ column: 'ssn', direction: 'asc' }] },
    userId: viewer.user_id,
  })
  expect(refusedSort.status).toBe(400)
  expect((refusedSort.body as { code?: string }).code).toBe('sensitive-column-not-filterable')
})

/* ----------------------------------------------------------------- download */

test('the download panel states what the file will contain before any click', async ({
  page,
  h,
}) => {
  const rows = fixtureRows()
  const allColumns = Object.keys(rows[0])
  const ds = await h.seed(rows, 'dl')

  await goto(page, `/query?dataset=${ds.id}`)
  const panel = page.getByTestId('download-panel')
  await expect(panel).toBeVisible()

  // The route it will call, named before it is called.
  await expect(panel).toContainText(`/datasets/${ds.id}/download`)

  // The manifest is the pre-click promise: columns, then rows.
  const stats = page.getByTestId('download-manifest').locator('[data-slot="stat"]')
  await expect(stats.first()).toContainText(String(allColumns.length))
  await expect(stats.nth(1)).toContainText(String(rows.length))

  // Narrow the projection and the promise narrows with it, carrying the
  // denominator it is now short of.
  await page.getByTestId('projection-region').click()
  await expect(stats.first()).toContainText(`${allColumns.length - 1} columns`)

  const params = () => numberIn(panel, /(\d+) params/)
  const baseline = await params()
  expect(baseline).not.toBeNull()

  // A row limit is a partial file, and says so.
  await page.getByTestId('download-limit').fill('10')
  await expect(stats.nth(1)).toContainText('10 rows')
  await expect.poll(params).toBe((baseline ?? 0) + 1)

  // A non-integer limit is not sent at all, and the panel says that too.
  await page.getByTestId('download-limit').fill('ten')
  await expect(panel).toContainText('whole rows only')
  await expect.poll(params).toBe(baseline)
  await page.getByTestId('download-limit').fill('')

  // filter_expr is a different language from the filter tree, and the panel
  // refuses to imply that the file will match the grid.
  await expect(panel).toContainText('The filter tree, the search box and the sort stack are not sent')
  await page.getByTestId('download-filter').fill("status = 'active'")
  await expect.poll(params).toBe((baseline ?? 0) + 1)
  await expect(page.getByTestId('download-manifest')).toContainText(
    'not known until the file arrives',
  )

  // Nothing is masked for this seat, so the gate is not in the way — stated
  // positively rather than left to be inferred from an absent warning.
  await expect(page.getByTestId('download-gate')).toContainText('Nothing is masked for this seat')
  await expect(page.getByTestId('download-gate')).toHaveAttribute('data-tone', 'neutral')
})

test('for a seat that reads a column masked, the panel says the whole download is refused — and it is', async ({
  page,
  h,
}) => {
  const rows = fixtureRows(10).map((r) => ({ ...r, ssn: `x-${r.row_id}` }))
  const ds = await h.seed(rows, 'dlgate')
  await h.markSensitive(ds.id, 'ssn')

  const viewer = await h.viewer()
  await h.asSeat(page, viewer.user_id, 'Viewer')
  await goto(page, `/query?dataset=${ds.id}`)

  const gate = page.getByTestId('download-gate')
  await expect(gate).toHaveAttribute('data-tone', 'warning')
  await expect(gate).toContainText('ssn')
  await expect(gate).toContainText('ensure_raw_access')
  // The specific trap: dropping the column from `columns` does not unlock it.
  await expect(gate).toContainText('the gate is on the dataset, not the column')

  // Now prove the prediction. `ensure_raw_access` answers 403 rather than
  // writing a partly-masked file, and Chromium logs every failed response as a
  // console error — here that refusal IS the expected behaviour.
  h.allowError(/403 \(Forbidden\)/)

  await page.getByTestId('download-run').click()
  await expect(page.getByTestId('download-restricted')).toBeVisible()
  await expect(page.getByTestId('download-restricted')).toContainText('Restricted for this seat')
  await expect(page.getByTestId('download-result')).toHaveCount(0)

  // And the refusal is the service's, not a client-side guess.
  const raw = await fetch(`${API_BASE}/datasets/${ds.id}/download?format=csv&sheet=data`, {
    headers: { 'X-User-Id': viewer.user_id },
  })
  expect(raw.status).toBe(403)
  expect(((await raw.json()) as { code?: string }).code).toBe('sensitive-data-restricted')
})

test('pinning an older version switches the download to the route that cannot take a subset', async ({
  page,
  h,
}) => {
  const ds = await h.seed(fixtureRows(8), 'dlpin')
  await h.addVersion(ds.id, fixtureRows(12))

  await goto(page, `/query?dataset=${ds.id}`)
  const panel = page.getByTestId('download-panel')
  // Newest version wins by default, and that is the route with all the knobs.
  await expect(panel).toContainText(`/datasets/${ds.id}/download`)
  await expect(page.getByTestId('download-limit')).toBeEnabled()

  await page.getByTestId('version-select').selectOption('1')

  await expect(panel).toContainText(`/datasets/${ds.id}/versions/1/download`)
  await expect(panel).toContainText('is not the current version')
  // Not merely ignored — not offered.
  await expect(page.getByTestId('download-limit')).toBeDisabled()
  await expect(page.getByTestId('download-filter')).toBeDisabled()
  await expect(panel).toContainText('columns omitted — every column')
})

/* ------------------------------------------------------------- save as view */

test('save as view persists the whole spec — verified by reading it back', async ({ page, h }) => {
  const rows = fixtureRows()
  const ds = await h.seed(rows, 'view')
  await goto(page, `/query?dataset=${ds.id}`)

  await addRootCondition(page, 'status', 'eq', 'active')
  await page.getByTestId('sort-add').selectOption('score')
  await page.getByTestId('sort-dir-0-desc').click()
  await page.getByTestId('projection-signup_date').click()
  await runQuery(page)

  const draft = JSON.parse((await page.getByTestId('compiled-spec').textContent()) ?? '{}')
  const name = `uitest view ${Date.now().toString(36)}`

  interface SavedView {
    name: string
    sheet_name: string | null
    version_selector: { mode: string; version_number?: number | null }
    query: Record<string, unknown>
  }
  const views = async (): Promise<SavedView[]> =>
    (await apiOk<{ items: SavedView[] }>('GET', `/datasets/${ds.id}/views`)).items

  await expect(page.getByTestId('save-view')).toBeDisabled() // no name yet
  await page.getByTestId('view-name').fill(name)
  await expect(page.getByTestId('save-view')).toBeEnabled()
  await page.getByTestId('save-view').click()

  // A toast is not evidence. Read it back.
  await expect.poll(async () => (await views()).some((v) => v.name === name)).toBe(true)
  const saved = (await views()).find((v) => v.name === name)!

  expect(saved.sheet_name).toBe('data')
  expect(saved.query.filters).toEqual(draft.filters)
  expect(saved.query.sort).toEqual(draft.sort)
  expect(saved.query.columns).toEqual(draft.columns)
  // A view stores the spec and NEVER a cursor: every open starts at page 1.
  expect(saved.query.cursor ?? null).toBeNull()
  // "latest" means it re-runs against whichever version is current.
  expect(saved.version_selector.mode).toBe('current')

  // And the saved spec really answers the question it claims to.
  const viaApi = await apiOk<{ total: number }>('POST', queryPath(ds.id), { body: saved.query })
  expect(viaApi.total).toBe(rows.filter((r) => r.status === 'active').length)

  // The pinned variant freezes the version as well as the spec.
  const pinnedName = `${name} pinned`
  await page.getByTestId('view-version-pinned').click()
  await page.getByTestId('view-name').fill(pinnedName)
  await page.getByTestId('save-view').click()

  await expect
    .poll(async () => {
      const v = (await views()).find((x) => x.name === pinnedName)
      return v ? [v.version_selector.mode, v.version_selector.version_number] : null
    })
    .toEqual(['version', 1])
})

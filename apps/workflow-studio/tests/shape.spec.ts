import { test, expect, goto } from './fixtures'

/**
 * SHAPE RULES, in a real browser.
 *
 * `npm run check:rules` proves the pure functions in `instrument/shape.ts`
 * behave. This proves the surfaces actually ADAPT — that the decisions reach
 * the DOM instead of being computed and discarded.
 *
 * The shapes here mirror `design-prototypes/terminal-shapes.html`: S1 wide
 * all-numeric, S4 hostile names, S6 mixed types.
 */

/** S1 — wide all-numeric. 35 columns is just past the 30-column threshold. */
const wideRows = (n: number) =>
  Array.from({ length: n }, (_, r) => {
    const row: Record<string, unknown> = { instrument_id: `RT-${40118822 + r}` }
    for (let c = 1; c <= 12; c++) row[`dv01_${c}m`] = (r + c) * 1.5
    for (let c = 1; c <= 12; c++) row[`cs01_${c}m`] = (r + c) * 2.5
    for (let c = 1; c <= 10; c++) row[`vega_${c}m`] = (r + c) * 0.5
    return row
  })

test('a wide table turns the rail into a column manager and says so', async ({ page, h }) => {
  const ds = await h.seed(wideRows(6), 'wide')
  await goto(page, `/data?dataset=${ds.id}`)

  // 35 columns > 30, so R1 fires: the rail defaults to the column manager
  // rather than a flat list nothing can navigate.
  await expect(page.getByTestId('rail-mode-columns')).toHaveAttribute('aria-selected', 'true')
  await expect(page.getByTestId('column-filter')).toBeVisible()

  // R1 also folds by longest common prefix — dv01_, cs01_, vega_.
  const groups = page.getByTestId('column-group')
  await expect(groups).toHaveCount(await groups.count())
  const groupLabels = await groups.evaluateAll((els) =>
    els.map((e) => e.textContent?.trim() ?? ''),
  )
  expect(groupLabels.some((l) => l.startsWith('dv01_'))).toBe(true)
  expect(groupLabels.some((l) => l.startsWith('cs01_'))).toBe(true)

  // R2 — the grid states what you are looking at instead of leaving you to
  // count columns.
  await expect(page.getByTestId('grid-ruler')).toContainText('35 columns')

  // The rail must remain switchable — the default is a default, not a cage.
  await page.getByTestId('rail-mode-datasets').click()
  await expect(page.getByTestId('rail-mode-datasets')).toHaveAttribute('aria-selected', 'true')
})

test('a narrow table leaves the rail alone', async ({ page, h }) => {
  const ds = await h.seed([{ id: 1, city: 'Oslo' }], 'narrow')
  await goto(page, `/data?dataset=${ds.id}`)

  // 2 columns: R1 must NOT fire. No mode switcher, no column manager.
  await expect(page.getByTestId('rail-mode-columns')).toHaveCount(0)
  await expect(page.getByTestId('column-filter')).toHaveCount(0)
})

test('the grid numbers rows without stealing the first data column', async ({ page, h }) => {
  const ds = await h.seed(
    Array.from({ length: 5 }, (_, i) => ({ id: i + 1, city: `city-${i + 1}` })),
    'gutter',
  )
  await goto(page, `/data?dataset=${ds.id}`)

  // R10 — the ordinal gutter is chrome. It must not become column one, or every
  // "the first column is X" reading in the product shifts by one.
  const first = page.getByTestId('first-cell').first()
  await expect(first).toHaveText('1')

  const headers = await page
    .locator('thead th[data-column]')
    .evaluateAll((els) => els.map((e) => e.textContent?.trim() ?? ''))
  expect(headers).toEqual(['id', 'city'])

  // The gutter itself is present and counts from 1 on the first page.
  const ordinals = await page
    .locator('tbody td[data-slot="row-gutter"]')
    .evaluateAll((els) => els.map((e) => e.textContent?.trim() ?? ''))
  expect(ordinals).toEqual(['1', '2', '3', '4', '5'])
})

test('a long value truncates from the middle, keeping both ends', async ({ page, h }) => {
  // S4 — hostile values. A 64-char hash ellipsised to its first N characters
  // makes every row read alike; the tail is what tells two rows apart.
  const head = 'HEADSTART'
  const tail = 'TAILEND'
  const long = `${head}${'x'.repeat(80)}${tail}`
  const ds = await h.seed([{ id: 1, blob: long }], 'trunc')
  await goto(page, `/data?dataset=${ds.id}`)

  const cell = page.getByTestId('cell').first()
  const shown = (await cell.textContent())?.trim() ?? ''

  expect(shown).not.toBe(long)
  expect(shown).toContain('…')
  expect(shown.startsWith(head)).toBe(true)
  expect(shown.endsWith(tail)).toBe(true)

  // The full value stays reachable — truncation must not destroy it.
  await expect(cell).toHaveAttribute('title', long)
})

test('row height does not vary with content', async ({ page, h }) => {
  // R10 — "row height never varies with content". A 1,400-char cell must not
  // make one row forty times taller than its neighbours.
  const ds = await h.seed(
    [
      { id: 1, note: 'short' },
      { id: 2, note: 'x'.repeat(1400) },
      { id: 3, note: 'also short' },
    ],
    'rowheight',
  )
  await goto(page, `/data?dataset=${ds.id}`)

  const heights = await page
    .locator('tbody tr')
    .evaluateAll((els) => els.map((e) => Math.round(e.getBoundingClientRect().height)))

  expect(heights).toHaveLength(3)
  expect(new Set(heights).size, `row heights varied: ${heights.join(', ')}`).toBe(1)
})

test('a 100%-distinct column gets an identity panel, and says the chart was suppressed', async ({
  page,
  h,
}) => {
  // S2 — all-unique. `uid` is distinct on every row, so a top-values chart is
  // 12 bars each one row tall: it looks like data and carries none. The
  // reference drew NO card at all, which reads as "this column is fine".
  const ds = await h.seed(
    Array.from({ length: 12 }, (_, i) => ({ uid: `UID-${1000 + i}`, tier: ['a', 'b'][i % 2] })),
    'identity',
  )
  await goto(page, `/data?dataset=${ds.id}`)
  await page.getByTestId('lens-analytics').click()

  // Only the uid card mentions "uid" — the other column is `tier`.
  const uidCard = page.getByTestId('profile-column').filter({ hasText: 'uid' })

  await expect(uidCard.getByTestId('profile-identity-panel')).toBeVisible({ timeout: 20_000 })
  await expect(uidCard).toContainText('Distribution suppressed, not omitted')
  // The useless chart must be gone, not merely pushed below the fold.
  await expect(uidCard.getByTestId('profile-top-values')).toHaveCount(0)

  // And a genuinely low-cardinality column in the SAME profile still gets one,
  // so this is a rule firing on shape rather than the chart being removed.
  const tierCard = page.getByTestId('profile-column').filter({ hasText: 'tier' })
  await expect(tierCard.getByTestId('profile-top-values')).toBeVisible()
  await expect(tierCard.getByTestId('profile-identity-panel')).toHaveCount(0)
})

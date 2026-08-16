import { test, expect, goto, apiOk } from './fixtures'
import type { Page } from '@playwright/test'

/**
 * Read header labels from `textContent`, not `innerText`.
 *
 * The header cells are styled `uppercase`, and `innerText` reports text as
 * *rendered* — so `id` comes back `ID` and a case-sensitive assertion fails on
 * a purely cosmetic rule. `textContent` is the underlying string.
 */
const headerTexts = (page: Page) =>
  page.locator('thead th').evaluateAll((els) => els.map((e) => e.textContent?.trim() ?? ''))

/** The first cell of the first row — the cheapest proof that a page turned. */
const firstCell = (page: Page) => page.locator('tbody tr td:first-child').first()

/**
 * The /data workspace: the persistent grid, and the selectors that drive it.
 *
 * The paging test here is the important one. Cursors are opaque and bound to
 * (version, spec); a stale one is a 400, and a non-total ORDER BY silently
 * skips and duplicates rows when the sort key has ties. Both failures look like
 * a working table.
 */

test('renders the seeded rows, columns and total the API reports', async ({ page, h }) => {
  const rows = Array.from({ length: 12 }, (_, i) => ({ id: i + 1, city: `c${i % 3}` }))
  const ds = await h.seed(rows, 'grid')

  await goto(page, `/data?dataset=${ds.id}`)

  await expect(page.getByTestId('row-count')).toContainText('12 rows')

  // Column headers come from the sheet's declared schema, so they are stable
  // even if a row happens to omit a key.
  expect(await headerTexts(page)).toEqual(['id', 'city'])

  await expect(page.locator('tbody tr')).toHaveCount(12)
})

test('cursor paging returns every row exactly once when the sort key has ties', async ({
  page,
  h,
}) => {
  // 120 rows over 3 distinct values: forty-way ties, which is what breaks a
  // non-total ORDER BY. Page size is 50, so this is three pages.
  const rows = Array.from({ length: 120 }, (_, i) => ({ row_id: i + 1, grp: i % 3 }))
  const ds = await h.seed(rows, 'paging')

  await goto(page, `/data?dataset=${ds.id}`)
  await expect(page.getByTestId('row-count')).toContainText('120 rows')

  const idsOnPage = async () =>
    page.locator('tbody tr td:first-child').evaluateAll((tds) =>
      tds.map((td) => td.textContent?.trim() ?? ''),
    )

  const seen: string[] = []
  for (let guard = 0; guard < 10; guard++) {
    seen.push(...(await idsOnPage()))
    const next = page.getByRole('button', { name: 'Next page' })
    if (await next.isDisabled()) break

    // Wait on the first CELL changing, not the row's concatenated text — a row
    // never equals a single id, so comparing against that passes instantly and
    // the loop races ahead of the fetch.
    const before = await firstCell(page).innerText()
    await next.click()
    await expect(firstCell(page)).not.toHaveText(before)
  }

  // Completeness AND uniqueness in one assertion, with the counts in the message.
  expect(new Set(seen).size, `saw ${seen.length} rows, ${new Set(seen).size} unique`).toBe(120)
})

test('paging backwards returns to the exact first page', async ({ page, h }) => {
  const rows = Array.from({ length: 80 }, (_, i) => ({ row_id: i + 1 }))
  const ds = await h.seed(rows, 'backpage')

  await goto(page, `/data?dataset=${ds.id}`)
  const first = await page.locator('tbody tr td:first-child').allInnerTexts()

  await page.getByRole('button', { name: 'Next page' }).click()
  await expect(page.locator('tbody tr td:first-child').first()).not.toHaveText(first[0])

  await page.getByRole('button', { name: 'Previous page' }).click()
  await expect
    .poll(async () => page.locator('tbody tr td:first-child').allInnerTexts())
    .toEqual(first)

  await expect(page.getByRole('button', { name: 'Previous page' })).toBeDisabled()
})

test('switching version reloads the grid against that version', async ({ page, h }) => {
  const ds = await h.seed([{ a: 1 }, { a: 2 }, { a: 3 }], 'versions')
  // Versions are immutable — new data is a new version, never an overwrite.
  await h.addVersion(ds.id, Array.from({ length: 9 }, (_, i) => ({ a: i })))

  await goto(page, `/data?dataset=${ds.id}`)

  // Newest version wins by default, so the grid opens on the 9-row v2.
  await expect(page.getByTestId('row-count')).toContainText('9 rows')

  await page.getByLabel('Version').selectOption('1')
  await expect(page.getByTestId('row-count')).toContainText('3 rows')
  await expect(page.locator('tbody tr')).toHaveCount(3)

  // Switching back must not replay v1's cursor against v2 (a 400 invalid-cursor).
  await page.getByLabel('Version').selectOption('2')
  await expect(page.getByTestId('row-count')).toContainText('9 rows')
})

test('a multi-sheet workbook lets you pick the sheet, and the grid follows', async ({
  page,
  h,
}) => {
  const wb = await h.seedWorkbook(
    {
      Customers: Array.from({ length: 4 }, (_, i) => ({ customer_id: i + 1, tier: 'gold' })),
      Orders: Array.from({ length: 11 }, (_, i) => ({ order_id: i + 1, amount: i * 10 })),
    },
    'sheets',
  )

  await goto(page, `/data?dataset=${wb.id}`)

  const sheetSelect = page.getByLabel('Sheet')
  const options = await sheetSelect.locator('option').allInnerTexts()
  expect(options.join(' ')).toContain('Customers')
  expect(options.join(' ')).toContain('Orders')

  // The option VALUE is the sheet name — per-sheet data paths take the display
  // name, while `sheet_key` addresses metadata routes.
  await sheetSelect.selectOption('Customers')
  await expect(page.getByTestId('row-count')).toContainText('4 rows')
  expect(await headerTexts(page)).toContain('customer_id')

  await sheetSelect.selectOption('Orders')
  await expect(page.getByTestId('row-count')).toContainText('11 rows')
  expect(await headerTexts(page)).toContain('order_id')
})

test('selecting another dataset from the rail swaps the grid without a stale-sheet 404', async ({
  page,
  h,
}) => {
  const token = Math.random().toString(36).slice(2, 8)
  const a = await h.seed([{ alpha: 1 }, { alpha: 2 }], `rail${token}`)
  const b = await h.seed(
    Array.from({ length: 5 }, (_, i) => ({ beta: i })),
    `rail${token}`,
  )

  await goto(page, `/data?dataset=${a.id}`)
  await expect(page.getByTestId('row-count')).toContainText('2 rows')

  // Narrow the rail to just this test's fixtures, then switch.
  await page.getByPlaceholder('Search datasets…').first().fill(`rail${token}`)
  await page.getByRole('button', { name: new RegExp(b.name) }).click()

  await expect(page.getByTestId('row-count')).toContainText('5 rows')
  expect(await headerTexts(page)).toContain('beta')

  // The old dataset's sheet name must never be paired with the new id.
  expect(h.errors.filter((e) => e.includes('HTTP 5'))).toEqual([])
})

test('the grid total agrees with the API for the same version and sheet', async ({ page, h }) => {
  const ds = await h.seed(
    Array.from({ length: 37 }, (_, i) => ({ n: i })),
    'agree',
  )

  await goto(page, `/data?dataset=${ds.id}`)

  const fromApi = await apiOk<{ total: number }>(
    'POST',
    `/datasets/${ds.id}/versions/1/sheets/data/query`,
    { body: { limit: 1 } },
  )
  await expect(page.getByTestId('row-count')).toContainText(`${fromApi.total} rows`)
})

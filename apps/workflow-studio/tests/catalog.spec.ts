import { test, expect, goto, apiOk } from './fixtures'

/**
 * The catalog: the browse surface.
 *
 * Every assertion here is computed either from a fresh API read or from the DOM
 * before and after the interaction — never from a constant.
 */

test('lists a seeded dataset with the row count the API reports', async ({ page, h }) => {
  const ds = await h.seed(
    Array.from({ length: 7 }, (_, i) => ({ id: i + 1, label: `row-${i}` })),
    'catalog',
  )

  await goto(page, '/catalog')
  await page.getByTestId('catalog-search').fill(ds.name)

  const row = page.getByRole('row').filter({ hasText: ds.name })
  await expect(row).toHaveCount(1)

  // The truth is what the API says, not what we seeded — if ingestion dropped a
  // row, this test should fail rather than agree with our own assumption.
  const fromApi = await apiOk<{ items: any[] }>('GET', `/datasets?q=${encodeURIComponent(ds.name)}`)
  const expected = fromApi.items.find((d) => d.id === ds.id)
  expect(expected, 'seeded dataset missing from the API').toBeTruthy()
  await expect(row).toContainText(String(expected.row_count))
})

test('search narrows the table, and clearing it widens it again', async ({ page, h }) => {
  // Two fixtures sharing a token, so every count below is scoped to this test.
  // An absolute total would be racy: other workers create and delete datasets
  // concurrently, and a test that measures global state measures their noise.
  const token = Math.random().toString(36).slice(2, 8)
  const a = await h.seed([{ a: 1 }], `search${token}`)
  await h.seed([{ a: 1 }], `search${token}`)

  await goto(page, '/catalog')

  const total = async () =>
    Number((await page.getByTestId('catalog-range').innerText()).match(/of (\d+)/)?.[1] ?? 0)

  await page.getByTestId('catalog-search').fill(`search${token}`)
  await expect(page.getByRole('row')).toHaveCount(3) // 2 rows + header
  const both = await total()
  expect(both).toBe(2)

  // Narrow to one of the two by its full unique name.
  await page.getByTestId('catalog-search').fill(a.name)
  await expect(page.getByRole('row')).toHaveCount(2)
  expect(await total()).toBe(1)

  // Both ends measured: the filter must be reversible, not just applicable.
  await page.getByTestId('catalog-search').fill(`search${token}`)
  await expect(page.getByRole('row')).toHaveCount(3)
  expect(await total()).toBe(both)
})

test('sorting by rows orders the whole catalog, not just one page', async ({ page, h }) => {
  const token = Math.random().toString(36).slice(2, 8)
  const sizes = [3, 17, 8]
  for (const n of sizes) {
    await h.seed(
      Array.from({ length: n }, (_, i) => ({ i })),
      `sort${token}`,
    )
  }

  await goto(page, '/catalog')
  await page.getByTestId('catalog-search').fill(`sort${token}`)
  await expect(page.getByRole('row')).toHaveCount(sizes.length + 1) // + header

  await page.getByTestId('catalog-sort-row_count').click()

  const readRowCounts = async () =>
    page.locator('tbody tr').evaluateAll((trs) =>
      trs.map((tr) => Number(tr.querySelectorAll('td')[4]?.textContent?.replace(/,/g, '') ?? 0)),
    )

  const asc = await readRowCounts()
  expect(asc).toEqual([...sizes].sort((a, b) => a - b))

  // Clicking the same key again flips direction rather than re-sorting ascending.
  await page.getByTestId('catalog-sort-row_count').click()
  expect(await readRowCounts()).toEqual([...sizes].sort((a, b) => b - a))
})

test('paging returns every dataset exactly once across pages', async ({ page, h }) => {
  // One more than a page, so there is a genuine second page to get wrong.
  const token = Math.random().toString(36).slice(2, 8)
  const seeded: string[] = []
  for (let i = 0; i < 26; i++) {
    const ds = await h.seed([{ n: i }], `page${token}`)
    seeded.push(ds.name)
  }

  await goto(page, '/catalog')
  await page.getByTestId('catalog-search').fill(`page${token}`)
  await expect(page.getByTestId('catalog-range')).toContainText('of 26')

  const namesOn = async () =>
    page.locator('tbody tr td:first-child').evaluateAll((tds) =>
      tds.map((td) => td.textContent?.trim() ?? ''),
    )

  const first = await namesOn()
  expect(first).toHaveLength(25)
  await expect(page.getByTestId('catalog-range')).toContainText('1–25 of 26')

  await page.getByTestId('catalog-next').click()
  const second = await namesOn()
  expect(second).toHaveLength(1)
  await expect(page.getByTestId('catalog-range')).toContainText('26–26 of 26')

  // Completeness and uniqueness in one check — the shape a paging bug fails.
  const seen = [...first, ...second]
  expect(new Set(seen).size).toBe(26)
  expect(new Set(seen)).toEqual(new Set(seeded))

  await expect(page.getByTestId('catalog-next')).toBeDisabled()
  await page.getByTestId('catalog-prev').click()
  await expect(page.getByTestId('catalog-range')).toContainText('1–25 of 26')
})

test('opening a dataset routes client-side and lands on that dataset', async ({ page, h }) => {
  const ds = await h.seed([{ a: 1 }, { a: 2 }], 'open')

  await goto(page, '/catalog')
  await page.getByTestId('catalog-search').fill(ds.name)

  // A full document reload wipes this; client-side routing preserves it.
  await page.evaluate(() => ((window as any).__spa = true))
  await page.getByRole('row').filter({ hasText: ds.name }).click()

  await expect(page).toHaveURL(new RegExp(`/data\\?dataset=${ds.id}`))
  expect(await page.evaluate(() => (window as any).__spa)).toBe(true)

  // And it actually opened THAT dataset, not merely the /data route.
  await expect(page.getByTestId('lens-body')).toContainText(ds.name)
})

test('the documentation filter is applied by the server, not faked locally', async ({ page, h }) => {
  const ds = await h.seed([{ a: 1 }], 'docfilter')

  await goto(page, '/catalog')
  await page.getByTestId('catalog-search').fill(ds.name)
  await expect(page.getByRole('row').filter({ hasText: ds.name })).toHaveCount(1)

  // A freshly uploaded dataset has no column documentation.
  await page.getByTestId('catalog-doc-filter').selectOption('full')
  await expect(page.getByRole('row').filter({ hasText: ds.name })).toHaveCount(0)

  await page.getByTestId('catalog-doc-filter').selectOption('none')
  await expect(page.getByRole('row').filter({ hasText: ds.name })).toHaveCount(1)

  // Cross-check the UI against the API's own answer for the same filter.
  const viaApi = await apiOk<{ items: any[] }>(
    'GET',
    `/datasets?q=${encodeURIComponent(ds.name)}&documentation=none`,
  )
  expect(viaApi.items.some((d) => d.id === ds.id)).toBe(true)
})

test('an unknown dataset id says so instead of silently showing another one', async ({
  page,
  h,
}) => {
  const real = await h.seed([{ a: 1 }], 'ghost')

  // Asking the API for a dataset that isn't there is the point of this test, so
  // the 404 is expected. A 5xx never is.
  h.allowError(/404 \(Not Found\)/)

  const ghost = '11111111-2222-3333-4444-555555555555'
  await goto(page, `/data?dataset=${ghost}`)

  // Silently falling back to a different dataset is the failure mode this
  // guards: you would be reading someone else's rows believing they were the
  // ones you linked to.
  await expect(page.getByTestId('data-error')).toContainText('not available to this seat')

  // The rail still lists real datasets — that is its job. What must NOT happen
  // is one of them being loaded as though it were the id that was asked for:
  // no rows, and no dataset detail in the lens.
  await expect(page.locator('tbody tr')).toHaveCount(0)
  await expect(page.getByTestId('lens-body')).not.toContainText(real.name)
  await expect(page.locator('body')).not.toContainText(ghost)
  expect(h.errors.filter((e) => e.includes('HTTP 5'))).toEqual([])
})

test('searching the rail filters the list without changing the dataset on screen', async ({
  page,
  h,
}) => {
  const token = Math.random().toString(36).slice(2, 8)
  const viewing = await h.seed([{ alpha: 1 }, { alpha: 2 }], `stick${token}`)
  const other = await h.seed(
    Array.from({ length: 6 }, (_, i) => ({ beta: i })),
    `stick${token}`,
  )

  await goto(page, `/data?dataset=${viewing.id}`)
  await expect(page.getByTestId('row-count')).toContainText('2 rows')

  // Type a search that EXCLUDES the dataset being viewed. The rail must narrow;
  // the grid must not move. This used to swap the grid to whatever matched
  // first, so the header named one dataset while the rows came from another.
  await page.getByTestId('rail-search').fill(other.name)
  await expect(page.getByRole('button', { name: new RegExp(other.name) })).toHaveCount(1)
  await expect(page.getByRole('button', { name: new RegExp(viewing.name) })).toHaveCount(0)

  await expect(page.getByTestId('row-count')).toContainText('2 rows')
  await expect(page.getByTestId('lens-body')).toContainText(viewing.name)
})

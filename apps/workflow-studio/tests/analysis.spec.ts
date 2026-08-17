import { test, expect, goto, apiOk } from './fixtures'
import type { Locator, Page } from '@playwright/test'

/**
 * The three analysis routes: `/aggregate`, `/pivot` (+ the SQL console) and
 * `/column`.
 *
 * What separates these screens from `/data` is that NOTHING ON THEM IS A STORED
 * VALUE. Every figure is computed, and a computed figure without its denominator
 * is the silent-wrong-answer class the whole INSTRUMENT layer exists to stop —
 * `sum` over a column that is 60% parseable prints a confident total for 60% of
 * the rows and looks exactly like a right answer.
 *
 * So every fixture here is seeded with group sizes and sums the TEST knows
 * arithmetically, and each assertion is made against that arithmetic or against
 * a fresh read of a *different* endpoint. Reading the number back off the same
 * screen that produced it would prove only that the DOM is self-consistent.
 */

/* ------------------------------------------------------------------ helpers */

/** A `<Stat>` row addressed by its identifier, exactly — `null` is not `null %`. */
function statRow(scope: Locator, name: string): Locator {
  return scope.locator(`[data-slot="stat"]:has([data-slot="identifier"]:text-is("${name}"))`)
}

/** The VALUE span of a `<Stat>`: the sibling immediately after its identifier. */
function statValue(scope: Locator, name: string): Locator {
  return statRow(scope, name).locator('[data-slot="identifier"] + span')
}

/**
 * The aggregate result, read as a grid.
 *
 * Measure cells render their alias in a `hidden` identifier (the header already
 * carries it), so `innerText` — which reports text as *rendered* — gives the
 * figure on the first line and the coverage note, when there is one, on the
 * second. That distinction is the point of the whole screen, so it is kept.
 */
async function aggregateGrid(page: Page) {
  const headers = await page
    .locator('table thead th [data-slot="identifier"]')
    .evaluateAll((els) => els.map((e) => (e.textContent ?? '').trim()))
  const rows = await page.getByTestId('result-row').evaluateAll((trs) =>
    trs.map((tr) =>
      Array.from(tr.querySelectorAll('td')).map((td) => (td as HTMLElement).innerText.trim()),
    ),
  )
  return { headers, rows }
}

/** First line of a cell — the figure, without its coverage note. */
const figureOf = (cell: string) => cell.split('\n')[0].trim()

/* ================================================================ /aggregate */

const PLANS = ['bronze', 'silver', 'gold', 'platinum']

/**
 * 40 rows, four plans, ten rows each. `amount` is distinct per row so every
 * group sum is a different number (a fixture where two groups tie would let a
 * mis-keyed cell pass), and `paid` is filled on exactly five rows per group so
 * one measure covers HALF its group and has to say so.
 */
const planRows = () =>
  Array.from({ length: 40 }, (_, i) => ({
    plan: PLANS[i % 4],
    amount: i + 1,
    paid: Math.floor(i / 4) % 2 === 0 ? 100 : '',
  }))

test('every group aggregate is my arithmetic, over a stated row denominator', async ({
  page,
  h,
}) => {
  const rows = planRows()
  const ds = await h.seed(rows, 'agg')

  // The answers, computed here rather than read back off the screen.
  const size = new Map<string, number>()
  const sum = new Map<string, number>()
  for (const r of rows) {
    size.set(r.plan, (size.get(r.plan) ?? 0) + 1)
    sum.set(r.plan, (sum.get(r.plan) ?? 0) + r.amount)
  }
  expect([...size.values()]).toEqual([10, 10, 10, 10])

  await goto(page, `/aggregate?dataset=${ds.id}`)
  await expect(page.getByTestId('result-row')).toHaveCount(4, { timeout: 25_000 })

  const { headers, rows: grid } = await aggregateGrid(page)
  expect(headers[0]).toBe('plan')
  const sumCol = headers.indexOf('amount_sum')
  const countCol = headers.indexOf('amount_count')
  expect(sumCol, `headers were ${headers.join(', ')}`).toBeGreaterThan(0)
  expect(countCol).toBeGreaterThan(0)

  // Every group: the right label, MY sum, MY count, and a row denominator.
  const seen = new Set<string>()
  for (const cells of grid) {
    const plan = PLANS.find((p) => cells[0].includes(p))
    expect(plan, `no plan label in ${JSON.stringify(cells[0])}`).toBeTruthy()
    seen.add(plan as string)

    expect(figureOf(cells[sumCol])).toBe(sum.get(plan as string)!.toLocaleString())
    expect(figureOf(cells[countCol])).toBe(String(size.get(plan as string)))

    // R11 — the group carries the population every figure in the row is over.
    expect(cells[0], `group cell: ${JSON.stringify(cells[0])}`).toMatch(
      new RegExp(`\\b${size.get(plan as string)} rows\\b`),
    )
  }
  expect([...seen].sort()).toEqual([...PLANS].sort())

  // Sorted by the measure, descending — so the arithmetic also fixes the order.
  const shown = grid.map((c) => Number(figureOf(c[sumCol]).replace(/,/g, '')))
  expect(shown).toEqual([...shown].sort((a, b) => b - a))

  // And the denominator itself is NAMED, not assumed: there is no COUNT(*) on
  // this API, so "rows in this group" had to be bought with a count over a real
  // column, and the screen says which one and whether it is exact.
  await expect(page.getByText('denominator: count(plan)')).toBeVisible()
  await expect(page.getByText('denominator exact')).toBeVisible()
})

test('a measure that covers half its group prints the share it was computed over', async ({
  page,
  h,
}) => {
  const rows = planRows()
  const ds = await h.seed(rows, 'aggcov')

  const filled = rows.filter((r) => r.paid !== '').length
  const perGroupFilled = filled / PLANS.length
  const perGroupSize = rows.length / PLANS.length
  expect(perGroupFilled).toBe(5)

  await goto(page, `/aggregate?dataset=${ds.id}`)
  await expect(page.getByTestId('result-row')).toHaveCount(4, { timeout: 25_000 })

  // Add sum(paid) from the column list — `paid` is null on half the rows, so
  // its sum describes a NARROWER population than the group it sits in.
  await page.getByTestId('column-row').filter({ hasText: 'paid' }).getByTestId('column-add-measure').click()

  await expect(page.locator('table thead th', { hasText: 'paid_sum' })).toBeVisible()
  const { headers, rows: grid } = await aggregateGrid(page)
  const paidCol = headers.indexOf('paid_sum')
  expect(paidCol).toBeGreaterThan(0)

  const expectedSum = (perGroupFilled * 100).toLocaleString()
  const expectedNote = `over ${((perGroupFilled / perGroupSize) * 100).toFixed(0)}% · ${perGroupFilled} rows`
  for (const cells of grid) {
    expect(figureOf(cells[paidCol])).toBe(expectedSum)
    // The qualifier rides on the figure, in every row it occurs.
    expect(cells[paidCol]).toContain(expectedNote)
  }

  // The grand total is qualified the same way, against the whole version.
  const totals = await page.getByTestId('totals-row').evaluateAll((trs) =>
    trs.map((tr) =>
      Array.from(tr.querySelectorAll('td')).map((td) => (td as HTMLElement).innerText.trim()),
    ),
  )
  expect(figureOf(totals[0][paidCol])).toBe((filled * 100).toLocaleString())
  expect(totals[0][paidCol]).toContain(
    `over ${((filled / rows.length) * 100).toFixed(0)}% · ${filled} rows`,
  )
})

/** Twelve groups of known, unequal size — the shape both R7 and the totals need. */
const twelveGroups = () => {
  const rows: { bucket: string; amount: number }[] = []
  for (let k = 0; k < 12; k++) {
    for (let n = 0; n <= k; n++) rows.push({ bucket: `g${String(k).padStart(2, '0')}`, amount: 1 })
  }
  return rows
}

test('the totals row is stated over ALL rows, not over the page it sits under', async ({
  page,
  h,
}) => {
  const rows = twelveGroups()
  const ds = await h.seed(rows, 'aggtot')
  const total = rows.length

  await goto(page, `/aggregate?dataset=${ds.id}`)
  await expect(page.getByTestId('result-row')).toHaveCount(12, { timeout: 25_000 })

  // Narrow the page to eight of the twelve groups. This is the case that makes
  // the claim testable: if "Totals" meant the visible rows, it would drop.
  await page.getByTestId('aggregate-limit').selectOption('8')
  await expect(page.getByTestId('result-row')).toHaveCount(8)

  const { headers, rows: grid } = await aggregateGrid(page)
  const sumCol = headers.indexOf('amount_sum')
  expect(sumCol).toBeGreaterThan(0)

  const visible = grid.reduce((s, c) => s + Number(figureOf(c[sumCol]).replace(/,/g, '')), 0)
  expect(visible, 'the page must not already hold every row').toBeLessThan(total)

  const totalsRow = page.getByTestId('totals-row')
  await expect(totalsRow).toContainText(`over all ${total.toLocaleString()} rows`)
  await expect(totalsRow).toContainText('not just this page')

  const totalCells = await totalsRow.evaluateAll((trs) =>
    trs.map((tr) =>
      Array.from(tr.querySelectorAll('td')).map((td) => (td as HTMLElement).innerText.trim()),
    ),
  )
  // The figure agrees with the words: it is the sum over all 78 rows.
  expect(Number(figureOf(totalCells[0][sumCol]).replace(/,/g, ''))).toBe(total)
})

test('there is no pager on /aggregate — the endpoint has no offset and no cursor', async ({
  page,
  h,
}) => {
  const ds = await h.seed(twelveGroups(), 'aggpage')

  await goto(page, `/aggregate?dataset=${ds.id}`)
  await expect(page.getByTestId('result-row')).toHaveCount(12, { timeout: 25_000 })

  // A pager here would be a lie: `AggregateRequest` carries `limit` and nothing
  // else, so there is no next page to ask the service for.
  await expect(page.getByRole('button', { name: /next page/i })).toHaveCount(0)
  await expect(page.getByRole('button', { name: /previous page/i })).toHaveCount(0)
  await expect(page.locator('[data-testid$="-next"], [data-testid$="-prev"]')).toHaveCount(0)

  // The limit IS the page control, and it widens rather than advances: the
  // first row is the same row at every limit, which a pager's would not be.
  const firstLabel = async () => (await page.getByTestId('result-row').first().innerText()).trim()
  const wide = await firstLabel()
  await page.getByTestId('aggregate-limit').selectOption('8')
  await expect(page.getByTestId('result-row')).toHaveCount(8)
  expect(await firstLabel()).toBe(wide)

  await expect(page.getByText(/takes a limit and no offset/i)).toBeVisible()
})

test('an all-numeric table withdraws the bar offer WITH A REASON instead of opening empty', async ({
  page,
  h,
}) => {
  // R5. Sixty rows, both columns numeric and distinct on every row: above the
  // 50-distinct ceiling nothing qualifies as a dimension, which is exactly the
  // shape the reference builder opened empty on and never recovered from.
  const ds = await h.seed(
    Array.from({ length: 60 }, (_, i) => ({ x: i + 1, y: (i + 1) * 3 })),
    'aggnodim',
  )

  await goto(page, `/aggregate?dataset=${ds.id}`)

  const panel = page.getByTestId('no-dimension')
  await expect(panel).toBeVisible({ timeout: 25_000 })
  await expect(panel).toContainText('No column qualifies as a dimension')

  // Withdrawn, not vanished — and it says why.
  const bar = panel.getByTestId('offer-bar')
  await expect(bar).toBeDisabled()
  await expect(bar).toContainText(/no column qualifies as a dimension/i)

  // The builder is NOT sitting there empty behind it.
  await expect(page.getByTestId('groupby-chip')).toHaveCount(0)
  await expect(page.getByTestId('measure-row')).toHaveCount(0)
  await expect(page.getByTestId('result-row')).toHaveCount(0)

  // And it is not a dead end: the offers that this shape CAN support are live,
  // carry their parameters, and produce a runnable spec.
  const bins = 10
  await expect(panel.getByTestId('offer-bin-count')).toHaveValue(String(bins))
  const distribution = panel.getByTestId('offer-distribution')
  await expect(distribution).toBeEnabled()
  await distribution.click()

  await expect(page.getByTestId('groupby-chip')).toHaveCount(1)
  await expect(page.getByTestId('bucket-kind')).toHaveValue('bin_count')
  await expect(page.getByTestId('result-row').first()).toBeVisible({ timeout: 25_000 })
  const groups = await page.getByTestId('result-row').count()
  expect(groups, 'binning 60 distinct values into 10 buckets').toBeGreaterThan(1)
  expect(groups).toBeLessThanOrEqual(bins)
  // Binning is a CHOICE, and the group key says which one was made — nothing is
  // bucketed silently, which is the other half of the rule.
  await expect(page.locator('table thead th').first()).toContainText(`bin_count ${bins}`)
})

test('above eight groups the top eight are drawn and the tail folds, carrying count and share', async ({
  page,
  h,
}) => {
  // R7. Group `gNN` has NN+1 rows and sum NN+1, so the ranking is total and the
  // fold's arithmetic is fixed before the browser starts.
  const rows = twelveGroups()
  const ds = await h.seed(rows, 'aggfold')

  const totals = new Map<string, number>()
  for (const r of rows) totals.set(r.bucket, (totals.get(r.bucket) ?? 0) + r.amount)
  const ranked = [...totals.entries()].sort((a, b) => b[1] - a[1])
  const head = ranked.slice(0, 8)
  const tail = ranked.slice(8)
  const tailValue = tail.reduce((s, [, v]) => s + v, 0)
  const grand = ranked.reduce((s, [, v]) => s + v, 0)
  const share = ((tailValue / grand) * 100).toFixed(1)

  await goto(page, `/aggregate?dataset=${ds.id}`)
  await expect(page.getByTestId('result-row')).toHaveCount(12, { timeout: 25_000 })

  const fold = page.getByTestId('ranked-fold')
  await expect(fold).toBeVisible()

  // Eight slots is the whole palette, so eight is where drawing stops.
  await expect(page.getByTestId('fold-bar')).toHaveCount(8)
  const drawn = await page
    .getByTestId('fold-bar')
    .evaluateAll((els) => els.map((e) => (e as HTMLElement).innerText.split('\n')[0].trim()))
  expect(drawn).toEqual(head.map(([label]) => label))

  // The four that are not drawn are still STATED — count and share both.
  const other = page.getByTestId('fold-other')
  await expect(other).toBeVisible()
  await expect(other).toContainText(`Other ×${tail.length}`)
  await expect(other).toContainText(`${tail.length} groups folded`)
  await expect(other).toContainText(`${share}% of this page`)
  await expect(other).toContainText('folded, never dropped')

  // The palette is not cycled: the ninth mark takes the beyond-the-palette
  // graphite token, never `--viz-1` again. Two identities in one hue is worse
  // than no hue at all, which is the failure this fold exists to avoid.
  const otherFill = await other
    .locator('[data-slot="magnitude-bar"] > div')
    .evaluate((el) => (el as HTMLElement).style.background)
  expect(otherFill).toBe('var(--m5)')

  const fills = await fold
    .locator('[data-slot="magnitude-bar"] > div')
    .evaluateAll((els) => els.map((e) => (e as HTMLElement).style.background))
  expect(fills).toHaveLength(9)
  expect(fills.filter((f) => /--viz-/.test(f))).toEqual([])
})

/* ==================================================================== /pivot */

/** 40 rows over 2 regions × 4 plans — eight cells, five rows in each. */
const crossRows = () =>
  Array.from({ length: 40 }, (_, i) => ({
    region: ['east', 'west'][i % 2],
    plan: PLANS[Math.floor(i / 2) % 4],
    amount: i + 1,
  }))

test('the cross-tab cells, row totals and grand total are my arithmetic', async ({ page, h }) => {
  const rows = crossRows()
  const ds = await h.seed(rows, 'pivot')

  const cell = new Map<string, number>()
  const rowTotal = new Map<string, number>()
  const colTotal = new Map<string, number>()
  let grand = 0
  for (const r of rows) {
    cell.set(`${r.region}|${r.plan}`, (cell.get(`${r.region}|${r.plan}`) ?? 0) + r.amount)
    rowTotal.set(r.region, (rowTotal.get(r.region) ?? 0) + r.amount)
    colTotal.set(r.plan, (colTotal.get(r.plan) ?? 0) + r.amount)
    grand += r.amount
  }

  await goto(page, `/pivot?dataset=${ds.id}`)
  await page.getByTestId('pivot-add-row').selectOption('region')
  await page.getByTestId('pivot-column-select').selectOption('plan')
  await page.getByTestId('pivot-measure-select').selectOption('amount')
  await page.getByTestId('pivot-run').click()

  const grid = page.getByTestId('pivot-grid')
  await expect(grid.locator('tbody tr')).toHaveCount(2, { timeout: 25_000 })

  // Column identity comes from the DOM — the service orders members itself, and
  // assuming an order here would be the test inventing a fact.
  const members = await grid
    .locator('thead tr')
    .nth(1)
    .locator('th')
    .evaluateAll((els) => els.map((e) => (e as HTMLElement).innerText.trim()))
  expect([...members].sort()).toEqual([...PLANS].sort())

  const body = await grid.locator('tbody tr').evaluateAll((trs) =>
    trs.map((tr) =>
      Array.from(tr.querySelectorAll('td')).map((td) => (td as HTMLElement).innerText.trim()),
    ),
  )

  for (const cells of body) {
    const region = cells[0]
    expect(['east', 'west']).toContain(region)
    members.forEach((member, i) => {
      const expected = cell.get(`${region}|${member}`)
      expect(
        cells[1 + i],
        `cell ${region} × ${member} — every cell is an aggregate, none is a stored value`,
      ).toBe(expected!.toLocaleString())
    })
    // The last column is the row total, re-aggregated by the service.
    expect(cells[cells.length - 1]).toBe(rowTotal.get(region)!.toLocaleString())
  }

  // The totals band: column totals and the grand total, both from arithmetic.
  const footer = await grid
    .locator('tfoot tr')
    .first()
    .locator('td')
    .evaluateAll((els) => els.map((e) => (e as HTMLElement).innerText.trim()))
  members.forEach((member, i) => {
    expect(footer[1 + i]).toBe(colTotal.get(member)!.toLocaleString())
  })
  expect(footer[footer.length - 1]).toBe(grand.toLocaleString())

  // R9 — the shares under the totals band divide by the grand total, and every
  // one of them goes through `ratio()`. Nothing prints from a zero denominator.
  const shares = await grid
    .locator('tfoot tr')
    .nth(1)
    .locator('td')
    .evaluateAll((els) => els.map((e) => (e as HTMLElement).innerText.trim()))
  members.forEach((member, i) => {
    const pct = ((colTotal.get(member)! / grand) * 100).toFixed(1)
    expect(shares[1 + i]).toBe(`${pct}%`)
  })
  expect(shares[shares.length - 1]).toBe('100.0%')
})

test('the row-label column stays on screen when the cross-tab is scrolled sideways', async ({
  page,
  h,
}) => {
  // 24 members is the widest the shelf will draw unfolded, and it is wide
  // enough to overflow: the reference pivot put 64,000px of members beside a
  // row label that scrolled away, so nothing said which row you were reading.
  const members = 24
  const rows = Array.from({ length: members * 2 }, (_, i) => ({
    region: i < members ? 'east' : 'west',
    member: `member-${String(i % members).padStart(2, '0')}`,
    amount: (i % members) + 1,
  }))
  const ds = await h.seed(rows, 'pivotstick')

  await goto(page, `/pivot?dataset=${ds.id}`)
  await page.getByTestId('pivot-add-row').selectOption('region')
  await page.getByTestId('pivot-column-select').selectOption('member')
  await page.getByTestId('pivot-measure-select').selectOption('amount')
  // Unfolded, so all 24 members become columns and the grid genuinely overflows.
  await page.getByTestId('pivot-fold-toggle').click()
  await page.getByTestId('pivot-run').click()

  const grid = page.getByTestId('pivot-grid')
  await expect(grid.locator('tbody tr')).toHaveCount(2, { timeout: 25_000 })
  await expect(grid.locator('thead tr').nth(1).locator('th')).toHaveCount(members)

  const label = page.getByTestId('pivot-row-label').first()
  expect(await label.evaluate((el) => getComputedStyle(el).position)).toBe('sticky')

  const container = page.locator('[data-slot="table-container"]').filter({ has: grid })
  const overflow = await container.evaluate((el) => el.scrollWidth - el.clientWidth)
  expect(overflow, 'the fixture must actually overflow or this proves nothing').toBeGreaterThan(0)

  const before = await label.innerText()
  await container.evaluate((el) => {
    el.scrollLeft = el.scrollWidth
  })
  await expect
    .poll(async () => container.evaluate((el) => el.scrollLeft))
    .toBeGreaterThan(0)

  // Still the same label, still at the left edge of the scrolled container.
  expect(await label.innerText()).toBe(before)
  const box = await label.boundingBox()
  const frame = await container.boundingBox()
  expect(box, 'the row label left the layout entirely').toBeTruthy()
  expect(frame).toBeTruthy()
  expect(box!.x).toBeLessThanOrEqual(frame!.x + 2)
  expect(box!.x + box!.width).toBeGreaterThan(frame!.x)
})

test('the column shelf enforces a member limit and offers the fold instead', async ({
  page,
  h,
}) => {
  // 30 members, above the 24-member limit. Member `mNN` totals 2·(NN+1), so the
  // ranking — and therefore which eight survive — is fixed by arithmetic.
  const members = 30
  const rows = Array.from({ length: members * 2 }, (_, i) => ({
    region: i < members ? 'east' : 'west',
    code: `m${String(i % members).padStart(2, '0')}`,
    amount: (i % members) + 1,
  }))
  const ds = await h.seed(rows, 'pivotcap')

  const totals = new Map<string, number>()
  for (const r of rows) totals.set(r.code, (totals.get(r.code) ?? 0) + r.amount)
  const ranked = [...totals.entries()].sort((a, b) => b[1] - a[1])
  const head = ranked.slice(0, 8).map(([label]) => label)
  const tail = ranked.slice(8)
  const tailValue = tail.reduce((s, [, v]) => s + v, 0)
  const grand = ranked.reduce((s, [, v]) => s + v, 0)
  const share = ((tailValue / grand) * 100).toFixed(1)

  await goto(page, `/pivot?dataset=${ds.id}`)
  await page.getByTestId('pivot-add-row').selectOption('region')
  await page.getByTestId('pivot-column-select').selectOption('code')
  await page.getByTestId('pivot-measure-select').selectOption('amount')

  // The shelf knows the cardinality BEFORE the run, from the profile.
  await expect(page.getByTestId('pivot-column-shelf')).toContainText(
    `${members} distinct · limit 24`,
    { timeout: 25_000 },
  )

  // Turn the fold off and the shelf refuses the field rather than drawing 30
  // columns — and the refusal is the offer, not a dead stop.
  await page.getByTestId('pivot-fold-toggle').click()
  const guard = page.getByTestId('pivot-member-guard')
  await expect(guard).toBeVisible()
  await expect(guard).toContainText(`${members} distinct members`)
  await expect(guard).toContainText('24-member limit')
  await expect(guard).toContainText('fold top 8')
  await expect(page.getByTestId('pivot-run')).toBeDisabled()

  // Accept the offer: the guard clears and the run is allowed.
  await page.getByTestId('pivot-fold-toggle').click()
  await expect(guard).toHaveCount(0)
  await expect(page.getByTestId('pivot-run')).toBeEnabled()
  await page.getByTestId('pivot-run').click()

  const grid = page.getByTestId('pivot-grid')
  await expect(grid.locator('tbody tr')).toHaveCount(2, { timeout: 25_000 })

  // Eight members drawn plus one "Other" — not thirty columns.
  const drawn = await grid
    .locator('thead tr')
    .nth(1)
    .locator('th')
    .evaluateAll((els) => els.map((e) => (e as HTMLElement).innerText.trim()))
  expect(drawn).toHaveLength(9)
  expect(drawn.slice(0, 8)).toEqual(head)
  await expect(page.getByTestId('pivot-other-column')).toContainText(`Other · ${tail.length}`)

  // What is not drawn is stated: its count AND its share of the measure.
  const legend = page.getByTestId('pivot-fold-legend')
  await expect(legend).toContainText(`${members} members · ${tail.length} folded into Other`)
  await expect(legend).toContainText(`Other carries ${share}%`)
  await expect(legend).toContainText(`across ${tail.length} members`)
})

test('the SQL console runs a read-only SELECT whose result matches a direct API read', async ({
  page,
  h,
}) => {
  const rows = crossRows()
  const ds = await h.seed(rows, 'sql')

  await goto(page, `/pivot?dataset=${ds.id}`)
  await page.getByTestId('sql-tab').click()

  // Presented as read-only, in words, before anything runs.
  await expect(page.getByText('SELECT only', { exact: true })).toBeVisible()
  await expect(page.getByText('no DDL or DML', { exact: true })).toBeVisible()
  await expect(page.getByText('one statement', { exact: true })).toBeVisible()
  await expect(page.getByText(/Read-only sandbox/)).toBeVisible()

  const statement = 'SELECT plan, amount FROM data ORDER BY amount DESC LIMIT 5'
  const editor = page.getByTestId('sql-editor').locator('.cm-content')
  await editor.click()
  await page.keyboard.press('ControlOrMeta+a')
  // `insertText`, not `type`: the editor is CodeMirror, and typing key by key
  // races its own re-measure (it throws "No tile at position N") and lets
  // bracket-closing rewrite the statement. One input event replaces the
  // selection cleanly. Read the buffer back before running — a console that
  // ran something other than what is on screen is the bug this guards.
  await page.keyboard.insertText(statement)
  await expect(editor).toHaveText(statement)

  await page.getByTestId('sql-run').click()

  const table = page.getByTestId('sql-result')
  await expect(table.locator('tbody tr')).toHaveCount(5, { timeout: 25_000 })

  // The statement that ran is echoed verbatim — a console that reformats what
  // you typed is a console you cannot trust the result of.
  await expect(page.getByText('Statement that ran')).toBeVisible()
  await expect(page.locator('pre')).toHaveText(statement)

  // The comparison is against a DIFFERENT endpoint: the sheet query API. Both
  // read the same version, so disagreement is a real defect, not a race.
  const read = await apiOk<{ items: { plan: string; amount: number }[]; total: number }>(
    'POST',
    `/datasets/${ds.id}/versions/1/sheets/data/query`,
    { body: { limit: 1000 } },
  )
  expect(read.total).toBe(rows.length)
  const expected = [...read.items]
    .sort((a, b) => b.amount - a.amount)
    .slice(0, 5)
    .map((r) => [r.plan, r.amount.toLocaleString()])

  const shown = await table.locator('tbody tr').evaluateAll((trs) =>
    trs.map((tr) =>
      Array.from(tr.querySelectorAll('td')).map((td) => (td as HTMLElement).innerText.trim()),
    ),
  )
  expect(shown).toEqual(expected)

  // Nothing on this surface offers a write path, because the endpoint has none.
  await expect(page.getByRole('button', { name: /insert|update|delete|drop/i })).toHaveCount(0)
})

test('a viewer on a sensitive dataset gets a refusal, not an error box', async ({ page, h }) => {
  // The refusal is the point: a pivot widens the distinct values of its column
  // dimension into output column NAMES, which masking cannot reach. So the
  // service declines outright rather than returning a masked cross-tab.
  const ds = await h.seed(
    Array.from({ length: 20 }, (_, i) => ({
      region: ['east', 'west'][i % 2],
      ssn: `SECRET-${i}`,
      amount: i + 1,
    })),
    'pivotsens',
  )
  await h.markSensitive(ds.id, 'ssn')

  // The 403 is the expected behaviour under test; Chromium logs every failed
  // request as a console error, so it is allowed explicitly rather than muted.
  h.allowError(/403 \(Forbidden\)/)

  const viewer = await h.viewer()
  await h.asSeat(page, viewer.user_id, 'Viewer')
  await goto(page, `/pivot?dataset=${ds.id}`)

  // Profiling is refused too, so the member limit cannot be checked before the
  // run — and the page says exactly that instead of showing an empty shelf.
  await expect(page.getByText('Distinct counts unavailable')).toBeVisible({ timeout: 25_000 })

  await page.getByTestId('pivot-add-row').selectOption('region')
  await page.getByTestId('pivot-column-select').selectOption('ssn')
  await page.getByTestId('pivot-measure-select').selectOption('amount')
  await page.getByTestId('pivot-run').click()

  // A first-class state, in the product's own words — not a stack trace and
  // not a generic failure.
  await expect(page.getByText('Restricted for this seat')).toBeVisible()
  await expect(page.getByText(/Pivoting is refused on datasets that declare sensitive columns/)).toBeVisible()
  await expect(page.getByTestId('pivot-grid')).toHaveCount(0)

  // And no sensitive value reached the page on the way to saying no.
  expect(await page.locator('body').innerText()).not.toContain('SECRET-')
})

/* =================================================================== /column */

test('profiling is not automatic — the page offers it and does not read as broken', async ({
  page,
  h,
}) => {
  const rows = Array.from({ length: 20 }, (_, i) => ({
    uid: `UID-${1000 + i}`,
    tier: ['a', 'b', 'c', 'd'][i % 4],
  }))
  const ds = await h.seed(rows, 'colgate')

  // `POST /profile` is a computation AND a refusable one, so the page must not
  // fire it on route entry. Count the requests rather than trusting the copy.
  let profileCalls = 0
  page.on('request', (r) => {
    if (r.url().includes('/profile')) profileCalls += 1
  })

  await goto(page, `/column?dataset=${ds.id}&column=tier`)
  await expect(page.getByTestId('column-name')).toHaveText('tier', { timeout: 25_000 })
  expect(profileCalls, 'nothing may profile on route entry').toBe(0)

  // Not broken: the denominators it CAN know are on screen and correct.
  await expect(page.getByTestId('metric-rows')).toContainText(String(rows.length))
  await expect(page.getByTestId('metric-columns')).toContainText('2')
  await expect(page.getByText('not profiled')).toBeVisible()

  // The gate says what is unknown, and that unknown is not the same as good.
  const gate = page.getByTestId('profile-gate')
  await expect(gate).toBeVisible()
  await expect(gate).toContainText('No profile has been run')
  await expect(gate).toContainText('unknown')
  await expect(page.getByTestId('headline')).toHaveCount(0)
  await expect(page.getByTestId('distribution-card')).toHaveCount(0)

  // The affordance is explicit, and taking it is what computes the statistics.
  const run = page.getByTestId('run-profile')
  await expect(run).toBeEnabled()
  await expect(run).toContainText('Run profile')
  await run.click()

  await expect(page.getByTestId('headline')).toBeVisible({ timeout: 25_000 })
  await expect(page.getByTestId('profile-gate')).toHaveCount(0)
  await expect(page.getByText('profiled', { exact: true })).toBeVisible()
  expect(profileCalls).toBeGreaterThan(0)
})

test('every statistic states the population it was computed over', async ({ page, h }) => {
  // R11. 30 rows, `score` filled on 18 of them and holding 5 distinct values —
  // so the value statistics saw a NARROWER population than the sheet, which is
  // precisely the case where an unqualified `mean` prints a wrong-looking-right
  // number.
  const rows = Array.from({ length: 30 }, (_, i) => ({
    label: `L${i % 6}`,
    score: i < 18 ? (i % 5) * 10 : '',
    blank: '',
  }))
  const ds = await h.seed(rows, 'colcov')

  const total = rows.length
  const filled = rows.filter((r) => r.score !== '').length
  const nulls = total - filled
  expect([filled, nulls]).toEqual([18, 12])

  await goto(page, `/column?dataset=${ds.id}&column=score`)
  await page.getByTestId('run-profile').click()
  await expect(page.getByTestId('headline')).toBeVisible({ timeout: 25_000 })

  // Cross-check the fixture against the service before asserting on the DOM:
  // if these disagree the failure should name the seam, not the selector.
  const profile = await apiOk<{
    row_count: number
    columns: { name: string; non_null_count: number; null_count: number; unique_count: number }[]
  }>('POST', '/profile', {
    body: {
      dataset_id: ds.id,
      version_number: 1,
      sheet: 'data',
      include_histograms: true,
      top_n: 5,
    },
  })
  const api = profile.columns.find((c) => c.name === 'score')!
  expect([profile.row_count, api.non_null_count, api.null_count]).toEqual([total, filled, nulls])

  const note = `over ${((filled / total) * 100).toFixed(0)}% · ${filled} rows`

  // The counts are over EVERY row, nulls included — that is their population.
  const counts = page.getByTestId('stats-counts')
  await expect(statValue(counts, 'rows')).toHaveText(String(total))
  await expect(statValue(counts, 'filled')).toHaveText(String(filled))
  await expect(statValue(counts, 'null')).toHaveText(String(nulls))
  await expect(statValue(counts, 'null %')).toHaveText(`${((nulls / total) * 100).toFixed(1)}%`)

  // …and the two that describe only the filled rows say so, in the same block.
  await expect(statRow(counts, 'distinct')).toHaveAttribute('data-partial', '')
  await expect(statRow(counts, 'distinct')).toContainText(note)
  await expect(statRow(counts, 'unique %')).toContainText(note)
  await expect(statValue(counts, 'distinct')).toHaveText(String(api.unique_count))
  // The counts over the whole sheet must NOT be narrowed — a denominator
  // applied where it does not belong is its own wrong number.
  await expect(statRow(counts, 'rows')).not.toHaveAttribute('data-partial', '')
  await expect(statRow(counts, 'null')).not.toHaveAttribute('data-partial', '')

  // Every value statistic carries the narrower population. Not one of them, all.
  const values = page.getByTestId('stats-values')
  const statCount = await values.locator('[data-slot="stat"]').count()
  expect(statCount).toBeGreaterThan(0)
  await expect(values.locator('[data-slot="stat"][data-partial]')).toHaveCount(statCount)
  for (const name of ['min', 'max', 'mean', 'p50', 'std']) {
    await expect(statRow(values, name), `${name} must state its population`).toContainText(note)
  }

  // The type badge is qualified for the same reason — an unqualified one is a
  // promise the column does not keep.
  await expect(page.getByTestId('column-type')).toContainText(
    `· ${Math.round((filled / total) * 100)}%`,
  )
})

test('a 100%-distinct column replaces the chart with an identity panel and says so', async ({
  page,
  h,
}) => {
  // R4 on the deep-dive. `uid` is distinct on every row: a top-values chart is
  // twenty bars each one row tall. The reference drew no card at all, and a
  // column with no card reads as a column with no problem.
  const rows = Array.from({ length: 20 }, (_, i) => ({
    uid: `UID-${1000 + i}`,
    tier: ['a', 'b', 'c', 'd'][i % 4],
  }))
  const ds = await h.seed(rows, 'colident')

  await goto(page, `/column?dataset=${ds.id}&column=uid`)
  await page.getByTestId('run-profile').click()
  await expect(page.getByTestId('distribution-card')).toBeVisible({ timeout: 25_000 })

  const card = page.getByTestId('distribution-card')
  await expect(card).toContainText('Identity')
  await expect(page.getByTestId('identity-panel')).toBeVisible()

  // The suppression is stated IN WORDS, with the number that triggered it.
  const note = page.getByTestId('suppression-note')
  await expect(note).toContainText('Distribution suppressed, not omitted')
  await expect(note).toContainText('100.0% distinct')
  await expect(note).toContainText('Above 95% a top-values chart is meaningless')

  // The useless chart is gone, not merely pushed below the fold.
  await expect(card.getByTestId('top-values')).toHaveCount(0)

  // What replaces it says something the chart could not: shape and identity.
  await expect(page.getByTestId('charset-signature')).toContainText('A{3}-9{4}')
  await expect(page.getByTestId('sample-values')).toBeVisible()

  // The same profile, one column over: the rule fires on SHAPE, it does not
  // remove the feature. `tier` has four values in twenty rows.
  await page.getByTestId('column-picker').selectOption({ label: 'tier' })
  await expect(page.getByTestId('column-name')).toHaveText('tier')
  await expect(page.getByTestId('identity-panel')).toHaveCount(0)
  await expect(page.getByTestId('top-values')).toBeVisible()

  const bars = await page
    .getByTestId('top-values')
    .locator('[data-testid="distribution-row"]')
    .evaluateAll((els) => els.map((e) => (e as HTMLElement).innerText.trim()))
  expect(bars).toHaveLength(4)

  const groups = new Map<string, number>()
  for (const r of rows) groups.set(r.tier, (groups.get(r.tier) ?? 0) + 1)
  for (const line of bars) {
    const label = line.split('\n')[0].trim()
    const count = groups.get(label)
    expect(count, `unexpected bar ${label}`).toBeTruthy()
    expect(line).toContain(String(count))
    expect(line).toContain(`${((count! / rows.length) * 100).toFixed(1)}%`)
  }
})

test('a statistic is never printed from a zero denominator', async ({ page, h }) => {
  // R9. `blank` is empty on every row: 0 filled rows, so `distinct / filled` is
  // a 0/0. The reference rendered `NaN%` and `width:NaN%` here.
  const rows = Array.from({ length: 30 }, (_, i) => ({
    label: `L${i % 6}`,
    score: i < 18 ? (i % 5) * 10 : '',
    blank: '',
  }))
  const ds = await h.seed(rows, 'colzero')

  await goto(page, `/column?dataset=${ds.id}&column=blank`)

  // Before any profile at all — there is nothing to divide by yet either.
  expect(await page.locator('body').innerText()).not.toContain('NaN')

  await page.getByTestId('run-profile').click()
  await expect(page.getByTestId('headline')).toBeVisible({ timeout: 25_000 })

  const counts = page.getByTestId('stats-counts')
  await expect(statValue(counts, 'rows')).toHaveText(String(rows.length))
  await expect(statValue(counts, 'filled')).toHaveText('0')
  await expect(statValue(counts, 'null')).toHaveText(String(rows.length))

  // The 0/0: an em dash, not a number, and not a zero either — "0%" would be a
  // claim about a population that does not exist.
  await expect(statValue(counts, 'unique %')).toHaveText('—')

  // The value statistics have no population at all and are suppressed as such.
  const values = page.getByTestId('stats-values')
  for (const name of ['min', 'max', 'mean', 'p50', 'std']) {
    await expect(statValue(values, name), `${name} over an empty population`).toHaveText('—')
  }
  // …and the block states the empty population rather than implying a failure.
  await expect(values).toContainText('over 0% · 0 rows')

  const text = await page.locator('body').innerText()
  expect(text, 'no statistic may reach the DOM from a zero denominator').not.toContain('NaN')
  expect(text).not.toContain('Infinity')
  expect(await page.content()).not.toContain('NaN')
})

test('an aggregate with no finite value says so, rather than showing an empty cell', async ({
  page,
  h,
}) => {
  // End-to-end proof for a fix that spans both apps. `STDDEV_SAMP` squares its
  // input, so one legitimate value near the double ceiling overflows the
  // accumulator. The service used to answer 400 for the WHOLE request — valid
  // data, no result. It now returns the other groups intact and names the
  // measure it could not represent, and the table has to say which.
  //
  // A blank cell would read as "no rows matched". That is the opposite of what
  // happened, and it is the confident-wrong-answer shape this codebase exists
  // to prevent.
  const rows = [
    ...Array.from({ length: 6 }, (_, i) => ({ grp: 'ok', reading: i + 1 })),
    { grp: 'huge', reading: 1 },
    { grp: 'huge', reading: 1e308 },
  ]
  const ds = await h.seed(rows, 'stdoverflow')

  // The service's own answer first, so the UI assertion cannot pass against a
  // premise that quietly changed.
  const res = await apiOk<{
    data: Record<string, unknown>[]
    unavailable_measures?: string[]
  }>('POST', '/aggregate', {
    body: {
      dataset_id: ds.id,
      version_number: 1,
      sheet: 'data',
      group_by: ['grp'],
      aggregations: [{ column: 'reading', function: 'std', alias: 'spread' }],
    },
  })
  expect(res.unavailable_measures, 'the service did not name the measure').toContain('spread')

  // The healthy group still has a real number — one bad group must not blank
  // the rest.
  const ok = res.data.find((r) => r.grp === 'ok')!
  expect(ok.spread, 'the unaffected group lost its value').not.toBeNull()

  await goto(page, `/aggregate?dataset=${ds.id}`)
  await expect(page.getByTestId('result-row')).toHaveCount(2, { timeout: 25_000 })

  // The builder already seeds a measure on the one numeric column, so switch
  // that one to std rather than adding a second on the same column.
  const measure = page.getByTestId('measure-row').filter({ hasText: 'reading' })
  await expect(measure).toHaveCount(1)
  await measure.getByTestId('measure-fn').selectOption('std')

  // The healthy group keeps a real figure; the overflowing one says why it has
  // none, instead of rendering the em dash that means "no rows".
  const table = page.locator('table')
  await expect(table).toContainText('no finite value', { timeout: 25_000 })

  const okRow = page.getByTestId('result-row').filter({ hasText: 'ok' })
  await expect(okRow, 'the unaffected group lost its figure on screen').not.toContainText(
    'no finite value',
  )
})

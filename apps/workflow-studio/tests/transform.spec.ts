import { test, expect, goto, api, apiOk } from './fixtures'
import type { Locator, Page, Response } from '@playwright/test'

/**
 * The transform lens — the multi-step pipeline builder.
 *
 * The property everything else here hangs off: this panel compiles SCHEMA ONLY.
 * `POST /datasets/{id}/transformations/compile` has two modes. Omit `rows` and
 * it needs nothing but `dataset:read`, touches no values, and answers "what
 * columns would come out". Send `rows` and the same endpoint becomes a read of
 * the pipeline's OUTPUT, gated behind `ensure_raw_access` — because a `compute`
 * step can copy a sensitive column into a new name, so masking the result by
 * source column name would not hold.
 *
 * The lens only ever asks the first question, which is what makes it work
 * identically for every seat: a viewer can design a pipeline over a dataset
 * whose values they are not cleared for. The first two tests below watch the
 * wire to prove the request stays on the safe side of that line, and prove the
 * gate is real by asking for `rows` from the same seat and being refused.
 *
 * `lenses.spec.ts` already covers the single-drop happy path and asserts no
 * `rows` on one request; this file starts where that stops — several compiles,
 * several step types, the folded per-step schemas, and the staleness rule that
 * stops the panel quoting a figure from a pipeline you have since edited.
 */

/* ------------------------------------------------------------------ helpers */

const lens = (page: Page) => page.getByTestId('lens-body')

/** A titled block inside the panel — used so a label is never matched twice. */
const section = (page: Page, title: string | RegExp) =>
  lens(page)
    .locator('div.mb-4')
    .filter({ has: page.getByRole('heading', { name: title, exact: true }) })

/** One `Row` of the Limits block: the label span's parent carries the value. */
const limitRow = (page: Page, label: string) =>
  section(page, 'Limits').getByText(label, { exact: true }).locator('xpath=..')

const stepCards = (page: Page) => page.getByTestId('transform-step')
const stepCard = (page: Page, i: number) => stepCards(page).nth(i)

/** The compile status word: not compiled · compiled · draft changed · refused. */
const statusWord = (page: Page) => lens(page).locator('[data-slot="status"]')

/** "Columns  4 ▸ 2" as a whitespace-free string, so `—` is unambiguous. */
async function columnsFigure(page: Page): Promise<string> {
  const raw =
    (await lens(page).locator('[data-slot="metric"]').filter({ hasText: /columns/i }).textContent()) ??
    ''
  return raw.replace(/\s+/g, '').replace(/^Columns/i, '')
}

async function openTransform(page: Page, datasetId: string) {
  await goto(page, `/data?dataset=${datasetId}`)
  await page.getByTestId('lens-transform').click()
  // The builder opens on one `drop` step, and only renders once the sheet's
  // columns have arrived — so this also waits out the schema fetch.
  await expect(stepCards(page)).toHaveCount(1)
}

/** Add a step by its exact op name from the "Add step" menu. */
async function addStep(page: Page, op: string): Promise<Locator> {
  const before = await stepCards(page).count()
  await page.getByTestId('transform-step-add').click()
  await page
    .getByTestId('transform-op')
    .filter({ has: page.locator(`[data-slot="identifier"]:text-is("${op}")`) })
    .click()
  await expect(stepCards(page)).toHaveCount(before + 1)
  return stepCard(page, before)
}

/** Click Compile and hand back the response, so tests can read the real body. */
async function compile(page: Page): Promise<Response> {
  const [res] = await Promise.all([
    page.waitForResponse(
      (r) => r.url().includes('/transformations/compile') && r.request().method() === 'POST',
    ),
    page.getByTestId('transform-compile').click(),
  ])
  await expect(page.getByTestId('transform-compile')).not.toContainText('Compiling')
  return res
}

/** Toggle one column chip inside a step card, matching the name exactly. */
async function toggleColumn(card: Locator, testid: string, name: string) {
  await card.getByTestId(testid).filter({ hasText: new RegExp(`^${name}$`) }).click()
}

/** Names of the chips a step card is offering, in order. */
const chipNames = (card: Locator, testid: string) =>
  card.getByTestId(testid).evaluateAll((els) => els.map((e) => e.textContent?.trim() ?? ''))

/** Record every compile request the page makes, with its body. */
function watchCompiles(page: Page): { url: string; body: string }[] {
  const seen: { url: string; body: string }[] = []
  page.on('request', (r) => {
    if (r.method() === 'POST' && r.url().includes('/transformations/compile')) {
      seen.push({ url: r.url(), body: r.postData() ?? '' })
    }
  })
  return seen
}

const SCHEMA_ONLY_KEYS = ['sheet', 'steps', 'version_selector']

/* ------------------------------------------------- 1. the security property */

test('no compile request ever carries `rows`, whatever the pipeline', async ({ page, h }) => {
  const ds = await h.seed(
    Array.from({ length: 10 }, (_, i) => ({
      order_id: i + 1,
      city: ['paris', 'lisbon'][i % 2],
      amount: i * 7,
      note: `n${i}`,
    })),
    'wire',
  )

  await openTransform(page, ds.id)
  const sent = watchCompiles(page)

  // Compile 1 — the seed `drop` step.
  await toggleColumn(stepCard(page, 0), 'transform-column-toggle', 'note')
  const first = await compile(page)

  // Compile 2 — a `compute` step, the one op that can invent a column out of a
  // sensitive one. If any request were going to ask for values, this is it.
  const computeCard = await addStep(page, 'compute')
  await computeCard.getByLabel('node', { exact: true }).selectOption('upper')
  await computeCard.getByLabel('into', { exact: true }).fill('city_upper')
  await computeCard.getByLabel('column', { exact: true }).selectOption('city')
  const second = await compile(page)

  // Compile 3 — a `filter`, which carries a literal value in the request.
  const filterCard = await addStep(page, 'filter')
  await filterCard.getByLabel('column', { exact: true }).selectOption('amount')
  await filterCard.getByLabel('operator', { exact: true }).selectOption('gt')
  await filterCard.getByLabel('value', { exact: true }).fill('10')
  const third = await compile(page)

  // The whole contract, on the wire: no `rows` key, and nothing beyond the
  // three schema-only fields. `rows` is what turns this into a data read.
  expect(sent.length).toBe(3)
  for (const req of sent) {
    const body = JSON.parse(req.body) as Record<string, unknown>
    expect(body).not.toHaveProperty('rows')
    expect(Object.keys(body).sort()).toEqual(SCHEMA_ONLY_KEYS)
    // …nor smuggled through the query string.
    expect(new URL(req.url).search).toBe('')
  }

  // And nothing came back either: the server answered shape, not values.
  for (const res of [first, second, third]) {
    const body = (await res.json()) as { rows: unknown[]; sampled: boolean; output_schema: unknown[] }
    expect(res.status()).toBe(200)
    expect(body.rows).toEqual([])
    expect(body.sampled).toBe(false)
    expect(body.output_schema.length).toBeGreaterThan(0)
  }

  // The panel says so, rather than leaving it to be inferred.
  await expect(lens(page).getByText('0 rows read · schema only')).toBeVisible()
  await expect(
    page.getByText('Schema only — no rows were read, so this works the same for every seat.'),
  ).toBeVisible()
})

test('a viewer designs a pipeline over a sensitive column — schema yes, values refused', async ({
  page,
  h,
}) => {
  const SENTINEL = 'TRANSFORM-LEAKCHECK'
  const cols = ['record_id', 'ssn', 'amount'] as const
  const ds = await h.seed(
    Array.from({ length: 6 }, (_, i) => ({
      record_id: i + 1,
      ssn: `${SENTINEL}-${i}`,
      amount: i * 3,
    })),
    'viewerwire',
  )
  await h.markSensitive(ds.id, 'ssn')

  const viewer = await h.viewer()
  await h.asSeat(page, viewer.user_id, 'Viewer')
  await openTransform(page, ds.id)

  const sent = watchCompiles(page)

  // A real pipeline, not an empty one: drop a column, keep the sensitive one.
  await toggleColumn(stepCard(page, 0), 'transform-column-toggle', 'amount')
  const res = await compile(page)

  // It works for this seat, and the sensitive column's NAME is part of the
  // answer — the gate is on values, not on the existence of the column.
  expect(res.status()).toBe(200)
  await expect(page.getByTestId('transform-output-schema')).toBeVisible()
  await expect(page.getByTestId('transform-output-name')).toHaveText(
    cols.filter((c) => c !== 'amount'),
  )

  expect(sent.length).toBe(1)
  const body = JSON.parse(sent[0].body) as Record<string, unknown>
  expect(body).not.toHaveProperty('rows')
  expect(Object.keys(body).sort()).toEqual(SCHEMA_ONLY_KEYS)

  const payload = (await res.json()) as { rows: unknown[] }
  expect(payload.rows).toEqual([])

  // Not one value reached the page — including through the grid beside it.
  expect(await page.locator('body').innerText()).not.toContain(SENTINEL)
  expect(await page.content()).not.toContain(SENTINEL)

  // The counterfactual, from the same seat: the identical pipeline WITH `rows`
  // is refused. That is what makes omitting `rows` load-bearing rather than a
  // stylistic choice — and it is why the lens must never send it.
  const steps = [{ type: 'drop', columns: ['amount'] }]
  const compileBody = { sheet: 'data', version_selector: { mode: 'current' }, steps }
  const withRows = await api('POST', `/datasets/${ds.id}/transformations/compile`, {
    body: { ...compileBody, rows: 5 },
    userId: viewer.user_id,
  })
  expect(withRows.status).toBe(403)

  const schemaOnly = await api('POST', `/datasets/${ds.id}/transformations/compile`, {
    body: compileBody,
    userId: viewer.user_id,
  })
  expect(schemaOnly.status).toBe(200)
  expect(schemaOnly.body.rows).toEqual([])
  expect(schemaOnly.body.output_schema.map((c: { name: string }) => c.name)).toEqual([
    'record_id',
    'ssn',
  ])
})

/* ----------------------------------------------------------- 2. the builder */

test('a drop step removes exactly those columns from the compiled output', async ({ page, h }) => {
  const row = { order_id: 1, region: 'EU', amount: 12, note: 'first' }
  const ds = await h.seed(
    Array.from({ length: 6 }, (_, i) => ({ ...row, order_id: i + 1, amount: i * 5 })),
    'drop',
  )
  const input = Object.keys(row)
  const dropped = ['amount', 'note']
  const expected = input.filter((c) => !dropped.includes(c))

  await openTransform(page, ds.id)
  const card = stepCard(page, 0)
  for (const c of dropped) await toggleColumn(card, 'transform-column-toggle', c)
  for (const c of dropped) {
    await expect(
      card.getByTestId('transform-column-toggle').filter({ hasText: new RegExp(`^${c}$`) }),
    ).toHaveAttribute('aria-pressed', 'true')
  }

  await compile(page)

  // The list itself, not its length — a count would pass on the wrong columns.
  await expect(page.getByTestId('transform-output-name')).toHaveText(expected)
  await expect(page.getByTestId('transform-dropped-column')).toHaveCount(dropped.length)
  for (const c of dropped) {
    await expect(page.getByTestId('transform-dropped-column').filter({ hasText: c })).toContainText(
      'gone',
    )
  }
  await expect(page.getByTestId('transform-output-schema')).toContainText(
    `${expected.length} columns · ${dropped.length} gone`,
  )
  expect(await columnsFigure(page)).toBe(`${input.length}▸${expected.length}`)

  // Re-read from the service: the panel is not narrating its own draft.
  const compiled = await apiOk<{ output_schema: { name: string }[] }>(
    'POST',
    `/datasets/${ds.id}/transformations/compile`,
    {
      body: {
        sheet: 'data',
        version_selector: { mode: 'current' },
        steps: [{ type: 'drop', columns: dropped }],
      },
    },
  )
  expect(compiled.output_schema.map((c) => c.name)).toEqual(expected)
})

test('two steps compose in order, and reversing them is refused', async ({ page, h }) => {
  // The 400 below is the point of the second half of this test.
  h.allowError(/400 \(Bad Request\)/)

  const row = { staff_id: 1, first_name: 'ada', amount: 10 }
  const ds = await h.seed(
    Array.from({ length: 5 }, (_, i) => ({ ...row, staff_id: i + 1, amount: i * 4 })),
    'compose',
  )

  await openTransform(page, ds.id)
  // Start from nothing so the two steps under test are steps 1 and 2.
  await stepCard(page, 0).getByTestId('transform-step-remove').click()
  await expect(stepCards(page)).toHaveCount(0)

  // Step 1: amount → amt.
  const rename = await addStep(page, 'rename')
  await rename.getByLabel('column', { exact: true }).selectOption('amount')
  await rename.getByLabel('new name', { exact: true }).fill('amt')
  await compile(page)
  await expect(page.getByTestId('transform-output-name')).toHaveText([
    'staff_id',
    'first_name',
    'amt',
  ])

  // Step 2 reads `amt`, a name that exists only because step 1 ran.
  const compute = await addStep(page, 'compute')
  await compute.getByLabel('node', { exact: true }).selectOption('mul')
  await compute.getByLabel('into', { exact: true }).fill('amt_x2')
  await compute.getByLabel('column', { exact: true }).selectOption('amt')
  await compute.getByLabel('operand', { exact: true }).fill('2')

  const res = await compile(page)
  const body = (await res.json()) as {
    output_schema: { name: string }[]
    step_schemas: { name: string }[][]
  }

  // The output reflects BOTH steps: the rename and the derived column.
  const outNames = body.output_schema.map((c) => c.name)
  expect(outNames).toEqual(['staff_id', 'first_name', 'amt', 'amt_x2'])
  expect(outNames).not.toContain('amount')
  await expect(page.getByTestId('transform-output-name')).toHaveText(outNames)
  expect(body.step_schemas.length).toBe(2)
  expect(body.step_schemas[0].map((c) => c.name)).toContain('amt')
  expect(body.step_schemas[1].map((c) => c.name)).toContain('amt_x2')

  // Order is not decoration. Run the compute first and `amt` does not exist
  // yet, so the service refuses — proof the schema folds step by step.
  await compute.getByLabel('Move step earlier').click()
  await expect(stepCard(page, 0)).toContainText('compute')
  const refused = await compile(page)
  expect(refused.status()).toBe(400)

  const detail = ((await refused.json()) as { detail: string }).detail
  expect(detail).toContain('amt')
  await expect(lens(page).getByText(detail, { exact: true })).toBeVisible()
  await expect(statusWord(page)).toContainText('refused')
  // A refusal withdraws the previous answer rather than leaving it on screen.
  await expect(page.getByTestId('transform-output-schema')).toHaveCount(0)
})

test("a later step's column picker offers the columns that exist at that step", async ({
  page,
  h,
}) => {
  const row = { order_id: 1, city: 'lisbon', amount: 5 }
  const ds = await h.seed(
    Array.from({ length: 5 }, (_, i) => ({ ...row, order_id: i + 1, amount: i })),
    'picker',
  )
  const input = Object.keys(row)

  await openTransform(page, ds.id)
  await stepCard(page, 0).getByTestId('transform-step-remove').click()
  await expect(stepCards(page)).toHaveCount(0)

  const compute = await addStep(page, 'compute')
  await compute.getByLabel('node', { exact: true }).selectOption('upper')
  await compute.getByLabel('into', { exact: true }).fill('city_upper')
  await compute.getByLabel('column', { exact: true }).selectOption('city')

  // This step's own picker sees the INPUT schema — `city_upper` does not exist
  // until the step it defines has run.
  expect(await compute.getByLabel('column', { exact: true }).evaluate((el) =>
    Array.from((el as HTMLSelectElement).options).map((o) => o.value),
  )).toEqual(['', ...input])

  const res = await compile(page)
  const folded = ((await res.json()) as { step_schemas: { name: string }[][] }).step_schemas[0].map(
    (c) => c.name,
  )
  expect(folded).toContain('city_upper')

  // A step added after the compile offers the folded schema, invented column
  // and all. (Adding an incomplete step does not change what was compiled, so
  // the folded schemas still describe the draft on screen.)
  const later = await addStep(page, 'drop')
  expect(await chipNames(later, 'transform-column-toggle')).toEqual(folded)
  expect(folded).toEqual([...input, 'city_upper'])

  // And the offer is real: picking the invented column drops it.
  await toggleColumn(later, 'transform-column-toggle', 'city_upper')
  await compile(page)
  await expect(page.getByTestId('transform-output-name')).toHaveText(input)
})

test('an incomplete step is held back rather than sent as a 422', async ({ page, h }) => {
  const ds = await h.seed(
    Array.from({ length: 4 }, (_, i) => ({ sku: `s${i}`, qty: i })),
    'incomplete',
  )

  await openTransform(page, ds.id)
  const sent = watchCompiles(page)
  const statuses: number[] = []
  page.on('response', (r) => {
    if (r.url().includes('/transformations/compile')) statuses.push(r.status())
  })

  // The seed `drop` has no columns; an empty `drop` is a 422 (`min_length=1`),
  // not a no-op. A `rename` with no target name is the same class of thing.
  const rename = await addStep(page, 'rename')
  await rename.getByLabel('column', { exact: true }).selectOption('sku')

  for (const i of [0, 1]) {
    await expect(stepCard(page, i)).toContainText('not sent')
    await expect(stepCard(page, i)).toContainText('Incomplete — held back from the compile.')
  }

  const res = await compile(page)
  expect(res.status()).toBe(200)
  expect(JSON.parse(sent[0].body).steps).toEqual([])

  // Complete the rename; now — and only now — it goes.
  await rename.getByLabel('new name', { exact: true }).fill('stock_keeping_unit')
  await expect(stepCard(page, 1)).not.toContainText('not sent')
  await expect(stepCard(page, 0)).toContainText('not sent')

  await compile(page)
  expect(sent.length).toBe(2)
  expect(JSON.parse(sent[1].body).steps).toEqual([
    { type: 'rename', renames: { sku: 'stock_keeping_unit' } },
  ])
  await expect(page.getByTestId('transform-output-name')).toHaveText(['stock_keeping_unit', 'qty'])

  // Nothing was ever refused: the withholding happened before the request.
  expect(statuses.every((s) => s < 400)).toBe(true)
})

/* --------------------------------------------------------- 3. silent-wrongs */

test('editing a step withdraws the figures the previous compile produced', async ({ page, h }) => {
  const row = { txn_id: 1, region: 'EU', amount: 3, memo: 'x', opened_on: '2026-01-01' }
  const ds = await h.seed(
    Array.from({ length: 5 }, (_, i) => ({ ...row, txn_id: i + 1, amount: i })),
    'stale',
  )
  const input = Object.keys(row)

  await openTransform(page, ds.id)
  const card = stepCard(page, 0)
  await toggleColumn(card, 'transform-column-toggle', 'memo')
  await compile(page)

  const afterFirst = input.filter((c) => c !== 'memo')
  await expect(statusWord(page)).toContainText('compiled')
  await expect(page.getByTestId('transform-output-name')).toHaveText(afterFirst)
  expect(await columnsFigure(page)).toBe(`${input.length}▸${afterFirst.length}`)
  // The step card states its own fold: n columns in ▸ n out.
  await expect(card).toContainText(`${input.length}`)

  // Now edit the step. Everything above was computed from a spec that no
  // longer exists — this is the silent-wrong-answer class: a count quoted
  // confidently from a pipeline you have since changed.
  await toggleColumn(card, 'transform-column-toggle', 'opened_on')

  await expect(statusWord(page)).toContainText('draft changed')
  expect(await columnsFigure(page)).toBe(`${input.length}▸—`)
  expect(await columnsFigure(page)).not.toContain(String(afterFirst.length))
  await expect(page.getByTestId('transform-output-schema')).toContainText('from the previous draft')
  // The per-step fold goes entirely — no "n in ▸ n out", no output block. It
  // described the old spec, so leaving it labelled would still be a wrong
  // answer for anyone who read the number and not the label.
  await expect(card).not.toContainText('Output after this step')
  await expect(card).not.toContainText('▸')

  // Compiling again re-establishes them, at the new figure.
  await compile(page)
  const afterSecond = afterFirst.filter((c) => c !== 'opened_on')
  await expect(statusWord(page)).toContainText('compiled')
  await expect(page.getByTestId('transform-output-name')).toHaveText(afterSecond)
  expect(await columnsFigure(page)).toBe(`${input.length}▸${afterSecond.length}`)
  await expect(page.getByTestId('transform-output-schema')).not.toContainText(
    'from the previous draft',
  )
})

test('a compile refusal shows the problem+json detail, not a generic message', async ({
  page,
  h,
}) => {
  h.allowError(/400 \(Bad Request\)/) // the refusal under test

  const ds = await h.seed(
    Array.from({ length: 4 }, (_, i) => ({ alpha: i, beta: `b${i}` })),
    'refuse',
  )

  // Renaming onto an existing name collides. Ask the service what it says
  // about that, so the expected text is never invented here.
  const steps = [{ type: 'rename', renames: { alpha: 'beta' } }]
  const problem = await api('POST', `/datasets/${ds.id}/transformations/compile`, {
    body: { sheet: 'data', version_selector: { mode: 'current' }, steps },
  })
  expect(problem.status).toBe(400)
  const detail: string = problem.body.detail
  expect(detail).toBeTruthy()

  await openTransform(page, ds.id)
  const rename = await addStep(page, 'rename')
  await rename.getByLabel('column', { exact: true }).selectOption('alpha')
  await rename.getByLabel('new name', { exact: true }).fill('beta')

  const res = await compile(page)
  expect(res.status()).toBe(400)

  await expect(lens(page).getByText(detail, { exact: true })).toBeVisible()
  await expect(statusWord(page)).toContainText('refused')

  // Not the envelope, and not a stand-in for it.
  const panel = await lens(page).innerText()
  expect(panel).not.toContain('Bad Request')
  expect(panel).not.toContain('about:blank')
  expect(panel).not.toMatch(/something went wrong|unexpected error|failed to fetch/i)
})

/* ------------------------------------------------------- 4. saved pipelines */

test('the saved list mirrors the API, and loading one populates the builder', async ({
  page,
  h,
}) => {
  const row = { order_id: 1, region: 'EU', amount: 4, note: 'n' }
  const ds = await h.seed(
    Array.from({ length: 6 }, (_, i) => ({ ...row, order_id: i + 1, amount: i })),
    'saved',
  )

  const tag = Math.random().toString(36).slice(2, 8)
  const twoStep = [
    { type: 'drop', columns: ['note'] },
    { type: 'sort', by: [{ column: 'amount', direction: 'desc' }] },
  ]
  await apiOk('POST', `/datasets/${ds.id}/transformations`, {
    body: { name: `pipe-alpha-${tag}`, sheet: 'data', steps: twoStep },
  })
  await apiOk('POST', `/datasets/${ds.id}/transformations`, {
    body: { name: `pipe-beta-${tag}`, sheet: 'data', steps: [{ type: 'limit', count: 25 }] },
  })

  const listed = await apiOk<{ items: { id: string; name: string; steps: unknown[] }[] }>(
    'GET',
    `/datasets/${ds.id}/transformations`,
  )
  expect(listed.items.length).toBe(2)

  await openTransform(page, ds.id)

  // The list is the API's list — same rows, same step counts, same heading.
  await expect(page.getByTestId('transformation')).toHaveCount(listed.items.length)
  await expect(
    lens(page).getByRole('heading', { name: `Saved pipelines (${listed.items.length})` }),
  ).toBeVisible()
  for (const item of listed.items) {
    const rowEl = page.getByTestId('transformation').filter({ hasText: item.name })
    await expect(rowEl).toHaveCount(1)
    const n = item.steps.length
    await expect(rowEl).toContainText(`${n} step${n === 1 ? '' : 's'}`)
  }

  // Load the two-step one into the builder.
  const alpha = page.getByTestId('transformation').filter({ hasText: `pipe-alpha-${tag}` })
  await alpha.getByTestId('transform-load').click()

  await expect(stepCards(page)).toHaveCount(twoStep.length)
  await expect(lens(page)).toContainText(`Holding “pipe-alpha-${tag}”`)
  await expect(stepCard(page, 0)).toContainText('drop')
  await expect(stepCard(page, 0)).toContainText('note')
  await expect(stepCard(page, 1)).toContainText('sort')
  await expect(stepCard(page, 1)).toContainText('amount desc')

  // The real proof it loaded faithfully: compiling the loaded draft produces
  // what the SAVED steps produce, read fresh from the service.
  const fromSaved = await apiOk<{ output_schema: { name: string }[] }>(
    'POST',
    `/datasets/${ds.id}/transformations/compile`,
    { body: { sheet: 'data', version_selector: { mode: 'current' }, steps: twoStep } },
  )
  const res = await compile(page)
  const fromBuilder = (await res.json()) as { output_schema: { name: string }[] }
  expect(fromBuilder.output_schema.map((c) => c.name)).toEqual(
    fromSaved.output_schema.map((c) => c.name),
  )
  await expect(page.getByTestId('transform-output-name')).toHaveText(
    fromSaved.output_schema.map((c) => c.name),
  )
})

test('a saved pipeline row names the sheet it reads', async ({ page, h }) => {
  // Regression guard. The client's `Transformation` type declared `sheet`, but
  // the API's TransformationOut returns `sheet_key` — so the field was always
  // undefined and every saved row reported "sheet not recorded" for a pipeline
  // whose sheet was very much recorded. A pipeline is pinned to ONE logical
  // sheet, so that line is the only thing telling you which one it runs over.

  const ds = await h.seed([{ a: 1, b: 2 }], 'sheetname')
  const saved = await apiOk<{ sheet_key: string }>('POST', `/datasets/${ds.id}/transformations`, {
    body: {
      name: `pipe-sheet-${Math.random().toString(36).slice(2, 8)}`,
      sheet: 'data',
      steps: [{ type: 'limit', count: 10 }],
    },
  })
  expect(saved.sheet_key).toBe('data')

  await openTransform(page, ds.id)
  const rowEl = page.getByTestId('transformation').first()
  await expect(rowEl).toContainText(saved.sheet_key)
  await expect(rowEl).not.toContainText('sheet not recorded')
})

/* --------------------------------------------------------------- 5. limits */

test('the stated limits are the service’s own — steps, expression depth, rows read', async ({
  page,
  h,
}) => {
  const ds = await h.seed(
    Array.from({ length: 3 }, (_, i) => ({ code: `c${i}`, value: i })),
    'limits',
  )

  // Derive both ceilings from the service refusing to exceed them, rather than
  // repeating the numbers the panel prints.
  const tooManySteps = await api('POST', `/datasets/${ds.id}/transformations/compile`, {
    body: {
      sheet: 'data',
      version_selector: { mode: 'current' },
      steps: Array.from({ length: 51 }, () => ({ type: 'limit', count: 1000 })),
    },
  })
  expect(tooManySteps.status).toBe(422)
  const stepCap = Number(/at most (\d+) items/.exec(JSON.stringify(tooManySteps.body))?.[1])
  expect(stepCap).toBeGreaterThan(0)

  let expr: Record<string, unknown> = { op: 'col', name: 'code' }
  for (let i = 0; i < 20; i += 1) expr = { op: 'str', fn: 'lower', value: expr }
  const tooDeep = await api('POST', `/datasets/${ds.id}/transformations/compile`, {
    body: {
      sheet: 'data',
      version_selector: { mode: 'current' },
      steps: [{ type: 'compute', into: 'deep', expression: expr }],
    },
  })
  expect(tooDeep.status).toBe(400)
  const depthCap = Number(/deeper than (\d+) levels/.exec(String(tooDeep.body.detail))?.[1])
  expect(depthCap).toBeGreaterThan(0)

  await openTransform(page, ds.id)

  const cards = await stepCards(page).count()
  await expect(limitRow(page, 'Steps')).toContainText(`${cards} / ${stepCap}`)
  await expect(page.getByTestId('transform-step-add')).toContainText(`${cards} / ${stepCap} used`)
  await expect(limitRow(page, 'Expression depth')).toContainText(`max ${depthCap}`)
  await expect(limitRow(page, 'Rows read here')).toContainText('0 — never')
  await expect(limitRow(page, 'Scope')).toContainText('single sheet · data')

  // The step counter tracks the pipeline rather than being decorative.
  await addStep(page, 'limit')
  await expect(limitRow(page, 'Steps')).toContainText(`${cards + 1} / ${stepCap}`)
  await expect(page.getByTestId('transform-step-add')).toContainText(
    `${cards + 1} / ${stepCap} used`,
  )

  // "0 rows read" is a claim about the wire; check it against the wire.
  const res = await compile(page)
  const body = (await res.json()) as { rows: unknown[] }
  expect(body.rows).toEqual([])
  await expect(lens(page).getByText('0 rows read · schema only')).toBeVisible()
})

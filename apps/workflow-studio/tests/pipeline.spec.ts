import { test, expect, goto, api, apiOk, uniqueName, API_BASE, ADMIN } from './fixtures'
import type { Locator, Page } from '@playwright/test'

/**
 * The pipeline surfaces: `/ingest`, `/sampling`, `/runs`.
 *
 * These three screens each make a claim about the platform that is easy to say
 * and expensive to get wrong, so every test here is aimed at the claim rather
 * than at the widget:
 *
 *  - **Ingest is ungated.** Nothing is validated, profiled or PII-scanned on the
 *    way in, and a version is immutable once written. So the tests assert both
 *    halves: the copy says it, AND the API agrees (no profile run exists, the
 *    dataset's `validation_status` is `none`, v1's row count is untouched after
 *    a second upload).
 *  - **A sample is a statistic about a population.** Every drawn count must
 *    arrive with the denominator it came out of, and the seed must be stated as
 *    what makes the draw reproducible — so one test runs the same draw twice and
 *    compares the rows rather than trusting the word "reproducible".
 *  - **Most job rows are history, not a queue.** Four of eight types have no
 *    worker, and nothing in the service schedules anything, so `/runs` must not
 *    imply either.
 *
 * Uploads go through the real `<input type=file>` with an in-memory buffer;
 * nothing is written to the repo. Every uploaded file is named through
 * `uniqueName`, so the prefix sweep collects it.
 */

/** Rows → CSV text. The upload names the dataset after the file, which we control. */
function toCsv(rows: Record<string, unknown>[]): string {
  const cols = Object.keys(rows[0])
  return [cols.join(','), ...rows.map((r) => cols.map((c) => String(r[c])).join(','))].join('\n') + '\n'
}

/** Stage a file on the ingest page. Hidden input — Playwright fills it anyway. */
async function stage(page: Page, name: string, body: string | Buffer, mimeType = 'text/csv') {
  await page.getByTestId('ingest-file-input').setInputFiles({
    name,
    mimeType,
    buffer: typeof body === 'string' ? Buffer.from(body, 'utf8') : body,
  })
  await expect(page.getByTestId('ingest-staged')).toContainText(name)
}

const XLSX_MIME = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'

/**
 * The bytes of a seeded workbook, straight from the service.
 *
 * A multi-sheet fixture cannot be built in this file — `seedWorkbook` borrows
 * openpyxl next door — but the version download returns a real `.xlsx` with
 * every sheet intact, which is exactly what a browser upload needs.
 */
async function workbookBytes(datasetId: string, version = 1): Promise<Buffer> {
  const res = await fetch(
    `${API_BASE}/datasets/${datasetId}/versions/${version}/download?format=xlsx`,
    { headers: { 'X-User-Id': ADMIN } },
  )
  if (!res.ok) throw new Error(`workbook download failed: ${res.status}`)
  return Buffer.from(await res.arrayBuffer())
}

/** The queue card for one file, found by the name we gave that file. */
function queueCard(page: Page, fileName: string): Locator {
  return page.getByTestId('ingest-item').filter({ hasText: fileName })
}

/** One `Fact` inside a queue card: an Eyebrow label over a figure. */
function fact(card: Locator, label: string): Locator {
  return card.getByText(label, { exact: true }).locator('..')
}

/** The sampling page's two rails, addressed by text only they contain. */
const drawRail = (page: Page) => page.locator('aside').filter({ hasText: 'Draw sample' })
const reproDock = (page: Page) => page.locator('aside').filter({ hasText: 'Reproducibility' })

/** The artifact filename a sampling run wrote. New on every run, so it is the
 * only reliable signal that a SECOND draw has actually landed. */
async function artifactName(page: Page): Promise<string> {
  return (
    await page.getByTestId('sampling-artifact').locator('span[title]').first().innerText()
  ).trim()
}

/** Wait for a queue card to reach a terminal phase, then return which one. */
async function settledPhase(card: Locator): Promise<string> {
  await expect(card).toHaveAttribute('data-phase', /ready|failed|cancelled/, { timeout: 60_000 })
  return (await card.getAttribute('data-phase')) ?? ''
}

// ---------------------------------------------------------------------------
// /ingest
// ---------------------------------------------------------------------------

test('ingest: a CSV upload creates the dataset the API then reports, with the fixture row count', async ({
  page,
}) => {
  await goto(page, '/ingest')
  await expect(page.getByTestId('ingest-page')).toBeVisible()

  // "New dataset" is the default destination, and it says what that produces.
  await expect(page.getByTestId('ingest-dest-new')).toHaveAttribute('aria-checked', 'true')
  await expect(page.getByTestId('ingest-dest-new')).toContainText(/v1/)

  const name = `${uniqueName('ingest-new')}.csv`
  const rows = [
    { sku: 'A-1', qty: 4 },
    { sku: 'A-2', qty: 9 },
    { sku: 'A-3', qty: 15 },
    { sku: 'A-4', qty: 2 },
  ]
  await stage(page, name, toCsv(rows))
  await page.getByTestId('ingest-start').click()

  const card = queueCard(page, name)
  expect(await settledPhase(card)).toBe('ready')

  // The API is the evidence; the card is the claim. Assert they agree, and
  // that both agree with the fixture.
  const found = await apiOk<{ items: any[] }>('GET', `/datasets?q=${encodeURIComponent(name)}`)
  const ds = found.items.find((d) => d.name === name)
  expect(ds, `no dataset named ${name} after the upload`).toBeTruthy()
  expect(ds.row_count).toBe(rows.length)
  expect(ds.current_version).toBe(1)

  await expect(fact(card, 'Rows')).toContainText(String(ds.row_count))
  await expect(fact(card, 'Columns')).toContainText(String(Object.keys(rows[0]).length))
  await expect(card).toContainText('new dataset')
})

test('ingest: uploading onto a dataset appends an immutable version instead of overwriting', async ({
  page,
  h,
}) => {
  const ds = await h.seed([{ a: 1 }, { a: 2 }], 'ingest-ver')

  await goto(page, `/ingest?dataset=${ds.id}`)

  // The destination fork is where the immutability claim has to be made, since
  // it is the moment a user decides between a new dataset and a new version.
  const versionChoice = page.getByTestId('ingest-dest-version')
  await expect(versionChoice).toHaveAttribute('aria-checked', 'true')
  await expect(versionChoice).toContainText(/immutable/i)
  await expect(versionChoice).toContainText(/Nothing is overwritten/i)

  const name = `${uniqueName('ingest-v2')}.csv`
  await stage(page, name, 'a\n1\n2\n3\n4\n5\n')
  await page.getByTestId('ingest-start').click()

  const card = queueCard(page, name)
  expect(await settledPhase(card)).toBe('ready')
  await expect(card).toContainText('new version')

  // Two versions exist and v1 is untouched — immutability is a claim about the
  // OLD version, so that is what gets asserted.
  const versions = await apiOk<{ items: any[] }>('GET', `/datasets/${ds.id}/versions`)
  expect(versions.items.length).toBe(2)

  const v1 = await apiOk<{ total: number }>(
    'POST',
    `/datasets/${ds.id}/versions/1/sheets/data/query`,
    { body: { limit: 1 } },
  )
  expect(v1.total).toBe(2)

  const v2 = await apiOk<{ total: number }>(
    'POST',
    `/datasets/${ds.id}/versions/2/sheets/data/query`,
    { body: { limit: 1 } },
  )
  expect(v2.total).toBe(5)

  // And the name did not move: appending a version must not rename the dataset
  // after whatever file happened to be uploaded second.
  const after = await apiOk<{ items: any[] }>(
    'GET',
    `/datasets?q=${encodeURIComponent(ds.name)}`,
  )
  expect(after.items.find((d) => d.id === ds.id).name).toBe(ds.name)
})

test('ingest: nothing is validated on the way in, and profiling is an explicit next step', async ({
  page,
}) => {
  await goto(page, '/ingest')

  // Stated before the upload, beside the button that performs it.
  await expect(page.getByTestId('ingest-start').locator('..')).toContainText(
    /Nothing is validated on the way in/i,
  )

  const name = `${uniqueName('ingest-ungated')}.csv`
  await stage(page, name, toCsv([{ amount: 1 }, { amount: 2 }, { amount: 3 }]))
  await page.getByTestId('ingest-start').click()

  const card = queueCard(page, name)
  expect(await settledPhase(card)).toBe('ready')

  // No claim of a check that never ran.
  const cardText = await card.innerText()
  expect(cardText).not.toMatch(/checks? passed/i)
  expect(cardText).not.toMatch(/\bvalidated\b/i)
  expect(cardText).not.toMatch(/validation (passed|complete|ran)/i)
  await expect(card).toContainText(/Nothing was checked/i)
  await expect(card).toContainText(/Profiling never runs on upload/i)

  const found = await apiOk<{ items: any[] }>('GET', `/datasets?q=${encodeURIComponent(name)}`)
  const ds = found.items.find((d) => d.name === name)
  expect(ds).toBeTruthy()
  // The service agrees with the copy: nothing was validated, nothing profiled.
  expect(ds.validation_status).toBe('none')
  const before = await apiOk<{ items: any[] }>(
    'GET',
    `/datasets/${ds.id}/versions/1/profile-runs`,
  )
  expect(before.items.length).toBe(0)

  // The explicit next step is offered, and it works — a toast would not be
  // evidence, so the profile run is re-read from the API.
  await expect(card.getByTestId('ingest-run-profile')).toBeVisible()
  await expect(card.getByTestId('ingest-run-profile')).toBeDisabled()
  await card.getByTestId('ingest-profile-version').fill('1')
  await card.getByTestId('ingest-run-profile').click()

  await expect
    .poll(
      async () =>
        (await apiOk<{ items: any[] }>('GET', `/datasets/${ds.id}/versions/1/profile-runs`)).items
          .length,
      { message: 'the explicit profile run never reached the service' },
    )
    .toBeGreaterThan(0)
})

test('ingest: sheet selection is an opt-in filter, and blank ingests every sheet', async ({
  page,
  h,
}) => {
  // A real multi-sheet workbook, uploaded with no `include_sheets` at all.
  const wb = await h.seedWorkbook(
    {
      'Q3 Detail': [{ region: 'US', amount: 10 }, { region: 'EU', amount: 20 }],
      'Q2 Detail': [{ region: 'US', amount: 3 }],
    },
    'ingest-wb',
  )

  const bytes = await workbookBytes(wb.id)

  await goto(page, '/ingest')

  const sheetField = page.getByTestId('ingest-include-sheets')
  const sheetSection = page.locator('section').filter({ has: sheetField })

  // Opt-in, not a gate: blank is the default, and the copy says what blank does.
  await expect(sheetField).toHaveValue('')
  await expect(sheetSection).toContainText(/opt-in/i)
  await expect(sheetSection).toContainText(/leave it blank and every sheet is ingested/i)
  await expect(sheetSection).toContainText(/sheet-not-found/i)

  // And it does not gate the upload: with the box empty the start control is
  // available, and the upload it performs lands EVERY sheet.
  const name = `${uniqueName('ingest-allsheets')}.xlsx`
  await stage(page, name, bytes, XLSX_MIME)
  await expect(page.getByTestId('ingest-start')).toBeEnabled()
  await page.getByTestId('ingest-start').click()

  const card = queueCard(page, name)
  expect(await settledPhase(card)).toBe('ready')

  const found = await apiOk<{ items: any[] }>('GET', `/datasets?q=${encodeURIComponent(name)}`)
  const ds = found.items.find((d) => d.name === name)
  expect(ds, `no dataset named ${name} after the workbook upload`).toBeTruthy()

  const sheets = await apiOk<{ items: { name: string }[] }>(
    'GET',
    `/datasets/${ds.id}/versions/1/sheets`,
  )
  expect(sheets.items.map((s) => s.name).sort()).toEqual([...wb.sheets].sort())
})

test('ingest: naming a sheet the workbook lacks fails the whole upload with sheet-not-found', async ({
  page,
  h,
}) => {
  // The refusal is the behaviour under test, so its 400 is expected.
  h.allowError(/400 \(Bad Request\)/)

  const wb = await h.seedWorkbook(
    {
      'Q3 Detail': [{ region: 'US', amount: 10 }],
      'Q2 Detail': [{ region: 'EU', amount: 3 }],
    },
    'ingest-wbmiss',
  )
  const bytes = await workbookBytes(wb.id)

  await goto(page, '/ingest')

  const name = `${uniqueName('ingest-missing')}.xlsx`
  await stage(page, name, bytes, XLSX_MIME)
  await page.getByTestId('ingest-include-sheets').fill('Sheet That Does Not Exist')
  await page.getByTestId('ingest-start').click()

  const card = queueCard(page, name)
  expect(await settledPhase(card)).toBe('failed')

  // The filter is real, it fails the WHOLE upload, and the kind is the typed one
  // the sheet copy names — not a generic "upload failed".
  await expect(card).toContainText('sheet-not-found')
  await expect(card).toContainText(/include_sheets matched no non-empty sheets/i)
  for (const sheet of wb.sheets) await expect(card).toContainText(sheet)

  const found = await apiOk<{ items: any[] }>('GET', `/datasets?q=${encodeURIComponent(name)}`)
  expect(found.items.some((d) => d.name === name)).toBe(false)
})

test('ingest: the resumable transport is offered, described as survivable, and actually transfers', async ({
  page,
}) => {
  await goto(page, '/ingest')

  const multipart = page.getByTestId('ingest-transport-multipart')
  const resumable = page.getByTestId('ingest-transport-resumable')

  // Multipart is the default for a small file, and says what a drop costs.
  await expect(multipart).toHaveAttribute('aria-checked', 'true')
  await expect(multipart).toContainText(/a dropped connection means starting over/i)

  // Resumable is offered as a choice and describes surviving the same drop.
  await expect(resumable).toContainText(/tus 1\.0\.0/i)
  await expect(resumable).toContainText(/Survives a dropped connection/i)
  await expect(resumable).toContainText(/continues from exactly there/i)

  await resumable.click()
  await expect(resumable).toHaveAttribute('aria-checked', 'true')
  await expect(multipart).toHaveAttribute('aria-checked', 'false')

  // The claim is only worth as much as the path behind it, so drive it: this
  // is the only test in the suite that uploads over tus rather than multipart.
  const name = `${uniqueName('ingest-tus')}.csv`
  const rows = [{ a: 1 }, { a: 2 }, { a: 3 }, { a: 4 }, { a: 5 }, { a: 6 }]
  await stage(page, name, toCsv(rows))
  await page.getByTestId('ingest-start').click()

  const card = queueCard(page, name)
  await expect(card).toContainText('tus 1.0.0')
  expect(await settledPhase(card)).toBe('ready')

  const found = await apiOk<{ items: any[] }>('GET', `/datasets?q=${encodeURIComponent(name)}`)
  const ds = found.items.find((d) => d.name === name)
  expect(ds, `the tus upload of ${name} produced no dataset`).toBeTruthy()
  expect(ds.row_count).toBe(rows.length)
})

test('ingest: a failed upload shows the service error and its error kind, not a generic message', async ({
  page,
  h,
}) => {
  // The 400 IS the behaviour under test — a refused file is a user error the
  // service types, so the browser logging it is expected rather than noise.
  h.allowError(/400 \(Bad Request\)/)

  // Something the service will refuse at processing time: a .xlsx that is not
  // a workbook. Ask the API first so the expected text is a fresh read rather
  // than a string copied out of the source.
  const probeName = `${uniqueName('ingest-probe')}.xlsx`
  const body = 'a,b\n1,2\n'
  const fd = new FormData()
  fd.append(
    'file',
    new Blob([body], {
      type: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    }),
    probeName,
  )
  const probe = await fetch(`${API_BASE}/upload?sync=true`, {
    method: 'POST',
    headers: { 'X-User-Id': ADMIN },
    body: fd,
  })
  const problem = (await probe.json()) as { detail: string; code: string }
  expect(probe.status).toBe(400)
  expect(problem.code).toBe('invalid-file')

  await goto(page, '/ingest')

  const name = `${uniqueName('ingest-bad')}.xlsx`
  await stage(
    page,
    name,
    body,
    'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
  )
  await page.getByTestId('ingest-start').click()

  const card = queueCard(page, name)
  expect(await settledPhase(card)).toBe('failed')

  // The service's own words and its machine-readable kind, not a house message.
  await expect(card).toContainText(problem.detail)
  await expect(card).toContainText(problem.code)
  expect(await card.innerText()).not.toContain('The upload did not complete.')

  // And it says what happened to the half-made dataset, which the API confirms:
  // a dataset the failed upload created is rolled back.
  await expect(card).toContainText(/rolled back/i)
  const found = await apiOk<{ items: any[] }>('GET', `/datasets?q=${encodeURIComponent(name)}`)
  expect(found.items.some((d) => d.name === name)).toBe(false)

  // The other branch of that sentence: onto an EXISTING dataset the failure is
  // kept as history. The dataset survives, the version number is spent, and v1
  // is untouched — which is exactly what the card claims.
  const ds = await h.seed([{ a: 1 }, { a: 2 }], 'ingest-badver')
  await goto(page, `/ingest?dataset=${ds.id}`)

  const versionName = `${uniqueName('ingest-badver')}.xlsx`
  await stage(
    page,
    versionName,
    body,
    'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
  )
  await page.getByTestId('ingest-start').click()

  const versionCard = queueCard(page, versionName)
  expect(await settledPhase(versionCard)).toBe('failed')
  await expect(versionCard).toContainText(problem.code)
  await expect(versionCard).toContainText(/version number is kept/i)

  const versions = await apiOk<{ items: any[] }>('GET', `/datasets/${ds.id}/versions`)
  expect(versions.items.find((v) => v.version_number === 2).status).toBe('failed')
  const surviving = await apiOk<{ items: any[] }>(
    'GET',
    `/datasets?q=${encodeURIComponent(ds.name)}`,
  )
  const record = surviving.items.find((d) => d.id === ds.id)
  expect(record, 'the pre-existing dataset was rolled back by a failed version').toBeTruthy()
  expect(record.current_version).toBe(1)
  expect(record.row_count).toBe(2)
})

// ---------------------------------------------------------------------------
// /sampling
// ---------------------------------------------------------------------------

/** 60 rows with class sizes we control: 30 US / 20 EU / 10 APAC. */
function populationRows(): Record<string, unknown>[] {
  return Array.from({ length: 60 }, (_, i) => ({
    id: i,
    region: i < 30 ? 'US' : i < 50 ? 'EU' : 'APAC',
    amount: i * 2,
  }))
}

test('sampling: a random draw states the drawn count over the population it came from', async ({
  page,
  h,
}) => {
  const rows = populationRows()
  const ds = await h.seed(rows, 'samp-random')

  await goto(page, `/sampling?dataset=${ds.id}`)

  // The population is stated before anything is drawn.
  await expect(drawRail(page)).toContainText(`${rows.length} rows`)

  await page.getByTestId('sampling-method-random').click()
  await page.getByTestId('sampling-size-count').fill('25')
  await page.getByTestId('sampling-run').click()

  const result = page.getByTestId('sampling-result')
  await expect(result).toBeVisible({ timeout: 30_000 })

  // A denominator, not a bare count — computed here, not copied from the page.
  const drawn = 25
  const pct = ((drawn / rows.length) * 100).toFixed(1)
  await expect(page.getByTestId('sampling-sampled')).toContainText(String(drawn))
  await expect(page.getByTestId('sampling-sampled')).toContainText(`${pct}% of the population`)
  await expect(result).toContainText(`${drawn} of ${rows.length}`)

  // The per-step accounting carries the pool it drew from, too.
  const step = page.getByTestId('sampling-step-row').first()
  await expect(step).toContainText('random')
  await expect(step).toContainText(String(rows.length))

  // The full sample is the artifact, so prove the artifact is real: the name on
  // screen must resolve to a registered `sample_output` row for this dataset.
  await expect(page.getByTestId('sampling-artifact')).toBeVisible()
  const filename = await artifactName(page)
  expect(filename).toMatch(/\.parquet$/)

  const listed = await apiOk<{ items: any[] }>('GET', '/samples?limit=1000')
  const registered = listed.items.find((a) => a.filename === filename)
  expect(registered, `${filename} is not registered in the artifacts table`).toBeTruthy()
  expect(registered.file_type).toBe('sample_output')
  expect(registered.dataset_id).toBe(ds.id)
})

test('sampling: an over-draw says the draw stops at the population, before it runs', async ({
  page,
  h,
}) => {
  const rows = populationRows()
  const ds = await h.seed(rows, 'samp-over')

  await goto(page, `/sampling?dataset=${ds.id}`)
  await expect(drawRail(page)).toContainText(`${rows.length} rows`)

  const over = rows.length * 10
  await page.getByTestId('sampling-size-count').fill(String(over))

  // Said BEFORE the run, and it names both the ceiling and the flag that lifts
  // it. Nothing has been drawn at this point.
  const rail = drawRail(page)
  await expect(rail).toContainText(`${over} rows is more than the sheet holds`)
  await expect(rail).toContainText(
    new RegExp(`Without replace the draw stops at ${rows.length}`, 'i'),
  )
  await expect(page.getByTestId('sampling-replace')).not.toBeChecked()

  // The warning is a warning, not a block: the draw is still offered.
  await expect(page.getByTestId('sampling-run')).toBeEnabled()
  await expect(page.getByTestId('sampling-result')).toHaveCount(0)

  // The sentence is testable, so test it. Without `replace`, the draw stops at
  // the population and the shortfall is reported rather than hidden.
  await page.getByTestId('sampling-run').click()
  const result = page.getByTestId('sampling-result')
  await expect(result).toBeVisible({ timeout: 30_000 })
  await expect(result).toContainText(`${rows.length} of ${rows.length}`)
  await expect(page.getByTestId('sampling-goal-warnings')).toContainText(/not reached/i)

  // ...and `replace` is what lifts the ceiling, exactly as the copy says.
  const capped = await artifactName(page)
  await page.getByTestId('sampling-replace').click()
  await expect(page.getByTestId('sampling-replace')).toBeChecked()
  await page.getByTestId('sampling-run').click()
  await expect
    .poll(async () => artifactName(page), { message: 'the replace draw never landed' })
    .not.toBe(capped)
  await expect(page.getByTestId('sampling-sampled')).toContainText(String(over))
})

test('sampling: the seed is stated as what makes a draw reproducible, and blank warns', async ({
  page,
  h,
}) => {
  const rows = populationRows()
  const ds = await h.seed(rows, 'samp-seed')

  await goto(page, `/sampling?dataset=${ds.id}`)

  const seedField = page.getByTestId('sampling-seed')
  const configured = await seedField.inputValue()
  expect(configured).not.toBe('')

  // The seed the run will use is shown in the reproducibility dock, and the
  // dock's value is the field's value — not a second, independent number.
  const dock = reproDock(page)
  await expect(dock).toContainText(configured)
  await expect(dock).toContainText(/Same seed and same configuration reproduce the same sample/i)

  // A blank seed is called out as unreproducible, in both places that mention it.
  await seedField.fill('')
  await expect(dock).toContainText(/cannot reconstruct this one|different rows on every run/i)
  await expect(drawRail(page)).toContainText(/this draw cannot be reproduced/i)
  await expect(drawRail(page)).toContainText(/does not invent a seed/i)

  // Then prove the claim rather than quoting it: the same seed and the same
  // configuration, run twice, must return the same rows.
  await seedField.fill(configured)
  await page.getByTestId('sampling-size-count').fill('8')
  await page.getByTestId('sampling-run').click()
  await expect(page.getByTestId('sampling-result')).toBeVisible({ timeout: 30_000 })
  const first = await page.getByTestId('sampling-preview').innerText()
  const firstArtifact = await artifactName(page)

  // A second run writes a new artifact, so waiting for the filename to change
  // is what distinguishes "ran again" from "still showing the first result" —
  // comparing the preview before the second draw landed would pass for free.
  await page.getByTestId('sampling-run').click()
  await expect
    .poll(async () => artifactName(page), { message: 'the second draw never landed' })
    .not.toBe(firstArtifact)
  const second = await page.getByTestId('sampling-preview').innerText()

  expect(second).toBe(first)
  await expect(page.getByTestId('sampling-result')).toContainText(`seed ${configured}`)
})

test('sampling: a stratified draw reports every class with its share of the population', async ({
  page,
  h,
}) => {
  const rows = populationRows()
  const ds = await h.seed(rows, 'samp-strat')

  // The class sizes are ours, so the expected per-stratum counts are known.
  const expected = new Map<string, number>()
  for (const r of rows) {
    const key = String(r.region)
    expected.set(key, (expected.get(key) ?? 0) + 1)
  }

  await goto(page, `/sampling?dataset=${ds.id}`)
  await page.getByTestId('sampling-method-stratified').click()
  await page.getByTestId('sampling-stratify-column').selectOption('region')

  // Each class carries its own denominator — `n of N`, never a bare count.
  const strata = page.getByTestId('sampling-strata')
  await expect(strata).toBeVisible({ timeout: 30_000 })
  for (const [cls, count] of expected) {
    await expect(strata.locator('div').filter({ hasText: cls }).first()).toContainText(
      `${count} of ${rows.length}`,
    )
  }

  // A balanced allocation names its classes, so the goal table must report each
  // one's target and what it actually got.
  await page.getByTestId('sampling-allocation-balanced').click()
  await page.getByTestId('sampling-size-count').fill('30')
  await page.getByTestId('sampling-run').click()
  await expect(page.getByTestId('sampling-result')).toBeVisible({ timeout: 30_000 })

  const goalRows = page.getByTestId('sampling-goal-row')
  await expect(goalRows).toHaveCount(expected.size)
  const perClass = 30 / expected.size
  for (const cls of expected.keys()) {
    const row = goalRows.filter({ hasText: cls })
    await expect(row).toHaveCount(1)
    await expect(row).toContainText(String(perClass))
    await expect(row).toContainText('met')
  }

  // And the sample as a whole still states its population.
  await expect(page.getByTestId('sampling-result')).toContainText(`30 of ${rows.length}`)
})

test('sampling: the artifact states its retention and nothing claims a sweep on a timer', async ({
  page,
  h,
}) => {
  const ds = await h.seed(populationRows().slice(0, 20), 'samp-retain')

  await goto(page, `/sampling?dataset=${ds.id}`)
  await page.getByTestId('sampling-size-count').fill('5')
  await page.getByTestId('sampling-run').click()
  await expect(page.getByTestId('sampling-result')).toBeVisible({ timeout: 30_000 })

  // The artifact's own clock, and the export's shorter one.
  const artifact = page.getByTestId('sampling-artifact')
  await expect(artifact).toContainText(/sample_output/)
  await expect(artifact).toContainText(/kept 30 days/i)
  await expect(artifact).toContainText(/7 days against the sample's 30/i)

  // "Past window" means eligible for collection, not collected.
  const dock = reproDock(page)
  await expect(dock).toContainText(/Nothing runs the sweep on a schedule/i)
  await expect(dock).toContainText(/eligible for collection, not collected/i)

  // Nothing promises a cadence that does not exist.
  const dockText = await dock.innerText()
  expect(dockText).not.toMatch(/nightly|hourly|every \d+ (hours|days|minutes)/i)
  expect(dockText).not.toMatch(/automatically (deleted|removed|purged)/i)
  expect(dockText).not.toMatch(/expires (on|at) /i)
})

test('sampling: a refusal for a viewer is a state, not an error box', async ({ page, h }) => {
  const ds = await h.seed(
    Array.from({ length: 10 }, (_, i) => ({ id: i, ssn: `SECRET-SAMPLE-${i}` })),
    'samp-refuse',
  )
  await h.markSensitive(ds.id, 'ssn')

  // /profile and /sample are both gated by `ensure_raw_access`, so the 403 is
  // the behaviour under test rather than a fault.
  h.allowError(/403 \(Forbidden\)/)

  const viewer = await h.viewer()
  await h.asSeat(page, viewer.user_id, 'Viewer')
  await goto(page, `/sampling?dataset=${ds.id}`)

  // A first-class state: named, explained, and with the action withdrawn.
  await expect(page.getByText('Restricted for this seat')).toBeVisible()
  await expect(page.getByTestId('sampling-run')).toBeDisabled()
  await expect(drawRail(page)).toContainText(/sampling is refused for this seat/i)
  await expect(page.getByTestId('sampling-result')).toHaveCount(0)

  // Not an error box, and not a leak of the raw values it is protecting.
  const bodyText = await page.locator('body').innerText()
  expect(bodyText).not.toContain('SECRET-SAMPLE')
  expect(bodyText).not.toMatch(/Forbidden|403|sensitive-data-restricted/)

  // The refusal is the real one, with the code the UI branches on.
  const refused = await api('POST', '/sample', {
    body: {
      dataset_id: ds.id,
      version_number: 1,
      sheet: 'data',
      target_total_volume: 5,
      sampling_steps: [{ method: 'random', sample_size: 5 }],
    },
    userId: viewer.user_id,
  })
  expect(refused.status).toBe(403)
  expect(refused.body.code).toBe('sensitive-data-restricted')
})

// ---------------------------------------------------------------------------
// /runs
// ---------------------------------------------------------------------------

/** Every visible row of the runs table. */
function runRows(page: Page): Locator {
  return page.getByTestId('runs-table').locator('tbody tr')
}

test('runs: an upload and a validation both appear, and the rows match GET /jobs', async ({
  page,
  h,
}) => {
  // Two known actions, each of which the service records as a job row.
  const ds = await h.seed([{ amount: 1 }, { amount: 2 }, { amount: 3 }], 'runs-actions')
  await apiOk('POST', `/datasets/${ds.id}/rules`, {
    body: {
      name: `runs-rule-${Math.random().toString(36).slice(2, 8)}`,
      rule_type: 'not_null',
      sheet_selector: 'data',
      column_selector: 'amount',
    },
  })
  await apiOk('POST', `/datasets/${ds.id}/versions/1/validate`, { body: {} })

  const mine = async () => {
    const jobs = await apiOk<{ items: any[] }>('GET', '/jobs?limit=50&offset=0')
    return jobs.items.filter((j) => j.dataset_id === ds.id)
  }
  const recorded = await mine()
  const byType = new Map(recorded.map((j) => [j.job_type, j]))
  // The two actions we performed are recorded. Not an exact set: this install
  // is shared, and a webhook subscription another test happens to have live
  // will attach a `webhook_delivery` row to our validation.
  expect([...byType.keys()]).toContain('import')
  expect([...byType.keys()]).toContain('validation')

  await goto(page, '/runs')

  // The table is global, so narrow it to our fixture by name — the runs page
  // resolves dataset ids to names, and this name is unique to this test.
  await page.getByTestId('runs-search').fill(ds.name)

  // Both actions are on screen, by id rather than by position.
  await expect(page.getByTestId(`runs-row-${byType.get('import').id}`)).toBeVisible({
    timeout: 20_000,
  })
  await expect(page.getByTestId(`runs-row-${byType.get('validation').id}`)).toBeVisible()

  // And the row count for this dataset agrees with a fresh read of /jobs. Both
  // sides are re-read each attempt: the page polls, and the service may add a
  // row for our dataset while we look at it.
  await expect
    .poll(
      async () => {
        const api = (await mine()).length
        const dom = await runRows(page).count()
        return dom === api ? 'match' : `dom=${dom} api=${api}`
      },
      { message: 'the table never agreed with GET /jobs' },
    )
    .toBe('match')

  // The mode word is a property of the job type, so it must be derivable from
  // the type alone — the four worker-backed types say `worker`, the rest say
  // `in-request`, and neither is a claim about this particular row.
  const workerTypes = new Set([
    'transform',
    'relationship_discovery',
    'artifact_gc',
    'webhook_delivery',
  ])
  const shown = await runRows(page).evaluateAll((rows) =>
    rows.map((r) => {
      const cells = r.querySelectorAll('td')
      return {
        type: cells[1]?.textContent?.trim() ?? '',
        mode: cells[2]?.textContent?.trim() ?? '',
        dataset: cells[3]?.textContent?.trim() ?? '',
      }
    }),
  )
  expect(shown.length).toBeGreaterThan(0)
  for (const row of shown) {
    expect(row.dataset).toContain(ds.name)
    expect(row.mode).toBe(workerTypes.has(row.type) ? 'worker' : 'in-request')
  }
  expect(shown.map((r) => r.type)).toContain('import')
  expect(shown.map((r) => r.type)).toContain('validation')
})

test('runs: the page states the execution model and never calls an in-request row queued', async ({
  page,
  h,
}) => {
  // Our own row, so the assertions below are never vacuous on an empty table.
  const ds = await h.seed([{ a: 1 }], 'runs-model')
  const seeded = await apiOk<{ items: any[] }>('GET', '/jobs?limit=50&offset=0')
  expect(seeded.items.some((j) => j.dataset_id === ds.id && j.job_type === 'import')).toBe(true)

  await goto(page, '/runs')

  const model = page.getByTestId('runs-execution-model')
  await expect(model).toBeVisible()

  // Four of eight have a worker; the other four are execution records.
  await expect(model).toContainText('Worker-backed · 4 of 8')
  await expect(model).toContainText('In-request · 4 of 8')
  await expect(model).toContainText(/there is no queue behind it/i)
  await expect(page.getByTestId('runs-note-sync')).toContainText(/defaults to/i)

  // The mode column exists and only ever says one of the two words.
  const modes = await runRows(page).evaluateAll((rows) =>
    rows.map((r) => r.querySelectorAll('td')[2]?.textContent?.trim() ?? ''),
  )
  // Guard against an empty table making the loop below vacuous.
  expect(modes.length).toBeGreaterThan(0)
  for (const mode of modes) expect(['worker', 'in-request']).toContain(mode)

  // A `pending` row of a handler-less type must never be called "queued": the
  // worker's claim query cannot see it. Assert on whatever rows exist rather
  // than manufacturing one, since only the service can strand a row.
  const rows = await runRows(page).evaluateAll((trs) =>
    trs.map((r) => {
      const cells = r.querySelectorAll('td')
      return {
        mode: cells[2]?.textContent?.trim() ?? '',
        status: cells[4]?.textContent?.trim() ?? '',
      }
    }),
  )
  for (const row of rows) {
    if (row.mode === 'in-request') expect(row.status).not.toBe('queued')
  }
})

test('runs: there is no scheduler, so nothing renders a next run', async ({ page }) => {
  await goto(page, '/runs')

  const note = page.getByTestId('runs-note-scheduler')
  await expect(note).toContainText(/no scheduler and no cron/i)
  await expect(note).toContainText(/artifact_gc/)

  // The only place "next run" may appear is that note, saying there isn't one.
  // Anything else would be a cadence the service cannot honour.
  const body = await page.locator('body').innerText()
  const noteText = await note.innerText()
  const occurrences = (s: string) => (s.match(/next run/gi) ?? []).length
  expect(occurrences(body)).toBe(occurrences(noteText))
  expect(body).not.toMatch(/next run (at|in|on) /i)
  expect(body).not.toMatch(/scheduled (for|at) /i)
  expect(body).not.toMatch(/runs every \d/i)
})

test('runs: filters narrow the table and the dock shows the selected run payload', async ({
  page,
  h,
}) => {
  const ds = await h.seed([{ amount: 5 }, { amount: 6 }], 'runs-filter')
  await apiOk('POST', `/datasets/${ds.id}/rules`, {
    body: {
      name: `runs-filter-rule-${Math.random().toString(36).slice(2, 8)}`,
      rule_type: 'not_null',
      sheet_selector: 'data',
      column_selector: 'amount',
    },
  })
  await apiOk('POST', `/datasets/${ds.id}/versions/1/validate`, { body: {} })

  const jobs = await apiOk<{ items: any[] }>('GET', '/jobs?limit=50&offset=0')
  const validation = jobs.items.find(
    (j) => j.dataset_id === ds.id && j.job_type === 'validation',
  )
  const importJob = jobs.items.find((j) => j.dataset_id === ds.id && j.job_type === 'import')
  expect(validation, 'no validation job recorded for the fixture').toBeTruthy()
  expect(importJob, 'no import job recorded for the fixture').toBeTruthy()

  await goto(page, '/runs')
  await page.getByTestId('runs-search').fill(ds.name)
  await expect(page.getByTestId(`runs-row-${validation.id}`)).toBeVisible({ timeout: 20_000 })
  await expect(page.getByTestId(`runs-row-${importJob.id}`)).toBeVisible()

  // The server-side filter must actually narrow: our import row leaves, our
  // validation row stays, and nothing of another type survives.
  await page.getByTestId('runs-type-filter').selectOption('validation')
  await page.getByTestId('runs-search').fill(ds.name)
  await expect(page.getByTestId(`runs-row-${validation.id}`)).toBeVisible({ timeout: 20_000 })
  await expect(page.getByTestId(`runs-row-${importJob.id}`)).toHaveCount(0)

  const types = await runRows(page).evaluateAll((rows) =>
    rows.map((r) => r.querySelectorAll('td')[1]?.textContent?.trim() ?? ''),
  )
  expect(types.length).toBeGreaterThan(0)
  for (const t of types) expect(t).toBe('validation')

  // The status filter stacks with it, and it is server-side too — a completed
  // filter that left a pending row on screen would be a filter in name only.
  await page.getByTestId('runs-status-filter').selectOption('completed')
  await page.getByTestId('runs-search').fill(ds.name)
  await expect(page.getByTestId(`runs-row-${validation.id}`)).toBeVisible({ timeout: 20_000 })
  const statuses = await runRows(page).evaluateAll((rows) =>
    rows.map((r) => r.querySelectorAll('td')[4]?.textContent?.trim() ?? ''),
  )
  expect(statuses.length).toBeGreaterThan(0)
  for (const s of statuses) expect(s).toBe('completed')

  // A filter that matches nothing says the filter is the reason, and says the
  // text box only narrows the page it holds.
  await page.getByTestId('runs-search').fill('no-such-run-zzz')
  await expect(page.getByTestId('runs-empty')).toContainText(
    /only narrows the loaded rows|no search parameter/i,
  )
  await page.getByTestId('runs-search').fill(ds.name)
  await expect(page.getByTestId(`runs-row-${validation.id}`)).toBeVisible()

  // The dock starts empty and says so, rather than showing an arbitrary run.
  await expect(page.getByTestId('runs-detail-empty')).toBeVisible()

  await page.getByTestId(`runs-row-${validation.id}`).click()
  await expect(page.getByTestId('runs-detail-id')).toHaveText(validation.id)

  // The payload is the row's own result, checked against a fresh read.
  const fresh = await apiOk<any>('GET', `/jobs/${validation.id}`)
  const payload = page.getByTestId('runs-detail-result')
  await expect(payload).toBeVisible()
  for (const key of Object.keys(fresh.result ?? {})) {
    await expect(payload).toContainText(key)
  }
  await expect(payload).toContainText(String(fresh.result.rules_total))
  await expect(page.getByTestId('runs-detail-mode-note')).toContainText(
    /no registered worker handler/i,
  )
})

test('runs: no timestamp ever renders as Invalid Date', async ({ page, h }) => {
  const ds = await h.seed([{ a: 1 }], 'runs-time')

  const jobs = await apiOk<{ items: any[] }>('GET', '/jobs?limit=50&offset=0')
  const mine = jobs.items.find((j) => j.dataset_id === ds.id)
  expect(mine, 'the seed upload recorded no job').toBeTruthy()

  await goto(page, '/runs')
  await expect(page.getByTestId(`runs-row-${mine.id}`)).toBeVisible({ timeout: 20_000 })

  // Every `started` cell is a real clock time or an em dash — never the string
  // JavaScript produces from an unparseable date.
  const started = await runRows(page).evaluateAll((rows) =>
    rows.map((r) => r.querySelectorAll('td')[6]?.textContent?.trim() ?? ''),
  )
  expect(started.length).toBeGreaterThan(0)
  for (const cell of started) {
    expect(cell).not.toBe('Invalid Date')
    expect(cell === '—' || /\d/.test(cell)).toBe(true)
  }

  await page.getByTestId(`runs-row-${mine.id}`).click()
  const detail = page.getByTestId('runs-detail')
  await expect(detail.getByTestId('runs-detail-id')).toHaveText(mine.id)
  expect(await detail.innerText()).not.toContain('Invalid Date')
  expect(await page.locator('body').innerText()).not.toContain('Invalid Date')
})

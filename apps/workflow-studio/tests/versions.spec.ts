import { execFileSync } from 'node:child_process'
import { readFileSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import type { Page } from '@playwright/test'
import { test, expect, goto, api, apiOk, uniqueName, API_BASE, ADMIN, pickScope } from './fixtures'

/**
 * The versions lens — the ledger and the pointers into it.
 *
 * `lenses.spec.ts` already proves the panel renders two versions and pins a tag
 * badge to the right row; `writes.spec.ts` already proves a tag write persists
 * and that promote refuses an unvalidated version. Nothing here repeats either.
 * What is under test is the part of the lens that makes a CLAIM about history:
 *
 *   - immutability, asserted against v1's rows *after* v2 exists rather than
 *     against a sentence in the footnote;
 *   - every delta, share and count computed from a fresh API read — the row
 *     delta, the schema diff, the row-level diff and its denominators;
 *   - the two ledgers that outlive the thing they describe: a tag's transition
 *     history and the dataset timeline;
 *   - the two refusals that are features, not faults: the promote quality gate,
 *     and `diff-key-required`, which the lens must render as a key PICKER;
 *   - the raw-data gate on download, from a seat that does not have it.
 *
 * Every fixture is seeded here with known answers and read back from the API
 * before it is asserted against the DOM. No number below is a literal that was
 * copied out of a passing run.
 */

/* ----------------------------------------------------------------- helpers */

/** One row of the immutable ledger, found by its exact `vN` identifier. */
function versionEntry(page: Page, n: number) {
  return page.getByTestId('version-entry').filter({ has: page.getByText(`v${n}`, { exact: true }) })
}

/** A tag's card in the Tags section. */
function tagRow(page: Page, tag: string) {
  return page.getByTestId('tag-entry').filter({ hasText: tag })
}

/**
 * The `→ vN` pointer inside a tag card, anchored so it cannot match the
 * "Promote → vN" button sitting three lines below it.
 */
function tagPointer(page: Page, tag: string) {
  return tagRow(page, tag).getByText(/^→ v\d+$/)
}

/**
 * A `<Stat>` row by name. Anchored, because the panel renders both `changed`
 * and `unchanged` and an unanchored match would silently take whichever came
 * first — which is exactly the class of selector bug rule 4 exists for.
 */
function stat(page: Page, name: string) {
  return page
    .getByTestId('lens-body')
    .locator('[data-slot="stat"]')
    .filter({ hasText: new RegExp(`^${name}`) })
}

/**
 * `coverageNote()` as the panel computes it. Restated rather than imported
 * because these tests must not depend on the module they are checking — the
 * inputs still come from a fresh API read, so nothing here is a literal.
 */
function note(counted: number, total: number, unit = 'rows'): string {
  const pct = ((counted / total) * 100).toFixed(1).replace(/\.0$/, '')
  return `over ${pct}% · ${counted.toLocaleString()} ${unit}`
}

/** The signed delta the lens prints — `−` is U+2212, not a hyphen. */
function signed(n: number): string {
  if (n === 0) return '±0'
  return `${n > 0 ? '+' : '−'}${Math.abs(n).toLocaleString()}`
}

/**
 * A real multi-sheet .xlsx.
 *
 * `h.seedWorkbook` builds the first version but there is no harness call that
 * adds an .xlsx *version*, and a rename candidate cannot exist without one — a
 * CSV upload always lands one sheet called `data`, so two CSV versions can
 * never produce a removed+added sheet pair. Same openpyxl the harness borrows.
 */
const VENV_PYTHON =
  process.env.ANALYTICS_PYTHON ||
  join(
    dirname(fileURLToPath(import.meta.url)),
    '..',
    '..',
    'analytics-service',
    'venv',
    'bin',
    'python',
  )

function buildWorkbook(sheets: Record<string, Record<string, unknown>[]>): Buffer {
  const out = join(tmpdir(), `uitest-versions-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}.xlsx`)
  const script = `
import json, sys
from openpyxl import Workbook
sheets = json.loads(sys.argv[1])
wb = Workbook()
wb.remove(wb.active)
for name, rows in sheets.items():
    ws = wb.create_sheet(title=name)
    if rows:
        cols = list(rows[0].keys())
        ws.append(cols)
        for r in rows:
            ws.append([r.get(c) for c in cols])
wb.save(sys.argv[2])
`
  execFileSync(VENV_PYTHON, ['-c', script, JSON.stringify(sheets), out], { stdio: 'pipe' })
  const bytes = readFileSync(out)
  rmSync(out, { force: true })
  return bytes
}

/** Upload a workbook, either as a new dataset or as a version of one. */
async function uploadWorkbook(
  sheets: Record<string, Record<string, unknown>[]>,
  name: string,
  datasetId?: string,
): Promise<string> {
  const fd = new FormData()
  fd.append(
    'file',
    new Blob([new Uint8Array(buildWorkbook(sheets))], {
      type: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    }),
    `${name}.xlsx`,
  )
  if (datasetId) fd.append('dataset_id', datasetId)
  const res = await fetch(`${API_BASE}/upload?sync=true`, {
    method: 'POST',
    headers: { 'X-User-Id': ADMIN },
    body: fd,
  })
  if (!res.ok) throw new Error(`workbook upload failed: ${res.status} ${(await res.text()).slice(0, 300)}`)
  return (await res.json()).dataset_id as string
}

const rows = (n: number, from = 1) =>
  Array.from({ length: n }, (_, i) => ({ id: from + i, name: `n${from + i}`, score: (from + i) * 10 }))

/* ------------------------------------------------------------ immutability */

test('a new version leaves the old one exactly as it was, and the panel says so', async ({
  page,
  h,
}) => {
  const first = rows(5)
  const second = rows(8)
  const ds = await h.seed(first, 'immutable')
  await h.addVersion(ds.id, second)

  await goto(page, `/data?dataset=${ds.id}`)
  await page.getByTestId('lens-versions').click()

  await expect(page.getByTestId('version-entry')).toHaveCount(2)

  // The claim, tested rather than read: v1 still holds its original rows even
  // though v2 now exists. A version is never edited, so an upload that had
  // overwritten in place would show up right here.
  const v1 = await apiOk<{ total: number }>(
    'POST',
    `/datasets/${ds.id}/versions/1/sheets/data/query`,
    { body: { limit: 1 } },
  )
  const v2 = await apiOk<{ total: number }>(
    'POST',
    `/datasets/${ds.id}/versions/2/sheets/data/query`,
    { body: { limit: 1 } },
  )
  expect(v1.total).toBe(first.length)
  expect(v2.total).toBe(second.length)

  // And nothing deletes one: both are still listed, both still `ready`.
  const listed = await apiOk<{ items: { version_number: number; status: string }[] }>(
    'GET',
    `/datasets/${ds.id}/versions`,
  )
  expect(listed.items.map((v) => v.version_number).sort()).toEqual([1, 2])
  expect(listed.items.every((v) => v.status === 'ready')).toBe(true)

  // The panel states the invariant in both registers it owns.
  await expect(page.getByTestId('lens-body')).toContainText(
    'Versions are immutable — new data is a new version, and nothing deletes one.',
  )
  await expect(page.getByTestId('lens-body')).toContainText('immutable')

  // v1 carries no delta, because there is no earlier ready version to compare
  // it against — an invented "±0" here would be a claim about a comparison that
  // was never made.
  await expect(versionEntry(page, 1)).not.toContainText('rows')
})

test('the row delta between two versions is the difference between them, sign and all', async ({
  page,
  h,
}) => {
  // Deliberately SHRINKING, so the minus sign and the negative percentage are
  // exercised. A growing fixture would pass with an `Math.abs` bug in place.
  const ds = await h.seed(rows(9), 'delta')
  await h.addVersion(ds.id, rows(4))

  await goto(page, `/data?dataset=${ds.id}`)
  await page.getByTestId('lens-versions').click()

  // Expectation from the service's own row counts, not from the fixture length.
  const listed = await apiOk<{ items: { version_number: number; row_count: number }[] }>(
    'GET',
    `/datasets/${ds.id}/versions`,
  )
  const r1 = listed.items.find((v) => v.version_number === 1)!.row_count
  const r2 = listed.items.find((v) => v.version_number === 2)!.row_count
  const delta = r2 - r1
  expect(delta).toBeLessThan(0)

  const pct = `${delta >= 0 ? '+' : '−'}${Math.abs((delta / r1) * 100).toFixed(2)}%`
  await expect(page.getByTestId('lens-body').locator('[data-slot="metric"]')).toContainText(
    `${signed(delta)} vs v1 (${pct})`,
  )

  // The same delta again on the ledger row, where it is measured against the
  // previous ready version rather than against the diff base.
  await expect(versionEntry(page, 2)).toContainText(`${signed(delta)} rows`)

  // And the workbook diff agrees, so the two numbers cannot drift apart.
  const diff = await apiOk<{ modified: { row_count_delta: number }[] }>(
    'GET',
    `/datasets/${ds.id}/versions/1/diff/2`,
  )
  expect(diff.modified[0].row_count_delta).toBe(delta)
})

/* -------------------------------------------------------------- schema diff */

test('the schema diff names the added column and the column whose type moved', async ({
  page,
  h,
}) => {
  // v1: score is an integer. v2: score is text, and `region` is new. Both
  // changes are deliberate, and neither is guessable from the row count.
  const ds = await h.seed(
    Array.from({ length: 5 }, (_, i) => ({ id: i + 1, name: `n${i + 1}`, score: (i + 1) * 10 })),
    'schemadiff',
  )
  await h.addVersion(
    ds.id,
    Array.from({ length: 8 }, (_, i) => ({
      id: i + 1,
      name: `n${i + 1}`,
      score: i % 2 === 0 ? 'high' : 'low',
      region: ['EU', 'US', 'APAC'][i % 3],
    })),
  )

  await goto(page, `/data?dataset=${ds.id}`)
  await page.getByTestId('lens-versions').click()

  // The workbook level: one sheet, modified, schema changed.
  const workbook = await apiOk<{
    added: unknown[]
    removed: unknown[]
    modified: { to_sheet: string; schema_changed: boolean; row_count_delta: number }[]
  }>('GET', `/datasets/${ds.id}/versions/1/diff/2`)
  expect(workbook.modified.length).toBe(1)
  expect(workbook.modified[0].schema_changed).toBe(true)

  const sheetRow = page.getByTestId('version-diff-sheet')
  await expect(sheetRow).toHaveCount(workbook.modified.length)
  await expect(sheetRow).toContainText(workbook.modified[0].to_sheet)
  await expect(sheetRow).toContainText(`${signed(workbook.modified[0].row_count_delta)} rows`)
  await expect(sheetRow).toContainText('schema changed')

  // The header states which pair is being compared, so the numbers are attached
  // to a direction rather than floating.
  await expect(page.getByTestId('lens-body')).toContainText('v1 → v2')

  // The column level, cross-checked against the endpoint the lens calls.
  const cols = await apiOk<{
    added_columns: { name: string; dtype: string; nullable: boolean }[]
    removed_columns: unknown[]
    type_changes: { column: string; from_dtype: string; to_dtype: string }[]
  }>('GET', `/datasets/${ds.id}/versions/1/sheets/data/diff/2`)
  expect(cols.added_columns.length).toBe(1)
  expect(cols.type_changes.length).toBe(1)

  const added = cols.added_columns[0]
  const typed = cols.type_changes[0]

  const changes = await page
    .getByTestId('version-diff-change')
    .evaluateAll((els) => els.map((e) => e.textContent ?? ''))
  expect(changes.length).toBe(cols.added_columns.length + cols.type_changes.length)

  // The added column, with the word "added" and its dtype — the word is what
  // makes the reading survive greyscale, so assert on it, not on a colour.
  expect(
    changes.some((t) => t.includes(added.name) && t.includes('added') && t.includes(added.dtype)),
    `no "added" line for ${added.name} in ${JSON.stringify(changes)}`,
  ).toBe(true)

  // The type change, carrying both dtypes in the direction they moved.
  expect(
    changes.some(
      (t) =>
        t.includes(typed.column) &&
        t.includes('type') &&
        t.includes(`${typed.from_dtype} → ${typed.to_dtype}`),
    ),
    `no "type" line for ${typed.column} in ${JSON.stringify(changes)}`,
  ).toBe(true)
})

/* --------------------------------------------------------------------- tags */

test('a tag pins to a whole version and stays there while a newer one is on screen', async ({
  page,
  h,
}) => {
  const ds = await h.seed(rows(3), 'tagpin')
  await h.addVersion(ds.id, rows(6))
  const tag = `pinned-${Math.random().toString(36).slice(2, 8)}`
  await apiOk('PUT', `/datasets/${ds.id}/tags`, { body: { tag_name: tag, version_number: 1 } })

  await goto(page, `/data?dataset=${ds.id}`)
  await page.getByTestId('lens-versions').click()

  // The page opens on the newest version; the tag must not follow it.
  // The picker is a listbox trigger, not an input — assert its rendered value.
  await expect(page.getByLabel('Version')).toHaveText(/^v2\b/)
  await expect(tagPointer(page, tag)).toHaveText('→ v1')
  await expect(tagRow(page, tag)).toContainText('1 version behind the newest')

  // The badge sits on the version it points at, and on no other.
  await expect(versionEntry(page, 1)).toContainText(tag)
  await expect(versionEntry(page, 2)).not.toContainText(tag)

  // A tag covers the whole version, never a sheet or a subset — the panel says
  // so, and the stored record has no sheet to scope it to.
  await expect(page.getByTestId('lens-body')).toContainText('whole version only')
  const tags = await apiOk<{ items: Record<string, unknown>[] }>('GET', `/datasets/${ds.id}/tags`)
  const rec = tags.items.find((t) => t.tag_name === tag)!
  expect(rec.version_number).toBe(1)
  expect(Object.keys(rec)).not.toContain('sheet')
  expect(Object.keys(rec)).not.toContain('sheet_key')
  expect(page.getByTestId('tag-entry')).toHaveCount(tags.items.length)

  // Move the page onto the tagged version and the description changes, because
  // it is describing a relationship between two version numbers, not a state.
  await pickScope(page, 'Version', /^v1\b/)
  await expect(tagRow(page, tag)).toContainText('the version you are viewing')
  await expect(tagPointer(page, tag)).toHaveText('→ v1')
})

test('the tag history ledger records a set and then a promote, newest first', async ({
  page,
  h,
}) => {
  // No rules, so promote's only condition is that the target is ready — the
  // gate itself is the next test's subject.
  const ds = await h.seed(rows(3), 'ledger')
  await h.addVersion(ds.id, rows(5))
  const tag = `hist-${Math.random().toString(36).slice(2, 8)}`

  await goto(page, `/data?dataset=${ds.id}`)
  await page.getByTestId('lens-versions').click()

  // 1. set → v1, from the version the page is viewing.
  await pickScope(page, 'Version', /^v1\b/)
  await expect(page.getByTestId('tag-set')).toHaveText(/Set tag → v1/)
  await page.getByTestId('tag-new-name').fill(tag)
  await page.getByTestId('tag-set').click()
  await expect(tagPointer(page, tag)).toHaveText('→ v1')

  // 2. promote → v2.
  await pickScope(page, 'Version', /^v2\b/)
  await expect(tagRow(page, tag).getByTestId('tag-promote')).toHaveText(/Promote → v2/)
  await tagRow(page, tag).getByTestId('tag-promote').click()
  await expect(tagPointer(page, tag)).toHaveText('→ v2')

  // The pointer above is one mutable number. The ledger is how it got there —
  // an endpoint this suite has never covered, and the only record a promote
  // leaves behind.
  const ledger = await apiOk<{
    items: { action: string; from_version_number: number | null; to_version_number: number | null }[]
    total: number
  }>('GET', `/datasets/${ds.id}/tags/${encodeURIComponent(tag)}/history`)
  expect(ledger.items.map((h) => h.action)).toEqual(['promote', 'set'])
  expect(ledger.items[0]).toMatchObject({ from_version_number: 1, to_version_number: 2 })
  expect(ledger.items[1]).toMatchObject({ from_version_number: null, to_version_number: 1 })

  await tagRow(page, tag).getByTestId('tag-history-toggle').click()
  const entries = page.getByTestId('tag-history-entry')
  await expect(entries).toHaveCount(ledger.items.length)
  await expect(stat(page, 'moves')).toContainText(String(ledger.total))

  // Same order, same transitions, each wearing its action WORD rather than a
  // hue alone — that is what makes the ledger readable in a screenshot.
  for (const [i, entry] of ledger.items.entries()) {
    const row = entries.nth(i)
    await expect(row).toContainText(entry.action)
    await expect(row).toContainText(
      `v${entry.from_version_number ?? '—'} → v${entry.to_version_number ?? '—'}`,
    )
  }
  // `promote` is the only transition that asserts anything about quality, so it
  // is the only one drawn as a pass.
  await expect(entries.nth(0).locator('[data-slot="status"]')).toHaveAttribute(
    'data-status',
    'good',
  )
  await expect(entries.nth(1).locator('[data-slot="status"]')).toHaveAttribute(
    'data-status',
    'unknown',
  )

  await expect(page.getByTestId('tag-history')).toContainText('The history survives the tag')
})

test('promote refuses an unvalidated version, beside the tag it would have moved', async ({
  page,
  h,
}) => {
  // The 409 is the behaviour under test — the browser logging it is expected.
  h.allowError(/409 \(Conflict\)/)

  const ds = await h.seed(rows(3), 'gate')
  await apiOk('POST', `/datasets/${ds.id}/rules`, {
    body: {
      name: `gate-${Math.random().toString(36).slice(2, 8)}`,
      rule_type: 'not_null',
      sheet_selector: 'data',
      column_selector: 'score',
      severity: 'error',
    },
  })
  const tag = `gated-${Math.random().toString(36).slice(2, 8)}`
  await apiOk('PUT', `/datasets/${ds.id}/tags`, { body: { tag_name: tag, version_number: 1 } })
  await h.addVersion(ds.id, rows(6))

  await goto(page, `/data?dataset=${ds.id}`)
  await page.getByTestId('lens-versions').click()

  // The gate is stated before it fires, with the rule counts it will apply.
  const rules = await apiOk<{ items: { enabled: boolean; severity: string | null }[] }>(
    'GET',
    `/datasets/${ds.id}/rules`,
  )
  const enabled = rules.items.filter((r) => r.enabled !== false)
  const errors = enabled.filter((r) => (r.severity ?? 'error') === 'error').length
  await expect(page.getByTestId('lens-body')).toContainText(
    'Promote is the only quality gate in the system.',
  )
  await expect(page.getByTestId('lens-body')).toContainText(
    `${enabled.length} enabled rule${enabled.length === 1 ? '' : 's'}, ${errors} error, ${enabled.length - errors} warning`,
  )

  await tagRow(page, tag).getByTestId('tag-promote').click()

  // The refusal is rendered beside the tag, keyed on the problem+json code.
  const refused = tagRow(page, tag).getByTestId('tag-promote-refused')
  await expect(refused).toBeVisible()
  await expect(refused).toContainText('Promote refused')

  const direct = await api<{ code: string }>('POST', `/datasets/${ds.id}/tags/${tag}/promote`, {
    body: { version_number: 2 },
  })
  expect(direct.status).toBe(409)
  expect(['validation-required', 'validation-failed']).toContain(direct.body.code)
  await expect(refused).toContainText(`409 ${direct.body.code}`)

  // And the consequence, stated where the refusal is: the pointer did not move.
  await expect(refused).toContainText('The tag still points at v1.')
  await expect(tagPointer(page, tag)).toHaveText('→ v1')

  const after = await apiOk<{ items: { tag_name: string; version_number: number }[] }>(
    'GET',
    `/datasets/${ds.id}/tags`,
  )
  expect(after.items.find((t) => t.tag_name === tag)!.version_number).toBe(1)
})

/* ----------------------------------------------------------------- row diff */

test('a row diff asks for a key, then answers with every denominator attached', async ({
  page,
  h,
}) => {
  // `diff-key-required` is a QUESTION the server asks, not a fault — but it
  // still arrives as a 400 the browser logs, so allow it explicitly.
  h.allowError(/400 \(Bad Request\)/)

  // ids 1..5 → 1,2,3,4,6,7: one row leaves, two arrive, two change a value and
  // two are untouched. Every count below is checked against the service anyway.
  const ds = await h.seed(
    [
      { id: 1, name: 'alpha', score: 10 },
      { id: 2, name: 'beta', score: 20 },
      { id: 3, name: 'gamma', score: 30 },
      { id: 4, name: 'delta', score: 40 },
      { id: 5, name: 'epsilon', score: 50 },
    ],
    'rowdiff',
  )
  await h.addVersion(ds.id, [
    { id: 1, name: 'alpha', score: 10 },
    { id: 2, name: 'BETA', score: 20 },
    { id: 3, name: 'gamma', score: 99 },
    { id: 4, name: 'delta', score: 40 },
    { id: 6, name: 'zeta', score: 60 },
    { id: 7, name: 'eta', score: 70 },
  ])

  await goto(page, `/data?dataset=${ds.id}`)
  await page.getByTestId('lens-versions').click()

  await expect(page.getByTestId('row-diff-sheet')).toHaveValue('data')
  await page.getByTestId('row-diff-run').click()

  // The refusal is a first-class STATE: a key picker with the question in it,
  // never an error box. This is the whole design of the section.
  const picker = page.getByTestId('row-diff-key-picker')
  await expect(picker).toBeVisible()
  await expect(picker).toContainText('Match rows on')
  await expect(picker).toContainText('400 diff-key-required')
  await expect(picker).toContainText('This sheet declares no primary key')
  // ...and not the generic failure rendering.
  await expect(page.getByTestId('lens-body')).not.toContainText('Restricted for this seat')

  // Confirm the code the picker is branching on is the real one.
  const refusal = await api<{ code: string }>(
    'POST',
    `/datasets/${ds.id}/versions/1/sheets/data/row-diff/2`,
    { body: { sample_limit: 20 } },
  )
  expect(refusal.status).toBe(400)
  expect(refusal.body.code).toBe('diff-key-required')

  // Answer the question and the diff runs.
  await picker.getByRole('button', { name: 'id', exact: true }).click()
  await expect(picker).toContainText('Composite key of 1 column')
  await page.getByTestId('row-diff-run').click()
  await expect(stat(page, 'added')).toBeVisible()

  const answer = await apiOk<{
    added: number
    removed: number
    changed: number
    unchanged: number
    key: string[]
    compared_columns: string[]
    column_changes: { column: string; changed_rows: number }[]
  }>('POST', `/datasets/${ds.id}/versions/1/sheets/data/row-diff/2`, {
    body: { sample_limit: 20, key: ['id'] },
  })
  const compared = answer.added + answer.removed + answer.changed + answer.unchanged
  const matched = answer.changed + answer.unchanged
  expect(compared).toBeGreaterThan(matched) // the two denominators really differ

  const sheets = await apiOk<{ items: { name: string; column_count: number }[] }>(
    'GET',
    `/datasets/${ds.id}/versions/2/sheets`,
  )
  const columnCount = sheets.items.find((s) => s.name === 'data')!.column_count

  // Each count with the population it was measured over. `added`/`removed` span
  // every row on either side; `changed`/`unchanged` only the matched ones — and
  // printing the second pair over the first would be a different claim.
  await expect(stat(page, 'added')).toContainText(String(answer.added))
  await expect(stat(page, 'added')).toContainText(note(answer.added, compared))
  await expect(stat(page, 'removed')).toContainText(String(answer.removed))
  await expect(stat(page, 'removed')).toContainText(note(answer.removed, compared))
  await expect(stat(page, 'changed')).toContainText(String(answer.changed))
  await expect(stat(page, 'changed')).toContainText(note(answer.changed, matched))
  await expect(stat(page, 'unchanged')).toContainText(String(answer.unchanged))
  await expect(stat(page, 'unchanged')).toContainText(note(answer.unchanged, matched))
  await expect(stat(page, 'compared')).toContainText(
    note(answer.compared_columns.length, columnCount, 'columns'),
  )

  // The denominators again in prose, and the key they were joined on.
  await expect(page.getByTestId('lens-body')).toContainText(
    `added and removed are measured over all ${compared} rows on either side; changed and unchanged only over the ${matched} that matched`,
  )
  await expect(page.getByTestId('lens-body')).toContainText(`Matched on ${answer.key.join(' + ')}`)

  // Per-column detail: one bar per column that actually moved.
  await expect(page.getByTestId('row-diff-column')).toHaveCount(answer.column_changes.length)
  for (const c of answer.column_changes) {
    await expect(page.getByTestId('row-diff-column').filter({ hasText: c.column })).toContainText(
      String(c.changed_rows),
    )
  }
})

test('a row diff across a column whose TYPE changed is answered, not crashed', async ({
  page,
  h,
}) => {
  // DEFECT — analytics-service, not the studio.
  //
  // `rowdiff.py:279` builds `l."score" IS DISTINCT FROM r."score"` over the two
  // versions without reconciling their dtypes, so DuckDB raises
  //   Conversion Error: Could not convert string 'high' to INT64 ... score
  // and the endpoint answers 500 `internal_server_error`. A type change between
  // versions is ordinary — the schema diff two sections up renders it as a
  // first-class outcome — so the row diff owes either an answer (compare as
  // text) or an explained refusal with a `code`. A 500 is neither.
  //
  // Expressed as the correct behaviour and marked failing. Do not "fix" this by
  // weakening the assertion.

  h.allowError(/400 \(Bad Request\)/)
  // A second, smaller defect rides along: the 500 leaves the error handler
  // WITHOUT the CORS headers every other response carries, so the browser can
  // never read the problem+json body — it sees an opaque network failure and
  // the studio has no `code` to branch on. Allowed here so the assertion below
  // is what fails, rather than the harness.
  h.allowError(/blocked by CORS policy/)
  h.allowError(/net::ERR_FAILED/)
  h.allowError(/HTTP 500/)

  const ds = await h.seed(
    Array.from({ length: 4 }, (_, i) => ({ id: i + 1, score: (i + 1) * 10 })),
    'rowdifftype',
  )
  await h.addVersion(
    ds.id,
    Array.from({ length: 4 }, (_, i) => ({ id: i + 1, score: i % 2 === 0 ? 'high' : 'low' })),
  )

  await goto(page, `/data?dataset=${ds.id}`)
  await page.getByTestId('lens-versions').click()

  await page.getByTestId('row-diff-run').click()
  const picker = page.getByTestId('row-diff-key-picker')
  await expect(picker).toBeVisible()
  await picker.getByRole('button', { name: 'id', exact: true }).click()
  await page.getByTestId('row-diff-run').click()

  const answer = await api('POST', `/datasets/${ds.id}/versions/1/sheets/data/row-diff/2`, {
    body: { sample_limit: 20, key: ['id'] },
  })
  expect(
    answer.status,
    `row-diff answered ${answer.status}: ${JSON.stringify(answer.body).slice(0, 200)}`,
  ).not.toBe(500)
  await expect(stat(page, 'added')).toBeVisible()
})

/* ----------------------------------------------------------------- download */

test('downloading a version reads raw rows, and the panel says which version', async ({
  page,
  h,
}) => {
  const ds = await h.seed(rows(3), 'download')
  await h.addVersion(ds.id, rows(7))

  await goto(page, `/data?dataset=${ds.id}`)
  await page.getByTestId('lens-versions').click()

  // The section names its own cost before it is used.
  await expect(page.getByTestId('lens-body')).toContainText('reads raw data')
  await expect(page.getByTestId('lens-body')).toContainText('Export reads raw rows')
  await expect(page.getByTestId('version-download')).toHaveText(/Download v2/)

  const started = page.waitForEvent('download')
  await page.getByTestId('version-download').click()
  const dl = await started

  // The same bytes the API hands out, byte for byte — proof the button exported
  // the version on screen rather than whatever the dataset points at now.
  const res = await fetch(`${API_BASE}/datasets/${ds.id}/versions/2/download?format=csv`, {
    headers: { 'X-User-Id': ADMIN },
  })
  expect(res.status).toBe(200)
  const expected = Buffer.from(await res.arrayBuffer())
  const disposition = res.headers.get('content-disposition') ?? ''
  const expectedName = /filename\*?=(?:UTF-8'')?"?([^";]+)"?/i.exec(disposition)?.[1] ?? ''
  expect(expectedName).toBeTruthy()
  expect(dl.suggestedFilename()).toBe(expectedName)

  const got = readFileSync(await dl.path())
  expect(got.length).toBe(expected.length)
  // Raw, not masked: a value from the fixture is in the file.
  expect(got.toString('utf8')).toContain('n7')

  // And the panel reports what left, attributed to the version it came from.
  await expect(page.getByTestId('lens-body')).toContainText('· from v2')
})

test('a seat without raw access is refused the download, and told why', async ({ page, h }) => {
  // The 403 is the feature: masking the grid would be theatre if the raw file
  // were still downloadable.
  h.allowError(/403 \(Forbidden\)/)

  const ds = await h.seed(
    Array.from({ length: 4 }, (_, i) => ({ id: i, ssn: `SECRET-LEAKCHECK-${i}` })),
    'dlrestricted',
  )
  await h.markSensitive(ds.id, 'ssn')

  const viewer = await h.viewer()
  await h.asSeat(page, viewer.user_id, 'Viewer')
  await goto(page, `/data?dataset=${ds.id}`)
  await page.getByTestId('lens-versions').click()

  await page.getByTestId('version-download').click()

  // A refusal state, not an error box — the difference between "the app is
  // broken" and "you may not have this".
  const body = page.getByTestId('lens-body')
  await expect(body).toContainText('Restricted for this seat')
  await expect(body).toContainText('Downloading a version is refused on datasets that declare')
  await expect(body).not.toContainText('access denied')

  // Nothing leaked while it refused.
  expect(await page.locator('body').innerText()).not.toContain('SECRET-LEAKCHECK')

  // Confirm the refusal is the real one, with the code the UI branches on.
  const res = await fetch(`${API_BASE}/datasets/${ds.id}/versions/1/download?format=csv`, {
    headers: { 'X-User-Id': viewer.user_id },
  })
  expect(res.status).toBe(403)
  expect((await res.json()).code).toBe('sensitive-data-restricted')
})

/* ----------------------------------------------------------------- timeline */

test('the activity timeline lists the events this fixture actually caused', async ({ page, h }) => {
  const ds = await h.seed(rows(4), 'timeline')
  await h.addVersion(ds.id, rows(9))
  const tag = `tl-${Math.random().toString(36).slice(2, 8)}`
  await apiOk('PUT', `/datasets/${ds.id}/tags`, { body: { tag_name: tag, version_number: 2 } })

  await goto(page, `/data?dataset=${ds.id}`)
  await page.getByTestId('lens-versions').click()
  await expect(page.getByTestId('timeline-event').first()).toBeVisible()

  const timeline = await apiOk<{
    items: { event_type: string; details: Record<string, unknown> }[]
    total: number
  }>('GET', `/datasets/${ds.id}/timeline?limit=20`)

  // Nothing writes to this dataset after the page loads, so the counts are
  // stable and can be compared directly rather than polled loosely.
  await expect(page.getByTestId('timeline-event')).toHaveCount(timeline.items.length)
  await expect(stat(page, 'events')).toContainText(String(timeline.total))

  // The two uploads and the tag write are all present, as words rather than as
  // raw event types.
  const created = timeline.items.filter((e) => e.event_type === 'version_created')
  expect(created.length).toBe(2)
  expect(timeline.items.some((e) => e.event_type === 'tag_set')).toBe(true)

  const events = page.getByTestId('timeline-event')
  await expect(events.filter({ hasText: 'version created' })).toHaveCount(created.length)
  await expect(events.filter({ hasText: 'tag set' })).toHaveCount(1)

  // A version_created row carries the version, its status and its row count,
  // built only from keys the event really has.
  for (const e of created) {
    await expect(
      events.filter({ hasText: `v${e.details.version_number} · ${e.details.status}` }),
    ).toContainText(`${e.details.row_count} rows`)
  }

  // The tag row names the tag and the transition it made.
  await expect(events.filter({ hasText: 'tag set' })).toContainText(`${tag} · v— → v2`)

  // Reads are usage, not history: nothing this browser did while looking at the
  // dataset may appear here.
  expect(timeline.items.every((e) => e.event_type !== 'query')).toBe(true)
  await expect(page.getByTestId('lens-body')).toContainText('Reads are usage, not history')
})

test('an unrecognised timeline event renders raw rather than taking the row down', async ({
  page,
  h,
}) => {
  const ds = await h.seed(rows(3), 'timelineunknown')

  // The service can only emit the eleven types the lens has words for, so the
  // fallback branch is unreachable with real data — and an unreachable branch
  // is exactly the one that breaks when a twelfth type ships. Inject one.
  const injected = {
    event_type: 'sheet_quarantined',
    occurred_at: new Date().toISOString(),
    actor: 'future@example.com',
    details: { unexpected: { nested: 'object' } },
  }
  await page.route('**/datasets/*/timeline*', async (route) => {
    const res = await route.fetch()
    const body = (await res.json()) as { items: unknown[]; total: number }
    await route.fulfill({
      response: res,
      json: { ...body, items: [injected, ...body.items], total: body.total + 1 },
    })
  })

  await goto(page, `/data?dataset=${ds.id}`)
  await page.getByTestId('lens-versions').click()

  const row = page.getByTestId('timeline-event').first()
  // Shown as its own event_type — honest, rather than relabelled as something
  // it is not, and rather than an empty status word.
  await expect(row).toContainText(injected.event_type)
  await expect(row).toContainText('future@example.com')
  // The nested object is not a fact this panel can render, so it is omitted
  // rather than printed as "[object Object]".
  await expect(row).not.toContainText('[object Object]')

  // The rest of the timeline still renders around it.
  await expect(page.getByTestId('timeline-event').filter({ hasText: 'version created' })).toHaveCount(
    1,
  )
})

/* ----------------------------------------------------------- confirm rename */

test('confirming a rename relinks the sheet instead of leaving it detached', async ({ page, h }) => {
  const shared = Array.from({ length: 5 }, (_, i) => ({ id: i + 1, amount: (i + 1) * 10 }))
  const ref = [{ k: 1 }, { k: 2 }]
  const name = uniqueName('rename')
  const id = await uploadWorkbook({ Orders: shared, Ref: ref }, name)
  await uploadWorkbook({ Orders2024: shared, Ref: ref }, `${name}-v2`, id)

  await goto(page, `/data?dataset=${id}`)
  await page.getByTestId('lens-versions').click()

  // The server offers the pair and refuses to act on it — an identical
  // fingerprint is evidence, not a decision.
  const before = await apiOk<{
    rename_candidates: { from_sheet: string; to_sheet: string; confidence: string; reason: string }[]
    renamed: unknown[]
  }>('GET', `/datasets/${id}/versions/1/diff/2`)
  expect(before.rename_candidates.length).toBe(1)
  expect(before.renamed.length).toBe(0)
  const candidate = before.rename_candidates[0]

  const card = page.getByTestId('rename-candidate')
  await expect(card).toHaveCount(1)
  await expect(card).toContainText(candidate.from_sheet)
  await expect(card).toContainText(candidate.to_sheet)
  await expect(card).toContainText(candidate.confidence)
  await expect(card).toContainText(candidate.reason)
  await expect(page.getByTestId('lens-body')).toContainText(
    'the server never auto-declares a rename',
  )

  // The logical identity is what the confirmation is actually about.
  const v1sheets = await apiOk<{ items: { name: string; logical_sheet_id: string }[] }>(
    'GET',
    `/datasets/${id}/versions/1/sheets`,
  )
  const originalLogicalId = v1sheets.items.find((s) => s.name === candidate.from_sheet)!
    .logical_sheet_id
  const v2before = await apiOk<{ items: { name: string; logical_sheet_id: string }[] }>(
    'GET',
    `/datasets/${id}/versions/2/sheets`,
  )
  expect(
    v2before.items.find((s) => s.name === candidate.to_sheet)!.logical_sheet_id,
  ).not.toBe(originalLogicalId)

  await card.getByTestId('rename-confirm').click()

  // The offer is withdrawn because it has been taken.
  await expect(page.getByTestId('rename-candidate')).toHaveCount(0)

  // Re-read: the pair is now a recorded rename, not a candidate — and the sheet
  // in v2 carries v1's logical id, which is what makes metadata, dictionary
  // entries and quality rules follow it instead of silently detaching.
  const after = await apiOk<{
    rename_candidates: unknown[]
    renamed: { from_sheet: string; to_sheet: string }[]
  }>('GET', `/datasets/${id}/versions/1/diff/2`)
  expect(after.rename_candidates.length).toBe(0)
  expect(after.renamed).toContainEqual(
    expect.objectContaining({ from_sheet: candidate.from_sheet, to_sheet: candidate.to_sheet }),
  )

  const v2after = await apiOk<{ items: { name: string; logical_sheet_id: string }[] }>(
    'GET',
    `/datasets/${id}/versions/2/sheets`,
  )
  expect(v2after.items.find((s) => s.name === candidate.to_sheet)!.logical_sheet_id).toBe(
    originalLogicalId,
  )

  // Confirming is a write driven from the browser, and it must be silent — the
  // harness asserts this at teardown too, but stating it here is what keeps the
  // `h` fixture (and therefore the console listener) attached to this test.
  expect(h.errors).toEqual([])
})

/* -------------------------------------------------------------- 404 not 403 */

test('a version history that is not yours is missing, never forbidden', async ({ page, h }) => {
  h.allowError(/404 \(Not Found\)/)

  const viewer = await h.viewer()
  // Well-formed and resolvable to nothing — the same path a foreign team's
  // dataset takes, because existence is hidden on purpose.
  const ghost = '11111111-2222-3333-4444-555555555555'

  // Every read this lens makes must answer 404. A 403 anywhere here confirms
  // the dataset exists somewhere, which is the leak the rule exists to stop.
  for (const path of [
    `/datasets/${ghost}/versions`,
    `/datasets/${ghost}/tags`,
    `/datasets/${ghost}/tags/prod/history`,
    `/datasets/${ghost}/timeline?limit=20`,
    `/datasets/${ghost}/versions/1/diff/2`,
  ]) {
    const res = await api('GET', path, { userId: viewer.user_id })
    expect(res.status, `${path} answered ${res.status}`).toBe(404)
  }
  const dl = await fetch(`${API_BASE}/datasets/${ghost}/versions/1/download?format=csv`, {
    headers: { 'X-User-Id': viewer.user_id },
  })
  expect(dl.status).toBe(404)

  await h.asSeat(page, viewer.user_id, 'Viewer')
  await goto(page, `/data?dataset=${ghost}`)

  // And the screen must not translate the 404 into a refusal.
  const text = (await page.locator('body').innerText()).toLowerCase()
  expect(text).not.toContain('access denied')
  expect(text).not.toContain('forbidden')
  expect(text).not.toContain('not permitted')
  expect(text).not.toContain('restricted for this seat')
})

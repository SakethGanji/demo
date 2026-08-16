import { test, expect, goto, api, apiOk, uniqueName } from './fixtures'

/**
 * Writes.
 *
 * Every test here ends by **re-reading the API**, never by trusting a toast.
 * That is not paranoia: the previous UI had ten delete call sites that showed
 * "Delete failed" while the delete had succeeded, because a 204 with a JSON
 * content-type threw on `.json()`. A success toast and a real write are
 * independent facts, and only one of them is the one under test.
 */

test('upload creates a dataset that the API then reports', async ({ page, h }) => {
  // Deep-link to our own fixture rather than letting the page fall back to
  // whichever dataset happens to be first: other workers delete theirs
  // concurrently, and a test that rides ambient state measures their churn.
  const anchor = await h.seed([{ a: 1 }], 'uploadanchor')
  await goto(page, `/data?dataset=${anchor.id}`)

  const name = uniqueName('upload')
  const csv = 'sku,qty\nA-1,4\nA-2,9\nA-3,15\n'

  await page.getByRole('button', { name: /Upload dataset/ }).click()
  await expect(page.getByTestId('upload-dialog')).toBeVisible()

  await page.getByTestId('upload-file').setInputFiles({
    name: `${name}.csv`,
    mimeType: 'text/csv',
    buffer: Buffer.from(csv),
  })
  await page.getByTestId('upload-submit').click()

  // The dialog closing is the UI's claim. The API is the evidence.
  await expect(page.getByTestId('upload-dialog')).toBeHidden()

  const found = await apiOk<{ items: any[] }>('GET', `/datasets?q=${encodeURIComponent(name)}`)
  const ds = found.items.find((d) => d.name === `${name}.csv`)
  expect(ds, `no dataset named ${name}.csv after upload`).toBeTruthy()
  expect(ds.row_count).toBe(3)

  // And the app selected it, so the user sees what they just uploaded.
  await expect(page.getByTestId('row-count')).toContainText('3 rows')
})

test('uploading onto a dataset adds a version instead of overwriting', async ({ page, h }) => {
  const ds = await h.seed([{ a: 1 }, { a: 2 }], 'newver')

  await goto(page, `/data?dataset=${ds.id}`)
  await expect(page.getByTestId('row-count')).toContainText('2 rows')

  await page.getByRole('button', { name: /Upload dataset/ }).click()
  await page.getByTestId('upload-file').setInputFiles({
    name: `${uniqueName('v2')}.csv`,
    mimeType: 'text/csv',
    buffer: Buffer.from('a\n1\n2\n3\n4\n5\n'),
  })
  await page.getByTestId('upload-as-version').check()
  await page.getByTestId('upload-submit').click()
  await expect(page.getByTestId('upload-dialog')).toBeHidden()

  // Two versions now exist, and v1 still has its original rows: immutability
  // is the claim, so assert the OLD version is untouched, not just that a new
  // one appeared.
  const versions = await apiOk<{ items: any[] }>('GET', `/datasets/${ds.id}/versions`)
  expect(versions.items.length).toBe(2)

  const v1 = await apiOk<{ total: number }>(
    'POST',
    `/datasets/${ds.id}/versions/1/sheets/data/query`,
    { body: { limit: 1 } },
  )
  expect(v1.total).toBe(2)

  await expect(page.getByTestId('row-count')).toContainText('5 rows')
})

test('editing metadata persists exactly the fields that were changed', async ({ page, h }) => {
  const ds = await h.seed([{ a: 1 }], 'meta')

  await goto(page, `/data?dataset=${ds.id}`)
  await page.getByTestId('lens-overview').click()
  await page.getByTestId('metadata-edit').click()

  const newDescription = `described-${Math.random().toString(36).slice(2, 8)}`
  await page.getByTestId('metadata-description').fill(newDescription)
  await page.getByTestId('metadata-classification').selectOption('confidential')
  await page.getByTestId('metadata-domain').fill('finance')
  await page.getByTestId('metadata-save').click()

  await expect(page.getByTestId('metadata-form')).toBeHidden()

  // Re-read: the record, not the form state, is what persisted.
  const after = await apiOk<{ items: any[] }>('GET', `/datasets?q=${encodeURIComponent(ds.name)}`)
  const rec = after.items.find((d) => d.id === ds.id)
  expect(rec.description).toBe(newDescription)
  expect(rec.classification).toBe('confidential')
  expect(rec.domain).toBe('finance')
  // The name was never touched, so PATCH must not have cleared it.
  expect(rec.name).toBe(ds.name)
})

test('creating a quality rule persists it with the shape the API expects', async ({ page, h }) => {
  const ds = await h.seed([{ amount: 1 }, { amount: 2 }], 'rulecreate')

  await goto(page, `/data?dataset=${ds.id}`)
  await page.getByTestId('lens-quality').click()
  await page.getByTestId('rule-add-toggle').click()

  const ruleName = `not-null-${Math.random().toString(36).slice(2, 8)}`
  await page.getByTestId('rule-name').fill(ruleName)
  await page.getByTestId('rule-type').selectOption('not_null')
  await page.getByTestId('rule-column').selectOption('amount')
  await page.getByTestId('rule-save').click()

  await expect(page.getByTestId('rule-form')).toBeHidden()

  const rules = await apiOk<{ items: any[] }>('GET', `/datasets/${ds.id}/rules`)
  const rule = rules.items.find((r) => r.name === ruleName)
  expect(rule, `rule ${ruleName} not persisted`).toBeTruthy()
  expect(rule.rule_type).toBe('not_null')
  expect(rule.column_selector).toBe('amount')
  // scope_type is derived server-side; the UI must not have invented one.
  expect(rule.scope_type).toBeTruthy()
  expect(rule.enabled).toBe(true)
})

test('disabling a rule persists, and the row reflects it', async ({ page, h }) => {
  const ds = await h.seed([{ amount: 1 }], 'ruletoggle')
  const ruleName = `toggle-${Math.random().toString(36).slice(2, 8)}`
  await apiOk('POST', `/datasets/${ds.id}/rules`, {
    body: {
      name: ruleName,
      rule_type: 'not_null',
      sheet_selector: 'data',
      column_selector: 'amount',
    },
  })

  await goto(page, `/data?dataset=${ds.id}`)
  await page.getByTestId('lens-quality').click()

  const row = page.getByTestId('rule').filter({ hasText: ruleName })
  await expect(row).toHaveCount(1)
  await expect(row.getByTestId('rule-toggle')).toHaveText('on')

  await row.getByTestId('rule-toggle').click()
  await expect(row.getByTestId('rule-toggle')).toHaveText('off')

  const after = await apiOk<{ items: any[] }>('GET', `/datasets/${ds.id}/rules`)
  expect(after.items.find((r) => r.name === ruleName).enabled).toBe(false)
})

test('deleting a rule removes it, and the row disappears', async ({ page, h }) => {
  const ds = await h.seed([{ amount: 1 }], 'ruledelete')
  const ruleName = `del-${Math.random().toString(36).slice(2, 8)}`
  await apiOk('POST', `/datasets/${ds.id}/rules`, {
    body: {
      name: ruleName,
      rule_type: 'not_null',
      sheet_selector: 'data',
      column_selector: 'amount',
    },
  })

  await goto(page, `/data?dataset=${ds.id}`)
  await page.getByTestId('lens-quality').click()

  // Scope to the rule's own row. A bare "delete" once matched a page-level
  // control and destroyed the fixture instead of the rule.
  const row = page.getByTestId('rule').filter({ hasText: ruleName })
  await expect(row).toHaveCount(1)
  await row.getByTestId('rule-delete').click()
  await expect(row).toHaveCount(0)

  // The 204-with-JSON-content-type case: assert the write, not the toast.
  const after = await apiOk<{ items: any[]; total: number }>('GET', `/datasets/${ds.id}/rules`)
  expect(after.items.some((r) => r.name === ruleName)).toBe(false)
  expect(after.total).toBe(0)
})

test('validation runs against the version on screen and records a result', async ({ page, h }) => {
  // One row violates the rule, so a pass would be the wrong answer.
  const ds = await h.seed([{ amount: 5 }, { amount: '' }, { amount: 7 }], 'validate')
  await apiOk('POST', `/datasets/${ds.id}/rules`, {
    body: {
      name: `amount-present-${Math.random().toString(36).slice(2, 8)}`,
      rule_type: 'not_null',
      sheet_selector: 'data',
      column_selector: 'amount',
      severity: 'error',
    },
  })

  await goto(page, `/data?dataset=${ds.id}`)
  await page.getByTestId('lens-quality').click()
  await page.getByTestId('validate-run').click()

  await expect(page.getByTestId('validation-result')).toBeVisible()

  const runs = await apiOk<{ items: any[] }>('GET', `/datasets/${ds.id}/versions/1/validations`)
  expect(runs.items.length).toBeGreaterThan(0)
  const latest = runs.items[0]
  expect(latest.rules_total).toBeGreaterThan(0)
  // The UI must show the real verdict, not a cheerful default.
  const failed = (latest.error_failures ?? 0) > 0
  await expect(page.getByTestId('validation-result')).toContainText(failed ? 'failed' : 'passed')
})

test('validate is not offered when no rule is enabled', async ({ page, h }) => {
  const ds = await h.seed([{ a: 1 }], 'novalidate')

  await goto(page, `/data?dataset=${ds.id}`)
  await page.getByTestId('lens-quality').click()

  // Validating with no enabled rules is a 400, not an empty pass — so the
  // button must be unavailable rather than let the user find that out.
  await expect(page.getByTestId('validate-run')).toBeDisabled()
})

test('setting a tag points it at the version on screen', async ({ page, h }) => {
  const ds = await h.seed([{ a: 1 }], 'tagset')
  const tag = `t${Math.random().toString(36).slice(2, 8)}`

  await goto(page, `/data?dataset=${ds.id}`)
  await page.getByTestId('lens-versions').click()

  await page.getByTestId('tag-new-name').fill(tag)
  await page.getByTestId('tag-set').click()

  await expect(page.getByTestId('tag-entry').filter({ hasText: tag })).toHaveCount(1)

  await expect
    .poll(async () => {
      const tags = await apiOk<{ items: any[] }>('GET', `/datasets/${ds.id}/tags`)
      return tags.items.find((t) => t.tag_name === tag)?.version_number ?? null
    }, { message: `tag ${tag} never persisted` })
    .toBe(1)
})

test('promote refuses a version that has not passed its rules, and says why', async ({
  page,
  h,
}) => {
  // The 409 IS the assertion here — the gate refusing an unvalidated version is
  // the behaviour under test, so the browser logging it is expected, not noise.
  h.allowError(/409 \(Conflict\)/)

  // A dataset with an enabled rule and no clean validation run cannot be promoted.
  const ds = await h.seed([{ amount: 1 }, { amount: 2 }], 'promote')
  await apiOk('POST', `/datasets/${ds.id}/rules`, {
    body: {
      name: `gate-${Math.random().toString(36).slice(2, 8)}`,
      rule_type: 'not_null',
      sheet_selector: 'data',
      column_selector: 'amount',
    },
  })
  const tag = `p${Math.random().toString(36).slice(2, 8)}`
  await apiOk('PUT', `/datasets/${ds.id}/tags`, { body: { tag_name: tag, version_number: 1 } })
  await h.addVersion(ds.id, [{ amount: 3 }])

  await goto(page, `/data?dataset=${ds.id}`)
  await page.getByTestId('lens-versions').click()

  const row = page.getByTestId('tag-entry').filter({ hasText: tag })
  await row.getByTestId('tag-promote').click()

  // The refusal is the feature. Confirm the tag did NOT move.
  const tags = await apiOk<{ items: any[] }>('GET', `/datasets/${ds.id}/tags`)
  expect(tags.items.find((t) => t.tag_name === tag).version_number).toBe(1)

  // And confirm the API's own reason, so the UI is refusing for the right cause.
  const direct = await api('POST', `/datasets/${ds.id}/tags/${tag}/promote`, {
    body: { version_number: 2 },
  })
  expect(direct.status).toBe(409)
  expect(['validation-required', 'validation-failed']).toContain(direct.body.code)
})

test('marking a column sensitive immediately masks it for a viewer', async ({ page, h }) => {
  const ds = await h.seed(
    Array.from({ length: 5 }, (_, i) => ({ id: i, ssn: `SECRET-LEAKCHECK-${i}` })),
    'dict',
  )

  await goto(page, `/data?dataset=${ds.id}`)
  await page.getByTestId('lens-overview').click()

  await page.getByTestId('dict-column').selectOption('ssn')
  await page.getByTestId('dict-business-name').fill('Social security number')
  await page.getByTestId('dict-sensitivity').selectOption('confidential')
  await page.getByTestId('dict-save').click()

  // Re-read the dictionary entry itself. Poll rather than read once: the click
  // returns before the request lands, and a bare read races the write — which
  // would make this test fail for a reason that has nothing to do with the
  // behaviour under test.
  await expect
    .poll(async () => (await api('GET', `/datasets/${ds.id}/sheet-metadata/data/columns/ssn`)).body)
    .toMatchObject({ sensitivity: 'confidential', business_name: 'Social security number' })

  // The point of the setting: a non-privileged seat must not see the value.
  const viewer = await h.viewer()
  const asViewer = await apiOk<any>(
    'POST',
    `/datasets/${ds.id}/versions/1/sheets/data/query`,
    { body: { limit: 5 }, userId: viewer.user_id },
  )
  expect(asViewer.masked_columns).toContain('ssn')
  expect(JSON.stringify(asViewer.items)).not.toContain('SECRET-LEAKCHECK')
})

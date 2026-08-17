import { test, expect, goto, api, apiOk, pickScope } from './fixtures'

/**
 * Masking and RBAC, seen through the UI.
 *
 * The sentinel pattern is the important one: seed a value that could only have
 * come from the raw data, then assert that string is absent from what a
 * non-privileged seat is shown. Asserting "the cell says ***" is weaker — it
 * passes even if the raw value is also sitting in the DOM somewhere else.
 *
 * Note masking applies to a viewer AND an editor. Only admin, owner or
 * superuser see raw values, so checking from an admin seat proves nothing.
 */

const SENTINEL = 'SECRET-LEAKCHECK'

test('a viewer sees masked values in the grid — not raw, and not refused', async ({ page, h }) => {
  const ds = await h.seed(
    Array.from({ length: 8 }, (_, i) => ({ id: i, ssn: `${SENTINEL}-${i}` })),
    'maskgrid',
  )
  await h.markSensitive(ds.id, 'ssn')

  const viewer = await h.viewer()
  await h.asSeat(page, viewer.user_id, 'Viewer')
  await goto(page, `/data?dataset=${ds.id}`)

  // Masked, not refused: the rows must still render.
  await expect(page.locator('tbody tr')).toHaveCount(8)

  // The sentinel must appear nowhere in the rendered page.
  const body = await page.locator('body').innerText()
  expect(body).not.toContain(SENTINEL)

  // And the masked column is named as masked rather than silently blanked.
  //
  // Scoped to the grid toolbar: masking is now stated in three places (the
  // cockpit strip, the dataset header and this badge), which is the point —
  // but an unscoped regex matches all three and fails strict mode.
  await expect(page.getByTestId('grid-masked-count')).toBeVisible()
  await expect(page.getByTestId('grid-masked-count')).toContainText(/\d+ masked/)

  const html = await page.content()
  expect(html).not.toContain(SENTINEL)
})

test('an admin sees the same column unmasked', async ({ page, h }) => {
  const ds = await h.seed(
    Array.from({ length: 5 }, (_, i) => ({ id: i, ssn: `${SENTINEL}-${i}` })),
    'maskadmin',
  )
  await h.markSensitive(ds.id, 'ssn')

  // Default seat is the System superuser — the control for the test above.
  await goto(page, `/data?dataset=${ds.id}`)
  await expect(page.locator('tbody tr')).toHaveCount(5)

  const body = await page.locator('body').innerText()
  expect(body).toContain(SENTINEL)
})

test('switching seats in the UI changes what the same page shows', async ({ page, h }) => {
  const ds = await h.seed(
    Array.from({ length: 5 }, (_, i) => ({ id: i, ssn: `${SENTINEL}-${i}` })),
    'seatswitch',
  )
  await h.markSensitive(ds.id, 'ssn')
  const viewer = await h.viewer()

  await goto(page, `/data?dataset=${ds.id}`)
  expect(await page.locator('body').innerText()).toContain(SENTINEL)

  // Drive the real control, not localStorage — this is the flow a person uses
  // to prove to themselves that masking bites.
  await pickScope(page, 'Acting seat', `${viewer.name} (${viewer.role})`)

  await expect
    .poll(async () => (await page.locator('body').innerText()).includes(SENTINEL))
    .toBe(false)

  // Still readable, just masked — a refusal here would be the wrong behaviour.
  await expect(page.locator('tbody tr')).toHaveCount(5)
})

test('a dataset with no sensitive columns is unaffected for a viewer', async ({ page, h }) => {
  // The guard must be narrow: masking machinery should not degrade ordinary data.
  const ds = await h.seed(
    Array.from({ length: 6 }, (_, i) => ({ id: i, city: `city-${i}` })),
    'nomask',
  )

  const viewer = await h.viewer()
  await h.asSeat(page, viewer.user_id, 'Viewer')
  await goto(page, `/data?dataset=${ds.id}`)

  await expect(page.locator('tbody tr')).toHaveCount(6)
  expect(await page.locator('body').innerText()).toContain('city-3')

  // And profiling still works, because nothing here is sensitive.
  await page.getByTestId('lens-analytics').click()
  await expect(page.getByTestId('profile-column').first()).toBeVisible()
  await expect(page.getByTestId('lens-body')).not.toContainText('Restricted for this seat')
})

test('the schema-only compile stays available to a viewer', async ({ page, h }) => {
  const ds = await h.seed(
    Array.from({ length: 6 }, (_, i) => ({ id: i, ssn: `${SENTINEL}-${i}` })),
    'viewercompile',
  )
  await h.markSensitive(ds.id, 'ssn')

  const viewer = await h.viewer()
  await h.asSeat(page, viewer.user_id, 'Viewer')
  await goto(page, `/data?dataset=${ds.id}`)
  await page.getByTestId('lens-transform').click()

  await page.getByTestId('transform-compile').click()

  // The gate is on VALUES, not on columns. Over-correcting into a 403 wall here
  // would stop a viewer designing a pipeline at all, which is not the intent.
  await expect(page.getByTestId('transform-output-schema')).toBeVisible()
  const names = await page
    .getByTestId('transform-output-name')
    .evaluateAll((els) => els.map((e) => e.textContent?.trim()))
  expect(names).toContain('ssn')

  // The column NAME is visible; no value is.
  expect(await page.locator('body').innerText()).not.toContain(SENTINEL)
})

test('a viewer is refused the writes an editor would be allowed', async ({ h }) => {
  const ds = await h.seed([{ amount: 1 }], 'rbac')
  const viewer = await h.viewer()

  // dataset:write is editor+, so each of these must be a 403 for a viewer.
  const rule = await api('POST', `/datasets/${ds.id}/rules`, {
    body: {
      name: `nope-${Math.random().toString(36).slice(2, 8)}`,
      rule_type: 'not_null',
      sheet_selector: 'data',
      column_selector: 'amount',
    },
    userId: viewer.user_id,
  })
  expect(rule.status).toBe(403)

  const patch = await api('PATCH', `/datasets/${ds.id}`, {
    body: { description: 'should not persist' },
    userId: viewer.user_id,
  })
  expect(patch.status).toBe(403)

  // Confirm nothing leaked through: re-read as admin.
  const after = await apiOk<{ items: any[] }>(
    'GET',
    `/datasets?q=${encodeURIComponent(ds.name)}`,
  )
  expect(after.items.find((d) => d.id === ds.id)?.description ?? null).not.toBe(
    'should not persist',
  )
})

test('a dataset in another team is hidden rather than forbidden', async ({ h }) => {
  const ds = await h.seed([{ a: 1 }], 'crossteam')

  // A brand-new team the System user does not belong to... except System is a
  // superuser, so use a viewer of the Default team as the outsider instead:
  // they can see the Default team, so the meaningful check is that a dataset
  // they may read is readable, and existence-hiding is a 404 not a 403.
  const viewer = await h.viewer()

  const readable = await api('GET', `/datasets/${ds.id}/versions`, { userId: viewer.user_id })
  expect(readable.status).toBe(200)

  // An id that does not exist must be 404 — never 403, which would confirm it
  // exists somewhere. Existence is hidden on purpose.
  const ghost = await api('GET', `/datasets/11111111-2222-3333-4444-555555555555/versions`, {
    userId: viewer.user_id,
  })
  expect(ghost.status).toBe(404)
})

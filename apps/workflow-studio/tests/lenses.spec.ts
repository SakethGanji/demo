import { test, expect, goto, api, apiOk } from './fixtures'

/**
 * The seven lenses.
 *
 * The design rule under test throughout: choosing a lens never hides the data.
 * The grid stays mounted and populated while the panel changes.
 */

test('the grid stays loaded while you move between lenses', async ({ page, h }) => {
  const ds = await h.seed(
    Array.from({ length: 6 }, (_, i) => ({ id: i, name: `n${i}` })),
    'lensgrid',
  )

  await goto(page, `/data?dataset=${ds.id}`)
  await expect(page.locator('tbody tr')).toHaveCount(6)

  for (const lens of ['quality', 'versions', 'analytics', 'relationships', 'transform', 'library']) {
    await page.getByTestId(`lens-${lens}`).click()
    await expect(page.getByTestId(`lens-${lens}`)).toHaveAttribute('aria-pressed', 'true')
    // The whole point of a lens over a tab: the table never goes away.
    await expect(page.locator('tbody tr')).toHaveCount(6)
  }
})

test('the analytics lens profiles every column, with a distribution for numerics', async ({
  page,
  h,
}) => {
  const ds = await h.seed(
    Array.from({ length: 40 }, (_, i) => ({
      amount: i * 3,
      region: ['EU', 'US', 'APAC'][i % 3],
    })),
    'profile',
  )

  await goto(page, `/data?dataset=${ds.id}`)
  await page.getByTestId('lens-analytics').click()

  // Compare against the API's own profile rather than a number we invented.
  const profile = await apiOk<any>('POST', '/profile', {
    body: { dataset_id: ds.id, version_number: 1, sheet: 'data', top_n: 5 },
  })
  await expect(page.getByTestId('profile-column')).toHaveCount(profile.columns.length)

  const names = await page
    .getByTestId('profile-column-name')
    .evaluateAll((els) => els.map((e) => e.textContent?.trim()))
  expect(new Set(names)).toEqual(new Set(profile.columns.map((c: any) => c.name)))

  // A numeric column gets a histogram; a categorical one gets its top values.
  await expect(page.getByTestId('profile-histogram').first()).toBeVisible()
  await expect(page.getByTestId('profile-top-values').first()).toBeVisible()

  // Health dimensions render alongside, each with a status and a summary.
  await expect(page.getByTestId('health-dimension').first()).toBeVisible()
})

test('the relationships lens seeds edges from foreign-key rules and confirms them', async ({
  page,
  h,
}) => {
  const wb = await h.seedWorkbook(
    {
      Customers: Array.from({ length: 10 }, (_, i) => ({ customer_id: i + 1, tier: 'gold' })),
      // Deliberately references ids beyond Customers, so the edge is real but imperfect.
      Orders: Array.from({ length: 20 }, (_, i) => ({ order_id: i + 1, customer_id: (i % 14) + 1 })),
    },
    'rel',
  )

  await apiOk('POST', `/datasets/${wb.id}/rules`, {
    body: {
      name: `fk-${Math.random().toString(36).slice(2, 8)}`,
      rule_type: 'foreign_key',
      sheet_selector: 'Orders',
      column_selector: 'customer_id',
      parameters: { ref_sheet: 'Customers', ref_column: 'customer_id' },
    },
  })

  await goto(page, `/data?dataset=${wb.id}`)
  await page.getByTestId('lens-relationships').click()

  await expect(page.getByTestId('relationship')).toHaveCount(0)
  await page.getByTestId('relationship-seed').click()
  await expect(page.getByTestId('relationship')).toHaveCount(1)

  const edge = page.getByTestId('relationship').first()
  await expect(edge).toContainText('customer_id')

  // Re-read: the edge is a real row, not a rendering artefact.
  const rels = await apiOk<{ items: any[] }>('GET', `/datasets/${wb.id}/relationships`)
  expect(rels.items.length).toBe(1)
  expect(rels.items[0].from_column).toBe('customer_id')
})

test('the transform lens compiles a schema without ever fetching rows', async ({ page, h }) => {
  const ds = await h.seed(
    Array.from({ length: 8 }, (_, i) => ({ keep: i, drop_me: `secret-${i}`, also: i * 2 })),
    'transform',
  )

  await goto(page, `/data?dataset=${ds.id}`)
  await page.getByTestId('lens-transform').click()

  // Watch the wire: the compile request must not ask for rows.
  const bodies: string[] = []
  page.on('request', (r) => {
    if (r.url().includes('/transformations/compile')) bodies.push(r.postData() ?? '')
  })

  await page.getByTestId('transform-column-toggle').filter({ hasText: 'drop_me' }).click()
  await page.getByTestId('transform-compile').click()

  await expect(page.getByTestId('transform-output-schema')).toBeVisible()
  const out = await page
    .getByTestId('transform-output-name')
    .evaluateAll((els) => els.map((e) => e.textContent?.trim()))
  expect(out).toEqual(['keep', 'also'])

  // `rows` absent is the entire security contract of this surface: with it, the
  // call becomes a read of pipeline OUTPUT and is gated behind raw access.
  expect(bodies.length).toBeGreaterThan(0)
  for (const b of bodies) {
    expect(JSON.parse(b)).not.toHaveProperty('rows')
  }

  // And the server agrees about the shape.
  const compiled = await apiOk<any>('POST', `/datasets/${ds.id}/transformations/compile`, {
    body: {
      sheet: 'data',
      version_selector: { mode: 'current' },
      steps: [{ type: 'drop', columns: ['drop_me'] }],
    },
  })
  expect(compiled.output_schema.map((c: any) => c.name)).toEqual(['keep', 'also'])
  expect(compiled.rows).toEqual([])
})

test('the library lens lists artifacts this dataset produced', async ({ page, h }) => {
  const ds = await h.seed(
    Array.from({ length: 12 }, (_, i) => ({ region: ['EU', 'US'][i % 2], amount: i })),
    'library',
  )

  // An aggregate persists an `aggregation_output` artifact against the dataset.
  await apiOk('POST', '/aggregate', {
    body: {
      dataset_id: ds.id,
      version_number: 1,
      sheet: 'data',
      group_by: ['region'],
      aggregations: [{ column: 'amount', function: 'sum', alias: 'total' }],
    },
  })

  await goto(page, `/data?dataset=${ds.id}`)
  await page.getByTestId('lens-library').click()

  await expect(page.getByTestId('artifact')).toHaveCount(1)

  // The kind and its retention are stated once per KIND GROUP rather than
  // repeated on every row — the clock is a property of the kind, so one
  // statement covers the whole block. The assertions are unchanged; only the
  // element that has to carry them moved outward.
  const group = page.getByTestId('artifact-group').first()
  await expect(group).toContainText('aggregation_output')
  // Retention is real and surprising; the panel must state it.
  await expect(group).toContainText('kept 30d')
  // And the row itself still carries its own countdown against that clock.
  await expect(page.getByTestId('artifact-retention').first()).toBeVisible()

  // Cross-check against the artifact table.
  const samples = await apiOk<{ items: any[] }>('GET', '/samples?limit=200')
  expect(samples.items.filter((a) => a.dataset_id === ds.id).length).toBe(1)
})

test('the versions lens shows immutable history and pins tags to versions', async ({ page, h }) => {
  const ds = await h.seed([{ a: 1 }, { a: 2 }], 'verlens')
  await h.addVersion(ds.id, Array.from({ length: 4 }, (_, i) => ({ a: i })))
  const tag = `v${Math.random().toString(36).slice(2, 8)}`
  await apiOk('PUT', `/datasets/${ds.id}/tags`, { body: { tag_name: tag, version_number: 1 } })

  await goto(page, `/data?dataset=${ds.id}`)
  await page.getByTestId('lens-versions').click()

  await expect(page.getByTestId('version-entry')).toHaveCount(2)
  await expect(page.getByTestId('tag-entry')).toHaveCount(1)
  await expect(page.getByTestId('tag-name')).toHaveText(tag)

  // The tag badge sits on the version it points at, not the one being viewed.
  const v1 = page.getByTestId('version-entry').filter({ hasText: 'v1' })
  await expect(v1).toContainText(tag)
})

test('the overview lens names the columns masked for this seat', async ({ page, h }) => {
  const ds = await h.seed(
    Array.from({ length: 4 }, (_, i) => ({ id: i, email: `u${i}@example.com` })),
    'maskoverview',
  )
  await h.markSensitive(ds.id, 'email')

  const viewer = await h.viewer()
  await h.asSeat(page, viewer.user_id, 'Viewer')
  await goto(page, `/data?dataset=${ds.id}`)

  await page.getByTestId('lens-overview').click()

  // The API is the source of truth for what is masked; the panel must agree.
  const asViewer = await apiOk<any>('POST', `/datasets/${ds.id}/versions/1/sheets/data/query`, {
    body: { limit: 4 },
    userId: viewer.user_id,
  })
  expect(asViewer.masked_columns).toContain('email')
  await expect(page.getByTestId('lens-body')).toContainText('Masked for this seat')
  await expect(page.getByTestId('lens-body')).toContainText('email')
})

test('a lens that the API refuses explains itself instead of looking broken', async ({
  page,
  h,
}) => {
  const ds = await h.seed(
    Array.from({ length: 6 }, (_, i) => ({ id: i, ssn: `SECRET-${i}` })),
    'restricted',
  )
  await h.markSensitive(ds.id, 'ssn')

  // Profiling computes over raw values, so it is refused outright for a seat
  // without elevated access — a 403, not a masked result.
  h.allowError(/403 \(Forbidden\)/)

  const viewer = await h.viewer()
  await h.asSeat(page, viewer.user_id, 'Viewer')
  await goto(page, `/data?dataset=${ds.id}`)
  await page.getByTestId('lens-analytics').click()

  await expect(page.getByTestId('lens-body')).toContainText('Restricted for this seat')
  await expect(page.getByTestId('profile-column')).toHaveCount(0)

  // Confirm the refusal is the real one, with the code the UI branches on.
  const refused = await api('POST', '/profile', {
    body: { dataset_id: ds.id, version_number: 1, sheet: 'data' },
    userId: viewer.user_id,
  })
  expect(refused.status).toBe(403)
  expect(refused.body.code).toBe('sensitive-data-restricted')
})

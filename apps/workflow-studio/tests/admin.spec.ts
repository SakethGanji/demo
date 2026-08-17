import { test, expect, goto, api, apiOk, ADMIN, DEFAULT_TEAM, uniqueName, TEST_PREFIX } from './fixtures'
import type { Harness } from './fixtures'
import type { Locator, Page } from '@playwright/test'

/**
 * The admin, governance and operations console.
 *
 * This page is the one place where a plausible-sounding wrong claim does real
 * damage: somebody reads it, believes a dataset is protected, and stops. So the
 * assertions here are deliberately grounded in a SECOND source rather than in
 * the page's own arithmetic:
 *
 *  - "masked" is checked against the masked set the SERVICE returns to a viewer
 *    (`masked_columns` on a query), not just against the dictionary the page
 *    read. If the page and the masking engine ever disagree, that is the bug
 *    this file exists to catch.
 *  - "classification enforces nothing" is checked by actually reading the rows
 *    of a `restricted` dataset as a viewer and finding the raw values there.
 *  - every write is verified by re-reading the API, never by a toast.
 *
 * Non-dataset resources (webhooks, teams, memberships) are NOT covered by the
 * prefix sweep, so they are registered below and removed in `afterAll`.
 */

/** An id that exists nowhere. Cross-tenant and absent are the same 404. */
const GHOST_DATASET = '11111111-2222-3333-4444-555555555555'

/**
 * Port 9 (discard) is closed on the loopback interface, so a delivery aimed at
 * it fails immediately rather than hanging or reaching anything real.
 */
const UNREACHABLE = 'http://127.0.0.1:9/uitest-admin-webhook'

interface DictEntry {
  column_name: string
  sensitivity?: string | null
}
interface SheetInfo {
  sheet_key: string
  column_count: number
  columns?: { name: string; normalized_name?: string | null }[]
}
interface Member {
  user_id: string
  email: string
  name: string
  role: string
}
interface Hook {
  id: string
  name: string
  url: string
  events: string[] | null
  enabled: boolean
  secret?: string
}
interface Delivery {
  id: string
  status: string
  attempts: number
  response_status: number | null
  error: string | null
}
interface AuditEntry {
  method: string
  path: string
  status_code: number | null
}

/* ------------------------------------------------------------- bookkeeping */

/**
 * Per-worker registries. Webhooks, teams and memberships are not datasets and
 * the name-prefix sweep never sees them.
 */
const createdWebhooks: string[] = []
const createdTeams: string[] = []

test.afterAll(async () => {
  for (const id of createdWebhooks) await api('DELETE', `/webhooks/${id}`)

  // Anything a crashed run of this spec left behind, found by name — but only
  // once it is old enough that no in-flight test could still own it. Both
  // workers share the prefix, and a sweep that took every matching name would
  // delete the other worker's subscription out from under its test. That is the
  // exact failure per-test cleanup used to cause on datasets.
  const hooks = await api<{ items: (Hook & { created_at: string })[] }>('GET', '/webhooks?limit=200')
  if (hooks.status === 200) {
    const stale = Date.now() - 15 * 60_000
    for (const w of hooks.body.items) {
      if (!w.name.startsWith(TEST_PREFIX)) continue
      const born = new Date(w.created_at).getTime()
      if (Number.isFinite(born) && born < stale) await api('DELETE', `/webhooks/${w.id}`)
    }
  }

  // A team cannot be deleted through the API and its last owner cannot be
  // removed (409), so the closest available cleanup is handing ownership to the
  // throwaway seat and dropping the System seat's membership — which is what
  // takes the team back out of the console's team list.
  for (const teamId of createdTeams) {
    const members = await api<{ items: Member[] }>('GET', `/teams/${teamId}/members`)
    if (members.status !== 200) continue
    let successor = members.body.items.find((m) => m.user_id !== ADMIN)?.user_id
    if (!successor) {
      // The test failed before it seated anyone. Make a successor rather than
      // leave a junk team in the console's own team list forever.
      const seat = await api<{ id: string }>('POST', '/auth/users', {
        body: { email: `${uniqueName('successor')}@example.com`, name: 'UI Test Successor', team_id: teamId },
      })
      if (seat.status >= 300) continue
      successor = seat.body.id
    }
    await api('PATCH', `/teams/${teamId}/members/${successor}`, { body: { role: 'owner' } })
    await api('DELETE', `/teams/${teamId}/members/${ADMIN}`)
  }
})

/* ----------------------------------------------------------------- helpers */

/** A count cell, with thousands separators stripped. */
function count(text: string): number {
  const digits = text.replace(/[^0-9-]/g, '')
  return digits === '' ? NaN : Number(digits)
}

/** The cell texts of one table row, in column order. */
function cells(row: Locator): Promise<string[]> {
  return row.locator('td').allInnerTexts()
}

/** One sheet's dictionary, read fresh. */
async function dictionary(datasetId: string, sheet = 'data'): Promise<DictEntry[]> {
  const page = await apiOk<{ items: DictEntry[] }>(
    'GET',
    `/datasets/${datasetId}/sheet-metadata/${sheet}/columns?limit=200`,
  )
  return page.items
}

/** The column list of a dataset's only sheet, read fresh. */
async function firstSheet(datasetId: string): Promise<SheetInfo> {
  const page = await apiOk<{ items: SheetInfo[] }>('GET', `/datasets/${datasetId}/sheets`)
  return page.items[0]
}

/**
 * What the masking engine itself hides from a seat — the second source. The
 * page derives its counts from the dictionary; this is the service's own
 * answer, and the two must agree.
 */
async function maskedForSeat(
  datasetId: string,
  userId: string,
  sheet = 'data',
): Promise<{ masked: string[]; rows: unknown[] }> {
  const res = await apiOk<{ items: unknown[]; masked_columns: string[] }>(
    'POST',
    `/datasets/${datasetId}/versions/1/sheets/${sheet}/query`,
    { body: { limit: 20 }, userId },
  )
  return { masked: res.masked_columns ?? [], rows: res.items }
}

/** The catalog row for a dataset, by exact name. */
async function catalogEntry(name: string): Promise<{ id: string; classification: string | null }> {
  const page = await apiOk<{ items: { id: string; name: string; classification: string | null }[] }>(
    'GET',
    `/datasets?q=${encodeURIComponent(name)}&limit=50`,
  )
  const hit = page.items.find((d) => d.name === name)
  if (!hit) throw new Error(`dataset ${name} not in the catalog`)
  return hit
}

/** The review-queue row for one dataset. Scoped by name — never a bare match. */
function reviewRow(page: Page, datasetName: string): Locator {
  return page.getByTestId('review-row').filter({ hasText: datasetName })
}

/**
 * The console opens EVERY dataset in the catalog to read its dictionary, so it
 * is uniquely exposed to another run's teardown sweep deleting a dataset
 * between the list call and the dictionary call. The page handles that (the row
 * becomes "no answer"), but Chromium logs the 404 — and its console message
 * carries no URL, so rule 5's allowance cannot be narrowed by text.
 *
 * The returned assertion is the narrowing: it fails if any 404 the browser saw
 * touched one of THIS test's own ids, which is the regression the console guard
 * is really there to catch.
 */
function allowForeignScan404s(page: Page, h: Harness, mine: string[]): () => void {
  h.allowError(/Failed to load resource: the server responded with a status of 404/)
  const seen: string[] = []
  page.on('response', (r) => {
    if (r.status() === 404) seen.push(r.url())
  })
  return () => {
    for (const url of seen) {
      for (const id of mine) {
        expect(url, `a 404 hit this test's own fixture`).not.toContain(id)
      }
    }
  }
}

/**
 * Poll a fresh API read until it produces a value, then return it.
 *
 * This is rule 2 in one function: a write is confirmed by reading the service
 * again, and the value that comes back is what the rest of the test asserts on.
 */
async function waitForValue<T>(read: () => Promise<T | null | undefined>, what: string): Promise<T> {
  await expect.poll(async () => (await read()) != null, { message: what }).toBe(true)
  const value = await read()
  if (value == null) throw new Error(`${what} disappeared between reads`)
  return value
}

/* ============================================================== governance */

test('a dataset labelled restricted with nothing declared masks nothing', async ({ page, h }) => {
  // THE assertion of this page. `classification` is catalog metadata; only a
  // column-level `sensitivity` masks. A screen that let those two blur would
  // send someone away believing this dataset is protected.
  const SENTINEL = 'LABELONLY-SENTINEL'
  const ds = await h.seed(
    Array.from({ length: 4 }, (_, i) => ({
      id: i,
      ssn: `${SENTINEL}-${i}`,
      city: `city-${i}`,
      amount: i * 3,
    })),
    'labelonly',
  )
  const noOwn404s = allowForeignScan404s(page, h, [ds.id])
  await apiOk('PATCH', `/datasets/${ds.id}`, { body: { classification: 'restricted' } })

  // Ground truth 1 — the catalog really does carry the scary label.
  expect((await catalogEntry(ds.name)).classification).toBe('restricted')

  // Ground truth 2 — and nothing at all is declared on it.
  const declared = (await dictionary(ds.id)).filter((e) => (e.sensitivity ?? '').trim() !== '')
  expect(declared).toEqual([])

  // Ground truth 3 — a viewer, the least privileged seat, reads it raw. This is
  // the label enforcing nothing, demonstrated rather than asserted.
  const viewer = await h.viewer()
  const seen = await maskedForSeat(ds.id, viewer.user_id)
  expect(seen.masked).toEqual([])
  expect(JSON.stringify(seen.rows)).toContain(SENTINEL)

  await goto(page, '/admin')

  // The page must SAY it, in the region that owns the labels.
  const statement = page.getByTestId('classification-statement')
  await expect(statement).toContainText(/not a control/i)
  await expect(statement).toContainText(/enforces nothing/i)
  await expect(statement).toContainText(/fully readable/i)

  // And it must name this dataset as label-only rather than leaving the reader
  // to work it out. The note is computed by the page; the fact is computed above.
  const labelOnly = page.getByTestId('label-only-note')
  await expect(labelOnly).toContainText(ds.name)
  await expect(labelOnly).toContainText(/Nothing is masked/i)

  // The label region is drawn unlike the sensitivity region on purpose: no
  // review status, no coverage bar. A "restricted" badge sitting beside a
  // masking figure is exactly the confusion this page exists to prevent.
  const labelCard = page.getByTestId('classification-card')
  await expect(labelCard.locator('[data-slot="status"]')).toHaveCount(0)
  await expect(labelCard.locator('[data-slot="magnitude-bar"]')).toHaveCount(0)

  // `restricted` is listed as a label with a real count — computed from the DOM.
  const labels = await page.getByTestId('classification-label').allInnerTexts()
  const restricted = labels.find((t) => t.trim().startsWith('restricted'))
  expect(restricted, `classification labels rendered: ${labels.join(' | ')}`).toBeTruthy()
  // At least this fixture. An exact equality against the facets endpoint would
  // race every other worker relabelling its own datasets.
  expect(count(restricted as string)).toBeGreaterThanOrEqual(1)

  // The queue row for it says 0 masked, everything undeclared.
  const row = reviewRow(page, ds.name)
  await expect(row).toHaveCount(1)
  expect(count(await row.getByTestId('review-masked').innerText())).toBe(0)
  const sheet = await firstSheet(ds.id)
  expect(count(await row.getByTestId('review-undeclared').innerText())).toBe(sheet.column_count)
  await expect(row.locator('[data-slot="status"]')).toHaveText(/unreviewed/)
  noOwn404s()
})

test('the review queue counts masked, declared-open and undeclared from the dictionary', async ({
  page,
  h,
}) => {
  // Six columns. One declared at a level that masks, one at a level that does
  // not (`internal` — one of the field's own documented examples), four never
  // looked at.
  const SENTINEL = 'QUEUE-SENTINEL'
  const ds = await h.seed(
    Array.from({ length: 5 }, (_, i) => ({
      id: i,
      ssn: `${SENTINEL}-${i}`,
      note: `note-${i}`,
      city: `city-${i}`,
      amount: i,
      region: `r-${i}`,
    })),
    'queue',
  )
  const noOwn404s = allowForeignScan404s(page, h, [ds.id])
  await h.markSensitive(ds.id, 'ssn') // → confidential, which masks
  await apiOk('PUT', `/datasets/${ds.id}/sheet-metadata/data/columns/note`, {
    body: { sensitivity: 'internal' },
  })

  // Expected values, computed from fresh reads — never typed in.
  const sheet = await firstSheet(ds.id)
  const columns = sheet.column_count
  const entries = (await dictionary(ds.id)).filter((e) => (e.sensitivity ?? '').trim() !== '')
  const viewer = await h.viewer()
  const seen = await maskedForSeat(ds.id, viewer.user_id)

  // The service's own masked set is the authority for "masked".
  expect(seen.masked).toEqual(['ssn'])
  const expectedMasked = seen.masked.length
  const expectedDeclaredOpen = entries.length - expectedMasked
  const expectedUndeclared = columns - entries.length
  expect(expectedDeclaredOpen).toBe(1)
  expect(expectedUndeclared).toBe(columns - 2)

  // `internal` is declared and inert: the value is still readable.
  expect(JSON.stringify(seen.rows)).toContain('note-3')
  expect(JSON.stringify(seen.rows)).not.toContain(SENTINEL)

  await goto(page, '/admin')

  const row = reviewRow(page, ds.name)
  await expect(row).toHaveCount(1)
  const text = await cells(row)
  // 0 name · 1 columns · 2 masked · 3 declared open · 4 undeclared · 5 bar · 6 review
  expect(count(text[1])).toBe(columns)
  expect(count(text[2])).toBe(expectedMasked)
  expect(count(text[3])).toBe(expectedDeclaredOpen)
  expect(count(text[4])).toBe(expectedUndeclared)
  expect(count(await row.getByTestId('review-masked').innerText())).toBe(expectedMasked)
  expect(count(await row.getByTestId('review-undeclared').innerText())).toBe(expectedUndeclared)
  await expect(row.locator('[data-slot="status"]')).toHaveText(/unreviewed/)

  // The inert level is named rather than counted as protection.
  await expect(page.getByTestId('inert-levels')).toContainText('internal')

  // And the page states where masking actually comes from.
  const statement = page.getByTestId('sensitivity-statement')
  await expect(statement).toContainText(/declared by a person/i)
  await expect(statement).toContainText(/no scanner/i)

  // The strip totals are sums over every scanned dataset, so they can only be
  // asserted as "at least this fixture" while other workers are seeding.
  expect(count(await page.getByTestId('metric-masked').innerText())).toBeGreaterThanOrEqual(
    expectedMasked,
  )
  expect(count(await page.getByTestId('metric-undeclared').innerText())).toBeGreaterThanOrEqual(
    expectedUndeclared,
  )
  noOwn404s()
})

test('a viewer reads masked and only admin, owner or superuser reads raw', async ({ page, h }) => {
  const viewer = await h.viewer()

  // The claim, tested against behaviour before it is tested against text.
  const ds = await h.seed(
    Array.from({ length: 4 }, (_, i) => ({ id: i, ssn: `ROLE-SENTINEL-${i}` })),
    'roles',
  )
  const noOwn404s = allowForeignScan404s(page, h, [ds.id])
  await h.markSensitive(ds.id, 'ssn')
  const asViewer = await maskedForSeat(ds.id, viewer.user_id)
  expect(asViewer.masked).toEqual(['ssn'])
  expect(JSON.stringify(asViewer.rows)).not.toContain('ROLE-SENTINEL')
  const asAdmin = await maskedForSeat(ds.id, ADMIN)
  expect(asAdmin.masked).toEqual([])
  expect(JSON.stringify(asAdmin.rows)).toContain('ROLE-SENTINEL')

  await goto(page, '/admin')

  // Every seat row must agree with the rule, whatever roles this team holds.
  const members = await apiOk<{ items: Member[] }>('GET', `/teams/${DEFAULT_TEAM}/members`)
  const seesRaw = (role: string) => role.toLowerCase() === 'admin' || role.toLowerCase() === 'owner'
  await expect(page.getByTestId('seat-row')).toHaveCount(members.items.length)

  for (const m of members.items) {
    const row = page.getByTestId('seat-row').filter({ hasText: m.email })
    await expect(row).toHaveCount(1)
    const text = await cells(row)
    // 0 seat · 1 email · 2 role · 3 sees values as · 4 manage
    expect(text[2].trim()).toBe(m.role)
    const access = text[3]
    expect(
      /\braw\b/.test(access),
      `${m.email} is ${m.role} and the page reads "${access.trim()}"`,
    ).toBe(seesRaw(m.role))
    if (!seesRaw(m.role)) expect(access).toMatch(/masked/)
    // An editor outranks a viewer and still cannot read values; the page must
    // say why, or the row looks like a bug.
    if (m.role.toLowerCase() === 'editor') expect(access).toMatch(/withheld by policy/i)
  }

  // The viewer specifically — the seat `h.viewer()` hands out.
  const seat = members.items.find((m) => m.user_id === viewer.user_id)
  expect(seat?.role).toBe('viewer')
  const viewerRow = page.getByTestId('seat-row').filter({ hasText: seat!.email })
  await expect(viewerRow).toHaveCount(1)
  const viewerAccess = (await cells(viewerRow))[3]
  expect(viewerAccess).toMatch(/masked/)
  expect(viewerAccess).not.toMatch(/\braw\b/)

  // And the seat doing the looking: a platform superuser resolves raw.
  await expect(page.getByTestId('admin-scope')).toContainText('superuser')
  await expect(page.getByTestId('admin-scope')).toContainText('sees raw')

  // The rule is stated where the roles are listed, including the editor carve-out.
  await expect(page.getByTestId('seats-card')).toContainText(
    /only\s+admin,\s+owner\s+and\s+a\s+platform\s+superuser/i,
  )
  await expect(page.getByTestId('read-sensitive-note')).toContainText(/withheld from editor/i)
  noOwn404s()
})

test('nothing on the console renders access denied or forbidden', async ({ page, h }) => {
  // A cross-tenant read is a 404 by design, so no surface may translate one
  // into a refusal — "not found" and "not yours" are the same answer.
  const viewer = await h.viewer()
  const ghost = await api('GET', `/datasets/${GHOST_DATASET}/versions`, { userId: viewer.user_id })
  expect(ghost.status).toBe(404)

  allowForeignScan404s(page, h, [])
  await goto(page, '/admin')

  // Wait for every region to have actually rendered, or "the word is absent"
  // is a claim about an empty page.
  await expect(page.getByTestId('team-row').first()).toBeVisible()
  await expect(page.getByTestId('seat-row').first()).toBeVisible()
  await expect(page.getByTestId('retention-row').first()).toBeVisible()
  await expect(page.getByTestId('webhooks-card')).toBeVisible()
  await expect(page.getByTestId('audit-row').first()).toBeVisible()
  await expect(page.getByTestId('review-row').first()).toBeVisible()
  await expect(page.getByTestId('storage-total')).not.toHaveText('—')

  const body = await page.locator('body').innerText()
  expect(body).not.toMatch(/access denied/i)
  expect(body).not.toMatch(/\bforbidden\b/i)
  expect(body).not.toMatch(/not authori[sz]ed/i)
  expect(body).not.toMatch(/permission denied/i)

  // The positive half: the page explains the 404 rather than inventing a 403.
  await expect(page.getByTestId('read-sensitive-note')).toContainText(/404/)
  await expect(page.getByTestId('read-sensitive-note')).toContainText(/never 403/i)
  await expect(page.getByTestId('teams-card')).toContainText(/404/)
})

/* ================================================================= storage */

test('retention describes eligibility, and the sweep is a manual one-shot', async ({ page, h }) => {
  const policy = await apiOk<{
    rules: { artifact_type: string; retention_days: number | null }[]
    orphan_grace_hours: number
    expired_pending: number
  }>('GET', '/storage/retention')
  const usage = await apiOk<{ total_bytes: number }>('GET', '/storage/usage')

  allowForeignScan404s(page, h, [])
  await goto(page, '/admin')

  // Every rule the service holds is rendered, with its own value.
  await expect(page.getByTestId('retention-row')).toHaveCount(policy.rules.length)
  for (const rule of policy.rules) {
    const row = page.getByTestId('retention-row').filter({ hasText: rule.artifact_type })
    await expect(row).toHaveCount(1)
    const text = await cells(row)
    if (rule.retention_days == null) expect(text[1]).toMatch(/indefinitely/i)
    else expect(count(text[1])).toBe(rule.retention_days)
  }

  // The total is the object store's, not a placeholder: parse the rendered
  // figure back to bytes rather than re-implementing the formatter.
  const totalText = await page.getByTestId('storage-total').innerText()
  const parsed = /([\d.]+)\s*(B|KB|MB|GB|TB)/.exec(totalText)
  expect(parsed, `storage total rendered as "${totalText}"`).toBeTruthy()
  const scale = { B: 1, KB: 1024, MB: 1024 ** 2, GB: 1024 ** 3, TB: 1024 ** 4 }
  const shown = Number(parsed![1]) * scale[parsed![2] as keyof typeof scale]
  expect(Math.abs(shown - usage.total_bytes)).toBeLessThanOrEqual(
    Math.max(1, usage.total_bytes * 0.05),
  )

  // `expired_pending` is past-deadline AND still present.
  expect(count(await page.getByTestId('expired-pending').innerText())).toBe(policy.expired_pending)
  await expect(page.getByTestId('storage-card')).toContainText(
    /artifacts eligible for collection — not collected/i,
  )

  // Nothing expires on its own, and the page has to say so.
  const statement = page.getByTestId('retention-statement')
  await expect(statement).toContainText(/no scheduler/i)
  await expect(statement).toContainText(/no cron/i)
  await expect(statement).toContainText(/deletes nothing/i)

  const guard = page.getByTestId('gc-guard')
  await expect(guard).toContainText(/only thing that ever removes an expired artifact/i)
  await expect(guard).toContainText(/One sweep runs when it is pressed and then stops/i)
  await expect(guard).toContainText(/bounded per call/i)
  await expect(page.getByTestId('run-gc')).toHaveText(/Run sweep now/i)

  // Press it. This is the only collector in the system.
  await page.getByTestId('run-gc').click()
  const result = page.getByTestId('gc-result')
  await expect(result).toBeVisible()
  const resultText = await result.innerText()
  expect(resultText).toMatch(/Last sweep:/)
  expect(resultText).toMatch(/more remaining|backlog reached/)

  // "backlog reached" is a claim about the store, so check the store.
  const after = await apiOk<{ expired_pending: number }>('GET', '/storage/retention')
  if (/backlog reached/.test(resultText)) expect(after.expired_pending).toBe(0)
  expect(after.expired_pending).toBeLessThanOrEqual(policy.expired_pending)
})

/* ================================================================ webhooks */

test('a subscription is created with its secret shown once, toggled, then deleted', async ({
  page,
  h,
}) => {
  const name = uniqueName('hook')
  allowForeignScan404s(page, h, [])
  await goto(page, '/admin')

  await page.getByTestId('webhook-name').fill(name)
  await page.getByTestId('webhook-url').fill(UNREACHABLE)
  await page
    .getByTestId('webhook-events')
    .locator('label')
    .filter({ hasText: 'dataset.published' })
    .locator('input')
    .check()
  await page.getByTestId('create-webhook').click()

  // Verified by re-reading, not by the toast.
  const created = await waitForValue(async () => {
    const list = await apiOk<{ items: Hook[] }>('GET', '/webhooks?limit=200')
    return list.items.find((w) => w.name === name)
  }, `subscription ${name} in GET /webhooks`)
  createdWebhooks.push(created.id)
  expect(created.url).toBe(UNREACHABLE)
  expect(created.events).toEqual(['dataset.published'])
  expect(created.enabled).toBe(true)

  // The signing secret is shown once and is never readable again.
  const secretBox = page.getByTestId('webhook-secret')
  await expect(secretBox).toBeVisible()
  const secretText = await secretBox.innerText()
  expect(secretText).toMatch(/shown once/i)
  const secret = /whsec_[A-Za-z0-9_-]+/.exec(secretText)
  expect(secret, `no secret rendered in: ${secretText}`).toBeTruthy()

  const listAgain = await apiOk<{ items: Hook[] }>('GET', '/webhooks?limit=200')
  expect(JSON.stringify(listAgain)).not.toContain(secret![0])
  const detail = await apiOk<Hook>('GET', `/webhooks/${created.id}`)
  expect(detail.secret ?? null).toBeNull()
  expect(JSON.stringify(detail)).not.toContain(secret![0])

  // The row — scoped by name, never a bare "Disable".
  const row = page.getByTestId('webhook-row').filter({ hasText: name })
  await expect(row).toHaveCount(1)
  await expect(row.locator('[data-slot="status"]')).toHaveText(/enabled/)

  await row.getByTestId('toggle-webhook').click()
  await expect
    .poll(async () => {
      const list = await apiOk<{ items: Hook[] }>('GET', '/webhooks?limit=200')
      return list.items.find((w) => w.id === created.id)?.enabled
    })
    .toBe(false)
  await expect(row.locator('[data-slot="status"]')).toHaveText(/disabled/)
  await expect(row.getByTestId('toggle-webhook')).toHaveText(/Enable/)

  // Delete through the confirmation, then prove it is gone from the service.
  await row.getByTestId('delete-webhook').click()
  await expect(page.getByTestId('delete-webhook-dialog')).toBeVisible()
  await page.getByTestId('confirm-delete-webhook').click()

  await expect
    .poll(async () => {
      const list = await apiOk<{ items: Hook[] }>('GET', '/webhooks?limit=200')
      return list.items.some((w) => w.id === created.id)
    })
    .toBe(false)
  expect((await api('GET', `/webhooks/${created.id}`)).status).toBe(404)
  await expect(page.getByTestId('webhook-row').filter({ hasText: name })).toHaveCount(0)
})

test('a test delivery is a real attempt and is recorded as one', async ({ page, h }) => {
  // Pointed at a closed port on purpose: "Send real delivery" is not a dry run,
  // and the only safe target is one that cannot receive anything.
  const name = uniqueName('hookdeliv')
  const hook = await apiOk<Hook>('POST', '/webhooks', {
    body: { name, url: UNREACHABLE, events: [], enabled: true },
  })
  createdWebhooks.push(hook.id)

  const before = await apiOk<{ total: number }>('GET', `/webhooks/${hook.id}/deliveries`)
  expect(before.total).toBe(0)

  const noOwn404s = allowForeignScan404s(page, h, [hook.id])
  await goto(page, '/admin')
  const row = page.getByTestId('webhook-row').filter({ hasText: name })
  await expect(row).toHaveCount(1)

  // No `h.allowError` is needed here: the service answers 200 and reports the
  // failed attempt in the body, so nothing 5xxs and the browser logs nothing.
  await row.getByTestId('test-webhook').click()

  // Scoped to the manual attempt by event type. This subscription is live and
  // takes every event, so another worker publishing a dataset really does add a
  // delivery row here — which is the subscription working, not noise to hide.
  const attempt = await waitForValue(async () => {
    const d = await apiOk<{ items: (Delivery & { event_type: string })[] }>(
      'GET',
      `/webhooks/${hook.id}/deliveries`,
    )
    return d.items.find((x) => x.event_type === 'webhook.test')
  }, 'a recorded webhook.test delivery attempt')
  expect(attempt.attempts).toBeGreaterThanOrEqual(1)
  expect(attempt.status).toBe('failed')
  expect(attempt.response_status).toBeNull()
  expect(attempt.error, 'a failed delivery must record why').toBeTruthy()

  // The page shows the same attempt, with its state and its reason.
  const deliveryRow = page.getByTestId('delivery-row').filter({ hasText: 'webhook.test' })
  await expect(deliveryRow).toHaveCount(1)
  await expect(deliveryRow.locator('[data-slot="status"]')).toHaveText(new RegExp(attempt.status))
  await expect(deliveryRow).toContainText(attempt.error as string)
  await expect(page.getByTestId('test-delivery-note')).toContainText(/not a\s+dry run/i)
  noOwn404s()
})

/* =============================================================== audit log */

test('the audit log carries a known action and pages without editorialising a 404', async ({
  page,
  h,
}) => {
  const ds = await h.seed([{ a: 1 }], 'audit')
  // Two known actions: one write that succeeded, and one that was answered 404
  // because the id exists nowhere — which is the same answer "exists, but not
  // yours" gets. The second is issued as a viewer, so a service that leaked
  // "you may not" would have had every opportunity to.
  await apiOk('PATCH', `/datasets/${ds.id}`, { body: { description: `audited ${ds.name}` } })
  const viewer = await h.viewer()
  const ghost = await api('PATCH', `/datasets/${GHOST_DATASET}`, {
    body: { description: 'never lands' },
    userId: viewer.user_id,
  })
  expect(ghost.status).toBe(404)

  const noOwn404s = allowForeignScan404s(page, h, [ds.id])
  const writePath = `/api/v1/datasets/${ds.id}`
  const ghostPath = `/api/v1/datasets/${GHOST_DATASET}`

  // The service must have recorded both before the page can be blamed for not
  // showing them.
  await expect
    .poll(async () => {
      const log = await apiOk<{ items: AuditEntry[] }>('GET', '/audit?limit=200')
      const write = log.items.find((e) => e.method === 'PATCH' && e.path === writePath)
      const miss = log.items.find((e) => e.method === 'PATCH' && e.path === ghostPath)
      return Boolean(write) && miss?.status_code === 404
    })
    .toBe(true)

  await goto(page, '/admin')

  // The half of the statement this test stands behind: the log is a record of
  // what was answered, not a control. Its other half — "a cross-tenant read
  // appears here as the 404 it was" — is wrong for reads; see the marked
  // failure below.
  await expect(page.getByTestId('audit-statement')).toContainText(/a record, not a control/i)

  /** Find a row across pages — the log is busy while other workers drive it. */
  const findRow = async (path: string, method: string): Promise<Locator | null> => {
    for (let i = 0; i < 6; i++) {
      const row = page
        .getByTestId('audit-row')
        .filter({ has: page.locator(`[title="${path}"]`) })
        .filter({ hasText: method })
      if ((await row.count()) > 0) return row.first()
      const older = page.getByTestId('audit-older')
      if (await older.isDisabled()) return null
      const range = await page.getByTestId('audit-range').innerText()
      await older.click()
      await expect(page.getByTestId('audit-range')).not.toHaveText(range)
    }
    return null
  }

  const writeRow = await findRow(writePath, 'PATCH')
  expect(writeRow, `no audit row for PATCH ${writePath}`).not.toBeNull()
  const writeCells = await cells(writeRow as Locator)
  // 0 when · 1 actor · 2 method · 3 path · 4 answered · 5 ms · 6 resource · 7 detail
  expect(writeCells[2].trim()).toBe('PATCH')
  expect(count(writeCells[4])).toBe(200)

  // Reload rather than paging back, so the 404 search starts from page one too.
  await goto(page, '/admin')
  const missRow = await findRow(ghostPath, 'PATCH')
  expect(missRow, `no audit row for PATCH ${ghostPath}`).not.toBeNull()
  const missCells = await cells(missRow as Locator)
  expect(count(missCells[4])).toBe(404)
  // The log records the answer; it does not translate it into a refusal.
  const missText = await (missRow as Locator).innerText()
  expect(missText).not.toMatch(/denied|forbidden|refused|blocked/i)

  // Paging: the window is stated, and moving changes it by exactly one page.
  await goto(page, '/admin')
  const firstPageRows = await page.getByTestId('audit-row').count()
  const range = page.getByTestId('audit-range')
  await expect(range).toHaveText(new RegExp(`^1–${firstPageRows}\\b`))
  await expect(page.getByTestId('audit-newer')).toBeDisabled()

  // Timestamps, newest first. Both invariants below are stated against these
  // rather than against row identity: the log is appended to continuously while
  // the other workers drive the API, so an offset window is NOT stable content
  // and asserting "page two differs from page one" would be asserting a race.
  const stamps = () =>
    page
      .getByTestId('audit-row')
      .evaluateAll((rows) => rows.map((r) => r.querySelector('td')?.textContent?.trim() ?? ''))

  const firstPage = await stamps()
  expect([...firstPage].sort().reverse()).toEqual(firstPage)

  await page.getByTestId('audit-older').click()
  await expect(range).toHaveText(new RegExp(`^${firstPageRows + 1}–`))
  const secondPage = await stamps()
  expect(secondPage.length).toBeGreaterThan(0)
  expect([...secondPage].sort().reverse()).toEqual(secondPage)
  // Paging goes backwards in time. New entries arrive at the head and can push
  // a row from page one onto page two, but nothing NEWER than page one's newest
  // may ever appear deeper in the log.
  expect(secondPage[0] <= firstPage[0]).toBe(true)

  await page.getByTestId('audit-newer').click()
  await expect(range).toHaveText(new RegExp(`^1–`))
  await expect(page.getByTestId('audit-newer')).toBeDisabled()
  noOwn404s()
})

/**
 * DEFECT — the console's own claim about the log is broader than the log.
 *
 * `audit-statement` reads: "A cross-tenant read appears here as the 404 it was".
 * It does not. The service records only mutating methods — a scan of 200 recent
 * entries contains POST, PUT, PATCH and DELETE and no GET at all — so a
 * cross-tenant READ is recorded nowhere, and an admin who takes the sentence at
 * face value would conclude an unauthorised read never happened.
 *
 * The write-shaped 404 in the test above IS recorded, and correctly, so the
 * honest wording is about a cross-tenant *write*, or the log needs to record
 * reads. Marked `test.fail` because the behaviour is the service's and the fix
 * belongs to whoever owns the sentence — not to this file.
 */
test('only raw-egress reads are audited, and the page says exactly that', async ({ page, h }) => {
  // The page used to say "a cross-tenant read appears here as the 404 it was".
  // It does not. The service audits every MUTATION plus exactly the reads that
  // take data out of the system — `/download` and `/samples/{file}/data`. An
  // ordinary refused GET leaves no trace, so an admin taking the old sentence
  // at face value would conclude an unauthorised read never happened.
  //
  // This pins BOTH halves — the behaviour and the wording — so they cannot
  // drift apart again.
  const viewer = await h.viewer()
  const ghost = await api('GET', `/datasets/${GHOST_DATASET}/versions`, { userId: viewer.user_id })
  expect(ghost.status).toBe(404)

  // Give the service a beat to write an entry if it were going to.
  await new Promise((r) => setTimeout(r, 1_500))
  const log = await apiOk<{ items: AuditEntry[] }>('GET', '/audit?limit=200')
  const readEntry = log.items.find(
    (e) => e.path === `/api/v1/datasets/${GHOST_DATASET}/versions` && e.method === 'GET',
  )
  expect(readEntry, 'a refused ordinary GET was audited — the page copy needs updating').toBeUndefined()

  // The GETs that ARE recorded are only the egress ones. If an ordinary read
  // path ever starts appearing here, the copy is wrong again.
  const auditedGets = log.items.filter((e) => e.method === 'GET')
  for (const e of auditedGets) {
    expect(
      /\/download|\/samples\/[^/]+\/data/.test(e.path),
      `an ordinary GET is being audited: ${e.path}`,
    ).toBe(true)
  }

  // And the page says so, rather than promising a row that will never exist.
  allowForeignScan404s(page, h, [])
  await goto(page, '/admin')
  const statement = page.getByTestId('audit-statement')
  await expect(statement).toContainText('Every mutation is recorded')
  await expect(statement).toContainText('take data out')
  await expect(statement).toContainText('leaves no entry')
})

/* ========================================================== teams and seats */

test('creating a team, seating someone, changing their role and removing them', async ({
  page,
  h,
}) => {
  const teamName = uniqueName('team')
  allowForeignScan404s(page, h, [])
  await goto(page, '/admin')

  await page.getByTestId('new-team-name').fill(teamName)
  await page.getByTestId('create-team').click()
  await expect(page.getByTestId('created-team')).toContainText(teamName)

  // Verified by re-reading the membership list, not by the guard note.
  const teams = await apiOk<{ items: { team_id: string; team_name: string; role: string }[] }>(
    'GET',
    '/teams',
  )
  const mine = teams.items.find((t) => t.team_name === teamName)
  expect(mine, 'the created team is absent from GET /teams').toBeTruthy()
  createdTeams.push(mine!.team_id)
  expect(mine!.role).toBe('owner')
  await expect(page.getByTestId('team-row').filter({ hasText: teamName })).toHaveCount(1)

  // Work inside the new team so the Default team — which other workers share —
  // is never mutated by this test.
  await page.evaluate(
    (identity) => window.localStorage.setItem('studio.identity', JSON.stringify(identity)),
    { userId: ADMIN, teamId: mine!.team_id, label: 'System (admin)' },
  )
  await goto(page, '/admin')
  await expect(page.getByTestId('admin-scope')).toContainText(teamName)

  const membersNow = await apiOk<{ items: Member[] }>('GET', `/teams/${mine!.team_id}/members`)
  await expect(page.getByTestId('seat-row')).toHaveCount(membersNow.items.length)

  // Create a seat through the console. `team_id` defaults to the acting team.
  const email = `${uniqueName('seat')}@example.com`
  await page.getByTestId('seat-email').fill(email)
  await page.getByTestId('seat-name').fill('UI Test Admin Seat')
  await page.getByTestId('create-seat').click()
  await expect(page.getByTestId('created-seat')).toContainText(email)

  const seated = await waitForValue(async () => {
    const m = await apiOk<{ items: Member[] }>('GET', `/teams/${mine!.team_id}/members`)
    return m.items.find((x) => x.email === email)
  }, `${email} in GET /teams/{id}/members`)
  expect(seated.role).toBe('viewer')

  const row = page.getByTestId('seat-row').filter({ hasText: email })
  await expect(row).toHaveCount(1)

  // Cycle the role through all four and check both the persisted value and what
  // the page says the seat resolves. viewer and editor read masked; admin and
  // owner read raw.
  const expected: Record<string, RegExp> = {
    viewer: /masked/,
    editor: /masked/,
    admin: /\braw\b/,
    owner: /\braw\b/,
  }
  for (const role of ['editor', 'admin', 'owner', 'viewer']) {
    await row.getByTestId('member-role').selectOption(role)
    await expect
      .poll(async () => {
        const m = await apiOk<{ items: Member[] }>('GET', `/teams/${mine!.team_id}/members`)
        return m.items.find((x) => x.email === email)?.role
      })
      .toBe(role)
    await expect(row.locator('td').nth(2)).toHaveText(role)
    const access = row.locator('td').nth(3)
    await expect(access).toHaveText(expected[role])
    if (role === 'editor') await expect(access).toContainText(/withheld by policy/i)
    if (role === 'viewer' || role === 'editor') await expect(access).not.toHaveText(/\braw\b/)
  }

  // Remove the membership — scoped to the row, and confirmed by re-reading.
  await row.getByTestId('remove-member').click()
  await expect(page.getByTestId('remove-member-dialog')).toBeVisible()
  await page.getByTestId('confirm-remove-member').click()
  await expect
    .poll(async () => {
      const m = await apiOk<{ items: Member[] }>('GET', `/teams/${mine!.team_id}/members`)
      return m.items.some((x) => x.email === email)
    })
    .toBe(false)
  await expect(page.getByTestId('seat-row').filter({ hasText: email })).toHaveCount(0)

  // And put them back through the add-member form, by exact email.
  await page.getByTestId('member-ref').fill(email)
  await page.getByTestId('add-member').click()
  await expect
    .poll(async () => {
      const m = await apiOk<{ items: Member[] }>('GET', `/teams/${mine!.team_id}/members`)
      return m.items.find((x) => x.email === email)?.role ?? null
    })
    .toBe('viewer')
  await expect(page.getByTestId('seat-row').filter({ hasText: email })).toHaveCount(1)
})

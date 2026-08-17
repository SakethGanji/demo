import { test, expect, goto, pickScope } from './fixtures'

/**
 * How failures are rendered.
 *
 * These three cover defects found in a code audit, and all three share a
 * shape: the UI was telling the user something that was not true. A raw error
 * code presented as a sentence, an error box sitting beside "there is nothing
 * here", and a header that silently stopped being sent. None of them threw, so
 * none of them would ever have failed a test that only asserted on happy paths.
 */

test('a dataset that is not readable explains itself without leaking the error code', async ({
  page,
  h,
}) => {
  // A well-formed id that cannot resolve. Cross-tenant reads answer 404 too, so
  // this is the same code path a foreign team's dataset takes.
  const missing = '00000000-0000-4000-8000-0000000000ff'
  h.allowError(/404 \(Not Found\)/)

  await goto(page, `/data?dataset=${missing}`)

  const box = page.getByTestId('data-error')
  await expect(box).toBeVisible()
  const text = (await box.textContent()) ?? ''

  // The specific, human message — not the generic one, because this call site
  // knows it was loading a dataset.
  expect(text).toContain('no readable version')

  // The bug: `${error.code}: ${error.detail}`, which put the problem+json code
  // in front of the user. A code is a branching key for the client, not a
  // sentence. Assert on the machine-readable spellings the service actually
  // emits rather than on the word "code".
  expect(text).not.toMatch(/not_found/)
  expect(text).not.toMatch(/^[a-z_-]+:/)

  // 404 hides existence on purpose — never translate it into a refusal.
  expect(text.toLowerCase()).not.toContain('access denied')
  expect(text.toLowerCase()).not.toContain('forbidden')
})

test('a failed lens fetch shows the error alone, never alongside an empty state', async ({
  page,
  h,
}) => {
  const ds = await h.seed([{ id: 1, name: 'a' }], 'lenserr')

  // Force the transformations read to fail. This is the only way to reach the
  // branch: the endpoint succeeds with an empty list in every normal run, which
  // is exactly why the bug survived.
  await page.route('**/transformations**', (route) =>
    route.fulfill({
      status: 500,
      contentType: 'application/problem+json',
      body: JSON.stringify({ code: 'internal_error', detail: 'Injected failure', status: 500 }),
    }),
  )
  h.allowError(/500/)
  h.allowError(/Injected failure/)

  await goto(page, `/data?dataset=${ds.id}`)
  await page.getByTestId('lens-transform').click()

  const panel = page.getByTestId('lens-body')
  await expect(panel.getByText('Injected failure')).toBeVisible()

  // The defect: the transform lens omitted the `!error` guard its siblings had,
  // so a failed fetch rendered the error AND "No saved transformations for this
  // dataset". An error that also asserts the list is empty invites the reader
  // to believe the second sentence.
  await expect(panel.getByText('No saved transformations for this dataset.')).toHaveCount(0)
})

test('switching seat keeps sending the team header', async ({ page, h }) => {
  const ds = await h.seed([{ id: 1, name: 'a' }], 'teamhdr')
  const viewer = await h.viewer()

  await goto(page, `/data?dataset=${ds.id}`)

  // Record the team header on analytics reads made AFTER the switch.
  const teamHeaders: (string | undefined)[] = []
  page.on('request', (req) => {
    if (req.url().includes('/api/v1/datasets')) {
      teamHeaders.push(req.headers()['x-team-id'])
    }
  })

  // Arm the wait BEFORE the action, then await it. Polling a counter after the
  // fact raced the refetch under full-suite load and made this flaky — and a
  // flaky test is worse than no test, because it teaches people to re-run.
  const reread = page.waitForResponse(
    (r) => r.url().includes('/api/v1/datasets') && r.request().method() === 'GET',
    { timeout: 30_000 },
  )
  await pickScope(page, 'Acting seat', `${viewer.name} (${viewer.role})`)
  await reread

  // The defect: the switcher wrote only { userId, label }, dropping `teamId`,
  // so `X-Team-Id` was never sent again for the rest of the session. The
  // service then fell back to the user's default team — right by luck for a
  // single-team user, wrong for anyone who belongs to two.
  expect(teamHeaders.some((v) => typeof v === 'string' && v.length > 0)).toBe(true)
})

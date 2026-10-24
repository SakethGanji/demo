/**
 * Writes in a notebook (Workbench v3): a change cell proposes a diff on the writable bank connection and nothing is
 * written until "Approve & apply"; an edit after the proposal makes it out of date (409 proposal_stale) with a
 * rerun action; the applied card carries attribution and the run resumes to the read below it;
 * rerunning past an applied write stops at the rerun bar, and "Skip it and continue" keeps the earlier result.
 * Only bank.wires W4 is written (status → released; idempotent across reruns).
 */
import { test, expect } from '../fixtures/test'
import { addCell, bankProject, CAPS_PROBE, cellEl, newNotebook, notBuilt, openNotebook, settled } from '../fixtures/notebook'

test('change → diff → stale proposal → apply with attribution → resume → rerun bar skip', async ({ page, api, s, net }) => {
  const bp = await bankProject(api)
  const nb = await newNotebook(api, bp.id)
  await addCell(api, nb.id, 'query', { connection: s.nb_conn_bank ?? 'bank', sql: "SELECT wire_id, status FROM bank.wires WHERE wire_id = 'W4'", limit: 10 }, { title: 'The wire' })
  await addCell(api, nb.id, 'change', {
    connection: s.nb_conn_bank_rw ?? 'bank_rw',
    table: 'bank.wires',
    key: { wire_id: '{{cells.c1.rows[0].wire_id}}' },
    set: { status: 'released' },
    reason: 'Pending too long',
  }, { title: 'Release it' })
  await addCell(api, nb.id, 'query', { connection: s.nb_conn_bank ?? 'bank', sql: "SELECT status FROM bank.wires WHERE wire_id = 'W4'", limit: 10 }, { title: 'Read it back' })
  const caps = await notBuilt(api, [CAPS_PROBE])

  await net.expect(caps, async () => {
    await openNotebook(page, s, bp.slug, nb.id)
    await expect(cellEl(page, 'c2')).toContainText('writes')
    await page.getByTestId('nb-run').click()
    await expect(page.getByTestId('nb-status')).toContainText('Needs you', { timeout: 30_000 })
  }, caps.length ? 10_000 : 0)

  const c2 = cellEl(page, 'c2')
  await expect(c2.getByTestId('nb-change-pending')).toBeVisible()
  await expect(c2).toContainText('released')
  // the workspace follows the write
  await expect(page.getByTestId('nb-workspace')).toHaveAttribute('data-tab', 'change')

  // edit the reason after the proposal → out of date; Apply is refused (409) and says so
  await c2.getByRole('button', { name: /^reason:/ }).click()
  const reason = page.getByRole('textbox', { name: 'reason' })
  await reason.fill('Pending past the settlement window')
  await reason.press('Enter')
  await expect(c2.getByTestId('nb-change-stale')).toBeVisible()
  await net.expect([{ method: 'POST', url: /\/api\/notebook-cells\/[^/]+\/apply$/, status: 409 }], async () => {
    await c2.getByTestId('nb-change-apply').click()
  })
  await c2.getByTestId('nb-change-stale').getByRole('button', { name: /Rerun from here/ }).click()
  await expect(c2.getByTestId('nb-change-stale')).toHaveCount(0, { timeout: 30_000 })
  await expect(c2.getByTestId('nb-change-pending')).toBeVisible()

  // approve & apply → attribution; the run resumes and reads the written row back
  await c2.getByTestId('nb-change-apply').click()
  await expect(c2.getByTestId('nb-change-applied')).toContainText('by you', { timeout: 30_000 })
  await expect(page.getByTestId('nb-status')).toContainText('Done', { timeout: 30_000 })

  const done = await settled(api, nb.id)
  const applied = done.cells.find((c) => c.name === 'c2')!.latest!
  expect(applied.status).toBe('applied')
  expect(applied.output).toMatchObject({ applied_by: 'you' })
  expect((done.cells.find((c) => c.name === 'c3')!.latest!.output as { rows: unknown[] }).rows).toEqual([{ status: 'released' }])

  // Run all again: it stops before the applied write instead of writing twice
  await page.getByTestId('nb-run').click()
  const bar = page.getByTestId('nb-rerun-bar')
  await expect(bar).toContainText('already applied', { timeout: 30_000 })
  await expect(bar).toContainText('by you')
  await page.getByTestId('nb-rerun-skip').click()
  await expect(page.getByTestId('nb-status')).toContainText('Done', { timeout: 30_000 })
  await expect(bar).toHaveCount(0)
  const after = await settled(api, nb.id)
  expect(after.runs[0].status).toBe('done')
  expect(after.runs[1].status).toBe('stopped_at_write')
  expect(after.cells.find((c) => c.name === 'c2')!.latest!.status).toBe('kept_earlier')
})

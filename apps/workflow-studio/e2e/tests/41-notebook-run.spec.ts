/**
 * Notebook page, run side (Workbench v3): Run all against the real bank DB, typed cells with result pills, the
 * workspace opening on the latest run's table when the notebook opens, a value token edit → stale → "Rerun from
 * here", "Run only if" skipping a cell, the outline rail (jump + remembered toggle) and the composer palette with
 * Ask greyed when AI is off.
 */
import type { Page } from '@playwright/test'
import { test, expect } from '../fixtures/test'
import { addCell, bankProject, CAPS_PROBE, cellEl, getNotebook, newNotebook, notBuilt, openNotebook, runAndSettle, settled } from '../fixtures/notebook'

const STUCK_SQL =
  "SELECT wire_id, amount FROM bank.wires WHERE status = 'stuck' AND created_at < now() - make_interval(days => %(days)s) ORDER BY wire_id"

async function seedWires(api: Parameters<typeof addCell>[0], bank: string) {
  const bp = await bankProject(api)
  const nb = await newNotebook(api, bp.id)
  await addCell(api, nb.id, 'data', { columns: ['max_age_days'], rows: [{ max_age_days: 1 }] }, { title: 'Settlement window' })
  await addCell(api, nb.id, 'query', { connection: bank, sql: STUCK_SQL, params: { days: '{{cells.c1.rows[0].max_age_days}}' }, limit: 100 }, { title: 'Stuck wires' })
  await addCell(api, nb.id, 'rule', { op: 'count', left: '{{cells.c2.rows}}' }, { title: 'How many' })
  await addCell(api, nb.id, 'rule', { op: 'filter', left: '{{cells.c2.rows}}', where: [{ field: 'amount', op: 'gt', value: 50 }] }, {
    title: 'Over the limit',
    guard: { left: '{{cells.c3.count}}', op: 'gt', right: 0 },
  })
  return { bp, nb }
}

const status = (page: Page) => page.getByTestId('nb-status')

test('Run all, then edit a value token: the cells go stale and Rerun from here skips the guarded cell', async ({ page, api, s, net }) => {
  const { bp, nb } = await seedWires(api, s.nb_conn_bank ?? 'bank')
  const caps = await notBuilt(api, [CAPS_PROBE])
  await net.expect(caps, async () => {
    await openNotebook(page, s, bp.slug, nb.id)
    await expect(page.getByTestId('nb-cell')).toHaveCount(4)
    await expect(status(page)).toContainText('Not run yet')
    // calm: nothing has run, so the workspace is closed (a slim rail)
    await expect(page.getByTestId('nb-workspace')).toHaveCount(0)
    await expect(page.getByTestId('nb-wsrail')).toBeVisible()
    // read-only query badge + kind parts
    await expect(cellEl(page, 'c2')).toContainText('read-only')

    await page.getByTestId('nb-run').click()
    await expect(status(page)).toContainText('Done', { timeout: 30_000 })
  }, caps.length ? 10_000 : 0)

  await expect(cellEl(page, 'c2')).toHaveAttribute('data-status', 'ok')
  await expect(cellEl(page, 'c2').getByTestId('nb-table')).toContainText('W1')
  await expect(cellEl(page, 'c2').getByTestId('nb-table')).toContainText('W2')
  await expect(cellEl(page, 'c3')).toContainText('2 of 2 rows match')
  await expect(cellEl(page, 'c4').getByTestId('nb-guard')).toContainText('true this run')
  // attribution line: run 1 by you
  await expect(cellEl(page, 'c2')).toContainText('run 1')
  const after1 = await getNotebook(api, nb.id)
  expect(after1.runs[0]).toMatchObject({ n: 1, status: 'done' })

  // edit the settlement window: 1 → 10 days
  await cellEl(page, 'c1').getByRole('button', { name: 'max_age_days: 1' }).click()
  const input = page.getByRole('textbox', { name: 'max_age_days' }).or(page.getByRole('spinbutton', { name: 'max_age_days' }))
  await input.fill('10')
  await input.press('Enter')
  await expect(cellEl(page, 'c3')).toHaveAttribute('data-status', 'stale')
  const rerun = page.getByTestId('nb-rerun-here')
  await expect(rerun).toHaveCount(1)
  // the spec really changed (and kept its number type)
  const edited = await getNotebook(api, nb.id)
  expect((edited.cells[0] as unknown as { spec: { rows: Array<{ max_age_days: unknown }> } }).spec.rows[0].max_age_days).toBe(10)

  await rerun.click()
  await expect(status(page)).toContainText(/Done/, { timeout: 30_000 })
  await expect(cellEl(page, 'c3')).toContainText('0 of 0 rows match')
  await expect(cellEl(page, 'c4')).toHaveAttribute('data-skipped', '')
  await expect(cellEl(page, 'c4').getByTestId('nb-guard')).toContainText('false · skipped')
  const after2 = await settled(api, nb.id)
  expect(after2.runs[0].n).toBe(2)
  expect(after2.cells.find((c) => c.name === 'c4')!.latest!.status).toBe('guard_false')
})

test('opening a notebook whose last run has a table opens the workspace on Results; the outline jumps and remembers', async ({ page, api, s, net }) => {
  const { bp, nb } = await seedWires(api, s.nb_conn_bank ?? 'bank')
  await runAndSettle(api, nb.id)
  const caps = await notBuilt(api, [CAPS_PROBE])
  await page.setViewportSize({ width: 1440, height: 900 })
  await net.expect(caps, async () => {
    await openNotebook(page, s, bp.slug, nb.id)
    const ws = page.getByTestId('nb-workspace')
    await expect(ws).toBeVisible()
    await expect(ws).toHaveAttribute('data-tab', 'results')
    await expect(ws.getByTestId('nb-ws-rows')).toContainText('W2')
    await expect(cellEl(page, 'c4')).toHaveAttribute('data-linked', '')

    // close it → the rail; reopen from the rail
    await page.getByTestId('nb-ws-close').click()
    await expect(ws).toHaveCount(0)
    await page.getByTestId('nb-wsrail').click()
    await expect(page.getByTestId('nb-workspace')).toBeVisible()

    // the outline: open on a wide screen, numbers + titles; click jumps; the toggle is remembered
    const outline = page.getByTestId('nb-outline')
    await expect(outline).toHaveAttribute('data-open', 'true')
    await outline.getByRole('button', { name: /Over the limit/ }).click()
    await expect(cellEl(page, 'c4')).toHaveAttribute('data-selected', '')
    await outline.getByRole('button', { name: 'Hide outline' }).click()
    await expect(outline).toHaveAttribute('data-open', 'false')
    await page.reload()
    await expect(page.getByTestId('nb-outline')).toHaveAttribute('data-open', 'false')
    await page.getByTestId('nb-outline').getByRole('button', { name: 'Show outline' }).click()
  }, caps.length ? 10_000 : 0)
})

test('the "/" palette adds a typed cell; Ask is greyed when AI is off', async ({ page, api, s, net }) => {
  const { bp, nb } = await seedWires(api, s.nb_conn_bank ?? 'bank')
  const capsR = await api.raw<{ ai: { available: boolean } }>('GET', '/notebooks/capabilities')
  const caps = await notBuilt(api, [CAPS_PROBE])
  await net.expect(caps, async () => {
    await openNotebook(page, s, bp.slug, nb.id)
    const composer = page.getByTestId('nb-composer')
    await composer.getByRole('textbox').fill('/')
    const palette = page.getByTestId('nb-palette')
    await expect(palette).toBeVisible()
    const ask = palette.getByRole('option', { name: /Ask Studio/ })
    if (capsR.status !== 200 || !capsR.body.ai.available) {
      await expect(ask).toHaveAttribute('aria-disabled', 'true')
      await expect(page.getByTestId('nb-ask')).toBeDisabled()
    }
    await palette.getByRole('option', { name: /^Note/ }).click()
    const draft = page.getByTestId('nb-draft')
    await expect(draft).toHaveAttribute('data-kind', 'note')
    await draft.getByRole('textbox').last().fill('Checked with the ops lead.')
    await page.getByTestId('nb-draft-add').click()
    await expect(page.getByTestId('nb-cell')).toHaveCount(5)
  }, caps.length ? 10_000 : 0)
  const after = await getNotebook(api, nb.id)
  expect(after.cells.map((c) => c.kind)).toEqual(['data', 'query', 'rule', 'rule', 'note'])
})

test('For each: rows run, failed rows are listed and "Retry failed rows" reruns only them', async ({ page, api, s, net }) => {
  const bp = await bankProject(api)
  const nb = await newNotebook(api, bp.id)
  await addCell(api, nb.id, 'data', { columns: ['n', 'fail'], rows: [{ n: 1, fail: 0 }, { n: 2, fail: 1 }, { n: 3, fail: 0 }] }, { title: 'Rows' })
  const loop = await addCell(api, nb.id, 'for_each', { over: '{{cells.c1.rows}}', max_rows: 50 }, { title: 'Each row' })
  await addCell(api, nb.id, 'http', { method: 'GET', url: `${s.upstreamUrl}/v1/leak?fail={{row.fail}}`, expect: { status: 200 } }, { title: 'Look it up', parent_cell_id: loop.id })
  const caps = await notBuilt(api, [CAPS_PROBE])
  await net.expect(caps, async () => {
    await openNotebook(page, s, bp.slug, nb.id)
    // nested numbering: the step inside is 2.1
    await expect(page.getByTestId('nb-outline')).toContainText('2.1')
    await page.getByTestId('nb-run').click()
    await expect(page.getByTestId('nb-status')).not.toContainText('Running', { timeout: 30_000 })
  }, caps.length ? 10_000 : 0)
  const fe = cellEl(page, 'c2').getByTestId('nb-foreach')
  await expect(fe).toContainText('2 ✓')
  await expect(fe).toContainText('1 ✗')
  await expect(fe.locator('.gl-ferow[data-status="fail"]')).toHaveCount(1)
  await expect(fe.locator('.gl-ferow')).toContainText('row 1')
  // the "A task per row" mode is labelled, not offered
  await expect(fe.getByRole('radio', { name: /A task per row · coming/ }).or(fe.getByRole('button', { name: /A task per row · coming/ }))).toBeDisabled()

  const runsBefore = (await getNotebook(api, nb.id)).runs.length
  await fe.getByRole('button', { name: 'Retry failed rows (1)' }).click()
  await expect(fe).toContainText('after retrying 1 row', { timeout: 30_000 })
  const after = await settled(api, nb.id)
  expect(after.runs.length).toBe(runsBefore + 1)
  expect((after.cells.find((c) => c.name === 'c2')!.latest!.output as { retried: number[] }).retried).toEqual([1])
})

test('Wait cell: the run waits a duration, then resolves', async ({ page, api, s, net }) => {
  const bp = await bankProject(api)
  const nb = await newNotebook(api, bp.id)
  await addCell(api, nb.id, 'data', { columns: ['n'], rows: [{ n: 1 }] }, { title: 'Something to wait after' })
  await addCell(api, nb.id, 'wait', { mode: 'duration', duration: '1s' }, { title: 'Give it a moment' })
  const caps = await notBuilt(api, [CAPS_PROBE])
  await net.expect(caps, async () => {
    await openNotebook(page, s, bp.slug, nb.id)
    await page.getByTestId('nb-run').click()
  }, caps.length ? 10_000 : 0)
  await expect(cellEl(page, 'c2').getByTestId('nb-wait-card')).toContainText('Resolved after', { timeout: 30_000 })
  const after = await settled(api, nb.id)
  expect(after.runs[0].status).toBe('done')
})

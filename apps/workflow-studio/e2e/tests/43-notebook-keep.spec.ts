/**
 * Keep as… (Workbench v3): the drawer previews what can be kept (the check strip of ✓ / ? / ✗ items from
 * POST keep/preview), Save keeps the notebook as a real workflow, the saved card links to that workflow's Page view,
 * and the notebook header shows "kept as workflow". Verified by re-reading the API.
 */
import { test, expect } from '../fixtures/test'
import { addCell, bankProject, CAPS_PROBE, getNotebook, newNotebook, notBuilt, openNotebook, runAndSettle } from '../fixtures/notebook'

test('Keep as workflow: strip, save, link to the Page view, kept chip', async ({ page, api, s, net }) => {
  const bp = await bankProject(api)
  const nb = await newNotebook(api, bp.id)
  await addCell(api, nb.id, 'query', { connection: s.nb_conn_bank ?? 'bank', sql: "SELECT wire_id, amount FROM bank.wires WHERE status = 'stuck' ORDER BY wire_id", limit: 100 }, { title: 'Stuck wires' })
  await addCell(api, nb.id, 'rule', { op: 'count', left: '{{cells.c1.rows}}' }, { title: 'How many' })
  await runAndSettle(api, nb.id)
  const probe = await api.raw('POST', `/notebooks/${nb.id}/keep/preview`, { target: 'workflow', trigger: { type: 'manual' } })
  const caps = await notBuilt(api, [CAPS_PROBE])
  const expects = [...caps, ...(probe.status === 501 ? [{ method: 'POST', url: /\/keep\/preview$/, status: 501 }] : [])]

  await net.expect(expects, async () => {
    await openNotebook(page, s, bp.slug, nb.id)
    await page.getByTestId('nb-keep-btn').getByRole('button', { name: 'Keep as…' }).click()
    const drawer = page.getByTestId('nb-keep')
    await expect(drawer).toBeVisible()
    if (probe.status === 501) {
      await expect(drawer.getByTestId('nb-keep-unavailable')).toContainText('not available on this engine yet')
      await expect(drawer.getByTestId('nb-keep-save')).toBeDisabled()
    }
  }, expects.length ? 10_000 : 0)
  test.skip(probe.status === 501, 'keep is not on this engine yet (the drawer says so; checked above)')

  const drawer = page.getByTestId('nb-keep')
  const strip = drawer.getByTestId('nb-keep-strip')
  await expect(strip).toBeVisible()
  await expect(strip.getByTestId('nb-keep-item').first()).toBeVisible()
  const name = `Kept ${nb.title}`
  await drawer.getByLabel('Name').fill(name)
  const save = drawer.getByTestId('nb-keep-save')
  await expect(save).toBeEnabled()
  await save.click()
  const saved = drawer.getByTestId('nb-keep-saved')
  await expect(saved).toContainText('Kept as workflow')
  const link = saved.getByRole('link', { name: /Open the workflow/ })
  await expect(link).toHaveAttribute('href', new RegExp(`/p/${bp.slug}/workflows/[^/?]+\\?view=page$`))

  const after = await getNotebook(api, nb.id) as unknown as { kept: Array<{ target: string; workflow_id: string; by: string | null }> }
  expect(after.kept).toHaveLength(1)
  expect(after.kept[0]).toMatchObject({ target: 'workflow', by: 'you' })
  const wf = await api.get<{ id: string; name: string }>(`/workflows/${after.kept[0].workflow_id}`)
  expect(wf.name).toBe(name)

  await page.keyboard.press('Escape')
  await expect(page.getByTestId('nb-status')).toContainText('kept as workflow')
})

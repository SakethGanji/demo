/**
 * Notebook Manager + template gallery (Workbench v3): the nav entry, the list with status / cells / no-AI ratio / last
 * run attribution, New notebook → gallery (Blank + engine templates), a template with a connection picker, and delete
 * with an inline confirm (verified by re-reading the API).
 */
import { test, expect, rand } from '../fixtures/test'
import { seedProject } from '../fixtures/seed'
import { addCell, bankProject, CAPS_PROBE, getNotebook, newNotebook, notBuilt, runAndSettle, TEMPLATES_PROBE } from '../fixtures/notebook'

test('the manager lists notebooks with status, cells, the no-AI ratio and the last run', async ({ page, api, s, net }) => {
  const p = await seedProject(api, 'nb40')
  const nb = await newNotebook(api, p.id, { title: `Ledger check ${rand(4)}` })
  await addCell(api, nb.id, 'data', { columns: ['n'], rows: [{ n: 1 }, { n: 2 }] })
  await addCell(api, nb.id, 'rule', { op: 'count', left: '{{cells.c1.rows}}' })
  await runAndSettle(api, nb.id)
  const caps = await notBuilt(api, [CAPS_PROBE])

  await net.expect(caps, async () => {
    await page.goto(`${s.studioUrl}/p/${p.slug}/notebooks`)
    const item = page.getByTestId('nb-item').filter({ hasText: nb.title })
    await expect(item).toBeVisible()
    await expect(item).toHaveAttribute('data-status', 'done')
    await expect(item).toContainText('2 cells')
    await expect(item).toContainText('2/2 no AI')
    await expect(item).toContainText('run 1')
    await expect(item).toContainText('by you')

    // open it
    await item.getByRole('link', { name: nb.title }).click()
    await expect(page).toHaveURL(new RegExp(`/p/${p.slug}/notebooks/${nb.id}$`))
    await expect(page.getByTestId('nb-cell')).toHaveCount(2)
    await expect(page.getByTestId('nb-status')).toContainText('Done')
  }, caps.length ? 10_000 : 0)
})

test('New notebook: Blank creates an empty notebook; delete asks inline and removes it', async ({ page, api, s, net }) => {
  const p = await seedProject(api, 'nb40b')
  const probes = await notBuilt(api, [TEMPLATES_PROBE, CAPS_PROBE])
  const title = `Blank ${rand(4)}`
  await net.expect(probes, async () => {
    await page.goto(`${s.studioUrl}/p/${p.slug}/notebooks`)
    await expect(page.getByTestId('nb-list-empty')).toBeVisible()
    await page.getByTestId('nb-new').click()
    const gallery = page.getByTestId('nb-gallery')
    await expect(gallery).toBeVisible()
    await expect(gallery.locator('[data-testid="nb-tpl"][data-slug=""]')).toHaveAttribute('aria-pressed', 'true')
    await gallery.getByLabel('Notebook title').fill(title)
    await page.getByTestId('nb-create').click()
    await expect(page.getByTestId('notebook-page')).toBeVisible()
    await expect(page.getByTestId('nb-empty')).toBeVisible()
    await expect(page.getByLabel('Notebook title')).toHaveValue(title)
  }, probes.length ? 10_000 : 0)

  const list = await api.get<Array<{ id: string; title: string; created_by: string | null }>>(`/projects/${p.id}/notebooks`)
  const made = list.find((n) => n.title === title)
  expect(made, 'the blank notebook exists').toBeTruthy()
  expect(made!.created_by).toBe('you')

  // back to the manager via the breadcrumb, delete with the inline confirm
  await page.getByRole('link', { name: 'Notebooks', exact: true }).first().click()
  const item = page.getByTestId('nb-item').filter({ hasText: title })
  await item.getByTestId('nb-item-delete').click()
  await item.getByTestId('nb-item-delete-confirm').click()
  await expect(item).toHaveCount(0)
  const r = await api.raw('GET', `/notebooks/${made!.id}`)
  expect(r.status).toBe(404)
})

test('a template asks for a connection and creates its typed cells', async ({ page, api, s, net }) => {
  const probe = await api.raw<Array<{ slug: string; cells: unknown[] }>>('GET', '/notebook-templates')
  test.skip(probe.status === 501, 'templates are not on this engine yet (the gallery then shows Blank only, labelled)')
  const tpl = probe.body.find((t) => t.slug === 'stuck-wires')
  test.skip(!tpl, 'the stuck-wires template is not shipped')
  const bp = await bankProject(api)
  const caps = await notBuilt(api, [CAPS_PROBE])
  const title = `Stuck wires ${rand(4)}`

  await net.expect(caps, async () => {
    await page.goto(`${s.studioUrl}/p/${bp.slug}/notebooks`)
    await page.getByTestId('nb-new').click()
    const card = page.locator('[data-testid="nb-tpl"][data-slug="stuck-wires"]')
    await expect(card).toBeVisible()
    await card.click()
    await expect(card).toHaveAttribute('aria-pressed', 'true')
    // the template changes rows, so only writable connections are offered
    const conn = page.getByTestId('nb-tpl-conn')
    await expect(conn.locator('option', { hasText: 'bank · read-only' })).toHaveCount(0)
    await conn.selectOption(s.nb_conn_bank_rw ?? 'bank_rw')
    await page.getByTestId('nb-gallery').getByLabel('Notebook title').fill(title)
    await page.getByTestId('nb-create').click()
    await expect(page.getByTestId('notebook-page')).toBeVisible()
    await expect(page.getByTestId('nb-cell')).toHaveCount(tpl!.cells.length)
    // the outline lists the cells by number (open by default on a wide screen)
    await expect(page.getByTestId('nb-outline').getByRole('button', { name: /Stuck wires older/ })).toBeVisible()
  }, caps.length ? 10_000 : 0)

  const list = await api.get<Array<{ id: string; title: string }>>(`/projects/${bp.id}/notebooks`)
  const nb = await getNotebook(api, list.find((n) => n.title === title)!.id)
  const q = nb.cells.find((c) => c.kind === 'query')!
  expect((q as unknown as { spec: { connection: string } }).spec.connection).toBe(s.nb_conn_bank_rw ?? 'bank_rw')
  await api.del(`/notebooks/${nb.id}`)
})

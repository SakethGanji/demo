/**
 * Browser cells (Workbench v3): a recorded recipe runs on the stack's own jobs-service (JOBS_DEV, no LLM) against a
 * tiny local portal; the cell shows "N of M passed", the workspace opens on the Browser tab with its frame area and
 * controls, a failing step opens the steps list naming the step, and "Take over" / agent browsing are labelled.
 * Skips, with the reason, when the jobs-service or Playwright's chromium is not available on this host.
 */
import http from 'node:http'
import type { AddressInfo } from 'node:net'
import { test, expect } from '../fixtures/test'
import { addCell, bankProject, CAPS_PROBE, cellEl, getNotebook, newNotebook, notBuilt, openNotebook } from '../fixtures/notebook'

const PORTAL = `<!doctype html><html><head><title>Accounts</title></head><body>
<h1>Accounts</h1><table><thead><tr><th>Account</th><th>Balance</th></tr></thead>
<tbody><tr><td>A1</td><td>1,000</td></tr><tr><td>A2</td><td>5,000</td></tr></tbody></table></body></html>`

let server: http.Server
let base = ''
test.beforeAll(async () => {
  server = http.createServer((req, res) => {
    res.writeHead(200, { 'content-type': 'text/html' })
    res.end(PORTAL)
  })
  await new Promise<void>((r) => server.listen(0, '127.0.0.1', () => r()))
  base = `http://127.0.0.1:${(server.address() as AddressInfo).port}`
})
test.afterAll(() => new Promise<void>((r) => server.close(() => r())))

test('a recorded browser recipe runs live; a failing step is named', async ({ page, api, s, net }) => {
  test.skip(!s.jobsUrl, `browser cells need the jobs-service + chromium: ${s.jobsSkip ?? 'not started'}`)
  test.setTimeout(240_000)
  const bp = await bankProject(api)
  const nb = await newNotebook(api, bp.id)
  await addCell(api, nb.id, 'browser', {
    base_url: base,
    steps: [
      { action: 'goto', url: '/accounts' },
      { action: 'expectVisible', target: { role: 'table' } },
    ],
  }, { title: 'Accounts page shows' })
  await addCell(api, nb.id, 'browser', {
    base_url: base,
    steps: [
      { action: 'goto', url: '/accounts' },
      { action: 'expectText', target: { role: 'heading', name: 'Accounts' }, text: 'Payments overview', timeout_ms: 1500 },
    ],
  }, { title: 'Heading says payments' })
  const caps = await notBuilt(api, [CAPS_PROBE])

  await net.expect(caps, async () => {
    await openNotebook(page, s, bp.slug, nb.id)
    await expect(cellEl(page, 'c1')).toContainText('127.0.0.1')
    await page.getByTestId('nb-run').click()
    await expect(page.getByTestId('nb-status')).toContainText(/Failed/, { timeout: 200_000 })
  }, caps.length ? 10_000 : 0)

  await expect(cellEl(page, 'c1')).toHaveAttribute('data-status', 'ok')
  await expect(cellEl(page, 'c1')).toContainText('2 of 2 passed')
  await expect(cellEl(page, 'c1')).toContainText('agent-driven browsing · coming')
  const c2 = cellEl(page, 'c2')
  await expect(c2).toHaveAttribute('data-status', 'fail')
  await expect(c2.getByTestId('nb-browser-steps')).toBeVisible()
  await expect(c2.getByTestId('nb-cell-error')).toContainText('step 2')

  // the workspace followed the browser cells
  const ws = page.getByTestId('nb-workspace')
  await expect(ws).toHaveAttribute('data-tab', 'browser')
  const tab = ws.getByTestId('nb-ws-browser')
  await expect(tab).toBeVisible()
  await expect(tab.getByRole('button', { name: /Take over/ })).toBeDisabled()

  const after = await getNotebook(api, nb.id)
  expect(after.cells[0].latest!.status).toBe('success')
  expect(after.cells[1].latest!.status).toBe('error')
})

/**
 * Design features, Runs view (INTEGRATION-TEST-PLAN §3A): inline expand, j/k, ⇧↵ run sheet, range presets,
 * status counts, stack-trace frame → /backend/<module>?line=N. Each scenario runs in both themes.
 * The run list's extra columns (e.g. a response status code) are deliberately not asserted here.
 */
import { test, expect, type EngineApi } from '../fixtures/test'
import { backdateRuns, expectTheme, THEMES, type Theme } from '../fixtures/design'
import { cursorLine } from '../fixtures/editor'
import { seedModuleSource, seedProject } from '../fixtures/seed'

// line 11 is the raise (asserted below)
const MODULE = `from studio import api


@api.get("/ok", name="ok")
def ok(req):
    return {"ok": True}


@api.get("/boom", name="boom")
def boom(req):
    raise ValueError("kaboom")
`
const RAISE_LINE = 11

async function seedRunsProject(api: EngineApi, test: string) {
  const p = await seedProject(api, test)
  await seedModuleSource(api, p, 'probe', MODULE)
  const routes = await api.get<Array<{ id: string; name: string }>>(`/projects/${p.id}/routes`)
  const id = (n: string) => routes.find((r) => r.name === n)!.id
  /** one route test in dev; returns the new run's id */
  const fire = async (name: 'ok' | 'boom') => {
    await api.post(`/projects/${p.id}/routes/${id(name)}/test`, { environment: 'dev', params: {}, query: {}, headers: {}, body: null })
    const [newest] = await api.get<Array<{ id: string }>>(`/projects/${p.id}/runs?limit=1`)
    return newest.id
  }
  return { p, fire }
}

const rowIds = (page: import('@playwright/test').Page) => page.getByTestId('run-row').evaluateAll((rs) => rs.map((r) => r.getAttribute('data-run-id')!))

for (const theme of THEMES) {
  test.describe(`runs · ${theme}`, () => {
    test.use({ colorScheme: theme as Theme })

    test('rows expand inline on click and ↵; the newest failed run opens on arrival', async ({ page, api, s }) => {
      const { p, fire } = await seedRunsProject(api, 'R1')
      await fire('ok')
      await fire('ok')
      const failedId = await fire('boom')
      await page.goto(`${s.studioUrl}/p/${p.slug}/runs?env=all`)
      await expectTheme(page, theme)
      await expect(page.getByTestId('run-row')).toHaveCount(3)

      // the page leads with what needs attention: the failed run is open with its stack trace
      const failed = page.locator(`[data-testid="run-row"][data-run-id="${failedId}"]`)
      await expect(failed).toHaveAttribute('data-expanded', 'true')
      await expect(page.getByTestId('run-detail')).toHaveCount(1)
      await expect(page.getByTestId('run-detail-stack')).toBeVisible()
      await expect(page.getByTestId('run-detail-stack-type')).toHaveText('ValueError')

      // click an OK run: it expands (and replaces the open one); click again: collapses
      const ok = page.locator('[data-testid="run-row"][data-status="success"]').first()
      await ok.click()
      await expect(ok).toHaveAttribute('data-expanded', 'true')
      await expect(failed).not.toHaveAttribute('data-expanded', 'true')
      await expect(page.getByTestId('run-detail')).toHaveCount(1)
      await expect(page.getByTestId('run-detail-meta')).toContainText('Started')
      await expect(page.getByTestId('run-detail-stack')).toHaveCount(0)
      await ok.click()
      await expect(page.getByTestId('run-detail')).toHaveCount(0)

      // ↵ toggles the selected row, Esc closes
      await ok.focus()
      await page.keyboard.press('Enter')
      await expect(ok).toHaveAttribute('data-expanded', 'true')
      await page.keyboard.press('Escape')
      await expect(page.getByTestId('run-detail')).toHaveCount(0)
    })

    test('j / k move the selection and ⇧↵ opens the run sheet', async ({ page, api, s }) => {
      const { p, fire } = await seedRunsProject(api, 'R2')
      await fire('ok')
      await fire('ok')
      await fire('ok')
      await page.goto(`${s.studioUrl}/p/${p.slug}/runs?env=all`)
      await expectTheme(page, theme)
      await expect(page.getByTestId('run-row')).toHaveCount(3)
      const ids = await rowIds(page)
      const row = (i: number) => page.locator(`[data-testid="run-row"][data-run-id="${ids[i]}"]`)

      // click the first row to select (and expand) it, then move: j → second, ↵ expands it
      await row(0).click()
      await expect(row(0)).toHaveAttribute('data-expanded', 'true')
      await page.keyboard.press('j')
      await page.keyboard.press('Enter')
      await expect(row(1)).toHaveAttribute('data-expanded', 'true')
      await expect(row(0)).not.toHaveAttribute('data-expanded', 'true')
      await page.keyboard.press('j')
      await page.keyboard.press('j') // clamps at the last row
      await page.keyboard.press('Enter')
      await expect(row(2)).toHaveAttribute('data-expanded', 'true')
      await page.keyboard.press('k')
      await page.keyboard.press('Enter')
      await expect(row(1)).toHaveAttribute('data-expanded', 'true')

      // ⇧↵ opens the sheet for the selected row
      await page.keyboard.press('Shift+Enter')
      await expect(page).toHaveURL(new RegExp(`/runs/${ids[1]}`))
      const sheet = page.getByTestId('run-sheet')
      await expect(sheet).toBeVisible()
      await expect(sheet.getByTestId('run-id')).toBeVisible()
      await expect(row(1)).toHaveAttribute('data-state', 'selected')
      // with the sheet open the list keys are inert
      await page.keyboard.press('j')
      await expect(page).toHaveURL(new RegExp(`/runs/${ids[1]}`))
      await page.keyboard.press('Escape')
      await expect(sheet).toHaveCount(0)
      await expect(page).toHaveURL(new RegExp(`/runs\\?`))
    })

    test('range presets and status counts', async ({ page, api, s }) => {
      const { p, fire } = await seedRunsProject(api, 'R3')
      // newest → oldest: ok, ok, boom (now); ok 3h ago; boom 3 days ago; ok 10 days ago.
      // The engine keeps the newest runs globally and prunes on every new run, so create everything first
      // and backdate last (old runs would be the first to be pruned).
      const old10d = await fire('ok')
      const old3d = await fire('boom')
      const old3h = await fire('ok')
      await fire('ok')
      await fire('boom')
      await fire('ok')
      backdateRuns(s, [old10d], 10 * 24)
      backdateRuns(s, [old3d], 3 * 24)
      backdateRuns(s, [old3h], 3)
      await page.goto(`${s.studioUrl}/p/${p.slug}/runs?env=all`)
      await expectTheme(page, theme)
      const rows = page.getByTestId('run-row')
      const err = page.getByTestId('runs-seg-err')

      // default range: last 24h = 3 fresh + the 3h-old one
      await expect(rows).toHaveCount(4)
      await expect(page.getByTestId('runs-subtitle')).toContainText('4 · 24h')
      await expect(page.getByTestId('runs-failed-count')).toContainText('1 failed')
      await expect(err).toContainText('1')

      const pick = async (key: string) => {
        await page.getByTestId('runs-range').click()
        await page.getByTestId(`runs-range-${key}`).click()
      }
      await pick('1h')
      await expect(rows).toHaveCount(3)
      await expect(page.getByTestId('runs-count')).toContainText('3 runs · 6 loaded')
      await pick('7d')
      await expect(rows).toHaveCount(5)
      await expect(err).toContainText('2')
      await pick('all')
      await expect(rows).toHaveCount(6)
      await expect(page.getByTestId('runs-count')).toContainText('6 runs')
      await pick('24h')
      await expect(rows).toHaveCount(4)

      // status segments filter the visible rows
      await pick('all')
      await err.click()
      await expect(rows).toHaveCount(2)
      for (const st of await rows.evaluateAll((rs) => rs.map((r) => r.getAttribute('data-status')))) expect(st).toBe('failed')
      await page.getByTestId('runs-seg-ok').click()
      await expect(rows).toHaveCount(4)
      for (const st of await rows.evaluateAll((rs) => rs.map((r) => r.getAttribute('data-status')))) expect(st).toBe('success')
      await page.getByTestId('runs-seg-all').click()
      await expect(rows).toHaveCount(6)

      // path filter narrows by route path
      await page.getByTestId('runs-path-filter').fill('boom')
      await expect(rows).toHaveCount(2)
    })

    test('a stack frame opens /backend/<module> with the cursor on that line (?line=N)', async ({ page, api, s }) => {
      const { p, fire } = await seedRunsProject(api, 'R4')
      await fire('boom')
      await page.goto(`${s.studioUrl}/p/${p.slug}/runs?env=all`)
      await expectTheme(page, theme)
      const frame = page.getByTestId('run-detail-stack-frame').first()
      await expect(frame).toBeVisible()
      await expect(frame).toContainText('probe')
      await frame.click()
      // the editor consumes `?line=N` (jump, then strips it from the URL), so assert where it lands
      await expect(page).toHaveURL(new RegExp(`/p/${p.slug}/backend/probe(\\?|$)`))
      await expect(page.getByTestId('code-editor').locator('.cm-content')).toBeVisible()
      await expect.poll(() => cursorLine(page)).toBe(RAISE_LINE)
    })
  })
}

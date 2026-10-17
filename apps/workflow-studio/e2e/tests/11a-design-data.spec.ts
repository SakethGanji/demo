/**
 * Design features, Data view (INTEGRATION-TEST-PLAN §3A): CSV import, delete + Undo, the Columns menu,
 * filter chips, relative created time. Each scenario runs in both themes.
 * CSV import outcomes are asserted through the rows API (not the request shape): the import may be
 * chunked or looped, the rows are what matters.
 */
import { test, expect } from '../fixtures/test'
import { apiRows, expectTheme, THEMES, type Theme } from '../fixtures/design'
import { seedProject, seedRows, seedTable } from '../fixtures/seed'

for (const theme of THEMES) {
  test.describe(`data · ${theme}`, () => {
    test.use({ colorScheme: theme as Theme })

    test('CSV import: dropzone → preview → mapping → progress → rows', async ({ page, api, s }) => {
      const p = await seedProject(api, 'D1')
      await seedTable(api, p, 'refunds')
      await page.goto(`${s.studioUrl}/p/${p.slug}/data/refunds`)
      await expectTheme(page, theme)
      await expect(page.getByTestId('data-empty')).toBeVisible()

      await page.getByTestId('table-menu').click()
      await page.getByTestId('table-import').click()
      const sheet = page.getByTestId('import-sheet')
      await expect(sheet).toBeVisible()
      await expect(sheet.getByTestId('import-dropzone')).toBeVisible()
      await expect(sheet.getByTestId('import-submit')).toBeDisabled()

      // 30 rows; the first header ("why") does not match a column, "extra" is skipped
      const lines = ['why,amount,extra']
      for (let i = 1; i <= 30; i++) lines.push(`"reason ${i}, quoted",${i * 2.5},junk${i}`)
      await sheet.getByTestId('import-file').setInputFiles({ name: 'refunds.csv', mimeType: 'text/csv', buffer: Buffer.from(lines.join('\n')) })

      // preview: file name, row count, first rows, "and N more"
      const preview = sheet.getByTestId('import-preview')
      await expect(preview).toContainText('refunds.csv')
      await expect(preview).toContainText('30 rows')
      await expect(sheet.getByTestId('import-preview-table').locator('tbody tr')).toHaveCount(5) // 4 rows + "and 26 more"
      await expect(sheet.getByTestId('import-preview-table')).toContainText('and 26 more')
      await expect(sheet.getByTestId('import-map-row')).toHaveCount(3)
      // mapping: amount auto-matched, why mapped by hand, extra stays skipped
      await expect(sheet.getByTestId('import-map-1')).toContainText('amount')
      await expect(sheet.getByTestId('import-map-0')).toContainText('skip')
      await sheet.getByTestId('import-map-0').click()
      await page.getByTestId('import-map-0-reason').click()
      await expect(sheet.getByTestId('import-map-0')).toContainText('reason')
      await expect(sheet.getByTestId('import-map-2')).toContainText('skip')

      // progress is shown while rows are written (recorded; the write may be too quick to catch)
      await page.evaluate(() => {
        const w = window as unknown as { __importProgress: boolean }
        w.__importProgress = false
        new MutationObserver(() => {
          if (document.querySelector('[data-testid="import-progress"]')) w.__importProgress = true
        }).observe(document.body, { subtree: true, childList: true })
      })
      await sheet.getByTestId('import-submit').click()
      await expect(page.getByTestId('data-row').first()).toBeVisible()
      await expect(sheet).toHaveCount(0, { timeout: 30_000 })
      test.info().annotations.push({ type: 'import-progress-seen', description: String(await page.evaluate(() => (window as unknown as { __importProgress: boolean }).__importProgress)) })

      // backend state: 30 rows, mapped values converted, the skipped column dropped
      const rows = await apiRows(api, p, 'refunds')
      expect(rows).toHaveLength(30)
      const byReason = new Map(rows.map((r) => [r.data.reason, r.data]))
      expect(byReason.get('reason 7, quoted')).toEqual({ reason: 'reason 7, quoted', amount: 17.5 })
      expect(rows.every((r) => !('extra' in r.data) && !('why' in r.data))).toBe(true)
      await expect(page.getByTestId('data-row-count')).toContainText('30 rows')
    })

    test('CSV import rejects a file with no matching header, then accepts a dropped replacement', async ({ page, api, s }) => {
      const p = await seedProject(api, 'D1b')
      await seedTable(api, p, 'refunds')
      await page.goto(`${s.studioUrl}/p/${p.slug}/data/refunds`)
      await page.getByTestId('data-empty-import').click()
      const sheet = page.getByTestId('import-sheet')
      await sheet.getByTestId('import-file').setInputFiles({ name: 'bad.csv', mimeType: 'text/csv', buffer: Buffer.from('foo,bar\n1,2\n') })
      await expect(sheet.getByTestId('import-error')).toContainText('no column named')
      // the sheet still lets the user map by hand or choose another file
      await sheet.getByTestId('import-file').setInputFiles({ name: 'ok.csv', mimeType: 'text/csv', buffer: Buffer.from('reason,amount\nlate,3\n') })
      await expect(sheet.getByTestId('import-error')).toHaveCount(0)
      await sheet.getByTestId('import-submit').click()
      await expect.poll(async () => (await apiRows(api, p, 'refunds')).length).toBe(1)
      await expect(sheet).toHaveCount(0)
    })

    test('delete a row and Undo restores it; bulk delete and Undo restores all', async ({ page, api, s }) => {
      const p = await seedProject(api, 'D2')
      await seedTable(api, p, 'refunds')
      await seedRows(api, p, 'refunds', [
        { reason: 'damaged', amount: 84.5 },
        { reason: 'late', amount: 12 },
        { reason: 'wrong size', amount: 30 },
      ])
      await page.goto(`${s.studioUrl}/p/${p.slug}/data/refunds`)
      await expectTheme(page, theme)
      await expect(page.getByTestId('data-row')).toHaveCount(3)

      // single row: ⋯ → Delete, toast, Undo
      const late = page.getByTestId('data-row').filter({ hasText: 'late' })
      await late.hover()
      await late.getByTestId('row-menu').click()
      await page.getByTestId('row-delete').click()
      await expect(page.getByTestId('data-row')).toHaveCount(2)
      expect((await apiRows(api, p, 'refunds')).map((r) => r.data.reason).sort()).toEqual(['damaged', 'wrong size'])
      await page.getByRole('button', { name: 'Undo' }).click()
      await expect(page.getByTestId('data-row')).toHaveCount(3)
      const back = await apiRows(api, p, 'refunds')
      expect(back.map((r) => r.data.reason).sort()).toEqual(['damaged', 'late', 'wrong size'])
      expect(back.find((r) => r.data.reason === 'late')!.data.amount).toBe(12)

      // bulk: select all → selection bar → delete → Undo
      await page.getByTestId('rows-select-all').click()
      await expect(page.getByTestId('rows-selected-count')).toContainText('3')
      await page.getByTestId('rows-delete-selected').click()
      await expect(page.getByTestId('data-empty')).toBeVisible()
      expect(await apiRows(api, p, 'refunds')).toHaveLength(0)
      await page.getByRole('button', { name: 'Undo' }).last().click()
      await expect(page.getByTestId('data-row')).toHaveCount(3)
      expect(await apiRows(api, p, 'refunds')).toHaveLength(3)
    })

    test('Columns menu hides a column and the choice survives a reload', async ({ page, api, s }) => {
      const p = await seedProject(api, 'D3')
      await seedTable(api, p, 'refunds')
      await seedRows(api, p, 'refunds', [{ reason: 'damaged', amount: 84.5 }])
      await page.goto(`${s.studioUrl}/p/${p.slug}/data/refunds`)
      await expectTheme(page, theme)
      await expect(page.getByTestId('cell-amount')).toBeVisible()
      await expect(page.getByTestId('cell-created')).toBeVisible()

      await page.getByTestId('data-columns-menu').click()
      await page.getByTestId('columns-toggle-amount').click()
      await page.getByTestId('columns-toggle-created_at').click()
      await page.keyboard.press('Escape')
      await expect(page.getByTestId('cell-amount')).toHaveCount(0)
      await expect(page.getByTestId('cell-created')).toHaveCount(0)
      await expect(page.getByTestId('cell-reason')).toBeVisible()
      await expect(page.getByTestId('data-columns-menu')).toContainText('2 hidden')

      await page.reload()
      await expect(page.getByTestId('cell-reason')).toBeVisible()
      await expect(page.getByTestId('cell-amount')).toHaveCount(0)
      await expect(page.getByTestId('cell-created')).toHaveCount(0)
      await expect(page.getByTestId('data-columns-menu')).toContainText('2 hidden')

      // un-hide one; the last visible column can never be hidden
      await page.getByTestId('data-columns-menu').click()
      await page.getByTestId('columns-toggle-amount').click()
      await page.keyboard.press('Escape')
      await expect(page.getByTestId('cell-amount')).toBeVisible()
      await page.getByTestId('data-columns-menu').click()
      await page.getByTestId('columns-toggle-id').click()
      await page.getByTestId('columns-toggle-reason').click()
      await expect(page.getByTestId('columns-toggle-amount')).toHaveAttribute('aria-disabled', 'true')
    })

    test('filter chips narrow the rows, the total and the profile', async ({ page, api, s }) => {
      const p = await seedProject(api, 'D4')
      await seedTable(api, p, 'refunds')
      await seedRows(api, p, 'refunds', [
        { reason: 'damaged', amount: 100 },
        { reason: 'late', amount: 20 },
        { reason: 'late', amount: 5 },
      ])
      await page.goto(`${s.studioUrl}/p/${p.slug}/data/refunds`)
      await expectTheme(page, theme)
      await expect(page.getByTestId('data-row')).toHaveCount(3)
      await expect(page.getByTestId('data-total-value')).toHaveText('125')
      const before = await page.getByTestId('profile-amount').innerText()

      await page.getByTestId('data-add-filter').click()
      await page.getByTestId('filter-value').fill('late')
      await page.getByTestId('filter-show-query').click()
      await expect(page.getByTestId('filter-query')).toContainText('late')
      await page.getByTestId('filter-apply').click()
      await expect(page.getByTestId('filter-chip')).toHaveCount(1)
      await expect(page.getByTestId('filter-chip')).toContainText('reason')
      await expect(page.getByTestId('filter-chip')).toContainText('late')
      await expect(page.getByTestId('data-row')).toHaveCount(2)
      await expect(page.getByTestId('data-total-value')).toHaveText('25')
      await expect(page.getByTestId('data-total-label')).toContainText('filtered')
      await expect.poll(() => page.getByTestId('profile-amount').innerText()).not.toBe(before)
      expect(new URL(page.url()).searchParams.get('filter')).toBeTruthy()

      // a filter that matches nothing → the filtered empty state, then remove the chip
      await page.getByTestId('data-add-filter').click()
      await page.getByTestId('filter-column').click()
      await page.getByTestId('filter-column-amount').click()
      await page.getByTestId('filter-value').fill('9999')
      await page.getByTestId('filter-apply').click()
      await expect(page.getByTestId('filter-chip')).toHaveCount(2)
      await expect(page.getByTestId('data-empty-filtered')).toBeVisible()
      await page.getByTestId('filter-chip-remove').last().click()
      await page.getByTestId('filter-chip-remove').first().click()
      await expect(page.getByTestId('filter-chip')).toHaveCount(0)
      await expect(page.getByTestId('data-row')).toHaveCount(3)
      await expect(page.getByTestId('data-total-value')).toHaveText('125')
    })

    test('created time is relative with the exact time on hover (data-ts)', async ({ page, api, s }) => {
      const p = await seedProject(api, 'D5')
      await seedTable(api, p, 'refunds')
      await seedRows(api, p, 'refunds', [{ reason: 'late', amount: 12 }])
      const [row] = await apiRows(api, p, 'refunds')
      await page.goto(`${s.studioUrl}/p/${p.slug}/data/refunds`)
      await expectTheme(page, theme)
      const created = page.getByTestId('cell-created')
      await expect(created).toHaveText(/^(now|\d+m ago)$/)
      const ts = await created.locator('xpath=ancestor::td').getAttribute('data-ts')
      expect(ts).toBe(row.created_at)
      expect(Number.isNaN(Date.parse(ts!))).toBe(false)
      await created.hover()
      const tip = page.locator('[data-slot="tooltip-content"]')
      await expect(tip).toBeVisible()
      // the exact stamp (a calendar date and clock time), not the relative text
      await expect(tip).toContainText(/\d{1,2}:\d{2}/)
      await expect(tip).not.toHaveText(/^(now|\d+m ago)$/)
    })
  })
}

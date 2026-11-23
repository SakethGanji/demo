/**
 * The glass design-system layer (src/shared/glass) on /__design#glass, in both themes.
 * Checks the look through computed styles (so a class silently dropped by tailwind-merge, or a
 * utility that never compiled, fails here), then drives every interactive primitive.
 */
import { test, expect } from '../fixtures/test'
import { THEMES, type Theme } from '../fixtures/design'
import type { Page } from '@playwright/test'

async function open(page: Page, studioUrl: string, theme: Theme) {
  await page.goto(`${studioUrl}/__design#glass`)
  // the theme class only (fixtures/design expectTheme pins an older body colour)
  if (theme === 'dark') await expect(page.locator('html')).toHaveClass(/dark/)
  else await expect(page.locator('html')).not.toHaveClass(/dark/)
  const root = page.getByTestId('glass-showcase')
  await root.scrollIntoViewIfNeeded()
  await expect(root).toBeVisible()
  return root
}

for (const theme of THEMES) {
  test.describe(`glass design · ${theme}`, () => {
    test.use({ colorScheme: theme as Theme })

    test('tokens and utilities render: sky, rings, mono labels, dotted tokens, green path', async ({ page, s }) => {
      const root = await open(page, s.studioUrl, theme)

      const sky = await page.getByTestId('glass-sky').evaluate((el) => getComputedStyle(el).backgroundImage)
      expect(sky).toContain('radial-gradient')
      expect(sky).toContain(theme === 'dark' ? 'rgb(12, 17, 39)' : 'rgb(243, 246, 248)')

      const panel = await page.getByTestId('glass-controls').evaluate((el) => {
        const cs = getComputedStyle(el)
        return { bw: cs.borderTopWidth, r: cs.borderTopLeftRadius, shadow: cs.boxShadow, img: cs.backgroundImage }
      })
      expect(panel.bw).toBe('0px')
      expect(panel.r).toBe('16px')
      expect(panel.shadow).toContain('inset') // the top highlight
      expect(panel.shadow.split('rgb').length).toBeGreaterThan(4) // highlight + ring + two-layer depth
      expect(panel.img).toContain('linear-gradient') // sheen

      // custom utilities survive (class present AND styled)
      const kind = root.locator('[data-testid="glass-step-query"] .gl-step-head > .kind-label').first()
      await expect(kind).toHaveClass(/\bkind-label\b/)
      expect(await kind.evaluate((el) => getComputedStyle(el).fontFamily)).toContain('JetBrains Mono')
      expect(await kind.evaluate((el) => getComputedStyle(el).fontSize)).toBe('10.5px')

      const tok = root.getByTestId('glass-step-query').locator('.vtoken').first()
      await expect(tok).toHaveClass(/\bvtoken\b/)
      expect(await tok.evaluate((el) => getComputedStyle(el).borderBottomStyle)).toBe('dotted')

      const prod = root.getByTestId('glass-chips').locator('.chip-prod')
      await expect(prod).toHaveText(/writes · PRODUCTION/)
      expect(await prod.evaluate((el) => getComputedStyle(el).backgroundColor)).not.toBe('rgba(0, 0, 0, 0)')

      const diff = await root.getByTestId('glass-diff').locator('.diff-line-add').evaluate((el) => getComputedStyle(el).backgroundColor)
      expect(diff).not.toBe('rgba(0, 0, 0, 0)')

      // tabular numbers on the page
      expect(await page.getByTestId('glass-sky').evaluate((el) => getComputedStyle(el).fontVariantNumeric)).toContain('tabular-nums')

      // the run path is drawn green; an off-path step keeps the hairline
      const rail = (id: string) => root.getByTestId(id).evaluate((el) => getComputedStyle(el, '::before').backgroundColor)
      const onPath = await rail('glass-step-query')
      const offPath = await root.getByTestId('glass-foreach').evaluate((el) => getComputedStyle(el, '::before').backgroundColor)
      expect(onPath).not.toBe(offPath)

      // stale is dimmed
      expect(Number(await root.getByTestId('glass-step-stale').locator('.gl-step-body').evaluate((el) => getComputedStyle(el).opacity))).toBeLessThan(0.6)
      await expect(root.getByTestId('glass-step-stale')).toContainText('from run 1')

      // every status mark is present
      for (const st of ['pending', 'running', 'ok', 'need', 'skip', 'stale', 'fail'])
        await expect(root.getByTestId('glass-chips').locator(`.gl-mk[data-status="${st}"]`)).toHaveCount(1)
      await expect(root.getByTestId('glass-check-strip')).toContainText('6/7')
    })

    test('page family: value token edit, branch fold, loop picker, for-each, table preview', async ({ page, s }) => {
      const root = await open(page, s.studioUrl, theme)
      const step = root.getByTestId('glass-step-query')

      await step.getByRole('button', { name: 'minutes: 10000' }).click()
      const input = page.getByRole('textbox', { name: 'minutes' })
      await expect(input).toBeFocused()
      await input.fill('25')
      await page.locator('[data-glass-token-editor]').getByRole('button', { name: 'Apply' }).click()
      await expect(step.getByRole('button', { name: 'minutes: 25' })).toBeVisible()
      await expect(input).toBeHidden()

      const branch = root.getByTestId('glass-branch')
      await expect(branch.getByText('Ask Maria to approve the release.')).toBeVisible()
      await expect(branch.getByText('POST /wires/release')).toHaveCount(0)
      const untaken = branch.getByRole('button', { name: /Release automatically/ })
      await expect(untaken).toHaveAttribute('aria-expanded', 'false')
      await untaken.click()
      await expect(untaken).toHaveAttribute('aria-expanded', 'true')
      await expect(branch.getByText('POST /wires/release')).toBeVisible()

      const loop = root.getByTestId('glass-loop')
      await expect(loop).toContainText('3 iterations')
      await loop.getByRole('button', { name: 'Iteration 3: W-1051' }).click()
      await expect(loop.getByRole('button', { name: 'Iteration 3: W-1051' })).toHaveAttribute('aria-pressed', 'true')
      await expect(loop).toContainText('Email the owner of W-1051.')
      await expect(loop.locator('.gl-sub .gl-step')).toHaveAttribute('data-status', 'fail')

      const fe = root.getByTestId('glass-foreach')
      await expect(fe).toContainText('148 ✓')
      await expect(fe).toContainText('2 ✗')
      await expect(fe.locator('.gl-ferow')).toHaveCount(2)
      await expect(fe).toContainText('409 already settled')

      const ann = step.locator('.gl-step-ann')
      await expect(ann).toHaveAttribute('aria-expanded', 'false')
      await ann.click()
      await expect(ann).toHaveAttribute('aria-expanded', 'true')

      await expect(root.getByTestId('glass-parallel').locator('.gl-lane')).toHaveCount(2)

      const table = root.getByTestId('glass-table')
      await expect(table.locator('tbody tr')).toHaveCount(3)
      await table.getByRole('button', { name: 'Show all 148 rows ▾' }).click()
      await expect(root.getByTestId('glass-last-action')).toHaveText('last: more-rows')
    })

    test('controls and surfaces: view switch, split button, palette, sheets, drawer', async ({ page, s }) => {
      const root = await open(page, s.studioUrl, theme)
      const last = root.getByTestId('glass-last-action')

      const view = root.getByRole('radiogroup', { name: 'View' })
      await view.getByRole('radio', { name: 'Page' }).click()
      await expect(view.getByRole('radio', { name: 'Page' })).toHaveAttribute('aria-checked', 'true')
      await page.keyboard.press('ArrowRight')
      await expect(view.getByRole('radio', { name: 'Editor' })).toHaveAttribute('aria-checked', 'true')
      await expect(view.getByRole('radio', { name: 'Editor' })).toBeFocused()

      const split = root.getByTestId('glass-split')
      await split.getByRole('button', { name: 'Run', exact: true }).click()
      await expect(last).toHaveText('last: run')
      await split.getByRole('button', { name: 'Run options' }).click()
      await page.getByRole('menuitem', { name: 'Run from selected step' }).click()
      await expect(last).toHaveText('last: run-from')

      await root.getByTestId('glass-open-palette').click()
      const pal = page.getByTestId('glass-palette')
      await expect(pal).toBeVisible()
      await expect(pal.getByRole('option', { name: /Ask AI/ })).toHaveAttribute('aria-disabled', 'true')
      await expect(pal.getByRole('option', { name: /Ask AI/ })).toContainText('AI is not configured')
      await page.keyboard.type('sql')
      await expect(pal.getByRole('option')).toHaveCount(1)
      await page.keyboard.press('Enter')
      await expect(pal).toBeHidden()
      await expect(last).toHaveText('last: palette-query')

      await root.getByTestId('glass-open-sheet').click()
      const sheet = page.getByTestId('glass-sheet')
      await expect(sheet).toHaveAttribute('data-side', 'right')
      await expect(sheet.getByRole('heading', { name: 'Inspector' })).toBeVisible()
      await page.keyboard.press('Escape')
      await expect(sheet).toBeHidden()

      await root.getByTestId('glass-open-wide').click()
      await expect(sheet).toHaveAttribute('data-side', 'wide')
      await expect(sheet.locator('.gl-pane')).toHaveCount(3)
      await expect(sheet.locator('.sheet-3pane')).toHaveCSS('display', 'grid')
      await sheet.getByRole('button', { name: 'Close' }).click()
      await expect(sheet).toBeHidden()

      const drawer = root.getByTestId('glass-drawer')
      await expect(root.getByTestId('glass-drawer-body')).toHaveCount(0)
      await drawer.getByRole('tab', { name: /Problems/ }).click()
      await expect(root.getByTestId('glass-drawer-body')).toHaveText('problems panel')
      await expect(drawer.getByRole('tab', { name: /Problems/ })).toHaveAttribute('aria-selected', 'true')
      await drawer.getByRole('tab', { name: /Runs/ }).click()
      await expect(root.getByTestId('glass-drawer-body')).toHaveText('runs panel')
      await drawer.getByRole('tab', { name: /Runs/ }).click()
      await expect(root.getByTestId('glass-drawer-body')).toHaveCount(0)
    })
  })
}

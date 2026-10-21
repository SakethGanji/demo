/**
 * The project dock (features/dock): the project's navigation. On editor pages it tucks itself away; `showDock`
 * brings it back through its handle (what a person does), so a step that clicks a tile never races the tuck.
 */
import { expect, type Page } from '@playwright/test'

export async function showDock(page: Page) {
  const dock = page.getByTestId('dock')
  await expect(dock).toBeVisible()
  if ((await dock.getAttribute('data-tucked')) !== null) await page.getByTestId('dock-handle').click()
  await expect(dock).not.toHaveAttribute('data-tucked', '')
}

/** Open a place that lives under the dock's More stack (secrets, apis, datasets, knowledge, workflows, settings, api-keys). */
export async function dockMore(page: Page, item: string) {
  await showDock(page)
  await page.getByTestId('dock-more').click()
  await page.getByTestId(`dock-${item}`).click()
}

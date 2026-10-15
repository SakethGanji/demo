/** Helpers for the CodeMirror editor in the Backend view. */
import { expect, type Page } from '@playwright/test'

export async function openModule(page: Page, studioUrl: string, slug: string, module: string, search = '') {
  await page.goto(`${studioUrl}/p/${slug}/backend/${module}${search}`)
  await expect(page.getByTestId('code-editor').locator('.cm-content')).toBeVisible()
}

/** Replace the whole document the way a user would (select all + type/paste). */
export async function setSource(page: Page, text: string) {
  const content = page.getByTestId('code-editor').locator('.cm-content')
  await content.click()
  await page.keyboard.press('ControlOrMeta+a')
  await page.keyboard.press('Delete')
  await page.keyboard.insertText(text)
}

export async function editorText(page: Page): Promise<string> {
  // .cm-line elements hold the visible document lines (block widgets are not lines)
  return page.getByTestId('code-editor').locator('.cm-line').evaluateAll((ls) => ls.map((l) => l.textContent ?? '').join('\n'))
}

/** 1-based line of the primary cursor, from the status bar (mirrors the CM state). */
export async function cursorLine(page: Page): Promise<number> {
  const t = (await page.getByTestId('cursor-pos').textContent()) ?? ''
  return Number(/Ln (\d+)/.exec(t)?.[1] ?? 0)
}

export async function save(page: Page) {
  await page.getByTestId('code-editor').locator('.cm-content').click()
  await page.keyboard.press('ControlOrMeta+s')
}

export async function errorLines(page: Page): Promise<number[]> {
  return page.getByTestId('code-editor').evaluate((root) => {
    const lines = [...root.querySelectorAll('.cm-line')]
    return lines.map((l, i) => (l.classList.contains('cm-error-line') ? i + 1 : 0)).filter(Boolean)
  })
}

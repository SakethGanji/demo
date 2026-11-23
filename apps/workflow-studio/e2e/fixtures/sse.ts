/**
 * Read a streamed (SSE) response the way the page saw it. Playwright cannot return the body of a streaming
 * response (`response.text()` → "No data found for resource"), so tee `window.fetch` inside the page.
 */
import { expect, type Page } from '@playwright/test'

export async function watchSse(page: Page, urlPart: string): Promise<void> {
  await page.evaluate((part) => {
    const w = window as unknown as { __sse?: Record<string, string[]>; __sseWrapped?: boolean }
    w.__sse ??= {}
    w.__sse[part] = []
    if (w.__sseWrapped) return
    w.__sseWrapped = true
    const orig = window.fetch.bind(window)
    window.fetch = async (input: RequestInfo | URL, init?: RequestInit) => {
      const res = await orig(input, init)
      const url = typeof input === 'string' ? input : input instanceof URL ? input.href : input.url
      for (const p of Object.keys(w.__sse!)) {
        if (url.includes(p)) {
          const copy = res.clone()
          void copy
            .text()
            .then((t) => w.__sse![p].push(t))
            .catch(() => w.__sse![p].push(''))
        }
      }
      return res
    }
  }, urlPart)
}

/** `[eventName, data]` pairs of the first completed stream seen since `watchSse`. */
export async function readSse(page: Page, urlPart: string): Promise<Array<[string, string]>> {
  let text = ''
  await expect
    .poll(async () => (text = (await page.evaluate((p) => (window as unknown as { __sse: Record<string, string[]> }).__sse[p][0] ?? '', urlPart))), {
      message: `waiting for the ${urlPart} stream to finish`,
    })
    .not.toBe('')
  const out: Array<[string, string]> = []
  for (const block of text.split(/\r?\n\r?\n/)) {
    const ev = /^event:\s*(.*)$/m.exec(block)?.[1]
    const data = [...block.matchAll(/^data:\s?(.*)$/gm)].map((m) => m[1]).join('\n')
    if (ev) out.push([ev, data])
  }
  return out
}

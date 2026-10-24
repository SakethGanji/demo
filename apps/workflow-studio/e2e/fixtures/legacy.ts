/** Opt-in: opens the workflow editor in its legacy form (`?legacy=1`) for specs that guard the old canvas editor.
 *  Covers page.goto and in-app navigation (history.pushState/replaceState) to /workflows/<id>. */
import type { Page } from '@playwright/test'

export async function legacyEditor(page: Page) {
  await page.addInitScript(() => {
    const isEditor = (u: URL) => /\/workflows\/[^/]+\/?$/.test(u.pathname)
    const tag = (u: URL) => {
      if (isEditor(u) && u.searchParams.get('legacy') !== '1') u.searchParams.set('legacy', '1')
      return u
    }
    const first = tag(new URL(window.location.href))
    if (first.href !== window.location.href) window.history.replaceState(window.history.state, '', first.pathname + first.search + first.hash)
    for (const m of ['pushState', 'replaceState'] as const) {
      const orig = window.history[m].bind(window.history)
      window.history[m] = (state: unknown, title: string, url?: string | URL | null) =>
        orig(state, title, url == null ? url : (() => { const u = tag(new URL(String(url), window.location.href)); return u.pathname + u.search + u.hash })())
    }
  })
}

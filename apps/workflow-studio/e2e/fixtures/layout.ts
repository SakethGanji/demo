/**
 * Per-panel clipping check (UI-DESIGN §12.3): for every `[data-testid$="-panel"]`,
 * `scrollWidth <= clientWidth + 1` unless it is marked `data-scroll-x="allowed"`; and every
 * visible button / tab inside a panel lies within the panel's box (≥ 90% of its own area).
 */
import { expect, type Page } from '@playwright/test'

export async function assertNoClipping(page: Page) {
  const problems = await page.evaluate(() => {
    const out: string[] = []
    const visible = (el: Element) => {
      const r = el.getBoundingClientRect()
      const cs = getComputedStyle(el)
      return r.width > 0 && r.height > 0 && cs.visibility !== 'hidden' && cs.display !== 'none' && Number(cs.opacity) > 0.05
    }
    const panels = [...document.querySelectorAll('[data-testid$="-panel"]')].filter(visible) as HTMLElement[]
    for (const p of panels) {
      const id = p.getAttribute('data-testid')
      if (p.getAttribute('data-scroll-x') !== 'allowed' && p.scrollWidth > p.clientWidth + 1)
        out.push(`${id}: horizontal overflow ${p.scrollWidth} > ${p.clientWidth}`)
      const pr = p.getBoundingClientRect()
      const controls = [...p.querySelectorAll('button, [role="tab"]')].filter(visible) as HTMLElement[]
      for (const c of controls) {
        // controls inside an element that scrolls on purpose are checked against that scroller
        if (c.closest('[data-scroll-x="allowed"]') && c.closest('[data-scroll-x="allowed"]') !== p) continue
        let clip = pr
        let anc = c.parentElement
        while (anc && anc !== p) {
          const cs = getComputedStyle(anc)
          if (/(auto|scroll)/.test(cs.overflowY) || /(auto|scroll)/.test(cs.overflowX)) {
            clip = anc.getBoundingClientRect()
            break
          }
          anc = anc.parentElement
        }
        const r = c.getBoundingClientRect()
        // A panel can itself be the vertical scroller. Controls wholly below
        // its fold are reachable by scrolling, just like those in nested scrollers.
        const panelScrollsY = /(auto|scroll)/.test(getComputedStyle(p).overflowY) && p.scrollHeight > p.clientHeight
        if (clip === pr && panelScrollsY && (r.bottom <= pr.top || r.top >= pr.bottom)) continue
        // A control straddling the fold of a vertically scrolling panel is reachable by scrolling too;
        // there only the horizontal fit matters (horizontal clipping is still a bug).
        if (clip === pr && panelScrollsY) {
          const hx = Math.max(0, Math.min(r.right, pr.right) - Math.max(r.left, pr.left))
          if (r.width > 0 && hx / r.width >= 0.9) continue
        }
        // controls scrolled out of a scrolling container are fine; only a partial cut is a bug
        const outside = r.bottom <= clip.top || r.top >= clip.bottom || r.right <= clip.left || r.left >= clip.right
        if (outside && clip !== pr) continue
        const ix = Math.max(0, Math.min(r.right, pr.right) - Math.max(r.left, pr.left))
        const iy = Math.max(0, Math.min(r.bottom, pr.bottom) - Math.max(r.top, pr.top))
        const area = r.width * r.height
        if (area > 0 && (ix * iy) / area < 0.9) {
          const label = (c.getAttribute('data-testid') || c.getAttribute('aria-label') || c.textContent || '').trim().slice(0, 40)
          out.push(`${id}: "${label}" is clipped (${Math.round(((ix * iy) / area) * 100)}% visible)`)
        }
      }
    }
    return out
  })
  expect(problems, 'layout clipping').toEqual([])
}

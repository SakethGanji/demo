import { describe, expect, it } from 'vitest'
import { SAFE, NODE_BOX, boundsOf, buildZoomFor, easeInOutCubic, followViewport, frameForRound, freeArea, viewportForBounds, cameraPadding, fallbackPanelInset, mayMoveCamera, rightInset, settleDelay, testHopIndex, typedSlice, withNeighbours } from './stageGeometry'

describe('rightInset', () => {
  it('is the part of the canvas the panel covers', () => {
    expect(rightInset(12, 1428, 1020)).toBe(408)
  })
  it('is 0 when there is no panel', () => {
    expect(rightInset(12, 1428, null)).toBe(0)
  })
  it('never goes negative or past the canvas', () => {
    expect(rightInset(0, 1000, 1200)).toBe(0)
    expect(rightInset(0, 1000, -50)).toBe(1000)
  })
})

describe('cameraPadding', () => {
  it('adds the margin on top of the measured inset on the right only', () => {
    expect(cameraPadding(408)).toEqual({ top: '48px', left: '48px', bottom: '48px', right: '456px' })
  })
  it('rounds fractional insets', () => {
    expect(cameraPadding(100.6).right).toBe('149px')
  })
})

describe('fallbackPanelInset', () => {
  it('caps at 384 + 24 on wide windows and shrinks on narrow ones', () => {
    expect(fallbackPanelInset(1600)).toBe(408)
    expect(fallbackPanelInset(600)).toBe(300)
  })
})

describe('withNeighbours', () => {
  it('adds direct neighbours in both directions, once', () => {
    const edges = [{ source: 'a', target: 'b' }, { source: 'b', target: 'c' }, { source: 'c', target: 'd' }]
    expect(withNeighbours(['b'], edges).sort()).toEqual(['a', 'b', 'c'])
    expect(withNeighbours(['b', 'a'], edges).sort()).toEqual(['a', 'b', 'c'])
  })
})

describe('typedSlice', () => {
  it('reveals whole characters and clamps', () => {
    expect(typedSlice('Receive', 0)).toBe('')
    expect(typedSlice('Receive', 0.5)).toBe('Rece')
    expect(typedSlice('Receive', 2)).toBe('Receive')
    expect(typedSlice('Receive', -1)).toBe('')
  })
})

describe('settleDelay', () => {
  it('sweeps left to right and is capped', () => {
    expect(settleDelay(0, 0)).toBe(0)
    expect(settleDelay(280, 0)).toBeCloseTo(0.06)
    expect(settleDelay(100000, 0)).toBe(0.5)
    expect(settleDelay(-50, 0)).toBe(0)
  })
})

describe('testHopIndex', () => {
  it('finds the hop on a path and rejects edges that are not consecutive on it', () => {
    const path = ['t', 'a', 'b']
    expect(testHopIndex(path, 't', 'a')).toBe(0)
    expect(testHopIndex(path, 'a', 'b')).toBe(1)
    expect(testHopIndex(path, 't', 'b')).toBe(-1)
    expect(testHopIndex(path, 'b', 'a')).toBe(-1)
  })
})

describe('mayMoveCamera', () => {
  it('needs an active stage that is still following', () => {
    expect(mayMoveCamera({ active: true, follow: true })).toBe(true)
    expect(mayMoveCamera({ active: true, follow: false })).toBe(false)
    expect(mayMoveCamera({ active: false, follow: true })).toBe(false)
  })
})

describe('viewportForBounds', () => {
  const size = { width: 1416, height: 820 }
  it('keeps the whole box left of the panel and never zooms past 1', () => {
    const r = { x: 0, y: 0, w: 1200, h: 300 }
    const v = viewportForBounds(r, size, { inset: 408 })
    expect(v.zoom).toBeLessThan(1)
    const right = v.x + (r.x + r.w) * v.zoom
    expect(right).toBeLessThanOrEqual(size.width - 408)
    expect(v.x + r.x * v.zoom).toBeGreaterThanOrEqual(0)
    expect(viewportForBounds({ x: 0, y: 0, w: 100, h: 100 }, size, { inset: 408 }).zoom).toBe(1)
  })
  it('centres the box in the free part of the canvas', () => {
    const v = viewportForBounds({ x: 0, y: 0, w: 400, h: 200 }, size, { inset: 408 })
    const left = v.x
    const right = size.width - 408 - (v.x + 400 * v.zoom)
    expect(left).toBeCloseTo(right, 3)
  })
})

describe('boundsOf', () => {
  it('is null for nothing and leaves headroom above for the AI marker', () => {
    expect(boundsOf([])).toBeNull()
    const b = boundsOf([{ x: 10, y: 100, w: 64, h: 64 }, { x: 300, y: 100, w: 64, h: 64 }])!
    expect(b.x).toBe(10)
    expect(b.y).toBeLessThan(100)
    expect(b.x + b.w).toBe(364)
  })
})

describe('following the build head', () => {
  const size = { width: 1440, height: 900 }
  const inset = 408
  const free = freeArea(size, inset)
  const head = (x: number, y = 0) => ({ x, y, w: NODE_BOX.w, h: NODE_BOX.h })
  const screenCentre = (h: { x: number; y: number; w: number; h: number }, vp: { x: number; y: number; zoom: number }) =>
    ({ x: (h.x + h.w / 2) * vp.zoom + vp.x, y: (h.y + h.h / 2) * vp.zoom + vp.y })

  it('the free area clears the panel, the pill and the toolbar', () => {
    expect(free.x + free.w).toBeLessThanOrEqual(size.width - inset)
    expect(free.y).toBeGreaterThan(0)
    expect(free.y + free.h).toBeLessThan(size.height)
  })

  it('followViewport is null while the head sits inside the safe box', () => {
    const vp = { x: 0, y: 0, zoom: 1 }
    const inside = { x: free.x + 0.3 * free.w, y: free.y + 0.4 * free.h, w: 130, h: 96 }
    expect(followViewport(inside, vp, size, inset)).toBeNull()
  })

  it('followViewport puts the head centre at anchorX of the free width, at the same zoom', () => {
    const vp = { x: 40, y: 200, zoom: 0.94 }
    const far = head(1500, 100)
    const next = followViewport(far, vp, size, inset)!
    expect(next).not.toBeNull()
    expect(next.zoom).toBe(0.94)
    expect(screenCentre(far, next).x).toBeCloseTo(free.x + SAFE.anchorX * free.w, 6)
    // after the pan the head is inside the safe box, so the next beat does not move again
    expect(followViewport(far, next, size, inset)).toBeNull()
  })

  it('a head off to the left or below is followed too, with y clamped into the band', () => {
    const vp = { x: 0, y: 0, zoom: 1 }
    const below = head(free.x + 0.3 * free.w, 2000)
    const next = followViewport(below, vp, size, inset)!
    const cy = screenCentre(below, next).y
    expect(cy).toBeLessThanOrEqual(free.y + SAFE.y1 * free.h + 1e-6)
    expect(cy).toBeGreaterThanOrEqual(free.y + SAFE.y0 * free.h - 1e-6)
    const left = followViewport(head(-900, 100), vp, size, inset)
    expect(left).not.toBeNull()
  })

  it('frameForRound frames everything when it fits at the build zoom, otherwise builds closer around the first head', () => {
    const small = { x: 0, y: 0, w: 400, h: 200 }
    const fit = frameForRound(small, head(0), size, inset)
    expect(fit.zoom).toBe(1)                                   // fitZoom 1 >= buildZoom 1: the plain fit
    const wide = { x: 0, y: -40, w: 1270, h: 240 }
    const fitZoom = viewportForBounds(wide, size, { inset, maxZoom: 1 }).zoom
    const vp = frameForRound(wide, head(0), size, inset)
    expect(fitZoom).toBeLessThan(1)
    expect(vp.zoom).toBeCloseTo(buildZoomFor(fitZoom), 10)
    expect(vp.zoom).toBeGreaterThan(fitZoom)
    expect(screenCentre(head(0), vp).x).toBeCloseTo(free.x + SAFE.anchorX * free.w, 6)
  })

  it('the zoom stays within [.8, 1] while building and never above 1', () => {
    for (const z of [0.1, 0.4, 0.64, 0.8, 0.95, 1, 3]) {
      expect(buildZoomFor(z)).toBeLessThanOrEqual(1)
      expect(buildZoomFor(z)).toBeGreaterThanOrEqual(0.8)
    }
    const huge = frameForRound({ x: 0, y: 0, w: 9000, h: 3000 }, head(0), size, inset)
    expect(huge.zoom).toBeLessThanOrEqual(1)
  })

  it('easeInOutCubic runs 0 -> 1 and is symmetric', () => {
    expect(easeInOutCubic(0)).toBe(0)
    expect(easeInOutCubic(1)).toBe(1)
    expect(easeInOutCubic(0.5)).toBeCloseTo(0.5, 10)
    expect(easeInOutCubic(0.25) + easeInOutCubic(0.75)).toBeCloseTo(1, 10)
  })
})

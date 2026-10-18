import { describe, expect, it } from 'vitest'
import { boundsOfBoxes, egoViewport, freeRect, SHEET_GAP, SHEET_W } from './camera'

describe('camera', () => {
  it('frees the tile minus the docked sheet', () => {
    expect(freeRect({ width: 1200, height: 700 }, false)).toEqual({ x: 0, y: 0, w: 1200, h: 700 })
    expect(freeRect({ width: 1200, height: 700 }, true).w).toBe(1200 - SHEET_W - SHEET_GAP * 2)
    expect(freeRect({ width: 100, height: 100 }, true).w).toBeGreaterThan(0)
  })

  it('centres the bounds in the free area, so nothing lands under the sheet', () => {
    const size = { width: 1200, height: 700 }
    const free = freeRect(size, true)
    const bounds = { x: -400, y: -200, w: 800, h: 400 }
    const vp = egoViewport(bounds, free)
    const left = bounds.x * vp.zoom + vp.x
    const right = (bounds.x + bounds.w) * vp.zoom + vp.x
    expect(left).toBeGreaterThanOrEqual(free.x)
    expect(right).toBeLessThanOrEqual(free.x + free.w)
    expect((left + right) / 2).toBeCloseTo(free.x + free.w / 2, 5)
    const top = bounds.y * vp.zoom + vp.y
    const bottom = (bounds.y + bounds.h) * vp.zoom + vp.y
    expect((top + bottom) / 2).toBeCloseTo(free.h / 2, 5)
  })

  it('never zooms past the cap or under the floor', () => {
    const free = { x: 0, y: 0, w: 1000, h: 800 }
    expect(egoViewport({ x: 0, y: 0, w: 10, h: 10 }, free, { maxZoom: 1.1 }).zoom).toBe(1.1)
    expect(egoViewport({ x: 0, y: 0, w: 100000, h: 100000 }, free, { minZoom: 0.2 }).zoom).toBe(0.2)
  })

  it('unions boxes', () => {
    expect(boundsOfBoxes([{ x: 0, y: 0, w: 10, h: 10 }, { x: 20, y: -5, w: 10, h: 10 }])).toEqual({ x: 0, y: -5, w: 30, h: 15 })
    expect(boundsOfBoxes([])).toBeNull()
  })
})

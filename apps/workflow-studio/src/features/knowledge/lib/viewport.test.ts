import { describe, expect, it } from 'vitest'
import { MAX_K, MIN_K, centerOn, easeOut, fitTransform, lerpTransform, toWorld, wheelFactor, zoomAround } from './viewport'

describe('viewport', () => {
  it('fits bounds in the middle of the view, never past maxK', () => {
    // (496 - 96) / 200 = 2 fits, but the zoom stops at maxK
    const t = fitTransform({ minX: -100, minY: -50, maxX: 100, maxY: 50 }, { width: 496, height: 400 }, 48, 3)
    expect(t.k).toBeCloseTo(2)
    const capped = fitTransform({ minX: -100, minY: -50, maxX: 100, maxY: 50 }, { width: 496, height: 400 }, 48, 1.6)
    expect(capped.k).toBe(1.6)
    // centre of the bounds lands at the centre of the view
    expect(capped.x).toBe(248)
    expect(capped.y).toBe(200)
  })

  it('fits nothing to the centre at 1x', () => {
    expect(fitTransform(null, { width: 200, height: 100 })).toEqual({ x: 100, y: 50, k: 1 })
  })

  it('zooms around a fixed screen point and clamps', () => {
    const t = { x: 10, y: 20, k: 1 }
    const p = { x: 100, y: 100 }
    const z = zoomAround(t, 2, p)
    expect(toWorld(z, p)).toEqual(toWorld(t, p))
    expect(zoomAround(t, 1000, p).k).toBe(MAX_K)
    expect(zoomAround(t, 0.0001, p).k).toBe(MIN_K)
  })

  it('centres a world point', () => {
    const t = centerOn({ x: 0, y: 0, k: 2 }, { x: 10, y: 10 }, { width: 100, height: 100 })
    expect(t).toEqual({ x: 30, y: 30, k: 2 })
  })

  it('turns wheel deltas into zoom factors', () => {
    expect(wheelFactor(-100)).toBeGreaterThan(1)
    expect(wheelFactor(100)).toBeLessThan(1)
    expect(wheelFactor(-3, 1)).toBeCloseTo(wheelFactor(-48, 0))
    expect(wheelFactor(-10, 0, true)).toBeGreaterThan(wheelFactor(-10, 0, false))
    expect(wheelFactor(-1e6)).toBe(wheelFactor(-120))
  })

  it('blends transforms and eases', () => {
    const m = lerpTransform({ x: 0, y: 0, k: 1 }, { x: 10, y: 20, k: 4 }, 0.5)
    expect(m).toEqual({ x: 5, y: 10, k: 2 })
    expect(easeOut(0)).toBe(0)
    expect(easeOut(1)).toBe(1)
    expect(easeOut(2)).toBe(1)
  })
})

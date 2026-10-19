import { describe, expect, it } from 'vitest'
import { cssVarName, easeInOutCubic, fitDistance, isTentative, particlePlan, pickLabels, relColor, shapeOf } from './graph3d'

describe('shapeOf', () => {
  it('gives each broad kind its own geometry', () => {
    expect(shapeOf('concept')).toBe('sphere')
    expect(shapeOf('capability', 'node_type')).toBe('cube')
    expect(shapeOf('capability', 'template')).toBe('octahedron')
    expect(shapeOf('capability', 'workflow')).toBe('gem')
    expect(shapeOf('pattern')).toBe('hexprism')
    expect(shapeOf('project')).toBe('torus')
    expect(shapeOf('app')).toBe('capsule')
    expect(shapeOf('role')).toBe('tetra')
    expect(shapeOf('mystery')).toBe('dot')
  })
})

describe('relations', () => {
  it('colours known relations from the viz palette and falls back to grey', () => {
    expect(relColor('implements')).toBe('var(--viz-2)')
    expect(relColor('whatever')).toBe('var(--viz-8)')
  })

  it('marks learned links and links touching a proposed node as tentative', () => {
    const byId = new Map([['a', { status: 'active' }], ['b', { status: 'proposed' }], ['c', { status: 'active' }]])
    expect(isTentative({ source: 'learned', src_id: 'a', dst_id: 'c' }, byId)).toBe(true)
    expect(isTentative({ source: 'seed', src_id: 'a', dst_id: 'b' }, byId)).toBe(true)
    expect(isTentative({ source: 'seed', src_id: 'a', dst_id: 'c' }, byId)).toBe(false)
  })
})

describe('particlePlan', () => {
  it('puts one particle per link at rest, capped', () => {
    const plan = particlePlan([3, 1, 2], null, { cap: 2 })
    expect(plan.map((p) => p.edgeId)).toEqual([1, 2])
    expect(plan.every((p) => !p.lit && p.phase >= 0 && p.phase < 1)).toBe(true)
  })

  it('only lit links carry particles while something is lit, several each', () => {
    const plan = particlePlan([1, 2, 3], new Set([2]), { perLit: 3 })
    expect(plan).toHaveLength(3)
    expect(new Set(plan.map((p) => p.edgeId))).toEqual(new Set([2]))
    expect(new Set(plan.map((p) => p.phase)).size).toBe(3)
  })

  it('keeps a link phase stable when the set changes', () => {
    const a = particlePlan([5, 9], null).find((p) => p.edgeId === 9)!.phase
    const b = particlePlan([9, 12, 40], null).find((p) => p.edgeId === 9)!.phase
    expect(a).toBe(b)
  })
})

describe('pickLabels', () => {
  const visible = new Set(['a', 'b', 'c', 'd', 'e', 'f'])
  const degree = new Map([['a', 9], ['b', 5], ['c', 1], ['d', 0], ['e', 0], ['f', 3]])
  it('always labels hovered and selected, plus a few hubs at rest', () => {
    const out = pickLabels({ hovered: 'd', selected: 'e', lit: null, visible, degree, distance: new Map(), near: 0, hubs: 2 })
    expect(out).toEqual(new Set(['d', 'e', 'a', 'b']))
  })

  it('labels lit nodes and near ones, never hidden ones', () => {
    const distance = new Map([['c', 10], ['f', 20], ['zz', 1]])
    const out = pickLabels({ lit: new Set(['c', 'f', 'zz']), visible, degree, distance, near: 15 })
    expect(out).toEqual(new Set(['f', 'c']))
  })
})

describe('camera maths', () => {
  it('stands further back for bigger spheres and narrower views', () => {
    expect(fitDistance(200, 50, 1.6)).toBeGreaterThan(fitDistance(100, 50, 1.6))
    expect(fitDistance(100, 50, 0.5)).toBeGreaterThan(fitDistance(100, 50, 1.6))
  })

  it('eases from 0 to 1 and clamps', () => {
    expect(easeInOutCubic(0)).toBe(0)
    expect(easeInOutCubic(1)).toBe(1)
    expect(easeInOutCubic(2)).toBe(1)
    expect(easeInOutCubic(0.5)).toBeCloseTo(0.5)
  })

  it('reads a CSS variable name out of var()', () => {
    expect(cssVarName('var(--viz-3)')).toBe('--viz-3')
    expect(cssVarName('red')).toBeNull()
  })
})

import { describe, expect, it } from 'vitest'
import type { KindGroup } from './kinds'
import { mulberry32, type LayoutEdge, type LayoutNode } from './layout'
import { boundingSphere, clusterCentres3d, lerpPositions3, runLayout3d, seedPositions3d, type Point3 } from './layout3d'

function synth(n: number, seed = 7, clusters = 0): { nodes: LayoutNode[]; edges: LayoutEdge[] } {
  const rand = mulberry32(seed)
  const groups: KindGroup[] = ['concept', 'capability', 'capability', 'capability', 'pattern', 'role', 'app']
  const nodes: LayoutNode[] = Array.from({ length: n }, (_, i) => ({
    id: `n${String(i).padStart(4, '0')}`,
    group: groups[i % groups.length],
    r: 5 + (i % 4),
    ...(clusters ? { cluster: groups[i % groups.length] === 'concept' ? null : `p${i % clusters}` } : {}),
  }))
  const concepts = nodes.filter((x) => x.group === 'concept')
  const edges: LayoutEdge[] = []
  for (const nd of nodes) {
    if (nd.group === 'concept') continue
    const k = 1 + Math.floor(rand() * 2)
    for (let j = 0; j < k; j++) edges.push({ src: nd.id, dst: concepts[Math.floor(rand() * concepts.length)].id })
  }
  return { nodes, edges }
}

const dist = (a: Point3, b: Point3) => Math.hypot(a.x - b.x, a.y - b.y, a.z - b.z)
const mean = (ps: Point3[]) => ({
  x: ps.reduce((s, p) => s + p.x, 0) / ps.length,
  y: ps.reduce((s, p) => s + p.y, 0) / ps.length,
  z: ps.reduce((s, p) => s + p.z, 0) / ps.length,
})

describe('runLayout3d', () => {
  it('is deterministic and independent of input order', () => {
    const { nodes, edges } = synth(60)
    const a = runLayout3d(nodes, edges)
    const b = runLayout3d([...nodes].reverse(), [...edges].reverse())
    expect([...a]).toEqual([...b].sort((x, y) => (x[0] < y[0] ? -1 : 1)))
    expect(a.size).toBe(60)
  })

  it('never produces NaN with coincident starts, self-loops and dangling edges', () => {
    const nodes: LayoutNode[] = [
      { id: 'a', group: 'concept', r: 6 },
      { id: 'b', group: 'concept', r: 6 },
      { id: 'c', group: 'capability', r: 5 },
    ]
    const o = { x: 0, y: 0, z: 0 }
    const pos = runLayout3d(nodes, [{ src: 'a', dst: 'a' }, { src: 'a', dst: 'zz' }, { src: 'a', dst: 'b' }], {
      initial: new Map([['a', o], ['b', o], ['c', o]]),
    })
    for (const p of pos.values()) for (const v of [p.x, p.y, p.z]) expect(Number.isFinite(v)).toBe(true)
    expect(dist(pos.get('a')!, pos.get('b')!)).toBeGreaterThan(6)
  })

  it('handles empty and single-node graphs', () => {
    expect(runLayout3d([], []).size).toBe(0)
    expect(runLayout3d([{ id: 'x', group: 'role', r: 5 }], []).get('x')).toEqual({ x: 0, y: 0, z: 0 })
  })

  it('has real depth, and keeps the kind regions in the x/y plane', () => {
    const { nodes, edges } = synth(140)
    const pos = runLayout3d(nodes, edges)
    const zs = [...pos.values()].map((p) => p.z)
    expect(Math.max(...zs) - Math.min(...zs)).toBeGreaterThan(60)
    const c = (g: KindGroup) => mean(nodes.filter((n) => n.group === g).map((n) => pos.get(n.id)!))
    expect(c('capability').x).toBeGreaterThan(c('concept').x + 30)
    expect(c('pattern').x).toBeLessThan(c('concept').x - 30)
  })

  it('gathers each cluster into its own constellation', () => {
    const { nodes, edges } = synth(120, 3, 3)
    const pos = runLayout3d(nodes, edges)
    const centre = (k: string) => mean(nodes.filter((n) => n.cluster === k).map((n) => pos.get(n.id)!))
    const spreadOf = (k: string) => {
      const c = centre(k)
      const ds = nodes.filter((n) => n.cluster === k).map((n) => dist(pos.get(n.id)!, c))
      return ds.reduce((s, v) => s + v, 0) / ds.length
    }
    for (const [a, b] of [['p0', 'p1'], ['p1', 'p2'], ['p0', 'p2']]) {
      expect(dist(centre(a), centre(b))).toBeGreaterThan(spreadOf(a) + spreadOf(b))
    }
  })

  it('warm-starts: an added node barely moves the rest', () => {
    const { nodes, edges } = synth(60)
    const before = runLayout3d(nodes, edges)
    const after = runLayout3d([...nodes, { id: 'zz', group: 'capability', r: 5 }], [...edges, { src: 'zz', dst: nodes[0].id }], { initial: before })
    let drift = 0
    for (const n of nodes) drift += dist(after.get(n.id)!, before.get(n.id)!)
    expect(drift / nodes.length).toBeLessThan(boundingSphere(before)!.r * 0.15)
  })
})

describe('clusterCentres3d', () => {
  it('puts a lone cluster without shared nodes at the origin', () => {
    expect(clusterCentres3d([{ cluster: 'a' }, { cluster: 'a' }]).get('a')).toEqual({ x: 0, y: 0, z: 0 })
  })

  it('spreads several clusters evenly at the same distance', () => {
    const nodes = [null, null, 'a', 'b', 'c', 'd'].map((cluster) => ({ cluster }))
    const c = clusterCentres3d(nodes)
    const rs = [...c.values()].map((p) => Math.hypot(p.x, p.y, p.z))
    for (const r of rs) expect(Math.abs(r - rs[0])).toBeLessThan(1)
  })
})

describe('helpers', () => {
  it('seeds known positions as given', () => {
    const p = { x: 1, y: 2, z: 3 }
    expect(seedPositions3d([{ id: 'a', group: 'concept', r: 5 }], [], new Map([['a', p]])).get('a')).toEqual(p)
  })

  it('bounds a sphere and lerps', () => {
    const pos = new Map([['a', { x: -10, y: 0, z: 0 }], ['b', { x: 10, y: 0, z: 0 }]])
    expect(boundingSphere(pos)).toEqual({ c: { x: 0, y: 0, z: 0 }, r: 10 })
    expect(boundingSphere(pos, ['zz'])).toBeNull()
    expect(lerpPositions3(new Map([['a', { x: 0, y: 0, z: 0 }]]), pos, 0.5).get('a')).toEqual({ x: -5, y: 0, z: 0 })
  })
})

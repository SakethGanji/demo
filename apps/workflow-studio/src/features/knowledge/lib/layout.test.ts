import { describe, expect, it } from 'vitest'
import type { KindGroup } from './kinds'
import {
  boundsOf, clusterAnchors, hashString, lerpPositions, mulberry32, runLayout, seedPositions, type LayoutEdge, type LayoutNode,
} from './layout'

/** A graph shaped like the engine's: concepts in the middle, capabilities/patterns implementing them. */
function synth(n: number, seed = 7): { nodes: LayoutNode[]; edges: LayoutEdge[] } {
  const rand = mulberry32(seed)
  const groups: KindGroup[] = ['concept', 'capability', 'capability', 'capability', 'pattern', 'role', 'app']
  const nodes: LayoutNode[] = Array.from({ length: n }, (_, i) => ({ id: `n${String(i).padStart(4, '0')}`, group: groups[i % groups.length], r: 5 + (i % 4) }))
  const concepts = nodes.filter((x) => x.group === 'concept')
  const edges: LayoutEdge[] = []
  for (const nd of nodes) {
    if (nd.group === 'concept') continue
    const k = 1 + Math.floor(rand() * 2)
    for (let j = 0; j < k; j++) edges.push({ src: nd.id, dst: concepts[Math.floor(rand() * concepts.length)].id })
  }
  for (let j = 0; j < concepts.length - 1; j += 2) edges.push({ src: concepts[j].id, dst: concepts[j + 1].id })
  return { nodes, edges }
}

const mean = (ps: { x: number; y: number }[]) => ({
  x: ps.reduce((s, p) => s + p.x, 0) / ps.length,
  y: ps.reduce((s, p) => s + p.y, 0) / ps.length,
})

describe('hash + prng', () => {
  it('are stable', () => {
    expect(hashString('abc')).toBe(hashString('abc'))
    expect(hashString('abc')).not.toBe(hashString('abd'))
    const a = mulberry32(1)
    const b = mulberry32(1)
    const xs = Array.from({ length: 5 }, () => a())
    expect(xs).toEqual(Array.from({ length: 5 }, () => b()))
    for (const x of xs) expect(x >= 0 && x < 1).toBe(true)
  })
})

describe('runLayout', () => {
  it('is deterministic and independent of input order', () => {
    const { nodes, edges } = synth(60)
    const a = runLayout(nodes, edges)
    const b = runLayout([...nodes].reverse(), [...edges].reverse())
    expect([...a]).toEqual([...b].sort((x, y) => (x[0] < y[0] ? -1 : 1)))
    expect(a.size).toBe(60)
  })

  it('never produces NaN, even with coincident starts, self-loops and dangling edges', () => {
    const nodes: LayoutNode[] = [
      { id: 'a', group: 'concept', r: 6 },
      { id: 'b', group: 'concept', r: 6 },
      { id: 'c', group: 'capability', r: 5 },
    ]
    const initial = new Map([['a', { x: 0, y: 0 }], ['b', { x: 0, y: 0 }], ['c', { x: 0, y: 0 }]])
    const pos = runLayout(nodes, [{ src: 'a', dst: 'a' }, { src: 'a', dst: 'zz' }, { src: 'a', dst: 'b' }, { src: 'b', dst: 'a' }], { initial })
    for (const p of pos.values()) {
      expect(Number.isFinite(p.x)).toBe(true)
      expect(Number.isFinite(p.y)).toBe(true)
    }
    expect(Math.hypot(pos.get('a')!.x - pos.get('b')!.x, pos.get('a')!.y - pos.get('b')!.y)).toBeGreaterThan(6)
  })

  it('handles empty and single-node graphs', () => {
    expect(runLayout([], []).size).toBe(0)
    const one = runLayout([{ id: 'x', group: 'role', r: 5 }], [])
    expect(one.get('x')).toEqual({ x: 0, y: 0 })
  })

  it('clusters by kind: capabilities right of concepts, patterns left, roles below, apps above', () => {
    const { nodes, edges } = synth(140)
    const pos = runLayout(nodes, edges)
    const c = (g: KindGroup) => mean(nodes.filter((n) => n.group === g).map((n) => pos.get(n.id)!))
    const concept = c('concept')
    expect(c('capability').x).toBeGreaterThan(concept.x + 40)
    expect(c('pattern').x).toBeLessThan(concept.x - 40)
    expect(c('role').y).toBeGreaterThan(concept.y + 40)
    expect(c('app').y).toBeLessThan(concept.y - 40)
  })

  it('keeps nodes from overlapping much', () => {
    const { nodes, edges } = synth(120)
    const pos = runLayout(nodes, edges)
    let overlaps = 0
    for (let i = 0; i < nodes.length; i++) {
      for (let j = i + 1; j < nodes.length; j++) {
        const a = pos.get(nodes[i].id)!
        const b = pos.get(nodes[j].id)!
        if (Math.hypot(a.x - b.x, a.y - b.y) < (nodes[i].r + nodes[j].r) * 0.8) overlaps++
      }
    }
    expect(overlaps).toBeLessThanOrEqual(2)
  })

  it('pulls linked nodes closer than unlinked ones', () => {
    const { nodes, edges } = synth(80)
    const pos = runLayout(nodes, edges)
    const d = (a: string, b: string) => Math.hypot(pos.get(a)!.x - pos.get(b)!.x, pos.get(a)!.y - pos.get(b)!.y)
    const linked = edges.map((e) => d(e.src, e.dst))
    let all = 0
    let cnt = 0
    for (let i = 0; i < nodes.length; i += 3) for (let j = i + 1; j < nodes.length; j += 5) { all += d(nodes[i].id, nodes[j].id); cnt++ }
    expect(linked.reduce((s, v) => s + v, 0) / linked.length).toBeLessThan(all / cnt)
  })

  it('warm-starts: an added node barely moves the existing map', () => {
    const { nodes, edges } = synth(60)
    const before = runLayout(nodes, edges)
    const extra: LayoutNode = { id: 'zz-new', group: 'capability', r: 5 }
    const after = runLayout([...nodes, extra], [...edges, { src: 'zz-new', dst: nodes[0].id }], { initial: before })
    let drift = 0
    for (const n of nodes) drift += Math.hypot(after.get(n.id)!.x - before.get(n.id)!.x, after.get(n.id)!.y - before.get(n.id)!.y)
    const span = boundsOf(before)!
    expect(drift / nodes.length).toBeLessThan((span.maxX - span.minX) * 0.1)
    expect(after.has('zz-new')).toBe(true)
  })

  it('lays out 300 nodes fast', () => {
    const { nodes, edges } = synth(300)
    runLayout(nodes, edges, { ticks: 20 }) // warm the JIT
    const t0 = performance.now()
    const pos = runLayout(nodes, edges)
    const ms = performance.now() - t0
    console.info(`[knowledge] runLayout 300 nodes / ${edges.length} edges / 300 ticks: ${ms.toFixed(1)} ms`)
    expect(pos.size).toBe(300)
    expect(ms).toBeLessThan(1500)
  })
})

describe('seedPositions', () => {
  it('keeps initial positions and seeds new nodes beside their neighbours', () => {
    const nodes: LayoutNode[] = [{ id: 'a', group: 'concept', r: 5 }, { id: 'b', group: 'capability', r: 5 }]
    const pos = seedPositions(nodes, [{ src: 'a', dst: 'b' }], new Map([['a', { x: 500, y: 500 }]]))
    expect(pos.get('a')).toEqual({ x: 500, y: 500 })
    expect(Math.hypot(pos.get('b')!.x - 500, pos.get('b')!.y - 500)).toBeLessThan(40)
  })

  it('anchors a lone kind at the origin', () => {
    const a = clusterAnchors(['capability'], 10)
    expect(a.capability).toEqual({ x: 0, y: 0 })
    const b = clusterAnchors(['capability', 'concept'], 10)
    expect(b.capability.x).toBeGreaterThan(0)
  })
})

describe('bounds + lerp', () => {
  it('grows bounds by radius and skips unknown ids', () => {
    const pos = new Map([['a', { x: 0, y: 0 }], ['b', { x: 10, y: 20 }]])
    expect(boundsOf(pos, undefined, () => 2)).toEqual({ minX: -2, minY: -2, maxX: 12, maxY: 22 })
    expect(boundsOf(pos, ['zz'])).toBeNull()
  })

  it('interpolates and puts new nodes at their target', () => {
    const from = new Map([['a', { x: 0, y: 0 }]])
    const to = new Map([['a', { x: 10, y: 10 }], ['b', { x: 3, y: 4 }]])
    const mid = lerpPositions(from, to, 0.5)
    expect(mid.get('a')).toEqual({ x: 5, y: 5 })
    expect(mid.get('b')).toEqual({ x: 3, y: 4 })
  })
})

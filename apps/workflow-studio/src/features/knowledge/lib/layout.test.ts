import { describe, expect, it } from 'vitest'
import type { KgKind } from '../kit/kinds'
import { adjacency, egoLayout, overlaps, overviewLayout, type Box } from './layout'
import { sampleGraph } from './fixtures.test-util'

const ALL = new Set<KgKind>(['concept', 'role', 'capability', 'pattern', 'app'])
const opts = { kinds: ALL, nodeTypes: true }

function anyOverlap(boxes: Box[]) {
  for (let i = 0; i < boxes.length; i++) for (let j = i + 1; j < boxes.length; j++) if (overlaps(boxes[i], boxes[j])) return [i, j]
  return null
}

describe('overviewLayout', () => {
  const g = sampleGraph()

  it('is deterministic: the same input gives the same output, whatever the input order', () => {
    const a = overviewLayout(g.nodes, g.edges, opts)
    const b = overviewLayout([...g.nodes].reverse(), [...g.edges].reverse(), opts)
    expect([...a.positions].sort()).toEqual([...b.positions].sort())
    expect([...a.edges.values()].map((e) => e.d).sort()).toEqual([...b.edges.values()].map((e) => e.d).sort())
  })

  it('places every visible node exactly once and no two boxes overlap', () => {
    const l = overviewLayout(g.nodes, g.edges, opts)
    expect(l.positions.size).toBe(g.nodes.length)
    expect(anyOverlap([...l.positions.values()])).toBeNull()
  })

  it('keeps roles on the left, concepts in the middle, capabilities on the right, the top band above', () => {
    const l = overviewLayout(g.nodes, g.edges, opts)
    const x = (id: string) => (l.positions.get(id) as Box).x
    expect(Math.max(...g.roles.map((n) => x(n.id)))).toBeLessThan(Math.min(...g.concepts.map((n) => x(n.id))))
    expect(Math.max(...g.concepts.map((n) => x(n.id)))).toBeLessThan(Math.min(...g.caps.map((n) => x(n.id))))
    for (const p of [...g.patterns, ...g.apps]) expect((l.positions.get(p.id) as Box).y + 56).toBeLessThanOrEqual(0)
  })

  it('hides node types unless asked, and hides filtered kinds', () => {
    const l = overviewLayout(g.nodes, g.edges, { kinds: new Set<KgKind>(['concept', 'capability']), nodeTypes: false })
    const types = g.caps.filter((c) => c.subkind === 'node_type')
    expect(types.length).toBeGreaterThan(0)
    for (const t of types) expect(l.positions.has(t.id)).toBe(false)
    for (const r of g.roles) expect(l.positions.has(r.id)).toBe(false)
    for (const e of l.edges.values()) { expect(l.positions.has(e.src)).toBe(true); expect(l.positions.has(e.dst)).toBe(true) }
  })

  it('draws only edges that touch a concept, plus escalation between roles; composes between capabilities is left out', () => {
    const l = overviewLayout(g.nodes, g.edges, opts)
    const rels = [...l.edges.values()]
    expect(rels.some((e) => e.rel === 'escalates_to')).toBe(true)
    expect(rels.some((e) => e.rel === 'composes')).toBe(false)
    for (const e of rels) expect(e.d).toMatch(/^M[-\d.]+,[-\d.]+ C/)
  })

  it('orders capabilities of a block by the barycentre of their concepts (stable and monotonic)', () => {
    const l = overviewLayout(g.nodes, g.edges, opts)
    const subs = g.caps.filter((c) => c.subkind === 'workflow')
    const yOf = (n: { id: string }) => (l.positions.get(n.id) as Box).y
    const conceptY = (c: (typeof g.caps)[number]) => {
      const e = g.edges.find((x) => x.src_id === c.id && x.rel === 'implements')
      return (l.positions.get(e?.dst_id as string) as Box).y
    }
    const sorted = [...subs].sort((a, b) => yOf(a) - yOf(b))
    for (let i = 1; i < sorted.length; i++) expect(conceptY(sorted[i - 1])).toBeLessThanOrEqual(conceptY(sorted[i]))
  })

  it('wraps a long block into columns of at most 14 rows', () => {
    const many = Array.from({ length: 30 }, (_, i) => ({ ...g.caps[0], id: `big-${i}`, key: `cap:big${i}`, label: `Big ${String(i).padStart(2, '0')}`, subkind: 'subworkflow' }))
    const l = overviewLayout([...g.nodes, ...many], g.edges, opts)
    const boxes = many.map((m) => l.positions.get(m.id) as Box)
    expect(new Set(boxes.map((b) => b.x)).size).toBe(3)
    expect(Math.max(...boxes.map((b) => b.y))).toBe(13 * 40)
    expect(anyOverlap([...l.positions.values()])).toBeNull()
  })
})

describe('egoLayout', () => {
  const g = sampleGraph()

  it('puts the focus at the origin and its depth-1 neighbours around it', () => {
    const l = egoLayout(g.concepts[1].id, g.nodes, g.edges, 1)
    expect(l).not.toBeNull()
    const focus = l!.positions.get(g.concepts[1].id) as Box
    expect(focus.x + focus.w / 2).toBe(0)
    expect(focus.y + focus.h / 2).toBe(0)
    const expected = new Set<string>()
    for (const e of g.edges) {
      if (e.src_id === g.concepts[1].id) expected.add(e.dst_id)
      if (e.dst_id === g.concepts[1].id) expected.add(e.src_id)
    }
    expect(new Set([...l!.hop.keys()].filter((id) => l!.hop.get(id) === 1))).toEqual(expected)
    expect(new Set(l!.positions.keys())).toEqual(new Set([g.concepts[1].id, ...expected]))
  })

  it('has no overlaps at depth 1 and depth 2, and depth 2 only adds nodes one hop further', () => {
    const one = egoLayout(g.concepts[1].id, g.nodes, g.edges, 1)!
    const two = egoLayout(g.concepts[1].id, g.nodes, g.edges, 2)!
    expect(anyOverlap([...one.positions.values()])).toBeNull()
    expect(anyOverlap([...two.positions.values()])).toBeNull()
    expect(two.positions.size).toBeGreaterThan(one.positions.size)
    for (const [id, h] of two.hop) if (h === 2) expect(one.positions.has(id)).toBe(false)
    const adj = adjacency([...two.edges.values()].map((e) => ({ id: e.id, src: e.src, dst: e.dst })))
    for (const [id, h] of two.hop) if (h === 2) expect([...(adj.get(id)?.nodes ?? [])].some((n) => two.hop.get(n) === 1)).toBe(true)
  })

  it('keeps the sector order of the overview: roles left, concepts up, capabilities right', () => {
    const l = egoLayout(g.concepts[1].id, g.nodes, g.edges, 1)!
    const c = (id: string) => { const b = l.positions.get(id) as Box; return { x: b.x + b.w / 2, y: b.y + b.h / 2 } }
    expect(c(g.roles[2].id).x).toBeLessThan(0)
    expect(c(g.concepts[2].id).y).toBeLessThan(0)
    expect(c(g.concepts[3].id).y).toBeLessThan(0)
    const cap = g.caps.find((x) => g.edges.some((e) => e.src_id === x.id && e.dst_id === g.concepts[1].id))!
    expect(c(cap.id).x).toBeGreaterThan(0)
  })

  it('is deterministic and returns a clockwise order starting at 12 o clock', () => {
    const a = egoLayout(g.concepts[0].id, g.nodes, g.edges, 2)!
    const b = egoLayout(g.concepts[0].id, [...g.nodes].reverse(), [...g.edges].reverse(), 2)!
    expect([...a.positions].sort()).toEqual([...b.positions].sort())
    const angles = a.clockwise.map((id) => (((a.angle.get(id) as number) - 270) + 360) % 360)
    expect(angles).toEqual([...angles].sort((x, y) => x - y))
  })

  it('returns null for an unknown focus, and draws one straight line per neighbour that stops at the box edges', () => {
    expect(egoLayout('nope', g.nodes, g.edges)).toBeNull()
    const l = egoLayout(g.concepts[1].id, g.nodes, g.edges, 1)!
    for (const e of l.edges.values()) expect(e.d).toMatch(/^M[-\d.]+,[-\d.]+ L[-\d.]+,[-\d.]+$/)
  })

  it('stays free of overlaps with 60 neighbours', () => {
    const hub = g.concepts[0]
    const extra = Array.from({ length: 60 }, (_, i) => ({ ...g.caps[0], id: `x-${i}`, key: `cap:x${i}`, label: `Extra capability ${i}` }))
    const edges = [...g.edges, ...extra.map((c, i) => ({ id: 1000 + i, src_id: c.id, rel: 'implements', dst_id: hub.id, props: {}, source: 'seed' }))]
    const l = egoLayout(hub.id, [...g.nodes, ...extra], edges, 1)!
    expect(anyOverlap([...l.positions.values()])).toBeNull()
  })
})

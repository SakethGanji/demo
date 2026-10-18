import { describe, expect, it } from 'vitest'
import { kg } from '../kit/beats'
import type { KgKind } from '../kit/kinds'
import { planAssembly } from './assembly'
import { overviewLayout } from './layout'
import { sampleGraph } from './fixtures.test-util'

const ALL = new Set<KgKind>(['concept', 'role', 'capability', 'pattern', 'app'])

describe('planAssembly', () => {
  const g = sampleGraph()
  const layout = overviewLayout(g.nodes, g.edges, { kinds: ALL, nodeTypes: true })

  it('plans every node and edge once, concepts first and the top band last', () => {
    const p = planAssembly(layout, g.nodes, false)
    expect(p.nodes.size).toBe(layout.positions.size)
    expect(p.edges.size).toBe(layout.edges.size)
    const maxConcept = Math.max(...g.concepts.map((c) => p.nodes.get(c.id)!.delay))
    for (const t of [...g.patterns, ...g.apps]) expect(p.nodes.get(t.id)!.delay).toBeGreaterThanOrEqual(kg.assemble.topAt)
    expect(maxConcept).toBeLessThan(kg.assemble.topAt)
    for (const t of [...g.patterns, ...g.apps]) expect(p.nodes.get(t.id)!.scale).toBe(1)
  })

  it('draws edges only after the nodes are in, and runs at most 12 comets', () => {
    const p = planAssembly(layout, g.nodes, false)
    for (const e of p.edges.values()) expect(e.delay).toBeGreaterThanOrEqual(kg.assemble.edgesAt)
    expect([...p.edges.values()].filter((e) => e.comet).length).toBeLessThanOrEqual(kg.assemble.maxComets)
    expect(p.total).toBeLessThanOrEqual(kg.assemble.maxTotal)
  })

  it('fits the capability stagger into its window', () => {
    const p = planAssembly(layout, g.nodes, false)
    const delays = g.caps.map((c) => p.nodes.get(c.id)!.delay)
    expect(Math.max(...delays) - Math.min(...delays)).toBeLessThanOrEqual(kg.assemble.capWindow)
  })

  it('goes compact when truncated: four batches, fades only, no comets', () => {
    const p = planAssembly(layout, g.nodes, true)
    expect(p.compact).toBe(true)
    expect(new Set([...p.nodes.values()].map((n) => n.delay)).size).toBeLessThanOrEqual(4)
    expect([...p.nodes.values()].every((n) => n.mode === 'fade')).toBe(true)
    expect([...p.edges.values()].some((e) => e.comet)).toBe(false)
  })
})

import { describe, expect, it } from 'vitest'
import type { Understanding } from '@/features/ai-builder/types'
import {
  askHits, buildModel, diffGraph, edgePath, emphasis, filterGraph, hubLabelBudget, kindCounts, labelIds, nodeRadius,
  relCounts, rippleDelays, searchNodes,
} from './graph'
import type { KgEdge, KgNode } from './types'

const node = (id: string, kind: string, label: string, extra: Partial<KgNode> = {}): KgNode => ({
  id, key: `${kind}:${label.toLowerCase()}`, kind, subkind: null, scope: 'global', visibility: 'org', label,
  summary: '', aliases: [], props: {}, source: 'seed', status: 'active', validated_count: 0, last_validated_at: null,
  updated_at: '2026-10-01T00:00:00Z', ...extra,
})
const edge = (id: number, src: string, rel: string, dst: string): KgEdge => ({ id, src_id: src, rel, dst_id: dst, props: {}, source: 'seed' })

const nodes = [
  node('c1', 'concept', 'Report', { key: 'concept:report' }),
  node('c2', 'concept', 'Schedule', { key: 'concept:schedule', aliases: ['cron job'] }),
  node('k1', 'capability', 'Profile', { key: 'node:Profile', subkind: 'node_type' }),
  node('k2', 'capability', 'Cron', { key: 'node:Cron', subkind: 'node_type' }),
  node('t1', 'capability', 'Dataset report email', { key: 'tpl:dataset-report-email', subkind: 'template', summary: 'emails a profile' }),
  node('p1', 'pattern', 'Weekly digest', { key: 'pattern:abc' }),
  node('r1', 'role', 'Approver', { key: 'role:approver' }),
]
const edges = [
  edge(1, 'k1', 'implements', 'c1'),
  edge(2, 't1', 'implements', 'c1'),
  edge(3, 'k2', 'implements', 'c2'),
  edge(4, 't1', 'composes', 'k1'),
  edge(5, 'p1', 'derived_from', 't1'),
  edge(6, 'p1', 'requires', 'r1'),
  edge(7, 'k1', 'implements', 'gone'), // dangling: dropped
]
const model = buildModel({ nodes, edges })
const all = filterGraph(model, { hiddenKinds: new Set(), hiddenRels: new Set() })

describe('buildModel', () => {
  it('drops dangling edges and counts degree by distinct neighbours', () => {
    expect(model.edges.map((e) => e.id)).toEqual([1, 2, 3, 4, 5, 6])
    expect(model.degree.get('t1')).toBe(3)
    expect(model.degree.get('r1')).toBe(1)
    expect(model.radius.get('t1')).toBeGreaterThan(model.radius.get('r1')!)
  })

  it('caps the radius', () => {
    expect(nodeRadius(10_000)).toBe(19)
    expect(nodeRadius(0, 'concept')).toBeGreaterThan(nodeRadius(0, 'capability'))
  })

  it('counts kinds and relations', () => {
    expect(kindCounts(nodes)).toEqual({ concept: 2, capability: 3, pattern: 1, role: 1, app: 0, other: 0 })
    expect(kindCounts([{ kind: 'mystery' }]).other).toBe(1)
    expect(relCounts(model.edges)[0]).toEqual(['implements', 3])
  })
})

describe('filterGraph', () => {
  it('hides kinds and the edges that touch them, and hidden relations', () => {
    const v = filterGraph(model, { hiddenKinds: new Set(['pattern']), hiddenRels: new Set(['composes']) })
    expect(v.nodes.has('p1')).toBe(false)
    expect([...v.edges].sort()).toEqual([1, 2, 3])
  })
})

describe('searchNodes', () => {
  it('ranks exact, prefix, word, substring, then key/alias', () => {
    expect(searchNodes(model, 'report')[0]).toBe('c1')
    expect(searchNodes(model, 'Report')).toContain('t1') // word in "Dataset report email"
    expect(searchNodes(model, 'cron')).toEqual(['k2', 'c2']) // label beats alias
    expect(searchNodes(model, '')).toEqual([])
    expect(searchNodes(model, 'report', new Set(['t1']))).toEqual(['t1'])
  })

  it('only searches summaries for longer queries', () => {
    expect(searchNodes(model, 'emails')).toEqual(['t1'])
    expect(searchNodes(model, 'ema')).toEqual(['t1']) // still the label word "email"
  })
})

describe('askHits', () => {
  const u = {
    concepts: [{ key: 'concept:report', label: 'Report', score: 1, source: 'lexical', evidence: '', confirmed: false }],
    retrieval: { subworkflows: [], templates: [{ key: 'tpl:dataset-report-email', label: '', why: '' }], patterns: [], apis: [], node_types: ['Cron'], roles: [] },
  } as unknown as Pick<Understanding, 'concepts' | 'retrieval'>

  it('lights concepts, their implementers and bare node_type names', () => {
    const h = askHits(model, u)
    expect(h.concepts).toEqual(['c1'])
    expect(new Set(h.implementers)).toEqual(new Set(['k1', 't1', 'k2']))
    expect(h.edges.has(1) && h.edges.has(2)).toBe(true)
    expect(h.edges.has(3)).toBe(false) // Cron implements Schedule, which was not matched
  })

  it('is empty without an understanding', () => {
    expect(askHits(model, null).nodes.size).toBe(0)
  })
})

describe('emphasis + labels', () => {
  it('focus lights the neighbourhood and wins over ask and search', () => {
    const em = emphasis(model, all, { focus: ['c1'], ask: askHits(model, null), search: ['r1'] })
    expect(em.mode).toBe('focus')
    expect(em.lit).toEqual(new Set(['c1', 'k1', 't1']))
    expect(em.flow).toEqual(new Set([1, 2]))
  })

  it('falls back to search, then to nothing', () => {
    expect(emphasis(model, all, { focus: [null], search: ['r1'] }).mode).toBe('search')
    const none = emphasis(model, all, { focus: [] })
    expect(none.lit).toBeNull()
  })

  it('never lights a hidden node', () => {
    const v = filterGraph(model, { hiddenKinds: new Set(['concept']), hiddenRels: new Set() })
    expect(emphasis(model, v, { focus: ['c1'] }).mode).toBe('none')
  })

  it('labels only the top hubs at rest, more as you zoom', () => {
    expect(hubLabelBudget(0.3, 300)).toBe(5)
    expect(hubLabelBudget(3, 300)).toBe(60)
    expect(hubLabelBudget(3, 4)).toBe(4)
    const at = labelIds(model, all, emphasis(model, all, { focus: [] }), { k: 0.3, hovered: 'r1' })
    expect(at.has('r1')).toBe(true)
    expect(at.size).toBe(6) // 5 hubs + the hovered leaf
  })

  it('labels the lit set when something is emphasised', () => {
    const em = emphasis(model, all, { focus: ['c2'] })
    expect(labelIds(model, all, em, { k: 1 })).toEqual(new Set(['c2', 'k2']))
  })
})

describe('diffGraph + ripple', () => {
  it('finds added and changed nodes', () => {
    const next = [...nodes.slice(1), node('new', 'concept', 'New'), { ...nodes[0], status: 'stale' }]
    expect(diffGraph(nodes, next)).toEqual({ added: ['new'], changed: ['c1'] })
    expect(diffGraph(null, next)).toEqual({ added: [], changed: [] })
  })

  it('delays grow with distance from the centroid', () => {
    const pos = new Map([['a', { x: 0, y: 0 }], ['b', { x: 100, y: 0 }], ['c', { x: -100, y: 0 }]])
    const d = rippleDelays(['a', 'b', 'missing'], pos, 1000)
    expect(d.get('a')).toBe(0)
    expect(d.get('b')).toBe(1000)
    expect(d.has('missing')).toBe(false)
  })
})

describe('edgePath', () => {
  it('draws a trimmed quadratic and survives zero length', () => {
    expect(edgePath({ x: 0, y: 0 }, { x: 100, y: 0 }, 10, 10)).toMatch(/^M[\d.]+,[-\d.]+Q50,12 [\d.]+,[-\d.]+$/)
    expect(edgePath({ x: 1, y: 1 }, { x: 1, y: 1 })).toBe('M1,1')
  })
})

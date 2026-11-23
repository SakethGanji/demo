import { describe, expect, it } from 'vitest'
import { buildModel, filterGraph } from './graph'
import { clusterCentres, runLayout, type LayoutNode } from './layout'
import { conceptProjects, PROJECT_PALETTE, projectChips, projectColor, SHARED, SHARED_COLOR, workflowIdFor } from './projects'
import type { KgNode } from './types'

const node = (id: string, kind: string, project_id: string | null, key = `${kind}:${id}`): KgNode => ({
  id, key, kind, subkind: null, scope: 'project', visibility: 'org', project_id, label: id, summary: '', aliases: [],
  props: {}, source: 'seed', status: 'active', validated_count: 0, last_validated_at: null, updated_at: null,
})

describe('projectColor', () => {
  it('is stable, from the palette, and neutral for shared knowledge', () => {
    expect(projectColor('proj_a')).toBe(projectColor('proj_a'))
    expect(PROJECT_PALETTE).toContain(projectColor('proj_b'))
    expect(projectColor(null)).toBe(SHARED_COLOR)
  })
})

describe('projectChips', () => {
  it('puts Shared first, then projects by size, labelling unknown ids by id', () => {
    const nodes = [node('c', 'concept', null), node('a1', 'pattern', 'pa'), node('b1', 'pattern', 'pb'), node('b2', 'app', 'pb'), node('x', 'app', 'px')]
    const chips = projectChips([{ id: 'pa', slug: 'a', name: 'Alpha' }, { id: 'pb', slug: 'b', name: 'Beta' }], nodes)
    expect(chips.map((c) => [c.key, c.label, c.count])).toEqual([[SHARED, 'Shared', 1], ['pb', 'Beta', 2], ['pa', 'Alpha', 1], ['px', 'px', 1]])
  })
})

describe('project filter, concept projects and workflow links', () => {
  const nodes = [node('c', 'concept', null), node('p1', 'pattern', 'pa'), node('p2', 'pattern', 'pb')]
  const edges = [
    { id: 1, src_id: 'p1', rel: 'implements', dst_id: 'c', props: {}, source: 'seed' },
    { id: 2, src_id: 'p2', rel: 'implements', dst_id: 'c', props: {}, source: 'seed' },
  ]
  const model = buildModel({ nodes, edges })
  it('hides a project and its edges', () => {
    const v = filterGraph(model, { hiddenKinds: new Set(), hiddenRels: new Set(), hiddenProjects: new Set(['pa']) })
    expect([...v.nodes].sort()).toEqual(['c', 'p2'])
    expect([...v.edges]).toEqual([2])
  })
  it('lists the projects building on a concept', () => {
    const refs = new Map([['pa', { id: 'pa', slug: 'a', name: 'Zed' }], ['pb', { id: 'pb', slug: 'b', name: 'Abe' }]])
    expect(conceptProjects(model, 'c', refs)).toEqual(['pb', 'pa'])
  })
  it('finds the workflow of a workflow node or a validated pattern', () => {
    expect(workflowIdFor({ key: 'wf:w1', kind: 'capability' })).toBe('w1')
    const nb = { validated_by: [{ direction: 'out' as const, node: { id: 'n', key: 'wf:w9', label: 'W', kind: 'capability', subkind: 'workflow' }, props: {}, source: 'learned' }] }
    expect(workflowIdFor({ key: 'pattern:abc', kind: 'pattern' }, nb)).toBe('w9')
    expect(workflowIdFor({ key: 'concept:x', kind: 'concept' }, nb)).toBeNull()
  })
})

describe('clustered layout', () => {
  const base: LayoutNode[] = [
    ...Array.from({ length: 6 }, (_, i) => ({ id: `s${i}`, group: 'concept' as const, r: 6 })),
    ...Array.from({ length: 5 }, (_, i) => ({ id: `a${i}`, group: 'pattern' as const, r: 5, cluster: 'pa' })),
    ...Array.from({ length: 5 }, (_, i) => ({ id: `b${i}`, group: 'pattern' as const, r: 5, cluster: 'pb' })),
  ]
  it('is identical to the plain layout without cluster keys', () => {
    const plain = base.map(({ id, group, r }) => ({ id, group, r }))
    expect([...runLayout(plain, [])]).toEqual([...runLayout(plain.map((n) => ({ ...n, cluster: null })), [])])
  })
  it('is deterministic and gathers each cluster round its own anchor', () => {
    const a = runLayout(base, [])
    expect([...a]).toEqual([...runLayout([...base].reverse(), [])])
    const centres = clusterCentres(base)
    const d = (id: string, k: string) => Math.hypot(a.get(id)!.x - centres.get(k)!.x, a.get(id)!.y - centres.get(k)!.y)
    for (let i = 0; i < 5; i++) {
      expect(d(`a${i}`, 'pa')).toBeLessThan(d(`a${i}`, 'pb'))
      expect(d(`b${i}`, 'pb')).toBeLessThan(d(`b${i}`, 'pa'))
    }
  })
})

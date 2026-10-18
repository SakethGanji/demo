import { describe, expect, it, vi } from 'vitest'
import type { TemplateSummary, WorkflowPreview } from './types'

const t = (slug: string, category: string, tags: string[] = []): TemplateSummary => ({
  slug, version: '1.0.0', name: slug, summary: `${slug} summary`, category, tags,
  workflows: [], tables: [], apis: [], requires: { templates: [], secrets: [], variables: [], egress_hosts: [] },
})

vi.stubGlobal('window', { location: { hostname: 'localhost' } })
const { categoriesOf, filterTemplates, groupOf, installProblem, previewToFlow, sortNeeds, testsTotals } = await import('./lib')
const { ApiError } = await import('@/shared/api/http')

describe('templates lib', () => {
  it('filters by category and search', () => {
    const l = [t('a', 'x', ['refund']), t('b', 'y')]
    expect(categoriesOf(l)).toEqual(['x', 'y'])
    expect(filterTemplates(l, 'y', '').map((x) => x.slug)).toEqual(['b'])
    expect(filterTemplates(l, 'all', 'REFUND').map((x) => x.slug)).toEqual(['a'])
  })
  it('lays out nodes without positions by depth', () => {
    const p: WorkflowPreview = {
      name: 'w', role: 'entry', interface: null, tests: [],
      nodes: [{ name: 'A', type: 'Webhook' }, { name: 'B', type: 'If' }],
      connections: [{ source_node: 'A', target_node: 'B' }],
    }
    const f = previewToFlow(p)
    expect(f.nodes[1].position.x).toBeGreaterThan(f.nodes[0].position.x)
    expect(f.edges).toHaveLength(1)
    expect(groupOf('Webhook')).toBe('trigger')
  })
  it('sorts secrets first and totals tests', () => {
    expect(sortNeeds([{ kind: 'variable', name: 'a', environments_missing: [] }, { kind: 'secret', name: 'z', environments_missing: [] }])[0].kind).toBe('secret')
    expect(testsTotals({ m: { passed: 2, failed: 1, total: 3 }, n: { passed: 1, failed: 0, total: 1 } })).toEqual({ passed: 3, failed: 1, total: 4 })
  })
  it('explains install errors', () => {
    const e = new ApiError(409, 'TEMPLATE_NAME_CLASH', 'x', { detail: { code: 'TEMPLATE_NAME_CLASH', message: 'x', clashes: [{ kind: 'workflow', name: 'Foo' }] } })
    expect(installProblem(e).lines[0]).toContain('Foo')
  })
})

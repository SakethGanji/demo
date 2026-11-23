import { describe, expect, it } from 'vitest'
import type { WorkflowDefinition } from '@/features/projects/hooks/useWorkflows'
import { diffWorkflows } from './workflowDiff'

const node = (name: string, parameters: Record<string, unknown> = {}, x = 0, extra: Record<string, unknown> = {}) => ({
  name,
  type: 'Set',
  parameters,
  position: { x, y: 0 },
  ...extra,
})
const wf = (...nodes: ReturnType<typeof node>[]): WorkflowDefinition => ({ nodes, connections: [] })

describe('diffWorkflows', () => {
  it('reports added and removed nodes by name', () => {
    const d = diffWorkflows(wf(node('a'), node('b')), wf(node('b'), node('c')))
    expect(d).toEqual({ added: ['c'], removed: ['a'], changed: [] })
  })
  it('reports parameter changes, ignoring key order', () => {
    const base = wf(node('a', { x: 1, y: { p: 1, q: 2 } }), node('b', { k: 1 }))
    const target = wf(node('a', { y: { q: 2, p: 1 }, x: 1 }), node('b', { k: 2 }))
    expect(diffWorkflows(base, target).changed).toEqual(['b'])
  })
  it('ignores position-only changes', () => {
    expect(diffWorkflows(wf(node('a', {}, 0)), wf(node('a', {}, 500)))).toEqual({ added: [], removed: [], changed: [] })
  })
  it('detects type, disabled, on_error and label changes', () => {
    const base = wf(node('t'), node('d'), node('e'), node('l'))
    const target = wf(
      { ...node('t'), type: 'If' },
      node('d', {}, 0, { disabled: true }),
      node('e', {}, 0, { on_error: 'continue' }),
      node('l', {}, 0, { label: 'Nice' }),
    )
    expect(diffWorkflows(base, target).changed).toEqual(['t', 'd', 'e', 'l'])
  })
  it('treats legacy continue_on_fail as on_error=continue', () => {
    const d = diffWorkflows(wf(node('a', {}, 0, { continue_on_fail: true })), wf(node('a', {}, 0, { on_error: 'continue' })))
    expect(d.changed).toEqual([])
  })
})

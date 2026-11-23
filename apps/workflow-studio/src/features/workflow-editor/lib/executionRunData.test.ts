import { describe, expect, it } from 'vitest'
import type { Node } from '@xyflow/react'
import { buildRunData } from './executionRunData'
import { useWorkflowStore } from '../stores/workflowStore'

const node = (name: string): Node => ({
  id: name,
  type: 'workflowNode',
  position: { x: 0, y: 0 },
  data: { name, type: 'Set', label: name } as never,
})
const nodes = [node('Webhook'), node('Set'), node('If')]
const edges = [
  { source: 'Webhook', target: 'Set' },
  { source: 'Set', target: 'If' },
]
const detail = {
  id: 'ex1',
  workflow_id: 'wf',
  workflow_name: 'W',
  status: 'failed',
  mode: 'manual',
  start_time: '2026-10-17T10:00:00Z',
  end_time: '2026-10-17T10:00:02Z',
  errors: [{ node_name: 'If', error: 'boom' }],
  node_data: { Webhook: [{ json: { a: 1 } }], Set: [{ json: { a: 1, b: 2 } }, { json: { a: 3 } }] },
  node_metrics: { Set: { executionTimeMs: 5 } },
}

describe('buildRunData', () => {
  it('maps node_data to output, upstream output to input, errors to error status', () => {
    const d = buildRunData(detail, nodes, edges)
    expect(d.Webhook.status).toBe('success')
    expect(d.Webhook.input).toBeNull()
    expect(d.Set.input).toEqual({ items: [{ a: 1 }] })
    expect(d.Set.output).toEqual({ items: [{ a: 1, b: 2 }, { a: 3 }] })
    expect(d.Set.metrics?.executionTimeMs).toBe(5)
    expect(d.If.status).toBe('error')
    expect(d.If.output).toEqual({ items: [], error: 'boom' })
    expect(d.Webhook.startTime).toBe(Date.parse('2026-10-17T10:00:00Z'))
  })

  it('skips nodes that did not run', () => {
    const d = buildRunData({ ...detail, errors: [] }, nodes, edges)
    expect(d.If).toBeUndefined()
  })
})

describe('workflowStore viewing a run', () => {
  it('fills the run-data store and exits cleanly', () => {
    useWorkflowStore.setState({ nodes, edges: edges as never, executionData: {}, viewingExecution: null })
    useWorkflowStore.getState().loadExecutionData(detail)
    const s = useWorkflowStore.getState()
    expect(s.viewingExecution).toEqual({ id: 'ex1', startTime: detail.start_time, status: 'failed' })
    expect(s.executionData.Set.output?.items).toHaveLength(2)
    s.exitViewingExecution()
    expect(useWorkflowStore.getState().viewingExecution).toBeNull()
    expect(useWorkflowStore.getState().executionData).toEqual({})
  })
})

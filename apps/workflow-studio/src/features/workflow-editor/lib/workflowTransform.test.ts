import { describe, it, expect } from 'vitest';
import type { Node, Edge } from '@xyflow/react';
import { toBackendWorkflow, fromBackendWorkflow } from './workflowTransform';
import { createWorkflowNodeData } from './createNodeData';
import type { WorkflowNodeData } from '../types/workflow';
import type { ApiWorkflowDetail } from '@/shared/lib/backendTypes';

function node(name: string, overrides: Partial<WorkflowNodeData> = {}): Node<WorkflowNodeData> {
  return {
    id: name,
    type: 'workflowNode',
    position: { x: 0, y: 0 },
    data: { ...createWorkflowNodeData({ type: 'Set' }, { name }), ...overrides },
  };
}

function api(nodes: Array<Record<string, unknown>>, connections: Array<Record<string, unknown>> = []): ApiWorkflowDetail {
  return { id: 'w1', name: 'W', active: false, definition: { nodes, connections } } as unknown as ApiWorkflowDetail;
}

describe('workflowTransform: disabled and onError', () => {
  it('writes disabled and on_error, never continue_on_fail', () => {
    const wf = toBackendWorkflow([node('A', { disabled: true, onError: 'continue' }), node('B')], [], 'W');
    expect(wf.nodes[0]).toMatchObject({ disabled: true, on_error: 'continue' });
    expect(wf.nodes[1]).toMatchObject({ disabled: false, on_error: 'stop' });
    expect('continue_on_fail' in wf.nodes[0]).toBe(false);
  });

  it('round-trips disabled and onError through the backend format', () => {
    const wf = toBackendWorkflow([node('A', { disabled: true, onError: 'error_output' })], [], 'W');
    const back = fromBackendWorkflow(api(wf.nodes as never, []));
    expect(back.nodes[0].data.disabled).toBe(true);
    expect(back.nodes[0].data.onError).toBe('error_output');
  });

  it('reads legacy continue_on_fail as on_error "continue"', () => {
    const back = fromBackendWorkflow(
      api([
        { name: 'A', type: 'Set', parameters: {}, continue_on_fail: true },
        { name: 'B', type: 'Set', parameters: {}, continue_on_fail: false },
        { name: 'C', type: 'Set', parameters: {}, continue_on_fail: true, on_error: 'error_output' },
        { name: 'D', type: 'Set', parameters: {} },
      ]),
    );
    expect(back.nodes.map((n) => n.data.onError)).toEqual(['continue', 'stop', 'error_output', 'stop']);
    expect(back.nodes.map((n) => n.data.disabled)).toEqual([false, false, false, false]);
  });

  it('maps the error handle to source_output "error" and back', () => {
    const edges: Edge[] = [
      { id: 'e1', source: 'A', target: 'B', sourceHandle: 'main', targetHandle: 'main' },
      { id: 'e2', source: 'A', target: 'C', sourceHandle: 'error', targetHandle: 'main' },
    ];
    const wf = toBackendWorkflow([node('A', { onError: 'error_output' }), node('B'), node('C')], edges, 'W');
    expect(wf.connections.map((c) => [c.target_node, c.source_output])).toEqual([
      ['B', 'main'],
      ['C', 'error'],
    ]);
    const back = fromBackendWorkflow(api(wf.nodes as never, wf.connections as never));
    expect(back.edges.find((e) => e.target === 'C')?.sourceHandle).toBe('error');
  });
});

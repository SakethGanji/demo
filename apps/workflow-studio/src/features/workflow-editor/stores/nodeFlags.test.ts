import { describe, it, expect, beforeEach } from 'vitest';
import type { Node } from '@xyflow/react';
import { useWorkflowStore } from './workflowStore';

const n = (name: string, extra: Record<string, unknown> = {}): Node => ({
  id: name,
  type: 'workflowNode',
  position: { x: 0, y: 0 },
  data: { name, type: 'Set', label: name, ...extra } as never,
});

describe('workflowStore node flags', () => {
  beforeEach(() => {
    useWorkflowStore.setState({
      nodes: [n('A', { onError: 'error_output' }), n('B'), n('C')],
      edges: [
        { id: 'e1', source: 'A', target: 'B', sourceHandle: 'main', targetHandle: 'main' },
        { id: 'e2', source: 'A', target: 'C', sourceHandle: 'error', targetHandle: 'main' },
      ],
      executionData: {},
    });
  });

  it('toggles disabled and marks the workflow dirty', () => {
    useWorkflowStore.getState().toggleNodeDisabled('B');
    expect((useWorkflowStore.getState().nodes[1].data as { disabled?: boolean }).disabled).toBe(true);
    expect(useWorkflowStore.getState()._isDirty).toBe(true);
    useWorkflowStore.getState().toggleNodeDisabled('B');
    expect((useWorkflowStore.getState().nodes[1].data as { disabled?: boolean }).disabled).toBe(false);
  });

  it('drops error-handle edges when leaving error_output, keeps main edges', () => {
    useWorkflowStore.getState().setNodeOnError('A', 'continue');
    const s = useWorkflowStore.getState();
    expect(s.edges.map((e) => e.id)).toEqual(['e1']);
    expect((s.nodes[0].data as { onError?: string }).onError).toBe('continue');
  });

  it('keeps error-handle edges while staying on error_output', () => {
    useWorkflowStore.getState().setNodeOnError('A', 'error_output');
    expect(useWorkflowStore.getState().edges).toHaveLength(2);
  });

  it('mergeExecutionData only touches the given nodes', () => {
    const s = useWorkflowStore.getState();
    s.mergeExecutionData({ A: { input: null, output: { items: [{ a: 1 }] }, status: 'success' }, B: { input: null, output: { items: [{ b: 1 }] }, status: 'success' } });
    useWorkflowStore.getState().mergeExecutionData({ B: { input: null, output: { items: [{ b: 2 }] }, status: 'success' } });
    const d = useWorkflowStore.getState().executionData;
    expect(d.A.output?.items).toEqual([{ a: 1 }]);
    expect(d.B.output?.items).toEqual([{ b: 2 }]);
  });
});

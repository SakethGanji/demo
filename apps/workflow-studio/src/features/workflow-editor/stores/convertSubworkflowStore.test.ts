import { beforeEach, describe, expect, it } from 'vitest';
import { selectedWorkflowNodeNames, useConvertSubworkflowStore } from './convertSubworkflowStore';

describe('convert sub-workflow store', () => {
  beforeEach(() => useConvertSubworkflowStore.setState({ open: false, nodeNames: [], highlight: new Set() }));

  it('opens with the selection and clears the highlight on close', () => {
    const s = useConvertSubworkflowStore.getState();
    s.openDialog(['A', 'B']);
    expect(useConvertSubworkflowStore.getState()).toMatchObject({ open: true, nodeNames: ['A', 'B'] });
    s.setHighlight(['A']);
    expect(useConvertSubworkflowStore.getState().highlight.has('A')).toBe(true);
    s.close();
    expect(useConvertSubworkflowStore.getState().open).toBe(false);
    expect(useConvertSubworkflowStore.getState().highlight.size).toBe(0);
  });

  it('names only selected workflow nodes, not sticky notes or unselected nodes', () => {
    const nodes = [
      { type: 'workflowNode', selected: true, data: { name: 'A' } },
      { type: 'workflowNode', selected: false, data: { name: 'B' } },
      { type: 'stickyNote', selected: true, data: { name: 'Note' } },
      { type: 'workflowNode', selected: true, data: { name: 'C' } },
    ];
    expect(selectedWorkflowNodeNames(nodes)).toEqual(['A', 'C']);
  });
});

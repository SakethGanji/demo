import { describe, expect, it } from 'vitest';
import { changesExistingWorkflow } from './proposalScope';

describe('changesExistingWorkflow', () => {
  it('is false for no proposal', () => {
    expect(changesExistingWorkflow(null)).toBe(false);
  });
  it('is false for a new build, also after it is kept and has a workflow id', () => {
    expect(changesExistingWorkflow({ workflow_id: null, base_definition: null })).toBe(false);
    expect(changesExistingWorkflow({ workflow_id: 'wf-1', base_definition: null })).toBe(false);
  });
  it('is true for a change to an existing workflow', () => {
    expect(changesExistingWorkflow({ workflow_id: 'wf-1', base_definition: { nodes: [], connections: [] } as never })).toBe(true);
  });
});

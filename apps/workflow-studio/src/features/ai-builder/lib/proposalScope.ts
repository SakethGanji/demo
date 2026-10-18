import type { Proposal } from '../types';

/**
 * True when the proposal changes a workflow that already existed. A brand-new build gets its `workflow_id` only
 * when it is kept, so the id alone cannot tell the two apart; the base draft can (it is null for a new workflow).
 */
export function changesExistingWorkflow(p: Pick<Proposal, 'workflow_id' | 'base_definition'> | null | undefined): boolean {
  return !!p && p.workflow_id != null && p.base_definition != null;
}

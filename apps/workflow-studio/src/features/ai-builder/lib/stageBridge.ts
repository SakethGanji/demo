/**
 * Where the sidebar meets the stage (PLAN-MOTION-UI §3.3): the draft the stage starts from, the settle pause on Keep, and
 * the swap that hands the canvas back to the saved draft without a visible jump.
 */
import { toast } from 'sonner';
import { workflowsApi } from '@/shared/lib/api';
import { motionOff } from '@/shared/motion/testFlag';
import { dur } from '@/shared/motion/tokens';
import { fromBackendWorkflow } from '@/features/workflow-editor/lib/workflowTransform';
import { useWorkflowStore } from '@/features/workflow-editor/stores/workflowStore';
import { carryMeasured } from '../stage/carryMeasured';
import { useStageStore, type StageEdge, type StageMode, type StageNode } from '../stage/stageStore';

/** The editor's draft as stage nodes. The "Add first step" placeholder is not a step, so it stays out. */
export function draftStageGraph(): { nodes: StageNode[]; edges: StageEdge[] } {
  const { nodes, edges } = useWorkflowStore.getState();
  return {
    nodes: nodes.filter((n) => n.type !== 'addNodes') as StageNode[],
    edges: edges as StageEdge[],
  };
}

/** Start (or re-point) the stage for `proposalId`. Safe to call on every state change. */
export function ensureStage(proposalId: string, mode: Exclude<StageMode, 'off'>): void {
  const st = useStageStore.getState();
  if (!st.active) {
    const { nodes, edges } = draftStageGraph();
    st.begin(proposalId, mode, nodes, edges);
    st.setPresence({ visible: true, node: null, label: null, at: null });
    return;
  }
  if (st.proposalId === proposalId) return;
  if (st.proposalId === 'pending') {
    useStageStore.setState({ proposalId });
    return;
  }
  // another proposal (a revision, or one opened from the history): a new stage, so the player starts over
  st.end();
  setTimeout(() => ensureStage(proposalId, mode), 0);
}

/** Leave the stage and let the draft show again. */
export function endStage(): void {
  const st = useStageStore.getState();
  if (st.active) st.end();
}

const sleep = (ms: number) => new Promise<void>((r) => setTimeout(r, ms));

/** The settle sweep on Keep: long enough for the green sweep across every node, none under the test flag. */
export async function settleStage(): Promise<void> {
  const st = useStageStore.getState();
  if (!st.active) return;
  st.setMode('settling');
  st.setPresence({ visible: false, node: null, label: null });
  if (motionOff()) return;
  await sleep(Math.min(1500, dur.settle * 1000 + st.nodes.length * 30 + 120));
}

/**
 * Load the saved workflow into the editor while the stage is still on screen, copying the stage's measurements onto the
 * loaded nodes, then drop the stage in the same tick: same geometry, same ids, nothing hidden for a frame.
 * `go` runs between the two (the URL change for a workflow that did not exist yet).
 */
export async function swapStageForDraft(workflowId: string, go?: () => Promise<unknown>): Promise<boolean> {
  try {
    const data = await workflowsApi.get(workflowId);
    const loaded = fromBackendWorkflow(data);
    const staged = useStageStore.getState().nodes;
    useWorkflowStore.getState().loadWorkflow({ ...loaded, nodes: carryMeasured(loaded.nodes, staged) });
    if (go) await go();
    endStage();
    return true;
  } catch (e) {
    toast.error('Could not reload the workflow', { description: e instanceof Error ? e.message : String(e) });
    endStage();
    return false;
  }
}

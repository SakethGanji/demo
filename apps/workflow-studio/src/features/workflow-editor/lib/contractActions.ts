/**
 * Learned-contract actions (CONTRACTS-STAGE2 §2): learn, approve, remove.
 * The draft is saved first (the engine reads the saved draft), and after approve/remove the
 * workflow is reloaded into the store the same way Stage 1's history restore does.
 */
import type { Node } from '@xyflow/react';
import { workflowsApi } from '@/shared/lib/api';
import type { ApiWorkflowDetail } from '@/shared/lib/backendTypes';
import { useWorkflowStore } from '../stores/workflowStore';
import { fromBackendWorkflow } from './workflowTransform';
import { saveDraftForRun, describeActionError } from './nodeActions';
import type { Shape } from './shapes';
import type { WorkflowNodeData } from '../types/workflow';

export interface ContractProposal {
  nodeName: string;
  workflowId: string;
  outputs: Record<string, Shape>;
  fromExecution: string;
  sampleCount: number;
}

export type ContractResult<T = void> = ({ ok: true } & (T extends void ? object : { value: T })) | { ok: false; message: string };

export function nodeNameOf(nodeId: string): string | null {
  const n = useWorkflowStore.getState().nodes.find((x) => x.id === nodeId && x.type === 'workflowNode') as
    | Node<WorkflowNodeData>
    | undefined;
  return n?.data.name ?? null;
}

/** Saves the draft, then asks the engine for a proposed contract. Nothing is stored by this call. */
export async function learnContract(
  nodeId: string,
  saveNew?: () => Promise<unknown>,
): Promise<{ ok: true; proposal: ContractProposal } | { ok: false; message: string }> {
  const name = nodeNameOf(nodeId);
  if (!name) return { ok: false, message: 'Node not found' };
  try {
    const workflowId = await saveDraftForRun(saveNew);
    if (!workflowId) return { ok: false, message: 'Save the workflow first' };
    const res = await workflowsApi.contractLearn(workflowId, name);
    return {
      ok: true,
      proposal: { nodeName: name, workflowId, outputs: res.outputs, fromExecution: res.from_execution, sampleCount: res.sample_count },
    };
  } catch (e) {
    return { ok: false, message: describeActionError(e).message };
  }
}

/** Puts a backend workflow into the store, keeping run data and pins (loadWorkflow clears both). */
export function reloadWorkflowKeepingRunData(api: ApiWorkflowDetail): void {
  const s = useWorkflowStore.getState();
  const { executionData, pinnedData } = s;
  s.loadWorkflow(fromBackendWorkflow(api));
  const after = useWorkflowStore.getState();
  after.mergeExecutionData(executionData);
  useWorkflowStore.setState({ pinnedData });
}

export async function approveContract(p: ContractProposal, approvedBy: string): Promise<ContractResult> {
  try {
    const wf = await workflowsApi.contractApprove(p.workflowId, p.nodeName, {
      outputs: p.outputs,
      from_execution: p.fromExecution,
      approved_by: approvedBy.trim(),
    });
    reloadWorkflowKeepingRunData(wf);
    return { ok: true };
  } catch (e) {
    return { ok: false, message: describeActionError(e).message };
  }
}

export async function removeContract(nodeId: string, saveNew?: () => Promise<unknown>): Promise<ContractResult> {
  const name = nodeNameOf(nodeId);
  if (!name) return { ok: false, message: 'Node not found' };
  try {
    const workflowId = await saveDraftForRun(saveNew);
    if (!workflowId) return { ok: false, message: 'Save the workflow first' };
    reloadWorkflowKeepingRunData(await workflowsApi.contractRemove(workflowId, name));
    return { ok: true };
  } catch (e) {
    return { ok: false, message: describeActionError(e).message };
  }
}

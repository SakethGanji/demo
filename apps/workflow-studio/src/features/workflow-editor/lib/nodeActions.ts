/**
 * Node-level run actions (Test step, Run to here, Run from here, Pin from last run).
 * Plain functions over the stores so the node panel, the context menu, the ⌘K palette and
 * shortcuts all share one path. Every call saves the draft first: the engine reads the saved draft.
 */
import type { Node } from '@xyflow/react';
import { workflowsApi } from '@/shared/lib/api';
import type { ApiExecutionResult } from '@/shared/lib/backendTypes';
import { ApiError } from '@/shared/api/http';
import { useWorkflowStore } from '../stores/workflowStore';
import { useEnvProfilesStore } from '../stores/envProfilesStore';
import { toBackendWorkflow, findUpstreamNodeName, buildNameToIdMap } from './workflowTransform';
import { flushPendingEdits } from './pendingEdits';
import { buildRunData } from './executionRunData';
import type { NodeExecutionData, WorkflowNodeData } from '../types/workflow';

export type TestSource = 'input' | 'last_run' | 'pinned';

export type NodeActionResult =
  | { ok: true; executionId: string; failed: boolean; firstError?: string }
  | { ok: false; message: string; status?: number; missingNodes?: string[] };

/** Saves the current canvas as the draft. A workflow that was never saved goes through `saveNew`. */
export async function saveDraftForRun(saveNew?: () => Promise<unknown>): Promise<string | null> {
  flushPendingEdits();
  const { workflowId, nodes, edges, workflowName, markAsSaved } = useWorkflowStore.getState();
  if (!workflowId) {
    await saveNew?.();
    return useWorkflowStore.getState().workflowId ?? null;
  }
  await workflowsApi.update(
    workflowId,
    toBackendWorkflow(nodes as Node<WorkflowNodeData>[], edges, workflowName, workflowId),
  );
  markAsSaved();
  return workflowId;
}

/** Turns a failed API call into a message (409 `missing_nodes` becomes a plain sentence). */
export function describeActionError(e: unknown): { message: string; status?: number; missingNodes?: string[] } {
  if (e instanceof ApiError) {
    const body = e.body as { detail?: unknown } | null;
    const detail = body?.detail;
    if (detail && typeof detail === 'object' && !Array.isArray(detail)) {
      const d = detail as { message?: string; missing_nodes?: string[] };
      const missing = Array.isArray(d.missing_nodes) ? d.missing_nodes : undefined;
      let message = d.message || e.message;
      if (e.status === 409 && missing && missing.length > 0) {
        message = `${message.replace(/\.$/, '')}. Missing output from: ${missing.join(', ')}.`;
      }
      return { message, status: e.status, missingNodes: missing };
    }
    if (typeof detail === 'string') return { message: detail, status: e.status };
    return { message: e.message, status: e.status };
  }
  return { message: e instanceof Error ? e.message : 'Unknown error' };
}

function toItems(data: unknown): Array<Record<string, unknown>> {
  if (!data) return [];
  if (Array.isArray(data)) {
    return data.map((it) =>
      it && typeof it === 'object' && 'json' in it ? (it as { json: Record<string, unknown> }).json : (it as Record<string, unknown>),
    );
  }
  return typeof data === 'object' ? [data as Record<string, unknown>] : [];
}

function nodeName(nodeId: string): string | null {
  const n = useWorkflowStore.getState().nodes.find((x) => x.id === nodeId && x.type === 'workflowNode');
  return n ? (n.data as WorkflowNodeData).name : null;
}

/** The items a node test starts from, for showing in the node's input panel after the run. */
function testInputItems(nodeId: string, source: TestSource, typed?: Array<Record<string, unknown>>) {
  if (source === 'input') return typed ?? [];
  const { nodes, edges, executionData, pinnedData } = useWorkflowStore.getState();
  const nameToId = buildNameToIdMap(nodes as Node<WorkflowNodeData>[]);
  const name = nodeName(nodeId);
  const upName = name ? findUpstreamNodeName(name, nameToId, edges) : null;
  const upId = upName ? nameToId.get(upName) : undefined;
  if (!upId) return [];
  if (source === 'pinned') return (pinnedData[upId] ?? []).map((i) => i.json);
  return (executionData[upId]?.output?.items as Array<Record<string, unknown>> | undefined) ?? [];
}

/** Run only this node. The result replaces this node's run data and nothing else. */
export async function testStep(
  nodeId: string,
  source: TestSource,
  typedItems?: Array<Record<string, unknown>>,
  saveNew?: () => Promise<unknown>,
): Promise<NodeActionResult> {
  const name = nodeName(nodeId);
  if (!name) return { ok: false, message: 'Node not found' };
  const store = useWorkflowStore.getState();
  const prev = store.executionData[nodeId];
  try {
    const id = await saveDraftForRun(saveNew);
    if (!id) return { ok: false, message: 'Save the workflow first' };
    store.mergeExecutionData({ [nodeId]: { input: prev?.input ?? null, output: null, status: 'running', startTime: Date.now() } });
    const res = await workflowsApi.testNode(id, name, {
      source,
      ...(source === 'input' ? { input_items: typedItems ?? [] } : {}),
      environment: useEnvProfilesStore.getState().activeEnvironment,
    });
    const err = res.errors.find((e) => e.node_name === name);
    const inputItems = testInputItems(nodeId, source, typedItems);
    const entry: NodeExecutionData = err
      ? { input: { items: inputItems }, output: { items: [], error: err.error }, status: 'error', startTime: Date.now(), endTime: Date.now() }
      : {
          input: { items: inputItems },
          output: { items: toItems(res.data[name]) },
          status: 'success',
          startTime: Date.now(),
          endTime: Date.now(),
        };
    useWorkflowStore.getState().mergeExecutionData({ [nodeId]: entry });
    return { ok: true, executionId: res.execution_id, failed: !!err, firstError: err?.error };
  } catch (e) {
    // Put back whatever the node showed before the call.
    useWorkflowStore.getState().mergeExecutionData({
      [nodeId]: prev ?? { input: null, output: null, status: 'idle' },
    });
    return { ok: false, ...describeActionError(e) };
  }
}

/** Replace the run data with a whole-run response (run-to / run-from), like a normal run. */
function applyRunResponse(res: ApiExecutionResult) {
  const { nodes, edges, exitViewingExecution, clearExecutionData, mergeExecutionData } = useWorkflowStore.getState();
  exitViewingExecution();
  clearExecutionData();
  const nodeData: Record<string, Array<{ json: Record<string, unknown> }>> = {};
  for (const [name, d] of Object.entries(res.data ?? {})) nodeData[name] = toItems(d).map((json) => ({ json }));
  const now = new Date().toISOString();
  mergeExecutionData(
    buildRunData({ start_time: now, end_time: now, errors: res.errors ?? [], node_data: nodeData }, nodes, edges),
  );
}

async function partialRun(
  call: (workflowId: string) => Promise<ApiExecutionResult>,
  saveNew?: () => Promise<unknown>,
): Promise<NodeActionResult> {
  try {
    const id = await saveDraftForRun(saveNew);
    if (!id) return { ok: false, message: 'Save the workflow first' };
    const res = await call(id);
    applyRunResponse(res);
    return { ok: true, executionId: res.execution_id, failed: res.errors.length > 0, firstError: res.errors[0]?.error };
  } catch (e) {
    return { ok: false, ...describeActionError(e) };
  }
}

export function runToNode(nodeId: string, saveNew?: () => Promise<unknown>) {
  const name = nodeName(nodeId);
  if (!name) return Promise.resolve<NodeActionResult>({ ok: false, message: 'Node not found' });
  return partialRun(
    (id) => workflowsApi.runTo(id, name, { environment: useEnvProfilesStore.getState().activeEnvironment }),
    saveNew,
  );
}

export function runFromNode(nodeId: string, saveNew?: () => Promise<unknown>) {
  const name = nodeName(nodeId);
  if (!name) return Promise.resolve<NodeActionResult>({ ok: false, message: 'Node not found' });
  return partialRun(
    (id) => workflowsApi.runFrom(id, name, { environment: useEnvProfilesStore.getState().activeEnvironment }),
    saveNew,
  );
}

/** Pins this node's output from the run data on the canvas. False when there is none. */
export function pinFromLastRun(nodeId: string): boolean {
  const { executionData, pinNodeData } = useWorkflowStore.getState();
  const items = executionData[nodeId]?.output?.items;
  if (!items || items.length === 0) return false;
  pinNodeData(nodeId, items.map((json) => ({ json: json as Record<string, unknown> })));
  return true;
}

/**
 * Turns a saved execution (`GET /api/executions/{id}`) into the per-node run data the canvas and
 * the node panel read (`workflowStore.executionData`), the same shape a live run fills.
 */
import type { Node, Edge } from '@xyflow/react';
import type { ApiExecutionDetail } from '@/shared/lib/api';
import type { NodeExecutionData, NodeMetrics, WorkflowNodeData } from '../types/workflow';
import { buildNameToIdMap, findUpstreamNodeName } from './workflowTransform';

export function buildRunData(
  detail: Pick<ApiExecutionDetail, 'start_time' | 'end_time' | 'errors' | 'node_data' | 'node_metrics'>,
  nodes: Node[],
  edges: Pick<Edge, 'source' | 'target'>[],
): Record<string, NodeExecutionData> {
  const nameToId = buildNameToIdMap(nodes as Node<WorkflowNodeData>[]);
  const start = Date.parse(detail.start_time) || undefined;
  const end = detail.end_time ? Date.parse(detail.end_time) || undefined : undefined;
  const errorByNode = new Map<string, string>();
  for (const e of detail.errors ?? []) errorByNode.set(e.node_name, e.error);

  const out: Record<string, NodeExecutionData> = {};
  for (const [name, id] of nameToId) {
    const items = detail.node_data?.[name];
    const metrics = detail.node_metrics?.[name] as NodeMetrics | undefined;
    const upstream = findUpstreamNodeName(name, nameToId, edges);
    const upstreamItems = upstream ? detail.node_data?.[upstream] : undefined;
    const input = upstreamItems ? { items: upstreamItems.map((d) => d.json) } : null;
    if (errorByNode.has(name)) {
      // A failed node still shows what it received, so the run can be debugged from its input.
      out[id] = {
        input,
        output: { items: [], error: errorByNode.get(name) },
        status: 'error',
        startTime: start,
        endTime: end,
        metrics,
      };
      continue;
    }
    if ((metrics as { status?: string } | undefined)?.status === 'waiting') {
      // An Approval that paused the run: its input is its pending output, so show no output yet.
      out[id] = { input: items ? { items: items.map((d) => d.json) } : input, output: { items: [] }, status: 'waiting', startTime: start, metrics };
      continue;
    }
    if (!items) continue; // the node did not run in this execution
    out[id] = {
      input,
      output: { items: items.map((d) => d.json) },
      status: 'success',
      startTime: start,
      endTime: end,
      metrics,
    };
  }
  return out;
}

/**
 * Builds what the canvas needs to show a version diff: per-node marks and faded ghost nodes.
 * Base = the saved version, target = what is on the canvas now, so "added" nodes exist on the
 * canvas (green), "removed" nodes only in the version (ghosts) and "changed" differ in parameters.
 */
import type { Node } from '@xyflow/react';
import type { ApiWorkflowDetail } from '@/shared/lib/backendTypes';
import type { WorkflowDefinition } from '@/features/projects/hooks/useWorkflows';
import type { WorkflowNodeData } from '../types/workflow';
import type { DiffMark } from '../stores/historyStore';
import { diffWorkflows } from './workflowDiff';
import { buildNameToIdMap, fromBackendWorkflow } from './workflowTransform';

/** Ghosts are not in the store, so React Flow never measures them; without a size it keeps them hidden. */
const GHOST_SIZE = { width: 64, height: 87 };

type BackendDefinition = ApiWorkflowDetail['definition'];

export function backendToDefinition(def: BackendDefinition): WorkflowDefinition {
  return {
    nodes: def.nodes.map((n) => ({ name: n.name, type: n.type, parameters: n.parameters ?? {}, position: n.position })),
    connections: def.connections.map((c) => ({
      sourceNode: c.source_node,
      targetNode: c.target_node,
      sourceOutput: c.source_output,
      targetInput: c.target_input,
    })),
  };
}

export function canvasToDefinition(nodes: Node[], edges: { source: string; target: string; sourceHandle?: string | null; targetHandle?: string | null }[]): WorkflowDefinition {
  const wf = nodes.filter((n) => n.type === 'workflowNode') as Node<WorkflowNodeData>[];
  const idToName = new Map(wf.map((n) => [n.id, n.data.name]));
  return {
    nodes: wf.map((n) => ({ name: n.data.name, type: n.data.type, parameters: n.data.parameters ?? {}, position: n.position })),
    connections: edges
      .filter((e) => idToName.has(e.source) && idToName.has(e.target))
      .map((e) => ({
        sourceNode: idToName.get(e.source)!,
        targetNode: idToName.get(e.target)!,
        sourceOutput: e.sourceHandle ?? 'output-0',
        targetInput: e.targetHandle ?? 'input-0',
      })),
  };
}

export function buildVersionDiffView(
  versionDef: BackendDefinition,
  nodes: Node[],
  edges: { source: string; target: string; sourceHandle?: string | null; targetHandle?: string | null }[],
): { marks: Record<string, DiffMark>; ghostNodes: Node[] } {
  const diff = diffWorkflows(backendToDefinition(versionDef), canvasToDefinition(nodes, edges));
  const nameToId = buildNameToIdMap(nodes as Node<WorkflowNodeData>[]);
  const marks: Record<string, DiffMark> = {};
  for (const name of diff.added) marks[nameToId.get(name) ?? name] = 'added';
  for (const name of diff.changed) marks[nameToId.get(name) ?? name] = 'changed';
  for (const name of diff.removed) marks[name] = 'removed';

  const removed = new Set(diff.removed);
  const ghostNodes = removed.size
    ? fromBackendWorkflow({ id: '', name: '', active: false, definition: versionDef } as ApiWorkflowDetail).nodes
        .filter((n) => removed.has(n.data.name))
        .map((n) => ({ ...n, draggable: false, selectable: false, connectable: false, deletable: false, focusable: false, zIndex: -1, measured: GHOST_SIZE }))
    : [];
  return { marks, ghostNodes };
}

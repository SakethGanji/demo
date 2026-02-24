/**
 * Custom hooks wrapping parameterized Zustand selectors.
 *
 * Keeps useCallback + equality logic in one place so components
 * just call e.g. `useNodeExecution(id)` with no boilerplate.
 */

import { useCallback } from 'react';
import type { Node } from 'reactflow';
import { useWorkflowStore } from '../stores/workflowStore';
import { getSubnodeDisplayLabel } from '../lib/nodeConfig';
import type {
  WorkflowNodeData,
  NodeExecutionData,
  SubnodeSlotDefinition,
} from '../types/workflow';
import { type NodeStyleConfig, getNodeStyles } from '../lib/nodeStyles';
import { normalizeNodeGroup, SUBNODE_SLOT_NAMES } from '../lib/nodeConfig';
import { jsonEqual, setEqual, shallowObjectEqual } from '../lib/equality';

// ---------------------------------------------------------------------------
// Execution data
// ---------------------------------------------------------------------------

/** Execution data for a single node (or null). */
export function useNodeExecution(nodeId: string | null) {
  return useWorkflowStore(
    useCallback(
      (s) => (nodeId ? s.executionData[nodeId] ?? null : null),
      [nodeId]
    )
  );
}

/** Just the execution status string for a single node (primitive — very stable). */
export function useNodeExecStatus(nodeId: string) {
  return useWorkflowStore(
    useCallback((s) => s.executionData[nodeId]?.status ?? null, [nodeId])
  );
}

/** Execution summary: hasErrors, hasLogs, totalDuration. */
export function useExecSummary() {
  return useWorkflowStore(
    useCallback((s) => {
      const entries = Object.values(s.executionData);
      return {
        hasErrors: entries.some((d) => d.status === 'error'),
        hasLogs: entries.length > 0,
        totalDuration: entries.reduce(
          (sum, d) => sum + (d.startTime && d.endTime ? d.endTime - d.startTime : 0),
          0
        ),
      };
    }, []),
    jsonEqual
  );
}

/** Map of node name → execution output items (for expression autocomplete). */
export function useAllNodeExecData() {
  return useWorkflowStore(
    useCallback((s) => {
      const result: Record<string, Record<string, unknown>[]> = {};
      for (const n of s.nodes) {
        const nodeName = n.data?.name || n.data?.label;
        if (nodeName && s.executionData[n.id]?.output?.items) {
          result[nodeName] = s.executionData[n.id].output.items as Record<string, unknown>[];
        }
      }
      return result;
    }, []),
    shallowObjectEqual
  );
}

// ---------------------------------------------------------------------------
// Node lookups
// ---------------------------------------------------------------------------

/** Find a node by ID (returns stable ref unless that node's data changes). */
export function useNodeById(nodeId: string | null) {
  return useWorkflowStore(
    useCallback(
      (s) => (nodeId ? s.nodes.find((n) => n.id === nodeId) ?? null : null),
      [nodeId]
    )
  );
}

/** Upstream node ID for a given target node (string primitive). */
export function useUpstreamNodeId(nodeId: string) {
  return useWorkflowStore(
    useCallback(
      (s) => s.edges.find((e) => e.target === nodeId)?.source ?? null,
      [nodeId]
    )
  );
}

/** Upstream node's execution output items. */
export function useUpstreamSampleData(upstreamNodeId: string | null) {
  return useWorkflowStore(
    useCallback(
      (s) => {
        if (!upstreamNodeId) return undefined;
        return s.executionData[upstreamNodeId]?.output?.items as
          | Record<string, unknown>[]
          | undefined;
      },
      [upstreamNodeId]
    )
  );
}

// ---------------------------------------------------------------------------
// Edge / connection queries (for canvas nodes)
// ---------------------------------------------------------------------------

/** Whether a node has any non-subnode input connection (boolean — very stable). */
export function useHasInputConnection(nodeId: string) {
  return useWorkflowStore(
    useCallback(
      (s) =>
        s.edges.some(
          (e) =>
            e.target === nodeId &&
            !e.data?.isSubnodeEdge &&
            !SUBNODE_SLOT_NAMES.includes(e.targetHandle || '')
        ),
      [nodeId]
    )
  );
}

/** Set of connected output handle IDs for a node. */
export function useConnectedOutputHandles(nodeId: string) {
  return useWorkflowStore(
    useCallback(
      (s) => {
        const handles = new Set<string>();
        for (const e of s.edges) {
          if (e.source === nodeId && e.sourceHandle) handles.add(e.sourceHandle);
        }
        return handles;
      },
      [nodeId]
    ),
    setEqual
  );
}

/** Subnode slot data: per-slot { canAddMore, subnodes[] }. */
export function useSubnodeSlotData(
  nodeId: string,
  subnodeSlots: SubnodeSlotDefinition[] | undefined
) {
  return useWorkflowStore(
    useCallback(
      (s) => {
        if (!subnodeSlots || subnodeSlots.length === 0) return null;

        const result: Record<
          string,
          {
            canAddMore: boolean;
            subnodes: { id: string; label: string; type: string; icon?: string; nodeType: string }[];
          }
        > = {};

        for (const slot of subnodeSlots) {
          const subnodeEdges = s.edges.filter(
            (e) =>
              e.target === nodeId &&
              e.data?.isSubnodeEdge &&
              (e.data as { slotName?: string }).slotName === slot.name
          );
          const subnodes = subnodeEdges
            .map((e) => {
              const node = s.nodes.find((n) => n.id === e.source);
              if (!node) return null;
              const nd = node.data as WorkflowNodeData;
              return {
                id: node.id,
                label: getSubnodeDisplayLabel(nd),
                type: nd.subnodeType || 'tool',
                icon: nd.icon,
                nodeType: nd.type,
              };
            })
            .filter(Boolean) as {
            id: string;
            label: string;
            type: string;
            icon?: string;
            nodeType: string;
          }[];

          result[slot.name] = {
            canAddMore: slot.multiple || subnodes.length === 0,
            subnodes,
          };
        }

        return result;
      },
      [nodeId, subnodeSlots]
    ),
    jsonEqual
  );
}

/** Connected subnodes grouped by slot (for NodeSettings). */
export function useConnectedSubnodes(
  nodeId: string,
  subnodeSlots: SubnodeSlotDefinition[] | undefined
) {
  return useWorkflowStore(
    useCallback(
      (s) => {
        if (!subnodeSlots || subnodeSlots.length === 0) return null;

        const subnodeEdges = s.edges.filter(
          (e) => e.target === nodeId && e.data?.isSubnodeEdge
        );

        const slotMap: Record<string, Node<WorkflowNodeData>[]> = {};
        for (const slot of subnodeSlots) {
          slotMap[slot.name] = [];
        }

        for (const edge of subnodeEdges) {
          const slotName = edge.data?.slotName || edge.targetHandle;
          const subnodeNode = s.nodes.find((n) => n.id === edge.source);
          if (subnodeNode && slotName && slotMap[slotName]) {
            slotMap[slotName].push(subnodeNode as Node<WorkflowNodeData>);
          }
        }

        return { slots: subnodeSlots, slotMap };
      },
      [nodeId, subnodeSlots]
    ),
    jsonEqual
  );
}

// ---------------------------------------------------------------------------
// Subnode helpers
// ---------------------------------------------------------------------------

/** Parent node styles for a subnode (derives from parent's group). */
export function useParentStyles(subnodeId: string) {
  return useWorkflowStore(
    useCallback(
      (s): NodeStyleConfig | null => {
        const parentEdge = s.edges.find(
          (e) => e.source === subnodeId && e.data?.isSubnodeEdge
        );
        if (!parentEdge) return null;
        const parentNode = s.nodes.find((n) => n.id === parentEdge.target);
        if (!parentNode) return null;
        const parentData = parentNode.data as WorkflowNodeData;
        const group = normalizeNodeGroup(
          parentData.group ? [parentData.group] : undefined
        );
        return getNodeStyles(group);
      },
      [subnodeId]
    ),
    (a, b) => {
      if (a === b) return true;
      if (!a || !b) return false;
      return (
        a.bgColor === b.bgColor &&
        a.borderColor === b.borderColor &&
        a.accentColor === b.accentColor &&
        a.iconBgColor === b.iconBgColor
      );
    }
  );
}

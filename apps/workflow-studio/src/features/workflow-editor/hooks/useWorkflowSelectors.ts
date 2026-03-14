/**
 * Custom hooks wrapping parameterized Zustand selectors.
 *
 * Keeps useCallback + equality logic in one place so components
 * just call e.g. `useNodeExecution(id)` with no boilerplate.
 */

import { useCallback } from 'react';
import { useWorkflowStore } from '../stores/workflowStore';

// ---------------------------------------------------------------------------
// Equality functions (inlined from equality.ts)
// ---------------------------------------------------------------------------

/** Deep-compare small objects via JSON serialization. */
function jsonEqual<T>(a: T, b: T): boolean {
  return JSON.stringify(a) === JSON.stringify(b);
}

/** Compare two Sets by size and membership. */
function setEqual<T>(a: Set<T>, b: Set<T>): boolean {
  if (a.size !== b.size) return false;
  for (const v of a) {
    if (!b.has(v)) return false;
  }
  return true;
}

/** Shallow-compare an object's keys and value references (===). */
function shallowObjectEqual<T extends Record<string, unknown>>(a: T, b: T): boolean {
  const keysA = Object.keys(a);
  const keysB = Object.keys(b);
  if (keysA.length !== keysB.length) return false;
  return keysA.every((k) => a[k] === b[k]);
}

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

/** Whether a node has any input connection (boolean — very stable). */
export function useHasInputConnection(nodeId: string) {
  return useWorkflowStore(
    useCallback(
      (s) =>
        s.edges.some(
          (e) => e.target === nodeId
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

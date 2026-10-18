import { useCallback } from 'react';
import { useWorkflowStore } from '../../../stores/workflowStore';
import type { WorkflowNodeData } from '../../../types/workflow';

/** Merges `patch` into the node's parameters (reads the latest node, so two quick edits never overwrite each other). */
export function useNodeParamsPatch(nodeId: string) {
  return useCallback(
    (patch: Record<string, unknown>) => {
      const store = useWorkflowStore.getState();
      const current = store.nodes.find((n) => n.id === nodeId);
      if (!current) return;
      store.updateNodeData(nodeId, { parameters: { ...(current.data as WorkflowNodeData).parameters, ...patch } });
    },
    [nodeId],
  );
}

import { useMemo } from 'react';
import { useWorkflowStore } from '../stores/workflowStore';
import { nodeLabelMap } from '../lib/tests';

/** Node name -> canvas label (name when unlabelled). */
export function useNodeLabels(): Record<string, string> {
  const nodes = useWorkflowStore((s) => s.nodes);
  return useMemo(() => nodeLabelMap(nodes), [nodes]);
}

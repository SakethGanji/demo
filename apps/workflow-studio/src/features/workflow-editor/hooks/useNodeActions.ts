import { useCallback, useMemo } from 'react';
import { toast } from 'sonner';
import { useWorkflowStore } from '../stores/workflowStore';
import { useSaveWorkflow } from './useWorkflowApi';
import { testStep, runToNode, runFromNode, pinFromLastRun, type NodeActionResult } from '../lib/nodeActions';

/** Node run actions with toasts for errors (the node panel shows its own inline message). */
export function useNodeActions() {
  const { saveWorkflow } = useSaveWorkflow();
  const saveNew = useCallback(() => saveWorkflow(), [saveWorkflow]);

  const report = useCallback((label: string, res: NodeActionResult) => {
    if (!res.ok) {
      toast.error(`${label} failed`, { description: res.message });
    } else if (res.failed) {
      toast.error(`${label}: the run had errors`, { description: res.firstError });
    }
    return res;
  }, []);

  return useMemo(
    () => ({
      testStep: async (nodeId: string, source: 'input' | 'last_run' | 'pinned' = 'last_run', items?: Array<Record<string, unknown>>) =>
        report('Test step', await testStep(nodeId, source, items, saveNew)),
      runTo: async (nodeId: string) => report('Run to here', await runToNode(nodeId, saveNew)),
      runFrom: async (nodeId: string) => report('Run from here', await runFromNode(nodeId, saveNew)),
      toggleDisable: (nodeId: string) => useWorkflowStore.getState().toggleNodeDisabled(nodeId),
      pinFromLastRun: (nodeId: string) => {
        const ok = pinFromLastRun(nodeId);
        if (!ok) toast.info('No run data to pin', { description: 'Run this node first.' });
        return ok;
      },
      saveNew,
    }),
    [report, saveNew],
  );
}

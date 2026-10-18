import { useEffect } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { driftApi } from '@/shared/lib/api';
import { qk } from '@/shared/api/queryKeys';
import { useWorkflowStore } from '../stores/workflowStore';
import { useTestStore } from '../stores/testStore';
import { staleSet } from '../lib/testsAi';

/** Open drift alerts and stale test ids of the open workflow. Refetched after each live run. */
export function useDrift() {
  const workflowId = useWorkflowStore((s) => s.workflowId);
  const lastExecutionId = useTestStore((s) => s.lastExecutionId);
  const qc = useQueryClient();
  const q = useQuery({
    queryKey: qk.workflowDrift(workflowId ?? ''),
    queryFn: () => driftApi.forWorkflow(workflowId!, 'open'),
    enabled: !!workflowId,
    staleTime: 5_000,
    refetchInterval: 10_000,
  });
  useEffect(() => {
    if (!workflowId || !lastExecutionId) return;
    const refetch = () => void qc.invalidateQueries({ queryKey: qk.workflowDrift(workflowId) });
    // detection runs when the execution completes, so look again shortly after it started
    const timers = [setTimeout(refetch, 1500), setTimeout(refetch, 5000)];
    return () => timers.forEach(clearTimeout);
  }, [workflowId, lastExecutionId, qc]);
  return q;
}

export function useStaleTests(): Set<string> {
  const { data } = useDrift();
  return staleSet(data);
}

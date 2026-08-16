import { useQuery } from '@tanstack/react-query';
import { executionsApi } from '@/shared/lib/api';

export interface LatestExecution {
  status: string;
  time: string;
  errorCount: number;
}

async function fetchLatestExecutions(): Promise<Map<string, LatestExecution>> {
  const executions = await executionsApi.list();
  const map = new Map<string, LatestExecution>();

  for (const exec of executions) {
    const existing = map.get(exec.workflow_id);
    if (!existing || new Date(exec.start_time) > new Date(existing.time)) {
      map.set(exec.workflow_id, {
        status: exec.status,
        time: exec.start_time,
        errorCount: exec.error_count,
      });
    }
  }

  return map;
}

export function useLatestExecutions() {
  return useQuery({
    queryKey: ['latest-executions'],
    queryFn: fetchLatestExecutions,
    staleTime: 1000 * 60 * 2,
  });
}

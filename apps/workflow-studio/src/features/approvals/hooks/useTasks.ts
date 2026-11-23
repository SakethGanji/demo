import { useInfiniteQuery, useQuery } from '@tanstack/react-query'
import { tasksApi } from '@/shared/api/endpoints'
import { qk } from '@/shared/api/queryKeys'
import { flattenPages, nextCursor, type InboxStatus } from '../lib/tasks'

const INBOX_POLL_MS = 15_000

/** The inbox: keyset pages of 25 (`next_cursor`), newest first. Never OFFSET (§8.4). */
export function useTaskInbox(pid: string, status: InboxStatus, workflowId?: string) {
  const q = useInfiniteQuery({
    queryKey: qk.tasks.list(pid, { status, workflowId: workflowId ?? null }),
    initialPageParam: null as string | null,
    queryFn: ({ pageParam }) => tasksApi.list(pid, { status, workflow_id: workflowId, cursor: pageParam }),
    getNextPageParam: (last) => nextCursor(last),
    refetchInterval: INBOX_POLL_MS,
  })
  return { ...q, tasks: flattenPages(q.data?.pages) }
}

export function useTask(id: string) {
  return useQuery({ queryKey: qk.tasks.detail(id), queryFn: () => tasksApi.get(id), refetchInterval: INBOX_POLL_MS })
}

/** The "N waiting" number behind the rail badge (§11.4). */
export function useTaskCounts(pid: string) {
  return useQuery({ queryKey: qk.tasks.counts(pid), queryFn: () => tasksApi.counts(pid), refetchInterval: INBOX_POLL_MS, staleTime: 5000 })
}

/** Open tasks of one workflow, tallied per Approval node for the canvas chip (`node-approval-waiting`). */
export function useWorkflowOpenTasks(pid: string, workflowId: string | null) {
  return useQuery({
    queryKey: qk.tasks.workflowOpen(pid, workflowId ?? ''),
    enabled: !!workflowId && workflowId !== 'new',
    queryFn: () => tasksApi.list(pid, { status: 'open', workflow_id: workflowId as string, limit: 100 }),
    refetchInterval: INBOX_POLL_MS,
    staleTime: 5000,
    select: (page) => {
      const byNode: Record<string, number> = {}
      for (const t of page.items) byNode[t.node_name] = (byNode[t.node_name] ?? 0) + 1
      return byNode
    },
  })
}

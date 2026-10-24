/**
 * Run data for the glass editor: the workflow's runs (paged, newest first), one run's detail, and the
 * run actions (Retry from the failed step, Save as test). Shared by the run picker, the Page view and the run card.
 */
import { useInfiniteQuery, useQuery, useQueryClient } from '@tanstack/react-query'
import { useNavigate } from '@tanstack/react-router'
import { useCallback } from 'react'
import { toast } from 'sonner'
import { runsApi } from '@/shared/api/endpoints'
import { request } from '@/shared/api/http'
import { executionsApi, type ApiExecutionDetail } from '@/shared/lib/api'
import { useEditorProjectStore } from '../../stores/editorProjectStore'
import { useWorkflowStore } from '../../stores/workflowStore'
import { newTestId, type TestCase } from '../../lib/tests'
import { isTriggerNode } from '../lib/pageSentences'
import { useGlassStore } from '../state/glassStore'

export const PAGE_SIZE = 20

export function useWorkflowRuns(workflowId: string | undefined, enabled = true) {
  const projectId = useEditorProjectStore((s) => s.projectId)
  return useInfiniteQuery({
    queryKey: ['glass', 'runs', projectId, workflowId],
    queryFn: ({ pageParam }) => runsApi.list(projectId, { workflow_id: workflowId, limit: PAGE_SIZE, before: pageParam ?? undefined }),
    initialPageParam: null as string | null,
    getNextPageParam: (last) => last.nextCursor,
    enabled: !!workflowId && workflowId !== 'new' && enabled,
    staleTime: 5_000,
  })
}

const RUNNING = new Set(['running', 'waiting', 'queued', 'new', 'pending'])

/** One run's detail; refetches while the run is still going. */
export function useRunDetail(runId: string | null | undefined) {
  return useQuery({
    queryKey: ['glass', 'run', runId],
    queryFn: () => executionsApi.get(runId!),
    enabled: !!runId,
    refetchInterval: (q) => (q.state.data && RUNNING.has(String(q.state.data.status).toLowerCase()) ? 1500 : false),
  })
}

/** Point every view at a run (`?execution=`), or back to live with null. */
export function useSetRun() {
  const navigate = useNavigate()
  return useCallback(
    (id: string | null) => {
      useGlassStore.getState().setRunId(id)
      useGlassStore.getState().setLiveChosen(!id)
      if (!id) useWorkflowStore.getState().exitViewingExecution()
      navigate({ to: '.', search: (prev: Record<string, unknown>) => ({ ...prev, execution: id ?? undefined }), replace: true } as never)
    },
    [navigate],
  )
}

/** Retry a failed run from the step that failed; the new run becomes the shown one. */
export function useRetryRun() {
  const setRun = useSetRun()
  const qc = useQueryClient()
  return useCallback(
    async (executionId: string) => {
      try {
        const res = await request<{ success: boolean; message?: string }>(`/api/executions/${encodeURIComponent(executionId)}/retry`, { method: 'POST' })
        const id = res.message?.match(/Retry started:\s*(\S+)/)?.[1]
        toast.success('Retrying from the failed step')
        void qc.invalidateQueries({ queryKey: ['glass', 'runs'] })
        if (id) setRun(id)
      } catch (e) {
        toast.error('Could not retry the run', { description: e instanceof Error ? e.message : String(e) })
      }
    },
    [qc, setRun],
  )
}

/**
 * A draft test from a saved run: the trigger's items as the input, and the item count each step produced as
 * the expectation. It joins the draft's tests (save the workflow to keep it).
 */
export function testFromRun(detail: ApiExecutionDetail, triggerName: string | undefined, nodeTypes: Record<string, string>): TestCase {
  const data = detail.node_data ?? {}
  const trig = triggerName ?? Object.keys(data).find((n) => isTriggerNode(nodeTypes[n] ?? ''))
  const items = trig ? (data[trig] ?? []).map((d) => d.json) : [{}]
  const failed = new Set((detail.errors ?? []).map((e) => e.node_name))
  const expect = Object.entries(data)
    .filter(([n]) => n !== trig && !failed.has(n))
    .map(([node, out]) => ({ node, op: 'count' as const, value: out.length }))
  return {
    id: newTestId(),
    name: `Run #${detail.id.slice(0, 6)}`,
    description: `Saved from the run of ${new Date(detail.start_time).toLocaleString()}`,
    target: { kind: 'workflow' },
    input: { items: items.length ? items : [{}] },
    mocks: {},
    expect,
    category: 'normal',
    status: 'draft',
    locked: false,
    source: 'replay',
  }
}

export async function saveRunAsTest(executionId: string, triggerName?: string) {
  try {
    const detail = await executionsApi.get(executionId)
    const st = useWorkflowStore.getState()
    const types: Record<string, string> = {}
    for (const n of st.nodes) if (n.type === 'workflowNode') types[(n.data as { name: string }).name] = (n.data as { type: string }).type
    const t = testFromRun(detail, triggerName, types)
    st.upsertTest(t)
    toast.success(`Saved ${t.name} as a draft test`, { description: 'Save the workflow to keep it.' })
  } catch (e) {
    toast.error('Could not save the run as a test', { description: e instanceof Error ? e.message : String(e) })
  }
}

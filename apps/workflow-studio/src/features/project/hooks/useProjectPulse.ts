/**
 * The live state of a project's workflows and apps, shared by the project rail and the Overview: the two lists, the
 * newest 100 runs (the same query key as `useRunStats` / `useStoryRuns`, so pages that also read runs make no
 * extra request) and the open approval counts. `now` is taken when the runs arrive, not on every render.
 */
import { useQuery } from '@tanstack/react-query'
import { runsApi } from '@/shared/api/endpoints'
import { qk } from '@/shared/api/queryKeys'
import type { RunListItem } from '@/shared/api/types'
import { useTaskCounts } from '@/features/approvals/hooks/useTasks'
import { useProjectApps, useProjectWorkflows } from '@/features/project/hooks/useProjectData'
import { entityStatuses, type ProjectEntity } from '../lib/entities'

const withNow = (d: { runs: RunListItem[] }) => ({ runs: d.runs, now: Date.now() })

export function useProjectPulse(pid: string, { poll = 8_000 }: { poll?: number } = {}) {
  const workflows = useProjectWorkflows(pid)
  const apps = useProjectApps(pid)
  const runs = useQuery({
    queryKey: qk.runs(pid, { stats: 100 }),
    queryFn: () => runsApi.list(pid, { limit: 100 }),
    refetchInterval: poll,
    select: withNow,
  })
  const tasks = useTaskCounts(pid)

  const wfEntities: ProjectEntity[] = (workflows.data ?? []).map((w) => ({ kind: 'workflow', id: w.id, name: w.name, updatedAt: w.updated_at }))
  const appEntities: ProjectEntity[] = (apps.data ?? []).map((a) => ({ kind: 'app', id: a.id, name: a.name, updatedAt: a.updated_at }))
  const entities = [...wfEntities, ...appEntities]
  const now = runs.data?.now ?? 0
  const status = entityStatuses(entities, runs.data?.runs ?? [], now, tasks.data?.by_workflow)

  return {
    workflows: workflows.data ?? [],
    apps: apps.data ?? [],
    wfEntities,
    appEntities,
    entities,
    status,
    runs: runs.data?.runs ?? [],
    now,
    openTasks: tasks.data?.open ?? 0,
    listsReady: workflows.data != null && apps.data != null,
    runsReady: runs.data != null,
    error: workflows.error ?? apps.error ?? runs.error ?? null,
  }
}

export type ProjectPulse = ReturnType<typeof useProjectPulse>

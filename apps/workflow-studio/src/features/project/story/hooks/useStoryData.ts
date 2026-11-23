/**
 * Everything the Overview story reads, gathered once: the project's objects, their relationships (app API
 * usage, route handlers, workflow definitions) and the newest runs. The run list shares `useRunStats`'s query
 * (same key, same 8s poll), so the page makes one request for both.
 */
import { useQueries, useQuery } from '@tanstack/react-query'
import { appApiApi, runsApi } from '@/shared/api/endpoints'
import { qk } from '@/shared/api/queryKeys'
import type { RunListItem } from '@/shared/api/types'
import { workflowsApi } from '@/shared/lib/api'
import { useDatasets } from '@/features/datasets/useDatasets'
import { triggerLabel, type WorkflowSummary } from '@/features/workflows-list/workflowMeta'
import { useMissingSecrets, useModules, useProjectApps, useProjectWorkflows, useRoutes, useSecrets, useTables } from '../../hooks/useProjectData'
import { buildModel, routesOf, type WorkflowInfo } from '../lib/model'

/** workflow definitions are fetched for at most this many workflows (trigger label, dataset wires) */
const MAX_DETAILS = 40

const withNow = (d: { runs: RunListItem[] }) => ({ runs: d.runs, now: Date.now() })

/** The newest 100 runs (the engine keeps 100 per project), polled every 8s. */
export function useStoryRuns(pid: string) {
  return useQuery({
    queryKey: qk.runs(pid, { stats: 100 }),
    queryFn: () => runsApi.list(pid, { limit: 100 }),
    refetchInterval: 8_000,
    select: withNow,
  })
}

export function useStoryData(pid: string) {
  const apps = useProjectApps(pid)
  const routes = useRoutes(pid)
  const modules = useModules(pid)
  const workflows = useProjectWorkflows(pid)
  const tables = useTables(pid)
  const datasets = useDatasets(pid)
  const secrets = useSecrets(pid)
  const missing = useMissingSecrets(pid)
  const runs = useStoryRuns(pid)

  const usage = useQueries({
    queries: (apps.data ?? []).map((a) => ({ queryKey: qk.apiUsage(a.id), queryFn: () => appApiApi.apiUsage(a.id) })),
  })
  const wfList = (workflows.data ?? []) as WorkflowSummary[]
  const details = useQueries({
    queries: wfList.slice(0, MAX_DETAILS).map((w) => ({
      queryKey: [...qk.workflow(w.id), w.updated_at],
      queryFn: () => workflowsApi.get(w.id),
      staleTime: 60_000,
    })),
  })

  // cheap (a few dozen objects, 100 runs): rebuilt each render, the React Compiler memoizes it
  const routeList = routes.data ?? []
  const usageMap = new Map((apps.data ?? []).map((a, i) => [a.id, usage[i]?.data ?? []]))
  const infos: WorkflowInfo[] = wfList.map((w, i) => {
    const detail = details[i]?.data
    const handles = routesOf(w, routeList).length > 0
    return { summary: w, detail, trigger: detail ? triggerLabel(detail.definition, handles) : handles ? 'Route' : '—' }
  })
  const model = buildModel({
    apps: apps.data ?? [],
    usage: usageMap,
    routes: routeList,
    modules: modules.data ?? [],
    workflows: infos,
    tables: tables.data ?? [],
    datasets: datasets.data ?? [],
    missing: missing.data ?? [],
    runs: runs.data?.runs ?? [],
    now: runs.data?.now ?? 0,
  })
  const detailById = new Map(wfList.map((w, i) => [w.id, details[i]?.data] as const))

  // the map is drawn once every list it needs has answered (an error counts as an answer), so lanes don't
  // pop in one by one; a failed list is named so the page can say what is missing instead of hiding it
  const lists = { interfaces: apps, routes, modules, workflows, tables, datasets, runs } as const
  const ready = Object.values(lists).every((q) => q.data != null || q.isError)
  const failed = (Object.keys(lists) as (keyof typeof lists)[]).filter((k) => lists[k].isError)
  const retry = () => {
    for (const k of failed) void lists[k].refetch()
  }

  return {
    model,
    apps: apps.data ?? [],
    routes: routes.data ?? [],
    modules: modules.data ?? [],
    workflows: wfList,
    detailById,
    usage: usageMap,
    tables: tables.data ?? [],
    datasets: datasets.data ?? [],
    secrets: secrets.data ?? [],
    missing: missing.data ?? [],
    runs: runs.data?.runs ?? [],
    runsLoaded: runs.data != null,
    now: runs.data?.now ?? 0,
    ready,
    failed,
    retry,
  }
}

export type StoryData = ReturnType<typeof useStoryData>

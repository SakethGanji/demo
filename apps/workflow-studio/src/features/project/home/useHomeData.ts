/**
 * Data for the Projects home that the list endpoint doesn't carry. Everything is derived from existing
 * endpoints and shares query keys with the pages it links to, so opening a project afterwards is warm:
 *  - 24h activity: 12 two-hour buckets from `runsApi.list` (newest 200 runs), only for projects with runs in 24h.
 *  - missing secrets: `secretsApi.missing` in the live environment (names the key, which the list doesn't).
 *  - apps: `listApps`, to say how many are published.
 */
import { useMemo } from 'react'
import { useQueries } from '@tanstack/react-query'
import { projectsApi, runsApi, secretsApi } from '@/shared/api/endpoints'
import { qk } from '@/shared/api/queryKeys'
import type { ProjectListItem, RunListItem } from '@/shared/api/types'

export const BUCKETS = 12
const BUCKET_MS = 2 * 60 * 60 * 1000
const RUN_LIMIT = 200
const MAX_ACTIVITY_PROJECTS = 8

export function bucketRuns(runs: Pick<RunListItem, 'start_time'>[], now = Date.now()): number[] {
  const out = Array<number>(BUCKETS).fill(0)
  for (const r of runs) {
    const t = new Date(r.start_time.endsWith('Z') || /[+-]\d\d:?\d\d$/.test(r.start_time) ? r.start_time : `${r.start_time}Z`).getTime()
    const age = now - t
    if (!Number.isFinite(age) || age < 0 || age >= BUCKETS * BUCKET_MS) continue
    out[BUCKETS - 1 - Math.floor(age / BUCKET_MS)]++
  }
  return out
}

export const isDefaultProject = (p: Pick<ProjectListItem, 'id'>) => p.id === 'proj_default'

/** 24h buckets per project id (shares the runs cache key family `['project', id, 'runs', …]`). */
export function useActivityBuckets(projects: ProjectListItem[]) {
  const active = projects.filter((p) => p.health.runs_24h > 0).slice(0, MAX_ACTIVITY_PROJECTS)
  const results = useQueries({
    queries: active.map((p) => ({
      queryKey: ['project', p.id, 'home-activity'] as const,
      queryFn: () => runsApi.list(p.id, { limit: RUN_LIMIT }),
      staleTime: 30_000,
    })),
  })
  const sig = results.map((r) => r.dataUpdatedAt).join(',')
  return useMemo(() => {
    const byId: Record<string, number[]> = {}
    active.forEach((p, i) => {
      const d = results[i]?.data
      if (d) byId[p.id] = bucketRuns(d.runs)
    })
    const total = Array<number>(BUCKETS).fill(0)
    for (const b of Object.values(byId)) b.forEach((n, i) => (total[i] += n))
    return { byId, total, loading: results.some((r) => r.isLoading) }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sig, active.map((p) => p.id).join(',')])
}

export interface MissingInfo {
  project: ProjectListItem
  key: string
  env: string
}

/** The first missing secret of each project (live environment), by project id. */
export function useMissingByProject(projects: ProjectListItem[]) {
  const real = projects.filter((p) => !isDefaultProject(p))
  const results = useQueries({
    queries: real.map((p) => ({
      queryKey: qk.missing(p.id, p.live_environment),
      queryFn: () => secretsApi.missing(p.id, p.live_environment),
      staleTime: 30_000,
    })),
  })
  const sig = results.map((r) => r.dataUpdatedAt).join(',')
  return useMemo(() => {
    const byId: Record<string, MissingInfo & { count: number }> = {}
    real.forEach((p, i) => {
      const list = results[i]?.data ?? []
      if (list.length) byId[p.id] = { project: p, key: list[0].key, env: p.live_environment, count: list.length }
    })
    return byId
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sig, real.map((p) => p.id).join(',')])
}

/** { published, draft } across every project's apps. */
export function useAppCounts(projects: ProjectListItem[]) {
  const withApps = projects.filter((p) => p.counts.apps > 0)
  const results = useQueries({
    queries: withApps.map((p) => ({
      queryKey: qk.apps(p.id),
      queryFn: () => projectsApi.listApps(p.id),
      staleTime: 30_000,
    })),
  })
  let published = 0
  let loaded = 0
  results.forEach((r) => {
    if (!r.data) return
    loaded += r.data.length
    published += r.data.filter((a) => !!a.published_at).length
  })
  return { published, draft: Math.max(0, loaded - published), loaded }
}

export function greeting(d = new Date()) {
  const h = d.getHours()
  return h < 12 ? 'Good morning' : h < 18 ? 'Good afternoon' : 'Good evening'
}

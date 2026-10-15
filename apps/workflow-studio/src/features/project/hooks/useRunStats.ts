/**
 * Client-side run statistics for the Overview (last call and p50 per route, a 24h sparkline).
 * Computed from the newest 100 runs (the engine keeps 100 per project, §3.6).
 */
import { useQuery } from '@tanstack/react-query'
import { runsApi } from '@/shared/api/endpoints'
import { qk } from '@/shared/api/queryKeys'
import type { RunListItem } from '@/shared/api/types'
import { parseTs } from '../lib/format'

export interface RouteStat {
  last: RunListItem
  p50: number | null
  calls24h: number
}

function median(xs: number[]): number | null {
  if (!xs.length) return null
  const s = [...xs].sort((a, b) => a - b)
  const m = Math.floor(s.length / 2)
  return s.length % 2 ? s[m] : (s[m - 1] + s[m]) / 2
}

export function useRunStats(pid: string) {
  return useQuery({
    queryKey: qk.runs(pid, { stats: 100 }),
    queryFn: () => runsApi.list(pid, { limit: 100 }),
    select: ({ runs }) => {
      const now = Date.now()
      const dayAgo = now - 24 * 3600 * 1000
      const recent = runs.filter((r) => (parseTs(r.start_time)?.getTime() ?? 0) >= dayAgo)
      const byRoute = new Map<string, RunListItem[]>()
      for (const r of recent) {
        if (!r.route_id) continue
        byRoute.set(r.route_id, [...(byRoute.get(r.route_id) ?? []), r])
      }
      const routes = new Map<string, RouteStat>()
      for (const [id, rs] of byRoute) {
        routes.set(id, {
          last: rs[0],
          p50: median(rs.map((r) => r.duration_ms).filter((d): d is number => d != null)),
          calls24h: rs.length,
        })
      }
      const buckets = new Array(12).fill(0) as number[]
      for (const r of recent) {
        const t = parseTs(r.start_time)?.getTime() ?? 0
        const idx = Math.min(11, Math.floor((t - dayAgo) / (2 * 3600 * 1000)))
        if (idx >= 0) buckets[idx]++
      }
      return {
        routes,
        sparkline: buckets,
        p50: median(recent.map((r) => r.duration_ms).filter((d): d is number => d != null)),
        recent: runs.slice(0, 4),
      }
    },
  })
}

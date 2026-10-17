/**
 * Client-side run statistics for the Overview (last call and p50 per route, a 24h sparkline).
 * Computed from the newest 100 runs (the engine keeps 100 per project, §3.6).
 */
import { useQuery } from '@tanstack/react-query'
import { runsApi } from '@/shared/api/endpoints'
import { qk } from '@/shared/api/queryKeys'
import type { RunListItem } from '@/shared/api/types'
import type { ActivityDay } from '@/shared/components/ui/charts'
import { runTone } from '@/shared/components/ui/status-dot'
import { parseTs } from '../lib/format'

const pad = (n: number) => String(n).padStart(2, '0')
const dayKey = (d: Date) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`

/** The last 30 calendar days (oldest first, ending today) counted from the runs the engine still holds. */
function activityDays(runs: RunListItem[], now: number): ActivityDay[] {
  const days = new Map<string, ActivityDay>()
  const today = new Date(now)
  for (let i = 29; i >= 0; i--) {
    const d = new Date(today.getFullYear(), today.getMonth(), today.getDate() - i)
    days.set(dayKey(d), { date: dayKey(d), count: 0, failed: 0 })
  }
  for (const r of runs) {
    const t = parseTs(r.start_time)
    const day = t && days.get(dayKey(t))
    if (!day) continue
    day.count++
    if (runTone(r.status) === 'err') day.failed++
  }
  return [...days.values()]
}

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
    // new runs light up the system diagram: poll gently while the Overview is open
    refetchInterval: 8_000,
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
        recent: runs.slice(0, 5),
        /** the newest failed run, e.g. "POST /refunds" (for a tooltip) */
        recentFailed: ((): string | undefined => {
          const f = runs.find((r) => runTone(r.status) === 'err')
          return f ? (f.route ?? f.workflow_name) : undefined
        })(),
        /** newest runs (id + route), for one packet per new run */
        newest: runs.slice(0, 20).map((r) => ({ id: r.id, route_id: r.route_id, failed: runTone(r.status) === 'err' })),
        /** per-day counts for the 30-day grid; only as deep as the runs the engine keeps (about 100) */
        days: activityDays(runs, now),
        failed30: runs.filter((r) => runTone(r.status) === 'err' && (parseTs(r.start_time)?.getTime() ?? 0) >= now - 30 * 864e5).length,
        /** how many runs the 30-day grid is counted from */
        sampled: runs.length,
      }
    },
  })
}

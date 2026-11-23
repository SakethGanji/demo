/**
 * Numbers for the Runs header, derived from the runs the page has loaded (the engine keeps the newest 100):
 * the last 24 hours in 24 hourly buckets, the failed count and the median duration.
 */
import { parseTs } from '@/features/project/lib/format'
import type { RunListItem } from '@/shared/api/types'

export const HOUR = 3_600_000

export const RANGES = [
  { key: '1h', label: 'Last hour', ms: HOUR },
  { key: '24h', label: 'Last 24h', ms: 24 * HOUR },
  { key: '7d', label: 'Last 7 days', ms: 7 * 24 * HOUR },
  { key: 'all', label: 'All time', ms: Infinity },
] as const
export type RangeKey = (typeof RANGES)[number]['key']

export const isFailed = (status: string | null | undefined) =>
  status === 'failed' || status === 'error' || status === 'crashed' || status === 'canceled'

export const startMs = (r: RunListItem) => parseTs(r.start_time)?.getTime() ?? 0

export interface HourBucket {
  /** window start / end (ms epoch) */
  from: number
  to: number
  count: number
  failed: number
}

export interface DayStats {
  buckets: HourBucket[]
  count: number
  failed: number
  p50: number | null
  /** the most recent failed run in the window */
  lastFailed: RunListItem | null
  /** the window reaches back past the oldest loaded run, so older runs may be missing */
  capped: boolean
}

export function dayStats(rows: RunListItem[], now: number, hasMore: boolean): DayStats {
  const buckets: HourBucket[] = Array.from({ length: 24 }, (_, i) => ({
    from: now - (24 - i) * HOUR,
    to: now - (23 - i) * HOUR,
    count: 0,
    failed: 0,
  }))
  const durations: number[] = []
  let failed = 0
  let count = 0
  let lastFailed: RunListItem | null = null
  let oldest = Infinity
  for (const r of rows) {
    const t = startMs(r)
    oldest = Math.min(oldest, t)
    const age = now - t
    if (age < 0 || age >= 24 * HOUR) continue
    const b = buckets[23 - Math.floor(age / HOUR)]
    b.count++
    count++
    if (isFailed(r.status)) {
      b.failed++
      failed++
      if (!lastFailed || t > startMs(lastFailed)) lastFailed = r
    }
    if (r.duration_ms != null) durations.push(r.duration_ms)
  }
  durations.sort((a, b) => a - b)
  const p50 = durations.length ? durations[Math.floor((durations.length - 1) / 2)] : null
  return { buckets, count, failed, p50, lastFailed, capped: hasMore || (rows.length >= 100 && oldest > now - 24 * HOUR) }
}

/** Catmull-Rom path through points */
export function smoothPath(P: [number, number][]): string {
  if (!P.length) return ''
  let d = `M${P[0][0]} ${P[0][1].toFixed(1)}`
  for (let i = 0; i < P.length - 1; i++) {
    const p0 = P[Math.max(0, i - 1)]
    const p1 = P[i]
    const p2 = P[i + 1]
    const p3 = P[Math.min(P.length - 1, i + 2)]
    d += ` C${(p1[0] + (p2[0] - p0[0]) / 6).toFixed(1)} ${(p1[1] + (p2[1] - p0[1]) / 6).toFixed(1)}, ${(p2[0] - (p3[0] - p1[0]) / 6).toFixed(1)} ${(p2[1] - (p3[1] - p1[1]) / 6).toFixed(1)}, ${p2[0]} ${p2[1].toFixed(1)}`
  }
  return d
}

/** `Oct 15, 09:23:10` in the viewer's zone */
export function exactTime(ts: string | null | undefined): string {
  const d = parseTs(ts)
  if (!d) return '—'
  return d.toLocaleString('en-US', { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false })
}

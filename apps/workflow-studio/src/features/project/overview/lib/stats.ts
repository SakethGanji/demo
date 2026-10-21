/**
 * The arithmetic behind the Overview's statistics band. Pure, so it is testable without a browser.
 *
 * Two sources, two windows, always labelled:
 *   - the 14-day volume comes from the engine's per-day aggregate (`…/runs/daily`);
 *   - everything per run (durations, success over time, per-item activity) comes from the newest 100 runs. When those
 *     100 do not reach back 24 hours, the window starts at the oldest of them and says so (`truncated`).
 */
import type { ModuleSchedule, RunListItem } from '@/shared/api/types'
import { DAY_MS, entityKey, toneOf, type ProjectEntity } from '@/features/project/lib/entities'
import { parseTs } from '../../lib/format'

export const RUN_CAP = 100

const at = (r: RunListItem) => parseTs(r.start_time)?.getTime() ?? 0

/** Linear-interpolated percentile (q in 0..1); null for no data. */
export function percentile(xs: number[], q: number): number | null {
  if (!xs.length) return null
  const s = [...xs].sort((a, b) => a - b)
  const i = (s.length - 1) * q
  const lo = Math.floor(i)
  const hi = Math.ceil(i)
  return s[lo] + (s[hi] - s[lo]) * (i - lo)
}

/** The smallest 1, 2 or 5 × 10^k at or above `v` (an axis top that reads as a round number). */
export function niceCeil(v: number): number {
  if (v <= 0) return 1
  const p = 10 ** Math.floor(Math.log10(v))
  for (const m of [1, 2, 5, 10]) if (m * p >= v) return m * p
  return 10 * p
}

export interface RunWindow {
  from: number
  to: number
  /** the newest 100 runs do not reach back a full day: the window starts at the oldest of them */
  truncated: boolean
}

export function recentWindow(runs: RunListItem[], now: number): RunWindow {
  const dayAgo = now - DAY_MS
  const oldest = runs.reduce((m, r) => Math.min(m, at(r) || m), now)
  const truncated = runs.length >= RUN_CAP && oldest > dayAgo
  return { from: truncated ? oldest : dayAgo, to: now, truncated }
}

export interface Bucket {
  from: number
  to: number
  total: number
  failed: number
}

/** `n` equal buckets over the window: run and failure counts per bucket. */
export function bucketRuns(runs: RunListItem[], w: RunWindow, n: number): Bucket[] {
  const span = Math.max(1, w.to - w.from)
  const out: Bucket[] = Array.from({ length: n }, (_, i) => ({ from: w.from + (span * i) / n, to: w.from + (span * (i + 1)) / n, total: 0, failed: 0 }))
  for (const r of runs) {
    const t = at(r)
    if (t < w.from || t > w.to) continue
    const i = Math.min(n - 1, Math.floor(((t - w.from) / span) * n))
    out[i].total++
    if (toneOf(r.status) === 'err') out[i].failed++
  }
  return out
}

/** Durations (ms) of the finished runs in the window. */
export function durations(runs: RunListItem[], w: RunWindow): number[] {
  return runs.filter((r) => r.duration_ms != null && toneOf(r.status) !== 'running' && at(r) >= w.from).map((r) => r.duration_ms as number)
}

export interface HeatRow {
  key: string
  label: string
  kind: 'workflow' | 'app' | 'other'
  id?: string
  cells: Bucket[]
  total: number
  failed: number
}

/**
 * Items × time: one row per app or workflow that ran in the window (busiest first, then by name), and one
 * "Other" row for runs no item owns (backend routes called directly, scheduled functions). `limit` rows at most;
 * `hidden` counts the active items left out.
 */
export function heatRows(entities: ProjectEntity[], runs: RunListItem[], w: RunWindow, n: number, limit: number): { rows: HeatRow[]; hidden: number } {
  const byKey = new Map(entities.map((e) => [entityKey(e.kind, e.id), e]))
  const owned = new Map<string, RunListItem[]>()
  const other: RunListItem[] = []
  for (const r of runs) {
    if (at(r) < w.from) continue
    const keys = [entityKey('workflow', r.workflow_id), ...(r.app_id ? [entityKey('app', r.app_id)] : [])].filter((k) => byKey.has(k))
    if (keys.length === 0) other.push(r)
    for (const k of keys) owned.set(k, [...(owned.get(k) ?? []), r])
  }
  const row = (key: string, label: string, kind: HeatRow['kind'], rs: RunListItem[], id?: string): HeatRow => {
    const cells = bucketRuns(rs, w, n)
    return { key, label, kind, id, cells, total: rs.length, failed: cells.reduce((s, c) => s + c.failed, 0) }
  }
  const items = [...owned.entries()]
    .map(([k, rs]) => {
      const e = byKey.get(k)!
      return row(k, e.name, e.kind, rs, e.id)
    })
    .sort((a, b) => b.total - a.total || a.label.localeCompare(b.label, undefined, { sensitivity: 'base', numeric: true }))
  const room = other.length ? limit - 1 : limit
  const rows = items.slice(0, room)
  if (other.length) rows.push(row('other', 'Routes & schedules', 'other', other))
  return { rows, hidden: Math.max(0, items.length - room) }
}

/** Upcoming runs of a schedule within `horizon` ms: exact for `every`, only the next one for a cron. `dense` when
 * there are more than `max` (drawn as a band, not ticks). */
export function scheduleTicks(s: ModuleSchedule, now: number, horizon: number, max = 60): { ticks: number[]; dense: boolean } {
  const next = parseTs(s.next_run_at)?.getTime()
  if (!s.enabled || next == null) return { ticks: [], dense: false }
  const first = Math.max(next, now)
  if (!s.every_seconds) return { ticks: first <= now + horizon ? [first] : [], dense: false }
  const step = s.every_seconds * 1000
  const count = Math.floor((now + horizon - first) / step) + 1
  if (count > max) return { ticks: [first], dense: true }
  return { ticks: Array.from({ length: Math.max(0, count) }, (_, i) => first + i * step), dense: false }
}

/** "every 30 s", "every 5 min", "every 2 h", or the cron text. */
export function cadence(s: ModuleSchedule): string {
  if (s.every_seconds) {
    const v = s.every_seconds
    if (v < 120) return `every ${v} s`
    if (v < 7200) return `every ${Math.round(v / 60)} min`
    return `every ${Math.round(v / 3600)} h`
  }
  return s.cron ?? 'unscheduled'
}

/** Powers of ten that fall inside [lo, hi] (the log axis's gridlines). */
export function decadeTicks(lo: number, hi: number): number[] {
  const out: number[] = []
  for (let p = 10 ** Math.ceil(Math.log10(Math.max(1, lo))); p <= hi; p *= 10) out.push(p)
  return out
}

export interface DurationBin {
  lo: number
  hi: number
  n: number
}

/** Durations as a histogram on a log scale: `count` equal-width bins (in log space) from the fastest run to the
 * slowest. A narrow spread is widened to ±0.15 decades so one value still draws as a peak, not a wall. */
export function logBins(ds: number[], count = 14): DurationBin[] {
  if (!ds.length) return []
  const lg = (v: number) => Math.log10(Math.max(1, v))
  let a = lg(Math.min(...ds))
  let b = lg(Math.max(...ds))
  if (b - a < 0.3) {
    const m = (a + b) / 2
    a = m - 0.15
    b = m + 0.15
  }
  const w = (b - a) / count
  const bins: DurationBin[] = Array.from({ length: count }, (_, i) => ({ lo: 10 ** (a + i * w), hi: 10 ** (a + (i + 1) * w), n: 0 }))
  for (const d of ds) bins[Math.max(0, Math.min(count - 1, Math.floor((lg(d) - a) / w)))].n++
  return bins
}

/** Runs and failures in the newest `n` days against the `n` before them (from the per-day aggregate). */
export function periodTrend(days: { count: number; failed: number }[], n = 7) {
  const sum = (xs: typeof days) => xs.reduce((s, d) => ({ runs: s.runs + d.count, failed: s.failed + d.failed }), { runs: 0, failed: 0 })
  const cur = sum(days.slice(-n))
  const prev = sum(days.slice(-2 * n, -n))
  return { runs: cur.runs, failed: cur.failed, prevRuns: prev.runs, prevFailed: prev.failed, hasPrev: days.length >= 2 * n }
}

/** Whether a run chart should be drawn per hour (every run of the last `n` days fell in the newest two of them, so
 * per-day bars would be one spike) or per day. 'none' when nothing ran. */
export function chartScale(days: { count: number }[], n = 14): 'hour' | 'day' | 'none' {
  const last = days.slice(-n)
  const active = last.map((d, i) => (d.count > 0 ? i : -1)).filter((i) => i >= 0)
  if (!active.length) return 'none'
  return active[0] >= last.length - 2 ? 'hour' : 'day'
}

/** A home-screen widget size: s 1×1 · m 2×1 · l 2×2. */
export type WidgetSize = 's' | 'm' | 'l'

/** How big "Needs attention" is for this many items: small when clear, medium for one or two, large past that. */
export const attentionSize = (n: number): WidgetSize => (n === 0 ? 's' : n <= 2 ? 'm' : 'l')

/** A short duration label for a log axis: 10ms, 1s, 1m. */
export function shortMs(ms: number): string {
  if (ms < 1000) return `${Math.round(ms)}ms`
  if (ms < 60_000) return `${+(ms / 1000).toFixed(1)}s`
  return `${+(ms / 60_000).toFixed(1)}m`
}

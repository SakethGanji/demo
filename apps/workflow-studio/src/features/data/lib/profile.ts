/** Column profiles for the row under the table header, computed from the loaded rows only. */
import type { Column, Row } from '@/shared/api/types'
import { isNumeric } from './cells'

export type Profile =
  | { kind: 'unique'; caption: string }
  | { kind: 'bar'; segments: { label: string; count: number }[]; caption: string }
  | { kind: 'hist'; bins: number[]; ranges: string[]; caption: string }
  | { kind: 'ticks'; marks: number[]; caption: string; tip: string }
  | { kind: 'none'; caption: string }

export const fmtNum = (n: number) => String(Math.round(n * 100) / 100)

const MD = { month: 'short', day: 'numeric', timeZone: 'UTC' } as const
const parse = (s: unknown) => {
  const t = typeof s === 'string' ? Date.parse(/Z|[+-]\d\d:?\d\d$/.test(s) ? s : `${s}Z`) : NaN
  return Number.isNaN(t) ? null : t
}

export function profileIds(rows: Row[]): Profile {
  return { kind: 'unique', caption: `${new Set(rows.map((r) => r.id)).size} unique` }
}

export function profileColumn(col: Column, rows: Row[]): Profile {
  const vals = rows.map((r) => r.data[col.name]).filter((v) => v !== null && v !== undefined && v !== '')
  if (!vals.length) return { kind: 'none', caption: rows.length ? 'no values' : '—' }
  if (isNumeric(col.type)) {
    const nums = vals.map(Number).filter(Number.isFinite)
    if (!nums.length) return { kind: 'none', caption: 'no values' }
    const lo = Math.min(...nums)
    const hi = Math.max(...nums)
    const bins = [0, 0, 0, 0, 0]
    for (const n of nums) bins[hi === lo ? 0 : Math.min(4, Math.floor(((n - lo) / (hi - lo)) * 5))]++
    const ranges = bins.map((c, i) => `${fmtNum(lo + ((hi - lo) / 5) * i)}–${fmtNum(lo + ((hi - lo) / 5) * (i + 1))}: ${c}`)
    return { kind: 'hist', bins, ranges, caption: lo === hi ? fmtNum(lo) : `${fmtNum(lo)} – ${fmtNum(hi)}` }
  }
  if (col.type === 'datetime') {
    const ts = vals.map(parse).filter((x): x is number => x != null)
    if (!ts.length) return { kind: 'none', caption: 'no values' }
    return ticksFor(ts, 'values')
  }
  if (col.type === 'json') return { kind: 'none', caption: `${vals.length} value${vals.length === 1 ? '' : 's'}` }
  const cnt = new Map<string, number>()
  for (const v of vals) {
    const k = typeof v === 'string' ? v : JSON.stringify(v)
    cnt.set(k, (cnt.get(k) ?? 0) + 1)
  }
  const order = [...cnt].sort((a, b) => b[1] - a[1])
  if (col.type !== 'boolean' && (order.length === vals.length && vals.length > 3)) return { kind: 'unique', caption: `${order.length} unique` }
  if (order.length > 12) return { kind: 'unique', caption: `${order.length} unique` }
  const top = order[0]
  return {
    kind: 'bar',
    segments: order.map(([label, count]) => ({ label, count })),
    caption: `${order.length} value${order.length === 1 ? '' : 's'} · ${top[0]} ${Math.round((top[1] / vals.length) * 100)}%`,
  }
}

/** created_at: ticks on the 24h strip when every row is from one UTC day, else across the span. */
export function profileCreated(rows: Row[], now = Date.now()): Profile {
  const ts = rows.map((r) => parse(r.created_at)).filter((x): x is number => x != null)
  if (!ts.length) return { kind: 'none', caption: '—' }
  return ticksFor(ts, 'rows', now)
}

function ticksFor(ts: number[], noun: string, now = Date.now()): Profile {
  const day = (t: number) => new Date(t).toISOString().slice(0, 10)
  const first = day(ts[0])
  if (ts.every((t) => day(t) === first)) {
    const today = first === day(now)
    const label = new Date(ts[0]).toLocaleDateString('en-US', MD)
    return {
      kind: 'ticks',
      marks: ts.map((t) => (t % 86_400_000) / 86_400_000),
      caption: today ? 'all today' : `all ${label}`,
      tip: `All ${ts.length} ${noun} ${today ? 'today' : label} (UTC)`,
    }
  }
  const lo = Math.min(...ts)
  const hi = Math.max(...ts)
  const span = Math.max(1, hi - lo)
  return {
    kind: 'ticks',
    marks: ts.map((t) => (t - lo) / span),
    caption: `${new Date(lo).toLocaleDateString('en-US', MD)} – ${new Date(hi).toLocaleDateString('en-US', MD)}`,
    tip: `${ts.length} ${noun}, ${new Date(lo).toLocaleDateString('en-US', MD)} to ${new Date(hi).toLocaleDateString('en-US', MD)} (UTC)`,
  }
}

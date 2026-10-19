/**
 * The Coverage and Memory views' pure parts: matrix columns and heat, row order, and the learn-event timeline
 * grouped by day.
 */
import { parseTs } from '@/features/project/lib/format'
import type { KgLearnEvent, KgMatrixRow } from './types'

/** Capability subkinds that appear in any row, the most used first (ties by name). */
export function coverageColumns(rows: Pick<KgMatrixRow, 'implemented_by'>[]): string[] {
  const m = new Map<string, number>()
  for (const r of rows) for (const [k, v] of Object.entries(r.implemented_by)) m.set(k, (m.get(k) ?? 0) + v)
  return [...m].sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0])).map(([k]) => k)
}

/** 0 (none) .. 4 (the column's best) on a square-root scale, so one implementer already shows. */
export function heatLevel(count: number, max: number): 0 | 1 | 2 | 3 | 4 {
  if (count <= 0 || max <= 0) return 0
  return Math.max(1, Math.min(4, Math.ceil(Math.sqrt(count / max) * 4))) as 1 | 2 | 3 | 4
}

export type CoverageSort = 'gaps' | 'coverage' | 'name'

/** Gaps first (then the thinnest coverage), best covered first, or A to Z. Never mutates `rows`. */
export function sortRows(rows: KgMatrixRow[], by: CoverageSort): KgMatrixRow[] {
  const name = (a: KgMatrixRow, b: KgMatrixRow) => a.concept.label.localeCompare(b.concept.label)
  const out = [...rows]
  if (by === 'name') return out.sort(name)
  if (by === 'coverage') return out.sort((a, b) => b.implemented_total - a.implemented_total || name(a, b))
  return out.sort((a, b) => Number(b.gap) - Number(a.gap) || a.implemented_total - b.implemented_total || name(a, b))
}

/** The engine's LEARN_TRIGGERS. */
const TRIGGERS: Record<string, string> = {
  seed: 'Seeded',
  rebuild: 'Rebuilt',
  kept_workflow: 'Kept a workflow',
  kept_app: 'Kept an app',
  human: 'Edited by a person',
}

/** `kept_workflow` -> "Kept a workflow"; unknown triggers are de-snaked. */
export function triggerLabel(t: string): string {
  if (TRIGGERS[t]) return TRIGGERS[t]
  const s = t.replaceAll('_', ' ').trim()
  return s ? s[0].toUpperCase() + s.slice(1) : 'Event'
}

/** Concept keys an event mentions (`payload.concepts`), deduplicated. */
export function eventConcepts(e: Pick<KgLearnEvent, 'payload'>): string[] {
  const c = e.payload?.concepts
  return Array.isArray(c) ? [...new Set(c.filter((x): x is string => typeof x === 'string'))] : []
}

/** Day buckets, newest first as the API sends them: "Today", "Yesterday", else e.g. "Mon 12 Oct". */
export function groupByDay(events: KgLearnEvent[], now = Date.now()): { day: string; items: KgLearnEvent[] }[] {
  const out: { day: string; items: KgLearnEvent[] }[] = []
  const today = startOfDay(now)
  for (const e of events) {
    const d = parseTs(e.at)
    const label = !d
      ? 'Unknown date'
      : (() => {
          const diff = Math.round((today - startOfDay(d.getTime())) / 86_400_000)
          if (diff === 0) return 'Today'
          if (diff === 1) return 'Yesterday'
          return d.toLocaleDateString(undefined, { weekday: 'short', day: 'numeric', month: 'short' })
        })()
    const last = out[out.length - 1]
    if (last && last.day === label) last.items.push(e)
    else out.push({ day: label, items: [e] })
  }
  return out
}

function startOfDay(ms: number): number {
  const d = new Date(ms)
  d.setHours(0, 0, 0, 0)
  return d.getTime()
}

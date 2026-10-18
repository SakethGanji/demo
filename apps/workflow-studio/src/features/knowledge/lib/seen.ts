/**
 * "Since your last visit" (PLAN-CAPGRAPH-UI §2.1): a per-project snapshot in localStorage and a pure diff against it.
 * Storage may be missing or blocked; every read and write is wrapped, and the page is correct without it.
 */
import type { MatrixTotals } from '../types'
import { count } from './labels'

export interface Seen {
  totals: MatrixTotals
  lastMemoryId: number
  /** concept keys that were gaps at the time */
  gapKeys: string[]
}

const key = (pid: string) => `kg:seen:${pid}`

export function readSeen(pid: string): Seen | null {
  try {
    const raw = localStorage.getItem(key(pid))
    if (!raw) return null
    const v = JSON.parse(raw) as Partial<Seen>
    if (!v || typeof v !== 'object' || !v.totals || typeof v.lastMemoryId !== 'number' || !Array.isArray(v.gapKeys)) return null
    return v as Seen
  } catch {
    return null
  }
}

export function writeSeen(pid: string, s: Seen): void {
  try {
    localStorage.setItem(key(pid), JSON.stringify(s))
  } catch {
    /* private window or blocked storage: the chip simply will not show next time */
  }
}

export interface SeenDiff {
  /** the largest change, as a short phrase; null when nothing changed */
  text: string | null
  /** concept keys that were gaps and are not any more */
  closedGaps: string[]
  newMemories: number
  /** per hero stat: did it change since the snapshot */
  changed: Partial<Record<keyof MatrixTotals, boolean>>
}

export function diffSeen(
  prev: Seen | null,
  now: { totals: MatrixTotals; memoryIds: readonly number[]; gapKeys: string[] },
  labelOf: (conceptKey: string) => string = (k) => k,
): SeenDiff | null {
  if (!prev) return null
  const closedGaps = prev.gapKeys.filter((k) => !now.gapKeys.includes(k))
  const newMemories = now.memoryIds.filter((id) => id > prev.lastMemoryId).length
  const changed: SeenDiff['changed'] = {}
  for (const k of Object.keys(now.totals) as (keyof MatrixTotals)[]) changed[k] = now.totals[k] !== prev.totals[k]
  const dPatterns = now.totals.patterns - prev.totals.patterns
  const dApps = now.totals.apps - prev.totals.apps
  let text: string | null = null
  if (closedGaps.length === 1) text = `${labelOf(closedGaps[0])} is no longer a gap`
  else if (closedGaps.length > 1) text = `${count(closedGaps.length, 'gap')} closed`
  else if (dPatterns > 0) text = `+${count(dPatterns, 'pattern')}`
  else if (dApps > 0) text = `+${count(dApps, 'app')}`
  else if (newMemories > 0) text = `${count(newMemories, 'new memory', 'new memories')}`
  else if (now.totals.validated > prev.totals.validated) text = `+${count(now.totals.validated - prev.totals.validated, 'validation')}`
  if (!text && !closedGaps.length && !newMemories) return { text: null, closedGaps, newMemories, changed }
  return { text, closedGaps, newMemories, changed }
}

/**
 * Run polling (DESIGN-STAGE5-UIS §6.1): every 0.5 s for the first 5 s, then every 2 s, then every 10 s after
 * 2 minutes. Terminal statuses stop it; `waiting` keeps polling (an approval can take days) at the slow rate.
 * Chat (§6.2): every 1 s while busy, else every 10 s while visible.
 */
import type { RunStatus, RunView } from './types'

export const POLL_FAST_MS = 500
export const POLL_MID_MS = 2000
export const POLL_SLOW_MS = 10_000
export const FAST_WINDOW_MS = 5000
export const MID_WINDOW_MS = 120_000

export function pollDelayMs(elapsedMs: number): number {
  if (elapsedMs < FAST_WINDOW_MS) return POLL_FAST_MS
  if (elapsedMs < MID_WINDOW_MS) return POLL_MID_MS
  return POLL_SLOW_MS
}

export function isTerminal(status: RunStatus | string | undefined): boolean {
  return status === 'success' || status === 'failed' || status === 'cancelled'
}

/** `refetchInterval` value for react-query: `false` stops polling. */
export function runRefetchInterval(view: RunView | undefined, elapsedMs: number): number | false {
  if (view && isTerminal(view.status)) return false
  return pollDelayMs(elapsedMs)
}

export function chatRefetchInterval(busy: boolean | undefined): number {
  return busy ? 1000 : 10_000
}

/** One-line headline for the run page. */
export function runHeadline(view: RunView | undefined): string {
  if (!view) return 'Starting…'
  switch (view.status) {
    case 'running':
      return view.progress?.current ? `Running · ${view.progress.current}` : 'Running…'
    case 'waiting': {
      const t = view.waiting_for[0]?.title
      return t ? `Waiting for ${t}` : 'Waiting for approval'
    }
    case 'success':
      return view.message ?? 'Done'
    case 'failed':
      return 'Something went wrong'
    case 'cancelled':
      return 'Cancelled'
  }
}

export function progressPercent(view: RunView | undefined): number | null {
  const p = view?.progress
  if (!p || !p.total) return null
  return Math.max(0, Math.min(100, Math.round((p.done / p.total) * 100)))
}

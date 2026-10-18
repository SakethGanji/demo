/** One unit format (UI-DESIGN §1.3): `331 ms`, `2m ago`. */
export function formatMs(ms: number | null | undefined): string {
  if (ms == null || Number.isNaN(ms)) return '—'
  if (ms >= 10_000) return `${(ms / 1000).toFixed(1)} s`
  return `${Math.round(ms)} ms`
}

/**
 * Engine timestamps are naive ISO strings in the engine host's local time (`datetime.now()`);
 * zone-less strings parse as browser-local time, which matches for a local studio.
 */
export function parseTs(ts: string | null | undefined): Date | null {
  if (!ts) return null
  const d = new Date(ts)
  return Number.isNaN(d.getTime()) ? null : d
}

export function timeAgo(ts: string | null | undefined, now = Date.now()): string {
  const d = parseTs(ts)
  if (!d) return '—'
  const s = Math.max(0, Math.round((now - d.getTime()) / 1000))
  if (s < 45) return 'now'
  const m = Math.round(s / 60)
  if (m < 60) return `${m}m ago`
  const h = Math.round(m / 60)
  if (h < 24) return `${h}h ago`
  const days = Math.round(h / 24)
  if (days === 1) return 'yesterday'
  if (days < 7) return `${days}d ago`
  return d.toLocaleDateString(undefined, { month: 'short', day: 'numeric' })
}

export function shortAge(ts: string | null | undefined): string {
  return timeAgo(ts).replace(' ago', '')
}

export function pluralize(n: number, one: string, many = `${one}s`): string {
  return `${n} ${n === 1 ? one : many}`
}

/** Most recently updated first (ISO timestamps sort as strings). */
export function byRecent<T extends { updated_at: string }>(xs: T[]): T[] {
  return [...xs].sort((a, b) => b.updated_at.localeCompare(a.updated_at))
}

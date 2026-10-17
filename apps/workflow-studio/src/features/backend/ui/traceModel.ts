/** Shared reading of a run trace for the backend test panel (labels, upstream, timing offsets). */
import type { BrokerCall, RunTrace } from '@/shared/api/types'

const params = (c: BrokerCall) => (c.parameters ?? c.params_redacted ?? {}) as Record<string, unknown>

export function callLabel(c: BrokerCall): string {
  const p = params(c)
  if (c.node_type === 'DataTable') return `DataTable.${String(p.operation ?? 'call')}`
  return c.node_type
}

export function callDetail(c: BrokerCall): string {
  const p = params(c)
  if (c.node_type === 'HttpRequest') {
    const parts = [`${String(p.method ?? 'GET')} ${String(p.url ?? '')}`]
    if (p.headers) parts.push(JSON.stringify(p.headers))
    return parts.join(' · ')
  }
  if (c.node_type === 'DataTable') return `table ${String(p.table ?? '')}${p.data ? ` · ${JSON.stringify(p.data)}` : ''}`
  return JSON.stringify(p)
}

/** The upstream of an HttpRequest call, e.g. `:41759/v1/balance`; null for other nodes. */
export function callEndpoint(c: BrokerCall): string | null {
  if (c.node_type !== 'HttpRequest') return null
  const url = String(params(c).url ?? '')
  if (!url) return null
  try {
    const u = new URL(url)
    return `${u.port ? `:${u.port}` : u.host}${u.pathname === '/' ? '' : u.pathname}`
  } catch {
    return url
  }
}

/**
 * Start offset (ms) of each broker call inside the sandbox span. The trace records each call's duration and
 * `created_at`, not its start, so offsets come from the gaps between `created_at`s; if those are unusable
 * (missing, unordered) the calls are laid end to end.
 */
export function callOffsets(trace: RunTrace | null | undefined): number[] {
  const calls = trace?.broker_calls ?? []
  const span = trace?.sandbox?.duration_ms ?? Infinity
  const seq: number[] = []
  let acc = 0
  for (const c of calls) {
    seq.push(acc)
    acc += c.duration_ms ?? 0
  }
  const t = calls.map((c) => Date.parse(c.created_at))
  if (calls.length < 2 || t.some((x) => !Number.isFinite(x))) return seq
  const rel = t.map((x) => x - t[0])
  const ok = rel.every((x, i) => (i === 0 ? true : x >= rel[i - 1])) && rel.every((x, i) => x + (calls[i].duration_ms ?? 0) <= span + 1)
  return ok ? rel : seq
}

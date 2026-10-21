import type { Connection, ConnectionStatus, PlanSource, QueryResult, SchemaTable } from './connectionsTypes'
import type { DotTone } from '@/shared/components/ui/status-dot'

export const tableId = (t: Pick<SchemaTable, 'schema' | 'name'>) => (t.schema && t.schema !== 'public' ? `${t.schema}.${t.name}` : t.name)

export function statusTone(s: ConnectionStatus): DotTone {
  return s === 'ok' ? 'ok' : s === 'error' ? 'err' : 'idle'
}

export function hostLabel(c: Pick<Connection, 'host' | 'database'>): string {
  return [c.host, c.database].filter(Boolean).join(' / ') || 'host unknown'
}

export function canConnect(name: string, url: string): boolean {
  return name.trim().length > 0 && /^postgres(ql)?:\/\/\S+$/i.test(url.trim())
}

/** Picker state: connection key -> chosen table names. An empty list means every table of that connection. */
export type SourceSelection = Record<string, string[]>

export function toggleConnection(sel: SourceSelection, key: string): SourceSelection {
  if (key in sel) {
    const next = { ...sel }
    delete next[key]
    return next
  }
  return { ...sel, [key]: [] }
}

export function toggleTable(sel: SourceSelection, key: string, table: string): SourceSelection {
  const cur = sel[key] ?? []
  const next = cur.includes(table) ? cur.filter((t) => t !== table) : [...cur, table]
  return { ...sel, [key]: next }
}

/** The `sources` field of a plan draft request, or undefined when nothing is selected. */
export function buildSources(sel: SourceSelection, known?: Connection[]): PlanSource[] | undefined {
  const keys = Object.keys(sel).filter((k) => !known || known.some((c) => c.key === k))
  if (!keys.length) return undefined
  return keys.map((k) => (sel[k].length ? { connection: k, tables: sel[k] } : { connection: k }))
}

export function sourceChipLabel(s: PlanSource): string {
  if (!s.tables?.length) return `${s.connection}: all tables`
  return s.tables.length <= 2 ? `${s.connection}: ${s.tables.join(', ')}` : `${s.connection}: ${s.tables.length} tables`
}

export function showSourcesHint(connections: Connection[] | undefined, sel: SourceSelection): boolean {
  return !!connections?.length && Object.keys(sel).length === 0
}

/** The engine sends rows keyed by column; the grid reads them by position. Array rows pass through. */
export function rowsAsArrays(r: QueryResult): QueryResult {
  if (!Array.isArray(r?.rows)) return r
  const rows = (r.rows as unknown[]).map((row) =>
    Array.isArray(row) ? row : r.columns.map((c) => (row as Record<string, unknown> | null)?.[c]))
  return { ...r, rows }
}

/** Result rows as objects keyed by column (duplicate column names keep the last value). */
export function mapQueryRows(r: QueryResult): Record<string, unknown>[] {
  return r.rows.map((row) => Object.fromEntries(r.columns.map((c, i) => [c, row[i]])))
}

export function cellText(v: unknown): string {
  if (v === null || v === undefined) return 'NULL'
  if (typeof v === 'object') return JSON.stringify(v)
  return String(v)
}

export function rowEstimateLabel(n: number | null | undefined): string {
  if (n == null || n < 0) return '—'
  if (n >= 1_000_000) return `~${(n / 1_000_000).toFixed(1)}M rows`
  if (n >= 1000) return `~${Math.round(n / 1000)}k rows`
  return `${n} row${n === 1 ? '' : 's'}`
}

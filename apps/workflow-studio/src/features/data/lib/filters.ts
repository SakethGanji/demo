/**
 * The rows filter, in exactly the engine grammar (`data_table_service.py:3-4`):
 * `{"col": v}` or `{"col": {"$eq|$ne|$gt|$gte|$lt|$lte": v, "$in": [..], "$contains": "s", "$exists": bool}}`,
 * combined with top-level `$and` / `$or` arrays. The filter bar edits a flat list of clauses joined
 * by one of `$and` / `$or`; anything else in the URL is kept as a "custom" filter.
 */
import type { Column, ColumnType, RowFilter } from '@/shared/api/types'

export type FilterOp = '$eq' | '$ne' | '$gt' | '$gte' | '$lt' | '$lte' | '$in' | '$contains' | '$exists'
export type Join = '$and' | '$or'

export interface Clause {
  column: string
  op: FilterOp
  value: unknown
}

export interface FilterModel {
  join: Join
  clauses: Clause[]
  /** set when the URL filter can't be shown as clauses (kept verbatim) */
  raw: RowFilter | null
}

export const OP_LABEL: Record<FilterOp, string> = {
  $eq: '=',
  $ne: '≠',
  $gt: '>',
  $gte: '≥',
  $lt: '<',
  $lte: '≤',
  $in: 'in',
  $contains: 'contains',
  $exists: 'exists',
}

/** `id` is the integer row id (the only metadata column the engine filters on). */
export type FilterColumnType = ColumnType | 'id'

export function opsFor(t: FilterColumnType): FilterOp[] {
  switch (t) {
    case 'id':
      return ['$eq', '$ne', '$gt', '$gte', '$lt', '$lte', '$in']
    case 'number':
    case 'integer':
      return ['$eq', '$ne', '$gt', '$gte', '$lt', '$lte', '$in', '$exists']
    case 'string':
      return ['$contains', '$eq', '$ne', '$in', '$exists']
    case 'boolean':
      return ['$eq', '$ne', '$exists']
    case 'datetime':
      return ['$eq', '$gt', '$gte', '$lt', '$lte', '$exists']
    case 'json':
      return ['$contains', '$exists']
  }
}

export function columnType(columns: Column[], name: string): FilterColumnType {
  if (name === 'id') return 'id'
  return columns.find((c) => c.name === name)?.type ?? 'string'
}

/** Parse one typed scalar from text; unparseable numbers stay text so the engine's error shows. */
export function parseScalar(t: FilterColumnType, text: string): unknown {
  const s = text.trim()
  if (t === 'id') return /^-?\d+$/.test(s) ? Number(s) : s
  if (t === 'number' || t === 'integer') return s !== '' && Number.isFinite(Number(s)) ? Number(s) : s
  if (t === 'boolean') return s === 'true' ? true : s === 'false' ? false : s
  if (t === 'json') {
    try {
      return JSON.parse(s)
    } catch {
      return s
    }
  }
  return text
}

export function parseClauseValue(t: FilterColumnType, op: FilterOp, text: string): unknown {
  if (op === '$exists') return text !== 'false'
  if (op === '$contains') return text
  if (op === '$in')
    return text
      .split(',')
      .map((x) => x.trim())
      .filter(Boolean)
      .map((x) => parseScalar(t, x))
  return parseScalar(t, text)
}

export function formatValue(v: unknown): string {
  if (Array.isArray(v)) return v.map(formatValue).join(', ')
  if (typeof v === 'string') return v
  return JSON.stringify(v)
}

export function clauseLabel(c: Clause): string {
  if (c.op === '$exists') return c.value ? `${c.column} exists` : `${c.column} is absent`
  return `${c.column} ${OP_LABEL[c.op]} ${formatValue(c.value)}`
}

const OPS = new Set<string>(Object.keys(OP_LABEL))

function clausesOf(obj: unknown): Clause[] | null {
  if (!obj || typeof obj !== 'object' || Array.isArray(obj)) return null
  const out: Clause[] = []
  for (const [k, v] of Object.entries(obj as Record<string, unknown>)) {
    if (k.startsWith('$')) return null
    if (v && typeof v === 'object' && !Array.isArray(v) && Object.keys(v).length && Object.keys(v).every((x) => x.startsWith('$'))) {
      for (const [op, x] of Object.entries(v as Record<string, unknown>)) {
        if (!OPS.has(op)) return null
        out.push({ column: k, op: op as FilterOp, value: x })
      }
    } else out.push({ column: k, op: '$eq', value: v })
  }
  return out
}

export function parseFilter(text: string | undefined): FilterModel {
  const empty: FilterModel = { join: '$and', clauses: [], raw: null }
  if (!text) return empty
  let f: unknown
  try {
    f = JSON.parse(text)
  } catch {
    return empty
  }
  if (!f || typeof f !== 'object' || Array.isArray(f)) return empty
  const keys = Object.keys(f)
  if (keys.length === 1 && (keys[0] === '$and' || keys[0] === '$or')) {
    const list = (f as Record<string, unknown>)[keys[0]]
    if (Array.isArray(list)) {
      const all: Clause[] = []
      for (const part of list) {
        const cs = clausesOf(part)
        if (!cs) return { ...empty, raw: f as RowFilter }
        all.push(...cs)
      }
      return { join: keys[0] as Join, clauses: all, raw: null }
    }
    return { ...empty, raw: f as RowFilter }
  }
  const cs = clausesOf(f)
  return cs ? { join: '$and', clauses: cs, raw: null } : { ...empty, raw: f as RowFilter }
}

function clauseFilter(c: Clause): RowFilter {
  return { [c.column]: { [c.op]: c.value } }
}

/** The engine filter for a model (`null` when empty). */
export function buildFilter(m: FilterModel): RowFilter | null {
  if (m.raw) return m.raw
  if (m.clauses.length === 0) return null
  if (m.clauses.length === 1) return clauseFilter(m.clauses[0])
  return { [m.join]: m.clauses.map(clauseFilter) }
}

export function serializeFilter(m: FilterModel): string | undefined {
  const f = buildFilter(m)
  return f ? JSON.stringify(f) : undefined
}

// ── sort ────────────────────────────────────────────────────────────────────

export const DEFAULT_SORT = '-created_at'

export function parseSort(s: string | undefined): { key: string; desc: boolean } {
  const v = s || DEFAULT_SORT
  return { key: v.replace(/^[-+]/, ''), desc: v.startsWith('-') }
}

/** Header click: same column flips direction; a new column starts ascending (timestamps: newest first). */
export function nextSort(current: string | undefined, key: string): string {
  const cur = parseSort(current)
  if (cur.key === key) return `${cur.desc ? '' : '-'}${key}`
  return key === 'created_at' || key === 'updated_at' ? `-${key}` : key
}

export function sortLabel(s: string | undefined): string {
  const { key, desc } = parseSort(s)
  if (key === 'created_at') return desc ? 'Newest first' : 'Oldest first'
  return `${key} ${desc ? '↓' : '↑'}`
}

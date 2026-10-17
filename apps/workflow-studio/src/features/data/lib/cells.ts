/** Cell display/edit conversions per column type (UI-DESIGN §3.4) and engine row errors. */
import { isApiError } from '@/shared/api/http'
import type { ColumnType } from '@/shared/api/types'
import { parseTs } from '@/features/project/lib/format'

export interface RowFieldError {
  row: number
  column: string | null
  error: string
}

/** `422 row_invalid` → `detail.errors[{row,column,error}]`, else null. */
export function rowErrors(e: unknown): RowFieldError[] | null {
  if (!isApiError(e, 'row_invalid')) return null
  const list = e.details.errors
  return Array.isArray(list) ? (list as RowFieldError[]) : []
}

export function isNumeric(t: ColumnType): boolean {
  return t === 'number' || t === 'integer'
}

/** Collapsed JSON: `{…} 3 keys`, `[…] 2 items`. */
export function jsonSummary(v: unknown): string {
  if (Array.isArray(v)) return `[…] ${v.length} item${v.length === 1 ? '' : 's'}`
  if (v && typeof v === 'object') {
    const n = Object.keys(v).length
    return `{…} ${n} key${n === 1 ? '' : 's'}`
  }
  return JSON.stringify(v)
}

export function displayValue(v: unknown, t: ColumnType): string {
  if (v === undefined || v === null) return ''
  if (t === 'json' || (typeof v === 'object' && v !== null)) return typeof v === 'object' ? jsonSummary(v) : JSON.stringify(v)
  if (t === 'datetime' && typeof v === 'string') return formatStamp(v) || v
  return String(v)
}

export function editText(v: unknown, t: ColumnType): string {
  if (v === undefined || v === null) return ''
  if (t === 'json' || typeof v === 'object') return JSON.stringify(v)
  return String(v)
}

/**
 * Text → value. Empty → null. Unparseable numbers/booleans are sent as typed text so the engine's
 * `row_invalid` ("expected number") is what the user sees. Only JSON is checked client-side.
 */
export function parseEdit(text: string, t: ColumnType): { value: unknown } | { error: string } {
  const s = text.trim()
  if (s === '') return { value: null }
  if (isNumeric(t)) return { value: Number.isFinite(Number(s)) ? Number(s) : text }
  if (t === 'boolean') return { value: s === 'true' ? true : s === 'false' ? false : text }
  if (t === 'json') {
    try {
      return { value: JSON.parse(s) }
    } catch {
      return { error: 'not valid JSON' }
    }
  }
  return { value: text }
}

/** `Oct 15, 09:12` */
export function formatStamp(ts: string | null | undefined, seconds = false): string {
  const d = parseTs(ts)
  if (!d) return ''
  if (seconds) return d.toLocaleString(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false })
  return d.toLocaleString(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit', hour12: false })
}

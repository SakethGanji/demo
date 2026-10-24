/** Pure helpers for cells: status, numbering, references, spec paths. No React. */
import type { StepStatus } from '@/shared/glass'
import type { Cell, CellResult, Notebook, RowsPreview, StoredCellKind } from '../types'

/** Labels for every stored kind: the removed ones (approval / person) still need a name on their read-only card. */
export const KIND_LABEL: Record<StoredCellKind, string> = {
  query: 'Query', http: 'HTTP', workflow: 'Workflow', rule: 'Rule', change: 'Change', approval: 'Approval', data: 'Data',
  store: 'Store', browser: 'Browser', for_each: 'For each', ask: 'Ask', note: 'Note', person: 'Person', wait: 'Wait',
}

/** Kind label text in the mono row ("for each", "query"). */
export const kindText = (k: StoredCellKind) => (k === 'for_each' ? 'for each' : k)

/** Top-level cells in order, each with its display number ("3", "3.1"). */
export interface Numbered { cell: Cell; n: string; depth: number; parent: Cell | null }
export function numberCells(cells: Cell[]): Numbered[] {
  const top = [...cells].filter((c) => !c.parent_cell_id).sort((a, b) => a.position - b.position)
  const out: Numbered[] = []
  top.forEach((c, i) => {
    out.push({ cell: c, n: String(i + 1), depth: 0, parent: null })
    ;[...(c.children ?? [])].sort((a, b) => a.position - b.position).forEach((ch, j) => out.push({ cell: ch, n: `${i + 1}.${j + 1}`, depth: 1, parent: c }))
  })
  return out
}

export function activeRun(nb: Notebook) {
  return nb.active_run_id ? nb.runs.find((r) => r.id === nb.active_run_id) ?? null : null
}

/** Status mark for a cell given its latest result and the live run (if any). */
export function cellStatus(cell: Cell, nb: Notebook, runningId: string | null): StepStatus {
  if (runningId === cell.id) return 'running'
  const live = activeRun(nb)
  const r = cell.latest
  if (live && cell.position >= live.from_position && !cell.parent_cell_id && (!r || r.run_id !== live.id)) return 'pending'
  if (!r) return cell.kind === 'note' ? 'ok' : 'pending'
  if (cell.stale) return 'stale'
  if (cell.kind === 'for_each' && failedRows(r) > 0) return 'need'
  return resultStatus(r)
}

/** Rows of a for-each result that failed (0 for any other result). A loop with failed rows needs attention. */
export function failedRows(r: CellResult | null | undefined): number {
  const o = r?.output as { failed?: unknown } | null | undefined
  return r && r.status === 'success' && o && typeof o.failed === 'number' ? o.failed : 0
}

export function resultStatus(r: CellResult): StepStatus {
  switch (r.status) {
    case 'success':
    case 'applied':
    case 'kept_earlier':
      return 'ok'
    case 'error':
      return 'fail'
    case 'needs_approval':
    case 'waiting':
      return 'need'
    default:
      return 'skip'
  }
}

/** The cell the live run is on: the first top-level cell at/after from_position without a result in this run. */
export function runningCellId(nb: Notebook): string | null {
  const live = activeRun(nb)
  if (!live || live.status !== 'running' && live.status !== 'waiting') return null
  const top = numberCells(nb.cells).filter((x) => x.depth === 0).map((x) => x.cell)
  const c = top.find((c) => c.position >= live.from_position && (!c.latest || c.latest.run_id !== live.id))
  return c?.id ?? null
}

// ── references ─────────────────────────────────────────────────────────────────────────────

const REF_RE = /^\{\{\s*(cells\.(\w+)|row)(?:\.([\w.[\]]+))?\s*\}\}$/

export function isRef(v: unknown): v is string {
  return typeof v === 'string' && REF_RE.test(v.trim())
}

function findCell(cells: Cell[], name: string): Cell | undefined {
  for (const c of cells) {
    if (c.name === name) return c
    const ch = c.children ? findCell(c.children, name) : undefined
    if (ch) return ch
  }
  return undefined
}

/** Human label for a reference. With `cells`, the referenced cell's title is used and a single-row data cell drops
 *  its `rows[0].`: "{{cells.c2.rows[0].max_age_days}}" -> "Settlement window · max_age_days". Without a title it
 *  falls back to the id: "{{cells.c3.rows[0].id}}" -> "c3 · rows[0].id"; "{{row.id}}" -> "row · id". */
export function refLabel(v: string, cells?: Cell[]): string {
  const m = REF_RE.exec(v.trim())
  if (!m) return v
  let who = m[2] ?? 'row'
  let path = m[3]
  const target = m[2] && cells ? findCell(cells, m[2]) : undefined
  if (target?.title?.trim()) {
    who = target.title.trim()
    const rows = (target.spec as { rows?: unknown[] } | null)?.rows
    if (target.kind === 'data' && Array.isArray(rows) && rows.length === 1 && path?.startsWith('rows[0].')) path = path.slice('rows[0].'.length)
  }
  return path ? `${who} · ${path}` : who
}

/** Display text for any spec value: refs shortened (named after their cell when `cells` is given), objects as compact JSON. */
export function valueText(v: unknown, cells?: Cell[]): string {
  if (v === null || v === undefined) return ''
  if (isRef(v)) return refLabel(v, cells)
  if (typeof v === 'string') return v
  if (typeof v === 'number' || typeof v === 'boolean') return String(v)
  return JSON.stringify(v)
}

/** Parse an edited token back to the type of the original value (number stays a number). */
export function coerce(next: string, prev: unknown): unknown {
  const t = next.trim()
  if (/^\{\{.*\}\}$/.test(t)) return t
  if (typeof prev === 'number' && t !== '' && !Number.isNaN(Number(t))) return Number(t)
  if (typeof prev === 'boolean') return t === 'true'
  if (prev !== null && typeof prev === 'object') {
    try {
      return JSON.parse(t)
    } catch {
      return t
    }
  }
  return next
}

/** Immutable set at a dotted path (`params.days`, `where.0.value`, `rows.0.max_age_days`). */
export function setPath<T>(obj: T, path: string, value: unknown): T {
  const keys = path.split('.')
  const root: unknown = Array.isArray(obj) ? [...obj] : { ...(obj as object) }
  let cur = root as Record<string, unknown>
  for (let i = 0; i < keys.length - 1; i++) {
    const k = keys[i]
    const nxt = cur[k]
    const copy = Array.isArray(nxt) ? [...nxt] : nxt && typeof nxt === 'object' ? { ...(nxt as object) } : /^\d+$/.test(keys[i + 1]) ? [] : {}
    cur[k] = copy
    cur = copy as Record<string, unknown>
  }
  cur[keys[keys.length - 1]] = value
  return root as T
}

export function getPath(obj: unknown, path: string): unknown {
  return path.split('.').reduce<unknown>((o, k) => (o && typeof o === 'object' ? (o as Record<string, unknown>)[k] : undefined), obj)
}

/** Refs a new cell can point at: earlier row-producing cells. */
/** Row sets a new cell may reference: only cells ABOVE it (a forward ref is bad_ref). `before`: the position the new
 *  cell sits under (a for-each's position for a step inside it); omitted = appended at the end. */
export function rowRefs(cells: Cell[], before?: number): { value: string; label: string }[] {
  return numberCells(cells)
    .filter((x) => x.depth === 0 && (before === undefined || x.cell.position < before) && ['query', 'data', 'rule', 'workflow', 'http'].includes(x.cell.kind))
    .map((x) => ({ value: `{{cells.${x.cell.name}.rows}}`, label: `${x.n} · ${x.cell.title || KIND_LABEL[x.cell.kind]} (${x.cell.name}.rows)` }))
}

export function asRows(out: CellResult['output']): RowsPreview | null {
  if (!out || !Array.isArray((out as RowsPreview).rows) || !Array.isArray((out as RowsPreview).columns)) return null
  return out as RowsPreview
}

/** A count rule's number when its output carries a count and no rows (shown as a big number, not an empty table). */
export function countOnly(cell: Cell): number | null {
  const o = cell.latest?.output as { count?: unknown; rows?: unknown } | null | undefined
  if (cell.kind !== 'rule' || !o || typeof o.count !== 'number') return null
  return Array.isArray(o.rows) && o.rows.length > 0 ? null : o.count
}

export function fmtMs(ms: number | null | undefined): string {
  if (ms === null || ms === undefined) return ''
  return ms < 1000 ? `${ms} ms` : `${(ms / 1000).toFixed(ms < 10_000 ? 1 : 0)} s`
}

export function fmtTime(iso: string | null | undefined): string {
  if (!iso) return ''
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return ''
  const today = new Date().toDateString() === d.toDateString()
  return today ? d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }) : d.toLocaleString([], { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })
}

export function plural(n: number, one: string, many = `${one}s`) {
  return `${n.toLocaleString('en-US')} ${n === 1 ? one : many}`
}

/** Which workspace tab a cell's result belongs in. */
export type WsTab = 'results' | 'data' | 'change' | 'browser' | 'api' | 'workflow' | 'trace' | 'report'
export function tabFor(kind: StoredCellKind): WsTab | null {
  switch (kind) {
    case 'query':
    case 'rule':
    case 'store':
    case 'for_each':
      return 'results'
    case 'data':
      return 'data'
    case 'change':
      return 'change'
    case 'browser':
      return 'browser'
    case 'http':
      return 'api'
    case 'workflow':
      return 'workflow'
    default:
      return null
  }
}

/** Tabs that open the workspace by themselves (browser, table, write): the calm rule. */
export const AUTO_TABS: WsTab[] = ['browser', 'results', 'change', 'workflow']

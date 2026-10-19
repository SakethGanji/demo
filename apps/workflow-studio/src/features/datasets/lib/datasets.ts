/** Pure helpers for project datasets: formatting, upload checks and the node picker's value mapping. */
import type { DatasetItem } from './types'

export const DATASET_ACCEPT = '.csv,.parquet,.xlsx,.xls'
const EXTENSIONS = ['csv', 'parquet', 'xlsx', 'xls']

/** `12.4 KB`, `3.1 MB`; `—` when unknown. */
export function formatBytes(n: number | null | undefined): string {
  if (n == null || !Number.isFinite(n) || n < 0) return '—'
  if (n < 1024) return `${n} B`
  const units = ['KB', 'MB', 'GB', 'TB']
  let v = n / 1024
  let i = 0
  while (v >= 1024 && i < units.length - 1) {
    v /= 1024
    i++
  }
  return `${v >= 100 ? Math.round(v) : v.toFixed(1)} ${units[i]}`
}

/** `1,204` with grouping; `?` when unknown. */
export function formatCount(n: number | null | undefined): string {
  return n == null || !Number.isFinite(n) ? '?' : n.toLocaleString('en-US')
}

/** `1,204 rows × 7 cols` */
export function shapeLabel(d: Pick<DatasetItem, 'row_count' | 'column_count'>): string {
  const rows = d.row_count === 1 ? 'row' : 'rows'
  const cols = d.column_count === 1 ? 'col' : 'cols'
  return `${formatCount(d.row_count)} ${rows} × ${formatCount(d.column_count)} ${cols}`
}

/** A preview cell as text: null/undefined empty, objects as JSON. */
export function cellText(v: unknown): string {
  if (v == null) return ''
  if (typeof v === 'object') return JSON.stringify(v)
  return String(v)
}

/** null when the file can be uploaded, otherwise a readable reason. */
export function uploadProblem(fileName: string): string | null {
  const ext = fileName.toLowerCase().split('.').pop() ?? ''
  if (!fileName.includes('.') || !EXTENSIONS.includes(ext)) return 'Choose a CSV, Parquet or Excel (.xlsx, .xls) file.'
  return null
}

const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i

export function isUuid(v: string): boolean {
  return UUID_RE.test(v.trim())
}

/** An `{{ … }}` expression rather than a literal. */
export function isExpressionValue(v: unknown): boolean {
  return typeof v === 'string' && v.includes('{{')
}

/**
 * Which dataset a node's `datasetId` value names. The picker saves the project link `id` (stable across renames).
 * The engine also accepts the raw analytics UUID (workflows saved before project datasets) and a name (expressions,
 * the AI agent), so those match too, in the same order the engine resolves them: id, analytics id, name.
 * `unknown` holds a non-empty value that names nothing in the list (kept so it is never silently cleared).
 */
export function resolveDatasetValue(
  value: string | null | undefined,
  items: DatasetItem[],
): { item: DatasetItem | null; unknown: string | null } {
  const v = (value ?? '').trim()
  if (!v) return { item: null, unknown: null }
  const item = items.find((d) => d.id === v) ?? items.find((d) => d.dataset_id === v) ?? items.find((d) => d.name === v)
  return item ? { item, unknown: null } : { item: null, unknown: v }
}

/** Why a saved value matches nothing: an id means the dataset was deleted, anything else is a name. */
export function missingDatasetMessage(unknown: string): string {
  return isUuid(unknown) ? 'This dataset was deleted from the project.' : `No dataset named “${unknown}” in this project.`
}

/** Datasets page URL for a project slug (opened in a new tab from the node picker). */
export function datasetsPageUrl(slug: string): string {
  return `/p/${encodeURIComponent(slug)}/datasets`
}

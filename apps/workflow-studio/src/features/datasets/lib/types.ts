/**
 * Project datasets (engine `/api/projects/{p}/datasets`): files imported into the analytics service and
 * named inside the project. Sample / Profile / Aggregate nodes save the link `id` and show the name.
 * `dataset_id` is the analytics service's `datasets.id` (a cross-database reference until the two databases merge).
 */

export type DatasetFormat = 'csv' | 'parquet' | 'xlsx' | 'xls'

export interface DatasetColumn {
  name: string
  type: string
}

export interface DatasetItem {
  id: string
  /** the analytics-service dataset UUID */
  dataset_id: string
  /** the analytics version imported (null if the engine couldn't read it back) */
  version_id: string | null
  name: string
  file_name: string
  format: DatasetFormat
  row_count: number | null
  column_count: number | null
  size_bytes: number | null
  columns: DatasetColumn[]
  /** the stored file is gone from the analytics service (only the detail endpoint checks) */
  missing?: boolean
  created_at: string
  updated_at: string
}

export interface DatasetDetail extends DatasetItem {
  /** up to 20 rows */
  preview: Record<string, unknown>[]
}

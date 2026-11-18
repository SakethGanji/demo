// Mirrors backend/app/schemas.py and the profile built in backend/app/profiling.py. Keep them in step.

export type Health = { status: 'ok'; version: string; datasets: number; data_dir: string }

export type ColumnType = 'text' | 'category' | 'number' | 'id'

export type ColumnProfile = {
  name: string
  type: ColumnType
  dtype: string
  role: 'target' | 'feature' | 'ignored'
  missing: number
  missing_share: number
  unique: number
  example: string
  stats?: { min: number; max: number; mean: number; std: number; median: number } | { mean_chars: number; max_chars: number } | null
  top?: { value: string; count: number }[]
}

export type ClassShare = { name: string; count: number; share: number }

export type Splits = {
  seed: number
  target: string | null
  stratified: boolean
  holdout_share: number
  holdout_rows: number
  n_folds: number
  fold_rows: number[]
  holdout_classes?: Record<string, number>
}

export type Profile = {
  rows: number
  n_cols: number
  kind: 'text' | 'tabular'
  target: string | null
  suggested_target: string | null
  label_candidates: string[]
  columns: ColumnProfile[]
  classes: ClassShare[]
  n_classes: number
  imbalance_ratio: number | null
  missing: { cells: number; share: number; rows_with_missing: number; columns_with_missing: string[] }
  duplicates: { rows: number; share: number; text: Record<string, number>; conflicting_labels: number; ignoring: string[] }
  warnings: string[]
  splits: Splits
}

export type DatasetSummary = {
  id: string
  name: string
  filename: string
  source: 'sample' | 'upload'
  sample: boolean
  note: string
  created_at: string
  size_bytes: number
  rows: number
  n_cols: number
  kind: 'text' | 'tabular'
  target: string | null
  n_classes: number
}

export type DatasetDetail = DatasetSummary & { profile: Profile }

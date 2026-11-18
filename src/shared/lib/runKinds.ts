import type { Run } from '@/shared/api/types'

/** Every run in the `runs` table has a kind: a tabular fit, an imported TabularPredictor, a TimeSeriesPredictor
 *  forecast or an IsolationForest anomaly run. Pages that list runs use this to show each with the metric that
 *  suits it and to open it on the right page. */
export type RunKind = 'tabular' | 'import' | 'forecast' | 'anomaly'
export type KindedRun = Run & { kind?: string; anomaly?: { n_flagged?: number; threshold?: number; contamination?: number; truth?: { roc_auc: number | null; precision_at_k: { k: number; precision: number }[]; planted: number } | null } | null }

export const kindOf = (r: Run): RunKind => {
  const k = (r as KindedRun).kind
  return k === 'forecast' || k === 'anomaly' || k === 'import' ? k : 'tabular'
}

/** The type filter's value for a run: the tabular problem type, or the kind for the others. */
export const typeOf = (r: Run): string => {
  const k = kindOf(r)
  return k === 'tabular' ? r.problem_type ?? 'pending' : k === 'import' ? 'imported' : k
}

export const TYPE_FILTERS = ['all', 'binary', 'multiclass', 'regression', 'quantile', 'forecast', 'anomaly', 'imported'] as const

/** The page a run opens on. */
export const runPage = (r: Run): string => {
  const k = kindOf(r)
  if (k === 'forecast') return `forecast?run=${r.id}`
  if (k === 'anomaly') return `anomaly?run=${r.id}`
  return `quick-train?run=${r.id}`
}

/** The run's headline number for a list: its own metric on held-out data, with the symbol and direction.
 *  Tabular and imported: AutoGluon's eval_metric on the fixed holdout. Forecast: the eval metric on the last H steps
 *  of every series. Anomaly: ROC AUC against the planted truth when the dataset has one (the sample), else the
 *  number of rows flagged at s ≥ s*. */
export function headline(r: Run): { label: string; value: number | null; text: string; dir: '↑' | '↓' | '' ; note?: string } {
  const k = kindOf(r)
  if (k === 'anomaly') {
    const a = (r as KindedRun).anomaly
    const auc = a?.truth?.roc_auc ?? null
    if (auc != null) return { label: 'AUC vs truth', value: auc, text: auc.toFixed(3), dir: '↑', note: 'ROC AUC of s against the planted anomalies (sample only)' }
    return { label: 'flagged', value: a?.n_flagged ?? null, text: a?.n_flagged != null ? `${a.n_flagged} at s*` : '—', dir: '', note: 'rows with s ≥ s*' }
  }
  const m = r.eval_metric?.name ?? ''
  const v = r.holdout?.metrics?.[m] ?? null
  const dir = r.eval_metric ? (r.eval_metric.greater_is_better ? '↑' : '↓') : ''
  return { label: m || '—', value: v, text: v == null ? '—' : Math.abs(v) >= 1000 ? v.toLocaleString(undefined, { maximumFractionDigits: 0 }) : v.toFixed(4), dir }
}

/** A short line for the problem column: "binary", "forecast · H = 28", "anomaly · IsolationForest + LOF". */
export function problemLine(r: Run): string {
  const k = kindOf(r)
  if (k === 'forecast') {
    const h = (r.config.prediction_length as number | undefined) ?? null
    return `forecast${h ? ` · H = ${h}` : ''}`
  }
  if (k === 'anomaly') return `anomaly · IsolationForest${r.config.lof ? ' + LOF' : ''}`
  const lv = (r as Run & { quantile_levels?: number[] | null }).quantile_levels
  const cov = (r as Run & { quantile?: { coverage?: number | null } | null }).quantile?.coverage
  const base = r.problem_type === 'quantile' && lv ? `quantile · τ = ${lv.join(', ')}${cov != null ? ` · cov ${cov.toFixed(3)}` : ''}` : r.problem_type ?? '—'
  return k === 'import' ? `${base} · imported` : base
}

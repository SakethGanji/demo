// S8 flows: forecasting (TimeSeriesPredictor), anomaly detection (IsolationForest) and importing a predictor.
// Runs of these kinds share the run record, its events and SSE with tabular runs (api.runs.get, followRun).

import { request } from './client'
import type { Run } from './types'

export type FcMapping = {
  item_id: string | null; timestamp: string; target: string
  known_covariates: string[]; static_features: string[]; past_covariates: string[]
}

export type FcOptions = {
  presets: { key: string; info: string }[]
  metrics: { key: string; formula: string; quantile: boolean }[]
  defaults: { prediction_length: number; eval_metric: string; presets: string; time_limit: number; quantile_levels: number[]; num_val_windows: number | null; ensemble: FcEnsembleKind; conformal: boolean; conformal_scope: 'pooled' | 'item' }
  ensembles: { key: FcEnsembleKind; label: string; info: string; ensemble_hyperparameters: Record<string, unknown> | null }[]
  num_val_windows_auto: string
}
export type FcEnsembleKind = 'greedy' | 'quantile' | 'both'

export type FcPreview = {
  rows: number; items: number; observed_rows: number; future_rows: number; start: string; end: string
  freq: string | null; seasonal_period: number; length_min: number; length_max: number; static_not_constant: string[]
  missing_target_inside: number; suggested_prediction_length: number
  series: Record<string, { t: string[]; y: (number | null)[] }>
}

export type FcCreate = {
  dataset_id: string; mapping: FcMapping; name?: string; prediction_length: number; freq?: string | null; eval_metric: string
  presets: string | null; time_limit: number | null; quantile_levels: number[]; num_val_windows: number | null
  ensemble?: FcEnsembleKind; conformal?: boolean; conformal_scope?: 'pooled' | 'item'
  hyperparameters?: Record<string, unknown> | string | null; fit_kwargs: Record<string, unknown>; predictor_kwargs?: Record<string, unknown>; source?: string
}

export type FcBand = { t: string[]; mean: number[]; q: Record<string, number[]>; q_raw?: Record<string, number[]>; actual?: (number | null)[] }
export type FcChart = { run_id: string; quantiles: number[]; items: Record<string, { history: { t: string[]; y: (number | null)[] }; holdout?: FcBand; future?: FcBand }> }

export type FcItemError = { item_id: string; n: number; MASE: number | null; WQL: number | null; sMAPE: number; MAPE: number; RMSE: number; bias: number; coverage: number; interval: [number, number]; scale: number; mean_actual: number }

export type FcRunExtra = {
  forecast?: { model: string; horizon: number; start: string; end: string; rows: number; known_covariates_source: string; quantiles: number[] } | null
  backtests?: { windows: { window: number; start: string; end: string }[]; models: Record<string, { window: number; MASE: number | null; WQL: number | null; coverage: number | null }[]>; error?: string } | null
  holdout?: { model: string; metrics: Record<string, number>; metrics_raw?: Record<string, number>; coverage: number | null; interval: [number, number]; items: FcItemError[]; horizon: number; eval_metric?: string
    recalibrated?: boolean; conformal?: FcConformal | null } | null
  ensembles?: { model: string; kind: string; weights: Record<string, number>; weights_per_quantile: Record<string, Record<string, number>> | null; best: boolean }[]
  data?: Record<string, unknown> & { items?: number; freq?: string; seasonal_period?: number; prediction_length?: number; start?: string; end?: string; train_end?: string; holdout_start?: string; known_covariates?: string[]; static_features?: string[] }
}

export type FcConformal = {
  method?: string; scope?: 'pooled' | 'item'; centre?: string; quantiles?: number[]; n_scores?: number; windows?: number; items?: number; model?: string
  factors?: Record<string, number | null>; levels?: Record<string, { c: number | null; n: number; k: number; raw_below: number | null }>
  per_item?: Record<string, Record<string, number | null>>; error?: string
}

export type AnTruth = {
  planted: number; precision_at_k: { k: number; hits: number; precision: number; recall: number }[]; roc_auc: number | null
  average_precision: number | null; by_kind: Record<string, { planted: number; found: number }>; flagged: number; base_rate: number; note: string
}
export type AnReason = { feature: string; phi: number; push: number; value: unknown; z: number | null; median?: number | null; share: number | null }
export type AnReview = { label: 0 | 1; by: string; at: string }
export type AnRow = { rank: number; row: number; s: number; Eh: number; lof: number | null; flagged: boolean; planted?: boolean; review?: AnReview | null; values: Record<string, unknown>; phi0: number | null; phi_sum: number; reasons: AnReason[]; why: string }
export type AnSummary = {
  threshold: number; contamination: number; n_flagged: number; psi: number; c_n: number; n_trees: number; fit_seconds: number; columns_out: number; mean_depth: number
  distribution: { edges: number[]; counts: number[]; quantiles: Record<string, number>; min: number; max: number; mean: number }
  lof: { n_neighbors: number; spearman_vs_iforest: number; overlap_top: number } | null
  truth?: AnTruth; truth_lof?: AnTruth
}
export type AnTop = { run_id: string; contamination: number; threshold: number; n_flagged: number; rows: AnRow[]; truth: AnTruth | null; n_rows: number; review?: AnReviewSummary }
export type AnReviewSummary = { confirmed: number; dismissed: number; reviewed: number; min_positives: number; can_train: boolean; needed: number }
export type AnLabels = AnReviewSummary & { dataset_id: string; labels: { row: number; label: 0 | 1; by: string; at: string; run_id: string; s: number | null; note: string }[]; truth_agreement?: { confirmed_planted: number; dismissed_planted: number } }
export type AnCompare = {
  model: string; rows_scored: number; unreviewed: number; n_oof: number; source: string; k: number; overlap_top_k: number
  hidden_in_unreviewed?: number; iforest?: AnTruth; supervised?: AnTruth; iforest_at_k?: AnTruth; supervised_at_k?: AnTruth; note?: string
}
export type AnSupervised = {
  run_id: string; dataset_id: string; created_at: string; by: string; negatives: 'unlabeled' | 'dismissed_only'; include_score: boolean
  confirmed: number; dismissed: number; reviewed: number; unreviewed: number; features_added: string[]; drop_columns: string[]
  fit_kwargs: Record<string, unknown>; eval_metric: string | null; status: string; error?: string | null
  comparison?: AnCompare; comparison_error?: string; best_model?: string | null; holdout?: Record<string, number> | null
}
export type AnSupervisedCreate = { presets?: string; time_limit?: number | null; eval_metric?: string; num_bag_folds?: number; include_score?: boolean; negatives?: 'unlabeled' | 'dismissed_only'; by?: string }
export type AnVelocity = {
  entity_column: string; time_column: string; amount_column: string; category_column: string | null; added: string[]; in_forest: string[]; not_in_forest: string[]
  windows_hours: Record<string, number>; rows_without_history: number; summary: Record<string, { mean: number; p99: number; max: number }>
}
export type AnScores = { edges: number[]; counts: number[]; n: number; sorted_sample: number[]; scatter?: { row: number; s: number; lof: number }[] }
export type AnBatch = { id: string; filename: string; summary: { rows: number; n_flagged: number; threshold: number; flagged_share: number; expected_share: number; mean_score: number }; top: { row: number; s: number; percentile: number; flagged: boolean; reasons: AnReason[]; why: string }[]; histogram: { edges: number[]; counts: number[] }; download: string }
export type AnGroup = { entity_column: string | null; time_column: string | null; relative_columns: string[]; source: string; amount_column?: string | null; category_column?: string | null; velocity_columns?: string[]; velocity_in_forest?: string[] }
export type AnCreate = { velocity_features?: boolean; amount_column?: string | null; category_column?: string | null; velocity_in_forest?: string[] | null; group_features?: boolean; entity_column?: string | null; time_column?: string | null; relative_columns?: string[]; dataset_id: string; features?: string[] | null; contamination: number; n_estimators: number; max_samples?: number | string | null; max_features?: number; lof: boolean; lof_neighbors?: number; iforest_kwargs?: Record<string, unknown>; random_state?: number; source?: string }

export type FlowRun = Omit<Run, 'holdout' | 'data' | 'ensembles'> & FcRunExtra & { kind?: string; anomaly?: AnSummary | null; imported?: Record<string, unknown> | null }

const J = (body: unknown) => ({ method: 'POST', body: JSON.stringify(body), headers: { 'Content-Type': 'application/json' } })


// G3 · Expected loss: PD × LGD × EAD (backend/app/el). One run trains three TabularPredictors.
export type ElDetect = { dataset_id: string; pd_column: string | null; lgd_column: string | null; ead_column: string | null; segments: string[]; drop_columns: string[]; split: string; time_column: string | null; group_column: string | null; evidence: Record<string, string[]> }
export type ElCreate = { dataset_id: string; pd_column?: string; lgd_column?: string; ead_column?: string; segments?: string[]; drop_columns?: string[]; time_limit?: number; presets?: string; fit_kwargs?: Record<string, unknown> }
export type ElPortfolio = { n: number; defaults: number; default_rate: number; mean_pd: number; el: number; realised: number; ratio: number | null; error_pct: number | null; ratio_ci: [number, number]; ead_total: number; el_rate: number | null; within_10pct: boolean }
export type ElComponent = { label: string; problem_type: string; eval_metric: string; best_model: string; train_rows: number; fit_seconds: number; n_models: number
  auc?: number; log_loss?: number; brier?: number; mean_pd?: number; default_rate?: number; n?: number; mae?: number; rmse?: number; r2?: number | null; mean_pred?: number; mean_true?: number; sum_pred?: number; sum_true?: number }
export type ElSegment = { value: string; n: number; el: number; share: number; mean_pd: number; mean_lgd: number; ead: number; el_rate: number | null; realised?: number; ratio?: number | null; defaults?: number }
export type ElShock = { column: string; op: 'pct' | 'pp' | 'add' | 'set'; value: number | string }
export type ElScenario = { name: string; shocks: (ElShock & { words: string; changed: number; clipped: number })[]; expect: 'up' | 'down' | null; why?: string | null; direction: 'up' | 'down' | 'flat'; as_expected: boolean | null
  base: number; stressed: number; delta: number; delta_pct: number | null; components: Record<'pd' | 'lgd' | 'ead', { base: number; stressed: number }>
  segments: Record<string, { value: string; n: number; base: number; stressed: number; delta: number; delta_pct: number | null }[]>; n: number; seconds: number }
export type ElRange = { numeric: true; min: number; max: number; median: number; share: boolean } | { numeric: false; values: string[] }
export type ElResult = { portfolio: ElPortfolio; components: Record<'pd' | 'lgd' | 'ead', ElComponent>; segments: Record<string, ElSegment[]>; scenarios: ElScenario[]; segment_columns: string[]
  columns: { pd: string; lgd: string; ead: string; positive_class: string | number }; inputs: string[]; ranges: Record<string, ElRange>; holdout_rows: number
  detected: { pd_column?: string; lgd_column?: string; ead_column?: string; segments?: string[]; drop_columns?: string[]; split?: string; evidence?: Record<string, string[]> } }
export type ElRun = { run_id: string; name: string; status: string; error: string | null; dataset_id: string; split: { strategy?: string; params?: Record<string, unknown> } & Record<string, unknown>
  config: { label: string; lgd_column: string; ead_column: string; segments: string[]; drop_columns: string[]; presets: string | null; time_limit: number | null }
  expected_loss: ElResult | null; custom_scenarios: ElScenario[] }

// G5 · Segmented models (backend/app/segmented): one AutoGluon model per segment against a global one, routed.
export type SegDetect = { dataset_id: string; label: string | null; problem_type: 'binary' | 'regression' | null; segment_column: string | null; candidates: string[]
  drop_columns: string[]; split: string; time_column: string | null; group_column: string | null; evidence: Record<string, string[]>
  heterogeneity: { feature: string; spread: number; high: [string, number]; low: [string, number]; opposite: boolean; differs: boolean; words: string } | null }
export type SegCreate = { dataset_id: string; label?: string; segment_column?: string; drop_columns?: string[]; time_limit?: number; cost?: { c_fp: number; c_fn: number } }
export type SegSide = { auc?: number; log_loss?: number; brier?: number; mean_p?: number; rmse?: number; mae?: number; mean_pred?: number }
export type SegDelta = { delta: number; ci: [number, number]; se: number; p_value?: number | null }
export type SegCompare = { n: number; positives?: number; rate?: number | null; mean_true?: number; global: SegSide; routed: SegSide
  delta_auc?: SegDelta | null; delta_log_loss?: SegDelta; delta_mse?: SegDelta; delta_rmse?: number; verdict: 'routed better' | 'global better' | 'no clear difference' }
export type SegRow = { value: string; train_rows: number; holdout_rows: number; model: string | null; why?: string; best_model?: string; fit_seconds?: number; n_models?: number
  routed: boolean; route_why: string; tuning?: { rows: number; global: number; segment: number; loss: string }; own?: SegCompare | null; routed_cmp?: SegCompare }
export type SegResult = { segment_column: string; problem_type: 'binary' | 'regression'; label: string; positive_class: string | number | null; threshold: number | null
  eval_metric: string; headline: 'AUC' | 'RMSE'; segments: SegRow[]; overall: SegCompare; overall_all_segment_models: SegCompare; routes: string[]; unseen_in_training: string[]
  global: { rows: number; best_model: string; fit_seconds: number; n_models: number; features: number }; holdout_rows: number; tuning_rows: number; wins: string[]; losses: string[]
  routing_rule: string; min_rows: number; min_class: number; detected: Partial<SegDetect> }
export type SegRun = { run_id: string; name: string; status: string; error: string | null; dataset_id: string; split: { strategy?: string } & Record<string, unknown>
  config: { label: string; problem_type: string; segment_column: string; drop_columns: string[]; presets: string | null; time_limit: number | null }
  segmented: SegResult | null }

// G6 · Time to event (backend/app/survival): Cox PH and a random survival forest (scikit-survival), calibrated against KM.
export type SurvDetect = { dataset_id: string; duration_column: string | null; event_column: string | null; strong: boolean
  duration_candidates: string[]; event_candidates: string[]; evidence: Record<string, string[]>; horizons: number[]; unit: string | null
  duration_range: [number, number] | null; event_rate: number | null; drop_columns: string[]; split: string; group_column: string | null; split_why: string }
export type SurvCreate = { dataset_id: string; duration_column?: string; event_column?: string; horizons?: number[]; drop_columns?: string[]; n_estimators?: number; min_samples_leaf?: number }
export type SurvCal = { max_abs_diff: number; in_ci: number; cells: number; ok: boolean; rule: string }
export type SurvAt = { t: number; auc: number; brier: number; mean_S: number }
export type SurvModel = { c_index: number; c_index_ci: [number, number]; val_c_index: number; val_ibs: number; at: SurvAt[]; mean_auc?: number; calibration: SurvCal }
export type SurvKM = { s: number[]; lo: number[]; hi: number[] }
export type SurvGroup = { group: string; n: number; events: number; risk_from: number; risk_to: number; pred: number[]; km: SurvKM
  at: { t: number; pred: number; km: number; lo: number; hi: number; diff: number; in_ci: boolean }[] }
export type SurvHR = { term: string; feature: string; kind: 'number' | 'category'; level: string | null; beta: number; se: number; z: number | null; p_value: number | null
  sd: number | null; hr_sd: number | null; hr: number; hr_ci: [number, number] }
export type SurvResult = { duration_column: string; event_column: string; unit: string | null; served: 'cox' | 'rsf'; val_from: string; chosen_by: string
  horizons: number[]; grid: number[]; models: Record<'cox' | 'rsf', SurvModel>; fit_seconds: Record<'cox' | 'rsf', number>; n_trees: number; ridge: number
  groups: SurvGroup[]; km_all: SurvKM; calibration: SurvCal; hazard_ratios: SurvHR[]; ref_levels: Record<string, string>; inputs: string[]; numeric: string[]
  dropped_categories: string[]; train_rows: number; val_rows: number; holdout_rows: number; events: { train: number; holdout: number }
  examples: Record<string, unknown>[]; detected: Partial<SurvDetect> }
export type SurvRun = { run_id: string; name: string; status: string; error: string | null; dataset_id: string; split: { strategy?: string } & Record<string, unknown>
  config: { duration_column: string; event_column: string; horizons: number[]; drop_columns: string[]; n_estimators: number; min_samples_leaf: number }
  survival: SurvResult | null }
export type SurvTry = { served: 'cox' | 'rsf'; unit: string | null; horizons: number[]; duration_column: string; event_column: string
  risk: number; risk_group: string; at: { t: number; survival: number; event_by: number }[]; median_time: number | null; curve: number[]; grid: number[]
  baseline_curve: number[]; km_all: number[]; features: string[]; missing_columns: string[]
  contributions?: { feature: string; value: unknown; log_hr: number; hr: number }[]; hr_vs_average?: number; predict_ms: number }

export const flows = {
  runs: (kind: 'forecast' | 'anomaly' | 'expected_loss' | 'segmented' | 'survival' | 'tabular' | 'all') => request<{ runs: FlowRun[] }>(`/runs?kind=${kind}`).then((r) => r.runs),
  run: (id: string) => request<FlowRun>(`/runs/${encodeURIComponent(id)}`),
  forecast: {
    options: () => request<FcOptions>('/forecast/options'),
    mapping: (ds: string) => request<{ mapping: FcMapping; source: string; prediction_length: number | null }>(`/forecast/mapping/${encodeURIComponent(ds)}`),
    preview: (dataset_id: string, mapping: FcMapping) => request<FcPreview>('/forecast/preview', { ...J({ dataset_id, mapping }), timeoutMs: 60_000 }),
    create: (body: FcCreate) => request<FlowRun>('/forecast/runs', { ...J(body), timeoutMs: 30_000 }),
    chart: (id: string) => request<FcChart>(`/forecast/runs/${encodeURIComponent(id)}/chart`, { timeoutMs: 30_000 }),
    csvUrl: (id: string, which: 'future' | 'holdout' | 'future_raw' | 'holdout_raw' = 'future') => `/api/forecast/runs/${encodeURIComponent(id)}/forecast.csv?which=${which}`,
    predict: (id: string, file: File) => {
      const f = new FormData(); f.append('file', file)
      return request<{ id: string; items: number; rows: number; seconds: number; known_covariates_source: string; chart: Omit<FcChart, 'run_id'>; model: string; download: string; recalibrated?: boolean }>(
        `/forecast/runs/${encodeURIComponent(id)}/predict`, { method: 'POST', body: f, timeoutMs: 300_000 })
    },
  },
  el: {
    detect: (ds: string) => request<ElDetect>(`/expected-loss/detect?dataset_id=${encodeURIComponent(ds)}`, { timeoutMs: 60_000 }),
    create: (body: ElCreate) => request<FlowRun>('/expected-loss/runs', { ...J(body), timeoutMs: 60_000 }),
    get: (id: string) => request<ElRun>(`/expected-loss/runs/${encodeURIComponent(id)}`),
    stress: (id: string, body: { name?: string; shocks: ElShock[]; expect?: 'up' | 'down' }) =>
      request<ElScenario>(`/expected-loss/runs/${encodeURIComponent(id)}/stress`, { ...J(body), timeoutMs: 120_000 }),
  },
  seg: {
    detect: (ds: string) => request<SegDetect>(`/segmented/detect?dataset_id=${encodeURIComponent(ds)}`, { timeoutMs: 60_000 }),
    create: (body: SegCreate) => request<FlowRun>('/segmented/runs', { ...J(body), timeoutMs: 60_000 }),
    get: (id: string) => request<SegRun>(`/segmented/runs/${encodeURIComponent(id)}`),
  },
  surv: {
    detect: (ds: string) => request<SurvDetect>(`/survival/detect?dataset_id=${encodeURIComponent(ds)}`, { timeoutMs: 60_000 }),
    create: (body: SurvCreate) => request<FlowRun>('/survival/runs', { ...J(body), timeoutMs: 60_000 }),
    get: (id: string) => request<SurvRun>(`/survival/runs/${encodeURIComponent(id)}`),
    tryIt: (vid: string, row: Record<string, unknown>) => request<SurvTry>(`/registry/versions/${encodeURIComponent(vid)}/predict`, { ...J({ row }), timeoutMs: 60_000 }),
  },
  anomaly: {
    mapping: (ds: string) => request<AnGroup>(`/anomaly/mapping/${encodeURIComponent(ds)}`),
    create: (body: AnCreate) => request<FlowRun>('/anomaly/runs', { ...J(body), timeoutMs: 30_000 }),
    top: (id: string, contamination?: number, limit = 100) => request<AnTop>(`/anomaly/runs/${encodeURIComponent(id)}/top?limit=${limit}${contamination ? `&contamination=${contamination}` : ''}`),
    scores: (id: string) => request<AnScores>(`/anomaly/runs/${encodeURIComponent(id)}/scores`),
    labels: (id: string) => request<AnLabels>(`/anomaly/runs/${encodeURIComponent(id)}/labels`),
    review: (id: string, items: { row: number; label: 'fraud' | 'not_fraud' | null }[], by: string) =>
      request<AnLabels>(`/anomaly/runs/${encodeURIComponent(id)}/labels`, J({ items, by })),
    supervised: (id: string) => request<{ run_id: string; review: AnReviewSummary; runs: AnSupervised[] }>(`/anomaly/runs/${encodeURIComponent(id)}/supervised`, { timeoutMs: 120_000 }),
    trainSupervised: (id: string, body: AnSupervisedCreate) => request<AnSupervised & { run: FlowRun }>(`/anomaly/runs/${encodeURIComponent(id)}/supervised`, { ...J(body), timeoutMs: 60_000 }),
    score: (id: string, file: File) => {
      const f = new FormData(); f.append('file', file)
      return request<AnBatch>(`/anomaly/runs/${encodeURIComponent(id)}/score`, { method: 'POST', body: f, timeoutMs: 300_000 })
    },
  },
  importRun: (form: { dataset_id: string; file?: File | null; path?: string; name?: string; created_by?: string }) => {
    const f = new FormData()
    f.append('dataset_id', form.dataset_id)
    if (form.file) f.append('file', form.file)
    if (form.path) f.append('path', form.path)
    if (form.name) f.append('name', form.name)
    f.append('created_by', form.created_by ?? '')
    return request<FlowRun & { inspected: { label: string; problem_type: string; eval_metric: string; features: string[]; model_best: string; n_models: number; autogluon_version: string; models: { model: string; score_val: number; stack_level: number }[] } }>(
      '/runs/import', { method: 'POST', body: f, timeoutMs: 600_000 })
  },
  register: (runId: string, created_by: string, model_name?: string) =>
    request<{ id: string; status: string; model_name: string; version: number }>(`/runs/${encodeURIComponent(runId)}/register`, { ...J({ created_by, model_name }), timeoutMs: 30_000 }),
}

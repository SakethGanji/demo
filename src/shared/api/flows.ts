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
  defaults: { prediction_length: number; eval_metric: string; presets: string; time_limit: number; quantile_levels: number[]; num_val_windows: number }
}

export type FcPreview = {
  rows: number; items: number; observed_rows: number; future_rows: number; start: string; end: string
  freq: string | null; seasonal_period: number; length_min: number; length_max: number; static_not_constant: string[]
  missing_target_inside: number; suggested_prediction_length: number
  series: Record<string, { t: string[]; y: (number | null)[] }>
}

export type FcCreate = {
  dataset_id: string; mapping: FcMapping; name?: string; prediction_length: number; freq?: string | null; eval_metric: string
  presets: string | null; time_limit: number | null; quantile_levels: number[]; num_val_windows: number | null
  hyperparameters?: Record<string, unknown> | string | null; fit_kwargs: Record<string, unknown>; predictor_kwargs?: Record<string, unknown>; source?: string
}

export type FcBand = { t: string[]; mean: number[]; q: Record<string, number[]>; actual?: (number | null)[] }
export type FcChart = { run_id: string; quantiles: number[]; items: Record<string, { history: { t: string[]; y: (number | null)[] }; holdout?: FcBand; future?: FcBand }> }

export type FcItemError = { item_id: string; n: number; MASE: number | null; WQL: number | null; sMAPE: number; MAPE: number; RMSE: number; bias: number; coverage: number; interval: [number, number]; scale: number; mean_actual: number }

export type FcRunExtra = {
  forecast?: { model: string; horizon: number; start: string; end: string; rows: number; known_covariates_source: string; quantiles: number[] } | null
  backtests?: { windows: { window: number; start: string; end: string }[]; models: Record<string, { window: number; MASE: number | null; WQL: number | null; coverage: number | null }[]>; error?: string } | null
  holdout?: { model: string; metrics: Record<string, number>; coverage: number | null; interval: [number, number]; items: FcItemError[]; horizon: number; eval_metric?: string } | null
  data?: Record<string, unknown> & { items?: number; freq?: string; seasonal_period?: number; prediction_length?: number; start?: string; end?: string; train_end?: string; holdout_start?: string; known_covariates?: string[]; static_features?: string[] }
}

export type AnTruth = {
  planted: number; precision_at_k: { k: number; hits: number; precision: number; recall: number }[]; roc_auc: number | null
  average_precision: number | null; by_kind: Record<string, { planted: number; found: number }>; flagged: number; base_rate: number; note: string
}
export type AnReason = { feature: string; phi: number; push: number; value: unknown; z: number | null; median?: number | null; share: number | null }
export type AnRow = { rank: number; row: number; s: number; Eh: number; lof: number | null; flagged: boolean; planted?: boolean; values: Record<string, unknown>; phi0: number | null; phi_sum: number; reasons: AnReason[]; why: string }
export type AnSummary = {
  threshold: number; contamination: number; n_flagged: number; psi: number; c_n: number; n_trees: number; fit_seconds: number; columns_out: number; mean_depth: number
  distribution: { edges: number[]; counts: number[]; quantiles: Record<string, number>; min: number; max: number; mean: number }
  lof: { n_neighbors: number; spearman_vs_iforest: number; overlap_top: number } | null
  truth?: AnTruth; truth_lof?: AnTruth
}
export type AnTop = { run_id: string; contamination: number; threshold: number; n_flagged: number; rows: AnRow[]; truth: AnTruth | null; n_rows: number }
export type AnScores = { edges: number[]; counts: number[]; n: number; sorted_sample: number[]; scatter?: { row: number; s: number; lof: number }[] }
export type AnBatch = { id: string; filename: string; summary: { rows: number; n_flagged: number; threshold: number; flagged_share: number; expected_share: number; mean_score: number }; top: { row: number; s: number; percentile: number; flagged: boolean; reasons: AnReason[]; why: string }[]; histogram: { edges: number[]; counts: number[] }; download: string }
export type AnCreate = { dataset_id: string; features?: string[] | null; contamination: number; n_estimators: number; max_samples?: number | string; max_features?: number; lof: boolean; lof_neighbors?: number; iforest_kwargs?: Record<string, unknown>; random_state?: number; source?: string }

export type FlowRun = Omit<Run, 'holdout' | 'data'> & FcRunExtra & { kind?: string; anomaly?: AnSummary | null; imported?: Record<string, unknown> | null }

const J = (body: unknown) => ({ method: 'POST', body: JSON.stringify(body), headers: { 'Content-Type': 'application/json' } })

export const flows = {
  runs: (kind: 'forecast' | 'anomaly' | 'tabular' | 'all') => request<{ runs: FlowRun[] }>(`/runs?kind=${kind}`).then((r) => r.runs),
  run: (id: string) => request<FlowRun>(`/runs/${encodeURIComponent(id)}`),
  forecast: {
    options: () => request<FcOptions>('/forecast/options'),
    mapping: (ds: string) => request<{ mapping: FcMapping; source: string; prediction_length: number | null }>(`/forecast/mapping/${encodeURIComponent(ds)}`),
    preview: (dataset_id: string, mapping: FcMapping) => request<FcPreview>('/forecast/preview', { ...J({ dataset_id, mapping }), timeoutMs: 60_000 }),
    create: (body: FcCreate) => request<FlowRun>('/forecast/runs', { ...J(body), timeoutMs: 30_000 }),
    chart: (id: string) => request<FcChart>(`/forecast/runs/${encodeURIComponent(id)}/chart`, { timeoutMs: 30_000 }),
    csvUrl: (id: string, which: 'future' | 'holdout' = 'future') => `/api/forecast/runs/${encodeURIComponent(id)}/forecast.csv?which=${which}`,
    predict: (id: string, file: File) => {
      const f = new FormData(); f.append('file', file)
      return request<{ id: string; items: number; rows: number; seconds: number; known_covariates_source: string; chart: Omit<FcChart, 'run_id'>; model: string; download: string }>(
        `/forecast/runs/${encodeURIComponent(id)}/predict`, { method: 'POST', body: f, timeoutMs: 300_000 })
    },
  },
  anomaly: {
    create: (body: AnCreate) => request<FlowRun>('/anomaly/runs', { ...J(body), timeoutMs: 30_000 }),
    top: (id: string, contamination?: number, limit = 100) => request<AnTop>(`/anomaly/runs/${encodeURIComponent(id)}/top?limit=${limit}${contamination ? `&contamination=${contamination}` : ''}`),
    scores: (id: string) => request<AnScores>(`/anomaly/runs/${encodeURIComponent(id)}/scores`),
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

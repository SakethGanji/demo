// Typed client for the ENSEMBLE API. Every call goes through the Vite proxy at /api.

import type { AuditEntry, BatchJob, BatchListItem, LatencyProfile, ModelCard, ScoreResult, ServingBenchmark, ModelVersion, RegisteredModel, RegisterRequest, RegistryCounts, Stage, TryResult, ApiStatus, CustomEnsemble, DatasetDetail, DatasetSplits, DatasetSummary, Explain, Fairness, FeatureImportance, Health, HoldoutPredictions, ImportanceResponse, ModelFamily, Prediction, Run, RunCreate, RunEvent, ShapResult, ThresholdAnalysis, ThresholdRecord, Holdout, GymSchema, GymValidation, VelaResponse, VelaWording, Autopilot, ApInbox, ApRequirement, VelaAnswer, LlmStatus, LlmSettings, SplitComparison, SplitDescription, SplitOptions, SplitSpec, CostAnalysis, CostComparison, CostRequest, MonotoneCheck } from './types'

export class ApiError extends Error {
  /** 0 when the API could not be reached at all (network error, timeout, or the dev proxy's 503). */
  constructor(message: string, readonly status: number) { super(message) }
  get unreachable() { return this.status === 0 }
}

const BASE = '/api'

export async function request<T>(path: string, init: RequestInit & { timeoutMs?: number } = {}): Promise<T> {
  const { timeoutMs = 15_000, ...rest } = init
  const ctl = new AbortController()
  const timer = setTimeout(() => ctl.abort(), timeoutMs)
  let res: Response
  try {
    res = await fetch(`${BASE}${path}`, { ...rest, signal: ctl.signal, headers: { Accept: 'application/json', ...rest.headers } })
  } catch (e) {
    throw new ApiError((e as Error).name === 'AbortError' ? 'The API did not answer in time.' : 'The API is not reachable.', 0)
  } finally {
    clearTimeout(timer)
  }
  if (res.status === 503 && res.headers.get('X-Ensemble-Proxy') === 'api-unreachable') throw new ApiError('The API is not reachable.', 0)
  const body = await res.json().catch(() => null)
  if (!res.ok) {
    const detail = body && typeof body.detail === 'string' ? body.detail : `Request failed (${res.status}).`
    throw new ApiError(detail, res.status)
  }
  return body as T
}

export const api = {
  health: () => request<Health>('/health', { timeoutMs: 3_000 }),
  status: () => request<ApiStatus>('/status', { timeoutMs: 5_000 }),
  datasets: {
    list: () => request<{ datasets: DatasetSummary[] }>('/datasets').then((r) => r.datasets),
    get: (id: string) => request<DatasetDetail>(`/datasets/${encodeURIComponent(id)}`),
    upload: (file: File, target?: string) => {
      const form = new FormData()
      form.append('file', file)
      if (target) form.append('target', target)
      return request<DatasetDetail>('/datasets', { method: 'POST', body: form, timeoutMs: 120_000 })
    },
    /** F4: confirm the column to predict; the API re-profiles and draws (or reuses) that target's own holdout. */
    setTarget: (id: string, target: string) => request<DatasetDetail>(`/datasets/${encodeURIComponent(id)}`, { method: 'PATCH', body: JSON.stringify({ target }), headers: { 'Content-Type': 'application/json' }, timeoutMs: 120_000 }),
    splits: (id: string) => request<DatasetSplits>(`/datasets/${encodeURIComponent(id)}/splits`),
    /** F6: strategies, date and entity columns found, and warnings for a random split. */
    splitOptions: (id: string, target?: string) => request<SplitOptions>(`/datasets/${encodeURIComponent(id)}/split-options${target ? `?target=${encodeURIComponent(target)}` : ''}`, { timeoutMs: 60_000 }),
    /** F6: draw (or reuse) a split and describe it: rows, groups, dates per role; timeline for time splits. */
    drawSplit: (id: string, target: string, spec: SplitSpec) => request<SplitDescription>(`/datasets/${encodeURIComponent(id)}/splits`, { method: 'POST', body: JSON.stringify({ target, ...spec }), headers: { 'Content-Type': 'application/json' }, timeoutMs: 120_000 }),
  },
  models: () => request<{ families: ModelFamily[] }>('/models').then((r) => r.families),
  runs: {
    create: (body: RunCreate) => request<Run>('/runs', { method: 'POST', body: JSON.stringify(body), headers: { 'Content-Type': 'application/json' } }),
    /** Tabular runs and imports by default; kind 'all' adds forecast and anomaly runs. */
    list: (datasetId?: string, kind: 'tabular' | 'forecast' | 'anomaly' | 'all' = 'tabular') => {
      const q = new URLSearchParams(); if (datasetId) q.set('dataset_id', datasetId); if (kind !== 'tabular') q.set('kind', kind)
      return request<{ runs: Run[] }>(`/runs${q.toString() ? `?${q}` : ''}`).then((r) => r.runs)
    },
    get: (id: string) => request<Run>(`/runs/${encodeURIComponent(id)}`),
    /** F6: the same run under another split (identical body, only split_spec differs). */
    splitTwin: (id: string, split_spec: SplitSpec) => request<Run>(`/runs/${encodeURIComponent(id)}/split-twin`, { method: 'POST', body: JSON.stringify({ split_spec }), headers: { 'Content-Type': 'application/json' }, timeoutMs: 60_000 }),
    /** F6: this run's holdout score beside the same data and label under other splits. */
    splitComparison: (id: string) => request<SplitComparison>(`/runs/${encodeURIComponent(id)}/split-comparison`),
    /** F7: expected cost per 1,000 decisions (the run's own cost, or c_fp / c_fn for any binary run). */
    cost: (id: string, q?: { c_fp: number; c_fn: number; g_tp?: number }) =>
      request<CostAnalysis>(`/runs/${encodeURIComponent(id)}/cost${q ? `?c_fp=${q.c_fp}&c_fn=${q.c_fn}&g_tp=${q.g_tp ?? 0}` : ''}`, { timeoutMs: 120_000 }),
    /** F7: the same fit trained to other metrics, all costed with this run's cost on the same holdout rows. */
    costComparison: (id: string) => request<CostComparison>(`/runs/${encodeURIComponent(id)}/cost-comparison`, { timeoutMs: 180_000 }),
    /** F7: the same run trained to another metric (or cost); only the metric differs. */
    metricTwin: (id: string, body: { eval_metric?: string; cost?: CostRequest; calibrate_threshold?: string | null }) =>
      request<Run>(`/runs/${encodeURIComponent(id)}/metric-twin`, { method: 'POST', body: JSON.stringify(body), headers: { 'Content-Type': 'application/json' }, timeoutMs: 60_000 }),
    /** F8: ICE / PD of one model (default the best) for the run's constraints, or `constraints` ('col:increasing,…') for any run. */
    monotone: (id: string, q: { model?: string; constraints?: string; rows?: number; curves?: number } = {}) => {
      const p = new URLSearchParams()
      Object.entries(q).forEach(([k, v]) => { if (v != null && v !== '') p.set(k, String(v)) })
      return request<MonotoneCheck>(`/runs/${encodeURIComponent(id)}/monotone${p.toString() ? `?${p}` : ''}`, { timeoutMs: 120_000 })
    },
    cancel: (id: string) => request<{ id: string; cancelling: boolean }>(`/runs/${encodeURIComponent(id)}/cancel`, { method: 'POST' }),
    /** Events recorded so far, once (no stream). */
    events: (id: string, from = 0) => request<{ events: RunEvent[]; next: number }>(`/runs/${encodeURIComponent(id)}/events?format=json&from=${from}`),
    predict: (id: string, rows: Record<string, unknown>[], model?: string) =>
      request<Prediction>(`/runs/${encodeURIComponent(id)}/predict`, {
        method: 'POST', body: JSON.stringify({ rows, model }), headers: { 'Content-Type': 'application/json' }, timeoutMs: 60_000,
      }),
    /** Fit a weighted ensemble on the chosen base models (AutoGluon's fit_weighted_ensemble). */
    ensemble: (id: string, models: string[]) =>
      request<CustomEnsemble & { run_id: string }>(`/runs/${encodeURIComponent(id)}/ensemble`, {
        method: 'POST', body: JSON.stringify({ models }), headers: { 'Content-Type': 'application/json' }, timeoutMs: 600_000,
      }),
    holdoutPredictions: (id: string) => request<HoldoutPredictions>(`/runs/${encodeURIComponent(id)}/holdout-predictions`, { timeoutMs: 60_000 }),
    featureImportance: (id: string) =>
      request<{ run_id: string; features: FeatureImportance[] }>(`/runs/${encodeURIComponent(id)}/feature-importance`, { timeoutMs: 180_000 }),
    /** Permutation importance with 95% t-intervals; `shuffles` recomputes it with that many shuffle sets. */
    importance: (id: string, shuffles?: number) =>
      request<ImportanceResponse>(`/runs/${encodeURIComponent(id)}/feature-importance${shuffles ? `?shuffles=${shuffles}` : ''}`, { timeoutMs: 300_000 }),
    /** Binary: every threshold metric on a 0.005 grid, at t, and the cost C(t) = c_FP·FP + c_FN·FN. */
    threshold: (id: string, q: { costFp?: number; costFn?: number; t?: number } = {}) => {
      const p = new URLSearchParams({ cost_fp: String(q.costFp ?? 1), cost_fn: String(q.costFn ?? 1) })
      if (q.t != null) p.set('t', String(q.t))
      return request<ThresholdAnalysis>(`/runs/${encodeURIComponent(id)}/threshold?${p}`, { timeoutMs: 30_000 })
    },
    /** Calibrate (metric) or set (value) the decision threshold; AutoGluon keeps it in the predictor. */
    setThreshold: (id: string, body: { metric?: string; value?: number }) =>
      request<{ run_id: string; threshold: ThresholdRecord; holdout: Holdout }>(`/runs/${encodeURIComponent(id)}/threshold`, {
        method: 'POST', body: JSON.stringify(body), headers: { 'Content-Type': 'application/json' }, timeoutMs: 120_000,
      }),
    explain: (id: string) => request<Explain>(`/runs/${encodeURIComponent(id)}/explain`, { timeoutMs: 60_000 }),
    fairness: (id: string, group: string, t?: number) =>
      request<Fairness>(`/runs/${encodeURIComponent(id)}/fairness?group=${encodeURIComponent(group)}${t != null ? `&t=${t}` : ''}`, { timeoutMs: 60_000 }),
    shap: (id: string, row: number, target?: string) =>
      request<ShapResult>(`/runs/${encodeURIComponent(id)}/shap?row=${row}${target ? `&target=${encodeURIComponent(target)}` : ''}`, { timeoutMs: 180_000 }),
  },
  gym: {
    schema: () => request<GymSchema>('/gym/schema', { timeoutMs: 60_000 }),
    /** Validate a run body without starting it: errors, the resolved call and its diff, AutoGluon's choices, T̂. */
    validate: (body: RunCreate) => request<GymValidation>('/gym/validate', { method: 'POST', body: JSON.stringify(body), headers: { 'Content-Type': 'application/json' }, timeoutMs: 60_000 }),
    /** The body that starts a run again, and its lineage. */
    fromRun: (id: string) => request<{ run_id: string; name: string; status: string; body: RunCreate; parent_run_id: string | null; patch: RunCreate['patch'] }>(`/runs/${encodeURIComponent(id)}/gym`),
    exportUrl: (id: string, download = false) => `${BASE}/runs/${encodeURIComponent(id)}/export.py${download ? '?download=true' : ''}`,
    exportText: async (id: string) => {
      const r = await fetch(`${BASE}/runs/${encodeURIComponent(id)}/export.py`)
      if (!r.ok) throw new ApiError(`Export failed (${r.status}).`, r.status)
      return r.text()
    },
  },
  vela: {
    findings: (runId: string) => request<VelaResponse>(`/runs/${encodeURIComponent(runId)}/vela`, { timeoutMs: 120_000 }),
    word: (runId: string) => request<VelaWording>(`/runs/${encodeURIComponent(runId)}/vela/wording`, { method: 'POST', timeoutMs: 90_000 }),
    /** A free-text question about a run or an autopilot, answered from its computed facts (F5: Anthropic SDK → Claude CLI →
     *  none), cached by (prompt hash, facts hash). The server's total budget bounds it; the browser waits a little longer. */
    ask: (q: { question: string; run_id?: string | null; autopilot_id?: string | null; timeout?: number; cache?: boolean }) => json<VelaAnswer>('/vela/ask', 'POST', q, 660_000),
  },
  /** F5: the language-model router: route, availability, latency per decider, settings. */
  llm: {
    status: () => request<LlmStatus>('/llm', { timeoutMs: 5_000 }),
    update: (patch: Partial<Record<keyof LlmSettings, number | boolean | string | null>>) => json<LlmStatus>('/llm/settings', 'PUT', patch),
    clearCache: () => json<{ cleared: number }>('/llm/cache/clear', 'POST', {}),
  },
  autopilot: {
    start: (b: { dataset_id: string; label?: string; goal?: string; requirements: ApRequirement[]; budget_seconds: number; autonomy: 'ask' | 'auto'; auto_confidence?: number; decider: 'auto' | 'layer1'; allow_synthetic?: boolean }) =>
      json<Autopilot>('/autopilot', 'POST', b),
    list: () => request<{ autopilots: Autopilot[] }>('/autopilot').then((r) => r.autopilots),
    get: (id: string) => request<Autopilot>(`/autopilot/${encodeURIComponent(id)}`),
    inbox: (id: string) => request<ApInbox>(`/autopilot/${encodeURIComponent(id)}/inbox`, { timeoutMs: 30_000 }),
    answer: (id: string, answers: { id: string; answer: string | null; label?: string }[]) =>
      json<{ answered: number }>(`/autopilot/${encodeURIComponent(id)}/inbox/answer`, 'POST', { answers }),
    bulk: (id: string, min_confidence: number, action: 'accept' | 'skip' | 'clear' = 'accept', kinds?: string[]) =>
      json<{ answered: number }>(`/autopilot/${encodeURIComponent(id)}/inbox/bulk`, 'POST', { min_confidence, action, kinds }),
    resume: (id: string) => json<Autopilot>(`/autopilot/${encodeURIComponent(id)}/resume`, 'POST', {}),
    cancel: (id: string) => json<Autopilot>(`/autopilot/${encodeURIComponent(id)}/cancel`, 'POST', {}),
  },
  registry: {
    get: () => request<{ models: RegisteredModel[]; versions: ModelVersion[]; counts: RegistryCounts }>('/registry'),
    summary: () => request<{ counts: RegistryCounts; audit: AuditEntry[]; production: ModelVersion[] }>('/registry/summary'),
    /** Register a finished run as a new version; it builds in a worker (status 'building' → 'ready'). */
    register: (runId: string, body: RegisterRequest) => json<ModelVersion>(`/runs/${encodeURIComponent(runId)}/register`, 'POST', body),
    /** Every model of the run: score, measured µs per row, models and bytes it needs. */
    latency: (runId: string) => request<LatencyProfile>(`/runs/${encodeURIComponent(runId)}/latency`, { timeoutMs: 180_000 }),
    version: (id: string) => request<ModelVersion>(`/registry/versions/${encodeURIComponent(id)}`),
    /** F3: the online path (no SHAP), timed by the predictor, the worker and the server. */
    score: (id: string, rows: Record<string, unknown>[]) => json<ScoreResult>(`/registry/versions/${encodeURIComponent(id)}/score`, 'POST', { rows }),
    /** F3: one-row p50 / p95 and batches of 1 → 1,000 as served, in a predict worker. */
    benchmark: (id: string) => json<ServingBenchmark & { version_id: string }>(`/registry/versions/${encodeURIComponent(id)}/benchmark`, 'POST', {}),
    transition: (id: string, stage: Stage, actor: string, comment = '') =>
      json<{ version: ModelVersion; audit: AuditEntry; archived: string[] }>(`/registry/versions/${encodeURIComponent(id)}/transition`, 'POST', { stage, actor, comment }),
    review: (id: string, decision: 'approve' | 'reject', reviewer: string, comment = '') =>
      json<{ version: ModelVersion; audit: AuditEntry }>(`/registry/versions/${encodeURIComponent(id)}/review`, 'POST', { decision, reviewer, comment }),
    audit: (q: { model?: string; versionId?: string; limit?: number } = {}) => {
      const p = new URLSearchParams()
      if (q.model) p.set('model', q.model)
      if (q.versionId) p.set('version_id', q.versionId)
      if (q.limit) p.set('limit', String(q.limit))
      return request<{ entries: AuditEntry[] }>(`/registry/audit?${p}`).then((r) => r.entries)
    },
    card: (id: string) => request<ModelCard>(`/registry/versions/${encodeURIComponent(id)}/card`, { timeoutMs: 60_000 }),
    /** F8: ICE / PD of the model the version serves (its own files) for the run's monotone constraints. */
    monotone: (id: string) => request<MonotoneCheck>(`/registry/versions/${encodeURIComponent(id)}/monotone`, { timeoutMs: 120_000 }),
    cardUrl: (id: string, format: 'md' | 'html') => `${BASE}/registry/versions/${encodeURIComponent(id)}/card?format=${format}&download=true`,
    editCard: (id: string, actor: string, fields: Record<string, string>) =>
      json<{ card: Record<string, string>; changed: string[] }>(`/registry/versions/${encodeURIComponent(id)}/card`, 'PUT', { actor, fields }),
    mlflow: (id: string) => request<Record<string, unknown>>(`/registry/versions/${encodeURIComponent(id)}/mlflow`, { timeoutMs: 60_000 }),
    /** Try It: the prediction, probabilities and Kernel SHAP reasons for one row (computed in a worker). */
    tryIt: (id: string, row: Record<string, unknown>, target?: string) =>
      json<TryResult>(`/registry/versions/${encodeURIComponent(id)}/predict`, 'POST', { row, explain: true, target }, 180_000),
    batches: (id: string) => request<{ batches: BatchListItem[] }>(`/registry/versions/${encodeURIComponent(id)}/batches`).then((r) => r.batches),
    batch: (bid: string) => request<BatchJob>(`/registry/batches/${encodeURIComponent(bid)}`, { timeoutMs: 30_000 }),
    batchUrl: (bid: string) => `${BASE}/registry/batches/${encodeURIComponent(bid)}/download`,
    scoreBatch: (id: string, file: File, createdBy: string) => {
      const form = new FormData()
      form.append('file', file)
      form.append('created_by', createdBy)
      return request<BatchJob>(`/registry/versions/${encodeURIComponent(id)}/batches`, { method: 'POST', body: form, timeoutMs: 120_000 })
    },
  },
}

function json<T>(path: string, method: string, body: unknown, timeoutMs = 60_000) {
  return request<T>(path, { method, body: JSON.stringify(body), headers: { 'Content-Type': 'application/json' }, timeoutMs })
}

/**
 * Follow a run's events live (SSE). Replays from `from` (default 0), so a late subscriber sees the whole run.
 * EventSource reconnects by itself with Last-Event-ID on a dropped connection; if it gives up (the API was down and
 * the proxy answered 503), this opens a new stream from the next unseen seq after `retryMs`. Events are delivered
 * once each, in order. The stream closes after run_succeeded / run_failed / run_cancelled. Returns a function that
 * stops listening.
 */
export function followRun(id: string, onEvent: (e: RunEvent) => void,
  opts: { from?: number; onEnd?: () => void; onError?: () => void; onOpen?: () => void; retryMs?: number } = {}) {
  const terminal = new Set(['run_succeeded', 'run_failed', 'run_cancelled'])
  const types = ['run_queued', 'run_started', 'data_ready', 'config_resolved', 'fit_started', 'model_started', 'model_finished',
    'model_failed', 'ensemble', 'fit_finished', 'leaderboard', 'evaluation', 'text_models', 'explaining', 'feature_importance',
    'log', 'calibrating', 'threshold', 'tracked', 'post_fit', 'forecast_ready', 'importing', 'run_succeeded', 'run_failed', 'run_cancelled']
  let next = opts.from ?? 0
  let ended = false
  let es: EventSource | null = null
  let timer: ReturnType<typeof setTimeout> | undefined
  const open = () => {
    if (ended) return
    const src = new EventSource(`${BASE}/runs/${encodeURIComponent(id)}/events?from=${next}`)
    es = src
    const handle = (msg: MessageEvent) => {
      const e = JSON.parse(msg.data) as RunEvent
      if (e.seq < next) return // already delivered (a replay overlapping what we had)
      next = e.seq + 1
      onEvent(e)
      if (terminal.has(e.type)) { ended = true; src.close(); opts.onEnd?.() }
    }
    for (const t of types) src.addEventListener(t, handle as EventListener)
    src.onopen = () => opts.onOpen?.()
    src.onerror = () => {
      if (ended) return
      opts.onError?.()
      if (src.readyState === EventSource.CLOSED) timer = setTimeout(open, opts.retryMs ?? 2000)
    }
  }
  open()
  return () => { ended = true; es?.close(); clearTimeout(timer) }
}

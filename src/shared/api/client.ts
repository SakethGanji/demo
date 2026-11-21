// Typed client for the ENSEMBLE API. Every call goes through the Vite proxy at /api.

import type { AuditEntry, BatchJob, BatchListItem, LatencyProfile, ModelCard, ScoreResult, ServingBenchmark, ModelVersion, RegisteredModel, RegisterRequest, RegistryCounts, Stage, TryResult, ApiStatus, CustomEnsemble, DataFix, DataFixResult, DataReport, DatasetDetail, DatasetSplits, Intake, DatasetSummary, Explain, FairLending, Fairness, Health, HoldoutPredictions, ImportanceResponse, ModelFamily, Prediction, Run, RunCreate, RunEvent, ShapResult, ThresholdAnalysis, ThresholdRecord, Holdout, GymSchema, GymValidation, VelaResponse, VelaWording, Autopilot, ApInbox, ApRequirement, VelaAnswer, LlmStatus, LlmSettings, SplitComparison, SplitDescription, SplitOptions, SplitSpec, CostAnalysis, CostComparison, CostRequest, MonotoneCheck, ScorecardReport, ScorecardScore, Ladder, FeatureLabJob, FeatureLabList, FeatureTransform, LearningCurve, LearningCurveRequest, ActiveLearning, ALRequest, Annotations, ShiftAction, ShiftCheck, ShiftList, ShiftRequest, SliceRequest, SliceRows, SliceSearch, SliceShap, SlicePool, ReasonsConfig, ReasonMapping, ReasonStability, RecourseAction, RecourseCheck, RecourseConfig, RecourseRows, Doctor, MonitoringState, MonitoringConfig, RetrainProposal, StreamSpec, StreamFeedResult, CreditReport, SqlDemo, SqlTable, SqlPreview, SqlLineage, SqlVersions, AgentSession, AgentSessionSummary, AgentStatus, Review, ReviewList, ChangesState, ChangeItem } from './types'

/** Who is acting (typed once in the Registry; there is no sign-in): recorded in the audit log. */
const actor = () => { try { return localStorage.getItem('ensemble.actor') || undefined } catch { return undefined } }

export class ApiError extends Error {
  /** 0 when the API could not be reached at all (network error, timeout, or the dev proxy's 503). */
  /** U1: `detail` is the API's structured refusal ({code, message, …}) when it sent one. */
  constructor(message: string, readonly status: number, readonly detail?: unknown) { super(message) }
  get unreachable() { return this.status === 0 }
}

const BASE = '/api'

export type RetireRequest = { id: string; version_id: string; status: 'pending' | 'approved' | 'rejected' | 'withdrawn'; requested_by: string; requested_at: string; reason: string; decided_by: string | null; decided_at: string | null; comment: string }

type SqlQuery = { url: string; query: string; limit?: number; password?: string }

export async function request<T>(path: string, init: RequestInit & { timeoutMs?: number } = {}): Promise<T> {
  const { timeoutMs = 15_000, ...rest } = init
  const ctl = new AbortController()
  const timer = setTimeout(() => ctl.abort(), timeoutMs)
  let res: Response
  try {
    // DL2: the acting name reaches the audit log of reads and of paths that take no `by` (header values must be Latin-1)
    const who = actor(); const hdr: Record<string, string> = who && /^[\x20-\x7e]+$/.test(who) ? { 'X-Ensemble-Actor': who } : {}
    res = await fetch(`${BASE}${path}`, { ...rest, signal: ctl.signal, headers: { Accept: 'application/json', ...hdr, ...rest.headers } })
  } catch (e) {
    throw new ApiError((e as Error).name === 'AbortError' ? 'The API did not answer in time.' : 'The API is not reachable.', 0)
  } finally {
    clearTimeout(timer)
  }
  if (res.status === 503 && res.headers.get('X-Ensemble-Proxy') === 'api-unreachable') throw new ApiError('The API is not reachable.', 0)
  const body = await res.json().catch(() => null)
  if (!res.ok) {
    // DL1: a refusal with a list of reasons ({message, blockers}) keeps them, as JSON the caller can read back
    const d = body?.detail
    const detail = typeof d === 'string' ? d : d && typeof d === 'object' && Array.isArray(d.blockers) ? JSON.stringify(d)
      : d && typeof d === 'object' && typeof d.message === 'string' ? d.message : `Request failed (${res.status}).`
    throw new ApiError(detail, res.status, d)
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
      const by = actor(); if (by) form.append('by', by)  // DL1: who uploaded it (audit, the version history)
      return request<DatasetDetail>('/datasets', { method: 'POST', body: form, timeoutMs: 120_000 })
    },
    /** G9: a dataset from a read-only SQL query (SQLite/DuckDB files on the server, or Postgres). The password is sent, never stored. */
    sql: {
      demo: () => request<SqlDemo>('/sql/demo'),
      tables: (url: string, password?: string) => json<{ tables: SqlTable[] }>('/sql/tables', 'POST', { url, password }),
      preview: (q: SqlQuery) => json<SqlPreview>('/sql/preview', 'POST', q, 120_000),
      create: (q: SqlQuery & { name?: string }) => json<DatasetDetail>('/sql/datasets', 'POST', { ...q, created_by: actor() }, 300_000),
      refresh: (id: string, password?: string) => json<DatasetDetail & { refresh: { parent: string; version: number; diff: SqlLineage['diff']; unchanged: boolean } }>(`/sql/datasets/${encodeURIComponent(id)}/refresh`, 'POST', { password, created_by: actor() }, 300_000),
      versions: (id: string) => request<SqlVersions>(`/sql/datasets/${encodeURIComponent(id)}/versions`),
    },
    /** F4: confirm the column to predict; the API re-profiles and draws (or reuses) that target's own holdout. */
    setTarget: (id: string, target: string) => request<DatasetDetail>(`/datasets/${encodeURIComponent(id)}`, { method: 'PATCH', body: JSON.stringify({ target }), headers: { 'Content-Type': 'application/json' }, timeoutMs: 120_000 }),
    /** G0 Smart Intake: column roles and what can be built; `roles` applies the user's edits. */
    intake: (id: string, roles?: Record<string, string>) => request<Intake>(`/datasets/${encodeURIComponent(id)}/intake`, roles ? { method: 'POST', body: JSON.stringify({ roles }), headers: { 'Content-Type': 'application/json' }, timeoutMs: 60_000 } : { timeoutMs: 60_000 }),
    /** D1 Data Doctor: every check with its rule, numbers, example rows and fix; the decision-time question. */
    doctor: (id: string) => request<DataReport>(`/datasets/${encodeURIComponent(id)}/data-doctor`, { timeoutMs: 120_000 }),
    /** D1: apply typed fixes into a new dataset version (lineage kept; the original is never changed). */
    applyFixes: (id: string, transforms: DataFix[]) => json<DataFixResult>(`/datasets/${encodeURIComponent(id)}/data-doctor/apply`, 'POST', { transforms, by: actor() }, 300_000),
    /** D1: store 'known at decision time?' and fairness-only answers; later runs drop those columns. */
    decisionTime: (id: string, known: Record<string, boolean | null>, fairness_only: string[]) => json<{ answered_at: string }>(`/datasets/${encodeURIComponent(id)}/decision-time`, 'POST', { known, fairness_only, by: actor() }),
    /** D2 Feature Lab: the dataset's searches (newest first) and the typed DSL. */
    featureLab: (id: string) => request<FeatureLabList>(`/datasets/${encodeURIComponent(id)}/feature-lab`),
    featureLabStart: (id: string, body: { target?: string; entity?: string | null; candidates?: FeatureTransform[]; max_candidates?: number; time_limit?: number }) =>
      json<FeatureLabJob>(`/datasets/${encodeURIComponent(id)}/feature-lab`, 'POST', { ...body, by: actor() }, 60_000),
    featureLabJob: (id: string, job: string) => request<FeatureLabJob>(`/datasets/${encodeURIComponent(id)}/feature-lab/${encodeURIComponent(job)}`),
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
    /** F11: the scorecard challenger: table, IV, holdout AUC vs the served model, ΔAUC with a bootstrap CI. */
    scorecard: (id: string) => request<ScorecardReport>(`/runs/${encodeURIComponent(id)}/scorecard`),
    /** D2: the model ladder: baseline → scorecard → single GBM → stack on one holdout, and the rung to serve. */
    ladder: (id: string) => request<Ladder>(`/runs/${encodeURIComponent(id)}/ladder`, { timeoutMs: 180_000 }),
    /** F12: the run's learning curve (status 'none' with the defaults until one is started). */
    learningCurve: (id: string) => request<LearningCurve>(`/runs/${encodeURIComponent(id)}/learning-curve`),
    /** F12: refit the run's exact config on nested subsamples of its training rows, in a worker process. */
    startLearningCurve: (id: string, body: LearningCurveRequest = {}) =>
      request<LearningCurve>(`/runs/${encodeURIComponent(id)}/learning-curve`, { method: 'POST', body: JSON.stringify(body), headers: { 'Content-Type': 'application/json' } }),
    cancelLearningCurve: (id: string) => request<{ cancelled: boolean }>(`/runs/${encodeURIComponent(id)}/learning-curve/cancel`, { method: 'POST' }),
    /** F13: the active-learning labelling queue, its rounds and the simulated active-vs-random comparison. */
    activeLearning: (id: string) => request<ActiveLearning>(`/runs/${encodeURIComponent(id)}/active-learning`),
    startActiveLearning: (id: string, body: ALRequest = {}) =>
      request<ActiveLearning>(`/runs/${encodeURIComponent(id)}/active-learning`, { method: 'POST', body: JSON.stringify(body), headers: { 'Content-Type': 'application/json' } }),
    answerActiveLearning: (id: string, answers: { id: string; answer: 'confirm' | 'relabel' | 'skip' | null; label?: string }[], reviewer = 'you') =>
      request<{ answered: number }>(`/runs/${encodeURIComponent(id)}/active-learning/answer`, { method: 'POST', body: JSON.stringify({ answers, reviewer }), headers: { 'Content-Type': 'application/json' } }),
    simulateActiveLearning: (id: string) => request<{ answered: number }>(`/runs/${encodeURIComponent(id)}/active-learning/simulate`, { method: 'POST' }),
    submitActiveLearning: (id: string) => request<{ round: number }>(`/runs/${encodeURIComponent(id)}/active-learning/submit`, { method: 'POST' }),
    cancelActiveLearning: (id: string) => request<{ cancelled: boolean }>(`/runs/${encodeURIComponent(id)}/active-learning/cancel`, { method: 'POST' }),
    /** F17: the Model Doctor (latency_ms: one-row budget; measure_latency: measure the served latency when missing). */
    doctor: (id: string, q: { latency_ms?: number | null; measure_latency?: boolean } = {}) => {
      const p = new URLSearchParams()
      if (q.latency_ms) p.set('latency_ms', String(q.latency_ms))
      if (q.measure_latency) p.set('measure_latency', 'true')
      return request<Doctor>(`/runs/${encodeURIComponent(id)}/doctor${p.toString() ? `?${p}` : ''}`, { timeoutMs: 600_000 })
    },
    /** D4: the review loop (roles → hypotheses → typed experiments → journal → improvement plan). */
    reviews: (id: string) => request<ReviewList>(`/runs/${encodeURIComponent(id)}/review`, { timeoutMs: 30_000 }),
    review: (id: string, rid: string) => request<Review>(`/runs/${encodeURIComponent(id)}/review/${encodeURIComponent(rid)}`, { timeoutMs: 30_000 }),
    startReview: (id: string, body: { max_experiments?: number; max_minutes?: number; offline?: boolean } = {}) =>
      json<Review>(`/runs/${encodeURIComponent(id)}/review`, 'POST', { by: actor(), ...body }, 60_000),
    /** F17: run one of the doctor's offers or prescriptions that is an API call (its endpoint, method and body). */
    doctorCall: (endpoint: string, method: 'GET' | 'POST' = 'POST', body?: Record<string, unknown>) =>
      request<Record<string, unknown>>(endpoint.replace(/^\/api/, ''), method === 'GET' ? { timeoutMs: 600_000 } : { method: 'POST', body: JSON.stringify(body ?? {}), headers: { 'Content-Type': 'application/json' }, timeoutMs: 600_000 }),
    /** F16: weak slices of the holdout (the latest search; searched with the defaults if there is none). */
    slices: (id: string) => request<SliceSearch>(`/runs/${encodeURIComponent(id)}/slices`, { timeoutMs: 60_000 }),
    searchSlices: (id: string, body: SliceRequest = {}) =>
      request<SliceSearch>(`/runs/${encodeURIComponent(id)}/slices`, { method: 'POST', body: JSON.stringify(body), headers: { 'Content-Type': 'application/json' }, timeoutMs: 120_000 }),
    sliceRows: (id: string, sid: string, limit?: number) =>
      request<SliceRows>(`/runs/${encodeURIComponent(id)}/slices/${encodeURIComponent(sid)}/rows${limit ? `?limit=${limit}` : ''}`, { timeoutMs: 60_000 }),
    sliceShap: (id: string, sid: string, rows = 6) =>
      request<SliceShap>(`/runs/${encodeURIComponent(id)}/slices/${encodeURIComponent(sid)}/shap`, { method: 'POST', body: JSON.stringify({ rows }), headers: { 'Content-Type': 'application/json' }, timeoutMs: 600_000 }),
    slicePool: (id: string, sid: string) => request<SlicePool>(`/runs/${encodeURIComponent(id)}/slices/${encodeURIComponent(sid)}/pool`),
    /** F15: the run's shift checks (adversarial validation) and what a new one can compare. */
    shifts: (id: string) => request<ShiftList>(`/runs/${encodeURIComponent(id)}/shift`),
    shift: (id: string, key: string) => request<ShiftCheck>(`/runs/${encodeURIComponent(id)}/shift/${encodeURIComponent(key)}`),
    startShift: (id: string, body: ShiftRequest) =>
      request<ShiftCheck>(`/runs/${encodeURIComponent(id)}/shift`, { method: 'POST', body: JSON.stringify(body), headers: { 'Content-Type': 'application/json' }, timeoutMs: 120_000 }),
    cancelShift: (id: string, key: string) => request<{ cancelled: boolean }>(`/runs/${encodeURIComponent(id)}/shift/${encodeURIComponent(key)}/cancel`, { method: 'POST' }),
    shiftWeightsVersion: (id: string, key: string) =>
      request<{ dataset_version: NonNullable<ShiftCheck['versions']>[number]; patch: NonNullable<ShiftAction['patch']> }>(`/runs/${encodeURIComponent(id)}/shift/${encodeURIComponent(key)}/weights-version${actor() ? `?by=${encodeURIComponent(actor()!)}` : ''}`, { method: 'POST', timeoutMs: 60_000 }),
    /** F14: annotators' agreement, quality, the CROWDLAB consensus and the disagreement queue. */
    annotations: (id: string, q: { offset?: number; limit?: number; include?: 'queue' | 'all' } = {}) => {
      const p = new URLSearchParams()
      Object.entries(q).forEach(([k, v]) => { if (v != null) p.set(k, String(v)) })
      return request<Annotations>(`/runs/${encodeURIComponent(id)}/annotations${p.toString() ? `?${p}` : ''}`)
    },
    loadSampleAnnotations: (id: string, body: { n_rows?: number; per_row?: number; seed?: number } = {}) =>
      request<Annotations>(`/runs/${encodeURIComponent(id)}/annotations/sample`, { method: 'POST', body: JSON.stringify(body), headers: { 'Content-Type': 'application/json' } }),
    uploadAnnotations: (id: string, file: File, mode: 'replace' | 'append' = 'replace') => {
      const form = new FormData()
      form.append('file', file)
      form.append('mode', mode)
      return request<Annotations>(`/runs/${encodeURIComponent(id)}/annotations/upload`, { method: 'POST', body: form })
    },
    answerAnnotations: (id: string, answers: { row: number; action: 'vote' | 'adjudicate' | 'clear'; label?: string }[], reviewer: string) =>
      request<{ answered: number }>(`/runs/${encodeURIComponent(id)}/annotations/answer`, { method: 'POST', body: JSON.stringify({ answers, reviewer }), headers: { 'Content-Type': 'application/json' } }),
    simulateAdjudication: (id: string, n = 50) =>
      request<{ adjudicated: number }>(`/runs/${encodeURIComponent(id)}/annotations/simulate-adjudication`, { method: 'POST', body: JSON.stringify({ n }), headers: { 'Content-Type': 'application/json' } }),
    annotationsVersion: (id: string) =>
      request<{ dataset_version: Annotations['versions'][number] }>(`/runs/${encodeURIComponent(id)}/annotations/version`, { method: 'POST' }),
    /** F11: the deployable scorecard on holdout rows (by position): points per feature, score, P, reason codes. */
    scorecardScore: (id: string, holdout_index: number[], top_k = 4) =>
      request<ScorecardScore>(`/runs/${encodeURIComponent(id)}/scorecard/score`, { method: 'POST', body: JSON.stringify({ holdout_index, top_k }), headers: { 'Content-Type': 'application/json' }, timeoutMs: 60_000 }),
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
    fairLending: (id: string, q: { surname?: string; zip?: string; truth?: string } = {}) =>
      request<FairLending>(`/runs/${encodeURIComponent(id)}/fair-lending?${new URLSearchParams(Object.entries(q).filter(([, v]) => v != null) as [string, string][])}`, { timeoutMs: 90_000 }),
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
  /** G10: the Vela agent. Conversations run on the server; the page follows them with `get(id, rev)`. */
  agent: {
    status: () => request<AgentStatus>('/agent/status', { timeoutMs: 5_000 }),
    list: () => request<{ sessions: AgentSessionSummary[] }>('/agent/sessions').then((r) => r.sessions),
    start: (message: string, user: string) => json<AgentSession>('/agent/sessions', 'POST', { message, user }),
    get: (id: string, since?: number) => request<AgentSession>(`/agent/sessions/${encodeURIComponent(id)}${since != null ? `?since=${since}` : ''}`),
    say: (id: string, message: string) => json<AgentSession>(`/agent/sessions/${encodeURIComponent(id)}/messages`, 'POST', { message }),
    confirm: (id: string, step_id: string, approve: boolean) => json<AgentSession>(`/agent/sessions/${encodeURIComponent(id)}/confirm`, 'POST', { step_id, approve }),
    stop: (id: string) => json<AgentSession>(`/agent/sessions/${encodeURIComponent(id)}/stop`, 'POST', {}),
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
    /** DL2: retire a version (four eyes; refused while champion or in Production). */
    retireState: (id: string) => request<{ requests: RetireRequest[] }>(`/registry/versions/${encodeURIComponent(id)}/retire`),
    retire: (id: string, by: string, reason: string) => json<RetireRequest & { version?: ModelVersion }>(`/registry/versions/${encodeURIComponent(id)}/retire`, 'POST', { by, reason }),
    decideRetire: (rid: string, approve: boolean, by: string, comment = '') => json<RetireRequest & { version?: ModelVersion }>(`/registry/version-requests/${encodeURIComponent(rid)}/decide`, 'POST', { approve, by, comment }),
    /** F3: the online path (no SHAP), timed by the predictor, the worker and the server. */
    score: (id: string, rows: Record<string, unknown>[]) => json<ScoreResult>(`/registry/versions/${encodeURIComponent(id)}/score`, 'POST', { rows }),
    /** F3: one-row p50 / p95 and batches of 1 → 1,000 as served, in a predict worker. */
    benchmark: (id: string) => json<ServingBenchmark & { version_id: string }>(`/registry/versions/${encodeURIComponent(id)}/benchmark`, 'POST', {}),
    transition: (id: string, stage: Stage, actor: string, comment = '', override_reason = '') =>
      json<{ version: ModelVersion; audit: AuditEntry; archived: string[] }>(`/registry/versions/${encodeURIComponent(id)}/transition`, 'POST', { stage, actor, comment, ...(override_reason ? { override_reason } : {}) }),
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
    cardUrl: (id: string, format: 'md' | 'html') => `${BASE}/registry/versions/${encodeURIComponent(id)}/card?format=${format}&download=true`,
    editCard: (id: string, actor: string, fields: Record<string, string>) =>
      json<{ card: Record<string, string>; changed: string[] }>(`/registry/versions/${encodeURIComponent(id)}/card`, 'PUT', { actor, fields }),
    /** F10: reason codes: the adverse class, the served threshold, the attribution method and the mapping table. */
    reasons: (id: string) => request<ReasonsConfig>(`/registry/versions/${encodeURIComponent(id)}/reasons`, { timeoutMs: 120_000 }),
    editReasons: (id: string, actor: string, body: { mapping?: Partial<ReasonMapping>[]; adverse_class?: string; reference?: 'approved' | 'population' }) =>
      json<ReasonsConfig>(`/registry/versions/${encodeURIComponent(id)}/reasons`, 'PUT', { actor, ...body }, 120_000),
    reasonStability: (id: string, body: { rows?: number; noise?: number; repeats?: number } = {}) =>
      json<ReasonStability & { version_id: string }>(`/registry/versions/${encodeURIComponent(id)}/reasons/stability`, 'POST', body, 600_000),
    /** G1: what a counterfactual may change, the acceptance check, and counterfactuals for posted rows. */
    recourse: (id: string) => request<RecourseConfig>(`/registry/versions/${encodeURIComponent(id)}/recourse`, { timeoutMs: 120_000 }),
    editRecourse: (id: string, actor: string, actions: Record<string, RecourseAction>) =>
      json<RecourseConfig>(`/registry/versions/${encodeURIComponent(id)}/recourse`, 'PUT', { actor, actions }, 120_000),
    recourseRows: (id: string, rows: Record<string, unknown>[]) =>
      json<RecourseRows>(`/registry/versions/${encodeURIComponent(id)}/recourse`, 'POST', { rows }, 300_000),
    recourseCheck: (id: string, rows = 120) =>
      json<RecourseCheck & { version_id: string }>(`/registry/versions/${encodeURIComponent(id)}/recourse/check`, 'POST', { rows }, 900_000),
    /** Try It: the prediction, probabilities and Kernel SHAP reasons for one row (computed in a worker). */
    tryIt: (id: string, row: Record<string, unknown>, target?: string) =>
      json<TryResult>(`/registry/versions/${encodeURIComponent(id)}/predict`, 'POST', { row, explain: true, target }, 180_000),
    batches: (id: string) => request<{ batches: BatchListItem[] }>(`/registry/versions/${encodeURIComponent(id)}/batches`).then((r) => r.batches),
    batch: (bid: string) => request<BatchJob>(`/registry/batches/${encodeURIComponent(bid)}`, { timeoutMs: 30_000 }),
    /** F15: the batch's shift check (adversarial AUC next to PSI); status 'none' until started. */
    batchShift: (bid: string) => request<ShiftCheck | { batch_id: string; status: 'none'; run_id?: string; key?: string }>(`/registry/batches/${encodeURIComponent(bid)}/shift`),
    startBatchShift: (bid: string) => request<ShiftCheck>(`/registry/batches/${encodeURIComponent(bid)}/shift`, { method: 'POST', body: '{}', headers: { 'Content-Type': 'application/json' }, timeoutMs: 120_000 }),
    batchUrl: (bid: string) => `${BASE}/registry/batches/${encodeURIComponent(bid)}/download`,
    scoreBatch: (id: string, file: File, createdBy: string, dataDate?: string) => {
      const form = new FormData()
      form.append('file', file)
      form.append('created_by', createdBy)
      if (dataDate) form.append('data_date', dataDate)
      return request<BatchJob>(`/registry/versions/${encodeURIComponent(id)}/batches`, { method: 'POST', body: form, timeoutMs: 120_000 })
    },
    /** G2: the credit-risk model report; its export links; a performance tape for the vintage curves. */
    creditReport: (id: string, q: { on?: 'holdout' | 'batches'; date_column?: string; cohort?: string } = {}) =>
      request<CreditReport>(`/registry/versions/${encodeURIComponent(id)}/credit-report?${new URLSearchParams(Object.entries(q).filter(([, v]) => v) as [string, string][])}`, { timeoutMs: 120_000 }),
    creditReportUrl: (id: string, format: 'md' | 'html') => `${BASE}/registry/versions/${encodeURIComponent(id)}/credit-report?format=${format}&download=true`,
    uploadCreditTape: (id: string, file: File, actor: string) => {
      const form = new FormData()
      form.append('file', file)
      form.append('actor', actor)
      return request<{ tape: { mapping: Record<string, unknown>; rows: number }; cohorts: number }>(`/registry/versions/${encodeURIComponent(id)}/credit-report/tape`, { method: 'POST', body: form, timeoutMs: 120_000 })
    },
    removeCreditTape: (id: string, actor: string) =>
      request<{ removed: boolean }>(`/registry/versions/${encodeURIComponent(id)}/credit-report/tape?actor=${encodeURIComponent(actor)}`, { method: 'DELETE' }),
    /** D4: post-deployment changes on the newest labelled batch; a better result becomes a request (a new version). */
    changes: (id: string) => request<ChangesState>(`/registry/versions/${encodeURIComponent(id)}/changes`, { timeoutMs: 300_000 }),
    startChange: (id: string, actorName: string, kind: string, column?: string) =>
      json<ChangeItem>(`/registry/versions/${encodeURIComponent(id)}/changes`, 'POST', { actor: actorName, kind, ...(column ? { column } : {}) }, 300_000),
    requestChange: (id: string, cid: string, actorName: string) =>
      json<{ change: ChangeItem; version: ModelVersion }>(`/registry/versions/${encodeURIComponent(id)}/changes/${encodeURIComponent(cid)}/request`, 'POST', { actor: actorName }, 120_000),
    /** F18: monitoring over time (series, alerts, challenger, retrain proposal). */
    monitoring: (id: string) => request<MonitoringState>(`/registry/versions/${encodeURIComponent(id)}/monitoring`, { timeoutMs: 60_000 }),
    putMonitoring: (model: string, body: { actor: string; thresholds?: Record<string, number>; challenger_version_id?: string; id_column?: string; label_column?: string; retrain_window_months?: number }) =>
      json<{ model: string; config: MonitoringConfig; changed: Record<string, unknown> }>(`/registry/models/${encodeURIComponent(model)}/monitoring`, 'PUT', body),
    uploadLabels: (bid: string, file: File, createdBy: string, idColumn?: string, labelColumn?: string, labelDate?: string) => {
      const form = new FormData()
      form.append('file', file)
      form.append('created_by', createdBy)
      if (idColumn) form.append('id_column', idColumn)
      if (labelColumn) form.append('label_column', labelColumn)
      if (labelDate) form.append('label_date', labelDate)
      return request<{ batch_id: string; labels: Record<string, unknown> }>(`/registry/batches/${encodeURIComponent(bid)}/labels`, { method: 'POST', body: form, timeoutMs: 120_000 })
    },
    retrainProposal: (id: string, windowMonths?: number) =>
      request<RetrainProposal>(`/registry/versions/${encodeURIComponent(id)}/monitoring/retrain${windowMonths ? `?window_months=${windowMonths}` : ''}`, { timeoutMs: 60_000 }),
    launchRetrain: (id: string, actor: string, windowMonths?: number) =>
      json<{ proposal: RetrainProposal; dataset: { id: string; rows: number; name: string }; run: { id: string; status: string } }>(`/registry/versions/${encodeURIComponent(id)}/monitoring/retrain`, 'POST', { actor, window_months: windowMonths }, 120_000),
    stream: () => request<StreamSpec>('/monitoring/stream'),
    feedStream: (id: string, month: number, actor: string) =>
      json<StreamFeedResult>(`/registry/versions/${encodeURIComponent(id)}/monitoring/stream`, 'POST', { month, actor }, 120_000),
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
    'log', 'calibrating', 'threshold', 'tracked', 'post_fit', 'forecast_ready', 'importing', 'el_model_started', 'el_model_finished', 'seg_model_started', 'seg_model_finished', 'surv_model_started', 'surv_model_finished', 'ri_model_started', 'ri_model_finished', 'run_succeeded', 'run_failed', 'run_cancelled']
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

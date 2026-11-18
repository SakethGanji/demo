// Typed client for the ENSEMBLE API. Every call goes through the Vite proxy at /api.

import type { DatasetDetail, DatasetSummary, FeatureImportance, Health, ModelFamily, Prediction, Run, RunCreate, RunEvent } from './types'

export class ApiError extends Error {
  /** 0 when the API could not be reached at all (network error, timeout, or the dev proxy's 503). */
  constructor(message: string, readonly status: number) { super(message) }
  get unreachable() { return this.status === 0 }
}

const BASE = '/api'

async function request<T>(path: string, init: RequestInit & { timeoutMs?: number } = {}): Promise<T> {
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
  datasets: {
    list: () => request<{ datasets: DatasetSummary[] }>('/datasets').then((r) => r.datasets),
    get: (id: string) => request<DatasetDetail>(`/datasets/${encodeURIComponent(id)}`),
    upload: (file: File, target?: string) => {
      const form = new FormData()
      form.append('file', file)
      if (target) form.append('target', target)
      return request<DatasetDetail>('/datasets', { method: 'POST', body: form, timeoutMs: 120_000 })
    },
  },
  models: () => request<{ families: ModelFamily[] }>('/models').then((r) => r.families),
  runs: {
    create: (body: RunCreate) => request<Run>('/runs', { method: 'POST', body: JSON.stringify(body), headers: { 'Content-Type': 'application/json' } }),
    list: (datasetId?: string) => request<{ runs: Run[] }>(`/runs${datasetId ? `?dataset_id=${encodeURIComponent(datasetId)}` : ''}`).then((r) => r.runs),
    get: (id: string) => request<Run>(`/runs/${encodeURIComponent(id)}`),
    cancel: (id: string) => request<{ id: string; cancelling: boolean }>(`/runs/${encodeURIComponent(id)}/cancel`, { method: 'POST' }),
    /** Events recorded so far, once (no stream). */
    events: (id: string, from = 0) => request<{ events: RunEvent[]; next: number }>(`/runs/${encodeURIComponent(id)}/events?format=json&from=${from}`),
    predict: (id: string, rows: Record<string, unknown>[], model?: string) =>
      request<Prediction>(`/runs/${encodeURIComponent(id)}/predict`, {
        method: 'POST', body: JSON.stringify({ rows, model }), headers: { 'Content-Type': 'application/json' }, timeoutMs: 60_000,
      }),
    featureImportance: (id: string) =>
      request<{ run_id: string; features: FeatureImportance[] }>(`/runs/${encodeURIComponent(id)}/feature-importance`, { timeoutMs: 180_000 }),
  },
}

/**
 * Follow a run's events live (SSE). Replays from `from` (default 0), so a late subscriber sees the whole run.
 * EventSource reconnects by itself with Last-Event-ID; the stream closes after run_succeeded / run_failed /
 * run_cancelled. Returns a function that stops listening.
 */
export function followRun(id: string, onEvent: (e: RunEvent) => void, opts: { from?: number; onEnd?: () => void; onError?: () => void } = {}) {
  const es = new EventSource(`${BASE}/runs/${encodeURIComponent(id)}/events?from=${opts.from ?? 0}`)
  const terminal = new Set(['run_succeeded', 'run_failed', 'run_cancelled'])
  let ended = false
  const handle = (msg: MessageEvent) => {
    const e = JSON.parse(msg.data) as RunEvent
    onEvent(e)
    if (terminal.has(e.type)) { ended = true; es.close(); opts.onEnd?.() }
  }
  const types = ['run_queued', 'run_started', 'data_ready', 'config_resolved', 'fit_started', 'model_started', 'model_finished',
    'model_failed', 'ensemble', 'fit_finished', 'leaderboard', 'evaluation', 'text_models', 'explaining', 'feature_importance',
    'log', 'run_succeeded', 'run_failed', 'run_cancelled']
  for (const t of types) es.addEventListener(t, handle as EventListener)
  es.onerror = () => { if (!ended) opts.onError?.() }
  return () => { ended = true; es.close() }
}

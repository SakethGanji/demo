// Typed client for the ENSEMBLE API. Every call goes through the Vite proxy at /api.

import type { DatasetDetail, DatasetSummary, Health } from './types'

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
}

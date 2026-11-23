/**
 * HTTP core for every engine call (UI-DESIGN §13 slice 0, UI-INTEGRATION §3.1).
 *
 * `ApiError` normalises the five error shapes the engine produces:
 *   1. `{"detail": {"code", "message", ...extra}}`         (project/module/route services)
 *   2. `{"detail": "text"}`                                 (plain HTTPException)
 *   3. `{"detail": [{"loc", "msg", "type"}, ...]}`          (FastAPI/pydantic 422)
 *   4. `{"error": "code", "message"?: "...", ...}`          (project router, public apps, sandbox)
 *   5. `{"ok": false, "error": {"code", "message"}}`        (broker)
 */

import { backends } from '@/shared/lib/config'

export interface FieldError {
  loc: (string | number)[]
  msg: string
}

export class ApiError extends Error {
  status: number
  code: string | null
  body: unknown
  fieldErrors: FieldError[]

  constructor(status: number, code: string | null, message: string, body: unknown, fieldErrors: FieldError[] = []) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.code = code
    this.body = body
    this.fieldErrors = fieldErrors
  }

  /** The `detail` object (or broker/router error object) minus `code` and `message`. */
  get details(): Record<string, unknown> {
    const b = this.body as Record<string, unknown> | null
    if (!b || typeof b !== 'object') return {}
    let src: unknown = null
    if (b.detail && typeof b.detail === 'object' && !Array.isArray(b.detail)) src = b.detail
    else if (b.error && typeof b.error === 'object') src = b.error
    else if (typeof b.error === 'string') src = b
    if (!src || typeof src !== 'object') return {}
    const { code: _c, message: _m, error: _e, ...rest } = src as Record<string, unknown>
    void _c; void _m; void _e
    return rest
  }
}

function asRecord(v: unknown): Record<string, unknown> | null {
  return v && typeof v === 'object' && !Array.isArray(v) ? (v as Record<string, unknown>) : null
}

export function parseError(status: number, body: unknown): ApiError {
  const b = asRecord(body)
  const fallback = `HTTP ${status}`
  if (!b) {
    const text = typeof body === 'string' && body.trim() ? body.trim().slice(0, 300) : fallback
    return new ApiError(status, null, text, body)
  }
  const detail = b.detail
  // 1. structured detail
  const d = asRecord(detail)
  if (d) {
    const code = typeof d.code === 'string' ? d.code : typeof d.error === 'string' ? d.error : null
    const message = typeof d.message === 'string' ? d.message : code ?? fallback
    return new ApiError(status, code, message, body)
  }
  // 2. string detail
  if (typeof detail === 'string') return new ApiError(status, null, detail, body)
  // 3. pydantic validation list
  if (Array.isArray(detail)) {
    const fieldErrors: FieldError[] = detail
      .map((x) => asRecord(x))
      .filter((x): x is Record<string, unknown> => !!x)
      .map((x) => ({ loc: (x.loc as (string | number)[]) ?? [], msg: String(x.msg ?? '') }))
    const first = fieldErrors[0]
    const where = first?.loc?.filter((p) => p !== 'body').join('.')
    const message = first ? (where ? `${where}: ${first.msg}` : first.msg) : fallback
    return new ApiError(status, 'invalid_request', message, body, fieldErrors)
  }
  // 5. broker
  const e = asRecord(b.error)
  if (e) {
    const code = typeof e.code === 'string' ? e.code : null
    return new ApiError(status, code, typeof e.message === 'string' ? e.message : code ?? fallback, body)
  }
  // 4. router / public app / sandbox
  if (typeof b.error === 'string') {
    const message = typeof b.message === 'string' ? b.message : b.error
    return new ApiError(status, b.error, message, body)
  }
  if (typeof b.message === 'string') return new ApiError(status, null, b.message, body)
  return new ApiError(status, null, fallback, body)
}

export type Query = Record<string, string | number | boolean | null | undefined>

export interface RequestOptions extends Omit<RequestInit, 'body'> {
  query?: Query
  /** JSON-serialised unless it is already a string/FormData/Blob. */
  body?: unknown
}

export function apiUrl(path: string, query?: Query): string {
  // "/api/..." is already prefixed; "/api-specs" is not
  const prefix = path === '/api' || path.startsWith('/api/') || path.startsWith('/api?') || path.startsWith('/a/') || path.startsWith('/execution-stream') ? '' : '/api'
  let url = `${backends.workflow}${prefix}${path}`
  if (query) {
    const qs = new URLSearchParams()
    for (const [k, v] of Object.entries(query)) {
      if (v === undefined || v === null || v === '') continue
      qs.set(k, String(v))
    }
    const s = qs.toString()
    if (s) url += (url.includes('?') ? '&' : '?') + s
  }
  return url
}

async function readBody(res: Response): Promise<unknown> {
  const text = await res.text()
  if (!text) return undefined
  try {
    return JSON.parse(text)
  } catch {
    return text
  }
}

function buildInit(opts: RequestOptions = {}): RequestInit {
  const { query: _q, body, headers, ...rest } = opts
  void _q
  const h: Record<string, string> = { 'Content-Type': 'application/json' }
  let payload: BodyInit | undefined
  if (body === undefined) payload = undefined
  else if (typeof body === 'string' || body instanceof FormData || body instanceof Blob) payload = body as BodyInit
  else payload = JSON.stringify(body)
  if (body instanceof FormData) delete h['Content-Type']
  return { ...rest, headers: { ...h, ...(headers as Record<string, string> | undefined) }, body: payload }
}

/** Raw request: returns the Response after error mapping (for headers like X-Next-Cursor). */
export async function requestRaw(path: string, opts: RequestOptions = {}): Promise<{ data: unknown; res: Response }> {
  const res = await fetch(apiUrl(path, opts.query), buildInit(opts))
  const data = await readBody(res)
  if (!res.ok) throw parseError(res.status, data)
  return { data, res }
}

export async function request<T>(path: string, opts: RequestOptions = {}): Promise<T> {
  const { data } = await requestRaw(path, opts)
  return data as T
}

export async function requestText(path: string, opts: RequestOptions = {}): Promise<string> {
  const res = await fetch(apiUrl(path, opts.query), buildInit(opts))
  const text = await res.text()
  if (!res.ok) {
    let body: unknown = text
    try {
      body = JSON.parse(text)
    } catch {
      /* keep text */
    }
    throw parseError(res.status, body)
  }
  return text
}

export async function requestBlob(path: string, opts: RequestOptions = {}): Promise<Blob> {
  const res = await fetch(apiUrl(path, opts.query), buildInit(opts))
  if (!res.ok) throw parseError(res.status, await readBody(res))
  return res.blob()
}

/** Human message for any thrown value (copy for errors always comes from ApiError.message, §8.1). */
export function errorMessage(e: unknown): string {
  if (e instanceof ApiError) return e.message
  if (e instanceof Error) return e.message
  return String(e)
}

export function isApiError(e: unknown, code?: string, status?: number): e is ApiError {
  return e instanceof ApiError && (code === undefined || e.code === code) && (status === undefined || e.status === status)
}

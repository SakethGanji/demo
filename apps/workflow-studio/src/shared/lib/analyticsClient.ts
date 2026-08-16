/**
 * Typed fetch client for the Analytics Service (datasets).
 *
 * Deliberately NOT `apiFetch` from ./api: that flattens every failure to
 * `new Error(errorData.error ?? 'HTTP {status}')`, which discards the status, the
 * body, and the problem+json `code` that this API expects the UI to branch on.
 * It also calls `.json()` unconditionally, which throws on a 204 — and this
 * service stamps `content-type: application/json` on 204s, so parsing by
 * content-type before checking status makes every successful DELETE look failed.
 *
 * Identity is the POC `X-User-Id` / `X-Team-Id` header pair; see ./identity.
 */

import { backends } from './config';
import { getIdentityHeaders } from './identity';

export const ANALYTICS_BASE = `${backends.analytics.replace(/\/+$/, '')}/api/v1`;

interface FieldError {
  loc?: (string | number)[];
  msg?: string;
}

/**
 * FastAPI request-validation failures put the real reason in `errors[]` and
 * leave `detail` as a constant "Request validation failed" — fold the specifics
 * in so a form can say which field is wrong and why.
 */
function fieldErrorText(body: Record<string, unknown>): string | null {
  const errs = body?.errors;
  if (!Array.isArray(errs) || errs.length === 0) return null;
  return (errs as FieldError[])
    .map((e) => {
      const path = (e.loc ?? []).filter((p) => p !== 'body').join('.');
      return path ? `${path}: ${e.msg}` : e.msg;
    })
    .filter(Boolean)
    .join('; ');
}

/**
 * Every 4xx/5xx from this service is problem+json:
 * `{ type, title, status, detail, instance, code, ...extra }`.
 * Branch on `code`, never on `detail` prose.
 *
 * Note both spellings occur: HTTP-status defaults use underscores
 * (`not_found`, `conflict`), domain codes use hyphens (`unknown-column`).
 */
export class AnalyticsApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly detail: string;
  readonly body: Record<string, unknown>;

  constructor(status: number, body: Record<string, unknown>) {
    const base = (body?.detail as string) || (body?.title as string) || `HTTP ${status}`;
    const fields = fieldErrorText(body);
    const detail = fields ? `${base}: ${fields}` : base;
    super(detail);
    this.name = 'AnalyticsApiError';
    this.status = status;
    this.code = (body?.code as string) || 'error';
    this.detail = detail;
    this.body = body ?? {};
  }

  /**
   * Cross-tenant reads return 404, never 403 — hiding existence is deliberate.
   * A "not found" may mean "exists, but not yours"; never render "access denied".
   */
  get isNotFound(): boolean {
    return this.status === 404;
  }

  /** The caller lacks elevated access to sensitive data; show a masked state. */
  get isSensitiveRestricted(): boolean {
    return this.code === 'sensitive-data-restricted';
  }
}

function qs(query?: Record<string, unknown>): string {
  if (!query) return '';
  const p = new URLSearchParams();
  for (const [k, v] of Object.entries(query)) {
    if (v === undefined || v === null || v === '') continue;
    if (Array.isArray(v)) v.forEach((x) => p.append(k, String(x)));
    else p.append(k, String(v));
  }
  const s = p.toString();
  return s ? `?${s}` : '';
}

async function parse(res: Response): Promise<unknown> {
  // Status FIRST. 204/205 carry no body but still arrive with
  // `content-type: application/json`, so a content-type-first parse throws on
  // the empty body and reports a successful delete as a failure.
  if (res.status === 204 || res.status === 205) return null;
  const ct = res.headers.get('content-type') ?? '';
  if (ct.includes('json')) {
    const text = await res.text();
    return text ? JSON.parse(text) : null;
  }
  return res.text();
}

async function request<T>(
  method: string,
  path: string,
  opts: { body?: unknown; query?: Record<string, unknown>; headers?: Record<string, string> } = {},
): Promise<T> {
  const isForm = opts.body instanceof FormData;
  const res = await fetch(`${ANALYTICS_BASE}${path}${qs(opts.query)}`, {
    method,
    headers: {
      ...getIdentityHeaders(),
      ...(opts.body !== undefined && !isForm ? { 'Content-Type': 'application/json' } : {}),
      ...(opts.headers ?? {}),
    },
    body: opts.body === undefined ? undefined : isForm ? (opts.body as FormData) : JSON.stringify(opts.body),
  });

  const data = await parse(res);
  if (!res.ok) {
    const body =
      typeof data === 'object' && data !== null
        ? (data as Record<string, unknown>)
        : { detail: String(data) };
    throw new AnalyticsApiError(res.status, body);
  }
  return data as T;
}

export const analytics = {
  get: <T>(path: string, query?: Record<string, unknown>) => request<T>('GET', path, { query }),
  post: <T>(path: string, body?: unknown, query?: Record<string, unknown>) =>
    request<T>('POST', path, { body, query }),
  put: <T>(path: string, body?: unknown) => request<T>('PUT', path, { body }),
  patch: <T>(path: string, body?: unknown) => request<T>('PATCH', path, { body }),
  del: <T>(path: string, query?: Record<string, unknown>) => request<T>('DELETE', path, { query }),

  /** Multipart upload; `fields` become form fields alongside the file. */
  upload: <T>(path: string, file: File, fields?: Record<string, string>, query?: Record<string, unknown>) => {
    const fd = new FormData();
    fd.append('file', file);
    for (const [k, v] of Object.entries(fields ?? {})) fd.append(k, v);
    return request<T>('POST', path, { body: fd, query });
  },
};

/** Offset-list envelope. `limit` is always >= 1, so ceil(total/limit) is safe. */
export interface Page<T> {
  items: T[];
  total: number;
  limit: number;
  offset: number;
}

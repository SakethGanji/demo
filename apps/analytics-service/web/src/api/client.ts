// Typed fetch client for the Analytics Service.
// - Base URL: `VITE_API_BASE` if set (e.g. https://analytics.internal), otherwise
//   same-origin `/api/v1` through the Vite dev proxy (see vite.config.ts). Set the
//   env var when embedding this UI in another app or serving it from another host.
// - Identity is the POC X-User-Id / X-Team-Id header pair (the service's auth model).
//   Swap `authHeaders()` for your own scheme (bearer token, cookie) when integrating.
// - Every non-2xx is surfaced as an ApiError carrying the problem+json `code` and
//   any extra fields, so the UI can branch on `code` and attach messages to fields.

/** Where the API lives. Trailing slashes are trimmed so callers can pass either form. */
const API_ROOT: string = (import.meta.env?.VITE_API_BASE || "").replace(/\/+$/, "");
export const API_BASE = `${API_ROOT}/api/v1`;

export interface Identity {
  userId: string;
  teamId?: string | null;
  label?: string;
}

let identity: Identity = { userId: "00000000-0000-0000-0000-000000000001", label: "System (admin)" };

export function setIdentity(next: Identity) {
  identity = next;
  try {
    localStorage.setItem("analytics.identity", JSON.stringify(next));
  } catch { /* ignore */ }
}

export function getIdentity(): Identity {
  return identity;
}

export function loadIdentity(): Identity {
  try {
    const raw = localStorage.getItem("analytics.identity");
    if (raw) identity = JSON.parse(raw);
  } catch { /* ignore */ }
  return identity;
}

interface FieldError { loc?: (string | number)[]; msg?: string }

/** FastAPI request-validation errors put the real reason in `errors[]` and leave
 *  `detail` as the constant "Request validation failed" — surface the specifics
 *  so a form can tell the user which field is wrong and why. */
function fieldErrorText(body: Record<string, unknown>): string | null {
  const errs = body?.errors;
  if (!Array.isArray(errs) || errs.length === 0) return null;
  return (errs as FieldError[])
    .map((e) => {
      const path = (e.loc || []).filter((p) => p !== "body").join(".");
      return path ? `${path}: ${e.msg}` : e.msg;
    })
    .filter(Boolean)
    .join("; ");
}

export class ApiError extends Error {
  status: number;
  code: string;
  detail: string;
  body: Record<string, unknown>;
  constructor(status: number, body: Record<string, unknown>) {
    const base = (body?.detail as string) || (body?.title as string) || `HTTP ${status}`;
    const fields = fieldErrorText(body);
    const detail = fields ? `${base}: ${fields}` : base;
    super(detail);
    this.name = "ApiError";
    this.status = status;
    this.code = (body?.code as string) || "error";
    this.detail = detail;
    this.body = body || {};
  }
}

function authHeaders(): Record<string, string> {
  const h: Record<string, string> = { "X-User-Id": identity.userId };
  if (identity.teamId) h["X-Team-Id"] = identity.teamId;
  return h;
}

function qs(query?: Record<string, unknown>): string {
  if (!query) return "";
  const p = new URLSearchParams();
  for (const [k, v] of Object.entries(query)) {
    if (v === undefined || v === null || v === "") continue;
    if (Array.isArray(v)) v.forEach((x) => p.append(k, String(x)));
    else p.append(k, String(v));
  }
  const s = p.toString();
  return s ? `?${s}` : "";
}

async function parse(res: Response): Promise<unknown> {
  // Status first: 204/205 carry no body, but the server still stamps
  // `content-type: application/json` on them — parsing by content-type first
  // made res.json() throw on the empty body, so every 204 DELETE surfaced as a
  // failure even though the delete had succeeded.
  if (res.status === 204 || res.status === 205) return null;
  const ct = res.headers.get("content-type") || "";
  if (ct.includes("json")) {
    const body = await res.text();
    return body ? JSON.parse(body) : null;   // tolerate any empty JSON body
  }
  return res.text();
}

async function request<T = unknown>(
  method: string,
  path: string,
  opts: { body?: unknown; query?: Record<string, unknown>; headers?: Record<string, string> } = {},
): Promise<T> {
  const isForm = opts.body instanceof FormData;
  const res = await fetch(`${API_BASE}${path}${qs(opts.query)}`, {
    method,
    headers: {
      ...authHeaders(),
      ...(opts.body !== undefined && !isForm ? { "Content-Type": "application/json" } : {}),
      ...(opts.headers || {}),
    },
    body: opts.body === undefined ? undefined : isForm ? (opts.body as FormData) : JSON.stringify(opts.body),
  });
  const data = await parse(res);
  if (!res.ok) {
    throw new ApiError(res.status, (typeof data === "object" && data ? data : { detail: String(data) }) as Record<string, unknown>);
  }
  return data as T;
}

export const api = {
  get: <T = unknown>(path: string, query?: Record<string, unknown>) => request<T>("GET", path, { query }),
  post: <T = unknown>(path: string, body?: unknown, query?: Record<string, unknown>) => request<T>("POST", path, { body, query }),
  put: <T = unknown>(path: string, body?: unknown) => request<T>("PUT", path, { body }),
  patch: <T = unknown>(path: string, body?: unknown) => request<T>("PATCH", path, { body }),
  del: <T = unknown>(path: string, query?: Record<string, unknown>) => request<T>("DELETE", path, { query }),

  // Multipart upload; `fields` become form fields alongside the file.
  upload: <T = unknown>(path: string, file: File, fields?: Record<string, string>, query?: Record<string, unknown>) => {
    const fd = new FormData();
    fd.append("file", file);
    for (const [k, v] of Object.entries(fields || {})) fd.append(k, v);
    return request<T>("POST", path, { body: fd, query });
  },

  // Fetch a file with identity headers and trigger a browser download.
  download: async (path: string, filename: string, query?: Record<string, unknown>) => {
    const res = await fetch(`${API_BASE}${path}${qs(query)}`, { headers: authHeaders() });
    if (!res.ok) throw new ApiError(res.status, (await parse(res)) as Record<string, unknown>);
    const blob = await res.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
  },
};

// Shared envelope helper.
export interface Page<T> { items: T[]; total: number; limit: number; offset: number; }

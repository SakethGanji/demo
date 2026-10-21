// API cases: chained requests with {{var}} templating, save via JSONPath-lite, expect status / json path / schema.
// Every request goes through test-builder's checkRequest (host allowlist + forbidden requests) before it is sent.
import { writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { checkRequest } from '../../test-builder/src/policy.ts';
import { totp } from '../../test-builder/src/totp.ts';
import { scrubDeep } from '../../test-builder/src/secrets.ts';
import type { Target } from '../../test-builder/src/config.ts';
import { emit, type CaseResult, type FailureContext, type Job } from './store.ts';
import type { ApiRequest } from './spec.ts';

// ---------------------------------------------------------------- JSONPath-lite
/** Supports $, .key, ['key'], [n] and a trailing .length. */
export function jsonPath(value: unknown, path: string): unknown {
  if (!path.startsWith('$')) throw new Error(`JSON path must start with "$": ${path}`);
  const re = /\.([A-Za-z_$][\w$-]*)|\[(\d+)\]|\['([^']*)'\]|\["([^"]*)"\]/y;
  let cur: unknown = value;
  let i = 1;
  while (i < path.length) {
    re.lastIndex = i;
    const m = re.exec(path);
    if (!m) throw new Error(`bad JSON path near "${path.slice(i)}"`);
    i = re.lastIndex;
    const key = m[1] ?? m[3] ?? m[4];
    if (key !== undefined) {
      if (key === 'length' && (Array.isArray(cur) || typeof cur === 'string')) { cur = cur.length; continue; }
      cur = cur !== null && typeof cur === 'object' ? (cur as Record<string, unknown>)[key] : undefined;
    } else {
      const n = Number(m[2]);
      cur = Array.isArray(cur) ? cur[n] : undefined;
    }
    if (cur === undefined) return undefined;
  }
  return cur;
}

// ---------------------------------------------------------------- templating
const WHOLE = /^\{\{\s*([\w:.-]+)\s*\}\}$/;

function lookup(name: string, vars: Record<string, unknown>): unknown {
  const t = /^totp:(\w+)$/.exec(name);
  if (t) {
    const secret = vars[t[1]!];
    if (typeof secret !== 'string') throw new Error(`template: no credential "${t[1]}" for {{totp:${t[1]}}}`);
    return totp(secret);
  }
  if (!(name in vars)) throw new Error(`template: undefined variable {{${name}}}`);
  return vars[name];
}

/** "{{x}}" alone keeps the variable's type; embedded "{{x}}" is stringified. */
export function template(v: unknown, vars: Record<string, unknown>): unknown {
  if (typeof v === 'string') {
    const whole = WHOLE.exec(v);
    if (whole) return lookup(whole[1]!, vars);
    return v.replace(/\{\{\s*([\w:.-]+)\s*\}\}/g, (_, n: string) => {
      const x = lookup(n, vars);
      return typeof x === 'string' ? x : JSON.stringify(x);
    });
  }
  if (Array.isArray(v)) return v.map((x) => template(x, vars));
  if (v && typeof v === 'object') return Object.fromEntries(Object.entries(v).map(([k, x]) => [k, template(x, vars)]));
  return v;
}

// ---------------------------------------------------------------- simple JSON schema
type Schema = { type?: string | string[]; required?: string[]; properties?: Record<string, Schema>; items?: Schema; enum?: unknown[]; minItems?: number; additionalProperties?: boolean };

function typeOf(v: unknown): string {
  if (v === null) return 'null';
  if (Array.isArray(v)) return 'array';
  if (typeof v === 'number') return Number.isInteger(v) ? 'integer' : 'number';
  return typeof v;
}

/** Returns a list of violations (empty = valid). Supports type, required, properties, items, enum, minItems, additionalProperties:false. */
export function validateSchema(v: unknown, s: Schema, at = '$'): string[] {
  const out: string[] = [];
  if (s.type) {
    const types = Array.isArray(s.type) ? s.type : [s.type];
    const t = typeOf(v);
    if (!types.some((x) => x === t || (x === 'number' && t === 'integer'))) return [`${at}: expected ${types.join('|')}, got ${t}`];
  }
  if (s.enum && !s.enum.some((e) => JSON.stringify(e) === JSON.stringify(v))) out.push(`${at}: not in enum ${JSON.stringify(s.enum)}`);
  if (v && typeof v === 'object' && !Array.isArray(v)) {
    const o = v as Record<string, unknown>;
    for (const r of s.required ?? []) if (!(r in o)) out.push(`${at}: missing required "${r}"`);
    for (const [k, ps] of Object.entries(s.properties ?? {})) if (k in o) out.push(...validateSchema(o[k], ps, `${at}.${k}`));
    if (s.additionalProperties === false) for (const k of Object.keys(o)) if (!s.properties?.[k]) out.push(`${at}: unexpected property "${k}"`);
  }
  if (Array.isArray(v)) {
    if (s.minItems !== undefined && v.length < s.minItems) out.push(`${at}: expected at least ${s.minItems} items, got ${v.length}`);
    if (s.items) v.slice(0, 200).forEach((x, i) => out.push(...validateSchema(x, s.items!, `${at}[${i}]`)));
  }
  return out;
}

// ---------------------------------------------------------------- runner
export interface ApiCaseInput { job: Job; target: Target; caseId: string; requests: ApiRequest[]; caseDir: string; signal: AbortSignal }

export async function runApiCase(i: ApiCaseInput): Promise<Omit<CaseResult, 'case_id' | 'kind' | 'title' | 'duration_ms' | 'artifacts'> & { files: string[] }> {
  const { job, target, caseId } = i;
  const vars: Record<string, unknown> = { ...job.creds };
  const exchanges: unknown[] = [];
  const network: FailureContext['network'] = [];
  const files: string[] = [];
  const finish = <T>(r: T): T & { files: string[] } => {
    const f = join(i.caseDir, `${caseId}.api.json`);
    writeFileSync(f, JSON.stringify(scrubDeep({ case_id: caseId, exchanges }, job.creds), null, 2));
    files.push(f);
    return { ...r, files };
  };
  const fail = (index: number, req: ApiRequest, error: string, response?: unknown) => {
    job.failures[caseId] = scrubDeep({ case_id: caseId, kind: 'api', steps: i.requests, failed_index: index, failed_step: req, error, network, response }, job.creds);
    emit(job, 'step_fail', caseId, `${req.method} ${req.path} failed: ${error}`, { index });
    return finish({ status: 'failed' as const, failed_step: { index, step: req, description: `${req.method} ${req.path}` }, error });
  };

  for (const [idx, req] of i.requests.entries()) {
    if (i.signal.aborted) return finish({ status: 'cancelled' as const });
    const label = req.name ?? `${req.method} ${req.path}`;
    job.progress.current_step = label;
    emit(job, 'step_start', caseId, label, { index: idx });
    let url: string, headers: Record<string, string>, body: unknown;
    try {
      const p = String(template(req.path, vars));
      url = new URL(p, target.baseUrl + '/').toString();
      headers = template(req.headers ?? {}, vars) as Record<string, string>;
      body = req.body === undefined ? undefined : template(req.body, vars);
    } catch (e) {
      return fail(idx, req, (e as Error).message);
    }
    const verdict = checkRequest(target, req.method, url);
    if (verdict) {
      const msg = `BLOCKED by policy: ${req.method} ${url} (${verdict.reason}${verdict.rule ? ` ${verdict.rule}` : ''})`;
      emit(job, 'blocked', caseId, msg, { index: idx, method: req.method, url, ...verdict });
      exchanges.push({ index: idx, method: req.method, url, blocked: verdict });
      return finish({ status: 'blocked' as const, failed_step: { index: idx, step: req, description: label }, error: msg });
    }
    const h: Record<string, string> = { accept: 'application/json', ...headers };
    let payload: string | undefined;
    if (body !== undefined) {
      payload = typeof body === 'string' ? body : JSON.stringify(body);
      if (!Object.keys(h).some((k) => k.toLowerCase() === 'content-type')) h['content-type'] = 'application/json';
    }
    const t0 = Date.now();
    let status: number, text: string, json: unknown, resHeaders: Record<string, string>;
    try {
      // redirect: manual - a 3xx hop to a forbidden path must not be followed behind the policy's back.
      const r = await fetch(url, { method: req.method, headers: h, body: payload, redirect: 'manual', signal: AbortSignal.any([i.signal, AbortSignal.timeout(20_000)]) });
      status = r.status;
      text = await r.text();
      resHeaders = {}; r.headers.forEach((v, k) => { resHeaders[k] = v; });
      try { json = text ? JSON.parse(text) : undefined; } catch { json = undefined; }
    } catch (e) {
      if (i.signal.aborted) return finish({ status: 'cancelled' as const });
      network.push({ method: req.method, url, status: 'network-error' });
      emit(job, 'request', caseId, `${req.method} ${new URL(url).pathname} -> network error`, { method: req.method, url, status: 0 });
      return fail(idx, req, `network error: ${(e as Error).message}`);
    }
    const ms = Date.now() - t0;
    network.push({ method: req.method, url, status });
    emit(job, 'request', caseId, `${req.method} ${new URL(url).pathname} -> ${status} (${ms} ms)`, { method: req.method, url, status, ms });
    exchanges.push({ index: idx, method: req.method, url, request_body: body, status, ms, response_headers: resHeaders, response_body: json ?? text.slice(0, 4000) });

    // expectations
    const ex = req.expect ?? {};
    const problems: string[] = [];
    if (ex.status !== undefined) {
      const ok = Array.isArray(ex.status) ? ex.status.includes(status) : ex.status === status;
      if (!ok) problems.push(`expected status ${JSON.stringify(ex.status)}, got ${status}`);
    } else if (status >= 400) {
      problems.push(`unexpected status ${status}`);
    }
    if (!problems.length && ex.json_path_equals) {
      for (const [p, want] of Object.entries(ex.json_path_equals)) {
        let got: unknown, w: unknown;
        try { got = jsonPath(json, p); w = template(want, vars); } catch (e) { problems.push((e as Error).message); continue; }
        if (JSON.stringify(got) !== JSON.stringify(w)) problems.push(`${p}: expected ${JSON.stringify(w)}, got ${JSON.stringify(got)}`);
      }
    }
    if (!problems.length && ex.schema) problems.push(...validateSchema(json, ex.schema as Schema).slice(0, 10));
    if (problems.length) return fail(idx, req, problems.join('; '), json ?? text.slice(0, 2000));

    for (const [name, p] of Object.entries(req.save ?? {})) {
      let v: unknown;
      try {
        v = p.startsWith('header:') ? resHeaders[p.slice(7).toLowerCase()] : jsonPath(json, p);
      } catch (e) { return fail(idx, req, `save ${name}: ${(e as Error).message}`); }
      if (v === undefined) return fail(idx, req, `save ${name}: ${p} not found in response`, json);
      vars[name] = v;
      // Saved tokens/secrets are masked from events and artifacts like credentials are.
      if (typeof v === 'string' && v.length >= 6 && /token|secret|pass|key/i.test(name)) job.creds[`saved_${caseId}_${name}`] = v;
    }
    emit(job, 'step_pass', caseId, `${label} -> ${status}`, { index: idx, saved: Object.keys(req.save ?? {}) });
  }
  return finish({ status: 'passed' as const });
}

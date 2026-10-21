// Request-body validation: target + policy -> test-builder Target, UI steps -> test-builder Actions, API requests.
import { join } from 'node:path';
import { validateAction, type Action, type ElementRef } from '../../test-builder/src/actions.ts';
import { parseRegex, parseRequestRule, type Target } from '../../test-builder/src/config.ts';

export class BadRequest extends Error {}

export interface TargetBody { base_url: string; allowed_hosts?: string[] }
export interface PolicyBody { forbidden_names?: string[]; forbidden_requests?: string[] }

/** Our own step that test-builder's vocabulary lacks. */
export interface ExpectUrlStep { action: 'expectUrl'; url: string; group?: string }
export type UiStep = Action | ExpectUrlStep;

export interface ApiRequest {
  method: string;
  path: string;
  headers?: Record<string, string>;
  body?: unknown;
  expect?: { status?: number | number[]; json_path_equals?: Record<string, unknown>; schema?: unknown };
  save?: Record<string, string>;
  name?: string;
}

export type CaseBody =
  | { id: string; kind: 'ui'; title?: string; steps: UiStep[] }
  | { id: string; kind: 'api'; title?: string; requests: ApiRequest[] };

const str = (v: unknown, where: string): string => {
  if (typeof v !== 'string' || !v) throw new BadRequest(`${where} must be a non-empty string`);
  return v;
};

/**
 * forbidden_names entry -> RegExp. "/.../flags" is a regex; a plain word or phrase is matched case-insensitively
 * on word boundaries, so "pay" blocks "Pay now" but not "Payments" or "Add payee".
 */
export function nameRule(s: string): RegExp {
  if (/^\/.*\/[a-z]*$/s.test(s)) return parseRegex(s);
  const esc = s.trim().replace(/[.*+?^${}()|[\]\\]/g, '\\$&').replace(/\s+/g, '\\s+');
  return new RegExp(`(^|\\b)${esc}(\\b|$)`, 'i');
}

export function buildTarget(jobId: string, dir: string, target: unknown, policy: unknown): Target {
  if (!target || typeof target !== 'object') throw new BadRequest('target is required');
  const t = target as TargetBody;
  let base: URL;
  try { base = new URL(str(t.base_url, 'target.base_url')); } catch (e) { throw e instanceof BadRequest ? e : new BadRequest('target.base_url must be an absolute URL'); }
  if (base.protocol !== 'http:' && base.protocol !== 'https:') throw new BadRequest('target.base_url must be http(s)');
  const allowed = Array.isArray(t.allowed_hosts) && t.allowed_hosts.length ? t.allowed_hosts : [base.host];
  if (allowed.some((h) => typeof h !== 'string')) throw new BadRequest('target.allowed_hosts must be strings');
  const p = (policy ?? {}) as PolicyBody;
  const names = Array.isArray(p.forbidden_names) ? p.forbidden_names : [];
  const reqs = Array.isArray(p.forbidden_requests) ? p.forbidden_requests : [];
  let forbiddenActions: RegExp[];
  try { forbiddenActions = names.map((n) => nameRule(str(n, 'policy.forbidden_names[]'))); } catch (e) { throw new BadRequest(`policy.forbidden_names: ${(e as Error).message}`); }
  return {
    path: join(dir, 'target.json'),
    name: `${jobId}:${base.host}`,
    baseUrl: base.toString().replace(/\/$/, ''),
    allowedHosts: allowed.map((h) => h.toLowerCase()),
    forbiddenActions,
    forbiddenRequests: reqs.map((r) => parseRequestRule(str(r, 'policy.forbidden_requests[]'))),
    credentials: {},
    budget: { maxSteps: 30, maxSeconds: 240 },
  };
}

const ALIASES: Record<string, string> = {
  expect_text: 'expectText', expect_visible: 'expectVisible', expect_count: 'expectCount', wait_for: 'waitFor',
  expect_url: 'expectUrl', wait: 'waitFor',
};

/** Validate one UI step. Accepts test-builder's camelCase verbs and snake_case aliases. */
export function normalizeStep(raw: unknown, where: string): UiStep {
  if (!raw || typeof raw !== 'object') throw new BadRequest(`${where}: step must be an object`);
  const o = { ...(raw as Record<string, unknown>) };
  if (typeof o.action === 'string' && ALIASES[o.action]) o.action = ALIASES[o.action];
  if (o.action === 'expectUrl') {
    const url = o.url ?? o.contains ?? o.pattern;
    return { action: 'expectUrl', url: str(url, `${where}.url`), group: typeof o.group === 'string' ? o.group : undefined };
  }
  if (o.action === 'done') throw new BadRequest(`${where}: "done" is not a runnable step`);
  try { return validateAction(o); } catch (e) { throw new BadRequest(`${where}: ${(e as Error).message}`); }
}

export function normalizeCases(raw: unknown): CaseBody[] {
  if (!Array.isArray(raw) || !raw.length) throw new BadRequest('cases must be a non-empty array');
  const seen = new Set<string>();
  return raw.map((c, i) => {
    if (!c || typeof c !== 'object') throw new BadRequest(`cases[${i}] must be an object`);
    const o = c as Record<string, unknown>;
    const id = str(o.id, `cases[${i}].id`);
    if (!/^[\w.-]{1,64}$/.test(id)) throw new BadRequest(`cases[${i}].id must match [A-Za-z0-9_.-]{1,64}`);
    if (seen.has(id)) throw new BadRequest(`duplicate case id ${id}`);
    seen.add(id);
    const title = typeof o.title === 'string' ? o.title : undefined;
    if (o.kind === 'ui') {
      if (!Array.isArray(o.steps) || !o.steps.length) throw new BadRequest(`cases[${i}].steps must be a non-empty array`);
      return { id, kind: 'ui', title, steps: o.steps.map((s, j) => normalizeStep(s, `cases[${i}].steps[${j}]`)) };
    }
    if (o.kind === 'api') {
      if (!Array.isArray(o.requests) || !o.requests.length) throw new BadRequest(`cases[${i}].requests must be a non-empty array`);
      const requests = o.requests.map((r, j) => {
        const w = `cases[${i}].requests[${j}]`;
        if (!r || typeof r !== 'object') throw new BadRequest(`${w} must be an object`);
        const q = r as Record<string, unknown>;
        const method = str(q.method, `${w}.method`).toUpperCase();
        if (!['GET', 'POST', 'PUT', 'PATCH', 'DELETE', 'HEAD', 'OPTIONS'].includes(method)) throw new BadRequest(`${w}.method unsupported`);
        if (q.save !== undefined && (typeof q.save !== 'object' || Object.values(q.save as object).some((v) => typeof v !== 'string'))) throw new BadRequest(`${w}.save must map var -> "$.path"`);
        if (q.headers !== undefined && (typeof q.headers !== 'object' || Object.values(q.headers as object).some((v) => typeof v !== 'string'))) throw new BadRequest(`${w}.headers must map name -> string`);
        return { ...(q as unknown as ApiRequest), method, path: str(q.path ?? q.url, `${w}.path`) };
      });
      return { id, kind: 'api', title, requests };
    }
    throw new BadRequest(`cases[${i}].kind must be "ui" or "api"`);
  });
}

export function refOf(step: UiStep): ElementRef | undefined {
  return 'target' in step ? step.target : undefined;
}

// Target config: loading, validation and compilation of policy rules.
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

export interface CredentialSpec { env: string; secret?: boolean }

export interface TargetConfigFile {
  name?: string;
  baseUrl: string;
  allowedHosts: string[];
  forbiddenActions: string[];
  forbiddenRequests: string[];
  credentials: Record<string, CredentialSpec | string>;
  budget?: { maxSteps?: number; maxSeconds?: number };
  api?: ApiConfig;
  hooks?: HooksConfig;
  auth?: AuthConfig;
}

/** Pre-authenticated session reuse: login (SSO/MFA) is handled OUTSIDE the tool and the resulting
 *  Playwright storageState is reused by every spec, exactly as an existing QA suite would. */
export interface AuthConfig { storageState?: string }

/** API-exploration settings: how to obtain a bearer token, and sample values for path params. */
export interface ApiConfig {
  tokenEndpoint?: string;                       // e.g. "/api/v1/auth/token"
  tokenBody?: Record<string, string>;           // field -> credential key or "totp:<key>"
  tokenField?: string;                          // JSON field holding the access token (default access_token)
  pathParams?: Record<string, string>;          // path-param name -> sample value
  unauthTest?: boolean;                          // emit a no-auth 401 negative test per secured GET
}

/** Setup/teardown the runner applies around the whole run (e.g. reset the app to seed state). */
export interface HooksConfig {
  resetUrl?: string;                             // absolute or path; POSTed before the run
  resetHeaders?: Record<string, string>;         // e.g. { "X-Test-Key": "..." } - values may be "env:VAR"
  resetBody?: unknown;
}

export interface RequestRule { raw: string; method: string; path: RegExp }

export interface Target {
  path: string;
  name: string;
  baseUrl: string;
  allowedHosts: string[];
  forbiddenActions: RegExp[];
  forbiddenRequests: RequestRule[];
  credentials: Record<string, CredentialSpec>;
  budget: { maxSteps: number; maxSeconds: number };
  api?: ApiConfig;
  hooks?: HooksConfig;
  auth?: AuthConfig;
}

/** Resolve a path that is relative to the target config file's own directory. */
export function resolveFromTarget(t: Target, p: string): string {
  return resolve(t.path, '..', p);
}

/** "/transfer|delete/i" -> RegExp; a bare string is treated as a case-insensitive pattern. */
export function parseRegex(src: string): RegExp {
  const m = /^\/(.*)\/([a-z]*)$/s.exec(src);
  return m ? new RegExp(m[1]!, m[2]) : new RegExp(src, 'i');
}

/** "POST /api/transfer*" -> { method: POST, path: ^/api/transfer.*$ }. Method "*" or omitted = any. */
export function parseRequestRule(raw: string): RequestRule {
  const parts = raw.trim().split(/\s+/);
  const [method, pattern] = parts.length === 1 ? ['*', parts[0]!] : [parts[0]!.toUpperCase(), parts[1]!];
  const esc = pattern.replace(/[.+?^${}()|[\]\\]/g, '\\$&').replace(/\*/g, '.*');
  return { raw, method, path: new RegExp(`^${esc}$`) };
}

export function loadTarget(file: string): Target {
  const path = resolve(file);
  let cfg: TargetConfigFile;
  try {
    cfg = JSON.parse(readFileSync(path, 'utf8')) as TargetConfigFile;
  } catch (e) {
    throw new Error(`cannot read target config ${path}: ${(e as Error).message}`);
  }
  if (!cfg.baseUrl) throw new Error('target config: baseUrl is required');
  if (!Array.isArray(cfg.allowedHosts) || cfg.allowedHosts.length === 0) {
    throw new Error('target config: allowedHosts must be a non-empty array');
  }
  const credentials: Record<string, CredentialSpec> = {};
  for (const [k, v] of Object.entries(cfg.credentials ?? {})) {
    credentials[k] = typeof v === 'string' ? { env: v, secret: /pass|secret|token|key/i.test(k) } : v;
  }
  return {
    path,
    name: cfg.name ?? new URL(cfg.baseUrl).host,
    baseUrl: cfg.baseUrl.replace(/\/$/, ''),
    allowedHosts: cfg.allowedHosts.map((h) => h.toLowerCase()),
    forbiddenActions: (cfg.forbiddenActions ?? []).map(parseRegex),
    forbiddenRequests: (cfg.forbiddenRequests ?? []).map(parseRequestRule),
    credentials,
    budget: { maxSteps: cfg.budget?.maxSteps ?? 60, maxSeconds: cfg.budget?.maxSeconds ?? 600 },
    api: cfg.api,
    hooks: cfg.hooks,
    auth: cfg.auth,
  };
}

/** Resolve credential values from env. Missing values are an error only when `required`. */
export function credentialValues(t: Target, required = true): Record<string, string> {
  const out: Record<string, string> = {};
  for (const [k, spec] of Object.entries(t.credentials)) {
    const v = process.env[spec.env];
    if (v === undefined || v === '') {
      if (required) throw new Error(`credential "${k}" needs env var ${spec.env} to be set`);
      continue;
    }
    out[k] = v;
  }
  return out;
}

export function secretValues(t: Target): string[] {
  return Object.entries(t.credentials)
    .filter(([, s]) => s.secret)
    .map(([, s]) => process.env[s.env])
    .filter((v): v is string => !!v);
}

// Northwind Bank - branch operations portal. A deliberately COMPLICATED QA target for test-builder.
//   node bank-app/server.ts      app on :4590 (BANK_PORT), card widget (a different origin) on :4591 (BANK_WIDGET_PORT)
// Features: password + TOTP MFA, cookie sessions with idle timeout + CSRF, JWT bearer API with refresh rotation,
// roles (teller / supervisor / auditor), maker-checker approvals, SPA with skeletons and random latency, SQLite
// persistence, bulk CSV payments with a background job + SSE, CSV/PDF statements, rate limiting, idempotency keys,
// feature flags + i18n, a websocket channel, legacy GET-with-side-effects + redirects, and a test-only reset API.
import { createServer, type IncomingMessage, type ServerResponse } from 'node:http';
import { readFileSync, existsSync } from 'node:fs';
import { join, dirname, extname } from 'node:path';
import { fileURLToPath } from 'node:url';
import { createHash, randomUUID } from 'node:crypto';
import type { DatabaseSync } from 'node:sqlite';
import { resetDb, openDb, ibanValid, type UserRow, type Role } from './lib/db.ts';
import { checkPassword, verifyTotp, signJwt, verifyJwt, token } from './lib/crypto.ts';
import { pdf } from './lib/pdf.ts';
import { acceptWebSocket } from './lib/ws.ts';

const here = dirname(fileURLToPath(import.meta.url));
const PORT = Number(process.env.BANK_PORT ?? 4590);
const WIDGET_PORT = Number(process.env.BANK_WIDGET_PORT ?? 4591);
const HOST = '127.0.0.1';
const ORIGIN = `http://${HOST}:${PORT}`;
const WIDGET_ORIGIN = `http://${HOST}:${WIDGET_PORT}`;
const ENV = process.env.BANK_ENV ?? 'test';
const TEST_KEY = process.env.BANK_TEST_KEY ?? 'qa-reset-key-2026';
const JWT_KEY = process.env.BANK_JWT_KEY ?? token(32);
const DB_FILE = process.env.BANK_DB ?? join(here, 'data', 'bank.db');

interface Config {
  latencyMin: number; latencyMax: number; sessionIdleSeconds: number; sessionWarnSeconds: number;
  accessTokenTtl: number; rateLimitPer10s: number; totpReplayProtection: boolean; bulkRowMs: number;
}
const [lMin, lMax] = (process.env.BANK_LATENCY ?? '200-1500').split('-').map(Number) as [number, number];
const DEFAULT_CONFIG: Config = {
  latencyMin: lMin, latencyMax: lMax ?? lMin, sessionIdleSeconds: 600, sessionWarnSeconds: 60,
  accessTokenTtl: 300, rateLimitPer10s: 120, totpReplayProtection: process.env.BANK_TOTP_REPLAY === '1', bulkRowMs: 400,
};
let cfg: Config = { ...DEFAULT_CONFIG };

let db: DatabaseSync = existsSync(DB_FILE) && process.env.BANK_RESET_ON_START === '0' ? openDb(DB_FILE) : resetDb(DB_FILE);

interface Session { sid: string; user: string; mfa: boolean; csrf: string; lastSeen: number; created: number }
const sessions = new Map<string, Session>();
const refreshTokens = new Map<string, { user: string; family: string; used: boolean }>();
const revokedFamilies = new Set<string>();
const rate = new Map<string, number[]>();
const sseClients = new Map<string, Set<ServerResponse>>();

// ---------------------------------------------------------------- helpers
class HttpError extends Error {
  status: number; code: string; details?: unknown; headers?: Record<string, string>;
  constructor(status: number, code: string, message: string, details?: unknown, headers?: Record<string, string>) {
    super(message); this.status = status; this.code = code; this.details = details; this.headers = headers;
  }
}

function send(res: ServerResponse, status: number, body: unknown, headers: Record<string, string> = {}): void {
  if (res.headersSent) return;
  const isBuf = Buffer.isBuffer(body);
  const isStr = typeof body === 'string';
  res.writeHead(status, {
    'content-type': isBuf ? 'application/octet-stream' : isStr ? 'text/html; charset=utf-8' : 'application/json',
    'cache-control': 'no-store',
    ...headers,
  });
  res.end(isBuf || isStr ? body : JSON.stringify(body));
}

const sendError = (res: ServerResponse, e: HttpError) =>
  send(res, e.status, { error: { code: e.code, message: e.message, ...(e.details ? { details: e.details } : {}) } }, e.headers ?? {});

async function readBody(req: IncomingMessage): Promise<Buffer> {
  const chunks: Buffer[] = [];
  let n = 0;
  for await (const c of req) { n += (c as Buffer).length; if (n > 2_000_000) throw new HttpError(413, 'PAYLOAD_TOO_LARGE', 'Body too large'); chunks.push(c as Buffer); }
  return Buffer.concat(chunks);
}

async function readJson(req: IncomingMessage): Promise<Record<string, unknown>> {
  const raw = (await readBody(req)).toString('utf8');
  if (!raw) return {};
  try {
    const v = JSON.parse(raw) as unknown;
    if (!v || typeof v !== 'object' || Array.isArray(v)) throw new Error();
    return v as Record<string, unknown>;
  } catch {
    throw new HttpError(400, 'MALFORMED_JSON', 'Request body must be a JSON object');
  }
}

const cookies = (req: IncomingMessage) => Object.fromEntries((req.headers.cookie ?? '').split(/;\s*/).filter(Boolean).map((c) => {
  const i = c.indexOf('='); return [c.slice(0, i), decodeURIComponent(c.slice(i + 1))];
}));

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));
const now = () => new Date().toISOString();
const user = (username: string) => db.prepare('SELECT * FROM users WHERE username = ?').get(username) as unknown as UserRow | undefined;

function audit(actor: string | null, action: string, detail: unknown) {
  db.prepare('INSERT INTO audit (at, actor, action, detail) VALUES (?,?,?,?)').run(now(), actor, action, JSON.stringify(detail));
}

/** "12,500.00" / "12500" / 12500.5 -> cents. Rejects anything ambiguous. */
function parseAmount(v: unknown): number | null {
  if (typeof v === 'number') return Number.isFinite(v) && v > 0 ? Math.round(v * 100) : null;
  if (typeof v !== 'string') return null;
  const s = v.trim().replace(/,/g, '');
  if (!/^\d+(\.\d{1,2})?$/.test(s)) return null;
  const c = Math.round(Number(s) * 100);
  return c > 0 ? c : null;
}
const money = (c: number) => (c / 100).toFixed(2);

interface Principal { user: UserRow; via: 'session' | 'bearer'; session?: Session }

function sessionFrom(req: IncomingMessage): Session | null {
  const sid = cookies(req).nw_sid;
  const s = sid ? sessions.get(sid) : undefined;
  if (!s) return null;
  if (Date.now() - s.lastSeen > cfg.sessionIdleSeconds * 1000) { sessions.delete(s.sid); return null; }
  return s;
}

function authenticate(req: IncomingMessage, method: string): Principal {
  const auth = req.headers.authorization;
  if (auth?.startsWith('Bearer ')) {
    const p = verifyJwt(auth.slice(7), JWT_KEY);
    if (p === 'expired') throw new HttpError(401, 'TOKEN_EXPIRED', 'Access token expired', undefined, { 'www-authenticate': 'Bearer error="invalid_token", error_description="expired"' });
    if (!p || typeof p.sub !== 'string') throw new HttpError(401, 'INVALID_TOKEN', 'Invalid access token', undefined, { 'www-authenticate': 'Bearer error="invalid_token"' });
    const u = user(p.sub);
    if (!u) throw new HttpError(401, 'INVALID_TOKEN', 'Unknown subject');
    return { user: u, via: 'bearer' };
  }
  const s = sessionFrom(req);
  if (!s || !s.mfa) throw new HttpError(401, sessionHadCookie(req) ? 'SESSION_EXPIRED' : 'UNAUTHENTICATED', sessionHadCookie(req) ? 'Your session has expired' : 'Authentication required', undefined, { 'www-authenticate': 'Bearer' });
  if (!['GET', 'HEAD'].includes(method) && req.headers['x-csrf-token'] !== s.csrf) throw new HttpError(403, 'CSRF_FAILED', 'Missing or invalid CSRF token');
  if (req.headers['x-background'] !== '1') s.lastSeen = Date.now();
  return { user: user(s.user)!, via: 'session', session: s };
}
const sessionHadCookie = (req: IncomingMessage) => !!cookies(req).nw_sid;

function requireRole(p: Principal, ...roles: Role[]) {
  if (!roles.includes(p.user.role)) throw new HttpError(403, 'FORBIDDEN', `Your role (${p.user.role}) is not allowed to do this`);
}

function rateLimit(key: string) {
  const t = Date.now();
  const arr = (rate.get(key) ?? []).filter((x) => t - x < 10_000);
  arr.push(t);
  rate.set(key, arr);
  if (arr.length > cfg.rateLimitPer10s) {
    const retry = Math.ceil((10_000 - (t - arr[0]!)) / 1000);
    throw new HttpError(429, 'RATE_LIMITED', 'Too many requests', { limitPer10s: cfg.rateLimitPer10s }, { 'retry-after': String(retry) });
  }
}

// ---------------------------------------------------------------- domain
type Row = Record<string, unknown>;
const accountOut = (a: Row) => ({ id: a.id, name: a.name, holder: a.holder, iban: a.iban, currency: a.currency, balance: money(a.balance_cents as number), status: a.status, nickname: a.nickname ?? null });
const payeeOut = (p: Row) => ({ id: p.id, name: p.name, iban: p.iban, bank: p.bank });
function paymentOut(p: Row) {
  const payee = db.prepare('SELECT name FROM payees WHERE id = ?').get(p.payee_id as string) as Row | undefined;
  return { id: p.id, fromAccountId: p.from_account, payeeId: p.payee_id, payeeName: payee?.name ?? null, amount: money(p.amount_cents as number), currency: p.currency, reference: p.reference, executeOn: p.execute_on, status: p.status, createdBy: p.created_by, approvedBy: p.approved_by ?? null, channel: p.channel, createdAt: p.created_at };
}

function getAccount(id: string): Row {
  const a = db.prepare('SELECT * FROM accounts WHERE id = ?').get(id) as Row | undefined;
  if (!a) throw new HttpError(404, 'NOT_FOUND', `Account ${id} not found`);
  return a;
}

function execute(paymentId: string) {
  const p = db.prepare('SELECT * FROM payments WHERE id = ?').get(paymentId) as Row;
  db.prepare('UPDATE accounts SET balance_cents = balance_cents - ? WHERE id = ?').run(p.amount_cents as number, p.from_account as string);
}

interface PaymentInput { fromAccountId?: unknown; payeeId?: unknown; amount?: unknown; currency?: unknown; reference?: unknown; executeOn?: unknown }

function validatePayment(b: PaymentInput): { errors: { field: string; message: string }[]; value?: { from: string; payee: string; cents: number; currency: string; reference: string; executeOn: string } } {
  const errors: { field: string; message: string }[] = [];
  const from = typeof b.fromAccountId === 'string' ? b.fromAccountId : '';
  const payee = typeof b.payeeId === 'string' ? b.payeeId : '';
  const cents = parseAmount(b.amount);
  const currency = typeof b.currency === 'string' ? b.currency : 'GBP';
  const reference = typeof b.reference === 'string' ? b.reference.trim() : '';
  const executeOn = typeof b.executeOn === 'string' ? b.executeOn : new Date().toISOString().slice(0, 10);
  const acc = from ? (db.prepare('SELECT * FROM accounts WHERE id = ?').get(from) as Row | undefined) : undefined;
  if (!acc) errors.push({ field: 'fromAccountId', message: 'Choose an account to pay from' });
  else if (acc.status !== 'open') errors.push({ field: 'fromAccountId', message: 'Account is closed' });
  if (!payee || !db.prepare('SELECT 1 FROM payees WHERE id = ?').get(payee)) errors.push({ field: 'payeeId', message: 'Choose a payee' });
  if (cents === null) errors.push({ field: 'amount', message: 'Enter an amount greater than 0 with at most 2 decimals' });
  else if (cents > 100_000_000) errors.push({ field: 'amount', message: 'Amount exceeds the 1,000,000.00 maximum' });
  if (currency !== 'GBP' && currency !== 'EUR') errors.push({ field: 'currency', message: 'Currency must be GBP or EUR' });
  if (!reference) errors.push({ field: 'reference', message: 'Reference is required' });
  else if (reference.length > 35) errors.push({ field: 'reference', message: 'Reference must be 35 characters or fewer' });
  if (!/^\d{4}-\d{2}-\d{2}$/.test(executeOn)) errors.push({ field: 'executeOn', message: 'Execution date must be YYYY-MM-DD' });
  if (errors.length) return { errors };
  return { errors, value: { from, payee, cents: cents!, currency, reference, executeOn } };
}

function createPayment(p: Principal, body: PaymentInput, idemKey: string | null, channel: string, batchId: string | null = null) {
  requireRole(p, 'teller', 'supervisor');
  const v = validatePayment(body);
  if (!v.value) throw new HttpError(400, 'VALIDATION_ERROR', 'Payment is invalid', v.errors);
  const hash = createHash('sha256').update(JSON.stringify(v.value)).digest('hex');
  if (idemKey) {
    const prior = db.prepare('SELECT * FROM payments WHERE created_by = ? AND idempotency_key = ?').get(p.user.username, idemKey) as Row | undefined;
    if (prior) {
      if (prior.request_hash !== hash) throw new HttpError(422, 'IDEMPOTENCY_KEY_REUSED', 'Idempotency-Key was already used with a different request');
      return { replay: true, payment: prior };
    }
  }
  const acc = getAccount(v.value.from);
  const pending = v.value.cents > p.user.payment_limit_cents;
  if (!pending && (acc.balance_cents as number) < v.value.cents) throw new HttpError(422, 'INSUFFICIENT_FUNDS', 'Insufficient funds');
  const id = `PMT-${String(Math.floor(Math.random() * 900000) + 100000)}`;
  db.prepare('INSERT INTO payments (id, from_account, payee_id, amount_cents, currency, reference, execute_on, status, created_by, idempotency_key, request_hash, channel, batch_id, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)')
    .run(id, v.value.from, v.value.payee, v.value.cents, v.value.currency, v.value.reference, v.value.executeOn, pending ? 'pending_approval' : 'completed', p.user.username, idemKey, hash, channel, batchId, now());
  if (!pending) execute(id);
  audit(p.user.username, 'payment.created', { id, amount: money(v.value.cents), status: pending ? 'pending_approval' : 'completed', channel });
  return { replay: false, payment: db.prepare('SELECT * FROM payments WHERE id = ?').get(id) as Row };
}

// ---------------------------------------------------------------- CSV
const CSV_HEADER = ['payee_name', 'iban', 'amount', 'currency', 'reference'];
function parseCsv(text: string): string[][] {
  const rows: string[][] = [];
  for (const line of text.replace(/^﻿/, '').split(/\r?\n/)) {
    if (!line.trim()) continue;
    const cells: string[] = [];
    let cur = ''; let q = false;
    for (let i = 0; i < line.length; i++) {
      const c = line[i]!;
      if (q) { if (c === '"' && line[i + 1] === '"') { cur += '"'; i++; } else if (c === '"') q = false; else cur += c; }
      else if (c === '"') q = true;
      else if (c === ',') { cells.push(cur); cur = ''; }
      else cur += c;
    }
    cells.push(cur);
    rows.push(cells.map((x) => x.trim()));
  }
  return rows;
}

interface BulkRow { row: number; payeeName: string; iban: string; amount: string; currency: string; reference: string }
function validateBulk(text: string): { rows: BulkRow[]; errors: { row: number; field: string; message: string }[] } {
  const all = parseCsv(text);
  const errors: { row: number; field: string; message: string }[] = [];
  if (!all.length) return { rows: [], errors: [{ row: 0, field: 'file', message: 'The file is empty' }] };
  const head = all[0]!.map((h) => h.toLowerCase());
  if (CSV_HEADER.some((h, i) => head[i] !== h)) return { rows: [], errors: [{ row: 1, field: 'header', message: `Header must be: ${CSV_HEADER.join(',')}` }] };
  const rows: BulkRow[] = [];
  all.slice(1).forEach((cells, i) => {
    const row = i + 2;
    if (cells.length !== CSV_HEADER.length) { errors.push({ row, field: 'row', message: `Expected ${CSV_HEADER.length} columns, found ${cells.length}` }); return; }
    const [payeeName, iban, amount, currency, reference] = cells as [string, string, string, string, string];
    if (!payeeName) errors.push({ row, field: 'payee_name', message: 'Payee name is required' });
    if (!ibanValid(iban)) errors.push({ row, field: 'iban', message: 'IBAN is invalid' });
    if (parseAmount(amount) === null) errors.push({ row, field: 'amount', message: 'Amount must be a positive number' });
    if (currency !== 'GBP' && currency !== 'EUR') errors.push({ row, field: 'currency', message: `Unsupported currency "${currency}"` });
    if (!reference) errors.push({ row, field: 'reference', message: 'Reference is required' });
    rows.push({ row, payeeName, iban, amount, currency, reference });
  });
  if (rows.length > 500) errors.push({ row: 0, field: 'file', message: 'At most 500 rows per file' });
  if (!rows.length && !errors.length) errors.push({ row: 0, field: 'file', message: 'The file has no payment rows' });
  return { rows, errors };
}

async function csvFromRequest(req: IncomingMessage): Promise<string> {
  const ct = req.headers['content-type'] ?? '';
  const body = await readBody(req);
  if (ct.startsWith('multipart/form-data')) {
    const boundary = /boundary=([^;]+)/.exec(ct)?.[1];
    if (!boundary) throw new HttpError(400, 'BAD_MULTIPART', 'Missing multipart boundary');
    const parts = body.toString('latin1').split(`--${boundary}`);
    const file = parts.find((p) => /name="file"/.test(p));
    if (!file) throw new HttpError(400, 'BAD_MULTIPART', 'Expected a "file" part');
    const i = file.indexOf('\r\n\r\n');
    return Buffer.from(file.slice(i + 4).replace(/\r\n$/, ''), 'latin1').toString('utf8');
  }
  if (ct.startsWith('text/csv') || ct.startsWith('text/plain')) return body.toString('utf8');
  throw new HttpError(415, 'UNSUPPORTED_MEDIA_TYPE', 'Send text/csv or multipart/form-data');
}

function pushSse(jobId: string) {
  const job = db.prepare('SELECT * FROM bulk_jobs WHERE id = ?').get(jobId) as Row;
  for (const res of sseClients.get(jobId) ?? []) {
    res.write(`event: progress\ndata: ${JSON.stringify(jobOut(job))}\n\n`);
    if (job.status === 'completed' || job.status === 'completed_with_errors') res.end();
  }
}
const jobOut = (j: Row) => ({ id: j.id, status: j.status, total: j.total, processed: j.processed, failed: j.failed, createdBy: j.created_by, createdAt: j.created_at });

function runBulkJob(jobId: string, p: Principal) {
  const job = db.prepare('SELECT * FROM bulk_jobs WHERE id = ?').get(jobId) as Row;
  const rows = JSON.parse(job.rows as string) as BulkRow[];
  let i = 0;
  db.prepare("UPDATE bulk_jobs SET status = 'processing' WHERE id = ?").run(jobId);
  const tick = () => {
    if (i >= rows.length) {
      const j = db.prepare('SELECT failed FROM bulk_jobs WHERE id = ?').get(jobId) as Row;
      db.prepare('UPDATE bulk_jobs SET status = ? WHERE id = ?').run((j.failed as number) ? 'completed_with_errors' : 'completed', jobId);
      pushSse(jobId);
      return;
    }
    const r = rows[i++]!;
    try {
      let payee = db.prepare('SELECT id FROM payees WHERE replace(iban, \' \', \'\') = ?').get(r.iban.replace(/\s/g, '').toUpperCase()) as Row | undefined;
      if (!payee) {
        const id = `PAY-B${Math.floor(Math.random() * 1e6)}`;
        db.prepare('INSERT INTO payees (id, name, iban, bank, created_by) VALUES (?,?,?,?,?)').run(id, r.payeeName, r.iban.replace(/\s/g, '').toUpperCase(), 'Unknown', p.user.username);
        payee = { id };
      }
      createPayment(p, { fromAccountId: 'ACC-1002', payeeId: payee.id, amount: r.amount, currency: r.currency, reference: r.reference }, `${jobId}-${r.row}`, 'bulk', jobId);
      db.prepare('UPDATE bulk_jobs SET processed = processed + 1 WHERE id = ?').run(jobId);
    } catch {
      db.prepare('UPDATE bulk_jobs SET processed = processed + 1, failed = failed + 1 WHERE id = ?').run(jobId);
    }
    pushSse(jobId);
    setTimeout(tick, cfg.bulkRowMs);
  };
  setTimeout(tick, cfg.bulkRowMs);
}

// ---------------------------------------------------------------- statements
function statementRows(accountId: string, from?: string, to?: string) {
  const f = from && /^\d{4}-\d{2}-\d{2}$/.test(from) ? from : '0000-00-00';
  const t = to && /^\d{4}-\d{2}-\d{2}$/.test(to) ? to : '9999-99-99';
  return db.prepare('SELECT * FROM transactions WHERE account_id = ? AND booked_on >= ? AND booked_on <= ? ORDER BY booked_on, id').all(accountId, f, t) as Row[];
}

// ---------------------------------------------------------------- router
type Handler = (ctx: { req: IncomingMessage; res: ServerResponse; params: string[]; url: URL; method: string }) => Promise<void> | void;
const routes: [string, RegExp, Handler][] = [];
const on = (method: string, path: RegExp, h: Handler) => routes.push([method, path, h]);

const api = (h: (ctx: Parameters<Handler>[0] & { p: Principal }) => Promise<unknown> | unknown, opts: { latency?: boolean } = {}): Handler => async (ctx) => {
  const p = authenticate(ctx.req, ctx.method);
  rateLimit(p.user.username);
  if (opts.latency !== false && cfg.latencyMax > 0) await sleep(cfg.latencyMin + Math.random() * (cfg.latencyMax - cfg.latencyMin));
  const out = await h({ ...ctx, p });
  if (out !== undefined) send(ctx.res, 200, out);
};

const page = (file: string) => readFileSync(join(here, 'public', file), 'utf8');
const TYPES: Record<string, string> = { '.js': 'text/javascript', '.css': 'text/css', '.html': 'text/html; charset=utf-8', '.svg': 'image/svg+xml', '.json': 'application/json' };

function spa(res: ServerResponse) {
  const pre = token(18);
  send(res, 200, page('index.html').replace('__CSRF__', pre).replace('__WIDGET_ORIGIN__', WIDGET_ORIGIN), {
    'set-cookie': `nw_pre=${pre}; HttpOnly; SameSite=Strict; Path=/`,
  });
}

// --- web auth
on('POST', /^\/auth\/login$/, async ({ req, res }) => {
  const pre = cookies(req).nw_pre;
  if (!pre || req.headers['x-csrf-token'] !== pre) throw new HttpError(403, 'CSRF_FAILED', 'Missing or invalid CSRF token');
  const b = await readJson(req);
  await sleep(300 + Math.random() * 400);
  const u = typeof b.username === 'string' ? user(b.username) : undefined;
  if (u && u.failed_logins >= 5) throw new HttpError(423, 'ACCOUNT_LOCKED', 'Too many failed attempts. Contact your supervisor.');
  if (!u || typeof b.password !== 'string' || !checkPassword(b.password, u.password_hash)) {
    if (u) db.prepare('UPDATE users SET failed_logins = failed_logins + 1 WHERE id = ?').run(u.id);
    audit(typeof b.username === 'string' ? b.username : null, 'login.failed', {});
    throw new HttpError(401, 'INVALID_CREDENTIALS', 'Incorrect username or password');
  }
  const s: Session = { sid: token(), user: u.username, mfa: false, csrf: token(), lastSeen: Date.now(), created: Date.now() };
  sessions.set(s.sid, s);
  send(res, 200, { mfaRequired: true, csrf: s.csrf }, { 'set-cookie': `nw_sid=${s.sid}; HttpOnly; SameSite=Lax; Path=/` });
});

on('POST', /^\/auth\/mfa$/, async ({ req, res }) => {
  const s = sessionFrom(req);
  if (!s) throw new HttpError(401, 'SESSION_EXPIRED', 'Start again: your sign-in session expired');
  if (req.headers['x-csrf-token'] !== s.csrf) throw new HttpError(403, 'CSRF_FAILED', 'Missing or invalid CSRF token');
  const b = await readJson(req);
  const u = user(s.user)!;
  const code = typeof b.code === 'string' ? b.code.replace(/\s/g, '') : '';
  const counter = /^\d{6}$/.test(code) ? verifyTotp(u.totp_secret, code) : null;
  if (counter === null) throw new HttpError(401, 'INVALID_OTP', 'That code is not valid. Check your authenticator app.');
  if (cfg.totpReplayProtection && counter <= u.last_totp_counter) {
    throw new HttpError(401, 'OTP_REUSED', 'That code was already used. Wait for the next code.');
  }
  db.prepare('UPDATE users SET last_totp_counter = ?, failed_logins = 0 WHERE id = ?').run(counter, u.id);
  sessions.delete(s.sid);
  const full: Session = { ...s, sid: token(), mfa: true, csrf: token(), lastSeen: Date.now() };
  sessions.set(full.sid, full);
  audit(u.username, 'login.success', {});
  send(res, 200, { csrf: full.csrf, user: { username: u.username, role: u.role } }, { 'set-cookie': `nw_sid=${full.sid}; HttpOnly; SameSite=Lax; Path=/` });
});

on('POST', /^\/auth\/logout$/, ({ req, res }) => {
  const s = sessionFrom(req);
  if (s) sessions.delete(s.sid);
  send(res, 200, { ok: true }, { 'set-cookie': 'nw_sid=; Max-Age=0; Path=/' });
});

on('POST', /^\/auth\/keepalive$/, ({ req, res }) => {
  const s = sessionFrom(req);
  if (!s || !s.mfa) throw new HttpError(401, 'SESSION_EXPIRED', 'Your session has expired');
  if (req.headers['x-csrf-token'] !== s.csrf) throw new HttpError(403, 'CSRF_FAILED', 'Missing or invalid CSRF token');
  s.lastSeen = Date.now();
  send(res, 200, { idleSeconds: cfg.sessionIdleSeconds });
});

on('GET', /^\/auth\/session$/, ({ req, res }) => {
  // Background poll: does NOT extend the session.
  const s = sessionFrom(req);
  if (!s || !s.mfa) return send(res, 200, { active: false });
  send(res, 200, { active: true, remainingSeconds: Math.max(0, Math.round(cfg.sessionIdleSeconds - (Date.now() - s.lastSeen) / 1000)), warnSeconds: cfg.sessionWarnSeconds });
});

// --- token API
on('POST', /^\/api\/v1\/auth\/token$/, async ({ req, res }) => {
  const b = await readJson(req);
  rateLimit(`token:${String(b.username)}`);
  const u = typeof b.username === 'string' ? user(b.username) : undefined;
  if (!u || typeof b.password !== 'string' || !checkPassword(b.password, u.password_hash)) throw new HttpError(401, 'INVALID_CREDENTIALS', 'Incorrect username or password');
  if (typeof b.otp !== 'string' || verifyTotp(u.totp_secret, b.otp) === null) throw new HttpError(401, 'INVALID_OTP', 'A valid one-time code is required');
  const family = token(8);
  const refresh = token();
  refreshTokens.set(refresh, { user: u.username, family, used: false });
  send(res, 200, { access_token: signJwt({ sub: u.username, role: u.role }, JWT_KEY, cfg.accessTokenTtl), token_type: 'Bearer', expires_in: cfg.accessTokenTtl, refresh_token: refresh });
});

on('POST', /^\/api\/v1\/auth\/refresh$/, async ({ req, res }) => {
  const b = await readJson(req);
  const rt = typeof b.refresh_token === 'string' ? refreshTokens.get(b.refresh_token) : undefined;
  if (!rt || revokedFamilies.has(rt.family)) throw new HttpError(401, 'INVALID_REFRESH_TOKEN', 'Refresh token is invalid');
  if (rt.used) { revokedFamilies.add(rt.family); throw new HttpError(401, 'REFRESH_TOKEN_REUSED', 'Refresh token reuse detected; all tokens in this family are revoked'); }
  rt.used = true;
  const next = token();
  refreshTokens.set(next, { user: rt.user, family: rt.family, used: false });
  const u = user(rt.user)!;
  send(res, 200, { access_token: signJwt({ sub: u.username, role: u.role }, JWT_KEY, cfg.accessTokenTtl), token_type: 'Bearer', expires_in: cfg.accessTokenTtl, refresh_token: next });
});

// --- me / flags
on('GET', /^\/api\/v1\/me$/, api(({ p }) => ({
  username: p.user.username, displayName: p.user.display_name, role: p.user.role, locale: p.user.locale,
  paymentLimit: money(p.user.payment_limit_cents), flags: JSON.parse(p.user.flags) as unknown, csrf: p.session?.csrf ?? null,
}), { latency: false }));
on('PATCH', /^\/api\/v1\/me$/, api(async ({ req, p }) => {
  const b = await readJson(req);
  if (b.locale !== 'en' && b.locale !== 'de') throw new HttpError(400, 'VALIDATION_ERROR', 'Invalid locale', [{ field: 'locale', message: 'locale must be en or de' }]);
  db.prepare('UPDATE users SET locale = ? WHERE id = ?').run(b.locale, p.user.id);
  return { locale: b.locale };
}, { latency: false }));

// --- accounts
on('GET', /^\/api\/v1\/accounts$/, api(() => (db.prepare('SELECT * FROM accounts ORDER BY id').all() as Row[]).map(accountOut)));
on('GET', /^\/api\/v1\/accounts\/([\w-]+)$/, api(({ params }) => accountOut(getAccount(params[0]!))));
on('PATCH', /^\/api\/v1\/accounts\/([\w-]+)$/, api(async ({ req, p, params }) => {
  requireRole(p, 'teller', 'supervisor');
  getAccount(params[0]!);
  const b = await readJson(req);
  if (typeof b.nickname !== 'string' || b.nickname.length > 40) throw new HttpError(400, 'VALIDATION_ERROR', 'Invalid nickname', [{ field: 'nickname', message: 'nickname must be a string of at most 40 characters' }]);
  db.prepare('UPDATE accounts SET nickname = ? WHERE id = ?').run(b.nickname, params[0]!);
  return accountOut(getAccount(params[0]!));
}));
on('POST', /^\/api\/v1\/accounts\/([\w-]+)\/close$/, api(({ p, params }) => {
  requireRole(p, 'supervisor');
  const a = getAccount(params[0]!);
  if (a.status === 'closed') throw new HttpError(409, 'ALREADY_CLOSED', 'Account is already closed');
  if ((a.balance_cents as number) !== 0) throw new HttpError(409, 'BALANCE_NOT_ZERO', 'Only accounts with a zero balance can be closed');
  db.prepare("UPDATE accounts SET status = 'closed' WHERE id = ?").run(params[0]!);
  audit(p.user.username, 'account.closed', { id: params[0] });
  return accountOut(getAccount(params[0]!));
}));

on('GET', /^\/api\/v1\/accounts\/([\w-]+)\/transactions$/, api(({ params, url }) => {
  getAccount(params[0]!);
  const q = url.searchParams;
  const pageNo = Math.max(1, Number(q.get('page') ?? 1) || 1);
  const size = Math.min(100, Math.max(1, Number(q.get('pageSize') ?? 25) || 25));
  const sortCols: Record<string, string> = { date: 'booked_on', amount: 'amount_cents', description: 'description', category: 'category' };
  const sort = sortCols[q.get('sort') ?? 'date'];
  if (!sort) throw new HttpError(400, 'VALIDATION_ERROR', 'Invalid sort', [{ field: 'sort', message: `sort must be one of ${Object.keys(sortCols).join(', ')}` }]);
  const order = q.get('order') === 'asc' ? 'ASC' : 'DESC';
  const where = ['account_id = ?'];
  const args: (string | number)[] = [params[0]!];
  if (q.get('q')) { where.push('description LIKE ?'); args.push(`%${q.get('q')}%`); }
  if (q.get('category')) { where.push('category = ?'); args.push(q.get('category')!); }
  if (q.get('from')) { where.push('booked_on >= ?'); args.push(q.get('from')!); }
  if (q.get('to')) { where.push('booked_on <= ?'); args.push(q.get('to')!); }
  const total = (db.prepare(`SELECT count(*) n FROM transactions WHERE ${where.join(' AND ')}`).get(...args) as Row).n as number;
  const items = (db.prepare(`SELECT * FROM transactions WHERE ${where.join(' AND ')} ORDER BY ${sort} ${order}, id ${order} LIMIT ? OFFSET ?`).all(...args, size, (pageNo - 1) * size) as Row[])
    .map((t) => ({ id: t.id, date: t.booked_on, description: t.description, category: t.category, amount: money(t.amount_cents as number), balance: money(t.balance_cents as number) }));
  return { items, page: pageNo, pageSize: size, total, totalPages: Math.max(1, Math.ceil(total / size)) };
}));

on('GET', /^\/api\/v1\/accounts\/([\w-]+)\/statement$/, api(({ res, params, url }) => {
  const a = getAccount(params[0]!);
  const from = url.searchParams.get('from') ?? undefined;
  const to = url.searchParams.get('to') ?? undefined;
  const rows = statementRows(a.id as string, from, to);
  const fmt = url.searchParams.get('format') ?? 'csv';
  const name = `statement-${a.id}-${from ?? 'all'}-${to ?? 'all'}`;
  if (fmt === 'csv') {
    const lines = [`# Northwind Bank statement for ${a.iban} (${a.holder})`, 'date,description,category,amount,balance',
      ...rows.map((t) => `${t.booked_on},"${String(t.description).replace(/"/g, '""')}",${t.category},${money(t.amount_cents as number)},${money(t.balance_cents as number)}`)];
    send(res, 200, lines.join('\n') + '\n', { 'content-type': 'text/csv; charset=utf-8', 'content-disposition': `attachment; filename="${name}.csv"` });
    return;
  }
  if (fmt === 'pdf') {
    const sum = rows.reduce((s, t) => s + (t.amount_cents as number), 0);
    const closing = rows.length ? (rows.at(-1)!.balance_cents as number) : (a.balance_cents as number);
    const lines = ['Northwind Bank - Account statement', `Account: ${a.name} ${a.iban}`, `Holder: ${a.holder}`, `Period: ${from ?? 'start'} to ${to ?? 'today'}`,
      `Transactions: ${rows.length}`, `Net movement: ${money(sum)} GBP`, `Closing balance: ${money(closing)} GBP`, '',
      ...rows.map((t) => `${t.booked_on}  ${String(t.description).padEnd(22)} ${money(t.amount_cents as number).padStart(10)}  ${money(t.balance_cents as number).padStart(10)}`)];
    send(res, 200, pdf(lines, `Statement ${a.id}`), { 'content-type': 'application/pdf', 'content-disposition': `attachment; filename="${name}.pdf"` });
    return;
  }
  throw new HttpError(400, 'VALIDATION_ERROR', 'Invalid format', [{ field: 'format', message: 'format must be csv or pdf' }]);
}));

on('GET', /^\/api\/v1\/accounts\/([\w-]+)\/cards$/, api(({ params }) => (db.prepare('SELECT id, last4, brand FROM cards WHERE account_id = ?').all(params[0]!) as Row[])));
on('POST', /^\/api\/v1\/cards$/, api(async ({ req, p }) => {
  requireRole(p, 'teller', 'supervisor');
  const b = await readJson(req);
  if (typeof b.token !== 'string' || !b.token.startsWith('tok_') || typeof b.accountId !== 'string') throw new HttpError(400, 'VALIDATION_ERROR', 'token and accountId are required');
  getAccount(b.accountId);
  const t = widgetTokens.get(b.token);
  if (!t) throw new HttpError(400, 'INVALID_CARD_TOKEN', 'Unknown card token');
  const id = `CARD-${Math.floor(Math.random() * 1e6)}`;
  db.prepare('INSERT INTO cards (id, account_id, last4, brand, token, created_by) VALUES (?,?,?,?,?,?)').run(id, b.accountId, t.last4, t.brand, b.token, p.user.username);
  return { id, last4: t.last4, brand: t.brand };
}));

// --- payees
on('GET', /^\/api\/v1\/payees$/, api(({ url }) => {
  const q = url.searchParams.get('q') ?? '';
  return (db.prepare('SELECT * FROM payees WHERE name LIKE ? ORDER BY name LIMIT ?').all(`%${q}%`, q ? 8 : 100) as Row[]).map(payeeOut);
}, { latency: true }));
on('GET', /^\/api\/v1\/payees\/([\w-]+)$/, api(({ params }) => {
  const r = db.prepare('SELECT * FROM payees WHERE id = ?').get(params[0]!) as Row | undefined;
  if (!r) throw new HttpError(404, 'NOT_FOUND', 'Payee not found');
  return payeeOut(r);
}));
function payeeBody(b: Record<string, unknown>) {
  const errors: { field: string; message: string }[] = [];
  const name = typeof b.name === 'string' ? b.name.trim() : '';
  const iban = typeof b.iban === 'string' ? b.iban.replace(/\s/g, '').toUpperCase() : '';
  if (!name) errors.push({ field: 'name', message: 'Name is required' });
  if (!ibanValid(iban)) errors.push({ field: 'iban', message: 'IBAN is invalid' });
  if (errors.length) throw new HttpError(400, 'VALIDATION_ERROR', 'Payee is invalid', errors);
  return { name, iban, bank: typeof b.bank === 'string' && b.bank ? b.bank : 'Unknown' };
}
on('POST', /^\/api\/v1\/payees$/, api(async ({ req, res, p }) => {
  requireRole(p, 'teller', 'supervisor');
  const v = payeeBody(await readJson(req));
  const id = `PAY-${Math.floor(Math.random() * 9e5) + 1e5}`;
  db.prepare('INSERT INTO payees (id, name, iban, bank, created_by) VALUES (?,?,?,?,?)').run(id, v.name, v.iban, v.bank, p.user.username);
  audit(p.user.username, 'payee.created', { id });
  send(res, 201, payeeOut(db.prepare('SELECT * FROM payees WHERE id = ?').get(id) as Row), { location: `/api/v1/payees/${id}` });
}));
on('PUT', /^\/api\/v1\/payees\/([\w-]+)$/, api(async ({ req, p, params }) => {
  requireRole(p, 'teller', 'supervisor');
  if (!db.prepare('SELECT 1 FROM payees WHERE id = ?').get(params[0]!)) throw new HttpError(404, 'NOT_FOUND', 'Payee not found');
  const v = payeeBody(await readJson(req));
  db.prepare('UPDATE payees SET name = ?, iban = ?, bank = ? WHERE id = ?').run(v.name, v.iban, v.bank, params[0]!);
  return payeeOut(db.prepare('SELECT * FROM payees WHERE id = ?').get(params[0]!) as Row);
}));
on('DELETE', /^\/api\/v1\/payees\/([\w-]+)$/, api(({ res, p, params }) => {
  requireRole(p, 'teller', 'supervisor');
  const r = db.prepare('DELETE FROM payees WHERE id = ?').run(params[0]!);
  if (!r.changes) throw new HttpError(404, 'NOT_FOUND', 'Payee not found');
  audit(p.user.username, 'payee.deleted', { id: params[0] });
  res.writeHead(204).end();
}));

// --- payments
on('GET', /^\/api\/v1\/payments$/, api(({ url }) => {
  const status = url.searchParams.get('status');
  const cursor = url.searchParams.get('cursor');
  const limit = Math.min(50, Number(url.searchParams.get('limit') ?? 20) || 20);
  const where: string[] = []; const args: string[] = [];
  if (status) { where.push('status = ?'); args.push(status); }
  if (cursor) { where.push('created_at < ?'); args.push(cursor); }
  const rows = db.prepare(`SELECT * FROM payments ${where.length ? 'WHERE ' + where.join(' AND ') : ''} ORDER BY created_at DESC LIMIT ?`).all(...args, limit + 1) as Row[];
  const items = rows.slice(0, limit).map(paymentOut);
  return { items, nextCursor: rows.length > limit ? (rows[limit - 1]!.created_at as string) : null };
}));
on('GET', /^\/api\/v1\/payments\/([\w-]+)$/, api(({ params }) => {
  const r = db.prepare('SELECT * FROM payments WHERE id = ?').get(params[0]!) as Row | undefined;
  if (!r) throw new HttpError(404, 'NOT_FOUND', 'Payment not found');
  return paymentOut(r);
}));
on('POST', /^\/api\/v1\/payments$/, api(async ({ req, res, p }) => {
  const key = req.headers['idempotency-key'];
  if (typeof key !== 'string' || !/^[\w-]{8,64}$/.test(key)) throw new HttpError(400, 'IDEMPOTENCY_KEY_REQUIRED', 'Idempotency-Key header (8-64 chars) is required');
  const r = createPayment(p, await readJson(req) as PaymentInput, key, p.via === 'bearer' ? 'api' : 'web');
  const out = paymentOut(r.payment);
  send(res, r.replay ? 200 : out.status === 'pending_approval' ? 202 : 201, out, r.replay ? { 'idempotent-replayed': 'true' } : { location: `/api/v1/payments/${out.id}` });
}));
const decide = (verdict: 'approved' | 'rejected') => api(({ p, params }) => {
  requireRole(p, 'supervisor');
  const r = db.prepare('SELECT * FROM payments WHERE id = ?').get(params[0]!) as Row | undefined;
  if (!r) throw new HttpError(404, 'NOT_FOUND', 'Payment not found');
  if (r.status !== 'pending_approval') throw new HttpError(409, 'NOT_PENDING', `Payment is ${r.status}, not pending approval`);
  if (r.created_by === p.user.username) throw new HttpError(403, 'SELF_APPROVAL', 'You cannot approve or reject a payment you created (four-eyes rule)');
  db.prepare('UPDATE payments SET status = ?, approved_by = ?, decided_at = ? WHERE id = ?').run(verdict, p.user.username, now(), params[0]!);
  if (verdict === 'approved') execute(params[0]!);
  audit(p.user.username, `payment.${verdict}`, { id: params[0] });
  return paymentOut(db.prepare('SELECT * FROM payments WHERE id = ?').get(params[0]!) as Row);
});
on('POST', /^\/api\/v1\/payments\/([\w-]+)\/approve$/, decide('approved'));
on('POST', /^\/api\/v1\/payments\/([\w-]+)\/reject$/, decide('rejected'));

// Legacy path kept "for old clients": permanent redirect that preserves the method and body.
on('POST', /^\/api\/v1\/orders$/, ({ res }) => send(res, 308, { moved: '/api/v1/payments' }, { location: '/api/v1/payments' }));

// Batch RPC: one POST that can do anything. Path-based request policies cannot see inside it.
on('POST', /^\/api\/v1\/batch$/, api(async ({ req, p }) => {
  const b = await readJson(req);
  const ops = Array.isArray(b.operations) ? b.operations as { op?: string; args?: PaymentInput }[] : [];
  return { results: ops.map((o) => {
    try {
      if (o.op === 'payments.create') return { ok: true, payment: paymentOut(createPayment(p, o.args ?? {}, null, 'batch').payment) };
      if (o.op === 'ping') return { ok: true };
      return { ok: false, error: 'unknown op' };
    } catch (e) { return { ok: false, error: (e as Error).message }; }
  }) };
}));

// --- bulk
on('POST', /^\/api\/v1\/bulk-payments\/validate$/, api(async ({ req, p }) => {
  requireRole(p, 'teller', 'supervisor');
  const v = validateBulk(await csvFromRequest(req));
  return { valid: !v.errors.length, rows: v.rows.length, errors: v.errors, totalAmount: money(v.rows.reduce((s, r) => s + (parseAmount(r.amount) ?? 0), 0)) };
}));
on('POST', /^\/api\/v1\/bulk-payments$/, api(async ({ req, res, p }) => {
  requireRole(p, 'teller', 'supervisor');
  const v = validateBulk(await csvFromRequest(req));
  if (v.errors.length) throw new HttpError(400, 'VALIDATION_ERROR', 'The file has errors', v.errors);
  const id = `JOB-${Math.floor(Math.random() * 9e5) + 1e5}`;
  db.prepare('INSERT INTO bulk_jobs (id, created_by, status, total, rows, created_at) VALUES (?,?,?,?,?,?)').run(id, p.user.username, 'queued', v.rows.length, JSON.stringify(v.rows), now());
  audit(p.user.username, 'bulk.submitted', { id, rows: v.rows.length });
  runBulkJob(id, p);
  send(res, 202, jobOut(db.prepare('SELECT * FROM bulk_jobs WHERE id = ?').get(id) as Row), { location: `/api/v1/bulk-payments/${id}` });
}));
on('GET', /^\/api\/v1\/bulk-payments\/([\w-]+)$/, api(({ params }) => {
  const j = db.prepare('SELECT * FROM bulk_jobs WHERE id = ?').get(params[0]!) as Row | undefined;
  if (!j) throw new HttpError(404, 'NOT_FOUND', 'Job not found');
  return jobOut(j);
}, { latency: false }));
on('GET', /^\/api\/v1\/bulk-payments\/([\w-]+)\/events$/, ({ req, res, params }) => {
  authenticate(req, 'GET');
  const j = db.prepare('SELECT * FROM bulk_jobs WHERE id = ?').get(params[0]!) as Row | undefined;
  if (!j) throw new HttpError(404, 'NOT_FOUND', 'Job not found');
  res.writeHead(200, { 'content-type': 'text/event-stream', 'cache-control': 'no-store', connection: 'keep-alive' });
  res.write(`event: progress\ndata: ${JSON.stringify(jobOut(j))}\n\n`);
  if (j.status === 'completed' || j.status === 'completed_with_errors') { res.end(); return; }
  const set = sseClients.get(params[0]!) ?? new Set();
  set.add(res); sseClients.set(params[0]!, set);
  req.on('close', () => set.delete(res));
});

// --- legacy pages with side effects (as real legacy apps have)
on('GET', /^\/legacy\/repeat-payment$/, ({ req, res, url }) => {
  const s = sessionFrom(req);
  if (!s?.mfa) return send(res, 302, '', { location: '/login' });
  const prior = db.prepare('SELECT * FROM payments WHERE id = ?').get(url.searchParams.get('id') ?? '') as Row | undefined;
  if (!prior) return send(res, 302, '', { location: '/payments?repeat=notfound' });
  const p: Principal = { user: user(s.user)!, via: 'session', session: s };
  try {
    const r = createPayment(p, { fromAccountId: prior.from_account, payeeId: prior.payee_id, amount: money(prior.amount_cents as number), currency: prior.currency, reference: `Repeat of ${prior.id}` }, null, 'legacy-get');
    send(res, 302, '', { location: `/payments?repeated=${r.payment.id as string}` });
  } catch (e) {
    send(res, 302, '', { location: `/payments?repeat=${encodeURIComponent((e as Error).message)}` });
  }
});
on('GET', /^\/go$/, ({ res, url }) => send(res, 302, '', { location: url.searchParams.get('url') ?? '/' }));

// --- test-only
const testOnly = (h: Handler): Handler => (ctx) => {
  if (ENV !== 'test') throw new HttpError(404, 'NOT_FOUND', 'Not found');
  if (ctx.req.headers['x-test-key'] !== TEST_KEY) throw new HttpError(403, 'FORBIDDEN', 'X-Test-Key required');
  return h(ctx);
};
on('POST', /^\/api\/_test\/reset$/, testOnly(async ({ req, res }) => {
  const b = await readJson(req);
  db.close();
  db = resetDb(DB_FILE);
  sessions.clear(); refreshTokens.clear(); revokedFamilies.clear(); rate.clear(); widgetTokens.clear();
  for (const set of sseClients.values()) for (const r of set) r.end();
  sseClients.clear();
  cfg = { ...DEFAULT_CONFIG, ...(b.config && typeof b.config === 'object' ? b.config as Partial<Config> : {}) };
  send(res, 200, { ok: true, config: cfg });
}));
on('GET', /^\/api\/_test\/audit$/, testOnly(({ res }) => send(res, 200, db.prepare('SELECT * FROM audit ORDER BY id').all())));
on('GET', /^\/api\/_test\/payments$/, testOnly(({ res }) => send(res, 200, (db.prepare('SELECT * FROM payments ORDER BY created_at').all() as Row[]).map(paymentOut))));

on('GET', /^\/openapi\.json$/, ({ res }) => send(res, 200, JSON.parse(readFileSync(join(here, 'openapi.json'), 'utf8'))));
on('GET', /^\/sw\.js$/, ({ res }) => send(res, 200, page('sw.js'), { 'content-type': 'text/javascript', 'service-worker-allowed': '/' }));
on('GET', /^\/static\/([\w.-]+)$/, ({ res, params }) => {
  const f = join(here, 'public', params[0]!);
  if (!existsSync(f)) throw new HttpError(404, 'NOT_FOUND', 'Not found');
  send(res, 200, readFileSync(f), { 'content-type': TYPES[extname(f)] ?? 'application/octet-stream', 'cache-control': 'no-cache' });
});

const server = createServer(async (req, res) => {
  const url = new URL(req.url ?? '/', ORIGIN);
  const method = req.method ?? 'GET';
  try {
    for (const [m, re, h] of routes) {
      if (m !== method) continue;
      const match = re.exec(url.pathname);
      if (match) { await h({ req, res, params: match.slice(1), url, method }); return; }
    }
    if (url.pathname.startsWith('/api/') || url.pathname.startsWith('/auth/')) throw new HttpError(404, 'NOT_FOUND', `No route for ${method} ${url.pathname}`);
    if (method === 'GET') return spa(res);
    throw new HttpError(405, 'METHOD_NOT_ALLOWED', 'Method not allowed');
  } catch (e) {
    if (e instanceof HttpError) return sendError(res, e);
    console.error(e);
    sendError(res, new HttpError(500, 'INTERNAL', 'Internal error'));
  }
});

server.on('upgrade', (req, socket) => {
  const url = new URL(req.url ?? '/', ORIGIN);
  const s = sessionFrom(req);
  if (url.pathname !== '/ws' || !s?.mfa) { socket.end('HTTP/1.1 401 Unauthorized\r\n\r\n'); return; }
  const conn = acceptWebSocket(req, socket);
  if (!conn) return;
  conn.onMessage((text) => {
    let m: Record<string, unknown>;
    try { m = JSON.parse(text) as Record<string, unknown>; } catch { return conn.send(JSON.stringify({ type: 'error', error: 'bad json' })); }
    if (m.type === 'ping') return conn.send(JSON.stringify({ type: 'pong', at: now() }));
    if (m.type === 'quickpay') {
      if (m.csrf !== s.csrf) return conn.send(JSON.stringify({ type: 'error', error: 'csrf' }));
      try {
        const r = createPayment({ user: user(s.user)!, via: 'session', session: s }, m as PaymentInput, null, 'websocket');
        conn.send(JSON.stringify({ type: 'paid', payment: paymentOut(r.payment) }));
      } catch (e) { conn.send(JSON.stringify({ type: 'error', error: (e as Error).message })); }
    }
  });
});

// ---------------------------------------------------------------- card widget: a DIFFERENT ORIGIN (port)
const widgetTokens = new Map<string, { last4: string; brand: string }>();
const luhn = (n: string) => n.split('').reverse().map(Number).reduce((s, d, i) => s + (i % 2 ? (d * 2 > 9 ? d * 2 - 9 : d * 2) : d), 0) % 10 === 0;
const widget = createServer(async (req, res) => {
  const url = new URL(req.url ?? '/', WIDGET_ORIGIN);
  try {
    if (req.method === 'GET' && url.pathname === '/widget/card-form') {
      return send(res, 200, readFileSync(join(here, 'widget', 'card-form.html'), 'utf8').replace(/__PARENT_ORIGIN__/g, ORIGIN), {
        'content-security-policy': `frame-ancestors ${ORIGIN}`,
      });
    }
    if (req.method === 'POST' && url.pathname === '/widget/api/tokenize') {
      const b = await readJson(req);
      const num = typeof b.number === 'string' ? b.number.replace(/\s/g, '') : '';
      const errors: { field: string; message: string }[] = [];
      if (!/^\d{16}$/.test(num) || !luhn(num)) errors.push({ field: 'number', message: 'Card number is invalid' });
      if (typeof b.expiry !== 'string' || !/^(0[1-9]|1[0-2])\/\d{2}$/.test(b.expiry)) errors.push({ field: 'expiry', message: 'Expiry must be MM/YY' });
      if (typeof b.cvc !== 'string' || !/^\d{3}$/.test(b.cvc)) errors.push({ field: 'cvc', message: 'CVC must be 3 digits' });
      if (errors.length) throw new HttpError(400, 'VALIDATION_ERROR', 'Card is invalid', errors);
      await sleep(400);
      const tok = `tok_${token(12)}`;
      widgetTokens.set(tok, { last4: num.slice(-4), brand: num.startsWith('4') ? 'Visa' : 'Mastercard' });
      return send(res, 200, { token: tok, last4: num.slice(-4), brand: num.startsWith('4') ? 'Visa' : 'Mastercard' });
    }
    throw new HttpError(404, 'NOT_FOUND', 'Not found');
  } catch (e) {
    sendError(res, e instanceof HttpError ? e : new HttpError(500, 'INTERNAL', 'Internal error'));
  }
});

server.listen(PORT, HOST, () => console.log(`northwind bank on ${ORIGIN} (env=${ENV}); card widget on ${WIDGET_ORIGIN}`));
widget.listen(WIDGET_PORT, HOST);
export { randomUUID };

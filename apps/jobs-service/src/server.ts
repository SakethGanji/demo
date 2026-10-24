// Jobs service: heavy work beside Command Studio (browser + API test runs, AI triage, exploration).
//   JOBS_SERVICE_TOKEN=... node src/server.ts        (or JOBS_DEV=1 for the dev token)
// Contract: scratchpad/qa-build/CONTRACT.md section 1. Every route except /health needs Authorization: Bearer <token>.
import { createServer, type IncomingMessage, type ServerResponse } from 'node:http';
import { createReadStream, existsSync, statSync } from 'node:fs';
import { join, extname } from 'node:path';
import { timingSafeEqual } from 'node:crypto';
import {
  artifactsDir, createJob, emit, isFinal, jobDir, jobs, loadFromDisk, persist, publicView, type Job,
} from './store.ts';
import { BadRequest, buildTarget, normalizeCases } from './spec.ts';
import { runTestRun } from './testRun.ts';
import { triage } from './triage.ts';
import { applyControl, CONTROL_ACTIONS, ControlError, InputError, relayInput, type ControlAction, type RelayedInput } from './control.ts';
import { runExplore } from './exploreJob.ts';
import { closeBrowser } from './uiCase.ts';

const HOST = process.env.JOBS_HOST ?? '127.0.0.1';
const PORT = Number(process.env.JOBS_PORT ?? 8095);
const DEV_TOKEN = 'dev-jobs-token';
const TOKEN = process.env.JOBS_SERVICE_TOKEN || (process.env.JOBS_DEV === '1' ? DEV_TOKEN : '');
if (!TOKEN) {
  console.error('JOBS_SERVICE_TOKEN is not set (set JOBS_DEV=1 to use the dev token).');
  process.exit(2);
}
const MAX_RUNNING = Math.max(1, Number(process.env.JOBS_MAX_RUNNING ?? 2));
const MAX_CASE_CONCURRENCY = 4;

// ---------------------------------------------------------------- queue
type Runner = (job: Job, signal: AbortSignal) => Promise<void>;
const queue: { job: Job; run: Runner; ctl: AbortController }[] = [];
const controllers = new Map<string, AbortController>();
let running = 0;

function enqueue(job: Job, run: Runner): void {
  const ctl = new AbortController();
  controllers.set(job.job_id, ctl);
  queue.push({ job, run, ctl });
  setImmediate(pump); // respond "queued" first; the job starts on the next tick
}

function pump(): void {
  while (running < MAX_RUNNING && queue.length) {
    const { job, run, ctl } = queue.shift()!;
    if (ctl.signal.aborted) continue;
    running++;
    job.status = 'running';
    job.started_at = new Date().toISOString();
    persist(job);
    run(job, ctl.signal)
      .then(() => {
        if (ctl.signal.aborted) job.status = 'cancelled';
        else if (job.type === 'test-run') job.status = job.results.every((r) => r.status === 'passed') ? 'passed' : 'failed';
        else job.status = 'passed';
      })
      .catch((e: Error) => {
        job.status = ctl.signal.aborted ? 'cancelled' : 'error';
        job.error = String(e.message).slice(0, 800);
        emit(job, 'log', null, `job ${job.status}: ${job.error}`);
      })
      .finally(() => {
        job.finished_at = new Date().toISOString();
        emit(job, 'log', null, `job finished: ${job.status}`);
        persist(job);
        controllers.delete(job.job_id);
        running--;
        pump();
      });
  }
}

// ---------------------------------------------------------------- job types
function credsOf(body: Record<string, unknown>): Record<string, string> {
  const c = body.credentials;
  if (c === undefined) return {};
  if (!c || typeof c !== 'object' || Array.isArray(c)) throw new BadRequest('credentials must be an object of strings');
  const out: Record<string, string> = {};
  for (const [k, v] of Object.entries(c)) {
    if (typeof v !== 'string') throw new BadRequest(`credentials.${k} must be a string`);
    if (!/^\w+$/.test(k)) throw new BadRequest(`credentials key "${k}" must be [A-Za-z0-9_]`);
    out[k] = v;
  }
  return out;
}

function startJob(body: Record<string, unknown>): Job {
  const type = body.type;
  if (type === 'test-run') {
    const cases = normalizeCases(body.cases);
    const creds = credsOf(body);
    const conc = Math.max(1, Math.min(MAX_CASE_CONCURRENCY, Number(body.concurrency ?? 1) || 1));
    const job = createJob('test-run', body, creds, typeof body.label === 'string' ? body.label : undefined);
    const target = buildTarget(job.job_id, jobDir(job.job_id), body.target, body.policy);
    job.progress.total = cases.length;
    job.results = cases.map((c) => ({ case_id: c.id, kind: c.kind, title: c.title, status: 'queued', duration_ms: 0, artifacts: [] }));
    persist(job);
    enqueue(job, (j, signal) => runTestRun({ job: j, target, cases, concurrency: conc, signal }));
    return job;
  }
  if (type === 'triage') {
    const srcId = body.job_id, caseId = body.case_id;
    if (typeof srcId !== 'string' || typeof caseId !== 'string') throw new BadRequest('triage needs job_id and case_id');
    const src = jobs.get(srcId);
    if (!src) throw new BadRequest(`unknown job_id ${srcId}`);
    const res = src.results.find((r) => r.case_id === caseId);
    if (!res) throw new BadRequest(`case ${caseId} is not in job ${srcId}`);
    const failure = src.failures[caseId];
    if (!failure) throw new BadRequest(`case ${caseId} has no failure evidence (status ${res.status})`);
    const job = createJob('triage', body, {}, `triage ${srcId}/${caseId}`);
    job.progress.total = 1;
    enqueue(job, async (j) => {
      j.creds = src.creds; // mask with the source run's credentials (memory only)
      emit(j, 'log', caseId, `triaging ${srcId}/${caseId}: ${failure.error}`);
      j.output = await triage(j, failure, src.job_id);
      emit(j, 'log', caseId, `verdict ${(j.output as { verdict: string }).verdict} (${(j.output as { source: string }).source})`);
      j.progress.done = 1;
    });
    return job;
  }
  if (type === 'explore') {
    if (typeof body.goal !== 'string' || !body.goal.trim()) throw new BadRequest('explore needs a goal');
    const creds = credsOf(body);
    const job = createJob('explore', body, creds, typeof body.label === 'string' ? body.label : `explore: ${body.goal.slice(0, 60)}`);
    const target = buildTarget(job.job_id, jobDir(job.job_id), body.target, body.policy);
    job.progress.total = 1;
    enqueue(job, async (j, signal) => { j.output = await runExplore(j, target, body.goal as string, signal); j.progress.done = 1; });
    return job;
  }
  throw new BadRequest('type must be "test-run", "triage" or "explore"');
}

// ---------------------------------------------------------------- http
function send(res: ServerResponse, status: number, body?: unknown, headers: Record<string, string> = {}): void {
  if (body === undefined) { res.writeHead(status, headers).end(); return; }
  const s = JSON.stringify(body);
  res.writeHead(status, { 'content-type': 'application/json', 'cache-control': 'no-store', ...headers }).end(s);
}

function authorized(req: IncomingMessage): boolean {
  const h = req.headers.authorization ?? '';
  const m = /^Bearer\s+(.+)$/i.exec(h);
  if (!m) return false;
  const a = Buffer.from(m[1]!.trim()), b = Buffer.from(TOKEN);
  return a.length === b.length && timingSafeEqual(a, b);
}

async function readBody(req: IncomingMessage): Promise<Record<string, unknown>> {
  const chunks: Buffer[] = [];
  let n = 0;
  for await (const c of req) { n += (c as Buffer).length; if (n > 5_000_000) throw new BadRequest('body too large'); chunks.push(c as Buffer); }
  if (!n) return {};
  try {
    const v = JSON.parse(Buffer.concat(chunks).toString('utf8')) as unknown;
    if (!v || typeof v !== 'object' || Array.isArray(v)) throw new Error();
    return v as Record<string, unknown>;
  } catch { throw new BadRequest('body must be a JSON object'); }
}

const TYPES: Record<string, string> = { '.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.webm': 'video/webm', '.zip': 'application/zip', '.json': 'application/json' };

async function handle(req: IncomingMessage, res: ServerResponse): Promise<void> {
  const url = new URL(req.url ?? '/', `http://${HOST}`);
  const method = req.method ?? 'GET';
  const parts = url.pathname.split('/').filter(Boolean).map(decodeURIComponent);

  if (method === 'GET' && url.pathname === '/health') return send(res, 200, { ok: true, service: 'jobs-service', running, queued: queue.length });
  if (!authorized(req)) return send(res, 401, { error: 'unauthorized' }, { 'www-authenticate': 'Bearer' });

  if (parts[0] !== 'jobs') return send(res, 404, { error: 'not found' });
  if (parts.length === 1) {
    if (method === 'POST') {
      const job = startJob(await readBody(req));
      return send(res, 201, { job_id: job.job_id, status: job.status });
    }
    if (method === 'GET') {
      const list = [...jobs.values()].sort((a, b) => b.created_at.localeCompare(a.created_at)).slice(0, 100)
        .map((j) => ({ job_id: j.job_id, type: j.type, label: j.label, status: j.status, created_at: j.created_at, finished_at: j.finished_at }));
      return send(res, 200, { jobs: list });
    }
    return send(res, 405, { error: 'method not allowed' });
  }
  const job = jobs.get(parts[1]!);
  if (!job) return send(res, 404, { error: 'job not found' });
  const sub = parts[2];

  if (!sub && method === 'GET') return send(res, 200, publicView(job));
  if (sub === 'events' && method === 'GET') {
    const after = Number(url.searchParams.get('after') ?? 0) || 0;
    const limit = Math.min(1000, Number(url.searchParams.get('limit') ?? 500) || 500);
    const events = job.events.filter((e) => e.seq > after).slice(0, limit);
    return send(res, 200, { events, next: events.at(-1)?.seq ?? after, status: job.status });
  }
  if (sub === 'frame' && method === 'GET') {
    if (!job.frame) return send(res, 204);
    return void res.writeHead(200, { 'content-type': 'image/jpeg', 'cache-control': 'no-store', 'x-frame-at': new Date(job.frameAt).toISOString(), 'content-length': String(job.frame.length) }).end(job.frame);
  }
  if (sub === 'artifacts' && method === 'GET') {
    const dir = artifactsDir(job.job_id);
    if (parts.length === 3) {
      const list = [...job.artifacts].sort().filter((n) => existsSync(join(dir, n))).map((name) => ({
        name, bytes: statSync(join(dir, name)).size, content_type: TYPES[extname(name)] ?? 'application/octet-stream',
        case_id: job.results.find((r) => r.artifacts.includes(name))?.case_id ?? null,
      }));
      return send(res, 200, { artifacts: list });
    }
    const name = parts[3]!;
    if (parts.length !== 4 || !/^[\w.-]+$/.test(name) || !job.artifacts.has(name) || !existsSync(join(dir, name))) return send(res, 404, { error: 'artifact not found' });
    const p = join(dir, name);
    res.writeHead(200, { 'content-type': TYPES[extname(name)] ?? 'application/octet-stream', 'content-length': String(statSync(p).size), 'content-disposition': `inline; filename="${name}"` });
    createReadStream(p).pipe(res);
    return;
  }
  if (sub === 'cancel' && method === 'POST') {
    if (!isFinal(job.status)) {
      job.cancelRequested = true;
      const ctl = controllers.get(job.job_id);
      ctl?.abort();
      emit(job, 'log', null, 'cancel requested');
      if (job.status === 'queued') {
        job.status = 'cancelled';
        job.finished_at = new Date().toISOString();
        for (const r of job.results) if (r.status === 'queued') r.status = 'cancelled';
        persist(job);
      }
    }
    return send(res, 202, { job_id: job.job_id, status: job.status, cancel_requested: true });
  }
  if (sub === 'control' && method === 'POST') {
    const action = (await readBody(req)).action;
    if (typeof action !== 'string' || !CONTROL_ACTIONS.includes(action as ControlAction)) throw new BadRequest(`action must be one of ${CONTROL_ACTIONS.join(', ')}`);
    try {
      return send(res, 200, { job_id: job.job_id, status: job.status, action, ...applyControl(job, action as ControlAction) });
    } catch (e) {
      if (e instanceof ControlError) return send(res, e.status, { error: e.message, status: job.status });
      throw e;
    }
  }
  if (sub === 'input' && method === 'POST') {
    // take over: one click / type / key / scroll on the live page (POST /jobs/{id}/control {action:'takeover'} first)
    try {
      return send(res, 200, { job_id: job.job_id, ...(await relayInput(job, (await readBody(req)) as unknown as RelayedInput)) });
    } catch (e) {
      if (e instanceof ControlError || e instanceof InputError) return send(res, e.status, { error: e.message, status: job.status });
      throw e;
    }
  }
  return send(res, 404, { error: 'not found' });
}

loadFromDisk();
const server = createServer((req, res) => {
  handle(req, res).catch((e: Error) => {
    if (e instanceof BadRequest) return send(res, 400, { error: e.message });
    console.error('[jobs] internal error:', e.message);
    if (!res.headersSent) send(res, 500, { error: 'internal error' });
  });
});
server.listen(PORT, HOST, () => console.log(`jobs-service on http://${HOST}:${PORT} (max ${MAX_RUNNING} running jobs${process.env.JOBS_DEV === '1' && !process.env.JOBS_SERVICE_TOKEN ? ', DEV token' : ''})`));

const shutdown = async () => {
  for (const c of controllers.values()) c.abort();
  server.close();
  await closeBrowser();
  process.exit(0);
};
process.on('SIGINT', shutdown);
process.on('SIGTERM', shutdown);

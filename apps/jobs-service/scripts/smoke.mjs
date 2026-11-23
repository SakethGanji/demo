// End-to-end smoke test of the jobs service against the bank demo app, through the HTTP API only.
//   node scripts/smoke.mjs     (env: JOBS_URL=http://127.0.0.1:8095 JOBS_TOKEN=dev-jobs-token BANK_URL=http://127.0.0.1:4590)
// Bank credentials are the test-only seed users, read from apps/test-builder/bank-app/lib/db.ts (never printed).
import { readFileSync } from 'node:fs';
import { createHash } from 'node:crypto';
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';
import { join, dirname } from 'node:path';

const here = dirname(fileURLToPath(import.meta.url));
const tb = join(here, '..', '..', 'test-builder');
const JSZip = createRequire(join(tb, 'package.json'))('jszip');
const JOBS = process.env.JOBS_URL ?? 'http://127.0.0.1:8095';
const TOKEN = process.env.JOBS_TOKEN ?? 'dev-jobs-token';
const BANK = process.env.BANK_URL ?? 'http://127.0.0.1:4590';
const bankHost = new URL(BANK).host;
const widgetHost = `${new URL(BANK).hostname}:${Number(new URL(BANK).port) + 1}`;

const seed = readFileSync(join(tb, 'bank-app', 'lib', 'db.ts'), 'utf8');
function seedUser(u) {
  const m = new RegExp(`username: '${u}', password: '([^']+)'.*?totp: '([A-Z2-7]+)'`).exec(seed);
  if (!m) throw new Error(`seed user ${u} not found`);
  return { password: m[1], totp: m[2] };
}
const t1 = seedUser('teller1'), t2 = seedUser('teller2');
const credentials = { username: 'teller1', password: t1.password, totp: t1.totp, username2: 'teller2', password2: t2.password, totp2: t2.totp };

let failures = 0;
const ok = (cond, msg) => { console.log(`${cond ? 'PASS' : 'FAIL'}  ${msg}`); if (!cond) failures++; };
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const H = { authorization: `Bearer ${TOKEN}`, 'content-type': 'application/json' };
async function api(method, path, body, headers = H) {
  const r = await fetch(JOBS + path, { method, headers, body: body ? JSON.stringify(body) : undefined });
  const ct = r.headers.get('content-type') ?? '';
  return { status: r.status, ct, body: ct.includes('json') ? await r.json() : Buffer.from(await r.arrayBuffer()) };
}

const login = (u = 'username', p = 'password', t = 'totp') => [
  { action: 'fill', target: { label: 'Username' }, value: `{{${u}}}`, group: 'Sign in' },
  { action: 'fill', target: { label: 'Password' }, value: `{{${p}}}`, group: 'Sign in' },
  { action: 'click', target: { role: 'button', name: 'Sign in' }, group: 'Sign in' },
  { action: 'otp', value: `{{totp:${t}}}`, group: 'MFA' },
];
const verify = { action: 'click', target: { role: 'button', name: 'Verify' }, group: 'MFA' };
const dashboard = { action: 'wait_for', target: { role: 'table', name: 'Customer accounts' }, group: 'Dashboard' };
const token = { name: 'get token', method: 'POST', path: '/api/v1/auth/token', body: { username: '{{username}}', password: '{{password}}', otp: '{{totp:totp}}' }, expect: { status: 200 }, save: { access_token: '$.access_token' } };
const bearer = { authorization: 'Bearer {{access_token}}' };

const run = {
  type: 'test-run',
  label: 'Smoke: bank release',
  target: { base_url: BANK, allowed_hosts: [bankHost, widgetHost] },
  policy: {
    forbidden_names: ['transfer', 'pay', 'approve', 'delete', 'close account'],
    forbidden_requests: ['POST /api/v1/payments', 'POST /api/v1/payments/*/approve', 'DELETE /api/v1/*', 'POST /api/v1/batch'],
  },
  credentials,
  concurrency: 2,
  cases: [
    { id: 'TC-101', kind: 'ui', title: 'Teller logs in and sees accounts', steps: [...login(), verify, dashboard,
      { action: 'expect_text', target: { role: 'cell', name: 'Everyday Checking' }, text: 'Everyday Checking' }] },
    { id: 'TC-102', kind: 'ui', title: 'MFA (button renamed in app)', steps: [...login(),
      { action: 'click', target: { role: 'button', name: 'Verify code' }, group: 'MFA' }, dashboard] },
    { id: 'TC-103', kind: 'ui', title: 'Quick pay must be blocked', steps: [...login('username2', 'password2', 'totp2'), verify, dashboard,
      { action: 'goto', url: '/payments/new' },
      { action: 'wait_for', target: { role: 'button', name: 'Pay now' } },
      { action: 'click', target: { role: 'button', name: 'Pay now' } },
      { action: 'expect_text', target: { role: 'status' }, text: 'never reached' }] },
    { id: 'TC-104', kind: 'ui', title: 'Open an account\'s transactions', steps: [...login(), verify, dashboard,
      { action: 'click', target: { role: 'link', name: 'Everyday Checking' } },
      { action: 'wait_for', target: { role: 'table', name: 'Transactions' } },
      { action: 'expect_url', url: '/accounts/' }] },
    { id: 'API-201', kind: 'api', title: 'Accounts list contract', requests: [token,
      { method: 'GET', path: '/api/v1/accounts', headers: bearer, expect: { status: 200, schema: { type: 'array', minItems: 1, items: { type: 'object', required: ['id', 'iban', 'currency'], properties: { id: { type: 'string' } } } } } }] },
    { id: 'API-202', kind: 'api', title: 'Create payee then read it back', requests: [token,
      { method: 'POST', path: '/api/v1/payees', headers: bearer, body: { name: 'Smoke Payee Ltd', iban: 'GB82WEST12345698765432', bank: 'West' }, expect: { status: 201, json_path_equals: { '$.name': 'Smoke Payee Ltd' } }, save: { payee_id: '$.id' } },
      { method: 'GET', path: '/api/v1/payees/{{payee_id}}', headers: bearer, expect: { status: 200, json_path_equals: { '$.id': '{{payee_id}}', '$.name': 'Smoke Payee Ltd' } } }] },
    { id: 'API-203', kind: 'api', title: 'No token -> 401', requests: [
      { method: 'GET', path: '/api/v1/me', expect: { status: 401, json_path_equals: { '$.error.code': 'UNAUTHENTICATED' } } }] },
    { id: 'API-204', kind: 'api', title: 'Payment POST must be blocked', requests: [token,
      { method: 'POST', path: '/api/v1/payments', headers: bearer, body: { from_account: 'ACC-1001', payee_id: 'PAY-001', amount: 1 }, expect: { status: 201 } }] },
  ],
};

async function waitFinal(id, onTick) {
  for (let i = 0; i < 600; i++) {
    const s = await api('GET', `/jobs/${id}`);
    if (onTick) await onTick(s.body);
    if (!['queued', 'running'].includes(s.body.status)) return s.body;
    await sleep(300);
  }
  throw new Error(`job ${id} did not finish`);
}

// ---------------------------------------------------------------- 1. auth
ok((await fetch(`${JOBS}/health`)).status === 200, '/health is 200 without a token');
ok((await api('GET', '/jobs', undefined, {})).status === 401, 'GET /jobs without token -> 401');
ok((await api('POST', '/jobs', run, { 'content-type': 'application/json' })).status === 401, 'POST /jobs without token -> 401');
ok((await api('GET', '/jobs', undefined, { authorization: 'Bearer wrong' })).status === 401, 'wrong token -> 401');
ok((await api('POST', '/jobs', { type: 'test-run', target: { base_url: BANK }, cases: [{ id: 'x', kind: 'ui', steps: [{ action: 'fly' }] }] })).status === 400, 'invalid step -> 400');

// ---------------------------------------------------------------- 2. the run
const t0 = Date.now();
const created = await api('POST', '/jobs', run);
ok(created.status === 201 && created.body.status === 'queued' && Date.now() - t0 < 1000, `POST /jobs -> 201 queued in ${Date.now() - t0} ms (${created.body.job_id})`);
const id = created.body.job_id;

const frames = new Set();
let frameBytes = 0, after = 0;
const events = [];
const final = await waitFinal(id, async () => {
  const f = await api('GET', `/jobs/${id}/frame`);
  if (f.status === 200 && f.ct === 'image/jpeg' && f.body.length > 1024 && f.body[0] === 0xff && f.body[1] === 0xd8) {
    frames.add(createHash('sha256').update(f.body).digest('hex'));
    frameBytes = Math.max(frameBytes, f.body.length);
  }
  const e = await api('GET', `/jobs/${id}/events?after=${after}`);
  events.push(...e.body.events);
  after = e.body.next;
});
const e = await api('GET', `/jobs/${id}/events?after=${after}`);
events.push(...e.body.events);

console.log(`\njob ${id} -> ${final.status} in ${((Date.now() - t0) / 1000).toFixed(1)} s; ${events.length} events; ${frames.size} distinct frames`);
for (const r of final.results) console.log(`  ${r.case_id.padEnd(8)} ${r.kind.padEnd(3)} ${r.status.padEnd(8)} ${String(r.duration_ms).padStart(6)} ms  ${r.error ? r.error.slice(0, 110) : ''}`);
console.log('');
const res = Object.fromEntries(final.results.map((r) => [r.case_id, r]));
ok(final.status === 'failed', 'job status is failed (one failure + blocks)');
ok(res['TC-101']?.status === 'passed', 'TC-101 passes');
ok(res['TC-102']?.status === 'failed' && res['TC-102'].failed_step?.index === 4, 'TC-102 fails at step 4 (renamed button)');
ok(res['TC-103']?.status === 'blocked', 'TC-103 forbidden click is blocked');
ok(events.some((ev) => ev.case_id === 'TC-103' && ev.type === 'blocked' && /Pay now/.test(ev.text)), 'TC-103 block is logged as a "blocked" event');
ok(res['TC-104']?.status === 'passed', 'TC-104 passes (expect_url)');
ok(res['API-201']?.status === 'passed', 'API-201 passes (schema)');
ok(res['API-202']?.status === 'passed', 'API-202 chained create -> get passes');
ok(res['API-203']?.status === 'passed', 'API-203 401 negative passes');
ok(res['API-204']?.status === 'blocked' && events.some((ev) => ev.case_id === 'API-204' && ev.type === 'blocked'), 'API-204 forbidden request blocked + logged');
const types = new Set(events.map((ev) => ev.type));
ok(['case_start', 'case_end', 'step_start', 'step_pass', 'step_fail', 'request', 'blocked', 'log'].every((t) => types.has(t)), `events cover every type (${[...types].join(',')})`);
ok(events.every((ev, i) => i === 0 || ev.seq > events[i - 1].seq), 'event seq strictly increasing across polls');
ok(frames.size >= 3 && frameBytes > 1024, `live frame: ${frames.size} distinct JPEG frames (largest ${frameBytes} bytes)`);

// ---------------------------------------------------------------- 3. artifacts + secret scan
const list = (await api('GET', `/jobs/${id}/artifacts`)).body.artifacts;
const names = list.map((a) => a.name);
console.log(`artifacts (${names.length}): ${names.join(', ')}`);
ok(names.some((n) => n.endsWith('.video.webm')), 'a video exists');
ok(names.some((n) => n.endsWith('.trace.zip')), 'a trace exists');
ok(names.includes('evidence.json') && names.includes('TC-102.failure.png'), 'evidence.json and failure screenshot exist');
const evidence = (await api('GET', `/jobs/${id}/artifacts/evidence.json`)).body;
const secrets = [credentials.password, credentials.password2, credentials.totp, credentials.totp2];
const needles = secrets.flatMap((s) => [s, encodeURIComponent(s), JSON.stringify(s).slice(1, -1), Buffer.from(s).toString('base64')]).map((s) => Buffer.from(s));
let leaks = 0, scanned = 0, zipEntries = 0, hashOk = 0;
for (const n of names) {
  const buf = Buffer.from(await (await fetch(`${JOBS}/jobs/${id}/artifacts/${encodeURIComponent(n)}`, { headers: H })).arrayBuffer());
  scanned++;
  leaks += needles.filter((nd) => buf.includes(nd)).length;
  const ev = evidence.artifacts.find((a) => a.name === n);
  if (ev && ev.sha256 === createHash('sha256').update(buf).digest('hex') && ev.bytes === buf.length) hashOk++;
  if (n.endsWith('.zip')) {
    const zip = await JSZip.loadAsync(buf);
    for (const entry of Object.values(zip.files)) {
      if (entry.dir) continue;
      const eb = await entry.async('nodebuffer');
      zipEntries++;
      leaks += needles.filter((nd) => eb.includes(nd)).length;
    }
  }
}
ok(leaks === 0, `no password/TOTP secret in any artifact (scanned ${scanned} files + ${zipEntries} zip entries, 0 hits)`);
ok(evidence.artifacts.length === names.length - 1 && hashOk === evidence.artifacts.length, `evidence.json sha256+size matches ${hashOk}/${evidence.artifacts.length} downloaded artifacts`);
const eventText = JSON.stringify(events);
ok(!secrets.some((s) => eventText.includes(s)), 'no secret in the event stream');

// ---------------------------------------------------------------- 4. triage
const tri = await api('POST', '/jobs', { type: 'triage', job_id: id, case_id: 'TC-102' });
ok(tri.status === 201, 'triage job created');
const tf = await waitFinal(tri.body.job_id);
const v = tf.output ?? {};
console.log(`triage: verdict=${v.verdict} confidence=${v.confidence} source=${v.source}\n  summary: ${v.summary}`);
const fixed = v.proposed_fix?.steps_after?.[4];
console.log(`  proposed fix step 4: ${JSON.stringify(v.proposed_fix?.steps_before?.[4]?.target)} -> ${JSON.stringify(fixed?.target)}`);
ok(tf.status === 'passed' && v.verdict === 'app_changed', 'triage verdict is app_changed');
ok(!!v.proposed_fix && fixed?.target?.name === 'Verify', 'proposed_fix renames the step target to "Verify"');

// ---------------------------------------------------------------- 5. cancel + control
const slow = await api('POST', '/jobs', { ...run, label: 'to be cancelled', concurrency: 1, cases: [run.cases[0], run.cases[3], { ...run.cases[0], id: 'TC-901' }] });
let sawRunning = false;
for (let i = 0; i < 60 && !sawRunning; i++) { sawRunning = (await api('GET', `/jobs/${slow.body.job_id}`)).body.status === 'running'; if (!sawRunning) await sleep(200); }
await sleep(1500);
const c = await api('POST', `/jobs/${slow.body.job_id}/cancel`);
ok(c.status === 202, 'POST /cancel -> 202');
const cf = await waitFinal(slow.body.job_id);
ok(cf.status === 'cancelled' && cf.results.every((r) => r.status === 'cancelled'), `cancelled job: status ${cf.status}, cases ${cf.results.map((r) => r.status).join('/')}`);
const ctl = await api('POST', `/jobs/${id}/control`);
ok(ctl.status === 501 && ctl.body.reserved === 'takeover', 'POST /control -> 501 {reserved:"takeover"}');

console.log(`\n${failures ? `SMOKE FAILED (${failures})` : 'SMOKE PASSED'}  run=${id} triage=${tri.body.job_id} cancelled=${slow.body.job_id}`);
process.exit(failures ? 1 : 0);

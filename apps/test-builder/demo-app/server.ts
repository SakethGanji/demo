// Demo target app: a tiny "online banking" site + JSON API, plain node:http, no deps.
//   node demo-app/server.ts            (PORT defaults to 4477)
// Credentials: DEMO_USER / DEMO_PASSWORD env (defaults below match targets/demo.json docs).
import { createServer, type IncomingMessage, type ServerResponse } from 'node:http';
import { readFileSync } from 'node:fs';
import { randomUUID } from 'node:crypto';
import { join, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';

const here = dirname(fileURLToPath(import.meta.url));
const PORT = Number(process.env.PORT ?? 4477);
const USER = process.env.DEMO_USER ?? 'teller01';
const PASSWORD = process.env.DEMO_PASSWORD ?? 'Sup3r-Secret-Pa55!';

interface Account { id: string; name: string; number: string; balance: number }
interface Payee { id: string; name: string; accountNumber: string; type: 'personal' | 'business' }

const accounts: Account[] = [
  { id: 'ACC-1', name: 'Everyday Checking', number: '10002001', balance: 2450.12 },
  { id: 'ACC-2', name: 'High Yield Savings', number: '10002002', balance: 18300.0 },
  { id: 'ACC-3', name: 'Business Operating', number: '10002003', balance: 97120.55 },
];
const payees: Payee[] = [{ id: 'P-1', name: 'City Water', accountNumber: '55501234', type: 'business' }];
const sessions = new Set<string>();
const audit: { at: string; method: string; path: string }[] = [];

function send(res: ServerResponse, status: number, body: unknown, headers: Record<string, string> = {}): void {
  const isStr = typeof body === 'string';
  res.writeHead(status, {
    'content-type': isStr ? 'text/html; charset=utf-8' : 'application/json',
    'cache-control': 'no-store',
    ...headers,
  });
  res.end(isStr ? body : JSON.stringify(body));
}

async function readJson(req: IncomingMessage): Promise<unknown> {
  const chunks: Buffer[] = [];
  for await (const c of req) chunks.push(c as Buffer);
  const raw = Buffer.concat(chunks).toString('utf8');
  if (!raw) return {};
  try { return JSON.parse(raw); } catch { return undefined; }
}

function sessionOf(req: IncomingMessage): string | undefined {
  const m = /(?:^|;\s*)tb_session=([^;]+)/.exec(req.headers.cookie ?? '');
  return m && sessions.has(m[1]!) ? m[1] : undefined;
}

function validatePayee(b: unknown): { errors?: Record<string, string>; payee?: Omit<Payee, 'id'> } {
  const errors: Record<string, string> = {};
  const o = (b && typeof b === 'object' ? b : {}) as Record<string, unknown>;
  const name = typeof o.name === 'string' ? o.name.trim() : '';
  const acct = typeof o.accountNumber === 'string' ? o.accountNumber.trim() : '';
  const type = o.type;
  if (!name) errors.name = 'Payee name is required';
  else if (name.length > 60) errors.name = 'Payee name must be 60 characters or fewer';
  if (!/^\d{8}$/.test(acct)) errors.accountNumber = 'Account number must be exactly 8 digits';
  if (type !== 'personal' && type !== 'business') errors.type = 'Payee type must be personal or business';
  if (Object.keys(errors).length) return { errors };
  return { payee: { name, accountNumber: acct, type: type as Payee['type'] } };
}

const page = (file: string) => readFileSync(join(here, 'public', file), 'utf8');

const server = createServer(async (req, res) => {
  const url = new URL(req.url ?? '/', `http://${req.headers.host}`);
  const { pathname } = url;
  const method = req.method ?? 'GET';
  if (pathname.startsWith('/api/')) audit.push({ at: new Date().toISOString(), method, path: pathname });

  // ---- pages
  if (method === 'GET' && (pathname === '/' || pathname === '/login')) return send(res, 200, page('login.html'));
  if (method === 'GET' && pathname === '/dashboard') {
    if (!sessionOf(req)) return send(res, 302, '', { location: '/' });
    return send(res, 200, page('dashboard.html'));
  }
  if (method === 'GET' && pathname === '/app.js') {
    res.writeHead(200, { 'content-type': 'text/javascript' });
    return res.end(page('app.js'));
  }
  if (method === 'GET' && pathname === '/openapi.json') {
    res.writeHead(200, { 'content-type': 'application/json' });
    return res.end(readFileSync(join(here, 'openapi.json')));
  }

  // ---- API
  if (method === 'POST' && pathname === '/api/login') {
    const b = (await readJson(req)) as Record<string, unknown> | undefined;
    if (b?.username === USER && b?.password === PASSWORD) {
      const sid = randomUUID();
      sessions.add(sid);
      return send(res, 200, { ok: true }, { 'set-cookie': `tb_session=${sid}; Path=/; HttpOnly; SameSite=Lax` });
    }
    return send(res, 401, { error: 'Invalid username or password' });
  }
  if (method === 'POST' && pathname === '/api/logout') return send(res, 200, { ok: true });
  if (method === 'GET' && pathname === '/api/accounts') {
    const q = (url.searchParams.get('q') ?? '').toLowerCase();
    return send(res, 200, accounts.filter((a) => !q || a.name.toLowerCase().includes(q) || a.number.includes(q)));
  }
  if (method === 'GET' && pathname === '/api/payees') return send(res, 200, payees);
  if (method === 'POST' && pathname === '/api/payees') {
    const b = await readJson(req);
    if (b === undefined) return send(res, 400, { error: 'Body must be JSON', errors: {} });
    const v = validatePayee(b);
    if (v.errors) return send(res, 400, { error: 'Validation failed', errors: v.errors });
    const p: Payee = { id: `P-${payees.length + 1}`, ...v.payee! };
    payees.push(p);
    return send(res, 201, p);
  }
  // Dangerous endpoints: they "work", so a test that reaches them would do real damage.
  if (method === 'POST' && pathname.startsWith('/api/transfer')) return send(res, 200, { ok: true, transferred: true });
  if (method === 'DELETE' && pathname.startsWith('/api/accounts/')) return send(res, 200, { ok: true, deleted: pathname.split('/').pop() });
  if (method === 'GET' && pathname === '/api/_audit') return send(res, 200, audit);

  send(res, 404, { error: 'Not found' });
});

server.listen(PORT, '127.0.0.1', () => {
  console.log(`demo-app listening on http://127.0.0.1:${PORT} (pid ${process.pid})`);
});

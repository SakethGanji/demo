"""Opt-in, billable: the 'Marketplace Trust & Payments' scenario (INTEGRATION-TEST-PLAN section 2 / 3B).

Isolated stack (own database, free ports, allow-listed environment) + real Gemini + a local upstream
HTTP server (balance / risk APIs). The browser driver is marketplace.cjs. The Gemini key is loaded from the
process environment only (never written anywhere); every Gemini call made by the engine is counted by a
sitecustomize hook that logs only metadata (model, status, token counts), never prompts or responses.
"""
import json, os, pathlib, secrets, socket, subprocess, tempfile, threading, time, signal, sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import httpx

ROOT = pathlib.Path(__file__).resolve().parents[4]
ENGINE = ROOT / 'apps/workflow-engine'
STUDIO = ROOT / 'apps/workflow-studio'
PY = sys.executable
assert os.environ.get('GEMINI_API_KEY'), 'Load the key as an environment variable first (see README)'
model = os.environ.get('WORKFLOW_MODULE_GEN_MODEL', 'gemini-3.5-flash')
db = 'workflows_test_e2e_' + secrets.token_hex(4)
tmp = pathlib.Path(tempfile.mkdtemp(prefix='marketplace-live-'))
PASSTHROUGH = ('PATH', 'HOME', 'LANG', 'HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY', 'NO_PROXY', 'http_proxy', 'https_proxy',
               'all_proxy', 'no_proxy', 'SSL_CERT_FILE', 'SSL_CERT_DIR', 'REQUESTS_CA_BUNDLE', 'CURL_CA_BUNDLE',
               'CHROMIUM_PATH', 'MARKETPLACE_ONLY', 'MARKETPLACE_SKIP', 'MARKETPLACE_MAX_LLM', 'MARKETPLACE_SERVE', 'MARKETPLACE_REUSE', 'MARKETPLACE_OUT', 'MARKETPLACE_DISPUTES_SOURCE')
base = {k: v for k, v in os.environ.items() if k in PASSTHROUGH}
base['PATH'] = str(STUDIO / 'node_modules/.bin') + ':' + base.get('PATH', '/usr/bin:/bin')
subprocess.run([PY, 'tests/e2e_stack.py', 'up'], cwd=ENGINE, env={**base, 'TEST_DB_NAME': db}, capture_output=True, text=True, check=True)


def port():
    with socket.socket() as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]


ep, vp, up = port(), port(), port()
assert ep != 8000 and vp != 5173

# Synthetic secret values: generated per run, kept only in a 0600 file for the assertions.
secret_values = {
    'PAYMENTS_TOKEN': 'pay_' + secrets.token_hex(12),
    'STRIPE_WEBHOOK_SECRET': 'whsec_' + secrets.token_hex(12),
    'RISK_API_KEY': 'risk_' + secrets.token_hex(12),
}
cred = tmp / 'credentials.json'
cred.write_text(json.dumps({'secrets': secret_values}))
cred.chmod(0o600)

# --- upstream: balance + risk APIs; logs requests (headers carry the real secrets) -----------------------
upstream_log = []


class Upstream(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, obj):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header('content-type', 'application/json')
        self.send_header('content-length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = self.path.split('?')[0]
        if path == '/__log':
            return self._send(200, upstream_log)
        upstream_log.append({'path': self.path, 'host': self.headers.get('host'),
                             'auth_ok': self.headers.get('authorization') == 'Bearer ' + secret_values['PAYMENTS_TOKEN'],
                             'risk_key_ok': self.headers.get('x-api-key') == secret_values['RISK_API_KEY']})
        if path == '/v1/balance':
            if self.headers.get('authorization') != 'Bearer ' + secret_values['PAYMENTS_TOKEN']:
                return self._send(401, {'error': 'bad token'})
            return self._send(200, {'available': 48250.5, 'currency': 'USD'})
        if path == '/v1/risk':
            if self.headers.get('x-api-key') != secret_values['RISK_API_KEY']:
                return self._send(401, {'error': 'bad key'})
            q = self.path.split('customer=')[-1]
            return self._send(200, {'customer': q, 'score': (sum(map(ord, q)) * 7) % 100})
        return self._send(404, {'error': 'unknown'})


httpd = ThreadingHTTPServer(('127.0.0.1', up), Upstream)
threading.Thread(target=httpd.serve_forever, daemon=True).start()

# --- count Gemini calls inside the engine (metadata only) ----------------------------------------------
hook = tmp / 'hook'
hook.mkdir()
llm_log = tmp / 'llm-calls.jsonl'
(hook / 'sitecustomize.py').write_text('''
import json, os, time
_LOG = os.environ.get("MARKETPLACE_LLM_LOG")
if _LOG:
    try:
        from google.genai import models as _m
        _orig = _m.AsyncModels.generate_content
        async def _wrapped(self, *a, **kw):
            t0 = time.time(); rec = {"t": t0, "model": kw.get("model")}
            try:
                r = await _orig(self, *a, **kw)
                u = getattr(r, "usage_metadata", None)
                rec.update(ok=True, in_tok=getattr(u, "prompt_token_count", 0) or 0, out_tok=getattr(u, "candidates_token_count", 0) or 0)
                return r
            except BaseException as e:
                rec.update(ok=False, err=type(e).__name__, code=getattr(e, "code", None), status=getattr(e, "status", None))
                raise
            finally:
                rec["ms"] = round((time.time() - t0) * 1000)
                with open(_LOG, "a") as f:
                    f.write(json.dumps(rec) + "\\n")
        _m.AsyncModels.generate_content = _wrapped
    except Exception:
        pass
''')

env = {**base, 'HOME': str(tmp), 'WORKFLOW_DB_HOST': '127.0.0.1', 'WORKFLOW_DB_PORT': '5433', 'WORKFLOW_DB_USER': 'workflow',
       'WORKFLOW_DB_PASSWORD': 'workflow', 'WORKFLOW_DB_NAME': db, 'WORKFLOW_DISABLE_DOTENV': '1', 'SEED': '0',
       'WORKFLOW_RELOAD': 'false', 'WORKFLOW_LOG_LEVEL': 'warning', 'GEMINI_API_KEY': os.environ['GEMINI_API_KEY'],
       'WORKFLOW_MODULE_GEN_MODEL': model, 'WORKFLOW_APP_BUILDER_MODEL': model, 'WORKFLOW_MODULE_GEN_TIMEOUT_S': '300',
       'WORKFLOW_RUN_RETENTION': '1000',  # default keeps only the newest 100 runs globally; the history step needs ~370
       'PYTHONPATH': str(hook), 'MARKETPLACE_LLM_LOG': str(llm_log)}
assert not any(k.startswith('WORKFLOW_FAKE') for k in env)
from cryptography.fernet import Fernet
env['WORKFLOW_ENCRYPTION_KEY'] = Fernet.generate_key().decode()
procs = []


def start(args, cwd, env, name):
    f = open(tmp / name, 'w')
    p = subprocess.Popen(args, cwd=cwd, env=env, stdout=f, stderr=f, start_new_session=True)
    procs.append(p)
    return p


def ready(url, p):
    with httpx.Client(trust_env=False) as c:
        for _ in range(160):
            if p.poll() is not None:
                raise RuntimeError('Service exited; inspect local log')
            try:
                if c.get(url).status_code == 200:
                    return
            except httpx.HTTPError:
                pass
            time.sleep(.25)
    raise RuntimeError('Service readiness timeout')


try:
    engine = start([PY, '-m', 'uvicorn', 'src.main:app', '--app-dir', str(ENGINE), '--host', '127.0.0.1', '--port', str(ep)], tmp, env, 'engine.log')
    ready(f'http://127.0.0.1:{ep}/health', engine)
    with httpx.Client(trust_env=False) as c:
        assert [p['id'] for p in c.get(f'http://127.0.0.1:{ep}/api/projects').json()] == ['proj_default']
    vite = start([str(STUDIO / 'node_modules/.bin/vite'), '--host', '127.0.0.1', '--port', str(vp), '--strictPort'], STUDIO,
                 {**base, 'VITE_WORKFLOW_API_URL': f'http://127.0.0.1:{ep}'}, 'vite.log')
    ready(f'http://127.0.0.1:{vp}', vite)
    state = {'engineUrl': f'http://127.0.0.1:{ep}', 'studioUrl': f'http://127.0.0.1:{vp}', 'upstreamUrl': f'http://127.0.0.1:{up}',
             'model': model, 'dbName': db, 'tmp': str(tmp), 'llmLog': str(llm_log), 'enginePid': engine.pid,
             'dbHost': '127.0.0.1', 'dbPort': 5433, 'dbUser': 'workflow', 'dbPassword': 'workflow'}
    (tmp / 'state.json').write_text(json.dumps(state))
    base['MARKETPLACE_STATE_FILE'] = str(tmp / 'state.json')
    base['MARKETPLACE_CREDENTIAL_FILE'] = str(cred)
    base['MARKETPLACE_ROOT'] = str(ROOT)
    base['MARKETPLACE_PYTHON'] = PY
    print('Real Gemini browser stack ready:', model, db, flush=True)
    if os.environ.get('MARKETPLACE_SERVE') == '1':
        # Diagnostic: keep the stack up so marketplace.cjs can be iterated (MARKETPLACE_ONLY=...) against it.
        print('SERVE: state file', tmp / 'state.json', flush=True)
        signal.signal(signal.SIGTERM, lambda *a: (_ for _ in ()).throw(SystemExit(0)))
        while True:
            time.sleep(3600)
    result = subprocess.run(['node', str(pathlib.Path(__file__).with_name('marketplace.cjs'))], env=base, text=True)
    code = result.returncode
finally:
    httpd.shutdown()
    for p in reversed(procs):
        if p.poll() is None:
            os.killpg(p.pid, signal.SIGTERM)
            try:
                p.wait(timeout=15)
            except subprocess.TimeoutExpired:
                os.killpg(p.pid, signal.SIGKILL)
    subprocess.run([PY, 'tests/e2e_stack.py', 'down', '--db', db], cwd=ENGINE, env=base, capture_output=True)
    key = os.environ.get('GEMINI_API_KEY', '')
    art = ROOT / 'screenshots/marketplace' / os.environ.get('MARKETPLACE_OUT', '')
    targets = list(tmp.glob('*.log')) + [llm_log] + [p for p in art.glob('*') if p.suffix in ('.json', '.py', '.ts', '.txt')]
    scan = {'gemini_key_occurrences': 0, 'secret_value_occurrences': 0, 'files_scanned': 0}
    for p in targets:
        if p.exists():
            text = p.read_text(errors='replace')
            scan['files_scanned'] += 1
            scan['gemini_key_occurrences'] += text.count(key) if key else 0
            scan['secret_value_occurrences'] += sum(text.count(v) for v in secret_values.values())
            if key:
                text = text.replace(key, '[REDACTED]')
            for v in secret_values.values():
                text = text.replace(v, '[REDACTED]')
            p.write_text(text)
    print('runner leak scan (before redaction):', json.dumps(scan), flush=True)
    art.mkdir(parents=True, exist_ok=True)
    (art / 'runner-leak-scan.json').write_text(json.dumps(scan, indent=2))
raise SystemExit(code)

"""Fixtures for the notebook integration tests (W2: keep, ask, browser).

* ``jobs_service``: apps/jobs-service on a free port with ``JOBS_DEV=1``, ``JOBS_LLM=off`` and a tmp
  ``JOBS_DATA_DIR`` (never the dev one on :8095). Skips, with the reason, only when node or Playwright's chromium is
  missing.
* ``target_site``: a static site on a ``ThreadingHTTPServer``: ``/login`` (Username / Password / Sign in) leads to
  ``/accounts`` (a table and a "Pay now" button), plus ``/wires/<id>`` and ``/alerts`` JSON endpoints.
* ``nbx_engine`` / ``nbx_api``: an engine with the test-only fake LLM, its own notebook storage dir, and
  ``JOBS_SERVICE_URL`` pointing at ``jobs_service`` (``JOBS_DEV=1``).
* ``nbx_project``: a project with connections ``bank`` (read-only) and ``bank_rw`` on ``bank_db``.
* ``fake_llm``: helpers to script the fake and read its call log.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Iterator

import httpx
import pytest

from .conftest import FAKE_LLM_TOKEN, LOG_DIR, EngineServer, StudioClient, _free_port, fake_env, uniq
from .notebook_support import bank_db  # noqa: F401  (re-exported fixture)

JOBS_DIR = Path(__file__).resolve().parents[3] / "jobs-service"
TOK = {"X-Test-Token": FAKE_LLM_TOKEN}
BUSY_PORTS = {8000, 8001, 8010, 8020, 8095, 5173, 5174, 5180, 4590, 4591}


def _port() -> int:
    for _ in range(20):
        p = _free_port()
        if p not in BUSY_PORTS:
            return p
    raise RuntimeError("no free port")


def chromium_missing() -> str | None:
    """Why a browser test can't run here, or None."""
    if shutil.which("node") is None:
        return "node is not installed on this host"
    if not (JOBS_DIR / "src" / "server.ts").exists():
        return f"apps/jobs-service is missing ({JOBS_DIR})"
    roots = [Path(os.environ["PLAYWRIGHT_BROWSERS_PATH"])] if os.environ.get("PLAYWRIGHT_BROWSERS_PATH") else []
    roots.append(Path.home() / ".cache" / "ms-playwright")
    if not any(r.is_dir() and any(r.glob("chromium*")) for r in roots):
        return "Playwright's chromium is not installed (npx playwright install chromium)"
    return None


# ── jobs service ─────────────────────────────────────────────────────────────


class JobsService:
    def __init__(self, data_dir: Path) -> None:
        self.port = _port()
        self.url = f"http://127.0.0.1:{self.port}"
        self.data_dir = data_dir
        self.proc: subprocess.Popen | None = None
        LOG_DIR.mkdir(exist_ok=True)
        self.log_path = LOG_DIR / f"jobs-{self.port}.log"

    def start(self) -> None:
        env = {k: v for k, v in os.environ.items() if not k.startswith(("JOBS_", "ANTHROPIC_", "OPENAI_", "GEMINI_"))}
        env.update({"JOBS_DEV": "1", "JOBS_LLM": "off", "JOBS_DATA_DIR": str(self.data_dir),
                    "JOBS_PORT": str(self.port), "JOBS_HOST": "127.0.0.1"})
        self._log = open(self.log_path, "w")
        self.proc = subprocess.Popen(["node", "src/server.ts"], cwd=JOBS_DIR, env=env, stdout=self._log,
                                     stderr=subprocess.STDOUT)
        deadline = time.time() + 30
        while time.time() < deadline:
            if self.proc.poll() is not None:
                raise RuntimeError(f"jobs-service exited early; log:\n{self.log_path.read_text()}")
            try:
                if httpx.get(self.url + "/health", timeout=1).status_code == 200:
                    return
            except httpx.HTTPError:
                pass
            time.sleep(0.2)
        raise RuntimeError(f"jobs-service did not become healthy; log:\n{self.log_path.read_text()}")

    def stop(self) -> None:
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=15)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        if getattr(self, "_log", None):
            self._log.close()


@pytest.fixture(scope="module")
def jobs_service(tmp_path_factory) -> Iterator[JobsService]:
    why = chromium_missing()
    if why:
        pytest.skip(f"browser tests need the jobs-service + chromium: {why}")
    js = JobsService(tmp_path_factory.mktemp("jobs-data"))
    js.start()
    try:
        yield js
    finally:
        js.stop()


# ── the target site ──────────────────────────────────────────────────────────

LOGIN_HTML = """<!doctype html><html><head><title>Portal</title></head><body>
<h1>Sign in</h1>
<form action="/accounts" method="get">
  <label for="u">Username</label><input id="u" name="u">
  <label for="p">Password</label><input id="p" name="p" type="password">
  <button type="submit">Sign in</button>
</form></body></html>"""

ACCOUNTS_HTML = """<!doctype html><html><head><title>Accounts</title></head><body>
<h1>Accounts</h1>
<table><thead><tr><th>Account</th><th>Balance</th></tr></thead>
<tbody><tr><td>A1</td><td>1000</td></tr><tr><td>A2</td><td>5000</td></tr></tbody></table>
<button type="button" onclick="document.title='paid'">Pay now</button>
</body></html>"""


#: Take over: a button over the top half of the 1280x800 viewport and an autofocused box; the page reports that it
#: loaded (so a test knows the page is up before it takes over) and mirrors what the user did into a status line.
TAKEOVER_HTML = """<!doctype html><html><head><title>Take over</title><style>
body{margin:0} #go{position:fixed;left:0;top:0;width:100vw;height:50vh;font-size:40px}
#box{position:fixed;left:10px;top:60vh;width:400px;font-size:20px}
#r{position:fixed;left:10px;top:75vh;font-size:20px}</style></head><body>
<button id="go" type="button">Big button</button>
<input id="box" aria-label="Box" autofocus>
<div id="r" role="status" aria-label="Result">clicked=no typed= entered=no</div>
<script>
const st = {clicked: 'no', typed: '', entered: 'no'};
const show = () => { document.getElementById('r').textContent = `clicked=${st.clicked} typed=${st.typed} entered=${st.entered}`; };
document.getElementById('go').onclick = () => { st.clicked = 'yes'; show(); document.getElementById('box').focus(); };
document.getElementById('box').oninput = (e) => { st.typed = e.target.value; show(); };
document.getElementById('box').onkeydown = (e) => { if (e.key === 'Enter') { st.entered = 'yes'; show(); } };
fetch('/takeover-loaded');
</script></body></html>"""


class _Site(BaseHTTPRequestHandler):
    seen: list[dict[str, Any]] = []

    def _send(self, status: int, body: str, ctype: str) -> None:
        raw = body.encode()
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self) -> None:  # noqa: N802
        path = self.path.split("?")[0]
        _Site.seen.append({"method": "GET", "path": self.path, "auth": self.headers.get("authorization")})
        if path in ("/", "/login"):
            return self._send(200, LOGIN_HTML, "text/html")
        if path == "/accounts":
            return self._send(200, ACCOUNTS_HTML, "text/html")
        if path == "/takeover":
            return self._send(200, TAKEOVER_HTML, "text/html")
        if path == "/takeover-loaded":
            return self._send(200, "{}", "application/json")
        if path.startswith("/wires/"):
            return self._send(200, json.dumps({"wire_id": path.rsplit("/", 1)[-1], "state": "stuck"}), "application/json")
        return self._send(404, "not found", "text/plain")

    def do_POST(self) -> None:  # noqa: N802
        n = int(self.headers.get("content-length") or 0)
        body = self.rfile.read(n) if n else b""
        _Site.seen.append({"method": "POST", "path": self.path, "body": body.decode("utf-8", "replace"),
                           "auth": self.headers.get("authorization")})
        if self.path.startswith("/alerts"):
            return self._send(200, json.dumps({"ok": True}), "application/json")
        return self._send(404, "not found", "text/plain")

    def log_message(self, *args: Any) -> None:
        pass


@pytest.fixture(scope="module")
def target_site() -> Iterator[str]:
    srv = ThreadingHTTPServer(("127.0.0.1", _port()), _Site)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    try:
        yield f"http://127.0.0.1:{srv.server_address[1]}"
    finally:
        srv.shutdown()
        srv.server_close()


# ── engines ──────────────────────────────────────────────────────────────────


def _engine(test_db, upstream: str, tmp_path_factory, label: str, jobs_url: str) -> EngineServer:
    """Fake-LLM engine from an empty cwd (no .env; dotenv disabled), like conftest's ``fake_stack``."""
    store = tmp_path_factory.mktemp("nbx-store")
    srv = EngineServer(test_db, extra_env={**fake_env(test_db, E2E_UPSTREAM_URL=upstream),
                                           "WORKFLOW_NOTEBOOK_STORAGE_DIR": str(store),
                                           "JOBS_SERVICE_URL": jobs_url, "JOBS_DEV": "1"},
                       cwd=str(tmp_path_factory.mktemp("nbx-cwd")), label=label)
    srv.start()
    return srv


@pytest.fixture(scope="module")
def nbx_engine(test_db, upstream, tmp_path_factory) -> Iterator[EngineServer]:
    """Fake LLM; jobs URL is a closed port unless the module also asks for ``nbx_jobs_engine``."""
    srv = _engine(test_db, upstream, tmp_path_factory, "-nbx", f"http://127.0.0.1:{_port()}")
    try:
        yield srv
    finally:
        srv.stop()


@pytest.fixture(scope="module")
def nbx_api(nbx_engine) -> StudioClient:
    return StudioClient(nbx_engine.base_url)


@pytest.fixture(scope="module")
def nbx_jobs_engine(test_db, upstream, tmp_path_factory, jobs_service) -> Iterator[EngineServer]:
    srv = _engine(test_db, upstream, tmp_path_factory, "-nbxj", jobs_service.url)
    try:
        yield srv
    finally:
        srv.stop()


@pytest.fixture(scope="module")
def nbx_jobs_api(nbx_jobs_engine) -> StudioClient:
    return StudioClient(nbx_jobs_engine.base_url)


def make_project(api: StudioClient, bank_db: Any | None = None) -> dict[str, Any]:
    p = api.post("/projects", {"name": uniq("nbx")}).json()
    out = {**p}
    if bank_db is not None:
        ro = api.post(f"/projects/{p['id']}/connections", {"name": "bank", "kind": "postgres", "url": bank_db.url})
        assert ro.status_code == 201, ro.text
        rw = api.post(f"/projects/{p['id']}/connections",
                      {"name": "bank_rw", "kind": "postgres", "url": bank_db.url, "readonly": False})
        assert rw.status_code == 201, rw.text
        out.update(ro=ro.json(), rw=rw.json())
    return out


def add_secret(api: StudioClient, project: dict[str, Any], key: str, value: str) -> dict[str, Any]:
    env = project.get("default_environment") or "dev"
    r = api.post(f"/projects/{project['id']}/secrets", {"key": key, "value": value, "environment": env,
                                                        "type": "secret"})
    assert r.status_code in (200, 201), r.text
    return r.json()


# ── fake LLM ─────────────────────────────────────────────────────────────────


def set_script(api: StudioClient, rules: list[dict[str, Any]]) -> None:
    r = api.put("/_test/fake-llm/script", {"rules": rules, "default": {"raise": "unmatched"}}, headers=TOK)
    assert r.status_code == 200, r.text


def llm_calls(api: StudioClient) -> list[dict[str, Any]]:
    r = api.get("/_test/fake-llm/calls", headers=TOK)
    assert r.status_code == 200, r.text
    return r.json()


def clear_calls(api: StudioClient) -> None:
    assert api.delete("/_test/fake-llm/calls", headers=TOK).status_code == 200


def set_unavailable(api: StudioClient, value: bool) -> None:
    assert api.put("/_test/fake-llm/state", {"unavailable": value}, headers=TOK).status_code == 200

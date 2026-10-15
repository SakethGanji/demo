"""Integration harness: the real engine over HTTP against a throwaway database.

* Creates ``workflows_test_<hex>`` on the configured Postgres (default localhost:5433),
  creates the ``"workflow-app"`` schema and applies every migration with the real migrator.
* Starts ``uvicorn src.main:app`` as a subprocess pointed at that database (SEED=0), so the
  lifespan, background tasks, SSE and cron behave exactly as in production.
* ``api`` is a client that calls the engine the way the studio does
  (``studio:shared/lib/api.ts:67-74``): ``/api`` prefix, ``Content-Type: application/json`` on
  every request (GET/DELETE included), no identity headers.

It never touches the ``workflows`` database or the :8000 engine.
Set KEEP_TEST_DB=1 to keep the database after the run.
"""

from __future__ import annotations

import json
import os
import secrets
import socket
import subprocess
import sys
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Iterator
from urllib.parse import parse_qs, urlparse

import httpx
import psycopg2
import pytest

ENGINE_DIR = Path(__file__).resolve().parents[2]
LOG_DIR = Path(__file__).resolve().parent / ".logs"

DB_HOST = os.environ.get("WORKFLOW_DB_HOST", "localhost")
DB_PORT = int(os.environ.get("WORKFLOW_DB_PORT", "5433"))
DB_USER = os.environ.get("WORKFLOW_DB_USER", "workflow")
DB_PASSWORD = os.environ.get("WORKFLOW_DB_PASSWORD", "workflow")
PROTECTED_DBS = {"workflows", "postgres", "template0", "template1"}


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _admin_conn():
    conn = psycopg2.connect(host=DB_HOST, port=DB_PORT, user=DB_USER, password=DB_PASSWORD, dbname="postgres")
    conn.autocommit = True
    return conn


class TestDatabase:
    __test__ = False
    def __init__(self) -> None:
        self.name = os.environ.get("TEST_DB_NAME") or f"workflows_test_{uuid.uuid4().hex[:8]}"
        if self.name in PROTECTED_DBS or not self.name.startswith("workflows_test_"):
            raise RuntimeError(f"refusing to use database {self.name!r} for tests")

    def env(self) -> dict[str, str]:
        return {
            "WORKFLOW_DB_HOST": DB_HOST,
            "WORKFLOW_DB_PORT": str(DB_PORT),
            "WORKFLOW_DB_USER": DB_USER,
            "WORKFLOW_DB_PASSWORD": DB_PASSWORD,
            "WORKFLOW_DB_NAME": self.name,
        }

    def create(self) -> None:
        conn = _admin_conn()
        with conn.cursor() as cur:
            cur.execute(f'CREATE DATABASE "{self.name}"')
        conn.close()
        c = self.connect()
        with c.cursor() as cur:
            cur.execute('CREATE SCHEMA IF NOT EXISTS "workflow-app"')
        c.commit()
        c.close()

    def migrate(self, *args: str, extra_env: dict[str, str] | None = None) -> str:
        env = {**os.environ, **self.env(), **(extra_env or {})}
        out = subprocess.run(
            [sys.executable, "-m", "src.db.migrate", *(args or ("apply",))],
            cwd=ENGINE_DIR, env=env, capture_output=True, text=True, timeout=120,
        )
        if out.returncode != 0:
            raise RuntimeError(f"migrate {args} failed:\n{out.stdout}\n{out.stderr}")
        return out.stdout

    def connect(self):
        return psycopg2.connect(
            host=DB_HOST, port=DB_PORT, user=DB_USER, password=DB_PASSWORD, dbname=self.name,
            options='-c search_path="workflow-app"',
        )

    def query(self, sql: str, params: tuple = ()) -> list[tuple]:
        c = self.connect()
        try:
            with c.cursor() as cur:
                cur.execute(sql, params)
                rows = cur.fetchall() if cur.description else []
            c.commit()
            return rows
        finally:
            c.close()

    def drop(self) -> None:
        conn = _admin_conn()
        with conn.cursor() as cur:
            cur.execute(f'DROP DATABASE IF EXISTS "{self.name}" WITH (FORCE)')
        conn.close()


class EngineServer:
    def __init__(self, db: TestDatabase) -> None:
        self.db = db
        self.port = _free_port()
        if self.port == 8000:
            raise RuntimeError("refusing to start test engine on :8000")
        self.base_url = f"http://127.0.0.1:{self.port}"
        self.encryption_key = __import__("cryptography.fernet", fromlist=["Fernet"]).Fernet.generate_key().decode()
        self.proc: subprocess.Popen | None = None
        LOG_DIR.mkdir(exist_ok=True)
        self.log_path = LOG_DIR / f"engine-{db.name}.log"

    def start(self) -> None:
        env = {
            **os.environ,
            **self.db.env(),
            "SEED": "0",
            "WORKFLOW_ENCRYPTION_KEY": self.encryption_key,
            "WORKFLOW_PUBLIC_TOKEN_SECRET": "test-public-token-secret",
            "WORKFLOW_RELOAD": "false",
            "WORKFLOW_LOG_LEVEL": "warning",
        }
        self._log = open(self.log_path, "w")
        self.proc = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "src.main:app", "--host", "127.0.0.1", "--port", str(self.port)],
            cwd=ENGINE_DIR, env=env, stdout=self._log, stderr=subprocess.STDOUT,
        )
        deadline = time.time() + 60
        while time.time() < deadline:
            if self.proc.poll() is not None:
                raise RuntimeError(f"engine exited early; log:\n{self.log_path.read_text()}")
            try:
                if httpx.get(self.base_url + "/health", timeout=1).status_code == 200:
                    return
            except httpx.HTTPError:
                pass
            time.sleep(0.25)
        raise RuntimeError(f"engine did not become healthy; log:\n{self.log_path.read_text()}")

    def stop(self) -> None:
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=20)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        if getattr(self, "_log", None):
            self._log.close()


class StudioClient:
    """Calls the engine exactly like the studio's ``apiFetch`` / direct fetches."""

    STUDIO_HEADERS = {"Content-Type": "application/json"}

    def __init__(self, base_url: str) -> None:
        self.base_url = base_url
        self.http = httpx.Client(base_url=base_url, timeout=60)

    # /api/... through apiFetch (always JSON content-type, even for GET/DELETE)
    def _api(self, method: str, endpoint: str, json_body: Any = None, **kw) -> httpx.Response:
        headers = {**self.STUDIO_HEADERS, **kw.pop("headers", {})}
        content = None if json_body is None else json.dumps(json_body)
        return self.http.request(method, "/api" + endpoint, content=content, headers=headers, **kw)

    def get(self, endpoint: str, **kw) -> httpx.Response:
        return self._api("GET", endpoint, **kw)

    def post(self, endpoint: str, body: Any = None, **kw) -> httpx.Response:
        return self._api("POST", endpoint, body, **kw)

    def put(self, endpoint: str, body: Any = None, **kw) -> httpx.Response:
        return self._api("PUT", endpoint, body, **kw)

    def patch(self, endpoint: str, body: Any = None, **kw) -> httpx.Response:
        return self._api("PATCH", endpoint, body, **kw)

    def delete(self, endpoint: str, **kw) -> httpx.Response:
        return self._api("DELETE", endpoint, **kw)

    # Raw (non-/api) paths: webhooks, streaming, public apps, project router
    def raw(self, method: str, path: str, **kw) -> httpx.Response:
        return self.http.request(method, path, **kw)

    def stream_events(self, path: str, body: dict) -> list[dict]:
        """POST an SSE endpoint the way useExecutionStream.ts does and parse `data:` lines."""
        events: list[dict] = []
        with self.http.stream("POST", path, content=json.dumps(body), headers=self.STUDIO_HEADERS) as r:
            assert r.status_code == 200, r.read()
            for line in r.iter_lines():
                if line.startswith("data:"):
                    payload = line[5:].strip()
                    if payload:
                        try:
                            events.append(json.loads(payload))
                        except json.JSONDecodeError:
                            pass
        return events


# ── Upstream fake API (for HttpRequest / api tester / allowlist tests) ───────


class _EchoHandler(BaseHTTPRequestHandler):
    server_version = "EchoUpstream"

    def _handle(self) -> None:
        length = int(self.headers.get("content-length") or 0)
        raw = self.rfile.read(length) if length else b""
        u = urlparse(self.path)
        if u.path.startswith("/status/"):
            code = int(u.path.split("/")[2])
            self.send_response(code)
            self.send_header("content-type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"status": code}).encode())
            return
        try:
            body: Any = json.loads(raw) if raw else None
        except Exception:
            body = raw.decode("utf-8", "replace")
        payload = {
            "method": self.command,
            "path": u.path,
            "query": {k: v[0] for k, v in parse_qs(u.query).items()},
            "headers": {k.lower(): v for k, v in self.headers.items()},
            "body": body,
        }
        data = json.dumps(payload).encode()
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    do_GET = do_POST = do_PUT = do_PATCH = do_DELETE = _handle

    def log_message(self, *args) -> None:  # silence
        pass


def pytest_configure(config) -> None:
    config.addinivalue_line("markers", "integration: real engine over HTTP against a throwaway DB")


# ── Fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture(scope="session")
def test_db() -> Iterator[TestDatabase]:
    db = TestDatabase()
    db.create()
    try:
        db.migrate("apply")
        yield db
    finally:
        if os.environ.get("KEEP_TEST_DB") != "1":
            db.drop()
        else:
            print(f"\nKEEP_TEST_DB=1 → kept database {db.name}")


@pytest.fixture(scope="session")
def engine(test_db: TestDatabase) -> Iterator[EngineServer]:
    srv = EngineServer(test_db)
    srv.start()
    try:
        yield srv
    finally:
        srv.stop()


@pytest.fixture(scope="session")
def api(engine: EngineServer) -> StudioClient:
    return StudioClient(engine.base_url)


@pytest.fixture(scope="session")
def upstream() -> Iterator[str]:
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _EchoHandler)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    try:
        yield f"http://127.0.0.1:{srv.server_address[1]}"
    finally:
        srv.shutdown()


def uniq(prefix: str = "t") -> str:
    return f"{prefix}-{secrets.token_hex(4)}"


# ── Workflow builders (shapes the editor sends: BackendWorkflow) ─────────────


def start_set_workflow(name: str | None = None, expr: str = "{{ 20 + 22 }}", field: str = "answer") -> dict:
    return {
        "name": name or uniq("wf"),
        "nodes": [
            {"name": "Start", "type": "Start", "parameters": {}, "position": {"x": 0, "y": 0}},
            {
                "name": "Set",
                "type": "Set",
                "parameters": {"mode": "manual", "fields": [{"name": field, "value": expr}], "keepOnlySet": True},
                "position": {"x": 200, "y": 0},
            },
        ],
        "connections": [{"source_node": "Start", "target_node": "Set", "source_output": "main", "target_input": "main"}],
        "settings": {},
    }


def webhook_workflow(path: str | None = None, method: str = "POST", response_mode: str = "lastNode",
                     set_value: str = "{{ $json.body.x }}", name: str | None = None) -> dict:
    params: dict[str, Any] = {"method": method, "responseMode": response_mode}
    if path:
        params["path"] = path
    return {
        "name": name or uniq("hook"),
        "nodes": [
            {"name": "Webhook", "type": "Webhook", "parameters": params, "position": {"x": 0, "y": 0}},
            {
                "name": "Set",
                "type": "Set",
                "parameters": {"mode": "manual", "fields": [{"name": "got", "value": set_value}], "keepOnlySet": True},
                "position": {"x": 200, "y": 0},
            },
        ],
        "connections": [{"source_node": "Webhook", "target_node": "Set", "source_output": "main", "target_input": "main"}],
        "settings": {},
    }


def http_workflow(url: str, headers: list[dict] | None = None, name: str | None = None, method: str = "GET") -> dict:
    return {
        "name": name or uniq("http"),
        "nodes": [
            {"name": "Start", "type": "Start", "parameters": {}, "position": {"x": 0, "y": 0}},
            {
                "name": "HTTP",
                "type": "HttpRequest",
                "parameters": {"method": method, "url": url, "headers": headers or [], "responseType": "json"},
                "position": {"x": 200, "y": 0},
            },
        ],
        "connections": [{"source_node": "Start", "target_node": "HTTP", "source_output": "main", "target_input": "main"}],
        "settings": {},
    }

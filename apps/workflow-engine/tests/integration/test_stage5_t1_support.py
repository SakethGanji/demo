"""Shared harness for the Stage 5 tester files (T1 + T2). Contains no tests.

* ``Stack``: its own throwaway Postgres database plus one or more real engine subprocesses (fake-LLM mode, so the
  loopback ``/api/_test/*`` hooks exist) started from an empty cwd. Engines are killed by PID in teardown.
* ``RefundApi``: an in-process fake bank API that counts every ``POST /refunds`` (the at-most-once witness) and has a
  ``/slow-refunds`` route that blocks until released, so a test can kill the engine while a refund call is in flight.
* Builders for the refund fixture, the public form flow, task lookups and polling with deadlines (no long sleeps).
"""

from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import tempfile
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable, Iterator

import pytest

from .conftest import FAKE_LLM_TOKEN, EngineServer, StudioClient, TestDatabase, fake_env, uniq

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "workflows"
CLASSIFIED = {"category": "damaged", "summary": "Looks like a damaged parcel.", "suggest": "approve"}
GOOD_FIELDS = {"order_id": "A-10042", "amount": 742, "reason": "damaged", "email": "dana@example.com"}


# ── fake bank API ─────────────────────────────────────────────────────────────


class RefundApi:
    """POST /refunds records {path, key, body}. POST /slow-refunds records, then blocks until ``release()``."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.lock = threading.Lock()
        self.hold = threading.Event()
        outer = self

        class H(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def _go(self) -> None:
                n = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(n).decode() if n else ""
                path = self.path.split("?", 1)[0]
                if path in ("/refunds", "/slow-refunds"):
                    with outer.lock:
                        outer.calls.append({"path": path, "key": self.headers.get("Idempotency-Key"), "body": raw})
                        idx = len(outer.calls)
                    if path == "/slow-refunds":
                        outer.hold.wait(60)
                    payload = {"ok": True, "refund_id": f"rf_{1000 + idx}"}
                else:
                    payload = {"ok": True}
                body = json.dumps(payload).encode()
                try:
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                except OSError:
                    pass

            do_GET = do_POST = do_PUT = _go

            def log_message(self, *a: Any) -> None:
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.server.daemon_threads = True
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def refunds(self) -> list[dict[str, Any]]:
        with self.lock:
            return [c for c in self.calls if c["path"] in ("/refunds", "/slow-refunds")]

    def reset(self) -> None:
        with self.lock:
            self.calls.clear()
        self.hold.clear()

    def release(self) -> None:
        self.hold.set()

    def close(self) -> None:
        self.hold.set()
        self.server.shutdown()
        self.server.server_close()


# ── stack: db + engines ───────────────────────────────────────────────────────


def kill_engine(srv: EngineServer) -> None:
    """kill -9 by PID (a crash, not a graceful stop)."""
    p = srv.proc
    if p is not None and p.poll() is None:
        try:
            os.kill(p.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        try:
            p.wait(timeout=15)
        except subprocess.TimeoutExpired:  # pragma: no cover
            pass
    if getattr(srv, "_log", None):
        srv._log.close()


class Stack:
    def __init__(self, label: str = "") -> None:
        self.db = TestDatabase()
        self.db.name = f"workflows_test_s5{label}_{uuid.uuid4().hex[:8]}"
        self.label = label
        self.cwd = tempfile.mkdtemp(prefix="s5-engine-cwd-")  # empty: the engine never finds a dotenv
        self.engines: list[EngineServer] = []
        self.refunds = RefundApi()
        self._enc_key: str | None = None
        self.db.create()
        self.db.migrate("apply")
        self.engine = self.add_engine()
        self.api = StudioClient(self.engine.base_url)

    def add_engine(self, tag: str = "") -> EngineServer:
        srv = EngineServer(self.db, extra_env=fake_env(self.db, E2E_UPSTREAM_URL=self.refunds.url), cwd=self.cwd, label=f"-s5{self.label}{tag}{len(self.engines)}")
        if self._enc_key:
            srv.encryption_key = self._enc_key
        self._enc_key = srv.encryption_key
        srv.start()
        self.engines.append(srv)
        return srv

    def client(self, srv: EngineServer | None = None) -> StudioClient:
        return StudioClient((srv or self.engine).base_url)

    def crash(self) -> None:
        """kill -9 the primary engine."""
        kill_engine(self.engine)

    def restart(self) -> EngineServer:
        """Start a fresh engine process on the same database (the old one must be dead) and make it the primary."""
        self.engine = self.add_engine("r")
        self.api = StudioClient(self.engine.base_url)
        return self.engine

    def hook(self, method: str, path: str, body: dict | None = None, srv: EngineServer | None = None, client: StudioClient | None = None):
        c = client or self.client(srv)
        return c.raw(method, "/api/_test" + path, content=json.dumps(body) if body is not None else None,
                     headers={"X-Test-Token": FAKE_LLM_TOKEN, "Content-Type": "application/json"}, timeout=120)

    def tick(self, srv: EngineServer | None = None, **body) -> dict:
        r = self.hook("POST", "/tasks/tick", {"wait": True, **body}, srv)
        assert r.status_code == 200, r.text
        return r.json()

    def q(self, sql: str, params: tuple = ()) -> list[tuple]:
        return self.db.query(sql, params)

    def close(self) -> None:
        for e in self.engines:
            kill_engine(e)
        self.refunds.close()
        try:
            self.db.drop()
        finally:
            shutil.rmtree(self.cwd, ignore_errors=True)


@pytest.fixture(scope="module")
def stack() -> Iterator[Stack]:
    s = Stack()
    try:
        yield s
    finally:
        s.close()


@pytest.fixture
def fresh_stack() -> Iterator[Stack]:
    """A private database and engine for tests that crash or restart the engine."""
    s = Stack("x")
    try:
        yield s
    finally:
        s.close()


# ── builders and helpers ──────────────────────────────────────────────────────


def N(name, type_, params=None, x=0):
    return {"name": name, "type": type_, "parameters": params or {}, "position": {"x": x, "y": 0}}


def C(a, b, port="main"):
    return {"source_node": a, "target_node": b, "source_output": port, "target_input": "main"}


def refund_workflow(refunds_url: str, slug: str, *, on_timeout: str = "timed_out", due_in: str | None = "3h", issue_path: str = "/refunds",
                    require_comment_on_reject: bool = False, form: list | None = None, name: str | None = None,
                    access: dict | None = None, pin: bool = True) -> dict:
    raw = (FIXTURES / "refund_triage_ui.json").read_text().replace("__REFUND_API_URL__", refunds_url)
    wf = json.loads(raw)
    wf["name"] = name or uniq("refund")
    wf["interface"]["slug"] = slug
    if access is not None:
        wf["interface"]["access"] = access
    for n in wf["nodes"]:
        if n["name"] == "Classify" and pin:
            n["pinned_data"] = [{"json": {**CLASSIFIED, "structured": dict(CLASSIFIED)}}]
        if n["name"] == "Manager review":
            p = n["parameters"]
            p["on_timeout"] = on_timeout
            if due_in is None:
                p.pop("due_in", None)
            else:
                p["due_in"] = due_in
            p["require_comment_on_reject"] = require_comment_on_reject
            if form is not None:
                p["form"] = form
        if n["name"] == "Issue refund":
            n["parameters"]["url"] = refunds_url + issue_path
    return wf


class Flow:
    """One published interface workflow in its own project."""

    def __init__(self, stack: Stack, wid: str, pid: str, pslug: str, slug: str) -> None:
        self.stack, self.wid, self.pid, self.pslug, self.slug = stack, wid, pid, pslug, slug
        self.base = f"/i/{pslug}/{slug}"

    @property
    def api(self) -> StudioClient:
        return self.stack.api


def new_project(api: StudioClient) -> tuple[str, str]:
    p = api.post("/projects", {"name": uniq("s5")}).json()
    return p["id"], p["slug"]


def publish_flow(stack: Stack, wf: dict, *, project: tuple[str, str] | None = None, publish: bool = True) -> Flow:
    api = stack.api
    pid, pslug = project or new_project(api)
    interface = wf.get("interface")
    body = {k: v for k, v in wf.items() if k != "interface"}
    r = api.post(f"/projects/{pid}/workflows", body)
    assert r.status_code == 201, r.text
    wid = r.json()["id"]
    if interface:
        put = api.put(f"/workflows/{wid}/interface", interface)
        assert put.status_code == 200, put.text
    if publish:
        pub = api.post(f"/workflows/{wid}/publish", {})
        assert pub.status_code == 200, pub.text
    return Flow(stack, wid, pid, pslug, (interface or {}).get("slug", ""))


def refund_flow(stack: Stack, **kw) -> Flow:
    return publish_flow(stack, refund_workflow(stack.refunds.url, kw.pop("slug", None) or uniq("refunds"), **kw))


def submit(flow: Flow, fields: dict | None = None, key: str | None = None, client: StudioClient | None = None, expect: int = 202) -> dict:
    c = client or flow.api
    r = c.post(f"{flow.base}/submit", {"fields": fields if fields is not None else dict(GOOD_FIELDS)},
               headers={"Idempotency-Key": key or uuid.uuid4().hex})
    assert r.status_code == expect, r.text
    return r.json()


def poll_until(fn: Callable[[], Any], ok: Callable[[Any], bool], timeout: float = 30, every: float = 0.15, what: str = "condition") -> Any:
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        last = fn()
        if ok(last):
            return last
        time.sleep(every)
    raise AssertionError(f"timed out after {timeout}s waiting for {what}; last = {last!r}")


def run_view(flow: Flow, sub: dict, client: StudioClient | None = None) -> dict:
    r = (client or flow.api).get(f"{flow.base}/runs/{sub['execution_id']}", headers={"Authorization": f"Bearer {sub['run_token']}"})
    assert r.status_code == 200, r.text
    return r.json()


def wait_run(flow: Flow, sub: dict, until=("success", "failed", "cancelled"), timeout: float = 30, client: StudioClient | None = None) -> dict:
    return poll_until(lambda: run_view(flow, sub, client), lambda v: v["status"] in until, timeout, what=f"run {sub['execution_id']} in {until}")


def exec_row(stack: Stack, exec_id: str) -> dict:
    rows = stack.q("SELECT status, metadata FROM executions WHERE id = %s", (exec_id,))
    assert rows, f"no execution {exec_id}"
    return {"status": rows[0][0], "metadata": rows[0][1] or {}}


def task_row(stack: Stack, task_id: str) -> dict:
    rows = stack.q("SELECT status, resume_state, decided_by, decision, decide_key, resume_heartbeat, resume_owner FROM human_tasks WHERE id = %s", (task_id,))
    assert rows, f"no task {task_id}"
    keys = ("status", "resume_state", "decided_by", "decision", "decide_key", "resume_heartbeat", "resume_owner")
    return dict(zip(keys, rows[0]))


def wait_task(flow: Flow, sub: dict, timeout: float = 30) -> dict:
    """The open task of a submitted run, found through the inbox (GET /api/tasks)."""
    def find():
        items = flow.api.get("/tasks", params={"project_id": flow.pid, "status": "all", "limit": 100}).json()["items"]
        return next((t for t in items if t["execution_id"] == sub["execution_id"]), None)
    return poll_until(find, lambda t: t is not None, timeout, what=f"a task of {sub['execution_id']}")


def submit_waiting(flow: Flow, fields: dict | None = None) -> tuple[dict, dict]:
    sub = submit(flow, fields)
    task = wait_task(flow, sub)
    wait_run(flow, sub, until=("waiting",))
    return sub, task


def decide(api: StudioClient, task_id: str, decision: str = "approved", by: str = "Dana", key: str | None = None, **extra):
    return api.post(f"/tasks/{task_id}/decide", {"decision": decision, "decided_by": by, **extra},
                    headers={"Idempotency-Key": key or uuid.uuid4().hex})


def concurrently(n: int, fn: Callable[[int], Any]) -> list[Any]:
    """Run ``fn(i)`` on n threads released together by a barrier; returns results in index order."""
    barrier = threading.Barrier(n)

    def run(i: int):
        barrier.wait(30)
        return fn(i)

    with ThreadPoolExecutor(max_workers=n) as ex:
        return list(ex.map(run, range(n)))

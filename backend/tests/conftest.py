"""Tests talk to a real uvicorn process over HTTP, exactly as the UI does (through /api).

Each test session gets its own data folder, so the samples are generated from scratch and nothing
touches backend/data. `api_server` can be restarted on the same folder to test recovery.
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest

BACKEND = Path(__file__).resolve().parent.parent


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class ApiServer:
    def __init__(self, data_dir: Path, env: dict[str, str] | None = None) -> None:
        self.data_dir = data_dir
        self.env = env or {}
        self.port = _free_port()
        self.proc: subprocess.Popen | None = None
        self.log = data_dir.parent / "server.log"

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def start(self, timeout: float = 90) -> None:
        env = {**os.environ, "ENSEMBLE_DATA_DIR": str(self.data_dir), "PYTHONDONTWRITEBYTECODE": "1", **self.env}
        self.proc = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "app.main:app", "--port", str(self.port), "--log-level", "warning"],
            cwd=BACKEND, env=env, stdout=open(self.log, "ab"), stderr=subprocess.STDOUT,
        )
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self.proc.poll() is not None:
                raise RuntimeError(f"API exited early; see {self.log}:\n{self.log.read_text()[-3000:]}")
            try:
                if httpx.get(f"{self.url}/api/health", timeout=1).status_code == 200:
                    return
            except httpx.HTTPError:
                pass
            time.sleep(0.2)
        raise RuntimeError(f"API did not become healthy in {timeout}s; see {self.log}")

    def stop(self) -> None:
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(10)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        self.proc = None

    def restart(self) -> None:
        self.stop()
        self.start()


@pytest.fixture(scope="session")
def api_server(tmp_path_factory: pytest.TempPathFactory):
    server = ApiServer(tmp_path_factory.mktemp("ensemble") / "data")
    server.start()
    yield server
    server.stop()


@pytest.fixture(scope="session")
def client(api_server: ApiServer):
    # A transport that follows restarts: the base URL is the same after api_server.restart().
    with httpx.Client(base_url=api_server.url, timeout=60) as c:
        yield c


@pytest.fixture(scope="session")
def data_dir(api_server: ApiServer) -> Path:
    return api_server.data_dir


# -- runs ---------------------------------------------------------------------

TERMINAL = ("run_succeeded", "run_failed", "run_cancelled")


def read_sse(client: httpx.Client, url: str, headers: dict | None = None, timeout: float = 600) -> list[dict]:
    """Read an SSE stream to its end. Returns [{id, event, data}] with data parsed as JSON (pings skipped)."""
    import json

    out, cur = [], {}
    with client.stream("GET", url, headers=headers or {}, timeout=httpx.Timeout(timeout, read=timeout)) as r:
        assert r.status_code == 200, r.read()
        assert r.headers["content-type"].startswith("text/event-stream")
        for line in r.iter_lines():
            if line == "":
                if "data" in cur:
                    cur["data"] = json.loads(cur["data"])
                    out.append(cur)
                cur = {}
            elif line.startswith(":"):
                continue
            else:
                k, _, v = line.partition(":")
                cur[k] = v[1:] if v.startswith(" ") else v
    return out


def wait_for_run(client: httpx.Client, run_id: str, timeout: float = 900, statuses=("succeeded", "failed", "cancelled")) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        r = client.get(f"/api/runs/{run_id}").json()
        if r["status"] in statuses:
            return r
        time.sleep(0.5)
    raise TimeoutError(f"run {run_id} still {r['status']} after {timeout}s")


def wait_for_event(client: httpx.Client, run_id: str, event_type: str, timeout: float = 180) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        evs = client.get(f"/api/runs/{run_id}/events", params={"format": "json"}).json()["events"]
        hit = next((e for e in evs if e["type"] == event_type), None)
        if hit:
            return hit
        if any(e["type"] in TERMINAL for e in evs):
            raise AssertionError(f"run {run_id} ended before {event_type}: {evs[-1]}")
        time.sleep(0.3)
    raise TimeoutError(f"no {event_type} event for {run_id} in {timeout}s")


def start_run(client: httpx.Client, **body) -> dict:
    r = client.post("/api/runs", json=body)
    assert r.status_code == 201, r.text
    return r.json()

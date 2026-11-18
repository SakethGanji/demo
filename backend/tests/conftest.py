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
    def __init__(self, data_dir: Path) -> None:
        self.data_dir = data_dir
        self.port = _free_port()
        self.proc: subprocess.Popen | None = None
        self.log = data_dir.parent / "server.log"

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def start(self, timeout: float = 90) -> None:
        env = {**os.environ, "ENSEMBLE_DATA_DIR": str(self.data_dir), "PYTHONDONTWRITEBYTECODE": "1"}
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

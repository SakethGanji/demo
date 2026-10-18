"""Shared pieces of the Stage 3B drift / smoke integration tests: the fake risk API subprocess and a Refund-triage-like workflow."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import urllib.request
import uuid
from pathlib import Path

from .conftest import uniq

ENGINE_DIR = Path(__file__).resolve().parents[2]
FIXTURE = ENGINE_DIR / "tests" / "fixtures" / "workflows" / "refund_triage.json"
FAKE_SERVER = ENGINE_DIR / "tests" / "fake_refund_api.py"
COLUMNS = [{"name": "order_id", "type": "string"}, {"name": "amount", "type": "number"},
           {"name": "reason", "type": "string"}, {"name": "status", "type": "string"}]
TABLE_MOCK = {"items": [{"id": 1, "order_id": "o-1", "amount": 900, "reason": "late", "status": "logged"}]}
V1_BODY = {"ok": True, "decision": "review", "received": "{}"}


class FakeApi:
    """tests/fake_refund_api.py as a subprocess on a free port; killed by PID."""

    def __init__(self) -> None:
        self.proc = subprocess.Popen([sys.executable, str(FAKE_SERVER)], stdout=subprocess.PIPE, text=True,
                                     start_new_session=True)
        self.port = json.loads(self.proc.stdout.readline())["port"]  # type: ignore[union-attr]
        self.url = f"http://127.0.0.1:{self.port}"

    def shape(self, version: str) -> None:
        req = urllib.request.Request(f"{self.url}/_shape", method="POST", data=json.dumps({"version": version}).encode(),
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=10) as r:
            assert json.loads(r.read())["version"] == version

    def stop(self) -> None:
        try:
            os.killpg(self.proc.pid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            pass
        self.proc.wait(timeout=10)


def tid() -> str:
    return "t_" + uuid.uuid4().hex[:8]


def big_case(name: str, **kw) -> dict:
    """Workflow test: a 900 refund goes through the Risk API, which is mocked unless the test (or smoke run) says otherwise."""
    return {
        "id": kw.pop("id", None) or tid(), "name": name, "target": {"kind": "workflow"}, "category": kw.pop("category", "normal"),
        "input": {"items": [{"body": {"order_id": "o-1", "amount": 900, "reason": "late"}}]},
        "mocks": kw.pop("mocks", {}), "expect": kw.pop("expect", [{"node": "Risk API", "op": "status", "value": "success"}]), **kw,
    }


def make_refund_workflow(api, fake_url: str, *, tests: list | None = None, risk_mock: dict | None = None) -> tuple[str, str]:
    """(project_id, workflow_id): Refund triage calling ``fake_url``/ok, default mocks that describe the v1 reply."""
    pid = api.post("/projects", {"name": uniq("drift")}).json()["id"]
    assert api.post(f"/projects/{pid}/tables", {"name": "refunds", "columns": COLUMNS}).status_code == 201
    wf = json.loads(FIXTURE.read_text().replace("__REFUND_API_URL__", f"{fake_url}/ok"))
    wf["name"] = uniq("refund")
    wf["settings"] = {}
    for n in wf["nodes"]:
        if n["type"] == "Webhook":
            n["parameters"]["path"] = uniq("refund")
    wf["test_defaults"] = {"mocks": {
        "Risk API": risk_mock or {"http": {"status": 200, "body": V1_BODY}},
        "Log escalated": TABLE_MOCK, "Log approved": TABLE_MOCK}}
    if tests:
        wf["tests"] = tests
    r = api.post(f"/projects/{pid}/workflows", wf)
    assert r.status_code == 201, r.text
    return pid, r.json()["id"]


def real_run(api, wid: str, amount: int = 900) -> dict:
    r = api.post(f"/workflows/{wid}/run", {"input_data": {"order_id": "o-1", "amount": amount, "reason": "late"}})
    assert r.status_code == 200, r.text
    return r.json()

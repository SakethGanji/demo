"""Builders shared by the Stage 8 B4 integration tests: a bank child workflow, parents that call it, small HTTP helpers."""

from __future__ import annotations

import time
from typing import Any

from .conftest import uniq

N = lambda name, type_, params=None, x=0: {"name": name, "type": type_, "parameters": params or {}, "position": {"x": x, "y": 0}}  # noqa: E731
C = lambda a, b, out="main": {"source_node": a, "target_node": b, "source_output": out, "target_input": "main"}  # noqa: E731


def setf(name: str, x: int = 200, **fields: str) -> dict:
    return N(name, "Set", {"mode": "manual", "fields": [{"name": k, "value": v} for k, v in fields.items()], "keepOnlySet": False}, x)


def trigger(*fields: tuple[str, str, bool], name: str = "Inputs") -> dict:
    return N(name, "ExecuteWorkflowTrigger", {"inputSource": "fields", "fields": [
        {"name": n, "type": t, "required": r} for n, t, r in fields]})


def child_body(tag: str = "v1", *fields: tuple[str, str, bool], name: str | None = None, project_id: str | None = None) -> dict:
    """'Refund eligibility': takes the declared fields, adds ``tag`` and a derived ``limit``."""
    declared = fields or (("amount", "number", False),)
    body = {"name": name or uniq("s8b4-child"),
            "nodes": [trigger(*declared), setf("Mark", tag=tag, limit="500")],
            "connections": [C("Inputs", "Mark")], "settings": {}}
    if project_id:
        body["project_id"] = project_id
    return body


def parent_body(child_id: str, version: Any = "latest", *, mode: str = "passThrough", inputs: dict | None = None,
                name: str | None = None, trigger_node: dict | None = None, project_id: str | None = None) -> dict:
    params: dict[str, Any] = {"workflowId": child_id, "inputMode": mode}
    if version is not None:
        params["version"] = version
    if inputs is not None:
        params["inputs"] = inputs
    start = trigger_node or N("Start", "Start")
    body = {"name": name or uniq("s8b4-parent"), "nodes": [start, N("call", "ExecuteWorkflow", params, 200)],
            "connections": [C(start["name"], "call")], "settings": {}}
    if project_id:
        body["project_id"] = project_id
    return body


def make(api, body: dict) -> str:
    r = api.post("/workflows", body)
    assert r.status_code == 201, r.text
    return r.json()["id"]


def publish(api, wid: str, expect: int = 200):
    r = api.post(f"/workflows/{wid}/publish", {})
    assert r.status_code == expect, r.text
    return r


def save(api, wid: str, body: dict) -> None:
    r = api.put(f"/workflows/{wid}", {"nodes": body["nodes"], "connections": body["connections"]})
    assert r.status_code == 200, r.text


def child_rows(db, child_id: str) -> list[dict]:
    rows = db.query("SELECT id, mode, status, parent_execution_id, parent_node_name, depth, root_execution_id, workflow_version_id "
                    "FROM executions WHERE workflow_id = %s ORDER BY start_time, id", (child_id,))
    keys = ("id", "mode", "status", "parent_execution_id", "parent_node_name", "depth", "root_execution_id", "workflow_version_id")
    return [dict(zip(keys, r)) for r in rows]


def wait_for(fn, timeout: float = 15.0, step: float = 0.2):
    """Poll ``fn`` until it returns something truthy (background runs finish after the HTTP reply)."""
    end = time.time() + timeout
    while True:
        out = fn()
        if out or time.time() > end:
            return out
        time.sleep(step)

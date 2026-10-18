"""Shared builders for the Stage 8 tester-1 tests (T1): small workflow factories and HTTP helpers over the real engine."""

from __future__ import annotations

import time
from typing import Any

from ._stage8_b4_support import C, N, child_body, child_rows, make, parent_body, publish, save, setf, trigger, wait_for  # noqa: F401
from .conftest import uniq
from .journeys._helpers import new_project  # noqa: F401


def call_node(name: str, child_id: str, version: Any = "latest", *, x: int = 200, on_error: str | None = None, **params: Any) -> dict:
    p: dict[str, Any] = {"workflowId": child_id, "version": version, "inputMode": params.pop("inputMode", "passThrough")}
    p.update(params)
    node = N(name, "ExecuteWorkflow", p, x)
    if on_error:
        node["on_error"] = on_error
    return node


def chain_workflow(name: str, child_id: str | None, version: Any = "draft", *, project_id: str | None = None, tag: str = "x") -> dict:
    """``Inputs -> call -> child`` (or just ``Inputs -> Mark`` at the end of a chain). Every link is callable."""
    nodes = [trigger(("x", "string", False))]
    conns: list[dict] = []
    if child_id is None:
        nodes.append(setf("Mark", tag=tag))
        conns.append(C("Inputs", "Mark"))
    else:
        nodes.append(call_node("call", child_id, version))
        conns.append(C("Inputs", "call"))
    body: dict[str, Any] = {"name": name, "nodes": nodes, "connections": conns, "settings": {}}
    if project_id:
        body["project_id"] = project_id
    return body


def run(api, wid: str, input_data: dict | None = None, expect: int = 200) -> dict:
    r = api.post(f"/workflows/{wid}/run", {"input_data": input_data} if input_data is not None else None)
    assert r.status_code == expect, r.text
    return r.json()


def check(api, wid: str, definition: dict | None = None) -> list[dict]:
    r = api.post(f"/workflows/{wid}/check", {"definition": definition} if definition else {})
    assert r.status_code == 200, r.text
    return r.json()["findings"]


def codes(findings: list[dict]) -> set[str]:
    return {f["code"] for f in findings}


def get(api, wid: str) -> dict:
    r = api.get(f"/workflows/{wid}")
    assert r.status_code == 200, r.text
    return r.json()


def executions(api, **q) -> list[dict]:
    from urllib.parse import urlencode

    r = api.get("/executions" + (("?" + urlencode(q)) if q else ""))
    assert r.status_code == 200, r.text
    return r.json()


def detail(api, eid: str) -> dict:
    r = api.get(f"/executions/{eid}")
    assert r.status_code == 200, r.text
    return r.json()


def timed(fn):
    t0 = time.monotonic()
    out = fn()
    return out, time.monotonic() - t0


def failing_child(name: str | None = None, *, project_id: str | None = None, threshold: int = 1) -> dict:
    """A child that takes ``n`` and fails (StopAndError) when ``n > threshold``, else adds ``verdict: fine``."""
    body = {"name": name or uniq("s8t1-gate"), "settings": {}, "nodes": [
        trigger(("n", "number", True)),
        N("Gate", "If", {"condition": "{{ $json.n > %d }}" % threshold}, 200),
        N("Boom", "StopAndError", {"errorType": "error", "message": "n too big: {{ $json.n }}"}, 400),
        setf("Ok", 400, verdict="fine")],
        "connections": [C("Inputs", "Gate"), C("Gate", "Boom", "true"), C("Gate", "Ok", "false")]}
    if project_id:
        body["project_id"] = project_id
    return body


def rows_parent(child_id: str, *, mode: str = "each", on_error: str | None = None, with_handler: bool = False,
                version: Any = "latest", name: str | None = None) -> dict:
    """Start -> Split (``body.rows`` into items) -> call (map ``n``) -> After [, Handler on the error port]."""
    nodes = [N("Start", "Start"),
             N("Split", "ItemLists", {"operation": "splitOut", "arrayField": "body.rows", "includeOther": False}, 100),
             call_node("call", child_id, version, x=200, on_error=on_error, inputMode="map",
                       inputs={"n": "{{ $json.n }}"}, mode=mode),
             setf("After", 400, seen="yes")]
    conns = [C("Start", "Split"), C("Split", "call"), C("call", "After")]
    if with_handler:
        nodes.append(setf("Handler", 400, handled="yes"))
        conns.append(C("call", "Handler", "error"))
    return {"name": name or uniq("s8t1-rows"), "nodes": nodes, "connections": conns, "settings": {}}

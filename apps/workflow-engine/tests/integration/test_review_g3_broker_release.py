"""UI-DESIGN-REVIEW.md fixes, group 3 — D5 broker read_only claim + allowed_functions, D8 release-pinned PythonStep."""

from __future__ import annotations

import pytest

from .conftest import uniq
from .journeys._helpers import bearer, needs_bwrap, new_project

pytestmark = pytest.mark.integration


def _token(api, P, **body):
    r = api.post(f"/projects/{P}/broker-tokens", {"environment": "dev", **body})
    assert r.status_code == 201, r.text
    return r.json()


def _call(api, tok, function, parameters, items=None):
    body = {"function": function, "parameters": parameters}
    if items is not None:
        body["items"] = items
    return api.post("/broker/call", body, headers=bearer(tok))


def test_d5_read_only_token_denies_writes_and_opaque_egress(api, upstream):
    P, _ = new_project(api, "d5")
    # audit #1: a read-only GET to a loopback host needs that host allowlisted for the call
    from urllib.parse import urlparse
    api.put(f"/projects/{P}", {"settings": {"egress_allowlist": [urlparse(upstream).hostname]}})
    api.post(f"/projects/{P}/tables", {"name": "t", "columns": [{"name": "a", "type": "string"}]})
    rw = _token(api, P)["token"]
    assert _call(api, rw, "data_table", {"table": "t", "operation": "insert", "data": {"a": "x"}}).status_code == 200
    t = _token(api, P, read_only=True)
    assert t["read_only"] is True
    tok = t["token"]
    denied = [
        ("data_table", {"table": "t", "operation": "insert", "data": {"a": "y"}}),
        ("data_table", {"table": "t", "operation": "update", "rowId": 1, "data": {"a": "z"}}),
        ("data_table", {"table": "t", "operation": "delete", "rowId": 1}),
        ("data_table", {"table": "t", "operation": "upsert", "matchColumns": ["a"], "data": {"a": "x"}}),
        ("data_table", {"table": "t", "operation": "{{ 'insert' }}", "data": {"a": "y"}}),
        ("http_request", {"method": "POST", "url": f"{upstream}/echo"}),
        ("http_request", {"method": "DELETE", "url": f"{upstream}/echo"}),
        ("http_request", {"method": "{{ 'PUT' }}", "url": f"{upstream}/echo"}),
        ("send_email", {"toEmail": "a@example.com", "subject": "s", "text": "t"}),
        ("postgres", {"operation": "executeQuery", "query": "select 1"}),
        ("llm_chat", {"prompt": "hi"}),
    ]
    for fn, params in denied:
        r = _call(api, tok, fn, params)
        assert r.status_code == 403, (fn, params, r.text)
        assert r.json()["error"]["code"] == "read_only", (fn, r.text)
        assert r.json()["error"]["function"] == fn
    r = _call(api, tok, "data_table", {"table": "t", "operation": "insert", "data": {"a": "y"}})
    assert r.json()["error"]["operation"] == "insert"
    # reads still work
    r = _call(api, tok, "data_table", {"table": "t", "operation": "list"})
    assert r.status_code == 200 and [i["a"] for i in r.json()["items"]] == ["x"]
    r = _call(api, tok, "data_table", {"table": "t", "operation": "get", "rowId": r.json()["items"][0]["id"]})
    assert r.status_code == 200
    r = _call(api, tok, "http_request", {"method": "GET", "url": f"{upstream}/echo"})
    assert r.status_code == 200 and r.json()["items"][0]["body"]["method"] == "GET"
    assert _call(api, tok, "http_request", {"url": f"{upstream}/echo"}).status_code == 200   # default method = GET
    assert _call(api, tok, "set", {"mode": "manual", "fields": [{"name": "x", "value": "1"}], "keepOnlySet": True}).status_code == 200
    # the table is unchanged by the denied calls; the denials are in the call log as denied/read_only
    rows = api.get(f"/projects/{P}/tables").json()[0]
    assert rows["row_count"] == 1
    log = api.get(f"/projects/{P}/broker-calls?status=denied").json()
    assert sum(1 for c in log if c["error_code"] == "read_only") == len(denied) + 1  # + the operation check


def test_d5_allowed_functions_are_enforced(api, upstream):
    P, _ = new_project(api, "d5f")
    t = _token(api, P, functions=["set"])
    assert t["allowed_functions"] == ["set"]
    r = _call(api, t["token"], "http_request", {"method": "GET", "url": f"{upstream}/echo"})
    assert r.status_code == 403 and r.json()["error"]["code"] == "function_not_allowed"
    assert _call(api, t["token"], "set", {"mode": "manual", "fields": [], "keepOnlySet": True}).status_code == 200
    r = api.post(f"/projects/{P}/broker-tokens", {"environment": "dev", "functions": ["nope_fn"]})
    assert r.status_code == 422 and r.json()["detail"]["code"] == "unknown_function"
    # audit #8: a dev token without `functions` is unscoped (None = every function-exposed node)
    assert _token(api, P)["allowed_functions"] is None


def test_d5_sandbox_and_agent_tokens_carry_scoped_claims():
    """Run-scoped tokens: sandbox runs get an explicit function set (+ read_only when asked),
    each agent studio-function tool gets a token for exactly its own node."""
    import asyncio
    from types import SimpleNamespace

    from src.broker import tokens as tk
    from src.broker.service import function_index, mint_for_context
    from src.sandbox import service as sbx

    seen = {}

    class FakeBackend:
        def available(self):
            return True, ""

        async def run(self, job):
            import json
            import os
            cfg = json.load(open(os.path.join(job.job_dir, "in", "input.json")))
            c = tk.registry.verify(cfg["token"])
            seen["claims"] = (c.read_only, c.allowed_functions)
            return SimpleNamespace(trace=lambda: {}, output={"ok": True}, timed_out=False)

    class NoSocket:
        def __init__(self, *_):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

    import src.broker.uds_server as uds
    orig_backend, orig_sock = sbx.get_backend, uds.BrokerSocket
    sbx.get_backend, uds.BrokerSocket = (lambda: FakeBackend()), NoSocket
    try:
        from src.engine.node_registry import register_all_nodes
        register_all_nodes()
        asyncio.run(sbx.SandboxService().run(kind="route", project_id="p", environment="dev", execution_id=None,
                                              origin="python_route", payload={}, entry="m:f", modules={}))
        ro, fns = seen["claims"]
        assert ro is False and fns == set(function_index().values())
        asyncio.run(sbx.SandboxService().run(kind="route", project_id="p", environment="dev", execution_id=None,
                                              origin="generate_self_test", payload={}, entry="m:f", modules={},
                                              read_only=True, allowed_functions={"DataTable"}))
        assert seen["claims"] == (True, {"DataTable"})
    finally:
        sbx.get_backend, uds.BrokerSocket = orig_backend, orig_sock

    ctx = SimpleNamespace(extras={}, project_id="p", environment="dev", execution_id="exec_1", team_id="default")
    t1 = mint_for_context(ctx, "agent", allowed_functions={"HttpRequest"})
    t2 = mint_for_context(ctx, "agent", allowed_functions={"Set"})
    assert t1 != t2
    assert tk.registry.verify(t1).allowed_functions == {"HttpRequest"}
    assert tk.registry.verify(t2).allowed_functions == {"Set"}


# ── D8: PythonStep in a release-pinned run uses the release's module source ──


@needs_bwrap
def test_d8_python_step_in_pinned_release_uses_snapshot_module(api):
    P, S = new_project(api, "d8")
    M = api.post(f"/projects/{P}/modules", {"name": "steps", "source": "def calc(items):\n    return [{'v': 2}]\n"}).json()["module"]["id"]
    wf = {"name": uniq("calc"), "nodes": [
        {"name": "Start", "type": "Start", "parameters": {}, "position": {"x": 0, "y": 0}},
        {"name": "Step", "type": "PythonStep", "parameters": {"module": "steps", "function": "calc"}, "position": {"x": 200, "y": 0}}],
        "connections": [{"source_node": "Start", "target_node": "Step", "source_output": "main", "target_input": "main"}]}
    W = api.post(f"/projects/{P}/workflows", wf).json()["id"]
    api.post(f"/projects/{P}/routes", {"method": "GET", "path": "/calc", "handler": {"type": "workflow", "workflow_id": W}})
    r = api.post(f"/projects/{P}/releases", {"environment": "dev"})
    assert r.status_code == 201, r.text
    r = api.post(f"/projects/{P}/releases/{r.json()['id']}/promote", {"to_environment": "prod"})
    assert r.status_code == 201, r.text
    assert api.raw("GET", f"/api/p/{S}/calc").json() == {"v": 2}
    # edit the draft module: prod (pinned) keeps serving the released source, dev sees the draft
    assert api.put(f"/projects/{P}/modules/{M}", {"source": "def calc(items):\n    return [{'v': 3}]\n"}).status_code == 200
    r = api.raw("GET", f"/api/p/{S}/calc")
    assert r.status_code == 200 and r.json() == {"v": 2}, r.text
    rid = api.get(f"/projects/{P}/routes").json()[0]["id"]
    assert api.post(f"/projects/{P}/routes/{rid}/test", {}).json()["body"] == {"v": 3}


def test_d5_virtual_tables_behave_as_empty_and_never_touch_real_rows(api):
    P, _ = new_project(api, "d5v")
    t = _token(api, P, read_only=True, virtual_tables=["refunds"])
    r = _call(api, t["token"], "data_table", {"table": "refunds", "operation": "list"})
    assert r.status_code == 200 and r.json()["items"] == []
    r = _call(api, t["token"], "data_table", {"table": "refunds", "operation": "get", "rowId": 1})
    assert r.status_code == 404 and r.json()["error"]["code"] == "row_not_found"
    r = _call(api, t["token"], "data_table", {"table": "refunds", "operation": "insert", "data": {"a": 1}})
    assert r.status_code == 404 and r.json()["error"]["code"] == "table_not_found"
    assert r.json()["error"]["table"] == "refunds"
    assert api.get(f"/projects/{P}/tables").json() == []
    # a table that is not virtual and does not exist is still the normal error
    r = _call(api, t["token"], "data_table", {"table": "other", "operation": "list"})
    assert r.json()["error"]["code"] == "table_not_found"

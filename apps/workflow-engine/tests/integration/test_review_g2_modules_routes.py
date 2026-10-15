"""UI-DESIGN-REVIEW.md fixes, group 2 — modules, route tests and the spec diff.

B4 optimistic concurrency, C7 route_conflict payload, C8 description preserved, D16 structured
handler errors, G-UI-4 inline trace, G-UI-10 handler.line, B1 table_not_found, D15 Code-node lines,
G-UI-5/C14 SDK reference from one source.
"""

from __future__ import annotations

import pytest

from .conftest import uniq
from .journeys._helpers import chain, needs_bwrap, new_project

pytestmark = pytest.mark.integration

MOD = '''from studio import api
import studio


@api.get("/hello", name="hello")
def hello(req):
    """Says hello."""
    return {"message": "hi"}
'''


def _mod(api, P, src=MOD, name="m"):
    r = api.post(f"/projects/{P}/modules", {"name": name, "source": src})
    assert r.status_code == 201, r.text
    return r.json()


# ── B4: concurrent edits are detected ────────────────────────────────────────


def test_b4_module_put_with_stale_base_is_409_module_changed(api):
    P, _ = new_project(api, "b4")
    m = _mod(api, P)["module"]
    M, sha0, at0 = m["id"], m["source_sha"], m["updated_at"]
    src1 = MOD.replace('"hi"', '"one"')
    r = api.put(f"/projects/{P}/modules/{M}", {"source": src1, "base_source_sha": sha0})
    assert r.status_code == 200, r.text
    sha1 = r.json()["module"]["source_sha"]
    # a second tab still holding sha0 must not overwrite src1
    r = api.put(f"/projects/{P}/modules/{M}", {"source": MOD.replace('"hi"', '"two"'), "base_source_sha": sha0})
    assert r.status_code == 409, r.text
    d = r.json()["detail"]
    assert d["code"] == "module_changed" and d["current"]["source_sha"] == sha1 and d["current"]["updated_at"]
    r = api.put(f"/projects/{P}/modules/{M}", {"source": MOD, "base_updated_at": at0})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "module_changed"
    assert api.get(f"/projects/{P}/modules/{M}").json()["source"] == src1
    # dry runs report the conflict too, and no base = last write wins (compatible)
    r = api.put(f"/projects/{P}/modules/{M}?dry_run=true", {"source": MOD, "base_source_sha": sha0})
    assert r.status_code == 409
    assert api.put(f"/projects/{P}/modules/{M}", {"source": MOD}).status_code == 200
    # GET exposes the sha as an ETag; If-Match works like base_source_sha
    g = api.get(f"/projects/{P}/modules/{M}")
    assert g.headers["etag"] == f'"{g.json()["source_sha"]}"'
    assert api.put(f"/projects/{P}/modules/{M}", {"source": src1}, headers={"If-Match": f'"{sha1}"'}).status_code == 409
    assert api.put(f"/projects/{P}/modules/{M}", {"source": src1}, headers={"If-Match": g.headers["etag"]}).status_code == 200


# ── C7: route_conflict says who owns the route, and where ────────────────────


def test_c7_route_conflict_payload_names_handler_and_line(api):
    P, _ = new_project(api, "c7")
    wf = chain("Create order", {"name": "Set", "type": "Set", "position": {"x": 1, "y": 1},
                                "parameters": {"mode": "manual", "fields": [], "keepOnlySet": True}})
    W = api.post(f"/projects/{P}/workflows", wf).json()["id"]
    rid = api.post(f"/projects/{P}/routes", {"method": "POST", "path": "/orders",
                                             "handler": {"type": "workflow", "workflow_id": W}}).json()["route"]["id"]
    src = 'from studio import api\n\n\n@api.post("/orders")\ndef create(req):\n    return {}\n'
    r = api.post(f"/projects/{P}/modules", {"name": "orders", "source": src})
    assert r.status_code == 409, r.text
    d = r.json()["detail"]
    assert d["code"] == "route_conflict" and d["line"] == 4 and d["errors"][0]["line"] == 4
    assert d["existing_route_id"] == rid
    assert d["handler"] == {"type": "workflow", "id": W, "name": "Create order"}
    # same-module duplicate keeps its line; another module's route names that module
    _mod(api, P, 'from studio import api\n@api.get("/a")\ndef a(req):\n    return {}\n', name="first")
    r = api.post(f"/projects/{P}/modules", {"name": "second", "source": 'from studio import api\n\n@api.get("/a")\ndef a2(req):\n    return {}\n'})
    d = r.json()["detail"]
    assert r.status_code == 409 and d["line"] == 3 and d["handler"]["type"] == "python"
    assert d["handler"]["name"] == "first" and d["handler"]["function"] == "a"
    # name clash with an existing operation → 409 name_taken with the line
    r = api.post(f"/projects/{P}/modules", {"name": "third", "source": 'from studio import api\n\n\n\n@api.get("/b", name="getA")\ndef b(req):\n    return {}\n'})
    d = r.json()["detail"]
    assert r.status_code == 409 and d["code"] == "name_taken" and d["line"] == 5 and d["handler"]["type"] == "python"


# ── C8: module save keeps route-level fields the decorator doesn't own ───────


def test_c8_module_save_preserves_route_description_and_schema(api):
    P, _ = new_project(api, "c8")
    M = _mod(api, P)["module"]["id"]
    route = api.get(f"/projects/{P}/routes").json()[0]
    assert route["description"] == "Says hello."
    schema = {"type": "object", "properties": {"message": {"type": "string"}}}
    r = api.put(f"/projects/{P}/routes/{route['id']}", {"description": "Edited in route settings", "response_schema": schema,
                                                         "timeout_s": 7})
    assert r.status_code == 200, r.text
    assert api.put(f"/projects/{P}/modules/{M}", {"source": MOD.replace('"hi"', '"hey"')}).status_code == 200
    after = api.get(f"/projects/{P}/routes/{route['id']}").json()
    assert after["description"] == "Edited in route settings"
    assert after["response_schema"] == schema and after["timeout_s"] == 7
    # an explicit description= in the decorator is owned by the code and wins
    src = MOD.replace('@api.get("/hello", name="hello")', '@api.get("/hello", name="hello", description="From code")')
    assert api.put(f"/projects/{P}/modules/{M}", {"source": src}).status_code == 200
    assert api.get(f"/projects/{P}/routes/{route['id']}").json()["description"] == "From code"


# ── G-UI-10: python routes know their decorator line ────────────────────────


def test_g_ui_10_python_route_handler_line(api):
    P, _ = new_project(api, "gl")
    _mod(api, P)
    route = api.get(f"/projects/{P}/routes").json()[0]
    assert route["handler"]["type"] == "python" and route["handler"]["line"] == 5
    assert api.get(f"/projects/{P}/routes/{route['id']}").json()["handler"]["line"] == 5


# ── D16 / G-UI-4 / B1: /test reports structured errors and the trace inline ──

BOOM = '''from studio import api
import studio

@api.get("/boom")
def boom(req):
    x = 1
    raise ValueError("kaput")


@api.post("/refunds")
def create_refund(req):
    return studio.db.table("refunds").insert({"reason": req.body["reason"]})
'''


@needs_bwrap
def test_d16_g_ui_4_b1_route_test_error_trace_and_table_not_found(api):
    P, _ = new_project(api, "d16")
    _mod(api, P, BOOM, name="refunds")
    routes = {r["path"]: r["id"] for r in api.get(f"/projects/{P}/routes").json()}
    r = api.post(f"/projects/{P}/routes/{routes['/boom']}/test", {})
    assert r.status_code == 200, r.text
    b = r.json()
    assert b["status"] == 502 and b["body"]["error"] == "handler_failed"
    err = b["error"]
    assert err["type"] == "ValueError" and err["message"] == "kaput"
    assert err["line"] == 7 and err["module"] == "refunds"
    assert "/modules/refunds.py" in err["traceback"]
    assert b["trace"]["sandbox"]["exit_code"] == 0 and isinstance(b["trace"]["broker_calls"], list)
    assert b["trace"] == api.get(f"/projects/{P}/runs/{b['execution_id']}").json()["trace"]

    # missing table → a distinct, actionable error code (UI offers "Create table")
    r = api.post(f"/projects/{P}/routes/{routes['/refunds']}/test", {"body": {"reason": "damaged"}}).json()
    assert r["status"] == 502 and r["body"]["error_code"] == "table_not_found"
    assert r["error"]["code"] == "table_not_found" and r["error"]["details"]["table"] == "refunds"
    calls = r["trace"]["broker_calls"]
    assert len(calls) == 1 and calls[0]["error_code"] == "table_not_found"
    api.post(f"/projects/{P}/tables", {"name": "refunds", "columns": [{"name": "reason", "type": "string"}]})
    r = api.post(f"/projects/{P}/routes/{routes['/refunds']}/test", {"body": {"reason": "damaged"}}).json()
    assert r["status"] == 200 and r["body"]["reason"] == "damaged" and r["error"] is None


def test_b1_canvas_data_table_missing_table_has_error_code(api):
    P, S = new_project(api, "b1c")
    wf = chain(uniq("dt"), {"name": "DT", "type": "DataTable", "position": {"x": 1, "y": 1},
                            "parameters": {"table": "nope", "operation": "list"}})
    W = api.post(f"/projects/{P}/workflows", wf).json()["id"]
    run = api.post(f"/workflows/{W}/run").json()
    assert run["status"] == "failed" and run["errors"][0]["error_code"] == "table_not_found"
    assert run["errors"][0]["details"] == {"table": "nope"}
    rid = api.post(f"/projects/{P}/routes", {"method": "GET", "path": "/dt", "handler": {"type": "workflow", "workflow_id": W}}).json()["route"]["id"]
    r = api.raw("GET", f"/api/p/{S}/dt")
    assert r.status_code == 502 and r.json()["error_code"] == "table_not_found"
    t = api.post(f"/projects/{P}/routes/{rid}/test", {}).json()
    assert t["error"]["code"] == "table_not_found" and t["error"]["node"] == "DT"


# ── D15: Code-node line numbers are the user's ───────────────────────────────


@needs_bwrap
def test_d15_code_node_syntax_and_runtime_lines_match_user_code(api):
    P, _ = new_project(api, "d15")
    code = "x = 1\ny = (\nreturn items"
    W = api.post(f"/projects/{P}/workflows", chain("c", {"name": "Code", "type": "Code", "parameters": {"code": code},
                                                        "position": {"x": 1, "y": 1}})).json()["id"]
    run = api.post(f"/workflows/{W}/run").json()
    assert run["status"] == "failed" and run["errors"][0]["error_code"] == "syntax_error"
    assert run["errors"][0]["details"]["line"] == 2, run["errors"][0]
    assert "Line 2:" in run["errors"][0]["error"]
    code = "a = 1\nb = 2\nraise ValueError('nope')\nreturn items"
    W = api.post(f"/projects/{P}/workflows", chain("c", {"name": "Code", "type": "Code", "parameters": {"code": code},
                                                        "position": {"x": 1, "y": 1}})).json()["id"]
    run = api.post(f"/workflows/{W}/run").json()
    assert run["errors"][0]["error_code"] == "code_error" and run["errors"][0]["details"]["line"] == 3, run["errors"][0]


def test_d15_legacy_in_process_code_node_line_numbers():
    """Same rule for the in-process fallback (no sandbox)."""
    import asyncio

    from src.engine.types import ExecutionContext, NodeData, NodeDefinition
    from src.nodes.data.code import CodeNode

    node = CodeNode()
    ctx = ExecutionContext(workflow=None, execution_id="exec_t", start_time=__import__("datetime").datetime.now(), mode="manual")
    with pytest.raises(Exception) as ei:
        asyncio.run(node._execute_in_process(ctx, NodeDefinition(name="Code", type="Code", parameters={"code": "x = 1\ny = (\n"}),
                                         [NodeData(json={})]))
    assert "Line 2:" in str(ei.value)


# ── G-UI-5 / C14: one source for the SDK reference ──────────────────────────


def test_g_ui_5_sdk_functions_have_python_params(api):
    cat = api.get("/sdk/functions").json()
    http = next(c for c in cat if c["function"] == "http_request")
    py = http["python"]
    names = [p["name"] for p in py["params"]]
    assert "url" in names and "method" in names
    method = next(p for p in py["params"] if p["name"] == "method")
    assert method["default"] == "GET" and "POST" in (method["enum"] or [])
    url = next(p for p in py["params"] if p["name"] == "url")
    assert url["required"] is True and url["property"] == "url"
    assert py["signature"].startswith("http_request(*, ") and "url: str" in py["signature"]


def test_c14_sdk_static_reference_comes_from_the_sdk_source(api):
    import inspect

    from src.sdk.static import api as sdk_api

    ref = api.get("/sdk/reference").json()
    by = {e["name"]: e for e in ref["entries"]}
    assert [p["name"] for p in by["Response"]["params"]] == list(inspect.signature(sdk_api.Response.__init__).parameters)[1:]
    assert by["Response"]["signature"].startswith("Response(status: int = 200, body: Any = None, headers")
    assert by["HTTPError"]["signature"].startswith("HTTPError(status: int, body: Any = None")
    assert by["api.get"]["signature"].startswith('api.get(path: str, *, auth: str = "none", name: str | None = None')
    assert set(by["api.get"]["auth_values"]) == {"none", "app_token", "api_key"}
    assert "studio.secret" in by and "studio.db.table" in by and "Request" in by
    assert "studio.secret('K')" in ref["rules"]["secrets"] and "f-string" in ref["rules"]["secrets"]
    assert "literal" in ref["rules"]["decorators"]
    assert ref["version"] == api.get("/sdk/python").json()["version"]

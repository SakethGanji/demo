"""Journey J16 (UI-INTEGRATION.md §6): Python modules + sandbox trace [bwrap]; plus phase-8 escape probes."""

from __future__ import annotations

import pytest

from ._helpers import chain, needs_bwrap, new_project

pytestmark = [pytest.mark.integration, needs_bwrap]

SRC1 = '''from studio import api, Response, HTTPError
import studio

@api.get("/py/orders/{id}", auth="none", name="getPyOrder")
def get_order(req):
    if req.params["id"] == "404":
        raise HTTPError(404, {"error": "not found"})
    return {"id": req.params["id"], "via": "python"}

@api.post("/py/orders", auth="none", name="createPyOrder")
def create_order(req):
    key = studio.secret("PYKEY")
    return Response(status=201, body={"ok": True})
'''
SRC_NO_POST = SRC1[: SRC1.index('@api.post')]


def test_j16_python_modules_and_trace(api):
    st = api.get("/sandbox/status").json()                                                     # 1
    assert st["available"] is True and st["backend"] == "bwrap"
    P, S = new_project(api, "j16")                                                             # 2
    r = api.post(f"/projects/{P}/modules", {"name": "orders", "source": SRC1})
    assert r.status_code == 201, r.text
    b = r.json()
    assert b["module"]["id"].startswith("mod_") and len(b["routes"]["created"]) == 2
    assert b["routes"]["updated"] == [] and b["routes"]["removed"] == [] and b["errors"] == []
    M = b["module"]["id"]
    assert any(m["id"] == M and m["name"] == "orders" for m in api.get(f"/projects/{P}/modules").json())  # 3
    assert api.get(f"/projects/{P}/modules/{M}").json()["source"] == SRC1
    routes = api.get(f"/projects/{P}/routes").json()                                           # 4
    py = next(r for r in routes if r["name"] == "getPyOrder")
    assert py["source"] == "code" and py["handler"] == {"type": "python", "module_id": M, "function": "get_order"}
    r = api.raw("GET", f"/api/p/{S}/py/orders/7")                                              # 5
    assert r.status_code == 200 and r.json() == {"id": "7", "via": "python"}
    XP = r.headers["x-execution-id"]
    r = api.raw("GET", f"/api/p/{S}/py/orders/404")
    assert r.status_code == 404 and r.json() == {"error": "not found"}
    r = api.raw("POST", f"/api/p/{S}/py/orders")
    assert r.status_code == 201 and r.json() == {"ok": True}
    tr = api.get(f"/projects/{P}/runs/{XP}").json()["trace"]                                   # 6
    assert tr["sandbox"]["exit_code"] == 0 and tr["sandbox"]["truncated"] is False and isinstance(tr["broker_calls"], list)
    m = next(x for x in api.get(f"/projects/{P}/secrets/missing").json() if x["key"] == "PYKEY")  # 7
    assert any(ref["kind"] == "module" and ref["id"] == M and ref["name"] == "orders" for ref in m["referenced_by"])
    r = api.put(f"/projects/{P}/modules/{M}", {"source": SRC1 + "\ndef broken(:\n"})          # 8
    assert r.status_code == 422 and r.json()["detail"]["code"] == "syntax_error"
    assert isinstance(r.json()["detail"]["errors"][0]["line"], int)
    assert api.get(f"/projects/{P}/modules/{M}").json()["source"] == SRC1
    r = api.put(f"/projects/{P}/modules/{M}", {"source": SRC_NO_POST})                         # 9
    assert r.status_code == 200 and [x["name"] for x in r.json()["routes"]["removed"]] == ["createPyOrder"]
    assert api.raw("POST", f"/api/p/{S}/py/orders").status_code in (404, 405)
    assert api.post(f"/projects/{P}/routes", {"method": "GET", "path": "/ui/{id}", "handler": {"type": "stub"}}).status_code == 201  # 10
    clash = SRC_NO_POST + '\n@api.get("/ui/{x}")\ndef ui(req):\n    return {}\n'
    r = api.put(f"/projects/{P}/modules/{M}", {"source": clash})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "route_conflict"
    assert api.get(f"/projects/{P}/modules/{M}").json()["source"] == SRC_NO_POST
    r = api.post(f"/projects/{P}/routes/{py['id']}/test", {"params": {"id": "9"}, "query": {}, "headers": {}, "body": None,  # 11
                                                          "environment": "dev", "apply_schemas": False})
    assert r.status_code == 200 and r.json()["status"] == 200 and r.json()["body"]["via"] == "python"
    r = api.delete(f"/projects/{P}/modules/{M}")                                               # 12
    assert r.status_code == 200 and any(x["name"] == "getPyOrder" for x in r.json()["routes"]["removed"])
    assert not any(x["source"] == "code" for x in api.get(f"/projects/{P}/routes").json())


PROBE = '''from studio import api
import os, socket, studio

@api.get("/probe")
def probe(req):
    out = {}
    try:
        socket.create_connection(("1.1.1.1", 80), timeout=1); out["net"] = "open"
    except Exception:
        out["net"] = "blocked"
    try:
        socket.create_connection(("127.0.0.1", int(req.query["port"])), timeout=1); out["loopback"] = "open"
    except Exception:
        out["loopback"] = "blocked"
    out["env"] = sorted(k for k in os.environ if k.startswith("WORKFLOW"))
    out["home"] = os.path.exists("/home")
    out["dotenv"] = os.path.exists(req.query["dotenv"])
    out["etc_passwd"] = os.path.exists("/etc/passwd")
    out["rows"] = studio.nodes.set(mode="manual", fields=[{"name": "x", "value": "1"}], keep_only_set=True)
    return out

@api.get("/hog")
def hog(req):
    x = bytearray(2 * 1024 * 1024 * 1024)
    return {"n": len(x)}

@api.get("/spin")
def spin(req):
    while True:
        pass

@api.get("/boom")
def boom(req):
    raise ValueError("kaput")
'''


def test_phase8_sandbox_escape_probes_and_limits(api, upstream):
    import pathlib
    P, S = new_project(api, "p8")
    dotenv = str(pathlib.Path(__file__).resolve().parents[3] / ".env.example")
    assert api.post(f"/projects/{P}/modules", {"name": "probe", "source": PROBE}).status_code == 201
    port = upstream.rsplit(":", 1)[1]
    r = api.raw("GET", f"/api/p/{S}/probe?port={port}&dotenv={dotenv}")
    assert r.status_code == 200, r.text
    o = r.json()
    assert o["net"] == "blocked" and o["loopback"] == "blocked"
    assert o["env"] == [] and o["home"] is False and o["dotenv"] is False and o["etc_passwd"] is False
    assert o["rows"] == [{"x": "1"}]                       # broker over the Unix socket works
    xid = r.headers["x-execution-id"]
    calls = api.get(f"/projects/{P}/runs/{xid}").json()["trace"]["broker_calls"]
    assert len(calls) == 1 and calls[0]["origin"] == "python_route"
    assert api.raw("GET", f"/api/p/{S}/hog").status_code == 502          # memory limit
    r = api.raw("GET", f"/api/p/{S}/boom")
    assert r.status_code == 502 and "kaput" in r.json()["message"]
    rid = next(x["id"] for x in api.get(f"/projects/{P}/routes").json() if x["path"] == "/spin")
    api.put(f"/projects/{P}/routes/{rid}", {"timeout_s": 2})
    r = api.raw("GET", f"/api/p/{S}/spin")
    assert r.status_code == 504 and r.json()["error"] == "timeout"


def test_phase8_python_step_and_sandboxed_code_node(api):
    P, S = new_project(api, "p8b")
    api.post(f"/projects/{P}/modules", {"name": "steps", "source": "def double(items):\n    return [{'v': i['v'] * 2} for i in items]\n"})
    step = {"name": "Step", "type": "PythonStep", "parameters": {"module": "steps", "function": "double"}, "position": {"x": 200, "y": 0}}
    set_node = {"name": "Seed", "type": "Set", "parameters": {"mode": "manual", "fields": [{"name": "v", "value": "{{ 21 }}"}], "keepOnlySet": True},
                "position": {"x": 100, "y": 0}}
    wf = {"name": "step", "nodes": [{"name": "Start", "type": "Start", "parameters": {}, "position": {"x": 0, "y": 0}}, set_node, step],
          "connections": [{"source_node": "Start", "target_node": "Seed", "source_output": "main", "target_input": "main"},
                          {"source_node": "Seed", "target_node": "Step", "source_output": "main", "target_input": "main"}]}
    W = api.post(f"/projects/{P}/workflows", wf).json()["id"]
    run = api.post(f"/workflows/{W}/run").json()
    assert run["status"] == "success", run
    assert run["data"]["Step"] == [{"json": {"v": 42}}]
    det = api.get(f"/projects/{P}/runs/{run['execution_id']}").json()
    assert det["node_metrics"]["Step"]["sandboxed"] is True
    # Code node now runs in the sandbox: no host files, full builtins, same globals
    code = "import os\nreturn [{'json': {'home': os.path.exists('/home'), 'n': len(items), 'sum': sum(range(4))}}]"
    W2 = api.post(f"/projects/{P}/workflows", chain("c", {"name": "Code", "type": "Code", "parameters": {"code": code},
                                                           "position": {"x": 200, "y": 0}})).json()["id"]
    run = api.post(f"/workflows/{W2}/run").json()
    assert run["data"]["Code"] == [{"json": {"home": False, "n": 1, "sum": 6}}]
    assert api.get(f"/projects/{P}/runs/{run['execution_id']}").json()["node_metrics"]["Code"]["sandboxed"] is True
    # Syntax errors keep their message shape
    W3 = api.post(f"/projects/{P}/workflows", chain("c", {"name": "Code", "type": "Code", "parameters": {"code": "return ("},
                                                           "position": {"x": 200, "y": 0}})).json()["id"]
    run = api.post(f"/workflows/{W3}/run").json()
    assert run["status"] == "failed" and "Syntax Error in code" in run["errors"][0]["error"]
    assert run["errors"][0]["error_code"] == "syntax_error"

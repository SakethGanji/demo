"""UI-DESIGN §10.2 — POST /api/projects/{project}/modules/run-draft (unsaved source, one request, sandbox)."""

from __future__ import annotations

from urllib.parse import urlparse

import pytest

from .conftest import FAKE_LLM_TOKEN, _EchoHandler, uniq
from .journeys._helpers import needs_bwrap, new_project

pytestmark = pytest.mark.integration

SECRET = "pay-tok-" + uniq("v")


def refunds_src(upstream: str) -> str:
    return f'''from studio import api, HTTPError, Response
import studio


@api.post("/refunds", name="createRefund")
def create_refund(req):
    body = req.body or {{}}
    return studio.db.table("refunds").insert({{"reason": body["reason"], "amount": body["amount"]}})


@api.get("/refunds/stats", name="refundStats")
def refund_stats(req):
    rows = studio.db.table("refunds").list()
    auth = f"Bearer {{studio.secret('PAYMENTS_TOKEN')}}"
    bal = studio.nodes.http_request(method="GET", url="{upstream}/v1/balance",
                                    headers=[{{"name": "Authorization", "value": auth}}])
    return {{"count": len(rows), "available": bal[0]["body"]["available"]}}


@api.get("/refunds/{{rid}}", name="getRefund", auth="api_key")
def get_refund(req):
    return {{"rid": req.params["rid"], "headers": sorted(req.headers), "query": req.query}}


@api.get("/leak")
def leak(req):
    auth = f"Bearer {{studio.secret('PAYMENTS_TOKEN')}}"
    echo = studio.nodes.http_request(method="GET", url="{upstream}/echo",
                                     headers=[{{"name": "Authorization", "value": auth}}])
    seen = echo[0]["body"]["headers"]["authorization"]
    return Response(status=200, body={{"seen": seen}}, headers={{"X-Seen": seen}})


@api.post("/echo")
def echo(req):
    return {{"body": req.body}}


@api.get("/boom")
def boom(req):
    x = 1
    raise ValueError("kaput")
'''


def _project(api, upstream, prefix="rd"):
    P, S = new_project(api, prefix)
    host = urlparse(upstream).hostname
    r = api.post(f"/projects/{P}/secrets", {"key": "PAYMENTS_TOKEN", "value": SECRET, "environment": "dev",
                                            "allowed_hosts": [host]})
    assert r.status_code == 201, r.text
    return P, S


def _draft(api, P, method, path, src, **extra):
    body = {"source": src, "module_name": "refunds", "module_id": None, "method": method, "path": path,
            "query": {}, "headers": {}, "body": None, "virtual_tables": [], **extra}
    return api.post(f"/projects/{P}/modules/run-draft", body)


@needs_bwrap
def test_run_draft_get_with_virtual_table_secret_trace_and_run_row(api, upstream):
    P, _ = _project(api, upstream)
    src = refunds_src(upstream)
    _EchoHandler.seen.clear()
    r = _draft(api, P, "GET", "/refunds/stats", src, virtual_tables=["refunds"],
               headers={"Authorization": "Bearer sk_nope", "Cookie": "a=b", "X-Api-Key": "k", "X-Trace": "t1"})
    assert r.status_code == 200, r.text
    b = r.json()
    assert set(b) == {"status", "headers", "body", "duration_ms", "execution_id", "route", "trace", "error", "inferred"}
    assert b["status"] == 200 and b["body"] == {"count": 0, "available": 12840.25} and b["error"] is None
    assert b["route"] == {"method": "GET", "path": "/refunds/stats", "name": "refundStats", "function": "refund_stats",
                          "line": 11}
    assert isinstance(b["duration_ms"], float) and b["execution_id"].startswith("exec_")
    sb = b["trace"]["sandbox"]
    assert set(sb) >= {"duration_ms", "exit_code", "stdout", "stderr", "truncated", "timed_out"} and sb["exit_code"] == 0
    calls = b["trace"]["broker_calls"]
    assert sorted(c["node_type"] for c in calls) == ["DataTable", "HttpRequest"] and {c["status"] for c in calls} == {"ok"}
    assert b["inferred"]["response_schema"]["type"] == "object"
    # the upstream saw the real secret; nothing in the response carries it
    bal = [s for s in _EchoHandler.seen if s["path"] == "/v1/balance"]
    assert bal and bal[-1]["headers"]["authorization"] == f"Bearer {SECRET}"
    assert SECRET not in r.text
    # the execution row
    run = api.get(f"/projects/{P}/runs/{b['execution_id']}").json()
    assert run["mode"] == "sandbox" and run["environment"] == "dev" and run["status"] == "success"
    assert run["trigger"]["type"] == "draft_test" and run["trigger"]["module_name"] == "refunds"
    assert run["trigger"]["route"] == "GET /refunds/stats" and run["route"] == "GET /refunds/stats"
    req = run["metadata"]["request"]
    assert req["method"] == "GET" and req["path"] == "/refunds/stats"
    assert "authorization" not in req["headers"] and "cookie" not in req["headers"] and "x-api-key" not in req["headers"]
    assert req["headers"]["x-trace"] == "t1"
    assert run["metadata"]["response"]["status"] == 200
    assert run["trace"] == b["trace"]
    # nothing was saved, no table was created
    assert api.get(f"/projects/{P}/modules").json() == []
    assert api.get(f"/projects/{P}/routes").json() == []
    assert not any(t["name"] == "refunds" for t in api.get(f"/projects/{P}/tables").json())
    assert any(x["id"] == b["execution_id"] for x in api.get(f"/projects/{P}/runs?trigger=draft_test").json())


@needs_bwrap
def test_run_draft_routing_params_auth_not_enforced_headers_stripped(api, upstream):
    P, _ = _project(api, upstream)
    src = refunds_src(upstream)
    r = _draft(api, P, "GET", "/refunds/42", src, query={"since": "2026-10-01"},
               headers={"authorization": "Bearer x", "x-other": "1"})
    b = r.json()
    assert r.status_code == 200 and b["status"] == 200, r.text
    # auth="api_key" is not enforced for drafts, credentials are stripped, params come from the matcher
    assert b["body"] == {"rid": "42", "headers": ["x-other"], "query": {"since": "2026-10-01"}}
    assert b["route"]["name"] == "getRefund" and b["route"]["path"] == "/refunds/{rid}"
    # $vars in the payload are not expanded
    r = _draft(api, P, "POST", "/echo", src, body={"x": "{{ $vars.PAYMENTS_TOKEN }}"})
    assert r.json()["body"] == {"body": {"x": "{{ $vars.PAYMENTS_TOKEN }}"}}


@needs_bwrap
def test_run_draft_writes_are_real_and_missing_tables_report_table_not_found(api, upstream):
    P, _ = _project(api, upstream)
    src = refunds_src(upstream)
    r = _draft(api, P, "POST", "/refunds", src, body={"reason": "damaged", "amount": 84.5}, virtual_tables=["refunds"])
    b = r.json()
    assert r.status_code == 200 and b["status"] == 502, r.text
    assert b["error"]["code"] == "table_not_found" and b["error"]["table"] == "refunds"
    assert api.post(f"/projects/{P}/tables", {"name": "refunds", "columns": [
        {"name": "reason", "type": "string"}, {"name": "amount", "type": "number"}]}).status_code == 201
    r = _draft(api, P, "POST", "/refunds", src, body={"reason": "damaged", "amount": 84.5}, virtual_tables=["refunds"])
    b = r.json()
    assert b["status"] == 200 and b["body"]["reason"] == "damaged" and b["error"] is None
    assert api.get(f"/projects/{P}/tables").json()[0]["row_count"] == 1   # the write is real
    # an existing table listed as virtual is NOT virtual
    r = _draft(api, P, "GET", "/refunds/stats", src, virtual_tables=["refunds"])
    assert r.json()["body"]["count"] == 1


@needs_bwrap
def test_run_draft_structured_error_redaction_and_other_modules_as_saved(api, upstream):
    P, _ = _project(api, upstream)
    src = refunds_src(upstream)
    r = _draft(api, P, "GET", "/boom", src).json()
    assert r["status"] == 502 and r["body"]["error"] == "handler_failed"
    e = r["error"]
    assert e["type"] == "ValueError" and e["message"] == "kaput" and e["line"] == 42 and e["module"] == "refunds"
    assert e["code"] == "handler_failed" and "refunds.py" in e["traceback"]
    # D4: a secret echoed back by an upstream is redacted in body and headers
    r = _draft(api, P, "GET", "/leak", src)
    assert r.status_code == 200 and SECRET not in r.text, r.text
    assert r.json()["body"] == {"seen": "Bearer ***"} and r.json()["headers"]["X-Seen"] == "Bearer ***"
    run = api.get(f"/projects/{P}/runs/{r.json()['execution_id']}").text
    assert SECRET not in run
    # other modules are loaded as SAVED; the draft replaces only its own module
    helper = "VALUE = 'saved'\n"
    assert api.post(f"/projects/{P}/modules", {"name": "helpers", "source": helper}).status_code == 201
    mine = 'from studio import api\nimport helpers\n\n@api.get("/h")\ndef h(req):\n    return {"v": helpers.VALUE}\n'
    saved = api.post(f"/projects/{P}/modules", {"name": "refunds", "source": mine.replace("/h", "/h_old")}).json()
    r = _draft(api, P, "GET", "/h", mine, module_id=saved["module"]["id"]).json()
    assert r["status"] == 200 and r["body"] == {"v": "saved"}
    # the saved module itself is untouched
    assert "/h_old" in api.get(f"/projects/{P}/modules/{saved['module']['id']}").json()["source"]


@needs_bwrap
def test_run_draft_timeout_is_http_200_with_inner_504(api, upstream):
    P, _ = new_project(api, "rdt")
    slow = 'from studio import api\nimport time\n\n@api.get("/slow")\ndef slow(req):\n    time.sleep(30)\n    return {}\n'
    m = api.post(f"/projects/{P}/modules", {"name": "slowmod", "source": slow}).json()
    rid = m["routes"]["created"][0]["id"]
    r = api.put(f"/projects/{P}/routes/{rid}", {"timeout_s": 2})
    assert r.status_code == 200, r.text
    r = api.post(f"/projects/{P}/modules/run-draft", {"source": slow, "module_name": "slowmod",
                                                     "module_id": m["module"]["id"], "method": "GET", "path": "/slow"})
    assert r.status_code == 200, r.text
    b = r.json()
    assert b["status"] == 504 and b["error"]["code"] == "timeout" and b["trace"]["sandbox"]["timed_out"] is True
    assert b["duration_ms"] < 15000


def test_run_draft_http_errors(api, upstream):
    P, _ = new_project(api, "rde")
    ok = 'from studio import api\n\n@api.get("/a")\ndef a(req):\n    return {}\n\n@api.post("/b/{x}")\ndef b(req):\n    return {}\n'
    base = {"source": ok, "module_name": "m", "method": "GET", "path": "/a"}
    post = lambda body, proj=P: api.post(f"/projects/{proj}/modules/run-draft", body)  # noqa: E731
    r = post(base, "proj_nope_" + uniq())
    assert r.status_code == 404 and r.json()["detail"]["code"] == "project_not_found"
    r = post({**base, "source": "def x(:\n"})
    assert r.status_code == 422 and r.json()["detail"]["code"] == "syntax_error"
    assert r.json()["detail"]["errors"][0]["line"] == 1
    r = post({**base, "source": 'from studio import api\nA="none"\n@api.get("/a", auth=A)\ndef a(req):\n    return {}\n'})
    assert r.status_code == 422 and r.json()["detail"]["code"] == "invalid_decorator"
    r = post({**base, "source": ok + '\n@api.get("/a")\ndef a2(req):\n    return {}\n'})
    assert r.status_code == 422 and r.json()["detail"]["code"] == "invalid_decorator"
    for name in ("Bad-Name", "generate", "studio", "1x"):
        r = post({**base, "module_name": name})
        assert r.status_code == 422 and r.json()["detail"]["code"] == "invalid_module_name", name
    for bad in ({**base, "extra": 1}, {**base, "source": "#" * 200_001}, {k: v for k, v in base.items() if k != "path"},
                {**base, "method": "TRACE"}, {**base, "virtual_tables": "refunds"}):
        r = post(bad)
        assert r.status_code == 422 and r.json()["detail"]["code"] == "invalid_request", (r.text[:300])
    r = post({**base, "method": "GET", "path": "/zzz"})
    assert r.status_code == 404
    d = r.json()["detail"]
    assert d["code"] == "route_not_in_source" and d["method"] == "GET" and d["path"] == "/zzz"
    assert d["available"] == ["GET /a", "POST /b/{x}"]
    r = post({**base, "method": "GET", "path": "/b/1"})
    assert r.status_code == 404 and r.json()["detail"]["code"] == "route_not_in_source"
    r = post({**base, "body": {"blob": "x" * (1024 * 1024 + 10)}})
    assert r.status_code == 413 and r.json()["detail"]["code"] == "payload_too_large"
    r = post({**base, "module_id": "mod_nope"})
    assert r.status_code == 404 and r.json()["detail"]["code"] == "module_not_found"


def test_run_draft_sandbox_unavailable(fake_api):
    P, _ = new_project(fake_api, "rdsu")
    tok = {"X-Test-Token": FAKE_LLM_TOKEN}
    ok = 'from studio import api\n\n@api.get("/a")\ndef a(req):\n    return {}\n'
    try:
        assert fake_api.raw("PUT", "/api/_test/sandbox", headers=tok, json={"available": False}).status_code == 200
        r = fake_api.post(f"/projects/{P}/modules/run-draft", {"source": ok, "module_name": "m", "method": "GET", "path": "/a"})
        assert r.status_code == 503 and r.json()["detail"]["code"] == "sandbox_unavailable"
    finally:
        fake_api.raw("PUT", "/api/_test/sandbox", headers=tok, json={"available": True})

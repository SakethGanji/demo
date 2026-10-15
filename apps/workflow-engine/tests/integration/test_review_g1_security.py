"""UI-DESIGN-REVIEW.md fixes, group 1 — security + dry-run correctness on core paths.

D1 non-literal decorator args, D2 /api/variables/{id} scoping, D4 outward redaction,
D7/D9 module dry-runs (+ unsupported dry_run rejected), C9 one dry-run shape, C1 CORS headers.
"""

from __future__ import annotations

import base64
import shutil

import pytest

from .conftest import http_workflow, uniq
from .journeys._helpers import START, app_token, bearer, chain, new_project

pytestmark = pytest.mark.integration

SECRET = "s3cr3t-Value-0042-zz"


def _secret(api, P: str, envs=("dev", "prod")) -> str:
    key = "K_" + uniq("k").replace("-", "_").upper()
    for env in envs:
        r = api.post(f"/projects/{P}/secrets", {"key": key, "value": SECRET, "environment": env, "type": "secret"})
        assert r.status_code == 201, r.text
    return key


# ── D1: non-literal decorator arguments ──────────────────────────────────────

NONLIT_AUTH = '''from studio import api
AUTH = "none"

@api.get("/x", auth=AUTH)
def x(req):
    return {}
'''

NONLIT_NAME = '''from studio import api
N = "getX"

@api.get("/x", auth="api_key", name=N)
def x(req):
    return {}
'''

SPLAT = '''from studio import api
KW = {"auth": "none"}


@api.get("/x", **KW)
def x(req):
    return {}
'''

EXTRA_POSITIONAL = '''from studio import api

@api.get("/x", "none")
def x(req):
    return {}
'''

PATH_EXPR = '''from studio import api
P = "/x"
@api.get(P)
def x(req):
    return {}
'''


@pytest.mark.parametrize("src,line", [(NONLIT_AUTH, 4), (NONLIT_NAME, 4), (SPLAT, 5), (EXTRA_POSITIONAL, 3), (PATH_EXPR, 3)])
def test_d1_non_literal_decorator_args_rejected_with_line(api, src, line):
    P, S = new_project(api, "d1")
    r = api.post(f"/projects/{P}/modules", {"name": "m", "source": src})
    assert r.status_code == 422, r.text
    d = r.json()["detail"]
    assert d["code"] == "invalid_decorator"
    assert d["errors"][0]["line"] == line
    assert api.get(f"/projects/{P}/modules").json() == []
    assert api.get(f"/projects/{P}/routes").json() == []
    # never falls back to a public route
    assert api.raw("GET", f"/api/p/{S}/x").status_code == 404


def test_d1_non_literal_auth_rejected_on_update_too(api):
    P, S = new_project(api, "d1u")
    ok = 'from studio import api\n\n@api.get("/x", auth="api_key")\ndef x(req):\n    return {}\n'
    M = api.post(f"/projects/{P}/modules", {"name": "m", "source": ok}).json()["module"]["id"]
    r = api.put(f"/projects/{P}/modules/{M}", {"source": NONLIT_AUTH})
    assert r.status_code == 422 and r.json()["detail"]["code"] == "invalid_decorator"
    assert r.json()["detail"]["errors"][0]["line"] == 4
    route = api.get(f"/projects/{P}/routes").json()[0]
    assert route["auth"] == "api_key"
    assert api.raw("GET", f"/api/p/{S}/x").status_code == 401
    # an invalid literal auth value is also refused with its line
    bad = 'from studio import api\n\n@api.get("/x", auth="public")\ndef x(req):\n    return {}\n'
    r = api.put(f"/projects/{P}/modules/{M}", {"source": bad})
    assert r.status_code == 422 and r.json()["detail"]["code"] == "invalid_decorator"
    assert r.json()["detail"]["errors"][0]["line"] == 3


# ── D2: /api/variables/{id} only reaches team-shared rows ────────────────────


def test_d2_project_secret_not_reachable_through_variables_api(api, upstream):
    P, _ = new_project(api, "d2")
    key = _secret(api, P, envs=("dev",))
    row = next(v for v in api.get(f"/projects/{P}/secrets?environment=dev").json() if v["key"] == key)
    vid = row["id"]
    assert api.get(f"/variables/{vid}").status_code == 404
    assert api.put(f"/variables/{vid}", {"value": "overwritten", "description": None}).status_code == 404
    assert api.delete(f"/variables/{vid}").status_code == 404
    after = next(v for v in api.get(f"/projects/{P}/secrets?environment=dev").json() if v["key"] == key)
    assert after["id"] == vid and after["updated_at"] == row["updated_at"]
    # the value is untouched: upstream still receives the original value (studio /run output is not redacted)
    W = api.post(f"/projects/{P}/workflows", http_workflow(f"{upstream}/echo",
                 headers=[{"name": "X-Tok", "value": f"{{{{ $vars.{key} }}}}"}])).json()["id"]
    run = api.post(f"/workflows/{W}/run").json()
    assert run["data"]["HTTP"][0]["json"]["body"]["headers"]["x-tok"] == SECRET, run
    # team rows of another team are not reachable either
    t = api.post("/variables", {"team_id": "default", "key": uniq("TV").replace("-", "_"), "value": "v",
                                "environment": "default", "type": "string"}).json()
    assert api.get(f"/variables/{t['id']}").status_code == 200
    assert api.get(f"/variables/{t['id']}?team_id=other").status_code == 404
    assert api.delete(f"/variables/{t['id']}?team_id=other").status_code == 404
    assert api.delete(f"/variables/{t['id']}").status_code == 200


# ── D4: every outward string is redacted ─────────────────────────────────────


def _route(api, P, path, wf) -> str:
    W = api.post(f"/projects/{P}/workflows", wf).json()["id"]
    r = api.post(f"/projects/{P}/routes", {"method": "GET", "path": path, "handler": {"type": "workflow", "workflow_id": W}})
    assert r.status_code == 201, r.text
    return W


def _respond_wf(key: str) -> dict:
    return chain(uniq("resp"), {"name": "Respond", "type": "Respond", "position": {"x": 200, "y": 0}, "parameters": {
        "statusCode": 200, "contentType": "application/json",
        "headers": [{"name": "X-Leak", "value": f"{{{{ $vars.{key} }}}}"},
                    {"name": "X-Leak-B64", "value": base64.b64encode(SECRET.encode()).decode()}],
        "body": f'{{"v": "{{{{ $vars.{key} }}}}"}}'}})


def _stop_wf(key: str) -> dict:
    return chain(uniq("stop"), {"name": "Stop", "type": "StopAndError", "position": {"x": 200, "y": 0},
                                "parameters": {"errorType": "error", "message": f"boom {{{{ $vars.{key} }}}}"}})


def test_d4_router_redacts_headers_bodies_errors_and_encoded_variants(api, upstream):
    P, S = new_project(api, "d4")
    key = _secret(api, P)
    b64 = base64.b64encode(SECRET.encode()).decode()
    _route(api, P, "/respond", _respond_wf(key))
    _route(api, P, "/stop", _stop_wf(key))
    _route(api, P, "/b64", http_workflow(f"{upstream}/b64", headers=[{"name": "X-Tok", "value": f"{{{{ $vars.{key} }}}}"}]))

    r = api.raw("GET", f"/api/p/{S}/respond")
    assert r.status_code == 200, r.text
    assert r.headers["x-leak"] == "***" and r.headers["x-leak-b64"] == "***"
    assert r.json() == {"v": "***"}

    r = api.raw("GET", f"/api/p/{S}/stop")
    assert r.status_code == 502 and r.json()["error"] == "handler_failed"
    assert SECRET not in r.text and "***" in r.json()["message"]

    r = api.raw("GET", f"/api/p/{S}/b64")
    assert r.status_code == 200, r.text
    assert b64 not in r.text and SECRET not in r.text
    assert r.json()["body"]["encoded"] == "***"

    # /test returns what the router would have returned — also redacted (headers included)
    rid = next(x["id"] for x in api.get(f"/projects/{P}/routes").json() if x["path"] == "/respond")
    t = api.post(f"/projects/{P}/routes/{rid}/test", {"params": {}, "query": {}, "headers": {}, "body": None})
    assert t.status_code == 200 and SECRET not in t.text and b64 not in t.text
    assert {k.lower(): v for k, v in t.json()["headers"].items()}["x-leak"] == "***"


def test_d4_dispatch_redaction_unit_covers_python_headers():
    """Python-route responses go through the same central redactor (headers + body + message)."""
    from src.services.route_dispatcher import DispatchResult, redact_result

    res = DispatchResult(201, {"a": f"x{SECRET}y", "n": 1}, {"X-K": SECRET, "X-Ok": "fine"}, "application/json", "exec_1")
    out = redact_result(res, {SECRET})
    assert out.headers == {"X-K": "***", "X-Ok": "fine"} and out.body == {"a": "x***y", "n": 1}
    txt = redact_result(DispatchResult(200, base64.b64encode(SECRET.encode()).decode(), {}, "text/plain"), {SECRET})
    assert txt.body == "***"


@pytest.mark.skipif(shutil.which("esbuild") is None, reason="esbuild not on PATH")
def test_d4_public_app_redacts_respond_headers_and_errors(api):
    P, S = new_project(api, "d4a")
    key = _secret(api, P)
    W1 = api.post(f"/projects/{P}/workflows", _respond_wf(key)).json()["id"]
    W2 = api.post(f"/projects/{P}/workflows", _stop_wf(key)).json()["id"]
    aid = api.post(f"/projects/{P}/apps", {"name": uniq("app"), "definition": {}}).json()["id"]
    src = "export default function App(){return <div>x</div>}"
    api.put(f"/apps/{aid}", {"source_code": src, "files": [{"path": "App.tsx", "content": src}], "create_version": True,
                             "version_trigger": "manual", "workflow_ids": [W1, W2]})
    slug = uniq("d4a")
    assert api.post(f"/apps/{aid}/publish", {"slug": slug, "access": "public"}).status_code == 200
    auth = bearer(app_token(api, slug))
    r = api.raw("POST", f"/a/{slug}/api/workflow/{W1}/run", json={}, headers=auth)
    assert r.status_code == 200 and r.headers["x-leak"] == "***" and r.headers["x-leak-b64"] == "***"
    assert SECRET not in r.text
    r = api.raw("POST", f"/a/{slug}/api/workflow/{W2}/run", json={}, headers=auth)
    assert r.status_code == 200 and r.json()["status"] == "failed"
    assert SECRET not in r.text and "***" in r.json()["errors"][0]["error"]


# ── D7 / D9 / C9: real dry runs, one shape ───────────────────────────────────

MOD = 'from studio import api\n\n@api.get("/a", auth="none")\ndef a(req):\n    return {"a": 1}\n\n@api.get("/b")\ndef b(req):\n    return {}\n'
DRY_KEYS = {"dry_run", "routes", "breaking_changes", "changes", "spec_revision", "spec_hash"}


def test_d7_module_create_dry_run_writes_nothing(api):
    P, S = new_project(api, "d7")
    r = api.post(f"/projects/{P}/modules?dry_run=true", {"name": "orders", "source": MOD})
    assert r.status_code == 200, r.text
    b = r.json()
    assert DRY_KEYS <= set(b) and b["dry_run"] is True
    assert sorted(x["path"] for x in b["routes"]["created"]) == ["/a", "/b"]
    assert b["module"]["id"] is None and b["module"]["name"] == "orders"
    assert {c["kind"] for c in b["changes"]} == {"route_added"} and b["breaking_changes"] == []
    assert api.get(f"/projects/{P}/modules").json() == []
    assert api.get(f"/projects/{P}/routes").json() == []
    # validation runs in dry-run mode too
    assert api.post(f"/projects/{P}/modules?dry_run=true", {"name": "orders", "source": "def x(:\n"}).json()["detail"]["code"] == "syntax_error"
    assert api.post(f"/projects/{P}/modules?dry_run=true", {"name": "Bad-Name", "source": MOD}).status_code == 422
    real = api.post(f"/projects/{P}/modules", {"name": "orders", "source": MOD})
    assert real.status_code == 201
    r = api.post(f"/projects/{P}/modules?dry_run=true", {"name": "orders", "source": MOD})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "name_taken"


def test_d9_module_delete_dry_run_lists_removed_routes(api):
    P, S = new_project(api, "d9")
    M = api.post(f"/projects/{P}/modules", {"name": "orders", "source": MOD}).json()["module"]["id"]
    rev = api.get(f"/projects/{P}/spec/revisions").json()[-1]["revision"]
    r = api.delete(f"/projects/{P}/modules/{M}?dry_run=true")
    assert r.status_code == 200, r.text
    b = r.json()
    assert DRY_KEYS <= set(b) and b["dry_run"] is True and b["spec_revision"] is None
    assert sorted(x["path"] for x in b["routes"]["removed"]) == ["/a", "/b"]
    assert b["routes"]["created"] == [] and b["routes"]["updated"] == []
    assert {c["kind"] for c in b["breaking_changes"]} == {"route_removed"}
    assert api.get(f"/projects/{P}/modules/{M}").status_code == 200
    assert len(api.get(f"/projects/{P}/routes").json()) == 2
    assert api.get(f"/projects/{P}/spec/revisions").json()[-1]["revision"] == rev


def test_c9_route_and_module_dry_runs_share_one_shape(api):
    P, S = new_project(api, "c9")
    M = api.post(f"/projects/{P}/modules", {"name": "orders", "source": MOD}).json()["module"]["id"]
    rid = api.post(f"/projects/{P}/routes", {"method": "GET", "path": "/ui", "handler": {"type": "stub"}}).json()["route"]["id"]
    shapes = [
        api.put(f"/projects/{P}/routes/{rid}?dry_run=true", {"path": "/ui2"}).json(),
        api.delete(f"/projects/{P}/routes/{rid}?dry_run=true").json(),
        api.put(f"/projects/{P}/modules/{M}?dry_run=true", {"source": MOD.replace('"/b"', '"/c"')}).json(),
        api.delete(f"/projects/{P}/modules/{M}?dry_run=true").json(),
        api.post(f"/projects/{P}/modules?dry_run=true", {"name": "more", "source": 'from studio import api\n@api.get("/z")\ndef z(req):\n    return {}\n'}).json(),
    ]
    for s in shapes:
        assert DRY_KEYS <= set(s), s
        assert set(s["routes"]) == {"created", "updated", "removed"}
        for c in s["changes"] + s["breaking_changes"]:
            assert {"kind", "route", "field", "breaking", "affected"} <= set(c)
    assert shapes[0]["routes"]["updated"][0]["id"] == rid
    assert shapes[1]["routes"]["removed"][0]["id"] == rid
    assert any(c["kind"] == "path_changed" for c in shapes[0]["breaking_changes"])


def test_d7_unsupported_dry_run_is_rejected_not_ignored(api):
    n = len(api.get("/projects").json())
    r = api.post("/projects?dry_run=true", {"name": uniq("nodry")})
    assert r.status_code == 422 and r.json()["detail"]["code"] == "dry_run_unsupported"
    assert len(api.get("/projects").json()) == n
    P, _ = new_project(api, "d7u")
    r = api.post(f"/projects/{P}/tables?dry_run=1", {"name": "t", "columns": [{"name": "a", "type": "string"}]})
    assert r.status_code == 422 and r.json()["detail"]["code"] == "dry_run_unsupported"
    assert api.get(f"/projects/{P}/tables").json() == []
    # user-defined API routes own their query string: ?dry_run reaches the handler untouched
    W = api.post(f"/projects/{P}/workflows", chain(uniq("q"), {"name": "Respond", "type": "Respond", "position": {"x": 1, "y": 1},
                 "parameters": {"statusCode": 200, "headers": [], "body": "{{ $json.query }}"}})).json()["id"]
    api.post(f"/projects/{P}/routes", {"method": "POST", "path": "/q", "handler": {"type": "workflow", "workflow_id": W}})
    slug = api.get(f"/projects/{P}").json()["slug"]
    r = api.raw("POST", f"/api/p/{slug}/q?dry_run=true", json={})
    assert r.status_code == 200 and r.json() == {"dry_run": "true"}, r.text
    # dry_run=false is harmless; supported endpoints keep working
    assert api.post(f"/projects/{P}/tables?dry_run=false", {"name": "t", "columns": [{"name": "a", "type": "string"}]}).status_code == 201


# ── C1: CORS exposes the headers the studio reads ────────────────────────────


def test_c1_cors_exposes_cursor_execution_and_etag_headers(api):
    r = api.get("/projects", headers={"Origin": "http://localhost:5174"})
    exposed = {h.strip().lower() for h in r.headers.get("access-control-expose-headers", "").split(",")}
    assert {"x-next-cursor", "x-execution-id", "etag"} <= exposed
    P, S = new_project(api, "c1")
    r = api.raw("GET", f"/api/p/{S}/nothing", headers={"Origin": "http://localhost:5174"})
    assert "x-execution-id" in r.headers.get("access-control-expose-headers", "").lower()

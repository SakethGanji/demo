"""Journeys J1 (flagship) and J17 (release gates / rollback) — UI-INTEGRATION.md §6."""

from __future__ import annotations

import shutil

import pytest

from ..conftest import uniq
from ._helpers import app_token, bearer, new_project, save_app

pytestmark = pytest.mark.integration
HAS_ESBUILD = shutil.which("esbuild") is not None

APP_SRC = ("import { api } from './api/client'\nexport default function App() {\n"
           "  const load = () => api.getOrdersById({ id: '42' })\n  return <button onClick={load}>Load</button>\n}\n")


def _handler_body(extra: list[dict] | None = None) -> dict:
    fields = [{"name": "id", "value": "{{ $json.params.id }}"}, {"name": "greeting", "value": "{{ $vars.GREETING }}"}] + (extra or [])
    return {"name": "GET /orders/{id}", "nodes": [
        {"name": "Start", "type": "Start", "parameters": {}, "position": {"x": 0, "y": 0}},
        {"name": "Set", "type": "Set", "parameters": {"mode": "manual", "fields": fields, "keepOnlySet": True}, "position": {"x": 200, "y": 0}}],
        "connections": [{"source_node": "Start", "target_node": "Set", "source_output": "main", "target_input": "main"}], "settings": {}}


def test_j01_flagship(api):
    lst = api.get("/projects").json()                                                          # 1
    d = next(p for p in lst if p["id"] == "proj_default")
    assert set(d["counts"]) == {"apps", "workflows", "routes", "tables"} and d["health"]["status"] in {"ok", "degraded", "failing", "unknown"}
    S = "shop-" + uniq("s")                                                                    # 2
    r = api.post("/projects", {"name": "Shop " + uniq(), "slug": S})
    p = r.json()
    assert r.status_code == 201 and p["id"].startswith("proj_") and p["slug"] == S
    assert p["environments"] == [{"name": "dev", "kind": "draft"}, {"name": "prod", "kind": "release"}]
    assert (p["default_environment"], p["live_environment"], p["settings"]["egress_allowlist"]) == ("dev", "prod", [])
    P = p["id"]
    assert api.get(f"/projects/{P}").json()["slug"] == S                                       # 3
    assert api.get(f"/projects/{S}").json()["id"] == P                                         # 4
    s = api.get(f"/projects/{P}/summary").json()                                               # 5
    assert s["counts"] == {"apps": 0, "workflows": 0, "routes": 0, "tables": 0} and s["last_run"] is None and s["health"]["status"] == "unknown"
    r = api.post(f"/projects/{P}/secrets", {"key": "GREETING", "value": "hello-dev", "environment": "dev", "type": "string"})  # 6
    g = r.json()
    assert r.status_code == 201 and (g["key"], g["environment"], g["type"], g["value"], g["scope"], g["overrides_team"], g["overridden"]) == \
        ("GREETING", "dev", "string", "hello-dev", "project", False, False)
    assert any(x["key"] == "GREETING" and x["scope"] == "project" for x in api.get(f"/projects/{P}/secrets?environment=dev").json())  # 7
    r = api.post(f"/projects/{P}/routes", {"method": "GET", "path": "/orders/{id}", "auth": "none", "handler": {"type": "workflow", "create": True}})  # 8
    assert r.status_code == 201
    rt, R1 = r.json()["route"], r.json()["spec_revision"]
    assert rt["id"].startswith("route_") and rt["name"] == "getOrdersById" and rt["source"] == "ui" and rt["response_mode"] == "auto"
    assert rt["validate_request"] is False and rt["used_by_count"] == 0 and rt["url"] == f"/api/p/{S}/orders/{{id}}"
    assert isinstance(R1, int) and r.json()["breaking_changes"] == []
    R, W = rt["id"], rt["handler"]["workflow_id"]
    assert any(w["id"] == W and w["name"] == "GET /orders/{id}" and w["project_id"] == P for w in api.get(f"/projects/{P}/workflows").json())  # 9
    wf = api.get(f"/workflows/{W}").json()                                                     # 10
    assert wf["project_id"] == P and {"Start", "Respond"} <= {n["type"] for n in wf["definition"]["nodes"]}
    r = api.raw("GET", f"/api/p/{S}/orders/42")                                                # 11
    assert r.status_code == 200 and r.json() == {"id": "42"} and r.headers.get("x-execution-id")
    assert api.put(f"/workflows/{W}", _handler_body()).status_code == 200                      # 12
    test = {"params": {"id": "42"}, "query": {}, "headers": {}, "body": None, "environment": "dev", "apply_schemas": False}
    t = api.post(f"/projects/{P}/routes/{R}/test", test).json()                                # 13
    assert t["status"] == 200 and t["body"] == {"id": "42", "greeting": "hello-dev"} and t["execution_id"].startswith("exec_")
    assert t["inferred"]["response_schema"]["properties"]["id"]["type"] == "string" and t["applied"] is False
    assert isinstance(t["duration_ms"], (int, float))
    assert api.get(f"/projects/{P}/routes/{R}").json()["response_schema"] is None             # 14
    assert api.post(f"/projects/{P}/routes/{R}/test", {**test, "apply_schemas": True}).json()["applied"] is True  # 15
    got = api.get(f"/projects/{P}/routes/{R}").json()                                          # 16
    assert {"id", "greeting"} <= set(got["response_schema"]["properties"]) and got["validate_request"] is False
    revs = api.get(f"/projects/{P}/spec/revisions").json()                                     # 17
    assert max(x["revision"] for x in revs) == R1 + 1
    d = api.get(f"/projects/{P}/spec/diff?from={R1}&to={R1 + 1}").json()                       # 18
    assert d["changes"] and all(c["breaking"] is False for c in d["changes"])
    spec = api.get(f"/projects/{P}/openapi.json?environment=dev").json()                       # 19
    assert spec["openapi"].startswith("3.1") and spec["paths"]["/orders/{id}"]["get"]["operationId"] == "getOrdersById"
    assert "greeting" in spec["paths"]["/orders/{id}"]["get"]["responses"]["200"]["content"]["application/json"]["schema"]["properties"]
    c = api.raw("GET", f"/api/projects/{P}/client.ts?environment=dev")                         # 20
    assert c.headers["content-type"].startswith("text/typescript") and "getOrdersById" in c.text
    assert f"'/api/p/{S}'" in c.text and "@generated" in c.text
    r = api.post(f"/projects/{P}/apps", {"name": "Orders UI", "definition": {"sourceCode": None}})  # 21
    A = r.json()["id"]
    assert r.json()["project_id"] == P
    r = api.post(f"/projects/{P}/apps/{A}/preview-token")                                      # 22
    assert r.status_code == 201 and r.json()["environment"] == "dev" and r.json()["expires_at"]
    PT = r.json()["token"]
    assert api.raw("GET", f"/api/p/{S}/orders/42", headers=bearer(PT)).json() == {"id": "42", "greeting": "hello-dev"}  # 23
    assert save_app(api, A, APP_SRC)["current_version"]["version_number"] == 1                 # 24
    u = api.get(f"/apps/{A}/api-usage").json()                                                 # 25
    assert any(x["operation"] == "getOrdersById" and x["method"] == "GET" and x["path"] == "/orders/{id}" and x["file"] == "App.tsx"
               and x["line"] == 3 and x["route_id"] == R and x["status"] == "ok" for x in u)
    assert any(x["app_id"] == A and x["app_name"] == "Orders UI" and x["line"] == 3 for x in api.get(f"/projects/{P}/routes/{R}/used-by").json())  # 26
    assert next(x for x in api.get(f"/projects/{P}/routes").json() if x["id"] == R)["used_by_count"] == 1  # 27
    if HAS_ESBUILD:                                                                            # 28 [esbuild]
        r = api.post(f"/apps/{A}/publish", {"slug": S + "-ui", "access": "public"})
        assert r.status_code == 200 and r.json()["active"] is True and r.json()["public_url"].endswith(f"/a/{S}-ui")
    r = api.post(f"/projects/{P}/releases", {"environment": "dev", "notes": "r1"})              # 29
    assert r.status_code == 201, r.text
    rel = r.json()
    # review D10: a draft env's release is a snapshot — never "active" (dev always serves drafts)
    assert rel["id"].startswith("rel_") and (rel["environment"], rel["number"], rel["is_active"], rel["status"]) == ("dev", 1, False, "snapshot")
    assert all(c["status"] in ("pass", "warn") for c in rel["checks"])
    assert any(c["id"] == "app_calls_resolve" and c["status"] == "pass" for c in rel["checks"])
    REL1 = rel["id"]
    r = api.post(f"/projects/{P}/releases/{REL1}/promote", {"to_environment": "prod"})         # 30
    assert r.status_code == 422 and r.json()["detail"]["code"] == "checks_failed"
    assert any(c["id"] == "secrets_present" and c["status"] == "fail" and c["details"]["missing"] == ["GREETING"] for c in r.json()["detail"]["checks"])
    prod = api.get(f"/projects/{P}/releases?environment=prod").json()                          # 31
    assert any(x["status"] == "blocked" and x["is_active"] is False for x in prod) and not any(x["is_active"] for x in prod)
    assert api.post(f"/projects/{P}/secrets", {"key": "GREETING", "value": "hello-prod", "environment": "prod", "type": "string"}).status_code == 201  # 32
    r = api.post(f"/projects/{P}/releases/{REL1}/promote", {"to_environment": "prod"})         # 33
    assert r.status_code == 201
    p1 = r.json()
    assert (p1["environment"], p1["is_active"], p1["status"], p1["source_release_id"]) == ("prod", True, "active", REL1)
    PROD1 = p1["id"]
    assert api.raw("GET", f"/api/p/{S}/orders/42").json() == {"id": "42", "greeting": "hello-prod"}  # 34
    assert api.put(f"/workflows/{W}", _handler_body([{"name": "v", "value": "2"}])).status_code == 200  # 35
    assert "v" not in api.raw("GET", f"/api/p/{S}/orders/42").json()                           # 36
    assert api.post(f"/projects/{P}/routes/{R}/test", test).json()["body"]["v"] == "2"         # 37
    if HAS_ESBUILD:                                                                            # 38-39 [esbuild]
        html = api.raw("GET", f"/a/{S}-ui")
        assert html.status_code == 200 and "window.__APP_TOKEN=" in html.text
        AT = app_token(api, S + "-ui")
        assert api.raw("GET", f"/api/p/{S}/orders/42", headers=bearer(AT)).json()["greeting"] == "hello-prod"
    r = api.post(f"/projects/{P}/releases", {"environment": "dev", "notes": "r2"})              # 40
    assert r.status_code == 201 and r.json()["number"] == 2 and r.json()["status"] == "snapshot"   # review D10
    REL2 = r.json()["id"]
    r = api.post(f"/projects/{P}/releases/{REL2}/promote", {"to_environment": "prod"})         # 41
    assert r.status_code == 201 and r.json()["is_active"] is True
    PROD2 = r.json()["id"]
    assert api.raw("GET", f"/api/p/{S}/orders/42").json()["v"] == "2"                          # 42
    d = api.get(f"/projects/{P}/releases/{PROD1}/diff?against=active").json()                  # 43
    assert {"routes", "apps", "workflows", "secrets"} <= set(d) and any(x["workflow_id"] == W for x in d["workflows"])
    r = api.post(f"/projects/{P}/environments/prod/rollback", {})                              # 44
    assert r.status_code == 200 and r.json()["environment"] == "prod" and r.json()["is_active"] is True and r.json()["id"] == PROD1
    assert "v" not in api.raw("GET", f"/api/p/{S}/orders/42").json()                           # 45
    prod = api.get(f"/projects/{P}/releases?environment=prod").json()                          # 46
    assert len([x for x in prod if x["is_active"]]) == 1 and any(x["id"] == PROD2 and x["status"] == "rolled_back" for x in prod)
    snap = api.get(f"/projects/{P}/releases/{PROD1}").json()["snapshot"]                       # 47
    assert any(x["name"] == "getOrdersById" and x["handler"]["type"] == "workflow" and x["handler"]["workflow_id"] == W for x in snap["routes"])
    assert any(x["app_id"] == A for x in snap["apps"]) and snap["spec_hash"]
    assert any(x["key"] == "GREETING" and x["scope"] == "project" for x in snap["secret_bindings"])
    s = api.get(f"/projects/{P}/summary").json()                                               # 48
    assert s["counts"] == {"apps": 1, "workflows": 1, "routes": 1, "tables": 0} and s["last_run"]["mode"] == "api"
    assert s["active_releases"]["prod"]["release_id"] == PROD1
    runs = api.get(f"/projects/{P}/runs?mode=api&limit=50").json()                             # 49
    assert runs and all(x["mode"] == "api" and x["environment"] in ("dev", "prod") for x in runs)


def test_j17_release_gates_and_rollback(api):
    P, S = new_project(api, "j17")                                                             # 1
    RA = api.post(f"/projects/{P}/routes", {"method": "GET", "path": "/a", "handler": {"type": "workflow", "create": True}}).json()["route"]["id"]
    A = api.post(f"/projects/{P}/apps", {"name": "a", "definition": {}}).json()["id"]         # 2
    save_app(api, A, "import { api } from './api/client'\nexport default function App() {\n"
                     f"  const x = () => {{ api.getA(); fetch('/api/p/{S}/missing') }}\n  return <div onClick={{x}} />\n}}\n")
    r = api.post(f"/projects/{P}/releases", {"environment": "dev"})                             # 3
    assert r.status_code == 422 and r.json()["detail"]["code"] == "checks_failed"
    assert any(c["id"] == "app_calls_resolve" and c["status"] == "fail" for c in r.json()["detail"]["checks"])
    assert r.json()["detail"]["release_id"].startswith("rel_") and r.json()["detail"]["number"] == 1
    dev = api.get(f"/projects/{P}/releases?environment=dev").json()                            # 4
    assert any(x["status"] == "blocked" and not x["is_active"] for x in dev) and not any(x["is_active"] for x in dev)
    assert api.post(f"/projects/{P}/routes/stubs", {"app_id": A, "routes": [{"method": "GET", "path": "/missing"}]}).status_code == 201  # 5
    r = api.post(f"/projects/{P}/releases?dry_run=true", {"environment": "dev"})                # G10 preview
    assert r.status_code == 200 and {"checks", "diff"} <= set(r.json())
    r = api.post(f"/projects/{P}/releases", {"environment": "dev", "notes": "with stub"})       # 6
    assert r.status_code == 201 and r.json()["status"] == "snapshot" and r.json()["is_active"] is False   # review D10
    ch = r.json()["checks"]
    assert any(c["id"] == "stub_routes" and c["status"] == "warn" and c["details"]["routes"] == ["GET /missing"] for c in ch)
    assert any(c["id"] == "app_calls_resolve" and c["status"] == "pass" for c in ch)
    D1 = r.json()["id"]
    snap = api.get(f"/projects/{P}/releases/{D1}").json()["snapshot"]                          # 7
    assert len(snap["routes"]) == 2 and any(x["app_id"] == A and isinstance(x["version_id"], int) for x in snap["apps"])
    assert len(snap["workflows"]) == 1 and isinstance(snap["workflows"][0]["version_id"], int)
    r = api.post(f"/projects/{P}/releases/{D1}/promote", {"to_environment": "prod"})            # 8
    assert r.status_code == 201
    P1 = r.json()["id"]
    r = api.put(f"/projects/{P}/routes/{RA}", {"auth": "api_key"})                              # 9
    assert r.status_code == 200 and any(c["breaking"] for c in r.json()["breaking_changes"])
    r = api.post(f"/projects/{P}/releases", {"environment": "dev"})                             # 10
    assert r.status_code == 201
    D2 = r.json()["id"]
    r = api.post(f"/projects/{P}/releases/{D2}/promote?dry_run=true", {"to_environment": "prod"})
    assert r.status_code == 200 and any(c["id"] == "breaking_vs_active" and c["status"] == "warn" for c in r.json()["checks"])
    r = api.post(f"/projects/{P}/releases/{D2}/promote", {"to_environment": "prod"})
    assert r.status_code == 201 and any(c["id"] == "breaking_vs_active" and c["status"] == "warn" for c in r.json()["checks"])
    P2 = r.json()["id"]
    assert api.raw("GET", f"/api/p/{S}/a").status_code == 401                                   # 11
    d = api.get(f"/projects/{P}/releases/{P2}/diff?against={P1}").json()                        # 12
    assert any(c["kind"] == "auth_changed" and c["breaking"] for c in d["routes"])
    r = api.post(f"/projects/{P}/environments/prod/rollback", {"release_id": P1})               # 13
    assert r.status_code == 200 and r.json()["id"] == P1 and r.json()["is_active"] is True and r.json()["checks"]
    assert all(c["status"] in ("pass", "warn") for c in r.json()["checks"])
    assert api.raw("GET", f"/api/p/{S}/a").status_code == 200                                   # 14
    prod = api.get(f"/projects/{P}/releases?environment=prod").json()                          # 15
    assert [x["id"] for x in prod if x["is_active"]] == [P1] and any(x["id"] == P2 and x["status"] == "rolled_back" for x in prod)
    # G12: no previous release → 409
    Q, _ = new_project(api, "j17q")
    assert api.post(f"/projects/{Q}/environments/prod/rollback", {}).json()["detail"]["code"] == "no_previous_release"

"""Journey J14 (UI-INTEGRATION.md §6): app ↔ API — usage, stubs from UI, breaking changes, preview, AI body."""

from __future__ import annotations

import re

import pytest

from ..conftest import uniq
from ._helpers import SET_WF, bearer, needs_esbuild, new_project, save_app

pytestmark = pytest.mark.integration


def _app_src(S: str) -> str:
    return ("import { api } from './api/client'\n"
            "export default function App() {\n"
            "  const a = () => api.getOrdersById({ id: '1' })\n"
            f"  const b = () => fetch('/api/p/{S}/refunds', {{ method: 'POST' }})\n"
            "  const c = () => api.listCustomers()\n"
            "  return <div onClick={() => { a(); b(); c() }} />\n"
            "}\n")


def _setup(api):
    P, S = new_project(api, "j14")                                                             # 1
    r = api.post(f"/projects/{P}/routes", {"method": "GET", "path": "/orders/{id}",
                                           "response_schema": {"type": "object", "properties": {"id": {"type": "string"}, "total": {"type": "number"}}},
                                           "handler": {"type": "workflow", "create": True}})
    R = r.json()["route"]["id"]
    A = api.post(f"/projects/{P}/apps", {"name": "Orders UI", "definition": {}}).json()["id"]  # 2
    save_app(api, A, _app_src(S))
    return P, S, R, A


def test_j14_app_api(api):
    P, S, R, A = _setup(api)
    usage = api.get(f"/apps/{A}/api-usage").json()                                             # 3
    assert any(u["operation"] == "getOrdersById" and u["line"] == 3 and u["route_id"] == R and u["status"] == "ok" for u in usage)
    assert any(u["method"] == "POST" and u["path"] == "/refunds" and u["line"] == 4 and u["route_id"] is None and u["status"] == "missing" for u in usage)
    assert any(u["operation"] == "listCustomers" and u["line"] == 5 and u["status"] == "missing" for u in usage)
    r = api.post(f"/projects/{P}/routes/stubs", {"app_id": A, "routes": [{"method": "POST", "path": "/refunds", "name": "createRefund"}]})  # 4
    assert r.status_code == 201 and r.json()["existing"] == [] and len(r.json()["created"]) == 1
    c = r.json()["created"][0]
    assert (c["name"], c["source"], c["handler"]["type"]) == ("createRefund", "app_stub", "stub")
    RST = c["id"]
    r = api.post(f"/projects/{P}/routes/stubs", {"app_id": A, "routes": [{"method": "POST", "path": "/refunds", "name": "createRefund"}]})  # 5
    assert r.status_code == 201 and r.json()["created"] == [] and [x["id"] for x in r.json()["existing"]] == [RST]
    row = next(u for u in api.get(f"/apps/{A}/api-usage").json() if u["path"] == "/refunds")   # 6
    assert row["route_id"] == RST and row["status"] == "ok"
    r = api.raw("POST", f"/api/p/{S}/refunds")                                                  # 7
    assert r.status_code == 501 and r.json()["operation"] == "createRefund"
    assert "createRefund" in api.raw("GET", f"/api/projects/{P}/client.ts").text
    r = api.put(f"/projects/{P}/routes/{RST}", {"handler": {"type": "workflow", "create": True}})  # 8
    assert r.status_code == 200 and r.json()["route"]["handler"]["type"] == "workflow"
    assert api.raw("POST", f"/api/p/{S}/refunds").status_code == 200
    narrowed = {"response_schema": {"type": "object", "properties": {"id": {"type": "string"}}}}
    prev = max(x["revision"] for x in api.get(f"/projects/{P}/spec/revisions").json())
    r = api.put(f"/projects/{P}/routes/{R}?dry_run=true", narrowed)                            # 9
    assert r.status_code == 200
    ch = next(c for c in r.json()["breaking_changes"] if c["kind"] == "response_field_removed")
    assert ch["field"] == "total" and ch["operation"] == "getOrdersById" and ch["breaking"] is True
    assert {"app_id": A, "file": "App.tsx", "line": 3}.items() <= ch["affected"][0].items()
    assert "total" in api.get(f"/projects/{P}/routes/{R}").json()["response_schema"]["properties"]
    r = api.put(f"/projects/{P}/routes/{R}", narrowed)                                         # 10
    assert any(c["kind"] == "response_field_removed" for c in r.json()["breaking_changes"]) and r.json()["spec_revision"] == prev + 1
    revs = api.get(f"/projects/{P}/spec/revisions").json()                                     # 11
    assert any(c["kind"] == "response_field_removed" and c["breaking"] for c in revs[-1]["changes"])
    d = api.get(f"/projects/{P}/spec/diff?from={prev}&to={prev + 1}").json()
    assert any(c["kind"] == "response_field_removed" for c in d["changes"])
    row = next(u for u in api.get(f"/apps/{A}/api-usage").json() if u["operation"] == "getOrdersById")  # 12
    assert row["status"] == "changed"
    r = api.post(f"/projects/{P}/apps/{A}/preview-token")                                       # 13
    assert r.status_code == 201 and r.json()["environment"] == "dev"
    assert api.raw("GET", f"/api/p/{S}/orders/1", headers=bearer(r.json()["token"])).status_code == 200
    body = {"message": "Build a UI for getOrdersById", "app_id": A, "project_id": P, "route_ids": [R],  # 14 [LLM]
            "api_execution_ids": [], "conversation_history": []}
    with api.http.stream("POST", "/api/ai/app-builder/chat", json=body, headers={"Content-Type": "application/json"}, timeout=60) as resp:
        assert resp.status_code == 200


def test_j14_used_by_and_preview_run(api):
    P, S, R, A = _setup(api)
    used = api.get(f"/projects/{P}/routes/{R}/used-by").json()
    assert any(u["app_id"] == A and u["app_name"] == "Orders UI" and u["file"] == "App.tsx" and u["line"] == 3 for u in used)
    assert next(r for r in api.get(f"/projects/{P}/routes").json() if r["id"] == R)["used_by_count"] == 1
    # G20: preview equivalent of the published workflow proxy (preview token, allow-list, default env)
    W = api.post(f"/projects/{P}/workflows", SET_WF("{{ $json.body.n * 2 }}")).json()["id"]
    api.put(f"/apps/{A}", {"workflow_ids": [W]})
    PT = api.post(f"/projects/{P}/apps/{A}/preview-token").json()["token"]
    r = api.raw("POST", f"/api/apps/{A}/workflow/{W}/run", json={"n": 4}, headers=bearer(PT))
    assert r.status_code == 200 and r.json()["data"] == [{"answer": 8}]
    run = next(x for x in api.get(f"/projects/{P}/runs").json() if x["id"] == r.json()["execution_id"])
    assert run["environment"] == "dev"
    assert api.raw("POST", f"/api/apps/{A}/workflow/{W}/run", json={}).status_code == 401
    other = api.post(f"/projects/{P}/workflows", SET_WF("{{ 1 }}")).json()["id"]
    assert api.raw("POST", f"/api/apps/{A}/workflow/{other}/run", json={}, headers=bearer(PT)).status_code == 403


@needs_esbuild
def test_j14_publish_bundles_current_client(api):                                              # 15 [esbuild]
    P, S, R, A = _setup(api)
    slug = uniq("cl")
    r = api.post(f"/apps/{A}/publish", {"slug": slug, "access": "public"})
    assert r.status_code == 200, r.text
    page = api.raw("GET", f"/a/{slug}").text
    js = re.search(rf"/a/{slug}/_assets/([0-9a-f]+)\.js", page).group(1)
    bundle = api.raw("GET", f"/a/{slug}/_assets/{js}.js").text
    spec_hash = api.get(f"/projects/{P}/openapi.json?environment=prod").headers["etag"].strip('"')
    assert "@generated by studio" in bundle and spec_hash in bundle

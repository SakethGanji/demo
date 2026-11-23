"""UI-DESIGN-REVIEW.md — D17 (G24) public-app run errors carry error_code/details; C10 /test semantics pinned."""

from __future__ import annotations

import shutil

import pytest

from .conftest import uniq
from .journeys._helpers import app_token, bearer, chain, new_project, save_app

pytestmark = pytest.mark.integration


@pytest.mark.skipif(shutil.which("esbuild") is None, reason="esbuild not on PATH")
def test_d17_public_app_run_errors_have_error_code_and_details(api):
    P, _ = new_project(api, "d17")
    W = api.post(f"/projects/{P}/workflows", chain(uniq("dt"), {"name": "DT", "type": "DataTable", "position": {"x": 1, "y": 1},
                                                              "parameters": {"table": "ghost", "operation": "list"}})).json()["id"]
    A = api.post(f"/projects/{P}/apps", {"name": uniq("app"), "definition": {}}).json()["id"]
    save_app(api, A, "export default function App(){return <div>x</div>}")
    api.put(f"/apps/{A}", {"workflow_ids": [W]})
    slug = uniq("d17")
    assert api.post(f"/apps/{A}/publish", {"slug": slug, "access": "public"}).status_code == 200
    r = api.raw("POST", f"/a/{slug}/api/workflow/{W}/run", json={}, headers=bearer(app_token(api, slug)))
    assert r.status_code == 200 and r.json()["status"] == "failed"
    e = r.json()["errors"][0]
    assert e["node_name"] == "DT" and e["error_code"] == "table_not_found" and e["details"] == {"table": "ghost"}
    # the studio preview equivalent (G20) has the same shape
    pt = api.post(f"/projects/{P}/apps/{A}/preview-token").json()["token"]
    e = api.raw("POST", f"/api/apps/{A}/workflow/{W}/run", json={}, headers=bearer(pt)).json()["errors"][0]
    assert e["error_code"] == "table_not_found"


def test_c10_route_test_semantics(api):
    P, S = new_project(api, "c10")
    stub = api.post(f"/projects/{P}/routes", {"method": "GET", "path": "/s", "handler": {"type": "stub"}}).json()["route"]
    r = api.post(f"/projects/{P}/routes/{stub['id']}/test", {"environment": "prod"})
    assert r.status_code == 422 and r.json()["detail"]["code"] == "environment_not_draft"
    r = api.post(f"/projects/{P}/routes/{stub['id']}/test", {"apply_schemas": True}).json()
    assert r["status"] == 501 and r["execution_id"] is None and r["trace"] is None and r["applied"] is False
    assert api.get(f"/projects/{P}/routes/{stub['id']}").json()["response_schema"] is None
    W = api.post(f"/projects/{P}/workflows", chain(uniq("e"), {"name": "Stop", "type": "StopAndError", "position": {"x": 1, "y": 1},
                                                             "parameters": {"errorType": "error", "message": "no"}})).json()["id"]
    rid = api.post(f"/projects/{P}/routes", {"method": "GET", "path": "/e", "handler": {"type": "workflow", "workflow_id": W}}).json()["route"]["id"]
    r = api.post(f"/projects/{P}/routes/{rid}/test", {"apply_schemas": True}).json()
    assert r["status"] == 502 and r["applied"] is False and r["error"]["node"] == "Stop"

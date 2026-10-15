"""Phase 5 — API section: route → test → infer → spec revisions → OpenAPI (J1 steps 8–19 shape)."""

from __future__ import annotations

import pytest

from .journeys._helpers import new_project

pytestmark = pytest.mark.integration


def test_phase5_route_test_infer_spec(api):
    P, S = new_project(api, "p5")
    api.post(f"/projects/{P}/secrets", {"key": "GREETING", "value": "hello-dev", "environment": "dev", "type": "string"})
    r = api.post(f"/projects/{P}/routes", {"method": "GET", "path": "/orders/{id}", "auth": "none",
                                           "handler": {"type": "workflow", "create": True}})
    assert r.status_code == 201, r.text
    out = r.json()
    route, R1 = out["route"], out["spec_revision"]
    assert route["name"] == "getOrdersById" and route["url"] == f"/api/p/{S}/orders/{{id}}"
    assert route["handler"]["type"] == "workflow" and route["handler"]["workflow_id"].startswith("wf_")
    assert isinstance(R1, int) and out["breaking_changes"] == [] and len(out["spec_hash"]) == 64
    W, R = route["handler"]["workflow_id"], route["id"]
    wf = api.get(f"/workflows/{W}").json()
    assert wf["name"] == "GET /orders/{id}" and {"Start", "Respond"} <= {n["type"] for n in wf["definition"]["nodes"]}
    assert any(h["route_id"] == R for h in next(w for w in api.get(f"/projects/{P}/workflows").json() if w["id"] == W)["handler_for"])

    r = api.raw("GET", f"/api/p/{S}/orders/42")
    assert r.status_code == 200 and r.json() == {"id": "42"} and r.headers["x-execution-id"].startswith("exec_")

    # Editor replaces the handler with a Set node (no Respond → response_mode auto)
    api.put(f"/workflows/{W}", {"name": "GET /orders/{id}", "nodes": [
        {"name": "Start", "type": "Start", "parameters": {}, "position": {"x": 0, "y": 0}},
        {"name": "Set", "type": "Set", "parameters": {"mode": "manual", "keepOnlySet": True, "fields": [
            {"name": "id", "value": "{{ $json.params.id }}"}, {"name": "greeting", "value": "{{ $vars.GREETING }}"}]},
         "position": {"x": 200, "y": 0}}],
        "connections": [{"source_node": "Start", "target_node": "Set", "source_output": "main", "target_input": "main"}], "settings": {}})

    test = {"params": {"id": "42"}, "query": {}, "headers": {}, "body": None, "environment": "dev", "apply_schemas": False}
    r = api.post(f"/projects/{P}/routes/{R}/test", test)
    assert r.status_code == 200, r.text
    t = r.json()
    assert t["status"] == 200 and t["body"] == {"id": "42", "greeting": "hello-dev"} and t["applied"] is False
    assert t["inferred"]["response_schema"]["properties"]["greeting"]["type"] == "string"
    assert isinstance(t["duration_ms"], float) and t["execution_id"].startswith("exec_")
    assert api.get(f"/projects/{P}/routes/{R}").json()["response_schema"] is None
    assert api.post(f"/projects/{P}/routes/{R}/test", {**test, "environment": "prod"}).json()["detail"]["code"] == "environment_not_draft"

    r = api.post(f"/projects/{P}/routes/{R}/test", {**test, "apply_schemas": True})
    assert r.json()["applied"] is True and r.json()["spec_revision"] == R1 + 1 and "route" in r.json()
    got = api.get(f"/projects/{P}/routes/{R}").json()
    assert set(got["response_schema"]["properties"]) == {"id", "greeting"} and got["validate_request"] is False

    revs = api.get(f"/projects/{P}/spec/revisions").json()
    assert max(x["revision"] for x in revs) == R1 + 1
    assert all({"spec_hash", "created_at", "changes"} <= set(x) for x in revs)
    d = api.get(f"/projects/{P}/spec/diff?from={R1}&to={R1 + 1}").json()
    assert d["changes"] and all(c["breaking"] is False for c in d["changes"])

    r = api.get(f"/projects/{P}/openapi.json?environment=dev")
    spec = r.json()
    assert spec["openapi"].startswith("3.1") and spec["servers"] == [{"url": f"/api/p/{S}"}]
    op = spec["paths"]["/orders/{id}"]["get"]
    assert op["operationId"] == "getOrdersById"
    assert "greeting" in op["responses"]["200"]["content"]["application/json"]["schema"]["properties"]
    assert r.headers["etag"].strip('"') and len(r.headers["etag"].strip('"')) == 64

    # Workflow used by a route can't be deleted
    assert api.delete(f"/workflows/{W}").json()["detail"]["code"] == "workflow_in_use"
    # dry-run delete previews the breaking change without deleting
    r = api.delete(f"/projects/{P}/routes/{R}?dry_run=true")
    assert r.json()["spec_revision"] is None and r.json()["breaking_changes"][0]["kind"] == "route_removed"
    assert api.get(f"/projects/{P}/routes/{R}").status_code == 200
    s = api.get(f"/projects/{P}/summary").json()
    assert s["counts"]["routes"] == 1 and s["stub_routes"] == 0 and s["api_keys"] == 0
    # route runs can be filtered by route id
    assert all(x["route_id"] == R and x["route"] == "GET /orders/{id}" for x in api.get(f"/projects/{P}/runs?route_id={R}").json())


def test_phase5_router_auth_errors_and_root_path(api):
    P, S = new_project(api, "p5b")
    api.post(f"/projects/{P}/routes", {"method": "GET", "path": "/", "handler": {"type": "workflow", "create": True}})
    assert api.raw("GET", f"/api/p/{S}").status_code == 200
    assert api.raw("GET", f"/api/p/{S}/", headers=bearer_bad()).json() == {"error": "unauthorized"}
    assert api.raw("GET", f"/api/p/{S}/", headers={"X-API-Key": "sk_dev_nope"}).status_code == 401


def bearer_bad():
    return {"Authorization": "Bearer not-a-token"}

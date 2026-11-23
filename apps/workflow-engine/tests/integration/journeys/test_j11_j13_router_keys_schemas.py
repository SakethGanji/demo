"""Journeys J11–J13 (UI-INTEGRATION.md §6): router semantics, API keys/auth modes, schemas + validate_request."""

from __future__ import annotations

import pytest

from ._helpers import SET_WF, bearer, chain, new_project, pending

pytestmark = pytest.mark.integration


def test_j11_router_semantics(api):
    P, S = new_project(api, "j11")                                                             # 1
    r = api.post(f"/projects/{P}/routes", {"method": "GET", "path": "/items", "handler": {"type": "stub"}})  # 2
    assert r.status_code == 201
    rt = r.json()["route"]
    assert (rt["name"], rt["handler"]["type"], rt["source"]) == ("getItems", "stub", "ui")
    RS = rt["id"]
    r = api.raw("GET", f"/api/p/{S}/items")                                                    # 3
    assert r.status_code == 501 and r.json() == {"error": "not_implemented", "route": "GET /items", "operation": "getItems"}
    r = api.raw("POST", f"/api/p/{S}/items")                                                   # 4
    assert r.status_code == 405 and r.json()["error"] == "method_not_allowed" and "GET" in r.headers["allow"]
    assert api.raw("GET", f"/api/p/{S}/nothing").json() == {"error": "route_not_found"}         # 5
    r = api.raw("GET", f"/api/p/no-such-project-{S}/x")
    assert r.status_code == 404 and r.json() == {"error": "project_not_found"}
    r = api.post(f"/projects/{P}/routes", {"method": "GET", "path": "/items", "handler": {"type": "stub"}})  # 6
    assert r.status_code == 409 and r.json()["detail"]["code"] == "route_conflict"
    assert api.post(f"/projects/{P}/routes", {"method": "GET", "path": "/a/{x}", "handler": {"type": "stub"}}).status_code == 201
    r = api.post(f"/projects/{P}/routes", {"method": "GET", "path": "/a/{y}", "handler": {"type": "stub"}})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "route_conflict"
    r = api.post(f"/projects/{P}/routes", {"method": "POST", "path": "/other", "name": "getItems", "handler": {"type": "stub"}})  # 7
    assert r.status_code == 409 and r.json()["detail"]["code"] == "name_taken"
    r = api.post(f"/projects/{P}/routes", {"method": "GET", "path": "items", "handler": {"type": "stub"}})
    assert r.status_code == 422 and r.json()["detail"]["code"] == "invalid_path"
    Q, _ = new_project(api, "j11q")                                                            # 8
    WQ = api.post(f"/projects/{Q}/workflows", SET_WF("{{ 1 }}")).json()["id"]
    r = api.post(f"/projects/{P}/routes", {"method": "GET", "path": "/q", "handler": {"type": "workflow", "workflow_id": WQ}})
    assert r.status_code == 422 and r.json()["detail"]["code"] == "handler_not_in_project"
    respond = {"name": "Respond", "type": "Respond", "position": {"x": 200, "y": 0},                # 9
               "parameters": {"statusCode": 201, "headers": [{"name": "X-Demo", "value": "yes"}],
                              "body": "{{ $json.params }}", "contentType": "application/json"}}
    WR = api.post(f"/projects/{P}/workflows", chain("respond", respond)).json()["id"]
    r = api.put(f"/projects/{P}/routes/{RS}", {"handler": {"type": "workflow", "workflow_id": WR}})  # 10
    assert r.status_code == 200 and r.json()["route"]["handler"]["workflow_id"] == WR and r.json()["breaking_changes"] == []
    r = api.raw("GET", f"/api/p/{S}/items")                                                    # 11
    assert r.status_code == 201 and r.headers["x-demo"] == "yes" and r.headers.get("x-execution-id")
    WS = api.post(f"/projects/{P}/workflows", SET_WF("{{ 42 }}")).json()["id"]                 # 12
    r = api.post(f"/projects/{P}/routes", {"method": "GET", "path": "/one", "response_mode": "all_items",
                                           "handler": {"type": "workflow", "workflow_id": WS}})
    assert r.status_code == 201
    one = r.json()["route"]["id"]
    assert api.raw("GET", f"/api/p/{S}/one").json() == [{"answer": 42}]
    assert api.put(f"/projects/{P}/routes/{one}", {"response_mode": "first_item"}).status_code == 200
    assert api.raw("GET", f"/api/p/{S}/one").json() == {"answer": 42}
    stop = {"name": "Stop", "type": "StopAndError", "parameters": {"errorMessage": "boom"}, "position": {"x": 200, "y": 0}}  # 13
    WE = api.post(f"/projects/{P}/workflows", chain("boom", stop)).json()["id"]
    assert api.post(f"/projects/{P}/routes", {"method": "GET", "path": "/boom", "handler": {"type": "workflow", "workflow_id": WE}}).status_code == 201
    r = api.raw("GET", f"/api/p/{S}/boom")
    assert r.status_code == 502 and r.json()["error"] == "handler_failed"
    XE = r.json()["execution_id"]
    assert XE and r.headers["x-execution-id"] == XE
    runs = api.get(f"/projects/{P}/runs?mode=api").json()                                      # 14
    assert any(x["id"] == XE and x["mode"] == "api" and x["status"] != "success" for x in runs)
    assert api.get(f"/projects/{P}/runs/{XE}").json()["errors"]
    r = api.post(f"/projects/{P}/routes/{RS}/test", {"params": {}, "query": {}, "headers": {}, "body": None,  # 15
                                                     "environment": "dev", "apply_schemas": False})
    assert r.status_code == 200 and r.json()["status"] == 201
    r = api.delete(f"/projects/{P}/routes/{RS}")                                               # 16
    assert r.status_code == 200 and isinstance(r.json()["spec_revision"], int)
    assert any(c["kind"] == "route_removed" and c["breaking"] for c in r.json()["breaking_changes"])
    assert api.raw("GET", f"/api/p/{S}/items").status_code == 404


def test_j12_api_keys_and_auth_modes(api):
    P, S = new_project(api, "j12")                                                             # 1
    r = api.post(f"/projects/{P}/routes", {"method": "GET", "path": "/admin/stats", "auth": "api_key",
                                           "handler": {"type": "workflow", "create": True}})  # 2
    assert r.status_code == 201
    r = api.post(f"/projects/{P}/routes", {"method": "GET", "path": "/me", "auth": "app_token",
                                           "handler": {"type": "workflow", "create": True}})
    assert r.status_code == 201
    r = api.raw("GET", f"/api/p/{S}/admin/stats")                                              # 3
    assert r.status_code == 401 and r.json() == {"error": "unauthorized"}
    assert api.raw("GET", f"/api/p/{S}/me").status_code == 401
    r = api.post(f"/projects/{P}/api-keys", {"name": "ci", "environment": "dev"})               # 4
    assert r.status_code == 201
    k = r.json()
    assert k["name"] == "ci" and k["environment"] == "dev" and k["prefix"].startswith("sk_dev_") and k["key"].startswith("sk_dev_")
    KID, KEY = k["id"], k["key"]
    keys = api.get(f"/projects/{P}/api-keys").json()                                           # 5
    assert any(x["id"] == KID and x["prefix"] == k["prefix"] for x in keys) and not any("key" in x for x in keys)
    r = api.raw("GET", f"/api/p/{S}/admin/stats", headers=bearer(KEY))                          # 6
    assert r.status_code == 200
    assert api.get(f"/projects/{P}/runs/{r.headers['x-execution-id']}").json()["environment"] == "dev"
    assert api.raw("GET", f"/api/p/{S}/admin/stats", headers={"X-API-Key": KEY}).status_code == 200  # 7
    assert api.raw("GET", f"/api/p/{S}/me", headers=bearer(KEY)).status_code == 200            # 8
    A = api.post(f"/projects/{P}/apps", {"name": "a", "definition": {}}).json()["id"]          # 9
    PT = api.post(f"/projects/{P}/apps/{A}/preview-token").json()["token"]
    assert api.raw("GET", f"/api/p/{S}/me", headers=bearer(PT)).status_code == 200
    assert api.raw("GET", f"/api/p/{S}/admin/stats", headers=bearer(PT)).status_code == 401
    client = api.raw("GET", f"/api/projects/{P}/client.ts").text                               # 10 (phase 6)
    assert "getMe" in client and "getAdminStats" not in client
    r = api.post(f"/projects/{P}/api-keys", {"name": "prod", "environment": "prod"})            # 11
    assert r.status_code == 201 and r.json()["key"].startswith("sk_prod_")
    r = api.raw("GET", f"/api/p/{S}/admin/stats", headers=bearer(r.json()["key"]))
    assert r.status_code == 200
    assert api.get(f"/projects/{P}/runs/{r.headers['x-execution-id']}").json()["environment"] == "prod"
    assert api.delete(f"/projects/{P}/api-keys/{KID}").status_code == 200                      # 12
    assert next(x for x in api.get(f"/projects/{P}/api-keys").json() if x["id"] == KID)["revoked_at"]
    assert api.raw("GET", f"/api/p/{S}/admin/stats", headers=bearer(KEY)).status_code == 401


def test_j13_schema_editing_and_validate_request(api):
    P, S = new_project(api, "j13")                                                             # 1
    schema = {"type": "object", "properties": {"body": {"type": "object", "properties": {"total": {"type": "number"}}, "required": ["total"]}}}
    r = api.post(f"/projects/{P}/routes", {"method": "POST", "path": "/orders", "auth": "none", "request_schema": schema,
                                           "handler": {"type": "workflow", "create": True}})
    assert r.status_code == 201 and r.json()["route"]["validate_request"] is False
    R = r.json()["route"]["id"]
    assert api.raw("POST", f"/api/p/{S}/orders", json={"total": "x"}).status_code == 200       # 2
    r = api.put(f"/projects/{P}/routes/{R}?dry_run=true", {"validate_request": True})          # 3
    assert r.status_code == 200 and isinstance(r.json()["breaking_changes"], list)
    assert api.get(f"/projects/{P}/routes/{R}").json()["validate_request"] is False
    r = api.put(f"/projects/{P}/routes/{R}", {"validate_request": True})                       # 4
    assert r.status_code == 200 and r.json()["route"]["validate_request"] is True
    r = api.raw("POST", f"/api/p/{S}/orders", json={"total": "x"})                             # 5
    assert r.status_code == 422 and r.json()["error"] == "request_invalid"
    assert any(e["path"] == "body.total" for e in r.json()["errors"])
    assert api.raw("POST", f"/api/p/{S}/orders", json={"total": 9.5}).status_code == 200
    assert api.raw("POST", f"/api/p/{S}/orders", json={}).status_code == 422
    r = api.post(f"/projects/{P}/routes/{R}/test", {"params": {}, "query": {}, "headers": {}, "body": {"total": 9.5},  # 6
                                                    "environment": "dev", "apply_schemas": True})
    assert r.status_code == 200 and r.json()["applied"] is True
    assert api.get(f"/projects/{P}/routes/{R}").json()["validate_request"] is True
    new = {"type": "object", "properties": {"body": {"type": "object", "properties": {"total": {"type": "number"}, "currency": {"type": "string"}},
                                                     "required": ["total", "currency"]}}}
    r = api.put(f"/projects/{P}/routes/{R}?dry_run=true", {"request_schema": new})             # 7
    assert r.status_code == 200
    assert any(c["breaking"] and "currency" in (c["field"] or "") for c in r.json()["breaking_changes"])

"""Journeys J7–J10 (UI-INTEGRATION.md §6)."""

from __future__ import annotations

import pytest

from ..conftest import uniq
from ._helpers import SET_WF, SIMPLE_APP, app_token, bearer, needs_esbuild, new_project, pending, save_app

pytestmark = pytest.mark.integration


def _move(api, wid: str, project_id: str):
    wf = api.get(f"/workflows/{wid}").json()
    return api.put(f"/workflows/{wid}", {"name": wf["name"], "nodes": wf["definition"]["nodes"],
                                          "connections": wf["definition"]["connections"], "project_id": project_id})


def test_j07_settings_environments_moves_delete(api):
    P, _ = new_project(api, "j7")                                                              # 1
    P2, _ = new_project(api, "j7b")
    r = api.put(f"/projects/{P}", {"name": "Renamed", "description": "d"})                     # 2
    assert (r.json()["name"], r.json()["description"]) == ("Renamed", "d")
    three = [{"name": "dev", "kind": "draft"}, {"name": "staging", "kind": "draft"}, {"name": "prod", "kind": "release"}]
    assert len(api.put(f"/projects/{P}", {"environments": three}).json()["environments"]) == 3  # 3
    assert api.put(f"/projects/{P}", {"default_environment": "nope"}).status_code == 422      # 4
    SID = api.post(f"/projects/{P}/secrets", {"key": "STG", "value": "x", "environment": "staging", "type": "string"}).json()["id"]  # 5
    two = [{"name": "dev", "kind": "draft"}, {"name": "prod", "kind": "release"}]
    r = api.put(f"/projects/{P}", {"environments": two})                                       # 6
    assert r.status_code == 409 and r.json()["detail"]["code"] == "environment_in_use"
    assert api.delete(f"/projects/{P}/secrets/{SID}").status_code == 200                       # 7
    r = api.put(f"/projects/{P}", {"environments": two})
    assert r.status_code == 200 and len(r.json()["environments"]) == 2
    r = api.put(f"/projects/{P}", {"settings": {"egress_allowlist": ["127.0.0.1"]}})           # 8
    assert r.json()["settings"]["egress_allowlist"] == ["127.0.0.1"]
    W = api.post(f"/projects/{P}/workflows", SET_WF("{{ 1 }}")).json()["id"]                   # 9
    r = api.post(f"/projects/{P}/routes", {"method": "GET", "path": "/ping", "handler": {"type": "workflow", "workflow_id": W}})
    assert r.status_code == 201
    route_id = r.json()["route"]["id"]
    r = _move(api, W, P2)                                                                      # 10
    assert r.status_code == 409 and r.json()["detail"]["code"] == "workflow_in_use"
    W2 = api.post(f"/projects/{P}/workflows", SET_WF("{{ 2 }}")).json()["id"]                  # 11
    r = _move(api, W2, P2)
    assert r.status_code == 200 and r.json()["project_id"] == P2
    assert any(w["id"] == W2 for w in api.get(f"/projects/{P2}/workflows").json())
    A = api.post(f"/projects/{P}/apps", {"name": uniq("a"), "definition": {}}).json()["id"]   # 12
    r = api.put(f"/apps/{A}", {"project_id": P2})
    assert r.status_code == 200 and r.json()["project_id"] == P2
    r = api.delete(f"/projects/{P}")                                                           # 13
    assert r.status_code == 409 and r.json()["detail"]["code"] == "project_not_empty"
    assert api.delete("/projects/proj_default").json()["detail"]["code"] == "default_project"  # 14
    assert api.delete(f"/projects/{P}/routes/{route_id}").status_code == 200                   # 15
    assert api.delete(f"/workflows/{W}").status_code == 200
    assert api.delete(f"/projects/{P}").json() == {"success": True}
    assert api.get(f"/projects/{P}").status_code == 404


def _answer(api, wid: str, env: str):
    ev = api.stream_events(f"/execution-stream/{wid}", {"input_data": None, "environment": env})
    return next(e for e in ev if e["type"] == "node:complete" and e.get("nodeName") == "Set")["data"][0]["json"]["answer"]


def test_j08_secrets(api):
    u = uniq("u").replace("-", "_").upper()
    P, _ = new_project(api, "j8")                                                              # 1
    TID = api.post("/variables", {"team_id": "default", "key": "TOKEN_" + u, "value": "a", "environment": "dev", "type": "string"}).json()["id"]
    r = api.post(f"/projects/{P}/secrets", {"key": "TOKEN_" + u, "value": "b", "environment": "dev", "type": "string"})  # 2
    assert r.status_code == 201 and r.json()["scope"] == "project" and r.json()["overrides_team"] is True
    PID = r.json()["id"]
    lst = api.get(f"/projects/{P}/secrets?environment=dev").json()                             # 3
    assert any(x["id"] == PID and x["scope"] == "project" and x["overrides_team"] and x["value"] == "b" for x in lst)
    assert any(x["id"] == TID and x["scope"] == "team" and x["overridden"] for x in lst)
    W = api.post(f"/projects/{P}/workflows", SET_WF("{{ $vars.TOKEN_" + u + " }}")).json()["id"]  # 4
    assert _answer(api, W, "dev") == "b"
    WD = api.post("/workflows", SET_WF("{{ $vars.TOKEN_" + u + " }}")).json()["id"]            # 5
    assert _answer(api, WD, "dev") == "a"
    r = api.post(f"/projects/{P}/secrets", {"key": "SK_" + u, "value": "s3cret-value", "environment": "dev", "type": "secret", "description": "d"})  # 6
    assert r.status_code == 201 and r.json()["value"] is None
    SKID = r.json()["id"]
    assert any(x["key"] == "SK_" + u and x["value"] is None for x in api.get(f"/projects/{P}/secrets").json())
    assert api.put(f"/projects/{P}/secrets/{SKID}", {"value": "rotated-value"}).json()["description"] == "d"  # 7
    assert api.put(f"/projects/{P}/secrets/{SKID}", {"allowed_hosts": ["api.example.com"]}).json()["allowed_hosts"] == ["api.example.com"]  # 8
    assert api.put(f"/projects/{P}/secrets/{TID}", {"value": "x"}).status_code == 404          # 9
    r = api.post(f"/projects/{P}/secrets", {"key": "TOKEN_" + u, "value": "b", "environment": "dev", "type": "string"})  # 10
    assert r.status_code == 409 and r.json()["detail"]["code"] == "exists"
    r = api.post(f"/projects/{P}/secrets", {"key": "1BAD", "value": "x", "environment": "dev", "type": "string"})  # 11
    assert r.status_code == 422 and r.json()["detail"]["code"] == "invalid_key"
    r = api.post(f"/projects/{P}/secrets", {"key": "OK", "value": "x", "environment": "nope", "type": "string"})   # 12
    assert r.status_code == 422 and r.json()["detail"]["code"] == "unknown_environment"
    W2 = api.post(f"/projects/{P}/workflows", SET_WF("{{ $vars.NOPE_" + u + " }}")).json()["id"]  # 13
    m = next(x for x in api.get(f"/projects/{P}/secrets/missing").json() if x["key"] == "NOPE_" + u)
    assert m["missing_in"] == ["dev", "prod"] and m["referenced_by"][0]["kind"] == "workflow"
    assert m["referenced_by"][0]["id"] == W2 and m["referenced_by"][0]["node"] == "Set"
    m = next(x for x in api.get(f"/projects/{P}/secrets/missing?environment=dev").json() if x["key"] == "NOPE_" + u)
    assert m["missing_in"] == ["dev"]
    assert api.get(f"/projects/{P}/summary").json()["missing_secrets"] >= 1                     # 14
    assert api.post(f"/projects/{P}/secrets", {"key": "NOPE_" + u, "value": "y", "environment": "dev", "type": "string"}).status_code == 201  # 15
    m = next(x for x in api.get(f"/projects/{P}/secrets/missing").json() if x["key"] == "NOPE_" + u)
    assert m["missing_in"] == ["prod"]
    assert api.delete(f"/projects/{P}/secrets/{PID}").status_code == 200                       # 16
    assert _answer(api, W, "dev") == "a"
    team_view = {x["id"] for x in api.get("/variables?environment=dev").json()}                # 17
    assert TID in team_view and PID not in team_view and SKID not in team_view
    api.delete(f"/variables/{TID}")


@needs_esbuild
def test_j09_published_app_runs_real_workflow(api):
    P, _ = new_project(api, "j9")                                                              # 1
    Q, _ = new_project(api, "j9q")
    W1 = api.post(f"/projects/{P}/workflows", SET_WF("{{ $json.body.x }}")).json()["id"]       # 2 (§11: body wrapper)
    W2 = api.post(f"/projects/{P}/workflows", SET_WF("{{ $vars.SV }}")).json()["id"]
    W3 = api.post(f"/projects/{P}/workflows", SET_WF("{{ 3 }}")).json()["id"]
    WQ = api.post(f"/projects/{Q}/workflows", SET_WF("{{ 4 }}")).json()["id"]
    assert api.post(f"/projects/{P}/secrets", {"key": "SV", "value": "pa55word-xyz", "environment": "prod", "type": "secret"}).status_code == 201  # 3
    A = api.post(f"/projects/{P}/apps", {"name": uniq("a"), "definition": {}}).json()["id"]   # 4
    r = api.put(f"/apps/{A}", {"workflow_ids": [W1, W2, WQ]})                                  # G22: refused at write
    assert r.status_code == 422 and r.json()["detail"]["code"] == "workflow_not_in_project" and r.json()["detail"]["workflow_ids"] == [WQ]
    WM = api.post(f"/projects/{P}/workflows", SET_WF("{{ 5 }}")).json()["id"]
    assert api.put(f"/apps/{A}", {"workflow_ids": [W1, W2, WM]}).json()["workflow_ids"] == [W1, W2, WM]
    save_app(api, A, SIMPLE_APP)                                                               # 5
    slug = uniq("run")
    assert api.post(f"/apps/{A}/publish", {"slug": slug, "access": "public"}).status_code == 200  # 6
    AT = app_token(api, slug)                                                                  # 7
    r = api.raw("POST", f"/a/{slug}/api/workflow/{W1}/run", json={"x": 5}, headers=bearer(AT))  # 8
    assert r.status_code == 200
    b = r.json()
    assert b["status"] == "success" and b["execution_id"].startswith("exec_") and b["errors"] == [] and b["data"][0]["answer"] == 5
    r = api.raw("POST", f"/a/{slug}/api/workflow/{W1}/run", json={"x": "{{ $vars.SV }}"}, headers=bearer(AT))  # 9
    assert r.json()["data"][0]["answer"] == "{{ $vars.SV }}"
    r = api.raw("POST", f"/a/{slug}/api/workflow/{W2}/run", json={}, headers=bearer(AT))       # 10
    assert r.json()["data"][0]["answer"] == "***"
    # 11: allow-listed workflow later moved to another project → refused at run time
    wf = api.get(f"/workflows/{WM}").json()
    api.put(f"/workflows/{WM}", {"name": wf["name"], "nodes": wf["definition"]["nodes"], "connections": wf["definition"]["connections"], "project_id": Q})
    r = api.raw("POST", f"/a/{slug}/api/workflow/{WM}/run", json={}, headers=bearer(AT))
    assert r.status_code == 403 and r.json()["detail"]["code"] == "workflow_not_in_project"
    assert api.raw("POST", f"/a/{slug}/api/workflow/{W3}/run", json={}, headers=bearer(AT)).status_code == 403  # 12
    assert api.raw("POST", f"/a/{slug}/api/workflow/{W1}/run", json={}, headers=bearer("garbage")).status_code == 401  # 13
    assert api.raw("POST", f"/a/{slug}/api/workflow/{W1}/run", json={}).status_code == 401
    assert any(x["id"] == b["execution_id"] and x["environment"] == "prod"
               for x in api.get(f"/projects/{P}/runs?limit=50").json())                       # 14


def test_j10_sdk_broker_policy(api, upstream):
    cat = api.get("/sdk/functions").json()                                                     # 1
    assert any(c["function"] == "http_request" and c["node_type"] == "HttpRequest" and c["egress"] for c in cat)
    assert not any(c["node_type"] in ("Webhook", "If") for c in cat)
    assert all(c["parameters"]["type"] == "object" for c in cat)
    sdk = api.get("/sdk/python").json()                                                        # 2
    assert len(sdk["version"]) == 64 and all(ch in "0123456789abcdef" for ch in sdk["version"])
    assert "studio/__init__.py" in sdk["files"]
    assert api.get("/sdk/python.zip").content[:2] == b"PK"                                     # 3
    P, _ = new_project(api, "j10")                                                             # 4
    r = api.post(f"/projects/{P}/broker-tokens", {"environment": "dev"})
    assert r.status_code == 201 and r.json()["token"].startswith("brk_") and r.json()["expires_at"]
    BT = r.json()["token"]
    assert api.post(f"/projects/{P}/broker-tokens", {"environment": "prod"}).status_code == 403  # 5
    call = {"function": "http_request", "parameters": {"method": "GET", "url": upstream + "/x",
            "headers": [{"name": "Authorization", "value": "Bearer {{ $vars.UP_KEY }}"}]}, "items": [{}]}
    r = api.raw("POST", "/api/broker/call", json={"function": "http_request", "parameters": {"method": "GET", "url": upstream + "/x"}})  # 6
    assert r.status_code == 401 and r.json()["ok"] is False and r.json()["error"]["code"] == "invalid_token"
    r = api.raw("POST", "/api/broker/call", json=call, headers=bearer(BT))                    # 7
    assert r.status_code == 422 and r.json()["ok"] is False and r.json()["call_id"]
    assert {k: r.json()["error"][k] for k in ("code", "key", "environment")} == {"code": "missing_secret", "key": "UP_KEY", "environment": "dev"}
    KID = api.post(f"/projects/{P}/secrets", {"key": "UP_KEY", "value": "tok-abc-123456", "environment": "dev", "type": "secret"}).json()["id"]  # 8
    r = api.raw("POST", "/api/broker/call", json=call, headers=bearer(BT))                    # 9
    assert r.status_code == 403 and r.json()["error"]["code"] == "secret_not_bound"
    assert api.put(f"/projects/{P}/secrets/{KID}", {"allowed_hosts": ["127.0.0.1"]}).status_code == 200  # 10
    r = api.raw("POST", "/api/broker/call", json=call, headers=bearer(BT))                    # 11 (§11: statusCode)
    assert r.status_code == 200 and r.json()["ok"] is True and r.json()["call_id"].startswith("bcall_")
    item = r.json()["items"][0]
    assert item["statusCode"] == 200 and item["body"]["headers"]["authorization"] == "Bearer ***"
    local = {**call, "parameters": {**call["parameters"], "url": upstream.replace("127.0.0.1", "localhost") + "/x"}}
    r = api.raw("POST", "/api/broker/call", json=local, headers=bearer(BT))                   # 12
    assert r.status_code == 403 and r.json()["error"]["code"] == "host_not_allowed" and r.json()["error"]["host"] == "localhost"
    r = api.raw("POST", "/api/broker/call", json={"function": "http_request", "parameters": {"method": "GET", "url": upstream + "/x", "credentialId": "cred_x"}}, headers=bearer(BT))  # 13
    assert r.status_code == 422 and r.json()["error"]["code"] == "credential_not_allowed"
    up = {**call, "parameters": {**call["parameters"], "headers": [{"name": "Authorization", "value": "Bearer {{ $vars.UP_KEY.toUpperCase() }}"}]}}
    r = api.raw("POST", "/api/broker/call", json=up, headers=bearer(BT))                      # 14
    assert r.status_code == 422 and r.json()["error"]["code"] == "secret_expression_not_allowed"
    r = api.raw("POST", "/api/broker/call", json={"function": "webhook", "parameters": {}}, headers=bearer(BT))  # 15
    assert r.status_code == 403 and r.json()["error"]["code"] == "function_not_allowed"
    log = api.get(f"/projects/{P}/broker-calls").json()                                        # 16
    assert len(log) >= 6 and not any("tok-abc-123456" in str(x) for x in log)
    assert any(x["status"] == "denied" and x["error_code"] == "host_not_allowed" for x in log)
    assert any(x["status"] == "ok" for x in log)

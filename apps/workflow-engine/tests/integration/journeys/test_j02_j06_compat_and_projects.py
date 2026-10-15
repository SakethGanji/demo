"""Journeys J2–J6 (UI-INTEGRATION.md §6): legacy editor/builder/variables/tester/chats, projects home."""

from __future__ import annotations

import pytest

from ..conftest import uniq
from ._helpers import SET_WF, SIMPLE_APP, needs_esbuild, new_project, pending

pytestmark = pytest.mark.integration


@pending(8)
def test_j02_legacy_editor_default_project(api):
    assert all("project_id" in w for w in api.get("/workflows").json())                      # 1
    r = api.post("/workflows", SET_WF("{{ 20 + 22 }}"))                                       # 2
    assert r.status_code == 201
    W = r.json()["id"]
    assert (r.json()["active"], r.json()["webhook_url"], r.json()["project_id"]) == (False, "/webhook/" + W, "proj_default")
    d = api.raw("GET", f"/api/workflows/{W}").json()                                         # 3 (editor.tsx:134 has no headers)
    assert d["project_id"] == "proj_default" and [n["name"] for n in d["definition"]["nodes"]] == ["Start", "Set"]
    assert W in {w["id"] for w in api.get("/workflows?project_id=proj_default").json()}       # 4
    assert any(w["id"] == W and w["project_id"] == "proj_default" for w in api.get("/projects/proj_default/workflows").json())
    p = api.get("/projects/proj_default").json()                                              # 5
    assert (p["default_environment"], p["live_environment"]) == ("default", "default")
    assert any(e["name"] == "default" for e in p["environments"])
    body = SET_WF("{{ 6 * 7 }}", name=d["name"])                                              # 6
    assert api.put(f"/workflows/{W}", body).status_code == 200
    ev = api.stream_events(f"/execution-stream/{W}", {"input_data": None, "environment": "default"})   # 7
    assert ev[0]["type"] == "execution:start" and ev[-1]["type"] == "execution:complete"
    assert any(e["type"] == "node:complete" and e.get("nodeName") == "Set" for e in ev)
    ev = api.stream_events("/execution-stream/adhoc", {**SET_WF("{{ 1 + 1 }}"), "input_data": None,      # 8
                                                       "environment": "default", "project_id": "proj_default"})
    assert ev[0]["type"] == "execution:start" and ev[-1]["type"] == "execution:complete"
    r = api.post("/workflows/run-adhoc", {**SET_WF("{{ 1 + 1 }}"), "environment": "default", "project_id": "proj_default"})  # 9
    assert r.status_code == 200 and r.json()["status"] == "success" and r.json()["errors"] == []
    r = api.post(f"/workflows/{W}/run")                                                       # 10
    assert r.status_code == 200 and r.json()["status"] == "success"
    X = r.json()["execution_id"]
    assert len([e for e in api.get(f"/executions?workflow_id={W}").json() if e["workflow_id"] == W]) >= 2  # 11
    assert any(x["id"] == X and x["environment"] == "default" for x in api.get("/projects/proj_default/runs?limit=50").json())  # 12
    det = api.get(f"/projects/proj_default/runs/{X}").json()                                  # 13
    assert det["status"] == "success" and "Set" in det["node_data"] and "trace" in det
    r = api.post(f"/workflows/{W}/publish", {"message": "v1"})                                # 14
    assert r.json()["active"] is True and isinstance(r.json()["version_id"], int)
    assert api.post(f"/workflows/{W}/unpublish").json()["active"] is False                    # 15
    types = {n["type"] for n in api.get("/nodes").json()}                                     # 16
    assert {"Start", "Set", "Respond", "DataTable", "PythonStep"} <= types
    resp = api.get("/nodes/Respond").json()
    assert {"type", "displayName", "description", "properties"} <= set(resp)
    assert api.get(f"/workflows/{W}").json()["project_id"] == "proj_default"                  # 17
    assert api.delete(f"/workflows/{W}").json()["success"] is True                            # 18


def test_j03_legacy_app_builder_and_published_app(api):
    r = api.post("/apps", {"name": uniq("app"), "definition": {"sourceCode": None}})          # 1
    assert r.status_code == 201 and r.json()["project_id"] == "proj_default"
    A = r.json()["id"]
    assert any(a["id"] == A for a in api.get("/apps").json())                                 # 2
    assert any(a["id"] == A for a in api.get("/apps?project_id=proj_default").json())
    assert api.put(f"/apps/{A}", {"name": "Renamed"}).json()["name"] == "Renamed"             # 3
    files = [{"path": "App.tsx", "content": SIMPLE_APP}]
    r = api.put(f"/apps/{A}", {"source_code": SIMPLE_APP, "files": files, "create_version": True, "version_trigger": "manual"})  # 4
    assert r.json()["current_version"]["version_number"] == 1
    r = api.put(f"/apps/{A}", {"source_code": SIMPLE_APP, "files": files, "create_version": True,               # 5
                               "version_trigger": "ai", "version_prompt": "make it blue"})
    assert r.json()["current_version"]["version_number"] == 2
    vers = api.get(f"/apps/{A}/versions").json()                                              # 6
    assert len(vers) == 2 and {"id", "version_number", "trigger", "label", "prompt", "created_at"} <= set(vers[0])
    V1 = next(v["id"] for v in vers if v["version_number"] == 1)
    assert api.patch(f"/apps/{A}/versions/{V1}", {"label": "first"}).json()["label"] == "first"  # 7
    assert api.get(f"/apps/{A}/versions/{V1}").json()["source_code"] == SIMPLE_APP           # 8
    assert api.post(f"/apps/{A}/versions", {"source_code": SIMPLE_APP, "trigger": "manual"}).status_code == 201  # 9
    r = api.post(f"/apps/{A}/versions/{V1}/revert")                                           # 10
    assert r.status_code == 200 and r.json().get("current_version") is not None
    assert api.put(f"/apps/{A}", {"api_execution_ids": []}).status_code == 200                # 11
    assert api.delete(f"/apps/{A}").json() == {"success": True}                               # 16


@needs_esbuild
def test_j03_publish_steps(api):
    A = api.post("/apps", {"name": uniq("app"), "definition": {"sourceCode": None}}).json()["id"]
    api.put(f"/apps/{A}", {"source_code": SIMPLE_APP, "files": [{"path": "App.tsx", "content": SIMPLE_APP}],
                           "create_version": True, "version_trigger": "manual"})
    slug = uniq("pub")
    r = api.post(f"/apps/{A}/publish", {"slug": slug, "access": "public"})                   # 12
    assert r.status_code == 200
    b = r.json()
    assert b["active"] is True and isinstance(b["version_id"], int) and b["bundle_hash"] and b["public_url"]
    d = api.get(f"/apps/{A}").json()                                                          # 13
    assert d["active"] is True and d["published_version"] and d["slug"] == slug
    assert "__APP_TOKEN" in api.raw("GET", f"/a/{slug}").text                                  # 14
    assert api.post(f"/apps/{A}/unpublish").json()["active"] is False                         # 15
    assert api.raw("GET", f"/a/{slug}").status_code == 404


def test_j04_team_variables_modal(api):
    assert isinstance(api.raw("GET", "/api/variables/environments").json(), list)             # 1 (G31: no content-type)
    K = "J4_" + uniq("k").replace("-", "_").upper()
    r = api.post("/variables", {"team_id": "default", "key": K, "value": "v1", "environment": "default", "type": "secret"})  # 2
    assert r.status_code == 201 and r.json()["value"] is None
    vid = r.json()["id"]
    assert any(v["key"] == K and v["value"] is None and v["type"] == "secret"
               for v in api.raw("GET", "/api/variables?environment=default").json())          # 3
    assert api.put(f"/variables/{vid}", {"value": "v2", "description": None}).status_code == 200  # 4
    assert api.post("/variables", {"team_id": "default", "key": K, "value": "x", "environment": "default", "type": "secret"}).status_code == 409  # 5
    P, _ = new_project(api, "j4")                                                             # 6
    assert api.post(f"/projects/{P}/secrets", {"key": K, "value": "p", "environment": "dev", "type": "secret"}).status_code == 201
    assert not any(v["key"] == K for v in api.raw("GET", "/api/variables?environment=dev").json())
    assert api.raw("DELETE", f"/api/variables/{vid}").json()["success"] is True              # 7


def test_j05_api_tester_and_ai_chats(api, upstream):
    r = api.post("/api-tester/execute", {"name": None, "method": "GET", "url": upstream + "/echo?x=1", "headers": {}})  # 1
    assert r.status_code == 201 and r.json()["response_status"] == 200 and r.json()["error"] is None
    E = r.json()["id"]
    assert any(e["id"] == E for e in api.get("/api-tester/executions").json())                # 2
    assert api.get(f"/api-tester/executions/{E}").json()["method"] == "GET"
    assert api.patch(f"/api-tester/executions/{E}", {"name": "echo"}).json()["name"] == "echo"  # 3
    assert api.delete(f"/api-tester/executions/{E}").status_code == 200                       # 4
    # [LLM] steps: assert the endpoint contract (accepted, streams) — no LLM key is configured.
    for path, body in [
        ("/api/ai/chat", {"message": "hi", "session_id": uniq(), "workflow_context": None, "conversation_history": [], "mode_hint": "auto"}),  # 5 (§11: studio sends "auto")
        ("/api/ai/app-builder/chat", {"message": "hi", "api_execution_ids": [], "conversation_history": []}),  # 6
    ]:
        with api.http.stream("POST", path, json=body, headers={"Content-Type": "application/json"}, timeout=60) as resp:
            assert resp.status_code != 422 and resp.status_code == 200


def test_j05_step7_app_builder_chat_with_project_context(api):
    P, _ = new_project(api, "j5")
    R = api.post(f"/projects/{P}/routes", {"method": "GET", "path": "/x", "handler": {"type": "stub"}}).json()["route"]["id"]
    A = api.post(f"/projects/{P}/apps", {"name": uniq("a"), "definition": {}}).json()["id"]
    body = {"message": "hi", "api_execution_ids": [], "conversation_history": [], "app_id": A, "project_id": P, "route_ids": [R]}
    with api.http.stream("POST", "/api/ai/app-builder/chat", json=body, headers={"Content-Type": "application/json"}, timeout=60) as resp:
        assert resp.status_code == 200


def test_j06_projects_home_shell_redirects(api):
    r = api.post("/projects", {"name": "Billing " + uniq()})                                  # 1
    assert r.status_code == 201
    P, S = r.json()["id"], r.json()["slug"]
    assert S and S == S.lower()
    r = api.post("/projects", {"name": "Other", "slug": S})                                   # 2
    assert r.status_code == 409 and r.json()["detail"]["code"] == "slug_taken"
    assert api.post("/projects", {"name": ""}).status_code == 422                            # 3
    assert api.get("/projects/proj_doesnotexist").status_code == 404                          # 4
    r = api.post(f"/projects/{P}/workflows", SET_WF("{{ 1 }}"))                               # 5
    assert r.status_code == 201 and r.json()["project_id"] == P
    W = r.json()["id"]
    assert [w["id"] for w in api.get(f"/projects/{P}/workflows").json()] == [W]               # 6
    assert [w["id"] for w in api.get(f"/workflows?project_id={P}").json()] == [W]
    allw = [w["id"] for w in api.get("/workflows").json()]
    assert W in allw and len(allw) > 1
    r = api.post(f"/projects/{P}/apps", {"name": "Billing UI", "definition": {"sourceCode": None}})  # 7
    A = r.json()["id"]
    assert r.status_code == 201 and r.json()["project_id"] == P
    assert [a["id"] for a in api.get(f"/projects/{P}/apps").json()] == [A]
    assert [a["id"] for a in api.get(f"/apps?project_id={P}").json()] == [A]
    assert api.get(f"/workflows/{W}").json()["project_id"] == P                               # 8
    assert api.get(f"/apps/{A}").json()["project_id"] == P
    X = api.post(f"/workflows/{W}/run").json()["execution_id"]                                # 9
    s = api.get(f"/projects/{P}/summary").json()                                              # 10
    assert s["counts"]["workflows"] == 1 and s["counts"]["apps"] == 1 and s["last_run"]["execution_id"] == X
    item = next(p for p in api.get("/projects").json() if p["id"] == P)                       # 11
    assert item["counts"]["workflows"] == 1 and item["last_run"]["execution_id"] == X
    Q, _ = new_project(api, "j6q")                                                            # 12
    assert api.get(f"/projects/{Q}/runs/{X}").status_code == 404

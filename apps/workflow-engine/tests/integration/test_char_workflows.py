"""Characterization: workflow endpoints the studio uses (BACKEND-DESIGN §1).

Payload shapes mirror studio:shared/lib/api.ts and features/*/hooks.
"""

from __future__ import annotations

import pytest

from .conftest import start_set_workflow, uniq

pytestmark = pytest.mark.integration


def test_workflow_lifecycle_like_editor(api):
    # POST /api/workflows (useWorkflowApi.ts:33)
    wf = start_set_workflow()
    r = api.post("/workflows", wf)
    assert r.status_code == 201, r.text
    created = r.json()
    assert set(created) >= {"id", "name", "active", "webhook_url", "created_at"}
    wid = created["id"]
    assert created["active"] is False
    assert created["webhook_url"] == f"/webhook/{wid}"

    # GET /api/workflows (useWorkflows.ts:56) then GET /{id} per item (N+1)
    lst = api.get("/workflows").json()
    item = next(w for w in lst if w["id"] == wid)
    assert set(item) >= {"id", "name", "active", "webhook_url", "node_count", "created_at", "updated_at"}
    assert item["node_count"] == 2

    # editor.tsx:134 direct fetch (no headers)
    r = api.raw("GET", f"/api/workflows/{wid}")
    assert r.status_code == 200
    detail = r.json()
    assert set(detail) >= {"id", "name", "active", "webhook_url", "definition", "created_at", "updated_at"}
    assert [n["name"] for n in detail["definition"]["nodes"]] == ["Start", "Set"]
    assert detail["definition"]["connections"][0]["source_node"] == "Start"

    # PUT /api/workflows/{id} (editor save)
    wf["name"] = wf["name"] + "-renamed"
    wf["nodes"][1]["parameters"]["fields"][0]["value"] = "{{ 6 * 7 }}"
    r = api.put(f"/workflows/{wid}", wf)
    assert r.status_code == 200, r.text
    assert r.json()["name"].endswith("-renamed")

    # POST /api/workflows/{id}/run (useWorkflowActions.ts:26 — no body)
    r = api.post(f"/workflows/{wid}/run")
    assert r.status_code == 200, r.text
    run = r.json()
    assert run["status"] == "success"
    assert run["execution_id"].startswith("exec_")
    assert run["data"]["Set"] == [{"json": {"answer": 42}}]
    assert run["errors"] == []

    # publish / versions / unpublish
    r = api.post(f"/workflows/{wid}/publish", {"message": "v1"})
    assert r.status_code == 200, r.text
    pub = r.json()
    assert pub["active"] is True and isinstance(pub["version_id"], int)
    vers = api.get(f"/workflows/{wid}/versions").json()
    assert len(vers) == 1 and vers[0]["version_number"] == 1 and vers[0]["message"] == "v1"
    v = api.get(f"/workflows/{wid}/versions/{vers[0]['id']}").json()
    assert "definition" in v
    r = api.post(f"/workflows/{wid}/publish", {})
    assert r.status_code == 200
    assert len(api.get(f"/workflows/{wid}/versions").json()) == 2
    r = api.post(f"/workflows/{wid}/unpublish")
    assert r.status_code == 200 and r.json() == {"id": wid, "active": False, "version_id": None}

    # executions list shows the run
    ex = api.get("/executions").json()
    mine = [e for e in ex if e["workflow_id"] == wid]
    assert mine and set(mine[0]) >= {"id", "workflow_id", "workflow_name", "status", "mode", "start_time", "end_time", "error_count"}
    assert mine[0]["status"] == "success"
    d = api.get(f"/executions/{mine[0]['id']}").json()
    assert d["node_data"]["Set"] == [{"json": {"answer": 42}}]

    # DELETE (useWorkflowActions.ts:85)
    r = api.delete(f"/workflows/{wid}")
    assert r.status_code == 200 and r.json()["success"] is True
    assert api.get(f"/workflows/{wid}").status_code == 404


def test_create_validation_and_missing(api):
    assert api.post("/workflows", {"name": "x", "nodes": [], "connections": []}).status_code == 422
    assert api.get("/workflows/wf_nope").status_code == 404
    assert api.put("/workflows/wf_nope", start_set_workflow()).status_code == 404
    assert api.delete("/workflows/wf_nope").status_code == 404
    assert api.post("/workflows/wf_nope/run").status_code == 404


def test_run_with_input_data_is_webhook_shaped(api):
    wf = start_set_workflow(expr="{{ $json.body.n * 2 }}", field="doubled")
    wid = api.post("/workflows", wf).json()["id"]
    r = api.post(f"/workflows/{wid}/run", {"input_data": {"n": 21}, "environment": "default"})
    assert r.status_code == 200
    assert r.json()["data"]["Set"] == [{"json": {"doubled": 42}}]


def test_run_adhoc(api):
    wf = start_set_workflow(name=uniq("adhoc"))
    r = api.post("/workflows/run-adhoc", wf)
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "success"
    assert r.json()["data"]["Set"][0]["json"]["answer"] == 42


def test_execution_stream_saved_and_adhoc(api):
    wf = start_set_workflow()
    wid = api.post("/workflows", wf).json()["id"]
    # useExecutionStream.ts:117,134
    events = api.stream_events(f"/execution-stream/{wid}", {"input_data": None, "environment": "default"})
    types = [e.get("type") for e in events]
    assert types[0] == "execution:start" and types[-1] == "execution:complete", types
    assert "node:complete" in types
    done = [e for e in events if e.get("type") == "node:complete" and e.get("nodeName") == "Set"]
    assert done and done[0]["data"][0]["json"]["answer"] == 42

    # useExecutionStream.ts:126,134
    events = api.stream_events("/execution-stream/adhoc", {**start_set_workflow(), "input_data": None, "environment": "default"})
    assert [e.get("type") for e in events][-1] == "execution:complete"


def test_nodes_catalogue(api):
    r = api.get("/nodes")
    assert r.status_code == 200
    types = {n["type"] for n in r.json()}
    for t in ["Start", "Webhook", "Cron", "If", "Switch", "Merge", "Set", "Code", "HttpRequest", "AIAgent", "LLMChat"]:
        assert t in types
    one = next(n for n in r.json() if n["type"] == "Set")
    assert set(one) >= {"type", "displayName", "description", "properties"}
    assert api.get("/nodes/Set").status_code == 200


def test_execution_detail_missing(api):
    assert api.get("/executions/exec_nope").status_code == 404

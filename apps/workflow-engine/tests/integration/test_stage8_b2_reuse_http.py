"""Stage 8 B2 over HTTP on real Postgres: child execution rows, publish rules, delete protection, the reference index,
the startup backfill and cascade delete (DESIGN-STAGE8-REUSE 3.3, 4.3, 5.1, 10)."""

from __future__ import annotations

import pytest

from .conftest import EngineServer, StudioClient, uniq

pytestmark = pytest.mark.integration


def child_body(tag: str = "v1") -> dict:
    return {
        "name": uniq("s8-child"),
        "nodes": [
            {"name": "Inputs", "type": "ExecuteWorkflowTrigger", "parameters": {}, "position": {"x": 0, "y": 0}},
            {"name": "Mark", "type": "Set", "parameters": {"mode": "manual", "fields": [{"name": "tag", "value": tag}]},
             "position": {"x": 200, "y": 0}},
        ],
        "connections": [{"source_node": "Inputs", "target_node": "Mark", "source_output": "main", "target_input": "main"}],
        "settings": {},
    }


def parent_body(child_id: str, version: str | int | None = "latest", name: str | None = None) -> dict:
    params: dict = {"workflowId": child_id, "inputMode": "passThrough"}
    if version is not None:
        params["version"] = version
    return {
        "name": name or uniq("s8-parent"),
        "nodes": [
            {"name": "Start", "type": "Start", "parameters": {}, "position": {"x": 0, "y": 0}},
            {"name": "call", "type": "ExecuteWorkflow", "parameters": params, "position": {"x": 200, "y": 0}},
        ],
        "connections": [{"source_node": "Start", "target_node": "call", "source_output": "main", "target_input": "main"}],
        "settings": {},
    }


def make(api, body: dict) -> str:
    r = api.post("/workflows", body)
    assert r.status_code == 201, r.text
    return r.json()["id"]


def publish(api, wid: str):
    return api.post(f"/workflows/{wid}/publish", {})


def refs(db, parent_id: str) -> list[tuple]:
    return db.query("SELECT parent_version_id IS NULL, node_name, child_workflow_id, version_spec FROM workflow_refs "
                    "WHERE parent_workflow_id = %s ORDER BY 1, node_name", (parent_id,))


def test_a_run_writes_the_child_row_and_deleting_the_root_cascades(api, test_db):
    child = make(api, child_body())
    assert publish(api, child).status_code == 200
    parent = make(api, parent_body(child))
    r = api.post(f"/workflows/{parent}/run")
    assert r.status_code == 200, r.text
    run = r.json()
    assert run["status"] == "success", run
    root = run["execution_id"]

    [row] = test_db.query("SELECT id, mode, status, parent_execution_id, parent_node_name, depth, root_execution_id, "
                          "workflow_version_id FROM executions WHERE workflow_id = %s", (child,))
    child_exec = row[0]
    assert row[1:7] == ("sub_workflow", "success", root, "call", 1, root) and row[7] is not None
    assert test_db.query("SELECT count(*) FROM node_outputs WHERE execution_id = %s", (child_exec,))[0][0] >= 2

    # the default run list shows the root and hides the child
    ids = {e["id"] for e in api.get("/executions").json()}
    assert root in ids and child_exec not in ids

    assert api.delete(f"/executions/{root}").status_code == 200
    assert test_db.query("SELECT 1 FROM executions WHERE id IN (%s, %s)", (root, child_exec)) == []
    assert test_db.query("SELECT 1 FROM node_outputs WHERE execution_id = %s", (child_exec,)) == []


def test_the_pinned_version_decides_what_runs(api, test_db):
    child = make(api, child_body("one"))
    assert publish(api, child).status_code == 200
    body = child_body("two")
    put = api.put(f"/workflows/{child}", {"nodes": body["nodes"], "connections": body["connections"]})
    assert put.status_code == 200, put.text
    assert publish(api, child).status_code == 200
    latest = make(api, parent_body(child, "latest"))
    pinned = make(api, parent_body(child, 1))
    assert api.post(f"/workflows/{latest}/run").json()["data"]["call"][0]["json"]["tag"] == "two"
    assert api.post(f"/workflows/{pinned}/run").json()["data"]["call"][0]["json"]["tag"] == "one"
    versions = {r[0] for r in test_db.query("SELECT workflow_version_id FROM executions WHERE workflow_id = %s", (child,))}
    assert len(versions) == 2 and None not in versions


def test_publish_rules(api):
    child = make(api, child_body())
    unpublished_child = make(api, child_body())
    assert publish(api, child).status_code == 200

    def findings(wid):
        r = publish(api, wid)
        assert r.status_code == 422, r.text
        return {(f["code"], f["node"]) for f in r.json()["detail"]["findings"]}

    assert ("WF023", "call") in findings(make(api, parent_body(child, "draft")))
    assert ("WF023", "call") in findings(make(api, parent_body(child, None)))          # legacy: no version at all
    assert ("WF022", "call") in findings(make(api, parent_body(unpublished_child, "latest")))
    assert ("WF022", "call") in findings(make(api, parent_body(child, 9)))
    assert publish(api, make(api, parent_body(child, "latest"))).status_code == 200
    assert publish(api, make(api, parent_body(child, 1))).status_code == 200
    assert publish(api, child).status_code == 200                                      # a child is never blocked by its parents


def test_delete_protection_and_the_reference_index(api, test_db):
    child = make(api, child_body())
    assert publish(api, child).status_code == 200
    draft_parent = make(api, parent_body(child, "draft"))
    live_parent = make(api, parent_body(child, "latest"))
    assert publish(api, live_parent).status_code == 200

    assert refs(test_db, draft_parent) == [(True, "call", child, "draft")]             # saved draft: one row
    assert refs(test_db, live_parent) == [(False, "call", child, "latest"), (True, "call", child, "latest")]   # draft + version

    r = api.delete(f"/workflows/{child}")
    assert r.status_code == 409, r.text
    detail = r.json()["detail"]
    assert detail["code"] == "WORKFLOW_IN_USE"
    assert api.get(f"/workflows/{child}").status_code == 200

    # editing the draft parent changes the index; the published version still blocks until it is replaced
    new = parent_body(child, "latest")
    api.put(f"/workflows/{draft_parent}", {"nodes": new["nodes"], "connections": new["connections"]})
    assert refs(test_db, draft_parent) == [(True, "call", child, "latest")]
    assert api.delete(f"/workflows/{child}").status_code == 409

    assert api.delete(f"/workflows/{live_parent}").status_code == 200                  # the published caller goes...
    assert refs(test_db, live_parent) == []                                            # ...taking its rows with it
    assert api.delete(f"/workflows/{child}").status_code == 200                        # a draft-only caller does not block
    assert api.post(f"/workflows/{draft_parent}/check", {}).status_code == 200        # and its next check just reports it


def test_the_startup_backfill_rebuilds_an_empty_index(api, test_db, engine):
    child = make(api, child_body())
    assert publish(api, child).status_code == 200
    parent = make(api, parent_body(child, "latest"))
    assert publish(api, parent).status_code == 200
    before = refs(test_db, parent)
    assert len(before) == 2
    test_db.query("DELETE FROM workflow_refs")
    assert refs(test_db, parent) == []

    second = EngineServer(test_db, label="-s8-backfill")          # a fresh process on the same database
    second.start()
    try:
        assert refs(test_db, parent) == before
        # a second start with rows present changes nothing
        test_db.query("DELETE FROM workflow_refs WHERE parent_workflow_id = %s AND parent_version_id IS NULL", (parent,))
    finally:
        second.stop()
    third = EngineServer(test_db, label="-s8-backfill2")
    third.start()
    try:
        assert len(refs(test_db, parent)) == 1                    # the non-empty table was left alone
    finally:
        third.stop()

"""Stage 8 B4 over HTTP on real Postgres: every run site hands sub-workflow calls to the DB invoker (a linked child row, the
published version), delete protection with ``used_by``, publish ``impact``, the executions API lineage, the picker and I/O
endpoints (DESIGN-STAGE8-REUSE 3.3, 4.3, 10.3, 11)."""

from __future__ import annotations

import pytest

from ._stage8_b4_support import C, N, child_body, child_rows, make, parent_body, publish, save, setf, trigger, wait_for
from .conftest import uniq

pytestmark = pytest.mark.integration


def _published_child(api, tag="v1", *fields, **kw) -> str:
    cid = make(api, child_body(tag, *fields, **kw))
    publish(api, cid)
    return cid


# ── the studio SSE run (the main UI path) ───────────────────────────────────────────────────────────────────────


def test_sse_run_of_a_saved_workflow_writes_a_linked_child_row_and_runs_the_published_child(api, test_db):
    child = _published_child(api, "published")
    save(api, child, child_body("unsaved-edit"))                       # the child's draft moved on; the pin says 'latest'
    parent = make(api, parent_body(child))
    events = api.stream_events(f"/execution-stream/{parent}", {"input_data": None, "environment": "default"})
    done = [e for e in events if e.get("type") == "execution:complete"]
    assert done, events
    called = [e for e in events if e.get("subworkflowParentNode") == "call"]
    assert called and all(e["subworkflowId"] == child for e in called)           # child node events ride the parent stream

    [row] = child_rows(test_db, child)
    root = test_db.query("SELECT id FROM executions WHERE workflow_id = %s", (parent,))[0][0]
    assert (row["mode"], row["status"], row["parent_execution_id"], row["parent_node_name"], row["depth"], row["root_execution_id"]) \
        == ("sub_workflow", "success", root, "call", 1, root)
    assert row["workflow_version_id"] is not None                                  # a pinned version, not the draft
    out = test_db.query("SELECT output::text FROM node_outputs WHERE execution_id = %s AND node_name = 'Mark'", (row["id"],))
    assert out and "published" in str(out[0][0])


def test_sse_adhoc_run_writes_a_linked_child_row(api, test_db):
    child = _published_child(api)
    body = parent_body(child)
    events = api.stream_events("/execution-stream/adhoc", {**body, "input_data": None, "environment": "default"})
    assert any(e.get("type") == "execution:complete" for e in events), events
    [row] = child_rows(test_db, child)
    assert row["mode"] == "sub_workflow" and row["parent_execution_id"] and row["depth"] == 1


def test_a_missing_child_version_fails_the_node_with_its_code_on_the_stream(api, test_db):
    child = make(api, child_body())                                                  # never published
    parent = make(api, parent_body(child, "latest"))
    events = api.stream_events(f"/execution-stream/{parent}", {"input_data": None, "environment": "default"})
    errs = [e for e in events if e.get("type") == "node:error"]
    assert errs and errs[0].get("error_code") == "SUBWORKFLOW_VERSION_MISSING", events
    assert child_rows(test_db, child) == []                                          # nothing ran, nothing was written


# ── webhooks (both response modes) ──────────────────────────────────────────────────────────────────────────────


def _webhook_parent(child: str, mode: str) -> dict:
    wh = N("Webhook", "Webhook", {"method": "POST", "responseMode": mode})
    return parent_body(child, trigger_node=wh)


def test_webhook_last_node_run_writes_a_linked_child_row(api, test_db):
    child = _published_child(api, "hooked")
    parent = make(api, _webhook_parent(child, "lastNode"))
    publish(api, parent)
    r = api.raw("POST", f"/webhook/{parent}", json={"amount": 5})
    assert r.status_code == 200, r.text
    assert r.json()["data"][0]["tag"] == "hooked"
    [row] = child_rows(test_db, child)
    assert row["mode"] == "sub_workflow" and row["parent_execution_id"] == r.json()["executionId"] and row["parent_node_name"] == "call"


def test_webhook_on_received_background_run_writes_a_linked_child_row(api, test_db):
    child = _published_child(api, "bg")
    parent = make(api, _webhook_parent(child, "onReceived"))
    publish(api, parent)
    r = api.raw("POST", f"/webhook/{parent}", json={"amount": 5})
    assert r.status_code == 200, r.text
    exec_id = r.json()["executionId"]
    rows = wait_for(lambda: child_rows(test_db, child))
    assert rows and rows[0]["parent_execution_id"] == exec_id and rows[0]["root_execution_id"] == exec_id


# ── executions API: lineage and include_children ────────────────────────────────────────────────────────────────


def test_execution_detail_has_lineage_and_the_list_hides_children_by_default(api, test_db):
    child = _published_child(api)
    parent = make(api, parent_body(child, 1))
    run = api.post(f"/workflows/{parent}/run").json()
    assert run["status"] == "success", run
    root = run["execution_id"]
    [crow] = child_rows(test_db, child)

    d = api.get(f"/executions/{root}").json()
    assert d["parent"] is None and d["root_execution_id"] == root and d["depth"] == 0
    [kid] = d["children"]
    assert (kid["execution_id"], kid["node"], kid["workflow_id"], kid["version_number"], kid["status"]) == (crow["id"], "call", child, 1, "success")
    assert kid["workflow_name"] and kid["duration_ms"] is not None

    c = api.get(f"/executions/{crow['id']}").json()
    assert c["parent"] == {"execution_id": root, "workflow_id": parent, "workflow_name": d["workflow_name"], "node": "call"}
    assert (c["root_execution_id"], c["depth"], c["children"]) == (root, 1, [])

    default_ids = {e["id"] for e in api.get("/executions").json()}
    assert root in default_ids and crow["id"] not in default_ids
    all_ids = {e["id"] for e in api.get("/executions?include_children=true").json()}
    assert {root, crow["id"]} <= all_ids
    only_child = [e["id"] for e in api.get(f"/executions?workflow_id={child}&include_children=true").json()]
    assert only_child == [crow["id"]]
    assert api.get(f"/executions?workflow_id={child}").json() == []


# ── delete protection and used-by ───────────────────────────────────────────────────────────────────────────────


def test_delete_409_carries_used_by_and_the_used_by_endpoint_agrees(api):
    child = _published_child(api)
    draft_parent = make(api, parent_body(child, "draft"))
    live_parent = make(api, parent_body(child, "latest"))
    publish(api, live_parent)

    r = api.delete(f"/workflows/{child}")
    assert r.status_code == 409, r.text
    detail = r.json()["detail"]
    assert detail["code"] == "WORKFLOW_IN_USE" and detail["message"]
    assert [(u["parent_workflow_id"], u["node"], u["where"]) for u in detail["used_by"]] == [(live_parent, "call", "published")]

    rows = api.get(f"/workflows/{child}/used-by").json()
    assert {(u["parent_workflow_id"], u["where"], u["version_spec"]) for u in rows} == {
        (draft_parent, "draft", "draft"), (live_parent, "draft", "latest"), (live_parent, "published", "latest")}
    assert all(u["parent_name"] and u["release_ids"] == [] for u in rows)
    assert api.get("/workflows/wf_nope_zz/used-by").status_code == 404


# ── publish impact ──────────────────────────────────────────────────────────────────────────────────────────────


def test_publishing_a_child_returns_the_impact_on_parents_that_follow_latest(api):
    child = _published_child(api, "v1", ("amount", "number", True))                  # v1 takes `amount`
    hook = lambda: N("Webhook", "Webhook", {"method": "POST", "path": uniq("imp")})  # noqa: E731
    mapped = {"amount": "{{ $json.body.amount }}"}
    ok = make(api, parent_body(child, "latest", mode="map", inputs=mapped, trigger_node=hook()))
    pinned = make(api, parent_body(child, 1, mode="map", inputs=mapped, trigger_node=hook()))
    publish(api, ok)
    publish(api, pinned)

    first = publish(api, child).json()                                              # unchanged child: nobody breaks
    assert [(i["parent_workflow_id"], i["node"], i["version_spec"]) for i in first["impact"]] == [(ok, "call", "latest")]
    assert first["impact"][0]["breaking"] == [] and first["impact"][0]["in_release"] is False and first["impact"][0]["parent_name"]

    save(api, child, child_body("v2", ("amount", "number", True), ("currency", "string", True)))   # a new required input
    second = publish(api, child).json()
    [row] = second["impact"]
    assert row["parent_workflow_id"] == ok                                          # the version-1 pin is not affected
    assert [f["code"] for f in row["breaking"]] == ["WF024"] and row["breaking"][0]["severity"] == "error"
    assert "currency" in row["breaking"][0]["message"]

    assert "impact" not in api.post(f"/workflows/{ok}/unpublish", {}).json()      # impact is a publish-only field


# ── checker lookups: WF02x run through the API ──────────────────────────────────────────────────────────────────


def test_check_endpoint_sees_the_unsaved_definition_closure(api):
    child = _published_child(api, "v1", ("amount", "number", True))
    parent = make(api, parent_body(child, "latest", mode="map", inputs={"amount": "{{ $json.body.amount }}"}))
    clean = api.post(f"/workflows/{parent}/check", {}).json()
    assert [f for f in clean["findings"] if f["code"] in ("WF021", "WF022", "WF024")] == []

    bad = parent_body(child, "latest", mode="map", inputs={})                        # unsaved: no mapping for the required field
    r = api.post(f"/workflows/{parent}/check", {"definition": {"nodes": bad["nodes"], "connections": bad["connections"]}}).json()
    assert [(f["code"], f["severity"]) for f in r["findings"] if f["code"] == "WF024"] == [("WF024", "error")]

    cyc = parent_body(parent, "latest")                                              # calls itself: WF020, from the unsaved edit
    r = api.post(f"/workflows/{parent}/check", {"definition": {"nodes": cyc["nodes"], "connections": cyc["connections"]}}).json()
    assert any(f["code"] == "WF020" for f in r["findings"])


def test_publish_refuses_a_draft_pin_and_an_unpublished_child(api):
    child = _published_child(api)
    never = make(api, child_body())
    for body, code in ((parent_body(child, "draft"), "WF023"), (parent_body(never, "latest"), "WF022")):
        r = publish(api, make(api, body), expect=422)
        assert code in {f["code"] for f in r.json()["detail"]["findings"]}


# ── picker, io ──────────────────────────────────────────────────────────────────────────────────────────────────


def test_picker_and_io_endpoints(api):
    p = api.post("/projects", {"name": uniq("s8b4"), "slug": uniq("s8b4p")}).json()["id"]
    child = make(api, child_body("v1", ("order_id", "string", True), ("amount", "number", False), project_id=p))
    publish(api, child)
    plain = make(api, {"name": uniq("plain"), "nodes": [N("Start", "Start"), setf("S", a="1")], "connections": [C("Start", "S")],
                       "settings": {}, "project_id": p})
    hum = make(api, {"name": uniq("human"), "project_id": p, "settings": {},
                     "nodes": [trigger(("x", "string", True)), N("Ask", "Approval", {"title": "ok?"}, 200)],
                     "connections": [C("Inputs", "Ask")]})
    caller = make(api, parent_body(child, "latest", name=uniq("caller"), project_id=p))
    publish(api, caller)

    rows = {r["id"]: r for r in api.get(f"/projects/{p}/subworkflows").json()}
    assert set(rows) == {child, plain, hum, caller}
    c = rows[child]
    assert (c["callable"], c["published_version"], c["has_approval"], c["used_by_count"], c["io_source"]) == (True, 1, False, 1, "published")
    assert [(i["name"], i["type"], i["required"]) for i in c["inputs"]] == [("order_id", "string", True), ("amount", "number", False)]
    assert "tag" in c["output_fields"] and "order_id" in c["output_fields"]
    assert rows[plain]["callable"] is False and rows[plain]["reasons"]
    assert rows[hum]["callable"] is False and rows[hum]["has_approval"] is True
    assert {r["id"] for r in api.get(f"/projects/{p}/subworkflows?callable_only=true").json()} == {child}
    assert [r["id"] for r in api.get(f"/projects/{p}/subworkflows?q=human").json()] == [hum]
    assert api.get("/projects/proj_nope_zz/subworkflows").status_code == 404

    io = api.get(f"/workflows/{child}/io").json()
    assert (io["workflow_id"], io["version_number"], io["callable"]) == (child, 1, True)
    assert [f["name"] for f in io["input_fields"]] == ["order_id", "amount"] and io["input"]["properties"]["order_id"]["type"] == "string"
    assert io["output"]["properties"]["tag"]["type"] == "string"

    save(api, child, child_body("v2", ("order_id", "string", True), ("amount", "number", False), ("note", "string", False)))
    draft = api.get(f"/workflows/{child}/io?version=draft").json()
    assert draft["version_number"] is None and "note" in [f["name"] for f in draft["input_fields"]]
    assert "note" not in [f["name"] for f in api.get(f"/workflows/{child}/io?version=1").json()["input_fields"]]
    assert "note" not in [f["name"] for f in api.get(f"/workflows/{child}/io").json()["input_fields"]]   # default = published
    assert api.get(f"/workflows/{child}/io?version=9").status_code == 404
    assert api.get(f"/workflows/{child}/io?version=bogus").status_code == 422
    assert api.get(f"/workflows/{plain}/io?version=latest").status_code == 404              # never published
    nocall = api.get(f"/workflows/{plain}/io").json()
    assert nocall["callable"] is False and "WF021" in {f["code"] for f in nocall["findings"]}
    assert "WF025" in {f["code"] for f in api.get(f"/workflows/{hum}/io").json()["findings"]}
    assert api.get("/workflows/wf_nope_zz/io").status_code == 404

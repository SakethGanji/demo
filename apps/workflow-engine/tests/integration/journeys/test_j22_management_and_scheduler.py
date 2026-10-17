"""Journey J22: management endpoints the studio rarely hits (executions, credentials, folders, files),
publish/unpublish trigger sync, and the cron/interval scheduler end to end."""

from __future__ import annotations

import time

import pytest

from ..conftest import uniq, webhook_workflow
from ._helpers import chain

pytestmark = pytest.mark.integration


def cron_wf(name=None, **params):
    return {"name": name or uniq("cron"), "nodes": [
        {"name": "Cron", "type": "Cron", "parameters": params, "position": {"x": 0, "y": 0}},
        {"name": "Set", "type": "Set", "parameters": {"mode": "manual", "fields": [{"name": "ticked", "value": "{{ 1 }}"}], "keepOnlySet": True},
         "position": {"x": 200, "y": 0}}],
        "connections": [{"source_node": "Cron", "target_node": "Set", "source_output": "main", "target_input": "main"}], "settings": {}}


def triggers(test_db, wid):
    return test_db.query("select type, webhook_path, config, enabled, next_run_at, last_run_at from active_triggers where workflow_id=%s order by id", (wid,))


# ── publish / unpublish trigger sync ────────────────────────────────────────

def test_publish_syncs_triggers_and_unpublish_removes_them(api, test_db):
    wid = api.post("/workflows", cron_wf(mode="cron", cronExpression="*/5 * * * *")).json()["id"]
    assert triggers(test_db, wid) == []                                         # drafts have no triggers
    assert api.post(f"/workflows/{wid}/publish", {}).status_code == 200
    (typ, path, cfg, enabled, nxt, last), = triggers(test_db, wid)
    assert (typ, cfg, enabled, last) == ("cron", {"expression": "*/5 * * * *"}, True, None)
    assert nxt.minute % 5 == 0 and nxt.second == 0
    # re-publishing replaces (never duplicates) the trigger rows
    assert api.post(f"/workflows/{wid}/publish", {}).status_code == 200
    assert len(triggers(test_db, wid)) == 1
    assert api.post(f"/workflows/{wid}/unpublish", {}).json()["active"] is False
    assert triggers(test_db, wid) == []

    iw = api.post("/workflows", cron_wf(mode="interval", interval=120)).json()["id"]
    api.post(f"/workflows/{iw}/publish", {})
    (typ, _, cfg, *_), = triggers(test_db, iw)
    assert (typ, cfg) == ("interval", {"seconds": 120})

    bad = api.post("/workflows", cron_wf(mode="cron", cronExpression="not a cron")).json()["id"]
    assert api.post(f"/workflows/{bad}/publish", {}).status_code == 200      # publishes, but no schedule is created
    assert triggers(test_db, bad) == []

    path = uniq("trg")
    ww = api.post("/workflows", webhook_workflow(path=path, method="PUT")).json()["id"]
    api.post(f"/workflows/{ww}/publish", {})
    (typ, wpath, cfg, *_), = triggers(test_db, ww)
    assert (typ, wpath, cfg["method"]) == ("webhook", path, "PUT")
    api.post(f"/workflows/{ww}/unpublish", {})
    r = api.raw("PUT", f"/webhook/p/{path}", json={})
    assert r.status_code in (400, 404)                                        # no longer routable once unpublished
    assert api.post("/workflows/wf_missing/publish", {}).status_code == 404
    assert api.post("/workflows/wf_missing/unpublish", {}).status_code == 404


def test_publish_rejects_workflow_without_trigger(api):
    wf = {"name": uniq("notrig"), "nodes": [{"name": "Set", "type": "Set", "parameters": {"mode": "manual", "fields": []}, "position": {"x": 0, "y": 0}}],
          "connections": [], "settings": {}}
    wid = api.post("/workflows", wf).json()["id"]
    r = api.post(f"/workflows/{wid}/publish", {})
    assert r.status_code in (400, 422) and "trigger" in r.text.lower()
    assert api.get(f"/workflows/{wid}").json()["active"] is False


def test_two_workflows_cannot_share_a_webhook_path(api):
    """Regression: the 2nd publish of an already-owned webhook path used to 500 (unique index violation)."""
    path = uniq("dup")
    a = api.post("/workflows", webhook_workflow(path=path, set_value="{{ 'A' }}")).json()["id"]
    b = api.post("/workflows", webhook_workflow(path=path, set_value="{{ 'B' }}")).json()["id"]
    assert api.post(f"/workflows/{a}/publish", {}).status_code == 200
    rb = api.post(f"/workflows/{b}/publish", {})
    assert rb.status_code == 400 and path in rb.json()["detail"] and a in rb.json()["detail"]
    assert api.get(f"/workflows/{b}").json()["active"] is False
    assert api.raw("POST", f"/webhook/p/{path}", json={}).json()["data"] == [{"got": "A"}]   # A still owns it
    assert api.post(f"/workflows/{a}/publish", {}).status_code == 200            # re-publishing the owner is fine
    assert api.post(f"/workflows/{a}/unpublish", {}).status_code == 200
    assert api.post(f"/workflows/{b}/publish", {}).status_code == 200            # freed -> B can take it
    assert api.raw("POST", f"/webhook/p/{path}", json={}).json()["data"] == [{"got": "B"}]


# ── scheduler ───────────────────────────────────────────────────────────────

def test_scheduler_runs_due_interval_trigger_once_and_reschedules(api, test_db):
    """The scheduler ticks every 30 s; this test waits for at most ~40 s (slowest test in the suite)."""
    wid = api.post("/workflows", cron_wf(mode="interval", interval=3600)).json()["id"]
    api.post(f"/workflows/{wid}/publish", {})
    deadline = time.time() + 45
    runs = []
    while time.time() < deadline:
        runs = [x for x in api.get(f"/executions?workflow_id={wid}").json()]
        if runs:
            break
        time.sleep(1)
    assert runs, "scheduler never ran the due interval trigger"
    d = api.get(f"/executions/{runs[0]['id']}").json()
    assert d["status"] == "success" and d["mode"] == "cron"
    (_, _, _, enabled, nxt, last), = triggers(test_db, wid)
    assert enabled and last is not None and (nxt - last).total_seconds() == pytest.approx(3600, abs=2)
    time.sleep(1.5)
    assert len(api.get(f"/executions?workflow_id={wid}").json()) == 1          # not due again for an hour
    # unpublished workflows are not run even if a trigger row were due
    api.post(f"/workflows/{wid}/unpublish", {})
    assert triggers(test_db, wid) == []


# ── executions ──────────────────────────────────────────────────────────────

def test_execution_delete_cancel_retry(api):
    ok = api.post("/workflows", chain("okrun", {"name": "Set", "type": "Set", "parameters": {"mode": "manual", "fields": [{"name": "a", "value": "{{ 1 }}"}], "keepOnlySet": True},
                                                "position": {"x": 200, "y": 0}})).json()["id"]
    run = api.post(f"/workflows/{ok}/run").json()
    xid = run["execution_id"]
    assert api.post(f"/executions/{xid}/cancel").status_code == 400            # finished runs cannot be cancelled
    assert api.post("/executions/exec_missing/cancel").status_code == 404
    assert api.post(f"/executions/{xid}/retry").status_code == 400             # only failed runs can be retried
    assert api.post("/executions/exec_missing/retry").status_code == 404
    stop = api.post("/workflows", chain("fail", {"name": "Stop", "type": "StopAndError", "parameters": {"errorMessage": "kaput"},
                                                "position": {"x": 200, "y": 0}})).json()["id"]
    bad = api.post(f"/workflows/{stop}/run").json()
    assert bad["status"] == "failed"
    r = api.post(f"/executions/{bad['execution_id']}/retry")
    assert r.status_code == 200 and "Retry started: exec_" in r.json()["message"]
    new_id = r.json()["message"].split(": ")[1]
    for _ in range(40):
        d = api.get(f"/executions/{new_id}")
        if d.status_code == 200 and d.json()["status"] != "running":
            break
        time.sleep(0.2)
    assert d.json()["status"] == "failed"                                      # same failure, new execution
    assert api.delete(f"/executions/{xid}").status_code == 200
    assert api.get(f"/executions/{xid}").status_code == 404
    assert api.delete(f"/executions/{xid}").status_code == 404
    assert xid not in [x["id"] for x in api.get(f"/executions?workflow_id={ok}").json()]


# ── credentials ─────────────────────────────────────────────────────────────

def test_credentials_are_encrypted_never_returned_and_crud_works(api, test_db):
    r = api.post("/credentials", {"name": "smtp-prod", "type": "smtp", "data": {"password": "hunter2-very-secret"}})
    assert r.status_code == 201 and set(r.json()) == {"id", "name", "type", "created_at"}
    cid = r.json()["id"]
    assert "hunter2" not in r.text and "hunter2" not in api.get(f"/credentials/{cid}").text
    assert "hunter2" not in api.get("/credentials").text
    stored, = test_db.query("select data from credentials where id=%s", (cid,))[0]
    assert "hunter2" not in stored and stored != ""
    assert api.post("/credentials", {"name": "", "type": "x", "data": {}}).status_code == 422
    assert api.post("/credentials", {"name": "n", "type": "x"}).status_code == 422
    r = api.put(f"/credentials/{cid}", {"name": "smtp-renamed", "data": {"password": "rotated-pass-0002"}})
    assert r.status_code == 200 and r.json()["name"] == "smtp-renamed" and "rotated" not in r.text
    stored2, = test_db.query("select data from credentials where id=%s", (cid,))[0]
    assert stored2 != stored and "rotated" not in stored2
    assert api.get(f"/credentials/{cid}").json()["name"] == "smtp-renamed"
    assert api.put("/credentials/cred_missing", {"name": "x"}).status_code == 404
    assert api.delete(f"/credentials/{cid}").status_code == 200
    assert api.delete(f"/credentials/{cid}").status_code == 404 and api.get(f"/credentials/{cid}").status_code == 404


# ── folders ─────────────────────────────────────────────────────────────────

def test_folders_crud_and_nesting(api):
    root = api.post("/folders", {"name": "Finance"}).json()
    child = api.post("/folders", {"name": "Q3", "parent_folder_id": root["id"]}).json()
    assert child["parent_folder_id"] == root["id"] and root["parent_folder_id"] is None
    listed = {f["id"]: f for f in api.get("/folders").json()}
    assert listed[child["id"]]["parent_folder_id"] == root["id"]
    assert api.get(f"/folders/{child['id']}").json()["name"] == "Q3"
    r = api.put(f"/folders/{child['id']}", {"name": "Q4", "parent_folder_id": None})
    assert r.status_code == 200 and r.json()["name"] == "Q4" and r.json()["parent_folder_id"] is None
    assert api.post("/folders", {"name": ""}).status_code == 422
    assert api.put("/folders/fld_missing", {"name": "x", "parent_folder_id": None}).status_code == 404
    assert api.get("/folders/fld_missing").status_code == 404
    assert api.delete(f"/folders/{child['id']}").status_code == 200
    assert api.delete(f"/folders/{child['id']}").status_code == 404
    assert api.delete(f"/folders/{root['id']}").status_code == 200


# ── file browser ────────────────────────────────────────────────────────────

def test_file_browse_and_validate(api, tmp_path):
    (tmp_path / "a.csv").write_text("x\n1\n")
    (tmp_path / "b.txt").write_text("hello")
    (tmp_path / ".hidden").write_text("x")
    (tmp_path / "sub").mkdir()
    r = api.get(f"/files/browse?path={tmp_path}")
    assert r.status_code == 200
    names = [e["name"] for e in r.json()["entries"]]
    assert names == ["sub", "a.csv", "b.txt"]                                    # dirs first, then files, hidden skipped
    assert r.json()["parent_path"] == str(tmp_path.parent)
    csv_only = api.get(f"/files/browse?path={tmp_path}&filter_extensions=csv").json()["entries"]
    assert [e["name"] for e in csv_only] == ["sub", "a.csv"] and csv_only[1]["size"] == 4 and csv_only[1]["extension"] == ".csv"
    assert api.get(f"/files/browse?path={tmp_path}/nope").status_code == 404
    assert api.get(f"/files/browse?path={tmp_path}/a.csv").status_code == 400
    assert api.get(f"/files/validate?path={tmp_path}/a.csv").json()["valid"] is True
    assert api.get(f"/files/validate?path={tmp_path}/a.csv&extensions=csv,parquet").json()["valid"] is True
    bad = api.get(f"/files/validate?path={tmp_path}/b.txt&extensions=.csv").json()
    assert bad["valid"] is False and "Invalid extension" in bad["error"]
    assert api.get(f"/files/validate?path={tmp_path}/zzz").json() == {"valid": False, "error": "File not found"}
    assert api.get(f"/files/validate?path={tmp_path}").json() == {"valid": False, "error": "Path is not a file"}
    assert api.get("/files/validate").status_code == 422

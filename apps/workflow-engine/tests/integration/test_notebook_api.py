"""Notebook core API (BUILD-PLAN §1.2-1.3, §3.1 edge cases): cells, runs, staleness, writes, apply/discard,
row storage, the run lock, for-each, validation and execution attribution. Real engine over HTTP, real Postgres."""

from __future__ import annotations

import threading
import time
from datetime import datetime, timedelta

import pytest

from .conftest import _EchoHandler, http_workflow, start_set_workflow, uniq
from .notebook_support import (  # noqa: F401  (fixtures)
    add,
    bank_db,
    cell_in,
    nb_api,
    nb_engine,
    nb_project,
    new_notebook,
    result_of,
    run_and_wait,
    wait_run,
)

pytestmark = pytest.mark.integration


def _wire(bank_db, status: str = "stuck", amount: float = 10) -> str:
    wid = uniq("W").replace("-", "_")
    bank_db.query("INSERT INTO bank.wires VALUES (%s, %s, %s, now() - interval '2 days')", (wid, amount, status))
    return wid


def _writes(test_db, cell_id: str) -> int:
    return test_db.query("SELECT count(*) FROM notebook_writes WHERE cell_id = %s", (cell_id,))[0][0]


def _change_notebook(nb_api, nb_project, bank_db, wire: str, new_status: str = "held"):
    nb = new_notebook(nb_api, nb_project, created_by="alice")
    c1 = add(nb_api, nb, "query", {"connection": "bank", "sql": "SELECT wire_id, status FROM bank.wires WHERE wire_id = %(w)s",
                                   "params": {"w": wire}})
    c2 = add(nb_api, nb, "change", {"connection": "bank_rw", "table": "bank.wires", "key": {"wire_id": wire},
                                    "set": {"status": new_status}, "reason": "hold it"})
    c3 = add(nb_api, nb, "query", {"connection": "bank", "sql": "SELECT status FROM bank.wires WHERE wire_id = %(w)s",
                                   "params": {"w": wire}})
    return nb, c1, c2, c3


def _run_and_apply(nb_api, nb, c2, by: str = "alice"):
    run = run_and_wait(nb_api, nb, triggered_by=by)
    assert run["status"] == "needs_you" and run["stop"]["reason"] == "needs_approval" and run["stop"]["cell_id"] == c2["id"]
    proposal = result_of(run, c2)
    assert proposal["status"] == "needs_approval"
    assert proposal["output"]["diff"][0]["changes"][0]["after"] in ("held", "frozen", "blocked")
    r = nb_api.post(f"/notebook-cells/{c2['id']}/apply", {"approved_by": by})
    assert r.status_code == 200, r.text
    assert r.json()["result"]["status"] == "applied"
    return wait_run(nb_api, run["id"])


# ── basics ────────────────────────────────────────────────────────────────────────────────────────────────────────────


def test_query_rule_data_refs_and_staleness(nb_api, nb_project):
    nb = new_notebook(nb_api, nb_project)
    assert nb["status"] == "idle" and nb["cells"] == [] and nb["runs"] == []
    c1 = add(nb_api, nb, "query", {"connection": "bank", "sql": "SELECT wire_id, amount, status FROM bank.wires "
                                                                 "WHERE wire_id IN ('W1','W2','W3') ORDER BY wire_id"})
    assert c1["name"] == "c1" and c1["effect"] == "read" and c1["position"] == 1 and c1["latest"] is None
    c2 = add(nb_api, nb, "rule", {"op": "filter", "left": "{{cells.c1.rows}}",
                                  "where": [{"field": "status", "op": "eq", "value": "stuck"}]})
    c3 = add(nb_api, nb, "data", {"csv": "wire_id,amount\nW1,100.5\nW2,2500\nW3,75"})
    assert c3["spec"]["rows"][0] == {"wire_id": "W1", "amount": 100.5} and c3["spec"]["columns"] == ["wire_id", "amount"]
    c4 = add(nb_api, nb, "rule", {"op": "compare", "left": "{{cells.c1.rows}}", "right": "{{cells.c3.rows}}",
                                  "key": "wire_id", "fields": [{"left": "amount", "right": "amount", "op": "approx",
                                                                "tolerance": 0.01}]})
    c5 = add(nb_api, nb, "note", {"markdown": "# done"})
    assert c5["effect"] == "none"
    run = run_and_wait(nb_api, nb, triggered_by="alice")
    assert run["status"] == "done" and run["n"] == 1 and run["triggered_by"] == "alice"
    assert result_of(run, c1)["output"]["row_count"] == 3
    assert [r["wire_id"] for r in result_of(run, c2)["output"]["rows"]] == ["W1", "W2"]
    cmp = result_of(run, c4)["output"]
    assert cmp["mismatches"] == 0 and cmp["matched"] == 3
    assert result_of(run, c5) is None  # notes do not run
    full = nb_api.get(f"/notebooks/{nb['id']}").json()
    assert full["status"] == "done" and full["active_run_id"] is None
    assert not any(c["stale"] for c in full["cells"] if c["latest"])
    # editing c2 makes c2 and everything below stale, not c1
    p = nb_api.patch(f"/notebook-cells/{c2['id']}", {"spec": {"op": "count", "left": "{{cells.c1.rows}}"}})
    assert p.status_code == 200 and p.json()["stale_from"] == c2["id"]
    full = nb_api.get(f"/notebooks/{nb['id']}").json()
    stale = {c["name"]: c["stale"] for c in full["cells"]}
    assert stale == {"c1": False, "c2": True, "c3": True, "c4": True, "c5": False}
    # a title-only edit changes nothing
    assert nb_api.patch(f"/notebook-cells/{c1['id']}", {"title": "wires"}).json()["stale_from"] is None
    # rerun from c2 reuses c1's earlier result
    run2 = run_and_wait(nb_api, nb, from_cell_id=c2["id"])
    assert run2["status"] == "done" and run2["from_position"] == 2 and result_of(run2, c1) is None
    assert result_of(run2, c2)["output"]["count"] == 3
    full = nb_api.get(f"/notebooks/{nb['id']}").json()
    assert not any(c["stale"] for c in full["cells"])
    assert [r["n"] for r in full["runs"]] == [2, 1]
    # list
    lst = nb_api.get(f"/projects/{nb_project['id']}/notebooks").json()
    item = next(i for i in lst if i["id"] == nb["id"])
    assert item["cell_count"] == 5 and item["needs_ai_count"] == 0 and item["writes_env"] is None
    assert item["last_run"]["n"] == 2
    assert [i["id"] for i in nb_api.get(f"/projects/{nb_project['id']}/notebooks",
                                        params={"q": nb["title"]}).json()] == [nb["id"]]


# ── §3.1 named edge cases ─────────────────────────────────────────────────────────────────────────────────────────────


def test_rerun_stops_before_applied_write(nb_api, nb_project, bank_db, test_db):
    wire = _wire(bank_db)
    nb, c1, c2, c3 = _change_notebook(nb_api, nb_project, bank_db, wire)
    assert c2["effect"] == "write"
    run = _run_and_apply(nb_api, nb, c2)
    assert run["status"] == "done"
    assert result_of(run, c3)["output"]["rows"] == [{"status": "held"}]
    assert bank_db.query("SELECT status FROM bank.wires WHERE wire_id = %s", (wire,)) == [("held",)]
    full = nb_api.get(f"/notebooks/{nb['id']}").json()
    assert cell_in(full, c2["id"])["applied"] is True
    lst = nb_api.get(f"/projects/{nb_project['id']}/notebooks").json()
    assert next(i for i in lst if i["id"] == nb["id"])["writes_env"]
    # someone resets the row; a rerun must stop before the applied change, not write again
    bank_db.query("UPDATE bank.wires SET status = 'stuck' WHERE wire_id = %s", (wire,))
    rerun = run_and_wait(nb_api, nb, triggered_by="bob")
    assert rerun["status"] == "stopped_at_write"
    assert rerun["stop"]["reason"] == "applied_write" and rerun["stop"]["cell_id"] == c2["id"]
    assert rerun["stop"]["applied_by"] == "alice" and rerun["stop"]["applied_at"]
    assert result_of(rerun, c1)["status"] == "success" and result_of(rerun, c2) is None and result_of(rerun, c3) is None
    assert bank_db.query("SELECT status FROM bank.wires WHERE wire_id = %s", (wire,)) == [("stuck",)]
    assert _writes(test_db, c2["id"]) == 1
    assert nb_api.get(f"/notebooks/{nb['id']}").json()["active_run_id"] is None


def test_rerun_skip_keeps_earlier_result(nb_api, nb_project, bank_db, test_db):
    wire = _wire(bank_db)
    nb, c1, c2, c3 = _change_notebook(nb_api, nb_project, bank_db, wire, "frozen")
    first = _run_and_apply(nb_api, nb, c2)
    applied = result_of(nb_api.get(f"/notebook-runs/{first['id']}").json(), c2)
    rerun = run_and_wait(nb_api, nb, on_applied="skip")
    assert rerun["status"] == "done"
    kept = result_of(rerun, c2)
    assert kept["status"] == "kept_earlier"
    assert kept["output"]["diff"] == applied["output"]["diff"]
    assert result_of(rerun, c3)["output"]["rows"] == [{"status": "frozen"}]
    assert _writes(test_db, c2["id"]) == 1


def test_reapply_requires_explicit_list_and_is_idempotent(nb_api, nb_project, bank_db, test_db):
    wire = _wire(bank_db)
    nb, c1, c2, c3 = _change_notebook(nb_api, nb_project, bank_db, wire, "blocked")
    _run_and_apply(nb_api, nb, c2)
    # without the explicit list: stop
    assert run_and_wait(nb_api, nb)["status"] == "stopped_at_write"
    # with it: the change is proposed again; the same idempotency key gives no second write
    rerun = run_and_wait(nb_api, nb, reapply=[c2["id"]])
    assert rerun["status"] == "needs_you" and result_of(rerun, c2)["status"] == "needs_approval"
    r = nb_api.post(f"/notebook-cells/{c2['id']}/apply", {"approved_by": "carol"})
    assert r.status_code == 200 and r.json()["result"]["output"]["already_applied"] is True
    assert wait_run(nb_api, rerun["id"])["status"] == "done"
    assert _writes(test_db, c2["id"]) == 1
    # applying a finished cell again returns the same result
    again = nb_api.post(f"/notebook-cells/{c2['id']}/apply", {"approved_by": "carol"})
    assert again.status_code == 200 and again.json()["result"]["id"] == r.json()["result"]["id"]
    assert _writes(test_db, c2["id"]) == 1

    # two applies at the same moment give one write row
    wire2 = _wire(bank_db)
    nb2, _a, d2, _b = _change_notebook(nb_api, nb_project, bank_db, wire2)
    run = run_and_wait(nb_api, nb2)
    assert run["status"] == "needs_you"
    out: list = []

    def go():
        out.append(nb_api.post(f"/notebook-cells/{d2['id']}/apply", {"approved_by": "dave"}))

    ts = [threading.Thread(target=go) for _ in range(2)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert [o.status_code for o in out] == [200, 200], [o.text for o in out]
    assert out[0].json()["result"]["id"] == out[1].json()["result"]["id"]
    assert _writes(test_db, d2["id"]) == 1
    assert wait_run(nb_api, run["id"])["status"] == "done"
    assert bank_db.query("SELECT status FROM bank.wires WHERE wire_id = %s", (wire2,)) == [("held",)]
    # an applied cell can not be deleted
    r = nb_api.delete(f"/notebook-cells/{d2['id']}")
    assert r.status_code == 409 and r.json()["detail"]["code"] == "cell_applied"


def test_discard_resumes_the_run(nb_api, nb_project, bank_db):
    wire = _wire(bank_db)
    nb, c1, c2, c3 = _change_notebook(nb_api, nb_project, bank_db, wire)
    run = run_and_wait(nb_api, nb, triggered_by="alice")
    r = nb_api.post(f"/notebook-cells/{c2['id']}/discard", {"by": "alice"})
    assert r.status_code == 200 and r.json()["result"]["status"] == "discarded"
    done = wait_run(nb_api, run["id"])
    assert done["status"] == "done" and result_of(done, c3)["output"]["rows"] == [{"status": "stuck"}]
    assert nb_api.post(f"/notebook-cells/{c2['id']}/discard", {}).status_code == 409


def test_removed_kinds_are_refused_but_stored_ones_load_read_only(nb_api, nb_project, test_db):
    """approval / person were removed: creating one is kind_removed; a stored one loads as removed, a run skips it,
    and the decide route is gone."""
    nb = new_notebook(nb_api, nb_project)
    for kind, spec in (("approval", {"assignee": "ops", "title": "ok?"}), ("person", {"assignee": "x", "title": "y"})):
        r = nb_api.post(f"/notebooks/{nb['id']}/cells", {"kind": kind, "spec": spec})
        assert r.status_code == 422 and r.json()["detail"]["code"] == "kind_removed", r.text
    r = nb_api.post(f"/notebooks/{nb['id']}/cells", {"kind": "nope", "spec": {}})
    assert r.status_code == 422 and r.json()["detail"]["code"] == "unknown_kind", r.text
    q1 = add(nb_api, nb, "query", {"connection": "bank", "sql": "SELECT 1 AS one"})
    # a notebook saved before the removal: an approval cell between two queries
    test_db.query("INSERT INTO notebook_cells (id, notebook_id, position, name, kind, spec, created_via, created_at, "
                  "updated_at) VALUES (%s, %s, 2, 'c2', 'approval', %s::jsonb, 'user', now(), now())",
                  (f"cell_old{nb['id'][-6:]}", nb["id"], '{"assignee": "ops", "title": "OK to go?", "four_eyes": true}'))
    q3 = add(nb_api, nb, "query", {"connection": "bank", "sql": "SELECT 3 AS three"})
    got = nb_api.get(f"/notebooks/{nb['id']}").json()
    old = next(c for c in got["cells"] if c["name"] == "c2")
    assert old["kind"] == "approval" and old["removed"] is True and old["spec"]["title"] == "OK to go?"
    assert all(c["removed"] is False for c in got["cells"] if c["name"] != "c2")
    run = run_and_wait(nb_api, nb)
    assert run["status"] == "done", run
    assert result_of(run, q3)["output"]["rows"] == [{"three": 3}] and result_of(run, old) is None
    assert result_of(run, q1)["status"] == "success"
    # editing it is refused; deleting it works; the decide route is gone
    r = nb_api.patch(f"/notebook-cells/{old['id']}", {"spec": {"assignee": "x", "title": "y"}})
    assert r.status_code == 422 and r.json()["detail"]["code"] == "kind_removed"
    # a title-only edit is an edit too: refused, and nothing is saved
    r = nb_api.patch(f"/notebook-cells/{old['id']}", {"title": "renamed"})
    assert r.status_code == 422 and r.json()["detail"]["code"] == "kind_removed", r.text
    again = next(c for c in nb_api.get(f"/notebooks/{nb['id']}").json()["cells"] if c["name"] == "c2")
    assert again["title"] is None
    assert nb_api.post(f"/notebook-cells/{old['id']}/decide", {"decision": "approved", "by": "b"}).status_code in (404, 405)
    assert nb_api.delete(f"/notebook-cells/{old['id']}").status_code in (200, 204)


def test_failed_cell_mid_task(nb_api, nb_project):
    nb = new_notebook(nb_api, nb_project)
    c1 = add(nb_api, nb, "query", {"connection": "bank", "sql": "SELECT count(*) AS n FROM bank.wires"})
    c2 = add(nb_api, nb, "query", {"connection": "bank", "sql": "SELECT * FROM bank.no_such_table"})
    c3 = add(nb_api, nb, "query", {"connection": "bank", "sql": "SELECT 1 AS one"})
    run = run_and_wait(nb_api, nb)
    assert run["status"] == "failed"
    assert run["stop"]["reason"] == "cell_failed" and run["stop"]["cell_id"] == c2["id"]
    assert result_of(run, c1)["status"] == "success"
    err = result_of(run, c2)
    assert err["status"] == "error" and err["error"]["code"] == "query_failed" and err["error"]["message"]
    assert result_of(run, c3) is None
    full = nb_api.get(f"/notebooks/{nb['id']}").json()
    assert full["status"] == "failed" and full["active_run_id"] is None
    # fix the cell and rerun from it
    nb_api.patch(f"/notebook-cells/{c2['id']}", {"spec": {"connection": "bank", "sql": "SELECT 2 AS two"}})
    run = run_and_wait(nb_api, nb, from_cell_id=c2["id"])
    assert run["status"] == "done" and result_of(run, c3)["output"]["rows"] == [{"one": 1}]


def test_empty_results(nb_api, nb_project):
    nb = new_notebook(nb_api, nb_project)
    c1 = add(nb_api, nb, "query", {"connection": "bank", "sql": "SELECT * FROM bank.wires WHERE false"})
    c2 = add(nb_api, nb, "rule", {"op": "count", "left": "{{cells.c1.rows}}"})
    c3 = add(nb_api, nb, "rule", {"op": "assert", "left": "{{cells.c1.rows}}",
                                  "where": [{"field": "status", "op": "eq", "value": "sent"}]})
    loop = add(nb_api, nb, "for_each", {"over": "{{cells.c1.rows}}"})
    add(nb_api, nb, "query", {"connection": "bank", "sql": "SELECT %(w)s AS w", "params": {"w": "{{row.wire_id}}"}},
        parent_cell_id=loop["id"])
    c6 = add(nb_api, nb, "query", {"connection": "bank", "sql": "SELECT 1 AS one"},
             guard={"left": "{{cells.c1.rows}}", "op": "nonempty"})
    run = run_and_wait(nb_api, nb)
    assert run["status"] == "done", run
    o1 = result_of(run, c1)["output"]
    assert o1["rows"] == [] and o1["row_count"] == 0 and o1["columns"] == ["wire_id", "amount", "status", "created_at"]
    assert result_of(run, c2)["output"]["count"] == 0
    assert result_of(run, c3)["status"] == "success"
    assert result_of(run, loop)["output"]["ok"] == 0 and result_of(run, loop)["output"]["failed"] == 0
    assert result_of(run, c6)["status"] == "guard_false"
    r = nb_api.get(f"/notebook-cells/{c1['id']}/rows").json()
    assert r == {"columns": ["wire_id", "amount", "status", "created_at"], "rows": [], "total": 0}


def test_large_table(nb_api, nb_project, nb_engine):
    nb = new_notebook(nb_api, nb_project)
    c1 = add(nb_api, nb, "query", {"connection": "bank", "sql": "SELECT id, label FROM bank.big ORDER BY id",
                                   "limit": 500})
    assert add(nb_api, nb, "query", {"connection": "bank", "sql": "SELECT 1", "limit": 501}, expect=422)
    c2 = add(nb_api, nb, "rule", {"op": "count", "left": "{{cells.c1.rows}}",
                                  "where": [{"field": "id", "op": "gt", "value": 450}]})
    run = run_and_wait(nb_api, nb)
    assert run["status"] == "done"
    res = result_of(run, c1)
    out = res["output"]
    assert len(out["rows"]) <= 50 and out["row_count"] == 500 and out["truncated"] is True and out["cap"] == 500
    assert res["output_ref"]
    assert result_of(run, c2)["output"]["count"] == 50  # the rule saw all 500 rows, not the preview
    page = nb_api.get(f"/notebook-cells/{c1['id']}/rows", params={"offset": 450, "limit": 100}).json()
    assert page["total"] == 500 and len(page["rows"]) == 50 and page["rows"][0] == {"id": 451, "label": "row 451"}
    assert nb_api.get(f"/notebook-cells/{c1['id']}/rows", params={"limit": 501}).status_code == 422
    r = nb_api.get(f"/notebooks/{nb['id']}")
    assert len(r.content) < 1_000_000
    assert (nb_engine.storage_dir / res["output_ref"]).exists()


def test_concurrent_reruns(nb_api, nb_project, upstream, test_db):
    nb = new_notebook(nb_api, nb_project)
    add(nb_api, nb, "data", {"rows": [{"i": i} for i in range(12)]})
    add(nb_api, nb, "wait", {"mode": "duration", "duration": "2s"})  # the run surely outlives both requests
    loop = add(nb_api, nb, "for_each", {"over": "{{cells.c1.rows}}"})
    add(nb_api, nb, "http", {"method": "GET", "url": upstream + "/echo/{{row.i}}"}, parent_cell_id=loop["id"])
    out: list = []

    def go():
        out.append(nb_api.post(f"/notebooks/{nb['id']}/runs", {}))

    ts = [threading.Thread(target=go) for _ in range(2)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    codes = sorted(o.status_code for o in out)
    assert codes == [202, 409], [o.text for o in out]
    ok = next(o for o in out if o.status_code == 202).json()
    busy = next(o for o in out if o.status_code == 409).json()["detail"]
    assert busy["code"] == "run_in_progress" and busy["run_id"] == ok["id"]
    done = wait_run(nb_api, ok["id"])
    assert done["status"] == "done", done
    assert result_of(done, loop)["output"]["ok"] == 12
    # the lock is free again
    assert run_and_wait(nb_api, nb)["status"] == "done"
    # a run whose heartbeat stopped (a crashed engine) is reaped: interrupted, lock freed
    fake = "nbr_stale_" + uniq("r").replace("-", "")
    test_db.query("INSERT INTO notebook_runs (id, notebook_id, n, from_position, status, on_applied, reapply, "
                  "heartbeat_at, started_at) VALUES (%s, %s, 99, 0, 'running', 'stop', '[]', %s, %s)",
                  (fake, nb["id"], datetime.now() - timedelta(minutes=5), datetime.now() - timedelta(minutes=6)))
    test_db.query("UPDATE notebooks SET active_run_id = %s, status = 'running' WHERE id = %s", (fake, nb["id"]))
    r = nb_api.post(f"/notebooks/{nb['id']}/runs", {})
    assert r.status_code == 202, r.text
    assert nb_api.get(f"/notebook-runs/{fake}").json()["status"] == "interrupted"
    wait_run(nb_api, r.json()["id"])


def test_guard_false(nb_api, nb_project, test_db):
    wid = nb_api.post("/workflows", start_set_workflow()).json()["id"]
    nb = new_notebook(nb_api, nb_project)
    add(nb_api, nb, "query", {"connection": "bank", "sql": "SELECT * FROM bank.wires WHERE false"})
    c2 = add(nb_api, nb, "workflow", {"workflow_id": wid}, guard={"left": "{{cells.c1.count}}", "op": "gt", "right": 0})
    run = run_and_wait(nb_api, nb)
    assert run["status"] == "done"
    res = result_of(run, c2)
    assert res["status"] == "guard_false" and res["execution_id"] is None
    assert test_db.query("SELECT count(*) FROM executions WHERE workflow_id = %s", (wid,))[0][0] == 0
    # flip the guard: now it runs and makes exactly one execution
    nb_api.patch(f"/notebook-cells/{c2['id']}", {"guard": {"left": "{{cells.c1.count}}", "op": "eq", "right": 0}})
    run = run_and_wait(nb_api, nb)
    res = result_of(run, c2)
    assert res["status"] == "success" and res["execution_id"] and res["output"]["rows"] == [{"answer": 42}]
    assert test_db.query("SELECT count(*) FROM executions WHERE workflow_id = %s", (wid,))[0][0] == 1


def test_for_each_with_failing_rows(nb_api, nb_project, upstream):
    nb = new_notebook(nb_api, nb_project)
    add(nb_api, nb, "data", {"rows": [{"p": "/ok/0"}, {"p": "/status/500"}, {"p": "/ok/2"}, {"p": "/ok/3"},
                                      {"p": "/ok/4"}]})
    loop = add(nb_api, nb, "for_each", {"over": "{{cells.c1.rows}}"})
    h = add(nb_api, nb, "http", {"method": "GET", "url": upstream + "{{row.p}}"}, parent_cell_id=loop["id"])
    assert h["parent_cell_id"] == loop["id"] and h["position"] == 1
    full = nb_api.get(f"/notebooks/{nb['id']}").json()
    assert [c["id"] for c in cell_in(full, loop["id"])["children"]] == [h["id"]]
    assert cell_in(full, loop["id"])["effect"] == "read"
    run = run_and_wait(nb_api, nb)
    assert run["status"] == "done"
    lo = result_of(run, loop)["output"]
    assert lo["ok"] == 4 and lo["failed"] == 1 and lo["row_count"] == 5
    assert [r["status"] for r in lo["rows"]] == ["success", "error", "success", "success", "success"]
    bad = result_of(run, h, 1)
    assert bad["status"] == "error" and bad["error"]["code"] == "http_failed" and bad["execution_id"]
    assert result_of(run, h, 0)["output"]["status"] == 200
    # stop_on_fail stops at that row
    nb_api.patch(f"/notebook-cells/{loop['id']}", {"spec": {"over": "{{cells.c1.rows}}", "stop_on_fail": True}})
    run = run_and_wait(nb_api, nb)
    assert run["status"] == "failed" and run["stop"]["cell_id"] == loop["id"]
    lo = result_of(run, loop)["output"]
    assert lo["ok"] == 1 and lo["failed"] == 1 and lo["stopped_at"] == 1
    assert result_of(run, h, 2) is None
    # more rows than max_rows
    nb_api.patch(f"/notebook-cells/{loop['id']}", {"spec": {"over": "{{cells.c1.rows}}", "max_rows": 3}})
    run = run_and_wait(nb_api, nb)
    assert result_of(run, loop)["error"]["code"] == "row_cap_exceeded"


def test_invalid_definitions(nb_api, nb_project, bank_db):
    nb = new_notebook(nb_api, nb_project)
    r = nb_api.post(f"/notebooks/{nb['id']}/cells", {"kind": "teleport", "spec": {}})
    assert r.status_code == 422
    r = nb_api.post(f"/notebooks/{nb['id']}/cells", {"kind": "query", "spec": {"connection": "bank",
                                                                                "sql": "UPDATE bank.wires SET status='x'"}})
    assert r.status_code == 422 and r.json()["detail"]["code"] == "not_select"
    r = nb_api.post(f"/notebooks/{nb['id']}/cells", {"kind": "query", "spec": {"connection": "nope", "sql": "SELECT 1"}})
    assert r.status_code == 422 and r.json()["detail"]["code"] == "connection_not_found"
    r = nb_api.post(f"/notebooks/{nb['id']}/cells", {"kind": "change", "spec": {
        "connection": "bank", "table": "bank.wires", "key": {"wire_id": "W1"}, "set": {"status": "x"}}})
    assert r.status_code == 422 and r.json()["detail"]["code"] == "connection_readonly"
    r = nb_api.post(f"/notebooks/{nb['id']}/cells", {"kind": "query", "spec": {"connection": "bank", "sql": "SELECT 1",
                                                                                "bogus": 1}})
    assert r.status_code == 422 and r.json()["detail"]["code"] == "invalid_spec" and r.json()["detail"]["fields"]
    # a for-each body cell at top level
    r = nb_api.post(f"/notebooks/{nb['id']}/cells", {"kind": "query", "spec": {
        "connection": "bank", "sql": "SELECT %(w)s AS w", "params": {"w": "{{row.wire_id}}"}}})
    assert r.status_code == 422 and r.json()["detail"]["code"] == "invalid_parent"
    q = add(nb_api, nb, "query", {"connection": "bank", "sql": "SELECT 1 AS one"})
    r = nb_api.post(f"/notebooks/{nb['id']}/cells", {"kind": "query", "parent_cell_id": q["id"],
                                                     "spec": {"connection": "bank", "sql": "SELECT 1"}})
    assert r.status_code == 422 and r.json()["detail"]["code"] == "invalid_parent"
    loop = add(nb_api, nb, "for_each", {"over": "{{cells.c1.rows}}"})
    r = nb_api.post(f"/notebooks/{nb['id']}/cells", {"kind": "wait", "parent_cell_id": loop["id"],
                                                     "spec": {"mode": "duration", "duration": "1s"}})
    assert r.status_code == 422 and r.json()["detail"]["code"] == "invalid_parent"
    # a reference to a cell that does not exist: the cell fails with bad_ref when it runs
    bad = add(nb_api, nb, "rule", {"op": "count", "left": "{{cells.c9.rows}}"})
    run = run_and_wait(nb_api, nb)
    assert run["status"] == "failed" and run["stop"]["cell_id"] == bad["id"]
    assert result_of(run, bad)["error"]["code"] == "bad_ref"
    # 404s
    assert nb_api.get("/notebooks/nb_nope").json()["detail"]["code"] == "notebook_not_found"
    assert nb_api.delete("/notebook-cells/cell_nope").status_code == 404
    assert nb_api.get("/notebook-runs/nbr_nope").status_code == 404
    # deleting a plain cell works; deleting the notebook works
    assert nb_api.delete(f"/notebook-cells/{bad['id']}").status_code == 204
    assert nb_api.delete(f"/notebooks/{nb['id']}").status_code == 204
    assert nb_api.get(f"/notebooks/{nb['id']}").status_code == 404


def test_attribution_recorded(nb_api, nb_project):
    wid = nb_api.post("/workflows", start_set_workflow()).json()["id"]
    r = nb_api.post(f"/workflows/{wid}/run", {"triggered_by": "alice", "run_as": "svc-ops", "via_agent": "helper"})
    assert r.status_code == 200, r.text
    eid = r.json()["execution_id"]
    want = {"triggered_by": "alice", "run_as": "svc-ops", "via_agent": "helper", "via": "api"}
    assert nb_api.get(f"/executions/{eid}").json()["attribution"] == want
    lst = nb_api.get("/executions", params={"workflow_id": wid}).json()
    assert next(e for e in lst if e["id"] == eid)["attribution"] == want
    # a plain run carries none
    eid2 = nb_api.post(f"/workflows/{wid}/run").json()["execution_id"]
    assert nb_api.get(f"/executions/{eid2}").json()["attribution"] is None
    # a notebook workflow cell: who started the notebook run, and which cell
    nb = new_notebook(nb_api, nb_project)
    c = add(nb_api, nb, "workflow", {"workflow_id": wid})
    run = run_and_wait(nb_api, nb, triggered_by="bob", run_as="svc-ops", via_agent="ask")
    res = result_of(run, c)
    att = nb_api.get(f"/executions/{res['execution_id']}").json()["attribution"]
    assert att == {"triggered_by": "bob", "run_as": "svc-ops", "via_agent": "ask",
                   "via": f"notebook:{nb['id']}/cell:{c['id']}/run:{run['id']}"}


def test_store_cell_records_a_write(nb_api, nb_project, test_db):
    nb = new_notebook(nb_api, nb_project)
    add(nb_api, nb, "data", {"rows": [{"wire_id": "W1", "amount": 1.5}, {"wire_id": "W2", "amount": 2}]})
    name = "nb_" + uniq("t").replace("-", "_")
    s = add(nb_api, nb, "store", {"new_table": name, "rows": "{{cells.c1.rows}}"})
    assert s["effect"] == "write"
    run = run_and_wait(nb_api, nb)
    assert run["status"] == "done", run
    out = result_of(run, s)["output"]
    assert out["created"] is True and out["inserted"] == 2
    assert _writes(test_db, s["id"]) == 1
    assert run_and_wait(nb_api, nb)["status"] == "stopped_at_write"


def test_cancel_run(nb_api, nb_project, upstream):
    nb = new_notebook(nb_api, nb_project)
    add(nb_api, nb, "data", {"rows": [{"i": i} for i in range(40)]})
    loop = add(nb_api, nb, "for_each", {"over": "{{cells.c1.rows}}"})
    add(nb_api, nb, "http", {"method": "GET", "url": upstream + "/echo/{{row.i}}"}, parent_cell_id=loop["id"])
    r = nb_api.post(f"/notebooks/{nb['id']}/runs", {})
    assert r.status_code == 202
    time.sleep(0.3)
    c = nb_api.post(f"/notebook-runs/{r.json()['id']}/cancel")
    assert c.status_code == 202 and c.json()["status"] == "cancelled"
    full = nb_api.get(f"/notebooks/{nb['id']}").json()
    assert full["active_run_id"] is None and full["status"] == "idle"


# ── wait, for-each modes / retry / preview (DESIGN-SYSTEM additions) ─────────────────────────────────────────


def test_wait_for_duration_and_condition(nb_api, nb_project, bank_db):
    nb = new_notebook(nb_api, nb_project)
    w = add(nb_api, nb, "wait", {"mode": "duration", "duration": "1s"})
    run = run_and_wait(nb_api, nb)
    assert run["status"] == "done" and result_of(run, w)["output"]["waited_s"] >= 1
    for bad in ({"mode": "duration"}, {"mode": "duration", "duration": "soon"}, {"mode": "condition"}):
        r = nb_api.post(f"/notebooks/{nb['id']}/cells", {"kind": "wait", "spec": bad})
        assert r.status_code == 422 and r.json()["detail"]["code"] == "invalid_spec", (bad, r.text)
    r = nb_api.post(f"/notebooks/{nb['id']}/cells", {"kind": "wait", "spec": {"mode": "condition", "condition": {
        "connection": "bank", "sql": "DELETE FROM bank.wires"}}})
    assert r.status_code == 422 and r.json()["detail"]["code"] == "not_select"
    # until a row shows up
    tbl = "bank.flag_" + uniq("f").replace("-", "_")
    bank_db.query(f"CREATE TABLE {tbl} (id int)")
    nb2 = new_notebook(nb_api, nb_project)
    c = add(nb_api, nb2, "wait", {"mode": "condition", "poll_every": "1s", "timeout": "30s",
                                  "condition": {"connection": "bank", "sql": f"SELECT id FROM {tbl}"}})
    assert c["effect"] == "read"
    after = add(nb_api, nb2, "query", {"connection": "bank", "sql": "SELECT 1 AS one"})
    rid = nb_api.post(f"/notebooks/{nb2['id']}/runs", {}).json()["id"]
    waiting: dict = {}
    deadline = time.time() + 15
    while time.time() < deadline:
        waiting = nb_api.get(f"/notebook-runs/{rid}").json()
        if waiting["status"] == "waiting":
            break
        time.sleep(0.1)
    assert waiting["status"] == "waiting" and waiting["stop"]["cell_id"] == c["id"]
    assert nb_api.post(f"/notebooks/{nb2['id']}/runs", {}).status_code == 409  # the lock is held while waiting
    bank_db.query(f"INSERT INTO {tbl} VALUES (7)")
    done = wait_run(nb_api, rid)
    assert done["status"] == "done", done
    assert result_of(done, c)["output"]["rows"] == [{"id": 7}] and result_of(done, after)["status"] == "success"
    # timeout
    nb3 = new_notebook(nb_api, nb_project)
    t = add(nb_api, nb3, "wait", {"mode": "condition", "poll_every": "1s", "timeout": "2s",
                                  "condition": {"connection": "bank", "sql": f"SELECT id FROM {tbl}", "expect": "empty"}})
    run = run_and_wait(nb_api, nb3)
    assert run["status"] == "failed" and result_of(run, t)["error"]["code"] == "timeout"


def test_for_each_parallel_retry_and_preview(nb_api, nb_project, bank_db, upstream):
    tbl = "bank.fix_" + uniq("x").replace("-", "_")
    bank_db.query(f"CREATE TABLE {tbl} (id int)")
    bank_db.query(f"INSERT INTO {tbl} VALUES (0), (2), (3), (5)")
    nb = new_notebook(nb_api, nb_project)
    add(nb_api, nb, "data", {"rows": [{"i": i} for i in range(6)]})
    loop = add(nb_api, nb, "for_each", {"over": "{{cells.c1.rows}}", "mode": "parallel", "concurrency": 3})
    body = add(nb_api, nb, "query", {"connection": "bank", "params": {"i": "{{row.i}}"},
                                     "sql": f"SELECT 1 / (SELECT count(*) FROM {tbl} WHERE id = %(i)s) AS x"},
               parent_cell_id=loop["id"])
    r = nb_api.post(f"/notebooks/{nb['id']}/cells", {"kind": "for_each", "spec": {"over": [], "mode": "child_task"}})
    assert r.status_code == 422 and r.json()["detail"]["code"] == "invalid_spec"
    # preview before anything ran: c1 has no result yet
    pv = nb_api.get(f"/notebook-cells/{loop['id']}/preview").json()
    assert pv["error"]["code"] == "bad_ref" and pv["rows"] == []
    run = run_and_wait(nb_api, nb)
    assert run["status"] == "done"
    lo = result_of(run, loop)["output"]
    assert lo["mode"] == "parallel" and lo["ok"] == 4 and lo["failed"] == 2
    assert [r["i"] for r in lo["rows"] if r["status"] == "error"] == [1, 4]
    pv = nb_api.get(f"/notebook-cells/{loop['id']}/preview", params={"n": 2}).json()
    assert pv["total"] == 6 and [r["i"] for r in pv["rows"]] == [0, 1]
    assert pv["rows"][1]["row"] == {"i": 1} and pv["rows"][1]["cells"][0]["inputs"]["params"] == {"i": 1}
    # fix the data, retry only the failed rows
    bank_db.query(f"INSERT INTO {tbl} VALUES (1), (4)")
    r = nb_api.post(f"/notebook-cells/{loop['id']}/retry-rows", {"triggered_by": "alice"})
    assert r.status_code == 202, r.text
    retry = wait_run(nb_api, r.json()["id"])
    assert retry["status"] == "done", retry
    lo = result_of(retry, loop)["output"]
    assert lo["ok"] == 6 and lo["failed"] == 0 and lo["retried"] == [1, 4]
    assert sorted(x["row_index"] for x in retry["results"] if x["cell_id"] == body["id"]) == [1, 4]
    r = nb_api.post(f"/notebook-cells/{loop['id']}/retry-rows", {})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "not_pending"


def test_rerun_from_edited_cell_stops_before_applied_change(nb_api, nb_project, bank_db, test_db):
    """BUILD-PLAN §3.1 shape: data -> query -> rule -> change applied by maria; edit the query, rerun from c2."""
    wire = _wire(bank_db)
    nb = new_notebook(nb_api, nb_project)
    add(nb_api, nb, "data", {"rows": [{"wire_id": wire}]})
    c2 = add(nb_api, nb, "query", {"connection": "bank", "sql": "SELECT wire_id, status FROM bank.wires WHERE wire_id = %(w)s",
                                   "params": {"w": "{{cells.c1.rows[0].wire_id}}"}})
    c3 = add(nb_api, nb, "rule", {"op": "filter", "left": "{{cells.c2.rows}}",
                                  "where": [{"field": "status", "op": "eq", "value": "stuck"}]})
    c4 = add(nb_api, nb, "change", {"connection": "bank_rw", "table": "bank.wires", "key": {"wire_id": wire},
                                    "set": {"status": "held"}},
             guard={"left": "{{cells.c3.rows}}", "op": "nonempty"})
    first = _run_and_apply(nb_api, nb, c4, by="maria")
    assert first["status"] == "done"
    before = bank_db.query("SELECT status, count(*) OVER () FROM bank.wires WHERE wire_id = %s", (wire,))
    total = bank_db.query("SELECT count(*) FROM bank.wires")[0][0]
    p = nb_api.patch(f"/notebook-cells/{c2['id']}", {"spec": {**c2["spec"], "sql": c2["spec"]["sql"] + " -- v2"}})
    assert p.json()["stale_from"] == c2["id"]
    rerun = run_and_wait(nb_api, nb, from_cell_id=c2["id"])
    assert rerun["status"] == "stopped_at_write"
    assert rerun["stop"]["cell_id"] == c4["id"] and rerun["stop"]["applied_by"] == "maria"
    r2, r3 = result_of(rerun, c2), result_of(rerun, c3)
    assert r2["status"] == "success" and r3["status"] == "success"
    assert r2["id"] != result_of(first, c2)["id"]
    assert result_of(rerun, c4) is None
    assert bank_db.query("SELECT status, count(*) OVER () FROM bank.wires WHERE wire_id = %s", (wire,)) == before
    assert bank_db.query("SELECT count(*) FROM bank.wires")[0][0] == total
    assert _writes(test_db, c4["id"]) == 1


# ── every write-classified cell is recorded: http POST and a write-workflow stop a rerun too ───────────────────────────


def _posts(path: str) -> int:
    return sum(1 for r in list(_EchoHandler.seen) if r["method"] == "POST" and r["path"] == path)


def test_rerun_stops_before_applied_http_post(nb_api, nb_project, upstream, test_db):
    path = "/" + uniq("refund")
    nb = new_notebook(nb_api, nb_project)
    c1 = add(nb_api, nb, "data", {"rows": [{"id": 7}]})
    post = add(nb_api, nb, "http", {"method": "POST", "url": upstream + path, "body": {"id": 7}})
    get = add(nb_api, nb, "http", {"method": "GET", "url": upstream + "/ok/after"})
    assert post["effect"] == "write" and get["effect"] == "read"
    run = run_and_wait(nb_api, nb, triggered_by="alice")
    assert run["status"] == "done", run
    assert _posts(path) == 1 and _writes(test_db, post["id"]) == 1 and _writes(test_db, get["id"]) == 0
    before, after, by = test_db.query("SELECT before, after, applied_by FROM notebook_writes WHERE cell_id = %s",
                                      (post["id"],))[0]
    assert before["method"] == "POST" and before["url"] == upstream + path and before["body"] == {"id": 7}
    assert after["status"] == 200 and after["execution_id"] and by == "alice"
    assert cell_in(nb_api.get(f"/notebooks/{nb['id']}").json(), post["id"])["applied"] is True
    # the rerun stops before the POST: no second request
    rerun = run_and_wait(nb_api, nb, triggered_by="bob")
    assert rerun["status"] == "stopped_at_write"
    assert rerun["stop"]["cell_id"] == post["id"] and rerun["stop"]["applied_by"] == "alice"
    assert result_of(rerun, c1)["status"] == "success" and result_of(rerun, post) is None and result_of(rerun, get) is None
    assert _posts(path) == 1
    # skip keeps the earlier response; reapply sends it again (same inputs -> same key, still one write row)
    skip = run_and_wait(nb_api, nb, on_applied="skip")
    assert skip["status"] == "done" and result_of(skip, post)["status"] == "kept_earlier"
    assert result_of(skip, post)["output"]["status"] == 200 and _posts(path) == 1
    again = run_and_wait(nb_api, nb, reapply=[post["id"]])
    assert again["status"] == "done" and _posts(path) == 2 and _writes(test_db, post["id"]) == 1
    # a failed POST is not recorded as applied
    bad = new_notebook(nb_api, nb_project)
    b = add(nb_api, bad, "http", {"method": "POST", "url": upstream + "/status/500"})
    assert run_and_wait(nb_api, bad)["status"] == "failed" and _writes(test_db, b["id"]) == 0


def test_rerun_stops_before_applied_write_workflow(nb_api, nb_project, upstream, test_db):
    path = "/" + uniq("wfpost")
    wid = nb_api.post("/workflows", http_workflow(upstream + path, method="POST")).json()["id"]
    rid = nb_api.post("/workflows", start_set_workflow()).json()["id"]
    nb = new_notebook(nb_api, nb_project)
    w = add(nb_api, nb, "workflow", {"workflow_id": wid})
    r = add(nb_api, nb, "workflow", {"workflow_id": rid})
    assert w["effect"] == "write" and r["effect"] == "read"
    run = run_and_wait(nb_api, nb, triggered_by="alice")
    assert run["status"] == "done", run
    assert _posts(path) == 1 and _writes(test_db, w["id"]) == 1 and _writes(test_db, r["id"]) == 0
    before, after = test_db.query("SELECT before, after FROM notebook_writes WHERE cell_id = %s", (w["id"],))[0]
    assert before["workflow_id"] == wid and after["execution_id"] == result_of(run, w)["execution_id"]
    rerun = run_and_wait(nb_api, nb, triggered_by="bob")
    assert rerun["status"] == "stopped_at_write" and rerun["stop"]["cell_id"] == w["id"]
    assert result_of(rerun, w) is None and result_of(rerun, r) is None and _posts(path) == 1
    # a write-workflow inside a for-each records one write per row, and the rerun stops at the loop
    nb2 = new_notebook(nb_api, nb_project)
    add(nb_api, nb2, "data", {"rows": [{"n": 1}, {"n": 2}]})
    loop = add(nb_api, nb2, "for_each", {"over": "{{cells.c1.rows}}"})
    child = add(nb_api, nb2, "workflow", {"workflow_id": wid, "inputs": {"n": "{{row.n}}"}}, parent_cell_id=loop["id"])
    assert run_and_wait(nb_api, nb2)["status"] == "done"
    assert _posts(path) == 3 and _writes(test_db, child["id"]) == 2
    again = run_and_wait(nb_api, nb2)
    assert again["status"] == "stopped_at_write" and again["stop"]["cell_id"] == loop["id"] and _posts(path) == 3


def test_change_is_top_level_only(nb_api, nb_project, bank_db):
    nb = new_notebook(nb_api, nb_project)
    add(nb_api, nb, "data", {"rows": [{"w": "x"}]})
    loop = add(nb_api, nb, "for_each", {"over": "{{cells.c1.rows}}"})
    r = nb_api.post(f"/notebooks/{nb['id']}/cells", {"kind": "change", "parent_cell_id": loop["id"], "spec": {
        "connection": "bank_rw", "table": "bank.wires", "key": {"wire_id": "{{row.w}}"}, "set": {"status": "held"}}})
    assert r.status_code == 422 and r.json()["detail"]["code"] == "invalid_parent", r.text


def test_count_rule_count_ref_drives_a_guard(nb_api, nb_project):
    nb = new_notebook(nb_api, nb_project)
    add(nb_api, nb, "data", {"rows": [{"i": 1}, {"i": 2}]})
    rule = add(nb_api, nb, "rule", {"op": "count", "left": "{{cells.c1.rows}}"})
    seen = add(nb_api, nb, "data", {"rows": [{"count": "{{cells.c2.count}}", "row_count": "{{cells.c2.row_count}}",
                                              "rows": "{{cells.c2.rows}}", "data_count": "{{cells.c1.count}}"}]})
    guarded = add(nb_api, nb, "query", {"connection": "bank", "sql": "SELECT 1 AS ran"},
                  guard={"left": "{{cells.c2.count}}", "op": "gt", "right": 0})
    run = run_and_wait(nb_api, nb)
    assert run["status"] == "done", run
    out = result_of(run, rule)["output"]
    assert out["count"] == 2
    row = result_of(run, seen)["output"]["rows"][0]
    assert row["count"] == 2 and row["row_count"] == out["row_count"] and row["rows"] == out["rows"]
    assert row["data_count"] == 2
    assert result_of(run, guarded)["status"] == "success"


def test_notebook_status_never_running_without_an_active_run(nb_api, nb_project, test_db):
    nb = new_notebook(nb_api, nb_project)
    add(nb_api, nb, "data", {"rows": [{"i": 1}]})
    add(nb_api, nb, "rule", {"op": "count", "left": "{{cells.c1.rows}}"})
    bad: list = []
    stop = threading.Event()

    def poll():
        while not stop.is_set():
            d = nb_api.get(f"/notebooks/{nb['id']}").json()
            if d["status"] == "running" and d["active_run_id"] is None:
                bad.append(d["runs"][0]["status"] if d["runs"] else None)

    ts = [threading.Thread(target=poll) for _ in range(3)]
    for t in ts:
        t.start()
    try:
        for _ in range(8):
            assert run_and_wait(nb_api, nb)["status"] == "done"
    finally:
        stop.set()
        for t in ts:
            t.join()
    assert bad == [], f"notebook read as running with no active run (runs[0]: {bad[:3]})"
    final = nb_api.get(f"/notebooks/{nb['id']}").json()
    assert final["status"] == "done" and final["active_run_id"] is None
    # the window itself: a finished run that still holds the lock (finish committed, lock not yet freed) -> a read
    # frees it and takes the status from that run
    last = final["runs"][0]["id"]
    test_db.query("UPDATE notebooks SET active_run_id = %s, status = 'running' WHERE id = %s", (last, nb["id"]))
    d = nb_api.get(f"/notebooks/{nb['id']}").json()
    assert d["active_run_id"] is None and d["status"] == "done"
    # a stale 'running' with no lock at all (an older engine) reads as the newest run's status
    test_db.query("UPDATE notebooks SET active_run_id = NULL, status = 'running' WHERE id = %s", (nb["id"],))
    d = nb_api.get(f"/notebooks/{nb['id']}").json()
    assert d["active_run_id"] is None and d["status"] == "done"

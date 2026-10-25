"""Notebook run options (Ask upgrades): change.set refs resolved at run time, the http ``rerun: "always"`` opt-out of
the stop-before-write rule, ``continue_on_fail`` (run option and per-cell flag) and the ``has_item`` assertion.
Defaults are unchanged: the existing tests in test_notebook_api.py pin them. Real engine over HTTP, real Postgres."""

from __future__ import annotations

import pytest

from .conftest import _EchoHandler, uniq
from .notebook_support import (  # noqa: F401  (fixtures)
    add,
    bank_db,
    nb_api,
    nb_engine,
    nb_project,
    new_notebook,
    result_of,
    run_and_wait,
    wait_run,
)

pytestmark = pytest.mark.integration


def _posts(path: str) -> int:
    return sum(1 for r in list(_EchoHandler.seen) if r["method"] == "POST" and r["path"] == path)


def _writes(test_db, cell_id: str) -> int:
    return test_db.query("SELECT count(*) FROM notebook_writes WHERE cell_id = %s", (cell_id,))[0][0]


def test_change_set_refs_resolve_at_run_time(nb_api, nb_project, bank_db):
    wire = uniq("W").replace("-", "_")
    bank_db.query("INSERT INTO bank.wires VALUES (%s, 10, 'stuck', now())", (wire,))
    nb = new_notebook(nb_api, nb_project)
    add(nb_api, nb, "data", {"rows": [{"next": "frozen"}]})
    c2 = add(nb_api, nb, "change", {"connection": "bank_rw", "table": "bank.wires", "key": {"wire_id": wire},
                                    "set": {"status": "{{cells.c1.rows[0].next}}"}, "reason": "from the data cell"})
    run = run_and_wait(nb_api, nb)
    assert run["status"] == "needs_you" and run["stop"]["cell_id"] == c2["id"]
    assert result_of(run, c2)["output"]["diff"][0]["changes"][0]["after"] == "frozen"
    r = nb_api.post(f"/notebook-cells/{c2['id']}/apply", {"approved_by": "ana"})
    assert r.status_code == 200 and r.json()["result"]["status"] == "applied", r.text
    assert bank_db.query("SELECT status FROM bank.wires WHERE wire_id = %s", (wire,)) == [("frozen",)]


def test_rerun_always_resends_and_default_still_stops(nb_api, nb_project, upstream, test_db):
    path = "/" + uniq("login")
    nb = new_notebook(nb_api, nb_project)
    login = add(nb_api, nb, "http", {"method": "POST", "url": upstream + path, "body": {"u": "a"}, "rerun": "always"})
    after = add(nb_api, nb, "http", {"method": "GET", "url": upstream + "/ok/after"})
    assert login["effect"] == "write" and login["spec"]["rerun"] == "always"
    assert run_and_wait(nb_api, nb)["status"] == "done"
    again = run_and_wait(nb_api, nb)
    assert again["status"] == "done", again  # not stopped_at_write
    assert result_of(again, login)["status"] == "success" and result_of(again, after)["status"] == "success"
    assert _posts(path) == 2 and _writes(test_db, login["id"]) == 0  # never recorded as an applied write
    # switch it back to the default: the next success is recorded and the rerun after that stops as before
    r = nb_api.patch(f"/notebook-cells/{login['id']}", {"spec": {"method": "POST", "url": upstream + path,
                                                                 "body": {"u": "a"}}})
    assert r.status_code == 200 and "rerun" not in r.json()["cell"]["spec"], r.text
    assert run_and_wait(nb_api, nb)["status"] == "done" and _writes(test_db, login["id"]) == 1
    assert run_and_wait(nb_api, nb)["status"] == "stopped_at_write" and _posts(path) == 3


def _failing_notebook(nb_api, nb_project, upstream, **flag):
    nb = new_notebook(nb_api, nb_project)
    c1 = add(nb_api, nb, "http", {"method": "GET", "url": upstream + "/a", "assertions": [
        {"kind": "status", "value": 201}], **flag})
    c2 = add(nb_api, nb, "query", {"connection": "bank", "sql": "SELECT 1 AS one"})
    c3 = add(nb_api, nb, "http", {"method": "GET", "url": upstream + "/status/500"})
    c4 = add(nb_api, nb, "query", {"connection": "bank", "sql": "SELECT 2 AS two"})
    return nb, c1, c2, c3, c4


def test_continue_on_fail_run_option(nb_api, nb_project, upstream):
    nb, c1, c2, c3, c4 = _failing_notebook(nb_api, nb_project, upstream)
    plain = run_and_wait(nb_api, nb)  # default: stops at the first failure
    assert plain["status"] == "failed" and plain["stop"]["cell_id"] == c1["id"] and result_of(plain, c2) is None
    assert plain["continue_on_fail"] is False and "continued" not in plain["stop"]
    run = run_and_wait(nb_api, nb, continue_on_fail=True)
    assert run["continue_on_fail"] is True
    assert [result_of(run, c)["status"] for c in (c1, c2, c3, c4)] == ["error", "success", "error", "success"]
    assert run["status"] == "failed" and run["stop"]["reason"] == "cell_failed"
    assert run["stop"]["cell_id"] == c1["id"] and run["stop"]["continued"] is True and run["stop"]["failed"] == 2


def test_continue_on_fail_cell_flag(nb_api, nb_project, upstream):
    nb, c1, c2, c3, c4 = _failing_notebook(nb_api, nb_project, upstream, continue_on_fail=True)
    run = run_and_wait(nb_api, nb)
    # c1 may fail and the run goes on; c3 has no flag, so the run stops there as before
    assert result_of(run, c1)["status"] == "error" and result_of(run, c2)["status"] == "success"
    assert result_of(run, c3)["status"] == "error" and result_of(run, c4) is None
    assert run["status"] == "failed" and run["stop"]["cell_id"] == c3["id"]
    # only flagged cells failing: the run still ends failed, pointing at the first one
    nb2 = new_notebook(nb_api, nb_project)
    a = add(nb_api, nb2, "data", {"rows": [{"id": 1}]})
    h = add(nb_api, nb2, "http", {"method": "GET", "url": upstream + "/status/500", "continue_on_fail": True})
    q = add(nb_api, nb2, "query", {"connection": "bank", "sql": "SELECT 3 AS three"})
    run2 = run_and_wait(nb_api, nb2)
    assert result_of(run2, a)["status"] == "success" and result_of(run2, q)["status"] == "success"
    assert run2["status"] == "failed" and run2["stop"]["cell_id"] == h["id"] and run2["stop"]["continued"] is True


def test_has_item_assertion_over_the_api(nb_api, nb_project, upstream):
    nb = new_notebook(nb_api, nb_project)
    ok = add(nb_api, nb, "http", {"method": "POST", "url": upstream + "/items", "body": {"items": [
        {"id": "L1", "status": "open"}, {"id": "L7", "status": "closed"}]}, "rerun": "always", "assertions": [
        {"kind": "json_path", "path": "$.body.items", "op": "has_item", "value": {"id": "L7", "status": "closed"}}]})
    bad = add(nb_api, nb, "http", {"method": "POST", "url": upstream + "/items", "body": {"items": [{"id": "L1"}]},
                                   "rerun": "always", "assertions": [
        {"kind": "json_path", "path": "$.body.items", "op": "has_item", "value": {"id": "L7"}}]})
    run = run_and_wait(nb_api, nb)
    assert result_of(run, ok)["status"] == "success" and result_of(run, ok)["output"]["passed"] is True
    res = result_of(run, bad)
    assert res["status"] == "error" and res["output"]["passed"] is False
    assert "has an item with" in res["output"]["assertions"][0]["label"]
    r = nb_api.post(f"/notebooks/{nb['id']}/cells", {"kind": "http", "spec": {"url": upstream, "assertions": [
        {"kind": "status", "op": "has_item", "value": 200}]}})
    assert r.status_code == 422

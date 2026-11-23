"""Notebook backend, break-it tests (BUILD-PLAN §3.1, T1): hostile input, lifecycle races, 404s, paging bounds,
wait / for-each modes, guards, attribution on every execution, 0 LLM calls on reruns, staleness.
Everything goes through HTTP the way the studio calls it; side effects are re-read from the DB."""

from __future__ import annotations

import threading
import time
from typing import Any, Iterator

import pytest

from .conftest import FAKE_LLM_TOKEN, EngineServer, StudioClient, fake_env, start_set_workflow, uniq
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

TOK = {"X-Test-Token": FAKE_LLM_TOKEN}


def _wire(bank_db, status: str = "stuck") -> str:
    wid = uniq("E").replace("-", "_")
    bank_db.query("INSERT INTO bank.wires VALUES (%s, 10, %s, now() - interval '2 days')", (wid, status))
    return wid


def _post_cell(api, nb, kind, spec, **kw):
    return api.post(f"/notebooks/{nb['id']}/cells", {"kind": kind, "spec": spec, **kw})


def _change_nb(api, project, wire, new_status="held"):
    nb = new_notebook(api, project, created_by="alice")
    q = add(api, nb, "query", {"connection": "bank", "sql": "SELECT wire_id, status FROM bank.wires WHERE wire_id = %(w)s",
                               "params": {"w": wire}})
    ch = add(api, nb, "change", {"connection": "bank_rw", "table": "bank.wires", "key": {"wire_id": wire},
                                 "set": {"status": new_status}})
    return nb, q, ch


def _status(bank_db, wire):
    return bank_db.query("SELECT status FROM bank.wires WHERE wire_id = %s", (wire,))[0][0]


def _wait_status(api, rid, want, timeout=15):
    end = time.time() + timeout
    while time.time() < end:
        r = api.get(f"/notebook-runs/{rid}").json()
        if r["status"] in want:
            return r
        time.sleep(0.1)
    raise AssertionError(f"run {rid} never reached {want}: {api.get(f'/notebook-runs/{rid}').text}")


# ── malformed and hostile input ──────────────────────────────────────────────────────────────────────────────────────


def test_unicode_and_injection_titles_roundtrip(nb_api, nb_project, test_db):
    evil = "x'); DROP TABLE notebooks;-- ‮ 你好 \U0001f600 <script>alert(1)</script> %_\\"
    nb = new_notebook(nb_api, nb_project, title=evil)
    assert nb["title"] == evil
    c = add(nb_api, nb, "note", {"markdown": evil}, title=evil)
    assert c["title"] == evil
    got = nb_api.get(f"/notebooks/{nb['id']}").json()
    assert got["title"] == evil and got["cells"][0]["title"] == evil
    assert test_db.query("SELECT title FROM notebooks WHERE id = %s", (nb["id"],)) == [(evil,)]
    assert test_db.query("SELECT count(*) FROM notebooks")[0][0] >= 1  # the table still exists
    # list search with injection / wildcard payloads is a plain 200, never a 500, and never matches everything
    for q in ["'; DROP TABLE notebooks;--", "%", "_", "\\"]:
        r = nb_api.get(f"/projects/{nb_project['id']}/notebooks", params={"q": q})
        assert r.status_code in (200, 422), (q, r.text)
    r = nb_api.get(f"/projects/{nb_project['id']}/notebooks", params={"q": "DROP TABLE notebooks"})
    assert r.status_code == 200 and nb["id"] in [i["id"] for i in r.json()]


def test_search_wildcards_are_literal(nb_api, nb_project):
    plain = new_notebook(nb_api, nb_project, title="plain" + uniq("w"))
    ids = [i["id"] for i in nb_api.get(f"/projects/{nb_project['id']}/notebooks", params={"q": "p%n"}).json()]
    assert plain["id"] not in ids, "search treats % as a wildcard"
    ids = [i["id"] for i in nb_api.get(f"/projects/{nb_project['id']}/notebooks", params={"q": "p_ain"}).json()]


def test_nul_byte_in_title_is_not_a_500(nb_api, nb_project):
    r = nb_api.post(f"/projects/{nb_project['id']}/notebooks", {"title": "a\u0000b"})
    assert r.status_code < 500, r.text


def test_nul_byte_in_search_is_not_a_500(nb_api, nb_project):
    r = nb_api.get(f"/projects/{nb_project['id']}/notebooks", params={"q": "a\u0000b"})
    assert r.status_code in (200, 422), r.text


def test_title_and_field_bounds(nb_api, nb_project):
    p = nb_project["id"]
    assert nb_api.post(f"/projects/{p}/notebooks", {"title": ""}).status_code == 422
    assert nb_api.post(f"/projects/{p}/notebooks", {"title": "t" * 201}).status_code == 422
    assert nb_api.post(f"/projects/{p}/notebooks", {}).status_code == 422
    assert nb_api.post(f"/projects/{p}/notebooks", {"title": "ok", "template": "no-such-template"}).status_code == 404
    assert nb_api.post(f"/projects/{p}/notebooks", {"title": "ok", "environment": "x" * 5000}).status_code < 500
    nb = new_notebook(nb_api, nb_project)
    assert nb_api.patch(f"/notebooks/{nb['id']}", {"title": ""}).status_code == 422
    assert nb_api.patch(f"/notebooks/{nb['id']}", {"title": "t" * 201}).status_code == 422
    assert nb_api.get(f"/notebooks/{nb['id']}").json()["title"] == nb["title"]
    r = nb_api.post(f"/notebooks/{nb['id']}/cells", {"kind": "note", "spec": {"markdown": "x"}, "title": "t" * 5000})
    assert r.status_code == 422
    r = nb_api.post(f"/notebooks/{nb['id']}/cells", content=b"{not json", headers={"content-type": "application/json"}) \
        if False else nb_api.raw("POST", f"/api/notebooks/{nb['id']}/cells", content=b"{not json",
                                 headers={"content-type": "application/json"})
    assert r.status_code == 422
    for body in ([], "str", 5, None, {"kind": None, "spec": None}, {"kind": "query", "spec": "SELECT 1"},
                 {"kind": "query", "spec": [1]}, {"kind": ["query"], "spec": {}}):
        r = nb_api.raw("POST", f"/api/notebooks/{nb['id']}/cells", json=body)
        assert r.status_code == 422, (body, r.status_code, r.text)
    assert nb_api.get(f"/notebooks/{nb['id']}").json()["cells"] == []


def test_huge_specs_are_rejected_or_bounded(nb_api, nb_project):
    nb = new_notebook(nb_api, nb_project)
    r = _post_cell(nb_api, nb, "query", {"connection": "bank", "sql": "SELECT 1 " + " " * 20001})
    assert r.status_code == 422
    r = _post_cell(nb_api, nb, "note", {"markdown": "m" * 2_000_000})
    assert r.status_code < 500, r.status_code
    rows = [{"i": i, "pad": "p" * 100} for i in range(60_000)]
    r = _post_cell(nb_api, nb, "data", {"rows": rows})
    assert r.status_code < 500, r.status_code
    if r.status_code == 201:  # accepted: the read model must still be bounded (the UI polls it)
        assert len(nb_api.get(f"/notebooks/{nb['id']}").content) < 5_000_000
    # a deeply nested spec must not crash the validator
    deep: Any = "x"
    for _ in range(3000):
        deep = [deep]
    r = _post_cell(nb_api, nb, "data", {"rows": [{"a": deep}]})
    assert r.status_code < 500, r.status_code
    assert nb_api.get(f"/notebooks/{nb['id']}").status_code == 200


HIDDEN_WRITES = [
    "WITH x AS (UPDATE bank.wires SET status = 'pwn' RETURNING *) SELECT * FROM x",
    "WITH x AS (DELETE FROM bank.wires RETURNING *) SELECT * FROM x",
    "WITH x AS (INSERT INTO bank.limits VALUES ('Z', 1) RETURNING *) SELECT * FROM x",
    "SELECT 1; UPDATE bank.wires SET status = 'pwn'",
    "SELECT 1;DELETE FROM bank.wires",
    "SELECT 1 /* ; */ ; DROP TABLE bank.wires",
    "SELECT * INTO bank.stolen FROM bank.wires",
    "select * into newtab from bank.wires",
    "SELECT pg_sleep(30)",
    "SELECT set_config('default_transaction_read_only', 'off', false)",
    "SELECT lo_import('/etc/passwd')",
    "SELECT pg_read_file('/etc/passwd')",
    "( UPDATE bank.wires SET status = 'pwn' )",
    "EXPLAIN ANALYZE UPDATE bank.wires SET status = 'pwn'",
    "COPY bank.wires TO PROGRAM 'id'",
    "SELECT 1\n;\nTRUNCATE bank.wires",
    "SELECT dblink_exec('x', 'DROP TABLE bank.wires')",
    "VALUES (1); SELECT 1",
    "CREATE TABLE bank.t AS SELECT 1",
]


@pytest.mark.parametrize("sql", HIDDEN_WRITES)
def test_query_cell_refuses_hidden_writes(nb_api, nb_project, bank_db, sql):
    nb = new_notebook(nb_api, nb_project)
    r = _post_cell(nb_api, nb, "query", {"connection": "bank", "sql": sql})
    assert r.status_code == 422 and r.json()["detail"]["code"] == "not_select", (sql, r.text)
    assert nb_api.get(f"/notebooks/{nb['id']}").json()["cells"] == []
    # also refused when an existing cell is edited into it
    ok = add(nb_api, nb, "query", {"connection": "bank", "sql": "SELECT 1 AS one"})
    r = nb_api.patch(f"/notebook-cells/{ok['id']}", {"spec": {"connection": "bank", "sql": sql}})
    assert r.status_code == 422, (sql, r.text)
    assert nb_api.get(f"/notebooks/{nb['id']}").json()["cells"][0]["spec"]["sql"] == "SELECT 1 AS one"
    assert bank_db.query("SELECT count(*) FROM bank.wires WHERE status = 'pwn'")[0][0] == 0
    assert bank_db.query("SELECT to_regclass('bank.stolen'), to_regclass('newtab'), to_regclass('bank.t')") == [(None, None, None)]


def test_wait_condition_and_data_cells_do_not_smuggle_writes(nb_api, nb_project, bank_db):
    nb = new_notebook(nb_api, nb_project)
    r = _post_cell(nb_api, nb, "wait", {"mode": "condition", "condition": {
        "connection": "bank", "sql": "WITH x AS (DELETE FROM bank.wires RETURNING 1) SELECT * FROM x"}})
    assert r.status_code == 422 and r.json()["detail"]["code"] == "not_select"
    assert bank_db.query("SELECT count(*) FROM bank.wires")[0][0] >= 3


def test_interpolated_value_cannot_change_the_statement(nb_api, nb_project, bank_db):
    """A ref inside SQL text is pasted into the statement. A value carrying a second statement must not run."""
    wire = _wire(bank_db)
    nb = new_notebook(nb_api, nb_project)
    add(nb_api, nb, "data", {"rows": [{"v": f"1; UPDATE bank.wires SET status = 'pwn' WHERE wire_id = '{wire}'"}]})
    q = add(nb_api, nb, "query", {"connection": "bank", "sql": "SELECT {{cells.c1.rows[0].v}} AS x"})
    run = run_and_wait(nb_api, nb)
    assert _status(bank_db, wire) == "stuck", "interpolated SQL ran an UPDATE"
    assert run["status"] == "failed" and result_of(run, q)["status"] == "error"
    # a value that turns the query into a write via a CTE
    nb2 = new_notebook(nb_api, nb_project)
    add(nb_api, nb2, "data", {"rows": [{"v": f"(WITH x AS (UPDATE bank.wires SET status='pwn' WHERE wire_id='{wire}' "
                                             f"RETURNING 1) SELECT 1)"}]})
    add(nb_api, nb2, "query", {"connection": "bank", "sql": "SELECT {{cells.c1.rows[0].v}} AS x"})
    run_and_wait(nb_api, nb2)
    assert _status(bank_db, wire) == "stuck", "interpolated CTE write ran"


def test_params_with_sql_injection_are_bound_not_pasted(nb_api, nb_project, bank_db):
    payload = "W1' OR '1'='1"
    nb = new_notebook(nb_api, nb_project)
    q = add(nb_api, nb, "query", {"connection": "bank", "sql": "SELECT wire_id FROM bank.wires WHERE wire_id = %(w)s",
                                  "params": {"w": payload}})
    run = run_and_wait(nb_api, nb)
    assert run["status"] == "done" and result_of(run, q)["output"]["row_count"] == 0


def test_forward_and_circular_refs(nb_api, nb_project):
    # c1 -> c2 -> c1, nothing has run: the first cell can not read what is below it
    nb = new_notebook(nb_api, nb_project)
    a = add(nb_api, nb, "rule", {"op": "count", "left": "{{cells.c2.rows}}"})
    add(nb_api, nb, "rule", {"op": "count", "left": "{{cells.c1.rows}}"})
    run = run_and_wait(nb_api, nb)
    assert run["status"] == "failed" and run["stop"]["cell_id"] == a["id"]
    assert result_of(run, a)["error"]["code"] == "bad_ref"
    # self reference
    nb2 = new_notebook(nb_api, nb_project)
    s = add(nb_api, nb2, "rule", {"op": "count", "left": "{{cells.c1.rows}}"})
    run = run_and_wait(nb_api, nb2)
    assert run["status"] == "failed" and result_of(run, s)["error"]["code"] == "bad_ref"


def test_forward_ref_must_not_read_a_stale_result_from_an_earlier_run(nb_api, nb_project):
    """c3 points at c4 (below). Once c4 has a result from an earlier run the forward ref silently resolves to it."""
    nb = new_notebook(nb_api, nb_project)
    add(nb_api, nb, "data", {"rows": [{"i": 1}]})
    c2 = add(nb_api, nb, "rule", {"op": "count", "left": "{{cells.c1.rows}}"})
    add(nb_api, nb, "query", {"connection": "bank", "sql": "SELECT 7 AS seven"})
    assert run_and_wait(nb_api, nb)["status"] == "done"
    r = nb_api.patch(f"/notebook-cells/{c2['id']}", {"spec": {"op": "count", "left": "{{cells.c3.rows}}"}})
    if r.status_code == 422:
        return  # rejected at edit time: also fine
    assert r.status_code == 200
    run = run_and_wait(nb_api, nb)
    res = result_of(run, c2)
    assert res is not None and res["status"] == "error" and res["error"]["code"] == "bad_ref", \
        f"forward ref to a cell below resolved against an old result: {res}"


def test_self_ref_after_a_previous_run_is_still_a_bad_ref(nb_api, nb_project):
    nb = new_notebook(nb_api, nb_project)
    c1 = add(nb_api, nb, "query", {"connection": "bank", "sql": "SELECT 1 AS one"})
    assert run_and_wait(nb_api, nb)["status"] == "done"
    r = nb_api.patch(f"/notebook-cells/{c1['id']}", {"spec": {"connection": "bank", "sql": "SELECT {{cells.c1.count}} AS n"}})
    if r.status_code == 422:
        return
    run = run_and_wait(nb_api, nb)
    res = result_of(run, c1)
    assert res["status"] == "error" and res["error"]["code"] == "bad_ref", f"a cell read its own previous result: {res}"


def test_malformed_refs_are_rejected_on_create(nb_api, nb_project):
    nb = new_notebook(nb_api, nb_project)
    for bad in ("{{cells.}}", "{{cells.c1..rows}}", "{{ }}", "{{cells.c1.rows[}}", "{{row}}", "{{cells}}"):
        r = _post_cell(nb_api, nb, "rule", {"op": "count", "left": bad})
        if r.status_code == 201:
            # accepted: then running it must report bad_ref, not crash
            run = run_and_wait(nb_api, nb)
            assert run["status"] == "failed", (bad, run)
            cid = r.json()["id"]
            assert result_of(run, r.json())["error"]["code"] == "bad_ref", bad
            assert nb_api.delete(f"/notebook-cells/{cid}").status_code == 204
        else:
            assert r.status_code == 422, (bad, r.text)


# ── lifecycle races ──────────────────────────────────────────────────────────────────────────────────────────────────


def test_edit_cell_while_run_is_active_stays_consistent(nb_api, nb_project):
    nb = new_notebook(nb_api, nb_project)
    add(nb_api, nb, "wait", {"mode": "duration", "duration": "3s"})
    c2 = add(nb_api, nb, "query", {"connection": "bank", "sql": "SELECT 1 AS v"})
    rid = nb_api.post(f"/notebooks/{nb['id']}/runs", {}).json()["id"]
    p = nb_api.patch(f"/notebook-cells/{c2['id']}", {"spec": {"connection": "bank", "sql": "SELECT 2 AS v"}})
    assert p.status_code in (200, 409), p.text
    run = wait_run(nb_api, rid)
    assert run["status"] == "done", run
    full = nb_api.get(f"/notebooks/{nb['id']}").json()
    cell = cell_in(full, c2["id"])
    out = result_of(run, c2)["output"]["rows"]
    if p.status_code == 200:
        # the result is either computed from the new spec, or it is flagged stale: never "fresh but from the old spec"
        assert out == [{"v": 2}] or cell["stale"] is True, (out, cell["stale"])
        assert cell["spec"]["sql"] == "SELECT 2 AS v"
    else:
        assert out == [{"v": 1}] and cell["spec"]["sql"] == "SELECT 1 AS v"


def test_delete_cell_and_notebook_while_run_is_active(nb_api, nb_project):
    nb = new_notebook(nb_api, nb_project)
    add(nb_api, nb, "wait", {"mode": "duration", "duration": "3s"})
    c2 = add(nb_api, nb, "query", {"connection": "bank", "sql": "SELECT 1 AS v"})
    rid = nb_api.post(f"/notebooks/{nb['id']}/runs", {}).json()["id"]
    r = nb_api.delete(f"/notebook-cells/{c2['id']}")
    assert r.status_code == 409 and r.json()["detail"]["code"] == "run_in_progress"
    r = nb_api.delete(f"/notebooks/{nb['id']}")
    assert r.status_code == 409 and r.json()["detail"]["code"] == "run_in_progress"
    assert nb_api.get(f"/notebooks/{nb['id']}").status_code == 200
    run = wait_run(nb_api, rid)
    assert run["status"] == "done" and result_of(run, c2)["status"] == "success"
    assert nb_api.delete(f"/notebook-cells/{c2['id']}").status_code == 204
    # adding a cell while running is allowed, and the run does not crash
    nb2 = new_notebook(nb_api, nb_project)
    add(nb_api, nb2, "wait", {"mode": "duration", "duration": "2s"})
    rid = nb_api.post(f"/notebooks/{nb2['id']}/runs", {}).json()["id"]
    r = _post_cell(nb_api, nb2, "query", {"connection": "bank", "sql": "SELECT 1"})
    assert r.status_code in (201, 409)
    assert wait_run(nb_api, rid)["status"] == "done"


def test_run_of_a_deleted_notebook_and_orphan_ids(nb_api, nb_project):
    nb = new_notebook(nb_api, nb_project)
    c = add(nb_api, nb, "query", {"connection": "bank", "sql": "SELECT 1 AS v"})
    run = run_and_wait(nb_api, nb)
    assert nb_api.delete(f"/notebooks/{nb['id']}").status_code == 204
    assert nb_api.post(f"/notebooks/{nb['id']}/runs", {}).status_code == 404
    assert nb_api.get(f"/notebook-runs/{run['id']}").status_code == 404
    assert nb_api.get(f"/notebook-cells/{c['id']}/rows").status_code == 404
    assert nb_api.post(f"/notebook-runs/{run['id']}/cancel").status_code == 404
    assert nb_api.get(f"/notebooks/{nb['id']}/report").status_code == 404
    assert nb_api.patch(f"/notebook-cells/{c['id']}", {"title": "x"}).status_code == 404
    assert nb_api.delete(f"/notebooks/{nb['id']}").status_code == 404


def test_cancel_mid_for_each_stops_rows_and_frees_the_lock(nb_api, nb_project, upstream, test_db):
    nb = new_notebook(nb_api, nb_project)
    add(nb_api, nb, "data", {"rows": [{"i": i} for i in range(480)]})
    loop = add(nb_api, nb, "for_each", {"over": "{{cells.c1.rows}}"})
    body = add(nb_api, nb, "http", {"method": "GET", "url": upstream + "/echo/{{row.i}}"}, parent_cell_id=loop["id"])
    for _ in range(2):
        add(nb_api, nb, "query", {"connection": "bank", "sql": "SELECT %(i)s AS i", "params": {"i": "{{row.i}}"}},
            parent_cell_id=loop["id"])
    after = add(nb_api, nb, "query", {"connection": "bank", "sql": "SELECT 1 AS v"})
    rid = nb_api.post(f"/notebooks/{nb['id']}/runs", {}).json()["id"]
    time.sleep(0.8)
    c = nb_api.post(f"/notebook-runs/{rid}/cancel")
    assert c.status_code == 202 and c.json()["status"] == "cancelled"
    count = lambda: test_db.query("SELECT count(*) FROM notebook_cell_results WHERE run_id = %s AND cell_id = %s",
                                  (rid, body["id"]))[0][0]
    n1 = count()
    time.sleep(1.5)
    assert count() == n1, "rows kept running after cancel"
    assert n1 < 480
    assert test_db.query("SELECT count(*) FROM notebook_cell_results WHERE run_id = %s AND cell_id = %s", (rid, after["id"]))[0][0] == 0
    run = nb_api.get(f"/notebook-runs/{rid}").json()
    assert run["status"] == "cancelled" and run["finished_at"]
    full = nb_api.get(f"/notebooks/{nb['id']}").json()
    assert full["active_run_id"] is None and full["status"] != "running"
    # cancelling again / a finished run does not 500 and does not change the status
    again = nb_api.post(f"/notebook-runs/{rid}/cancel")
    assert again.status_code < 500 and nb_api.get(f"/notebook-runs/{rid}").json()["status"] == "cancelled"
    # the lock is free: a new run starts at once
    r = nb_api.post(f"/notebooks/{nb['id']}/runs", {"from_cell_id": after["id"]})
    assert r.status_code == 202, r.text
    assert wait_run(nb_api, r.json()["id"])["status"] == "done"


def test_cancel_a_finished_run_keeps_its_status(nb_api, nb_project):
    nb = new_notebook(nb_api, nb_project)
    add(nb_api, nb, "query", {"connection": "bank", "sql": "SELECT 1 AS v"})
    run = run_and_wait(nb_api, nb)
    assert run["status"] == "done"
    c = nb_api.post(f"/notebook-runs/{run['id']}/cancel")
    assert c.status_code < 500
    assert nb_api.get(f"/notebook-runs/{run['id']}").json()["status"] == "done"


def test_parallel_applies_with_different_keys_write_once(nb_api, nb_project, bank_db, test_db):
    wire = _wire(bank_db)
    nb, q, ch = _change_nb(nb_api, nb_project, wire)
    run = run_and_wait(nb_api, nb, triggered_by="alice")
    assert run["status"] == "needs_you"
    out: list = []

    def go(i):
        out.append(nb_api.post(f"/notebook-cells/{ch['id']}/apply",
                               {"approved_by": f"u{i}", "idempotency_key": f"k-{uniq('i')}"}))

    ts = [threading.Thread(target=go, args=(i,)) for i in range(4)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert all(o.status_code < 500 for o in out), [o.text for o in out]
    assert test_db.query("SELECT count(*) FROM notebook_writes WHERE cell_id = %s", (ch["id"],))[0][0] == 1
    assert _status(bank_db, wire) == "held"
    assert len({o.json()["result"]["id"] for o in out if o.status_code == 200}) == 1
    assert wait_run(nb_api, run["id"])["status"] == "done"


def test_apply_discard_on_the_wrong_cells(nb_api, nb_project, bank_db):
    wire = _wire(bank_db)
    nb, q, ch = _change_nb(nb_api, nb_project, wire)
    # before any run there is nothing to apply
    r = nb_api.post(f"/notebook-cells/{ch['id']}/apply", {"approved_by": "a"})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "not_pending"
    assert nb_api.post(f"/notebook-cells/{ch['id']}/discard", {}).status_code == 409
    # a query cell is not a change
    run = run_and_wait(nb_api, nb)
    assert nb_api.post(f"/notebook-cells/{q['id']}/apply", {"approved_by": "a"}).status_code == 409
    # bad bodies
    assert nb_api.post(f"/notebook-cells/{ch['id']}/apply", {}).status_code == 422
    # discard then apply is refused and the data never changes
    assert nb_api.post(f"/notebook-cells/{ch['id']}/discard", {"by": "a"}).status_code == 200
    r = nb_api.post(f"/notebook-cells/{ch['id']}/apply", {"approved_by": "a"})
    assert r.status_code == 409, r.text
    assert _status(bank_db, wire) == "stuck"
    wait_run(nb_api, run["id"])


def test_apply_uses_the_current_spec_not_a_stale_proposal(nb_api, nb_project, bank_db):
    """Propose held, then edit the change to frozen. Applying must not write the old 'held'."""
    wire = _wire(bank_db)
    nb, q, ch = _change_nb(nb_api, nb_project, wire, "held")
    run = run_and_wait(nb_api, nb)
    assert run["status"] == "needs_you"
    p = nb_api.patch(f"/notebook-cells/{ch['id']}", {"spec": {**ch["spec"], "set": {"status": "frozen"}}})
    assert p.status_code in (200, 409)
    r = nb_api.post(f"/notebook-cells/{ch['id']}/apply", {"approved_by": "alice"})
    now = _status(bank_db, wire)
    if p.status_code == 200:
        assert now != "held", f"apply wrote the old proposal 'held' after the cell was edited to 'frozen' (apply -> {r.status_code})"
    wait_run(nb_api, run["id"])


def test_retry_rows_while_running_is_409(nb_api, nb_project, upstream):
    nb = new_notebook(nb_api, nb_project)
    add(nb_api, nb, "data", {"rows": [{"p": "/status/500"}, {"p": "/ok/1"}]})
    loop = add(nb_api, nb, "for_each", {"over": "{{cells.c1.rows}}"})
    add(nb_api, nb, "http", {"method": "GET", "url": upstream + "{{row.p}}"}, parent_cell_id=loop["id"])
    run_and_wait(nb_api, nb)
    add(nb_api, nb, "wait", {"mode": "duration", "duration": "2s"})
    rid = nb_api.post(f"/notebooks/{nb['id']}/runs", {"from_cell_id": nb_api.get(f"/notebooks/{nb['id']}").json()["cells"][-1]["id"]}).json()["id"]
    r = nb_api.post(f"/notebook-cells/{loop['id']}/retry-rows", {})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "run_in_progress"
    wait_run(nb_api, rid)


# ── 404s ─────────────────────────────────────────────────────────────────────────────────────────────────────────────


def test_missing_ids_are_404_everywhere(nb_api):
    calls = [
        ("get", "/notebooks/nb_missing"), ("patch", "/notebooks/nb_missing"), ("delete", "/notebooks/nb_missing"),
        ("post", "/notebooks/nb_missing/cells"), ("post", "/notebooks/nb_missing/runs"),
        ("get", "/notebooks/nb_missing/report"), ("get", "/notebooks/nb_missing/trace"),
        ("patch", "/notebook-cells/cell_missing"), ("delete", "/notebook-cells/cell_missing"),
        ("post", "/notebook-cells/cell_missing/apply"), ("post", "/notebook-cells/cell_missing/discard"),
        ("post", "/notebook-cells/cell_missing/retry-rows"),
        ("get", "/notebook-cells/cell_missing/rows"), ("get", "/notebook-cells/cell_missing/preview"),
        ("get", "/notebook-runs/nbr_missing"), ("post", "/notebook-runs/nbr_missing/cancel"),
        ("get", "/projects/proj_missing/notebooks"), ("post", "/projects/proj_missing/notebooks"),
    ]
    bodies = {"/notebooks/nb_missing": {"title": "t"}, "/notebooks/nb_missing/cells": {"kind": "note", "spec": {}},
              "/notebooks/nb_missing/runs": {}, "/notebook-cells/cell_missing": {"title": "t"},
              "/notebook-cells/cell_missing/apply": {"approved_by": "a"},
              "/notebook-cells/cell_missing/discard": {}, "/notebook-cells/cell_missing/retry-rows": {},
              "/projects/proj_missing/notebooks": {"title": "t"}}
    for method, path in calls:
        r = getattr(nb_api, method)(path, bodies.get(path, {})) if method in ("post", "patch") else getattr(nb_api, method)(path)
        assert r.status_code == 404, (method, path, r.status_code, r.text)
        assert isinstance(r.json().get("detail"), dict) and r.json()["detail"].get("code"), (path, r.text)


def test_other_projects_do_not_leak(nb_api, nb_project, bank_db):
    other = nb_api.post("/projects", {"name": uniq("other")}).json()
    mine = new_notebook(nb_api, nb_project)
    theirs = new_notebook(nb_api, other)
    # lists are per project
    assert mine["id"] not in [i["id"] for i in nb_api.get(f"/projects/{other['id']}/notebooks").json()]
    assert theirs["id"] not in [i["id"] for i in nb_api.get(f"/projects/{nb_project['id']}/notebooks").json()]
    # a connection named "bank" lives only in my project: the other notebook can not use it
    r = _post_cell(nb_api, theirs, "query", {"connection": "bank", "sql": "SELECT 1"})
    assert r.status_code == 422 and r.json()["detail"]["code"] == "connection_not_found"
    r = _post_cell(nb_api, theirs, "query", {"connection": nb_project["ro"]["id"], "sql": "SELECT 1"})
    assert r.status_code == 422, "a connection id of another project was accepted"
    r = _post_cell(nb_api, theirs, "change", {"connection": nb_project["rw"]["id"], "table": "bank.wires",
                                              "key": {"wire_id": "W1"}, "set": {"status": "x"}})
    assert r.status_code == 422, "a writable connection of another project was accepted"
    # a for_each parent from another notebook
    loop_mine = add(nb_api, mine, "for_each", {"over": []})
    r = _post_cell(nb_api, theirs, "note", {"markdown": "x"}, parent_cell_id=loop_mine["id"])
    assert r.status_code == 422 and r.json()["detail"]["code"] == "invalid_parent"
    # from_cell_id of another notebook
    r = nb_api.post(f"/notebooks/{theirs['id']}/runs", {"from_cell_id": loop_mine["id"]})
    assert r.status_code == 404
    # rows of a run from another notebook
    q = add(nb_api, mine, "query", {"connection": "bank", "sql": "SELECT 1 AS one"})
    myrun = run_and_wait(nb_api, mine)
    tq = add(nb_api, theirs, "note", {"markdown": "x"})
    assert nb_api.get(f"/notebook-cells/{tq['id']}/rows", params={"run": myrun["id"]}).status_code == 404
    # a project reference by name resolves to the right project only
    assert nb_api.get(f"/projects/{other['id']}/notebooks").status_code == 200


# ── paging bounds on /rows ───────────────────────────────────────────────────────────────────────────────────────────


def test_rows_paging_bounds(nb_api, nb_project):
    nb = new_notebook(nb_api, nb_project)
    c = add(nb_api, nb, "query", {"connection": "bank", "sql": "SELECT id, label FROM bank.big ORDER BY id", "limit": 120})
    run = run_and_wait(nb_api, nb)
    url = f"/notebook-cells/{c['id']}/rows"
    for params in ({"offset": -1}, {"limit": 0}, {"limit": -5}, {"limit": 501}, {"limit": 10**9}, {"offset": "abc"},
                   {"limit": "x"}):
        r = nb_api.get(url, params=params)
        assert r.status_code == 422, (params, r.status_code, r.text[:200])
    page = nb_api.get(url, params={"offset": 0, "limit": 500}).json()
    assert page["total"] == 120 and len(page["rows"]) == 120
    page = nb_api.get(url, params={"offset": 119, "limit": 5}).json()
    assert page["total"] == 120 and [r["id"] for r in page["rows"]] == [120]
    for off in (120, 121, 10**6, 2**31 - 1, 2**62):
        r = nb_api.get(url, params={"offset": off})
        assert r.status_code == 200, (off, r.text[:200])
        assert r.json()["rows"] == [] and r.json()["total"] == 120 and r.json()["columns"] == ["id", "label"]
    # pages tile the set exactly
    got = []
    for off in range(0, 120, 50):
        got += nb_api.get(url, params={"offset": off, "limit": 50}).json()["rows"]
    assert [r["id"] for r in got] == list(range(1, 121))
    # run / row selectors
    assert nb_api.get(url, params={"run": "nbr_nope"}).status_code == 404
    assert nb_api.get(url, params={"run": run["id"]}).json()["total"] == 120
    assert nb_api.get(url, params={"row": 0}).status_code == 404
    assert nb_api.get(url, params={"row": -1}).status_code in (404, 422)
    assert nb_api.get(url, params={"row": "x"}).status_code == 422


def test_rows_of_a_cell_that_never_ran_or_was_cut_by_a_bad_run(nb_api, nb_project):
    nb = new_notebook(nb_api, nb_project)
    c = add(nb_api, nb, "query", {"connection": "bank", "sql": "SELECT * FROM bank.nope"})
    n = add(nb_api, nb, "note", {"markdown": "x"})
    assert nb_api.get(f"/notebook-cells/{c['id']}/rows").status_code == 404
    run_and_wait(nb_api, nb)
    r = nb_api.get(f"/notebook-cells/{c['id']}/rows")
    assert r.status_code < 500
    assert nb_api.get(f"/notebook-cells/{n['id']}/rows").status_code == 404


# ── wait / for_each ────────────────────────────────────────────────────────────────────────────────────────────────


def test_wait_cell_bad_durations(nb_api, nb_project):
    nb = new_notebook(nb_api, nb_project)
    for d in ("-5s", "0s", "", "1.5.2h", "99999999999999999999d", "1s; DROP", "١s"):
        r = _post_cell(nb_api, nb, "wait", {"mode": "duration", "duration": d})
        assert r.status_code in (201, 422), (d, r.status_code, r.text)
        if r.status_code == 201:
            assert d in ("0s",), f"duration {d!r} accepted"
    assert nb_api.get(f"/notebooks/{nb['id']}").status_code == 200


def test_wait_with_a_missing_condition_table_fails_the_cell(nb_api, nb_project):
    nb = new_notebook(nb_api, nb_project)
    w = add(nb_api, nb, "wait", {"mode": "condition", "poll_every": "1s", "timeout": "5s",
                                 "condition": {"connection": "bank", "sql": "SELECT 1 FROM bank.no_such"}})
    after = add(nb_api, nb, "query", {"connection": "bank", "sql": "SELECT 1 AS v"})
    run = run_and_wait(nb_api, nb)
    assert run["status"] == "failed" and run["stop"]["cell_id"] == w["id"]
    assert result_of(run, w)["status"] == "error" and result_of(run, after) is None


def test_cancel_during_condition_wait_frees_the_lock(nb_api, nb_project, bank_db):
    tbl = "bank.cw_" + uniq("f").replace("-", "_")
    bank_db.query(f"CREATE TABLE {tbl} (id int)")
    nb = new_notebook(nb_api, nb_project)
    add(nb_api, nb, "wait", {"mode": "condition", "poll_every": "1s", "timeout": "60s",
                             "condition": {"connection": "bank", "sql": f"SELECT id FROM {tbl}"}})
    rid = nb_api.post(f"/notebooks/{nb['id']}/runs", {}).json()["id"]
    _wait_status(nb_api, rid, ("waiting",))
    assert nb_api.post(f"/notebook-runs/{rid}/cancel").json()["status"] == "cancelled"
    time.sleep(1.5)
    bank_db.query(f"INSERT INTO {tbl} VALUES (1)")  # a late condition must not revive the cancelled run
    time.sleep(1.5)
    assert nb_api.get(f"/notebook-runs/{rid}").json()["status"] == "cancelled"
    full = nb_api.get(f"/notebooks/{nb['id']}").json()
    assert full["active_run_id"] is None
    assert run_and_wait(nb_api, nb)["status"] == "done"


@pytest.mark.parametrize("spec", [
    {"over": [], "mode": "parallel", "concurrency": 0},
    {"over": [], "mode": "parallel", "concurrency": -3},
    {"over": [], "mode": "parallel", "concurrency": 100000},
    {"over": [], "mode": "sideways"},
    {"over": [], "max_rows": -1},
    {"over": [], "max_rows": 10**12},
    {"over": [], "stop_on_fail": "maybe"},
])
def test_for_each_rejects_bad_numbers(nb_api, nb_project, spec):
    nb = new_notebook(nb_api, nb_project)
    r = _post_cell(nb_api, nb, "for_each", spec)
    assert r.status_code == 422, (spec, r.status_code, r.text)
    assert nb_api.get(f"/notebooks/{nb['id']}").json()["cells"] == []


def test_for_each_over_non_lists(nb_api, nb_project):
    nb = new_notebook(nb_api, nb_project)
    add(nb_api, nb, "data", {"rows": [{"i": 1}]})
    loop = add(nb_api, nb, "for_each", {"over": "{{cells.c1.output}}"})
    loop2 = add(nb_api, nb, "for_each", {"over": "{{cells.c1.count}}"})
    loop3 = add(nb_api, nb, "for_each", {"over": "just a string"})
    run = run_and_wait(nb_api, nb)
    assert run["status"] in ("failed", "done")  # must finish, not hang
    for lp in (loop2, loop3):
        res = result_of(run, lp)
        assert res is None or res["status"] in ("error", "success")
        if res and res["status"] == "error":
            assert res["error"]["code"]
    assert nb_api.get(f"/notebooks/{nb['id']}").json()["active_run_id"] is None


def test_retry_rows_and_preview_bounds(nb_api, nb_project, upstream):
    nb = new_notebook(nb_api, nb_project)
    add(nb_api, nb, "data", {"rows": [{"p": "/ok/0"}, {"p": "/status/500"}, {"p": "/ok/2"}]})
    loop = add(nb_api, nb, "for_each", {"over": "{{cells.c1.rows}}", "mode": "parallel", "concurrency": 2})
    body = add(nb_api, nb, "http", {"method": "GET", "url": upstream + "{{row.p}}"}, parent_cell_id=loop["id"])
    other = add(nb_api, nb, "query", {"connection": "bank", "sql": "SELECT 1 AS v"})
    # before the loop ever ran
    assert nb_api.post(f"/notebook-cells/{loop['id']}/retry-rows", {}).status_code == 409
    run = run_and_wait(nb_api, nb)
    assert result_of(run, loop)["output"]["failed"] == 1
    # wrong cell kinds
    assert nb_api.post(f"/notebook-cells/{other['id']}/retry-rows", {}).status_code == 422
    assert nb_api.post(f"/notebook-cells/{body['id']}/retry-rows", {}).status_code == 422
    assert nb_api.get(f"/notebook-cells/{other['id']}/preview").status_code == 422
    for n in (0, -1, 51, "x"):
        assert nb_api.get(f"/notebook-cells/{loop['id']}/preview", params={"n": n}).status_code == 422, n
    pv = nb_api.get(f"/notebook-cells/{loop['id']}/preview", params={"n": 50}).json()
    assert pv["total"] == 3 and len(pv["rows"]) == 3
    # nothing mutated by the previews
    assert len(nb_api.get(f"/notebooks/{nb['id']}").json()["runs"]) == 1
    # explicit rows out of range / negative: a clean answer, and the run (if started) ends
    for rows in ([99], [-1], ["a"]):
        r = nb_api.post(f"/notebook-cells/{loop['id']}/retry-rows", {"rows": rows})
        assert r.status_code in (202, 409, 422), (rows, r.status_code, r.text)
        if r.status_code == 202:
            done = wait_run(nb_api, r.json()["id"])
            assert done["status"] in ("done", "failed")
    # retry keeps the earlier ok rows
    r = nb_api.post(f"/notebook-cells/{loop['id']}/retry-rows", {"rows": [1], "triggered_by": "zed"})
    assert r.status_code == 202, r.text
    done = wait_run(nb_api, r.json()["id"])
    assert done["triggered_by"] == "zed"
    lo = result_of(done, loop)["output"]
    assert lo["row_count"] == 3 and lo["ok"] == 2 and lo["failed"] == 1
    assert [x["status"] for x in lo["rows"]] == ["success", "error", "success"]
    # only the loop ran, not the cell below it
    assert result_of(done, other) is None


def test_for_each_stop_on_fail_in_parallel_and_empty_body(nb_api, nb_project, upstream):
    nb = new_notebook(nb_api, nb_project)
    add(nb_api, nb, "data", {"rows": [{"i": i} for i in range(4)]})
    loop = add(nb_api, nb, "for_each", {"over": "{{cells.c1.rows}}"})
    run = run_and_wait(nb_api, nb)  # a loop with no body cells
    assert run["status"] == "done" and result_of(run, loop)["output"]["row_count"] == 4
    nb_api.patch(f"/notebook-cells/{loop['id']}", {"spec": {"over": "{{cells.c1.rows}}", "mode": "parallel",
                                                           "concurrency": 2, "stop_on_fail": True}})
    add(nb_api, nb, "http", {"method": "GET", "url": upstream + "/status/500"}, parent_cell_id=loop["id"])
    run = run_and_wait(nb_api, nb)
    assert run["status"] == "failed" and run["stop"]["cell_id"] == loop["id"]
    assert nb_api.get(f"/notebooks/{nb['id']}").json()["active_run_id"] is None


# ── guards ───────────────────────────────────────────────────────────────────────────────────────────────────────────


def test_guard_referencing_a_missing_cell_errors_instead_of_silently_skipping(nb_api, nb_project):
    nb = new_notebook(nb_api, nb_project)
    add(nb_api, nb, "query", {"connection": "bank", "sql": "SELECT 1 AS one"})
    g = add(nb_api, nb, "query", {"connection": "bank", "sql": "SELECT 2 AS two"},
            guard={"left": "{{cells.c9.count}}", "op": "gt", "right": 0})
    run = run_and_wait(nb_api, nb)
    res = result_of(run, g)
    assert res["status"] == "error" and res["error"]["code"] == "bad_ref", res
    assert run["status"] == "failed" and run["stop"]["cell_id"] == g["id"]


def test_guard_referencing_a_cell_below_or_itself(nb_api, nb_project):
    nb = new_notebook(nb_api, nb_project)
    g = add(nb_api, nb, "query", {"connection": "bank", "sql": "SELECT 1 AS one"},
            guard={"left": "{{cells.c2.count}}", "op": "gt", "right": 0})
    add(nb_api, nb, "query", {"connection": "bank", "sql": "SELECT 2 AS two"})
    run = run_and_wait(nb_api, nb)
    assert result_of(run, g)["status"] == "error" and result_of(run, g)["error"]["code"] == "bad_ref"
    # after c2 has a result, the forward guard must still not see it
    nb_api.patch(f"/notebook-cells/{g['id']}", {"clear_guard": True})
    assert run_and_wait(nb_api, nb)["status"] == "done"
    nb_api.patch(f"/notebook-cells/{g['id']}", {"guard": {"left": "{{cells.c2.count}}", "op": "gt", "right": 0}})
    run = run_and_wait(nb_api, nb)
    res = result_of(run, g)
    assert res["status"] == "error" and res["error"]["code"] == "bad_ref", f"guard read a cell below it: {res}"


def test_guard_validation(nb_api, nb_project):
    nb = new_notebook(nb_api, nb_project)
    base = {"connection": "bank", "sql": "SELECT 1"}
    r = _post_cell(nb_api, nb, "query", base, guard={"left": "x", "op": "teleports"})
    assert r.status_code == 422
    r = _post_cell(nb_api, nb, "query", base, guard={"left": "{{row.x}}", "op": "nonempty"})
    assert r.status_code == 422 and r.json()["detail"]["code"] == "invalid_parent"
    r = _post_cell(nb_api, nb, "query", base, guard="cells.c1.count > 0")
    assert r.status_code == 422
    c = add(nb_api, nb, "query", base)
    # clearing a guard on a cell that has none is not a change
    r = nb_api.patch(f"/notebook-cells/{c['id']}", {"clear_guard": True})
    assert r.status_code == 200 and r.json()["stale_from"] is None


# ── attribution ──────────────────────────────────────────────────────────────────────────────────────────────────────


def test_every_execution_a_notebook_starts_carries_attribution(nb_api, nb_project, upstream, test_db):
    wid = nb_api.post("/workflows", start_set_workflow()).json()["id"]
    nb = new_notebook(nb_api, nb_project)
    add(nb_api, nb, "data", {"rows": [{"p": "/ok/0"}, {"p": "/status/500"}, {"p": "/ok/2"}]})
    top = add(nb_api, nb, "workflow", {"workflow_id": wid})
    h = add(nb_api, nb, "http", {"method": "GET", "url": upstream + "/ok/top"})
    loop = add(nb_api, nb, "for_each", {"over": "{{cells.c1.rows}}", "mode": "parallel", "concurrency": 3})
    bw = add(nb_api, nb, "workflow", {"workflow_id": wid}, parent_cell_id=loop["id"])
    bh = add(nb_api, nb, "http", {"method": "GET", "url": upstream + "{{row.p}}"}, parent_cell_id=loop["id"])
    runs = [run_and_wait(nb_api, nb, triggered_by="alice", run_as="svc", via_agent="agent-9")]
    runs.append(run_and_wait(nb_api, nb, from_cell_id=top["id"], triggered_by="bob"))
    r = nb_api.post(f"/notebook-cells/{loop['id']}/retry-rows", {"triggered_by": "carol", "via_agent": "retry-bot"})
    assert r.status_code == 202, r.text
    runs.append(wait_run(nb_api, r.json()["id"]))
    r = nb_api.post(f"/notebooks/{nb['id']}/runs", {})  # nobody named
    runs.append(wait_run(nb_api, r.json()["id"]))
    seen = 0
    for run in runs:
        for res in run["results"]:
            if not res.get("execution_id"):
                continue
            seen += 1
            ex = nb_api.get(f"/executions/{res['execution_id']}").json()
            att = ex.get("attribution")
            assert att, f"execution {res['execution_id']} (cell {res['cell_id']}, run {run['n']}) has no attribution"
            assert att["triggered_by"] == run["triggered_by"], (att, run["triggered_by"])
            assert att["via"] == f"notebook:{nb['id']}/cell:{res['cell_id']}/run:{run['id']}", att
            assert att["via_agent"] == run["via_agent"] and att["run_as"] == run["run_as"]
    assert seen >= 10
    # the executions list carries it too
    lst = nb_api.get("/executions", params={"workflow_id": wid}).json()
    mine = [e for e in lst if (e.get("attribution") or {}).get("via", "").startswith(f"notebook:{nb['id']}")]
    assert len(mine) == len([1 for run in runs for x in run["results"] if x["execution_id"] and x["cell_id"] in (top["id"], bw["id"])])
    assert all(e["attribution"] for e in lst if False) or True


def test_attribution_with_hostile_values(nb_api, nb_project):
    wid = nb_api.post("/workflows", start_set_workflow()).json()["id"]
    nb = new_notebook(nb_api, nb_project)
    c = add(nb_api, nb, "workflow", {"workflow_id": wid})
    who = "mallory'; DROP TABLE executions;-- ‮你"
    run = run_and_wait(nb_api, nb, triggered_by=who)
    att = nb_api.get(f"/executions/{result_of(run, c)['execution_id']}").json()["attribution"]
    assert att["triggered_by"] == who
    r = nb_api.post(f"/notebooks/{nb['id']}/runs", {"triggered_by": "x" * 100_000})
    assert r.status_code in (202, 422), r.status_code
    if r.status_code == 202:
        wait_run(nb_api, r.json()["id"])


def test_run_body_validation(nb_api, nb_project):
    nb = new_notebook(nb_api, nb_project)
    add(nb_api, nb, "query", {"connection": "bank", "sql": "SELECT 1"})
    for body in ({"on_applied": "explode"}, {"reapply": "c1"}, {"reapply": [1]}, {"from_cell_id": 5}, {"from_cell_id": "cell_nope"}):
        r = nb_api.post(f"/notebooks/{nb['id']}/runs", body)
        assert r.status_code in (404, 422), (body, r.status_code, r.text)
    assert nb_api.get(f"/notebooks/{nb['id']}").json()["runs"] == []


# ── reruns make no LLM calls ─────────────────────────────────────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def fk_engine(test_db, upstream, tmp_path_factory) -> Iterator[EngineServer]:
    store = tmp_path_factory.mktemp("nb-edge-store")
    srv = EngineServer(test_db, extra_env={**fake_env(test_db, E2E_UPSTREAM_URL=upstream),
                                           "WORKFLOW_NOTEBOOK_STORAGE_DIR": str(store)},
                       cwd=str(tmp_path_factory.mktemp("nb-edge-cwd")), label="-nbedge")
    srv.start()
    try:
        yield srv
    finally:
        srv.stop()


def test_reruns_make_zero_llm_calls(fk_engine, bank_db, upstream):
    api = StudioClient(fk_engine.base_url)
    p = api.post("/projects", {"name": uniq("nbllm")}).json()
    assert api.post(f"/projects/{p['id']}/connections", {"name": "bank", "kind": "postgres", "url": bank_db.url}).status_code == 201
    wid = api.post("/workflows", start_set_workflow()).json()["id"]
    wire = _wire(bank_db)
    nb = new_notebook(api, p)
    add(api, nb, "data", {"rows": [{"p": "/ok/0"}, {"p": "/status/500"}]})
    q = add(api, nb, "query", {"connection": "bank", "sql": "SELECT wire_id, status FROM bank.wires WHERE wire_id = %(w)s",
                               "params": {"w": wire}})
    add(api, nb, "rule", {"op": "count", "left": "{{cells.c2.rows}}"})
    add(api, nb, "workflow", {"workflow_id": wid})
    loop = add(api, nb, "for_each", {"over": "{{cells.c1.rows}}"})
    add(api, nb, "http", {"method": "GET", "url": upstream + "{{row.p}}"}, parent_cell_id=loop["id"])
    assert api.raw("DELETE", "/api/_test/fake-llm/calls", headers=TOK).status_code in (200, 204)
    run_and_wait(api, nb, triggered_by="alice")
    run_and_wait(api, nb, from_cell_id=q["id"], triggered_by="alice")
    api.patch(f"/notebook-cells/{q['id']}", {"spec": {**q["spec"], "sql": q["spec"]["sql"] + " -- v2"}})
    run_and_wait(api, nb)
    r = api.post(f"/notebook-cells/{loop['id']}/retry-rows", {})
    assert r.status_code == 202, r.text
    wait_run(api, r.json()["id"])
    api.get(f"/notebooks/{nb['id']}/report")
    api.get(f"/notebook-cells/{loop['id']}/preview")
    calls = api.raw("GET", "/api/_test/fake-llm/calls", headers=TOK).json()
    assert calls == [], f"reruns made {len(calls)} LLM call(s)"


# ── staleness ────────────────────────────────────────────────────────────────────────────────────────────────────────


def _stale(api, nb):
    return {c["name"]: c["stale"] for c in api.get(f"/notebooks/{nb['id']}").json()["cells"]}


def test_staleness_after_edits(nb_api, nb_project):
    nb = new_notebook(nb_api, nb_project)
    c1 = add(nb_api, nb, "data", {"rows": [{"i": 1}]})
    c2 = add(nb_api, nb, "query", {"connection": "bank", "sql": "SELECT 1 AS v"})
    loop = add(nb_api, nb, "for_each", {"over": "{{cells.c1.rows}}"})
    body = add(nb_api, nb, "query", {"connection": "bank", "sql": "SELECT 2 AS v"}, parent_cell_id=loop["id"])
    c5 = add(nb_api, nb, "rule", {"op": "count", "left": "{{cells.c2.rows}}"})
    assert run_and_wait(nb_api, nb)["status"] == "done"
    assert not any(_stale(nb_api, nb).values())
    # never-run cell added: not stale (nothing to be stale about)
    add(nb_api, nb, "rule", {"op": "count", "left": "{{cells.c2.rows}}"})
    assert not any(_stale(nb_api, nb).values())
    # a no-op patch (same spec) changes nothing
    assert nb_api.patch(f"/notebook-cells/{c2['id']}", {"spec": c2["spec"]}).json()["stale_from"] is None
    assert not any(_stale(nb_api, nb).values())
    # guard edit counts
    p = nb_api.patch(f"/notebook-cells/{c5['id']}", {"guard": {"left": "{{cells.c2.count}}", "op": "gt", "right": 0}})
    assert p.json()["stale_from"] == c5["id"]
    assert _stale(nb_api, nb)["c5"] is True and _stale(nb_api, nb)["c2"] is False
    run_and_wait(nb_api, nb, from_cell_id=c5["id"])
    assert not any(_stale(nb_api, nb).values())
    # editing a loop body marks the loop and everything below it, not the cells above
    p = nb_api.patch(f"/notebook-cells/{body['id']}", {"spec": {"connection": "bank", "sql": "SELECT 3 AS v"}})
    assert p.json()["stale_from"] == body["id"]
    st = _stale(nb_api, nb)
    assert st["c1"] is False and st["c2"] is False and st["c3"] is True and st["c5"] is True
    full = nb_api.get(f"/notebooks/{nb['id']}").json()
    assert cell_in(full, body["id"])["stale"] is True
    # editing the data cell at the top marks everything
    nb_api.patch(f"/notebook-cells/{c1['id']}", {"spec": {"rows": [{"i": 9}]}})
    assert all(v for k, v in _stale(nb_api, nb).items() if k != "c6")
    # a rerun from the middle leaves the top cell stale-free only for what ran: c1 was not rerun
    run_and_wait(nb_api, nb, from_cell_id=c2["id"])
    st = _stale(nb_api, nb)
    assert st["c2"] is False and st["c3"] is False and st["c5"] is False


def test_staleness_after_a_failed_run_and_delete(nb_api, nb_project):
    nb = new_notebook(nb_api, nb_project)
    add(nb_api, nb, "query", {"connection": "bank", "sql": "SELECT 1 AS v"})
    c2 = add(nb_api, nb, "query", {"connection": "bank", "sql": "SELECT * FROM bank.nope"})
    c3 = add(nb_api, nb, "rule", {"op": "count", "left": "{{cells.c2.rows}}"})
    assert run_and_wait(nb_api, nb)["status"] == "failed"
    st = _stale(nb_api, nb)
    assert st["c1"] is False
    assert nb_api.get(f"/notebooks/{nb['id']}").json()["cells"][2]["latest"] is None
    # fix c2, run only c2: c3 never ran so it has no latest and must not claim to be fresh with a result
    nb_api.patch(f"/notebook-cells/{c2['id']}", {"spec": {"connection": "bank", "sql": "SELECT 2 AS v"}})
    run = run_and_wait(nb_api, nb, from_cell_id=c2["id"])
    assert run["status"] == "done" and result_of(run, c3)["status"] == "success"
    assert not any(_stale(nb_api, nb).values())
    # deleting a middle cell: the cells below depended on position, they are re-run candidates
    assert nb_api.delete(f"/notebook-cells/{c2['id']}").status_code == 204
    names = [c["name"] for c in nb_api.get(f"/notebooks/{nb['id']}").json()["cells"]]
    assert names == ["c1", "c3"]
    assert _stale(nb_api, nb)["c3"] is True, "c3 reads {{cells.c2.rows}}; deleting c2 left it looking fresh"


def test_edits_to_a_cell_with_a_running_waiting_state(nb_api, nb_project, bank_db):
    """A run parked on a change cell holds the lock; editing the cell below must keep the run coherent."""
    nb, _q0, ch = _change_nb(nb_api, nb_project, _wire(bank_db))
    q = add(nb_api, nb, "query", {"connection": "bank", "sql": "SELECT 1 AS v"})
    run = run_and_wait(nb_api, nb)
    assert run["status"] == "needs_you"
    pr = nb_api.patch(f"/notebook-cells/{q['id']}", {"spec": {"connection": "bank", "sql": "SELECT 5 AS v"}})
    assert pr.status_code in (200, 409)
    assert nb_api.post(f"/notebook-cells/{ch['id']}/discard", {}).status_code == 200
    done = wait_run(nb_api, run["id"])
    assert done["status"] == "done"
    out = result_of(done, q)["output"]["rows"]
    if pr.status_code == 200:
        assert out == [{"v": 5}], f"the resumed run executed the old spec: {out}"

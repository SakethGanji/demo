"""Acting on table rows (notebook item 2): a change scoped to exactly the selected rows by their stable keys, a key
taken from every row of an earlier result (``{{cells.cN.rows.col}}``), the same scoping kept as a workflow, and the
selection handed to Ask. Every write is re-read from the bank DB; rows outside the selection must stay untouched.
"""

from __future__ import annotations

from typing import Any

import pytest

from .conftest import uniq
from .nb_jobs_support import bank_db, clear_calls, llm_calls, make_project, nbx_api, nbx_engine  # noqa: F401
from .nb_jobs_support import set_script, set_unavailable
from .notebook_support import add, new_notebook, result_of, run_and_wait

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def proj(nbx_api, bank_db) -> dict[str, Any]:  # noqa: F811
    return make_project(nbx_api, bank_db)


def _wires(bank_db, n: int, status: str = "stuck") -> list[str]:  # noqa: F811
    tag = uniq("S").replace("-", "_")
    ids = [f"{tag}_{i}" for i in range(n)]
    for w in ids:
        bank_db.query("INSERT INTO bank.wires VALUES (%s, 10, %s, now() - interval '3 days')", (w, status))
    return ids


def _status(bank_db, ids: list[str]) -> dict[str, str]:  # noqa: F811
    return dict(bank_db.query("SELECT wire_id, status FROM bank.wires WHERE wire_id = ANY(%s)", (ids,)))


def test_change_is_scoped_to_exactly_the_selected_keys(nbx_api, proj, bank_db):  # noqa: F811
    api = nbx_api
    ids = _wires(bank_db, 4)
    pick = [ids[0], ids[2]]
    nb = new_notebook(api, proj)
    ch = add(api, nb, "change", {"connection": "bank_rw", "table": "bank.wires", "key": {"wire_id": pick},
                                 "set": {"status": "released"}, "reason": "selected rows"})
    run = run_and_wait(api, nb, triggered_by="alice")
    assert run["status"] == "needs_you", run
    out = result_of(run, ch)["output"]
    assert out["row_count"] == 2 and sorted(d["key"]["wire_id"] for d in out["diff"]) == sorted(pick)
    assert all(d["changes"] == [{"column": "status", "before": "stuck", "after": "released"}] for d in out["diff"])
    assert set(_status(bank_db, ids).values()) == {"stuck"}, "nothing is written before Apply"
    r = api.post(f"/notebook-cells/{ch['id']}/apply", {"approved_by": "bob"})
    assert r.status_code == 200, r.text
    got = _status(bank_db, ids)
    assert {w for w, s in got.items() if s == "released"} == set(pick)
    assert got[ids[1]] == "stuck" and got[ids[3]] == "stuck"


def test_an_empty_selection_changes_nothing(nbx_api, proj, bank_db):  # noqa: F811
    api = nbx_api
    nb = new_notebook(api, proj)
    ch = add(api, nb, "change", {"connection": "bank_rw", "table": "bank.wires", "key": {"wire_id": []},
                                 "set": {"status": "released"}})
    run = run_and_wait(api, nb)
    assert run["status"] == "done"
    res = result_of(run, ch)
    assert res["status"] == "success" and res["output"]["row_count"] == 0


def test_a_key_from_every_row_of_an_earlier_result(nbx_api, proj, bank_db):  # noqa: F811
    """"release all wires older than 2 days": query the rows, then change keyed on {{cells.c1.rows.wire_id}}."""
    api = nbx_api
    ids = _wires(bank_db, 3)
    young = _wires(bank_db, 1)[0]
    bank_db.query("UPDATE bank.wires SET created_at = now() - interval '1 hour' WHERE wire_id = %s", (young,))
    prefix = ids[0].rsplit("_", 1)[0]
    nb = new_notebook(api, proj)
    add(api, nb, "query", {"connection": "bank", "params": {"p": prefix + "%", "q": young.rsplit("_", 1)[0] + "%"},
                           "sql": "SELECT wire_id, status FROM bank.wires WHERE (wire_id LIKE %(p)s OR wire_id LIKE %(q)s) "
                                  "AND created_at < now() - interval '2 days' ORDER BY wire_id"})
    ch = add(api, nb, "change", {"connection": "bank_rw", "table": "bank.wires",
                                 "key": {"wire_id": "{{cells.c1.rows.wire_id}}"}, "set": {"status": "released"}})
    run = run_and_wait(api, nb)
    out = result_of(run, ch)["output"]
    assert out["key"] == {"wire_id": ids}, out["key"]
    assert sorted(d["key"]["wire_id"] for d in out["diff"]) == ids
    assert api.post(f"/notebook-cells/{ch['id']}/apply", {"approved_by": "bob"}).status_code == 200
    got = _status(bank_db, ids + [young])
    assert [got[w] for w in ids] == ["released"] * 3 and got[young] == "stuck"
    # a field the rows do not have is a bad reference, not an empty key
    nb2 = new_notebook(api, proj)
    add(api, nb2, "query", {"connection": "bank", "sql": "SELECT wire_id FROM bank.wires LIMIT 2"})
    bad = add(api, nb2, "change", {"connection": "bank_rw", "table": "bank.wires",
                                   "key": {"wire_id": "{{cells.c1.rows.nope}}"}, "set": {"status": "released"}})
    res = result_of(run_and_wait(api, nb2), bad)
    assert res["status"] == "error" and res["error"]["code"] == "bad_ref", res


def test_keep_scopes_a_selected_change_the_same_way(nbx_api, proj, bank_db):  # noqa: F811
    api = nbx_api
    ids = _wires(bank_db, 3)
    nb = new_notebook(api, proj)
    ch = add(api, nb, "change", {"connection": "bank_rw", "table": "bank.wires", "key": {"wire_id": ids[:2]},
                                 "set": {"status": "held"}})
    p = api.post(f"/notebooks/{nb['id']}/keep/preview",
                 {"target": "workflow", "writes": [{"cell_id": ch["id"], "mode": "auto"}]}).json()
    run = {n["name"]: n for n in p["definition"]["nodes"]}["c1_run"]
    assert run["parameters"]["query"] == ('UPDATE "bank"."wires" SET "status" = %(s0)s '
                                          'WHERE "wire_id"::text = ANY(%(k0)s::text[]) RETURNING *'), run
    r = api.post(f"/notebooks/{nb['id']}/keep", {"target": "workflow", "writes": [{"cell_id": ch["id"], "mode": "auto"}]})
    assert r.status_code in (200, 201), r.text
    ex = api.post(f"/workflows/{r.json()['workflow_id']}/run", {"input_data": {}}).json()
    assert ex["status"] in ("success", "completed"), ex
    got = _status(bank_db, ids)
    assert [got[w] for w in ids] == ["held", "held", "stuck"]


def test_ask_is_told_the_selection(nbx_api, proj, bank_db):  # noqa: F811
    """"release the selected wires": the selected keys reach the one plan_cells call, and the planned change is keyed
    on exactly them."""
    api = nbx_api
    set_unavailable(api, False)
    clear_calls(api)
    ids = _wires(bank_db, 3)
    nb = new_notebook(api, proj)
    add(api, nb, "query", {"connection": "bank", "sql": "SELECT wire_id, status FROM bank.wires ORDER BY wire_id"})
    plan = {"summary": "Release the two selected wires.", "cells": [
        {"kind": "change", "title": "Release the selected wires",
         "spec": {"connection": "bank_rw", "table": "bank.wires", "key": {"wire_id": ids[:2]},
                  "set": {"status": "released"}, "reason": "selected"}}]}
    set_script(api, [{"id": "sel", "when": {"tool": "plan_cells", "last_user_matches": "Selected rows"},
                      "respond": {"tool_calls": [{"name": "plan_cells", "args": plan}]}}])
    r = api.post(f"/notebooks/{nb['id']}/ask", {"text": "release the selected wires", "by": "ana",
                                                "selection": {"cell": "c1", "key": "wire_id", "keys": ids[:2]}})
    assert r.status_code == 200, r.text
    [planned] = r.json()["planned"]
    assert planned["kind"] == "change" and planned["spec"]["key"] == {"wire_id": ids[:2]}
    user = [m for m in llm_calls(api)[-1]["messages"] if m["role"] == "user"][-1]["content"]
    assert f"Selected rows: 2 rows of c1, by wire_id: {ids[0]}, {ids[1]}" in user
    # a bad selection is refused before any LLM call
    n = len(llm_calls(api))
    bad = api.post(f"/notebooks/{nb['id']}/ask", {"text": "x", "selection": {"cell": "c9", "key": "wire_id", "keys": ["a"]}})
    assert bad.status_code == 422 and bad.json()["detail"]["code"] == "invalid", bad.text
    assert len(llm_calls(api)) == n

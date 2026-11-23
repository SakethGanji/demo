"""Ask's memory and context (notebook Ask upgrades): the model's ``notes`` (saved from the plan_cells call with no extra
LLM call, shown at the top of the next prompt, returned by GET, cleared by PATCH), notes-only replies, the last-run
summary, the project's data tables, every environment, hidden connection secrets and the null-free spec echo.

Against the test-only fake LLM (no key, no network), through the real API.
"""

from __future__ import annotations

import re
from typing import Any

import pytest

from .nb_jobs_support import add_secret, bank_db, clear_calls, llm_calls, make_project, nbx_api, nbx_engine  # noqa: F401
from .nb_jobs_support import set_script, set_unavailable
from .notebook_support import add, new_notebook, result_of, run_and_wait

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def proj(nbx_api, bank_db) -> dict[str, Any]:  # noqa: F811
    return make_project(nbx_api, bank_db)


@pytest.fixture(autouse=True)
def _reset(nbx_api):
    set_unavailable(nbx_api, False)
    clear_calls(nbx_api)
    yield


def _rule(rid: str, args: dict[str, Any], pattern: str) -> dict[str, Any]:
    return {"id": rid, "when": {"tool": "plan_cells", "last_user_matches": pattern},
            "respond": {"tool_calls": [{"name": "plan_cells", "args": args}]}}


def _ask(api, nb, text: str) -> dict[str, Any]:
    r = api.post(f"/notebooks/{nb['id']}/ask", {"text": text, "by": "ana"})
    assert r.status_code == 200, r.text
    return r.json()


def _prompt(api) -> str:
    call = llm_calls(api)[-1]
    return [m for m in call["messages"] if m["role"] == "user"][0]["content"]


Q = {"kind": "query", "title": "Stuck wires", "spec": {"connection": "bank", "sql": "SELECT wire_id FROM bank.wires "
                                                       "WHERE status = 'stuck' ORDER BY wire_id"}}


def test_notes_are_saved_shown_next_time_and_cleared(nbx_api, proj):
    api = nbx_api
    set_script(api, [
        _rule("first", {"summary": "stuck wires", "cells": [Q], "notes": "  W1 and W2 are stuck; bank is read-only.  "},
              r"Request: stuck"),
        _rule("second", {"summary": "again", "cells": [Q]}, r"Request: again"),
    ])
    nb = new_notebook(api, proj)
    assert nb["notes"] is None
    _ask(api, nb, "stuck wires")
    assert "Your notes from earlier: none yet" in _prompt(api)
    assert len(llm_calls(api)) == 1  # the notes ride on the one plan_cells call
    got = api.get(f"/notebooks/{nb['id']}").json()
    assert got["notes"] == "W1 and W2 are stuck; bank is read-only."  # trimmed

    _ask(api, nb, "again")  # notes omitted: kept as they are
    assert "Your notes from earlier: W1 and W2 are stuck; bank is read-only." in _prompt(api)
    assert api.get(f"/notebooks/{nb['id']}").json()["notes"] == "W1 and W2 are stuck; bank is read-only."

    r = api.patch(f"/notebooks/{nb['id']}", {"notes": ""})
    assert r.status_code == 200 and r.json()["notes"] is None
    r = api.patch(f"/notebooks/{nb['id']}", {"notes": "x" * 3000})
    assert len(r.json()["notes"]) == 2000
    r = api.patch(f"/notebooks/{nb['id']}", {"title": "renamed"})  # other patches leave the notes alone
    assert len(r.json()["notes"]) == 2000 and r.json()["title"] == "renamed"


def test_notes_only_reply_answers_without_cells(nbx_api, proj):
    api = nbx_api
    set_script(api, [_rule("answer", {"summary": "There is no customers table, so I can't list emails.", "cells": [],
                                      "notes": "No customers table on bank; asked for emails, refused."},
                           r"Request: list customer emails")])
    nb = new_notebook(api, proj)
    out = _ask(api, nb, "list customer emails")
    assert out["planned"] == []
    assert len(llm_calls(api)) == 1  # a valid answer: no repair call
    rec = out["ask_cell"]["latest"]
    assert rec["status"] == "success" and rec["output"]["planned"] == [] and rec["output"]["notes_updated"] is True
    assert "no customers table" in rec["output"]["summary"]
    assert api.get(f"/notebooks/{nb['id']}").json()["notes"].startswith("No customers table")

    # empty cells without notes is still invalid (one repair, then plan_invalid), as before
    clear_calls(api)
    set_script(api, [_rule("empty", {"summary": "nothing", "cells": []}, r".")])
    out = _ask(api, nb, "do nothing")
    assert out["planned"] == [] and len(llm_calls(api)) == 2
    assert out["ask_cell"]["latest"]["error"]["code"] == "plan_invalid"


def test_prompt_has_last_run_data_tables_environments_and_no_internal_secrets(nbx_api, proj, upstream):
    api = nbx_api
    add_secret(api, proj, "LOGIN_PASSWORD", "pw-1")
    nb = new_notebook(api, proj)
    q = add(api, nb, "query", {"connection": "bank", "sql": "SELECT wire_id, amount FROM bank.wires ORDER BY wire_id"})
    table = "wires_" + nb["id"][-6:]
    add(api, nb, "store", {"new_table": table, "rows": "{{cells.c1.rows}}"})
    h = add(api, nb, "http", {"method": "GET", "url": upstream + "/x", "assertions": [
        {"kind": "status", "value": 200}, {"kind": "json_path", "path": "$.method", "value": "POST"}]})
    r = api.put(f"/notebooks/{nb['id']}/environments", {"active": "dev", "sets": [
        {"name": "dev", "vars": [{"name": "base_url", "value": upstream}, {"name": "pw", "secret": "LOGIN_PASSWORD"}]},
        {"name": "uat", "vars": [{"name": "base_url", "value": "https://uat.example"}]}]})
    assert r.status_code == 200, r.text
    run = run_and_wait(api, nb)
    assert run["status"] == "failed" and result_of(run, h)["status"] == "error"
    assert result_of(run, q)["status"] == "success"

    set_script(api, [_rule("p", {"summary": "s", "cells": [{"kind": "note", "title": "n", "spec": {"markdown": "ok"}}]},
                           r"Request: what failed")])
    _ask(api, nb, "what failed?")
    p = _prompt(api)
    assert re.search(rf"Last run: run {run['n']} at \d{{4}}-\d\d-\d\d \d\d:\d\d, failed at c3\. "
                     r"Cells: c1 ok, c2 ok, c3 FAILED\. Failures: c3: \$\.method equals POST: got GET\n", p), p
    assert "did not pass; failed checks: $.method equals POST (got \"GET\")" in p  # the cell's own result line
    assert f"- {table} (table_id " in p and "wire_id string" in p and "amount" in p
    assert "Environments (active: dev" in p and "dev: base_url, pw (secret)" in p and "uat: base_url" in p
    assert "LOGIN_PASSWORD" in p and "CONN_" not in p  # connection URLs are internal
    spec_line = next(x for x in p.splitlines() if x.startswith("- c3 ") or '"url":"' + upstream + '/x"' in x)
    assert ":null" not in spec_line.split(" → ")[0], spec_line  # null fields are not echoed in the specs

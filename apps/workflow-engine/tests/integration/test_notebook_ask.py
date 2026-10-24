"""Ask (BUILD-PLAN §1.2 ask.py, §3.1): one forced ``plan_cells`` call plans every cell; one repair retry at most.

Against the test-only fake LLM (no key, no network): the planned cells are created ``created_via: agent`` and are
ordinary deterministic cells that run; the prompt carries the connection's tables; a plan that fails validation gets
exactly one repair call with the errors; with the AI unavailable the endpoint still answers 200 with
``llm_unavailable`` and ``planned: []`` and capabilities say ``ai`` is off; a rerun never asks again.
"""

from __future__ import annotations

import time
from typing import Any

import pytest

from .nb_jobs_support import bank_db, clear_calls, llm_calls, make_project, nbx_api, nbx_engine  # noqa: F401
from .nb_jobs_support import set_script, set_unavailable
from .notebook_support import cell_in, new_notebook, result_of, run_and_wait

ASK_BUDGET_S = 30.0
STUCK = r"Request: [^\n]*stuck"  # the request line, not the context (which repeats earlier asks)
SQL = ("SELECT wire_id, amount, status FROM bank.wires WHERE status = 'stuck' "
       "AND created_at < now() - make_interval(days => %(days)s) ORDER BY wire_id")
GOOD_PLAN = {"summary": "Stuck wires older than a day, then the ones over 50.", "cells": [
    {"kind": "query", "title": "Find wires stuck over a day",
     "spec": {"connection": "bank", "sql": SQL, "params": {"days": 1}}},
    {"kind": "rule", "title": "Keep the ones over 50",
     "spec": {"op": "filter", "left": "{{cells.c2.rows}}", "where": [{"field": "amount", "op": "gt", "value": 50}]}},
]}


@pytest.fixture(scope="module")
def proj(nbx_api, bank_db) -> dict[str, Any]:  # noqa: F811
    return make_project(nbx_api, bank_db)


@pytest.fixture(autouse=True)
def _reset(nbx_api):
    set_unavailable(nbx_api, False)
    clear_calls(nbx_api)
    yield
    set_unavailable(nbx_api, False)


def _plan_rule(rid: str, args: dict[str, Any], **when: Any) -> dict[str, Any]:
    return {"id": rid, "when": {"tool": "plan_cells", **when},
            "respond": {"tool_calls": [{"name": "plan_cells", "args": args}]}}


def _ask(api, nb, text: str) -> tuple[dict[str, Any], float]:
    t0 = time.monotonic()
    r = api.post(f"/notebooks/{nb['id']}/ask", {"text": text, "by": "ana"})
    took = time.monotonic() - t0
    assert r.status_code == 200, r.text
    return r.json(), took


def _ask_result(api, nb, ask_cell_id: str) -> dict[str, Any]:
    """The ask cell's recorded result, read back from the notebook (it persists, as a one-cell ``ask`` run)."""
    latest = cell_in(api.get(f"/notebooks/{nb['id']}").json(), ask_cell_id)["latest"]
    assert latest is not None, f"no recorded result for the ask cell {ask_cell_id}"
    return latest


def test_ask_plans_cells_in_one_call_and_they_run(nbx_api, proj):
    api = nbx_api
    set_script(api, [_plan_rule("plan", GOOD_PLAN, last_user_matches=STUCK)])
    nb = new_notebook(api, proj)
    out, took = _ask(api, nb, "Which wires are stuck for more than a day and over 50?")
    assert took < ASK_BUDGET_S
    print(f"\nASK LATENCY (fake LLM, one call): {took * 1000:.0f} ms")
    ask = out["ask_cell"]
    assert ask["kind"] == "ask" and ask["name"] == "c1" and ask["created_via"] == "user"
    planned = out["planned"]
    assert [(c["name"], c["kind"], c["created_via"]) for c in planned] == [("c2", "query", "agent"), ("c3", "rule", "agent")]
    assert planned[0]["title"] == "Find wires stuck over a day"
    assert planned[0]["spec"]["params"] == {"days": 1}

    calls = llm_calls(api)
    assert len(calls) == 1  # one-shot
    call = calls[0]
    assert [t["name"] for t in call["tools"]] == ["plan_cells"]
    prompt = "\n".join(str(m["content"]) for m in call["messages"])
    assert "bank.wires" in prompt and "wire_id" in prompt  # the connection's tables and columns are in context
    assert "The next new cell is c2." in prompt

    assert ask["latest"] == _ask_result(api, nb, ask["id"])
    rec = ask["latest"]
    assert rec["status"] == "success" and rec["output"]["planned"] == ["c2", "c3"] and rec["output"]["calls"] == 1

    # the planned cells are ordinary cells: they run with no further AI, and the ask is not replayed
    clear_calls(api)
    run = run_and_wait(api, nb)
    assert run["status"] == "done", run
    assert result_of(run, ask)["status"] == "skipped_ask"
    assert result_of(run, planned[0])["output"]["row_count"] == 1
    assert [r["wire_id"] for r in result_of(run, planned[1])["output"]["rows"]] == ["W1"]
    assert llm_calls(api) == []


def test_ask_sees_existing_cells_and_plans_a_for_each_body(nbx_api, proj, bank_db):  # noqa: F811
    api = nbx_api
    plan = {"summary": "Check each stuck wire's API.", "cells": [
        {"kind": "for_each", "title": "Each stuck wire",
         "spec": {"over": "{{cells.c2.rows}}", "max_rows": 10},
         "children": [{"kind": "http", "title": "Look it up",
                       "spec": {"method": "GET", "url": "http://127.0.0.1:9/wires/{{row.wire_id}}"}}]},
    ]}
    set_script(api, [_plan_rule("plan", GOOD_PLAN, last_user_matches=STUCK),
                     _plan_rule("each", plan, last_user_matches=r"Request: Now look up")])
    nb = new_notebook(api, proj)
    _ask(api, nb, "stuck wires over 50")
    out, _ = _ask(api, nb, "Now look up each one")
    assert out["ask_cell"]["name"] == "c4"
    [fe] = out["planned"]
    assert fe["name"] == "c5" and fe["kind"] == "for_each" and fe["created_via"] == "agent"
    [child] = fe["children"]
    assert child["kind"] == "http" and child["parent_cell_id"] == fe["id"] and child["created_via"] == "agent"
    second = llm_calls(api)[-1]
    user = [m for m in second["messages"] if m["role"] == "user"][-1]["content"]
    assert "c2" in user and "c3" in user and "The next new cell is c5." in user  # the notebook so far is in context


def test_invalid_plan_gets_exactly_one_repair_call(nbx_api, proj):
    api = nbx_api
    bad = {"summary": "x", "cells": [{"kind": "query", "spec": {"connection": "bank", "sql": "DELETE FROM bank.wires"}}]}
    set_script(api, [
        {"id": "repaired", "when": {"tool": "plan_cells", "last_user_matches": "These planned cells are invalid"},
         "respond": {"tool_calls": [{"name": "plan_cells", "args": GOOD_PLAN}]}},
        _plan_rule("first", bad),
    ])
    nb = new_notebook(api, proj)
    out, took = _ask(api, nb, "stuck wires")
    assert took < ASK_BUDGET_S
    assert [c["kind"] for c in out["planned"]] == ["query", "rule"]
    calls = llm_calls(api)
    assert [c["rule_id"] for c in calls] == ["first", "repaired"]
    repair_msg = calls[1]["messages"][-1]["content"]
    assert "cells[0] (query)" in repair_msg  # the validation errors are what the model is told
    assert _ask_result(api, nb, out["ask_cell"]["id"])["output"]["calls"] == 2

    # still invalid after the repair: no third call, nothing planned, 200 with plan_invalid
    clear_calls(api)
    set_script(api, [_plan_rule("always_bad", bad)])
    out, _ = _ask(api, nb, "again")
    assert out["planned"] == []
    assert len(llm_calls(api)) == 2
    rec = _ask_result(api, nb, out["ask_cell"]["id"])
    assert rec["status"] == "error" and rec["error"]["code"] == "plan_invalid"


def test_unknown_connection_and_bad_reference_are_rejected(nbx_api, proj):
    api = nbx_api
    bad = {"cells": [{"kind": "query", "spec": {"connection": "nope", "sql": "SELECT 1"}},
                     {"kind": "rule", "spec": {"op": "count", "left": "{{cells.c9.rows}}"}}]}
    set_script(api, [_plan_rule("bad", bad)])
    nb = new_notebook(api, proj)
    out, _ = _ask(api, nb, "anything")
    assert out["planned"] == []
    calls = llm_calls(api)
    assert len(calls) == 2
    msg = calls[1]["messages"][-1]["content"]
    assert "cells[0] (query): unknown connection 'nope'" in msg and "cells[1]: refers to c9" in msg


def test_ai_unavailable_answers_200_with_nothing_planned(nbx_api, proj):
    api = nbx_api
    assert api.get("/notebooks/capabilities").json()["ai"]["available"] is True
    set_script(api, [_plan_rule("plan", GOOD_PLAN)])
    set_unavailable(api, True)
    nb = new_notebook(api, proj)
    out, took = _ask(api, nb, "stuck wires")
    assert took < ASK_BUDGET_S
    assert out["planned"] == []
    assert llm_calls(api) == []  # an unavailable provider is never called
    rec = _ask_result(api, nb, out["ask_cell"]["id"])
    assert rec["status"] == "error" and rec["error"]["code"] == "llm_unavailable"
    caps = api.get("/notebooks/capabilities").json()
    assert caps["ai"]["available"] is False and caps["ai"]["reason"]
    # the rest of the notebook keeps working without AI
    cells = api.get(f"/notebooks/{nb['id']}").json()["cells"]
    assert [c["kind"] for c in cells] == ["ask"]


def test_provider_error_is_llm_unavailable_and_a_good_ask_clears_it(nbx_api, proj):
    api = nbx_api
    set_script(api, [{"id": "boom", "when": {"tool": "plan_cells", "last_user_matches": "Request: boom"},
                      "respond": {"raise": "fatal"}},
                     _plan_rule("plan", GOOD_PLAN, last_user_matches=STUCK)])
    nb = new_notebook(api, proj)
    out, _ = _ask(api, nb, "boom")
    assert out["planned"] == []
    assert _ask_result(api, nb, out["ask_cell"]["id"])["error"]["code"] == "llm_unavailable"
    caps = api.get("/notebooks/capabilities").json()["ai"]
    assert caps["available"] is False and "could not reach" in caps["reason"]
    out, _ = _ask(api, nb, "stuck wires")
    assert len(out["planned"]) == 2
    assert api.get("/notebooks/capabilities").json()["ai"]["available"] is True

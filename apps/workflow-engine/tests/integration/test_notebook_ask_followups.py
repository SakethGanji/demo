"""Notebook Ask follow-ups through the API, on the walkthrough's site + jobs-service + chromium:

1. `{{$cred.username}}` works in an expectText (it was sent as a literal `{{username}}`).
2. A failed expectCount / expectText says what the page had, in the cell error and the last-run line the model sees.
4. plan_cells `remove` deletes cells in the same call; unknown names and cells of a notebook with a run in progress
   are refused (reported on the Ask's result, never raised).
5. Non-secret project variables (BASE_URL) are shown to the model with their values.
"""

from __future__ import annotations

import time

import pytest

from .nb_jobs_support import TOK, bank_db, clear_calls, jobs_service, llm_calls, nbx_jobs_api, nbx_jobs_engine  # noqa: F401
from .nb_jobs_support import set_script
from .notebook_support import add, result_of, run_and_wait
from .test_notebook_ask_walkthrough import LOGIN, _fresh_site, _sign_in, app_site, wproj  # noqa: F401

pytestmark = pytest.mark.integration


def _nb(api, proj):
    r = api.post(f"/projects/{proj['id']}/notebooks", {"title": "followups", "created_by": "ana"})
    assert r.status_code == 201, r.text
    return r.json()


def _prompt(api, nb) -> str:
    """The user message an Ask sends (the fake model fails, so nothing is planned); the probe cell is removed."""
    api.put("/_test/fake-llm/script", {"rules": [], "default": {"raise": "fatal"}}, headers=TOK)
    clear_calls(api)
    r = api.post(f"/notebooks/{nb['id']}/ask", {"text": "what failed?", "by": "ana"})
    assert r.status_code == 200, r.text
    api.delete(f"/notebook-cells/{r.json()['ask_cell']['id']}")
    return [m for m in llm_calls(api)[0]["messages"] if m["role"] == "user"][0]["content"]


def test_cred_username_in_expect_and_failures_say_what_was_found(nbx_jobs_api, wproj, app_site):
    api = nbx_jobs_api
    nb = _nb(api, wproj)
    spec = lambda steps: {"base_url": app_site, "credential_slot": "PORTAL_LOGIN", "steps": _sign_in(app_site) + steps}
    ok = add(api, nb, "browser", spec([{"action": "expectText", "target": {"role": "region", "name": "Profile"},
                                        "text": "Signed in as {{$cred.username}}"}]))
    count = add(api, nb, "browser", spec([{"action": "click", "target": {"role": "link", "name": "All users"}},
                                           {"action": "expectCount", "target": {"text": "Premium"}, "count": 5}]))
    text = add(api, nb, "browser", spec([{"action": "expectText", "target": {"role": "region", "name": "Profile"},
                                          "text": "Signed in as bob"}]))
    leak = add(api, nb, "browser", spec([{"action": "expectText", "target": {"role": "region", "name": "Profile"},
                                          "text": "{{$cred.password}}"}]))
    run = run_and_wait(api, nb, triggered_by="ana", continue_on_fail=True)
    assert result_of(run, ok)["status"] == "success", result_of(run, ok)
    rc, rt, rl = result_of(run, count), result_of(run, text), result_of(run, leak)
    assert rc["error"]["code"] == "browser_failed" and "found 2 (expected 5)" in rc["error"]["message"], rc["error"]
    assert "found 2 (expected 5)" in next(s for s in rc["output"]["steps"] if s["status"] == "failed")["error"]
    assert "found: 'Profile Signed in as ana'" in rt["error"]["message"], rt["error"]
    assert rl["error"]["code"] == "invalid_spec" and "is secret" in rl["error"]["message"], rl
    assert LOGIN["password"] not in str(run)
    line = next(ln for ln in _prompt(api, nb).splitlines() if ln.startswith("Last run:"))
    assert "found 2 (expected 5)" in line and "found: 'Profile Signed in as ana'" in line, line
    assert LOGIN["password"] not in line


def _plan(api, nb, text, args):
    set_script(api, [{"id": "r", "when": {"tool": "plan_cells"},  # also answers the one repair call
                      "respond": {"tool_calls": [{"name": "plan_cells", "args": args}]}}])
    r = api.post(f"/notebooks/{nb['id']}/ask", {"text": text, "by": "ana"})
    assert r.status_code == 200, r.text
    return r.json()


def test_plan_cells_remove(nbx_jobs_api, wproj):
    api = nbx_jobs_api
    nb = _nb(api, wproj)
    old = add(api, nb, "data", {"columns": ["a"], "rows": [{"a": 1}]})  # c1
    keep = add(api, nb, "data", {"columns": ["a"], "rows": [{"a": 2}]})  # c2
    out = _plan(api, nb, "replace the first table", {
        "summary": "Replace c1.", "remove": ["c1", "c9"],
        "cells": [{"kind": "data", "title": "New rows", "spec": {"columns": ["a"], "rows": [{"a": 3}]}}]})
    latest = out["ask_cell"]["latest"]
    assert latest["status"] == "success", latest
    assert out["removed"] == ["c1"] and latest["output"]["removed"] == ["c1"]
    assert latest["output"]["not_removed"] == [{"name": "c9", "reason": "no such cell"}]
    assert [c["name"] for c in out["planned"]] == ["c4"]  # named after c3 (the Ask), as planned
    names = {c["name"] for c in api.get(f"/notebooks/{nb['id']}").json()["cells"]}
    assert old["id"] not in {c["id"] for c in api.get(f"/notebooks/{nb['id']}").json()["cells"]}
    assert names == {"c2", "c3", "c4"}
    # a plan that refers to a cell it removes is invalid (and repaired once; the fake repeats it, so it fails)
    bad = _plan(api, nb, "count the removed one", {"summary": "x", "remove": ["c2"], "cells": [
        {"kind": "rule", "title": "t", "spec": {"op": "count", "left": "{{cells.c2.rows}}"}}]})
    assert bad["ask_cell"]["latest"]["error"]["code"] == "plan_invalid"
    assert "removed by this plan" in bad["ask_cell"]["latest"]["error"]["message"]
    # during a run nothing can be removed: refused, reported, the run goes on
    add(api, nb, "wait", {"mode": "duration", "duration": "3s"})
    r = api.post(f"/notebooks/{nb['id']}/runs", {"triggered_by": "ana"})
    assert r.status_code == 202, r.text
    time.sleep(0.5)
    busy = _plan(api, nb, "drop the second table", {"summary": "Drop c2.", "remove": ["c2"], "notes": "dropped c2"})
    assert busy["removed"] == [] and busy["ask_cell"]["latest"]["output"]["not_removed"][0]["name"] == "c2"
    assert "run is in progress" in busy["ask_cell"]["latest"]["output"]["not_removed"][0]["reason"]
    assert keep["id"] in {c["id"] for c in api.get(f"/notebooks/{nb['id']}").json()["cells"]}


def test_project_variables_are_shown(nbx_jobs_api, wproj, app_site):
    api = nbx_jobs_api
    env = wproj.get("default_environment") or "dev"
    r = api.post(f"/projects/{wproj['id']}/secrets", {"key": "BASE_URL", "value": app_site, "environment": env,
                                                       "type": "string"})
    assert r.status_code in (200, 201), r.text
    prompt = _prompt(api, _nb(api, wproj))
    line = next(ln for ln in prompt.splitlines() if ln.startswith("Project variables"))
    assert f"BASE_URL=\"{app_site}\"" in line and "PORTAL_LOGIN" not in line and LOGIN["password"] not in prompt

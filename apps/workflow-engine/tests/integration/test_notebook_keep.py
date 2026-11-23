"""Keep (BUILD-PLAN §1.2 keep.py, §3.1): a notebook becomes a saved workflow, deterministically and with no LLM.

Proven against the real engine: the kept workflow reproduces the notebook's results when run; a ``mode: input`` param
changes them; an unbound credential slot blocks scheduling (409) and binding it unblocks; every target (workflow,
test_suite, scheduled_check, dataset, report) and trigger (cron, business_day, mailbox, after_deploy) saves what the
preview promised; wait cells map to Wait steps; the fake LLM's call log stays empty throughout.
"""

from __future__ import annotations

from typing import Any

import pytest

from .nb_jobs_support import bank_db, clear_calls, llm_calls, make_project, nbx_api, nbx_engine  # noqa: F401
from .notebook_support import add, result_of, run_and_wait

WIRES_SQL = ("SELECT wire_id, amount, status FROM bank.wires WHERE status = 'stuck' "
             "AND created_at < now() - make_interval(days => %(days)s) ORDER BY wire_id")


@pytest.fixture(scope="module")
def proj(nbx_api, bank_db) -> dict[str, Any]:  # noqa: F811
    return make_project(nbx_api, bank_db)


def _nb(api, proj, title: str = "Stuck wires") -> dict[str, Any]:
    from .conftest import uniq

    r = api.post(f"/projects/{proj['id']}/notebooks", {"title": uniq(title)})
    assert r.status_code == 201, r.text
    return r.json()


def _wires_notebook(api, proj) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    """c1 data (threshold) → c2 query (stuck wires older than `days`) → c3 rule (amount over the threshold) + a note."""
    nb = _nb(api, proj)
    cells = {
        "c1": add(api, nb, "data", {"columns": ["min_amount"], "rows": [{"min_amount": 50}]}, title="Threshold"),
        "c2": add(api, nb, "query", {"connection": "bank", "sql": WIRES_SQL, "params": {"days": 1}}, title="Stuck wires"),
        "c3": add(api, nb, "rule", {"op": "filter", "left": "{{cells.c2.rows}}",
                                    "where": [{"field": "amount", "op": "gt", "value": "{{cells.c1.rows[0].min_amount}}"}]},
                  title="Over the threshold"),
        "note": add(api, nb, "note", {"markdown": "Wires stuck past the settlement window."}),
    }
    return nb, cells


def _items(api, execution_id: str, node: str) -> list[dict[str, Any]]:
    r = api.get(f"/executions/{execution_id}")
    assert r.status_code == 200, r.text
    return [it.get("json") for it in (r.json()["node_data"].get(node) or [])]


def _run_kept(api, wid: str, input_data: dict[str, Any] | None = None) -> dict[str, Any]:
    r = api.post(f"/workflows/{wid}/run", {"input_data": input_data or {}})
    assert r.status_code == 200, r.text
    return r.json()


def _preview(api, nb, body: dict[str, Any]) -> dict[str, Any]:
    r = api.post(f"/notebooks/{nb['id']}/keep/preview", body)
    assert r.status_code == 200, r.text
    return r.json()


def _item(p: dict[str, Any], key: str) -> dict[str, Any]:
    for it in p["items"]:
        if it["key"] == key:
            return it
    raise AssertionError(f"no item {key}: {[i['key'] for i in p['items']]}")


# ── the core promise: the kept workflow reproduces the notebook ──────────────


def test_kept_workflow_reproduces_the_notebook_and_takes_an_input(nbx_api, proj):
    api = nbx_api
    clear_calls(api)
    nb, c = _wires_notebook(api, proj)
    run = run_and_wait(api, nb)
    assert run["status"] == "done", run
    nb_c2, nb_c3 = result_of(run, c["c2"]), result_of(run, c["c3"])
    assert nb_c2["output"]["row_count"] == 1 and nb_c3["output"]["row_count"] == 1  # W1 only (3 days, 100.50)

    body = {"target": "workflow", "params": [{"ref": "c2.spec.params.days", "mode": "input", "name": "days"}]}
    p = _preview(api, nb, body)
    assert p["can_save"] is True, p["items"]
    assert "input `days`" in _item(p, "params:c2.spec.params.days")["message"]
    names = [n["name"] for n in p["definition"]["nodes"]]
    assert names == ["Start", "c1", "c2_in", "c2_run", "c2", "c3"], names
    assert p["definition"]["description"] == "Wires stuck past the settlement window."
    start = p["definition"]["nodes"][0]
    assert start["parameters"]["input_schema"]["properties"]["days"]["default"] == 1

    r = api.post(f"/notebooks/{nb['id']}/keep", body)
    assert r.status_code == 201, r.text
    kept = r.json()
    assert kept["target"] == "workflow" and kept["schedule_active"] is False
    wid = kept["workflow_id"]
    wf = api.get(f"/workflows/{wid}").json()
    assert [n["name"] for n in wf["definition"]["nodes"]] == names  # saved exactly what the preview showed

    ex = _run_kept(api, wid)
    assert ex["status"] == "success", ex
    c2 = _items(api, ex["execution_id"], "c2")[0]
    c3 = _items(api, ex["execution_id"], "c3")[0]
    assert c2["row_count"] == nb_c2["output"]["row_count"]
    assert [r["wire_id"] for r in c2["rows"]] == [r["wire_id"] for r in nb_c2["output"]["rows"]] == ["W1"]
    assert c3["count"] == c3["row_count"] == nb_c3["output"]["row_count"] == 1
    assert [r["wire_id"] for r in c3["rows"]] == ["W1"]

    # the input changes the result the way the notebook would: days=0 also catches W2 (2 hours old, 2500)
    ex0 = _run_kept(api, wid, {"days": 0})
    assert ex0["status"] == "success", ex0
    assert [r["wire_id"] for r in _items(api, ex0["execution_id"], "c2")[0]["rows"]] == ["W1", "W2"]
    assert _items(api, ex0["execution_id"], "c3")[0]["count"] == 2

    nbj = api.get(f"/notebooks/{nb['id']}").json()
    assert nbj["kept"][-1]["workflow_id"] == wid and nbj["kept"][-1]["target"] == "workflow"
    assert llm_calls(api) == []  # Keep never calls a model


def test_empty_result_stops_quietly_or_continues(nbx_api, proj):
    api = nbx_api
    nb = _nb(api, proj, "Nothing stuck")
    q = add(api, nb, "query", {"connection": "bank", "sql": WIRES_SQL, "params": {"days": 30}})
    add(api, nb, "rule", {"op": "count", "left": f"{{{{cells.{q['name']}.rows}}}}"})
    quiet = api.post(f"/notebooks/{nb['id']}/keep", {"target": "workflow"}).json()
    ex = _run_kept(api, quiet["workflow_id"])
    assert ex["status"] == "success"
    assert _items(api, ex["execution_id"], "c1_run")[0]["rowCount"] == 0
    assert _items(api, ex["execution_id"], "c2") == []  # stopped quietly after the empty query
    cont = api.post(f"/notebooks/{nb['id']}/keep", {"target": "workflow", "empty_policy": "continue"}).json()
    ex = _run_kept(api, cont["workflow_id"])
    assert _items(api, ex["execution_id"], "c2")[0]["count"] == 0


def test_guarded_change_waits_for_approval(nbx_api, proj):
    api = nbx_api
    nb = _nb(api, proj, "Release")
    add(api, nb, "query", {"connection": "bank", "sql": WIRES_SQL, "params": {"days": 1}})
    add(api, nb, "change", {"connection": "bank_rw", "table": "bank.wires", "key": {"wire_id": "{{cells.c1.rows[0].wire_id}}"},
                            "set": {"status": "released"}, "reason": "past the window"},
        guard={"left": "{{cells.c1.count}}", "op": "gt", "right": 0})
    p = _preview(api, nb, {"target": "workflow"})
    names = [n["name"] for n in p["definition"]["nodes"]]
    assert names == ["Start", "c1_in", "c1_run", "c1", "c2_in", "c2_if", "c2_ok", "c2_run", "c2"], names
    assert "waits for an approval" in _item(p, f"writes:{nb_cell(api, nb, 'c2')['id']}")["message"]
    run = {n["name"]: n for n in p["definition"]["nodes"]}["c2_run"]
    assert run["parameters"]["query"] == 'UPDATE "bank"."wires" SET "status" = %(s0)s WHERE "wire_id" = %(k0)s RETURNING *'
    wid = api.post(f"/notebooks/{nb['id']}/keep", {"target": "workflow"}).json()["workflow_id"]
    ex = _run_kept(api, wid)
    assert ex["status"] == "waiting", ex  # the Approval holds the write; nothing was changed
    # "auto" drops the approval step; a read-only connection is refused with the reason
    nb2 = _nb(api, proj, "Release auto")
    cell = add(api, nb2, "change", {"connection": "bank_rw", "table": "bank.wires", "key": {"wire_id": "W1"},
                                    "set": {"status": "released"}})
    p2 = _preview(api, nb2, {"target": "workflow", "writes": [{"cell_id": cell["id"], "mode": "auto"}]})
    assert p2["can_save"] and not any(n["type"] == "Approval" for n in p2["definition"]["nodes"])
    nb3 = _nb(api, proj, "Release ro")
    r = api.post(f"/notebooks/{nb3['id']}/cells", {"kind": "change", "spec": {
        "connection": "bank", "table": "bank.wires", "key": {"wire_id": "W1"}, "set": {"status": "released"}}})
    assert r.status_code in (409, 422) and r.json()["detail"]["code"] == "connection_readonly", r.text


def nb_cell(api, nb, name: str) -> dict[str, Any]:
    for c in api.get(f"/notebooks/{nb['id']}").json()["cells"]:
        if c["name"] == name:
            return c
    raise KeyError(name)


# ── credentials ──────────────────────────────────────────────────────────────


def test_unbound_credential_blocks_scheduling_until_bound(nbx_api, proj):
    from .nb_jobs_support import add_secret

    api = nbx_api
    nb = _nb(api, proj, "Health")
    h = add(api, nb, "http", {"method": "GET", "url": "http://127.0.0.1:9/health", "credential_slot": "health_api"})
    p = _preview(api, nb, {"target": "scheduled_check"})
    it = _item(p, f"slots:{h['id']}")
    assert it["status"] == "needs_choice" and p["can_save"] is True and p["can_schedule"] is False
    r = api.post(f"/notebooks/{nb['id']}/keep", {"target": "scheduled_check"})
    assert r.status_code == 409, r.text
    assert r.json()["detail"]["code"] == "unbound_credential"
    assert [i["key"] for i in r.json()["detail"]["items"]] == [f"slots:{h['id']}"]
    # a plain workflow may be saved unbound (it isn't scheduled)
    assert api.post(f"/notebooks/{nb['id']}/keep", {"target": "workflow"}).status_code == 201
    # a scheduled check with the Manual trigger is a draft: nothing is scheduled, so an unbound slot doesn't block it
    manual = {"target": "scheduled_check", "trigger": {"type": "manual"}}
    p = _preview(api, nb, manual)
    assert p["definition"]["nodes"][0]["type"] == "Start" and "draft" in _item(p, "trigger")["message"]
    r = api.post(f"/notebooks/{nb['id']}/keep", manual)
    assert r.status_code == 201, r.text
    assert r.json()["schedule_active"] is False
    wf = api.get(f"/workflows/{r.json()['workflow_id']}").json()
    assert wf["active"] is False and not any(n["type"] in ("Cron", "Webhook") for n in wf["definition"]["nodes"])

    from .conftest import uniq

    key = uniq("HEALTH_TOKEN").upper().replace("-", "_")
    add_secret(api, proj, key, "s3cret")
    body = {"target": "scheduled_check", "slots": [{"cell_id": h["id"], "credential_id": key}]}
    p = _preview(api, nb, body)
    assert _item(p, f"slots:{h['id']}")["status"] == "ok" and p["can_schedule"] is True
    run = {n["name"]: n for n in p["definition"]["nodes"]}["c1_run"]
    assert {"name": "Authorization", "value": "Bearer {{ $vars.%s }}" % key} in run["parameters"]["headers"]
    assert "s3cret" not in str(p["definition"])  # the secret value never enters the definition
    r = api.post(f"/notebooks/{nb['id']}/keep", body)
    assert r.status_code == 201, r.text
    assert r.json()["schedule_active"] is True
    wf = api.get(f"/workflows/{r.json()['workflow_id']}").json()
    assert wf["active"] is True and wf["definition"]["nodes"][0]["type"] == "Cron"
    assert wf["definition"]["nodes"][0]["parameters"]["cronExpression"] == "45 6 * * 1-5"


# ── triggers ─────────────────────────────────────────────────────────────────


def test_triggers_business_day_mailbox_after_deploy(nbx_api, proj):
    api = nbx_api
    nb, _ = _wires_notebook(api, proj)
    run_and_wait(api, nb)

    p = _preview(api, nb, {"target": "scheduled_check", "trigger": {"type": "business_day", "cron": "10 7 * * *"}})
    t = _item(p, "trigger")
    assert t["status"] == "ok" and "business day at 07:10" in t["message"] and "Holidays are not skipped" in t["message"]
    assert p["definition"]["nodes"][0]["parameters"]["cronExpression"] == "10 7 * * 1-5"

    p = _preview(api, nb, {"target": "workflow", "trigger": {"type": "mailbox"}})
    t = _item(p, "trigger")
    assert t["status"] == "needs_choice" and "coming" in t["message"]
    assert p["definition"]["nodes"][0]["type"] == "Start"
    r = api.post(f"/notebooks/{nb['id']}/keep", {"target": "workflow", "trigger": {"type": "mailbox"}})
    assert r.status_code == 201 and r.json()["schedule_active"] is False

    body = {"target": "scheduled_check", "trigger": {"type": "after_deploy"}}
    p = _preview(api, nb, body)
    t = _item(p, "trigger")
    hook = p["definition"]["nodes"][0]
    assert hook["type"] == "Webhook" and hook["name"] == "Deploy"
    assert f"/webhook/p/{hook['parameters']['path']}" in t["message"]
    r = api.post(f"/notebooks/{nb['id']}/keep", body)
    assert r.status_code == 201, r.text
    kept = r.json()
    assert kept["webhook_url"] == f"/webhook/p/{hook['parameters']['path']}" and kept["schedule_active"] is True
    # the deploy pipeline calls it; the JSON body's fields would be inputs
    hit = api.raw("POST", kept["webhook_url"], json={"deploy": "v42"})
    assert hit.status_code in (200, 202), hit.text
    import time

    deadline = time.time() + 30
    checks: dict[str, Any] = {}
    while time.time() < deadline:
        checks = api.get(f"/notebooks/{nb['id']}/checks").json()
        if checks["runs"] and checks["runs"][0]["status"] in ("success", "failed"):
            break
        time.sleep(0.3)
    assert checks["workflow_id"] == kept["workflow_id"]
    assert checks["runs"] and checks["runs"][0]["status"] == "success", checks
    assert checks["pass"] >= 1 and checks["fail"] == 0
    ex = checks["runs"][0]["execution_id"]
    assert _items(api, ex, "c3")[0]["count"] == 1


# ── test suite / dataset / report ────────────────────────────────────────────


def test_test_suite_replays_the_run(nbx_api, proj):
    api = nbx_api
    nb, _ = _wires_notebook(api, proj)
    blocked = _preview(api, nb, {"target": "test_suite"})
    assert blocked["can_save"] is False and _item(blocked, "excluded:run")["status"] == "blocked"
    run_and_wait(api, nb)
    p = _preview(api, nb, {"target": "test_suite"})
    assert p["can_save"] is True, p["items"]
    tests = p["definition"]["tests"]
    assert len(tests) == 1 and tests[0]["mocks"]["c2_run"]["items"][0]["rowCount"] == 1
    assert {"node": "c3", "path": "count", "op": "eq", "value": 1} in tests[0]["expect"]
    r = api.post(f"/notebooks/{nb['id']}/keep", {"target": "test_suite"})
    assert r.status_code == 201, r.text
    assert r.json()["spec_url"] is None  # no browser cells
    res = api.post(f"/workflows/{r.json()['workflow_id']}/tests/run", {})
    assert res.status_code == 200, res.text
    out = res.json()["summary"]
    assert out["total"] == out["passed"] == 1 and out["failed"] == out["errored"] == 0, res.json()["results"]


def test_dataset_target_creates_the_table_and_fills_it(nbx_api, proj):
    from .conftest import uniq

    api = nbx_api
    nb, _ = _wires_notebook(api, proj)
    table = uniq("stuck").replace("-", "_")
    body = {"target": "dataset", "dataset_table": table, "dataset_cell_id": nb_cell(api, nb, "c3")["id"]}
    p = _preview(api, nb, body)
    assert p["can_save"] is False and "Run the notebook first" in _item(p, "writes:dataset")["message"]
    run_and_wait(api, nb)
    p = _preview(api, nb, body)
    assert p["can_save"] is True, p["items"]
    msg = _item(p, "writes:dataset")["message"]
    assert f"`{table}`" in msg and "created on save" in msg and "wire_id string" in msg
    assert [n["name"] for n in p["definition"]["nodes"]][-2:] == ["dataset_rows", "dataset"]
    r = api.post(f"/notebooks/{nb['id']}/keep", body)
    assert r.status_code == 201, r.text
    assert r.json()["table"] == table and r.json()["schedule_active"] is False  # manual trigger: not published
    t = next(x for x in api.get(f"/projects/{proj['id']}/tables").json() if x["name"] == table)
    assert [c["name"] for c in t["columns"]] == ["wire_id", "amount", "status"]
    ex = _run_kept(api, r.json()["workflow_id"])
    assert ex["status"] == "success", ex
    rows = api.get(f"/projects/{proj['id']}/tables/{t['id']}/rows").json()
    assert [x["data"] for x in rows["rows"]] == [{"wire_id": "W1", "amount": 100.5, "status": "stuck"}]
    # a second keep into the same (now existing) table reuses its columns
    p2 = _preview(api, nb, body)
    assert "created on save" not in _item(p2, "writes:dataset")["message"]


def test_report_target_renders_markdown_on_a_schedule(nbx_api, proj):
    api = nbx_api
    nb, _ = _wires_notebook(api, proj)
    run_and_wait(api, nb)
    nb_md = api.get(f"/notebooks/{nb['id']}/report").json()["markdown"]
    assert "## c3 · Over the threshold" in nb_md and "| W1 |" in nb_md
    p = _preview(api, nb, {"target": "report"})
    assert p["can_save"] and p["definition"]["nodes"][0]["type"] == "Cron"
    assert p["definition"]["nodes"][-1]["name"] == "report"
    r = api.post(f"/notebooks/{nb['id']}/keep", {"target": "report"})
    assert r.status_code == 201 and r.json()["schedule_active"] is True
    ex = _run_kept(api, r.json()["workflow_id"])
    assert ex["status"] == "success", ex
    md = _items(api, ex["execution_id"], "report")[0]["markdown"]
    for line in ("## c1 · Threshold", "## c2 · Stuck wires", "## c3 · Over the threshold", "| W1 |",
                 "Wires stuck past the settlement window."):
        assert line in md, md
    assert "3 of 3 cells have a result." in md


# ── wait / templates ───────────────────────────────────────────────────────


def test_daily_data_quality_template_maps_wait(nbx_api, proj):
    from .conftest import uniq

    api = nbx_api
    tpl = [t for t in api.get("/notebook-templates").json() if t["slug"] == "daily-data-quality"]
    assert tpl and [c["kind"] for c in tpl[0]["cells"]] == ["query", "for_each", "http", "wait"]
    r = api.post(f"/projects/{proj['id']}/notebooks", {"title": uniq("dq"), "template": "daily-data-quality",
                                                       "connection": "bank"})
    assert r.status_code == 201, r.text
    nb = r.json()
    assert [c["name"] for c in nb["cells"]] == ["c1", "c2", "c4", "c5"]
    p = _preview(api, nb, {"target": "scheduled_check"})
    nodes = {n["name"]: n for n in p["definition"]["nodes"]}
    assert nodes["c5_run"]["type"] == "Wait" and nodes["c5_run"]["parameters"] == {"unit": "minutes", "duration": 2}
    assert {"c4_if", "c2_has", "c3_run"} <= set(nodes)
    assert not any(n["type"] == "Approval" for n in nodes.values())
    # an over-long wait is refused with the reason (the engine's Wait is capped at 5 minutes)
    nb2 = _nb(api, proj, "long wait")
    add(api, nb2, "data", {"columns": ["x"], "rows": [{"x": 1}]})
    w = add(api, nb2, "wait", {"mode": "duration", "duration": "2h"})
    p2 = _preview(api, nb2, {"target": "workflow"})
    assert p2["can_save"] is False and "at most 5 minutes" in _item(p2, f"excluded:{w['id']}:wait")["message"]


def test_browser_and_ask_cells_are_excluded_with_reasons(nbx_api, proj):
    api = nbx_api
    nb = _nb(api, proj, "mixed")
    add(api, nb, "data", {"columns": ["x"], "rows": [{"x": 1}]})
    b = add(api, nb, "browser", {"base_url": "http://127.0.0.1:9", "steps": [{"action": "goto", "url": "/"}]})
    p = _preview(api, nb, {"target": "workflow"})
    assert "Export as test suite" in _item(p, f"excluded:{b['id']}")["message"] and p["can_save"]
    p = _preview(api, nb, {"target": "test_suite"})
    assert "Exported to the .spec.ts" in _item(p, f"excluded:{b['id']}")["message"]
    # references to an unkept cell block the keep
    nb2 = _nb(api, proj, "refs")
    d = add(api, nb2, "data", {"columns": ["x"], "rows": [{"x": 1}]})
    rl = add(api, nb2, "rule", {"op": "count", "left": "{{cells.c1.rows}}"})
    p = _preview(api, nb2, {"target": "workflow", "cell_ids": [rl["id"]]})
    assert p["can_save"] is False and "c1" in _item(p, f"excluded:{rl['id']}:refs")["message"]
    assert d  # c1 exists but was not chosen
    r = api.post(f"/notebooks/{nb2['id']}/keep", {"target": "workflow", "cell_ids": [rl["id"]]})
    assert r.status_code == 422 and r.json()["detail"]["code"] == "invalid"

"""Stage 3B live smoke tests over HTTP on a real engine + Postgres (CONTRACTS-STAGE3B section 5), written from the contract.

Refund triage calls the fake risk API (tests/fake_refund_api.py, POST /_shape v1|v2). A smoke run executes chosen tests with
only ``live_nodes`` calling the real system; every other outside step keeps its mock (strict offline otherwise). Structural
assertions fail a smoke run, value assertions are reported as info. Also: config defaults and validation, 409 rules, run
history order, the scheduler tick, and the ``smoke_tests_pass`` release check.
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta

import pytest

from .drift_smoke_support import FakeApi, big_case, make_refund_workflow, tid

pytestmark = pytest.mark.integration

V1_ONLY = {"node": "Risk API", "path": "body.decision", "op": "exists"}      # true only in v1
V2_ONLY = {"node": "Risk API", "path": "body.score", "op": "exists"}         # true only in v2 (and only if the node is really live)


@pytest.fixture(scope="module")
def fake():
    f = FakeApi()
    try:
        yield f
    finally:
        f.stop()


@pytest.fixture(autouse=True)
def _v1(fake):
    fake.shape("v1")
    yield
    fake.shape("v1")


def cfg(api, wid: str, **body):
    base = {"enabled": True, "test_ids": [], "live_nodes": ["Risk API"], "schedule": None, "before_release": False}
    return api.put(f"/workflows/{wid}/smoke", {**base, **body})


def smoke_run(api, wid: str):
    return api.post(f"/workflows/{wid}/smoke/run", {})


# -- config ----------------------------------------------------------------------------------------------------------


def test_defaults_and_roundtrip(api, fake):
    t = big_case("big refund", id="t_b0000001", expect=[V1_ONLY])
    _, wid = make_refund_workflow(api, fake.url, tests=[t])
    r = api.get(f"/workflows/{wid}/smoke")
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["enabled"] is False and d["test_ids"] == [] and d["live_nodes"] == []
    assert d["schedule"] is None and d["before_release"] is False
    assert api.get("/workflows/wf_missing_zz/smoke").status_code == 404
    assert api.get("/workflows/wf_missing_zz/smoke/runs").status_code == 404
    assert api.post("/workflows/wf_missing_zz/smoke/run", {}).status_code == 404

    r = cfg(api, wid, test_ids=["t_b0000001"], schedule="*/15 * * * *", before_release=True)
    assert r.status_code == 200, r.text
    saved = r.json()
    assert saved["enabled"] is True and saved["test_ids"] == ["t_b0000001"] and saved["live_nodes"] == ["Risk API"]
    assert saved["schedule"] == "*/15 * * * *" and saved["before_release"] is True and saved["next_run_at"]
    assert api.get(f"/workflows/{wid}/smoke").json()["test_ids"] == ["t_b0000001"]
    assert api.get(f"/workflows/{wid}/smoke/runs").json() == []


def test_put_validation_is_422(api, fake):
    t = big_case("big refund", id="t_b0000002", expect=[V1_ONLY])
    _, wid = make_refund_workflow(api, fake.url, tests=[t])
    assert cfg(api, wid, test_ids=["t_nope0000"]).status_code == 422                              # unknown test id
    assert cfg(api, wid, test_ids=["t_b0000002"], live_nodes=["Ghost API"]).status_code == 422     # unknown node
    for name in ("Normalize", "Amount over 500", "Webhook"):                                       # not an outside step
        assert cfg(api, wid, test_ids=["t_b0000002"], live_nodes=[name]).status_code == 422, name
    for name in ("Log escalated", "Log approved"):                                                 # database nodes can't go live
        assert cfg(api, wid, test_ids=["t_b0000002"], live_nodes=[name]).status_code == 422, name
    assert cfg(api, wid, test_ids=["t_b0000002"], live_nodes=["Risk API", "Log escalated"]).status_code == 422
    assert cfg(api, wid, test_ids=["t_b0000002"], schedule="not a cron").status_code == 422
    for too_often in ("* * * * *", "*/2 * * * *", "*/4 * * * *", "0/1 * * * *", "* * * * * *"):
        assert cfg(api, wid, test_ids=["t_b0000002"], schedule=too_often).status_code == 422, too_often
    assert cfg(api, wid, test_ids=["t_b0000002"], schedule="*/5 * * * *").status_code == 200       # exactly the minimum is fine
    assert cfg(api, wid, test_ids=["t_b0000002"], schedule="0 6 * * *").status_code == 200
    # nothing invalid was stored by the rejected calls
    assert api.get(f"/workflows/{wid}/smoke").json()["schedule"] == "0 6 * * *"


def test_run_is_409_without_tests_or_without_live_nodes(api, fake):
    t = big_case("big refund", id="t_b0000003", expect=[V1_ONLY])
    _, wid = make_refund_workflow(api, fake.url, tests=[t])
    assert smoke_run(api, wid).status_code == 409                          # never configured
    assert cfg(api, wid, test_ids=[], live_nodes=["Risk API"]).status_code == 200
    assert smoke_run(api, wid).status_code == 409                          # no test_ids
    assert cfg(api, wid, test_ids=["t_b0000003"], live_nodes=[]).status_code == 200
    assert smoke_run(api, wid).status_code == 409                          # no live_nodes
    assert api.get(f"/workflows/{wid}/smoke/runs").json() == []            # a 409 leaves no run behind


# -- running ---------------------------------------------------------------------------------------------------------


def test_live_node_hits_the_fake_api_while_other_steps_stay_mocked(api, fake):
    # The default mock describes v1. Only a real call can see body.score (v2), and Log escalated has to stay mocked.
    t = big_case("v2 only fields", id="t_b0000004", expect=[
        V2_ONLY, {"node": "Risk API", "path": "statusCode", "op": "eq", "value": 200},
        {"node": "Log escalated", "op": "status", "value": "success"}])
    _, wid = make_refund_workflow(api, fake.url, tests=[t])
    assert cfg(api, wid, test_ids=["t_b0000004"]).status_code == 200

    fake.shape("v2")
    r = smoke_run(api, wid)
    assert r.status_code == 200, r.text
    run = r.json()
    assert run["id"].startswith("smoke_") and run["trigger"] == "manual" and run["workflow_id"] == wid
    assert run["status"] == "passed", run
    assert run["started_at"] and run["finished_at"]
    res = run["results"][0]
    assert res["test_id"] == "t_b0000004" and res["name"] == "v2 only fields" and res["status"] == "passed"
    calls = res["live_calls"]
    assert [c["node"] for c in calls] == ["Risk API"] and calls[0]["status_code"] == 200 and calls[0]["ms"] >= 0

    # same test, v1 server: the live reply has no score -> the structural assertion fails (proves the reply was live, not the mock)
    fake.shape("v1")
    run2 = smoke_run(api, wid).json()
    assert run2["status"] == "failed" and run2["results"][0]["status"] == "failed"
    assert any(f["kind"] == "assertion" and f["node"] == "Risk API" for f in run2["results"][0]["failures"])


def test_an_unmocked_non_live_outside_step_still_fails_strict_offline(api, fake):
    # no mock for Log escalated: it is not live, so it can neither be mocked nor reach the table
    t = big_case("big refund", id="t_b0000005", expect=[V1_ONLY])
    _, wid = make_refund_workflow(api, fake.url, tests=[t])
    wf = api.get(f"/workflows/{wid}").json()["definition"]
    defaults = {"mocks": {k: v for k, v in wf["test_defaults"]["mocks"].items() if k != "Log escalated"}}
    assert api.put(f"/workflows/{wid}", {"nodes": wf["nodes"], "connections": wf["connections"], "test_defaults": defaults}).status_code == 200
    assert cfg(api, wid, test_ids=["t_b0000005"]).status_code == 200
    run = smoke_run(api, wid).json()
    assert run["status"] in ("error", "failed"), run
    fails = run["results"][0]["failures"]
    assert any(f["kind"] == "unmocked" and f["node"] == "Log escalated" for f in fails), fails
    assert not any(f.get("node") == "Risk API" and f["kind"] == "unmocked" for f in fails)   # the live node is not "unmocked"


def test_value_ops_are_info_and_never_fail(api, fake):
    t = big_case("values moved", id="t_b0000006", expect=[
        V1_ONLY,                                                                              # structural, true on v1
        {"node": "Risk API", "path": "body.decision", "op": "eq", "value": "approve"},        # live says "review"
        {"node": "Risk API", "path": "body.decision", "op": "neq", "value": "review"},
        {"node": "Risk API", "path": "statusCode", "op": "gt", "value": 999},
        {"node": "Risk API", "path": "statusCode", "op": "lt", "value": 1}])
    _, wid = make_refund_workflow(api, fake.url, tests=[t])
    assert cfg(api, wid, test_ids=["t_b0000006"]).status_code == 200
    run = smoke_run(api, wid).json()
    assert run["status"] == "passed", run
    res = run["results"][0]
    assert res["status"] == "passed" and res["failures"] == []
    info = res.get("info") or res.get("warnings") or []
    assert len(info) == 4 and all(i["kind"] == "info" for i in info), res
    assert {i["node"] for i in info} == {"Risk API"}


@pytest.mark.parametrize("name,assertion", [
    ("status", {"node": "Risk API", "op": "status", "value": "error"}),
    ("exists", {"node": "Risk API", "path": "body.nope", "op": "exists"}),
    ("not_exists", {"node": "Risk API", "path": "body.decision", "op": "not_exists"}),
    ("type", {"node": "Risk API", "path": "body.ok", "op": "type", "value": "string"}),
    ("count", {"node": "Risk API", "op": "count", "value": 7}),
    ("shape", {"node": "Risk API", "path": "body", "op": "shape",
               "value": {"type": "object", "required": ["score"], "properties": {"score": {"type": "number"}}}}),
])
def test_structural_ops_fail_a_smoke_run(api, fake, name, assertion):
    t = big_case(f"structural {name}", id=tid(), expect=[assertion])
    _, wid = make_refund_workflow(api, fake.url, tests=[t])
    assert cfg(api, wid, test_ids=[t["id"]]).status_code == 200
    run = smoke_run(api, wid).json()
    assert run["status"] == "failed", (name, run)
    assert run["results"][0]["status"] == "failed" and run["results"][0]["failures"]


def test_unreachable_live_system_is_not_a_pass(api, fake):
    t = big_case("big refund", id="t_b0000007", expect=[{"node": "Risk API", "op": "status", "value": "success"}])
    _, wid = make_refund_workflow(api, fake.url, tests=[t])
    d = api.get(f"/workflows/{wid}").json()["definition"]
    next(n for n in d["nodes"] if n["name"] == "Risk API")["parameters"]["url"] = f"{fake.url}/drop"   # closes the connection
    assert api.put(f"/workflows/{wid}", {"nodes": d["nodes"], "connections": d["connections"]}).status_code == 200
    assert cfg(api, wid, test_ids=["t_b0000007"]).status_code == 200
    run = smoke_run(api, wid).json()
    assert run["status"] in ("failed", "error"), run


def test_runs_are_listed_newest_first_with_limit(api, fake):
    t = big_case("big refund", id="t_b0000008", expect=[V1_ONLY])
    _, wid = make_refund_workflow(api, fake.url, tests=[t])
    assert cfg(api, wid, test_ids=["t_b0000008"]).status_code == 200
    ids = []
    for _ in range(3):
        r = smoke_run(api, wid)
        assert r.status_code == 200, r.text
        ids.append(r.json()["id"])
        time.sleep(0.05)
    listed = api.get(f"/workflows/{wid}/smoke/runs").json()
    assert [x["id"] for x in listed] == ids[::-1]
    assert [x["started_at"] for x in listed] == sorted((x["started_at"] for x in listed), reverse=True)
    two = api.get(f"/workflows/{wid}/smoke/runs?limit=2").json()
    assert [x["id"] for x in two] == ids[::-1][:2]
    assert api.get(f"/workflows/{wid}/smoke/runs?limit=0").status_code == 422


def test_a_smoke_run_never_changes_the_stored_tests_or_writes_table_rows(api, fake):
    import json

    t = big_case("big refund", id="t_b0000009", expect=[V1_ONLY])
    pid, wid = make_refund_workflow(api, fake.url, tests=[t])
    before = json.dumps(api.get(f"/workflows/{wid}").json()["definition"]["tests"], sort_keys=True)
    assert cfg(api, wid, test_ids=["t_b0000009"]).status_code == 200
    assert smoke_run(api, wid).json()["status"] == "passed"
    assert json.dumps(api.get(f"/workflows/{wid}").json()["definition"]["tests"], sort_keys=True) == before


# -- scheduler -------------------------------------------------------------------------------------------------------


def test_scheduler_tick_runs_a_due_config_and_advances_next_run_at(api, fake, test_db):
    """The engine's scheduler ticks every 30 s in its own process; make the config due and wait for it (<= ~75 s)."""
    t = big_case("big refund", id="t_b0000010", expect=[V1_ONLY])
    _, wid = make_refund_workflow(api, fake.url, tests=[t])
    assert cfg(api, wid, test_ids=["t_b0000010"], schedule="*/5 * * * *").status_code == 200
    past = datetime.now() - timedelta(minutes=10)
    test_db.query("UPDATE smoke_configs SET next_run_at = %s WHERE workflow_id = %s", (past, wid))
    # a config that is disabled must not run even when overdue
    _, off = make_refund_workflow(api, fake.url, tests=[big_case("x", id="t_b0000011", expect=[V1_ONLY])])
    assert cfg(api, off, test_ids=["t_b0000011"], schedule="*/5 * * * *").status_code == 200
    assert cfg(api, off, enabled=False, test_ids=["t_b0000011"], schedule="*/5 * * * *").status_code == 200
    test_db.query("UPDATE smoke_configs SET next_run_at = %s WHERE workflow_id = %s", (past, off))

    deadline = time.time() + 80
    runs = []
    while time.time() < deadline:
        runs = api.get(f"/workflows/{wid}/smoke/runs").json()
        if runs:
            break
        time.sleep(2)
    assert runs, "the scheduler never ran the due smoke config"
    assert runs[0]["trigger"] == "schedule" and runs[0]["status"] == "passed", runs[0]
    nxt = datetime.fromisoformat(api.get(f"/workflows/{wid}/smoke").json()["next_run_at"]).replace(tzinfo=None)
    assert nxt > datetime.now() - timedelta(seconds=5) and nxt > past + timedelta(minutes=10)   # moved on into the future
    time.sleep(1.5)
    assert len(api.get(f"/workflows/{wid}/smoke/runs").json()) == 1          # not due again for minutes
    assert api.get(f"/workflows/{off}/smoke/runs").json() == []


# -- release ---------------------------------------------------------------------------------------------------------


def _release_project(api, fake, *, before_release: bool, enabled: bool = True):
    t = big_case("decision present", id="t_b0000020", expect=[V1_ONLY])
    pid, wid = make_refund_workflow(api, fake.url, tests=[t])
    assert api.post(f"/projects/{pid}/routes", {"method": "GET", "path": "/refund", "handler": {"type": "workflow", "workflow_id": wid}}).status_code == 201
    assert cfg(api, wid, test_ids=["t_b0000020"], enabled=enabled, before_release=before_release).status_code == 200
    return pid, wid


def _checks(api, pid: str) -> list:
    r = api.post(f"/projects/{pid}/releases?dry_run=true", {"environment": "dev"})
    assert r.status_code == 200, r.text
    return [c for c in r.json()["checks"] if c["id"] == "smoke_tests_pass"]


def test_release_check_smoke_tests_pass_blocks_on_failure_and_passes_when_fixed(api, fake):
    pid, wid = _release_project(api, fake, before_release=True)

    fake.shape("v1")
    c = _checks(api, pid)
    assert len(c) == 1 and c[0]["status"] == "pass" and c[0]["details"]["workflow_id"] == wid, c
    runs = api.get(f"/workflows/{wid}/smoke/runs").json()
    assert runs and runs[0]["trigger"] == "release" and runs[0]["status"] == "passed"

    fake.shape("v2")  # decision moved: the structural assertion now fails live
    c = _checks(api, pid)
    assert len(c) == 1 and c[0]["status"] == "fail", c
    assert api.get(f"/workflows/{wid}/smoke/runs").json()[0]["status"] == "failed"
    real = api.post(f"/projects/{pid}/releases", {"environment": "dev"})
    assert real.status_code == 422 and real.json()["detail"]["code"] == "checks_failed", real.text

    fake.shape("v1")
    c = _checks(api, pid)
    assert c[0]["status"] == "pass"
    ok = api.post(f"/projects/{pid}/releases", {"environment": "dev"})
    assert ok.status_code == 201, ok.text


def test_release_check_is_absent_unless_enabled_and_before_release(api, fake):
    pid, _ = _release_project(api, fake, before_release=False)
    fake.shape("v2")
    assert _checks(api, pid) == []
    pid2, wid2 = _release_project(api, fake, before_release=True, enabled=False)
    assert _checks(api, pid2) == []
    assert api.get(f"/workflows/{wid2}/smoke/runs").json() == []
    assert api.post(f"/projects/{pid}/releases", {"environment": "dev"}).status_code == 201   # nothing blocks it


def test_release_check_fails_when_smoke_is_enabled_but_cannot_run(api, fake):
    """before_release + enabled with a live system that doesn't answer is a blocked release, never a silent pass."""
    pid, wid = _release_project(api, fake, before_release=True)
    d = api.get(f"/workflows/{wid}").json()["definition"]
    next(n for n in d["nodes"] if n["name"] == "Risk API")["parameters"]["url"] = f"{fake.url}/drop"
    assert api.put(f"/workflows/{wid}", {"nodes": d["nodes"], "connections": d["connections"]}).status_code == 200
    c = _checks(api, pid)
    assert len(c) == 1 and c[0]["status"] == "fail", c

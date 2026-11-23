"""Integration-plan §0.3 backend gaps: bulk row insert, run status_code, daily run counts."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from .conftest import uniq
from .journeys._helpers import chain, new_project

pytestmark = pytest.mark.integration


def _table(api, P):
    cols = [{"name": "email", "type": "string", "required": True}, {"name": "n", "type": "integer"}]
    r = api.post(f"/projects/{P}/tables", {"name": "items", "columns": cols})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _count(api, P, T):
    return api.get(f"/projects/{P}/tables/{T}/rows?count=true&limit=1").json()["count"]


def _route(api, P, path, node):
    W = api.post(f"/projects/{P}/workflows", chain(uniq("w"), node)).json()["id"]
    r = api.post(f"/projects/{P}/routes", {"method": "GET", "path": path, "handler": {"type": "workflow", "workflow_id": W}})
    assert r.status_code == 201, r.text


# ── (a) bulk insert ──────────────────────────────────────────────────────────


def test_bulk_insert_1000_ok_1001_rejected_bad_row_inserts_nothing(api):
    P, _ = new_project(api, "bulk")
    T = _table(api, P)
    base = f"/projects/{P}/tables/{T}/rows"
    r = api.post(base, {"rows": [{"email": f"u{i}@x.io", "n": i} for i in range(1000)]})
    assert r.status_code == 201 and r.json()["inserted"] == 1000
    assert _count(api, P, T) == 1000

    r = api.post(base, {"rows": [{"email": f"v{i}@x.io"} for i in range(1001)]})
    assert r.status_code == 422 and r.json()["detail"]["code"] == "too_many_rows"
    assert _count(api, P, T) == 1000

    rows = [{"email": f"w{i}@x.io"} for i in range(300)]
    rows[173] = {"email": "bad@x.io", "n": "not-a-number"}
    rows[250] = {"n": 1}
    r = api.post(base, {"rows": rows})
    d = r.json()["detail"]
    assert r.status_code == 422 and d["code"] == "row_invalid"
    assert {(e["row"], e["column"]) for e in d["errors"]} == {(173, "n"), (250, "email")}
    assert _count(api, P, T) == 1000  # atomic: nothing from the bad batch landed


# ── (b) status_code on run items ─────────────────────────────────────────────


def test_run_items_carry_the_route_response_status_code(api):
    P, S = new_project(api, "sc")
    _route(api, P, "/created", {"name": "Respond", "type": "Respond", "position": {"x": 1, "y": 1},
                                "parameters": {"statusCode": 201, "body": "ok"}})
    _route(api, P, "/boom", {"name": "Stop", "type": "StopAndError", "position": {"x": 1, "y": 1},
                             "parameters": {"errorType": "error", "message": "boom"}})
    ok = api.raw("GET", f"/api/p/{S}/created")
    bad = api.raw("GET", f"/api/p/{S}/boom")
    assert ok.status_code == 201 and bad.status_code == 502
    runs = {r["id"]: r for r in api.get(f"/projects/{P}/runs").json()}
    assert runs[ok.headers["x-execution-id"]]["status_code"] == 201
    failed = runs[bad.headers["x-execution-id"]]
    assert failed["status"] == "failed" and failed["status_code"] == 502
    assert api.get(f"/projects/{P}/runs/{failed['id']}").json()["status_code"] == 502
    # a manual run has no HTTP response
    W = api.post(f"/projects/{P}/workflows", chain(uniq("m"), {"name": "Stop", "type": "StopAndError", "position": {"x": 1, "y": 1},
                                                              "parameters": {"errorType": "error", "message": "x"}})).json()["id"]
    xid = api.post(f"/workflows/{W}/run").json()["execution_id"]
    assert api.get(f"/projects/{P}/runs/{xid}").json()["status_code"] is None


# ── (c) daily aggregate ──────────────────────────────────────────────────────


def test_runs_daily_is_zero_filled_counts_failures_and_beyond_100_runs(api):
    P, S = new_project(api, "daily")
    _route(api, P, "/ok", {"name": "Respond", "type": "Respond", "position": {"x": 1, "y": 1}, "parameters": {"statusCode": 200, "body": "ok"}})
    _route(api, P, "/bad", {"name": "Stop", "type": "StopAndError", "position": {"x": 1, "y": 1},
                            "parameters": {"errorType": "error", "message": "boom"}})
    for _ in range(3):
        api.raw("GET", f"/api/p/{S}/ok")
    api.raw("GET", f"/api/p/{S}/bad")
    d = api.get(f"/projects/{P}/runs/daily?days=30")
    assert d.status_code == 200
    days = d.json()
    assert len(days) == 30 and [x["date"] for x in days] == sorted(x["date"] for x in days)
    assert all(set(x) == {"date", "total", "failed"} for x in days)
    assert days[-1]["date"] == datetime.now().date().isoformat()
    assert (days[-1]["total"], days[-1]["failed"]) == (4, 1)
    assert sum(x["total"] for x in days[:-1]) == 0
    assert len(api.get(f"/projects/{P}/runs/daily?days=7").json()) == 7
    # environment filter: the runs' environment decides
    env = api.get(f"/projects/{P}/runs?limit=1").json()[0]["environment"]
    assert api.get(f"/projects/{P}/runs/daily?days=2&environment={env}").json()[-1]["total"] == 4
    assert api.get(f"/projects/{P}/runs/daily?days=2&environment=nope-env").json()[-1]["total"] == 0
    assert api.get(f"/projects/{P}/runs/daily?days=0").status_code == 422
    assert api.get("/projects/no-such-project/runs/daily").status_code == 404


def test_runs_daily_buckets_backdated_runs_by_day_and_drops_older_than_window(api, test_db):
    P, S = new_project(api, "dailyold")
    _route(api, P, "/ok", {"name": "Respond", "type": "Respond", "position": {"x": 1, "y": 1}, "parameters": {"statusCode": 200, "body": "ok"}})
    _route(api, P, "/bad", {"name": "Stop", "type": "StopAndError", "position": {"x": 1, "y": 1},
                            "parameters": {"errorType": "error", "message": "boom"}})
    ids = [api.raw("GET", f"/api/p/{S}/{p}").headers["x-execution-id"] for p in ("ok", "ok", "bad", "bad", "ok", "ok")]
    for xid, ago in zip(ids, (3, 3, 3, 10, 29, 45)):
        test_db.query("update executions set start_time = start_time - make_interval(days => %s) where id = %s", (ago, xid))
    days = {x["date"]: x for x in api.get(f"/projects/{P}/runs/daily?days=30").json()}
    d = lambda n: (datetime.now().date() - timedelta(days=n)).isoformat()  # noqa: E731
    assert (days[d(3)]["total"], days[d(3)]["failed"]) == (3, 1)
    assert (days[d(10)]["total"], days[d(10)]["failed"]) == (1, 1)
    assert (days[d(29)]["total"], days[d(29)]["failed"]) == (1, 0)
    assert d(45) not in days and sum(x["total"] for x in days.values()) == 5
    # another project's runs never leak in
    Q, _ = new_project(api, "dailyother")
    assert sum(x["total"] for x in api.get(f"/projects/{Q}/runs/daily").json()) == 0


# ── clarify dialogue gaps (backend_templates/gaps.py): no topic is asked twice across modules ──────────────────────

_SPEC = {"entities": [{"name": "runs", "fields": [{"name": "key", "type": "string"}]}],
         "workflows": [{"entity": "runs", "states": ["queued", "done"], "transitions": [{"from": "queued", "to": "done"}]}],
         "roles": [{"name": "lead"}], "external_systems": [{"name": "Jobs", "use": "run tests"}]}


def test_domain_questions_skip_topics_already_answered():
    from src.services.backend_templates import gaps

    prompt = "approve runs, import from jira, keep history, call the jobs api with a password, delete old runs"
    full = {q["topic"] for q in gaps.domain_questions(prompt, _SPEC, ["dev", "live"])}
    assert {"credentials", "environments", "retention", "approvals"} <= full
    left = {q["topic"] for q in gaps.domain_questions(prompt, _SPEC, ["dev", "live"], answered={"credentials", "retention"})}
    assert left == full - {"credentials", "retention"}


def test_stored_answers_map_back_to_their_topic():
    from src.services.backend_templates import gaps

    assert gaps.topic_of("runs:dom_credentials") == "credentials"
    assert gaps.topic_of("runs:dom2_secret_names") == "credentials"
    assert gaps.topic_of("runs:q1") is None and gaps.topic_of("runs:q1", "roles") == "roles"


def test_secret_names_followup_needs_names_not_known():
    from src.services.backend_templates import gaps

    spec = {**_SPEC, "open_questions": [{"id": "dom_credentials", "topic": "credentials", "question": "?"}]}
    ids = lambda ans, **kw: {q["id"] for q in gaps.followup_questions(spec, {"dom_credentials": ans}, 1, **kw)}   # noqa: E731
    assert "dom2_secret_names" in ids("Project secrets referenced by name")
    assert "dom2_secret_names" not in ids("Project secrets: BANK_USER and BANK_PASS")
    assert "dom2_secret_names" not in ids("Project secrets for the bank", known_secrets={"bank"})
    assert "dom2_secret_names" in ids("Project secrets for the bank", known_secrets={"other"})
    # the other follow-ups are unaffected
    spec2 = {**_SPEC, "open_questions": [{"id": "dom_safety", "topic": "safety", "question": "?"}]}
    assert gaps.followup_questions(spec2, {"dom_safety": "Server-side limits"}, 1, known_secrets={"x"})[0]["id"] == "dom2_limits"


def test_plan_routes_ledger_is_the_plans_routes_only():
    from src.services.backend_templates import coverage

    spec = {"routes": [{"key": "POST /runs"}, {"key": "GET /runs/{id}"}, {"key": "bogus"}],
            "acceptance_criteria": ["GET /elsewhere works"]}
    assert coverage.plan_routes(spec) == {"POST /runs": "POST /runs", "GET /runs/{}": "GET /runs/{id}"}
    assert coverage.plan_routes(None) == {}


def test_plan_block_states_contracts_helpers_and_the_import_rule():
    from src.services.backend_templates import prompts

    spec = {"routes": [{"key": "POST /runs", "purpose": "start",
                        "request": {"fields": [{"name": "test_ids", "required": True}]},
                        "response": {"fields": [{"name": "id"}]}, "errors": [{"status": 409, "code": "illegal_transition"}]}],
            "helpers": [{"name": "audit", "signature": "audit(event)"}], "shared_module": "common",
            "nfr": {"errors": {"shape": "{error, message}"}, "pagination": {"shape": "{rows,total}"}}}
    out = prompts.plan_block(spec)
    assert "POST /runs" in out and "test_ids*" in out and "409 illegal_transition" in out
    assert "from common import audit" in out and "never redefine" in out and "{error, message}" in out

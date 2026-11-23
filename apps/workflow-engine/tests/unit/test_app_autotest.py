"""Scenario tests of a one-shot app: the runner (no database), the build that feeds failures to its single fix pass, the skip when
a bundle has no tests, the truncated-reply fallback and the named gaps of a build report. The model is a fake."""

from __future__ import annotations

from typing import Any

import pytest

from src.engine.llm_provider import LLMResponse, ToolCall
from src.services import app_autotest as at
from src.services import build_coordinator as bc
from src.services import oneshot_builder as ob
from tests.unit.conftest import anyio_backend, pg, pg_dbname  # noqa: F401
from tests.unit.test_oneshot_builder import (FakeModel, _generator_db, big_plan, new_project, plan_json,  # noqa: F401
                                             typed_bundle_json)

pytestmark = pytest.mark.anyio

@pytest.fixture(autouse=True)
def _nodes():
    """Routes that touch tables call the broker, which needs the node registry the app fills at startup."""
    from src.engine.node_registry import node_registry, register_all_nodes

    if not node_registry.list():
        register_all_nodes()


ROUTES = {"create": {"method": "POST", "path": "/items"}, "get": {"method": "GET", "path": "/items/{id}"},
          "slow": {"method": "GET", "path": "/slow"}}


class Recorder:
    """A fake ``send``: records every request; answers from ``answers`` by ``METHOD path``."""

    def __init__(self, answers: dict[str, Any]) -> None:
        self.answers, self.sent = answers, []

    async def __call__(self, method, path, query, headers, body):
        self.sent.append({"method": method, "path": path, "query": query, "headers": headers, "body": body})
        a = self.answers[f"{method} {path}"]
        if callable(a):
            return await a()
        return a


def step(as_, op, input=None, expect=None, save=None):  # noqa: A002
    return {"as": as_, "op": op, "input": input or {}, "expect": expect or {}, "save": save or {}}


async def test_a_passing_scenario_chains_saved_values_and_sends_the_preview_identity():
    send = Recorder({"POST /items": (201, {"id": 7, "status": "pending"}), "GET /items/7": (200, {"id": 7, "status": "pending"})})
    tests = [{"name": "create then read", "steps": [
        step("alice", "create", {"title": "x"}, {"status": 201}, {"rid": "id"}),
        step("bob", "get", {"id": "$rid"}, {"fields": {"id": "$rid", "status": "pending"}})]}]
    rep = await at.run_scenarios(tests, ROUTES, send, token="tok")
    assert rep == {"total": 1, "passed": 1, "results": [{"name": "create then read", "passed": True, "reason": "ok"}]}
    first, second = send.sent
    assert first["body"] == {"title": "x"} and second["path"] == "/items/7" and second["query"] == {}
    assert first["headers"]["x-preview-user"] == "alice" and first["headers"]["x-preview-user-roles"] == "staff"
    assert second["headers"]["x-preview-user"] == "bob" and second["headers"]["x-preview-user-roles"] == "manager"
    assert first["headers"]["authorization"] == "Bearer tok"


async def test_uniq_is_stable_within_a_scenario_and_differs_between_runs():
    send = Recorder({"POST /items": (201, {"id": 1})})
    sc = [{"name": "n", "steps": [step("alice", "create", {"title": "Suite $uniq"}), step("alice", "create", {"title": "Suite $uniq"})]}]
    await at.run_scenarios(sc, ROUTES, send)
    await at.run_scenarios(sc, ROUTES, send)
    t = [b["body"]["title"] for b in send.sent]
    assert t[0] == t[1] and t[2] == t[3] and t[0] != t[2]
    assert "$uniq" not in t[0] and len(t[0]) == len("Suite ") + 6


async def test_a_failing_scenario_says_what_was_expected_and_what_came_back():
    send = Recorder({"POST /items": (403, {"error": "not_manager", "message": "no"})})
    tests = [{"name": "wrong code", "steps": [step("alice", "create", {}, {"status": 201})]},
             {"name": "right refusal", "steps": [step("alice", "create", {}, {"status": "4xx", "error": "not_manager"})]},
             {"name": "wrong error", "steps": [step("alice", "create", {}, {"error": "forbidden"})]}]
    rep = await at.run_scenarios(tests, ROUTES, send)
    assert [r["passed"] for r in rep["results"]] == [False, True, False] and rep["passed"] == 1
    assert "expected status 201, got 403" in rep["results"][0]["reason"]
    assert "expected error 'forbidden'" in rep["results"][2]["reason"]
    errs = at.failure_errors(rep)
    assert [e["where"] for e in errs] == ["scenario:wrong code", "scenario:wrong error"] and "TEST is wrong" in errs[0]["message"]


async def test_bad_references_fail_with_a_reason_not_a_crash():
    send = Recorder({"GET /items/1": (200, {"id": 1})})
    tests = [{"name": "a", "steps": [step("alice", "get", {"id": "$nope"})]},
             {"name": "b", "steps": [step("mallory", "get", {"id": 1})]},
             {"name": "c", "steps": [step("alice", "missingOp")]},
             {"name": "d", "steps": [step("alice", "get", {"id": 1}, {}, {"v": "no.such"})]},
             {"name": "e", "steps": [step("alice", "get", {})]}]
    rep = await at.run_scenarios(tests, ROUTES, send)
    reasons = [r["reason"] for r in rep["results"]]
    assert "never saved" in reasons[0] and "unknown user" in reasons[1] and "no route named" in reasons[2]
    assert "cannot save" in reasons[3] and "path value 'id'" in reasons[4] and rep["passed"] == 0


async def test_a_slow_step_times_out_and_the_total_budget_is_bounded():
    import asyncio

    async def hang():
        await asyncio.sleep(5)
        return 200, {}

    send = Recorder({"GET /slow": hang})
    tests = [{"name": "s1", "steps": [step("alice", "slow")]}, {"name": "s2", "steps": [step("alice", "slow")]}]
    rep = await at.run_scenarios(tests, ROUTES, send, step_timeout=0.1, total_timeout=0.15)
    assert "no answer within" in rep["results"][0]["reason"]
    assert not rep["results"][1]["passed"] and rep["results"][1]["reason"]          # timed out or out of budget, never silently skipped


def test_normalize_drops_malformed_scenarios_and_the_toggle_defaults_on(monkeypatch):
    assert at.normalize_tests([{"name": "", "steps": []}, "x", {"name": "ok", "steps": [{"op": "a"}, {"nope": 1}]}])[0]["steps"][0]["as"] == ""
    monkeypatch.delenv("WORKFLOW_ONESHOT_AUTOTEST", raising=False)
    assert at.enabled()
    monkeypatch.setenv("WORKFLOW_ONESHOT_AUTOTEST", "0")
    assert not at.enabled()


def office_scenarios(list_status: int) -> list[dict[str, Any]]:
    return [
        {"name": "staff submits and reads own request", "steps": [
            step("alice", "createRequest", {"item": "Pens", "quantity": 2, "reason": "stock"}, {"status": 201}, {"rid": "id"}),
            step("alice", "getRequest", {"id": "$rid"}, {"fields": {"status": "pending", "item": "Pens"}})]},
        {"name": "staff cannot list every request", "steps": [step("alice", "listRequests", {}, {"status": list_status})]}]


async def test_a_failing_scenario_goes_to_the_one_fix_pass_and_passes_after(pg, monkeypatch):
    from src.services import project_plan as pp

    bundle = typed_bundle_json()
    bundle["tests"] = office_scenarios(200)             # wrong: the app refuses a non-approver with 403
    fixed = {"message": "the test was wrong: staff are refused", "tests": office_scenarios(403)[1:]}
    m = FakeModel(bundles=[bundle], changes=[fixed])
    monkeypatch.setattr("src.engine.llm_provider.call_llm", m)
    logs: list[str] = []

    async def log(msg):
        logs.append(msg)

    pid = await new_project(pg)
    res = await ob.build(pid, pp.normalize(plan_json()),
                         app_key="supply_requests", log=log)
    assert m.calls == ["emit_bundle", "emit_changes"], m.calls
    assert "Checks: 1/2 scenarios passing" in logs and "Checks: 2/2 scenarios passing" in logs, logs
    assert res["ok"] and res["errors"] == [], res["errors"]
    assert res["autotest"]["total"] == 2 and res["autotest"]["passed"] == 2
    assert res["autotest"]["tests_changed_by_fix"] == ["staff cannot list every request"]
    assert any("changed test" in line for line in logs)


async def test_a_scenario_still_failing_after_the_fix_is_a_named_gap(pg, monkeypatch):
    from src.services import project_plan as pp

    bundle = typed_bundle_json()
    bundle["tests"] = office_scenarios(200)
    m = FakeModel(bundles=[bundle], changes=[{"message": "nothing", "app": {"files": [{"path": "notes.md", "content": "x"}]}}])
    monkeypatch.setattr("src.engine.llm_provider.call_llm", m)
    pid = await new_project(pg)
    res = await ob.build(pid, pp.normalize(plan_json()), app_key="supply_requests")
    assert not res["ok"] and res["autotest"]["passed"] == 1
    scen = [e for e in res["errors"] if e["where"].startswith("scenario:")]
    assert [e["where"] for e in scen] == ["scenario:staff cannot list every request"]
    gaps = bc.named_gaps(scen)
    assert gaps["scenarios"] and "expected status 200, got 403" in gaps["scenarios"][0]


async def test_a_bundle_without_tests_skips_gracefully_and_the_toggle_turns_them_off(pg, monkeypatch):
    from src.services import project_plan as pp

    m = FakeModel(bundles=[typed_bundle_json()])
    monkeypatch.setattr("src.engine.llm_provider.call_llm", m)
    pid = await new_project(pg)
    res = await ob.build(pid, pp.normalize(plan_json()), app_key="supply_requests")
    assert res["ok"] and res["autotest"]["total"] == 0 and "no tests" in res["autotest"]["skipped"]

    monkeypatch.setenv("WORKFLOW_ONESHOT_AUTOTEST", "0")
    b = typed_bundle_json()
    b["tests"] = office_scenarios(200)
    monkeypatch.setattr("src.engine.llm_provider.call_llm", FakeModel(bundles=[b]))
    res2 = await ob.build(await new_project(pg), pp.normalize(plan_json()), app_key="supply_requests")
    assert res2["ok"] and "off" in res2["autotest"]["skipped"]


async def test_a_truncated_single_call_falls_back_to_writing_feature_by_feature(monkeypatch):
    plan = big_plan()
    plan["routes"], plan["data"] = plan["routes"][:3], {"tables": plan["data"]["tables"][:2]}
    assert not ob.is_big(plan)
    calls: list[str] = []

    async def fake(model, messages, temperature=0.2, tools=None, **kw):
        user = messages[-1]["content"]
        key = next((k for k in ("alpha", "beta", "gamma") if f"the `{k}` feature area" in user), None)
        calls.append(key or "whole")
        if key is None:
            return LLMResponse(text="", output_truncated=True)
        return LLMResponse(tool_calls=[ToolCall(id=key, name="emit_bundle", args={
            "tables": [], "module": {"name": key, "source": "import studio\n"},
            "app": {"files": [{"path": f"views/{key.title()}List.tsx", "content": "export default function V() { return null }"}]}})])

    monkeypatch.setattr("src.engine.llm_provider.call_llm", fake)
    logs: list[str] = []

    async def log(m):
        logs.append(m)

    bundle = await ob.write_app(plan, app_key="big", log=log)
    assert calls[0] == "whole" and sorted(calls[1:]) == ["alpha", "beta", "gamma"]
    assert any("cut off" in line for line in logs) and len(bundle["modules"]) == 3


def test_a_build_report_never_has_an_unnamed_gap():
    assert bc.named_gaps([]) == {}
    gaps = bc.named_gaps([{"where": "app", "code": "tsc", "message": "bad type"},
                          {"where": "scenario:s", "code": "scenario_failed", "message": "step 1: expected status 201, got 403. Fix the code so"}])
    assert gaps["checks"] == ["[app] bad type"] and gaps["scenarios"] == ["s: step 1: expected status 201, got 403"]
    merged = bc._merge_autotest([{"total": 1, "passed": 1, "results": [{"name": "a", "passed": True, "reason": "ok"}]}, None])
    assert merged["total"] == 1 and merged["passed"] == 1

"""Stage 6 B4: the builder loop in process on the throwaway Postgres, with the hand-written fake LLM scripts
(tests/fixtures/fake_llm/ai_builder). No network, no real model."""

from __future__ import annotations

import json

import pytest
from sqlalchemy import text

from src.engine import wf_ai
from src.repositories.ai_proposal_repository import AIProposalRepository
from src.services import ai_builder as B
from src.services import proposal_sandbox as sb
from tests.unit._stage6_b4_support import PROJECT, install_script, load_rules, prompts_seen, system_lines, uninstall
from tests.unit.conftest import anyio_backend, pg, pg_dbname  # noqa: F401

pytestmark = pytest.mark.anyio


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    wf_ai.clear_cache()
    for k in ("WF_AI_MAX_CALLS", "WF_AI_MAX_REPAIR_ROUNDS", "WF_AI_BUILD_DEADLINE_S", "WF_AI_STORE_PROMPTS"):
        monkeypatch.delenv(k, raising=False)
    B._BUSY.clear()
    yield
    uninstall()


async def spec_and_build(request: str = "Amounts over 500 are marked for review, the rest approved, the caller always gets the status.",
                         actor: str = "Asha") -> dict:
    v = await B.draft_spec(PROJECT, request, actor)
    assert v["status"] == "spec_draft", v
    v = await B.approve_spec(v["id"], actor)
    assert v["status"] == "spec_approved"
    return await B.build(v["id"], actor)


async def events(pid: str) -> list[str]:
    return [e["event"] for e in (await B.list_events(pid))["items"]]


async def test_first_try_builds_a_ready_proposal_with_passing_tests(pg):
    fake = install_script("first_try")
    v = await spec_and_build()
    assert v["status"] == "ready" and v["kind"] == "build"
    rep = v["report"]
    assert rep["passed"] is True and rep["stopped_because"] == "passed" and len(rep["rounds"]) == 1
    assert rep["tests"]["summary"]["total"] == 3 and rep["tests"]["summary"]["passed"] == 3  # the fixture spec now has the 3 tests SPEC009 asks for
    assert rep["checker"]["errors"] == 0
    assert rep["spec_coverage"] == {"steps_missing": [], "steps_wrong_type": [], "rules_untested": []}
    d = v["proposed_definition"]
    assert [n["name"] for n in d["nodes"]] == ["Receive amount", "Over limit?", "Mark for review", "Mark auto approved", "Reply"]
    assert all(n["position"] != {"x": 0, "y": 0} or n["name"] == "Receive amount" for n in d["nodes"])
    assert {t["status"] for t in d["tests"]} == {"approved"} and all(t["locked"] for t in d["tests"])
    assert v["diff"]["counts"]["added"] == 5 and len(d["connections"]) == 5
    assert v["usage"]["llm_calls"] == 2
    assert system_lines(fake) == ["WF_AI:SPEC", "WF_AI:BUILD"]
    ev = await events(v["id"])
    assert ev[0] == "spec_requested" and "spec_drafted" in ev and "spec_approved" in ev
    assert ev.count("round_started") == 1 and "ops_applied" in ev and "checked" in ev and "tested" in ev and ev[-1] == "build_finished"
    assert ev.count("llm_call") == 2


async def test_a_repair_round_fixes_a_broken_first_draft(pg):
    fake = install_script("repairable")
    v = await spec_and_build()
    assert v["status"] == "ready"
    rep = v["report"]
    assert rep["passed"] is True and rep["stopped_because"] == "passed" and len(rep["rounds"]) == 2
    assert rep["rounds"][0]["rejected"] >= 1 and rep["rounds"][0]["tests"]["failed"] >= 1   # the unknown node type, the wrong limit
    assert rep["rounds"][1]["tests"]["failed"] == 0 and rep["best_round"] == 1
    log = [o for o in v["ops_log"] if o["result"] == "error"]
    assert any("Teleport" in o["message"] or "node type" in o["message"] for o in log)
    assert system_lines(fake) == ["WF_AI:SPEC", "WF_AI:BUILD", "WF_AI:REPAIR"]
    # the repair prompt carried the evidence: the op error and the failing test with its trace
    repair_user = json.loads(fake.calls[2]["messages"][-1]["content"])
    assert repair_user["op_errors"] and repair_user["test_failures"]
    assert repair_user["test_failures"][0]["trace"] and repair_user["round"] == 1


async def test_gives_up_after_three_repair_rounds_with_a_clean_result(pg):
    install_script("unrepairable")
    v = await spec_and_build()
    assert v["status"] == "ready"                       # a usable definition, red tests
    rep = v["report"]
    assert rep["passed"] is False and rep["stopped_because"] == "rounds_exhausted" and len(rep["rounds"]) == 4
    assert v["usage"]["llm_calls"] == 5 and v["error_code"] is None
    with pytest.raises(sb.TestsFailing):                # and Keep says so
        async with B._sf()() as s:
            await sb.keep(s, v["id"], "Asha")


async def test_budget_exhaustion_stops_the_loop_and_keeps_the_best_round(pg, monkeypatch):
    monkeypatch.setenv("WF_AI_MAX_CALLS", "2")
    install_script("call_budget")
    v = await spec_and_build()
    assert v["status"] == "ready" and v["report"]["stopped_because"] == "call_budget" and v["report"]["passed"] is False
    assert len(v["report"]["rounds"]) == 1 and v["usage"]["llm_calls"] == 2


async def test_budget_exhausted_before_round_zero_is_a_502_and_a_failed_proposal(pg, monkeypatch):
    monkeypatch.setenv("WF_AI_MAX_CALLS", "1")
    install_script("first_try")
    v = await B.draft_spec(PROJECT, "Triage amounts", "Asha")
    v = await B.approve_spec(v["id"], "Asha")
    with pytest.raises(B.AIFailed) as ei:
        await B.build(v["id"], "Asha")
    assert ei.value.http_status == 502 and ei.value.extra["ai_code"] == "call_budget"
    row = await B.get_row(v["id"])
    assert row["status"] == "failed" and row["error_code"] == "call_budget"


async def test_no_progress_stops_early(pg):
    install_script("no_progress")
    v = await spec_and_build()
    assert v["report"]["stopped_because"] == "no_progress" and len(v["report"]["rounds"]) == 2 and v["report"]["passed"] is False


async def test_a_missing_spec_step_is_fed_back_and_fixed(pg):
    fake = install_script("missing_step")
    v = await spec_and_build()
    rep = v["report"]
    assert rep["passed"] is True and rep["stopped_because"] == "passed" and len(rep["rounds"]) == 2
    repair_user = json.loads(fake.calls[2]["messages"][-1]["content"])
    assert repair_user["spec_coverage"]["steps_missing"] == ["Reply"]


async def test_test_change_attempts_are_refused_and_surfaced(pg):
    install_script("test_change_attempt")
    v = await spec_and_build()
    assert v["status"] == "ready" and v["report"]["passed"] is True
    msgs = {o["op"]: o["message"] for o in v["ops_log"] if o["result"] == "error"}
    assert "update_test" in msgs and "add_test" in msgs and "remove_node" in msgs
    names = [t["name"] for t in v["proposed_definition"]["tests"]]
    assert "weakened" not in names and "sneaky" not in names and len(names) == 3
    assert any(n["name"] == "Over limit?" for n in v["proposed_definition"]["nodes"])
    tcr = v["test_change_requests"]
    assert len(tcr) == 1 and tcr[0]["test_id"] == v["spec"]["tests"][1]["id"] and "boundary" in tcr[0]["reason"]
    assert "test_change_requested" in await events(v["id"])
    # the tests in the proposal are exactly the approved ones
    assert sb.tampered_tests(v["spec"]["tests"], v["proposed_definition"]) == []


async def test_pii_and_secrets_never_reach_the_model(pg, monkeypatch):
    from cryptography.fernet import Fernet
    from src.services import credential_service

    # patch the settings object credential_service actually reads (other tests may rebind src.core.config.settings)
    monkeypatch.setattr(credential_service.settings, "encryption_key", Fernet.generate_key().decode())
    from src.repositories.variable_repository import VariableRepository
    from src.services.project_service import effective_environment, resolve_project

    secret = "CANARY-" + "ab12cd34-ef56-7890-ab12-cd34ef567890"
    async with pg() as s:
        env = effective_environment(await resolve_project(s, PROJECT), "default")
        var = await VariableRepository(s).create("BANK_KEY", secret, environment=env, type="secret", project_id=PROJECT)
    fake = install_script("first_try")
    request = (f"Triage amounts. Pay to card 4111 1111 1111 1111, contact ana.perez@bank-example.com, phone 415-555-0132, "
               f"SSN 123-45-6789, account number 12345678901. Use key {secret} for the call.")
    v = await B.draft_spec(PROJECT, request, "Asha")
    v = await B.approve_spec(v["id"], "Asha")
    v = await B.build(v["id"], "Asha")
    assert v["status"] == "ready"
    seen = "\n".join(prompts_seen(fake))
    for leaked in (secret, "4111 1111 1111 1111", "ana.perez@bank-example.com", "415-555-0132", "123-45-6789", "12345678901"):
        assert leaked not in seen, leaked
    assert "[CARD]" in seen and "[EMAIL]" in seen
    assert v["request"].count("[CARD]") == 1 and secret not in json.dumps(v)
    ev = (await B.list_events(v["id"]))["items"]
    scrubbed = next(e for e in ev if e["event"] == "scrubbed")["payload"]
    assert scrubbed["secrets"] >= 1 and scrubbed["pii"]["card"] == 1 and scrubbed["pii"]["email"] == 1
    assert any("card number" in w for w in v["spec"]["warnings"])
    row = await B.get_row(v["id"])
    assert row["request_sha256"] == B._sha(request)      # integrity of the original, never stored
    async with pg() as s:
        await VariableRepository(s).delete(var.id)


async def test_a_bad_first_reply_goes_through_the_json_fix_call(pg):
    fake = install_script("bad_json_then_fix")
    v = await B.draft_spec(PROJECT, "Triage amounts", "Asha")
    assert v["status"] == "spec_draft" and len(v["spec"]["steps"]) == 5
    assert system_lines(fake) == ["WF_AI:SPEC", "WF_AI:JSON_FIX"] and v["usage"]["llm_calls"] == 2
    outcomes = [e["payload"]["outcome"] for e in (await B.list_events(v["id"]))["items"] if e["event"] == "llm_call"]
    assert outcomes == ["bad_json", "ok"]


async def test_bad_json_twice_fails_the_proposal_with_502(pg):
    install_script("bad_json_twice")
    with pytest.raises(B.AIFailed) as ei:
        await B.draft_spec(PROJECT, "Triage amounts", "Asha")
    assert ei.value.http_status == 502 and ei.value.extra["ai_code"] == "bad_json"
    row = await B.get_row(ei.value.extra["proposal_id"])
    assert row["status"] == "failed" and row["error_code"] == "bad_json"


async def test_an_invalid_spec_gets_one_repair_call(pg):
    fake = install_script("spec_repair")
    v = await B.draft_spec(PROJECT, "Triage amounts", "Asha")
    assert system_lines(fake) == ["WF_AI:SPEC", "WF_AI:SPEC_REPAIR"]
    assert not [i for i in v["spec"]["issues"] if i["level"] == "error"] and len(v["spec"]["tests"]) == 3
    first_issues = json.loads(fake.calls[1]["messages"][-1]["content"])["issues"]
    assert any(i["code"] == "SPEC004" for i in first_issues)


async def test_the_injection_exfil_node_is_refused_when_an_allowlist_exists(pg):
    async with pg() as s:
        await s.execute(text("UPDATE projects SET settings = jsonb_set(coalesce(settings, '{}'::jsonb), '{egress_allowlist}', '[\"api.bank.test\"]') "
                             "WHERE id = :p"), {"p": PROJECT})
        await s.commit()
    try:
        install_script("injection")
        v = await spec_and_build()
    finally:
        async with pg() as s:
            await s.execute(text("UPDATE projects SET settings = settings - 'egress_allowlist' WHERE id = :p"), {"p": PROJECT})
            await s.commit()
    assert v["status"] == "ready"
    assert not any(n["type"] == "HttpRequest" for n in v["proposed_definition"]["nodes"])
    assert "exfil.example" not in json.dumps(v["proposed_definition"]) and v["diff"]["new_destinations"] == []
    assert any("egress" in o["message"] for o in v["ops_log"] if o["result"] == "error")


async def test_the_stale_reaper_fails_an_old_building_row(pg):
    from src.engine.stale_reaper import StaleReaper

    install_script("first_try")
    v = await B.draft_spec(PROJECT, "Triage amounts", "Asha")
    async with pg() as s:
        await s.execute(text("UPDATE ai_proposals SET status = 'building', updated_at = now() - interval '10 minutes' WHERE id = :i"), {"i": v["id"]})
        fresh = await AIProposalRepository(s).insert({"project_id": PROJECT, "kind": "build", "status": "building", "actor": "B", "request": "r",
                                                      "request_sha256": "0" * 64, "model": "gemini-3.5-flash", "provider": "gemini"})
        await s.commit()
    await StaleReaper(pg)._tick()
    old, new = await B.get_row(v["id"]), await B.get_row(fresh["id"])
    assert old["status"] == "failed" and old["error_code"] == "deadline"
    assert new["status"] == "building"                   # a recent build is left alone
    assert "build_finished" in await events(v["id"])


async def test_a_model_that_sends_no_ops_ends_failed_with_a_clear_error(pg):
    install_script("empty_ops")
    v = await spec_and_build()
    assert v["status"] == "failed" and v["error_code"] == "no_workflow" and "did not add any nodes" in v["error_message"]
    assert v["report"]["passed"] is False and v["report"]["stopped_because"] in ("no_progress", "rounds_exhausted")
    with pytest.raises(sb.BadState):
        await B.keep(v["id"], "Asha", accept_failing=True)
    row = await B.get_row(v["id"])
    assert row["status"] == "failed"
    assert "build_finished" in await events(v["id"])


async def test_a_spec_node_with_the_wrong_type_can_be_swapped_but_not_just_removed(pg):
    fake = install_script("swap_node_type")
    v = await spec_and_build()
    assert v["status"] == "ready" and v["report"]["passed"] is True and len(v["report"]["rounds"]) == 2
    assert next(n for n in v["proposed_definition"]["nodes"] if n["name"] == "Over limit?")["type"] == "If"
    assert not [o for o in v["ops_log"] if o["op"] == "remove_node" and o["result"] == "error"]
    first = json.loads(fake.calls[2]["messages"][-1]["content"])
    assert first["spec_coverage"]["steps_wrong_type"] == [{"step": "Over limit?", "expected": "If", "actual": "Set"}]


async def test_a_deadline_before_round_zero_is_a_502_and_one_after_it_keeps_the_round(pg, monkeypatch):
    from src.engine.wf_ai import Meter

    install_script("repairable")
    v = await B.draft_spec(PROJECT, "Triage amounts", "Asha")
    v = await B.approve_spec(v["id"], "Asha")
    monkeypatch.setenv("WF_AI_BUILD_DEADLINE_S", "0.001")
    with pytest.raises(B.AIFailed) as ei:
        await B.build(v["id"], "Asha")
    assert ei.value.extra["ai_code"] == "deadline" and (await B.get_row(v["id"]))["error_code"] == "deadline"

    monkeypatch.delenv("WF_AI_BUILD_DEADLINE_S")
    install_script("repairable")
    v2 = await B.draft_spec(PROJECT, "Triage amounts, again", "Asha")
    v2 = await B.approve_spec(v2["id"], "Asha")
    monkeypatch.setattr(Meter, "remaining_s", lambda self: -1.0 if self.calls >= 2 else None)     # time runs out after round 0
    done = await B.build(v2["id"], "Asha")
    assert done["status"] == "ready" and done["report"]["stopped_because"] == "deadline" and len(done["report"]["rounds"]) == 1


async def test_a_round_that_changed_the_approved_tests_is_discarded_not_ranked(pg, monkeypatch):
    install_script("first_try")
    real = B.finalize

    def sabotage(defn, st, base_names):
        out = real(defn, st, base_names)
        for t in out[0].get("tests") or []:
            t["expect"] = t["expect"][:1]                        # weaken every approved test: fewer assertions to fail
        return out

    monkeypatch.setattr(B, "finalize", sabotage)
    v = await B.draft_spec(PROJECT, "Triage amounts", "Asha")
    v = await B.approve_spec(v["id"], "Asha")
    with pytest.raises(B.AIFailed) as ei:
        await B.build(v["id"], "Asha")
    assert ei.value.extra["ai_code"] == "tests_changed"
    row = await B.get_row(v["id"])
    assert row["status"] == "failed" and row["error_code"] == "tests_changed" and row["proposed_definition"] is None


async def test_a_model_that_sends_the_args_wrapped_or_with_other_names_is_still_understood(pg):
    rules = [
        load_rules("first_try")[0],
        {"id": "build", "when": {"system_matches": "^WF_AI:BUILD\\n"}, "respond": {"text": json.dumps({"ops": [
            {"op": "add_node", "args_json": json.dumps({"args": {"node_name": "Receive amount", "node_type": "Webhook", "params": {"method": "POST", "path": "w", "responseMode": "lastNode"}}})},
            {"op": "add_node", "args_json": json.dumps({"name": "Over limit?", "type": "If", "parameters": {"condition": "$json.body.amount > 500"}})},
            {"op": "add_node", "args_json": json.dumps({"name": "Mark for review", "type": "Set", "parameters": {"mode": "manual", "keepOnlySet": True, "fields": [{"name": "status", "value": "review"}]}})},
            {"op": "add_node", "args_json": json.dumps({"name": "Mark auto approved", "type": "Set", "parameters": {"mode": "manual", "keepOnlySet": True, "fields": [{"name": "status", "value": "approved"}]}})},
            {"op": "add_node", "args_json": json.dumps({"name": "Reply", "type": "Respond", "parameters": {"statusCode": 200, "body": {"status": "{{ $json.status }}"}}})},
            {"op": "connect", "args_json": json.dumps({"from": "receive amount", "to": "Over limit?"})},
            {"op": "connect", "args_json": json.dumps({"from": "Over limit?", "to": "Mark for review", "port": "True"})},
            {"op": "connect", "args_json": json.dumps({"from": "Over limit?", "to": "Mark auto approved", "port": "False"})},
            {"op": "connect", "args_json": json.dumps({"source": "Mark for review", "target": "Reply"})},
            {"op": "connect", "args_json": json.dumps({"source": "Mark auto approved", "target": "Reply"})}]})}}]
    install_script(rules)
    v = await spec_and_build()
    assert v["status"] == "ready" and v["report"]["passed"] is True and len(v["report"]["rounds"]) == 1
    codes = {o["op"] for o in v["ops_log"] if o["result"] == "auto_fix"}
    assert "FIX13" in codes and "FIX07" in codes          # the bare If condition was wrapped; True / False were read as the ports true / false


async def test_nodes_the_model_named_its_own_way_take_the_spec_step_names_in_round_zero(pg):
    install_script("renamed_nodes")
    v = await spec_and_build()
    assert v["status"] == "ready" and v["report"]["passed"] is True and len(v["report"]["rounds"]) == 1
    assert [n["name"] for n in v["proposed_definition"]["nodes"]] == ["Receive amount", "Over limit?", "Mark for review", "Mark auto approved", "Reply"]
    assert [o["op"] for o in v["ops_log"] if o["result"] == "auto_fix"].count("FIX14") == 5


# ── live-build events for the studio canvas (round_shape, tested.results) ────────────────────────


async def event_items(pid: str) -> list[dict]:
    return (await B.list_events(pid))["items"]


def _fast(rules: list[dict]) -> list[dict]:
    return [{**r, "respond": {k: v for k, v in r["respond"].items() if k != "delay_ms"}} for r in rules]


async def test_showcase_emits_round_shape_and_per_test_results_every_round(pg):
    install_script(_fast(load_rules("showcase")))
    v = await spec_and_build("Classify refund requests; pay small ones at once, send larger ones to a manager, always reply with the status.")
    assert v["status"] == "ready" and v["report"]["passed"] is True and len(v["report"]["rounds"]) == 2
    items = await event_items(v["id"])
    names = [e["event"] for e in items]
    build = names[names.index("round_started"):names.index("build_finished")]
    per_round = ["round_started", "round_shape", "ops_applied", "checked", "tested"]
    assert [n for n in build if n in per_round] == per_round * 2
    shapes = [e["payload"] for e in items if e["event"] == "round_shape"]
    tested = [e["payload"] for e in items if e["event"] == "tested"]
    assert [s["round"] for s in shapes] == [0, 1]
    assert [n["name"] for n in shapes[0]["nodes"]] == ["Receive refund request", "Normalise request", "Over limit?", "Send to manager", "Pay refund",
                                                       "Log decision", "Reply"]
    assert all(set(n) == {"name", "type", "position"} and set(n["position"]) == {"x", "y"} for n in shapes[0]["nodes"])
    assert len(shapes[0]["connections"]) == 7 and shapes[0]["changed"] == [] and shapes[0]["removed"] == []
    assert shapes[1]["changed"] == [{"name": "Over limit?", "fields": ["condition"]}] and shapes[1]["removed"] == []
    # nothing a person typed or a model wrote into a parameter is on the wire
    blob = json.dumps(shapes)
    assert "parameters" not in blob and "manager_review" not in blob and "USD" not in blob and "amount" not in blob
    # round 0 fails exactly one test, round 1 fixes it
    assert (tested[0]["failed"], tested[1]["failed"]) == (1, 0)
    res0, res1 = tested[0]["results"], tested[1]["results"]
    assert len(res0) == len(res1) == 3
    assert [r["status"] for r in res0].count("failed") == 1 and {r["status"] for r in res1} == {"passed"}
    for r in res0 + res1:
        assert set(r) == {"test_id", "name", "status", "path", "failed_nodes"} and r["path"][0] == "Receive refund request"
        assert r["path"][-1] == "Reply"
    bad = next(r for r in res0 if r["status"] == "failed")
    assert bad["failed_nodes"] and bad["path"] == ["Receive refund request", "Normalise request", "Over limit?", "Pay refund", "Log decision", "Reply"]
    assert next(r for r in res1 if r["name"].startswith("A large"))["path"][3] == "Send to manager"


def test_run_order_is_rebuilt_from_the_wiring_not_the_definition_order():
    defn = {"nodes": [{"name": n} for n in ("Reply", "B", "Skipped", "A", "Trigger")],
            "connections": [{"source_node": "Trigger", "target_node": "A"}, {"source_node": "A", "target_node": "B"},
                            {"source_node": "A", "target_node": "Skipped"}, {"source_node": "B", "target_node": "Reply"}]}
    assert B._run_order(defn, {"Reply", "B", "A", "Trigger"}) == ["Trigger", "A", "B", "Reply"]
    cyc = {"nodes": [{"name": "X"}, {"name": "Y"}], "connections": [{"source_node": "X", "target_node": "Y"}, {"source_node": "Y", "target_node": "X"}]}
    assert B._run_order(cyc, {"X", "Y"}) == ["X", "Y"]


def test_shape_of_reports_removed_changed_and_a_type_swap_without_values():
    prev = {"nodes": [{"name": "A", "type": "Set", "parameters": {"k": 1, "same": 2}}, {"name": "Gone", "type": "Set"}], "connections": []}
    now = {"nodes": [{"name": "A", "type": "Code", "parameters": {"k": 9, "same": 2}, "position": {"x": 5, "y": 6}}], "connections": [
        {"source_node": "A", "target_node": "A2", "source_output": "true"}]}
    s = B._shape_of(3, now, prev)
    assert s["removed"] == ["Gone"] and s["changed"] == [{"name": "A", "fields": ["type", "k"]}]
    assert s["nodes"] == [{"name": "A", "type": "Code", "position": {"x": 5, "y": 6}}] and s["connections"] == [["A", "true", "A2", "main"]]

"""Stage 6 B4: AI on any node (fix_error, write_code, write_expression, explain) in process on the throwaway Postgres,
with the hand-written fake LLM scripts. Every task makes a node_assist proposal behind the same gate as the builder."""

from __future__ import annotations

import json

import pytest

from src.engine import wf_ai
from src.repositories.workflow_repository import WorkflowRepository, definition_to_workflow, workflow_to_definition
from src.services import ai_builder as B
from src.services import ai_node_assist as NA
from src.services import proposal_sandbox as sb
from tests.unit._stage6_b4_support import PROJECT, install_script, system_lines, uninstall
from tests.unit.conftest import anyio_backend, pg, pg_dbname  # noqa: F401

pytestmark = pytest.mark.anyio


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    wf_ai.clear_cache()
    for k in ("WF_AI_MAX_CALLS", "WF_AI_SEND_RUN_VALUES"):
        monkeypatch.delenv(k, raising=False)
    B._BUSY.clear()
    yield
    uninstall()


FEE_TEST = {"id": "t_b4000001", "name": "fee is 1% of the amount", "target": {"kind": "workflow"}, "category": "normal",
            "input": {"items": [{"body": {"amount": 1000}, "headers": {}, "query": {}, "method": "POST"}]}, "mocks": {},
            "expect": [{"node": "Compute fee", "path": "fee", "op": "eq", "value": 10}], "status": "approved", "approved_by": "sam", "source": "human"}


def fee_workflow(value: str = "{{ $json.body.amnt * 0.01 }}", code: bool = False) -> dict:
    compute = ({"name": "Compute fee", "type": "Code", "parameters": {"code": "return items"}, "position": {"x": 280, "y": 0}} if code else
               {"name": "Compute fee", "type": "Set", "parameters": {"mode": "manual", "keepOnlySet": True, "fields": [{"name": "fee", "value": value}]},
                "position": {"x": 280, "y": 0}})
    test = dict(FEE_TEST)
    if code:
        test["expect"] = [{"node": "Compute fee", "path": "fee", "op": "eq", "value": 10}]
    return {"name": "Fee", "nodes": [
        {"name": "Receive amount", "type": "Webhook", "parameters": {"method": "POST", "path": "fee-b4", "responseMode": "lastNode"}, "position": {"x": 0, "y": 0}},
        compute,
        {"name": "Reply", "type": "Respond", "parameters": {"statusCode": 200, "body": {"fee": "{{ $json.fee }}"}}, "position": {"x": 560, "y": 0}}],
        "connections": [{"source_node": "Receive amount", "target_node": "Compute fee", "source_output": "main", "target_input": "main"},
                        {"source_node": "Compute fee", "target_node": "Reply", "source_output": "main", "target_input": "main"}],
        "settings": {}, "tests": [test], "test_defaults": {}}


async def seed(pg, definition: dict) -> str:
    async with pg() as s:
        wf = await WorkflowRepository(s).create(definition_to_workflow(definition, "Fee"), project_id=PROJECT)
    return wf.id


async def draft(pg, wid: str) -> dict:
    async with pg() as s:
        return workflow_to_definition((await WorkflowRepository(s).get(wid)).workflow)


async def test_explain_answers_without_a_gate_and_is_cached(pg):
    fake = install_script("node_explain")
    wid = await seed(pg, fee_workflow())
    status, body = await NA.run(wid, "Compute fee", "explain", "Asha", instruction="What does this do?")
    assert status == 200 and "one percent" in body["answer"]
    row = await B.get_row(body["proposal_id"])
    assert row["status"] == "answered" and row["kind"] == "node_assist" and row["answer"] == body["answer"] and row["workflow_id"] == wid
    assert system_lines(fake) == ["WF_AI:NODE_EXPLAIN"]
    # the second identical question is a cache hit: no second model call, still logged
    status, again = await NA.run(wid, "Compute fee", "explain", "Asha", instruction="What does this do?")
    assert status == 200 and len(fake.calls) == 1
    ev = (await B.list_events(again["proposal_id"]))["items"]
    assert [e["payload"]["cached"] for e in ev if e["event"] == "llm_call"] == [True]
    assert (await draft(pg, wid)) == await draft(pg, wid)


async def test_fix_error_proposes_a_fix_that_makes_the_approved_test_pass(pg):
    fake = install_script("node_fix_error")
    wid = await seed(pg, fee_workflow())
    before = await draft(pg, wid)
    status, v = await NA.run(wid, "Compute fee", "fix_error", "Asha", instruction="the fee is always empty, the field is called amount")
    assert status == 201 and v["kind"] == "node_assist" and v["status"] == "ready", v
    rep = v["report"]
    assert rep["passed"] is True and rep["assist"]["task"] == "fix_error" and rep["base"]["approved_failed"] == 1
    assert rep["tests"]["summary"]["passed"] == 1
    fee = next(n for n in v["proposed_definition"]["nodes"] if n["name"] == "Compute fee")
    assert fee["parameters"]["fields"][0]["value"] == "{{ $json.body.amount * 0.01 }}"
    assert v["diff"]["nodes"]["changed"] == [{"name": "Compute fee", "fields": ["parameters.fields"]}]
    assert await draft(pg, wid) == before                     # nothing saved before Keep
    # the model saw shapes and the scope, not values
    user = json.loads(fake.calls[0]["messages"][-1]["content"])
    assert user["scope"] == ["Compute fee", "Receive amount", "Reply"] and user["untrusted_instruction"].startswith("the fee")
    # Keep writes it through the normal save path; the approved test is untouched; Undo restores the draft
    async with pg() as s:
        r = await sb.keep(s, v["id"], "Asha")
    after = await draft(pg, wid)
    assert after["nodes"][1]["parameters"]["fields"][0]["value"] == "{{ $json.body.amount * 0.01 }}"
    assert [t["id"] for t in after["tests"]] == ["t_b4000001"] and after["tests"][0]["locked"] is True
    async with pg() as s:
        await sb.undo(s, v["id"], "Asha")
    assert (await draft(pg, wid))["nodes"][1]["parameters"] == before["nodes"][1]["parameters"]
    assert r["workflow_id"] == wid


async def test_fix_error_cannot_touch_nodes_outside_its_scope(pg):
    install_script("node_fix_error_out_of_scope")
    d = fee_workflow()                      # Audit is two steps downstream of the failing node: not in scope
    d["nodes"].append({"name": "Audit", "type": "Set", "parameters": {"mode": "manual", "fields": []}, "position": {"x": 840, "y": 0}})
    d["connections"].append({"source_node": "Reply", "target_node": "Audit", "source_output": "main", "target_input": "main"})
    wid = await seed(pg, d)
    status, v = await NA.run(wid, "Compute fee", "fix_error", "Asha", instruction="fee is empty")
    assert v["status"] == "ready" and v["report"]["passed"] is True
    refused = [o for o in v["ops_log"] if o["result"] == "error"]
    assert len(refused) == 1 and refused[0]["args"]["node"] == "Audit" and "out of scope" in refused[0]["message"]
    audit = next(n for n in v["proposed_definition"]["nodes"] if n["name"] == "Audit")
    assert audit["parameters"] == {"mode": "manual", "fields": []}


async def test_fix_error_without_a_failed_run_or_instruction_is_a_422(pg):
    install_script("node_fix_error")
    wid = await seed(pg, fee_workflow())
    with pytest.raises(NA.NoFailedRun) as ei:
        await NA.run(wid, "Compute fee", "fix_error", "Asha")
    assert ei.value.http_status == 422 and ei.value.code == "no_failed_run"


async def test_write_expression_sets_the_field_after_the_gate(pg):
    fake = install_script("node_write_expression")
    wid = await seed(pg, fee_workflow())
    status, v = await NA.run(wid, "Compute fee", "write_expression", "Asha", field="fields[0].value", instruction="one percent of the amount")
    assert status == 201 and v["status"] == "ready" and v["report"]["passed"] is True
    fee = next(n for n in v["proposed_definition"]["nodes"] if n["name"] == "Compute fee")
    assert fee["parameters"]["fields"][0]["value"] == "{{ $json.body.amount * 0.01 }}" and fee["parameters"]["fields"][0]["name"] == "fee"
    assert system_lines(fake) == ["WF_AI:NODE_EXPR"]
    user = json.loads(fake.calls[0]["messages"][-1]["content"])
    assert user["field"] == "fields[0].value" and "amnt" in user["untrusted_current_value"] and "Receive amount" in user["upstream_nodes"]


async def test_write_expression_with_a_bad_expression_is_not_green(pg):
    install_script("node_write_expression_bad")
    wid = await seed(pg, fee_workflow())
    status, v = await NA.run(wid, "Compute fee", "write_expression", "Asha", field="fields[0].value", instruction="whatever")
    assert status == 201 and v["report"]["passed"] is False and v["report"]["warnings"]


async def test_write_expression_needs_a_field_and_write_code_needs_a_code_node(pg):
    install_script("node_write_expression")
    wid = await seed(pg, fee_workflow())
    with pytest.raises(B.RequestInvalid):
        await NA.run(wid, "Compute fee", "write_expression", "Asha", instruction="x")
    with pytest.raises(B.RequestInvalid):
        await NA.run(wid, "Compute fee", "write_code", "Asha", instruction="x")


async def test_unknown_node_workflow_and_task_are_404_or_422(pg):
    install_script("node_explain")
    wid = await seed(pg, fee_workflow())
    with pytest.raises(sb.ProposalNotFound):
        await NA.run(wid, "Nope", "explain", "Asha")
    with pytest.raises(sb.ProposalNotFound):
        await NA.run("wf_missing_b4", "Compute fee", "explain", "Asha")
    with pytest.raises(B.RequestInvalid):
        await NA.run(wid, "Compute fee", "dance", "Asha")
    with pytest.raises(B.ActorRequired):
        await NA.run(wid, "Compute fee", "explain", "  ")


async def test_write_code_sets_the_code_after_a_dry_run(pg):
    install_script("node_write_code")
    wid = await seed(pg, fee_workflow(code=True))
    status, v = await NA.run(wid, "Compute fee", "write_code", "Asha", instruction="fee = 1% of the amount")
    assert status == 201 and v["kind"] == "node_assist" and v["status"] == "ready"
    code = next(n for n in v["proposed_definition"]["nodes"] if n["name"] == "Compute fee")["parameters"]["code"]
    assert "new_item" in code and "0.01" in code
    assert v["report"]["dry_run"]["status"] == "passed" and v["report"]["passed"] is True       # the example item came from the approved test's input
    assert [d for d in v["ops_log"] if d["op"] == "set_parameter" and d["result"] == "ok"]
    assert v["report"]["tests"]["summary"]["passed"] == 1                                       # and the approved test still passes with the new code


async def test_write_code_gets_one_repair_round_with_the_failure_as_feedback(pg):
    fake = install_script("node_write_code_repair")
    wid = await seed(pg, fee_workflow(code=True))
    status, v = await NA.run(wid, "Compute fee", "write_code", "Asha", instruction="fee = 1% of the amount")
    assert v["status"] == "ready" and v["report"]["passed"] is True and len(v["report"]["rounds"]) == 2
    assert system_lines(fake) == ["WF_AI:NODE_CODE", "WF_AI:NODE_CODE"]
    second = json.loads(fake.calls[1]["messages"][-1]["content"])
    assert "division" in second["untrusted_error"] or "zero" in second["untrusted_error"] or "failed" in second["untrusted_error"]
    assert "/ 0" in second["untrusted_current_code"]
    code = next(n for n in v["proposed_definition"]["nodes"] if n["name"] == "Compute fee")["parameters"]["code"]
    assert "0.01" in code and "/ 0" not in code

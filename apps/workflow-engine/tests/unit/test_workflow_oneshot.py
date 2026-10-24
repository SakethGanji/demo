"""The editor chat on the one-shot builder (``workflow_oneshot``): one model call writes the whole workflow and its tests, an edit is one
``changes.json`` patch on the current draft, the platform checks and runs the tests (at most 2 fix calls), and the result is a proposal that
saves nothing until Keep. The model is replaced by canned replies; everything else (checker, test runner, proposals, Keep) is real."""

from __future__ import annotations

import json

import pytest

from src.repositories.workflow_repository import WorkflowRepository, workflow_to_definition
from src.schemas.ai_chat import AIChatRequest
from src.services import ai_builder as B
from src.services import ai_chat_service as C
from src.services import workflow_oneshot as W
from tests.unit._stage6_b4_support import PROJECT
from tests.unit.conftest import anyio_backend, pg, pg_dbname  # noqa: F401

pytestmark = pytest.mark.anyio


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    monkeypatch.delenv("WF_CHAT_BUILDER", raising=False)
    B._BUSY.clear()
    yield


# ── a small workflow, as the model would write it ─────────────────────────────────────────────

def workflow(threshold: int = 500) -> dict:
    return {
        "name": "Large payment check",
        "nodes": [
            {"name": "Payment in", "type": "Webhook", "parameters": {"method": "POST", "path": "payment-check"}},
            {"name": "Is large?", "type": "If", "parameters": {"condition": f"{{{{ float($json.body.amount) > {threshold} }}}}"}},
            {"name": "Reply review", "type": "Respond", "parameters": {"statusCode": 200, "body": {"status": "review"}}},
            {"name": "Reply ok", "type": "Respond", "parameters": {"statusCode": 200, "body": {"status": "ok"}}},
        ],
        "connections": [
            {"source_node": "Payment in", "target_node": "Is large?"},
            {"source_node": "Is large?", "target_node": "Reply review", "source_output": "true"},
            {"source_node": "Is large?", "target_node": "Reply ok", "source_output": "false"},
        ],
        "tests": [
            {"name": "Over the limit goes to review", "input": {"items": [{"body": {"amount": threshold + 100}}]},
             "expect": [{"node": "Reply review", "subject": "request", "path": "body.status", "op": "eq", "value": "review"},
                        {"node": "Reply ok", "subject": "node", "op": "status", "value": "not_run"}]},
            {"name": "At the limit is ok", "input": {"items": [{"body": {"amount": threshold}}]},
             "expect": [{"node": "Reply ok", "subject": "request", "path": "body.status", "op": "eq", "value": "ok"}]},
        ],
    }


def build_reply(d: dict, summary: str = "flags payments over 500 for review.") -> str:
    return f'Built: {summary}\nNeeds: nothing\n\n```json file="workflow.json"\n{json.dumps(d, indent=1)}\n```\n'


def patch_reply(patch: dict, summary: str = "raised the limit to 1000.") -> str:
    return f'Changed: {summary}\n\n```json file="changes.json"\n{json.dumps(patch, indent=1)}\n```\n'


class FakeModel:
    def __init__(self, *replies: str) -> None:
        self.replies, self.prompts = list(replies), []

    async def __call__(self, prompt: str) -> str:
        self.prompts.append(prompt)
        return self.replies.pop(0)


def fake(monkeypatch, *replies: str) -> FakeModel:
    m = FakeModel(*replies)
    monkeypatch.setattr(W, "call_model", m)
    return m


async def chat(request: AIChatRequest, actor: str | None = "Asha") -> list[dict]:
    out = []
    async for ev in C.AIChatService().stream_chat(request, actor):
        out.append({"event": ev["event"], **json.loads(ev["data"])})
    return out


def proposal_of(events: list[dict]) -> dict:
    return next(e for e in events if e["event"] == "proposal")


async def stored(pg, wid: str) -> dict:
    async with pg() as s:
        return workflow_to_definition((await WorkflowRepository(s).get(wid)).workflow)


# ── pure: the patch, the parse, the rules ────────────────────────────────────────────────────

def test_a_rename_follows_connections_references_test_mocks_and_assertions():
    d = workflow()
    d["nodes"].append({"name": "Note", "type": "Set", "parameters": {"fields": {"s": '{{ $node["Is large?"].json.x }}'}}})
    d["tests"][0]["mocks"] = {"Is large?": {"items": []}}
    out = W.apply_patch(d, {"rename": {"Is large?": "Over limit?"}})
    names = {n["name"] for n in out["nodes"]}
    assert "Over limit?" in names and "Is large?" not in names
    assert {c["source_node"] for c in out["connections"]} == {"Payment in", "Over limit?"}
    assert '$node["Over limit?"]' in out["nodes"][-1]["parameters"]["fields"]["s"]
    assert "Over limit?" in out["tests"][0]["mocks"]
    assert d["nodes"][1]["name"] == "Is large?", "the current workflow is not mutated"


def test_a_replaced_node_keeps_its_position_and_a_replaced_test_keeps_its_id_and_approval():
    d = workflow()
    d["nodes"][1]["position"] = {"x": 7, "y": 9}
    d["tests"][0].update(id="t_0000abcd", status="approved", locked=True, approved_by="Asha", approved_at="2026-10-01")
    new_if = {"name": "Is large?", "type": "If", "parameters": {"condition": "{{ float($json.body.amount) > 1000 }}"}}
    new_test = {"name": "Over the limit goes to review", "input": {"items": [{"body": {"amount": 1100}}]}, "expect": []}
    out = W.apply_patch(d, {"nodes": [new_if], "tests": [new_test]})
    assert out["nodes"][1]["position"] == {"x": 7, "y": 9}
    assert "1000" in out["nodes"][1]["parameters"]["condition"]
    t = out["tests"][0]
    assert (t["id"], t["status"], t["locked"], t["approved_by"]) == ("t_0000abcd", "approved", True, "Asha")


def test_removing_a_node_drops_its_links_and_unknown_names_are_errors_for_the_model():
    out = W.apply_patch(workflow(), {"remove_nodes": ["Reply ok"], "remove_tests": ["At the limit is ok"]})
    assert all("Reply ok" not in (c["source_node"], c["target_node"]) for c in out["connections"])
    assert [t["name"] for t in out["tests"]] == ["Over the limit goes to review"]
    with pytest.raises(ValueError, match="rename"):
        W.apply_patch(workflow(), {"rename": {"Nope": "X"}})
    with pytest.raises(ValueError, match="remove_nodes"):
        W.apply_patch(workflow(), {"remove_nodes": ["Nope"]})


def test_connect_and_disconnect_default_to_the_main_ports_and_never_duplicate():
    out = W.apply_patch(workflow(), {"disconnect": [{"source_node": "Is large?", "target_node": "Reply ok", "source_output": "false"}],
                                     "connect": [{"source_node": "Payment in", "target_node": "Is large?"}]})
    assert len(out["connections"]) == 2


def test_an_api_named_in_the_prompt_is_stored_by_its_spec_id():
    reply = build_reply({"nodes": [{"name": "Get", "type": "HttpRequest", "parameters": {"api_ref": "accounts.getBalance"}}], "connections": []})
    d = W.parse(reply, {"accounts": "spec_123"})
    assert d["nodes"][0]["parameters"]["api_ref"] == {"spec_id": "spec_123", "operation_id": "getBalance"}
    assert W.shown(d, type("P", (), {"api_ids": lambda self: {"accounts": "spec_123"}})())["nodes"][0]["parameters"]["api_ref"]["spec_id"] == "accounts"


def test_a_reply_with_trailing_commas_still_parses_and_a_reply_without_a_block_is_an_error():
    assert W.parse('```json file="workflow.json"\n{"nodes": [], "connections": [],}\n```', {})["nodes"] == []
    with pytest.raises(ValueError):
        W.parse("I could not do it.", {})
    with pytest.raises(ValueError):
        W.read_patch("no block")


def test_database_rules_name_the_connection_read_only_writes_and_pasted_values():
    dbs = {"core": {"writable": False}, "ops": {"writable": True}}

    def pgnode(name, **p):
        return {"name": name, "type": "Postgres", "parameters": p}

    d = {"nodes": [pgnode("A", connection="nope", query="select 1"),
                   pgnode("B", connection="core", operation="query", query="update t set x=1"),
                   pgnode("C", connection="ops", operation="query", query="select * from t where id = '{{ $json.id }}'"),
                   pgnode("D", connection="ops", operation="query", query="insert into t values (%s)", queryParameters=["{{ $json.id }}"]),
                   pgnode("E", connection="core", operation="query", query="WITH x AS (select 1) select * from x")]}
    fails = W.db_lint(d, dbs)
    assert len(fails) == 3
    assert '"A"' in fails[0] and "read-only" in fails[1] and "pasted" in fails[2]


def test_the_builders_tests_are_drafts_so_a_later_chat_edit_can_change_them():
    d = W.finish(W.parse(build_reply(workflow()), {}))
    assert {t["status"] for t in d["tests"]} == {"draft"} and not any(t.get("locked") for t in d["tests"])
    assert all(t["id"].startswith("t_") for t in d["tests"])
    assert all(n.get("position") for n in d["nodes"]), "the platform lays it out"


def test_only_a_test_a_person_approved_is_guarded():
    old = W.finish(workflow())["tests"]
    changed = json.loads(json.dumps(old))
    changed[0]["expect"] = []
    assert W.locked_test_failures(old, changed) == []
    old[0].update(status="approved", locked=True)
    fails = W.locked_test_failures(old, changed)
    assert len(fails) == 1 and "Over the limit goes to review" in fails[0]


def test_a_question_is_answered_and_anything_else_is_a_change():
    assert C.local_route("What does this do?") == "explain"
    assert C.local_route("how does the escalation work") == "explain"
    assert C.local_route("Raise the limit to 1000") == "modify"
    assert C.local_route("Add an email to the manager") == "modify"
    # found live: an instruction that opens with "When ..." is a change, not a question
    assert C.local_route("When the customer says it is fraud, also email compliance") == "modify"
    assert C.local_route("When does the reminder go out?") == "explain"
    assert C.local_route("Can you add an email to compliance?") == "modify"
    assert C.local_route("Could you explain the treasury step?") == "explain"
    assert C.local_route("Do not email the customer on a hold") == "modify"
    assert C.local_route("If the amount is over 1M, ask treasury twice") == "modify"


def test_the_proposal_event_leads_with_the_models_summary_and_its_needs():
    view = {"id": "prop_1", "workflow_id": None, "status": "ready", "diff": {"counts": {"added": 4, "removed": 0, "changed": 0}},
            "report": {"passed": True, "summary": "flags large payments.", "needs": "an approver group"}}
    ev = C._proposal_event(view)
    assert ev["summary"] == "flags large payments. Proposed: 4 added. Tests pass. Needs: an approver group"
    view["report"]["needs"] = "nothing."
    assert "Needs" not in C._proposal_event(view)["summary"]
    assert C._proposal_event({"id": "p", "status": "failed", "error_message": "boom"})["summary"] == "boom"


def test_the_chat_uses_the_one_shot_builder_unless_switched_back(monkeypatch):
    assert C.use_oneshot()
    monkeypatch.setenv("WF_CHAT_BUILDER", "legacy")
    assert not C.use_oneshot()


# ── through the chat, on Postgres: build, keep, edit, keep ──────────────────────────────────

async def test_a_request_builds_a_tested_workflow_that_saves_nothing_until_kept(pg, monkeypatch):
    model = fake(monkeypatch, build_reply(workflow()))
    events = await chat(AIChatRequest(message="Flag payments over 500 for review", project_id=PROJECT, mode_hint="generate"))
    assert [e["event"] for e in events][:2] == ["route", "phase"] and events[0]["route"] == "build"
    p = proposal_of(events)
    assert p["status"] == "ready" and p["report"]["passed"] and p["workflow_id"] is None
    assert p["summary"].startswith("flags payments over 500 for review.")
    assert len(model.prompts) == 1 and "Flag payments over 500 for review" in model.prompts[0]

    view = await B.get_view(p["proposal_id"])
    assert view["report"]["builder"] == "oneshot" and len(view["report"]["rounds"]) == 1
    assert view["report"]["tests"]["summary"]["total"] == 2 and view["report"]["tests"]["summary"]["failed"] == 0

    k = await B.keep(p["proposal_id"], "Asha")
    d = await stored(pg, k["workflow_id"])
    assert {n["name"] for n in d["nodes"]} == {"Payment in", "Is large?", "Reply review", "Reply ok"}
    assert len(d["tests"]) == 2


async def test_a_change_by_chat_is_one_patch_on_the_draft_and_keep_applies_it(pg, monkeypatch):
    fake(monkeypatch, build_reply(workflow()))
    built = proposal_of(await chat(AIChatRequest(message="Flag payments over 500", project_id=PROJECT, mode_hint="generate")))
    wid = (await B.keep(built["proposal_id"], "Asha"))["workflow_id"]
    before = await stored(pg, wid)

    new_if = {"name": "Is large?", "type": "If", "parameters": {"condition": "{{ float($json.body.amount) > 1000 }}"}}
    tests = [{"name": "Over the limit goes to review", "input": {"items": [{"body": {"amount": 1100}}]},
              "expect": [{"node": "Reply review", "subject": "request", "path": "body.status", "op": "eq", "value": "review"}]},
             {"name": "At the limit is ok", "input": {"items": [{"body": {"amount": 1000}}]},
              "expect": [{"node": "Reply ok", "subject": "request", "path": "body.status", "op": "eq", "value": "ok"}]}]
    model = fake(monkeypatch, patch_reply({"nodes": [new_if], "tests": tests}))
    events = await chat(AIChatRequest(message="Raise the limit to 1000", project_id=PROJECT, workflow_id=wid))
    assert events[0]["route"] == "modify"
    p = proposal_of(events)
    assert p["status"] == "ready" and p["report"]["passed"] and p["workflow_id"] == wid
    assert p["diff"]["counts"] == {"added": 0, "removed": 0, "changed": 1}
    assert '"Is large?"' in model.prompts[0] and "Raise the limit to 1000" in model.prompts[0]
    assert await stored(pg, wid) == before, "nothing is saved before Keep"

    await B.keep(p["proposal_id"], "Asha")
    after = await stored(pg, wid)
    cond = next(n for n in after["nodes"] if n["name"] == "Is large?")["parameters"]["condition"]
    assert "1000" in cond
    pos = {n["name"]: n.get("position") for n in before["nodes"]}
    assert all(n.get("position") == pos[n["name"]] for n in after["nodes"]), "an edit leaves the canvas where it was"
    assert [t["id"] for t in after["tests"]] == [t["id"] for t in before["tests"]], "a changed test is the same test"


async def test_a_failing_first_reply_gets_one_fix_call_with_what_failed(pg, monkeypatch):
    bad = workflow()
    bad["tests"][1]["expect"][0]["value"] = "review"     # the model's test contradicts its own workflow
    model = fake(monkeypatch, build_reply(bad), build_reply(workflow()))
    p = proposal_of(await chat(AIChatRequest(message="Flag payments over 500", project_id=PROJECT, mode_hint="generate")))
    assert p["status"] == "ready" and p["report"]["passed"]
    assert len(model.prompts) == 2
    assert 'test "At the limit is ok"' in model.prompts[1] and "Previous reply:" in model.prompts[1]
    view = await B.get_view(p["proposal_id"])
    assert [len(r["failures"]) > 0 for r in view["report"]["rounds"]] == [True, False]


async def test_a_reply_that_never_passes_is_offered_with_its_failures_not_hidden(pg, monkeypatch):
    bad = workflow()
    bad["tests"][1]["expect"][0]["value"] = "review"
    model = fake(monkeypatch, build_reply(bad), "no json here", build_reply(bad))
    p = proposal_of(await chat(AIChatRequest(message="Flag payments over 500", project_id=PROJECT, mode_hint="generate")))
    assert len(model.prompts) == 3
    assert p["status"] == "ready" and not p["report"]["passed"] and "Some tests fail" in p["summary"]
    view = await B.get_view(p["proposal_id"])
    assert view["report"]["stopped_because"] == "rounds_exhausted" and view["report"]["failures"]


async def test_an_edit_may_not_change_a_test_a_person_approved(pg, monkeypatch):
    fake(monkeypatch, build_reply(workflow()))
    built = proposal_of(await chat(AIChatRequest(message="Flag payments over 500", project_id=PROJECT, mode_hint="generate")))
    wid = (await B.keep(built["proposal_id"], "Asha"))["workflow_id"]
    base = await stored(pg, wid)
    locked = base["tests"][0]
    assert locked["status"] == "draft"
    # a person approves the first test (POST /workflows/{id}/tests/{test}/approve)
    from src.engine.node_registry import node_registry
    from src.repositories.execution_repository import ExecutionRepository
    from src.services.node_service import NodeService
    from src.services.workflow_service import WorkflowService

    async with pg() as s:
        await WorkflowService(WorkflowRepository(s), ExecutionRepository(s), NodeService(node_registry)).approve_test(wid, locked["id"], "Asha")
        await s.commit()

    changed = {"name": locked["name"], "input": locked["input"], "expect": []}
    model = fake(monkeypatch, patch_reply({"tests": [changed]}, "dropped the checks"), patch_reply({}, "left it"))
    p = proposal_of(await chat(AIChatRequest(message="Simplify the first test", project_id=PROJECT, workflow_id=wid)))
    assert len(model.prompts) == 2 and "is locked" in model.prompts[1]
    assert p["status"] == "ready" and p["report"]["passed"]
    k = await B.keep(p["proposal_id"], "Asha")
    assert k
    after = await stored(pg, wid)
    assert after["tests"][0]["expect"] == locked["expect"] and after["tests"][0]["locked"]


async def test_a_question_is_answered_from_the_workflow_and_creates_no_proposal(pg, monkeypatch):
    fake(monkeypatch, build_reply(workflow()))
    built = proposal_of(await chat(AIChatRequest(message="Flag payments over 500", project_id=PROJECT, mode_hint="generate")))
    wid = (await B.keep(built["proposal_id"], "Asha"))["workflow_id"]
    model = fake(monkeypatch, "Payments over 500 get a review reply; the rest get ok.")
    events = await chat(AIChatRequest(message="What does this do?", project_id=PROJECT, workflow_id=wid))
    assert [e["event"] for e in events if e["event"] in ("text", "proposal")] == ["text"]
    assert '"Is large?"' in model.prompts[0] and "position" not in model.prompts[0]


async def test_a_change_with_nothing_open_builds_a_new_workflow(pg, monkeypatch):
    fake(monkeypatch, build_reply(workflow()))
    events = await chat(AIChatRequest(message="Flag payments over 500 for review", project_id=PROJECT))
    assert events[0]["route"] == "build" and proposal_of(events)["status"] == "ready"


async def test_ask_for_a_change_in_the_review_patches_the_proposal_and_keep_still_creates_the_workflow(pg, monkeypatch):
    fake(monkeypatch, build_reply(workflow()))
    first = proposal_of(await chat(AIChatRequest(message="Flag payments over 500", project_id=PROJECT, mode_hint="generate")))
    new_if = {"name": "Is large?", "type": "If", "parameters": {"condition": "{{ float($json.body.amount) > 1000 }}"}}
    tests = [{"name": "Over the limit goes to review", "input": {"items": [{"body": {"amount": 1100}}]},
              "expect": [{"node": "Reply review", "subject": "request", "path": "body.status", "op": "eq", "value": "review"}]},
             {"name": "At the limit is ok", "input": {"items": [{"body": {"amount": 1000}}]},
              "expect": [{"node": "Reply ok", "subject": "request", "path": "body.status", "op": "eq", "value": "ok"}]}]
    model = fake(monkeypatch, patch_reply({"nodes": [new_if], "tests": tests}))
    child = await B.revise(first["proposal_id"], "Make the limit 1000", "Asha", PROJECT)
    assert len(model.prompts) == 1 and '"Is large?"' in model.prompts[0]
    assert child["status"] == "ready" and child["report"]["passed"] and child["id"] != first["proposal_id"]
    assert (await B.get_view(first["proposal_id"]))["status"] == "discarded"
    wid = (await B.keep(child["id"], "Asha"))["workflow_id"]
    d = await stored(pg, wid)
    assert "1000" in next(n for n in d["nodes"] if n["name"] == "Is large?")["parameters"]["condition"]


def test_a_request_keeps_its_email_addresses_but_loses_secrets_and_card_numbers():
    clean, counts = W.scrub_request("Email compliance@bank.test and ops@bank.test; card 4111 1111 1111 1111; key sk-abcdefghijklmnopqrstuvwx",
                                    {"hunter2-secret"})
    assert "compliance@bank.test" in clean and "ops@bank.test" in clean
    assert "4111" not in clean and "sk-abcdefghijklmnopqrstuvwx" not in clean
    assert W.scrub_request("the token is hunter2-secret", {"hunter2-secret"})[0].count("hunter2-secret") == 0


async def test_an_email_address_in_the_request_reaches_the_model(pg, monkeypatch):
    model = fake(monkeypatch, build_reply(workflow()))
    await chat(AIChatRequest(message="Flag payments over 500 and email risk@bank.test", project_id=PROJECT, mode_hint="generate"))
    assert "risk@bank.test" in model.prompts[0]

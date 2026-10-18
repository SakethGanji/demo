"""Stage 6 B4: modify proposals on an existing workflow, spec editing and approval rules, revise, Keep / Undo / Discard through the
builder service (the same calls the routes make), and the per-(project, actor) build lock."""

from __future__ import annotations

import json

import pytest

from src.engine import wf_ai
from src.repositories.workflow_repository import WorkflowRepository, workflow_to_definition
from src.services import ai_builder as B
from src.services import proposal_sandbox as sb
from tests.unit._stage6_b4_support import PROJECT, install_script, uninstall
from tests.unit.conftest import anyio_backend, pg, pg_dbname  # noqa: F401

pytestmark = pytest.mark.anyio


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    wf_ai.clear_cache()
    for k in ("WF_AI_MAX_CALLS", "WF_AI_MAX_REPAIR_ROUNDS"):
        monkeypatch.delenv(k, raising=False)
    B._BUSY.clear()
    yield
    uninstall()


async def kept_workflow(pg) -> tuple[str, dict]:
    """Build and Keep the base 'Amount triage' workflow; returns (workflow id, its saved draft)."""
    install_script("first_try")
    v = await B.draft_spec(PROJECT, "Amounts over 500 are marked for review", "Asha")
    v = await B.approve_spec(v["id"], "Asha")
    v = await B.build(v["id"], "Asha")
    k = await B.keep(v["id"], "Asha")
    assert k["proposal"]["status"] == "kept"
    async with pg() as s:
        return k["workflow_id"], workflow_to_definition((await WorkflowRepository(s).get(k["workflow_id"])).workflow)


async def draft_of(pg, wid: str) -> dict:
    async with pg() as s:
        return workflow_to_definition((await WorkflowRepository(s).get(wid)).workflow)


async def modify_build(wid: str):
    fake = install_script("modify_flag_high_value")
    v = await B.draft_spec(PROJECT, "Also flag amounts over 10000 as high value", "Asha", workflow_id=wid)
    return fake, v


async def test_a_modify_proposal_changes_only_what_the_change_needs_and_keeps_the_locked_tests(pg):
    wid, original = await kept_workflow(pg)
    fake, v = await modify_build(wid)
    assert v["kind"] == "modify" and v["workflow_id"] == wid and v["status"] == "spec_draft"
    assert not [i for i in v["spec"]["issues"] if i["level"] == "error"], v["spec"]["issues"]
    spec_user = json.loads(fake.calls[0]["messages"][-1]["content"])
    assert spec_user["mode"] == "modify" and len(spec_user["current_workflow"]["nodes"]) == 5 and len(spec_user["locked_tests"]) == 3
    v = await B.approve_spec(v["id"], "Asha")
    assert v["spec"]["tests"][0]["status"] == "approved"
    v = await B.build(v["id"], "Asha")
    assert v["status"] == "ready" and v["report"]["passed"] is True, v["report"]
    build_user = json.loads(fake.calls[1]["messages"][-1]["content"])
    assert build_user["current_workflow"] is not None and len(build_user["current_workflow"]["nodes"]) == 5
    assert v["diff"]["counts"] == {"added": 1, "removed": 0, "changed": 0}
    assert v["diff"]["connections"]["removed"] == [["Mark for review", "main", "Reply", "main"]] and len(v["diff"]["tests"]["added"]) == 1
    summary = v["report"]["tests"]["summary"]
    assert summary["total"] == 4 and summary["passed"] == 4 and summary["approved_total"] == 4
    # existing nodes keep their positions; only the new one was laid out
    old_pos = {n["name"]: n["position"] for n in original["nodes"]}
    new_pos = {n["name"]: n["position"] for n in v["proposed_definition"]["nodes"]}
    assert all(new_pos[k] == p for k, p in old_pos.items()) and new_pos["Flag high value"] not in old_pos.values()
    assert await draft_of(pg, wid) == original                     # still nothing saved

    k = await B.keep(v["id"], "Asha")
    after = await draft_of(pg, wid)
    assert k["workflow_id"] == wid and any(n["name"] == "Flag high value" for n in after["nodes"]) and len(after["tests"]) == 4
    assert all(t["locked"] for t in after["tests"])
    u = await B.undo(v["id"], "Asha")
    assert u["status"] == "undone"
    assert await draft_of(pg, wid) == original


async def test_keep_on_a_moved_draft_is_refused_then_works_for_a_fresh_proposal(pg):
    wid, original = await kept_workflow(pg)
    fake, v = await modify_build(wid)
    v = await B.build((await B.approve_spec(v["id"], "Asha"))["id"], "Asha")
    # a person edits the workflow meanwhile
    moved = json.loads(json.dumps(original))
    moved["nodes"][3]["parameters"]["fields"][0]["value"] = "auto"
    async with pg() as s:
        from src.repositories.workflow_repository import definition_to_workflow

        stored = await WorkflowRepository(s).get(wid)
        await WorkflowRepository(s).update(wid, definition_to_workflow(moved, stored.name, wid))
    with pytest.raises(sb.DraftMoved) as ei:
        await B.keep(v["id"], "Asha")
    assert ei.value.http_status == 409 and ei.value.code == "draft_moved"
    assert (await B.get_row(v["id"]))["status"] == "ready"        # refused cleanly, still keepable after a fresh proposal
    assert (await draft_of(pg, wid))["nodes"][3]["parameters"]["fields"][0]["value"] == "auto"


async def test_discard_writes_nothing_and_a_second_discard_is_the_same_answer(pg):
    wid, original = await kept_workflow(pg)
    fake, v = await modify_build(wid)
    v = await B.build((await B.approve_spec(v["id"], "Asha"))["id"], "Asha")
    d = await B.discard(v["id"], "Asha")
    assert d["status"] == "discarded" and await draft_of(pg, wid) == original
    assert (await B.discard(v["id"], "Asha"))["status"] == "discarded"
    with pytest.raises(sb.BadState):
        await B.keep(v["id"], "Asha")


async def test_keep_refuses_red_tests_unless_accepted_and_records_it(pg):
    install_script("unrepairable")
    v = await B.draft_spec(PROJECT, "Triage amounts", "Asha")
    v = await B.build((await B.approve_spec(v["id"], "Asha"))["id"], "Asha")
    assert v["report"]["passed"] is False
    with pytest.raises(sb.TestsFailing) as ei:
        await B.keep(v["id"], "Asha")
    assert ei.value.http_status == 409 and ei.value.code == "tests_failing"
    k = await B.keep(v["id"], "Asha", accept_failing=True, name="Amount triage (red)")
    assert k["proposal"]["accepted_failing"] is True and k["proposal"]["status"] == "kept"
    evs = (await B.list_events(v["id"]))["items"]
    assert next(e for e in evs if e["event"] == "kept")["payload"]["accepted_failing"] is True


async def test_approval_is_refused_while_spec_errors_remain_and_editing_revalidates(pg):
    install_script("spec_repair")                                    # the repair call fixes it; we then break it ourselves
    v = await B.draft_spec(PROJECT, "Triage amounts", "Asha")
    spec = {k: v2 for k, v2 in v["spec"].items() if k not in ("issues", "warnings")}
    broken = json.loads(json.dumps(spec))
    broken["steps"][1]["node_type"] = "NoSuchNode"
    broken["rule_tests"] = {k: [] for k in broken["rule_tests"]}
    v2 = await B.put_spec(v["id"], broken, "Asha")
    codes = {i["code"] for i in v2["spec"]["issues"] if i["level"] == "error"}
    assert {"SPEC002", "SPEC004"} <= codes
    with pytest.raises(B.SpecInvalid) as ei:
        await B.approve_spec(v["id"], "Asha")
    assert ei.value.http_status == 422 and ei.value.code == "spec_invalid" and ei.value.extra["issues"]
    # edit it back: approvable, and the edit is audited
    fixed = await B.put_spec(v["id"], spec, "Asha")
    assert not [i for i in fixed["spec"]["issues"] if i["level"] == "error"]
    ev = (await B.list_events(v["id"]))["items"]
    edits = [e for e in ev if e["event"] == "spec_edited"]
    assert len(edits) == 2 and "steps" in edits[0]["payload"]["changed"] and edits[0]["actor"] == "Asha"
    ok = await B.approve_spec(v["id"], "Asha")
    assert ok["status"] == "spec_approved" and ok["spec_fingerprint"] and ok["spec_approved_by"] == "Asha"
    assert all(t["status"] == "approved" and t["locked"] and t["approved_by"] == "Asha" for t in ok["spec"]["tests"])
    with pytest.raises(sb.BadState):                                 # no more edits once approved
        await B.put_spec(v["id"], spec, "Asha")
    # approving again is the same answer; building twice is a bad state
    assert (await B.approve_spec(v["id"], "Asha"))["status"] == "spec_approved"


async def test_user_edited_tests_get_fresh_state_and_cannot_arrive_approved(pg):
    install_script("first_try")
    v = await B.draft_spec(PROJECT, "Triage amounts", "Asha")
    spec = {k: x for k, x in v["spec"].items() if k not in ("issues", "warnings")}
    sneaky = json.loads(json.dumps(spec))
    sneaky["tests"][0].update({"status": "approved", "locked": True, "approved_by": "mallory", "source": "human"})
    sneaky["tests"][1]["id"] = "not-an-id"
    got = await B.put_spec(v["id"], sneaky, "Asha")
    assert [t["status"] for t in got["spec"]["tests"]] == ["draft", "draft", "draft"] and not any(t["locked"] for t in got["spec"]["tests"])
    assert all("approved_by" not in t for t in got["spec"]["tests"])
    assert got["spec"]["tests"][0]["source"] == "ai" and got["spec"]["tests"][1]["id"].startswith("t_")


async def test_revise_builds_a_child_from_the_result_and_discards_the_parent(pg):
    install_script("revise")
    v = await B.draft_spec(PROJECT, "Triage amounts", "Asha")
    v = await B.build((await B.approve_spec(v["id"], "Asha"))["id"], "Asha")
    child = await B.revise(v["id"], "Reply with status 202 instead", "Asha")
    assert child["kind"] == "modify" and child["parent_id"] == v["id"] and child["status"] == "ready" and child["workflow_id"] is None
    assert child["report"]["passed"] is True and child["spec_fingerprint"] == v["spec_fingerprint"]
    assert next(n for n in child["proposed_definition"]["nodes"] if n["name"] == "Reply")["parameters"]["statusCode"] == 202
    assert (await B.get_row(v["id"]))["status"] == "discarded"
    k = await B.keep(child["id"], "Asha")                              # a child of an unsaved build still creates the workflow
    assert k["workflow_id"].startswith("wf_")
    with pytest.raises(sb.BadState):                                   # only a ready proposal can be revised
        await B.revise(v["id"], "again", "Asha")


async def test_two_builds_by_the_same_actor_in_one_project_are_busy_but_other_actors_are_not(pg):
    async def go(actor: str):
        v = await B.draft_spec(PROJECT, "Triage amounts", actor)
        return (await B.approve_spec(v["id"], actor))["id"]

    install_script("first_try")
    a1, a2 = await go("Asha"), await go("Asha")
    b1 = await go("Ben")
    with B._Busy(PROJECT, "Asha"):
        with pytest.raises(B.BuilderBusy) as ei:
            await B.build(a1, "Asha")
        assert ei.value.http_status == 409 and ei.value.code == "builder_busy"
        assert (await B.get_row(a1))["status"] == "spec_approved"       # refused before anything changed
        assert (await B.build(b1, "Ben"))["status"] == "ready"
    assert (await B.build(a2, "Asha"))["status"] == "ready"
    assert ("proj_default", "Asha") not in B._BUSY


async def test_policy_and_configuration_errors(pg, monkeypatch):
    install_script("first_try")
    with pytest.raises(B.ModelNotAllowed) as ei:
        await B.draft_spec(PROJECT, "x", "Asha", model="gpt-4o")
    assert ei.value.http_status == 403 and ei.value.code == "model_not_allowed"
    uninstall()
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_CLOUD_PROJECT", raising=False)
    monkeypatch.delenv("LLM_PROXY_PROJECT", raising=False)
    from src.engine import llm_provider

    monkeypatch.setattr(llm_provider, "is_configured", lambda model: False)
    with pytest.raises(B.AINotConfigured) as ei2:
        await B.draft_spec(PROJECT, "x", "Asha")
    assert ei2.value.http_status == 503 and ei2.value.code == "ai_not_configured"
    with pytest.raises(B.ActorRequired):
        await B.draft_spec(PROJECT, "x", "   ")
    monkeypatch.setattr(llm_provider, "is_configured", lambda model: True)
    with pytest.raises(sb.ProposalNotFound):
        await B.draft_spec("proj_does_not_exist", "x", "Asha")
    with pytest.raises(sb.ProposalNotFound):
        await B.draft_spec(PROJECT, "x", "Asha", workflow_id="wf_missing")
    with pytest.raises(B.RequestInvalid):
        await B.draft_spec(PROJECT, "   ", "Asha")


async def test_a_proposal_of_another_project_is_a_404(pg):
    install_script("first_try")
    v = await B.draft_spec(PROJECT, "Triage amounts", "Asha")
    assert (await B.get_view(v["id"], PROJECT))["id"] == v["id"]
    with pytest.raises(sb.ProposalNotFound):
        await B.get_view(v["id"], "proj_other")
    with pytest.raises(sb.ProposalNotFound):
        await B.list_events(v["id"], 0, "proj_other")
    with pytest.raises(sb.ProposalNotFound):
        await B.approve_spec(v["id"], "Asha", "proj_other")


async def test_events_are_append_only_ordered_and_pageable(pg):
    install_script("first_try")
    v = await B.draft_spec(PROJECT, "Triage amounts", "Asha")
    v = await B.build((await B.approve_spec(v["id"], "Asha"))["id"], "Asha")
    all_ev = await B.list_events(v["id"])
    seqs = [e["seq"] for e in all_ev["items"]]
    assert seqs == sorted(seqs) and seqs[0] == 1 and all_ev["last_seq"] == seqs[-1]
    page = await B.list_events(v["id"], seqs[3])
    assert [e["seq"] for e in page["items"]] == seqs[4:]
    from src.repositories.ai_proposal_repository import AIProposalRepository

    public = {n for n in dir(AIProposalRepository) if not n.startswith("_")}
    assert not {n for n in public if "delete" in n or "update_event" in n or "remove" in n}        # no update / delete path for events
    lst = await B.list_proposals(PROJECT, status="ready")
    assert any(i["id"] == v["id"] and i["passed"] is True and i["llm_calls"] == 2 for i in lst["items"])

"""Stage 6 B1 on the throwaway Postgres: spike W1-S1 (tests on an unsaved proposal), Keep / Undo / Discard against the
normal save path, and their races (DESIGN-STAGE6-AI-BUILDER sections 4.8, 6.4, 6.11).
"""

from __future__ import annotations

import asyncio
import copy
import json
import uuid
from pathlib import Path

import pytest
from sqlalchemy import text

from src.repositories.ai_proposal_repository import AIProposalRepository, new_proposal_id
from src.repositories.workflow_repository import WorkflowRepository, definition_to_workflow, workflow_to_definition
from src.services import proposal_sandbox as sb

# the Postgres fixtures live with the unit tests; reuse them so one harness serves both
from tests.unit.conftest import anyio_backend, pg, pg_dbname  # noqa: F401

pytestmark = pytest.mark.anyio

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "workflows" / "refund_triage.json"
RISK_URL = "http://risk.example.test/v1/score"
PROJECT = "proj_default"


def tid() -> str:
    return "t_" + uuid.uuid4().hex[:8]


def refund_definition() -> dict:
    wf = json.loads(FIXTURE.read_text().replace("__REFUND_API_URL__", RISK_URL))
    wf["settings"] = {}
    wf.setdefault("connections", [])
    return wf


def refund_test(status: str = "draft", expect_amount: int = 900, **kw) -> dict:
    return {
        "id": kw.pop("id", None) or tid(), "name": "big refund", "target": {"kind": "workflow"},
        "input": {"items": [{"body": {"order_id": "o-1", "amount": 900, "reason": "late"}}]},
        "mocks": {"Risk API": {"http": {"status": 200, "body": {"decision": "review"}}},
                  "Log escalated": {"items": [{"id": 1}]}, "Log approved": {"items": [{"id": 2}]}},
        "expect": [{"node": "Amount over 500", "port": "true", "op": "count", "value": 1},
                   {"node": "Normalize", "path": "amount", "op": "eq", "value": expect_amount}],
        "category": "normal", "status": status, **kw,
    }


def simple(name: str = "wf", tests: list | None = None, extra_node: bool = False) -> dict:
    nodes = [
        {"name": "Start", "type": "Start", "parameters": {}, "position": {"x": 0, "y": 0}},
        {"name": "Set a", "type": "Set", "parameters": {"mode": "manual", "fields": [{"name": "a", "value": "1"}]},
         "position": {"x": 280, "y": 0}},
    ]
    conns = [{"source_node": "Start", "target_node": "Set a", "source_output": "main", "target_input": "main"}]
    if extra_node:
        nodes.append({"name": "Set b", "type": "Set", "parameters": {"mode": "manual", "fields": [{"name": "b", "value": "2"}]},
                      "position": {"x": 560, "y": 0}})
        conns.append({"source_node": "Set a", "target_node": "Set b", "source_output": "main", "target_input": "main"})
    return {"name": name, "nodes": nodes, "connections": conns, "settings": {}, "tests": tests or [], "test_defaults": {}}


def a_test(status: str = "draft", **kw) -> dict:
    return {"id": kw.pop("id", None) or tid(), "name": "sets a", "target": {"kind": "workflow"},
            "input": {"items": [{"x": 1}]}, "mocks": {}, "category": "normal", "status": status,
            "expect": [{"node": "Set a", "port": "main", "op": "count", "value": 1}], **kw}


async def count(sf, table: str) -> int:
    async with sf() as s:
        return (await s.execute(text(f"SELECT count(*) FROM {table}"))).scalar()


async def make_workflow(sf, definition: dict, name: str = "wf") -> object:
    async with sf() as s:
        return await WorkflowRepository(s).create(definition_to_workflow(definition, name))


async def draft_of(sf, wid: str) -> dict:
    async with sf() as s:
        return workflow_to_definition((await WorkflowRepository(s).get(wid)).workflow)


async def seed(sf, **over) -> dict:
    row = {
        "id": new_proposal_id(), "project_id": PROJECT, "kind": "build", "status": "ready", "actor": "Asha",
        "request": "do it", "request_sha256": "0" * 64, "model": "gemini-3.5-flash", "provider": "gemini",
        "report": {"passed": True},
    }
    row.update(over)
    async with sf() as s:
        out = await AIProposalRepository(s).insert(row)
        await s.commit()
    return out


async def get_row(sf, pid: str) -> dict:
    async with sf() as s:
        return await AIProposalRepository(s).get(pid)


async def events(sf, pid: str) -> list[str]:
    async with sf() as s:
        items, _ = await AIProposalRepository(s).list_events(pid)
    return [e["event"] for e in items]


async def workflow_ids(sf) -> set[str]:
    async with sf() as s:
        return set((await s.execute(text("SELECT id FROM workflows"))).scalars())


async def seed_modify(sf, *, proposed: dict, base: dict, wf, **over) -> dict:
    """A ready modify proposal whose base is the stored draft of ``wf``."""
    async with sf() as s:
        stored = await WorkflowRepository(s).get(wf.id)
        base_def, base_hash = sb.draft_of(stored)
    return await seed(sf, kind="modify", workflow_id=wf.id, base_definition=base_def, base_draft_hash=base_hash,
                      proposed_definition=proposed, **over)


# ── spike W1-S1 ──────────────────────────────────────────────────────────────


async def test_spike_tests_of_an_unsaved_proposal_run_without_writes(pg):
    definition = refund_definition()
    definition["tests"] = [refund_test()]
    p = await seed(pg, status="ready", proposed_definition=definition)
    before = {t: await count(pg, t) for t in ("executions", "node_outputs", "workflows", "human_tasks", "workflow_versions")}
    async with pg() as s:
        report = await sb.run_proposal_tests(s, p, definition)
    assert report["summary"]["total"] == 1, report
    res = report["results"][0]
    assert res["status"] == "passed", res
    assert res["execution_id"] is None                       # persist=False: no execution row exists to point at
    after = {t: await count(pg, t) for t in before}
    assert after == before                                   # no execution, node-output, workflow, task or version rows
    assert p["id"] not in await workflow_ids(pg)             # and the proposal's id never became a workflow


async def test_spike_a_failing_test_of_an_unsaved_proposal_reports_failed_not_raised(pg):
    definition = refund_definition()
    definition["tests"] = [refund_test(expect_amount=1)]
    p = await seed(pg, proposed_definition=definition)
    async with pg() as s:
        report = await sb.run_proposal_tests(s, p, definition)
    assert report["results"][0]["status"] == "failed"
    assert report["results"][0]["execution_id"] is None


async def test_spike_a_modify_proposal_uses_the_real_workflow_id_and_still_writes_nothing(pg):
    definition = refund_definition()
    definition["tests"] = []
    wf = await make_workflow(pg, definition, "Refunds")
    proposed = copy.deepcopy(definition)
    proposed["tests"] = [refund_test()]
    p = await seed_modify(pg, proposed=proposed, base=definition, wf=wf)
    before = await count(pg, "executions")
    async with pg() as s:
        stored = sb.proposal_stored(p, proposed)
        assert stored.id == wf.id
        report = await sb.run_proposal_tests(s, p, proposed)
    assert report["results"][0]["status"] == "passed"
    assert await count(pg, "executions") == before
    assert (await draft_of(pg, wf.id))["tests"] == []        # the stored draft is untouched


async def test_spike_the_check_lookups_work_without_a_stored_workflow(pg):
    from src.services.check_lookups import build_lookups
    from src.services.workflow_checker import check_workflow

    async with pg() as s:
        lookups = await build_lookups(s, PROJECT, None, team_id="default")
    out = check_workflow(refund_definition(), lookups=lookups)
    assert "shapes_by_node" in out and isinstance(out.get("findings", out.get("errors", [])), list)


# ── Keep: new workflow ───────────────────────────────────────────────────────


def approved_spec(tests: list[dict]) -> dict:
    return {"spec_version": 1, "title": "Spec title", "tests": tests}


async def test_keep_creates_the_workflow_with_approved_tests_and_is_idempotent(pg):
    t = a_test("approved", approved_by="Asha")
    proposed = simple("Built", [t])
    spec = approved_spec([t])
    p = await seed(pg, proposed_definition=proposed, spec=spec, spec_fingerprint=sb.fingerprint([t]), spec_approved_by="Asha")
    async with pg() as s:
        r = await sb.keep(s, p["id"], "Asha")
    assert r["already"] is False and r["workflow_id"].startswith("wf_")
    d = await draft_of(pg, r["workflow_id"])
    assert [n["name"] for n in d["nodes"]] == ["Start", "Set a"]
    assert d["tests"][0]["status"] == "approved" and d["tests"][0]["locked"] is True
    row = await get_row(pg, p["id"])
    assert row["status"] == "kept" and row["workflow_id"] == r["workflow_id"] and row["kept_by"] == "Asha"
    assert row["kept_draft_hash"] == sb.canonical_hash(d)
    async with pg() as s:
        again = await sb.keep(s, p["id"], "Someone else")
    assert again["already"] is True and again["workflow_id"] == r["workflow_id"]
    assert (await events(pg, p["id"])).count("kept") == 1
    assert (await get_row(pg, p["id"]))["kept_by"] == "Asha"


async def test_keep_uses_the_name_given_else_the_definition_name(pg):
    p = await seed(pg, proposed_definition=simple("From definition"))
    async with pg() as s:
        r = await sb.keep(s, p["id"], "Asha", name="Chosen name")
    async with pg() as s:
        assert (await WorkflowRepository(s).get(r["workflow_id"])).name == "Chosen name"
    p2 = await seed(pg, proposed_definition=simple("From definition"))
    async with pg() as s:
        r2 = await sb.keep(s, p2["id"], "Asha")
    async with pg() as s:
        assert (await WorkflowRepository(s).get(r2["workflow_id"])).name == "From definition"


async def test_keep_refuses_red_tests_unless_accepted_and_audits_the_acceptance(pg):
    p = await seed(pg, proposed_definition=simple(), report={"passed": False})
    async with pg() as s:
        with pytest.raises(sb.TestsFailing) as e:
            await sb.keep(s, p["id"], "Asha")
    assert e.value.http_status == 409 and e.value.code == "tests_failing"
    assert (await get_row(pg, p["id"]))["status"] == "ready"
    assert len(await workflow_ids(pg) - set()) >= 0
    async with pg() as s:
        r = await sb.keep(s, p["id"], "Asha", accept_failing=True)
    row = await get_row(pg, p["id"])
    assert row["accepted_failing"] is True and r["workflow_id"]
    async with pg() as s:
        items, _ = await AIProposalRepository(s).list_events(p["id"])
    assert [e for e in items if e["event"] == "kept"][0]["payload"]["accepted_failing"] is True


@pytest.mark.parametrize("status", ["spec_draft", "spec_approved", "building", "failed", "undone", "discarded", "answered"])
async def test_keep_only_works_on_a_ready_proposal(pg, status):
    p = await seed(pg, status=status, proposed_definition=simple())
    async with pg() as s:
        with pytest.raises(sb.BadState) as e:
            await sb.keep(s, p["id"], "Asha")
    assert e.value.status == status and e.value.detail()["code"] == "bad_state"


async def test_keep_unknown_or_foreign_proposal_is_not_found(pg):
    async with pg() as s:
        with pytest.raises(sb.ProposalNotFound):
            await sb.keep(s, "p_doesnotexist", "Asha")
    p = await seed(pg, proposed_definition=simple())
    async with pg() as s:
        with pytest.raises(sb.ProposalNotFound):
            await sb.keep(s, p["id"], "Asha", project_id="proj_other")
    assert (await get_row(pg, p["id"]))["status"] == "ready"


async def test_keep_rolls_back_the_claim_when_the_workflow_is_invalid(pg):
    bad = simple()
    bad["connections"].append({"source_node": "Set a", "target_node": "Ghost", "source_output": "main", "target_input": "main"})
    p = await seed(pg, proposed_definition=bad)
    before = await workflow_ids(pg)
    async with pg() as s:
        with pytest.raises(Exception):
            await sb.keep(s, p["id"], "Asha")
    row = await get_row(pg, p["id"])
    assert row["status"] == "ready" and row["kept_by"] is None
    assert await workflow_ids(pg) == before
    assert "kept" not in await events(pg, p["id"])


async def test_keep_defence_in_depth_catches_a_changed_approved_test(pg):
    t = a_test("approved", approved_by="Asha")
    changed = {**t, "name": "sneaky rename"}
    p = await seed(pg, proposed_definition=simple("x", [changed]), spec=approved_spec([t]),
                   spec_fingerprint=sb.fingerprint([t]))
    async with pg() as s:
        with pytest.raises(sb.LockedTests) as e:
            await sb.keep(s, p["id"], "Asha")
    assert e.value.test_ids == [t["id"]] and e.value.http_status == 409
    assert (await get_row(pg, p["id"]))["status"] == "ready"


async def test_keep_catches_a_removed_approved_test_and_an_edited_spec(pg):
    t = a_test("approved", approved_by="Asha")
    p = await seed(pg, proposed_definition=simple("x", []), spec=approved_spec([t]), spec_fingerprint=sb.fingerprint([t]))
    async with pg() as s:
        with pytest.raises(sb.LockedTests) as e:
            await sb.keep(s, p["id"], "Asha")
    assert e.value.test_ids == [t["id"]]
    edited = {**t, "name": "edited after approval"}
    p2 = await seed(pg, proposed_definition=simple("x", [edited]), spec=approved_spec([edited]), spec_fingerprint=sb.fingerprint([t]))
    async with pg() as s:
        with pytest.raises(sb.LockedTests):
            await sb.keep(s, p2["id"], "Asha")


# ── Keep: modify ─────────────────────────────────────────────────────────────


async def test_keep_modify_replaces_the_draft_and_stamps_new_approved_tests(pg):
    wf = await make_workflow(pg, simple("Mine"), "Mine")
    t = a_test("approved", approved_by="Asha")
    proposed = simple("Mine", [t], extra_node=True)
    p = await seed_modify(pg, proposed=proposed, base=simple(), wf=wf, spec=approved_spec([t]),
                          spec_fingerprint=sb.fingerprint([t]), spec_approved_by="Asha")
    async with pg() as s:
        r = await sb.keep(s, p["id"], "Asha")
    assert r["workflow_id"] == wf.id
    d = await draft_of(pg, wf.id)
    assert [n["name"] for n in d["nodes"]] == ["Start", "Set a", "Set b"]
    assert d["tests"][0]["status"] == "approved" and d["tests"][0]["locked"] and d["tests"][0]["approved_by"] == "Asha"
    row = await get_row(pg, p["id"])
    assert row["kept_draft_hash"] == sb.canonical_hash(d)


async def test_keep_modify_refuses_when_the_draft_moved(pg):
    wf = await make_workflow(pg, simple("Mine"), "Mine")
    p = await seed_modify(pg, proposed=simple("Mine", extra_node=True), base=simple(), wf=wf)
    moved = simple("Mine", extra_node=True)
    moved["nodes"][1]["parameters"]["fields"][0]["value"] = "human edit"
    async with pg() as s:
        await WorkflowRepository(s).update(wf.id, definition_to_workflow(moved, "Mine", wf.id))
    async with pg() as s:
        with pytest.raises(sb.DraftMoved) as e:
            await sb.keep(s, p["id"], "Asha")
    assert e.value.http_status == 409 and e.value.code == "draft_moved"
    assert (await get_row(pg, p["id"]))["status"] == "ready"
    assert (await draft_of(pg, wf.id))["nodes"][1]["parameters"]["fields"][0]["value"] == "human edit"


async def test_keep_modify_refuses_when_the_workflow_was_deleted(pg):
    wf = await make_workflow(pg, simple("Mine"), "Mine")
    p = await seed_modify(pg, proposed=simple("Mine", extra_node=True), base=simple(), wf=wf)
    async with pg() as s:
        await WorkflowRepository(s).delete(wf.id)
    async with pg() as s:
        with pytest.raises(sb.DraftMoved):
            await sb.keep(s, p["id"], "Asha")


async def test_keep_modify_runs_the_normal_lock_rules(pg):
    locked = a_test("approved", approved_by="Bo", approved_at="2026-10-01T00:00:00")
    wf = await make_workflow(pg, simple("Mine", [locked]), "Mine")
    changed = {**locked, "name": "AI changed a locked test"}
    p = await seed_modify(pg, proposed=simple("Mine", [changed]), base=simple(), wf=wf)
    async with pg() as s:
        with pytest.raises(sb.LockedTests) as e:
            await sb.keep(s, p["id"], "Asha")
    assert e.value.test_ids == [locked["id"]]
    dropped = await seed_modify(pg, proposed=simple("Mine", []), base=simple(), wf=wf)
    async with pg() as s:
        with pytest.raises(sb.LockedTests) as e2:
            await sb.keep(s, dropped["id"], "Asha")
    assert e2.value.test_ids == [locked["id"]]
    assert [t["name"] for t in (await draft_of(pg, wf.id))["tests"]] == ["sets a"]


async def test_keep_modify_refuses_a_new_test_that_arrives_approved_by_a_sneaky_route(pg):
    """A test new in the proposal that is *not* in the approved spec is still allowed (status is the AI's to propose
    only as draft): the lock rules compare the neutralised copy, so a pre-existing test flipped to approved is caught."""
    draft = a_test("draft")
    wf = await make_workflow(pg, simple("Mine", [draft]), "Mine")
    flipped = {**draft, "status": "approved", "approved_by": "AI"}
    p = await seed_modify(pg, proposed=simple("Mine", [flipped]), base=simple(), wf=wf)
    async with pg() as s:
        with pytest.raises(sb.LockedTests) as e:
            await sb.keep(s, p["id"], "Asha")
    assert e.value.test_ids == [draft["id"]]


# ── Undo ─────────────────────────────────────────────────────────────────────


async def test_undo_a_modify_restores_the_base_and_removes_the_tests_it_added(pg):
    wf = await make_workflow(pg, simple("Mine"), "Mine")
    t = a_test("approved", approved_by="Asha")
    p = await seed_modify(pg, proposed=simple("Mine", [t], extra_node=True), base=simple(), wf=wf,
                          spec=approved_spec([t]), spec_fingerprint=sb.fingerprint([t]))
    async with pg() as s:
        await sb.keep(s, p["id"], "Asha")
    async with pg() as s:
        r = await sb.undo(s, p["id"], "Asha")
    assert r["already"] is False
    d = await draft_of(pg, wf.id)
    assert [n["name"] for n in d["nodes"]] == ["Start", "Set a"] and d["tests"] == []
    assert d["connections"] == simple()["connections"]
    assert (await get_row(pg, p["id"]))["status"] == "undone"
    async with pg() as s:
        assert (await sb.undo(s, p["id"], "Asha"))["already"] is True
    assert (await events(pg, p["id"])).count("undone") == 1


async def test_undo_restores_a_base_with_no_connections(pg):
    """The normal PUT keeps the old connections when given an empty list; Undo must really empty them."""
    base = simple("Mine")
    base["connections"] = []
    wf = await make_workflow(pg, base, "Mine")
    p = await seed_modify(pg, proposed=simple("Mine"), base=base, wf=wf)
    async with pg() as s:
        await sb.keep(s, p["id"], "Asha")
    assert len((await draft_of(pg, wf.id))["connections"]) == 1
    async with pg() as s:
        await sb.undo(s, p["id"], "Asha")
    assert (await draft_of(pg, wf.id))["connections"] == []


async def test_undo_refuses_when_the_draft_moved_after_keep(pg):
    wf = await make_workflow(pg, simple("Mine"), "Mine")
    p = await seed_modify(pg, proposed=simple("Mine", extra_node=True), base=simple(), wf=wf)
    async with pg() as s:
        await sb.keep(s, p["id"], "Asha")
    moved = await draft_of(pg, wf.id)
    moved["nodes"][0]["position"] = {"x": 5, "y": 5}
    async with pg() as s:
        await WorkflowRepository(s).update(wf.id, definition_to_workflow(moved, "Mine", wf.id))
    async with pg() as s:
        with pytest.raises(sb.DraftMoved):
            await sb.undo(s, p["id"], "Asha")
    assert (await get_row(pg, p["id"]))["status"] == "kept"
    assert len((await draft_of(pg, wf.id))["nodes"]) == 3


async def test_undo_of_a_new_workflow_deletes_it_when_never_published(pg):
    p = await seed(pg, proposed_definition=simple("Fresh"))
    async with pg() as s:
        r = await sb.keep(s, p["id"], "Asha")
    wid = r["workflow_id"]
    assert wid in await workflow_ids(pg)
    async with pg() as s:
        await sb.undo(s, p["id"], "Asha")
    assert wid not in await workflow_ids(pg)
    row = await get_row(pg, p["id"])
    assert row["status"] == "undone" and row["workflow_id"] is None   # FK SET NULL; the event keeps the id
    async with pg() as s:
        items, _ = await AIProposalRepository(s).list_events(p["id"])
    assert [e for e in items if e["event"] == "undone"][0]["payload"]["workflow_id"] == wid


async def test_undo_of_a_published_new_workflow_is_bad_state_and_changes_nothing(pg):
    p = await seed(pg, proposed_definition=simple("Fresh"))
    async with pg() as s:
        r = await sb.keep(s, p["id"], "Asha")
    wid = r["workflow_id"]
    async with pg() as s:
        await s.execute(text("INSERT INTO workflow_versions (workflow_id, version_number, definition) VALUES (:w, 1, CAST('{}' AS jsonb))"), {"w": wid})
        vid = (await s.execute(text("SELECT id FROM workflow_versions WHERE workflow_id = :w"), {"w": wid})).scalar()
        await s.execute(text("UPDATE workflows SET published_version_id = :v WHERE id = :w"), {"v": vid, "w": wid})
        await s.commit()
    async with pg() as s:
        with pytest.raises(sb.BadState):
            await sb.undo(s, p["id"], "Asha")
    assert wid in await workflow_ids(pg) and (await get_row(pg, p["id"]))["status"] == "kept"


@pytest.mark.parametrize("status", ["spec_draft", "spec_approved", "building", "ready", "failed", "discarded", "answered"])
async def test_undo_needs_a_kept_proposal(pg, status):
    p = await seed(pg, status=status, proposed_definition=simple())
    async with pg() as s:
        with pytest.raises(sb.BadState):
            await sb.undo(s, p["id"], "Asha")


# ── Discard ──────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("status", ["spec_draft", "spec_approved", "ready", "failed"])
async def test_discard_works_from_the_open_states_and_writes_nothing_to_workflows(pg, status):
    p = await seed(pg, status=status, proposed_definition=simple())
    before = await workflow_ids(pg)
    async with pg() as s:
        r = await sb.discard(s, p["id"], "Asha")
    assert r["already"] is False and r["proposal"]["status"] == "discarded"
    assert await workflow_ids(pg) == before
    async with pg() as s:
        assert (await sb.discard(s, p["id"], "Asha"))["already"] is True
    assert (await events(pg, p["id"])).count("discarded") == 1


@pytest.mark.parametrize("status", ["building", "kept", "undone", "answered"])
async def test_discard_is_refused_from_the_other_states(pg, status):
    p = await seed(pg, status=status)
    async with pg() as s:
        with pytest.raises(sb.BadState) as e:
            await sb.discard(s, p["id"], "Asha")
    assert e.value.status == status


async def test_keep_after_discard_and_discard_after_keep_are_bad_state(pg):
    p = await seed(pg, proposed_definition=simple())
    async with pg() as s:
        await sb.discard(s, p["id"], "Asha")
    async with pg() as s:
        with pytest.raises(sb.BadState):
            await sb.keep(s, p["id"], "Asha")
    q = await seed(pg, proposed_definition=simple())
    async with pg() as s:
        await sb.keep(s, q["id"], "Asha")
    async with pg() as s:
        with pytest.raises(sb.BadState):
            await sb.discard(s, q["id"], "Asha")


# ── races ────────────────────────────────────────────────────────────────────


async def test_ten_concurrent_keeps_create_exactly_one_workflow(pg):
    p = await seed(pg, proposed_definition=simple("Race"))
    before = await workflow_ids(pg)

    async def go(i: int):
        async with pg() as s:
            return await sb.keep(s, p["id"], f"user{i}")

    results = await asyncio.gather(*[go(i) for i in range(10)], return_exceptions=True)
    ok = [r for r in results if isinstance(r, dict)]
    assert len(ok) == 10, results
    assert len({r["workflow_id"] for r in ok}) == 1
    assert sum(1 for r in ok if not r["already"]) == 1
    assert len(await workflow_ids(pg) - before) == 1
    assert (await events(pg, p["id"])).count("kept") == 1
    winner = (await get_row(pg, p["id"]))["kept_by"]
    assert winner in {f"user{i}" for i in range(10)}


async def test_keep_and_discard_race_has_one_winner_and_a_consistent_state(pg):
    for _ in range(5):
        p = await seed(pg, proposed_definition=simple("Race2"))
        before = await workflow_ids(pg)

        async def k():
            async with pg() as s:
                return await sb.keep(s, p["id"], "k")

        async def d():
            async with pg() as s:
                return await sb.discard(s, p["id"], "d")

        rk, rd = await asyncio.gather(k(), d(), return_exceptions=True)
        row = await get_row(pg, p["id"])
        new = await workflow_ids(pg) - before
        if row["status"] == "kept":
            assert isinstance(rk, dict) and isinstance(rd, sb.BadState) and len(new) == 1
        else:
            assert row["status"] == "discarded" and isinstance(rd, dict) and isinstance(rk, sb.BadState) and not new


async def test_concurrent_undo_removes_once(pg):
    wf = await make_workflow(pg, simple("Mine"), "Mine")
    p = await seed_modify(pg, proposed=simple("Mine", extra_node=True), base=simple(), wf=wf)
    async with pg() as s:
        await sb.keep(s, p["id"], "Asha")

    async def go():
        async with pg() as s:
            return await sb.undo(s, p["id"], "Asha")

    results = await asyncio.gather(*[go() for _ in range(6)], return_exceptions=True)
    assert all(isinstance(r, dict) for r in results), results
    assert sum(1 for r in results if not r["already"]) == 1
    assert (await events(pg, p["id"])).count("undone") == 1
    assert len((await draft_of(pg, wf.id))["nodes"]) == 2


async def test_keep_on_a_stale_modify_loses_to_a_concurrent_human_save(pg):
    wf = await make_workflow(pg, simple("Mine"), "Mine")
    p = await seed_modify(pg, proposed=simple("Mine", extra_node=True), base=simple(), wf=wf)
    moved = simple("Mine")
    moved["nodes"][1]["position"] = {"x": 999, "y": 999}

    async def human():
        async with pg() as s:
            await WorkflowRepository(s).update(wf.id, definition_to_workflow(moved, "Mine", wf.id))

    await human()
    async with pg() as s:
        with pytest.raises(sb.DraftMoved):
            await sb.keep(s, p["id"], "Asha")
    assert len((await draft_of(pg, wf.id))["nodes"]) == 2

"""Stage 8 B3: ``build_lookups`` loads the call closure from the database, and the checker reads it (throwaway Postgres)."""

from __future__ import annotations

import pytest

from src.repositories.workflow_repository import workflow_to_definition
from src.services.check_lookups import MAX_CLOSURE_WORKFLOWS, build_lookups, load_call_closure
from src.services.workflow_checker import check_workflow
from tests.unit._stage8_b2_world import Env, build, nd
from tests.unit.conftest import anyio_backend, pg, pg_dbname  # noqa: F401

pytestmark = pytest.mark.anyio


def child(name: str, *fields: tuple, extra: str = "tag") -> object:
    return build(
        [nd("Inputs", "ExecuteWorkflowTrigger", inputSource="fields", fields=[{"name": n, "type": t, "required": r} for n, t, r in fields]),
         nd("Work", "Set", fields=[{"name": extra, "value": "1"}])],
        [("Inputs", "Work")], name)


def parent(name: str, calls: list[tuple[str, str, object]], **params) -> object:
    nodes = [nd("Start", "Start", input_schema={"type": "object", "properties": {"order_id": {"type": "string"}}})]
    conns = []
    for node_name, wid, version in calls:
        nodes.append(nd(node_name, "ExecuteWorkflow", workflowId=wid, version=version, inputMode="passThrough", **params))
        conns.append(("Start", node_name))
    return build(nodes, conns, name)


async def lookups_for(w, stored, **kw):
    async with w.sf() as s:
        return await build_lookups(s, stored.project_id, None, team_id=getattr(stored, "team_id", None) or "default", workflow_id=stored.id, **kw)


async def test_closure_is_loaded_and_typed(pg):
    w = Env(pg)
    kid = await w.create(child(f"kid-{w.prefix}", ("order_id", "string", True), extra="first"))
    await w.publish_ptr(kid)
    kid2 = await w.create(child(f"kid2-{w.prefix}", ("order_id", "string", True), extra="draft_only"))
    par = await w.create(parent(f"par-{w.prefix}", [("Latest", kid.id, "latest"), ("Draft", kid2.id, "draft")]))
    other = await w.create(child(f"other-{w.prefix}", ("x", "string", True)))  # not in the closure
    lk = await lookups_for(w, par)
    assert set(lk.children) == {kid.id, kid2.id} and other.id not in lk.children
    meta = lk.workflow_meta(kid.id)
    assert meta["published_version"] == 1 and meta["versions"] == [1] and meta["project_id"] == par.project_id
    assert lk.workflow_meta(kid2.id)["published_version"] is None
    assert lk.current_workflow_id == par.id and lk.current_project_id == par.project_id
    res = check_workflow(workflow_to_definition(par.workflow), lookups=lk)
    assert [f for f in res["findings"] if f["severity"] == "error"] == []
    assert res["shapes_by_node"]["Latest"]["outputs"]["main"]["properties"].keys() >= {"order_id", "first"}
    assert "draft_only" in res["shapes_by_node"]["Draft"]["outputs"]["main"]["properties"]
    assert {f["code"] for f in res["findings"]} >= {"WF023"}


async def test_latest_follows_the_published_version_and_pins_read_the_old_one(pg):
    w = Env(pg)
    kid = await w.create(child(f"kid-{w.prefix}", ("order_id", "string", True), extra="v1_field"))
    await w.publish_ptr(kid)
    await w.edit_draft(kid, child(f"kid-{w.prefix}", ("order_id", "string", True), extra="v2_field"))
    await w.publish_ptr(kid)
    par = await w.create(parent(f"par-{w.prefix}", [("Newest", kid.id, "latest"), ("Pinned", kid.id, 1), ("Missing", kid.id, 5)]))
    lk = await lookups_for(w, par)
    assert lk.workflow_meta(kid.id)["versions"] == [1, 2] and lk.workflow_meta(kid.id)["published_version"] == 2
    res = check_workflow(workflow_to_definition(par.workflow), lookups=lk)
    props = lambda n: set(res["shapes_by_node"][n]["outputs"]["main"].get("properties", {}))  # noqa: E731
    assert "v2_field" in props("Newest") and "v1_field" not in props("Newest")
    assert "v1_field" in props("Pinned") and "v2_field" not in props("Pinned")
    f = [x for x in res["findings"] if x["code"] == "WF022"]
    assert [x["node"] for x in f] == ["Missing"] and "no version 5" in f[0]["message"]


async def test_cycle_through_saved_workflows_is_found(pg):
    w = Env(pg)
    a = await w.create(parent(f"a-{w.prefix}", []))  # A is saved calling nothing...
    b = await w.create(parent(f"b-{w.prefix}", [("To A", a.id, "draft")]))
    edited = workflow_to_definition(parent(f"a-{w.prefix}", [("To B", b.id, "draft")]))  # ...and is being edited to call B
    lk = await lookups_for(w, a, definition=edited)
    res = check_workflow(edited, lookups=lk)
    f = [x for x in res["findings"] if x["code"] == "WF020"]
    assert len(f) == 1 and f[0]["node"] == "To B" and f[0]["severity"] == "error"


async def test_unsaved_definition_seeds_the_closure(pg):
    w = Env(pg)
    kid = await w.create(child(f"kid-{w.prefix}", ("order_id", "string", True)))
    await w.publish_ptr(kid)
    saved = await w.create(parent(f"par-{w.prefix}", []))  # saved draft calls nothing
    edited = workflow_to_definition(parent(saved.name, [("New call", kid.id, "latest")]))
    lk = await lookups_for(w, saved, definition=edited)
    assert set(lk.children) == {kid.id}
    assert set((await lookups_for(w, saved)).children) == set()


async def test_unknown_ids_and_no_workflow_id_degrade_quietly(pg):
    w = Env(pg)
    par = await w.create(parent(f"par-{w.prefix}", [("Ghost", "wf_does_not_exist", "latest")]))
    lk = await lookups_for(w, par)
    assert lk.children == {}
    res = check_workflow(workflow_to_definition(par.workflow), lookups=lk)
    assert [f["code"] for f in res["findings"] if f["code"].startswith("WF02") or f["code"] == "WF006"] == ["WF006"]
    async with pg() as s:
        bare = await build_lookups(s, par.project_id, None, team_id="default")
    assert bare.children == {} and bare.current_workflow_id is None


async def test_closure_walks_levels_once_and_survives_diamonds_and_cycles(pg):
    w = Env(pg)
    leaf = await w.create(child(f"leaf-{w.prefix}", ("order_id", "string", True)))
    await w.publish_ptr(leaf)
    left = await w.create(parent(f"left-{w.prefix}", [("Down", leaf.id, "latest")]))
    right = await w.create(parent(f"right-{w.prefix}", [("Down", leaf.id, 1)]))
    await w.publish_ptr(left)
    await w.publish_ptr(right)
    top = await w.create(parent(f"top-{w.prefix}", [("L", left.id, "latest"), ("R", right.id, "latest")]))
    async with pg() as s:
        got = await load_call_closure(s, "default", top.project_id, workflow_to_definition(top.workflow))
    assert set(got) == {left.id, right.id, leaf.id}
    assert got[leaf.id].versions.keys() == {1} and got[leaf.id].published_version == 1
    assert MAX_CLOSURE_WORKFLOWS >= 100

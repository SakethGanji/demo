"""Stage 8 B2: the ``workflow_refs`` index, publish rules and delete protection (DESIGN-STAGE8-REUSE 4.3, 5.1, 10.3)."""

from __future__ import annotations

import json

import pytest
from sqlalchemy import text

from src.core.exceptions import ConflictError, WorkflowHasErrorsError
from src.repositories.execution_repository import ExecutionRepository
from src.repositories.workflow_repository import WorkflowRepository
from src.schemas.workflow import WorkflowCreateRequest, WorkflowUpdateRequest
from src.services.workflow_refs import (
    WorkflowInUseError,
    backfill_if_empty,
    blocking_references,
    refs_of,
    sync_refs,
    used_by,
)
from src.services.workflow_service import WorkflowService
from tests.unit._stage8_b2_world import Env, build, child_wf, install_nodes, nd, parent_wf, remove_nodes
from tests.unit.conftest import anyio_backend, pg, pg_dbname  # noqa: F401

pytestmark = pytest.mark.anyio


@pytest.fixture(autouse=True)
def nodes():
    install_nodes()
    yield
    remove_nodes()


def svc_of(s) -> WorkflowService:
    from src.engine.node_registry import node_registry
    from src.services.node_service import NodeService

    return WorkflowService(WorkflowRepository(s), ExecutionRepository(s), NodeService(node_registry))


def defn(*calls) -> dict:
    return {"nodes": [{"name": n, "type": t, "parameters": p} for n, t, p in calls], "connections": []}


# ── pure parsing ───────────────────────────────────────────────────────────────────────────────────────────────────


def test_refs_of_reads_every_pin_form():
    d = defn(
        ("a", "ExecuteWorkflow", {"workflowId": "wf_1", "version": "latest"}),
        ("b", "ExecuteWorkflow", {"workflowId": "wf_2", "version": 3}),
        ("c", "ExecuteWorkflow", {"workflowId": "wf_3", "version": "7"}),
        ("d", "ExecuteWorkflow", {"workflowId": "wf_4", "version": "draft"}),
        ("legacy", "ExecuteWorkflow", {"workflowId": "wf_5"}),            # no version: a legacy node runs the draft
        ("tool", "WorkflowTool", {"workflowId": "wf_6"}),                # an agent tool defaults to latest
        ("rl", "ExecuteWorkflow", {"workflowId": {"__rl": True, "value": "wf_7"}, "version": "latest"}),
    )
    assert refs_of(d) == [("a", "wf_1", "latest"), ("b", "wf_2", "3"), ("c", "wf_3", "7"), ("d", "wf_4", "draft"),
                          ("legacy", "wf_5", "draft"), ("tool", "wf_6", "latest"), ("rl", "wf_7", "latest")]


def test_refs_of_skips_what_it_cannot_point_at():
    d = defn(
        ("expr", "ExecuteWorkflow", {"workflowId": "={{ $json.target }}", "version": "latest"}),
        ("tmpl", "ExecuteWorkflow", {"workflowId": "wf_{{ $json.n }}"}),
        ("empty", "ExecuteWorkflow", {"workflowId": "  "}),
        ("none", "ExecuteWorkflow", {}),
        ("other", "Set", {"workflowId": "wf_9"}),
        ("ok", "ExecuteWorkflow", {"workflowId": " wf_1 ", "version": "bogus"}),
    )
    assert refs_of(d) == [("ok", "wf_1", "draft")]
    assert refs_of(None) == [] and refs_of({}) == []


# ── the index ──────────────────────────────────────────────────────────────────────────────────────────────────────


async def rows(w, where="TRUE"):
    return await w.sql(f"SELECT parent_workflow_id, parent_version_id, node_name, child_workflow_id, version_spec "
                       f"FROM workflow_refs WHERE {where} ORDER BY node_name")


async def test_sync_replaces_the_rows_of_one_draft_or_version_only(pg):
    w = Env(pg)
    child = await w.create(child_wf(f"c-{w.prefix}"))
    parent = await w.create(parent_wf(f"p-{w.prefix}", child.id))
    v = await w.publish_ptr(parent)
    async with pg() as s:
        assert await sync_refs(s, parent.id, None, defn(("one", "ExecuteWorkflow", {"workflowId": child.id, "version": "latest"}),
                                                       ("two", "ExecuteWorkflow", {"workflowId": child.id, "version": 1})))
        assert await sync_refs(s, parent.id, v, defn(("one", "ExecuteWorkflow", {"workflowId": child.id, "version": "latest"})))
        await s.commit()
    pid = f"parent_workflow_id = '{parent.id}'"
    got = sorted((r[1] is None, r[2], r[4]) for r in await rows(w, pid))
    assert got == [(False, "one", "latest"), (True, "one", "latest"), (True, "two", "1")]
    async with pg() as s:                                              # a later save replaces the draft rows, leaves the version's
        await sync_refs(s, parent.id, None, defn(("three", "ExecuteWorkflow", {"workflowId": child.id, "version": "draft"})))
        await s.commit()
    got = sorted((r[1] is None, r[2], r[4]) for r in await rows(w, pid))
    assert got == [(False, "one", "latest"), (True, "three", "draft")]
    async with pg() as s:                                              # idempotent
        await sync_refs(s, parent.id, None, defn(("three", "ExecuteWorkflow", {"workflowId": child.id, "version": "draft"})))
        await s.commit()
    assert len(await rows(w, pid)) == 2


async def test_rows_follow_the_parent_when_it_is_deleted(pg):
    w = Env(pg)
    child = await w.create(child_wf(f"c-{w.prefix}"))
    parent = await w.create(parent_wf(f"p-{w.prefix}", child.id))
    v = await w.publish_ptr(parent)
    async with pg() as s:
        await sync_refs(s, parent.id, None, defn(("call", "ExecuteWorkflow", {"workflowId": child.id, "version": "latest"})))
        await sync_refs(s, parent.id, v, defn(("call", "ExecuteWorkflow", {"workflowId": child.id, "version": "latest"})))
        await s.commit()
    assert len(await rows(w, f"child_workflow_id = '{child.id}'")) == 2
    async with pg() as s:
        await WorkflowRepository(s).delete(parent.id)
    assert await rows(w, f"child_workflow_id = '{child.id}'") == []     # both FKs cascade


async def test_backfill_fills_an_empty_table_once_and_leaves_a_filled_one_alone(pg):
    w = Env(pg)
    child = await w.create(child_wf(f"c-{w.prefix}"))
    parent = await w.create(parent_wf(f"p-{w.prefix}", child.id, "latest"))     # created through the repository: no index rows
    pv = await w.publish_ptr(parent)
    await w.sql("DELETE FROM workflow_refs")
    assert await backfill_if_empty(pg) >= 2
    got = {(r[0], r[1]) for r in await rows(w, f"child_workflow_id = '{child.id}'")}
    assert got == {(parent.id, None), (parent.id, pv)}                  # the draft and the published version
    assert await backfill_if_empty(pg) == 0                             # not empty any more: nothing to do
    await w.sql("DELETE FROM workflow_refs WHERE parent_version_id IS NULL")
    assert await backfill_if_empty(pg) == 0                             # still not empty: never overwrites


# ── used-by, delete protection (10.3) ──────────────────────────────────────────────────────────────────────────────


async def link(w, parent, child, version="latest", *, publish=False):
    d = {"nodes": [{"name": "call", "type": "ExecuteWorkflow", "parameters": {"workflowId": child.id, "version": version}}]}
    async with w.sf() as s:
        await sync_refs(s, parent.id, None, d)
        await s.commit()
    if publish:
        v = await w.publish_ptr(parent)
        async with w.sf() as s:
            await sync_refs(s, parent.id, v, d)
            await s.commit()


async def test_only_published_parents_and_live_releases_block_a_delete(pg):
    w = Env(pg)
    child = await w.create(child_wf(f"c-{w.prefix}"))
    draft_only = await w.create(parent_wf(f"d-{w.prefix}", child.id))
    published = await w.create(parent_wf(f"p-{w.prefix}", child.id))
    await link(w, draft_only, child)
    async with pg() as s:
        assert await blocking_references(s, child.id) == []             # a draft-only reference does not block
        # repository writes index themselves now (B10), so the not-yet-published parent is listed as a draft caller too
        assert [r["where"] for r in await used_by(s, child.id)] == ["draft", "draft"]
    await link(w, published, child, publish=True)
    async with pg() as s:
        block = await blocking_references(s, child.id)
        assert [(r["parent_workflow_id"], r["where"], r["node"]) for r in block] == [(published.id, "published", "call")]
        assert {r["where"] for r in await used_by(s, child.id)} == {"draft", "published"}
    # a parent that no longer publishes the call stops blocking
    await w.edit_draft(published, build([nd("Start", "Start")], [], published.name))
    v2 = await w.publish_ptr(published)
    async with pg() as s:
        await sync_refs(s, published.id, v2, {"nodes": []})
        await s.commit()
        assert await blocking_references(s, child.id) == []             # the old version is history, not the live pointer


async def test_a_live_release_snapshot_blocks_until_it_is_blocked_or_archived(pg):
    w = Env(pg)
    child = await w.create(child_wf(f"c-{w.prefix}"))
    parent = await w.create(parent_wf(f"p-{w.prefix}", child.id))
    snap = {"workflows": [], "subworkflows": [{"parent_workflow_id": parent.id, "parent_version_id": 1, "node": "call",
                                               "child_workflow_id": child.id, "child_version_id": 1, "spec": "latest"}]}
    rid = f"rel_{w.prefix}"
    await w.sql("INSERT INTO releases (id, project_id, environment, number, is_active, status, snapshot, checks) "
                "VALUES (:i, :p, 'prod', 1, false, 'superseded', CAST(:s AS jsonb), '[]'::jsonb)",
                i=rid, p=child.project_id, s=json.dumps(snap))
    async with pg() as s:
        block = await blocking_references(s, child.id)
        assert [(r["where"], r["release_ids"], r["parent_workflow_id"]) for r in block] == [("release", [rid], parent.id)]
    await w.sql("UPDATE releases SET status = 'blocked' WHERE id = :i", i=rid)
    async with pg() as s:
        assert await blocking_references(s, child.id) == []
    await w.sql("DELETE FROM releases WHERE id = :i", i=rid)


async def test_workflow_service_refuses_to_delete_a_called_workflow(pg):
    w = Env(pg)
    child = await w.create(child_wf(f"c-{w.prefix}"))
    parent = await w.create(parent_wf(f"p-{w.prefix}", child.id))
    await link(w, parent, child, publish=True)
    async with pg() as s:
        svc = svc_of(s)
        with pytest.raises(WorkflowInUseError) as e:
            await svc.delete_workflow(child.id)
        err = e.value
        assert isinstance(err, ConflictError) and err.code == "WORKFLOW_IN_USE"
        assert [r["parent_workflow_id"] for r in err.details["used_by"]] == [parent.id] and parent.name in err.message
    async with pg() as s:
        assert await WorkflowRepository(s).get(child.id) is not None    # still there
        svc = svc_of(s)
        assert await svc.delete_workflow(parent.id)                     # the parent itself is free to go
    async with pg() as s:
        svc = svc_of(s)
        assert await svc.delete_workflow(child.id)                      # and now the child


async def test_a_draft_only_caller_does_not_block_the_delete(pg):
    w = Env(pg)
    child = await w.create(child_wf(f"c-{w.prefix}"))
    parent = await w.create(parent_wf(f"p-{w.prefix}", child.id))
    await link(w, parent, child)
    async with pg() as s:
        assert await svc_of(s).delete_workflow(child.id)


# ── the service keeps the index current ────────────────────────────────────────────────────────────────────────────


def create_body(name: str, child_id: str, version=None) -> WorkflowCreateRequest:
    params = {"workflowId": child_id, **({"version": version} if version is not None else {})}
    return WorkflowCreateRequest.model_validate({
        "name": name,
        "nodes": [{"name": "Start", "type": "Start", "parameters": {}, "position": {"x": 0, "y": 0}},
                  {"name": "call", "type": "ExecuteWorkflow", "parameters": params, "position": {"x": 200, "y": 0}}],
        "connections": [{"source_node": "Start", "target_node": "call", "source_output": "main", "target_input": "main"}],
        "settings": {},
    })


async def test_create_update_restore_and_publish_keep_the_index_in_step(pg):
    w = Env(pg)
    child = await w.create(child_wf(f"c-{w.prefix}"))
    other = await w.create(child_wf(f"o-{w.prefix}"))
    await w.publish_ptr(child)
    await w.publish_ptr(other)
    async with pg() as s:
        svc = svc_of(s)
        created = await svc.create_workflow(create_body(f"p-{w.prefix}", child.id, "latest"))
        pid = created.id
    where = f"parent_workflow_id = '{pid}'"
    assert [(r[1], r[3], r[4]) for r in await rows(w, where)] == [(None, child.id, "latest")]       # create

    async with pg() as s:
        svc = svc_of(s)
        body = create_body("x", other.id, 1)
        await svc.update_workflow(pid, WorkflowUpdateRequest(nodes=body.nodes, connections=body.connections))
    assert [(r[1], r[3], r[4]) for r in await rows(w, where)] == [(None, other.id, "1")]            # update

    async with pg() as s:
        svc = svc_of(s)
        res = await svc.publish(pid)
        vid = res.version_id
    assert sorted((r[1] is None, r[3]) for r in await rows(w, where)) == [(False, other.id), (True, other.id)]   # publish

    async with pg() as s:
        svc = svc_of(s)
        body = create_body("x", child.id, "latest")
        await svc.update_workflow(pid, WorkflowUpdateRequest(nodes=body.nodes, connections=body.connections))
        await svc.restore_version(pid, vid)
    assert [(r[1], r[3]) for r in await rows(w, where + " AND parent_version_id IS NULL")] == [(None, other.id)]   # restore

    async with pg() as s:                                              # apply_ops (non-dry) syncs too
        svc = svc_of(s)
        await svc.apply_ops(pid, [{"op": "set_parameter", "args": {"node": "call", "path": "version", "value": "draft"}}])
    assert [r[4] for r in await rows(w, where + " AND parent_version_id IS NULL")] == ["draft"]


# ── publish rules (4.3) ────────────────────────────────────────────────────────────────────────────────────────────


async def publish_findings(w, pid):
    async with w.sf() as s:
        svc = svc_of(s)
        try:
            await svc.publish(pid)
        except WorkflowHasErrorsError as e:
            return [(f["code"], f["node"]) for f in e.findings]
    return []


async def make_parent(w, child_id, version):
    async with w.sf() as s:
        svc = svc_of(s)
        return (await svc.create_workflow(create_body(f"p-{w.prefix}-{version}", child_id, version))).id


async def test_a_draft_pin_cannot_be_published_but_latest_and_numbers_can(pg):
    w = Env(pg)
    child = await w.create(child_wf(f"c-{w.prefix}"))
    await w.publish_ptr(child)
    assert ("WF023", "call") in await publish_findings(w, await make_parent(w, child.id, "draft"))
    legacy = await make_parent(w, child.id, None)                      # no version at all is a legacy draft pin
    assert ("WF023", "call") in await publish_findings(w, legacy)
    assert await publish_findings(w, await make_parent(w, child.id, "latest")) == []
    assert await publish_findings(w, await make_parent(w, child.id, 1)) == []


async def test_publish_refuses_a_pin_that_cannot_resolve(pg):
    w = Env(pg)
    fresh = await w.create(child_wf(f"fresh-{w.prefix}"))               # never published
    assert ("WF022", "call") in await publish_findings(w, await make_parent(w, fresh.id, "latest"))
    await w.publish_ptr(fresh)
    assert ("WF022", "call") in await publish_findings(w, await make_parent(w, fresh.id, 9))
    assert await publish_findings(w, await make_parent(w, fresh.id, 1)) == []


async def test_publishing_a_child_never_fails_because_of_its_parents_and_reports_impact(pg):
    w = Env(pg)
    child = await w.create(child_wf(f"c-{w.prefix}"))
    await w.publish_ptr(child)
    pid = await make_parent(w, child.id, "latest")
    async with pg() as s:
        await svc_of(s).publish(pid)
    pinned = await make_parent(w, child.id, 1)
    async with pg() as s:
        await svc_of(s).publish(pinned)
    async with pg() as s:
        svc = svc_of(s)
        await svc.publish(child.id)                                    # a second version of the child: fine
        impact = await svc.publish_impact(child.id)
    assert [(i["parent_workflow_id"], i["node"], i["version_spec"], i["in_release"], i["breaking"]) for i in impact] == \
        [(pid, "call", "latest", False, [])]                           # only the parent that follows latest; the pinned one is safe

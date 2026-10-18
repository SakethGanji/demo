"""Stage 8 B2: ``DbSubworkflowInvoker`` (DESIGN-STAGE8-REUSE 3.3-3.5) and its wiring into ``ExecutionService``, on Postgres."""

from __future__ import annotations

import pytest

from src.engine.node_registry import node_registry
from src.engine.subworkflow import (
    APPROVAL_IN_SUBWORKFLOW,
    RECURSION_LIMIT,
    SUBWORKFLOW_CYCLE,
    SUBWORKFLOW_NOT_CALLABLE,
    SUBWORKFLOW_NOT_FOUND,
    SUBWORKFLOW_TIMEOUT,
    SUBWORKFLOW_VERSION_MISSING,
)
from src.engine.test_mode import TestRunOptions
from src.engine.types import ExecutionEvent, NodeData
from tests.unit._stage8_b2_world import PROBES, Env, build, child_wf, install_nodes, nd, parent_wf, remove_nodes
from tests.unit.conftest import anyio_backend, pg, pg_dbname  # noqa: F401

pytestmark = pytest.mark.anyio


@pytest.fixture(autouse=True)
def nodes():
    install_nodes()
    yield
    remove_nodes()


async def setup(pg, child=None, version="latest", boom=False):
    w = Env(pg)
    child_def = child or child_wf(f"child-{w.prefix}", "v1")
    child_def.name = f"child-{w.prefix}"
    c = await w.create(child_def)
    v = await w.publish_ptr(c)
    p = await w.create(parent_wf(f"parent-{w.prefix}", c.id, version, boom=boom))
    return w, c, p, v


# ── the child row ──────────────────────────────────────────────────────────────────────────────────────────────────


async def test_child_row_is_committed_with_lineage_even_though_the_parent_fails(pg):
    w, c, p, v = await setup(pg, boom=True)
    stored, ctx = await w.run(p.id)
    assert ctx.errors and ctx.errors[0].node_name == "after"      # the parent failed after the child had finished
    parent_row = await w.execution(ctx.execution_id)
    assert parent_row["status"] == "failed" and parent_row["parent_execution_id"] is None

    [child] = await w.rows_of(c.id)
    assert child["mode"] == "sub_workflow" and child["status"] == "success"
    assert child["parent_execution_id"] == ctx.execution_id and child["parent_node_name"] == "call"
    assert child["depth"] == 1 and child["root_execution_id"] == ctx.execution_id
    assert child["workflow_version_id"] == v and child["project_id"] == p.project_id
    assert child["metadata"]["trigger"]["type"] == "sub_workflow"
    assert child["metadata"]["subworkflow"]["version_number"] == 1
    outs = await w.outputs(child["id"])                            # the child's node outputs were persisted too
    assert set(n for n, _ in outs) >= {"Inputs", "work"}


async def test_the_child_row_survives_a_rollback_of_the_parent_session(pg):
    w, c, p, v = await setup(pg)
    ctx = await w.parent_ctx(p)
    res = await w.invoker().invoke(w.call(ctx, c.id))
    assert res.status == "success" and res.child_version_number == 1
    assert [i.json["tag"] for i in res.items] == ["v1"]
    async with pg() as parent_session:                              # a parent whose own transaction is thrown away
        await parent_session.rollback()
    [row] = await w.rows_of(c.id)
    assert row["id"] == res.child_execution_id and row["status"] == "success"


async def test_a_pinned_number_runs_that_version_and_records_it(pg):
    w, c, p, v1 = await setup(pg)
    await w.edit_draft(c, child_wf(c.name, "v2"))
    v2 = await w.publish_ptr(c)
    ctx = await w.parent_ctx(p)
    old = await w.invoker().invoke(w.call(ctx, c.id, 1))
    new = await w.invoker().invoke(w.call(ctx, c.id, "latest"))
    assert [i.json["tag"] for i in old.items] == ["v1"] and old.child_version_number == 1
    assert [i.json["tag"] for i in new.items] == ["v2"] and new.child_version_number == 2
    rows = {r["id"]: r for r in await w.rows_of(c.id)}
    assert rows[old.child_execution_id]["workflow_version_id"] == v1
    assert rows[new.child_execution_id]["workflow_version_id"] == v2
    assert [rows[i]["metadata"]["subworkflow"]["call_index"] for i in (old.child_execution_id, new.child_execution_id)] == [1, 2]


async def test_a_draft_call_records_no_version(pg):
    w, c, p, v = await setup(pg)
    await w.edit_draft(c, child_wf(c.name, "wip"))
    ctx = await w.parent_ctx(p)
    res = await w.invoker().invoke(w.call(ctx, c.id, "draft"))
    assert [i.json["tag"] for i in res.items] == ["wip"] and res.child_version_number is None
    [row] = await w.rows_of(c.id)
    assert row["workflow_version_id"] is None


async def test_a_failing_child_is_a_failed_result_with_a_failed_row(pg):
    w, c, p, v = await setup(pg, child=child_wf("x", middle=nd("kaboom", "TestBoom")))
    ctx = await w.parent_ctx(p)
    res = await w.invoker().invoke(w.call(ctx, c.id))
    assert res.status == "failed" and res.error is not None and res.error.node_name == "kaboom"
    [row] = await w.rows_of(c.id)
    assert row["status"] == "failed" and row["id"] == res.child_execution_id and row["error_count"] == 1


async def test_test_runs_save_their_children_as_test_rows_and_unsaved_runs_save_nothing(pg):
    w, c, p, v1 = await setup(pg)
    await w.edit_draft(c, child_wf(c.name, "v2"))
    await w.publish_ptr(c)
    saved = await w.parent_ctx(p, mode="test", test_options=TestRunOptions(persist=True))
    res = await w.invoker().invoke(w.call(saved, c.id))
    [row] = await w.rows_of(c.id)
    assert row["mode"] == "test" and row["id"] == res.child_execution_id and row["parent_execution_id"] == saved.execution_id

    unsaved = await w.parent_ctx(p, mode="test", row=False, test_options=TestRunOptions(persist=False))
    res = await w.invoker().invoke(w.call(unsaved, c.id, 1))     # versions still resolve
    assert res.status == "success" and res.child_execution_id is None and res.child_version_number == 1
    assert [i.json["tag"] for i in res.items] == ["v1"]
    assert len(await w.rows_of(c.id)) == 1                         # nothing new was written


async def test_a_parent_without_a_row_gives_an_unlinked_child_instead_of_a_foreign_key_error(pg):
    w, c, p, v = await setup(pg)
    ctx = await w.parent_ctx(p, row=False)                         # e.g. cron: the row is only created at completion
    res = await w.invoker().invoke(w.call(ctx, c.id))
    assert res.status == "success"
    [row] = await w.rows_of(c.id)
    assert row["parent_execution_id"] is None and row["root_execution_id"] == ctx.execution_id


# ── guards (3.4): in order, before any row ─────────────────────────────────────────────────────────────────────────


async def codes(w, ctx, child_id, version="latest"):
    res = await w.invoker().invoke(w.call(ctx, child_id, version))
    assert res.status == "failed" and res.child_execution_id is None
    return res.error.error_code, res.error.error


async def test_every_guard_refuses_before_a_row_exists(pg):
    w, c, p, v = await setup(pg)
    ctx = await w.parent_ctx(p)
    assert (await codes(w, ctx, "wf_nope"))[0] == SUBWORKFLOW_NOT_FOUND
    assert (await codes(w, await w.parent_ctx(p, project_id="proj_else"), c.id))[0] == SUBWORKFLOW_NOT_FOUND
    fresh = await w.create(child_wf(f"fresh-{w.prefix}"))
    code, msg = await codes(w, ctx, fresh.id)
    assert code == SUBWORKFLOW_VERSION_MISSING and msg == f"Sub-workflow '{fresh.name}' has never been published."
    bare = await w.create(child_wf(f"bare-{w.prefix}", trigger=False))
    await w.publish_ptr(bare)
    code, msg = await codes(w, ctx, bare.id)
    assert code == SUBWORKFLOW_NOT_CALLABLE and "no Execute Workflow Trigger" in msg
    assert (await codes(w, await w.parent_ctx(p, depth=10), c.id))[0] == RECURSION_LIMIT
    code, msg = await codes(w, await w.parent_ctx(p, call_stack=(p.id, c.id)), c.id)
    assert code == SUBWORKFLOW_CYCLE and "can't call itself" in msg
    appr = await w.create(child_wf(f"appr-{w.prefix}", middle=nd("review", "Approval")))
    await w.publish_ptr(appr)
    code, msg = await codes(w, ctx, appr.id)
    assert code == APPROVAL_IN_SUBWORKFLOW and "approvals must be in the top-level workflow" in msg
    for wid in (c.id, fresh.id, bare.id, appr.id):
        assert await w.rows_of(wid) == []
    # the approval child's first node never ran: nothing was executed for it


async def test_guards_run_in_the_documented_order(pg):
    w, c, p, v = await setup(pg)
    appr = await w.create(child_wf(f"appr-{w.prefix}", middle=nd("review", "Approval")))
    await w.publish_ptr(appr)
    # depth beats cycle beats approval
    both = await w.parent_ctx(p, depth=10, call_stack=(p.id, appr.id))
    assert (await codes(w, both, appr.id))[0] == RECURSION_LIMIT
    cyc = await w.parent_ctx(p, call_stack=(p.id, appr.id))
    assert (await codes(w, cyc, appr.id))[0] == SUBWORKFLOW_CYCLE
    # version missing beats not-callable
    bare = await w.create(child_wf(f"bare-{w.prefix}", trigger=False))
    assert (await codes(w, await w.parent_ctx(p), bare.id))[0] == SUBWORKFLOW_VERSION_MISSING


# ── timeout (3.5) ──────────────────────────────────────────────────────────────────────────────────────────────────


async def test_timeout_fails_the_child_row_with_cancelled_at(pg):
    w, c, p, v = await setup(pg, child=child_wf("x", middle=nd("slow", "TestSleep", seconds=10)))
    ctx = await w.parent_ctx(p)
    res = await w.invoker().invoke(w.call(ctx, c.id, timeout=0.3))
    assert res.status == "timeout" and res.error.error_code == SUBWORKFLOW_TIMEOUT
    [row] = await w.rows_of(c.id)
    assert row["id"] == res.child_execution_id
    assert row["status"] == "failed" and row["cancelled_at"] is not None and row["end_time"] is not None
    assert row["metadata"]["error_code"] == SUBWORKFLOW_TIMEOUT and "timed out" in row["metadata"]["error"]


async def test_the_parents_deadline_caps_the_childs_timeout(pg):
    import time

    w, c, p, v = await setup(pg, child=child_wf("x", middle=nd("slow", "TestSleep", seconds=10)))
    ctx = await w.parent_ctx(p)
    ctx.deadline = time.monotonic() + 0.3
    t0 = time.monotonic()
    res = await w.invoker().invoke(w.call(ctx, c.id, timeout=300))
    assert res.status == "timeout" and time.monotonic() - t0 < 5


# ── what the child inherits ────────────────────────────────────────────────────────────────────────────────────────


async def test_child_gets_its_own_api_specs_and_shares_variables_and_secrets(pg, monkeypatch):
    seen = []

    async def fake_specs(session, nodes):
        seen.append([n.name for n in nodes])
        return {"spec_child": {"id": "spec_child"}}

    monkeypatch.setattr("src.services.api_catalog.load_api_specs", fake_specs)
    w, c, p, v = await setup(pg, child=child_wf("x", middle=nd("probe", "TestProbe")))
    ctx = await w.parent_ctx(p, extras={"api_specs": {"spec_parent": {}}, "secret_values": {"s3cret"}, "egress_allowlist": ["a.example"]})
    ctx.variables = {"K": "v"}
    res = await w.invoker().invoke(w.call(ctx, c.id))
    assert res.status == "success"
    [probe] = PROBES
    assert probe["api_specs"] == {"spec_child": {"id": "spec_child"}}   # the child's, not the parent's (G6)
    assert ctx.extras["api_specs"] == {"spec_parent": {}}                # and the parent's dict is untouched
    assert probe["variables"] == {"K": "v"} and probe["secret_values"] == {"s3cret"}
    assert probe["depth"] == 1 and probe["call_stack"] == (p.id, c.id) and probe["root"] == ctx.execution_id
    assert seen == [["Inputs", "work", "probe"]]                         # specs were loaded for the child's nodes only


async def test_child_node_events_reach_the_parent_stream_tagged(pg):
    w, c, p, v = await setup(pg)
    ctx = await w.parent_ctx(p)
    events: list[ExecutionEvent] = []
    ctx.on_event = events.append
    await w.invoker().invoke(w.call(ctx, c.id))
    node_events = [e for e in events if e.type.value.startswith("node:")]
    assert node_events and all(e.subworkflow_parent_node == "call" and e.subworkflow_id == c.id for e in node_events)


# ── wiring ─────────────────────────────────────────────────────────────────────────────────────────────────────────


async def test_run_saved_workflow_runs_the_whole_chain_through_the_db_invoker(pg):
    w, c, p, v = await setup(pg)
    stored, ctx = await w.run(p.id)
    assert not ctx.errors
    assert ctx.root_execution_id == ctx.execution_id
    from src.services.subworkflow_service import DbSubworkflowInvoker

    assert isinstance(ctx.subworkflow_invoker, DbSubworkflowInvoker)
    [row] = await w.rows_of(c.id)
    assert row["root_execution_id"] == ctx.execution_id and row["parent_execution_id"] == ctx.execution_id
    assert ctx.node_states["call"][0].json["tag"] == "v1"


async def test_grandchildren_link_to_their_parent_and_share_the_root(pg):
    w = Env(pg)
    leaf = await w.create(child_wf(f"leaf-{w.prefix}", "leaf"))
    await w.publish_ptr(leaf)
    mid_def = build([nd("Inputs", "ExecuteWorkflowTrigger"),
                     nd("down", "ExecuteWorkflow", workflowId=leaf.id, version="latest", inputMode="passThrough")],
                    [("Inputs", "down")], f"mid-{w.prefix}")
    mid = await w.create(mid_def)
    await w.publish_ptr(mid)
    top = await w.create(parent_wf(f"top-{w.prefix}", mid.id))
    _, ctx = await w.run(top.id)
    assert not ctx.errors
    [m] = await w.rows_of(mid.id)
    [l] = await w.rows_of(leaf.id)
    assert (m["depth"], l["depth"]) == (1, 2)
    assert m["parent_execution_id"] == ctx.execution_id and l["parent_execution_id"] == m["id"]
    assert m["root_execution_id"] == l["root_execution_id"] == ctx.execution_id


async def test_a_workflow_calling_itself_fails_at_the_guard_not_at_depth_ten(pg):
    w = Env(pg)
    selfie = await w.create(child_wf(f"self-{w.prefix}", "x"))
    d = build([nd("Inputs", "ExecuteWorkflowTrigger"),
               nd("again", "ExecuteWorkflow", workflowId=selfie.id, version="draft", inputMode="passThrough")],
              [("Inputs", "again")], selfie.name)
    await w.edit_draft(selfie, d)
    top = await w.create(parent_wf(f"top-{w.prefix}", selfie.id, "draft"))
    _, ctx = await w.run(top.id)
    assert ctx.errors
    details = ctx.errors[0].details or {}
    assert details.get("child_error_code") == SUBWORKFLOW_CYCLE or "can't call itself" in str(details.get("child_error"))
    assert len(await w.rows_of(selfie.id)) == 1                    # one call ran; the second was refused before a row


async def test_retry_from_failure_keeps_the_retried_run_as_a_row_children_can_point_at(pg):
    import asyncio

    from src.engine import execution_registry
    from src.repositories.execution_repository import ExecutionRepository
    from src.repositories.workflow_repository import WorkflowRepository
    from src.services.execution_service import ExecutionService

    w, c, p, v = await setup(pg, child=child_wf("x", middle=nd("kaboom", "TestBoom")))
    _, ctx = await w.run(p.id)
    assert ctx.errors
    async with pg() as s:
        new_id = await ExecutionService(ExecutionRepository(s), WorkflowRepository(s)).retry_from_failure(ctx.execution_id)
    task = execution_registry.get_all().get(new_id)
    if task is not None:
        await asyncio.wait_for(task, 30)
    kids = [r for r in await w.rows_of(c.id) if r["parent_execution_id"] == new_id]
    assert len(kids) == 1 and kids[0]["root_execution_id"] == new_id

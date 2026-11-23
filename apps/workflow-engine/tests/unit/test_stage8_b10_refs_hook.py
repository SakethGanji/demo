"""Stage 8 B10: every draft write refreshes the ``workflow_refs`` index ("used by"), in the write's own transaction.

The hook lives in ``WorkflowRepository`` (create / update / set_interface), so a caller can't forget it; the service no longer
calls it a second time. ``restore_version`` writes the model directly and keeps its own call.
"""

from __future__ import annotations

import pytest

from src.engine.node_registry import node_registry
from src.repositories.execution_repository import ExecutionRepository
from src.repositories.workflow_repository import WorkflowRepository, workflow_to_definition
from src.schemas.workflow import WorkflowCreateRequest, WorkflowUpdateRequest
from src.services import workflow_refs
from src.services.node_service import NodeService
from src.services.workflow_service import WorkflowService
from tests.unit._stage8_b2_world import Env, build, child_wf, install_nodes, nd, parent_wf, remove_nodes
from tests.unit.conftest import anyio_backend, pg, pg_dbname  # noqa: F401

pytestmark = pytest.mark.anyio


@pytest.fixture(autouse=True)
def nodes():
    install_nodes()
    yield
    remove_nodes()


def svc(s) -> WorkflowService:
    return WorkflowService(WorkflowRepository(s), ExecutionRepository(s), NodeService(node_registry))


async def used(w: Env, child_id: str) -> list[tuple[str, str, str]]:
    return [(r[0], r[2], r[4]) for r in await w.sql(
        "SELECT parent_workflow_id, parent_version_id, node_name, child_workflow_id, version_spec FROM workflow_refs "
        "WHERE child_workflow_id = :c AND parent_version_id IS NULL ORDER BY parent_workflow_id, node_name", c=child_id)]


def no_call(name: str):
    return build([nd("Start", "Start")], [], name)


async def test_repository_create_indexes_the_new_draft(pg):
    w = Env(pg)
    child = await w.create(child_wf(f"c-{w.prefix}"))
    parent = await w.create(parent_wf(f"p-{w.prefix}", child.id, "latest"))
    assert await used(w, child.id) == [(parent.id, "call", "latest")]


async def test_repository_update_moves_and_removes_the_rows(pg):
    w = Env(pg)
    a = await w.create(child_wf(f"a-{w.prefix}"))
    b = await w.create(child_wf(f"b-{w.prefix}"))
    parent = await w.create(parent_wf(f"p-{w.prefix}", a.id, "latest"))
    await w.edit_draft(parent, parent_wf(parent.name, b.id, 2))
    assert await used(w, a.id) == [] and await used(w, b.id) == [(parent.id, "call", "2")]
    await w.edit_draft(parent, no_call(parent.name))                    # a save that drops the call must clear the row
    assert await used(w, b.id) == []


async def test_a_save_without_calls_before_or_after_costs_no_index_work(pg, monkeypatch):
    w = Env(pg)
    wf = await w.create(no_call(f"plain-{w.prefix}"))
    calls: list = []
    real = workflow_refs.sync_refs

    async def spy(*a, **kw):
        calls.append(a)
        return await real(*a, **kw)

    monkeypatch.setattr(workflow_refs, "sync_refs", spy)
    await w.edit_draft(wf, no_call(wf.name))
    assert calls == []


async def test_set_interface_keeps_the_rows_and_repairs_a_missing_one(pg):
    w = Env(pg)
    child = await w.create(child_wf(f"c-{w.prefix}"))
    parent = await w.create(parent_wf(f"p-{w.prefix}", child.id))
    await w.sql("DELETE FROM workflow_refs")                            # an index that fell behind (e.g. a pre-hook write)
    async with pg() as s:
        await WorkflowRepository(s).set_interface(parent.id, {"kind": "form", "title": "x"})
    assert await used(w, child.id) == [(parent.id, "call", "latest")]


async def test_service_create_update_and_ops_refresh_exactly_once_each(pg, monkeypatch):
    w = Env(pg)
    child = await w.create(child_wf(f"c-{w.prefix}"))
    other = await w.create(child_wf(f"o-{w.prefix}"))
    count = {"n": 0}
    real = workflow_refs.sync_refs

    async def spy(*a, **kw):
        count["n"] += 1
        return await real(*a, **kw)

    monkeypatch.setattr(workflow_refs, "sync_refs", spy)

    def body(target: str) -> WorkflowCreateRequest:
        return WorkflowCreateRequest.model_validate({
            "name": f"svc-{w.prefix}",
            "nodes": [{"name": "Start", "type": "Start", "parameters": {}, "position": {"x": 0, "y": 0}},
                      {"name": "call", "type": "ExecuteWorkflow", "parameters": {"workflowId": target, "version": "latest"},
                       "position": {"x": 200, "y": 0}}],
            "connections": [{"source_node": "Start", "target_node": "call", "source_output": "main", "target_input": "main"}],
            "settings": {},
        })

    async with pg() as s:
        created = await svc(s).create_workflow(body(child.id))
    assert count["n"] == 1 and await used(w, child.id) == [(created.id, "call", "latest")]

    count["n"] = 0
    async with pg() as s:
        b = body(other.id)
        await svc(s).update_workflow(created.id, WorkflowUpdateRequest(nodes=b.nodes, connections=b.connections))
    assert count["n"] == 1
    assert await used(w, child.id) == [] and await used(w, other.id) == [(created.id, "call", "latest")]

    count["n"] = 0
    async with pg() as s:                                               # node mutation ops (the toolkit path)
        await svc(s).apply_ops(created.id, [{"op": "set_parameter", "args": {"node": "call", "path": "workflowId", "value": child.id}}])
    assert count["n"] == 1
    assert await used(w, child.id) == [(created.id, "call", "latest")] and await used(w, other.id) == []


async def test_restore_version_reindexes_the_draft(pg):
    w = Env(pg)
    child = await w.create(child_wf(f"c-{w.prefix}"))
    parent = await w.create(parent_wf(f"p-{w.prefix}", child.id))
    vid = await w.publish(parent)                                       # a version that calls the child
    await w.edit_draft(parent, no_call(parent.name))
    assert await used(w, child.id) == []
    async with pg() as s:
        await svc(s).restore_version(parent.id, vid)
    assert await used(w, child.id) == [(parent.id, "call", "latest")]


async def test_a_failed_write_leaves_no_index_rows_behind(pg):
    """The refresh shares the write's transaction: a rollback after it takes the rows with it."""
    w = Env(pg)
    child = await w.create(child_wf(f"c-{w.prefix}"))
    parent = await w.create(no_call(f"p-{w.prefix}"))
    async with pg() as s:
        repo = WorkflowRepository(s)
        model = await repo.get_model(parent.id)
        model.draft_definition = workflow_to_definition(parent_wf(parent.name, child.id))
        await repo._index_draft(model, {})
        await s.rollback()
    assert await used(w, child.id) == []

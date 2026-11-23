"""Stage 8 B2: execution lineage in ``ExecutionRepository`` (DESIGN-STAGE8-REUSE 3.3, 10.1) on Postgres."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text

from src.repositories.execution_repository import ExecutionRepository
from tests.unit._stage8_b2_world import Env, child_wf, install_nodes, parent_wf, remove_nodes
from tests.unit.conftest import anyio_backend, pg, pg_dbname  # noqa: F401

pytestmark = pytest.mark.anyio


@pytest.fixture(autouse=True)
def nodes():
    install_nodes()
    yield
    remove_nodes()


def eid() -> str:
    return "exec_" + uuid.uuid4().hex[:12]


async def add_output(sf, execution_id: str) -> None:
    async with sf() as s:
        await s.execute(text("INSERT INTO node_outputs (execution_id, node_name, output, status, run_index) "
                             "VALUES (:e, 'n', '[]'::jsonb, 'success', 0)"), {"e": execution_id})
        await s.commit()


async def start(sf, wf, *, parent=None, root=None, depth=0, node=None, version=None, max_records=100, mode="manual") -> str:
    i = eid()
    async with sf() as s:
        await ExecutionRepository(s, max_records=max_records).start(
            i, wf.id, wf.name, mode, project_id=wf.project_id, environment="default",
            parent_execution_id=parent, parent_node_name=node, depth=depth, root_execution_id=root,
            workflow_version_id=version)
    return i


async def exists(sf, ids: list[str]) -> set[str]:
    async with sf() as s:
        r = await s.execute(text("SELECT id FROM executions WHERE id = ANY(:i)"), {"i": ids})
        return {x[0] for x in r.all()}


async def purge_executions(w: Env) -> None:
    await w.sql("DELETE FROM node_outputs")
    await w.sql("DELETE FROM human_tasks")
    await w.sql("DELETE FROM executions")


async def test_start_writes_the_lineage_columns(pg):
    w = Env(pg)
    wf = await w.create(child_wf(f"c-{w.prefix}"))
    root = await start(pg, wf)
    kid = await start(pg, wf, parent=root, root=root, depth=1, node="call", version=None, mode="sub_workflow")
    async with pg() as s:
        row = await s.get(__import__("src.db.models", fromlist=["ExecutionModel"]).ExecutionModel, kid)
    assert (row.parent_execution_id, row.root_execution_id, row.depth, row.parent_node_name) == (root, root, 1, "call")
    assert row.mode == "sub_workflow"
    async with pg() as s:
        lin = await ExecutionRepository(s).lineage(root)
    assert lin["parent"] is None and lin["root_execution_id"] == root and lin["depth"] == 0
    assert [c["execution_id"] for c in lin["children"]] == [kid] and lin["children"][0]["node"] == "call"
    async with pg() as s:
        lin = await ExecutionRepository(s).lineage(kid)
    assert lin["parent"]["execution_id"] == root and lin["parent"]["node"] == "call" and lin["depth"] == 1


async def test_the_parent_link_is_a_real_foreign_key(pg):
    w = Env(pg)
    wf = await w.create(child_wf(f"c-{w.prefix}"))
    with pytest.raises(Exception):
        await start(pg, wf, parent="exec_does_not_exist", depth=1)


async def test_cleanup_counts_roots_only_and_children_never_prune_their_parents(pg):
    w = Env(pg)
    await purge_executions(w)
    wf = await w.create(child_wf(f"c-{w.prefix}"))
    roots = [await start(pg, wf, max_records=3) for _ in range(3)]
    kids = [await start(pg, wf, parent=roots[0], root=roots[0], depth=1, max_records=3, mode="sub_workflow") for _ in range(8)]
    async with pg() as s:
        await ExecutionRepository(s, max_records=3).cleanup()
    assert await exists(pg, roots + kids) == set(roots + kids)          # 3 roots + 8 children: nothing is over the limit
    newest = await start(pg, wf, max_records=3)                         # a 4th root prunes the oldest root...
    gone = await exists(pg, [roots[0]])
    assert gone == set()
    assert await exists(pg, kids) == set()                              # ...and its children go with it
    assert await exists(pg, roots[1:] + [newest]) == set(roots[1:] + [newest])


async def test_a_new_child_does_not_trigger_a_prune(pg):
    w = Env(pg)
    await purge_executions(w)
    wf = await w.create(child_wf(f"c-{w.prefix}"))
    roots = [await start(pg, wf, max_records=2) for _ in range(3)]      # the 3rd start pruned the 1st
    assert await exists(pg, roots) == set(roots[1:])
    await w.sql("UPDATE executions SET start_time = now() - interval '1 day' WHERE id = :i", i=roots[1])
    kid = await start(pg, wf, parent=roots[2], root=roots[2], depth=1, max_records=1, mode="sub_workflow")
    assert await exists(pg, [roots[1], roots[2], kid]) == {roots[1], roots[2], kid}   # max_records=1 would have pruned a root


async def test_deleting_a_root_cascades_through_children_grandchildren_and_node_outputs(pg):
    w = Env(pg)
    wf = await w.create(child_wf(f"c-{w.prefix}"))
    root = await start(pg, wf)
    kid = await start(pg, wf, parent=root, root=root, depth=1, mode="sub_workflow")
    grand = await start(pg, wf, parent=kid, root=root, depth=2, mode="sub_workflow")
    other = await start(pg, wf)
    for i in (root, kid, grand, other):
        await add_output(pg, i)
    async with pg() as s:
        assert await ExecutionRepository(s).delete(root)
    assert await exists(pg, [root, kid, grand, other]) == {other}
    rows = await w.sql("SELECT execution_id FROM node_outputs WHERE execution_id = ANY(:i)", i=[root, kid, grand])
    assert rows == []


async def test_deleting_the_parent_workflow_removes_children_that_live_under_another_workflow(pg):
    from src.repositories.workflow_repository import WorkflowRepository

    w = Env(pg)
    child = await w.create(child_wf(f"child-{w.prefix}"))
    parent = await w.create(parent_wf(f"parent-{w.prefix}", child.id))
    root = await start(pg, parent)
    kid = await start(pg, child, parent=root, root=root, depth=1, mode="sub_workflow")
    await add_output(pg, kid)
    async with pg() as s:
        assert await WorkflowRepository(s).delete(parent.id)
    assert await exists(pg, [root, kid]) == set()


async def test_clear_survives_cascading_children(pg):
    w = Env(pg)
    wf = await w.create(child_wf(f"c-{w.prefix}"))
    root = await start(pg, wf)
    await start(pg, wf, parent=root, root=root, depth=1, mode="sub_workflow")
    async with pg() as s:
        await ExecutionRepository(s).clear()
    assert await w.sql("SELECT 1 FROM executions") == []


async def test_list_hides_children_unless_asked(pg):
    w = Env(pg)
    await purge_executions(w)
    wf = await w.create(child_wf(f"c-{w.prefix}"))
    root = await start(pg, wf)
    kid = await start(pg, wf, parent=root, root=root, depth=1, mode="sub_workflow")
    async with pg() as s:
        repo = ExecutionRepository(s)
        assert [r.id for r in await repo.list(workflow_id=wf.id)] == [root]
        assert {r.id for r in await repo.list(workflow_id=wf.id, include_children=True)} == {root, kid}
        assert [r.id for r in await repo.list(parent_execution_id=root)] == [kid]
        days = await repo.daily_counts(wf.project_id)
    assert sum(d["total"] for d in days) == 1                           # a run is counted once, not once per sub-workflow


async def test_the_migration_shape(pg_dbname):
    db = pg_dbname
    assert "applied    20261021120000   reuse" in db.migrate("status")
    cols = {r[0] for r in db.query("SELECT column_name FROM information_schema.columns WHERE table_schema='workflow-app' "
                                   "AND table_name='executions'")}
    assert "root_execution_id" in cols
    assert db.query("SELECT confdeltype FROM pg_constraint WHERE conname = 'fk_executions_parent'") == [("c",)]  # CASCADE
    tables = {r[0] for r in db.query("SELECT table_name FROM information_schema.tables WHERE table_schema='workflow-app'")}
    assert {"workflow_refs", "template_installs"} <= tables
    idx = {r[0] for r in db.query("SELECT indexname FROM pg_indexes WHERE schemaname='workflow-app'")}
    assert {"ix_executions_root", "ux_workflow_refs_node", "ix_workflow_refs_child", "ix_workflow_refs_project"} <= idx


def test_the_migration_rolls_back_and_applies_again_on_its_own_database():
    from tests.integration.conftest import TestDatabase

    db = TestDatabase()
    try:
        db.create()
    except Exception as e:
        pytest.skip(f"Postgres not reachable: {e}")
    try:
        db.migrate("apply")
        # "rollback" with no target undoes only the newest migration; name the Stage 8 one so later migrations (capability graph) go with it
        db.migrate("rollback", "20261021120000")
        assert db.query("SELECT 1 FROM information_schema.tables WHERE table_schema='workflow-app' AND table_name='workflow_refs'") == []
        assert db.query("SELECT 1 FROM information_schema.columns WHERE table_schema='workflow-app' AND table_name='executions' "
                        "AND column_name='root_execution_id'") == []
        db.query("INSERT INTO executions (id, workflow_id, workflow_name, status, mode, parent_execution_id) "
                 "VALUES ('e_orphan', 'w', 'w', 'success', 'manual', 'e_missing_parent')")   # legal before the FK existed
        db.migrate("apply")
        db.migrate("apply")                                             # idempotent
        assert db.query("SELECT parent_execution_id FROM executions WHERE id = 'e_orphan'") == [(None,)]
        assert db.query("SELECT 1 FROM information_schema.tables WHERE table_schema='workflow-app' AND table_name='workflow_refs'") == [(1,)]
    finally:
        db.drop()

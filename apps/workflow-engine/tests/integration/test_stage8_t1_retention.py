"""Stage 8 T1 (tester): run retention counts roots only, and a pruned root takes every level of its sub-workflow runs with it
(DESIGN-STAGE8-REUSE 3.3, R3). Runs on its own database and engine with ``WORKFLOW_RUN_RETENTION=3`` so that pruning one
root can never touch another test's executions."""

from __future__ import annotations

import pytest

from ._stage8_t1_support import call_node, chain_workflow, failing_child, make, publish, rows_parent, run, uniq
from .conftest import EngineServer, StudioClient, TestDatabase

pytestmark = pytest.mark.integration

KEEP = 3


@pytest.fixture(scope="module")
def world():
    import os

    db = TestDatabase()
    db.name = db.name.replace("workflows_test_", "workflows_test_s8t1ret_", 1) if not os.environ.get("TEST_DB_NAME") else db.name + "_s8t1ret"
    db.create()
    srv = None
    try:
        db.migrate("apply")
        srv = EngineServer(db, extra_env={"WORKFLOW_RUN_RETENTION": str(KEEP)}, label="-s8t1-retention")
        srv.start()
        yield StudioClient(srv.base_url), db
    finally:
        if srv:
            srv.stop()
        db.drop()


def roots(db) -> list[str]:
    return [r[0] for r in db.query("SELECT id FROM executions WHERE parent_execution_id IS NULL AND mode <> 'test' ORDER BY start_time, id")]


def test_many_children_never_prune_their_parents_and_a_pruned_root_takes_its_whole_tree(world):
    api, db = world
    grand = make(api, chain_workflow(uniq("g"), None))
    publish(api, grand)
    mid = make(api, chain_workflow(uniq("m"), grand, "latest"))
    publish(api, mid)
    top = make(api, chain_workflow(uniq("t"), mid, "latest"))
    publish(api, top)

    first = run(api, top)["execution_id"]
    tree_of_first = [r[0] for r in db.query("SELECT id FROM executions WHERE root_execution_id = %s", (first,))]
    assert len(tree_of_first) == 2                                              # mid and grand under the first root

    # a busy sub-workflow: one root that starts eight child runs. They must not push the roots out (8 > KEEP).
    gate = make(api, failing_child(threshold=99))
    publish(api, gate)
    busy = make(api, rows_parent(gate))
    busy_run = run(api, busy, {"rows": [{"n": i} for i in range(8)]})["execution_id"]
    assert db.query("SELECT count(*) FROM executions WHERE parent_execution_id = %s", (busy_run,))[0][0] == 8
    assert busy_run in roots(db) and first in roots(db), "children counted against the retention and pruned a root"

    # two more roots push the oldest ones out; the first root's tree must go with it, level by level
    for _ in range(3):
        run(api, top)
    live = roots(db)
    assert len(live) == KEEP
    assert first not in live
    assert db.query("SELECT count(*) FROM executions WHERE id = ANY(%s)", (tree_of_first,))[0][0] == 0
    assert db.query("SELECT count(*) FROM node_outputs WHERE execution_id = ANY(%s)", (tree_of_first,))[0][0] == 0
    # no orphan: every remaining child points at a remaining parent
    assert db.query("SELECT count(*) FROM executions c WHERE c.parent_execution_id IS NOT NULL "
                    "AND NOT EXISTS (SELECT 1 FROM executions p WHERE p.id = c.parent_execution_id)")[0][0] == 0
    # and the surviving roots still have their children
    for rid in live:
        n = db.query("SELECT count(*) FROM executions WHERE root_execution_id = %s", (rid,))[0][0]
        assert n in (2, 8), (rid, n)

"""Stage 8 B4: the run sites that call ``WorkflowRunner.run`` directly hand sub-workflow calls to the DB invoker
(DESIGN-STAGE8-REUSE 3.3). Cron, the broker and the test runner on the throwaway Postgres; the SSE stream and webhooks are
covered over HTTP in tests/integration/test_stage8_b4_wiring.py."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.engine.node_registry import node_registry
from tests.unit._stage8_b2_world import Env, build, child_wf, install_nodes, nd, parent_wf, remove_nodes
from tests.unit.conftest import anyio_backend, pg, pg_dbname  # noqa: F401

pytestmark = pytest.mark.anyio


@pytest.fixture(autouse=True)
def nodes():
    install_nodes()
    yield
    remove_nodes()


async def world(pg, version="latest"):
    w = Env(pg)
    child = child_wf(f"child-{w.prefix}", "published")
    c = await w.create(child)
    await w.publish_ptr(c)
    await w.edit_draft(c, child_wf(c.name, "draft-edit"))          # the draft moved on; 'latest' must not see it
    p = await w.create(parent_wf(f"parent-{w.prefix}", c.id, version))
    return w, c, p


async def child_outputs(w, child_id):
    [row] = await w.rows_of(child_id)
    async with w.sf() as s:
        from sqlalchemy import text
        r = await s.execute(text("SELECT output::text FROM node_outputs WHERE execution_id = :e AND node_name = 'work'"), {"e": row["id"]})
        return row, r.scalar()


# ── cron ─────────────────────────────────────────────────────────────────────────────────────────────────────────


async def test_cron_run_links_its_child_row_to_a_parent_row_that_exists_during_the_run(pg):
    from src.engine.cron_scheduler import CronScheduler

    w, c, p = await world(pg)
    await w.sql("UPDATE workflows SET active = true WHERE id = :w", w=p.id)
    await CronScheduler(pg)._run_cron(1, p.id)
    [parent_row] = await w.rows_of(p.id)
    assert parent_row["mode"] == "cron" and parent_row["status"] == "success"
    row, out = await child_outputs(w, c.id)
    assert row["mode"] == "sub_workflow" and row["parent_execution_id"] == parent_row["id"]
    assert row["root_execution_id"] == parent_row["id"] and row["depth"] == 1 and row["workflow_version_id"] is not None
    assert "published" in out and "draft-edit" not in out          # the published version ran, not the child's draft


async def test_cron_writes_the_parents_node_outputs_now_that_its_row_exists_up_front(pg):
    from src.engine.cron_scheduler import CronScheduler

    w, c, p = await world(pg)
    await w.sql("UPDATE workflows SET active = true WHERE id = :w", w=p.id)
    await CronScheduler(pg)._run_cron(1, p.id)
    [parent_row] = await w.rows_of(p.id)
    assert {n for n, _ in await w.outputs(parent_row["id"])} >= {"Start", "call"}


# ── broker (SDK) ─────────────────────────────────────────────────────────────────────────────────────────────────


async def test_broker_execute_workflow_resolves_versions_and_writes_the_child_row(pg):
    from src.broker.policy import EgressGuard
    from src.broker.service import BrokerService

    w, c, p = await world(pg)
    claims = SimpleNamespace(project_id=p.project_id, team_id="default", environment="default", origin="sdk",
                             execution_id=None, read_only=False)
    status, body = await BrokerService(pg)._run_node(
        claims, "ExecuteWorkflow", {"workflowId": c.id, "version": "latest", "inputMode": "passThrough"},
        [{"a": 1}], {}, set(), EgressGuard([], {}))
    assert status == 200, body
    assert body["items"][0]["tag"] == "published"
    row, out = await child_outputs(w, c.id)
    assert row["mode"] == "sub_workflow" and row["depth"] == 1 and row["workflow_version_id"] is not None


# ── the test runner ──────────────────────────────────────────────────────────────────────────────────────────────
# ExecuteWorkflow is an outside step: a test answers it with a mock, and only HttpRequest can go live in a smoke run, so the
# invoker is not reached by any test today. It is reached by the inline child of section 6.3 (test_inline=True), which plugs
# in through this same ``subworkflow_invoker`` argument. These tests pin that the argument is there and what it does.


def _test(call: str) -> dict:
    return {"id": "t_00000b41", "name": "calls the child", "target": {"kind": "workflow"}, "input": {"items": [{"a": 1}]},
            "mocks": {call: {"items": [{"tag": "mocked"}]}}, "expect": [{"node": call, "op": "status", "value": "success"}],
            "category": "normal", "status": "draft", "source": "human"}


async def run_tests_spying(w, p, persist, monkeypatch):
    from src.engine.workflow_runner import WorkflowRunner
    from src.repositories.workflow_repository import WorkflowRepository
    from src.services import test_runner

    seen: list = []

    class Spy(WorkflowRunner):
        async def run(self, *a, **kw):
            seen.append(kw.get("subworkflow_invoker"))
            return await super().run(*a, **kw)

    monkeypatch.setattr(test_runner, "WorkflowRunner", Spy)
    async with w.sf() as s:
        repo = WorkflowRepository(s)
        stored = await repo.get(p.id)
        stored.workflow.tests = [_test("call")]
        report = await test_runner.run_tests(repo, stored, None, persist=persist)
    return report, seen


@pytest.mark.parametrize("persist", [True, False])
async def test_the_test_runner_hands_the_db_invoker_to_every_run_and_mocks_still_win(pg, monkeypatch, persist):
    from src.services.subworkflow_service import DbSubworkflowInvoker

    w, c, p = await world(pg)
    report, seen = await run_tests_spying(w, p, persist, monkeypatch)
    [res] = report["results"]
    assert res["status"] == "passed", res
    assert len(seen) == 1 and isinstance(seen[0], DbSubworkflowInvoker)
    assert await w.rows_of(c.id) == []                              # the mock answered; the child never ran
    assert [i["tag"] for i in res["outputs"]["call"]["main"]] == ["mocked"]


async def test_that_invoker_runs_a_test_child_as_a_test_row_when_saved_and_not_at_all_when_unsaved(pg, monkeypatch):
    """What the 6.3 inline child gets from the runner's invoker: a ``mode='test'`` row for a saved run, nothing for an unsaved
    one, and the published version in both cases."""
    from src.engine.test_mode import TestRunOptions

    w, c, p = await world(pg)
    _, seen = await run_tests_spying(w, p, True, monkeypatch)
    invoker = seen[0]
    saved = await w.parent_ctx(p, mode="test", test_options=TestRunOptions(persist=True))
    res = await invoker.invoke(w.call(saved, c.id))
    assert [i.json["tag"] for i in res.items] == ["published"]
    [row] = await w.rows_of(c.id)
    assert row["mode"] == "test" and row["parent_execution_id"] == saved.execution_id

    unsaved = await w.parent_ctx(p, mode="test", row=False, test_options=TestRunOptions(persist=False))
    res = await invoker.invoke(w.call(unsaved, c.id))
    assert [i.json["tag"] for i in res.items] == ["published"] and res.child_execution_id is None
    assert len(await w.rows_of(c.id)) == 1

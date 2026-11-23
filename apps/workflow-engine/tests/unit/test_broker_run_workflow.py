"""``run_workflow``: backend code calls one of its project's workflows through the broker.

The broker runs the call as an ``ExecuteWorkflow`` node through the DB sub-workflow invoker, so version selection, the
Execute Workflow Trigger input, the guards and failure shaping are the sub-workflow ones. These tests use the throwaway
Postgres (``pg``) and call ``BrokerService`` the way the sandbox socket and ``/api/broker/call`` do.
"""

from __future__ import annotations

import uuid

import pytest

from src.engine.node_registry import node_registry
from src.engine.types import NodeData, NodeExecutionResult
from tests.unit._stage8_b2_world import Env, build, child_wf, install_nodes, nd, remove_nodes, _node
from tests.unit.conftest import anyio_backend, pg, pg_dbname  # noqa: F401

pytestmark = pytest.mark.anyio


class CallBackNode(_node("TestCallBack")):
    """What a Code / PythonStep node does: mint the run's broker token and call ``run_workflow`` with it.

    Emits the broker's answer as its item, so a test can read what the inner call got."""

    async def execute(self, context, node_definition, input_data):
        from src.broker.service import BrokerService, mint_for_context

        token = mint_for_context(context, "code_node")
        status, body = await BrokerService().call_with_token(
            token, "run_workflow", None, {"workflow": node_definition.parameters["workflow"], "input": {"hop": 1}}, None)
        return NodeExecutionResult(outputs={"main": [NodeData(json={"inner_status": status, "inner": body})]})


@pytest.fixture(autouse=True)
def nodes():
    install_nodes()
    node_registry.register(CallBackNode)
    yield
    node_registry._nodes.pop("TestCallBack", None)
    node_registry._instances.pop("TestCallBack", None)
    remove_nodes()


def plain(items: list[dict]) -> list[dict]:
    """The items without the trigger's bookkeeping fields (_triggeredAt, _triggerType, _executionDepth), which an
    ExecuteWorkflow call returns too."""
    return [{k: v for k, v in i.items() if not k.startswith("_")} for i in items]


def claims_for(project_id: str, **kw):
    from src.broker.tokens import BrokerClaims

    return BrokerClaims(project_id=project_id, environment=kw.pop("environment", "default"), execution_id=None,
                        origin="dev", **kw)


async def call(pg, claims, workflow, payload=None, **params):
    from src.broker.service import BrokerService

    return await BrokerService(pg).call(claims, "run_workflow", None, {"workflow": workflow, "input": payload, **params}, None)


async def world(pg):
    w = Env(pg)
    c = await w.create(child_wf(f"scorer-{w.prefix}", "published"))
    await w.publish_ptr(c)
    await w.edit_draft(c, child_wf(c.name, "draft"))          # the draft moved on after the publish
    return w, c


# ── declaration ──────────────────────────────────────────────────────────────────────────────────────────────────


async def test_run_workflow_is_a_declared_function_in_the_catalogue_and_the_generated_sdk():
    from src.broker.service import function_index
    from src.sdk.generate import catalogue, generate_python_sdk

    install_nodes()
    assert function_index()["run_workflow"] == "RunWorkflow"
    entry = next(f for f in catalogue() if f["function"] == "run_workflow")
    assert entry["parameters"]["required"] == ["workflow"]
    assert set(entry["parameters"]["properties"]) == {"workflow", "input", "timeoutSeconds"}
    assert entry["python"]["signature"].startswith("run_workflow(*, workflow: str")
    nodes_py = generate_python_sdk()["files"]["studio/nodes.py"]
    assert "def run_workflow(*, workflow: str = _UNSET" in nodes_py and '_call("RunWorkflow"' in nodes_py
    # the raw node stays out of reach: a caller can't pick its own version or skip the project checks
    assert "execute_workflow" not in function_index()


# ── 1. happy path ────────────────────────────────────────────────────────────────────────────────────────────────


async def test_code_calls_a_workflow_by_name_or_id_and_gets_its_output(pg):
    w, c = await world(pg)
    for ref in (c.name, c.id):
        status, body = await call(pg, claims_for(c.project_id), ref, {"amount": 5})
        assert status == 200, body
        assert body["ok"] is True and plain(body["items"]) == [{"amount": 5, "tag": "draft"}]   # a draft env runs the draft
        assert body["metadata"]["subworkflowCalls"] == 1
    rows = await w.rows_of(c.id)
    assert len(rows) == 2 and {r["mode"] for r in rows} == {"sub_workflow"} and rows[0]["workflow_version_id"] is None


async def test_a_list_input_is_one_item_each_and_no_input_is_one_empty_item(pg):
    w, c = await world(pg)
    status, body = await call(pg, claims_for(c.project_id), c.name, [{"n": 1}, {"n": 2}])
    assert status == 200 and [i["n"] for i in body["items"]] == [1, 2]
    status, body = await call(pg, claims_for(c.project_id), c.name)
    assert status == 200 and plain(body["items"]) == [{"tag": "draft"}]


# ── 2. unknown workflow / 3. another project's workflow ──────────────────────────────────────────────────────────


async def test_an_unknown_workflow_is_a_404(pg):
    w, c = await world(pg)
    status, body = await call(pg, claims_for(c.project_id), "no-such-workflow")
    assert status == 404, body
    assert body["error"] == {"code": "workflow_not_found", "message": "workflow 'no-such-workflow' not found in this project",
                             "workflow": "no-such-workflow"}


async def test_another_projects_workflow_looks_exactly_like_a_missing_one(pg):
    w, c = await world(pg)
    for ref in (c.id, c.name):
        status, body = await call(pg, claims_for("proj_somewhere_else"), ref)
        assert status == 404, body
        assert body["error"] == {"code": "workflow_not_found", "message": f"workflow '{ref}' not found in this project",
                                 "workflow": ref}
    assert await w.rows_of(c.id) == []                                             # nothing ran


async def test_a_name_two_workflows_share_is_refused_as_ambiguous(pg):
    w, c = await world(pg)
    twin = await w.create(child_wf(c.name, "twin"))
    status, body = await call(pg, claims_for(c.project_id), c.name)
    assert status == 409 and body["error"]["code"] == "workflow_ambiguous"
    assert sorted(body["error"]["workflow_ids"]) == sorted([c.id, twin.id])
    status, body = await call(pg, claims_for(c.project_id), twin.id)               # the id still works
    assert status == 200 and body["items"][0]["tag"] == "twin"


# ── 4. a failing workflow is a structured broker error ───────────────────────────────────────────────────────────


async def test_a_failing_workflow_surfaces_as_a_structured_broker_error(pg):
    w = Env(pg)
    c = await w.create(child_wf(f"fails-{w.prefix}", "x", middle=nd("explode", "TestBoom")))
    status, body = await call(pg, claims_for(c.project_id), c.name, {"a": 1})
    assert status == 502, body
    err = body["error"]
    assert err["code"] == "SUBWORKFLOW_FAILED"
    assert err["message"] == f"Sub-workflow '{c.name}' failed at 'explode': boom after the child"
    assert err["child_node"] == "explode" and err["child_error"] == "boom after the child"
    assert err["child_workflow_id"] == c.id and err["child_execution_id"]
    [row] = await w.rows_of(c.id)
    assert row["id"] == err["child_execution_id"] and row["status"] == "failed"


async def test_a_workflow_without_an_execute_workflow_trigger_is_not_callable(pg):
    w = Env(pg)
    c = await w.create(child_wf(f"no-trigger-{w.prefix}", trigger=False))
    status, body = await call(pg, claims_for(c.project_id), c.id)
    assert status == 422 and body["error"]["code"] == "SUBWORKFLOW_NOT_CALLABLE"


async def test_secrets_cannot_be_passed_into_a_workflow_input(pg):
    w, c = await world(pg)
    status, body = await call(pg, claims_for(c.project_id), c.name, {"k": "{{ $vars.ANY_SECRET }}"})
    assert status in (403, 422) and body["error"]["code"] in ("secret_not_bound", "missing_secret")


async def test_a_read_only_token_may_not_run_workflows(pg):
    w, c = await world(pg)
    status, body = await call(pg, claims_for(c.project_id, read_only=True), c.name)
    assert status == 403 and body["error"]["code"] == "read_only"
    assert await w.rows_of(c.id) == []


# ── 5. the recursion guard ───────────────────────────────────────────────────────────────────────────────────────


async def test_a_workflow_whose_code_calls_the_same_workflow_is_refused_not_looped(pg):
    w = Env(pg)
    name = f"loop-{w.prefix}"
    loop = await w.create(build([nd("Inputs", "ExecuteWorkflowTrigger"), nd("code", "TestCallBack", workflow=name)],
                                [("Inputs", "code")], name))
    status, body = await call(pg, claims_for(loop.project_id), name)
    assert status == 200, body
    [item] = body["items"]
    assert item["inner_status"] == 508
    assert item["inner"]["error"]["code"] == "SUBWORKFLOW_CYCLE"
    assert f"'{name}' → '{name}'" in item["inner"]["error"]["message"]
    assert len(await w.rows_of(loop.id)) == 1                                       # it ran once, never again


async def test_two_workflows_calling_each_other_through_code_stop_at_the_cycle(pg):
    w = Env(pg)
    a_name, b_name = f"ping-{w.prefix}", f"pong-{w.prefix}"
    a = await w.create(build([nd("Inputs", "ExecuteWorkflowTrigger"), nd("code", "TestCallBack", workflow=b_name)],
                             [("Inputs", "code")], a_name))
    b = await w.create(build([nd("Inputs", "ExecuteWorkflowTrigger"), nd("code", "TestCallBack", workflow=a_name)],
                             [("Inputs", "code")], b_name))
    status, body = await call(pg, claims_for(a.project_id), a_name)
    assert status == 200, body
    inner = body["items"][0]["inner"]                      # a → code → b
    assert inner["ok"] is True
    innermost = inner["items"][0]["inner"]                 # b → code → a: refused
    assert innermost["error"]["code"] == "SUBWORKFLOW_CYCLE"
    assert len(await w.rows_of(a.id)) == 1 and len(await w.rows_of(b.id)) == 1


async def test_the_sub_workflow_depth_limit_applies_to_code_calls(pg):
    w, c = await world(pg)
    status, body = await call(pg, claims_for(c.project_id, depth=10), c.name)       # max_execution_depth is 10
    assert status == 508 and body["error"]["code"] == "RECURSION_LIMIT"
    status, body = await call(pg, claims_for(c.project_id, depth=9), c.name)
    assert status == 200, body
    [row] = [r for r in await w.rows_of(c.id)]
    assert row["depth"] == 10


async def test_the_sandbox_token_carries_the_calling_runs_call_stack_and_depth():
    """A PythonStep's sandbox token knows which workflows are already running, so its code can't re-enter them."""
    from types import SimpleNamespace

    from src.broker import tokens as tk
    from src.sandbox import node_runner
    from src.sandbox import service as sbx

    seen = {}

    class FakeBackend:
        def available(self):
            return True, ""

        async def run(self, job):
            import json
            import os
            cfg = json.load(open(os.path.join(job.job_dir, "in", "input.json")))
            seen["claims"] = tk.registry.verify(cfg["token"])
            return SimpleNamespace(trace=lambda: {"stdout": "", "stderr": ""}, output={"ok": True, "result": []},
                                   timed_out=False, stderr="")

    class NoSocket:
        def __init__(self, *_):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

    import src.broker.uds_server as uds
    orig_backend, orig_sock = sbx.get_backend, uds.BrokerSocket
    sbx.get_backend, uds.BrokerSocket = (lambda: FakeBackend()), NoSocket
    try:
        ctx = SimpleNamespace(extras={"release_modules": {}, "release_id": "rel_x"}, project_id="p", environment="prod",
                              execution_id="exec_1", team_id="default", execution_depth=3, call_stack=("wf_a", "wf_b"),
                              call_names={"wf_a": "A", "wf_b": "B"})
        await node_runner.run_in_sandbox(ctx, kind="step", payload={}, entry="m:f")
    finally:
        sbx.get_backend, uds.BrokerSocket = orig_backend, orig_sock
    c = seen["claims"]
    assert (c.call_stack, c.depth, c.release_id) == (("wf_a", "wf_b"), 3, "rel_x")
    assert c.call_names == {"wf_a": "A", "wf_b": "B"}


# ── draft vs release ─────────────────────────────────────────────────────────────────────────────────────────────


async def insert_release(w, project_id: str, snapshot: dict) -> str:
    rid = "rel_" + uuid.uuid4().hex[:12]
    import json
    await w.sql("UPDATE releases SET is_active = false WHERE project_id = :p AND environment = 'prod'", p=project_id)
    await w.sql(
        "INSERT INTO releases (id, project_id, environment, number, is_active, status, snapshot, checks, created_at) "
        "VALUES (:i, :p, 'prod', (SELECT COALESCE(MAX(number), 0) + 1 FROM releases WHERE project_id = :p "
        "AND environment = 'prod'), true, 'active', CAST(:s AS jsonb), '[]'::jsonb, now())",
        i=rid, p=project_id, s=json.dumps(snapshot))
    return rid


async def test_in_a_release_the_pinned_version_runs_and_an_unpinned_workflow_runs_its_latest_published(pg):
    w, c = await world(pg)
    async with w.sf() as s:
        from sqlalchemy import text
        v1 = (await s.execute(text("SELECT published_version_id FROM workflows WHERE id = :w"), {"w": c.id})).scalar()
    await w.edit_draft(c, child_wf(c.name, "published-2"))
    await w.publish_ptr(c)                                    # the live pointer moved on to v2 after the release
    await w.edit_draft(c, child_wf(c.name, "draft-2"))

    pinned = await insert_release(w, c.project_id, {"workflows": [{"workflow_id": c.id, "version_id": v1, "via": "route"}],
                                                    "subworkflows": [], "modules": []})
    status, body = await call(pg, claims_for(c.project_id, environment="prod", release_id=pinned), c.name)
    assert status == 200, body
    assert body["items"][0]["tag"] == "published"            # v1, as the release pinned it
    unpinned = await insert_release(w, c.project_id, {"workflows": [], "subworkflows": [], "modules": []})
    status, body = await call(pg, claims_for(c.project_id, environment="prod", release_id=unpinned), c.name)
    assert status == 200 and body["items"][0]["tag"] == "published-2"   # not pinned: the latest published version
    status, body = await call(pg, claims_for(c.project_id), c.name)
    assert status == 200 and body["items"][0]["tag"] == "draft-2"        # a draft environment runs the draft


async def test_in_a_release_a_never_published_unpinned_workflow_is_refused(pg):
    w = Env(pg)
    c = await w.create(child_wf(f"unpublished-{w.prefix}"))
    rid = await insert_release(w, c.project_id, {"workflows": [], "subworkflows": [], "modules": []})
    status, body = await call(pg, claims_for(c.project_id, environment="prod", release_id=rid), c.name)
    assert status == 409 and body["error"]["code"] == "SUBWORKFLOW_VERSION_MISSING"
    assert await w.rows_of(c.id) == []

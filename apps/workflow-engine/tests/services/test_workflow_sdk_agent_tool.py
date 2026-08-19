"""The SDK as an agent tool, end to end (HANDOFF-SDK-COMPLETION.md §2 and
verification bar #2): a harness-driven agent run resolves the ``sdk`` binding,
"calls" build_workflow with a script, and a real workflow lands in the real
Postgres and is fetched back. The LLM is faked (creds still absent per the
platform handoff); everything else — resolver, provider, subprocess sandbox,
WorkflowRepository, database — is real.

Requires the live engine Postgres (workflow-engine-postgres-1).

Run: venv/bin/python -m pytest tests/services/test_workflow_sdk_agent_tool.py -v
"""

from __future__ import annotations

import asyncio
import os
import sys
from typing import Any

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.abspath(os.path.join(_HERE, "..", ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from src.db.session import async_session_factory, engine  # noqa: E402
from src.repositories.workflow_repository import WorkflowRepository  # noqa: E402
from src.services.agent_run_service import AgentRunService  # noqa: E402
from src.services.agent_tool_resolver import AgentToolResolver  # noqa: E402

from tests.services.test_agent_run_service import (  # noqa: E402
    FakeRunRepo,
    QuietRecorder,
    make_run_db,
    outcome,
)

_NAME_PREFIX = "sdk_agent_tool_test__"

SDK_BINDING = {"source": "sdk", "tool_key": "build_workflow", "enabled": True}

GOOD_SCRIPT = (
    'cron = Cron(mode="cron", cronExpression="0 7 * * 1")\n'
    'fetch = HttpRequest(method="GET", url="https://example.com/export")\n'
    'check = If(field="body", operation="isNotEmpty")\n'
    'stop = StopAndError(errorType="error", message="empty export")\n'
    'load = Postgres(operation="query", query="select 1")\n'
    "cron >> fetch\n"
    "fetch >> check\n"
    "check.true >> load\n"
    "check.false >> stop\n"
    "validate()\n"
)


def _run(coro):
    """Fresh event loop per test + disposed pool, same reason as
    tests/engine/test_workflow_sdk_integration.py — asyncpg connections are
    bound to the loop that created them."""

    async def _wrapped():
        await engine.dispose()
        return await coro

    return asyncio.run(_wrapped())


def test_resolver_resolves_sdk_binding_to_the_toolkit():
    async def scenario():
        resolved = await AgentToolResolver(
            session_factory=async_session_factory
        ).resolve([SDK_BINDING])
        assert resolved.unavailable == []
        by_name = {t["name"]: t for t in resolved.extra_tools}
        assert set(by_name) == {"build_workflow", "list_workflows", "run_workflow"}
        assert "script" in by_name["build_workflow"]["input_schema"]["properties"]
        assert "workflow_id" in by_name["run_workflow"]["input_schema"]["properties"]
        assert all(callable(t["execute"]) for t in by_name.values())
        # The reference rides along for prompt injection (on the builder only).
        appendix = by_name["build_workflow"]["system_prompt_appendix"]
        assert "Cron(" in appendix
        assert "adds a node to the workflow immediately" in appendix
        assert "Chain workflows" in appendix

    _run(scenario())


def test_agent_run_builds_and_persists_a_real_workflow(monkeypatch):
    async def scenario():
        db = make_run_db("run-sdk-e2e", "sess-sdk-e2e", status="running")
        repo = FakeRunRepo(db)
        svc = AgentRunService(
            run_repo=repo, agent_repo=None, session_factory=async_session_factory
        )

        seen: dict[str, Any] = {}

        class WorkflowBuildingRuntime:
            """Stands in for the LLM loop: reads the injected reference, calls
            the tool once, succeeds."""

            def __init__(self, session_factory: Any) -> None:
                pass

            async def run(self, spec: Any, recorder: Any) -> Any:
                assert "Workflow toolkit" in spec.system_prompt
                (tool,) = [t for t in spec.extra_tools if t["name"] == "build_workflow"]
                # The appendix must have been stripped before the model seam.
                assert "system_prompt_appendix" not in tool
                result = await tool["execute"](
                    script=GOOD_SCRIPT, name=f"{_NAME_PREFIX}e2e"
                )
                seen["tool_result"] = result
                return outcome(response=str(result.get("workflow_id")), tool_call_count=1)

        monkeypatch.setattr(
            "src.engine.agent_event_recorder.AgentEventRecorder", QuietRecorder
        )
        monkeypatch.setattr(
            "src.services.agent_run_service.AgentRuntime", WorkflowBuildingRuntime
        )
        monkeypatch.setattr(
            "src.repositories.agent_run_repository.AgentRunRepository",
            lambda session: repo,
        )

        await svc._execute(
            run_id="run-sdk-e2e",
            session_id="sess-sdk-e2e",
            snapshot={"tools": [SDK_BINDING], "system_prompt": "You build workflows."},
            task_text="build the weekly export workflow",
            input_json={},
            variables={},
            max_run_seconds=60,
        )

        result = seen["tool_result"]
        assert result["ok"] is True and result["persisted"] is True, result
        workflow_id = result["workflow_id"]
        assert db["status"] == "success"

        # Verification bar #2: fetched back from the real database.
        async with async_session_factory() as session:
            wf_repo = WorkflowRepository(session)
            try:
                stored = await wf_repo.get(workflow_id)
                assert stored is not None
                assert stored.name == f"{_NAME_PREFIX}e2e"
                types = sorted(n.type for n in stored.workflow.nodes)
                assert types == ["Cron", "HttpRequest", "If", "Postgres", "StopAndError"]
                ports = {c.source_output for c in stored.workflow.connections}
                assert {"true", "false"} <= ports
                # Auto-layout gave the canvas real coordinates.
                assert all(n.position for n in stored.workflow.nodes)
            finally:
                await wf_repo.delete(workflow_id)

    _run(scenario())


def test_dry_run_builds_but_never_persists():
    async def scenario():
        resolved = await AgentToolResolver(
            session_factory=async_session_factory
        ).resolve([SDK_BINDING])
        (tool,) = [t for t in resolved.extra_tools if t["name"] == "build_workflow"]
        result = await tool["execute"](script=GOOD_SCRIPT, dry_run=True)
        assert result["ok"] is True
        assert result["persisted"] is False and result["dry_run"] is True
        assert len(result["workflow"]["nodes"]) == 5
        assert "workflow_id" not in result

    _run(scenario())


def test_build_then_discover_then_run_the_chain():
    """The operator loop the platform exists for: build a workflow, find it,
    execute it, read every node's output back — all through the agent's own
    tools, against the real engine and the real database."""

    async def scenario():
        resolved = await AgentToolResolver(
            session_factory=async_session_factory
        ).resolve([SDK_BINDING])
        by_name = {t["name"]: t for t in resolved.extra_tools}
        wf_name = f"{_NAME_PREFIX}chain"

        built = await by_name["build_workflow"]["execute"](
            script=(
                "start = Start()\n"
                'transform = Code(code="return items")\n'
                "start >> transform\n"
                "validate()\n"
            ),
            name=wf_name,
        )
        assert built["ok"] is True and built["persisted"] is True, built
        workflow_id = built["workflow_id"]
        try:
            # Discover it the way the agent would.
            listed = await by_name["list_workflows"]["execute"](query=wf_name)
            assert listed["ok"] is True
            assert any(w["id"] == workflow_id for w in listed["workflows"])

            # Execute it and read the outputs back.
            ran = await by_name["run_workflow"]["execute"](
                workflow_id=workflow_id, input={"probe": 42}
            )
            assert ran["ok"] is True, ran
            assert ran["status"] == "success"
            assert ran["execution_id"]
            assert "Code" in ran["outputs"], ran["outputs"]

            # A missing id is an error result, not an exception.
            missing = await by_name["run_workflow"]["execute"](workflow_id="wf_nope")
            assert missing["ok"] is False and "not found" in missing["error"]
        finally:
            async with async_session_factory() as session:
                await WorkflowRepository(session).delete(workflow_id)

    _run(scenario())


def test_bad_script_returns_errors_and_persists_nothing():
    async def scenario():
        resolved = await AgentToolResolver(
            session_factory=async_session_factory
        ).resolve([SDK_BINDING])
        (tool,) = [t for t in resolved.extra_tools if t["name"] == "build_workflow"]

        # Script error (the eval's classic: wiring the constructor)
        result = await tool["execute"](script="s = Start()\nCron >> s\n")
        assert result["ok"] is False and result["persisted"] is False
        assert "CONSTRUCTOR" in result["error"]
        # The partial graph names what DID build before the failing line.
        assert result["partial_graph"]["nodes"] == [{"name": "Start", "type": "Start"}]

        # Validation problem without a script error (forgot validate())
        result = await tool["execute"](script="a = Set()\nb = Set()\na >> b\n")
        assert result["ok"] is False and result["persisted"] is False
        assert any("no trigger" in p for p in result["problems"])

    _run(scenario())

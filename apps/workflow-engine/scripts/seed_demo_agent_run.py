"""Seed one honest demo agent run: "Demo Workflow Author" builds the weekly
export workflow through the REAL build_workflow tool.

Everything except the narration text is real machinery: the agent row, the
session, the run, the recorder-written events, the tool execution (subprocess
sandbox), and the persisted workflow all go through the same code paths a live
LLM-driven run uses. The two thinking/response lines are canned — which is
exactly what this run is labeled as. Idempotent: re-running replaces the
previous demo run's workflow by name and adds a fresh run.

Usage: venv/bin/python scripts/seed_demo_agent_run.py
Prints the run id on success.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from datetime import datetime

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.abspath(os.path.join(_HERE, ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

AGENT_NAME = "Demo Workflow Author"
TASK = "(seeded demo) Build a workflow that fetches the weekly export every Monday and loads it into Postgres per region"
WORKFLOW_NAME = "demo-weekly-export"

SCRIPT = """# Weekly export: fetch, branch on emptiness, load three regions
cron = Cron(mode="cron", cronExpression="0 7 * * 1")
fetch = HttpRequest(method="GET", url="https://example.com/export", responseType="json")
check = If(field="body", operation="isNotEmpty")
cron >> fetch >> check

for region in ("US", "EU", "APAC"):
    node = Postgres(name=f"Load {region}", operation="query", query="INSERT INTO finance.ledger VALUES ($1)")
    check.true >> node

check.false >> StopAndError(errorType="error", message="Export was empty")
validate()
"""


async def main() -> None:
    from src.db.session import async_session_factory
    from src.engine.agent_event_recorder import AgentEventRecorder
    from src.repositories.agent_repository import AgentRepository
    from src.repositories.agent_run_repository import AgentRunRepository
    from src.repositories.workflow_repository import WorkflowRepository
    from src.services.workflow_sdk_tool_service import build_agent_tools

    # Replace any previous demo workflow so re-seeding stays tidy.
    async with async_session_factory() as session:
        wf_repo = WorkflowRepository(session)
        for w in await wf_repo.list():
            if w.name == WORKFLOW_NAME:
                await wf_repo.delete(w.id)

    # Agent (reused if present) — bound to the real sdk toolkit.
    async with async_session_factory() as session:
        agents = AgentRepository(session)
        existing = [a for a in await agents.list() if a.name == AGENT_NAME]
        if existing:
            agent = existing[0]
        else:
            agent = await agents.create(
                {
                    "name": AGENT_NAME,
                    "description": "Seeded demo agent — builds workflows via the SDK toolkit.",
                    "model": "claude-sonnet-5",
                    "system_prompt": "You are a workflow author.",
                }
            )
            await agents.replace_bindings(
                agent.id,
                [{"source": "sdk", "tool_key": "build_workflow", "enabled": True}],
            )

    # Session + run through the real repository (turn allocation, locks, all of it).
    async with async_session_factory() as session:
        runs = AgentRunRepository(session)
        session_row = await runs.create_session(
            {
                "agent_id": agent.id,
                "team_id": agent.team_id,
                "title": "Seeded demo: weekly export",
                "agent_version": agent.version,
                "agent_config": {"name": agent.name, "model": agent.model},
            }
        )
        run = await runs.begin_turn(
            session_row.id,
            {"task": TASK, "trigger": "seed-demo"},
        )
        await runs.update_run(run.id, {"status": "running"})

    # The REAL tool, executed for real (sandbox → validate → persist).
    tools = await build_agent_tools(
        [{"source": "sdk", "tool_key": "build_workflow", "enabled": True}],
        async_session_factory,
    )
    build = next(t for t in tools if t["name"] == "build_workflow")
    result = await build["execute"](script=SCRIPT, name=WORKFLOW_NAME)
    if not result.get("ok"):
        raise SystemExit(f"tool execution failed: {result}")

    # Events through the real recorder — the exact rows a live run writes.
    # The recorder is the ExecutionEventCallback: it takes event objects with
    # .type / .data[0].json / .node_name, same as AIAgentNode._emit_event.
    from types import SimpleNamespace

    recorder = AgentEventRecorder(run.id)
    await recorder.start()

    def emit(event_type: str, payload: dict, node_name: str = "<agent>") -> None:
        recorder(
            SimpleNamespace(
                type=event_type,
                data=[SimpleNamespace(json=payload)],
                node_name=node_name,
                timestamp=datetime.now(),
            )
        )

    emit(
        "agent:thinking",
        {
            "content": (
                "The task names a schedule (every Monday), a fetch, an emptiness "
                "guard, and three regional loads — one build_workflow script "
                "with a for-loop over regions covers it."
            ),
            "iteration": 1,
        },
    )
    emit(
        "agent:tool_call",
        {
            "tool": "build_workflow",
            "arguments": {"script": SCRIPT, "name": WORKFLOW_NAME},
            "id": "toolu_demo_build_1",
            "iteration": 1,
        },
    )
    emit(
        "agent:tool_result",
        {
            "tool": "build_workflow",
            "result": json.dumps(result),
            "id": "toolu_demo_build_1",
            "iteration": 1,
            "is_error": False,
        },
    )
    emit(
        "agent:response",
        {
            "content": (
                f"Built and saved '{WORKFLOW_NAME}' "
                f"({len(result['workflow']['nodes'])} nodes). The false branch "
                "stops with an error when the export is empty; the true branch "
                "fans out to one Postgres load per region."
            )
        },
    )
    await recorder.close()

    now = datetime.now()
    async with async_session_factory() as session:
        runs = AgentRunRepository(session)
        await runs.update_run(
            run.id,
            {
                "status": "success",
                "response": f"Built and saved '{WORKFLOW_NAME}'.",
                "iterations": 1,
                "tool_call_count": 1,
                "event_count": recorder.event_count,
                "ended_at": now,
            },
        )
        await runs.update_session(session_row.id, {"status": "idle", "last_run_at": now})

    print(run.id)


if __name__ == "__main__":
    asyncio.run(main())

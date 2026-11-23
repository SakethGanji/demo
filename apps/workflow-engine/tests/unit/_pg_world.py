"""Helpers for the DB-backed Stage 5 tests: a counting node, workflows on the throwaway database, run/decide shortcuts."""

from __future__ import annotations

import asyncio
import json
import uuid
from typing import Any

from sqlalchemy import text

from src.engine import task_resumer
from src.engine.node_registry import node_registry, register_all_nodes
from src.engine.types import Connection, NodeData, NodeDefinition, NodeExecutionResult, Workflow
from src.nodes.base import BaseNode, NodeTypeDescription

CALLS: list[str] = []  # names of TestCounter nodes in the order they really ran


class CounterNode(BaseNode):
    """Passes items through and records that it ran: how the tests prove 'ran once' and 'never re-run'."""

    node_description = NodeTypeDescription(name="TestCounter", display_name="TestCounter", description="test")

    @property
    def type(self) -> str:
        return "TestCounter"

    @property
    def description(self) -> str:
        return "test"

    async def execute(self, context, node_definition, input_data):
        CALLS.append(node_definition.name)
        return NodeExecutionResult(outputs={"main": [NodeData(json={**d.json, "via": node_definition.name}) for d in input_data]})


def install_counter() -> None:
    register_all_nodes()
    CALLS.clear()
    node_registry.register(CounterNode)


def remove_counter() -> None:
    node_registry._nodes.pop("TestCounter", None)
    node_registry._instances.pop("TestCounter", None)


def nd(name: str, type_: str = "TestCounter", **params: Any) -> NodeDefinition:
    return NodeDefinition(name=name, type=type_, parameters=params)


def build(nodes: list[NodeDefinition], conns: list[tuple], name: str = "wf") -> Workflow:
    return Workflow(
        name=name, nodes=nodes,
        connections=[Connection(source_node=c[0], target_node=c[1], source_output=c[2] if len(c) > 2 else "main") for c in conns],
    )


def approval_flow(**approval_params: Any) -> Workflow:
    """start -> up -> review(Approval) -> yes | no | late, all TestCounter except the trigger and the Approval."""
    return build(
        [nd("start", "Start"), nd("up"), nd("review", "Approval", **approval_params), nd("yes"), nd("no"), nd("late")],
        [("start", "up"), ("up", "review"), ("review", "yes", "approved"), ("review", "no", "rejected"),
         ("review", "late", "timed_out")],
        name="approval-flow",
    )


class World:
    def __init__(self, sf: Any) -> None:
        self.sf = sf
        self.prefix = uuid.uuid4().hex[:8]

    async def create(self, workflow: Workflow) -> Any:
        from src.repositories.workflow_repository import WorkflowRepository

        async with self.sf() as s:
            return await WorkflowRepository(s).create(workflow)

    async def publish(self, stored: Any) -> int:
        """Snapshot the current draft as an immutable version; returns its id."""
        from src.repositories.workflow_repository import workflow_to_definition

        async with self.sf() as s:
            r = await s.execute(
                text("""INSERT INTO workflow_versions (workflow_id, version_number, definition)
                        VALUES (:w, COALESCE((SELECT max(version_number) FROM workflow_versions WHERE workflow_id = :w), 0) + 1,
                                CAST(:d AS jsonb)) RETURNING id"""),
                {"w": stored.id, "d": json.dumps(workflow_to_definition(stored.workflow))},
            )
            vid = r.scalar()
            await s.commit()
            return int(vid)

    async def edit_draft(self, stored: Any, workflow: Workflow) -> None:
        from src.repositories.workflow_repository import WorkflowRepository

        async with self.sf() as s:
            await WorkflowRepository(s).update(stored.id, workflow)

    async def run(self, workflow_id: str, item: dict[str, Any] | None = None, **kw: Any) -> tuple[Any, Any]:
        from src.repositories.execution_repository import ExecutionRepository
        from src.repositories.workflow_repository import WorkflowRepository
        from src.services.execution_service import ExecutionService

        async with self.sf() as s:
            svc = ExecutionService(ExecutionRepository(s), WorkflowRepository(s))
            return await svc.run_saved_workflow(workflow_id, initial_item=item or {"amount": 5}, mode="manual", **kw)

    async def tasks_of(self, execution_id: str) -> list[dict[str, Any]]:
        async with self.sf() as s:
            r = await s.execute(
                text("SELECT * FROM human_tasks WHERE execution_id = :e ORDER BY node_name, run_index"), {"e": execution_id}
            )
            return [dict(x) for x in r.mappings().all()]

    async def task(self, execution_id: str, node: str = "review") -> dict[str, Any]:
        rows = [t for t in await self.tasks_of(execution_id) if t["node_name"] == node]
        assert rows, f"no task for {node}"
        return rows[0]

    async def execution(self, execution_id: str) -> dict[str, Any]:
        async with self.sf() as s:
            r = await s.execute(text("SELECT * FROM executions WHERE id = :e"), {"e": execution_id})
            row = r.mappings().first()
            return dict(row) if row else {}

    async def outputs(self, execution_id: str) -> dict[tuple[str, int], str]:
        async with self.sf() as s:
            r = await s.execute(
                text("SELECT node_name, run_index, status FROM node_outputs WHERE execution_id = :e"), {"e": execution_id}
            )
            return {(x[0], x[1]): x[2] for x in r.all()}

    async def decide(self, task_id: str, decision: str = "approved", key: str | None = None, by: str = "dana", **kw: Any) -> dict[str, Any]:
        from src.services.task_service import TaskService

        async with self.sf() as s:
            return await TaskService(s).decide(task_id, decision, kw.get("comment"), kw.get("form_data"), by,
                                              key or uuid.uuid4().hex)

    async def settle(self) -> None:
        """Wait for every background resume started in this process."""
        while True:
            pending = [t for t in list(task_resumer._inflight) if not t.done()]
            if not pending:
                return
            await asyncio.gather(*pending, return_exceptions=True)

    async def sql(self, query: str, **params: Any) -> list[Any]:
        async with self.sf() as s:
            r = await s.execute(text(query), params)
            rows = r.all() if r.returns_rows else []
            await s.commit()
            return rows


async def make_world(sf: Any) -> World:
    """A World on a clean task table: the scheduler sweeps are database-wide, so tasks left by an earlier test must not leak in."""
    w = World(sf)
    await w.sql("DELETE FROM human_tasks")
    task_resumer.FAULT.clear()
    return w

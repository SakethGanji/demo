"""Helpers for the Stage 8 B2 tests: probe nodes, workflow builders, and a parent ``ExecutionContext`` on the throwaway DB."""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

from sqlalchemy import text

from src.engine.node_registry import node_registry, register_all_nodes
from src.engine.subworkflow import SubworkflowCall
from src.engine.types import Connection, NodeData, NodeDefinition, NodeExecutionResult, Workflow
from src.nodes.base import BaseNode, NodeTypeDescription
from tests.unit._pg_world import World

PROBES: list[dict[str, Any]] = []  # what TestProbe saw, in order


def _node(type_name: str):
    class _N(BaseNode):
        node_description = NodeTypeDescription(name=type_name, display_name=type_name, description="test")

        @property
        def type(self) -> str:
            return type_name

        @property
        def description(self) -> str:
            return "test"

    return _N


class SleepNode(_node("TestSleep")):
    async def execute(self, context, node_definition, input_data):
        await asyncio.sleep(float(node_definition.parameters.get("seconds", 5)))
        return NodeExecutionResult(outputs={"main": list(input_data)})


class BoomNode(_node("TestBoom")):
    async def execute(self, context, node_definition, input_data):
        raise RuntimeError("boom after the child")


class ProbeNode(_node("TestProbe")):
    async def execute(self, context, node_definition, input_data):
        PROBES.append({
            "node": node_definition.name, "execution_id": context.execution_id,
            "api_specs": dict(context.extras.get("api_specs") or {}), "variables": dict(context.variables),
            "secret_values": set(context.extras.get("secret_values") or ()), "depth": context.execution_depth,
            "call_stack": tuple(context.call_stack), "root": context.root_execution_id,
        })
        return NodeExecutionResult(outputs={"main": [NodeData(json={**d.json, "probed": node_definition.name}) for d in input_data]})


class MarkNode(_node("TestMark")):
    """Passes items through and tags them with its ``tag`` parameter: tells two versions of a child apart."""

    async def execute(self, context, node_definition, input_data):
        tag = node_definition.parameters.get("tag", "")
        return NodeExecutionResult(outputs={"main": [NodeData(json={**d.json, "tag": tag}) for d in input_data]})


def install_nodes() -> None:
    register_all_nodes()
    PROBES.clear()
    for cls in (SleepNode, BoomNode, ProbeNode, MarkNode):
        node_registry.register(cls)


def remove_nodes() -> None:
    for t in ("TestSleep", "TestBoom", "TestProbe", "TestMark"):
        node_registry._nodes.pop(t, None)
        node_registry._instances.pop(t, None)


def nd(name: str, type_: str, **params: Any) -> NodeDefinition:
    return NodeDefinition(name=name, type=type_, parameters=params)


def build(nodes: list[NodeDefinition], conns: list[tuple], name: str) -> Workflow:
    return Workflow(
        name=name, nodes=nodes,
        connections=[Connection(source_node=c[0], target_node=c[1], source_output=c[2] if len(c) > 2 else "main") for c in conns],
    )


def child_wf(name: str, tag: str = "v1", *, middle: NodeDefinition | None = None, trigger: bool = True) -> Workflow:
    """``Inputs`` (ExecuteWorkflowTrigger) -> ``work`` (TestMark) [-> middle]."""
    nodes = [nd("Inputs", "ExecuteWorkflowTrigger"), nd("work", "TestMark", tag=tag)]
    conns: list[tuple] = [("Inputs", "work")]
    if middle is not None:
        nodes.append(middle)
        conns.append(("work", middle.name))
    if not trigger:
        nodes = nodes[1:]
        conns = conns[1:]
    return build(nodes, conns, name)


def parent_wf(name: str, child_id: str, version: Any = "latest", *, boom: bool = False, call_name: str = "call") -> Workflow:
    params: dict[str, Any] = {"workflowId": child_id, "inputMode": "passThrough"}
    if version is not None:
        params["version"] = version
    nodes = [nd("Start", "Start"), nd(call_name, "ExecuteWorkflow", **params)]
    conns: list[tuple] = [("Start", call_name)]
    if boom:
        nodes.append(nd("after", "TestBoom"))
        conns.append((call_name, "after"))
    return build(nodes, conns, name)


class Env(World):
    """A World plus publish-with-pointer and a parent context on the throwaway database."""

    async def publish_ptr(self, stored: Any) -> int:
        from src.repositories.workflow_repository import WorkflowRepository

        async with self.sf() as s:
            fresh = await WorkflowRepository(s).get(stored.id)  # the *current* draft, not the object the caller holds
        vid = await self.publish(fresh)
        await self.sql("UPDATE workflows SET published_version_id = :v WHERE id = :w", v=vid, w=stored.id)
        return vid

    async def parent_ctx(
        self, parent: Any, *, mode: str = "manual", row: bool = True, test_options: Any = None, depth: int = 0,
        call_stack: tuple[str, ...] | None = None, extras: dict[str, Any] | None = None, project_id: str | None = None,
        version_id: int | None = None,
    ) -> Any:
        from src.engine.workflow_runner import WorkflowRunner
        from src.repositories.execution_repository import ExecutionRepository

        ctx = WorkflowRunner()._create_context(parent.workflow, mode)
        ctx.execution_id = "exec_" + uuid.uuid4().hex[:12]
        ctx.project_id = project_id or parent.project_id
        ctx.environment = "default"
        ctx.call_stack = call_stack if call_stack is not None else (parent.id,)
        ctx.root_execution_id = ctx.execution_id
        ctx.execution_depth = depth
        ctx.version_id = version_id
        ctx.test_options = test_options
        if extras:
            ctx.extras.update(extras)
        if row:
            async with self.sf() as s:
                await ExecutionRepository(s).start(
                    ctx.execution_id, parent.id, parent.name, mode, project_id=parent.project_id, environment="default")
        return ctx

    def call(self, ctx: Any, child_id: str, version: Any = "latest", node: str = "call", timeout: float = 30,
             items: list[NodeData] | None = None) -> SubworkflowCall:
        return SubworkflowCall(parent_context=ctx, parent_node=node, workflow_id=child_id, version=version,
                               items=items or [NodeData(json={"a": 1})], timeout_seconds=timeout)

    def invoker(self):
        from src.services.subworkflow_service import DbSubworkflowInvoker

        return DbSubworkflowInvoker(self.sf)

    async def rows_of(self, workflow_id: str) -> list[dict[str, Any]]:
        async with self.sf() as s:
            r = await s.execute(text("SELECT * FROM executions WHERE workflow_id = :w ORDER BY start_time, id"), {"w": workflow_id})
            return [dict(x) for x in r.mappings().all()]

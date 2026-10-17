"""Tiny fake node registry so runner behaviour can be unit-tested without Postgres or real nodes."""

from __future__ import annotations

from src.engine.types import (
    Connection, NodeData, NodeDefinition, NodeExecutionResult, Workflow,
)
from src.engine.workflow_runner import WorkflowRunner


class PassNode:
    input_count = 1

    def __init__(self, log: list[str]) -> None:
        self.log = log

    async def execute(self, context, node_def, input_data):
        self.log.append(node_def.name)
        return NodeExecutionResult(outputs={"main": [NodeData(json={**i.json, "via": node_def.name}) for i in input_data]})


class BranchNode:
    input_count = 1

    def __init__(self, log: list[str]) -> None:
        self.log = log

    async def execute(self, context, node_def, input_data):
        self.log.append(node_def.name)
        return NodeExecutionResult(outputs={"true": input_data, "false": None})


class FailNode:
    input_count = 1

    def __init__(self, log: list[str]) -> None:
        self.log = log

    async def execute(self, context, node_def, input_data):
        self.log.append(node_def.name)
        raise RuntimeError("boom")


class FakeRegistry:
    def __init__(self, log: list[str]) -> None:
        self._nodes = {"Pass": PassNode(log), "Branch": BranchNode(log), "Fail": FailNode(log)}

    def get(self, node_type: str):
        return self._nodes[node_type]


def make_runner() -> tuple[WorkflowRunner, list[str]]:
    log: list[str] = []
    runner = WorkflowRunner()
    runner._registry = FakeRegistry(log)
    return runner, log


def node(name: str, type_: str = "Pass", **kw) -> NodeDefinition:
    return NodeDefinition(name=name, type=type_, **kw)


def wf(nodes: list[NodeDefinition], conns: list[tuple]) -> Workflow:
    return Workflow(
        name="t",
        nodes=nodes,
        connections=[
            Connection(source_node=c[0], target_node=c[1], source_output=c[2] if len(c) > 2 else "main")
            for c in conns
        ],
    )


def items(*dicts: dict) -> list[NodeData]:
    return [NodeData(json=d) for d in dicts]

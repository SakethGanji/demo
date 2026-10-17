"""Partial runs for the editor: test one node, run to a node, run from a node.

All three go through ExecutionService.run_saved_workflow (the single run path) with the
partial-run options of WorkflowRunner.run (start_node_name / stop_at / pre_populated_states).
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import select

from ..core.exceptions import (
    ExecutionNotFoundError,
    MissingNodeDataError,
    NodeNotFoundInWorkflowError,
    ValidationError,
    WorkflowNotFoundError,
)
from ..db.models import ExecutionModel, NodeOutputModel
from ..engine.graph import TRIGGER_NODE_TYPES, ancestors, upstream_connections
from ..engine.types import NodeData
from .execution_service import ExecutionService

if TYPE_CHECKING:
    from ..repositories import ExecutionRepository, WorkflowRepository


def _items(raw: Any) -> list[NodeData]:
    """Saved node_outputs rows hold [{"json": {...}}]."""
    return [NodeData(json=i.get("json", {})) for i in raw] if isinstance(raw, list) else []


class PartialRunService:
    def __init__(self, execution_repo: ExecutionRepository, workflow_repo: WorkflowRepository) -> None:
        self._execution_repo = execution_repo
        self._workflow_repo = workflow_repo
        self._exec_service = ExecutionService(execution_repo, workflow_repo)
        self._session = workflow_repo._session

    async def _load(self, workflow_id: str, name: str):
        stored = await self._workflow_repo.get(workflow_id)
        if not stored:
            raise WorkflowNotFoundError(workflow_id)
        if not any(n.name == name for n in stored.workflow.nodes):
            raise NodeNotFoundInWorkflowError(name)
        return stored

    async def _outputs_of(self, execution_id: str) -> dict[str, list[NodeData]]:
        """Successful saved outputs of one execution (latest run_index wins per node)."""
        rows = (await self._session.execute(
            select(NodeOutputModel)
            .where(NodeOutputModel.execution_id == execution_id, NodeOutputModel.status == "success")
            .order_by(NodeOutputModel.run_index, NodeOutputModel.created_at)
        )).scalars().all()
        return {r.node_name: _items(r.output) for r in rows}

    async def _latest_output(self, workflow_id: str, node_name: str) -> list[NodeData] | None:
        """The node's saved output from the workflow's latest execution that has one."""
        row = (await self._session.execute(
            select(NodeOutputModel)
            .join(ExecutionModel, ExecutionModel.id == NodeOutputModel.execution_id)
            .where(
                ExecutionModel.workflow_id == workflow_id,
                NodeOutputModel.node_name == node_name,
                NodeOutputModel.status == "success",
            )
            .order_by(ExecutionModel.start_time.desc(), NodeOutputModel.run_index.desc())
            .limit(1)
        )).scalars().first()
        return _items(row.output) if row else None

    def _is_multi_input(self, node_type: str) -> bool:
        from ..engine.node_registry import node_registry
        count = getattr(node_registry.get(node_type), "input_count", 1)
        return count > 1

    @staticmethod
    def _merge_seed(name: str, conns: list[Any], items_by_source: dict[str, list[NodeData]]) -> dict[str, dict[str, list[NodeData]]]:
        """Pre-seed a multi-input node's pending inputs, one entry per unique incoming connection."""
        seed: dict[str, list[NodeData]] = {}
        for c in conns:
            seed[f"{c.source_node}:{c.source_output}"] = items_by_source.get(c.source_node, [])
        return {f"{name}:0": seed}

    # ── test one node ────────────────────────────────────────────────────────────────

    async def test_node(
        self, workflow_id: str, name: str, source: str, input_items: list[dict[str, Any]] | None = None,
        environment: str = "default",
    ):
        stored = await self._load(workflow_id, name)
        node = next(n for n in stored.workflow.nodes if n.name == name)
        conns = upstream_connections(stored.workflow.connections, name)
        upstream = conns[0].source_node if conns else None

        items: list[NodeData]
        items_by_source: dict[str, list[NodeData]] = {}
        if source == "input":
            if input_items is None:
                raise ValidationError("input_items is required when source is 'input'", field="input_items")
            items = [NodeData(json=d) for d in input_items]
            if upstream:
                items_by_source[upstream] = items
        elif source in ("last_run", "pinned"):
            items = []
            missing: list[str] = []
            for src in dict.fromkeys(c.source_node for c in conns):
                if source == "pinned":
                    src_node = next((n for n in stored.workflow.nodes if n.name == src), None)
                    data = list(src_node.pinned_data) if src_node and src_node.pinned_data else None
                else:
                    data = await self._latest_output(workflow_id, src)
                if data is None:
                    missing.append(src)
                else:
                    items_by_source[src] = data
            if missing:
                where = "pinned data" if source == "pinned" else "a saved output in the latest runs"
                raise MissingNodeDataError(f"No {where} for upstream node(s): {', '.join(missing)}", missing)
            items = items_by_source.get(upstream, []) if upstream else []
        else:
            raise ValidationError("source must be one of: input, last_run, pinned", field="source")

        pending = None
        if self._is_multi_input(node.type):
            # Merge-like nodes read their inputs from the pre-seeded pending map, not from the start job
            pending = self._merge_seed(name, conns, items_by_source)
            items = []
        _, context = await self._exec_service.run_saved_workflow(
            workflow_id, environment=environment, strict_environment=True, mode="manual",
            metadata={"trigger": {"type": "node_test", "node": name}},
            start_node_name=name, stop_at=name, initial_data_override=items, pending_inputs=pending,
        )
        return context

    # ── run to / from ────────────────────────────────────────────────────────────────

    async def run_to(
        self, workflow_id: str, name: str, input_data: dict[str, Any] | None = None, environment: str = "default",
    ):
        await self._load(workflow_id, name)
        _, context = await self._exec_service.run_saved_workflow(
            workflow_id, input_data, environment=environment, strict_environment=True, mode="manual",
            metadata={"trigger": {"type": "run_to", "node": name}}, stop_at=name,
        )
        return context

    async def run_from(self, workflow_id: str, name: str, execution_id: str | None = None, environment: str = "default"):
        stored = await self._load(workflow_id, name)
        needed = ancestors(stored.workflow.connections, name)
        if execution_id is None:
            # Like n8n: reuse the most recent run that has every upstream output, so a node test
            # (which saves only one node) doesn't hide the last full run.
            recent = await self._execution_repo.list(workflow_id=workflow_id, limit=20)
            if not recent:
                raise ExecutionNotFoundError(f"(no executions for workflow {workflow_id})")
            execution_id, saved = recent[0].id, await self._outputs_of(recent[0].id)
            for record in recent:
                outputs = await self._outputs_of(record.id)
                if all(n in outputs for n in needed):
                    execution_id, saved = record.id, outputs
                    break
        else:
            record = await self._execution_repo.get(execution_id)
            if not record or record.workflow_id != workflow_id:
                raise ExecutionNotFoundError(execution_id)
            saved = await self._outputs_of(execution_id)

        node = next(n for n in stored.workflow.nodes if n.name == name)
        conns = upstream_connections(stored.workflow.connections, name)
        missing = [n for n in needed if n not in saved]
        if missing:
            raise MissingNodeDataError(
                f"Execution {execution_id} has no saved output for upstream node(s): {', '.join(missing)}", missing,
            )
        pre_populated = {n: saved[n] for n in needed}

        multi = self._is_multi_input(node.type)
        if conns:
            items = [] if multi else pre_populated.get(conns[0].source_node, [])
        elif node.type in TRIGGER_NODE_TYPES:
            items = [NodeData(json={"triggeredAt": datetime.now().isoformat(), "mode": "manual"})]
        else:
            items = []
        pending = self._merge_seed(name, conns, pre_populated) if multi else None

        _, context = await self._exec_service.run_saved_workflow(
            workflow_id, environment=environment, strict_environment=True, mode="manual",
            metadata={"trigger": {"type": "run_from", "node": name, "source_execution": execution_id}},
            start_node_name=name, initial_data_override=items,
            pre_populated_states=pre_populated, pending_inputs=pending,
        )
        return context

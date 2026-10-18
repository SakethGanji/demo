"""Learn / approve / delete the output contract of one node instance (Stage 2, CONTRACTS-STAGE2 §2).

* **learn**: infer a Shape from a saved ``node_outputs`` row (read the same way as ``partial_run.py``:
  rows hold ``[{"json": {...}}]``). Pure read — nothing is saved.
* **approve**: a person approves (possibly edited) shapes; they are written onto the node in the DRAFT
  definition as ``contract`` and from then on replace the node type's computed outputs.
* **delete**: remove that ``contract`` from the draft.

Published versions are immutable snapshots, so they are never touched here; a contract reaches a
version when the draft is published.
"""

from __future__ import annotations

import copy
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
from ..db.models import ExecutionModel, NodeOutputModel, WorkflowModel
from ..engine import shapes

if TYPE_CHECKING:
    from ..repositories import ExecutionRepository, WorkflowRepository


def _json_items(raw: Any) -> list[dict[str, Any]]:
    """Saved rows hold ``[{"json": {...}}]``; keep the json dicts."""
    if not isinstance(raw, list):
        return []
    return [i.get("json", {}) for i in raw if isinstance(i, dict) and isinstance(i.get("json", {}), dict)]


class ContractService:
    def __init__(self, execution_repo: ExecutionRepository, workflow_repo: WorkflowRepository) -> None:
        self._execution_repo = execution_repo
        self._workflow_repo = workflow_repo
        self._session = workflow_repo._session

    async def _load_node(self, workflow_id: str, name: str):
        stored = await self._workflow_repo.get(workflow_id)
        if not stored:
            raise WorkflowNotFoundError(workflow_id)
        node = next((n for n in stored.workflow.nodes if n.name == name), None)
        if node is None:
            raise NodeNotFoundInWorkflowError(name)
        return node

    async def _saved_output(self, workflow_id: str, name: str, execution_id: str | None) -> tuple[str, list[dict[str, Any]]] | None:
        """(execution_id, json items) of the node's saved successful output, or None."""
        stmt = (
            select(NodeOutputModel, ExecutionModel.id)
            .join(ExecutionModel, ExecutionModel.id == NodeOutputModel.execution_id)
            .where(
                ExecutionModel.workflow_id == workflow_id,
                NodeOutputModel.node_name == name,
                NodeOutputModel.status == "success",
            )
            .order_by(ExecutionModel.start_time.desc(), NodeOutputModel.run_index.desc())
        )
        if execution_id is not None:
            stmt = stmt.where(NodeOutputModel.execution_id == execution_id)
        for row, exec_id in (await self._session.execute(stmt)).all():
            items = _json_items(row.output)
            if items:  # an empty output teaches nothing; fall through to the next-latest run
                return exec_id, items
        return None

    # ── learn ────────────────────────────────────────────────────────────────────────────

    async def learn(self, workflow_id: str, name: str, execution_id: str | None = None) -> dict[str, Any]:
        await self._load_node(workflow_id, name)
        if execution_id is not None:
            record = await self._execution_repo.get(execution_id)
            if not record or record.workflow_id != workflow_id:
                raise ExecutionNotFoundError(execution_id)
        found = await self._saved_output(workflow_id, name, execution_id)
        if found is None:
            where = f"execution {execution_id}" if execution_id else "any execution of this workflow"
            raise MissingNodeDataError(f"No saved output for node '{name}' in {where}", [name])
        exec_id, items = found
        return {"outputs": {"main": shapes.from_sample(items)}, "from_execution": exec_id, "sample_count": len(items)}

    # ── approve / delete ─────────────────────────────────────────────────────────────────

    async def approve(
        self, workflow_id: str, name: str, outputs: dict[str, Any], from_execution: str | None, approved_by: str,
    ) -> None:
        node = await self._load_node(workflow_id, name)
        approved_by = (approved_by or "").strip()
        if not approved_by:
            raise ValidationError("approved_by is required", field="approved_by")
        if not isinstance(outputs, dict) or not outputs:
            raise ValidationError("outputs must be a non-empty object of port -> shape", field="outputs")
        clean: dict[str, Any] = {}
        for port, raw in outputs.items():
            if not isinstance(raw, dict):
                raise ValidationError(f"output '{port}' must be a shape object", field="outputs")
            clean[str(port)] = shapes.normalize(raw, "observed")
        contract = {
            "outputs": clean,
            "source": "observed",
            "approved_by": approved_by,
            "approved_at": datetime.now().isoformat(),
        }
        if from_execution:
            contract["from_execution"] = from_execution
        await self._write(workflow_id, node.name, contract)

    async def delete(self, workflow_id: str, name: str) -> None:
        await self._load_node(workflow_id, name)
        await self._write(workflow_id, name, None)

    async def _write(self, workflow_id: str, name: str, contract: dict[str, Any] | None) -> None:
        db_workflow = await self._session.get(WorkflowModel, workflow_id)
        if not db_workflow:
            raise WorkflowNotFoundError(workflow_id)
        definition = copy.deepcopy(db_workflow.draft_definition)
        for n in definition.get("nodes", []):
            if n.get("name") == name:
                if contract is None:
                    n.pop("contract", None)
                else:
                    n["contract"] = contract
        db_workflow.draft_definition = definition
        db_workflow.updated_at = datetime.now()
        await self._session.commit()

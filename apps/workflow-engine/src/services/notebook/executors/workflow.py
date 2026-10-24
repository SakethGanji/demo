"""workflow: run a saved workflow (pinned version number, or ``"published"`` = the published version, else the draft).

The execution carries ``metadata.attribution`` ``{triggered_by, run_as, via_agent, via: notebook:…/cell:…/run:…}``.
Output rows are the items of the last node that ran.
"""

from __future__ import annotations

import time
from typing import Any

from . import CellOutcome
from ._common import ms_since


async def _version_row_id(s: Any, workflow_id: str, version: Any) -> tuple[bool, int | None]:
    from sqlalchemy import select

    from ....db.models import WorkflowModel, WorkflowVersionModel

    wf = await s.get(WorkflowModel, workflow_id)
    if wf is None:
        return False, None
    if isinstance(version, int) and not isinstance(version, bool):
        vid = (await s.execute(select(WorkflowVersionModel.id).where(
            WorkflowVersionModel.workflow_id == workflow_id,
            WorkflowVersionModel.version_number == version))).scalar()
        if vid is None:
            raise LookupError(f"workflow has no version {version}")
        return True, vid
    return True, wf.published_version_id


async def run(ctx: Any, cell: Any, inputs: dict[str, Any]) -> CellOutcome:
    from ....engine.types import run_status
    from ....repositories.execution_repository import ExecutionRepository
    from ....repositories.workflow_repository import WorkflowRepository
    from ...execution_service import ExecutionService
    from ..storage import preview

    t0 = time.perf_counter()
    wid = inputs.get("workflow_id")
    try:
        async with ctx.session() as s:
            found, vid = await _version_row_id(s, wid, inputs.get("version", "published"))
            if not found:
                return CellOutcome.fail("workflow_failed", f"workflow '{wid}' not found")
            svc = ExecutionService(ExecutionRepository(s), WorkflowRepository(s))
            _stored, context = await svc.run_saved_workflow(
                wid, inputs.get("inputs") or None, environment=ctx.environment or "default", mode="manual",
                version_id=vid, timeout_s=300.0,
                metadata={"trigger": {"type": "notebook"}, "attribution": ctx.attribution_for(cell.id)})
    except LookupError as e:
        return CellOutcome(status="error", error={"code": "workflow_failed", "message": str(e)}, ms=ms_since(t0))
    except TimeoutError:
        return CellOutcome(status="error", error={"code": "timeout", "message": "the workflow took longer than 300s"},
                           ms=ms_since(t0))
    except Exception as e:  # noqa: BLE001
        return CellOutcome(status="error", error={"code": "workflow_failed", "message": str(e)[:500]}, ms=ms_since(t0))
    status = run_status(context)
    last = list(context.node_states)[-1] if context.node_states else None
    items = context.node_states.get(last) or [] if last else []
    rows = [i.json if isinstance(i.json, dict) else {"value": i.json} for i in items]
    out = preview(rows, None, execution_id=context.execution_id, status=status, last_node=last)
    if status == "failed":
        msg = context.errors[0].error if context.errors else "the workflow failed"
        return CellOutcome(status="error", output=out, full_rows=rows, execution_id=context.execution_id,
                           ms=ms_since(t0), error={"code": "workflow_failed", "message": str(msg)[:500]})
    return CellOutcome(output=out, full_rows=rows, execution_id=context.execution_id, ms=ms_since(t0))

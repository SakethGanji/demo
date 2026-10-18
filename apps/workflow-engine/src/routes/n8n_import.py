"""n8n import route (Stage 2 contract section 4). Converts only; never saves."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.dependencies import get_db_session

from ..schemas.workflow import WorkflowCreateRequest
from ..services.n8n_import import import_n8n

router = APIRouter(prefix="/workflows")


class N8nImportRequest(BaseModel):
    n8n: Any = Field(..., description="n8n workflow export JSON ({name, nodes, connections, ...})")
    name: str | None = Field(None, description="Override the imported workflow's name")
    project_id: str | None = Field(None, description="Project to run the checker against")


@router.post("/import/n8n")
async def import_n8n_workflow(body: N8nImportRequest, session: AsyncSession = Depends(get_db_session)) -> dict[str, Any]:
    """Translate an n8n workflow into our workflow shape plus an import report. Nothing is saved."""
    try:
        workflow, report = import_n8n(body.n8n, name=body.name, project_id=body.project_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    try:
        validated = WorkflowCreateRequest(**workflow)
    except ValidationError as e:  # importer bug, not a client error; keep the detail useful
        raise HTTPException(status_code=500, detail=f"Imported workflow failed validation: {e}") from e
    imported = validated.model_dump(mode="json", exclude_none=True)
    # The checker needs the project's vars, tables and allowlist, so it runs here rather than in the pure importer
    from ..services.check_lookups import build_lookups
    from ..services.workflow_checker import check_workflow
    lookups = await build_lookups(session, body.project_id)
    report["findings"] = check_workflow(imported, lookups=lookups)["findings"]
    return {"workflow": imported, "report": report}

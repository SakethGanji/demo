"""Read-only audit trail endpoint (platform superusers only)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.api.pagination import Page, PageParams, pagination
from app.features.auth.deps import Principal, get_principal
from app.shared import audit

router = APIRouter(prefix="/audit", tags=["audit"])


class AuditEntry(BaseModel):
    id: int
    occurred_at: str
    actor_user_id: str | None = None
    actor_email: str | None = None
    team_id: str | None = None
    action: str
    method: str
    path: str
    status_code: int
    resource_type: str | None = None
    resource_id: str | None = None
    ip: str | None = None
    user_agent: str | None = None
    request_id: str | None = None
    metadata: dict[str, Any] | None = None


@router.get("", response_model=Page[AuditEntry])
async def list_audit(
    page: PageParams = Depends(pagination),
    principal: Principal = Depends(get_principal),
) -> Page[AuditEntry]:
    """Return the audit trail, newest first. Restricted to platform superusers."""
    if not principal.is_superuser:
        raise HTTPException(403, "Audit access is restricted to platform administrators")
    rows, total = await audit.query(limit=page.limit, offset=page.offset)
    return Page.of([AuditEntry(**r) for r in rows], total, page)

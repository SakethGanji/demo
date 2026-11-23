"""Project knowledge API (PLAN-BUILDER WP-1): the builders' per-project memory, exposed for the studio and for debugging.

    GET  /projects/{project}/knowledge?q=&kinds=&budget=   ranked retrieval within a token budget
    POST /projects/{project}/knowledge                     upsert one row by (kind, key)
    POST /projects/{project}/knowledge/reindex             rebuild the code-derived rows (tables, routes, screens, kit use)
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.dependencies import get_db_session
from ..db.knowledge_models import KINDS, SOURCES, STATUSES
from ..services import project_knowledge as pk
from ..services.project_service import ProjectError, ProjectService
from .projects import raise_project_error

router = APIRouter()


class KnowledgeUpsert(BaseModel):
    kind: str
    key: str = Field(min_length=1, max_length=500)
    title: str = ""
    body: dict[str, Any] = Field(default_factory=dict)
    text: str | None = None
    status: str | None = None
    source: str = "human"
    source_ref: str | None = None


async def _project(session: AsyncSession, ref: str):
    try:
        return await ProjectService(session).require(ref)
    except ProjectError as e:
        raise_project_error(e)


def _bad(e: pk.KnowledgeError) -> HTTPException:
    return HTTPException(422, detail={"code": "invalid_knowledge", "message": str(e), "kinds": list(KINDS),
                                      "statuses": list(STATUSES), "sources": list(SOURCES)})


@router.get("/projects/{project}/knowledge")
async def get_knowledge(project: str, q: str = "", kinds: str | None = Query(None, description="comma-separated kinds"),
                        budget: int = Query(pk.DEFAULT_BUDGET, ge=1, le=20000), session: AsyncSession = Depends(get_db_session)):
    p = await _project(session, project)
    try:
        return await pk.retrieve(session, p.id, q, [k.strip() for k in (kinds or "").split(",") if k.strip()] or None, budget)
    except pk.KnowledgeError as e:
        raise _bad(e)


@router.post("/projects/{project}/knowledge")
async def upsert_knowledge(project: str, body: KnowledgeUpsert, session: AsyncSession = Depends(get_db_session)):
    p = await _project(session, project)
    try:
        return await pk.upsert(session, p.id, body.kind, body.key, title=body.title, body=body.body, text=body.text,
                               status=body.status, source=body.source, source_ref=body.source_ref)
    except pk.KnowledgeError as e:
        await session.rollback()
        raise _bad(e)


@router.post("/projects/{project}/knowledge/reindex")
async def reindex_knowledge(project: str, session: AsyncSession = Depends(get_db_session)):
    p = await _project(session, project)
    return await pk.reindex_project(session, p.id)

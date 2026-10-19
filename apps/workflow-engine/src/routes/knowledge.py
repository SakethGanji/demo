"""Knowledge API (PLAN-CAPABILITY-GRAPH section 6.1 and 7; CONTRACTS-CAPGRAPH.md section 7).

    GET  /knowledge/graph?project_id=&team_id=&kinds=&focus=&depth=&learned_only=
    GET  /knowledge/nodes/{id}?project_id=&team_id=
    GET  /knowledge/capabilities?project_id=&team_id=
    GET  /knowledge/memory?project_id=&team_id=&limit=&cursor=
    POST /knowledge/understand      {project_id?, team_id?, request}      lexical only; no LLM, no row
    POST /knowledge/rebuild         {project_id?, team_id?, llm_tagging?}
    POST /knowledge/nodes/{id}/accept | /reject   {actor}  (?project_id=)

Team mode: without ``project_id`` a request reads (or rebuilds) the team-wide graph of ``team_id`` (default: the studio's default team),
every project of the team at once. When both are given, ``project_id`` wins, so the per-project behaviour is unchanged.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.dependencies import get_db_session
from ..schemas.knowledge import (
    DEFAULT_TEAM, CapabilitiesResponse, DecideRequest, GraphResponse, MemoryResponse, NodeDetail, NodeView, RebuildRequest, RebuildResponse,
    UnderstandRequest,
)
from ..services import authz, kg_service
from ..services.project_service import resolve_project

router = APIRouter(prefix="/knowledge")


async def _project(session: AsyncSession, ref: str):
    p = await resolve_project(session, ref)
    if p is None:
        raise HTTPException(404, detail={"code": "PROJECT_NOT_FOUND", "message": f"project not found: {ref}"})
    return p


def _http(e: kg_service.KnowledgeError) -> HTTPException:
    return HTTPException(status_code=e.status, detail=e.detail)


@router.get("/graph", response_model=GraphResponse)
async def get_graph(
    request: Request, project_id: str | None = None, team_id: str = Query(DEFAULT_TEAM),
    kinds: str | None = Query(None, description="comma-separated node kinds"),
    focus: str | None = None, depth: int = Query(1, ge=1, le=2), learned_only: bool = False, session: AsyncSession = Depends(get_db_session),
):
    kind_list = [k for k in (kinds or "").split(",") if k] or None
    if project_id is None:
        authz.authorize(request, "knowledge.read", {"team_id": team_id})
        try:
            return await kg_service.graph(session, None, kinds=kind_list, focus=focus, depth=depth, learned_only=learned_only, team_id=team_id)
        except kg_service.KnowledgeError as e:
            raise _http(e)
    authz.authorize(request, "knowledge.read", {"project_id": project_id})
    p = await _project(session, project_id)
    try:
        return await kg_service.graph(session, p.id, kinds=kind_list, focus=focus, depth=depth,
                                      learned_only=learned_only)
    except kg_service.KnowledgeError as e:
        raise _http(e)


@router.get("/nodes/{node_id}", response_model=NodeDetail)
async def get_node(request: Request, node_id: str, project_id: str | None = None, team_id: str = Query(DEFAULT_TEAM),
                   session: AsyncSession = Depends(get_db_session)):
    if project_id is None:
        authz.authorize(request, "knowledge.read", {"team_id": team_id, "node_id": node_id})
        try:
            return await kg_service.node_detail(session, None, node_id, team_id=team_id)
        except kg_service.KnowledgeError as e:
            raise _http(e)
    authz.authorize(request, "knowledge.read", {"project_id": project_id, "node_id": node_id})
    p = await _project(session, project_id)
    try:
        return await kg_service.node_detail(session, p.id, node_id)
    except kg_service.KnowledgeError as e:
        raise _http(e)


@router.get("/capabilities", response_model=CapabilitiesResponse)
async def get_capabilities(request: Request, project_id: str | None = None, team_id: str = Query(DEFAULT_TEAM),
                           session: AsyncSession = Depends(get_db_session)):
    if project_id is None:
        authz.authorize(request, "knowledge.read", {"team_id": team_id})
        try:
            return await kg_service.capabilities(session, None, team_id=team_id)
        except kg_service.KnowledgeError as e:
            raise _http(e)
    authz.authorize(request, "knowledge.read", {"project_id": project_id})
    p = await _project(session, project_id)
    return await kg_service.capabilities(session, p.id)


@router.get("/memory", response_model=MemoryResponse)
async def get_memory(request: Request, project_id: str | None = None, team_id: str = Query(DEFAULT_TEAM),
                     limit: int = Query(50, ge=1, le=200), cursor: str | None = None, session: AsyncSession = Depends(get_db_session)):
    if project_id is None:
        authz.authorize(request, "knowledge.read", {"team_id": team_id})
        try:
            return await kg_service.memory(session, None, limit=limit, cursor=cursor, team_id=team_id)
        except kg_service.KnowledgeError as e:
            raise _http(e)
    authz.authorize(request, "knowledge.read", {"project_id": project_id})
    p = await _project(session, project_id)
    try:
        return await kg_service.memory(session, p.id, limit=limit, cursor=cursor)
    except kg_service.KnowledgeError as e:
        raise _http(e)


@router.post("/understand")
async def post_understand(request: Request, body: UnderstandRequest, session: AsyncSession = Depends(get_db_session)):
    if body.project_id is None:
        authz.authorize(request, "knowledge.read", {"team_id": body.team_id})
        try:
            return await kg_service.understand(session, None, body.request, team_id=body.team_id)
        except kg_service.KnowledgeError as e:
            raise _http(e)
    authz.authorize(request, "knowledge.read", {"project_id": body.project_id})
    p = await _project(session, body.project_id)
    return await kg_service.understand(session, p, body.request)


@router.post("/rebuild", response_model=RebuildResponse)
async def post_rebuild(request: Request, body: RebuildRequest, session: AsyncSession = Depends(get_db_session)):
    if body.project_id is None:
        authz.authorize(request, "knowledge.write", {"team_id": body.team_id})
        try:
            return await kg_service.rebuild(session, None, body.llm_tagging, team_id=body.team_id)
        except kg_service.KnowledgeError as e:
            raise _http(e)
    authz.authorize(request, "knowledge.write", {"project_id": body.project_id})
    p = await _project(session, body.project_id)
    return await kg_service.rebuild(session, p.id, body.llm_tagging)


@router.post("/nodes/{node_id}/accept", response_model=NodeView)
async def accept_node(request: Request, node_id: str, body: DecideRequest | None = None, project_id: str | None = None,
                      session: AsyncSession = Depends(get_db_session)):
    return await _decide(request, node_id, project_id, "active", session)


@router.post("/nodes/{node_id}/reject", response_model=NodeView)
async def reject_node(request: Request, node_id: str, body: DecideRequest | None = None, project_id: str | None = None,
                      session: AsyncSession = Depends(get_db_session)):
    return await _decide(request, node_id, project_id, "rejected", session)


async def _decide(request: Request, node_id: str, project_id: str | None, to: str, session: AsyncSession):
    authz.authorize(request, "knowledge.write", {"node_id": node_id, "project_id": project_id})
    pid = (await _project(session, project_id)).id if project_id else None
    try:
        return await kg_service.decide(session, pid, node_id, to)
    except kg_service.KnowledgeError as e:
        raise _http(e)

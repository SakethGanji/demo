"""Broker + SDK routes (BACKEND-DESIGN §3.4, phase 4)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from ..broker.service import BrokerService
from ..broker.tokens import BrokerClaims, registry
from ..core.dependencies import get_db_session
from ..services.project_service import ProjectError, ProjectService, env_kind, env_names
from .projects import raise_project_error

router = APIRouter()


class BrokerCallRequest(BaseModel):
    function: str | None = None
    node: str | None = None
    parameters: dict[str, Any] = {}
    items: list[Any] | None = None


class BrokerTokenRequest(BaseModel):
    environment: str | None = None
    max_calls: int | None = None  # optional lower call budget (≤ server max)


def _bearer(request: Request) -> str | None:
    h = request.headers.get("authorization") or ""
    if h.lower().startswith("bearer "):
        return h.split(" ", 1)[1].strip()
    return None


@router.post("/broker/call")
async def broker_call(body: BrokerCallRequest, request: Request):
    status, payload = await BrokerService().call_with_token(
        _bearer(request), body.function, body.node, body.parameters, body.items)
    return JSONResponse(payload, status_code=status)


@router.get("/sdk/functions")
async def sdk_functions():
    from ..sdk.generate import catalogue
    return catalogue()


@router.get("/sdk/python")
async def sdk_python():
    from ..sdk.generate import generate_python_sdk
    return generate_python_sdk()


@router.get("/sdk/python.zip")
async def sdk_python_zip():
    from ..sdk.generate import generate_python_sdk, sdk_zip
    v = generate_python_sdk()["version"]
    return Response(sdk_zip(), media_type="application/zip",
                    headers={"Content-Disposition": f'attachment; filename="studio-sdk-{v}.zip"'})


@router.post("/projects/{project}/broker-tokens", status_code=201)
async def dev_broker_token(project: str, body: BrokerTokenRequest, session: AsyncSession = Depends(get_db_session)):
    try:
        p = await ProjectService(session).require(project)
    except ProjectError as e:
        raise_project_error(e)
    env = body.environment or p.default_environment
    if env not in env_names(p):
        raise_project_error(ProjectError(422, "unknown_environment", f"environment '{env}' is not in this project"))
    if env_kind(p, env) != "draft":
        raise_project_error(ProjectError(403, "release_environment", "developer broker tokens are only issued for draft environments"))
    import time
    from datetime import datetime
    claims = BrokerClaims(project_id=p.id, environment=env, execution_id=None, origin="dev", team_id=p.team_id)
    if body.max_calls:
        claims.max_calls = max(1, min(body.max_calls, claims.max_calls))
    token = registry.mint(claims, ttl_s=3600)
    return {"token": token, "environment": env,
            "expires_at": datetime.fromtimestamp(claims.expires_at).isoformat(timespec="seconds")}


@router.get("/projects/{project}/broker-calls")
async def list_broker_calls(project: str, execution_id: str | None = None, session: AsyncSession = Depends(get_db_session)):
    try:
        p = await ProjectService(session).require(project)
    except ProjectError as e:
        raise_project_error(e)
    from ..repositories.broker_call_repository import BrokerCallRepository
    return await BrokerCallRepository(session).list(execution_id=execution_id, project_id=p.id)

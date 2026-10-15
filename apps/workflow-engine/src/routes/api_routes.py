"""API section management routes (BACKEND-DESIGN §3.5, phase 5) + project router (/api/p/...)."""

from __future__ import annotations

import time
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.dependencies import get_db_session
from ..core.identity import get_actor
from ..services.api_key_service import ApiKeyService
from ..services.api_route_service import ApiRouteService, route_spec
from ..services.project_service import ProjectError, ProjectService, env_kind, env_names
from ..services.route_dispatcher import DispatchResult, RouteDispatcher, RouteRequest, parse_body
from ..services.spec_tools import build_openapi, diff_specs, spec_hash
from .projects import raise_project_error

router = APIRouter(prefix="/projects/{project}")
project_router = APIRouter(prefix="/api/p")


class RouteBody(BaseModel):
    method: str | None = None
    path: str | None = None
    name: str | None = None
    auth: str | None = None
    description: str | None = None
    request_schema: dict[str, Any] | None = None
    response_schema: dict[str, Any] | None = None
    response_mode: str | None = None
    validate_request: bool | None = None
    timeout_s: int | None = None
    handler: dict[str, Any] | None = None


class RouteTestBody(BaseModel):
    params: dict[str, Any] = {}
    query: dict[str, Any] = {}
    headers: dict[str, str] = {}
    body: Any = None
    environment: str | None = None
    apply_schemas: bool = False


class ApiKeyBody(BaseModel):
    name: str
    environment: str | None = None


async def _project(project: str, session: AsyncSession):
    try:
        return await ProjectService(session).require(project)
    except ProjectError as e:
        raise_project_error(e)


def _body(b: RouteBody) -> dict[str, Any]:
    d = b.model_dump(exclude_unset=True)
    for k in ("auth", "response_mode", "timeout_s", "validate_request"):
        if k in d and d[k] is None:
            d.pop(k)
    return d


async def _guard(coro):
    try:
        return await coro
    except ProjectError as e:
        raise_project_error(e)


# ── routes CRUD ──────────────────────────────────────────────────────────────


@router.get("/routes")
async def list_routes(project: str, session: AsyncSession = Depends(get_db_session)):
    p = await _project(project, session)
    return await ApiRouteService(session).list(p)


@router.post("/routes", status_code=201)
async def create_route(project: str, body: RouteBody, request: Request, session: AsyncSession = Depends(get_db_session)):
    p = await _project(project, session)
    return await _guard(ApiRouteService(session).create(p, _body(body), actor=get_actor(request)))


@router.get("/routes/{rid}")
async def get_route(project: str, rid: str, session: AsyncSession = Depends(get_db_session)):
    p = await _project(project, session)
    svc = ApiRouteService(session)
    return await svc.to_dict(p, await _guard(svc.get(p, rid)))


@router.put("/routes/{rid}")
async def update_route(project: str, rid: str, body: RouteBody, request: Request, dry_run: bool = False,
                       session: AsyncSession = Depends(get_db_session)):
    p = await _project(project, session)
    return await _guard(ApiRouteService(session).update(p, rid, _body(body), dry_run=dry_run, actor=get_actor(request)))


@router.delete("/routes/{rid}")
async def delete_route(project: str, rid: str, dry_run: bool = False, session: AsyncSession = Depends(get_db_session)):
    p = await _project(project, session)
    return await _guard(ApiRouteService(session).delete(p, rid, dry_run=dry_run))


@router.post("/routes/{rid}/test")
async def test_route(project: str, rid: str, body: RouteTestBody, request: Request,
                     session: AsyncSession = Depends(get_db_session)):
    """Run the route's handler in a draft env and infer schemas (G7, G8)."""
    from ..services.schema_inference import infer_json_schema

    p = await _project(project, session)
    svc = ApiRouteService(session)
    r = await _guard(svc.get(p, rid))
    env = body.environment or p.default_environment
    if env not in env_names(p) or env_kind(p, env) != "draft":
        raise_project_error(ProjectError(422, "environment_not_draft", f"'{env}' is not a draft environment of this project"))
    path = r.path
    for k, v in (body.params or {}).items():
        path = path.replace("{" + k + "}", str(v))
    started = time.perf_counter()
    disp = RouteDispatcher(session)
    # Dispatch exactly this route (draft row) with auth bypassed and the chosen env.
    spec = route_spec(r)
    req = RouteRequest(method=r.method, path=path, query=dict(body.query or {}),
                       headers={k.lower(): v for k, v in (body.headers or {}).items()}, body=body.body)
    orig = disp.route_set

    async def only_this(_p, _e):
        return [spec], None
    disp.route_set = only_this  # type: ignore[assignment]
    res = await disp.handle(p, req, forced_env=env)
    disp.route_set = orig  # type: ignore[assignment]
    duration = round((time.perf_counter() - started) * 1000, 2)

    req_sample: dict[str, Any] = {"params": body.params or {}, "query": body.query or {}}
    if body.body is not None:
        req_sample["body"] = body.body
    inferred = {"request_schema": infer_json_schema(req_sample),
                "response_schema": infer_json_schema(res.body) if isinstance(res.body, (dict, list)) else None}
    out: dict[str, Any] = {"status": res.status, "headers": {"content-type": res.content_type, **res.headers},
                           "body": res.body, "duration_ms": duration, "execution_id": res.execution_id,
                           "inferred": inferred, "applied": False,
                           # review D16: structured, redacted handler error (null on success)
                           "error": res.error,
                           # review G-UI-4: the run's trace inline (same as GET …/runs/{id}.trace)
                           "trace": await run_trace(session, res.execution_id) if res.execution_id else None}
    if body.apply_schemas and 200 <= res.status < 300:
        upd = {"request_schema": inferred["request_schema"]}
        if inferred["response_schema"] is not None:
            upd["response_schema"] = inferred["response_schema"]
        result = await _guard(svc.update(p, rid, upd, actor=get_actor(request), allow_code=True))
        out.update({"applied": True, **result})
    return out


async def run_trace(session: AsyncSession, execution_id: str) -> dict[str, Any]:
    """The run's trace with broker calls expanded — shared with GET …/runs/{id}."""
    from ..db.models import ExecutionModel
    from ..repositories.broker_call_repository import BrokerCallRepository

    row = await session.get(ExecutionModel, execution_id)
    if row is not None:
        await session.refresh(row)
    trace = dict(((row.exec_metadata or {}) if row else {}).get("trace") or {})
    trace["broker_calls"] = await BrokerCallRepository(session).list(execution_id=execution_id)
    return trace


# ── spec ─────────────────────────────────────────────────────────────────────


@router.get("/openapi.json")
async def openapi(project: str, environment: str | None = None, session: AsyncSession = Depends(get_db_session)):
    p = await _project(project, session)
    env = environment or p.default_environment
    specs, release = await RouteDispatcher(session).route_set(p, env)
    if release is not None and release.snapshot.get("spec"):
        spec = release.snapshot["spec"]
    else:
        latest = await ApiRouteService(session).latest_revision(p.id)
        spec = build_openapi(p, specs, latest.revision if latest else None)
    return JSONResponse(spec, headers={"ETag": f'"{spec_hash(spec)}"'})


@router.get("/spec/revisions")
async def spec_revisions(project: str, session: AsyncSession = Depends(get_db_session)):
    p = await _project(project, session)
    return await ApiRouteService(session).revisions(p)


@router.get("/spec/diff", name="spec_diff")
async def spec_diff(project: str, request: Request, session: AsyncSession = Depends(get_db_session)):
    p = await _project(project, session)
    svc = ApiRouteService(session)
    try:
        frm, to = int(request.query_params.get("from", "0")), int(request.query_params["to"])
    except (KeyError, ValueError):
        raise_project_error(ProjectError(422, "invalid_revision", "from and to must be revision numbers"))
    old = await _guard(svc.revision_spec(p, frm)) if frm > 0 else None
    new = await _guard(svc.revision_spec(p, to))
    return {"from": frm, "to": to, "changes": await svc._with_affected(p, diff_specs(old, new))}


# ── API keys ─────────────────────────────────────────────────────────────────


@router.get("/api-keys")
async def list_keys(project: str, session: AsyncSession = Depends(get_db_session)):
    p = await _project(project, session)
    return await ApiKeyService(session).list(p)


@router.post("/api-keys", status_code=201)
async def create_key(project: str, body: ApiKeyBody, request: Request, session: AsyncSession = Depends(get_db_session)):
    p = await _project(project, session)
    return await _guard(ApiKeyService(session).create(p, body.name, body.environment, actor=get_actor(request)))


@router.delete("/api-keys/{key_id}")
async def revoke_key(project: str, key_id: str, session: AsyncSession = Depends(get_db_session)):
    p = await _project(project, session)
    await _guard(ApiKeyService(session).revoke(p, key_id))
    return {"success": True}


# ── app ↔ API (phase 6) ──────────────────────────────────────────────────────


class StubsBody(BaseModel):
    app_id: str | None = None
    routes: list[dict[str, Any]]


async def client_source(session: AsyncSession, p, environment: str | None = None) -> tuple[str, str]:
    from ..services.client_codegen import generate_client
    env = environment or p.default_environment
    specs, release = await RouteDispatcher(session).route_set(p, env)
    if release is not None:
        rev, h = None, release.snapshot.get("spec_hash") or ""
    else:
        latest = await ApiRouteService(session).latest_revision(p.id)
        rev = latest.revision if latest else None
        h = spec_hash(build_openapi(p, specs, rev))
    return generate_client(p.slug, specs, rev, h), h


@router.get("/client.ts")
async def client_ts(project: str, environment: str | None = None, session: AsyncSession = Depends(get_db_session)):
    p = await _project(project, session)
    src, h = await client_source(session, p, environment)
    return Response(src, media_type="text/typescript", headers={"ETag": f'"{h}"'})


@router.get("/routes/{rid}/used-by")
async def route_used_by(project: str, rid: str, session: AsyncSession = Depends(get_db_session)):
    from ..services.api_usage import ApiUsageService
    p = await _project(project, session)
    await _guard(ApiRouteService(session).get(p, rid))
    return await ApiUsageService(session).used_by(p, rid)


@router.post("/routes/stubs", status_code=201)
async def create_stubs(project: str, body: StubsBody, request: Request, session: AsyncSession = Depends(get_db_session)):
    from ..db.models import AppModel
    from ..services.api_route_service import path_shape
    p = await _project(project, session)
    if body.app_id:
        app = await session.get(AppModel, body.app_id)
        if not app or app.project_id != p.id:
            raise_project_error(ProjectError(404, "app_not_found", "App not found in this project"))
    svc = ApiRouteService(session)
    created, existing = [], []
    for spec in body.routes:
        method = str(spec.get("method") or "GET").upper()
        path = spec.get("path")
        match = next((r for r in await svc.rows(p.id)
                      if r.method == method and isinstance(path, str) and r.path_shape == path_shape(path)), None)
        if match:
            existing.append(await svc.to_dict(p, match))
            continue
        payload = {k: spec[k] for k in ("name", "description", "request_schema", "response_schema", "auth") if spec.get(k) is not None}
        res = await _guard(svc.create(p, {**payload, "method": method, "path": path, "handler": {"type": "stub"}},
                                      actor=get_actor(request), source="app_stub"))
        created.append(res["route"])
    return {"created": created, "existing": existing}


# ── preview tokens (phase 6; used by the studio preview iframe) ───────────────


@router.post("/apps/{app_id}/preview-token", status_code=201)
async def preview_token(project: str, app_id: str, session: AsyncSession = Depends(get_db_session)):
    from datetime import datetime

    from ..core.public_tokens import issue_token, verify_token
    from ..db.models import AppModel

    p = await _project(project, session)
    app = await session.get(AppModel, app_id)
    if not app or app.project_id != p.id:
        raise_project_error(ProjectError(404, "app_not_found", "App not found in this project"))
    env = p.default_environment
    token = issue_token(app.id, 0, ttl_seconds=3600, env=env, preview=True)
    exp = verify_token(token).exp
    return {"token": token, "environment": env, "expires_at": utc_iso(exp)}  # review C13: UTC with Z


def utc_iso(epoch: float) -> str:
    from datetime import datetime, timezone
    return datetime.fromtimestamp(epoch, tz=timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


# ── the project router ───────────────────────────────────────────────────────


def to_response(res: DispatchResult) -> Response:
    headers = dict(res.headers)
    if res.execution_id:
        headers["X-Execution-Id"] = res.execution_id
    if isinstance(res.body, (bytes, bytearray)):
        return Response(bytes(res.body), status_code=res.status, headers=headers, media_type=res.content_type)
    if res.content_type and not res.content_type.startswith("application/json"):
        return Response("" if res.body is None else str(res.body), status_code=res.status, headers=headers,
                        media_type=res.content_type)
    return JSONResponse(res.body, status_code=res.status, headers=headers)


@project_router.api_route("/{project}/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
                          include_in_schema=False)
@project_router.api_route("/{project}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"], include_in_schema=False)
async def project_api(project: str, request: Request, path: str = "", session: AsyncSession = Depends(get_db_session)):
    from ..services.project_service import resolve_project

    p = await resolve_project(session, project)
    if p is None:
        return JSONResponse({"error": "project_not_found"}, status_code=404)
    raw = await request.body()
    req = RouteRequest(
        method=request.method, path="/" + path if path else "/",
        query=dict(request.query_params),
        headers={k.lower(): v for k, v in request.headers.items()},
        body=parse_body(raw, request.headers.get("content-type", "")),
    )
    return to_response(await RouteDispatcher(session).handle(p, req))

"""AI app composer routes (PLAN-CAPABILITY-GRAPH section 5.1; ``services/app_composer``).

    POST /api/ai/apps                       201  request -> AppBuildView, status `clarify` (open questions) or `planned` (the plan)
    POST /api/ai/apps/{id}/answers          200  answer the questions -> `planned` (409 bad_state, 422 answer_invalid)
    PUT  /api/ai/apps/{id}/plan             200  edit the plan, re-validated APP001-APP008, no model call (422 plan_invalid)
    POST /api/ai/apps/{id}/approve-plan     200  one spec_draft proposal per part, children first -> `specs`
    POST /api/ai/apps/{id}/build-next       200  build the next part (one per call) -> `building` | `ready`; the view plus `built`
    POST /api/ai/apps/{id}/keep             200  install the whole app as one bundle -> `kept` (409 parts_not_ready | tests_failing, 422 bundle_invalid | install_failed)
    POST /api/ai/apps/{id}/discard          200  discard every part proposal; nothing was saved
    GET  /api/ai/apps/{id}                  200  AppBuildView
    GET  /api/ai/apps?project_id=           200  {apps, next_cursor}

Each part is a normal proposal (`parts[].proposal_id`): review, edit and approve its spec, and play its build, with the existing
`/api/ai/proposals/{id}` endpoints. Errors use the house envelope ``{"detail": {"code", "message", ...}}``. Pass `project_id` as a query
parameter on the `/apps/{id}` routes to scope them (an app of another project is 404).
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Header, HTTPException, Query

from ..schemas.ai_apps import AppActorBody, AppAnswersBody, CreateAppBody, KeepAppBody, PutPlanBody
from ..services import ai_builder, app_composer
from ..services.proposal_sandbox import ProposalError

router = APIRouter()

ActorHeader = Annotated[str | None, Header(alias="X-Actor-Name")]
ProjectQuery = Annotated[str | None, Query(description="Scope the app build to this project (404 if it belongs to another).")]


def _fail(e: ProposalError) -> HTTPException:
    return HTTPException(status_code=e.http_status, detail=e.detail())


def _actor(body_actor: str | None, header: str | None) -> str:
    return ai_builder.clean_actor(body_actor if (body_actor or "").strip() else header)


@router.post("/ai/apps", status_code=201)
async def create_app(body: CreateAppBody, x_actor_name: ActorHeader = None) -> dict[str, Any]:
    try:
        return await app_composer.create(body.project_id, body.request, _actor(body.actor, x_actor_name), answers=body.answers, use_llm=body.use_llm)
    except ProposalError as e:
        raise _fail(e)


@router.get("/ai/apps")
async def list_apps(project_id: str, status: str | None = None, limit: int = Query(50, ge=1, le=200), cursor: str | None = None) -> dict[str, Any]:
    try:
        return await app_composer.list_apps(project_id, status=status, limit=limit, cursor=cursor)
    except ProposalError as e:
        raise _fail(e)


@router.get("/ai/apps/{app_id}")
async def get_app(app_id: str, project_id: ProjectQuery = None) -> dict[str, Any]:
    try:
        return await app_composer.get_view(app_id, project_id)
    except ProposalError as e:
        raise _fail(e)


@router.post("/ai/apps/{app_id}/answers")
async def post_answers(app_id: str, body: AppAnswersBody, project_id: ProjectQuery = None, x_actor_name: ActorHeader = None) -> dict[str, Any]:
    try:
        return await app_composer.answer(app_id, body.answers, _actor(body.actor, x_actor_name), project_id)
    except ProposalError as e:
        raise _fail(e)


@router.put("/ai/apps/{app_id}/plan")
async def put_plan(app_id: str, body: PutPlanBody, project_id: ProjectQuery = None, x_actor_name: ActorHeader = None) -> dict[str, Any]:
    try:
        return await app_composer.put_plan(app_id, body.plan, _actor(body.actor, x_actor_name), project_id)
    except ProposalError as e:
        raise _fail(e)


@router.post("/ai/apps/{app_id}/approve-plan")
async def approve_plan(app_id: str, body: AppActorBody | None = None, project_id: ProjectQuery = None, x_actor_name: ActorHeader = None) -> dict[str, Any]:
    try:
        return await app_composer.approve_plan(app_id, _actor(body.actor if body else None, x_actor_name), project_id)
    except ProposalError as e:
        raise _fail(e)


@router.post("/ai/apps/{app_id}/build-next")
async def build_next(app_id: str, body: AppActorBody | None = None, project_id: ProjectQuery = None, x_actor_name: ActorHeader = None) -> dict[str, Any]:
    try:
        return await app_composer.build_next(app_id, _actor(body.actor if body else None, x_actor_name), project_id)
    except ProposalError as e:
        raise _fail(e)


@router.post("/ai/apps/{app_id}/keep")
async def keep_app(app_id: str, body: KeepAppBody | None = None, project_id: ProjectQuery = None, x_actor_name: ActorHeader = None) -> dict[str, Any]:
    body = body or KeepAppBody()
    try:
        return await app_composer.keep(app_id, _actor(body.actor, x_actor_name), accept_failing=body.accept_failing, project_id=project_id)
    except ProposalError as e:
        raise _fail(e)


@router.post("/ai/apps/{app_id}/discard")
async def discard_app(app_id: str, body: AppActorBody | None = None, project_id: ProjectQuery = None, x_actor_name: ActorHeader = None) -> dict[str, Any]:
    try:
        return await app_composer.discard(app_id, _actor(body.actor if body else None, x_actor_name), project_id)
    except ProposalError as e:
        raise _fail(e)

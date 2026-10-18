"""Stage 6 AI builder routes (DESIGN-STAGE6-AI-BUILDER section 4).

    POST /api/ai/builder/spec                       201  request -> spec_draft proposal (1-2 model calls)
    PUT  /api/ai/proposals/{id}/spec                200  edit the spec (no model call)
    POST /api/ai/proposals/{id}/answers             200  answer a `clarify` proposal's questions -> spec_draft (409 bad_state, 422 answer_invalid)
    POST /api/ai/proposals/{id}/spec/approve        200  422 spec_invalid while errors remain
    POST /api/ai/proposals/{id}/build               200  runs the loop, ready | failed (502 ai_failed when round 0 got no answer)
    POST /api/ai/proposals/{id}/revise              201  child proposal built from this one's result
    GET  /api/ai/proposals/{id}                     200  ProposalView
    GET  /api/ai/proposals?project_id=              200  audit list
    GET  /api/ai/proposals/{id}/events?after=       200  progress / audit events
    POST /api/ai/proposals/{id}/keep | undo | discard     200
    POST /api/workflows/{id}/nodes/{name}/ai        200 (explain) | 201 (proposal)
    GET  /api/ai/builder/config                     200
    GET  /api/ai/builder/api-refs?project_id=       200  the project's api_ref strings (the spec step picker)

Errors use the house envelope ``{"detail": {"code", "message", ...}}``. A proposal of another project is 404: pass ``project_id`` as a
query parameter on the ``/proposals/{id}`` routes to scope them.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Header, HTTPException, Query

from ..schemas.ai_builder import ActorBody, AnswersBody, KeepBody, NodeAIBody, PutSpecBody, ReviseBody, SpecRequestBody
from ..services import ai_builder, ai_node_assist
from ..services.proposal_sandbox import ProposalError

router = APIRouter()

ActorHeader = Annotated[str | None, Header(alias="X-Actor-Name")]
ProjectQuery = Annotated[str | None, Query(description="Scope the proposal to this project (404 if it belongs to another).")]


def _fail(e: ProposalError) -> HTTPException:
    return HTTPException(status_code=e.http_status, detail=e.detail())


def _actor(body_actor: str | None, header: str | None) -> str:
    return ai_builder.clean_actor(body_actor if (body_actor or "").strip() else header)


@router.get("/ai/builder/config")
async def builder_config() -> dict[str, Any]:
    return ai_builder.config_view()


@router.get("/ai/builder/api-refs")
async def builder_api_refs(project_id: str) -> dict[str, Any]:
    try:
        return await ai_builder.api_refs(project_id)
    except ProposalError as e:
        raise _fail(e)


@router.post("/ai/builder/spec", status_code=201)
async def create_spec(body: SpecRequestBody, x_actor_name: ActorHeader = None) -> dict[str, Any]:
    try:
        return {"proposal": await ai_builder.draft_spec(body.project_id, body.request, _actor(body.actor, x_actor_name),
                                                        workflow_id=body.workflow_id, model=body.model,
                                                        **({"use_graph": True, "clarify": body.clarify, "answers": body.answers} if body.use_graph else {}))}
    except ProposalError as e:
        raise _fail(e)


@router.get("/ai/proposals")
async def list_proposals(project_id: str, workflow_id: str | None = None, status: str | None = None, limit: int = Query(50, ge=1, le=200),
                         cursor: str | None = None) -> dict[str, Any]:
    try:
        return await ai_builder.list_proposals(project_id, workflow_id=workflow_id, status=status, limit=limit, cursor=cursor)
    except ProposalError as e:
        raise _fail(e)


@router.get("/ai/proposals/{proposal_id}")
async def get_proposal(proposal_id: str, project_id: ProjectQuery = None) -> dict[str, Any]:
    try:
        return await ai_builder.get_view(proposal_id, project_id)
    except ProposalError as e:
        raise _fail(e)


@router.get("/ai/proposals/{proposal_id}/events")
async def proposal_events(proposal_id: str, after: int = Query(0, ge=0), project_id: ProjectQuery = None) -> dict[str, Any]:
    try:
        return await ai_builder.list_events(proposal_id, after, project_id)
    except ProposalError as e:
        raise _fail(e)


@router.post("/ai/proposals/{proposal_id}/answers")
async def post_answers(proposal_id: str, body: AnswersBody, project_id: ProjectQuery = None, x_actor_name: ActorHeader = None) -> dict[str, Any]:
    try:
        return await ai_builder.answer_questions(proposal_id, body.answers, _actor(body.actor, x_actor_name), project_id)
    except ProposalError as e:
        raise _fail(e)


@router.put("/ai/proposals/{proposal_id}/spec")
async def put_spec(proposal_id: str, body: PutSpecBody, project_id: ProjectQuery = None, x_actor_name: ActorHeader = None) -> dict[str, Any]:
    try:
        return await ai_builder.put_spec(proposal_id, body.spec, _actor(body.actor, x_actor_name), project_id)
    except ProposalError as e:
        raise _fail(e)


@router.post("/ai/proposals/{proposal_id}/spec/approve")
async def approve_spec(proposal_id: str, body: ActorBody | None = None, project_id: ProjectQuery = None, x_actor_name: ActorHeader = None) -> dict[str, Any]:
    try:
        return await ai_builder.approve_spec(proposal_id, _actor(body.actor if body else None, x_actor_name), project_id)
    except ProposalError as e:
        raise _fail(e)


@router.post("/ai/proposals/{proposal_id}/build")
async def build(proposal_id: str, body: ActorBody | None = None, project_id: ProjectQuery = None, x_actor_name: ActorHeader = None) -> dict[str, Any]:
    try:
        return await ai_builder.build(proposal_id, _actor(body.actor if body else None, x_actor_name), project_id)
    except ProposalError as e:
        raise _fail(e)


@router.post("/ai/proposals/{proposal_id}/revise", status_code=201)
async def revise(proposal_id: str, body: ReviseBody, project_id: ProjectQuery = None, x_actor_name: ActorHeader = None) -> dict[str, Any]:
    try:
        return await ai_builder.revise(proposal_id, body.instruction, _actor(body.actor, x_actor_name), project_id)
    except ProposalError as e:
        raise _fail(e)


@router.post("/ai/proposals/{proposal_id}/keep")
async def keep(proposal_id: str, body: KeepBody | None = None, project_id: ProjectQuery = None, x_actor_name: ActorHeader = None) -> dict[str, Any]:
    body = body or KeepBody()
    try:
        return await ai_builder.keep(proposal_id, _actor(body.actor, x_actor_name), accept_failing=body.accept_failing, name=body.name,
                                     project_id=project_id)
    except ProposalError as e:
        raise _fail(e)


@router.post("/ai/proposals/{proposal_id}/discard")
async def discard(proposal_id: str, body: ActorBody | None = None, project_id: ProjectQuery = None, x_actor_name: ActorHeader = None) -> dict[str, Any]:
    try:
        return await ai_builder.discard(proposal_id, _actor(body.actor if body else None, x_actor_name), project_id)
    except ProposalError as e:
        raise _fail(e)


@router.post("/ai/proposals/{proposal_id}/undo")
async def undo(proposal_id: str, body: ActorBody | None = None, project_id: ProjectQuery = None, x_actor_name: ActorHeader = None) -> dict[str, Any]:
    try:
        return await ai_builder.undo(proposal_id, _actor(body.actor if body else None, x_actor_name), project_id)
    except ProposalError as e:
        raise _fail(e)


@router.post("/workflows/{workflow_id}/nodes/{node_name:path}/ai")
async def node_ai(workflow_id: str, node_name: str, body: NodeAIBody, x_actor_name: ActorHeader = None):
    """explain -> 200 ``{answer, proposal_id}``; every other task -> 201 ``ProposalView`` (kind node_assist)."""
    from fastapi.responses import JSONResponse

    try:
        status, payload = await ai_node_assist.run(workflow_id, node_name, body.task, _actor(body.actor, x_actor_name),
                                                   field=body.field, instruction=body.instruction, execution_id=body.execution_id)
    except ProposalError as e:
        raise _fail(e)
    return JSONResponse(status_code=status, content=payload)

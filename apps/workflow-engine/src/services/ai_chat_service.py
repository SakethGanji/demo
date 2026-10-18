"""The editor's AI chat (DESIGN-STAGE6-AI-BUILDER section 8): it answers questions and it proposes changes, it never saves anything.

``POST /api/ai/chat`` streams SSE. One message becomes one of four routes:

* ``explain``  a read-only answer, streamed as ``text``.
* ``build``    no workflow yet: ``draft_spec`` makes a ``spec_draft`` proposal; the event ``spec`` carries it and the person approves the
               spec (then builds) through the builder endpoints.
* ``modify`` / ``fix``  a workflow is open: ``ai_builder.chat_change`` makes a ``modify`` proposal on the stored draft, builds it once
               behind the same check-and-test gate as everything else, and the event ``proposal`` says how it went. Keep is the
               person's click in the review panel.

There is no write tool here and no ``operations`` event: whatever changes a workflow is a proposal that a person reviews. Every model call
goes through ``engine/wf_ai`` (model allowlist, call budget, prompt-size limit) with the payload scrubbed by ``engine/ai_policy`` first,
so secrets and PII (card numbers, IBANs, ...) in the message, the history or the workflow never reach the model.

SSE events (the envelope is unchanged: ``{"event", "data": json}``): ``phase``, ``route``, ``text``, ``spec``, ``proposal``, ``error``
(``{message, code, proposal_id?}``), ``done``.
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any, AsyncGenerator

from ..engine import ai_policy, wf_ai
from ..engine.wf_ai import AIError, Meter
from ..schemas.ai_chat import AIChatRequest
from . import ai_builder as B
from . import ai_builder_prompts as prompts
from . import ai_grounding
from . import proposal_sandbox as sb
from .proposal_sandbox import ProposalError, ProposalNotFound

if TYPE_CHECKING:
    from ..engine.node_registry import NodeRegistryClass

logger = logging.getLogger(__name__)

ROUTES = ("build", "modify", "explain", "fix")
MAX_HISTORY_TURNS = 10
MAX_TURN_CHARS = 2000

# The read-only lookups the chat may use to build its context. There is deliberately no save / update / delete / execute here: a test
# (tests/unit/test_stage6_b5_chat.py) asserts that every name is a ``get_*`` or ``validate_workflow``.
READ_ONLY_TOOLS = ("get_node_catalog", "get_node_schema", "get_workflow", "validate_workflow")


class AIChatService:
    """Routes a chat message and answers or proposes. Holds no write access to workflows."""

    def __init__(self, node_registry: "NodeRegistryClass | None" = None, workflow_service: Any = None) -> None:
        # ``workflow_service`` is accepted for the dependency wiring and never used: this service cannot write.
        self._registry = node_registry

    # ── read-only tools (context for the model) ─────────────────────────────────────────

    def tool_names(self) -> tuple[str, ...]:
        return READ_ONLY_TOOLS

    def _tool_get_node_catalog(self, ctx: "B.Ctx | None", types: set[str]) -> list[dict[str, Any]]:
        return ai_grounding.catalog_subset(None, ctx.catalog if ctx else None, extra_types=types)

    def _tool_get_node_schema(self, node_type: str) -> dict[str, Any] | None:
        if self._registry is None:
            return None
        info = self._registry.get_node_type_info(node_type)
        if info is None:
            return None
        return {"type": info.type, "description": info.description, "group": info.group, "properties": info.properties,
                "inputs": info.inputs, "outputs": info.outputs}

    @staticmethod
    def _tool_get_workflow(base: dict[str, Any] | None) -> dict[str, Any] | None:
        return B.compact_definition(base)

    @staticmethod
    def _tool_validate_workflow(base: dict[str, Any] | None, ctx: "B.Ctx | None") -> list[dict[str, Any]]:
        if base is None or ctx is None:
            return []
        return B._findings_for_model(B.check_all(base, ctx).get("findings") or [], 20)

    # ── entry point ─────────────────────────────────────────────────────────────────────

    async def stream_chat(self, request: AIChatRequest, actor: str | None = None) -> AsyncGenerator[dict[str, Any], None]:
        try:
            async for ev in self._stream(request, actor or request.actor):
                yield ev
        except ProposalError as e:
            yield _error(e.message, e.code, **{k: v for k, v in e.extra.items() if k == "proposal_id"})
        except AIError as e:
            he = B.ai_error_to_http(e)
            yield _error(he.message, he.code)
        except Exception:  # noqa: BLE001 - the stream must end cleanly
            logger.exception("AI chat stream error")
            yield _error("An error occurred while processing your request.", "internal_error")
        yield _sse("done", {"type": "done"})

    async def _stream(self, request: AIChatRequest, actor: str | None) -> AsyncGenerator[dict[str, Any], None]:
        message = (request.message or "").strip()
        if not message:
            raise B.RequestInvalid("Say what you want to know or change.")
        if len(message) > B.MAX_REQUEST_CHARS:
            raise B.RequestInvalid(f"The message is longer than {B.MAX_REQUEST_CHARS} characters.")
        cfg = wf_ai.config()
        B.check_model(cfg.model)            # the residency policy first: before any scrub or prompt work

        base, ctx, project_ref = await self._open_context(request)
        hint = request.mode_hint
        route: str | None = {"generate": "build", "modify": "modify", "fix": "fix"}.get(hint)
        node = request.selected_node
        answer = ""
        if route is None:                    # auto or explain: one WF_AI:CHAT_ROUTE call (it answers a question in the same reply)
            yield _sse("phase", {"type": "phase", "phase": "routing", "message": "Reading your message..."})
            route, node, answer = await self._route(request, message, base, ctx, cfg)
        route = self._possible(route, base is not None, project_ref is not None)
        yield _sse("route", {"type": "route", "route": route, "node": node})

        if route == "explain":
            if not answer.strip():
                raise B.AIFailed("The model did not produce an answer.", ai_code="bad_json")
            yield _sse("text", {"type": "text", "content": answer.strip()})
            return

        who = B.clean_actor(actor)           # a change or a spec names who asked (X-Actor-Name or `actor`)
        if route == "build":
            if not project_ref:
                raise B.RequestInvalid("Open a project to build a workflow: send `project_id`.")
            yield _sse("phase", {"type": "phase", "phase": "spec", "message": "Writing the spec..."})
            view = await B.draft_spec(project_ref, message, who)
            yield _sse("spec", {"type": "spec", "proposal_id": view["id"], "status": view["status"], "spec": view["spec"]})
            return

        yield _sse("phase", {"type": "phase", "phase": "building", "message": "Making the change on a copy. Nothing is saved until you keep it."})
        view = await B.chat_change(request.workflow_id or "", message, who, project_id=request.project_id, node=node)
        yield _sse("proposal", _proposal_event(view))

    # ── context ─────────────────────────────────────────────────────────────────────────

    async def _open_context(self, request: AIChatRequest) -> tuple[dict[str, Any] | None, "B.Ctx | None", str | None]:
        """``(stored draft or None, project ctx or None, project ref)``. A workflow of another project is 404."""
        from ..repositories.workflow_repository import WorkflowRepository

        base, project_ref = None, request.project_id
        if request.workflow_id:
            async with B._sf()() as s:
                stored = await WorkflowRepository(s).get(request.workflow_id)
                if stored is None:
                    raise ProposalNotFound(f"Workflow not found: {request.workflow_id}")
                base, _ = sb.draft_of(stored)
                if project_ref and stored.project_id != project_ref and not await B._same_project(s, project_ref, stored.project_id):
                    raise ProposalNotFound(f"Workflow not found: {request.workflow_id}")
                project_ref = stored.project_id
        ctx = await B.load_ctx(project_ref) if project_ref else None
        return base, ctx, project_ref

    @staticmethod
    def _possible(route: str, has_workflow: bool, has_project: bool) -> str:
        if route in ("modify", "fix") and not has_workflow:
            return "build"       # nothing to change: a request for a workflow is a request to build one
        return route if route in ROUTES else "explain"

    async def _route(self, request: AIChatRequest, message: str, base: dict[str, Any] | None, ctx: "B.Ctx | None",
                     cfg: Any) -> tuple[str, str | None, str]:
        types = {n.get("type") for n in (base or {}).get("nodes") or []}
        history = [{"role": m.role, "content": m.content[:MAX_TURN_CHARS]}
                   for m in request.conversation_history[-MAX_HISTORY_TURNS:] if m.role in ("user", "assistant") and m.content]
        summary = {"name": (base or {}).get("name"), "nodes": [{"name": n.get("name"), "type": n.get("type")} for n in (base or {}).get("nodes") or []]} if base else {}
        payload = {
            "untrusted_message": message, "mode_hint": request.mode_hint, "has_workflow": base is not None, "workflow_summary": summary,
            "current_workflow": self._tool_get_workflow(base), "catalog": self._tool_get_node_catalog(ctx, types) if base is not None else [],
            "findings": self._tool_validate_workflow(base, ctx), "untrusted_history": history,
        }
        secrets = ctx.secrets if ctx else set()
        clean, _ = ai_policy.scrub_payload(payload, secrets)
        system, user = prompts.render("chat_route", clean)
        meter = Meter(max_calls=cfg.max_calls, case=(ctx.project_id if ctx else "chat"), secret_values=secrets)
        reply = await wf_ai.ask("chat_route", system, user, prompts.CHAT_ROUTE_SCHEMA, meter=meter, model=cfg.model)
        for ev in meter.events:  # no proposal row to hold the audit for a question: the hashes go to the log
            logger.info("ai chat call %s", json.dumps({k: ev.get(k) for k in ("task", "model", "prompt_sha256", "input_tokens", "output_tokens", "outcome")}))
        route = reply.get("route") if reply.get("route") in ROUTES else "explain"
        hint_route = {"explain": "explain"}.get(request.mode_hint)
        if hint_route:
            route = hint_route
        node = reply.get("node") if isinstance(reply.get("node"), str) and reply.get("node") else None
        if node is not None and node not in {n.get("name") for n in (base or {}).get("nodes") or []}:
            node = None
        return route, request.selected_node or node, str(reply.get("answer") or "")


# ── events ──────────────────────────────────────────────────────────────────────────────


def _proposal_event(view: dict[str, Any]) -> dict[str, Any]:
    diff = view.get("diff") or {}
    counts = diff.get("counts") or {"added": 0, "removed": 0, "changed": 0}
    passed = bool((view.get("report") or {}).get("passed"))
    status = view.get("status")
    if status != "ready":
        summary = view.get("error_message") or "The change could not be built."
    else:
        bits = [f"{counts.get(k, 0)} {k}" for k in ("added", "changed", "removed") if counts.get(k)]
        summary = ("Proposed: " + ", ".join(bits) if bits else "Proposed: no change to the nodes") + (". Tests pass." if passed else ". Some tests fail.")
    return {"type": "proposal", "proposal_id": view["id"], "workflow_id": view.get("workflow_id"), "status": status, "summary": summary,
            "diff": {"counts": counts}, "report": {"passed": passed}}


def _sse(event_type: str, data: dict[str, Any]) -> dict[str, Any]:
    """Build an SSE event dict matching the existing contract."""
    return {"event": event_type, "data": json.dumps(data, default=str)}


def _error(message: str, code: str, **extra: Any) -> dict[str, Any]:
    return _sse("error", {"type": "error", "message": message, "code": code, **extra})

"""Interfaces: how people use a workflow (Stage 5, DESIGN-STAGE5-UIS sections 3, 6.1, 7).

Two audiences, one pipeline:

* **Public** (``/api/i/{project}/{slug}/...``): anonymous. Resolves the *published* version through ``interface_slugs``;
  anything missing, unpublished or ``access.mode == "studio"`` is a 404. Submissions are validated server-side against
  the trigger's ``input_schema``, deduplicated by ``Idempotency-Key``, and started as a detached run. The caller only
  ever sees a ``RunView`` (no node names beyond the current label, no errors, no ids beyond the contract).
* **Internal** (studio): read / replace the draft's ``interface``, generate one, preview-submit against the *draft*,
  fetch preview data. Preview runs the draft in the project's default environment; live runs the published version in
  the project's live environment (n8n's Test URL vs Production URL).

Calls into the durable-approvals half (Builder A) are isolated in :func:`start_detached_run` (the run) and in the
``human_tasks`` / ``executions.metadata.result`` reads of :class:`InterfaceRepository`.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import logging
import secrets
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable

from ..core.exceptions import WorkflowNotFoundError
from ..engine import form_fields as ff
from ..repositories.interface_repository import InterfaceRepository
from ..schemas import interface as schema

logger = logging.getLogger(__name__)

MAX_SUBMIT_BYTES = 64 * 1024
RUN_TIMEOUT_S = 900.0
RUN_TOKEN_PREFIX = "rt_"
_KEY_OK = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_.:")
PREVIEW_KEY_PREFIX = "pv:"


class InterfaceError(Exception):
    """A request failure with an HTTP status and the engine's ``{code, message, fields?}`` envelope."""

    def __init__(self, status: int, code: str, message: str, fields: dict[str, Any] | None = None,
                 headers: dict[str, str] | None = None) -> None:
        super().__init__(message)
        self.status, self.code, self.message, self.fields, self.headers = status, code, message, fields, headers

    def detail(self) -> dict[str, Any]:
        d: dict[str, Any] = {"code": self.code, "message": self.message}
        if self.fields:
            d["fields"] = self.fields
        return d


def not_found() -> InterfaceError:
    return InterfaceError(404, "NOT_FOUND", "not found")


# ── tokens ───────────────────────────────────────────────────────────────────────────────────


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def new_token(prefix: str = "") -> str:
    return prefix + secrets.token_urlsafe(32)


def token_matches(token: str | None, stored_hash: str | None) -> bool:
    return bool(token and stored_hash) and hmac.compare_digest(hash_token(token), stored_hash)  # type: ignore[arg-type]


def bearer(authorization: str | None) -> str | None:
    if authorization and authorization.lower().startswith("bearer "):
        return authorization.split(" ", 1)[1].strip() or None
    return None


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


def clean_idempotency_key(header: str | None, body_key: str | None) -> str:
    """The header wins; the body field is the fallback for proxies that strip it (R12). Required, 1-128 safe chars."""
    key = (header or body_key or "").strip()
    if not key:
        raise InterfaceError(400, "IDEMPOTENCY_KEY_REQUIRED", "Send an Idempotency-Key header (or idempotency_key in the body).")
    if len(key) > 128 or any(ch not in _KEY_OK for ch in key):
        raise InterfaceError(400, "IDEMPOTENCY_KEY_INVALID", "The idempotency key must be 1-128 letters, digits or - _ . :")
    return key


# ── targets ──────────────────────────────────────────────────────────────────────────────────


@dataclass
class Target:
    """What a request runs: the interface plus the definition and environment it runs in."""

    project_id: str
    project_slug: str
    workflow_id: str
    slug: str
    interface: dict[str, Any]
    definition: dict[str, Any]
    mode: str                                   # "live" | "preview"
    environment: str
    version_id: int | None                      # None = run the draft
    extras: dict[str, Any] | None = None
    trigger_node: str = ""
    kind: str = ""
    settings: dict[str, Any] = field(default_factory=dict)

    @property
    def input_schema(self) -> dict[str, Any]:
        return schema.trigger_input_schema(self.definition, self.interface) or {}


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


# ── the detached run (the call site that changes when Builder A wires Wave 2) ────────────────


async def start_detached_run(
    workflow_id: str,
    *,
    item: dict[str, Any],
    start_node: str,
    environment: str,
    version_id: int | None,
    extras: dict[str, Any] | None,
    metadata: dict[str, Any],
    on_finished: Callable[[Any], Awaitable[None]] | None = None,
    execution_id: str | None = None,
) -> str:
    """Start ``workflow_id`` from ``start_node`` with one trigger item and return its execution id at once.

    The run lives in its own task and DB session, so a closed browser or a finished request never cancels it
    (section 6.1). ``ExecutionService.run_saved_workflow`` reports the id through ``on_context_ready`` after it has
    written the execution row; this function waits for that, then returns. ``on_finished(context)`` runs inside the
    task once the run ended (success, failure or suspension). Wave 2 (Builder A) may swap the body for a native
    ``execution_id=`` / ``detached=True`` call; callers don't change."""
    from ..db.session import async_session_factory
    from ..engine import execution_registry
    from ..engine.types import NodeData
    from ..repositories.execution_repository import ExecutionRepository
    from ..repositories.workflow_repository import WorkflowRepository
    from .execution_service import ExecutionService

    loop = asyncio.get_running_loop()
    ready: asyncio.Future[str] = loop.create_future()

    async def on_ready(exec_id: str, _env: str, _stored: Any) -> None:
        if not ready.done():
            ready.set_result(exec_id)

    async def runner() -> None:
        try:
            async with async_session_factory() as session:
                svc = ExecutionService(ExecutionRepository(session), WorkflowRepository(session))
                _, context = await svc.run_saved_workflow(
                    workflow_id, None, environment=environment, mode="api", substitute_inputs=False,
                    version_id=version_id, timeout_s=RUN_TIMEOUT_S, metadata=metadata, extras=extras,
                    start_node_name=start_node, initial_data_override=[NodeData(json=item)], on_context_ready=on_ready,
                    execution_id=execution_id,
                )
            if on_finished is not None:
                await on_finished(context)
        except asyncio.CancelledError:
            raise
        except BaseException as e:   # noqa: BLE001 - surfaced to the waiter below, logged otherwise
            if not ready.done():
                ready.set_exception(e)
            else:
                logger.exception("interface run %s failed", workflow_id)
                if on_finished is not None:
                    try:
                        await on_finished(None)
                    except Exception:   # noqa: BLE001
                        logger.exception("interface completion hook failed")

    task = asyncio.create_task(runner())
    try:
        exec_id = await asyncio.wait_for(asyncio.shield(ready), timeout=60.0)
    except BaseException:
        task.cancel()
        raise
    execution_registry.register(exec_id, task)
    return exec_id


# ── service ──────────────────────────────────────────────────────────────────────────────────


class InterfaceService:
    """Interface operations over one DB session. ``repo`` and ``start_run`` are injectable for tests."""

    def __init__(self, session: Any, *, repo: InterfaceRepository | None = None,
                 start_run: Callable[..., Awaitable[str]] | None = None) -> None:
        self._session = session
        self.repo = repo or InterfaceRepository(session)
        self._start_run = start_run or start_detached_run

    # ── resolution ───────────────────────────────────────────────────────────────────────────

    async def _project(self, ref: str, *, slug_only: bool = False) -> Any:
        from .project_service import resolve_project

        return await resolve_project(self._session, ref, slug_only=slug_only)

    async def resolve_live(self, project_ref: str, slug: str, *, kinds: tuple[str, ...] | None = None) -> Target:
        """The published interface behind ``/i/{project}/{slug}``. Every miss is the same 404."""
        project = await self._project(project_ref, slug_only=True)   # 7.2: the public address is the slug, never the internal id
        if project is None:
            raise not_found()
        row = await self.repo.get_slug(project.id, slug)
        if row is None:
            raise not_found()
        wf = await self.repo.workflow_row(row["workflow_id"])
        if wf is None or not wf.get("active") or wf.get("project_id") != project.id:
            raise not_found()
        ver = await self.repo.version_definition(row["version_id"])
        definition = (ver or {}).get("definition") or {}
        iface = definition.get("interface")
        if not isinstance(iface, dict) or iface.get("slug") != slug:
            raise not_found()
        if (iface.get("access") or {}).get("mode", "public") != "public":
            raise not_found()
        if kinds and iface.get("kind") not in kinds:
            raise not_found()
        version_id, extras, env = await self._live_environment(project, wf["id"], row["version_id"])
        return Target(project_id=project.id, project_slug=getattr(project, "slug", None) or project.id, workflow_id=wf["id"],
                      slug=slug, interface=iface, definition=definition, mode="live", environment=env, version_id=version_id,
                      extras=extras, trigger_node=iface.get("trigger_node") or "", kind=iface.get("kind") or "",
                      settings=dict(getattr(project, "settings", None) or {}))

    async def _live_environment(self, project: Any, workflow_id: str, version_id: int) -> tuple[int, dict[str, Any] | None, str]:
        """Live = the project's live environment. A release environment runs the active release's pinned version of the
        workflow (never the drafts), exactly like a published app; a workflow the release doesn't pin is unavailable."""
        from .project_service import env_kind

        env = getattr(project, "live_environment", None) or "default"
        extras = None
        if env_kind(project, env) == "release":
            from .release_service import ReleaseService
            from .route_dispatcher import release_extras

            rel = await ReleaseService(self._session).active_release(project.id, env)
            if rel is not None:
                pin = next((w for w in (rel.snapshot or {}).get("workflows") or []
                        if w["workflow_id"] == workflow_id and w.get("via") != "subworkflow"), None)
                if pin is None:
                    raise InterfaceError(503, "INTERFACE_UNAVAILABLE", "This page isn't available right now.")
                version_id, extras = pin["version_id"], release_extras(rel)
        return version_id, extras, env

    async def resolve_preview(self, workflow_id: str, *, kinds: tuple[str, ...] | None = None) -> Target:
        """The draft interface of ``workflow_id`` (studio preview). 404 when the workflow is unknown, 409 without an interface."""
        from ..repositories.workflow_repository import WorkflowRepository, workflow_to_definition
        from .project_service import effective_environment

        stored = await WorkflowRepository(self._session).get(workflow_id)
        if stored is None:
            raise WorkflowNotFoundError(workflow_id)
        definition = workflow_to_definition(stored.workflow)
        iface = definition.get("interface")
        if not isinstance(iface, dict) or not iface:
            raise InterfaceError(409, "NO_INTERFACE", "This workflow has no interface yet.")
        if kinds and iface.get("kind") not in kinds:
            raise InterfaceError(409, "WRONG_KIND", f"This interface is a {iface.get('kind')}, not a {' / '.join(kinds)}.")
        project = await self._project(stored.project_id)
        env = effective_environment(project, "default")
        return Target(project_id=stored.project_id, project_slug=getattr(project, "slug", None) or stored.project_id,
                      workflow_id=workflow_id, slug=iface.get("slug") or "preview", interface=iface, definition=definition,
                      mode="preview", environment=env, version_id=None, trigger_node=iface.get("trigger_node") or "",
                      kind=iface.get("kind") or "", settings=dict(getattr(project, "settings", None) or {}))

    # ── public: schema ───────────────────────────────────────────────────────────────────────

    def schema_of(self, t: Target) -> dict[str, Any]:
        """``GET /schema``: derived from the trigger's Shape and the interface only (no node internals)."""
        iface = t.interface
        params = _trigger_params(t.definition, t.trigger_node)
        out: dict[str, Any] = {
            "kind": t.kind,
            "title": iface.get("title") or params.get("title") or "",
            "description": iface.get("description") or params.get("description") or "",
            "submit_label": params.get("submit_label") or "Submit",
            "fields": [],
        }
        if t.kind in ("form", "custom"):
            out["fields"] = ff.derive_fields(t.input_schema, params.get("fields_ui"), params.get("field_order"))
        elif t.kind == "chat":
            out["welcome_message"] = params.get("welcomeMessage") or ""
            out["placeholder"] = params.get("placeholder") or "Type a message..."
        if t.kind == "custom":
            screens = iface.get("screens") or {}
            public = {sid: spec for sid, spec in screens.items() if sid in ("start", "result")}
            if (iface.get("access") or {}).get("public_tasks"):
                public.update({sid: spec for sid, spec in screens.items() if sid.startswith("task:")})
            out["screens"] = public
        return out

    # ── submit (public + preview) ────────────────────────────────────────────────────────────

    def validate_fields(self, t: Target, fields: Any) -> dict[str, Any]:
        clean, errors = ff.validate_submission(t.input_schema, fields)
        if errors:
            unknown = any(m == "unknown field" for m in errors.values())
            raise InterfaceError(422, "UNKNOWN_FIELD" if unknown else "VALIDATION_FAILED",
                                 "Some fields need attention." if not unknown else "The form was sent with a field it doesn't have.",
                                 fields=errors)
        return clean

    async def submit(self, t: Target, fields: Any, idempotency_key: str) -> dict[str, Any]:
        """Validate, dedupe and start a detached run. Returns ``{submission_id, execution_id, run_token}``."""
        if t.kind not in ("form", "custom"):
            raise not_found()
        clean = self.validate_fields(t, fields)
        key = (PREVIEW_KEY_PREFIX if t.mode == "preview" else "") + idempotency_key
        token = new_token(RUN_TOKEN_PREFIX)
        sub_id = new_id("sub")
        inserted = await self.repo.insert_submission(id=sub_id, project_id=t.project_id, workflow_id=t.workflow_id, slug=t.slug,
                                                     key=key, token_hash=hash_token(token), mode=t.mode)
        await self.repo.commit()
        if not inserted:
            return await self._replay(t, key)
        item = {**clean, "_submission": {"id": sub_id, "submitted_at": _utcnow(), "mode": t.mode}}
        meta = {"trigger": {"type": "interface", "slug": t.slug},
                "interface": {"slug": t.slug, "submission_id": sub_id, "mode": t.mode}}
        try:
            exec_id = await self._start_run(t.workflow_id, item=item, start_node=t.trigger_node, environment=t.environment,
                                            version_id=t.version_id, extras=t.extras, metadata=meta)
        except BaseException:
            await self.repo.delete_submission(sub_id)      # nothing ran: let the same key try again
            await self.repo.commit()
            raise
        await self.repo.set_submission_execution(sub_id, exec_id)
        await self.repo.commit()
        return {"submission_id": sub_id, "execution_id": exec_id, "run_token": token}

    async def _replay(self, t: Target, key: str) -> dict[str, Any]:
        """The same Idempotency-Key again: the same submission and execution; the poll token is reissued."""
        for _ in range(40):                                  # the first request may still be starting its run
            row = await self.repo.get_submission_by_key(t.project_id, t.slug, key)
            if row is None:
                raise InterfaceError(409, "SUBMISSION_RETRY", "That submission was withdrawn; send it again.")
            if row.get("execution_id"):
                token = new_token(RUN_TOKEN_PREFIX)
                await self.repo.set_submission_token_hash(row["id"], hash_token(token))
                await self.repo.commit()
                return {"submission_id": row["id"], "execution_id": row["execution_id"], "run_token": token}
            await self.repo.rollback()
            await asyncio.sleep(0.15)
        raise InterfaceError(409, "SUBMISSION_IN_PROGRESS", "That submission is still being started; try again in a moment.")

    # ── public: run view ─────────────────────────────────────────────────────────────────────

    async def run_view(self, t: Target, execution_id: str, token: str | None) -> dict[str, Any]:
        """The only thing a public client learns about a run (section 6.1). A wrong / missing token is a 404."""
        sub = await self.repo.get_submission_by_execution(execution_id)
        if (sub is None or sub["project_id"] != t.project_id or sub["slug"] != t.slug or sub["mode"] != t.mode
                or not token_matches(token, sub["run_token_hash"])):
            raise not_found()
        run = await self.repo.get_run(execution_id)
        if run is None or run["workflow_id"] != t.workflow_id:
            raise not_found()
        return await self._build_run_view(t, run)

    async def _build_run_view(self, t: Target, run: dict[str, Any]) -> dict[str, Any]:
        status = _public_status(run["status"])
        definition = await self._definition_of_run(t, run)
        meta = run.get("metadata") or {}
        view: dict[str, Any] = {"execution_id": run["id"], "status": status, "waiting_for": [], "result": None,
                                "message": None, "error": None}
        if not (t.interface.get("access") or {}).get("hide_progress"):
            prog = await self.repo.progress(run["id"])
            total = reachable_count(definition, t.trigger_node)
            view["progress"] = {"done": min(prog["done"], total) if total else prog["done"], "total": total,
                                "current": _node_label(definition, prog["current"])}
        if status == "waiting":
            view["waiting_for"] = [{"task_id": x["id"], "title": x["title"], "due_at": _iso(x.get("due_at"))}
                                   for x in await self.repo.open_tasks(run["id"])]
        elif status == "success":
            result = meta.get("result")
            view["result"] = result if result is not None else None
            if result is None:
                view["message"] = _trigger_params(definition, t.trigger_node).get("success_message") or "Submitted"
        elif status == "failed":
            view["error"] = {"code": "RUN_FAILED", "message": f"Something went wrong. Reference {run['id']}"}
        return view

    async def _definition_of_run(self, t: Target, run: dict[str, Any]) -> dict[str, Any]:
        vid = run.get("workflow_version_id")
        if vid and vid != t.version_id:
            ver = await self.repo.version_definition(vid)
            if ver and ver.get("definition"):
                return ver["definition"]
        return t.definition

    # ── internal: the interface of a workflow ────────────────────────────────────────────────

    async def get_interface(self, workflow_id: str) -> dict[str, Any]:
        from ..repositories.workflow_repository import WorkflowRepository, workflow_to_definition

        stored = await WorkflowRepository(self._session).get(workflow_id)
        if stored is None:
            raise WorkflowNotFoundError(workflow_id)
        definition = workflow_to_definition(stored.workflow)
        iface = definition.get("interface") or None
        live = None
        row = await self.repo.slug_of_workflow(workflow_id)
        if row is not None:
            project = await self._project(stored.project_id)
            pslug = getattr(project, "slug", None) or stored.project_id
            live = {"slug": row["slug"], "version_id": row["version_id"], "url": f"/i/{pslug}/{row['slug']}"}
        return {"interface": iface, "live": live, "wired": schema.compute_wired(definition, iface),
                "warnings": schema.interface_findings(definition)}

    async def put_interface(self, workflow_id: str, raw: Any) -> dict[str, Any]:
        """Replace (or, with ``None``, remove) the draft's interface. 422 ``INTERFACE_INVALID`` with ``fields``."""
        from ..repositories.workflow_repository import WorkflowRepository

        repo = WorkflowRepository(self._session)
        if await repo.get(workflow_id) is None:
            raise WorkflowNotFoundError(workflow_id)
        clean: dict[str, Any] | None = None
        if raw is not None:
            clean, errors = schema.parse_interface(raw)
            if errors:
                code = "SCREEN_TOO_LARGE" if any("SCREEN_TOO_LARGE" in e["message"] for e in errors) else "INTERFACE_INVALID"
                raise InterfaceError(422, code, "The interface isn't valid.", fields={e["field"] or "interface": e["message"] for e in errors})
        if await repo.set_interface(workflow_id, clean) is None:
            raise WorkflowNotFoundError(workflow_id)
        return await self.get_interface(workflow_id)

    async def generate(self, workflow_id: str, kind: str, slug: str | None) -> dict[str, Any]:
        from ..repositories.workflow_repository import WorkflowRepository, workflow_to_definition

        stored = await WorkflowRepository(self._session).get(workflow_id)
        if stored is None:
            raise WorkflowNotFoundError(workflow_id)
        if slug is not None and not schema.SLUG_RE.match(slug):
            raise InterfaceError(422, "INTERFACE_INVALID", "The slug isn't valid.", fields={"slug": "use 2-63 lowercase letters, digits or dashes"})
        return {"interface": schema.generate_interface(workflow_to_definition(stored.workflow), stored.name, kind, slug)}

    # ── internal: preview ────────────────────────────────────────────────────────────────────

    async def preview_submit(self, workflow_id: str, fields: Any, idempotency_key: str) -> dict[str, Any]:
        t = await self.resolve_preview(workflow_id, kinds=("form", "custom"))
        out = await self.submit(t, fields, idempotency_key)
        return {"execution_id": out["execution_id"], "submission_id": out["submission_id"]}

    async def preview_data(self, workflow_id: str, execution_id: str | None) -> dict[str, Any]:
        """Real run data for the live preview: ``{input, nodes, tasks, result}`` (defaults to the latest non-test run)."""
        from ..repositories.workflow_repository import WorkflowRepository, workflow_to_definition

        stored = await WorkflowRepository(self._session).get(workflow_id)
        if stored is None:
            raise WorkflowNotFoundError(workflow_id)
        run = await (self.repo.get_run(execution_id) if execution_id else self.repo.latest_run(workflow_id))
        if run is None or run["workflow_id"] != workflow_id:
            if execution_id:
                raise not_found()
            return {"execution_id": None, "status": None, "input": {}, "nodes": {}, "tasks": [], "result": None}
        definition = workflow_to_definition(stored.workflow)
        trigger = (definition.get("interface") or {}).get("trigger_node") or ""
        nodes = await self.repo.node_outputs(run["id"])
        tasks = await self.repo.tasks_of_execution(run["id"])
        summaries = [{"id": x["id"], "workflow_id": x["workflow_id"], "workflow_name": stored.name, "execution_id": x["execution_id"],
                      "node_name": x["node_name"], "title": x["title"], "status": x["status"], "assignees": x.get("assignees") or [],
                      "due_at": _iso(x.get("due_at")), "created_at": _iso(x.get("created_at")), "decided_by": x.get("decided_by"),
                      "decided_at": _iso(x.get("decided_at"))} for x in tasks]
        return {"execution_id": run["id"], "status": run["status"], "input": nodes.get(trigger) or {}, "nodes": nodes,
                "tasks": summaries, "result": (run.get("metadata") or {}).get("result")}


    async def ai_edit(self, workflow_id: str, screen_id: str, instruction: str, spec: dict[str, Any]) -> dict[str, Any]:
        """'Change the screen...': the model edits one screen of the draft (nothing is saved)."""
        from ..repositories.workflow_repository import WorkflowRepository, workflow_to_definition
        from . import interface_ai

        stored = await WorkflowRepository(self._session).get(workflow_id)
        if stored is None:
            raise WorkflowNotFoundError(workflow_id)
        definition = workflow_to_definition(stored.workflow)
        iface = definition.get("interface")
        if not isinstance(iface, dict) or not iface:
            raise InterfaceError(409, "NO_INTERFACE", "This workflow has no interface yet.")
        node_keys: dict[str, list[str]] = {}
        run = await self.repo.latest_run(workflow_id)
        if run is not None:
            for name, item in (await self.repo.node_outputs(run["id"])).items():
                if isinstance(item, dict):
                    node_keys[name] = list(item)[:40]
        return await interface_ai.edit_screen(definition, iface, screen_id, instruction, spec, node_keys)

    async def client_ts(self, workflow_id: str) -> str:
        """The typed TypeScript client of the draft interface (deterministic)."""
        from ..repositories.workflow_repository import WorkflowRepository, workflow_to_definition
        from .interface_client_codegen import generate_interface_client
        from .workflow_checker import StaticLookups, check_workflow

        stored = await WorkflowRepository(self._session).get(workflow_id)
        if stored is None:
            raise WorkflowNotFoundError(workflow_id)
        definition = workflow_to_definition(stored.workflow)
        iface = definition.get("interface")
        if not isinstance(iface, dict) or not iface:
            raise InterfaceError(409, "NO_INTERFACE", "This workflow has no interface yet.")
        project = await self._project(stored.project_id)
        shapes = check_workflow(definition, lookups=StaticLookups()).get("shapes_by_node")
        return generate_interface_client(definition, iface, getattr(project, "slug", None) or stored.project_id,
                                         workflow_id=workflow_id, project_id=stored.project_id, shapes_by_node=shapes)

    async def promote(self, workflow_id: str) -> tuple[int, dict[str, Any]]:
        from ..repositories.workflow_repository import WorkflowRepository
        from .interface_promote import promote

        stored = await WorkflowRepository(self._session).get(workflow_id)
        if stored is None:
            raise WorkflowNotFoundError(workflow_id)
        project = await self._project(stored.project_id)
        return await promote(self._session, workflow_id, project_slug=getattr(project, "slug", None) or stored.project_id)


# ── helpers ──────────────────────────────────────────────────────────────────────────────────


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    return value.isoformat() if hasattr(value, "isoformat") else str(value)


def _public_status(status: str | None) -> str:
    if status in ("success", "failed", "cancelled", "waiting"):
        return status
    if status in ("error", "timeout", "crashed"):
        return "failed"
    return "running"


def _trigger_params(definition: dict[str, Any], trigger: str) -> dict[str, Any]:
    node = next((n for n in definition.get("nodes") or [] if n.get("name") == trigger), None)
    return dict((node or {}).get("parameters") or {})


def _node_label(definition: dict[str, Any], name: str | None) -> str | None:
    if not name:
        return None
    node = next((n for n in definition.get("nodes") or [] if n.get("name") == name), None)
    return (node or {}).get("label") or name


def reachable_count(definition: dict[str, Any], trigger: str) -> int:
    """Enabled nodes reachable from ``trigger`` (the trigger included): the ``total`` of a run's progress."""
    nodes = {n["name"]: n for n in definition.get("nodes") or [] if isinstance(n, dict) and n.get("name")}
    if trigger not in nodes:
        return 0
    out: dict[str, list[str]] = {}
    for c in definition.get("connections") or []:
        out.setdefault(c.get("source_node"), []).append(c.get("target_node"))
    seen, todo = {trigger}, [trigger]
    while todo:
        for nxt in out.get(todo.pop(), []):
            if nxt in nodes and nxt not in seen and not nodes[nxt].get("disabled"):
                seen.add(nxt)
                todo.append(nxt)
    return len(seen)

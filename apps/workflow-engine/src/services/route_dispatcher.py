"""Project router dispatch: auth → environment → match → handler (BACKEND-DESIGN §3.5, phase 5).

* The caller never chooses the environment: API key → its env; app token → live env
  (preview token → default env); no credential → live env.
* Inbound payloads are never ``$vars``-substituted (decision 1).
* JSON responses are secret-redacted; every handled request carries ``X-Execution-Id``.
"""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models import AppModel, ProjectModel
from .project_service import env_kind
from .spec_tools import PARAM_RE, validate

STRIP_HEADERS = {"authorization", "x-api-key", "cookie", "host", "content-length"}


@dataclass
class RouteRequest:
    method: str
    path: str
    query: dict[str, Any] = field(default_factory=dict)
    headers: dict[str, str] = field(default_factory=dict)
    body: Any = None


@dataclass
class DispatchResult:
    status: int
    body: Any = None
    headers: dict[str, str] = field(default_factory=dict)
    content_type: str = "application/json"
    execution_id: str | None = None
    # Review D16: structured handler error for management callers (/test, run-draft); never sent by the router.
    error: dict[str, Any] | None = None


@dataclass
class Credential:
    kind: str  # none | api_key | app_token
    environment: str
    app_id: str | None = None
    key_id: str | None = None
    preview: bool = False
    test: bool = False  # /test (management call, auth bypassed)

    @property
    def trigger_type(self) -> str:
        """Review C2: route_test | app_preview | app | api_key | public."""
        if self.test:
            return "route_test"
        if self.kind == "app_token":
            return "app_preview" if self.preview else "app"
        return "api_key" if self.kind == "api_key" else "public"


class AuthError(Exception):
    def __init__(self, error: str = "unauthorized", status: int = 401) -> None:
        self.error, self.status = error, status


def compile_path(path: str) -> re.Pattern:
    parts = []
    for seg in [s for s in path.split("/") if s]:
        m = PARAM_RE.fullmatch(seg)
        parts.append(f"(?P<{m.group(1)}>[^/]+)" if m else re.escape(seg))
    return re.compile("^/" + "/".join(parts) + "$") if parts else re.compile("^/?$")


def _specificity(path: str) -> tuple:
    segs = [s for s in path.split("/") if s]
    return (-sum(1 for s in segs if not PARAM_RE.fullmatch(s)), sum(1 for s in segs if PARAM_RE.fullmatch(s)), -len(segs))


def match_route(specs: list[dict[str, Any]], method: str, path: str) -> tuple[dict | None, dict[str, str], list[str]]:
    """(route spec, path params, allowed methods for this path)."""
    path = "/" + path.strip("/") if path.strip("/") else "/"
    hits = []
    for s in specs:
        m = compile_path(s["path"]).match(path)
        if m:
            hits.append((s, m.groupdict()))
    hits.sort(key=lambda h: _specificity(h[0]["path"]))
    allowed = sorted({h[0]["method"] for h in hits})
    for s, params in hits:
        if s["method"] == method:
            return s, params, allowed
    return None, {}, allowed


class RouteDispatcher:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    # ── route source (phase 9: release snapshots) ────────────────────────

    async def route_set(self, project: ProjectModel, env: str) -> tuple[list[dict[str, Any]], Any]:
        if env_kind(project, env) == "release":
            try:
                from .release_service import ReleaseService
                rel = await ReleaseService(self._s).active_release(project.id, env)
            except ImportError:
                rel = None
            if rel is not None:
                return list(rel.snapshot.get("routes") or []), rel
        from .api_route_service import ApiRouteService, route_spec
        return [route_spec(r) for r in await ApiRouteService(self._s).rows(project.id)], None

    # ── auth ─────────────────────────────────────────────────────────────

    async def authenticate(self, project: ProjectModel, headers: dict[str, str]) -> Credential:
        from ..core.public_tokens import PublicTokenError, app_token_valid_for, verify_token
        from .api_key_service import ApiKeyService

        auth = headers.get("authorization") or ""
        bearer = auth.split(" ", 1)[1].strip() if auth.lower().startswith("bearer ") else None
        api_key = headers.get("x-api-key") or (bearer if bearer and bearer.startswith("sk_") else None)
        if api_key:
            k = await ApiKeyService(self._s).verify(project.id, api_key)
            if not k:
                raise AuthError()
            return Credential("api_key", k.environment, key_id=k.id)
        if bearer:
            try:
                claims = verify_token(bearer)
            except PublicTokenError as e:
                raise AuthError("token_expired" if "expired" in str(e) else "unauthorized")
            app = await self._s.get(AppModel, claims.app_id)
            if not app or app.project_id != project.id:
                raise AuthError()
            if claims.preview:
                env = claims.env or project.default_environment
                if env_kind(project, env) != "draft":
                    raise AuthError()
                return Credential("app_token", env, app_id=app.id, preview=True)
            if not app_token_valid_for(app, claims):  # review D3: app + rotation key, not the version
                raise AuthError()
            return Credential("app_token", project.live_environment, app_id=app.id)
        if auth:
            raise AuthError()
        return Credential("none", project.live_environment)

    # ── entry point ──────────────────────────────────────────────────────

    async def handle(self, project: ProjectModel, req: RouteRequest, *, forced_env: str | None = None) -> DispatchResult:
        try:
            cred = Credential("api_key", forced_env, test=True) if forced_env else await self.authenticate(project, req.headers)
        except AuthError as e:
            return DispatchResult(e.status, {"error": e.error})
        result = await self._dispatch(project, req, cred)
        # Review D4: one redactor for everything that leaves the router — body, headers and
        # error messages, for every handler type (workflow, Respond node, python).
        from .variable_loader import load_run_secrets
        secrets = await load_run_secrets(environment=cred.environment, team_id=project.team_id, project_id=project.id)
        result = redact_result(result, secrets)
        if result.execution_id:
            await self._record_exchange(result, req, secrets)
        return result

    async def _record_exchange(self, res: DispatchResult, req: RouteRequest, secrets: set[str]) -> None:
        """Review C3: metadata.request / metadata.response on the run — redacted, 16 KB each."""
        from ..repositories.execution_repository import ExecutionRepository
        from .vars_substitution import redact_deep

        req_part = {"method": req.method.upper(), "path": req.path, "query": dict(req.query or {}),
                    "headers": {k: v for k, v in (req.headers or {}).items() if k.lower() not in STRIP_HEADERS}}
        req_part = {**redact_deep(req_part, secrets), **capped_body(redact_deep(req.body, secrets)
                                                                    if isinstance(req.body, (dict, list, str)) else req.body)}
        resp_part = {"status": res.status, "headers": dict(res.headers or {}), "content_type": res.content_type,
                     **capped_body(res.body)}
        try:
            await ExecutionRepository(self._s).set_metadata(res.execution_id, {"request": req_part, "response": resp_part})
        except Exception:  # never fail a request because the run row could not be annotated
            import logging
            logging.getLogger(__name__).warning("could not store request/response on %s", res.execution_id, exc_info=True)

    async def _dispatch(self, project: ProjectModel, req: RouteRequest, cred: Credential) -> DispatchResult:
        specs, release = await self.route_set(project, cred.environment)
        spec, params, allowed = match_route(specs, req.method.upper(), req.path)
        if spec is None:
            if allowed:
                return DispatchResult(405, {"error": "method_not_allowed", "allowed": allowed},
                                      headers={"Allow": ", ".join(allowed)})
            return DispatchResult(404, {"error": "route_not_found"})
        if spec["auth"] == "api_key" and cred.kind != "api_key":
            return DispatchResult(401, {"error": "unauthorized"})
        if spec["auth"] == "app_token" and cred.kind not in ("app_token", "api_key"):
            return DispatchResult(401, {"error": "unauthorized"})
        if spec.get("validate_request") and spec.get("request_schema"):
            errors = validate({"params": params, "query": req.query, "body": req.body}, spec["request_schema"])
            if errors:
                return DispatchResult(422, {"error": "request_invalid", "errors": errors})
        handler = spec.get("handler") or {}
        if handler.get("type") == "stub":
            return DispatchResult(501, {"error": "not_implemented", "route": f"{spec['method']} {spec['path']}",
                                        "operation": spec["name"]})
        trigger = {"type": cred.trigger_type, "route_id": spec["id"], "method": spec["method"], "route": spec["path"],
                   "path": req.path, "app_id": cred.app_id, "api_key_id": cred.key_id, "auth": cred.kind,
                   "release_id": getattr(release, "id", None)}
        item = {
            "req": {"method": req.method.upper(), "path": req.path, "route": spec["path"], "params": params,
                    "query": req.query, "headers": {k: v for k, v in req.headers.items() if k.lower() not in STRIP_HEADERS},
                    "body": req.body},
            "params": params, "query": req.query,
            "headers": {k: v for k, v in req.headers.items() if k.lower() not in STRIP_HEADERS},
            "body": req.body, "method": req.method.upper(),
        }
        if handler.get("type") == "workflow":
            return await self._run_workflow(spec, handler, item, cred.environment, trigger, release)
        if handler.get("type") == "python":
            return await self._run_python(project, spec, handler, item, cred.environment, trigger, release)
        return DispatchResult(500, {"error": "invalid_handler"})

    # ── handlers ─────────────────────────────────────────────────────────

    async def _run_workflow(self, spec, handler, item, env, trigger, release=None) -> DispatchResult:
        from ..core.exceptions import WorkflowExecutionError, WorkflowNotFoundError
        from ..repositories.execution_repository import ExecutionRepository
        from ..repositories.workflow_repository import WorkflowRepository
        from .execution_service import ExecutionService
        from .vars_substitution import redact_deep

        svc = ExecutionService(ExecutionRepository(self._s), WorkflowRepository(self._s))
        exec_ids: list[str] = []

        async def remember(exec_id, _env, _stored):
            exec_ids.append(exec_id)

        try:
            _, context = await svc.run_saved_workflow(
                handler["workflow_id"], None, environment=env, mode="api", substitute_inputs=False,
                initial_item=item, version_id=handler.get("version_id"), timeout_s=float(spec.get("timeout_s") or 30),
                metadata={"trigger": trigger}, on_context_ready=remember,
                # Review D8: a release-pinned run's PythonStep/Code nodes import the release's module
                # sources, not the drafts.
                extras=release_extras(release),
            )
        except asyncio.TimeoutError:
            xid = exec_ids[0] if exec_ids else None
            return DispatchResult(504, {"error": "timeout", "execution_id": xid}, execution_id=xid)
        except (WorkflowNotFoundError, WorkflowExecutionError) as e:
            xid = exec_ids[0] if exec_ids else None
            return DispatchResult(502, {"error": "handler_failed", "execution_id": xid,
                                        "message": getattr(e, "message", str(e))}, execution_id=xid)
        secrets = context.extras.get("secret_values") or set()
        xid = context.execution_id
        if context.webhook_response is not None:
            wr = context.webhook_response
            body = redact_deep(wr.body, secrets) if isinstance(wr.body, (dict, list, str)) else wr.body
            return DispatchResult(wr.status_code, body, dict(wr.headers or {}), wr.content_type or "application/json", xid)
        if context.errors:
            e = context.errors[0]
            body = {"error": "handler_failed", "execution_id": xid, "message": redact_deep(e.error, secrets)}
            if e.error_code:
                body["error_code"] = e.error_code
            return DispatchResult(502, body, execution_id=xid,
                                  error={"type": "NodeError", "node": e.node_name, "message": e.error, "code": e.error_code,
                                         "details": e.details, "traceback": None, "line": None, "module": None,
                                         "table": (e.details or {}).get("table")})
        items = []
        if context.last_completed_node and context.last_completed_node in context.node_states:
            items = [d.json for d in context.node_states[context.last_completed_node]]
        mode = spec.get("response_mode") or "auto"
        if mode == "all_items":
            body: Any = items
        elif mode == "first_item":
            body = items[0] if items else None
        else:
            body = items[0] if len(items) == 1 else items
        return DispatchResult(200, redact_deep(body, secrets), execution_id=xid)

    async def _run_python(self, project, spec, handler, item, env, trigger, release) -> DispatchResult:
        try:
            from .python_handler import run_python_route
        except ImportError:
            return DispatchResult(503, {"error": "sandbox_unavailable"})
        return await run_python_route(self._s, project, spec, handler, item, env, trigger, release)


BODY_CAP_BYTES = 16384


def capped_body(body: Any) -> dict[str, Any]:
    """{body, truncated[, body_preview]}: bodies over 16 KB (serialized) are replaced by a preview."""
    try:
        text = body if isinstance(body, str) else json.dumps(body, default=str)
    except (TypeError, ValueError):
        text = str(body)
    raw = text.encode("utf-8", "replace") if text is not None else b""
    if len(raw) <= BODY_CAP_BYTES:
        return {"body": body, "truncated": False}
    return {"body": None, "truncated": True, "size_bytes": len(raw),
            "body_preview": raw[:BODY_CAP_BYTES].decode("utf-8", "ignore")}


def release_extras(release) -> dict[str, Any] | None:
    if release is None:
        return None
    return {"release_id": release.id,
            "release_modules": {m["name"]: m["source"] for m in (release.snapshot or {}).get("modules") or []}}


def redact_result(res: DispatchResult, secrets: set[str]) -> DispatchResult:
    """Redact secret values (and their encoded variants) from a response's body and headers."""
    from .vars_substitution import expand_secret_variants, redact_deep

    variants = expand_secret_variants(set(secrets or ()))
    if not variants:
        return res
    if isinstance(res.body, (dict, list, str)):
        res.body = redact_deep(res.body, variants, expanded=True)
    if res.error is not None:
        res.error = redact_deep(res.error, variants, expanded=True)
    res.headers = {str(k): redact_deep(str(v), variants, expanded=True) for k, v in (res.headers or {}).items()}
    return res


def parse_body(raw: bytes, content_type: str) -> Any:
    if not raw:
        return None
    if "application/json" in (content_type or "") or raw[:1] in (b"{", b"["):
        try:
            return json.loads(raw)
        except (ValueError, UnicodeDecodeError):
            pass
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return None

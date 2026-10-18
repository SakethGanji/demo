"""BrokerService — run exactly one node as a function, under policy (BACKEND-DESIGN §3.4, §5).

The node runs through ``WorkflowRunner.run`` on a one-node workflow (``start_node_name="fn"``),
with no node_outputs persistence, the token's (project, environment) variables, a host-checking
httpx client (no redirects), and full-response redaction. Every call is logged to broker_calls.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from ..db.models import BrokerCallModel, ProjectModel
from ..engine.types import NodeData, NodeDefinition, Workflow
from ..services.vars_substitution import expand_secret_variants, redact_deep
from ..utils.ids import generate_id
from .policy import EgressGuard, PolicyError, check_parameters, check_read_only, egress_kind, is_exposed, snake
from .tokens import BrokerClaims, registry
from .workflows import NODE_TYPE as RUN_WORKFLOW

logger = logging.getLogger(__name__)


def function_index() -> dict[str, str]:
    """{function_name: node_type} for every exposed node, plus ``run_workflow`` (``RunWorkflow``, see broker/workflows.py)."""
    from ..engine.node_registry import node_registry
    from .workflows import FUNCTION, NODE_TYPE

    return {**{snake(t): t for t in node_registry.list() if is_exposed(t)}, FUNCTION: NODE_TYPE}


def resolve_function(function: str | None, node: str | None) -> str:
    idx = function_index()
    if node:
        if node not in idx.values():
            raise PolicyError("function_not_allowed", 403, f"node '{node}' is not callable as a function")
        return node
    if function and function in idx:
        return idx[function]
    raise PolicyError("function_not_allowed", 403, f"function '{function}' is not callable")


def _slot(origin: str, allowed_functions: set[str] | None) -> str:
    return f"broker_token:{origin}" + (":" + ",".join(sorted(allowed_functions)) if allowed_functions else "")


def mint_for_context(context: Any, origin: str, ttl_s: float = 600.0, max_calls: int | None = None,
                     allowed_functions: set[str] | None = None, read_only: bool = False) -> str:
    """Mint (or reuse) the run-scoped token for an ExecutionContext. Revoke with ``revoke_for_context``.

    Review D5: ``allowed_functions`` is always set on the claims — the given set (e.g. an agent tool's
    own node), or else every function-exposed node at mint time. Tokens are kept per
    (origin, function set), so two tools of one agent never share a wider token."""
    from ..core.config import settings

    slot = _slot(origin, allowed_functions)
    existing = context.extras.get(slot)
    found = registry.verify(existing) if existing else None
    # A sub-workflow inherits its parent's extras (and so its tokens); its code needs a token that names *its* run,
    # or run_workflow's cycle/depth guard would see the parent's call stack.
    if found is not None and found.execution_id == context.execution_id:
        return existing
    frame = frame_of(context)
    claims = BrokerClaims(
        project_id=context.project_id or "proj_default",
        environment=context.environment or "default",
        execution_id=context.execution_id,
        origin=origin,
        team_id=getattr(context, "team_id", "default"),
        max_calls=max_calls or getattr(settings, "broker_max_calls", 500),
        allowed_functions=set(allowed_functions) if allowed_functions else set(function_index().values()),
        read_only=read_only,
        **frame,
    )
    token = registry.mint(claims, ttl_s)
    context.extras[slot] = token
    return token


def frame_of(context: Any) -> dict[str, Any]:
    """Where code minted a token from ``context`` runs, as BrokerClaims fields: the workflows on its call stack, their
    names, the nesting depth and the release it runs in. ``run_workflow`` hands these to the sub-workflow guards."""
    from ..engine.subworkflow import call_stack_of

    extras = getattr(context, "extras", None) or {}
    try:
        stack = tuple(call_stack_of(context)) if getattr(context, "workflow", None) is not None else \
            tuple(getattr(context, "call_stack", None) or ())
    except Exception:
        stack = tuple(getattr(context, "call_stack", None) or ())
    return {"call_stack": stack, "call_names": dict(getattr(context, "call_names", None) or {}),
            "depth": int(getattr(context, "execution_depth", 0) or 0), "release_id": extras.get("release_id")}


def revoke_for_context(context: Any, origin: str, allowed_functions: set[str] | None = None) -> None:
    registry.revoke(context.extras.pop(_slot(origin, allowed_functions), None))


class _VirtualResult(Exception):
    """Internal: a list on a virtual (not yet created) table → an empty result."""


class BrokerService:
    def __init__(self, session_factory=None) -> None:
        if session_factory is None:
            from ..db.session import async_session_factory
            session_factory = async_session_factory
        self._sf = session_factory

    async def call_with_token(self, token: str | None, function: str | None, node: str | None,
                              parameters: dict[str, Any] | None, items: list[Any] | None) -> tuple[int, dict[str, Any]]:
        claims = registry.verify(token)
        if claims is None:
            return 401, {"ok": False, "call_id": None, "error": {"code": "invalid_token", "message": "invalid or expired broker token"}}
        return await self.call(claims, function, node, parameters, items)

    async def call(self, claims: BrokerClaims, function: str | None, node: str | None,
                   parameters: dict[str, Any] | None, items: list[Any] | None) -> tuple[int, dict[str, Any]]:
        call_id = generate_id("bcall")
        started = time.perf_counter()
        parameters = parameters or {}
        items = items if items is not None else [{}]
        node_type = node or function or "?"
        secrets: set[str] = set()
        try:
            if not isinstance(parameters, dict) or not isinstance(items, list):
                raise PolicyError("invalid_request", 400, "parameters must be an object and items a list")
            node_type = resolve_function(function, node)
            if claims.allowed_functions is not None and node_type not in claims.allowed_functions \
                    and snake(node_type) not in claims.allowed_functions:
                raise PolicyError("function_not_allowed", 403, f"'{snake(node_type)}' is not allowed for this token")
            if node_type == "DataTable" and claims.virtual_tables and parameters.get("table") in claims.virtual_tables:
                claims.calls_used += 1
                op = str(parameters.get("operation") or "list").strip().lower()
                if op == "list":
                    raise _VirtualResult()
                if op == "get":
                    raise PolicyError("row_not_found", 404, f"row not found in '{parameters.get('table')}' (table not created yet)",
                                      table=parameters.get("table"))
                raise PolicyError("table_not_found", 404, f"table '{parameters.get('table')}' not found in this project",
                                  table=parameters.get("table"))
            if claims.read_only:
                check_read_only(node_type, parameters)
            if claims.calls_used >= claims.max_calls:
                raise PolicyError("call_limit_exceeded", 429, f"broker call limit ({claims.max_calls}) reached")
            claims.calls_used += 1

            refs = check_parameters(parameters, items)
            variables, secrets, rows, project = await self._load_env(claims)
            missing = sorted(k for k in refs if k not in rows)
            if missing:
                raise PolicyError("missing_secret", 422, f"secret '{missing[0]}' is not defined in '{claims.environment}'",
                                  key=missing[0], environment=claims.environment)
            secret_keys = {k for k in refs if rows[k].type == "secret"}
            kind = egress_kind(node_type)
            secret_hosts: dict[str, list[str]] = {}
            for k in sorted(secret_keys):
                hosts = rows[k].allowed_hosts
                if not hosts or kind not in {"http", "postgres"}:
                    raise PolicyError("secret_not_bound", 403,
                                      f"secret '{k}' is not bound to any host for '{snake(node_type)}'"
                                      if not hosts else f"secret '{k}' cannot be used with '{snake(node_type)}'",
                                      key=k)
                secret_hosts[k] = hosts
            allowlist = list(((project.settings or {}) if project else {}).get("egress_allowlist") or [])
            if kind == "opaque" and allowlist:
                raise PolicyError("host_not_allowed", 403,
                                  f"'{snake(node_type)}' egress cannot be checked against the project allowlist")

            # Own task => the runner's contextvars (execution id, $vars, secrets) never leak into the caller.
            if node_type == RUN_WORKFLOW:
                status, body = await asyncio.create_task(
                    self._run_workflow(claims, parameters, variables, secrets, project, allowlist))
            else:
                guard = EgressGuard(allowlist, secret_hosts, read_only=claims.read_only)
                status, body = await asyncio.create_task(
                    self._run_node(claims, node_type, parameters, items, variables, secrets, guard))
        except _VirtualResult:
            status, body = 200, {"ok": True, "call_id": call_id, "items": [], "metadata": {"virtual_table": True}}
        except PolicyError as e:
            status, body = e.status, {"ok": False, "call_id": call_id,
                                      "error": {"code": e.code, "message": e.message, **e.extra}}
        except Exception as e:  # pragma: no cover - defensive
            logger.exception("broker call failed")
            status, body = 500, {"ok": False, "call_id": call_id, "error": {"code": "internal_error", "message": str(e)}}

        body["call_id"] = call_id
        variants = expand_secret_variants(secrets)
        body = redact_deep(body, variants, expanded=True)
        await self._log(call_id, claims, node_type, parameters, variants, body, status,
                        (time.perf_counter() - started) * 1000)
        claims.trace.append(call_id)
        return status, body

    async def _load_env(self, claims: BrokerClaims):
        from ..repositories.variable_repository import VariableRepository

        async with self._sf() as session:
            repo = VariableRepository(session)
            variables = await repo.load_runtime_map(claims.team_id, claims.environment, claims.project_id)
            secrets = await repo.load_runtime_secret_values(claims.team_id, claims.environment, claims.project_id)
            rows = await repo.load_runtime_rows(claims.team_id, claims.environment, claims.project_id)
            project = await session.get(ProjectModel, claims.project_id)
        return variables, secrets, rows, project

    async def _run_workflow(self, claims: BrokerClaims, parameters: dict, variables: dict, secrets: set,
                            project: Any, allowlist: list[str]) -> tuple[int, dict[str, Any]]:
        """``run_workflow`` (broker/workflows.py): an ``ExecuteWorkflow`` node on the same one-node path as every other
        function, with the calling code's call stack and depth (the sub-workflow guards) and no broker egress guard: the
        workflow runs like any other run of the project, its own nodes applying the project's egress allowlist."""
        from . import workflows as rw

        async with self._sf() as session:
            model, version, extras = await rw.target(session, claims, project, parameters.get("workflow"))
        params = {"workflowId": model.id, "version": version, "inputMode": "passThrough", "mode": "once",
                  "outputMode": "items"}
        if parameters.get("timeoutSeconds") is not None:
            params["timeoutSeconds"] = parameters["timeoutSeconds"]
        status, body = await self._run_node(
            claims, "ExecuteWorkflow", params, rw.input_items(parameters.get("input")), variables, secrets, None,
            node_name=rw.CALL_NODE, extras={**extras, "egress_allowlist": allowlist})
        if not body.get("ok") and status == 502:
            status = rw.STATUS_OF.get((body.get("error") or {}).get("code"), 502)
        return status, body

    async def _run_node(self, claims: BrokerClaims, node_type: str, parameters: dict, items: list,
                        variables: dict, secrets: set, guard: EgressGuard | None, *, node_name: str = "fn",
                        extras: dict[str, Any] | None = None) -> tuple[int, dict[str, Any]]:
        """Run one node on a one-node workflow. ``guard`` = the per-call egress guard (None: run_workflow, whose child
        is an ordinary run); ``extras`` are added to the run's extras."""
        from ..engine.workflow_runner import WorkflowRunner

        wf = Workflow(name=f"broker:{node_type}", nodes=[NodeDefinition(name=node_name, type=node_type, parameters=parameters)],
                      connections=[])
        initial = [NodeData(json=i if isinstance(i, dict) else {"value": i}) for i in items] or [NodeData(json={})]
        from ..services.subworkflow_wiring import subworkflow_invoker

        run_extras: dict[str, Any] = {"broker_origin": claims.origin, "parent_execution_id": claims.execution_id,
                                      **(extras or {})}
        http_options: dict[str, Any] | None = None
        if guard is not None:
            run_extras["broker_egress_guard"] = guard
            http_options = {"follow_redirects": False, "event_hooks": {"request": [guard.httpx_hook]}}
        runner = WorkflowRunner(db_session_factory=None)
        context = await runner.run(
            wf, node_name, initial, "manual",
            # Stage 8: an ExecuteWorkflow call from the SDK resolves versions and writes the child row like any other run
            subworkflow_invoker=subworkflow_invoker(self._sf),
            variables=variables, secret_values=secrets,
            project_id=claims.project_id, environment=claims.environment, team_id=claims.team_id,
            extras=run_extras,
            http_client_options=http_options,
            # the calling code's place in the call tree: a child it starts is checked against these (cycle, depth)
            execution_id=getattr(claims, "execution_id", None),
            call_stack=tuple(getattr(claims, "call_stack", ()) or ()),
            call_names=dict(getattr(claims, "call_names", None) or {}),
            execution_depth=int(getattr(claims, "depth", 0) or 0),
        )
        if guard is not None and guard.violation is not None:
            v = guard.violation
            return v.status, {"ok": False, "error": {"code": v.code, "message": v.message, **v.extra}}
        if context.errors:
            e = context.errors[0]
            # A node failure with a stable code (e.g. table_not_found, row_invalid) keeps it; others are node_failed.
            return 502, {"ok": False, "error": {"code": e.error_code or "node_failed", "message": e.error,
                                                **{k: v for k, v in (e.details or {}).items() if k not in ("code", "message")}}}
        out = context.node_states.get(node_name) or []
        metrics = dict(context.node_metrics.get(node_name) or {})
        for k in ("startedAt", "completedAt", "executionOrder", "retries", "maxRetries",
                  "inputDataSizeBytes", "outputDataSizeBytes", "activeOutputs"):
            metrics.pop(k, None)
        return 200, {"ok": True, "items": [d.json for d in out], "metadata": metrics}

    async def _log(self, call_id: str, claims: BrokerClaims, node_type: str, parameters: dict, variants: set,
                   body: dict, status: int, duration_ms: float) -> None:
        err = body.get("error") or {}
        try:
            from ..repositories.broker_call_repository import BrokerCallRepository
            async with self._sf() as session:
                await BrokerCallRepository(session).add(BrokerCallModel(
                    id=call_id, execution_id=claims.execution_id, project_id=claims.project_id,
                    environment=claims.environment, origin=claims.origin, node_type=node_type,
                    params_redacted=redact_deep(parameters, variants, expanded=True) if isinstance(parameters, dict) else {},
                    status="ok" if status == 200 else ("denied" if status in (401, 403, 422, 429) else "error"),
                    error_code=err.get("code"), error=err.get("message"), duration_ms=round(duration_ms, 2),
                ))
        except Exception:
            logger.warning("failed to log broker call", exc_info=True)

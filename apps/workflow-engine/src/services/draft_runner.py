"""Run unsaved module source for one request in the sandbox (UI-DESIGN §10.2 run-draft, and the
§10.1 generate self-test). Never saves code; writes made by the handler are real.

* Route resolution: only the draft's own decorators, with the router's matcher rules
  (static segments first, then fewest params, then longest); path params come from the matcher.
* Other modules: the project's SAVED sources; the draft replaces its own module (by id and name).
* Environment: ``default_environment`` secrets; ``authorization``/``cookie``/``x-api-key`` stripped;
  payload ``$vars`` are never expanded; route ``auth`` is not enforced.
* Tables: listed ``virtual_tables`` that do not exist are virtual (reads empty, writes table_not_found).
* Execution row: mode ``sandbox``, trigger ``draft_test`` (or ``generate_self_test``), redacted
  ``metadata.request/response`` and trace. Everything returned is redacted (D4).
"""

from __future__ import annotations

import time
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models import ApiRouteModel, CodeModuleModel, DataTableModel, ExecutionModel, ProjectModel
from .project_service import ProjectError

RESERVED_MODULE_NAMES = {"studio", "new", "generate", "default"}
STRIP_REQUEST_HEADERS = {"authorization", "cookie", "x-api-key", "host", "content-length"}
DEFAULT_TIMEOUT_S = 30.0


def check_module_name(name: str) -> None:
    from .code_module_service import MODULE_NAME_RE

    if not isinstance(name, str) or not MODULE_NAME_RE.match(name) or name in RESERVED_MODULE_NAMES:
        raise ProjectError(422, "invalid_module_name",
                           "module name must match ^[a-z_][a-z0-9_]*$ and not be one of "
                           + ", ".join(sorted(RESERVED_MODULE_NAMES)), module_name=name)


def draft_routes(source: str) -> list[dict[str, Any]]:
    """The draft's decorators (raises 422 syntax_error / invalid_decorator, duplicates included)."""
    from .api_route_service import default_name, path_shape
    from .code_module_service import discover_routes

    decls = discover_routes(source)
    seen: dict[tuple[str, str], dict[str, Any]] = {}
    for d in decls:
        shape = (d["method"], path_shape(d["path"]))
        if shape in seen:
            msg = f"{d['method']} {d['path']} is declared twice in this module (first on line {seen[shape]['line']})"
            raise ProjectError(422, "invalid_decorator", msg, line=d["line"],
                               errors=[{"line": d["line"], "column": None, "message": msg}])
        seen[shape] = d
        d["name"] = d["name"] or default_name(d["method"], d["path"])
    return decls


def resolve_route(decls: list[dict[str, Any]], method: str, path: str) -> tuple[dict[str, Any], dict[str, str]]:
    from .route_dispatcher import match_route

    spec, params, _ = match_route(decls, method.upper(), path)
    if spec is None:
        raise ProjectError(404, "route_not_in_source", f"{method.upper()} {path} is not declared in this module",
                           method=method.upper(), path=path,
                           available=[f"{d['method']} {d['path']}" for d in decls])
    return spec, params


async def project_secret_values(session: AsyncSession, project: ProjectModel, env: str) -> set[str]:
    from .variable_loader import load_run_secrets
    return await load_run_secrets(environment=env, team_id=project.team_id, project_id=project.id)


async def run_draft(session: AsyncSession, project: ProjectModel, *, source: str, module_name: str,
                    module_id: str | None, method: str, path: str, query: dict[str, Any] | None = None,
                    headers: dict[str, str] | None = None, body: Any = None,
                    virtual_tables: list[str] | None = None, trigger_type: str = "draft_test",
                    origin: str = "draft_test", read_only: bool = False, max_calls: int | None = None,
                    timeout_s: float | None = None, decls: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """Validate, run and record one draft request. Raises ProjectError for the pre-run HTTP errors."""
    from ..repositories.execution_repository import ExecutionRepository
    from ..repositories.node_output_repository import NodeOutputRepository
    from ..sandbox.backend import SandboxUnavailable, availability
    from ..sandbox.service import SandboxService
    from ..utils.ids import execution_id as gen_exec_id
    from ..routes.api_routes import run_trace
    from .python_handler import structured_error
    from .route_dispatcher import DispatchResult, RouteDispatcher, RouteRequest, redact_result
    from .schema_inference import infer_json_schema
    from .vars_substitution import expand_secret_variants, redact_deep

    check_module_name(module_name)
    saved_mod = None
    if module_id:
        saved_mod = await session.get(CodeModuleModel, module_id)
        if not saved_mod or saved_mod.project_id != project.id:
            raise ProjectError(404, "module_not_found", "Module not found")
    decls = decls if decls is not None else draft_routes(source)
    spec, params = resolve_route(decls, method, path)
    ok, reason = availability()
    if not ok:
        raise ProjectError(503, "sandbox_unavailable", f"the python sandbox is unavailable: {reason}")

    env = project.default_environment
    # other modules as saved; the draft replaces its own module (old name dropped on a rename)
    rows = (await session.execute(select(CodeModuleModel).where(CodeModuleModel.project_id == project.id))).scalars().all()
    modules = {m.name: m.source for m in rows if not (module_id and m.id == module_id)}
    modules[module_name] = source
    if timeout_s is None:
        timeout_s = DEFAULT_TIMEOUT_S
        if module_id:
            saved_route = (await session.execute(select(ApiRouteModel).where(
                ApiRouteModel.project_id == project.id, ApiRouteModel.handler_module_id == module_id,
                ApiRouteModel.handler_function == spec["function"]))).scalars().first()
            if saved_route is not None and saved_route.timeout_s:
                timeout_s = float(saved_route.timeout_s)
    existing_tables = set((await session.execute(select(DataTableModel.name).where(
        DataTableModel.project_id == project.id))).scalars().all())
    virtual = {t for t in (virtual_tables or []) if t not in existing_tables}

    clean_headers = {str(k).lower(): str(v) for k, v in (headers or {}).items()
                     if str(k).lower() not in STRIP_REQUEST_HEADERS}
    concrete = "/" + path.strip("/") if path.strip("/") else "/"
    req = {"method": method.upper(), "path": concrete, "route": spec["path"], "params": params,
           "query": dict(query or {}), "headers": clean_headers, "body": body}
    route_label = f"{spec['method']} {spec['path']}"
    trigger = {"type": trigger_type, "module_name": module_name, "module_id": module_id, "route": route_label,
               "path": concrete}

    exec_id = gen_exec_id()
    await ExecutionRepository(session, max_records=100).start(
        exec_id, module_id or f"draft:{module_name}", f"{route_label} (draft)", "sandbox", project_id=project.id,
        environment=env, metadata={"trigger": trigger, "labels": {"env": env}, "handler": "python_draft"})
    secrets = await project_secret_values(session, project, env)
    variants = expand_secret_variants(set(secrets))

    def red(v: Any) -> Any:
        return redact_deep(v, variants, expanded=True) if variants else v

    started = time.perf_counter()
    try:
        result, _calls = await SandboxService().run(
            kind="route", project_id=project.id, environment=env, execution_id=exec_id, origin=origin,
            payload={"req": req}, entry=f"{module_name}:{spec['function']}", modules=modules,
            timeout_s=float(timeout_s), team_id=project.team_id, read_only=read_only,
            virtual_tables=virtual or None, max_calls=max_calls)
    except SandboxUnavailable as e:
        row = await session.get(ExecutionModel, exec_id)
        row.status, row.end_time, row.error_count = "failed", datetime.now(), 1
        await session.commit()
        raise ProjectError(503, "sandbox_unavailable", f"the python sandbox is unavailable: {e}") from None
    duration = round((time.perf_counter() - started) * 1000, 2)

    trace = {"sandbox": result.trace()}
    error: dict[str, Any] | None = None
    if result.timed_out:
        status, resp_body, resp_headers = 504, {"error": "timeout", "execution_id": exec_id}, {}
        error = {"type": "Timeout", "message": f"the handler did not finish within {timeout_s:g} s",
                 "traceback": None, "line": None, "module": None, "code": "timeout", "details": None, "table": None}
    else:
        out = result.output or {}
        if not out.get("ok"):
            error = structured_error(out.get("error") or {"type": "SandboxError",
                                                          "message": (result.stderr or "no output")[-2000:]})
            error["code"] = error.get("code") or "handler_failed"
            status, resp_headers = 502, {}
            resp_body = {"error": "handler_failed", "execution_id": exec_id,
                         "message": f"{error.get('type')}: {error.get('message')}"}
            if error["code"] != "handler_failed":
                resp_body["error_code"] = error["code"]
        else:
            res = out.get("result") or {}
            status = int(res.get("status") or 200)
            resp_body = res.get("body")
            resp_headers = {str(k): str(v) for k, v in (res.get("headers") or {}).items()}
    dres = redact_result(DispatchResult(status, resp_body, resp_headers, "application/json", exec_id, error=error),
                         secrets)

    row = await session.get(ExecutionModel, exec_id)
    row.status, row.end_time = ("success" if error is None else "failed"), datetime.now()
    row.error_count = 0 if error is None else 1
    stored_trace = red({**trace, **({"error": dres.error} if dres.error else {})})
    row.exec_metadata = {**(row.exec_metadata or {}), "trace": stored_trace}
    await session.commit()
    if error is not None:
        await NodeOutputRepository(session).save_output(
            execution_id=exec_id, node_name=spec["function"], output=[], metrics={"status": "error"}, status="error",
            error=red(f"{error.get('type')}: {error.get('message')}"))
    await RouteDispatcher(session)._record_exchange(
        dres, RouteRequest(method=method.upper(), path=concrete, query=dict(query or {}), headers=clean_headers,
                           body=body), set(secrets))

    sample: dict[str, Any] = {"params": params, "query": dict(query or {})}
    if body is not None:
        sample["body"] = body
    full_trace = await run_trace(session, exec_id)
    return {
        "status": dres.status, "headers": {"content-type": dres.content_type, **dres.headers}, "body": dres.body,
        "duration_ms": float(duration), "execution_id": exec_id,
        "route": {"method": spec["method"], "path": spec["path"], "name": spec["name"], "function": spec["function"],
                  "line": spec["line"]},
        "trace": red(full_trace), "error": dres.error,
        "inferred": {"request_schema": infer_json_schema(sample),
                     "response_schema": infer_json_schema(dres.body) if isinstance(dres.body, (dict, list)) else None},
    }

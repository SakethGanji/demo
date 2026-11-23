"""Python route handlers in the sandbox (BACKEND-DESIGN §3.8, phase 8).

Each request: an execution row (mode "api", workflow_id = route id, G30), the module sources
(release snapshot or live), a sandbox run with a run-scoped broker, and the per-request trace
(sandbox exit/duration/stdout/stderr + broker call ids) in executions.metadata.trace.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession


_FRAME_RE = None


def structured_error(err: dict[str, Any]) -> dict[str, Any]:
    """Review D16: {type, message, traceback, line, module, code, details} from the runner's error.

    ``line``/``module`` come from the innermost traceback frame in a project module
    (``/run/job/in/modules/<name>.py``), so the UI can link straight to the editor line."""
    import re

    global _FRAME_RE
    if _FRAME_RE is None:
        _FRAME_RE = re.compile(r'File "/run/job/in/modules/([A-Za-z_][A-Za-z0-9_]*)\.py", line (\d+)')
    tb = err.get("traceback") or ""
    frames = _FRAME_RE.findall(tb)
    module, line = (frames[-1][0], int(frames[-1][1])) if frames else (None, None)
    message = err.get("message")
    if err.get("type") == "BrokerError" and err.get("code") and isinstance(message, str) \
            and message.startswith(f"{err['code']}: "):
        message = message[len(err["code"]) + 2:]
    return {"type": err.get("type"), "message": message, "traceback": tb or None, "line": line, "module": module,
            "code": err.get("code"), "details": err.get("details") or None,
            "table": (err.get("details") or {}).get("table")}  # UI-DESIGN §4.4: error.table for table_not_found


def _trusted_req(item: dict[str, Any], default_caller: dict[str, Any], clean_headers) -> dict[str, Any]:
    """The request the handler sees: identity headers removed, ``caller`` set by the engine (``item["caller"]`` /
    ``item["req"]["caller"]``, written only by server code) and never taken from client input."""
    req = dict(item["req"])
    req["headers"] = clean_headers(req.get("headers"))
    req["caller"] = item.get("caller") or req.get("caller") or default_caller
    return req


async def run_python_route(session: AsyncSession, project, spec: dict[str, Any], handler: dict[str, Any],
                           item: dict[str, Any], env: str, trigger: dict[str, Any], release) -> Any:
    from ..db.models import CodeModuleModel, ExecutionModel
    from ..repositories.node_output_repository import NodeOutputRepository
    from ..sandbox.backend import SandboxUnavailable
    from ..sandbox.service import SandboxService
    from ..utils.ids import execution_id as gen_exec_id
    from .code_module_service import CodeModuleService
    from .route_dispatcher import ANONYMOUS_CALLER, DispatchResult, clean_headers
    from .variable_loader import load_run_secrets
    from .vars_substitution import redact_deep

    if release is not None:
        modules = {m["name"]: m["source"] for m in release.snapshot.get("modules") or []}
        mod_name = next((m["name"] for m in release.snapshot.get("modules") or [] if m["module_id"] == handler.get("module_id")), None)
    else:
        modules = await CodeModuleService(session).sources(project.id)
        mod = await session.get(CodeModuleModel, handler.get("module_id"))
        mod_name = mod.name if mod else None
    if not mod_name:
        return DispatchResult(502, {"error": "handler_failed", "message": "handler module not found"})

    from ..repositories.execution_repository import ExecutionRepository

    exec_id = gen_exec_id()
    # Review D6: through the repository so retention (newest 100, cascading) applies here too.
    await ExecutionRepository(session).start(
        exec_id, spec["id"], f"{spec['method']} {spec['path']}", "api", project_id=project.id, environment=env,
        metadata={"trigger": trigger, "labels": {"env": env}, "handler": "python"})
    # Audit #4: include the project's TEAM secrets (stored trace + node-output error are redacted with these).
    secrets = await load_run_secrets(environment=env, team_id=project.team_id, project_id=project.id)

    async def finish(status: str, trace: dict[str, Any], error: str | None = None) -> None:
        row = await session.get(ExecutionModel, exec_id)
        row.status, row.end_time = status, datetime.now()
        row.error_count = 1 if error else 0
        row.exec_metadata = {**(row.exec_metadata or {}), "trace": redact_deep(trace, secrets)}
        await session.commit()
        if error:
            await NodeOutputRepository(session).save_output(
                execution_id=exec_id, node_name=handler.get("function") or "handler", output=[],
                metrics={"status": "error"}, status="error", error=redact_deep(error, secrets))

    try:
        result, calls = await SandboxService().run(
            kind="route", project_id=project.id, environment=env, execution_id=exec_id, origin="python_route",
            payload={"req": _trusted_req(item, ANONYMOUS_CALLER, clean_headers)}, entry=f"{mod_name}:{handler.get('function')}", modules=modules,
            timeout_s=float(spec.get("timeout_s") or 30), team_id=project.team_id,
            # run_workflow from this handler runs the versions this release pinned
            frame={"release_id": release.id} if release is not None else None)
    except SandboxUnavailable as e:
        await finish("failed", {"sandbox": None, "broker_calls": []}, f"sandbox unavailable: {e}")
        return DispatchResult(503, {"error": "sandbox_unavailable", "execution_id": exec_id}, execution_id=exec_id)

    trace = {"sandbox": result.trace(), "broker_calls": calls}
    if result.timed_out:
        await finish("failed", trace, "timed out")
        return DispatchResult(504, {"error": "timeout", "execution_id": exec_id}, execution_id=exec_id)
    out = result.output or {}
    if not out.get("ok"):
        err = structured_error(out.get("error") or {"type": "SandboxError", "message": (result.stderr or "no output")[-2000:]})
        await finish("failed", {**trace, "error": err}, f"{err.get('type')}: {err.get('message')}")
        body = {"error": "handler_failed", "execution_id": exec_id,
                "message": redact_deep(f"{err.get('type')}: {err.get('message')}", secrets)}
        if err.get("code"):
            body["error_code"] = err["code"]
        return DispatchResult(502, body, execution_id=exec_id, error=redact_deep(err, secrets))
    res = out.get("result") or {}
    await finish("success", trace)
    body = res.get("body")
    headers = {str(k): str(v) for k, v in (res.get("headers") or {}).items()}
    # a handler that sets its own Content-Type (CSV, HTML, plain text) is sent as that, not JSON-encoded
    ctype = next((headers.pop(k) for k in list(headers) if k.lower() == "content-type"), "application/json")
    return DispatchResult(int(res.get("status") or 200), redact_deep(body, secrets) if isinstance(body, (dict, list, str)) else body,
                          headers, ctype, exec_id)

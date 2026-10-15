"""Python route handlers in the sandbox (BACKEND-DESIGN §3.8, phase 8).

Each request: an execution row (mode "api", workflow_id = route id, G30), the module sources
(release snapshot or live), a sandbox run with a run-scoped broker, and the per-request trace
(sandbox exit/duration/stdout/stderr + broker call ids) in executions.metadata.trace.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession


async def run_python_route(session: AsyncSession, project, spec: dict[str, Any], handler: dict[str, Any],
                           item: dict[str, Any], env: str, trigger: dict[str, Any], release) -> Any:
    from ..db.models import CodeModuleModel, ExecutionModel
    from ..repositories.node_output_repository import NodeOutputRepository
    from ..sandbox.backend import SandboxUnavailable
    from ..sandbox.service import SandboxService
    from ..utils.ids import execution_id as gen_exec_id
    from .code_module_service import CodeModuleService
    from .route_dispatcher import DispatchResult
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

    exec_id = gen_exec_id()
    started = datetime.now()
    session.add(ExecutionModel(id=exec_id, workflow_id=spec["id"], workflow_name=f"{spec['method']} {spec['path']}",
                               status="running", mode="api", start_time=started, project_id=project.id, environment=env,
                               exec_metadata={"trigger": trigger, "labels": {"env": env}, "handler": "python"}))
    await session.commit()
    secrets = await load_run_secrets(environment=env, project_id=project.id)

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
            payload={"req": item["req"]}, entry=f"{mod_name}:{handler.get('function')}", modules=modules,
            timeout_s=float(spec.get("timeout_s") or 30), team_id=project.team_id)
    except SandboxUnavailable as e:
        await finish("failed", {"sandbox": None, "broker_calls": []}, f"sandbox unavailable: {e}")
        return DispatchResult(503, {"error": "sandbox_unavailable", "execution_id": exec_id}, execution_id=exec_id)

    trace = {"sandbox": result.trace(), "broker_calls": calls}
    if result.timed_out:
        await finish("failed", trace, "timed out")
        return DispatchResult(504, {"error": "timeout", "execution_id": exec_id}, execution_id=exec_id)
    out = result.output or {}
    if not out.get("ok"):
        err = out.get("error") or {"type": "SandboxError", "message": (result.stderr or "no output")[-2000:]}
        await finish("failed", {**trace, "error": err}, f"{err.get('type')}: {err.get('message')}")
        return DispatchResult(502, {"error": "handler_failed", "execution_id": exec_id,
                                    "message": redact_deep(f"{err.get('type')}: {err.get('message')}", secrets)},
                              execution_id=exec_id)
    res = out.get("result") or {}
    await finish("success", trace)
    body = res.get("body")
    return DispatchResult(int(res.get("status") or 200), redact_deep(body, secrets) if isinstance(body, (dict, list, str)) else body,
                          {str(k): str(v) for k, v in (res.get("headers") or {}).items()}, "application/json", exec_id)

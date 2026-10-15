"""Run node code (Code / PythonStep / CodeTool) in the sandbox with the run's broker scope."""

from __future__ import annotations

import os
from typing import Any

from .backend import SandboxUnavailable, get_backend


def sandbox_available() -> bool:
    return get_backend().available()[0]


def legacy_code_allowed() -> bool:
    """Legacy in-process Code node is only used when the sandbox is unavailable (or forced)."""
    v = (os.environ.get("WORKFLOW_CODE_NODE_LEGACY") or "auto").lower()
    if v in ("1", "true", "yes", "force"):
        return True
    if v in ("0", "false", "no"):
        return False
    return not sandbox_available()


class SandboxNodeError(RuntimeError):
    def __init__(self, message: str, code: str | None = None, details: dict | None = None) -> None:
        super().__init__(message)
        self.code = code or "sandbox_error"
        self.details = details or {}


async def run_in_sandbox(context: Any, *, kind: str, payload: dict[str, Any], entry: str | None = None,
                         timeout_s: float = 30.0, origin: str = "python_step") -> tuple[Any, dict[str, Any]]:
    """Returns (result, trace). Raises SandboxNodeError on any failure (code/details set)."""
    from ..db.session import async_session_factory
    from ..services.code_module_service import CodeModuleService
    from .service import SandboxService

    project_id = getattr(context, "project_id", None) or "proj_default"
    async with async_session_factory() as session:
        modules = await CodeModuleService(session).sources(project_id)
    try:
        result, calls = await SandboxService().run(
            kind=kind, project_id=project_id, environment=getattr(context, "environment", None) or "default",
            execution_id=context.execution_id, origin=origin, payload=payload, entry=entry, modules=modules,
            timeout_s=timeout_s, team_id=getattr(context, "team_id", "default"))
    except SandboxUnavailable as e:
        raise SandboxNodeError(f"sandbox unavailable: {e}", "sandbox_unavailable")
    trace = {**result.trace(), "broker_calls": calls}
    trace["stdout"], trace["stderr"] = trace["stdout"][-4000:], trace["stderr"][-4000:]
    if result.timed_out:
        raise SandboxNodeError(f"Code execution timed out ({int(timeout_s)} second limit)", "timeout", {"sandbox": trace})
    out = result.output or {}
    if not out.get("ok"):
        err = out.get("error") or {"type": "SandboxError", "message": (result.stderr or "no output")[-1000:]}
        if err.get("type") == "BrokerError":
            raise SandboxNodeError(f"Code execution failed: {err.get('message')}", err.get("code"), err.get("details") or {})
        if err.get("type") == "SyntaxError":
            raise SandboxNodeError(f"Syntax Error in code: {err.get('message')}", "syntax_error", {"line": err.get("line")})
        raise SandboxNodeError(f"Code execution failed: {err.get('message')}", "code_error",
                               {"type": err.get("type"), "traceback": (err.get("traceback") or "")[-2000:],
                                "line": err.get("line")})
    return out.get("result"), {**trace, "logs": out.get("logs") or []}

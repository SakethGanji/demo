"""SandboxService: prepare a job, run it in the backend with a run-scoped broker (§5.5, §3.8)."""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from typing import Any

from ..broker.tokens import BrokerClaims, registry
from .backend import SandboxJob, SandboxResult, SandboxUnavailable, availability, get_backend


class SandboxService:
    async def run(self, *, kind: str, project_id: str, environment: str, execution_id: str | None,
                  origin: str, payload: dict[str, Any], entry: str | None = None,
                  modules: dict[str, str] | None = None, timeout_s: float = 30.0,
                  team_id: str = "default", read_only: bool = False,
                  allowed_functions: set[str] | None = None,
                  virtual_tables: set[str] | None = None) -> tuple[SandboxResult, list[str]]:
        """Returns (result, broker call ids made during the run). Raises SandboxUnavailable."""
        backend = get_backend()
        ok, reason = availability(backend)
        if not ok:
            raise SandboxUnavailable(reason)
        job_dir = tempfile.mkdtemp(prefix="sbx-", dir="/tmp")
        broker_dir = os.path.join(job_dir, "b")
        from ..broker.service import function_index
        # Review D5: explicit function scope on every sandbox token; read_only for self-tests.
        claims = BrokerClaims(project_id=project_id, environment=environment, execution_id=execution_id,
                              origin=origin, team_id=team_id, read_only=read_only,
                              virtual_tables=set(virtual_tables) if virtual_tables else None,
                              allowed_functions=set(allowed_functions) if allowed_functions else set(function_index().values()))
        token = registry.mint(claims, ttl_s=timeout_s + 5)
        try:
            os.makedirs(os.path.join(job_dir, "in", "modules"))
            os.makedirs(os.path.join(job_dir, "out"))
            os.makedirs(broker_dir)
            for name, src in (modules or {}).items():
                with open(os.path.join(job_dir, "in", "modules", f"{name}.py"), "w") as f:
                    f.write(src)
            with open(os.path.join(job_dir, "in", "input.json"), "w") as f:
                json.dump({"kind": kind, "entry": entry, "payload": payload, "token": token}, f, default=str)
            for d in (job_dir, os.path.join(job_dir, "in"), os.path.join(job_dir, "out")):
                os.chmod(d, 0o700)
            from ..broker.uds_server import BrokerSocket
            async with BrokerSocket(broker_dir):
                result = await backend.run(SandboxJob(job_dir=job_dir, broker_dir=broker_dir, timeout_s=timeout_s))
            return result, list(claims.trace)
        finally:
            registry.revoke(token)
            shutil.rmtree(job_dir, ignore_errors=True)


__all__ = ["SandboxService", "SandboxUnavailable"]

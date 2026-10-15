"""SandboxBackend interface + selection (BACKEND-DESIGN §5.5).

``WORKFLOW_SANDBOX_BACKEND`` = ``bwrap`` (default when bubblewrap works) | ``disabled``.
There is never a silent in-process fallback for python handlers.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any, Protocol


@dataclass
class SandboxJob:
    job_dir: str          # host dir containing in/ (ro) and out/ (rw)
    broker_dir: str | None  # host dir holding broker.sock (rw bind), or None
    timeout_s: float = 30.0
    memory_mb: int = 1024


@dataclass
class SandboxResult:
    exit_code: int | None
    stdout: str
    stderr: str
    output: dict[str, Any] | None
    duration_ms: float
    timed_out: bool = False
    truncated: bool = False
    extra: dict[str, Any] = field(default_factory=dict)

    def trace(self) -> dict[str, Any]:
        return {"exit_code": self.exit_code, "duration_ms": self.duration_ms, "timed_out": self.timed_out,
                "stdout": self.stdout, "stderr": self.stderr, "truncated": self.truncated}


class SandboxUnavailable(RuntimeError):
    pass


class SandboxBackend(Protocol):
    name: str

    def available(self) -> tuple[bool, str]: ...

    async def run(self, job: SandboxJob) -> SandboxResult: ...


class DisabledBackend:
    name = "disabled"

    def __init__(self, reason: str = "disabled by configuration") -> None:
        self.reason = reason

    def available(self) -> tuple[bool, str]:
        return False, self.reason

    async def run(self, job: SandboxJob) -> SandboxResult:
        raise SandboxUnavailable(self.reason)


@lru_cache
def get_backend() -> SandboxBackend:
    choice = (os.environ.get("WORKFLOW_SANDBOX_BACKEND") or "auto").lower()
    if choice == "disabled":
        return DisabledBackend()
    if shutil.which("bwrap") is None:
        return DisabledBackend("bubblewrap (bwrap) is not installed")
    from .bwrap import BwrapBackend
    b = BwrapBackend()
    ok, reason = b.available()
    return b if ok else DisabledBackend(reason)


def backend_status() -> dict[str, Any]:
    b = get_backend()
    ok, reason = b.available()
    out: dict[str, Any] = {"backend": b.name, "available": ok}
    if ok and b.name == "bwrap":
        try:
            out["version"] = subprocess.run(["bwrap", "--version"], capture_output=True, text=True, timeout=5).stdout.split()[-1]
        except Exception:
            pass
    else:
        out["reason"] = reason
    return out

"""bubblewrap backend: no network, no host files, no environment (BACKEND-DESIGN §5.5)."""

from __future__ import annotations

import asyncio
import json
import os
import resource
import signal
import subprocess
import sys
import sysconfig
import time
from functools import lru_cache
from pathlib import Path

from .backend import SandboxJob, SandboxResult

STREAM_CAP = 64 * 1024
OUTPUT_CAP = 10 * 1024 * 1024


def _site_packages() -> str | None:
    p = sysconfig.get_paths().get("purelib")
    return p if p and Path(p).is_dir() else None


class BwrapBackend:
    name = "bwrap"

    @lru_cache
    def available(self) -> tuple[bool, str]:
        try:
            out = subprocess.run(self._argv_base() + ["/usr/bin/python3", "-I", "-S", "-c", "print(1)"],
                                 capture_output=True, text=True, timeout=15)
        except Exception as e:  # pragma: no cover
            return False, f"bwrap failed: {e}"
        if out.returncode != 0 or out.stdout.strip() != "1":
            return False, f"bwrap probe failed: {out.stderr.strip()[:300]}"
        return True, ""

    def _argv_base(self) -> list[str]:
        argv = ["bwrap", "--unshare-all", "--die-with-parent", "--new-session", "--cap-drop", "ALL",
                "--hostname", "sandbox", "--clearenv",
                "--setenv", "PATH", "/usr/bin", "--setenv", "HOME", "/tmp", "--setenv", "PYTHONDONTWRITEBYTECODE", "1",
                "--setenv", "OPENBLAS_NUM_THREADS", "1", "--setenv", "OMP_NUM_THREADS", "1",
                "--ro-bind", "/usr", "/usr"]
        for link in ("/lib", "/lib64", "/bin"):
            if Path(link).is_symlink():
                argv += ["--symlink", os.readlink(link), link]
            elif Path(link).exists():
                argv += ["--ro-bind", link, link]
        argv += ["--proc", "/proc", "--dev", "/dev", "--size", str(64 * 1024 * 1024), "--tmpfs", "/tmp"]
        return argv

    def argv(self, job: SandboxJob, sdk_dir: str) -> list[str]:
        argv = self._argv_base() + ["--ro-bind", sdk_dir, "/opt/studio"]
        site = _site_packages()
        if site:
            argv += ["--ro-bind", site, "/opt/site"]
        argv += ["--ro-bind", os.path.join(job.job_dir, "in"), "/run/job/in",
                 "--bind", os.path.join(job.job_dir, "out"), "/run/job/out"]
        if job.broker_dir:
            argv += ["--bind", job.broker_dir, "/run/broker"]
        argv += ["--chdir", "/tmp", "/usr/bin/python3", "-I", "-S", "/opt/studio/runner.py"]
        return argv

    async def run(self, job: SandboxJob) -> SandboxResult:
        from ..sdk.generate import sdk_dir

        argv = self.argv(job, str(sdk_dir()))
        mem = job.memory_mb * 1024 * 1024
        cpu = int(job.timeout_s) + 2

        def limits() -> None:  # runs in the child before exec
            resource.setrlimit(resource.RLIMIT_AS, (mem, mem))
            resource.setrlimit(resource.RLIMIT_CPU, (cpu, cpu))
            resource.setrlimit(resource.RLIMIT_FSIZE, (16 * 1024 * 1024, 16 * 1024 * 1024))
            resource.setrlimit(resource.RLIMIT_NOFILE, (64, 64))
            resource.setrlimit(resource.RLIMIT_CORE, (0, 0))

        started = time.perf_counter()
        proc = await asyncio.create_subprocess_exec(
            *argv, stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            preexec_fn=limits, start_new_session=True, env={"PATH": "/usr/bin:/bin"},
        )
        timed_out = False
        try:
            out, err = await asyncio.wait_for(proc.communicate(), timeout=job.timeout_s)
        except asyncio.CancelledError:
            # UI-DESIGN §10.1 cancellation: a cancelled run never leaves a sandbox process group behind.
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            try:
                await asyncio.shield(proc.wait())
            except BaseException:
                pass
            raise
        except asyncio.TimeoutError:
            timed_out = True
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            out, err = await proc.communicate()
        duration = round((time.perf_counter() - started) * 1000, 2)
        truncated = len(out) > STREAM_CAP or len(err) > STREAM_CAP
        output = None
        out_path = Path(job.job_dir) / "out" / "out.json"
        if out_path.exists() and out_path.stat().st_size <= OUTPUT_CAP:
            try:
                output = json.loads(out_path.read_text())
            except ValueError:
                output = None
        elif out_path.exists():
            truncated = True
        return SandboxResult(
            exit_code=proc.returncode, stdout=out[:STREAM_CAP].decode("utf-8", "replace"),
            stderr=err[:STREAM_CAP].decode("utf-8", "replace"), output=output, duration_ms=duration,
            timed_out=timed_out, truncated=truncated,
        )

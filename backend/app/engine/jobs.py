"""Plumbing shared by the job modules that run in their own OS process (the fit worker, learning curves, shift
checks, labelling queues, annotator probabilities): start one, find it again after an API restart, stop its group."""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

from .. import config


def dev_reload() -> bool:
    return os.environ.get("ENSEMBLE_DEV_RELOAD", "") not in ("", "0", "false")


def alive(pid: int | None, module: str) -> bool:
    """Is pid a live process running `module` (python -m <module>)? Reads /proc, so a recycled pid does not count."""
    if not pid:
        return False
    try:
        cmd = Path(f"/proc/{pid}/cmdline").read_bytes().replace(b"\0", b" ").decode(errors="replace")
    except OSError:
        return False
    return module in cmd


def kill_group(pid: int, sig: int = signal.SIGTERM) -> None:
    try:
        os.killpg(os.getpgid(pid), sig)
    except (ProcessLookupError, PermissionError):
        pass


def watch_arg() -> str:
    """The pid a job process should watch: the API, or under dev reload the reloader above it."""
    return str(os.getppid()) if dev_reload() else str(os.getpid())


def spawn(module: str, args: list[str], log_path: Path, extra_env: dict[str, str] | None = None) -> subprocess.Popen:
    """`python -m <module> <args>` in its own session, output appended to log_path."""
    env = {**os.environ, "PYTHONUNBUFFERED": "1", "MLFLOW_DISABLE_AGENT_HINT": "1", **(extra_env or {}),
           "PYTHONPATH": os.pathsep.join(x for x in (str(config.BACKEND_DIR), os.environ.get("PYTHONPATH", "")) if x)}
    with open(log_path, "ab") as out:
        return subprocess.Popen([sys.executable, "-m", module, *args], cwd=config.BACKEND_DIR, env=env, stdout=out,
                                stderr=subprocess.STDOUT, start_new_session=True)


def watch_parent(ppid: int, anchor: bool = False) -> None:
    """Exit if the API dies, so a job never runs on as an orphan. With `anchor` (the pid in the job's argv: the API, or
    under dev reload uvicorn's reloader) watch that pid instead of our parent, so a reload does not stop the job."""
    def gone() -> bool:
        if not anchor:
            return os.getppid() != ppid
        try:
            os.kill(ppid, 0)
            return False
        except ProcessLookupError:
            return True
        except PermissionError:
            return False

    def loop():
        while True:
            if gone():
                os._exit(3)
            time.sleep(1)

    threading.Thread(target=loop, daemon=True).start()

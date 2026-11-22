"""F-X5a (X5 J4): SIGTERM with a busy heavy pool.

uvicorn answers SIGTERM by waiting for every open connection and background task, with no time limit unless it was
started with --timeout-graceful-shutdown. A request waiting on a heavy-pool call (SHAP, the Model Doctor, a build run by
the embedded worker) kept the API alive for minutes, and a hung one for ever.

`install(app)` (called at the end of the lifespan's startup, so uvicorn's own handler is in place) wraps the SIGTERM /
SIGINT handler:

1. at once: uvicorn's handler (no new connections; open requests may finish) and the embedded worker stops claiming jobs;
2. after `ENSEMBLE_SHUTDOWN_GRACE_S` (default 20 s), if the process is still up: held jobs are put back on the queue and
   their processes stopped, the heavy pool's processes are killed (a request waiting on one gets its error at once),
   and uvicorn is told to stop waiting (its force-exit flag);
3. `ENSEMBLE_SHUTDOWN_HARD_S` (default 10 s) after that, the process exits (`os._exit`) whatever is still running.
"""

from __future__ import annotations

import logging
import os
import signal
import threading
import time
from typing import Any

log = logging.getLogger("ensemble.shutdown")


def _secs(name: str, default: float) -> float:
    try:
        return max(0.0, float(os.environ.get(name, "") or default))
    except ValueError:
        return default


def grace_s() -> float:
    return _secs("ENSEMBLE_SHUTDOWN_GRACE_S", 20.0)


def hard_s() -> float:
    return _secs("ENSEMBLE_SHUTDOWN_HARD_S", 10.0)


class Graceful:
    def __init__(self, app: Any) -> None:
        self.app = app
        self.started = threading.Event()
        self.prev: dict[int, Any] = {}

    def install(self) -> None:
        if threading.current_thread() is not threading.main_thread():
            return
        for sig in (signal.SIGTERM, signal.SIGINT):
            prev = signal.getsignal(sig)
            self.prev[sig] = prev
            signal.signal(sig, self._handler)

    def _handler(self, sig: int, frame: Any) -> None:
        prev = self.prev.get(sig)
        if callable(prev):
            prev(sig, frame)  # uvicorn: should_exit (a second SIGINT: force exit)
        if not self.started.is_set():
            self.started.set()
            log.warning("signal %s: stopping; held work gets %.0f s, then it is stopped", signal.Signals(sig).name, grace_s())
            threading.Thread(target=self._run, args=(prev,), name="graceful-shutdown", daemon=True).start()

    def _stop_claiming(self) -> None:
        w = getattr(self.app.state, "worker", None)
        if w is not None:
            w._stop.set()  # no new jobs; held ones keep running through the grace period

    def _force(self, prev: Any) -> None:
        st = self.app.state
        try:
            if getattr(st, "agent", None) is not None:
                st.agent.shutting_down = True
            w = getattr(st, "worker", None)
            if w is not None:
                from .jobs import dev_reload

                w.stop(requeue=True, kill=not dev_reload())  # held jobs back on the queue, their processes stopped
        except Exception:  # noqa: BLE001
            log.exception("stopping the worker")
        try:
            if getattr(st, "heavy", None) is not None:
                st.heavy.shutdown()  # pool processes killed: a request waiting on one gets its error now
        except Exception:  # noqa: BLE001
            log.exception("stopping the heavy pool")
        srv = getattr(prev, "__self__", None)
        if srv is not None and hasattr(srv, "force_exit"):
            srv.force_exit = True  # uvicorn stops waiting for connections and tasks

    def _run(self, prev: Any) -> None:
        self._stop_claiming()
        t_end = time.monotonic() + grace_s()
        while time.monotonic() < t_end:
            time.sleep(0.25)
        log.warning("grace period over: stopping held jobs and the heavy pool")
        self._force(prev)
        time.sleep(hard_s())
        log.error("still running %.0f s after the grace period: exiting", hard_s())
        logging.shutdown()
        os._exit(0)


def install(app: Any) -> Graceful:
    g = Graceful(app)
    g.install()
    app.state.graceful = g
    return g

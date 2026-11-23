"""Cell executors: one module per kind, each exposing ``async def run(ctx, cell, inputs) -> CellOutcome``.

``REGISTRY`` maps a kind to the dotted module that implements it. ``get_executor(kind)`` imports on first use, so a
module that is missing (not built yet) or slow to import (browser, ask) never breaks engine start-up; the run loop turns
``ExecutorUnavailable`` into a cell error.
"""

from __future__ import annotations

import importlib
from dataclasses import dataclass, field
from types import ModuleType
from typing import Any, Awaitable, Callable, Protocol

ResultStatus = str  # one of schemas.notebook.ResultStatus


@dataclass
class CellOutcome:
    """What an executor returns. The run loop persists it as a ``notebook_cell_results`` row.

    - ``output``: the preview stored in the DB (``{columns, rows<=50, row_count, ...}`` or a summary dict).
    - ``full_rows``: the complete row set; the run loop writes it to storage and sets ``output_ref``.
    - ``status``: usually ``success`` / ``error``; ``change`` returns ``needs_approval``, ``wait`` ``waiting``.
    - ``error``: ``{code, message, ...}`` when status is ``error``.
    - ``rows`` (for_each only): per-row outcomes, persisted with ``row_index``.
    """

    status: ResultStatus = "success"
    output: dict[str, Any] | None = None
    full_rows: list[dict[str, Any]] | None = None
    error: dict[str, Any] | None = None
    execution_id: str | None = None
    job_id: str | None = None
    ms: int | None = None
    rows: list["CellOutcome"] = field(default_factory=list)

    @classmethod
    def fail(cls, code: str, message: str, **extra: Any) -> "CellOutcome":
        return cls(status="error", error={"code": code, "message": message, **extra})

    @property
    def ok(self) -> bool:
        return self.status != "error"


@dataclass
class CellContext:
    """What an executor gets besides its cell and resolved inputs. Built by the run loop (``runner.py``).

    - ``notebook_id`` / ``project_id`` / ``environment``: where the cell runs (``environment`` may be None = the
      project's default).
    - ``run_id`` / ``run_n``: the notebook run; ``attribution`` = ``{triggered_by, run_as, via_agent}`` of that run.
    - ``row`` / ``row_index``: the current for-each row (None at top level).
    - ``run_body``: for_each only, ``await ctx.run_body(row, i) -> CellOutcome`` runs the body cells for one row and
      persists their results (row_index = i); the returned outcome's status is ``error`` when a body cell failed.
    - ``session()``: a fresh ``AsyncSession`` (``async with ctx.session() as s``); never share one across tasks.
    """

    notebook_id: str
    project_id: str
    environment: str | None
    run_id: str
    run_n: int
    attribution: dict[str, Any] = field(default_factory=dict)
    row: Any = None
    row_index: int | None = None
    run_body: Callable[..., Awaitable["CellOutcome"]] | None = None
    only_rows: list[int] | None = None  # for_each retry: run just these row indexes
    previous: dict[str, Any] | None = None  # for_each retry: the output being retried (to merge the other rows)

    async def set_run_status(self, status: str, stop: dict[str, Any] | None = None) -> None:
        """``running`` <-> ``waiting`` while a wait cell polls (only while the run is still live)."""
        from sqlalchemy import update

        from ....db.notebook_models import NotebookRunModel

        async with self.session() as s:
            await s.execute(update(NotebookRunModel).where(NotebookRunModel.id == self.run_id,
                                                           NotebookRunModel.status.in_(("running", "waiting")))
                            .values(status=status, stop=stop))
            await s.commit()

    def session(self) -> Any:
        from ....db.session import async_session_factory

        return async_session_factory()

    def via(self, cell_id: str) -> str:
        return f"notebook:{self.notebook_id}/cell:{cell_id}/run:{self.run_id}"

    def attribution_for(self, cell_id: str) -> dict[str, Any]:
        """The ``executions.metadata.attribution`` of a run this cell starts."""
        return {**{k: self.attribution.get(k) for k in ("triggered_by", "run_as", "via_agent")}, "via": self.via(cell_id)}


class ExecutorUnavailable(RuntimeError):
    """The executor module for a kind is not importable (not built yet, or a dependency is missing)."""

    def __init__(self, kind: str, reason: str):
        super().__init__(f"no executor for {kind!r}: {reason}")
        self.kind = kind
        self.reason = reason


class Executor(Protocol):
    run: Callable[..., Awaitable[CellOutcome]]


# kind -> module path relative to this package. browser/ask are W2's; all imports are lazy.
REGISTRY: dict[str, str] = {
    "query": ".query",
    "http": ".http",
    "workflow": ".workflow",
    "rule": ".rule",
    "change": ".change",
    "data": ".data",
    "store": ".store",
    "for_each": ".for_each",
    "note": ".note",
    "wait": ".wait",
    "browser": ".browser",  # lazy: jobs-service client
    "ask": ".ask",  # lazy: LLM client
}

_loaded: dict[str, ModuleType] = {}


def get_executor(kind: str) -> ModuleType:
    """Import (once) and return the executor module for ``kind``. Raises ``ExecutorUnavailable``."""
    mod = _loaded.get(kind)
    if mod is not None:
        return mod
    path = REGISTRY.get(kind)
    if path is None:
        raise ExecutorUnavailable(kind, "unknown kind")
    try:
        mod = importlib.import_module(path, __name__)
    except ImportError as e:
        raise ExecutorUnavailable(kind, str(e)) from e
    if not callable(getattr(mod, "run", None)):
        raise ExecutorUnavailable(kind, f"{mod.__name__} has no run()")
    _loaded[kind] = mod
    return mod


__all__ = ["CellContext", "CellOutcome", "Executor", "ExecutorUnavailable", "REGISTRY", "get_executor"]

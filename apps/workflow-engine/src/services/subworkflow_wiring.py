"""One place that decides which invoker a run site hands to ``WorkflowRunner.run`` (DESIGN-STAGE8-REUSE 3.2-3.3).

Every site that runs a workflow outside ``ExecutionService.run_saved_workflow`` (the studio SSE stream, webhooks, cron, the
broker, the test runner) calls ``subworkflow_invoker(...)`` instead of building the invoker itself, so a sub-workflow
call behaves the same everywhere: the version is resolved (draft, latest, N or the release pin), the guards run, and the
child gets its own committed execution row linked to the parent.

Session semantics: the invoker opens its own session per call from ``session_factory`` (default: the engine's
``async_session_factory``), so a child row is committed even when the parent run fails or rolls back. A caller that has its
own factory (cron, broker) passes it so the child lands in the same database as the parent.

Unsaved runs need no argument. A test run with ``persist=False`` is recognised by the invoker itself (it reads
``parent.test_options.persist``): it resolves versions and applies every guard exactly like a saved run, but writes no row
and runs the child on an in-memory runner (section 3.3). A test run with ``persist=True`` writes the child as ``mode='test'``.
"""

from __future__ import annotations

from typing import Any, Callable


def subworkflow_invoker(session_factory: Callable[[], Any] | None = None) -> Any:
    """The ``SubworkflowInvoker`` for a run site. ``session_factory`` defaults to the engine's own."""
    from .subworkflow_service import DbSubworkflowInvoker

    return DbSubworkflowInvoker(session_factory)

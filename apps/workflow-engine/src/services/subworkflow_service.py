"""Running a child workflow from the database side (DESIGN-STAGE8-REUSE sections 3.3-3.6, 4.1-4.2).

* ``resolve_child`` picks the workflow definition a call runs: the draft, the live published version, a numbered version,
  or the version a release snapshot pinned.
* ``DbSubworkflowInvoker`` is the ``SubworkflowInvoker`` the engine uses for runs that go through ``ExecutionService``.
  It guards the call, writes the child's own execution row in its own session (so the row survives a failing parent),
  runs the child in process with a timeout, and completes the row.

Unsaved runs (a test with ``persist=False``) take the same path without any row, so version resolution and the guards
behave identically.
"""

from __future__ import annotations

import asyncio
import copy
import logging
from datetime import datetime
from typing import Any, Callable

from ..engine.subworkflow import (
    RECURSION_LIMIT,
    SUBWORKFLOW_FAILED,
    SUBWORKFLOW_TIMEOUT,
    SUBWORKFLOW_VERSION_MISSING,
    SubworkflowCall,
    SubworkflowError,
    SubworkflowResult,
    check_guards,
    effective_timeout,
    failed_result,
    find_trigger_node,
    not_found,
    result_from_context,
    tagged_event_callback,
    timeout_result,
)
from ..engine.test_mode import UnmockedEffectError
from ..engine.types import ExecutionError, RecursionLimitError

logger = logging.getLogger(__name__)


# --- version resolution (section 4.2) -----------------------------------------------------------------------------


def _missing(message: str, **details: Any) -> SubworkflowError:
    return SubworkflowError(SUBWORKFLOW_VERSION_MISSING, message, details or None)


def pin_key(parent_version_id: int | None, parent_node: str) -> str:
    """The key of a release pin: the parent's version row id and the calling node (section 4.4)."""
    return f"{parent_version_id}:{parent_node}"


async def version_number_of(session: Any, version_id: int | None) -> int | None:
    """The child's ``version_number`` for a version row id (None for a draft run)."""
    if version_id is None:
        return None
    from sqlalchemy import text

    return (await session.execute(
        text("SELECT version_number FROM workflow_versions WHERE id = :i"), {"i": version_id})).scalar()


async def version_id_for(session: Any, workflow_id: str, version_number: int) -> int | None:
    from sqlalchemy import text

    return (await session.execute(
        text("SELECT id FROM workflow_versions WHERE workflow_id = :w AND version_number = :n"),
        {"w": workflow_id, "n": int(version_number)})).scalar()


async def resolve_child(
    session: Any, parent_ctx: Any, parent_node: str, workflow_id: str, version: str | int,
) -> tuple[Any, int | None]:
    """``(StoredWorkflow, version_row_id | None)`` for a call; ``None`` means the draft.

    Raises ``SubworkflowError`` with ``SUBWORKFLOW_NOT_FOUND`` (missing, or in another project) or
    ``SUBWORKFLOW_VERSION_MISSING``. A run inside a release never reads the live publish pointer: it resolves through
    ``extras["release_pins"]``. A release without pins predates Stage 8: it falls back to draft-environment resolution
    and the run gets a warning (section 4.5).
    """
    from ..repositories.workflow_repository import WorkflowRepository

    repo = WorkflowRepository(session)
    model = await repo.get_model(workflow_id)
    parent_project = getattr(parent_ctx, "project_id", None)
    if model is None or (parent_project and model.project_id != parent_project):
        raise not_found(workflow_id)  # 404-hides-existence: another project's workflow looks absent
    name = model.name

    extras = parent_ctx.extras if isinstance(getattr(parent_ctx, "extras", None), dict) else {}
    pins = extras.get("release_pins")
    if pins is not None:
        vid = pins.get(pin_key(parent_ctx.version_id, parent_node))
        if vid is None:
            raise _missing(f"Sub-workflow '{name}' isn't pinned in this release (node '{parent_node}').",
                           workflow_id=workflow_id, node=parent_node)
        stored = await repo.get_version_workflow(workflow_id, int(vid))
        if stored is None:
            raise _missing(f"Sub-workflow '{name}' has no version {vid} (pinned by this release).",
                           workflow_id=workflow_id, version_id=vid)
        return stored, int(vid)
    if extras.get("release_id"):
        warnings = extras.setdefault("run_warnings", [])
        msg = "release predates sub-workflow pinning; re-cut the release"
        if msg not in warnings:
            warnings.append(msg)

    if version == "draft":
        return await repo.get(workflow_id), None
    if version == "latest":
        vid = model.published_version_id
        if vid is None:
            raise _missing(f"Sub-workflow '{name}' has never been published.", workflow_id=workflow_id)
    else:
        try:
            number = int(version)
        except (TypeError, ValueError):
            raise _missing(f"Sub-workflow '{name}' has no version {version}.", workflow_id=workflow_id) from None
        vid = await version_id_for(session, workflow_id, number)
        if vid is None:
            raise _missing(f"Sub-workflow '{name}' has no version {number}.", workflow_id=workflow_id, version=number)
    stored = await repo.get_version_workflow(workflow_id, int(vid))
    if stored is None:
        raise _missing(f"Sub-workflow '{name}' has no version {version}.", workflow_id=workflow_id)
    return stored, int(vid)


# --- the invoker (section 3.3) ------------------------------------------------------------------------------------


def _is_unsaved(parent: Any) -> bool:
    """A test run with ``persist=False`` writes no rows, so neither does its child."""
    opts = getattr(parent, "test_options", None)
    return opts is not None and not getattr(opts, "persist", True)


def _inline_options(call: SubworkflowCall, workflow: Any) -> Any:
    """The test options of an inline child (section 6.3): the parent's clock, seed and persistence; mocks from the child
    version's own ``test_defaults.mocks`` and from the parent test's ``"<node>/<child node>"`` keys (those win)."""
    from ..engine.test_mode import TestRunOptions

    popts = call.parent_context.test_options
    prefix = f"{call.parent_node}/"
    own = {k[len(prefix):]: v for k, v in (popts.mocks or {}).items() if k.startswith(prefix) and len(k) > len(prefix)}
    defaults = dict(((getattr(workflow, "test_defaults", None) or {}).get("mocks")) or {})
    return TestRunOptions(
        mocks=copy.deepcopy(own), default_mocks=copy.deepcopy(defaults),
        now=popts.now, seed=popts.seed, persist=popts.persist,
    )


def _child_node_status(name: str, child: Any) -> str:
    """Same rule as the test runner's per-node status, for one node of the inline child."""
    if any(e.node_name == name for e in child.errors):
        return "error"
    emitted = child.test_outputs.get(name)
    if emitted is not None:
        return "success" if any(emitted.values()) else "no_output"
    if name in child.node_states or name in child.node_metrics:
        return "success"
    return "not_run"


def _surface_inline_child(call: SubworkflowCall, workflow: Any, child: Any, res: SubworkflowResult) -> SubworkflowResult:
    """Copy the inline child's test outputs, requests and node status into the parent under ``"<node>/<child node>"``,
    so assertions can name them; turn an unmocked child step into ``TEST_UNMOCKED_EFFECT`` naming that path."""
    parent = call.parent_context
    prefix = f"{call.parent_node}/"
    for node, ports in child.test_outputs.items():
        emitted = parent.test_outputs.setdefault(prefix + node, {})
        for port, items in ports.items():
            if items:
                emitted.setdefault(port, []).extend(items)
    for node, reqs in child.test_requests.items():
        parent.test_requests.setdefault(prefix + node, []).extend(copy.deepcopy(reqs))
    statuses = parent.extras.setdefault("test_inline_status", {})
    for n in workflow.nodes:
        key = prefix + n.name
        status = _child_node_status(n.name, child)
        if statuses.get(key) in (None, "not_run") or status != "not_run":
            statuses[key] = status
    unmocked = next((e for e in child.errors if e.error_code == "TEST_UNMOCKED_EFFECT"), None)
    if unmocked is not None:
        effect = (unmocked.details or {}).get("effect") or "an outside system"
        path = prefix + unmocked.node_name
        res.status = "failed"
        res.items = []
        res.error = ExecutionError(
            node_name=path, error=str(UnmockedEffectError(path, str(effect))), timestamp=datetime.now(),
            error_code="TEST_UNMOCKED_EFFECT", details={"node": path, "effect": effect},
        )
    return res


class DbSubworkflowInvoker:
    """Runs sub-workflow calls with a committed child execution row. One instance can serve a whole root run."""

    def __init__(self, session_factory: Callable[[], Any] | None = None, runner_factory: Callable[..., Any] | None = None) -> None:
        self._session_factory = session_factory
        self._runner_factory = runner_factory

    def _factory(self) -> Callable[[], Any]:
        if self._session_factory is not None:
            return self._session_factory
        from ..db import session as db_session  # looked up per call, so tests can swap the factory

        return db_session.async_session_factory

    def _runner(self, persist: bool) -> Any:
        from ..engine.workflow_runner import WorkflowRunner

        if self._runner_factory is not None:
            return self._runner_factory(self._factory() if persist else None)
        return WorkflowRunner(db_session_factory=self._factory() if persist else None)

    async def invoke(self, call: SubworkflowCall) -> SubworkflowResult:
        from ..repositories.execution_repository import ExecutionRepository
        from ..utils.ids import execution_id as gen_exec_id
        from .api_catalog import load_api_specs

        parent = call.parent_context
        persist = not _is_unsaved(parent)
        factory = self._factory()
        attempts = parent.extras.setdefault("_subworkflow_calls", {})
        call_index = attempts[call.parent_node] = attempts.get(call.parent_node, 0) + 1

        async with factory() as session:
            # 1. resolve (guards 1-2), 2. guards 3-6; nothing is written before all of them pass
            try:
                stored, version_id = await resolve_child(session, parent, call.parent_node, call.workflow_id, call.version)
            except SubworkflowError as e:
                return failed_result(call, e)
            if stored is None:  # the draft vanished between the model read and the load
                return failed_result(call, not_found(call.workflow_id))
            version_number = await version_number_of(session, version_id)
            workflow = stored.workflow
            guard = check_guards(parent, call.workflow_id, stored.name, workflow)
            if guard is not None:
                return failed_result(call, guard, child_name=stored.name, version_number=version_number)
            trigger = find_trigger_node(workflow)
            assert trigger is not None  # check_guards refused a child without one

            seconds = effective_timeout(call)
            child_extras = {"api_specs": await load_api_specs(session, workflow.nodes)}

            exec_id = gen_exec_id()
            repo = ExecutionRepository(session)
            if persist:
                row_exists = await self._parent_row_exists(session, parent.execution_id)
                mode = "test" if parent.mode == "test" else "sub_workflow"
                await repo.start(
                    exec_id, stored.id, stored.name, mode,
                    project_id=stored.project_id, environment=parent.environment,
                    metadata={
                        "labels": {"env": parent.environment},
                        "trigger": {"type": "sub_workflow", "parent_execution_id": parent.execution_id,
                                    "parent_node": call.parent_node},
                        "subworkflow": {"call_index": call_index, "version_number": version_number,
                                        "version_spec": str(call.version)},
                    },
                    # a parent that never had a row (cron and similar paths insert at completion) can't be a FK target
                    parent_execution_id=parent.execution_id if row_exists else None,
                    parent_node_name=call.parent_node,
                    depth=parent.execution_depth + 1,
                    root_execution_id=parent.root_execution_id or parent.execution_id,
                    workflow_version_id=version_id,
                )

            runner = self._runner(persist)
            inline_opts = _inline_options(call, workflow) if call.test_inline else None
            extra_kwargs = {"test_options": inline_opts} if inline_opts is not None else {}
            run = runner.run_subworkflow(
                workflow=workflow,
                start_node_name=trigger.name,
                input_data=list(call.items),
                parent_context=parent,
                on_event=tagged_event_callback(parent, call.parent_node, call.workflow_id),
                workflow_id=call.workflow_id,
                execution_id=exec_id,
                extras=child_extras,
                version_id=version_id,
                **extra_kwargs,
            )
            try:
                child = await asyncio.wait_for(run, seconds)
            except asyncio.TimeoutError:
                if persist:
                    await self._finish_timeout(session, exec_id, stored.name, seconds)
                return timeout_result(
                    call, seconds, child_name=stored.name, version_number=version_number,
                    child_execution_id=exec_id if persist else None)
            except asyncio.CancelledError:
                if persist:
                    await self._finish_cancelled(session, exec_id)
                raise
            except RecursionLimitError as e:
                err = SubworkflowError(RECURSION_LIMIT, str(e))
                if persist:
                    await repo.mark_failed(exec_id, str(err), RECURSION_LIMIT)
                return failed_result(call, err, child_name=stored.name, version_number=version_number)
            except Exception as e:  # the runner reports node errors in the context; this is a bug or infrastructure
                logger.exception("sub-workflow %s crashed", call.workflow_id)
                if persist:
                    await repo.mark_failed(exec_id, str(e), SUBWORKFLOW_FAILED)
                res = failed_result(call, SubworkflowError(SUBWORKFLOW_FAILED, str(e)), child_name=stored.name,
                                    version_number=version_number)
                res.child_execution_id = exec_id if persist else None
                return res

            if persist:
                await repo.complete(child, stored.id, stored.name)
            res = result_from_context(
                child, workflow, workflow_id=call.workflow_id, child_name=stored.name,
                version_number=version_number, child_execution_id=exec_id)
            if not persist:
                res.child_execution_id = None  # an unsaved run has no row for the UI to link to
            if inline_opts is not None:
                res = _surface_inline_child(call, workflow, child, res)
            return res

    @staticmethod
    async def _parent_row_exists(session: Any, execution_id: str) -> bool:
        from sqlalchemy import text

        return (await session.execute(text("SELECT 1 FROM executions WHERE id = :i"), {"i": execution_id})).first() is not None

    @staticmethod
    async def _finish_timeout(session: Any, exec_id: str, name: str, seconds: float) -> None:
        """Section 3.5: failed, ``SUBWORKFLOW_TIMEOUT``, ``cancelled_at`` set."""
        from ..db.models import ExecutionModel
        from ..repositories.execution_repository import ExecutionRepository

        try:
            await ExecutionRepository(session).mark_failed(
                exec_id, f"Sub-workflow '{name}' timed out after {seconds:g}s.", SUBWORKFLOW_TIMEOUT)
            row = await session.get(ExecutionModel, exec_id)
            if row is not None:
                row.cancelled_at = datetime.now()
                await session.commit()
        except Exception:
            logger.warning("could not record the timeout of %s", exec_id, exc_info=True)

    @staticmethod
    async def _finish_cancelled(session: Any, exec_id: str) -> None:
        """The parent was cancelled while the child ran: the child ends with it."""
        from ..repositories.execution_repository import ExecutionRepository

        try:
            await ExecutionRepository(session).cancel(exec_id)
        except BaseException:
            logger.warning("could not record the cancellation of %s", exec_id, exc_info=True)


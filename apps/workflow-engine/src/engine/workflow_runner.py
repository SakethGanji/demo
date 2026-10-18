"""
Workflow runner - executes DAG-based workflows.

Uses a queue-based BFS approach for node execution.
"""

from __future__ import annotations

import asyncio
import copy
import random
import json
import logging
from datetime import datetime
from time import perf_counter
from typing import Any, TYPE_CHECKING, Literal

import httpx

logger = logging.getLogger(__name__)

#: trigger-like nodes that emit the test's input items instead of running when a test starts at them
_TEST_TRIGGER_TYPES = {"Start", "Webhook", "Cron", "ChatInput", "ErrorTrigger", "ExecuteWorkflowTrigger"}

from .effects import effect_of
from .expression_engine import ExpressionEngine, expression_engine
from .logging import execution_id_var, execution_variables_var, execution_secrets_var
from .test_mode import (
    BadMockError,
    MockPlan,
    TestRunOptions,
    UnmockedEffectError,
    frozen_now_var,
    http_mock_client,
    plan_mock,
    refusing_client,
    test_rng_var,
)
from .types import (
    ExecutionContext,
    ExecutionError,
    ExecutionEvent,
    ExecutionEventCallback,
    ExecutionEventType,
    ExecutionJob,
    NodeData,
    NodeDefinition,
    NodeExecutionResult,
    RecursionLimitError,
    Workflow,
    WorkflowStopSignal,
    NO_OUTPUT_SIGNAL,
)

if TYPE_CHECKING:
    from .node_registry import NodeRegistryClass


class WorkflowRunner:
    """Executes DAG-based workflows using queue-based processing."""

    def __init__(self, db_session_factory=None, event_bus=None) -> None:
        from .node_registry import node_registry
        self._registry: NodeRegistryClass = node_registry
        self._db_session = db_session_factory  # None = skip persistence
        self._event_bus = event_bus  # PgEventBus | None
        self._unsaved_runs: set[str] = set()  # test runs with persist=False: no node_outputs rows

    async def run(
        self,
        workflow: Workflow,
        start_node_name: str,
        initial_data: list[NodeData] | None = None,
        mode: Literal["manual", "webhook", "cron"] = "manual",
        on_event: ExecutionEventCallback | None = None,
        workflow_repository: Any | None = None,
        execution_id: str | None = None,
        version_id: int | None = None,
        pre_populated_states: dict[str, list[NodeData]] | None = None,
        variables: dict[str, str] | None = None,
        secret_values: set[str] | None = None,
        project_id: str | None = None,
        environment: str | None = None,
        team_id: str = "default",
        extras: dict[str, Any] | None = None,
        http_client_options: dict[str, Any] | None = None,
        stop_at: str | None = None,
        pending_inputs: dict[str, dict[str, list[NodeData]]] | None = None,
        test_options: TestRunOptions | None = None,
    ) -> ExecutionContext:
        """
        Run a workflow from a starting node.

        Args:
            workflow: The workflow definition to execute
            start_node_name: Name of the node to start execution from
            initial_data: Initial input data for the start node
            mode: Execution mode (manual, webhook, cron)
            on_event: Optional callback for real-time execution events
            workflow_repository: Optional repository for subworkflow loading
            stop_at: Name of a node after which no downstream jobs are queued
            pending_inputs: Pre-seeded Merge inputs ({"<node>:<run_index>": {"<src>:<output>": items}}),
                used when a partial run starts at a multi-input node
            test_options: run in test mode (mocks, frozen clock, no credentials, no real network); see engine/test_mode.py

        Returns:
            ExecutionContext with all node states and errors
        """
        if initial_data is None:
            initial_data = [NodeData(json={})]

        context = self._create_context(workflow, mode)
        if execution_id:
            context.execution_id = execution_id
        context.workflow_repository = workflow_repository
        context.on_event = on_event
        if variables:
            context.variables = variables
        if test_options is not None:
            context.test_options = test_options
            context.frozen_now = test_options.now
            context.rng = random.Random(test_options.seed)
            # secret $vars never reach a test: they resolve to a placeholder
            secrets = {v for v in (secret_values or set()) if v}
            context.variables = {k: (f"<secret:{k}>" if v in secrets else v) for k, v in context.variables.items()}
            if not test_options.persist:
                self._unsaved_runs.add(context.execution_id)
        context.project_id = project_id
        context.environment = environment
        context.team_id = team_id or "default"
        context.stop_at = stop_at
        if pending_inputs:
            context.pending_inputs.update(pending_inputs)
        if extras:
            context.extras.update(extras)
        context.extras.setdefault("secret_values", secret_values or set())

        # Set execution_id for structured logging correlation
        execution_id_var.set(context.execution_id)
        # Expose team variables to per-node ExpressionEngine.create_context calls
        execution_variables_var.set(context.variables)
        # Expose decrypted secret VALUES for nodes that need to redact them out
        # of user-visible execution metadata (e.g. HttpRequest.requestUrl).
        execution_secrets_var.set(secret_values or set())

        # Pre-populate states for retry-from-failure
        if pre_populated_states:
            context.node_states = pre_populated_states

        total_nodes = len(workflow.nodes)
        completed_nodes = 0

        # Build node lookup dict for O(1) access
        node_map: dict[str, NodeDefinition] = {n.name: n for n in workflow.nodes}

        # Emit execution start event
        self._emit_event(
            on_event,
            ExecutionEvent(
                type=ExecutionEventType.EXECUTION_START,
                execution_id=context.execution_id,
                timestamp=datetime.now(),
                progress={"completed": 0, "total": total_nodes},
            ),
        )

        # Find start node
        start_node = node_map.get(start_node_name)
        if not start_node:
            error = f'Start node "{start_node_name}" not found in workflow'
            self._emit_event(
                on_event,
                ExecutionEvent(
                    type=ExecutionEventType.EXECUTION_ERROR,
                    execution_id=context.execution_id,
                    timestamp=datetime.now(),
                    error=error,
                ),
            )
            raise ValueError(error)

        # Initialize job queue with start node
        queue: list[ExecutionJob] = [
            ExecutionJob(
                node_name=start_node_name,
                input_data=initial_data,
                source_node=None,
                source_output="main",
                run_index=0,
            )
        ]

        # Track which nodes have been executed (for progress tracking)
        executed_nodes: set[str] = set()

        # Process jobs until queue is empty
        # Safety limit to prevent infinite loops (configurable via workflow settings)
        iteration = 0
        max_iterations = workflow.settings.get("max_iterations", 1000)

        # Shared HTTP client for all nodes. Timeout sized for slow LLM-backed
        # endpoints (e.g. PromptLab full-stage eval on ~1000-row datasets with
        # long system prompts can take 60–180s).
        client_opts: dict[str, Any] = {"timeout": 900.0, "follow_redirects": True}
        if http_client_options:
            client_opts.update(http_client_options)
        clock_token = rng_token = None
        if test_options is not None:
            # the expression helpers and the AI tools read the frozen clock / seeded RNG from these
            clock_token = frozen_now_var.set(test_options.now)
            rng_token = test_rng_var.set(context.rng)
        # a test run never touches the network: HttpRequest gets its own mock client per job
        async with (refusing_client() if test_options is not None else httpx.AsyncClient(**client_opts)) as http_client:
            context.http_client = http_client

            while queue and iteration < max_iterations:
                iteration += 1

                # Process all currently available jobs in parallel (BFS layer)
                current_batch = queue[:]
                queue.clear()

                tasks = []
                for job in current_batch:
                    # Emit node start event (only first time for each node)
                    node_def = node_map.get(job.node_name)
                    if node_def and job.node_name not in executed_nodes:
                        self._emit_event(
                            on_event,
                            ExecutionEvent(
                                type=ExecutionEventType.NODE_START,
                                execution_id=context.execution_id,
                                timestamp=datetime.now(),
                                node_name=job.node_name,
                                node_type=node_def.type,
                                progress={"completed": completed_nodes, "total": total_nodes},
                            ),
                        )

                    tasks.append(self._process_job(context, job, queue, node_map, on_event))

                # Run batch
                results = await asyncio.gather(*tasks, return_exceptions=True)

                # Process results
                for job, result in zip(current_batch, results):
                    had_error = False
                    if isinstance(result, Exception):
                        logger.error(f"Error processing job {job.node_name}: {result}")
                        had_error = True
                    else:
                        had_error = result

                    # Track completion and emit node complete event
                    node_def = node_map.get(job.node_name)
                    if job.node_name not in executed_nodes:
                        executed_nodes.add(job.node_name)
                        completed_nodes += 1

                        if not had_error and node_def:
                            self._emit_event(
                                on_event,
                                ExecutionEvent(
                                    type=ExecutionEventType.NODE_COMPLETE,
                                    execution_id=context.execution_id,
                                    timestamp=datetime.now(),
                                    node_name=job.node_name,
                                    node_type=node_def.type,
                                    data=context.node_states.get(job.node_name),
                                    progress={"completed": completed_nodes, "total": total_nodes},
                                    metrics=context.node_metrics.get(job.node_name),
                                ),
                            )

            if iteration >= max_iterations:
                error = "Execution exceeded maximum iterations (possible infinite loop)"
                context.errors.append(
                    ExecutionError(
                        node_name="WorkflowRunner",
                        error=error,
                        timestamp=datetime.now(),
                    )
                )
                self._emit_event(
                    on_event,
                    ExecutionEvent(
                        type=ExecutionEventType.EXECUTION_ERROR,
                        execution_id=context.execution_id,
                        timestamp=datetime.now(),
                        error=error,
                    ),
                )

        if clock_token is not None and rng_token is not None:
            frozen_now_var.reset(clock_token)
            test_rng_var.reset(rng_token)
        self._unsaved_runs.discard(context.execution_id)

        # Revoke any broker tokens minted for this run (Code node, agent tools, ...).
        if context.parent_execution_id is None:
            from ..broker.tokens import registry as _broker_tokens
            for k in [k for k in context.extras if k.startswith("broker_token:")]:
                _broker_tokens.revoke(context.extras.pop(k))

        # Emit execution complete event
        self._emit_event(
            on_event,
            ExecutionEvent(
                type=ExecutionEventType.EXECUTION_COMPLETE,
                execution_id=context.execution_id,
                timestamp=datetime.now(),
                progress={"completed": completed_nodes, "total": total_nodes},
            ),
        )

        return context

    async def run_subworkflow(
        self,
        workflow: Workflow,
        start_node_name: str,
        input_data: list[NodeData],
        parent_context: ExecutionContext,
        on_event: ExecutionEventCallback | None = None,
    ) -> ExecutionContext:
        """
        Run a workflow as a subworkflow of another execution.

        Inherits execution context from parent (depth tracking, HTTP client, etc.)
        while maintaining isolation for node states.

        Args:
            workflow: The subworkflow definition to execute
            start_node_name: Name of the node to start execution from
            input_data: Input data for the subworkflow
            parent_context: Parent execution context for depth tracking
            on_event: Optional callback for real-time execution events

        Returns:
            ExecutionContext with subworkflow results

        Raises:
            RecursionLimitError: If max execution depth is exceeded
        """
        # Check recursion limit
        if parent_context.execution_depth >= parent_context.max_execution_depth:
            raise RecursionLimitError(
                f"Maximum subworkflow depth of {parent_context.max_execution_depth} exceeded"
            )

        # Create child context with incremented depth
        child_context = self._create_context(workflow, parent_context.mode)
        child_context.execution_depth = parent_context.execution_depth + 1
        child_context.max_execution_depth = parent_context.max_execution_depth
        child_context.parent_execution_id = parent_context.execution_id
        child_context.workflow_repository = parent_context.workflow_repository

        # Share HTTP client for efficiency
        child_context.http_client = parent_context.http_client

        # Subworkflows inherit the parent's $vars so secrets resolve identically.
        child_context.variables = parent_context.variables
        child_context.project_id = parent_context.project_id
        child_context.environment = parent_context.environment
        child_context.team_id = parent_context.team_id
        child_context.extras = parent_context.extras
        child_context.test_options = parent_context.test_options
        child_context.frozen_now = parent_context.frozen_now
        child_context.rng = parent_context.rng
        child_context.test_requests = parent_context.test_requests

        # Propagate event callback for nested subworkflows
        child_context.on_event = on_event

        # Run the subworkflow
        # Note: We don't use self.run() directly because it creates its own HTTP client
        # Instead we run the core execution logic with the pre-configured context
        return await self._run_with_context(
            workflow=workflow,
            start_node_name=start_node_name,
            initial_data=input_data,
            context=child_context,
            on_event=on_event,
        )

    async def _run_with_context(
        self,
        workflow: Workflow,
        start_node_name: str,
        initial_data: list[NodeData],
        context: ExecutionContext,
        on_event: ExecutionEventCallback | None = None,
        stop_at: str | None = None,
    ) -> ExecutionContext:
        """
        Run a workflow with a pre-configured execution context.

        Used by run_subworkflow to execute with inherited context.
        """
        context.stop_at = stop_at
        total_nodes = len(workflow.nodes)
        completed_nodes = 0

        # Build node lookup dict for O(1) access
        node_map: dict[str, NodeDefinition] = {n.name: n for n in workflow.nodes}

        # Emit execution start event
        self._emit_event(
            on_event,
            ExecutionEvent(
                type=ExecutionEventType.EXECUTION_START,
                execution_id=context.execution_id,
                timestamp=datetime.now(),
                progress={"completed": 0, "total": total_nodes},
            ),
        )

        # Find start node
        start_node = node_map.get(start_node_name)
        if not start_node:
            error = f'Start node "{start_node_name}" not found in workflow'
            self._emit_event(
                on_event,
                ExecutionEvent(
                    type=ExecutionEventType.EXECUTION_ERROR,
                    execution_id=context.execution_id,
                    timestamp=datetime.now(),
                    error=error,
                ),
            )
            raise ValueError(error)

        # Initialize job queue with start node
        queue: list[ExecutionJob] = [
            ExecutionJob(
                node_name=start_node_name,
                input_data=initial_data,
                source_node=None,
                source_output="main",
                run_index=0,
            )
        ]

        # Track which nodes have been executed
        executed_nodes: set[str] = set()

        # Process jobs until queue is empty
        iteration = 0
        max_iterations = workflow.settings.get("max_iterations", 1000)

        while queue and iteration < max_iterations:
            iteration += 1

            # Process all currently available jobs in parallel (BFS layer)
            current_batch = queue[:]
            queue.clear()

            tasks = []
            for job in current_batch:
                node_def = node_map.get(job.node_name)
                if node_def and job.node_name not in executed_nodes:
                    self._emit_event(
                        on_event,
                        ExecutionEvent(
                            type=ExecutionEventType.NODE_START,
                            execution_id=context.execution_id,
                            timestamp=datetime.now(),
                            node_name=job.node_name,
                            node_type=node_def.type,
                            progress={"completed": completed_nodes, "total": total_nodes},
                        ),
                    )

                tasks.append(self._process_job(context, job, queue, node_map, on_event))

            # Run batch
            results = await asyncio.gather(*tasks, return_exceptions=True)

            # Process results
            for job, result in zip(current_batch, results):
                had_error = False
                if isinstance(result, Exception):
                    logger.error(f"Error processing job {job.node_name}: {result}")
                    had_error = True
                else:
                    had_error = result

                node_def = node_map.get(job.node_name)
                if job.node_name not in executed_nodes:
                    executed_nodes.add(job.node_name)
                    completed_nodes += 1

                    if not had_error and node_def:
                        self._emit_event(
                            on_event,
                            ExecutionEvent(
                                type=ExecutionEventType.NODE_COMPLETE,
                                execution_id=context.execution_id,
                                timestamp=datetime.now(),
                                node_name=job.node_name,
                                node_type=node_def.type,
                                data=context.node_states.get(job.node_name),
                                progress={"completed": completed_nodes, "total": total_nodes},
                                metrics=context.node_metrics.get(job.node_name),
                            ),
                        )

        if iteration >= max_iterations:
            error = "Execution exceeded maximum iterations (possible infinite loop)"
            context.errors.append(
                ExecutionError(
                    node_name="WorkflowRunner",
                    error=error,
                    timestamp=datetime.now(),
                )
            )
            self._emit_event(
                on_event,
                ExecutionEvent(
                    type=ExecutionEventType.EXECUTION_ERROR,
                    execution_id=context.execution_id,
                    timestamp=datetime.now(),
                    error=error,
                ),
            )

        # Emit execution complete event
        self._emit_event(
            on_event,
            ExecutionEvent(
                type=ExecutionEventType.EXECUTION_COMPLETE,
                execution_id=context.execution_id,
                timestamp=datetime.now(),
                progress={"completed": completed_nodes, "total": total_nodes},
            ),
        )

        return context

    def _emit_event(
        self, on_event: ExecutionEventCallback | None, event: ExecutionEvent
    ) -> None:
        """Helper to emit events safely (local callback + optional PG bus)."""
        if on_event:
            try:
                on_event(event)
            except Exception:
                logger.exception("Error in execution event callback")

        # Publish to PgEventBus if available (fire-and-forget)
        if self._event_bus:
            try:
                event_dict = {
                    "type": event.type.value,
                    "executionId": event.execution_id,
                    "timestamp": event.timestamp.isoformat(),
                }
                if event.node_name:
                    event_dict["nodeName"] = event.node_name
                if event.node_type:
                    event_dict["nodeType"] = event.node_type
                if event.error:
                    event_dict["error"] = event.error
                if event.progress:
                    event_dict["progress"] = event.progress
                if event.data:
                    event_dict["data"] = [{"json": d.json} for d in event.data]
                if event.metrics:
                    event_dict["metrics"] = event.metrics
                asyncio.create_task(
                    self._event_bus.publish(event.execution_id, event_dict)
                )
            except Exception:
                logger.debug("Failed to publish event to PG bus", exc_info=True)

    async def _persist_node_output(
        self,
        execution_id: str,
        node_name: str,
        output: list[NodeData] | None,
        metrics: dict[str, Any] | None,
        status: str,
        run_index: int = 0,
        error: str | None = None,
    ) -> None:
        """Write node output to DB. Non-critical — execution continues on failure."""
        if not self._db_session or execution_id in self._unsaved_runs:
            return
        try:
            from ..repositories.node_output_repository import NodeOutputRepository
            async with self._db_session() as session:
                repo = NodeOutputRepository(session)
                serialized = [{"json": d.json} for d in output] if output else []
                await repo.save_output(
                    execution_id=execution_id,
                    node_name=node_name,
                    output=serialized,
                    metrics=metrics,
                    status=status,
                    run_index=run_index,
                    error=error,
                )
        except Exception:
            logger.warning(f"Failed to persist node output for {node_name}", exc_info=True)

    async def _process_job(
        self,
        context: ExecutionContext,
        job: ExecutionJob,
        queue: list[ExecutionJob],
        node_map: dict[str, NodeDefinition],
        on_event: ExecutionEventCallback | None = None,
    ) -> bool:
        """
        Process a single execution job.

        Returns True if there was an error, False otherwise.
        """
        node_def = node_map.get(job.node_name)

        if not node_def:
            error = f'Node "{job.node_name}" not found'
            context.errors.append(
                ExecutionError(
                    node_name=job.node_name,
                    error=error,
                    timestamp=datetime.now(),
                )
            )
            self._emit_event(
                on_event,
                ExecutionEvent(
                    type=ExecutionEventType.NODE_ERROR,
                    execution_id=context.execution_id,
                    timestamp=datetime.now(),
                    node_name=job.node_name,
                    error=error,
                ),
            )
            return True

        node = self._registry.get(node_def.type)

        input_count = getattr(node, "input_count", 1)

        # Handle multi-input nodes (like Merge)
        if input_count > 1 or input_count == float("inf"):
            handled = self._handle_multi_input_node(
                context,
                node_def,
                job.input_data,
                job.source_node,
                job.source_output,
                queue,
                job.run_index,
            )
            if not handled:
                return False  # Waiting for more inputs

        # Disabled nodes pass their input straight to "main" (like n8n)
        if node_def.disabled:
            return await self._process_disabled_job(context, node_def, job, queue, node_map)

        # Test mode: mocks beat pinned data; an outside step with neither fails the run (never calls out)
        plan = None
        opts = context.test_options
        if opts is not None:
            context.test_inputs.setdefault(job.node_name, []).extend(job.input_data)
            for mocks in (opts.mocks, opts.default_mocks):
                if job.node_name in mocks:
                    plan = plan_mock(mocks[job.node_name], job.node_name, node_def.type, job.input_data, context)
                    if plan is not None:
                        break
            if plan is not None and isinstance(plan.error, BadMockError):
                return await self._fail_test_setup(context, node_def, job, queue, node_map, plan.error, on_event)
            if node_def.type in _TEST_TRIGGER_TYPES and job.source_node is None:
                # a workflow-target test starts at the trigger, whose output is replaced by the test's input items
                plan = MockPlan("items", {"main": list(job.input_data)})
            effect = effect_of(node_def.type)
            if plan is None and not node_def.pinned_data and effect != "none":
                return await self._fail_test_setup(
                    context, node_def, job, queue, node_map, UnmockedEffectError(job.node_name, effect), on_event
                )

        # Check for pinned data
        if node_def.pinned_data and plan is None:
            context.node_states[job.node_name] = node_def.pinned_data
            context.last_completed_node = job.node_name
            self._queue_next_nodes(
                context,
                node_def,
                NodeExecutionResult(outputs={"main": node_def.pinned_data}),
                queue,
                node_map,
                job.run_index,
            )
            return False

        # Resolve expressions in parameters
        resolved_node_def = self._resolve_node_parameters(context, node_def, job.input_data)

        # Resolve credentials if node references one
        credential_id = resolved_node_def.parameters.get("credentialId")
        if credential_id and self._db_session and opts is None:  # test runs never resolve credentials
            try:
                cred_data = await self._resolve_credential(credential_id, context.team_id)
                if cred_data:
                    resolved_params = {**resolved_node_def.parameters, "_credentials": cred_data}
                    resolved_node_def = NodeDefinition(
                        name=resolved_node_def.name,
                        type=resolved_node_def.type,
                        parameters=resolved_params,
                        position=resolved_node_def.position,
                        pinned_data=resolved_node_def.pinned_data,
                        retry_on_fail=resolved_node_def.retry_on_fail,
                        retry_delay=resolved_node_def.retry_delay,
                        continue_on_fail=resolved_node_def.continue_on_fail,
                        disabled=resolved_node_def.disabled,
                        on_error=resolved_node_def.on_error,
                        contract=resolved_node_def.contract,
                    )
            except Exception:
                logger.warning(f"Failed to resolve credential {credential_id}", exc_info=True)

        if opts is not None and effect_of(node_def.type) != "none" and node_def.type != "HttpRequest":
            # what an outside step would have been asked (HttpRequest records real requests through its mock client)
            ectx = ExpressionEngine.create_context(
                job.input_data, context.node_states, context.execution_id, 0, variables=context.variables
            )
            params = expression_engine.resolve(copy.deepcopy(node_def.parameters), ectx)
            params = {k: v for k, v in params.items() if k != "_credentials"}
            context.test_requests.setdefault(job.node_name, []).append({"params": params})

        # Execute node with retry and error handling
        result: NodeExecutionResult | None = None
        max_retries = node_def.retry_on_fail
        retry_delay = node_def.retry_delay
        last_error: Exception | None = None
        retries_used = 0

        # Track execution order
        context.execution_order += 1
        execution_order = context.execution_order

        node_start = perf_counter()
        started_at = datetime.now()

        for attempt in range(max_retries + 1):
            try:
                if plan is None:
                    result = await node.execute(context, resolved_node_def, job.input_data)
                else:
                    result = await self._execute_planned(plan, node, context, resolved_node_def, job)
                last_error = None
                retries_used = attempt
                break  # Success, exit retry loop
            except WorkflowStopSignal as stop:
                # Handle graceful workflow stop
                if stop.error_type == "error":
                    context.errors.append(
                        ExecutionError(
                            node_name=job.node_name,
                            error=stop.message,
                            timestamp=datetime.now(),
                        )
                    )
                # Clear the queue to stop execution
                queue.clear()
                # Store stop info in node state for visibility
                context.node_states[job.node_name] = [
                    NodeData(json={"_stopped": True, "_message": stop.message, "_type": stop.error_type})
                ]
                return stop.error_type == "error"  # Return True if error, False if warning
            except Exception as e:
                last_error = e
                retries_used = attempt
                if attempt < max_retries:
                    if opts is None:  # a test run never waits between retries
                        await asyncio.sleep(retry_delay / 1000)
                    continue

        completed_at = datetime.now()
        execution_time_ms = round((perf_counter() - node_start) * 1000, 2)

        error_routed = False

        # Handle final error after all retries exhausted
        if (last_error or not result) and node_def.on_error == "error_output":
            # Route the failed items to the "error" output; the run continues and the node counts as a success
            error_msg = str(last_error) if last_error else "Unknown execution error"
            error_items = [
                NodeData(json={**item.json, "error": {"message": error_msg, "node": job.node_name}})
                for item in (job.input_data or [NodeData(json={})])
            ]
            result = NodeExecutionResult(outputs={"main": None, "error": error_items})
            error_routed = True
        elif last_error or not result:
            error_msg = str(last_error) if last_error else "Unknown execution error"
            retry_info = f" (after {max_retries + 1} attempts)" if max_retries > 0 else ""
            error_code = getattr(last_error, "code", None) if isinstance(getattr(last_error, "code", None), str) else None
            error_details = getattr(last_error, "details", None) if isinstance(getattr(last_error, "details", None), dict) else None
            context.errors.append(
                ExecutionError(
                    node_name=job.node_name,
                    error=f"{error_msg}{retry_info}",
                    timestamp=datetime.now(),
                    error_code=error_code,
                    details=error_details,
                )
            )

            # Build error metrics
            error_metrics: dict[str, Any] = {
                "startedAt": started_at.isoformat(),
                "completedAt": completed_at.isoformat(),
                "executionTimeMs": execution_time_ms,
                "executionOrder": execution_order,
                "retries": retries_used,
                "maxRetries": max_retries,
                "inputItemCount": len(job.input_data),
                "status": "error",
            }
            if error_code:
                error_metrics["errorCode"] = error_code
                error_metrics["errorDetails"] = error_details or {}
            context.node_metrics[job.node_name] = error_metrics

            self._emit_event(
                on_event,
                ExecutionEvent(
                    type=ExecutionEventType.NODE_ERROR,
                    execution_id=context.execution_id,
                    timestamp=datetime.now(),
                    node_name=job.node_name,
                    node_type=node_def.type,
                    error=error_msg,
                    metrics=error_metrics,
                ),
            )

            # Persist error output to DB
            await self._persist_node_output(
                execution_id=context.execution_id,
                node_name=job.node_name,
                output=None,
                metrics=error_metrics,
                status="error",
                run_index=job.run_index,
                error=error_msg,
            )

            # Check continueOnFail
            if node_def.on_error == "continue":
                result = NodeExecutionResult(
                    outputs={"main": [NodeData(json={"error": error_msg, "_errorNode": job.node_name})]}
                )
            else:
                # Stop execution: clear the queue so no downstream nodes run
                queue.clear()
                # Propagate NO_OUTPUT to any multi-input nodes waiting on this branch
                self._propagate_no_output(context, node_def, queue, node_map, job.run_index)
                return True

        if node_def.on_error == "error_output" and not error_routed and "error" not in result.outputs:
            # No failure: tell any Merge on the error branch that it will get nothing
            result = NodeExecutionResult(outputs={**result.outputs, "error": None}, metadata=result.metadata)

        # Update run count for loop support
        current_count = context.node_run_counts.get(job.node_name, 0)
        context.node_run_counts[job.node_name] = current_count + 1

        # Store node output (main output for state)
        if error_routed:
            main_output = result.outputs["error"]
        else:
            # First non-empty output, so an If whose items all went "false" still saves its state
            main_output = result.outputs.get("main") or next((v for v in result.outputs.values() if v), None)
        if main_output:
            context.node_states[job.node_name] = main_output
            context.last_completed_node = job.node_name

        # Build success metrics
        output_item_count = sum(
            len(items) for items in result.outputs.values() if items
        )
        active_outputs = [k for k, v in result.outputs.items() if v]
        # Estimate data sizes
        input_size = 0
        for item in job.input_data:
            try:
                input_size += len(json.dumps(item.json))
            except (TypeError, ValueError):
                pass
        output_size = 0
        for items in result.outputs.values():
            if items:
                for item in items:
                    try:
                        output_size += len(json.dumps(item.json))
                    except (TypeError, ValueError):
                        pass

        node_metrics: dict[str, Any] = {
            "startedAt": started_at.isoformat(),
            "completedAt": completed_at.isoformat(),
            "executionTimeMs": execution_time_ms,
            "executionOrder": execution_order,
            "retries": retries_used,
            "maxRetries": max_retries,
            "inputItemCount": len(job.input_data),
            "outputItemCount": output_item_count,
            "activeOutputs": active_outputs,
            "inputDataSizeBytes": input_size,
            "outputDataSizeBytes": output_size,
            "status": "success",
        }
        if error_routed:
            node_metrics["errorRouted"] = True
            node_metrics["error"] = error_msg
        # Merge node-provided metadata (tokens, HTTP status, branch info, etc.)
        if result.metadata:
            node_metrics.update(result.metadata)
        context.node_metrics[job.node_name] = node_metrics

        # Persist node output to DB (non-blocking, non-critical)
        await self._persist_node_output(
            execution_id=context.execution_id,
            node_name=job.node_name,
            output=main_output,
            metrics=node_metrics,
            status="success",
            run_index=job.run_index,
        )

        # Queue next nodes based on outputs
        self._queue_next_nodes(context, node_def, result, queue, node_map, job.run_index)
        return False

    async def _execute_planned(
        self,
        plan: Any,
        node: Any,
        context: ExecutionContext,
        node_def: NodeDefinition,
        job: ExecutionJob,
    ) -> NodeExecutionResult:
        """One attempt of a mocked node (test mode); called inside the retry loop like ``node.execute``."""
        if plan.kind == "raise":
            raise plan.error
        if plan.kind == "items":
            return NodeExecutionResult(outputs={port: list(items) for port, items in plan.outputs.items()})
        # http: the real HttpRequest code runs against a client that records requests and answers from the mock
        client = http_mock_client(plan, node_def.name, job.input_data, context)
        node_context = copy.copy(context)
        node_context.http_client = client
        try:
            return await node.execute(node_context, node_def, job.input_data)
        finally:
            await client.aclose()

    async def _fail_test_setup(
        self,
        context: ExecutionContext,
        node_def: NodeDefinition,
        job: ExecutionJob,
        queue: list[ExecutionJob],
        node_map: dict[str, NodeDefinition],
        error: Exception,
        on_event: ExecutionEventCallback | None,
    ) -> bool:
        """A test-setup failure (unmocked outside step, unusable mock): bypasses retries and on_error and stops the run."""
        code = getattr(error, "code", None)
        details = getattr(error, "details", None)
        context.errors.append(
            ExecutionError(
                node_name=job.node_name, error=str(error), timestamp=datetime.now(), error_code=code, details=details,
            )
        )
        context.execution_order += 1
        metrics: dict[str, Any] = {
            "executionOrder": context.execution_order,
            "inputItemCount": len(job.input_data),
            "status": "error",
            "errorCode": code,
            "errorDetails": details or {},
        }
        context.node_metrics[job.node_name] = metrics
        self._emit_event(
            on_event,
            ExecutionEvent(
                type=ExecutionEventType.NODE_ERROR,
                execution_id=context.execution_id,
                timestamp=datetime.now(),
                node_name=job.node_name,
                node_type=node_def.type,
                error=str(error),
                metrics=metrics,
            ),
        )
        await self._persist_node_output(
            execution_id=context.execution_id, node_name=job.node_name, output=None, metrics=metrics,
            status="error", run_index=job.run_index, error=str(error),
        )
        queue.clear()
        self._propagate_no_output(context, node_def, queue, node_map, job.run_index)
        return True

    async def _process_disabled_job(
        self,
        context: ExecutionContext,
        node_def: NodeDefinition,
        job: ExecutionJob,
        queue: list[ExecutionJob],
        node_map: dict[str, NodeDefinition],
    ) -> bool:
        """A disabled node emits its input items unchanged on "main" (it still reports normal node events)."""
        items = job.input_data
        # A disabled Merge passes through everything it has received
        pending = context.pending_inputs.get(f"{node_def.name}:{job.run_index}")
        if pending is not None:
            items = [
                item for data in pending.values() if data is not NO_OUTPUT_SIGNAL and isinstance(data, list)
                for item in data
            ]
            del context.pending_inputs[f"{node_def.name}:{job.run_index}"]

        context.execution_order += 1
        now = datetime.now()
        metrics: dict[str, Any] = {
            "startedAt": now.isoformat(),
            "completedAt": now.isoformat(),
            "executionTimeMs": 0,
            "executionOrder": context.execution_order,
            "retries": 0,
            "maxRetries": 0,
            "inputItemCount": len(job.input_data),
            "outputItemCount": len(items),
            "activeOutputs": ["main"] if items else [],
            "status": "success",
            "disabled": True,
        }
        context.node_metrics[job.node_name] = metrics
        context.node_run_counts[job.node_name] = context.node_run_counts.get(job.node_name, 0) + 1
        if items:
            context.node_states[job.node_name] = items
            context.last_completed_node = job.node_name
        await self._persist_node_output(
            execution_id=context.execution_id,
            node_name=job.node_name,
            output=items,
            metrics=metrics,
            status="success",
            run_index=job.run_index,
        )
        self._queue_next_nodes(
            context, node_def, NodeExecutionResult(outputs={"main": items}), queue, node_map, job.run_index
        )
        return False

    def _handle_multi_input_node(
        self,
        context: ExecutionContext,
        node_def: NodeDefinition,
        input_data: list[NodeData],
        source_node: str | None,
        source_output: str,
        queue: list[ExecutionJob],
        run_index: int,
    ) -> bool:
        """
        Handle nodes that expect multiple inputs (like Merge).

        Returns True if ready to execute, False if still waiting.
        """
        node_key = f"{node_def.name}:{run_index}"

        if node_key not in context.pending_inputs:
            context.pending_inputs[node_key] = {}

        pending = context.pending_inputs[node_key]
        input_key = f"{source_node}:{source_output}" if source_node else "initial"

        pending[input_key] = input_data

        # Get unique connection keys
        expected_connections = [
            f"{c.source_node}:{c.source_output}"
            for c in context.workflow.connections
            if c.target_node == node_def.name
        ]

        unique_expected_inputs = len(set(expected_connections))

        # Check if we have all inputs (including NO_OUTPUT signals)
        return len(pending) >= unique_expected_inputs

    def _queue_next_nodes(
        self,
        context: ExecutionContext,
        node_def: NodeDefinition,
        result: NodeExecutionResult,
        queue: list[ExecutionJob],
        node_map: dict[str, NodeDefinition],
        run_index: int,
    ) -> None:
        """Queue next nodes based on node outputs."""
        if context.test_options is not None:
            # what each port emitted, for the test assertions (a node listed here ran without failing)
            emitted = context.test_outputs.setdefault(node_def.name, {})
            for port, items in result.outputs.items():
                if items:
                    emitted.setdefault(port, []).extend(items)
        if context.stop_at is not None and node_def.name == context.stop_at:
            return  # partial run: nothing runs after the stop node
        for output_name, output_data in result.outputs.items():
            connections = [
                c
                for c in context.workflow.connections
                if c.source_node == node_def.name
                and c.source_output == output_name
            ]

            for conn in connections:
                target_def = node_map.get(conn.target_node)
                if not target_def:
                    continue

                # Determine if this is a loop (going back to earlier node)
                is_loop = output_name == "loop"
                next_run_index = run_index + 1 if is_loop else run_index

                if output_data is None:
                    # NO_OUTPUT signal - only propagate to multi-input nodes (Merge)
                    target_node = self._registry.get(target_def.type)
                    target_input_count = getattr(target_node, "input_count", 1)
                    if target_input_count > 1 or target_input_count == float("inf"):
                        # Send signal to multi-input node so it knows this branch is dead
                        node_key = f"{conn.target_node}:{next_run_index}"
                        if node_key not in context.pending_inputs:
                            context.pending_inputs[node_key] = {}
                        context.pending_inputs[node_key][f"{node_def.name}:{output_name}"] = []
                    # Don't queue execution for single-input nodes when output is null
                elif output_data:
                    queue.append(
                        ExecutionJob(
                            node_name=conn.target_node,
                            input_data=output_data,
                            source_node=node_def.name,
                            source_output=output_name,
                            run_index=next_run_index,
                        )
                    )

    def _propagate_no_output(
        self,
        context: ExecutionContext,
        node_def: NodeDefinition,
        queue: list[ExecutionJob],
        node_map: dict[str, NodeDefinition],
        run_index: int,
    ) -> None:
        """Propagate NO_OUTPUT signal to all downstream nodes."""
        connections = [c for c in context.workflow.connections if c.source_node == node_def.name]

        for conn in connections:
            target_def = node_map.get(conn.target_node)
            if not target_def:
                continue

            target_node = self._registry.get(target_def.type)
            target_input_count = getattr(target_node, "input_count", 1)

            # If target is multi-input, send NO_OUTPUT signal
            if target_input_count > 1 or target_input_count == float("inf"):
                node_key = f"{conn.target_node}:{run_index}"
                if node_key not in context.pending_inputs:
                    context.pending_inputs[node_key] = {}
                context.pending_inputs[node_key][f"{node_def.name}:{conn.source_output}"] = NO_OUTPUT_SIGNAL

    def _resolve_node_parameters(
        self,
        context: ExecutionContext,
        node_def: NodeDefinition,
        input_data: list[NodeData],
    ) -> NodeDefinition:
        """Resolve expressions in node parameters.

        Note: $json expressions are left unresolved (skip_json=True) so nodes
        can evaluate them per-item. Only $node references are pre-resolved.
        """
        expr_context = ExpressionEngine.create_context(
            input_data,
            context.node_states,
            context.execution_id,
            0,
            variables=context.variables,
        )

        # Skip $json expressions - let nodes resolve them per-item
        resolved_params = expression_engine.resolve(node_def.parameters, expr_context, skip_json=True)

        return NodeDefinition(
            name=node_def.name,
            type=node_def.type,
            parameters=resolved_params,
            position=node_def.position,
            pinned_data=node_def.pinned_data,
            retry_on_fail=node_def.retry_on_fail,
            retry_delay=node_def.retry_delay,
            continue_on_fail=node_def.continue_on_fail,
            disabled=node_def.disabled,
            on_error=node_def.on_error,
            contract=node_def.contract,
            label=node_def.label,
        )

    def _create_context(
        self, workflow: Workflow, mode: Literal["manual", "webhook", "cron"]
    ) -> ExecutionContext:
        """Create fresh execution context."""
        return ExecutionContext(
            workflow=workflow,
            execution_id=self._generate_id(),
            start_time=datetime.now(),
            mode=mode,
        )

    def find_start_node(self, workflow: Workflow) -> NodeDefinition | None:
        """Find start node in workflow."""
        # Priority: Webhook > Cron > ExecuteWorkflowTrigger > Start > first node
        for node_type in ["Webhook", "Cron", "ExecuteWorkflowTrigger", "Start"]:
            node = next((n for n in workflow.nodes if n.type == node_type), None)
            if node:
                return node
        return workflow.nodes[0] if workflow.nodes else None

    def _generate_id(self) -> str:
        """Generate unique execution ID."""
        from ..utils.ids import execution_id
        return execution_id()


    async def _resolve_credential(self, credential_id: str, team_id: str = "default") -> dict[str, Any] | None:
        """Load and decrypt a credential by ID — only if it belongs to the run's team."""
        if not self._db_session:
            return None
        from ..services.credential_service import decrypt
        from ..repositories.credential_repository import CredentialRepository
        async with self._db_session() as session:
            repo = CredentialRepository(session)
            cred = await repo.get(credential_id)
            if not cred or (cred.team_id or "default") != (team_id or "default"):
                return None
            return decrypt(cred.data)


"""
Workflow runner - executes DAG-based workflows.

Uses a queue-based BFS approach for node execution.
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from datetime import datetime
from time import perf_counter
from typing import Any, TYPE_CHECKING, Literal

import httpx

logger = logging.getLogger(__name__)

from .expression_engine import ExpressionEngine, expression_engine
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
    ResolvedSubnode,
    SubnodeContext,
    SubnodeSlotDefinition,
    Workflow,
    WorkflowStopSignal,
    NO_OUTPUT_SIGNAL,
)

if TYPE_CHECKING:
    from .node_registry import NodeRegistryClass


class WorkflowRunner:
    """Executes DAG-based workflows using queue-based processing."""

    def __init__(self) -> None:
        from .node_registry import node_registry
        self._registry: NodeRegistryClass = node_registry

    async def run(
        self,
        workflow: Workflow,
        start_node_name: str,
        initial_data: list[NodeData] | None = None,
        mode: Literal["manual", "webhook", "cron"] = "manual",
        on_event: ExecutionEventCallback | None = None,
        workflow_repository: Any | None = None,
        execution_id: str | None = None,
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

        # Shared HTTP client for all nodes
        async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as http_client:
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
    ) -> ExecutionContext:
        """
        Run a workflow with a pre-configured execution context.

        Used by run_subworkflow to execute with inherited context.
        """
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
        """Helper to emit events safely."""
        if on_event:
            try:
                on_event(event)
            except Exception:
                logger.exception("Error in execution event callback")

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

        # Skip execution for subnodes - they only provide configuration
        if self._is_subnode(node_def.type):
            logger.debug(f"Skipping subnode '{job.node_name}' - subnodes don't execute directly")
            return False

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

        # Check for pinned data
        if node_def.pinned_data:
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

        # Resolve subnodes for this node (if it has subnode slots)
        subnode_context = self._resolve_subnodes(context, node_def, node_map)

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
                # Pass subnode_context to nodes that support it
                if subnode_context:
                    result = await node.execute(
                        context, resolved_node_def, job.input_data,
                        subnode_context=subnode_context
                    )
                else:
                    result = await node.execute(context, resolved_node_def, job.input_data)
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
                    await asyncio.sleep(retry_delay / 1000)
                    continue

        completed_at = datetime.now()
        execution_time_ms = round((perf_counter() - node_start) * 1000, 2)

        # Handle final error after all retries exhausted
        if last_error or not result:
            error_msg = str(last_error) if last_error else "Unknown execution error"
            retry_info = f" (after {max_retries + 1} attempts)" if max_retries > 0 else ""
            context.errors.append(
                ExecutionError(
                    node_name=job.node_name,
                    error=f"{error_msg}{retry_info}",
                    timestamp=datetime.now(),
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

            # Check continueOnFail
            if node_def.continue_on_fail:
                result = NodeExecutionResult(
                    outputs={"main": [NodeData(json={"error": error_msg, "_errorNode": job.node_name})]}
                )
            else:
                # Stop execution: clear the queue so no downstream nodes run
                queue.clear()
                # Propagate NO_OUTPUT to any multi-input nodes waiting on this branch
                self._propagate_no_output(context, node_def, queue, node_map, job.run_index)
                return True

        # Update run count for loop support
        current_count = context.node_run_counts.get(job.node_name, 0)
        context.node_run_counts[job.node_name] = current_count + 1

        # Store node output (main output for state)
        main_output = result.outputs.get("main") or next(iter(result.outputs.values()), None)
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
        # Merge node-provided metadata (tokens, HTTP status, branch info, etc.)
        if result.metadata:
            node_metrics.update(result.metadata)
        context.node_metrics[job.node_name] = node_metrics

        # Queue next nodes based on outputs
        self._queue_next_nodes(context, node_def, result, queue, node_map, job.run_index)
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

        # Get unique connection keys (only normal connections, not subnodes)
        expected_connections = [
            f"{c.source_node}:{c.source_output}"
            for c in context.workflow.connections
            if c.target_node == node_def.name and c.connection_type == "normal"
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
        for output_name, output_data in result.outputs.items():
            # Find normal connections from this output (not subnode connections)
            connections = [
                c
                for c in context.workflow.connections
                if c.source_node == node_def.name
                and c.source_output == output_name
                and c.connection_type == "normal"
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
        # Filter out subnodes first
        main_nodes = [n for n in workflow.nodes if not self._is_subnode(n.type)]

        # Priority: Webhook > Cron > ExecuteWorkflowTrigger > Start > first node
        for node_type in ["Webhook", "Cron", "ExecuteWorkflowTrigger", "Start"]:
            node = next((n for n in main_nodes if n.type == node_type), None)
            if node:
                return node
        return main_nodes[0] if main_nodes else None

    def _generate_id(self) -> str:
        """Generate unique execution ID."""
        return f"exec_{int(datetime.now().timestamp() * 1000)}_{uuid.uuid4().hex[:7]}"

    def _resolve_subnodes(
        self,
        context: ExecutionContext,
        node_def: NodeDefinition,
        node_map: dict[str, NodeDefinition],
    ) -> SubnodeContext | None:
        """
        Resolve all subnodes connected to a parent node.

        Returns SubnodeContext with resolved model, memory, and tool configs.
        Validates connections and logs warnings for invalid configurations.
        """
        node_instance = self._registry.get(node_def.type)
        desc = node_instance.node_description

        # Only resolve for nodes with subnode slots
        if not desc or not desc.subnode_slots:
            return None

        # Find subnode connections (connection_type == "subnode")
        subnode_connections = [
            c for c in context.workflow.connections
            if c.target_node == node_def.name and c.connection_type == "subnode"
        ]

        if not subnode_connections:
            return None

        models: list[ResolvedSubnode] = []
        memory: list[ResolvedSubnode] = []
        tools: list[ResolvedSubnode] = []

        # Track connections per slot for multiplicity validation
        slot_connection_counts: dict[str, int] = {}

        for conn in subnode_connections:
            subnode_def = node_map.get(conn.source_node)
            if not subnode_def:
                logger.warning(f"Subnode '{conn.source_node}' not found")
                continue

            subnode_instance = self._registry.get(subnode_def.type)
            subnode_desc = subnode_instance.node_description

            if not subnode_desc or not subnode_desc.is_subnode:
                logger.warning(f"Node '{conn.source_node}' is not a subnode")
                continue

            # Get the target slot
            target_slot_name = conn.slot_name or conn.target_input
            slot = self._get_slot_for_connection(desc.subnode_slots, target_slot_name)

            if not slot:
                logger.warning(
                    f"Slot '{target_slot_name}' not found on node '{node_def.name}'"
                )
                continue

            # Validate subnode connection
            subnode_slot_type = subnode_desc.subnode_type or "unknown"
            validation_errors = self._validate_subnode_connection(
                slot=slot,
                subnode_type=subnode_def.type,
                subnode_slot_type=subnode_slot_type,
                subnode_name=subnode_def.name,
            )

            if validation_errors:
                for error in validation_errors:
                    logger.warning(f"Subnode validation: {error}")
                continue

            # Check multiplicity constraint
            slot_connection_counts[target_slot_name] = slot_connection_counts.get(target_slot_name, 0) + 1
            if not slot.multiple and slot_connection_counts[target_slot_name] > 1:
                logger.warning(
                    f"Slot '{slot.name}' on node '{node_def.name}' does not accept multiple "
                    f"subnodes, but {slot_connection_counts[target_slot_name]} are connected. "
                    f"Only the first will be used."
                )
                continue

            # Resolve expressions in subnode parameters before get_config
            try:
                expr_context = ExpressionEngine.create_context(
                    [],  # subnodes don't have input data
                    context.node_states,
                    context.execution_id,
                    0,
                )
                resolved_params = expression_engine.resolve(
                    subnode_def.parameters, expr_context, skip_json=False
                )
                resolved_subnode_def = NodeDefinition(
                    name=subnode_def.name,
                    type=subnode_def.type,
                    parameters=resolved_params,
                    position=subnode_def.position,
                    retry_on_fail=subnode_def.retry_on_fail,
                    retry_delay=subnode_def.retry_delay,
                    continue_on_fail=subnode_def.continue_on_fail,
                )
            except Exception as e:
                logger.warning(f"Error resolving subnode '{conn.source_node}' parameters: {e}")
                resolved_subnode_def = subnode_def

            # Get config from subnode
            try:
                config = subnode_instance.get_config(resolved_subnode_def)
            except Exception as e:
                logger.error(f"Error getting config from subnode '{conn.source_node}': {e}")
                continue

            resolved = ResolvedSubnode(
                node_name=subnode_def.name,
                node_type=subnode_def.type,
                slot_name=target_slot_name,
                slot_type=subnode_slot_type,
                config=config,
            )

            # Categorize by slot type
            if resolved.slot_type == "model":
                models.append(resolved)
            elif resolved.slot_type == "memory":
                memory.append(resolved)
            elif resolved.slot_type == "tool":
                tools.append(resolved)

        return SubnodeContext(models=models, memory=memory, tools=tools)

    def _is_subnode(self, node_type: str) -> bool:
        """Check if a node type is a subnode."""
        try:
            node_instance = self._registry.get(node_type)
            desc = node_instance.node_description
            return desc.is_subnode if desc else False
        except ValueError:
            return False

    def _validate_subnode_connection(
        self,
        slot: SubnodeSlotDefinition,
        subnode_type: str,
        subnode_slot_type: str,
        subnode_name: str,
    ) -> list[str]:
        """
        Validate a subnode connection against its target slot.

        Returns a list of validation errors (empty if valid).
        """
        errors: list[str] = []

        # Check slot type matches
        if slot.slot_type != subnode_slot_type:
            errors.append(
                f"Subnode '{subnode_name}' has type '{subnode_slot_type}' but slot "
                f"'{slot.name}' expects type '{slot.slot_type}'"
            )

        # Check accepted node types if specified
        if slot.accepted_node_types and subnode_type not in slot.accepted_node_types:
            errors.append(
                f"Subnode type '{subnode_type}' is not accepted by slot '{slot.name}'. "
                f"Accepted types: {slot.accepted_node_types}"
            )

        return errors

    def _get_slot_for_connection(
        self,
        slots: list[SubnodeSlotDefinition],
        slot_name: str,
    ) -> SubnodeSlotDefinition | None:
        """Find a slot definition by name."""
        return next((s for s in slots if s.name == slot_name), None)

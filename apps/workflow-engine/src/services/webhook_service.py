"""Webhook service for handling webhook triggers."""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Any

from ..core.exceptions import (
    WorkflowNotFoundError,
    WorkflowInactiveError,
    WebhookError,
)
from ..engine.types import NodeData, WebhookResponse

if TYPE_CHECKING:
    from ..engine.types import StoredWorkflow, NodeDefinition
    from ..repositories import WorkflowRepository, ExecutionRepository

logger = logging.getLogger(__name__)


class WebhookService:
    """Service for webhook operations."""

    def __init__(
        self,
        workflow_repo: WorkflowRepository,
        execution_repo: ExecutionRepository,
    ) -> None:
        self._workflow_repo = workflow_repo
        self._execution_repo = execution_repo

    async def handle_webhook(
        self,
        workflow_id: str,
        method: str,
        body: dict[str, Any],
        headers: dict[str, str],
        query_params: dict[str, str],
        raw_body: bytes = b"",
    ) -> dict[str, Any] | WebhookResponse:
        """Handle incoming webhook request by workflow ID."""
        stored = await self._workflow_repo.get(workflow_id)
        if not stored:
            raise WorkflowNotFoundError(workflow_id)

        return await self._execute_webhook(stored, method, body, headers, query_params, raw_body)

    async def handle_webhook_by_path(
        self,
        path: str,
        method: str,
        body: dict[str, Any],
        headers: dict[str, str],
        query_params: dict[str, str],
        raw_body: bytes = b"",
    ) -> dict[str, Any] | WebhookResponse:
        """Handle incoming webhook request by custom path.
        Uses active_triggers table if available, falls back to scanning workflows."""
        from ..repositories.trigger_repository import TriggerRepository

        trigger_repo = TriggerRepository(self._workflow_repo._session)
        trigger = await trigger_repo.find_webhook_trigger(path, method)

        if trigger:
            stored = await self._workflow_repo.get(trigger.workflow_id)
            if not stored:
                raise WorkflowNotFoundError(f"webhook path '{path}'")
            return await self._execute_webhook(stored, method, body, headers, query_params, raw_body)

        # Fallback: scan workflows directly (for backward compat / SQLite dev mode)
        stored = await self._workflow_repo.find_by_webhook_path(path)
        if not stored:
            raise WorkflowNotFoundError(f"webhook path '{path}'")

        return await self._execute_webhook(stored, method, body, headers, query_params, raw_body)

    async def _execute_webhook(
        self,
        stored: StoredWorkflow,
        method: str,
        body: dict[str, Any],
        headers: dict[str, str],
        query_params: dict[str, str],
        raw_body: bytes = b"",
    ) -> dict[str, Any] | WebhookResponse:
        """Core webhook execution logic."""
        if not stored.active:
            raise WorkflowInactiveError(stored.id)

        # Find webhook node
        webhook_node = next(
            (n for n in stored.workflow.nodes if n.type == "Webhook"),
            None,
        )
        if not webhook_node:
            raise WebhookError("Workflow has no Webhook trigger", stored.id)

        # Check method is allowed (fix: no POST bypass)
        allowed_method = webhook_node.parameters.get("method", "POST")
        if allowed_method != method:
            raise WebhookError(
                f"Method {method} not allowed for this webhook (expected {allowed_method})",
                stored.id,
            )

        # Build webhook data (includes raw body for signature verification etc.)
        webhook_data = NodeData(
            json={
                "body": body,
                "headers": headers,
                "query": query_params,
                "method": method,
                "triggeredAt": datetime.now().isoformat(),
                "rawBody": raw_body.decode("utf-8", errors="replace") if raw_body else "",
            }
        )

        # Check response mode before executing
        response_mode = webhook_node.parameters.get("responseMode", "onReceived")

        if response_mode == "onReceived":
            return await self._handle_on_received(stored, webhook_node, webhook_data)
        else:
            return await self._handle_last_node(stored, webhook_node, webhook_data)

    async def _handle_on_received(
        self,
        stored: StoredWorkflow,
        webhook_node: NodeDefinition,
        webhook_data: NodeData,
    ) -> dict[str, Any]:
        """Respond immediately and execute workflow in the background."""
        from ..utils.ids import execution_id
        exec_id = execution_id()

        # Fire-and-forget background execution with registry tracking
        from ..engine import execution_registry
        task = asyncio.create_task(
            self._run_background(stored, webhook_node, webhook_data, exec_id)
        )
        execution_registry.register(exec_id, task)

        return {
            "status": "success",
            "executionId": exec_id,
            "message": "Workflow triggered",
        }

    async def _run_background(
        self,
        stored: StoredWorkflow,
        webhook_node: NodeDefinition,
        webhook_data: NodeData,
        execution_id: str,
    ) -> None:
        """Run workflow in background and save execution with its own DB session."""
        from ..engine.workflow_runner import WorkflowRunner
        from ..engine.types import ExecutionContext, ExecutionError
        from ..db.session import async_session_factory  # noqa: F811
        from ..repositories.execution_repository import ExecutionRepository

        try:
            # Pre-create execution row so node_outputs FK is satisfied during run
            async with async_session_factory() as session:
                exec_repo = ExecutionRepository(session)
                await exec_repo.start(execution_id, stored.id, stored.name, "webhook")

            runner = WorkflowRunner(db_session_factory=async_session_factory)
            context = await runner.run(
                stored.workflow,
                webhook_node.name,
                [webhook_data],
                "webhook",
                workflow_repository=self._workflow_repo,
                execution_id=execution_id,
            )

            # Use a fresh DB session for background persistence
            async with async_session_factory() as session:
                exec_repo = ExecutionRepository(session)
                await exec_repo.complete(context, stored.id, stored.name)
        except Exception as e:
            logger.exception(
                "Background webhook execution failed for workflow %s", stored.id
            )
            # Save a failed execution record so users can see the error
            try:
                async with async_session_factory() as session:
                    exec_repo = ExecutionRepository(session)
                    fail_context = ExecutionContext(
                        workflow=stored.workflow,
                        execution_id=execution_id,
                        start_time=datetime.now(),
                        mode="webhook",
                    )
                    fail_context.errors.append(
                        ExecutionError(
                            node_name=webhook_node.name,
                            error=str(e),
                            timestamp=datetime.now(),
                        )
                    )
                    await exec_repo.complete(fail_context, stored.id, stored.name)
            except Exception:
                logger.exception(
                    "Failed to save error execution for workflow %s", stored.id
                )

    async def _handle_last_node(
        self,
        stored: StoredWorkflow,
        webhook_node: NodeDefinition,
        webhook_data: NodeData,
    ) -> dict[str, Any] | WebhookResponse:
        """Execute workflow synchronously and return last node's output."""
        from ..engine.workflow_runner import WorkflowRunner
        from ..db.session import async_session_factory
        from ..utils.ids import execution_id as gen_exec_id

        exec_id = gen_exec_id()
        await self._execution_repo.start(exec_id, stored.id, stored.name, "webhook")

        runner = WorkflowRunner(db_session_factory=async_session_factory)
        context = await runner.run(
            stored.workflow,
            webhook_node.name,
            [webhook_data],
            "webhook",
            workflow_repository=self._workflow_repo,
            execution_id=exec_id,
        )

        await self._execution_repo.complete(context, stored.id, stored.name)

        # Custom response from RespondToWebhook node takes priority
        if context.webhook_response:
            return context.webhook_response

        # Use tracked last_completed_node instead of dict ordering
        if context.last_completed_node and context.last_completed_node in context.node_states:
            last_node_data = context.node_states[context.last_completed_node]
            return {
                "status": "success" if not context.errors else "failed",
                "executionId": context.execution_id,
                "data": [d.json for d in last_node_data],
            }

        return {
            "status": "success" if not context.errors else "failed",
            "executionId": context.execution_id,
            "data": [],
        }

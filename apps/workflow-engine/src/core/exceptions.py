"""Custom exceptions for workflow engine."""

from typing import Any


class WorkflowEngineError(Exception):
    """Base exception for all workflow engine errors."""

    def __init__(self, message: str, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.details = details or {}


class WorkflowNotFoundError(WorkflowEngineError):
    """Raised when a workflow is not found."""

    def __init__(self, workflow_id: str) -> None:
        super().__init__(
            message=f"Workflow not found: {workflow_id}",
            details={"workflow_id": workflow_id},
        )
        self.workflow_id = workflow_id


class ExecutionNotFoundError(WorkflowEngineError):
    """Raised when an execution record is not found."""

    def __init__(self, execution_id: str) -> None:
        super().__init__(
            message=f"Execution not found: {execution_id}",
            details={"execution_id": execution_id},
        )
        self.execution_id = execution_id


class NodeNotFoundError(WorkflowEngineError):
    """Raised when a node type is not found."""

    def __init__(self, node_type: str) -> None:
        super().__init__(
            message=f"Node type not found: {node_type}",
            details={"node_type": node_type},
        )
        self.node_type = node_type


class ValidationError(WorkflowEngineError):
    """Raised when validation fails."""

    def __init__(self, message: str, field: str | None = None) -> None:
        super().__init__(
            message=message,
            details={"field": field} if field else {},
        )
        self.field = field


class WorkflowExecutionError(WorkflowEngineError):
    """Raised when workflow execution fails."""

    def __init__(
        self,
        message: str,
        workflow_id: str | None = None,
        node_name: str | None = None,
    ) -> None:
        super().__init__(
            message=message,
            details={
                "workflow_id": workflow_id,
                "node_name": node_name,
            },
        )
        self.workflow_id = workflow_id
        self.node_name = node_name


class WebhookError(WorkflowEngineError):
    """Raised when webhook handling fails."""

    def __init__(self, message: str, workflow_id: str) -> None:
        super().__init__(
            message=message,
            details={"workflow_id": workflow_id},
        )
        self.workflow_id = workflow_id


class WorkflowInactiveError(WorkflowEngineError):
    """Raised when trying to trigger an inactive workflow."""

    def __init__(self, workflow_id: str) -> None:
        super().__init__(
            message=f"Workflow is not active: {workflow_id}",
            details={"workflow_id": workflow_id},
        )
        self.workflow_id = workflow_id

"""Core module for workflow engine - config, exceptions, and dependencies."""

from .config import settings, Settings
from .exceptions import (
    WorkflowEngineError,
    WorkflowNotFoundError,
    ExecutionNotFoundError,
    NodeNotFoundError,
    ValidationError,
    WorkflowExecutionError,
)
from .dependencies import (
    get_workflow_repository,
    get_execution_repository,
    get_node_registry,
    get_workflow_service,
    get_execution_service,
    get_node_service,
)

__all__ = [
    # Config
    "settings",
    "Settings",
    # Exceptions
    "WorkflowEngineError",
    "WorkflowNotFoundError",
    "ExecutionNotFoundError",
    "NodeNotFoundError",
    "ValidationError",
    "WorkflowExecutionError",
    # Dependencies
    "get_workflow_repository",
    "get_execution_repository",
    "get_node_registry",
    "get_workflow_service",
    "get_execution_service",
    "get_node_service",
]

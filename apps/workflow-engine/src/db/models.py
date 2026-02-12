"""SQLModel database models."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlmodel import Column, Field, SQLModel
from sqlalchemy import JSON


class WorkflowModel(SQLModel, table=True):
    """Workflow database model."""

    __tablename__ = "workflows"

    id: str = Field(primary_key=True)
    name: str = Field(index=True)
    description: str | None = Field(default=None)
    active: bool = Field(default=False, index=True)

    # Store the full workflow definition as JSON
    # This includes: nodes, connections, settings
    definition: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))

    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)


class ExecutionModel(SQLModel, table=True):
    """Execution history database model."""

    __tablename__ = "executions"

    id: str = Field(primary_key=True)
    workflow_id: str = Field(index=True)
    workflow_name: str

    status: str = Field(index=True)  # running, success, failed
    mode: str  # manual, webhook, cron

    # Store node execution data as JSON
    node_data: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))

    # Store errors as JSON array
    errors: list[dict[str, Any]] = Field(default_factory=list, sa_column=Column(JSON))

    # Per-node execution metrics
    node_metrics: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))

    start_time: datetime = Field(default_factory=datetime.now, index=True)
    end_time: datetime | None = Field(default=None)

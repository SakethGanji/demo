"""ProjectPlan versions (migration 20261026120000). One row per (project, version); at most one ``approved`` per project."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import Column as SAColumn, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, SQLModel

PLAN_STATUSES = ("draft", "approved", "superseded")


class ProjectPlanModel(SQLModel, table=True):
    __tablename__ = "project_plans"

    id: str = Field(primary_key=True)  # plan_<uuid>
    project_id: str = Field(foreign_key="projects.id", index=True)
    version: int
    status: str = Field(default="draft")
    base_version: int | None = Field(default=None)
    body: dict[str, Any] = Field(default_factory=dict, sa_column=SAColumn(JSONB, nullable=False, server_default=text("'{}'::jsonb")))
    questions: list[dict[str, Any]] = Field(default_factory=list,
                                            sa_column=SAColumn(JSONB, nullable=False, server_default=text("'[]'::jsonb")))
    round: int = Field(default=0)
    created_by: str | None = Field(default=None)
    approved_by: str | None = Field(default=None)
    approved_at: datetime | None = Field(default=None)
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)

"""Build runs (migration 20261027120000). One row per build of an approved plan; at most one active per project."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import Column as SAColumn, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, SQLModel

BUILD_STATUSES = ("queued", "running", "waiting_for_input", "done", "partial", "failed", "cancelled")
ACTIVE_STATUSES = ("queued", "running", "waiting_for_input")


class ProjectBuildModel(SQLModel, table=True):
    __tablename__ = "project_builds"

    id: str = Field(primary_key=True)  # build_<uuid>
    project_id: str = Field(foreign_key="projects.id", index=True)
    plan_version: int
    app_key: str | None = Field(default=None)
    rebuild: bool = Field(default=False)
    status: str = Field(default="queued")
    reason: str | None = Field(default=None)
    steps: list[dict[str, Any]] = Field(default_factory=list,
                                        sa_column=SAColumn(JSONB, nullable=False, server_default=text("'[]'::jsonb")))
    report: dict[str, Any] | None = Field(default=None, sa_column=SAColumn(JSONB, nullable=True))
    created_by: str | None = Field(default=None)
    created_at: datetime = Field(default_factory=datetime.now)
    started_at: datetime | None = Field(default=None)
    finished_at: datetime | None = Field(default=None)
    updated_at: datetime = Field(default_factory=datetime.now)

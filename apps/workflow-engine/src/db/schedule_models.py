"""Scheduled functions of code modules (@schedule)."""

from __future__ import annotations

from datetime import datetime

from sqlmodel import Field, SQLModel


class ModuleScheduleModel(SQLModel, table=True):
    __tablename__ = "module_schedules"

    id: str = Field(primary_key=True)  # sch_<12 hex>
    project_id: str = Field(foreign_key="projects.id", index=True)
    module_id: str = Field(foreign_key="code_modules.id")
    function: str
    cron: str | None = Field(default=None)
    every_seconds: int | None = Field(default=None)
    enabled: bool = Field(default=True)
    next_run_at: datetime | None = Field(default=None)
    running_since: datetime | None = Field(default=None)
    last_run_at: datetime | None = Field(default=None)
    last_status: str | None = Field(default=None)
    last_error: str | None = Field(default=None)
    duration_ms: int | None = Field(default=None)
    last_execution_id: str | None = Field(default=None)
    line: int | None = Field(default=None)
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)

"""Project file changes (migration 20261028120000): the project's commit log over its one file tree. Diff stats only."""

from __future__ import annotations

from datetime import datetime

from sqlmodel import Field, SQLModel

CHANGE_OPS = ("create", "update", "delete")
CHANGE_ACTORS = ("backend_builder", "ui_builder", "user", "plan")


class ProjectFileChangeModel(SQLModel, table=True):
    __tablename__ = "project_file_changes"

    id: str = Field(primary_key=True)  # chg_<uuid>
    project_id: str = Field(foreign_key="projects.id", index=True)
    path: str
    op: str
    actor: str = Field(default="user")
    build_id: str | None = Field(default=None)
    step_key: str | None = Field(default=None)
    summary: str | None = Field(default=None)
    lines_added: int = Field(default=0)
    lines_removed: int = Field(default=0)
    created_at: datetime = Field(default_factory=datetime.now)

"""Notebooks, their cells, runs, per-cell results and applied writes (migration 20261031120000).

Like the other add-on model modules (connection_models, plan_models, ...), this is imported where it is used; the
notebook routes import it at module load, so the tables are registered on SQLModel.metadata when the app starts.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import Column as SAColumn
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, SQLModel


class NotebookModel(SQLModel, table=True):
    __tablename__ = "notebooks"

    id: str = Field(primary_key=True)  # nb_<hex>
    project_id: str = Field(foreign_key="projects.id", index=True)
    team_id: str | None = Field(default=None)
    title: str
    description: str | None = Field(default=None)
    status: str = Field(default="idle")  # idle | running | needs_you | failed | done
    environment: str | None = Field(default=None)
    template_slug: str | None = Field(default=None)
    run_as: str | None = Field(default=None)
    created_by: str | None = Field(default=None)
    active_run_id: str | None = Field(default=None)  # NULL = free (the run lock)
    kept: list[dict[str, Any]] = Field(default_factory=list, sa_column=SAColumn(JSONB, nullable=False, server_default="[]"))
    ai_notes: str | None = Field(default=None)  # Ask's running notes (migration 20261102120000), at most 2000 chars
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)


class NotebookCellModel(SQLModel, table=True):
    __tablename__ = "notebook_cells"

    id: str = Field(primary_key=True)  # cell_<hex>
    notebook_id: str = Field(foreign_key="notebooks.id", index=True)
    parent_cell_id: str | None = Field(default=None, foreign_key="notebook_cells.id")
    position: int
    name: str  # c1, c2, ...
    kind: str
    title: str | None = Field(default=None)
    spec: dict[str, Any] = Field(default_factory=dict, sa_column=SAColumn(JSONB, nullable=False, server_default="{}"))
    guard: dict[str, Any] | None = Field(default=None, sa_column=SAColumn(JSONB, nullable=True))
    created_by: str | None = Field(default=None)
    created_via: str = Field(default="user")  # user | agent | template
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)


class NotebookRunModel(SQLModel, table=True):
    __tablename__ = "notebook_runs"

    id: str = Field(primary_key=True)  # nbr_<hex>
    notebook_id: str = Field(foreign_key="notebooks.id", index=True)
    n: int
    from_position: int = Field(default=0)
    status: str = Field(default="running")
    stop: dict[str, Any] | None = Field(default=None, sa_column=SAColumn(JSONB, nullable=True))
    on_applied: str = Field(default="stop")  # stop | skip
    reapply: list[str] = Field(default_factory=list, sa_column=SAColumn(JSONB, nullable=False, server_default="[]"))
    triggered_by: str | None = Field(default=None)
    run_as: str | None = Field(default=None)
    via_agent: str | None = Field(default=None)
    heartbeat_at: datetime | None = Field(default=None)
    started_at: datetime = Field(default_factory=datetime.now)
    finished_at: datetime | None = Field(default=None)
    continue_on_fail: bool = Field(default=False)  # a failing cell does not stop the run (migration 20261102120000)


class NotebookCellResultModel(SQLModel, table=True):
    __tablename__ = "notebook_cell_results"

    id: str = Field(primary_key=True)  # nbres_<hex>
    run_id: str = Field(foreign_key="notebook_runs.id", index=True)
    cell_id: str = Field(foreign_key="notebook_cells.id", index=True)
    row_index: int | None = Field(default=None)
    status: str
    output: dict[str, Any] | None = Field(default=None, sa_column=SAColumn(JSONB, nullable=True))
    output_ref: str | None = Field(default=None)
    error: dict[str, Any] | None = Field(default=None, sa_column=SAColumn(JSONB, nullable=True))
    execution_id: str | None = Field(default=None)
    job_id: str | None = Field(default=None)
    ms: int | None = Field(default=None)
    created_at: datetime = Field(default_factory=datetime.now)


class NotebookWriteModel(SQLModel, table=True):
    __tablename__ = "notebook_writes"

    id: str = Field(primary_key=True)  # nbw_<hex>
    cell_id: str = Field(foreign_key="notebook_cells.id", index=True)
    run_id: str | None = Field(default=None, foreign_key="notebook_runs.id")
    row_index: int | None = Field(default=None)
    idempotency_key: str = Field(unique=True)
    before: dict[str, Any] | None = Field(default=None, sa_column=SAColumn(JSONB, nullable=True))
    after: dict[str, Any] | None = Field(default=None, sa_column=SAColumn(JSONB, nullable=True))
    applied_by: str | None = Field(default=None)
    applied_at: datetime = Field(default_factory=datetime.now)


class NotebookEnvironmentModel(SQLModel, table=True):
    """Named variable sets for a notebook's API tests (migration 20261101130000)."""

    __tablename__ = "notebook_environments"

    notebook_id: str = Field(primary_key=True, foreign_key="notebooks.id")
    active: str | None = Field(default=None)
    sets: list[dict[str, Any]] = Field(default_factory=list, sa_column=SAColumn(JSONB, nullable=False, server_default="[]"))
    updated_at: datetime = Field(default_factory=datetime.now)

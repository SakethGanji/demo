"""Applied data-table schema migrations (migration 20261026130000). The history of additive column changes per table."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import Column as SAColumn
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, SQLModel


class DataTableMigrationModel(SQLModel, table=True):
    __tablename__ = "data_table_migrations"

    id: str = Field(primary_key=True)  # dtm_<12 hex>
    project_id: str = Field(foreign_key="projects.id")
    table_id: str = Field(foreign_key="data_tables.id")
    plan_version: int | None = Field(default=None)
    ops: list[dict[str, Any]] = Field(default_factory=list, sa_column=SAColumn(JSONB, nullable=False))
    op_hash: str
    applied_by: str | None = Field(default=None)
    applied_at: datetime = Field(default_factory=datetime.now)

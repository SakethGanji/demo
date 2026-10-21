"""External database connections of a project (migration 20261029120000). The URL is a project secret, never a column."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import Column as SAColumn
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, SQLModel


class ProjectConnectionModel(SQLModel, table=True):
    __tablename__ = "project_connections"

    id: str = Field(primary_key=True)  # conn_<uuid>
    project_id: str = Field(foreign_key="projects.id", index=True)
    key: str
    name: str
    kind: str = Field(default="postgres")
    host: str
    port: int | None = Field(default=None)
    database: str = Field(default="")
    readonly: bool = Field(default=True)
    secret_key: str
    status: str = Field(default="unknown")
    error: str | None = Field(default=None)
    introspected_at: datetime | None = Field(default=None)
    schema_snapshot: dict[str, Any] | None = Field(default=None, sa_column=SAColumn(JSONB, nullable=True))
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)

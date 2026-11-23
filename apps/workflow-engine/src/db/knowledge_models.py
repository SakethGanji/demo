"""Project knowledge store model (migration 20261024120000). ``tsv`` is a generated column: never written, only searched in SQL."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import Column as SAColumn, Index, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, SQLModel

KINDS = ("requirement", "spec", "decision", "clarify_answer", "table_schema", "route", "screen", "component_use", "failure",
         "acceptance")
STATUSES = ("draft", "approved")
SOURCES = ("design", "code", "human", "failure")


class ProjectKnowledgeModel(SQLModel, table=True):
    __tablename__ = "project_knowledge"
    __table_args__ = (Index("ix_project_knowledge_project", "project_id", "kind"),)

    id: str = Field(primary_key=True)  # pk_<12 hex>
    project_id: str = Field(foreign_key="projects.id")
    kind: str
    key: str
    title: str = Field(default="")
    body: dict[str, Any] = Field(default_factory=dict, sa_column=SAColumn(JSONB, nullable=False, server_default=text("'{}'::jsonb")))
    text: str = Field(default="")
    status: str = Field(default="draft")
    version: int = Field(default=1)
    source: str = Field(default="human")
    source_ref: str | None = Field(default=None)
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)

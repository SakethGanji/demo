"""Template bundle (``wf-template/1``) and install request schemas (Stage 8, DESIGN-STAGE8-REUSE section 8).

The bundle models are strict (``extra="forbid"``): a misspelt key is an error, never silently ignored. The deeper
rules (tokens, secrets, hosts, tests, approvals) are lints in ``services/template_bundle.py``.
"""

from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

BUNDLE_FORMAT = "wf-template/1"
SEMVER_RE = re.compile(r"^\d+\.\d+\.\d+$")
KEY_RE = re.compile(r"^[a-z][a-z0-9_]{0,40}$")
SLUG_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
COLUMN_TYPES = ("string", "number", "integer", "boolean", "datetime", "json")


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TplTemplateRequirement(_Strict):
    slug: str
    version: str = "*"  # "*", "1.2.3" or "^1" / "^1.2.3"


class TplSecret(_Strict):
    name: str = Field(..., pattern=r"^[A-Za-z_][A-Za-z0-9_]*$")
    used_by: list[str] = Field(default_factory=list)  # "<workflow key>/<node name>"


class TplVariable(_Strict):
    name: str = Field(..., pattern=r"^[A-Za-z_][A-Za-z0-9_]*$")
    default: str | None = None
    type: Literal["string", "number"] = "string"
    description: str | None = None


class TplRequires(_Strict):
    templates: list[TplTemplateRequirement] = Field(default_factory=list)
    secrets: list[TplSecret] = Field(default_factory=list)
    variables: list[TplVariable] = Field(default_factory=list)
    egress_hosts: list[str] = Field(default_factory=list)


class TplApi(_Strict):
    """An API for the project's catalog: an OpenAPI file shipped with the template, or an already-normalised ``spec``
    (``{base_url, operations}``, what the export tool writes)."""

    key: str = Field(..., pattern=KEY_RE.pattern)
    name: str = Field(..., min_length=1, max_length=120)
    file: str | None = None
    spec: dict[str, Any] | None = None


class TplColumn(_Strict):
    name: str
    type: str
    required: bool = False
    default: Any = None


class TplTable(_Strict):
    key: str = Field(..., pattern=KEY_RE.pattern)
    name: str = Field(..., pattern=r"^[A-Za-z_][A-Za-z0-9_]{0,62}$")
    description: str | None = None
    columns: list[TplColumn] = Field(..., min_length=1)
    seed_rows: list[dict[str, Any]] = Field(default_factory=list)

    @field_validator("columns")
    @classmethod
    def _column_types(cls, v: list[TplColumn]) -> list[TplColumn]:
        for c in v:
            if c.type not in COLUMN_TYPES:
                raise ValueError(f"column '{c.name}': type must be one of {', '.join(COLUMN_TYPES)}")
        return v


class TplWorkflow(_Strict):
    key: str = Field(..., pattern=KEY_RE.pattern)
    role: Literal["entry", "subworkflow"]
    name: str = Field(..., min_length=1, max_length=255)
    description: str | None = Field(None, max_length=1000)
    publish: bool = False
    definition: dict[str, Any]


class TplApp(_Strict):
    """Marks a bundle that the AI app composer generated (Capability Graph, PLAN section 5.3): a whole application installed as one
    bundle. With it present a bundle may have more than one ``entry`` workflow (``TPL_ENTRY_COUNT``)."""

    title: str = Field(..., min_length=1, max_length=120)
    build_id: str = Field(..., min_length=1, max_length=64)


class TplFewShot(_Strict):
    request: str = Field(..., min_length=1)
    spec: dict[str, Any]
    eval_overlap: list[str] = Field(default_factory=list)


class TplCi(_Strict):
    min_tests_per_workflow: int = Field(3, ge=1, le=50)


class TemplateBundle(_Strict):
    format: str
    slug: str
    version: str
    name: str = Field(..., min_length=1, max_length=120)
    summary: str = Field(..., min_length=1, max_length=500)
    category: str = Field(..., min_length=1, max_length=60)
    tags: list[str] = Field(default_factory=list)
    min_engine_stage: int = Field(8, ge=1)
    requires: TplRequires = Field(default_factory=TplRequires)
    apis: list[TplApi] = Field(default_factory=list)
    tables: list[TplTable] = Field(default_factory=list)
    workflows: list[TplWorkflow] = Field(..., min_length=1)
    few_shot: TplFewShot | None = None
    ci: TplCi = Field(default_factory=TplCi)
    app: TplApp | None = None


# ── requests ─────────────────────────────────────────────────────────────────────────────────


class InstallRequest(BaseModel):
    """Body of ``POST /projects/{p}/templates/{slug}/install`` (section 8.3)."""

    model_config = ConfigDict(extra="forbid")

    version: str | None = None
    clash: Literal["rename", "fail"] = "rename"
    reuse_tables: list[str] = Field(default_factory=list)
    as_copy: bool = False
    run_tests: bool = True
    environment: str | None = None

"""Library feature schemas — saved analytics, runs, publish, lineage."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

Kind = Literal["sample", "aggregate", "profile", "pivot"]


class VersionSelector(BaseModel):
    """Which version a saved definition runs against."""

    mode: Literal["current", "tag", "version"] = "current"
    tag: str | None = None
    version_number: int | None = None

    @model_validator(mode="after")
    def _check(self):
        if self.mode == "tag" and not self.tag:
            raise ValueError("mode=tag requires tag")
        if self.mode == "version" and self.version_number is None:
            raise ValueError("mode=version requires version_number")
        return self


class DefinitionCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=2000)
    kind: Kind
    version_selector: VersionSelector = Field(default_factory=VersionSelector)
    sheet: str | None = Field(default=None, description="Sheet for multi-sheet datasets")
    params: dict[str, Any] = Field(
        default_factory=dict,
        description="Body of the underlying /sample, /aggregate, or /profile request "
                    "(minus dataset/version/sheet, which come from the definition)")


class DefinitionUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = None
    version_selector: VersionSelector | None = None
    sheet: str | None = None
    params: dict[str, Any] | None = None


class DefinitionOut(BaseModel):
    id: str
    dataset_id: str
    name: str
    description: str | None = None
    kind: str
    version_selector: dict[str, Any]
    sheet: str | None = None
    params: dict[str, Any] = Field(default_factory=dict)
    created_by: str | None = None
    created_at: str
    updated_at: str


class AnalyticsRunOut(BaseModel):
    id: str
    definition_id: str
    dataset_version_id: str | None = None
    job_id: str | None = None
    status: str
    result_summary: dict[str, Any] | None = None
    artifact_id: str | None = None
    triggered_by: str | None = None
    started_at: str
    completed_at: str | None = None
    error: str | None = None


class RunResponse(AnalyticsRunOut):
    """A completed run plus the inline result of the underlying operation."""

    result: dict[str, Any] | None = None


class PublishRequest(BaseModel):
    mode: Literal["new_dataset", "new_version"]
    name: str | None = Field(
        default=None, max_length=255,
        description="Name for the new dataset (new_dataset mode only)")

    @model_validator(mode="after")
    def _name_only_for_new_dataset(self) -> "PublishRequest":
        # new_version mode has no name to set — a version is named by its
        # number. Silently dropping a supplied name lost input from a UI that
        # filled the field then switched mode; reject it so the field's meaning
        # is honest.
        if self.mode == "new_version" and self.name is not None:
            raise ValueError("name applies only to new_dataset mode")
        return self


class PublishResponse(BaseModel):
    dataset_id: str
    dataset_name: str
    version_id: str
    version_number: int
    mode: str


class LineageParent(BaseModel):
    """One version of this dataset and the version it was derived from.

    ``parent_visible`` is false when the parent lives in a team the caller
    cannot read; the identifying fields are then null rather than the row being
    dropped, so the derivation still shows the right number of sources. See
    ``repo.get_lineage``.
    """

    id: str
    dataset_version_id: str
    version_number: int
    relation: str
    created_at: str
    parent_visible: bool = True
    parent_dataset_id: str | None = None
    parent_dataset_name: str | None = None
    parent_version_id: str | None = None
    parent_version_number: int | None = None
    parent_sheet_key: str | None = None


class LineageChild(BaseModel):
    """One version derived FROM this dataset.

    ``child_visible`` false means the derived dataset belongs to a team the
    caller cannot read, so its identity is withheld. ``parent_version_number``
    and ``parent_sheet_key`` describe *this* dataset's side of the edge and are
    never withheld.
    """

    id: str
    relation: str
    created_at: str
    child_visible: bool = True
    child_dataset_id: str | None = None
    child_dataset_name: str | None = None
    child_version_id: str | None = None
    child_version_number: int | None = None
    parent_version_number: int | None = None
    parent_sheet_key: str | None = None


class LineageResponse(BaseModel):
    dataset_id: str
    parents: list[LineageParent] = Field(default_factory=list)
    children: list[LineageChild] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Chart definitions (Wave 2 §14) — thin, no query logic of their own
# ---------------------------------------------------------------------------

ChartType = Literal["bar", "line", "area", "scatter", "pie", "table", "kpi"]


class ChartCreate(BaseModel):
    """A chart over exactly one data source: a saved definition OR a view."""

    name: str = Field(..., min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=2000)
    chart_type: ChartType
    definition_id: str | None = Field(
        default=None, description="Saved analytics definition (pivot/aggregate/...) to render")
    view_id: str | None = Field(default=None, description="Saved view to render")
    config: dict[str, Any] = Field(
        default_factory=dict,
        description="Frontend-owned encoding (axes, series, colors) — opaque here")

    @model_validator(mode="after")
    def _exactly_one_source(self):
        if (self.definition_id is None) == (self.view_id is None):
            raise ValueError("exactly one of definition_id or view_id is required")
        return self


class ChartUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=2000)
    chart_type: ChartType | None = None
    definition_id: str | None = None
    view_id: str | None = None
    config: dict[str, Any] | None = None


class ChartOut(BaseModel):
    id: str
    dataset_id: str
    definition_id: str | None = None
    view_id: str | None = None
    name: str
    description: str | None = None
    chart_type: str
    config: dict[str, Any] = Field(default_factory=dict)
    created_by: str | None = None
    created_at: str
    updated_at: str


class ChartSeries(BaseModel):
    """One plotted series, aligned positionally to the shared category axis."""

    name: str
    data: list[Any] = Field(default_factory=list)


class ChartRenderResponse(BaseModel):
    """A chart's data, computed from the definition or view it references.

    Charts own no query logic — rendering re-runs the referenced source and
    shapes the result. Nothing is persisted: this is a read.
    """

    chart_id: str
    chart_type: str
    categories: list[str] = Field(default_factory=list)
    series: list[ChartSeries] = Field(default_factory=list)
    x_field: str | None = None
    y_fields: list[str] = Field(default_factory=list)
    series_field: str | None = None
    row_count: int = 0
    total_rows: int | None = Field(
        default=None,
        description="Rows the source matched in total. Larger than `row_count` "
                    "means the render read only the first page. Null means the "
                    "source clipped the result without reporting a total — "
                    "there are more rows than `row_count`, and `truncated` is "
                    "true.")
    masked_columns: list[str] = Field(
        default_factory=list,
        description="Columns whose values were masked for this caller because "
                    "the data dictionary marks them sensitive")
    source: dict[str, Any] = Field(
        default_factory=dict, description="Which definition or view was run")
    truncated: bool = Field(
        default=False,
        description="This chart does not show everything: the category axis was "
                    "capped for readability, or the source returned more rows "
                    "than one page")


class LineageNode(BaseModel):
    id: str
    name: str
    domain: str | None = None
    deprecated: bool = False
    created_at: str
    is_root: bool = False


class LineageEdge(BaseModel):
    """`child_id` was derived from `parent_id` by `relation`."""

    child_id: str
    parent_id: str
    relation: str
    depth: int


class LineageGraphResponse(BaseModel):
    """The full derivation DAG around a dataset, upstream and downstream.

    `GET .../lineage` answers one hop; this walks the whole chain, so a
    published dataset can be traced back through every join, transformation,
    and upload that produced it.
    """

    dataset_id: str
    nodes: list[LineageNode] = Field(default_factory=list)
    edges: list[LineageEdge] = Field(default_factory=list)
    max_depth: int
    truncated: bool = Field(
        default=False,
        description="The DAG continues past `max_depth` — raise it to see further. "
                    "False for a graph that ends exactly at the cap.")
    hidden_nodes: int = Field(
        default=0,
        description="Datasets in this DAG that belong to teams you cannot read. "
                    "They and their edges are withheld; this is how many.")

"""Notebook contract (BUILD-PLAN §1.3): every request/response body, the cell kinds, per-kind specs and error codes.

The studio mirrors this file by hand in ``apps/workflow-studio/src/features/notebook/types.ts``; change both together.

Errors use the house envelope ``{"detail": {"code", "message", ...extra}}`` (see ``ErrorCode`` and ``notebook_error``).
Spec models validate the *shape* of a cell spec at create/patch time. Values may be ``{{cells.cN.rows}}`` /
``{{row.x}}`` references, so fields that can hold a reference are typed ``Any`` (or ``str | <type>``) here; the run loop
resolves references and reports ``bad_ref`` as a cell error.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

# --------------------------------------------------------------------------------------------------------------------
# Enums (as Literals)
# --------------------------------------------------------------------------------------------------------------------

CellKind = Literal[
    "query", "http", "workflow", "rule", "change", "data", "store", "browser", "for_each", "ask", "note", "wait",
]
CELL_KINDS: tuple[str, ...] = CellKind.__args__  # type: ignore[attr-defined]
#: Kinds that were removed (approval / person). Stored cells of these kinds still load, read-only (``Cell.removed``);
#: the run loop skips them, Keep leaves them out, and creating or editing one is 422 ``kind_removed``.
REMOVED_KINDS: tuple[str, ...] = ("approval", "person")
StoredCellKind = Literal[
    "query", "http", "workflow", "rule", "change", "data", "store", "browser", "for_each", "ask", "note", "wait",
    "approval", "person",
]

CellEffect = Literal["read", "write", "none", "llm"]
CreatedVia = Literal["user", "agent", "template"]

NotebookStatus = Literal["idle", "running", "needs_you", "failed", "done"]
RunStatus = Literal["running", "waiting", "done", "failed", "stopped_at_write", "needs_you", "cancelled", "interrupted"]
ResultStatus = Literal[
    "success", "error", "skipped_ask", "guard_false", "kept_earlier", "needs_approval", "waiting", "applied",
    "discarded", "cancelled",
]
OnApplied = Literal["stop", "skip"]
StopReason = Literal["applied_write", "cell_failed", "needs_approval", "waiting", "cancelled"]

KeepTarget = Literal["workflow", "test_suite", "scheduled_check"]
KeepItemStatus = Literal["ok", "needs_choice", "blocked"]
KeepItemKind = Literal["params", "trigger", "slots", "run_as", "writes", "empty_policy", "excluded"]
ParamMode = Literal["fixed", "input"]
WriteMode = Literal["approval", "auto"]
EmptyPolicy = Literal["stop_quietly", "continue"]

ControlAction = Literal["pause", "resume", "step", "stop", "takeover", "handback"]

# --------------------------------------------------------------------------------------------------------------------
# Error codes
# --------------------------------------------------------------------------------------------------------------------

ErrorCode = Literal[
    # HTTP-level (detail.code)
    "notebook_not_found",       # 404
    "cell_not_found",           # 404
    "run_not_found",            # 404
    "template_not_found",       # 404
    "invalid_spec",             # 422 {fields: [{loc, msg}]}
    "unknown_kind",             # 422
    "kind_removed",             # 422 approval / person cells were removed (stored ones still load read-only)
    "not_select",               # 422 query SQL is not SELECT/WITH
    "connection_readonly",      # 422 change cell on a read-only connection
    "connection_not_found",     # 422
    "invalid_parent",           # 422 parent_cell_id is not a for_each of this notebook, or kind not allowed there
    "invalid",                  # 422 generic (keep)
    "cell_applied",             # 409 delete of a cell with a notebook_writes row
    "run_in_progress",          # 409 {run_id}
    "not_pending",              # 409 apply/discard on a cell that is not waiting
    "unbound_credential",       # 409 {items}
    "spec_export_unavailable",  # 501
    "not_implemented",          # 501 (Phase 0 stubs)
    # Cell-level (CellResult.error.code)
    "bad_ref",
    "llm_unavailable",
    "browser_unavailable",
    "query_failed",
    "http_failed",
    "workflow_failed",
    "rule_failed",
    "change_failed",
    "store_failed",
    "row_cap_exceeded",
    "timeout",
    "cancelled",
    "internal",
]


def notebook_error(status: int, code: str, message: str, **extra: Any) -> HTTPException:
    """Build an HTTPException in the house envelope. ``raise notebook_error(409, "run_in_progress", "...", run_id=r)``."""
    return HTTPException(status_code=status, detail={"code": code, "message": message, **extra})


class ApiErrorBody(BaseModel):
    code: str
    message: str
    model_config = ConfigDict(extra="allow")


class CellError(BaseModel):
    code: str
    message: str
    model_config = ConfigDict(extra="allow")


# --------------------------------------------------------------------------------------------------------------------
# Per-kind specs
# --------------------------------------------------------------------------------------------------------------------


MAX_NESTING = 64


def _reject_nul(data: Any) -> Any:
    """NUL characters can not be stored in Postgres text/JSONB: refuse them anywhere in a request (keys or values) as a
    422 ``invalid`` instead of a 500 at insert time. Also refuses nesting deeper than 64 levels. Iterative, so a deeply nested
    body can not blow the stack."""
    stack = [(data, 0)]
    while stack:
        v, depth = stack.pop()
        if depth > MAX_NESTING:
            # an HTTP error, not a ValueError: a validation error would echo the (too deep) input back and the
            # default 422 handler can not serialise it
            raise notebook_error(422, "invalid", f"the body is nested more than {MAX_NESTING} levels deep")
        if isinstance(v, str):
            if "\x00" in v:
                raise notebook_error(422, "invalid", "text must not contain NUL (\\u0000) characters")
        elif isinstance(v, dict):
            stack.extend((k, depth + 1) for k in v.keys())
            stack.extend((x, depth + 1) for x in v.values())
        elif isinstance(v, (list, tuple)):
            stack.extend((x, depth + 1) for x in v)
    return data


class _NoNul(BaseModel):
    @model_validator(mode="before")
    @classmethod
    def _no_nul(cls, data: Any) -> Any:
        return _reject_nul(data)


class _Spec(_NoNul):
    model_config = ConfigDict(extra="forbid")


class QuerySpec(_Spec):
    connection: str  # connection id or key
    sql: str = Field(..., min_length=1, max_length=20000)
    params: dict[str, Any] = Field(default_factory=dict)
    limit: int = Field(500, ge=1, le=500)


class HttpExpect(_Spec):
    status: int | None = None
    json_path_equals: dict[str, Any] | None = None  # {"$.a.b": value}


_IDENT_RE = r"^[A-Za-z_][A-Za-z0-9_]{0,99}$"


class HttpAuth(_Spec):
    """How the request signs in. ``secret`` is a project secret's key (the value never enters the notebook); the
    engine fills it in. ``api_key`` sends ``name: <secret>`` as a header or ``?name=<secret>`` (``add_to: query``)."""

    type: Literal["none", "bearer", "basic", "api_key"] = "none"
    secret: str | None = Field(None, pattern=_IDENT_RE)
    username: str | None = Field(None, max_length=200)
    name: str | None = Field(None, max_length=100, pattern=r"^[A-Za-z0-9_.\-]+$")
    add_to: Literal["header", "query"] = "header"

    @model_validator(mode="after")
    def _needs_secret(self) -> "HttpAuth":
        if self.type != "none" and not self.secret:
            raise ValueError(f"{self.type} auth needs a secret (a project secret's key)")
        if self.type == "basic" and not self.username:
            raise ValueError("basic auth needs a username")
        return self


class HttpAssertion(_Spec):
    """One sentence-style check, e.g. ``{kind: status, value: 200}``, ``{kind: time, op: lt, value: 500}``,
    ``{kind: json_path, path: "$.id", op: exists}``, ``{kind: header, name: Content-Type, op: exists}``,
    ``{kind: schema, json_schema: {...}}``."""

    kind: Literal["status", "time", "json_path", "header", "schema"]
    op: Literal["eq", "ne", "lt", "le", "gt", "ge", "contains", "not_contains", "exists", "not_exists", "matches",
                "in", "has_item"] = "eq"  # has_item: the list at ``path`` has an item whose fields equal ``value``
    path: str | None = Field(None, max_length=500)
    name: str | None = Field(None, max_length=200)
    value: Any = None
    json_schema: dict[str, Any] | None = None

    @model_validator(mode="after")
    def _shape(self) -> "HttpAssertion":
        if self.kind == "json_path" and not self.path:
            raise ValueError("a json_path assertion needs a path such as $.id")
        if self.kind == "header" and not self.name:
            raise ValueError("a header assertion needs the header name")
        if self.kind == "schema" and not isinstance(self.json_schema, dict):
            raise ValueError("a schema assertion needs json_schema")
        if self.kind == "time" and not isinstance(self.value, (int, float)):
            raise ValueError("a time assertion needs a number of milliseconds")
        if self.op == "has_item" and self.kind != "json_path":
            raise ValueError("has_item is a json_path check: path names a list, value the fields an item must have")
        return self


class HttpExtract(_Spec):
    """Save a value from the response as a variable later cells use as ``{{name}}`` / ``{{vars.name}}``."""

    name: str = Field(..., pattern=_IDENT_RE)
    source: Literal["json_path", "header", "status"] = "json_path"
    path: str | None = Field(None, max_length=500)


class HttpSpec(_Spec):
    method: Literal["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE"] = "GET"
    url: str | None = None
    api_ref: str | None = None
    params: dict[str, Any] = Field(default_factory=dict)
    headers: dict[str, str] = Field(default_factory=dict)
    body: Any = None
    body_type: Literal["json", "form"] = "json"
    auth: HttpAuth | None = None
    assertions: list[HttpAssertion] = Field(default_factory=list, max_length=50)
    extract: list[HttpExtract] = Field(default_factory=list, max_length=20)
    expect: HttpExpect | None = None
    credential_slot: str | None = None
    # ``always``: the request is safe to send again (a login, a test call), so a success is not recorded as an applied
    # write and a rerun does not stop before it. Unset/``once`` (the default) keeps the stop-before-applied-write rule.
    rerun: Literal["once", "always"] | None = None
    continue_on_fail: bool | None = None  # true: a failure of this cell does not stop the run


class WorkflowSpec(_Spec):
    workflow_id: str
    version: int | Literal["published"] = "published"
    inputs: dict[str, Any] = Field(default_factory=dict)


class RuleField(_Spec):
    left: str
    right: str
    op: Literal["eq", "ne", "lt", "le", "gt", "ge", "approx"] = "eq"
    tolerance: float | None = None


class RuleWhere(_Spec):
    field: str
    op: Literal["eq", "ne", "lt", "le", "gt", "ge", "in", "not_in", "contains", "is_null", "not_null", "older_than"]
    value: Any = None


class RuleSpec(_Spec):
    op: Literal["compare", "filter", "count", "assert"]
    left: Any  # usually "{{cells.c2.rows}}"
    right: Any = None
    key: str | list[str] | None = None
    fields: list[RuleField] = Field(default_factory=list)
    where: list[RuleWhere] = Field(default_factory=list)
    continue_on_fail: bool | None = None  # true: a failing check does not stop the run
    fail_on_mismatch: bool | None = None  # compare only: true makes any mismatch fail the rule (default: report only)


class ChangeSpec(_Spec):
    connection: str
    table: str
    key: dict[str, Any]
    set: dict[str, Any]
    reason: str = ""
    idempotency_key: str | None = None


MAX_DATA_ROWS = 5000


class DataSpec(_Spec):
    columns: list[str] = Field(default_factory=list)
    rows: list[dict[str, Any]] | list[list[Any]] = Field(default_factory=list, max_length=MAX_DATA_ROWS)
    csv: str | None = None  # pasted CSV, parsed into columns/rows at create time
    from_table: str | None = None  # a project data table (id or name): its current rows, read when the cell runs


class StoreSpec(_Spec):
    table_id: str | None = None
    new_table: str | None = None
    mode: Literal["append", "upsert"] = "append"
    key: str | list[str] | None = None
    rows: Any  # "{{cells.c4.rows}}"


class BrowserStep(BaseModel):
    """One test-builder Action (apps/test-builder/src/actions.ts); validated strictly by the jobs-service."""

    action: Literal[
        "goto", "click", "fill", "select", "press", "hover", "upload", "otp", "waitFor", "download", "expectVisible",
        "expectText", "expectCount", "done",
    ]
    model_config = ConfigDict(extra="allow")


class BrowserSpec(_Spec):
    base_url: str
    allowed_hosts: list[str] = Field(default_factory=list)
    credential_slot: str | None = None
    steps: list[BrowserStep] = Field(default_factory=list)
    expect_per_row: bool | None = None
    writes: bool = False


class ForEachSpec(_Spec):
    over: Any  # "{{cells.c2.rows}}"
    max_rows: int = Field(500, ge=1, le=500)
    stop_on_fail: bool = False
    mode: Literal["sequential", "parallel", "child_task"] = "sequential"  # child_task: coming (create refuses it)
    concurrency: int = Field(4, ge=1, le=16)  # parallel only


class WaitCondition(_Spec):
    """Re-run a read-only query until its rows are ``nonempty`` (or ``empty``)."""

    connection: str
    sql: str = Field(..., min_length=1, max_length=20000)
    params: dict[str, Any] = Field(default_factory=dict)
    expect: Literal["nonempty", "empty"] = "nonempty"


class WaitSpec(_Spec):
    """``duration``: sleep (``"30s"``, ``"2h"``); ``condition``: poll a query every ``poll_every`` until it holds or
    ``timeout`` passes (cell error ``timeout``). The run is ``waiting`` meanwhile. Event waits are coming."""

    mode: Literal["duration", "condition"] = "duration"
    duration: str | None = None
    condition: WaitCondition | None = None
    poll_every: str = "30s"
    timeout: str = "1h"


class AskSpec(_Spec):
    text: str = Field(..., min_length=1, max_length=8000)


class NoteSpec(_Spec):
    markdown: str = ""


SPEC_MODELS: dict[str, type[BaseModel]] = {
    "query": QuerySpec,
    "http": HttpSpec,
    "workflow": WorkflowSpec,
    "rule": RuleSpec,
    "change": ChangeSpec,
    "data": DataSpec,
    "store": StoreSpec,
    "browser": BrowserSpec,
    "for_each": ForEachSpec,
    "ask": AskSpec,
    "note": NoteSpec,
    "wait": WaitSpec,
}

# Kinds that may only live inside a for_each body / never inside one.
TOP_LEVEL_ONLY_KINDS: frozenset[str] = frozenset({"for_each", "ask", "note", "wait", "change"})


def validate_spec(kind: str, spec: dict[str, Any]) -> dict[str, Any]:
    """Validate ``spec`` against its kind's model. Raises 422 ``unknown_kind`` / ``invalid_spec {fields}``."""
    if kind in REMOVED_KINDS:
        raise notebook_error(422, "kind_removed", f"{kind} cells were removed; existing ones stay as read-only cards",
                             kinds=list(CELL_KINDS))
    model = SPEC_MODELS.get(kind)
    if model is None:
        raise notebook_error(422, "unknown_kind", f"unknown cell kind: {kind!r}", kinds=list(CELL_KINDS))
    try:
        out = model.model_validate(spec or {}).model_dump(mode="json", exclude_none=False)
    except ValidationError as e:
        fields = [{"loc": list(err["loc"]), "msg": err["msg"]} for err in e.errors()]
        raise notebook_error(422, "invalid_spec", f"invalid {kind} spec", fields=fields) from None
    for k in OPTIONAL_SPEC_FIELDS:  # newer optional fields are stored only when set: older specs dump unchanged
        if k in out and out[k] is None:
            del out[k]
    return out


#: Spec fields added after the first release. Left out of a validated spec when unset, so a spec without them
#: validates (and is stored, hashed and compared) exactly as before.
OPTIONAL_SPEC_FIELDS: tuple[str, ...] = ("rerun", "continue_on_fail", "from_table", "fail_on_mismatch")


class Guard(_Spec):
    """Run the cell only when the condition holds. ``left`` is usually a ref such as ``{{cells.c4.count}}``."""

    left: Any
    op: Literal["eq", "ne", "lt", "le", "gt", "ge", "nonempty", "empty"] = "nonempty"
    right: Any = None


# --------------------------------------------------------------------------------------------------------------------
# Shared response pieces
# --------------------------------------------------------------------------------------------------------------------


class RowsPreview(BaseModel):
    """``CellResult.output`` for row-producing cells: ≤50 rows, plus the true count and cap info."""

    columns: list[str] = Field(default_factory=list)
    rows: list[dict[str, Any]] = Field(default_factory=list)
    row_count: int = 0
    truncated: bool = False
    cap: int | None = None
    model_config = ConfigDict(extra="allow")


class CellResult(BaseModel):
    id: str
    run_id: str
    cell_id: str
    row_index: int | None = None
    status: ResultStatus
    output: dict[str, Any] | None = None
    output_ref: str | None = None
    error: CellError | None = None
    execution_id: str | None = None
    job_id: str | None = None
    ms: int | None = None
    created_at: datetime
    run_n: int | None = None  # convenience for "from run 1" labels


class Cell(BaseModel):
    id: str
    notebook_id: str
    parent_cell_id: str | None = None
    position: int
    name: str  # c1, c2, ... (used in references)
    kind: StoredCellKind
    title: str | None = None
    spec: dict[str, Any] = Field(default_factory=dict)
    guard: Guard | None = None
    created_by: str | None = None
    created_via: CreatedVia = "user"
    created_at: datetime
    updated_at: datetime
    # computed
    effect: CellEffect = "none"
    needs_ai: bool = False
    stale: bool = False
    latest: CellResult | None = None
    children: list[Cell] | None = None
    applied: bool = False  # has a notebook_writes row
    removed: bool = False  # a removed kind (approval / person): shown read-only, skipped by runs, left out by Keep


class RunStop(BaseModel):
    reason: StopReason
    cell_id: str | None = None
    applied_at: datetime | None = None
    applied_by: str | None = None
    model_config = ConfigDict(extra="allow")


class RunSummary(BaseModel):
    id: str
    notebook_id: str
    n: int
    from_position: int = 0
    status: RunStatus
    stop: RunStop | None = None
    on_applied: OnApplied = "stop"
    triggered_by: str | None = None
    run_as: str | None = None
    via_agent: str | None = None
    heartbeat_at: datetime | None = None
    started_at: datetime
    finished_at: datetime | None = None
    continue_on_fail: bool = False


class Run(RunSummary):
    results: list[CellResult] = Field(default_factory=list)


class KeptEntry(BaseModel):
    target: KeepTarget
    workflow_id: str
    at: datetime
    by: str | None = None


class LastRunInfo(BaseModel):
    n: int
    status: RunStatus
    at: datetime
    triggered_by: str | None = None
    run_as: str | None = None
    via_agent: str | None = None


class CheckSummary(BaseModel):
    pass_: int = Field(0, alias="pass")
    fail: int = 0
    last: list[str] = Field(default_factory=list)  # recent execution statuses, oldest first
    model_config = ConfigDict(populate_by_name=True)


class NotebookListItem(BaseModel):
    id: str
    title: str
    status: NotebookStatus
    cell_count: int
    no_ai_count: int
    needs_ai_count: int
    needs_you: bool
    writes_env: str | None = None  # environment name when any cell writes, e.g. "production"
    last_run: LastRunInfo | None = None
    kept: list[KeptEntry] = Field(default_factory=list)
    check: CheckSummary | None = None
    updated_at: datetime
    created_by: str | None = None


class Notebook(BaseModel):
    id: str
    project_id: str
    team_id: str | None = None
    title: str
    description: str | None = None
    status: NotebookStatus
    environment: str | None = None
    template_slug: str | None = None
    run_as: str | None = None
    created_by: str | None = None
    active_run_id: str | None = None
    kept: list[KeptEntry] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime
    cells: list[Cell] = Field(default_factory=list)
    runs: list[RunSummary] = Field(default_factory=list)  # latest 20, newest first
    notes: str | None = None  # Ask's running notes for this notebook (the model rewrites them; PATCH clears them)


# --------------------------------------------------------------------------------------------------------------------
# Capabilities / templates
# --------------------------------------------------------------------------------------------------------------------


class CapabilityFlag(BaseModel):
    available: bool
    reason: str | None = None


class WritesCapability(BaseModel):
    available: bool


class Capabilities(BaseModel):
    ai: CapabilityFlag
    browser: CapabilityFlag
    writes: WritesCapability


class TemplateCellSummary(BaseModel):
    kind: CellKind
    title: str | None = None


class NotebookTemplate(BaseModel):
    slug: str
    title: str
    description: str
    icon: str | None = None
    keeps_as: KeepTarget | None = None
    cells: list[TemplateCellSummary] = Field(default_factory=list)


# --------------------------------------------------------------------------------------------------------------------
# Requests
# --------------------------------------------------------------------------------------------------------------------


class _Req(_NoNul):
    model_config = ConfigDict(extra="forbid")


class NotebookCreate(_Req):
    title: str = Field(..., min_length=1, max_length=200)
    template: str | None = None
    environment: str | None = None
    connection: str | None = None  # fills __CONNECTION__ in templates
    created_by: str | None = None
    description: str | None = None


class NotebookPatch(_Req):
    title: str | None = Field(None, min_length=1, max_length=200)
    description: str | None = None
    environment: str | None = None
    run_as: str | None = None
    notes: str | None = Field(None, max_length=20000)  # "" or null clears Ask's notes; longer text is cut to 2000


class CellCreate(_Req):
    kind: str  # validated by validate_spec: unknown_kind / kind_removed in the house envelope
    title: str | None = Field(None, max_length=200)
    spec: dict[str, Any] = Field(default_factory=dict)
    guard: Guard | None = None
    parent_cell_id: str | None = None
    created_by: str | None = None


class CellPatch(_Req):
    title: str | None = Field(None, max_length=200)
    spec: dict[str, Any] | None = None
    guard: Guard | None = None
    clear_guard: bool = False


class CellPatchResponse(BaseModel):
    cell: Cell
    stale_from: str | None = None  # id of the first cell now stale (the edited one), or None if nothing changed


class RunCreate(_Req):
    from_cell_id: str | None = None
    on_applied: OnApplied = "stop"
    reapply: list[str] = Field(default_factory=list)
    triggered_by: str | None = None
    run_as: str | None = None
    via_agent: str | None = None
    continue_on_fail: bool = False  # a failing cell does not stop the run; the run still ends ``failed``


class ApplyRequest(_Req):
    approved_by: str
    idempotency_key: str | None = None


class DiscardRequest(_Req):
    by: str | None = None


class RetryRowsRequest(_Req):
    """Rerun a for-each for some row indexes only (default: the failed rows of its latest result)."""

    rows: list[int] | None = None
    triggered_by: str | None = None
    run_as: str | None = None
    via_agent: str | None = None


class PreviewCellInputs(BaseModel):
    cell_id: str
    name: str
    kind: StoredCellKind
    inputs: dict[str, Any] | None = None  # the spec with {{row.…}} / {{cells.…}} resolved
    error: CellError | None = None  # e.g. bad_ref


class PreviewRow(BaseModel):
    i: int
    row: Any
    cells: list[PreviewCellInputs] = Field(default_factory=list)


class ForEachPreview(BaseModel):
    """``GET /notebook-cells/{id}/preview?n=5``: the first n rows' resolved body inputs, nothing run."""

    total: int
    rows: list[PreviewRow] = Field(default_factory=list)
    error: CellError | None = None  # ``over`` could not be resolved


class CellActionResponse(BaseModel):
    """Response of apply / discard."""

    result: CellResult
    run: RunSummary


class RowsPage(BaseModel):
    columns: list[str]
    rows: list[dict[str, Any]]
    total: int


class ControlRequest(_Req):
    action: ControlAction


class CellInputRequest(_Req):
    """One take-over input for a browser cell's live page (POST /notebook-cells/{id}/input)."""
    type: Literal["click", "type", "key", "scroll"]
    x: float | None = Field(None, ge=0, le=1)
    y: float | None = Field(None, ge=0, le=1)
    text: str | None = Field(None, min_length=1, max_length=2000)
    key: str | None = Field(None, max_length=20)
    dx: float | None = Field(None, ge=-5000, le=5000)
    dy: float | None = Field(None, ge=-5000, le=5000)


class EnvVar(_Req):
    """One environment variable: a plain ``value`` (e.g. ``base_url``), or ``secret`` = a project secret's key, which
    a request uses as ``{{ $vars.KEY }}`` (the value is never stored or shown here)."""
    name: str = Field(..., pattern=_IDENT_RE)
    value: str | None = Field(None, max_length=4000)
    secret: str | None = Field(None, pattern=_IDENT_RE)

    @model_validator(mode="after")
    def _one(self) -> "EnvVar":
        if (self.value is None) == (self.secret is None):
            raise ValueError(f"{self.name}: give either a value or a secret")
        return self


class EnvSet(_Req):
    name: str = Field(..., min_length=1, max_length=60, pattern=r"^[A-Za-z0-9 _.\-]+$")
    vars: list[EnvVar] = Field(default_factory=list, max_length=100)


class NotebookEnvironments(_Req):
    """``PUT/GET /notebooks/{id}/environments``: named variable sets (``dev``, ``staging``) and the active one."""
    active: str | None = None
    sets: list[EnvSet] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def _active_exists(self) -> "NotebookEnvironments":
        names = [s.name for s in self.sets]
        if len(set(names)) != len(names):
            raise ValueError("environment names must be unique")
        for s in self.sets:
            vn = [v.name for v in s.vars]
            if len(set(vn)) != len(vn):
                raise ValueError(f"{s.name}: variable names must be unique")
        if self.active is not None and self.active not in names:
            raise ValueError(f"active environment {self.active!r} is not one of {names}")
        return self


class ImportHttpRequest(_Req):
    """``POST /notebooks/{id}/import-http``: a pasted cURL command, or endpoints of an API-catalog spec."""
    curl: str | None = Field(None, min_length=1, max_length=20000)
    spec_id: str | None = None
    operation_ids: list[str] = Field(default_factory=list, max_length=100)
    add: bool = False  # true: append the cells; false: only return the planned specs (a preview)
    by: str | None = None

    @model_validator(mode="after")
    def _one(self) -> "ImportHttpRequest":
        if (self.curl is None) == (self.spec_id is None):
            raise ValueError("give either curl or spec_id")
        return self


class AskSelection(_Req):
    """Rows the user ticked in a result table: the cell (by name), the key column, and the keys of the rows."""
    cell: str = Field(..., pattern=r"^c\d+$")
    key: str = Field(..., pattern=r"^[A-Za-z_][A-Za-z0-9_]*$")
    keys: list[str | int | float | bool | None] = Field(..., min_length=1, max_length=500)


class AskRequest(_Req):
    text: str = Field(..., min_length=1, max_length=8000)
    by: str | None = None
    selection: AskSelection | None = None


class AskResponse(BaseModel):
    ask_cell: Cell  # its latest result is error{code:"llm_unavailable"} when degraded
    planned: list[Cell] = Field(default_factory=list)
    removed: list[str] = Field(default_factory=list)  # names of the cells the plan's `remove` deleted


class ReportResponse(BaseModel):
    markdown: str


class TraceEntry(BaseModel):
    ts: datetime
    cell_id: str | None = None
    run_n: int | None = None
    kind: str
    text: str
    ms: int | None = None
    execution_id: str | None = None
    job_id: str | None = None


# --------------------------------------------------------------------------------------------------------------------
# Keep
# --------------------------------------------------------------------------------------------------------------------


class KeepParam(_Req):
    ref: str  # "c2.spec.params.days" style path to a value token
    mode: ParamMode = "fixed"
    name: str | None = None


class KeepTrigger(_Req):
    type: Literal["manual", "cron"] = "manual"
    cron: str | None = None


class KeepSlot(_Req):
    cell_id: str
    credential_id: str | None = None


class KeepWrite(_Req):
    cell_id: str
    mode: WriteMode = "approval"


class KeepRequest(_Req):
    target: KeepTarget
    cell_ids: list[str] = Field(default_factory=list)  # empty = all keepable cells
    params: list[KeepParam] = Field(default_factory=list)
    trigger: KeepTrigger = Field(default_factory=KeepTrigger)
    slots: list[KeepSlot] = Field(default_factory=list)
    run_as: str | None = None
    writes: list[KeepWrite] = Field(default_factory=list)
    empty_policy: EmptyPolicy = "stop_quietly"
    name: str | None = None
    by: str | None = None


class KeepChoice(BaseModel):
    value: Any
    label: str


class KeepItem(BaseModel):
    key: str  # e.g. "slots:cell_x", "excluded:cell_y"
    kind: KeepItemKind
    status: KeepItemStatus
    message: str
    cell_id: str | None = None
    choices: list[KeepChoice] | None = None


class KeepPreview(BaseModel):
    items: list[KeepItem]
    definition: dict[str, Any]  # the workflow definition that would be saved
    can_save: bool
    can_schedule: bool


class KeepResponse(BaseModel):
    workflow_id: str
    target: KeepTarget
    schedule_active: bool = False
    spec_url: str | None = None


class CheckRun(BaseModel):
    execution_id: str
    status: str
    start_time: datetime | None = None
    ms: int | None = None


class ChecksResponse(BaseModel):
    workflow_id: str | None = None
    runs: list[CheckRun] = Field(default_factory=list)
    pass_: int = Field(0, alias="pass")
    fail: int = 0
    model_config = ConfigDict(populate_by_name=True)


Cell.model_rebuild()

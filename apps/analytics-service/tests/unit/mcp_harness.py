"""Substitute for ``AnalyticsClient`` so the MCP tool bodies can be unit tested.

Every tool in ``app/features/mcp/tools/`` reaches the platform through exactly
one object — ``Ctx.client`` — which normally speaks HTTP over an in-process
ASGI transport to the real FastAPI app, and therefore needs Postgres, MinIO and
a seeded dataset before a single line of a tool body runs. Replace that one
object and the whole tool becomes reachable with none of it: argument handling,
path construction, response rendering, and the error translation that is the
actual product of this layer.

**The limitation, stated plainly.** A test built on this harness proves the tool
handles a given service response correctly. It does *not* prove the service ever
produces that response. Those are two different claims and only the second one
needs a database. ``tests/test_mcp_endpoint.py`` makes it — it drives the real
27 tools through the mounted endpoint against real data. If a canned shape here
drifts from what the service returns, the tests built on it keep passing while
production breaks, so the builders below construct their payloads from the
service's own pydantic response models (``Page``, ``QueryPage``,
``SqlQueryResponse``, ``VersionInfo``, …) and dump them the way FastAPI does.
A field renamed in a schema breaks the builder loudly rather than silently
canning a stale shape. Where a builder takes free-form ``**extra``, or where a
test scripts a bare dict, that guarantee is off and the shape is a guess.

Faithfulness notes worth knowing before you script anything:

* ``AnalyticsClient`` has **no** ``delete``. The tool surface writes but never
  deletes, and the client has no method for it; a fake that offered one would
  invite tests for a capability that cannot exist.
* ``get``/``post``/``put``/``patch`` drop ``None`` values from params and body
  (``client._clean``) so an unset tool parameter is never sent. ``merge_patch``
  deliberately does not, because the metadata routes distinguish an absent field
  (keep) from an explicit null (clear). The fake reproduces both, and records
  the body *after* that filtering — so what a test asserts on is what would have
  gone on the wire.
* ``merge_patch`` and ``patch`` both record as ``PATCH``: they are the same
  request to the service. The difference between them is visible in the body.
* A success with no content is ``None``, not ``{}`` — script ``None`` for a 204.

This module contains no tests. ``tests/unit/test_mcp_harness_smoke.py`` proves
it works against ``tools/orient.py``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Iterable, Sequence

from mcp.server.mcpserver import MCPServer

from app.api.pagination import Page
from app.features.auth.schemas import MeResponse, MembershipOut, UserOut
from app.features.data_accelerator.schemas import (
    DatasetInfo,
    SheetColumn,
    SheetMetadataResponse,
    VersionInfo,
)
from app.features.discovery.api import ColumnHit, ColumnMetadataOut, SheetMetadataOut
from app.features.discovery.health import DatasetHealthResponse, HealthDimension
from app.features.explorer.schemas import SqlQueryResponse
from app.features.mcp.client import ProblemError
from app.features.mcp.tools._common import Ctx
from app.shared.query.schemas import QueryPage

__all__ = [
    "Call",
    "FakeAnalyticsClient",
    "ToolSet",
    "column_hit",
    "column_metadata",
    "dataset",
    "health",
    "make_ctx",
    "me",
    "page",
    "problem",
    "query_page",
    "register_tools",
    "sheet",
    "sheet_column",
    "sheet_metadata",
    "sheet_selection_required",
    "sql_result",
    "unknown_column",
    "version",
]


# ---------------------------------------------------------------------------
# The fake client
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Call:
    """One request a tool made, as the service would have received it.

    ``params`` and ``body`` are post-``_clean``: keys whose value was ``None``
    are already gone, exactly as on the wire. ``body`` is ``None`` for GET.
    """

    method: str
    path: str
    params: dict[str, Any] = field(default_factory=dict)
    body: dict[str, Any] | None = None


def _clean(mapping: dict[str, Any] | None) -> dict[str, Any]:
    """Mirror of ``client._clean`` — drop None so no explicit null is sent."""
    if not mapping:
        return {}
    return {k: v for k, v in mapping.items() if v is not None}


class FakeAnalyticsClient:
    """Scripted stand-in for ``AnalyticsClient``, with the same method surface.

    Script a response per (method, path)::

        fake = FakeAnalyticsClient()
        fake.on_get("/auth/me", me(name="Ada"))
        fake.on_get("/datasets/d1/versions", problem(404, "Not found", "not-found"))

    A scripted value is returned as-is; a scripted ``ProblemError`` is raised,
    which is how the real client reports every non-2xx. An *unscripted* call is
    an ``AssertionError`` naming what was scripted — deliberately not a 404,
    because a tool calling an endpoint the test did not anticipate is a fact the
    test should be told about rather than a service condition to render.
    """

    def __init__(self) -> None:
        self.calls: list[Call] = []
        self._script: dict[tuple[str, str], list[Any]] = {}
        self.closed = False

    # -- scripting ----------------------------------------------------------

    def on(self, method: str, path: str, *responses: Any) -> "FakeAnalyticsClient":
        """Script one or more responses for ``method path``.

        With several responses the calls consume them in order and the last one
        repeats, so a tool that polls the same endpoint can be given a sequence
        without the test hand-rolling a counter.
        """
        if not responses:
            raise TypeError("script at least one response (use None for a 204)")
        self._script[(method.upper(), path)] = list(responses)
        return self

    def on_get(self, path: str, *responses: Any) -> "FakeAnalyticsClient":
        return self.on("GET", path, *responses)

    def on_post(self, path: str, *responses: Any) -> "FakeAnalyticsClient":
        return self.on("POST", path, *responses)

    def on_put(self, path: str, *responses: Any) -> "FakeAnalyticsClient":
        return self.on("PUT", path, *responses)

    def on_patch(self, path: str, *responses: Any) -> "FakeAnalyticsClient":
        """Covers both ``patch`` and ``merge_patch`` — one request to the service."""
        return self.on("PATCH", path, *responses)

    # -- the AnalyticsClient surface ----------------------------------------

    async def aclose(self) -> None:
        self.closed = True

    async def get(self, path: str, **params: Any) -> Any:
        return self._respond("GET", path, _clean(params), None)

    async def post(self, path: str, body: dict[str, Any] | None = None, **params: Any) -> Any:
        return self._respond("POST", path, _clean(params), _clean(body or {}))

    async def put(self, path: str, body: dict[str, Any] | None = None, **params: Any) -> Any:
        return self._respond("PUT", path, _clean(params), _clean(body or {}))

    async def patch(self, path: str, body: dict[str, Any] | None = None, **params: Any) -> Any:
        return self._respond("PATCH", path, _clean(params), _clean(body or {}))

    async def merge_patch(self, path: str, body: dict[str, Any]) -> Any:
        """Verbatim body — explicit nulls survive, which is the whole point."""
        return self._respond("PATCH", path, {}, dict(body))

    # -- inspection ---------------------------------------------------------

    def trace(self) -> list[tuple[str, str]]:
        """(method, path) in call order — for pinning the endpoint sequence."""
        return [(c.method, c.path) for c in self.calls]

    def calls_to(self, method: str, path: str) -> list[Call]:
        return [c for c in self.calls if c.method == method.upper() and c.path == path]

    def one_call_to(self, method: str, path: str) -> Call:
        """The single call to an endpoint; fails if it was called 0 or 2+ times."""
        found = self.calls_to(method, path)
        assert len(found) == 1, (
            f"expected exactly one {method.upper()} {path}, got {len(found)}. "
            f"Trace: {self.trace()}"
        )
        return found[0]

    # -- internals ----------------------------------------------------------

    def _respond(
        self, method: str, path: str, params: dict[str, Any], body: dict[str, Any] | None
    ) -> Any:
        self.calls.append(Call(method=method, path=path, params=params, body=body))
        key = (method, path)
        if key not in self._script:
            raise AssertionError(
                f"unscripted call: {method} {path}\n"
                f"  params={params} body={body}\n"
                f"  scripted: {sorted(f'{m} {p}' for m, p in self._script)}"
            )
        queued = self._script[key]
        response = queued.pop(0) if len(queued) > 1 else queued[0]
        if isinstance(response, ProblemError):
            raise response
        return response


def make_ctx(client: FakeAnalyticsClient) -> Ctx:
    """Wrap the fake in the ``Ctx`` every tool closure captures."""
    return Ctx(client=client)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Reaching the tool functions
# ---------------------------------------------------------------------------


class ToolSet(dict):
    """Registered tools by name, mapped to the async function itself.

    Calling ``tools["whoami"]()`` runs the real closure — ``@guard`` and all —
    but bypasses the MCP argument model, so pydantic-level constraints
    (``ge=1``, ``le=1000``) are NOT exercised. Use ``tools.server`` when the
    published schema is what a test is about; ``tests/unit/
    test_mcp_unknown_arguments.py`` is the model for that kind of test.
    """

    server: MCPServer
    ctx: Ctx
    client: FakeAnalyticsClient


def register_tools(module: Any, client: FakeAnalyticsClient) -> ToolSet:
    """Register a tool module against the fake and return its tools by name.

    ``module`` is any of the ``app.features.mcp.tools.*`` modules, which each
    expose ``register(server, ctx)``.
    """
    server: MCPServer = MCPServer(name="harness")
    ctx = make_ctx(client)
    module.register(server, ctx)
    tools = ToolSet(
        {name: tool.fn for name, tool in server._tool_manager._tools.items()}
    )
    tools.server = server
    tools.ctx = ctx
    tools.client = client
    return tools


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


def problem(status: int, detail: str, code: str, **extra: Any) -> ProblemError:
    """The exception the real client raises for any non-2xx.

    ``extra`` is everything the problem+json body carried outside the RFC 7807
    keys — the actionable payload the tool layer exists to surface.
    """
    return ProblemError(status=status, detail=detail, code=code, extra=dict(extra))


def sheet_selection_required(sheets: Sequence[str]) -> ProblemError:
    """The service's real multi-sheet rejection (``app/shared/datasets.py``)."""
    return problem(
        400,
        f"This dataset version has {len(sheets)} sheets — name one via the 'sheet' parameter",
        "sheet-selection-required",
        sheets=list(sheets),
    )


def unknown_column(name: str, available: Sequence[str]) -> ProblemError:
    """The service's real column rejection (``app/shared/query/validate.py``)."""
    return problem(
        400,
        f"Unknown column: '{name}'",
        "unknown-column",
        column=name,
        available=list(available),
    )


# ---------------------------------------------------------------------------
# Response builders — each dumped from the service's own response model
# ---------------------------------------------------------------------------


def _dump(model: Any) -> dict[str, Any]:
    return model.model_dump(mode="json")


def page(
    items: Iterable[dict[str, Any]], *, total: int | None = None,
    limit: int = 50, offset: int = 0,
) -> dict[str, Any]:
    """The ``Page[T]`` envelope every list endpoint returns.

    ``total`` defaults to the number of items, which is the un-paged case.
    """
    listed = list(items)
    model: Page = Page[dict[str, Any]](
        items=listed,
        total=len(listed) if total is None else total,
        limit=limit,
        offset=offset,
    )
    return _dump(model)


def me(
    *, user_id: str = "u-1", email: str = "ada@example.com", name: str = "Ada Lovelace",
    is_superuser: bool = False, teams: Sequence[tuple[str, str]] = (("Analytics", "admin"),),
) -> dict[str, Any]:
    """``GET /auth/me``. ``teams`` is (team_name, role) pairs."""
    return _dump(
        MeResponse(
            user=UserOut(
                id=user_id, email=email, name=name, is_superuser=is_superuser,
                created_at="2026-01-01T00:00:00Z",
            ),
            memberships=[
                MembershipOut(team_id=f"t-{i}", team_name=team_name, role=role)  # type: ignore[arg-type]
                for i, (team_name, role) in enumerate(teams)
            ],
        )
    )


def dataset(
    *, id: str = "ds-1", name: str = "Orders", domain: str | None = "sales",
    current_version: int | None = 3, row_count: int | None = 1000,
    validation_status: str | None = "passed", documentation: str | None = "full",
    **extra: Any,
) -> dict[str, Any]:
    """One item of ``Page[DatasetInfo]`` from ``GET /datasets``."""
    return _dump(
        DatasetInfo(
            id=id, name=name, domain=domain, current_version=current_version,
            row_count=row_count, validation_status=validation_status,
            documentation=documentation,
            created_at="2026-01-01T00:00:00Z", updated_at="2026-01-02T00:00:00Z",
            **extra,
        )
    )


def version(
    *, version_number: int = 1, status: str = "ready", row_count: int | None = 1000,
    sheet_count: int | None = 1, tags: Sequence[str] = (), **extra: Any,
) -> dict[str, Any]:
    """One item of ``Page[VersionInfo]`` from ``GET /datasets/{id}/versions``.

    That list is newest-first, and ``resolve_version`` takes the first ``ready``
    entry — so order matters when scripting several.
    """
    return _dump(
        VersionInfo(
            id=f"v-{version_number}", version_number=version_number, status=status,
            row_count=row_count, sheet_count=sheet_count, tags=list(tags),
            created_at="2026-01-01T00:00:00Z", **extra,
        )
    )


def sheet_column(
    name: str, dtype: str = "string", *, position: int = 0,
    normalized_name: str | None = None, **extra: Any,
) -> dict[str, Any]:
    """One entry of ``SheetMetadataResponse.columns``."""
    return _dump(
        SheetColumn(
            name=name, normalized_name=normalized_name or name, dtype=dtype,
            position=position, **extra,
        )
    )


def sheet(
    *, name: str = "Sheet1", sheet_key: str | None = "sheet1", row_count: int = 100,
    columns: Sequence[dict[str, Any]] = (), status: str = "ready", **extra: Any,
) -> dict[str, Any]:
    """One item of ``Page[SheetMetadataResponse]`` from ``GET /datasets/{id}/sheets``.

    ``column_count`` follows ``columns`` unless overridden, so a test cannot
    accidentally assert a count the column list contradicts.
    """
    listed = [SheetColumn(**c) for c in columns]
    extra.setdefault("column_count", len(listed))
    return _dump(
        SheetMetadataResponse(
            name=name, sheet_key=sheet_key, row_count=row_count, status=status,
            columns=listed, **extra,
        )
    )


def query_page(
    items: Iterable[dict[str, Any]], *, total: int | None = None,
    next_cursor: str | None = None, masked_columns: Sequence[str] = (),
) -> dict[str, Any]:
    """``POST .../query``. Cursor-paged — NOT the ``Page[T]`` envelope."""
    return _dump(
        QueryPage(
            items=list(items), total=total, next_cursor=next_cursor,
            masked_columns=list(masked_columns),
        )
    )


def sql_result(
    items: Iterable[dict[str, Any]], *, columns: Sequence[str] | None = None,
    truncated: bool = False, tables: Sequence[str] = ("sheet1",),
    result_file: str = "query_output_1.parquet", row_count: int | None = None,
) -> dict[str, Any]:
    """``POST .../sql``. ``columns`` defaults to the keys of the first row."""
    listed = list(items)
    if columns is None:
        columns = list(listed[0]) if listed else []
    return _dump(
        SqlQueryResponse(
            columns=list(columns), items=listed,
            row_count=len(listed) if row_count is None else row_count,
            truncated=truncated, tables=list(tables), result_file=result_file,
        )
    )


def health(
    dimensions: dict[str, tuple[str, str]], *, dataset_id: str = "ds-1",
    current_version_number: int | None = 3,
) -> dict[str, Any]:
    """``GET /datasets/{id}/health``. ``dimensions`` maps name -> (status, summary)."""
    return _dump(
        DatasetHealthResponse(
            dataset_id=dataset_id,
            current_version_number=current_version_number,
            dimensions={
                name: HealthDimension(status=status, summary=summary)
                for name, (status, summary) in dimensions.items()
            },
        )
    )


def column_hit(
    *, dataset_id: str = "ds-1", dataset_name: str = "Orders", sheet_name: str = "Sheet1",
    sheet_key: str = "sheet1", column_name: str = "customer_id", dtype: str = "string",
    **extra: Any,
) -> dict[str, Any]:
    """One item of ``Page[ColumnHit]`` from ``GET /search/columns``."""
    extra.setdefault("normalized_name", column_name)
    return _dump(
        ColumnHit(
            dataset_id=dataset_id, dataset_name=dataset_name, sheet_name=sheet_name,
            sheet_key=sheet_key, column_name=column_name, dtype=dtype, **extra,
        )
    )


def sheet_metadata(
    *, sheet_key: str = "sheet1", grain: str | None = None,
    primary_key_columns: Sequence[str] | None = None, description: str | None = None,
    dataset_id: str = "ds-1",
) -> dict[str, Any]:
    """One item of ``GET /datasets/{id}/sheet-metadata`` (``SheetMetadataOut``)."""
    return _dump(
        SheetMetadataOut(
            id=f"sm-{sheet_key}", dataset_id=dataset_id, sheet_key=sheet_key,
            grain=grain,
            primary_key_columns=list(primary_key_columns) if primary_key_columns else None,
            description=description, updated_at="2026-01-01T00:00:00Z",
        )
    )


def column_metadata(
    column_name: str, *, business_name: str | None = None, description: str | None = None,
    semantic_type: str | None = None, unit: str | None = None,
    sensitivity: str | None = None, allowed_values: Sequence[Any] | None = None,
    sheet_key: str = "sheet1", dataset_id: str = "ds-1",
) -> dict[str, Any]:
    """One item of ``GET .../sheet-metadata/{sheet_key}/columns`` (``ColumnMetadataOut``)."""
    return _dump(
        ColumnMetadataOut(
            id=f"cm-{column_name}", dataset_id=dataset_id, sheet_key=sheet_key,
            logical_sheet_id=f"ls-{sheet_key}", column_name=column_name,
            business_name=business_name, description=description,
            semantic_type=semantic_type, unit=unit, sensitivity=sensitivity,
            allowed_values=list(allowed_values) if allowed_values is not None else None,
            updated_at="2026-01-01T00:00:00Z",
        )
    )


# A tool function, for annotating fixtures in the tests that use this module.
Tool = Callable[..., Awaitable[str]]

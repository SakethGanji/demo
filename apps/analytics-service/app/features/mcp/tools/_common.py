"""Shared plumbing for the tool modules: error translation, sheet/version
resolution, and response size limits.

Request *validation* deliberately does not live here any more. When these tools
ran as a separate process they mirrored the service's filter grammar and sort
vocabulary client-side, because the service used to coerce a malformed filter
into an empty group and an unrecognised sort order into its default — both of
which return confidently wrong data. The service now rejects those inputs, so
the mirrors were deleted rather than left to drift; see ``explain``'s
``unknown-operator`` / ``invalid-filter`` / 422 branches, which turn the
service's own rejection into the same actionable guidance.

One exception survives, ``require_sort_order``, and only for the one tool that
sorts in this process rather than asking the service to; its docstring says why.
"""

from __future__ import annotations

import functools
from dataclasses import dataclass
from typing import Any, Awaitable, Callable
from urllib.parse import quote

from mcp.server.mcpserver.exceptions import ToolError

from ..client import AnalyticsClient, ProblemError

SORT_ORDERS = ("asc", "desc")

MAX_RESPONSE_CHARS = 60_000
"""Ceiling on a single tool response, enforced for every tool by :func:`guard`.

A wide ``SELECT`` can otherwise emit tens of MB, and so can a 1000-row
``query_rows`` over a sheet of long text columns, a 1000-row pivot, or a
``list_artifacts`` page. Several tools clamp themselves with a hint specific to
what they read (lower ``depth``, project fewer columns); this is the backstop
for the ones that do not, so the ceiling is a property of the tool surface
rather than of the tools that remembered.
"""

TRUNCATION_MARK = "[response truncated at"
"""How :func:`clamp` starts its marker — used to detect an already-clamped body."""


@dataclass
class Ctx:
    """What every tool closure captures.

    Notably *not* an identity. The client attaches the acting user per request
    from :mod:`app.features.mcp.identity`, so one ``Ctx`` shared by every
    closure still serves each caller as themselves.
    """

    client: AnalyticsClient


def require_sort_order(value: str | None, *, default: str = "asc") -> str:
    """Reject anything that is not exactly ``asc`` or ``desc``.

    Needed in the one place the service cannot reject it for us:
    ``list_saved_objects`` sorts in this process, so there is no service call
    to do the rejecting.

    It used to guard ``read_artifact`` too, because
    ``GET /samples/{filename}/data`` typed ``sort_order`` as a bare ``str`` and
    fell back to ascending on anything it did not recognise. That route is now
    ``Literal["asc", "desc"]`` like ``/aggregate`` and ``/pivot``, so the mirror
    was dropped there rather than left to drift.

    Deliberately does *not* lowercase. The aggregate and pivot request models
    are ``Literal["asc", "desc"]``, so ``"ASC"`` is a 422 there; accepting
    it here would give one parameter name two contracts across one tool surface,
    and quietly rewriting a caller's input is the same class of behaviour this
    guard exists to prevent.
    """
    if value is None:
        return default
    if value not in SORT_ORDERS:
        raise ToolError(
            f"sort_order must be 'asc' or 'desc' (lowercase), got {value!r}. "
            "Anything else would silently reverse the result."
        )
    return value


def clamp(text: str, budget: int, *, hint: str = "") -> str:
    """Hard ceiling on a single tool response.

    The marker is assembled before the closing bracket is added, so a call with
    no ``hint`` ends ``characters.]`` rather than ``characters. ]``. Stripping
    the finished string instead would be a no-op: adjacent string literals are
    concatenated at parse time, so a trailing ``.rstrip()`` binds to a value
    that always ends in ``]``.
    """
    if len(text) <= budget:
        return text
    kept = text[:budget]
    marker = f"response truncated at {budget:,} characters."
    if hint:
        marker = f"{marker} {hint}"
    return f"{kept}\n\n[{marker}]"


def enforce_ceiling(text: str) -> str:
    """Apply :data:`MAX_RESPONSE_CHARS` to a finished tool response.

    Skips a body that already carries a :func:`clamp` marker. A tool that
    clamped itself deliberately rides a few hundred characters over the budget —
    ``run_sql`` and ``read_artifact`` hoist their artifact handle and paging hint
    outside the clamp precisely so a truncation cannot delete the instructions
    for recovering from it — and re-clamping at the same budget would cut off
    exactly those, plus the marker explaining the cut.
    """
    if TRUNCATION_MARK in text:
        return text
    return clamp(
        text,
        MAX_RESPONSE_CHARS,
        hint="Narrow the request: fewer columns, a smaller limit, or a filter.",
    )


def guard(fn: Callable[..., Awaitable[str]]) -> Callable[..., Awaitable[str]]:
    """Translate analytics-service problems into actionable tool errors, and
    hold every response to the size ceiling.

    The ceiling lives here rather than in each tool because it is a property of
    the transport, not of any one query: an oversized response is a context
    window spent before the model reads a word of it, and the tools that forgot
    to clamp (``query_rows``, ``aggregate``, ``pivot``, ``list_artifacts`` and
    every orient tool) are exactly the ones nobody thought could get large.
    """

    @functools.wraps(fn)
    async def wrapper(*args: Any, **kwargs: Any) -> str:
        try:
            return enforce_ceiling(await fn(*args, **kwargs))
        except ProblemError as exc:
            raise ToolError(explain(exc)) from exc

    return wrapper


def _listed(values: Any, cap: int = 40) -> str:
    if not isinstance(values, list) or not values:
        return ""
    shown = ", ".join(str(v) for v in values[:cap])
    return f"{shown} …" if len(values) > cap else shown


def explain(exc: ProblemError) -> str:
    """Turn a problem+json response into guidance the model can act on."""
    code = exc.code

    if code == "sheet-selection-required":
        # The service's detail already explains the rule; only add the names.
        names = ", ".join(exc.sheets) or "unknown"
        return f"{exc.detail} Available sheets: {names}."

    if code == "unknown-column":
        available = ", ".join(exc.available_columns[:40])
        suffix = " …" if len(exc.available_columns) > 40 else ""
        return (
            f"{exc.detail} Available columns: {available}{suffix}. "
            "Call describe_dataset to see the full schema."
        )

    if code == "unknown-operator":
        # The service names the offending operator, the column it was on, and
        # the whole valid vocabulary — a better answer than a client-side copy
        # of the grammar could give, and one that cannot fall out of date.
        operators = _listed(exc.extra.get("available"), cap=100)
        return (
            f"{exc.detail} Valid filter operators: {operators}. "
            "A filter group is {'logic': 'and'|'or', 'conditions': [...]}; a condition "
            "is {'column': ..., 'op': ..., 'value': ...}."
        )

    if code == "invalid-filter":
        keys = _listed(exc.extra.get("keys"))
        got = f" Keys sent: {keys}." if keys else ""
        return (
            f"{exc.detail}{got} A filter node is either a group "
            "({'logic': 'and'|'or', 'conditions': [...]}) or a condition "
            "({'column': ..., 'op': ..., 'value': ...}) — never both, and never a "
            "bare scalar."
        )

    if code == "operator-type-mismatch":
        return (
            f"{exc.detail} The operator does not apply to this column's type "
            f"({exc.extra.get('dtype', 'unknown')}). String operators need a text column, "
            "date operators need a date/timestamp column."
        )

    if code in {"select-only", "invalid-sql"}:
        return (
            f"{exc.detail} run_sql accepts exactly one SELECT statement — no INSERT, "
            "UPDATE, COPY, multiple statements, or trailing semicolon. "
            "A WITH ... SELECT common table expression is fine."
        )

    if code == "sql-timeout":
        return (
            f"{exc.detail} The query exceeded the 30s limit. Add a filter, aggregate "
            "rather than scanning, or narrow the columns selected."
        )

    if code == "version-too-large-for-sql":
        return (
            f"{exc.detail} This version is too large to materialise for ad-hoc SQL. "
            "Use query_rows (server-side filter and pagination) or aggregate instead."
        )

    if code == "too-many-pivot-columns":
        return (
            f"{exc.detail} The pivot column has too many distinct values "
            f"(limit {exc.extra.get('limit', 200)}). Filter it down first, or bucket it "
            "with date_trunc / bin_count."
        )

    if code == "sheet-not-in-version":
        return f"{exc.detail} That sheet does not exist in this version of the dataset."

    if exc.status == 401:
        return (
            f"{exc.detail} The X-User-Id this MCP session presented does not name an "
            "active analytics-service user."
        )

    if exc.status == 403:
        return (
            f"{exc.detail} You are a member of the owning team but lack the required "
            "permission for this action."
        )

    if exc.status == 404:
        return (
            f"{exc.detail} Note: the analytics service returns 404 both for things that "
            "do not exist and for things owned by a team you are not in — so this does "
            "not prove the resource is absent."
        )

    if exc.status == 422:
        # Request-shape rejections (a Literal violated, a filter operand of the
        # wrong arity) arrive as a pydantic error array. Unrendered it reads
        # "Request validation failed", which tells a model nothing.
        problems = []
        for item in exc.extra.get("errors") or []:
            if not isinstance(item, dict):
                continue
            where = ".".join(str(p) for p in (item.get("loc") or []) if p != "body")
            problems.append(f"{where or 'request'}: {item.get('msg') or 'invalid'}")
        if problems:
            return f"{exc.detail}. " + " ".join(problems)
        return exc.detail

    if exc.status == 400 and code == "bad_request":
        return exc.detail

    return f"{exc.detail} (code: {code})"


async def resolve_version(ctx: Ctx, dataset_id: str, version: int | None) -> int:
    """Default to the newest ready version when none is given.

    The version list is returned newest-first, so the first `ready` entry is the
    latest readable one. Pass `version` explicitly to pin an older one.
    """
    if version is not None:
        return version
    page = await ctx.client.get(f"/datasets/{dataset_id}/versions")
    for item in page_items(page):
        if item.get("status") == "ready":
            return int(item["version_number"])
    raise ToolError(
        "This dataset has no ready version to read — it may still be processing, "
        "or the upload may have failed."
    )


def seg(value: Any) -> str:
    """Percent-encode one free-text path segment.

    Names that come from a workbook — sheet titles, column headers — and names
    a user typed — tag names — are interpolated into REST paths. httpx does not
    escape path-structural characters, so a sheet literally named ``Sheet #1``
    (legal in Excel) truncates the URL at the ``#``: everything after it,
    including the ``/query`` suffix, is parsed as a fragment and dropped, and
    the call comes back as a bare 404 that reads like the dataset is missing.
    ``/`` and ``?`` fail the same way.

    ``safe=""`` so ``/`` is escaped too; encoding is a no-op for the ordinary
    names (``orders``, ``customer_id``) that make up almost every real call.
    Deliberately not applied to numeric versions or UUIDs, which cannot contain
    anything that needs escaping.
    """
    return quote(str(value), safe="")


def sheet_path(dataset_id: str, version: int, sheet: str | None, suffix: str) -> str:
    """Build the sheet-scoped path when a sheet is named, else the auto-resolving one.

    ``suffix`` is built by the caller and may legitimately carry a ``/`` (e.g.
    ``columns/{column}``), so it is not encoded here — a caller interpolating a
    name into it encodes that name with :func:`seg` itself.
    """
    base = f"/datasets/{dataset_id}/versions/{version}"
    if sheet:
        return f"{base}/sheets/{seg(sheet)}/{suffix}"
    return f"{base}/{suffix}"


def page_items(page: Any) -> list[dict[str, Any]]:
    if isinstance(page, dict):
        items = page.get("items")
        if isinstance(items, list):
            return items
    return []

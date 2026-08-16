"""Tools that return actual data rows — always bounded, never the whole dataset."""

from __future__ import annotations

from typing import Annotated, Any

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import Field

from .. import render
from ..client import ProblemError
from ._common import (
    MAX_RESPONSE_CHARS,
    Ctx,
    clamp,
    explain,
    guard,
    page_items,
    resolve_version,
    sheet_path,
)


async def _table_hint(ctx: Ctx, dataset_id: str, version: int) -> str:
    """List the real table names so a failed query can be corrected in one step.

    ``GET /datasets/{id}/sheets`` resolves the dataset's *current* version and
    lists that version's sheets — there is no version-scoped listing route. The
    query that just failed ran against ``version``, which is the newest **ready**
    version by default and any pinned number when one was passed, and neither is
    necessarily the current one (a tag rollback moves the current pointer
    backwards). Saying "in this version" about a list read from a different
    version is how a model concludes a table does not exist, or spells the table
    it is about to query from a schema the query will not see. So the names are
    offered with the version they actually came from.
    """
    try:
        page = await ctx.client.get(f"/datasets/{dataset_id}/sheets")
    except ProblemError:
        return ""
    names = [str(s.get("sheet_key")) for s in page_items(page) if s.get("sheet_key")]
    if not names:
        return ""
    return (
        f"Queryable table names in the dataset's current version: {', '.join(names)}. "
        f"This query ran against version {version}; if that is not the current "
        "version its sheets may differ — call describe_dataset to confirm. "
        "Table names are sheet keys, not the dataset or file name."
    )


# Re-exported: the ceiling used to be declared here and is imported by name in
# several places. It lives in `_common` now so `guard` can apply it to every
# tool, and so there is one number rather than two that happen to be equal.
__all__ = ["FILTER_HELP", "MAX_RESPONSE_CHARS", "register"]

FILTER_HELP = """\
Recursive filter tree. A group is {"logic": "and"|"or", "conditions": [...]}, and each
condition is {"column": str, "op": str, "value": any}. Groups may nest inside conditions.
Operators: is_null, is_not_null, is_empty, is_not_empty, is_duplicate, is_unique (no value);
eq, neq, gt, gte, lt, lte (scalar); in, not_in (non-empty list); between, not_between,
len_between, date_between (exactly [low, high]); contains, icontains, not_contains,
starts_with, ends_with, regex (text columns); len_eq, len_gt, len_gte, len_lt, len_lte;
top_n, bottom_n (scalar N); top_pct, bottom_pct (fraction 0-1); date_before, date_after,
last_n_days (date/timestamp columns).
Example: {"logic":"and","conditions":[{"column":"region","op":"in","value":["US","CA"]},
{"column":"amount","op":"gt","value":100}]}"""


def _query_page(payload: dict[str, Any]) -> tuple[list[dict[str, Any]], str]:
    rows = payload.get("items") or []
    notes = []
    masked = payload.get("masked_columns") or []
    if masked:
        notes.append(
            f"The service withheld these columns from you: {', '.join(masked)}."
        )
    if payload.get("next_cursor"):
        notes.append(
            f"More rows available — pass cursor='{payload['next_cursor']}' to query_rows "
            "with the same spec to continue."
        )
    return rows, " ".join(notes)


def register(server: MCPServer, ctx: Ctx) -> None:
    @server.tool(
        name="query_rows",
        description=(
            "Look at rows from ONE sheet: filter, sort, project and page, all "
            "server-side. With just a limit it is a plain preview; add filters to "
            "narrow it. Returns only the rows you ask for. Cannot join across sheets "
            "and cannot compute expressions — use run_sql for either. Use this rather "
            "than pulling a dataset in full."
        ),
    )
    @guard
    async def query_rows(
        dataset_id: Annotated[str, Field(description="Dataset UUID.")],
        sheet: Annotated[
            str | None,
            Field(description="Sheet name or key. Required when the version has multiple sheets."),
        ] = None,
        version: Annotated[
            int | None, Field(description="Version number. Defaults to the newest ready version.")
        ] = None,
        columns: Annotated[
            list[str] | None,
            Field(description="Columns to return. Omit for all — naming them saves tokens."),
        ] = None,
        filters: Annotated[dict[str, Any] | None, Field(description=FILTER_HELP)] = None,
        search: Annotated[
            str | None,
            Field(description="Case-insensitive substring matched across all text columns."),
        ] = None,
        sort: Annotated[
            list[dict[str, Any]] | None,
            Field(description='Sort order, e.g. [{"column": "amount", "direction": "desc"}].'),
        ] = None,
        limit: Annotated[int, Field(description="Rows to return (1-1000).", ge=1, le=1000)] = 50,
        cursor: Annotated[
            str | None,
            Field(description="Opaque cursor from a previous call. The rest of the spec must match."),
        ] = None,
    ) -> str:
        resolved = await resolve_version(ctx, dataset_id, version)
        body: dict[str, Any] = {"limit": limit}
        if columns:
            body["columns"] = columns
        if filters:
            body["filters"] = filters
        if search:
            body["search"] = search
        if sort:
            body["sort"] = sort
        if cursor:
            body["cursor"] = cursor
        payload = await ctx.client.post(sheet_path(dataset_id, resolved, sheet, "query"), body)
        rows, notes = _query_page(payload)
        return render.join(
            render.fields([("dataset", dataset_id), ("version", resolved), ("sheet", sheet)]),
            render.table(rows, columns),
            render.count_note(len(rows), payload.get("total")),
            notes,
        )

    @server.tool(
        name="run_sql",
        description=(
            "Run one read-only SELECT against a dataset version in a sandbox, and get "
            "back a bounded result plus a handle to the full output. This is the most "
            "capable analysis tool here: joins, aggregates, window functions, CTEs. "
            "Each ready sheet is a table named by its sheet_key — call describe_dataset "
            "first to learn the table and column names. Prefer computing here over "
            "reading rows and reasoning over them."
        ),
    )
    @guard
    async def run_sql(
        dataset_id: Annotated[str, Field(description="Dataset UUID.")],
        sql: Annotated[
            str,
            Field(
                description=(
                    "A single SELECT statement, no trailing semicolon. WITH ... SELECT is "
                    "allowed. Anything that writes is rejected."
                ),
                min_length=1,
            ),
        ],
        version: Annotated[
            int | None, Field(description="Version number. Defaults to the newest ready version.")
        ] = None,
        max_rows: Annotated[
            int,
            Field(description="Rows to show in the response (1-500). The full result is kept as an artifact.", ge=1, le=500),
        ] = 50,
    ) -> str:
        resolved = await resolve_version(ctx, dataset_id, version)
        try:
            payload = await ctx.client.post(
                f"/datasets/{dataset_id}/versions/{resolved}/sql", {"sql": sql}
            )
        except ProblemError as exc:
            # A catalog error usually means the model guessed the table name.
            # The names are cheap to fetch, so answer the question it just failed.
            if exc.code == "sql-error":
                raise ToolError(
                    f"{explain(exc)} {await _table_hint(ctx, dataset_id, resolved)}"
                ) from exc
            raise
        rows = payload.get("items") or []
        shown = rows[:max_rows]

        notes = []
        if payload.get("truncated"):
            notes.append(
                "The service capped this result at 10,000 rows — aggregate or filter "
                "further if you need completeness."
            )
        if len(rows) > len(shown):
            notes.append(f"Showing {len(shown)} of {len(rows)} returned rows.")
        # Always surface the handle: read_artifact needs it, and it is the only way
        # to chain a result that is not fully shown.
        if payload.get("result_file"):
            notes.append(f"Artifact: {payload['result_file']} (read with read_artifact).")

        # Clamp the table, never the notes. `clamp` keeps the head and drops the
        # tail, and the notes are the tail — so joining first would delete the
        # artifact handle, the "showing N of M" count and the 10,000-row cap
        # warning at exactly the moment the truncation hint tells the caller to
        # go read that artifact. The notes ride outside the budget rather than
        # shrinking it, so the marker still names the real ceiling; they are a
        # fixed few hundred characters (one filename and two fixed sentences),
        # which is the overshoot MAX_RESPONSE_CHARS tolerates.
        note_text = " ".join(n for n in notes if n)
        body = render.join(
            render.fields(
                [
                    ("version", resolved),
                    ("tables", ", ".join(payload.get("tables") or [])),
                    ("row_count", payload.get("row_count")),
                ]
            ),
            render.table(shown, payload.get("columns")),
        )
        return render.join(
            clamp(
                body,
                MAX_RESPONSE_CHARS,
                hint="Project fewer columns, or lower max_rows, then read the artifact.",
            ),
            note_text,
        )

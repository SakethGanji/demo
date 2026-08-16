"""Compute tools — the service does the arithmetic, you get a bounded summary.

DuckDB computes; the model orchestrates. A SUM over 10k rows is exact here and a
coin flip if the model does it in context.
"""

from __future__ import annotations

from typing import Annotated, Any

from mcp.server.mcpserver import MCPServer
from pydantic import Field

from app.shared.constants import AGG_FUNCTIONS_TEXT

from .. import render
from ._common import (
    Ctx,
    guard,
    page_items,
    resolve_version,
    seg,
    sheet_path,
)

# Derived, not restated. A hand-written copy here once listed the functions
# independently of the validator; a tool description that advertises a function
# the service rejects costs a model a whole round trip to discover.
AGGREGATIONS_HELP = (
    'List of aggregations, e.g. [{"column": "amount", "function": "sum", "alias": "total"}]. '
    f"Allowed functions: {AGG_FUNCTIONS_TEXT}. `alias` is optional and defaults to "
    "'{column}_{function}'."
)

GROUP_BY_HELP = (
    'Columns to group by. Each entry is either a column name, or a bucket object such as '
    '{"column": "created_at", "date_trunc": "month"} or {"column": "amount", "bin_count": 10}. '
    "A bucket takes exactly one of date_trunc, bin_width or bin_count."
)


def register(server: MCPServer, ctx: Ctx) -> None:
    @server.tool(
        name="profile_column",
        description=(
            "Statistics for one column: null rate, cardinality, quantiles, a bounded "
            "histogram, most and least common values, and a few example values. Bounded "
            "by construction — prefer this over profiling a whole sheet."
        ),
    )
    @guard
    async def profile_column(
        dataset_id: Annotated[str, Field(description="Dataset UUID.")],
        column: Annotated[str, Field(description="Column name (normalized name preferred).")],
        sheet: Annotated[
            str | None,
            Field(description="Sheet name or key. Required when the version has multiple sheets."),
        ] = None,
        version: Annotated[
            int | None, Field(description="Version number. Defaults to the newest ready version.")
        ] = None,
    ) -> str:
        resolved = await resolve_version(ctx, dataset_id, version)
        payload = await ctx.client.get(
            sheet_path(dataset_id, resolved, sheet, f"columns/{seg(column)}")
        )
        top = [
            {"value": v.get("value"), "count": v.get("count"), "pct": v.get("percent")}
            for v in payload.get("top_values") or []
        ]
        rare = [
            {"value": v.get("value"), "count": v.get("count"), "pct": v.get("percent")}
            for v in payload.get("rare_values") or []
        ]
        # Below the top_values cap, "least common" is just "most common" reversed.
        distinct = payload.get("unique_count")
        if isinstance(distinct, int) and distinct <= len(top):
            rare = []

        # `count` is the sheet's total row count and is documented as such;
        # `non_null_count` is COUNT(column). It is nullable on the schema so
        # profiles stored before the field existed still deserialize, hence the
        # fallback rather than a bare read.
        total_rows = payload.get("count")
        nulls = payload.get("null_count")
        non_null = payload.get("non_null_count")
        if non_null is None and isinstance(total_rows, int) and isinstance(nulls, int):
            non_null = total_rows - nulls
        histogram = [
            {"from": b.get("bin_start"), "to": b.get("bin_end"), "count": b.get("count")}
            for b in payload.get("histogram") or []
        ]
        return render.join(
            render.fields(
                [
                    ("column", payload.get("name")),
                    ("sheet", payload.get("sheet_name")),
                    ("dtype", payload.get("dtype")),
                    ("rows", total_rows),
                    ("non_null", non_null),
                    ("nulls", nulls),
                    ("null_pct", payload.get("null_percent")),
                    ("distinct", payload.get("unique_count")),
                    ("uniqueness", payload.get("uniqueness")),
                    ("candidate_key", payload.get("is_candidate_key")),
                    ("min", payload.get("min")),
                    ("max", payload.get("max")),
                    ("mean", payload.get("mean")),
                    ("median", payload.get("median")),
                    ("std", payload.get("std")),
                    ("q25", payload.get("q25")),
                    ("q75", payload.get("q75")),
                    ("min_date", payload.get("min_date")),
                    ("max_date", payload.get("max_date")),
                    ("examples", ", ".join(str(e) for e in payload.get("examples") or [])),
                ]
            ),
            render.section("Most common", render.table(top)) if top else "",
            render.section("Least common", render.table(rare)) if rare else "",
            render.section("Histogram", render.table(histogram)) if histogram else "",
        )

    @server.tool(
        name="aggregate",
        description=(
            "Group and aggregate ONE sheet by existing column names — 'how many', "
            "'total by', 'average per'. Returns the grouped result (bounded) plus a "
            "handle to the full output. Use it instead of reading rows and adding them "
            "up yourself. LIMITS: single sheet only (no joins), and it aggregates plain "
            "columns only — it cannot compute an expression such as quantity*price, and "
            "cannot group by a column that lives in another sheet. For either of those, "
            "use run_sql."
        ),
    )
    @guard
    async def aggregate(
        dataset_id: Annotated[str, Field(description="Dataset UUID.")],
        group_by: Annotated[list[Any], Field(description=GROUP_BY_HELP)],
        aggregations: Annotated[list[dict[str, Any]], Field(description=AGGREGATIONS_HELP)],
        sheet: Annotated[
            str | None,
            Field(description="Sheet name or key. Required when the version has multiple sheets."),
        ] = None,
        version: Annotated[
            int | None, Field(description="Version number. Defaults to the newest ready version.")
        ] = None,
        filters: Annotated[
            dict[str, Any] | None,
            Field(description="Filter tree applied before grouping — same shape as query_rows."),
        ] = None,
        having: Annotated[
            list[dict[str, Any]] | None,
            Field(description='Post-aggregation filters, e.g. [{"column": "total", "op": "gt", "value": 100}].'),
        ] = None,
        sort_by: Annotated[
            str | None, Field(description="Output column or aggregation alias to sort by.")
        ] = None,
        sort_order: Annotated[
            str, Field(description="'asc' or 'desc', lowercase.")
        ] = "desc",
        limit: Annotated[int, Field(description="Max groups to return (1-1000).", ge=1, le=1000)] = 50,
    ) -> str:
        resolved = await resolve_version(ctx, dataset_id, version)
        payload = await ctx.client.post(
            "/aggregate",
            {
                "dataset_id": dataset_id,
                "version_number": resolved,
                "sheet": sheet,
                "group_by": group_by,
                "aggregations": aggregations,
                "having": having,
                "filters": filters,
                "sort_by": sort_by,
                "sort_order": sort_order,
                "limit": limit,
                "return_data": True,
            },
        )
        rows = payload.get("data") or []
        notes = []
        if payload.get("truncated"):
            notes.append("Result was truncated by the service's aggregation row cap.")
        if payload.get("result_file"):
            notes.append(
                f"Full result saved as artifact '{payload['result_file']}' — read it with read_artifact."
            )

        # `totals` is now a true grand total: the service recomputes it over
        # every group the filter matched, not the page it returned, and drops
        # the non-additive aggregations itself — reporting each in
        # `totals_omitted` as alias -> reason. The old label here said "summed
        # across the groups shown", which was wrong on both counts.
        totals = payload.get("totals") or {}
        omitted = payload.get("totals_omitted") or {}
        totals_block = ""
        if totals:
            totals_block = render.section(
                "Totals (over all groups, not just the rows shown)",
                render.fields(list(totals.items())),
            )
        if omitted:
            reasons = "; ".join(f"{alias} ({reason})" for alias, reason in sorted(omitted.items()))
            notes.append(
                f"No overall total for {reasons}. Summing per-group averages, maxima or "
                "distinct counts describes nothing; use run_sql for a true overall value."
            )

        return render.join(
            render.fields(
                [
                    ("rows_scanned", payload.get("original_count")),
                    ("groups", payload.get("group_count")),
                ]
            ),
            render.table(rows, payload.get("columns")),
            totals_block,
            " ".join(notes),
        )

    @server.tool(
        name="pivot",
        description=(
            "Cross-tabulate ONE sheet: row dimensions against one pivot dimension, with "
            "aggregated values and optional percentage displays. Returns a bounded "
            "table plus a handle to the full output. Same limits as aggregate — single "
            "sheet, plain column names only, no joins or computed expressions; use "
            "run_sql for those."
        ),
    )
    @guard
    async def pivot(
        dataset_id: Annotated[str, Field(description="Dataset UUID.")],
        rows: Annotated[list[Any], Field(description=f"Row dimensions. {GROUP_BY_HELP}", min_length=1)],
        values: Annotated[
            list[dict[str, Any]],
            Field(
                description=(
                    f"{AGGREGATIONS_HELP} Each entry may also set "
                    '"display": "value" | "pct_of_row" | "pct_of_column" | "pct_of_grand_total".'
                ),
                min_length=1,
            ),
        ],
        columns: Annotated[
            Any | None,
            Field(description="The pivot dimension: a column name or a bucket object. Max 200 distinct values."),
        ] = None,
        sheet: Annotated[str | None, Field(description="Sheet name or key.")] = None,
        version: Annotated[int | None, Field(description="Version number.")] = None,
        filters: Annotated[
            dict[str, Any] | None, Field(description="Filter tree — same shape as query_rows.")
        ] = None,
        include_row_totals: Annotated[bool, Field(description="Add per-row total columns.")] = False,
        include_column_totals: Annotated[bool, Field(description="Compute per-column totals.")] = False,
        limit: Annotated[int, Field(description="Max rows to return (1-1000).", ge=1, le=1000)] = 50,
    ) -> str:
        resolved = await resolve_version(ctx, dataset_id, version)
        payload = await ctx.client.post(
            "/pivot",
            {
                "dataset_id": dataset_id,
                "version_number": resolved,
                "sheet": sheet,
                "rows": rows,
                "columns": columns,
                "values": values,
                "filters": filters,
                "include_row_totals": include_row_totals,
                "include_column_totals": include_column_totals,
                "limit": limit,
                "return_data": True,
            },
        )
        data = payload.get("data") or []
        notes = []
        if payload.get("truncated"):
            notes.append("Result was truncated by the service's row cap.")
        if payload.get("result_file"):
            notes.append(f"Full result saved as artifact '{payload['result_file']}'.")

        # Both totals maps get a heading, for the same reason aggregate's does.
        # `totals` is re-aggregated by the service over every row the filter
        # matched, not over the page returned, so rendering it as bare
        # `alias: value` lines directly under a capped table reads as a subtotal
        # of the rows shown — the one reading that is certainly wrong.
        totals_block = ""
        if payload.get("totals"):
            totals_block = render.section(
                "Totals (over all groups, not just the rows shown)",
                render.fields(list(payload["totals"].items())),
            )
        # `column_totals` was requested by the caller and previously never
        # rendered at all: the flag travelled to the service, the service
        # computed them, and the tool dropped them on the floor.
        column_totals = payload.get("column_totals") or {}
        column_totals_block = ""
        if column_totals:
            column_totals_block = render.section(
                "Column totals (per output column, over all rows)",
                render.fields(list(column_totals.items())),
            )
        elif include_column_totals and columns is None:
            # The service only computes column totals when there is a pivot
            # dimension to total across. Silently returning none for a flag the
            # caller set is how a model concludes the totals are all zero.
            notes.append(
                "No column totals: they are computed per pivot column, and this "
                "call has no `columns` dimension. Grand totals are shown instead."
            )

        return render.join(
            render.fields(
                [
                    ("rows_scanned", payload.get("original_count")),
                    ("rows", payload.get("row_count")),
                    ("pivot_columns", ", ".join(payload.get("pivot_columns") or [])),
                ]
            ),
            render.table(data, payload.get("columns")),
            totals_block,
            column_totals_block,
            " ".join(notes),
        )

    @server.tool(
        name="check_quality",
        description=(
            "Concrete data-quality evidence for one sheet: per-column null rates, "
            "duplicate row groups, and the most recent validation run. Computed live, so "
            "it answers even when get_dataset_health reports 'unknown' for want of a "
            "stored profile run. Two caveats: rows sharing a NULL in the grouped column "
            "count as a duplicate group, and it reports counts without naming the "
            "offending rows — use run_sql to see them."
        ),
    )
    @guard
    async def check_quality(
        dataset_id: Annotated[str, Field(description="Dataset UUID.")],
        sheet: Annotated[
            str | None,
            Field(description="Sheet name or key. Required when the version has multiple sheets."),
        ] = None,
        version: Annotated[int | None, Field(description="Version number.")] = None,
        duplicate_columns: Annotated[
            str | None,
            Field(description="Comma-separated columns to group duplicates on. Omit for whole-row duplicates."),
        ] = None,
        max_columns: Annotated[
            int, Field(description="How many worst-null columns to list.", ge=1, le=200)
        ] = 25,
    ) -> str:
        resolved = await resolve_version(ctx, dataset_id, version)

        missing = await ctx.client.get(sheet_path(dataset_id, resolved, sheet, "missing"))
        null_rows = [
            {
                "column": c.get("column"),
                "nulls": c.get("null_count"),
                "null_pct": c.get("null_percent"),
            }
            for c in (missing.get("columns") or [])[:max_columns]
        ]
        total_columns = len(missing.get("columns") or [])

        duplicates = await ctx.client.get(
            sheet_path(dataset_id, resolved, sheet, "duplicates"),
            columns=duplicate_columns,
            limit=10,
        )

        validations = await ctx.client.get(
            f"/datasets/{dataset_id}/versions/{resolved}/validations", limit=1
        )
        runs = page_items(validations)
        validation_block = "No validation run recorded for this version."
        if runs:
            run = runs[0]
            validation_block = render.fields(
                [
                    ("status", run.get("status")),
                    ("rules_total", run.get("rules_total")),
                    ("passed", run.get("rules_passed")),
                    ("failed", run.get("rules_failed")),
                    ("error_failures", run.get("error_failures")),
                    ("warning_failures", run.get("warning_failures")),
                    ("completed_at", run.get("completed_at")),
                ]
            )

        return render.join(
            render.fields(
                [
                    ("sheet", missing.get("sheet_name")),
                    ("version", resolved),
                    ("rows", missing.get("row_count")),
                    ("source", missing.get("source")),
                ]
            ),
            render.section(
                f"Missing values (worst {len(null_rows)} of {total_columns} columns)",
                render.table(null_rows),
            ),
            render.section(
                "Duplicates",
                render.fields(
                    [
                        ("grouped_on", ", ".join(duplicates.get("columns") or [])),
                        ("whole_row", duplicates.get("exact")),
                        ("duplicate_groups", duplicates.get("group_count")),
                        ("duplicate_rows", duplicates.get("duplicate_rows")),
                    ]
                ),
            ),
            render.section("Latest validation run", validation_block),
        )

    @server.tool(
        name="compare_versions",
        description=(
            "Diff two versions of a dataset: sheets added, removed or modified, column "
            "additions, type changes, and row-count deltas. Use it to explain what "
            "changed between refreshes."
        ),
    )
    @guard
    async def compare_versions(
        dataset_id: Annotated[str, Field(description="Dataset UUID.")],
        from_version: Annotated[int, Field(description="Baseline version number.")],
        to_version: Annotated[int, Field(description="Comparison version number.")],
        sheet: Annotated[
            str | None,
            Field(description="Restrict to one sheet for column-level detail. Omit for the workbook summary."),
        ] = None,
    ) -> str:
        base = f"/datasets/{dataset_id}/versions/{from_version}"
        if sheet:
            payload = await ctx.client.get(f"{base}/sheets/{seg(sheet)}/diff/{to_version}")
            type_changes = [
                {"column": c.get("column"), "from": c.get("from_dtype"), "to": c.get("to_dtype")}
                for c in payload.get("type_changes") or []
            ]
            return render.join(
                render.fields(
                    [
                        ("sheet", payload.get("to_sheet")),
                        ("identical", payload.get("identical")),
                        ("rows_before", payload.get("from_row_count")),
                        ("rows_after", payload.get("to_row_count")),
                        ("row_delta", payload.get("row_count_delta")),
                    ]
                ),
                render.section(
                    "Added columns",
                    render.bullets(c.get("name", "") for c in payload.get("added_columns") or []),
                ),
                render.section(
                    "Removed columns",
                    render.bullets(c.get("name", "") for c in payload.get("removed_columns") or []),
                ),
                render.section("Type changes", render.table(type_changes)) if type_changes else "",
            )

        payload = await ctx.client.get(f"{base}/diff/{to_version}")
        modified = [
            {
                "sheet": m.get("to_sheet"),
                "schema_changed": m.get("schema_changed"),
                "row_delta": m.get("row_count_delta"),
            }
            for m in payload.get("modified") or []
        ]
        renames = [
            {"from": r.get("from_sheet"), "to": r.get("to_sheet"), "confidence": r.get("confidence")}
            for r in payload.get("rename_candidates") or []
        ]
        return render.join(
            render.fields([("from", from_version), ("to", to_version)]),
            render.section(
                "Added sheets", render.bullets(s.get("name", "") for s in payload.get("added") or [])
            ),
            render.section(
                "Removed sheets",
                render.bullets(s.get("name", "") for s in payload.get("removed") or []),
            ),
            render.section("Modified sheets", render.table(modified)) if modified else "",
            render.section("Possible renames", render.table(renames)) if renames else "",
            render.section("Unchanged", ", ".join(payload.get("unchanged") or [])),
            "Pass `sheet` to see column-level changes for one sheet.",
        )

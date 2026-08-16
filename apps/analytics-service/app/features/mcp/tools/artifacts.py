"""Artifact tools — the chaining primitive.

Every compute endpoint writes its full result to the object store and returns a
filename. Reading that back with server-side projection, filtering and paging is
what lets you chain sample -> aggregate -> inspect without the intermediate rows
ever passing through the model.
"""

from __future__ import annotations

from typing import Annotated, Any

from mcp.server.mcpserver import MCPServer
from pydantic import Field

from .. import render
from ._common import MAX_RESPONSE_CHARS, Ctx, clamp, guard, page_items, seg


def register(server: MCPServer, ctx: Ctx) -> None:
    @server.tool(
        name="list_artifacts",
        description=(
            "List result files you can read — outputs of previous SQL queries, "
            "aggregations, pivots, samples and validation failures. Use it to find a "
            "handle from earlier work instead of recomputing."
        ),
    )
    @guard
    async def list_artifacts(
        dataset_id: Annotated[
            str | None,
            Field(description="Only artifacts derived from this dataset."),
        ] = None,
        kind: Annotated[
            str | None,
            Field(
                description=(
                    "Only this artifact type, e.g. query_output, aggregation_output, "
                    "pivot_output, sample_output, validation_failures."
                )
            ),
        ] = None,
        limit: Annotated[int, Field(description="Max artifacts to return (1-1000).", ge=1, le=1000)] = 50,
        offset: Annotated[
            int,
            Field(
                description=(
                    "Raw artifacts to skip before scanning. With dataset_id or kind set "
                    "this moves the scan window, not the list of matches — use it to "
                    "look further back than the 1000 most recent artifacts, and raise "
                    "limit (not offset) to see more matches from the window you are on."
                ),
                ge=0,
            ),
        ] = 0,
    ) -> str:
        # The API has no filter parameters here, so filter locally rather than
        # silently ignoring what the caller asked for. When filtering, scan the
        # widest page the API allows (metadata only) — a narrow scan would make
        # older matches invisible and report "none" when matches exist.
        #
        # `offset` is therefore a scan-window control, not a page cursor over the
        # matches: it is handed to /samples unchanged and skips raw artifact rows.
        # Both notes below have to describe it that way.
        filtering = bool(dataset_id or kind)
        fetch = 1000 if filtering else limit
        page = await ctx.client.get("/samples", limit=fetch, offset=offset)
        scanned = len(page_items(page))
        rows = [
            {
                "filename": item.get("filename"),
                "kind": item.get("file_type"),
                "bytes": item.get("size_bytes"),
                "dataset_id": item.get("dataset_id"),
                "created": item.get("created_at"),
            }
            for item in page_items(page)
            if (dataset_id is None or item.get("dataset_id") == dataset_id)
            and (kind is None or item.get("file_type") == kind)
        ]
        clipped = len(rows) > limit
        rows = rows[:limit]
        if not rows:
            if not filtering:
                return "No artifacts available."
            more = (
                f" Scanned the {scanned} most recent artifacts; older ones may exist "
                "beyond that window — retry with offset."
                if scanned >= fetch
                else ""
            )
            return f"No artifacts matched that filter.{more}"
        # The unfiltered listing IS the raw page, so /samples' own `total` counts
        # exactly the thing being shown and `offset` is a true page cursor over
        # it. Suppressing both — the old code passed `total=None` on every path —
        # turned "50 of 900 artifacts" into a bare "50 artifacts shown.", which is
        # how a model concludes the handle it is hunting for does not exist and
        # recomputes the query that produced it. On the filtered path `total`
        # counts raw rows, not matches, so it stays hidden there: a count that
        # answers a different question is worse than none.
        total = page.get("total") if not filtering and isinstance(page, dict) else None
        more_pages = ""
        if isinstance(total, int) and total > offset + len(rows):
            more_pages = f"More artifacts — call again with offset={offset + len(rows)}."
        return render.join(
            render.table(rows),
            render.count_note(len(rows), total, noun="artifacts"),
            more_pages,
            # Never "use offset" for the *filtered* path: clipping only happens
            # there (unfiltered, fetch == limit so there is nothing to clip), and
            # advancing offset there re-scans from a raw row and returns a page
            # that overlaps — often exactly repeats — the one just shown.
            # `limit` goes to 1000, which is the whole scanned window.
            "More matches exist in the scanned window — raise limit (up to 1000). "
            "offset moves the scan window, not the matches, so it would re-list these."
            if clipped
            else "",
            "Filenames are opaque hashes: the service records an artifact's kind and "
            "dataset but not the query that produced it, so identify one by kind and "
            "timestamp, or just recompute.",
        )

    @server.tool(
        name="read_artifact",
        description=(
            "Read a result file produced by run_sql, aggregate, pivot or a sample, with "
            "server-side column projection, filtering, sorting and paging. This is how "
            "you chain one computation into the next without pulling the whole result "
            "into context."
        ),
    )
    @guard
    async def read_artifact(
        filename: Annotated[
            str,
            Field(description="Artifact filename, e.g. the result_file returned by run_sql."),
        ],
        columns: Annotated[
            list[str] | None,
            Field(description="Columns to return. Naming them saves a lot of tokens."),
        ] = None,
        filter_expr: Annotated[
            str | None,
            Field(description="SQL WHERE clause without the WHERE keyword, e.g. \"region = 'US' AND amount > 100\"."),
        ] = None,
        sort_by: Annotated[str | None, Field(description="Column to sort by.")] = None,
        sort_order: Annotated[str, Field(description="'asc' or 'desc', lowercase.")] = "asc",
        limit: Annotated[int, Field(description="Rows to return (1-500).", ge=1, le=500)] = 50,
        offset: Annotated[int, Field(description="Rows to skip, for paging.", ge=0)] = 0,
    ) -> str:
        payload: dict[str, Any] = await ctx.client.get(
            f"/samples/{seg(filename)}/data",
            columns=",".join(columns) if columns else None,
            filter_expr=filter_expr,
            sort_by=sort_by,
            # Passed straight through: GET /samples/{filename}/data types
            # sort_order as Literal["asc","desc"] now, so the service rejects
            # anything else with a 422 that ``explain`` renders. Mirroring that
            # check here would only risk drifting from it.
            sort_order=sort_order,
            limit=limit,
            offset=offset,
        )
        rows = payload.get("data") or []

        schema = ", ".join(
            f"{c.get('name')} ({c.get('dtype')})" for c in payload.get("columns") or []
        )
        notes = []
        total = payload.get("filtered_count")
        if isinstance(total, int) and total > offset + len(rows):
            notes.append(
                f"More rows available — call again with offset={offset + len(rows)}."
            )

        # Clamp the table, never the count and the paging hint — same rule as
        # run_sql. `clamp` keeps the head and drops the tail, and these two are
        # the tail: a caller whose result was truncated is precisely the caller
        # who needs "N of M rows shown" and the concrete `offset=` for the next
        # call, and both would be the first characters deleted. They ride
        # outside the budget rather than shrinking it, and they are a fixed two
        # short sentences.
        tail = render.join(render.count_note(len(rows), total), " ".join(notes))
        body = render.join(
            render.fields(
                [
                    ("artifact", payload.get("filename")),
                    ("rows_total", payload.get("total_count")),
                    ("rows_after_filter", payload.get("filtered_count")),
                ]
            ),
            render.section("Schema", schema),
            render.table(rows, columns),
        )
        return render.join(
            clamp(
                body,
                MAX_RESPONSE_CHARS,
                hint="Use `columns` to project fewer fields, or lower `limit`.",
            ),
            tail,
        )

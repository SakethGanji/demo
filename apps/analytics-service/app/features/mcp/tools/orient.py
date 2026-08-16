"""Orientation tools — cheap, Postgres-only, and they return no data rows.

These answer most questions without touching a single byte of the dataset.
"""

from __future__ import annotations

from typing import Annotated, Any

from mcp.server.mcpserver import MCPServer
from pydantic import Field

from .. import render
from ._common import Ctx, guard, page_items, seg

PAGE_LIMIT = 200
"""Largest page the service's ``pagination`` dependency allows (``le=200``).

Asking for it explicitly rather than taking the default 50 is the difference
between "this sheet documents 60 columns" and "this sheet documents 50 columns",
and only the second is a claim the tool can make silently.
"""


def _page_note(shown: int, total: Any, noun: str) -> str:
    """Say so when the route had more than one page's worth."""
    if not isinstance(total, int) or total <= shown:
        return ""
    return (
        f"Showing {shown} of {total} {noun} — this tool reads one page of "
        f"{PAGE_LIMIT}. The remaining {total - shown} are documented but not listed "
        "here; read them from GET /datasets/{id}/sheet-metadata with an offset."
    )


def register(server: MCPServer, ctx: Ctx) -> None:
    @server.tool(
        name="whoami",
        description=(
            "Show the acting user and their team memberships. Every other tool runs "
            "with these permissions. Call this first if a request unexpectedly returns "
            "'not found' — the dataset may belong to a team you are not in."
        ),
    )
    @guard
    async def whoami() -> str:
        me = await ctx.client.get("/auth/me")
        user = me.get("user", {})
        memberships = me.get("memberships", [])
        # Test fixtures can leave a user in hundreds of teams; the full list is noise
        # in a tool the model is told to call first.
        shown = memberships[:10]
        teams = [f"{m.get('team_name')} ({m.get('role')})" for m in shown]
        if len(memberships) > len(shown):
            teams.append(f"…and {len(memberships) - len(shown)} more")
        is_superuser = bool(user.get("is_superuser"))
        return render.join(
            render.fields(
                [
                    ("user", user.get("name")),
                    ("email", user.get("email")),
                    ("user_id", user.get("id")),
                    ("superuser", is_superuser),
                    ("teams", len(memberships)),
                ]
            ),
            render.section("Teams", render.bullets(teams)),
            "As a superuser you can read every team's data, and sensitive columns are "
            "returned to you unmasked."
            if is_superuser
            else "",
        )

    @server.tool(
        name="search_datasets",
        description=(
            "Find datasets by name, description, or domain. This is how you turn a "
            "topic into a dataset_id — no other tool returns one. Returns metadata "
            "only, never data rows."
        ),
    )
    @guard
    async def search_datasets(
        query: Annotated[
            str | None,
            Field(description="Free text matched against dataset name and description."),
        ] = None,
        domain: Annotated[
            str | None, Field(description="Restrict to one business domain.")
        ] = None,
        favorites_only: Annotated[
            bool, Field(description="Only datasets the acting user has starred.")
        ] = False,
        validation_status: Annotated[
            str | None, Field(description="One of: passed, failed, none.")
        ] = None,
        documentation: Annotated[
            str | None,
            Field(description="Filter by documentation coverage: full, partial, none."),
        ] = None,
        limit: Annotated[int, Field(description="Max datasets to return (1-200).", ge=1, le=200)] = 25,
        offset: Annotated[int, Field(description="Rows to skip, for paging.", ge=0)] = 0,
    ) -> str:
        page = await ctx.client.get(
            "/datasets",
            q=query,
            domain=domain,
            favorites=favorites_only or None,
            validation_status=validation_status,
            documentation=documentation,
            limit=limit,
            offset=offset,
        )
        items = page_items(page)
        if not items:
            return "No datasets matched. Note that datasets owned by teams you are not a member of are invisible rather than forbidden."
        rows = [
            {
                "dataset_id": item.get("id"),
                "name": item.get("name"),
                "domain": item.get("domain"),
                "version": item.get("current_version"),
                "rows": item.get("row_count"),
                "validation": item.get("validation_status"),
                "docs": item.get("documentation"),
            }
            for item in items
        ]
        return render.join(
            render.table(rows),
            render.count_note(len(rows), page.get("total"), noun="datasets"),
        )

    @server.tool(
        name="describe_dataset",
        description=(
            "Full schema of a dataset's current version: every sheet, every column and "
            "its type, row counts, and available versions and tags. Read this before "
            "querying so you use real column names. Returns no data rows."
        ),
    )
    @guard
    async def describe_dataset(
        dataset_id: Annotated[str, Field(description="Dataset UUID from search_datasets.")],
    ) -> str:
        sheets_page = await ctx.client.get(f"/datasets/{dataset_id}/sheets")
        versions_page = await ctx.client.get(f"/datasets/{dataset_id}/versions")

        blocks: list[str] = []
        for sheet in page_items(sheets_page):
            columns = sheet.get("columns") or []
            listed = ", ".join(
                f"{c.get('normalized_name') or c.get('name')} ({c.get('dtype')})"
                for c in columns
            )
            blocks.append(
                render.join(
                    render.fields(
                        [
                            ("sheet", sheet.get("name")),
                            ("sheet_key", sheet.get("sheet_key")),
                            ("rows", sheet.get("row_count")),
                            ("columns", sheet.get("column_count")),
                            ("status", sheet.get("status")),
                        ]
                    ),
                    listed,
                )
            )

        versions = [
            {
                "version": v.get("version_number"),
                "status": v.get("status"),
                "rows": v.get("row_count"),
                "sheets": v.get("sheet_count"),
                "tags": ", ".join(v.get("tags") or []),
                "created": v.get("created_at"),
            }
            for v in page_items(versions_page)[:10]
        ]

        return render.join(
            render.section("Sheets (current version)", "\n\n".join(blocks) or "(none)"),
            render.section("Versions (newest first)", render.table(versions)),
            "Column names shown are the normalized names — use these in filters and SQL. "
            "In run_sql each sheet is a table named by its sheet_key.",
        )

    @server.tool(
        name="get_data_dictionary",
        description=(
            "Business meaning of a sheet's columns: business name, description, "
            "semantic type, unit, sensitivity, and allowed values, plus the sheet's "
            "grain and primary key. Use this to interpret columns instead of guessing "
            "from their names. Returns no data rows."
        ),
    )
    @guard
    async def get_data_dictionary(
        dataset_id: Annotated[str, Field(description="Dataset UUID.")],
        sheet_key: Annotated[
            str | None,
            Field(
                description=(
                    "Sheet key (the normalized slug from describe_dataset). Omit to list "
                    "sheet-level metadata for every sheet."
                )
            ),
        ] = None,
    ) -> str:
        # Both dictionary routes are paginated and default to 50. A dataset with
        # more sheets — or, far more often, a sheet with more than 50 documented
        # columns — would silently answer "there is no dictionary entry for that
        # column" about a column that has one. PAGE_LIMIT is the route maximum
        # (``pagination`` is ``le=200``), and anything past it is disclosed
        # rather than dropped.
        meta_page = await ctx.client.get(
            f"/datasets/{dataset_id}/sheet-metadata", limit=PAGE_LIMIT
        )
        sheet_rows = [
            {
                "sheet_key": m.get("sheet_key"),
                "grain": m.get("grain"),
                "primary_key": ", ".join(m.get("primary_key_columns") or []),
                "description": m.get("description"),
            }
            for m in page_items(meta_page)
        ]
        sheets_block = (
            render.join(
                render.section(
                    "Sheet metadata",
                    render.table(sheet_rows, full=("description",)),
                ),
                _page_note(len(sheet_rows), meta_page.get("total"), "documented sheets"),
            )
            if sheet_rows
            else "No sheet-level metadata (grain, primary key, description) has been "
            "recorded for this dataset."
        )

        if not sheet_key:
            return render.join(
                sheets_block,
                "Pass sheet_key to see the column-level dictionary for one sheet. "
                "Sheet keys come from describe_dataset.",
            )

        columns_page = await ctx.client.get(
            f"/datasets/{dataset_id}/sheet-metadata/{seg(sheet_key)}/columns",
            limit=PAGE_LIMIT,
        )
        column_rows = [
            {
                "column": c.get("column_name"),
                "business_name": c.get("business_name"),
                "semantic_type": c.get("semantic_type"),
                "unit": c.get("unit"),
                "sensitivity": c.get("sensitivity"),
                "allowed_values": ", ".join(str(v) for v in (c.get("allowed_values") or [])),
                "description": c.get("description"),
            }
            for c in page_items(columns_page)
        ]
        if not column_rows:
            return render.join(
                sheets_block,
                f"No column dictionary entries recorded for sheet '{sheet_key}'. Nothing "
                "in this sheet is tagged sensitive, so no column will be masked anywhere "
                "— judge sensitivity from the column names and sample values yourself.",
            )
        tagged = [c["column"] for c in column_rows if c.get("sensitivity")]
        return render.join(
            sheets_block,
            render.section(
                f"Columns — {sheet_key}",
                # `description` and `allowed_values` are the two cells whose tail
                # carries meaning: a description is stored up to 2000 characters
                # and an allowed-values list is a closed set, so an 80-character
                # cell cap turns both into confident half-answers.
                render.table(column_rows, full=("allowed_values", "description")),
            ),
            _page_note(
                len(column_rows), columns_page.get("total"), f"documented columns in {sheet_key}"
            ),
            (
                f"Tagged sensitive: {', '.join(tagged)}. These are masked on preview_rows "
                "and query_rows for callers lacking permission to read them — admins and "
                "superusers see raw values, and run_sql is never masked."
                if tagged
                else "No column here is tagged sensitive, so nothing will be masked."
            ),
        )

    @server.tool(
        name="search_columns",
        description=(
            "Find columns by name fragment across every dataset you can read. Use this "
            "when you know the field you need but not which dataset holds it."
        ),
    )
    @guard
    async def search_columns(
        query: Annotated[str, Field(description="Column-name fragment, e.g. 'customer_id'.", min_length=1)],
        limit: Annotated[int, Field(description="Max matches (1-200).", ge=1, le=200)] = 50,
    ) -> str:
        page = await ctx.client.get("/search/columns", q=query, limit=limit)
        rows = [
            {
                "dataset": hit.get("dataset_name"),
                "dataset_id": hit.get("dataset_id"),
                "sheet": hit.get("sheet_name"),
                "sheet_key": hit.get("sheet_key"),
                "column": hit.get("column_name"),
                "dtype": hit.get("dtype"),
            }
            for hit in page_items(page)
        ]
        if not rows:
            return f"No columns matching '{query}'."
        return render.join(
            render.table(rows), render.count_note(len(rows), page.get("total"), noun="columns")
        )

    @server.tool(
        name="get_dataset_health",
        description=(
            "Quality read-out for a dataset across seven dimensions: schema stability, "
            "validation, missing data, duplicates, drift, freshness, and documentation. "
            "Use it to judge whether a dataset is trustworthy before analysing it."
        ),
    )
    @guard
    async def get_dataset_health(
        dataset_id: Annotated[str, Field(description="Dataset UUID.")],
    ) -> str:
        health: dict[str, Any] = await ctx.client.get(f"/datasets/{dataset_id}/health")
        dimensions = health.get("dimensions") or {}
        # Rendered as lines rather than a table: the summary is the actionable part
        # and must not be truncated to fit a column.
        lines = [
            f"{name}: {body.get('status')} — {body.get('summary')}"
            for name, body in dimensions.items()
        ]
        unknown = [n for n, b in dimensions.items() if b.get("status") == "unknown"]
        hint = ""
        if unknown:
            hint = (
                f"'unknown' ({', '.join(unknown)}) means no stored profile or validation "
                "run exists — not that the data is bad. check_quality computes null rates "
                "and duplicates live for any sheet, so use it rather than treating these "
                "as unanswerable."
            )
        return render.join(
            render.fields([("current_version", health.get("current_version_number"))]),
            "\n".join(lines),
            "Status values: ok, warning, attention, unknown. There is deliberately no "
            "single aggregate score.",
            hint,
        )

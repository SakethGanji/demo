"""Context tools — provenance, history, declared relationships, and saved work.

Everything here reads control-plane metadata only: lineage edges, timeline
events, job rows, relationship edges and saved definitions. No dataset rows are
touched, so these are cheap and safe to call before deciding how to query.
"""

from __future__ import annotations

from typing import Annotated, Any, Callable

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import Field

from .. import render
from ._common import (
    MAX_RESPONSE_CHARS,
    Ctx,
    clamp,
    guard,
    page_items,
    require_sort_order,
)

PAGE_SIZE = 200
"""The service caps every paginated list at 200 items per request."""

SCAN_CAP = 1000
"""How far a client-side filter will page before it stops and says so.

Two endpoints this module uses take no filter the caller needs — /timeline has
no event_type filter and /jobs has no dataset filter — so filtering happens
here. Scanning a bounded window and reporting the window is honest; scanning one
page and reporting "none" would not be.
"""

SAVED_KINDS: dict[str, str] = {
    "view": "views",
    "analytics": "analytics",
    "chart": "charts",
    "rule": "rules",
    "transformation": "transformations",
}

KIND_HELP = (
    "view = saved filter/projection over one sheet; "
    "analytics = saved sample/aggregate/profile/pivot definition; "
    "chart = saved chart over a definition or a view; "
    "rule = data-quality rule; "
    "transformation = saved multi-step pipeline over one sheet"
)


def _ts(value: Any) -> Any:
    """Trim 'YYYY-MM-DD HH:MM:SS.microseconds+00' to the second."""
    if isinstance(value, str) and len(value) > 19 and value[4] == "-" and value[10] in " T":
        return value[:19]
    return value


def _selector(value: Any) -> str:
    """One-token summary of a version_selector blob."""
    if not isinstance(value, dict):
        return ""
    mode = value.get("mode") or "current"
    if mode == "tag":
        return f"tag:{value.get('tag')}"
    if mode == "version":
        return f"v{value.get('version_number')}"
    return "current"


def _kv(mapping: Any) -> str:
    """Flatten a small details/evidence blob to `k=v` pairs on one line."""
    if not isinstance(mapping, dict):
        return render.scalar(mapping)
    return " ".join(
        f"{k}={render.scalar(v, limit=60)}"
        for k, v in mapping.items()
        if v not in (None, "", [], {})
    )


def _inline(value: Any) -> str:
    if isinstance(value, list):
        return "[" + ", ".join(render.scalar(v, limit=None) for v in value) + "]"
    return render.scalar(value, limit=None)


def _tree(value: Any, indent: int = 0) -> list[str]:
    """Indented lines for an arbitrarily nested blob.

    A transform step or a view's query is a nested tree the caller may need to
    reproduce exactly, so it is written out in full rather than squeezed into
    table cells where the interesting part gets cut off.
    """
    pad = "  " * indent
    lines: list[str] = []
    if isinstance(value, dict):
        for key, item in value.items():
            if item in (None, "", [], {}):
                continue
            if isinstance(item, dict) or (
                isinstance(item, list) and any(isinstance(v, (dict, list)) for v in item)
            ):
                lines.append(f"{pad}{key}:")
                lines.extend(_tree(item, indent + 1))
            else:
                lines.append(f"{pad}{key}: {_inline(item)}")
    elif isinstance(value, list):
        for item in value:
            if isinstance(item, (dict, list)):
                sub = _tree(item, indent + 1)
                lines.append(f"{pad}- {sub[0].strip()}" if sub else f"{pad}-")
                lines.extend(sub[1:])
            else:
                lines.append(f"{pad}- {_inline(item)}")
    else:
        lines.append(f"{pad}{_inline(value)}")
    return lines


def _blob(title: str, value: Any) -> str:
    """Render an opaque nested value (params, steps, config, query) as text."""
    if not value:
        return ""
    return render.section(title, "\n".join(_tree(value)))


async def _fetch_all(
    ctx: Ctx, path: str, cap: int = SCAN_CAP
) -> tuple[list[dict[str, Any]], Any, bool]:
    """Every item of a Page[] endpoint, up to `cap`.

    Half the saved-object list routes ignore limit/offset entirely and return
    the whole set in one response; the other half page properly. Collecting
    everything here makes the two behave identically for the caller, and lets
    sorting and paging be exact rather than per-page.

    Returns (items, total, exhausted). `exhausted` is False when the cap cut the
    read short, and it is not decoration: the caller re-sorts these items, and a
    sort over a truncated window silently reorders the answer. Every other
    bounded scan in this module (`_scan`) reports its window, and so must this
    one — see the SCAN_CAP docstring.
    """
    items: list[dict[str, Any]] = []
    total: Any = None
    exhausted = False
    offset = 0
    while True:
        page = await ctx.client.get(path, limit=PAGE_SIZE, offset=offset)
        chunk = page_items(page)
        total = page.get("total") if isinstance(page, dict) else None
        items.extend(chunk)
        if not chunk or len(chunk) < PAGE_SIZE:
            exhausted = True
            break
        if isinstance(total, int) and len(items) >= total:
            exhausted = True
            break
        if len(items) >= cap:
            break
        offset += len(chunk)
    # Reading everything the server had still leaves items dropped if the server
    # returned more than `cap` in one response — the routes that ignore
    # limit/offset do exactly that — so the slice has to be part of the test.
    return items[:cap], total, exhausted and len(items) <= cap


async def _scan(
    ctx: Ctx,
    path: str,
    *,
    keep: Callable[[dict[str, Any]], bool],
    want: int,
    offset: int = 0,
    cap: int = SCAN_CAP,
    **params: Any,
) -> tuple[list[dict[str, Any]], int, Any, bool]:
    """Page a list endpoint until `want` matches are found or `cap` is scanned.

    Returns (matches, scanned, total, exhausted).
    """
    matched: list[dict[str, Any]] = []
    scanned = 0
    total: Any = None
    exhausted = False
    cursor = offset
    while scanned < cap:
        page = await ctx.client.get(path, limit=PAGE_SIZE, offset=cursor, **params)
        chunk = page_items(page)
        total = page.get("total") if isinstance(page, dict) else None
        scanned += len(chunk)
        matched.extend(item for item in chunk if keep(item))
        cursor += len(chunk)
        if not chunk or len(chunk) < PAGE_SIZE:
            exhausted = True
            break
        if isinstance(total, int) and cursor >= total:
            exhausted = True
            break
        if len(matched) >= want:
            break
    return matched[:want], scanned, total, exhausted


def _saved_row(kind: str, item: dict[str, Any]) -> dict[str, Any]:
    common = {"id": item.get("id"), "name": item.get("name")}
    if kind == "view":
        return {
            **common,
            "sheet": item.get("sheet_key") or item.get("sheet_name"),
            "version": _selector(item.get("version_selector")),
            "created": _ts(item.get("created_at")),
        }
    if kind == "analytics":
        return {
            **common,
            "kind": item.get("kind"),
            "sheet": item.get("sheet"),
            "version": _selector(item.get("version_selector")),
            "created": _ts(item.get("created_at")),
        }
    if kind == "chart":
        return {
            **common,
            "chart_type": item.get("chart_type"),
            "source": (
                f"definition:{item['definition_id']}"
                if item.get("definition_id")
                else f"view:{item.get('view_id')}"
            ),
            "created": _ts(item.get("created_at")),
        }
    if kind == "rule":
        return {
            **common,
            "rule_type": item.get("rule_type"),
            "scope": item.get("scope_type"),
            "sheet": item.get("sheet_selector"),
            "column": item.get("column_selector"),
            "severity": item.get("severity"),
            "enabled": item.get("enabled"),
        }
    return {
        **common,
        "sheet": item.get("sheet_key"),
        "steps": len(item.get("steps") or []),
        "version": _selector(item.get("version_selector")),
        "created": _ts(item.get("created_at")),
    }


def _saved_detail(kind: str, item: dict[str, Any]) -> str:
    head = render.fields(
        [
            ("kind", kind),
            ("id", item.get("id")),
            ("name", item.get("name")),
            ("description", item.get("description")),
            ("dataset_id", item.get("dataset_id")),
            ("sheet", item.get("sheet_key") or item.get("sheet") or item.get("sheet_selector")),
            ("definition_kind", item.get("kind") if kind == "analytics" else None),
            ("chart_type", item.get("chart_type")),
            ("rule_type", item.get("rule_type")),
            ("scope", item.get("scope_type")),
            ("column", item.get("column_selector")),
            ("severity", item.get("severity")),
            ("enabled", item.get("enabled")),
            ("definition_id", item.get("definition_id")),
            ("view_id", item.get("view_id")),
            ("version", _selector(item.get("version_selector"))),
            ("created_by", item.get("created_by")),
            ("created_at", _ts(item.get("created_at"))),
            ("updated_at", _ts(item.get("updated_at"))),
        ]
    )
    return render.join(
        head,
        _blob("Query", item.get("query")),
        _blob("Params", item.get("params")),
        _blob("Steps", item.get("steps")),
        _blob("Parameters", item.get("parameters")),
        _blob("Config", item.get("config")),
    )


def _audit_summary(dataset_id: str | None, details: dict[str, Any]) -> str:
    """`POST …/versions/1/sql -> 200`.

    Audit paths repeat the api prefix and the dataset id on every row; both are
    already known to the caller, so they are pure cost.
    """
    path = str(details.get("path") or "")
    prefixes = [f"/api/v1/datasets/{dataset_id}"] if dataset_id else []
    prefixes.append("/api/v1")
    for prefix in prefixes:
        if path.startswith(prefix):
            path = "…" + path[len(prefix) :]
            break
    return f"{details.get('method')} {path} -> {details.get('status_code')}"


def register(server: MCPServer, ctx: Ctx) -> None:
    @server.tool(
        name="get_lineage",
        description=(
            "Where a dataset came from and what was derived from it. Use it to answer "
            "'is this a raw upload or a derived table?', 'which dataset is the source of "
            "truth?', and 'what breaks if this changes?'. Default is one hop (immediate "
            "parents and children); full_graph=true walks the whole derivation DAG. "
            "Lineage is only recorded for datasets the platform itself produced — a "
            "publish, join, or transformation — so a directly uploaded dataset correctly "
            "has none, and an ETL step done outside the platform leaves no edge. "
            "Returns no data rows."
        ),
    )
    @guard
    async def get_lineage(
        dataset_id: Annotated[str, Field(description="Dataset UUID from search_datasets.")],
        full_graph: Annotated[
            bool,
            Field(
                description=(
                    "False (default): immediate parents and children only, with the "
                    "version numbers and the sheet each edge came from. True: the whole "
                    "upstream+downstream DAG as nodes and edges, without version detail."
                )
            ),
        ] = False,
        depth: Annotated[
            int,
            Field(description="Hops to walk when full_graph=true (1-25).", ge=1, le=25),
        ] = 10,
    ) -> str:
        if not full_graph:
            payload: dict[str, Any] = await ctx.client.get(f"/datasets/{dataset_id}/lineage")
            parents = [
                {
                    "parent_dataset": edge.get("parent_dataset_name"),
                    "parent_dataset_id": edge.get("parent_dataset_id"),
                    "parent_version": edge.get("parent_version_number"),
                    "parent_sheet": edge.get("parent_sheet_key"),
                    "relation": edge.get("relation"),
                    "into_version": edge.get("version_number"),
                    "at": _ts(edge.get("created_at")),
                }
                for edge in payload.get("parents") or []
            ]
            children = [
                {
                    "child_dataset": edge.get("child_dataset_name"),
                    "child_dataset_id": edge.get("child_dataset_id"),
                    "child_version": edge.get("child_version_number"),
                    "from_version": edge.get("parent_version_number"),
                    "from_sheet": edge.get("parent_sheet_key"),
                    "relation": edge.get("relation"),
                    "at": _ts(edge.get("created_at")),
                }
                for edge in payload.get("children") or []
            ]
            if not parents and not children:
                return (
                    "No lineage recorded for this dataset: nothing was derived from it, "
                    "and it was not produced from another dataset — it was uploaded "
                    "directly. Lineage edges are only written when the platform publishes "
                    "a transformation, join, or saved-analytics result."
                )
            return clamp(
                render.join(
                    render.section(
                        "Derived from (parents)",
                        render.table(parents) if parents else "(none — this side is a root)",
                    ),
                    render.section(
                        "Derived into (children)",
                        render.table(children) if children else "(none — nothing was built from this)",
                    ),
                    "Each row is one recorded derivation of one version. "
                    "Call again with full_graph=true to follow the chain further back.",
                ),
                MAX_RESPONSE_CHARS,
                hint="Use full_graph=true with a smaller depth for an overview instead.",
            )

        graph: dict[str, Any] = await ctx.client.get(
            f"/datasets/{dataset_id}/lineage/graph", max_depth=depth
        )
        nodes = graph.get("nodes") or []
        edges = graph.get("edges") or []
        names = {n.get("id"): n.get("name") for n in nodes}
        node_rows = [
            {
                "dataset_id": n.get("id"),
                "name": n.get("name"),
                "domain": n.get("domain"),
                "deprecated": n.get("deprecated"),
                "root": n.get("is_root"),
                "created": _ts(n.get("created_at")),
            }
            for n in nodes
        ]
        edge_rows = [
            {
                "child": names.get(e.get("child_id"), e.get("child_id")),
                "parent": names.get(e.get("parent_id"), e.get("parent_id")),
                "relation": e.get("relation"),
                "hops": e.get("depth"),
            }
            for e in edges
        ]
        if not edge_rows:
            return render.join(
                render.section("Datasets in the graph", render.table(node_rows)),
                f"This dataset is isolated in the lineage graph within {depth} hops: no "
                "recorded parent and no recorded child. That is normal for a directly "
                "uploaded dataset.",
            )
        return clamp(
            render.join(
                render.section("Datasets in the graph", render.table(node_rows)),
                render.section("Edges (child was derived from parent)", render.table(edge_rows)),
                render.fields(
                    [("max_depth", graph.get("max_depth")), ("truncated", graph.get("truncated"))]
                ),
                "truncated=true means the depth cap was hit and the DAG continues beyond "
                "these nodes — raise depth (max 25) to see further."
                if graph.get("truncated")
                else "",
            ),
            MAX_RESPONSE_CHARS,
            hint="Lower `depth` to see fewer hops.",
        )

    @server.tool(
        name="get_activity",
        description=(
            "What has happened to a dataset: its event timeline, its usage counters, and "
            "the background jobs that ran against it. Use it to check whether a dataset "
            "is actively maintained, when it last changed, who changed it, or why a "
            "validation/profile/transform did not produce the result you expected. "
            "Reads are NOT in the timeline — a GET leaves no event, only the usage "
            "counters — so a quiet timeline does not mean an unused dataset. Omit "
            "dataset_id to see recent jobs across every dataset you can read. "
            "Returns no data rows."
        ),
    )
    @guard
    async def get_activity(
        dataset_id: Annotated[
            str | None,
            Field(
                description=(
                    "Dataset UUID. Required for the timeline and usage sections; omit it "
                    "to get platform-wide jobs only."
                )
            ),
        ] = None,
        event_types: Annotated[
            list[str] | None,
            Field(
                description=(
                    "Keep only these timeline events. Known types: version_created, "
                    "tag_set, tag_promote, tag_rollback, tag_delete, validation_run, "
                    "profile_run, transformation_run, derived_from, published_to, audit. "
                    "'audit' rows are write requests and usually dominate — filter them "
                    "out to see the meaningful history."
                )
            ),
        ] = None,
        include_jobs: Annotated[
            bool, Field(description="Include the background-jobs section.")
        ] = True,
        job_status: Annotated[
            str | None,
            Field(description="Filter jobs by status: pending, running, completed, failed."),
        ] = None,
        job_type: Annotated[
            str | None,
            Field(
                description=(
                    "Filter jobs by type: import, validation, profiling, analytics, "
                    "transform, relationship_discovery, artifact_gc, webhook_delivery."
                )
            ),
        ] = None,
        limit: Annotated[
            int, Field(description="Max timeline events and max jobs (1-200).", ge=1, le=200)
        ] = 25,
        offset: Annotated[int, Field(description="Timeline events to skip, for paging.", ge=0)] = 0,
    ) -> str:
        if not dataset_id and not include_jobs:
            raise ToolError(
                "Nothing to report: the timeline and usage sections need a dataset_id, "
                "and include_jobs is false. Pass a dataset_id, or leave include_jobs on "
                "for platform-wide jobs."
            )

        blocks: list[str] = []
        notes: list[str] = []

        if dataset_id:
            usage: dict[str, Any] = await ctx.client.get(f"/datasets/{dataset_id}/usage")
            usage_fields = render.fields(
                [
                    ("downloads", usage.get("downloads")),
                    ("writes", usage.get("writes")),
                    ("reads", usage.get("reads")),
                    ("total_events", usage.get("total_events")),
                    ("last_activity_at", _ts(usage.get("last_activity_at"))),
                ]
            )
            # `UsageResponse` types the counters as required ints, so
            # `render.fields` always renders them — zero is a value, not an
            # empty. The "nothing here" sentence therefore has to hang off the
            # counters being zero, not off the block being blank; hung off the
            # block it could never appear, and "downloads: 0 / writes: 0 /
            # total_events: 0" alone reads as a broken counter rather than as an
            # untouched dataset. It is a live case: the counters are built from
            # the audit trail and count only successful requests, so a dataset
            # whose only traffic was denied reports all zeroes.
            quiet = not any(
                usage.get(k) for k in ("downloads", "writes", "reads", "total_events")
            )
            blocks.append(
                render.section(
                    "Usage",
                    render.join(
                        usage_fields,
                        # `reads` counts the queries, renders and previews that
                        # are POSTs because their request is a spec — NOT plain
                        # GET reads, which are not audited at all. Saying "no
                        # reads" flat would therefore assert something this
                        # trail cannot see; the distinction is the difference
                        # between "nobody uses it" and "nobody changes it".
                        "No recorded activity at all — nothing queried, downloaded or "
                        "changed. Plain GET reads are not audited, and neither are "
                        "denied requests, so this is 'nobody has successfully queried, "
                        "changed or exported this dataset', not 'nobody has looked at it'."
                        if quiet
                        else "",
                    ),
                )
            )

            wanted = {t.strip().lower() for t in event_types} if event_types else None
            if wanted:
                events, scanned, all_events, exhausted = await _scan(
                    ctx,
                    f"/datasets/{dataset_id}/timeline",
                    keep=lambda item: str(item.get("event_type", "")).lower() in wanted,
                    want=limit,
                    offset=offset,
                )
                # The endpoint's `total` counts every event type, so it is not the
                # total of what was filtered — reporting it would overstate.
                total = None
                if not events:
                    where = (
                        "in the whole timeline"
                        if exhausted
                        else f"in the {scanned} most recent events"
                    )
                    notes.append(
                        f"No {', '.join(sorted(wanted))} events {where}"
                        + ("." if exhausted else " — retry with a larger offset.")
                        + f" The timeline holds {all_events} events in total."
                    )
                elif not exhausted:
                    notes.append(
                        f"Timeline was filtered locally over the {scanned} most recent of "
                        f"{all_events} events — /timeline has no event_type filter."
                    )
            else:
                page = await ctx.client.get(
                    f"/datasets/{dataset_id}/timeline", limit=limit, offset=offset
                )
                events = page_items(page)
                total = page.get("total")

            rows = [
                {
                    "when": _ts(event.get("occurred_at")),
                    "event": event.get("event_type"),
                    "actor": event.get("actor"),
                    "details": (
                        _audit_summary(dataset_id, event.get("details") or {})
                        if event.get("event_type") == "audit"
                        else _kv(event.get("details"))
                    ),
                }
                for event in events
            ]
            if rows:
                # Table cells are capped, so a lineage event's dataset id can be cut
                # off. get_lineage prints those ids in full — say so rather than let
                # the model retype a truncated uuid.
                lineage_seen = any(
                    row["event"] in {"derived_from", "published_to"} for row in rows
                )
                blocks.append(
                    render.section(
                        "Timeline (newest first)",
                        render.join(
                            render.table(rows),
                            render.count_note(len(rows), total, noun="events"),
                            "Lineage events appear here in summary only — call get_lineage "
                            "for the untruncated dataset ids on each edge."
                            if lineage_seen
                            else "",
                        ),
                    )
                )
            elif not wanted:
                blocks.append(
                    render.section(
                        "Timeline (newest first)",
                        "No history events recorded for this dataset"
                        + (f" beyond offset {offset}." if offset else "."),
                    )
                )

        if include_jobs:
            if dataset_id:
                jobs, scanned, _, exhausted = await _scan(
                    ctx,
                    "/jobs",
                    keep=lambda item: item.get("dataset_id") == dataset_id,
                    want=limit,
                    status=job_status,
                    job_type=job_type,
                )
                shown_total = None
            else:
                page = await ctx.client.get(
                    "/jobs", status=job_status, job_type=job_type, limit=limit
                )
                jobs = page_items(page)
                shown_total = page.get("total")
                scanned, exhausted = len(jobs), True

            job_rows = [
                {
                    "job_id": job.get("id"),
                    "type": job.get("job_type"),
                    "status": job.get("status"),
                    "progress": job.get("progress"),
                    "dataset_id": job.get("dataset_id"),
                    "created": _ts(job.get("created_at")),
                    "completed": _ts(job.get("completed_at")),
                    "error": job.get("error"),
                }
                for job in jobs
            ]
            if job_rows:
                blocks.append(
                    render.section(
                        "Jobs (newest first)",
                        render.join(
                            render.table(job_rows),
                            render.count_note(len(job_rows), shown_total, noun="jobs"),
                        ),
                    )
                )
            else:
                filters = ", ".join(
                    part
                    for part in (
                        f"status={job_status}" if job_status else "",
                        f"job_type={job_type}" if job_type else "",
                    )
                    if part
                )
                suffix = f" matching {filters}" if filters else ""
                where = (
                    "for this dataset"
                    if dataset_id and exhausted
                    else f"for this dataset in the {scanned} most recent jobs"
                    if dataset_id
                    else "at all"
                )
                blocks.append(
                    render.section("Jobs", f"No background jobs{suffix} {where}.")
                )
            if dataset_id and not exhausted:
                notes.append(
                    f"Jobs were filtered to this dataset locally over the {scanned} most "
                    "recent jobs — /jobs has no dataset filter, so older ones may exist."
                )

        return clamp(
            render.join(*blocks, " ".join(notes)),
            MAX_RESPONSE_CHARS,
            hint="Lower `limit`, or pass event_types to drop the audit rows.",
        )

    @server.tool(
        name="list_relationships",
        description=(
            "Declared and suggested join keys between sheets — which column in one sheet "
            "references which column in another, within this dataset or across datasets. "
            "Read this before writing a join so you use a key someone has actually "
            "verified. Only CONFIRMED relationships can drive the join builder; "
            "'suggested' edges are statistical guesses from discovery and 'rejected' ones "
            "were turned down by a human, so neither is usable as a join key without "
            "review. Pass relationship_id for the evidence (overlap, distinctness, "
            "confidence) behind one edge. Returns no data rows."
        ),
    )
    @guard
    async def list_relationships(
        dataset_id: Annotated[
            str, Field(description="Dataset UUID that owns the edges (the 'from' side).")
        ],
        status: Annotated[
            str | None,
            Field(
                description=(
                    "Filter by review state: confirmed (usable for joins), suggested "
                    "(unreviewed guess), rejected. Omit for all three."
                )
            ),
        ] = None,
        relationship_id: Annotated[
            str | None,
            Field(description="Show one edge in full, with its evidence, instead of the list."),
        ] = None,
        limit: Annotated[int, Field(description="Max edges to return (1-200).", ge=1, le=200)] = 50,
        offset: Annotated[int, Field(description="Edges to skip, for paging.", ge=0)] = 0,
    ) -> str:
        if relationship_id:
            edge: dict[str, Any] = await ctx.client.get(
                f"/datasets/{dataset_id}/relationships/{relationship_id}"
            )
            evidence = edge.get("evidence") or {}
            usable = str(edge.get("status")) == "confirmed"
            return render.join(
                render.fields(
                    [
                        ("id", edge.get("id")),
                        ("from", f"{edge.get('from_sheet') or edge.get('from_logical_sheet_id')}."
                                 f"{edge.get('from_column')}"),
                        ("to", f"{edge.get('to_sheet') or edge.get('to_logical_sheet_id')}."
                               f"{edge.get('to_column')}"),
                        ("to_dataset_id", edge.get("to_dataset_id")),
                        ("status", edge.get("status")),
                        ("method", edge.get("method")),
                        ("confidence", edge.get("confidence")),
                        ("created_by", edge.get("created_by")),
                        ("reviewed_by", edge.get("reviewed_by")),
                        ("created_at", _ts(edge.get("created_at"))),
                        ("updated_at", _ts(edge.get("updated_at"))),
                    ]
                ),
                render.section("Evidence", render.fields(list(evidence.items())))
                if evidence
                else "No evidence was recorded for this edge.",
                "This edge is confirmed, so it can drive a join."
                if usable
                else f"This edge is '{edge.get('status')}', so it cannot drive a join — only "
                "confirmed edges can. Treat it as a hypothesis and check the evidence.",
            )

        if status is not None:
            status = status.strip().lower()
            if status not in {"suggested", "confirmed", "rejected"}:
                raise ToolError(
                    f"status must be suggested, confirmed or rejected, got {status!r}."
                )

        page = await ctx.client.get(
            f"/datasets/{dataset_id}/relationships", status=status, limit=limit, offset=offset
        )
        items = page_items(page)
        if not items:
            scope = f" with status '{status}'" if status else ""
            return (
                f"No relationships{scope} are recorded for this dataset. Nothing here is "
                "usable as a verified join key — infer keys from column names and the data "
                "dictionary instead, and remember that only confirmed edges can drive the "
                "join builder."
            )
        rows = [
            {
                "relationship_id": edge.get("id"),
                "from": f"{edge.get('from_sheet') or '?'}.{edge.get('from_column')}",
                "to": f"{edge.get('to_sheet') or '?'}.{edge.get('to_column')}",
                "cross_dataset": edge.get("to_dataset_id") != edge.get("dataset_id"),
                "status": edge.get("status"),
                "method": edge.get("method"),
                "confidence": edge.get("confidence"),
            }
            for edge in items
        ]
        confirmed = sum(1 for edge in items if edge.get("status") == "confirmed")
        if confirmed == len(rows):
            verdict = "All of these are confirmed, so any of them can drive a join."
        elif confirmed:
            verdict = (
                f"{confirmed} of these are confirmed and can drive a join; the other "
                f"{len(rows) - confirmed} cannot."
            )
        else:
            verdict = (
                "None of these are confirmed, so none can drive a join yet — they are "
                "hypotheses awaiting review."
            )
        return clamp(
            render.join(
                render.table(rows),
                render.count_note(len(rows), page.get("total"), noun="relationships"),
                f"Listed most confident first. {verdict} "
                "Pass relationship_id to see the evidence behind one edge.",
            ),
            MAX_RESPONSE_CHARS,
            hint="Filter with `status`, or lower `limit`.",
        )

    @server.tool(
        name="list_saved_objects",
        description=(
            "Work other people already saved on a dataset: views, saved analytics "
            "definitions, charts, quality rules, and transformation pipelines. Check here "
            "before building an analysis from scratch — a saved object records the sheet, "
            "columns and filters a human considered correct, which is better evidence than "
            "guessing. Call with no kind for a count of each type, with a kind for the "
            "list, and with kind + object_id for the full definition. This server is "
            "read-only: it can show these definitions but cannot create or run them. "
            "Returns no data rows."
        ),
    )
    @guard
    async def list_saved_objects(
        dataset_id: Annotated[str, Field(description="Dataset UUID.")],
        kind: Annotated[
            str | None,
            Field(description=f"One of view, analytics, chart, rule, transformation. {KIND_HELP}. Omit to count all kinds."),
        ] = None,
        object_id: Annotated[
            str | None,
            Field(description="Show one object in full (requires kind)."),
        ] = None,
        sort_order: Annotated[
            str,
            Field(description="'desc' for newest first (default), 'asc' for oldest first."),
        ] = "desc",
        limit: Annotated[int, Field(description="Max objects to return (1-200).", ge=1, le=200)] = 50,
        offset: Annotated[int, Field(description="Objects to skip, for paging.", ge=0)] = 0,
    ) -> str:
        if kind is not None:
            kind = kind.strip().lower()
            # The plural is what the counts table shows, so accept it too.
            if kind not in SAVED_KINDS and kind.endswith("s"):
                kind = kind[:-1]
            if kind not in SAVED_KINDS:
                raise ToolError(
                    f"kind must be one of {', '.join(SAVED_KINDS)}, got {kind!r}. {KIND_HELP}."
                )
        if object_id and not kind:
            raise ToolError("object_id needs kind — the id alone does not say which type to fetch.")

        if kind is None:
            counts: list[dict[str, Any]] = []
            for name, segment in SAVED_KINDS.items():
                page = await ctx.client.get(f"/datasets/{dataset_id}/{segment}", limit=1)
                counts.append({"kind": name, "count": page.get("total") if isinstance(page, dict) else None})
            if not any(row["count"] for row in counts):
                return (
                    "Nothing has been saved on this dataset — no views, analytics "
                    "definitions, charts, quality rules or transformations. There is no "
                    "prior work to reuse here, and with no quality rules a validation run "
                    "would check nothing."
                )
            return render.join(
                render.table(counts),
                f"Call again with kind=<one of these> for the list. {KIND_HELP}.",
            )

        segment = SAVED_KINDS[kind]

        if object_id:
            # Every kind, rules included, is fetched by id. Rules used to be the
            # exception: the quality API had no GET for a single rule, so this
            # scanned the list instead — up to five requests, and a rule past the
            # 1000-item scan window came back as "may still exist beyond that
            # window", which is a hedge, not an answer, about an id the caller is
            # holding. `GET /datasets/{id}/rules/{rule_id}` exists now, so a
            # missing rule 404s like every other missing object and `explain`
            # adds the standing caveat that a 404 also covers another team's
            # dataset.
            item = await ctx.client.get(f"/datasets/{dataset_id}/{segment}/{object_id}")
            return clamp(
                _saved_detail(kind, item), MAX_RESPONSE_CHARS, hint="This definition is large."
            )

        order = require_sort_order(sort_order, default="desc")
        items, total, exhausted = await _fetch_all(ctx, f"/datasets/{dataset_id}/{segment}")
        if not items:
            return (
                f"No saved {segment} on this dataset. "
                + (
                    "With no quality rules defined, a validation run would have nothing "
                    "to check."
                    if kind == "rule"
                    else "Call list_saved_objects with no kind to see whether other kinds "
                    "of saved work exist."
                )
            )
        # The sort below runs in this process over whatever `_fetch_all` managed
        # to read, and the service does not return these newest-first (rules are
        # ORDER BY created_at, everything else ORDER BY name). So a capped read
        # means the sort saw the alphabetically-first — not the newest — slice,
        # and neither 'desc' nor 'asc' answers the question that was asked. The
        # count below still reports the true total, which is exactly what makes
        # staying silent here dishonest: "50 of 1500" reads as the newest 50.
        window_note = (
            ""
            if exhausted
            else (
                f"Only the first {len(items)} {segment} were read, and the sort ran over "
                "those — anything outside that window is missing from this ordering at "
                "every offset. The service returns these in its own order (name, or "
                "creation time for rules), so this is not the newest slice."
            )
        )
        items.sort(key=lambda item: str(item.get("created_at") or ""), reverse=(order == "desc"))
        window = items[offset : offset + limit]
        if not window:
            # Only reachable when the read was exhausted: offset caps at 200 and
            # a capped read holds SCAN_CAP (1000) items, so a truncated window is
            # never shorter than the largest offset. No window_note needed here.
            return (
                f"Only {len(items)} saved {segment} exist, so offset {offset} is past the "
                "end. Lower the offset."
            )
        rows = [_saved_row(kind, item) for item in window]
        return clamp(
            render.join(
                render.table(rows),
                render.count_note(len(rows), total if isinstance(total, int) else len(items), noun=segment),
                window_note,
                f"Pass kind='{kind}' with object_id to see one in full "
                + ("(query, filters, columns)." if kind in {"view", "analytics", "transformation"}
                   else "(parameters and config)."),
            ),
            MAX_RESPONSE_CHARS,
            hint="Lower `limit` or page with `offset`.",
        )

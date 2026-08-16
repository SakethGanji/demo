"""Pipeline tools — the write side: relationships, joins, transformations, publishing.

Everything else here reads. These four tools create things, and three of
them create things that outlive the conversation: a relationship edge, a saved
transformation, an artifact, a published dataset or version.

The shape is deliberately a ladder, and each rung has a free rehearsal:

    relationship -> confirm -> join preview -> join execute -> publish
    transformation -> preview -> run -> publish

`join_datasets action="preview"` and `transform_data action="preview"` persist
nothing at all, so there is never a reason to run the expensive step blind.
Nothing here deletes: no tool removes a relationship, a transformation, an
artifact or a version, and publishing always creates rather than overwrites.
"""

from __future__ import annotations

from typing import Annotated, Any

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import Field

from .. import render
from ._common import Ctx, clamp, guard

RELATIONSHIP_ACTIONS = ("suggest", "seed", "declare", "confirm", "reject")
JOIN_ACTIONS = ("preview", "execute")
TRANSFORM_ACTIONS = ("create", "preview", "run", "inspect")
PUBLISH_SOURCES = ("analytics", "transformation", "join")

STEPS_HELP = """\
Ordered pipeline, max 50 steps. Each step is an object with a "type" and that type's
own fields. The pipeline is compiled against the sheet's schema when it is saved, so a
bad column name or type is rejected at create time rather than at run time.

Column-set steps: {"type":"select","columns":[...]} | {"type":"drop","columns":[...]} |
{"type":"rename","renames":{"old":"new"}} | {"type":"reorder","columns":[...]} |
{"type":"split","column":c,"delimiter":"-","index":1,"into":"part"} |
{"type":"merge","columns":[a,b],"into":"full","separator":" ","drop_sources":false} |
{"type":"compute","into":"name","expression":<expr>}
Value steps (column set unchanged): {"type":"cast","column":c,"to":"double"} |
{"type":"trim","columns":[...],"mode":"both"} |
{"type":"case_normalize","columns":[...],"mode":"lower"} |
{"type":"replace","column":c,"mode":"substring","find":"x","replace_with":"y","nulls_to":"?"} |
{"type":"parse_dates","columns":[...],"format":"%Y-%m-%d"}
Row steps: {"type":"filter","where":<filter tree, same shape as query_rows>} |
{"type":"deduplicate","subset":[...],"keep":"first","order_by":[{"column":c,"direction":"desc"}]} |
{"type":"sort","by":[{"column":c,"direction":"asc"}]} | {"type":"limit","count":n,"offset":0}

A compute `expression` is a typed tree, never a formula string. Nodes:
{"op":"col","name":c} | {"op":"lit","value":v} |
{"op":"arith","fn":"add|sub|mul|div|mod","left":<expr>,"right":<expr>} |
{"op":"concat","parts":[<expr>,...],"separator":" "} |
{"op":"if","cases":[{"when":<filter tree>,"then":<expr>}],"else":<expr>} |
{"op":"coalesce","args":[<expr>,...]} | {"op":"round","value":<expr>,"digits":2} |
{"op":"date_extract","part":"year|month|day|hour|minute|dow|week|quarter","value":<expr>} |
{"op":"cast","value":<expr>,"to":"varchar|text|integer|bigint|double|decimal|boolean|date|timestamp|time"} |
{"op":"str","fn":"lower|upper|trim|length|substr","value":<expr>,"start":1,"length":3}
Expressions nest at most 12 deep. arith needs numeric operands and date_extract needs a
DATE/TIMESTAMP one — a type mismatch is rejected, not silently coerced.

Concrete example — line total, then only the big ones, newest first:
[{"type":"compute","into":"line_total","expression":{"op":"round","digits":2,
  "value":{"op":"arith","fn":"mul","left":{"op":"col","name":"quantity"},
           "right":{"op":"col","name":"unit_price"}}}},
 {"type":"filter","where":{"logic":"and","conditions":[
   {"column":"line_total","op":"gt","value":100}]}},
 {"type":"sort","by":[{"column":"line_total","direction":"desc"}]}]"""

VERSION_SELECTOR_HELP = (
    'Which version the pipeline reads: {"mode":"current"} (default), '
    '{"mode":"tag","tag":"prod"}, or {"mode":"version","version_number":3}.'
)


def _require(action: str, allowed: tuple[str, ...], name: str = "action") -> str:
    normalized = str(action).strip().lower()
    if normalized not in allowed:
        raise ToolError(
            f"{name} must be one of {', '.join(allowed)}, got {action!r}."
        )
    return normalized


def _needs(value: Any, param: str, when: str) -> Any:
    """Fail before the request when a branch's required argument is missing.

    *when* is the condition as the caller would write it, e.g. ``action='run'``.
    """
    if value in (None, "", [], {}):
        raise ToolError(f"{param} is required when {when}.")
    return value


def _relationship_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "relationship_id": r.get("id"),
            "from": f"{r.get('from_sheet')}.{r.get('from_column')}",
            "to": f"{r.get('to_sheet')}.{r.get('to_column')}",
            "cross_dataset": r.get("to_dataset_id") != r.get("dataset_id"),
            "status": r.get("status"),
            "method": r.get("method"),
            "confidence": r.get("confidence"),
        }
        for r in rows
    ]


def _evidence_line(row: dict[str, Any]) -> str:
    evidence = row.get("evidence") or {}
    interesting = [
        ("coverage", evidence.get("coverage")),
        ("target_uniqueness", evidence.get("target_uniqueness")),
        ("name_score", evidence.get("name_score")),
        ("matched_distinct", evidence.get("matched_distinct")),
        ("child_distinct", evidence.get("child_distinct")),
        ("rule_name", evidence.get("rule_name")),
    ]
    body = render.fields(interesting)
    return body


def _selector_text(selector: dict[str, Any] | None) -> str:
    """Render a version selector as prose rather than a raw dict."""
    selector = selector or {}
    mode = selector.get("mode", "current")
    if mode == "tag":
        return f"tag '{selector.get('tag')}'"
    if mode == "version":
        return f"version {selector.get('version_number')}"
    return "current version"


def _warning_block(warnings: dict[str, Any]) -> str:
    return render.fields(
        [
            ("left_rows", warnings.get("left_rows")),
            ("right_rows", warnings.get("right_rows")),
            ("left_duplicate_keys", warnings.get("left_duplicate_keys")),
            ("right_duplicate_keys", warnings.get("right_duplicate_keys")),
            ("many_to_many", warnings.get("many_to_many")),
            ("estimated_output_rows", warnings.get("estimated_output_rows")),
            ("row_expansion_factor", warnings.get("row_expansion_factor")),
            ("unmatched_left_pct", warnings.get("unmatched_left_pct")),
            ("unmatched_right_pct", warnings.get("unmatched_right_pct")),
            ("column_collisions", ", ".join(warnings.get("column_collisions") or [])),
        ]
    )


def _join_advice(warnings: dict[str, Any]) -> str:
    notes: list[str] = []
    if warnings.get("many_to_many"):
        notes.append(
            "MANY-TO-MANY: both sides repeat the key, so output rows multiply. "
            "Aggregate or deduplicate one side first unless you meant this."
        )
    factor = warnings.get("row_expansion_factor")
    if isinstance(factor, (int, float)) and factor > 1.5:
        notes.append(
            f"Each left row becomes {factor:g} output rows — any SUM over the result "
            "will double-count the left side's values."
        )
    for side in ("left", "right"):
        pct = warnings.get(f"unmatched_{side}_pct")
        if isinstance(pct, (int, float)) and pct >= 5:
            notes.append(
                f"{pct}% of {side} rows have no match. An inner join drops them; "
                "how='left' keeps the left side's."
            )
    collisions = warnings.get("column_collisions") or []
    if collisions:
        notes.append(
            f"Colliding column names ({', '.join(collisions)}) are kept from both sides — "
            "the right side's copy is prefixed with its sheet key."
        )
    return " ".join(notes)


def register(server: MCPServer, ctx: Ctx) -> None:
    @server.tool(
        name="manage_relationships",
        description=(
            "Find, declare and review the key relationships between sheets — which "
            "column in one sheet references which column in another. THIS TOOL CHANGES "
            "STATE: every action writes relationship rows that persist for the whole "
            "team.\n\n"
            "Use it when you want to join two sheets, or to understand how a workbook "
            "fits together. A relationship is the ONLY way join_datasets can be told "
            "what to join on — there is no free-form join-key parameter anywhere — and "
            "only a CONFIRMED relationship may drive a join, so a suggested edge must "
            "be confirmed first.\n\n"
            "Actions: 'suggest' probes the data statistically for undeclared "
            "relationships and records what it finds as suggestions (it also returns "
            "every suggestion already on the dataset, so it doubles as the way to get a "
            "relationship_id); 'seed' turns the dataset's foreign_key quality rules "
            "into edges; 'declare' states one by hand and confirms it by default; "
            "'confirm' and 'reject' review one. Suggestions are never auto-applied, and "
            "rejecting one stops discovery resurrecting it.\n\n"
            "Requires write permission on the dataset (and read on the target dataset "
            "for a cross-dataset edge). There is deliberately no delete: an edge you "
            "do not want is rejected, not removed."
        ),
    )
    @guard
    async def manage_relationships(
        action: Annotated[
            str,
            Field(description="suggest | seed | declare | confirm | reject."),
        ],
        dataset_id: Annotated[
            str,
            Field(description="Dataset that owns the relationship (the referencing side)."),
        ],
        relationship_id: Annotated[
            str | None,
            Field(description="Relationship UUID. Required for confirm and reject."),
        ] = None,
        from_column: Annotated[
            str | None,
            Field(description="declare: the referencing column, e.g. orders.customer_id -> 'customer_id'."),
        ] = None,
        to_column: Annotated[
            str | None,
            Field(description="declare: the referenced column on the target sheet."),
        ] = None,
        from_sheet: Annotated[
            str | None,
            Field(description="declare: sheet holding from_column. Required on a multi-sheet version."),
        ] = None,
        to_sheet: Annotated[
            str | None,
            Field(description="declare: sheet holding to_column. Required on a multi-sheet version."),
        ] = None,
        to_dataset_id: Annotated[
            str | None,
            Field(description="declare: target dataset for a cross-dataset edge. Defaults to this dataset."),
        ] = None,
        confirmed: Annotated[
            bool,
            Field(description="declare: confirm immediately so the edge can drive a join. Set false to leave it for review."),
        ] = True,
    ) -> str:
        verb = _require(action, RELATIONSHIP_ACTIONS)

        if verb == "suggest":
            payload = await ctx.client.post(
                f"/datasets/{dataset_id}/relationships/suggest", {}, sync=True
            )
            rows = payload.get("relationships") or []
            return clamp(
                render.join(
                    render.fields(
                        [
                            ("column_pairs_examined", payload.get("pairs_examined")),
                            ("new_suggestions", payload.get("suggested")),
                        ]
                    ),
                    render.section(
                        "Suggested relationships (awaiting review)",
                        render.table(_relationship_rows(rows)),
                    ),
                    "Nothing here can drive a join yet. Confirm the ones that are real "
                    "with action='confirm', and reject the rest — a rejected edge is not "
                    "proposed again. Confidence blends name similarity, how much of the "
                    "referencing column's values exist in the target, and how unique the "
                    "target is.",
                ),
                60_000,
                hint="Confirm the edges you need one at a time by relationship_id.",
            )

        if verb == "seed":
            payload = await ctx.client.post(f"/datasets/{dataset_id}/relationships/seed", {})
            rows = payload.get("relationships") or []
            return render.join(
                render.fields([("created_or_refreshed", payload.get("created"))]),
                render.table(_relationship_rows(rows)),
                "Seeding is idempotent: re-running refreshes the evidence but never "
                "overrides a status someone has already set. Seeded edges start as "
                "suggestions — confirm one before joining on it. If nothing was created, "
                "this dataset has no enabled foreign_key quality rules; use "
                "action='suggest' or action='declare' instead.",
            )

        if verb == "declare":
            _needs(from_column, "from_column", "action='declare'")
            _needs(to_column, "to_column", "action='declare'")
            row = await ctx.client.post(
                f"/datasets/{dataset_id}/relationships",
                {
                    "from_sheet": from_sheet,
                    "from_column": from_column,
                    "to_dataset_id": to_dataset_id,
                    "to_sheet": to_sheet,
                    "to_column": to_column,
                    "confirmed": confirmed,
                },
            )
            return render.join(
                "Declared this relationship:",
                render.table(_relationship_rows([row])),
                render.fields([("created_at", row.get("created_at"))]),
                (
                    "Status is 'confirmed', so join_datasets can use this relationship_id now."
                    if row.get("status") == "confirmed"
                    else "Status is not yet 'confirmed', so a join will refuse it — call "
                    "action='confirm' with this relationship_id first."
                ),
                "Both endpoints were validated against the current schemas; the columns "
                "shown are the normalized names the service resolved to.",
            )

        # confirm / reject
        _needs(relationship_id, "relationship_id", f"action='{verb}'")
        row = await ctx.client.post(
            f"/datasets/{dataset_id}/relationships/{relationship_id}/{verb}", {}
        )
        tail = (
            "This edge can now drive a join — pass its relationship_id to join_datasets."
            if verb == "confirm"
            else "Discovery will not propose this pairing again. It is turned down, not "
            "deleted, and can be confirmed later if it turns out to be real."
        )
        return render.join(
            f"Relationship {verb}ed:",
            render.table(_relationship_rows([row])),
            render.section("Evidence", _evidence_line(row)),
            render.fields([("reviewed_by", row.get("reviewed_by"))]),
            tail,
        )

    @server.tool(
        name="join_datasets",
        description=(
            "Join two sheets on a confirmed relationship — the only way to combine "
            "sheets across datasets. Use it when the answer needs columns from both "
            "sides and run_sql cannot reach them because they live in different "
            "datasets.\n\n"
            "ALWAYS run action='preview' first. The preview persists nothing, costs "
            "nothing, and is the only thing that will tell you the join is about to "
            "explode: it measures duplicate keys on each side, whether the join is "
            "many-to-many, the estimated output row count and expansion factor, the "
            "percentage of rows on each side with no match, and which column names "
            "collide — plus a five-row sample of the actual result. A join that "
            "silently triples the left side will make every later SUM wrong.\n\n"
            "action='execute' CHANGES STATE: it materializes the join into a "
            "join_output artifact and records a run. Read the result with read_artifact "
            "using the returned sample_file, or turn it into a real dataset with "
            "publish_result. Execute requires the relationship to be CONFIRMED and "
            "returns a 409 if it is not (preview does not — you can measure a suggested "
            "edge before deciding). The join keys come from the relationship; there is "
            "no key parameter."
        ),
    )
    @guard
    async def join_datasets(
        action: Annotated[str, Field(description="preview | execute.")],
        relationship_id: Annotated[
            str,
            Field(description="A relationship from manage_relationships. It supplies both sheets and both keys."),
        ],
        how: Annotated[
            str,
            Field(description="'inner' keeps only matching rows; 'left' keeps every row of the referencing side."),
        ] = "inner",
        left_version: Annotated[
            int | None,
            Field(description="Version of the referencing dataset. Defaults to its current version."),
        ] = None,
        right_version: Annotated[
            int | None,
            Field(description="Version of the target dataset. Defaults to its current version."),
        ] = None,
        select_columns: Annotated[
            list[str] | None,
            Field(description="Project the joined result down to these output column names. Omit to keep every column."),
        ] = None,
    ) -> str:
        verb = _require(action, JOIN_ACTIONS)
        if how not in {"inner", "left"}:
            raise ToolError(f"how must be 'inner' or 'left', got {how!r}.")
        spec = {
            "relationship_id": relationship_id,
            "how": how,
            "left_version": left_version,
            "right_version": right_version,
            "select_columns": select_columns,
        }

        if verb == "preview":
            payload = await ctx.client.post("/joins/preview", spec)
            warnings = payload.get("warnings") or {}
            relationship = payload.get("relationship") or {}
            columns = payload.get("output_columns") or []
            return clamp(
                render.join(
                    render.table(_relationship_rows([relationship])),
                    render.section(f"Pre-flight ({how} join)", _warning_block(warnings)),
                    _join_advice(warnings),
                    render.section("Output columns", ", ".join(columns)),
                    render.section("Sample", render.table(payload.get("preview") or [], columns)),
                    "Nothing was written — this was a measurement only. Re-run with "
                    "action='execute' when the numbers look right, narrowing with "
                    "select_columns if the output is wide.",
                ),
                60_000,
                hint="Pass select_columns to narrow the sample.",
            )

        payload = await ctx.client.post("/joins/execute", spec)
        warnings = payload.get("warnings") or {}
        columns = payload.get("output_columns") or []
        return clamp(
            render.join(
                "Join executed. Created:",
                render.fields(
                    [
                        ("run_id", payload.get("run_id")),
                        ("sample_file", payload.get("sample_file")),
                        ("row_count", payload.get("row_count")),
                        ("how", how),
                        ("columns", len(columns)),
                    ]
                ),
                render.table(_relationship_rows([payload.get("relationship") or {}])),
                render.section("Measured", _warning_block(warnings)),
                _join_advice(warnings),
                render.section("Output columns", ", ".join(columns)),
                "The full result is the artifact named by sample_file — read it with "
                "read_artifact (project columns, filter, page) rather than asking for it "
                "whole. Pass run_id to publish_result with source='join' to turn it into "
                "a real dataset or version.",
            ),
            60_000,
            hint="Read the output with read_artifact instead.",
        )

    @server.tool(
        name="transform_data",
        description=(
            "Build and run a saved transformation pipeline over one sheet: clean, "
            "reshape, filter, deduplicate and compute new columns as an ordered list of "
            "declarative steps. Use it when the work is repeatable data preparation "
            "rather than a one-off question — a pipeline is stored, re-runnable against "
            "whatever version it targets, and publishable as a dataset. For a single "
            "ad-hoc answer, run_sql or aggregate is cheaper.\n\n"
            "Actions: 'create' saves a pipeline (CHANGES STATE; the steps are compiled "
            "against the sheet's schema first, so an unknown column or a type mismatch "
            "fails here rather than at run time; max 50 steps, expressions nest at most "
            "12 deep). 'preview' is a DRY RUN that persists nothing — no run, no "
            "artifact — and is the right way to check a pipeline does what you meant; it "
            "returns 50 rows by default (max 500) and is APPROXIMATE, because it samples "
            "the source instead of scanning it, so counts and filters on rare values are "
            "not representative. 'run' CHANGES STATE: it executes over the whole sheet "
            "and writes a transform_output artifact. 'inspect' reads one run back with "
            "its auto-profile and its drift against the source sheet, and changes "
            "nothing.\n\n"
            "Create and run need write permission on the dataset. The source version is "
            "never modified — output only ever lands in a new artifact. There is no "
            "delete: a pipeline you no longer want is simply not run."
        ),
    )
    @guard
    async def transform_data(
        action: Annotated[str, Field(description="create | preview | run | inspect.")],
        dataset_id: Annotated[str, Field(description="Dataset the pipeline belongs to.")],
        definition_id: Annotated[
            str | None,
            Field(description="Saved transformation UUID, returned by action='create'. Required for preview and run."),
        ] = None,
        run_id: Annotated[
            str | None,
            Field(description="Transformation run UUID, returned by action='run'. Required for inspect."),
        ] = None,
        name: Annotated[
            str | None, Field(description="create: name for the saved pipeline.")
        ] = None,
        description: Annotated[
            str | None, Field(description="create: what the pipeline is for.")
        ] = None,
        sheet: Annotated[
            str | None,
            Field(description="create: sheet the pipeline reads. Required when the version has several sheets. Pinned by logical identity, so a rename does not break it."),
        ] = None,
        steps: Annotated[
            list[dict[str, Any]] | None,
            Field(description=STEPS_HELP),
        ] = None,
        version_selector: Annotated[
            dict[str, Any] | None,
            Field(description=VERSION_SELECTOR_HELP),
        ] = None,
        rows: Annotated[
            int,
            Field(description="preview: rows sampled from the source before the pipeline runs (1-500).", ge=1, le=500),
        ] = 50,
    ) -> str:
        verb = _require(action, TRANSFORM_ACTIONS)

        if verb == "create":
            _needs(name, "name", "action='create'")
            if steps is None:
                raise ToolError(
                    "steps is required when action='create'. An empty list is allowed "
                    "but produces a pipeline that copies the sheet unchanged."
                )
            payload = await ctx.client.post(
                f"/datasets/{dataset_id}/transformations",
                {
                    "name": name,
                    "description": description,
                    "sheet": sheet,
                    "version_selector": version_selector or {"mode": "current"},
                    "steps": steps,
                },
            )
            saved = payload.get("steps") or []
            return render.join(
                "Transformation saved. Created:",
                render.fields(
                    [
                        ("definition_id", payload.get("id")),
                        ("name", payload.get("name")),
                        ("sheet_key", payload.get("sheet_key")),
                        ("steps", len(saved)),
                        ("step_types", ", ".join(str(s.get("type")) for s in saved)),
                        ("runs_against", _selector_text(payload.get("version_selector"))),
                        ("created_at", payload.get("created_at")),
                    ]
                ),
                "It compiled cleanly against the sheet schema. Dry-run it with "
                "action='preview' before action='run' — preview costs nothing and shows "
                "the output columns the compiler folded.",
            )

        if verb == "preview":
            _needs(definition_id, "definition_id", "action='preview'")
            payload = await ctx.client.post(
                f"/datasets/{dataset_id}/transformations/{definition_id}/preview",
                {},
                rows=rows,
            )
            columns = payload.get("columns") or []
            schema = [
                {
                    "column": c.get("normalized_name") or c.get("name"),
                    "dtype": c.get("dtype"),
                }
                for c in payload.get("output_schema") or []
            ]
            data = payload.get("rows") or []
            return clamp(
                render.join(
                    render.fields(
                        [
                            ("sheet", payload.get("sheet_name")),
                            ("version", payload.get("version_number")),
                            ("output_columns", len(columns)),
                        ]
                    ),
                    render.section("Output schema", render.table(schema)),
                    render.section("Rows", render.table(data, columns)),
                    render.count_note(len(data), None),
                    "Dry run: nothing was written, no run was recorded, no artifact "
                    "exists. These rows come from a SAMPLE of the source, so they are "
                    "approximate — row counts and rare values here do not describe the "
                    "whole sheet. Use action='run' for the real thing."
                    if payload.get("approximate", True)
                    else "Dry run: nothing was written.",
                ),
                60_000,
                hint="Lower `rows`, or add a select step to the pipeline.",
            )

        if verb == "run":
            _needs(definition_id, "definition_id", "action='run'")
            payload = await ctx.client.post(
                f"/datasets/{dataset_id}/transformations/{definition_id}/run", {}, sync=True
            )
            summary = payload.get("result_summary") or {}
            return render.join(
                "Transformation run complete. Created:",
                render.fields(
                    [
                        ("run_id", payload.get("id")),
                        ("status", payload.get("status")),
                        ("sample_file", summary.get("sample_file")),
                        ("source_rows", summary.get("source_row_count")),
                        ("output_rows", summary.get("row_count")),
                        ("output_columns", ", ".join(summary.get("output_columns") or [])),
                        ("sheet", summary.get("sheet")),
                        ("version", summary.get("version_number")),
                        ("artifact_id", payload.get("artifact_id")),
                        ("error", payload.get("error")),
                    ]
                ),
                "The output is a transform_output artifact — read it with read_artifact "
                "using sample_file. action='inspect' on this run_id adds the output "
                "profile and the drift against the source sheet; publish_result with "
                "source='transformation' turns it into a dataset or a new version. The "
                "source version was not touched.",
            )

        # inspect
        _needs(run_id, "run_id", "action='inspect'")
        payload = await ctx.client.get(
            f"/datasets/{dataset_id}/transformations/runs/{run_id}"
        )
        summary = payload.get("result_summary") or {}
        profile = payload.get("output_profile") or {}
        drift = payload.get("source_drift") or {}

        profile_rows = [
            {
                "column": c.get("name"),
                "dtype": c.get("dtype"),
                "nulls_pct": c.get("null_percent"),
                "distinct": c.get("unique_count"),
                "min": c.get("min"),
                "max": c.get("max"),
                "mean": c.get("mean"),
            }
            for c in (profile.get("columns") or [])
        ]
        drift_rows = [
            {
                "column": c.get("column"),
                "null_pct_delta": c.get("null_percent_delta"),
                "distinct_delta": c.get("unique_count_delta"),
                "mean_delta": c.get("mean_delta"),
                "std_delta": c.get("std_delta"),
            }
            for c in (drift.get("columns") or [])
            if any(
                c.get(k) not in (None, 0)
                for k in ("null_percent_delta", "unique_count_delta", "mean_delta", "std_delta")
            )
        ]
        drift_head = render.fields(
            [
                ("source_rows", drift.get("from_row_count")),
                ("output_rows", drift.get("to_row_count")),
                ("row_delta", drift.get("row_count_delta")),
                ("duplicate_rows_delta", drift.get("duplicate_rows_delta")),
                ("columns_added", ", ".join(drift.get("added_columns") or [])),
                ("columns_removed", ", ".join(drift.get("removed_columns") or [])),
            ]
        )
        return clamp(
            render.join(
                render.fields(
                    [
                        ("run_id", payload.get("id")),
                        ("definition_id", payload.get("definition_id")),
                        ("status", payload.get("status")),
                        ("mode", payload.get("mode")),
                        ("sample_file", summary.get("sample_file")),
                        ("output_rows", summary.get("row_count")),
                        ("started_at", payload.get("started_at")),
                        ("completed_at", payload.get("completed_at")),
                        ("error", payload.get("error")),
                    ]
                ),
                render.section("Output profile", render.table(profile_rows))
                if profile_rows
                else "No output profile was recorded — profiling is best-effort and is "
                "skipped for oversized outputs.",
                render.section("Drift vs source sheet", drift_head) if drift else "",
                render.section("Changed columns", render.table(drift_rows)) if drift_rows else "",
                "This action only reads. Nothing about the run changed.",
            ),
            60_000,
            hint="The full output rows are in the sample_file artifact — use read_artifact.",
        )

    @server.tool(
        name="publish_result",
        description=(
            "Turn a computed output into a first-class dataset or a new version of an "
            "existing one, so it can be searched, described, queried and versioned like "
            "any other data. Use it when a join, a transformation or a saved analytics "
            "run has produced something worth keeping rather than a one-off answer; for "
            "a result you only need to read once, read_artifact is enough.\n\n"
            "THIS TOOL CHANGES STATE, but it is strictly additive: mode='new_dataset' "
            "creates a new dataset (it refuses rather than shadow an existing name), and "
            "mode='new_version' appends a version to the target dataset. Nothing is ever "
            "overwritten and no version is ever replaced — versions are immutable, and "
            "the source data behind the run is untouched. Every publish records lineage, "
            "so the result can be traced back to its parents (a join records BOTH sides).\n\n"
            "Pick `source` to match where the run came from: 'analytics' for a saved "
            "analytics definition run, 'transformation' for transform_data action='run', "
            "'join' for join_datasets action='execute'. Needs write permission on the "
            "dataset being written to. Publishing does not delete the artifact or the "
            "run — those remain readable."
        ),
    )
    @guard
    async def publish_result(
        source: Annotated[
            str,
            Field(description="analytics | transformation | join — which kind of run produced run_id."),
        ],
        run_id: Annotated[
            str,
            Field(description="Run UUID: from transform_data action='run', join_datasets action='execute', or an analytics run."),
        ],
        mode: Annotated[
            str,
            Field(description="'new_dataset' creates a separate dataset; 'new_version' appends a version to the existing one."),
        ] = "new_dataset",
        dataset_id: Annotated[
            str | None,
            Field(description="Dataset that owns the run. Required for source='analytics' and source='transformation'. For source='join' it is optional and names the target of new_version mode (defaults to the join's left side)."),
        ] = None,
        name: Annotated[
            str | None,
            Field(description="Name for the new dataset (new_dataset mode only). Defaults to the source name plus a suffix. Must not collide with an existing dataset in the team."),
        ] = None,
    ) -> str:
        kind = _require(source, PUBLISH_SOURCES, name="source")
        if mode not in {"new_dataset", "new_version"}:
            raise ToolError(
                f"mode must be 'new_dataset' or 'new_version', got {mode!r}."
            )
        body: dict[str, Any] = {"mode": mode, "name": name}

        if kind == "join":
            body["dataset_id"] = dataset_id
            path = f"/joins/{run_id}/publish"
        else:
            _needs(dataset_id, "dataset_id", f"source='{kind}'")
            path = (
                f"/datasets/{dataset_id}/analytics/runs/{run_id}/publish"
                if kind == "analytics"
                else f"/datasets/{dataset_id}/transformations/runs/{run_id}/publish"
            )

        payload = await ctx.client.post(path, body)
        lineage_note = (
            "Both parents of the join are recorded in lineage, so this dataset can be "
            "traced back to each source."
            if kind == "join"
            else "Lineage records the version this was derived from."
        )
        return render.join(
            "Published. Created:",
            render.fields(
                [
                    ("dataset_id", payload.get("dataset_id")),
                    ("dataset_name", payload.get("dataset_name")),
                    ("version_number", payload.get("version_number")),
                    ("version_id", payload.get("version_id")),
                    ("mode", payload.get("mode")),
                ]
            ),
            lineage_note
            + " Nothing was overwritten: "
            + (
                "this is a brand new dataset."
                if payload.get("mode") == "new_dataset"
                else "the earlier versions of this dataset are unchanged and still readable."
            ),
            "Use describe_dataset on the dataset_id above to see the published schema.",
        )

"""The 60,000-character ceiling is a property of the tool surface, not of the
tools that remembered to apply it.

``MAX_RESPONSE_CHARS`` was declared in ``tools/look.py`` and applied by hand in
``run_sql``, ``read_artifact`` and four tools in ``tools/context.py``. Everything
else returned whatever it rendered. That list of exceptions is not the harmless
one it looks like:

* ``query_rows`` takes ``limit`` up to 1000 and projects every column by
  default. One page of a sheet of long text columns is megabytes.
* ``aggregate`` and ``pivot`` take ``limit`` up to 1000 rows of arbitrary width,
  and a pivot's width is the cardinality of the pivot dimension — up to 200
  columns.
* ``list_artifacts`` renders up to 1000 rows.
* ``describe_dataset`` renders every column of every sheet of a workbook.

An oversized tool result is not a slow response; it is the model's whole context
spent before it reads a word, and it is unrecoverable in the sense that matters
— the model cannot ask for less after the fact. So the ceiling moved into
``guard``, which every one of the 27 tools already wears.

The one thing the move must not do is re-clamp a tool that clamped itself.
``run_sql`` and ``read_artifact`` deliberately overshoot the budget by a few
hundred characters to keep their artifact handle and their ``offset=`` hint
outside the truncated region; a second clamp at the same budget would delete
precisely those, and the marker explaining the cut with them.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.features.mcp.tools import _common, artifacts, compute, look, orient
from mcp_harness import (
    FakeAnalyticsClient,
    ToolSet,
    page,
    query_page,
    register_tools,
    sheet,
    version,
)

CEILING = _common.MAX_RESPONSE_CHARS
WIDE_CELL = "x" * 400


def wide_rows(count: int, cols: int = 12) -> list[dict[str, Any]]:
    """Rows far past the ceiling: 12 columns of 400 characters is ~5kB a row."""
    return [{f"c{c}": f"{r}-{c}-{WIDE_CELL}" for c in range(cols)} for r in range(count)]


@pytest.fixture
def client() -> FakeAnalyticsClient:
    fake = FakeAnalyticsClient()
    fake.on_get("/datasets/ds-1/versions", page([version(version_number=4)]))
    return fake


async def test_query_rows_is_held_to_the_ceiling_like_every_other_tool(client):
    """``limit`` is 1000 and the default projection is every column, so the
    largest legal ``query_rows`` call is bounded only by the sheet. It carried no
    clamp at all: the response went out at whatever size the rows happened to be,
    and the caller discovered the size by losing the conversation."""
    tools: ToolSet = register_tools(look, client)
    client.on_post(
        "/datasets/ds-1/versions/4/sheets/q1/query", query_page(wide_rows(200))
    )

    out = await tools["query_rows"](dataset_id="ds-1", sheet="q1", limit=1000)

    assert len(out) < CEILING + 500
    assert "[response truncated at 60,000 characters." in out
    assert "Narrow the request" in out


async def test_a_wide_pivot_is_held_to_the_ceiling(client):
    """A pivot's width is the cardinality of the pivot dimension — the service
    allows 200 distinct values — multiplied by the value specs. Wide by
    construction is exactly the shape that needs a ceiling."""
    from app.features.data_accelerator.schemas import PivotResponse

    rows = wide_rows(200)
    tools: ToolSet = register_tools(compute, client)
    client.on_post(
        "/pivot",
        PivotResponse(
            success=True, original_count=100_000, row_count=len(rows),
            columns=list(rows[0]), pivot_columns=list(rows[0]), data=rows,
        ).model_dump(mode="json"),
    )

    out = await tools["pivot"](
        dataset_id="ds-1", rows=["region"], columns="quarter",
        values=[{"column": "amount", "function": "sum"}], limit=1000,
    )

    assert len(out) < CEILING + 500


async def test_a_thousand_artifacts_are_held_to_the_ceiling(client):
    """``list_artifacts`` renders up to 1000 rows of filename, kind, size,
    dataset and timestamp. It is the tool a model calls when it has lost track of
    a handle, which is to say when its context is already under pressure."""
    from app.features.files.schemas import FileEntry

    tools: ToolSet = register_tools(artifacts, client)
    client.on_get(
        "/samples",
        page([
            FileEntry(
                key=f"artifacts/team/ds-1/query_output/{'q' * 200}{i}.parquet",
                filename=f"{'q' * 200}{i}.parquet", size_bytes=2048,
                file_type="query_output", dataset_id="ds-1",
                created_at="2026-08-01T10:00:00Z",
            ).model_dump(mode="json")
            for i in range(1000)
        ]),
    )

    out = await tools["list_artifacts"](limit=1000)

    assert len(out) < CEILING + 500


async def test_describe_dataset_is_held_to_the_ceiling(client):
    """The orient tools were exempt on the theory that metadata is small.
    A workbook of 60 sheets with 300 columns each is metadata, and it is
    megabytes of it — and this is the tool a model is told to call *first*."""
    tools: ToolSet = register_tools(orient, client)
    client.on_get(
        "/datasets/ds-1/sheets",
        page([
            sheet(
                name=f"Sheet {s}", sheet_key=f"sheet{s}",
                columns=[
                    {"name": f"column_with_a_long_name_{c}",
                     "normalized_name": f"column_with_a_long_name_{c}",
                     "dtype": "string", "position": c}
                    for c in range(300)
                ],
            )
            for s in range(60)
        ]),
    )
    client.on_get("/datasets/ds-1/versions", page([version(version_number=4)]))

    out = await tools["describe_dataset"](dataset_id="ds-1")

    assert len(out) < CEILING + 500
    assert "[response truncated at 60,000 characters." in out


async def test_a_response_that_fits_is_returned_verbatim_with_no_marker(client):
    """The overwhelming majority of calls. A ceiling that leaves a fingerprint on
    ordinary output would train the model to expect truncation everywhere, and
    the marker would stop meaning anything on the call where it is true."""
    tools: ToolSet = register_tools(look, client)
    client.on_post(
        "/datasets/ds-1/versions/4/sheets/q1/query",
        query_page([{"region": "EU", "amount": 3}]),
    )

    out = await tools["query_rows"](dataset_id="ds-1", sheet="q1")

    assert "[response truncated" not in out
    assert out.endswith("1 rows shown.")


async def test_a_tool_that_clamped_itself_is_not_clamped_a_second_time(client):
    """``run_sql`` puts its artifact handle *outside* its own clamp on purpose,
    which leaves the finished string a few hundred characters over the budget. A
    blanket outer clamp at the same budget would cut the handle off again — and
    would cut off the marker that says the result was truncated, leaving a
    response that is silently partial. The inner clamp's specific hint is also
    better than the generic one, so the first clamp wins."""
    from mcp_harness import sql_result

    tools: ToolSet = register_tools(look, client)
    client.on_post(
        "/datasets/ds-1/versions/4/sql",
        sql_result(wide_rows(400), truncated=True, result_file="query_output_ab12.parquet"),
    )

    out = await tools["run_sql"](dataset_id="ds-1", sql="SELECT * FROM q1", max_rows=500)

    assert "Artifact: query_output_ab12.parquet (read with read_artifact)." in out
    assert "then read the artifact" in out
    assert "Narrow the request" not in out
    assert out.count("[response truncated at") == 1


async def test_the_ceiling_constant_is_shared_rather_than_redeclared(client):
    """It was declared in ``look.py`` and re-declared in ``context.py``, and both
    copies were the number 60000 written out twice. Two ceilings that are only
    equal by coincidence is how one of them gets raised alone."""
    from app.features.mcp.tools import context

    assert look.MAX_RESPONSE_CHARS is _common.MAX_RESPONSE_CHARS
    assert context.MAX_RESPONSE_CHARS is _common.MAX_RESPONSE_CHARS


def test_a_body_that_already_carries_a_truncation_marker_is_left_alone():
    """The unit-level statement of the rule above, so the reason survives even if
    ``run_sql`` is rewritten: ``enforce_ceiling`` is a backstop, not a second
    pass, and a body that says it was truncated has already been bounded by
    someone who knew what they were cutting."""
    inner = _common.clamp("z" * 200_000, CEILING, hint="Lower `max_rows`.")
    already = inner + "\n\nArtifact: query_output_ab12.parquet."

    assert _common.enforce_ceiling(already) == already
    assert already.endswith("Artifact: query_output_ab12.parquet.")


async def test_read_artifact_keeps_its_paging_hint_when_the_table_is_truncated(client):
    """``read_artifact`` had ``run_sql``'s old bug: the row count and the
    concrete ``offset=N`` for the next page were rendered *inside* the clamp, so
    a truncated read deleted the two sentences telling the caller it was
    truncated and how to continue. The caller left holding a partial page with no
    stated total and no next offset re-reads from zero, or stops."""
    tools: ToolSet = register_tools(artifacts, client)
    rows = wide_rows(400)
    client.on_get(
        "/samples/query_output_ab12.parquet/data",
        {
            "filename": "query_output_ab12.parquet",
            "columns": [{"name": c, "dtype": "string"} for c in rows[0]],
            "data": rows,
            "total_count": 5000,
            "filtered_count": 5000,
        },
    )

    out = await tools["read_artifact"](filename="query_output_ab12.parquet", limit=500)

    assert "[response truncated at 60,000 characters." in out
    assert "400 of 5000 rows shown." in out
    assert "More rows available — call again with offset=400." in out

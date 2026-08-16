"""What a response has to keep when it hits the 60,000-character ceiling.

``_common.clamp`` is head-keeping: it slices the front of the text and appends a
marker. That is the right shape — a table cut at the front is still readable —
but it makes *ordering* load-bearing. Anything a tool appends after the volume
it is trying to bound is the first thing deleted, and in ``run_sql`` that tail
is the artifact handle: the one string that lets the model chain the result it
was just told it could not see in full. The hint literally says "then read the
artifact" while the sentence naming the artifact is what got cut.

The second thing pinned here is smaller and purely cosmetic, but it is the sort
of thing that goes unnoticed for years: the marker's ``.rstrip()`` binds to a
concatenation that always ends in ``]``, so it never removed the dangling space
it was written to remove. Every hintless truncation in this codebase — and
there are eleven call sites — ended ``characters. ]``.

Unit layer: no service response reaches this logic, only its rendering.
"""

from __future__ import annotations

import pytest

from app.features.mcp.tools import look
from app.features.mcp.tools._common import clamp
from mcp_harness import (
    FakeAnalyticsClient,
    ToolSet,
    page,
    register_tools,
    sql_result,
    version,
)


@pytest.fixture
def client() -> FakeAnalyticsClient:
    return FakeAnalyticsClient()


@pytest.fixture
def tools(client: FakeAnalyticsClient) -> ToolSet:
    return register_tools(look, client)


# ---------------------------------------------------------------------------
# clamp itself
# ---------------------------------------------------------------------------


def test_a_truncation_marker_with_no_hint_has_no_dangling_space_before_the_bracket():
    """Eleven ``clamp`` call sites pass no hint. Each one closed with
    ``characters. ]`` because the ``.rstrip()`` meant to tidy that up was applied
    to a string ending in ``]``, where it could never do anything. Pinning the
    exact ending is the only way to keep the strip honest, since a substring
    assertion on "characters." passes either way."""
    out = clamp("z" * 500, 100)

    assert out.endswith("characters.]")
    assert "characters. ]" not in out


def test_a_truncation_marker_with_a_hint_keeps_exactly_one_space_before_it():
    """The complement: fixing the dangling space must not run the hint into the
    full stop. Both spellings are wrong in the same place, so both need a
    test."""
    out = clamp("z" * 500, 100, hint="Lower `limit`.")

    assert out.endswith("[response truncated at 100 characters. Lower `limit`.]")


def test_text_within_budget_is_returned_untouched_with_no_marker():
    """The overwhelming majority of responses. A marker on an untruncated
    result would tell the model to go fetch a remainder that does not exist."""
    assert clamp("short", 100) == "short"
    assert clamp("z" * 100, 100) == "z" * 100


# ---------------------------------------------------------------------------
# run_sql — the notes must outlive the table
# ---------------------------------------------------------------------------


async def test_the_artifact_handle_survives_the_response_ceiling(tools, client):
    """500 rows of a dozen wide columns is well past 60,000 characters, and
    ``max_rows`` allows exactly that. Before this, the truncation deleted the
    artifact filename, the "showing N of M" count and the 10,000-row cap warning
    — the whole tail — leaving a model that has been told to read an artifact
    whose name it was never given, and whose only remaining option is to re-run
    the same expensive query. The handle is the chaining primitive of this whole
    tool surface; it is the last thing that may be dropped, not the first."""
    wide = [{f"c{c}": f"value-{r}-{c}-{'x' * 40}" for c in range(12)} for r in range(600)]
    client.on_get("/datasets/ds-1/versions", page([version(version_number=2)]))
    client.on_post(
        "/datasets/ds-1/versions/2/sql",
        sql_result(wide, truncated=True, result_file="query_output_ab12.parquet"),
    )

    out = await tools["run_sql"](
        dataset_id="ds-1", sql="SELECT * FROM orders", max_rows=500
    )

    assert "[response truncated at 60,000 characters." in out
    assert "Artifact: query_output_ab12.parquet (read with read_artifact)." in out
    assert "Showing 500 of 600 returned rows." in out
    assert "The service capped this result at 10,000 rows" in out


async def test_the_ceiling_still_bounds_the_truncated_response(tools, client):
    """Moving the notes outside the clamped text is only acceptable because the
    notes are short and fixed — one filename and two canned sentences. If a note
    ever grows unbounded, this ceiling is the thing that would stop catching it,
    so the overshoot is pinned rather than assumed."""
    wide = [{f"c{c}": f"value-{r}-{c}-{'x' * 40}" for c in range(12)} for r in range(600)]
    client.on_get("/datasets/ds-1/versions", page([version(version_number=2)]))
    client.on_post(
        "/datasets/ds-1/versions/2/sql",
        sql_result(wide, truncated=True, result_file="query_output_ab12.parquet"),
    )

    out = await tools["run_sql"](
        dataset_id="ds-1", sql="SELECT * FROM orders", max_rows=500
    )

    assert len(out) < look.MAX_RESPONSE_CHARS + 500


async def test_a_result_that_fits_reads_the_same_as_it_always_did(tools, client):
    """The untruncated path is the common one and its layout is unchanged:
    header fields, then the table, then the notes, joined by blank lines. A
    refactor that moved the notes must not have moved them relative to the
    table."""
    client.on_get("/datasets/ds-1/versions", page([version(version_number=2)]))
    client.on_post(
        "/datasets/ds-1/versions/2/sql",
        sql_result([{"region": "US", "n": 3}], result_file="query_output_1.parquet"),
    )

    out = await tools["run_sql"](dataset_id="ds-1", sql="SELECT * FROM orders")

    assert "[response truncated" not in out
    assert out.index("region | n") < out.index("Artifact: query_output_1.parquet")

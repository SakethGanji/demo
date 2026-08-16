"""What ``pivot`` has to do with the two totals maps the service hands back.

``PivotResponse`` carries two of them and they answer different questions.
``totals`` is a grand total: the service re-runs the aggregation with no group
keys at all, over every row the filter matched, so it is *not* a subtotal of the
table above it and is unrelated to the row cap. ``column_totals`` is one entry
per output column, re-aggregated across the pivot dimension, and it exists only
because the caller passed ``include_column_totals=True``.

Two failures were possible here and both were live:

* the tool forwarded ``include_column_totals`` and then never rendered
  ``column_totals``. The service computed them, charged the caller a second
  aggregation pass for them, and the tool dropped the result. A flag that
  silently does nothing is worse than one that errors — the model believes it
  asked and believes the answer is "there are none".
* ``totals`` was rendered as bare ``alias: value`` lines immediately under the
  pivot table, with no heading. Directly beneath a table that has just been
  capped at ``limit`` rows, ``total: 12345`` reads as the sum of those rows. It
  is not, and a model that reports it as such is off by whatever the cap cut.
  ``aggregate`` has said "over all groups, not just the rows shown" since the
  service started re-aggregating; pivot shares the engine and had to share the
  label.

Unit layer via ``mcp_harness.FakeAnalyticsClient``: the payloads are dumped from
``PivotResponse`` itself, so a field rename breaks this file loudly.
"""

from __future__ import annotations

from typing import Any, Sequence

import pytest

from app.features.data_accelerator.schemas import PivotResponse
from app.features.mcp.tools import compute
from mcp_harness import FakeAnalyticsClient, ToolSet, page, register_tools, version

SUM_AMOUNT = [{"column": "amount", "function": "sum", "alias": "total"}]


@pytest.fixture
def client() -> FakeAnalyticsClient:
    fake = FakeAnalyticsClient()
    fake.on_get("/datasets/ds-1/versions", page([version(version_number=4)]))
    return fake


@pytest.fixture
def tools(client: FakeAnalyticsClient) -> ToolSet:
    return register_tools(compute, client)


def pivot_result(
    rows: Sequence[dict[str, Any]] = (), *, columns: Sequence[str] | None = None,
    pivot_columns: Sequence[str] = (), totals: dict[str, Any] | None = None,
    column_totals: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """``POST /pivot`` (``PivotResponse``), dumped as FastAPI would."""
    listed = list(rows)
    return PivotResponse(
        success=True, original_count=1000, row_count=len(listed),
        columns=list(columns) if columns is not None else (list(listed[0]) if listed else []),
        pivot_columns=list(pivot_columns), data=listed, totals=totals,
        column_totals=column_totals,
    ).model_dump(mode="json")


async def test_the_column_totals_the_caller_asked_for_are_actually_rendered(tools, client):
    """``include_column_totals=True`` travels to the service, the service runs a
    second aggregation across the pivot dimension to produce them, and the tool
    used to render nothing at all. The caller's question — "what did each quarter
    come to across every region, not just the regions on this page?" — was
    answered by the service and thrown away in the renderer, and the model's only
    reading of a missing section is that there was nothing to report."""
    client.on_post(
        "/pivot",
        pivot_result(
            [{"region": "EU", "Q1": 10, "Q2": 20}],
            columns=["region", "Q1", "Q2"], pivot_columns=["Q1", "Q2"],
            totals={"total": 999},
            column_totals={"Q1": 4100, "Q2": 5200},
        ),
    )

    out = await tools["pivot"](
        dataset_id="ds-1", rows=["region"], columns="quarter",
        values=SUM_AMOUNT, include_column_totals=True, limit=1,
    )

    assert "## Column totals (per output column, over all rows)" in out
    assert "Q1: 4100" in out
    assert "Q2: 5200" in out


async def test_the_pivot_grand_total_is_labelled_as_covering_more_than_the_page(
    tools, client
):
    """The one-line version: ``total: 12345`` sitting under a table capped at one
    row is a number with no stated scope, and the nearest thing to it on the page
    is the row that was shown. The service computed it over all 1000 scanned
    rows. Without the heading a model reports "EU totals 12345" — a statement
    about one region built from a number covering every region."""
    client.on_post(
        "/pivot",
        pivot_result(
            [{"region": "EU", "Q1": 10}],
            columns=["region", "Q1"], pivot_columns=["Q1"],
            totals={"total": 12345},
        ),
    )

    out = await tools["pivot"](
        dataset_id="ds-1", rows=["region"], columns="quarter", values=SUM_AMOUNT, limit=1,
    )

    assert "## Totals (over all groups, not just the rows shown)" in out
    assert "total: 12345" in out
    # The heading must precede the number, or it labels nothing.
    assert out.index("## Totals") < out.index("total: 12345")


async def test_column_totals_requested_without_a_pivot_dimension_say_why_none_came_back(
    tools, client
):
    """``column_totals`` are per *pivot* column, so the service returns none when
    the request has no ``columns`` dimension — a plain grouped table is a
    documented pivot mode. Rendering silence there leaves the caller believing
    the totals are zero or that the flag failed. Naming the reason turns a dead
    end into the next call."""
    client.on_post(
        "/pivot",
        pivot_result([{"region": "EU", "total": 9}], totals={"total": 9}),
    )

    out = await tools["pivot"](
        dataset_id="ds-1", rows=["region"], values=SUM_AMOUNT, include_column_totals=True,
    )

    assert "No column totals: they are computed per pivot column" in out
    assert "## Column totals" not in out


async def test_a_pivot_with_no_totals_at_all_grows_no_empty_headings(tools, client):
    """Every value spec can be ``display='pct'``, in which case the service
    computes no grand total and no column totals. Sections headed "Totals" with
    nothing under them read as a computation that failed, and ``render.section``
    is only empty-safe if the caller checks first."""
    client.on_post(
        "/pivot",
        pivot_result([{"region": "EU", "Q1": 10}], columns=["region", "Q1"]),
    )

    out = await tools["pivot"](
        dataset_id="ds-1", rows=["region"], columns="quarter", values=SUM_AMOUNT,
    )

    assert "Totals" not in out
    assert "region | Q1" in out

"""``app/features/mcp/tools/compute.py`` — the five tools that make the service
do the arithmetic: ``profile_column``, ``aggregate``, ``pivot``,
``check_quality`` and ``compare_versions``.

What is under test is everything that happens *around* the computation, which
is the whole of what this layer contributes:

* the request actually sent — which endpoint, which version, and which optional
  keys were left off rather than sent as ``null``;
* the relabelling each tool does on the way back (``percent`` → ``pct``,
  ``bin_start`` → ``from``, ``non_null_count`` derived when the stored profile
  predates the field), because a model reads the label and not the schema;
* the caveats a bounded answer is only honest with — truncation, a saved
  artifact, a total that was deliberately *not* computed;
* the error translation, asserted on its actionable payload. "aggregate raised"
  is not the guarantee; "aggregate named the columns that do exist" is.

Deliberately not covered here: that the service ever produces these payloads.
The canned shapes below are built from the service's own response models
(``AggregateResponse``, ``PivotResponse``, ``ColumnExplorerResponse``,
``MissingResponse``, ``DuplicatesResponse``, ``ValidationRunOut``,
``WorkbookDiffResponse``, ``SheetDiffResponse``) so a field rename breaks this
file loudly, but a model that no longer describes reality would not.
``tests/test_mcp_endpoint.py`` is where that claim is made, against real data.

Also not covered: the ``sql-timeout`` / ``select-only`` branches of
``_common.explain``. They belong to ``run_sql`` in ``tools/look.py``; no tool in
this module reaches ``/sql``.
"""

from __future__ import annotations

from typing import Any, Sequence

import pytest
from mcp.server.mcpserver.exceptions import ToolError

from app.features.data_accelerator.schemas import (
    AggregateResponse,
    ColumnTypeChange,
    HistogramBin,
    ModifiedSheet,
    PivotResponse,
    RenameCandidate,
    SheetColumn,
    SheetDiffResponse,
    SheetSummary,
    TopValue,
    WorkbookDiffResponse,
)
from app.features.explorer.schemas import (
    ColumnExplorerResponse,
    ColumnMissing,
    DuplicatesResponse,
    MissingResponse,
)
from app.features.mcp.tools import compute
from app.features.quality.schemas import ValidationRunOut
from app.shared.constants import ALLOWED_AGG_FUNCTIONS
from mcp_harness import (
    FakeAnalyticsClient,
    page,
    problem,
    register_tools,
    sheet_selection_required,
    unknown_column,
    version,
)

# ---------------------------------------------------------------------------
# Payload builders — each dumped from the route's own response_model, for the
# reason the harness gives: a canned dict is a guess, a dumped model is a claim
# the schema keeps honest.
# ---------------------------------------------------------------------------


def _dump(model: Any) -> dict[str, Any]:
    return model.model_dump(mode="json")


def column_stats(
    *, name: str = "amount", dtype: str = "numeric", count: int = 1000,
    null_count: int = 0, non_null_count: int | None = None, unique_count: int = 900,
    top_values: Sequence[tuple[Any, int, float]] = (),
    rare_values: Sequence[tuple[Any, int, float]] = (),
    histogram: Sequence[tuple[float, float, int]] | None = None,
    sheet_name: str = "Q1", examples: Sequence[Any] = (), **extra: Any,
) -> dict[str, Any]:
    """``GET .../columns/{column}`` (``ColumnExplorerResponse``).

    ``top_values``/``rare_values`` are ``(value, count, percent)`` triples —
    note ``percent``, which is the name the tool renames to ``pct``.
    """
    extra.setdefault("null_percent", round(100 * null_count / count, 4) if count else 0.0)
    extra.setdefault("is_candidate_key", False)
    return _dump(
        ColumnExplorerResponse(
            name=name, normalized_name=name, sheet_name=sheet_name, dtype=dtype,
            count=count, null_count=null_count, non_null_count=non_null_count,
            unique_count=unique_count,
            top_values=[TopValue(value=v, count=c, percent=p) for v, c, p in top_values],
            rare_values=[TopValue(value=v, count=c, percent=p) for v, c, p in rare_values],
            examples=list(examples),
            histogram=(
                None if histogram is None
                else [HistogramBin(bin_start=a, bin_end=b, count=c) for a, b, c in histogram]
            ),
            **extra,
        )
    )


def agg_result(
    rows: Sequence[dict[str, Any]] = (), *, columns: Sequence[str] | None = None,
    original_count: int = 1000, group_count: int | None = None,
    totals: dict[str, Any] | None = None, totals_omitted: dict[str, Any] | None = None,
    unavailable_measures: Sequence[str] = (),
    truncated: bool = False, result_file: str | None = None,
) -> dict[str, Any]:
    """``POST /aggregate`` (``AggregateResponse``)."""
    listed = list(rows)
    return _dump(
        AggregateResponse(
            success=True, original_count=original_count,
            group_count=len(listed) if group_count is None else group_count,
            columns=list(columns) if columns is not None else (list(listed[0]) if listed else []),
            data=listed, totals=totals, totals_omitted=totals_omitted,
            unavailable_measures=list(unavailable_measures),
            truncated=truncated, result_file=result_file,
        )
    )


def pivot_result(
    rows: Sequence[dict[str, Any]] = (), *, columns: Sequence[str] | None = None,
    pivot_columns: Sequence[str] = (), original_count: int = 1000,
    row_count: int | None = None, totals: dict[str, Any] | None = None,
    column_totals: dict[str, Any] | None = None,
    unavailable_measures: Sequence[str] = (), truncated: bool = False,
    result_file: str | None = None,
) -> dict[str, Any]:
    """``POST /pivot`` (``PivotResponse``)."""
    listed = list(rows)
    return _dump(
        PivotResponse(
            success=True, original_count=original_count,
            row_count=len(listed) if row_count is None else row_count,
            columns=list(columns) if columns is not None else (list(listed[0]) if listed else []),
            pivot_columns=list(pivot_columns), data=listed, totals=totals,
            column_totals=column_totals,
            unavailable_measures=list(unavailable_measures),
            truncated=truncated, result_file=result_file,
        )
    )


def missing_report(
    columns: Sequence[tuple[str, int, float]] = (), *, sheet_name: str = "Q1",
    row_count: int = 1000, source: str = "profile_run",
) -> dict[str, Any]:
    """``GET .../missing`` (``MissingResponse``); ``columns`` worst-first."""
    return _dump(
        MissingResponse(
            sheet_name=sheet_name, source=source, row_count=row_count,
            columns=[ColumnMissing(column=c, null_count=n, null_percent=p)
                     for c, n, p in columns],
        )
    )


def duplicates_report(
    *, sheet_name: str = "Q1", columns: Sequence[str] = (), exact: bool = True,
    row_count: int = 1000, group_count: int = 0, duplicate_rows: int = 0,
) -> dict[str, Any]:
    """``GET .../duplicates`` (``DuplicatesResponse``)."""
    return _dump(
        DuplicatesResponse(
            sheet_name=sheet_name, columns=list(columns), exact=exact,
            row_count=row_count, group_count=group_count, duplicate_rows=duplicate_rows,
        )
    )


def validation_run(
    *, status: str = "failed", rules_total: int = 12, rules_passed: int = 10,
    rules_failed: int = 2, error_failures: int = 1, warning_failures: int = 1,
    completed_at: str | None = "2026-02-01T10:00:00Z",
) -> dict[str, Any]:
    """One item of ``Page[ValidationRunOut]`` from ``GET .../validations``."""
    return _dump(
        ValidationRunOut(
            id="vr-1", dataset_id="ds-1", dataset_version_id="v-4", status=status,
            rules_total=rules_total, rules_passed=rules_passed, rules_failed=rules_failed,
            error_failures=error_failures, warning_failures=warning_failures,
            started_at="2026-02-01T09:59:00Z", completed_at=completed_at,
        )
    )


def workbook_diff(
    *, added: Sequence[str] = (), removed: Sequence[str] = (),
    modified: Sequence[tuple[str, bool, int | None]] = (),
    unchanged: Sequence[str] = (),
    renames: Sequence[tuple[str, str, str]] = (),
    from_version: int = 1, to_version: int = 2,
) -> dict[str, Any]:
    """``GET .../versions/{from}/diff/{to}`` (``WorkbookDiffResponse``)."""
    def summary(name: str) -> SheetSummary:
        return SheetSummary(name=name, row_count=10, column_count=3)

    return _dump(
        WorkbookDiffResponse(
            dataset_id="ds-1", from_version=from_version, to_version=to_version,
            added=[summary(n) for n in added], removed=[summary(n) for n in removed],
            modified=[ModifiedSheet(sheet_key=n.lower(), from_sheet=n, to_sheet=n,
                                    schema_changed=changed, row_count_delta=delta)
                      for n, changed, delta in modified],
            unchanged=list(unchanged),
            rename_candidates=[RenameCandidate(from_sheet=f, to_sheet=t, confidence=c,
                                               reason="matching schema fingerprint")
                               for f, t, c in renames],
        )
    )


def sheet_diff(
    *, identical: bool = False, added_columns: Sequence[str] = (),
    removed_columns: Sequence[str] = (),
    type_changes: Sequence[tuple[str, str, str]] = (),
    from_row_count: int | None = 100, to_row_count: int | None = 120,
    to_sheet: str = "Q1", from_version: int = 1, to_version: int = 2,
) -> dict[str, Any]:
    """``GET .../versions/{from}/sheets/{s}/diff/{to}`` (``SheetDiffResponse``)."""
    def col(name: str, position: int) -> SheetColumn:
        return SheetColumn(name=name, normalized_name=name, dtype="string", position=position)

    delta = (
        None if from_row_count is None or to_row_count is None
        else to_row_count - from_row_count
    )
    return _dump(
        SheetDiffResponse(
            dataset_id="ds-1", sheet_key=to_sheet.lower(), from_sheet=to_sheet,
            to_sheet=to_sheet, from_version=from_version, to_version=to_version,
            identical=identical,
            added_columns=[col(n, i) for i, n in enumerate(added_columns)],
            removed_columns=[col(n, i) for i, n in enumerate(removed_columns)],
            type_changes=[ColumnTypeChange(column=c, from_dtype=f, to_dtype=t)
                          for c, f, t in type_changes],
            from_row_count=from_row_count, to_row_count=to_row_count,
            row_count_delta=delta,
        )
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

VERSIONS = "/datasets/ds-1/versions"


@pytest.fixture
def client() -> FakeAnalyticsClient:
    """Newest ready version is 4, so every "did it resolve?" assertion is a 4."""
    fake = FakeAnalyticsClient()
    fake.on_get(VERSIONS, page([version(version_number=4), version(version_number=3)]))
    return fake


@pytest.fixture
def tools(client):
    return register_tools(compute, client)


# ---------------------------------------------------------------------------
# profile_column — one column's statistics, relabelled for reading
# ---------------------------------------------------------------------------


async def test_profiling_a_column_of_a_named_sheet_asks_the_sheet_scoped_endpoint(tools, client):
    """The version-scoped path auto-resolves the sheet and 400s on a multi-sheet
    version. Dropping the caller's ``sheet`` on the floor would turn a valid
    request into sheet-selection-required, or worse, profile the wrong sheet."""
    client.on_get(
        "/datasets/ds-1/versions/4/sheets/Q1/columns/amount",
        column_stats(name="amount", sheet_name="Q1", dtype="numeric"),
    )
    out = await tools["profile_column"](dataset_id="ds-1", column="amount", sheet="Q1")

    client.one_call_to("GET", "/datasets/ds-1/versions/4/sheets/Q1/columns/amount")
    assert "column: amount" in out
    assert "sheet: Q1" in out


async def test_profiling_without_a_sheet_leaves_the_resolution_to_the_service(tools, client):
    """No ``sheet`` must produce the *version*-scoped path, not a path with an
    empty sheet segment. The service auto-resolves single-sheet versions there;
    a ``/sheets//columns/x`` URL would 404 on a dataset that is perfectly fine."""
    client.on_get("/datasets/ds-1/versions/4/columns/amount", column_stats())
    await tools["profile_column"](dataset_id="ds-1", column="amount")

    assert client.trace() == [
        ("GET", VERSIONS),
        ("GET", "/datasets/ds-1/versions/4/columns/amount"),
    ]


async def test_an_explicitly_pinned_version_is_never_upgraded_to_the_newest(tools, client):
    """Pinning a version is how a caller compares against history. Resolving the
    newest anyway would answer a different question with total confidence — and
    the version list would not even be consulted, so nothing would hint at it."""
    client.on_get("/datasets/ds-1/versions/2/columns/amount", column_stats())
    await tools["profile_column"](dataset_id="ds-1", column="amount", version=2)

    assert client.calls_to("GET", VERSIONS) == []


async def test_the_most_common_values_are_reported_with_their_share_of_the_sheet(tools, client):
    """A raw count answers "how many"; the percentage answers "is this the bulk
    of the data". The service calls it ``percent`` and the tool renders ``pct`` —
    if that rename silently drops, every value shows a blank share column."""
    client.on_get(
        "/datasets/ds-1/versions/4/columns/status",
        column_stats(name="status", dtype="categorical", unique_count=50,
                     top_values=[("paid", 700, 70.0), ("void", 200, 20.0)]),
    )
    out = await tools["profile_column"](dataset_id="ds-1", column="status")

    assert "## Most common" in out
    assert "value | count | pct" in out
    assert "paid | 700 | 70" in out


async def test_least_common_is_dropped_when_it_would_repeat_most_common_backwards(tools, client):
    """Below the service's top-values cap, "rare" is the same list reversed.
    Printing both doubles the token cost of the profile and invites a model to
    read a coincidence of ordering as a second, independent finding."""
    client.on_get(
        "/datasets/ds-1/versions/4/columns/status",
        column_stats(name="status", unique_count=2,
                     top_values=[("paid", 700, 70.0), ("void", 300, 30.0)],
                     rare_values=[("void", 300, 30.0), ("paid", 700, 70.0)]),
    )
    out = await tools["profile_column"](dataset_id="ds-1", column="status")

    assert "## Most common" in out
    assert "## Least common" not in out


async def test_least_common_survives_when_the_column_has_more_values_than_were_listed(tools, client):
    """The other side of the same rule: with 5,000 distinct values the rare list
    is real evidence (typos, stray sentinels) and must not be suppressed."""
    client.on_get(
        "/datasets/ds-1/versions/4/columns/status",
        column_stats(name="status", unique_count=5000,
                     top_values=[("paid", 700, 70.0)],
                     rare_values=[("pald", 1, 0.1)]),
    )
    out = await tools["profile_column"](dataset_id="ds-1", column="status")

    assert "## Least common" in out
    assert "pald | 1 | 0.1" in out


async def test_a_profile_stored_before_non_null_count_existed_still_reports_non_null(tools, client):
    """``non_null_count`` is nullable so old persisted profiles deserialize. If
    the tool read it bare, every pre-existing profile would report a blank
    non-null count and a model would conclude the column is entirely null."""
    client.on_get(
        "/datasets/ds-1/versions/4/columns/amount",
        column_stats(count=1000, null_count=300, non_null_count=None),
    )
    out = await tools["profile_column"](dataset_id="ds-1", column="amount")

    assert "rows: 1000" in out
    assert "non_null: 700" in out
    assert "nulls: 300" in out


async def test_a_supplied_non_null_count_is_reported_rather_than_recomputed(tools, client):
    """``non_null_count`` is SQL ``COUNT(column)`` and is authoritative; ``count``
    is the sheet row count and can be stale relative to it. Deriving over the
    top of the real measurement would invent a number the service never made."""
    client.on_get(
        "/datasets/ds-1/versions/4/columns/amount",
        column_stats(count=1000, null_count=299, non_null_count=700),
    )
    out = await tools["profile_column"](dataset_id="ds-1", column="amount")

    assert "non_null: 700" in out
    assert "non_null: 701" not in out


async def test_the_histogram_bins_are_labelled_as_the_range_they_cover(tools, client):
    """``bin_start``/``bin_end`` are storage names; a model reading a bounded
    text table needs to see that a row means "from X to Y", not two opaque
    numbers it might mistake for a value and a count."""
    client.on_get(
        "/datasets/ds-1/versions/4/columns/amount",
        column_stats(histogram=[(0.0, 10.0, 40), (10.0, 20.0, 60)]),
    )
    out = await tools["profile_column"](dataset_id="ds-1", column="amount")

    assert "## Histogram" in out
    assert "from | to | count" in out
    assert "0 | 10 | 40" in out


async def test_a_column_with_no_histogram_gets_no_empty_histogram_heading(tools, client):
    """String columns have no histogram. A "## Histogram" heading over "(no
    rows)" costs tokens and reads as a failed computation rather than an
    inapplicable one."""
    client.on_get(
        "/datasets/ds-1/versions/4/columns/name",
        column_stats(name="name", dtype="text", histogram=None, top_values=[]),
    )
    out = await tools["profile_column"](dataset_id="ds-1", column="name")

    assert "## Histogram" not in out
    assert "## Most common" not in out
    assert "column: name" in out


async def test_profiling_an_unknown_column_answers_with_the_columns_that_exist(tools, client):
    """The single most common model error on this tool is a guessed column name.
    One round trip should fix it, which means the rejection has to carry the
    vocabulary — a bare "unknown column" costs a describe_dataset call first."""
    client.on_get(
        "/datasets/ds-1/versions/4/columns/amt",
        unknown_column("amt", ["amount", "amount_usd", "quantity"]),
    )
    with pytest.raises(ToolError) as err:
        await tools["profile_column"](dataset_id="ds-1", column="amt")

    assert "Unknown column: 'amt'" in str(err.value)
    assert "Available columns: amount, amount_usd, quantity" in str(err.value)


async def test_profiling_a_multi_sheet_version_names_the_sheets_to_choose_from(tools, client):
    """sheet-selection-required without the names is a dead end: the model has
    to call describe_dataset and retry. With them it retries immediately."""
    client.on_get(
        "/datasets/ds-1/versions/4/columns/amount",
        sheet_selection_required(["Q1", "Q2", "Notes"]),
    )
    with pytest.raises(ToolError) as err:
        await tools["profile_column"](dataset_id="ds-1", column="amount")

    assert "Available sheets: Q1, Q2, Notes." in str(err.value)


async def test_a_dataset_with_nothing_ready_says_so_instead_of_reporting_no_data(tools, client):
    """An upload still processing and an upload that failed both leave zero
    ready versions. Falling through to a query would report an empty result —
    "there is no such data" — for a dataset that is merely not ready yet."""
    client.on_get(VERSIONS, page([version(version_number=1, status="processing")]))
    with pytest.raises(ToolError) as err:
        await tools["profile_column"](dataset_id="ds-1", column="amount")

    assert "no ready version" in str(err.value)
    assert "still be processing" in str(err.value)


# ---------------------------------------------------------------------------
# aggregate — the tool that exists so a model never adds numbers in context
# ---------------------------------------------------------------------------


async def test_aggregate_sends_one_request_pinned_to_the_resolved_version(tools, client):
    """``/aggregate`` accepts several version selectors; sending the *resolved*
    number is what makes two tool calls in one conversation describe the same
    data even if a new version lands between them. ``return_data`` must be true
    or the rendered table is empty while the call still reports success."""
    client.on_post("/aggregate", agg_result([{"region": "EU", "total": 5}]))
    await tools["aggregate"](
        dataset_id="ds-1", group_by=["region"],
        aggregations=[{"column": "amount", "function": "sum", "alias": "total"}],
        sheet="Q1", filters={"logic": "and", "conditions": []},
        having=[{"column": "total", "op": "gt", "value": 1}],
        sort_by="total", sort_order="asc", limit=25,
    )

    call = client.one_call_to("POST", "/aggregate")
    assert call.body == {
        "dataset_id": "ds-1",
        "version_number": 4,
        "sheet": "Q1",
        "group_by": ["region"],
        "aggregations": [{"column": "amount", "function": "sum", "alias": "total"}],
        "having": [{"column": "total", "op": "gt", "value": 1}],
        "filters": {"logic": "and", "conditions": []},
        "sort_by": "total",
        "sort_order": "asc",
        "limit": 25,
        "return_data": True,
    }


async def test_aggregate_omits_the_optional_keys_rather_than_sending_them_as_null(tools, client):
    """Not cosmetic. ``AggregateRequest.having`` is ``list[HavingCondition]``
    with a list default, so an explicit ``"having": null`` is a 422 — every
    call that did not use HAVING would fail. Absent means "use the default";
    null does not."""
    client.on_post("/aggregate", agg_result([{"region": "EU", "total": 5}]))
    await tools["aggregate"](
        dataset_id="ds-1", group_by=["region"],
        aggregations=[{"column": "amount", "function": "sum"}],
    )

    call = client.one_call_to("POST", "/aggregate")
    assert "having" not in call.body
    assert "filters" not in call.body
    assert "sheet" not in call.body
    assert "sort_by" not in call.body
    # The ones with real defaults still travel, so the service is not guessing.
    assert call.body["sort_order"] == "desc"
    assert call.body["limit"] == 50


async def test_aggregate_renders_the_groups_in_the_column_order_the_service_chose(tools, client):
    """The service returns ``columns`` in group-by-then-aggregation order.
    Rebuilding the header from dict keys would reorder it per row source and
    put the measure before the dimension, which reads as a different table."""
    client.on_post(
        "/aggregate",
        agg_result(
            [{"total": 90, "region": "EU"}, {"total": 10, "region": "US"}],
            columns=["region", "total"], original_count=1000, group_count=2,
        ),
    )
    out = await tools["aggregate"](
        dataset_id="ds-1", group_by=["region"],
        aggregations=[{"column": "amount", "function": "sum", "alias": "total"}],
    )

    assert "rows_scanned: 1000" in out
    assert "groups: 2" in out
    assert "region | total" in out
    assert "EU | 90" in out


async def test_the_totals_are_labelled_as_covering_every_group_not_the_page_shown(tools, client):
    """This label was once "summed across the groups shown" while the service
    was already re-aggregating over every filtered row. A model quoting the old
    label understates a total by whatever the limit cut off, and does so in
    prose that sounds precise."""
    client.on_post(
        "/aggregate",
        agg_result([{"region": "EU", "total": 90}], group_count=400,
                   totals={"total": 12345}),
    )
    out = await tools["aggregate"](
        dataset_id="ds-1", group_by=["region"],
        aggregations=[{"column": "amount", "function": "sum", "alias": "total"}],
        limit=1,
    )

    assert "## Totals (over all groups, not just the rows shown)" in out
    assert "total: 12345" in out


async def test_an_aggregation_with_no_grand_total_says_why_and_what_to_do(tools, client):
    """The dangerous failure is silence: a model that sees ``total`` present and
    ``avg_price`` absent will often sum the per-group averages itself, which
    describes nothing. Naming each omitted alias with its reason, and pointing
    at run_sql, is the whole point of ``totals_omitted``."""
    client.on_post(
        "/aggregate",
        agg_result([{"region": "EU", "total": 90, "avg_price": 3.5}],
                   totals={"total": 12345},
                   totals_omitted={"avg_price": "non-additive",
                                   "distinct_skus": "non-additive"}),
    )
    out = await tools["aggregate"](
        dataset_id="ds-1", group_by=["region"],
        aggregations=[{"column": "amount", "function": "mean", "alias": "avg_price"}],
    )

    assert "No overall total for avg_price (non-additive); distinct_skus (non-additive)." in out
    assert "use run_sql for a true overall value" in out


async def test_a_measure_with_no_finite_value_is_named_not_left_as_a_blank_cell(
        tools, client):
    """A null in an aggregate table normally means "no rows here". When the
    service reports the alias in ``unavailable_measures`` it means the opposite
    — there are rows, and their true value has no finite double (`std` squares
    its input, so one legitimate value near 1e308 leaves the range). Unsaid,
    the model reads it as missing data and retries in run_sql, where the same
    arithmetic is an error rather than a null."""
    client.on_post(
        "/aggregate",
        agg_result([{"region": "EU", "sd": None}, {"region": "US", "sd": 10.0}],
                   columns=["region", "sd"], unavailable_measures=["sd"],
                   totals_omitted={"sd": "non-additive"}),
    )
    out = await tools["aggregate"](
        dataset_id="ds-1", group_by=["region"],
        aggregations=[{"column": "amount", "function": "std", "alias": "sd"}],
    )

    assert "Blank cells under sd are values with no finite double" in out
    assert "not something run_sql can compute either" in out


async def test_a_pivot_cell_with_no_finite_value_is_named_too(tools, client):
    """Same caveat, and it matters more in a grid: a pivot is *expected* to
    have empty cells for combinations that never occur, so an unrepresentable
    cell is indistinguishable from an absent one without being told."""
    client.on_post(
        "/pivot",
        pivot_result([{"region": "EU", "Q1": None, "Q2": 10.0}],
                     columns=["region", "Q1", "Q2"], pivot_columns=["Q1", "Q2"],
                     unavailable_measures=["sd"]),
    )
    out = await tools["pivot"](
        dataset_id="ds-1", rows=["region"], columns="quarter",
        values=[{"column": "amount", "function": "std", "alias": "sd"}],
    )

    assert "Blank cells under sd are values with no finite double" in out
    assert "not empty cells" in out


async def test_a_truncated_aggregate_says_so_and_hands_over_the_full_result(tools, client):
    """A capped table that does not admit it is a wrong answer to "list every
    region". Both facts have to arrive together — that it was cut, and the
    artifact name plus the tool that reads it, so recovery is one call."""
    client.on_post(
        "/aggregate",
        agg_result([{"region": "EU", "total": 90}], group_count=9000,
                   truncated=True, result_file="aggregation_abc.parquet"),
    )
    out = await tools["aggregate"](
        dataset_id="ds-1", group_by=["region"],
        aggregations=[{"column": "amount", "function": "sum", "alias": "total"}],
    )

    assert "truncated by the service's aggregation row cap" in out
    assert "aggregation_abc.parquet" in out
    assert "read_artifact" in out


async def test_an_aggregate_that_matched_nothing_says_no_rows_rather_than_going_blank(tools, client):
    """An empty render is indistinguishable from a broken tool. "(no rows)"
    after a stated ``rows_scanned`` tells a model its filter was too narrow,
    not that the dataset is empty."""
    client.on_post("/aggregate", agg_result([], columns=["region", "total"],
                                            original_count=1000, group_count=0))
    out = await tools["aggregate"](
        dataset_id="ds-1", group_by=["region"],
        aggregations=[{"column": "amount", "function": "sum", "alias": "total"}],
        filters={"logic": "and", "conditions": [
            {"column": "region", "op": "eq", "value": "ZZ"}]},
    )

    assert "rows_scanned: 1000" in out
    assert "(no rows)" in out


async def test_grouping_by_a_column_that_does_not_exist_lists_the_ones_that_do(tools, client):
    """Same one-round-trip rule as profile_column, on the tool a model reaches
    for most: the rejection must carry the schema, not just the complaint."""
    client.on_post("/aggregate", unknown_column("regoin", ["region", "country", "amount"]))
    with pytest.raises(ToolError) as err:
        await tools["aggregate"](
            dataset_id="ds-1", group_by=["regoin"],
            aggregations=[{"column": "amount", "function": "sum"}],
        )

    assert "Available columns: region, country, amount" in str(err.value)
    assert "describe_dataset" in str(err.value)


async def test_aggregating_a_multi_sheet_version_names_the_sheets(tools, client):
    """``sheet`` is optional on the signature and mandatory in practice for a
    workbook. The names have to come back or the retry is a guess."""
    client.on_post("/aggregate", sheet_selection_required(["Q1", "Q2"]))
    with pytest.raises(ToolError) as err:
        await tools["aggregate"](
            dataset_id="ds-1", group_by=["region"],
            aggregations=[{"column": "amount", "function": "sum"}],
        )

    assert "Available sheets: Q1, Q2." in str(err.value)


async def test_a_rejected_sort_order_names_the_field_that_was_wrong(tools, client):
    """``sort_order`` is a ``Literal["asc","desc"]``, so "ascending" is a
    pydantic 422. Unrendered that arrives as "Request validation failed", which
    does not say which of eleven body keys to fix."""
    client.on_post(
        "/aggregate",
        problem(422, "Request validation failed", "validation_error",
                errors=[{"loc": ["body", "sort_order"],
                         "msg": "Input should be 'asc' or 'desc'"}]),
    )
    with pytest.raises(ToolError) as err:
        await tools["aggregate"](
            dataset_id="ds-1", group_by=["region"],
            aggregations=[{"column": "amount", "function": "sum"}],
            sort_order="ascending",
        )

    assert "sort_order: Input should be 'asc' or 'desc'" in str(err.value)


async def test_an_unknown_aggregation_function_is_reported_with_what_is_allowed(tools, client):
    """The service names the offending function and the allowed set precisely so
    pydantic does not swallow it into a generic 422 — the tool layer must pass
    that detail through intact rather than replacing it with a code."""
    client.on_post(
        "/aggregate",
        problem(400, "Unknown aggregation function: avg. Allowed: ['count', 'mean', 'sum']",
                "bad_request"),
    )
    with pytest.raises(ToolError) as err:
        await tools["aggregate"](
            dataset_id="ds-1", group_by=["region"],
            aggregations=[{"column": "amount", "function": "avg"}],
        )

    assert "Unknown aggregation function: avg." in str(err.value)
    assert "Allowed: ['count', 'mean', 'sum']" in str(err.value)


# ---------------------------------------------------------------------------
# pivot — the same engine, cross-tabulated
# ---------------------------------------------------------------------------


async def test_pivot_sends_the_row_and_value_specs_with_the_resolved_version(tools, client):
    """Same version-pinning contract as aggregate, plus the flags: the totals
    switches must travel as booleans, since omitting them would silently mean
    "false" and a caller who asked for totals would get a table without them
    and no indication why."""
    client.on_post("/pivot", pivot_result([{"region": "EU", "2025": 1}]))
    await tools["pivot"](
        dataset_id="ds-1", rows=["region"], columns="year",
        values=[{"column": "amount", "function": "sum", "alias": "total"}],
        sheet="Q1", filters={"logic": "and", "conditions": []},
        include_row_totals=True, include_column_totals=True, limit=10,
    )

    assert client.one_call_to("POST", "/pivot").body == {
        "dataset_id": "ds-1",
        "version_number": 4,
        "sheet": "Q1",
        "rows": ["region"],
        "columns": "year",
        "values": [{"column": "amount", "function": "sum", "alias": "total"}],
        "filters": {"logic": "and", "conditions": []},
        "include_row_totals": True,
        "include_column_totals": True,
        "limit": 10,
        "return_data": True,
    }


async def test_a_pivot_without_a_pivot_dimension_omits_the_key_entirely(tools, client):
    """``PivotRequest.columns`` omitted means "a plain grouped table" — a
    documented mode. It has to be absent rather than null so the request keeps
    reaching the service unchanged, and the false flags must still be sent."""
    client.on_post("/pivot", pivot_result([{"region": "EU", "total": 9}]))
    await tools["pivot"](
        dataset_id="ds-1", rows=["region"],
        values=[{"column": "amount", "function": "sum", "alias": "total"}],
    )

    call = client.one_call_to("POST", "/pivot")
    assert "columns" not in call.body
    assert "filters" not in call.body
    assert "sheet" not in call.body
    assert call.body["include_row_totals"] is False
    assert call.body["include_column_totals"] is False


async def test_a_pivot_names_the_values_that_became_columns(tools, client):
    """The pivot dimension's distinct values are the shape of the answer. A
    model that cannot see them cannot tell a missing quarter from a zero one,
    and cannot write the follow-up filter."""
    client.on_post(
        "/pivot",
        pivot_result([{"region": "EU", "Q1": 10, "Q2": 20}],
                     columns=["region", "Q1", "Q2"], pivot_columns=["Q1", "Q2"],
                     original_count=500, row_count=1),
    )
    out = await tools["pivot"](
        dataset_id="ds-1", rows=["region"], columns="quarter",
        values=[{"column": "amount", "function": "sum"}],
    )

    assert "rows_scanned: 500" in out
    assert "pivot_columns: Q1, Q2" in out
    assert "region | Q1 | Q2" in out
    assert "EU | 10 | 20" in out


async def test_a_pivot_dimension_with_too_many_values_is_told_how_to_bucket_it(tools, client):
    """Pivoting on a high-cardinality column is the single most likely way to
    fail this tool. The recovery is not "try again" — it is bucket or filter —
    and the message has to carry the cap and the bucketing vocabulary or the
    model retries the same doomed call with a smaller limit."""
    client.on_post(
        "/pivot",
        problem(400,
                "Pivot dimension 'customer_id' has more than 200 distinct values "
                "— bucket it or filter first",
                "too-many-pivot-columns", limit=200),
    )
    with pytest.raises(ToolError) as err:
        await tools["pivot"](
            dataset_id="ds-1", rows=["region"], columns="customer_id",
            values=[{"column": "amount", "function": "sum"}],
        )

    message = str(err.value)
    assert "Pivot dimension 'customer_id' has more than 200 distinct values" in message
    assert "limit 200" in message
    assert "date_trunc / bin_count" in message


async def test_a_truncated_pivot_reports_the_cut_and_the_saved_result(tools, client):
    """Pivot's cap is a row cap, not aggregate's group cap, and the note says
    so; conflating them would send a model to change the wrong parameter."""
    client.on_post(
        "/pivot",
        pivot_result([{"region": "EU", "Q1": 10}], truncated=True,
                     result_file="pivot_def.parquet"),
    )
    out = await tools["pivot"](
        dataset_id="ds-1", rows=["region"], columns="quarter",
        values=[{"column": "amount", "function": "sum"}],
    )

    assert "truncated by the service's row cap" in out
    assert "pivot_def.parquet" in out


async def test_pivot_grand_totals_are_reported_when_the_service_computed_them(tools, client):
    """``totals`` is re-aggregated over every filtered row for the value-display
    specs. Dropping it forces a model to add the visible cells, which is wrong
    the moment the table is capped."""
    client.on_post(
        "/pivot",
        pivot_result([{"region": "EU", "Q1": 10}], totals={"total": 999}),
    )
    out = await tools["pivot"](
        dataset_id="ds-1", rows=["region"], columns="quarter",
        values=[{"column": "amount", "function": "sum", "alias": "total"}],
    )

    assert "total: 999" in out


async def test_a_pivot_that_matched_nothing_says_no_rows(tools, client):
    """As with aggregate: an empty body reads as a broken tool rather than an
    over-narrow filter."""
    client.on_post("/pivot", pivot_result([], columns=["region"], original_count=500,
                                          row_count=0))
    out = await tools["pivot"](
        dataset_id="ds-1", rows=["region"],
        values=[{"column": "amount", "function": "sum"}],
        filters={"logic": "and", "conditions": [
            {"column": "region", "op": "eq", "value": "ZZ"}]},
    )

    assert "rows_scanned: 500" in out
    assert "(no rows)" in out


# ---------------------------------------------------------------------------
# The aggregation vocabulary the tools advertise
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("tool_name,field", [("aggregate", "aggregations"),
                                             ("pivot", "values")])
async def test_the_published_help_lists_exactly_the_functions_the_service_accepts(
    tools, tool_name, field,
):
    """A hand-written copy of this list once drifted from the validator. A tool
    description advertising a function the service rejects costs a model a
    whole round trip to discover, and one it omits is a capability nobody uses.
    Parsed from the published schema, so this fails if the derivation is
    replaced by a literal."""
    published = {t.name: t for t in await tools.server.list_tools()}
    description = published[tool_name].input_schema["properties"][field]["description"]

    listed = description.split("Allowed functions: ")[1].split(".")[0]
    assert set(listed.split(", ")) == set(ALLOWED_AGG_FUNCTIONS)


# ---------------------------------------------------------------------------
# check_quality — three reads, one bounded answer
# ---------------------------------------------------------------------------


def _quality_scripted(client: FakeAnalyticsClient, *, sheet: str | None = None,
                      missing: dict[str, Any] | None = None,
                      duplicates: dict[str, Any] | None = None,
                      validations: dict[str, Any] | None = None) -> dict[str, str]:
    base = "/datasets/ds-1/versions/4"
    prefix = f"{base}/sheets/{sheet}" if sheet else base
    paths = {
        "missing": f"{prefix}/missing",
        "duplicates": f"{prefix}/duplicates",
        "validations": f"{base}/validations",
    }
    client.on_get(paths["missing"], missing if missing is not None else missing_report())
    client.on_get(paths["duplicates"],
                  duplicates if duplicates is not None else duplicates_report())
    client.on_get(paths["validations"], validations if validations is not None else page([]))
    return paths


async def test_check_quality_reads_three_endpoints_and_caps_what_it_pulls_back(tools, client):
    """One tool call, three service reads — and both list reads are bounded at
    the request. An uncapped duplicates or validations fetch would put an
    arbitrary amount of data into the model's context for a summary that shows
    counts only."""
    paths = _quality_scripted(client, sheet="Q1")
    await tools["check_quality"](dataset_id="ds-1", sheet="Q1")

    assert client.trace() == [
        ("GET", VERSIONS),
        ("GET", paths["missing"]),
        ("GET", paths["duplicates"]),
        ("GET", paths["validations"]),
    ]
    assert client.one_call_to("GET", paths["duplicates"]).params == {"limit": 10}
    assert client.one_call_to("GET", paths["validations"]).params == {"limit": 1}


async def test_omitting_duplicate_columns_leaves_whole_row_duplicates_to_the_service(tools, client):
    """``columns=None`` must not travel. The route's ``columns`` query parameter
    defaults to None meaning "group on every column"; sending an empty or null
    value is a different request and would change what "duplicate" means."""
    paths = _quality_scripted(client)
    await tools["check_quality"](dataset_id="ds-1")

    assert client.one_call_to("GET", paths["duplicates"]).params == {"limit": 10}


async def test_duplicate_columns_are_forwarded_and_echoed_as_what_was_grouped_on(tools, client):
    """"Are there duplicate customers" and "are there duplicate rows" are
    different questions with different answers. The output has to name the
    grouping, or a count gets attributed to the wrong question."""
    paths = _quality_scripted(
        client,
        duplicates=duplicates_report(columns=["customer_id"], exact=False,
                                     group_count=3, duplicate_rows=8),
    )
    out = await tools["check_quality"](dataset_id="ds-1", duplicate_columns="customer_id")

    assert client.one_call_to("GET", paths["duplicates"]).params == {
        "columns": "customer_id", "limit": 10}
    assert "grouped_on: customer_id" in out
    assert "duplicate_groups: 3" in out
    assert "duplicate_rows: 8" in out


async def test_only_the_worst_columns_are_listed_and_the_heading_admits_the_rest(tools, client):
    """A 300-column sheet would otherwise fill the response with columns that
    have no nulls. The heading has to state both numbers — "worst 2 of 5" —
    because a model reading a truncated list as complete will report that the
    remaining columns are clean when nothing was checked."""
    _quality_scripted(
        client,
        missing=missing_report([("a", 900, 90.0), ("b", 500, 50.0), ("c", 10, 1.0),
                                ("d", 1, 0.1), ("e", 0, 0.0)]),
    )
    out = await tools["check_quality"](dataset_id="ds-1", max_columns=2)

    assert "## Missing values (worst 2 of 5 columns)" in out
    assert "a | 900 | 90" in out
    assert "b | 500 | 50" in out
    assert "\nc | " not in out


async def test_check_quality_names_the_version_it_actually_examined(tools, client):
    """The usual call omits ``version``, so the answer is about whichever
    version happened to be newest. Without the resolved number in the output, a
    "this data is clean" finding cannot be tied to anything later."""
    _quality_scripted(client, missing=missing_report(row_count=1000, source="profile_run"))
    out = await tools["check_quality"](dataset_id="ds-1")

    assert "version: 4" in out
    assert "rows: 1000" in out
    assert "source: profile_run" in out


async def test_the_latest_validation_run_is_summarised_with_its_failure_split(tools, client):
    """Errors and warnings are not the same finding: a version with two warnings
    is publishable and one with two errors is not. Collapsing them into
    "2 failed" removes the only distinction that changes what happens next."""
    _quality_scripted(
        client,
        validations=page([validation_run(status="failed", rules_total=12, rules_passed=10,
                                         rules_failed=2, error_failures=1,
                                         warning_failures=1)]),
    )
    out = await tools["check_quality"](dataset_id="ds-1")

    assert "## Latest validation run" in out
    assert "status: failed" in out
    assert "error_failures: 1" in out
    assert "warning_failures: 1" in out


async def test_a_version_with_no_validation_run_says_so_rather_than_showing_nothing(tools, client):
    """An empty section reads as "validation passed with nothing to report".
    "No validation run recorded" is the opposite claim — nothing was checked —
    and it is the one that should prompt running validation."""
    _quality_scripted(client, validations=page([]))
    out = await tools["check_quality"](dataset_id="ds-1")

    assert "No validation run recorded for this version." in out


async def test_check_quality_on_a_workbook_names_the_sheets_and_stops(tools, client):
    """The first of three reads rejects, and the tool must surface the sheet
    names rather than pressing on — a duplicates or validations read against an
    unresolvable sheet would either error again or answer about the wrong one."""
    client.on_get("/datasets/ds-1/versions/4/missing",
                  sheet_selection_required(["Q1", "Q2"]))
    with pytest.raises(ToolError) as err:
        await tools["check_quality"](dataset_id="ds-1")

    assert "Available sheets: Q1, Q2." in str(err.value)
    assert client.trace() == [("GET", VERSIONS), ("GET", "/datasets/ds-1/versions/4/missing")]


async def test_naming_a_sheet_that_is_not_in_this_version_says_exactly_that(tools, client):
    """Distinct from "no such sheet anywhere": the sheet may exist in another
    version. A generic 404 would send the model to re-check the dataset name
    instead of the version."""
    client.on_get(
        "/datasets/ds-1/versions/4/sheets/Q3/missing",
        problem(404, "The view's sheet is not present in version 4",
                "sheet-not-in-version", version_number=4),
    )
    with pytest.raises(ToolError) as err:
        await tools["check_quality"](dataset_id="ds-1", sheet="Q3")

    assert "not present in version 4" in str(err.value)
    assert "does not exist in this version of the dataset" in str(err.value)


# ---------------------------------------------------------------------------
# compare_versions — what changed between two refreshes
# ---------------------------------------------------------------------------


async def test_comparing_a_workbook_uses_the_two_numbers_given_and_resolves_nothing(tools, client):
    """Both versions are required arguments, so there is nothing to default. A
    version lookup here would be a silent substitution on a tool whose entire
    output is "what differs between these two"."""
    client.on_get("/datasets/ds-1/versions/1/diff/2",
                  workbook_diff(unchanged=["Q1"], from_version=1, to_version=2))
    out = await tools["compare_versions"](dataset_id="ds-1", from_version=1, to_version=2)

    assert client.trace() == [("GET", "/datasets/ds-1/versions/1/diff/2")]
    assert "from: 1" in out
    assert "to: 2" in out


async def test_a_workbook_diff_separates_added_removed_modified_and_renamed(tools, client):
    """Four different events with four different responses. A rename candidate
    in particular is advisory — reported next to its confidence so a model does
    not treat "Q1 became Quarter1" as an established fact and rewrite queries."""
    client.on_get(
        "/datasets/ds-1/versions/1/diff/2",
        workbook_diff(added=["Quarter1"], removed=["Q1"],
                      modified=[("Notes", True, -5)], unchanged=["Refs", "Lookup"],
                      renames=[("Q1", "Quarter1", "high")]),
    )
    out = await tools["compare_versions"](dataset_id="ds-1", from_version=1, to_version=2)

    assert "## Added sheets\n- Quarter1" in out
    assert "## Removed sheets\n- Q1" in out
    assert "## Modified sheets" in out
    assert "Notes | true | -5" in out
    assert "## Possible renames" in out
    assert "Q1 | Quarter1 | high" in out
    assert "## Unchanged\nRefs, Lookup" in out


async def test_a_workbook_diff_with_no_additions_says_none_rather_than_omitting_it(tools, client):
    """An absent "Added sheets" heading is ambiguous between "none were added"
    and "the tool did not look". "(none)" is a finding; silence is not."""
    client.on_get("/datasets/ds-1/versions/1/diff/2", workbook_diff(unchanged=["Q1"]))
    out = await tools["compare_versions"](dataset_id="ds-1", from_version=1, to_version=2)

    assert "## Added sheets\n(none)" in out
    assert "## Removed sheets\n(none)" in out
    assert "## Modified sheets" not in out
    assert "## Possible renames" not in out


async def test_the_workbook_summary_points_at_the_column_level_follow_up(tools, client):
    """The workbook view says a sheet was modified but not how. Without the
    pointer a model reports "Notes changed" and stops, when one more call with
    ``sheet`` would name the columns."""
    client.on_get("/datasets/ds-1/versions/1/diff/2",
                  workbook_diff(modified=[("Notes", True, 0)]))
    out = await tools["compare_versions"](dataset_id="ds-1", from_version=1, to_version=2)

    assert "Pass `sheet` to see column-level changes for one sheet." in out


async def test_comparing_one_sheet_asks_the_sheet_scoped_diff(tools, client):
    """A different endpoint, not a filter over the workbook diff — the
    column-level payload only exists on the sheet route."""
    client.on_get("/datasets/ds-1/versions/1/sheets/Q1/diff/2",
                  sheet_diff(added_columns=["discount"], from_row_count=100,
                             to_row_count=120))
    out = await tools["compare_versions"](dataset_id="ds-1", from_version=1,
                                          to_version=2, sheet="Q1")

    assert client.trace() == [("GET", "/datasets/ds-1/versions/1/sheets/Q1/diff/2")]
    assert "sheet: Q1" in out
    assert "rows_before: 100" in out
    assert "rows_after: 120" in out
    assert "row_delta: 20" in out
    assert "## Added columns\n- discount" in out


async def test_a_column_type_change_names_the_old_and_new_types(tools, client):
    """The change most likely to break a downstream query silently: the column
    is still there, still populated, and no longer comparable the same way.
    Naming only the column would hide the reason it now behaves differently."""
    client.on_get(
        "/datasets/ds-1/versions/1/sheets/Q1/diff/2",
        sheet_diff(type_changes=[("amount", "string", "numeric")]),
    )
    out = await tools["compare_versions"](dataset_id="ds-1", from_version=1,
                                          to_version=2, sheet="Q1")

    assert "## Type changes" in out
    assert "column | from | to" in out
    assert "amount | string | numeric" in out


async def test_an_unchanged_sheet_reports_identical_instead_of_an_empty_diff(tools, client):
    """Three empty sections read as "the tool found nothing", which is what a
    failure looks like too. The explicit ``identical`` flag is the positive
    statement that the comparison ran and found no difference."""
    client.on_get("/datasets/ds-1/versions/1/sheets/Q1/diff/2",
                  sheet_diff(identical=True, from_row_count=100, to_row_count=100))
    out = await tools["compare_versions"](dataset_id="ds-1", from_version=1,
                                          to_version=2, sheet="Q1")

    assert "identical: true" in out
    assert "## Type changes" not in out
    assert "## Added columns\n(none)" in out


async def test_a_version_that_cannot_be_read_is_not_reported_as_absent(tools, client):
    """The service returns 404 both for "no such version" and for "owned by a
    team you are not in". A model told only "not found" concludes the data does
    not exist and stops; the note is what makes it ask about access instead."""
    client.on_get("/datasets/ds-1/versions/1/diff/9",
                  problem(404, "Version 9 not found", "not-found"))
    with pytest.raises(ToolError) as err:
        await tools["compare_versions"](dataset_id="ds-1", from_version=1, to_version=9)

    message = str(err.value)
    assert "Version 9 not found" in message
    assert "owned by a team you are not in" in message
    assert "does not prove the resource is absent" in message

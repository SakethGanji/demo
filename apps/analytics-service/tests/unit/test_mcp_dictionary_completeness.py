"""``get_data_dictionary`` may not quietly shorten what the dictionary says.

This tool is the only source a model has for what a column *means*, and two
different mechanisms were silently editing that answer:

* **The cell cap.** ``render.table`` truncates every cell at 80 characters,
  which is right for sampled data and wrong for the two fields here whose tail
  is load-bearing. A description is stored up to 2000 characters — discovery
  caps it there — and its qualifications live at the end ("… excludes
  intercompany transfers"), so a model reading the first 79 characters gets a
  confident half-definition. ``allowed_values`` is worse: it is a *closed set*,
  and a closed set cut mid-list is indistinguishable from a complete one. The
  model then writes a filter over a vocabulary missing its last few members and
  reports the result as exhaustive.

* **The page.** Both dictionary routes are ordinary paginated list routes with a
  default ``limit`` of 50. A sheet documenting 60 columns answered with 50 of
  them and said nothing, so "there is no dictionary entry for ``discount_code``"
  was a statement about page one, presented as a statement about the sheet.
  Since the sensitivity roll-up ("Tagged sensitive: …") is derived from the same
  rows, an unread page also means a PII column reported as unlisted — the exact
  failure the sensitivity notice exists to prevent.

Unit layer: the payloads are dumped from ``SheetMetadataOut`` and
``ColumnMetadataOut``, so this pins the rendering, not the storage.
"""

from __future__ import annotations

import pytest

from app.features.mcp.tools import orient
from mcp_harness import (
    FakeAnalyticsClient,
    ToolSet,
    column_metadata,
    page,
    register_tools,
    sheet_metadata,
)

META = "/datasets/ds-1/sheet-metadata"
COLUMNS = "/datasets/ds-1/sheet-metadata/q1/columns"

# 2000 characters is the service's stored maximum for a column description.
LONG_DESCRIPTION = (
    "Net recognised revenue for the line item, in reporting currency, after "
    "discounts and returns but BEFORE intercompany eliminations — do not sum "
    "this across legal entities without excluding the intercompany flag."
)


@pytest.fixture
def client() -> FakeAnalyticsClient:
    return FakeAnalyticsClient()


@pytest.fixture
def tools(client: FakeAnalyticsClient) -> ToolSet:
    return register_tools(orient, client)


async def test_a_long_column_description_is_not_cut_off_mid_sentence(tools, client):
    """The qualification is at the end of a description, always: the first
    clause says what the column is and the last says when not to trust it. Cut
    at 79 characters this one reads "Net recognised revenue for the line item,
    in reporting currency, after disc…" — a model has the definition and has
    lost the warning that summing it across entities double-counts."""
    client.on_get(META, page([sheet_metadata(sheet_key="q1")]))
    client.on_get(
        COLUMNS,
        page([column_metadata("net_revenue", description=LONG_DESCRIPTION)]),
    )

    out = await tools["get_data_dictionary"](dataset_id="ds-1", sheet_key="q1")

    assert LONG_DESCRIPTION in out
    assert "…" not in out


async def test_a_long_allowed_values_list_is_rendered_whole(tools, client):
    """The single most dangerous truncation in this tool. ``allowed_values`` is
    a closed set, and a closed set with its tail removed still looks like a
    closed set — nothing in the output says otherwise. A model told the statuses
    are ``draft, submitted, …`` up to the 80th character will write
    ``status IN (...)`` over the visible ones and describe the result as
    covering every status."""
    statuses = [
        "draft", "submitted", "in_review", "approved", "rejected",
        "cancelled", "settled", "reconciled", "archived", "written_off",
    ]
    client.on_get(META, page([sheet_metadata(sheet_key="q1")]))
    client.on_get(COLUMNS, page([column_metadata("status", allowed_values=statuses)]))

    out = await tools["get_data_dictionary"](dataset_id="ds-1", sheet_key="q1")

    for status in statuses:
        assert status in out
    assert "…" not in out


async def test_a_long_sheet_description_is_not_cut_off_either(tools, client):
    """The sheet-level description carries the grain caveats and is rendered
    through the same table on the path taken when no ``sheet_key`` is passed —
    the default call, and the first thing a model reads about the dataset."""
    client.on_get(
        META,
        page([sheet_metadata(sheet_key="q1", description=LONG_DESCRIPTION)]),
    )

    out = await tools["get_data_dictionary"](dataset_id="ds-1")

    assert LONG_DESCRIPTION in out


async def test_the_column_dictionary_asks_for_the_largest_page_the_route_allows(
    tools, client
):
    """Left to the route's default the tool reads 50 of a wide sheet's entries
    and reports them as the dictionary. ``pagination`` is ``le=200``, so asking
    for 200 is both the maximum available and the difference between covering
    every real sheet and covering the narrow ones."""
    client.on_get(META, page([sheet_metadata(sheet_key="q1")]))
    client.on_get(COLUMNS, page([column_metadata("amount")]))

    await tools["get_data_dictionary"](dataset_id="ds-1", sheet_key="q1")

    assert client.one_call_to("GET", META).params == {"limit": 200}
    assert client.one_call_to("GET", COLUMNS).params == {"limit": 200}


async def test_a_dictionary_larger_than_one_page_says_how_much_was_not_shown(
    tools, client
):
    """Even at 200 a wide sheet can overflow, and the failure mode is a false
    negative: asked "is customer_email documented as PII?", a tool holding page
    one answers "no such entry" about an entry that exists. Reporting the count
    it read against the count that exists converts a wrong answer into a partial
    one, which the model can act on."""
    client.on_get(META, page([sheet_metadata(sheet_key="q1")]))
    client.on_get(
        COLUMNS,
        page([column_metadata(f"c{i}") for i in range(200)], total=340, limit=200),
    )

    out = await tools["get_data_dictionary"](dataset_id="ds-1", sheet_key="q1")

    assert "Showing 200 of 340 documented columns in q1" in out
    assert "documented but not listed" in out


async def test_a_dictionary_that_fits_in_one_page_carries_no_partial_warning(
    tools, client
):
    """The complement, and the reason the warning means anything: almost every
    sheet fits. A caveat printed on every dictionary is one a model learns to
    skip, and it would be skipped on the sheet where it is true."""
    client.on_get(META, page([sheet_metadata(sheet_key="q1")]))
    client.on_get(COLUMNS, page([column_metadata("amount"), column_metadata("region")]))

    out = await tools["get_data_dictionary"](dataset_id="ds-1", sheet_key="q1")

    assert "Showing" not in out
    assert "not listed" not in out

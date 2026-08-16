"""Free-text names must survive the trip into a REST path.

Sheet titles, column headers and tag names are all interpolated straight into
``/api/v1/...`` paths by the tool modules, and ``AnalyticsClient`` hands the
resulting string to httpx unchanged. httpx does not escape path-structural
characters, so an unencoded ``#`` in a name is parsed as the start of a URL
fragment and *everything after it is discarded* — including the ``/query`` or
``/columns`` suffix that selects the route. The request then lands on a path no
route matches and comes back as a flat 404, which ``_common.explain`` renders
as "this may not exist, or may belong to a team you are not in". A model reading
that concludes the dataset is unreachable and stops, when the truth is that the
workbook has a sheet called ``Sheet #1`` — a perfectly legal Excel sheet name
that ``app/shared/datasets.py::_find_sheet`` matches by exact name.

These tests assert on the path the tool *asks for* and on what httpx then makes
of it, because the bug lives in the gap between those two: a path that looks
right in a trace can still be truncated on the wire.

Not covered here: whether the service routes the encoded path back to the right
sheet. That is a Starlette/ASGI question about a running app with real data;
``tests/test_mcp_endpoint.py`` is where end-to-end claims are made.
"""

from __future__ import annotations

import httpx
import pytest

from app.features.mcp.tools import compute, curate, look
from app.features.mcp.tools._common import seg, sheet_path
from mcp_harness import (
    FakeAnalyticsClient,
    ToolSet,
    page,
    query_page,
    register_tools,
    version,
)

API_PREFIX = "/api/v1"


def raw_path(path: str) -> bytes:
    """What httpx would actually put on the wire for a client-built path.

    Mirrors ``AnalyticsClient``: same base_url shape, same ``request(method,
    path)`` call, so the parsing that drops a fragment happens here too.
    """
    return httpx.URL(f"http://mcp.internal{API_PREFIX}{path}").raw_path


@pytest.fixture
def client() -> FakeAnalyticsClient:
    return FakeAnalyticsClient()


# ---------------------------------------------------------------------------
# The helper itself
# ---------------------------------------------------------------------------


def test_an_unencoded_hash_in_a_path_segment_silently_amputates_the_url():
    """The premise of every other test in this file, pinned so nobody
    "simplifies" the encoding away on the grounds that httpx surely handles it.

    Without encoding, the route suffix does not merely get mangled — it is gone,
    so the request cannot 404 informatively; it 404s on a path the service has
    never heard of."""
    unencoded = "/datasets/ds-1/versions/1/sheets/Sheet #1/query"

    assert raw_path(unencoded) == b"/api/v1/datasets/ds-1/versions/1/sheets/Sheet%20"
    assert b"/query" not in raw_path(unencoded)


def test_sheet_path_encodes_a_sheet_name_so_the_route_suffix_survives():
    """``sheet_path`` is the single chokepoint for every sheet-scoped call
    (query, aggregate, pivot, missing, duplicates, columns/…). If it does not
    encode, six tools break on the same workbook."""
    built = sheet_path("ds-1", 4, "Sheet #1", "query")

    assert built == "/datasets/ds-1/versions/4/sheets/Sheet%20%231/query"
    assert raw_path(built).endswith(b"/sheets/Sheet%20%231/query")


def test_sheet_path_leaves_an_ordinary_sheet_name_byte_for_byte_alone():
    """The complement, and the reason this fix is safe to apply everywhere:
    almost every real name is already URL-safe, so encoding must be invisible.
    A test that only checked hostile names would not catch an encoder that
    mangled ``orders`` into ``orders%0A``."""
    assert sheet_path("ds-1", 4, "orders", "query") == "/datasets/ds-1/versions/4/sheets/orders/query"
    assert sheet_path("ds-1", 4, None, "query") == "/datasets/ds-1/versions/4/query"


def test_the_segment_encoder_escapes_the_separator_as_well_as_the_fragment():
    """``/`` is the other character that changes the *shape* of the path rather
    than a name inside it: unencoded, one segment becomes two and the route
    arity no longer matches. Escaping it keeps the failure inside the service
    (a 404 naming the sheet) instead of inside the URL parser."""
    assert seg("Q1/Q2") == "Q1%2FQ2"
    assert seg("Sheet #1") == "Sheet%20%231"
    assert seg("rate %") == "rate%20%25"
    assert seg("customer_id") == "customer_id"


# ---------------------------------------------------------------------------
# Through the tools
# ---------------------------------------------------------------------------


async def test_query_rows_reaches_a_sheet_whose_name_contains_a_hash(client):
    """The headline case. ``sheet`` is documented as "Sheet name or key" and the
    service matches the raw workbook name first, so the model is invited to pass
    exactly the string ``describe_dataset`` printed to it."""
    tools: ToolSet = register_tools(look, client)
    client.on_get("/datasets/ds-1/versions", page([version(version_number=4)]))
    client.on_post(
        "/datasets/ds-1/versions/4/sheets/Sheet%20%231/query", query_page([{"a": 1}])
    )

    await tools["query_rows"](dataset_id="ds-1", sheet="Sheet #1", limit=5)

    call = client.one_call_to("POST", "/datasets/ds-1/versions/4/sheets/Sheet%20%231/query")
    assert raw_path(call.path).endswith(b"/sheets/Sheet%20%231/query")


async def test_profile_column_reaches_a_column_whose_name_contains_a_hash(client):
    """``columns/{column}`` is built by the caller, not by ``sheet_path``, so it
    needs its own encoding — a fix applied only to the sheet segment would leave
    profiling a column called ``order #`` broken while looking done."""
    tools: ToolSet = register_tools(compute, client)
    client.on_get("/datasets/ds-1/versions", page([version(version_number=4)]))
    client.on_get(
        "/datasets/ds-1/versions/4/sheets/orders/columns/order%20%23",
        {"name": "order #", "dtype": "string", "count": 10},
    )

    out = await tools["profile_column"](dataset_id="ds-1", sheet="orders", column="order #")

    call = client.one_call_to(
        "GET", "/datasets/ds-1/versions/4/sheets/orders/columns/order%20%23"
    )
    assert raw_path(call.path).endswith(b"/columns/order%20%23")
    assert "column: order #" in out


async def test_tag_history_reaches_a_tag_whose_name_contains_a_hash(client):
    """Tag names are free text — ``SetTagRequest`` only strips and lowercases
    them — and the history is the audit trail. "No such tag" for a tag that is
    right there is the worst possible answer from an audit tool."""
    tools: ToolSet = register_tools(curate, client)
    client.on_get("/datasets/ds-1/tags/release%20%231/history", page([], total=0))

    out = await tools["manage_tags"](action="history", dataset_id="ds-1", tag="Release #1")

    call = client.one_call_to("GET", "/datasets/ds-1/tags/release%20%231/history")
    assert raw_path(call.path).endswith(b"/tags/release%20%231/history")
    assert "release #1" in out

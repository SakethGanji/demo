"""``tests/unit/mcp_harness.py`` driven against the simplest real tool module.

Two jobs. The first is to prove the substitution works end to end: a tool module
registered against ``FakeAnalyticsClient`` runs its real bodies, real rendering
and the real ``@guard`` translation with no Postgres anywhere. The second is to
pin what ``orient.py`` guarantees while doing it — every test here asserts on
tool output or on the request the tool made, not merely that it did not crash.

``orient.py`` is the module under test because it is the thinnest: read-only,
Postgres-only, no version resolution, no artifacts. What is deliberately not
covered here is everything the harness cannot see — that the service actually
returns these shapes. ``tests/test_mcp_endpoint.py`` covers that against real
data, and if the two ever disagree it is this file that is wrong.
"""

from __future__ import annotations

import pytest
from mcp.server.mcpserver.exceptions import ToolError

from app.features.mcp.tools import orient
from mcp_harness import (
    FakeAnalyticsClient,
    column_hit,
    dataset,
    health,
    me,
    page,
    problem,
    register_tools,
    sheet,
    sheet_column,
    version,
)


@pytest.fixture
def client() -> FakeAnalyticsClient:
    return FakeAnalyticsClient()


@pytest.fixture
def tools(client):
    return register_tools(orient, client)


# ---------------------------------------------------------------------------
# whoami — the tool a model is told to call first
# ---------------------------------------------------------------------------


async def test_whoami_names_the_teams_whose_data_the_session_can_reach(tools, client):
    """The tool exists to answer "why is this dataset invisible to me". A team
    list that omits the role is only half that answer: read access and write
    access differ, and the model would have to fail a write to discover it."""
    client.on_get("/auth/me", me(name="Ada Lovelace", teams=[("Finance", "admin"),
                                                            ("Ops", "viewer")]))
    out = await tools["whoami"]()

    assert "user: Ada Lovelace" in out
    assert "- Finance (admin)" in out
    assert "- Ops (viewer)" in out
    assert client.trace() == [("GET", "/auth/me")]


async def test_a_superuser_is_told_that_nothing_they_read_is_masked(tools, client):
    """A superuser reading a sensitive column sees raw values. If the tool does
    not say so, a model can quote PII into a transcript believing the service
    masked it — the masking is real, but it is not applied to this caller."""
    client.on_get("/auth/me", me(is_superuser=True))
    out = await tools["whoami"]()
    assert "sensitive columns are returned to you unmasked" in out


async def test_hundreds_of_memberships_do_not_flood_the_first_tool_call(tools, client):
    """Seeded environments put one user in every team. Rendering all of them
    spends the model's context on noise before it has asked anything — but the
    count must survive, or a truncated list reads as the complete one."""
    client.on_get("/auth/me", me(teams=[(f"Team {i}", "viewer") for i in range(30)]))
    out = await tools["whoami"]()

    assert "teams: 30" in out
    assert out.count("(viewer)") == 10
    assert "…and 20 more" in out


# ---------------------------------------------------------------------------
# search_datasets — the only tool that turns a topic into a dataset_id
# ---------------------------------------------------------------------------


async def test_filters_the_caller_did_not_set_are_not_sent_at_all(tools, client):
    """``favorites_only`` defaults to False, and the tool must translate that to
    "no favorites filter" rather than ``favorites=false``. The two are not the
    same query, and the second one is a plausible reading of an absent flag."""
    client.on_get("/datasets", page([dataset()]))
    await tools["search_datasets"](query="orders")

    call = client.one_call_to("GET", "/datasets")
    assert call.params == {"q": "orders", "limit": 25, "offset": 0}


async def test_an_empty_search_says_invisible_rather_than_absent(tools, client):
    """Datasets owned by teams the caller is not in are filtered out of this
    list, not rejected. A bare "no results" invites the model to report that
    the data does not exist, when the real fix is a team membership."""
    client.on_get("/datasets", page([]))
    out = await tools["search_datasets"](query="payroll")

    assert "No datasets matched" in out
    assert "teams you are not a member of are invisible rather than forbidden" in out


async def test_the_dataset_id_is_carried_into_the_result_table(tools, client):
    """Nothing else in the tool surface returns a dataset_id, so every later
    call depends on this column being present and named the way the other
    tools' ``dataset_id`` argument is."""
    client.on_get("/datasets", page([dataset(id="ds-42", name="Orders")], total=7))
    out = await tools["search_datasets"]()

    assert "dataset_id" in out.splitlines()[0]
    assert "ds-42" in out
    assert "1 of 7 datasets shown." in out


# ---------------------------------------------------------------------------
# describe_dataset — the call every query is supposed to be based on
# ---------------------------------------------------------------------------


async def test_describe_dataset_reports_the_names_that_are_legal_downstream(tools, client):
    """Filters and SQL take *normalized* names and *sheet keys*; the display
    name and the raw header are not accepted anywhere. A model that copies what
    this tool prints must get a query that runs."""
    client.on_get(
        "/datasets/ds-1/sheets",
        page([sheet(name="Q1 Revenue", sheet_key="q1_revenue", columns=[
            sheet_column("Order ID", "int64", normalized_name="order_id", position=0),
            sheet_column("Amount", "double", normalized_name="amount", position=1),
        ])]),
    )
    client.on_get("/datasets/ds-1/versions", page([version(version_number=3)]))
    out = await tools["describe_dataset"](dataset_id="ds-1")

    assert "sheet_key: q1_revenue" in out
    assert "order_id (int64), amount (double)" in out
    assert "each sheet is a table named by its sheet_key" in out
    # Both reads are needed: schema comes from one endpoint, history the other.
    assert client.trace() == [("GET", "/datasets/ds-1/sheets"),
                              ("GET", "/datasets/ds-1/versions")]


# ---------------------------------------------------------------------------
# Error translation — the actual product of this layer
# ---------------------------------------------------------------------------


async def test_a_404_is_reported_as_ambiguous_rather_than_as_absence(tools, client):
    """The service returns 404 both for "no such dataset" and for "not your
    team". A tool that passes the 404 through unqualified teaches the model to
    conclude the data does not exist and stop, when the answer is whoami."""
    client.on_get(
        "/datasets/ds-1/health",
        problem(404, "Dataset not found: ds-1", "http-404"),
    )
    with pytest.raises(ToolError) as excinfo:
        await tools["get_dataset_health"](dataset_id="ds-1")

    message = str(excinfo.value)
    assert "Dataset not found: ds-1" in message
    assert "owned by a team you are not in" in message


async def test_an_unknown_health_dimension_names_the_tool_that_can_answer_it(tools, client):
    """'unknown' means no stored profile, not bad data. Left unexplained it
    reads as a quality failure, and the null rates it is missing are one live
    ``check_quality`` call away."""
    client.on_get("/datasets/ds-1/health", health({
        "validation": ("ok", "Last run passed"),
        "missing_data": ("unknown", "No profile recorded"),
        "duplicates": ("unknown", "No profile recorded"),
    }))
    out = await tools["get_dataset_health"](dataset_id="ds-1")

    assert "missing_data: unknown — No profile recorded" in out
    assert "'unknown' (missing_data, duplicates)" in out
    assert "check_quality" in out


async def test_search_columns_repeats_the_query_when_nothing_matched(tools, client):
    """The empty answer has to be attributable to the fragment that produced
    it; a model retrying a search cannot tell two empty results apart."""
    client.on_get("/search/columns", page([]))
    assert "No columns matching 'custmer_id'." == await tools["search_columns"](
        query="custmer_id"
    )

    client.on_get("/search/columns", page([column_hit(column_name="customer_id")]))
    out = await tools["search_columns"](query="customer_id", limit=5)
    assert "customer_id" in out
    assert client.calls_to("GET", "/search/columns")[-1].params == {
        "q": "customer_id", "limit": 5,
    }


# ---------------------------------------------------------------------------
# The harness's own contract, which every test above is standing on
# ---------------------------------------------------------------------------


async def test_an_unscripted_endpoint_fails_loudly_instead_of_looking_empty(tools, client):
    """If the fake answered an unanticipated call with ``None`` or an empty
    page, a tool that called the wrong endpoint would render "(no rows)" and
    the test would pass. The failure has to name the call that was not
    expected, or this whole suite is measuring the fake."""
    client.on_get("/datasets/ds-1/sheets", page([sheet()]))
    with pytest.raises(AssertionError) as excinfo:
        await tools["describe_dataset"](dataset_id="ds-1")

    message = str(excinfo.value)
    assert "unscripted call: GET /datasets/ds-1/versions" in message
    assert "/datasets/ds-1/sheets" in message  # what *was* scripted


async def test_every_tool_the_module_registers_is_reachable_by_name(tools):
    """``register_tools`` reads the server's registry rather than a hand-written
    list, so a tool added to the module is testable without touching the
    harness — and a renamed tool fails here rather than silently going
    untested."""
    assert set(tools) == {"whoami", "search_datasets", "describe_dataset",
                          "get_data_dictionary", "search_columns", "get_dataset_health"}

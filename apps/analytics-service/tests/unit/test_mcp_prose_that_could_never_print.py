"""Two sentences the MCP tools were written to say and could not say.

Both are the same class of defect — a caveat guarded by a condition that never
holds, or worded for a fact that is not the one being reported — and both are
invisible to a test that only checks the happy path, because the happy path is
where the caveat is correctly absent.

**``get_activity``'s zero-usage line.** It hung off ``render.fields(...) or
"No recorded activity at all"``, and ``render.fields`` only returns the empty
string when every pair is empty. ``UsageResponse`` types its counters as
required ``int``, so the block is never empty and the sentence could never
print. Meanwhile the counters are derived from the audit trail and count
successful requests only, so all-zero is a real and reachable state — a dataset
nobody has touched, or one whose only traffic was denied — and a row of zeroes
with no explanation reads as a broken counter rather than as a quiet dataset.

**``run_sql``'s table-name hint.** It said "Queryable table names in this
version" about a list read from ``GET /datasets/{id}/sheets``, which resolves
the dataset's *current* version. The query that just failed ran against the
newest **ready** version, or against whatever version the caller pinned, and a
tag rollback moves the current pointer backwards independently of both. Naming
the wrong version's tables inside a catalog error is worse than naming none:
the model rewrites its query against a schema the query will never see.
"""

from __future__ import annotations

import pytest
from mcp.server.mcpserver.exceptions import ToolError

from app.features.mcp.tools import context, look
from mcp_harness import (
    FakeAnalyticsClient,
    ToolSet,
    page,
    problem,
    register_tools,
    sheet,
    version,
)

USAGE = "/datasets/ds-1/usage"
TIMELINE = "/datasets/ds-1/timeline"


@pytest.fixture
def client() -> FakeAnalyticsClient:
    return FakeAnalyticsClient()


@pytest.fixture
def activity(client: FakeAnalyticsClient) -> ToolSet:
    return register_tools(context, client)


@pytest.fixture
def sql(client: FakeAnalyticsClient) -> ToolSet:
    return register_tools(look, client)


def usage(*, downloads: int, writes: int, total_events: int, reads: int = 0,
          last: str | None = None):
    """``GET /datasets/{id}/usage`` — the counters are non-optional ints."""
    return {
        "dataset_id": "ds-1", "downloads": downloads, "writes": writes,
        "reads": reads, "total_events": total_events, "last_activity_at": last,
    }


# ---------------------------------------------------------------------------
# get_activity — three zeroes need a sentence, not just three zeroes
# ---------------------------------------------------------------------------


async def test_a_dataset_with_no_recorded_usage_is_told_apart_from_a_broken_counter(
    activity, client
):
    """The whole point of this tool is judging whether a dataset is maintained.
    "downloads: 0 / writes: 0 / reads: 0 / total_events: 0" beside an empty
    timeline is consistent with a live, read-only reference table *and* with a
    counter that is not wired up, and a model that guesses wrong either
    dismisses a good dataset or trusts a dead one. The sentence has to name
    what is and is not counted — and since GET reads are not audited, "no
    reads" is precisely the claim it must NOT make."""
    client.on_get(USAGE, usage(downloads=0, writes=0, reads=0, total_events=0))
    client.on_get(TIMELINE, page([]))

    out = await activity["get_activity"](dataset_id="ds-1", include_jobs=False)

    assert "total_events: 0" in out
    assert "reads: 0" in out
    assert "No recorded activity at all — nothing queried, downloaded or changed." in out
    assert "Plain GET reads are not audited" in out
    assert "denied requests" in out
    assert "not 'nobody has looked at it'" in out


async def test_a_dataset_with_recorded_usage_gets_no_such_caveat(activity, client):
    """The complement. One recorded write makes the sentence false, and a
    "nothing has happened here" line printed above a real write count is the
    kind of contradiction a model resolves by picking one at random."""
    client.on_get(
        USAGE,
        usage(downloads=0, writes=3, total_events=3, last="2026-05-01T09:00:00+00:00"),
    )
    client.on_get(TIMELINE, page([]))

    out = await activity["get_activity"](dataset_id="ds-1", include_jobs=False)

    assert "writes: 3" in out
    assert "No recorded activity" not in out


async def test_a_dataset_that_is_only_queried_is_not_reported_as_untouched(
    activity, client
):
    """The state the write/read split newly makes visible.

    A reference table that everyone queries and nobody edits is the most
    common shape in a catalog. While every ``POST .../query`` was counted as a
    write, it reported as heavily written to; if the fix had merely dropped
    those rows it would now report as dead. Neither is what a model should
    conclude, and the "nothing happened here" caveat must stay off a dataset
    with recorded queries.
    """
    client.on_get(
        USAGE,
        usage(downloads=0, writes=0, reads=12, total_events=12,
              last="2026-05-01T09:00:00+00:00"),
    )
    client.on_get(TIMELINE, page([]))

    out = await activity["get_activity"](dataset_id="ds-1", include_jobs=False)

    assert "writes: 0" in out and "reads: 12" in out
    assert "No recorded activity" not in out


# ---------------------------------------------------------------------------
# run_sql — the table hint has to name the version it read
# ---------------------------------------------------------------------------


async def test_the_table_name_hint_does_not_claim_to_describe_the_queried_version(
    sql, client
):
    """The failing query ran against version 7 — the newest ready one — while
    the sheet list came from the dataset's current version, which a tag rollback
    has left at 3. Labelling that list "in this version" tells the model the
    version-7 tables are called ``orders_2026`` and ``returns`` on the sole
    evidence of version 3, and a model that then writes ``FROM returns`` gets
    the same catalog error a second time with no way to see why."""
    client.on_get("/datasets/ds-1/versions", page([version(version_number=7)]))
    client.on_post(
        "/datasets/ds-1/versions/7/sql",
        problem(400, "Query failed: Table with name orders does not exist", "sql-error"),
    )
    client.on_get(
        "/datasets/ds-1/sheets",
        page([sheet(name="Orders 2026", sheet_key="orders_2026"),
              sheet(name="Returns", sheet_key="returns")]),
    )

    with pytest.raises(ToolError) as err:
        await sql["run_sql"](dataset_id="ds-1", sql="SELECT * FROM orders")

    message = str(err.value)
    assert "in this version" not in message
    assert "the dataset's current version: orders_2026, returns." in message
    assert "This query ran against version 7" in message
    assert "describe_dataset" in message


async def test_the_hint_names_the_pinned_version_when_the_caller_pinned_one(sql, client):
    """A pinned version is the case where the mismatch is most likely and least
    excusable: the caller deliberately reached for an old version, and the sheet
    list cannot be about it unless the pin happens to equal the current
    pointer."""
    client.on_post(
        "/datasets/ds-1/versions/2/sql",
        problem(400, "Query failed: Table with name orders does not exist", "sql-error"),
    )
    client.on_get("/datasets/ds-1/sheets", page([sheet(sheet_key="orders_2026")]))

    with pytest.raises(ToolError) as err:
        await sql["run_sql"](dataset_id="ds-1", version=2, sql="SELECT * FROM orders")

    assert "This query ran against version 2" in str(err.value)

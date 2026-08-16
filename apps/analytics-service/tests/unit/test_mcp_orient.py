"""``tools/orient.py`` — what a model learns before it reads a single row.

The six orientation tools are the only ones that turn a vague request into the
identifiers every other tool demands: a ``dataset_id``, a ``sheet_key``, a
normalized column name, and enough context to know whether the answer can be
trusted. They are cheap and Postgres-only, so the failure they cause is never a
crash — it is a model that proceeds confidently on a wrong identifier, a stale
version, or a column it does not understand.

So every test here asserts on *content*: the parameter name the route actually
reads, the identifier the next tool needs, the sheet names, the sensitive column
names, the untruncated health summary. A test that only proved "a string came
back" would pass against a tool that returned the word "ok".

Deliberately not covered:

* That the service produces these payloads. The harness cans them from the
  service's own response models, which catches a rename but not a semantic
  change; ``tests/test_mcp_endpoint.py`` drives the real routes against real
  data and is authoritative where the two disagree.
* Pydantic argument constraints (``limit`` ge/le). Calling a tool through
  ``ToolSet`` bypasses the MCP argument model by design; see
  ``test_mcp_unknown_arguments.py`` for the schema-level style.
* Substitution mechanics of the harness itself — ``test_mcp_harness_smoke.py``
  owns those, and the basic happy paths it already pins are not repeated here.
"""

from __future__ import annotations

import pytest
from mcp.server.mcpserver.exceptions import ToolError

from app.features.mcp.tools import orient
from mcp_harness import (
    FakeAnalyticsClient,
    column_hit,
    column_metadata,
    dataset,
    health,
    me,
    page,
    problem,
    register_tools,
    sheet,
    sheet_column,
    sheet_metadata,
    version,
)


@pytest.fixture
def client() -> FakeAnalyticsClient:
    return FakeAnalyticsClient()


@pytest.fixture
def tools(client):
    return register_tools(orient, client)


def _table_rows(out: str) -> list[list[str]]:
    """Pipe-delimited data rows, split into cells."""
    return [
        [cell.strip() for cell in line.split("|")]
        for line in out.splitlines()
        if "|" in line
    ]


# ---------------------------------------------------------------------------
# whoami — the tool the model is told to call when something is "not found"
# ---------------------------------------------------------------------------


async def test_an_unusable_session_identity_names_the_header_that_carries_it(tools, client):
    """whoami is where a misconfigured MCP session is meant to be diagnosed. The
    service's own 401 detail describes an authentication failure in general; it
    does not mention ``X-User-Id``, which is the single thing an operator has to
    fix. Without that name in the message the failure is indistinguishable from
    a credential problem inside the service, and the session is unusable for
    every tool, not just this one."""
    client.on_get("/auth/me", problem(401, "Not authenticated", "http-401"))
    with pytest.raises(ToolError) as excinfo:
        await tools["whoami"]()

    message = str(excinfo.value)
    assert "Not authenticated" in message
    assert "X-User-Id" in message
    assert "does not name an active analytics-service user" in message


async def test_a_user_in_no_team_is_shown_the_state_that_hides_every_dataset(tools, client):
    """Zero memberships is the exact condition under which ``search_datasets``
    returns nothing and every ``dataset_id`` 404s — and it is invisible from
    those tools, because the service filters rather than forbids. If whoami
    renders an empty team list without saying the count is zero, the one tool
    that can explain the silence does not explain it."""
    client.on_get("/auth/me", me(name="Ada Lovelace", teams=[]))
    out = await tools["whoami"]()

    assert "teams: 0" in out
    assert "(none)" in out
    # And an ordinary caller must not be told masking is off for them.
    assert "superuser: false" in out
    assert "unmasked" not in out


async def test_exactly_ten_teams_is_not_reported_as_a_truncated_list(tools, client):
    """The list is capped at ten. An off-by-one in that cap invents "…and 0
    more", which tells the model its team list is incomplete and that the
    dataset it cannot see might be in a team it was not shown — sending it to
    an administrator over a complete answer."""
    client.on_get("/auth/me", me(teams=[(f"Team {i}", "viewer") for i in range(10)]))
    out = await tools["whoami"]()

    assert "teams: 10" in out
    assert out.count("(viewer)") == 10
    assert "more" not in out


# ---------------------------------------------------------------------------
# search_datasets — the only source of a dataset_id
# ---------------------------------------------------------------------------


async def test_each_filter_reaches_the_route_under_the_name_the_route_reads(tools, client):
    """Four of the six filter names differ between the tool and the route
    (``query``→``q``, ``favorites_only``→``favorites``), and FastAPI ignores a
    query parameter it does not declare. A rename on either side therefore does
    not fail — it silently drops the filter and returns a *superset*, which
    reads exactly like a valid answer and is the worst possible outcome for a
    tool whose job is narrowing thousands of datasets to one."""
    client.on_get("/datasets", page([dataset()]))
    await tools["search_datasets"](
        query="revenue",
        domain="finance",
        favorites_only=True,
        validation_status="passed",
        documentation="full",
        limit=5,
        offset=10,
    )

    assert client.one_call_to("GET", "/datasets").params == {
        "q": "revenue",
        "domain": "finance",
        "favorites": True,
        "validation_status": "passed",
        "documentation": "full",
        "limit": 5,
        "offset": 10,
    }


async def test_the_result_table_carries_the_trust_signals_not_only_the_names(tools, client):
    """This table is the whole basis on which a model picks one dataset out of
    many, and it is the last chance to do so cheaply — the next call reads data.
    Dropping ``validation`` or ``docs`` from the row does not break anything
    visibly; it just means the model picks the dataset whose validation failed
    and reports its numbers as fact."""
    client.on_get(
        "/datasets",
        page([
            dataset(id="ds-good", name="Orders", current_version=4,
                    validation_status="passed", documentation="full"),
            dataset(id="ds-bad", name="Orders (raw)", current_version=1,
                    validation_status="failed", documentation="none"),
        ]),
    )
    out = await tools["search_datasets"](query="orders")

    header, first, second = _table_rows(out)[:3]
    assert header == ["dataset_id", "name", "domain", "version", "rows",
                      "validation", "docs"]
    assert first[0] == "ds-good" and first[5] == "passed" and first[6] == "full"
    assert second[0] == "ds-bad" and second[5] == "failed" and second[6] == "none"
    # The version number is what pins a later query to the rows seen here.
    assert first[3] == "4"


# ---------------------------------------------------------------------------
# describe_dataset — the call every query is supposed to be based on
# ---------------------------------------------------------------------------


async def test_a_dataset_that_is_still_processing_shows_why_it_has_no_schema(tools, client):
    """A freshly uploaded dataset has a version but no ready sheets, so the
    schema half of this answer is legitimately empty. Rendering only "(none)"
    would read as "this dataset has no columns" — a permanent-sounding fact —
    when the version table right below it says ``processing`` and the correct
    action is to wait and call again."""
    client.on_get("/datasets/ds-1/sheets", page([]))
    client.on_get("/datasets/ds-1/versions",
                  page([version(version_number=1, status="processing")]))
    out = await tools["describe_dataset"](dataset_id="ds-1")

    assert "## Sheets (current version)\n(none)" in out
    assert "processing" in out
    assert client.trace() == [("GET", "/datasets/ds-1/sheets"),
                              ("GET", "/datasets/ds-1/versions")]


async def test_only_the_ten_newest_versions_are_listed_and_newest_comes_first(tools, client):
    """Versions are immutable, so a busy dataset accumulates them forever and an
    uncapped table would dominate the response to a question about *columns*.
    The cap is only safe while the order is newest-first: reversed, this tool
    would offer ten obsolete versions and hide the one that holds today's data,
    and every number the model then quotes would be historic."""
    client.on_get("/datasets/ds-1/sheets", page([sheet()]))
    client.on_get(
        "/datasets/ds-1/versions",
        page([version(version_number=n) for n in range(15, 0, -1)], total=15),
    )
    out = await tools["describe_dataset"](dataset_id="ds-1")

    listed = [row[0] for row in _table_rows(out) if row[0].isdigit()]
    assert listed == ["15", "14", "13", "12", "11", "10", "9", "8", "7", "6"]


async def test_version_tags_are_rendered_as_names_a_caller_can_reuse(tools, client):
    """Tags apply to a whole version and are how a caller pins one without
    knowing its number. A Python list rendered through ``str`` gives
    ``['prod', 'q1-close']``, and a model copying that into a later argument
    sends the brackets and quotes with it."""
    client.on_get("/datasets/ds-1/sheets", page([sheet()]))
    client.on_get(
        "/datasets/ds-1/versions",
        page([version(version_number=7, tags=["prod", "q1-close"])]),
    )
    out = await tools["describe_dataset"](dataset_id="ds-1")

    assert "prod, q1-close" in out
    assert "['prod'" not in out


# ---------------------------------------------------------------------------
# get_data_dictionary — what the columns MEAN, which no schema can say
# ---------------------------------------------------------------------------


async def test_the_sheet_level_call_does_not_guess_at_a_sheet_to_expand(tools, client):
    """Called without ``sheet_key`` this tool has no key to expand, and the
    column route is keyed on one. Inventing a key (the first sheet, say) would
    make the answer depend on sheet order and could silently describe the wrong
    sheet — so it must stop at one request and hand back the way to get the key,
    which is a tool name the model can act on immediately."""
    client.on_get(
        "/datasets/ds-1/sheet-metadata",
        page([sheet_metadata(sheet_key="q1_revenue", grain="one row per order line")]),
    )
    out = await tools["get_data_dictionary"](dataset_id="ds-1")

    assert client.trace() == [("GET", "/datasets/ds-1/sheet-metadata")]
    assert "Pass sheet_key to see the column-level dictionary" in out
    assert "describe_dataset" in out


async def test_the_grain_and_primary_key_are_reported_because_totals_depend_on_them(tools, client):
    """The grain is the difference between ``SUM(amount)`` being revenue and
    being revenue multiplied by the number of line items, and the primary key is
    the only way to know a join will not fan out. Neither is derivable from the
    schema, so if this tool drops them the model has no source for them at all
    and will assume one row per entity."""
    client.on_get(
        "/datasets/ds-1/sheet-metadata",
        page([sheet_metadata(
            sheet_key="q1_revenue",
            grain="one row per order line",
            primary_key_columns=["order_id", "line_no"],
            description="Recognised revenue, restated monthly",
        )]),
    )
    out = await tools["get_data_dictionary"](dataset_id="ds-1")

    assert "one row per order line" in out
    assert "order_id, line_no" in out
    assert "Recognised revenue, restated monthly" in out


async def test_a_dataset_with_no_dictionary_says_so_instead_of_showing_an_empty_table(tools, client):
    """``render.table([])`` is the string "(no rows)", which in a section headed
    "Sheet metadata" reads as a failure or as an empty dataset. The absence of a
    dictionary is neither — it is an undocumented but perfectly queryable
    dataset, and the message has to name the three things that were missing so
    the model knows what it is going without."""
    client.on_get("/datasets/ds-1/sheet-metadata", page([]))
    out = await tools["get_data_dictionary"](dataset_id="ds-1")

    assert "(no rows)" not in out
    assert "No sheet-level metadata (grain, primary key, description) has been recorded" in out


async def test_columns_tagged_sensitive_are_named_together_with_where_masking_applies(tools, client):
    """Two different mistakes hang on this sentence. A model that does not know
    a column is masked will compute on the mask characters and report the result
    as data; a model that thinks masking is universal will use ``run_sql`` on a
    PII column believing the service will protect it. So the tagged names and
    the scope of the masking have to arrive together, in the tool whose job is
    explaining what the columns mean."""
    client.on_get("/datasets/ds-1/sheet-metadata", page([sheet_metadata(sheet_key="q1")]))
    client.on_get(
        "/datasets/ds-1/sheet-metadata/q1/columns",
        page([
            column_metadata("email", business_name="Contact email",
                            semantic_type="email", sensitivity="pii"),
            column_metadata("amount", business_name="Order amount",
                            semantic_type="currency_usd", unit="USD"),
        ]),
    )
    out = await tools["get_data_dictionary"](dataset_id="ds-1", sheet_key="q1")

    assert "## Columns — q1" in out
    assert "Contact email" in out and "currency_usd" in out
    assert "Tagged sensitive: email." in out
    assert "amount" not in out.split("Tagged sensitive:")[1]
    assert "run_sql is never masked" in out
    assert client.trace() == [
        ("GET", "/datasets/ds-1/sheet-metadata"),
        ("GET", "/datasets/ds-1/sheet-metadata/q1/columns"),
    ]


async def test_an_undocumented_sheet_is_reported_as_unjudged_rather_than_as_safe(tools, client):
    """"No entries" and "nothing sensitive here" are the same output and
    opposite facts. Since masking is driven entirely by these tags, an
    undocumented sheet is exactly the one where PII will come back in the clear
    — so the message has to push the judgement back onto the caller, and name
    the sheet_key it looked under so a typo is visible rather than being read as
    an all-clear."""
    client.on_get("/datasets/ds-1/sheet-metadata", page([sheet_metadata(sheet_key="q1")]))
    client.on_get("/datasets/ds-1/sheet-metadata/q1/columns", page([]))
    out = await tools["get_data_dictionary"](dataset_id="ds-1", sheet_key="q1")

    assert "No column dictionary entries recorded for sheet 'q1'." in out
    assert "judge sensitivity from the column names and sample values yourself" in out
    assert "Tagged sensitive" not in out


async def test_a_closed_set_of_numeric_codes_is_still_rendered(tools, client):
    """``allowed_values`` is typed ``list[Any]`` because status codes and rating
    scales are stored as numbers. Joining them without coercion raises
    ``TypeError: sequence item 0: expected str, int found`` — the whole tool
    call fails, and it fails only for the datasets whose enumerations are
    numeric, which is the sort of gap a happy-path fixture never touches."""
    client.on_get("/datasets/ds-1/sheet-metadata", page([sheet_metadata(sheet_key="q1")]))
    client.on_get(
        "/datasets/ds-1/sheet-metadata/q1/columns",
        page([column_metadata("status_code", allowed_values=[10, 20, 30])]),
    )
    out = await tools["get_data_dictionary"](dataset_id="ds-1", sheet_key="q1")

    assert "10, 20, 30" in out


async def test_a_sheet_key_that_does_not_exist_is_reported_with_the_key(tools, client):
    """``sheet_key`` is a normalized slug the model has to have copied from
    ``describe_dataset``, so a wrong one is nearly always a transcription error
    it can fix itself — but only if the rejected key is echoed back. The 404
    caveat matters here too: this route 404s for a sheet in someone else's
    dataset the same way it does for a misspelling."""
    client.on_get("/datasets/ds-1/sheet-metadata", page([sheet_metadata(sheet_key="q1_revenue")]))
    client.on_get(
        "/datasets/ds-1/sheet-metadata/q1/columns",
        problem(404, "Sheet not found: q1", "http-404"),
    )
    with pytest.raises(ToolError) as excinfo:
        await tools["get_data_dictionary"](dataset_id="ds-1", sheet_key="q1")

    message = str(excinfo.value)
    assert "Sheet not found: q1" in message
    assert "404 both for things that do not exist" in message


# ---------------------------------------------------------------------------
# search_columns — finding the dataset from the field
# ---------------------------------------------------------------------------


async def test_a_column_hit_carries_both_identifiers_needed_to_query_it(tools, client):
    """This tool exists to be used when the dataset is unknown, so a hit that
    names only the dataset and the column is half an answer: every query also
    needs the ``sheet_key``, and getting it means a second ``describe_dataset``
    round trip per hit. The human-readable names alone are not usable — neither
    the dataset name nor the sheet name is accepted as an argument anywhere."""
    client.on_get(
        "/search/columns",
        page([column_hit(dataset_id="ds-9", dataset_name="Orders", sheet_name="Q1 Revenue",
                         sheet_key="q1_revenue", column_name="customer_id", dtype="int64")],
             total=3),
    )
    out = await tools["search_columns"](query="customer")

    header, hit = _table_rows(out)[:2]
    assert header == ["dataset", "dataset_id", "sheet", "sheet_key", "column", "dtype"]
    assert hit == ["Orders", "ds-9", "Q1 Revenue", "q1_revenue", "customer_id", "int64"]
    assert "1 of 3 columns shown." in out


# ---------------------------------------------------------------------------
# get_dataset_health — is this dataset worth analysing
# ---------------------------------------------------------------------------


async def test_a_long_health_summary_is_never_truncated(tools, client):
    """The status word is not the actionable part — the summary is, and it is
    where the offending column names live. This read-out is rendered as lines
    precisely so it escapes the 80-character cell limit that applies to every
    table in this layer; render it as a table and the sentence gets cut at the
    point where it starts naming names, leaving a warning the model can neither
    act on nor discount."""
    long_summary = (
        "3 of 12 columns are more than 40% null: shipping_note, promo_code, "
        "refund_reason — all optional at capture time"
    )
    assert len(long_summary) > 80
    client.on_get("/datasets/ds-1/health",
                  health({"missing_data": ("warning", long_summary)}))
    out = await tools["get_dataset_health"](dataset_id="ds-1")

    assert long_summary in out
    assert "…" not in out


async def test_a_fully_profiled_dataset_carries_no_unknown_disclaimer(tools, client):
    """The 'unknown means no profile' paragraph is there to stop a model
    treating a missing profile as bad data. Emitted unconditionally it does the
    opposite damage: it invites the model to discount statuses that *are*
    populated, and it repeats a paragraph of boilerplate on every health read.
    The "no aggregate score" line, by contrast, must be unconditional — models
    otherwise average the seven dimensions into a number the service
    deliberately refuses to define."""
    client.on_get("/datasets/ds-1/health", health(
        {"validation": ("ok", "Last run passed"),
         "freshness": ("warning", "Newest version is 90 days old")},
        current_version_number=7,
    ))
    out = await tools["get_dataset_health"](dataset_id="ds-1")

    assert "current_version: 7" in out
    assert "validation: ok — Last run passed" in out
    assert "check_quality" not in out
    assert "'unknown'" not in out
    assert "no single aggregate score" in out

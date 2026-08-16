"""``app/features/mcp/tools/look.py`` — the two tools that return actual rows.

``query_rows`` and ``run_sql`` are where a model spends most of its budget, and
they are the two tools that can hurt it in ways it cannot detect: a spec silently
dropped on the way to the service, a masked column read as real data, a page that
was not the last one with nothing saying so, or a table name it guessed wrong and
is given no way to correct. Every test below asserts on the *content* the tool
put in front of the model — the request it actually sent, the numbers in the
count note, the sheet keys in a failed-SQL hint — because a test that only
asserts "an error was raised" would pass against a version that returned the
bare word "error", which is the failure this layer exists to prevent.

Driven through ``mcp_harness.FakeAnalyticsClient``, so the tool bodies, the
rendering and the real ``@guard`` translation run with no Postgres and no
storage. What that cannot prove is that the service ever produces these
payloads; ``tests/test_mcp_endpoint.py`` makes that claim against real data.

Deliberately not covered here:

* The full branch matrix of ``_common.explain``. Only the codes ``look.py``'s own
  parameters can provoke are exercised (sheet selection, unknown column, unknown
  operator, select-only, 404); the rest belong with ``_common``.
* Pydantic argument constraints, which ``ToolSet`` bypasses — the one test that
  needs them reads the published schema off ``tools.server`` instead.
* Whether the sandbox actually rejects a non-SELECT, or masking actually masks.
  Both are service behaviour reached over a database.
"""

from __future__ import annotations

import json
import re

import pytest
from mcp.server.mcpserver.exceptions import ToolError

from app.features.mcp.tools import look
from app.shared.duck import SQL_ROW_CAP
from app.shared.query.schemas import KNOWN_OPS, FilterGroup
from mcp_harness import (
    FakeAnalyticsClient,
    page,
    problem,
    query_page,
    register_tools,
    sheet,
    sheet_selection_required,
    sql_result,
    unknown_column,
    version,
)


@pytest.fixture
def client() -> FakeAnalyticsClient:
    return FakeAnalyticsClient()


@pytest.fixture
def tools(client):
    return register_tools(look, client)


def _versions(*items):
    """``GET /datasets/{id}/versions`` — newest first, as the service returns it."""
    return page(list(items))


# ---------------------------------------------------------------------------
# Choosing the version — shared by both tools
# ---------------------------------------------------------------------------


async def test_omitting_the_version_reads_the_newest_ready_one_not_the_newest(tools, client):
    """A version that is still ingesting is listed before the readable one. If
    the tool took the newest entry outright it would query a version whose rows
    do not exist yet, and the model would be told the dataset is empty rather
    than that it is not ready."""
    client.on_get(
        "/datasets/ds-1/versions",
        _versions(version(version_number=5, status="processing"),
                  version(version_number=4, status="ready"),
                  version(version_number=3, status="ready")),
    )
    client.on_post("/datasets/ds-1/versions/4/sheets/Q1/query", query_page([{"a": 1}]))

    out = await tools["query_rows"](dataset_id="ds-1", sheet="Q1")

    assert client.trace() == [
        ("GET", "/datasets/ds-1/versions"),
        ("POST", "/datasets/ds-1/versions/4/sheets/Q1/query"),
    ]
    assert "version: 4" in out


async def test_an_explicit_version_is_honoured_without_a_lookup_round_trip(tools, client):
    """Pinning a version is how a model reproduces an earlier answer. Resolving
    anyway would both cost a call and — on a dataset whose newest ready version
    has moved on — quietly answer about different data than was asked for."""
    client.on_post("/datasets/ds-1/versions/2/sheets/Q1/query", query_page([{"a": 1}]))

    out = await tools["query_rows"](dataset_id="ds-1", sheet="Q1", version=2)

    assert client.trace() == [("POST", "/datasets/ds-1/versions/2/sheets/Q1/query")]
    assert "version: 2" in out


async def test_a_dataset_with_no_ready_version_says_why_rather_than_returning_nothing(
    tools, client
):
    """The upload-still-processing case. An empty result here is the single most
    misleading answer available: the data exists, it is just not readable yet,
    and a model told "0 rows" will report the dataset as empty to a user."""
    client.on_get("/datasets/ds-1/versions",
                  _versions(version(version_number=1, status="processing")))

    with pytest.raises(ToolError) as exc:
        await tools["query_rows"](dataset_id="ds-1", sheet="Q1")

    assert "no ready version" in str(exc.value)
    assert "still be processing" in str(exc.value)
    # And it must not have gone on to query a version it never resolved.
    assert client.trace() == [("GET", "/datasets/ds-1/versions")]


# ---------------------------------------------------------------------------
# query_rows — what actually goes on the wire
# ---------------------------------------------------------------------------


async def test_query_rows_addresses_the_named_sheet_by_path_not_by_body_field(tools, client):
    """The sheet is a path segment, and every filter/sort route in the service is
    scoped that way. Sending it in the body instead would land on the
    auto-resolving route, which on a multi-sheet version returns
    sheet-selection-required even though the caller did name a sheet."""
    client.on_post("/datasets/ds-1/versions/7/sheets/Q1/query", query_page([{"a": 1}]))

    await tools["query_rows"](dataset_id="ds-1", sheet="Q1", version=7)

    call = client.one_call_to("POST", "/datasets/ds-1/versions/7/sheets/Q1/query")
    assert "sheet" not in call.body


async def test_omitting_the_sheet_posts_to_the_version_scoped_route(tools, client):
    """A single-sheet dataset must be readable without the caller first learning
    the sheet's name. The service resolves the only sheet on the un-scoped route;
    inventing a name like "Sheet1" here would 404 on most real datasets."""
    client.on_post("/datasets/ds-1/versions/7/query", query_page([{"a": 1}]))

    out = await tools["query_rows"](dataset_id="ds-1", version=7)

    assert client.trace() == [("POST", "/datasets/ds-1/versions/7/query")]
    # Nothing claims a sheet name that was never established.
    assert "sheet:" not in out


async def test_a_plain_preview_sends_only_a_limit(tools, client):
    """The no-arguments case must be a clean preview. An explicit null filter or
    an empty sort array reaching the service is a 422 on a request the caller
    made correctly."""
    client.on_post("/datasets/ds-1/versions/7/sheets/Q1/query", query_page([{"a": 1}]))

    await tools["query_rows"](dataset_id="ds-1", sheet="Q1", version=7, limit=25)

    assert client.one_call_to(
        "POST", "/datasets/ds-1/versions/7/sheets/Q1/query"
    ).body == {"limit": 25}


async def test_an_empty_projection_means_every_column_not_no_columns(tools, client):
    """``columns=[]`` is what a model emits when it built the projection list
    from an empty loop. Forwarding it would ask the service for a zero-column
    result — either a 422 or rows with nothing in them — when the honest reading
    of "no columns named" is "all of them", which is also the tool's documented
    default. Same reasoning for an empty filter tree, sort list or search string."""
    client.on_post("/datasets/ds-1/versions/7/sheets/Q1/query", query_page([{"a": 1}]))

    await tools["query_rows"](
        dataset_id="ds-1", sheet="Q1", version=7,
        columns=[], filters={}, sort=[], search="", cursor="",
    )

    assert client.one_call_to(
        "POST", "/datasets/ds-1/versions/7/sheets/Q1/query"
    ).body == {"limit": 50}


async def test_the_filter_tree_reaches_the_service_exactly_as_written(tools, client):
    """This layer used to mirror the filter grammar and normalise it on the way
    through; that mirror drifted and was deleted (see ``_common``'s module
    docstring). Any re-introduced rewriting — flattening a single-condition
    group, defaulting a logic key, coercing a value — changes which rows come
    back while still returning a confident answer, so the body must be byte-for-
    byte what the caller composed."""
    filters = {
        "logic": "or",
        "conditions": [
            {"column": "region", "op": "in", "value": ["US", "CA"]},
            {"logic": "and", "conditions": [
                {"column": "amount", "op": "gt", "value": 100},
                {"column": "note", "op": "contains", "value": "rush", "case_sensitive": False},
            ]},
        ],
    }
    sort = [{"column": "amount", "direction": "desc"}]
    client.on_post("/datasets/ds-1/versions/7/sheets/Q1/query", query_page([]))

    await tools["query_rows"](
        dataset_id="ds-1", sheet="Q1", version=7, filters=filters, sort=sort,
        columns=["region", "amount"], search="rush", cursor="c-1", limit=200,
    )

    assert client.one_call_to("POST", "/datasets/ds-1/versions/7/sheets/Q1/query").body == {
        "limit": 200,
        "columns": ["region", "amount"],
        "filters": filters,
        "search": "rush",
        "sort": sort,
        "cursor": "c-1",
    }


# ---------------------------------------------------------------------------
# query_rows — what the model reads back
# ---------------------------------------------------------------------------


async def test_the_result_states_which_dataset_version_and_sheet_produced_it(tools, client):
    """A model interleaves several datasets in one context. Rows with no
    provenance line get attributed to whichever dataset was discussed last, and
    the version matters because a re-run after an upload is a different answer."""
    client.on_post("/datasets/ds-1/versions/4/sheets/Q1/query",
                   query_page([{"region": "US"}]))

    out = await tools["query_rows"](dataset_id="ds-1", sheet="Q1", version=4)

    assert "dataset: ds-1" in out
    assert "version: 4" in out
    assert "sheet: Q1" in out


async def test_rows_are_rendered_in_the_column_order_that_was_requested(tools, client):
    """The projection order is a caller instruction, not a formatting detail: it
    is how a model lines a table up against one it printed earlier. JSON object
    order from the service is not a contract, so the requested order has to win."""
    client.on_post(
        "/datasets/ds-1/versions/4/sheets/Q1/query",
        query_page([{"amount": 10, "region": "US"}, {"amount": 20, "region": "CA"}]),
    )

    out = await tools["query_rows"](
        dataset_id="ds-1", sheet="Q1", version=4, columns=["region", "amount"]
    )

    assert "region | amount" in out
    assert "US | 10" in out
    assert "CA | 20" in out


async def test_no_matching_rows_is_stated_in_words_not_left_as_a_blank(tools, client):
    """An empty region between two headings reads as a rendering failure, and a
    model retries a query it should have concluded matched nothing. "(no rows)"
    plus an explicit count is an answer it can act on."""
    client.on_post("/datasets/ds-1/versions/4/sheets/Q1/query", query_page([], total=0))

    out = await tools["query_rows"](dataset_id="ds-1", sheet="Q1", version=4)

    assert "(no rows)" in out
    assert "0 rows shown." in out


async def test_a_partial_page_reports_the_total_it_was_taken_from(tools, client):
    """Without the total, 50 returned rows are indistinguishable from 50 matching
    rows, and a model will happily compute a sum over the first page and present
    it as the answer for the whole dataset."""
    client.on_post("/datasets/ds-1/versions/4/sheets/Q1/query",
                   query_page([{"a": 1}, {"a": 2}], total=904))

    out = await tools["query_rows"](dataset_id="ds-1", sheet="Q1", version=4, limit=2)

    assert "2 of 904 rows shown." in out


async def test_a_complete_result_is_not_dressed_up_as_a_partial_one(tools, client):
    """The mirror of the above: when the page is everything, saying "2 of 2"
    invites a pointless follow-up page request."""
    client.on_post("/datasets/ds-1/versions/4/sheets/Q1/query",
                   query_page([{"a": 1}, {"a": 2}], total=2))

    out = await tools["query_rows"](dataset_id="ds-1", sheet="Q1", version=4)

    assert "2 rows shown." in out
    assert " of " not in out


async def test_more_rows_available_comes_with_the_cursor_needed_to_get_them(tools, client):
    """The one piece of state the caller cannot derive. Announcing that more rows
    exist without handing over the opaque cursor — and without saying the rest of
    the spec must be repeated unchanged — costs a round trip at best and produces
    a second page from a different query at worst."""
    client.on_post(
        "/datasets/ds-1/versions/4/sheets/Q1/query",
        query_page([{"a": 1}], total=500, next_cursor="eyJvIjoxfQ=="),
    )

    out = await tools["query_rows"](dataset_id="ds-1", sheet="Q1", version=4, limit=1)

    assert "cursor='eyJvIjoxfQ=='" in out
    assert "query_rows" in out
    assert "same spec" in out


async def test_the_last_page_does_not_offer_a_cursor(tools, client):
    """A model handed a paging instruction will follow it, and a cursor-less
    "more rows available" note is an instruction it cannot follow."""
    client.on_post("/datasets/ds-1/versions/4/sheets/Q1/query",
                   query_page([{"a": 1}], total=1, next_cursor=None))

    out = await tools["query_rows"](dataset_id="ds-1", sheet="Q1", version=4)

    assert "cursor" not in out
    assert "More rows available" not in out


async def test_columns_the_service_withheld_are_named_in_the_response(tools, client):
    """Masking replaces the value in place — the key is still there, holding
    something that looks like an email or an ID. Nothing in the row says it is a
    placeholder, so a model without this note will aggregate over masked values,
    join on them, or quote them back to the user as real data."""
    client.on_post(
        "/datasets/ds-1/versions/4/sheets/Q1/query",
        query_page([{"email": "a***@example.com", "amount": 5}],
                   masked_columns=["email", "ssn"]),
    )

    out = await tools["query_rows"](dataset_id="ds-1", sheet="Q1", version=4)

    assert "withheld these columns from you: email, ssn." in out


async def test_an_unmasked_result_carries_no_masking_note(tools, client):
    """A standing "nothing was withheld" line trains a model to skip the line,
    which is precisely when the real one appears."""
    client.on_post("/datasets/ds-1/versions/4/sheets/Q1/query",
                   query_page([{"amount": 5}], masked_columns=[]))

    out = await tools["query_rows"](dataset_id="ds-1", sheet="Q1", version=4)

    assert "withheld" not in out


# ---------------------------------------------------------------------------
# query_rows — turning a rejection into the next call
# ---------------------------------------------------------------------------


async def test_a_multi_sheet_version_answers_with_the_sheet_names(tools, client):
    """The whole product of this layer in one case. "Name a sheet" costs a
    describe_dataset round trip; "name a sheet — here they are" is answerable on
    the spot, and the names are the only thing the model is missing."""
    client.on_post("/datasets/ds-1/versions/4/query",
                   sheet_selection_required(["Q1 2026", "Q2 2026", "Notes"]))

    with pytest.raises(ToolError) as exc:
        await tools["query_rows"](dataset_id="ds-1", version=4)

    assert "Available sheets: Q1 2026, Q2 2026, Notes." in str(exc.value)


async def test_an_unknown_column_answers_with_the_columns_that_do_exist(tools, client):
    """A misremembered column name is the most common failure in a long session.
    Echoing the available ones turns a retry loop into one corrected call, and
    naming describe_dataset covers the case where the list was itself truncated."""
    client.on_post(
        "/datasets/ds-1/versions/4/sheets/Q1/query",
        unknown_column("revenu", ["region", "revenue", "amount", "order_date"]),
    )

    with pytest.raises(ToolError) as exc:
        await tools["query_rows"](dataset_id="ds-1", sheet="Q1", version=4,
                                  columns=["revenu"])

    message = str(exc.value)
    assert "'revenu'" in message
    assert "Available columns: region, revenue, amount, order_date." in message
    assert "describe_dataset" in message


async def test_an_unknown_filter_operator_answers_with_the_whole_vocabulary(tools, client):
    """``filters`` is the richest thing a model has to compose blind, and an
    invented operator (``like``, ``>=``, ``not_empty``) is the usual result. The
    reply has to carry both the legal operators and the node shape, or the next
    attempt guesses again."""
    client.on_post(
        "/datasets/ds-1/versions/4/sheets/Q1/query",
        problem(400, "Unknown filter operator: 'like'", "unknown-operator",
                column="note", available=["contains", "icontains", "regex"]),
    )

    with pytest.raises(ToolError) as exc:
        await tools["query_rows"](
            dataset_id="ds-1", sheet="Q1", version=4,
            filters={"column": "note", "op": "like", "value": "x"},
        )

    message = str(exc.value)
    assert "Valid filter operators: contains, icontains, regex." in message
    assert "'logic': 'and'|'or'" in message
    assert "'column': ..., 'op': ..., 'value': ..." in message


async def test_a_404_says_it_may_mean_forbidden_rather_than_absent(tools, client):
    """The service returns 404 for a dataset owned by another team, on purpose.
    A model that reads it literally tells the user their dataset was deleted;
    the caveat redirects it to whoami and an access request instead."""
    client.on_post("/datasets/ds-1/versions/4/sheets/Q1/query",
                   problem(404, "Dataset not found", "not-found"))

    with pytest.raises(ToolError) as exc:
        await tools["query_rows"](dataset_id="ds-1", sheet="Q1", version=4)

    assert "owned by a team you are not in" in str(exc.value)


# ---------------------------------------------------------------------------
# FILTER_HELP — the grammar the model is handed before it composes anything
# ---------------------------------------------------------------------------


def _documented_operators() -> set[str]:
    """The operator names listed in FILTER_HELP, parenthesised prose removed."""
    body = look.FILTER_HELP.split("Operators:", 1)[1].split("Example:", 1)[0]
    body = re.sub(r"\([^)]*\)", "", body)
    return {token.strip(" .\n") for token in re.split(r"[,;\n]", body)} - {""}


def test_the_documented_operators_are_exactly_the_ones_the_service_accepts():
    """``FILTER_HELP`` is the only description of the filter grammar a model ever
    sees, and it is a hand-written copy of a Literal in another package. Both
    drift directions are silent and expensive: an operator added to the service
    and not to this text is unreachable, and an operator documented here but
    removed from the service produces a 400 on a filter the model composed by
    following its instructions exactly."""
    assert _documented_operators() == set(KNOWN_OPS)


def test_the_worked_example_is_a_filter_the_service_would_accept():
    """A model copies the example before it reads the grammar. An example that
    fails validation — a stale key name, a group nested where a condition
    belongs — is worse than none, because it fails on the first call and looks
    like a service fault."""
    example = json.loads(look.FILTER_HELP.split("Example:", 1)[1])
    parsed = FilterGroup.model_validate(example)

    assert parsed.logic == "and"
    assert [c.op for c in parsed.conditions] == ["in", "gt"]


async def test_the_filter_grammar_is_published_in_the_tools_own_schema(tools):
    """The help is only worth maintaining if it reaches the model. It is attached
    as the ``filters`` field description, which is what ``tools/list`` sends; if
    it stops being wired there the text stays perfectly correct and perfectly
    invisible."""
    published = {t.name: t.input_schema for t in await tools.server.list_tools()}
    described = published["query_rows"]["properties"]["filters"]["description"]

    assert "is_duplicate" in described
    assert described == look.FILTER_HELP


# ---------------------------------------------------------------------------
# run_sql — request, and the bounded result
# ---------------------------------------------------------------------------


async def test_run_sql_posts_the_statement_to_the_resolved_versions_sandbox(tools, client):
    """SQL runs against one version's materialised tables. Losing the version in
    path construction would silently execute against whatever the sandbox mounted
    by default, and the answer would look completely normal."""
    client.on_get("/datasets/ds-1/versions", _versions(version(version_number=9)))
    client.on_post("/datasets/ds-1/versions/9/sql", sql_result([{"n": 1}]))

    out = await tools["run_sql"](dataset_id="ds-1", sql="SELECT count(*) AS n FROM sheet1")

    assert client.one_call_to("POST", "/datasets/ds-1/versions/9/sql").body == {
        "sql": "SELECT count(*) AS n FROM sheet1"
    }
    assert "version: 9" in out


async def test_the_result_names_the_tables_that_were_queryable(tools, client):
    """The reply doubles as a schema hint for the next query: a model that
    guessed one table name right learns the others without a second tool call,
    and learns immediately if a sheet it expected was not mounted."""
    client.on_post("/datasets/ds-1/versions/2/sql",
                   sql_result([{"n": 3}], tables=["orders", "returns"], row_count=3))

    out = await tools["run_sql"](dataset_id="ds-1", version=2, sql="SELECT 1 AS n")

    assert "tables: orders, returns" in out
    assert "row_count: 3" in out


async def test_result_columns_are_rendered_in_the_engines_own_order(tools, client):
    """SELECT order is meaningful to whoever wrote the SELECT — it is how a model
    reads positional results back. Re-deriving the header from row keys would
    reorder it, and a NULL in the first row would drop a column from the header
    entirely."""
    client.on_post(
        "/datasets/ds-1/versions/2/sql",
        sql_result([{"total": 10, "region": "US"}], columns=["region", "total"]),
    )

    out = await tools["run_sql"](dataset_id="ds-1", version=2, sql="SELECT region, total FROM t")

    assert "region | total" in out
    assert "US | 10" in out


async def test_showing_fewer_rows_than_returned_says_how_many_were_withheld(tools, client):
    """``max_rows`` trims the rendered table but not the result. Without the
    count, a model treats the shown rows as the complete result of its own
    aggregate — and unlike a paged query there is no cursor here to hint
    otherwise, only the artifact."""
    rows = [{"i": i, "label": f"row-{i}"} for i in range(5)]
    client.on_post("/datasets/ds-1/versions/2/sql", sql_result(rows))

    out = await tools["run_sql"](dataset_id="ds-1", version=2, sql="SELECT i, label FROM t",
                                 max_rows=2)

    assert "Showing 2 of 5 returned rows." in out
    # Trimmed from the top of the result, not sampled from it.
    assert "0 | row-0" in out and "1 | row-1" in out
    assert "row-2" not in out and "row-4" not in out


async def test_a_fully_shown_result_is_not_annotated_as_partial(tools, client):
    """The common case must stay quiet; a "showing 3 of 3" note next to a
    complete answer invites a re-run with a larger max_rows for nothing."""
    client.on_post("/datasets/ds-1/versions/2/sql",
                   sql_result([{"i": 0}, {"i": 1}, {"i": 2}]))

    out = await tools["run_sql"](dataset_id="ds-1", version=2, sql="SELECT i FROM t")

    assert "Showing" not in out


async def test_hitting_the_engines_row_cap_is_reported_as_incompleteness(tools, client):
    """``truncated`` means the sandbox stopped at its row cap — the result is not
    the answer to the question asked. This is the one condition where a model
    must change its approach rather than page, so the note names the cap and
    tells it to aggregate instead.

    The number is a literal in ``look.py`` and the cap lives in
    ``app.shared.duck``; asserting against the constant is what stops a raised
    cap from leaving a confidently wrong figure in front of the model."""
    client.on_post("/datasets/ds-1/versions/2/sql",
                   sql_result([{"i": 0}], truncated=True))

    out = await tools["run_sql"](dataset_id="ds-1", version=2, sql="SELECT i FROM t")

    assert f"capped this result at {SQL_ROW_CAP:,} rows" in out
    assert "aggregate or filter" in out


async def test_the_artifact_handle_is_surfaced_even_when_every_row_is_shown(tools, client):
    """The handle is the only way to chain a SQL result into another tool, and
    the caller cannot construct the filename. Emitting it only on truncation
    would make chaining depend on result size — a model that learned the pattern
    on a big result would stop finding it on a small one."""
    client.on_post("/datasets/ds-1/versions/2/sql",
                   sql_result([{"i": 0}], result_file="query_output_7.parquet"))

    out = await tools["run_sql"](dataset_id="ds-1", version=2, sql="SELECT i FROM t")

    assert "Artifact: query_output_7.parquet (read with read_artifact)." in out


async def test_a_query_that_matched_nothing_still_reports_a_result(tools, client):
    """Zero rows from a SELECT is a finding, not a failure. It must not render as
    an empty response that a model reads as a broken tool and retries."""
    client.on_post("/datasets/ds-1/versions/2/sql", sql_result([], tables=["orders"]))

    out = await tools["run_sql"](dataset_id="ds-1", version=2,
                                 sql="SELECT * FROM orders WHERE 1=0")

    assert "(no rows)" in out
    assert "tables: orders" in out
    assert "Artifact:" in out


async def test_an_enormous_result_is_cut_at_the_response_ceiling_with_a_way_forward(
    tools, client
):
    """A wide SELECT over 500 rows can emit megabytes, which blows the caller's
    context window before it can read anything. The cut has to be visible — a
    silently shortened table looks like the complete result — and it has to say
    what to do differently.

    The related hazard — the notes being appended after the table, so the
    artifact handle was the first thing truncation removed — is fixed, and
    pinned by ``tests/unit/test_mcp_response_ceiling.py``."""
    wide = [{f"c{c}": f"value-{r}-{c}-{'x' * 40}" for c in range(12)} for r in range(500)]
    client.on_post("/datasets/ds-1/versions/2/sql", sql_result(wide))

    out = await tools["run_sql"](dataset_id="ds-1", version=2, sql="SELECT * FROM t",
                                 max_rows=500)

    assert "[response truncated at 60,000 characters." in out
    assert "lower max_rows" in out
    assert len(out) < look.MAX_RESPONSE_CHARS + 500


# ---------------------------------------------------------------------------
# run_sql — the table-name hint, which is the reason this tool is usable at all
# ---------------------------------------------------------------------------


async def test_a_failed_query_answers_with_the_real_table_names(tools, client):
    """Table names are sheet *keys* — normalised, not the dataset name and not
    the tab label a user would say. A model asked to "query the orders file"
    writes ``FROM orders`` and gets a catalog error; the names cost one call the
    tool is already positioned to make, so the correction lands in one step
    instead of a describe_dataset detour."""
    client.on_get("/datasets/ds-1/versions", _versions(version(version_number=4)))
    client.on_post("/datasets/ds-1/versions/4/sql",
                   problem(400, "Query failed: Table with name orders does not exist",
                           "sql-error"))
    client.on_get(
        "/datasets/ds-1/sheets",
        page([sheet(name="Orders 2026", sheet_key="orders_2026"),
              sheet(name="Returns", sheet_key="returns")]),
    )

    with pytest.raises(ToolError) as exc:
        await tools["run_sql"](dataset_id="ds-1", sql="SELECT * FROM orders")

    message = str(exc.value)
    assert "Table with name orders does not exist" in message
    assert (
        "Queryable table names in the dataset's current version: orders_2026, returns."
        in message
    )
    assert "Table names are sheet keys, not the dataset or file name." in message


async def test_the_original_sql_error_survives_a_failed_hint_lookup(tools, client):
    """The hint is a bonus. If listing the sheets fails too — no read permission,
    a version with no recorded sheets — the model must still be told what the
    engine actually said, rather than getting an error about the enrichment."""
    client.on_post("/datasets/ds-1/versions/4/sql",
                   problem(400, "Query failed: Referenced column x not found", "sql-error"))
    client.on_get("/datasets/ds-1/sheets", problem(404, "Dataset not found", "not-found"))

    with pytest.raises(ToolError) as exc:
        await tools["run_sql"](dataset_id="ds-1", version=4, sql="SELECT x FROM t")

    message = str(exc.value)
    assert "Referenced column x not found" in message
    assert "Queryable table names" not in message


async def test_no_table_hint_is_offered_when_there_are_no_names_to_offer(tools, client):
    """An empty "Queryable table names in this version:" reads as "there are no
    tables", which is a different and wrong diagnosis of the same failure."""
    client.on_post("/datasets/ds-1/versions/4/sql",
                   problem(400, "Query failed: syntax error", "sql-error"))
    client.on_get("/datasets/ds-1/sheets", page([]))

    with pytest.raises(ToolError) as exc:
        await tools["run_sql"](dataset_id="ds-1", version=4, sql="SELEC 1")

    assert "Queryable table names" not in str(exc.value)
    assert str(exc.value).rstrip().endswith("(code: sql-error)")


async def test_a_rejected_statement_is_not_answered_with_table_names(tools, client):
    """``select-only`` means the SQL was never executed — the tables were never
    the problem. Listing them would send the model off correcting a name that was
    already right, and it costs a call on the one path that should be cheapest."""
    client.on_post(
        "/datasets/ds-1/versions/4/sql",
        problem(400, "Only a single SELECT statement is allowed", "select-only"),
    )

    with pytest.raises(ToolError) as exc:
        await tools["run_sql"](dataset_id="ds-1", version=4,
                               sql="DROP TABLE orders")

    message = str(exc.value)
    assert "exactly one SELECT statement" in message
    assert "WITH ... SELECT" in message
    assert ("GET", "/datasets/ds-1/sheets") not in client.trace()


# ---------------------------------------------------------------------------
# The published bounds — "always bounded, never the whole dataset"
# ---------------------------------------------------------------------------


async def test_the_row_limits_are_enforced_by_the_published_schema_not_by_hope(tools):
    """``ToolSet`` calls the closure directly and so bypasses the argument model;
    these ceilings only exist in the JSON schema, and they are the module's
    stated promise. Without them a model can ask for a million rows, and the
    first thing that notices is the caller's context window.

    ``min_length`` on ``sql`` belongs to the same guard: an empty statement is a
    round trip that can only ever come back as a 400."""
    published = {t.name: t.input_schema for t in await tools.server.list_tools()}

    limit = published["query_rows"]["properties"]["limit"]
    assert (limit["minimum"], limit["maximum"]) == (1, 1000)

    max_rows = published["run_sql"]["properties"]["max_rows"]
    assert (max_rows["minimum"], max_rows["maximum"]) == (1, 500)

    assert published["run_sql"]["properties"]["sql"]["minLength"] == 1
    assert published["run_sql"]["required"] == ["dataset_id", "sql"]

"""``tools/artifacts.py`` — the two tools that make chaining possible.

Every compute endpoint (run_sql, aggregate, pivot, sample, validation) writes
its full result to the object store and hands back a *filename*. ``read_artifact``
is the only way to get those rows back, and ``list_artifacts`` is the only way to
find a filename you did not keep. If either one misreports, the model's choices
are to recompute — paying for the same scan twice — or to answer from a result
it cannot see. So the guarantees pinned here are mostly about what the text
*says*: which handle to use, whether more rows exist and at which offset, and
whether "nothing matched" means an empty store, an exhausted scan window, or a
permission problem. Those three read identically to a model unless the tool
distinguishes them, and it does.

Driven through ``mcp_harness.FakeAnalyticsClient``: the tool bodies run for real
(argument handling, path and query-string construction, rendering, and the
``@guard`` error translation) with no Postgres, no MinIO and no artifact rows.

Deliberately **not** covered here:

* That the service ever produces the payloads scripted below. Both shapes are
  built from their route's own source of truth — ``FileEntry`` for ``GET
  /samples``, and ``_sample_data`` below mirroring ``files.services.downloads.
  read_sample_data``'s return dict, which has no response model to import.
  ``tests/test_mcp_endpoint.py`` makes the end-to-end claim against real data.
* The published ``ge``/``le`` bounds on ``limit`` and ``offset``. Calling a tool
  through the harness bypasses the MCP argument model, so those are schema-level
  facts; ``test_mcp_unknown_arguments.py`` is the model for testing there.
* Whether ``/samples`` returns the artifacts a given principal may see. That is
  an authorization question about a database, not about this layer.
"""

from __future__ import annotations

import pytest
from mcp.server.mcpserver.exceptions import ToolError

from app.features.files.schemas import FileEntry
from app.features.mcp.tools import artifacts
from mcp_harness import FakeAnalyticsClient, ToolSet, page, problem, register_tools

SAMPLES = "/samples"


@pytest.fixture
def client() -> FakeAnalyticsClient:
    return FakeAnalyticsClient()


@pytest.fixture
def tools(client: FakeAnalyticsClient) -> ToolSet:
    return register_tools(artifacts, client)


# ---------------------------------------------------------------------------
# Payload builders
# ---------------------------------------------------------------------------


def entry(
    *,
    filename: str = "query_output_1.parquet",
    file_type: str = "query_output",
    size_bytes: int = 2048,
    dataset_id: str | None = "ds-1",
    created_at: str | None = "2026-08-01T10:00:00Z",
    key: str | None = None,
) -> dict:
    """One item of ``Page[FileEntry]`` from ``GET /samples``.

    Constructed from the route's own response model, so a renamed field breaks
    this builder loudly instead of canning a shape the service stopped sending.
    """
    return FileEntry(
        key=key or f"artifacts/team/ds-1/query_output/{filename}",
        filename=filename,
        size_bytes=size_bytes,
        file_type=file_type,
        dataset_id=dataset_id,
        created_at=created_at,
    ).model_dump(mode="json")


def _sample_data(
    rows: list[dict],
    *,
    filename: str = "query_output_1.parquet",
    columns: list[tuple[str, str]] | None = None,
    total_count: int = 1000,
    filtered_count: int | None = None,
    offset: int = 0,
    limit: int = 50,
) -> dict:
    """``GET /samples/{filename}/data``.

    That route has **no** ``response_model`` — it returns
    ``downloads.read_sample_data``'s dict verbatim — so unlike the harness
    builders this shape cannot be derived from a pydantic model and is a
    hand-kept mirror of that function's ``return``. ``columns`` is the file's
    *full* schema even when the caller projected a subset, which is why the
    Schema section can name a column the table does not show.
    """
    if columns is None:
        keys: dict[str, None] = {}
        for row in rows:
            for k in row:
                keys[k] = None
        columns = [(k, "VARCHAR") for k in keys]
    return {
        "filename": filename,
        "total_count": total_count,
        "filtered_count": len(rows) if filtered_count is None else filtered_count,
        "offset": offset,
        "limit": limit,
        "columns": [{"name": n, "dtype": d} for n, d in columns],
        "data": rows,
    }


def data_path(filename: str = "query_output_1.parquet") -> str:
    return f"/samples/{filename}/data"


# ---------------------------------------------------------------------------
# list_artifacts — which page it asks for
# ---------------------------------------------------------------------------


async def test_an_unfiltered_listing_asks_for_exactly_the_page_the_caller_wanted(tools, client):
    """No filter means the service's own paging is the whole answer, so asking
    for more than ``limit`` would be paid-for bytes nobody reads. The regression
    this guards is the cheap fix to the filtered case — always scanning 1000 —
    which would make the common call an order of magnitude more expensive."""
    client.on_get(SAMPLES, page([entry()], total=1))

    await tools["list_artifacts"](limit=5, offset=10)

    assert client.one_call_to("GET", SAMPLES).params == {"limit": 5, "offset": 10}


@pytest.mark.parametrize(
    "kwargs",
    [{"kind": "pivot_output"}, {"dataset_id": "ds-9"}, {"kind": "export", "dataset_id": "ds-9"}],
    ids=["kind", "dataset", "both"],
)
async def test_a_filtered_listing_scans_the_widest_page_the_api_allows(tools, client, kwargs):
    """``GET /samples`` has no filter parameters, so the tool filters in this
    process. Scanning only ``limit`` rows would then hide every older match
    behind whatever the newest N artifacts happen to be, and report "none
    matched" while the artifact sits at position 51. The 1000 is the route's
    own ``le`` bound on ``limit``."""
    client.on_get(SAMPLES, page([], total=0))

    await tools["list_artifacts"](limit=5, **kwargs)

    assert client.one_call_to("GET", SAMPLES).params["limit"] == 1000


async def test_the_listing_never_asks_the_service_to_filter_on_its_behalf(tools, client):
    """The filter terms must not leak into the query string. ``/samples``
    ignores unknown query parameters, so a ``kind=`` sent hopefully would come
    back unfiltered and the caller would be told those were the only artifacts
    of that kind in existence."""
    client.on_get(SAMPLES, page([entry()], total=1))

    await tools["list_artifacts"](dataset_id="ds-1", kind="query_output")

    params = client.one_call_to("GET", SAMPLES).params
    assert set(params) == {"limit", "offset"}


# ---------------------------------------------------------------------------
# list_artifacts — what the table says
# ---------------------------------------------------------------------------


async def test_each_artifact_is_listed_under_the_handle_read_artifact_actually_takes(tools, client):
    """``FileEntry`` carries both ``key`` (the storage path) and ``filename``.
    Only ``filename`` addresses ``read_artifact`` / ``GET /samples/{filename}``,
    so surfacing the key would hand the model a plausible-looking string that
    404s. The other columns are renamed to the vocabulary the tool descriptions
    use — ``file_type`` is "kind" everywhere a model reads about it."""
    client.on_get(
        SAMPLES,
        page([entry(filename="a1b2.parquet", file_type="aggregation_output",
                    size_bytes=4096, dataset_id="ds-7",
                    created_at="2026-08-01T10:00:00Z",
                    key="artifacts/team/ds-7/aggregation_output/a1b2.parquet")]),
    )

    out = await tools["list_artifacts"]()

    assert "filename | kind | bytes | dataset_id | created" in out
    assert "a1b2.parquet | aggregation_output | 4096 | ds-7 | 2026-08-01T10:00:00Z" in out
    assert "artifacts/team/" not in out


async def test_the_listing_warns_that_a_filename_does_not_record_what_produced_it(tools, client):
    """The service stores an artifact's kind and dataset but not the query
    behind it, and the filenames are hashes. Without saying so, a model picks
    the newest ``query_output`` and treats it as *its* query's output — which is
    right until two queries are in flight, and then it silently answers from
    someone else's result set."""
    client.on_get(SAMPLES, page([entry()]))

    out = await tools["list_artifacts"]()

    assert "opaque hashes" in out
    assert "kind and timestamp" in out or "identify one by kind" in out


@pytest.mark.parametrize(
    "kwargs, expected",
    [
        ({"kind": "pivot_output"}, {"p.parquet"}),
        ({"dataset_id": "ds-2"}, {"q2.parquet", "p.parquet"}),
        ({"dataset_id": "ds-2", "kind": "query_output"}, {"q2.parquet"}),
    ],
    ids=["kind-only", "dataset-only", "both-must-match"],
)
async def test_the_filters_narrow_the_table_and_combine_as_and(tools, client, kwargs, expected):
    """Two filters that behaved as OR would return artifacts from a dataset the
    caller did not name — and every artifact filename looks equally plausible,
    so nothing downstream would catch it."""
    client.on_get(
        SAMPLES,
        page([
            entry(filename="q1.parquet", file_type="query_output", dataset_id="ds-1"),
            entry(filename="q2.parquet", file_type="query_output", dataset_id="ds-2"),
            entry(filename="p.parquet", file_type="pivot_output", dataset_id="ds-2"),
        ]),
    )

    out = await tools["list_artifacts"](**kwargs)

    listed = {name for name in ("q1.parquet", "q2.parquet", "p.parquet") if name in out}
    assert listed == expected


async def test_more_matches_than_the_limit_are_announced_rather_than_dropped(tools, client):
    """The tool filters locally, so it can hold more matches than the caller
    asked for and must not just discard the tail. A silent trim reads as "these
    are all of them", and "find the artifact from earlier" is exactly the task
    where the one you want is the one that got cut."""
    client.on_get(
        SAMPLES,
        page([entry(filename=f"q{i}.parquet", file_type="query_output") for i in range(5)]),
    )

    out = await tools["list_artifacts"](kind="query_output", limit=2)

    assert "More matches exist in the scanned window — raise limit (up to 1000)." in out
    assert "2 artifacts shown." in out
    assert "q2.parquet" not in out


async def test_a_full_page_of_matches_is_not_announced_as_having_more(tools, client):
    """The complement: crying "more exist" on an exact fit sends the model
    paging through an empty second page every time."""
    client.on_get(
        SAMPLES,
        page([entry(filename=f"q{i}.parquet", file_type="query_output") for i in range(2)]),
    )

    out = await tools["list_artifacts"](kind="query_output", limit=2)

    assert "More matches exist" not in out


# ---------------------------------------------------------------------------
# list_artifacts — the three different kinds of "nothing"
# ---------------------------------------------------------------------------


async def test_an_empty_store_is_reported_as_empty_not_as_a_filter_miss(tools, client):
    """"No artifacts available" tells the model to go compute something.
    "No artifacts matched that filter" tells it to widen the filter. Collapsing
    the two costs a wasted turn in whichever direction is wrong."""
    client.on_get(SAMPLES, page([], total=0))

    out = await tools["list_artifacts"]()

    assert out == "No artifacts available."


async def test_a_filter_miss_inside_a_partial_scan_does_not_invent_a_hidden_window(tools, client):
    """When the scan came back short of its 1000-row ceiling, it saw every
    artifact there is — so the answer is definitive and telling the caller to
    "retry with offset" would send it paging through nothing."""
    client.on_get(SAMPLES, page([entry(file_type="query_output")], total=1))

    out = await tools["list_artifacts"](kind="validation_failures")

    assert out == "No artifacts matched that filter."


async def test_a_filter_miss_after_a_saturated_scan_says_the_window_ended_and_how_to_move_it(
    tools, client
):
    """A full 1000-row page means the scan hit its ceiling and older artifacts
    may exist beyond it. Reported as a bare "no match", a model concludes the
    artifact was garbage-collected and recomputes a query that already ran."""
    client.on_get(
        SAMPLES,
        page([entry(filename=f"q{i}.parquet", file_type="query_output") for i in range(1000)],
             total=5000, limit=1000),
    )

    out = await tools["list_artifacts"](kind="sample_output")

    assert "No artifacts matched that filter." in out
    assert "Scanned the 1000 most recent artifacts" in out
    assert "retry with offset" in out


async def test_an_identity_failure_is_not_dressed_up_as_an_empty_artifact_store(tools, client):
    """``/samples`` returns only what the principal may see, so a broken
    ``X-User-Id`` and an empty store are indistinguishable from the outside.
    The 401 must survive as a 401, naming the header, or the operator chases a
    storage bug that does not exist."""
    client.on_get(SAMPLES, problem(401, "Unknown user", "unauthorized"))

    with pytest.raises(ToolError) as err:
        await tools["list_artifacts"]()

    assert "Unknown user" in str(err.value)
    assert "X-User-Id" in str(err.value)


# ---------------------------------------------------------------------------
# read_artifact — the request it builds
# ---------------------------------------------------------------------------


async def test_reading_an_artifact_needs_no_dataset_or_version_lookup(tools, client):
    """An artifact is addressed by filename alone — that is what makes
    ``run_sql -> read_artifact`` a two-call chain. Any resolution step added
    here would need a dataset id the caller does not necessarily have, since
    ``result_file`` is handed back without one."""
    client.on_get(data_path(), _sample_data([{"a": 1}]))

    await tools["read_artifact"](filename="query_output_1.parquet")

    assert client.trace() == [("GET", "/samples/query_output_1.parquet/data")]


async def test_the_projection_is_sent_comma_joined_the_way_the_route_splits_it(tools, client):
    """``GET /samples/{filename}/data`` parses ``columns`` with
    ``columns.split(",")``. A JSON array or a repeated parameter would arrive as
    one column name nothing matches, and the route answers that with
    "Columns not found" — an error about a column the caller never named."""
    client.on_get(data_path(), _sample_data([{"region": "US", "amount": 1}]))

    await tools["read_artifact"](filename="query_output_1.parquet", columns=["region", "amount"])

    assert client.one_call_to("GET", data_path()).params["columns"] == "region,amount"


@pytest.mark.parametrize("columns", [None, []], ids=["omitted", "empty-list"])
async def test_no_projection_sends_no_columns_parameter_at_all(tools, client, columns):
    """An empty ``columns=`` is not "all columns" to that route — it is a
    projection of nothing. The whole-artifact read is the default path and must
    stay reachable both by omitting the argument and by clearing it."""
    client.on_get(data_path(), _sample_data([{"a": 1}]))

    await tools["read_artifact"](filename="query_output_1.parquet", columns=columns)

    assert "columns" not in client.one_call_to("GET", data_path()).params


async def test_unset_read_options_are_dropped_rather_than_sent_as_nulls(tools, client):
    """``filter_expr=None`` and ``sort_by=None`` must not reach the wire: a
    literal ``filter_expr=None`` string becomes a WHERE clause the SQL
    sanitizer rejects, and the default read is the most common call there is."""
    client.on_get(data_path(), _sample_data([{"a": 1}]))

    await tools["read_artifact"](filename="query_output_1.parquet")

    assert client.one_call_to("GET", data_path()).params == {
        "sort_order": "asc", "limit": 50, "offset": 0
    }


async def test_every_read_option_reaches_the_service_under_the_routes_own_parameter_name(
    tools, client
):
    """Server-side projection, filtering, sorting and paging are the entire
    point of this tool — they keep intermediate rows out of the context window.
    A parameter renamed on either side is silently ignored by FastAPI, and the
    caller gets the unfiltered, unsorted head of the file labelled as a
    filtered, sorted answer."""
    client.on_get(data_path(), _sample_data([{"region": "US"}]))

    await tools["read_artifact"](
        filename="query_output_1.parquet",
        columns=["region"],
        filter_expr="amount > 100",
        sort_by="amount",
        sort_order="desc",
        limit=25,
        offset=75,
    )

    assert client.one_call_to("GET", data_path()).params == {
        "columns": "region",
        "filter_expr": "amount > 100",
        "sort_by": "amount",
        "sort_order": "desc",
        "limit": 25,
        "offset": 75,
    }


async def test_a_malformed_sort_order_is_forwarded_not_repaired(tools, client):
    """This tool deliberately has no client-side sort_order check — the route
    types it ``Literal["asc","desc"]`` and 422s. Re-adding a mirror here that
    lowercased or defaulted the value would return rows in the *opposite* order
    and call it success, which is the exact defect that made the route a
    Literal in the first place. So ``"DESC"`` must arrive as ``"DESC"``."""
    client.on_get(data_path(), _sample_data([{"a": 1}]))

    await tools["read_artifact"](filename="query_output_1.parquet", sort_order="DESC")

    assert client.one_call_to("GET", data_path()).params["sort_order"] == "DESC"


async def test_the_422_for_a_bad_sort_order_names_the_parameter_and_its_two_legal_values(
    tools, client
):
    """Having chosen to let the service reject it, the tool must render the
    rejection. FastAPI's raw 422 detail is "Request validation failed" and the
    useful part is buried in an ``errors`` array — unrendered, the model is told
    it was wrong but not what about."""
    client.on_get(
        data_path(),
        problem(
            422, "Request validation failed", "unprocessable_entity",
            errors=[{
                "loc": ["query", "sort_order"],
                "msg": "Input should be 'asc' or 'desc'",
                "type": "literal_error",
            }],
        ),
    )

    with pytest.raises(ToolError) as err:
        await tools["read_artifact"](filename="query_output_1.parquet", sort_order="DESC")

    message = str(err.value)
    assert "query.sort_order" in message
    assert "Input should be 'asc' or 'desc'" in message


# ---------------------------------------------------------------------------
# read_artifact — what the rendered result says
# ---------------------------------------------------------------------------


async def test_the_header_reports_both_row_counts_so_a_filter_can_be_judged(tools, client):
    """``total_count`` vs ``filtered_count`` is how a caller learns its
    ``filter_expr`` matched 12 of 1000 rows rather than 12 of 12. Showing only
    the page it got back makes an over-narrow filter indistinguishable from a
    small artifact."""
    client.on_get(
        data_path(),
        _sample_data([{"region": "US"}], total_count=1000, filtered_count=12),
    )

    out = await tools["read_artifact"](filename="query_output_1.parquet")

    assert "artifact: query_output_1.parquet" in out
    assert "rows_total: 1000" in out
    assert "rows_after_filter: 12" in out


async def test_the_schema_section_pairs_each_column_with_its_dtype(tools, client):
    """Chaining means writing the *next* ``filter_expr`` or ``sort_by`` against
    this artifact. Without dtypes the model guesses, and quoting a numeric
    column or comparing a VARCHAR date is a DuckDB error a turn later."""
    client.on_get(
        data_path(),
        _sample_data(
            [{"region": "US", "amount": 12.5}],
            columns=[("region", "VARCHAR"), ("amount", "DOUBLE")],
        ),
    )

    out = await tools["read_artifact"](filename="query_output_1.parquet")

    assert "## Schema" in out
    assert "region (VARCHAR), amount (DOUBLE)" in out


async def test_the_table_uses_the_requested_column_order_not_the_rows_key_order(tools, client):
    """When the caller projects columns, that order is the one it reasons about.
    DuckDB returns row dicts in the file's own column order, so falling back to
    key order silently transposes the header against a caller that asked for
    ``[amount, region]`` and is reading positionally."""
    client.on_get(
        data_path(),
        _sample_data([{"region": "US", "amount": 12}, {"region": "EU", "amount": 7}]),
    )

    out = await tools["read_artifact"](filename="query_output_1.parquet",
                                       columns=["amount", "region"])

    assert "amount | region" in out
    assert "12 | US" in out


@pytest.mark.parametrize(
    "offset, expected", [(0, "offset=5"), (5, "offset=10")], ids=["first-page", "second-page"]
)
async def test_a_partial_result_states_the_exact_next_offset(tools, client, offset, expected):
    """"More rows available" without the number makes the model recompute the
    arithmetic from ``limit`` — which is wrong whenever the service returned
    fewer rows than asked for, and produces a page that skips rows."""
    client.on_get(data_path(), _sample_data([{"a": i} for i in range(5)], filtered_count=12))

    out = await tools["read_artifact"](filename="query_output_1.parquet", limit=5, offset=offset)

    assert f"More rows available — call again with {expected}." in out


async def test_the_final_page_does_not_invite_another_call(tools, client):
    """Offering another page at the end of the result costs a round trip per
    read and teaches the model to distrust the note on the pages that matter."""
    client.on_get(data_path(), _sample_data([{"a": 1}, {"a": 2}], filtered_count=12))

    out = await tools["read_artifact"](filename="query_output_1.parquet", limit=5, offset=10)

    assert "More rows available" not in out
    assert "2 of 12 rows shown." in out


async def test_a_result_without_a_filtered_count_offers_no_next_offset(tools, client):
    """``filtered_count`` is what the "more rows" arithmetic is built on. If a
    future response shape drops it or sends null, the tool must fall silent
    rather than compute an offset from ``None`` — and it must not print an empty
    ``rows_after_filter:`` line either."""
    # _sample_data defaults the count from the rows; blank it explicitly.
    payload = _sample_data([{"a": 1}])
    payload["filtered_count"] = None
    client.on_get(data_path(), payload)

    out = await tools["read_artifact"](filename="query_output_1.parquet")

    assert "More rows available" not in out
    assert "rows_after_filter" not in out
    assert "1 rows shown." in out


async def test_a_filter_that_matches_nothing_renders_an_explicit_empty_table(tools, client):
    """Zero matching rows is a legitimate, informative answer — the filter is
    valid and nothing satisfies it. Rendered as blank space it looks like a
    truncated or failed response, and the model retries the same query."""
    client.on_get(
        data_path(),
        _sample_data([], columns=[("region", "VARCHAR")], total_count=1000, filtered_count=0),
    )

    out = await tools["read_artifact"](filename="query_output_1.parquet",
                                       filter_expr="region = 'ZZ'")

    assert "(no rows)" in out
    assert "0 rows shown." in out
    assert "rows_total: 1000" in out


async def test_an_oversized_read_is_truncated_and_names_the_two_levers_that_shrink_it(
    tools, client
):
    """A single tool result that blows the context window costs the whole
    session, not just the call. Truncation alone is not enough — cut off
    mid-table the model cannot tell a short artifact from a clipped one, so the
    marker has to say it was clipped and how to ask for less."""
    wide = {f"c{i}": "x" * 79 for i in range(5)}
    client.on_get(data_path(), _sample_data([dict(wide) for _ in range(400)]))

    out = await tools["read_artifact"](filename="query_output_1.parquet", limit=400)

    assert "[response truncated at 60,000 characters." in out
    assert "Use `columns` to project fewer fields, or lower `limit`.]" in out
    assert len(out) < 60_200


# ---------------------------------------------------------------------------
# read_artifact — errors, translated
# ---------------------------------------------------------------------------


async def test_an_unreadable_filename_is_not_reported_as_proof_it_never_existed(tools, client):
    """``/samples`` 404s both for a filename that is gone and for one owned by
    another team — the 404-hides-existence rule. Read as "no such file", a model
    recomputes; read as "possibly yours to ask for", it can escalate. The
    service's own detail names the file, so both halves must survive."""
    client.on_get(
        data_path("nope.parquet"),
        problem(404, "File not found: nope.parquet", "not_found"),
    )

    with pytest.raises(ToolError) as err:
        await tools["read_artifact"](filename="nope.parquet")

    message = str(err.value)
    assert "File not found: nope.parquet" in message
    assert "owned by a team you are not in" in message


async def test_projecting_a_column_the_artifact_lacks_names_the_offending_column(tools, client):
    """The single most common ``read_artifact`` mistake is carrying a column
    name over from the source sheet when ``run_sql`` aliased it. The service's
    400 lists exactly which names failed; the tool must pass that through
    verbatim and must not append a ``(code: ...)`` suffix that reads like the
    machine-readable part of the answer when it carries nothing."""
    client.on_get(
        data_path(),
        problem(400, "Columns not found: ['revenu']", "bad_request"),
    )

    with pytest.raises(ToolError) as err:
        await tools["read_artifact"](filename="query_output_1.parquet", columns=["revenu"])

    message = str(err.value)
    assert message == "Columns not found: ['revenu']"


async def test_a_rejected_filter_expression_says_which_rule_it_broke(tools, client):
    """``filter_expr`` is raw SQL and the sanitizer rejects semicolons and DDL
    keywords. "Invalid filter" would leave a model rewriting a filter that was
    semantically fine and failed on punctuation."""
    client.on_get(
        data_path(),
        problem(400, "Filter expression must not contain semicolons", "bad_request"),
    )

    with pytest.raises(ToolError) as err:
        await tools["read_artifact"](filename="query_output_1.parquet",
                                     filter_expr="amount > 1; DROP TABLE t")

    assert "must not contain semicolons" in str(err.value)


async def test_an_unknown_sort_column_reaches_the_caller_as_the_service_named_it(tools, client):
    """Sorting by a column the artifact does not have is a 400 that names the
    column. Swallowed into a generic error, the caller cannot tell it from a
    sort_order problem or a missing file."""
    client.on_get(
        data_path(),
        problem(400, "Sort column not found: totl", "bad_request"),
    )

    with pytest.raises(ToolError) as err:
        await tools["read_artifact"](filename="query_output_1.parquet", sort_by="totl")

    assert "Sort column not found: totl" in str(err.value)

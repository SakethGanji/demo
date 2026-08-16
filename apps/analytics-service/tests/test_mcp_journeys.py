"""MCP journeys — the tool surface driven the way a model actually drives it.

The "UI" here is an LLM client, so every step goes over the real JSON-RPC
endpoint at ``/api/v1/mcp`` (``initialize`` → ``tools/list`` → ``tools/call``)
and every assertion is about the RENDERED TEXT, because that text is the entire
product for a model: an id that is not written into the response cannot be used
in the next call, and an error whose prose does not name the fix costs a whole
round trip to rediscover.

Each journey is one test function, so ordering is explicit and state flows step
to step — step N asserts something that is only true BECAUSE of step N-1. What
is being tested is the ladder the server's own INSTRUCTIONS advertise:

    orient → analyse → chain a handle → curate → publish

``tests/test_mcp_endpoint.py`` covers the protocol, identity laundering and the
undeclared-argument guard. This file covers the flows: it does not re-test that
transport, it uses it.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import re

import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.main import app

from conftest import (
    XLSX_MIME,
    auth,
    create_team_user,
    make_crm_workbook,
    rid,
    upload_file,
    upload_inline,
)

MCP_URL = "/api/v1/mcp"
RPC_HEADERS = {
    "Content-Type": "application/json",
    "Accept": "application/json, text/event-stream",
}


# ---------------------------------------------------------------------------
# Transport
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture(scope="session")
async def mcp_live():
    """Hold the MCP session manager's lifespan open for the session.

    ``StreamableHTTPSessionManager.run()`` may only be called once per
    instance, and ``tests/test_mcp_endpoint.py`` owns an equivalent fixture, so
    the two must not both start it. Two guards make the pair order-independent:
    this fixture no-ops when the manager is already running, and when it is the
    one that starts it, it swaps ``lifespan()`` for a no-op so a sibling
    fixture entering it later is harmless. Neither guard touches app code —
    both are test-local, and the swap is undone when the session ends.
    """
    manager = app.state.mcp.session_manager
    if getattr(manager, "_has_started", False):
        yield app.state.mcp
        return

    started, stop = asyncio.Event(), asyncio.Event()

    async def hold():
        async with app.state.mcp.lifespan():
            started.set()
            await stop.wait()

    task = asyncio.create_task(hold())
    await started.wait()

    @contextlib.asynccontextmanager
    async def already_running():
        yield

    real_lifespan = app.state.mcp.lifespan
    app.state.mcp.lifespan = already_running
    try:
        yield app.state.mcp
    finally:
        app.state.mcp.lifespan = real_lifespan
        stop.set()
        await task


@pytest_asyncio.fixture
async def rpc(mcp_live):
    """Post one JSON-RPC message to the mounted endpoint as a given user."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as http:

        async def call(user_id, method, params=None, *, mid=1):
            body = {"jsonrpc": "2.0", "id": mid, "method": method}
            if params is not None:
                body["params"] = params
            headers = dict(RPC_HEADERS)
            if user_id:
                headers["X-User-Id"] = user_id
            return await http.post(MCP_URL, json=body, headers=headers)

        yield call


async def _call(rpc, user_id, name, arguments):
    response = await rpc(user_id, "tools/call", {"name": name, "arguments": arguments or {}})
    assert response.status_code == 200, response.text
    body = response.json()
    assert "error" not in body, body["error"]
    result = body["result"]
    return result, "".join(b.get("text", "") for b in result.get("content", []))


async def tool(rpc, user_id, name, arguments=None) -> str:
    """Call a tool that must succeed; return the text the model would read."""
    result, text = await _call(rpc, user_id, name, arguments)
    assert not result.get("isError"), f"{name} failed unexpectedly:\n{text}"
    return text


async def tool_error(rpc, user_id, name, arguments=None) -> str:
    """Call a tool that must fail; return the guidance the model would read."""
    result, text = await _call(rpc, user_id, name, arguments)
    assert result.get("isError"), f"{name} unexpectedly succeeded:\n{text}"
    return text


def field(text: str, key: str) -> str:
    """Read a ``key: value`` line back out of a rendered response.

    This is exactly what a model does to carry an id from one call to the next,
    so a field that stops being rendered is a broken journey, not a cosmetic
    change.
    """
    match = re.search(rf"^{re.escape(key)}: (.+)$", text, re.M)
    assert match, f"no '{key}:' line in:\n{text}"
    return match.group(1).strip()


def artifact_handle(text: str) -> str:
    """The filename run_sql/aggregate/pivot hand back for read_artifact."""
    match = re.search(r"artifact '([^']+)'", text) or re.search(r"Artifact: (\S+)", text)
    assert match, f"no artifact handle in:\n{text}"
    return match.group(1)


def table_rows(text: str, header_startswith: str) -> list[list[str]]:
    """Split a pipe-delimited render.table back into cells."""
    lines = [line for line in text.splitlines() if " | " in line]
    start = next(i for i, line in enumerate(lines) if line.startswith(header_startswith))
    return [[c.strip() for c in line.split("|")] for line in lines[start + 1:]]


# ---------------------------------------------------------------------------
# Fixtures shaped like the data a screen is opened on
# ---------------------------------------------------------------------------


async def crm_dataset(client, admin_id, tmp_path, *, team_id=None, prefix="crm"):
    """A two-sheet workbook: Customers 1:N Orders, plus a hidden Scratch sheet."""
    token = f"{prefix}-{rid()}"
    path = tmp_path / f"{token}.xlsx"
    make_crm_workbook(path)
    kwargs = {"team_id": team_id} if team_id else {}
    body = await upload_file(client, admin_id, path, name=f"{token}.xlsx",
                             content_type=XLSX_MIME, **kwargs)
    return body["dataset_id"], token


SALES_ROWS = [
    {"region": "US", "quarter": "Q1", "amount": 10},
    {"region": "US", "quarter": "Q2", "amount": 20},
    {"region": "US", "quarter": "Q3", "amount": 30},
    {"region": "EU", "quarter": "Q1", "amount": 40},
    {"region": "EU", "quarter": "Q2", "amount": 50},
    {"region": "EU", "quarter": "Q3", "amount": 60},
    {"region": "APAC", "quarter": "Q1", "amount": 70},
    {"region": "APAC", "quarter": "Q2", "amount": 80},
    {"region": "APAC", "quarter": "Q3", "amount": 90},
    {"region": "LATAM", "quarter": "Q1", "amount": 100},
    {"region": "LATAM", "quarter": "Q2", "amount": 110},
    {"region": "LATAM", "quarter": "Q3", "amount": 120},
]


# ---------------------------------------------------------------------------
# 1. Orientation
# ---------------------------------------------------------------------------


async def test_a_model_turns_a_topic_into_a_filtered_query_without_guessing_a_name(
    rpc, client, admin_id, tmp_path
):
    """FLOW: the assistant's opening exchange — "what did each customer order?".

    The thesis of ``tools/_common.py::explain`` is that every refusal is
    correctable in ONE step from its own text. A model has no schema in
    context: the only sheet names it can use are the ones the
    sheet-selection-required error listed, and the only column names it can
    filter on are the ones the unknown-column error listed. If either error
    stops naming them, the assistant's first answer becomes a guess.
    """
    ds, token = await crm_dataset(client, admin_id, tmp_path)

    # ---- 1. Topic -> dataset_id. No other tool returns one. ----
    found = await tool(rpc, admin_id, "search_datasets", {"query": token})
    assert ds in found and token in found
    assert "1 datasets shown." in found

    # ---- 2. dataset_id -> the real sheet and column names ----
    described = await tool(rpc, admin_id, "describe_dataset", {"dataset_id": ds})
    assert "sheet_key: orders" in described and "sheet_key: customers" in described
    assert "customer_id (BIGINT)" in described and "tier (VARCHAR)" in described
    # The version table is what a version picker renders.
    assert "version | status | rows" in described and "ready" in described

    # ---- 3. A multi-sheet version refuses to guess, and lists the choices ----
    refused = await tool_error(rpc, admin_id, "query_rows", {"dataset_id": ds})
    assert "Available sheets:" in refused
    offered = refused.split("Available sheets:")[1]
    assert "Customers" in offered and "Orders" in offered

    # ---- 4. The name from the error is the name that works ----
    rows = await tool(rpc, admin_id, "query_rows", {"dataset_id": ds, "sheet": "Orders"})
    assert "sheet: Orders" in rows and "3 rows shown." in rows
    assert "order_id | customer_id | total" in rows

    # ---- 5. A guessed column is refused, and the refusal names the real ones ----
    wrong = await tool_error(rpc, admin_id, "query_rows", {
        "dataset_id": ds, "sheet": "Orders",
        "filters": {"conditions": [{"column": "customer", "op": "eq", "value": 1}]},
    })
    assert "Available columns:" in wrong and "customer_id" in wrong
    assert "Call describe_dataset to see the full schema." in wrong

    # ---- 6. Corrected in one step, and the filter really filtered ----
    filtered = await tool(rpc, admin_id, "query_rows", {
        "dataset_id": ds, "sheet": "Orders", "columns": ["order_id", "total"],
        "filters": {"conditions": [{"column": "customer_id", "op": "eq", "value": 1}]},
    })
    assert "2 rows shown." in filtered
    # Projection and filter both applied: two of the three orders, two of the
    # three columns. Order 11 belongs to customer 2 and must not be here.
    assert [row[:2] for row in table_rows(filtered, "order_id | total")] == \
        [["10", "100"], ["12", "60"]]
    assert "customer_id" not in filtered.split("## ")[0].split("\n\n", 1)[1]

    # ---- 7. The same field, found without knowing which sheet holds it ----
    hits = await tool(rpc, admin_id, "search_columns", {"query": "customer_id"})
    assert ds in hits
    sheets_with_the_column = {row[3] for row in table_rows(hits, "dataset | dataset_id")}
    assert {"customers", "orders"} <= sheets_with_the_column


async def test_a_guessed_table_name_is_corrected_in_one_step_by_the_sql_error(
    rpc, client, admin_id, tmp_path
):
    """FLOW: the SQL console's first failed statement.

    ``look.py::_table_hint`` is the only place a failed tool call makes a
    second request to answer the question it just failed. Models reliably guess
    the dataset (or file) name as the table name; without the hint the error is
    a bare catalog message and the next statement is another guess.
    """
    ds, token = await crm_dataset(client, admin_id, tmp_path)

    guessed = await tool_error(rpc, admin_id, "run_sql", {
        "dataset_id": ds, "sql": f'SELECT * FROM "{token}"'})
    assert "Queryable table names in the dataset's current version:" in guessed
    named = guessed.split("current version:")[1]
    assert "orders" in named and "customers" in named
    assert "Table names are sheet keys, not the dataset or file name." in guessed

    # The names from the error drive a statement that runs.
    joined = await tool(rpc, admin_id, "run_sql", {
        "dataset_id": ds,
        "sql": "SELECT c.tier, SUM(o.total) AS revenue FROM orders o "
               "JOIN customers c ON c.customer_id = o.customer_id GROUP BY c.tier",
    })
    assert "tables: customers, orders" in joined or "tables: orders, customers" in joined
    assert "tier | revenue" in joined
    assert "gold | 160" in joined and "silver | 40" in joined


# ---------------------------------------------------------------------------
# 2. Chaining a handle
# ---------------------------------------------------------------------------


async def test_a_large_sql_result_is_chained_into_a_second_read_by_handle(
    rpc, client, admin_id
):
    """FLOW: "run this query, then show me the top rows" without re-running it.

    The handle-passing contract is the chaining primitive the whole surface is
    built on: compute writes an artifact, the tool surfaces the filename,
    list_artifacts finds it again, and read_artifact pages it with server-side
    projection. A UI that lost the filename, or the ``offset=`` arithmetic in
    the paging hint, would have to recompute the query on every scroll.
    """
    ds = (await upload_inline(client, admin_id, json.dumps(SALES_ROWS)))["dataset_id"]

    described = await tool(rpc, admin_id, "describe_dataset", {"dataset_id": ds})
    assert "sheet_key: data" in described and "amount (BIGINT)" in described

    # ---- 1. Compute in the service; show only a slice ----
    ran = await tool(rpc, admin_id, "run_sql", {
        "dataset_id": ds,
        "sql": "SELECT region, quarter, amount FROM data ORDER BY amount DESC",
        "max_rows": 3,
    })
    assert "row_count: 12" in ran
    assert "Showing 3 of 12 returned rows." in ran
    handle = artifact_handle(ran)

    # ---- 2. The same handle is discoverable later, by kind ----
    listed = await tool(rpc, admin_id, "list_artifacts",
                        {"dataset_id": ds, "kind": "query_output"})
    assert handle in listed and "query_output" in listed
    assert "Filenames are opaque hashes" in listed

    # A kind nothing produced says so, rather than showing the others.
    empty = await tool(rpc, admin_id, "list_artifacts",
                       {"dataset_id": ds, "kind": "pivot_output"})
    assert "No artifacts matched that filter." in empty

    # ---- 3. Read the artifact with a server-side filter and projection ----
    page1 = await tool(rpc, admin_id, "read_artifact", {
        "filename": handle, "columns": ["region", "amount"],
        "filter_expr": "amount > 20", "sort_by": "amount", "sort_order": "asc",
        "limit": 2,
    })
    assert f"artifact: {handle}" in page1
    assert "rows_total: 12" in page1 and "rows_after_filter: 10" in page1
    # The schema block describes the whole artifact; the projection applies to
    # the rows, which is what the caller pays tokens for.
    assert "region (VARCHAR), quarter (VARCHAR), amount (BIGINT)" in page1
    assert "region | amount" in page1
    assert "2 of 10 rows shown." in page1
    assert "US | 30" in page1 and "EU | 40" in page1

    # ---- 4. The response says which offset comes next; use exactly that ----
    assert "More rows available — call again with offset=2." in page1
    page2 = await tool(rpc, admin_id, "read_artifact", {
        "filename": handle, "columns": ["region", "amount"],
        "filter_expr": "amount > 20", "sort_by": "amount", "sort_order": "asc",
        "limit": 2, "offset": 2,
    })
    assert "EU | 50" in page2 and "EU | 60" in page2
    assert "US | 30" not in page2                       # the page really advanced
    assert "More rows available — call again with offset=4." in page2


# ---------------------------------------------------------------------------
# 3. Governance
# ---------------------------------------------------------------------------


async def test_a_dataset_goes_from_unvalidated_to_a_governed_production_tag(
    rpc, client, admin_id
):
    """SCREEN: the quality/governance panel, from "unknown" to a promoted tag.

    Four tools reference each other in their descriptions — health says a
    validation run is missing, promote refuses without one and names the tool
    that makes one, and rollback is documented as never gated. That cross-tool
    contract is the whole product here: if promote stopped naming the version
    it wants validated, or if ``set``/``rollback`` acquired the gate, the only
    way out of a bad promotion would disappear.
    """
    rows = [{"id": 1, "region": "US"}, {"id": 2, "region": None}]
    ds = (await upload_inline(client, admin_id, json.dumps(rows)))["dataset_id"]
    await upload_inline(client, admin_id, json.dumps(
        [{"id": 1, "region": "US"}, {"id": 2, "region": "EU"}]), dataset_id=ds)

    # ---- 1. Health opens as "unknown", and says what would answer it ----
    health = await tool(rpc, admin_id, "get_dataset_health", {"dataset_id": ds})
    assert "current_version: 2" in health
    assert "validation: unknown — No completed validation run" in health
    assert "check_quality computes null rates" in health

    # ---- 2. Declare an expectation the data actually breaks ----
    created = await tool(rpc, admin_id, "manage_quality_rules", {
        "action": "create", "dataset_id": ds, "name": "region is always known",
        "rule_type": "not_null", "sheet_selector": "data",
        "column_selector": "region", "severity": "error",
    })
    rule_id = field(created, "rule_id")
    assert "scope_type: column" in created
    assert "Nothing has been evaluated yet" in created

    # ---- 3. Promotion is now gated, and the refusal names the next call ----
    ungated_first = await tool_error(rpc, admin_id, "manage_tags", {
        "action": "promote", "dataset_id": ds, "tag": "production", "version": 1,
        "reason": "first go-live",
    })
    assert "has not been validated" in ungated_first
    assert "run_quality_check(action='validate', version=1)" in ungated_first

    # ---- 4. Run the rules; the failing rows are saved, not summarised away ----
    validated = await tool(rpc, admin_id, "run_quality_check", {
        "action": "validate", "dataset_id": ds, "version": 1})
    assert "Validated version 1." in validated
    assert "rules_total: 1" in validated and "error_failures: 1" in validated
    assert "region is always known -> " in validated
    assert "manage_tags action='promote' will refuse this version" in validated
    sample = validated.split("region is always known -> ")[1].split("\n")[0].strip()

    offending = await tool(rpc, admin_id, "read_artifact", {"filename": sample})
    assert "1 rows shown." in offending and "id" in offending

    # ---- 5. Promote now fails differently, and carries the run id ----
    blocked = await tool_error(rpc, admin_id, "manage_tags", {
        "action": "promote", "dataset_id": ds, "tag": "production", "version": 1,
        "reason": "first go-live",
    })
    assert "failed validation: 1 error-level failure(s)" in blocked
    assert "validation_run_id" in blocked
    assert "use action='set' as the documented ungated escape hatch" in blocked

    # ---- 6. set is the escape hatch: ungated, and it says so ----
    forced = await tool(rpc, admin_id, "manage_tags", {
        "action": "set", "dataset_id": ds, "tag": "production", "version": 1})
    assert "Tag 'production' now points at version 1." in forced
    assert "This was the ungated path — no validation was checked." in forced
    moved = await tool(rpc, admin_id, "manage_tags", {
        "action": "set", "dataset_id": ds, "tag": "production", "version": 2})
    assert "now points at version 2" in moved

    # ---- 7. Rollback is the emergency path and is NOT gated ----
    rolled = await tool(rpc, admin_id, "manage_tags", {
        "action": "rollback", "dataset_id": ds, "tag": "production",
        "reason": "bad numbers in v2"})
    assert "version 2 -> 1" in rolled
    assert "Rollback is never quality-gated" in rolled

    # ---- 8. Downgrade the rule rather than the data, then re-validate ----
    updated = await tool(rpc, admin_id, "manage_quality_rules", {
        "action": "update", "dataset_id": ds, "rule_id": rule_id,
        "severity": "warning"})
    assert "severity: warning" in updated
    revalidated = await tool(rpc, admin_id, "run_quality_check", {
        "action": "validate", "dataset_id": ds, "version": 1})
    assert "error_failures: 0" in revalidated and "warning_failures: 1" in revalidated

    # ---- 9. The gate opens, and says which gate it was ----
    promoted = await tool(rpc, admin_id, "manage_tags", {
        "action": "promote", "dataset_id": ds, "tag": "production", "version": 1,
        "reason": "warnings accepted"})
    assert "Promoted tag 'production': version 1 -> 1." in promoted
    assert "Promotion passed the quality gate" in promoted

    # ---- 10. The audit trail a UI renders in the tag drawer ----
    history = await tool(rpc, admin_id, "manage_tags", {
        "action": "history", "dataset_id": ds, "tag": "production"})
    actions = [row[1] for row in table_rows(history, "when | action")]
    assert actions[0] == "promote"                      # newest first
    assert actions.count("set") == 2 and "rollback" in actions
    assert "bad numbers in v2" in history and "warnings accepted" in history

    # ---- 11. Health is asked about the CURRENT version, which is still unrun ----
    after = await tool(rpc, admin_id, "get_dataset_health", {"dataset_id": ds})
    assert "validation: unknown — No completed validation run for the current version" \
        in after
    assert "missing_data: unknown" in after and "duplicates: unknown" in after

    # ---- 12. Profiling and validating the current version close those out ----
    profiled = await tool(rpc, admin_id, "run_quality_check", {
        "action": "profile", "dataset_id": ds})
    assert "Profiled version 2" in profiled
    assert "get_dataset_health will stop reporting 'unknown'" in profiled

    current = await tool(rpc, admin_id, "run_quality_check", {
        "action": "validate", "dataset_id": ds})
    assert "Validated version 2." in current and "rules_failed: 0" in current

    final = await tool(rpc, admin_id, "get_dataset_health", {"dataset_id": ds})
    assert "validation: ok — Latest run: 0/1 rules failed (0 error-level)" in final
    assert "missing_data: unknown" not in final and "duplicates: unknown" not in final


# ---------------------------------------------------------------------------
# 4. The write ladder: relationships → join → publish → lineage
# ---------------------------------------------------------------------------


async def test_two_sheets_are_joined_only_after_a_confirmed_key_and_then_published(
    rpc, client, admin_id, tmp_path
):
    """FLOW: the join builder, rung by rung.

    The asymmetry is the safety property: ``preview`` works on a suggested edge
    (that is how you review it) and ``execute`` refuses one. The pre-flight
    numbers — expansion factor, unmatched percentages, collisions — are the
    only warning a model gets before a join silently multiplies rows and makes
    every later SUM wrong. Publishing then has to record BOTH parents, which is
    the only way a UI can render a joined dataset's provenance.
    """
    left, left_token = await crm_dataset(client, admin_id, tmp_path, prefix="orders-side")
    right, _ = await crm_dataset(client, admin_id, tmp_path, prefix="customer-side")

    # ---- 1. Nothing is declared yet: the join builder has no key to offer ----
    none_yet = await tool(rpc, admin_id, "list_relationships", {"dataset_id": left})
    assert "No relationships are recorded for this dataset." in none_yet
    assert "only confirmed edges can drive the join builder" in none_yet

    # ---- 2. A foreign-key rule someone already wrote becomes an edge ----
    await tool(rpc, admin_id, "manage_quality_rules", {
        "action": "create", "dataset_id": left, "name": "orders reference customers",
        "rule_type": "foreign_key", "sheet_selector": "orders",
        "column_selector": "customer_id",
        "parameters": {"ref_sheet": "customers", "ref_column": "customer_id"}})
    seeded = await tool(rpc, admin_id, "manage_relationships",
                        {"action": "seed", "dataset_id": left})
    assert "created_or_refreshed: 1" in seeded
    assert "orders.customer_id | customers.customer_id" in seeded
    assert "Seeded edges start as suggestions — confirm one before joining on it." \
        in seeded

    # ---- 3. Discovery proposes, it never applies ----
    suggested = await tool(rpc, admin_id, "manage_relationships",
                           {"action": "suggest", "dataset_id": left})
    assert "Nothing here can drive a join yet." in suggested
    within = next(row for row in table_rows(suggested, "relationship_id | from")
                  if row[1] == "orders.customer_id")
    assert within[2] == "customers.customer_id" and within[4] == "suggested"
    within_id = within[0]

    # ---- 4. A cross-dataset edge a human states by hand, left unconfirmed ----
    declared = await tool(rpc, admin_id, "manage_relationships", {
        "action": "declare", "dataset_id": left, "from_sheet": "Orders",
        "from_column": "customer_id", "to_dataset_id": right,
        "to_sheet": "Customers", "to_column": "customer_id", "confirmed": False,
    })
    assert "Status is not yet 'confirmed', so a join will refuse it" in declared
    cross = next(row for row in table_rows(declared, "relationship_id | from")
                 if row[3] == "true")
    cross_id = cross[0]

    # ---- 5. Execute refuses a merely-suggested edge ----
    refused = await tool_error(rpc, admin_id, "join_datasets",
                               {"action": "execute", "relationship_id": cross_id})
    assert "has not been confirmed" in refused

    # ---- 6. ...but preview measures it, which is how you review it ----
    preview = await tool(rpc, admin_id, "join_datasets",
                         {"action": "preview", "relationship_id": cross_id, "how": "inner"})
    assert "left_rows: 3" in preview and "right_rows: 3" in preview
    assert "many_to_many: false" in preview and "row_expansion_factor: 1" in preview
    assert "unmatched_right_pct: 33.33" in preview
    assert "33.33% of right rows have no match" in preview
    assert "order_id, customer_id, total, tier" in preview
    assert "Nothing was written — this was a measurement only." in preview

    # ---- 7. Confirm, then the same call executes ----
    confirmed = await tool(rpc, admin_id, "manage_relationships", {
        "action": "confirm", "dataset_id": left, "relationship_id": cross_id})
    assert "This edge can now drive a join" in confirmed

    executed = await tool(rpc, admin_id, "join_datasets",
                          {"action": "execute", "relationship_id": cross_id})
    assert "Join executed. Created:" in executed
    run_id = field(executed, "run_id")
    sample_file = field(executed, "sample_file")
    assert "row_count: 3" in executed

    # ---- 8. The result is readable by handle, not by re-joining ----
    joined_rows = await tool(rpc, admin_id, "read_artifact",
                             {"filename": sample_file, "columns": ["order_id", "tier"]})
    assert "rows_total: 3" in joined_rows and "order_id | tier" in joined_rows
    assert "gold" in joined_rows

    # ---- 9. Promote the result to a real dataset ----
    published = await tool(rpc, admin_id, "publish_result", {
        "source": "join", "run_id": run_id, "mode": "new_dataset",
        "name": f"joined-{rid()}"})
    assert "mode: new_dataset" in published
    assert "Both parents of the join are recorded in lineage" in published
    published_id = field(published, "dataset_id")

    # ---- 10. Provenance: both sides, by id, from the published dataset ----
    lineage = await tool(rpc, admin_id, "get_lineage", {"dataset_id": published_id})
    parents = table_rows(lineage, "parent_dataset | parent_dataset_id")
    assert {row[1] for row in parents} == {left, right}
    assert {row[4] for row in parents} == {"joined_from"}

    graph = await tool(rpc, admin_id, "get_lineage",
                       {"dataset_id": published_id, "full_graph": True})
    assert "Edges (child was derived from parent)" in graph
    assert left_token in graph                          # nodes carry names, not just ids

    # ---- 11. The edge nobody wants is turned down, not deleted ----
    rejected = await tool(rpc, admin_id, "manage_relationships", {
        "action": "reject", "dataset_id": left, "relationship_id": within_id})
    assert "Discovery will not propose this pairing again." in rejected
    still_there = await tool(rpc, admin_id, "list_relationships",
                             {"dataset_id": left, "status": "rejected",
                              "relationship_id": within_id})
    assert "status: rejected" in still_there
    assert "cannot drive a join — only confirmed edges can" in still_there


async def test_a_pipeline_is_compiled_at_save_time_dry_run_executed_and_published(
    rpc, client, admin_id
):
    """FLOW: the data-prep editor — write steps, dry run, run, inspect, publish.

    Fail-at-create-not-at-run is the reason this grammar is worth exposing at
    all: a saved pipeline that only explodes on the first real run is a broken
    editor. ``preview`` persisting nothing is the other half — it is what makes
    the dry run free — and ``list_saved_objects`` rendering the steps back is
    the only way a UI can show a user what a stored pipeline does.
    """
    rows = [
        {"id": 1, "city": "  ny ", "qty": 2, "price": 10.0},
        {"id": 2, "city": "NY", "qty": 3, "price": 10.0},
        {"id": 2, "city": "NY", "qty": 3, "price": 10.0},
        {"id": 3, "city": "la", "qty": 1, "price": 5.0},
    ]
    ds = (await upload_inline(client, admin_id, json.dumps(rows)))["dataset_id"]

    # ---- 1. A bad column is refused AT CREATE, with the real names ----
    rejected = await tool_error(rpc, admin_id, "transform_data", {
        "action": "create", "dataset_id": ds, "name": "typo", "sheet": "data",
        "steps": [{"type": "select", "columns": ["quantity"]}],
    })
    assert "Available columns:" in rejected and "qty" in rejected

    nothing_saved = await tool(rpc, admin_id, "list_saved_objects",
                               {"dataset_id": ds, "kind": "transformation"})
    assert "No saved transformations on this dataset." in nothing_saved

    # ---- 2. A pipeline that compiles ----
    steps = [
        {"type": "trim", "columns": ["city"]},
        {"type": "case_normalize", "columns": ["city"], "mode": "upper"},
        {"type": "deduplicate", "subset": ["id"], "keep": "first"},
        {"type": "compute", "into": "line_total", "expression": {
            "op": "arith", "fn": "mul",
            "left": {"op": "col", "name": "qty"},
            "right": {"op": "col", "name": "price"}}},
    ]
    created = await tool(rpc, admin_id, "transform_data", {
        "action": "create", "dataset_id": ds, "name": f"cleanup-{rid()}",
        "sheet": "data", "description": "trim, upper, dedupe, line total",
        "steps": steps,
    })
    definition_id = field(created, "definition_id")
    assert "sheet_key: data" in created and "steps: 4" in created
    assert "step_types: trim, case_normalize, deduplicate, compute" in created
    assert "It compiled cleanly against the sheet schema." in created

    # ---- 3. The dry run: output schema and rows, nothing persisted ----
    previewed = await tool(rpc, admin_id, "transform_data", {
        "action": "preview", "dataset_id": ds, "definition_id": definition_id})
    assert "line_total | DOUBLE" in previewed or "line_total | BIGINT" in previewed
    assert "NY" in previewed and "  ny " not in previewed
    assert "Dry run: nothing was written, no run was recorded, no artifact exists." \
        in previewed.replace("\n", " ")
    assert "approximate" in previewed

    # A dry run leaves no run to inspect and no artifact to find.
    assert "No artifacts matched that filter." in await tool(
        rpc, admin_id, "list_artifacts", {"dataset_id": ds, "kind": "transform_output"})

    # ---- 4. The saved pipeline reads back, step by step ----
    listed = await tool(rpc, admin_id, "list_saved_objects",
                        {"dataset_id": ds, "kind": "transformation"})
    assert definition_id in listed and "1 transformations shown." in listed
    detail = await tool(rpc, admin_id, "list_saved_objects", {
        "dataset_id": ds, "kind": "transformation", "object_id": definition_id})
    assert "## Steps" in detail
    assert "type: deduplicate" in detail and "fn: mul" in detail
    assert "into: line_total" in detail

    # ---- 5. The real run, over the whole sheet ----
    ran = await tool(rpc, admin_id, "transform_data", {
        "action": "run", "dataset_id": ds, "definition_id": definition_id})
    run_id = field(ran, "run_id")
    assert "status: completed" in ran
    assert "source_rows: 4" in ran and "output_rows: 3" in ran
    assert "line_total" in field(ran, "output_columns")
    run_sample = field(ran, "sample_file")

    now_there = await tool(rpc, admin_id, "list_artifacts",
                           {"dataset_id": ds, "kind": "transform_output"})
    assert run_sample in now_there

    # ---- 6. Inspect: profile plus drift against the source sheet ----
    inspected = await tool(rpc, admin_id, "transform_data", {
        "action": "inspect", "dataset_id": ds, "run_id": run_id})
    assert f"definition_id: {definition_id}" in inspected
    assert "source_rows: 4" in inspected and "output_rows: 3" in inspected
    assert "row_delta: -1" in inspected
    assert "columns_added: line_total" in inspected
    assert "This action only reads. Nothing about the run changed." in inspected

    # ---- 7. Publish as a new version; the old one is untouched ----
    published = await tool(rpc, admin_id, "publish_result", {
        "source": "transformation", "dataset_id": ds, "run_id": run_id,
        "mode": "new_version"})
    assert f"dataset_id: {ds}" in published and "version_number: 2" in published
    assert "the earlier versions of this dataset are unchanged" in published

    described = await tool(rpc, admin_id, "describe_dataset", {"dataset_id": ds})
    versions = table_rows(described, "version | status | rows")
    assert [row[0] for row in versions] == ["2", "1"]
    assert [row[2] for row in versions] == ["3", "4"]    # v1 still has its 4 rows
    assert "line_total" in described                      # the current version gained it


# ---------------------------------------------------------------------------
# 5. Documentation and masking
# ---------------------------------------------------------------------------


async def test_a_documentation_screen_writes_every_level_and_reads_it_all_back(
    rpc, client, admin_id
):
    """SCREEN: the "document this dataset" form, and the dictionary it feeds.

    ``write_documentation`` is the only write path for documentation and
    ``get_data_dictionary`` is its only read path, so this pair closing is the
    whole feature. ``target='column'`` is the branch most likely to break:
    ``_merge_write`` falls back to PUT on a 404, and that 404 could be about
    the dataset, the sheet, or the column.
    """
    rows = [{"customer_id": 1, "email": "a@bank.com", "country": "US"},
            {"customer_id": 2, "email": "b@bank.com", "country": "GB"}]
    ds = (await upload_inline(client, admin_id, json.dumps(rows)))["dataset_id"]

    # ---- 1. Nothing documented yet ----
    empty = await tool(rpc, admin_id, "get_data_dictionary", {"dataset_id": ds})
    assert "No sheet-level metadata (grain, primary key, description) has been recorded" \
        in empty
    assert "Pass sheet_key to see the column-level dictionary" in empty

    # ---- 2. Dataset level ----
    await tool(rpc, admin_id, "write_documentation", {
        "target": "dataset", "dataset_id": ds, "description": "One row per customer",
        "domain": "crm", "classification": "internal", "source_system": "Salesforce",
        "refresh_frequency": "daily"})

    # ---- 3. Sheet level: grain and primary key ----
    sheet_doc = await tool(rpc, admin_id, "write_documentation", {
        "target": "sheet", "dataset_id": ds, "sheet_key": "data",
        "grain": "one row per customer", "primary_key_columns": ["customer_id"]})
    assert "grain: one row per customer" in sheet_doc
    assert "primary_key_columns: customer_id" in sheet_doc

    # ---- 4. Column level: the branch with the PUT-on-404 fallback ----
    column_doc = await tool(rpc, admin_id, "write_documentation", {
        "target": "column", "dataset_id": ds, "sheet_key": "data",
        "column_name": "country", "business_name": "Country of residence",
        "semantic_type": "country_code", "unit": "ISO-3166",
        "allowed_values": ["US", "GB", "DE"]})
    assert "Updated dictionary entry for data.country." in column_doc
    assert "allowed_values: US, GB, DE" in column_doc

    # ---- 5. One read shows all three levels ----
    dictionary = await tool(rpc, admin_id, "get_data_dictionary",
                            {"dataset_id": ds, "sheet_key": "data"})
    assert "one row per customer" in dictionary and "customer_id" in dictionary
    assert "## Columns — data" in dictionary
    country = next(row for row in table_rows(dictionary, "column | business_name")
                   if row[0] == "country")
    assert country[1] == "Country of residence" and country[3] == "ISO-3166"
    assert "US, GB, DE" in dictionary
    assert "No column here is tagged sensitive, so nothing will be masked." in dictionary

    # ---- 6. Clearing one field is not the same as omitting it ----
    cleared = await tool(rpc, admin_id, "write_documentation", {
        "target": "column", "dataset_id": ds, "sheet_key": "data",
        "column_name": "country", "clear": ["unit"]})
    stored = cleared.split("## Now stored")[-1]
    assert "unit:" not in stored
    assert "business_name: Country of residence" in stored   # merge, not replace
    assert "semantic_type: country_code" in stored

    reread = await tool(rpc, admin_id, "get_data_dictionary",
                        {"dataset_id": ds, "sheet_key": "data"})
    country = next(row for row in table_rows(reread, "column | business_name")
                   if row[0] == "country")
    assert country[3] == "" and country[1] == "Country of residence"

    # ---- 7. The health screen's documentation dimension moved ----
    health = await tool(rpc, admin_id, "get_dataset_health", {"dataset_id": ds})
    assert "documentation: attention" not in health
    assert "has_description" not in health                  # prose, not evidence dumps
    assert re.search(r"^documentation: (ok|warning) — ", health, re.M)


async def test_a_restricted_editor_sees_masked_columns_and_is_refused_raw_sql(
    rpc, client, admin_id
):
    """SCREEN: the same data preview, opened by someone without PII access.

    ``look.py`` promises to say which columns were withheld, and
    ``get_data_dictionary`` promises to name which tools mask. Both are
    statements about REST behaviour made by the tool layer, so if the masking
    rules move they become lies with nothing failing. Every other MCP test runs
    as the seeded superuser, for whom nothing is ever masked.
    """
    editor, team_id = await create_team_user(client, admin_id, "editor")
    rows = [{"customer_id": 1, "email": "alice@bank.com", "balance": 10},
            {"customer_id": 2, "email": "bob@bank.com", "balance": 20}]
    ds = (await upload_inline(client, editor, json.dumps(rows),
                              team_id=team_id))["dataset_id"]

    # ---- 1. An admin tags the column as PII ----
    tagged = await tool(rpc, admin_id, "write_documentation", {
        "target": "column", "dataset_id": ds, "sheet_key": "data",
        "column_name": "email", "sensitivity": "pii", "semantic_type": "email"})
    assert "sensitivity: pii" in tagged
    assert "Sensitivity here is a label, not an access control" in tagged

    # ---- 2. The dictionary warns the restricted reader BEFORE they query ----
    dictionary = await tool(rpc, editor, "get_data_dictionary",
                            {"dataset_id": ds, "sheet_key": "data"})
    assert "Tagged sensitive: email." in dictionary
    assert "masked on preview_rows and query_rows for callers lacking permission" \
        in dictionary

    # ---- 3. query_rows masks, and names what it withheld ----
    masked = await tool(rpc, editor, "query_rows", {"dataset_id": ds})
    assert "The service withheld these columns from you: email." in masked
    assert "alice@bank.com" not in masked and "a***@***.com" in masked
    assert "10" in masked and "20" in masked            # non-sensitive columns intact

    # ---- 4. The superuser sees the same rows unmasked, with no note ----
    raw = await tool(rpc, admin_id, "query_rows", {"dataset_id": ds})
    assert "alice@bank.com" in raw
    assert "withheld these columns" not in raw

    # ---- 5. Arbitrary SQL cannot be masked per column, so it is refused ----
    denied = await tool_error(rpc, editor, "run_sql", {
        "dataset_id": ds, "sql": "SELECT email FROM data"})
    assert "declares sensitive columns" in denied
    assert "You are a member of the owning team but lack the required permission" in denied
    assert "alice@bank.com" not in denied

    # The superuser's SQL is unaffected.
    allowed = await tool(rpc, admin_id, "run_sql",
                         {"dataset_id": ds, "sql": "SELECT email FROM data"})
    assert "alice@bank.com" in allowed


# ---------------------------------------------------------------------------
# 6. Reuse and RBAC
# ---------------------------------------------------------------------------


async def test_an_editor_finds_what_others_already_built_before_recomputing(
    rpc, client, admin_id
):
    """SCREEN: the dataset's "saved work" tab, opened before any new analysis.

    This is the orient-before-you-compute step, and it is where the tools
    compensate for the REST surface: /timeline has no event_type filter, so
    ``get_activity`` filters in process and says so. Both compensations are
    invisible to a reader of the REST spec.
    """
    ds = (await upload_inline(client, admin_id, json.dumps(SALES_ROWS)))["dataset_id"]
    await upload_inline(client, admin_id, json.dumps(SALES_ROWS), dataset_id=ds)

    # A colleague saved a view through the UI, and a rule through the assistant.
    view = await client.post(f"/api/v1/datasets/{ds}/views", headers=auth(admin_id), json={
        "name": "big deals", "sheet": "data",
        "query": {"filters": {"conditions": [
            {"column": "amount", "op": "gt", "value": 50}]},
            "sort": [{"column": "amount", "direction": "desc"}], "limit": 5}})
    assert view.status_code == 201, view.text
    view_id = view.json()["id"]

    rule = await tool(rpc, admin_id, "manage_quality_rules", {
        "action": "create", "dataset_id": ds, "name": "amount is positive",
        "rule_type": "range", "sheet_selector": "data", "column_selector": "amount",
        "parameters": {"min": 0}, "severity": "warning"})
    rule_id = field(rule, "rule_id")

    # ---- 1. What exists, by kind ----
    counts = await tool(rpc, admin_id, "list_saved_objects", {"dataset_id": ds})
    by_kind = {row[0]: row[1] for row in table_rows(counts, "kind | count")}
    assert by_kind == {"view": "1", "analytics": "0", "chart": "0", "rule": "1",
                       "transformation": "0"}

    # ---- 2. The list for one kind (the plural is accepted too) ----
    views = await tool(rpc, admin_id, "list_saved_objects",
                       {"dataset_id": ds, "kind": "views"})
    assert view_id in views and "big deals" in views
    assert "1 views shown." in views

    # ---- 3. The saved query renders in full, so a UI can show what it does ----
    detail = await tool(rpc, admin_id, "list_saved_objects", {
        "dataset_id": ds, "kind": "view", "object_id": view_id})
    assert "## Query" in detail
    assert "column: amount" in detail and "op: gt" in detail and "value: 50" in detail
    assert "sheet: data" in detail and "version: current" in detail

    # ---- 4. A rule is fetched by the same id-shaped call ----
    rule_detail = await tool(rpc, admin_id, "list_saved_objects", {
        "dataset_id": ds, "kind": "rule", "object_id": rule_id})
    assert "rule_type: range" in rule_detail and "severity: warning" in rule_detail
    assert "## Parameters" in rule_detail and "min: 0" in rule_detail

    # ---- 5. History, filtered to the events that mean something ----
    activity = await tool(rpc, admin_id, "get_activity", {
        "dataset_id": ds, "event_types": ["version_created"]})
    events = {row[1] for row in table_rows(activity, "when | event")}
    assert events == {"version_created"}                 # audit rows filtered out
    assert "/timeline has no event_type filter" in activity or len(events) == 1
    assert "## Usage" in activity and "writes:" in activity

    unfiltered = await tool(rpc, admin_id, "get_activity", {"dataset_id": ds})
    assert "audit" in {row[1] for row in table_rows(unfiltered, "when | event")}

    quiet = await tool(rpc, admin_id, "get_activity", {
        "dataset_id": ds, "event_types": ["tag_promote"]})
    assert "No tag_promote events in the whole timeline." in quiet

    # ---- 6. An uploaded dataset has no provenance, and says why ----
    lineage = await tool(rpc, admin_id, "get_lineage", {"dataset_id": ds})
    assert "No lineage recorded for this dataset" in lineage
    assert "it was uploaded directly" in lineage
    graph = await tool(rpc, admin_id, "get_lineage",
                       {"dataset_id": ds, "full_graph": True, "depth": 3})
    assert "isolated in the lineage graph within 3 hops" in graph


async def test_the_write_ladder_is_hidden_across_teams_and_refused_below_role(
    rpc, client, admin_id
):
    """FLOW: two tenants and a read-only seat, on the tools that change state.

    403-vs-404 is the whole cross-team contract, and ``explain`` has separate
    prose for each: a 404 must not confirm the resource exists, and a 403 must
    tell an in-team caller that the problem is their role, not the dataset. A
    UI branches on exactly that difference — "no such dataset" versus "ask an
    admin".
    """
    alice, alice_team = await create_team_user(client, admin_id, "editor")
    bob, _ = await create_team_user(client, admin_id, "editor")
    viewer, _ = await create_team_user(client, admin_id, "viewer", team_id=alice_team)

    rows = [{"id": 1, "amount": 10}, {"id": 2, "amount": 20}]
    ds = (await upload_inline(client, alice, json.dumps(rows),
                              team_id=alice_team))["dataset_id"]

    # ---- 1. The owner writes ----
    created = await tool(rpc, alice, "manage_quality_rules", {
        "action": "create", "dataset_id": ds, "name": "id not null",
        "rule_type": "not_null", "sheet_selector": "data", "column_selector": "id"})
    assert "Created rule 'id not null'" in created

    # ---- 2. Another team is answered "not found", not "forbidden" ----
    hidden = await tool_error(rpc, bob, "manage_quality_rules", {
        "action": "create", "dataset_id": ds, "name": "sneaky",
        "rule_type": "not_null", "sheet_selector": "data", "column_selector": "id"})
    assert "not found" in hidden.lower()
    assert "returns 404 both for things that do not exist and for things owned by a " \
        "team you are not in" in hidden
    assert "permission" not in hidden.lower()            # never confirms existence
    assert "not found" in (await tool_error(
        rpc, bob, "describe_dataset", {"dataset_id": ds})).lower()

    # ---- 3. In-team but under-privileged is told it is about the role ----
    denied = await tool_error(rpc, viewer, "manage_tags", {
        "action": "set", "dataset_id": ds, "tag": "production", "version": 1})
    assert "You are a member of the owning team but lack the required permission" in denied

    definition = await tool(rpc, alice, "transform_data", {
        "action": "create", "dataset_id": ds, "name": f"trim-{rid()}", "sheet": "data",
        "steps": [{"type": "limit", "count": 1}]})
    definition_id = field(definition, "definition_id")

    denied_run = await tool_error(rpc, viewer, "transform_data", {
        "action": "run", "dataset_id": ds, "definition_id": definition_id})
    assert "You are a member of the owning team but lack the required permission" \
        in denied_run

    # ---- 4. The viewer can still READ, so the 403 was about the write ----
    readable = await tool(rpc, viewer, "query_rows", {"dataset_id": ds})
    assert "2 rows shown." in readable
    saved = await tool(rpc, viewer, "list_saved_objects",
                       {"dataset_id": ds, "kind": "transformation"})
    assert definition_id in saved

    # ---- 5. Nothing the refused calls attempted actually landed ----
    rules = await tool(rpc, alice, "list_saved_objects", {"dataset_id": ds, "kind": "rule"})
    assert "1 rules shown." in rules and "sneaky" not in rules
    assert "No tag_set events" in await tool(rpc, alice, "get_activity", {
        "dataset_id": ds, "event_types": ["tag_set"], "include_jobs": False})


# ---------------------------------------------------------------------------
# 7. The analysis screen
# ---------------------------------------------------------------------------


async def test_an_analysis_screen_profiles_aggregates_pivots_and_diffs_two_versions(
    rpc, client, admin_id
):
    """SCREEN: the dataset dashboard — who am I, what is here, what changed.

    Everything a chart or a stat tile renders comes from these five tools, and
    each one hands back an artifact handle the UI needs to page the full result
    rather than re-running the computation on every scroll.
    """
    # ---- 1. The session header ----
    me = await tool(rpc, admin_id, "whoami", {})
    assert f"user_id: {admin_id}" in me and "superuser: true" in me
    assert "sensitive columns are returned to you unmasked" in me

    ds = (await upload_inline(client, admin_id, json.dumps(SALES_ROWS)))["dataset_id"]

    # ---- 2. Find the field without knowing which dataset holds it ----
    hits = await tool(rpc, admin_id, "search_columns", {"query": "amount"})
    assert ds in hits and "1 columns shown." in hits

    # ---- 3. One column's distribution ----
    profile = await tool(rpc, admin_id, "profile_column",
                         {"dataset_id": ds, "column": "amount"})
    assert "rows: 12" in profile and "non_null: 12" in profile and "nulls: 0" in profile
    assert "min: 10" in profile and "max: 120" in profile and "mean: 65" in profile
    assert "candidate_key: true" in profile

    # ---- 4. Grouped totals, sorted the way the table renders them ----
    agg = await tool(rpc, admin_id, "aggregate", {
        "dataset_id": ds, "group_by": ["region"],
        "aggregations": [{"column": "amount", "function": "sum", "alias": "total"}],
        "sort_by": "total", "sort_order": "desc"})
    assert "rows_scanned: 12" in agg and "groups: 4" in agg
    ranked = [row[0] for row in table_rows(agg, "region | total")]
    assert ranked == ["LATAM", "APAC", "EU", "US"]
    assert "Totals (over all groups, not just the rows shown)" in agg
    assert "total: 780" in agg                          # 10+20+…+120
    agg_handle = artifact_handle(agg)

    # The grouped result is itself a handle, so a UI pages it rather than re-grouping.
    regrouped = await tool(rpc, admin_id, "read_artifact",
                           {"filename": agg_handle, "sort_by": "total", "sort_order": "desc"})
    assert "rows_total: 4" in regrouped and "LATAM | 330" in regrouped

    # ---- 5. The cross-tab a pivot table renders ----
    pivoted = await tool(rpc, admin_id, "pivot", {
        "dataset_id": ds, "rows": ["region"], "columns": "quarter",
        "values": [{"column": "amount", "function": "sum", "alias": "amt"}],
        "include_column_totals": True})
    assert "pivot_columns: Q1, Q2, Q3" in pivoted
    assert "rows: 4" in pivoted
    assert "Column totals (per output column, over all rows)" in pivoted

    # ---- 6. Live quality evidence, with no stored profile run ----
    quality = await tool(rpc, admin_id, "check_quality", {"dataset_id": ds})
    assert "sheet: data" in quality and "rows: 12" in quality
    assert "Missing values (worst 3 of 3 columns)" in quality
    assert "duplicate_groups: 0" in quality
    assert "No validation run recorded for this version." in quality

    # ---- 7. A refresh lands; what changed? ----
    v2_rows = [dict(row, note="checked") for row in SALES_ROWS]
    v2_rows.append({"region": "US", "quarter": "Q4", "amount": 5, "note": "late"})
    await upload_inline(client, admin_id, json.dumps(v2_rows), dataset_id=ds)

    diff = await tool(rpc, admin_id, "compare_versions", {
        "dataset_id": ds, "from_version": 1, "to_version": 2})
    assert "from: 1" in diff and "to: 2" in diff
    assert "schema_changed | row_delta" in diff and "true | 1" in diff
    assert "Pass `sheet` to see column-level changes for one sheet." in diff

    sheet_diff = await tool(rpc, admin_id, "compare_versions", {
        "dataset_id": ds, "from_version": 1, "to_version": 2, "sheet": "data"})
    assert "identical: false" in sheet_diff
    assert "rows_before: 12" in sheet_diff and "rows_after: 13" in sheet_diff
    assert "## Added columns" in sheet_diff and "- note" in sheet_diff

    # ---- 8. Every computed result is still reachable by handle ----
    artifacts = await tool(rpc, admin_id, "list_artifacts", {"dataset_id": ds})
    assert agg_handle in artifacts
    kinds = {row[1] for row in table_rows(artifacts, "filename | kind")}
    assert {"aggregation_output", "pivot_output"} <= kinds


# ---------------------------------------------------------------------------
# 8. The protocol handshake a client performs before any of the above
# ---------------------------------------------------------------------------


async def test_a_client_that_just_connected_can_reach_every_tool_it_was_told_about(
    rpc, client, admin_id
):
    """FLOW: what a freshly configured MCP client does on connect.

    A client initializes, reads the tool list, and then calls tools by the
    names in that list. The instructions and the schemas are the only
    documentation a model gets, so a tool whose declared name or required
    arguments drift from what the server accepts is unreachable — the client
    never learns about it any other way.
    """
    handshake = await rpc(admin_id, "initialize", {
        "protocolVersion": "2025-06-18", "capabilities": {},
        "clientInfo": {"name": "journey", "version": "1"}})
    instructions = handshake.json()["result"]["instructions"]
    assert "search_datasets" in instructions and "read_artifact" in instructions

    listing = await rpc(admin_id, "tools/list", {}, mid=2)
    tools = {t["name"]: t for t in listing.json()["result"]["tools"]}
    assert len(tools) == 27

    # Every tool named in the instructions is actually registered under that name.
    for name in re.findall(r"^  (\w+)", instructions, re.M):
        assert name in tools, f"instructions advertise an unregistered tool: {name}"

    # The first rung of the ladder needs no arguments at all, and the schema says so.
    assert tools["whoami"]["inputSchema"].get("required", []) == []
    assert "dataset_id" in tools["describe_dataset"]["inputSchema"]["required"]

    ds = (await upload_inline(client, admin_id, json.dumps(SALES_ROWS)))["dataset_id"]
    first = await tool(rpc, admin_id, "search_datasets", {})
    assert ds in first


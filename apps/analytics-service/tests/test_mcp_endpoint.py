"""The mounted MCP endpoint: protocol, identity, and RBAC.

The regression these tests exist for: the tools used to live in a standalone
process that baked one ``X-User-Id`` into one HTTP client at startup. One
process per user made that correct. Mounted in a multi-tenant service it would
mean whichever user booted the process serves every caller. So the tests that
matter here drive the endpoint as two different users *in the same process* and
assert each sees only their own data — sequentially and concurrently.
"""

from __future__ import annotations

import asyncio
import json

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.api.errors import ProblemException
from app.features.mcp import identity as mcp_identity
from app.main import app

from conftest import auth, create_team_user, upload_inline

MCP_URL = "/api/v1/mcp"
RPC_HEADERS = {
    "Content-Type": "application/json",
    "Accept": "application/json, text/event-stream",
}


@pytest_asyncio.fixture(scope="session")
async def mcp_running():
    """Enter the MCP session manager's lifespan for the whole test session.

    The manager's ``run()`` may only be called once per instance — the same
    constraint a real process has, where the app lifespan enters it once. Held
    open by a dedicated task because it owns an anyio task group, which must be
    entered and exited by the same task; a fixture body is not guaranteed to be
    finalized in the task that ran it.
    """
    started, stop = asyncio.Event(), asyncio.Event()

    async def hold():
        async with app.state.mcp.lifespan():
            started.set()
            await stop.wait()

    task = asyncio.create_task(hold())
    await started.wait()
    yield app.state.mcp
    stop.set()
    await task


@pytest_asyncio.fixture
async def rpc(mcp_running):
    """Post one JSON-RPC message to the mounted endpoint as a given user."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as http:

        async def call(user_id, method, params=None, *, mid=1, headers=None):
            body = {"jsonrpc": "2.0", "id": mid, "method": method}
            if params is not None:
                body["params"] = params
            hdrs = dict(RPC_HEADERS)
            if user_id:
                hdrs["X-User-Id"] = user_id
            hdrs.update(headers or {})
            return await http.post(MCP_URL, json=body, headers=hdrs)

        yield call


def tool_text(response) -> str:
    """Flatten a tools/call result into the text a model would see."""
    body = response.json()
    assert "error" not in body, body["error"]
    return "".join(b.get("text", "") for b in body["result"].get("content", []))


async def call_tool(rpc, user_id, name, arguments=None):
    return tool_text(await rpc(user_id, "tools/call", {"name": name, "arguments": arguments or {}}))


# ---------------------------------------------------------------------------
# Protocol surface
# ---------------------------------------------------------------------------


async def test_initialize_and_list_tools(rpc, admin_id):
    r = await rpc(admin_id, "initialize", {
        "protocolVersion": "2025-06-18", "capabilities": {},
        "clientInfo": {"name": "pytest", "version": "1"},
    })
    assert r.status_code == 200, r.text
    result = r.json()["result"]
    assert "dataset platform" in result["instructions"].lower()

    r = await rpc(admin_id, "tools/list", {}, mid=2)
    tools = r.json()["result"]["tools"]
    names = {t["name"] for t in tools}
    assert len(tools) == 27, sorted(names)
    # The ladder's first rung and the workhorse, by name — the tool surface is
    # a contract with every configured client, not an implementation detail.
    assert {"search_datasets", "describe_dataset", "run_sql", "whoami"} <= names
    assert all(t.get("inputSchema", {}).get("type") == "object" for t in tools)


async def test_mcp_is_not_in_the_rest_openapi_schema():
    spec = app.openapi()  # must still build
    assert not any("mcp" in path for path in spec["paths"]), "MCP leaked into the REST spec"
    assert len(spec["paths"]) > 100


# ---------------------------------------------------------------------------
# Identity is the service's own, resolved per request
# ---------------------------------------------------------------------------


async def test_unauthenticated_request_is_rejected(rpc):
    r = await rpc(None, "tools/list", {})
    assert r.status_code == 401
    assert r.headers["content-type"].startswith("application/problem+json")
    assert r.json()["code"] == "unauthorized"


async def test_unknown_user_is_rejected(rpc):
    r = await rpc("00000000-0000-0000-0000-0000000000ff", "tools/list", {})
    assert r.status_code == 401
    assert "Unknown or inactive user" in r.json()["detail"]


async def test_identity_comes_from_get_principal_not_a_trusted_header(rpc, admin_id):
    """A well-formed but unknown id is refused; only real users get through."""
    fabricated = "11111111-2222-3333-4444-555555555555"
    assert (await rpc(fabricated, "tools/list", {})).status_code == 401
    assert (await rpc(admin_id, "tools/list", {})).status_code == 200


async def test_an_auth_problem_exception_is_answered_as_problem_json(rpc, monkeypatch):
    """A ``ProblemException`` out of ``get_principal`` reaches the client intact.

    Two independent things have to hold for this, and it is worth knowing which
    is doing the work. The endpoint's own guard used to catch FastAPI's
    ``HTTPException``, which is a *sibling* of ``ProblemException`` under
    Starlette's — so it did not match. It now catches Starlette's base.

    Even before that, this test passed: the mount is a plain ``Route`` on
    ``app.router``, so it sits inside the app's ``ExceptionMiddleware``, and
    ``app.api.errors`` registers its handler on Starlette's class, which
    handler lookup finds by walking the exception's MRO. That safety net is
    real, and it is why the fix changed nothing observable from outside. It is
    also why the bug survived: the endpoint had no failing test.
    ``tests/unit/test_mcp_identity_errors.py`` drives the middleware with no
    app around it, which is where the difference is visible.
    """
    async def refuse(request, x_user_id=None):
        raise ProblemException(
            401, "Token expired", code="token-expired",
            expired_at="2026-08-06T00:00:00Z")

    monkeypatch.setattr(mcp_identity, "get_principal", refuse)

    r = await rpc("00000000-0000-0000-0000-000000000001", "tools/list", {})
    assert r.status_code == 401
    assert r.headers["content-type"].startswith("application/problem+json")
    body = r.json()
    assert body["code"] == "token-expired"          # not flattened to a bare detail
    assert body["expired_at"] == "2026-08-06T00:00:00Z"
    assert body["detail"] == "Token expired"
    assert body["instance"] == MCP_URL


async def test_tool_without_a_bound_caller_raises(mcp_running):
    with pytest.raises(mcp_identity.NoIdentityError):
        mcp_identity.current_identity()


async def test_principal_is_stashed_for_the_audit_middleware(rpc, client, admin_id):
    """``get_principal`` stashes onto ``request.state``; the audit trail reads it."""
    await call_tool(rpc, admin_id, "whoami")
    r = await client.get("/api/v1/audit", headers=auth(admin_id), params={"limit": 50})
    assert r.status_code == 200, r.text
    entries = [e for e in r.json()["items"] if e.get("path") == MCP_URL]
    assert entries, "the MCP call was not audited"
    assert entries[0]["actor_user_id"] == admin_id


# ---------------------------------------------------------------------------
# The RBAC regression: two users, one process
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def two_tenants(client, admin_id):
    """Two users in two teams, each owning one dataset the other cannot see."""
    rows = json.dumps([{"id": 1, "label": "x"}, {"id": 2, "label": "y"}])
    made = []
    for _ in range(2):
        user_id, team_id = await create_team_user(client, admin_id, "editor")
        body = await upload_inline(client, user_id, rows, team_id=team_id)
        made.append((user_id, team_id, body["dataset_id"]))
    return made


async def test_each_user_sees_only_their_own_datasets(rpc, two_tenants):
    (alice, _, alice_ds), (bob, _, bob_ds) = two_tenants

    alice_view = await call_tool(rpc, alice, "search_datasets", {"limit": 50})
    bob_view = await call_tool(rpc, bob, "search_datasets", {"limit": 50})

    assert alice_ds in alice_view and bob_ds not in alice_view
    assert bob_ds in bob_view and alice_ds not in bob_view


async def test_cross_tenant_read_is_hidden_not_served(rpc, two_tenants):
    (alice, _, alice_ds), (bob, _, bob_ds) = two_tenants
    assert "Dataset not found" in await call_tool(
        rpc, alice, "describe_dataset", {"dataset_id": bob_ds})
    assert "Dataset not found" in await call_tool(
        rpc, bob, "describe_dataset", {"dataset_id": alice_ds})


async def test_identity_does_not_leak_between_sequential_callers(rpc, two_tenants):
    """Interleaved calls on one endpoint each report their own caller."""
    (alice, *_), (bob, *_) = two_tenants
    for expected in (alice, bob, alice, bob):
        text = await call_tool(rpc, expected, "whoami")
        assert f"user_id: {expected}" in text


async def test_identity_does_not_leak_between_concurrent_callers(rpc, two_tenants):
    """The laundering regression, under concurrency.

    Twelve overlapping calls alternating between two users. A ContextVar bound
    once per connection (or per session) rather than per message would show up
    here as some callers being answered as the other one.
    """
    (alice, *_), (bob, *_) = two_tenants
    order = [alice, bob] * 6
    texts = await asyncio.gather(*(call_tool(rpc, u, "whoami") for u in order))
    for expected, text in zip(order, texts):
        assert f"user_id: {expected}" in text


async def test_permission_denied_is_a_403_not_a_silent_success(rpc, client, admin_id):
    """A viewer cannot write documentation, and is told why."""
    viewer, team_id = await create_team_user(client, admin_id, "viewer")
    body = await upload_inline(
        client, admin_id, json.dumps([{"id": 1}]), team_id=team_id)
    text = await call_tool(rpc, viewer, "write_documentation", {
        "target": "dataset", "dataset_id": body["dataset_id"], "domain": "sales",
    })
    assert "permission" in text.lower()


# ---------------------------------------------------------------------------
# Behaviour the fold-in changed
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def numbers_dataset(client, admin_id):
    rows = json.dumps([
        {"grp": "a", "amount": 10}, {"grp": "a", "amount": 30},
        {"grp": "b", "amount": 5}, {"grp": "b", "amount": 25},
        {"grp": "c", "amount": 100},
    ])
    body = await upload_inline(client, admin_id, rows)
    return body["dataset_id"]


async def test_aggregate_totals_span_all_groups_and_name_what_was_omitted(
    rpc, admin_id, numbers_dataset
):
    text = await call_tool(rpc, admin_id, "aggregate", {
        "dataset_id": numbers_dataset,
        "group_by": ["grp"],
        "aggregations": [
            {"column": "amount", "function": "sum", "alias": "total"},
            {"column": "amount", "function": "max", "alias": "worst"},
        ],
        "sort_by": "total", "limit": 1,
    })
    # One group shown, but the total covers all three (10+30+5+25+100).
    assert "total: 170" in text
    assert "over all groups, not just the rows shown" in text
    assert "summed across the groups shown" not in text
    # The service decides what is not additive and says why; the tool relays it.
    assert "worst (non-additive)" in text


async def test_profile_column_uses_the_services_non_null_count(rpc, admin_id, client):
    body = await upload_inline(client, admin_id, json.dumps(
        [{"v": 1}, {"v": None}, {"v": 3}, {"v": None}]))
    text = await call_tool(rpc, admin_id, "profile_column", {
        "dataset_id": body["dataset_id"], "column": "v"})
    assert "rows: 4" in text        # `count` is the sheet's row count
    assert "non_null: 2" in text    # `non_null_count` is COUNT(column)
    assert "nulls: 2" in text


async def test_bad_filter_operator_is_rejected_by_the_service_with_the_vocabulary(
    rpc, admin_id, numbers_dataset
):
    """Nothing client-side mirrors the filter grammar any more."""
    text = await call_tool(rpc, admin_id, "query_rows", {
        "dataset_id": numbers_dataset,
        "filters": {"conditions": [{"column": "amount", "op": "greater_than", "value": 1}]},
    })
    assert "Unknown filter operator" in text
    assert "greater_than" in text
    assert "Valid filter operators" in text and "gte" in text


async def test_bad_sort_order_surfaces_as_an_error_rather_than_being_corrected(
    rpc, admin_id, numbers_dataset
):
    aggregate = await call_tool(rpc, admin_id, "aggregate", {
        "dataset_id": numbers_dataset, "group_by": ["grp"],
        "aggregations": [{"column": "amount", "function": "sum", "alias": "total"}],
        "sort_order": "ASC",
    })
    # 422 from the request model, rendered field-by-field rather than as the
    # bare "Request validation failed".
    assert "sort_order" in aggregate and "'asc' or 'desc'" in aggregate

    # read_artifact no longer mirrors the check: GET /samples/{f}/data types
    # sort_order as Literal["asc","desc"] too, so the service rejects it and the
    # tool renders that 422 field-by-field. (The 422 lands before the artifact
    # is resolved, hence the made-up filename.)
    artifact = await call_tool(rpc, admin_id, "read_artifact", {
        "filename": "nope.parquet", "sort_order": "DESC"})
    assert "sort_order" in artifact and "'asc' or 'desc'" in artifact


async def test_write_documentation_merges_and_can_clear(rpc, admin_id, numbers_dataset):
    args = {"target": "sheet", "dataset_id": numbers_dataset, "sheet_key": "data"}
    # First write creates the record.
    await call_tool(rpc, admin_id, "write_documentation",
                    {**args, "grain": "one row per payment", "description": "payments"})
    # Second write touches one field and must not erase the other.
    text = await call_tool(rpc, admin_id, "write_documentation",
                           {**args, "description": "payments, restated"})
    assert "grain: one row per payment" in text
    assert "description: payments, restated" in text
    # Clearing is newly possible: PUT could only ever blank a field by omitting
    # it, which is indistinguishable from leaving it alone.
    text = await call_tool(rpc, admin_id, "write_documentation", {**args, "clear": ["grain"]})
    assert "grain" not in text.split("## Now stored")[-1]


async def test_write_documentation_rejects_a_field_that_is_both_written_and_cleared(
    rpc, admin_id, numbers_dataset
):
    text = await call_tool(rpc, admin_id, "write_documentation", {
        "target": "sheet", "dataset_id": numbers_dataset, "sheet_key": "data",
        "grain": "one row per payment", "clear": ["grain"],
    })
    assert "pick one" in text


async def test_write_documentation_clears_dataset_fields_too(
    rpc, admin_id, numbers_dataset
):
    """`clear` is ONE mechanism, used by all three targets.

    The dataset branch stripped nulls out of the PATCH body before sending it,
    so once a description or a domain was written it could never be blanked
    through the tool — `clear` was refused outright and writing None was
    indistinguishable from omitting the parameter. PATCH /datasets/{id} honours
    an explicit null, so the tool just has to stop swallowing it.
    """
    args = {"target": "dataset", "dataset_id": numbers_dataset}
    text = await call_tool(rpc, admin_id, "write_documentation", {
        **args, "description": "payment lines", "domain": "finance",
        "source_system": "Stripe", "refresh_frequency": "daily"})
    assert "domain: finance" in text

    # Clearing one field leaves the rest alone — it is a merge, not a replace.
    text = await call_tool(rpc, admin_id, "write_documentation",
                           {**args, "clear": ["domain"]})
    stored = text.split("## Now stored")[-1]
    assert "domain: finance" not in stored
    assert "source_system: Stripe" in stored
    assert "description: payment lines" in stored

    # Every nullable dataset field is reachable, including in one call.
    text = await call_tool(rpc, admin_id, "write_documentation", {
        **args, "clear": ["description", "source_system", "refresh_frequency"]})
    stored = text.split("## Now stored")[-1]
    for gone in ("payment lines", "Stripe", "daily"):
        assert gone not in stored

    # Clear and write in the same call.
    text = await call_tool(rpc, admin_id, "write_documentation", {
        **args, "domain": "revenue", "clear": ["deprecation_reason"]})
    assert "domain: revenue" in text.split("## Now stored")[-1]


async def test_write_documentation_refuses_to_clear_a_required_dataset_field(
    rpc, admin_id, numbers_dataset
):
    """The four NOT NULL columns are refused client-side, by name.

    The REST layer already answers an explicit null on them with a 422, but
    that 422 talks about a null in a request body — it never mentions `clear`,
    which is the parameter the caller actually got wrong. Which fields are
    required is a fixed schema fact the tool knows, so it says so directly
    rather than spending a round trip to be told less.
    """
    args = {"target": "dataset", "dataset_id": numbers_dataset}
    for field in ("name", "classification", "deprecated"):
        text = await call_tool(rpc, admin_id, "write_documentation",
                               {**args, "clear": [field]})
        assert "cannot be cleared" in text and field in text
        # ...and it names what CAN be cleared, so the next call is right.
        assert "description" in text and "deprecation_reason" in text

    # A field that is not a dataset field at all keeps its own message.
    text = await call_tool(rpc, admin_id, "write_documentation",
                           {**args, "clear": ["grain"]})
    assert "not a dataset field" in text

    # Nothing was written by any of the above.
    text = await call_tool(rpc, admin_id, "write_documentation",
                           {**args, "domain": "finance"})
    assert "domain: finance" in text


async def test_write_documentation_needs_something_to_do(rpc, admin_id, numbers_dataset):
    text = await call_tool(rpc, admin_id, "write_documentation",
                           {"target": "dataset", "dataset_id": numbers_dataset})
    assert "at least one dataset field" in text


async def test_unknown_tool_name_is_an_error_not_a_crash(rpc, admin_id):
    r = await rpc(admin_id, "tools/call", {"name": "delete_everything", "arguments": {}})
    assert r.status_code == 200
    body = r.json()
    assert "error" in body or body["result"].get("isError")


# ---------------------------------------------------------------------------
# Undeclared arguments are rejected, not dropped
# ---------------------------------------------------------------------------
#
# The SDK builds each tool's argument model with pydantic's default
# `extra="ignore"`, so a misspelled parameter never reached the tool and the
# call reported success — the caller got a confident answer to a question they
# did not ask, with nothing in the response to signal it. Guarded now by
# `app.features.mcp.strict_args`; the vocabulary comparison itself is unit
# tested, these drive the real 27 tools through the real transport.


async def test_a_misspelled_argument_fails_the_call_instead_of_being_dropped(
    rpc, admin_id, numbers_dataset
):
    r = await rpc(admin_id, "tools/call", {"name": "query_rows", "arguments": {
        "dataset_id": numbers_dataset, "limitt": 1}})
    result = r.json()["result"]

    assert result["isError"] is True
    text = "".join(b.get("text", "") for b in result["content"])
    assert "limitt" in text and "did you mean 'limit'" in text
    assert "Valid arguments:" in text and "limit" in text
    # The tool did not run: no data came back at all. Before the guard this
    # same call answered with every row, as though `limitt` had never been sent.
    assert "grp" not in text and "Nothing ran." in text


async def test_a_correct_call_with_optional_arguments_omitted_is_unaffected(
    rpc, admin_id, numbers_dataset
):
    """The guard must cost a well-formed call nothing.

    `query_rows` declares nine parameters and one is required; leaving eight of
    them out is the normal case, not an undeclared-argument case.
    """
    minimal = await call_tool(rpc, admin_id, "query_rows", {"dataset_id": numbers_dataset})
    assert "grp" in minimal and "5 rows shown" in minimal

    full = await call_tool(rpc, admin_id, "query_rows", {
        "dataset_id": numbers_dataset, "limit": 2, "columns": ["grp"], "sheet": "data"})
    assert "2 of 5 rows shown" in full and "amount" not in full

    # A tool that declares no parameters at all still answers when sent none.
    assert f"user_id: {admin_id}" in await call_tool(rpc, admin_id, "whoami")


async def test_a_missing_required_argument_still_gets_the_sdks_own_error(rpc, admin_id):
    """The guard checks one thing — whether the key is declared. Required-ness
    stays with the SDK's arg model, which already reports it by field name."""
    r = await rpc(admin_id, "tools/call",
                  {"name": "describe_dataset", "arguments": {}})
    result = r.json()["result"]
    assert result["isError"] is True
    text = "".join(b.get("text", "") for b in result["content"])
    assert "dataset_id" in text and "Unknown argument" not in text


async def test_rejection_happens_with_the_caller_still_bound(rpc, two_tenants):
    """Ordering check: the guard is appended AFTER `identity_middleware`, so it
    runs inside it. If that inverted, identity would no longer be bound for the
    handler and every tool would raise `NoIdentityError` instead of answering.
    """
    (alice, *_), (bob, *_) = two_tenants
    for user in (alice, bob):
        rejected = await rpc(user, "tools/call", {"name": "whoami", "arguments": {"nope": 1}})
        assert rejected.json()["result"]["isError"] is True
        # ...and the very next call from the same user is served as themselves.
        assert f"user_id: {user}" in await call_tool(rpc, user, "whoami")

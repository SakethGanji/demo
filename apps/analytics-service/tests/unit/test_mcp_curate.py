"""``curate.py`` — the four tools that CHANGE state, driven against a fake service.

``write_documentation``, ``manage_quality_rules``, ``run_quality_check`` and
``manage_tags`` are the only MCP tools that write. Two properties make them
worth pinning at unit level, and both are invisible to a "did it 200?" test:

1. **What goes on the wire.** A write tool that sends the wrong body, drops a
   field, or hits the wrong path corrupts stored state and then renders a
   confident confirmation of it. Every write test here asserts on the recorded
   request, not just on the returned text. The sharpest case is ``clear``:
   these tools distinguish *omitted* (keep what is stored) from *explicitly
   null* (erase it), which only survives if the tool uses ``merge_patch`` and
   not ``patch``. ``patch`` strips nulls, so the regression is silent — the
   tool reports "cleared" and nothing was cleared.
2. **Refusals that name the way forward.** These tools reject a lot before
   ever calling the service — an unclearable field, a rule type whose
   parameters are missing, a promote blocked by the quality gate. A refusal
   that says only "invalid" costs a model a round trip per guess. Every error
   test asserts the actionable content: the field names, the rule vocabulary,
   the parameter shape, the tool call to make next.

Deliberately NOT covered here:

* That the service produces these response shapes. This file cans them (from
  the service's own pydantic models, so a rename breaks loudly), which proves
  the tool handles a shape, never that the shape occurs.
  ``tests/test_mcp_endpoint.py`` makes that claim against real Postgres.
* Pydantic argument constraints (``version`` ``ge=1``, ``limit`` ``le=200``).
  Calling through ``ToolSet`` bypasses the MCP argument model by design; see
  ``tests/unit/test_mcp_unknown_arguments.py`` for schema-level testing.
* Authorization. ``dataset:write`` is enforced by the route, and the tool sees
  only the 403 — the one thing tested here is that it is re-raised into
  ``guard`` rather than swallowed by a local ``except``.
"""

from __future__ import annotations

from typing import Any, Sequence

import pytest
from mcp.server.mcpserver.exceptions import ToolError

from app.features.data_accelerator.schemas import (
    DatasetPatched,
    TagHistoryEntry,
    TagInfo,
    TagOpResponse,
)
from app.features.explorer.schemas import InsightOut, ProfileRunOut
from app.features.mcp.tools import curate
from app.features.quality.schemas import RuleOut, RuleResultOut, ValidationDetail
from mcp_harness import (
    FakeAnalyticsClient,
    column_metadata,
    page,
    problem,
    register_tools,
    sheet_metadata,
    version,
)


@pytest.fixture
def client() -> FakeAnalyticsClient:
    return FakeAnalyticsClient()


@pytest.fixture
def tools(client):
    return register_tools(curate, client)


# ---------------------------------------------------------------------------
# Response builders for the routes mcp_harness did not audit.
#
# Same rule as the harness: build the service's OWN response model and dump it,
# so a field renamed in the schema breaks these loudly instead of leaving a
# stale canned shape that keeps every test below passing.
# ---------------------------------------------------------------------------


def _dump(model: Any) -> dict[str, Any]:
    return model.model_dump(mode="json")


def patched_dataset(**over: Any) -> dict[str, Any]:
    """``PATCH /datasets/{id}`` -> ``DatasetPatched``."""
    return _dump(
        DatasetPatched(
            **{
                "id": "ds-1", "name": "Orders", "created_at": "2026-01-01T00:00:00Z",
                "updated_at": "2026-08-07T09:00:00Z", **over,
            }
        )
    )


def rule(**over: Any) -> dict[str, Any]:
    """``POST /datasets/{id}/rules`` and its PATCH -> ``RuleOut``."""
    return _dump(
        RuleOut(
            **{
                "id": "r-1", "dataset_id": "ds-1", "name": "orders have a customer",
                "scope_type": "column", "rule_type": "not_null", "severity": "error",
                "enabled": True, "created_at": "2026-01-01T00:00:00Z",
                "updated_at": "2026-08-07T09:00:00Z", **over,
            }
        )
    )


def rule_result(**over: Any) -> dict[str, Any]:
    """One entry of ``ValidationDetail.results`` -> ``RuleResultOut``."""
    return _dump(
        RuleResultOut(
            **{
                "rule_name": "orders have a customer", "rule_type": "not_null",
                "scope_type": "column", "severity": "error", "status": "passed", **over,
            }
        )
    )


def validation_run(results: Sequence[dict[str, Any]] = (), **over: Any) -> dict[str, Any]:
    """``POST .../validate`` -> ``ValidationDetail`` (run header + per-rule results)."""
    return _dump(
        ValidationDetail(
            **{
                "id": "vr-1", "dataset_id": "ds-1", "dataset_version_id": "v-4",
                "status": "completed", "started_at": "2026-08-07T09:00:00Z",
                "completed_at": "2026-08-07T09:00:05Z",
                "results": [RuleResultOut(**r) for r in results], **over,
            }
        )
    )


def insight(**over: Any) -> dict[str, Any]:
    """One entry of ``ProfileRunOut.insights`` -> ``InsightOut``."""
    return _dump(
        InsightOut(
            **{"rule": "high_null_rate", "severity": "warning",
               "message": "customer_id is 42% null", **over}
        )
    )


def profile_run(insights: Sequence[dict[str, Any]] = (), **over: Any) -> dict[str, Any]:
    """One entry of ``POST .../profile-runs`` -> ``ProfileRunOut``.

    Note the route's ``response_model`` is a bare ``list[ProfileRunOut]``, not
    the ``Page`` envelope every other list endpoint uses.
    """
    return _dump(
        ProfileRunOut(
            **{
                "id": "pr-1", "dataset_id": "ds-1", "dataset_version_id": "v-4",
                "logical_sheet_id": "ls-1", "sheet_name": "orders", "status": "completed",
                "algorithm_version": 3, "started_at": "2026-08-07T09:00:00Z",
                "insights": [InsightOut(**i) for i in insights], **over,
            }
        )
    )


def tag_info(**over: Any) -> dict[str, Any]:
    """``PUT /datasets/{id}/tags`` -> ``TagInfo``."""
    return _dump(
        TagInfo(
            **{
                "tag_name": "production", "version_id": "v-4", "version_number": 4,
                "created_at": "2026-01-01T00:00:00Z", "updated_at": "2026-08-07T09:00:00Z",
                **over,
            }
        )
    )


def tag_op(**over: Any) -> dict[str, Any]:
    """``POST .../promote`` and ``.../rollback`` -> ``TagOpResponse``."""
    return _dump(
        TagOpResponse(
            **{"tag_name": "production", "action": "promote", "from_version_number": 3,
               "to_version_number": 4, **over}
        )
    )


def tag_history_entry(**over: Any) -> dict[str, Any]:
    """One item of ``Page[TagHistoryEntry]`` from ``GET .../tags/{tag}/history``."""
    return _dump(
        TagHistoryEntry(
            **{"id": 1, "tag_name": "production", "action": "promote",
               "from_version_number": 3, "to_version_number": 4,
               "created_at": "2026-08-07T09:00:00Z", **over}
        )
    )


# ===========================================================================
# write_documentation — target='dataset'
# ===========================================================================


async def test_documenting_a_dataset_sends_only_the_fields_the_caller_supplied(tools, client):
    """A merge write. If the tool ever sent the unset parameters too, one call
    documenting the domain would blank the description, the source system and
    everything else the dataset already had — a data-loss bug whose only symptom
    is a successful-looking write."""
    client.on_patch("/datasets/ds-1", patched_dataset(domain="sales", classification="internal"))

    out = await tools["write_documentation"](
        target="dataset", dataset_id="ds-1", domain="sales"
    )

    assert client.one_call_to("PATCH", "/datasets/ds-1").body == {"domain": "sales"}
    assert "Updated dataset ds-1." in out
    assert "domain: sales" in out


async def test_clearing_a_dataset_field_sends_an_explicit_null_not_an_absent_key(tools, client):
    """The whole point of `clear`. PATCH tells an absent key (keep the stored
    value) from a null one (erase it), and `client.patch` strips nulls — so a
    tool built on `patch` instead of `merge_patch` reports 'description:
    (cleared)' while the description stays exactly where it was. Assert the null
    survives to the wire, because the rendered confirmation cannot detect this."""
    client.on_patch("/datasets/ds-1", patched_dataset(description=None))

    out = await tools["write_documentation"](
        target="dataset", dataset_id="ds-1", clear=["description"]
    )

    body = client.one_call_to("PATCH", "/datasets/ds-1").body
    assert "description" in body and body["description"] is None
    assert "description: (cleared)" in out


async def test_writing_and_clearing_the_same_field_is_refused_before_the_write(tools, client):
    """Ambiguous intent. Either resolution silently discards half the request,
    and the caller learns which half only by reading it back."""
    with pytest.raises(ToolError) as excinfo:
        await tools["write_documentation"](
            target="dataset", dataset_id="ds-1", description="new", clear=["description"]
        )

    assert "description is both written and listed in clear — pick one." in str(excinfo.value)
    assert client.calls == [], "the ambiguous request must not reach the service"


async def test_clearing_a_not_null_dataset_field_names_the_ones_that_can_be_cleared(tools, client):
    """`name`, `classification` and `deprecated` back NOT NULL columns, so the
    service answers an explicit null with a 422 that talks about a request body
    the caller never wrote. Diagnosed here instead, in the caller's own
    vocabulary, and with the whole clearable set so the retry is one step."""
    with pytest.raises(ToolError) as excinfo:
        await tools["write_documentation"](
            target="dataset", dataset_id="ds-1", clear=["name"]
        )

    message = str(excinfo.value)
    assert "name cannot be cleared — it is a required dataset field." in message
    assert "Write a new value for it instead." in message
    assert ("Clearable dataset fields: description, domain, source_system, "
            "refresh_frequency, deprecation_reason." in message)
    assert client.calls == []


async def test_clear_matches_field_names_case_insensitively(tools, client):
    """'NAME' is the same field as 'name'. Without normalization it would fall
    through to the 'not a dataset field' branch and tell the caller their field
    does not exist, which is a wrong diagnosis of a right guess."""
    with pytest.raises(ToolError) as excinfo:
        await tools["write_documentation"](
            target="dataset", dataset_id="ds-1", clear=["  NAME  "]
        )

    assert "name cannot be cleared" in str(excinfo.value)


async def test_clearing_an_unrecognised_field_echoes_it_back_and_lists_the_real_ones(tools, client):
    """A typo in `clear` is otherwise a no-op that returns success. Echoing the
    caller's own spelling (not a normalized form) is what makes the typo visible."""
    with pytest.raises(ToolError) as excinfo:
        await tools["write_documentation"](
            target="dataset", dataset_id="ds-1", clear=["Descrption"]
        )

    message = str(excinfo.value)
    assert "clear names 'Descrption', which is not a dataset field." in message
    assert "Clearable dataset fields: description, domain," in message


async def test_a_write_with_no_fields_at_all_lists_the_writable_dataset_fields(tools, client):
    """An empty PATCH is a no-op the service happily 200s, so the tool would
    report a successful write that wrote nothing. Refusing it is only useful if
    the refusal carries the vocabulary the caller was missing."""
    with pytest.raises(ToolError) as excinfo:
        await tools["write_documentation"](target="dataset", dataset_id="ds-1")

    message = str(excinfo.value)
    assert "Provide at least one dataset field to write, or name one in clear." in message
    assert ("Dataset fields: name, description, classification, domain, source_system, "
            "refresh_frequency, deprecated, deprecation_reason." in message)
    assert client.calls == []


async def test_classification_is_lowercased_before_it_reaches_the_service(tools, client):
    """``Classification`` is a lowercase ``Literal`` on the request model, so
    'Confidential' is a 422 that reads like a validation dump. Normalizing here
    turns a plausible spelling into a successful write."""
    client.on_patch("/datasets/ds-1", patched_dataset(classification="confidential"))

    await tools["write_documentation"](
        target="dataset", dataset_id="ds-1", classification="Confidential"
    )

    assert client.one_call_to("PATCH", "/datasets/ds-1").body == {
        "classification": "confidential"
    }


async def test_an_unsupported_classification_names_the_four_that_exist(tools, client):
    """Free text here would be stored and then silently mean nothing to every
    consumer. The refusal has to carry the closed set, or the caller guesses."""
    with pytest.raises(ToolError) as excinfo:
        await tools["write_documentation"](
            target="dataset", dataset_id="ds-1", classification="secret"
        )

    assert ("classification must be one of public, internal, confidential, restricted, "
            "got 'secret'." in str(excinfo.value))
    assert client.calls == []


async def test_documenting_a_dataset_never_falls_back_to_a_whole_record_put(tools, client):
    """The dataset record always exists, so a 404 means the DATASET does — not a
    missing metadata row. A PUT fallback here (which sheet and column writes do
    have) would be a whole-record replace against a resource the caller cannot
    see, so the 404 must surface as the 404 it is."""
    client.on_patch("/datasets/ds-1", problem(404, "Dataset not found: ds-1", "http-404"))

    with pytest.raises(ToolError) as excinfo:
        await tools["write_documentation"](target="dataset", dataset_id="ds-1", domain="sales")

    assert "owned by a team you are not in" in str(excinfo.value)
    assert client.trace() == [("PATCH", "/datasets/ds-1")]


async def test_an_unknown_target_names_the_three_that_exist(tools, client):
    """The first decision the tool makes. Getting it wrong must not read like a
    service error, and must not cost a round trip to find the vocabulary."""
    with pytest.raises(ToolError) as excinfo:
        await tools["write_documentation"](target="table", dataset_id="ds-1")

    assert "target must be one of dataset, sheet, column, got 'table'." in str(excinfo.value)
    assert client.calls == []


async def test_the_target_is_matched_case_insensitively(tools, client):
    """'Dataset' is unambiguous; rejecting it would be pedantry that costs a
    retry. (Contrast `sort_order` in _common, which deliberately does not
    normalize because there the two spellings mean different things to the
    service.)"""
    client.on_patch("/datasets/ds-1", patched_dataset(domain="sales"))

    await tools["write_documentation"](target=" Dataset ", dataset_id="ds-1", domain="sales")

    assert client.calls_to("PATCH", "/datasets/ds-1")


# ===========================================================================
# write_documentation — target='sheet' and target='column'
# ===========================================================================


async def test_documenting_a_sheet_without_a_sheet_key_says_which_target_needs_it(tools, client):
    """Otherwise the request goes to `/sheet-metadata/None` and comes back a
    404, which the guard explains as 'may be owned by another team' — a
    completely wrong diagnosis of a missing argument."""
    with pytest.raises(ToolError) as excinfo:
        await tools["write_documentation"](target="sheet", dataset_id="ds-1", grain="one per order")

    assert "sheet_key is required for target='sheet'." in str(excinfo.value)
    assert client.calls == []


async def test_documenting_a_column_without_a_column_name_says_so(tools, client):
    """Same failure mode one level down: sheet_key present, column_name absent."""
    with pytest.raises(ToolError) as excinfo:
        await tools["write_documentation"](
            target="column", dataset_id="ds-1", sheet_key="orders", business_name="Customer"
        )

    assert "column_name is required for target='column'." in str(excinfo.value)
    assert client.calls == []


async def test_sheet_documentation_goes_to_the_logical_sheet_key_path(tools, client):
    """Sheet documentation is keyed by the LOGICAL sheet, which is what lets it
    survive a confirmed sheet rename. A path built from anything else (a sheet
    name, a version-scoped id) silently strands the record on the old sheet."""
    client.on_patch(
        "/datasets/ds-1/sheet-metadata/orders",
        sheet_metadata(sheet_key="orders", grain="one row per order line",
                       primary_key_columns=["order_id", "line_no"]),
    )

    out = await tools["write_documentation"](
        target="sheet", dataset_id="ds-1", sheet_key="orders",
        grain="one row per order line", primary_key_columns=["order_id", "line_no"],
    )

    call = client.one_call_to("PATCH", "/datasets/ds-1/sheet-metadata/orders")
    assert call.body == {
        "grain": "one row per order line",
        "primary_key_columns": ["order_id", "line_no"],
    }
    assert "Updated sheet metadata for 'orders'." in out
    assert "primary_key_columns: order_id, line_no" in out


async def test_the_first_documentation_ever_written_for_a_sheet_falls_back_to_put(tools, client):
    """PATCH updates but never creates, so the very first write for a sheet 404s
    and the caller is told the sheet may belong to another team. The PUT fallback
    is safe precisely because the record is new: on an absent record PUT and
    PATCH mean the same thing. The body must be identical — a fallback that
    re-derived it could replace fields the PATCH intended to leave alone."""
    client.on_patch(
        "/datasets/ds-1/sheet-metadata/orders",
        problem(404, "No metadata recorded for sheet: orders", "http-404"),
    )
    client.on_put(
        "/datasets/ds-1/sheet-metadata/orders",
        sheet_metadata(sheet_key="orders", grain="one row per order"),
    )

    out = await tools["write_documentation"](
        target="sheet", dataset_id="ds-1", sheet_key="orders", grain="one row per order"
    )

    assert client.trace() == [
        ("PATCH", "/datasets/ds-1/sheet-metadata/orders"),
        ("PUT", "/datasets/ds-1/sheet-metadata/orders"),
    ]
    patched = client.one_call_to("PATCH", "/datasets/ds-1/sheet-metadata/orders")
    put = client.one_call_to("PUT", "/datasets/ds-1/sheet-metadata/orders")
    assert put.body == patched.body == {"grain": "one row per order"}
    assert "Updated sheet metadata for 'orders'." in out


async def test_a_non_404_failure_never_retries_as_a_whole_record_put(tools, client):
    """The fallback is only sound for a record that does not exist yet. Retrying
    a 403 (or a 409, or a 422) as PUT against a record that DOES exist is a
    whole-record replace: every documented field the caller did not send this
    time would be blanked."""
    client.on_patch(
        "/datasets/ds-1/sheet-metadata/orders",
        problem(403, "Requires dataset:write", "http-403"),
    )

    with pytest.raises(ToolError) as excinfo:
        await tools["write_documentation"](
            target="sheet", dataset_id="ds-1", sheet_key="orders", grain="one per order"
        )

    assert client.trace() == [("PATCH", "/datasets/ds-1/sheet-metadata/orders")]
    assert "lack the required permission" in str(excinfo.value)


async def test_a_sheet_write_can_only_clear_the_three_sheet_fields(tools, client):
    """`clear` is validated per target. Naming a dataset field while documenting
    a sheet must not be forwarded and must not borrow the dataset vocabulary."""
    with pytest.raises(ToolError) as excinfo:
        await tools["write_documentation"](
            target="sheet", dataset_id="ds-1", sheet_key="orders", clear=["domain"]
        )

    message = str(excinfo.value)
    assert "clear names 'domain', which is not a sheet field." in message
    assert "Clearable sheet fields: grain, primary_key_columns, description." in message


async def test_column_documentation_is_written_under_the_normalized_column_name(tools, client):
    """The dictionary entry is stored per normalized name, and the reader tools
    (get_data_dictionary) look it up that way. A write that landed under a
    physical name would read back as 'documentation: none'."""
    client.on_patch(
        "/datasets/ds-1/sheet-metadata/orders/columns/customer_id",
        column_metadata("customer_id", business_name="Customer", sheet_key="orders",
                        semantic_type="uuid", allowed_values=["US", "GB"]),
    )

    out = await tools["write_documentation"](
        target="column", dataset_id="ds-1", sheet_key="orders", column_name="customer_id",
        business_name="Customer", semantic_type="uuid", allowed_values=["US", "GB"],
    )

    call = client.one_call_to("PATCH", "/datasets/ds-1/sheet-metadata/orders/columns/customer_id")
    assert call.body == {
        "business_name": "Customer", "semantic_type": "uuid", "allowed_values": ["US", "GB"],
    }
    assert "Updated dictionary entry for orders.customer_id." in out
    assert "allowed_values: US, GB" in out


async def test_recording_a_sensitivity_says_plainly_that_it_masks_nothing(tools, client):
    """The dangerous misreading of this tool. A model that writes
    sensitivity='pii' and reports the column protected has told its user
    something false; the field is documentation and no read path consults it."""
    client.on_patch(
        "/datasets/ds-1/sheet-metadata/orders/columns/email",
        column_metadata("email", sensitivity="pii", sheet_key="orders"),
    )

    out = await tools["write_documentation"](
        target="column", dataset_id="ds-1", sheet_key="orders",
        column_name="email", sensitivity="pii",
    )

    assert "Sensitivity here is a label, not an access control" in out
    assert "nothing is masked as a result of it" in out


async def test_clearing_a_column_field_survives_as_a_null_to_the_wire(tools, client):
    """Same null-stripping hazard as the dataset target, on the path a model is
    most likely to use: removing a unit or a semantic_type that turned out wrong."""
    client.on_patch(
        "/datasets/ds-1/sheet-metadata/orders/columns/amount",
        column_metadata("amount", sheet_key="orders"),
    )

    await tools["write_documentation"](
        target="column", dataset_id="ds-1", sheet_key="orders",
        column_name="amount", clear=["unit", "semantic_type"],
    )

    body = client.one_call_to(
        "PATCH", "/datasets/ds-1/sheet-metadata/orders/columns/amount"
    ).body
    assert body == {"unit": None, "semantic_type": None}


# ===========================================================================
# manage_quality_rules
# ===========================================================================


async def test_creating_a_rule_defaults_to_error_severity_and_enabled(tools, client):
    """These two defaults decide whether the rule gates tag promotion and
    whether it runs at all. Defaulting to warning, or to disabled, would produce
    a dataset that looks governed and is not."""
    client.on_post("/datasets/ds-1/rules", rule(name="customer_id is present"))

    out = await tools["manage_quality_rules"](
        action="create", dataset_id="ds-1", name="customer_id is present",
        rule_type="not_null", sheet_selector="orders", column_selector="customer_id",
    )

    assert client.one_call_to("POST", "/datasets/ds-1/rules").body == {
        "name": "customer_id is present", "rule_type": "not_null",
        "sheet_selector": "orders", "column_selector": "customer_id",
        "parameters": {}, "severity": "error", "enabled": True,
    }
    assert "Created rule 'customer_id is present' (r-1)." in out


async def test_a_newly_created_rule_says_nothing_has_been_evaluated_yet(tools, client):
    """Rules are declarations. A model that creates one and reports the dataset
    validated has skipped the step that actually checks anything, and
    get_dataset_health will still say 'unknown'."""
    client.on_post("/datasets/ds-1/rules", rule(enabled=True))

    out = await tools["manage_quality_rules"](
        action="create", dataset_id="ds-1", name="r", rule_type="unique",
        sheet_selector="orders", column_selector="order_id",
    )

    assert "Nothing has been evaluated yet" in out
    assert "run_quality_check with action='validate'" in out


async def test_a_rule_created_disabled_says_validation_will_skip_it(tools, client):
    """enabled=false is the documented substitute for the delete that does not
    exist. Reporting it like any other successful create would leave the caller
    expecting it to run."""
    client.on_post("/datasets/ds-1/rules", rule(enabled=False))

    out = await tools["manage_quality_rules"](
        action="create", dataset_id="ds-1", name="r", rule_type="unique",
        sheet_selector="orders", column_selector="order_id", enabled=False,
    )

    assert "This rule is disabled and will be skipped by validation runs." in out
    assert client.one_call_to("POST", "/datasets/ds-1/rules").body["enabled"] is False


async def test_an_unsupported_rule_type_lists_every_type_that_exists(tools, client):
    """The service would 422 this as a Literal violation, which renders as a
    pydantic dump. The eight valid types are a short, closed, useful list — say
    them and the retry is one step."""
    with pytest.raises(ToolError) as excinfo:
        await tools["manage_quality_rules"](
            action="create", dataset_id="ds-1", name="r", rule_type="not_nul",
            sheet_selector="orders",
        )

    message = str(excinfo.value)
    assert "rule_type 'not_nul' is not supported." in message
    assert ("Valid types: accepted_values, foreign_key, not_null, range, regex_match, "
            "row_count_min, sheet_exists, unique." in message)
    assert client.calls == []


async def test_a_dataset_scoped_rule_still_requires_a_sheet_selector(tools, client):
    """The counterintuitive one: sheet_exists is 'dataset'-scoped, so a caller
    reasonably assumes it needs no sheet. It does — the selector names the sheet
    whose existence is asserted. The refusal has to say the scope out loud or it
    reads like a bug in the tool."""
    with pytest.raises(ToolError) as excinfo:
        await tools["manage_quality_rules"](
            action="create", dataset_id="ds-1", name="orders exists",
            rule_type="sheet_exists",
        )

    message = str(excinfo.value)
    assert "sheet_selector is required for every rule type, including sheet_exists" in message
    assert "(dataset scope)" in message
    assert "Pass a sheet_key from describe_dataset." in message


@pytest.mark.parametrize(
    "rule_type,scope",
    [("not_null", "column"), ("unique", "column"), ("range", "column"),
     ("foreign_key", "cross_sheet")],
)
async def test_a_rule_that_needs_a_column_names_its_scope_when_one_is_missing(
    tools, client, rule_type, scope
):
    """Which rule types need a column is not guessable from the name — and the
    service records a rule with a null selector, then reports status='error' at
    validation time. Catching it at create keeps the failure at the point the
    caller can still fix it, and naming the scope explains why."""
    kwargs: dict[str, Any] = {
        "action": "create", "dataset_id": "ds-1", "name": "r",
        "rule_type": rule_type, "sheet_selector": "orders",
    }
    if rule_type == "range":
        kwargs["parameters"] = {"min": 0}
    if rule_type == "foreign_key":
        kwargs["parameters"] = {"ref_sheet": "customers", "ref_column": "customer_id"}

    with pytest.raises(ToolError) as excinfo:
        await tools["manage_quality_rules"](**kwargs)

    assert f"{rule_type} is a {scope}-scoped rule and needs column_selector" in str(excinfo.value)
    assert "(a normalized column name)" in str(excinfo.value)


@pytest.mark.parametrize(
    "rule_type,parameters,expected",
    [
        ("accepted_values", None,
         'accepted_values needs parameters {"values": [...]} with at least one entry.'),
        ("accepted_values", {"values": []},
         'accepted_values needs parameters {"values": [...]} with at least one entry.'),
        ("foreign_key", {"ref_sheet": "customers"},
         'foreign_key needs parameters {"ref_sheet": "<sheet_key>", "ref_column": "<column>"}.'),
        ("regex_match", {},
         'regex_match needs parameters {"pattern": "<regex>"}.'),
        ("range", {},
         'range needs parameters with "min", "max", or both'),
    ],
    ids=["accepted-values-absent", "accepted-values-empty", "foreign-key-half",
         "regex-no-pattern", "range-neither-bound"],
)
async def test_missing_rule_parameters_are_answered_with_the_exact_shape_required(
    tools, client, rule_type, parameters, expected
):
    """`parameters` is a free-form dict, so the service stores whatever arrives
    and the rule fails only later, at validation time, as status='error' — long
    after the caller has moved on. Each refusal quotes the literal JSON to send,
    which is the difference between one retry and a guessing loop."""
    with pytest.raises(ToolError) as excinfo:
        await tools["manage_quality_rules"](
            action="create", dataset_id="ds-1", name="r", rule_type=rule_type,
            sheet_selector="orders", column_selector="c", parameters=parameters,
        )

    assert expected in str(excinfo.value)
    assert client.calls == [], "an unusable rule must not be persisted"


async def test_a_range_rule_with_only_an_upper_bound_is_accepted(tools, client):
    """The rule needs min OR max, not both. Requiring both would make 'no order
    over 100000' unexpressible, and the error message promises otherwise."""
    client.on_post("/datasets/ds-1/rules", rule(rule_type="range"))

    await tools["manage_quality_rules"](
        action="create", dataset_id="ds-1", name="r", rule_type="range",
        sheet_selector="orders", column_selector="amount", parameters={"max": 100000},
    )

    assert client.one_call_to("POST", "/datasets/ds-1/rules").body["parameters"] == {
        "max": 100000
    }


async def test_a_range_rule_whose_bound_is_zero_is_not_mistaken_for_no_bound(tools, client):
    """`{"min": 0}` is the single most likely range rule anyone writes
    ('amount is never negative'), and a falsiness check instead of an is-None
    check rejects it. The rule would be unwritable through this tool."""
    client.on_post("/datasets/ds-1/rules", rule(rule_type="range"))

    await tools["manage_quality_rules"](
        action="create", dataset_id="ds-1", name="non-negative", rule_type="range",
        sheet_selector="orders", column_selector="amount", parameters={"min": 0},
    )

    assert client.one_call_to("POST", "/datasets/ds-1/rules").body["parameters"] == {"min": 0}


async def test_creating_a_rule_requires_a_name_and_says_when(tools, client):
    """Names are the rule's identity in every validation result and in the
    duplicate-name 409. An unnamed rule is a 422 about a request body."""
    with pytest.raises(ToolError) as excinfo:
        await tools["manage_quality_rules"](
            action="create", dataset_id="ds-1", rule_type="unique",
            sheet_selector="orders", column_selector="order_id",
        )

    assert "name is required on create." in str(excinfo.value)


async def test_updating_a_rule_requires_the_rule_id_and_says_when(tools, client):
    """Without it the PATCH path is `/rules/None`, which 404s as
    'may be owned by another team' — the wrong diagnosis entirely."""
    with pytest.raises(ToolError) as excinfo:
        await tools["manage_quality_rules"](
            action="update", dataset_id="ds-1", enabled=False
        )

    assert "rule_id is required on update." in str(excinfo.value)
    assert client.calls == []


async def test_an_update_with_nothing_to_change_lists_what_can_be_changed(tools, client):
    """An empty PATCH 200s and returns the record unchanged, so the tool would
    report a successful update that changed nothing. The refusal doubles as the
    place to say rule_type is immutable, which is the field a caller most often
    came here to change."""
    with pytest.raises(ToolError) as excinfo:
        await tools["manage_quality_rules"](
            action="update", dataset_id="ds-1", rule_id="r-1"
        )

    message = str(excinfo.value)
    assert ("Provide at least one field to update: name, description, sheet_selector, "
            "column_selector, parameters, severity, or enabled." in message)
    assert "rule_type cannot be changed — create a new rule instead." in message
    assert client.calls == []


async def test_disabling_a_rule_is_a_patch_of_exactly_that_one_field(tools, client):
    """Retirement is enabled=false because there is no delete. If the tool sent
    the unset parameters alongside it, retiring a rule would also wipe its
    selectors and parameters — and `parameters` on update REPLACES, so the rule
    could not be re-enabled to what it was."""
    client.on_patch("/datasets/ds-1/rules/r-1", rule(enabled=False))

    out = await tools["manage_quality_rules"](
        action="update", dataset_id="ds-1", rule_id="r-1", enabled=False
    )

    assert client.one_call_to("PATCH", "/datasets/ds-1/rules/r-1").body == {"enabled": False}
    assert "Updated rule" in out
    assert "This rule is disabled and will be skipped by validation runs." in out


async def test_an_unsupported_severity_names_both_valid_values_and_is_not_sent(tools, client):
    """severity decides whether a failure blocks promote_tag. 'critical' stored
    verbatim would be a rule whose failures block nothing, silently."""
    with pytest.raises(ToolError) as excinfo:
        await tools["manage_quality_rules"](
            action="create", dataset_id="ds-1", name="r", rule_type="unique",
            sheet_selector="orders", column_selector="order_id", severity="critical",
        )

    assert "severity must be one of error, warning, got 'critical'." in str(excinfo.value)
    assert client.calls == []


async def test_an_unknown_rule_action_names_create_and_update_and_not_delete(tools, client):
    """There is deliberately no delete. A caller who tries one must be told the
    two actions that exist rather than getting a 405 from a path that was never
    built."""
    with pytest.raises(ToolError) as excinfo:
        await tools["manage_quality_rules"](
            action="delete", dataset_id="ds-1", rule_id="r-1"
        )

    assert "action must be one of create, update, got 'delete'." in str(excinfo.value)


# ===========================================================================
# run_quality_check — action='validate'
# ===========================================================================


async def test_validating_without_a_version_resolves_the_newest_ready_one(tools, client):
    """The version list is newest-first and may lead with a version still
    processing. Validating that one is a 409; silently validating an OLDER
    version than the caller meant would be worse — the run would pass and gate
    nothing that matters."""
    client.on_get(
        "/datasets/ds-1/versions",
        page([version(version_number=5, status="processing"),
              version(version_number=4, status="ready")]),
    )
    client.on_post("/datasets/ds-1/versions/4/validate", validation_run())

    out = await tools["run_quality_check"](action="validate", dataset_id="ds-1")

    assert client.trace() == [
        ("GET", "/datasets/ds-1/versions"),
        ("POST", "/datasets/ds-1/versions/4/validate"),
    ]
    assert "Validated version 4." in out


async def test_an_explicit_version_is_used_without_listing_the_versions(tools, client):
    """Pinning a version is how an older one gets validated at all. A tool that
    still resolved the newest ready version would ignore the argument and report
    success against the wrong data."""
    client.on_post("/datasets/ds-1/versions/2/validate", validation_run())

    out = await tools["run_quality_check"](action="validate", dataset_id="ds-1", version=2)

    assert client.trace() == [("POST", "/datasets/ds-1/versions/2/validate")]
    assert "Validated version 2." in out


async def test_validating_a_dataset_with_no_enabled_rules_says_how_to_get_some(tools, client):
    """A 400 here means the dataset declared nothing to check. The trap is a
    dataset whose rules all exist but are disabled — the caller sees rules in
    get_dataset_health and cannot explain the 400 — so the message has to
    single that case out."""
    client.on_post(
        "/datasets/ds-1/versions/4/validate",
        problem(400, "Dataset has no enabled quality rules.", "bad_request"),
    )

    with pytest.raises(ToolError) as excinfo:
        await tools["run_quality_check"](action="validate", dataset_id="ds-1", version=4)

    message = str(excinfo.value)
    assert "Dataset has no enabled quality rules." in message
    assert "Create at least one rule with manage_quality_rules(action='create')" in message
    assert "a rule that exists but is disabled does not count" in message


async def test_validating_a_version_that_is_not_ready_points_at_describe_dataset(tools, client):
    """A 409 is about version STATUS, not about the rules. Without the pointer
    to where statuses are visible, the only next move is to retry blindly."""
    client.on_post(
        "/datasets/ds-1/versions/4/validate",
        problem(409, "Version 4 is not ready (status: processing).", "conflict"),
    )

    with pytest.raises(ToolError) as excinfo:
        await tools["run_quality_check"](action="validate", dataset_id="ds-1", version=4)

    message = str(excinfo.value)
    assert "Version 4 is not ready (status: processing)." in message
    assert "Only a 'ready' version can be validated" in message
    assert "check describe_dataset for version statuses" in message


async def test_a_permission_failure_on_validate_is_not_swallowed_by_the_local_handler(tools, client):
    """The tool catches ProblemError to special-case 400 and 409. Anything else
    must re-raise so `guard` renders it — a bare `except ProblemError` here
    would turn every 403 and 404 into one of the two rule-shaped explanations."""
    client.on_post(
        "/datasets/ds-1/versions/4/validate",
        problem(403, "Requires dataset:write on this dataset.", "http-403"),
    )

    with pytest.raises(ToolError) as excinfo:
        await tools["run_quality_check"](action="validate", dataset_id="ds-1", version=4)

    message = str(excinfo.value)
    assert "Requires dataset:write on this dataset." in message
    assert "lack the required permission" in message
    assert "enabled quality rules" not in message


async def test_a_validation_run_reports_each_rule_with_its_failure_count(tools, client):
    """The run header alone ('3 failed') names nothing actionable. The per-rule
    table is what tells a caller which sheet and column to go and fix."""
    client.on_post(
        "/datasets/ds-1/versions/4/validate",
        validation_run(
            [
                {"rule_name": "customer_id present", "rule_type": "not_null",
                 "scope_type": "column", "sheet_selector": "orders",
                 "column_selector": "customer_id", "severity": "error",
                 "status": "failed", "failure_count": 12,
                 "message": "12 null values"},
                {"rule_name": "order_id unique", "rule_type": "unique",
                 "scope_type": "column", "sheet_selector": "orders",
                 "column_selector": "order_id", "severity": "error", "status": "passed"},
            ],
            rules_total=2, rules_passed=1, rules_failed=1,
            error_failures=0, warning_failures=0,
        ),
    )

    out = await tools["run_quality_check"](action="validate", dataset_id="ds-1", version=4)

    assert "validation_run_id: vr-1" in out
    assert "rules_total: 2" in out
    assert "customer_id present | not_null | orders | customer_id | error | failed | 12" in out
    assert "order_id unique" in out


async def test_failing_rows_are_offered_as_artifacts_with_the_tool_that_reads_them(tools, client):
    """Failing rows are dataset content, so they go to the object store rather
    than into the response. Naming the file without naming read_artifact leaves
    a filename with no documented way to open it."""
    client.on_post(
        "/datasets/ds-1/versions/4/validate",
        validation_run(
            [{"rule_name": "customer_id present", "rule_type": "not_null",
              "scope_type": "column", "severity": "error", "status": "failed",
              "failure_count": 12, "failure_sample_file": "failures_r1.parquet"}],
            rules_failed=1,
        ),
    )

    out = await tools["run_quality_check"](action="validate", dataset_id="ds-1", version=4)

    assert "## Failing rows saved (up to 5 per rule)" in out
    assert "- customer_id present -> failures_r1.parquet" in out
    assert "Read any of these with read_artifact(filename=...)." in out


async def test_a_rule_that_could_not_be_evaluated_is_distinguished_from_a_rule_that_failed(
    tools, client
):
    """status='error' means the RULE is broken — a selector pointing at a column
    that no longer exists — not that the data is bad. Read as a data failure it
    sends someone to fix rows that are fine, while the rule keeps not running."""
    client.on_post(
        "/datasets/ds-1/versions/4/validate",
        validation_run(
            [{"rule_name": "legacy_code in range", "rule_type": "range",
              "scope_type": "column", "severity": "error", "status": "error",
              "message": "Column 'legacy_code' not found"}],
        ),
    )

    out = await tools["run_quality_check"](action="validate", dataset_id="ds-1", version=4)

    assert "Rules that could not be evaluated at all: legacy_code in range." in out
    assert "'error' means the rule is broken (missing sheet or column, bad parameters)" in out
    assert "not that the data failed" in out


async def test_error_level_failures_warn_that_promotion_is_now_blocked(tools, client):
    """The consequence a caller will otherwise meet as an unexplained 409 from
    manage_tags(action='promote'), one tool call later. Stating it here, with
    both ways out, is the difference between a blocked promotion and a mystery."""
    client.on_post(
        "/datasets/ds-1/versions/4/validate",
        validation_run(
            [{"rule_name": "customer_id present", "rule_type": "not_null",
              "scope_type": "column", "severity": "error", "status": "failed",
              "failure_count": 12}],
            rules_failed=1, error_failures=1, warning_failures=0,
        ),
    )

    out = await tools["run_quality_check"](action="validate", dataset_id="ds-1", version=4)

    assert "manage_tags action='promote' will refuse this version" in out
    assert "downgraded to severity='warning'" in out


async def test_a_clean_validation_run_adds_none_of_the_warning_notes(tools, client):
    """The notes are conditional. Emitting the promotion-blocked warning on a
    passing run would teach a caller to ignore it on the run where it matters."""
    client.on_post(
        "/datasets/ds-1/versions/4/validate",
        validation_run(
            [{"rule_name": "order_id unique", "rule_type": "unique",
              "scope_type": "column", "severity": "error", "status": "passed"}],
            rules_total=1, rules_passed=1, rules_failed=0,
            error_failures=0, warning_failures=0,
        ),
    )

    out = await tools["run_quality_check"](action="validate", dataset_id="ds-1", version=4)

    assert "will refuse this version" not in out
    assert "could not be evaluated" not in out
    assert "Failing rows saved" not in out


async def test_only_warning_level_failures_do_not_claim_promotion_is_blocked(tools, client):
    """severity='warning' is the documented way to record a known-imperfect rule
    without gating releases. Warning about promotion here would make the
    downgrade pointless."""
    client.on_post(
        "/datasets/ds-1/versions/4/validate",
        validation_run(
            [{"rule_name": "amount in range", "rule_type": "range",
              "scope_type": "column", "severity": "warning", "status": "failed",
              "failure_count": 3}],
            rules_failed=1, error_failures=0, warning_failures=1,
        ),
    )

    out = await tools["run_quality_check"](action="validate", dataset_id="ds-1", version=4)

    assert "warning_failures: 1" in out
    assert "will refuse this version" not in out


async def test_an_enormous_validation_run_is_truncated_with_a_way_to_narrow_it(tools, client):
    """One tool response is capped at 60k characters. Without the cap a dataset
    with thousands of rules returns a response no context window can hold; with
    a cap but no hint, the caller has no next move."""
    many = [
        {"rule_name": f"rule number {i}", "rule_type": "not_null", "scope_type": "column",
         "sheet_selector": "orders", "column_selector": f"column_{i}", "severity": "error",
         "status": "failed", "failure_count": i,
         "message": "a reasonably long explanation of what went wrong here" }
        for i in range(2000)
    ]
    client.on_post("/datasets/ds-1/versions/4/validate", validation_run(many, rules_failed=2000))

    out = await tools["run_quality_check"](action="validate", dataset_id="ds-1", version=4)

    assert "[response truncated at 60,000 characters." in out
    assert "read the per-rule results with get_dataset_health" in out
    assert len(out) < 61_000


# ===========================================================================
# run_quality_check — action='profile'
# ===========================================================================


async def test_profiling_reports_one_row_per_sheet_and_every_insight_it_found(tools, client):
    """Profiling exists to replace get_dataset_health's 'unknown'. A summary
    that counted runs without listing the insights would leave the caller
    knowing something was found and not what."""
    client.on_post(
        "/datasets/ds-1/versions/4/profile-runs",
        [
            profile_run(
                [insight(rule="high_null_rate", severity="warning",
                         column_name="customer_id", message="42% null"),
                 insight(rule="constant_column", severity="info",
                         column_name="region", message="one distinct value")],
                sheet_name="orders",
            ),
            profile_run(id="pr-2", sheet_name="customers", logical_sheet_id="ls-2"),
        ],
    )

    out = await tools["run_quality_check"](action="profile", dataset_id="ds-1", version=4)

    assert "Profiled version 4 — 2 sheet(s), 2 insight(s)." in out
    assert "orders | completed | 2 | 3" in out
    assert "customers | completed | 0 | 3" in out
    assert "orders | warning | high_null_rate | customer_id | 42% null" in out
    assert "orders | info | constant_column | region | one distinct value" in out
    assert "get_dataset_health will stop reporting 'unknown'" in out


async def test_a_sheet_whose_profile_run_errored_is_shown_rather_than_dropped(tools, client):
    """Silently omitting a failed sheet makes a partial profile look complete,
    and the health report then keeps saying 'unknown' for that sheet with no
    trace of why."""
    client.on_post(
        "/datasets/ds-1/versions/4/profile-runs",
        [profile_run(sheet_name="orders", status="failed",
                     error="Sheet data file missing")],
    )

    out = await tools["run_quality_check"](action="profile", dataset_id="ds-1", version=4)

    assert "orders | failed | 0 | 3 | Sheet data file missing" in out


async def test_profiling_a_version_with_no_sheets_says_so_instead_of_rendering_nothing(
    tools, client
):
    """An empty list must not render as a blank success. `(no rows)` plus the
    zero counts is the difference between 'profiling found nothing' and
    'profiling did not run'."""
    client.on_post("/datasets/ds-1/versions/4/profile-runs", [])

    out = await tools["run_quality_check"](action="profile", dataset_id="ds-1", version=4)

    assert "Profiled version 4 — 0 sheet(s), 0 insight(s)." in out
    assert "(no rows)" in out


async def test_a_page_wrapped_profile_response_is_unwrapped_rather_than_counted_as_zero(
    tools, client
):
    """The route's response_model is a bare `list[ProfileRunOut]` today, unlike
    every other list endpoint. If it is ever brought into line with the `Page`
    envelope, this fallback is what stops the tool reporting '0 sheet(s)' for a
    successful profile of a dataset that has plenty."""
    client.on_post(
        "/datasets/ds-1/versions/4/profile-runs",
        page([profile_run(sheet_name="orders"),
              profile_run(id="pr-2", sheet_name="customers", logical_sheet_id="ls-2")]),
    )

    out = await tools["run_quality_check"](action="profile", dataset_id="ds-1", version=4)

    assert "Profiled version 4 — 2 sheet(s), 0 insight(s)." in out
    assert "orders | completed" in out


async def test_an_unknown_quality_check_action_names_validate_and_profile(tools, client):
    """The two actions do different work and need different permissions, and
    'run' or 'check' are the obvious wrong guesses."""
    with pytest.raises(ToolError) as excinfo:
        await tools["run_quality_check"](action="run", dataset_id="ds-1")

    assert "action must be one of validate, profile, got 'run'." in str(excinfo.value)
    assert client.calls == [], "the version list must not be fetched for an invalid action"


async def test_a_dataset_with_no_ready_version_is_explained_before_any_write(tools, client):
    """resolve_version's refusal has to reach the caller intact: a dataset with
    only a processing version is a wait-and-retry, not a permissions problem or
    a missing rule set."""
    client.on_get("/datasets/ds-1/versions", page([version(version_number=1, status="processing")]))

    with pytest.raises(ToolError) as excinfo:
        await tools["run_quality_check"](action="validate", dataset_id="ds-1")

    assert "no ready version to read" in str(excinfo.value)
    assert client.trace() == [("GET", "/datasets/ds-1/versions")]


# ===========================================================================
# manage_tags
# ===========================================================================


async def test_setting_a_tag_sends_the_name_and_version_and_admits_it_was_ungated(tools, client):
    """action='set' is the escape hatch: no validation, no ready check, no audit
    reason. Rendered like a promotion it would become the default path, and the
    quality gate would be decorative."""
    client.on_put("/datasets/ds-1/tags", tag_info(tag_name="production", version_number=4))

    out = await tools["manage_tags"](action="set", dataset_id="ds-1", tag="production", version=4)

    assert client.one_call_to("PUT", "/datasets/ds-1/tags").body == {
        "tag_name": "production", "version_number": 4
    }
    assert "Tag 'production' now points at version 4." in out
    assert "This was the ungated path — no validation was checked." in out
    assert "Use action='promote' when the move should be governed and auditable." in out


async def test_setting_a_tag_without_a_version_is_refused_before_the_write(tools, client):
    """A tag with no target is meaningless, and the PUT body would carry a null
    version_number that the request model rejects as a validation dump."""
    with pytest.raises(ToolError) as excinfo:
        await tools["manage_tags"](action="set", dataset_id="ds-1", tag="production")

    assert "version is required for action='set'." in str(excinfo.value)
    assert client.calls == []


async def test_promoting_without_a_version_is_refused_before_the_write(tools, client):
    """Same contract on the governed path — and here a mistake would be recorded
    in the audit history, so it must not be attempted at all."""
    with pytest.raises(ToolError) as excinfo:
        await tools["manage_tags"](action="promote", dataset_id="ds-1", tag="production")

    assert "version is required for action='promote'." in str(excinfo.value)
    assert client.calls == []


async def test_a_tag_name_is_lowercased_and_stripped_before_it_becomes_a_url_path(tools, client):
    """Tags are case-insensitive and stored lowercased. 'Production' left as-is
    in the path is a lookup against a tag that does not exist — a 404 that reads
    as 'the dataset may belong to another team' for a tag sitting right there."""
    client.on_post("/datasets/ds-1/tags/production/promote", tag_op())

    await tools["manage_tags"](
        action="promote", dataset_id="ds-1", tag="  Production  ", version=4, reason="ship it"
    )

    call = client.one_call_to("POST", "/datasets/ds-1/tags/production/promote")
    assert call.body == {"version_number": 4, "reason": "ship it"}


async def test_a_promotion_blocked_for_want_of_a_validation_run_names_the_call_to_make(
    tools, client
):
    """'validation-required' means the gate has never run, not that the data is
    bad — and the fix is one specific tool call with one specific version. A
    caller told only 'validation required' is as likely to retry the promotion."""
    client.on_post(
        "/datasets/ds-1/tags/production/promote",
        problem(409, "Version 4 has no completed validation run.", "validation-required"),
    )

    with pytest.raises(ToolError) as excinfo:
        await tools["manage_tags"](
            action="promote", dataset_id="ds-1", tag="production", version=4
        )

    message = str(excinfo.value)
    assert "Version 4 has no completed validation run." in message
    assert "run_quality_check(action='validate', version=4)" in message
    assert "This dataset has enabled quality rules" in message


async def test_a_promotion_blocked_by_failures_reports_the_run_and_the_three_ways_out(
    tools, client
):
    """'validation-failed' is the gate working. The response carries the run id
    and the failure counts, which are the only handle on WHICH failures — drop
    them and the caller cannot find the run. The three exits (fix, downgrade,
    ungated set) must all be named, or `set` gets rediscovered as a workaround
    with no idea that it bypasses the gate."""
    client.on_post(
        "/datasets/ds-1/tags/production/promote",
        problem(
            409, "Version 4 failed validation.", "validation-failed",
            validation_run_id="vr-9", error_failures=3, warning_failures=1,
        ),
    )

    with pytest.raises(ToolError) as excinfo:
        await tools["manage_tags"](
            action="promote", dataset_id="ds-1", tag="production", version=4
        )

    message = str(excinfo.value)
    assert "Version 4 failed validation." in message
    assert "validation_run_id vr-9" in message
    assert "3 error / 1 warning" in message
    assert "Fix the data and re-validate" in message
    assert "downgrade the offending rules to severity='warning'" in message
    assert "use action='set' as the documented ungated escape hatch" in message


async def test_an_unrelated_promotion_failure_is_left_to_the_generic_explanation(tools, client):
    """The local handler exists for the two quality-gate codes only. Catching
    everything would explain a 404 as a failed quality gate and send the caller
    to run validation on a dataset they cannot even see."""
    client.on_post(
        "/datasets/ds-1/tags/production/promote",
        problem(404, "Dataset not found: ds-1", "http-404"),
    )

    with pytest.raises(ToolError) as excinfo:
        await tools["manage_tags"](
            action="promote", dataset_id="ds-1", tag="production", version=4
        )

    message = str(excinfo.value)
    assert "owned by a team you are not in" in message
    assert "quality" not in message.lower()


async def test_a_successful_promotion_states_the_gate_it_passed(tools, client):
    """The promotion's value is the guarantee behind it. Rendered the same as an
    ungated `set`, a governed release is indistinguishable from a manual one in
    whatever the model reports back."""
    client.on_post(
        "/datasets/ds-1/tags/production/promote",
        tag_op(action="promote", from_version_number=3, to_version_number=4,
               reason="quarter close"),
    )

    out = await tools["manage_tags"](
        action="promote", dataset_id="ds-1", tag="production", version=4,
        reason="quarter close",
    )

    assert "Promoted tag 'production': version 3 -> 4." in out
    assert "reason: quarter close" in out
    assert "Promotion passed the quality gate" in out
    assert "no error-level failures" in out
    assert "The acting user is recorded automatically as the actor" in out


async def test_a_rollback_says_it_was_never_quality_gated(tools, client):
    """Rollback is the emergency path and is deliberately ungated, so it works
    while validation is failing. Reporting it as gate-approved would assert the
    restored version is validated when nothing checked."""
    client.on_post(
        "/datasets/ds-1/tags/production/rollback",
        tag_op(action="rollback", from_version_number=4, to_version_number=3,
               reason="bad numbers"),
    )

    out = await tools["manage_tags"](
        action="rollback", dataset_id="ds-1", tag="production", reason="bad numbers"
    )

    assert client.one_call_to("POST", "/datasets/ds-1/tags/production/rollback").body == {
        "reason": "bad numbers"
    }
    assert "Rolled back tag 'production': version 4 -> 3." in out
    assert "Rollback is never quality-gated" in out
    assert "Promotion passed the quality gate" not in out


async def test_a_rollback_with_nowhere_to_go_points_at_the_tag_history(tools, client):
    """Rollback replays the tag's OWN history, so a tag set once has no prior
    version. The 409 alone reads like the gate rejecting the rollback, which
    would be the exact opposite of what rollback is for."""
    client.on_post(
        "/datasets/ds-1/tags/production/rollback",
        problem(409, "Tag 'production' has no previous version.", "conflict"),
    )

    with pytest.raises(ToolError) as excinfo:
        await tools["manage_tags"](action="rollback", dataset_id="ds-1", tag="production")

    message = str(excinfo.value)
    assert "Tag 'production' has no previous version." in message
    assert "Rollback replays the tag's own history" in message
    assert "Check manage_tags(action='history')." in message


async def test_an_unrelated_rollback_failure_is_left_to_the_generic_explanation(tools, client):
    """The local handler here keys on STATUS (409), not on a code, so it is the
    broadest of the four catches. A 403 explained as 'this tag has no earlier
    version' would send the caller hunting through a history that is fine, when
    the real answer is that they cannot write to this dataset at all."""
    client.on_post(
        "/datasets/ds-1/tags/production/rollback",
        problem(403, "Requires dataset:write on this dataset.", "http-403"),
    )

    with pytest.raises(ToolError) as excinfo:
        await tools["manage_tags"](action="rollback", dataset_id="ds-1", tag="production")

    message = str(excinfo.value)
    assert "Requires dataset:write on this dataset." in message
    assert "lack the required permission" in message
    assert "history" not in message


async def test_tag_history_is_a_read_that_names_the_actor_and_the_reason(tools, client):
    """The history is the audit trail: who moved production, when, and why. A
    render that dropped the actor or the reason would leave a trail that proves
    a change happened and nothing about accountability for it."""
    client.on_get(
        "/datasets/ds-1/tags/production/history",
        page(
            [tag_history_entry(id=2, action="rollback", from_version_number=4,
                               to_version_number=3, reason="bad numbers",
                               actor_email="ada@example.com",
                               created_at="2026-08-07T10:00:00Z"),
             tag_history_entry(id=1, action="promote", from_version_number=3,
                               to_version_number=4, reason="quarter close",
                               actor_email="grace@example.com",
                               created_at="2026-08-06T10:00:00Z")],
            total=7,
        ),
    )

    out = await tools["manage_tags"](action="history", dataset_id="ds-1", tag="production")

    assert client.trace() == [("GET", "/datasets/ds-1/tags/production/history")]
    assert "## History of tag 'production'" in out
    assert "2026-08-07T10:00:00Z | rollback | 4 | 3 | ada@example.com | bad numbers" in out
    assert "2026-08-06T10:00:00Z | promote | 3 | 4 | grace@example.com | quarter close" in out
    assert "2 of 7 transitions shown." in out
    assert "Newest first." in out


async def test_history_falls_back_to_the_actor_user_id_when_there_is_no_email(tools, client):
    """A service account, or a user deleted since the transition, has no email.
    A blank actor column in an audit trail is the worst possible rendering of
    'we do know who did this'."""
    client.on_get(
        "/datasets/ds-1/tags/production/history",
        page([tag_history_entry(actor_email=None, actor_user_id="svc-loader")]),
    )

    out = await tools["manage_tags"](action="history", dataset_id="ds-1", tag="production")

    assert "svc-loader" in out


async def test_history_paging_arguments_reach_the_service(tools, client):
    """The truncation hint tells the caller to page with offset. If limit and
    offset were dropped, following that advice would return the same first page
    forever."""
    client.on_get("/datasets/ds-1/tags/production/history", page([], total=0))

    await tools["manage_tags"](
        action="history", dataset_id="ds-1", tag="production", limit=10, offset=20
    )

    call = client.one_call_to("GET", "/datasets/ds-1/tags/production/history")
    assert call.params == {"limit": 10, "offset": 20}


async def test_an_empty_tag_history_renders_as_no_rows_not_as_a_blank_section(tools, client):
    """A tag that exists but was only ever `set` with no recorded transitions
    reads identically to a broken response unless the emptiness is stated."""
    client.on_get("/datasets/ds-1/tags/production/history", page([], total=0))

    out = await tools["manage_tags"](action="history", dataset_id="ds-1", tag="production")

    assert "(no rows)" in out
    assert "0 transitions shown." in out


async def test_reading_history_writes_nothing(tools, client):
    """The one non-writing action on a writing tool. If it ever acquired a write
    (an access log, a touch), reading the audit trail would change the thing
    being audited."""
    client.on_get("/datasets/ds-1/tags/production/history", page([tag_history_entry()]))

    await tools["manage_tags"](action="history", dataset_id="ds-1", tag="Production")

    assert client.trace() == [("GET", "/datasets/ds-1/tags/production/history")]


async def test_an_unknown_tag_action_names_all_four_and_not_delete(tools, client):
    """There is deliberately no tag deletion. The refusal is where a caller
    finds that out, together with the four actions that do exist."""
    with pytest.raises(ToolError) as excinfo:
        await tools["manage_tags"](action="delete", dataset_id="ds-1", tag="production")

    assert "action must be one of set, promote, rollback, history, got 'delete'." in str(
        excinfo.value
    )
    assert client.calls == []

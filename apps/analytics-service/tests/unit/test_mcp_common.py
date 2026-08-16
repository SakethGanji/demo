"""``tools/_common.py`` — the error-translation core every MCP tool shares.

This module is where a service rejection becomes something a model can act on
*in one step*. That is the whole product of the tool layer: the service says
"this dataset has 3 sheets", ``explain`` says which three; the service says
"unknown column", ``explain`` says which columns exist. A test here that only
asserted "an error was raised" would pass against a version that returned a
bare ``"error"``, so every test below asserts on the actionable content — the
names, the vocabulary, the limit, the field path.

Covered: all seventeen ``explain`` branches (ten problem codes, four HTTP
statuses, the pydantic 422 array, the ``bad_request`` pass-through and the
fallback), their precedence over one another, ``require_sort_order``, ``clamp``,
``page_items``, ``sheet_path``, ``resolve_version`` and the ``guard`` decorator.

Two levels of fidelity are used deliberately:

* Most tests hand a ``ProblemError`` straight to ``explain``. That is honest —
  ``explain`` is pure, and its input is fully described by (status, code, detail,
  extra).
* The section "the actionable payload survives the real wire" instead raises the
  *service's own* ``ProblemException`` from its real raise site, serialises it
  through the real problem+json handler, and parses it with the real client
  parser. Those tests are the ones that would catch the failure mode this file
  is otherwise blind to: a raise site renaming ``available`` to ``columns``,
  leaving ``explain`` reading a key nobody sends and emitting
  "Available columns: ." forever.

Deliberately not covered: that the service reaches those raise sites at all —
that needs Postgres and lives in ``tests/test_mcp_endpoint.py``. Nor the MCP
argument models: calling a tool through the harness bypasses them
(``tests/unit/test_mcp_unknown_arguments.py`` is the model for schema-level
tests).
"""

from __future__ import annotations

import dataclasses

import httpx
import pytest
from fastapi.encoders import jsonable_encoder
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import ValidationError

from app.api.errors import ProblemException, problem_response
from app.features.data_accelerator.schemas import AggregateRequest
from app.features.data_accelerator.services.pivot import MAX_PIVOT_COLUMNS
from app.features.mcp.client import ProblemError, _problem_from
from app.features.mcp.tools import context, look
from app.features.mcp.tools._common import (
    SORT_ORDERS,
    Ctx,
    clamp,
    explain,
    guard,
    page_items,
    require_sort_order,
    resolve_version,
    sheet_path,
)
from app.shared.query.schemas import KNOWN_OPS, Filter, FilterGroup, QuerySpec
from app.shared.query.validate import validate_spec
from mcp_harness import (
    FakeAnalyticsClient,
    make_ctx,
    page,
    problem,
    query_page,
    register_tools,
    sheet_selection_required,
    unknown_column,
    version,
)


@pytest.fixture
def client() -> FakeAnalyticsClient:
    return FakeAnalyticsClient()


@pytest.fixture
def ctx(client) -> Ctx:
    return make_ctx(client)


def from_the_wire(exc: ProblemException) -> ProblemError:
    """Round-trip a service-raised problem through response and client parser.

    Uses the real ``problem_response`` (which builds the body every route
    returns) and the real ``client._problem_from`` (which splits RFC 7807 keys
    from the extras). A private import, on purpose: a reimplementation here
    could keep a key that the real parser drops, and the point of these tests is
    that the key reaches ``explain``.
    """
    response = problem_response(
        exc.status_code, str(exc.detail), "/api/v1/test", code=exc.code, **exc.extra
    )
    return _problem_from(
        httpx.Response(
            status_code=response.status_code,
            content=response.body,
            headers=dict(response.headers),
        )
    )


# ---------------------------------------------------------------------------
# explain — the branches keyed on problem code
#
# Each of these exists because the service's own `detail` stops one sentence
# short of a usable retry. The assertion is on the sentence that gets added.
# ---------------------------------------------------------------------------


def test_a_multi_sheet_rejection_names_every_sheet_the_caller_could_have_picked():
    """Without the names this is an infinite loop: the model is told to name a
    sheet, has no way to guess one, and the only tool that lists them is a
    different call. The names turn a dead end into a one-step retry."""
    out = explain(sheet_selection_required(["Q1", "Q2", "Annual Summary"]))

    assert "name one via the 'sheet' parameter" in out          # the service's half
    assert out.endswith("Available sheets: Q1, Q2, Annual Summary.")  # ours


def test_a_sheet_selection_error_carrying_no_sheet_list_says_so_instead_of_trailing_off():
    """If the payload ever loses its `sheets` key the message must not read
    "Available sheets: ." — an empty list looks like "there are no sheets",
    which is a different and untrue statement."""
    out = explain(problem(400, "This version has 2 sheets.", "sheet-selection-required"))

    assert out.endswith("Available sheets: unknown.")


def test_an_unknown_column_error_names_the_columns_that_do_exist():
    """A near-miss on a column name is the single most common tool failure, and
    it is fully recoverable from the schema — which the error already has in
    hand. Sending it back is the difference between one retry and a describe /
    re-read / retry cycle."""
    out = explain(unknown_column("revenu", ["order_id", "revenue", "region"]))

    assert "Unknown column: 'revenu'" in out
    assert "Available columns: order_id, revenue, region." in out
    assert "describe_dataset" in out          # for the schema this message cannot fit


def test_a_wide_sheets_column_list_is_capped_rather_than_flooding_the_response():
    """Real sheets run to hundreds of columns. Pasting all of them turns a
    recoverable error into a response that costs more than the query would
    have, so the list is capped — and the cap is announced, or the model would
    reasonably conclude column 41 does not exist."""
    out = explain(unknown_column("x", [f"col{i}" for i in range(100)]))

    assert "col0" in out and "col39" in out
    assert "col40" not in out
    assert "…" in out


def test_a_column_list_that_exactly_fits_the_cap_is_not_marked_as_truncated():
    """Off-by-one at the boundary would claim columns were withheld when the
    list is complete, sending the model to describe_dataset for nothing."""
    out = explain(unknown_column("x", [f"col{i}" for i in range(40)]))

    assert "col39" in out
    assert "…" not in out


def test_an_unknown_operator_error_carries_the_whole_valid_vocabulary():
    """The tool layer used to keep its own copy of the filter grammar and drifted
    from it. The service's rejection already knows every accepted operator, so
    the message quotes the service instead of a local mirror — and the mirror
    was deleted."""
    out = explain(
        problem(
            400,
            "Unknown filter operator: 'greater_than' on column 'amount'",
            "unknown-operator",
            op="greater_than",
            column="amount",
            available=sorted(KNOWN_OPS),
        )
    )

    assert "'greater_than'" in out and "'amount'" in out
    for operator in ("eq", "gte", "in", "not_between", "starts_with"):
        assert f"{operator}," in out or f" {operator}." in out
    # And the shape, because an unknown operator usually comes with a guessed
    # node shape.
    assert "'logic': 'and'|'or'" in out
    assert "'column': ..., 'op': ..., 'value': ..." in out


def test_the_operator_vocabulary_is_listed_in_full_at_todays_size():
    """The 100-name cap is a ceiling against a runaway payload, not a budget the
    real vocabulary is near. If a future grammar crosses it, the message starts
    hiding operators and this test should be the thing that says so."""
    assert len(KNOWN_OPS) <= 100
    out = explain(problem(400, "Unknown filter operator: 'x'", "unknown-operator",
                          available=sorted(KNOWN_OPS)))

    assert "…" not in out
    for operator in sorted(KNOWN_OPS):
        assert operator in out


def test_an_absurdly_long_operator_list_is_still_capped():
    """The ceiling is real: a payload this size would blow the response budget
    and the truncation must be visible, not silent."""
    out = explain(problem(400, "Unknown filter operator: 'x'", "unknown-operator",
                          available=[f"op{i}" for i in range(150)]))

    assert "op99" in out
    assert "op100" not in out
    assert "…" in out


def test_a_malformed_filter_node_echoes_the_keys_that_were_actually_sent():
    """The failure is nearly always a node that mixes a condition and a group.
    Echoing the keys back names the specific node in a nested tree; without them
    the model has to bisect its own filter to find which one was wrong."""
    out = explain(
        problem(
            400,
            "Malformed filter condition: mixes Filter fields with FilterGroup fields",
            "invalid-filter",
            keys=["column", "conditions", "op", "value"],
        )
    )

    assert "Keys sent: column, conditions, op, value." in out
    assert "never both, and never a bare scalar" in out


def test_a_malformed_filter_with_no_keys_omits_the_clause_rather_than_printing_an_empty_one():
    """`conditions: ["x"]` — a bare scalar — has no keys to report. "Keys sent: ."
    would read as "you sent no keys", which is not what happened."""
    out = explain(problem(400, "Malformed filter condition: expected an object, got str",
                          "invalid-filter"))

    assert "Keys sent" not in out
    assert "expected an object, got str" in out
    assert "never both, and never a bare scalar" in out


def test_an_operator_type_mismatch_names_the_columns_actual_type():
    """`contains` on a number is a modelling mistake, not a typo — retrying the
    same operator on the same column can never work. Naming the dtype tells the
    model to change the operator (or the column) rather than the spelling."""
    out = explain(
        problem(
            400,
            "Operator 'contains' requires a text column; 'amount' is DOUBLE",
            "operator-type-mismatch",
            column="amount", op="contains", dtype="DOUBLE",
        )
    )

    assert "'amount' is DOUBLE" in out
    assert "this column's type (DOUBLE)" in out
    assert "String operators need a text column" in out


def test_an_operator_type_mismatch_without_a_dtype_says_unknown_not_none():
    """A column whose dtype never got recorded must not render "(None)" — that
    reads like a real type and invites a retry against it."""
    out = explain(problem(400, "Operator mismatch", "operator-type-mismatch"))

    assert "(unknown)" in out
    assert "None" not in out


@pytest.mark.parametrize("code", ["select-only", "invalid-sql"])
def test_a_rejected_statement_states_exactly_what_run_sql_accepts(code):
    """Both rejections have the same fix and the same four common causes. The
    trailing semicolon in particular is invisible to a model re-reading its own
    SQL, and 'multiple statements' is what the parser calls it."""
    out = explain(problem(400, "Only SELECT statements are allowed, got INSERT", code))

    assert "exactly one SELECT statement" in out
    assert "trailing semicolon" in out
    assert "WITH ... SELECT" in out           # so a legal CTE is not abandoned too


def test_a_sql_timeout_says_narrow_the_query_rather_than_inviting_a_retry():
    """The default reaction to a timeout is to try again. It will time out
    again — the limit is on the work, not on luck — so the message has to name
    the three things that actually change the outcome."""
    out = explain(problem(400, "Query exceeded the 30s time limit", "sql-timeout"))

    assert "30s" in out
    assert "Add a filter" in out and "aggregate" in out


def test_a_version_too_large_for_sql_names_the_tools_that_can_still_read_it():
    """A hard "no" here is a dead end for the whole task; the data is readable,
    just not by materialising it. The message has to hand over the two tools
    that page and aggregate server-side."""
    out = explain(problem(413, "Version data exceeds the raw-SQL materialization limit",
                          "version-too-large-for-sql", size_bytes=9_000_000_000))

    assert "query_rows" in out and "aggregate" in out


def test_a_pivot_cardinality_error_states_the_limit_it_actually_hit():
    """The fix is to reduce distinct values, which is impossible to aim without
    the target. The service sends the limit; the message must use the sent one,
    not a hardcoded guess that drifts when the service is retuned."""
    out = explain(problem(400, "Pivot dimension 'city' has too many distinct values",
                          "too-many-pivot-columns", limit=25))

    assert "limit 25" in out
    assert "date_trunc" in out and "bin_count" in out     # the two ways to bucket


def test_the_pivot_limit_fallback_matches_the_service_constant():
    """If the payload ever arrives without `limit`, the number printed is a
    default in this file. A default that disagrees with the service is a
    confidently wrong instruction, so it is pinned to the real constant."""
    out = explain(problem(400, "Too many pivot columns", "too-many-pivot-columns"))

    assert f"limit {MAX_PIVOT_COLUMNS}" in out


def test_a_sheet_missing_from_a_version_is_reported_against_the_version():
    """Sheets come and go between versions. Saying "does not exist" without
    "in this version" sends the model to look for a different dataset when the
    right move is a different version — or the version it did not pin."""
    out = explain(problem(404, "The relationship's sheet is not present in version 4",
                          "sheet-not-in-version", version_number=4))

    assert "in version 4" in out
    assert "does not exist in this version of the dataset" in out


# ---------------------------------------------------------------------------
# explain — the branches keyed on HTTP status
# ---------------------------------------------------------------------------


def test_a_401_points_at_the_identity_header_rather_than_the_data():
    """401 from inside MCP never means "log in" — there is no login here. It
    means the X-User-Id this session forwards names no active user, which is a
    deployment fact the model cannot fix by rephrasing its request."""
    out = explain(problem(401, "Not authenticated", "unauthorized"))

    assert "X-User-Id" in out
    assert "active analytics-service user" in out


def test_a_403_says_the_membership_is_fine_and_the_permission_is_not():
    """403 and 404 are different diagnoses here: 403 means you are in the team
    but lack the right, 404 means you may not be in the team at all. Collapsing
    them would send a model to ask for the wrong thing."""
    out = explain(problem(403, "Insufficient permission", "forbidden"))

    assert "member of the owning team" in out
    assert "lack the required permission" in out


def test_a_404_refuses_to_claim_the_resource_is_absent():
    """404-hides-existence is a deliberate security property of this service: a
    dataset owned by another team is indistinguishable from one that never
    existed. A model told "not found" will report the dataset was deleted; it
    has to be told the other reading, or it will state a falsehood confidently."""
    out = explain(problem(404, "Dataset not found", "not_found"))

    assert "does not prove the resource is absent" in out
    assert "a team you are not in" in out


# ---------------------------------------------------------------------------
# explain — the 422 pydantic error array
#
# The service's detail for every 422 is the literal string "Request validation
# failed" (app/api/errors.py::_validation_exception_handler). Unrendered, that
# names neither the field nor the reason.
# ---------------------------------------------------------------------------


def test_a_422_renders_the_pydantic_array_into_a_field_and_a_reason():
    """Built from a real pydantic failure, not a canned dict: if the error entry
    shape changes (`loc`/`msg`), this must break rather than keep rendering a
    stale format the service no longer sends."""
    with pytest.raises(ValidationError) as raised:
        AggregateRequest(
            group_by=["region"],
            aggregations=[{"column": "amount", "function": "sum"}],
            sort_order="ASC",
        )
    errors = jsonable_encoder(raised.value.errors())

    out = explain(problem(422, "Request validation failed", "http-422", errors=errors))

    assert "sort_order: Input should be 'asc' or 'desc'" in out
    assert out.startswith("Request validation failed. ")


def test_a_422_drops_the_body_wrapper_from_the_field_path():
    """FastAPI prefixes every body error with "body". Every one of these tools
    sends a body, so the prefix distinguishes nothing and only makes the path
    harder to match against the argument the model actually passed."""
    out = explain(
        problem(422, "Request validation failed", "http-422",
                errors=[{"loc": ["body", "sort", 0, "direction"],
                         "msg": "Input should be 'asc' or 'desc'"}])
    )

    assert "sort.0.direction: Input should be 'asc' or 'desc'" in out
    assert "body" not in out


def test_a_422_names_every_failing_field_in_one_message():
    """One round trip per rejected field is a tax on the model with no
    diagnostic value — the service already found all of them."""
    out = explain(
        problem(422, "Request validation failed", "http-422",
                errors=[{"loc": ["body", "limit"], "msg": "Input should be <= 1000"},
                        {"loc": ["body", "filters", "conditions", 0, "value"],
                         "msg": "Input should be a valid list"}])
    )

    assert "limit: Input should be <= 1000" in out
    assert "filters.conditions.0.value: Input should be a valid list" in out


def test_a_422_error_with_no_location_is_labelled_request_not_left_blank():
    """A model-level (whole-body) validator has no `loc`. ": Value error, ..."
    with nothing in front of the colon reads like a rendering bug and gives the
    model no anchor at all."""
    out = explain(problem(422, "Request validation failed", "http-422",
                          errors=[{"msg": "Value error, group_by cannot be empty"}]))

    assert "request: Value error, group_by cannot be empty" in out


def test_a_422_entry_missing_its_message_still_says_something():
    """Half a rendered entry is worse than none — "sort_order: " invites the
    model to believe the field is fine."""
    out = explain(problem(422, "Request validation failed", "http-422",
                          errors=[{"loc": ["body", "sort_order"]}]))

    assert "sort_order: invalid" in out


@pytest.mark.parametrize(
    "errors", [None, [], ["not-a-dict", 7]],
    ids=["absent", "empty", "unparseable"],
)
def test_a_422_with_no_usable_error_array_falls_back_to_the_bare_detail(errors):
    """The detail is thin, but a dangling "Request validation failed. " with
    nothing after it looks like the tool lost the explanation it was given."""
    out = explain(problem(422, "Request validation failed", "http-422", errors=errors))

    assert out == "Request validation failed"


# ---------------------------------------------------------------------------
# explain — precedence, pass-through and fallback
# ---------------------------------------------------------------------------


def test_a_known_code_wins_over_the_status_it_arrived_with():
    """`sheet-not-in-version` is raised as a 404 (app/features/relationships/
    joins.py). If the status branches were checked first, the specific,
    actionable message would be replaced by the generic 404 disclaimer for
    every code the service happens to send with a 401/403/404."""
    out = explain(problem(404, "The sheet is not present in version 4", "sheet-not-in-version"))

    assert "does not exist in this version of the dataset" in out
    assert "does not prove the resource is absent" not in out


def test_a_400_bad_request_is_passed_through_without_boilerplate():
    """`bad_request` is what a route gets when it raises a plain 400 with no
    code, so its detail is already the whole explanation. Appending
    "(code: bad_request)" would add a token of pure noise to the most common
    hand-written error in the service."""
    out = explain(problem(400, "A tag name cannot contain '/'", "bad_request"))

    assert out == "A tag name cannot contain '/'"


def test_an_unrecognised_problem_still_surfaces_its_code():
    """The fallback's job is to not swallow the one machine-readable thing the
    response carried. A model that reports the code back gets a human to the
    raise site; "an error occurred" gets nobody anywhere."""
    out = explain(problem(409, "This dataset is locked by a running job", "dataset-locked"))

    assert out == "This dataset is locked by a running job (code: dataset-locked)"


def test_a_400_with_an_unhandled_code_falls_through_to_the_fallback():
    """`sql-error` deliberately has no branch of its own — look.py appends the
    real table names to it instead. That composition only works if explain
    returns the detail plus the code rather than swallowing it."""
    out = explain(problem(400, "Query failed: Table with name custmers does not exist",
                          "sql-error"))

    assert "custmers" in out
    assert "(code: sql-error)" in out


def test_a_client_side_timeout_reaches_the_model_as_a_timeout():
    """The client raises this itself, with status 0, when the service never
    answered — no status branch can match it. It must still come out naming the
    timeout rather than being mistaken for a service rejection."""
    exc = ProblemError(
        0,
        "The analytics service did not respond within 60s. Narrow the request and retry.",
        "timeout",
        {},
    )

    out = explain(exc)

    assert "did not respond within 60s" in out
    assert "(code: timeout)" in out


def test_a_500_is_not_dressed_up_as_something_the_caller_can_fix():
    """There is no guidance for a server fault, and inventing some ("try a
    smaller request") would send the model into a retry loop against a bug."""
    out = explain(problem(500, "An unexpected error occurred.", "internal_server_error"))

    assert out == "An unexpected error occurred. (code: internal_server_error)"


# ---------------------------------------------------------------------------
# The actionable payload survives the real wire
#
# Everything above hands `explain` a hand-built ProblemError. These raise the
# service's OWN exception at its OWN raise site, serialise it with the real
# problem+json handler and parse it with the real client parser. They are what
# fails if a raise site renames the key `explain` reads.
# ---------------------------------------------------------------------------


SCHEMA = [
    {"name": "Order Amount", "normalized_name": "order_amount", "dtype": "DOUBLE", "position": 0},
    {"name": "Region", "normalized_name": "region", "dtype": "VARCHAR", "position": 1},
]


def test_the_services_unknown_column_payload_still_carries_a_column_list():
    """`explain` reads `extra["available"]` (via ProblemError.available_columns).
    validate.py::_resolve is what puts it there. Renaming it on either side
    leaves the message reading "Available columns: ." — still a 400, still
    plausible-looking, and useless."""
    with pytest.raises(ProblemException) as raised:
        validate_spec(QuerySpec(columns=["revenu"]), SCHEMA)

    out = explain(from_the_wire(raised.value))

    assert "Available columns: order_amount, region." in out


def test_the_services_unknown_operator_payload_still_carries_the_vocabulary():
    """Same contract for `extra["available"]` on the Filter model's validator —
    which is the reason the client-side grammar mirror could be deleted."""
    with pytest.raises(ProblemException) as raised:
        Filter(column="amount", op="greater_than", value=5)

    out = explain(from_the_wire(raised.value))

    assert "Valid filter operators: between," in out
    assert "gte" in out and "not_in" in out


def test_the_services_operator_type_mismatch_payload_still_carries_the_dtype():
    """`extra["dtype"]` is the whole added value of this branch; without it the
    message degrades to "(unknown)" and repeats what detail already said."""
    spec = QuerySpec(
        filters=FilterGroup(
            logic="and",
            conditions=[Filter(column="order_amount", op="contains", value="x")],
        )
    )
    with pytest.raises(ProblemException) as raised:
        validate_spec(spec, SCHEMA)

    out = explain(from_the_wire(raised.value))

    assert "this column's type (DOUBLE)" in out


def test_the_services_invalid_filter_payload_still_carries_the_offending_keys():
    """`extra["keys"]` from _condition_kind. This is the node that mixes a
    condition with a group — the exact case the echo exists to localise."""
    with pytest.raises(ProblemException) as raised:
        FilterGroup(logic="and",
                    conditions=[{"column": "a", "op": "eq", "value": 1, "conditions": []}])

    out = explain(from_the_wire(raised.value))

    assert "Keys sent: column, conditions, op, value." in out


def test_a_route_raising_a_plain_400_lands_on_the_pass_through_branch():
    """The `bad_request` branch is keyed on a code no raise site writes by
    hand — errors.py derives it from the status. If that derivation changed,
    every hand-written 400 in the service would start growing a "(code: ...)"
    suffix, and this is the only thing that would notice."""
    parsed = from_the_wire(ProblemException(400, "A tag name cannot contain '/'"))

    assert parsed.code == "bad_request"
    assert explain(parsed) == "A tag name cannot contain '/'"


# ---------------------------------------------------------------------------
# require_sort_order
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("value", ["asc", "desc"])
def test_a_valid_sort_order_is_returned_unchanged(value):
    assert require_sort_order(value) == value


def test_an_omitted_sort_order_takes_the_callers_default():
    """The default is per-caller, not global: list_saved_objects wants newest
    first ("desc"). A shared default here would silently reverse it."""
    assert require_sort_order(None) == "asc"
    assert require_sort_order(None, default="desc") == "desc"


def test_an_uppercase_sort_order_is_rejected_rather_than_lowercased():
    """The interesting decision in this module. Accepting "ASC" here would be
    kind, and would give `sort_order` two different contracts across one tool
    surface — the aggregate and pivot request models are Literal["asc","desc"]
    and 422 on it. One parameter that means different things in different tools
    is exactly the confusion these tools exist to remove, and quietly rewriting
    a caller's input is the same class of behaviour this guard prevents."""
    with pytest.raises(ToolError) as raised:
        require_sort_order("ASC")

    assert require_sort_order.__doc__ and "does *not* lowercase" in require_sort_order.__doc__
    assert "'ASC'" in str(raised.value)


def test_the_rejection_says_what_is_valid_and_what_would_have_gone_wrong():
    """The consequence matters more than the rule: a sort order that is quietly
    coerced returns the WRONG END of the data — the ten worst rows presented as
    the ten best — and nothing downstream looks like an error."""
    with pytest.raises(ToolError) as raised:
        require_sort_order("descending")

    message = str(raised.value)
    assert "must be 'asc' or 'desc' (lowercase)" in message
    assert "'descending'" in message
    assert "silently reverse the result" in message


@pytest.mark.parametrize("value", ["", " asc", "ascending", "DESC", "Asc", "1"],
                         ids=["empty", "padded", "word", "upper", "title", "numeric"])
def test_every_near_miss_is_rejected_not_interpreted(value):
    """No stripping, no case folding, no aliases: each of these would otherwise
    have to be guessed at, and a wrong guess is undetectable in the output."""
    with pytest.raises(ToolError):
        require_sort_order(value)


def test_the_accepted_vocabulary_is_the_two_the_service_accepts():
    """SORT_ORDERS is the local copy of a service Literal. It exists in one
    place so the drift is a one-line fix rather than a hunt."""
    assert SORT_ORDERS == ("asc", "desc")


async def test_a_bad_sort_order_costs_no_service_call(client):
    """Reached through the real tool, not just called directly: the guard must
    fire before list_saved_objects starts paging the saved-object list, or a
    rejected call still burns the round trips."""
    tools = register_tools(context, client)

    with pytest.raises(ToolError) as raised:
        await tools["list_saved_objects"](dataset_id="ds-1", kind="view", sort_order="ASC")

    assert "must be 'asc' or 'desc' (lowercase)" in str(raised.value)
    assert client.calls == [], client.trace()


# ---------------------------------------------------------------------------
# clamp
# ---------------------------------------------------------------------------


def test_a_response_within_budget_is_untouched():
    """No marker, no reformatting — the common case must be byte-identical or
    every downstream assertion about tool output becomes budget-dependent."""
    assert clamp("small", 60_000) == "small"


def test_a_response_exactly_at_budget_is_not_marked_as_truncated():
    """Off-by-one here tells the model rows were withheld when none were, and
    it will page for data that does not exist."""
    text = "x" * 100
    assert clamp(text, 100) == text


def test_a_truncated_response_keeps_the_start_and_says_it_was_cut():
    """Silent truncation is the dangerous version: a model that reads a table
    with the last rows shaved off will report a total that is simply wrong. The
    kept part is the *start*, because that is where the header and the field
    block are."""
    out = clamp("y" * 500, 100)

    assert out.startswith("y" * 100)
    assert "y" * 101 not in out
    assert "[response truncated at 100 characters." in out


def test_the_budget_is_printed_with_thousands_separators():
    """These budgets are 60,000. "60000" invites a model to read it as a row
    count or a byte count; the grouped form reads as the character limit it is."""
    out = clamp("z" * 70_000, 60_000)

    assert "truncated at 60,000 characters" in out


def test_the_hint_tells_the_caller_which_knob_to_turn():
    """Knowing the response was cut is only half of it — the recovery differs
    per tool (lower `limit`, page with `offset`, narrow the SELECT), and the
    caller of clamp is the only one who knows which."""
    out = clamp("z" * 500, 100, hint="Lower `limit` or page with `offset`.")

    assert out.endswith("Lower `limit` or page with `offset`.]")


def test_a_truncation_marker_with_no_hint_carries_no_borrowed_advice():
    """An empty hint must not inherit another tool's suggestion."""
    out = clamp("z" * 500, 100)

    assert "Lower" not in out and "limit" not in out


# ---------------------------------------------------------------------------
# page_items
# ---------------------------------------------------------------------------


def test_page_items_unwraps_the_standard_list_envelope():
    """Every list route returns Page[T]; this is the one place that knows it, so
    no tool has to remember whether the key is `items`, `data` or `results`."""
    assert page_items(page([{"id": "a"}, {"id": "b"}], total=2)) == [{"id": "a"}, {"id": "b"}]


def test_an_empty_page_is_an_empty_list_not_a_failure():
    """"No datasets yet" is a normal answer, and a tool must be able to render
    it rather than raise."""
    assert page_items(page([])) == []


@pytest.mark.parametrize(
    "payload",
    [None, {}, {"items": None}, {"items": {"a": 1}}, [{"id": "a"}], "unexpected", 7],
    ids=["none-204", "no-items-key", "null-items", "items-not-a-list",
         "bare-array", "string", "number"],
)
def test_a_response_that_is_not_a_page_yields_no_items_instead_of_crashing(payload):
    """`None` is the real one: the client returns None for a 204 or an empty
    body, so any route that starts answering 204 would otherwise turn every
    caller into an AttributeError inside a tool — an unhandled crash, not a
    translated error. The rest are the same defence for a shape change."""
    assert page_items(payload) == []


# ---------------------------------------------------------------------------
# sheet_path
# ---------------------------------------------------------------------------


def test_naming_a_sheet_builds_the_sheet_scoped_path():
    assert (
        sheet_path("ds-1", 4, "Q1", "query")
        == "/datasets/ds-1/versions/4/sheets/Q1/query"
    )


def test_omitting_the_sheet_uses_the_route_that_resolves_it_server_side():
    """Not a client-side default: the version-level route auto-resolves a
    single-sheet version and raises sheet-selection-required for a multi-sheet
    one. Picking a sheet here would defeat that rule and answer confidently
    about an arbitrary sheet."""
    assert sheet_path("ds-1", 4, None, "query") == "/datasets/ds-1/versions/4/query"


def test_an_empty_sheet_name_is_treated_as_no_sheet():
    """A caller passing "" would otherwise build ".../sheets//query", which is a
    404 about a path instead of the sheet-selection guidance they need."""
    assert sheet_path("ds-1", 4, "", "query") == "/datasets/ds-1/versions/4/query"


@pytest.mark.parametrize("suffix", ["query", "aggregate", "columns/amount", "missing"])
def test_the_suffix_is_appended_verbatim_so_nested_routes_work(suffix):
    """compute.py builds "columns/{column}" through this — a helper that
    escaped or split the suffix would silently break the per-column routes."""
    assert sheet_path("ds-1", 2, "Q1", suffix).endswith(f"/sheets/Q1/{suffix}")


# ---------------------------------------------------------------------------
# resolve_version
# ---------------------------------------------------------------------------


async def test_an_explicit_version_is_honoured_without_asking_the_service(ctx, client):
    """Pinning a version is how a multi-step analysis stays consistent while
    someone uploads a new one mid-conversation. It must also cost nothing: an
    extra list call per tool call is a real tax at 27 tools."""
    assert await resolve_version(ctx, "ds-1", 7) == 7
    assert client.calls == []


async def test_version_zero_is_an_explicit_version_not_an_omission(ctx, client):
    """`if version:` instead of `if version is not None:` would silently upgrade
    version 0 to "newest ready" and answer about different data than was asked
    for."""
    assert await resolve_version(ctx, "ds-1", 0) == 0
    assert client.calls == []


async def test_no_version_resolves_to_the_newest_ready_one(ctx, client):
    """The list is newest-first, so the first ready entry is the latest readable
    version."""
    client.on_get("/datasets/ds-1/versions",
                  page([version(version_number=9), version(version_number=8)]))

    assert await resolve_version(ctx, "ds-1", None) == 9
    assert client.one_call_to("GET", "/datasets/ds-1/versions").path


async def test_a_version_still_processing_is_skipped_for_the_newest_ready_one():
    """The common race: an upload is in flight while the model is working.
    Reading the newest *row* would hit a version with no data behind it — a
    confusing error, or worse a partial answer — instead of the last good one."""
    client = FakeAnalyticsClient()
    client.on_get("/datasets/ds-1/versions", page([
        version(version_number=11, status="processing"),
        version(version_number=10, status="failed"),
        version(version_number=9, status="ready"),
    ]))

    assert await resolve_version(make_ctx(client), "ds-1", None) == 9


@pytest.mark.parametrize(
    "versions",
    [[], [version(version_number=3, status="processing")],
     [version(version_number=3, status="failed")], [{"version_number": 3}]],
    ids=["no-versions", "processing", "failed", "no-status-field"],
)
async def test_a_dataset_with_nothing_ready_is_an_explained_refusal(versions):
    """Never fall back to a non-ready version — its parquet may be absent or
    half-written. And the refusal has to name the two causes, because "no ready
    version" alone reads like a permanent property of the dataset when it is
    usually a wait."""
    client = FakeAnalyticsClient()
    client.on_get("/datasets/ds-1/versions", page(versions))

    with pytest.raises(ToolError) as raised:
        await resolve_version(make_ctx(client), "ds-1", None)

    message = str(raised.value)
    assert "no ready version" in message
    assert "still be processing" in message and "upload may have failed" in message


async def test_a_version_number_sent_as_a_string_is_used_as_a_number():
    """The value goes straight into a URL path. A quoted "9" would build
    /versions/9 anyway, but any arithmetic or comparison a caller does on the
    returned value would then be string comparison — 10 < 9."""
    client = FakeAnalyticsClient()
    client.on_get("/datasets/ds-1/versions",
                  page([{"status": "ready", "version_number": "9"}]))

    assert await resolve_version(make_ctx(client), "ds-1", None) == 9


async def test_a_failure_listing_versions_is_not_swallowed(ctx, client):
    """resolve_version deliberately does not catch: a 404 on the version list is
    the caller's answer (bad dataset id, or a team they are not in), and
    inventing "no ready version" would misdiagnose it."""
    client.on_get("/datasets/ds-1/versions", problem(404, "Dataset not found", "not_found"))

    with pytest.raises(ProblemError):
        await resolve_version(ctx, "ds-1", None)


# ---------------------------------------------------------------------------
# guard — and the two helpers wired together inside a real tool
# ---------------------------------------------------------------------------


async def test_guard_turns_a_service_problem_into_the_explained_message():
    """The decorator is the only thing standing between a raw ProblemError and
    the model. If it stopped translating, every tool would start reporting
    "Unknown column: 'revenu'" with no schema attached."""
    exc = unknown_column("revenu", ["revenue", "region"])

    @guard
    async def failing() -> str:
        raise exc

    with pytest.raises(ToolError) as raised:
        await failing()

    assert str(raised.value) == explain(exc)
    assert "Available columns: revenue, region." in str(raised.value)


async def test_the_original_problem_stays_attached_as_the_cause():
    """The translated text drops the status code. Chaining keeps the original on
    the traceback so a server-side log still shows whether this was a 403 or a
    422 — the message alone cannot tell an operator that."""
    exc = problem(403, "Insufficient permission", "forbidden")

    @guard
    async def failing() -> str:
        raise exc

    with pytest.raises(ToolError) as raised:
        await failing()

    assert raised.value.__cause__ is exc


async def test_a_successful_call_passes_straight_through():
    @guard
    async def fine(**kwargs) -> str:
        return f"ok {kwargs}"

    assert await fine(a=1) == "ok {'a': 1}"


async def test_a_bug_in_a_tool_body_is_not_disguised_as_service_guidance():
    """guard catches ProblemError and nothing else, on purpose. Swallowing a
    KeyError into an actionable-looking message would hand the model advice
    invented for a defect it cannot fix, and hide the traceback that identifies
    it."""
    @guard
    async def buggy() -> str:
        raise KeyError("items")

    with pytest.raises(KeyError):
        await buggy()


async def test_a_tool_error_raised_inside_is_not_re_wrapped():
    """require_sort_order and the tools' own argument checks raise ToolError
    directly. Re-wrapping would nest the message inside another error object,
    and the client shows only the outer one."""
    @guard
    async def refusing() -> str:
        raise ToolError("object_id needs kind — the id alone does not say which type.")

    with pytest.raises(ToolError) as raised:
        await refusing()

    assert str(raised.value) == "object_id needs kind — the id alone does not say which type."


async def test_guard_keeps_the_signature_the_mcp_schema_is_built_from(client):
    """functools.wraps is load-bearing, not tidiness: MCPServer derives each
    tool's published input schema from the decorated function's signature. Lose
    it and every guarded tool publishes (*args, **kwargs) — no argument names,
    no types, and UnknownArgumentGuard with no vocabulary to police."""
    tools = register_tools(look, client)
    published = {t.name: t for t in await tools.server.list_tools()}

    properties = published["query_rows"].input_schema["properties"]
    assert {"dataset_id", "sheet", "version", "limit"} <= set(properties)
    assert "kwargs" not in properties


async def test_resolve_version_and_sheet_path_compose_into_one_real_request(client):
    """The three helpers meeting inside a live tool: the newest ready version is
    found, the sheet-scoped path is built from it, and the version actually used
    is reported back — a model that cannot see which version it read cannot cite
    its own answer."""
    client.on_get("/datasets/ds-1/versions",
                  page([version(version_number=5, status="processing"),
                        version(version_number=4)]))
    client.on_post("/datasets/ds-1/versions/4/sheets/Q1/query",
                   query_page([{"region": "US", "amount": 10}], total=1))
    tools = register_tools(look, client)

    out = await tools["query_rows"](dataset_id="ds-1", sheet="Q1", limit=10)

    assert client.trace() == [
        ("GET", "/datasets/ds-1/versions"),
        ("POST", "/datasets/ds-1/versions/4/sheets/Q1/query"),
    ]
    assert "version: 4" in out


async def test_a_service_rejection_inside_a_real_tool_comes_back_explained(client):
    """End to end through the decorator on a real tool: the sheet names the
    service sent must survive rendering, not just explain() in isolation."""
    client.on_get("/datasets/ds-1/versions", page([version(version_number=4)]))
    client.on_post("/datasets/ds-1/versions/4/query",
                   sheet_selection_required(["Q1", "Q2"]))
    tools = register_tools(look, client)

    with pytest.raises(ToolError) as raised:
        await tools["query_rows"](dataset_id="ds-1")

    assert "Available sheets: Q1, Q2." in str(raised.value)


# ---------------------------------------------------------------------------
# Ctx
# ---------------------------------------------------------------------------


def test_the_shared_context_holds_no_identity():
    """One Ctx is captured by all 27 tool closures for the life of the process.
    If a user, token or team ever got cached on it, every caller after the first
    would be served as that first user — a cross-tenant data leak that no test
    of a single session would ever show. Identity is attached per request by
    app/features/mcp/identity.py instead."""
    fields = [f.name for f in dataclasses.fields(Ctx)]

    assert fields == ["client"]

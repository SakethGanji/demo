"""The write side of the MCP tool surface: ``app/features/mcp/tools/pipeline.py``.

Four tools — ``manage_relationships``, ``join_datasets``, ``transform_data``,
``publish_result`` — and every one of them creates something that outlives the
conversation. That makes two properties worth pinning hard:

1. **Which endpoint gets called, with which body.** A write tool that posts to
   the wrong path, or that sends an explicit ``null`` where the caller left a
   parameter out, does damage that no amount of good rendering undoes. The
   asserts here are on the fake client's recorded calls, so a path or body
   change is a test failure rather than a production surprise.
2. **What the failure tells the model.** These tools sit at the top of a ladder
   (relationship -> confirm -> join, create -> preview -> run) where every rung
   can refuse. A refusal that does not name the missing argument, the valid
   actions, the available columns or the sheet names costs a round trip *and*
   leaves the model guessing; several of the branches below exist purely to
   produce that one-step-recoverable message, and are asserted on their content.

Deliberately NOT covered here:

* That the service ever produces the responses scripted below. These are unit
  tests over the tool bodies with ``AnalyticsClient`` replaced (see
  ``tests/unit/mcp_harness.py``); ``tests/test_mcp_endpoint.py`` makes the
  end-to-end claim against real Postgres and real data.
* The pydantic argument model. Calling ``tools[name](...)`` runs the closure but
  bypasses the MCP arg model, so ``Field(ge=…, le=…)`` is not exercised — the
  two schema-level tests at the bottom go through ``tools.server`` instead.
* The prose of the tool *descriptions*, except where the description is the only
  place a model can learn a grammar it must produce (the transform step list).

The response bodies are built from the service's own response models
(``RelationshipOut``, ``JoinPreview``, ``TransformPreview``, ``PublishResponse``,
…) rather than hand-written dicts, so a renamed field breaks these builders
loudly instead of leaving the tests passing against a shape that no longer
exists.
"""

from __future__ import annotations

from typing import Any, Sequence

import pytest
from mcp.server.mcpserver.exceptions import ToolError

from app.features.data_accelerator.schemas import (
    ColumnDrift,
    ColumnProfile,
    ProfileResponse,
    SheetProfileDrift,
)
from app.features.library.schemas import PublishResponse
from app.features.mcp.tools import pipeline
from app.features.relationships.schemas import (
    JoinExecuteResponse,
    JoinPreview,
    JoinWarnings,
    RelationshipOut,
    SeedResponse,
    SuggestResponse,
)
from app.features.transform.schemas import (
    OutputColumn,
    TransformationOut,
    TransformationRunDetail,
    TransformPreview,
)
from mcp_harness import FakeAnalyticsClient, problem, register_tools, sheet_selection_required, unknown_column


# ---------------------------------------------------------------------------
# Response builders — each dumped from the route's declared response_model
# ---------------------------------------------------------------------------


def _dump(model: Any) -> dict[str, Any]:
    return model.model_dump(mode="json")


def relationship(
    *,
    id: str = "rel-1",
    dataset_id: str = "ds-1",
    from_sheet: str | None = "Orders",
    from_column: str = "customer_id",
    to_dataset_id: str | None = None,
    to_sheet: str | None = "Customers",
    to_column: str = "id",
    status: str = "suggested",
    method: str = "discovery",
    confidence: float | None = 0.92,
    evidence: dict[str, Any] | None = None,
    reviewed_by: str | None = None,
) -> dict[str, Any]:
    """One ``RelationshipOut`` — the row every relationship route returns."""
    return _dump(
        RelationshipOut(
            id=id,
            dataset_id=dataset_id,
            from_logical_sheet_id="ls-left",
            from_sheet=from_sheet,
            from_column=from_column,
            to_dataset_id=to_dataset_id or dataset_id,
            to_logical_sheet_id="ls-right",
            to_sheet=to_sheet,
            to_column=to_column,
            status=status,
            method=method,
            evidence=evidence or {},
            confidence=confidence,
            reviewed_by=reviewed_by,
            created_at="2026-01-01T00:00:00Z",
            updated_at="2026-01-02T00:00:00Z",
        )
    )


def suggest_response(
    rows: Sequence[dict[str, Any]] = (), *, pairs_examined: int = 0, suggested: int = 0
) -> dict[str, Any]:
    return _dump(
        SuggestResponse(
            pairs_examined=pairs_examined,
            suggested=suggested,
            relationships=[RelationshipOut(**r) for r in rows],
        )
    )


def seed_response(rows: Sequence[dict[str, Any]] = (), *, created: int = 0) -> dict[str, Any]:
    return _dump(
        SeedResponse(created=created, relationships=[RelationshipOut(**r) for r in rows])
    )


def warnings(**overrides: Any) -> JoinWarnings:
    """``JoinWarnings`` with a clean, unremarkable join as the baseline."""
    base: dict[str, Any] = {
        "left_rows": 100,
        "right_rows": 100,
        "left_duplicate_keys": 0,
        "right_duplicate_keys": 0,
        "many_to_many": False,
        "estimated_output_rows": 100,
        "row_expansion_factor": 1.0,
        "unmatched_left_pct": 0.0,
        "unmatched_right_pct": 0.0,
        "column_collisions": [],
    }
    base.update(overrides)
    return JoinWarnings(**base)


def join_preview(
    *,
    rows: Sequence[dict[str, Any]] = (),
    output_columns: Sequence[str] = ("order_id", "customer_name"),
    warn: JoinWarnings | None = None,
    rel: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return _dump(
        JoinPreview(
            warnings=warn or warnings(),
            output_columns=list(output_columns),
            preview=list(rows),
            relationship=RelationshipOut(**(rel or relationship(status="confirmed"))),
        )
    )


def join_execute(
    *,
    run_id: str = "run-9",
    sample_file: str = "join_output_9.parquet",
    row_count: int = 240,
    output_columns: Sequence[str] = ("order_id", "customer_name"),
    warn: JoinWarnings | None = None,
    rel: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return _dump(
        JoinExecuteResponse(
            run_id=run_id,
            sample_file=sample_file,
            row_count=row_count,
            warnings=warn or warnings(),
            output_columns=list(output_columns),
            relationship=RelationshipOut(**(rel or relationship(status="confirmed"))),
        )
    )


def transformation(
    *,
    id: str = "def-1",
    name: str = "clean orders",
    sheet_key: str | None = "orders",
    steps: Sequence[dict[str, Any]] = (),
    version_selector: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return _dump(
        TransformationOut(
            id=id,
            dataset_id="ds-1",
            logical_sheet_id="ls-1",
            sheet_key=sheet_key,
            name=name,
            version_selector=version_selector or {"mode": "current"},
            steps=list(steps),
            created_at="2026-01-01T00:00:00Z",
            updated_at="2026-01-01T00:00:00Z",
        )
    )


def transform_preview(
    *,
    rows: Sequence[dict[str, Any]] = (),
    columns: Sequence[str] = ("order_id", "line_total"),
    schema: Sequence[tuple[str, str, str]] = (),
    approximate: bool = True,
    version_number: int = 4,
    sheet_name: str = "Orders",
) -> dict[str, Any]:
    """``TransformPreview``. ``schema`` is (name, normalized_name, dtype) triples."""
    return _dump(
        TransformPreview(
            columns=list(columns),
            rows=list(rows),
            approximate=approximate,
            output_schema=[
                OutputColumn(name=n, normalized_name=norm, dtype=dtype, position=i)
                for i, (n, norm, dtype) in enumerate(schema)
            ],
            version_number=version_number,
            sheet_name=sheet_name,
        )
    )


def transform_run(
    *,
    id: str = "trun-1",
    status: str = "completed",
    mode: str = "sync",
    summary: dict[str, Any] | None = None,
    artifact_id: str | None = "art-1",
    error: str | None = None,
    profile: dict[str, Any] | None = None,
    drift: dict[str, Any] | None = None,
    completed_at: str | None = "2026-01-01T00:05:00Z",
) -> dict[str, Any]:
    return _dump(
        TransformationRunDetail(
            id=id,
            definition_id="def-1",
            status=status,
            mode=mode,
            result_summary=summary,
            artifact_id=artifact_id,
            started_at="2026-01-01T00:00:00Z",
            completed_at=completed_at,
            error=error,
            output_profile=profile,
            source_drift=drift,
        )
    )


def run_summary(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "sample_file": "transform_output_1.parquet",
        "source_row_count": 1000,
        "row_count": 940,
        "column_count": 2,
        "step_count": 3,
        "output_columns": ["order_id", "line_total"],
        "sheet": "Orders",
        "version_number": 4,
    }
    base.update(overrides)
    return base


def output_profile(*columns: dict[str, Any]) -> dict[str, Any]:
    """A ``ProfileResponse`` dump, as ``_profile_output`` stores it on the run."""
    listed = [
        ColumnProfile(
            name=c["name"],
            dtype=c.get("dtype", "numeric"),
            count=c.get("count", 100),
            null_count=c.get("null_count", 0),
            null_percent=c.get("null_percent", 0.0),
            unique_count=c.get("unique_count", 100),
            top_values=[],
            mean=c.get("mean"),
            min=c.get("min"),
            max=c.get("max"),
        )
        for c in columns
    ]
    return _dump(
        ProfileResponse(
            success=True,
            row_count=100,
            column_count=len(listed),
            columns=listed,
            memory_usage_bytes=1024,
            duplicate_row_count=0,
        )
    )


def source_drift(
    *columns: dict[str, Any],
    from_row_count: int = 1000,
    to_row_count: int = 940,
    duplicate_rows_delta: int | None = None,
    added_columns: Sequence[str] = (),
    removed_columns: Sequence[str] = (),
) -> dict[str, Any]:
    """A ``SheetProfileDrift`` dump, as ``_profile_output`` stores it."""
    drift = _dump(
        SheetProfileDrift(
            sheet_key="orders",
            from_row_count=from_row_count,
            to_row_count=to_row_count,
            row_count_delta=to_row_count - from_row_count,
            duplicate_rows_delta=duplicate_rows_delta,
            columns=[ColumnDrift(**c) for c in columns],
            added_columns=list(added_columns),
            removed_columns=list(removed_columns),
        )
    )
    drift["source"] = "profile_run"
    return drift


def publish_response(
    *,
    dataset_id: str = "ds-new",
    dataset_name: str = "Orders enriched",
    version_id: str = "v-1",
    version_number: int = 1,
    mode: str = "new_dataset",
) -> dict[str, Any]:
    return _dump(
        PublishResponse(
            dataset_id=dataset_id,
            dataset_name=dataset_name,
            version_id=version_id,
            version_number=version_number,
            mode=mode,
        )
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def client() -> FakeAnalyticsClient:
    return FakeAnalyticsClient()


@pytest.fixture
def tools(client):
    return register_tools(pipeline, client)


# ===========================================================================
# manage_relationships
# ===========================================================================


async def test_suggesting_relationships_runs_in_request_rather_than_enqueuing_a_job(
    tools, client
):
    """``sync=true`` is what makes 'suggest' usable in a conversation: without it
    the route dispatches to the worker and returns a job_id, and the model would
    have to poll something this tool surface gives it no way to poll. The flag
    lives in the query string, not the body — a body field would be silently
    ignored by FastAPI and the tool would go async without saying so."""
    client.on_post(
        "/datasets/ds-1/relationships/suggest",
        suggest_response([relationship()], pairs_examined=18, suggested=1),
    )
    await tools["manage_relationships"](action="suggest", dataset_id="ds-1")

    call = client.one_call_to("POST", "/datasets/ds-1/relationships/suggest")
    assert call.params == {"sync": True}
    assert call.body == {}


async def test_suggest_returns_the_relationship_id_needed_to_confirm_each_edge(
    tools, client
):
    """A suggestion the model cannot address is inert: 'confirm' takes a
    relationship_id and there is no other route that lists suggestions, so this
    response is the only place the id can come from. Dropping it would strand
    every discovered edge in a state no tool can move it out of."""
    client.on_post(
        "/datasets/ds-1/relationships/suggest",
        suggest_response(
            [relationship(id="rel-abc", from_column="customer_id", to_column="id")],
            pairs_examined=18,
            suggested=1,
        ),
    )
    out = await tools["manage_relationships"](action="suggest", dataset_id="ds-1")

    assert "rel-abc" in out
    # The endpoints are rendered as sheet.column so the pairing can be judged
    # without a second describe_dataset call.
    assert "Orders.customer_id" in out
    assert "Customers.id" in out
    assert "0.92" in out                      # confidence, to triage by
    assert "column_pairs_examined: 18" in out
    assert "new_suggestions: 1" in out


async def test_a_suggestion_says_it_cannot_drive_a_join_until_it_is_confirmed(
    tools, client
):
    """Only a CONFIRMED edge may drive a join — an unconfirmed one is a 409 from
    /joins/execute. Suggest is the tool that produces unconfirmed edges, so if it
    does not say so here the model learns the rule by burning a join call."""
    client.on_post(
        "/datasets/ds-1/relationships/suggest",
        suggest_response([relationship()], pairs_examined=4, suggested=1),
    )
    out = await tools["manage_relationships"](action="suggest", dataset_id="ds-1")

    assert "Nothing here can drive a join yet" in out
    assert "action='confirm'" in out
    assert "a rejected edge is not proposed again" in out


async def test_an_edge_pointing_at_another_dataset_is_flagged_cross_dataset(
    tools, client
):
    """Cross-dataset is the whole reason join_datasets exists (run_sql cannot
    reach across datasets), and it is also the case that needs read permission on
    a second dataset. Two sheet names alone do not reveal which case this is."""
    client.on_post(
        "/datasets/ds-1/relationships/suggest",
        suggest_response(
            [
                relationship(id="same", to_dataset_id="ds-1"),
                relationship(id="other", to_dataset_id="ds-2"),
            ],
            pairs_examined=2,
            suggested=2,
        ),
    )
    out = await tools["manage_relationships"](action="suggest", dataset_id="ds-1")

    same = next(line for line in out.splitlines() if line.startswith("same |"))
    other = next(line for line in out.splitlines() if line.startswith("other |"))
    assert "| false |" in same
    assert "| true |" in other


async def test_seeding_does_not_ask_for_the_sync_query_parameter(tools, client):
    """/relationships/seed has no ``sync`` parameter — it is always in-request.
    Sending one would be dropped by FastAPI today, but the pairing of "this route
    takes sync, that one does not" is exactly the kind of thing a copy-paste
    breaks, and the failure mode (a background job nobody polls) is silent."""
    client.on_post("/datasets/ds-1/relationships/seed", seed_response(created=0))
    await tools["manage_relationships"](action="seed", dataset_id="ds-1")

    call = client.one_call_to("POST", "/datasets/ds-1/relationships/seed")
    assert call.params == {}
    assert call.body == {}


async def test_seeding_nothing_explains_why_instead_of_returning_an_empty_table(
    tools, client
):
    """Seeding reads the dataset's foreign_key quality rules. With none enabled
    the honest answer is an empty list, which reads as "this dataset has no
    relationships" — the opposite of the truth. The tool has to name the
    precondition and the two actions that work without it."""
    client.on_post("/datasets/ds-1/relationships/seed", seed_response(created=0))
    out = await tools["manage_relationships"](action="seed", dataset_id="ds-1")

    assert "created_or_refreshed: 0" in out
    assert "(no rows)" in out
    assert "no enabled foreign_key quality rules" in out
    assert "action='suggest'" in out and "action='declare'" in out


async def test_reseeding_promises_it_will_not_overturn_a_human_decision(tools, client):
    """Seeding is idempotent and never overrides a status someone already set.
    A model that believes otherwise will avoid re-seeding after a review, or will
    re-seed expecting a reset — both wrong."""
    client.on_post(
        "/datasets/ds-1/relationships/seed",
        seed_response([relationship(method="fk_rule")], created=1),
    )
    out = await tools["manage_relationships"](action="seed", dataset_id="ds-1")

    assert "idempotent" in out
    assert "never overrides a status someone has already set" in out
    assert "confirm one before joining on it" in out


@pytest.mark.parametrize("missing", ["from_column", "to_column"])
async def test_declaring_without_both_endpoints_names_the_parameter_and_the_branch(
    tools, client, missing
):
    """These are optional in the tool signature (four of the five actions do not
    want them) so the arg model cannot enforce them. Left to the service it is a
    422 on a request that never needed to be sent; caught here the message names
    the parameter AND the action that requires it, which is the pair a model
    needs to retry in one step."""
    kwargs = {"from_column": "customer_id", "to_column": "id"}
    kwargs.pop(missing)
    with pytest.raises(ToolError) as excinfo:
        await tools["manage_relationships"](
            action="declare", dataset_id="ds-1", **kwargs
        )

    assert str(excinfo.value) == f"{missing} is required when action='declare'."
    # Nothing was written: the point of validating before the request.
    assert client.calls == []


async def test_declaring_omits_the_optional_endpoints_rather_than_sending_null(
    tools, client
):
    """``to_dataset_id`` defaults to the owning dataset and the sheets default to
    the version's only sheet — but only if the keys are ABSENT. An explicit null
    is a different request, and on a single-sheet dataset it is the difference
    between a working declaration and a resolution failure."""
    client.on_post("/datasets/ds-1/relationships", relationship(status="confirmed"))
    await tools["manage_relationships"](
        action="declare", dataset_id="ds-1", from_column="customer_id", to_column="id"
    )

    call = client.one_call_to("POST", "/datasets/ds-1/relationships")
    assert call.body == {"from_column": "customer_id", "to_column": "id", "confirmed": True}


async def test_declaring_across_datasets_and_sheets_sends_every_endpoint_field(
    tools, client
):
    """The cross-dataset, multi-sheet case is the one where all four locator
    fields matter; dropping any of them silently declares a different edge."""
    client.on_post("/datasets/ds-1/relationships", relationship(status="confirmed"))
    await tools["manage_relationships"](
        action="declare",
        dataset_id="ds-1",
        from_sheet="Orders",
        from_column="customer_id",
        to_dataset_id="ds-2",
        to_sheet="Customers",
        to_column="id",
        confirmed=False,
    )

    assert client.one_call_to("POST", "/datasets/ds-1/relationships").body == {
        "from_sheet": "Orders",
        "from_column": "customer_id",
        "to_dataset_id": "ds-2",
        "to_sheet": "Customers",
        "to_column": "id",
        "confirmed": False,
    }


async def test_a_declared_edge_that_is_not_confirmed_says_a_join_will_refuse_it(
    tools, client
):
    """``confirmed=False`` produces an edge that looks declared but cannot drive
    a join. The status line is the service's, not an echo of the argument, so
    this also catches the case where the service declines to confirm."""
    client.on_post("/datasets/ds-1/relationships", relationship(status="suggested"))
    out = await tools["manage_relationships"](
        action="declare",
        dataset_id="ds-1",
        from_column="customer_id",
        to_column="id",
        confirmed=False,
    )

    assert "a join will refuse it" in out
    assert "action='confirm'" in out


async def test_a_confirmed_declaration_says_the_edge_is_usable_now(tools, client):
    """The default path. If this said "confirm it first" the model would burn a
    call re-confirming something already confirmed."""
    client.on_post("/datasets/ds-1/relationships", relationship(status="confirmed"))
    out = await tools["manage_relationships"](
        action="declare", dataset_id="ds-1", from_column="customer_id", to_column="id"
    )

    assert "join_datasets can use this relationship_id now" in out
    assert "a join will refuse it" not in out


async def test_a_declaration_says_the_columns_shown_are_the_normalized_ones(
    tools, client
):
    """The service normalizes column names on the way in, so the edge may not
    read back as it was written ("Customer ID" -> "customer_id"). Unannounced,
    that looks like the wrong column was linked."""
    client.on_post(
        "/datasets/ds-1/relationships",
        relationship(status="confirmed", from_column="customer_id"),
    )
    out = await tools["manage_relationships"](
        action="declare", dataset_id="ds-1", from_column="Customer ID", to_column="id"
    )

    assert "normalized names the service resolved to" in out
    assert "Orders.customer_id" in out


@pytest.mark.parametrize("verb", ["confirm", "reject"])
async def test_reviewing_without_a_relationship_id_names_the_action_that_needs_it(
    tools, client, verb
):
    """Same class as the declare guard: an optional-in-signature parameter that
    is mandatory for two of the five actions. The message has to name which."""
    with pytest.raises(ToolError) as excinfo:
        await tools["manage_relationships"](action=verb, dataset_id="ds-1")

    assert str(excinfo.value) == f"relationship_id is required when action='{verb}'."
    assert client.calls == []


async def test_confirming_posts_to_the_review_route_and_reports_the_evidence(
    tools, client
):
    """Confirm and reject are distinct routes built from the verb; a mix-up would
    reject what the caller confirmed, with a success message either way. The
    evidence is what makes a confirmation reviewable after the fact — coverage
    and target uniqueness are the numbers that say whether the edge is real."""
    client.on_post(
        "/datasets/ds-1/relationships/rel-1/confirm",
        relationship(
            status="confirmed",
            reviewed_by="u-7",
            evidence={
                "coverage": 0.98,
                "target_uniqueness": 1.0,
                "name_score": 0.8,
                "matched_distinct": 480,
                "child_distinct": 490,
                "rule_name": "orders_fk_customers",
            },
        ),
    )
    out = await tools["manage_relationships"](
        action="confirm", dataset_id="ds-1", relationship_id="rel-1"
    )

    call = client.one_call_to("POST", "/datasets/ds-1/relationships/rel-1/confirm")
    assert call.body == {}
    assert "Relationship confirmed:" in out
    assert "coverage: 0.98" in out
    assert "target_uniqueness: 1" in out
    assert "matched_distinct: 480" in out
    assert "rule_name: orders_fk_customers" in out
    assert "reviewed_by: u-7" in out
    assert "pass its relationship_id to join_datasets" in out


async def test_rejecting_says_the_edge_is_turned_down_not_deleted(tools, client):
    """There is deliberately no delete on this surface. A model told only that
    the edge is "rejected" may go looking for a delete tool, or may believe the
    pairing is gone for good when it can be confirmed later."""
    client.on_post(
        "/datasets/ds-1/relationships/rel-1/reject", relationship(status="rejected")
    )
    out = await tools["manage_relationships"](
        action="reject", dataset_id="ds-1", relationship_id="rel-1"
    )

    client.one_call_to("POST", "/datasets/ds-1/relationships/rel-1/reject")
    assert "Relationship rejected:" in out
    assert "not propose this pairing again" in out
    assert "turned down, not deleted" in out
    assert "can be confirmed later" in out


async def test_an_unknown_relationship_action_lists_all_five_and_writes_nothing(
    tools, client
):
    """This tool multiplexes five verbs onto one name, so a wrong verb is the
    most likely mistake a model makes with it. Echoing what was sent alongside
    the whole vocabulary is what makes the retry a single step."""
    with pytest.raises(ToolError) as excinfo:
        await tools["manage_relationships"](action="approve", dataset_id="ds-1")

    message = str(excinfo.value)
    assert "action must be one of suggest, seed, declare, confirm, reject" in message
    assert "got 'approve'" in message
    assert client.calls == []


async def test_the_action_verb_tolerates_case_and_stray_whitespace(tools, client):
    """The verb is also a URL segment for confirm/reject. Normalizing it means a
    ``"Confirm"`` from a model does not become a request to /Confirm, which the
    router answers with a 404 that reads as "no such relationship"."""
    client.on_post(
        "/datasets/ds-1/relationships/rel-1/confirm", relationship(status="confirmed")
    )
    await tools["manage_relationships"](
        action="  CONFIRM ", dataset_id="ds-1", relationship_id="rel-1"
    )

    assert client.trace() == [("POST", "/datasets/ds-1/relationships/rel-1/confirm")]


async def test_declaring_an_edge_onto_a_column_that_does_not_exist_lists_the_columns(
    tools, client
):
    """Both endpoints are validated against the live schema at declare time, so
    a typo comes back as unknown-column. The available-column list is the whole
    product of that translation — without it the model guesses again."""
    client.on_post(
        "/datasets/ds-1/relationships",
        unknown_column("custmer_id", ["order_id", "customer_id", "total"]),
    )
    with pytest.raises(ToolError) as excinfo:
        await tools["manage_relationships"](
            action="declare", dataset_id="ds-1", from_column="custmer_id", to_column="id"
        )

    message = str(excinfo.value)
    assert "Unknown column: 'custmer_id'" in message
    assert "Available columns: order_id, customer_id, total" in message


async def test_declaring_on_a_multi_sheet_version_comes_back_with_the_sheet_names(
    tools, client
):
    """from_sheet/to_sheet are optional and resolve automatically on a
    single-sheet version. On a workbook the service refuses — and the names it
    refuses with are exactly the values the retry needs."""
    client.on_post(
        "/datasets/ds-1/relationships",
        sheet_selection_required(["Orders", "Customers", "Products"]),
    )
    with pytest.raises(ToolError) as excinfo:
        await tools["manage_relationships"](
            action="declare", dataset_id="ds-1", from_column="customer_id", to_column="id"
        )

    assert "Available sheets: Orders, Customers, Products." in str(excinfo.value)


# ===========================================================================
# join_datasets
# ===========================================================================


async def test_a_join_preview_sends_only_the_arguments_that_were_given(tools, client):
    """left_version/right_version omitted must mean "current version of each
    side", which is the absence of the key. Sending explicit nulls, or sending
    ``select_columns: null``, is a different request body and a 422 risk on a
    route whose spec model has no nullable-vs-absent equivalence to lean on."""
    client.on_post("/joins/preview", join_preview())
    await tools["join_datasets"](action="preview", relationship_id="rel-1")

    call = client.one_call_to("POST", "/joins/preview")
    assert call.body == {"relationship_id": "rel-1", "how": "inner"}
    assert call.params == {}


async def test_a_join_preview_forwards_pinned_versions_and_the_projection(tools, client):
    """The four optional spec fields are the difference between previewing what
    you asked for and previewing something else that also succeeds."""
    client.on_post("/joins/preview", join_preview())
    await tools["join_datasets"](
        action="preview",
        relationship_id="rel-1",
        how="left",
        left_version=2,
        right_version=7,
        select_columns=["order_id", "customer_name"],
    )

    assert client.one_call_to("POST", "/joins/preview").body == {
        "relationship_id": "rel-1",
        "how": "left",
        "left_version": 2,
        "right_version": 7,
        "select_columns": ["order_id", "customer_name"],
    }


async def test_a_preview_states_that_nothing_was_written(tools, client):
    """The tool's own description tells the model to always preview first. That
    advice is only free if the preview is unambiguously a measurement — a model
    that suspects it materialized something will skip the rehearsal."""
    client.on_post("/joins/preview", join_preview(rows=[{"order_id": 1}]))
    out = await tools["join_datasets"](action="preview", relationship_id="rel-1")

    assert "Nothing was written" in out
    assert "measurement only" in out
    assert "action='execute'" in out


async def test_a_preview_reports_every_measured_number_before_the_expensive_step(
    tools, client
):
    """These nine numbers are the entire reason preview exists. Any one of them
    dropped from the rendering makes the pre-flight look clean on a join that is
    not, and the cost lands on a later SUM rather than here."""
    client.on_post(
        "/joins/preview",
        join_preview(
            warn=warnings(
                left_rows=1000,
                right_rows=250,
                left_duplicate_keys=12,
                right_duplicate_keys=3,
                estimated_output_rows=1180,
                row_expansion_factor=1.18,
                unmatched_left_pct=2.5,
                unmatched_right_pct=1.0,
            )
        ),
    )
    out = await tools["join_datasets"](action="preview", relationship_id="rel-1")

    assert "Pre-flight (inner join)" in out
    for line in (
        "left_rows: 1000",
        "right_rows: 250",
        "left_duplicate_keys: 12",
        "right_duplicate_keys: 3",
        "many_to_many: false",
        "estimated_output_rows: 1180",
        "row_expansion_factor: 1.18",
        "unmatched_left_pct: 2.5",
        "unmatched_right_pct: 1",
    ):
        assert line in out, line


async def test_a_many_to_many_join_is_called_out_as_double_counting_sums(tools, client):
    """The failure this whole tool is shaped around: both sides repeat the key,
    the output multiplies, and every later aggregate is quietly wrong. A number
    alone is not enough — the advice has to say what to do (aggregate or
    deduplicate one side) because the numbers look like success."""
    client.on_post(
        "/joins/preview",
        join_preview(
            warn=warnings(
                many_to_many=True, estimated_output_rows=900, row_expansion_factor=3.0
            )
        ),
    )
    out = await tools["join_datasets"](action="preview", relationship_id="rel-1")

    assert "MANY-TO-MANY" in out
    assert "Aggregate or deduplicate one side first" in out
    assert "Each left row becomes 3 output rows" in out
    assert "double-count" in out


async def test_an_unmatched_side_is_quantified_with_the_fix_that_keeps_the_rows(
    tools, client
):
    """An inner join dropping 40% of the left side is not an error anywhere in
    the stack — it is a correct join over a partial key. Naming the percentage
    and ``how='left'`` in the same sentence is the only thing standing between
    that and a confidently truncated answer."""
    client.on_post(
        "/joins/preview",
        join_preview(warn=warnings(unmatched_left_pct=40.0, unmatched_right_pct=12.5)),
    )
    out = await tools["join_datasets"](action="preview", relationship_id="rel-1")

    assert "40.0% of left rows have no match" in out
    assert "12.5% of right rows have no match" in out
    assert "An inner join drops them" in out
    assert "how='left' keeps the left side's" in out


async def test_colliding_column_names_are_listed_with_the_prefixing_rule(tools, client):
    """Both copies survive a collision and the right one is renamed. A model that
    does not know the rule will select ``amount`` and get the left side's,
    thinking it asked for the right's."""
    client.on_post(
        "/joins/preview",
        join_preview(warn=warnings(column_collisions=["amount", "created_at"])),
    )
    out = await tools["join_datasets"](action="preview", relationship_id="rel-1")

    assert "column_collisions: amount, created_at" in out
    assert "Colliding column names (amount, created_at) are kept from both sides" in out
    assert "prefixed with its sheet key" in out


async def test_an_unremarkable_join_gets_no_advice_paragraph_at_all(tools, client):
    """Advice on a clean join is noise that teaches the model to skim the block
    where the real warnings live. The thresholds are deliberate: expansion is
    only interesting ABOVE 1.5, unmatched only at 5% or more."""
    client.on_post(
        "/joins/preview",
        join_preview(
            warn=warnings(
                row_expansion_factor=1.5, unmatched_left_pct=4.9, unmatched_right_pct=4.9
            )
        ),
    )
    out = await tools["join_datasets"](action="preview", relationship_id="rel-1")

    assert "MANY-TO-MANY" not in out
    assert "output rows" not in out          # the row-expansion sentence
    assert "have no match" not in out
    assert "kept from both sides" not in out


async def test_the_preview_sample_is_rendered_under_the_service_output_columns(
    tools, client
):
    """The sample table's columns come from the join's declared output_columns,
    not from whatever keys the first sample row happens to have. A row with a
    null in a column would otherwise drop that column from the header and shift
    every value one place left."""
    client.on_post(
        "/joins/preview",
        join_preview(
            output_columns=["order_id", "customer_name", "amount"],
            rows=[{"order_id": 1, "amount": 10}, {"order_id": 2, "customer_name": "Ada"}],
        ),
    )
    out = await tools["join_datasets"](action="preview", relationship_id="rel-1")

    assert "## Output columns\norder_id, customer_name, amount" in out
    sample = out.split("## Sample\n")[1]
    assert sample.splitlines()[0] == "order_id | customer_name | amount"
    assert sample.splitlines()[1] == "1 |  | 10"
    assert sample.splitlines()[2] == "2 | Ada | "


async def test_executing_a_join_names_the_artifact_and_the_two_ways_on(tools, client):
    """The result is an artifact, not rows in the response — a model that does
    not learn ``sample_file`` here has materialized something it cannot read.
    run_id is the same for publish_result, and the source= it needs is 'join'."""
    client.on_post(
        "/joins/execute",
        join_execute(run_id="run-9", sample_file="join_output_9.parquet", row_count=240),
    )
    out = await tools["join_datasets"](
        action="execute", relationship_id="rel-1", how="left"
    )

    assert client.one_call_to("POST", "/joins/execute").body == {
        "relationship_id": "rel-1",
        "how": "left",
    }
    assert "run_id: run-9" in out
    assert "sample_file: join_output_9.parquet" in out
    assert "row_count: 240" in out
    assert "how: left" in out
    assert "columns: 2" in out
    assert "read_artifact" in out
    assert "publish_result with source='join'" in out


async def test_an_executed_join_repeats_the_measurements_it_actually_produced(
    tools, client
):
    """Execute reports its own warnings, not the preview's. Skipping them here
    would mean the only join a model ever sees measured is one it might not have
    run, and a preview taken against different versions can disagree."""
    client.on_post(
        "/joins/execute",
        join_execute(
            warn=warnings(
                many_to_many=True, row_expansion_factor=2.4, estimated_output_rows=240
            )
        ),
    )
    out = await tools["join_datasets"](action="execute", relationship_id="rel-1")

    assert "## Measured" in out
    assert "estimated_output_rows: 240" in out
    assert "MANY-TO-MANY" in out
    assert "Each left row becomes 2.4 output rows" in out


async def test_executing_on_an_unconfirmed_edge_surfaces_the_service_refusal(
    tools, client
):
    """Preview accepts a suggested edge, execute does not — a 409 the caller can
    only have hit by skipping the review step. The detail must survive the
    translation intact, because it is the only text that names the precondition."""
    client.on_post(
        "/joins/execute",
        problem(
            409,
            "This relationship has not been confirmed — only confirmed "
            "relationships can drive a join",
            "relationship-not-confirmed",
            current_status="suggested",
        ),
    )
    with pytest.raises(ToolError) as excinfo:
        await tools["join_datasets"](action="execute", relationship_id="rel-1")

    message = str(excinfo.value)
    assert "has not been confirmed" in message
    assert "only confirmed relationships can drive a join" in message
    assert "relationship-not-confirmed" in message


@pytest.mark.parametrize("how", ["outer", "INNER", "cross", ""])
async def test_an_unsupported_join_type_names_both_supported_ones(tools, client, how):
    """The service's spec model is Literal["inner","left"], so anything else is a
    422 whose rendering ("Input should be 'inner' or 'left'") arrives after a
    round trip. Catching it here costs nothing and says the same thing. Note the
    check is exact: 'INNER' is rejected rather than quietly rewritten, because a
    tool that rewrites its inputs is the behaviour this surface avoids."""
    with pytest.raises(ToolError) as excinfo:
        await tools["join_datasets"](
            action="preview", relationship_id="rel-1", how=how
        )

    assert f"how must be 'inner' or 'left', got {how!r}." == str(excinfo.value)
    assert client.calls == []


async def test_an_unknown_join_action_names_preview_and_execute(tools, client):
    """'run', 'materialize' and 'build' are all plausible guesses. None of them
    should reach a route, and the correction has to arrive with the message."""
    with pytest.raises(ToolError) as excinfo:
        await tools["join_datasets"](action="run", relationship_id="rel-1")

    assert "action must be one of preview, execute, got 'run'." == str(excinfo.value)
    assert client.calls == []


async def test_an_oversized_preview_is_truncated_with_the_way_to_shrink_it(
    tools, client
):
    """A join sample can be arbitrarily wide. The clamp keeps one tool response
    from swallowing the context window, and the hint has to name the parameter
    that fixes it — a bare "truncated" leaves the model to retry identically."""
    wide = [{"order_id": i, "customer_name": "x" * 70} for i in range(2000)]
    client.on_post(
        "/joins/preview",
        join_preview(rows=wide, output_columns=["order_id", "customer_name"]),
    )
    out = await tools["join_datasets"](action="preview", relationship_id="rel-1")

    assert "[response truncated at 60,000 characters." in out
    assert "Pass select_columns to narrow the sample." in out


# ===========================================================================
# transform_data
# ===========================================================================


async def test_creating_a_pipeline_without_a_name_says_which_action_needs_it(
    tools, client
):
    """Three of the four actions have no use for a name, so it cannot be
    required in the signature. The guard has to say which branch wants it."""
    with pytest.raises(ToolError) as excinfo:
        await tools["transform_data"](action="create", dataset_id="ds-1", steps=[])

    assert str(excinfo.value) == "name is required when action='create'."
    assert client.calls == []


async def test_omitting_steps_is_refused_while_an_empty_list_is_explicitly_legal(
    tools, client
):
    """These are different intents that a plain "required" message conflates:
    ``steps=None`` is a caller who forgot, ``steps=[]`` is a caller who means
    "copy the sheet". Refusing the first without describing the second invites a
    retry with a bogus placeholder step."""
    with pytest.raises(ToolError) as excinfo:
        await tools["transform_data"](action="create", dataset_id="ds-1", name="p")

    message = str(excinfo.value)
    assert "steps is required when action='create'" in message
    assert "An empty list is allowed" in message
    assert "copies the sheet unchanged" in message
    assert client.calls == []


async def test_an_empty_step_list_is_sent_rather_than_rejected(tools, client):
    """The other half of the contract above — if this raised, the message would
    be a lie and the "copy the sheet" pipeline unreachable."""
    client.on_post("/datasets/ds-1/transformations", transformation(steps=[]))
    await tools["transform_data"](
        action="create", dataset_id="ds-1", name="passthrough", steps=[]
    )

    assert client.one_call_to("POST", "/datasets/ds-1/transformations").body["steps"] == []


async def test_creating_a_pipeline_defaults_the_version_selector_to_current(
    tools, client
):
    """A saved pipeline re-runs later, so what it reads is a stored decision.
    Omitting the key would let the service default it, but the tool sends the
    explicit ``{"mode":"current"}`` — the difference matters the day the route's
    default changes, because a stored pipeline would start reading elsewhere."""
    client.on_post("/datasets/ds-1/transformations", transformation())
    await tools["transform_data"](
        action="create", dataset_id="ds-1", name="clean", steps=[{"type": "drop"}]
    )

    assert client.one_call_to("POST", "/datasets/ds-1/transformations").body == {
        "name": "clean",
        "version_selector": {"mode": "current"},
        "steps": [{"type": "drop"}],
    }


async def test_a_pinned_version_selector_and_description_are_forwarded_verbatim(
    tools, client
):
    """A pipeline pinned to a tag is how a scheduled transformation stays on
    'prod' rather than following whatever was uploaded last."""
    client.on_post("/datasets/ds-1/transformations", transformation())
    await tools["transform_data"](
        action="create",
        dataset_id="ds-1",
        name="clean",
        description="drop test rows",
        sheet="Orders",
        steps=[],
        version_selector={"mode": "tag", "tag": "prod"},
    )

    assert client.one_call_to("POST", "/datasets/ds-1/transformations").body == {
        "name": "clean",
        "description": "drop test rows",
        "sheet": "Orders",
        "version_selector": {"mode": "tag", "tag": "prod"},
        "steps": [],
    }


async def test_a_saved_pipeline_reports_the_definition_id_and_what_compiled(
    tools, client
):
    """definition_id is required by both preview and run and appears nowhere
    else. The step types are the service's stored copy, so a step the compiler
    folded or reordered shows up here rather than being assumed from the input."""
    client.on_post(
        "/datasets/ds-1/transformations",
        transformation(
            id="def-77",
            name="clean orders",
            sheet_key="orders",
            steps=[{"type": "compute"}, {"type": "filter"}, {"type": "sort"}],
        ),
    )
    out = await tools["transform_data"](
        action="create", dataset_id="ds-1", name="clean orders", steps=[{"type": "compute"}]
    )

    assert "definition_id: def-77" in out
    assert "sheet_key: orders" in out
    assert "steps: 3" in out
    assert "step_types: compute, filter, sort" in out
    assert "action='preview' before action='run'" in out


@pytest.mark.parametrize(
    "selector,expected",
    [
        ({"mode": "current"}, "runs_against: current version"),
        ({"mode": "tag", "tag": "prod"}, "runs_against: tag 'prod'"),
        ({"mode": "version", "version_number": 3}, "runs_against: version 3"),
        ({}, "runs_against: current version"),
    ],
    ids=["current", "tag", "pinned", "empty"],
)
async def test_a_saved_pipeline_says_in_prose_which_version_it_will_read(
    tools, client, selector, expected
):
    """A stored pipeline outlives the conversation that made it, so "what does
    this read next time" is the question a reader has. Echoing the raw selector
    dict makes the model parse JSON to answer it; an empty selector rendering as
    something other than "current version" would misstate the service default."""
    client.on_post(
        "/datasets/ds-1/transformations", transformation(version_selector=selector)
    )
    out = await tools["transform_data"](
        action="create", dataset_id="ds-1", name="clean", steps=[]
    )

    assert expected in out


async def test_a_step_naming_a_column_that_does_not_exist_fails_with_the_column_list(
    tools, client
):
    """Pipelines are compiled against the sheet schema AT CREATE TIME — that is
    the design promise, and it is only worth anything if the rejection carries
    the columns that do exist. Otherwise the model has traded a run-time failure
    for a create-time one with the same amount of guessing."""
    client.on_post(
        "/datasets/ds-1/transformations",
        unknown_column("unit_prise", ["order_id", "quantity", "unit_price"]),
    )
    with pytest.raises(ToolError) as excinfo:
        await tools["transform_data"](
            action="create",
            dataset_id="ds-1",
            name="line totals",
            steps=[{"type": "compute", "into": "t", "expression": {}}],
        )

    message = str(excinfo.value)
    assert "Unknown column: 'unit_prise'" in message
    assert "Available columns: order_id, quantity, unit_price" in message
    assert "describe_dataset" in message


async def test_creating_against_a_workbook_without_a_sheet_lists_the_sheets(
    tools, client
):
    """``sheet`` is optional because most versions have one. On a workbook the
    service refuses, and the sheet names it refuses with are the retry."""
    client.on_post(
        "/datasets/ds-1/transformations", sheet_selection_required(["Q1", "Q2", "Q3"])
    )
    with pytest.raises(ToolError) as excinfo:
        await tools["transform_data"](
            action="create", dataset_id="ds-1", name="clean", steps=[]
        )

    assert "Available sheets: Q1, Q2, Q3." in str(excinfo.value)


async def test_a_malformed_step_comes_back_with_the_field_that_was_wrong(tools, client):
    """The step grammar is large and nested, so a 422 is the likeliest create
    failure. Unrendered it reads "Request validation failed", which tells a model
    nothing about which of fifty steps was rejected."""
    client.on_post(
        "/datasets/ds-1/transformations",
        problem(
            422,
            "Request validation failed",
            "validation-error",
            errors=[
                {
                    "loc": ["body", "steps", 1, "count"],
                    "msg": "Input should be a valid integer",
                }
            ],
        ),
    )
    with pytest.raises(ToolError) as excinfo:
        await tools["transform_data"](
            action="create",
            dataset_id="ds-1",
            name="clean",
            steps=[{"type": "limit", "count": "ten"}],
        )

    assert "steps.1.count: Input should be a valid integer" in str(excinfo.value)


@pytest.mark.parametrize("verb", ["preview", "run"])
async def test_previewing_or_running_without_a_definition_id_names_the_action(
    tools, client, verb
):
    """Both are path segments. Left to the request they would produce
    ``/transformations/None/preview``, whose 404 reads as "no such pipeline"."""
    with pytest.raises(ToolError) as excinfo:
        await tools["transform_data"](action=verb, dataset_id="ds-1")

    assert str(excinfo.value) == f"definition_id is required when action='{verb}'."
    assert client.calls == []


async def test_the_preview_row_count_travels_as_a_query_parameter(tools, client):
    """``rows`` is a Query on the route, not a body field. In the body it would
    be silently ignored and every preview would return the default 50 — a
    sampling difference invisible in the output."""
    client.on_post(
        "/datasets/ds-1/transformations/def-1/preview", transform_preview()
    )
    await tools["transform_data"](
        action="preview", dataset_id="ds-1", definition_id="def-1", rows=25
    )

    call = client.one_call_to("POST", "/datasets/ds-1/transformations/def-1/preview")
    assert call.params == {"rows": 25}
    assert call.body == {}


async def test_a_preview_marks_its_rows_approximate_so_counts_are_not_believed(
    tools, client
):
    """Preview samples the source instead of scanning it. Rows that look like a
    result but describe a sample are the most dangerous thing this tool returns:
    a filter on a rare value can come back empty from a pipeline that works."""
    client.on_post(
        "/datasets/ds-1/transformations/def-1/preview",
        transform_preview(rows=[{"order_id": 1, "line_total": 20.5}], approximate=True),
    )
    out = await tools["transform_data"](
        action="preview", dataset_id="ds-1", definition_id="def-1"
    )

    assert "nothing was written" in out
    assert "no run was recorded" in out
    assert "SAMPLE of the source" in out
    assert "rare values here do not describe the whole sheet" in out
    assert "action='run' for the real thing" in out


async def test_an_exact_preview_does_not_carry_the_sampling_warning(tools, client):
    """When the service says the preview scanned everything, repeating the
    approximation caveat would train the model to discount numbers that are
    exact — and to re-run a pipeline it did not need to run."""
    client.on_post(
        "/datasets/ds-1/transformations/def-1/preview",
        transform_preview(rows=[{"order_id": 1}], approximate=False),
    )
    out = await tools["transform_data"](
        action="preview", dataset_id="ds-1", definition_id="def-1"
    )

    assert "Dry run: nothing was written." in out
    assert "SAMPLE" not in out
    assert "approximate" not in out


async def test_a_preview_shows_the_output_schema_under_its_normalized_names(
    tools, client
):
    """A computed column is renamed on the way in ("Line Total" ->
    "line_total"), and the normalized name is what every later filter, sort and
    select must use. Showing the pretty name would send the model back to a
    column the compiled pipeline does not have."""
    client.on_post(
        "/datasets/ds-1/transformations/def-1/preview",
        transform_preview(
            columns=["order_id", "line_total"],
            schema=[("order_id", "order_id", "BIGINT"), ("Line Total", "line_total", "DOUBLE")],
            rows=[{"order_id": 1, "line_total": 20.5}],
        ),
    )
    out = await tools["transform_data"](
        action="preview", dataset_id="ds-1", definition_id="def-1"
    )

    schema_block = out.split("## Output schema\n")[1].split("\n\n")[0]
    assert schema_block.splitlines()[0] == "column | dtype"
    assert "line_total | DOUBLE" in schema_block
    assert "Line Total" not in schema_block
    assert "sheet: Orders" in out
    assert "version: 4" in out
    assert "output_columns: 2" in out


async def test_a_preview_that_produces_no_rows_still_reports_the_schema_it_would_have(
    tools, client
):
    """A pipeline whose filter matches nothing in the SAMPLE is the normal
    confusing case. An empty response with no schema and no row count reads as a
    broken pipeline; the schema proves it compiled and ran."""
    client.on_post(
        "/datasets/ds-1/transformations/def-1/preview",
        transform_preview(
            columns=["order_id"], schema=[("order_id", "order_id", "BIGINT")], rows=[]
        ),
    )
    out = await tools["transform_data"](
        action="preview", dataset_id="ds-1", definition_id="def-1"
    )

    assert "order_id | BIGINT" in out
    assert "## Rows\n(no rows)" in out
    assert "0 rows shown." in out


async def test_an_oversized_preview_is_truncated_with_the_two_ways_to_shrink_it(
    tools, client
):
    """The clamp's hint names the caller's own parameter and the pipeline change
    that fixes it — the two things a model can actually do next."""
    wide = [{"order_id": i, "note": "y" * 70} for i in range(2000)]
    client.on_post(
        "/datasets/ds-1/transformations/def-1/preview",
        transform_preview(columns=["order_id", "note"], rows=wide),
    )
    out = await tools["transform_data"](
        action="preview", dataset_id="ds-1", definition_id="def-1"
    )

    assert "[response truncated at 60,000 characters." in out
    assert "Lower `rows`, or add a select step to the pipeline." in out


async def test_a_run_executes_in_request_rather_than_enqueuing_a_job(tools, client):
    """``sync=false`` returns a run still ``running`` for a worker to finish, and
    nothing on this tool surface polls transformation runs except inspect — which
    the model would have no reason to call if the run said it was complete."""
    client.on_post(
        "/datasets/ds-1/transformations/def-1/run",
        transform_run(summary=run_summary()),
    )
    await tools["transform_data"](
        action="run", dataset_id="ds-1", definition_id="def-1"
    )

    call = client.one_call_to("POST", "/datasets/ds-1/transformations/def-1/run")
    assert call.params == {"sync": True}
    assert call.body == {}


async def test_a_completed_run_names_the_artifact_and_both_things_to_do_with_it(
    tools, client
):
    """The rows are in an artifact, never in this response. Both the source and
    output row counts are shown because the delta is the pipeline's actual
    effect, and a run that dropped 90% of the sheet looks identical to one that
    dropped none without them."""
    client.on_post(
        "/datasets/ds-1/transformations/def-1/run",
        transform_run(
            id="trun-5",
            summary=run_summary(source_row_count=1000, row_count=940),
        ),
    )
    out = await tools["transform_data"](
        action="run", dataset_id="ds-1", definition_id="def-1"
    )

    assert "run_id: trun-5" in out
    assert "status: completed" in out
    assert "sample_file: transform_output_1.parquet" in out
    assert "source_rows: 1000" in out
    assert "output_rows: 940" in out
    assert "output_columns: order_id, line_total" in out
    assert "artifact_id: art-1" in out
    assert "read_artifact" in out
    assert "publish_result with source='transformation'" in out
    assert "source version was not touched" in out


async def test_a_failed_run_surfaces_the_error_the_service_recorded(tools, client):
    """A run can fail after the request succeeds — the failure lives on the run
    row, not in the HTTP status, so ``guard`` never sees it. If the error string
    is not rendered the model is left with a run_id, no sample_file and no
    explanation, which is indistinguishable from a successful empty result."""
    client.on_post(
        "/datasets/ds-1/transformations/def-1/run",
        transform_run(
            status="failed",
            summary=None,
            artifact_id=None,
            error="Binder Error: No function matches round(VARCHAR, INTEGER)",
        ),
    )
    out = await tools["transform_data"](
        action="run", dataset_id="ds-1", definition_id="def-1"
    )

    assert "status: failed" in out
    assert "error: Binder Error: No function matches round(VARCHAR, INTEGER)" in out
    # No artifact was produced, so no sample_file field is claimed.
    assert "sample_file:" not in out
    assert "artifact_id:" not in out


async def test_inspecting_without_a_run_id_says_which_id_it_wants(tools, client):
    """run_id and definition_id are both UUIDs on the same tool and are easy to
    swap. Naming the parameter and the action is the difference between a fix
    and a retry with the other UUID."""
    with pytest.raises(ToolError) as excinfo:
        await tools["transform_data"](action="inspect", dataset_id="ds-1")

    assert str(excinfo.value) == "run_id is required when action='inspect'."
    assert client.calls == []


async def test_inspecting_a_run_is_a_read_and_says_so(tools, client):
    """The other three actions on this tool all write. 'inspect' is the one that
    is free, and the route is a GET — a POST here would create a second run."""
    client.on_get(
        "/datasets/ds-1/transformations/runs/trun-1",
        transform_run(summary=run_summary()),
    )
    out = await tools["transform_data"](
        action="inspect", dataset_id="ds-1", run_id="trun-1"
    )

    assert client.trace() == [("GET", "/datasets/ds-1/transformations/runs/trun-1")]
    assert "This action only reads. Nothing about the run changed." in out


async def test_inspecting_reports_the_per_column_profile_of_the_output(tools, client):
    """The auto-profile is why inspect exists: it is the only way to see what the
    pipeline produced without reading the artifact. Null rates and distinct
    counts are how a silently-broken cast or join shows up."""
    client.on_get(
        "/datasets/ds-1/transformations/runs/trun-1",
        transform_run(
            summary=run_summary(),
            profile=output_profile(
                {
                    "name": "line_total",
                    "dtype": "numeric",
                    "null_percent": 3.5,
                    "unique_count": 812,
                    "min": 0.5,
                    "max": 990.0,
                    "mean": 42.25,
                }
            ),
        ),
    )
    out = await tools["transform_data"](
        action="inspect", dataset_id="ds-1", run_id="trun-1"
    )

    profile_block = out.split("## Output profile\n")[1].split("\n\n")[0]
    assert profile_block.splitlines()[0] == (
        "column | dtype | nulls_pct | distinct | min | max | mean"
    )
    assert "line_total | numeric | 3.5 | 812 | 0.5 | 990 | 42.25" in profile_block


async def test_a_run_with_no_profile_explains_the_absence_as_best_effort(tools, client):
    """Profiling is skipped for oversized outputs and swallows its own failures,
    so a missing profile is normal. Rendering nothing makes it read as a run that
    produced no columns — and invites a re-run that will skip it again."""
    client.on_get(
        "/datasets/ds-1/transformations/runs/trun-1",
        transform_run(summary=run_summary(), profile=None),
    )
    out = await tools["transform_data"](
        action="inspect", dataset_id="ds-1", run_id="trun-1"
    )

    assert "No output profile was recorded" in out
    assert "best-effort" in out
    assert "oversized outputs" in out
    assert "## Output profile" not in out


async def test_inspect_lists_only_the_columns_that_actually_drifted(tools, client):
    """Drift is computed for every column present on both sides, and on a typical
    pipeline most of them are unchanged. Printing all of them buries the two that
    moved; the filter is what makes this section readable, and a column whose
    every delta is zero or missing carries no information at all."""
    client.on_get(
        "/datasets/ds-1/transformations/runs/trun-1",
        transform_run(
            summary=run_summary(),
            drift=source_drift(
                {"column": "order_id"},
                {"column": "quantity", "null_percent_delta": 0.0, "unique_count_delta": 0},
                {"column": "unit_price", "mean_delta": -3.5, "std_delta": 0.25},
            ),
        ),
    )
    out = await tools["transform_data"](
        action="inspect", dataset_id="ds-1", run_id="trun-1"
    )

    changed = out.split("## Changed columns\n")[1].split("\n\n")[0]
    assert "unit_price" in changed
    assert "order_id" not in changed
    assert "quantity" not in changed
    assert "-3.5" in changed and "0.25" in changed


async def test_inspect_reports_the_row_and_column_shape_change_against_the_source(
    tools, client
):
    """A pipeline's whole effect is usually "fewer rows, different columns". The
    added/removed column lists are how a projection or a compute step is verified
    without reading the artifact, and the row delta is how a filter is."""
    client.on_get(
        "/datasets/ds-1/transformations/runs/trun-1",
        transform_run(
            summary=run_summary(),
            drift=source_drift(
                from_row_count=1000,
                to_row_count=940,
                duplicate_rows_delta=-12,
                added_columns=["line_total"],
                removed_columns=["internal_note", "scratch"],
            ),
        ),
    )
    out = await tools["transform_data"](
        action="inspect", dataset_id="ds-1", run_id="trun-1"
    )

    assert "## Drift vs source sheet" in out
    assert "source_rows: 1000" in out
    assert "output_rows: 940" in out
    assert "row_delta: -60" in out
    assert "duplicate_rows_delta: -12" in out
    assert "columns_added: line_total" in out
    assert "columns_removed: internal_note, scratch" in out
    # Nothing drifted per column, so that section is omitted rather than empty.
    assert "## Changed columns" not in out


async def test_inspecting_a_run_with_neither_profile_nor_drift_still_reports_the_run(
    tools, client
):
    """Both are optional columns on the run row. The identifiers and the
    sample_file are what inspect must never lose — they are the way back to the
    output when everything optional is missing."""
    client.on_get(
        "/datasets/ds-1/transformations/runs/trun-1",
        transform_run(id="trun-1", summary=run_summary(), profile=None, drift=None),
    )
    out = await tools["transform_data"](
        action="inspect", dataset_id="ds-1", run_id="trun-1"
    )

    assert "run_id: trun-1" in out
    assert "definition_id: def-1" in out
    assert "sample_file: transform_output_1.parquet" in out
    assert "output_rows: 940" in out
    assert "## Drift vs source sheet" not in out


async def test_an_unknown_transform_action_names_all_four(tools, client):
    """Four verbs, two of which write. A near-miss like 'execute' (which is
    join_datasets' verb) must not fall through to a write branch."""
    with pytest.raises(ToolError) as excinfo:
        await tools["transform_data"](action="execute", dataset_id="ds-1")

    assert (
        "action must be one of create, preview, run, inspect, got 'execute'."
        == str(excinfo.value)
    )
    assert client.calls == []


# ===========================================================================
# publish_result
# ===========================================================================


async def test_publishing_a_transformation_run_uses_the_dataset_scoped_route(
    tools, client
):
    """Three sources, three different routes, one run_id shape. Posting a
    transformation run to the analytics path is a 404 that reads as "no such
    run", which sends the model looking for the run rather than the source."""
    client.on_post(
        "/datasets/ds-1/transformations/runs/trun-1/publish", publish_response()
    )
    await tools["publish_result"](
        source="transformation", run_id="trun-1", dataset_id="ds-1", name="Clean orders"
    )

    call = client.one_call_to("POST", "/datasets/ds-1/transformations/runs/trun-1/publish")
    assert call.body == {"mode": "new_dataset", "name": "Clean orders"}


async def test_publishing_an_analytics_run_uses_the_analytics_route(tools, client):
    """Same shape, different feature. The only thing distinguishing them is the
    ``source`` argument, so the mapping is worth pinning explicitly."""
    client.on_post(
        "/datasets/ds-1/analytics/runs/arun-1/publish", publish_response()
    )
    await tools["publish_result"](
        source="analytics", run_id="arun-1", dataset_id="ds-1"
    )

    call = client.one_call_to("POST", "/datasets/ds-1/analytics/runs/arun-1/publish")
    assert call.body == {"mode": "new_dataset"}


async def test_publishing_a_join_uses_the_team_scoped_route_and_needs_no_dataset_id(
    tools, client
):
    """A join has two parents and no single owner, so its publish route is not
    dataset-scoped and dataset_id is genuinely optional — requiring it would make
    the common 'new_dataset' case impossible to express."""
    client.on_post("/joins/run-9/publish", publish_response())
    await tools["publish_result"](source="join", run_id="run-9")

    assert client.trace() == [("POST", "/joins/run-9/publish")]
    assert client.one_call_to("POST", "/joins/run-9/publish").body == {
        "mode": "new_dataset"
    }


async def test_a_join_published_as_a_new_version_names_the_target_dataset(tools, client):
    """In new_version mode dataset_id picks WHICH side of the join gets the
    version; omitted it defaults to the left. Dropping the key would silently
    append to the wrong dataset."""
    client.on_post("/joins/run-9/publish", publish_response(mode="new_version"))
    await tools["publish_result"](
        source="join", run_id="run-9", mode="new_version", dataset_id="ds-2"
    )

    assert client.one_call_to("POST", "/joins/run-9/publish").body == {
        "mode": "new_version",
        "dataset_id": "ds-2",
    }


@pytest.mark.parametrize("kind", ["analytics", "transformation"])
async def test_publishing_a_dataset_scoped_run_without_dataset_id_names_the_source(
    tools, client, kind
):
    """dataset_id is optional in the signature because source='join' does not
    need it. For the other two it is a path segment, and ``/datasets/None/...``
    404s in a way that blames the run."""
    with pytest.raises(ToolError) as excinfo:
        await tools["publish_result"](source=kind, run_id="r-1")

    assert str(excinfo.value) == f"dataset_id is required when source='{kind}'."
    assert client.calls == []


async def test_publishing_reports_what_was_created_not_what_was_asked_for(tools, client):
    """The new dataset_id is the only handle on the published result and cannot
    be derived from the arguments — the name may be suffixed to avoid a
    collision, and in new_version mode the version number is the service's."""
    client.on_post(
        "/joins/run-9/publish",
        publish_response(
            dataset_id="ds-77",
            dataset_name="Orders enriched (2)",
            version_id="ver-1",
            version_number=1,
        ),
    )
    out = await tools["publish_result"](
        source="join", run_id="run-9", name="Orders enriched"
    )

    assert "dataset_id: ds-77" in out
    assert "dataset_name: Orders enriched (2)" in out
    assert "version_number: 1" in out
    assert "version_id: ver-1" in out
    assert "describe_dataset" in out


async def test_publishing_a_join_says_both_parents_are_traceable(tools, client):
    """A joined dataset is the one output whose provenance genuinely needs two
    sources. If lineage only recorded one, "where did this come from" would have
    a confidently incomplete answer."""
    client.on_post("/joins/run-9/publish", publish_response())
    out = await tools["publish_result"](source="join", run_id="run-9")

    assert "Both parents of the join are recorded in lineage" in out


async def test_publishing_a_transformation_says_lineage_records_one_version(
    tools, client
):
    """The single-parent counterpart — claiming two parents here would be false."""
    client.on_post(
        "/datasets/ds-1/transformations/runs/trun-1/publish", publish_response()
    )
    out = await tools["publish_result"](
        source="transformation", run_id="trun-1", dataset_id="ds-1"
    )

    assert "Lineage records the version this was derived from." in out
    assert "Both parents" not in out


async def test_a_new_version_publish_promises_the_earlier_versions_still_read(
    tools, client
):
    """Versions are immutable and publish is strictly additive. A model that
    thinks new_version overwrites will avoid it and create dataset sprawl
    instead; one that thinks it overwrites and wants that is worse. The claim is
    made from the mode the SERVICE reports, not the mode that was requested."""
    client.on_post(
        "/datasets/ds-1/transformations/runs/trun-1/publish",
        publish_response(mode="new_version", version_number=5),
    )
    out = await tools["publish_result"](
        source="transformation", run_id="trun-1", dataset_id="ds-1", mode="new_version"
    )

    assert "Nothing was overwritten" in out
    assert "earlier versions of this dataset are unchanged and still readable" in out
    assert "brand new dataset" not in out


async def test_a_new_dataset_publish_says_it_shadowed_nothing(tools, client):
    """new_dataset refuses rather than shadow an existing name, so a success here
    means the name was free — worth stating, because the alternative outcome is
    an error rather than a silent rename."""
    client.on_post("/joins/run-9/publish", publish_response(mode="new_dataset"))
    out = await tools["publish_result"](source="join", run_id="run-9")

    assert "this is a brand new dataset" in out
    assert "still readable" not in out


async def test_an_unknown_publish_source_names_the_three_kinds(tools, client):
    """The error has to say ``source`` rather than ``action``: this tool's
    discriminator is named differently from the other three, and a message
    blaming the wrong parameter sends the retry to a parameter that does not
    exist."""
    with pytest.raises(ToolError) as excinfo:
        await tools["publish_result"](source="query", run_id="r-1", dataset_id="ds-1")

    assert (
        "source must be one of analytics, transformation, join, got 'query'."
        == str(excinfo.value)
    )
    assert client.calls == []


@pytest.mark.parametrize("mode", ["overwrite", "replace", "NEW_DATASET"])
async def test_an_unknown_publish_mode_names_both_modes_and_writes_nothing(
    tools, client, mode
):
    """'overwrite' and 'replace' are the two things this tool deliberately cannot
    do, so they are the likeliest wrong guesses — and they must not be attempted.
    The check is exact, so an uppercase mode is refused rather than rewritten."""
    with pytest.raises(ToolError) as excinfo:
        await tools["publish_result"](
            source="join", run_id="run-9", mode=mode
        )

    assert f"mode must be 'new_dataset' or 'new_version', got {mode!r}." == str(
        excinfo.value
    )
    assert client.calls == []


async def test_publishing_over_an_existing_name_surfaces_the_service_refusal(
    tools, client
):
    """new_dataset refuses a name collision rather than shadowing. The service's
    detail names the offending name; a bare 409 would leave the model retrying
    with the same one."""
    client.on_post(
        "/joins/run-9/publish",
        problem(409, "A dataset named 'Orders enriched' already exists", "duplicate-name"),
    )
    with pytest.raises(ToolError) as excinfo:
        await tools["publish_result"](
            source="join", run_id="run-9", name="Orders enriched"
        )

    assert "A dataset named 'Orders enriched' already exists" in str(excinfo.value)


async def test_publishing_without_write_permission_explains_the_membership_case(
    tools, client
):
    """403 here means "in the team, wrong role" — a distinction a model cannot
    otherwise draw, and the difference between asking for a role and giving up."""
    client.on_post(
        "/datasets/ds-1/transformations/runs/trun-1/publish",
        problem(403, "Permission denied: dataset:write", "forbidden"),
    )
    with pytest.raises(ToolError) as excinfo:
        await tools["publish_result"](
            source="transformation", run_id="trun-1", dataset_id="ds-1"
        )

    message = str(excinfo.value)
    assert "Permission denied: dataset:write" in message
    assert "member of the owning team but lack the required permission" in message


# ===========================================================================
# The published schema — what the model can see before it calls anything
# ===========================================================================


async def _schema(tools, name: str) -> dict[str, Any]:
    listed = {t.name: t for t in await tools.server.list_tools()}
    return listed[name].input_schema or {}


async def test_the_preview_sampling_budget_is_published_as_a_bound(tools):
    """Calling the tool function directly bypasses the arg model, so the 1-500
    bound only exists if it is in the published schema. Unbounded, a model asking
    for 100_000 preview rows gets a 422 from the route instead of a clamp — and
    the route's own bound is the only thing that would stop it."""
    schema = await _schema(tools, "transform_data")
    rows = schema["properties"]["rows"]
    assert rows["minimum"] == 1
    assert rows["maximum"] == 500
    assert rows["default"] == 50


async def test_the_step_grammar_is_published_where_the_model_can_read_it(tools):
    """``steps`` is a free-form list of dicts — the JSON schema says nothing
    about what a step looks like, so the field description is the ONLY place the
    vocabulary exists. A model that cannot see the step types cannot write a
    pipeline at all, and every create becomes a 422 guessing game."""
    schema = await _schema(tools, "transform_data")
    description = schema["properties"]["steps"]["description"]

    for step_type in (
        "select", "drop", "rename", "reorder", "split", "merge", "compute",
        "cast", "trim", "case_normalize", "replace", "parse_dates",
        "filter", "deduplicate", "sort", "limit",
    ):
        assert f'"{step_type}"' in description, step_type
    # A compute expression is a typed tree, not a formula string — the single
    # most likely wrong assumption, and the one a 422 explains worst.
    assert "typed tree, never a formula string" in description
    assert "max 50 steps" in description
    assert "nest at most 12 deep" in description

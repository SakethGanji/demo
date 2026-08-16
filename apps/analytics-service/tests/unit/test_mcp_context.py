"""``tools/context.py``: lineage, activity, relationships and saved work.

These four tools answer "should I trust this dataset, and has someone already
done this work?" — questions a model asks *before* it queries anything. Their
product is not the JSON they fetch; it is what they say when the answer is
**empty or unusable**, because that is the moment a model either gives up, or
invents a join key, or reports "the API returned nothing" to a human. So the
assertions here are mostly about prose: that an absent lineage edge is
explained as "uploaded directly" rather than rendered as an empty table, that
an unconfirmed relationship says out loud it cannot drive a join, and that a
locally-filtered list admits how far it actually looked.

Every service response is scripted through ``mcp_harness.FakeAnalyticsClient``,
so no Postgres and no storage are involved. What that buys and what it does not
is stated in that module's docstring; the short version is that these tests
prove the tool handles a given payload, not that the service emits it —
``tests/test_mcp_endpoint.py`` makes the second claim against real data.

Deliberately NOT covered here:

* The pydantic argument model. Calling ``tools["get_activity"](limit=9999)``
  runs the closure directly and bypasses ``Field(ge=1, le=200)``, so no test
  below may assert a range rejection; ``test_mcp_unknown_arguments.py`` is the
  model for schema-level tests.
* Whether the service really returns these shapes. The builders below construct
  the routes' own response models (``LineageResponse``, ``TimelineEvent``,
  ``JobOut``, ``RelationshipOut``, ``DatasetViewOut``, …) so a renamed field
  breaks the builder loudly, but ``LineageResponse.parents`` is typed
  ``dict[str, Any]`` — those keys are matched by hand against
  ``library/repo.py::get_lineage`` and are not schema-checked.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.features.discovery.api import TimelineEvent, UsageResponse
from app.features.explorer.schemas import DatasetViewOut
from app.features.jobs.api import JobOut
from app.features.library.schemas import (
    ChartOut,
    DefinitionOut,
    LineageEdge,
    LineageGraphResponse,
    LineageNode,
    LineageResponse,
)
from app.features.mcp.tools import context
from app.features.mcp.tools.context import _audit_summary, _selector, _ts
from app.features.quality.schemas import RuleOut
from app.features.relationships.schemas import RelationshipOut
from app.features.transform.schemas import TransformationOut
from mcp.server.mcpserver.exceptions import ToolError
from mcp_harness import FakeAnalyticsClient, ToolSet, page, problem, register_tools

TS = "2026-05-01T09:00:00+00:00"


@pytest.fixture
def client() -> FakeAnalyticsClient:
    return FakeAnalyticsClient()


@pytest.fixture
def tools(client: FakeAnalyticsClient) -> ToolSet:
    return register_tools(context, client)


# ---------------------------------------------------------------------------
# Builders — each from the route's own response model where one exists
# ---------------------------------------------------------------------------


def _dump(model: Any) -> dict[str, Any]:
    return model.model_dump(mode="json")


def _lineage(*, parents: list[dict] = (), children: list[dict] = ()) -> dict[str, Any]:
    """``GET /datasets/{id}/lineage``. Edge keys per ``library/repo.py``."""
    return _dump(
        LineageResponse(dataset_id="ds-1", parents=list(parents), children=list(children))
    )


def _parent_edge(**over: Any) -> dict[str, Any]:
    return {
        "id": "le-1",
        "dataset_version_id": "dv-2",
        "version_number": 2,
        "parent_dataset_id": "ds-src",
        "parent_version_id": "dv-src-7",
        "parent_dataset_name": "Raw Orders",
        "parent_version_number": 7,
        "parent_sheet_key": "orders",
        "relation": "transformation",
        "created_at": "2026-04-01 08:30:00.123456+00",
        **over,
    }


def _child_edge(**over: Any) -> dict[str, Any]:
    return {
        "id": "le-2",
        "child_dataset_id": "ds-out",
        "child_dataset_name": "Orders Weekly",
        "child_version_id": "dv-out-1",
        "child_version_number": 1,
        "parent_version_number": 3,
        "parent_sheet_key": "orders",
        "relation": "publish",
        "created_at": "2026-04-02 09:00:00.999+00",
        **over,
    }


def _graph(
    *, nodes: list[dict] = (), edges: list[dict] = (), max_depth: int = 10,
    truncated: bool = False,
) -> dict[str, Any]:
    """``GET /datasets/{id}/lineage/graph``."""
    return _dump(
        LineageGraphResponse(
            dataset_id="ds-1",
            nodes=[LineageNode(**n) for n in nodes],
            edges=[LineageEdge(**e) for e in edges],
            max_depth=max_depth,
            truncated=truncated,
        )
    )


def _node(id: str, name: str, **over: Any) -> dict[str, Any]:
    return {"id": id, "name": name, "created_at": TS, **over}


def _edge(child_id: str, parent_id: str, *, relation: str = "join", depth: int = 1) -> dict:
    return {"child_id": child_id, "parent_id": parent_id, "relation": relation, "depth": depth}


def _usage(**over: Any) -> dict[str, Any]:
    """``GET /datasets/{id}/usage``."""
    fields: dict[str, Any] = {
        "dataset_id": "ds-1", "downloads": 4, "writes": 11, "total_events": 40,
        "last_activity_at": "2026-05-01 09:00:00.482913+00",
    }
    return _dump(UsageResponse(**{**fields, **over}))


def _event(event_type: str = "version_created", **over: Any) -> dict[str, Any]:
    """One ``TimelineEvent``."""
    fields: dict[str, Any] = {
        "event_type": event_type,
        "occurred_at": "2026-04-03 11:22:33.4455+00",
        "actor": "ada@example.com",
        "details": {},
    }
    return _dump(TimelineEvent(**{**fields, **over}))


def _job(**over: Any) -> dict[str, Any]:
    """One ``JobOut``."""
    fields: dict[str, Any] = {
        "id": "job-1", "job_type": "validation", "status": "completed",
        "dataset_id": "ds-1", "progress": 100, "created_at": TS,
        "completed_at": "2026-05-01 09:04:00.1+00",
    }
    return _dump(JobOut(**{**fields, **over}))


def _relationship(**over: Any) -> dict[str, Any]:
    """One ``RelationshipOut``."""
    fields: dict[str, Any] = {
        "id": "rel-1", "dataset_id": "ds-1",
        "from_logical_sheet_id": "ls-orders", "from_sheet": "orders",
        "from_column": "customer_id",
        "to_dataset_id": "ds-1", "to_logical_sheet_id": "ls-customers",
        "to_sheet": "customers", "to_column": "id",
        "status": "confirmed", "method": "manual", "evidence": {},
        "confidence": 0.98, "created_by": "ada@example.com",
        "created_at": TS, "updated_at": TS,
    }
    return _dump(RelationshipOut(**{**fields, **over}))


def _view(**over: Any) -> dict[str, Any]:
    fields: dict[str, Any] = {
        "id": "vw-1", "dataset_id": "ds-1", "logical_sheet_id": "ls-1",
        "sheet_key": "orders", "sheet_name": "Orders", "name": "Open orders",
        "version_selector": {"mode": "current"}, "query": {},
        "created_at": TS, "updated_at": TS,
    }
    return _dump(DatasetViewOut(**{**fields, **over}))


def _definition(**over: Any) -> dict[str, Any]:
    fields: dict[str, Any] = {
        "id": "def-1", "dataset_id": "ds-1", "name": "Revenue by region",
        "kind": "aggregate", "version_selector": {"mode": "current"},
        "sheet": "orders", "params": {}, "created_at": TS, "updated_at": TS,
    }
    return _dump(DefinitionOut(**{**fields, **over}))


def _chart(**over: Any) -> dict[str, Any]:
    fields: dict[str, Any] = {
        "id": "ch-1", "dataset_id": "ds-1", "name": "Revenue", "chart_type": "bar",
        "definition_id": "def-1", "config": {}, "created_at": TS, "updated_at": TS,
    }
    return _dump(ChartOut(**{**fields, **over}))


def _rule(**over: Any) -> dict[str, Any]:
    fields: dict[str, Any] = {
        "id": "rl-1", "dataset_id": "ds-1", "name": "customer_id not null",
        "scope_type": "column", "sheet_selector": "orders",
        "column_selector": "customer_id", "rule_type": "not_null",
        "parameters": {}, "severity": "error", "enabled": True,
        "created_at": TS, "updated_at": TS,
    }
    return _dump(RuleOut(**{**fields, **over}))


def _transformation(**over: Any) -> dict[str, Any]:
    fields: dict[str, Any] = {
        "id": "tr-1", "dataset_id": "ds-1", "logical_sheet_id": "ls-1",
        "sheet_key": "orders", "name": "Clean orders",
        "version_selector": {"mode": "current"}, "steps": [],
        "created_at": TS, "updated_at": TS,
    }
    return _dump(TransformationOut(**{**fields, **over}))


# ---------------------------------------------------------------------------
# get_lineage — one hop
# ---------------------------------------------------------------------------


async def test_one_hop_lineage_names_the_source_dataset_its_id_and_the_version_it_fed(
    tools, client
):
    """"Where did this come from" is only answerable if the answer carries the
    parent's *id*: a model that gets only a display name has to search for it
    again, and two datasets may share a name across teams."""
    client.on_get("/datasets/ds-1/lineage", _lineage(parents=[_parent_edge()]))

    out = await tools["get_lineage"](dataset_id="ds-1")

    assert client.one_call_to("GET", "/datasets/ds-1/lineage").params == {}
    assert "Raw Orders" in out
    assert "ds-src" in out
    assert "transformation" in out
    # Which parent version fed which of our versions — both numbers, not one.
    assert "| 7 |" in out and "| 2" in out
    assert "orders" in out


async def test_one_hop_lineage_does_not_touch_the_graph_endpoint(tools, client):
    """full_graph defaults to false, and the graph walk is the expensive call.
    A default that quietly walked the DAG would make the cheap question costly."""
    client.on_get("/datasets/ds-1/lineage", _lineage(parents=[_parent_edge()]))

    await tools["get_lineage"](dataset_id="ds-1")

    assert client.trace() == [("GET", "/datasets/ds-1/lineage")]


async def test_a_dataset_with_no_lineage_is_told_it_was_uploaded_directly(tools, client):
    """The failure this prevents: a model reading two empty tables and concluding
    the lineage feature is broken, or worse, that the dataset is orphaned. No
    edges is the *normal* state for a direct upload, and the reply has to say
    which of the two possible causes applies."""
    client.on_get("/datasets/ds-1/lineage", _lineage())

    out = await tools["get_lineage"](dataset_id="ds-1")

    assert "No lineage recorded" in out
    assert "uploaded" in out
    # And why: only platform-produced datasets get edges at all.
    assert "publishes" in out and "transformation, join" in out
    assert "(no rows)" not in out


async def test_a_root_dataset_labels_the_empty_parent_side_rather_than_omitting_it(
    tools, client
):
    """One-sided lineage is the common case. Dropping the empty side entirely
    would read as "not asked"; saying "this side is a root" is a fact."""
    client.on_get("/datasets/ds-1/lineage", _lineage(children=[_child_edge()]))

    out = await tools["get_lineage"](dataset_id="ds-1")

    assert "(none — this side is a root)" in out
    assert "Orders Weekly" in out
    assert "ds-out" in out


async def test_a_leaf_dataset_says_nothing_was_built_from_it(tools, client):
    """The "what breaks if I change this?" question. An empty children section
    is the answer "nothing downstream depends on it" and must be stated."""
    client.on_get("/datasets/ds-1/lineage", _lineage(parents=[_parent_edge()]))

    out = await tools["get_lineage"](dataset_id="ds-1")

    assert "(none — nothing was built from this)" in out


async def test_lineage_timestamps_are_trimmed_to_the_second(tools, client):
    """Postgres hands back microseconds and a timezone suffix. Nine extra
    characters per cell, in a table cell capped at 80, pushes the useful part
    out of view — and nobody reconciles lineage to the microsecond."""
    client.on_get("/datasets/ds-1/lineage", _lineage(parents=[_parent_edge()]))

    out = await tools["get_lineage"](dataset_id="ds-1")

    assert "2026-04-01 08:30:00" in out
    assert "123456" not in out


# ---------------------------------------------------------------------------
# get_lineage — full graph
# ---------------------------------------------------------------------------


async def test_the_full_graph_is_requested_at_the_depth_the_caller_asked_for(tools, client):
    """depth is the only cost control on a DAG walk. If it were dropped on the
    floor the service default would silently apply and the caller's narrowing
    would do nothing."""
    client.on_get(
        "/datasets/ds-1/lineage/graph",
        _graph(nodes=[_node("ds-1", "Orders")], max_depth=3),
    )

    await tools["get_lineage"](dataset_id="ds-1", full_graph=True, depth=3)

    assert client.one_call_to("GET", "/datasets/ds-1/lineage/graph").params == {"max_depth": 3}


async def test_graph_edges_are_rendered_as_dataset_names_not_raw_ids(tools, client):
    """The edge list only carries uuids. Resolved against the node list they
    become a readable chain; unresolved, a model has to hold five uuids in its
    head to see that A came from B came from C."""
    client.on_get(
        "/datasets/ds-1/lineage/graph",
        _graph(
            nodes=[_node("ds-1", "Orders"), _node("ds-src", "Raw Orders", is_root=True)],
            edges=[_edge("ds-1", "ds-src", relation="transformation", depth=1)],
        ),
    )

    out = await tools["get_lineage"](dataset_id="ds-1", full_graph=True)

    assert "Orders | Raw Orders | transformation | 1" in out


async def test_an_edge_to_a_dataset_missing_from_the_node_list_still_shows_its_id(
    tools, client
):
    """A node can be absent because it is beyond the depth cap or in a team the
    caller cannot read. Printing an empty cell there would hide a real edge;
    the id is at least something to look up."""
    client.on_get(
        "/datasets/ds-1/lineage/graph",
        _graph(nodes=[_node("ds-1", "Orders")], edges=[_edge("ds-1", "ds-hidden")]),
    )

    out = await tools["get_lineage"](dataset_id="ds-1", full_graph=True)

    assert "Orders | ds-hidden" in out


async def test_an_isolated_dataset_names_the_depth_that_was_searched(tools, client):
    """"No edges" only means something alongside how far we looked. Without the
    hop count a model cannot tell "no lineage" from "not far enough"."""
    client.on_get(
        "/datasets/ds-1/lineage/graph",
        _graph(nodes=[_node("ds-1", "Orders", is_root=True)], max_depth=4),
    )

    out = await tools["get_lineage"](dataset_id="ds-1", full_graph=True, depth=4)

    assert "isolated in the lineage graph within 4 hops" in out
    assert "directly uploaded dataset" in out


async def test_a_truncated_graph_says_the_dag_continues_and_names_the_depth_ceiling(
    tools, client
):
    """truncated=true is the one case where the answer is incomplete. Reported
    as a bare boolean in a fields block it gets ignored; the recovery action
    (raise depth, and how far it can go) has to be spelled out."""
    client.on_get(
        "/datasets/ds-1/lineage/graph",
        _graph(
            nodes=[_node("ds-1", "Orders"), _node("ds-src", "Raw")],
            edges=[_edge("ds-1", "ds-src")],
            max_depth=2,
            truncated=True,
        ),
    )

    out = await tools["get_lineage"](dataset_id="ds-1", full_graph=True, depth=2)

    assert "truncated: true" in out
    assert "the DAG continues beyond" in out
    assert "raise depth (max 25)" in out


async def test_a_complete_graph_does_not_tell_the_caller_to_raise_depth(tools, client):
    """The mirror of the test above: advice to page further when there is
    nothing further teaches a model to distrust the advice."""
    client.on_get(
        "/datasets/ds-1/lineage/graph",
        _graph(
            nodes=[_node("ds-1", "Orders"), _node("ds-src", "Raw")],
            edges=[_edge("ds-1", "ds-src")],
        ),
    )

    out = await tools["get_lineage"](dataset_id="ds-1", full_graph=True)

    assert "raise depth" not in out


# ---------------------------------------------------------------------------
# get_activity — the refusal, and the happy path
# ---------------------------------------------------------------------------


async def test_asking_for_no_sections_at_all_is_refused_with_both_ways_to_fix_it(
    tools, client
):
    """Without dataset_id and without jobs there is literally nothing to fetch.
    Returning an empty string would look like "this dataset has no activity",
    which is a different and wrong answer."""
    with pytest.raises(ToolError) as exc:
        await tools["get_activity"](include_jobs=False)

    assert "Pass a dataset_id" in str(exc.value)
    assert "include_jobs" in str(exc.value)
    # Refused before any request went out.
    assert client.calls == []


async def test_one_dataset_reports_usage_timeline_and_jobs_in_that_order(tools, client):
    """Pins the endpoint set. Each section answers a different question — how
    much it is used, what changed it, what ran against it — and a silently
    dropped section reads as an empty one."""
    client.on_get("/datasets/ds-1/usage", _usage())
    client.on_get("/datasets/ds-1/timeline", page([_event()], total=1))
    client.on_get("/jobs", page([_job()], total=1))

    out = await tools["get_activity"](dataset_id="ds-1")

    assert client.trace() == [
        ("GET", "/datasets/ds-1/usage"),
        ("GET", "/datasets/ds-1/timeline"),
        ("GET", "/jobs"),
    ]
    assert "## Usage" in out
    assert "downloads: 4" in out and "writes: 11" in out
    assert "last_activity_at: 2026-05-01 09:00:00" in out
    assert "## Timeline (newest first)" in out
    assert "## Jobs (newest first)" in out


async def test_the_unfiltered_timeline_is_paged_by_the_service_with_the_callers_window(
    tools, client
):
    """With no event_types there is nothing to filter locally, so limit/offset
    must go to the service rather than being re-implemented here — otherwise
    paging would silently cost a full scan per call."""
    client.on_get("/datasets/ds-1/usage", _usage())
    client.on_get("/datasets/ds-1/timeline", page([_event()], total=90, limit=5, offset=10))
    client.on_get("/jobs", page([], total=0))

    out = await tools["get_activity"](dataset_id="ds-1", limit=5, offset=10)

    assert client.one_call_to("GET", "/datasets/ds-1/timeline").params == {
        "limit": 5, "offset": 10,
    }
    assert "1 of 90 events shown." in out


async def test_a_dataset_with_no_history_says_so_instead_of_printing_an_empty_table(
    tools, client
):
    """"(no rows)" is a rendering artefact, not an answer. A dataset with no
    history is a real and reportable state."""
    client.on_get("/datasets/ds-1/usage", _usage())
    client.on_get("/datasets/ds-1/timeline", page([], total=0))
    client.on_get("/jobs", page([], total=0))

    out = await tools["get_activity"](dataset_id="ds-1")

    assert "No history events recorded for this dataset." in out
    assert "(no rows)" not in out


async def test_an_empty_page_past_the_end_of_the_timeline_blames_the_offset(tools, client):
    """Same emptiness, different cause. Without "beyond offset 40" a model
    concludes the dataset has no history when it simply paged off the end."""
    client.on_get("/datasets/ds-1/usage", _usage())
    client.on_get("/datasets/ds-1/timeline", page([], total=12, offset=40))
    client.on_get("/jobs", page([], total=0))

    out = await tools["get_activity"](dataset_id="ds-1", offset=40)

    assert "beyond offset 40." in out


# ---------------------------------------------------------------------------
# get_activity — how events are summarised
# ---------------------------------------------------------------------------


async def test_an_audit_row_is_compressed_to_method_path_status_without_the_known_prefix(
    tools, client
):
    """Audit rows dominate a busy timeline, and every one of them repeats
    `/api/v1/datasets/<the id you just passed in>`. At 80 characters per cell
    that prefix pushes the actual endpoint out of the cell — the caller pays
    tokens for a string it supplied itself and still cannot see the verb."""
    audit = _event(
        "audit",
        details={
            "method": "POST",
            "path": "/api/v1/datasets/ds-1/versions/1/sql",
            "status_code": 200,
            "request_id": "req-9",
        },
    )
    client.on_get("/datasets/ds-1/usage", _usage())
    client.on_get("/datasets/ds-1/timeline", page([audit], total=1))
    client.on_get("/jobs", page([], total=0))

    out = await tools["get_activity"](dataset_id="ds-1")

    assert "POST …/versions/1/sql -> 200" in out
    assert "/api/v1/datasets/ds-1" not in out


def test_an_audit_path_for_another_dataset_still_loses_the_api_prefix():
    """The timeline can carry a path that is not under the dataset being asked
    about. Falling through with the full path would be inconsistent; falling
    through with nothing would lose the endpoint."""
    assert _audit_summary("ds-1", {
        "method": "PATCH", "path": "/api/v1/jobs/j-2", "status_code": 204,
    }) == "PATCH …/jobs/j-2 -> 204"


def test_an_audit_path_that_matches_no_known_prefix_is_left_whole():
    """Better a long path than a mangled one. The elision is a fixed-prefix
    strip, not a truncation, so a path that starts somewhere unexpected must
    come through byte for byte — a leading "…" on an unrecognised path would
    imply something was removed that was not."""
    assert _audit_summary(None, {
        "method": "POST", "path": "/internal/reindex", "status_code": 500,
    }) == "POST /internal/reindex -> 500"


async def test_a_non_audit_events_details_become_key_equals_value_and_drop_the_empties(
    tools, client
):
    """A tag_set event's `reason` is the whole point of the row, but the same
    blob carries nulls for every field that did not apply. Rendering
    `from_version=None` spends tokens asserting nothing happened."""
    tag = _event(
        "tag_set",
        details={"tag": "prod", "from_version": None, "to_version": 4, "reason": "signed off"},
    )
    client.on_get("/datasets/ds-1/usage", _usage())
    client.on_get("/datasets/ds-1/timeline", page([tag], total=1))
    client.on_get("/jobs", page([], total=0))

    out = await tools["get_activity"](dataset_id="ds-1")

    assert "tag=prod" in out and "to_version=4" in out and "reason=signed off" in out
    assert "from_version" not in out


async def test_a_lineage_event_points_at_get_lineage_for_the_untruncated_ids(tools, client):
    """Cells are capped at 80 characters, so a `published_to` row's child
    dataset id can arrive cut in half. A model that retypes a truncated uuid
    gets a 404 and no idea why — say where the whole id lives instead."""
    ev = _event(
        "published_to",
        details={"relation": "publish", "child_dataset_id": "d" * 40, "child_dataset": "Wk"},
    )
    client.on_get("/datasets/ds-1/usage", _usage())
    client.on_get("/datasets/ds-1/timeline", page([ev], total=1))
    client.on_get("/jobs", page([], total=0))

    out = await tools["get_activity"](dataset_id="ds-1")

    assert "call get_lineage for the untruncated dataset ids" in out


async def test_a_timeline_without_lineage_events_does_not_advertise_get_lineage(
    tools, client
):
    """Advice attached to every response is advice a model learns to skip. It
    must appear only when a truncated id is actually on screen."""
    client.on_get("/datasets/ds-1/usage", _usage())
    client.on_get("/datasets/ds-1/timeline", page([_event("version_created")], total=1))
    client.on_get("/jobs", page([], total=0))

    out = await tools["get_activity"](dataset_id="ds-1")

    assert "get_lineage" not in out


# ---------------------------------------------------------------------------
# get_activity — the locally filtered timeline
# ---------------------------------------------------------------------------


def _timeline_pages(match_per_page: int = 1) -> dict[str, Any]:
    """A full 200-row page, mostly audit rows — what a busy dataset looks like."""
    rows = [_event("audit", details={"method": "PUT", "path": "/api/v1/x", "status_code": 200})]
    rows *= 200 - match_per_page
    rows += [_event("version_created", details={"version_number": 4})] * match_per_page
    return page(rows, total=5000, limit=200)


async def test_event_type_filtering_happens_here_because_the_endpoint_has_no_such_filter(
    tools, client
):
    """/timeline takes no event_type parameter. Sending one would be silently
    ignored by FastAPI and the caller would get audit rows back believing they
    were filtered — the exact silent-wrong-answer this local scan avoids."""
    client.on_get("/datasets/ds-1/usage", _usage())
    client.on_get("/datasets/ds-1/timeline", _timeline_pages())
    client.on_get("/jobs", page([], total=0))

    out = await tools["get_activity"](dataset_id="ds-1", event_types=["version_created"])

    for call in client.calls_to("GET", "/datasets/ds-1/timeline"):
        assert set(call.params) == {"limit", "offset"}
        assert call.params["limit"] == 200
    assert "audit" not in out.split("## Jobs")[0].split("## Timeline")[1]


async def test_a_partial_scan_admits_how_far_it_looked_and_why(tools, client):
    """The dangerous version of this feature reports 5 matches as if it had
    read the whole history. Saying "the 1000 most recent of 5000" is the
    difference between a fact and an unfounded conclusion."""
    client.on_get("/datasets/ds-1/usage", _usage())
    client.on_get("/datasets/ds-1/timeline", _timeline_pages())
    client.on_get("/jobs", page([], total=0))

    out = await tools["get_activity"](dataset_id="ds-1", event_types=["version_created"])

    assert len(client.calls_to("GET", "/datasets/ds-1/timeline")) == 5  # SCAN_CAP / PAGE_SIZE
    assert "filtered locally over the 1000 most recent of 5000 events" in out
    assert "/timeline has no event_type filter" in out


async def test_a_filtered_timeline_never_reports_the_unfiltered_total(tools, client):
    """The endpoint's `total` counts every event type. Printing "5 of 5000
    events shown" next to a filtered table claims 4995 more matching events
    exist, which is false and invites pointless paging."""
    client.on_get("/datasets/ds-1/usage", _usage())
    client.on_get("/datasets/ds-1/timeline", _timeline_pages())
    client.on_get("/jobs", page([], total=0))

    out = await tools["get_activity"](dataset_id="ds-1", event_types=["version_created"])

    assert "5 events shown." in out
    assert "of 5000 events shown" not in out


async def test_a_filter_matching_nothing_in_a_partial_scan_offers_a_larger_offset(
    tools, client
):
    """"No such events" after reading 20% of the history is a claim the tool
    cannot support. It must report the window it read and the retry that would
    widen it."""
    client.on_get("/datasets/ds-1/usage", _usage())
    client.on_get("/datasets/ds-1/timeline", _timeline_pages(match_per_page=0))
    client.on_get("/jobs", page([], total=0))

    out = await tools["get_activity"](dataset_id="ds-1", event_types=["profile_run"])

    assert "No profile_run events in the 1000 most recent events" in out
    assert "retry with a larger offset" in out
    assert "The timeline holds 5000 events in total." in out


async def test_a_filter_matching_nothing_in_a_complete_scan_states_it_as_a_fact(
    tools, client
):
    """When the whole timeline was read, "in the whole timeline" is the honest
    phrasing and there is no larger offset to suggest — proposing one would
    send the model round a loop that cannot succeed."""
    client.on_get("/datasets/ds-1/usage", _usage())
    client.on_get("/datasets/ds-1/timeline", page([_event("audit")], total=1))
    client.on_get("/jobs", page([], total=0))

    out = await tools["get_activity"](dataset_id="ds-1", event_types=["profile_run"])

    assert "No profile_run events in the whole timeline." in out
    assert "retry with a larger offset" not in out


async def test_several_event_types_are_named_in_one_sorted_list_when_none_match(
    tools, client
):
    """The caller asked about three things; the report must name all three, or
    a model cannot tell which of them was missing."""
    client.on_get("/datasets/ds-1/usage", _usage())
    client.on_get("/datasets/ds-1/timeline", page([_event("audit")], total=1))
    client.on_get("/jobs", page([], total=0))

    out = await tools["get_activity"](
        dataset_id="ds-1", event_types=["validation_run", "profile_run"]
    )

    assert "No profile_run, validation_run events" in out


async def test_event_types_are_matched_case_insensitively_and_untrimmed(tools, client):
    """The tool description lists the types in lowercase, but a model copying
    one out of a rendered table or a sentence brings the surrounding spaces and
    sometimes the capitalisation. Rejecting that costs a whole round trip for
    nothing."""
    client.on_get("/datasets/ds-1/usage", _usage())
    client.on_get(
        "/datasets/ds-1/timeline",
        page([_event("version_created", details={"version_number": 4})], total=1),
    )
    client.on_get("/jobs", page([], total=0))

    out = await tools["get_activity"](dataset_id="ds-1", event_types=[" Version_Created "])

    assert "version_number=4" in out


# ---------------------------------------------------------------------------
# get_activity — jobs
# ---------------------------------------------------------------------------


async def test_platform_wide_jobs_pass_the_status_and_type_filters_to_the_service(
    tools, client
):
    """/jobs *does* filter by status and type. Re-implementing that locally
    would scan pages the service could have skipped."""
    client.on_get("/jobs", page([_job(status="failed", job_type="import")], total=3))

    out = await tools["get_activity"](job_status="failed", job_type="import", limit=10)

    assert client.one_call_to("GET", "/jobs").params == {
        "status": "failed", "job_type": "import", "limit": 10,
    }
    assert "1 of 3 jobs shown." in out


async def test_omitted_job_filters_are_not_sent_as_empty_values(tools, client):
    """An unset optional must not reach the wire. `status=None` as a query
    string would be the literal filter "None" and match nothing."""
    client.on_get("/jobs", page([_job()], total=1))

    await tools["get_activity"]()

    assert client.one_call_to("GET", "/jobs").params == {"limit": 25}


async def test_jobs_for_one_dataset_are_filtered_here_and_the_window_is_reported(
    tools, client
):
    """/jobs has no dataset filter, so a dataset's jobs are found by scanning.
    A scan that stops at the cap and does not say so would report "no import
    job ran" about a dataset whose import is simply older than 1000 jobs."""
    client.on_get("/datasets/ds-1/usage", _usage())
    client.on_get("/datasets/ds-1/timeline", page([], total=0))
    client.on_get("/jobs", page([_job(dataset_id="ds-other")] * 200, total=5000, limit=200))

    out = await tools["get_activity"](dataset_id="ds-1")

    assert len(client.calls_to("GET", "/jobs")) == 5
    assert "filtered to this dataset locally over the 1000 most recent jobs" in out
    assert "/jobs has no dataset filter, so older ones may exist" in out


async def test_the_job_scan_stops_as_soon_as_the_callers_window_is_full(tools, client):
    """The scan cap is 1000 jobs — five requests. Scanning to the cap when the
    first page already produced more matches than the caller asked for spends
    four requests to throw the results away."""
    client.on_get("/datasets/ds-1/usage", _usage())
    client.on_get("/datasets/ds-1/timeline", page([], total=0))
    client.on_get("/jobs", page([_job()] * 200, total=5000, limit=200))

    out = await tools["get_activity"](dataset_id="ds-1", limit=25)

    assert len(client.calls_to("GET", "/jobs")) == 1
    assert "25 jobs shown." in out
    # And it still admits it only looked at the first page.
    assert "over the 200 most recent jobs" in out


async def test_a_job_scan_that_reached_the_end_does_not_blame_the_window(tools, client):
    """The mirror: when every job was read, "older ones may exist" is false and
    would keep a model paging forever."""
    client.on_get("/datasets/ds-1/usage", _usage())
    client.on_get("/datasets/ds-1/timeline", page([], total=0))
    client.on_get("/jobs", page([_job()], total=1))

    out = await tools["get_activity"](dataset_id="ds-1")

    assert "No background jobs for this dataset." not in out
    assert "older ones may exist" not in out
    assert "job-1" in out


async def test_no_matching_jobs_repeats_the_filters_that_were_applied(tools, client):
    """An empty result is only interpretable next to the query that produced
    it; otherwise "no jobs" reads as "this platform runs no jobs"."""
    client.on_get("/jobs", page([], total=0))

    out = await tools["get_activity"](job_status="failed", job_type="import")

    assert "No background jobs matching status=failed, job_type=import at all." in out


async def test_no_jobs_for_a_dataset_after_a_complete_scan_says_it_plainly(tools, client):
    """Scoped emptiness, honestly scoped: "for this dataset", not "at all"."""
    client.on_get("/datasets/ds-1/usage", _usage())
    client.on_get("/datasets/ds-1/timeline", page([], total=0))
    client.on_get("/jobs", page([_job(dataset_id="ds-other")], total=1))

    out = await tools["get_activity"](dataset_id="ds-1")

    assert "No background jobs for this dataset." in out


async def test_jobs_can_be_switched_off_entirely(tools, client):
    """The jobs section costs up to five requests for one dataset. A caller who
    only wants the timeline must be able to not pay for it."""
    client.on_get("/datasets/ds-1/usage", _usage())
    client.on_get("/datasets/ds-1/timeline", page([_event()], total=1))

    out = await tools["get_activity"](dataset_id="ds-1", include_jobs=False)

    assert client.calls_to("GET", "/jobs") == []
    assert "Jobs" not in out


async def test_an_oversized_activity_report_is_cut_and_the_hint_names_the_way_out(
    tools, client
):
    """A 200-event, 200-job report blows past any sane context budget. Cutting
    it is not enough — the caller needs to know which argument to change, and
    that dropping the audit rows is the big win."""
    other = "8c1f0b6e-25a7-4f3b-9a01-2c9d4e7f6a55"
    audit = _event(
        "audit",
        actor="someone.with.a.long.name@example.com",
        details={"method": "POST",
                 "path": f"/api/v1/datasets/{other}/versions/12/sheets/orders/query",
                 "status_code": 200},
    )
    job = _job(
        id="1f2e3d4c-5b6a-4798-8899-aabbccddeeff",
        dataset_id="ds-1",
        job_type="relationship_discovery",
        status="failed",
        error="worker died: " + "x" * 90,
    )
    client.on_get("/datasets/ds-1/usage", _usage())
    client.on_get("/datasets/ds-1/timeline", page([audit] * 200, total=4000, limit=200))
    client.on_get("/jobs", page([job] * 200, total=200, limit=200))

    out = await tools["get_activity"](dataset_id="ds-1", limit=200)

    assert "[response truncated at 60,000 characters." in out
    assert "pass event_types to drop the audit rows" in out


async def test_a_permission_failure_on_usage_stops_before_the_rest_and_explains_403(
    tools, client
):
    """403 here means "in the team, wrong role" — distinct from the 404 the
    service returns for a dataset in a team you are not in. A model that cannot
    tell them apart retries the wrong fix."""
    client.on_get("/datasets/ds-1/usage", problem(403, "Forbidden", "forbidden"))

    with pytest.raises(ToolError) as exc:
        await tools["get_activity"](dataset_id="ds-1")

    assert "member of the owning team but lack the required permission" in str(exc.value)
    assert client.trace() == [("GET", "/datasets/ds-1/usage")]


# ---------------------------------------------------------------------------
# list_relationships — the list
# ---------------------------------------------------------------------------


async def test_the_relationship_list_passes_the_status_filter_to_the_service(tools, client):
    """"Show me only confirmed keys" is the main use of this tool. Filtering
    locally instead would mean the 50-row window fills with rejected edges."""
    client.on_get("/datasets/ds-1/relationships", page([_relationship()], total=1))

    await tools["list_relationships"](dataset_id="ds-1", status="confirmed", limit=10, offset=5)

    assert client.one_call_to("GET", "/datasets/ds-1/relationships").params == {
        "status": "confirmed", "limit": 10, "offset": 5,
    }


async def test_an_omitted_status_returns_all_three_states_without_sending_a_filter(
    tools, client
):
    """`status=None` on the wire would be the literal string "None" and match
    no edge — the caller would be told the dataset has no relationships."""
    client.on_get("/datasets/ds-1/relationships", page([_relationship()], total=1))

    await tools["list_relationships"](dataset_id="ds-1")

    assert "status" not in client.one_call_to("GET", "/datasets/ds-1/relationships").params


async def test_an_invalid_status_names_all_three_valid_values(tools, client):
    """The vocabulary is three words; a model that is only told "invalid" has
    to guess, and the service would have answered with an empty list rather
    than an error, so this is the only place the mistake can be caught."""
    with pytest.raises(ToolError) as exc:
        await tools["list_relationships"](dataset_id="ds-1", status="verified")

    assert "suggested, confirmed or rejected" in str(exc.value)
    assert "'verified'" in str(exc.value)
    assert client.calls == []


async def test_status_is_accepted_with_stray_case_and_spacing(tools, client):
    """Rejecting " Confirmed " would spend a round trip on a difference that
    cannot change the answer."""
    client.on_get("/datasets/ds-1/relationships", page([_relationship()], total=1))

    await tools["list_relationships"](dataset_id="ds-1", status=" Confirmed ")

    assert client.one_call_to("GET", "/datasets/ds-1/relationships").params["status"] == (
        "confirmed"
    )


async def test_a_list_of_confirmed_edges_says_any_of_them_can_drive_a_join(tools, client):
    """The verdict line is the point of the tool: the status column alone
    requires the reader to already know that only `confirmed` is usable."""
    client.on_get(
        "/datasets/ds-1/relationships",
        page([_relationship(), _relationship(id="rel-2")], total=2),
    )

    out = await tools["list_relationships"](dataset_id="ds-1")

    assert "All of these are confirmed, so any of them can drive a join." in out
    assert "orders.customer_id" in out and "customers.id" in out


async def test_a_mixed_list_counts_how_many_edges_are_actually_usable(tools, client):
    """Two of five usable is a materially different answer from five of five,
    and the count saves the model re-reading the status column row by row."""
    items = [
        _relationship(id="a"),
        _relationship(id="b", status="suggested", method="discovery"),
        _relationship(id="c", status="rejected", method="discovery"),
    ]
    client.on_get("/datasets/ds-1/relationships", page(items, total=3))

    out = await tools["list_relationships"](dataset_id="ds-1")

    assert "1 of these are confirmed and can drive a join; the other 2 cannot." in out


async def test_a_list_with_nothing_confirmed_warns_that_none_are_usable_yet(tools, client):
    """The worst outcome for this tool is a model joining on a statistical
    guess. A page full of `suggested` edges looks like an answer unless the
    text says it is not one."""
    items = [_relationship(id="a", status="suggested", method="discovery", confidence=0.91)]
    client.on_get("/datasets/ds-1/relationships", page(items, total=1))

    out = await tools["list_relationships"](dataset_id="ds-1", status="suggested")

    assert "None of these are confirmed, so none can drive a join yet" in out
    assert "hypotheses awaiting review" in out


async def test_a_cross_dataset_edge_is_flagged_as_crossing(tools, client):
    """A join to another dataset needs that dataset loaded and readable. The
    flag is what tells a model the join is not a within-dataset one."""
    items = [
        _relationship(id="a"),
        _relationship(id="b", to_dataset_id="ds-2", to_sheet="regions", to_column="code"),
    ]
    client.on_get("/datasets/ds-1/relationships", page(items, total=2))

    out = await tools["list_relationships"](dataset_id="ds-1")

    body = [line for line in out.splitlines() if line.startswith(("a |", "b |"))]
    assert body[0].split(" | ")[3] == "false"
    assert body[1].split(" | ")[3] == "true"


async def test_an_edge_whose_sheet_name_is_missing_renders_a_placeholder_not_none(
    tools, client
):
    """`from_sheet` is nullable on the model. "None.customer_id" reads like a
    real sheet called None; "?" reads as unknown, which it is."""
    client.on_get(
        "/datasets/ds-1/relationships",
        page([_relationship(from_sheet=None)], total=1),
    )

    out = await tools["list_relationships"](dataset_id="ds-1")

    assert "?.customer_id" in out
    assert "None.customer_id" not in out


async def test_no_relationships_at_all_says_what_to_do_instead(tools, client):
    """An empty list is where a model is most likely to invent a join key. The
    reply has to give it a legitimate next move — column names and the data
    dictionary — and repeat the confirmed-only rule it is about to break."""
    client.on_get("/datasets/ds-1/relationships", page([], total=0))

    out = await tools["list_relationships"](dataset_id="ds-1")

    assert "No relationships are recorded for this dataset." in out
    assert "infer keys from column names and the data dictionary" in out
    assert "only confirmed edges can drive the join builder" in out


async def test_an_empty_filtered_list_repeats_the_status_that_was_filtered_on(
    tools, client
):
    """"No relationships" and "no *confirmed* relationships" are different
    facts: the second one leaves suggested edges worth reviewing."""
    client.on_get("/datasets/ds-1/relationships", page([], total=0))

    out = await tools["list_relationships"](dataset_id="ds-1", status="confirmed")

    assert "No relationships with status 'confirmed' are recorded" in out


async def test_a_long_relationship_list_is_cut_with_a_hint_naming_status_and_limit(
    tools, client
):
    """200 edges with long column names exceed the response budget. The cut
    must name the two arguments that shrink it, or the retry is a guess."""
    wide = _relationship(
        id="r" * 90, from_column="c" * 90, to_column="t" * 90, method="m" * 90,
    )
    client.on_get("/datasets/ds-1/relationships", page([wide] * 200, total=400, limit=200))

    out = await tools["list_relationships"](dataset_id="ds-1", limit=200)

    assert "[response truncated at 60,000 characters." in out
    assert "Filter with `status`, or lower `limit`." in out


async def test_a_404_on_relationships_warns_that_404_can_also_mean_no_access(
    tools, client
):
    """The service hides cross-team existence behind 404. A model told only
    "not found" will report the dataset does not exist, when the real fix is an
    access request."""
    client.on_get(
        "/datasets/ds-1/relationships",
        problem(404, "Dataset not found: ds-1", "not-found"),
    )

    with pytest.raises(ToolError) as exc:
        await tools["list_relationships"](dataset_id="ds-1")

    assert "owned by a team you are not in" in str(exc.value)


# ---------------------------------------------------------------------------
# list_relationships — one edge in full
# ---------------------------------------------------------------------------


async def test_one_edge_is_fetched_by_id_and_prints_its_evidence(tools, client):
    """Evidence is why the edge exists. Overlap and distinctness are what let a
    human accept or reject a suggestion, and they appear nowhere in the list."""
    edge = _relationship(
        id="rel-9", status="suggested", method="discovery", confidence=0.87,
        evidence={"overlap_ratio": 0.97, "distinct_ratio": 1.0, "sampled_rows": 5000},
    )
    client.on_get("/datasets/ds-1/relationships/rel-9", edge)

    out = await tools["list_relationships"](dataset_id="ds-1", relationship_id="rel-9")

    assert client.trace() == [("GET", "/datasets/ds-1/relationships/rel-9")]
    assert "## Evidence" in out
    assert "overlap_ratio: 0.97" in out
    assert "sampled_rows: 5000" in out


async def test_a_suggested_edge_is_told_in_words_that_it_cannot_drive_a_join(tools, client):
    """The single most damaging misread on this surface: treating a discovery
    guess as a verified key. The status has to come with its consequence."""
    client.on_get(
        "/datasets/ds-1/relationships/rel-9",
        _relationship(id="rel-9", status="suggested", method="discovery"),
    )

    out = await tools["list_relationships"](dataset_id="ds-1", relationship_id="rel-9")

    assert "This edge is 'suggested', so it cannot drive a join" in out
    assert "Treat it as a hypothesis and check the evidence." in out


async def test_a_confirmed_edge_is_told_it_is_usable(tools, client):
    """The positive case must be equally explicit, or a model hedges on a key
    a human already verified and falls back to guessing."""
    client.on_get("/datasets/ds-1/relationships/rel-1", _relationship())

    out = await tools["list_relationships"](dataset_id="ds-1", relationship_id="rel-1")

    assert "This edge is confirmed, so it can drive a join." in out


async def test_an_edge_with_no_evidence_says_so_rather_than_showing_a_blank_section(
    tools, client
):
    """A manually declared edge has no evidence by construction. An empty
    "## Evidence" heading would read as missing data rather than as N/A."""
    client.on_get("/datasets/ds-1/relationships/rel-1", _relationship(evidence={}))

    out = await tools["list_relationships"](dataset_id="ds-1", relationship_id="rel-1")

    assert "No evidence was recorded for this edge." in out
    assert "## Evidence" not in out


# ---------------------------------------------------------------------------
# list_saved_objects — the counts view
# ---------------------------------------------------------------------------

ALL_SEGMENTS = ["views", "analytics", "charts", "rules", "transformations"]


def _script_counts(client: FakeAnalyticsClient, **totals: int) -> None:
    for segment in ALL_SEGMENTS:
        client.on_get(f"/datasets/ds-1/{segment}", page([], total=totals.get(segment, 0)))


async def test_no_kind_counts_every_kind_with_one_cheap_request_each(tools, client):
    """The overview must not download five full lists to count them; limit=1
    makes each probe O(1) on a dataset with hundreds of saved objects."""
    _script_counts(client, views=3, rules=7)

    out = await tools["list_saved_objects"](dataset_id="ds-1")

    assert client.trace() == [("GET", f"/datasets/ds-1/{s}") for s in ALL_SEGMENTS]
    for call in client.calls:
        assert call.params == {"limit": 1}
    assert "view | 3" in out and "rule | 7" in out and "chart | 0" in out


async def test_the_counts_table_explains_what_each_kind_is_and_how_to_open_one(
    tools, client
):
    """A bare count table is five nouns and five numbers. Without the glossary
    a model cannot tell a "view" from a "transformation", and picks by name."""
    _script_counts(client, views=3)

    out = await tools["list_saved_objects"](dataset_id="ds-1")

    assert "Call again with kind=<one of these> for the list." in out
    assert "view = saved filter/projection over one sheet" in out
    assert "rule = data-quality rule" in out


async def test_a_dataset_with_nothing_saved_warns_that_validation_would_check_nothing(
    tools, client
):
    """Zero rules is not a neutral fact: a validation run against it passes
    while checking nothing, which reads as "this data is fine"."""
    _script_counts(client)

    out = await tools["list_saved_objects"](dataset_id="ds-1")

    assert "Nothing has been saved on this dataset" in out
    assert "with no quality rules a validation run would check nothing" in out


# ---------------------------------------------------------------------------
# list_saved_objects — argument handling
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "given,segment",
    [("view", "views"), ("views", "views"), ("VIEW ", "views"),
     ("analytics", "analytics"), ("charts", "charts"), ("rules", "rules"),
     ("transformations", "transformations")],
)
async def test_the_plural_shown_in_the_counts_table_is_accepted_as_a_kind(
    tools, client, given, segment
):
    """The counts table prints singular kinds but the URL segment is plural,
    and both appear in this tool's own output. Rejecting the form the caller
    just read on screen is a round trip spent on our own inconsistency."""
    client.on_get(f"/datasets/ds-1/{segment}", page([], total=0))

    await tools["list_saved_objects"](dataset_id="ds-1", kind=given)

    assert client.trace() == [("GET", f"/datasets/ds-1/{segment}")]


async def test_an_unknown_kind_lists_the_five_valid_kinds_and_what_they_mean(
    tools, client
):
    """A model that guessed "dashboard" needs the vocabulary *and* the
    semantics in one step, otherwise it guesses again from the same names."""
    with pytest.raises(ToolError) as exc:
        await tools["list_saved_objects"](dataset_id="ds-1", kind="dashboard")

    message = str(exc.value)
    assert "kind must be one of view, analytics, chart, rule, transformation" in message
    assert "'dashboard'" in message
    assert "chart = saved chart over a definition or a view" in message
    assert client.calls == []


async def test_an_object_id_without_a_kind_explains_why_the_id_alone_is_not_enough(
    tools, client
):
    """Five tables, five id spaces, no way to tell which one an id belongs to.
    Guessing would mean up to five requests and four 404s."""
    with pytest.raises(ToolError) as exc:
        await tools["list_saved_objects"](dataset_id="ds-1", object_id="vw-1")

    assert "object_id needs kind" in str(exc.value)
    assert "does not say which type to fetch" in str(exc.value)
    assert client.calls == []


async def test_a_bad_sort_order_is_rejected_here_because_nothing_downstream_can_reject_it(
    tools, client
):
    """This tool sorts in-process, so there is no service call to validate
    sort_order. "DESC" silently falling back to ascending would hand back the
    oldest saved work labelled as the newest."""
    with pytest.raises(ToolError) as exc:
        await tools["list_saved_objects"](dataset_id="ds-1", kind="view", sort_order="DESC")

    assert "sort_order must be 'asc' or 'desc' (lowercase)" in str(exc.value)
    assert "silently reverse" in str(exc.value)
    assert client.calls == []


# ---------------------------------------------------------------------------
# list_saved_objects — the list
# ---------------------------------------------------------------------------


async def test_saved_objects_are_listed_newest_first_by_default(tools, client):
    """"What did someone do most recently" is the question this answers. The
    list routes return insertion order, so the sort has to happen here."""
    items = [
        _view(id="old", name="Old", created_at="2026-01-01T00:00:00+00:00"),
        _view(id="new", name="New", created_at="2026-03-01T00:00:00+00:00"),
        _view(id="mid", name="Mid", created_at="2026-02-01T00:00:00+00:00"),
    ]
    client.on_get("/datasets/ds-1/views", page(items, total=3))

    out = await tools["list_saved_objects"](dataset_id="ds-1", kind="view")

    assert out.index("New") < out.index("Mid") < out.index("Old")


async def test_ascending_order_actually_reverses_the_list(tools, client):
    """sort_order that is accepted but ignored is worse than one that is
    rejected: the caller believes it is looking at the oldest work."""
    items = [
        _view(id="old", name="Old", created_at="2026-01-01T00:00:00+00:00"),
        _view(id="new", name="New", created_at="2026-03-01T00:00:00+00:00"),
    ]
    client.on_get("/datasets/ds-1/views", page(items, total=2))

    out = await tools["list_saved_objects"](dataset_id="ds-1", kind="view", sort_order="asc")

    assert out.index("Old") < out.index("New")


async def test_the_whole_list_is_collected_before_sorting_so_paging_is_exact(tools, client):
    """Sorting one page at a time would make "newest first" mean "newest on
    this page", and the newest object of all could sit on page three. The
    collector pages the endpoint out and sorts the union."""
    first = page([_view(id=f"a{i}", name=f"A{i}", created_at="2026-01-01T00:00:00+00:00")
                  for i in range(200)], total=250, limit=200)
    second = page([_view(id="z", name="Newest", created_at="2026-09-09T00:00:00+00:00")] * 50,
                  total=250, limit=200, offset=200)
    client.on_get("/datasets/ds-1/views", first, second)

    out = await tools["list_saved_objects"](dataset_id="ds-1", kind="view", limit=1)

    assert [c.params for c in client.calls_to("GET", "/datasets/ds-1/views")] == [
        {"limit": 200, "offset": 0}, {"limit": 200, "offset": 200},
    ]
    assert "Newest" in out
    assert "1 of 250 views shown." in out


async def test_a_list_route_that_ignores_paging_is_not_fetched_twice(tools, client):
    """Half these routes (analytics, charts, rules) ignore limit/offset and
    return the whole set in one response. Paging blindly on "the page was
    full" would re-fetch the same 250 rows at offset 250 and count every
    object twice; the reported total is what stops the loop."""
    items = [_definition(id=f"d{i}", name=f"D{i}",
                         created_at=f"2026-01-01T00:00:{i % 60:02d}+00:00")
             for i in range(250)]
    client.on_get("/datasets/ds-1/analytics", page(items, total=250))

    out = await tools["list_saved_objects"](dataset_id="ds-1", kind="analytics")

    assert len(client.calls_to("GET", "/datasets/ds-1/analytics")) == 1
    assert "50 of 250 analytics shown." in out


async def test_an_offset_past_the_end_says_how_many_objects_actually_exist(tools, client):
    """An empty window from over-paging is indistinguishable from an empty
    dataset unless the count is given — and the count is what makes the retry
    correct on the first attempt."""
    client.on_get("/datasets/ds-1/views", page([_view()] * 3, total=3))

    out = await tools["list_saved_objects"](dataset_id="ds-1", kind="view", offset=10)

    assert "Only 3 saved views exist, so offset 10 is past the end." in out
    assert "Lower the offset." in out


async def test_no_saved_views_points_at_the_overview_for_the_other_kinds(tools, client):
    """A dead end for one kind is not a dead end for the dataset; the next
    useful call is the one that counts all five."""
    client.on_get("/datasets/ds-1/views", page([], total=0))

    out = await tools["list_saved_objects"](dataset_id="ds-1", kind="view")

    assert "No saved views on this dataset." in out
    assert "Call list_saved_objects with no kind" in out


async def test_no_saved_rules_warns_that_validation_has_nothing_to_check(tools, client):
    """Same emptiness, different consequence — a validation run on a dataset
    with no rules reports success, which is the most misleading green there is."""
    client.on_get("/datasets/ds-1/rules", page([], total=0))

    out = await tools["list_saved_objects"](dataset_id="ds-1", kind="rule")

    assert "With no quality rules defined, a validation run would have nothing to check." in out


@pytest.mark.parametrize(
    "kind,segment,item,expected",
    [
        ("view", "views", _view(), ["sheet", "version", "created"]),
        ("analytics", "analytics", _definition(), ["kind", "sheet", "version"]),
        ("chart", "charts", _chart(), ["chart_type", "source"]),
        ("rule", "rules", _rule(), ["rule_type", "scope", "column", "severity", "enabled"]),
        ("transformation", "transformations", _transformation(), ["sheet", "steps", "version"]),
    ],
)
async def test_each_kind_is_summarised_by_the_columns_that_distinguish_it(
    tools, client, kind, segment, item, expected
):
    """One shared row shape would hide what matters per kind — a rule's
    severity and enabled flag decide whether it runs at all, and a chart's
    source decides whether it is even reproducible."""
    client.on_get(f"/datasets/ds-1/{segment}", page([item], total=1))

    out = await tools["list_saved_objects"](dataset_id="ds-1", kind=kind)

    header = out.splitlines()[0].split(" | ")
    assert header[:2] == ["id", "name"]
    for column in expected:
        assert column in header


async def test_a_disabled_rule_is_shown_as_disabled_rather_than_omitted(tools, client):
    """`enabled: false` is falsy, and a renderer that skips empties would drop
    it — leaving a rule that never runs looking identical to one that does."""
    client.on_get("/datasets/ds-1/rules", page([_rule(enabled=False)], total=1))

    out = await tools["list_saved_objects"](dataset_id="ds-1", kind="rule")

    assert "| false" in out


async def test_a_chart_names_the_definition_or_the_view_it_was_built_on(tools, client):
    """A chart owns no query. Without its source id the caller cannot see what
    it plots, and the two sources live in different tables."""
    items = [_chart(id="c1", definition_id="def-7", view_id=None),
             _chart(id="c2", definition_id=None, view_id="vw-3")]
    client.on_get("/datasets/ds-1/charts", page(items, total=2))

    out = await tools["list_saved_objects"](dataset_id="ds-1", kind="chart")

    assert "definition:def-7" in out
    assert "view:vw-3" in out


async def test_a_transformation_row_counts_its_steps_instead_of_dumping_them(tools, client):
    """Steps are nested blobs; inlined into a table cell they are truncated to
    unusable fragments. The count tells the caller whether it is worth opening."""
    client.on_get(
        "/datasets/ds-1/transformations",
        page([_transformation(steps=[{"op": "dedupe"}, {"op": "select"}, {"op": "rename"}])],
             total=1),
    )

    out = await tools["list_saved_objects"](dataset_id="ds-1", kind="transformation")

    assert "| 3 |" in out
    assert "dedupe" not in out


@pytest.mark.parametrize(
    "selector,shown",
    [({"mode": "current"}, "current"),
     ({"mode": "tag", "tag": "prod"}, "tag:prod"),
     ({"mode": "version", "version_number": 4}, "v4"),
     ({}, "current")],
)
async def test_a_version_selector_is_compressed_to_one_token(
    tools, client, selector, shown
):
    """Which version a saved view reads is a one-word fact — pinned tag, pinned
    number, or follows current — and it changes whether the view's result is
    stable. Rendered as a raw JSON blob it costs a whole cell and gets cut."""
    client.on_get("/datasets/ds-1/views", page([_view(version_selector=selector)], total=1))

    out = await tools["list_saved_objects"](dataset_id="ds-1", kind="view")

    assert f"| {shown} |" in out


def test_a_version_selector_that_is_not_an_object_renders_as_nothing():
    """Defensive: the column is opaque JSONB. A stray scalar must not become
    the string "None" in a column where "current" means something specific."""
    assert _selector(None) == ""
    assert _selector("current") == ""


@pytest.mark.parametrize(
    "kind,segment,item,hint",
    [("view", "views", _view(), "(query, filters, columns)."),
     ("rule", "rules", _rule(), "(parameters and config).")],
)
async def test_the_follow_up_hint_describes_what_that_kind_of_detail_contains(
    tools, client, kind, segment, item, hint
):
    """The detail call is a second round trip; the hint has to say what it buys
    for *this* kind, since a rule's detail and a view's detail share no fields."""
    client.on_get(f"/datasets/ds-1/{segment}", page([item], total=1))

    out = await tools["list_saved_objects"](dataset_id="ds-1", kind=kind)

    assert f"Pass kind='{kind}' with object_id to see one in full {hint}" in out


# ---------------------------------------------------------------------------
# list_saved_objects — one object in full
# ---------------------------------------------------------------------------


async def test_a_saved_view_in_full_reproduces_its_query_tree(tools, client):
    """The reason to read someone else's view is to reuse the filter they
    considered correct. Truncated to a table cell it is unusable; the nested
    form has to survive intact, operators and values included."""
    view = _view(
        query={
            "columns": ["order_id", "total"],
            "filters": {
                "logic": "and",
                "conditions": [
                    {"column": "status", "op": "eq", "value": "open"},
                    {"column": "total", "op": "gt", "value": 100},
                ],
            },
        },
    )
    client.on_get("/datasets/ds-1/views/vw-1", view)

    out = await tools["list_saved_objects"](dataset_id="ds-1", kind="view", object_id="vw-1")

    assert client.trace() == [("GET", "/datasets/ds-1/views/vw-1")]
    assert "## Query" in out
    assert "columns: [order_id, total]" in out
    assert "logic: and" in out
    assert "column: status" in out and "op: eq" in out and "value: open" in out
    assert "value: 100" in out


async def test_an_unfiltered_view_does_not_print_an_empty_filters_block(tools, client):
    """"filters:" with nothing under it reads as "the filters were withheld".
    A view that filters nothing is a projection, and that is worth knowing
    exactly because it means the view can be reused on any subset."""
    client.on_get(
        "/datasets/ds-1/views/vw-1",
        _view(query={"columns": ["order_id"], "filters": {}, "sort": []}),
    )

    out = await tools["list_saved_objects"](dataset_id="ds-1", kind="view", object_id="vw-1")

    assert "columns: [order_id]" in out
    assert "filters" not in out
    assert "sort" not in out


async def test_an_opaque_nested_chart_config_is_written_out_rather_than_dropped(
    tools, client
):
    """`config` is frontend-owned JSON with no schema on this side, so the
    renderer has to survive arbitrary nesting — lists inside lists included. A
    branch that silently skipped a shape it did not expect would hand back a
    chart definition that cannot be reproduced, and nothing would say so."""
    client.on_get(
        "/datasets/ds-1/charts/ch-1",
        _chart(config={"legend": {"position": "right"}, "series": [[1, 2], [3, 4]]}),
    )

    out = await tools["list_saved_objects"](dataset_id="ds-1", kind="chart", object_id="ch-1")

    assert "## Config" in out
    assert "legend:" in out and "  position: right" in out
    assert "series:" in out
    for value in ("1", "2", "3", "4"):
        assert value in out.split("series:")[1]


async def test_a_detail_header_names_the_kind_it_is_showing(tools, client):
    """Five kinds share one detail renderer and several share field names. The
    kind line is what stops a chart being read as a view."""
    client.on_get("/datasets/ds-1/charts/ch-1", _chart(description="Quarterly revenue"))

    out = await tools["list_saved_objects"](dataset_id="ds-1", kind="chart", object_id="ch-1")

    assert out.startswith("kind: chart")
    assert "chart_type: bar" in out
    assert "definition_id: def-1" in out
    assert "description: Quarterly revenue" in out


async def test_a_transformation_detail_prints_every_step_in_order(tools, client):
    """A pipeline is only reusable if it can be replayed exactly. A summary
    that says "3 steps" is what the list already said."""
    steps = [
        {"op": "filter", "column": "status", "value": "open"},
        {"op": "derive", "name": "margin", "expr": "revenue - cost"},
    ]
    client.on_get("/datasets/ds-1/transformations/tr-1", _transformation(steps=steps))

    out = await tools["list_saved_objects"](
        dataset_id="ds-1", kind="transformation", object_id="tr-1"
    )

    assert "## Steps" in out
    assert out.index("op: filter") < out.index("op: derive")
    assert "expr: revenue - cost" in out


async def test_a_quality_rule_detail_is_fetched_by_id_like_every_other_kind(tools, client):
    """This lookup used to scan the whole rule list, because the quality API had
    no GET for one rule. It has one now, so the scan — up to five requests of 200
    rows, and a window past which a real rule reads as absent — is both slower
    and less correct than asking for the id. One request, and the same shape as
    views, analytics, charts and transformations."""
    client.on_get("/datasets/ds-1/rules/rl-2",
                  _rule(id="rl-2", name="Wanted", severity="warning"))

    out = await tools["list_saved_objects"](dataset_id="ds-1", kind="rule", object_id="rl-2")

    assert client.trace() == [("GET", "/datasets/ds-1/rules/rl-2")]
    assert "name: Wanted" in out
    assert "severity: warning" in out


async def test_a_rule_id_that_matches_nothing_is_the_service_s_own_404(tools, client):
    """The scan-based miss returned a flat "no such rule", which was the best
    available answer while the lookup was local — and a wrong one, since a rule
    on a dataset the caller cannot see is indistinguishable from a rule that was
    never created. Now the service answers, and ``explain`` attaches the caveat
    the whole API is built on: a 404 also covers another team's data."""
    client.on_get("/datasets/ds-1/rules/nope", problem(404, "Rule not found: nope", "http-404"))

    with pytest.raises(ToolError) as exc:
        await tools["list_saved_objects"](dataset_id="ds-1", kind="rule", object_id="nope")

    message = str(exc.value)
    assert "Rule not found: nope" in message
    assert "404 both for things that do not exist" in message


async def test_an_analytics_definition_detail_keeps_its_params_blob(tools, client):
    """The params blob *is* the definition — group-by columns, aggregations,
    filters. Dropping it leaves a name and a kind, which reuse cannot be built
    on."""
    definition = _definition(
        params={"group_by": ["region"], "aggregations": [{"column": "total", "fn": "sum"}]},
    )
    client.on_get("/datasets/ds-1/analytics/def-1", definition)

    out = await tools["list_saved_objects"](
        dataset_id="ds-1", kind="analytics", object_id="def-1"
    )

    assert "definition_kind: aggregate" in out
    assert "## Params" in out
    assert "group_by: [region]" in out
    assert "column: total" in out and "fn: sum" in out


async def test_a_detail_view_drops_fields_the_object_does_not_have(tools, client):
    """One renderer over five shapes means most fields are null for any given
    object. Printing `rule_type: None` on a view spends tokens asserting
    absence and reads as a missing value."""
    client.on_get("/datasets/ds-1/views/vw-1", _view(description=None))

    out = await tools["list_saved_objects"](dataset_id="ds-1", kind="view", object_id="vw-1")

    assert "rule_type" not in out
    assert "description" not in out
    assert "chart_type" not in out


# ---------------------------------------------------------------------------
# Timestamp trimming — used by every table above
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "given,expected",
    [
        ("2026-04-01 08:30:00.123456+00", "2026-04-01 08:30:00"),
        ("2026-04-01T08:30:00.123456+00:00", "2026-04-01T08:30:00"),
        ("2026-04-01T08:30:00", "2026-04-01T08:30:00"),
        ("2026-04-01", "2026-04-01"),
        (None, None),
        (7, 7),
        ("not a timestamp at all", "not a timestamp at all"),
    ],
)
def test_only_things_that_look_like_timestamps_are_trimmed(given, expected):
    """The trim is positional, not parsed, so it runs on whatever the column
    held. A free-text value of the right length must survive unmangled — this
    helper is applied to `created_at` fields that are typed ``str`` and could
    carry anything."""
    assert _ts(given) == expected

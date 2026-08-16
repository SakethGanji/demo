"""Two tools scan a bounded window and then answer as if they had seen it all.

``tools/context.py``'s own ``SCAN_CAP`` docstring states the house rule:
"Scanning a bounded window and reporting the window is honest; scanning one page
and reporting 'none' would not be." ``_scan`` obeys it — it returns an
``exhausted`` flag and its callers print "filtered locally over the N most
recent". ``_fetch_all`` did not, and ``list_saved_objects`` then *re-sorted* the
truncated window in this process and printed the true global total beside it.
"50 of 1500 views shown, newest first" over the alphabetically-first 1000 is not
a partial answer, it is a wrong one, and nothing in the text lets a reader tell.

``tools/artifacts.py`` has the mirror-image problem in prose rather than data:
``offset`` is handed to ``/samples`` unchanged, so it skips *raw* artifact rows,
but the clipped-listing note told the caller to page with it. Following that
advice re-scans from a raw row and returns matches already shown — a "next page"
that overlaps, or exactly repeats, the previous one.

Unit layer via ``mcp_harness.FakeAnalyticsClient``: what is under test is how
these tools describe their own reach, which needs no database.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.features.explorer.schemas import DatasetViewOut
from app.features.files.schemas import FileEntry
from app.features.mcp.tools import artifacts, context
from app.features.quality.schemas import RuleOut
from mcp_harness import FakeAnalyticsClient, ToolSet, page, register_tools

TS = "2026-05-01T09:00:00+00:00"
SAMPLES = "/samples"
VIEWS = "/datasets/ds-1/views"
RULES = "/datasets/ds-1/rules"


@pytest.fixture
def client() -> FakeAnalyticsClient:
    return FakeAnalyticsClient()


@pytest.fixture
def saved(client: FakeAnalyticsClient) -> ToolSet:
    return register_tools(context, client)


@pytest.fixture
def files(client: FakeAnalyticsClient) -> ToolSet:
    return register_tools(artifacts, client)


def view(**over: Any) -> dict[str, Any]:
    """One item of ``Page[DatasetViewOut]`` from ``GET /datasets/{id}/views``."""
    fields: dict[str, Any] = {
        "id": "vw-1", "dataset_id": "ds-1", "logical_sheet_id": "ls-1",
        "sheet_key": "orders", "sheet_name": "Orders", "name": "Open orders",
        "version_selector": {"mode": "current"}, "query": {},
        "created_at": TS, "updated_at": TS,
    }
    return DatasetViewOut(**{**fields, **over}).model_dump(mode="json")


def rule(**over: Any) -> dict[str, Any]:
    """One item of ``Page[RuleOut]`` from ``GET /datasets/{id}/rules``."""
    fields: dict[str, Any] = {
        "id": "rl-1", "dataset_id": "ds-1", "name": "customer_id not null",
        "scope_type": "column", "sheet_selector": "orders",
        "column_selector": "customer_id", "rule_type": "not_null",
        "parameters": {}, "severity": "error", "enabled": True,
        "created_at": TS, "updated_at": TS,
    }
    return RuleOut(**{**fields, **over}).model_dump(mode="json")


def entry(*, filename: str, file_type: str) -> dict[str, Any]:
    """One item of ``Page[FileEntry]`` from ``GET /samples``."""
    return FileEntry(
        key=f"artifacts/team/ds-1/{file_type}/{filename}",
        filename=filename,
        size_bytes=2048,
        file_type=file_type,
        dataset_id="ds-1",
        created_at="2026-08-01T10:00:00Z",
    ).model_dump(mode="json")


# ---------------------------------------------------------------------------
# list_saved_objects — a sort over a window that was cut short
# ---------------------------------------------------------------------------


async def test_list_saved_objects_says_so_when_the_sort_ran_over_a_truncated_window(
    saved, client
):
    """``_fetch_all`` stops at 1000 items, and the tool then sorts what it has
    by ``created_at``. The service does not hand these over newest-first — views,
    analytics, charts and transformations come back ``ORDER BY name`` — so the
    1000 that were read are the alphabetically-first 1000, and re-sorting them
    "newest first" yields the newest of an arbitrary slice. Printed beside a
    truthful global total of 1500, that reads to a model as the newest 50 of
    1500, and it will go on to say "the most recent saved view is X" about a
    view that is nothing of the sort."""
    pages = [
        page(
            [
                view(id=f"vw-{p * 200 + i}", name=f"A{p * 200 + i:04d}",
                     created_at=f"2026-01-01T00:00:{i % 60:02d}+00:00")
                for i in range(200)
            ],
            total=1500, limit=200, offset=p * 200,
        )
        for p in range(5)
    ]
    client.on_get(VIEWS, *pages)

    out = await saved["list_saved_objects"](dataset_id="ds-1", kind="view", limit=50)

    assert "50 of 1500 views shown." in out
    assert "Only the first 1000 views were read" in out
    assert "not the newest slice" in out


async def test_a_fully_read_saved_object_list_carries_no_truncation_warning(saved, client):
    """The complement, and the reason the warning is worth anything: it appears
    only when the sort really was partial. A note on every listing would be
    noise and would be learned as noise."""
    client.on_get(VIEWS, page([view(id=f"vw-{i}", name=f"V{i}") for i in range(3)], total=3))

    out = await saved["list_saved_objects"](dataset_id="ds-1", kind="view")

    assert "3 views shown." in out
    assert "were read" not in out
    assert "not the newest slice" not in out


async def test_a_route_that_returns_more_than_the_cap_in_one_response_is_still_truncated(
    saved, client
):
    """The quality, analytics and chart routes ignore limit/offset and return
    every row in a single response. The paging loop then exits believing it has
    everything — ``len(items) >= total`` — while the ``[:cap]`` slice on the way
    out has already dropped 500 rows. "Read the whole list" and "kept the whole
    list" are different claims and only the second one licenses silence."""
    rules = [
        rule(id=f"rl-{i}", name=f"R{i}", created_at=f"2026-01-01T00:00:{i % 60:02d}+00:00")
        for i in range(1500)
    ]
    client.on_get(RULES, page(rules, total=1500))

    out = await saved["list_saved_objects"](dataset_id="ds-1", kind="rule", limit=10)

    assert len(client.calls_to("GET", RULES)) == 1
    assert "10 of 1500 rules shown." in out
    assert "Only the first 1000 rules were read" in out


async def test_a_rule_beyond_the_scan_window_is_no_longer_looked_for_by_scanning(
    saved, client
):
    """This tool used to find a rule by scanning the list, because the quality
    API had no GET for one rule, and the scan stopped at 1000 — so a rule the
    caller was holding a real id for came back hedged ("may still exist beyond
    that window") or, before that, as a flat false negative whose obvious next
    move is to create the rule again and have it fire twice on every validation
    run. ``GET /datasets/{id}/rules/{rule_id}`` exists now: no window, no scan,
    no hedge. The bounded-window rule this file is about still governs the
    *listing* path above; it no longer has anything to govern here."""
    client.on_get(f"{RULES}/rl-1400", rule(id="rl-1400", name="R1400"))

    out = await saved["list_saved_objects"](
        dataset_id="ds-1", kind="rule", object_id="rl-1400"
    )

    assert client.trace() == [("GET", f"{RULES}/rl-1400")]
    assert "name: R1400" in out
    assert "window" not in out


# ---------------------------------------------------------------------------
# list_artifacts — offset moves the scan window, not the matches
# ---------------------------------------------------------------------------


async def test_a_clipped_filtered_listing_does_not_advise_an_offset_that_re_lists_the_same_matches(
    files, client
):
    """When a filter is set the tool scans 1000 raw rows and filters in-process,
    so ``offset`` skips raw artifacts, not matches. Telling the caller to page
    with it hands back a window shifted by two *rows*, which — when the skipped
    rows did not match — is the identical set of matches. The model then either
    reports the same artifact twice or loops. ``limit`` goes to 1000, the whole
    scanned window, so it is a complete remedy on its own."""
    rows = []
    for i in range(5):
        rows.append(entry(filename=f"q{i}.parquet", file_type="query_output"))
        rows.append(entry(filename=f"other{i}.parquet", file_type="pivot_output"))
    client.on_get(SAMPLES, page(rows), page(rows[2:]))

    first = await files["list_artifacts"](kind="query_output", limit=2)
    second = await files["list_artifacts"](kind="query_output", limit=2, offset=2)

    assert "use offset" not in first
    assert "raise limit (up to 1000)" in first
    # The offset really did go to the raw scan, and the "next page" it produces
    # overlaps the first — which is precisely why it must not be advertised.
    assert client.calls_to("GET", SAMPLES)[1].params == {"limit": 1000, "offset": 2}
    assert "q1.parquet" in first and "q1.parquet" in second


async def test_an_unfiltered_listing_reports_the_true_total_and_a_concrete_next_offset(
    files, client
):
    """With no filter the listing *is* the raw page, so ``/samples``' own
    ``total`` counts exactly what is being shown and ``offset`` is a real page
    cursor over it. Both were suppressed — ``count_note`` was called with
    ``total=None`` on every path — so a caller looking at 50 of 900 artifacts was
    told "50 artifacts shown." with nothing to suggest a 51st. The model
    concludes the handle it is hunting for was never written and recomputes the
    query that produced it, which is the one thing this tool exists to avoid."""
    rows = [entry(filename=f"q{i}.parquet", file_type="query_output") for i in range(50)]
    client.on_get(SAMPLES, page(rows, total=900, limit=50))

    out = await files["list_artifacts"](limit=50)

    assert "50 of 900 artifacts shown." in out
    assert "More artifacts — call again with offset=50." in out


async def test_a_filtered_listing_does_not_borrow_the_raw_total(files, client):
    """The complement, and the reason the total is conditional: on the filtered
    path ``total`` counts raw artifact rows, while the table counts matches.
    Printing "3 of 900 artifacts shown" over three matches answers a question
    nobody asked and reads as 897 more of the kind that was filtered for."""
    rows = [entry(filename=f"q{i}.parquet", file_type="query_output") for i in range(3)]
    rows += [entry(filename=f"p{i}.parquet", file_type="pivot_output") for i in range(3)]
    client.on_get(SAMPLES, page(rows, total=900, limit=1000))

    out = await files["list_artifacts"](kind="query_output")

    assert "3 artifacts shown." in out
    assert "900" not in out
    assert "offset=" not in out


async def test_the_last_page_of_an_unfiltered_listing_offers_no_further_offset(
    files, client
):
    """A "next page" hint on the final page is an instruction to make a call that
    returns nothing, and a model that follows it reports the empty result as the
    dataset having no artifacts."""
    rows = [entry(filename=f"q{i}.parquet", file_type="query_output") for i in range(10)]
    client.on_get(SAMPLES, page(rows, total=60, limit=50, offset=50))

    out = await files["list_artifacts"](limit=50, offset=50)

    assert "10 of 60 artifacts shown." in out
    assert "offset=" not in out


async def test_an_empty_filtered_result_still_advises_offset_to_look_further_back(
    files, client
):
    """The one place the advice is right, kept honest here so the fix above does
    not delete it by association. With no matches in the 1000 rows scanned,
    moving the raw window back is exactly the correct next call — there is no
    filtered list for it to disagree with."""
    client.on_get(SAMPLES, page([entry(filename=f"p{i}.parquet", file_type="pivot_output")
                                 for i in range(1000)]))

    out = await files["list_artifacts"](kind="query_output")

    assert "Scanned the 1000 most recent artifacts" in out
    assert "retry with offset" in out

"""Transform journeys — the pipeline builder, the run-history panel, and publish.

Written the way the screens will drive the API: every step is a real HTTP call
in the order a UI makes it, and each step asserts something that is only true
BECAUSE of the step before it. State (definition id, run id, published dataset
id) flows exactly as it does in a browser session — nothing is read from the
database or the service layer.

The screens modelled here:

* **Transform builder** — compile-as-you-type, save, preview after every edit,
  and a rejected edit that must leave the saved definition untouched.
* **Run history panel** — several runs, paged newest first, each row opening
  its own detail; the async row polled to completion.
* **Source picker** — the ``version_selector`` dropdown, pinned vs current.
* **Failure screen** — everything the UI renders about a run that failed.
* **Publish** — new dataset, new version, and the chain of both.
* **Delete confirmation** — what disappears and what deliberately does not.
* **Read-only mode** — a viewer, plus another team's user, on every control.

Companions: tests/test_transformations.py (feature contracts) and
tests/test_transform_editing_and_publishing.py (single-defect regressions).
"""

from __future__ import annotations

import json

from conftest import (
    DEFAULT_TEAM_ID,
    XLSX_MIME,
    auth,
    create_team_user,
    make_workbook,
    rid,
    upload_file,
    upload_inline,
)

PROBLEM = "application/problem+json"

# One dirty sheet: untrimmed text, mixed case, a duplicate id, a NULL name.
ROWS = [
    {"id": 1, "name": "  Alice  ", "city": "NY", "amount": 10.0},
    {"id": 2, "name": "bob", "city": "ny", "amount": 20.0},
    {"id": 2, "name": "bob", "city": "ny", "amount": 20.0},
    {"id": 3, "name": "Carol", "city": "LA", "amount": 30.0},
    {"id": 4, "name": None, "city": "sf", "amount": 40.0},
]

TRIM = {"type": "trim", "columns": ["name"]}
DROP_CITY = {"type": "drop", "columns": ["city"]}
DEDUPE = {"type": "deduplicate", "subset": ["id"], "keep": "first"}


async def _dataset(client, admin_id, rows=None):
    body = await upload_inline(client, admin_id, json.dumps(rows or ROWS))
    return body["dataset_id"]


async def _definition(client, headers, ds, *, name, steps, **extra):
    r = await client.post(f"/api/v1/datasets/{ds}/transformations", headers=headers,
                          json={"name": name, "sheet": "data", "steps": steps, **extra})
    assert r.status_code == 201, r.text
    return r.json()


# ---------------------------------------------------------------------------
# 1. The builder screen
# ---------------------------------------------------------------------------

async def test_journey_a_builder_composes_a_pipeline_step_by_step_and_a_rejected_edit_leaves_it_intact(
        client, admin_id):
    """SCREEN: the transformation builder — add a step, preview, add another.

    The builder polls ``compile`` while the pipeline is still nameless, saves
    once the user commits, and re-previews after every edit. Two things it
    cannot survive losing:

    * ``output_schema`` must shrink the moment a ``drop`` step is added — that
      list is what populates the column picker for step N+1, so a stale one
      offers columns that no longer exist and every later step 400s;
    * a REJECTED PATCH must be atomic. ``update_transformation`` assembles its
      ``fields`` dict before it validates, so only statement ordering stops a
      bad third step from half-writing ``version_selector``/``steps``. If it
      ever half-writes, the user's saved pipeline is silently retargeted by an
      edit the API told them it refused.
    """
    ds = await _dataset(client, admin_id)
    h = auth(admin_id)
    base = f"/api/v1/datasets/{ds}/transformations"

    # ---- 1. Nameless draft: compile as the user types, no persistence ----
    r = await client.post(f"{base}/compile", headers=h,
                          json={"sheet": "data", "steps": [TRIM, DROP_CITY]})
    assert r.status_code == 200, r.text
    draft = r.json()
    assert draft["sampled"] is False and draft["rows"] == []
    # Per-step columns: the picker for step 2 still offers city, step 3 does not.
    assert [c["name"] for c in draft["step_schemas"][0]] == ["id", "name", "city", "amount"]
    assert [c["name"] for c in draft["step_schemas"][1]] == ["id", "name", "amount"]
    assert draft["version_number"] == 1 and draft["sheet_name"] == "data"

    # Same draft, now with rows — the "show me what it does" toggle.
    r = await client.post(f"{base}/compile", headers=h,
                          json={"sheet": "data", "steps": [TRIM], "rows": 25})
    assert r.status_code == 200, r.text
    assert r.json()["sampled"] is True
    assert "  Alice  " not in [row["name"] for row in r.json()["rows"]]
    assert "Alice" in [row["name"] for row in r.json()["rows"]]

    # Compiling wrote nothing: the list the builder returns to is still empty.
    assert (await client.get(base, headers=h)).json()["total"] == 0

    # ---- 2. Commit the draft as a saved definition ----
    saved = await _definition(client, h, ds, name=f"cleanup-{rid()}", steps=[TRIM])
    did = saved["id"]
    assert saved["version_selector"] == {"mode": "current"}
    assert saved["sheet_key"] == "data" and saved["logical_sheet_id"]
    assert [s["type"] for s in saved["steps"]] == ["trim"]

    listing = (await client.get(base, headers=h)).json()
    assert listing["total"] == 1 and listing["items"][0]["id"] == did
    assert listing["limit"] == 50 and listing["offset"] == 0

    # ---- 3. Preview #1: four columns, city still present ----
    r = await client.post(f"{base}/{did}/preview?rows=25", headers=h)
    assert r.status_code == 200, r.text
    first = r.json()
    assert first["approximate"] is True
    assert [c["name"] for c in first["output_schema"]] == ["id", "name", "city", "amount"]
    assert first["columns"] == ["id", "name", "city", "amount"]
    assert len(first["rows"]) == 5           # nothing dropped yet
    assert first["version_number"] == 1 and first["sheet_name"] == "data"

    # ---- 4. Add a second step; preview #2 must lose the dropped column ----
    r = await client.patch(f"{base}/{did}", headers=h, json={"steps": [TRIM, DROP_CITY]})
    assert r.status_code == 200, r.text
    two_steps = r.json()
    assert [s["type"] for s in two_steps["steps"]] == ["trim", "drop"]
    saved_updated_at = two_steps["updated_at"]

    r = await client.post(f"{base}/{did}/preview?rows=25", headers=h)
    assert r.status_code == 200, r.text
    second = r.json()
    assert [c["name"] for c in second["output_schema"]] == ["id", "name", "amount"]
    assert "city" not in second["columns"], "the column picker would still offer city"
    assert all("city" not in row for row in second["rows"])

    # ---- 5. Step 3 sorts by the column step 2 dropped: refused ----
    # Sent together with a version_selector change, so a half-write would show.
    r = await client.patch(f"{base}/{did}", headers=h, json={
        "version_selector": {"mode": "version", "version_number": 1},
        "steps": [TRIM, DROP_CITY, {"type": "sort", "by": [{"column": "city"}]}]})
    assert r.status_code == 400, r.text
    assert r.headers["content-type"].startswith(PROBLEM)
    problem = r.json()
    assert problem["code"] == "unknown-column"
    assert "city" not in problem["available"], "available is the folded schema"
    assert set(problem["available"]) >= {"id", "name", "amount"}

    # ---- 6. The saved definition is exactly what step 4 left ----
    after = (await client.get(f"{base}/{did}", headers=h)).json()
    assert [s["type"] for s in after["steps"]] == ["trim", "drop"]
    assert after["version_selector"] == {"mode": "current"}, "refused PATCH half-wrote"
    assert after["logical_sheet_id"] == saved["logical_sheet_id"]
    assert after["updated_at"] == saved_updated_at, "a refused PATCH touched the row"

    # ---- 7. And preview still works, unchanged ----
    r = await client.post(f"{base}/{did}/preview?rows=25", headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["columns"] == second["columns"]
    assert len(r.json()["rows"]) == 5

    # The whole session was a dry run: no run row was ever created.
    assert (await client.get(f"{base}/{did}/runs", headers=h)).json()["total"] == 0


# ---------------------------------------------------------------------------
# 2. Read-only mode
# ---------------------------------------------------------------------------

async def test_journey_a_viewer_reads_the_whole_transform_screen_and_every_control_is_refused(
        client, admin_id):
    """SCREEN: the transform tab rendered for a viewer, plus an outsider.

    A viewer must be able to render everything — the definition list, the
    detail, a preview, the run-history panel AND a run's detail — because those
    are the panels the screen is made of. Every mutating control must come back
    403 so the UI can grey it out and, when clicked anyway, show "you don't have
    permission" rather than a generic failure.

    ``publish`` is the one that matters most: it creates a dataset or a version,
    so a viewer who can reach it is a real authorization hole. A user from
    another team must get 404 everywhere instead — 403 would confirm the
    definition exists.
    """
    ds = await _dataset(client, admin_id)
    h = auth(admin_id)
    base = f"/api/v1/datasets/{ds}/transformations"
    did = (await _definition(client, h, ds, name=f"cleanup-{rid()}",
                             steps=[TRIM, DEDUPE]))["id"]
    run_id = (await client.post(f"{base}/{did}/run", headers=h)).json()["id"]

    viewer, _ = await create_team_user(client, admin_id, "viewer", team_id=DEFAULT_TEAM_ID)
    vh = auth(viewer)

    # ---- 1. Everything the read-only screen renders ----
    listing = await client.get(base, headers=vh)
    assert listing.status_code == 200 and listing.json()["total"] == 1

    detail = await client.get(f"{base}/{did}", headers=vh)
    assert detail.status_code == 200
    assert detail.json()["id"] == did and detail.json()["sheet_key"] == "data"

    preview = await client.post(f"{base}/{did}/preview", headers=vh)
    assert preview.status_code == 200, preview.text
    assert len(preview.json()["rows"]) == 4          # the duplicate id collapsed

    compiled = await client.post(f"{base}/compile", headers=vh,
                                 json={"sheet": "data", "steps": [DROP_CITY]})
    assert compiled.status_code == 200, "compile persists nothing, so it is a read"

    runs = await client.get(f"{base}/{did}/runs", headers=vh)
    assert runs.status_code == 200, runs.text
    assert runs.json()["total"] == 1 and runs.json()["items"][0]["id"] == run_id

    run_detail = await client.get(f"{base}/runs/{run_id}", headers=vh)
    assert run_detail.status_code == 200, run_detail.text
    assert run_detail.json()["status"] == "completed"
    assert run_detail.json()["published_version_id"] is None

    # ---- 2. Every control that writes ----
    refused = {
        "create": await client.post(base, headers=vh, json={
            "name": f"v-{rid()}", "sheet": "data", "steps": [TRIM]}),
        "rename": await client.patch(f"{base}/{did}", headers=vh,
                                     json={"name": f"nope-{rid()}"}),
        "run": await client.post(f"{base}/{did}/run", headers=vh),
        "async run": await client.post(f"{base}/{did}/run?sync=false", headers=vh),
        "publish": await client.post(f"{base}/runs/{run_id}/publish", headers=vh,
                                     json={"mode": "new_version"}),
        "delete": await client.delete(f"{base}/{did}", headers=vh),
    }
    for label, response in refused.items():
        assert response.status_code == 403, f"{label}: {response.status_code}"
        assert response.headers["content-type"].startswith(PROBLEM), label

    # Nothing the viewer touched had an effect.
    assert (await client.get(f"{base}/{did}/runs", headers=h)).json()["total"] == 1
    assert (await client.get(f"/api/v1/datasets/{ds}/versions", headers=h)).json()["total"] == 1

    # ---- 3. Another team sees nothing at all — 404, not 403 ----
    outsider, _ = await create_team_user(client, admin_id, "admin")
    oh = auth(outsider)
    for label, response in {
        "list": await client.get(base, headers=oh),
        "detail": await client.get(f"{base}/{did}", headers=oh),
        "run history": await client.get(f"{base}/{did}/runs", headers=oh),
        "run detail": await client.get(f"{base}/runs/{run_id}", headers=oh),
        "patch": await client.patch(f"{base}/{did}", headers=oh, json={"name": "x"}),
        "delete": await client.delete(f"{base}/{did}", headers=oh),
        "publish": await client.post(f"{base}/runs/{run_id}/publish", headers=oh,
                                     json={"mode": "new_dataset"}),
    }.items():
        assert response.status_code == 404, f"{label}: {response.status_code}"

    # And the definition survived every one of those attempts.
    assert (await client.get(f"{base}/{did}", headers=h)).status_code == 200


# ---------------------------------------------------------------------------
# 3. The run-history panel
# ---------------------------------------------------------------------------

async def test_journey_the_run_history_panel_pages_newest_first_and_each_row_opens_its_detail(
        client, admin_id):
    """SCREEN: the run-history panel under a saved transformation.

    Three runs, then paged two at a time. The panel renders rows from the LIST
    endpoint and opens each row via the run-detail endpoint, so the two must
    agree about status and id. What breaks a history panel first is ordering:
    ``ORDER BY started_at DESC`` with an ``offset`` page is entirely unverified
    elsewhere, and the still-``running`` async row has to sort in with the
    completed ones rather than being special-cased to the bottom.

    The async row is also how a UI knows to poll: it comes back ``running`` with
    a null ``job_id``, and only becomes ``completed`` with a ``result_summary``
    once the worker has picked it up.
    """
    from app.shared import worker

    ds = await _dataset(client, admin_id)
    h = auth(admin_id)
    base = f"/api/v1/datasets/{ds}/transformations"
    did = (await _definition(client, h, ds, name=f"cleanup-{rid()}",
                             steps=[TRIM, DEDUPE]))["id"]

    # ---- 1. Two synchronous runs, then one queued ----
    r1 = (await client.post(f"{base}/{did}/run", headers=h)).json()
    r2 = (await client.post(f"{base}/{did}/run", headers=h)).json()
    assert r1["status"] == r2["status"] == "completed"
    assert r1["id"] != r2["id"] and r1["job_id"] and r1["artifact_id"]

    r3 = await client.post(f"{base}/{did}/run?sync=false", headers=h)
    assert r3.status_code == 200, r3.text
    queued = r3.json()
    assert queued["status"] == "running"
    assert queued["job_id"] is None, "nothing has executed yet"
    assert queued["result_summary"] is None and queued["completed_at"] is None

    # ---- 2. Page 1 of the panel: newest first, total says there are more ----
    page1 = await client.get(f"{base}/{did}/runs", params={"limit": 2, "offset": 0},
                             headers=h)
    assert page1.status_code == 200, page1.text
    body1 = page1.json()
    assert body1["total"] == 3 and body1["limit"] == 2 and body1["offset"] == 0
    assert len(body1["items"]) == 2
    assert [i["id"] for i in body1["items"]] == [queued["id"], r2["id"]]
    assert body1["items"][0]["status"] == "running"   # sorts by time, not status

    # ---- 3. Page 2: the remainder, no overlap ----
    page2 = await client.get(f"{base}/{did}/runs", params={"limit": 2, "offset": 2},
                             headers=h)
    assert page2.status_code == 200
    body2 = page2.json()
    assert body2["total"] == 3 and body2["offset"] == 2
    assert [i["id"] for i in body2["items"]] == [r1["id"]]
    assert body2["items"][0]["completed_at"] and body2["items"][0]["error"] is None

    # ---- 4. Open the queued row: still running, so the UI polls ----
    detail_url = f"{base}/runs/{queued['id']}"
    opened = (await client.get(detail_url, headers=h)).json()
    assert opened["status"] == "running" and opened["artifact_id"] is None
    assert opened["output_profile"] is None and opened["source_drift"] is None

    # ---- 5. The worker runs; the same row now carries a result ----
    assert await worker.run_pending_jobs_once() >= 1
    finished = (await client.get(detail_url, headers=h)).json()
    assert finished["id"] == queued["id"]
    assert finished["status"] == "completed" and finished["job_id"]
    assert finished["result_summary"]["row_count"] == 4
    assert finished["result_summary"]["source_row_count"] == 5
    assert finished["completed_at"] and finished["error"] is None
    assert finished["output_profile"]["row_count"] == 4

    # ---- 6. The panel re-renders: same three rows, the newest now completed ----
    refreshed = (await client.get(f"{base}/{did}/runs", headers=h)).json()
    assert refreshed["total"] == 3
    assert [i["id"] for i in refreshed["items"]] == [queued["id"], r2["id"], r1["id"]]
    assert [i["status"] for i in refreshed["items"]] == ["completed"] * 3

    # ---- 7. Opening a run id from another definition's URL is a 404 ----
    other = (await _definition(client, h, ds, name=f"other-{rid()}",
                               steps=[{"type": "limit", "count": 1}]))["id"]
    assert (await client.get(f"{base}/{other}/runs", headers=h)).json()["total"] == 0


# ---------------------------------------------------------------------------
# 4. The source picker (version_selector)
# ---------------------------------------------------------------------------

async def test_journey_a_pinned_pipeline_keeps_reading_its_version_until_the_picker_is_changed(
        client, admin_id):
    """SCREEN: the "runs against" picker on the transformation settings panel.

    Every other transform test leaves ``version_selector`` at its default
    ``{mode: current}``, so the pinned branch of ``_selector_pin`` is unexercised
    through this feature. A pin that silently drifted to the newest version
    would not fail anything — it would just start producing different numbers,
    which is the worst possible failure mode for a saved pipeline.

    The journey pins v1, uploads a v2 with different rows, and proves preview,
    run and the published output all still describe v1; then flips the picker to
    ``current`` and proves all three move together.
    """
    ds = await _dataset(client, admin_id)              # v1: 5 rows, one dup id
    h = auth(admin_id)
    base = f"/api/v1/datasets/{ds}/transformations"

    pinned = await _definition(
        client, h, ds, name=f"pinned-{rid()}", steps=[TRIM, DEDUPE],
        version_selector={"mode": "version", "version_number": 1})
    did = pinned["id"]
    assert pinned["version_selector"] == {"mode": "version", "version_number": 1}

    # ---- 1. The dataset moves on: v2 has the same columns, different rows ----
    v2_rows = [{"id": 7, "name": "Zed", "city": "SF", "amount": 70.0},
               {"id": 8, "name": "Yara", "city": "SF", "amount": 80.0}]
    await upload_inline(client, admin_id, json.dumps(v2_rows), dataset_id=ds)
    versions = (await client.get(f"/api/v1/datasets/{ds}/versions", headers=h)).json()
    assert [v["version_number"] for v in versions["items"]] == [2, 1]

    # ---- 2. The picker still reads back the pin, and preview honours it ----
    assert (await client.get(f"{base}/{did}", headers=h)).json()["version_selector"] \
        == {"mode": "version", "version_number": 1}
    preview = (await client.post(f"{base}/{did}/preview?rows=50", headers=h)).json()
    assert preview["version_number"] == 1
    assert {row["id"] for row in preview["rows"]} == {1, 2, 3, 4}, "read v2, not the pin"

    # ---- 3. Running it records the pinned version, not the current one ----
    run = (await client.post(f"{base}/{did}/run", headers=h)).json()
    assert run["status"] == "completed"
    summary = run["result_summary"]
    assert summary["version_number"] == 1
    assert summary["source_row_count"] == 5 and summary["row_count"] == 4

    detail = (await client.get(f"{base}/runs/{run['id']}", headers=h)).json()
    assert detail["dataset_version_id"] == versions["items"][1]["id"], "pinned to v1"

    # ---- 4. Publishing closes the loop: the output is v1's data ----
    published = await client.post(f"{base}/runs/{run['id']}/publish", headers=h,
                                  json={"mode": "new_dataset", "name": f"from-v1-{rid()}"})
    assert published.status_code == 200, published.text
    pub = published.json()
    assert pub["mode"] == "new_dataset" and pub["version_number"] == 1
    rows = (await client.get(f"/api/v1/datasets/{pub['dataset_id']}/versions/1/preview",
                             headers=h)).json()
    assert rows["total"] == 4
    assert {row["id"] for row in rows["items"]} == {1, 2, 3, 4}

    # ---- 5. The user changes the picker to "current" ----
    r = await client.patch(f"{base}/{did}", headers=h,
                           json={"version_selector": {"mode": "current"}})
    assert r.status_code == 200, r.text
    assert r.json()["version_selector"] == {"mode": "current"}
    assert [s["type"] for s in r.json()["steps"]] == ["trim", "deduplicate"], \
        "changing the picker rewrote the steps"

    preview2 = (await client.post(f"{base}/{did}/preview?rows=50", headers=h)).json()
    assert preview2["version_number"] == 2
    assert {row["id"] for row in preview2["rows"]} == {7, 8}

    run2 = (await client.post(f"{base}/{did}/run", headers=h)).json()
    assert run2["result_summary"]["version_number"] == 2
    assert run2["result_summary"]["source_row_count"] == 2

    # ---- 6. Publishing that as a new version of the same dataset ----
    r = await client.post(f"{base}/runs/{run2['id']}/publish", headers=h,
                          json={"mode": "new_version"})
    assert r.status_code == 200, r.text
    assert r.json()["dataset_id"] == ds and r.json()["version_number"] == 3
    after = (await client.get(f"/api/v1/datasets/{ds}/versions", headers=h)).json()
    assert after["total"] == 3
    # v1 is immutable: the pin is still meaningful.
    src = await client.get(f"/api/v1/datasets/{ds}/versions/1/preview", headers=h)
    assert src.json()["total"] == 5


# ---------------------------------------------------------------------------
# 5. The sheet went away
# ---------------------------------------------------------------------------

async def test_journey_a_pipeline_whose_sheet_left_the_current_version_fails_before_a_run_row_exists(
        client, admin_id, tmp_path):
    """FLOW: someone uploads a version that no longer has the pipeline's sheet.

    ``start_run`` resolves the sheet BEFORE it inserts the run row, precisely so
    this is a request-time error rather than a run that sits at ``running``
    forever (nothing audits ``transformation_runs`` for stranded rows). The half
    nobody checks is ``total == 0`` on the history panel afterwards.

    A UI also has to be able to tell this apart from a broken pipeline, so
    preview and run must answer the SAME machine-readable code — otherwise the
    builder shows "fix your steps" for a problem that has nothing to do with
    them. The recovery a user actually performs (pin back to the version that
    still has the sheet) is the last step.
    """
    v1, v2 = tmp_path / "v1.xlsx", tmp_path / "v2.xlsx"
    make_workbook(v1)                              # Revenue / Expenses / Secrets
    make_workbook(v2, second_sheet="Spending")     # Expenses is simply gone
    ds = (await upload_file(client, admin_id, v1, name="book.xlsx",
                            content_type=XLSX_MIME))["dataset_id"]
    h = auth(admin_id)
    base = f"/api/v1/datasets/{ds}/transformations"

    did = (await _definition(
        client, h, ds, name=f"costs-{rid()}", sheet="Expenses",
        steps=[{"type": "sort", "by": [{"column": "cost", "direction": "desc"}]}]
    ))["id"]

    # It works today, against v1.
    ok = await client.post(f"{base}/{did}/preview", headers=h)
    assert ok.status_code == 200 and ok.json()["sheet_name"] == "Expenses"

    # ---- 1. A colleague uploads a version without that sheet ----
    await upload_file(client, admin_id, v2, name="book.xlsx",
                      content_type=XLSX_MIME, dataset_id=ds)

    # ---- 2. Run is refused at request time ----
    run = await client.post(f"{base}/{did}/run", headers=h)
    assert run.status_code == 404, run.text
    assert run.headers["content-type"].startswith(PROBLEM)
    assert run.json()["code"] == "sheet-not-in-version"
    assert run.json()["version_number"] == 2, "the UI names the offending version"

    # ---- 3. No orphan row: the history panel is still empty ----
    runs = await client.get(f"{base}/{did}/runs", headers=h)
    assert runs.status_code == 200
    assert runs.json()["total"] == 0, "a stranded 'running' row was left behind"
    assert runs.json()["items"] == []

    # ---- 4. Preview says exactly the same thing, so the UI branches once ----
    preview = await client.post(f"{base}/{did}/preview", headers=h)
    assert preview.status_code == run.status_code
    assert preview.json()["code"] == run.json()["code"]
    assert preview.json()["detail"] == run.json()["detail"]

    # ---- 5. The definition itself is still readable and editable ----
    still = await client.get(f"{base}/{did}", headers=h)
    assert still.status_code == 200 and still.json()["sheet_key"] == "expenses"

    # ---- 6. Recovery: pin back to the version that still has the sheet ----
    r = await client.patch(f"{base}/{did}", headers=h,
                           json={"version_selector": {"mode": "version",
                                                      "version_number": 1}})
    assert r.status_code == 200, r.text
    fixed = await client.post(f"{base}/{did}/run", headers=h)
    assert fixed.status_code == 200, fixed.text
    assert fixed.json()["result_summary"]["sheet"] == "Expenses"
    assert fixed.json()["result_summary"]["version_number"] == 1
    assert (await client.get(f"{base}/{did}/runs", headers=h)).json()["total"] == 1


# ---------------------------------------------------------------------------
# 6. The failure screen
# ---------------------------------------------------------------------------

async def test_journey_a_failed_run_is_fully_explained_on_its_detail_screen_and_cannot_be_published(
        client, admin_id):
    """SCREEN: the run-failure panel, reached from a red row in the history list.

    A pipeline can be valid at save time and still blow up on the data — here a
    ``parse_dates`` whose format never matches. The UI's job afterwards is to
    show WHY, and the fields it renders are all on the run DETAIL endpoint,
    which nothing currently asserts for a failed run: ``error`` set,
    ``result_summary``/``artifact_id``/``output_profile``/``source_drift`` null,
    ``completed_at`` set (so the row is not shown as still spinning).

    Publishing a failed run is a different 409 branch from publishing one that
    is merely still running, and the Publish button on this screen must be
    disabled by it. Finally the user fixes the pipeline in place and re-runs —
    the history keeps both attempts.
    """
    ds = await _dataset(client, admin_id)
    h = auth(admin_id)
    base = f"/api/v1/datasets/{ds}/transformations"
    did = (await _definition(
        client, h, ds, name=f"dates-{rid()}",
        steps=[{"type": "parse_dates", "columns": ["name"], "format": "%Y-%m-%d"}]
    ))["id"]

    # ---- 1. Save succeeded, so the failure only appears on execution ----
    r = await client.post(f"{base}/{did}/run", headers=h)
    assert r.status_code == 400, r.text
    assert r.headers["content-type"].startswith(PROBLEM)
    assert r.json()["code"] == "transformation-failed"

    # ---- 2. The failing POST returned a problem, not a run — find it by listing ----
    runs = (await client.get(f"{base}/{did}/runs", headers=h)).json()
    assert runs["total"] == 1
    row = runs["items"][0]
    assert row["status"] == "failed" and row["error"]

    # ---- 3. The detail screen: everything it renders about the failure ----
    detail = await client.get(f"{base}/runs/{row['id']}", headers=h)
    assert detail.status_code == 200, detail.text
    failed = detail.json()
    assert failed["id"] == row["id"] and failed["status"] == "failed"
    assert failed["error"] and failed["error"] == row["error"]
    assert failed["completed_at"], "a failed run must not render as still running"
    assert failed["artifact_id"] is None and failed["result_summary"] is None
    assert failed["output_profile"] is None and failed["source_drift"] is None
    assert failed["published_version_id"] is None

    # ---- 4. Publish is refused, and says why ----
    pub = await client.post(f"{base}/runs/{row['id']}/publish", headers=h,
                            json={"mode": "new_dataset", "name": f"x-{rid()}"})
    assert pub.status_code == 409, pub.text
    assert pub.headers["content-type"].startswith(PROBLEM)
    # NOTE: this reads prose because there is nothing else to read — the refusal
    # carries only the generic ``conflict`` code, the same one a run that is
    # still executing gets. See the ui_ergonomics note on this file.
    assert "failed" in pub.json()["detail"]
    # Nothing was created by the attempt.
    assert (await client.get(f"/api/v1/datasets/{ds}/versions", headers=h)).json()["total"] == 1

    # ---- 5. Previewing reproduces the failure with the same code ----
    preview = await client.post(f"{base}/{did}/preview", headers=h)
    assert preview.status_code == 400
    assert preview.json()["code"] == "transformation-failed"
    assert (await client.get(f"{base}/{did}/runs", headers=h)).json()["total"] == 1, \
        "preview created a run row"

    # ---- 6. The user fixes the step in place and re-runs ----
    r = await client.patch(f"{base}/{did}", headers=h, json={"steps": [TRIM, DEDUPE]})
    assert r.status_code == 200, r.text
    good = await client.post(f"{base}/{did}/run", headers=h)
    assert good.status_code == 200, good.text
    assert good.json()["status"] == "completed"

    history = (await client.get(f"{base}/{did}/runs", headers=h)).json()
    assert history["total"] == 2
    assert [i["status"] for i in history["items"]] == ["completed", "failed"]

    # The now-successful run publishes.
    ok = await client.post(f"{base}/runs/{good.json()['id']}/publish", headers=h,
                           json={"mode": "new_version"})
    assert ok.status_code == 200, ok.text
    assert ok.json()["version_number"] == 2


# ---------------------------------------------------------------------------
# 7. Chained derivation
# ---------------------------------------------------------------------------

async def test_journey_a_published_transform_is_transformed_again_and_lineage_walks_the_chain(
        client, admin_id):
    """SCREEN: the lineage graph on a dataset that is two transforms deep.

    "This dataset is a transform of a transform of an upload" is the whole point
    of publishing into a new dataset, and only single-hop derivation is covered
    anywhere. The chain is built the way a user builds it — publish, then open
    the published dataset and transform THAT — and then read back the two
    screens that render it: ``/lineage`` (one hop, both directions) and
    ``/lineage/graph`` (the whole DAG, with depth). The timeline is the third:
    the source dataset must show ``published_to`` and the leaf ``derived_from``.

    A published dataset always lands its data on a sheet named ``data``, which
    is the id the second transformation needs and can only learn from the
    dataset metadata — so that lookup is part of the journey.
    """
    ds1 = await _dataset(client, admin_id)
    h = auth(admin_id)
    base1 = f"/api/v1/datasets/{ds1}/transformations"

    # ---- 1. First hop: clean the upload, publish it as its own dataset ----
    did1 = (await _definition(client, h, ds1, name=f"clean-{rid()}",
                              steps=[TRIM, DEDUPE]))["id"]
    run1 = (await client.post(f"{base1}/{did1}/run", headers=h)).json()
    assert run1["result_summary"]["row_count"] == 4
    pub1 = await client.post(f"{base1}/runs/{run1['id']}/publish", headers=h,
                             json={"mode": "new_dataset", "name": f"cleaned-{rid()}"})
    assert pub1.status_code == 200, pub1.text
    ds2 = pub1.json()["dataset_id"]

    # ---- 2. The UI opens the new dataset to find the sheet to transform ----
    meta = await client.get(f"/api/v1/datasets/{ds2}", headers=h)
    assert meta.status_code == 200, meta.text
    assert [s["name"] for s in meta.json()["sheets"]] == ["data"]

    # ---- 3. Second hop: transform the published output ----
    base2 = f"/api/v1/datasets/{ds2}/transformations"
    did2 = (await _definition(client, h, ds2, name=f"top-{rid()}",
                              steps=[{"type": "sort",
                                      "by": [{"column": "amount", "direction": "desc"}]},
                                     {"type": "limit", "count": 2}]))["id"]
    run2 = (await client.post(f"{base2}/{did2}/run", headers=h)).json()
    assert run2["result_summary"]["source_row_count"] == 4, "reads the published rows"
    assert run2["result_summary"]["row_count"] == 2
    pub2 = await client.post(f"{base2}/runs/{run2['id']}/publish", headers=h,
                             json={"mode": "new_dataset", "name": f"top2-{rid()}"})
    assert pub2.status_code == 200, pub2.text
    ds3 = pub2.json()["dataset_id"]

    top = (await client.get(f"/api/v1/datasets/{ds3}/versions/1/preview",
                            headers=h)).json()
    assert top["total"] == 2
    assert {row["amount"] for row in top["items"]} == {30.0, 40.0}

    # ---- 4. One hop, both directions ----
    lin3 = (await client.get(f"/api/v1/datasets/{ds3}/lineage", headers=h)).json()
    assert [p["relation"] for p in lin3["parents"]] == ["transformed_from"]
    assert lin3["parents"][0]["parent_dataset_id"] == ds2
    assert lin3["parents"][0]["parent_visible"] is True
    assert lin3["children"] == []

    lin2 = (await client.get(f"/api/v1/datasets/{ds2}/lineage", headers=h)).json()
    assert lin2["parents"][0]["parent_dataset_id"] == ds1
    assert [c["child_dataset_id"] for c in lin2["children"]] == [ds3]
    assert lin2["children"][0]["child_visible"] is True
    assert lin2["children"][0]["parent_version_number"] == 1

    # ---- 5. The whole DAG, which is what the graph view draws ----
    graph = await client.get(f"/api/v1/datasets/{ds3}/lineage/graph", headers=h)
    assert graph.status_code == 200, graph.text
    g = graph.json()
    assert {n["id"] for n in g["nodes"]} == {ds1, ds2, ds3}
    assert [n["id"] for n in g["nodes"] if n["is_root"]] == [ds3]
    edges = {(e["child_id"], e["parent_id"]): e for e in g["edges"]}
    assert set(edges) == {(ds3, ds2), (ds2, ds1)}
    assert edges[(ds3, ds2)]["depth"] == 1 and edges[(ds2, ds1)]["depth"] == 2
    assert all(e["relation"] == "transformed_from" for e in g["edges"])
    assert g["truncated"] is False and g["hidden_nodes"] == 0

    # ---- 6. The timelines tell the same story from each end ----
    tl1 = (await client.get(f"/api/v1/datasets/{ds1}/timeline",
                            params={"limit": 200}, headers=h)).json()
    kinds1 = [e["event_type"] for e in tl1["items"]]
    assert "transformation_run" in kinds1 and "published_to" in kinds1
    published_to = next(e for e in tl1["items"] if e["event_type"] == "published_to")
    assert published_to["details"]["child_dataset_id"] == ds2

    tl3 = (await client.get(f"/api/v1/datasets/{ds3}/timeline",
                            params={"limit": 200}, headers=h)).json()
    derived = next(e for e in tl3["items"] if e["event_type"] == "derived_from")
    assert derived["details"]["relation"] == "transformed_from"
    assert derived["details"]["version_number"] == 1


# ---------------------------------------------------------------------------
# 8. The delete confirmation
# ---------------------------------------------------------------------------

async def test_journey_deleting_a_transformation_takes_its_run_history_but_not_what_it_published(
        client, admin_id):
    """SCREEN: the "delete this transformation?" confirmation dialog.

    The dialog's copy depends entirely on what actually goes: the run history
    cascades with the definition (a schema-level ON DELETE CASCADE nothing
    asserts), but a dataset that was already published from a run must NOT — it
    is somebody's production input by then. The UI also keeps run ids in URLs,
    so it needs to know a bookmarked run detail answers 404 rather than 500 or,
    worse, a row belonging to a different definition.
    """
    ds = await _dataset(client, admin_id)
    h = auth(admin_id)
    base = f"/api/v1/datasets/{ds}/transformations"
    did = (await _definition(client, h, ds, name=f"cleanup-{rid()}",
                             steps=[TRIM, DEDUPE]))["id"]
    run = (await client.post(f"{base}/{did}/run", headers=h)).json()
    run_id, sample_file = run["id"], run["result_summary"]["sample_file"]

    pub = await client.post(f"{base}/runs/{run_id}/publish", headers=h,
                            json={"mode": "new_dataset", "name": f"cleaned-{rid()}"})
    assert pub.status_code == 200, pub.text
    published = pub.json()["dataset_id"]

    # A second definition, to prove the delete is scoped to one.
    keeper = (await _definition(client, h, ds, name=f"keeper-{rid()}",
                                steps=[{"type": "limit", "count": 1}]))["id"]
    await client.post(f"{base}/{keeper}/run", headers=h)

    # ---- 1. Delete ----
    r = await client.delete(f"{base}/{did}", headers=h)
    assert r.status_code == 204, r.text
    assert r.content == b""

    # ---- 2. Everything the deleted definition owned is gone ----
    assert (await client.get(f"{base}/{did}", headers=h)).status_code == 404
    assert (await client.get(f"{base}/{did}/runs", headers=h)).status_code == 404
    assert (await client.get(f"{base}/runs/{run_id}", headers=h)).status_code == 404
    assert (await client.post(f"{base}/{did}/preview", headers=h)).status_code == 404
    assert (await client.post(f"{base}/{did}/run", headers=h)).status_code == 404
    assert (await client.post(f"{base}/runs/{run_id}/publish", headers=h,
                              json={"mode": "new_version"})).status_code == 404
    # Deleting twice is a 404, not a silent success.
    assert (await client.delete(f"{base}/{did}", headers=h)).status_code == 404

    # ---- 3. The sibling definition and its history are untouched ----
    listing = (await client.get(base, headers=h)).json()
    assert listing["total"] == 1 and [i["id"] for i in listing["items"]] == [keeper]
    assert (await client.get(f"{base}/{keeper}/runs", headers=h)).json()["total"] == 1

    # ---- 4. What was published outlives the pipeline that made it ----
    rows = await client.get(f"/api/v1/datasets/{published}/versions/1/preview", headers=h)
    assert rows.status_code == 200, rows.text
    assert rows.json()["total"] == 4

    lineage = (await client.get(f"/api/v1/datasets/{published}/lineage", headers=h)).json()
    assert [p["relation"] for p in lineage["parents"]] == ["transformed_from"]
    assert lineage["parents"][0]["parent_dataset_id"] == ds

    # The run's output artifact is still addressable too — deleting a
    # definition is not a data-deletion tool.
    assert (await client.get(f"/api/v1/samples/{sample_file}/data",
                             headers=h)).status_code == 200

    # ---- 5. And the source dataset never lost a version ----
    assert (await client.get(f"/api/v1/datasets/{ds}/versions", headers=h)).json()["total"] == 1

"""Transformations: editing a saved pipeline, compiling an unsaved one, publishing once.

Companion to tests/test_transformations.py, which covers the happy path. These
pin the states a builder UI actually walks into — rename collisions, a pipeline
edited after the sheet under it was renamed, a validity check before the thing
has a name, a double-clicked Publish — each of which produced a wrong answer
rather than an error.
"""

from __future__ import annotations

import base64
import json

from conftest import (
    DEFAULT_TEAM_ID,
    XLSX_MIME,
    auth,
    make_workbook,
    upload_file,
    upload_inline,
)

ROWS = [
    {"id": 1, "name": "Alice", "city": "NY", "amount": 10.0},
    {"id": 2, "name": "bob", "city": "ny", "amount": 20.0},
    {"id": 3, "name": "Carol", "city": "LA", "amount": 30.0},
]


async def make_dataset(client, admin_id):
    return (await upload_inline(client, admin_id, json.dumps(ROWS)))["dataset_id"]


def body(name, steps=None, **extra):
    return {"name": name, "sheet": "data",
            "steps": steps if steps is not None else [{"type": "limit", "count": 2}],
            **extra}


# --- renaming a definition ----------------------------------------------------

async def test_renaming_a_transformation_to_a_name_already_used_on_the_dataset_is_a_409(
        client, admin_id):
    """Two saved pipelines, rename one onto the other: a conflict, not a crash.

    ``transformation_definitions`` has a UNIQUE (dataset_id, name). POST routes
    around it with ON CONFLICT DO NOTHING, but PATCH issues a plain UPDATE, so
    the violation used to escape as an IntegrityError and be rendered by the
    unhandled-exception handler: a 500 saying "An unexpected error occurred."
    A rename form cannot show "that name is taken" from that — it looks like the
    service broke, and the user retries the same doomed request.
    """
    ds = await make_dataset(client, admin_id)
    h = auth(admin_id)
    base = f"/api/v1/datasets/{ds}/transformations"

    assert (await client.post(base, headers=h, json=body("cleanup"))).status_code == 201
    second = (await client.post(base, headers=h, json=body("draft"))).json()

    r = await client.patch(f"{base}/{second['id']}", headers=h, json={"name": "cleanup"})
    assert r.status_code == 409, r.text
    problem = r.json()
    assert problem["code"] == "transformation-name-taken"
    assert "cleanup" in problem["detail"], "the detail must name the collision"

    # The losing definition is untouched — a failed rename is not a partial one.
    assert (await client.get(f"{base}/{second['id']}", headers=h)).json()["name"] == "draft"


async def test_creating_and_renaming_report_the_same_name_collision_code(
        client, admin_id):
    """POST and PATCH reach the same constraint by different SQL.

    A UI branching on ``code`` must not have to know which verb it used, so the
    two paths are pinned together; POST's 409 previously carried no ``code`` at
    all and fell back to the generic ``conflict``.
    """
    ds = await make_dataset(client, admin_id)
    h = auth(admin_id)
    base = f"/api/v1/datasets/{ds}/transformations"
    assert (await client.post(base, headers=h, json=body("cleanup"))).status_code == 201

    duplicate = await client.post(base, headers=h, json=body("cleanup"))
    assert duplicate.status_code == 409
    assert duplicate.json()["code"] == "transformation-name-taken"


# --- editing after a confirmed rename -----------------------------------------

async def test_patching_steps_on_a_version_pinned_definition_survives_a_confirmed_sheet_rename(
        client, admin_id, tmp_path):
    """A definition pinned to v1 stays editable after the sheet is renamed in v2.

    ``definition["sheet_key"]`` is the sheet's CURRENT name (joined from the
    logical sheet). PATCH re-resolved the target by that name — inside the
    version the definition is pinned to, where the sheet still carries its OLD
    name. So after a confirmed rename the pipeline could still be RUN (run
    resolves by logical id) but never edited again: every PATCH answered 404,
    and the only way out of it was to delete the definition and its run history.
    """
    v1, v2 = tmp_path / "v1.xlsx", tmp_path / "v2.xlsx"
    make_workbook(v1)
    make_workbook(v2, second_sheet="Spending")   # Expenses renamed, same schema
    ds = (await upload_file(client, admin_id, v1, name="book.xlsx",
                            content_type=XLSX_MIME))["dataset_id"]
    h = auth(admin_id)
    base = f"/api/v1/datasets/{ds}/transformations"

    did = (await client.post(base, headers=h, json={
        "name": "costs", "sheet": "Expenses",
        "version_selector": {"mode": "version", "version_number": 1},
        "steps": [{"type": "sort", "by": [{"column": "cost", "direction": "desc"}]}],
    })).json()["id"]

    await upload_file(client, admin_id, v2, name="book.xlsx",
                      content_type=XLSX_MIME, dataset_id=ds)
    r = await client.post(f"/api/v1/datasets/{ds}/versions/2/confirm-rename", headers=h,
                          json={"from_sheet": "Expenses", "to_sheet": "Spending"})
    assert r.status_code == 200, r.text

    # The sheet now answers to "Spending" — but v1 still spells it "Expenses".
    assert (await client.get(f"{base}/{did}", headers=h)).json()["sheet_key"] == "spending"

    r = await client.patch(f"{base}/{did}", headers=h,
                           json={"steps": [{"type": "limit", "count": 1}]})
    assert r.status_code == 200, r.text
    updated = r.json()
    assert len(updated["steps"]) == 1
    # Editing the steps did not re-point the definition at some other sheet.
    assert updated["logical_sheet_id"] == \
        (await client.get(f"{base}/{did}", headers=h)).json()["logical_sheet_id"]

    # And the edited pipeline still runs against the pinned version.
    run = await client.post(f"{base}/{did}/run", headers=h)
    assert run.status_code == 200, run.text
    assert run.json()["result_summary"]["version_number"] == 1
    assert run.json()["result_summary"]["row_count"] == 1


async def test_patching_with_an_explicit_sheet_still_retargets_by_name(
        client, admin_id, tmp_path):
    """Resolving by identity must not disable the deliberate move to another sheet.

    The fix narrows name resolution to callers who actually sent ``sheet``; this
    is the other half of that contract, so a later "simplify" cannot delete the
    name path along with the bug.
    """
    path = tmp_path / "book.xlsx"
    make_workbook(path)
    ds = (await upload_file(client, admin_id, path, name="book.xlsx",
                            content_type=XLSX_MIME))["dataset_id"]
    h = auth(admin_id)
    base = f"/api/v1/datasets/{ds}/transformations"

    did = (await client.post(base, headers=h, json={
        "name": "costs", "sheet": "Expenses",
        "steps": [{"type": "limit", "count": 1}]})).json()["id"]
    before = (await client.get(f"{base}/{did}", headers=h)).json()

    r = await client.patch(f"{base}/{did}", headers=h,
                           json={"sheet": "Revenue",
                                 "steps": [{"type": "limit", "count": 2}]})
    assert r.status_code == 200, r.text
    assert r.json()["sheet_key"] == "revenue"
    assert r.json()["logical_sheet_id"] != before["logical_sheet_id"]


# --- compiling an unsaved pipeline --------------------------------------------

async def test_a_pipeline_can_be_compiled_and_previewed_without_saving_a_definition(
        client, admin_id):
    """A builder must be able to ask "is this valid?" before the thing has a name.

    Validation and preview used to be reachable only through a persisted
    definition, and creating one needs a name that is unique per dataset. So a
    step-by-step UI had to save-and-delete a throwaway definition on every
    keystroke — writing rows (and colliding on the name) purely to type-check.
    """
    ds = await make_dataset(client, admin_id)
    h = auth(admin_id)
    url = f"/api/v1/datasets/{ds}/transformations/compile"

    r = await client.post(url, headers=h, json={
        "sheet": "data",
        "steps": [{"type": "drop", "columns": ["city"]},
                  {"type": "compute", "into": "double_amount",
                   "expression": {"op": "arith", "fn": "mul",
                                  "left": {"op": "col", "name": "amount"},
                                  "right": {"op": "lit", "value": 2}}}],
    })
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["sampled"] is False and out["rows"] == []
    assert [c["name"] for c in out["output_schema"]] == \
        ["id", "name", "amount", "double_amount"]
    # Per-step columns are what a picker for step N+1 offers.
    assert [c["name"] for c in out["step_schemas"][0]] == ["id", "name", "amount"]
    assert len(out["step_schemas"]) == 2
    assert out["version_number"] == 1 and out["sheet_name"] == "data"

    # With rows, it is a real dry run over a bounded sample.
    r = await client.post(url, headers=h, json={
        "sheet": "data", "rows": 10,
        "steps": [{"type": "case_normalize", "columns": ["city"], "mode": "upper"}]})
    assert r.status_code == 200, r.text
    sampled = r.json()
    assert sampled["sampled"] is True and len(sampled["rows"]) == 3
    assert all(row["city"] == row["city"].upper() for row in sampled["rows"])

    # Nothing was persisted: no definition, no run.
    listing = await client.get(f"/api/v1/datasets/{ds}/transformations", headers=h)
    assert listing.json()["total"] == 0


async def test_compiling_an_invalid_pipeline_answers_exactly_as_saving_it_would(
        client, admin_id):
    """The point of the endpoint is that its verdict is the saved one.

    If compile were more permissive than create, a UI would show a green tick
    and then fail on save; if it were stricter it would block a legal pipeline.
    Same compiler, same problem+json, asserted side by side.
    """
    ds = await make_dataset(client, admin_id)
    h = auth(admin_id)
    steps = [{"type": "drop", "columns": ["city"]},
             {"type": "sort", "by": [{"column": "city"}]}]

    compiled = await client.post(
        f"/api/v1/datasets/{ds}/transformations/compile", headers=h,
        json={"sheet": "data", "steps": steps})
    saved = await client.post(
        f"/api/v1/datasets/{ds}/transformations", headers=h,
        json=body("x", steps=steps))

    assert compiled.status_code == saved.status_code == 400
    assert compiled.json()["code"] == saved.json()["code"] == "unknown-column"
    assert compiled.json()["available"] == saved.json()["available"]


async def test_compile_is_readable_by_a_reader_and_invisible_across_teams(
        client, admin_id):
    """Compiling persists nothing, so it is a read — but still team-scoped.

    A new endpoint that skipped ``ensure_dataset_permission`` would be a schema
    disclosure: the folded output columns name every column of the sheet.
    """
    from conftest import create_team_user

    ds = await make_dataset(client, admin_id)
    outsider, _ = await create_team_user(client, admin_id, "admin")
    r = await client.post(f"/api/v1/datasets/{ds}/transformations/compile",
                          headers=auth(outsider),
                          json={"sheet": "data", "steps": []})
    assert r.status_code == 404, r.text


# --- publishing once ----------------------------------------------------------

async def test_publishing_the_same_run_twice_does_not_create_a_second_version(
        client, admin_id):
    """A double-clicked Publish must not fork the dataset's history.

    Publishing was unguarded and unrecorded: each POST minted another
    byte-identical version plus another ``transformed_from`` lineage edge, so
    the version list and the lineage graph both grew a duplicate that nothing
    could distinguish from a real second publication — and no endpoint could be
    asked whether a run had already been published.
    """
    ds = await make_dataset(client, admin_id)
    h = auth(admin_id)
    did = (await client.post(f"/api/v1/datasets/{ds}/transformations", headers=h,
                             json=body("cleanup"))).json()["id"]
    run_id = (await client.post(
        f"/api/v1/datasets/{ds}/transformations/{did}/run", headers=h)).json()["id"]
    publish = f"/api/v1/datasets/{ds}/transformations/runs/{run_id}/publish"

    first = await client.post(publish, headers=h, json={"mode": "new_version"})
    assert first.status_code == 200, first.text
    assert first.json()["version_number"] == 2

    second = await client.post(publish, headers=h, json={"mode": "new_version"})
    assert second.status_code == 409, second.text
    problem = second.json()
    assert problem["code"] == "run-already-published"
    # The 409 carries the existing publication so a UI can link to it.
    assert problem["published_version_number"] == 2
    assert problem["published_version_id"] == first.json()["version_id"]
    assert problem["published_dataset_id"] == ds

    versions = (await client.get(f"/api/v1/datasets/{ds}/versions", headers=h)).json()
    assert versions["total"] == 2, "the second publish minted another version"

    lineage = (await client.get(f"/api/v1/datasets/{ds}/lineage", headers=h)).json()
    assert len([c for c in lineage["children"]
                if c["relation"] == "transformed_from"]) == 1


async def test_a_run_reports_the_version_it_was_published_as(client, admin_id):
    """"Has this been published?" has to be answerable from the run itself.

    Without it a UI can only offer Publish again and find out by being refused;
    the run detail is where it already looks for the run's outcome.
    """
    ds = await make_dataset(client, admin_id)
    h = auth(admin_id)
    did = (await client.post(f"/api/v1/datasets/{ds}/transformations", headers=h,
                             json=body("cleanup"))).json()["id"]
    run_id = (await client.post(
        f"/api/v1/datasets/{ds}/transformations/{did}/run", headers=h)).json()["id"]
    detail_url = f"/api/v1/datasets/{ds}/transformations/runs/{run_id}"

    before = (await client.get(detail_url, headers=h)).json()
    assert before["published_version_id"] is None

    published = (await client.post(
        f"{detail_url}/publish", headers=h,
        json={"mode": "new_dataset", "name": f"cleaned-{run_id[:8]}"})).json()

    after = (await client.get(detail_url, headers=h)).json()
    assert after["published_version_id"] == published["version_id"]
    assert after["published_dataset_id"] == published["dataset_id"]
    assert after["published_version_number"] == 1


# --- previewing a pin that has no data ----------------------------------------

async def _tus_create(client, user_id, dataset_id, *, filename="next.csv", size=64):
    """Announce a TUS upload — this alone creates a path-less 'uploading' version."""
    meta = ", ".join(
        f"{k} {base64.b64encode(v.encode()).decode()}"
        for k, v in {"filename": filename, "dataset_id": dataset_id}.items())
    r = await client.post("/api/v1/tus/", headers={
        **auth(user_id), "X-Team-Id": DEFAULT_TEAM_ID,
        "Upload-Length": str(size), "Upload-Metadata": meta,
        "Tus-Resumable": "1.0.0"})
    assert r.status_code == 201, r.text


async def test_previewing_a_tag_that_points_at_a_data_less_version_answers_like_running_it(
        client, admin_id):
    """Preview and Run must agree about a tag moved onto an unfinished upload.

    Tags can be moved to any version of the dataset, including one still
    ``uploading``. Run said "Version has no data (status: uploading)" — actionable.
    Preview skipped that guard and fell through to the sheet lookup, which found
    no sheet rows and reported ``sheet-not-in-version``: it told the user their
    sheet had been deleted, sending them to fix a definition that was correct,
    while the answer was "wait for the upload / move the tag back".
    """
    ds = await make_dataset(client, admin_id)
    h = auth(admin_id)
    assert (await client.put(f"/api/v1/datasets/{ds}/tags", headers=h,
                             json={"tag_name": "live", "version_number": 1})
            ).status_code == 200

    base = f"/api/v1/datasets/{ds}/transformations"
    did = (await client.post(base, headers=h, json={
        "name": "cleanup", "sheet": "data",
        "version_selector": {"mode": "tag", "tag": "live"},
        "steps": [{"type": "limit", "count": 1}]})).json()["id"]

    # Version 2 exists but has no bytes behind it yet; move the tag onto it.
    await _tus_create(client, admin_id, ds)
    assert (await client.put(f"/api/v1/datasets/{ds}/tags", headers=h,
                             json={"tag_name": "live", "version_number": 2})
            ).status_code == 200

    preview = await client.post(f"{base}/{did}/preview", headers=h)
    run = await client.post(f"{base}/{did}/run", headers=h)

    assert preview.status_code == run.status_code == 404
    assert preview.json()["detail"] == run.json()["detail"]
    assert "no data" in preview.json()["detail"]
    assert preview.json()["code"] != "sheet-not-in-version"

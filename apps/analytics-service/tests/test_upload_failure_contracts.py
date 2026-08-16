"""What the upload/download surface says when something goes wrong.

Every case here used to answer either 500 "An unexpected error occurred." for
something the caller could fix, or 200 for something they were not entitled to.
Both are unbranchable for a UI: the first gives it nothing to show next to the
field that was wrong, the second gives it no signal at all.
"""

from __future__ import annotations

import io

from conftest import (
    DEFAULT_TEAM_ID,
    SAMPLE_CSV,
    XLSX_MIME,
    auth,
    create_team_user,
    make_workbook,
    upload_file,
    upload_inline,
)

PROBLEM = "application/problem+json"


async def _dataset_ids(client, user_id):
    r = await client.get("/api/v1/datasets", headers=auth(user_id),
                         params={"limit": 200})
    assert r.status_code == 200, r.text
    return {d["id"] for d in r.json()["items"]}


async def test_inline_json_that_cannot_be_loaded_is_a_400_that_leaves_nothing_behind(
        client, admin_id):
    """The inline-JSON branch created the dataset and version rows first and
    loaded the rows second, with no try/except around any of it. A payload
    pandas cannot build a frame from — a JSON array mixing objects and scalars
    is the everyday case — therefore returned a bare 500 AND left a dataset
    nobody asked for, holding a version stuck at "uploading" that the status
    endpoint reports as "processing" for the life of the deployment."""
    before = await _dataset_ids(client, admin_id)

    r = await client.post("/api/v1/upload",
                          headers={**auth(admin_id), "X-Team-Id": DEFAULT_TEAM_ID},
                          data={"data": '[{"a": 1}, 5]'})

    assert r.status_code == 400, r.text
    assert r.headers["content-type"].startswith(PROBLEM)
    assert r.json()["code"] == "invalid-file"

    # No orphan dataset, so nothing to explain in the catalog.
    assert await _dataset_ids(client, admin_id) == before


async def test_a_misspelled_include_sheets_names_the_sheets_that_exist(
        client, admin_id, tmp_path):
    """`include_sheets` is a user-typed opt-in, so getting it wrong is the most
    likely way to fail a partial-workbook upload. It raised a bare ValueError,
    which the sync path mapped to 500 `internal_server_error` — the UI could
    not tell it apart from an outage, and the message listing the workbook's
    real sheet names never reached the sheet picker."""
    path = tmp_path / "book.xlsx"
    make_workbook(path)

    with open(path, "rb") as f:
        r = await client.post(
            "/api/v1/upload",
            headers={**auth(admin_id), "X-Team-Id": DEFAULT_TEAM_ID},
            files={"file": ("book.xlsx", f, XLSX_MIME)},
            data={"include_sheets": "Revenu"})

    assert r.status_code == 400, r.text
    assert r.headers["content-type"].startswith(PROBLEM)
    body = r.json()
    assert body["code"] == "sheet-not-found"
    assert "Revenue" in body["detail"]  # the real sheet name, for the picker


async def test_upload_status_404s_for_a_deleted_version_even_with_a_warm_cache(
        client, admin_id):
    """The permission check was conditional on the version row existing, and
    the in-memory status cache is never evicted. Delete the dataset and the
    cache entry — which holds the preview ROWS and the full column list — was
    still served, to any authenticated caller, with no team check at all."""
    body = await upload_file(client, admin_id, SAMPLE_CSV, name="leak.csv")
    vid, ds = body["version_id"], body["dataset_id"]

    from app.features.files.services.processing import processing_status  # noqa: PLC0415
    cached = processing_status.get(vid)
    assert cached and cached.get("preview"), "cache must be warm to mean anything"

    r = await client.delete(f"/api/v1/datasets/{ds}", headers=auth(admin_id))
    assert r.status_code == 200, r.text

    for user in (admin_id, (await create_team_user(client, admin_id, "editor"))[0]):
        r = await client.get(f"/api/v1/upload/status/{vid}", headers=auth(user))
        assert r.status_code == 404, r.text
        assert r.json().get("preview") is None
        assert r.json().get("columns") is None

    # And the entry is gone, not merely unreachable: nothing ever evicted this
    # dict, so every deleted dataset's preview rows stayed resident for the
    # life of the process.
    assert vid not in processing_status


async def test_storage_usage_is_restricted_to_platform_administrators(
        client, admin_id):
    """The totals sum every team's datasets, artifacts and staging bytes — the
    same cross-tenant aggregate its two siblings (/storage/retention,
    /storage/gc) are explicitly superuser-only for. This one was authenticated
    but ungated, so any member of any team could read the whole platform's
    volume, and could not read a per-team number out of it either."""
    editor, _ = await create_team_user(client, admin_id, "editor")

    r = await client.get("/api/v1/storage/usage", headers=auth(editor))
    assert r.status_code == 403, r.text
    assert "administrators" in r.json()["detail"]

    r = await client.get("/api/v1/storage/usage", headers=auth(admin_id))
    assert r.status_code == 200, r.text


async def test_an_unknown_artifact_column_lists_the_columns_that_do_exist(
        client, admin_id):
    """An artifact's columns are whatever the query that produced it aliased
    them to, and there is no schema endpoint for an artifact — so "Columns not
    found: ['revenu']" with the available set discarded left the caller (very
    often an MCP model carrying a name over from the source sheet) guessing.
    The `unknown-column` code is what makes the renderer attach the list."""
    ds = (await upload_inline(
        client, admin_id, '[{"n": 1}, {"n": 2}]'))["dataset_id"]
    h = auth(admin_id)
    r = await client.post("/api/v1/sample", headers=h, json={
        "dataset_id": ds, "target_total_volume": 2,
        "sampling_steps": [{"method": "random", "sample_size": 2}]})
    fname = r.json()["sample_file"]
    url = f"/api/v1/samples/{fname}/data"

    r = await client.get(url, headers=h, params={"columns": "nope"})
    assert r.status_code == 400, r.text
    body = r.json()
    assert body["code"] == "unknown-column"
    assert "n" in body["available"]

    r = await client.get(url, headers=h, params={"sort_by": "nope"})
    assert r.status_code == 400, r.text
    body = r.json()
    assert body["code"] == "unknown-column"
    assert "n" in body["available"]


async def test_sheet_replace_advertises_its_response_shape_in_openapi(client):
    """The route returned a bare `dict`, so its six fields — including
    `version_number` and the copy-on-write `reused_sheets` list a UI needs to
    tell the user what was NOT re-uploaded — were an untyped object in the
    schema. Every other JSON route in the module is typed; a generated client
    got nothing for this one."""
    spec = (await client.get("/openapi.json")).json()
    path = spec["paths"]["/api/v1/datasets/{dataset_id}/sheets/{sheet_name}/replace"]
    schema = path["post"]["responses"]["200"]["content"]["application/json"]["schema"]

    ref = schema.get("$ref", "")
    assert ref, f"replace response is untyped: {schema}"
    model = spec["components"]["schemas"][ref.rsplit("/", 1)[-1]]
    assert {"dataset_id", "version_id", "version_number", "replaced_sheet",
            "reused_sheets", "row_count"} <= set(model["properties"])


async def test_uploading_an_empty_file_still_succeeds(client, admin_id):
    """Guard on the inline/multipart restructure: the zero-row upload is a
    legitimate case (an empty export), and it must not be swept up by the new
    "unloadable payload" 400."""
    r = await client.post("/api/v1/upload",
                          headers={**auth(admin_id), "X-Team-Id": DEFAULT_TEAM_ID},
                          files={"file": ("empty.csv", io.BytesIO(b""), "text/csv")})
    assert r.status_code == 200, r.text

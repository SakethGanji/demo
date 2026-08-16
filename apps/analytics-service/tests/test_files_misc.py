"""Files odds and ends — sample listing, storage usage, upload edge cases,
and team-context resolution on uploads (fallback + spoof rejection)."""

from __future__ import annotations

import io

from conftest import DEFAULT_TEAM_ID, auth, create_team_user, upload_inline

PROBLEM = "application/problem+json"


async def test_samples_listing_includes_new_sample(client, admin_id):
    ds = (await upload_inline(
        client, admin_id, '[{"a": 1}, {"a": 2}, {"a": 3}]'))["dataset_id"]
    h = auth(admin_id)
    r = await client.post("/api/v1/sample", headers=h, json={
        "dataset_id": ds, "target_total_volume": 2,
        "sampling_steps": [{"method": "random", "sample_size": 2}]})
    fname = r.json()["sample_file"]

    listing = (await client.get("/api/v1/samples", headers=h)).json()
    mine = next(e for e in listing["items"] if e["filename"] == fname)
    # file_type is the artifact kind now, not a constant — the listing reads
    # the artifacts table, so it can say what the file actually is.
    assert mine["file_type"] == "sample_output" and mine["size_bytes"] > 0


async def test_storage_usage_accounts_for_data(client, admin_id):
    before = (await client.get("/api/v1/storage/usage",
                               headers=auth(admin_id))).json()
    assert {"total_bytes", "datasets_bytes", "samples_bytes",
            "exports_bytes", "uploads_bytes"} <= set(before)

    await upload_inline(client, admin_id, '[{"a": 1}, {"a": 2}]')
    after = (await client.get("/api/v1/storage/usage",
                              headers=auth(admin_id))).json()
    assert after["datasets_bytes"] > before["datasets_bytes"]
    assert after["total_bytes"] >= after["datasets_bytes"]


async def test_empty_csv_uploads_as_zero_row_version(client, admin_id):
    r = await client.post("/api/v1/upload",
                          headers={**auth(admin_id), "X-Team-Id": DEFAULT_TEAM_ID},
                          files={"file": ("empty.csv", io.BytesIO(b""), "text/csv")})
    assert r.status_code == 200, r.text
    assert (r.json()["row_count"] or 0) == 0


async def test_corrupt_xlsx_fails_without_ready_version(client, admin_id):
    h = {**auth(admin_id), "X-Team-Id": DEFAULT_TEAM_ID}
    r = await client.post("/api/v1/upload", headers=h, files={
        "file": ("bad.xlsx", io.BytesIO(b"this is not a zip archive"),
                 "application/octet-stream")})
    # Unparseable uploads are the user's error: problem+json 400 with a
    # machine-readable code (ROADMAP §4), not a 500.
    assert r.status_code == 400
    assert r.headers["content-type"].startswith(PROBLEM)
    body = r.json()
    assert body["code"] == "invalid-file"
    assert "could not be parsed" in body["detail"]

    # Nothing usable was left behind.
    listing = (await client.get("/api/v1/datasets", headers=auth(admin_id))).json()
    for d in listing["items"]:
        versions = (await client.get(f"/api/v1/datasets/{d['id']}/versions",
                                     headers=auth(admin_id))).json()["items"]
        assert all(v["status"] != "ready" for v in versions)


async def test_unsupported_extension_rejected_upfront(client, admin_id):
    r = await client.post("/api/v1/upload",
                          headers={**auth(admin_id), "X-Team-Id": DEFAULT_TEAM_ID},
                          files={"file": ("notes.txt", io.BytesIO(b"hello"),
                                          "text/plain")})
    assert r.status_code == 400 and "Unsupported file type" in r.json()["detail"]


async def test_upload_without_team_header_falls_back_to_home_team(client, admin_id):
    editor, _ = await create_team_user(client, admin_id, "editor")
    r = await client.post("/api/v1/upload", headers=auth(editor),  # no X-Team-Id
                          data={"data": '[{"a": 1}]'})
    assert r.status_code == 200, r.text
    ds = r.json()["dataset_id"]

    # It landed in the editor's home team: visible to them, hidden elsewhere.
    listing = (await client.get("/api/v1/datasets", headers=auth(editor))).json()
    assert any(d["id"] == ds for d in listing["items"])
    outsider, _ = await create_team_user(client, admin_id, "viewer")
    assert (await client.get(f"/api/v1/datasets/{ds}",
                             headers=auth(outsider))).status_code == 404


async def test_spoofed_team_header_is_rejected(client, admin_id):
    editor, _ = await create_team_user(client, admin_id, "editor")
    _, other_team = await create_team_user(client, admin_id, "viewer")

    r = await client.post("/api/v1/upload",
                          headers={**auth(editor), "X-Team-Id": other_team},
                          data={"data": '[{"a": 1}]'})
    assert r.status_code == 403
    assert "not a member" in r.json()["detail"]

    # Same guard on the TUS creation path.
    r = await client.post("/api/v1/tus/", headers={
        **auth(editor), "X-Team-Id": other_team, "Upload-Length": "10",
        "Upload-Metadata": "filename YS5jc3Y="})  # a.csv
    assert r.status_code == 403


async def test_sample_data_sort_order_is_validated_not_coerced(client, admin_id):
    """`GET /samples/{filename}/data` used to read
    `"DESC" if sort_order.lower() == "desc" else "ASC"`, so every value it did
    not recognise — including `"descending"` — silently returned *ascending*
    rows under a 200. It is now Literal["asc","desc"], matching /aggregate and
    /pivot: a bad value is a 422, never a quietly reversed result set."""
    ds = (await upload_inline(
        client, admin_id,
        '[{"n": 1}, {"n": 3}, {"n": 2}]'))["dataset_id"]
    h = auth(admin_id)
    r = await client.post("/api/v1/sample", headers=h, json={
        "dataset_id": ds, "target_total_volume": 3,
        "sampling_steps": [{"method": "random", "sample_size": 3}]})
    fname = r.json()["sample_file"]
    url = f"/api/v1/samples/{fname}/data"

    # Both directions really are honoured.
    asc = await client.get(url, headers=h, params={"sort_by": "n", "sort_order": "asc"})
    assert asc.status_code == 200, asc.text
    assert [row["n"] for row in asc.json()["data"]] == [1, 2, 3]
    desc = await client.get(url, headers=h, params={"sort_by": "n", "sort_order": "desc"})
    assert [row["n"] for row in desc.json()["data"]] == [3, 2, 1]

    # Anything else is rejected. "DESC" is the dangerous one: it used to come
    # back ascending, which is the exact opposite of what was asked.
    for bad in ("DESC", "ASC", "descending", "Desc", ""):
        r = await client.get(url, headers=h, params={"sort_by": "n", "sort_order": bad})
        assert r.status_code == 422, f"{bad!r}: {r.text}"
        assert r.headers["content-type"].startswith(PROBLEM)
        assert "sort_order" in r.text

    # Omitted still defaults to ascending.
    r = await client.get(url, headers=h, params={"sort_by": "n"})
    assert [row["n"] for row in r.json()["data"]] == [1, 2, 3]

    # An invalid sort_order with no sort_by is still rejected — the old code
    # only looked at it inside `if sort_by`, so it was silently accepted.
    r = await client.get(url, headers=h, params={"sort_order": "DESC"})
    assert r.status_code == 422, r.text

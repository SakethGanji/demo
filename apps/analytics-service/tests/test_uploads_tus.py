"""TUS resumable uploads — /api/v1/tus/* (create, patch, status, terminate).

Drives the protocol the way tus-js-client would: announce with Upload-Length +
metadata, PATCH chunks at explicit offsets, poll status, and end with a real
ready dataset version. Error paths: unknown ids, offset mismatch, wrong
content-type, checksum rejection, cancellation.
"""

from __future__ import annotations

import base64
import hashlib

from conftest import DEFAULT_TEAM_ID, auth

CSV = b"customer_id,tier\n1,gold\n2,silver\n3,gold\n4,bronze\n"
TUS_CT = "application/offset+octet-stream"


def _meta(filename, **extra):
    pairs = {"filename": filename, **extra}
    return ", ".join(f"{k} {base64.b64encode(v.encode()).decode()}"
                     for k, v in pairs.items())


async def _create(client, user_id, *, filename="orders.csv", size=None):
    r = await client.post("/api/v1/tus/", headers={
        **auth(user_id), "X-Team-Id": DEFAULT_TEAM_ID,
        "Upload-Length": str(size if size is not None else len(CSV)),
        "Upload-Metadata": _meta(filename),
        "Tus-Resumable": "1.0.0",
    })
    assert r.status_code == 201, r.text
    location = r.headers["location"]
    assert location.startswith("/api/v1/tus/")
    return location


async def _patch(client, user_id, location, chunk, offset, **headers):
    return await client.patch(location, headers={
        **auth(user_id), "Content-Type": TUS_CT,
        "Upload-Offset": str(offset), "Tus-Resumable": "1.0.0", **headers,
    }, content=chunk)


async def test_tus_chunked_upload_to_ready_version(client, admin_id):
    location = await _create(client, admin_id)
    h = auth(admin_id)

    # Discovery advertises the protocol.
    opts = await client.options("/api/v1/tus/", headers=h)
    assert opts.status_code == 204 and "creation" in opts.headers["tus-extension"]

    # Offset check before any bytes.
    head = await client.head(location, headers=h)
    assert head.status_code == 200 and head.headers["upload-offset"] == "0"

    # Two chunks at explicit offsets.
    cut = len(CSV) // 2
    r = await _patch(client, admin_id, location, CSV[:cut], 0)
    assert r.status_code == 204 and r.headers["upload-offset"] == str(cut)
    r = await _patch(client, admin_id, location, CSV[cut:], cut)
    assert r.status_code == 204 and r.headers["upload-offset"] == str(len(CSV))

    # Final PATCH triggered processing (background task runs in-process).
    status = (await client.get(f"{location}/status", headers=h)).json()
    assert status["status"] == "complete", status
    assert status["row_count"] == 4
    ds = status["dataset_id"]

    versions = (await client.get(f"/api/v1/datasets/{ds}/versions",
                                 headers=h)).json()["items"]
    assert versions[0]["status"] == "ready" and versions[0]["row_count"] == 4


async def test_tus_offset_and_content_type_guards(client, admin_id):
    location = await _create(client, admin_id)

    # Wrong offset → 409 and no bytes accepted.
    r = await _patch(client, admin_id, location, CSV, 10)
    assert r.status_code == 409

    # Wrong content-type → 415.
    r = await client.patch(location, headers={
        **auth(admin_id), "Content-Type": "application/octet-stream",
        "Upload-Offset": "0"}, content=CSV)
    assert r.status_code == 415

    head = await client.head(location, headers=auth(admin_id))
    assert head.headers["upload-offset"] == "0"


async def test_tus_checksum_rejection_allows_retry(client, admin_id):
    location = await _create(client, admin_id)
    h = auth(admin_id)

    wrong = base64.b64encode(hashlib.sha256(b"other bytes").digest()).decode()
    r = await _patch(client, admin_id, location, CSV, 0,
                     **{"Upload-Checksum": f"sha256 {wrong}"})
    assert r.status_code == 460  # TUS checksum-mismatch

    # Server rolled the file back — same offset retries cleanly.
    head = await client.head(location, headers=h)
    assert head.headers["upload-offset"] == "0"

    good = base64.b64encode(hashlib.sha256(CSV).digest()).decode()
    r = await _patch(client, admin_id, location, CSV, 0,
                     **{"Upload-Checksum": f"sha256 {good}"})
    assert r.status_code == 204
    status = (await client.get(f"{location}/status", headers=h)).json()
    assert status["status"] == "complete"


async def test_tus_terminate_cancels_and_fails_version(client, admin_id):
    location = await _create(client, admin_id)
    h = auth(admin_id)
    await _patch(client, admin_id, location, CSV[:10], 0)

    # Mid-upload status knows the dataset/version already.
    status = (await client.get(f"{location}/status", headers=h)).json()
    assert status["status"] == "uploading"
    ds = status["dataset_id"]

    r = await client.request("DELETE", location, headers=h)
    assert r.status_code == 204

    # Upload state is gone; the version row is failed, not ready.
    assert (await client.head(location, headers=h)).status_code == 404
    versions = (await client.get(f"/api/v1/datasets/{ds}/versions",
                                 headers=h)).json()["items"]
    assert versions and versions[0]["status"] == "failed"


async def test_tus_unknown_id_and_create_validation(client, admin_id):
    h = auth(admin_id)
    ghost = "/api/v1/tus/deadbeefdeadbeefdeadbeefdeadbeef"
    assert (await client.head(ghost, headers=h)).status_code == 404
    assert (await _patch(client, admin_id, ghost, b"x", 0)).status_code == 404
    assert (await client.get(f"{ghost}/status", headers=h)).status_code == 404
    assert (await client.request("DELETE", ghost, headers=h)).status_code == 404

    # Creation requires Upload-Length and a supported extension.
    r = await client.post("/api/v1/tus/", headers={
        **h, "X-Team-Id": DEFAULT_TEAM_ID, "Upload-Metadata": _meta("a.csv")})
    assert r.status_code == 400
    r = await client.post("/api/v1/tus/", headers={
        **h, "X-Team-Id": DEFAULT_TEAM_ID, "Upload-Length": "10",
        "Upload-Metadata": _meta("a.txt")})
    assert r.status_code == 400

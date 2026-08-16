"""TUS follow-up routes — who may touch an upload, and how it ends.

Creation (POST /tus/) was always authorized; the four routes that operate on an
existing upload — HEAD, PATCH, DELETE and GET .../status — were not, and the
ways an upload can *finish* (cancelled, completed, abandoned, restarted
mid-flight) each had a bookkeeping hole. These tests drive the protocol the way
tus-js-client does and pin the endings.
"""

from __future__ import annotations

import base64
import hashlib
import json
import time

from conftest import DEFAULT_TEAM_ID, auth, create_team_user

CSV = b"customer_id,tier\n1,gold\n2,silver\n3,gold\n4,bronze\n"
TUS_CT = "application/offset+octet-stream"


def _meta(filename, **extra):
    pairs = {"filename": filename, **extra}
    return ", ".join(f"{k} {base64.b64encode(v.encode()).decode()}"
                     for k, v in pairs.items())


async def _create(client, user_id, *, filename="orders.csv", size=None,
                  team_id=DEFAULT_TEAM_ID):
    r = await client.post("/api/v1/tus/", headers={
        **auth(user_id), "X-Team-Id": team_id,
        "Upload-Length": str(size if size is not None else len(CSV)),
        "Upload-Metadata": _meta(filename),
        "Tus-Resumable": "1.0.0",
    })
    assert r.status_code == 201, r.text
    return r.headers["location"]


async def _patch(client, user_id, location, chunk, offset, **headers):
    return await client.patch(location, headers={
        **auth(user_id), "Content-Type": TUS_CT,
        "Upload-Offset": str(offset), "Tus-Resumable": "1.0.0", **headers,
    }, content=chunk)


async def _versions(client, user_id, dataset_id):
    r = await client.get(f"/api/v1/datasets/{dataset_id}/versions",
                         headers=auth(user_id))
    assert r.status_code == 200, r.text
    return r.json()["items"]


async def test_tus_follow_up_routes_reject_a_principal_from_another_team(
        client, admin_id):
    """The upload id was the only credential the follow-up routes checked, so
    anyone who came by one — a Location header echoed into a log, a proxy
    trace, a shared browser session — could append bytes to another team's
    upload, cancel it, or read back its preview rows. Creation authorizes
    against the dataset; these four must do the same, and must 404 rather than
    403 so they do not confirm the upload exists."""
    location = await _create(client, admin_id)
    outsider, _ = await create_team_user(client, admin_id, "editor")
    h = auth(outsider)

    assert (await client.head(location, headers=h)).status_code == 404
    assert (await _patch(client, outsider, location, CSV, 0)).status_code == 404
    assert (await client.get(f"{location}/status", headers=h)).status_code == 404
    assert (await client.request("DELETE", location, headers=h)).status_code == 404

    # The owner's upload is untouched: still open, still at offset zero.
    owner = auth(admin_id)
    head = await client.head(location, headers=owner)
    assert head.status_code == 200 and head.headers["upload-offset"] == "0"
    status = (await client.get(f"{location}/status", headers=owner)).json()
    assert status["status"] == "uploading"
    versions = await _versions(client, admin_id, status["dataset_id"])
    assert versions[0]["status"] == "uploading"


async def test_tus_terminate_after_completion_does_not_demote_the_ready_version(
        client, admin_id):
    """A tus client that DELETEs its Location URL as cleanup after a successful
    upload used to destroy the upload it had just made: terminate marked the
    version `failed` unconditionally, and every ready-filtered read then
    stopped resolving the data with no undo path anywhere in the API."""
    location = await _create(client, admin_id)
    h = auth(admin_id)

    r = await _patch(client, admin_id, location, CSV, 0)
    assert r.status_code == 204
    status = (await client.get(f"{location}/status", headers=h)).json()
    assert status["status"] == "complete", status
    ds = status["dataset_id"]

    r = await client.request("DELETE", location, headers=h)
    assert r.status_code == 204

    versions = await _versions(client, admin_id, ds)
    assert versions[0]["status"] == "ready", versions
    assert versions[0]["row_count"] == 4
    # Still the dataset's current version, so its rows still resolve.
    r = await client.get(f"/api/v1/datasets/{ds}", headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["row_count"] == 4


async def test_tus_create_rejects_unparseable_and_negative_upload_length(
        client, admin_id):
    """`int()` on the raw header was unguarded. `Upload-Length: abc` became a
    500 that blamed the server for the client's typo, and a NEGATIVE length was
    worse than an error: it passed the max-size and free-space checks, then
    satisfied `offset >= total_size` on the first PATCH, so the version went
    `ready` over a truncated file that nobody was told was truncated."""
    h = {**auth(admin_id), "X-Team-Id": DEFAULT_TEAM_ID,
         "Upload-Metadata": _meta("a.csv")}

    for bad in ("abc", "", "10.5", "-5"):
        r = await client.post("/api/v1/tus/", headers={**h, "Upload-Length": bad})
        assert r.status_code == 400, f"{bad!r}: {r.status_code} {r.text}"
        assert "Upload-Length" in r.json()["detail"]


async def test_tus_patch_rejects_a_non_numeric_upload_offset(client, admin_id):
    """Same unguarded `int()` on the PATCH side: a client sending a junk offset
    got a 500 instead of the 400 that tells it to re-read the offset via HEAD."""
    location = await _create(client, admin_id)

    r = await client.patch(location, headers={
        **auth(admin_id), "Content-Type": TUS_CT, "Upload-Offset": "abc"},
        content=CSV)
    assert r.status_code == 400, r.text
    assert "Upload-Offset" in r.json()["detail"]

    # And no bytes were accepted.
    head = await client.head(location, headers=auth(admin_id))
    assert head.headers["upload-offset"] == "0"


async def test_tus_create_rejects_malformed_upload_metadata(client, admin_id):
    """Upload-Metadata values are base64. Un-padded or non-UTF-8 values raised
    binascii.Error/UnicodeDecodeError straight through the handler and rendered
    as an opaque 500, so a client with a broken encoder had nothing to go on."""
    h = {**auth(admin_id), "X-Team-Id": DEFAULT_TEAM_ID, "Upload-Length": "10"}

    r = await client.post("/api/v1/tus/",
                          headers={**h, "Upload-Metadata": "filename YS5jc3Z"})
    assert r.status_code == 400, r.text
    assert r.json()["code"] == "invalid-upload-metadata"

    r = await client.post("/api/v1/tus/",
                          headers={**h, "Upload-Metadata": "filename /w=="})
    assert r.status_code == 400, r.text
    assert r.json()["code"] == "invalid-upload-metadata"


async def test_tus_checksum_mismatch_carries_a_machine_readable_code(
        client, admin_id):
    """460 is outside the service's status→title map, so the problem body came
    back with title "Error" and code "error" — indistinguishable from any other
    failure to a client branching on `code` rather than on the raw status."""
    location = await _create(client, admin_id)
    wrong = base64.b64encode(hashlib.sha256(b"other bytes").digest()).decode()

    r = await _patch(client, admin_id, location, CSV, 0,
                     **{"Upload-Checksum": f"sha256 {wrong}"})
    assert r.status_code == 460
    assert r.json()["code"] == "checksum-mismatch", r.text


async def test_tus_status_answers_from_the_database_after_a_restart(
        client, admin_id):
    """The TUS poller read only the in-process cache, while the on-disk upload
    state outlives the process by up to the 7-day expiry. After a restart a
    finished upload therefore reported "uploading" forever and a UI polling it
    never stopped spinning — the sibling /upload/status/{id} has answered from
    the DB since the ops hardening pass; this one had not."""
    location = await _create(client, admin_id)
    h = auth(admin_id)
    assert (await _patch(client, admin_id, location, CSV, 0)).status_code == 204

    status = (await client.get(f"{location}/status", headers=h)).json()
    vid = status["version_id"]

    # Simulate a process restart: the cache is gone, the meta file is not.
    from app.features.files.services.processing import processing_status
    processing_status.pop(vid, None)

    body = (await client.get(f"{location}/status", headers=h)).json()
    assert body["status"] == "complete", body
    assert body["row_count"] == 4


async def test_an_expired_tus_upload_closes_the_version_row_it_created(
        client, admin_id):
    """POST /tus/ creates a real dataset_versions row before a single byte
    arrives. The 7-day sweep deleted only the staging files, so an abandoned
    upload left that row at status `uploading` forever: permanently
    "processing" in the version list, holding a version number, and — once the
    meta file was gone — with a 404 as the only explanation available."""
    from app.infra.db.storage import uploads_dir

    location = await _create(client, admin_id)
    h = auth(admin_id)
    upload_id = location.rsplit("/", 1)[-1]
    ds = (await client.get(f"{location}/status", headers=h)).json()["dataset_id"]

    # Backdate the upload past the expiry window.
    meta_path = uploads_dir() / f"{upload_id}.meta.json"
    meta = json.loads(meta_path.read_text())
    meta["updated_at"] = time.time() - (8 * 24 * 3600)
    meta_path.write_text(json.dumps(meta))

    # Creating any upload runs the sweep opportunistically.
    await _create(client, admin_id, filename="other.csv")

    assert (await client.head(location, headers=h)).status_code == 404
    versions = await _versions(client, admin_id, ds)
    assert versions[0]["status"] == "failed", versions

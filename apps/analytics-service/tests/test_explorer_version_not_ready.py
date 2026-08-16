"""Explorer surfaces addressing a version whose bytes have not landed yet.

A version row exists the moment a TUS upload is *announced*, long before any
bytes or sheet rows exist. Every explorer surface that can be pointed at such a
version — directly by number, or indirectly through a saved view's selector —
must say so with ``code == "version-not-ready"``, because that is the only
answer whose remedy is "wait", and every neighbouring 404 on the same screen
means "navigate away" or "your view is broken".
"""

from __future__ import annotations

import base64
import json

from conftest import DEFAULT_TEAM_ID, auth, upload_inline

ROWS = [
    {"id": 1, "name": "alpha", "score": 10.0},
    {"id": 2, "name": "beta", "score": 30.0},
]


async def _dataset(client, user_id) -> str:
    return (await upload_inline(client, user_id, json.dumps(ROWS)))["dataset_id"]


async def _announce_upload(client, user_id, dataset_id,
                           *, filename="next.csv", size=64) -> None:
    """Announce a TUS upload — this alone creates a path-less 'uploading' version."""
    meta = ", ".join(
        f"{k} {base64.b64encode(v.encode()).decode()}"
        for k, v in {"filename": filename, "dataset_id": dataset_id}.items())
    r = await client.post("/api/v1/tus/", headers={
        **auth(user_id), "X-Team-Id": DEFAULT_TEAM_ID,
        "Upload-Length": str(size), "Upload-Metadata": meta,
        "Tus-Resumable": "1.0.0"})
    assert r.status_code == 201, r.text


async def test_running_a_saved_view_whose_tag_moved_onto_an_unfinished_upload_says_version_not_ready(
        client, admin_id):
    """A view pinned by tag must not accuse its own sheet when the tag moves.

    A tag can be moved onto a version that is still ``uploading``; such a
    version has no sheet rows at all, so the rename-proof lookup found nothing
    and the run answered ``sheet-not-in-version`` — "that sheet is not in this
    version of the dataset". In production that is a lie with an expensive
    remedy: the views list renders the tile as permanently broken and sends the
    user to repair or delete a view that is perfectly correct, when the actual
    answer is "the upload has not finished — wait, or move the tag back".
    """
    ds = await _dataset(client, admin_id)
    h = auth(admin_id)
    assert (await client.put(f"/api/v1/datasets/{ds}/tags", headers=h,
                             json={"tag_name": "live", "version_number": 1})
            ).status_code == 200

    base = f"/api/v1/datasets/{ds}/views"
    vid = (await client.post(base, headers=h, json={
        "name": "grid", "sheet": "data",
        "version_selector": {"mode": "tag", "tag": "live"},
        "query": {"limit": 10}})).json()["id"]
    assert (await client.post(f"{base}/{vid}/run", headers=h)).status_code == 200

    # v2 is announced but carries no bytes; the tag is moved onto it.
    await _announce_upload(client, admin_id, ds)
    assert (await client.put(f"/api/v1/datasets/{ds}/tags", headers=h,
                             json={"tag_name": "live", "version_number": 2})
            ).status_code == 200

    r = await client.post(f"{base}/{vid}/run", headers=h)
    assert r.status_code == 404, r.text
    assert r.headers["content-type"].startswith("application/problem+json")
    body = r.json()
    assert body["code"] == "version-not-ready", body
    assert body["version_status"] == "uploading"
    assert body["version_number"] == 2
    # The view itself is untouched — the tile is waiting, not broken.
    assert (await client.get(f"{base}/{vid}", headers=h)).status_code == 200

    # Moving the tag back is the repair the code now points at, and it works.
    assert (await client.put(f"/api/v1/datasets/{ds}/tags", headers=h,
                             json={"tag_name": "live", "version_number": 1})
            ).status_code == 200
    assert (await client.post(f"{base}/{vid}/run", headers=h)).status_code == 200


async def test_the_explorer_view_editor_reports_a_still_uploading_pin_the_same_way_preview_does(
        client, admin_id):
    """Create, retarget and preview must agree about a data-less version.

    All three are on the same screen. ``preview`` already answered
    ``version-not-ready``; creating a view pinned at that version answered the
    generic ``not_found`` ("Sheet not found: data") and retargeting an existing
    view answered ``sheet-not-in-version``. Three different diagnoses of one
    state means the editor cannot render one "still uploading, retry shortly"
    message, and two of the three blame the user's sheet choice.
    """
    ds = await _dataset(client, admin_id)
    h = auth(admin_id)
    base = f"/api/v1/datasets/{ds}/views"
    vid = (await client.post(base, headers=h, json={
        "name": "grid", "sheet": "data", "query": {"limit": 10}})).json()["id"]

    await _announce_upload(client, admin_id, ds)
    pin = {"mode": "version", "version_number": 2}

    preview = await client.get(f"/api/v1/datasets/{ds}/versions/2/preview", headers=h)
    created = await client.post(base, headers=h, json={
        "name": "pinned-at-v2", "sheet": "data",
        "version_selector": pin, "query": {"limit": 10}})
    retargeted = await client.patch(f"{base}/{vid}", headers=h,
                                    json={"version_selector": pin})

    for label, r in (("preview", preview), ("create", created),
                     ("retarget", retargeted)):
        assert r.status_code == 404, (label, r.text)
        assert r.json()["code"] == "version-not-ready", (label, r.json())
        assert r.json()["version_status"] == "uploading", (label, r.json())

    # Nothing was stored and nothing was repointed by the refused calls.
    listing = (await client.get(base, headers=h)).json()
    assert listing["total"] == 1
    assert listing["items"][0]["version_selector"] == {"mode": "current"}


async def test_listing_profile_runs_for_a_still_uploading_version_refuses_like_creating_them(
        client, admin_id):
    """GET and POST on /profile-runs must agree that the version is not ready.

    The listing was the one explorer route that skipped the guard: it resolved
    the version and returned an empty page. The Quality tab reads that as
    "ready, nothing profiled yet" and enables Run Profiling — which the POST on
    the same path then refuses with a 404, so the only way to discover the real
    state was to press a button that cannot work.
    """
    ds = await _dataset(client, admin_id)
    h = auth(admin_id)
    await _announce_upload(client, admin_id, ds)
    url = f"/api/v1/datasets/{ds}/versions/2/profile-runs"

    listed = await client.get(url, headers=h)
    created = await client.post(url, headers=h)

    assert listed.status_code == 404, listed.text
    assert listed.json()["code"] == "version-not-ready", listed.json()
    assert listed.json()["version_status"] == "uploading"
    assert created.status_code == 404 and created.json()["code"] == "version-not-ready"

    # The ready version still lists normally, so the guard is about data, not
    # about the route.
    ok = await client.get(f"/api/v1/datasets/{ds}/versions/1/profile-runs", headers=h)
    assert ok.status_code == 200 and ok.json()["total"] == 0


async def test_a_still_uploading_version_stays_invisible_to_another_team(
        client, admin_id):
    """version-not-ready must never leak across the 404-hides-existence line.

    The new code and its ``version_status`` field describe a version that an
    outsider is not allowed to know exists at all; if the guard ran before the
    RBAC check, the richer body would confirm the dataset to a stranger.
    """
    from conftest import create_team_user

    ds = await _dataset(client, admin_id)
    await _announce_upload(client, admin_id, ds)
    outsider, _ = await create_team_user(client, admin_id, "editor")

    r = await client.get(f"/api/v1/datasets/{ds}/versions/2/preview",
                         headers=auth(outsider))
    assert r.status_code == 404, r.text
    assert r.json().get("code") != "version-not-ready"
    assert "version_status" not in r.json()

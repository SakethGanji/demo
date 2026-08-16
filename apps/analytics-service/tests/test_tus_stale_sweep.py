"""TUS staging is reaped opportunistically — pinning that the sweep is awaited.

``tus_create`` calls :func:`cleanup_stale_uploads` so that abandoned resumable
uploads do not accumulate: staging bytes for a file nobody will finish, plus a
``dataset_versions`` row stuck at ``uploading`` that burns a version number and
shows up in the version list as permanently "processing".

That call is the *only* thing that ever runs the sweep, and it is an async
function called for its side effects. Called without ``await`` it returns a
coroutine that is dropped on the floor, so nothing is ever reaped, no version
is ever closed, and the only symptom is a ``RuntimeWarning`` in the log — which
is precisely the kind of failure no assertion in the TUS suite could see,
because every one of them is about the upload the test just started.
"""

from __future__ import annotations

import json
import time

from conftest import DEFAULT_TEAM_ID, auth

from app.features.files.services.tus import tus_meta_path, uploads_dir
from app.shared.constants import TUS_UPLOAD_EXPIRY_SECONDS

CSV = b"a,b\n1,2\n"


async def _create(client, user_id) -> str:
    r = await client.post("/api/v1/tus/", headers={
        **auth(user_id), "X-Team-Id": DEFAULT_TEAM_ID,
        "Upload-Length": str(len(CSV)),
        "Upload-Metadata": "filename YS5jc3Y=",
        "Tus-Resumable": "1.0.0",
    })
    assert r.status_code == 201, r.text
    return r.headers["location"].rsplit("/", 1)[-1]


def _backdate(upload_id: str) -> None:
    """Age an upload's metadata past the expiry the sweep judges it by."""
    path = tus_meta_path(upload_id)
    meta = json.loads(path.read_text())
    old = time.time() - TUS_UPLOAD_EXPIRY_SECONDS - 60
    meta["created_at"] = old
    meta["updated_at"] = old
    path.write_text(json.dumps(meta))


async def test_starting_an_upload_reaps_a_previously_abandoned_one(client, admin_id):
    """The opportunistic sweep must actually run, not just be scheduled.

    Without the ``await``, abandoned staging files and their ``uploading``
    version rows live forever: staging disk grows with every abandoned upload
    and the dataset's version list fills with rows that never resolve.
    """
    abandoned = await _create(client, admin_id)
    assert tus_meta_path(abandoned).exists()
    _backdate(abandoned)

    # Any subsequent create is the sweep's trigger.
    await _create(client, admin_id)

    assert not tus_meta_path(abandoned).exists(), sorted(
        p.name for p in uploads_dir().glob("*.meta.json"))
    # And the upload is genuinely unreachable, not merely missing its file.
    r = await client.head(f"/api/v1/tus/{abandoned}", headers=auth(admin_id))
    assert r.status_code == 404, r.text


async def test_the_sweep_leaves_a_live_upload_alone(client, admin_id):
    """Reaping is by age, so a fresh concurrent upload must survive it.

    A sweep that took every staged upload would destroy uploads in progress
    the moment any other client started one.
    """
    live = await _create(client, admin_id)
    await _create(client, admin_id)

    assert tus_meta_path(live).exists()
    r = await client.head(f"/api/v1/tus/{live}", headers=auth(admin_id))
    assert r.status_code == 200, r.text

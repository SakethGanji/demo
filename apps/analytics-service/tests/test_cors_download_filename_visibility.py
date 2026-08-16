"""A real download must hand the browser both the filename and permission to read it.

The unit companion (tests/unit/test_cors_exposed_response_headers.py) pins the
exposed *list*; this pins the pairing on a live response, because the bug only
bites when both halves are present: the download endpoints do send
``Content-Disposition: attachment; filename="orders_v3.csv"``, and CORS used to
hide it from JS. A cross-origin UI therefore got a real file with no name and
saved it as the URL's last segment ("download"), so two versions of the same
dataset overwrote each other in the user's Downloads folder.

Asserting the header and its exposure together is what makes this a regression
test rather than two independent facts: renaming or dropping either side —
changing how downloads name files, or trimming the CORS list — breaks it.
"""

from __future__ import annotations

import json

from conftest import auth, upload_inline

ORIGIN = "https://console.example.com"
ROWS = [{"region": "EU", "amount": 100.5}, {"region": "US", "amount": 50.5}]


def _exposed(response) -> set[str]:
    raw = response.headers.get("access-control-expose-headers", "")
    return {h.strip().lower() for h in raw.split(",") if h.strip()}


async def test_dataset_download_names_the_file_and_lets_a_browser_read_the_name(
        client, admin_id):
    ds = (await upload_inline(client, admin_id, json.dumps(ROWS)))["dataset_id"]

    r = await client.get(f"/api/v1/datasets/{ds}/download",
                         headers={**auth(admin_id), "Origin": ORIGIN})

    assert r.status_code == 200, r.text
    assert "filename=" in r.headers.get("content-disposition", "")
    assert "content-disposition" in _exposed(r)


async def test_version_download_filename_is_readable_cross_origin(client, admin_id):
    """The versioned download is the one whose name (…_v1.csv) actually matters."""
    ds = (await upload_inline(client, admin_id, json.dumps(ROWS)))["dataset_id"]

    r = await client.get(f"/api/v1/datasets/{ds}/versions/1/download?format=csv",
                         headers={**auth(admin_id), "Origin": ORIGIN})

    assert r.status_code == 200, r.text
    assert "_v1.csv" in r.headers.get("content-disposition", "")
    assert "content-disposition" in _exposed(r)

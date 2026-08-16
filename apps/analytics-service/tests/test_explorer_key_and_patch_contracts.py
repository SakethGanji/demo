"""Explorer contracts a client can only get right if the server does.

Two defects, both of the "silently wrong, no error anywhere" kind:

* the SQL console wrote its result parquet under the *raw* path parameter and
  registered it under the *canonical* dataset id, so an upper-case UUID in the
  URL produced an artifact row pointing at nothing;
* ``PATCH /views/{id}`` could not clear a description, because an explicit
  ``null`` was indistinguishable from an omitted field.
"""

from __future__ import annotations

import json

from sqlalchemy import text

from app.infra.db.postgres import async_session_factory
from conftest import auth, upload_inline

ROWS = [
    {"id": 1, "name": "alpha", "score": 10.5},
    {"id": 2, "name": "beta", "score": 20.0},
    {"id": 3, "name": "gamma", "score": 30.25},
]

VIEW_BODY = {
    "name": "high-scores",
    "description": "score > 15, best first",
    "sheet": "data",
    "query": {
        "filters": {"conditions": [{"column": "score", "op": "gt", "value": 15}]},
        "sort": [{"column": "score", "direction": "desc"}],
        "limit": 2,
    },
}


async def _dataset(client, admin_id):
    return (await upload_inline(client, admin_id, json.dumps(ROWS)))["dataset_id"]


# --- storage key must not depend on how the URL spelled the uuid --------------

async def test_sql_results_stay_reachable_when_the_url_uuid_is_upper_case(
        client, admin_id):
    """The SQL result parquet must be written under the key it is registered at.

    Postgres compares uuids by value, so ``/datasets/{DS_UPPER}/versions/1/sql``
    authorizes and runs exactly like the lower-case spelling. A storage key is
    a plain string, though: writing under the raw path parameter while
    registering under ``str(ds["id"])`` puts the file at
    ``artifacts/{team}/{DS_UPPER}/query_output/...`` and the artifact row at
    ``.../{ds_lower}/...``. The artifact row is the only way to resolve a blob,
    so the analyst's query result 404s on download and the bytes that were
    written are unreachable forever — and nothing errors at write time.
    """
    ds = await _dataset(client, admin_id)
    h = auth(admin_id)

    r = await client.post(f"/api/v1/datasets/{ds.upper()}/versions/1/sql",
                          headers=h, json={"sql": "SELECT * FROM data"})
    assert r.status_code == 200, r.text
    result_file = r.json()["result_file"]
    assert result_file

    fetched = await client.get(f"/api/v1/samples/{result_file}/data", headers=h)
    assert fetched.status_code == 200, fetched.text
    body = fetched.json()
    rows = body["data"] if isinstance(body, dict) else body
    assert len(rows) == len(ROWS)

    # A registered artifact whose key resolves also has a size; None means the
    # sizing probe missed the object, which is the same mismatch from the
    # other end.
    async with async_session_factory() as s:
        row = (await s.execute(
            text("SELECT storage_key, size_bytes FROM artifacts "
                 "WHERE filename = :f"),
            {"f": result_file})).mappings().first()
    assert row is not None, "the SQL result must be registered as an artifact"
    assert row["size_bytes"], (
        "the registered key must point at the parquet that was written")
    assert ds.upper() not in row["storage_key"], (
        "the key must use the canonical dataset id, not the URL spelling")


# --- PATCH must be able to clear a nullable column ----------------------------

async def test_patching_a_view_description_to_null_clears_it(client, admin_id):
    """``{"description": null}`` is a request to clear, not a no-op.

    ``description`` is the one nullable, free-text column on a saved view, and
    ``if body.description is not None`` collapsed "omitted" and "explicitly
    null" into the same branch. A user who removed the text from the
    description box and saved got a 200 and the old description back, with no
    way to ever remove it short of deleting and re-creating the view (which
    changes its id and breaks every bookmark to it).
    """
    ds = await _dataset(client, admin_id)
    h = auth(admin_id)
    base = f"/api/v1/datasets/{ds}/views"

    view = (await client.post(base, headers=h, json=VIEW_BODY)).json()
    vid = view["id"]
    assert view["description"] == "score > 15, best first"

    r = await client.patch(f"{base}/{vid}", headers=h, json={"description": None})
    assert r.status_code == 200, r.text
    assert r.json()["description"] is None, "an explicit null must clear it"

    # And it is persisted, not just echoed.
    r = await client.get(f"{base}/{vid}", headers=h)
    assert r.status_code == 200 and r.json()["description"] is None

    # Omitting the field still leaves the column alone.
    await client.patch(f"{base}/{vid}", headers=h, json={"description": "back"})
    r = await client.patch(f"{base}/{vid}", headers=h, json={"name": "renamed"})
    assert r.status_code == 200, r.text
    assert r.json()["description"] == "back" and r.json()["name"] == "renamed"

    # An explicit null for the NOT NULL name is still ignored, not a 500.
    r = await client.patch(f"{base}/{vid}", headers=h, json={"name": None})
    assert r.status_code == 200, r.text
    assert r.json()["name"] == "renamed"

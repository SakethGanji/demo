"""Documentation coverage counts CONTENT, not the mere existence of a row.

A data-dictionary column entry with every field null documents nothing, so it
must not inflate the health documentation dimension (nor the catalog facet /
?documentation= filter). The sheet side already guards on content; the column
side did not, so an empty PUT — or blanking a real entry — left the column
counted as documented.
"""

from __future__ import annotations

import json

from conftest import auth, upload_inline

_ROWS = [{"region": "EU", "amount": 100}, {"region": "US", "amount": 200}]


async def _documented_columns(client, uid, ds):
    r = await client.get(f"/api/v1/datasets/{ds}/health", headers=auth(uid))
    assert r.status_code == 200, r.text
    return r.json()["dimensions"]["documentation"]["evidence"]["documented_columns"]


async def _put_col(client, uid, ds, col, body):
    r = await client.put(f"/api/v1/datasets/{ds}/sheet-metadata/data/columns/{col}",
                         headers=auth(uid), json=body)
    assert r.status_code == 200, r.text
    return r.json()


async def test_empty_column_entries_are_not_counted_as_documented(client, admin_id):
    ds = (await upload_inline(client, admin_id, json.dumps(_ROWS)))["dataset_id"]
    for col in ("region", "amount"):
        stored = await _put_col(client, admin_id, ds, col, {})
        assert stored["business_name"] is None and stored["description"] is None
    assert await _documented_columns(client, admin_id, ds) == 0


async def test_a_real_entry_counts_and_blanking_it_uncounts(client, admin_id):
    ds = (await upload_inline(client, admin_id, json.dumps(_ROWS)))["dataset_id"]
    await _put_col(client, admin_id, ds, "region", {"business_name": "Sales region"})
    assert await _documented_columns(client, admin_id, ds) == 1

    # Clear the only content field back to null: the entry no longer documents
    # anything and must stop being counted.
    await _put_col(client, admin_id, ds, "region", {"business_name": None})
    assert await _documented_columns(client, admin_id, ds) == 0


async def test_catalog_documentation_facet_ignores_blank_only_entries(client, admin_id):
    ds = (await upload_inline(client, admin_id, json.dumps(_ROWS)))["dataset_id"]
    await _put_col(client, admin_id, ds, "region", {})
    # A dataset whose only "documentation" is a blank entry is undocumented, so
    # it appears under ?documentation=none, not =partial.
    none_ids = {d["id"] for d in (await client.get(
        "/api/v1/datasets?documentation=none", headers=auth(admin_id))).json()["items"]}
    assert ds in none_ids

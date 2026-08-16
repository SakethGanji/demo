"""Discovery metadata routes must resolve the thing they are documenting.

Two ways a write could name a target that does not exist and still look like a
success:

1. ``PUT /datasets/{id}/sheet-metadata/{sheet_key}`` stored a record for ANY
   key. A typo answered 200, wrote a row whose ``logical_sheet_id`` was null,
   and left it there for good — this path has no DELETE.
2. The GET/DELETE column-dictionary fallback passed the RAW url segment to the
   repo once the column had left the current version's schema, so the physical
   spelling that created an entry could no longer reach it.
"""

from __future__ import annotations

import json

from openpyxl import Workbook

from conftest import XLSX_MIME, auth, upload_file, upload_inline

ROWS = [{"region": "EU", "amount": 100.0}, {"region": "US", "amount": 50.0}]


def _wb(path, headers):
    wb = Workbook()
    ws = wb.active
    ws.title = "Accounts"
    ws.append(headers)
    ws.append(["Acme", "EU"][: len(headers)])
    wb.save(path)


async def _upload_wb(client, admin_id, path, *, dataset_id=None):
    return await upload_file(client, admin_id, path, name="book.xlsx",
                             content_type=XLSX_MIME, dataset_id=dataset_id)


async def test_putting_sheet_metadata_on_a_sheet_that_does_not_exist_is_a_404(
    client, admin_id,
):
    """A misspelled sheet key must be rejected, not stored.

    In production a 200 here is a silent data-loss report: the analyst is told
    the grain and primary key were recorded, the record is orphaned (no
    ``logical_sheet_id``, so it never counts toward documentation coverage and
    never shows against the sheet), and nothing can remove it because the path
    has no DELETE. The column routes under this same prefix already 404 for the
    identical typo, and the MCP write path documents that its PUT fallback
    surfaces "the sheet does not exist" — which only holds if PUT says so.
    """
    ds = (await upload_inline(client, admin_id, json.dumps(ROWS)))["dataset_id"]
    h = auth(admin_id)

    r = await client.put(f"/api/v1/datasets/{ds}/sheet-metadata/dat", headers=h,
                         json={"grain": "one row per order"})
    assert r.status_code == 404, r.text
    assert "dat" in r.json()["detail"]

    # Nothing was written, so the typo cannot come back as a phantom record.
    listed = (await client.get(f"/api/v1/datasets/{ds}/sheet-metadata",
                               headers=h)).json()
    assert listed["total"] == 0

    # The real sheet still works, under any spelling PUT normalizes.
    r = await client.put(f"/api/v1/datasets/{ds}/sheet-metadata/Data", headers=h,
                         json={"grain": "one row per order"})
    assert r.status_code == 200, r.text
    assert r.json()["sheet_key"] == "data"
    assert r.json()["logical_sheet_id"]


async def test_sheet_metadata_can_still_be_written_for_a_sheet_dropped_from_the_current_version(
    client, admin_id, tmp_path,
):
    """Resolving the sheet must not mean "present in the CURRENT version".

    Logical sheets are never retired, so documenting a sheet that stopped
    arriving — the case where you most want to write down what it used to
    mean — has to keep working. A check against the current version's sheets
    instead of the logical ones would break exactly that.
    """
    v1, v2 = tmp_path / "v1.xlsx", tmp_path / "v2.xlsx"
    _wb(v1, ["Business Name", "Region"])
    ds = (await _upload_wb(client, admin_id, v1))["dataset_id"]
    h = auth(admin_id)

    wb = Workbook()
    wb.active.title = "Other"
    wb.active.append(["Region"])
    wb.active.append(["EU"])
    wb.save(v2)
    await _upload_wb(client, admin_id, v2, dataset_id=ds)

    r = await client.put(f"/api/v1/datasets/{ds}/sheet-metadata/Accounts",
                         headers=h, json={"grain": "one row per account"})
    assert r.status_code == 200, r.text
    assert r.json()["sheet_key"] == "accounts" and r.json()["logical_sheet_id"]


async def test_a_dictionary_entry_is_reachable_by_its_physical_name_after_the_column_is_dropped(
    client, admin_id, tmp_path,
):
    """The gone-column fallback must apply the SAME normalization as ingest.

    ``PUT .../columns/Business Name`` stores the entry as ``business_name``.
    Once the column leaves the current version there is no schema to resolve
    against, and the fallback used to hand the repo the raw URL segment — so in
    production the spelling that WROTE the documentation could no longer read
    or delete it, and a caller who had only ever seen the physical header
    concluded the entry was gone while it sat in the list route.
    """
    v1, v2 = tmp_path / "v1.xlsx", tmp_path / "v2.xlsx"
    _wb(v1, ["Business Name", "Region"])
    _wb(v2, ["Region"])
    ds = (await _upload_wb(client, admin_id, v1))["dataset_id"]
    h = auth(admin_id)
    base = f"/api/v1/datasets/{ds}/sheet-metadata/Accounts/columns"

    r = await client.put(f"{base}/Business Name", headers=h,
                         json={"business_name": "Legal entity"})
    assert r.status_code == 200, r.text
    assert r.json()["column_name"] == "business_name"

    await _upload_wb(client, admin_id, v2, dataset_id=ds)

    # The entry is still listed under its normalized name...
    listed = (await client.get(base, headers=h)).json()
    assert [e["column_name"] for e in listed["items"]] == ["business_name"]

    # ...and the physical spelling still reaches it, for read and for delete.
    r = await client.get(f"{base}/Business Name", headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["business_name"] == "Legal entity"

    assert (await client.delete(f"{base}/Business Name",
                                headers=h)).status_code == 204
    assert (await client.get(base, headers=h)).json()["total"] == 0

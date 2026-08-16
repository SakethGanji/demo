"""Confirming a rename must not silently destroy the new sheet's own objects.

``reassign_logical_sheet`` folds the spurious identity into the surviving one
and then ``DELETE``s it. Four tables reference ``dataset_sheets(id)`` with
``ON DELETE CASCADE`` — ``transformation_definitions`` (and, through it, every
``transformation_run``), ``dataset_views``, and both endpoints of
``dataset_relationships``. The 409 conflict guard counted none of them.

So if anyone built a pipeline, a saved view or a relationship against the new
version's sheet BEFORE the rename was confirmed, confirm-rename answered 200
and their work was gone — no 409, no audit entry naming what was deleted, and
no way to get it back. The migrations' claim that these tables "need no
rewriting" on a rename is true of the SURVIVING identity and exactly wrong for
the spurious one the rename deletes.

These tests pin that each such object is counted, reported by name in
``attached``, and still present after the refusal.
"""

from __future__ import annotations

from openpyxl import Workbook

from conftest import XLSX_MIME, auth, upload_file


def _wb_with_costs(path, sheet_name):
    """Revenue + one costs sheet, so the rename has a same-schema candidate."""
    wb = Workbook()
    ws = wb.active
    ws.title = "Revenue"
    ws.append(["Amount", "Region"])
    ws.append([100, "EU"])
    costs = wb.create_sheet(sheet_name)
    costs.append(["Item", "Cost"])
    costs.append(["rent", 50])
    wb.save(path)


async def _renamed_dataset(client, admin_id, tmp_path):
    """v1 has 'Expenses'; v2 renames it to 'Ops' (same schema, uncconfirmed)."""
    v1, v2 = tmp_path / "v1.xlsx", tmp_path / "v2.xlsx"
    _wb_with_costs(v1, "Expenses")
    _wb_with_costs(v2, "Ops")
    ds = (await upload_file(client, admin_id, v1, name="book.xlsx",
                            content_type=XLSX_MIME))["dataset_id"]
    await upload_file(client, admin_id, v2, name="book.xlsx",
                      content_type=XLSX_MIME, dataset_id=ds)
    return ds


async def _confirm(client, admin_id, ds):
    return await client.post(f"/api/v1/datasets/{ds}/versions/2/confirm-rename",
                             headers=auth(admin_id),
                             json={"from_sheet": "Expenses", "to_sheet": "Ops"})


async def test_a_transformation_on_the_new_sheet_blocks_the_rename(
        client, admin_id, tmp_path):
    ds = await _renamed_dataset(client, admin_id, tmp_path)
    h = auth(admin_id)

    did = (await client.post(f"/api/v1/datasets/{ds}/transformations", headers=h,
                             json={"name": "cleanup", "sheet": "Ops",
                                   "steps": [{"type": "limit", "count": 1}]}))
    assert did.status_code in (200, 201), did.text
    did = did.json()["id"]

    r = await _confirm(client, admin_id, ds)
    assert r.status_code == 409, r.text
    body = r.json()
    assert body["code"] == "conflicting-sheet-state"
    assert body["attached"]["transformations"] == 1

    # And the definition is still there — the refusal actually protected it.
    assert (await client.get(f"/api/v1/datasets/{ds}/transformations/{did}",
                             headers=h)).status_code == 200


async def test_a_transformation_run_on_the_new_sheet_is_counted_too(
        client, admin_id, tmp_path):
    """Run history cascades through the definition, so it is reported too."""
    ds = await _renamed_dataset(client, admin_id, tmp_path)
    h = auth(admin_id)

    did = (await client.post(f"/api/v1/datasets/{ds}/transformations", headers=h,
                             json={"name": "cleanup", "sheet": "Ops",
                                   "steps": [{"type": "limit", "count": 1}]})
           ).json()["id"]
    r = await client.post(f"/api/v1/datasets/{ds}/transformations/{did}/run",
                          headers=h)
    assert r.status_code == 200, r.text

    r = await _confirm(client, admin_id, ds)
    assert r.status_code == 409, r.text
    assert r.json()["attached"]["transformation_runs"] == 1


async def test_a_saved_view_on_the_new_sheet_blocks_the_rename(
        client, admin_id, tmp_path):
    ds = await _renamed_dataset(client, admin_id, tmp_path)
    h = auth(admin_id)

    view = await client.post(f"/api/v1/datasets/{ds}/views", headers=h,
                             json={"name": "ops-costs", "sheet": "Ops",
                                   "query": {}})
    assert view.status_code in (200, 201), view.text
    view_id = view.json()["id"]

    r = await _confirm(client, admin_id, ds)
    assert r.status_code == 409, r.text
    assert r.json()["attached"]["saved_views"] == 1

    assert (await client.get(f"/api/v1/datasets/{ds}/views/{view_id}",
                             headers=h)).status_code == 200


async def test_a_relationship_on_the_new_sheet_blocks_the_rename(
        client, admin_id, tmp_path):
    ds = await _renamed_dataset(client, admin_id, tmp_path)
    h = auth(admin_id)

    rel = await client.post(f"/api/v1/datasets/{ds}/relationships", headers=h,
                            json={"from_sheet": "Ops", "from_column": "item",
                                  "to_sheet": "Revenue", "to_column": "region"})
    assert rel.status_code in (200, 201), rel.text

    r = await _confirm(client, admin_id, ds)
    assert r.status_code == 409, r.text
    assert r.json()["attached"]["relationships"] == 1


async def test_a_clean_rename_still_confirms(client, admin_id, tmp_path):
    """The guard must not make confirm-rename unusable in the normal case."""
    ds = await _renamed_dataset(client, admin_id, tmp_path)
    h = auth(admin_id)

    # State on the SURVIVING identity is precisely what the rename carries
    # over, so it must not be counted as a conflict.
    r = await client.put(f"/api/v1/datasets/{ds}/sheet-metadata/Expenses",
                         headers=h, json={"grain": "per expense"})
    assert r.status_code == 200, r.text

    r = await _confirm(client, admin_id, ds)
    assert r.status_code == 200, r.text
    assert r.json()["to_sheet"] == "Ops"

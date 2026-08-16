"""Editing a saved view must resolve its sheet the way running it does.

``run_view`` resolves the sheet by logical id — that is §1's whole payoff, and
``test_saved_views.py::test_view_survives_confirmed_rename`` pins it. But
``update_view`` resolved it by *name*, and by the logical sheet's CURRENT name
at that, against whatever version the view pins. After a confirmed rename, a
version-pinned view therefore ran fine and could not be edited at all: every
PATCH that touched the query came back 404 "Sheet not found: <new name>",
naming a sheet the user never typed.
"""

from __future__ import annotations

from conftest import XLSX_MIME, auth, make_workbook, upload_file


async def _renamed_dataset(client, admin_id, tmp_path):
    """A dataset whose 'Expenses' sheet became 'Spending' in version 2."""
    v1, v2 = tmp_path / "v1.xlsx", tmp_path / "v2.xlsx"
    make_workbook(v1)                            # Revenue / Expenses / Secrets
    make_workbook(v2, second_sheet="Spending")   # same schema, new name
    ds = (await upload_file(client, admin_id, v1, name="book.xlsx",
                            content_type=XLSX_MIME))["dataset_id"]
    await upload_file(client, admin_id, v2, name="book.xlsx",
                      content_type=XLSX_MIME, dataset_id=ds)
    r = await client.post(f"/api/v1/datasets/{ds}/versions/2/confirm-rename",
                          headers=auth(admin_id),
                          json={"from_sheet": "Expenses", "to_sheet": "Spending"})
    assert r.status_code == 200, r.text
    return ds


async def test_a_version_pinned_view_is_still_editable_after_a_confirmed_rename(
        client, admin_id, tmp_path):
    ds = await _renamed_dataset(client, admin_id, tmp_path)
    h = auth(admin_id)
    base = f"/api/v1/datasets/{ds}/views"

    view = (await client.post(base, headers=h, json={
        "name": "costs-v1", "sheet": "Expenses",
        "version_selector": {"mode": "version", "version_number": 1},
        "query": {"sort": [{"column": "cost", "direction": "desc"}]}})).json()
    # The listing reports the sheet's CURRENT name — this is what update_view
    # used to feed back into a lookup against version 1, where it cannot exist.
    assert view["sheet_name"] == "Spending"

    r = await client.patch(f"{base}/{view['id']}", headers=h, json={
        "query": {"sort": [{"column": "cost", "direction": "asc"}]}})
    assert r.status_code == 200, r.text
    assert r.json()["query"]["sort"] == [{"column": "cost", "direction": "asc"}]
    assert r.json()["logical_sheet_id"] == view["logical_sheet_id"]

    # Still pinned, still runnable, still the same rows.
    r = await client.post(f"{base}/{view['id']}/run", headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["version_number"] == 1
    assert r.json()["sheet_name"] == "Expenses"
    assert [i["Item"] for i in r.json()["result"]["items"]] == ["power", "rent"]


async def test_pinning_an_older_version_on_a_renamed_sheet_view_still_works(
        client, admin_id, tmp_path):
    """The same failure from the other direction.

    A current-mode view over a renamed sheet PATCHed to pin version 1: the
    sheet is unchanged, so its identity must carry back to the older version
    even though the older version knows it by the older name.
    """
    ds = await _renamed_dataset(client, admin_id, tmp_path)
    h = auth(admin_id)
    base = f"/api/v1/datasets/{ds}/views"

    view = (await client.post(base, headers=h, json={
        "name": "costs", "sheet": "Spending"})).json()

    r = await client.patch(f"{base}/{view['id']}", headers=h, json={
        "version_selector": {"mode": "version", "version_number": 1}})
    assert r.status_code == 200, r.text
    assert r.json()["logical_sheet_id"] == view["logical_sheet_id"]

    r = await client.post(f"{base}/{view['id']}/run", headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["version_number"] == 1 and r.json()["sheet_name"] == "Expenses"


async def test_repointing_a_view_at_a_different_sheet_by_name_still_works(
        client, admin_id, tmp_path):
    """Resolving by logical id must not disable deliberate retargeting.

    When the caller actually supplies ``sheet``, that name is the instruction
    and the view's logical identity has to move to it.
    """
    ds = await _renamed_dataset(client, admin_id, tmp_path)
    h = auth(admin_id)
    base = f"/api/v1/datasets/{ds}/views"

    view = (await client.post(base, headers=h, json={
        "name": "costs", "sheet": "Spending"})).json()

    r = await client.patch(f"{base}/{view['id']}", headers=h,
                           json={"sheet": "Revenue"})
    assert r.status_code == 200, r.text
    assert r.json()["logical_sheet_id"] != view["logical_sheet_id"]
    assert r.json()["sheet_name"] == "Revenue"

    r = await client.patch(f"{base}/{view['id']}", headers=h,
                           json={"sheet": "NoSuchSheet"})
    assert r.status_code == 404 and "NoSuchSheet" in r.json()["detail"]


async def test_patching_a_view_whose_sheet_is_absent_from_the_pinned_version(
        client, admin_id, tmp_path):
    """A sheet that simply is not in the pinned version is a typed 404.

    Same ``sheet-not-in-version`` contract ``run`` already uses, so a UI can
    branch on one code instead of two.
    """
    v1, v2 = tmp_path / "v1.xlsx", tmp_path / "v2.xlsx"
    make_workbook(v1)
    # No confirm-rename this time, so 'Spending' is a NEW logical sheet that
    # version 1 has never heard of.
    make_workbook(v2, second_sheet="Spending")
    ds = (await upload_file(client, admin_id, v1, name="book.xlsx",
                            content_type=XLSX_MIME))["dataset_id"]
    await upload_file(client, admin_id, v2, name="book.xlsx",
                      content_type=XLSX_MIME, dataset_id=ds)
    h = auth(admin_id)
    base = f"/api/v1/datasets/{ds}/views"

    view = (await client.post(base, headers=h, json={
        "name": "spending", "sheet": "Spending",
        "version_selector": {"mode": "version", "version_number": 2}})).json()

    r = await client.patch(f"{base}/{view['id']}", headers=h, json={
        "version_selector": {"mode": "version", "version_number": 1}})
    assert r.status_code == 404, r.text
    assert r.json()["code"] == "sheet-not-in-version"
    assert r.json()["version_number"] == 1

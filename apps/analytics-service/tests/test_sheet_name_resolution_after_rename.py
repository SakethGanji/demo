"""Name-addressed sheet resolution must span a confirmed rename.

ROADMAP §1 keeps URLs name-addressed and states that "resolution gains one hop:
name -> version sheet row -> logical id ... resolution goes logical-id-first".
The shared resolver (``app/shared/datasets.py::_find_sheet``) only ever matched
the *per-version* ``sheet_name``/``sheet_key``, which are frozen at ingest and
deliberately untouched by confirm-rename (parquet layout is keyed by them).

What breaks in production without this: after a user confirms "Expenses" ->
"Operating Costs", every screen still shows the sheet under its current name --
that is what ``GET /datasets/{id}/sheets`` and the dictionary return. Pin an
older version in the version picker and click that same sheet and the request
carries ``sheet=Operating Costs`` against version 1, whose row is still named
"Expenses". Sampling, profiling, exports and the schema diff all 404 with
"Sheet not found", so the rename silently strands every historical version
behind a name the UI no longer knows.
"""

from __future__ import annotations

from conftest import XLSX_MIME, auth, make_workbook, upload_file


async def _upload(client, admin_id, path, *, dataset_id=None):
    return await upload_file(client, admin_id, path, name="book.xlsx",
                             content_type=XLSX_MIME, dataset_id=dataset_id)


async def _renamed_dataset(client, admin_id, tmp_path):
    """Two versions plus a confirmed Expenses -> Operating Costs rename."""
    v1, v2 = tmp_path / "v1.xlsx", tmp_path / "v2.xlsx"
    make_workbook(v1, second_sheet="Expenses")
    make_workbook(v2, second_sheet="Operating Costs")
    ds = (await _upload(client, admin_id, v1))["dataset_id"]
    await _upload(client, admin_id, v2, dataset_id=ds)
    h = auth(admin_id)
    r = await client.post(f"/api/v1/datasets/{ds}/versions/2/confirm-rename",
                          headers=h,
                          json={"from_sheet": "Expenses", "to_sheet": "Operating Costs"})
    assert r.status_code == 200, r.text
    return ds, h


async def test_the_current_sheet_name_resolves_against_an_older_version_after_a_confirmed_rename(
    client, admin_id, tmp_path,
):
    """The name the UI displays must address the sheet in every version.

    Without this, pinning version 1 and clicking the sheet the picker calls
    "Operating Costs" 404s, because version 1's row is still named "Expenses".
    """
    ds, h = await _renamed_dataset(client, admin_id, tmp_path)

    # The sheet picker only ever offers the current name.
    names = {s["name"] for s in (await client.get(
        f"/api/v1/datasets/{ds}/sheets", headers=h)).json()["items"]}
    assert "Operating Costs" in names and "Expenses" not in names

    # ...and that name addresses the sheet in the OLD version too.
    r = await client.post("/api/v1/sample", headers=h, json={
        "dataset_id": ds, "version_number": 1, "sheet": "Operating Costs",
        "target_total_volume": 1,
        "sampling_steps": [{"method": "random", "sample_size": 1}],
    })
    assert r.status_code == 200, r.text
    assert r.json()["sampled_count"] == 1

    # The metadata read for the pinned version resolves under the same name.
    r = await client.get(f"/api/v1/datasets/{ds}/versions/1/sheets", headers=h)
    assert r.status_code == 200, r.text
    v1_names = {s["name"] for s in r.json()["items"]}
    assert "Expenses" in v1_names  # the frozen per-version name is unchanged


async def test_the_per_version_sheet_name_still_wins_over_a_logical_alias(
    client, admin_id, tmp_path,
):
    """Logical-name matching is a fallback, never an override.

    Version 1 physically contains a sheet named "Expenses"; resolving that name
    against version 1 must keep returning version 1's own row, not some other
    sheet whose logical identity happens to answer to it. Without the ordering
    a rename could silently redirect a historical query to the wrong parquet.
    """
    ds, h = await _renamed_dataset(client, admin_id, tmp_path)

    r = await client.post("/api/v1/sample", headers=h, json={
        "dataset_id": ds, "version_number": 1, "sheet": "Expenses",
        "target_total_volume": 1,
        "sampling_steps": [{"method": "random", "sample_size": 1}],
    })
    assert r.status_code == 200, r.text

    # The retired name does NOT leak forward onto the new version: version 2
    # has no sheet called "Expenses" and must keep saying so.
    r = await client.post("/api/v1/sample", headers=h, json={
        "dataset_id": ds, "version_number": 2, "sheet": "Expenses",
        "target_total_volume": 1,
        "sampling_steps": [{"method": "random", "sample_size": 1}],
    })
    assert r.status_code == 404, r.text

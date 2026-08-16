"""Per-sheet schema diff and row diff can span a confirmed rename.

After "Expenses" → "Spending" is confirmed, the two versions hold one logical
sheet under two per-version keys. The workbook diff already reports the rename;
the per-sheet and row-level diffs must be able to drill into it too, under
either spelling, instead of 404ing the sheet the workbook diff just named.
"""

from __future__ import annotations

from conftest import auth, make_workbook, upload_file, create_team_user, XLSX_MIME


async def _renamed(client, admin_id, tmp_path):
    editor, team = await create_team_user(client, admin_id, "editor")
    h = auth(editor)
    v1, v2 = tmp_path / "v1.xlsx", tmp_path / "v2.xlsx"
    make_workbook(v1, second_sheet="Expenses")
    make_workbook(v2, second_sheet="Spending")
    ds = (await upload_file(client, editor, v1, name="book.xlsx",
                            content_type=XLSX_MIME, team_id=team))["dataset_id"]
    await upload_file(client, editor, v2, name="book.xlsx", content_type=XLSX_MIME,
                      dataset_id=ds, team_id=team)
    r = await client.post(f"/api/v1/datasets/{ds}/versions/2/confirm-rename",
                          headers=h, json={"from_sheet": "Expenses", "to_sheet": "Spending"})
    assert r.status_code == 200, r.text
    return ds, h


async def test_schema_diff_spans_a_confirmed_rename_under_either_spelling(client, admin_id, tmp_path):
    ds, h = await _renamed(client, admin_id, tmp_path)
    for spelling in ("Expenses", "Spending"):
        r = await client.get(
            f"/api/v1/datasets/{ds}/versions/1/sheets/{spelling}/diff/2", headers=h)
        assert r.status_code == 200, (spelling, r.text)
        # Same content on both sides → no column added/removed; the diff spans
        # the two names of the one logical sheet.
        body = r.json()
        assert body["added_columns"] == [] and body["removed_columns"] == []
        assert body["from_sheet"] == "Expenses" and body["to_sheet"] == "Spending"


async def test_row_diff_spans_a_confirmed_rename_under_either_spelling(client, admin_id, tmp_path):
    ds, h = await _renamed(client, admin_id, tmp_path)
    for spelling in ("Expenses", "Spending"):
        r = await client.post(
            f"/api/v1/datasets/{ds}/versions/1/sheets/{spelling}/row-diff/2",
            headers=h, json={"key": ["item"]})
        assert r.status_code == 200, (spelling, r.text)

    # The workbook diff still settles the rename under `renamed`.
    wd = (await client.get(f"/api/v1/datasets/{ds}/versions/1/diff/2", headers=h)).json()
    assert any(rn["to_sheet"] == "Spending" for rn in wd.get("renamed") or []), wd.get("renamed")

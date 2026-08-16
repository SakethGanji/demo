"""A confirmed rename must stop being a *suggestion* on the next diff.

``confirm-rename`` relinks logical identity (``app/shared/repo.py``'s
``reassign_logical_sheet`` rewrites ``dataset_version_sheets.logical_sheet_id``
and the keyed state that hangs off it) but deliberately never rewrites a
version's physical ``sheet_key`` — v1 keeps ``expenses``, v2 keeps
``spending``. ``workbook_diff`` matched sheets on ``sheet_key`` alone, so:

* the pair stayed in ``added`` + ``removed`` (accurate at the physical level),
  and
* ``rename_candidates`` re-emitted the *same* suggestion forever, because the
  schema fingerprints still match — which is exactly the condition that gated
  the confirm in the first place.

What breaks in production without this test: the rename banner never
dismisses. A user confirms "Expenses became Spending", reloads the compare
screen, and is asked the identical question again; clicking Confirm a second
time answers 404 (the old key is already gone), so the banner is both
permanent and unactionable. ARCHITECTURE.md §"Everything durable keys off
logical_sheet_id" says sheet-keyed state must either key on the logical id or
be handled by ``reassign_logical_sheet``; the diff did neither.
"""

from __future__ import annotations

from openpyxl import Workbook

from conftest import XLSX_MIME, auth, create_team_user, upload_file


def _workbook(path, *, costs_sheet):
    """Revenue + a costs sheet whose CONTENT never changes, so renaming it
    fingerprints as a high-confidence rename candidate."""
    wb = Workbook()
    ws = wb.active
    ws.title = "Revenue"
    ws.append(["amount", "region"])
    ws.append([100, "EU"])
    ws.append([200, "US"])
    costs = wb.create_sheet(costs_sheet)
    costs.append(["item", "cost"])
    costs.append(["rent", 50])
    wb.save(path)


async def _renamed_dataset(client, admin_id, tmp_path):
    """A dataset whose second sheet was renamed Expenses -> Spending in v2."""
    editor, team = await create_team_user(client, admin_id, "editor")
    h = auth(editor)

    v1, v2 = tmp_path / "v1.xlsx", tmp_path / "v2.xlsx"
    _workbook(v1, costs_sheet="Expenses")
    _workbook(v2, costs_sheet="Spending")

    ds = (await upload_file(client, editor, v1, name="book.xlsx",
                            content_type=XLSX_MIME, team_id=team))["dataset_id"]
    await upload_file(client, editor, v2, name="book.xlsx", content_type=XLSX_MIME,
                      dataset_id=ds, team_id=team)
    return ds, h


async def test_workbook_diff_stops_suggesting_a_rename_once_it_has_been_confirmed(
    client, admin_id, tmp_path,
):
    """The banner has to dismiss itself. Re-suggesting a settled rename offers
    a button whose only possible outcome is a 404."""
    ds, h = await _renamed_dataset(client, admin_id, tmp_path)

    before = (await client.get(f"/api/v1/datasets/{ds}/versions/1/diff/2",
                               headers=h)).json()
    assert [(c["from_sheet"], c["to_sheet"]) for c in before["rename_candidates"]] \
        == [("Expenses", "Spending")]
    assert before["renamed"] == [], "nothing is settled before the user confirms"

    r = await client.post(f"/api/v1/datasets/{ds}/versions/2/confirm-rename",
                          headers=h,
                          json={"from_sheet": "Expenses", "to_sheet": "Spending"})
    assert r.status_code == 200, r.text
    logical = r.json()["logical_sheet_id"]

    after = (await client.get(f"/api/v1/datasets/{ds}/versions/1/diff/2",
                              headers=h)).json()
    assert after["rename_candidates"] == []

    # And the second confirm the stale banner would have fired is indeed dead,
    # which is why re-suggesting it was never harmless.
    r = await client.post(f"/api/v1/datasets/{ds}/versions/2/confirm-rename",
                          headers=h,
                          json={"from_sheet": "Expenses", "to_sheet": "Spending"})
    assert r.status_code == 404

    # The pair is reported as SETTLED instead of merely vanishing, so the
    # compare screen can still explain why `added`/`removed` list both names.
    assert after["renamed"] == [{
        "logical_sheet_id": logical,
        "from_sheet": "Expenses", "to_sheet": "Spending",
        "from_sheet_key": "expenses", "to_sheet_key": "spending",
    }]
    assert [s["logical_sheet_id"] for s in after["added"]] == [logical]
    assert [s["logical_sheet_id"] for s in after["removed"]] == [logical]


async def test_an_unconfirmed_rename_is_still_only_a_candidate_never_a_renamed_entry(
    client, admin_id, tmp_path,
):
    """``renamed`` must mean "the user said so", not "the fingerprints match".
    If a fingerprint match alone populated it, the diff would be declaring
    renames it has no authority to declare — the exact thing
    ``RenameCandidate`` exists to avoid."""
    ds, h = await _renamed_dataset(client, admin_id, tmp_path)

    diff = (await client.get(f"/api/v1/datasets/{ds}/versions/1/diff/2",
                             headers=h)).json()

    assert diff["renamed"] == []
    assert len(diff["rename_candidates"]) == 1
    assert diff["rename_candidates"][0]["confidence"] == "high"
    # The two names are still two separate identities until the confirm lands.
    assert diff["added"][0]["logical_sheet_id"] != diff["removed"][0]["logical_sheet_id"]

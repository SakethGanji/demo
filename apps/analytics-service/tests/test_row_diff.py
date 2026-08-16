"""Keyed row-level diff — the "what actually changed" review workflow.

Schema diff and profile drift answer "what columns changed" and "how did the
distribution move". This answers the question a reviewer actually asks before
promoting a version: which rows changed, and to what.
"""

from __future__ import annotations

import json

from conftest import auth, create_team_user, upload_inline

DEFAULT_TEAM = "00000000-0000-0000-0000-000000000001"

V1 = [
    {"id": 1, "name": "Ana", "amount": 10.0},
    {"id": 2, "name": "Bob", "amount": 20.0},
    {"id": 3, "name": "Cy", "amount": 30.0},
    {"id": 4, "name": "Dee", "amount": 40.0},
]
# id 1 unchanged, 2 and 4 repriced, 3 gone, 5 new.
V2 = [
    {"id": 1, "name": "Ana", "amount": 10.0},
    {"id": 2, "name": "Bob", "amount": 25.0},
    {"id": 4, "name": "Dee", "amount": 45.0},
    {"id": 5, "name": "Eve", "amount": 50.0},
]


async def two_versions(client, admin_id, v1=None, v2=None):
    ds = (await upload_inline(client, admin_id, json.dumps(v1 or V1)))["dataset_id"]
    await upload_inline(client, admin_id, json.dumps(v2 or V2), dataset_id=ds)
    return ds


async def declare_key(client, admin_id, ds, columns=("id",)):
    r = await client.put(f"/api/v1/datasets/{ds}/sheet-metadata/data",
                         headers=auth(admin_id),
                         json={"primary_key_columns": list(columns)})
    assert r.status_code in (200, 201), r.text


def url(ds, sheet="data", a=1, b=2):
    return f"/api/v1/datasets/{ds}/versions/{a}/sheets/{sheet}/row-diff/{b}"


# --- the core answer ----------------------------------------------------------

async def test_row_diff_classifies_every_row(client, admin_id):
    ds = await two_versions(client, admin_id)
    h = auth(admin_id)

    r = await client.post(url(ds), headers=h, json={"key": ["id"]})
    assert r.status_code == 200, r.text
    body = r.json()

    assert (body["added"], body["removed"], body["changed"], body["unchanged"]) \
        == (1, 1, 2, 1)
    assert body["key"] == ["id"]
    assert body["sheet"] == "data"
    assert sorted(body["compared_columns"]) == ["amount", "name"]

    # The "what moved" summary points straight at the column that changed.
    assert body["column_changes"] == [{"column": "amount", "changed_rows": 2}]

    assert [row["id"] for row in body["added_sample"]] == [5]
    assert [row["id"] for row in body["removed_sample"]] == [3]
    changed = {c["row_key"]: c for c in body["changed_sample"]}
    assert changed["2"]["column_name"] == "amount"
    assert (changed["2"]["before_value"], changed["2"]["after_value"]) == ("20.0", "25.0")


async def test_the_full_diff_is_an_artifact_not_a_postgres_row(client, admin_id):
    """Control-plane rule: the cell-level result is dataset content."""
    ds = await two_versions(client, admin_id)
    h = auth(admin_id)
    body = (await client.post(url(ds), headers=h, json={"key": ["id"]})).json()

    assert body["diff_file"] and body["diff_artifact_id"]
    r = await client.get(f"/api/v1/samples/{body['diff_file']}/data", headers=h)
    assert r.status_code == 200, r.text
    rows = r.json()["data"] if isinstance(r.json(), dict) else r.json()

    by_type: dict[str, list] = {}
    for row in rows:
        by_type.setdefault(row["change_type"], []).append(row)
    # 2 changed cells, plus every column of the added and the removed row.
    assert len(by_type["changed"]) == 2
    assert len(by_type["added"]) == 3
    assert len(by_type["removed"]) == 3
    assert {r_["column_name"] for r_ in by_type["added"]} == {"id", "name", "amount"}


async def test_identical_versions_produce_no_diff_artifact(client, admin_id):
    ds = await two_versions(client, admin_id, V1, V1)
    r = await client.post(url(ds), headers=auth(admin_id), json={"key": ["id"]})
    assert r.status_code == 200
    body = r.json()
    assert (body["added"], body["removed"], body["changed"]) == (0, 0, 0)
    assert body["unchanged"] == 4
    assert body["diff_file"] is None      # nothing to write


# --- the key ------------------------------------------------------------------

async def test_the_key_defaults_to_the_declared_primary_key(client, admin_id):
    """This is what makes capturing a sheet's PK in the dictionary pay off."""
    ds = await two_versions(client, admin_id)
    await declare_key(client, admin_id, ds)

    r = await client.post(url(ds), headers=auth(admin_id))
    assert r.status_code == 200, r.text
    assert r.json()["key"] == ["id"]
    assert r.json()["changed"] == 2


async def test_without_a_key_the_request_says_how_to_supply_one(client, admin_id):
    ds = await two_versions(client, admin_id)
    r = await client.post(url(ds), headers=auth(admin_id))
    assert r.status_code == 400
    assert r.json()["code"] == "diff-key-required"
    assert "primary key" in r.json()["detail"]


async def test_a_non_unique_key_is_refused_rather_than_answered_wrongly(
        client, admin_id):
    """A fanned-out join would report rows as both added and removed."""
    dupes = [{"id": 1, "name": "a"}, {"id": 1, "name": "b"}, {"id": 2, "name": "c"}]
    ds = await two_versions(client, admin_id, dupes, dupes)

    r = await client.post(url(ds), headers=auth(admin_id), json={"key": ["id"]})
    assert r.status_code == 409, r.text
    assert r.json()["code"] == "ambiguous-diff-key"
    assert r.json()["duplicate_keys"] == 1
    assert r.json()["version_number"] == 1


async def test_an_unknown_key_column_is_rejected(client, admin_id):
    ds = await two_versions(client, admin_id)
    r = await client.post(url(ds), headers=auth(admin_id), json={"key": ["ghost"]})
    assert r.status_code == 400
    assert r.json()["code"] == "unknown-column"
    assert "id" in r.json()["available"]


async def test_a_composite_key_works(client, admin_id):
    a = [{"region": "NY", "sku": "x", "qty": 1}, {"region": "LA", "sku": "x", "qty": 2}]
    b = [{"region": "NY", "sku": "x", "qty": 9}, {"region": "LA", "sku": "x", "qty": 2}]
    ds = await two_versions(client, admin_id, a, b)
    r = await client.post(url(ds), headers=auth(admin_id),
                          json={"key": ["region", "sku"]})
    assert r.status_code == 200, r.text
    assert (r.json()["changed"], r.json()["unchanged"]) == (1, 1)


# --- scoping the comparison ---------------------------------------------------

async def test_comparing_a_subset_of_columns(client, admin_id):
    ds = await two_versions(client, admin_id)
    r = await client.post(url(ds), headers=auth(admin_id),
                          json={"key": ["id"], "columns": ["name"]})
    assert r.status_code == 200, r.text
    # No name changed between the versions, so nothing is "changed".
    assert r.json()["changed"] == 0
    assert r.json()["compared_columns"] == ["name"]


async def test_an_unknown_compare_column_is_rejected(client, admin_id):
    ds = await two_versions(client, admin_id)
    r = await client.post(url(ds), headers=auth(admin_id),
                          json={"key": ["id"], "columns": ["ghost"]})
    assert r.status_code == 400
    assert r.json()["code"] == "unknown-column"


async def test_columns_added_between_versions_are_not_compared(client, admin_id):
    """A new column is a schema change; the schema diff already reports it."""
    a = [{"id": 1, "name": "Ana"}]
    b = [{"id": 1, "name": "Ana", "extra": "new"}]
    ds = await two_versions(client, admin_id, a, b)
    r = await client.post(url(ds), headers=auth(admin_id), json={"key": ["id"]})
    assert r.status_code == 200, r.text
    assert r.json()["compared_columns"] == ["name"]
    assert r.json()["changed"] == 0


# --- cross-cutting contracts --------------------------------------------------

async def test_multi_sheet_versions_require_a_named_sheet(client, admin_id, tmp_path):
    from conftest import XLSX_MIME, make_workbook, upload_file

    v1, v2 = tmp_path / "v1.xlsx", tmp_path / "v2.xlsx"
    make_workbook(v1)
    make_workbook(v2)
    ds = (await upload_file(client, admin_id, v1, name="b.xlsx",
                            content_type=XLSX_MIME))["dataset_id"]
    await upload_file(client, admin_id, v2, name="b.xlsx",
                      content_type=XLSX_MIME, dataset_id=ds)

    r = await client.post(url(ds, sheet="Revenue"), headers=auth(admin_id),
                          json={"key": ["Amount"]})
    assert r.status_code in (200, 409), r.text     # resolves the named sheet

    missing = await client.post(url(ds, sheet="Nope"), headers=auth(admin_id),
                                json={"key": ["x"]})
    assert missing.status_code == 404


async def test_row_diff_is_hidden_across_teams(client, admin_id):
    ds = await two_versions(client, admin_id)
    outsider, _ = await create_team_user(client, admin_id, "admin")
    r = await client.post(url(ds), headers=auth(outsider), json={"key": ["id"]})
    assert r.status_code == 404


async def test_viewers_can_diff(client, admin_id):
    """Reviewing a version is a read operation."""
    ds = await two_versions(client, admin_id)
    viewer, _ = await create_team_user(client, admin_id, "viewer",
                                       team_id=DEFAULT_TEAM)
    r = await client.post(url(ds), headers=auth(viewer), json={"key": ["id"]})
    assert r.status_code == 200, r.text
    assert r.json()["changed"] == 2

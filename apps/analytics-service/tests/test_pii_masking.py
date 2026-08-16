"""Column masking driven by the data dictionary's `sensitivity` field.

The point under test: declaring a column sensitive actually restricts it. That
means masking wherever raw rows are returned AND gating the raw-file download —
masking alone would be theatre, since anyone could fetch the original file.
"""

from __future__ import annotations

import json

from conftest import auth, create_team_user, upload_inline

DEFAULT_TEAM = "00000000-0000-0000-0000-000000000001"

ROWS = [
    {"id": 1, "email": "ana@example.com", "amount": 100.0},
    {"id": 2, "email": "bob@example.com", "amount": 250.0},
]


async def dataset_with_pii(client, admin_id, sensitivity="confidential"):
    ds = (await upload_inline(client, admin_id, json.dumps(ROWS)))["dataset_id"]
    r = await client.put(
        f"/api/v1/datasets/{ds}/sheet-metadata/data/columns/email",
        headers=auth(admin_id),
        json={"business_name": "Contact email", "semantic_type": "email",
              "sensitivity": sensitivity})
    assert r.status_code == 200, r.text
    return ds


async def analyst(client, admin_id, role="editor"):
    """A user in the dataset's team WITHOUT dataset:read_sensitive."""
    uid, _ = await create_team_user(client, admin_id, role, team_id=DEFAULT_TEAM)
    return uid


# --- masking on the row-reading surfaces --------------------------------------

async def test_preview_masks_a_sensitive_column(client, admin_id):
    ds = await dataset_with_pii(client, admin_id)
    uid = await analyst(client, admin_id)

    r = await client.get(f"/api/v1/datasets/{ds}/versions/1/preview",
                         headers=auth(uid))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["masked_columns"] == ["email"]
    assert {row["email"] for row in body["items"]} == {"a***@***.com", "b***@***.com"}
    # Non-sensitive columns are untouched.
    assert {row["amount"] for row in body["items"]} == {100.0, 250.0}


async def test_structured_query_masks_too(client, admin_id):
    ds = await dataset_with_pii(client, admin_id)
    uid = await analyst(client, admin_id)

    r = await client.post(f"/api/v1/datasets/{ds}/versions/1/query",
                          headers=auth(uid), json={"columns": ["email", "amount"]})
    assert r.status_code == 200, r.text
    assert r.json()["masked_columns"] == ["email"]
    assert all("@***." in row["email"] for row in r.json()["items"])


async def test_a_saved_view_masks_when_run(client, admin_id):
    ds = await dataset_with_pii(client, admin_id)
    h = auth(admin_id)
    view = (await client.post(f"/api/v1/datasets/{ds}/views", headers=h, json={
        "name": "all", "sheet": "data", "query": {}})).json()
    uid = await analyst(client, admin_id)

    r = await client.post(f"/api/v1/datasets/{ds}/views/{view['id']}/run",
                          headers=auth(uid))
    assert r.status_code == 200, r.text
    assert r.json()["result"]["masked_columns"] == ["email"]


async def test_row_diff_samples_are_masked(client, admin_id):
    ds = await dataset_with_pii(client, admin_id)
    await upload_inline(client, admin_id,
                        json.dumps(ROWS + [{"id": 3, "email": "cy@example.com",
                                            "amount": 5.0}]), dataset_id=ds)
    uid = await analyst(client, admin_id)

    r = await client.post(
        f"/api/v1/datasets/{ds}/versions/1/sheets/data/row-diff/2",
        headers=auth(uid), json={"key": ["id"]})
    assert r.status_code == 200, r.text
    assert r.json()["masked_columns"] == ["email"]
    assert r.json()["added_sample"][0]["email"] == "c***@***.com"


# --- the elevated path --------------------------------------------------------

async def test_an_admin_sees_the_real_values(client, admin_id):
    ds = await dataset_with_pii(client, admin_id)
    elevated = await analyst(client, admin_id, role="admin")

    r = await client.get(f"/api/v1/datasets/{ds}/versions/1/preview",
                         headers=auth(elevated))
    assert r.status_code == 200, r.text
    assert r.json()["masked_columns"] == []
    assert "ana@example.com" in {row["email"] for row in r.json()["items"]}


async def test_editors_are_deliberately_not_exempt(client, admin_id):
    """People who work with a dataset daily shouldn't routinely see its PII."""
    ds = await dataset_with_pii(client, admin_id)
    editor = await analyst(client, admin_id, role="editor")
    r = await client.get(f"/api/v1/datasets/{ds}/versions/1/preview",
                         headers=auth(editor))
    assert r.json()["masked_columns"] == ["email"]


# --- masking is a control, not decoration -------------------------------------

async def test_the_raw_download_is_gated_once_pii_is_declared(client, admin_id):
    """Otherwise masking is trivially bypassed by downloading the file."""
    ds = await dataset_with_pii(client, admin_id)
    uid = await analyst(client, admin_id)

    r = await client.get(f"/api/v1/datasets/{ds}/download", headers=auth(uid))
    assert r.status_code == 403
    assert r.json()["code"] == "sensitive-data-restricted"

    version = await client.get(f"/api/v1/datasets/{ds}/versions/1/download",
                               headers=auth(uid))
    assert version.status_code == 403


async def test_an_admin_can_still_download(client, admin_id):
    ds = await dataset_with_pii(client, admin_id)
    elevated = await analyst(client, admin_id, role="admin")
    r = await client.get(f"/api/v1/datasets/{ds}/versions/1/download",
                         headers=auth(elevated))
    assert r.status_code == 200


# --- datasets without declarations are unaffected -----------------------------

async def test_a_dataset_with_no_sensitivity_behaves_exactly_as_before(
        client, admin_id):
    ds = (await upload_inline(client, admin_id, json.dumps(ROWS)))["dataset_id"]
    uid = await analyst(client, admin_id)

    preview = await client.get(f"/api/v1/datasets/{ds}/versions/1/preview",
                               headers=auth(uid))
    assert preview.json()["masked_columns"] == []
    assert "ana@example.com" in {r["email"] for r in preview.json()["items"]}

    download = await client.get(f"/api/v1/datasets/{ds}/versions/1/download",
                                headers=auth(uid))
    assert download.status_code == 200


async def test_a_non_sensitive_level_does_not_mask(client, admin_id):
    ds = await dataset_with_pii(client, admin_id, sensitivity="internal")
    uid = await analyst(client, admin_id)
    r = await client.get(f"/api/v1/datasets/{ds}/versions/1/preview",
                         headers=auth(uid))
    assert r.json()["masked_columns"] == []

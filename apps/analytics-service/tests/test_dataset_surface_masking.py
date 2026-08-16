"""Sensitive columns on the data_accelerator surfaces that still returned raw.

``app/shared/masking.py`` calls masking "a real control, not a display
convenience" and gates ``/download`` so it cannot be sidestepped by fetching
the file. Two families of data_accelerator route ignored it entirely:

* ``GET /datasets/{id}`` and ``GET /datasets/{id}/sheets/{sheet}`` return a
  literal five-row ``preview`` straight out of the parquet. An editor who is
  deliberately NOT exempt (``test_pii_masking.py::
  test_editors_are_deliberately_not_exempt``) got the real values from the
  very first screen of the dataset detail page.

* ``/sample``, ``/sample/coordinated``, ``/profile``, ``/pivot`` and
  ``/aggregate`` accept caller-supplied filters, group-bys, stratification
  keys and sort orders over a dataset, return rows/column stats inline, AND
  persist their unmasked output as a parquet artifact the whole team can
  download. That is the same shape as the SQL console, which already requires
  ``dataset:read_sensitive`` for exactly this reason — so these get the same
  gate rather than per-column masking that a filter oracle would defeat.

Every test pairs the non-exempt caller with the admin case, so none of this
can be mistaken for "masking is always on".
"""

from __future__ import annotations

import json

from conftest import auth, create_team_user, upload_inline

DEFAULT_TEAM = "00000000-0000-0000-0000-000000000001"

ROWS = [
    {"id": 1, "email": "ana@example.com", "amount": 100.0},
    {"id": 2, "email": "bob@example.com", "amount": 250.0},
]

REAL_EMAILS = {"ana@example.com", "bob@example.com"}


async def _dataset_with_pii(client, admin_id):
    ds = (await upload_inline(client, admin_id, json.dumps(ROWS)))["dataset_id"]
    r = await client.put(
        f"/api/v1/datasets/{ds}/sheet-metadata/data/columns/email",
        headers=auth(admin_id),
        json={"business_name": "Contact email", "semantic_type": "email",
              "sensitivity": "confidential"})
    assert r.status_code == 200, r.text
    return ds


async def _analyst(client, admin_id):
    """An editor in the dataset's team, deliberately without read_sensitive."""
    uid, _ = await create_team_user(client, admin_id, "editor", team_id=DEFAULT_TEAM)
    return uid


def _leaks(blob) -> bool:
    text = json.dumps(blob, default=str)
    return any(e in text for e in REAL_EMAILS)


# --- the detail previews ------------------------------------------------------

async def test_the_dataset_detail_preview_masks_a_sensitive_column(client, admin_id):
    ds = await _dataset_with_pii(client, admin_id)
    uid = await _analyst(client, admin_id)

    r = await client.get(f"/api/v1/datasets/{ds}", headers=auth(uid))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["masked_columns"] == ["email"]
    assert {row["email"] for row in body["preview"]} == {"a***@***.com", "b***@***.com"}
    # Non-sensitive columns are untouched.
    assert {row["amount"] for row in body["preview"]} == {100.0, 250.0}
    assert not _leaks(body)


async def test_the_sheet_detail_preview_masks_a_sensitive_column(client, admin_id):
    ds = await _dataset_with_pii(client, admin_id)
    uid = await _analyst(client, admin_id)

    r = await client.get(f"/api/v1/datasets/{ds}/sheets/data", headers=auth(uid))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["masked_columns"] == ["email"]
    assert {row["email"] for row in body["preview"]} == {"a***@***.com", "b***@***.com"}
    assert not _leaks(body)


async def test_an_admin_still_sees_raw_preview_values(client, admin_id):
    ds = await _dataset_with_pii(client, admin_id)

    body = (await client.get(f"/api/v1/datasets/{ds}", headers=auth(admin_id))).json()
    assert body["masked_columns"] == []
    assert {row["email"] for row in body["preview"]} == REAL_EMAILS

    body = (await client.get(f"/api/v1/datasets/{ds}/sheets/data",
                             headers=auth(admin_id))).json()
    assert body["masked_columns"] == []
    assert {row["email"] for row in body["preview"]} == REAL_EMAILS


async def test_a_dataset_with_no_declared_sensitivity_previews_exactly_as_before(
        client, admin_id):
    """The control only engages where the dictionary declares something."""
    ds = (await upload_inline(client, admin_id, json.dumps(ROWS)))["dataset_id"]
    uid = await _analyst(client, admin_id)

    body = (await client.get(f"/api/v1/datasets/{ds}", headers=auth(uid))).json()
    assert body["masked_columns"] == []
    assert {row["email"] for row in body["preview"]} == REAL_EMAILS


# --- the analytics compute endpoints ------------------------------------------

ANALYTICS_CALLS = {
    "/api/v1/sample": lambda ds: {
        "dataset_id": ds, "target_total_volume": 2,
        "sampling_steps": [{"method": "random", "sample_size": 2}]},
    "/api/v1/profile": lambda ds: {"dataset_id": ds},
    "/api/v1/aggregate": lambda ds: {
        "dataset_id": ds, "group_by": ["email"],
        "aggregations": [{"column": "amount", "function": "sum"}]},
    "/api/v1/pivot": lambda ds: {
        "dataset_id": ds, "rows": ["email"], "columns": "id",
        "values": [{"column": "amount", "function": "sum"}]},
}


async def test_the_analytics_endpoints_refuse_a_caller_without_read_sensitive(
        client, admin_id):
    """Each one returns rows or cell statistics AND persists a raw parquet."""
    ds = await _dataset_with_pii(client, admin_id)
    uid = await _analyst(client, admin_id)

    for url, body in ANALYTICS_CALLS.items():
        r = await client.post(url, headers=auth(uid), json=body(ds))
        assert r.status_code == 403, f"{url} -> {r.status_code}: {r.text}"
        assert r.json()["code"] == "sensitive-data-restricted", url
        assert not _leaks(r.json()), url


async def test_coordinated_sampling_is_gated_too(client, admin_id):
    """The gate is in `_authorize_source`, so it fires before any sheet work."""
    ds = await _dataset_with_pii(client, admin_id)
    uid = await _analyst(client, admin_id)

    r = await client.post("/api/v1/sample/coordinated", headers=auth(uid), json={
        "dataset_id": ds, "driver_sheet": "data", "target_total_volume": 2,
        "sampling_steps": [{"method": "random", "sample_size": 2}],
        "related": [{"sheet": "data", "left_on": "id", "right_on": "id"}]})
    assert r.status_code == 403, r.text
    assert r.json()["code"] == "sensitive-data-restricted"


async def test_an_admin_can_still_run_the_analytics_endpoints(client, admin_id):
    ds = await _dataset_with_pii(client, admin_id)

    for url, body in ANALYTICS_CALLS.items():
        r = await client.post(url, headers=auth(admin_id), json=body(ds))
        assert r.status_code == 200, f"{url} -> {r.status_code}: {r.text}"


async def test_analytics_on_a_dataset_without_sensitivity_is_unaffected(
        client, admin_id):
    ds = (await upload_inline(client, admin_id, json.dumps(ROWS)))["dataset_id"]
    uid = await _analyst(client, admin_id)

    for url, body in ANALYTICS_CALLS.items():
        r = await client.post(url, headers=auth(uid), json=body(ds))
        assert r.status_code == 200, f"{url} -> {r.status_code}: {r.text}"

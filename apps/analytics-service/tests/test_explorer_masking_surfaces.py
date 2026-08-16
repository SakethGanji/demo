"""Masking on the explorer surfaces that were returning raw values.

``app/shared/masking.py`` says the control is "a real control, not a display
convenience" and gates ``/download`` so masking cannot be sidestepped by
fetching the file. But four explorer surfaces returned the very values that
gate exists to withhold:

* ``/columns/{column}`` — the single-column drawer, whose ``top_values``,
  ``rare_values`` and ``examples`` are literal cells;
* ``/duplicates`` — whose ``examples`` are whole rows and whose ``key`` is the
  grouped-on values;
* ``/missing`` — whose ``rows_most_missing`` are whole rows;
* ``/sql`` — arbitrary SELECT, plus a persisted parquet artifact anyone on the
  team can then download.

And the structured query path masked *after* execution, so ``filters``,
``search`` and ``sort`` still ran on raw values while ``total`` — which
``mask_rows`` never touches — reported the answer. That turns the preview into
a search oracle: ``email eq '<guess>'`` and read the count.

Every test here uses an *editor*, who is deliberately not exempt
(``test_pii_masking.py::test_editors_are_deliberately_not_exempt``), and pairs
it with the admin case so the fixes cannot be mistaken for "masking is always
on".
"""

from __future__ import annotations

import json

from conftest import auth, create_team_user, upload_inline

DEFAULT_TEAM = "00000000-0000-0000-0000-000000000001"

ROWS = [
    {"id": 1, "email": "ana@example.com", "tier": "gold"},
    {"id": 2, "email": "ana@example.com", "tier": "gold"},   # duplicate row
    {"id": 3, "email": "bob@example.com", "tier": None},
    {"id": 4, "email": None, "tier": None},                  # most-missing row
]


async def _dataset_with_pii(client, admin_id):
    ds = (await upload_inline(client, admin_id, json.dumps(ROWS)))["dataset_id"]
    r = await client.put(
        f"/api/v1/datasets/{ds}/sheet-metadata/data/columns/email",
        headers=auth(admin_id),
        json={"business_name": "Contact email", "semantic_type": "email",
              "sensitivity": "confidential"})
    assert r.status_code == 200, r.text
    return ds


async def _editor(client, admin_id):
    uid, _ = await create_team_user(client, admin_id, "editor", team_id=DEFAULT_TEAM)
    return uid


def _leaks(blob) -> bool:
    """Whether any real email address survived into the payload."""
    return "@example.com" in json.dumps(blob)


# --- the column explorer ------------------------------------------------------

async def test_the_column_explorer_masks_a_sensitive_columns_values(
        client, admin_id):
    ds = await _dataset_with_pii(client, admin_id)
    uid = await _editor(client, admin_id)
    url = f"/api/v1/datasets/{ds}/versions/1/columns/email"

    r = await client.get(url, headers=auth(uid))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["masked_columns"] == ["email"]
    assert not _leaks(body), body
    assert {tv["value"] for tv in body["top_values"]} == {"a***@***.com",
                                                          "b***@***.com"}
    assert {tv["value"] for tv in body["rare_values"]} <= {"a***@***.com",
                                                           "b***@***.com"}
    assert set(body["examples"]) <= {"a***@***.com", "b***@***.com"}
    # Counts are not values, so they stay: the drawer is still usable.
    assert body["null_count"] == 1 and body["unique_count"] == 2

    # The admin sees the real thing, so this is masking and not deletion.
    r = await client.get(url, headers=auth(admin_id))
    assert r.status_code == 200 and r.json()["masked_columns"] == []
    assert "ana@example.com" in {tv["value"] for tv in r.json()["top_values"]}


async def test_the_column_explorer_withholds_value_bearing_statistics(
        client, admin_id):
    """min/max/quantiles/histogram of a masked column are real values too.

    Masking ``top_values`` while returning ``min``/``max`` would hand over the
    two extreme cells verbatim — the same leak by a different field name — so a
    masked column's distributional statistics are withheld, not masked.
    """
    ds = (await upload_inline(client, admin_id, json.dumps(
        [{"ssn": 100000000 + i} for i in range(5)])))["dataset_id"]
    r = await client.put(
        f"/api/v1/datasets/{ds}/sheet-metadata/data/columns/ssn",
        headers=auth(admin_id),
        json={"semantic_type": "identifier", "sensitivity": "pii"})
    assert r.status_code == 200, r.text
    uid = await _editor(client, admin_id)

    body = (await client.get(f"/api/v1/datasets/{ds}/versions/1/columns/ssn",
                             headers=auth(uid))).json()
    assert body["masked_columns"] == ["ssn"]
    assert body["min"] is None and body["max"] is None
    assert body["q25"] is None and body["q75"] is None and body["mean"] is None
    assert body["histogram"] is None
    assert "100000000" not in json.dumps(body)

    admin_body = (await client.get(f"/api/v1/datasets/{ds}/versions/1/columns/ssn",
                                   headers=auth(admin_id))).json()
    assert admin_body["min"] == 100000000.0 and admin_body["max"] == 100000004.0


# --- duplicates + missing -----------------------------------------------------

async def test_duplicate_groups_and_missing_rows_mask_a_sensitive_column(
        client, admin_id):
    ds = await _dataset_with_pii(client, admin_id)
    uid = await _editor(client, admin_id)

    r = await client.get(f"/api/v1/datasets/{ds}/versions/1/duplicates",
                         params={"columns": "email"}, headers=auth(uid))
    assert r.status_code == 200, r.text
    dupes = r.json()
    assert dupes["masked_columns"] == ["email"]
    assert not _leaks(dupes), dupes
    group = next(g for g in dupes["groups"] if g["count"] == 2)
    assert {row["email"] for row in group["examples"]} == {"a***@***.com"}

    r = await client.get(f"/api/v1/datasets/{ds}/versions/1/missing",
                         headers=auth(uid))
    assert r.status_code == 200, r.text
    missing = r.json()
    assert missing["masked_columns"] == ["email"]
    assert not _leaks(missing), missing
    # Null-ness survives masking — that is the whole point of this report.
    assert {c["column"]: c["null_count"] for c in missing["columns"]} == {
        "email": 1, "tier": 2, "id": 0}
    assert any(row["row"]["email"] is None for row in missing["rows_most_missing"])

    # Admin still sees raw values on both.
    r = await client.get(f"/api/v1/datasets/{ds}/versions/1/duplicates",
                         params={"columns": "email"}, headers=auth(admin_id))
    assert r.json()["masked_columns"] == [] and _leaks(r.json())


async def test_a_masked_group_key_stays_distinguishable_between_groups(
        client, admin_id):
    """Collapsing every masked key to ``***`` would merge distinct groups.

    The duplicates view exists to show *which* rows repeat. If two different
    email groups both rendered as ``{"email": "***"}`` the caller could no
    longer tell one group from the other, so masked key values become a stable
    pseudonym instead.
    """
    rows = ([{"email": "ana@example.com"}] * 2) + ([{"email": "bob@example.com"}] * 3)
    ds = (await upload_inline(client, admin_id, json.dumps(rows)))["dataset_id"]
    r = await client.put(
        f"/api/v1/datasets/{ds}/sheet-metadata/data/columns/email",
        headers=auth(admin_id),
        json={"semantic_type": "email", "sensitivity": "confidential"})
    assert r.status_code == 200, r.text
    uid = await _editor(client, admin_id)

    body = (await client.get(f"/api/v1/datasets/{ds}/versions/1/duplicates",
                             headers=auth(uid))).json()
    assert body["group_count"] == 2 and len(body["groups"]) == 2
    keys = [g["key"]["email"] for g in body["groups"]]
    assert len(set(keys)) == 2, keys        # still two distinct groups
    assert not _leaks(body), body           # but no real address in sight
    # The pseudonym is stable, so the same value groups the same way each time.
    again = (await client.get(f"/api/v1/datasets/{ds}/versions/1/duplicates",
                              headers=auth(uid))).json()
    assert [g["key"]["email"] for g in again["groups"]] == keys


# --- the raw-SQL console ------------------------------------------------------

async def test_the_sql_console_is_gated_once_pii_is_declared(client, admin_id):
    ds = await _dataset_with_pii(client, admin_id)
    uid = await _editor(client, admin_id)
    url = f"/api/v1/datasets/{ds}/versions/1/sql"

    r = await client.post(url, headers=auth(uid),
                          json={"sql": "SELECT email FROM data"})
    assert r.status_code == 403, r.text
    assert r.json()["code"] == "sensitive-data-restricted"

    # Not even indirectly: no query_output parquet was persisted by the attempt.
    r = await client.get("/api/v1/samples", headers=auth(uid))
    assert r.status_code == 200, r.text
    assert [a for a in r.json()["items"] if a["file_type"] == "query_output"] == []

    # An admin (dataset:read_sensitive) is unaffected.
    r = await client.post(url, headers=auth(admin_id),
                          json={"sql": "SELECT email FROM data"})
    assert r.status_code == 200, r.text
    assert "ana@example.com" in json.dumps(r.json()["items"])


async def test_the_sql_console_stays_open_when_nothing_is_declared_sensitive(
        client, admin_id):
    """The gate must not tax datasets the control was never about."""
    ds = (await upload_inline(client, admin_id, json.dumps(ROWS)))["dataset_id"]
    uid = await _editor(client, admin_id)
    r = await client.post(f"/api/v1/datasets/{ds}/versions/1/sql",
                          headers=auth(uid),
                          json={"sql": "SELECT COUNT(*) AS n FROM data"})
    assert r.status_code == 200, r.text
    assert r.json()["items"] == [{"n": 4}]


# --- the filter/total oracle --------------------------------------------------

async def test_a_masked_column_cannot_be_filtered_on(client, admin_id):
    ds = await _dataset_with_pii(client, admin_id)
    uid = await _editor(client, admin_id)

    r = await client.post(f"/api/v1/datasets/{ds}/versions/1/query",
                          headers=auth(uid),
                          json={"filters": {"logic": "and", "conditions": [
                              {"column": "email", "op": "eq",
                               "value": "ana@example.com"}]}})
    assert r.status_code == 400, r.text
    assert r.json()["code"] == "sensitive-column-not-filterable"
    assert r.json()["columns"] == ["email"]

    # A prefix probe is the same oracle one character at a time.
    r = await client.post(f"/api/v1/datasets/{ds}/versions/1/query",
                          headers=auth(uid),
                          json={"filters": {"logic": "and", "conditions": [
                              {"column": "email", "op": "starts_with",
                               "value": "a"}]}})
    assert r.status_code == 400
    assert r.json()["code"] == "sensitive-column-not-filterable"

    # The admin may filter — this is a masking rule, not a schema rule.
    r = await client.post(f"/api/v1/datasets/{ds}/versions/1/query",
                          headers=auth(admin_id),
                          json={"filters": {"logic": "and", "conditions": [
                              {"column": "email", "op": "eq",
                               "value": "ana@example.com"}]}})
    assert r.status_code == 200 and r.json()["total"] == 2


async def test_a_masked_column_cannot_be_sorted_or_searched_on(client, admin_id):
    ds = await _dataset_with_pii(client, admin_id)
    uid = await _editor(client, admin_id)
    url = f"/api/v1/datasets/{ds}/versions/1/query"

    r = await client.post(url, headers=auth(uid),
                          json={"sort": [{"column": "email", "direction": "asc"}]})
    assert r.status_code == 400 and r.json()["code"] == "sensitive-column-not-filterable"

    # `search` is icontains across every text column, masked ones included.
    r = await client.post(url, headers=auth(uid), json={"search": "ana@"})
    assert r.status_code == 400 and r.json()["code"] == "sensitive-column-not-filterable"
    assert r.json()["columns"] == ["email"]

    # Projecting it is still fine — the values come back masked.
    r = await client.post(url, headers=auth(uid), json={"columns": ["email", "tier"]})
    assert r.status_code == 200, r.text
    assert r.json()["masked_columns"] == ["email"]
    assert not _leaks(r.json())

    # Filtering and sorting on a NON-sensitive column is untouched.
    r = await client.post(url, headers=auth(uid),
                          json={"sort": [{"column": "id", "direction": "desc"}],
                                "filters": {"logic": "and", "conditions": [
                                    {"column": "id", "op": "gt", "value": 2}]}})
    assert r.status_code == 200 and r.json()["total"] == 2


async def test_a_saved_view_over_a_masked_column_is_refused_at_run_time(
        client, admin_id):
    """A view created before the column was declared sensitive is a stored oracle.

    The filter is persisted, so nothing re-checks it unless ``run`` does; the
    unprivileged caller would otherwise just press "run" to get the count.
    """
    ds = (await upload_inline(client, admin_id, json.dumps(ROWS)))["dataset_id"]
    h = auth(admin_id)
    view = (await client.post(
        f"/api/v1/datasets/{ds}/views", headers=h,
        json={"name": "ana", "sheet": "data",
              "query": {"filters": {"logic": "and", "conditions": [
                  {"column": "email", "op": "eq", "value": "ana@example.com"}]}}}
    )).json()

    r = await client.put(
        f"/api/v1/datasets/{ds}/sheet-metadata/data/columns/email", headers=h,
        json={"semantic_type": "email", "sensitivity": "confidential"})
    assert r.status_code == 200, r.text

    uid = await _editor(client, admin_id)
    r = await client.post(f"/api/v1/datasets/{ds}/views/{view['id']}/run",
                          headers=auth(uid))
    assert r.status_code == 400, r.text
    assert r.json()["code"] == "sensitive-column-not-filterable"

    r = await client.post(f"/api/v1/datasets/{ds}/views/{view['id']}/run", headers=h)
    assert r.status_code == 200 and r.json()["result"]["total"] == 2

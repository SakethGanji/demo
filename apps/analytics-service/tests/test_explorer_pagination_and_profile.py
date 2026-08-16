"""Explorer paging completeness and profile-run robustness.

- Cursor paging must return every matching row exactly once even when the sort
  key has heavy ties (or no sort is given). Paging is LIMIT/OFFSET over separate
  per-page queries, so the ORDER BY must be a total order; otherwise DuckDB can
  return tied rows in a different order per page, silently skipping some and
  duplicating others while `total` still reads correct.
- A profile run must not 500 when a numeric-pair correlation is undefined (a
  constant column → CORR() is NULL). That is a legitimate "no correlation".
"""

from __future__ import annotations

import json

from conftest import auth, upload_inline


async def test_cursor_paging_is_complete_when_the_sort_key_has_ties(client, admin_id):
    h = auth(admin_id)
    # `grp` = i % 3 → heavy ties, so ORDER BY grp alone is not a total order.
    rows = [{"id": i, "grp": i % 3, "name": f"n{i}"} for i in range(30)]
    ds = (await upload_inline(client, admin_id, json.dumps(rows)))["dataset_id"]
    url = f"/api/v1/datasets/{ds}/versions/1/query"
    spec = {"sort": [{"column": "grp", "direction": "asc"}], "limit": 4}

    # Page through to exhaustion, twice, and require identical complete coverage.
    for _ in range(2):
        seen, cursor, pages = [], None, 0
        while True:
            body = (await client.post(url, headers=h, json={**spec, "cursor": cursor})).json()
            pages += 1
            seen.extend(i["id"] for i in body["items"])
            assert body["total"] == 30
            cursor = body["next_cursor"]
            if cursor is None or pages > 50:
                break
        assert sorted(seen) == list(range(30)), sorted(seen)


async def test_cursor_paging_is_complete_with_no_sort(client, admin_id):
    h = auth(admin_id)
    rows = [{"id": i, "grp": i % 2} for i in range(25)]
    ds = (await upload_inline(client, admin_id, json.dumps(rows)))["dataset_id"]
    url = f"/api/v1/datasets/{ds}/versions/1/query"

    seen, cursor, pages = [], None, 0
    while True:
        body = (await client.post(url, headers=h, json={"limit": 7, "cursor": cursor})).json()
        pages += 1
        seen.extend(i["id"] for i in body["items"])
        cursor = body["next_cursor"]
        if cursor is None or pages > 50:
            break
    assert sorted(seen) == list(range(25)), sorted(seen)


async def test_profile_run_tolerates_an_undefined_correlation(client, admin_id):
    h = auth(admin_id)
    # `flag` is constant → CORR(amt, flag) is NULL. Must not 500.
    rows = [{"amt": i, "flag": 1} for i in range(1, 6)]
    ds = (await upload_inline(client, admin_id, json.dumps(rows)))["dataset_id"]
    r = await client.post(f"/api/v1/datasets/{ds}/versions/1/profile-runs", headers=h)
    assert r.status_code == 200, (r.status_code, r.json())

    # The run is recorded as completed (not left as a failed run).
    lst = (await client.get(f"/api/v1/datasets/{ds}/versions/1/profile-runs", headers=h)).json()
    assert lst["items"] and lst["items"][0]["status"] == "completed", lst

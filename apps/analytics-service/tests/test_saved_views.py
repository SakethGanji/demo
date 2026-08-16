"""Wave 1 §10 — saved views: CRUD, selector pinning, logical-sheet resolution.

The §1 payoff under test: a view keyed on logical_sheet_id keeps working after
a confirmed sheet rename, with no stored-query rewriting.
"""

from __future__ import annotations

import json

from conftest import (
    XLSX_MIME,
    auth,
    create_team_user,
    make_workbook,
    upload_file,
    upload_inline,
)

ROWS_V1 = [
    {"id": 1, "name": "alpha", "score": 10.5},
    {"id": 2, "name": "beta", "score": 20.0},
    {"id": 3, "name": "gamma", "score": 30.25},
    {"id": 4, "name": "delta", "score": 40.0},
    {"id": 5, "name": "epsilon", "score": 50.0},
]
ROWS_V2 = ROWS_V1 + [{"id": 6, "name": "zeta", "score": 60.0}]

VIEW_BODY = {
    "name": "high-scores",
    "description": "score > 15, best first",
    "sheet": "data",
    "query": {
        "filters": {"conditions": [{"column": "score", "op": "gt", "value": 15}]},
        "sort": [{"column": "score", "direction": "desc"}],
        "limit": 2,
    },
}


async def test_view_crud_lifecycle(client, admin_id):
    ds = (await upload_inline(client, admin_id, json.dumps(ROWS_V1)))["dataset_id"]
    h = auth(admin_id)
    base = f"/api/v1/datasets/{ds}/views"

    r = await client.post(base, headers=h, json=VIEW_BODY)
    assert r.status_code == 201, r.text
    view = r.json()
    assert view["sheet_key"] == "data" and view["logical_sheet_id"]
    assert view["version_selector"] == {"mode": "current"}
    assert "cursor" not in view["query"]  # per-run state is never stored
    vid = view["id"]

    r = await client.post(base, headers=h, json=VIEW_BODY)
    assert r.status_code == 409

    r = await client.get(base, headers=h)
    assert r.status_code == 200
    assert r.json()["total"] == 1 and r.json()["items"][0]["id"] == vid

    r = await client.patch(f"{base}/{vid}", headers=h,
                           json={"description": "updated",
                                 "query": {**VIEW_BODY["query"], "limit": 3}})
    assert r.status_code == 200, r.text
    assert r.json()["description"] == "updated"
    assert r.json()["query"]["limit"] == 3

    r = await client.delete(f"{base}/{vid}", headers=h)
    assert r.status_code == 204
    assert (await client.get(f"{base}/{vid}", headers=h)).status_code == 404


async def test_view_create_validates_query_and_selector(client, admin_id):
    ds = (await upload_inline(client, admin_id, json.dumps(ROWS_V1)))["dataset_id"]
    h = auth(admin_id)
    base = f"/api/v1/datasets/{ds}/views"

    bad = {**VIEW_BODY, "query": {"columns": ["nope"]}}
    r = await client.post(base, headers=h, json=bad)
    assert r.status_code == 400 and r.json()["code"] == "unknown-column"

    r = await client.post(base, headers=h,
                          json={**VIEW_BODY, "version_selector": {"mode": "tag"}})
    assert r.status_code == 422  # tag mode requires a tag

    r = await client.post(
        base, headers=h,
        json={**VIEW_BODY,
              "version_selector": {"mode": "tag", "tag": "nope"}})
    assert r.status_code == 404  # tag doesn't exist


async def test_view_run_follows_current_and_respects_pins(client, admin_id):
    ds = (await upload_inline(client, admin_id, json.dumps(ROWS_V1)))["dataset_id"]
    h = auth(admin_id)
    base = f"/api/v1/datasets/{ds}/views"

    current = (await client.post(base, headers=h, json=VIEW_BODY)).json()
    pinned = (await client.post(
        base, headers=h,
        json={**VIEW_BODY, "name": "high-scores-v1",
              "version_selector": {"mode": "version", "version_number": 1}})).json()

    r = await client.post(f"{base}/{current['id']}/run", headers=h)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["version_number"] == 1 and body["sheet_name"] == "data"
    assert [i["name"] for i in body["result"]["items"]] == ["epsilon", "delta"]
    assert body["result"]["total"] == 4

    # New current version: the current-mode view follows, the pin holds.
    await upload_inline(client, admin_id, json.dumps(ROWS_V2), dataset_id=ds)
    r = await client.post(f"{base}/{current['id']}/run", headers=h)
    assert r.json()["version_number"] == 2
    assert [i["name"] for i in r.json()["result"]["items"]] == ["zeta", "epsilon"]

    r = await client.post(f"{base}/{pinned['id']}/run", headers=h)
    assert r.json()["version_number"] == 1
    assert r.json()["result"]["total"] == 4

    # Cursor paging via run overrides.
    first = (await client.post(f"{base}/{pinned['id']}/run", headers=h,
                               json={"limit": 3})).json()
    cursor = first["result"]["next_cursor"]
    assert cursor
    second = (await client.post(f"{base}/{pinned['id']}/run", headers=h,
                                json={"limit": 3, "cursor": cursor})).json()
    got = ([i["name"] for i in first["result"]["items"]]
           + [i["name"] for i in second["result"]["items"]])
    assert got == ["epsilon", "delta", "gamma", "beta"]


async def test_view_survives_confirmed_rename(client, admin_id, tmp_path):
    v1, v2 = tmp_path / "v1.xlsx", tmp_path / "v2.xlsx"
    make_workbook(v1)                        # Revenue / Expenses / Secrets
    make_workbook(v2, second_sheet="Spending")  # Expenses renamed, same schema
    ds = (await upload_file(client, admin_id, v1, name="book.xlsx",
                            content_type=XLSX_MIME))["dataset_id"]
    h = auth(admin_id)

    view = (await client.post(
        f"/api/v1/datasets/{ds}/views", headers=h,
        json={"name": "costs", "sheet": "Expenses",
              "query": {"sort": [{"column": "cost", "direction": "desc"}]}})).json()

    await upload_file(client, admin_id, v2, name="book.xlsx",
                      content_type=XLSX_MIME, dataset_id=ds)
    r = await client.post(f"/api/v1/datasets/{ds}/versions/2/confirm-rename",
                          headers=h,
                          json={"from_sheet": "Expenses", "to_sheet": "Spending"})
    assert r.status_code == 200, r.text

    # No view mutation happened — logical identity carries it to the new name.
    r = await client.post(f"/api/v1/datasets/{ds}/views/{view['id']}/run", headers=h)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["version_number"] == 2 and body["sheet_name"] == "Spending"
    assert [i["Item"] for i in body["result"]["items"]] == ["rent", "power"]


async def test_view_run_404_when_sheet_leaves_current(client, admin_id, tmp_path):
    v1, v2 = tmp_path / "v1.xlsx", tmp_path / "v2.xlsx"
    make_workbook(v1)                        # has Expenses
    make_workbook(v2, second_sheet="Totally-Different")
    ds = (await upload_file(client, admin_id, v1, name="book.xlsx",
                            content_type=XLSX_MIME))["dataset_id"]
    h = auth(admin_id)
    view = (await client.post(
        f"/api/v1/datasets/{ds}/views", headers=h,
        json={"name": "costs", "sheet": "Expenses", "query": {}})).json()

    await upload_file(client, admin_id, v2, name="book.xlsx",
                      content_type=XLSX_MIME, dataset_id=ds)
    # No rename confirmed: the logical sheet simply isn't in v2.
    r = await client.post(f"/api/v1/datasets/{ds}/views/{view['id']}/run", headers=h)
    assert r.status_code == 404, r.text
    assert r.json()["code"] == "sheet-not-in-version"


async def test_view_authorization(client, admin_id):
    editor, team = await create_team_user(client, admin_id, "editor")
    ds = (await upload_inline(client, editor, json.dumps(ROWS_V1),
                              team_id=team))["dataset_id"]
    base = f"/api/v1/datasets/{ds}/views"
    view = (await client.post(base, headers=auth(editor), json=VIEW_BODY)).json()

    # In-team viewer: can read + run, cannot write (truthful 403).
    viewer, _ = await create_team_user(client, admin_id, "viewer", team_id=team)
    assert (await client.get(base, headers=auth(viewer))).status_code == 200
    r = await client.post(f"{base}/{view['id']}/run", headers=auth(viewer))
    assert r.status_code == 200
    assert (await client.post(base, headers=auth(viewer),
                              json={**VIEW_BODY, "name": "x"})).status_code == 403

    # Outsider: existence hidden.
    outsider, _ = await create_team_user(client, admin_id, "editor")
    for method, url, kwargs in (
        ("get", base, {}),
        ("post", f"{base}/{view['id']}/run", {}),
    ):
        r = await getattr(client, method)(url, headers=auth(outsider), **kwargs)
        assert r.status_code == 404

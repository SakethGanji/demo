"""Explorer error/paging contracts a UI has to be able to branch on.

Each test here pins a place where the endpoint answered with the wrong *shape*
— a 500 where a 400 belonged, a generic ``not_found`` where a distinguishable
state belonged, a bare array where the service's Page envelope belonged, or a
guard that silently could not fire.
"""

from __future__ import annotations

import json

from conftest import XLSX_MIME, auth, make_workbook, upload_file, upload_inline

PROBLEM = "application/problem+json"

ROWS = [
    {"region": "EU", "quarter": "Q1", "amount": 100},
    {"region": "EU", "quarter": "Q1", "amount": 100},
    {"region": "US", "quarter": "Q2", "amount": 250},
]


async def _dataset(client, admin_id):
    return (await upload_inline(client, admin_id, json.dumps(ROWS)))["dataset_id"]


# --- `columns=` that names nothing --------------------------------------------

async def test_duplicates_rejects_a_columns_value_that_names_no_column(
        client, admin_id):
    """``?columns=,`` is truthy but resolves to zero columns.

    It therefore skipped the "group on everything" fallback and interpolated an
    empty list into ``GROUP BY  HAVING COUNT(*) > 1``, which DuckDB rejects at
    parse time. Nothing on this path catches ``duckdb.Error``, so a typo in a
    query string came back as a 500 "An unexpected error occurred." — the one
    response a client cannot act on.
    """
    ds = await _dataset(client, admin_id)
    h = auth(admin_id)

    for value in (",", " ", ",,", " , "):
        r = await client.get(f"/api/v1/datasets/{ds}/versions/1/duplicates",
                             params={"columns": value}, headers=h)
        assert r.status_code == 400, (value, r.status_code, r.text)
        assert r.headers["content-type"].startswith(PROBLEM)
        body = r.json()
        assert body["code"] == "empty-column-selection"
        assert "region" in body["available"]

    # Omitting it entirely still means "every column".
    r = await client.get(f"/api/v1/datasets/{ds}/versions/1/duplicates", headers=h)
    assert r.status_code == 200 and r.json()["exact"] is True


# --- a version that exists but has no bytes yet -------------------------------

async def test_a_still_ingesting_version_is_distinguishable_from_a_missing_one(
        client, admin_id):
    """Poll-or-give-up is the decision, and only the ``code`` can drive it.

    An async upload creates the ``dataset_versions`` row with ``status
    'uploading'`` and ``path NULL`` before any bytes land. That 404 rendered as
    the generic ``not_found`` — byte-identical in ``code`` to "there is no such
    version" — so a client had to parse English prose, or spend an extra
    request on ``/files/upload/status``, to know whether waiting would help.
    """
    from app.features.files import repo as files_repo

    ds = await _dataset(client, admin_id)
    h = auth(admin_id)
    await files_repo.create_version(ds, status="uploading",
                                    source={"type": "upload"})

    for url in (f"/api/v1/datasets/{ds}/versions/2/preview",
                f"/api/v1/datasets/{ds}/versions/2/missing",
                f"/api/v1/datasets/{ds}/versions/2/profile-runs"):
        r = (await client.post(url, headers=h, json={})
             if url.endswith("profile-runs") else await client.get(url, headers=h))
        assert r.status_code == 404, (url, r.text)
        body = r.json()
        assert body["code"] == "version-not-ready", url
        assert body["version_status"] == "uploading"
        assert body["version_number"] == 2

    r = await client.post(f"/api/v1/datasets/{ds}/versions/2/sql", headers=h,
                          json={"sql": "SELECT 1"})
    assert r.status_code == 404 and r.json()["code"] == "version-not-ready"

    # A version that genuinely does not exist keeps the generic code.
    r = await client.get(f"/api/v1/datasets/{ds}/versions/99/preview", headers=h)
    assert r.status_code == 404 and r.json()["code"] == "not_found"


# --- profile runs are a collection --------------------------------------------

async def test_listing_profile_runs_is_paged_and_filterable(client, admin_id,
                                                            tmp_path):
    """It was the only collection in the service returning a bare array.

    Without the envelope a client cannot tell a truncated page from the whole
    set, has no ``total`` to render, and cannot ask for just the completed runs
    — so it has to fetch every run of every sheet and filter client-side.
    """
    path = tmp_path / "book.xlsx"
    make_workbook(path)  # Revenue / Expenses / Secrets
    ds = (await upload_file(client, admin_id, path, name="book.xlsx",
                            content_type=XLSX_MIME))["dataset_id"]
    h = auth(admin_id)
    url = f"/api/v1/datasets/{ds}/versions/1/profile-runs"

    created = (await client.post(url, headers=h)).json()
    assert len(created) >= 2

    r = await client.get(url, headers=h)
    assert r.status_code == 200, r.text
    body = r.json()
    assert set(body) >= {"items", "total", "limit", "offset"}
    assert body["total"] == len(created) and len(body["items"]) == len(created)

    r = await client.get(url, headers=h, params={"limit": 1})
    assert r.status_code == 200
    page = r.json()
    assert len(page["items"]) == 1 and page["total"] == len(created)

    r = await client.get(url, headers=h, params={"status": "completed"})
    assert r.json()["total"] == len(created)
    r = await client.get(url, headers=h, params={"status": "failed"})
    assert r.json()["total"] == 0 and r.json()["items"] == []
    r = await client.get(url, headers=h, params={"algorithm_version": 99})
    assert r.json()["total"] == 0


# --- cursors are scoped to the sheet they were minted on ----------------------

async def test_a_cursor_from_one_sheet_is_rejected_on_another(client, admin_id,
                                                              tmp_path):
    """The cursor carried the version but not the sheet.

    Both sheets of a workbook share a version id, and an empty spec hashes the
    same on either, so a cursor minted on sheet A was accepted verbatim on
    sheet B — the caller silently resumed at that offset in a *different*
    sheet instead of getting the documented ``invalid-cursor``.
    """
    path = tmp_path / "book.xlsx"
    make_workbook(path)
    ds = (await upload_file(client, admin_id, path, name="book.xlsx",
                            content_type=XLSX_MIME))["dataset_id"]
    h = auth(admin_id)
    base = f"/api/v1/datasets/{ds}/versions/1/sheets"

    r = await client.post(f"{base}/Revenue/query", headers=h, json={"limit": 1})
    assert r.status_code == 200, r.text
    cursor = r.json()["next_cursor"]
    assert cursor

    r = await client.post(f"{base}/Expenses/query", headers=h,
                          json={"limit": 1, "cursor": cursor})
    assert r.status_code == 400, r.text
    assert r.json()["code"] == "invalid-cursor"

    # It still works on the sheet it came from.
    r = await client.post(f"{base}/Revenue/query", headers=h,
                          json={"limit": 1, "cursor": cursor})
    assert r.status_code == 200, r.text


# --- the raw-SQL size guard on versions with no recorded sheet sizes ----------

async def _legacy_version(client, admin_id, ds, *, source):
    """A version row shaped like the pre-sheet-table era: no sheet rows."""
    from app.features.files import repo as files_repo
    from app.shared import repo as shared_repo

    v1 = await shared_repo.get_version_by_number(ds, 1)
    return await files_repo.create_version(
        ds, path=v1["path"], storage_type=v1["storage_type"], status="ready",
        size_bytes=None, source=source)


async def test_the_sql_size_guard_still_fires_on_a_version_with_no_sheet_rows(
        client, admin_id, monkeypatch):
    """The guard was structurally unable to fire on legacy versions.

    ``_sql_tables`` returned the synthetic ``{"data": ver["path"]}`` table
    *before* reaching the size check, and the JSONB-fallback sheet rows it
    would otherwise have summed hardcode ``size_bytes: None`` — so the total
    was exactly 0 and the 512MB materialization limit, whose entire job is to
    stop ``open_sandboxed`` OOM-ing the service, never applied to precisely
    the versions whose size nobody recorded.
    """
    from app.features.explorer import service as explorer_service

    monkeypatch.setattr(explorer_service, "MAX_SQL_MATERIALIZE_BYTES", 1)
    ds = await _dataset(client, admin_id)
    h = auth(admin_id)

    # (a) legacy version with no sheet rows at all -> synthetic single table.
    ver = await _legacy_version(client, admin_id, ds, source={"type": "upload"})
    r = await client.post(
        f"/api/v1/datasets/{ds}/versions/{ver['version_number']}/sql",
        headers=h, json={"sql": "SELECT COUNT(*) FROM data"})
    assert r.status_code == 413, r.text
    assert r.json()["code"] == "version-too-large-for-sql"
    assert r.json()["size_bytes"] > 1

    # (b) legacy version whose sheets come from the JSONB fallback, where
    #     size_bytes is always None.
    ver = await _legacy_version(client, admin_id, ds,
                                source={"type": "upload",
                                        "sheets": [{"name": "data"}]})
    r = await client.post(
        f"/api/v1/datasets/{ds}/versions/{ver['version_number']}/sql",
        headers=h, json={"sql": "SELECT COUNT(*) FROM data"})
    assert r.status_code == 413, r.text
    assert r.json()["code"] == "version-too-large-for-sql"


async def test_a_legacy_version_under_the_limit_still_runs_sql(client, admin_id):
    """Sizing a legacy version must not break it — only bound it."""
    ds = await _dataset(client, admin_id)
    ver = await _legacy_version(client, admin_id, ds, source={"type": "upload"})
    r = await client.post(
        f"/api/v1/datasets/{ds}/versions/{ver['version_number']}/sql",
        headers=auth(admin_id), json={"sql": "SELECT COUNT(*) AS n FROM data"})
    assert r.status_code == 200, r.text
    assert r.json()["items"] == [{"n": 3}]

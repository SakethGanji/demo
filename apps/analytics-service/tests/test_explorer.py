"""Wave 1 §6 — explorer preview + structured query endpoints.

Covers: preview, projection/filter/sort/search compilation, cursor paging
round-trips, DSL validation errors as problem+json, the
sheet-selection-required contract on multi-sheet versions, cross-team 404
existence hiding, and the audit trail.
"""

from __future__ import annotations

import json

from conftest import (
    XLSX_MIME,
    auth,
    create_team_user,
    make_crm_workbook,
    make_workbook,
    upload_file,
    upload_inline,
)

PROBLEM = "application/problem+json"

ROWS = [
    {"id": 1, "name": "alpha", "score": 10.5, "tier": "gold"},
    {"id": 2, "name": "beta", "score": 20.0, "tier": "gold"},
    {"id": 3, "name": "gamma", "score": 30.25, "tier": "gold"},
    {"id": 4, "name": "delta", "score": 40.0, "tier": "silver"},
    {"id": 5, "name": "alphabet", "score": 50.0, "tier": "bronze"},
]


async def _inline_dataset(client, admin_id):
    return (await upload_inline(client, admin_id, json.dumps(ROWS)))["dataset_id"]


async def test_preview_single_sheet_version(client, admin_id):
    ds = await _inline_dataset(client, admin_id)
    r = await client.get(f"/api/v1/datasets/{ds}/versions/1/preview",
                        params={"limit": 3}, headers=auth(admin_id))
    assert r.status_code == 200, r.text
    body = r.json()
    assert len(body["items"]) == 3 and body["total"] == 5
    assert body["next_cursor"]  # more rows exist
    assert {"id", "name", "score"} <= set(body["items"][0])


async def test_query_projection_filter_sort(client, admin_id):
    ds = await _inline_dataset(client, admin_id)
    r = await client.post(
        f"/api/v1/datasets/{ds}/versions/1/query", headers=auth(admin_id),
        json={"columns": ["name", "score"],
              "filters": {"logic": "and", "conditions": [
                  {"column": "score", "op": "gt", "value": 15}]},
              "sort": [{"column": "score", "direction": "desc"}]})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["total"] == 4
    assert [row["name"] for row in body["items"]] == [
        "alphabet", "delta", "gamma", "beta"]
    assert set(body["items"][0]) == {"name", "score"}


async def test_query_search_over_text_columns(client, admin_id):
    ds = await _inline_dataset(client, admin_id)
    r = await client.post(f"/api/v1/datasets/{ds}/versions/1/query",
                          headers=auth(admin_id), json={"search": "alpha"})
    assert r.status_code == 200, r.text
    assert {row["name"] for row in r.json()["items"]} == {"alpha", "alphabet"}


async def test_query_cursor_pagination_roundtrip(client, admin_id):
    ds = await _inline_dataset(client, admin_id)
    spec = {"sort": [{"column": "id"}], "limit": 2}
    seen: list[int] = []
    cursor = None
    for _ in range(4):  # 5 rows / page size 2 → 3 pages max
        r = await client.post(f"/api/v1/datasets/{ds}/versions/1/query",
                              headers=auth(admin_id),
                              json={**spec, "cursor": cursor})
        assert r.status_code == 200, r.text
        body = r.json()
        seen += [row["id"] for row in body["items"]]
        cursor = body["next_cursor"]
        if cursor is None:
            break
    assert seen == [1, 2, 3, 4, 5]

    # A cursor is bound to its spec: replaying it under a different query 400s.
    first = await client.post(f"/api/v1/datasets/{ds}/versions/1/query",
                              headers=auth(admin_id), json=spec)
    mismatched = await client.post(
        f"/api/v1/datasets/{ds}/versions/1/query", headers=auth(admin_id),
        json={"sort": [{"column": "score"}], "limit": 2,
              "cursor": first.json()["next_cursor"]})
    assert mismatched.status_code == 400
    assert mismatched.json()["code"] == "invalid-cursor"


async def test_query_dsl_validation_problems(client, admin_id):
    ds = await _inline_dataset(client, admin_id)
    h = auth(admin_id)
    url = f"/api/v1/datasets/{ds}/versions/1/query"

    r = await client.post(url, headers=h, json={"columns": ["nope"]})
    assert r.status_code == 400 and r.json()["code"] == "unknown-column"
    assert "available" in r.json()

    r = await client.post(url, headers=h, json={
        "filters": {"conditions": [
            {"column": "score", "op": "icontains", "value": "x"}]}})
    assert r.status_code == 400 and r.json()["code"] == "operator-type-mismatch"

    r = await client.post(url, headers=h, json={"cursor": "garbage!!"})
    assert r.status_code == 400 and r.json()["code"] == "invalid-cursor"
    assert r.headers["content-type"].startswith(PROBLEM)


async def test_malformed_filter_condition_is_rejected_not_ignored(client, admin_id):
    """Regression: a bad operator used to return every row instead of erroring.

    ``FilterGroup``'s fields all have defaults, so a condition that failed
    ``Filter`` matched the group branch of the union instead and parsed as an
    empty group — dropping the WHERE clause and answering 200 with the whole
    table. It must be a problem+json 4xx, and never a 500.
    """
    ds = await _inline_dataset(client, admin_id)
    h = auth(admin_id)
    url = f"/api/v1/datasets/{ds}/versions/1/query"

    r = await client.post(url, headers=h, json={"filters": {
        "logic": "and",
        "conditions": [{"column": "score", "op": "greater_than", "value": 15}]}})
    body = r.json()
    assert r.status_code == 400, r.text
    assert r.headers["content-type"].startswith(PROBLEM)
    assert body["code"] == "unknown-operator"
    assert body["op"] == "greater_than" and body["column"] == "score"
    assert "gt" in body["available"]
    assert "items" not in body  # emphatically not a 200 over all 5 rows

    # Unknown column inside a filter — same 400 contract as a bad projection.
    r = await client.post(url, headers=h, json={"filters": {
        "conditions": [{"column": "ghost", "op": "eq", "value": 1}]}})
    assert r.status_code == 400 and r.json()["code"] == "unknown-column"

    # A condition that is neither a Filter nor a group.
    r = await client.post(url, headers=h, json={
        "filters": {"conditions": [{"col": "score", "operator": "gt"}]}})
    assert r.status_code == 400 and r.json()["code"] == "invalid-filter"

    # Bad value arity stays a 4xx too — the framework's 422 envelope, with the
    # offending condition's location.
    r = await client.post(url, headers=h, json={"filters": {
        "conditions": [{"column": "score", "op": "between", "value": [1]}]}})
    assert r.status_code == 422, r.text
    assert r.headers["content-type"].startswith(PROBLEM)

    # The correctly-spelled query still works, and filters fewer rows than the
    # unfiltered table — the number the defect used to hand back.
    r = await client.post(url, headers=h, json={"filters": {
        "logic": "and",
        "conditions": [{"column": "score", "op": "gt", "value": 15}]}})
    assert r.status_code == 200, r.text
    assert r.json()["total"] == 4

    # An intentionally empty filter group is still a valid no-op.
    for empty in ({}, {"logic": "or", "conditions": []},
                  {"conditions": [{"logic": "and", "conditions": []}]}):
        r = await client.post(url, headers=h, json={"filters": empty})
        assert r.status_code == 200, r.text
        assert r.json()["total"] == len(ROWS)


async def test_multisheet_requires_sheet_selection(client, admin_id, tmp_path):
    path = tmp_path / "book.xlsx"
    make_workbook(path)
    up = await upload_file(client, admin_id, path, name="book.xlsx",
                           content_type=XLSX_MIME)
    ds = up["dataset_id"]
    h = auth(admin_id)

    r = await client.post(f"/api/v1/datasets/{ds}/versions/1/query",
                          headers=h, json={})
    assert r.status_code == 400, r.text
    assert r.json()["code"] == "sheet-selection-required"
    assert set(r.json()["sheets"]) == {"Revenue", "Expenses", "Secrets"}

    r = await client.get(
        f"/api/v1/datasets/{ds}/versions/1/sheets/Expenses/preview", headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["total"] == 2

    # Sheet-scoped query resolves normalized column names from that sheet.
    r = await client.post(
        f"/api/v1/datasets/{ds}/versions/1/sheets/Expenses/query", headers=h,
        json={"filters": {"conditions": [
            {"column": "item", "op": "eq", "value": "rent"}]}})
    assert r.status_code == 200, r.text
    assert r.json()["total"] == 1 and r.json()["items"][0]["Cost"] == 50

    r = await client.get(
        f"/api/v1/datasets/{ds}/versions/1/sheets/Nope/preview", headers=h)
    assert r.status_code == 404


async def test_cross_team_dataset_hidden(client, admin_id):
    ds = await _inline_dataset(client, admin_id)  # Default team
    outsider, _ = await create_team_user(client, admin_id, "viewer")
    r = await client.get(f"/api/v1/datasets/{ds}/versions/1/preview",
                        headers=auth(outsider))
    assert r.status_code == 404  # existence hidden, not 403


# --- §7 column explorer -------------------------------------------------------


async def test_column_explorer_numeric_candidate_key(client, admin_id):
    ds = await _inline_dataset(client, admin_id)
    r = await client.get(f"/api/v1/datasets/{ds}/versions/1/columns/id",
                        headers=auth(admin_id))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["dtype"] == "numeric"
    assert body["count"] == 5 and body["null_count"] == 0
    assert body["unique_count"] == 5 and body["uniqueness"] == 1.0
    assert body["is_candidate_key"] is True
    assert body["min"] == 1 and body["max"] == 5 and body["median"] == 3
    assert body["histogram"], "numeric column should carry histogram bins"
    assert set(body["examples"]) <= {1, 2, 3, 4, 5}
    assert body["sheet_name"] == "data"


async def test_column_explorer_top_and_rare_values(client, admin_id):
    ds = await _inline_dataset(client, admin_id)
    r = await client.get(f"/api/v1/datasets/{ds}/versions/1/columns/tier",
                        headers=auth(admin_id))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["dtype"] == "categorical"
    assert body["is_candidate_key"] is False
    assert body["uniqueness"] == 0.6  # 3 distinct / 5 non-null
    assert body["top_values"][0] == {"value": "gold", "count": 3, "percent": 60.0}
    rare = body["rare_values"]
    assert rare[0]["count"] == 1 and rare[0]["value"] in {"bronze", "silver"}


async def test_column_explorer_resolves_normalized_names(client, admin_id, tmp_path):
    path = tmp_path / "book.xlsx"
    make_workbook(path)  # Revenue has dup "Amount" headers → amount, amount_2
    ds = (await upload_file(client, admin_id, path, name="book.xlsx",
                            content_type=XLSX_MIME))["dataset_id"]
    h = auth(admin_id)

    r = await client.get(
        f"/api/v1/datasets/{ds}/versions/1/sheets/Revenue/columns/amount_2",
        headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["normalized_name"] == "amount_2"
    assert r.json()["name"] == "Amount.1"  # physical (pandas-deduped) name differs

    # Multi-sheet version without a sheet → the standing contract.
    r = await client.get(f"/api/v1/datasets/{ds}/versions/1/columns/amount_2",
                        headers=h)
    assert r.status_code == 400 and r.json()["code"] == "sheet-selection-required"


async def test_column_explorer_unknown_column(client, admin_id):
    ds = await _inline_dataset(client, admin_id)
    r = await client.get(f"/api/v1/datasets/{ds}/versions/1/columns/nope",
                        headers=auth(admin_id))
    assert r.status_code == 400, r.text
    assert r.json()["code"] == "unknown-column"
    assert "tier" in r.json()["available"]


# --- §6b raw-SQL escape hatch -------------------------------------------------


async def test_sql_cross_sheet_join_and_persisted_result(client, admin_id, tmp_path):
    path = tmp_path / "crm.xlsx"
    make_crm_workbook(path)
    editor, team = await create_team_user(client, admin_id, "editor")
    ds = (await upload_file(client, editor, path, name="crm.xlsx",
                            content_type=XLSX_MIME, team_id=team))["dataset_id"]

    r = await client.post(
        f"/api/v1/datasets/{ds}/versions/1/sql", headers=auth(editor),
        json={"sql": "SELECT c.tier, SUM(o.total) AS spend FROM orders o "
                     "JOIN customers c USING (customer_id) "
                     "GROUP BY c.tier ORDER BY spend DESC"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["columns"] == ["tier", "spend"]
    assert body["items"] == [{"tier": "gold", "spend": 160.0},
                             {"tier": "silver", "spend": 40.0}]
    assert body["row_count"] == 2 and body["truncated"] is False
    assert set(body["tables"]) == {"customers", "orders", "scratch"}

    # The result parquet is persisted, owned, and downloadable by the team...
    fname = body["result_file"]
    r = await client.get(f"/api/v1/samples/{fname}/data", headers=auth(editor))
    assert r.status_code == 200, r.text

    # ...and hidden from other teams (artifact ownership row exists).
    outsider, _ = await create_team_user(client, admin_id, "editor")
    r = await client.get(f"/api/v1/samples/{fname}", headers=auth(outsider))
    assert r.status_code == 404


async def test_sql_rejects_non_select_and_multi_statement(client, admin_id):
    ds = await _inline_dataset(client, admin_id)
    h = auth(admin_id)
    url = f"/api/v1/datasets/{ds}/versions/1/sql"

    r = await client.post(url, headers=h, json={
        "sql": "COPY data TO '/tmp/exfil.parquet' (FORMAT PARQUET)"})
    assert r.status_code == 400 and r.json()["code"] == "select-only"

    r = await client.post(url, headers=h, json={"sql": "SELECT 1; SELECT 2"})
    assert r.status_code == 400 and r.json()["code"] == "invalid-sql"


async def test_sql_file_read_blocked_and_sanitized(client, admin_id):
    ds = await _inline_dataset(client, admin_id)
    r = await client.post(f"/api/v1/datasets/{ds}/versions/1/sql",
                          headers=auth(admin_id),
                          json={"sql": "SELECT * FROM '/etc/passwd'"})
    assert r.status_code == 400, r.text
    assert r.json()["code"] == "sql-error"
    assert "/etc/passwd" not in r.json()["detail"]


async def test_sql_size_guard(client, admin_id, monkeypatch):
    from app.features.explorer import service as explorer_service

    monkeypatch.setattr(explorer_service, "MAX_SQL_MATERIALIZE_BYTES", 1)
    ds = await _inline_dataset(client, admin_id)
    r = await client.post(f"/api/v1/datasets/{ds}/versions/1/sql",
                          headers=auth(admin_id),
                          json={"sql": "SELECT COUNT(*) FROM data"})
    assert r.status_code == 413, r.text
    assert r.json()["code"] == "version-too-large-for-sql"


async def test_sql_cross_team_404(client, admin_id):
    ds = await _inline_dataset(client, admin_id)
    outsider, _ = await create_team_user(client, admin_id, "viewer")
    r = await client.post(f"/api/v1/datasets/{ds}/versions/1/sql",
                          headers=auth(outsider),
                          json={"sql": "SELECT 1"})
    assert r.status_code == 404


async def test_query_lands_in_audit_trail(client, admin_id):
    ds = await _inline_dataset(client, admin_id)
    h = auth(admin_id)
    r = await client.post(f"/api/v1/datasets/{ds}/versions/1/query",
                          headers=h, json={})
    assert r.status_code == 200
    entries = (await client.get("/api/v1/audit", params={"limit": 10},
                                headers=h)).json()["items"]
    assert any(e["path"].endswith("/versions/1/query") for e in entries)

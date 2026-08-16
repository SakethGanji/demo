"""Aggregation extras (ROADMAP §3) — POST /api/v1/aggregate.

Structured pre-aggregation filters (bound values), HAVING over aggregation
aliases, date/numeric group-by bucketing, conditional aggregates via
``AggregationSpec.filter``, sort validation over bucket/agg aliases, grand
totals re-aggregated over every group, and the server-side output row cap with
the ``truncated`` flag.
"""

from __future__ import annotations

from app.features.data_accelerator.services import aggregation as aggregation_service
from conftest import auth, upload_file

SALES_CSV = """region,product,amount,order_date,rep
EU,widget,100.5,2024-01-05,Alice
EU,gadget,50.0,2024-01-20,O'Brien
US,widget,200.0,2024-02-10,Bob
US,gadget,75.0,2024-02-15,Alice
APAC,widget,300.0,2024-03-01,O'Brien
EU,widget,25.0,2024-03-15,Bob
"""
# Region totals: EU 175.5, US 275.0, APAC 300.0

# 8 groups — deliberately more than the limits the totals tests use, so a
# page-scoped total is visibly different from the real one.
MANY_GROUPS_CSV = "grp,amount\n" + "".join(
    f"g{i},{i * 1000}\n" for i in range(1, 9))
# sum = 36000, count = 8, max = 8000. Summing the per-group maxima also gives
# 36000 — the shape of the defect where a `max` footer read 75000.


async def _upload_sales(client, admin_id, tmp_path):
    p = tmp_path / "sales.csv"
    p.write_text(SALES_CSV)
    return (await upload_file(client, admin_id, p))["dataset_id"]


async def _upload_many_groups(client, admin_id, tmp_path):
    p = tmp_path / "many_groups.csv"
    p.write_text(MANY_GROUPS_CSV)
    return (await upload_file(client, admin_id, p))["dataset_id"]


def _body(ds, **over):
    body = {
        "dataset_id": ds,
        "group_by": ["region"],
        "aggregations": [{"column": "amount", "function": "sum", "alias": "total"}],
    }
    body.update(over)
    return body


async def _post(client, admin_id, body):
    return await client.post("/api/v1/aggregate", headers=auth(admin_id), json=body)


async def test_plain_aggregation_still_works(client, admin_id, tmp_path):
    """Existing-style request: group/sum/sort/limit — no regression, truncated off."""
    ds = await _upload_sales(client, admin_id, tmp_path)
    r = await _post(client, admin_id, _body(
        ds, sort_by="total", sort_order="desc", limit=2))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["success"] and body["truncated"] is False
    assert body["columns"] == ["region", "total"]
    assert body["group_count"] == 2
    assert [(row["region"], row["total"]) for row in body["data"]] == [
        ("APAC", 300.0), ("US", 275.0)]
    # Grand total spans EVERY group, not the 2 that came back (which sum to
    # 575.0) — the footer describes the whole result set, not the page.
    assert body["totals"]["total"] == 750.5
    assert body["totals_omitted"] is None


async def test_structured_filters_with_bound_quote(client, admin_id, tmp_path):
    """A value containing a single quote goes through as a bind, not SQL text."""
    ds = await _upload_sales(client, admin_id, tmp_path)
    r = await _post(client, admin_id, _body(ds, filters={
        "logic": "and",
        "conditions": [{"column": "rep", "op": "eq", "value": "O'Brien"}]}))
    assert r.status_code == 200, r.text
    rows = {row["region"]: row["total"] for row in r.json()["data"]}
    assert rows == {"EU": 50.0, "APAC": 300.0}

    # Unknown filter column is rejected before any query runs.
    r = await _post(client, admin_id, _body(ds, filters={
        "conditions": [{"column": "ghost", "op": "eq", "value": 1}]}))
    assert r.status_code == 400 and "ghost" in r.json()["detail"]


async def test_filters_and_filter_expr_are_anded(client, admin_id, tmp_path):
    ds = await _upload_sales(client, admin_id, tmp_path)
    r = await _post(client, admin_id, _body(
        ds,
        filters={"conditions": [{"column": "rep", "op": "eq", "value": "Alice"}]},
        filter_expr="amount >= 100"))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["group_count"] == 1
    assert body["data"] == [{"region": "EU", "total": 100.5}]


async def test_having_filters_groups_and_rejects_unknown_alias(client, admin_id, tmp_path):
    ds = await _upload_sales(client, admin_id, tmp_path)
    r = await _post(client, admin_id, _body(
        ds, having=[{"column": "total", "op": "gt", "value": 200}]))
    assert r.status_code == 200, r.text
    assert {row["region"] for row in r.json()["data"]} == {"US", "APAC"}

    r = await _post(client, admin_id, _body(
        ds, having=[{"column": "nope", "op": "gt", "value": 200}]))
    assert r.status_code == 400
    detail = r.json()["detail"]
    assert "nope" in detail and "total" in detail  # names the valid aliases


async def test_date_trunc_bucket_and_sort_on_bucket_alias(client, admin_id, tmp_path):
    ds = await _upload_sales(client, admin_id, tmp_path)
    r = await _post(client, admin_id, _body(
        ds,
        group_by=[{"column": "order_date", "date_trunc": "month", "alias": "month"}],
        sort_by="month", sort_order="asc"))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["columns"] == ["month", "total"]
    months = [str(row["month"])[:7] for row in body["data"]]
    assert months == ["2024-01", "2024-02", "2024-03"]
    assert [row["total"] for row in body["data"]] == [150.5, 275.0, 325.0]


async def test_bin_width_bucket_default_alias(client, admin_id, tmp_path):
    ds = await _upload_sales(client, admin_id, tmp_path)
    r = await _post(client, admin_id, _body(
        ds,
        group_by=[{"column": "amount", "bin_width": 100}],
        sort_by="amount_bucket", sort_order="asc"))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["columns"] == ["amount_bucket", "total"]
    assert [(row["amount_bucket"], row["total"]) for row in body["data"]] == [
        (0.0, 150.0), (100.0, 100.5), (200.0, 200.0), (300.0, 300.0)]


async def test_bin_count_bucket(client, admin_id, tmp_path):
    """4 equal-width bins over amount's min/max (25..300); max lands in bin 4."""
    ds = await _upload_sales(client, admin_id, tmp_path)
    r = await _post(client, admin_id, _body(
        ds,
        group_by=[{"column": "amount", "bin_count": 4, "alias": "bin"}],
        sort_by="bin", sort_order="asc"))
    assert r.status_code == 200, r.text
    assert [(row["bin"], row["total"]) for row in r.json()["data"]] == [
        (1, 150.0), (2, 100.5), (3, 200.0), (4, 300.0)]


async def test_conditional_aggregate_with_where_and_having(client, admin_id, tmp_path):
    """WHERE + FILTER + HAVING binds all in one statement, plus sort on an agg alias."""
    ds = await _upload_sales(client, admin_id, tmp_path)
    r = await _post(client, admin_id, _body(
        ds,
        filters={"conditions": [{"column": "rep", "op": "neq", "value": "Bob"}]},
        aggregations=[
            {"column": "amount", "function": "sum", "alias": "total"},
            {"column": "amount", "function": "sum", "alias": "widget_total",
             "filter": {"conditions": [
                 {"column": "product", "op": "eq", "value": "widget"}]}},
        ],
        having=[{"column": "total", "op": "gt", "value": 100}],
        sort_by="total", sort_order="desc"))
    assert r.status_code == 200, r.text
    body = r.json()
    # Bob's rows excluded: EU 150.5 (widget 100.5), US 75 (dropped by HAVING), APAC 300.
    assert [(row["region"], row["total"], row["widget_total"])
            for row in body["data"]] == [("APAC", 300.0, 300.0), ("EU", 150.5, 100.5)]


async def test_sort_on_aggregation_alias_asc(client, admin_id, tmp_path):
    ds = await _upload_sales(client, admin_id, tmp_path)
    r = await _post(client, admin_id, _body(ds, sort_by="total", sort_order="asc"))
    assert r.status_code == 200, r.text
    assert [row["region"] for row in r.json()["data"]] == ["EU", "US", "APAC"]


async def test_row_cap_sets_truncated(client, admin_id, tmp_path, monkeypatch):
    ds = await _upload_sales(client, admin_id, tmp_path)
    monkeypatch.setattr(aggregation_service, "MAX_AGGREGATION_ROWS", 2)

    # No user limit: the cap cuts the 3 groups down to 2 → truncated.
    r = await _post(client, admin_id, _body(ds, sort_by="total", sort_order="desc"))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["group_count"] == 2 and len(body["data"]) == 2
    assert body["truncated"] is True
    assert [row["region"] for row in body["data"]] == ["APAC", "US"]

    # A user limit below the cap did its own cutting → not "truncated".
    r = await _post(client, admin_id, _body(ds, limit=1))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["group_count"] == 1 and body["truncated"] is False


async def test_empty_group_by_returns_single_grand_total_row(client, admin_id, tmp_path):
    """No group_by → GROUP BY is omitted: one row aggregating the whole table."""
    ds = await _upload_sales(client, admin_id, tmp_path)
    r = await _post(client, admin_id, _body(
        ds,
        group_by=[],
        aggregations=[
            {"column": "amount", "function": "sum", "alias": "total"},
            {"column": "region", "function": "nunique", "alias": "regions"},
        ],
        sort_by="total", sort_order="desc"))  # sort on an agg alias still accepted
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["success"] and body["truncated"] is False
    assert body["columns"] == ["total", "regions"]
    assert body["group_count"] == 1
    assert body["data"] == [{"total": 750.5, "regions": 3}]


async def test_bucket_with_two_kinds_is_422(client, admin_id, tmp_path):
    ds = await _upload_sales(client, admin_id, tmp_path)
    r = await _post(client, admin_id, _body(
        ds, group_by=[{"column": "amount", "bin_width": 100, "bin_count": 4}]))
    assert r.status_code == 422


# ---------------------------------------------------------------------------
# Grand totals — over every group, and only where a total means something
# ---------------------------------------------------------------------------


async def test_sum_and_count_totals_span_all_groups_when_truncated(
    client, admin_id, tmp_path, monkeypatch,
):
    """The regression: totals used to be a SUM over the returned page, so they
    went silently partial the moment the server cap cut the result."""
    ds = await _upload_many_groups(client, admin_id, tmp_path)
    monkeypatch.setattr(aggregation_service, "MAX_AGGREGATION_ROWS", 3)

    r = await _post(client, admin_id, _body(
        ds, group_by=["grp"],
        aggregations=[{"column": "amount", "function": "sum", "alias": "total"},
                      {"column": "amount", "function": "count", "alias": "n"}],
        sort_by="total", sort_order="desc"))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["truncated"] is True
    assert body["group_count"] == 3
    assert [row["grp"] for row in body["data"]] == ["g8", "g7", "g6"]
    # The page holds 8000+7000+6000 = 21000 across 3 rows; the totals cover all 8.
    assert sum(row["total"] for row in body["data"]) == 21000.0
    assert body["totals"] == {"total": 36000.0, "n": 8}
    assert body["totals_omitted"] is None


async def test_totals_span_all_groups_when_the_caller_limits(
    client, admin_id, tmp_path,
):
    """Same rule when the caller — not the cap — did the cutting."""
    ds = await _upload_many_groups(client, admin_id, tmp_path)
    r = await _post(client, admin_id, _body(
        ds, group_by=["grp"],
        aggregations=[{"column": "amount", "function": "sum", "alias": "total"}],
        sort_by="total", sort_order="desc", limit=2))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["truncated"] is False and body["group_count"] == 2
    assert sum(row["total"] for row in body["data"]) == 15000.0
    assert body["totals"] == {"total": 36000.0}


async def test_non_additive_totals_are_omitted_with_a_reason_not_summed(
    client, admin_id, tmp_path,
):
    """`max` must never report the SUM of the per-group maxima (36000 here)."""
    ds = await _upload_many_groups(client, admin_id, tmp_path)
    r = await _post(client, admin_id, _body(
        ds, group_by=["grp"],
        aggregations=[
            {"column": "amount", "function": "sum", "alias": "total"},
            {"column": "amount", "function": "max", "alias": "peak"},
            {"column": "amount", "function": "mean", "alias": "avg"},
            {"column": "grp", "function": "nunique", "alias": "groups"},
        ]))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["totals"] == {"total": 36000.0}
    # Absent from totals AND explained, so "no total" is distinguishable from
    # "the total is zero".
    for alias in ("peak", "avg", "groups"):
        assert alias not in body["totals"]
        assert body["totals_omitted"][alias] == "non-additive"


async def test_totals_are_omitted_entirely_when_nothing_is_additive(
    client, admin_id, tmp_path,
):
    ds = await _upload_many_groups(client, admin_id, tmp_path)
    r = await _post(client, admin_id, _body(
        ds, group_by=["grp"],
        aggregations=[{"column": "amount", "function": "max", "alias": "peak"}]))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["totals"] is None
    assert body["totals_omitted"] == {"peak": "non-additive"}


async def test_totals_cover_the_groups_that_passed_having(client, admin_id, tmp_path):
    """HAVING removes groups from the table, so they stay out of the footer —
    but the LIMIT still must not bound it."""
    ds = await _upload_many_groups(client, admin_id, tmp_path)
    r = await _post(client, admin_id, _body(
        ds, group_by=["grp"],
        aggregations=[{"column": "amount", "function": "sum", "alias": "total"}],
        having=[{"column": "total", "op": "gte", "value": 6000}],
        sort_by="total", sort_order="desc", limit=1))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["group_count"] == 1 and body["data"][0]["total"] == 8000.0
    assert body["totals"] == {"total": 21000.0}  # g6 + g7 + g8


# ---------------------------------------------------------------------------
# Sort validation — never silently invert, never silently drop the ORDER BY
# ---------------------------------------------------------------------------


async def test_invalid_sort_order_is_422_not_a_silent_descending_sort(
    client, admin_id, tmp_path,
):
    """`sort_order` is a strict lowercase enum, matching pivot. Anything else
    used to fall through to DESC, inverting requests like 'ASC'/'ascending'."""
    ds = await _upload_sales(client, admin_id, tmp_path)
    for bad in ("ASC", "Asc", "ascending", "descending", "garbage", ""):
        r = await _post(client, admin_id, _body(
            ds, sort_by="total", sort_order=bad))
        assert r.status_code == 422, f"{bad!r} -> {r.status_code}: {r.text}"
        assert r.json()["status"] == 422

    # Only the exact lowercase values are accepted, and they mean what they say.
    r = await _post(client, admin_id, _body(ds, sort_by="total", sort_order="asc"))
    assert r.status_code == 200, r.text
    assert [row["region"] for row in r.json()["data"]] == ["EU", "US", "APAC"]
    r = await _post(client, admin_id, _body(ds, sort_by="total", sort_order="desc"))
    assert r.status_code == 200, r.text
    assert [row["region"] for row in r.json()["data"]] == ["APAC", "US", "EU"]


async def test_omitted_sort_order_still_defaults_to_desc(client, admin_id, tmp_path):
    """The default is unchanged — only invalid input behaves differently now."""
    ds = await _upload_sales(client, admin_id, tmp_path)
    r = await _post(client, admin_id, _body(ds, sort_by="total"))
    assert r.status_code == 200, r.text
    assert [row["region"] for row in r.json()["data"]] == ["APAC", "US", "EU"]


async def test_invalid_sort_by_is_rejected_naming_the_valid_options(
    client, admin_id, tmp_path,
):
    """It used to drop the ORDER BY silently and return arbitrary order."""
    ds = await _upload_sales(client, admin_id, tmp_path)
    r = await _post(client, admin_id, _body(ds, sort_by="ghost"))
    assert r.status_code == 400, r.text
    problem = r.json()
    assert problem["code"] == "unknown-column"
    assert "ghost" in problem["detail"]
    assert problem["columns"] == ["ghost"]
    assert problem["available"] == ["region", "total"]  # group names + agg aliases

    # A bucket alias is a valid sort target; the underlying column is not.
    r = await _post(client, admin_id, _body(
        ds, group_by=[{"column": "order_date", "date_trunc": "month",
                       "alias": "month"}],
        sort_by="order_date"))
    assert r.status_code == 400, r.text
    assert r.json()["available"] == ["month", "total"]

"""Aggregating a column that holds a float near the double ceiling (1e308).

The same arithmetic that took out `POST /profile` reaches DuckDB through
`POST /aggregate` and `POST /pivot`: `function: "std"` compiles to
`STDDEV_SAMP`, which accumulates a sum of squares and *raises*
`OutOfRangeException` when that intermediate leaves the double range. It is a
grouped query, so one extreme value in one group used to take out every group
and every other measure in the same request — the whole aggregation failed,
not the one cell that has no finite answer.

`SUM` fails the other way round on the same data: it does not raise, it returns
`inf`, which is not JSON and moves the failure to the client's parser.

The rule, which must read the same here as it does on a profile: a figure with
no finite double comes back null, is NAMED as unavailable, and every other
group and measure still computes.
"""

from __future__ import annotations

import json

from conftest import DEFAULT_TEAM_ID, auth, upload_file

# `big` cannot have a standard deviation (the sum of squares overflows); `ok`
# is an ordinary group sharing the request with it, and must be untouched.
EXTREME_CSV = """grp,amount
big,1e308
big,-1e308
big,5
ok,10
ok,20
ok,30
"""

# Two same-signed extremes: SUM is +inf here, with no exception raised at all.
SUM_OVERFLOW_CSV = """grp,amount
big,1e308
big,1e308
ok,10
ok,20
"""

# A second dimension, so a pivot widens into a grid where exactly one cell
# (big/x) is the unrepresentable one. Every other cell has std 10.
WIDE_CSV = """grp,part,amount
big,x,1e308
big,x,-1e308
big,x,5
big,y,10
big,y,20
big,y,30
ok,x,10
ok,x,20
ok,x,30
ok,y,10
ok,y,20
ok,y,30
"""


async def _upload(client, admin_id, tmp_path, body: str, name: str) -> str:
    p = tmp_path / name
    p.write_text(body)
    return (await upload_file(client, admin_id, p))["dataset_id"]


def assert_json_finite(body) -> None:
    """No Infinity/-Infinity/NaN anywhere in the payload — none of the three is
    JSON, so a client parser rejects the whole response."""
    json.dumps(body, allow_nan=False)


def row(body: dict, dim: str, value: str) -> dict:
    return next(r for r in body["data"] if r[dim] == value)


async def _aggregate(client, admin_id, **body):
    return await client.post("/api/v1/aggregate", headers=auth(admin_id), json=body)


async def _pivot(client, admin_id, **body):
    return await client.post("/api/v1/pivot", headers=auth(admin_id), json=body)


async def test_grouped_std_over_a_group_containing_1e308(client, admin_id, tmp_path):
    ds = await _upload(client, admin_id, tmp_path, EXTREME_CSV, "extreme.csv")
    r = await _aggregate(
        client, admin_id, dataset_id=ds, group_by=["grp"],
        aggregations=[{"column": "amount", "function": "std", "alias": "sd"}])
    assert r.status_code == 200, (r.status_code, r.text)
    body = r.json()
    assert_json_finite(body)

    assert body["group_count"] == 2
    # The group with no representable deviation reads null — never a number.
    assert row(body, "grp", "big")["sd"] is None
    # The ordinary group is computed exactly, as if the other group didn't exist.
    assert row(body, "grp", "ok")["sd"] == 10.0
    # And the response says WHICH measure had no finite value.
    assert body["unavailable_measures"] == ["sd"]


async def test_one_bad_group_does_not_blank_the_other_measures(client, admin_id, tmp_path):
    ds = await _upload(client, admin_id, tmp_path, EXTREME_CSV, "extreme.csv")
    r = await _aggregate(
        client, admin_id, dataset_id=ds, group_by=["grp"],
        aggregations=[
            {"column": "amount", "function": "std", "alias": "sd"},
            {"column": "amount", "function": "sum", "alias": "total"},
            {"column": "amount", "function": "mean", "alias": "avg"},
            {"column": "amount", "function": "min", "alias": "lo"},
            {"column": "amount", "function": "max", "alias": "hi"},
            {"column": "amount", "function": "count", "alias": "n"},
        ])
    assert r.status_code == 200, (r.status_code, r.text)
    body = r.json()
    assert_json_finite(body)

    ok = row(body, "grp", "ok")
    assert (ok["sd"], ok["total"], ok["avg"], ok["lo"], ok["hi"], ok["n"]) == (
        10.0, 60.0, 20.0, 10.0, 30.0, 3)

    big = row(body, "grp", "big")
    assert big["sd"] is None
    assert big["n"] == 3 and big["lo"] == -1e308 and big["hi"] == 1e308
    # Only the deviation is missing; the additive measures are real numbers.
    assert big["total"] is not None and big["avg"] is not None

    assert body["unavailable_measures"] == ["sd"]
    # `sum`/`count` still get their grand totals — the recovery must not cost
    # the footer.
    assert body["totals"]["total"] == 65.0
    assert body["totals"]["n"] == 6


async def test_std_with_no_group_by_is_a_single_unrepresentable_row(
        client, admin_id, tmp_path):
    # No group_by at all: one grand-total row, and it is the overflowing one.
    ds = await _upload(client, admin_id, tmp_path, EXTREME_CSV, "extreme.csv")
    r = await _aggregate(
        client, admin_id, dataset_id=ds, group_by=[],
        aggregations=[{"column": "amount", "function": "std", "alias": "sd"},
                      {"column": "amount", "function": "count", "alias": "n"}])
    assert r.status_code == 200, (r.status_code, r.text)
    body = r.json()
    assert_json_finite(body)
    assert body["data"] == [{"sd": None, "n": 6}]
    assert body["unavailable_measures"] == ["sd"]


async def test_sum_overflowing_to_infinity_is_null_and_named(client, admin_id, tmp_path):
    ds = await _upload(client, admin_id, tmp_path, SUM_OVERFLOW_CSV, "sumoverflow.csv")
    r = await _aggregate(
        client, admin_id, dataset_id=ds, group_by=["grp"],
        aggregations=[{"column": "amount", "function": "sum", "alias": "total"}])
    assert r.status_code == 200, (r.status_code, r.text)
    body = r.json()
    assert_json_finite(body)
    assert row(body, "grp", "big")["total"] is None
    assert row(body, "grp", "ok")["total"] == 30.0
    assert body["unavailable_measures"] == ["total"]


async def test_the_persisted_result_artifact_is_also_finite(client, admin_id, tmp_path):
    # The parquet the response points at is read back by /samples/{f}/data, so
    # an Infinity written into it fails there instead — one step further from
    # the cause.
    ds = await _upload(client, admin_id, tmp_path, SUM_OVERFLOW_CSV, "sumoverflow.csv")
    r = await _aggregate(
        client, admin_id, dataset_id=ds, group_by=["grp"],
        aggregations=[{"column": "amount", "function": "sum", "alias": "total"}])
    assert r.status_code == 200, (r.status_code, r.text)
    name = r.json()["result_file"]
    got = await client.get(f"/api/v1/samples/{name}/data", headers=auth(admin_id))
    assert got.status_code == 200, (got.status_code, got.text)
    assert_json_finite(got.json())


async def test_sorting_by_the_unrepresentable_measure_still_returns(
        client, admin_id, tmp_path):
    ds = await _upload(client, admin_id, tmp_path, EXTREME_CSV, "extreme.csv")
    r = await _aggregate(
        client, admin_id, dataset_id=ds, group_by=["grp"],
        aggregations=[{"column": "amount", "function": "std", "alias": "sd"}],
        sort_by="sd", sort_order="desc")
    assert r.status_code == 200, (r.status_code, r.text)
    body = r.json()
    assert_json_finite(body)
    assert body["group_count"] == 2
    assert row(body, "grp", "ok")["sd"] == 10.0


async def test_filtered_conditional_std_survives_the_extreme_group(
        client, admin_id, tmp_path):
    # A per-measure FILTER (WHERE ...) is compiled with bound values; the
    # recovery path must carry those binds, not drop them.
    ds = await _upload(client, admin_id, tmp_path, EXTREME_CSV, "extreme.csv")
    r = await _aggregate(
        client, admin_id, dataset_id=ds, group_by=["grp"],
        aggregations=[
            {"column": "amount", "function": "std", "alias": "sd"},
            {"column": "amount", "function": "sum", "alias": "positive",
             "filter": {"conditions": [
                 {"column": "amount", "op": "gt", "value": 0}]}},
        ])
    assert r.status_code == 200, (r.status_code, r.text)
    body = r.json()
    assert_json_finite(body)
    assert row(body, "grp", "ok")["positive"] == 60.0
    assert row(body, "grp", "ok")["sd"] == 10.0
    assert row(body, "grp", "big")["sd"] is None
    assert body["unavailable_measures"] == ["sd"]


async def test_one_bad_group_among_many_leaves_the_rest_exact(client, admin_id, tmp_path):
    # 40 groups, one of which cannot have a deviation. The other 39 must come
    # back with the value DuckDB would have computed had the extreme row never
    # existed — this is the case that a "null the measure" shortcut would get
    # wrong, and it is also what exercises the halving that isolates the group.
    rows = ["grp,amount"]
    for g in range(40):
        rows += [f"g{g:02d},{10 + g}", f"g{g:02d},{20 + g}", f"g{g:02d},{30 + g}"]
    rows.append("g17,1e308")
    ds = await _upload(client, admin_id, tmp_path, "\n".join(rows) + "\n", "many.csv")

    r = await _aggregate(
        client, admin_id, dataset_id=ds, group_by=["grp"],
        aggregations=[{"column": "amount", "function": "std", "alias": "sd"},
                      {"column": "amount", "function": "count", "alias": "n"}])
    assert r.status_code == 200, (r.status_code, r.text)
    body = r.json()
    assert_json_finite(body)
    assert body["group_count"] == 40
    assert body["unavailable_measures"] == ["sd"]
    for g in range(40):
        got = row(body, "grp", f"g{g:02d}")
        if g == 17:
            assert got["sd"] is None and got["n"] == 4
            continue
        # Three consecutive-ish values 10 apart: sample std is exactly 10.
        assert got["sd"] == 10.0, (g, got)
        assert got["n"] == 3


async def test_two_measures_overflow_in_different_groups(client, admin_id, tmp_path):
    # `sd` has no value for `big`, `total` has none for `huge`, and neither
    # measure loses the group the OTHER one failed in.
    body = "grp,amount\nbig,1e308\nbig,-1e308\nbig,5\nhuge,1e308\nhuge,1e308\nok,10\nok,20\nok,30\n"
    ds = await _upload(client, admin_id, tmp_path, body, "twobad.csv")
    r = await _aggregate(
        client, admin_id, dataset_id=ds, group_by=["grp"],
        aggregations=[{"column": "amount", "function": "std", "alias": "sd"},
                      {"column": "amount", "function": "sum", "alias": "total"}])
    assert r.status_code == 200, (r.status_code, r.text)
    got = r.json()
    assert_json_finite(got)
    assert row(got, "grp", "big")["sd"] is None
    assert row(got, "grp", "big")["total"] is not None
    assert row(got, "grp", "huge")["total"] is None
    assert row(got, "grp", "huge")["sd"] == 0.0
    assert row(got, "grp", "ok") == {"grp": "ok", "sd": 10.0, "total": 60.0}
    assert got["unavailable_measures"] == ["sd", "total"]
    # No grand total for a measure that has no finite value somewhere — a
    # footer computed over what is left would be a partial sum called a whole.
    assert (got["totals"] or {}).get("total") is None
    assert got["totals_omitted"]["total"] == "unrepresentable"


async def test_having_over_a_measure_that_overflows(client, admin_id, tmp_path):
    # HAVING re-expands the aggregate expression, so it has to be re-expanded
    # over the recovered measure too, or the filter raises the exception the
    # main query just recovered from.
    ds = await _upload(client, admin_id, tmp_path, EXTREME_CSV, "extreme.csv")
    r = await _aggregate(
        client, admin_id, dataset_id=ds, group_by=["grp"],
        aggregations=[{"column": "amount", "function": "std", "alias": "sd"},
                      {"column": "amount", "function": "count", "alias": "n"}],
        having=[{"column": "sd", "op": "gt", "value": 1}])
    assert r.status_code == 200, (r.status_code, r.text)
    body = r.json()
    assert_json_finite(body)
    # The unrepresentable group cannot satisfy a `> 1` test, so it drops out —
    # the same way SQL drops a NULL — and the ordinary group survives.
    assert [g["grp"] for g in body["data"]] == ["ok"]


async def test_pivot_std_over_a_group_containing_1e308(client, admin_id, tmp_path):
    ds = await _upload(client, admin_id, tmp_path, EXTREME_CSV, "extreme.csv")
    r = await _pivot(
        client, admin_id, dataset_id=ds, rows=["grp"],
        values=[{"column": "amount", "function": "std", "alias": "sd"}])
    assert r.status_code == 200, (r.status_code, r.text)
    body = r.json()
    assert_json_finite(body)
    assert row(body, "grp", "big")["sd"] is None
    assert row(body, "grp", "ok")["sd"] == 10.0
    assert body["unavailable_measures"] == ["sd"]
    # The grand total is over every row, which includes the extreme one, so it
    # has no finite value either — null, not a number.
    assert body["totals"] == {"sd": None}


async def test_pivot_widened_cells_survive_the_extreme_group(client, admin_id, tmp_path):
    # With a pivot dimension the measure is widened into one column per value:
    # exactly one cell of the grid has no finite deviation, and the rest of the
    # grid — including the row and column totals — still computes.
    ds = await _upload(client, admin_id, tmp_path, WIDE_CSV, "wide.csv")
    r = await _pivot(
        client, admin_id, dataset_id=ds, rows=["grp"], columns="part",
        values=[{"column": "amount", "function": "std", "alias": "sd"}],
        include_column_totals=True)
    assert r.status_code == 200, (r.status_code, r.text)
    body = r.json()
    assert_json_finite(body)
    assert body["pivot_columns"] == ["x", "y"]
    big, ok = row(body, "grp", "big"), row(body, "grp", "ok")
    assert big["x"] is None            # the extreme cell
    assert big["y"] == 10.0            # its neighbour in the same row
    assert ok["x"] == 10.0 and ok["y"] == 10.0
    assert body["unavailable_measures"] == ["sd"]


async def test_ordinary_aggregation_names_nothing_unavailable(client, admin_id, tmp_path):
    ds = await _upload(client, admin_id, tmp_path, EXTREME_CSV, "extreme.csv")
    r = await _aggregate(
        client, admin_id, dataset_id=ds, group_by=["grp"],
        aggregations=[{"column": "amount", "function": "count", "alias": "n"}],
        filters={"conditions": [{"column": "grp", "op": "eq", "value": "ok"}]})
    assert r.status_code == 200, (r.status_code, r.text)
    body = r.json()
    assert_json_finite(body)
    assert body["data"] == [{"grp": "ok", "n": 3}]
    assert body["unavailable_measures"] == []


async def test_saved_analytics_definition_runs_over_extreme_data(
        client, admin_id, tmp_path):
    # The library runs the very same service for a saved definition, so a
    # scheduled/report run failed identically — and a failed run is recorded
    # as one, which looks like the dataset broke.
    ds = await _upload(client, admin_id, tmp_path, EXTREME_CSV, "extreme.csv")
    h = {**auth(admin_id), "X-Team-Id": DEFAULT_TEAM_ID}
    r = await client.post(f"/api/v1/datasets/{ds}/analytics", headers=h, json={
        "name": "deviation-by-group", "kind": "aggregate",
        "params": {"group_by": ["grp"],
                   "aggregations": [{"column": "amount", "function": "std",
                                     "alias": "sd"}]}})
    assert r.status_code == 201, r.text
    definition = r.json()["id"]

    r = await client.post(f"/api/v1/datasets/{ds}/analytics/{definition}/run",
                          headers=h)
    assert r.status_code == 200, (r.status_code, r.text)
    body = r.json()
    assert_json_finite(body)
    assert body["status"] == "completed"

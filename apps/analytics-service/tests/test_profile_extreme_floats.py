"""Profiling a column that holds a float near the double ceiling (1e308).

DuckDB's variance-family aggregates (STDDEV_SAMP, VAR_SAMP, CORR — CORR is
STDDEV_POP under the hood) accumulate a sum of squares, so a single legitimate,
finite value near DBL_MAX makes that intermediate overflow and DuckDB raises
`OutOfRangeException` rather than returning a value. The histogram's bin edges
overflow the same way, to +/-inf, which is not encodable as JSON.

Both are per-SHEET failures: the profile is computed for every column at once,
so one extreme value used to take out the column deep-dive, the analytics lens
and the aggregate builder's capability gates for the whole sheet. A statistic
that cannot be represented must come back as null (and be named as unavailable),
never as a fabricated number, an `Infinity`, or a 500.
"""

from __future__ import annotations

import json
import math

from conftest import DEFAULT_TEAM_ID, auth, upload_inline


async def upload_csv(client, admin_id, body: str) -> str:
    """Upload *body* as a CSV — the reporter's path; ingest keeps 1e308 exactly."""
    r = await client.post(
        "/api/v1/upload", headers={**auth(admin_id), "X-Team-Id": DEFAULT_TEAM_ID},
        files={"file": ("extreme.csv", body.encode(), "text/csv")})
    assert r.status_code == 200, r.text
    return r.json()["dataset_id"]


async def profile(client, admin_id, ds: str) -> dict:
    r = await client.post("/api/v1/profile", headers=auth(admin_id),
                          json={"dataset_id": ds})
    assert r.status_code == 200, (r.status_code, r.text)
    return r.json()


def col(body: dict, name: str) -> dict:
    return next(c for c in body["columns"] if c["name"] == name)


def assert_json_finite(body) -> None:
    """No Infinity/-Infinity/NaN anywhere in the payload.

    `json.dumps(..., allow_nan=False)` is the check that matters: those three
    are Python/JS extensions, not JSON, and a client parser rejects them.
    """
    json.dumps(body, allow_nan=False)


async def test_profile_returns_for_a_column_containing_1e308(client, admin_id):
    ds = await upload_csv(client, admin_id, "v\n1\n2\n3\n1e308\n")
    body = await profile(client, admin_id, ds)
    assert_json_finite(body)

    v = col(body, "v")
    assert v["dtype"] == "numeric"
    assert v["count"] == 4 and v["null_count"] == 0
    # The representable statistics still come back, exactly.
    assert v["min"] == 1.0 and v["max"] == 1e308
    assert v["median"] == 2.5
    # std overflows the sum of squares -> null, and said so.
    assert v["std"] is None
    assert "std" in v["unavailable_stats"]


async def test_profile_returns_for_a_column_containing_negative_1e308(client, admin_id):
    ds = await upload_csv(client, admin_id, "v\n-1\n-2\n-3\n-1e308\n")
    body = await profile(client, admin_id, ds)
    assert_json_finite(body)

    v = col(body, "v")
    assert v["min"] == -1e308 and v["max"] == -1.0
    assert v["std"] is None
    assert "std" in v["unavailable_stats"]


async def test_extreme_value_does_not_blind_the_other_columns_on_the_sheet(client, admin_id):
    # `ok` is an ordinary column sharing the sheet with the extreme one; its
    # statistics must be untouched, which is the whole point of the fix.
    ds = await upload_csv(client, admin_id, "big,ok\n1,10\n2,20\n3,30\n1e308,40\n")
    body = await profile(client, admin_id, ds)
    assert_json_finite(body)

    ok = col(body, "ok")
    assert ok["mean"] == 25.0
    assert ok["std"] is not None and abs(ok["std"] - 12.909944) < 1e-5
    assert ok["min"] == 10.0 and ok["max"] == 40.0
    assert ok["unavailable_stats"] == []

    big = col(body, "big")
    assert big["std"] is None and "std" in big["unavailable_stats"]
    assert big["max"] == 1e308


async def test_ordinary_column_is_unaffected(client, admin_id):
    rows = [{"amt": float(i) * 1.5, "label": f"l{i % 3}"} for i in range(1, 21)]
    ds = (await upload_inline(client, admin_id, json.dumps(rows)))["dataset_id"]
    body = await profile(client, admin_id, ds)
    assert_json_finite(body)

    amt = col(body, "amt")
    assert amt["mean"] is not None and amt["std"] is not None
    assert amt["unavailable_stats"] == []
    assert amt["histogram"] and all(
        math.isfinite(b["bin_start"]) and math.isfinite(b["bin_end"])
        for b in amt["histogram"])


async def test_histogram_bin_edges_never_overflow(client, admin_id):
    # min and max both extreme: the naive edge formula computes
    # bucket * (hi - lo) first, and 2e308 is +inf.
    ds = await upload_csv(client, admin_id, "v\n-1e308\n0\n1\n1e308\n")
    body = await profile(client, admin_id, ds)
    assert_json_finite(body)

    v = col(body, "v")
    for b in (v["histogram"] or []):
        assert math.isfinite(b["bin_start"]) and math.isfinite(b["bin_end"]), b


async def test_correlations_survive_an_extreme_column(client, admin_id):
    # CORR() is STDDEV_POP internally and raises on the same overflow; asking
    # for the matrix must not take the whole profile down with it.
    ds = await upload_csv(client, admin_id, "big,ok\n1,10\n2,20\n3,30\n1e308,40\n")
    r = await client.post("/api/v1/profile", headers=auth(admin_id),
                          json={"dataset_id": ds, "include_correlations": True})
    assert r.status_code == 200, (r.status_code, r.text)
    body = r.json()
    assert_json_finite(body)
    assert body["correlations"] is None or body["correlations"]["big"]["ok"] is None


async def test_column_explorer_survives_an_extreme_column(client, admin_id):
    # The deep-dive drawer profiles a single column through the same helper.
    ds = await upload_csv(client, admin_id, "v\n1\n2\n3\n1e308\n")
    vers = (await client.get(f"/api/v1/datasets/{ds}/versions",
                             headers=auth(admin_id))).json()
    vnum = vers["items"][0]["version_number"]
    r = await client.get(f"/api/v1/datasets/{ds}/versions/{vnum}/columns/v",
                         headers=auth(admin_id))
    assert r.status_code == 200, (r.status_code, r.text)
    body = r.json()
    assert_json_finite(body)
    assert body["std"] is None and "std" in body["unavailable_stats"]

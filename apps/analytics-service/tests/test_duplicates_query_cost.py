"""`/duplicates` must not cost one DuckDB query per group it returns.

``df`` is a lazy VIEW over the sheet's parquet (``data_io.load_data``), not a
materialized table, so every ``SELECT * FROM df WHERE ...`` re-reads the
object — over the network when storage is S3. The endpoint ran one such query
per returned group, up to ``MAX_DUPLICATE_GROUPS`` (100), inside an ``async
def`` that blocks the event loop and with no timeout anywhere on the path (the
``conn.interrupt`` watchdog in ``app/shared/duck.py`` guards only the ``/sql``
sandbox). So a caller could turn one request into ~100 full scans just by
raising ``limit``, and stall every other request while it ran.

The examples for all returned groups now come back in a single query, so the
cost is constant in ``limit``.
"""

from __future__ import annotations

import json

import pytest

from conftest import auth, upload_inline

# 12 duplicate groups of 2 rows each, so `limit` genuinely varies the fan-out.
ROWS = [{"gid": i, "note": f"n{i}"} for i in range(12) for _ in range(2)]


class _CountingConnection:
    """Delegates to a real DuckDB connection, counting ``execute`` calls."""

    def __init__(self, inner):
        self._inner = inner
        self.executes = 0

    def execute(self, *args, **kwargs):
        self.executes += 1
        return self._inner.execute(*args, **kwargs)

    def close(self):
        self._inner.close()

    def __getattr__(self, name):
        return getattr(self._inner, name)


@pytest.fixture
def duckdb_execute_counts(monkeypatch):
    from app.features.explorer import data_quality

    counts: list[_CountingConnection] = []
    real = data_quality.load_data

    def _counting(path, *args, **kwargs):
        conn = _CountingConnection(real(path, *args, **kwargs))
        counts.append(conn)
        return conn

    monkeypatch.setattr(data_quality, "load_data", _counting)
    return counts


async def test_duplicates_issues_the_same_number_of_queries_at_any_limit(
        client, admin_id, duckdb_execute_counts):
    ds = (await upload_inline(client, admin_id, json.dumps(ROWS)))["dataset_id"]
    h = auth(admin_id)
    url = f"/api/v1/datasets/{ds}/versions/1/duplicates"

    r = await client.get(url, headers=h, params={"columns": "gid", "limit": 1})
    assert r.status_code == 200, r.text
    assert len(r.json()["groups"]) == 1
    small = duckdb_execute_counts[-1].executes

    r = await client.get(url, headers=h, params={"columns": "gid", "limit": 12})
    assert r.status_code == 200, r.text
    assert len(r.json()["groups"]) == 12
    large = duckdb_execute_counts[-1].executes

    assert large == small, (
        f"{large} queries for 12 groups vs {small} for 1 — the per-group "
        "example fetch is back")
    assert large <= 5, large


async def test_the_batched_example_fetch_returns_the_same_answer(client, admin_id):
    """Constant query count must not change what the endpoint says.

    The rows are now bucketed in Python from one windowed query, so this pins
    that each group still gets its own examples and only its own.
    """
    ds = (await upload_inline(client, admin_id, json.dumps(ROWS)))["dataset_id"]
    r = await client.get(f"/api/v1/datasets/{ds}/versions/1/duplicates",
                         headers=auth(admin_id), params={"limit": 12})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["group_count"] == 12 and len(body["groups"]) == 12
    for group in body["groups"]:
        assert group["count"] == 2
        assert len(group["examples"]) == 2
        assert {e["gid"] for e in group["examples"]} == {group["key"]["gid"]}
        assert {e["note"] for e in group["examples"]} == {f"n{int(group['key']['gid'])}"}


async def test_a_null_group_key_still_matches_its_own_rows(client, admin_id):
    """NULL keys are matched with ``IS NOT DISTINCT FROM``, and NULL and NaN
    must not be bucketed together when the results are grouped in Python."""
    rows = [{"gid": None, "note": "a"}, {"gid": None, "note": "b"},
            {"gid": 1, "note": "c"}, {"gid": 1, "note": "d"}]
    ds = (await upload_inline(client, admin_id, json.dumps(rows)))["dataset_id"]
    r = await client.get(f"/api/v1/datasets/{ds}/versions/1/duplicates",
                         headers=auth(admin_id), params={"columns": "gid"})
    assert r.status_code == 200, r.text
    groups = {("null" if g["key"]["gid"] is None else "one"): g
              for g in r.json()["groups"]}
    assert set(groups) == {"null", "one"}
    assert {e["note"] for e in groups["null"]["examples"]} == {"a", "b"}
    assert {e["note"] for e in groups["one"]["examples"]} == {"c", "d"}

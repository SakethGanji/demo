"""Every "that column does not exist" answer from /aggregate is `unknown-column`.

``_assemble_sql`` already published the shared contract for ``sort_by``
(``code="unknown-column"`` plus ``columns`` and ``available``), and
``app/shared/filters.py`` documents that shape as "the same shape as
/aggregate". But the three checks in ``run_aggregation`` itself — group_by,
``aggregations[].column`` and the filter pre-scan — raised bare
``HTTPException``s. The problem+json envelope renders those as the generic
``code: "bad_request"`` with no ``columns`` and no ``available``.

What breaks in production without this test:

* an aggregate builder cannot tell "you picked a dead column" from any other
  400, so it cannot highlight the offending picker or repopulate it from
  ``available`` — it can only print prose;
* ``app/features/mcp/tools/_common.py`` branches on
  ``code == "unknown-column"`` to re-offer the real column list, so the MCP
  guidance path degraded to a bare error for exactly the mistake an LLM makes
  most (naming a column that was dropped in a later version).

A bad aggregation *function* is a different mistake — a different control on
the screen — so it gets its own slug rather than being folded into this one.
"""

from __future__ import annotations

import json

from conftest import auth, create_team_user, upload_inline

PROBLEM = "application/problem+json"

ROWS = [
    {"order_id": 1, "region": "EU", "amount": 100.0},
    {"order_id": 2, "region": "US", "amount": 50.0},
]

AGGS = [{"column": "amount", "function": "sum", "alias": "amt"}]


async def _dataset(client, admin_id):
    editor, team = await create_team_user(client, admin_id, "editor")
    ds = (await upload_inline(client, editor, json.dumps(ROWS),
                              team_id=team))["dataset_id"]
    return ds, auth(editor)


async def test_an_unknown_group_by_column_answers_unknown_column_with_the_available_list(
    client, admin_id,
):
    """The group-by picker's own error. Without the slug and the ``available``
    list the screen cannot re-render its column menu from the failure."""
    ds, h = await _dataset(client, admin_id)

    r = await client.post("/api/v1/aggregate", headers=h, json={
        "dataset_id": ds, "group_by": ["ghost"], "aggregations": AGGS})

    assert r.status_code == 400
    assert r.headers["content-type"].startswith(PROBLEM)
    body = r.json()
    assert body["code"] == "unknown-column", body
    assert body["columns"] == ["ghost"]
    assert set(body["available"]) >= {"order_id", "region", "amount"}
    assert body["available"] == sorted(body["available"])
    assert "ghost" in body["detail"]


async def test_a_group_by_reports_every_missing_column_not_just_the_first(
    client, admin_id,
):
    """A builder with several dead columns must be able to mark them all in one
    pass; reporting one at a time makes the user re-submit N times."""
    ds, h = await _dataset(client, admin_id)

    r = await client.post("/api/v1/aggregate", headers=h, json={
        "dataset_id": ds, "group_by": ["region", "ghost", "phantom"],
        "aggregations": AGGS})

    assert r.status_code == 400
    assert r.json()["code"] == "unknown-column"
    assert r.json()["columns"] == ["ghost", "phantom"]


async def test_an_unknown_aggregation_column_answers_unknown_column_too(
    client, admin_id,
):
    """The measure picker fails the same way the group-by picker does — one
    error renderer has to cover the whole builder."""
    ds, h = await _dataset(client, admin_id)

    r = await client.post("/api/v1/aggregate", headers=h, json={
        "dataset_id": ds, "group_by": ["region"],
        "aggregations": [{"column": "ghost", "function": "sum", "alias": "m"}]})

    assert r.status_code == 400
    body = r.json()
    assert body["code"] == "unknown-column", body
    assert body["columns"] == ["ghost"]
    assert "amount" in body["available"]


async def test_an_unknown_filter_column_answers_unknown_column_too(client, admin_id):
    """The filter rail is the third way to name a column on this endpoint, and
    it was the third bare 400."""
    ds, h = await _dataset(client, admin_id)

    r = await client.post("/api/v1/aggregate", headers=h, json={
        "dataset_id": ds, "group_by": ["region"], "aggregations": AGGS,
        "filters": {"logic": "and", "conditions": [
            {"column": "ghost", "op": "eq", "value": "EU"}]}})

    assert r.status_code == 400
    body = r.json()
    assert body["code"] == "unknown-column", body
    assert body["columns"] == ["ghost"]
    assert "region" in body["available"]


async def test_an_unknown_column_in_a_per_measure_filter_answers_unknown_column_too(
    client, admin_id,
):
    """A conditional measure ("sum of amount where region = EU") names columns
    in its own filter — the fourth place on this endpoint a dead column can be
    typed, and it must fail like the other three."""
    ds, h = await _dataset(client, admin_id)

    r = await client.post("/api/v1/aggregate", headers=h, json={
        "dataset_id": ds, "group_by": ["region"],
        "aggregations": [{"column": "amount", "function": "sum", "alias": "eu",
                          "filter": {"logic": "and", "conditions": [
                              {"column": "ghost", "op": "eq", "value": "EU"}]}}]})

    assert r.status_code == 400
    body = r.json()
    assert body["code"] == "unknown-column", body
    assert body["columns"] == ["ghost"]
    assert "region" in body["available"]


async def test_an_unknown_aggregation_function_gets_its_own_slug_not_unknown_column(
    client, admin_id,
):
    """A bad function is a bad *dropdown value*, not a bad column. Sharing the
    column slug would make a UI highlight the wrong control and try to fix it
    by offering column names."""
    ds, h = await _dataset(client, admin_id)

    r = await client.post("/api/v1/aggregate", headers=h, json={
        "dataset_id": ds, "group_by": ["region"],
        "aggregations": [{"column": "amount", "function": "wat", "alias": "m"}]})

    assert r.status_code == 400
    body = r.json()
    assert body["code"] == "unknown-aggregation-function", body
    assert "sum" in body["available"]
    assert "columns" not in body

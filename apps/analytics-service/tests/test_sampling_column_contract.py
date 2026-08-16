"""POST /sample must reject unknown column references, like its siblings do.

Every column name in a sample request is eventually interpolated into SQL or
handed to pandas. Before this file existed, ``/sample`` had two different bad
answers for a name that doesn't exist:

* ``sort_by`` was *silently dropped*. The rows came back in pipeline order, the
  response said ``success: true``, and ``reproducibility.post_processing``
  echoed the sort that never happened — so a caller replaying that metadata
  would reproduce a different ordering and never learn why. That is the worst
  class of bug this service can ship: a confident wrong answer.
* every other column (``distribution_goals.column``, a step's
  ``stratify_column``/``cluster_column``/``weight_column``/``time_column``)
  reached DuckDB unchecked and raised a BinderException, which the unhandled
  handler renders as a 500 "An unexpected error occurred." — no field, no
  column list, nothing a UI can highlight.

``/aggregate`` and ``/pivot`` both publish an ``unknown-column`` 400 for the
same mistake. These tests pin that sampling now does too.
"""

from __future__ import annotations

from conftest import auth, upload_inline

ROWS = ('[{"a": 1, "grp": "x", "w": 1.0}, '
        ' {"a": 2, "grp": "y", "w": 2.0}, '
        ' {"a": 3, "grp": "x", "w": 3.0}]')


async def _dataset(client, admin_id):
    return (await upload_inline(client, admin_id, ROWS))["dataset_id"]


async def test_sample_rejects_an_unknown_sort_by_instead_of_returning_pipeline_order(
        client, admin_id):
    ds = await _dataset(client, admin_id)
    r = await client.post("/api/v1/sample", headers=auth(admin_id), json={
        "dataset_id": ds, "target_total_volume": 3,
        "sampling_steps": [{"method": "random", "sample_size": 3}],
        "sort_by": "ghost"})
    assert r.status_code == 400, r.text
    body = r.json()
    assert body["code"] == "unknown-column"
    assert body["columns"] == ["ghost"]
    assert body["fields"] == ["sort_by"]
    assert body["available"] == ["a", "grp", "w"]
    assert "ghost" in body["detail"]


async def test_sample_actually_applies_a_valid_sort_by(client, admin_id):
    """The rejection above must not be the only reason a sort "works".

    Tightening the guard is only correct if the accepted path still sorts, so
    this pins the ordering the response claims in ``post_processing``.
    """
    ds = await _dataset(client, admin_id)
    r = await client.post("/api/v1/sample", headers=auth(admin_id), json={
        "dataset_id": ds, "target_total_volume": 3,
        "sampling_steps": [{"method": "random", "sample_size": 3}],
        "sort_by": "a", "sort_descending": True})
    assert r.status_code == 200, r.text
    body = r.json()
    assert [row["a"] for row in body["data"]] == [3, 2, 1]
    assert body["reproducibility"]["post_processing"]["sort_by"] == "a"


async def test_sample_rejects_an_unknown_distribution_goal_column_with_the_available_list(
        client, admin_id):
    ds = await _dataset(client, admin_id)
    r = await client.post("/api/v1/sample", headers=auth(admin_id), json={
        "dataset_id": ds, "target_total_volume": 2,
        "sampling_steps": [{"method": "random", "sample_size": 2}],
        "distribution_goals": {"column": "ghost", "class_minimums": {"x": 1}}})
    assert r.status_code == 400, r.text
    body = r.json()
    assert body["code"] == "unknown-column"
    assert body["fields"] == ["distribution_goals.column"]
    assert body["available"] == ["a", "grp", "w"]


async def test_sample_rejects_unknown_step_columns_naming_the_step_that_owns_them(
        client, admin_id):
    """A multi-step pipeline needs to say *which* step is wrong.

    "Column not found: ghost" is not enough to fix a five-step request body.
    """
    ds = await _dataset(client, admin_id)
    h = auth(admin_id)
    for step, field in (
        ({"method": "stratified", "sample_size": 2, "stratify_column": "ghost"},
         "stratify_column"),
        ({"method": "cluster", "cluster_column": "ghost"}, "cluster_column"),
        ({"method": "weighted", "sample_size": 2, "weight_column": "ghost"},
         "weight_column"),
        ({"method": "time_stratified", "sample_size": 2, "time_column": "ghost"},
         "time_column"),
    ):
        r = await client.post("/api/v1/sample", headers=h, json={
            "dataset_id": ds, "target_total_volume": 2,
            "sampling_steps": [{"method": "random", "sample_size": 1}, step]})
        assert r.status_code == 400, (step, r.text)
        body = r.json()
        assert body["code"] == "unknown-column", step
        assert body["fields"] == [f"sampling_steps[1].{field}"], step


async def test_sample_rejects_unknown_deduplicate_columns_rather_than_raising_a_key_error(
        client, admin_id):
    """``drop_duplicates(subset=[...])`` raises KeyError, i.e. a bare 500."""
    ds = await _dataset(client, admin_id)
    r = await client.post("/api/v1/sample", headers=auth(admin_id), json={
        "dataset_id": ds, "target_total_volume": 3,
        "sampling_steps": [{"method": "random", "sample_size": 3}],
        "deduplicate": True, "deduplicate_columns": ["ghost"]})
    assert r.status_code == 400, r.text
    assert r.json()["code"] == "unknown-column"
    assert r.json()["fields"] == ["deduplicate_columns"]

"""The bounded sampling/profiling counts must surface as 422s, not 500s.

``tests/unit/test_sampling_request_bounds.py`` pins the pydantic constraints
themselves. This file pins the half a UI actually sees: that a non-positive
``time_bins``/``num_clusters``/``top_n`` is refused by the request layer with
the offending field named in the problem+json ``errors`` array, instead of
reaching DuckDB and coming back as an opaque 500.
"""

from __future__ import annotations

from conftest import auth, upload_inline


def _fields(body):
    return {".".join(str(p) for p in e["loc"]) for e in body["errors"]}


async def test_sample_rejects_non_positive_bin_and_cluster_counts_at_the_request_layer(
        client, admin_id):
    ds = (await upload_inline(
        client, admin_id,
        '[{"ts": "2024-01-01", "region": "a"}, {"ts": "2024-02-01", "region": "b"}]',
    ))["dataset_id"]
    h = auth(admin_id)

    for step, field in (
        ({"method": "time_stratified", "time_column": "ts", "time_bins": 0}, "time_bins"),
        ({"method": "time_stratified", "time_column": "ts", "time_bins": -3}, "time_bins"),
        ({"method": "cluster", "cluster_column": "region", "num_clusters": 0}, "num_clusters"),
        ({"method": "cluster", "cluster_column": "region", "num_clusters": -1}, "num_clusters"),
    ):
        r = await client.post("/api/v1/sample", headers=h, json={
            "dataset_id": ds, "target_total_volume": 2, "sampling_steps": [step]})
        assert r.status_code == 422, (step, r.text)
        assert any(f.endswith(field) for f in _fields(r.json())), (step, r.text)


async def test_profile_rejects_a_negative_top_n_at_the_request_layer(client, admin_id):
    ds = (await upload_inline(client, admin_id, '[{"a": 1}, {"a": 2}]'))["dataset_id"]
    r = await client.post("/api/v1/profile", headers=auth(admin_id), json={
        "dataset_id": ds, "top_n": -1})
    assert r.status_code == 422, r.text
    assert any(f.endswith("top_n") for f in _fields(r.json())), r.text

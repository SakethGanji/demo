"""Integration — derived artifacts land in the partitioned layout and get collected.

Runs against both storage backends (local FS and S3/MinIO), because the whole
point of the layout is that it is expressible as bucket prefixes, and the two
backends disagree about almost everything else (real directories vs key
prefixes, mtime semantics, delete-prefix cost).

What is actually being pinned down:

* an output written by a service is reachable by the key its ownership row
  records — the writer and the registrar must agree without communicating;
* the key is partitioned by team, dataset, and kind, so a bucket policy or a
  lifecycle rule can address exactly one of those;
* deleting a dataset drops the whole prefix, including blobs whose row never
  landed;
* the listing comes from Postgres, so it shows what a caller can actually open
  and nothing else.
"""

from __future__ import annotations

from conftest import SAMPLE_CSV, auth, create_team_user, upload_file, upload_inline

from app.infra.db.storage import ARTIFACT_ROOT, ArtifactLayout, get_storage


async def _sample(client, user, ds):
    r = await client.post("/api/v1/sample", headers=auth(user), json={
        "dataset_id": ds, "target_total_volume": 2,
        "sampling_steps": [{"method": "random", "sample_size": 2}], "seed": 3})
    assert r.status_code == 200, r.text
    return r.json()["sample_file"]


# ---------------------------------------------------------------------------
# Layout
# ---------------------------------------------------------------------------


async def test_output_lands_under_team_dataset_kind(client, admin_id):
    editor, team = await create_team_user(client, admin_id, "editor")
    ds = (await upload_file(client, editor, SAMPLE_CSV, name="s.csv",
                            team_id=team))["dataset_id"]
    fname = await _sample(client, editor, ds)

    expected = ArtifactLayout("sample_output", team_id=team,
                              dataset_id=ds).key(fname)
    assert get_storage().exists(expected), expected
    # Every segment is present and in order — this is what a prefix policy
    # or lifecycle rule is written against.
    assert expected.split("/")[:4] == [ARTIFACT_ROOT, team, ds, "sample_output"]


async def test_registered_key_matches_where_the_writer_put_it(client, admin_id):
    """The writer and the ownership registration derive the key separately.

    If they ever drift the artifact is silently unreachable — the download
    resolves through the row, so it would 404 while the bytes sit in the
    bucket. Downloading is the end-to-end check that they agree.
    """
    editor, team = await create_team_user(client, admin_id, "editor")
    ds = (await upload_file(client, editor, SAMPLE_CSV, name="s.csv",
                            team_id=team))["dataset_id"]
    fname = await _sample(client, editor, ds)

    r = await client.get(f"/api/v1/samples/{fname}", headers=auth(editor))
    assert r.status_code == 200, r.text
    assert r.content


async def test_different_kinds_get_different_prefixes(client, admin_id):
    """Retention differs by kind, so the kinds must be separable in the bucket."""
    editor, team = await create_team_user(client, admin_id, "editor")
    ds = (await upload_file(client, editor, SAMPLE_CSV, name="s.csv",
                            team_id=team))["dataset_id"]
    sample_file = await _sample(client, editor, ds)

    r = await client.post(f"/api/v1/datasets/{ds}/versions/1/sql",
                          headers=auth(editor), json={"sql": "SELECT 1 AS n"})
    assert r.status_code == 200, r.text
    query_file = r.json()["result_file"]

    sample_key = ArtifactLayout("sample_output", team_id=team,
                                dataset_id=ds).key(sample_file)
    query_key = ArtifactLayout("query_output", team_id=team,
                               dataset_id=ds).key(query_file)
    assert get_storage().exists(sample_key)
    assert get_storage().exists(query_key)
    assert sample_key.rsplit("/", 1)[0] != query_key.rsplit("/", 1)[0]


async def test_export_inherits_the_source_prefix(client, admin_id):
    """An export swept by the source's dataset rule, not stranded elsewhere."""
    editor, team = await create_team_user(client, admin_id, "editor")
    ds = (await upload_file(client, editor, SAMPLE_CSV, name="s.csv",
                            team_id=team))["dataset_id"]
    fname = await _sample(client, editor, ds)

    r = await client.post(f"/api/v1/samples/{fname}/export?format=csv",
                          headers=auth(editor))
    assert r.status_code == 200, r.text
    export_file = r.json()["export_file"]

    key = ArtifactLayout("export", team_id=team, dataset_id=ds).key(export_file)
    assert get_storage().exists(key)
    prefix = ArtifactLayout("export", team_id=team, dataset_id=ds).dataset_prefix()
    assert key.startswith(prefix + "/")

    # And it is downloadable through the row it inherited.
    r = await client.get(f"/api/v1/samples/{export_file}", headers=auth(editor))
    assert r.status_code == 200


async def test_ownerless_output_uses_the_shared_prefix(client, admin_id):
    """Inline data has no dataset; the blob still lands somewhere sweepable."""
    r = await client.post("/api/v1/sample", headers=auth(admin_id), json={
        "data": [{"a": 1}, {"a": 2}, {"a": 3}],
        "target_total_volume": 2,
        "sampling_steps": [{"method": "random", "sample_size": 2}]})
    assert r.status_code == 200, r.text
    fname = r.json()["sample_file"]

    keys = [k for k in get_storage().list_keys(ARTIFACT_ROOT) if k.endswith(fname)]
    assert len(keys) == 1, keys
    assert keys[0].startswith(ARTIFACT_ROOT + "/")


# ---------------------------------------------------------------------------
# Listing
# ---------------------------------------------------------------------------


async def test_listing_reports_the_real_kind_and_size(client, admin_id):
    ds = (await upload_inline(
        client, admin_id, '[{"a": 1}, {"a": 2}, {"a": 3}]'))["dataset_id"]
    fname = await _sample(client, admin_id, ds)

    listing = (await client.get("/api/v1/samples", headers=auth(admin_id))).json()
    mine = next(e for e in listing["items"] if e["filename"] == fname)
    assert mine["file_type"] == "sample_output"
    assert mine["size_bytes"] > 0
    assert mine["dataset_id"] == ds


async def test_listing_only_shows_files_the_caller_can_open(client, admin_id):
    """The listing and the download must agree — it reads the same rows."""
    editor, team = await create_team_user(client, admin_id, "editor")
    ds = (await upload_file(client, editor, SAMPLE_CSV, name="s.csv",
                            team_id=team))["dataset_id"]
    fname = await _sample(client, editor, ds)

    other, _ = await create_team_user(client, admin_id, "editor")
    listing = (await client.get("/api/v1/samples", headers=auth(other))).json()
    assert all(e["filename"] != fname for e in listing["items"])
    r = await client.get(f"/api/v1/samples/{fname}", headers=auth(other))
    assert r.status_code == 404

    listing = (await client.get("/api/v1/samples", headers=auth(editor))).json()
    assert any(e["filename"] == fname for e in listing["items"])


async def test_listing_paginates(client, admin_id):
    ds = (await upload_inline(
        client, admin_id, '[{"a": 1}, {"a": 2}, {"a": 3}]'))["dataset_id"]
    for _ in range(3):
        await _sample(client, admin_id, ds)

    page = (await client.get("/api/v1/samples?limit=2&offset=0",
                             headers=auth(admin_id))).json()
    assert len(page["items"]) == 2
    assert page["total"] >= 3 and page["limit"] == 2


# ---------------------------------------------------------------------------
# Deletion
# ---------------------------------------------------------------------------


async def test_dataset_delete_drops_the_whole_prefix(client, admin_id):
    """Including a blob whose ownership row never landed.

    The per-key loop this replaced could only reach registered artifacts, so a
    crash between writing and registering leaked bytes permanently.
    """
    editor, team = await create_team_user(client, admin_id, "editor")
    ds = (await upload_file(client, editor, SAMPLE_CSV, name="s.csv",
                            team_id=team))["dataset_id"]
    fname = await _sample(client, editor, ds)

    storage = get_storage()
    layout = ArtifactLayout("sample_output", team_id=team, dataset_id=ds)
    stray = ArtifactLayout("query_output", team_id=team,
                           dataset_id=ds).key("unregistered.parquet")
    storage.write_bytes(stray, b"PAR1strayPAR1")

    assert storage.exists(layout.key(fname)) and storage.exists(stray)

    r = await client.delete(f"/api/v1/datasets/{ds}", headers=auth(admin_id))
    assert r.status_code == 200, r.text

    assert not storage.exists(layout.key(fname))
    assert not storage.exists(stray)
    assert not storage.list_keys(layout.dataset_prefix())


# ---------------------------------------------------------------------------
# Backend size listing
# ---------------------------------------------------------------------------


async def test_list_sizes_agrees_with_per_key_size(client, admin_id):
    """`list_sizes` must report exactly what `size()` would, per key.

    It exists because summing a prefix by calling `size()` per key costs one
    HEAD request per object on S3 — that turned /storage/usage into a 31-second
    call on a bucket with 24k objects. Reading the size from the listing is
    only safe if the two agree, so this pins them together.
    """
    ds = (await upload_inline(
        client, admin_id, '[{"a": 1}, {"a": 2}, {"a": 3}]'))["dataset_id"]
    await _sample(client, admin_id, ds)

    storage = get_storage()
    for prefix in ("datasets", ARTIFACT_ROOT):
        listed = storage.list_sizes(prefix)
        assert listed, f"expected objects under {prefix}"
        assert [k for k, _ in listed] == storage.list_keys(prefix)
        for key, size in listed:
            assert size == storage.size(key), key


async def test_list_sizes_on_a_missing_prefix_is_empty(client, admin_id):
    assert get_storage().list_sizes("no/such/prefix") == []

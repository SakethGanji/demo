"""Sample-artifact authorization — /samples/{filename} is no longer a capability URL.

Persisted sample/aggregation outputs are registered in ``artifacts`` with
dataset/team ownership at creation time; downloads and listings authorize
against those rows. Cross-team access 404s (existence hidden, matching the
service-wide discipline).

Since the storage layout moved under ``artifacts/{team}/{dataset}/{kind}/``,
that row is also the only way to *reach* the object at all: the key carries
segments a bare filename does not. Authorization and resolution became the
same lookup, so a blob with no row is unreachable by everyone.
"""

from __future__ import annotations

from conftest import SAMPLE_CSV, auth, create_team_user, upload_file

PROBLEM = "application/problem+json"


async def _team_dataset_and_sample(client, admin_id):
    """An editor in a fresh team uploads a CSV and samples it.

    Returns (editor_id, team_id, dataset_id, sample_filename).
    """
    editor, team = await create_team_user(client, admin_id, "editor")
    ds = (await upload_file(client, editor, SAMPLE_CSV,
                            name="sample.csv", team_id=team))["dataset_id"]
    r = await client.post("/api/v1/sample", headers=auth(editor), json={
        "dataset_id": ds,
        "target_total_volume": 2,
        "sampling_steps": [{"method": "random", "sample_size": 2}],
        "seed": 7,
    })
    assert r.status_code == 200, r.text
    fname = r.json()["sample_file"]
    assert fname
    return editor, team, ds, fname


async def test_owner_team_can_download_others_get_404(client, admin_id):
    editor, team, _ds, fname = await _team_dataset_and_sample(client, admin_id)

    # Owner (creator) reads both endpoints.
    r = await client.get(f"/api/v1/samples/{fname}", headers=auth(editor))
    assert r.status_code == 200
    r = await client.get(f"/api/v1/samples/{fname}/data", headers=auth(editor))
    assert r.status_code == 200 and r.json().get("data") is not None

    # A viewer in the SAME team can read (dataset:read).
    viewer, _ = await create_team_user(client, admin_id, "viewer", team_id=team)
    r = await client.get(f"/api/v1/samples/{fname}", headers=auth(viewer))
    assert r.status_code == 200

    # Any user in ANOTHER team gets 404 — existence hidden, not 403.
    outsider, _ = await create_team_user(client, admin_id, "editor")
    for path in (f"/api/v1/samples/{fname}", f"/api/v1/samples/{fname}/data"):
        r = await client.get(path, headers=auth(outsider))
        assert r.status_code == 404, r.text
        assert r.headers["content-type"].startswith(PROBLEM)

    # Superuser bypass.
    r = await client.get(f"/api/v1/samples/{fname}", headers=auth(admin_id))
    assert r.status_code == 200


async def test_listing_is_team_scoped(client, admin_id):
    _editor, _team, _ds, fname = await _team_dataset_and_sample(client, admin_id)

    outsider, _ = await create_team_user(client, admin_id, "editor")
    listed = (await client.get("/api/v1/samples", headers=auth(outsider))).json()
    assert fname not in {e["filename"] for e in listed["items"]}

    # Superuser still sees everything.
    listed = (await client.get("/api/v1/samples", headers=auth(admin_id))).json()
    assert fname in {e["filename"] for e in listed["items"]}


async def test_aggregation_output_is_scoped_too(client, admin_id):
    editor, _team, ds, _f = await _team_dataset_and_sample(client, admin_id)
    r = await client.post("/api/v1/aggregate", headers=auth(editor), json={
        "dataset_id": ds,
        "group_by": ["gender"],
        "aggregations": [{"column": "conversation_id", "function": "count", "alias": "n"}],
    })
    assert r.status_code == 200, r.text
    result_file = r.json()["result_file"]
    assert result_file

    outsider, _ = await create_team_user(client, admin_id, "viewer")
    r = await client.get(f"/api/v1/samples/{result_file}", headers=auth(outsider))
    assert r.status_code == 404
    r = await client.get(f"/api/v1/samples/{result_file}", headers=auth(editor))
    assert r.status_code == 200


async def test_unregistered_file_is_unreachable(client, admin_id):
    """A blob with no artifact row cannot be fetched by anyone — not even a
    superuser.

    The storage key carries team/dataset/kind segments a bare filename does
    not, so the artifact row is the only thing that can produce a key. That
    makes an unregistered blob unaddressable rather than merely unauthorized,
    which is why the retention sweep exists to reclaim it.
    """
    from pathlib import Path
    import tempfile

    from app.infra.db.storage import ArtifactLayout, get_storage

    with tempfile.NamedTemporaryFile(suffix=".parquet", delete=False) as f:
        f.write(b"PAR1legacybytesPAR1")
        tmp = Path(f.name)
    fname = "legacy_orphan_test.parquet"
    key = ArtifactLayout("sample_output").key(fname)
    get_storage().put_file(key, tmp)
    tmp.unlink()

    user, _ = await create_team_user(client, admin_id, "editor")
    for who in (user, admin_id):
        r = await client.get(f"/api/v1/samples/{fname}", headers=auth(who))
        assert r.status_code == 404, who

    get_storage().delete(key)

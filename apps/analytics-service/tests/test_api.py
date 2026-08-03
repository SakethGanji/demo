"""Integration tests covering the core invariants of each phase.

Run: ``venv/bin/python -m pytest`` (Postgres up + migrations applied).
"""

from __future__ import annotations

from conftest import DEFAULT_TEAM_ID, SAMPLE_CSV, auth, rid

PROBLEM = "application/problem+json"


# --- Phase 1: clean API conventions -----------------------------------------

async def test_health(client):
    r = await client.get("/health")
    assert r.status_code == 200 and r.json()["status"] == "healthy"


async def test_unauthenticated_is_401_problem_json(client):
    r = await client.get("/api/v1/datasets")
    assert r.status_code == 401
    assert r.headers["content-type"].startswith(PROBLEM)
    body = r.json()
    assert body["status"] == 401 and body["code"] == "unauthorized"


async def test_unknown_user_id_is_401(client):
    r = await client.get("/api/v1/datasets", headers=auth("22222222-2222-2222-2222-222222222222"))
    assert r.status_code == 401


async def test_legacy_paths_gone(client):
    assert (await client.get("/datasets")).status_code == 404
    assert (await client.post("/prompt-lab/datasets")).status_code == 404


async def test_page_envelope(client, admin_id):
    r = await client.get("/api/v1/datasets", headers=auth(admin_id))
    assert r.status_code == 200
    body = r.json()
    assert set(["items", "total", "limit", "offset"]).issubset(body.keys())


async def test_malformed_id_is_404_not_500(client, admin_id):
    for path in ["/api/v1/datasets/not-a-uuid",
                 "/api/v1/datasets/11111111-1111-1111-1111-111111111111"]:
        r = await client.get(path, headers=auth(admin_id))
        assert r.status_code == 404, (path, r.status_code)
        assert r.headers["content-type"].startswith(PROBLEM)


async def test_sample_random_does_not_crash(client, admin_id, admin_dataset):
    r = await client.post(
        "/api/v1/sample",
        headers=auth(admin_id),
        json={"dataset_id": admin_dataset, "target_total_volume": 5,
              "sampling_steps": [{"method": "random", "sample_size": 5}]},
    )
    assert r.status_code == 200, r.text
    assert r.json()["sampled_count"] <= 5


# --- Phase 1/3: dataset lifecycle -------------------------------------------

async def test_upload_surfaces_version_id_and_versions(client, admin_id, admin_dataset):
    # versions is a Page and has one ready version
    r = await client.get(f"/api/v1/datasets/{admin_dataset}/versions", headers=auth(admin_id))
    assert r.status_code == 200
    body = r.json()
    assert body["total"] >= 1 and body["items"][0]["status"] == "ready"


async def test_tag_lifecycle(client, admin_id, admin_dataset):
    h = auth(admin_id)
    r = await client.put(f"/api/v1/datasets/{admin_dataset}/tags", headers=h,
                         json={"tag_name": "production", "version_number": 1})
    assert r.status_code == 200
    assert (await client.get(f"/api/v1/datasets/{admin_dataset}/tags/production", headers=h)).status_code == 200
    assert (await client.delete(f"/api/v1/datasets/{admin_dataset}/tags/production", headers=h)).status_code == 200
    assert (await client.get(f"/api/v1/datasets/{admin_dataset}/tags/production", headers=h)).status_code == 404


async def test_classification_default_patch_and_validation(client, admin_id, admin_dataset):
    h = auth(admin_id)
    r = await client.patch(f"/api/v1/datasets/{admin_dataset}", headers=h,
                          json={"classification": "restricted"})
    assert r.status_code == 200 and r.json()["classification"] == "restricted"
    bad = await client.patch(f"/api/v1/datasets/{admin_dataset}", headers=h,
                            json={"classification": "top-secret"})
    assert bad.status_code == 422


async def test_storage_lifecycle_download_and_delete(client, admin_id, admin_dataset):
    """Exercise the storage backend end-to-end: stream, convert, delete.

    Runs against whichever backend is configured (local FS or S3/MinIO).
    """
    h = auth(admin_id)
    # Direct parquet streaming (stored_file_response over the backend)
    r = await client.get(f"/api/v1/datasets/{admin_dataset}/versions/1/download",
                         params={"format": "parquet"}, headers=h)
    assert r.status_code == 200 and len(r.content) > 0
    assert r.content[:4] == b"PAR1"  # parquet magic
    # CSV conversion — DuckDB reads the stored parquet (s3:// via httpfs on S3)
    r = await client.get(f"/api/v1/datasets/{admin_dataset}/download",
                         params={"format": "csv", "limit": 5}, headers=h)
    assert r.status_code == 200 and r.content
    # Delete removes DB rows and stored artifacts (delete_prefix per version)
    r = await client.delete(f"/api/v1/datasets/{admin_dataset}", headers=h)
    assert r.status_code == 200 and r.json()["success"]
    assert (await client.get(f"/api/v1/datasets/{admin_dataset}", headers=h)).status_code == 404


async def test_sample_file_persisted_and_readable(client, admin_id, admin_dataset):
    """A sampling run persists its output to storage and it can be read back."""
    h = auth(admin_id)
    r = await client.post(
        "/api/v1/sample", headers=h,
        json={"dataset_id": admin_dataset, "target_total_volume": 3,
              "sampling_steps": [{"method": "random", "sample_size": 3}]},
    )
    assert r.status_code == 200, r.text
    fname = r.json()["sample_file"]
    assert fname
    r2 = await client.get(f"/api/v1/samples/{fname}/data", headers=h)
    assert r2.status_code == 200 and r2.json()["total_count"] >= 0
    r3 = await client.get(f"/api/v1/samples/{fname}", headers=h)
    assert r3.status_code == 200 and len(r3.content) > 0


# --- Phase 2: identity + RBAC + isolation ------------------------------------

async def _make_user_in_new_team(client, admin_id, role: str):
    """Create a fresh team + user with the given role; return (user_id, team_id)."""
    h = auth(admin_id)
    team = (await client.post("/api/v1/teams", headers=h, json={"name": f"t-{rid()}"})).json()
    u = await client.post("/api/v1/auth/users", headers=h,
                          json={"email": f"u-{rid()}@bank.com", "name": "U", "team_id": team["id"]})
    assert u.status_code == 201, u.text
    uid = u.json()["id"]
    if role != "viewer":
        r = await client.patch(f"/api/v1/teams/{team['id']}/members/{uid}", headers=h, json={"role": role})
        assert r.status_code == 200, r.text
    return uid, team["id"]


async def test_rbac_viewer_is_read_only(client, admin_id):
    uid, team_id = await _make_user_in_new_team(client, admin_id, "viewer")
    # admin uploads a dataset into the viewer's team
    with open(SAMPLE_CSV, "rb") as f:
        up = await client.post("/api/v1/upload",
                               headers={**auth(admin_id), "X-Team-Id": team_id},
                               files={"file": ("s.csv", f, "text/csv")})
    ds = up.json()["dataset_id"]
    h = auth(uid)
    assert (await client.get(f"/api/v1/datasets/{ds}", headers=h)).status_code == 200      # read ok
    assert (await client.patch(f"/api/v1/datasets/{ds}", headers=h,
                               json={"description": "x"})).status_code == 403               # write denied
    assert (await client.delete(f"/api/v1/datasets/{ds}", headers=h)).status_code == 403    # delete denied


async def test_rbac_team_isolation_hides_existence(client, admin_id):
    # Two independent teams/users; each must not see the other's dataset (404, not 403).
    uid_a, team_a = await _make_user_in_new_team(client, admin_id, "editor")
    uid_b, team_b = await _make_user_in_new_team(client, admin_id, "editor")
    with open(SAMPLE_CSV, "rb") as f:
        ds_b = (await client.post("/api/v1/upload",
                                  headers={**auth(admin_id), "X-Team-Id": team_b},
                                  files={"file": ("s.csv", f, "text/csv")})).json()["dataset_id"]
    # user A cannot see team B's dataset
    r = await client.get(f"/api/v1/datasets/{ds_b}", headers=auth(uid_a))
    assert r.status_code == 404
    # and it does not appear in A's listing
    listing = (await client.get("/api/v1/datasets", headers=auth(uid_a))).json()
    assert all(d["id"] != ds_b for d in listing["items"])


async def test_file_path_source_requires_superuser(client, admin_id):
    """Raw file_path reads bypass team scoping, so only superusers may use them."""
    uid, _ = await _make_user_in_new_team(client, admin_id, "editor")
    r = await client.post(
        "/api/v1/sample",
        headers=auth(uid),
        json={"file_path": "/etc/hostname", "target_total_volume": 1,
              "sampling_steps": [{"method": "random", "sample_size": 1}]},
    )
    assert r.status_code == 403


async def test_team_admin_cannot_grant_owner(client, admin_id):
    """A team admin must not assign a role above their own (no self-escalation)."""
    h = auth(admin_id)
    team = (await client.post("/api/v1/teams", headers=h, json={"name": f"t-{rid()}"})).json()
    tid = team["id"]
    ua = (await client.post("/api/v1/auth/users", headers=h,
                            json={"email": f"a-{rid()}@bank.com", "name": "A", "team_id": tid})).json()["id"]
    ub = (await client.post("/api/v1/auth/users", headers=h,
                            json={"email": f"b-{rid()}@bank.com", "name": "B", "team_id": tid})).json()["id"]
    assert (await client.patch(f"/api/v1/teams/{tid}/members/{ua}", headers=h,
                               json={"role": "admin"})).status_code == 200
    # team admin A tries to promote B (or self) to owner → 403
    r = await client.patch(f"/api/v1/teams/{tid}/members/{ub}", headers=auth(ua), json={"role": "owner"})
    assert r.status_code == 403
    r = await client.patch(f"/api/v1/teams/{tid}/members/{ua}", headers=auth(ua), json={"role": "owner"})
    assert r.status_code == 403


async def test_me_reflects_membership(client, admin_id):
    uid, team_id = await _make_user_in_new_team(client, admin_id, "viewer")
    r = await client.get("/api/v1/auth/me", headers=auth(uid))
    assert r.status_code == 200
    body = r.json()
    assert body["user"]["id"] == uid
    assert any(m["team_id"] == team_id and m["role"] == "viewer" for m in body["memberships"])


# --- Phase 3: hardening ------------------------------------------------------

async def test_security_headers_present(client):
    r = await client.get("/health")
    for hdr in ["x-content-type-options", "x-frame-options", "strict-transport-security",
                "content-security-policy", "referrer-policy", "x-request-id"]:
        assert hdr in r.headers, hdr
    assert r.headers["x-content-type-options"] == "nosniff"


async def test_request_id_echoed(client):
    r = await client.get("/health", headers={"X-Request-Id": "test-req-123"})
    assert r.headers.get("x-request-id") == "test-req-123"


async def test_audit_requires_superuser(client, admin_id):
    assert (await client.get("/api/v1/audit", headers=auth(admin_id))).status_code == 200
    uid, tid = await _make_user_in_new_team(client, admin_id, "admin")  # team admin, not superuser
    assert (await client.get("/api/v1/audit", headers=auth(uid))).status_code == 403

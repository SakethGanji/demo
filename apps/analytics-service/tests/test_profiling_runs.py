"""Wave 1 §8 — persisted profile runs + rule-based insights.

Covers: the run lifecycle (job row → run rows → completed with insights),
idempotent re-profiling, cross-version insight rules (new categories,
null-rate spike, new sheet), and the standing authorization contracts.
"""

from __future__ import annotations

import json

from conftest import (
    XLSX_MIME,
    auth,
    create_team_user,
    make_workbook,
    upload_file,
    upload_inline,
)

V1_ROWS = [
    {"id": 1, "email": "a@x.com", "tier": "gold"},
    {"id": 2, "email": "b@x.com", "tier": "gold"},
    {"id": 3, "email": "c@x.com", "tier": "silver"},
    {"id": 4, "email": "d@x.com", "tier": "gold"},
    {"id": 5, "email": "e@x.com", "tier": "silver"},
]
# v2: a brand-new category and a null-rate jump on email.
V2_ROWS = [
    {"id": 1, "email": None, "tier": "gold"},
    {"id": 2, "email": None, "tier": "copper"},
    {"id": 3, "email": None, "tier": "copper"},
    {"id": 4, "email": "d@x.com", "tier": "silver"},
    {"id": 5, "email": None, "tier": "copper"},
]


def _rules(run: dict) -> set[str]:
    return {i["rule"] for i in run["insights"]}


async def test_profile_run_lifecycle(client, admin_id):
    ds = (await upload_inline(client, admin_id, json.dumps(V1_ROWS)))["dataset_id"]
    h = auth(admin_id)

    r = await client.post(f"/api/v1/datasets/{ds}/versions/1/profile-runs", headers=h)
    assert r.status_code == 200, r.text
    runs = r.json()
    assert len(runs) == 1
    run = runs[0]
    assert run["status"] == "completed" and run["sheet_name"] == "data"
    assert run["logical_sheet_id"] and run["completed_at"]
    assert "likely-primary-key" in _rules(run)

    # The run rode the jobs table.
    r = await client.get("/api/v1/jobs", headers=h,
                        params={"job_type": "profiling", "status": "completed"})
    assert r.status_code == 200 and r.json()["total"] >= 1

    # Listing echoes the persisted run; detail carries the profile JSON.
    # GET is a Page envelope like every other collection in the service.
    r = await client.get(f"/api/v1/datasets/{ds}/versions/1/profile-runs", headers=h)
    assert r.status_code == 200 and r.json()["total"] == 1
    listed = r.json()["items"][0]
    assert listed["id"] == run["id"] and _rules(listed) == _rules(run)

    r = await client.get(f"/api/v1/datasets/{ds}/profile-runs/{run['id']}", headers=h)
    assert r.status_code == 200, r.text
    profile = r.json()["profile"]
    assert profile["row_count"] == 5
    assert {c["name"] for c in profile["columns"]} == {"id", "email", "tier"}


async def test_reprofile_is_idempotent(client, admin_id):
    ds = (await upload_inline(client, admin_id, json.dumps(V1_ROWS)))["dataset_id"]
    h = auth(admin_id)
    first = (await client.post(
        f"/api/v1/datasets/{ds}/versions/1/profile-runs", headers=h)).json()
    second = (await client.post(
        f"/api/v1/datasets/{ds}/versions/1/profile-runs", headers=h)).json()
    assert first[0]["id"] == second[0]["id"]  # same (version, sheet, algo) row
    r = await client.get(f"/api/v1/datasets/{ds}/versions/1/profile-runs", headers=h)
    assert r.json()["total"] == 1  # reset, not duplicated


async def test_cross_version_insights(client, admin_id):
    ds = (await upload_inline(client, admin_id, json.dumps(V1_ROWS)))["dataset_id"]
    await upload_inline(client, admin_id, json.dumps(V2_ROWS), dataset_id=ds)
    h = auth(admin_id)

    r = await client.post(f"/api/v1/datasets/{ds}/versions/1/profile-runs", headers=h)
    assert r.status_code == 200, r.text
    r = await client.post(f"/api/v1/datasets/{ds}/versions/2/profile-runs", headers=h)
    assert r.status_code == 200, r.text
    rules = _rules(r.json()[0])
    assert "null-rate-spike" in rules  # email: 0% → 80%
    assert "high-null-rate" in rules
    assert "new-categories" in rules   # copper
    cat = next(i for i in r.json()[0]["insights"] if i["rule"] == "new-categories")
    assert "copper" in cat["evidence"]["added"]


async def test_new_sheet_insight_on_workbook(client, admin_id, tmp_path):
    v1, v2 = tmp_path / "v1.xlsx", tmp_path / "v2.xlsx"
    make_workbook(v1)
    make_workbook(v2, quarter_col=True, second_sheet="Spending")
    ds = (await upload_file(client, admin_id, v1, name="book.xlsx",
                            content_type=XLSX_MIME))["dataset_id"]
    await upload_file(client, admin_id, v2, name="book.xlsx",
                      content_type=XLSX_MIME, dataset_id=ds)
    h = auth(admin_id)

    r = await client.post(f"/api/v1/datasets/{ds}/versions/1/profile-runs", headers=h)
    assert r.status_code == 200, r.text
    assert len(r.json()) == 3  # Revenue, Expenses, Secrets

    r = await client.post(f"/api/v1/datasets/{ds}/versions/2/profile-runs", headers=h)
    assert r.status_code == 200, r.text
    by_sheet = {run["sheet_name"]: run for run in r.json()}
    assert "new-sheet" in _rules(by_sheet["Spending"])   # Expenses → Spending
    assert "new-sheet" not in _rules(by_sheet["Revenue"])


# --- §9 profile drift on the diff endpoints -----------------------------------


async def test_sheet_diff_profile_drift(client, admin_id):
    ds = (await upload_inline(client, admin_id, json.dumps(V1_ROWS)))["dataset_id"]
    await upload_inline(client, admin_id, json.dumps(V2_ROWS), dataset_id=ds)
    h = auth(admin_id)

    # Without runs, include=profile is a clean contract error naming both sides.
    r = await client.get(f"/api/v1/datasets/{ds}/versions/1/sheets/data/diff/2",
                        params={"include": "profile"}, headers=h)
    assert r.status_code == 400, r.text
    assert r.json()["code"] == "profile-required"
    assert r.json()["missing"] == ["from", "to"]

    for v in (1, 2):
        assert (await client.post(
            f"/api/v1/datasets/{ds}/versions/{v}/profile-runs",
            headers=h)).status_code == 200

    r = await client.get(f"/api/v1/datasets/{ds}/versions/1/sheets/data/diff/2",
                        params={"include": "profile"}, headers=h)
    assert r.status_code == 200, r.text
    drift = r.json()["profile_drift"]
    assert drift["row_count_delta"] == 0
    cols = {c["column"]: c for c in drift["columns"]}
    assert cols["email"]["null_percent_delta"] == 80.0
    assert "copper" in cols["tier"]["added_categories"]

    # Plain diffs stay unchanged (no drift section).
    r = await client.get(f"/api/v1/datasets/{ds}/versions/1/sheets/data/diff/2",
                        headers=h)
    assert r.status_code == 200 and r.json()["profile_drift"] is None


async def test_workbook_diff_profile_drift_and_missing(client, admin_id):
    ds = (await upload_inline(client, admin_id, json.dumps(V1_ROWS)))["dataset_id"]
    await upload_inline(client, admin_id, json.dumps(V2_ROWS), dataset_id=ds)
    h = auth(admin_id)

    # Only one side profiled → the sheet lands in profile_missing, not an error.
    assert (await client.post(f"/api/v1/datasets/{ds}/versions/1/profile-runs",
                              headers=h)).status_code == 200
    r = await client.get(f"/api/v1/datasets/{ds}/versions/1/diff/2",
                        params={"include": "profile"}, headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["profile_drift"] == [] and r.json()["profile_missing"] == ["data"]

    assert (await client.post(f"/api/v1/datasets/{ds}/versions/2/profile-runs",
                              headers=h)).status_code == 200
    r = await client.get(f"/api/v1/datasets/{ds}/versions/1/diff/2",
                        params={"include": "profile"}, headers=h)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["profile_missing"] == []
    assert len(body["profile_drift"]) == 1
    assert body["profile_drift"][0]["sheet_key"] == "data"

    r = await client.get(f"/api/v1/datasets/{ds}/versions/1/diff/2",
                        params={"include": "nonsense"}, headers=h)
    assert r.status_code == 400


async def test_profile_runs_authorization(client, admin_id):
    ds = (await upload_inline(client, admin_id, json.dumps(V1_ROWS)))["dataset_id"]
    outsider, _ = await create_team_user(client, admin_id, "viewer")
    for method, url in (
        ("post", f"/api/v1/datasets/{ds}/versions/1/profile-runs"),
        ("get", f"/api/v1/datasets/{ds}/versions/1/profile-runs"),
    ):
        r = await getattr(client, method)(url, headers=auth(outsider))
        assert r.status_code == 404, (method, url)  # existence hidden

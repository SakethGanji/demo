"""End-to-end journeys — every step in the order a UI would drive it.

Two journeys against the real app + Postgres + storage backend:

1. Team workbook lifecycle: bootstrap a team and roles → upload an Excel
   workbook → browse metadata/sheets → analytics → new version → diffs →
   promote/rollback with history → RBAC boundaries → downloads → audit →
   delete.
2. CSV/single-table lifecycle: upload → implicit sheet resolution everywhere →
   async upload polling → new version via inline JSON → sheet diff → raw tag
   set + promote → delete.

Each journey is one test function so ordering is explicit and state flows
step to step, exactly like a browser session.
"""

from __future__ import annotations

from conftest import (
    DEFAULT_TEAM_ID, SAMPLE_CSV, auth, create_team_user, make_workbook, rid,
)

PROBLEM = "application/problem+json"


async def test_journey_team_workbook_lifecycle(client, admin_id, tmp_path):
    # ---- 1. Bootstrap: platform admin creates a team and provisions roles ----
    h_admin = auth(admin_id)
    team = (await client.post("/api/v1/teams", headers=h_admin,
                              json={"name": f"payments-{rid()}"})).json()
    tid = team["id"]
    team_admin, _ = await create_team_user(client, admin_id, "admin", team_id=tid)
    editor, _ = await create_team_user(client, admin_id, "editor", team_id=tid)
    viewer, _ = await create_team_user(client, admin_id, "viewer", team_id=tid)
    outsider, _ = await create_team_user(client, admin_id, "editor")

    # Everyone sees their own membership.
    me = (await client.get("/api/v1/auth/me", headers=auth(editor))).json()
    assert any(m["team_id"] == tid and m["role"] == "editor" for m in me["memberships"])

    # ---- 2. Editor uploads workbook v1 ----
    v1_file = tmp_path / "payments_v1.xlsx"
    make_workbook(v1_file)
    with open(v1_file, "rb") as f:
        up = await client.post("/api/v1/upload",
                               headers={**auth(editor), "X-Team-Id": tid},
                               files={"file": ("payments.xlsx", f, "application/octet-stream")})
    assert up.status_code == 200, up.text
    ds = up.json()["dataset_id"]
    assert up.json()["row_count"] == 6  # TOTAL across sheets: 3 + 2 + 1

    # ---- 3. Browse: listing, metadata, sheets ----
    h_editor = auth(editor)
    listing = (await client.get("/api/v1/datasets", headers=h_editor)).json()
    assert any(d["id"] == ds for d in listing["items"])

    meta = (await client.get(f"/api/v1/datasets/{ds}", headers=h_editor)).json()
    assert meta["default_sheet"] == "Revenue"
    assert {s["name"]: s["visibility"] for s in meta["sheets"]} == {
        "Revenue": "visible", "Expenses": "visible", "Secrets": "hidden"}
    assert all(s["storage_key"] for s in meta["sheets"])  # no NULL sentinel on new ingests

    sheets = (await client.get(f"/api/v1/datasets/{ds}/sheets", headers=h_editor)).json()["items"]
    rev = next(s for s in sheets if s["name"] == "Revenue")
    by_norm = {c["normalized_name"]: c for c in rev["columns"]}
    assert set(by_norm) == {"amount", "amount_2", "column_2", "region"}
    assert by_norm["amount"]["header_was_duplicated"] and by_norm["amount_2"]["header_was_duplicated"]
    assert by_norm["column_2"]["generated_name"]

    one = (await client.get(f"/api/v1/datasets/{ds}/sheets/Expenses", headers=h_editor)).json()
    assert one["row_count"] == 2 and one["preview"]

    # ---- 4. Analytics: sheet contract, then real runs ----
    r = await client.post("/api/v1/sample", headers=h_editor, json={
        "dataset_id": ds, "target_total_volume": 2,
        "sampling_steps": [{"method": "random", "sample_size": 2}]})
    assert r.status_code == 400 and r.json()["code"] == "sheet-selection-required"
    assert set(r.json()["sheets"]) == {"Revenue", "Expenses", "Secrets"}

    r = await client.post("/api/v1/sample", headers=h_editor, json={
        "dataset_id": ds, "sheet": "Revenue", "target_total_volume": 2,
        "sampling_steps": [{"method": "random", "sample_size": 2}]})
    assert r.status_code == 200 and r.json()["sampled_count"] <= 2

    r = await client.post("/api/v1/profile", headers=h_editor, json={
        "dataset_id": ds, "sheet": "Expenses", "include_histograms": False})
    assert r.status_code == 200 and r.json()["row_count"] == 2

    r = await client.post("/api/v1/aggregate", headers=h_editor, json={
        "dataset_id": ds, "sheet": "Expenses", "group_by": ["Item"],
        "aggregations": [{"column": "Cost", "function": "sum"}]})
    assert r.status_code == 200 and r.json()["group_count"] == 2

    # ---- 5. Editor uploads v2 (Revenue +column +row, Expenses renamed) ----
    v2_file = tmp_path / "payments_v2.xlsx"
    make_workbook(v2_file, quarter_col=True, second_sheet="Spending",
                  extra_revenue_row=True)
    with open(v2_file, "rb") as f:
        up2 = await client.post("/api/v1/upload", headers=h_editor,
                                files={"file": ("payments.xlsx", f, "application/octet-stream")},
                                data={"dataset_id": ds})
    assert up2.status_code == 200, up2.text

    versions = (await client.get(f"/api/v1/datasets/{ds}/versions", headers=h_editor)).json()["items"]
    assert [v["version_number"] for v in versions] == [2, 1]
    v1_info, v2_info = versions[1], versions[0]
    assert v1_info["row_count"] == 6 and v2_info["row_count"] == 7  # totals, not first-sheet
    assert v1_info["sheet_count"] == 3 and v2_info["sheet_count"] == 3
    assert v1_info["source_checksum"] and v1_info["manifest_checksum"]
    assert v1_info["manifest_checksum"] != v2_info["manifest_checksum"]

    # ---- 6. Review the change: workbook diff, then drill into a sheet ----
    diff = (await client.get(f"/api/v1/datasets/{ds}/versions/1/diff/2", headers=h_editor)).json()
    assert [s["name"] for s in diff["added"]] == ["Spending"]
    assert [s["name"] for s in diff["removed"]] == ["Expenses"]
    assert diff["unchanged"] == ["Secrets"]
    assert diff["rename_candidates"][0]["confidence"] == "high"
    mod = {m["sheet_key"]: m for m in diff["modified"]}
    assert mod["revenue"]["schema_changed"] and mod["revenue"]["row_count_delta"] == 1

    sdiff = (await client.get(
        f"/api/v1/datasets/{ds}/versions/1/sheets/Revenue/diff/2", headers=h_editor)).json()
    assert [c["normalized_name"] for c in sdiff["added_columns"]] == ["quarter"]
    assert sdiff["row_count_delta"] == 1 and not sdiff["identical"]

    # ---- 7. Governance: promote → refresh → rollback, with history ----
    r = await client.post(f"/api/v1/datasets/{ds}/tags/Production/promote", headers=h_editor,
                          json={"version_number": 1, "reason": "initial go-live"})
    assert r.status_code == 200 and r.json()["to_version_number"] == 1

    # Case-insensitive: promoted as 'Production', resolves as anything.
    for variant in ("production", "PRODUCTION", "Production"):
        r = await client.get(f"/api/v1/datasets/{ds}/tags/{variant}", headers=h_editor)
        assert r.status_code == 200 and r.json()["version_number"] == 1, variant

    r = await client.post(f"/api/v1/datasets/{ds}/tags/production/promote", headers=h_editor,
                          json={"version_number": 2, "reason": "monthly refresh"})
    assert r.json()["from_version_number"] == 1

    # Analytics can pin to the tag.
    r = await client.post("/api/v1/profile", headers=h_editor, json={
        "dataset_id": ds, "tag": "production", "sheet": "Spending", "include_histograms": False})
    assert r.status_code == 200

    r = await client.post(f"/api/v1/datasets/{ds}/tags/production/rollback", headers=h_editor,
                          json={"reason": "refresh had bad rows"})
    assert r.status_code == 200 and r.json()["to_version_number"] == 1

    hist = (await client.get(f"/api/v1/datasets/{ds}/tags/production/history",
                             headers=h_editor)).json()["items"]
    assert [e["action"] for e in hist] == ["rollback", "promote", "promote"]
    assert hist[0]["reason"] == "refresh had bad rows"
    assert all(e["actor_email"].startswith("editor-") for e in hist)
    assert all(e["request_id"] for e in hist)
    assert all(e["tag_name"] == "production" for e in hist)

    # ---- 8. RBAC boundaries the UI must respect ----
    h_viewer, h_outsider = auth(viewer), auth(outsider)
    assert (await client.get(f"/api/v1/datasets/{ds}/sheets", headers=h_viewer)).status_code == 200
    r = await client.post(f"/api/v1/datasets/{ds}/tags/production/promote", headers=h_viewer,
                          json={"version_number": 2})
    assert r.status_code == 403                                     # viewer: read-only
    for path in (f"/api/v1/datasets/{ds}",
                 f"/api/v1/datasets/{ds}/versions/1/diff/2",
                 f"/api/v1/datasets/{ds}/tags/production/history"):
        assert (await client.get(path, headers=h_outsider)).status_code == 404, path  # hidden

    # ---- 9. Downloads (current + version-pinned, sheet contract) ----
    r = await client.get(f"/api/v1/datasets/{ds}/download",
                         params={"format": "csv"}, headers=h_editor)
    assert r.status_code == 400 and r.json()["code"] == "sheet-selection-required"
    r = await client.get(f"/api/v1/datasets/{ds}/download",
                         params={"format": "csv", "sheet": "Spending"}, headers=h_editor)
    assert r.status_code == 200 and b"Item" in r.content
    r = await client.get(f"/api/v1/datasets/{ds}/versions/1/download",
                         params={"format": "parquet", "sheet": "Expenses"}, headers=h_editor)
    assert r.status_code == 200 and r.content[:4] == b"PAR1"

    # ---- 10. Audit: the governance actions are on the record ----
    audit_page = (await client.get("/api/v1/audit", params={"limit": 200},
                                   headers=h_admin)).json()
    paths = [e["path"] for e in audit_page["items"]]
    assert f"/api/v1/datasets/{ds}/tags/production/rollback" in paths
    assert any(p.endswith("/promote") for p in paths)
    assert (await client.get("/api/v1/audit", headers=auth(team_admin))).status_code == 403

    # ---- 11. Retire: only the team admin may delete ----
    assert (await client.delete(f"/api/v1/datasets/{ds}", headers=h_editor)).status_code == 403
    assert (await client.delete(f"/api/v1/datasets/{ds}", headers=auth(team_admin))).status_code == 200
    assert (await client.get(f"/api/v1/datasets/{ds}", headers=h_editor)).status_code == 404


async def test_journey_csv_single_table_lifecycle(client, admin_id):
    h = auth(admin_id)

    # ---- 1. Async upload with status polling (how a UI shows progress) ----
    with open(SAMPLE_CSV, "rb") as f:
        up = await client.post("/api/v1/upload", params={"sync": "false"},
                               headers={**h, "X-Team-Id": DEFAULT_TEAM_ID},
                               files={"file": ("accounts.csv", f, "text/csv")})
    assert up.status_code == 200, up.text
    ds, vid = up.json()["dataset_id"], up.json()["version_id"]
    status = (await client.get(f"/api/v1/upload/status/{vid}", headers=h)).json()
    assert status["status"] == "complete" and status["row_count"] > 0

    # ---- 2. Single table: everything resolves without naming a sheet ----
    meta = (await client.get(f"/api/v1/datasets/{ds}", headers=h)).json()
    assert [s["name"] for s in meta["sheets"]] == ["data"]
    assert meta["sheets"][0]["storage_key"]

    r = await client.post("/api/v1/sample", headers=h, json={
        "dataset_id": ds, "target_total_volume": 3,
        "sampling_steps": [{"method": "random", "sample_size": 3}]})
    assert r.status_code == 200
    r = await client.post("/api/v1/profile", headers=h, json={
        "dataset_id": ds, "include_histograms": False})
    assert r.status_code == 200
    r = await client.get(f"/api/v1/datasets/{ds}/download",
                         params={"format": "csv", "limit": 3}, headers=h)
    assert r.status_code == 200

    # ---- 3. New version via inline JSON with a different shape ----
    r = await client.post("/api/v1/upload", headers=h,
                          data={"data": '[{"brand": "x", "score": 1}]', "dataset_id": ds})
    assert r.status_code == 200, r.text

    versions = (await client.get(f"/api/v1/datasets/{ds}/versions", headers=h)).json()["items"]
    assert versions[0]["version_number"] == 2 and versions[0]["sheet_count"] == 1
    assert versions[0]["source_checksum"] and versions[0]["manifest_checksum"]

    sdiff = (await client.get(f"/api/v1/datasets/{ds}/versions/1/sheets/data/diff/2",
                              headers=h)).json()
    assert {"brand", "score"}.issubset({c["normalized_name"] for c in sdiff["added_columns"]})
    assert sdiff["removed_columns"]  # original CSV columns are gone in v2

    # ---- 4. Tags: raw set for a scratch pointer, promote for governance ----
    r = await client.put(f"/api/v1/datasets/{ds}/tags", headers=h,
                         json={"tag_name": "Latest-Good", "version_number": 1})
    assert r.status_code == 200 and r.json()["tag_name"] == "latest-good"  # normalized
    r = await client.post(f"/api/v1/datasets/{ds}/tags/latest-good/promote", headers=h,
                          json={"version_number": 2, "reason": "schema v2 approved"})
    assert r.status_code == 200 and r.json()["from_version_number"] == 1

    hist = (await client.get(f"/api/v1/datasets/{ds}/tags/latest-good/history",
                             headers=h)).json()["items"]
    assert [e["action"] for e in hist] == ["promote", "set"]

    # Promote refuses versions that don't exist; rollback works from history.
    r = await client.post(f"/api/v1/datasets/{ds}/tags/latest-good/promote", headers=h,
                          json={"version_number": 9})
    assert r.status_code == 404
    r = await client.post(f"/api/v1/datasets/{ds}/tags/latest-good/rollback", headers=h, json={})
    assert r.status_code == 200 and r.json()["to_version_number"] == 1

    # ---- 5. Retire ----
    assert (await client.delete(f"/api/v1/datasets/{ds}", headers=h)).status_code == 200
    assert (await client.get(f"/api/v1/datasets/{ds}", headers=h)).status_code == 404

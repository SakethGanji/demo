"""Postgres is the control plane — it must not hold copies of dataset rows.

The rule: Postgres stores identity, config, schema, counts, status, and
pointers. Anything that is a copy of dataset cell values belongs in the object
store, reachable through an `artifacts` row so /samples authorization applies.

This file is the guard. It is deliberately schema-driven rather than a list of
known-bad columns, so a future migration that reintroduces a row-shaped JSONB
column has to confront the rule rather than slip past it.
"""

from __future__ import annotations

import json

import pytest
from sqlalchemy import text

from conftest import auth, upload_inline
from app.infra.db.postgres.session import engine

ROWS = [{"id": i, "email": f"u{i}@example.com", "amount": i * 10.0}
        for i in range(1, 6)] + [{"id": None, "email": "bad", "amount": -1.0}]

# JSONB columns that legitimately hold control-plane state. Everything here is
# config a user wrote, a schema, a set of counts, or a pointer — never a copy
# of rows lifted out of a dataset.
ALLOWED_JSONB = {
    ("audit_log", "metadata"),
    ("analytics_definitions", "params"), ("analytics_definitions", "version_selector"),
    ("analytics_runs", "result_summary"),
    ("chart_definitions", "config"),
    ("dataset_column_metadata", "allowed_values"),
    ("dataset_relationships", "evidence"),
    ("dataset_sheet_metadata", "primary_key_columns"),
    ("dataset_version_sheets", "schema_json"),
    ("dataset_versions", "source"),
    ("dataset_views", "query"), ("dataset_views", "version_selector"),
    ("datasets", "metadata"),
    ("jobs", "parameters"), ("jobs", "result"),
    ("profile_insights", "evidence"),
    # Profiles are aggregate statistics (counts, ratios, capped top-N) that the
    # catalog, health, and drift features read via SQL. Deliberate exception —
    # see HANDOFF "Control plane" note.
    ("profile_runs", "profile"),
    ("transformation_runs", "output_profile"), ("transformation_runs", "source_drift"),
    ("transformation_runs", "result_summary"),
    ("transformation_definitions", "steps"),
    ("transformation_definitions", "version_selector"),
    ("quality_rules", "parameters"),
    # Event-type allow-list — pure subscription config.
    ("webhook_subscriptions", "events"),
    # The outbound event body: ids and counts only. Deliberately thin, and
    # test_webhooks asserts no dataset content reaches it — a webhook body
    # lands in logs and third-party systems outside this service's control.
    ("webhook_deliveries", "payload"),
}


async def test_no_unreviewed_jsonb_columns_exist():
    """Every JSONB column is accounted for as control-plane state.

    A new one is not necessarily wrong — but it must be reviewed against the
    rule and added to ALLOWED_JSONB deliberately.
    """
    async with engine.begin() as conn:
        rows = (await conn.execute(text("""
            SELECT table_name, column_name FROM information_schema.columns
            WHERE table_schema = 'accelerator' AND data_type = 'jsonb'
        """))).all()
    found = {(t, c) for t, c in rows}
    unreviewed = sorted(found - ALLOWED_JSONB)
    assert not unreviewed, (
        f"Unreviewed JSONB column(s): {unreviewed}. If one stores dataset rows, "
        "write them to the object store as an artifact instead. If it is "
        "control-plane state, add it to ALLOWED_JSONB.")


async def test_validation_failures_are_not_stored_in_postgres(client, admin_id):
    """The regression this rule was written for: failing rows used to be JSONB."""
    ds = (await upload_inline(client, admin_id, json.dumps(ROWS)))["dataset_id"]
    h = auth(admin_id)
    await client.post(f"/api/v1/datasets/{ds}/rules", headers=h, json={
        "name": "id-not-null", "rule_type": "not_null",
        "sheet_selector": "data", "column_selector": "id"})

    r = await client.post(f"/api/v1/datasets/{ds}/versions/1/validate", headers=h)
    assert r.status_code == 200, r.text
    [result] = r.json()["results"]
    assert result["status"] == "failed" and result["failure_count"] == 1

    # The column is gone from the schema entirely.
    async with engine.begin() as conn:
        cols = (await conn.execute(text("""
            SELECT column_name FROM information_schema.columns
            WHERE table_schema = 'accelerator'
              AND table_name = 'validation_rule_results'
        """))).scalars().all()
    assert "sample_failures" not in cols
    assert "failure_artifact_id" in cols

    # The rows themselves are retrievable from the object store.
    rows = await client.get(
        f"/api/v1/samples/{result['failure_sample_file']}/data", headers=h)
    assert rows.status_code == 200
    body = rows.json()
    assert len(body["data"] if isinstance(body, dict) else body) == 1


async def test_failing_rows_are_authorized_like_any_other_artifact(client, admin_id):
    """Registering them as artifacts is what makes cross-team access 404."""
    from conftest import create_team_user

    ds = (await upload_inline(client, admin_id, json.dumps(ROWS)))["dataset_id"]
    h = auth(admin_id)
    await client.post(f"/api/v1/datasets/{ds}/rules", headers=h, json={
        "name": "id-not-null", "rule_type": "not_null",
        "sheet_selector": "data", "column_selector": "id"})
    result = (await client.post(f"/api/v1/datasets/{ds}/versions/1/validate",
                                headers=h)).json()["results"][0]

    outsider, _ = await create_team_user(client, admin_id, "admin")
    r = await client.get(f"/api/v1/samples/{result['failure_sample_file']}",
                         headers=auth(outsider))
    assert r.status_code == 404

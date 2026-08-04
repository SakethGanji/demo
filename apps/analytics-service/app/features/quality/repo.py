"""Quality feature — rules CRUD + validation run persistence."""

from __future__ import annotations

import json

from sqlalchemy import text

from app.infra.db.postgres import async_session_factory
from app.shared.repo import is_uuid

_RULE_COLS = """id::text, dataset_id::text, name, description, scope_type,
                sheet_selector, column_selector, rule_type, parameters,
                severity, enabled, created_by::text,
                created_at::text AS created_at, updated_at::text AS updated_at"""


async def create_rule(dataset_id: str, fields: dict, created_by: str) -> dict:
    async with async_session_factory() as s:
        row = (await s.execute(
            text(f"""
                INSERT INTO quality_rules
                    (dataset_id, name, description, scope_type, sheet_selector,
                     column_selector, rule_type, parameters, severity, enabled, created_by)
                VALUES (:did, :name, :description, :scope_type, :sheet_selector,
                        :column_selector, :rule_type, CAST(:parameters AS jsonb),
                        :severity, :enabled, :created_by)
                RETURNING {_RULE_COLS}
            """),
            {"did": dataset_id, "created_by": created_by,
             "name": fields["name"], "description": fields.get("description"),
             "scope_type": fields["scope_type"],
             "sheet_selector": fields.get("sheet_selector"),
             "column_selector": fields.get("column_selector"),
             "rule_type": fields["rule_type"],
             "parameters": json.dumps(fields.get("parameters") or {}),
             "severity": fields.get("severity", "error"),
             "enabled": fields.get("enabled", True)},
        )).mappings().one()
        await s.commit()
        return dict(row)


async def list_rules(dataset_id: str, enabled_only: bool = False) -> list[dict]:
    async with async_session_factory() as s:
        clause = " AND enabled" if enabled_only else ""
        rows = (await s.execute(
            text(f"SELECT {_RULE_COLS} FROM quality_rules "
                 f"WHERE dataset_id = :did{clause} ORDER BY created_at"),
            {"did": dataset_id},
        )).mappings().all()
        return [dict(r) for r in rows]


async def get_rule(dataset_id: str, rule_id: str) -> dict | None:
    if not is_uuid(rule_id):
        return None
    async with async_session_factory() as s:
        row = (await s.execute(
            text(f"SELECT {_RULE_COLS} FROM quality_rules WHERE id = :id AND dataset_id = :did"),
            {"id": rule_id, "did": dataset_id},
        )).mappings().first()
        return dict(row) if row else None


_MUTABLE = {"name", "description", "sheet_selector", "column_selector",
            "parameters", "severity", "enabled"}


async def update_rule(dataset_id: str, rule_id: str, fields: dict) -> dict | None:
    updates = {k: v for k, v in fields.items() if k in _MUTABLE}
    if not updates or not is_uuid(rule_id):
        return await get_rule(dataset_id, rule_id)
    sets, params = ["updated_at = now()"], {"id": rule_id, "did": dataset_id}
    for k, v in updates.items():
        if k == "parameters":
            sets.append("parameters = CAST(:parameters AS jsonb)")
            params["parameters"] = json.dumps(v or {})
        else:
            sets.append(f"{k} = :{k}")
            params[k] = v
    async with async_session_factory() as s:
        row = (await s.execute(
            text(f"UPDATE quality_rules SET {', '.join(sets)} "
                 f"WHERE id = :id AND dataset_id = :did RETURNING {_RULE_COLS}"),
            params,
        )).mappings().first()
        await s.commit()
        return dict(row) if row else None


async def delete_rule(dataset_id: str, rule_id: str) -> bool:
    if not is_uuid(rule_id):
        return False
    async with async_session_factory() as s:
        result = await s.execute(
            text("DELETE FROM quality_rules WHERE id = :id AND dataset_id = :did"),
            {"id": rule_id, "did": dataset_id},
        )
        await s.commit()
        return result.rowcount > 0


async def count_enabled_rules(dataset_id: str) -> int:
    async with async_session_factory() as s:
        return (await s.execute(
            text("SELECT COUNT(*) FROM quality_rules WHERE dataset_id = :did AND enabled"),
            {"did": dataset_id},
        )).scalar()


# ---------------------------------------------------------------------------
# Validation runs
# ---------------------------------------------------------------------------

_RUN_COLS = """id::text, dataset_id::text, dataset_version_id::text, job_id::text,
               status, rules_total, rules_passed, rules_failed,
               error_failures, warning_failures, triggered_by::text,
               started_at::text AS started_at,
               completed_at::text AS completed_at, error"""


async def create_run(dataset_id: str, version_id: str, job_id: str | None,
                     triggered_by: str | None) -> dict:
    async with async_session_factory() as s:
        row = (await s.execute(
            text(f"""
                INSERT INTO validation_runs (dataset_id, dataset_version_id, job_id, triggered_by)
                VALUES (:did, :vid, :jid, :uid)
                RETURNING {_RUN_COLS}
            """),
            {"did": dataset_id, "vid": version_id, "jid": job_id, "uid": triggered_by},
        )).mappings().one()
        await s.commit()
        return dict(row)


async def complete_run(run_id: str, results: list[dict]) -> dict:
    """Persist rule results + summary counts in one transaction."""
    passed = sum(1 for r in results if r["status"] == "passed")
    failed = sum(1 for r in results if r["status"] in ("failed", "error"))
    error_failures = sum(1 for r in results
                         if r["status"] in ("failed", "error") and r["severity"] == "error")
    warning_failures = sum(1 for r in results
                           if r["status"] in ("failed", "error") and r["severity"] == "warning")
    async with async_session_factory() as s:
        for r in results:
            await s.execute(
                text("""
                    INSERT INTO validation_rule_results
                        (validation_run_id, rule_id, rule_name, rule_type, scope_type,
                         sheet_selector, column_selector, severity, status,
                         failure_count, message, sample_failures)
                    VALUES (:rid, :rule_id, :rule_name, :rule_type, :scope_type,
                            :sheet_selector, :column_selector, :severity, :status,
                            :failure_count, :message, CAST(:sample_failures AS jsonb))
                """),
                {"rid": run_id, **{k: r.get(k) for k in
                                   ("rule_id", "rule_name", "rule_type", "scope_type",
                                    "sheet_selector", "column_selector", "severity",
                                    "status", "failure_count", "message")},
                 "sample_failures": json.dumps(r["sample_failures"])
                                    if r.get("sample_failures") is not None else None},
            )
        row = (await s.execute(
            text(f"""
                UPDATE validation_runs
                SET status = 'completed', completed_at = now(),
                    rules_total = :total, rules_passed = :passed, rules_failed = :failed,
                    error_failures = :ef, warning_failures = :wf
                WHERE id = :id
                RETURNING {_RUN_COLS}
            """),
            {"id": run_id, "total": len(results), "passed": passed, "failed": failed,
             "ef": error_failures, "wf": warning_failures},
        )).mappings().one()
        await s.commit()
        return dict(row)


async def fail_run(run_id: str, error: str) -> None:
    async with async_session_factory() as s:
        await s.execute(
            text("UPDATE validation_runs SET status = 'failed', error = :e, "
                 "completed_at = now() WHERE id = :id"),
            {"id": run_id, "e": error},
        )
        await s.commit()


async def list_runs(dataset_id: str, version_id: str,
                    limit: int = 50, offset: int = 0) -> tuple[list[dict], int]:
    async with async_session_factory() as s:
        params = {"did": dataset_id, "vid": version_id}
        total = (await s.execute(
            text("SELECT COUNT(*) FROM validation_runs "
                 "WHERE dataset_id = :did AND dataset_version_id = :vid"), params,
        )).scalar()
        rows = (await s.execute(
            text(f"SELECT {_RUN_COLS} FROM validation_runs "
                 f"WHERE dataset_id = :did AND dataset_version_id = :vid "
                 f"ORDER BY started_at DESC LIMIT :limit OFFSET :offset"),
            {**params, "limit": limit, "offset": offset},
        )).mappings().all()
        return [dict(r) for r in rows], total


async def get_run(dataset_id: str, run_id: str) -> dict | None:
    if not is_uuid(run_id):
        return None
    async with async_session_factory() as s:
        row = (await s.execute(
            text(f"SELECT {_RUN_COLS} FROM validation_runs "
                 f"WHERE id = :id AND dataset_id = :did"),
            {"id": run_id, "did": dataset_id},
        )).mappings().first()
        return dict(row) if row else None


async def list_run_results(run_id: str) -> list[dict]:
    async with async_session_factory() as s:
        rows = (await s.execute(
            text("""
                SELECT id::text, rule_id::text, rule_name, rule_type, scope_type,
                       sheet_selector, column_selector, severity, status,
                       failure_count, message, sample_failures
                FROM validation_rule_results
                WHERE validation_run_id = :rid
                ORDER BY rule_name
            """),
            {"rid": run_id},
        )).mappings().all()
        return [dict(r) for r in rows]


async def latest_completed_run(dataset_id: str, version_id: str) -> dict | None:
    """The most recent completed run for a version — the promotion gate input."""
    async with async_session_factory() as s:
        row = (await s.execute(
            text(f"""
                SELECT {_RUN_COLS} FROM validation_runs
                WHERE dataset_id = :did AND dataset_version_id = :vid
                  AND status = 'completed'
                ORDER BY started_at DESC LIMIT 1
            """),
            {"did": dataset_id, "vid": version_id},
        )).mappings().first()
        return dict(row) if row else None

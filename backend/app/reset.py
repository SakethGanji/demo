"""Reset the workbench: delete every run, registered model, batch, autopilot, agent conversation, MLflow record and
dataset. `make reset` calls POST /api/admin/reset when the
API is running (so its in-memory state is cleared too), else `python -m app.reset` offline.

DL3 (C1): a reset is governed like every other destructive action, on both paths:
- off unless ENSEMBLE_DEMO_MODE=1 (a demo / local-dev switch; never set it on a shared or production instance);
- `by` must be an admin (ENSEMBLE_ADMINS);
- refused while any registered version is live (not retired, not rejected) or any champion is approved or pending,
  unless a four-eyes approval is in place: someone asks (POST /api/admin/reset-requests), another person approves, and
  an admin runs the reset with that request id (used once);
- the audit log (and the reset requests) are never deleted; the reset writes its own audit row (who, counts, approval);
- F-X4b (X4 M4): refused with 409 `legal_hold` (naming the cases) while any legal hold is active, checked again in the
  delete transaction: a reset deletes every team's data, so any hold covers it, and no active hold is ever left
  pointing at nothing (holds, like the audit log, are kept);
- the catalog goes with the data it describes (lineage edges, champions, cards, requests, annotations, projects), so no
  champion or edge is left pointing at nothing."""

from __future__ import annotations

import argparse
import os
import shutil
from typing import Any

from . import config
from .db import Database
from .lake.migrate import LEGACY_TABLES

#: deleted by a reset (the audit_log, admin_requests and catalog_meta are kept)
TABLES = ("run_events", "runs", "batch_jobs", "model_versions", "registered_models",
          # P01: what refers to datasets / projects goes first (foreign keys); teams, principals and blobs are kept
          "run_inputs", "join_frames", "joins", "splits", "table_relations", "table_keys", "table_files", "version_tables",
          "dataset_root_tables", "dataset_roots", "uploads", "certifications", "column_tags", "data_contracts", "datasets",
          "lineage_edges", "champions", "dataset_cards", "dataset_requests", "version_requests", "annotations", "projects",
          "lake_migrations", *LEGACY_TABLES)
FOLDERS = ("runs", "registry", "mlflow", "autopilot", "agent", "datasets")


class ResetRefused(Exception):
    def __init__(self, status: int, message: Any) -> None:
        super().__init__(message if isinstance(message, str) else str(message))
        self.status, self.payload = status, message


def demo_mode() -> bool:
    return os.environ.get("ENSEMBLE_DEMO_MODE", "").strip().lower() in ("1", "true", "yes")


def live_governance(con) -> list[str]:
    """What a reset would destroy that governance protects: live registered versions and champions."""
    out = [f"registered version {r['model_name']} v{r['version']} ({r['approval']}, {r['stage'] or 'no stage'}) is live: retire or reject it"
           for r in con.execute("SELECT model_name, version, approval, stage FROM model_versions WHERE retired_at IS NULL"
                                " AND COALESCE(approval, '') != 'rejected' AND COALESCE(status, '') != 'failed'")]
    try:
        out += [f"champion {r['id']} of project {r['project_id']} is {r['status']}" for r in
                con.execute("SELECT id, project_id, status FROM champions WHERE status IN ('approved', 'pending')")]
    except Exception:  # noqa: BLE001 - no catalog tables yet
        pass
    return out


def user_data(con) -> list[str]:
    """DL4: what a reset would wipe that a person made: datasets (every version) and runs. Any of it needs four eyes."""
    out = []
    try:
        n_ds = int(con.execute("SELECT COUNT(*) FROM datasets").fetchone()[0])
        n_runs = int(con.execute("SELECT COUNT(*) FROM runs").fetchone()[0])
    except Exception:  # noqa: BLE001 - no tables yet
        return out
    if n_ds:
        out.append(f"{n_ds} user dataset version(s) (uploads, edits and other versions) would be deleted")
    if n_runs:
        out.append(f"{n_runs} run(s) would be deleted")
    return out


def holds_refusal(con) -> dict[str, Any] | None:
    """F-X4b (X4 M4): the 409 body when any legal hold is active (P24's rule: a destructive path calls catalog/holds)."""
    from .catalog import holds as H

    hs = [dict(r) for r in con.execute("SELECT * FROM legal_holds WHERE released_at IS NULL ORDER BY placed_at")]
    return H.refusal(hs, "reset the workbench (it deletes every team's data)") if hs else None


def check(db: Database, by: str | None, request_id: str | None = None) -> dict[str, Any] | None:
    """Raise ResetRefused unless this reset may run; returns the four-eyes approval it uses (or None)."""
    from .catalog import governance as G

    if not demo_mode():
        raise ResetRefused(403, "Reset is disabled: it deletes every run, model and upload. It is a demo / local-dev tool, "
                                "on only when the API runs with ENSEMBLE_DEMO_MODE=1.")
    if not by or not G.identity_key(by):
        raise ResetRefused(400, "Say who is resetting: by (an admin from ENSEMBLE_ADMINS).")
    if not G.is_admin(by):
        raise ResetRefused(403, f"Only an admin (ENSEMBLE_ADMINS) may reset; {by} is not one.")
    with db.connect() as con:
        held = holds_refusal(con)
        if held:
            raise ResetRefused(409, held)
        live = live_governance(con) + user_data(con)
        if not live and not request_id:
            return None
        # §4.4 (DLA4): a request_id that is sent is always checked and returned, so the reset marks it used even when
        # nothing is live any more (before, it stayed 'approved': reusable, and it blocked every new request)
        if not request_id:
            raise ResetRefused(409, {"message": "Can't reset: user data, live models or champions would be deleted. Get a "
                                                "four-eyes approval (POST /api/admin/reset-requests {by, reason}; someone else "
                                                "approves) and send its request_id.", "blockers": live})
        r = con.execute("SELECT * FROM admin_requests WHERE id = ? AND action = 'reset'", (request_id,)).fetchone()
        if not r:
            raise ResetRefused(404, f"No reset request '{request_id}'.")
        if r["status"] != "approved":
            raise ResetRefused(409, f"Reset request {request_id} is {r['status']}, not approved.")
        if not (G.same_person(by, r["requested_by"]) or G.same_person(by, r["decided_by"])):
            raise ResetRefused(403, f"Reset request {request_id} was asked by {r['requested_by']} and approved by {r['decided_by']}: "
                                    "one of them runs it.")
        return dict(r)


def reset(settings: config.Settings, by: str | None = None, request_id: str | None = None, reason: str = "",
          via: str = "api") -> dict[str, Any]:
    from .catalog.schema import audit, now

    db = Database(settings.db_path)  # migrates: the admin_requests table exists
    approval = check(db, by, request_id)
    counts = {}
    with db.connect() as con:
        held = holds_refusal(con)  # again, in the delete transaction: a hold placed since check() still stops it
        if held:
            raise ResetRefused(409, held)
        live = live_governance(con)
        for t in TABLES:
            # a table this build doesn't have is skipped (checked first: a failed statement aborts a Postgres transaction)
            if not con.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (t,)).fetchone():
                continue
            counts[t] = int(con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0])
        audit_rows = int(con.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0])
        # the audit row goes in the same transaction as the deletes: no reset without its record
        audit(con, by, "admin_reset", comment=reason[:500],
              details={"deleted": counts, "audit_rows_kept": audit_rows, "via": via, "live_at_reset": live,
                       "four_eyes": ({k: approval.get(k) for k in ("id", "requested_by", "decided_by", "reason")} if approval else None),
                       "demo_mode": True})
        con.execute("DROP TRIGGER IF EXISTS splits_ro_d")  # P01: splits are immutable except to a reset
        con.execute("DROP TRIGGER IF EXISTS ri_ro_d")  # F-X1 M6: so are run inputs
        for t in counts:
            con.execute(f"DELETE FROM {t}")
        from .catalog.schema import IMMUTABLE_TRIGGERS

        con.execute(IMMUTABLE_TRIGGERS["splits_ro_d"])
        con.execute(IMMUTABLE_TRIGGERS["ri_ro_d"])
        if approval:
            con.execute("UPDATE admin_requests SET status = 'used', used_at = ?, used_by = ? WHERE id = ?", (now(), by, approval["id"]))
    for f in FOLDERS:
        shutil.rmtree(settings.data_dir / f, ignore_errors=True)
    settings.runs_dir.mkdir(parents=True, exist_ok=True)
    settings.datasets_dir.mkdir(parents=True, exist_ok=True)
    return {"deleted": counts, "data_dir": str(settings.data_dir), "audit_kept": True, "by": by,
            "four_eyes": approval["id"] if approval else None}


if __name__ == "__main__":
    import json
    import sys

    ap = argparse.ArgumentParser(description="Reset the workbench (offline). Needs ENSEMBLE_DEMO_MODE=1 and an admin name.")
    ap.add_argument("--by", default=os.environ.get("BY", ""), help="an admin from ENSEMBLE_ADMINS")
    ap.add_argument("--request", default=None, help="an approved four-eyes reset request id (needed while models are live)")
    ap.add_argument("--reason", default="")
    a = ap.parse_args()
    try:
        out = reset(config.load_settings(), by=a.by, request_id=a.request, reason=a.reason, via="cli")
    except ResetRefused as e:
        db = Database(config.load_settings().db_path)
        try:
            from .catalog import governance as G

            G.refused(db, a.by or "unknown", "admin_reset", e.status, e.payload, {"via": "cli"})
        except Exception:  # noqa: BLE001
            pass
        print(json.dumps({"refused": e.status, "detail": e.payload}, indent=1), file=sys.stderr)
        sys.exit(1)
    print(json.dumps(out, indent=1))

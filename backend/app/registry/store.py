"""Registered models, their versions, the audit log and batch-scoring jobs, in the app's SQLite database.
Used by the API process and by the worker processes that build versions and score batches."""

from __future__ import annotations

import json
from typing import Any

from ..db import Database
from ..engine.jsonsafe import jsonable
from ..engine.store import now

STAGES = ("None", "Staging", "Production", "Archived")
JSON_COLS = {"model_versions": ("options", "progress", "result", "card"), "audit_log": ("details",),
             "batch_jobs": ("result",)}


def _dump(v: Any) -> str | None:
    return None if v is None else json.dumps(jsonable(v))


class RegistryStore:
    def __init__(self, db: Database) -> None:
        self.db = db

    @staticmethod
    def _row(table: str, r) -> dict[str, Any]:
        d = dict(r)
        for c in JSON_COLS.get(table, ()):
            if d.get(c) is not None:
                d[c] = json.loads(d[c])
        return d

    def _get(self, table: str, id_: str) -> dict[str, Any] | None:
        with self.db.connect() as con:
            r = con.execute(f"SELECT * FROM {table} WHERE id = ?", (id_,)).fetchone()
        return self._row(table, r) if r else None

    def _update(self, table: str, id_: str, fields: dict[str, Any]) -> None:
        for c in JSON_COLS.get(table, ()):
            if c in fields:
                fields[c] = _dump(fields[c])
        cols = ", ".join(f"{k} = :{k}" for k in fields)
        with self.db.connect() as con:
            con.execute(f"UPDATE {table} SET {cols} WHERE id = :_id", {**fields, "_id": id_})

    def _merge(self, table: str, id_: str, parts: dict[str, Any]) -> None:
        with self.db.connect() as con:
            r = con.execute(f"SELECT result FROM {table} WHERE id = ?", (id_,)).fetchone()
            res = json.loads(r["result"]) if r and r["result"] else {}
            res.update(jsonable(parts))
            con.execute(f"UPDATE {table} SET result = ? WHERE id = ?", (json.dumps(res), id_))

    # -- registered models ---------------------------------------------------

    def ensure_model(self, name: str, dataset_id: str, label: str, description: str = "") -> bool:
        with self.db.connect() as con:
            cur = con.execute("INSERT OR IGNORE INTO registered_models (name, created_at, description, dataset_id, label)"
                              " VALUES (?, ?, ?, ?, ?)", (name, now(), description, dataset_id, label))
            return cur.rowcount > 0

    def get_model(self, name: str) -> dict[str, Any] | None:
        with self.db.connect() as con:
            r = con.execute("SELECT * FROM registered_models WHERE name = ?", (name,)).fetchone()
        return dict(r) if r else None

    def list_models(self) -> list[dict[str, Any]]:
        with self.db.connect() as con:
            return [dict(r) for r in con.execute("SELECT * FROM registered_models ORDER BY created_at, rowid")]

    # -- versions ------------------------------------------------------------

    def create_version(self, rec: dict[str, Any]) -> dict[str, Any]:
        """Assigns the next version number of the model atomically."""
        with self.db.connect() as con:
            n = con.execute("SELECT COALESCE(MAX(version), 0) + 1 FROM model_versions WHERE model_name = ?",
                            (rec["model_name"],)).fetchone()[0]
            con.execute(
                "INSERT INTO model_versions (id, model_name, version, run_id, status, created_at, created_by, options, progress, card)"
                " VALUES (?, ?, ?, ?, 'building', ?, ?, ?, ?, ?)",
                (rec["id"], rec["model_name"], n, rec["run_id"], now(), rec["created_by"], _dump(rec["options"]),
                 _dump({"steps": []}), _dump(rec.get("card") or {})))
        v = self.get_version(rec["id"])
        from ..catalog.store import Catalog  # P1: the version's built_from edge to its run

        Catalog(self.db).record_build(v)
        return v

    def get_version(self, vid: str) -> dict[str, Any] | None:
        return self._get("model_versions", vid)

    def list_versions(self, model_name: str | None = None) -> list[dict[str, Any]]:
        q = "SELECT * FROM model_versions"
        args: tuple = ()
        if model_name:
            q += " WHERE model_name = ?"
            args = (model_name,)
        with self.db.connect() as con:
            rows = con.execute(q + " ORDER BY created_at DESC, rowid DESC", args).fetchall()
        return [self._row("model_versions", r) for r in rows]

    def update_version(self, vid: str, **fields: Any) -> None:
        self._update("model_versions", vid, fields)

    def merge_version_result(self, vid: str, **parts: Any) -> None:
        self._merge("model_versions", vid, parts)

    def step(self, vid: str, name: str, status: str, **info: Any) -> None:
        """Record a build step (started / done / skipped / failed) for the UI's progress list."""
        with self.db.connect() as con:
            r = con.execute("SELECT progress FROM model_versions WHERE id = ?", (vid,)).fetchone()
            prog = json.loads(r["progress"]) if r and r["progress"] else {"steps": []}
            steps = prog["steps"]
            cur = next((s for s in steps if s["name"] == name), None)
            if cur is None:
                cur = {"name": name}
                steps.append(cur)
            cur.update(jsonable({"status": status, "ts": now(), **info}))
            con.execute("UPDATE model_versions SET progress = ? WHERE id = ?", (json.dumps(prog), vid))

    # -- audit ---------------------------------------------------------------

    def audit(self, *, actor: str, action: str, version: dict[str, Any] | None = None, from_stage: str | None = None,
              to_stage: str | None = None, comment: str = "", details: dict[str, Any] | None = None,
              run_id: str | None = None) -> dict[str, Any]:
        with self.db.connect() as con:
            cur = con.execute(
                "INSERT INTO audit_log (ts, actor, action, model_name, version, version_id, run_id, from_stage, to_stage,"
                " comment, model_hash, details) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (now(), actor, action, version and version["model_name"], version and version["version"],
                 version and version["id"], run_id or (version and version["run_id"]), from_stage, to_stage, comment or "",
                 version and version.get("model_hash"), _dump(details)))
            r = con.execute("SELECT * FROM audit_log WHERE id = ?", (cur.lastrowid,)).fetchone()
        return self._row("audit_log", r)

    def audit_log(self, *, model_name: str | None = None, version_id: str | None = None, limit: int = 200) -> list[dict[str, Any]]:
        q, args = "SELECT * FROM audit_log", []
        conds = []
        if model_name:
            conds.append("model_name = ?")
            args.append(model_name)
        if version_id:
            conds.append("version_id = ?")
            args.append(version_id)
        if conds:
            q += " WHERE " + " AND ".join(conds)
        with self.db.connect() as con:
            rows = con.execute(q + " ORDER BY id DESC LIMIT ?", (*args, limit)).fetchall()
        return [self._row("audit_log", r) for r in rows]

    # -- batch jobs ------------------------------------------------------------

    def create_batch(self, rec: dict[str, Any]) -> None:
        with self.db.connect() as con:
            con.execute("INSERT INTO batch_jobs (id, version_id, status, created_at, created_by, filename, n_rows, data_date)"
                        " VALUES (?, ?, 'queued', ?, ?, ?, ?, ?)",
                        (rec["id"], rec["version_id"], now(), rec.get("created_by") or "", rec["filename"], rec.get("n_rows"),
                         rec.get("data_date")))

    def get_batch(self, bid: str) -> dict[str, Any] | None:
        return self._get("batch_jobs", bid)

    def list_batches(self, version_id: str | None = None) -> list[dict[str, Any]]:
        q, args = "SELECT * FROM batch_jobs", ()
        if version_id:
            q, args = q + " WHERE version_id = ?", (version_id,)
        with self.db.connect() as con:
            return [self._row("batch_jobs", r) for r in con.execute(q + " ORDER BY created_at DESC, rowid DESC", args)]

    def update_batch(self, bid: str, **fields: Any) -> None:
        self._update("batch_jobs", bid, fields)

    def merge_batch_result(self, bid: str, **parts: Any) -> None:
        """F18: add parts (labels, shadow) to a finished batch's result without rewriting the rest."""
        self._merge("batch_jobs", bid, parts)

    def recover(self) -> None:
        msg = "The API stopped while this was in progress."
        with self.db.connect() as con:
            # P22: builds and batches on the durable job queue keep going (a worker runs them); only orphans fail
            live = "SELECT ref_id FROM jobs WHERE ref_kind = ? AND status IN ('queued', 'leased')"
            con.execute(f"UPDATE model_versions SET status = 'failed', error = ? WHERE status = 'building' AND id NOT IN ({live})",
                        (msg, "model_version"))
            con.execute("UPDATE batch_jobs SET status = 'failed', error = ?, stage = 'failed' WHERE status IN ('queued', 'running')"
                        f" AND id NOT IN ({live})", (msg, "batch"))

    # -- summary ---------------------------------------------------------------

    def counts(self) -> dict[str, Any]:
        with self.db.connect() as con:
            stages = {s: 0 for s in STAGES}
            for r in con.execute("SELECT stage, COUNT(*) n FROM model_versions WHERE status = 'ready' GROUP BY stage"):
                stages[r["stage"]] = r["n"]
            approvals = {a: 0 for a in ("pending", "approved", "rejected")}
            for r in con.execute("SELECT approval, COUNT(*) n FROM model_versions WHERE status = 'ready' GROUP BY approval"):
                approvals[r["approval"]] = r["n"]
            building = con.execute("SELECT COUNT(*) FROM model_versions WHERE status = 'building'").fetchone()[0]
            models = con.execute("SELECT COUNT(*) FROM registered_models").fetchone()[0]
            versions = con.execute("SELECT COUNT(*) FROM model_versions").fetchone()[0]
            batches = con.execute("SELECT COUNT(*) FROM batch_jobs").fetchone()[0]
        return {"models": models, "versions": versions, "building": building, "stages": stages,
                "approvals": approvals, "batches": batches}

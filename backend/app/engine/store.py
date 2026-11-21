"""Runs and their events in SQLite. Used by the API process and by each worker process."""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from typing import Any

from ..db import Database
from .jsonsafe import jsonable

ACTIVE = ("queued", "running")
TERMINAL_EVENTS = ("run_succeeded", "run_failed", "run_cancelled")


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _primary(con, run_id: str, result: dict[str, Any]) -> None:
    """P1: keep the indexed primary_metric / primary_score in step with the result JSON."""
    from ..catalog.schema import primary

    m, v, g = primary(result)
    if m is not None:
        con.execute("UPDATE runs SET primary_metric = ?, primary_score = ?, greater_is_better = ? WHERE id = ?", (m, v, g, run_id))


def _extra_edges(con, run_id: str, items: list[Any]) -> None:
    from ..catalog.schema import add_edge

    for e in items or []:
        if isinstance(e, dict) and e.get("run_id"):
            add_edge(con, ("run", run_id), ("run", e["run_id"]), "branched_from",
                     meta={k: v for k, v in {"source": "doctor-experiment", "experiment": e.get("kind"), "patch": e.get("title"),
                                             "review_id": e.get("review_id")}.items() if v})
            con.execute("UPDATE runs SET parent_run_id = COALESCE(parent_run_id, ?), source = CASE WHEN source IS NULL OR source = 'user'"
                        " THEN 'doctor-experiment' ELSE source END WHERE id = ?", (run_id, e["run_id"]))


class RunStore:
    def __init__(self, db: Database) -> None:
        self.db = db

    # -- runs ---------------------------------------------------------------

    def create(self, rec: dict[str, Any]) -> None:
        with self.db.connect() as con:
            con.execute(
                "INSERT INTO runs (id, name, dataset_id, label, status, created_at, config)"
                " VALUES (:id, :name, :dataset_id, :label, 'queued', :created_at, :config)",
                {**rec, "config": json.dumps(jsonable(rec["config"]))},
            )
        from ..catalog.store import Catalog  # P1: indexed columns, trained_on / branched_from edges

        Catalog(self.db).record_run(rec["id"], rec["dataset_id"], rec["config"], actor=(rec["config"].get("patch") or {}).get("by"))

    def get(self, run_id: str) -> dict[str, Any] | None:
        with self.db.connect() as con:
            r = con.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
        return self._row(r) if r else None

    #: P1: indexed columns only, no config / result JSON
    LIGHT = ("id", "name", "dataset_id", "label", "status", "created_at", "started_at", "finished_at", "error", "project_id",
             "parent_run_id", "source", "kind", "primary_metric", "primary_score", "greater_is_better", "predictor_state",
             "predictor_bytes")

    @staticmethod
    def _where(**filters: Any) -> tuple[str, list[Any]]:
        conds, args = [], []
        for k, v in filters.items():
            if v is None:
                continue
            if isinstance(v, (list, tuple, set)):
                conds.append(f"{k} IN ({','.join('?' * len(v))})")
                args.extend(v)
            else:
                conds.append(f"{k} = ?")
                args.append(v)
        return (" WHERE " + " AND ".join(conds)) if conds else "", args

    def count_where(self, **filters: Any) -> int:
        w, args = self._where(**filters)
        with self.db.connect() as con:
            return int(con.execute(f"SELECT COUNT(*) FROM runs{w}", args).fetchone()[0])

    def list(self, limit: int | None = None, offset: int = 0, **filters: Any) -> list[dict[str, Any]]:
        """Full rows (config and result parsed), newest first. P1: filters on the indexed columns (dataset_id, status,
        project_id, parent_run_id, source, kind; a list means IN) run in SQL, so callers parse only the rows they need."""
        w, args = self._where(**filters)
        with self.db.connect() as con:
            if limit is None:
                rows = con.execute(f"SELECT * FROM runs{w} ORDER BY created_at DESC, id DESC", args).fetchall()
            else:  # F-P2: pick the page's ids on the narrow columns first, so the sort never reads every result JSON
                rows = con.execute(f"SELECT * FROM runs WHERE id IN (SELECT id FROM runs{w} ORDER BY created_at DESC, id DESC"
                                   f" LIMIT {int(limit)} OFFSET {int(offset)}) ORDER BY created_at DESC, id DESC", args).fetchall()
        return [self._row(r) for r in rows]

    def list_light(self, limit: int | None = None, offset: int = 0, **filters: Any) -> list[dict[str, Any]]:
        """P1: the indexed columns only (no JSON parsed), newest first: the list path for thousands of runs."""
        w, args = self._where(**filters)
        q = f"SELECT {', '.join(self.LIGHT)} FROM runs{w} ORDER BY created_at DESC, id DESC"
        if limit is not None:
            q += " LIMIT ? OFFSET ?"
            args += [int(limit), int(offset)]
        with self.db.connect() as con:
            return [dict(r) for r in con.execute(q, args).fetchall()]

    def children(self, run_id: str, source: str | None = None) -> list[dict[str, Any]]:
        """P1: runs branched from `run_id`, from the lineage edges (no scan of every run)."""
        from ..catalog.store import Catalog

        return Catalog(self.db).children(run_id, source)

    def count(self) -> int:
        with self.db.connect() as con:
            return int(con.execute("SELECT COUNT(*) FROM runs").fetchone()[0])

    def list_active(self) -> list[dict[str, Any]]:
        with self.db.connect() as con:
            rows = con.execute("SELECT * FROM runs WHERE status IN ('queued', 'running') ORDER BY created_at, id").fetchall()
        return [self._row(r) for r in rows]

    def update(self, run_id: str, **fields: Any) -> None:
        if "result" in fields and fields["result"] is not None:
            fields["result"] = json.dumps(jsonable(fields["result"]))
        cols = ", ".join(f"{k} = :{k}" for k in fields)
        with self.db.connect() as con:
            con.execute(f"UPDATE runs SET {cols} WHERE id = :_id", {**fields, "_id": run_id})
            if fields.get("result") is not None:
                _primary(con, run_id, json.loads(fields["result"]))

    def mark_running(self, run_id: str, pid: int) -> None:
        with self.db.connect() as con:
            con.execute("UPDATE runs SET status = 'running', pid = ?, started_at = ? WHERE id = ? AND status = 'queued'",
                        (pid, now(), run_id))

    def merge_result(self, run_id: str, **parts: Any) -> dict[str, Any]:
        """Update keys of the stored result (a JSON object) without touching the others."""
        with self.db.connect() as con:
            r = con.execute("SELECT result FROM runs WHERE id = ?", (run_id,)).fetchone()
            result = json.loads(r["result"]) if r and r["result"] else {}
            result.update(jsonable(parts))
            con.execute("UPDATE runs SET result = ? WHERE id = ?", (json.dumps(result), run_id))
            if "holdout" in parts or "eval_metric" in parts:
                _primary(con, run_id, result)
            if parts.get("experiments_extra"):  # P1: segment / group-split experiment runs recorded on the parent
                _extra_edges(con, run_id, parts["experiments_extra"])
        return result

    def finish(self, run_id: str, status: str, error: str | None = None, only_if_active: bool = True) -> bool:
        """Set a terminal status. Returns False (and changes nothing) if the run had already finished."""
        q = "UPDATE runs SET status = ?, error = COALESCE(?, error), finished_at = ? WHERE id = ?"
        if only_if_active:
            q += " AND status IN ('queued', 'running')"
        with self.db.connect() as con:
            cur = con.execute(q, (status, error, now(), run_id))
            if cur.rowcount > 0:  # F-P2: 'present' only once a predictor folder exists; 'none' otherwise (never 'evicted')
                r = con.execute("SELECT json_extract(config, '$.predictor_path') AS pp, predictor_state FROM runs WHERE id = ?",
                                (run_id,)).fetchone()
                if r and r["predictor_state"] in (None, "none", "present"):
                    from pathlib import Path

                    has = bool(r["pp"]) and Path(r["pp"]).exists()
                    con.execute("UPDATE runs SET predictor_state = ? WHERE id = ?", ("present" if has else "none", run_id))
            return cur.rowcount > 0

    @staticmethod
    def _row(r) -> dict[str, Any]:
        d = dict(r)
        d["config"] = json.loads(d["config"])
        d["result"] = json.loads(d["result"]) if d["result"] else None
        return d

    # -- events -------------------------------------------------------------

    def emit(self, run_id: str, type_: str, /, **data: Any) -> int:
        """Append an event (run_id and type_ are positional-only, so a payload may carry its own `run_id`). seq is assigned atomically inside the INSERT, so several writers stay ordered."""
        ts = time.time()
        payload = json.dumps(jsonable(data))
        with self.db.connect() as con:
            seq = con.execute(
                "INSERT INTO run_events (run_id, seq, ts, type, data)"
                " SELECT ?, COALESCE(MAX(seq) + 1, 0), ?, ?, ? FROM run_events WHERE run_id = ? RETURNING seq",
                (run_id, ts, type_, payload, run_id),
            ).fetchone()[0]
        return int(seq)

    def events(self, run_id: str, start: int = 0, limit: int = 1000) -> list[dict[str, Any]]:
        with self.db.connect() as con:
            rows = con.execute(
                "SELECT seq, ts, type, data FROM run_events WHERE run_id = ? AND seq >= ? ORDER BY seq LIMIT ?",
                (run_id, start, limit),
            ).fetchall()
        return [{"seq": r["seq"], "ts": r["ts"], "type": r["type"], **json.loads(r["data"])} for r in rows]

    def count_events(self, run_id: str) -> int:
        with self.db.connect() as con:
            return int(con.execute("SELECT COUNT(*) FROM run_events WHERE run_id = ?", (run_id,)).fetchone()[0])

    def has_terminal_event(self, run_id: str) -> bool:
        with self.db.connect() as con:
            q = f"SELECT 1 FROM run_events WHERE run_id = ? AND type IN ({','.join('?' * len(TERMINAL_EVENTS))}) LIMIT 1"
            return con.execute(q, (run_id, *TERMINAL_EVENTS)).fetchone() is not None

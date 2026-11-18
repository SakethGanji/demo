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

    def get(self, run_id: str) -> dict[str, Any] | None:
        with self.db.connect() as con:
            r = con.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
        return self._row(r) if r else None

    def list(self) -> list[dict[str, Any]]:
        with self.db.connect() as con:
            rows = con.execute("SELECT * FROM runs ORDER BY created_at DESC, rowid DESC").fetchall()
        return [self._row(r) for r in rows]

    def count(self) -> int:
        with self.db.connect() as con:
            return int(con.execute("SELECT COUNT(*) FROM runs").fetchone()[0])

    def list_active(self) -> list[dict[str, Any]]:
        with self.db.connect() as con:
            rows = con.execute("SELECT * FROM runs WHERE status IN ('queued', 'running') ORDER BY rowid").fetchall()
        return [self._row(r) for r in rows]

    def update(self, run_id: str, **fields: Any) -> None:
        if "result" in fields and fields["result"] is not None:
            fields["result"] = json.dumps(jsonable(fields["result"]))
        cols = ", ".join(f"{k} = :{k}" for k in fields)
        with self.db.connect() as con:
            con.execute(f"UPDATE runs SET {cols} WHERE id = :_id", {**fields, "_id": run_id})

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
        return result

    def finish(self, run_id: str, status: str, error: str | None = None, only_if_active: bool = True) -> bool:
        """Set a terminal status. Returns False (and changes nothing) if the run had already finished."""
        q = "UPDATE runs SET status = ?, error = COALESCE(?, error), finished_at = ? WHERE id = ?"
        if only_if_active:
            q += " AND status IN ('queued', 'running')"
        with self.db.connect() as con:
            cur = con.execute(q, (status, error, now(), run_id))
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

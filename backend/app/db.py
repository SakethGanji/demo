"""SQLite through the standard library. One short-lived connection per call keeps it thread-safe."""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

SCHEMA = """
CREATE TABLE IF NOT EXISTS datasets (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    filename    TEXT NOT NULL,
    source      TEXT NOT NULL CHECK (source IN ('sample', 'upload')),
    note        TEXT NOT NULL DEFAULT '',
    created_at  TEXT NOT NULL,
    size_bytes  INTEGER NOT NULL,
    rows        INTEGER NOT NULL,
    target      TEXT,
    kind        TEXT NOT NULL,
    profile     TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS runs (
    id           TEXT PRIMARY KEY,
    name         TEXT NOT NULL,
    dataset_id   TEXT NOT NULL,
    label        TEXT NOT NULL,
    status       TEXT NOT NULL CHECK (status IN ('queued', 'running', 'succeeded', 'failed', 'cancelled')),
    created_at   TEXT NOT NULL,
    started_at   TEXT,
    finished_at  TEXT,
    pid          INTEGER,
    error        TEXT,
    config       TEXT NOT NULL,
    result       TEXT
);

-- Every run's progress as an append-only list; seq starts at 0 per run. SSE replays from any seq.
CREATE TABLE IF NOT EXISTS run_events (
    run_id  TEXT NOT NULL,
    seq     INTEGER NOT NULL,
    ts      REAL NOT NULL,
    type    TEXT NOT NULL,
    data    TEXT NOT NULL,
    PRIMARY KEY (run_id, seq)
);
"""


class Database:
    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as con:
            con.executescript(SCHEMA)

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        con = sqlite3.connect(self.path, timeout=30)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("PRAGMA foreign_keys=ON")
        try:
            yield con
            con.commit()
        finally:
            con.close()

    # -- datasets ---------------------------------------------------------

    def insert_dataset(self, rec: dict[str, Any]) -> None:
        with self.connect() as con:
            con.execute(
                "INSERT OR REPLACE INTO datasets (id, name, filename, source, note, created_at, size_bytes, rows, target, kind, profile)"
                " VALUES (:id, :name, :filename, :source, :note, :created_at, :size_bytes, :rows, :target, :kind, :profile)",
                {**rec, "profile": json.dumps(rec["profile"])},
            )

    def list_datasets(self) -> list[dict[str, Any]]:
        with self.connect() as con:
            rows = con.execute("SELECT * FROM datasets ORDER BY created_at, rowid").fetchall()
        return [self._row(r) for r in rows]

    def get_dataset(self, dataset_id: str) -> dict[str, Any] | None:
        with self.connect() as con:
            r = con.execute("SELECT * FROM datasets WHERE id = ?", (dataset_id,)).fetchone()
        return self._row(r) if r else None

    @staticmethod
    def _row(r: sqlite3.Row) -> dict[str, Any]:
        d = dict(r)
        d["profile"] = json.loads(d["profile"])
        return d

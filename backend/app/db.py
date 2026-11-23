"""The metadata DB. SQLite through the standard library by default (local dev): one short-lived connection per call keeps
it thread-safe. P23: with ENSEMBLE_DB_URL=postgresql://… (read from the environment only, never stored) the same
`connect()` hands out pooled Postgres connections through the dialect layer in db_pg.py."""

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

-- The model registry (S5). MLflow's registry mirrors it (see app/registry/tracking.py); this is what the UI reads.
CREATE TABLE IF NOT EXISTS registered_models (
    name         TEXT PRIMARY KEY,
    created_at   TEXT NOT NULL,
    description  TEXT NOT NULL DEFAULT '',
    dataset_id   TEXT NOT NULL,
    label        TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS model_versions (
    id           TEXT PRIMARY KEY,
    model_name   TEXT NOT NULL REFERENCES registered_models(name),
    version      INTEGER NOT NULL,
    run_id       TEXT NOT NULL,
    status       TEXT NOT NULL CHECK (status IN ('building', 'ready', 'failed')),
    stage        TEXT NOT NULL DEFAULT 'None' CHECK (stage IN ('None', 'Staging', 'Production', 'Archived')),
    approval     TEXT NOT NULL DEFAULT 'pending' CHECK (approval IN ('pending', 'approved', 'rejected')),
    created_at   TEXT NOT NULL,
    created_by   TEXT NOT NULL,
    options      TEXT NOT NULL,
    progress     TEXT,
    result       TEXT,
    card         TEXT,
    error        TEXT,
    path         TEXT,
    model_hash   TEXT,
    UNIQUE (model_name, version)
);

-- Append-only: who did what to which version, when, with the model's hash at that moment.
CREATE TABLE IF NOT EXISTS audit_log (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    ts           TEXT NOT NULL,
    actor        TEXT NOT NULL,
    action       TEXT NOT NULL,
    model_name   TEXT,
    version      INTEGER,
    version_id   TEXT,
    run_id       TEXT,
    from_stage   TEXT,
    to_stage     TEXT,
    comment      TEXT NOT NULL DEFAULT '',
    model_hash   TEXT,
    details      TEXT
);

CREATE TABLE IF NOT EXISTS batch_jobs (
    id           TEXT PRIMARY KEY,
    version_id   TEXT NOT NULL,
    status       TEXT NOT NULL CHECK (status IN ('queued', 'running', 'succeeded', 'failed')),
    created_at   TEXT NOT NULL,
    finished_at  TEXT,
    created_by   TEXT NOT NULL DEFAULT '',
    filename     TEXT NOT NULL,
    n_rows       INTEGER,
    progress     REAL NOT NULL DEFAULT 0,
    stage        TEXT NOT NULL DEFAULT 'queued',
    result       TEXT,
    error        TEXT
);
"""


#: P08: the columns of a list row, with the two profile fields the list shows (json_extract, not the whole profile)
LIGHT_COLS = ("id, name, filename, source, note, created_at, size_bytes, rows, target, kind, project_id, parent_id, root_id, op, state,"
              " COALESCE(n_cols, CAST(json_extract(profile, '$.n_cols') AS INTEGER)) AS _n_cols,"
              " CAST(json_extract(profile, '$.n_classes') AS INTEGER) AS _n_classes")

_pg_migrated: set[str] = set()


class Database:
    def __init__(self, path: Path, url: str | None = None) -> None:
        from . import db_pg

        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        self.url = url if url is not None else db_pg.env_url()
        self.dialect = "postgres" if db_pg.is_pg_url(self.url) else "sqlite"
        if self.dialect == "postgres":
            self._migrate_pg()
            return
        with self.connect() as con:
            con.executescript(SCHEMA)
            # F18: a scored batch carries the date of its data (monitoring over time). Added to older databases in place.
            cols = {r[1] for r in con.execute("PRAGMA table_info(batch_jobs)")}
            if "data_date" not in cols:
                con.execute("ALTER TABLE batch_jobs ADD COLUMN data_date TEXT")
            from .catalog.schema import migrate  # P1: projects, lineage edges, annotations, champions, indexed run columns

            migrate(con)
            # P08: the dataset list's order and count without reading rows (state is a late column, after profile)
            con.execute("CREATE INDEX IF NOT EXISTS ix_datasets_list ON datasets (state, created_at, id)")

    def _migrate_pg(self) -> None:
        """Once per process and URL: the §2.2 schema, as the owner role when ENSEMBLE_DB_OWNER_URL is set (then the app
        role named by ENSEMBLE_DB_APP_ROLE gets the §2.2 grants)."""
        import os

        from . import db_pg
        from .catalog import pg_schema

        if self.url in _pg_migrated:
            return
        owner = pg_schema.owner_url()
        role = (os.environ.get("ENSEMBLE_DB_APP_ROLE") or "").strip() or None
        if owner:
            with db_pg.direct(owner) as con:
                pg_schema.migrate(con, app_role=role)
        else:
            with self.connect() as con:
                pg_schema.migrate(con)
        _pg_migrated.add(self.url)

    @contextmanager
    def admin_connect(self) -> Iterator[sqlite3.Connection]:
        """LB-PG: schema-level startup work (the backfills, which create the audit triggers and may UPDATE append-only
        tables) runs as the owner role on Postgres when ENSEMBLE_DB_OWNER_URL is set; the app role has no such rights."""
        if self.dialect == "postgres":
            from . import db_pg
            from .catalog import pg_schema

            owner = pg_schema.owner_url()
            if owner:
                with db_pg.direct(owner) as pcon:
                    yield pcon  # type: ignore[misc]
                return
        with self.connect() as con:
            yield con

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        if self.dialect == "postgres":
            from . import db_pg

            with db_pg.connect(self.url) as pcon:
                yield pcon  # type: ignore[misc]
            return
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
                "INSERT INTO datasets (id, name, filename, source, note, created_at, size_bytes, rows, target, kind, profile)"
                " VALUES (:id, :name, :filename, :source, :note, :created_at, :size_bytes, :rows, :target, :kind, :profile)"
                # an upsert, not OR REPLACE: P1's columns (project, parent, root, sha256) survive a re-profile
                " ON CONFLICT (id) DO UPDATE SET name = excluded.name, filename = excluded.filename, source = excluded.source,"
                " note = excluded.note, created_at = excluded.created_at, size_bytes = excluded.size_bytes, rows = excluded.rows,"
                " target = excluded.target, kind = excluded.kind, profile = excluded.profile",
                {**rec, "profile": json.dumps(rec["profile"])},
            )

    def list_datasets(self, light: bool = False) -> list[dict[str, Any]]:
        """Every version. P08: `light` reads only the list's columns and two profile fields (n_cols, n_classes), not
        the whole profile JSON of each row."""
        if light:
            with self.connect() as con:
                rows = con.execute(f"SELECT {LIGHT_COLS} FROM datasets ORDER BY created_at, id").fetchall()
            return [self._light(r) for r in rows]
        with self.connect() as con:
            rows = con.execute("SELECT * FROM datasets ORDER BY created_at, id").fetchall()
        return [self._row(r) for r in rows]

    def count_datasets(self) -> int:
        with self.connect() as con:
            return int(con.execute("SELECT count(*) FROM datasets").fetchone()[0])

    def list_datasets_page(self, *, limit: int, after: str | None = None, include_hidden: bool = False,
                           project_id: str | None = None, root_id: str | None = None) -> dict[str, Any]:
        """P08: one page of the dataset list in the list's order (created_at, id), by keyset: `after` is the previous
        page's `next_after` (an opaque cursor). The ordering and the count use the covering index ix_datasets_list
        (state, created_at, id), so no row's profile is read except the page's own n_cols / n_classes."""
        where, params = ["1=1"], []
        if not include_hidden:
            where.append("state IS NULL")
        if project_id:
            where.append("project_id = ?")
            params.append(project_id)
        if root_id:
            where.append("(id = ? OR root_id = ?)")
            params += [root_id, root_id]
        w = " AND ".join(where)
        cur: list[Any] | None = None
        if after:
            try:
                cur = json.loads(after)
                assert isinstance(cur, list) and len(cur) == 3 and cur[0] == "r"
            except (ValueError, AssertionError):
                cur = None
        kc, kp = "", []
        if cur:
            kc, kp = " AND (created_at > ? OR (created_at = ? AND id > ?))", [cur[1], cur[1], cur[2]]
        with self.connect() as con:
            rest = con.execute(f"SELECT id, created_at FROM datasets WHERE {w}{kc} ORDER BY created_at, id LIMIT ?",
                               [*params, *kp, limit + 1]).fetchall()
            keys = [(r[0], ["r", r[1], r[0]]) for r in rest]
            more = len(keys) > limit
            keys = keys[:limit]
            got = {r["id"]: r for r in con.execute(f"SELECT {LIGHT_COLS} FROM datasets WHERE id IN ({','.join('?' * len(keys))})",
                                                   [k for k, _ in keys]).fetchall()} if keys else {}
            total = int(con.execute(f"SELECT count(*) FROM datasets WHERE {w}", params).fetchone()[0])
        nxt = json.dumps(keys[-1][1]) if more and keys else None
        return {"rows": [self._light(got[k]) for k, _ in keys if k in got], "next_after": nxt, "total": total}

    @staticmethod
    def _light(r: sqlite3.Row) -> dict[str, Any]:
        d = {k: r[k] for k in r.keys() if k not in ("_n_cols", "_n_classes", "_rank")}
        d["profile"] = {"n_cols": r["_n_cols"] or 0, "n_classes": r["_n_classes"] or 0}
        return d

    def get_dataset(self, dataset_id: str) -> dict[str, Any] | None:
        with self.connect() as con:
            r = con.execute("SELECT * FROM datasets WHERE id = ?", (dataset_id,)).fetchone()
        return self._row(r) if r else None

    @staticmethod
    def _row(r: sqlite3.Row) -> dict[str, Any]:
        d = dict(r)
        d["profile"] = json.loads(d["profile"])
        return d

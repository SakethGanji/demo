"""LB-B (L07/L08/L09): the workflow tables of labeling.md §3.1 (tasks, members, assignments, gold, proposals, prompts,
LLM runs and cache). Additive DDL only: `CREATE … IF NOT EXISTS`, so when the core schema (LB-A) already made a table
with the §3.1 shape this is a no-op. Portable SQL (SQLite as written; the same text runs on Postgres 16: TEXT JSON,
BIGINT ids, partial unique index). `ensure()` runs once per process on first use; it never touches the core tables
(label_layers, label_schemas, label_events, label_snapshots, the registries)."""

from __future__ import annotations

import threading
from typing import Any

DDL = [
    """CREATE TABLE IF NOT EXISTS label_tasks (
  id TEXT PRIMARY KEY, layer_id TEXT NOT NULL, name TEXT NOT NULL,
  task_type TEXT NOT NULL, consensus TEXT NOT NULL DEFAULT 'majority',
  schema_version INTEGER NOT NULL, columns_json TEXT NOT NULL, context_json TEXT NOT NULL,
  votes_per_row INTEGER NOT NULL DEFAULT 1, gold_share TEXT NOT NULL DEFAULT '0.05',
  blind_share TEXT NOT NULL DEFAULT '0.10',
  show_prelabel INTEGER NOT NULL DEFAULT 1 CHECK (show_prelabel IN (0,1)),
  review TEXT NOT NULL DEFAULT 'disputed' CHECK (review IN ('none','disputed','all')),
  unmasked_columns TEXT NOT NULL DEFAULT '[]', unmask_request_id TEXT,
  min_gold_accuracy TEXT NOT NULL DEFAULT '0.80',
  lease_seconds INTEGER NOT NULL DEFAULT 900,
  state TEXT NOT NULL DEFAULT 'open' CHECK (state IN ('draft','open','paused','closed')),
  created_at TEXT NOT NULL, created_by TEXT NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS label_task_members (task_id TEXT NOT NULL, principal_id TEXT NOT NULL,
  role TEXT NOT NULL CHECK (role IN ('annotator','reviewer')), added_by TEXT NOT NULL, added_at TEXT NOT NULL,
  paused_at TEXT, paused_reason TEXT, PRIMARY KEY (task_id, principal_id, role))""",
    """CREATE TABLE IF NOT EXISTS label_assignments (
  id TEXT PRIMARY KEY, task_id TEXT NOT NULL, row_id BIGINT NOT NULL, version_id TEXT NOT NULL,
  item_key TEXT NOT NULL DEFAULT '', reason TEXT NOT NULL,
  priority TEXT NOT NULL DEFAULT '0', is_gold INTEGER NOT NULL DEFAULT 0, is_blind INTEGER NOT NULL DEFAULT 0,
  assignee TEXT, status TEXT NOT NULL CHECK (status IN ('open','leased','done','skipped','expired','cancelled')),
  leased_until TEXT, done_at TEXT, created_at TEXT NOT NULL, fill_id TEXT NOT NULL)""",
    "CREATE INDEX IF NOT EXISTS ix_la_next ON label_assignments (task_id, status, priority DESC, row_id)",
    "CREATE UNIQUE INDEX IF NOT EXISTS ux_la_once ON label_assignments (task_id, row_id, item_key, assignee) WHERE assignee IS NOT NULL",
    """CREATE TABLE IF NOT EXISTS label_gold (task_id TEXT NOT NULL, row_id BIGINT NOT NULL, column_name TEXT NOT NULL,
  value TEXT, pool TEXT NOT NULL CHECK (pool IN ('qa','few_shot','calibration')),
  set_by TEXT NOT NULL, set_at TEXT NOT NULL, PRIMARY KEY (task_id, row_id, column_name))""",
    """CREATE TABLE IF NOT EXISTS label_proposals (id TEXT PRIMARY KEY, layer_id TEXT NOT NULL, origin TEXT NOT NULL
  CHECK (origin IN ('edit','data_doctor','task','import')), base_version_id TEXT NOT NULL, n_cells BIGINT NOT NULL,
  reason TEXT NOT NULL, status TEXT NOT NULL CHECK (status IN ('pending','approved','rejected','withdrawn','applied')),
  proposed_by TEXT NOT NULL, proposed_at TEXT NOT NULL, decided_by TEXT, decided_at TEXT, comment TEXT NOT NULL DEFAULT '',
  applied_version_id TEXT)""",
    """CREATE TABLE IF NOT EXISTS label_prompts (
  id TEXT PRIMARY KEY, layer_id TEXT NOT NULL, name TEXT NOT NULL, version INTEGER NOT NULL,
  schema_version INTEGER NOT NULL, model TEXT NOT NULL,
  effort TEXT NOT NULL DEFAULT 'low', template TEXT NOT NULL, context_json TEXT NOT NULL,
  few_shot TEXT NOT NULL DEFAULT '{}', output_schema TEXT NOT NULL, prompt_sha256 TEXT NOT NULL,
  calibration TEXT, created_at TEXT NOT NULL, created_by TEXT NOT NULL, UNIQUE (layer_id, name, version))""",
    """CREATE TABLE IF NOT EXISTS label_llm_runs (
  id TEXT PRIMARY KEY, prompt_id TEXT NOT NULL, job_id TEXT NOT NULL, purpose TEXT NOT NULL
  CHECK (purpose IN ('preview','calibrate','label')), selector TEXT NOT NULL, samples_k INTEGER NOT NULL,
  mode TEXT NOT NULL CHECK (mode IN ('online','batch')), destination TEXT NOT NULL,
  columns_sent TEXT NOT NULL, unmasked_request_id TEXT, few_shot_sha256 TEXT,
  max_usd TEXT NOT NULL, est_usd TEXT, spent_usd TEXT NOT NULL DEFAULT '0', tokens_in BIGINT NOT NULL DEFAULT 0,
  tokens_cached BIGINT NOT NULL DEFAULT 0, tokens_out BIGINT NOT NULL DEFAULT 0, rows_total BIGINT,
  rows_done BIGINT NOT NULL DEFAULT 0, rows_cached BIGINT NOT NULL DEFAULT 0, rows_failed BIGINT NOT NULL DEFAULT 0,
  pii_redactions BIGINT NOT NULL DEFAULT 0, status TEXT NOT NULL, created_at TEXT NOT NULL, created_by TEXT NOT NULL,
  finished_at TEXT)""",
    """CREATE TABLE IF NOT EXISTS label_llm_cache (key TEXT PRIMARY KEY, layer_id TEXT NOT NULL, row_id BIGINT NOT NULL,
  response TEXT NOT NULL, tokens_in INTEGER, tokens_out INTEGER, created_at TEXT NOT NULL)""",
    "CREATE INDEX IF NOT EXISTS ix_llc_row ON label_llm_cache (layer_id, row_id)",
]

#: LB-B's own columns on its own tables (added when missing; never on the core tables)
EXTRA_COLUMNS = {
    "label_tasks": [("min_gold_items", "INTEGER NOT NULL DEFAULT 20"), ("guidelines", "TEXT NOT NULL DEFAULT ''"),
                    ("team_id", "TEXT")],
    "label_task_members": [("guest", "INTEGER NOT NULL DEFAULT 0")],
    "label_assignments": [("column_name", "TEXT"), ("prelabel_event", "TEXT")],
    "label_llm_runs": [("task_id", "TEXT"), ("result", "TEXT"), ("error", "TEXT")],
    "label_proposals": [("cells", "TEXT")],
}

_done = False
_lock = threading.Lock()


def _cols(con: Any, table: str) -> set[str]:
    try:
        cur = con.execute(f"SELECT * FROM {table} WHERE 1 = 0")
        return {d[0] for d in cur.description}
    except Exception:  # noqa: BLE001
        return set()


def ensure(db: Any) -> None:
    global _done
    if _done:
        return
    with _lock:
        if _done:
            return
        if getattr(db, "dialect", "sqlite") == "postgres":  # LB-PG: made by catalog/pg_schema.migrate (labels.schema.pg_ddl)
            _done = True
            return
        with db.connect() as con:
            for s in DDL:
                con.execute(s)
            for t, cols in EXTRA_COLUMNS.items():
                have = _cols(con, t)
                for c, decl in cols:
                    if c not in have:
                        con.execute(f"ALTER TABLE {t} ADD COLUMN {c} {decl}")
        _done = True

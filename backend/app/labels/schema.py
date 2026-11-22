"""LB-A (labeling plan §3.1): the label tables, portable DDL (SQLite as written; Postgres through db_pg.translate).

Append-only event log keyed (layer, table, __row_id, column, item_key); registries for sources / task types / consensus
methods / queue strategies (adding one is an INSERT, never DDL, §15.1); frozen snapshots; run pins on run_inputs.
`migrate(con)` is idempotent and is called from catalog.schema.migrate (SQLite) and pg_schema (Postgres)."""

from __future__ import annotations

from typing import Any

DDL = """
CREATE TABLE IF NOT EXISTS label_layers (
  id TEXT PRIMARY KEY,
  root_id TEXT NOT NULL REFERENCES dataset_roots(id), table_name TEXT NOT NULL,
  team_id TEXT NOT NULL REFERENCES teams(id),
  name TEXT NOT NULL, purpose TEXT NOT NULL DEFAULT '',
  kind TEXT NOT NULL DEFAULT 'labels' CHECK (kind IN ('labels','corrections','outcomes')),
  salt TEXT NOT NULL,
  next_seq BIGINT NOT NULL DEFAULT 1,
  is_default INTEGER NOT NULL DEFAULT 0 CHECK (is_default IN (0,1)),
  state TEXT NOT NULL DEFAULT 'active' CHECK (state IN ('active','frozen','archived')),
  created_at TEXT NOT NULL, created_by TEXT NOT NULL,
  UNIQUE (root_id, table_name, team_id, name));
CREATE UNIQUE INDEX IF NOT EXISTS ux_ll_default ON label_layers (root_id, table_name, team_id) WHERE is_default = 1;
CREATE INDEX IF NOT EXISTS ix_ll_root ON label_layers (root_id, table_name);

CREATE TABLE IF NOT EXISTS label_schemas (
  id TEXT PRIMARY KEY, layer_id TEXT NOT NULL REFERENCES label_layers(id), version INTEGER NOT NULL,
  spec TEXT NOT NULL, spec_sha256 TEXT NOT NULL,
  guidelines TEXT NOT NULL DEFAULT '', guidelines_sha256 TEXT NOT NULL,
  migrates_from INTEGER, class_map TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL, created_by TEXT NOT NULL, UNIQUE (layer_id, version));

CREATE TABLE IF NOT EXISTS label_source_types (name TEXT PRIMARY KEY, machine INTEGER NOT NULL CHECK (machine IN (0,1)),
  precedence INTEGER NOT NULL, may_grade_holdout INTEGER NOT NULL DEFAULT 0 CHECK (may_grade_holdout IN (0,1)),
  module TEXT NOT NULL, since_fmt INTEGER NOT NULL DEFAULT 1);
CREATE TABLE IF NOT EXISTS label_task_types (name TEXT PRIMARY KEY, item_shape TEXT NOT NULL
  CHECK (item_shape IN ('cell','row_span','pair','list')), value_kind TEXT NOT NULL, module TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS label_consensus_methods (name TEXT PRIMARY KEY, task_types TEXT NOT NULL, module TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS label_queue_strategies (name TEXT PRIMARY KEY, module TEXT NOT NULL, needs_run INTEGER NOT NULL DEFAULT 0);

CREATE TABLE IF NOT EXISTS label_events (
  fmt INTEGER NOT NULL DEFAULT 1,
  id TEXT NOT NULL,
  layer_id TEXT NOT NULL REFERENCES label_layers(id), seq BIGINT NOT NULL,
  row_id BIGINT NOT NULL, column_name TEXT NOT NULL,
  item_key TEXT NOT NULL DEFAULT '',
  kind TEXT NOT NULL CHECK (kind IN ('label','confirm','reject','adjudicate','retract','propose','flag')),
  value TEXT,
  is_null INTEGER NOT NULL DEFAULT 0 CHECK (is_null IN (0,1)),
  source TEXT NOT NULL REFERENCES label_source_types(name),
  actor TEXT NOT NULL,
  task_id TEXT, assignment_id TEXT, refers_to TEXT,
  schema_version INTEGER NOT NULL,
  confidence TEXT,
  seen_version_id TEXT NOT NULL,
  row_hash_ctx TEXT, row_hash_all TEXT, ctx_cols_sha256 TEXT,
  base_value_hash TEXT,
  provenance TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL,
  purged_at TEXT, purge_id TEXT REFERENCES approval_requests(id),
  PRIMARY KEY (layer_id, seq), UNIQUE (layer_id, id));
CREATE INDEX IF NOT EXISTS ix_le_row ON label_events (layer_id, row_id, column_name, item_key, seq);
CREATE INDEX IF NOT EXISTS ix_le_task ON label_events (task_id, actor);
CREATE INDEX IF NOT EXISTS ix_le_source ON label_events (layer_id, source, seq);

CREATE TABLE IF NOT EXISTS label_tasks (
  id TEXT PRIMARY KEY, layer_id TEXT NOT NULL REFERENCES label_layers(id), name TEXT NOT NULL,
  task_type TEXT NOT NULL REFERENCES label_task_types(name),
  consensus TEXT NOT NULL DEFAULT 'majority' REFERENCES label_consensus_methods(name),
  schema_version INTEGER NOT NULL, columns_json TEXT NOT NULL,
  context_json TEXT NOT NULL,
  votes_per_row INTEGER NOT NULL DEFAULT 1, gold_share TEXT NOT NULL DEFAULT '0.05',
  blind_share TEXT NOT NULL DEFAULT '0.10',
  show_prelabel INTEGER NOT NULL DEFAULT 1 CHECK (show_prelabel IN (0,1)),
  review TEXT NOT NULL DEFAULT 'disputed' CHECK (review IN ('none','disputed','all')),
  unmasked_columns TEXT NOT NULL DEFAULT '[]', unmask_request_id TEXT,
  min_gold_accuracy TEXT NOT NULL DEFAULT '0.80',
  lease_seconds INTEGER NOT NULL DEFAULT 900,
  state TEXT NOT NULL DEFAULT 'open' CHECK (state IN ('draft','open','paused','closed')),
  created_at TEXT NOT NULL, created_by TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS label_task_members (task_id TEXT NOT NULL REFERENCES label_tasks(id), principal_id TEXT NOT NULL,
  role TEXT NOT NULL CHECK (role IN ('annotator','reviewer')), added_by TEXT NOT NULL, added_at TEXT NOT NULL,
  paused_at TEXT, paused_reason TEXT, PRIMARY KEY (task_id, principal_id, role));
CREATE TABLE IF NOT EXISTS label_assignments (
  id TEXT PRIMARY KEY, task_id TEXT NOT NULL REFERENCES label_tasks(id), row_id BIGINT NOT NULL,
  version_id TEXT NOT NULL,
  item_key TEXT NOT NULL DEFAULT '',
  reason TEXT NOT NULL REFERENCES label_queue_strategies(name),
  priority TEXT NOT NULL DEFAULT '0', is_gold INTEGER NOT NULL DEFAULT 0, is_blind INTEGER NOT NULL DEFAULT 0,
  assignee TEXT, status TEXT NOT NULL CHECK (status IN ('open','leased','done','skipped','expired','cancelled')),
  leased_until TEXT, done_at TEXT, created_at TEXT NOT NULL, fill_id TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS ix_la_next ON label_assignments (task_id, status, priority DESC, row_id);
CREATE UNIQUE INDEX IF NOT EXISTS ux_la_once ON label_assignments (task_id, row_id, item_key, assignee) WHERE assignee IS NOT NULL;
CREATE TABLE IF NOT EXISTS label_gold (task_id TEXT NOT NULL REFERENCES label_tasks(id), row_id BIGINT NOT NULL, column_name TEXT NOT NULL,
  value TEXT, pool TEXT NOT NULL CHECK (pool IN ('qa','few_shot','calibration')),
  set_by TEXT NOT NULL, set_at TEXT NOT NULL, PRIMARY KEY (task_id, row_id, column_name));

CREATE TABLE IF NOT EXISTS label_snapshots (
  id TEXT PRIMARY KEY, layer_id TEXT NOT NULL REFERENCES label_layers(id), through_seq BIGINT NOT NULL,
  schema_version INTEGER NOT NULL, policy TEXT NOT NULL, policy_sha256 TEXT NOT NULL,
  blob_sha256 TEXT NOT NULL REFERENCES blobs(sha256),
  manifest_blob TEXT NOT NULL REFERENCES blobs(sha256),
  snapshot_sha256 TEXT NOT NULL UNIQUE, values_sha256 TEXT NOT NULL, events_sha256 TEXT NOT NULL DEFAULT '',
  counts TEXT NOT NULL,
  contract_result TEXT,
  state TEXT NOT NULL DEFAULT 'draft' CHECK (state IN ('draft','approved','superseded','purged')),
  created_at TEXT NOT NULL, created_by TEXT NOT NULL, approved_by TEXT, approved_at TEXT, approval_comment TEXT);
CREATE INDEX IF NOT EXISTS ix_lsn_layer ON label_snapshots (layer_id, created_at);

CREATE TABLE IF NOT EXISTS label_proposals (id TEXT PRIMARY KEY, layer_id TEXT NOT NULL, origin TEXT NOT NULL
  CHECK (origin IN ('edit','data_doctor','task','import')), base_version_id TEXT NOT NULL, n_cells BIGINT NOT NULL,
  reason TEXT NOT NULL, status TEXT NOT NULL CHECK (status IN ('pending','approved','rejected','withdrawn','applied')),
  proposed_by TEXT NOT NULL, proposed_at TEXT NOT NULL, decided_by TEXT, decided_at TEXT, comment TEXT NOT NULL DEFAULT '',
  applied_version_id TEXT);

CREATE TABLE IF NOT EXISTS label_prompts (
  id TEXT PRIMARY KEY, layer_id TEXT NOT NULL, name TEXT NOT NULL, version INTEGER NOT NULL,
  schema_version INTEGER NOT NULL, model TEXT NOT NULL,
  effort TEXT NOT NULL DEFAULT 'low', template TEXT NOT NULL, context_json TEXT NOT NULL,
  few_shot TEXT NOT NULL DEFAULT '{}',
  output_schema TEXT NOT NULL, prompt_sha256 TEXT NOT NULL,
  calibration TEXT,
  created_at TEXT NOT NULL, created_by TEXT NOT NULL, UNIQUE (layer_id, name, version));
CREATE TABLE IF NOT EXISTS label_llm_runs (
  id TEXT PRIMARY KEY, prompt_id TEXT NOT NULL, job_id TEXT NOT NULL, purpose TEXT NOT NULL
  CHECK (purpose IN ('preview','calibrate','label')), selector TEXT NOT NULL, samples_k INTEGER NOT NULL,
  mode TEXT NOT NULL CHECK (mode IN ('online','batch')), destination TEXT NOT NULL,
  columns_sent TEXT NOT NULL, unmasked_request_id TEXT, few_shot_sha256 TEXT,
  max_usd TEXT NOT NULL, est_usd TEXT, spent_usd TEXT NOT NULL DEFAULT '0', tokens_in BIGINT NOT NULL DEFAULT 0,
  tokens_cached BIGINT NOT NULL DEFAULT 0, tokens_out BIGINT NOT NULL DEFAULT 0, rows_total BIGINT, rows_done BIGINT NOT NULL DEFAULT 0,
  rows_cached BIGINT NOT NULL DEFAULT 0, rows_failed BIGINT NOT NULL DEFAULT 0, pii_redactions BIGINT NOT NULL DEFAULT 0,
  status TEXT NOT NULL, created_at TEXT NOT NULL, created_by TEXT NOT NULL, finished_at TEXT);
CREATE TABLE IF NOT EXISTS label_llm_cache (
  key TEXT PRIMARY KEY,
  layer_id TEXT NOT NULL, row_id BIGINT NOT NULL, response TEXT NOT NULL, tokens_in INTEGER, tokens_out INTEGER,
  created_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS ix_llc_row ON label_llm_cache (layer_id, row_id);
"""

#: run pins (columns added to run_inputs; written at INSERT by the worker, never updated)
RUN_INPUT_COLS = [("label_snapshot_id", "TEXT"), ("label_snapshot_sha256", "TEXT"), ("overlay_sha256", "TEXT"),
                  ("overlay_blob", "TEXT"), ("overlay_spec", "TEXT")]

SEEDS = {
    "label_source_types": ("name, machine, precedence, may_grade_holdout, module", [
        ("human", 0, 10, 1, "labels.plugins.sources"), ("import", 0, 20, 1, "labels.plugins.sources"),
        ("migration", 0, 30, 1, "labels.plugins.sources"), ("edit", 0, 40, 1, "labels.plugins.sources"),
        ("remap", 0, 45, 1, "labels.plugins.sources"), ("rule", 1, 50, 0, "labels.plugins.sources"),
        ("llm", 1, 60, 0, "labels.plugins.sources"), ("model", 1, 70, 0, "labels.plugins.sources"),
        ("cleanlab", 1, 80, 0, "labels.plugins.sources")]),
    "label_task_types": ("name, item_shape, value_kind, module", [
        ("class", "cell", "class", "labels.plugins.tasks"), ("multi_label", "cell", "multi", "labels.plugins.tasks"),
        ("number", "cell", "number", "labels.plugins.tasks"), ("text", "cell", "text", "labels.plugins.tasks"),
        ("cell_correction", "cell", "any", "labels.plugins.tasks")]),
    "label_consensus_methods": ("name, task_types, module", [
        ("majority", '["class","multi_label","number","text","cell_correction"]', "labels.plugins.consensus"),
        ("crowdlab", '["class"]', "labels.plugins.consensus"),
        ("single", '["class","multi_label","number","text","cell_correction"]', "labels.plugins.consensus")]),
    "label_queue_strategies": ("name, module, needs_run", [
        (n, "labels.plugins.strategies", 1 if n in ("active", "cleanlab") else 0)
        for n in ("random", "active", "cleanlab", "disputed", "stale", "gold", "llm_low_conf", "rule_conflict", "proposal",
                  "manual")]),
}

_FROZEN = ("NEW.id = OLD.id AND NEW.fmt = OLD.fmt AND NEW.item_key = OLD.item_key AND NEW.layer_id = OLD.layer_id"
           " AND NEW.seq = OLD.seq AND NEW.row_id = OLD.row_id AND NEW.column_name = OLD.column_name AND NEW.kind = OLD.kind"
           " AND NEW.source = OLD.source AND NEW.actor = OLD.actor AND NEW.created_at = OLD.created_at"
           " AND NEW.is_null {is_} OLD.is_null AND NEW.task_id {is_} OLD.task_id AND NEW.assignment_id {is_} OLD.assignment_id"
           " AND NEW.refers_to {is_} OLD.refers_to AND NEW.schema_version = OLD.schema_version"
           " AND NEW.confidence {is_} OLD.confidence AND NEW.seen_version_id = OLD.seen_version_id"
           " AND NEW.ctx_cols_sha256 {is_} OLD.ctx_cols_sha256")
_REDACT = ("OLD.purged_at IS NULL AND NEW.purged_at IS NOT NULL AND NEW.purge_id IS NOT NULL AND NEW.value IS NULL"
           " AND NEW.provenance = '{}' AND NEW.row_hash_ctx IS NULL AND NEW.row_hash_all IS NULL AND NEW.base_value_hash IS NULL")

TRIGGERS = {
    "le_ro_d": "CREATE TRIGGER IF NOT EXISTS le_ro_d BEFORE DELETE ON label_events BEGIN SELECT RAISE(ABORT, 'label events are append-only'); END",
    "le_ro_u": ("CREATE TRIGGER IF NOT EXISTS le_ro_u BEFORE UPDATE ON label_events WHEN NOT (" + _REDACT + " AND "
                + _FROZEN.format(is_="IS") + ") BEGIN SELECT RAISE(ABORT, 'label events are append-only (only a purge may redact)'); END"),
    "ri_label_ro_u": ("CREATE TRIGGER IF NOT EXISTS ri_label_ro_u BEFORE UPDATE OF label_snapshot_id, label_snapshot_sha256, overlay_sha256,"
                      " overlay_blob, overlay_spec ON run_inputs BEGIN SELECT RAISE(ABORT, 'run inputs are immutable'); END"),
}


def pg_triggers() -> dict[str, tuple[str, str]]:
    """The Postgres twins (catalog/pg_schema.PG_TRIGGERS entries), same names and messages."""
    from ..catalog.pg_schema import _ro

    return {
        "le_ro_d": ("label_events", _ro("le_ro_d", "DELETE", "label_events", "label events are append-only")),
        "le_ro_u": ("label_events", _ro("le_ro_u", "UPDATE", "label_events", "label events are append-only (only a purge may redact)",
                                        "NOT (" + _REDACT + " AND " + _FROZEN.format(is_="IS NOT DISTINCT FROM") + ")")),
        "ri_label_ro_u": ("run_inputs", _ro("ri_label_ro_u", "UPDATE OF label_snapshot_id, label_snapshot_sha256, overlay_sha256,"
                                            " overlay_blob, overlay_spec", "run_inputs", "run inputs are immutable")),
    }


#: blobs.kind and blob_refs.owner_kind gain the label kinds (a CHECK: SQLite rebuilds the table once; Postgres swaps it)
BLOB_KINDS = ("'table','split','join_cache','manifest','source','legacy_csv','legacy_json','frozen_holdout','sample',"
              "'audit_archive','label_snapshot','label_overlay'")
REF_KINDS = "'version','split','join_frame','run','upload','export','label_snapshot'"

PG_DDL = [
    "ALTER TABLE blobs DROP CONSTRAINT IF EXISTS blobs_kind_check",
    f"ALTER TABLE blobs ADD CONSTRAINT blobs_kind_check CHECK (kind IN ({BLOB_KINDS}))",
    "ALTER TABLE blob_refs DROP CONSTRAINT IF EXISTS blob_refs_owner_kind_check",
    f"ALTER TABLE blob_refs ADD CONSTRAINT blob_refs_owner_kind_check CHECK (owner_kind IN ({REF_KINDS}))",
]


def _table_sql(con: Any, name: str) -> str:
    r = con.execute("SELECT sql FROM sqlite_master WHERE type = 'table' AND name = ?", (name,)).fetchone()
    return (r[0] if r else "") or ""


def _rebuild(con: Any, table: str, col: str, kinds: str) -> None:
    """SQLite cannot alter a CHECK: create table_new with the widened list, copy, drop, rename (foreign keys off, so the
    tables that reference `table` by name keep pointing at it); indexes and triggers re-created by the caller."""
    import re

    sql = _table_sql(con, table)
    if not sql or "'label_snapshot'" in sql:
        return
    pat = re.compile(rf"CHECK\s*\(\s*{col}\s+IN\s*\([^)]*\)\s*\)", re.S)
    if not pat.search(sql):
        raise RuntimeError(f"labels.schema: unexpected {table} DDL; cannot widen its CHECK")
    idx = [r[0] for r in con.execute("SELECT sql FROM sqlite_master WHERE type IN ('index','trigger') AND tbl_name = ?"
                                     " AND sql IS NOT NULL", (table,))]
    cols = ", ".join(r[1] for r in con.execute(f"PRAGMA table_info({table})"))
    con.commit()
    con.execute("PRAGMA foreign_keys=OFF")
    try:
        con.execute("BEGIN")
        create = pat.sub(f"CHECK ({col} IN ({kinds}))", sql, count=1)
        create = create.replace(f"CREATE TABLE {table}", f"CREATE TABLE {table}__new", 1).replace(
            f'CREATE TABLE "{table}"', f"CREATE TABLE {table}__new", 1)
        con.execute(create)
        con.execute(f"INSERT INTO {table}__new ({cols}) SELECT {cols} FROM {table}")
        con.execute(f"DROP TABLE {table}")
        con.execute(f"ALTER TABLE {table}__new RENAME TO {table}")
        for q in idx:
            con.execute(q)
        con.commit()
    finally:
        con.execute("PRAGMA foreign_keys=ON")


def migrate(con: Any) -> None:
    """SQLite, idempotent."""
    from ..catalog.schema import IMMUTABLE_TRIGGERS, _step

    _rebuild(con, "blobs", "kind", BLOB_KINDS)
    _rebuild(con, "blob_refs", "owner_kind", REF_KINDS)
    con.execute(IMMUTABLE_TRIGGERS["blobs_key_ro"])
    con.executescript(DDL)
    have = {r[1] for r in con.execute("PRAGMA table_info(run_inputs)")}
    for c, typ in RUN_INPUT_COLS:
        if c not in have:
            con.execute(f"ALTER TABLE run_inputs ADD COLUMN {c} {typ}")
    seed(con)
    con.execute("DROP TRIGGER IF EXISTS lsch_ro_u")  # an early LB-A build: it blocked the purge scrub of class names
    for q in TRIGGERS.values():
        con.execute(q)
    _step(con, "labels_l01a")


def seed(con: Any) -> None:
    for table, (cols, rows) in SEEDS.items():
        q = f"INSERT INTO {table} ({cols}) VALUES ({', '.join('?' for _ in cols.split(','))}) ON CONFLICT DO NOTHING"
        for r in rows:
            con.execute(q, r)


def pg_ddl() -> list[str]:
    from ..db_pg import split_script, translate

    out = [translate(s, False) for s in split_script(DDL)]
    out += [translate(f"ALTER TABLE run_inputs ADD COLUMN IF NOT EXISTS {c} {t}", False) for c, t in RUN_INPUT_COLS]
    return out + PG_DDL

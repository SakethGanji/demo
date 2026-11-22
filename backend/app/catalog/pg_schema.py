"""P23 (§2.2): the Postgres schema, built from the same DDL strings SQLite uses, plus what only Postgres needs.

- Tables, columns and indexes: db.SCHEMA, catalog SCHEMA, the RUN/DATASET/MV/DSR columns, P01_DDL + P01_COLS, P10_DDL,
  lake.migrate.LEGACY_DDL (the CSV-era split table) and ix_datasets_list, each statement through db_pg.translate (INTEGER → BIGINT,
  REAL → DOUBLE PRECISION, AUTOINCREMENT → identity). Columns are added with ADD COLUMN IF NOT EXISTS.
- Deliberate deltas from §2.2: timestamps and JSON stay TEXT (not timestamptz / jsonb). The audit chain hashes the
  stored text, and the copy from SQLite must be byte-equal (audit_log.details / decision, prev_hash, row_hash), so the
  column types the hashes cover are not changed. JSON paths go through the json_extract / json_valid functions below.
- Triggers: every SQLite trigger has a Postgres twin under the same name (`PG_TRIGGERS`), raising SQLSTATE 23000, so
  `CREATE TRIGGER IF NOT EXISTS <name> …` / `DROP TRIGGER IF EXISTS <name>` in the app translate by name (db_pg).
- `sqlite_master` is a view over pg_catalog (type, name, tbl_name, sql) for the existence checks.
- Grants (defence in depth): when ENSEMBLE_DB_APP_ROLE names the app's login role and migrations run as the owner
  (ENSEMBLE_DB_OWNER_URL), the app role gets SELECT/INSERT/UPDATE/DELETE on every table except audit_log, splits,
  run_inputs, version_tables, table_files, which get INSERT and SELECT only (run_inputs also UPDATE(split_id, match):
  the P10 migration re-points a split id; the hash columns stay read-only by trigger)."""

from __future__ import annotations

import hashlib
import os
import re
from typing import Any

FUNCTIONS = r"""
CREATE OR REPLACE FUNCTION ens_jsonb(t text) RETURNS jsonb LANGUAGE plpgsql IMMUTABLE AS $$
BEGIN
  RETURN t::jsonb;
EXCEPTION WHEN others THEN
  -- F-X5b (X5 K3): a stored "\u0000" (or a lone surrogate escape) is read as U+2400 / U+FFFD; never an error
  BEGIN
    RETURN regexp_replace(regexp_replace(t, '(?<!\\)((?:\\\\)*)\\u0000', '\1\\u2400', 'g'),
                          '(?<!\\)((?:\\\\)*)\\u[dD][89a-fA-F][0-9a-fA-F]{2}', '\1\\ufffd', 'g')::jsonb;
  EXCEPTION WHEN others THEN
    RETURN NULL;
  END;
END $$;
CREATE OR REPLACE FUNCTION json_valid(t text) RETURNS boolean LANGUAGE plpgsql IMMUTABLE AS $$
BEGIN
  IF t IS NULL THEN RETURN NULL; END IF;
  RETURN ens_jsonb(t) IS NOT NULL;  -- F-X5b: a stored "\u0000" reads as valid (sanitised), like on SQLite
EXCEPTION WHEN others THEN
  RETURN FALSE;
END $$;
CREATE OR REPLACE FUNCTION json_extract(t text, path text) RETURNS text LANGUAGE sql IMMUTABLE AS $$
  SELECT CASE WHEN t IS NULL THEN NULL
              WHEN path = '$' THEN t
              WHEN strpos(t, '\u') > 0
                THEN ens_jsonb(t) #>> string_to_array(regexp_replace(substr(path, 3), '\[(\d+)\]', '.\1', 'g'), '.')
              ELSE (t::jsonb) #>> string_to_array(regexp_replace(substr(path, 3), '\[(\d+)\]', '.\1', 'g'), '.') END
$$;
CREATE OR REPLACE FUNCTION ens_ro() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  RAISE EXCEPTION '%', TG_ARGV[0] USING ERRCODE = '23000';
END $$;
CREATE OR REPLACE VIEW sqlite_master AS
  SELECT 'table'::text AS type, c.relname::text AS name, c.relname::text AS tbl_name, NULL::text AS sql
    FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
   WHERE n.nspname = current_schema() AND c.relkind IN ('r', 'p')
  UNION ALL
  SELECT 'index', i.indexname::text, i.tablename::text, i.indexdef FROM pg_indexes i WHERE i.schemaname = current_schema()
  UNION ALL
  SELECT 'trigger', t.tgname::text, c.relname::text, pg_get_triggerdef(t.oid)
    FROM pg_trigger t JOIN pg_class c ON c.oid = t.tgrelid JOIN pg_namespace n ON n.oid = c.relnamespace
   WHERE NOT t.tgisinternal AND n.nspname = current_schema();
"""


def _ro(name: str, when: str, table: str, msg: str, cond: str = "") -> str:
    return (f"CREATE OR REPLACE TRIGGER {name} BEFORE {when} ON {table} FOR EACH ROW"
            f"{' WHEN (' + cond + ')' if cond else ''} EXECUTE FUNCTION ens_ro('{msg}')")


#: name → (table, Postgres statement); the same names and messages as the SQLite triggers in schema.py
PG_TRIGGERS: dict[str, tuple[str, str]] = {
    "splits_ro_u": ("splits", _ro("splits_ro_u", "UPDATE", "splits", "splits are immutable")),
    "splits_ro_d": ("splits", _ro("splits_ro_d", "DELETE", "splits", "splits are immutable")),
    "vt_ro_u": ("version_tables", _ro("vt_ro_u", "UPDATE", "version_tables", "version tables are immutable")),
    "tf_ro_u": ("table_files", _ro("tf_ro_u", "UPDATE", "table_files", "table files are immutable")),
    "ri_ro_u": ("run_inputs", _ro("ri_ro_u", "UPDATE OF table_sha256, read_sha256, split_sha256, sample_sha256", "run_inputs",
                                  "run inputs are immutable")),
    "ri_ro_d": ("run_inputs", _ro("ri_ro_d", "DELETE", "run_inputs", "run inputs are immutable")),
    "blobs_key_ro": ("blobs", _ro("blobs_key_ro", "UPDATE OF sha256, key, bytes", "blobs", "blob identity is immutable")),
    # P10: written once; the migration's first set of manifest_sha256 / data_sha256 on a CSV-era row is allowed
    "ds_hash_ro": ("datasets", _ro("ds_hash_ro", "UPDATE OF manifest_sha256, data_sha256, legacy_sha256", "datasets",
                                   "version hashes are immutable",
                                   "(OLD.manifest_sha256 IS NOT NULL AND (NEW.manifest_sha256 IS DISTINCT FROM OLD.manifest_sha256"
                                   " OR NEW.data_sha256 IS DISTINCT FROM OLD.data_sha256 OR NEW.legacy_sha256 IS DISTINCT FROM"
                                   " OLD.legacy_sha256)) OR (OLD.legacy_sha256 IS NOT NULL AND NEW.legacy_sha256 IS DISTINCT FROM"
                                   " OLD.legacy_sha256)")),
    "audit_ro_u": ("audit_log", _ro("audit_ro_u", "UPDATE", "audit_log", "audit is append-only")),
    "audit_ro_d": ("audit_log", _ro("audit_ro_d", "DELETE", "audit_log", "audit is append-only")),
    # F-X4b (X4 M6): every audit head, write-once; with the grants below the app role cannot drop these (owner only)
    "ahead_ro_u": ("audit_heads", _ro("ahead_ro_u", "UPDATE", "audit_heads", "audit heads are write-once")),
    "ahead_ro_d": ("audit_heads", _ro("ahead_ro_d", "DELETE", "audit_heads", "audit heads are write-once")),
}
#: the tables the app role may only INSERT into and SELECT from (§2.2)
APPEND_ONLY = ("audit_log", "audit_heads", "splits", "run_inputs", "version_tables", "table_files")


def trigger_sql(sql: str) -> str | None:
    """`CREATE TRIGGER [IF NOT EXISTS] <name> …` / `DROP TRIGGER [IF EXISTS] <name>` (SQLite) → the Postgres statement."""
    m = re.match(r"\s*CREATE\s+TRIGGER\s+(?:IF\s+NOT\s+EXISTS\s+)?(\w+)", sql, re.I)
    if m:
        if m.group(1) not in PG_TRIGGERS:
            raise ValueError(f"trigger {m.group(1)} has no Postgres twin in catalog/pg_schema.PG_TRIGGERS")
        return PG_TRIGGERS[m.group(1)][1]
    m = re.match(r"\s*DROP\s+TRIGGER\s+(IF\s+EXISTS\s+)?(\w+)\s*;?\s*$", sql, re.I)
    if m:
        t = PG_TRIGGERS.get(m.group(2))
        if not t:
            raise ValueError(f"trigger {m.group(2)} has no Postgres twin")
        return f"DROP TRIGGER {m.group(1) or ''}{m.group(2)} ON {t[0]}"
    return None


def ddl() -> list[str]:
    """Every table / column / index statement, in SQLite's order, translated."""
    from .. import db as D
    from ..db_pg import split_script, translate
    from ..lake.migrate import LEGACY_DDL
    from . import schema as S

    out: list[str] = []
    for s in split_script(D.SCHEMA):
        out.append(translate(s, False))
    out.append(translate("ALTER TABLE batch_jobs ADD COLUMN IF NOT EXISTS data_date TEXT", False))
    for s in split_script(S.SCHEMA):
        out.append(translate(s, False))
    for table, cols in (("runs", S.RUN_COLS), ("datasets", S.DATASET_COLS), ("model_versions", S.MV_COLS),
                        ("dataset_requests", S.DSR_COLS)):
        for c, typ in cols.items():
            out.append(translate(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {c} {typ}", False))
    out += [translate(q, False) for q in S.INDEXES]
    for s in split_script(S.P01_DDL):
        out.append(translate(s, False))
    for table, cols in S.P01_COLS.items():
        for c, typ in cols:
            out.append(translate(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {c} {typ}", False))
    out += [translate(q, False) for q in S.P01_INDEXES]
    out.append("CREATE INDEX IF NOT EXISTS ix_datasets_list ON datasets (state, created_at, id)")  # P08
    for s in split_script(S.P10_DDL + LEGACY_DDL):
        out.append(translate(s, False))
    return out


def schema_hash() -> str:
    body = "\n".join(ddl()) + FUNCTIONS + "\n".join(t[1] for t in PG_TRIGGERS.values())
    return hashlib.sha256(body.encode()).hexdigest()[:16]


def migrate(con: Any, *, app_role: str | None = None, force: bool = False) -> dict[str, Any]:
    """Idempotent. Serialised across processes by an advisory lock; skipped when the recorded schema hash matches."""
    from . import schema as S

    h = schema_hash()
    con.execute("SELECT pg_advisory_xact_lock(hashtext('ensemble.migrate'))")
    have = con.execute("SELECT to_regclass('catalog_meta') IS NOT NULL").fetchone()[0] and con.execute(
        "SELECT value FROM catalog_meta WHERE key = 'pg_schema'").fetchone()
    if have and have[0] == h and not app_role and not force:
        return {"skipped": True, "schema": h}
    for q in ddl():
        con.execute(q)
    con.raw.execute(FUNCTIONS)  # dollar-quoted bodies: sent as one script, untranslated
    for name, (_, q) in PG_TRIGGERS.items():
        if name.startswith("audit_"):
            continue
        con.raw.execute(q)
    S.seed_audit_heads(con)
    S._step(con, "p01_ddl")
    S._step(con, "p01_lineage_check")  # created with join_frame / joined_from / merged_from already
    S.ensure_default_team(con)
    S._step(con, "p01_team_default")
    S._step(con, "p10_ddl")
    # as on SQLite: the audit triggers come once the P01 backfill has run (it is the last UPDATE audit_log gets)
    if con.execute("SELECT 1 FROM catalog_meta WHERE key = 'backfill_p01'").fetchone():
        for name in ("audit_ro_u", "audit_ro_d"):
            con.raw.execute(PG_TRIGGERS[name][1])
    granted = grant(con, app_role) if app_role else None
    con.execute("INSERT INTO catalog_meta (key, value) VALUES ('pg_schema', ?) ON CONFLICT (key) DO UPDATE SET value = excluded.value",
                (h,))
    return {"skipped": False, "schema": h, "granted": granted}


def grant(con: Any, role: str) -> dict[str, Any]:
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", role):
        raise ValueError("ENSEMBLE_DB_APP_ROLE must be a plain role name")
    tables = [r[0] for r in con.execute("SELECT tablename FROM pg_tables WHERE schemaname = current_schema()")]
    con.raw.execute(f"GRANT USAGE ON SCHEMA public TO {role}")
    for t in tables:
        con.raw.execute(f"REVOKE ALL ON {t} FROM {role}")
        if t in APPEND_ONLY:
            con.raw.execute(f"GRANT SELECT, INSERT ON {t} TO {role}")
        else:
            con.raw.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {t} TO {role}")
    con.raw.execute(f"GRANT UPDATE (split_id, match) ON run_inputs TO {role}")
    con.raw.execute(f"GRANT SELECT ON sqlite_master TO {role}")
    con.raw.execute(f"GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {role}")
    return {"role": role, "append_only": list(APPEND_ONLY), "tables": len(tables)}


def owner_url() -> str | None:
    return (os.environ.get("ENSEMBLE_DB_OWNER_URL") or "").strip() or None

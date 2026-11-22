"""X5/x2: state.py for a Postgres metadata DB (ENSEMBLE_DB_URL): storage counts, lake_migrations, blobs, and the same
fingerprint of the tables the migration writes. usage: pg_state.py <url> [--fp-out file]"""
import hashlib, json, sys
from pathlib import Path
import psycopg
from psycopg.rows import dict_row
c = psycopg.connect(sys.argv[1], row_factory=dict_row)
q = lambda s: c.execute(s).fetchall()
print("storage:", {r["s"]: r["n"] for r in q("SELECT COALESCE(storage,'csv') s, COUNT(*) n FROM datasets GROUP BY 1")})
lm = q("SELECT version_id, status, reason, folder_state, attempts FROM lake_migrations ORDER BY at")
agg = {}
for r in lm: agg[(r["status"], r["folder_state"])] = agg.get((r["status"], r["folder_state"]), 0) + 1
print("lake_migrations:", agg)
for r in lm:
    if r["status"] != "migrated" or r["folder_state"] != "renamed": print("   ", r)
print("blobs:", {r["k"]: r["n"] for r in q("SELECT state || '/' || kind k, COUNT(*) n FROM blobs GROUP BY 1")})
print("jobs(migrate):", q("SELECT id, status, attempts, error FROM jobs WHERE kind = 'migrate' ORDER BY created_at"))
if "--fp-out" in sys.argv:
    fp = {}
    for t, cols in (("datasets", "id, storage, sha256, legacy_sha256, manifest_sha256, data_sha256, n_rows, n_cols"),
                    ("version_tables", "*"), ("table_files", "*"), ("blobs", "sha256, key, kind, bytes, n_rows, state"),
                    ("blob_refs", "blob_sha256, owner_kind, owner_id, role"), ("splits", "version_id, split_key, blob_sha256, split_sha256, legacy_sha256, n_rows, n_holdout"),
                    ("run_inputs", "run_id, role, version_id, table_sha256, legacy_csv_sha256, split_id, split_legacy_sha256, read_mode"),
                    ("dataset_root_tables", "*"), ("lineage_edges", "*"),
                    ("lake_migrations", "version_id, status, reason, legacy_sha256, manifest_sha256, folder_state")):
        rows = sorted(json.dumps(r, sort_keys=True, default=str) for r in q(f"SELECT {cols} FROM {t}"))
        fp[t] = {"n": len(rows), "sha": hashlib.sha256("\n".join(rows).encode()).hexdigest()[:16]}
    Path(sys.argv[sys.argv.index("--fp-out") + 1]).write_text(json.dumps(fp, indent=1)); print("fingerprint:", fp)

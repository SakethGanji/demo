"""X2: the migration state of a data dir (read-only): storage counts, lake_migrations, folders, blobs, refs, jobs, and a
fingerprint of the tables the migration writes (for the idempotency check). usage: state.py <data dir> [--fp-out file]"""
import hashlib, json, sqlite3, sys
from pathlib import Path
D = Path(sys.argv[1]); con = sqlite3.connect(D / "ensemble.sqlite3"); con.row_factory = sqlite3.Row
q = lambda s, *a: [dict(r) for r in con.execute(s, a)]
print("storage:", {r["s"]: r["n"] for r in q("SELECT COALESCE(storage,'csv') s, COUNT(*) n FROM datasets GROUP BY 1")})
try:
    lm = q("SELECT version_id, status, reason, folder_state, attempts FROM lake_migrations ORDER BY at")
except sqlite3.OperationalError:
    lm = []
print("lake_migrations:", {k: sum(1 for r in lm if (r["status"], r["folder_state"]) == k) for k in {(r["status"], r["folder_state"]) for r in lm}})
for r in lm:
    if r["status"] != "migrated" or r["folder_state"] != "renamed" or r["attempts"] != 1:
        print("   ", r)
ds = D / "datasets"
mig = sorted(p.name for p in ds.glob("*.migrated"))
print("folders: .migrated", len(mig), "| data.csv files", len(list(ds.glob("*/data.csv"))), "| in .migrated", len(list(ds.glob("*.migrated/data.csv"))))
print("blobs:", {r["state"] + "/" + r["kind"]: r["n"] for r in q("SELECT state, kind, COUNT(*) n FROM blobs GROUP BY 1, 2")})
print("blob_refs:", q("SELECT COUNT(*) n FROM blob_refs")[0]["n"], "| splits:", q("SELECT COUNT(*) n FROM splits")[0]["n"],
      "| run_inputs:", q("SELECT COUNT(*) n FROM run_inputs")[0]["n"])
try:
    print("jobs(migrate):", q("SELECT id, status, attempts, error FROM jobs WHERE kind = 'migrate' ORDER BY created_at"))
except sqlite3.OperationalError as e:
    print("jobs:", e)
print("audit:", {r["action"]: r["n"] for r in q("SELECT action, COUNT(*) n FROM audit_log WHERE action LIKE '%migrat%' GROUP BY 1")})
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
    Path(sys.argv[sys.argv.index("--fp-out") + 1]).write_text(json.dumps(fp, indent=1))
    print("fingerprint:", fp)

"""Step 1: schema additions (idempotent) and the backfill from config, lineage.json and model_versions.

Edges run downstream: src is the upstream node, dst the derived one, `relation` says how dst relates to src:
    dataset  → dataset   derived_from    (a version; meta: op, changes summary)
    dataset  → run       trained_on
    run      → run       branched_from   (meta: source, patch, review_id, hypothesis_id, verdict)
    run      → version   built_from
`inferred` = 1 when the edge was reconstructed without root_rows (an old version or run): never claim a paired Δ
across it."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS projects (
    id              TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    root_dataset_id TEXT,
    description     TEXT NOT NULL DEFAULT '',
    created_at      TEXT NOT NULL,
    created_by      TEXT NOT NULL DEFAULT 'system'
);

CREATE TABLE IF NOT EXISTS lineage_edges (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    src_kind    TEXT NOT NULL CHECK (src_kind IN ('dataset', 'run', 'version')),
    src_id      TEXT NOT NULL,
    dst_kind    TEXT NOT NULL CHECK (dst_kind IN ('dataset', 'run', 'version')),
    dst_id      TEXT NOT NULL,
    relation    TEXT NOT NULL CHECK (relation IN ('derived_from', 'trained_on', 'branched_from', 'built_from', 'compared_in')),
    actor       TEXT NOT NULL DEFAULT 'system',
    meta        TEXT,
    inferred    INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT NOT NULL,
    UNIQUE (src_kind, src_id, dst_kind, dst_id, relation)
);
CREATE INDEX IF NOT EXISTS ix_edges_src ON lineage_edges (src_kind, src_id);
CREATE INDEX IF NOT EXISTS ix_edges_dst ON lineage_edges (dst_kind, dst_id);

-- Tags and notes on anything (a run, a dataset version, a registered version, a project).
CREATE TABLE IF NOT EXISTS annotations (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    target_kind  TEXT NOT NULL CHECK (target_kind IN ('dataset', 'run', 'version', 'project')),
    target_id    TEXT NOT NULL,
    kind         TEXT NOT NULL CHECK (kind IN ('tag', 'note')),
    value        TEXT NOT NULL,
    actor        TEXT NOT NULL,
    created_at   TEXT NOT NULL,
    deleted_at   TEXT,
    deleted_by   TEXT
);
CREATE INDEX IF NOT EXISTS ix_annotations_target ON annotations (target_kind, target_id);

-- A project's champion: a registered version, requested by one person and approved by another (four eyes); audited.
CREATE TABLE IF NOT EXISTS champions (
    id            TEXT PRIMARY KEY,
    project_id    TEXT NOT NULL,
    version_id    TEXT NOT NULL,
    run_id        TEXT,
    status        TEXT NOT NULL CHECK (status IN ('pending', 'approved', 'rejected', 'superseded', 'withdrawn')),
    requested_by  TEXT NOT NULL,
    requested_at  TEXT NOT NULL,
    decided_by    TEXT,
    decided_at    TEXT,
    comment       TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS ix_champions_project ON champions (project_id, status);

-- DL1: one card per dataset (keyed by its root): what it is, who owns it, and the default version (a pointer: a
-- rollback moves it, never the data).
CREATE TABLE IF NOT EXISTS dataset_cards (
    root_id            TEXT PRIMARY KEY,
    description        TEXT NOT NULL DEFAULT '',
    owner              TEXT NOT NULL DEFAULT '',
    source             TEXT NOT NULL DEFAULT '',
    label_definition   TEXT NOT NULL DEFAULT '',
    known_issues       TEXT NOT NULL DEFAULT '',
    default_version_id TEXT,
    updated_at         TEXT,
    updated_by         TEXT
);

-- DL1: a delete needs four eyes (or an admin): one person asks, another approves; both audited.
CREATE TABLE IF NOT EXISTS dataset_requests (
    id            TEXT PRIMARY KEY,
    dataset_id    TEXT NOT NULL,
    root_id       TEXT NOT NULL,
    action        TEXT NOT NULL CHECK (action IN ('delete')),
    status        TEXT NOT NULL CHECK (status IN ('pending', 'approved', 'rejected', 'withdrawn')),
    requested_by  TEXT NOT NULL,
    requested_at  TEXT NOT NULL,
    reason        TEXT NOT NULL DEFAULT '',
    decided_by    TEXT,
    decided_at    TEXT,
    comment       TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS ix_dataset_requests ON dataset_requests (root_id, status);

-- DL2: retiring a registered version needs four eyes (or an admin): one person asks, another approves; both audited.
CREATE TABLE IF NOT EXISTS version_requests (
    id            TEXT PRIMARY KEY,
    version_id    TEXT NOT NULL,
    action        TEXT NOT NULL CHECK (action IN ('retire')),
    status        TEXT NOT NULL CHECK (status IN ('pending', 'approved', 'rejected', 'withdrawn')),
    requested_by  TEXT NOT NULL,
    requested_at  TEXT NOT NULL,
    reason        TEXT NOT NULL DEFAULT '',
    decided_by    TEXT,
    decided_at    TEXT,
    comment       TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS ix_version_requests ON version_requests (version_id, status);

-- DL3 (C1): an admin reset while models or champions are live needs four eyes: one person asks, another approves.
-- Kept through a reset, like the audit log.
CREATE TABLE IF NOT EXISTS admin_requests (
    id            TEXT PRIMARY KEY,
    action        TEXT NOT NULL CHECK (action IN ('reset')),
    status        TEXT NOT NULL CHECK (status IN ('pending', 'approved', 'rejected', 'withdrawn', 'used')),
    requested_by  TEXT NOT NULL,
    requested_at  TEXT NOT NULL,
    reason        TEXT NOT NULL DEFAULT '',
    decided_by    TEXT,
    decided_at    TEXT,
    comment       TEXT NOT NULL DEFAULT '',
    used_at       TEXT,
    used_by       TEXT
);

-- DL4 (M-d): the sha256 of every split file (holdout and folds) when it was drawn, and a compressed copy of its bytes,
-- so verify and the worker at fit start can refuse a split file changed outside the app, and restore it.
CREATE TABLE IF NOT EXISTS split_files (
    dataset_id    TEXT NOT NULL,
    file          TEXT NOT NULL,
    sha256        TEXT NOT NULL,
    recorded_at   TEXT NOT NULL,
    basis         TEXT NOT NULL DEFAULT 'drawn',
    content       BLOB,
    PRIMARY KEY (dataset_id, file)
);

CREATE TABLE IF NOT EXISTS catalog_meta (
    key    TEXT PRIMARY KEY,
    value  TEXT NOT NULL
);
"""

RUN_COLS = {
    "project_id": "TEXT", "parent_run_id": "TEXT", "source": "TEXT", "kind": "TEXT",
    "primary_metric": "TEXT", "primary_score": "REAL", "greater_is_better": "INTEGER",
    "predictor_state": "TEXT", "predictor_bytes": "INTEGER", "state_changed_at": "TEXT",
}
DATASET_COLS = {"project_id": "TEXT", "parent_id": "TEXT", "root_id": "TEXT", "sha256": "TEXT", "n_rows": "INTEGER",
                "op": "TEXT",
                # DL1: who made it; archived / deleted (NULL = active) with when and by whom; the last integrity check
                "created_by": "TEXT", "state": "TEXT", "state_at": "TEXT", "state_by": "TEXT", "verified_at": "TEXT",
                "verified_ok": "INTEGER",
                # DL2: 'dataset' when a whole-dataset archive hid it (an unarchive of the dataset restores only those)
                "state_scope": "TEXT",
                # DL4 (M-d): one sha256 over the version's split files (each file's own hash is in split_files)
                "splits_sha256": "TEXT"}
# DL2: a registered version retired (four eyes): its data may be archived, and deleted after the retention period
MV_COLS = {"retired_at": "TEXT", "retired_by": "TEXT"}
# DL2: a delete request's options (freeze a holdout sample)
DSR_COLS = {"options": "TEXT"}
INDEXES = [
    "CREATE INDEX IF NOT EXISTS ix_runs_dataset ON runs (dataset_id, created_at)",
    "CREATE INDEX IF NOT EXISTS ix_runs_project ON runs (project_id, created_at)",
    "CREATE INDEX IF NOT EXISTS ix_runs_parent ON runs (parent_run_id)",
    "CREATE INDEX IF NOT EXISTS ix_runs_status ON runs (status)",
    "CREATE INDEX IF NOT EXISTS ix_datasets_root ON datasets (root_id)",
]
BACKFILL_VERSION = "1"

#: what the predictor state is while the predictor folder is on disk / gone
PRESENT, SLIMMED, EVICTED = "present", "slimmed", "evicted"
#: no predictor on disk and never evicted (queued, cancelled before fitting, failed)
NONE = "none"


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def migrate(con: sqlite3.Connection) -> None:
    """Idempotent: CREATE IF NOT EXISTS, and ALTER only the columns that are missing."""
    con.executescript(SCHEMA)
    for table, cols in (("runs", RUN_COLS), ("datasets", DATASET_COLS), ("model_versions", MV_COLS), ("dataset_requests", DSR_COLS)):
        have = {r[1] for r in con.execute(f"PRAGMA table_info({table})")}
        for c, typ in cols.items():
            if c not in have:
                con.execute(f"ALTER TABLE {table} ADD COLUMN {c} {typ}")
    for q in INDEXES:
        con.execute(q)
    # F-P2: champions gained the 'withdrawn' status (a CHECK cannot be altered in SQLite: rebuild the table once)
    sql = (con.execute("SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'champions'").fetchone() or [""])[0] or ""
    if "withdrawn" not in sql:
        con.execute("ALTER TABLE champions RENAME TO champions_old")
        con.execute("DROP INDEX IF EXISTS ix_champions_project")
        con.executescript(SCHEMA)
        con.execute("INSERT INTO champions SELECT id, project_id, version_id, run_id, status, requested_by, requested_at, decided_by,"
                    " decided_at, comment FROM champions_old")
        con.execute("DROP TABLE champions_old")


def project_id_for(root_dataset_id: str) -> str:
    """The default project of a dataset root (one per root)."""
    return f"prj_{root_dataset_id.removeprefix('ds_')}"


def file_sha256(path: Path) -> str | None:
    if not path.exists():
        return None
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def primary(result: dict[str, Any] | None) -> tuple[str | None, float | None, int | None]:
    """(metric, holdout score, greater_is_better) from a run's result; None when it has none."""
    res = result or {}
    em = res.get("eval_metric") or {}
    h = res.get("holdout") or {}
    m = em.get("name") if isinstance(em, dict) else em
    m = m or h.get("eval_metric")
    v = (h.get("metrics") or {}).get(m) if m else None
    gib = em.get("greater_is_better", True) if isinstance(em, dict) else True
    try:
        v = None if v is None else float(v)
    except (TypeError, ValueError):
        v = None
    return m, v, int(bool(gib)) if m else None


def run_columns(cfg: dict[str, Any], result: dict[str, Any] | None = None) -> dict[str, Any]:
    """The indexed columns a run's config (and result, once it has one) determines."""
    out = {"parent_run_id": cfg.get("parent_run_id"), "source": cfg.get("source") or ("import" if cfg.get("import") else "user"),
           "kind": cfg.get("kind") or ("import" if cfg.get("import") else "tabular")}
    if result is not None:
        m, v, g = primary(result)
        out.update(primary_metric=m, primary_score=v, greater_is_better=g)
    return out


def edge_meta_for_run(cfg: dict[str, Any]) -> dict[str, Any]:
    patch = cfg.get("patch") or {}
    ex = patch.get("experiment") or {}
    return {k: v for k, v in {
        "source": cfg.get("source"), "patch": patch.get("title") or patch.get("name") or patch.get("id"),
        "experiment": ex.get("kind"), "review_id": patch.get("review_id"), "hypothesis_id": patch.get("hypothesis_id"),
    }.items() if v is not None}


def add_edge(con: sqlite3.Connection, src: tuple[str, str], dst: tuple[str, str], relation: str, *, actor: str = "system",
             meta: dict[str, Any] | None = None, inferred: bool = False, created_at: str | None = None,
             update_meta: bool = False) -> None:
    sql = ("INSERT INTO lineage_edges (src_kind, src_id, dst_kind, dst_id, relation, actor, meta, inferred, created_at)"
           " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)")
    if update_meta:
        sql += (" ON CONFLICT (src_kind, src_id, dst_kind, dst_id, relation) DO UPDATE SET meta = excluded.meta,"
                " inferred = MIN(inferred, excluded.inferred)")
    else:
        sql = sql.replace("INSERT INTO", "INSERT OR IGNORE INTO")
    con.execute(sql, (src[0], src[1], dst[0], dst[1], relation, actor or "system",
                      json.dumps(meta, default=str) if meta else None, int(inferred), created_at or now()))


def audit(con: sqlite3.Connection, actor: str | None, action: str, details: dict[str, Any], comment: str = "",
          run_id: str | None = None) -> None:
    """DL1: an audit_log row written on the caller's connection (the registry's audit() opens its own). Dataset actions
    carry details.dataset_id and details.root_id, so a dataset's history is one query."""
    con.execute("INSERT INTO audit_log (ts, actor, action, run_id, comment, details) VALUES (?, ?, ?, ?, ?, ?)",
                (now(), actor or "system", action, run_id, comment or "", json.dumps(details, default=str)))


def ensure_project(con: sqlite3.Connection, root_id: str, name: str, created_at: str | None = None) -> str:
    pid = project_id_for(root_id)
    con.execute("INSERT OR IGNORE INTO projects (id, name, root_dataset_id, created_at) VALUES (?, ?, ?, ?)",
                (pid, name.replace(" (sample)", ""), root_id, created_at or now()))
    return pid


def drop_if_phantom(con: sqlite3.Connection, pid: str) -> bool:
    """Remove a system-made default project that holds nothing (no dataset, run, champion or annotation): what a SQL
    refresh or a monitoring window left behind when its dataset became a version of another project's root."""
    r = con.execute("SELECT created_by FROM projects WHERE id = ?", (pid,)).fetchone()
    if not r or r[0] != "system":
        return False
    for q in ("SELECT 1 FROM datasets WHERE project_id = ?", "SELECT 1 FROM runs WHERE project_id = ?",
              "SELECT 1 FROM champions WHERE project_id = ?",
              "SELECT 1 FROM annotations WHERE target_kind = 'project' AND target_id = ? AND deleted_at IS NULL"):
        if con.execute(q + " LIMIT 1", (pid,)).fetchone():
            return False
    con.execute("DELETE FROM projects WHERE id = ?", (pid,))
    return True


def backfill(con: sqlite3.Connection, data_dir: Path, *, force: bool = False) -> dict[str, Any]:
    """Fill the new columns and edges from what is already stored. Idempotent: only NULL columns are filled and edges are
    INSERT OR IGNORE, so it can run again after a crash (or with force=True after new old-style data appeared)."""
    done = con.execute("SELECT value FROM catalog_meta WHERE key = 'backfill'").fetchone()
    if done and done[0] == BACKFILL_VERSION and not force:
        return {"skipped": True}
    ds_dir = data_dir / "datasets"
    stats = {"datasets": 0, "projects": 0, "runs": 0, "edges": 0, "inferred_edges": 0}
    n_edges0 = con.execute("SELECT COUNT(*) FROM lineage_edges").fetchone()[0]
    # -- datasets: parent / root from lineage.json --------------------------------------------------------------
    rows = con.execute("SELECT id, name, rows, created_at, profile FROM datasets").fetchall()
    lin: dict[str, dict[str, Any]] = {}
    for r in rows:
        p = ds_dir / r["id"] / "lineage.json"
        try:
            lin[r["id"]] = json.loads(p.read_text()) if p.exists() else {}
        except (OSError, ValueError):
            lin[r["id"]] = {}
    names = {r["id"]: r["name"] for r in rows}

    def root_of(i: str, seen: set[str] | None = None) -> str:
        seen = seen or set()
        lj = lin.get(i) or {}
        par = lj.get("parent")
        if not par or par == i or par in seen or par not in lin:
            return lj.get("root") if lj.get("root") in lin and not par else i
        seen.add(i)
        return root_of(par, seen)

    for r in rows:
        i = r["id"]
        lj = lin[i]
        par = lj.get("parent") if lj.get("parent") in lin and lj.get("parent") != i else None
        root = root_of(i)
        pid = ensure_project(con, root, names.get(root) or root, r["created_at"])
        op = lj.get("kind") or ("autopilot" if lj.get("autopilot") else "active_learning" if lj.get("active_learning")
                                else "annotations" if lj.get("annotations") else "shift_weights" if lj.get("shift_weights")
                                else "sql" if lj.get("source") == "sql" else None)
        if par and not op:
            op = "derived"
        sha = file_sha256(ds_dir / i / "data.csv")
        con.execute("UPDATE datasets SET project_id = COALESCE(project_id, ?), parent_id = COALESCE(parent_id, ?),"
                    " root_id = COALESCE(root_id, ?), sha256 = COALESCE(sha256, ?), n_rows = COALESCE(n_rows, rows),"
                    " op = COALESCE(op, ?) WHERE id = ?", (pid, par, root, sha, op, i))
        if par:
            rr = lj.get("root_rows")
            add_edge(con, ("dataset", par), ("dataset", i), "derived_from", actor=str(lj.get("by") or "system"),
                     meta={"op": op, "rows_before": lj.get("rows_before"), "rows_after": lj.get("rows_after") or r["rows"],
                           "backfilled": True}, inferred=not (rr and len(rr) == r["rows"]), created_at=r["created_at"])
        stats["datasets"] += 1
    # -- runs: indexed columns + trained_on + branched_from ---------------------------------------------------------
    ds_proj = {r[0]: r[1] for r in con.execute("SELECT id, project_id FROM datasets")}
    for r in con.execute("SELECT id, dataset_id, status, created_at, config, result FROM runs").fetchall():
        try:
            cfg = json.loads(r["config"]) or {}
        except ValueError:
            cfg = {}
        try:
            res = json.loads(r["result"]) if r["result"] else None
        except ValueError:
            res = None
        cols = run_columns(cfg, res)
        pp = cfg.get("predictor_path")
        state = (PRESENT if pp and Path(pp).exists() else (EVICTED if pp else None))
        con.execute("UPDATE runs SET project_id = COALESCE(project_id, ?), parent_run_id = COALESCE(parent_run_id, ?),"
                    " source = COALESCE(source, ?), kind = COALESCE(kind, ?), primary_metric = COALESCE(primary_metric, ?),"
                    " primary_score = COALESCE(primary_score, ?), greater_is_better = COALESCE(greater_is_better, ?),"
                    " predictor_state = COALESCE(predictor_state, ?) WHERE id = ?",
                    (ds_proj.get(r["dataset_id"]), cols["parent_run_id"], cols["source"], cols["kind"], cols.get("primary_metric"),
                     cols.get("primary_score"), cols.get("greater_is_better"), state, r["id"]))
        if r["dataset_id"]:
            add_edge(con, ("dataset", r["dataset_id"]), ("run", r["id"]), "trained_on", created_at=r["created_at"])
        if cfg.get("parent_run_id"):
            add_edge(con, ("run", cfg["parent_run_id"]), ("run", r["id"]), "branched_from", meta={**edge_meta_for_run(cfg), "backfilled": True},
                     created_at=r["created_at"])
        for e in (res or {}).get("experiments_extra") or []:  # segment / group-split runs recorded on the parent
            if isinstance(e, dict) and e.get("run_id"):
                add_edge(con, ("run", r["id"]), ("run", e["run_id"]), "branched_from",
                         meta={k: v for k, v in {"source": "doctor-experiment", "experiment": e.get("kind"), "patch": e.get("title"),
                                                 "review_id": e.get("review_id"), "backfilled": True}.items() if v},
                         created_at=e.get("created_at") or r["created_at"])
                con.execute("UPDATE runs SET parent_run_id = COALESCE(parent_run_id, ?), source = CASE WHEN source IS NULL OR source = 'user'"
                            " THEN 'doctor-experiment' ELSE source END WHERE id = ?", (r["id"], e["run_id"]))
        stats["runs"] += 1
    # -- registered versions: built_from -------------------------------------------------------------------------
    for v in con.execute("SELECT id, run_id, created_by, created_at, model_name, version FROM model_versions").fetchall():
        add_edge(con, ("run", v["run_id"]), ("version", v["id"]), "built_from", actor=v["created_by"],
                 meta={"model_name": v["model_name"], "version": v["version"], "backfilled": True}, created_at=v["created_at"])
    stats["projects"] = con.execute("SELECT COUNT(*) FROM projects").fetchone()[0]
    stats["edges"] = con.execute("SELECT COUNT(*) FROM lineage_edges").fetchone()[0] - n_edges0
    stats["inferred_edges"] = con.execute("SELECT COUNT(*) FROM lineage_edges WHERE inferred = 1").fetchone()[0]
    con.execute("INSERT OR REPLACE INTO catalog_meta (key, value) VALUES ('backfill', ?)", (BACKFILL_VERSION,))
    con.execute("INSERT OR REPLACE INTO catalog_meta (key, value) VALUES ('backfill_at', ?)", (now(),))
    return stats


BACKFILL2_VERSION = "1"


def backfill_fp2(con: sqlite3.Connection, data_dir: Path, *, force: bool = False) -> dict[str, Any]:
    """F-P2, once per database (idempotent): remove the empty projects SQL refreshes and monitoring windows left; give
    backfilled roots their op; 'none' (not present / evicted) for runs with no predictor folder and no tombstone; store
    review_id / verdict on review branch edges (so the tree never globs reviews/ per edge); n_changes and op on
    derived_from edges from lineage.json."""
    done = con.execute("SELECT value FROM catalog_meta WHERE key = 'backfill_fp2'").fetchone()
    if done and done[0] == BACKFILL2_VERSION and not force:
        return {"skipped": True}
    out = {"phantom_projects": 0, "root_ops": 0, "predictor_none": 0, "review_edges": 0, "derived_meta": 0}
    for (pid,) in con.execute("SELECT id FROM projects WHERE created_by = 'system'").fetchall():
        out["phantom_projects"] += int(drop_if_phantom(con, pid))
    out["root_ops"] = con.execute("UPDATE datasets SET op = COALESCE(source, 'upload') WHERE parent_id IS NULL AND op IS NULL").rowcount
    # predictor_state: evicted needs a tombstone; present needs the folder
    for r in con.execute("SELECT id, predictor_state, json_extract(config, '$.predictor_path') AS pp,"
                         " json_extract(config, '$.run_dir') AS rd FROM runs WHERE predictor_state IS NULL OR predictor_state IN ('present', 'evicted')").fetchall():
        has = bool(r["pp"]) and Path(r["pp"]).exists()
        tomb = bool(r["rd"]) and (Path(r["rd"]) / "EVICTED.json").exists()
        new = PRESENT if has else (EVICTED if tomb else NONE)
        if new != r["predictor_state"]:
            con.execute("UPDATE runs SET predictor_state = ? WHERE id = ?", (new, r["id"]))
            out["predictor_none"] += int(new == NONE)
    # review branches: the journal says which review ran each branch (and its verdict once the review is over)
    run_dirs = {r[0]: r[1] for r in con.execute("SELECT id, json_extract(config, '$.run_dir') FROM runs")}
    for rid, rd in run_dirs.items():
        d = Path(rd or "/nonexistent") / "reviews"
        if not rd or not d.exists():
            continue
        for p in d.glob("rv_*.json"):
            try:
                rv = json.loads(p.read_text())
            except (OSError, ValueError):
                continue
            over = rv.get("status") in ("done", "stopped", "interrupted", "failed")
            for j in rv.get("journal") or []:
                if not j.get("run_id"):
                    continue
                e = con.execute("SELECT id, meta FROM lineage_edges WHERE src_kind = 'run' AND src_id = ? AND dst_kind = 'run'"
                                " AND dst_id = ? AND relation = 'branched_from'", (rid, j["run_id"])).fetchone()
                if not e:
                    continue
                m = json.loads(e["meta"]) if e["meta"] else {}
                new_m = {**m, "review_id": m.get("review_id") or p.stem}
                if over and j.get("verdict") and not m.get("verdict"):
                    new_m.update(verdict=j.get("verdict"), hypothesis_id=(j.get("hypothesis") or {}).get("id"), step=j.get("step"),
                                 hypothesis=(j.get("hypothesis") or {}).get("title"))
                if new_m != m:
                    con.execute("UPDATE lineage_edges SET meta = ? WHERE id = ?", (json.dumps(new_m, default=str), e["id"]))
                    out["review_edges"] += 1
    # derived_from: n_changes and op from the version's own lineage.json
    for e in con.execute("SELECT id, dst_id, meta FROM lineage_edges WHERE relation = 'derived_from'").fetchall():
        p = data_dir / "datasets" / e["dst_id"] / "lineage.json"
        try:
            lj = json.loads(p.read_text()) if p.exists() else {}
        except (OSError, ValueError):
            lj = {}
        m = json.loads(e["meta"]) if e["meta"] else {}
        ch = lj.get("changes")
        new_m = dict(m)
        if isinstance(ch, list) and ch and not m.get("n_changes"):
            new_m["n_changes"] = len(ch)
        if lj.get("op") and lj["op"] != m.get("op"):
            new_m["op"] = lj["op"]
            con.execute("UPDATE datasets SET op = ? WHERE id = ?", (lj["op"], e["dst_id"]))
        if new_m != m:
            con.execute("UPDATE lineage_edges SET meta = ? WHERE id = ?", (json.dumps(new_m, default=str), e["id"]))
            out["derived_meta"] += 1
    con.execute("INSERT OR REPLACE INTO catalog_meta (key, value) VALUES ('backfill_fp2', ?)", (BACKFILL2_VERSION,))
    return out

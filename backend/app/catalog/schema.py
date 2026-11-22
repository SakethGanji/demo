"""Step 1: schema additions (idempotent) and the backfill from config, lineage.json and model_versions.

Edges run downstream: src is the upstream node, dst the derived one, `relation` says how dst relates to src:
    dataset  → dataset   derived_from    (a version; meta: op, changes summary)
    dataset  → run       trained_on
    run      → run       branched_from   (meta: source, patch, review_id, hypothesis_id, verdict)
    run      → version   built_from
`inferred` = 1 when the edge was reconstructed without a row map (an old version or run): never claim a paired Δ
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
    src_kind    TEXT NOT NULL CHECK (src_kind IN ('dataset', 'run', 'version', 'join_frame')),
    src_id      TEXT NOT NULL,
    dst_kind    TEXT NOT NULL CHECK (dst_kind IN ('dataset', 'run', 'version', 'join_frame')),
    dst_id      TEXT NOT NULL,
    relation    TEXT NOT NULL CHECK (relation IN ('derived_from', 'trained_on', 'branched_from', 'built_from', 'compared_in',
                                                  'joined_from', 'merged_from')),
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
                # DL4 (M-d): one sha256 over the version's split files (read-only since P10; kept for old rows)
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
    migrate_p01(con)
    migrate_p10(con)


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


def audit(con: sqlite3.Connection, actor: str | None, action: str, details: dict[str, Any] | None = None, comment: str = "",
          run_id: str | None = None, **cols: Any) -> int:
    """DL1: an audit_log row written on the caller's connection. Dataset actions carry details.dataset_id and
    details.root_id, so a dataset's history is one query.

    P19 (C §1.7): every row is team-scoped and hash-chained. `cols` may set team_id, project_id, object_kind, object_id,
    principal_id, auth, request_id, decision and the registry's columns (model_name, version, version_id, from_stage,
    to_stage, model_hash). What is not given is filled: the team from the object (dataset → run → model version →
    project → tm_default), the principal, auth and request id from the current request (authz.context). Then
    row_hash = sha256(prev_hash + "\\n" + canonical(row)), prev_hash = the team's previous row_hash. Returns the id."""
    d = details if isinstance(details, dict) else {}
    row: dict[str, Any] = {c: None for c in AUDIT_CHAIN_COLS}
    row.update({"ts": now(), "actor": actor or "system", "action": action, "run_id": run_id, "comment": comment or "",
                "details": json.dumps(d, default=str)})
    row.update({k: v for k, v in cols.items() if k in AUDIT_CHAIN_COLS and k not in ("prev_hash",)})
    row["on_behalf_of"] = cols.get("on_behalf_of")
    if isinstance(row["decision"], dict):
        row["decision"] = json.dumps(row["decision"], sort_keys=True, default=str)
    _audit_scope(con, row, d)
    _audit_who(con, row)
    key = f"audit_head:{row['team_id']}"
    # the write lock first (a write statement), so two writers never chain onto the same head
    con.execute("INSERT INTO catalog_meta (key, value) VALUES (?, '') ON CONFLICT (key) DO UPDATE SET value = value", (key,))
    # P23: on Postgres the head row is also read FOR UPDATE (the upsert above already holds its row lock)
    lock = " FOR UPDATE" if getattr(con, "dialect", "sqlite") == "postgres" else ""
    head = con.execute(f"SELECT value FROM catalog_meta WHERE key = ?{lock}", (key,)).fetchone()[0] or ""
    row["prev_hash"] = head
    h = audit_row_hash(row)
    names = list(AUDIT_CHAIN_COLS) + ["on_behalf_of", "row_hash"]
    new_id = con.execute(f"INSERT INTO audit_log ({', '.join(names)}) VALUES ({', '.join('?' * len(names))}) RETURNING id",
                         [row[c] for c in AUDIT_CHAIN_COLS] + [row["on_behalf_of"], h]).fetchone()[0]
    con.execute("UPDATE catalog_meta SET value = ? WHERE key = ?", (h, key))
    # F-X4b (X4 M6 / m1): the head is also recorded write-once in audit_heads (no UPDATE / DELETE: triggers, and on
    # Postgres the app role has SELECT, INSERT only), so deleting the tail and rewinding catalog_meta fails verify
    con.execute("INSERT INTO audit_heads (team_id, audit_id, head, ts) VALUES (?, ?, ?, ?)", (row["team_id"], int(new_id), h, row["ts"]))
    return int(new_id)


#: the columns a row's hash covers (everything but id and row_hash), in this order
AUDIT_CHAIN_COLS = ("ts", "actor", "action", "model_name", "version", "version_id", "run_id", "from_stage", "to_stage",
                    "comment", "model_hash", "details", "team_id", "project_id", "object_kind", "object_id", "principal_id",
                    "auth", "request_id", "decision", "prev_hash")


def _col(row: Any, c: str) -> Any:
    try:
        return row[c]
    except (KeyError, IndexError):
        return None


def audit_row_hash(row: Any) -> str:
    """sha256(prev_hash + "\\n" + canonical JSON of the chained columns): the same bytes on SQLite and Postgres.
    F-X4b: `on_behalf_of` (the agent's caller) is covered when it is set; rows without it hash as before."""
    r = {c: row[c] for c in AUDIT_CHAIN_COLS}
    if _col(row, "on_behalf_of") is not None:
        r["on_behalf_of"] = _col(row, "on_behalf_of")
    body = json.dumps({k: v for k, v in r.items() if k != "prev_hash"}, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, default=str)
    return hashlib.sha256(f"{r['prev_hash'] or ''}\n{body}".encode()).hexdigest()


def _audit_scope(con: sqlite3.Connection, row: dict[str, Any], d: dict[str, Any]) -> None:
    def one(q: str, a: Any) -> Any:
        try:
            return con.execute(q, (a,)).fetchone()
        except sqlite3.Error:  # an early start (before the P01 columns) audits into tm_default
            return None

    team, project = row.get("team_id") or d.get("team_id"), row.get("project_id") or d.get("project_id")
    if not team:
        for k in ("dataset_id", "root_id"):
            if isinstance(d.get(k), str) and (r := one("SELECT COALESCE(d.team_id, p.team_id), d.project_id FROM datasets d"
                                                     " LEFT JOIN projects p ON p.id = d.project_id WHERE d.id = ?", d[k])):
                team, project = r[0], project or r[1]
                break
    rid = row.get("run_id") or d.get("run_id")
    vid = row.get("version_id") or (d.get("version_id") if str(d.get("version_id") or "").startswith("mv_") else None)
    if not team and vid and (r := one("SELECT run_id FROM model_versions WHERE id = ?", vid)):
        rid = rid or r[0]
    if not team and isinstance(rid, str) and (r := one("SELECT COALESCE(r.team_id, p.team_id), r.project_id FROM runs r"
                                                    " LEFT JOIN projects p ON p.id = r.project_id WHERE r.id = ?", rid)):
        team, project = r[0], project or r[1]
    if not team and isinstance(project, str) and (r := one("SELECT team_id FROM projects WHERE id = ?", project)):
        team = r[0]
    if not team:  # a row that names no object (a refusal): the object this request was authorised against
        try:
            from ..authz.context import current
            dec = [x for x in (current().decisions if current() else []) if x.get("team")]
        except Exception:  # noqa: BLE001
            dec = []
        if dec:
            team = dec[-1]["team"]
            if not row.get("object_kind") and dec[-1].get("object_id"):
                row["object_kind"], row["object_id"] = dec[-1].get("object_kind"), dec[-1]["object_id"]
    row["team_id"] = team or DEFAULT_TEAM
    row["project_id"] = project if isinstance(project, str) else None
    if not row.get("object_kind"):
        for kind, v in (("dataset", d.get("dataset_id")), ("run", rid), ("model_version", vid), ("project", project)):
            if isinstance(v, str) and v:
                row["object_kind"], row["object_id"] = kind, v
                break


def _audit_who(con: sqlite3.Connection, row: dict[str, Any]) -> None:
    from .governance import identity_key

    try:
        from ..authz.context import current
        ctx = current()
    except Exception:  # noqa: BLE001 - no request (a job, a backfill)
        ctx = None
    if ctx is not None:
        row["request_id"] = row.get("request_id") or ctx.request_id
    k = identity_key(row["actor"])
    p = ctx.principal if ctx is not None else None
    if p is not None and p.auth == "agent" and row.get("principal_id") in (None, p.id, p.agent_id):
        # F-X4b (X4 C1/M6): a row written while the agent acts: its own service principal, auth agent, and the caller
        # who started the session (on_behalf_of), whatever name the body carried
        row["principal_id"], row["auth"], row["on_behalf_of"] = p.agent_id, "agent", p.on_behalf_of
        return
    if not row.get("principal_id") and p is not None and identity_key(p.display_name) == k:
        row["principal_id"], row["auth"] = p.id, row.get("auth") or p.auth
    agent_prefix = "Vela agent on behalf of "
    if not row.get("principal_id") and str(row["actor"]).startswith(agent_prefix):
        # F-X4b: a row a background job writes for the agent (batch_scored, …): the actor text was set from the session's
        # bound principal (and checked against it by require() in enforce): the agent's principal + that caller
        try:
            ag = con.execute("SELECT id FROM principals WHERE identity_key = ? AND kind = 'service'", (identity_key("Vela agent"),)).fetchone()
            ow = con.execute("SELECT id FROM principals WHERE identity_key = ?", (identity_key(row["actor"][len(agent_prefix):]),)).fetchone()
        except sqlite3.Error:
            ag = ow = None
        if ag and ow:
            row["principal_id"], row["auth"], row["on_behalf_of"] = ag[0], "agent", row.get("on_behalf_of") or ow[0]
            return
    if not row.get("principal_id") and k:
        try:
            r = con.execute("SELECT id, kind FROM principals WHERE identity_key = ?", (k,)).fetchone()
        except sqlite3.Error:
            r = None
        if r:
            row["principal_id"] = r[0]
            row["auth"] = row.get("auth") or ("typed" if r[1] == "person" else r[1])
    if not row.get("auth"):
        row["auth"] = "system" if k in ("", "system") else "typed"


def verify_audit_chain(con: sqlite3.Connection, team_id: str, data_dir: Any = None) -> dict[str, Any]:
    """Walk a team's chain from its first hashed row: every row re-hashes to its row_hash, links to the previous one, no
    unhashed row appears after the chain starts, and the last row is the recorded head (a deleted tail shows).

    F-X4b (X4 M6 / m1): the tail is also checked against anchors the app cannot rewrite (catalog/audit_anchor.py):
    the latest write-once `audit_heads` row must be the chain's last row, and with `data_dir` the latest signed
    checkpoint's row must still be in the chain with the same hash (and no checkpoint line may fail its signature)."""
    from . import audit_anchor as A

    rows = con.execute("SELECT * FROM audit_log WHERE team_id = ? ORDER BY id", (team_id,)).fetchall()
    head = con.execute("SELECT value FROM catalog_meta WHERE key = ?", (f"audit_head:{team_id}",)).fetchone()
    head = head[0] if head else ""
    anchor = A.latest_head(con, team_id)
    cps, bad = A.read(data_dir) if data_dir is not None else ({}, [])
    cp = cps.get(team_id)
    cp_seen = None
    prev, n, legacy, started, last_id = "", 0, 0, False, None
    for r in rows:
        if r["row_hash"] is None:
            if not started:
                legacy += 1
                continue
            return {"ok": False, "rows": n, "legacy_rows": legacy, "first_break": {
                "id": r["id"], "ts": r["ts"], "action": r["action"], "problem": "a row without a hash after the chain started "
                "(written outside the app)"}}
        started = True
        if (r["prev_hash"] or "") != prev:
            return {"ok": False, "rows": n, "legacy_rows": legacy, "first_break": {
                "id": r["id"], "ts": r["ts"], "action": r["action"], "problem": "prev_hash does not link to the previous row "
                "(a row was removed or inserted before it)", "expected_prev": prev[:16], "found_prev": (r["prev_hash"] or "")[:16]}}
        h = audit_row_hash(r)
        if h != r["row_hash"]:
            return {"ok": False, "rows": n, "legacy_rows": legacy, "first_break": {
                "id": r["id"], "ts": r["ts"], "action": r["action"], "problem": "the row does not re-hash to its row_hash "
                "(it was edited)", "row_hash": r["row_hash"][:16], "recomputed": h[:16]}}
        if cp is not None and int(r["id"]) == int(cp["audit_id"]):
            cp_seen = h
        prev, n, last_id = h, n + 1, int(r["id"])
    if prev != head:
        return {"ok": False, "rows": n, "legacy_rows": legacy, "first_break": {
            "id": None, "problem": "the last row is not the recorded head (rows were removed from the end)",
            "head": head[:16], "last": prev[:16]}}
    anchored: dict[str, Any] = {"audit_heads": anchor["audit_id"] if anchor else None,
                                "checkpoint": {"audit_id": int(cp["audit_id"]), "at": cp["at"]} if cp else None}
    if anchor is not None and (anchor["head"] != prev or anchor["audit_id"] != last_id):
        return {"ok": False, "rows": n, "legacy_rows": legacy, "anchored": anchored, "first_break": {
            "id": anchor["audit_id"], "problem": "the chain ends before the latest head recorded in audit_heads (rows were "
            "removed from the end and the head rewound)", "recorded_head": anchor["head"][:16], "last": prev[:16],
            "last_id": last_id}}
    if cp is not None and cp_seen != cp["head"]:
        return {"ok": False, "rows": n, "legacy_rows": legacy, "anchored": anchored, "first_break": {
            "id": int(cp["audit_id"]), "problem": "a signed checkpoint names a row that is no longer in the chain with that "
            "hash (rows were removed from the end, and their audit_heads too)", "checkpoint_head": cp["head"][:16],
            "checkpoint_at": cp["at"], "last_id": last_id}}
    if bad:
        return {"ok": False, "rows": n, "legacy_rows": legacy, "anchored": anchored, "first_break": {
            "id": None, "problem": f"{len(bad)} checkpoint line(s) fail their signature (the checkpoint file was edited)",
            "lines": [b["line"] for b in bad[:10]]}}
    return {"ok": True, "rows": n, "legacy_rows": legacy, "head": head, "anchored": anchored}


def audit_link(con: sqlite3.Connection, audit_id: int) -> tuple[bool, str]:
    """One row's chain link (P09 split check 3): it re-hashes to its row_hash and the team's next row (or the head)
    links to it. Rows from before the chain started are reported, not failed."""
    r = con.execute("SELECT * FROM audit_log WHERE id = ?", (audit_id,)).fetchone()
    if r is None:
        return False, "no such audit row"
    if r["row_hash"] is None:
        later = con.execute("SELECT 1 FROM audit_log WHERE team_id IS NOT DISTINCT FROM ? AND id < ? AND row_hash IS NOT NULL LIMIT 1",
                            (r["team_id"], audit_id)).fetchone()
        return (False, "no chain link: the row was written outside the app (after the chain started)") if later else \
            (True, "written before the chain started (P19)")
    if audit_row_hash(r) != r["row_hash"]:
        return False, "the row does not re-hash to its row_hash (edited)"
    nxt = con.execute("SELECT prev_hash FROM audit_log WHERE team_id = ? AND id > ? AND row_hash IS NOT NULL ORDER BY id LIMIT 1",
                      (r["team_id"], audit_id)).fetchone()
    if nxt is None:
        head = con.execute("SELECT value FROM catalog_meta WHERE key = ?", (f"audit_head:{r['team_id']}",)).fetchone()
        ok = bool(head and head[0] == r["row_hash"])
        return ok, "chain link ok (the team's head)" if ok else "the row is not linked from the team's head"
    ok = nxt[0] == r["row_hash"]
    return ok, "chain link ok" if ok else "the next row does not link to it"


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
              "SELECT 1 FROM dataset_roots WHERE project_id = ?",  # P01: a root row refers to it (FK)
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
            from ..lake.migrate import legacy_row_map

            mapped = legacy_row_map(lj, r["rows"]) is not None
            add_edge(con, ("dataset", par), ("dataset", i), "derived_from", actor=str(lj.get("by") or "system"),
                     meta={"op": op, "rows_before": lj.get("rows_before"), "rows_after": lj.get("rows_after") or r["rows"],
                           "backfilled": True}, inferred=not mapped, created_at=r["created_at"])
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


# == P01: the data-platform schema (docs/plans/data-platform-final.md §2.1) ==========================================
# Tables are created with IF NOT EXISTS, columns only when missing, and every step id is recorded once in catalog_meta
# ('step:p01_…'), so a second start changes nothing. P10 adds lake_migrations and the ds_hash_ro trigger (migrate_p10).

P01_DDL = """
CREATE TABLE IF NOT EXISTS principals (
  id TEXT PRIMARY KEY, display_name TEXT NOT NULL, identity_key TEXT NOT NULL UNIQUE,
  email TEXT, idp TEXT, subject TEXT,
  kind TEXT NOT NULL DEFAULT 'person' CHECK (kind IN ('person','service','system')),
  is_admin INTEGER NOT NULL DEFAULT 0 CHECK (is_admin IN (0,1)),
  created_at TEXT NOT NULL, last_seen_at TEXT, disabled_at TEXT);
CREATE UNIQUE INDEX IF NOT EXISTS ux_principal_subject ON principals (idp, subject) WHERE subject IS NOT NULL;

CREATE TABLE IF NOT EXISTS teams (
  id TEXT PRIMARY KEY, slug TEXT NOT NULL UNIQUE, name TEXT NOT NULL, description TEXT NOT NULL DEFAULT '',
  state TEXT NOT NULL DEFAULT 'active' CHECK (state IN ('active','archived')),
  policy TEXT NOT NULL DEFAULT '{}',
  storage_quota_bytes BIGINT, store_root TEXT,
  created_at TEXT NOT NULL, created_by TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS team_members (
  team_id TEXT NOT NULL REFERENCES teams(id), principal_id TEXT NOT NULL REFERENCES principals(id),
  role TEXT NOT NULL CHECK (role IN ('viewer','member','maintainer','owner','risk_reviewer')),
  source TEXT NOT NULL DEFAULT 'manual' CHECK (source IN ('manual','idp_group')),
  added_by TEXT NOT NULL, added_at TEXT NOT NULL,
  PRIMARY KEY (team_id, principal_id));
CREATE INDEX IF NOT EXISTS ix_members_principal ON team_members (principal_id);

CREATE TABLE IF NOT EXISTS idp_group_roles (idp TEXT NOT NULL, group_name TEXT NOT NULL, team_id TEXT NOT NULL, role TEXT NOT NULL,
  PRIMARY KEY (idp, group_name, team_id));

CREATE TABLE IF NOT EXISTS shares (
  id TEXT PRIMARY KEY, object_kind TEXT NOT NULL CHECK (object_kind IN ('dataset','project')), object_id TEXT NOT NULL,
  owner_team TEXT NOT NULL, grantee_team TEXT NOT NULL,
  scope TEXT NOT NULL DEFAULT 'certified' CHECK (scope IN ('certified','default','pinned','all')), pinned_version TEXT,
  pii TEXT NOT NULL DEFAULT 'masked' CHECK (pii IN ('masked','unmasked')),
  status TEXT NOT NULL CHECK (status IN ('pending','active','rejected','revoked','expired')),
  requested_by TEXT NOT NULL, requested_at TEXT NOT NULL, reason TEXT NOT NULL DEFAULT '',
  decided_by TEXT, decided_at TEXT, expires_at TEXT, revoked_by TEXT, revoked_at TEXT);
CREATE INDEX IF NOT EXISTS ix_shares_object ON shares (object_kind, object_id, status);
CREATE INDEX IF NOT EXISTS ix_shares_grantee ON shares (grantee_team, status);

CREATE TABLE IF NOT EXISTS approval_requests (
  id TEXT PRIMARY KEY, action TEXT NOT NULL, object_kind TEXT NOT NULL, object_id TEXT NOT NULL, team_id TEXT NOT NULL,
  payload TEXT NOT NULL DEFAULT '{}',
  status TEXT NOT NULL CHECK (status IN ('pending','approved','rejected','withdrawn','used','expired')),
  requested_by TEXT NOT NULL, requested_at TEXT NOT NULL, reason TEXT NOT NULL DEFAULT '',
  decided_by TEXT, decided_at TEXT, comment TEXT NOT NULL DEFAULT '');
CREATE INDEX IF NOT EXISTS ix_approvals_team ON approval_requests (team_id, status);

CREATE TABLE IF NOT EXISTS dataset_roots (
  id TEXT PRIMARY KEY,
  team_id TEXT NOT NULL REFERENCES teams(id), project_id TEXT NOT NULL REFERENCES projects(id),
  name TEXT NOT NULL, description TEXT NOT NULL DEFAULT '', owner TEXT NOT NULL DEFAULT '', source TEXT NOT NULL DEFAULT '',
  label_definition TEXT NOT NULL DEFAULT '', known_issues TEXT NOT NULL DEFAULT '',
  default_version_id TEXT, next_version_no INTEGER NOT NULL DEFAULT 1,
  state TEXT NOT NULL DEFAULT 'active' CHECK (state IN ('active','archived','deleted')),
  created_at TEXT NOT NULL, created_by TEXT NOT NULL, updated_at TEXT, updated_by TEXT);
CREATE INDEX IF NOT EXISTS ix_roots_project ON dataset_roots (project_id, state);
CREATE INDEX IF NOT EXISTS ix_roots_team ON dataset_roots (team_id, state);
CREATE UNIQUE INDEX IF NOT EXISTS ux_roots_name ON dataset_roots (project_id, name) WHERE state <> 'deleted';

CREATE TABLE IF NOT EXISTS dataset_root_tables (
  root_id TEXT NOT NULL REFERENCES dataset_roots(id), table_name TEXT NOT NULL, next_row_id BIGINT NOT NULL,
  PRIMARY KEY (root_id, table_name));

CREATE TABLE IF NOT EXISTS version_tables (
  version_id TEXT NOT NULL REFERENCES datasets(id), table_name TEXT NOT NULL, ordinal INTEGER NOT NULL,
  table_sha256 TEXT NOT NULL, n_rows BIGINT NOT NULL, n_cols INTEGER NOT NULL, bytes BIGINT NOT NULL,
  schema_json TEXT NOT NULL, schema_sha256 TEXT NOT NULL,
  row_id_min BIGINT, row_id_max BIGINT, row_id_sorted INTEGER NOT NULL DEFAULT 1 CHECK (row_id_sorted IN (0,1)),
  change TEXT NOT NULL CHECK (change IN ('new','carried','appended','rewritten')),
  from_version TEXT, from_table TEXT, source_file TEXT,
  PRIMARY KEY (version_id, table_name), UNIQUE (version_id, ordinal));
CREATE INDEX IF NOT EXISTS ix_vt_sha ON version_tables (table_sha256);

CREATE TABLE IF NOT EXISTS blobs (
  sha256 TEXT PRIMARY KEY, key TEXT NOT NULL UNIQUE,
  kind TEXT NOT NULL CHECK (kind IN ('table','split','join_cache','manifest','source','legacy_csv','legacy_json',
                                     'frozen_holdout','sample','audit_archive')),
  bytes BIGINT NOT NULL, n_rows BIGINT,
  state TEXT NOT NULL CHECK (state IN ('pending','live','purging','purged')),
  created_at TEXT NOT NULL, verified_at TEXT, verified_ok INTEGER,
  purged_at TEXT, purged_by TEXT, purge_reason TEXT);
CREATE INDEX IF NOT EXISTS ix_blobs_state ON blobs (state, created_at);

CREATE TABLE IF NOT EXISTS table_files (
  version_id TEXT NOT NULL, table_name TEXT NOT NULL, part INTEGER NOT NULL,
  blob_sha256 TEXT NOT NULL REFERENCES blobs(sha256),
  role TEXT NOT NULL DEFAULT 'base' CHECK (role IN ('base','append','upsert','delete')),
  n_rows BIGINT NOT NULL, row_id_min BIGINT, row_id_max BIGINT,
  PRIMARY KEY (version_id, table_name, part),
  FOREIGN KEY (version_id, table_name) REFERENCES version_tables(version_id, table_name));
CREATE INDEX IF NOT EXISTS ix_tf_blob ON table_files (blob_sha256);

CREATE TABLE IF NOT EXISTS table_keys (
  version_id TEXT NOT NULL, table_name TEXT NOT NULL, columns_json TEXT NOT NULL,
  kind TEXT NOT NULL CHECK (kind IN ('primary','unique')),
  declared_by TEXT NOT NULL,
  valid INTEGER NOT NULL CHECK (valid IN (0,1)), n_duplicates BIGINT NOT NULL DEFAULT 0, n_nulls BIGINT NOT NULL DEFAULT 0,
  PRIMARY KEY (version_id, table_name, kind, columns_json));

CREATE TABLE IF NOT EXISTS table_relations (
  version_id TEXT NOT NULL, name TEXT NOT NULL,
  from_table TEXT NOT NULL, from_cols_json TEXT NOT NULL, to_table TEXT NOT NULL, to_cols_json TEXT NOT NULL,
  cardinality TEXT NOT NULL CHECK (cardinality IN ('n:1','1:1')), declared_by TEXT NOT NULL,
  n_orphans BIGINT NOT NULL DEFAULT 0, PRIMARY KEY (version_id, name));

CREATE TABLE IF NOT EXISTS blob_refs (
  blob_sha256 TEXT NOT NULL REFERENCES blobs(sha256),
  owner_kind TEXT NOT NULL CHECK (owner_kind IN ('version','split','join_frame','run','upload','export')),
  owner_id TEXT NOT NULL, role TEXT NOT NULL,
  team_id TEXT NOT NULL, created_at TEXT NOT NULL,
  PRIMARY KEY (blob_sha256, owner_kind, owner_id, role));
CREATE INDEX IF NOT EXISTS ix_blob_refs_owner ON blob_refs (owner_kind, owner_id);
CREATE INDEX IF NOT EXISTS ix_blob_refs_team ON blob_refs (team_id);

CREATE TABLE IF NOT EXISTS splits (
  id TEXT PRIMARY KEY, version_id TEXT NOT NULL REFERENCES datasets(id), table_name TEXT NOT NULL,
  split_key TEXT NOT NULL, target TEXT, strategy TEXT NOT NULL, params_json TEXT NOT NULL DEFAULT '{}',
  seed INTEGER NOT NULL, holdout_share TEXT NOT NULL, n_folds INTEGER NOT NULL,
  blob_sha256 TEXT NOT NULL REFERENCES blobs(sha256), split_sha256 TEXT NOT NULL,
  legacy_sha256 TEXT,
  n_rows BIGINT NOT NULL, n_holdout BIGINT NOT NULL,
  carried_from TEXT REFERENCES splits(id), created_at TEXT NOT NULL, created_by TEXT NOT NULL,
  UNIQUE (version_id, table_name, split_key));

CREATE TABLE IF NOT EXISTS joins (
  id TEXT PRIMARY KEY, root_id TEXT NOT NULL REFERENCES dataset_roots(id), team_id TEXT NOT NULL,
  name TEXT NOT NULL, spec_version INTEGER NOT NULL, spec_json TEXT NOT NULL, spec_sha256 TEXT NOT NULL,
  state TEXT NOT NULL DEFAULT 'active' CHECK (state IN ('active','archived')),
  created_at TEXT NOT NULL, created_by TEXT NOT NULL,
  UNIQUE (root_id, name, spec_version));
CREATE TABLE IF NOT EXISTS join_frames (
  id TEXT PRIMARY KEY, join_id TEXT NOT NULL REFERENCES joins(id), version_id TEXT NOT NULL REFERENCES datasets(id),
  grain_table TEXT NOT NULL, inputs_json TEXT NOT NULL,
  frame_sha256 TEXT NOT NULL,
  n_rows BIGINT NOT NULL, schema_json TEXT NOT NULL,
  cache_blob TEXT REFERENCES blobs(sha256),
  created_at TEXT NOT NULL, created_by TEXT NOT NULL,
  UNIQUE (join_id, version_id), UNIQUE (frame_sha256));

CREATE TABLE IF NOT EXISTS run_inputs (
  id TEXT PRIMARY KEY, run_id TEXT NOT NULL,
  role TEXT NOT NULL CHECK (role IN ('train','score','reference','join_input')),
  version_id TEXT, table_name TEXT, join_frame_id TEXT,
  table_sha256 TEXT, read_sha256 TEXT, match INTEGER,
  legacy_csv_sha256 TEXT,
  split_id TEXT, split_sha256 TEXT, split_legacy_sha256 TEXT,
  read_mode TEXT NOT NULL DEFAULT 'engine_v1',
  sample_spec TEXT, sample_sha256 TEXT, rows_total BIGINT, rows_used BIGINT, frame_mb INTEGER, columns_dropped TEXT,
  created_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS ix_run_inputs_run ON run_inputs (run_id);
CREATE INDEX IF NOT EXISTS ix_run_inputs_version ON run_inputs (version_id, table_name);
CREATE INDEX IF NOT EXISTS ix_run_inputs_split ON run_inputs (split_id);
CREATE INDEX IF NOT EXISTS ix_run_inputs_jf ON run_inputs (join_frame_id);

CREATE TABLE IF NOT EXISTS uploads (
  id TEXT PRIMARY KEY, team_id TEXT NOT NULL, project_id TEXT NOT NULL, root_id TEXT, parent_version_id TEXT,
  status TEXT NOT NULL CHECK (status IN ('staged','checked','committed','discarded','expired')),
  files_json TEXT NOT NULL, plan_json TEXT NOT NULL DEFAULT '{}', check_json TEXT, check_sha256 TEXT,
  committed_version_id TEXT, created_by TEXT NOT NULL, created_at TEXT NOT NULL, expires_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS table_stats (table_sha256 TEXT PRIMARY KEY, stats_version INTEGER NOT NULL, computed_at TEXT NOT NULL,
  n_rows BIGINT NOT NULL, n_cols INTEGER NOT NULL, columns TEXT NOT NULL, seconds REAL NOT NULL);
CREATE TABLE IF NOT EXISTS diff_cache (a_sha256 TEXT NOT NULL, b_sha256 TEXT NOT NULL, key TEXT NOT NULL, computed_at TEXT NOT NULL,
  result TEXT NOT NULL, PRIMARY KEY (a_sha256, b_sha256, key));
CREATE TABLE IF NOT EXISTS storage_usage (team_id TEXT NOT NULL, computed_at TEXT NOT NULL, bytes_referenced BIGINT NOT NULL,
  bytes_unique BIGINT NOT NULL, n_blobs BIGINT NOT NULL, PRIMARY KEY (team_id, computed_at));
CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY, kind TEXT NOT NULL, team_id TEXT, resource TEXT NOT NULL, mem_mb INTEGER NOT NULL,
  priority INTEGER NOT NULL DEFAULT 0, payload TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('queued','leased','succeeded','failed','cancelled')),
  attempts INTEGER NOT NULL DEFAULT 0, max_attempts INTEGER NOT NULL DEFAULT 2, created_at TEXT NOT NULL, not_before TEXT,
  leased_by TEXT, lease_until TEXT, started_at TEXT, finished_at TEXT, error TEXT, result TEXT,
  by_principal TEXT NOT NULL, ref_kind TEXT, ref_id TEXT);
CREATE INDEX IF NOT EXISTS ix_jobs_claim ON jobs (status, resource, priority DESC, created_at);
CREATE INDEX IF NOT EXISTS ix_jobs_ref ON jobs (ref_kind, ref_id);
CREATE TABLE IF NOT EXISTS workers (id TEXT PRIMARY KEY, host TEXT NOT NULL, started_at TEXT NOT NULL, heartbeat_at TEXT NOT NULL,
  slots TEXT NOT NULL, version TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS certifications (id TEXT PRIMARY KEY, root_id TEXT NOT NULL, version_id TEXT NOT NULL,
  level TEXT NOT NULL CHECK (level IN ('certified','approved_for_model')),
  status TEXT NOT NULL CHECK (status IN ('pending','active','rejected','superseded','expired','revoked')),
  contract_id TEXT, contract_result TEXT, requested_by TEXT NOT NULL, requested_at TEXT NOT NULL,
  reason TEXT NOT NULL DEFAULT '', decided_by TEXT, decided_at TEXT, comment TEXT NOT NULL DEFAULT '', expires_at TEXT);
CREATE INDEX IF NOT EXISTS ix_cert_root ON certifications (root_id, status);
CREATE TABLE IF NOT EXISTS legal_holds (id TEXT PRIMARY KEY,
  scope_kind TEXT NOT NULL CHECK (scope_kind IN ('team','project','dataset','version','run','model')), scope_id TEXT NOT NULL,
  case_ref TEXT NOT NULL, reason TEXT NOT NULL, placed_by TEXT NOT NULL, placed_at TEXT NOT NULL,
  released_by TEXT, released_at TEXT);
CREATE INDEX IF NOT EXISTS ix_holds_scope ON legal_holds (scope_kind, scope_id) WHERE released_at IS NULL;
CREATE TABLE IF NOT EXISTS column_tags (root_id TEXT NOT NULL, table_name TEXT NOT NULL, column_name TEXT NOT NULL,
  tag TEXT NOT NULL CHECK (tag IN ('pii_direct','pii_quasi','protected','sensitive','key','none')),
  mask TEXT NOT NULL CHECK (mask IN ('redact','hash','last4','year_only','bucket','none')),
  source TEXT NOT NULL CHECK (source IN ('suggested','confirmed')), set_by TEXT NOT NULL, set_at TEXT NOT NULL,
  PRIMARY KEY (root_id, table_name, column_name));
CREATE TABLE IF NOT EXISTS data_contracts (id TEXT PRIMARY KEY, root_id TEXT NOT NULL, version INTEGER NOT NULL, spec TEXT NOT NULL,
  created_by TEXT NOT NULL, created_at TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1, UNIQUE (root_id, version));
-- F-X4b (X4 M6 / m1): every audit head, write-once (ahead_ro_u / ahead_ro_d; on Postgres the app role has SELECT, INSERT).
CREATE TABLE IF NOT EXISTS audit_heads (team_id TEXT NOT NULL, audit_id INTEGER NOT NULL, head TEXT NOT NULL, ts TEXT NOT NULL,
  PRIMARY KEY (team_id, audit_id));
"""

#: columns added to existing tables (none removed); SQLite allows a CHECK and a constant default on an added column
P01_COLS: dict[str, list[tuple[str, str]]] = {
    "projects": [("team_id", "TEXT NOT NULL DEFAULT 'tm_default'"), ("state", "TEXT NOT NULL DEFAULT 'active'")],
    "datasets": [("team_id", "TEXT"), ("version_no", "INTEGER"),
                 ("storage", "TEXT NOT NULL DEFAULT 'csv' CHECK (storage IN ('csv','parquet'))"),
                 ("manifest_sha256", "TEXT"),  # commit hash; NULL while storage='csv'
                 ("data_sha256", "TEXT"),      # dedupe identity
                 ("legacy_sha256", "TEXT"),    # the data.csv sha256 (= old `sha256`), kept forever
                 ("n_tables", "INTEGER NOT NULL DEFAULT 1"), ("n_cols", "INTEGER"), ("primary_table", "TEXT"),
                 ("reason", "TEXT NOT NULL DEFAULT ''"), ("job_id", "TEXT"), ("contract_result", "TEXT")],
    "runs": [("team_id", "TEXT"), ("created_by", "TEXT")],
    "audit_log": [(c, "TEXT") for c in ("team_id", "project_id", "object_kind", "object_id", "principal_id", "auth", "request_id",
                                        "decision", "prev_hash", "row_hash", "on_behalf_of")],
    "jobs": [("progress", "TEXT")],  # F-JOBS: {pct, phase, at} JSON text, written by the holder
}
P01_INDEXES = [
    "CREATE INDEX IF NOT EXISTS ix_projects_team ON projects (team_id)",
    "CREATE UNIQUE INDEX IF NOT EXISTS ux_versions_no ON datasets (root_id, version_no)",
    "CREATE INDEX IF NOT EXISTS ix_versions_data_sha ON datasets (root_id, data_sha256)",
    "CREATE INDEX IF NOT EXISTS ix_versions_project ON datasets (project_id, created_at)",
    "CREATE INDEX IF NOT EXISTS ix_versions_team ON datasets (team_id, created_at)",
    "CREATE INDEX IF NOT EXISTS ix_versions_parent ON datasets (parent_id)",
    "CREATE INDEX IF NOT EXISTS ix_runs_team ON runs (team_id, created_at)",
    "CREATE INDEX IF NOT EXISTS ix_audit_team_ts ON audit_log (team_id, ts)",
    "CREATE INDEX IF NOT EXISTS ix_audit_object ON audit_log (object_kind, object_id, ts)",
    "CREATE INDEX IF NOT EXISTS ix_audit_actor ON audit_log (actor, ts)",
]
#: the protecting rows must not be rewritable through the app or a stray UPDATE (not a defence against file access)
IMMUTABLE_TRIGGERS = {
    "splits_ro_u": "CREATE TRIGGER IF NOT EXISTS splits_ro_u BEFORE UPDATE ON splits BEGIN SELECT RAISE(ABORT, 'splits are immutable'); END",
    "splits_ro_d": "CREATE TRIGGER IF NOT EXISTS splits_ro_d BEFORE DELETE ON splits BEGIN SELECT RAISE(ABORT, 'splits are immutable'); END",
    "vt_ro_u": "CREATE TRIGGER IF NOT EXISTS vt_ro_u BEFORE UPDATE ON version_tables BEGIN SELECT RAISE(ABORT, 'version tables are immutable'); END",
    "tf_ro_u": "CREATE TRIGGER IF NOT EXISTS tf_ro_u BEFORE UPDATE ON table_files BEGIN SELECT RAISE(ABORT, 'table files are immutable'); END",
    "ri_ro_u": "CREATE TRIGGER IF NOT EXISTS ri_ro_u BEFORE UPDATE OF table_sha256, read_sha256, split_sha256, sample_sha256 ON run_inputs"
               " BEGIN SELECT RAISE(ABORT, 'run inputs are immutable'); END",
    # F-X1 M6: a deleted run_inputs row would remove split check 4's evidence (the hash a run trained on)
    "ri_ro_d": "CREATE TRIGGER IF NOT EXISTS ri_ro_d BEFORE DELETE ON run_inputs BEGIN SELECT RAISE(ABORT, 'run inputs are immutable'); END",
    # F-X4b: the audit heads are write-once (verify compares the chain's last row with the latest one)
    "ahead_ro_u": "CREATE TRIGGER IF NOT EXISTS ahead_ro_u BEFORE UPDATE ON audit_heads BEGIN SELECT RAISE(ABORT, 'audit heads are write-once'); END",
    "ahead_ro_d": "CREATE TRIGGER IF NOT EXISTS ahead_ro_d BEFORE DELETE ON audit_heads BEGIN SELECT RAISE(ABORT, 'audit heads are write-once'); END",
    "blobs_key_ro": "CREATE TRIGGER IF NOT EXISTS blobs_key_ro BEFORE UPDATE OF sha256, key, bytes ON blobs"
                    " BEGIN SELECT RAISE(ABORT, 'blob identity is immutable'); END",
}
#: P10 (§2.1): a version's hashes are written once. The WHEN clause lets the migration set manifest_sha256 / data_sha256
#: the first time (a CSV-era row has only legacy_sha256), and nothing after that; legacy_sha256 never changes once set.
DS_HASH_RO = ("CREATE TRIGGER IF NOT EXISTS ds_hash_ro BEFORE UPDATE OF manifest_sha256, data_sha256, legacy_sha256 ON datasets"
              " WHEN (OLD.manifest_sha256 IS NOT NULL AND (NEW.manifest_sha256 IS NOT OLD.manifest_sha256"
              " OR NEW.data_sha256 IS NOT OLD.data_sha256 OR NEW.legacy_sha256 IS NOT OLD.legacy_sha256))"
              " OR (OLD.legacy_sha256 IS NOT NULL AND NEW.legacy_sha256 IS NOT OLD.legacy_sha256)"
              " BEGIN SELECT RAISE(ABORT, 'version hashes are immutable'); END")
#: P10: one row per CSV-era version the migration handled (migrated / skipped and why / failed), and its folder step
P10_DDL = """
CREATE TABLE IF NOT EXISTS lake_migrations (
  version_id TEXT PRIMARY KEY, root_id TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('migrated','skipped','failed')),
  reason TEXT, message TEXT, detail TEXT, legacy_sha256 TEXT, manifest_sha256 TEXT,
  folder_state TEXT, at TEXT NOT NULL, job_id TEXT, attempts INTEGER NOT NULL DEFAULT 1);
CREATE INDEX IF NOT EXISTS ix_lake_migrations_status ON lake_migrations (status, at);
"""


def migrate_p10(con: sqlite3.Connection) -> None:
    """P10: the migration's bookkeeping table, the CSV-era split-file table (read by the migration only), and ds_hash_ro."""
    from ..lake.migrate import LEGACY_DDL

    con.executescript(P10_DDL + LEGACY_DDL)
    con.execute(DS_HASH_RO)
    _step(con, "p10_ddl")


#: created once the P01 backfill has written audit_log.team_id (the backfill is the last UPDATE audit_log ever gets)
AUDIT_TRIGGERS = {
    "audit_ro_u": "CREATE TRIGGER IF NOT EXISTS audit_ro_u BEFORE UPDATE ON audit_log BEGIN SELECT RAISE(ABORT, 'audit is append-only'); END",
    "audit_ro_d": "CREATE TRIGGER IF NOT EXISTS audit_ro_d BEFORE DELETE ON audit_log BEGIN SELECT RAISE(ABORT, 'audit is append-only'); END",
}
DEFAULT_TEAM = "tm_default"
P01_BACKFILL_VERSION = "1"


def _step(con: sqlite3.Connection, step: str) -> None:
    """Record a migration step id once (the first time it ran); a later start leaves the row untouched."""
    con.execute("INSERT OR IGNORE INTO catalog_meta (key, value) VALUES (?, ?)", (f"step:{step}", now()))


def _has(con: sqlite3.Connection, kind: str, name: str) -> bool:
    return con.execute("SELECT 1 FROM sqlite_master WHERE type = ? AND name = ?", (kind, name)).fetchone() is not None


def _lineage_ddl() -> list[str]:
    part = SCHEMA[SCHEMA.index("CREATE TABLE IF NOT EXISTS lineage_edges"):SCHEMA.index("-- Tags and notes")]
    return [s.strip() for s in part.split(";") if s.strip()]


def migrate_p01(con: sqlite3.Connection) -> None:
    con.executescript(P01_DDL)
    for table, cols in P01_COLS.items():
        have = {r[1] for r in con.execute(f"PRAGMA table_info({table})")}
        for c, typ in cols:
            if c not in have:
                con.execute(f"ALTER TABLE {table} ADD COLUMN {c} {typ}")
    for q in P01_INDEXES:
        con.execute(q)
    for q in IMMUTABLE_TRIGGERS.values():
        con.execute(q)
    seed_audit_heads(con)
    _step(con, "p01_ddl")
    # lineage_edges: kinds += join_frame, relations += joined_from / merged_from. SQLite cannot alter a CHECK: rebuild
    # once, ids kept (the champions pattern of F-P2, in one transaction).
    sql = (con.execute("SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'lineage_edges'").fetchone() or [""])[0] or ""
    if "join_frame" not in sql:
        con.commit()
        con.execute("BEGIN")
        con.execute("ALTER TABLE lineage_edges RENAME TO lineage_edges_old")
        con.execute("DROP INDEX IF EXISTS ix_edges_src")
        con.execute("DROP INDEX IF EXISTS ix_edges_dst")
        for q in _lineage_ddl():
            con.execute(q)
        cols = "id, src_kind, src_id, dst_kind, dst_id, relation, actor, meta, inferred, created_at"
        con.execute(f"INSERT INTO lineage_edges ({cols}) SELECT {cols} FROM lineage_edges_old")
        con.execute("DROP TABLE lineage_edges_old")
        con.commit()
    _step(con, "p01_lineage_check")
    ensure_default_team(con)
    _step(con, "p01_team_default")
    if con.execute("SELECT 1 FROM catalog_meta WHERE key = 'backfill_p01'").fetchone():
        for q in AUDIT_TRIGGERS.values():
            con.execute(q)


def seed_audit_heads(con: Any) -> int:
    """F-X4b: a database whose chains started before audit_heads existed gets each team's current head recorded once,
    so the anchor starts there (rows before it are covered by the chain links only)."""
    return con.execute(
        "INSERT INTO audit_heads (team_id, audit_id, head, ts) SELECT a.team_id, a.id, a.row_hash, a.ts FROM audit_log a"
        " JOIN catalog_meta m ON m.key = 'audit_head:' || a.team_id AND m.value = a.row_hash"
        " WHERE a.team_id IS NOT NULL AND NOT EXISTS (SELECT 1 FROM audit_heads h WHERE h.team_id = a.team_id)"
        " ON CONFLICT (team_id, audit_id) DO NOTHING").rowcount


def ensure_default_team(con: sqlite3.Connection) -> None:
    con.execute("INSERT OR IGNORE INTO teams (id, slug, name, description, created_at, created_by) VALUES (?, 'default', 'Default team',"
                " 'Every project made before teams existed.', ?, 'system')", (DEFAULT_TEAM, now()))


def team_of_project(con: sqlite3.Connection, project_id: str | None) -> str:
    r = con.execute("SELECT team_id FROM projects WHERE id = ?", (project_id,)).fetchone() if project_id else None
    return (r[0] if r and r[0] else None) or DEFAULT_TEAM


def free_root_name(con: sqlite3.Connection, project_id: str, name: str, root: str) -> str:
    """ux_roots_name: one live name per project. Two roots with the same name in one project (possible before P01) keep
    their datasets.name; the second gets its id appended in dataset_roots."""
    taken = con.execute("SELECT 1 FROM dataset_roots WHERE project_id = ? AND name = ? AND id <> ? AND state <> 'deleted'",
                        (project_id, name, root)).fetchone()
    return f"{name} ({root})" if taken else name


def root_state(con: sqlite3.Connection, root: str) -> str:
    """dsets.dataset_state in SQL: deleted when every version is, archived when every remaining one is."""
    states = [r[0] for r in con.execute("SELECT state FROM datasets WHERE id = ? OR root_id = ?", (root, root))]
    if states and all(s == "deleted" for s in states):
        return "deleted"
    live = [s for s in states if s != "deleted"]
    return "archived" if live and all(s == "archived" for s in live) else "active"


def ensure_root(con: sqlite3.Connection, root: str) -> bool:
    """The dataset_roots row of a root dataset, from its datasets row and its card (DL1). True when it was created."""
    if con.execute("SELECT 1 FROM dataset_roots WHERE id = ?", (root,)).fetchone():
        return False
    r = con.execute("SELECT id, name, project_id, created_at, created_by FROM datasets WHERE id = ?", (root,)).fetchone()
    if not r:
        return False
    pid = r["project_id"] or project_id_for(root)
    con.execute("INSERT OR IGNORE INTO projects (id, name, root_dataset_id, created_at) VALUES (?, ?, ?, ?)",
                (pid, (r["name"] or root).replace(" (sample)", ""), root, r["created_at"] or now()))
    c = con.execute("SELECT * FROM dataset_cards WHERE root_id = ?", (root,)).fetchone()
    c = dict(c) if c else {}
    nmax = con.execute("SELECT MAX(version_no) FROM datasets WHERE id = ? OR root_id = ?", (root, root)).fetchone()[0] or 0
    state = root_state(con, root)
    name = r["name"] or root
    if state != "deleted":
        name = free_root_name(con, pid, name, root)
    con.execute("INSERT INTO dataset_roots (id, team_id, project_id, name, description, owner, source, label_definition, known_issues,"
                " default_version_id, next_version_no, state, created_at, created_by, updated_at, updated_by)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (root, team_of_project(con, pid), pid, name, c.get("description") or "", c.get("owner") or "", c.get("source") or "",
                 c.get("label_definition") or "", c.get("known_issues") or "", c.get("default_version_id"), nmax + 1, state,
                 r["created_at"] or now(), r["created_by"] or "system", c.get("updated_at"), c.get("updated_by")))
    return True


def _alloc_version_no(con: sqlite3.Connection, root: str) -> int:
    nmax = con.execute("SELECT MAX(version_no) FROM datasets WHERE id = ? OR root_id = ?", (root, root)).fetchone()[0] or 0
    nxt = con.execute("SELECT next_version_no FROM dataset_roots WHERE id = ?", (root,)).fetchone()
    n = max(nmax + 1, nxt[0] if nxt else 1)
    con.execute("UPDATE dataset_roots SET next_version_no = ? WHERE id = ?", (n + 1, root))
    return n


def sync_version(con: sqlite3.Connection, dataset_id: str) -> None:
    """P01, called by the catalog write hooks once a version has its root and project: its dataset_roots row, version_no
    (allocated from the root), team_id, n_cols and legacy_sha256 (= the data.csv sha256 while storage is 'csv')."""
    r = con.execute("SELECT id, root_id, project_id, version_no FROM datasets WHERE id = ?", (dataset_id,)).fetchone()
    if not r:
        return
    root = r["root_id"] or r["id"]
    if root != r["id"]:  # it was its own root before it became a version of another: that root is gone (nothing refers to it yet)
        dep = root_has_dependants(con, r["id"])
        if dep:  # P02: never drop a root row that lake rows hang off (the VersionWriter never re-roots; legacy paths must not)
            raise ValueError(f"{r['id']} cannot become a version of {root}: its dataset root has {dep}")
        con.execute("DELETE FROM dataset_roots WHERE id = ?", (r["id"],))
    ensure_root(con, root)
    n = r["version_no"] if r["version_no"] is not None else _alloc_version_no(con, root)
    con.execute("UPDATE datasets SET version_no = ?, team_id = ?,"
                " legacy_sha256 = CASE WHEN COALESCE(storage, 'csv') = 'csv' THEN sha256 ELSE legacy_sha256 END,"
                " n_cols = CASE WHEN json_valid(profile) THEN CAST(json_extract(profile, '$.n_cols') AS INTEGER) ELSE n_cols END"
                " WHERE id = ?", (n, team_of_project(con, r["project_id"]), dataset_id))
    con.execute("UPDATE dataset_roots SET state = ? WHERE id = ?", (root_state(con, root), root))


#: P02: rows that refer to a dataset root (or mark it as a lake dataset); sync_version refuses to delete such a root row
ROOT_DEPENDANTS = (("dataset_root_tables", "SELECT 1 FROM dataset_root_tables WHERE root_id = ?"),
                   ("joins", "SELECT 1 FROM joins WHERE root_id = ?"),
                   ("column_tags", "SELECT 1 FROM column_tags WHERE root_id = ?"),
                   ("data_contracts", "SELECT 1 FROM data_contracts WHERE root_id = ?"),
                   ("certifications", "SELECT 1 FROM certifications WHERE root_id = ?"),
                   ("parquet versions", "SELECT 1 FROM datasets WHERE (id = ? OR root_id = ?) AND storage = 'parquet'"))


def root_has_dependants(con: sqlite3.Connection, root: str) -> str | None:
    for what, q in ROOT_DEPENDANTS:
        if con.execute(q + " LIMIT 1", (root,) * q.count("?")).fetchone():
            return what
    return None


def sync_root_state(con: sqlite3.Connection, root: str) -> None:
    con.execute("UPDATE dataset_roots SET state = ? WHERE id = ?", (root_state(con, root), root))


def _principal_names(con: sqlite3.Connection) -> list[str]:
    """Every distinct actor name the database knows (P01 backfill step 2), in first-seen order."""
    qs = ["SELECT actor FROM audit_log ORDER BY id",
          "SELECT created_by FROM datasets ORDER BY created_at",
          "SELECT json_extract(config, '$.by') FROM runs WHERE json_valid(config) ORDER BY created_at"]
    for t, cols in (("dataset_requests", ("requested_by", "decided_by")), ("version_requests", ("requested_by", "decided_by")),
                    ("admin_requests", ("requested_by", "decided_by", "used_by")), ("champions", ("requested_by", "decided_by"))):
        qs += [f"SELECT {c} FROM {t} ORDER BY requested_at" for c in cols]
    seen: dict[str, None] = {}
    for q in qs:
        for (v,) in con.execute(q):
            if isinstance(v, str) and v.strip():
                seen.setdefault(v.strip(), None)
    import os

    for a in os.environ.get("ENSEMBLE_ADMINS", "").split(","):
        if a.strip():
            seen.setdefault(a.strip(), None)
    return list(seen)


def principal_id_for(key: str) -> str:
    return "pr_" + hashlib.sha256(key.encode()).hexdigest()[:12]


def backfill_p01(con: sqlite3.Connection, *, force: bool = False) -> dict[str, Any]:
    """P01 backfill (§2.1), idempotent: only NULL columns and missing rows are filled. Then the audit triggers."""
    from .governance import admins, identity_key

    done = con.execute("SELECT value FROM catalog_meta WHERE key = 'backfill_p01'").fetchone()
    if done and done[0] == P01_BACKFILL_VERSION and not force:
        return {"skipped": True}
    ts = now()
    out: dict[str, Any] = {}
    # 1. the default team; every project without a known team is in it
    ensure_default_team(con)
    con.execute("UPDATE projects SET team_id = ? WHERE team_id IS NULL OR team_id NOT IN (SELECT id FROM teams)", (DEFAULT_TEAM,))
    out["projects_in_default_team"] = con.execute("SELECT COUNT(*) FROM projects WHERE team_id = ?", (DEFAULT_TEAM,)).fetchone()[0]
    # 2. principals: one per identity key (the four-eyes key); admins → owners of tm_default, people → members
    names = _principal_names(con)
    adm = admins()
    keys: dict[str, str] = {}
    for n in names:
        k = identity_key(n)
        if k:
            keys.setdefault(k, n)
    for k, n in keys.items():
        pid = principal_id_for(k)
        kind = "system" if k == "system" else "person"
        con.execute("INSERT OR IGNORE INTO principals (id, display_name, identity_key, kind, is_admin, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                    (pid, n, k, kind, int(k in adm), ts))
        if kind == "person":
            con.execute("INSERT OR IGNORE INTO team_members (team_id, principal_id, role, source, added_by, added_at)"
                        " VALUES (?, ?, ?, 'manual', 'system', ?)", (DEFAULT_TEAM, pid, "owner" if k in adm else "member", ts))
    out["actor_names"] = len(names)
    out["actor_identity_keys"] = len(keys)
    out["principals"] = con.execute("SELECT COUNT(*) FROM principals").fetchone()[0]
    out["team_members"] = con.execute("SELECT COUNT(*) FROM team_members WHERE team_id = ?", (DEFAULT_TEAM,)).fetchone()[0]
    # 3. dataset_roots from the root rows and dataset_cards
    roots = [r[0] for r in con.execute("SELECT DISTINCT COALESCE(root_id, id) FROM datasets")]
    out["roots_created"] = sum(int(ensure_root(con, rt)) for rt in roots)
    out["roots"] = con.execute("SELECT COUNT(*) FROM dataset_roots").fetchone()[0]
    out["root_names_suffixed"] = con.execute("SELECT COUNT(*) FROM dataset_roots r JOIN datasets d ON d.id = r.id WHERE r.name <> d.name").fetchone()[0]
    # 4. versions: version_no in creation order per root (= the history's v), team, n_cols, legacy hash, storage
    renumbered = 0
    for rt in roots:
        if con.execute("SELECT 1 FROM datasets WHERE (id = ? OR root_id = ?) AND version_no IS NULL", (rt, rt)).fetchone():
            con.execute("UPDATE datasets SET version_no = NULL WHERE id = ? OR root_id = ?", (rt, rt))
            ids = [r[0] for r in con.execute("SELECT id FROM datasets WHERE id = ? OR root_id = ? ORDER BY created_at, id", (rt, rt))]
            for i, did in enumerate(ids):
                con.execute("UPDATE datasets SET version_no = ? WHERE id = ?", (i + 1, did))
            renumbered += 1
        con.execute("UPDATE dataset_roots SET next_version_no = (SELECT COALESCE(MAX(version_no), 0) + 1 FROM datasets"
                    " WHERE id = ? OR root_id = ?) WHERE id = ?", (rt, rt, rt))
    out["roots_numbered"] = renumbered
    con.execute("UPDATE datasets SET team_id = COALESCE(team_id, (SELECT p.team_id FROM projects p WHERE p.id = datasets.project_id), ?),"
                " legacy_sha256 = CASE WHEN COALESCE(storage, 'csv') = 'csv' THEN COALESCE(legacy_sha256, sha256) ELSE legacy_sha256 END,"
                " n_cols = COALESCE(n_cols, CASE WHEN json_valid(profile) THEN CAST(json_extract(profile, '$.n_cols') AS INTEGER) END),"
                " storage = COALESCE(storage, 'csv')", (DEFAULT_TEAM,))
    out["versions"] = con.execute("SELECT COUNT(*) FROM datasets").fetchone()[0]
    out["versions_numbered"] = con.execute("SELECT COUNT(*) FROM datasets WHERE version_no IS NOT NULL").fetchone()[0]
    # 5. runs: the team of the run's dataset (else its project's)
    out["runs_team_set"] = con.execute(
        "UPDATE runs SET team_id = COALESCE((SELECT d.team_id FROM datasets d WHERE d.id = runs.dataset_id),"
        " (SELECT p.team_id FROM projects p WHERE p.id = runs.project_id)) WHERE team_id IS NULL").rowcount
    # 6. audit rows: the team of details.dataset_id (else details.root_id). Only before the append-only trigger exists.
    if not _has(con, "trigger", "audit_ro_u"):
        out["audit_team_set"] = con.execute(
            "UPDATE audit_log SET team_id = COALESCE((SELECT d.team_id FROM datasets d WHERE d.id = json_extract(audit_log.details, '$.dataset_id')),"
            " (SELECT d.team_id FROM datasets d WHERE d.id = json_extract(audit_log.details, '$.root_id')))"
            " WHERE team_id IS NULL AND json_valid(details) AND (json_extract(details, '$.dataset_id') IN (SELECT id FROM datasets)"
            " OR json_extract(details, '$.root_id') IN (SELECT id FROM datasets))").rowcount
    for q in AUDIT_TRIGGERS.values():
        con.execute(q)
    con.execute("INSERT OR REPLACE INTO catalog_meta (key, value) VALUES ('backfill_p01', ?)", (P01_BACKFILL_VERSION,))
    con.execute("INSERT OR REPLACE INTO catalog_meta (key, value) VALUES ('backfill_p01_at', ?)", (ts,))
    _step(con, "p01_backfill")
    return out

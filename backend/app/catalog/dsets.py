"""DL1: a dataset (a root and its versions) as an analyst manages it.

history   every version with who, when, op, changes, rows × columns, hash, integrity, use; the card; the default
diff      any two versions: schema, rows (by the parent's row map, root rows or an exact hash), per-column shifts
usage     reverse lineage: the runs, registered versions and champions that used a version (and its descendants)
default   the version Build opens: a pointer; a rollback moves it back and never touches data
card      description, owner, source, label definition, known issues (and the name)
state     archive (hidden, refused while in use) and delete (four eyes or an admin; files removed, a tombstone and every
          lineage record kept)
verify    re-hash a version's file against the sha256 stored when it was made
search    by text, project, tag, owner, op and date
Every action writes an audit_log row with details.dataset_id and details.root_id."""

from __future__ import annotations

import json
import secrets
import shutil
from typing import Any

import numpy as np

from .. import frames
from . import governance as G
from . import schema as S
from .schema import audit, file_sha256, now

ACTIVE, ARCHIVED, DELETED = None, "archived", "deleted"
CARD_FIELDS = {"description": 2000, "owner": 120, "source": 500, "label_definition": 1000, "known_issues": 2000}


class DsError(ValueError):
    def __init__(self, status: int, message: Any) -> None:
        super().__init__(message if isinstance(message, str) else json.dumps(message))
        self.status, self.payload = status, message


def _j(v: str | None) -> Any:
    return json.loads(v) if v else None


# -- the tree ---------------------------------------------------------------------------------------------------------

def root_of(con, dataset_id: str) -> str:
    r = con.execute("SELECT id, root_id FROM datasets WHERE id = ?", (dataset_id,)).fetchone()
    if not r:
        raise DsError(404, f"No dataset with id '{dataset_id}'.")
    return r["root_id"] or r["id"]


def tree_rows(con, root: str) -> list[dict[str, Any]]:
    rows = con.execute("SELECT id, name, filename, source, note, created_at, rows, target, kind, project_id, parent_id, root_id, sha256,"
                       " op, created_by, state, state_at, state_by, state_scope, verified_at, verified_ok, size_bytes,"
                       " splits_sha256, version_no, team_id, storage, legacy_sha256,"
                       " COALESCE(n_cols, CAST(json_extract(profile, '$.n_cols') AS INTEGER)) AS n_cols FROM datasets WHERE id = ? OR root_id = ? ORDER BY created_at, id",
                       (root, root)).fetchall()
    out = [dict(r) for r in rows]
    for i, r in enumerate(out):
        r["v"] = i + 1
    return out


CARD_COLS = (*CARD_FIELDS, "default_version_id", "updated_at", "updated_by")
_ROOT_CARD_SQL = "SELECT id AS root_id, " + ", ".join(CARD_COLS) + " FROM dataset_roots"


def card(con, root: str) -> dict[str, Any]:
    """P01: the card lives in dataset_roots; a root without a row there (made before the backfill) falls back to
    dataset_cards, which stays written for one release."""
    r = con.execute(_ROOT_CARD_SQL + " WHERE id = ?", (root,)).fetchone()
    if r is None:
        r = con.execute("SELECT * FROM dataset_cards WHERE root_id = ?", (root,)).fetchone()
    return dict(r) if r else {"root_id": root, **{k: "" for k in CARD_FIELDS}, "default_version_id": None, "updated_at": None,
                              "updated_by": None}


def _write_card(con, root: str, fields: dict[str, Any]) -> None:
    """Write card columns to both dataset_cards and the root's dataset_roots row (created when missing)."""
    con.execute("INSERT OR IGNORE INTO dataset_cards (root_id) VALUES (?)", (root,))
    S.ensure_root(con, root)
    for k, v in fields.items():
        if k not in CARD_COLS:
            raise ValueError(k)
        con.execute(f"UPDATE dataset_cards SET {k} = ? WHERE root_id = ?", (v, root))
        con.execute(f"UPDATE dataset_roots SET {k} = ? WHERE id = ?", ("" if v is None and k in CARD_FIELDS else v, root))


def effective_default(rows: list[dict[str, Any]], c: dict[str, Any]) -> tuple[str | None, str]:
    """(version id, how): the pointer when it names an active version, else the newest active version."""
    live = [r for r in rows if not r["state"]]
    if c.get("default_version_id") and any(r["id"] == c["default_version_id"] for r in live):
        return c["default_version_id"], "set"
    return (live[-1]["id"], "latest (no default set)") if live else (None, "none: every version is archived or deleted")


def usage(con, ids: list[str]) -> dict[str, Any]:
    """Reverse lineage of these dataset ids: trained_on → runs, built_from → registered versions, champions.
    DL2 (B1): driven by lineage_edges with LEFT JOINs, so a deleted run row can't hide the version or champion built
    from it (a run whose row is gone shows as status 'deleted')."""
    if not ids:
        return {"runs": [], "versions": [], "champions": []}
    q = ",".join("?" * len(ids))
    runs = [dict(r) for r in con.execute(
        "SELECT e.dst_id AS id, e.src_id AS dataset_id, r.name, COALESCE(r.status, 'deleted') AS status, r.label,"
        " COALESCE(r.created_at, e.created_at) AS created_at, r.source, r.primary_metric, r.primary_score, r.predictor_state"
        f" FROM lineage_edges e LEFT JOIN runs r ON r.id = e.dst_id WHERE e.relation = 'trained_on' AND e.src_kind = 'dataset'"
        f" AND e.src_id IN ({q}) ORDER BY created_at DESC", ids)]
    rids = [r["id"] for r in runs]
    vers, champs = [], []
    if rids:
        qr = ",".join("?" * len(rids))
        vers = [dict(r) for r in con.execute(
            "SELECT e.dst_id AS id, e.src_id AS run_id, COALESCE(v.model_name, json_extract(e.meta, '$.model_name')) AS model_name,"
            " COALESCE(v.version, CAST(json_extract(e.meta, '$.version') AS INTEGER)) AS version, COALESCE(v.status, 'missing') AS status, v.stage,"
            " COALESCE(v.approval, 'unknown') AS approval, v.created_by, v.created_at, v.retired_at, v.retired_by"
            f" FROM lineage_edges e LEFT JOIN model_versions v ON v.id = e.dst_id WHERE e.relation = 'built_from' AND e.src_kind = 'run'"
            f" AND e.src_id IN ({qr})", rids)]
        vids = [v["id"] for v in vers] or [""]
        qv = ",".join("?" * len(vids))
        champs = [dict(r) for r in con.execute(
            f"SELECT * FROM champions WHERE (run_id IN ({qr}) OR version_id IN ({qv})) AND status IN ('approved', 'pending')"
            " ORDER BY requested_at DESC", (*rids, *vids))]
    ds_of = {r["id"]: r["dataset_id"] for r in runs}
    run_of = {v["id"]: v["run_id"] for v in vers}
    champ_vids = {c["version_id"] for c in champs}
    for v in vers:
        v["dataset_id"] = ds_of.get(v["run_id"])
        v["retired"] = bool(v.get("retired_at"))
        v["champion"] = v["id"] in champ_vids
    for c in champs:
        c["run_id"] = c.get("run_id") or run_of.get(c["version_id"])
        c["dataset_id"] = ds_of.get(c["run_id"])
    return {"runs": runs, "versions": vers, "champions": champs}


def _days_since(ts: str | None) -> float | None:
    from datetime import datetime, timezone

    try:
        t = datetime.fromisoformat(str(ts))
    except (TypeError, ValueError):
        return None
    if t.tzinfo is None:
        t = t.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - t).total_seconds() / 86400


def blockers(con, ids: list[str], what: str = "delete") -> list[str]:
    """Why these versions can't be archived (what='archive') or deleted (what='delete'), each with the way to clear it.
    DL2: archive is allowed when the only models on the data are Archived or retired and not champion; delete only
    when every model on it was retired at least the retention period ago (config.retired_data_retention_days, needs
    model-risk approval). Rejected and failed versions never block. A champion (approved or requested) and a run still
    training always block."""
    from .. import config

    u = usage(con, ids)
    days = config.retired_data_retention_days()
    out = []
    for v in u["versions"]:
        if v["approval"] == "rejected" or v["status"] == "failed":
            continue
        name = f"{v['model_name']} v{v['version']} ({v['approval']}{'' if v['stage'] in (None, 'None') else ', ' + v['stage']})"
        src = f"built from run {v['run_id']} on {v['dataset_id']}"
        if v["champion"]:
            continue  # reported once, as the champion below
        if v["retired"]:
            if what == "archive":
                continue
            age = _days_since(v["retired_at"])
            if age is not None and age >= days:
                continue
            out.append(f"{name} was retired on {str(v['retired_at'])[:10]} by {v['retired_by']}; data behind a retired model may be "
                       f"deleted {days:g} days after retirement ({max(0.0, days - (age or 0)):.0f} days to go). The period is "
                       "ENSEMBLE_RETIRED_DATA_RETENTION_DAYS (retention policy: needs model-risk approval); archive hides it now.")
            continue
        if what == "archive" and v["stage"] == "Archived":
            continue
        how = ("reject it (Registry → the version → Review) or retire it" if v["approval"] == "pending" else "retire it")
        how += (f" (Registry → the version → Retire…, POST /api/registry/versions/{v['id']}/retire; four eyes)"
                + (", or move it to Archived" if what == "archive" else ""))
        out.append(f"{name} was {src}: {how}.")
    for c in u["champions"]:
        how = (f"reject the request (POST /api/champions/{c['id']}/decide with approve=false)" if c["status"] == "pending" else
               f"approve another champion for project {c['project_id']} first (Models → Lineage → champion; POST /api/projects/"
               f"{c['project_id']}/champion), then retire this version")
        out.append(f"champion {'request ' if c['status'] == 'pending' else ''}{c['id']} ({c['status']}) of project {c['project_id']} uses "
                   f"version {c['version_id']} (run {c['run_id']} on {c['dataset_id']}): {how}.")
    out += [f"run {r['id']} is {r['status']} on {r['dataset_id']}: wait for it or cancel it (POST /api/runs/{r['id']}/cancel)."
            for r in u["runs"] if r["status"] in ("queued", "running")]
    return out


def dataset_state(rows: list[dict[str, Any]]) -> str:
    """DL2: a dataset's state from its versions: deleted when every version is, archived when every remaining one is
    (v1 alone can be archived or deleted while later versions stay)."""
    if rows and all(r["state"] == DELETED for r in rows):
        return DELETED
    live = [r for r in rows if r["state"] != DELETED]
    return ARCHIVED if live and all(r["state"] == ARCHIVED for r in live) else "active"


def _descendants(con, dataset_id: str) -> list[str]:
    rows = con.execute("WITH RECURSIVE d(id) AS (SELECT ? UNION SELECT e.dst_id FROM lineage_edges e JOIN d ON e.src_id = d.id"
                       " WHERE e.relation = 'derived_from' AND e.src_kind = 'dataset') SELECT id FROM d", (dataset_id,)).fetchall()
    return [r[0] for r in rows if r[0] != dataset_id]


_LIN_CACHE: dict[str, tuple[tuple[int, int], dict[str, Any]]] = {}
from ..lake.migrate import LINEAGE_ROW_ARRAYS as _ROW_ARRAYS  # noqa: E402 - CSV-era lineage row maps (P10)


def lineage_meta(store, dataset_id: str) -> dict[str, Any]:
    """P08: a version's lineage.json without its row maps (one int per row: 178 MB at 10M
    rows) — what history and diff show (kind, parent, renamed, edit_log, reason, checks …). The arrays are cut out of
    the bytes before parsing; the result is cached by the file's size and mtime."""
    p = store.dir(dataset_id) / "lineage.json"
    try:
        st = p.stat()
    except OSError:
        return {}
    k = (st.st_size, st.st_mtime_ns)
    hit = _LIN_CACHE.get(str(p))
    if hit and hit[0] == k:
        return hit[1]
    data = p.read_bytes()
    for key in _ROW_ARRAYS:
        i = data.find(key)
        if i < 0:
            continue
        j = i + len(key)
        while j < len(data) and data[j:j + 1] in b" \t\r\n":
            j += 1
        if data[j:j + 1] == b"[":
            e = data.find(b"]", j)
            if e > 0:
                data = data[:j] + b"null" + data[e + 1:]
    try:
        out = json.loads(data)
    except ValueError:
        out = {}
    if len(_LIN_CACHE) > 2048:
        _LIN_CACHE.clear()
    _LIN_CACHE[str(p)] = (k, out)
    return out


# -- read ---------------------------------------------------------------------------------------------------------------

def history(app, dataset_id: str) -> dict[str, Any]:
    db, store = app.state.db, app.state.datasets
    with db.connect() as con:
        root = root_of(con, dataset_id)
        rows = tree_rows(con, root)
        c = card(con, root)
        ids = [r["id"] for r in rows]
        q = ",".join("?" * len(ids))
        edges = {r["dst_id"]: {"actor": r["actor"], "meta": _j(r["meta"]) or {}, "inferred": bool(r["inferred"])} for r in con.execute(
            f"SELECT dst_id, actor, meta, inferred FROM lineage_edges WHERE relation = 'derived_from' AND dst_id IN ({q})", ids)}
        u = usage(con, ids)
        tags = {}
        for r in con.execute(f"SELECT target_id, value FROM annotations WHERE target_kind = 'dataset' AND kind = 'tag' AND deleted_at IS NULL"
                             f" AND target_id IN ({q})", ids):
            tags.setdefault(r[0], []).append(r[1])
        reqs = [dict(r) for r in con.execute("SELECT * FROM dataset_requests WHERE root_id = ? ORDER BY requested_at DESC LIMIT 20", (root,))]
        aud = [dict(r) for r in con.execute(
            f"SELECT id, ts, actor, action, comment, details FROM audit_log WHERE json_extract(details, '$.root_id') = ?"
            f" OR json_extract(details, '$.dataset_id') IN ({q}) ORDER BY id DESC LIMIT 60", (root, *ids))]
        proj = con.execute("SELECT id, name FROM projects WHERE id = ?", (rows[0]["project_id"],)).fetchone() if rows else None
    default, how = effective_default(rows, c)
    for r in rows:
        e = edges.get(r["id"]) or {}
        lin = lineage_meta(store, r["id"])  # P08: never the row maps
        r["created_by"] = r["created_by"] or e.get("actor") or ("system" if r["source"] == "sample" else None)
        r["changes"] = {k: lin.get(k) for k in ("matched_by", "matched", "changed", "added", "removed", "rows_before", "rows_after",
                                                "relabelled", "dropped", "synthetic") if lin.get(k) is not None}
        r["n_changes"] = (e.get("meta") or {}).get("n_changes")
        r["warnings"] = ((lin.get("checks") or {}).get("warnings")) or []
        r["reason"] = lin.get("reason") or None  # DL2: the reason / change ticket given when it was made
        if lin.get("kind") == "edit":  # DL3: an in-app edit: each operation and what it touched
            r["edit_ops"] = [{k: e.get(k) for k in ("i", "op", "summary", "rows", "cells", "column")} for e in lin.get("edit_log") or []]
        if lin.get("duplicate_of"):
            r["duplicate_of"] = lin["duplicate_of"]
        r["paired_with_parent"] = None if not r["parent_id"] else not e.get("inferred", True)
        r["is_default"] = r["id"] == default
        r["tags"] = sorted(tags.get(r["id"], []))
        r["used_by"] = {"runs": sum(1 for x in u["runs"] if x["dataset_id"] == r["id"]),
                        "versions": sum(1 for x in u["versions"] if x["dataset_id"] == r["id"]),
                        "champion": any(x["dataset_id"] == r["id"] and x["status"] == "approved" for x in u["champions"])}
    for a in aud:
        a["details"] = _j(a["details"])
    root_rec = rows[0] if rows else None
    return {"root_id": root, "name": root_rec["name"] if root_rec else root, "project": dict(proj) if proj else None,
            "state": (lambda st: None if st == "active" else st)(dataset_state(rows)), "card": c, "default_version_id": default, "default_how": how,
            "versions": rows, "requests": reqs, "audit": aud, "focus": dataset_id,
            "admins_configured": bool(G.admins()), "retention_days": _retention_days(), "retired_versions": [
                {k: v.get(k) for k in ("id", "model_name", "version", "retired_at", "retired_by", "dataset_id")} for v in u["versions"] if v["retired"]]}


def _retention_days() -> float:
    from .. import config

    return config.retired_data_retention_days()


def diff(app, a: str, b: str, key: list[str] | None = None, wait_s: float = 0.0) -> dict[str, Any]:
    """P08 (§5.4, R6): the two-pass DuckDB diff (lake/diff.py), cached by (a sha, b sha, key). Same root: on
    `__row_id`; `key` (a confirmed business key) for two roots or CSV vs Parquet; else a multiset row-hash diff
    (added / removed only). The DL1 response shape is kept; `tables`, `samples`, `changed_by_column` and
    `schema.dtype_changes` are new. DL3: an edit of a adds its operations (`edit`) and compares renamed columns under
    their old names."""
    from ..lake import diff as LD

    db, store = app.state.db, app.state.datasets
    recs = {}
    for x in (a, b):
        rec = db.get_dataset(x)
        if not rec:
            raise DsError(404, f"No dataset with id '{x}'.")
        if rec.get("state") == DELETED:
            raise DsError(410, f"{x} was deleted: its rows are gone (the tombstone and lineage are kept).")
        recs[x] = rec
    lb = lineage_meta(store, b)  # P08: without the row maps
    ren = (lb.get("renamed") or {}) if lb.get("kind") == "edit" and lb.get("parent") == a else {}
    tgt = recs[a]["target"]
    try:
        out = LD.compute(db, store, a, b, key=key or None, renamed=ren, target=tgt)
        out = LD.with_columns(out, wait_s=wait_s)
    except LD.DiffError as e:
        raise DsError(e.status, e.payload()) from e
    with db.connect() as con:
        same_root = root_of(con, a) == root_of(con, b)
    out.setdefault("checks", [])
    out["a"] = {"id": a, "name": recs[a]["name"], "rows": out["rows"]["a"], "target": tgt, "sha256": recs[a].get("sha256"),
                "table": out.get("a_table"), "table_sha256": out.get("a_sha256")}
    out["b"] = {"id": b, "name": recs[b]["name"], "rows": out["rows"]["b"], "target": recs[b]["target"], "sha256": recs[b].get("sha256"),
                "table": out.get("b_table"), "table_sha256": out.get("b_sha256")}
    out["same_dataset"] = same_root
    if ren:
        for c in out["columns"]:
            if c.get("column") in ren:
                c["column"] = f"{c['column']} → {ren[c['column']]}"
        for e in out["rows"]["changed_examples"]:
            for c in e["cells"]:
                if c["column"] in ren:
                    c["column"] = f"{c['column']} → {ren[c['column']]}"
    if lb.get("kind") == "edit" and lb.get("parent") == a:  # DL3: an edit of a: the operations, their cells old → new
        out["edit"] = {"by": lb.get("by"), "reason": lb.get("reason"), "ops": lb.get("edit_log") or [], "renamed": ren}
    return json.loads(json.dumps(out, default=lambda v: v.item() if hasattr(v, "item") else str(v)))


def search(app, *, q: str = "", project: str | None = None, tag: str | None = None, owner: str | None = None,
           op: str | None = None, since: str | None = None, until: str | None = None, state: str = "active") -> list[dict[str, Any]]:
    with app.state.db.connect() as con:
        rows = [dict(r) for r in con.execute(
            "SELECT d.id, d.name, d.filename, d.source, d.created_at, d.rows, d.target, d.project_id, d.parent_id, d.root_id, d.op,"
            " d.created_by, d.state, d.sha256, COALESCE(d.n_cols, CAST(json_extract(d.profile, '$.n_cols') AS INTEGER)) AS n_cols FROM datasets d ORDER BY d.created_at, d.id")]
        cards = {r["root_id"]: dict(r) for r in con.execute("SELECT * FROM dataset_cards")}
        cards.update({r["root_id"]: dict(r) for r in con.execute(_ROOT_CARD_SQL)})  # P01: dataset_roots first
        tags: dict[str, set[str]] = {}
        for r in con.execute("SELECT target_id, value FROM annotations WHERE target_kind = 'dataset' AND kind = 'tag' AND deleted_at IS NULL"):
            tags.setdefault(r[0], set()).add(r[1])
        projects = {r[0]: r[1] for r in con.execute("SELECT id, name FROM projects")}
        n_runs = {r[0]: r[1] for r in con.execute("SELECT dataset_id, COUNT(*) FROM runs GROUP BY dataset_id")}
    trees: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        trees.setdefault(r["root_id"] or r["id"], []).append(r)
    out = []
    ql = (q or "").strip().lower()
    for root, vs in trees.items():
        head = next((v for v in vs if v["id"] == root), vs[0])
        c = cards.get(root) or {}
        st = dataset_state(vs)
        # DL2: state matches any version (state=deleted finds a dataset with a deleted v1 too)
        in_state = [v for v in vs if (v["state"] or "active") == state]
        if state != "all" and not in_state:
            continue
        all_tags = sorted(set().union(*(tags.get(v["id"], set()) for v in vs)))
        live = [v for v in vs if not v["state"]]
        if project and head["project_id"] != project:
            continue
        if tag and not any(tag.strip().lower() in t.lower() for t in all_tags):
            continue
        if owner and owner.lower() not in (c.get("owner") or head["created_by"] or "").lower():
            continue
        if op and not any((v["op"] or "") == op for v in vs):
            continue
        if since and not any(v["created_at"] >= since for v in vs):
            continue
        if until and not any(v["created_at"][:len(until)] <= until for v in vs):
            continue
        if ql and not any(ql in str(x or "").lower() for x in (head["name"], head["filename"], root, c.get("description"), c.get("owner"),
                                                                  c.get("source"), *[v["id"] for v in vs], *all_tags)):
            continue
        dflt, how = effective_default(vs, c)
        last = vs[-1]
        out.append({"root_id": root, "name": head["name"], "source": head["source"], "state": st, "project_id": head["project_id"],
                    "project": projects.get(head["project_id"]), "owner": c.get("owner") or "", "created_by": head["created_by"],
                    "description": c.get("description") or "", "tags": all_tags, "n_versions": len(vs), "n_live": len(live),
                    "ops": sorted({v["op"] for v in vs if v["op"]}), "default_version_id": dflt, "default_how": how,
                    "rows": next((v["rows"] for v in vs if v["id"] == dflt), head["rows"]),
                    "n_cols": next((v["n_cols"] for v in vs if v["id"] == dflt), head["n_cols"]),
                    "target": head["target"], "created_at": head["created_at"], "last_change": last["created_at"],
                    "n_runs": sum(n_runs.get(v["id"], 0) for v in vs),
                    "versions_in_state": [] if state in ("all", "active") else [v["id"] for v in in_state]})
    return sorted(out, key=lambda r: r["last_change"], reverse=True)


# -- write ----------------------------------------------------------------------------------------------------------------

def update_card(app, dataset_id: str, fields: dict[str, Any], by: str) -> dict[str, Any]:
    with app.state.db.connect() as con:
        root = root_of(con, dataset_id)
        before = card(con, root)
        sets = {k: v for k, v in fields.items() if k in CARD_FIELDS}
        _write_card(con, root, {**sets, "updated_at": now(), "updated_by": by})
        name_before = None
        if fields.get("name"):
            name_before = con.execute("SELECT name FROM datasets WHERE id = ?", (root,)).fetchone()[0]
            con.execute("UPDATE datasets SET name = ? WHERE id = ? OR root_id = ?", (fields["name"], root, root))
            rr = con.execute("SELECT project_id FROM dataset_roots WHERE id = ?", (root,)).fetchone()
            if rr:
                con.execute("UPDATE dataset_roots SET name = ? WHERE id = ?", (S.free_root_name(con, rr[0], fields["name"], root), root))
        changed = {k: {"before": (before.get(k) or "")[:300], "after": str(v)[:300]} for k, v in sets.items() if (before.get(k) or "") != v}
        if name_before is not None and name_before != fields["name"]:
            changed["name"] = {"before": name_before, "after": fields["name"]}
        if changed:
            audit(con, by, "dataset_card_edited", {"dataset_id": root, "root_id": root, "changed": changed})
        return card(con, root)


def set_default(app, dataset_id: str, version_id: str, by: str, reason: str) -> dict[str, Any]:
    with app.state.db.connect() as con:
        root = root_of(con, dataset_id)
        rows = tree_rows(con, root)
        v = next((r for r in rows if r["id"] == version_id), None)
        if not v:
            raise DsError(400, f"{version_id} is not a version of {root}.")
        if v["state"]:
            raise DsError(409, f"v{v['v']} ({version_id}) is {v['state']}: only an active version can be the default.")
        c = card(con, root)
        cur, _ = effective_default(rows, c)
        if cur == version_id and c.get("default_version_id") == version_id:
            raise DsError(409, f"v{v['v']} is already the default.")
        cur_v = next((r for r in rows if r["id"] == cur), None)
        rollback = bool(cur_v and v["v"] < cur_v["v"])
        _write_card(con, root, {"default_version_id": version_id, "updated_at": now(), "updated_by": by})
        audit(con, by, "dataset_rolled_back" if rollback else "dataset_default_set", comment=reason,
              details={"dataset_id": version_id, "root_id": root, "from": cur, "from_v": cur_v and cur_v["v"], "to": version_id,
                       "to_v": v["v"], "data_changed": False})
    return {"root_id": root, "default_version_id": version_id, "v": v["v"], "previous": cur, "rollback": rollback,
            "note": "the pointer moved; no file was changed or copied"}


def current_default(app, dataset_id: str) -> str | None:
    with app.state.db.connect() as con:
        root = root_of(con, dataset_id)
        return effective_default(tree_rows(con, root), card(con, root))[0]


def settle_default(app, new_id: str, before: str | None, warnings: list[dict[str, Any]], by: str, make_default: str) -> dict[str, Any]:
    """DL2 (B2): a new uploaded version becomes the default only when it has no contract warnings (and the uploader did
    not say no), else on an explicit POST …/default. A version with warnings is created but not made the default; when
    no default was set, the pointer is pinned to the one that was in use, so "latest" can't pick the new version."""
    with app.state.db.connect() as con:
        root = root_of(con, new_id)
        rows = tree_rows(con, root)
        c = card(con, root)
        v = next(r for r in rows if r["id"] == new_id)
        if not warnings and make_default != "no":
            _write_card(con, root, {"default_version_id": new_id, "updated_at": now(), "updated_by": by})
            audit(con, by, "dataset_default_set", comment="new version without contract warnings",
                  details={"dataset_id": new_id, "root_id": root, "from": before, "to": new_id, "to_v": v["v"], "auto": True,
                           "data_changed": False})
            return {"is_default": True, "why": "no contract warnings: it is now the default"}
        pinned = None
        if not c.get("default_version_id") and before and before != new_id:
            _write_card(con, root, {"default_version_id": before, "updated_at": now(), "updated_by": by})
            pinned = before
        why = (f"{len(warnings)} contract warning(s): " + "; ".join(w["code"] for w in warnings[:6])) if warnings else "you asked not to"
        return {"is_default": False, "why": why, "default_stays": before, "pinned": pinned,
                "how_to_promote": f"after review: Data → v{v['v']} → Make default (POST /api/datasets/{root}/default with "
                                  f'{{"version_id": "{new_id}", "by": …, "reason": …}})'}


def _targets(con, dataset_id: str, only_version: bool = False) -> tuple[str, list[dict[str, Any]], bool]:
    """(root, the versions the action covers, whole dataset?): the root's id means every version, unless only_version
    (DL2: v1 alone, e.g. a PII purge of the first extract while later versions stay; root rows still map)."""
    root = root_of(con, dataset_id)
    rows = tree_rows(con, root)
    whole = dataset_id == root and not only_version
    return root, (rows if whole else [r for r in rows if r["id"] == dataset_id]), whole


def _run_users(con, ids: list[str]) -> list[dict[str, Any]]:
    """DL4 (m-h): succeeded runs trained on these versions whose run is not deleted and not retired (no registered version
    built from it that was retired): deleting the data makes them irreproducible."""
    u = usage(con, ids)
    retired_runs = {v["run_id"] for v in u["versions"] if v.get("retired")}
    return [r for r in u["runs"] if r["status"] == "succeeded" and r["id"] not in retired_runs]


def _guard(con, root: str, targets: list[dict[str, Any]], whole: bool, what: str, ack: dict[str, Any] | None = None) -> None:
    ids = [r["id"] for r in targets if r["state"] != DELETED]
    bad = blockers(con, ids, "archive" if what == "archive" else "delete")
    if what == "delete" and not ack:  # DL4 (m-h): a succeeded run trained on it would lose the data it was trained on
        users = _run_users(con, ids)
        if users:
            bad += [f"run {r['id']} ({r.get('name') or 'unnamed'}, succeeded) was trained on {r['dataset_id']}: deleting the data makes it "
                    "irreproducible. Delete the run, or retire the model built from it, or send "
                    '"irreproducible": {"reason": "…"} with the delete to accept that (four eyes, audited).' for r in users[:20]]
            if len(users) > 20:
                bad.append(f"… and {len(users) - 20} more succeeded runs.")
    if bad:
        raise DsError(409, {"message": f"Can't {what}: a version is in use. Each line below says how to clear it.", "blockers": bad})
    if not whole:
        rows = tree_rows(con, root)
        cur, how = effective_default(rows, card(con, root))
        if cur in ids and how == "set":
            raise DsError(409, f"Can't {what} the default version: make another version the default first (Data → the version → "
                               f"Make default; POST /api/datasets/{root}/default).")


def archive(app, dataset_id: str, by: str, archived: bool, reason: str, only_version: bool = False) -> dict[str, Any]:
    """Hide (or show again) a version, or the whole dataset when the id is its root (unless only_version). DL2 (B7): a
    whole-dataset archive marks what it hid (state_scope 'dataset'); unarchiving the dataset restores only those, so a
    version archived on its own stays archived."""
    with app.state.db.connect() as con:
        root, targets, whole = _targets(con, dataset_id, only_version)
        if any(r["state"] == DELETED for r in targets if r["id"] == dataset_id) and not whole:
            raise DsError(410, f"{dataset_id} was deleted.")
        if archived:
            _guard(con, root, targets, whole, "archive")
            ids = [r["id"] for r in targets if r["state"] is None]
        elif whole:
            ids = [r["id"] for r in targets if r["state"] == ARCHIVED and r.get("state_scope") == "dataset"]
        else:
            ids = [r["id"] for r in targets if r["state"] == ARCHIVED]
        if not ids:
            raise DsError(409, f"Nothing to {'archive' if archived else 'unarchive'}: "
                               + ("every version is already archived or deleted." if archived else "no version was hidden by "
                                  + ("the dataset's archive (versions archived on their own are unarchived one by one)." if whole else "an archive.")))
        q = ",".join("?" * len(ids))
        con.execute(f"UPDATE datasets SET state = ?, state_at = ?, state_by = ?, state_scope = ? WHERE id IN ({q})",
                    (ARCHIVED if archived else None, now(), by, ("dataset" if whole else "version") if archived else None, *ids))
        S.sync_root_state(con, root)  # P01
        audit(con, by, "dataset_archived" if archived else "dataset_unarchived", comment=reason,
              details={"dataset_id": dataset_id, "root_id": root, "whole_dataset": whole, "ids": ids})
    return {"root_id": root, "ids": ids, "state": ARCHIVED if archived else "active", "whole_dataset": whole}


def _ack(irreproducible: Any) -> dict[str, Any] | None:
    """DL4 (m-h): {"reason": "…"} (or true with no reason → refused): the person deleting accepts that runs trained on these
    versions can no longer be reproduced from their data."""
    if irreproducible in (None, False):
        return None
    reason = str((irreproducible or {}).get("reason") if isinstance(irreproducible, dict) else "").strip()
    if len(reason) < 10:
        raise DsError(400, 'irreproducible is {"reason": "…"} (at least 10 characters): why it is acceptable that the runs '
                           "trained on this data can't be reproduced after the delete.")
    return {"reason": reason[:500]}


def request_delete(app, dataset_id: str, by: str, reason: str, only_version: bool = False,
                   freeze_holdout: bool = False, irreproducible: Any = None) -> dict[str, Any]:
    """Four eyes: a pending request another person approves. An admin (ENSEMBLE_ADMINS) deletes at once. The guard
    runs now and again at approval. DL2: only_version deletes v1 alone (the root id otherwise means every version);
    freeze_holdout keeps a sample of the holdout rows beside the tombstone for re-validating the retired models."""
    with app.state.db.connect() as con:
        root, targets, whole = _targets(con, dataset_id, only_version)
        if all(r["state"] == DELETED for r in targets):
            raise DsError(410, f"{dataset_id} is already deleted.")
        ack = _ack(irreproducible)
        _guard(con, root, targets, whole, "delete", ack=ack)
        if con.execute("SELECT 1 FROM dataset_requests WHERE dataset_id = ? AND status = 'pending'", (dataset_id,)).fetchone():
            raise DsError(409, f"A delete of {dataset_id} is already waiting for approval.")
        rid = "dsr_" + secrets.token_hex(4)
        # §4.4 (DLA4): runs_made_irreproducible is computed for every request (an empty list when no run loses its data)
        opts = {"only_version": bool(only_version and not whole and dataset_id == root), "freeze_holdout": bool(freeze_holdout),
                "runs_made_irreproducible": [r["id"] for r in _run_users(con, [t["id"] for t in targets if t["state"] != DELETED])],
                **({"irreproducible": ack} if ack else {})}
        con.execute("INSERT INTO dataset_requests (id, dataset_id, root_id, action, status, requested_by, requested_at, reason, options)"
                    " VALUES (?, ?, ?, 'delete', 'pending', ?, ?, ?, ?)", (rid, dataset_id, root, by, now(), reason, json.dumps(opts)))
        audit(con, by, "dataset_delete_requested", comment=reason,
              details={"dataset_id": dataset_id, "root_id": root, "request_id": rid, "whole_dataset": whole, **opts,
                       "ids": [r["id"] for r in targets], "needs": "approval by someone other than " + by + " (or an admin)"})
    if G.is_admin(by) and G.allow_admin_self_approve():  # DL3 (m1): only when the policy allows it, and said so in the audit
        return decide_delete(app, rid, True, by, G.SELF_APPROVED, admin_self=True)
    return {**get_request(app, rid), **({"note": "you are an admin, but a delete still needs a second person "
                                                 "(ENSEMBLE_ALLOW_ADMIN_SELF_APPROVE is off)"} if G.is_admin(by) else {})}


def get_request(app, rid: str) -> dict[str, Any]:
    with app.state.db.connect() as con:
        r = con.execute("SELECT * FROM dataset_requests WHERE id = ?", (rid,)).fetchone()
    if not r:
        raise DsError(404, f"No dataset request '{rid}'.")
    out = dict(r)
    if out.get("action") == "delete":  # §4.4: the runs this delete makes irreproducible, in the response itself
        out["runs_made_irreproducible"] = (_j(out.get("options")) or {}).get("runs_made_irreproducible") or []
    return out


def decide_delete(app, rid: str, approve: bool, by: str, comment: str, admin_self: bool = False, withdraw: bool = False) -> dict[str, Any]:
    req = get_request(app, rid)
    if req["status"] != "pending":
        raise DsError(409, f"This request is {req['status']}.")
    opts = _j(req.get("options")) or {}
    from ..authz.policy import four_eyes  # C §1.5 (P21): every decide path goes through one check

    mine = four_eyes("data.delete_decide", req, by) is not None
    if withdraw and not (mine or G.is_admin(by)):  # DL3 (m6): only the requester (identity key) or an admin withdraws
        raise DsError(403, f"Only {req['requested_by']} (who asked) or an admin may withdraw this request; {by} may reject it "
                           "as a reviewer instead (approve: false, with a comment).")
    if withdraw or (not approve and mine):  # DL2 (B8): the requester withdraws their own request
        with app.state.db.connect() as con:
            con.execute("UPDATE dataset_requests SET status = 'withdrawn', decided_by = ?, decided_at = ?, comment = ? WHERE id = ?",
                        (by, now(), comment, rid))
            audit(con, by, "dataset_delete_withdrawn", comment=comment,
                  details={"dataset_id": req["dataset_id"], "root_id": req["root_id"], "request_id": rid})
        return get_request(app, rid)
    self_admin = bool(mine and approve and G.is_admin(by) and G.allow_admin_self_approve())
    if mine and approve and not self_admin:
        raise DsError(403, f"Four eyes: {req['requested_by']} asked for this delete, so someone else must decide, admins included "
                           "(ENSEMBLE_ALLOW_ADMIN_SELF_APPROVE is off; names are compared on letters and digits only; names, not "
                           "authenticated identities).")
    if self_admin and G.SELF_APPROVED not in (comment or ""):
        comment = G.SELF_APPROVED + (f": {comment}" if comment else "")
    store = app.state.datasets
    with app.state.db.connect() as con:
        root, targets, whole = _targets(con, req["dataset_id"], bool(opts.get("only_version")))
        if approve:
            _guard(con, root, targets, whole, "delete", ack=opts.get("irreproducible"))  # re-checked: something may use it now
        con.execute("UPDATE dataset_requests SET status = ?, decided_by = ?, decided_at = ?, comment = ? WHERE id = ?",
                    ("approved" if approve else "rejected", by, now(), comment, rid))
        if not approve:
            audit(con, by, "dataset_delete_rejected", comment=comment,
                  details={"dataset_id": req["dataset_id"], "root_id": root, "request_id": rid, "requested_by": req["requested_by"]})
            return dict(con.execute("SELECT * FROM dataset_requests WHERE id = ?", (rid,)).fetchone())  # DL3: as committed
        gone = []
        for r in targets:
            if r["state"] == DELETED:
                continue
            d = store.dir(r["id"])
            frozen = _freeze_holdout(store, r) if opts.get("freeze_holdout") else None
            tomb = {"dataset_id": r["id"], "root_id": root, "parent_id": r["parent_id"], "name": r["name"], "v": r["v"], "op": r["op"],
                    "rows": r["rows"], "n_cols": r["n_cols"], "sha256": r["sha256"], "created_at": r["created_at"],
                    "created_by": r["created_by"], "deleted_at": now(), "requested_by": req["requested_by"], "approved_by": by,
                    "reason": req["reason"], "request_id": rid,
                    "kept": ["lineage.json", "this tombstone", "the datasets row", "every lineage edge", "the audit log",
                             *(["holdout_sample.csv (frozen for re-validation)"] if frozen else [])],
                    **({"holdout_sample": frozen} if frozen else {})}
            if d.is_dir():
                for p in d.iterdir():
                    if p.name in ("lineage.json", "holdout_sample.csv"):
                        continue
                    shutil.rmtree(p) if p.is_dir() else p.unlink()
                (d / "TOMBSTONE.json").write_text(json.dumps(tomb, indent=1))
            con.execute("UPDATE datasets SET state = ?, state_at = ?, state_by = ? WHERE id = ?", (DELETED, now(), by, r["id"]))
            gone.append({"id": r["id"], "v": r["v"], "sha256": r["sha256"], **({"holdout_sample": frozen} if frozen else {})})
        S.sync_root_state(con, root)  # P01
        audit(con, by, "dataset_deleted", comment=comment or req["reason"],
              details={"dataset_id": req["dataset_id"], "root_id": root, "request_id": rid, "requested_by": req["requested_by"],
                       "approved_by": by, "admin": G.is_admin(by), "self_approved_by_admin": self_admin, "whole_dataset": whole,
                       "deleted": gone, **({k: opts[k] for k in ("irreproducible", "runs_made_irreproducible") if k in opts})})
    return {**get_request(app, rid), "deleted": gone}


HOLDOUT_SAMPLE_ROWS = 2000


def _freeze_holdout(store, r: dict[str, Any]) -> dict[str, Any] | None:
    """DL2: up to HOLDOUT_SAMPLE_ROWS holdout rows of a version about to be deleted, kept as holdout_sample.csv (with
    their row positions) so a retired model can still be re-validated. Optional: a PII purge leaves it off."""
    from .schema import file_sha256

    try:
        df = frames.read_version(r["id"])
        _, sp, _ = store.ensure_splits(r["id"], r["target"] if r["target"] in df.columns else None)
        hold = [int(i) for i in sp.get("holdout") or [] if int(i) < len(df)]
    except Exception:  # noqa: BLE001 - no split to freeze: the delete goes ahead without a sample
        return None
    if not hold:
        return None
    rng = np.random.default_rng(0)
    pick = sorted(rng.choice(hold, size=min(HOLDOUT_SAMPLE_ROWS, len(hold)), replace=False).tolist())
    out = df.iloc[pick].copy()
    out.insert(0, "_row", pick)
    p = store.dir(r["id"]) / "holdout_sample.csv"
    out.to_csv(p, index=False)
    return {"file": "holdout_sample.csv", "rows": len(pick), "of_holdout": len(hold), "sha256": file_sha256(p)}


def verify(app, dataset_id: str, by: str, whole: bool = False) -> dict[str, Any]:
    store = app.state.datasets
    with app.state.db.connect() as con:
        root = root_of(con, dataset_id)
        rows = [r for r in tree_rows(con, root) if (whole or r["id"] == dataset_id)]
        out = []
        for r in rows:
            if r["state"] == DELETED:
                out.append({"id": r["id"], "v": r["v"], "ok": None, "status": "deleted (tombstone)",
                            "stored": r["legacy_sha256"] if r.get("storage") == "parquet" and r.get("legacy_sha256") else r["sha256"]})
                continue
            lake_info = None
            if r.get("storage") == "parquet":  # P05: a lake version is re-hashed part by part (lake/verify.py), not data.csv
                from ..lake import migrate as MG
                from ..lake import verify as LV

                lv = LV.verify_version(app.state.db, app.state.lake, r["id"])
                data_ok = bool(lv["data_ok"])
                now_sha = r["sha256"] if data_ok else None  # the version hash is recomputed from its parts; a part that differs breaks it
                status = "ok" if data_ok else "MISMATCH: " + "; ".join(p for p in lv.get("problems") or []
                                                                        if not p.startswith("split "))[:300]
                lake_info = {"kind": "parquet", "data_sha256": r["sha256"], "manifest_sha256": lv.get("manifest_sha256"),
                             "data_ok": data_ok, "problems": lv.get("problems") or [],
                             "files": [{k: f[k] for k in ("table", "part", "status")} for f in lv.get("files") or []],
                             "splits": [{k: x.get(k) for k in ("id", "split_key", "status", "failed")} for x in lv["splits"]]}
                mig = MG.legacy_verify(app.state.db, app.state.lake, r["id"], lv) if r.get("legacy_sha256") else None
                if mig is not None:  # P10: a migrated version answers as it did while CSV-era: the data.csv hash, now its blob's
                    stored_legacy = r["legacy_sha256"]
                    lake_info["legacy"] = mig["legacy"]
                    now_sha = mig["legacy"]["now"]
                    data_ok = data_ok and now_sha == stored_legacy
                    if not data_ok:
                        status = ("legacy CSV blob missing" if not now_sha else "MISMATCH: " + "; ".join(
                            [p for p in lv.get("problems") or [] if not p.startswith("split ")] +
                            ([] if now_sha == stored_legacy else ["the legacy CSV blob does not re-hash"]))[:300])
            else:
                now_sha = file_sha256(frames.version_file(r["id"]))
                data_ok = bool(now_sha and r["sha256"] and now_sha == r["sha256"])
                status = "ok" if data_ok else ("file missing" if not now_sha else "no stored hash" if not r["sha256"] else "MISMATCH")
            if r.get("storage") == "parquet" and lake_info and lake_info.get("legacy") is not None:
                splits = mig["splits"]  # P10: the DL4 files, each checked through its migrated split and its legacy blob
            elif r.get("storage") == "parquet":  # P09: the five checks per Parquet split
                splits = [{"file": f"{x['split_key']} ({x['id']})", "status": x["status"], "stored": None, "now": None,
                           "failed": x["failed"], "checks": x["checks"]} for x in lv["splits"]]
            else:
                splits = store.check_splits(r["id"])  # DL4 (M-d): CSV-era versions keep the split JSON hashes until P10
            bad_splits = [x for x in splits if x["status"] != "ok"]
            ok = data_ok and not bad_splits
            if data_ok and bad_splits:
                status = "SPLIT MISMATCH"
            con.execute("UPDATE datasets SET verified_at = ?, verified_ok = ? WHERE id = ?", (now(), int(ok), r["id"]))
            con.commit()  # P10: release the write lock before the next version's lake verify writes on its own connection
            remedy = []
            if status.startswith("MISMATCH") and r.get("storage") == "parquet":
                remedy.append(f"A file of v{r['v']} in the lake no longer has the bytes it was committed with (GET /api/datasets/"
                              f"{r['id']}/verify names it). Restore the blob from a backup; nothing reads it until then.")
            elif status == "MISMATCH":
                remedy.append(f"The file was changed outside the app and is not trusted. Create a new version from it "
                              f"(POST /api/datasets/{r['id']}/adopt-disk-file with by and reason), and restore v{r['v']}'s "
                              "file from an export or backup.")
            if bad_splits and r.get("storage") == "parquet":
                remedy.append(f"{', '.join(x['file'] for x in bad_splits)} (which rows are held out, and the folds) fails "
                              + "; ".join(f"check {c['n']} {c['check']}: {c['detail']}" for x in bad_splits for c in x.get("checks") or []
                                          if not c["ok"])[:600]
                              + ". POST /api/datasets/" + r["id"] + "/restore-splits (with by and reason) offers the split the earliest run "
                              "used, from that run's pinned blob.")
            elif bad_splits:
                remedy.append(f"{', '.join(x['file'] for x in bad_splits)} (which rows are held out, and the folds) changed outside the "
                              "app. Restore the split as it was drawn (POST /api/datasets/" + r["id"] + "/restore-splits with by and "
                              "reason: written back from the copy recorded when it was drawn, hash-checked).")
            out.append({"id": r["id"], "v": r["v"], "ok": ok, "status": status,
                        "stored": r["legacy_sha256"] if lake_info and lake_info.get("legacy") is not None else r["sha256"], "now": now_sha,
                        "data_ok": data_ok, "splits_sha256": r.get("splits_sha256"),
                        "splits": [{k: x.get(k) for k in ("file", "status", "stored", "now", "failed", "checks") if k in x}
                                   for x in splits],
                        **({"remedy": " ".join(remedy)} if remedy else {}),
                        **({"storage": lake_info} if lake_info else {})})
        bad = [x for x in out if x["ok"] is False]
        audit(con, by, "dataset_integrity_failed" if bad else "dataset_verified",
              details={"dataset_id": dataset_id, "root_id": root, "checked": len(out), "failed": [x["id"] for x in bad],
                       "results": [{**{k: x.get(k) for k in ("id", "status", "stored", "now")},
                                    "splits_failed": [y["file"] for y in x.get("splits") or [] if y["status"] != "ok"]} for x in out]})
    return {"root_id": root, "results": out, "ok": not bad}


def verify_all(app, by: str) -> dict[str, Any]:
    """DL2: re-hash every version of every dataset that is not deleted (the admin "verify all" and the optional timer)."""
    with app.state.db.connect() as con:
        roots = [r[0] for r in con.execute("SELECT DISTINCT COALESCE(root_id, id) FROM datasets WHERE state IS DISTINCT FROM 'deleted'")]
    res = [verify(app, r, by, whole=True) for r in roots]
    failed = [{"root_id": x["root_id"], **y} for x in res for y in x["results"] if y["ok"] is False]
    at = now()
    from ..lake.migrate import record_verify_all

    record_verify_all(app.state.db, not failed, at)  # P10: the migrated folders are deleted after two passes (§7 row 1)
    return {"datasets": len(roots), "checked": sum(len(x["results"]) for x in res), "failed": failed, "ok": not failed, "at": at}


def integrity_gate(app, dataset_id: str, override: Any, by: str | None) -> dict[str, Any]:
    """DL2 (B3): no training on a version whose last verify was a MISMATCH, unless an admin overrides with a reason
    (audited). Returns what the run records; raises DsError 409 / 403."""
    rec = app.state.db.get_dataset(dataset_id) or {}
    notes = []
    if rec.get("state") == ARCHIVED:
        notes.append(f"{dataset_id} is archived (hidden from pickers by {rec.get('state_by')} on {str(rec.get('state_at'))[:10]}); "
                     "training on it is allowed and recorded.")
    reason = str(override or "").strip()
    if rec.get("verified_ok") != 0:
        out = {"integrity": "ok" if rec.get("verified_ok") == 1 else "not verified", "notes": notes,
               "checked_at_fit": "the worker hashes the bytes it reads at fit start and refuses the fit if they differ"}
        if reason:  # DL3 (M1): an admin may arm an override for the fit-time check (audited only if it is used)
            if not G.is_admin(by):
                raise DsError(403, f"Only an admin (ENSEMBLE_ADMINS) may override the integrity check; {by or 'nobody'} is not one.")
            if len(reason) < 10:
                raise DsError(400, "An override needs a reason of at least 10 characters (it goes in the audit log).")
            out["override_armed"] = {"by": by, "reason": reason[:1000]}
        return out
    msg = (f"{dataset_id}'s last integrity check ({str(rec.get('verified_at'))[:19]}) was a MISMATCH: data.csv or a split file "
           "(which rows are held out) no longer has the sha256 stored when it was made. Verify says which, and how to restore it "
           "(Data → the version → Verify hash; a split file: Restore the split as drawn), or an admin "
           'sends {"integrity_override_reason": "…", "by": "<admin>"} to train on it anyway (audited).')
    if not reason:
        raise DsError(409, msg)
    if not G.is_admin(by):
        raise DsError(403, f"Only an admin (ENSEMBLE_ADMINS) may train on a version whose hash does not match; {by or 'nobody'} is not one.")
    if len(reason) < 10:
        raise DsError(400, "An override needs a reason of at least 10 characters (it goes in the audit log).")
    with app.state.db.connect() as con:
        audit(con, by, "training_integrity_override", comment=reason[:1000],
              details={"dataset_id": dataset_id, "root_id": rec.get("root_id") or dataset_id, "verified_at": rec.get("verified_at")})
    return {"integrity": "MISMATCH, overridden", "override_by": by, "override_reason": reason[:1000], "notes": notes,
            "override_armed": {"by": by, "reason": reason[:1000]}}


def export_manifest(app, dataset_id: str, by: str) -> dict[str, Any]:
    """What goes in the zip next to data.csv: the chain up to the root, the stored and current hash."""
    with app.state.db.connect() as con:
        root = root_of(con, dataset_id)
        rows = {r["id"]: r for r in tree_rows(con, root)}
        r = rows[dataset_id]
        if r["state"] == DELETED:
            raise DsError(410, f"{dataset_id} was deleted: only its tombstone and lineage are kept.")
        chain, x = [], r
        while x:
            chain.append({k: x.get(k) for k in ("id", "v", "op", "created_by", "created_at", "rows", "n_cols", "sha256", "parent_id")})
            x = rows.get(x["parent_id"]) if x.get("parent_id") else None
        sha_now = file_sha256(frames.version_file(dataset_id))
        audit(con, by, "dataset_exported", details={"dataset_id": dataset_id, "root_id": root, "v": r["v"], "sha256": sha_now,
                                                    "integrity_ok": sha_now == r["sha256"]})
    return {"dataset_id": dataset_id, "name": r["name"], "v": r["v"], "root_id": root, "target": r["target"], "rows": r["rows"],
            "sha256_stored": r["sha256"], "sha256_file": sha_now, "integrity_ok": sha_now == r["sha256"],
            "lineage_chain": chain, "exported_at": now(), "exported_by": by,
            "how_to_check": "sha256sum data.csv must equal sha256_stored"}


def match_upload(app, filename: str, content: bytes) -> dict[str, Any]:
    """DL4 (#11): the live dataset this file most looks like (same or similar columns, its target present, a similar
    name), or None. Reads the file's header and a few rows; writes nothing."""
    import difflib
    import re as _re

    from ..datasets import read_upload

    df, _, stem = read_upload(filename, content)
    norm = lambda c: _re.sub(r"[^a-z0-9]", "", str(c).lower())  # noqa: E731
    cols = {norm(c): str(c) for c in df.columns}

    def base(n: str) -> str:  # "loans_2026-11 v2.csv" and "Loans" compare as "loans"
        n = _re.sub(r"(?i)\.(csv|tsv|txt)$", "", n or "")
        n = _re.sub(r"(?i)([_\- ]?(v|version)\s?\d+|[_\- ]?\d{4}([-_]?\d{2}){0,2}|[_\- ]?(copy|new|final|latest|updated))+$", "", n)
        return norm(n)

    with app.state.db.connect() as con:
        roots = [r[0] for r in con.execute("SELECT DISTINCT COALESCE(root_id, id) FROM datasets WHERE state IS NULL")]
        cands = []
        for root in roots:
            rows = tree_rows(con, root)
            live = [r for r in rows if r["state"] is None]
            if not live:
                continue
            cur, _ = effective_default(rows, card(con, root))
            r = next((x for x in live if x["id"] == cur), live[-1])
            try:
                pcols = frames.column_names(frames.table(r["id"]))
            except Exception:  # noqa: BLE001 - a version with no readable file is not a candidate
                continue
            pn = {norm(c): c for c in pcols}
            inter = set(cols) & set(pn)
            jac = len(inter) / max(1, len(set(cols) | set(pn)))
            tgt = r["target"]
            same_target = bool(tgt) and norm(tgt) in cols
            name_sim = difflib.SequenceMatcher(None, base(stem), base(r["name"])).ratio() if base(stem) and base(r["name"]) else 0.0
            ok = (jac >= 0.6 and (same_target or jac >= 0.85)) or (name_sim >= 0.8 and jac >= 0.4)
            if not ok:
                continue
            reasons = []
            if jac == 1:
                reasons.append(f"the same {len(pn)} columns")
            else:
                reasons.append(f"{len(inter)} of {len(pn)} columns in common")
            if same_target:
                reasons.append(f"its target '{tgt}' is in the file")
            if name_sim >= 0.8:
                reasons.append(f"a similar name ('{stem}' ~ '{r['name']}')")
            cands.append({"root_id": root, "name": r["name"], "version_id": r["id"], "v": r["v"], "next_v": len(rows) + 1,
                          "target": tgt, "rows": r["rows"], "score": round(0.6 * jac + 0.25 * same_target + 0.15 * name_sim, 3),
                          "columns_in_common": len(inter), "columns_added": sorted(cols[c] for c in set(cols) - set(pn)),
                          "columns_removed": sorted(pn[c] for c in set(pn) - set(cols)), "same_target": same_target,
                          "name_similarity": round(name_sim, 2), "sample": r["source"] == "sample", "reasons": reasons})
    # an upload of the user's own data matches their datasets before a built-in sample with the same columns
    cands.sort(key=lambda c: (c["score"] - (0.05 if c["sample"] else 0)), reverse=True)
    return {"filename": filename, "rows": int(len(df)), "columns": [str(c) for c in df.columns],
            "match": cands[0] if cands else None, "others": cands[1:4],
            "note": "nothing was written; send the file to POST /api/datasets/{version_id}/versions to add it as the next version, "
                    "or to POST /api/datasets to create a new dataset"}

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

from . import governance as G
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
                       " op, created_by, state, state_at, state_by, verified_at, verified_ok, size_bytes,"
                       " json_extract(profile, '$.n_cols') AS n_cols FROM datasets WHERE id = ? OR root_id = ? ORDER BY created_at, rowid",
                       (root, root)).fetchall()
    out = [dict(r) for r in rows]
    for i, r in enumerate(out):
        r["v"] = i + 1
    return out


def card(con, root: str) -> dict[str, Any]:
    r = con.execute("SELECT * FROM dataset_cards WHERE root_id = ?", (root,)).fetchone()
    return dict(r) if r else {"root_id": root, **{k: "" for k in CARD_FIELDS}, "default_version_id": None, "updated_at": None,
                              "updated_by": None}


def effective_default(rows: list[dict[str, Any]], c: dict[str, Any]) -> tuple[str | None, str]:
    """(version id, how): the pointer when it names an active version, else the newest active version."""
    live = [r for r in rows if not r["state"]]
    if c.get("default_version_id") and any(r["id"] == c["default_version_id"] for r in live):
        return c["default_version_id"], "set"
    return (live[-1]["id"], "latest (no default set)") if live else (None, "none: every version is archived or deleted")


def usage(con, ids: list[str]) -> dict[str, Any]:
    """Reverse lineage of these dataset ids: trained_on → runs, built_from → registered versions, champions."""
    if not ids:
        return {"runs": [], "versions": [], "champions": []}
    q = ",".join("?" * len(ids))
    runs = [dict(r) for r in con.execute(
        "SELECT r.id, r.name, r.status, r.dataset_id, r.label, r.created_at, r.source, r.primary_metric, r.primary_score, r.predictor_state"
        f" FROM lineage_edges e JOIN runs r ON r.id = e.dst_id WHERE e.relation = 'trained_on' AND e.src_kind = 'dataset' AND e.src_id IN ({q})"
        " ORDER BY r.created_at DESC", ids)]
    rids = [r["id"] for r in runs]
    vers, champs = [], []
    if rids:
        qr = ",".join("?" * len(rids))
        vers = [dict(r) for r in con.execute(
            "SELECT v.id, v.model_name, v.version, v.run_id, v.status, v.stage, v.approval, v.created_by, v.created_at"
            f" FROM lineage_edges e JOIN model_versions v ON v.id = e.dst_id WHERE e.relation = 'built_from' AND e.src_id IN ({qr})", rids)]
        champs = [dict(r) for r in con.execute(f"SELECT * FROM champions WHERE run_id IN ({qr}) AND status IN ('approved', 'pending')"
                                               " ORDER BY requested_at DESC", rids)]
    ds_of = {r["id"]: r["dataset_id"] for r in runs}
    for v in vers:
        v["dataset_id"] = ds_of.get(v["run_id"])
    for c in champs:
        c["dataset_id"] = ds_of.get(c["run_id"])
    return {"runs": runs, "versions": vers, "champions": champs}


def blockers(con, ids: list[str]) -> list[str]:
    """Why these versions can't be archived or deleted: a registered version that isn't rejected (or failed), a champion
    (approved or requested), a run still training."""
    u = usage(con, ids)
    out = [f"{v['model_name']} v{v['version']} ({v['approval']}{'' if v['stage'] in (None, 'None') else ', ' + v['stage']}) was built from run {v['run_id']} on {v['dataset_id']}"
           for v in u["versions"] if v["approval"] != "rejected" and v["status"] != "failed"]
    out += [f"champion {'request ' if c['status'] == 'pending' else ''}{c['id']} ({c['status']}) of project {c['project_id']} uses run "
            f"{c['run_id']} on {c['dataset_id']}" for c in u["champions"]]
    out += [f"run {r['id']} is {r['status']} on {r['dataset_id']}" for r in u["runs"] if r["status"] in ("queued", "running")]
    return out


def _descendants(con, dataset_id: str) -> list[str]:
    rows = con.execute("WITH RECURSIVE d(id) AS (SELECT ? UNION SELECT e.dst_id FROM lineage_edges e JOIN d ON e.src_id = d.id"
                       " WHERE e.relation = 'derived_from' AND e.src_kind = 'dataset') SELECT id FROM d", (dataset_id,)).fetchall()
    return [r[0] for r in rows if r[0] != dataset_id]


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
        lin = store.lineage(r["id"]) or {}
        r["created_by"] = r["created_by"] or e.get("actor") or ("system" if r["source"] == "sample" else None)
        r["changes"] = {k: lin.get(k) for k in ("matched_by", "matched", "changed", "added", "removed", "rows_before", "rows_after",
                                                "relabelled", "dropped", "synthetic") if lin.get(k) is not None}
        r["n_changes"] = (e.get("meta") or {}).get("n_changes")
        r["warnings"] = ((lin.get("checks") or {}).get("warnings")) or []
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
            "state": root_rec["state"] if root_rec else None, "card": c, "default_version_id": default, "default_how": how,
            "versions": rows, "requests": reqs, "audit": aud, "focus": dataset_id,
            "admins_configured": bool(G.admins())}


def _pairs(app, a: str, b: str) -> tuple[tuple[np.ndarray, np.ndarray] | None, str, str | None]:
    """The same original rows in a and b: b's row map onto its parent a (or the reverse), else shared root rows, else None."""
    from .compare import root_info

    store = app.state.datasets
    la, lb = store.lineage(a) or {}, store.lineage(b) or {}
    for x, y, lin, flip in ((a, b, lb, False), (b, a, la, True)):
        pr = lin.get("parent_rows")
        if lin.get("parent") == x and pr:
            pr = np.asarray(pr, dtype=int)
            iy = np.flatnonzero(pr >= 0)
            ix = pr[iy]
            key = lin.get("row_key")
            how = f"{y}'s row map onto its parent ({lin.get('matched_by') or 'upload'})"
            return ((iy, ix) if flip else (ix, iy)), how, key
    ra, ma, _ = root_info(app, a)
    rb, mb, _ = root_info(app, b)
    if ra == rb and ma is not None and mb is not None:
        pos = {int(r): i for i, r in enumerate(ma) if r >= 0}
        ia, ib = [], []
        for j, r in enumerate(mb):
            i = pos.get(int(r)) if r >= 0 else None
            if i is not None:
                ia.append(i)
                ib.append(j)
        return (np.asarray(ia, dtype=int), np.asarray(ib, dtype=int)), f"the same original rows of {ra}", None
    return None, "exact row hash", None


def diff(app, a: str, b: str) -> dict[str, Any]:
    from .. import dataset_versions as DV

    db, store = app.state.db, app.state.datasets
    recs = {}
    for x in (a, b):
        rec = db.get_dataset(x)
        if not rec:
            raise DsError(404, f"No dataset with id '{x}'.")
        if rec.get("state") == DELETED:
            raise DsError(410, f"{x} was deleted: its rows are gone (the tombstone and lineage are kept).")
        recs[x] = rec
    fa, fb = store.load_frame(a), store.load_frame(b)
    pairs, how, key = _pairs(app, a, b)
    out = DV.diff(fa, fb, pairs, how, key)
    with db.connect() as con:
        same_root = root_of(con, a) == root_of(con, b)
    tgt = recs[a]["target"]
    out["a"] = {"id": a, "name": recs[a]["name"], "rows": len(fa), "target": tgt, "sha256": recs[a].get("sha256")}
    out["b"] = {"id": b, "name": recs[b]["name"], "rows": len(fb), "target": recs[b]["target"], "sha256": recs[b].get("sha256")}
    out["same_dataset"] = same_root
    out["checks"] = DV.contract_checks(fa, fb, tgt)["warnings"]
    return json.loads(json.dumps(out, default=lambda v: v.item() if hasattr(v, "item") else str(v)))


def search(app, *, q: str = "", project: str | None = None, tag: str | None = None, owner: str | None = None,
           op: str | None = None, since: str | None = None, until: str | None = None, state: str = "active") -> list[dict[str, Any]]:
    with app.state.db.connect() as con:
        rows = [dict(r) for r in con.execute(
            "SELECT d.id, d.name, d.filename, d.source, d.created_at, d.rows, d.target, d.project_id, d.parent_id, d.root_id, d.op,"
            " d.created_by, d.state, d.sha256, json_extract(d.profile, '$.n_cols') AS n_cols FROM datasets d ORDER BY d.created_at, d.rowid")]
        cards = {r["root_id"]: dict(r) for r in con.execute("SELECT * FROM dataset_cards")}
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
        st = head["state"] or "active"
        if state != "all" and st != state:
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
                    "n_runs": sum(n_runs.get(v["id"], 0) for v in vs)})
    return sorted(out, key=lambda r: r["last_change"], reverse=True)


# -- write ----------------------------------------------------------------------------------------------------------------

def update_card(app, dataset_id: str, fields: dict[str, Any], by: str) -> dict[str, Any]:
    with app.state.db.connect() as con:
        root = root_of(con, dataset_id)
        before = card(con, root)
        con.execute("INSERT OR IGNORE INTO dataset_cards (root_id) VALUES (?)", (root,))
        sets = {k: v for k, v in fields.items() if k in CARD_FIELDS}
        for k, v in sets.items():
            con.execute(f"UPDATE dataset_cards SET {k} = ? WHERE root_id = ?", (v, root))
        con.execute("UPDATE dataset_cards SET updated_at = ?, updated_by = ? WHERE root_id = ?", (now(), by, root))
        name_before = None
        if fields.get("name"):
            name_before = con.execute("SELECT name FROM datasets WHERE id = ?", (root,)).fetchone()[0]
            con.execute("UPDATE datasets SET name = ? WHERE id = ? OR root_id = ?", (fields["name"], root, root))
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
        con.execute("INSERT OR IGNORE INTO dataset_cards (root_id) VALUES (?)", (root,))
        con.execute("UPDATE dataset_cards SET default_version_id = ?, updated_at = ?, updated_by = ? WHERE root_id = ?",
                    (version_id, now(), by, root))
        audit(con, by, "dataset_rolled_back" if rollback else "dataset_default_set", comment=reason,
              details={"dataset_id": version_id, "root_id": root, "from": cur, "from_v": cur_v and cur_v["v"], "to": version_id,
                       "to_v": v["v"], "data_changed": False})
    return {"root_id": root, "default_version_id": version_id, "v": v["v"], "previous": cur, "rollback": rollback,
            "note": "the pointer moved; no file was changed or copied"}


def _targets(con, dataset_id: str) -> tuple[str, list[dict[str, Any]], bool]:
    """(root, the versions the action covers, whole dataset?): the root's id means every version."""
    root = root_of(con, dataset_id)
    rows = tree_rows(con, root)
    whole = dataset_id == root
    return root, (rows if whole else [r for r in rows if r["id"] == dataset_id]), whole


def _guard(con, root: str, targets: list[dict[str, Any]], whole: bool, what: str) -> None:
    ids = [r["id"] for r in targets if r["state"] != DELETED]
    bad = blockers(con, ids)
    if bad:
        raise DsError(409, {"message": f"Can't {what}: a version is in use. Reject or retire these first.", "blockers": bad})
    if not whole:
        rows = tree_rows(con, root)
        cur, how = effective_default(rows, card(con, root))
        if cur in ids and how == "set":
            raise DsError(409, f"Can't {what} the default version: set another default first.")


def archive(app, dataset_id: str, by: str, archived: bool, reason: str) -> dict[str, Any]:
    with app.state.db.connect() as con:
        root, targets, whole = _targets(con, dataset_id)
        if any(r["state"] == DELETED for r in targets if r["id"] == dataset_id):
            raise DsError(410, f"{dataset_id} was deleted.")
        if archived:
            _guard(con, root, targets, whole, "archive")
        ids = [r["id"] for r in targets if r["state"] != DELETED]
        q = ",".join("?" * len(ids))
        con.execute(f"UPDATE datasets SET state = ?, state_at = ?, state_by = ? WHERE id IN ({q})",
                    (ARCHIVED if archived else None, now(), by, *ids))
        audit(con, by, "dataset_archived" if archived else "dataset_unarchived", comment=reason,
              details={"dataset_id": dataset_id, "root_id": root, "whole_dataset": whole, "ids": ids})
    return {"root_id": root, "ids": ids, "state": ARCHIVED if archived else "active", "whole_dataset": whole}


def request_delete(app, dataset_id: str, by: str, reason: str) -> dict[str, Any]:
    """Four eyes: a pending request another person approves. An admin (ENSEMBLE_ADMINS) deletes at once. The guard
    runs now and again at approval."""
    with app.state.db.connect() as con:
        root, targets, whole = _targets(con, dataset_id)
        if all(r["state"] == DELETED for r in targets):
            raise DsError(410, f"{dataset_id} is already deleted.")
        _guard(con, root, targets, whole, "delete")
        if con.execute("SELECT 1 FROM dataset_requests WHERE dataset_id = ? AND status = 'pending'", (dataset_id,)).fetchone():
            raise DsError(409, f"A delete of {dataset_id} is already waiting for approval.")
        rid = "dsr_" + secrets.token_hex(4)
        con.execute("INSERT INTO dataset_requests (id, dataset_id, root_id, action, status, requested_by, requested_at, reason)"
                    " VALUES (?, ?, ?, 'delete', 'pending', ?, ?, ?)", (rid, dataset_id, root, by, now(), reason))
        audit(con, by, "dataset_delete_requested", comment=reason,
              details={"dataset_id": dataset_id, "root_id": root, "request_id": rid, "whole_dataset": whole,
                       "ids": [r["id"] for r in targets], "needs": "approval by someone other than " + by + " (or an admin)"})
    if G.is_admin(by):
        return decide_delete(app, rid, True, by, "admin: deleted at once", admin_self=True)
    return get_request(app, rid)


def get_request(app, rid: str) -> dict[str, Any]:
    with app.state.db.connect() as con:
        r = con.execute("SELECT * FROM dataset_requests WHERE id = ?", (rid,)).fetchone()
    if not r:
        raise DsError(404, f"No dataset request '{rid}'.")
    return dict(r)


def decide_delete(app, rid: str, approve: bool, by: str, comment: str, admin_self: bool = False) -> dict[str, Any]:
    req = get_request(app, rid)
    if req["status"] != "pending":
        raise DsError(409, f"This request is {req['status']}.")
    if G.same_person(by, req["requested_by"]) and not (admin_self or G.is_admin(by)):
        raise DsError(403, f"Four eyes: {req['requested_by']} asked for this delete, so someone else (or an admin) must decide "
                           "(names are compared on letters and digits only; names, not authenticated identities).")
    store = app.state.datasets
    with app.state.db.connect() as con:
        root, targets, whole = _targets(con, req["dataset_id"])
        if approve:
            _guard(con, root, targets, whole, "delete")  # re-checked: something may use it now
        con.execute("UPDATE dataset_requests SET status = ?, decided_by = ?, decided_at = ?, comment = ? WHERE id = ?",
                    ("approved" if approve else "rejected", by, now(), comment, rid))
        if not approve:
            audit(con, by, "dataset_delete_rejected", comment=comment,
                  details={"dataset_id": req["dataset_id"], "root_id": root, "request_id": rid, "requested_by": req["requested_by"]})
            return get_request(app, rid)
        gone = []
        for r in targets:
            if r["state"] == DELETED:
                continue
            d = store.dir(r["id"])
            tomb = {"dataset_id": r["id"], "root_id": root, "parent_id": r["parent_id"], "name": r["name"], "v": r["v"], "op": r["op"],
                    "rows": r["rows"], "n_cols": r["n_cols"], "sha256": r["sha256"], "created_at": r["created_at"],
                    "created_by": r["created_by"], "deleted_at": now(), "requested_by": req["requested_by"], "approved_by": by,
                    "reason": req["reason"], "request_id": rid,
                    "kept": ["lineage.json", "this tombstone", "the datasets row", "every lineage edge", "the audit log"]}
            if d.is_dir():
                for p in d.iterdir():
                    if p.name == "lineage.json":
                        continue
                    shutil.rmtree(p) if p.is_dir() else p.unlink()
                (d / "TOMBSTONE.json").write_text(json.dumps(tomb, indent=1))
            con.execute("UPDATE datasets SET state = ?, state_at = ?, state_by = ? WHERE id = ?", (DELETED, now(), by, r["id"]))
            gone.append({"id": r["id"], "v": r["v"], "sha256": r["sha256"]})
        audit(con, by, "dataset_deleted", comment=comment or req["reason"],
              details={"dataset_id": req["dataset_id"], "root_id": root, "request_id": rid, "requested_by": req["requested_by"],
                       "approved_by": by, "admin": G.is_admin(by), "whole_dataset": whole, "deleted": gone})
    return {**get_request(app, rid), "deleted": gone}


def verify(app, dataset_id: str, by: str, whole: bool = False) -> dict[str, Any]:
    store = app.state.datasets
    with app.state.db.connect() as con:
        root = root_of(con, dataset_id)
        rows = [r for r in tree_rows(con, root) if (whole or r["id"] == dataset_id)]
        out = []
        for r in rows:
            if r["state"] == DELETED:
                out.append({"id": r["id"], "v": r["v"], "ok": None, "status": "deleted (tombstone)", "stored": r["sha256"]})
                continue
            now_sha = file_sha256(store.csv_path(r["id"]))
            ok = bool(now_sha and r["sha256"] and now_sha == r["sha256"])
            status = "ok" if ok else ("file missing" if not now_sha else "no stored hash" if not r["sha256"] else "MISMATCH")
            con.execute("UPDATE datasets SET verified_at = ?, verified_ok = ? WHERE id = ?", (now(), int(ok), r["id"]))
            out.append({"id": r["id"], "v": r["v"], "ok": ok, "status": status, "stored": r["sha256"], "now": now_sha})
        bad = [x for x in out if x["ok"] is False]
        audit(con, by, "dataset_integrity_failed" if bad else "dataset_verified",
              details={"dataset_id": dataset_id, "root_id": root, "checked": len(out), "failed": [x["id"] for x in bad],
                       "results": [{k: x.get(k) for k in ("id", "status", "stored", "now")} for x in out]})
    return {"root_id": root, "results": out, "ok": not bad}


def export_manifest(app, dataset_id: str, by: str) -> dict[str, Any]:
    """What goes in the zip next to data.csv: the chain up to the root, the stored and current hash."""
    store = app.state.datasets
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
        sha_now = file_sha256(store.csv_path(dataset_id))
        audit(con, by, "dataset_exported", details={"dataset_id": dataset_id, "root_id": root, "v": r["v"], "sha256": sha_now,
                                                    "integrity_ok": sha_now == r["sha256"]})
    return {"dataset_id": dataset_id, "name": r["name"], "v": r["v"], "root_id": root, "target": r["target"], "rows": r["rows"],
            "sha256_stored": r["sha256"], "sha256_file": sha_now, "integrity_ok": sha_now == r["sha256"],
            "lineage_chain": chain, "exported_at": now(), "exported_by": by,
            "how_to_check": "sha256sum data.csv must equal sha256_stored"}

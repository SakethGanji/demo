"""P24 (plan §5.10, §6.1, §7 risk 6, §11 item 5): purge with the cross-team impact report.

A purge removes columns (or rows, by `__row_id`) of one table from a dataset **and from every byte that holds them**:

1. **Request** (`POST /api/datasets/{id}/purge {table?, columns?, row_ids?, reason, by}`, `data.purge`, a maintainer):
   - a new version is written from `{id}` without the columns / rows (the other tables carried by reference, the
     table's splits carried for the rows that stay, the keys that do not use a purged column re-checked);
   - the **affected blobs**: every part of that table in every version of the dataset (deleted ones too: their bytes
     may still be stored), those versions' legacy CSV and frozen-holdout blobs, the upload sources that committed
     them, and the join caches built from those tables;
   - the **reference set**: every `blob_refs` row on an affected blob, across all teams (a version of another dataset
     that deduped onto the bytes, a run's pin, a join frame, an upload), plus the runs trained on affected versions,
     the grantee teams of live shares with their runs on the data (`shares.runs_on`), and the registered models and
     champions built from those runs;
   - the **teams involved** (owner team, every team holding a ref or a run) each need an approval by one of their
     **owners**, never the requester (four eyes: `policy.four_eyes('data.purge_decide')`, §11 item 5);
   - a legal hold on anything in the set blocks the request (409 `legal_hold`).
2. **Decide** (`POST /api/purges/{id}/decide {team, approve, comment, by}`): an owner of `team` approves for that team
   (or rejects: the purge is rejected). When every team has approved, it executes.
3. **Execute, all-or-nothing** (one transaction, re-checked: a hold, or a new team in the set, stops it): every
   referencing version → `deleted` (its refs dropped), run pins on the blobs → `purged:<role>` markers (the run rows
   stay: re-verify reports `purged`, not a mismatch), every other ref dropped, the default moved to the new version,
   the blobs → `purging`; then each blob's bytes go through `lake.store.delete_blob(..., bypass_governance=True)` (every
   S3 version) and the row becomes `purged` (kept forever). Bytes that fail to delete stay `purging` and
   `POST /api/purges/{id}/resume` finishes them.

Nothing runs on a timer; every step is audited in every involved team's chain. The audit log never holds row values.
Not built: the redacted manifest for sensitive column names (§5.10 step 4); a purge of a CSV-era (unmigrated) root.
"""

from __future__ import annotations

import json
import secrets
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.concurrency import run_in_threadpool

from ..authz import body, path, require
from ..authz.deps import query
from . import governance as G
from .schema import DEFAULT_TEAM, audit, now

ACTION = "data.purge"
_NEEDLES: dict[str, Any] = {}  # F-X4c: purge id → the purged values (memory only, never stored)


class PurgeError(Exception):
    def __init__(self, status: int, code: str, message: str, **extra: Any) -> None:
        self.status, self.payload = status, {"code": code, "message": message, **extra}
        super().__init__(message)


def _q(n: int) -> str:
    return ",".join("?" * n)


def _team_of_version(con, vid: str) -> str:
    r = con.execute("SELECT COALESCE(d.team_id, p.team_id) FROM datasets d LEFT JOIN projects p ON p.id = d.project_id WHERE d.id = ?",
                    (vid,)).fetchone()
    return (r[0] if r else None) or DEFAULT_TEAM


# --------------------------------------------------------------------------- the reference set


def affected(con, root: str, table: str, columns: list[str], row_ids: list[int], exclude: set[str]) -> dict[str, Any]:
    """Blobs holding the purged values, and everything that references them (see the module doc)."""
    vers = [dict(r) for r in con.execute(
        "SELECT d.id, d.state, d.version_no, d.storage, vt.schema_json FROM datasets d JOIN version_tables vt ON vt.version_id = d.id"
        " AND vt.table_name = ? WHERE COALESCE(d.root_id, d.id) = ? ORDER BY d.version_no", (table, root))]
    hit = []
    for v in vers:
        if v["id"] in exclude:
            continue
        cols = {c["name"] for c in json.loads(v["schema_json"] or "[]")}
        if (columns and cols & set(columns)) or row_ids:
            hit.append(v)
    blobs: dict[str, dict[str, Any]] = {}

    def add(sha: str | None, why: str) -> None:
        if sha:
            blobs.setdefault(sha, {"sha256": sha, "why": []})["why"].append(why)

    for v in hit:
        for r in con.execute("SELECT blob_sha256, part FROM table_files WHERE version_id = ? AND table_name = ?", (v["id"], table)):
            add(r[0], f"part {r[1]} of table {table} in {v['id']}")
        for r in con.execute("SELECT blob_sha256, role FROM blob_refs WHERE owner_kind = 'version' AND owner_id = ?"
                             " AND (role = 'legacy_csv' OR role = 'frozen_holdout' OR role = 'source')", (v["id"],)):
            add(r[0], f"{r[1]} of {v['id']}")
        for r in con.execute("SELECT x.blob_sha256 FROM blob_refs x JOIN uploads u ON u.id = x.owner_id WHERE x.owner_kind = 'upload'"
                             " AND x.role = 'source' AND u.committed_version_id = ?", (v["id"],)):
            add(r[0], f"upload source of {v['id']}")
    table_shas = {r[0] for v in hit for r in con.execute("SELECT table_sha256 FROM version_tables WHERE version_id = ? AND table_name = ?",
                                                          (v["id"], table))}
    frames = []
    for f in con.execute("SELECT id, version_id, inputs_json, cache_blob FROM join_frames WHERE cache_blob IS NOT NULL"):
        ins = json.loads(f["inputs_json"] or "[]")
        if any(x.get("table_sha256") in table_shas for x in ins):
            frames.append(f["id"])
            add(f["cache_blob"], f"cache of join frame {f['id']}")
    shas = sorted(blobs)
    refs: list[dict[str, Any]] = []
    for i in range(0, len(shas), 300):
        ch = shas[i:i + 300]
        refs += [dict(r) for r in con.execute(f"SELECT blob_sha256, owner_kind, owner_id, role, team_id FROM blob_refs"
                                              f" WHERE blob_sha256 IN ({_q(len(ch))}) ORDER BY owner_kind, owner_id", ch)]
    states = {}
    for i in range(0, len(shas), 300):
        ch = shas[i:i + 300]
        states.update({r[0]: {"state": r[1], "bytes": r[2], "kind": r[3], "key": r[4]} for r in con.execute(
            f"SELECT sha256, state, bytes, kind, key FROM blobs WHERE sha256 IN ({_q(len(ch))})", ch)})
    for s in shas:
        blobs[s].update(states.get(s, {"state": "missing"}))
    # versions of any dataset (any team) that reference the bytes
    ref_versions = sorted({r["owner_id"] for r in refs if r["owner_kind"] == "version"} | {v["id"] for v in hit if v["state"] != "deleted"})
    ref_versions = [v for v in ref_versions if v not in exclude]
    vinfo = []
    for vid in ref_versions:
        r = con.execute("SELECT d.id, COALESCE(d.root_id, d.id) AS root, d.name, d.version_no, d.state, d.project_id,"
                        " COALESCE(d.team_id, p.team_id) AS team FROM datasets d LEFT JOIN projects p ON p.id = d.project_id"
                        " WHERE d.id = ?", (vid,)).fetchone()
        if r:
            vinfo.append({**dict(r), "team": r["team"] or DEFAULT_TEAM, "other_dataset": r["root"] != root})
    vids = [v["id"] for v in vinfo] + [v["id"] for v in hit if v["state"] == "deleted"]
    runs: dict[str, dict[str, Any]] = {}
    if vids:
        for r in con.execute(f"SELECT r.id, r.status, r.dataset_id, r.project_id, COALESCE(r.team_id, p.team_id, d.team_id) AS team"
                             f" FROM runs r LEFT JOIN projects p ON p.id = r.project_id LEFT JOIN datasets d ON d.id = r.dataset_id"
                             f" WHERE r.dataset_id IN ({_q(len(vids))}) OR r.id IN (SELECT run_id FROM run_inputs WHERE version_id IN ({_q(len(vids))}))",
                             (*vids, *vids)):
            runs[r["id"]] = {**dict(r), "team": r["team"] or DEFAULT_TEAM, "via": "trained on an affected version"}
    for x in refs:
        if x["owner_kind"] == "run" and x["owner_id"] not in runs:
            r = con.execute("SELECT r.id, r.status, r.dataset_id, r.project_id, COALESCE(r.team_id, p.team_id) AS team FROM runs r"
                            " LEFT JOIN projects p ON p.id = r.project_id WHERE r.id = ?", (x["owner_id"],)).fetchone()
            if r:
                runs[r["id"]] = {**dict(r), "team": r["team"] or x["team_id"], "via": f"pins {x['role']}"}
    rids = sorted(runs)
    models, champs = [], []
    if rids:
        models = [dict(r) for r in con.execute(f"SELECT id, model_name, version, approval, stage, run_id FROM model_versions"
                                               f" WHERE run_id IN ({_q(len(rids))})", rids)]
        mids = [m["id"] for m in models] or [""]
        champs = [dict(r) for r in con.execute(f"SELECT id, status, project_id, run_id, version_id FROM champions WHERE status IN"
                                               f" ('pending', 'approved') AND (run_id IN ({_q(len(rids))}) OR version_id IN ({_q(len(mids))}))",
                                               (*rids, *mids))]
    return {"versions_with_values": [{"id": v["id"], "v": v["version_no"], "state": v["state"]} for v in hit],
            "blobs": list(blobs.values()), "refs": refs, "versions": vinfo, "runs": list(runs.values()),
            "join_frames": frames, "models": models, "champions": champs}


def _shares(con, root: str, project: str | None, runs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    from . import shares as SH

    out = []
    for s in con.execute("SELECT * FROM shares WHERE status = 'active' AND ((object_kind = 'dataset' AND object_id = ?)"
                         " OR (object_kind = 'project' AND object_id = ?)) ORDER BY requested_at", (root, project or "")):
        s = dict(s)
        theirs = SH.runs_on(con, s)
        out.append({"share_id": s["id"], "grantee_team": s["grantee_team"], "scope": s["scope"], "pii": s["pii"],
                    "grantee_runs": [r["id"] for r in theirs]})
    return out


def impact(con, root: str, table: str, columns: list[str], row_ids: list[int], new_version: str | None, app=None) -> dict[str, Any]:
    from . import artifacts as A, holds as H

    owner = con.execute("SELECT team_id, project_id, name FROM dataset_roots WHERE id = ?", (root,)).fetchone()
    owner_team = (owner[0] if owner else None) or _team_of_version(con, root)
    a = affected(con, root, table, columns, row_ids, {new_version} if new_version else set())
    sh = _shares(con, root, owner[1] if owner else None, a["runs"])
    teams = {owner_team} | {v["team"] for v in a["versions"]} | {r["team"] for r in a["runs"]} | \
            {x["team_id"] for x in a["refs"]} | {s["grantee_team"] for s in sh if s["grantee_runs"]}
    keys: set[tuple[str, str]] = set()
    for v in a["versions"]:
        keys |= H.version_keys(con, v["id"])
    for r in a["runs"]:
        keys |= H.run_keys(con, r["id"])
    keys |= {("team", t) for t in teams}
    holds = H.active(con, keys)
    by_team: dict[str, dict[str, Any]] = {}
    for t in sorted(teams):
        by_team[t] = {"versions": [v["id"] for v in a["versions"] if v["team"] == t],
                      "runs": [r["id"] for r in a["runs"] if r["team"] == t],
                      "refs": sum(1 for x in a["refs"] if x["team_id"] == t),
                      "shares": [s["share_id"] for s in sh if s["grantee_team"] == t]}
    derived = A.inventory(app, con, a, columns, table) if app is not None else None
    return {"root_id": root, "owner_team": owner_team, "table": table, "columns": columns, "n_row_ids": len(row_ids),
            **a, "shares": sh, "derived": derived, "teams": sorted(teams), "by_team": by_team,
            "holds": [{k: h[k] for k in ("id", "case_ref", "scope_kind", "scope_id")} for h in holds],
            "bytes": sum(b.get("bytes") or 0 for b in a["blobs"]),
            "note": "executing deletes every listed version, replaces run pins by purged markers (re-verify says purged), "
                    "and deletes every listed blob (all S3 versions). Champions and models listed here lose their data."}


# --------------------------------------------------------------------------- the new version


def _new_version(app, parent: str, table: str, columns: list[str], row_ids: list[int], by: str, reason: str) -> dict[str, Any]:
    import pyarrow as pa

    from ..lake import splitstore as SS
    from ..lake.writer import ROW_ID, VersionWriter, _q as qi

    db, store = app.state.db, app.state.lake
    with db.connect() as con:
        tables = [r[0] for r in con.execute("SELECT table_name FROM version_tables WHERE version_id = ? ORDER BY ordinal", (parent,))]
        prim = con.execute("SELECT primary_table, target, COALESCE(root_id, id) FROM datasets WHERE id = ?", (parent,)).fetchone()
        keys = [dict(k) for k in con.execute("SELECT * FROM table_keys WHERE version_id = ? AND table_name = ?", (parent, table))]
        d = con.execute("SELECT default_version_id FROM dataset_roots WHERE id = ?", (prim[2],)).fetchone()
    w = VersionWriter(db, store, op="purge", by=by, parent_id=parent, reason=reason, dedupe=False, primary_table=prim["primary_table"])
    with w:
        for t in tables:
            if t != table:
                w.carry(t)
                w.carry_splits(t)
                continue
            base = w._parent_table(t)
            from ..lake.duck import parquet_rel

            rel = parquet_rel([store.uri(p.key) for p in base.parts])
            excl = f" EXCLUDE ({', '.join(qi(c) for c in columns)})" if columns else ""
            where = ""
            params: list[Any] = []
            if row_ids:
                w.con.execute("SELECT 1").fetchall()
                w.con.register("_purge_ids", pa.table({ROW_ID: pa.array(row_ids, pa.int64())}))
                where = f" WHERE {ROW_ID} NOT IN (SELECT {ROW_ID} FROM _purge_ids)"
            w.write(t, f"SELECT *{excl} FROM {rel}{where}")
            kept_ids = None
            for s in SS.rows_of_version(db, parent):  # the table's splits, for the rows that stay
                if s["table_name"] != t or not s.get("blob_key"):
                    continue
                ids, folds = SS.read_blob(store, s["blob_key"])
                if row_ids:
                    if kept_ids is None:
                        kept_ids = set(row_ids)
                    m = [i not in kept_ids for i in ids.tolist()]
                    ids, folds = ids[m], folds[m]
                view = "_ps_" + secrets.token_hex(4)
                w.con.execute("SELECT 1").fetchall()
                w.con.register(view, pa.table({ROW_ID: pa.array(ids, pa.int64()), "fold": pa.array(folds, pa.int8())}))
                try:
                    w.split(t, s["split_key"], f"SELECT {ROW_ID}, fold FROM {view}", strategy=s["strategy"], seed=int(s["seed"]),
                            holdout_share=str(s["holdout_share"]), n_folds=int(s["n_folds"]), target=s["target"],
                            params_json=json.loads(s["params_json"] or "{}"), carried_from=s["id"])
                except Exception as e:  # noqa: BLE001
                    w.op_log.append({"op": "split_not_carried", "table": t, "split_key": s["split_key"], "why": type(e).__name__})
                finally:
                    w.con.execute("SELECT 1").fetchall()
                    w.con.unregister(view)
            for k in keys:
                cols = json.loads(k["columns_json"])
                if not set(cols) & set(columns):
                    w.key(t, cols, kind=k["kind"], declared_by=k["declared_by"])
        w.extra_ops.append({"op": "purge", "table": table, "columns": columns, "n_row_ids": len(row_ids)})
        return w.commit(make_default=bool(d and d[0] == parent), target=prim["target"], note=f"purge of {table} on {parent}")


# --------------------------------------------------------------------------- request / decide / execute


def _load(con, pid: str) -> dict[str, Any]:
    r = con.execute("SELECT * FROM approval_requests WHERE id = ? AND action = ?", (pid, ACTION)).fetchone()
    if not r:
        raise PurgeError(404, "not_found", f"No purge request {pid}.")
    out = dict(r)
    out["payload"] = json.loads(out["payload"] or "{}")
    return out


def _audit_all(con, by: str, action: str, teams: list[str], details: dict[str, Any], comment: str = "", pid: str = "") -> None:
    for t in teams:
        audit(con, by, action, details, comment=comment, team_id=t, object_kind="purge", object_id=pid)


def request(app, vid: str, b: dict[str, Any], by: str) -> dict[str, Any]:
    from . import holds as H

    reason = str(b.get("reason") or "").strip()
    if len(reason) < 10:
        raise PurgeError(400, "reason_required", "reason is required (at least 10 characters): why these values must go.")
    columns = b.get("columns") or []
    row_ids = b.get("row_ids") or []
    if not isinstance(columns, list) or not all(isinstance(c, str) and c for c in columns):
        raise PurgeError(400, "bad_columns", "columns is a list of column names.")
    if not isinstance(row_ids, list) or not all(isinstance(i, int) and not isinstance(i, bool) for i in row_ids):
        raise PurgeError(400, "bad_row_ids", "row_ids is a list of __row_id integers.")
    if not columns and not row_ids:
        raise PurgeError(400, "nothing_to_purge", "Send columns and/or row_ids.")
    db = app.state.db
    with db.connect() as con:
        v = con.execute("SELECT id, COALESCE(root_id, id) AS root, storage, state, primary_table FROM datasets WHERE id = ?", (vid,)).fetchone()
        if not v:
            raise PurgeError(404, "not_found", f"No dataset version {vid}.")
        if v["state"] == "deleted":
            raise PurgeError(410, "deleted", f"{vid} is deleted: purge from a live version of the dataset.")
        csv = con.execute("SELECT id FROM datasets WHERE COALESCE(root_id, id) = ? AND storage <> 'parquet' AND COALESCE(state, '') <> 'deleted'",
                          (v["root"],)).fetchall()
        if csv:
            raise PurgeError(409, "not_parquet", "Every version of the dataset must be migrated (P10) before a purge: "
                                                 + ", ".join(r[0] for r in csv[:10]))
        table = str(b.get("table") or v["primary_table"] or "")
        t = con.execute("SELECT schema_json FROM version_tables WHERE version_id = ? AND table_name = ?", (vid, table)).fetchone()
        if not t:
            raise PurgeError(404, "table_not_found", f"{vid} has no table {table!r}.")
        have = [c["name"] for c in json.loads(t[0])]
        missing = [c for c in columns if c not in have]
        if missing:
            raise PurgeError(400, "unknown_column", f"Table {table!r} of {vid} has no column(s) {', '.join(missing)}.", columns=missing)
        if len(set(columns)) >= len(have):
            raise PurgeError(400, "all_columns", "A purge keeps at least one column; delete the dataset instead.")
        if con.execute("SELECT 1 FROM approval_requests WHERE action = ? AND object_id = ? AND status IN ('pending', 'approved')",
                       (ACTION, v["root"])).fetchone():
            raise PurgeError(409, "purge_pending", f"A purge of {v['root']} is already waiting; decide or withdraw it first.")
        pre = impact(con, v["root"], table, columns, row_ids, None, app)
    if pre["holds"]:
        raise PurgeError(409, "legal_hold", H.refusal([{**h, "placed_by": "", "placed_at": ""} for h in pre["holds"]],
                                                      f"purge {table}.{','.join(columns) or 'rows'}")["message"],
                         cases=sorted({h["case_ref"] for h in pre["holds"]}), holds=pre["holds"])
    new = _new_version(app, vid, table, columns, row_ids, by, f"purge: {reason}"[:500])
    with db.connect() as con:
        rep = impact(con, v["root"], table, columns, row_ids, new["version_id"], app)
        pid = "pur_" + secrets.token_hex(5)
        payload = {"version_id": vid, "root_id": v["root"], "table": table, "columns": columns, "row_ids": row_ids,
                   "new_version_id": new["version_id"], "teams_needed": rep["teams"], "approvals": {}, "impact": rep}
        con.execute("INSERT INTO approval_requests (id, action, object_kind, object_id, team_id, payload, status, requested_by,"
                    " requested_at, reason) VALUES (?, ?, 'dataset', ?, ?, ?, 'pending', ?, ?, ?)",
                    (pid, ACTION, v["root"], rep["owner_team"], json.dumps(payload, default=str), by, now(), reason))
        _audit_all(con, by, "purge_requested", rep["teams"], {
            "purge_id": pid, "dataset_id": vid, "root_id": v["root"], "table": table, "columns": columns, "n_row_ids": len(row_ids),
            "new_version_id": new["version_id"], "teams_needed": rep["teams"], "n_blobs": len(rep["blobs"]), "bytes": rep["bytes"],
            "versions": [x["id"] for x in rep["versions"]], "runs": [x["id"] for x in rep["runs"]],
            "shares": [s["share_id"] for s in rep["shares"]]}, comment=reason, pid=pid)
    return get(app, pid)


def get(app, pid: str) -> dict[str, Any]:
    with app.state.db.connect() as con:
        r = _load(con, pid)
    p = r.pop("payload")
    need = [t for t in p["teams_needed"] if t not in p["approvals"]]
    return {**r, **{k: p[k] for k in ("version_id", "root_id", "table", "columns", "new_version_id", "teams_needed", "approvals")},
            "n_row_ids": len(p.get("row_ids") or []), "waiting_for": need, "impact": p["impact"],
            **({"executed": p["executed"]} if p.get("executed") else {}),
            **({"sweep": (p.get("executed") or {}).get("sweep")} if (p.get("executed") or {}).get("sweep") else {})}


def decide(app, pid: str, b: dict[str, Any], by: str) -> dict[str, Any]:
    from ..authz.policy import four_eyes, role_of

    approve, withdraw = b.get("approve"), b.get("withdraw") is True
    comment = str(b.get("comment") or "")[:1000]
    db = app.state.db
    with db.connect() as con:
        r = _load(con, pid)
        p = r["payload"]
        if r["status"] != "pending":
            raise PurgeError(409, "not_pending", f"Purge {pid} is {r['status']}.")
        mine = four_eyes("data.purge_decide", r, by)
        if withdraw:
            from ..authz.policy import is_requester
            mine = "requester" if is_requester("data.purge_decide", r, by) else None
            if not mine:
                raise PurgeError(403, "forbidden", f"Only {r['requested_by']} (who asked) withdraws this purge.")
            con.execute("UPDATE approval_requests SET status = 'withdrawn', decided_by = ?, decided_at = ?, comment = ? WHERE id = ?",
                        (by, now(), comment, pid))
            _audit_all(con, by, "purge_withdrawn", p["teams_needed"], {"purge_id": pid, "root_id": p["root_id"]}, comment, pid)
            return get(app, pid)
        if not isinstance(approve, bool):
            raise PurgeError(400, "bad_body", 'Send {"team": …, "approve": true|false} or {"withdraw": true}.')
        if mine:
            raise PurgeError(409, "four_eyes", mine)
        team = con.execute("SELECT id FROM teams WHERE id = ? OR slug = ?", (str(b.get("team") or ""), str(b.get("team") or ""))).fetchone()
        if not team:
            raise PurgeError(400, "team_required", f"team is the team you decide for: one of {', '.join(p['teams_needed'])}.")
        team = team[0]
        if team not in p["teams_needed"]:
            raise PurgeError(409, "team_not_involved", f"{team} is not involved in this purge ({', '.join(p['teams_needed'])}).")
        if team in p["approvals"]:
            raise PurgeError(409, "already_approved", f"{team} approved on {p['approvals'][team]['at']} ({p['approvals'][team]['by']}).")
    pr = principal_or_typed(app, by)
    with db.connect() as con:
        role = role_of(con, pr.id, team) if pr is not None else None
    if role != "owner" and not G.poc_mode():
        raise PurgeError(403, "owner_required", f"A purge is approved for {team} by an owner of {team} (§11: owners of every affected "
                                                f"team + four eyes); {by} is {role or 'not a member'}.", team=team, needed="owner")
    if approve and not G.poc_mode() and any(a["by_key"] == G.identity_key(by) for a in p["approvals"].values()):
        raise PurgeError(409, "four_eyes", f"{by} already approved for another team: each team's approval is a different person.")
    with db.connect() as con:
        if not approve:
            con.execute("UPDATE approval_requests SET status = 'rejected', decided_by = ?, decided_at = ?, comment = ? WHERE id = ?",
                        (by, now(), comment, pid))
            _audit_all(con, by, "purge_rejected", p["teams_needed"], {"purge_id": pid, "root_id": p["root_id"], "team": team}, comment, pid)
            return get(app, pid)
        p["approvals"][team] = {"by": by, "by_key": G.identity_key(by), "at": now(), "comment": comment}
        con.execute("UPDATE approval_requests SET payload = ? WHERE id = ? AND status = 'pending'", (json.dumps(p, default=str), pid))
        _audit_all(con, by, "purge_approved", p["teams_needed"], {"purge_id": pid, "root_id": p["root_id"], "team": team,
                                                                  "waiting_for": [t for t in p["teams_needed"] if t not in p["approvals"]]},
                   comment, pid)
    if all(t in p["approvals"] for t in p["teams_needed"]):
        return execute(app, pid, by)
    return get(app, pid)


def principal_or_typed(app, by: str):
    """The request's principal (or the typed `by` when no header was sent)."""
    from ..authz import identity
    from ..authz.context import current

    ctx = current()
    if ctx is not None and ctx.resolved and ctx.principal is not None:
        return ctx.principal
    return identity._from_name(app.state.db, by, "typed")


def execute(app, pid: str, by: str) -> dict[str, Any]:
    """All-or-nothing: the DB changes in one transaction, then the derived copies (F-X4c), then the bytes, then the
    sweep (see the module doc)."""
    from . import artifacts as A, dsets, holds as H

    db = app.state.db
    with db.connect() as con:
        r = _load(con, pid)
        p = r["payload"]
        rep = impact(con, p["root_id"], p["table"], p["columns"], p.get("row_ids") or [], p["new_version_id"], app)
        new_teams = [t for t in rep["teams"] if t not in p["teams_needed"]]
        if new_teams:  # the reference set grew since the request: those teams approve too
            p["teams_needed"] += new_teams
            p["impact"] = rep
            con.execute("UPDATE approval_requests SET payload = ? WHERE id = ?", (json.dumps(p, default=str), pid))
            _audit_all(con, by, "purge_impact_grew", p["teams_needed"], {"purge_id": pid, "new_teams": new_teams}, pid=pid)
            con.commit()
            raise PurgeError(409, "impact_grew", f"The reference set now reaches {', '.join(new_teams)}: an owner of each approves too.",
                             new_teams=new_teams)
        if rep["holds"]:
            raise PurgeError(409, "legal_hold", "A legal hold now covers part of the reference set: "
                             + ", ".join(sorted({h["case_ref"] for h in rep["holds"]})), holds=rep["holds"])
        shas = [b["sha256"] for b in rep["blobs"] if b.get("state") in ("pending", "live", "purging")]
        # F-X4c: the purged values, read from the lake before the bytes go (kept in memory only, for the scrub and sweep)
        needles = A.collect(app, con, [v["id"] for v in rep["versions_with_values"]], p["table"], p["columns"],
                            p.get("row_ids") or [], p["new_version_id"])
        _NEEDLES[pid] = needles
        reason = f"purge:{pid}"
        vids = [v["id"] for v in rep["versions"] if v["state"] != "deleted"]
        roots = sorted({v["root"] for v in rep["versions"]})
        for vid in vids:
            con.execute("UPDATE datasets SET state = 'deleted', state_at = ?, state_by = ? WHERE id = ?", (now(), by, vid))
            dsets.drop_version_refs(con, vid)
        q = _q(len(shas)) if shas else "''"
        n_marked = n_dropped = 0
        if shas:
            n_marked = con.execute(f"UPDATE blob_refs SET role = 'purged:' || role WHERE owner_kind = 'run' AND role NOT LIKE 'purged:%'"
                                   f" AND blob_sha256 IN ({q})", shas).rowcount or 0
            n_dropped = con.execute(f"DELETE FROM blob_refs WHERE owner_kind <> 'run' AND blob_sha256 IN ({q})", shas).rowcount or 0
            con.execute(f"UPDATE blobs SET state = 'purging', purge_reason = ? WHERE sha256 IN ({q}) AND state IN ('pending', 'live', 'purging')",
                        (reason, *shas))
        cur = con.execute("SELECT default_version_id FROM dataset_roots WHERE id = ?", (p["root_id"],)).fetchone()
        moved = None
        if cur and cur[0] in vids:
            dsets._write_card(con, p["root_id"], {"default_version_id": p["new_version_id"], "updated_at": now(), "updated_by": by})
            moved = {"from": cur[0], "to": p["new_version_id"]}
        for root in roots:
            from . import schema as S
            S.sync_root_state(con, root)
        p["executed"] = {"at": now(), "by": by, "versions_deleted": vids, "run_pins_marked": n_marked, "refs_dropped": n_dropped,
                         "blobs": shas, "default_moved": moved}
        con.execute("UPDATE approval_requests SET status = 'approved', decided_by = ?, decided_at = ?, payload = ? WHERE id = ?",
                    (by, now(), json.dumps(p, default=str), pid))
        from ..labels.purge import purge_in_tx  # LB-A: label events redacted, snapshots + overlays purged with the rest

        p["executed"]["labels"] = purge_in_tx(con, pid, p["root_id"], p["table"], p["columns"], p.get("row_ids") or [], by)
        con.execute("UPDATE approval_requests SET payload = ? WHERE id = ?", (json.dumps(p, default=str), pid))
        _audit_all(con, by, "purge_executed", p["teams_needed"], {
            "purge_id": pid, "root_id": p["root_id"], "new_version_id": p["new_version_id"], "versions_deleted": vids,
            "run_pins_marked": n_marked, "refs_dropped": n_dropped, "n_blobs": len(shas), "approvals": {t: a["by"] for t, a in p["approvals"].items()},
            "default_moved": moved}, pid=pid)
    _holdout_files(app, vids)
    _scrub(app, pid, by, rep)
    return _delete_bytes(app, pid, by)


def _scrub(app, pid: str, by: str, rep: dict[str, Any]) -> None:
    """F-X4c: every derived copy of the purged values (catalog/artifacts.py) deleted or redacted; recorded and audited."""
    from . import artifacts as A

    nd = _NEEDLES.get(pid)
    if nd is None:
        return
    inv = rep.get("derived")
    if not inv:
        with app.state.db.connect() as con:
            inv = A.inventory(app, con, rep, rep["columns"], rep["table"])
    try:
        out = A.scrub(app, pid, by, inv, nd, rep["table"])
    except Exception as e:  # noqa: BLE001 - the sweep will report what is left; the error is recorded
        out = {"error": f"{type(e).__name__}: {e}"[:500]}
    with app.state.db.connect() as con:
        r = _load(con, pid)
        p = r["payload"]
        p["executed"]["derived"] = {k: out.get(k) for k in ("n_deleted", "n_redacted", "deleted", "redacted", "purged_models",
                                                            "model_versions_purged", "db", "error")}
        con.execute("UPDATE approval_requests SET payload = ? WHERE id = ?", (json.dumps(p, default=str), pid))
        _audit_all(con, by, "purge_derived_scrubbed", p["teams_needed"], {
            "purge_id": pid, "deleted": out.get("n_deleted"), "redacted": out.get("n_redacted"),
            "purged_models": [m["run_id"] for m in out.get("purged_models") or []],
            "model_versions_purged": out.get("model_versions_purged"), "db": (out.get("db") or {}).get("redacted"),
            "error": out.get("error")}, pid=pid)


def _sweep(app, pid: str, by: str, p: dict[str, Any]) -> dict[str, Any]:
    from . import artifacts as A

    shas = list(p["executed"].get("blobs") or [])
    inv = (p.get("impact") or {}).get("derived")
    try:
        res = A.sweep(app, _NEEDLES.get(pid), shas, inv)
    except Exception as e:  # noqa: BLE001
        res = {"status": "purge_incomplete", "error": f"{type(e).__name__}: {e}"[:500], "hits": [], "n_hits": 0}
    return res


def _holdout_files(app, vids: list[str]) -> None:
    """DL2 frozen holdout samples (CSV files beside a tombstone) hold row values: they go too."""
    store = app.state.datasets
    for v in vids:
        try:
            f = store.dir(v) / "holdout_sample.csv"
            if f.exists():
                f.unlink()
        except Exception:  # noqa: BLE001
            pass


def _delete_bytes(app, pid: str, by: str) -> dict[str, Any]:
    from ..lake.store import delete_blob

    db, store = app.state.db, app.state.lake
    with db.connect() as con:
        r = _load(con, pid)
        p = r["payload"]
        todo = [x[0] for x in con.execute("SELECT sha256 FROM blobs WHERE purge_reason = ? AND state = 'purging' ORDER BY sha256",
                                          (f"purge:{pid}",))]
    done, failed = [], []
    for sha in todo:
        try:
            out = delete_blob(db, store, sha, by=by, reason=f"purge:{pid}", bypass_governance=True)
        except Exception as e:  # noqa: BLE001 - stays 'purging'; POST /api/purges/{id}/resume finishes it
            failed.append({"sha256": sha, "error": f"{type(e).__name__}: {e}"[:300]})
            continue
        with db.connect() as con:
            con.execute("UPDATE blobs SET state = 'purged', purged_at = ?, purged_by = ? WHERE sha256 = ? AND state = 'purging'",
                        (now(), by, sha))
        done.append({"sha256": sha, "key": out.get("key"), "versions_removed": out.get("versions_removed")})
    with db.connect() as con:
        p = _load(con, pid)["payload"]
        p["executed"]["bytes_deleted"] = (p["executed"].get("bytes_deleted") or 0) + len(done)
        p["executed"]["bytes_failed"] = failed
        con.execute("UPDATE approval_requests SET payload = ? WHERE id = ?", (json.dumps(p, default=str), pid))
        _audit_all(con, by, "purge_bytes_deleted", p["teams_needed"], {"purge_id": pid, "deleted": len(done), "failed": len(failed),
                                                                       "store": store.root}, pid=pid)
    sw = _sweep(app, pid, by, p)  # F-X4c: are the values gone everywhere?
    with db.connect() as con:
        p = _load(con, pid)["payload"]
        p["executed"]["sweep"] = sw
        status = "used" if not failed and sw["status"] == "clean" else "approved"
        con.execute("UPDATE approval_requests SET status = ?, payload = ? WHERE id = ?", (status, json.dumps(p, default=str), pid))
        _audit_all(con, by, "purge_sweep" if sw["status"] == "clean" else "purge_incomplete", p["teams_needed"], {
            "purge_id": pid, "status": sw["status"], "values_searched": sw.get("values"), "files_searched": sw.get("files_searched"),
            "n_hits": sw.get("n_hits"), "where": [{k: h.get(k) for k in ("where", "path", "table", "key", "kind")} for h in sw.get("hits") or []][:50],
            "s3": sw.get("s3")}, pid=pid)
    if status == "used":
        _NEEDLES.pop(pid, None)
    return {**get(app, pid), "blobs_deleted": done, "blobs_failed": failed, "sweep": sw}


def resume(app, pid: str, by: str) -> dict[str, Any]:
    """Finish the bytes that failed, scrub the derived copies again (when the values are still in this process), sweep."""
    with app.state.db.connect() as con:
        r = _load(con, pid)
    if r["status"] != "approved" or not r["payload"].get("executed"):
        raise PurgeError(409, "not_executing", f"Purge {pid} is {r['status']}: only an executed purge with bytes or copies left is resumed.")
    if pid in _NEEDLES:
        _scrub(app, pid, by, r["payload"]["impact"])
    return _delete_bytes(app, pid, by)


def resweep(app, pid: str, by: str) -> dict[str, Any]:
    """POST /api/purges/{id}/sweep: run the verification sweep again (structure only after a restart: the values are
    never stored)."""
    with app.state.db.connect() as con:
        r = _load(con, pid)
        p = r["payload"]
    if not p.get("executed"):
        raise PurgeError(409, "not_executed", f"Purge {pid} is {r['status']}: nothing to sweep yet.")
    sw = _sweep(app, pid, by, p)
    with app.state.db.connect() as con:
        p = _load(con, pid)["payload"]
        p["executed"]["sweep"] = sw
        con.execute("UPDATE approval_requests SET payload = ? WHERE id = ?", (json.dumps(p, default=str), pid))
        _audit_all(con, by, "purge_sweep" if sw["status"] == "clean" else "purge_incomplete", p["teams_needed"], {
            "purge_id": pid, "status": sw["status"], "values_searched": sw.get("values"), "n_hits": sw.get("n_hits"), "resweep": True}, pid=pid)
    return {**get(app, pid), "sweep": sw}


def listing(app, team: str | None) -> list[dict[str, Any]]:
    with app.state.db.connect() as con:
        rows = [dict(r) for r in con.execute("SELECT id, status, object_id, team_id, requested_by, requested_at, reason, payload"
                                             " FROM approval_requests WHERE action = ? ORDER BY requested_at DESC", (ACTION,))]
    out = []
    for r in rows:
        p = json.loads(r.pop("payload") or "{}")
        if team and team not in p.get("teams_needed", []):
            continue
        out.append({**r, "root_id": r.pop("object_id"), "table": p.get("table"), "columns": p.get("columns"),
                    "teams_needed": p.get("teams_needed"), "approvals": {t: a["by"] for t, a in (p.get("approvals") or {}).items()}})
    return out


# --------------------------------------------------------------------------- a run's re-verify


def run_marker(con, run_id: str, dataset_id: str | None) -> dict[str, Any] | None:
    """Was the data this run trained on purged? (its pins became `purged:*`, or its version's table parts were purged)."""
    pins = [dict(r) for r in con.execute("SELECT x.role, x.blob_sha256, b.purge_reason, b.purged_at, b.purged_by FROM blob_refs x"
                                         " JOIN blobs b ON b.sha256 = x.blob_sha256 WHERE x.owner_kind = 'run' AND x.owner_id = ?"
                                         " AND x.role LIKE 'purged:%'", (run_id,))]
    parts = []
    if dataset_id:
        parts = [dict(r) for r in con.execute("SELECT f.table_name, f.blob_sha256, b.purge_reason, b.purged_at, b.purged_by FROM table_files f"
                                              " JOIN blobs b ON b.sha256 = f.blob_sha256 WHERE f.version_id = ? AND b.state IN ('purging', 'purged')"
                                              " AND b.purge_reason LIKE 'purge:%'", (dataset_id,))]
    hit = pins + parts
    if not hit:
        return None
    pids = sorted({(h["purge_reason"] or "").split(":", 1)[-1] for h in hit if h.get("purge_reason")})
    return {"purge_ids": pids, "purged_at": max((h["purged_at"] or "") for h in hit) or None,
            "blobs": sorted({h["blob_sha256"] for h in hit}), "pins": [h["role"] for h in pins]}


# --------------------------------------------------------------------------- routes

router = APIRouter(prefix="/api")


def _who(b: dict[str, Any]) -> str:
    from .api import _who as w

    return w(b.get("by") or G.current_actor())


async def _go(fn, *a):
    try:
        return await run_in_threadpool(fn, *a)
    except PurgeError as e:
        raise HTTPException(e.status, e.payload) from e


@router.post("/datasets/{dataset_id}/purge", status_code=201, dependencies=[require("data.purge", path("dataset_id", "dataset"))])
async def purge_request(dataset_id: str, request: Request, b: dict[str, Any]) -> dict[str, Any]:
    """{table?, columns?, row_ids?, reason, by}: a new version without the values, and the impact report across teams. An
    owner of every involved team approves (POST /api/purges/{id}/decide); then it executes, all-or-nothing."""
    b = b if isinstance(b, dict) else {}
    by = _who(b)
    try:
        return await run_in_threadpool(request_fn, request.app, dataset_id, b, by)
    except PurgeError as e:
        G.refused(request.app.state.db, by, "purge", e.status, e.payload, {"dataset_id": dataset_id})
        raise HTTPException(e.status, e.payload) from e


def request_fn(app, vid, b, by):
    return request(app, vid, b, by)


@router.get("/purges", dependencies=[require("data.read_meta", query("team", "team"), default="global")])
async def list_purges(request: Request, team: str | None = None) -> dict[str, Any]:
    return {"purges": await run_in_threadpool(listing, request.app, team)}


@router.get("/purges/{pid}", dependencies=[require("data.read_meta", path("pid", "purge"))])
async def get_purge(pid: str, request: Request) -> dict[str, Any]:
    return await _go(get, request.app, pid)


@router.post("/purges/{pid}/decide", dependencies=[require("data.purge_decide", body("team", "team"), path("pid", "purge"))])
async def decide_purge(pid: str, request: Request, b: dict[str, Any]) -> dict[str, Any]:
    """{team, approve: true|false, comment?, by}: an owner of `team` decides for it (not the requester; one person, one
    team). Or {withdraw: true} by the requester."""
    b = b if isinstance(b, dict) else {}
    by = _who(b)
    try:
        return await run_in_threadpool(decide, request.app, pid, b, by)
    except PurgeError as e:
        G.refused(request.app.state.db, by, "purge_decide", e.status, e.payload, {"purge_id": pid})
        raise HTTPException(e.status, e.payload) from e


@router.post("/purges/{pid}/resume", dependencies=[require("data.purge_decide", path("pid", "purge"))])
async def resume_purge(pid: str, request: Request, b: dict[str, Any] | None = None) -> dict[str, Any]:
    b = b if isinstance(b, dict) else {}
    return await _go(resume, request.app, pid, _who(b))


@router.post("/purges/{pid}/sweep", dependencies=[require("data.purge_decide", path("pid", "purge"))])
async def sweep_purge(pid: str, request: Request, b: dict[str, Any] | None = None) -> dict[str, Any]:
    """F-X4c: run the post-purge verification sweep again; `executed.sweep` and the audit record the result."""
    b = b if isinstance(b, dict) else {}
    return await _go(resweep, request.app, pid, _who(b))


def _scope_purge(con: Any, i: str):
    from ..authz.scope import Scope, _dataset

    r = con.execute("SELECT object_id FROM approval_requests WHERE id = ? AND action = ?", (i, ACTION)).fetchone()
    s = _dataset(con, r[0]) if r else None
    return Scope(s.team_id, s.project_id, "purge", i) if s else None


def _register() -> None:
    from ..authz.scope import RESOLVERS

    RESOLVERS.setdefault("purge", _scope_purge)


_register()

"""Frozen snapshots (labeling plan §4.3, §5.4): a resolution written once as a content-addressed Parquet blob in the lake
plus a canonical manifest blob; identified by snapshot_sha256; "same result" = equal events + policy + values hashes.

    create(app_or_db, store, layer_id, policy, by) -> snapshot
    get(db, snapshot_id) -> snapshot ;  verify(db, store, snapshot_id) -> {ok, …} ;  load(db, store, snapshot_id) -> [rows]
POC: built synchronously in the request (the plan's `label_snapshot` job is the 10M-row path)."""

from __future__ import annotations

import hashlib
import json
import secrets
import struct
from pathlib import Path
from typing import Any

from ..catalog.schema import audit, now
from .resolve import resolve
from .store import _layer_full, current_schema
from .values import LabelError, canon_json

COLS = ("__row_id", "column", "item_key", "epoch_hash", "value", "is_null", "status", "method", "n_votes", "n_annotators", "agreement", "quality",
        "confidence", "sources", "row_hash_ctx", "row_hash_all", "ctx_cols_sha256", "ctx_cols", "base_value_hash",
        "seen_version_id", "last_seq")


def values_sha(rows: list[dict[str, Any]]) -> str:
    h = hashlib.sha256(b"ensemble.labels/1\n")
    for r in sorted(rows, key=lambda x: (int(x["__row_id"] if "__row_id" in x else x["row_id"]), x["column"])):
        rid = int(r["__row_id"] if "__row_id" in r else r["row_id"])
        v = r["value"]
        h.update(struct.pack("<q", rid) + r["column"].encode() + b"\x1f" + (b"\x00" if v is None else v.encode()) + b"\x1f"
                 + r["status"].encode() + b"\x1e")
    return h.hexdigest()


def events_sha(db: Any, layer_id: str, through_seq: int) -> str:
    h = hashlib.sha256(b"ensemble.labelevents/1\n")
    with db.connect() as con:
        for r in con.execute("SELECT id, seq, kind, source, actor, value FROM label_events WHERE layer_id = ? AND seq <= ?"
                             " AND purged_at IS NULL ORDER BY seq", (layer_id, through_seq)):
            h.update(r[0].encode() + b"\x1f" + struct.pack("<q", int(r[1])) + b"\x1f" + r[2].encode() + r[3].encode()
                     + r[4].encode() + (b"\x00" if r[5] is None else r[5].encode()) + b"\x1e")
    return h.hexdigest()


def _put(db: Any, store: Any, path: Path, ext: str, n_rows: int | None, owner: str, role: str, team: str) -> str:
    from ..lake import manifest as M

    sha, nbytes = M.file_sha256(path)
    key = store.put_file(path, sha, ext)
    with db.connect() as con:
        con.execute("INSERT INTO blobs (sha256, key, kind, bytes, n_rows, state, created_at) VALUES (?, ?, 'label_snapshot', ?, ?, 'live', ?)"
                    " ON CONFLICT (sha256) DO UPDATE SET state = 'live' WHERE blobs.state IN ('pending', 'live')",
                    (sha, key, nbytes, n_rows, now()))
        con.execute("INSERT INTO blob_refs (blob_sha256, owner_kind, owner_id, role, team_id, created_at)"
                    " VALUES (?, 'label_snapshot', ?, ?, ?, ?) ON CONFLICT DO NOTHING", (sha, owner, role, team, now()))
    return sha


def _table(rows: list[dict[str, Any]]):
    import pyarrow as pa

    data = {c: [] for c in COLS}
    for r in rows:
        for c in COLS:
            data[c].append(r.get("row_id") if c == "__row_id" else r.get(c))
    types = {"__row_id": pa.int64(), "is_null": pa.bool_(), "n_votes": pa.int32(), "n_annotators": pa.int32(),
             "last_seq": pa.int64()}
    for r in rows:
        r.setdefault("item_key", "")
    return pa.table({c: pa.array(data[c], type=types.get(c, pa.string())) for c in COLS})


def create(db: Any, store: Any, layer_id: str, policy: dict[str, Any] | None, by: str) -> dict[str, Any]:
    import pyarrow.parquet as pq

    res = resolve(db, layer_id, policy)
    with db.connect() as con:
        lay = _layer_full(con, layer_id)
        sch = current_schema(con, layer_id)
    rows = res["rows"]
    sid = "lsn_" + secrets.token_hex(8)
    pol = res["policy"]
    pol_sha = hashlib.sha256(canon_json(pol).encode()).hexdigest()
    vsha = values_sha(rows)
    esha = events_sha(db, layer_id, res["through_seq"])
    counts: dict[str, Any] = {"rows": len(rows), "by_status": {}, "by_method": {}, "by_source": {}}
    for r in rows:
        counts["by_status"][r["status"]] = counts["by_status"].get(r["status"], 0) + 1
        counts["by_method"][r["method"]] = counts["by_method"].get(r["method"], 0) + 1
        for s in r["sources"].split(","):
            counts["by_source"][s] = counts["by_source"].get(s, 0) + 1
    counts["applied"] = counts["by_status"].get("applied", 0)
    counts["disputed"] = counts["by_status"].get("disputed", 0)
    counts["prelabel_only"] = counts["by_status"].get("prelabel_only", 0)
    tmp = store.tmp_dir("labelsnap-" + secrets.token_hex(4))
    try:
        f = tmp / "snapshot.parquet"
        pq.write_table(_table(sorted(rows, key=lambda r: (r["row_id"], r["column"]))), f, compression="zstd")
        bsha = _put(db, store, f, "parquet", len(rows), sid, "snapshot", lay["team_id"])
        manifest = {"format": "ensemble.labelsnap/1",
                    "layer": {"id": layer_id, "root_id": lay["root_id"], "table": lay["table_name"], "team_id": lay["team_id"]},
                    "schema": {"version": sch["version"] if sch else 0, "spec_sha256": sch["spec_sha256"] if sch else None,
                               "guidelines_sha256": sch["guidelines_sha256"] if sch else None},
                    "policy": pol, "policy_sha256": pol_sha, "through_seq": res["through_seq"],
                    "events_sha256": esha, "values_sha256": vsha, "blob": {"sha256": bsha, "n_rows": len(rows)},
                    "counts": counts, "created_by": by, "created_at": now(), "id": sid}
        mt = canon_json(manifest)
        mf = tmp / "manifest.json"
        mf.write_text(mt, encoding="utf-8")
        msha = _put(db, store, mf, "json", None, sid, "manifest", lay["team_id"])
    finally:
        if hasattr(store, "remove_tmp"):
            store.remove_tmp(tmp)
    snap_sha = hashlib.sha256(mt.encode()).hexdigest()
    with db.connect() as con:
        con.execute("INSERT INTO label_snapshots (id, layer_id, through_seq, schema_version, policy, policy_sha256, blob_sha256,"
                    " manifest_blob, snapshot_sha256, values_sha256, events_sha256, counts, state, created_at, created_by)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'draft', ?, ?)",
                    (sid, layer_id, res["through_seq"], sch["version"] if sch else 0, canon_json(pol), pol_sha, bsha, msha,
                     snap_sha, vsha, esha, canon_json(counts), manifest["created_at"], by))
        audit(con, by, "label_snapshot_created", {"snapshot_id": sid, "layer_id": layer_id, "through_seq": res["through_seq"],
                                                  "snapshot_sha256": snap_sha, "applied": counts["applied"]},
              object_kind="label_snapshot", object_id=sid)
    return get(db, sid)


def get(db: Any, snapshot_id: str) -> dict[str, Any]:
    with db.connect() as con:
        r = con.execute("SELECT s.*, l.root_id, l.table_name, l.team_id FROM label_snapshots s JOIN label_layers l ON l.id = s.layer_id"
                        " WHERE s.id = ?", (snapshot_id,)).fetchone()
    if not r:
        raise LabelError(404, "snapshot_not_found", f"No label snapshot {snapshot_id}.")
    d = dict(r)
    d["policy"] = json.loads(d["policy"])
    d["counts"] = json.loads(d["counts"])
    return d


def _blob_path(db: Any, store: Any, sha: str) -> Path:
    with db.connect() as con:
        r = con.execute("SELECT key, state FROM blobs WHERE sha256 = ?", (sha,)).fetchone()
    if not r or r["state"] in ("purging", "purged"):
        raise LabelError(410, "snapshot_purged", "The snapshot's blob was purged.")
    return Path(store.local_copy(r["key"]))


def load(db: Any, store: Any, snapshot_id: str) -> list[dict[str, Any]]:
    import pyarrow.parquet as pq

    s = get(db, snapshot_id)
    if s["state"] == "purged":
        raise LabelError(410, "snapshot_purged", "This snapshot was purged.")
    return pq.read_table(_blob_path(db, store, s["blob_sha256"])).to_pylist()


def verify(db: Any, store: Any, snapshot_id: str) -> dict[str, Any]:
    from ..lake import manifest as M

    s = get(db, snapshot_id)
    if s["state"] == "purged":
        return {"snapshot_id": snapshot_id, "ok": None, "status": "purged"}
    out: dict[str, Any] = {"snapshot_id": snapshot_id}
    try:
        p = _blob_path(db, store, s["blob_sha256"])
        mp = _blob_path(db, store, s["manifest_blob"])
    except (LabelError, FileNotFoundError) as e:
        return {**out, "ok": False, "status": "missing", "message": str(e)}
    out["blob_ok"] = M.file_sha256(p)[0] == s["blob_sha256"]
    out["manifest_ok"] = M.file_sha256(mp)[0] == s["manifest_blob"] and \
        hashlib.sha256(mp.read_bytes()).hexdigest() == s["manifest_blob"]
    out["snapshot_sha_ok"] = hashlib.sha256(mp.read_bytes()).hexdigest() == s["manifest_blob"] and \
        hashlib.sha256(mp.read_text(encoding="utf-8").encode()).hexdigest() == s["snapshot_sha256"]
    try:
        out["values_ok"] = values_sha(load(db, store, snapshot_id)) == s["values_sha256"]
    except Exception:  # noqa: BLE001 - an unreadable blob is a mismatch
        out["values_ok"] = False
    out["ok"] = all(out[k] for k in ("blob_ok", "manifest_ok", "snapshot_sha_ok", "values_ok"))
    out["status"] = "ok" if out["ok"] else "mismatch"
    return out

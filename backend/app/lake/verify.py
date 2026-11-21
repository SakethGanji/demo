"""P02: verify a lake (Parquet) version against its hashes: every part re-hashed against its key, every table sha
recomputed from its parts, the manifest blob re-hashed and checked canonical, and the DB rows compared with it."""

from __future__ import annotations

import json
from typing import Any

from ..catalog import schema as S
from . import manifest as M
from .store import BlobStore


def _rehash(store: BlobStore, key: str) -> str | None:
    if not store.exists(key):
        return None
    with store.open(key) as f:
        return M.file_sha256(f)[0]


def load_manifest(db, store: BlobStore, version_id: str) -> tuple[dict[str, Any], dict[str, Any] | None, bytes | None]:
    with db.connect() as con:
        v = con.execute("SELECT * FROM datasets WHERE id = ?", (version_id,)).fetchone()
        if not v:
            raise KeyError(version_id)
        v = dict(v)
        b = con.execute("SELECT key FROM blobs WHERE sha256 = ?", (v.get("manifest_sha256"),)).fetchone() if v.get("manifest_sha256") else None
    if not b or not store.exists(b[0]):
        return v, None, None
    with store.open(b[0]) as f:
        raw = f.read()
    return v, json.loads(raw), raw


def verify_version(db, store: BlobStore, version_id: str) -> dict[str, Any]:
    with db.connect() as con:
        v = con.execute("SELECT id, root_id, storage, manifest_sha256, data_sha256, state FROM datasets WHERE id = ?",
                        (version_id,)).fetchone()
        if not v:
            raise KeyError(version_id)
        v = dict(v)
        tables = [dict(t) for t in con.execute("SELECT * FROM version_tables WHERE version_id = ? ORDER BY ordinal", (version_id,))]
        files = [dict(f) for f in con.execute("SELECT f.*, b.key, b.state AS blob_state FROM table_files f JOIN blobs b"
                                              " ON b.sha256 = f.blob_sha256 WHERE f.version_id = ? ORDER BY f.table_name, f.part",
                                              (version_id,))]
        mkey = con.execute("SELECT key FROM blobs WHERE sha256 = ?", (v["manifest_sha256"],)).fetchone()
        splits = [dict(s) for s in con.execute("SELECT s.id, s.split_sha256, s.blob_sha256, b.key FROM splits s JOIN blobs b"
                                               " ON b.sha256 = s.blob_sha256 WHERE s.version_id = ?", (version_id,))]
    problems: list[str] = []
    checked_files = []
    for f in files:
        now = _rehash(store, f["key"])
        st = "ok" if now == f["blob_sha256"] else ("missing" if now is None else "MISMATCH")
        checked_files.append({"table": f["table_name"], "part": f["part"], "key": f["key"], "stored": f["blob_sha256"],
                              "now": now, "status": st})
        if st != "ok":
            problems.append(f"{f['table_name']} part {f['part']}: {st}")
    for t in tables:
        parts = [(f["role"], f["blob_sha256"]) for f in files if f["table_name"] == t["table_name"]]
        if not parts or M.table_sha256(parts) != t["table_sha256"]:
            problems.append(f"{t['table_name']}: table_sha256 does not match its parts")
    mstatus, m = "missing", None
    if mkey:
        now = _rehash(store, mkey[0])
        if now is None:
            mstatus = "missing"
        elif now != v["manifest_sha256"]:
            mstatus = "MISMATCH"
        else:
            with store.open(mkey[0]) as fh:
                m, mp = M.check_manifest_bytes(fh.read())
            problems += mp
            mstatus = "ok" if not mp else "invalid"
            if m.get("data_sha256") != v["data_sha256"]:
                problems.append("datasets.data_sha256 differs from the manifest")
            mt = {t["name"]: t for t in (m.get("data") or {}).get("tables", [])}
            for t in tables:
                x = mt.get(t["table_name"])
                if not x or x["table_sha256"] != t["table_sha256"] or x["schema_sha256"] != t["schema_sha256"]:
                    problems.append(f"{t['table_name']}: DB row differs from the manifest")
            if set(mt) != {t["table_name"] for t in tables}:
                problems.append("the manifest and the DB list different tables")
    if mstatus != "ok":
        problems.append(f"manifest: {mstatus}")
    checked_splits = []
    for s in splits:
        now = _rehash(store, s["key"])
        st = "ok" if now == s["blob_sha256"] else ("missing" if now is None else "MISMATCH")
        if st == "ok":
            sh, _ = M.split_sha256_parquet(store.local_copy(s["key"]))
            st = "ok" if sh == s["split_sha256"] else "SPLIT HASH MISMATCH"
        checked_splits.append({"id": s["id"], "status": st})
        if st != "ok":
            problems.append(f"split {s['id']}: {st}")
    ok = not problems
    at = S.now()
    with db.connect() as con:
        for f in checked_files:
            con.execute("UPDATE blobs SET verified_at = ?, verified_ok = ? WHERE sha256 = ?", (at, int(f["status"] == "ok"), f["stored"]))
        con.execute("UPDATE datasets SET verified_at = ?, verified_ok = ? WHERE id = ?", (at, int(ok), version_id))
    return {"id": version_id, "root_id": v["root_id"], "storage": v["storage"], "ok": ok,
            "status": "ok" if ok else "MISMATCH", "manifest_sha256": v["manifest_sha256"], "manifest": mstatus,
            "files": checked_files, "splits": checked_splits, "problems": problems, "verified_at": at}

"""P09 (plan §4.3, §5.7, DLA4 #3): splits as content-addressed Parquet, carried by join, checked five ways.

A split of a Parquet version's table is a blob `(__row_id BIGINT, fold TINYINT)` sorted by `__row_id` and covering
exactly the table's rows. `fold` keeps its meaning: 0..k-1 a training row's fold, -1 holdout, -2 excluded, -3 tuning.
Its identity is `split_sha256` (§3.3, `manifest.SplitHasher`), independent of the Parquet writer.

* **Insert-only rows.** `splits` rows are never updated or deleted (triggers). One transaction writes the row, its
  `blob_refs` entry and the audit row `split_created {split_id, split_sha256, blob_sha256, …}`. A split drawn lazily (the
  first time a target is used) is committed here (`commit_split`); a split carried into a new version is committed
  with the version by the VersionWriter (`carry_into` / `given_into` → `w.split`).
* **Carry by join** (§5.7): `SELECT c.__row_id, p.fold FROM child c LEFT JOIN parent_split p USING (__row_id)`; a row
  with no parent fold gets `newfold = splitmix64(__row_id ⊕ seed)`: the holdout when it falls under the new-row holdout
  share, else fold `mix mod k`. Deterministic and engine-independent (numpy over the new ids only).
* **Runs pin their split.** A run records `split_id` / `split_sha256` in `run_inputs` and holds a
  `blob_refs (owner_kind 'run', role 'split')` on the blob, so the exact split every run used survives GC and a tamper.
* **The five checks** (`integrity`): 1 the blob re-hashed against its key; 2 `split_sha256` recomputed from the file;
  3 the `split_created` audit row (and its chain link, written from P19); 4 every earlier run that passed the gate on
  this split id, or on the same (version, table, split key), recorded the same hash; 5 a carried split moved no
  surviving row to another fold. The protecting hashes live outside the mutable row: the blob key, the audit row, the
  runs' `run_inputs` (trigger-protected) and their pins.
* **Restore** (`restore_offer` / `restore`) puts back the split the earliest run used, from that run's pinned blob.
* **The positional view.** Most consumers still read a run's split as DL4 JSON by read position (`cfg.splits_path`).
  For a Parquet version that file is a *view* written from the blob (`view_of`), never a source: the worker's gate
  re-derives it from the verified blob before a fit.

CSV-era versions keep DL4's split JSON and its recorded hashes until P10 migrates them. A Parquet version made before
P09 (P05–P08 wrote DL4 JSON bridges) has its recorded JSON adopted on first use (`adopt_legacy`), hash-checked against
the hash DL4 recorded first, with `legacy_sha256` = the JSON's sha256 (what its old runs recorded as `splits_sha256`).
"""

from __future__ import annotations

import json
import logging
import secrets
from pathlib import Path
from typing import Any

import numpy as np

from ..catalog import schema as S
from . import manifest as M
from .store import BlobStore, parse_key

log = logging.getLogger("ensemble.splits")

ROW_ID = "__row_id"
CHECKS = ("blob", "split_sha256", "audit", "runs", "carry")
CHECK_TEXT = {"blob": "the blob re-hashed against its key", "split_sha256": "split_sha256 recomputed from the file",
              "audit": "the split_created audit row", "runs": "the hash every earlier run on this split recorded",
              "carry": "no surviving row changed fold against the split it was carried from"}


class SplitError(Exception):
    def __init__(self, status: int, code: str, message: str, **extra: Any) -> None:
        super().__init__(message)
        self.status, self.code, self.message, self.extra = status, code, message, extra

    @property
    def payload(self) -> dict[str, Any]:
        return {"code": self.code, "message": self.message, **self.extra}


# --------------------------------------------------------------------------- lookups


def is_lake(db, version_id: str) -> bool:
    with db.connect() as con:
        r = con.execute("SELECT storage FROM datasets WHERE id = ?", (version_id,)).fetchone()
    return bool(r and r[0] == "parquet")


def primary_table(db, version_id: str) -> str:
    with db.connect() as con:
        r = con.execute("SELECT primary_table FROM datasets WHERE id = ?", (version_id,)).fetchone()
        if r and r[0]:
            return r[0]
        t = con.execute("SELECT table_name FROM version_tables WHERE version_id = ? ORDER BY ordinal LIMIT 1", (version_id,)).fetchone()
    return t[0] if t else "data"


def table_row_ids(version_id: str, table: str | None = None) -> np.ndarray:
    """The table's `__row_id`s in read order (sorted): position i of every positional consumer is row id ids[i]."""
    from .. import frames
    from . import duck

    lt = frames.lake_table(frames.Table(version_id, table or frames.PRIMARY_TABLE))
    with duck.job() as c:
        return c.execute(f"SELECT {ROW_ID} FROM {frames.lake_sql(lt)} ORDER BY {ROW_ID}").fetchnumpy()[ROW_ID].astype("int64")


def row_ids(db, dstore, version_id: str) -> np.ndarray | None:
    """The root row id of every row of a version, by read position: a Parquet version's `__row_id`s; a CSV-era
    version's lineage row map (D1/D2, until P10), or 0..n-1 for a root; None when unmapped."""
    from .. import frames

    if is_lake(db, version_id):
        return table_row_ids(version_id)
    lin = dstore.lineage(version_id) if dstore is not None else None
    n = frames.n_rows(frames.table(version_id))
    rr = (lin or {}).get("root_rows")
    if rr and len(rr) == n:
        return np.asarray(rr, dtype="int64")
    with db.connect() as con:
        r = con.execute("SELECT parent_id FROM datasets WHERE id = ?", (version_id,)).fetchone()
    if (not r or not r[0]) and not (lin or {}).get("parent"):
        return np.arange(n, dtype="int64")
    return None


def get_row(db, version_id: str, table: str, split_key: str) -> dict[str, Any] | None:
    with db.connect() as con:
        r = con.execute("SELECT s.*, b.key AS blob_key FROM splits s LEFT JOIN blobs b ON b.sha256 = s.blob_sha256"
                        " WHERE s.version_id = ? AND s.table_name = ? AND s.split_key = ?", (version_id, table, split_key)).fetchone()
    return dict(r) if r else None


def get_by_id(db, split_id: str) -> dict[str, Any] | None:
    with db.connect() as con:
        r = con.execute("SELECT s.*, b.key AS blob_key FROM splits s LEFT JOIN blobs b ON b.sha256 = s.blob_sha256 WHERE s.id = ?",
                        (split_id,)).fetchone()
    return dict(r) if r else None


def rows_of_version(db, version_id: str) -> list[dict[str, Any]]:
    with db.connect() as con:
        return [dict(r) for r in con.execute("SELECT s.*, b.key AS blob_key FROM splits s LEFT JOIN blobs b ON b.sha256 = s.blob_sha256"
                                             " WHERE s.version_id = ? ORDER BY s.table_name, s.split_key", (version_id,))]


def _blob_key(db, sha: str) -> str | None:
    with db.connect() as con:
        r = con.execute("SELECT key FROM blobs WHERE sha256 = ?", (sha,)).fetchone()
    return r[0] if r else None


def read_blob(store: BlobStore, key: str) -> tuple[np.ndarray, np.ndarray]:
    """(row ids, folds) of a split file, in file order."""
    import pyarrow.parquet as pq

    t = pq.read_table(str(store.local_copy(key)), columns=[ROW_ID, "fold"])
    return (t.column(0).to_numpy(zero_copy_only=False).astype("int64"),
            t.column(1).to_numpy(zero_copy_only=False).astype("int64"))


# --------------------------------------------------------------------------- metadata ↔ the DL4 dict


def _floats_back(v: Any, key: str = "") -> Any:
    """params_json holds no floats (decimal text); the view gives the UI its numbers back."""
    if isinstance(v, dict):
        return {k: _floats_back(x, k) for k, x in v.items()}
    if isinstance(v, list):
        return [_floats_back(x, key) for x in v]
    if isinstance(v, str) and key in ("positive_rate", "mean", "tuning_share", "parsed_share", "rows_per_group"):
        try:
            return float(v)
        except ValueError:
            return v
    return v


def meta_of(sp: dict[str, Any]) -> dict[str, Any]:
    """The `splits` row fields of a DL4 split dict (target, strategy, seed, share as text, folds, params)."""
    from ..lake.derive import no_floats

    extra = {k: sp[k] for k in ("params", "stratified", "cutoff", "tuning_cutoff", "diagram", "carried", "adopted_from",
                                "derived_from") if sp.get(k) is not None}
    return {"target": sp.get("target"), "strategy": sp.get("strategy") or "random-stratified",
            "seed": int(sp.get("seed") or 42), "holdout_share": M.decimal_str(sp.get("holdout_share") or 0),
            "n_folds": int(sp.get("n_folds") or 5), "params_json": no_floats(extra)}


def view(row: dict[str, Any], folds: np.ndarray) -> dict[str, Any]:
    """The DL4-shaped split (positions in read order) of a Parquet split: fold i is the row with the i-th smallest id."""
    extra = _floats_back(json.loads(row.get("params_json") or "{}"))
    fold = np.asarray(folds, dtype="int64")
    sp: dict[str, Any] = {"seed": int(row["seed"]), "target": row["target"], "strategy": row["strategy"], "key": row["split_key"],
                          "stratified": bool(extra.get("stratified", False)), "holdout_share": float(row["holdout_share"]),
                          "n_folds": int(row["n_folds"]), "holdout": np.flatnonzero(fold == -1).tolist(),
                          "fold": fold.tolist()}
    if extra.get("params"):
        sp["params"] = extra["params"]
        sp["tuning"] = np.flatnonzero(fold == -3).tolist()
        sp["excluded"] = np.flatnonzero(fold == -2).tolist()
    for k in ("cutoff", "tuning_cutoff", "diagram", "carried", "adopted_from", "derived_from"):
        if extra.get(k) is not None:
            sp[k] = extra[k]
    sp.update(created_at=row["created_at"], storage="parquet",
              view_of={"split_id": row["id"], "split_sha256": row["split_sha256"], "blob_sha256": row["blob_sha256"],
                       "table": row["table_name"], "note": "a positional view of the Parquet split; the blob is the record"})
    return sp


def view_of_row(db, store: BlobStore, row: dict[str, Any]) -> dict[str, Any]:
    key = row.get("blob_key") or _blob_key(db, row["blob_sha256"])
    if not key or not store.exists(key):
        raise SplitError(409, "split_integrity", f"The split file of {row['split_key']} ({row['blob_sha256'][:12]}…) is missing "
                                                 "from the lake.", split_id=row["id"])
    _, folds = read_blob(store, key)
    return view(row, folds)


def write_view(path: Path, sp: dict[str, Any]) -> Path:
    """The view file consumers read by `cfg.splits_path` (rewritten when it differs from the blob's)."""
    raw = json.dumps(sp)
    if not (path.exists() and path.read_text() == raw):
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(f".{secrets.token_hex(3)}.tmp")
        tmp.write_text(raw)
        tmp.replace(path)
    return path


def is_view(sp: dict[str, Any] | None) -> bool:
    return bool(sp and sp.get("view_of"))


# --------------------------------------------------------------------------- folds


def new_folds(ids: np.ndarray, seed: int, share: float, n_folds: int) -> np.ndarray:
    """newfold(__row_id) (§5.7): splitmix64(__row_id ⊕ seed) < share·2⁶⁴ → holdout (-1), else fold mix mod k."""
    ids = np.asarray(ids, dtype="int64")
    if not len(ids):
        return np.zeros(0, dtype="int64")
    mix = M.splitmix64(ids ^ np.int64(seed))
    out = (mix % np.uint64(max(1, n_folds))).astype("int64")
    if share:
        out[mix.astype(np.float64) / 2.0 ** 64 < float(share)] = -1
    return out


def _arrow(ids: np.ndarray, folds: np.ndarray) -> Any:
    import pyarrow as pa

    return pa.table({ROW_ID: pa.array(np.asarray(ids, dtype="int64")), "fold": pa.array(np.asarray(folds, dtype="int8"))})


def _join_carry(con, child_sql: str, parent_ids: np.ndarray, parent_folds: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """(child ids sorted, the parent's fold or -128 where the row is new) by `child LEFT JOIN parent_split USING (__row_id)`."""
    name = "_psplit_" + secrets.token_hex(3)
    con.execute("SELECT 1").fetchall()
    con.register(name, _arrow(parent_ids, parent_folds))
    try:
        r = con.execute(f"SELECT c.{ROW_ID}, coalesce(p.fold::INTEGER, -128) AS fold FROM ({child_sql}) c"
                        f" LEFT JOIN {name} p USING ({ROW_ID}) ORDER BY c.{ROW_ID}").fetchnumpy()
    finally:
        con.execute("SELECT 1").fetchall()
        con.unregister(name)
    return r[ROW_ID].astype("int64"), r["fold"].astype("int64")


def parent_split_arrays(db, store: BlobStore, parent_ids: np.ndarray, parent_view: dict[str, Any]
                        ) -> tuple[np.ndarray, np.ndarray, str | None]:
    """(ids, folds, split_id) of a parent's split: from its blob (Parquet), else its DL4 JSON by position (CSV-era)."""
    vo = parent_view.get("view_of") or {}
    if vo.get("split_id"):
        row = get_by_id(db, vo["split_id"])
        if row:
            ids, folds = read_blob(store, row.get("blob_key") or _blob_key(db, row["blob_sha256"]))
            return ids, folds, row["id"]
    folds = np.asarray(parent_view.get("fold") or [], dtype="int64")
    ids = np.asarray(parent_ids, dtype="int64")
    if len(folds) != len(ids):
        raise SplitError(409, "split_rows", f"The parent's split has {len(folds)} rows for {len(ids)} rows.")
    order = np.argsort(ids, kind="stable")
    return ids[order], folds[order], None


def carry_into(w, table: str, parent_ids: np.ndarray, parent_view: dict[str, Any] | None, new_holdout_share: float,
               *, target_in_child: bool = True, parent_vid: str | None = None) -> dict[str, Any] | None:
    """Carry the parent's split into the writer's new table by join (§5.7). Structured (group / time) splits are not
    carried row by row: they are drawn fresh on first use, as before. Returns the carried summary or None."""
    if parent_view is None or parent_view.get("params") or not target_in_child:
        return None
    pids, pfolds, pid = parent_split_arrays(w.db, w.store, parent_ids, parent_view)
    ids, fold = _join_carry(w.con, f"SELECT {ROW_ID} FROM {w._rel(table)}", pids, pfolds)
    new = fold == -128
    seed, k = int(parent_view.get("seed") or 42), int(parent_view.get("n_folds") or 5)
    fold[new] = new_folds(ids[new], seed, new_holdout_share, k)
    kept_hold = int(((fold == -1) & ~new).sum())
    carried = {"from": parent_view.get("view_of", {}).get("split_id") or "(DL4 JSON by position)",
               "from_version": parent_vid, "by": "__row_id (join)", "kept_holdout": kept_hold,
               "new_rows": int(new.sum()), "new_holdout": int(((fold == -1) & new).sum()),
               "new_holdout_share": M.decimal_str(new_holdout_share)}
    sp = {**{k_: parent_view.get(k_) for k_ in ("seed", "target", "strategy", "stratified", "holdout_share", "n_folds")},
          "carried": carried}
    return _to_writer(w, table, parent_view["key"], ids, fold, sp, carried_from=pid)


def given_into(w, table: str, ids_read_order: np.ndarray, sp: dict[str, Any], parent_ids: np.ndarray | None,
               parent_view: dict[str, Any] | None) -> dict[str, Any] | None:
    """A caller's split (DL4 dict by read position of the new table) written with the version. It counts as carried
    from the parent's split of the same key only when no surviving row changed fold (check 5 holds by construction)."""
    from ..splits import split_key

    fold = np.asarray(sp.get("fold") or [], dtype="int64")
    ids = np.asarray(ids_read_order, dtype="int64")
    if len(fold) != len(ids):
        return None
    key = sp.get("key") or split_key(sp.get("target"), sp.get("strategy") or "random-stratified", sp.get("params"))
    pid, changed = None, None
    if parent_view is not None and parent_view.get("key") == key and parent_ids is not None:
        pids, pfolds, psid = parent_split_arrays(w.db, w.store, parent_ids, parent_view)
        _, pf = _join_carry(w.con, f"SELECT {ROW_ID} FROM {w._rel(table)}", pids, pfolds)
        order = np.argsort(ids, kind="stable")
        surv = pf != -128
        changed = int((pf[surv] != fold[order][surv]).sum())
        pid = psid if changed == 0 else None
    meta_sp = {**sp, "key": key, "given": {"by": "the caller (by position)", **({"changed_vs_parent": changed} if changed is not None else {})}}
    return _to_writer(w, table, key, ids, fold, meta_sp, carried_from=pid)


def _to_writer(w, table: str, key: str, ids: np.ndarray, fold: np.ndarray, sp: dict[str, Any], *,
               carried_from: str | None, legacy_sha256: str | None = None) -> dict[str, Any]:
    name = "_csplit_" + secrets.token_hex(3)
    w.con.execute("SELECT 1").fetchall()
    w.con.register(name, _arrow(ids, fold))
    try:
        meta = meta_of(sp)
        out = w.split(table, key, f"SELECT {ROW_ID}, fold FROM {name}", strategy=meta["strategy"], seed=meta["seed"],
                      holdout_share=meta["holdout_share"], n_folds=meta["n_folds"], target=meta["target"],
                      params_json=meta["params_json"], carried_from=carried_from, legacy_sha256=legacy_sha256)
    finally:
        w.con.execute("SELECT 1").fetchall()
        w.con.unregister(name)
    return {**out, "key": key, "carried_from": carried_from, **({"carried": sp["carried"]} if sp.get("carried") else {})}


# --------------------------------------------------------------------------- a split drawn after the version (lazy)


def _team(con, version_id: str) -> tuple[str, str]:
    r = con.execute("SELECT COALESCE(root_id, id), team_id, project_id FROM datasets WHERE id = ?", (version_id,)).fetchone()
    root = r[0] if r else version_id
    return root, (r[1] if r and r[1] else S.team_of_project(con, r[2] if r else None))


def audit_details(row: dict[str, Any], root: str) -> dict[str, Any]:
    """split_created carries everything needed to rebuild the row (restore uses it when the row itself was replaced)."""
    return {"dataset_id": row["version_id"], "root_id": root, "split_id": row["id"], "split_sha256": row["split_sha256"],
            "blob_sha256": row["blob_sha256"], "table": row["table_name"], "split_key": row["split_key"],
            "target": row["target"], "strategy": row["strategy"], "params_json": row["params_json"], "seed": row["seed"],
            "holdout_share": row["holdout_share"], "n_folds": row["n_folds"], "n_rows": row["n_rows"],
            "n_holdout": row["n_holdout"], "carried_from": row.get("carried_from"), "legacy_sha256": row.get("legacy_sha256")}


def commit_split(db, store: BlobStore, version_id: str, table: str, key: str, ids: np.ndarray, folds: np.ndarray,
                 sp: dict[str, Any], *, by: str, legacy_sha256: str | None = None, basis: str = "drawn") -> dict[str, Any]:
    """Write the blob, then ONE transaction: INSERT splits + blob_refs + audit split_created (§5.7). A concurrent draw
    of the same key loses the insert and returns the winner's row (a split is never redrawn)."""
    from . import duck

    ids = np.asarray(ids, dtype="int64")
    folds = np.asarray(folds, dtype="int64")
    have = table_row_ids(version_id, table)
    order = np.argsort(ids, kind="stable")
    ids, folds = ids[order], folds[order]
    if len(ids) != len(have) or not np.array_equal(ids, have):
        raise SplitError(400, "split_rows", f"A split of {table!r} must cover exactly its {len(have):,} rows ({len(ids):,} given).")
    tmp = store.tmp_dir("split-" + secrets.token_hex(4))
    try:
        f = tmp / "split.parquet"
        with duck.job() as c:
            c.execute("SELECT 1").fetchall()  # DuckDB 2.0: the job's setup result must be drained before register()
            c.register("_s", _arrow(ids, folds))
            c.execute(f"COPY (SELECT {ROW_ID}::BIGINT AS {ROW_ID}, fold::TINYINT AS fold FROM _s ORDER BY {ROW_ID})"
                      f" TO '{str(f).replace(chr(39), chr(39) * 2)}' (FORMAT parquet, COMPRESSION zstd)")
        ssha, n = M.split_sha256_parquet(f)
        bsha, nbytes = M.file_sha256(f)
        bkey = store.put_file(f, bsha, "parquet")
    finally:
        if hasattr(store, "remove_tmp"):
            store.remove_tmp(tmp)
    meta = meta_of(sp)
    M.canonical(meta["params_json"])
    row = {"id": "spl_" + secrets.token_hex(6), "version_id": version_id, "table_name": table, "split_key": key,
           "target": meta["target"], "strategy": meta["strategy"], "params_json": json.dumps(meta["params_json"]),
           "seed": meta["seed"], "holdout_share": meta["holdout_share"], "n_folds": meta["n_folds"], "blob_sha256": bsha,
           "split_sha256": ssha, "legacy_sha256": legacy_sha256, "n_rows": int(n), "n_holdout": int((folds == -1).sum()),
           "carried_from": None, "created_at": S.now(), "created_by": by or "system"}
    with db.connect() as con:
        con.execute("BEGIN IMMEDIATE")
        old = con.execute("SELECT id FROM splits WHERE version_id = ? AND table_name = ? AND split_key = ?",
                          (version_id, table, key)).fetchone()
        if old:
            con.rollback()
            return get_by_id(db, old[0])  # type: ignore[return-value]
        root, team = _team(con, version_id)
        con.execute("INSERT INTO blobs (sha256, key, kind, bytes, n_rows, state, created_at) VALUES (?, ?, 'split', ?, ?, 'live', ?)"
                    " ON CONFLICT (sha256) DO UPDATE SET state = 'live' WHERE state IN ('pending', 'live')",
                    (bsha, bkey, nbytes, int(n), S.now()))
        con.execute("INSERT INTO splits (id, version_id, table_name, split_key, target, strategy, params_json, seed, holdout_share,"
                    " n_folds, blob_sha256, split_sha256, legacy_sha256, n_rows, n_holdout, carried_from, created_at, created_by)"
                    " VALUES (:id, :version_id, :table_name, :split_key, :target, :strategy, :params_json, :seed, :holdout_share,"
                    " :n_folds, :blob_sha256, :split_sha256, :legacy_sha256, :n_rows, :n_holdout, :carried_from, :created_at,"
                    " :created_by)", row)
        con.execute("INSERT OR IGNORE INTO blob_refs (blob_sha256, owner_kind, owner_id, role, team_id, created_at)"
                    " VALUES (?, 'split', ?, 'split', ?, ?)", (bsha, row["id"], team, S.now()))
        S.audit(con, by or "system", "split_created", {**audit_details(row, root), "basis": basis})
    return get_by_id(db, row["id"])  # type: ignore[return-value]


def adopt_legacy(db, store: BlobStore, dstore, version_id: str, table: str, key: str, path: Path, *, by: str
                 ) -> dict[str, Any] | None:
    """A Parquet version made before P09 (or a caller that wrote a positional file for a derived version): its DL4 JSON
    becomes the Parquet split. When DL4 recorded the file's hash, the bytes must still match it (a changed
    file is not laundered into a split: 409 split_integrity, restore it first)."""
    import hashlib

    raw = path.read_bytes()
    sp = json.loads(raw)
    if is_view(sp):
        return None
    sha = hashlib.sha256(raw).hexdigest()
    rel = dstore.split_rel(version_id, path)
    recorded = dstore.split_hashes(version_id).get(rel)
    if recorded and recorded != sha:
        raise SplitError(409, "split_integrity", f"{rel} of {version_id} differs from the hash recorded when it was drawn "
                                                 f"({recorded[:12]}… then, {sha[:12]}… now): it is not adopted as the version's "
                                                 "Parquet split. Restore it first (POST …/restore-splits).", file=rel)
    ids = table_row_ids(version_id, table)
    fold = np.asarray(sp.get("fold") or [], dtype="int64")
    if len(fold) != len(ids):
        return None
    basis = "adopted from the DL4 split JSON (hash-checked)" if recorded else "adopted from a positional split file"
    return commit_split(db, store, version_id, table, key, ids, fold, {**sp, "adopted_from": rel}, by=by,
                        legacy_sha256=sha if recorded else None, basis=basis)


# --------------------------------------------------------------------------- run pins


def pin_run(db, run_id: str, row: dict[str, Any]) -> None:
    """The run's hold on its exact split blob (GC never deletes it; restore can always offer it)."""
    with db.connect() as con:
        _, team = _team(con, row["version_id"])
        con.execute("INSERT OR IGNORE INTO blob_refs (blob_sha256, owner_kind, owner_id, role, team_id, created_at)"
                    " VALUES (?, 'run', ?, 'split', ?, ?)", (row["blob_sha256"], run_id, team, S.now()))


def pin_of(row: dict[str, Any]) -> dict[str, Any]:
    """What a run config records about its split (`cfg.split_pin`)."""
    return {"split_id": row["id"], "split_sha256": row["split_sha256"], "blob_sha256": row["blob_sha256"],
            "version_id": row["version_id"], "table": row["table_name"], "split_key": row["split_key"]}


# --------------------------------------------------------------------------- the five checks


def _rehash(store: BlobStore, key: str | None) -> str | None:
    if not key or not store.exists(key):
        return None
    with store.open(key) as f:
        return M.file_sha256(f)[0]


def evidence_runs(db, version_id: str, table: str, split_key: str, split_id: str | None) -> list[dict[str, Any]]:
    """Earlier runs whose fit passed the split gate on this split id, or on the same (version, table, split key): their
    recorded hash (run_inputs.split_sha256 / the DL4 JSON sha for runs before P09), oldest first."""
    with db.connect() as con:
        rows = con.execute(
            "SELECT ri.run_id, ri.split_id, ri.split_sha256, ri.split_legacy_sha256, r.created_at,"
            " json_extract(r.config, '$.split.key') AS key, b.blob_sha256"
            " FROM run_inputs ri JOIN runs r ON r.id = ri.run_id"
            " LEFT JOIN blob_refs b ON b.owner_kind = 'run' AND b.owner_id = ri.run_id AND b.role = 'split'"
            " WHERE ri.role = 'train' AND (ri.split_id = ? OR (ri.version_id = ? AND ri.table_name = ?"
            "   AND json_extract(r.config, '$.split.key') = ?))"
            " AND COALESCE(json_extract(r.result, '$.splits_integrity.match'), 1) = 1"
            " AND json_extract(r.result, '$.splits_integrity.override') IS NULL"
            " ORDER BY r.created_at, ri.run_id", (split_id, version_id, table, split_key)).fetchall()
    return [dict(r) for r in rows if r["split_sha256"] or r["split_legacy_sha256"]]


def _audit_row(db, split_id: str) -> dict[str, Any] | None:
    with db.connect() as con:
        r = con.execute("SELECT id, ts, actor, details, prev_hash, row_hash FROM audit_log WHERE action = 'split_created'"
                        " AND json_extract(details, '$.split_id') = ? ORDER BY id LIMIT 1", (split_id,)).fetchone()
    if not r:
        return None
    d = dict(r)
    d["details"] = json.loads(d["details"] or "{}")
    return d


def integrity(db, store: BlobStore, row: dict[str, Any], *, exclude_run: str | None = None) -> dict[str, Any]:
    """The five checks of §4.3 on one split row. `ok` is False when any check fails; `failed` names them in order."""
    checks: list[dict[str, Any]] = []
    key = row.get("blob_key") or _blob_key(db, row["blob_sha256"])
    pk = parse_key(key) if key else None
    now = _rehash(store, key)
    c1 = bool(now and now == row["blob_sha256"] and pk and pk[0] == row["blob_sha256"])
    checks.append({"n": 1, "check": "blob", "ok": c1, "detail": f"blob {row['blob_sha256'][:12]}… " + (
        "re-hashed: matches its key" if c1 else "is missing from the lake" if now is None else f"re-hashes to {now[:12]}…")})
    folds = None
    if now is not None:
        try:
            sh, n = M.split_sha256_parquet(store.local_copy(key))
            c2 = sh == row["split_sha256"] and n == int(row["n_rows"])
            d2 = f"split_sha256 {sh[:12]}… over {n:,} rows" + ("" if c2 else f" ≠ the row's {row['split_sha256'][:12]}…")
            _, folds = read_blob(store, key)
        except Exception as e:  # noqa: BLE001 - an unreadable file fails the check, it does not crash it
            c2, d2 = False, f"the file does not read as a split: {str(e)[:120]}"
    else:
        c2, d2 = False, "no file to hash"
    checks.append({"n": 2, "check": "split_sha256", "ok": c2, "detail": d2})
    a = _audit_row(db, row["id"])
    if a is None:
        c3, d3 = False, f"no split_created audit row for {row['id']}: the row was not written by the app"
    else:
        ad = a["details"]
        c3 = ad.get("split_sha256") == row["split_sha256"] and ad.get("blob_sha256") == row["blob_sha256"]
        d3 = (f"audit #{a['id']} ({a['ts'][:19]}, {a['actor']}) records " +
              ("the same split_sha256 and blob" if c3 else f"split_sha256 {str(ad.get('split_sha256'))[:12]}…, blob "
                                                         f"{str(ad.get('blob_sha256'))[:12]}…"))
        with db.connect() as con:  # P19: the row's hash-chain link (re-hashes, and the team's next row links to it)
            link_ok, link = S.audit_link(con, a["id"])
        c3 = c3 and link_ok
        d3 += "; chain link: " + link
    checks.append({"n": 3, "check": "audit", "ok": c3, "detail": d3})
    ev = [e for e in evidence_runs(db, row["version_id"], row["table_name"], row["split_key"], row["id"]) if e["run_id"] != exclude_run]
    differ = [e for e in ev if (e["split_sha256"] or None) not in (None, row["split_sha256"])
              or (not e["split_sha256"] and e["split_legacy_sha256"] != row.get("legacy_sha256"))]
    c4 = not differ
    if not ev:
        d4 = "no earlier run on this split"
    elif c4:
        d4 = f"the same hash as {len(ev)} earlier run(s): " + ", ".join(e["run_id"] for e in ev[:8])
    else:
        theirs = sorted({(e["split_sha256"] or e["split_legacy_sha256"] or "")[:12] for e in differ})
        d4 = (f"differs from runs {', '.join(e['run_id'] for e in differ[:8])}" + (" …" if len(differ) > 8 else "") +
              f": they trained on split_sha256 {', '.join(t + '…' for t in theirs)}, the split is {row['split_sha256'][:12]}… now")
    checks.append({"n": 4, "check": "runs", "ok": c4, "detail": d4, "runs": [e["run_id"] for e in ev],
                   "runs_differ": [e["run_id"] for e in differ]})
    if row.get("carried_from"):
        prow = get_by_id(db, row["carried_from"])
        if prow is None:
            c5, d5 = False, f"carried from {row['carried_from']}, which is not in the splits table"
        elif folds is None:
            c5, d5 = False, "the file could not be read"
        else:
            try:
                pids, pfolds = read_blob(store, prow.get("blob_key") or _blob_key(db, prow["blob_sha256"]))
                ids, _ = read_blob(store, key)
                common, ia, ib = np.intersect1d(ids, pids, assume_unique=True, return_indices=True)
                moved = int((folds[ia] != pfolds[ib]).sum())
                c5 = moved == 0
                d5 = f"{len(common):,} surviving rows of {row['carried_from']}: {moved:,} changed fold"
            except Exception as e:  # noqa: BLE001
                c5, d5 = False, f"the parent split could not be read: {str(e)[:120]}"
        checks.append({"n": 5, "check": "carry", "ok": c5, "detail": d5})
    else:
        checks.append({"n": 5, "check": "carry", "ok": True, "detail": "not a carried split", "applies": False})
    failed = [c["check"] for c in checks if not c["ok"]]
    out = {"split_id": row["id"], "version_id": row["version_id"], "table": row["table_name"], "split_key": row["split_key"],
           "split_sha256": row["split_sha256"], "blob_sha256": row["blob_sha256"], "ok": not failed, "failed": failed,
           "checks": checks}
    if failed:
        first = next(c for c in checks if not c["ok"])
        lead = checks[3] if not checks[3]["ok"] else first
        out["message"] = (f"split_integrity: {row['split_key']} of {row['version_id']} {lead['detail']}" +
                          "".join(f"; check {c['n']} ({c['check']}): {c['detail']}" for c in checks if not c["ok"] and c is not lead))
    return out


def verify_version(db, store: BlobStore, version_id: str) -> list[dict[str, Any]]:
    return [integrity(db, store, r) for r in rows_of_version(db, version_id)]


# --------------------------------------------------------------------------- restore from run pins


def restore_offer(db, store: BlobStore, version_id: str) -> list[dict[str, Any]]:
    """Per split of the version that fails its checks: the split the earliest run on it used (that run's pinned blob,
    re-hashed), or why none can be offered."""
    out = []
    for row in rows_of_version(db, version_id):
        chk = integrity(db, store, row)
        if chk["ok"]:
            continue
        offer: dict[str, Any] = {"split_key": row["split_key"], "table": row["table_name"], "now": {
            "split_id": row["id"], "split_sha256": row["split_sha256"], "blob_sha256": row["blob_sha256"],
            "n_holdout": row["n_holdout"]}, "failed": chk["failed"], "checks": chk["checks"]}
        ev = [e for e in evidence_runs(db, version_id, row["table_name"], row["split_key"], None) if e.get("blob_sha256")]
        cand = None
        for e in ev:
            bkey = _blob_key(db, e["blob_sha256"])
            sh = _rehash(store, bkey)
            if sh != e["blob_sha256"]:
                continue
            ssha, n = M.split_sha256_parquet(store.local_copy(bkey))
            if e["split_sha256"] and ssha != e["split_sha256"]:
                continue
            _, folds = read_blob(store, bkey)
            cand = {"run_id": e["run_id"], "run_created_at": e["created_at"], "split_id": e["split_id"],
                    "split_sha256": ssha, "blob_sha256": e["blob_sha256"], "n_rows": int(n),
                    "n_holdout": int((folds == -1).sum()), "same_as_now": ssha == row["split_sha256"],
                    "runs_agreeing": [x["run_id"] for x in ev if (x["split_sha256"] or "") == ssha]}
            break
        offer["offer"] = cand
        if cand is None:
            offer["why_none"] = ("no earlier run pinned an intact copy of this split" if not ev else
                                 "every pinned copy fails its own hash: restore the blob from a backup")
        elif cand["same_as_now"]:
            offer["why_none"] = ("the earliest run used these same bytes: the failure is not in the split's content "
                                 f"({', '.join(chk['failed'])}); nothing to restore")
            offer["offer"] = None
        out.append(offer)
    return out


def restore(db, store: BlobStore, version_id: str, *, split_key: str, blob_sha256: str, by: str, reason: str) -> dict[str, Any]:
    """Put back the split a run pinned: the failing row is replaced by the original row (rebuilt from its split_created
    audit row, the run's pin and the blob itself), in one transaction that lifts and restores the immutability
    triggers, and is audited with both rows. The data is not touched."""
    offers = {o["split_key"]: o for o in restore_offer(db, store, version_id)}
    o = offers.get(split_key)
    if not o or not o.get("offer"):
        raise SplitError(409, "nothing_to_restore", (o or {}).get("why_none") or
                         f"{split_key} of {version_id} passes its checks: nothing to restore.")
    cand = o["offer"]
    if cand["blob_sha256"] != blob_sha256:
        raise SplitError(409, "offer_changed", f"The split on offer is blob {cand['blob_sha256'][:12]}… (run {cand['run_id']}), "
                                               f"not {blob_sha256[:12]}…: GET the offer again.", offer=cand)
    bad = get_row(db, version_id, o["table"], split_key)
    orig = _audit_row(db, cand["split_id"]) if cand.get("split_id") else None
    od = (orig or {}).get("details") or {}
    if od and (od.get("split_sha256") != cand["split_sha256"] or od.get("blob_sha256") != cand["blob_sha256"]):
        od = {}
    bkey = _blob_key(db, cand["blob_sha256"])
    _, folds = read_blob(store, bkey)
    src = od or bad or {}
    row = {"id": cand.get("split_id") or ("spl_" + secrets.token_hex(6)), "version_id": version_id, "table_name": o["table"],
           "split_key": split_key, "target": src.get("target"), "strategy": src.get("strategy") or "random-stratified",
           "params_json": src.get("params_json") or "{}", "seed": int(src.get("seed") or 42),
           "holdout_share": str(src.get("holdout_share") or "0.2"), "n_folds": int(src.get("n_folds") or 5),
           "blob_sha256": cand["blob_sha256"], "split_sha256": cand["split_sha256"],
           "legacy_sha256": od.get("legacy_sha256"), "n_rows": cand["n_rows"], "n_holdout": int((folds == -1).sum()),
           "carried_from": od.get("carried_from"), "created_at": (orig or {}).get("ts") or S.now(),
           "created_by": (orig or {}).get("actor") or by}
    with db.connect() as con:
        con.execute("BEGIN IMMEDIATE")
        root, team = _team(con, version_id)
        con.execute("DROP TRIGGER IF EXISTS splits_ro_d")
        con.execute("DROP TRIGGER IF EXISTS splits_ro_u")
        if bad:
            con.execute("DELETE FROM splits WHERE id = ?", (bad["id"],))
            con.execute("DELETE FROM blob_refs WHERE owner_kind = 'split' AND owner_id = ?", (bad["id"],))
        if con.execute("SELECT 1 FROM splits WHERE id = ?", (row["id"],)).fetchone():
            con.execute("DELETE FROM splits WHERE id = ?", (row["id"],))
        con.execute("INSERT INTO splits (id, version_id, table_name, split_key, target, strategy, params_json, seed, holdout_share,"
                    " n_folds, blob_sha256, split_sha256, legacy_sha256, n_rows, n_holdout, carried_from, created_at, created_by)"
                    " VALUES (:id, :version_id, :table_name, :split_key, :target, :strategy, :params_json, :seed, :holdout_share,"
                    " :n_folds, :blob_sha256, :split_sha256, :legacy_sha256, :n_rows, :n_holdout, :carried_from, :created_at,"
                    " :created_by)", row)
        con.execute("INSERT OR IGNORE INTO blob_refs (blob_sha256, owner_kind, owner_id, role, team_id, created_at)"
                    " VALUES (?, 'split', ?, 'split', ?, ?)", (row["blob_sha256"], row["id"], team, S.now()))
        con.execute("UPDATE blobs SET state = 'live' WHERE sha256 = ? AND state IN ('pending', 'live')", (row["blob_sha256"],))
        con.execute(S.IMMUTABLE_TRIGGERS["splits_ro_u"])
        con.execute(S.IMMUTABLE_TRIGGERS["splits_ro_d"])
        if orig is None:  # the original's audit row is missing: the restored row gets its own (marked as a restore)
            S.audit(con, by, "split_created", {**audit_details(row, root), "basis": f"restored from run {cand['run_id']}'s pin"})
        S.audit(con, by, "dataset_splits_restored", comment=reason,
                details={"dataset_id": version_id, "root_id": root, "split_key": split_key, "table": o["table"],
                         "from_run": cand["run_id"], "restored": {k: row[k] for k in ("id", "split_sha256", "blob_sha256", "n_holdout")},
                         "replaced": {k: bad[k] for k in ("id", "split_sha256", "blob_sha256", "n_holdout", "created_at",
                                                          "created_by")} if bad else None,
                         "failed_before": o["failed"]})
    return {"restored": {k: row[k] for k in ("id", "split_key", "split_sha256", "blob_sha256", "n_holdout")},
            "from_run": cand["run_id"], "replaced": {k: bad[k] for k in ("id", "split_sha256", "blob_sha256", "n_holdout")} if bad else None}
